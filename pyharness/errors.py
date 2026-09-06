"""pyharness/errors.py — 全系统唯一失败语义层 (specs/errors.py.md)

ErrorSpec 注册表 + PyHError 异常树 + 唯一抛出入口 raise_code
+ 三消费者转换 (to_model_message/to_user_message/to_event)。
错误码契约以 ERR.md 为登记册;禁止裸 raise str、无码异常跨边界、现场造码。
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import Any, NoReturn, Optional

log = logging.getLogger("pyharness.errors")


# ---------------------------------------------------------------- ErrorSpec
@dataclass(frozen=True)
class ErrorSpec:
    """错误码注册表条目:code/name/advice 必填 + 三轴分类 (ERR §1.3)。"""

    code: str
    name: str
    advice: str
    retryable: str = "N"        # R=自动退避 / F=修复后重试 / N=不可重试
    action: str = "拒写"        # 拒写|回喂|降级|暂停|终态|隔离
    fatal: bool = False


# ------------------------------------------------------------------ 注册表
ERRORS: dict[str, ErrorSpec] = {}


def register(code: str, name: str, advice: str, *,
             retryable: str = "N", action: str = "拒写", fatal: bool = False) -> None:
    """登记单码;重复登记拒绝 (码一经发布不改含义,禁覆盖)。"""
    if code in ERRORS:
        raise ValueError(f"错误码重复登记:{code}")
    ERRORS[code] = ErrorSpec(code, name, advice, retryable, action, fatal)


def register_default_codes() -> None:
    """发布码全量入册 (ERR.md §2 全量目录权威;与 ERR.md/EVENT-SCHEMA 同步)。"""
    # ===== BUS 域 =====
    register("BUS-002", "保留名冲突(注册/卸载脊柱八模块或 spine 子系统)", "拒绝,不改注册表,registry.updated 留痕")
    register("BUS-003", "非法状态迁移", "拒绝,状态机层抛(F006)")
    # ===== EVT 域 =====
    register("EVT-100", "信封/载荷非法", "拒写,返字段明细,修正后重试")
    register("EVT-101", "seq 不连续/重复", "拒写;疑丢事件跑 repair")
    register("EVT-102", "类型未注册", "拒写;先注册 schema 才能 emit")
    register("EVT-103", "订阅者异常", "封装隔离,不中断其他订阅者")
    register("EVT-104", "终态写违规", "拒写;查绕过路径(INV-01)")
    register("EVT-105", "未知斜杠命令", "system.error 回显,会话继续,零 LLM")
    register("EVT-106", "先于 created", "拒写")
    # ===== PERS 域 =====
    register("PERS-201", "回放坏行", "记跳+隔离,不中断回放;删否经 repair")
    register("PERS-202", "落盘失败", "强同步抛错;异步→system.error+会话暂停(拒新不丢旧),repair 恢复")
    register("PERS-221", "spill 写失败", "结构化错误+tool.error 回喂")
    register("PERS-222", "spill 越权读", "拒绝零读取,回喂")
    register("PERS-223", "spill 超限拒写", "拒写,回喂")
    # ===== LLM 域 =====
    register("LLM-301", "超时", "R 退避≤4(落 llm.retry)→降级")
    register("LLM-302", "认证失败", "不重试直接降级,带 degraded_from;查 CRED-701")
    register("LLM-303", "可重试上游错", "R 退避≤4,耗尽降级;可取消")
    register("LLM-304", "业务性失败", "不重试不降级上抛;agent-loop 收 reason=error")
    register("LLM-305", "预算超限", "任务成本超预算,中止循环(reason=budget)")
    register("LLM-310", "链尾全败", "终止任务(reason=error);降级>5 次/会话告警")
    register("LLM-399", "模型域未预期", "非 PyHError 异常兜底归因;按 bug 提单,禁重试")
    # ===== GRD 域 =====
    register("GRD-401", "调用被拒", "终局:guard.rejected 强同步,Provider 零执行,无 tool.result")
    register("GRD-402", "重放已拒调用", "拒绝+system.error 防重放")
    register("GRD-403", "批准后被新拒", "以新决策为准(非错误),单调性高于批准")
    # ===== APR 域 =====
    register("APR-501", "无审批通道", "直接拒;不发 approval.requested,无等待")
    register("APR-502", "等待被取消", "denied,无悬挂 Future")
    register("APR-503", "裁决重放/未知 id", "system.error,不重复执行")
    # ===== CFG 域 =====
    register("CFG-601", "配置非法/越权", "拒绝启动会话,列非法字段;HARDENED 只紧不松")
    register("CFG-602", "模板缺变量", "结构化错误,会话继续")
    register("CFG-603", "护栏段构建失败", "拒绝本次 LLM 调用(缺护栏不发请求)")
    register("CFG-607", "未知配置键/白名单外 env", "结构化错误;核对 CFG §4.3 白名单与键拼写")
    register("CFG-608", "热更只读键被拒", "策略/预算类键需重启生效;拒热更")
    # ===== CRED 域 =====
    register("CRED-701", "凭据缺失", "拒绝(结构化错误),绝不空串/None 续跑")
    register("CRED-702", "权限过宽(>600)", "启动拒载")
    register("CRED-703", "疑似密钥外泄", "system.error 告警+打码放行")
    # ===== TLB 域 =====
    register("TLB-801", "重复/非法注册", "拒绝并提示注销重注册;无脏数据")
    register("TLB-802", "工具未注册", "tool.error 回喂 LLM 自查,不静默")
    register("TLB-803", "参数校验失败", "回喂明细零执行(INV-06);连败 2 次终止轮")
    register("TLB-805", "执行超时/异常", "tool.error 回喂,重试性由 LLM 判断")
    register("TLB-806", "二进制拒读", "结构化错误,提示换工具")
    # ===== CYC 域 =====
    register("CYC-999", "未预期异常", "system.error+终态 reason=error;堆栈仅本地 debug")


# ------------------------------------------------------------ 唯一抛出入口
def _domain_class(code: str) -> type[PyHError]:
    prefix = code.split("-", 1)[0] if "-" in code else ""
    return _DOMAIN_CLASSES.get(prefix, PyHError)


def raise_code(code: str, **ctx: Any) -> NoReturn:
    """全系统唯一抛出入口:查表→按码域抛对应域类;未登记码兜底 CYC-999。

    双落纪律:每次 raise_code ≥1 行日志 (code=xxx key=value)。
    ctx 只放脱敏结构化现场;secret 原文永不出凭据模块。
    """
    spec = ERRORS.get(code)
    if spec is None:
        spec = ERRORS.get("CYC-999", ErrorSpec("CYC-999", "未知内部错误", "查日志归因"))
        ctx = {**ctx, "unknown_code": code}
        code = "CYC-999"
    log.error("code=%s name=%s %s", code, spec.name,
              " ".join(f"{k}={v}" for k, v in ctx.items()))
    raise _domain_class(code)(code, ctx=ctx, spec=spec)


# ---------------------------------------------------------------- PyHError
class PyHError(Exception):
    """结构化错误基类:code/message/retryable/cause/context 五要素。

    str() 只含 [code] name,不吐 ctx 敏感值。
    """

    def __init__(self, code: str, *, ctx: Optional[dict] = None,
                 spec: Optional[ErrorSpec] = None):
        self.code = code
        self.ctx = dict(ctx or {})
        self.cause = self.ctx.pop("cause", None)      # 底层异常仅本地,不外泄文案
        self.spec = spec or ERRORS.get(code) or ERRORS.get(
            "CYC-999", ErrorSpec("CYC-999", "未知内部错误", "查日志归因"))
        self.retryable = self.spec.retryable
        self.message = self.spec.name
        super().__init__(f"[{self.code}] {self.message}")

    def to_event(self) -> dict:
        """事件化:system.error payload;堆栈永不进事件。"""
        return {"type": "system.error", "actor": "system",
                "payload": {"code": self.code,
                            "hint": f"{self.message}:{self.spec.advice}",
                            "ctx": self.ctx}}

    def to_model_message(self) -> str:
        """给 LLM 的可行动文本:≤2000 字符,含 [code]+name+advice。"""
        msg = f"错误[{self.code}]:{self.message}。建议:{self.spec.advice}"
        tip = self.ctx.get("detail") or self.ctx.get("hint")
        if tip:
            msg += f"明细:{str(tip)[:200]}"
        return msg[:2000]

    def to_user_message(self) -> str:
        """给用户/远端:只含 code+advice,绝不吐 ctx 明细。"""
        return f"{self.code}:{self.spec.advice}"


# ------------------------------------------------------------ 域错误类族
class EventError(PyHError): ...        # EVT-1xx 事件域
class BusError(PyHError): ...          # BUS-0xx 总线/注册表/生命周期
class PersistenceError(PyHError): ...  # PERS-2xx JSONL 真源
class LLMError(PyHError): ...          # LLM-3xx 模型域
class GuardError(PyHError): ...        # GRD-4xx 单调链
class ApprovalError(PyHError): ...     # APR-5xx 审批流
class ConfigError(PyHError): ...       # CFG-6xx 配置
class CredentialError(PyHError): ...   # CRED-7xx 凭据
class ToolError(PyHError): ...         # TLB-8xx 工具管道
class CycleError(PyHError): ...        # CYC-9xx 主循环/兜底

# 语义别名(供调用点可读;raise_code 仍是唯一造错口)
UnknownCode = CycleError

# 错误码域前缀 → 域异常类映射(raise_code 按码域抛对应类,支持 isinstance 归类)
_DOMAIN_CLASSES = {
    "EVT": EventError, "BUS": BusError, "PERS": PersistenceError,
    "LLM": LLMError, "GRD": GuardError, "APR": ApprovalError,
    "CFG": ConfigError, "CRED": CredentialError, "TLB": ToolError,
    "CYC": CycleError,
}


# ------------------------------------------------------------- 工具函数
def struct_error(code: str, **kv: Any) -> dict:
    """日志单行结构化:含 code/name/retryable + 调用点补充字段。"""
    spec = ERRORS.get(code) or ERRORS.get(
        "CYC-999", ErrorSpec("CYC-999", "未知内部错误", "查日志归因"))
    return {"code": code, "name": spec.name, "retryable": spec.retryable, **kv}


def wrap_unexpected(e: Exception, module: str) -> PyHError:
    """非 PyHError 未预期异常兜底:归 CYC-999;堆栈仅本地 debug。"""
    log.debug("unexpected error in %s:\n%s", module, traceback.format_exc())
    return PyHError("CYC-999", ctx={"module": module,
                                    "cause": e, "type": type(e).__name__})


# import 时全量入册(幂等:重复 import 不重复登记)
register_default_codes()
