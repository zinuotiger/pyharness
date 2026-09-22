"""pyharness/governance/context.py — 治理层单实例上下文(S2-1 骨架;S3-2-1 加 authorize)。

一句话职责:治理层的**唯一挂载面**——``ctx.governance``(ADR-018 契约冻结:
单实例挂载,**禁止**向 ``Ctx`` 追加治理散字段)。

形状(全部已接线):
    policy     : PolicyEngine        ← S2-1
    decisions  : DecisionEngine      ← S3-2-1
    receipts   : ReceiptStore        ← S4/M4
    evidence   : EvidenceCollector   ← S5/M6
    audit      : AuditSystem         ← S5/M7(replay-only,不订阅)

``authorize()``(S3-2-1,B4):``tool_executor`` 关 2 的**唯一治理入口**——
``GuardChain.evaluate_detailed() → EvaluationResult → DecisionEngine.decide()
→ Decision``。它**只做编排**,不重新求值、不重新判定 verdict。

决策语义所有权(S3-2-1 R1 裁定):``critical→reject`` 归 ``GuardChain``;
``high+无通道→reject`` 与 ``denied/timeout→不执行`` 归 ``approval.py``/executor。
故本方法**不得**向 ``DecisionEngine.decide()`` 传 ``approval_available`` /
``approval_verdict``——有测试守卫钉死这一点。

四字段先以 ``None`` 占位而非省略,是为了**冻结形状**(ADR-018 §目录冻结):
装配层与测试可稳定断言 ``ctx.governance.decisions is None``("尚未接线"),
而不是 ``AttributeError``——缺失可见,不静默。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pyharness.errors import raise_code
from pyharness.governance.audit import AuditSystem
from pyharness.governance.decision import (Decision, DecisionEngine,
                                           Principal)
from pyharness.governance.evidence import EvidenceCollector
from pyharness.governance.policy import PolicyEngine
from pyharness.governance.receipt import ReceiptStore

# 无通道时的主体(M5/S4-P1-3):headless / 无人类通道 = 框架自身驱动;**沿用**
# approval 侧 ``by="system"`` 的既有语义(非新规则),经 ``from_legacy_by`` 统一派生。
_FRAMEWORK_BY = "system"


@dataclass
class GovernanceContext:
    """治理层上下文:五个构件全部已接线(None 仅表示"该环境未装配")。"""

    policy: PolicyEngine
    # ---- S5 形状:五个构件全部已接线(None 仅表示"该环境未装配")----
    decisions: Optional[DecisionEngine] = None   # S3: DecisionEngine(关 2 出口升格)
    receipts: Optional[ReceiptStore] = None      # S4/M4: 凭证(已接线)
    evidence: Optional[EvidenceCollector] = None   # S5/M6: 证据(S5-2b 已接线)
    audit: Optional[AuditSystem] = None          # S5/M7: 审计(S5-3b 已接线)

    def policy_fingerprint(self) -> str:
        """当前策略指纹(审计/凭证引用面;纯只读)。"""
        return self.policy.fingerprint()

    @staticmethod
    def principal_of(ctx: Any) -> Principal:
        """正式主体派生(M5/S4-P1-3):取自运行时**真实 caller/channel identity**。

        ``ctx.channel`` 由各外壳在装配时**框架侧**写入(非客户端自报),经
        ``EngineSpine.channel`` → ``create_agent`` / ``_runtime_ctx`` 落到 agent
        ctx(GAP-11 修复:此前该跳缺失,agent ctx 无 ``channel`` 属性,致每次授权
        都静默降级为 SYSTEM):

        - CLI:``"cli"``;headless → ``None``(``cli.py``)
        - ACP:``"acp:<client_id>"``(``acp.py``;显式忽略客户端自报 ``by``)
        - Desktop:``"desktop"``(``application/service.py`` / ``desktop/app.py``)

        经**既有** ``Principal.from_legacy_by`` 统一解析(HUMAN + 通道 + id)。

        **五态语义(N5 冻结;规范表见 ``core/channel.py`` 模块 docstring)**——
        本层因 ADR-018:308 不得 import ``pyharness.core.*``,故**消费**本包已有的
        ``Principal.from_legacy_by`` 分类,并由 ``tests/invariants`` 的真值表断言
        与 ``core.channel.resolve_channel`` **逐状态一致**(沿用 ``HUMAN_CHANNELS``
        的"受守卫的重复"先例):

        1. ``channel="cli|web|acp:<id>|desktop"`` ⇒ HUMAN 主体(前缀匹配,``acp:<id>``
           **保留 ACP 身份**,不降级);
        2. ``channel`` **显式声明为 ``None``** ⇒ headless / 无人类通道 ⇒
           **明确的 SYSTEM 主体**(沿用 approval 侧 ``by="system"`` 既有语义,
           **绝不伪装** HUMAN/AGENT);
        3. ``channel`` **属性缺失(从未声明)** ⇒ **``APR-503`` fail-closed**
           ——"没人声明过这是什么通道"与"已声明无通道"是两件事;
        4. ``channel`` 为**非白名单字符串**(如 ``"hacker"``)⇒ ``APR-503``;
        5. ``channel`` 为**空串/非字符串**(如 ``""``)⇒ ``APR-503``
           ——**修复点**:此前 ``ch or _FRAMEWORK_BY`` 的 falsy 短路会把 ``""``
           **静默降级为 system**,与第 3/4 条的 fail-closed 精神相悖。
        """
        if not hasattr(ctx, "channel"):
            raise_code("APR-503",
                       why="ctx.channel 未声明:通道身份缺失,禁止静默降级为 "
                           "system(fail-closed)。外壳须显式声明 "
                           "channel='cli'|'acp:<id>'|'desktop',或显式声明 "
                           "headless(channel=None)。")
        ch = getattr(ctx, "channel", None)
        if ch is None:                       # 显式 headless:唯一合法的无通道声明
            return Principal.from_legacy_by(_FRAMEWORK_BY)
        # 其余一律交既有分类:白名单/前缀合法 ⇒ HUMAN;空串/非白名单 ⇒ APR-503。
        # 不再做 falsy 合并 —— 声明就是声明,不得被 `or` 改写。
        return Principal.from_legacy_by(ch)

    async def authorize(self, call: Any, ctx: Any, *,
                        principal: Optional[Principal] = None,
                        inputs_digest: str = "",
                        prior: Optional[Decision] = None,
                        approval_ref: Optional[int] = None) -> Decision:
        """唯一治理入口(S3-2-1 建立;S3-2-2 起发射 ``decision.issued``)。

        调用链:``GuardChain.evaluate_detailed()`` → ``EvaluationResult`` →
        ``DecisionEngine.decide()`` → ``Decision`` → ``session.append``。

        **scope 前置的唯一运行时所有者是 ``GuardChain._evaluate_full``**——
        本方法不自行 ``scope.can_use``,避免与规则链重复计算同一条件(职责上提,
        非重复计算)。

        **不传** ``approval_available`` / ``approval_verdict``(R1:该语义归
        ``approval.py``/executor)。

        异常:治理层未接线(``decisions``/``chain`` 为 None)→ ``CYC-999``,
        fail-closed(绝不返回"看似授权的"决策)。
        """
        if self.decisions is None:
            raise_code("CYC-999", module="governance.context", field="decisions",
                       why="治理层未接线:decisions 为空,禁止授权(fail-closed)")
        chain = self.policy.chain
        if chain is None:
            raise_code("CYC-999", module="governance.context", field="chain",
                       why="治理层未接线:guard 链为空,无法求值(fail-closed)")
        scope = getattr(ctx, "scope", None)
        # 1) 求值(唯一真源;verdict 与 refs 来自同一次实际求值)
        #    session=getattr(ctx,"session",None)(ADR-021):guard.evaluated /
        #    guard.rejected 必须与同 call_id 的 tool.call/tool.result 落在**发起
        #    该调用**的会话——缺此一跳,子 Agent 的守卫事件会落到装配期会话
        #    (父),使其日志内 INV-04 不可满足(AuditSystem 报 NO-GUARD-EVENT)。
        #    链仍唯一,只换汇点。用 getattr 缺省 None = 沿用装配期会话,与本方法
        #    对 ctx 其余属性(scope/channel)的防御式取法一致,且对未装配 ctx.session
        #    的调用方(轻装配/单测替身)保持既有语义不变。
        evaluation = await chain.evaluate_detailed(
            call, scope, session=getattr(ctx, "session", None))
        # 2) 装配决策(纯;策略取自引擎当前策略——指纹随内容变)
        decision = await self.decisions.decide(
            evaluation, principal=principal or self.principal_of(ctx), call=call,
            policy=self.policy.current(scope),
            inputs_digest=inputs_digest, prior=prior, approval_ref=approval_ref)
        # 3) 治理证据事件(强同步;一次 authorize 恰一条)
        await self._emit_decision_issued(ctx, decision, call)
        # 4) 决策凭证(M4;强同步):按 receipt_kind_of 产生规则发射——
        #    ALLOW/REJECT → kind="decision";审批后重新授权(D2)→ kind="approval";
        #    D1(审批请求决策)→ 不产生(R-2)。未接线(纯内存/单测)时降级为不发射。
        if self.receipts is not None:
            await self.receipts.emit(ctx, decision,
                                     call_id=getattr(call, "call_id", "") or "")
        return decision

    @staticmethod
    async def _emit_decision_issued(ctx: Any, decision: Decision,
                                    call: Any) -> None:
        """发射 ``decision.issued``(一次 authorize 恰一条)。

        I/O 边界:仅 ``session.append``(``sync=True``)——治理层"对下只写事件"
        (ADR-013);``call_id`` 走 ``Envelope.trace``(不入载荷)。
        """
        sess = getattr(ctx, "session", None)
        if sess is None:
            return                                   # 未接线(纯内存/单测):降级
        p = decision.principal
        payload = {
            "decision_id": decision.decision_id,
            "verdict": str(decision.verdict),
            "tool": decision.tool,
            "guard_ids": list(decision.guard_ids),
            "policy_refs": list(decision.policy_refs),
            "policy_fingerprint": decision.policy_fingerprint,
            "inputs_digest": decision.inputs_digest,
            "principal_kind": str(p.kind) if p is not None else "system",
            "principal_id": p.id if p is not None else _FRAMEWORK_BY,
            "principal_channel": p.channel if p is not None else None,
            "ts": decision.ts,
            "approval_ref": decision.approval_ref,
            "supersedes": decision.supersedes,
        }
        r = sess.append("decision.issued", payload, actor="system", sync=True,
                        trace={"call_id": getattr(call, "call_id", "")})
        if hasattr(r, "__await__"):
            await r


__all__ = ["GovernanceContext"]
