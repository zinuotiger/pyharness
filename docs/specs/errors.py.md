# specs/errors.py.md — 编码规格

> 目标代码:pyharness/errors.py,PRD-Core F019(错误码体系)/F020(结构化错误)的实现载体;错误码契约以 ERR.md 为登记册。AI 编码 Agent 只读本文件即可写出错误体系全量代码。权威源:PRD-Core.md F019/F020、ERR.md §1-§5、EVENT-SCHEMA.md §3.3.4/§3.4.7/§3.5.6(system.error 事件)。全中文,仅 Python,禁 TS。禁止:裸 raise str、无码异常跨边界、现场造码、把 CYC-999 当常态(ERR §1.5)。

## 模块职责
一句话:全系统唯一失败语义层——ErrorSpec 注册表 + PyHError 异常树(code/message/retryable/cause/context)+ 唯一抛出入口 raise_code + 三消费者转换(to_model_message 给 LLM / to_user_message 给用户 / to_event 给审计日志),每码携带分类与默认处置。

## 依赖
| import | 用途 |
|---|---|
| dataclasses | ErrorSpec 值对象 |
| typing(Optional、Any) | 类型标注 |
| traceback | 堆栈仅本地 debug 日志,绝不上行(ERR §1.4) |
| logging | 双落纪律:每次 raise_code ≥1 行日志(code=xxx key=value) |
| pyharness.config(redact_out 注) | 脱敏为配置域能力,errors 只组装不重复实现(INV-09 由出口横切保证) |

禁止:把 SDK/OS 异常原文文案透传(ERR §5.1 ① 根因类别保留、厂商文案丢弃);异常文本外泄至远端。

## 类与函数清单

### class ErrorSpec
**功能**:错误码注册表条目:code/name/advice/retryable 四必填 + 处置动作与致命度(ERR §1.3 三轴)。
**参数表**:
| 字段 | 类型 | 说明 |
|---|---|---|
| code | str | `域前缀-NNN`,如 EVT-102 |
| name | str | 人类可读名(中文,供 message) |
| advice | str | 给 LLM/用户的可行动建议(修复方向) |
| retryable | str | R=自动退避 / F=修复后重试 / N=不可重试 |
| action | str | 拒写/回喂/降级/暂停/终态/隔离 六值之一 |
| fatal | bool | True=终止/暂停/拒载级(如 LLM-310、CYC-999、CFG-601) |
**伪代码**:
```python
@dataclass(frozen=True)
class ErrorSpec:
    code: str
    name: str
    advice: str
    retryable: str = "N"        # R / F / N
    action: str = "拒写"        # 拒写|回喂|降级|暂停|终态|隔离
    fatal: bool = False
```

### ERRORS 注册表与 register(code, name, advice, retryable, action, fatal) -> None
**功能**:登记单码;重复登记拒绝(防表内冲突);全量发布码在 import 时经 register_default_codes() 一次入册(ERR §2 目录)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| code/name/advice | str | 是 | 见 ErrorSpec |
| retryable/action/fatal | str/bool | 否 | 默认 N/拒写/False |
**伪代码**:
```python
ERRORS: dict[str, ErrorSpec] = {}

def register(code: str, name: str, advice: str, *,
             retryable: str = "N", action: str = "拒写", fatal: bool = False) -> None:
    if code in ERRORS:
        raise ValueError(f"错误码重复登记:{code}")        # 码一经发布不改含义,禁覆盖
    ERRORS[code] = ErrorSpec(code, name, advice, retryable, action, fatal)

def register_default_codes() -> None:
    # ===== 发布码全量入册(ERR §2;实现同步状态列与 ERR.md/EVENT-SCHEMA) =====
    register("BUS-002", "保留名冲突", "换名;确认不在脊柱八名+guard/approval/credentials", retryable="N", action="拒写")
    register("BUS-003", "非法状态迁移", "对照 F006 生命周期状态机,查调用顺序", retryable="N", action="拒写")
    register("EVT-100", "信封/载荷非法", "对照 EVENT-SCHEMA 词表修正字段明细后重试", retryable="F", action="拒写")
    register("EVT-101", "seq 不连续/重复", "疑丢事件请跑 repair(F060)", retryable="F", action="拒写")
    register("EVT-102", "类型未注册", "emit 前先 register_type;查拼写", retryable="F", action="拒写")
    register("EVT-103", "订阅者异常", "查本地日志订阅者堆栈,修复后重载", retryable="N", action="隔离")
    register("EVT-104", "终态写违规", "查绕过 agent.close 的写路径(INV-01)", retryable="N", action="拒写")
    register("EVT-105", "未知斜杠命令", "查 F041 命令表拼写;零 LLM 属预期", retryable="N", action="隔离", fatal=False)
    register("EVT-106", "先于 created", "外壳先 create 再 submit,首事件恒 session.created", retryable="N", action="拒写")
    register("CFG-601", "配置非法/越权", "修四层配置字段;HARDENED 无回退", retryable="F", action="拒写", fatal=True)
    register("CFG-602", "模板缺变量", "查 scope/config 派生变量表补齐", retryable="F", action="隔离")
    register("CFG-603", "护栏段构建失败", "查护栏 kind;缺护栏不发请求属预期", retryable="F", action="拒写")
    register("CFG-607", "未知配置键/白名单外 env", "核对 CFG §4.3 白名单与键拼写", retryable="N", action="隔离")
    register("CFG-608", "热更只读键被拒", "策略/预算类键需重启生效", retryable="N", action="拒写")
    # 其余域发布码(GRD/APR/LLM/PERS/CRED/TLB/CYC)同表登记,见 ERR §2 全量目录
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ValueError | 同码二次 register | — | 表内冲突即 bug;码不可覆盖,只能新码 |
| UnknownCode | 引用未登记码 | CYC-999 前置 | 走 ERR §1.5 五步新码流程,禁现场造码 |
**关联测试**:TC-GWT-ERR-01(注册表完整,每码具 ErrorSpec)→ tests/acceptance/test_f019_error_codes.py(§8)。

### raise_code(code: str, **ctx) -> NoReturn
**功能**:全系统唯一抛出入口:查表(未登记 → UnknownCode 兜底)→ 组装 PyHError(code+ctx+spec);每次调用 ≥1 行日志 + 供事件化(双落纪律,ERR §6.3)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| code | str | 是 | 已登记错误码 |
| **ctx | Any | 否 | 脱敏结构化现场(module/attempt/字段明细),只放脱敏值,secret 原文永不出凭据模块 |
**伪代码**:
```python
def raise_code(code: str, **ctx) -> NoReturn:
    spec = ERRORS.get(code)
    if spec is None:                                   # 未登记码:兜底而非静默
        spec = ERRORS["CYC-999"]
        ctx = {**ctx, "unknown_code": code}
        code = "CYC-999"
    log.error(f"code={code} name={spec.name} " + " ".join(f"{k}={v}" for k, v in ctx.items()))
    raise PyHError(code, ctx=ctx, spec=spec)           # 堆栈仅进本地 debug(ERR §1.4)
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| UnknownCode | ERRORS 查无此码 | CYC-999(兜底) | 新码走五步登记;禁止裸 raise str |
**关联测试**:TC-GWT-ERR-01(raise_code 未登记码抛 UnknownCode)、TC-ERR-06(码不变式)→ test_f019_error_codes.py(§8)。

### class PyHError(Exception)
**功能**:结构化错误基类:code/message/retryable/cause/context 五要素;str() 只含 `[code] name`(不吐 ctx 敏感值);提供 to_event/to_model_message/to_user_message 三形态。
**参数表**:
| 字段 | 类型 | 说明 |
|---|---|---|
| code | str | 注册码 |
| ctx | dict | 脱敏现场;可含 cause(底层异常引用) |
| spec | ErrorSpec | 注册表条目(name/advice/retryable) |
**伪代码**:
```python
class PyHError(Exception):
    def __init__(self, code: str, *, ctx: Optional[dict] = None, spec: Optional[ErrorSpec] = None):
        self.code = code
        self.ctx = dict(ctx or {})
        self.cause = self.ctx.pop("cause", None)           # 底层异常仅本地,不外泄文案
        self.spec = spec or ERRORS.get(code) or ERRORS["CYC-999"]
        self.retryable = self.spec.retryable               # R/F/N(注册表权威,每行不重复带)
        self.message = self.spec.name
        super().__init__(f"[{self.code}] {self.message}")  # str() 不含 ctx(防敏感值入日志)
```
**关联测试**:TC-F020(三端一致)、TC-GWT-ERR-06(传播链码不变式)→ test_f020_struct_errors.py(§8)。

### 域错误类族(EventError/BusError/PersistenceError/LLMError/GuardError/ApprovalError/ConfigError/CredentialError/ToolError/CycleError)
**功能**:十域异常类,按 ERR §1.2 域区间归属;跨模块边界透传 code,禁止模糊化成裸 Exception。
**伪代码**:
```python
class EventError(PyHError): ...        # EVT-1xx 事件域(拒写/隔离)
class BusError(PyHError): ...          # BUS-0xx 总线/注册表/生命周期
class PersistenceError(PyHError): ...  # PERS-2xx JSONL 真源
class LLMError(PyHError): ...          # LLM-3xx 模型域(驱动重试/降级)
class GuardError(PyHError): ...        # GRD-4xx 单调链(拒绝终局,零副作用)
class ApprovalError(PyHError): ...     # APR-5xx 审批流(安全默认拒)
class ConfigError(PyHError): ...       # CFG-6xx 配置合并+装配
class CredentialError(PyHError): ...   # CRED-7xx 凭据(拒而非空串)
class ToolError(PyHError): ...         # TLB-8xx 工具管道(回喂)
class CycleError(PyHError): ...        # CYC-9xx 主循环/兜底
# 语义别名(供调用点可读):UnknownEventType=EventError("EVT-102")……
# raise_code 是唯一出口;域类仅作 isinstance 归类与显式捕获,不做第二造错口。
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 任一域类实例 | 对应域失败路径(经 raise_code) | 各自码 | 按 ErrorSpec.action:拒写/回喂/降级/暂停/终态/隔离 |
**关联测试**:TC-F019(每码结构化类)、TC-GWT-ERR-04 → test_f019_error_codes.py、test_f014_guard.py(§8)。

### PyHError.to_event() -> dict
**功能**:事件化:system.error payload = code+hint/advice+ctx(脱敏),actor=system;禁用户 actor 冒名(ERR §5.3);tool.error/llm.error 由调用模块换 actor 落。
**伪代码**:
```python
def to_event(self) -> dict:
    return {"type": "system.error", "actor": "system",
            "payload": {"code": self.code,
                        "hint": f"{self.message}:{self.spec.advice}",
                        "ctx": self.ctx}}              # ctx 已脱敏;堆栈永不进事件
```
**异常表**:无;调用方负责按 ERR §6.3 双落(事件=审计事实、日志=排障)。
**关联测试**:TC-GWT-ERR-06(事件 payload.code==X)→ test_f020_struct_errors.py(§8)。

### PyHError.to_model_message() -> str
**功能**:给 LLM 的可行动文本:≤2000 字符,含 [code]+name+可行动 advice;供 tool.error/llm.error 回喂自纠;不含堆栈与 ctx 内部细节(ERR §1.4 ④)。
**伪代码**:
```python
def to_model_message(self) -> str:
    msg = f"错误[{self.code}]:{self.message}。建议:{self.spec.advice}"
    tip = self.ctx.get("detail") or self.ctx.get("hint")
    if tip:
        msg += f"明细:{str(tip)[:200]}"                 # 明细截断,防上下文爆炸
    return msg[:2000]                                   # 硬上限 2000 字符
```
**关联测试**:TC-GWT-ERR-06(LLM 文本含 [X]+advice)、TC-GWT-ERR-03(TLB-803 回喂明细)→ test_f020_struct_errors.py(§8)。

### PyHError.to_user_message() -> str
**功能**:给用户/远端:只含 code+advice,绝不吐 ctx 明细/内部路径/环境值(ERR §1.4 ⑤;远端响应横切 redact_out)。
**伪代码**:
```python
def to_user_message(self) -> str:
    return f"{self.code}:{self.spec.advice}"           # 仅码+建议;ctx 全部丢弃
```
**异常表**:无。
**关联测试**:TC-GWT-ERR-06(远端仅 code+advice)→ test_f020_struct_errors.py、test_f064_cli.py(§8)。

### struct_error(code: str, **kv) -> dict
**功能**:日志单行结构化(ERR §6.1):时间 级别 模块 code=xxx key=value;EVT-103/BUS 日志调用点复用。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| code | str | 是 | 错误码 |
| **kv | Any | 否 | 必带上下文字段(如事件写链:seq/type_/actor) |
**伪代码**:
```python
def struct_error(code: str, **kv) -> dict:
    spec = ERRORS.get(code, ERRORS["CYC-999"])
    return {"code": code, "name": spec.name,
            "retryable": spec.retryable, **kv}          # retryable 注册表字段不重复写
```
**关联测试**:TC-GWT-ERR-06(日志 code==X)、TC-GWT-ERR-01 → test_f019_error_codes.py(§8)。

### wrap_unexpected(e: Exception, module: str) -> PyHError
**功能**:非 PyHError 未预期异常兜底:归 CYC-999(堆栈仅本地 debug,禁当常态);未知失败绝不现场造码(ERR §1.5-2)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| e | Exception | 是 | 未预期异常(原始引用进 ctx.cause) |
| module | str | 是 | 归因模块名(agent-loop/tools/…) |
**伪代码**:
```python
def wrap_unexpected(e: Exception, module: str) -> PyHError:
    log.debug(traceback.format_exc())                    # 堆栈仅本地 debug
    return PyHError("CYC-999", ctx={"module": module,
                                    "cause": e, "type": type(e).__name__})
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| CYC-999(包装后抛) | 任意非 PyHError 未预期异常 | CYC-999 | 本地取堆栈归因;按 bug 提单;禁止重试该错误 |
**关联测试**:TC-GWT-ERR-07 兜底语义、test_f007_agent_loop.py(reason=error)→ test_f019_error_codes.py(§8)。

## 错误码消费方决策口诀(实现对齐,ERR §1.3)
401/429/超时 → 退避或降级(按码不按文本);TLB-802/803 → 回喂不降级,连败 2 次终止该轮;GRD-401 → 终局零副作用,同 call_id 不可重放(GRD-402);CFG-6xx → 装配失败,缺护栏不发请求;EVT-1xx → 拒写不进日志;PERS-202 → 强同步抛/异步暂停;CRED-701 → 拒而非空串;未知失败 → CYC-999,禁现场造码。

## 关联文档
- PRD-Core.md(F019 错误码体系、F020 结构化错误、§3.7 EVT 处置表)
- ERR.md(§1 设计原则与码结构、§2 全量目录、§3 明细、§5 传播链、§6 日志规范、§9.2 一致性注记——TLB-404 禁引用)
- EVENT-SCHEMA.md(§3.3.4 llm.error、§3.4.7 tool.error、§3.5.6 system.error 事件负载)
- CFG.md(§5.2 CFG-6xx 码表:601/602/603 已登记,607/608 落地同步 ERR)
- DIS-SEAM.md(§2.7 seam 通用异常、§4.7 总线错误码)
