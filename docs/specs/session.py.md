# specs/session.py.md — 编码规格

> **模块文件**:`pyharness/core/session.py` | **功能编号**:F009(核心)· F018 · PRD §3 全节 · F063(修正语义联动) | **权威口径**:PRD-Core §3(事件协议唯一权威)/§5.2 F009、DIS-CORE §3(伪代码级)、EVENT-SCHEMA §2(信封字段级)
> **一句话**:会话事件日志门面——append-only 唯一写入口(校验 → seq 分配 → 总线分发 → 强同步 flush)与派生视图工厂(derive_messages/events_after/get);原则 1(事件溯源)的物理闸门,系统内除本模块外不存在"append 到历史"路径(INV-01)。

## 模块职责

1. **唯一写入口 `append`**:全系统改变会话事实的唯一通道(原则 1);先校验后写——坏数据(信封非法/类型未注册/seq 乱序/终态后写入)在入口被拒,不进内存/总线/日志;`seq/ts` 只由框架在此分配,LLM/工具/插件无权自报(防伪造乱序)。
2. **append-only,无 update/delete API**:本类**不提供**任何 update/delete/原地改写方法(测试钉死:类方法清单 grep 断言);"修正"= 追加修正事件(`user.message_edited`/`user.feedback`/`context.compacted`/`session.recovered`),原文永留日志,reducer 取新版留旧痕。
3. **派生视图工厂(纯函数)**:`derive_messages()`(别名 `derive_history`,§3.5 reducer 唯一权威实现,禁止第二份历史存储)、`events_after()`(增量读)、`events_between()`(闭区间切片)、`get()`(单条读取)——UI/消息历史/FTS/计量一切视图都经此派生,不另存状态。
4. **强同步三类**(§3.6/EVENT-SCHEMA §1.2):`user.message`、`guard.rejected`、`approval.*` 在 append 内落盘成功才返回——崩溃最多丢强同步点后 ≤0.5s 攒批窗事件,由 repair 的 `session.recovered` 声明。
5. **缓存纪律(INV-01/INV-03)**:`history_cache` 仅当日志尾部未变时有效;任何 append 后整体失效;`open_session` 重放重建后缓存一律重建;rebuild 与缓存逐事件比对测试钉死。
6. **会话状态机**:`pending → active → finished`(session.created seq=1 引导;close 写 finished 后拒一切 append:EVT-104);repair 恢复路径经 `recovering` 由 recovered 事件声明合法化。

## 依赖

- **依赖方向**(单向,INV-08):`session → persistence`(flush/replay 只读+强同步落盘);session 经 `bus.emit` 分发事件;校验器 `validate_envelope` 属 events 层(events 包)。
- **消费方**(只读派生,禁止反向依赖):system-prompt(derive_messages)、agent-loop(derive_messages + append)、tools/llm(append 留痕)、UI/FTS/审计(events_after/events_between/get)。
- 外部依赖:`events.Envelope/validate_envelope`(信封与词表注册表)、`errors.raise_code`(F020)、`persistence.SessionStore`(注入)。

## 类与函数清单

### 信封字段(Envelope,EVENT-SCHEMA §2.1 字段级权威)

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `seq` | int | ✓ | 会话内从 1 起单调 +1(ge=1);仅本类分配 |
| `ts` | str | ✓ | ISO8601 UTC 微秒,末尾 Z(正则 `^\d{4}-\d{2}-\d{2}T.*Z$`) |
| `type` | str | ✓ | 词表枚举;未注册 → EVT-102 拒写 |
| `session_id` | str | ✓ | 会话 UUID(min_length=8);fork 产生新 id(F059) |
| `actor` | Literal 六值 | ✓ | user/agent/llm/tool/system/plugin |
| `origin` | str? | — | 来源能力 id(如 cap:tool_fs) |
| `task_id` | str? | — | 所属任务段(F044) |
| `payload` | dict | ✓ | 按 type 的 pydantic 模型强校验 |
| `trace` | dict? | — | 语义父关联 `{"parent_seq": int}` |

**SessionLog 私有字段**:`sid` / `_seq`(=max 续写)/ `_cache`(可弃重建)/ `history_cache`(append 即失效)/ `_folded`(compacted 声明区间)/ `_closed`(拒写)/ `_persistence`(注入)。

### 校验链(顺序不可交换,EVENT-SCHEMA §2.2)

1. 信封字段合法(类型/必填/枚举/正则)→ EVT-100
2. 类型已注册(词表或插件注册 schema)→ EVT-102
3. payload 模型校验(按 type 强类型)→ EVT-100
4. seq 连续性(= `_seq+1`)→ EVT-101(疑丢事件 → 跑 repair)
5. 会话状态(未 created → EVT-106;已 finished/closed → EVT-104)

### `async def append(type_: str, payload: dict, *, actor: str, sync: bool = False, trace: dict | None = None, origin: str | None = None, task_id: str | None = None) -> Envelope` — 唯一写入口(F009)

**功能**:校验 → 分配 seq → 入内存(订阅者可即时读)→ 总线分发 →(强同步三类)落盘 → 派生缓存整体失效;返回带 seq 的 Envelope。**本类唯一写方法**。

```python
async def append(self, type_, payload, *, actor, sync=False, trace=None,
                 origin=None, task_id=None):
    if self._closed:                              # 终态后写入:结构上不可能(EVT-104)
        raise PyHError("EVT-104", ctx={"type": type_,
            "advice": "会话已结束(finished 仅一次);查绕过 agent.close 的写路径(INV-01)"})
    if type_ == "session.finished":
        self._closed = True                       # 先置位防并发双写(finished 单次)
    env = validate_envelope({"type": type_, "payload": payload, "actor": actor,
        "session_id": self.sid, "seq": self._seq + 1, "trace": trace,
        "origin": origin, "task_id": task_id})    # 校验链 5 步;失败拒写,不进日志
    self._seq = env.seq                           # seq 单调前进(max+1,永不回填)
    self._cache.append(env)                       # 先入内存:订阅者/派生视图可即时读
    await bus.emit(env.type, env)                 # 总线分发;日志订阅者(§8)负责物理落盘
    if sync or env.type in SYNC_TYPES:            # 强同步三类:user.message /
        await self._persistence.flush(env.seq)    #   guard.rejected / approval.*(§3.6)
    self.history_cache = None                     # 派生缓存整体失效(INV-03)
    return env
```

**参数表**:`type_` = §3.3 词表名;`payload` 按 type 字段表;`actor` 必填(六值枚举);`sync` = 本次强制强同步;`trace` = 父 seq 关联(仅审计,不参与 reducer);`origin/task_id` = 来源与任务段。

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 信封字段非法/空消息/payload 校验失败 | EVT-100 | 拒写返字段明细,修正重发 |
| `PyHError` | seq 不连续/重复 | EVT-101 | 拒写;疑丢事件跑 repair(F060) |
| `PyHError` | 类型未注册 | EVT-102 | 拒写;插件先注册类型 schema |
| `PyHError` | finished 后 append | EVT-104 | 拒写;查绕过路径(INV-01) |
| `PyHError` | 事件先于 session.created | EVT-106 | 拒写;查 bootstrap 顺序 |
| `PyHError` | 强同步点落盘失败 | PERS-202 | append 抛错;repair 后重试 |

**关联测试**:GWT-S3-01(未知类型/坏信封/乱 seq → EVT-102/100/101 且日志行数不变——拒写零副作用)。

### `def derive_messages(self, max_tokens: int | None = None) -> list[dict]` — 派生历史(纯函数 reducer;别名 derive_history)

**功能**:日志 → LLM 消息历史,§3.5 reducer **唯一权威实现**;纯函数——输入仅为本类日志与 max_tokens,无副作用、无第二份状态;缓存可整体丢弃重建(INV-01/03)。`user.message→user`;`llm.response(有 content)→assistant`;`llm.response(空)+tool.result→assistant(tool_calls)+tool` 配对;`guard.rejected` 只留审计流**不进** LLM 上下文;`llm.chunk` 不入日志。

```python
def derive_messages(self, max_tokens=None):
    msgs, pending = [], None                      # pending = 待配对 tool 的 response seq
    for ev in self._cache:                        # seq 升序遍历(坏行已由 persistence 隔离)
        t = ev.type
        if t == "user.message":
            msgs.append({"role": "user", "content": ev.payload["content"]})
        elif t == "user.message_edited":          # 修正 = 追加事件:取新版留旧痕(F063)
            self._replace_at(msgs, ev.payload["target_seq"], ev.payload["new_content"])
        elif t == "context.compacted":            # 摘要代折叠区间(空洞合法化,§3.4)
            msgs.append({"role": "system", "content":
                f"[已压缩 {ev.payload['ranges']}] {ev.payload['summary']}"})
        elif t == "llm.response":
            c = ev.payload.get("content") or ""
            if c: msgs.append({"role": "assistant", "content": c})
            else: pending = ev.seq                # 空 content = 工具调用轮,等 tool.result 配对
        elif t == "tool.result" and pending is not None:
            msgs.append({"role": "tool", "content": ev.payload["summary"],
                         "name": ev.payload["name"]})
            pending = None
        # guard.rejected/llm.chunk/其他:审计流或瞬时事件,不进 LLM 上下文
    return self._truncate_head(msgs, max_tokens)  # 超窗头部截断(scope.window_tokens 联动)
```

**参数表**:`max_tokens` = 窗口余量(scope.window_tokens 联动;None = 不截断,测试用)。**返回**:`list[{"role", "content", …}]`。**异常表**:无(纯派生;坏行已由 persistence.replay 记跳隔离 PERS-201)。缓存语义:调用方可用 `history_cache`(尾部未变时有效),append 即失效——任何实现不得绕过本函数自建历史(INV-01)。

**关联测试**:GWT-S3-02(reducer 配对:空 content response + tool.result → assistant+tool 配对;guard.rejected 不在结果)、GWT-S3-03(user.message seq=2 + message_edited(2) → derive 取新内容,日志仍含原文两行)、INV-01(inv01_history_derived_from_log:append 即历史变化,无第二份状态可不同步)。

### `def events_after(self, after_seq: int = 0) -> Iterator[Envelope]` — 增量读(F009)

**功能**:seq 升序生成器,返回 `seq > after_seq` 的全部事件(排他);UI 增量同步/子代理拉新事件/审计尾随共用;内存缺失时惰性从 `persistence.replay` 补齐。

```python
def events_after(self, after_seq=0):
    if self._needs_rebuild:                       # 缓存不可用时从磁盘重建(可弃优化)
        self.rebuild_from_log()
    start = bisect_right(self._seq_index(), after_seq)   # 二分定位,跳过已读区间
    for env in self._cache[start:]:
        yield env                                 # 只读遍历:绝不在迭代中写日志
```

**参数表**:`after_seq` 排他下界(0 = 全量)。**异常表**:无(回放坏行已隔离;空洞经 compacted/recovered 声明合法化)。**关联测试**:GWT-S3-04 配套(append 后 events_after(last) 恰返回新事件,与 rebuild 结果逐事件一致——INV-03)。

### `def events_between(self, lo: int, hi: int) -> Iterator[Envelope]` — 闭区间切片

**功能**:`[lo, hi]` 闭区间事件迭代;段查询(F044)/预算复算/复算审计共用;基于 events_after(lo-1) 实现,不另存状态。

```python
def events_between(self, lo, hi):
    for env in self.events_after(lo - 1):         # 复用增量读,下界排他转闭区间
        if env.seq > hi: break                    # 上界截断
        yield env
```

**参数表**:`lo/hi` = seq 闭区间(lo ≤ hi,否则空迭代)。**异常表**:无。

### `def get(self, seq: int) -> Envelope | None` — 单条读取

**功能**:按 seq 取单条事件;不存在/被 compacted 折叠(空洞)→ 返回 None 不抛;用于 trace 父 seq 解析、approval_id 复核、审计单点取证。

```python
def get(self, seq):
    if not (1 <= seq <= self._seq): return None   # 越界:不存在
    i = bisect_left(self._seq_index(), seq)       # O(log n) 定位
    if i >= len(self._cache) or self._cache[i].seq != seq:
        return None                               # 空洞(compacted/repair 声明区间)
    return self._cache[i]
```

**参数表**:`seq` ≥ 1。**异常表**:无(None 语义 = 不存在或已折叠)。**关联测试**:空洞场景(get 折叠区间内 seq → None;get 正常 seq → Envelope)。

### `async def open_session(sid: str, persistence: SessionStore) -> SessionLog` — 启动/恢复重建

**功能**:重放日志重建会话状态(崩溃恢复与审计回放同一条代码路径);repair(F060)先于本函数执行;文件不存在 = 新会话(等 session.created,EVT-106 守卫);坏行 PERS-201 记跳不中断;无声明空洞告警(F031);`_seq` 从最后完整点续写(空洞不回填)。

```python
async def open_session(sid, persistence):
    log = SessionLog(sid=sid, _persistence=persistence)
    last = None
    for env in persistence.replay():              # 坏行 PERS-201 记跳,不中断回放(§3.8)
        if env.session_id != sid: continue        # 多会话文件过滤(轮转合并场景兜底)
        if last and env.seq != last.seq + 1 and not log._folded_contains(env.seq):
            log.warn_hole(env.seq)                # 无 compacted/recovered 声明的空洞 → 告警(F031)
        log._cache.append(env); last = env
    log._seq = last.seq if last else 0            # 从最后完整点续写(§3.4:空洞只解释不回填)
    log.history_cache = None                      # 派生缓存一律重建(INV-03)
    if last and last.type == "session.finished": log._closed = True   # 终态会话恢复后仍拒写
    return log
```

**参数表**:`sid` 会话 id;`persistence` = §persistence 实例(已指向本会话文件)。**异常表**:文件不存在 = 新会话(不抛);不可修复损坏 → PERS-201 + 建议先跑 repair(F060)。

### `def rebuild_from_log(self) -> None` — 缓存整体重建(INV-03)

**功能**:丢弃内存 `_cache`/`history_cache`,从 persistence.replay 整体重放重建——"缓存可整体丢弃重建"落地;任何 append 后调用方应比对重建结果与缓存逐事件一致(测试钉死)。

```python
def rebuild_from_log(self):
    self._cache = []; self._seq = 0; self.history_cache = None
    for env in self._persistence.replay():
        if env.session_id != self.sid: continue
        self._cache.append(env); self._seq = env.seq
    self._needs_rebuild = False                   # 重建完成标记(events_after 惰性触发)
```

### `def stats(self) -> dict` — 只读统计

**功能**:审计/外壳查询:`{session_id, seq, event_count, closed, folded_ranges, holes_warned}`;纯只读。

### `def close_marker(self) -> None` — 终态拒写置位

**功能**:由 `agent.close` 在 finished 落盘后调用,置 `_closed=True`(后续 append → EVT-104);不写事件(事件由 agent.close 写)。

## 会话状态机(事件流生命周期)

```text
 PENDING ──session.created(seq=1)──► ACTIVE ──agent.close──► FINISHED
   │                                  │  ▲                     │任何 append
   │ open 发现损坏/半行               │  │repair 完成           ▼
   └──► RECOVERING ──(recovered 事件)─┘               EVT-104 拒写(终态不可逆)
```

| 当前→目标 | 触发 | 动作与事件 |
|---|---|---|
| pending→active | session.created 落盘 | 日志可接受事件(seq 从 1) |
| pending→recovering | open 遇半行/坏行/空洞 | repair(F060)→ session.recovered |
| active→active | 普通 append | seq+1;订阅者可见;异步落盘(≤0.5s/64 条) |
| active→finished | agent.close 写 finished | 置 _closed;强同步 flush |
| finished→— | 任何 append | EVT-104/EVT-106 拒写(终态不可逆) |
| active→active | compaction/fork | 仅声明事件;态不变(空洞由声明合法化) |

## 边界与限制

1. **只追加,无 update/delete**:修正 = 追加编辑事件(F063),reducer 取新版留旧痕;方法面 grep 测试断言无 remove/update。
2. **先校验后写**:校验链 5 步任一失败 → 拒写,日志行数不变(零副作用)。
3. **缓存纪律**:history_cache 仅尾部未变时有效,append 即失效;rebuild 与缓存逐事件比对(INV-03)。
4. **seq 空洞合法化**:空洞必须以 context.compacted / fork.created / session.recovered 声明;append 永远 max+1,永不回填。
5. **单进程单写者**(INV-07):禁止跨进程打开同一日志写(写由注入的 persistence 单句柄完成)。
6. **版本演进**:未知类型回放跳过 + 警告不中断(§3.8);事件类型只增不改,payload 只加可选字段。
7. **SYNC_TYPES 常量** = `{"user.message", "guard.rejected", "approval.requested", "approval.granted", "approval.denied", "approval.timeout"}`(§3.6 强同步三类全集)。

## 关联测试(汇总)

GWT-S3-01 校验链拒写 · GWT-S3-02 reducer 配对 · GWT-S3-03 修正覆盖 · GWT-S3-04 缓存一致(`tests/acceptance/test_f009_session_log.py`);INV-01(历史必由日志派生)/INV-03(rebuild 一致)并入 `tests/acceptance/test_f018_invariants.py`;里程碑:重启回放后上下文与崩溃前一致。

## 关联文档

- PRD-Core.md §3 全节(事件协议唯一权威:信封/词汇表/seq 空洞/派生 reducer/JSONL/违规表)、§5.2 F009/F018/F063、§4.1(原则 1 落地检查点)
- DIS-CORE.md §3(本模块伪代码级唯一权威)、§8.3.2(replay 坏行隔离契约)
- EVENT-SCHEMA.md §1/§2(总则/信封字段级/校验链/seq 规则)、§3(A 会话生命周期 payload)
- ERR.md §2.2(EVT-1xx 处置表)、§2.3(PERS-201/202 协作);ADD.md ADR-001/ADR-006
