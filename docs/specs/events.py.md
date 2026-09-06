# specs/events.py.md — 编码规格

> 目标代码:pyharness/events/(envelope.py 信封模型、vocab.py 词表注册、payload.py 各事件负载模型),即 PRD-Core §3 事件协议的实现载体,供 pyharness/core/session.py(F009)唯一写入口调用。AI 编码 Agent 只读本文件即可实现事件校验全链。权威源:PRD-Core.md §3.1-§3.8(信封/词表/seq/违规表)、§8.2;EVENT-SCHEMA.md(字段级权威展开);ERR.md §2.2(EVT-1xx);DIS-SEAM.md §4.4(全流程与保证)。全中文,仅 Python,禁 TS。

## 模块职责
一句话:会话事件唯一的 pydantic 模型层——Envelope 信封强校验 + 57 个事件负载词表注册 + 五步校验链(任一失败拒写,不进内存/总线/日志),被 session.append 作为唯一写入口闸门。

## 依赖
| import | 用途 |
|---|---|
| pydantic(BaseModel、Field、ConfigDict、ValidationError、field_validator) | 信封与全部 payload 模型强类型 |
| typing(Optional、Literal、Any) | actor 枚举与可选字段 |
| re | ts 正则 `^\d{4}-\d{2}-\d{2}T.*Z$` |
| datetime(timezone、datetime) | ts 框架统一打(UTC 微秒) |
| pyharness.errors(raise_code、PyHError) | EVT-100/101/102/104/106 唯一抛出入口 |
| pyharness.bus.event_bus(EventBus) | validate 后由 session.append 分发;events 模块自身不依赖总线实现细节(仅类型注解) |

禁止:任何模型含第二份消息历史/内存态;payload 内嵌字面换行(写盘须 JSON 转义,PRD §3.6);LLM/工具/插件自报 seq 与 ts(§3.1-5)。

## 常量与词表

### ACTORS / SYNC_TYPES / TRANSIENT_TYPES 常量
**功能**:actor 六枚举;强同步三类事件族(SYNC_TYPES,落盘成功才返回);瞬时事件(仅总线,禁入日志)。
**伪代码**:
```python
ACTORS = ("user", "agent", "llm", "tool", "system", "plugin")       # Envelope.actor Literal
SYNC_TYPES = frozenset({          # EVENT-SCHEMA §8.1 强同步矩阵
    "user.message", "guard.rejected",
    "approval.requested", "approval.granted", "approval.denied", "approval.timeout",
    "session.finished", "session.recovered", "segment.start",
    "fork.created", "context.compacted"})
TRANSIENT_TYPES = frozenset({"llm.chunk", "registry.updated"})      # 仅总线,禁 append(§1.3)
```
**关联测试**:TC-F009 → test_f009_session_log.py(强同步)、TC-F018 INV-01 → test_f018_invariants.py(§8)。

## 类与函数清单

### class Envelope(BaseModel)
**功能**:事件信封,十字段强校验(PRD §3.2 / EVENT-SCHEMA §2.1);model_validate 失败 → EVT-100。
**参数表**:
| 字段 | 类型 | 规则 |
|---|---|---|
| seq | int | `ge=1`,会话内单调 +1,框架分配 |
| ts | str | ISO8601 UTC,正则 `^\d{4}-\d{2}-\d{2}T.*Z$`,微秒,框架打 |
| type | str | 词表枚举;未注册 → EVT-102 |
| session_id | str | `min_length=8`,形如 `s-abc12345`;fork 新 id |
| actor | Literal | user/agent/llm/tool/system/plugin 六值 |
| origin | Optional[str] | 插件/能力 id(如 plugin:echo);脊柱事件可省略 |
| task_id | Optional[str] | 所属任务段;无任务上下文为空 |
| payload | dict | 按 type 二次强校验,失败 → EVT-100 |
| trace | Optional[dict] | 语义父关联,建议 `{"parent_seq": int}` |
**伪代码**:
```python
class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)          # 冻结信封:拒多余字段
    seq: int = Field(ge=1)
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")
    type: str
    session_id: str = Field(min_length=8)
    actor: Literal["user", "agent", "llm", "tool", "system", "plugin"]
    origin: Optional[str] = None
    task_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    trace: Optional[dict] = None

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: str) -> str:                                 # 框架统一打,禁外部自报
        if not v.endswith("Z"):
            raise ValueError("ts 必须 UTC 且以 Z 结尾")
        return v
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ValidationError→EVT-100 | 字段类型错/枚举非法/ts 格式/缺必填/extra 字段 | EVT-100 | 拒写,返回字段明细;对照 §3 词表修正 |
| EVT-106 | 会话未 created 即写(状态机侧) | EVT-106 | 首事件必须是 session.created(seq=1) |
**关联测试**:TC-F009、TC-GWT-ERR-07(拒写不落盘)→ test_f009_session_log.py、test_f019_error_codes.py(§8)。

### register_event_type(type_: str, model: type[BaseModel], *, transient=False) -> None
**功能**:词表注册(框架 57 类型启动期注册;插件运行时注册 `plugin.<id>.<name>` 命名空间);重复注册拒绝(EVT-102,§3.8 只增不改)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| type_ | str | 是 | 事件名 |
| model | type[BaseModel] | 是 | 对应 payload pydantic 模型 |
| transient | bool | 否 | True=仅总线不入日志(如 llm.chunk) |
**伪代码**:
```python
_EVENT_REGISTRY: dict[str, type[BaseModel]] = {}
_TRANSIENT: set[str] = set(TRANSIENT_TYPES)

def register_event_type(type_: str, model: type[BaseModel], *, transient: bool = False) -> None:
    if type_ in _EVENT_REGISTRY:                       # 词表冻结:重复注册 = 语义冲突
        raise_code("EVT-102", type_=type_, detail="重复注册,事件类型只增不改")
    _EVENT_REGISTRY[type_] = model
    if transient:
        _TRANSIENT.add(type_)
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| UnknownEventType(重复) | 同 type_ 二次注册 | EVT-102 | 破坏性变更 = 新类型名(如 user.message_v2),旧类型冻结 |
**关联测试**:TC-F019 注册表完整、TC-GWT-ERR-01 → test_f019_error_codes.py(§8)。

### payload_model_for(type_: str) -> type[BaseModel]
**功能**:查类型 → payload 模型;未注册返回 None(由调用方按 EVT-102 拒)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| type_ | str | 是 | 事件名 |
**伪代码**:
```python
def payload_model_for(type_: str) -> Optional[type[BaseModel]]:
    return _EVENT_REGISTRY.get(type_)                  # 未注册 → None,校验链第 2 步用
```
**关联测试**:TC-GWT-ERR-07(EVT-102 拒写)→ test_f019_error_codes.py(§8)。

### validate_payload(type_: str, payload: dict) -> dict
**功能**:payload 强类型二次校验(EVENT-SCHEMA §2.2 第 3 步);成功返回规范化 dict;失败 → EVT-100 带字段明细。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| type_ | str | 是 | 事件名(须已注册) |
| payload | dict | 是 | 原始负载 |
**伪代码**:
```python
def validate_payload(type_: str, payload: dict) -> dict:
    model = payload_model_for(type_)
    if model is None:
        raise_code("EVT-102", type_=type_)             # 未注册类型(第 2 步)
    try:
        return model(**payload).model_dump()           # strict:多余字段拒绝(F026 同纪律)
    except ValidationError as e:
        raise_code("EVT-100", type_=type_, detail=summarize_validation(e))  # 字段明细回馈
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| UnknownEventType | 类型未注册 | EVT-102 | 先 register_event_type 才能 emit |
| PayloadInvalid | 必填缺失/类型错/枚举非法/extra 字段 | EVT-100 | 拒写;按 detail 明细修正后重试 |
**关联测试**:TC-F009、TC-GWT-ERR-07 → test_f009_session_log.py、test_f019_error_codes.py(§8)。

### validate_envelope(raw: dict, seq_state: "SeqState", session_open: bool = True) -> Envelope
**功能**:五步校验链唯一实现(信封→类型→payload→seq 连续→会话状态),顺序不可交换;任一失败拒写(不进内存/总线/日志,EVENT-SCHEMA §2.2)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| raw | dict | 是 | 待写事件(含 type/payload/actor/session_id 等) |
| seq_state | SeqState | 是 | 会话 seq 分配器(_next_seq,校验第 4 步) |
| session_open | bool | 是 | 会话是否已 created 且未 finished(第 5 步) |
**伪代码**:
```python
def validate_envelope(raw: dict, seq_state, session_open: bool = True) -> Envelope:
    try:
        env = Envelope.model_validate(raw)             # 1 信封字段(类型/枚举/正则)→ EVT-100
    except ValidationError as e:
        raise_code("EVT-100", detail=summarize_validation(e))
    if env.type not in _EVENT_REGISTRY:                # 2 类型必须注册 → EVT-102
        raise_code("EVT-102", type_=env.type)
    env.payload = validate_payload(env.type, env.payload)          # 3 payload 强校验 → EVT-100
    expected = seq_state.next_seq(env.session_id)      # 4 seq 必须 = _next_seq+1 → EVT-101
    if env.seq != expected:
        raise_code("EVT-101", session_id=env.session_id,
                   expected=expected, got=env.seq)
    if not session_open:                               # 5 会话状态 → EVT-106/EVT-104
        raise_code("EVT-106", type_=env.type, hint="首事件必须是 session.created")
    return env
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 信封非法 | 字段类型/枚举/ts 正则失败 | EVT-100 | 拒写,返字段明细 |
| 类型未注册 | type 不在词表 | EVT-102 | 先注册 schema;查拼写 |
| payload 非法 | 负载模型校验失败 | EVT-100 | 按明细修正 |
| seq 不连续/重复 | seq ≠ next | EVT-101 | 拒写;疑丢事件跑 repair(F060) |
| 先于 created | session_open=False 且首事件非 created | EVT-106 | 外壳先 create 再 submit |
| 终态写违规 | finished/closed 后 append(状态机侧另查) | EVT-104 | 查绕过 session.append 路径(INV-01) |
**关联测试**:TC-GWT-ERR-07(坏信封/乱 seq/未知类型/closed 后写分别 EVT-100/101/102/104 且日志行数不变)→ test_f009_session_log.py(§8)。

### make_envelope(session_id, type_, actor, payload, *, origin=None, task_id=None, trace=None, seq_state=None) -> Envelope
**功能**:框架打点构造器:seq/ts 由框架统一分配(§3.1-5,LLM/工具/插件无权自报),构造后再走 validate_envelope 全链。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| session_id / type_ / actor | str | 是 | 会话 id、事件名、六枚举之一 |
| payload | dict | 是 | 负载(须匹配词表模型) |
| origin / task_id / trace | str?/str?/dict? | 否 | 信封可选字段 |
| seq_state | SeqState | 是 | 取 next_seq 并 +1 记账 |
**伪代码**:
```python
def make_envelope(session_id, type_, actor, payload, *, origin=None,
                  task_id=None, trace=None, seq_state=None) -> Envelope:
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    raw = {"seq": seq_state.next_seq(session_id),      # 框架唯一打点(防伪造乱序)
           "ts": ts, "type": type_, "session_id": session_id,
           "actor": actor, "origin": origin, "task_id": task_id,
           "payload": payload, "trace": trace}
    return validate_envelope(raw, seq_state, session_open=session_id in seq_state.open)  # 复用全链
```
**异常表**:同 validate_envelope(EVT-100/101/102/106/104);调用方按码处置。
**关联测试**:TC-F009(append 唯一入口/seq/强同步)→ test_f009_session_log.py(§8)。

### class SeqState
**功能**:per-session seq 分配与空洞口径:内存 `_next` 从日志 max 重建;append 恒 max+1;compaction/fork/repair 空洞只解释不回填(§3.4)。
**伪代码**:
```python
class SeqState:
    def __init__(self):
        self._next: dict[str, int] = {}                # session_id → 下个可用 seq
        self.open: set[str] = set()                    # 已 created 且未 finished
    def rebuild(self, session_id: str, max_seq: int) -> None:   # 启动/repair 后从日志重建
        self._next[session_id] = max_seq + 1
        self.open.add(session_id)
    def next_seq(self, session_id: str) -> int:
        return self._next.get(session_id, 1)           # 无记录 → 期望 1(session.created)
    def commit(self, env: Envelope) -> None:           # validate 通过后记账
        self._next[env.session_id] = env.seq + 1
```
**关联测试**:TC-F009(seq 单调)、TC-F060 repair 空洞 → test_f009_session_log.py、test_f060_repair.py(§8)。

### check_seq_gap(events: list[int], declared: list[tuple[int, int]]) -> list[int]
**功能**:回放空洞自检:无 compacted/recovered 声明覆盖的 seq 空洞 → 返回列表供 warn_hole 告警(F031),不中断回放。
**伪代码**:
```python
def check_seq_gap(events: list[int], declared: list[tuple[int, int]]) -> list[int]:
    declared_holes: set[int] = set()
    for lo, hi in declared:
        declared_holes.update(range(lo, hi + 1))       # 声明区间 = 合法空洞
    actual = set(events)
    expected = set(range(1, max(events) + 1)) if events else set()
    return sorted(expected - actual - declared_holes)  # 未声明空洞 → 告警源
```
**关联测试**:TC-F031 运行时自检(SEQ-GAP)、TC-F058/F060 空洞声明 → test_f031_selfcheck.py(§8)。

## payload 词表模型注册清单(register_event_type 全量入册)
> 字段要点按 EVENT-SCHEMA §3(✓=必填);实现为每事件一个 pydantic 模型(extra="forbid"),启动期一次注册。

| 组 | 事件 type | payload 必填要点(其余按字段表) |
|---|---|---|
| A 会话 | session.created | title、model(恒 seq=1) |
| A 会话 | session.renamed | new_title(≤64 非空)、by |
| A 会话 | session.finished | reason ∈ idle/timeout/budget/error/cancelled(只一次,EVT-104) |
| A 会话 | session.recovered | fixed[] 非空、backup、lost |
| B 用户 | user.message | content 非空(强同步) |
| B 用户 | user.message_edited | target_seq、new_content(30 分钟窗,超窗 EDT-001) |
| B 用户 | user.feedback | target_seq、kind ∈ up/down/flag、note ≤500 |
| B 用户 | user.attachment.image | file_path、mime 白名单、sha256、w、h |
| B 用户 | user.command | name(命令表)、args |
| C 模型 | llm.request | model、degraded_from、prompt_tokens、n_tools |
| C 模型 | llm.response | model、finish_reason、content、tool_calls(content 与 tool_calls 至少其一) |
| C 模型 | llm.usage | model、in_tokens、out_tokens(≥0)、cache_hit、cost_est |
| C 模型 | llm.error | code(必注册码)、message、retryable |
| C 模型 | agent.message | content 非空、model |
| C 模型 | llm.chunk | delta(瞬时,仅总线) |
| C 模型 | llm.retry | attempt(<上限)、delay_ms、model |
| D 工具 | tool.call | name、args(强类型)、raw_args、call_id |
| D 工具 | guard.evaluated | tool、decision ∈ allow/deny/need_approval、guard_ids、reasons |
| D 工具 | guard.rejected | tool、guard_id、reason、policy_ref(强同步) |
| D 工具 | approval.requested | tool、args_summary、ttl_ms(默认 120000)、risk(强同步) |
| D 工具 | approval.granted/denied/timeout | approval_id(=请求 seq)、by(强同步) |
| D 工具 | tool.result | name、call_id、ok、summary(≤2KB)、truncated、spill_ref |
| D 工具 | tool.error | name、call_id、code、message |
| E 编排 | task.enqueued/started/completed/failed | task_id、pos(enqueued)、reason |
| E 编排 | segment.start/end | task_id、start_seq(强同步 start) |
| E 编排 | plan.proposed/approved/rejected/exec.step | plan_id、goal、steps ≤8、step_idx |
| E 编排 | goal.created/updated/completed | goal_id、status ∈ active/paused/done/abandoned |
| E 编排 | schedule.trigger | job、cron、fired_at |
| E 编排 | job.started/completed/failed | job_id、elapsed_ms、reason |
| E 编排 | subagent.spawned/joined/failed | sub_id、parent_seq、task/summary |
| E 编排 | workflow.step | wf_name、step_idx、action、state |
| E 编排 | queue.suspended/resumed | reason、by |
| E 系统 | fork.created | new_session_id、base_seq(强同步) |
| E 系统 | context.compacted | ranges[[lo,hi]]、summary、tokens_before/after(强同步) |
| E 系统 | plugin.installed/uninstalled | plugin_id、version、api_version |
| E 系统 | bus.backpressure | sender、dropped、sample ≤100 |
| E 系统 | system.cancelled | what、reason |
| E 系统 | system.error | code(必注册)、hint、ctx |
| E 系统 | todo.updated | task_id、todos ≤20 |
| E 系统 | syscheck.fail | findings[]、trigger |

**关联测试**:TC-F009、TC-GWT-ERR-07;词表完整性对照 EVENT-SCHEMA §3(57 名+llm.retry)由 test_f019_error_codes.py 注册表断言兜底(§8)。

## 关联文档
- PRD-Core.md(§3 事件协议权威:信封/词表/seq 空洞/违规表 §3.7/版本演进 §3.8;F009 唯一写入口)
- EVENT-SCHEMA.md(§2 信封字段级、§2.2 校验链、§3 词表 57 事件 payload 字段表、§6 EVT 码总表、§7 演进、§8 落盘矩阵)
- ERR.md(§2.2 EVT-1xx 全量、§1.3 处置三轴、§9.2 注 1:TLB-404 示例冲突禁引用)
- DIS-SEAM.md(§4.4 事件系统全流程:session.append → validate → emit → 日志订阅者)
- CFG.md(§3.9 固定常量:payload ≤64KB、seq/ts 框架 UTC)
