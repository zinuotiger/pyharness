"""pyharness/governance/decision.py — 治理决策一等对象(S3-1)。

一句话职责:把"治理决策"从**值枚举**升格为**一等对象**——一次决策携带
主键(``decision_id``)、主体(``Principal``)、结果(``Verdict``)、依据
(``policy_refs`` / ``policy_fingerprint`` / ``guard_ids``)与输入摘要
(``inputs_digest``),供 S3-2 的运行时接线与 S4 的凭证/审计复用。

**本步(S3-1)只有数据契约 + 纯决策语义;无运行时接线。**

依赖方向(ADR-018:308,强制,有 AST 测试断言):本模块**只允许** import
标准库与 ``pyharness.errors``;**禁止** ``pyharness.core.*``(tools_guard /
tools_executor / scope / approval)、``pyharness.engine``、``pyharness.bus``、
``pyharness.persistence``。凡需引用 ``ToolCall`` / ``Scope`` 一律用 ``Any``
——不得用 ``TYPE_CHECKING`` 变通(保留 import 即保留方向依赖)。

治理层纯度(INV-G5):``DecisionEngine`` **不执行工具、不写文件、不 append
session、不发事件、不调审批通道、不实现 ``authorize()``、不建 receipt /
evidence / audit**。

契约增量(2026-09-14 S3-1 人工裁定;设计见 S3-1 会话设计):

1. **Δ-1 不重实现 g1–g7 waterfall**:``tools_guard.PolicyRule.check`` /
   ``GuardChain.evaluate`` 仍是**规则求值的唯一真源**。本模块的
   ``DecisionEngine`` 只做"决策装配 + 治理决策语义"——它**消费**一条已算好的
   ``EvaluationResult``(S3-2 由装配层传入),**不**自己编排规则求值。故本层
   不存在第二套 g1–g7 求值逻辑(守 G-2)。
2. **Δ-2 ``decision_id`` / ``ts`` 可注入**:构造期接受 ``id_factory`` /
   ``clock``;默认 uuid4 / UTC-now。否则"相同输入得到相同 Decision"不可测。
3. **Δ-3 ``inputs_digest`` 是入参字段**:算法 = 既有
   ``tools_executor._approval_binding``(复用而非重写),但治理层不得 import
   ``tools_executor``,故由调用方(S3-2/S4)传入,本步不计。
4. **Δ-5 ``Decision`` 与 ``tools_guard.Decision`` 并存**:后者是"值"(StrEnum),
   本类是"事件/凭证载体"。本步**不删除、不重命名**旧枚举(零回归);两者的
   值域由测试锁定为逐一对应。
5. **``CHANNELS`` 本地声明**:治理层不得 import ``core/approval.py``,故人类
   通道白名单在本地声明,并**由一致性测试**断言与 ``approval._HUMAN_CHANNELS``
   相等——把"复制"变成"受守卫的重复",而非静默的第二真源。

依赖风险继承(不作修复):``decision.issued`` 后续是**强同步治理证据事件**,
其持久化可靠性继承 **F-SYNC-1**(适配器内 flush 失败 → 行被丢弃 + 总线 EVT-103
隔离 → 调用方静默成功)。**F-SYNC-1 必须在 S4 Entry Gate 前关闭或取得架构豁免**;
本模块不修、不掩盖该缺口。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Optional, Protocol, runtime_checkable
from uuid import uuid4

from pyharness.errors import raise_code

# 人类通道白名单(与 core/approval.py::_HUMAN_CHANNELS 同源;本地声明理由见
# 模块 docstring Δ-5 后一条;一致性由 test_t14_channel_whitelist_matches_approval
# 守卫)。**不含** llm/tool/plugin——它们无权冒充人类裁决(APR-503)。
HUMAN_CHANNELS: tuple[str, ...] = ("cli", "web", "acp", "desktop")

# legacy ``by`` 的非法前缀 → PrincipalKind 映射(approval.py:743 明列这三类)。
_LEGACY_PREFIX_KIND: dict[str, "PrincipalKind"] = {}   # 延迟填充(StrEnum 定义后)


class PrincipalKind(StrEnum):
    """决策主体类别(设计 §3.1)。"""

    HUMAN = "human"      # cli / web / acp / desktop 通道
    AGENT = "agent"      # 会话内的 agent(LLM 意图)
    TOOL = "tool"        # 工具自身(无自主意志的代理者)
    PLUGIN = "plugin"    # 插件
    SYSTEM = "system"    # 框架内部(超时、清理、恢复)


_LEGACY_PREFIX_KIND.update({"llm": PrincipalKind.AGENT,
                            "tool": PrincipalKind.TOOL,
                            "plugin": PrincipalKind.PLUGIN})


@dataclass(frozen=True)
class Principal:
    """决策主体——取代裸 ``by: str`` 自由字符串(设计 §3.1)。

    ``by`` 的真实格式以**现网代码**为准(非文档推测):
      - ``"system"``(approval.py:305/401/541/558/567 的框架自决)
      - ``"<channel>"`` 裸通道名(cli.py:1460 ``by="cli"``;desktop/constants.py:11)
      - ``"<channel>:<id>"``(approval.py:747 的合法形态)
      - ``"llm:|tool:|plugin:<id>"``(approval.py:743 列明的非人类前缀)

    不变量(构造期强制):``kind=HUMAN`` ⇒ ``channel ∈ HUMAN_CHANNELS`` 且
    ``id`` 不以 ``llm:`` / ``tool:`` / ``plugin:`` 开头(延续 S-2 假冒审批防线)。
    """

    kind: PrincipalKind
    id: str
    channel: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PrincipalKind):
            raise_code("CYC-999", module="governance.decision", field="kind",
                       why="Principal.kind 必须为 PrincipalKind", got=str(self.kind))
        if not isinstance(self.id, str) or not self.id:
            raise_code("CYC-999", module="governance.decision", field="id",
                       why="Principal.id 必须为非空字符串")
        if self.kind is PrincipalKind.HUMAN:
            if self.channel not in HUMAN_CHANNELS:
                raise_code("APR-503", by=self.id if not self.channel else
                           f"{self.channel}:{self.id}",
                           why="HUMAN 主体的 channel 必须在人类通道白名单内")
            if self.id.startswith(("llm:", "tool:", "plugin:")):
                raise_code("APR-503", by=self.id,
                           why="HUMAN 主体 id 不得使用非人类前缀(假冒审批防线)")
        elif self.channel is not None and self.channel not in HUMAN_CHANNELS:
            raise_code("CYC-999", module="governance.decision", field="channel",
                       why="channel 若给出必须 ∈ HUMAN_CHANNELS",
                       got=str(self.channel))

    @classmethod
    def from_legacy_by(cls, by: Any) -> "Principal":
        """legacy ``by`` 字符串 → ``Principal``(无 I/O;非法格式 → APR-503)。

        解析面严格对齐现网真实取值:见类 docstring 的四类格式。
        """
        if not isinstance(by, str) or not by:
            raise_code("APR-503", why="legacy by 缺失:by 由框架按通道打,不可省略")
        if by == "system":
            return cls(PrincipalKind.SYSTEM, "system", None)
        for prefix, kind in _LEGACY_PREFIX_KIND.items():
            if by.startswith(prefix + ":"):
                rest = by[len(prefix) + 1:]
                if not rest:
                    raise_code("APR-503", by=by, why=f"{prefix}: 前缀后缺 id")
                return cls(kind, rest, None)
        for chan in HUMAN_CHANNELS:
            if by == chan:
                return cls(PrincipalKind.HUMAN, chan, chan)
            if by.startswith(chan + ":"):
                rest = by[len(chan) + 1:]
                if not rest:
                    raise_code("APR-503", by=by, why=f"{chan}: 前缀后缺 id")
                return cls(PrincipalKind.HUMAN, rest, chan)
        raise_code("APR-503", by=by,
                   why="非法 by:仅 system / 人类通道名或其 'channel:id' / "
                       "llm:|tool:|plugin: 前缀合法(任意自报身份无权冒充人类批准)")

    def to_legacy_by(self) -> str:
        """``Principal`` → legacy ``by``(与 ``from_legacy_by`` 双向无损)。"""
        if self.kind is PrincipalKind.SYSTEM:
            return self.id if self.channel is None else f"{self.channel}:{self.id}"
        if self.kind is PrincipalKind.HUMAN:
            return self.channel if self.id == self.channel else f"{self.channel}:{self.id}"
        prefix = {PrincipalKind.AGENT: "llm", PrincipalKind.TOOL: "tool",
                  PrincipalKind.PLUGIN: "plugin"}[self.kind]
        return f"{prefix}:{self.id}"


class Verdict(StrEnum):
    """治理决策三值——值域与 ``tools_guard.Decision``(旧 StrEnum)**逐一对应**。

    ``Verdict.REJECT == "reject"`` 成立(StrEnum 语义),故 S3-2 接线后既有
    ``tools_executor.py`` 的 ``d == "reject"`` / ``d != "allow"`` **零改动**。
    """

    ALLOW = "allow"
    REJECT = "reject"
    APPROVAL = "approval"


def _utc_now() -> str:
    """默认时钟(ISO8601 UTC;可被 ``clock`` 注入覆盖以便确定性测试)。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, eq=False)
class Decision:
    """★ 治理层核心产物:一次有主体、有依据、有 ID、可引用的决策(设计 §3.3)。

    取代旧 ``tools_guard.Decision`` 三值枚举——**枚举是"值",本对象是"事件"**。
    本步两者并存(Δ-5)。

    兼容面(M3 硬要求):``__eq__`` 支持与 ``str`` / ``Verdict`` 比较,
    ``__str__`` 返回 verdict 字面量。

    相等/哈希语义(实现选择,已在测试中锁定):
      - ``Decision == Decision``:逐字段全等;
      - ``Decision == str|Verdict``:比较 verdict 字面量;
      - ``__hash__`` 取 **verdict** —— 使"``d == 'reject'`` ⇒
        ``hash(d) == hash('reject')``"的 eq/hash 契约成立。代价:同 verdict 的
        不同 Decision 哈希碰撞(合法;本对象是值载体而非集合键)。跨类型相等
        天然非传递(``d1 == 'reject'``、``d2 == 'reject'`` 但 ``d1 != d2``),
        故**不要**把 Decision 与裸字符串混入同一 set/dict。
    """

    decision_id: str
    verdict: Verdict
    tool: str
    policy_refs: tuple[str, ...] = ()
    guard_ids: tuple[str, ...] = ()
    policy_fingerprint: str = ""
    inputs_digest: str = ""
    principal: Optional[Principal] = None
    ts: str = ""
    approval_ref: Optional[int] = None
    receipt_id: Optional[str] = None

    def _fields(self) -> tuple:
        return (self.decision_id, self.verdict, self.tool, self.policy_refs,
                self.guard_ids, self.policy_fingerprint, self.inputs_digest,
                self.principal, self.ts, self.approval_ref, self.receipt_id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Decision):
            return self._fields() == other._fields()
        if isinstance(other, str):            # 含 Verdict(StrEnum 是 str 子类)
            return str(self.verdict) == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.verdict)

    def __str__(self) -> str:
        return str(self.verdict)

    @property
    def is_terminal_reject(self) -> bool:
        """终局拒绝:零副作用、无续跑 API(旧 ``Decision.REJECT`` 语义)。"""
        return self.verdict is Verdict.REJECT


@dataclass(frozen=True)
class EvaluationResult:
    """规则级求值结果面(``GuardChain.evaluate`` 的产出形状)。

    **S3-1 不产生它**——由装配层(S3-2)把既有求值真源的产出传入,本层只做升格。
    ``verdict`` 为旧三值字面量;``guard_ids`` / ``policy_refs`` 为已命中面。
    """

    verdict: str
    guard_ids: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()


@runtime_checkable
class ApprovalChannel(Protocol):
    """HITL 通道契约——**仅接口声明**(设计 §3.3)。

    由 ``core/approval.py`` **原位**实现(鸭子类型,不 import 治理层);
    本步(S3-1)**不连接** ``approval.py``、不发起任何请求。
    """

    async def request(self, call: Any, args_summary: str, ctx: Any, *,
                      binding: Optional[str] = None) -> str: ...

    def grant_binding(self, call_id: str) -> Optional[str]: ...

    async def cancel_all(self, reason: str) -> None: ...


class DecisionEngine:
    """决策装配 + 治理决策语义(纯;无 I/O、无事件、无执行)。

    **不重实现 g1–g7**(Δ-1):规则求值的唯一真源仍是
    ``tools_guard.PolicyRule.check`` / ``GuardChain.evaluate``;本类**消费**
    一条已算好的 ``EvaluationResult``,只施加**治理决策语义**:

      1. verdict 归一(Verdict 值域外 → CYC-999);
      2. **单调性**:前序决策若为终局 REJECT,批准不可将其放宽(G-5 / GRD-403);
      3. **无通道即拒**:verdict=APPROVAL 但无可用审批通道 → REJECT(APR-501 语义);
      4. **批准结果**:``approval_verdict`` 为 ``denied`` / ``timeout`` 时
         APPROVAL → REJECT(``granted`` 保持 APPROVAL,由执行侧重入校验);
      5. 装配 Decision(id/ts 经注入的工厂与时钟产生)。
    """

    def __init__(self, *, id_factory: Optional[Callable[[], str]] = None,
                 clock: Optional[Callable[[], str]] = None) -> None:
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._clock = clock or _utc_now

    async def decide(self, evaluation: EvaluationResult, *,
                     principal: Principal,
                     call: Any = None,
                     policy: Any = None,
                     tool: Optional[str] = None,
                     inputs_digest: str = "",
                     approval_available: bool = True,
                     approval_verdict: Optional[str] = None,
                     prior: Optional[Decision] = None,
                     approval_ref: Optional[int] = None) -> Decision:
        """把一条求值结果升格为治理决策(纯函数;确定性由注入设施保证)。"""
        try:
            verdict = Verdict(evaluation.verdict)
        except ValueError:
            raise_code("CYC-999", module="governance.decision", field="verdict",
                       why="求值结果 verdict 不在 {allow,reject,approval} 内",
                       got=str(evaluation.verdict))

        # 2) 单调性:终局 reject 不可被批准放宽。
        if prior is not None and prior.verdict is Verdict.REJECT:
            verdict = Verdict.REJECT
        # 3) 无审批通道 → APPROVAL 降为 REJECT。
        elif verdict is Verdict.APPROVAL and not approval_available:
            verdict = Verdict.REJECT
        # 4) 审批结果:denied / timeout 使 APPROVAL 作废。
        elif verdict is Verdict.APPROVAL and approval_verdict in ("denied", "timeout"):
            verdict = Verdict.REJECT

        tool_name = tool if tool is not None else str(getattr(call, "name", "") or "")
        fingerprint = str(getattr(policy, "fingerprint", "") or "")
        return Decision(
            decision_id=self._id_factory(),
            verdict=verdict,
            tool=tool_name,
            policy_refs=tuple(evaluation.policy_refs),
            guard_ids=tuple(evaluation.guard_ids),
            policy_fingerprint=fingerprint,
            inputs_digest=inputs_digest,
            principal=principal,
            ts=self._clock(),
            approval_ref=approval_ref,
        )


__all__ = [
    "PrincipalKind", "Principal", "Verdict", "Decision",
    "EvaluationResult", "ApprovalChannel", "DecisionEngine",
    "HUMAN_CHANNELS",
]
