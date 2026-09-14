"""pyharness/governance/context.py — 治理层单实例上下文(S2-1 骨架;S3-2-1 加 authorize)。

一句话职责:治理层的**唯一挂载面**——``ctx.governance``(ADR-018 契约冻结:
单实例挂载,**禁止**向 ``Ctx`` 追加治理散字段)。

形状:
    policy     : PolicyEngine      ← S2-1
    decisions  : DecisionEngine    ← S3-2-1(本步接线)
    receipts   : Any = None        ← S4: ReceiptStore
    evidence   : Any = None        ← S5: EvidenceCollector
    audit      : Any = None        ← S5: AuditSystem

``authorize()``(S3-2-1,B4):``tool_executor`` 关 2 的**唯一治理入口**——
``GuardChain.evaluate_detailed() → EvaluationResult → DecisionEngine.decide()
→ Decision``。它**只做编排**,不重新求值、不重新判定 verdict。

决策语义所有权(S3-2-1 R1 裁定):``critical→reject`` 归 ``GuardChain``;
``high+无通道→reject`` 与 ``denied/timeout→不执行`` 归 ``approval.py``/executor。
故本方法**不得**向 ``DecisionEngine.decide()`` 传 ``approval_available`` /
``approval_verdict``——有测试守卫钉死这一点。

本阶段**不发射** ``decision.issued``(事件注册/载荷/executor 两出口接线属 S3-2-2)。

四字段先以 ``None`` 占位而非省略,是为了**冻结形状**(ADR-018 §目录冻结):
装配层与测试可稳定断言 ``ctx.governance.decisions is None``("尚未接线"),
而不是 ``AttributeError``——缺失可见,不静默。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pyharness.errors import raise_code
from pyharness.governance.decision import (Decision, DecisionEngine,
                                           Principal, PrincipalKind)
from pyharness.governance.policy import PolicyEngine

# 临时治理主体(S3-2-2):**不是**正式用户身份模型——M5/S4 再引入真正的
# principal 来源。不使用 actor="tool" 冒充决策主体(actor 是事件归属,非决策者)。
# approval transport 的通道身份(channel)同样不等于治理 principal。
_TEMPORARY_PRINCIPAL = Principal(PrincipalKind.SYSTEM, "pyharness-runtime", None)


@dataclass
class GovernanceContext:
    """治理层上下文(policy + decisions 已接线;receipts/evidence/audit 仍为形状占位)。"""

    policy: PolicyEngine
    # ---- S3~S5 形状占位(None = "尚未接线")----
    decisions: Optional[DecisionEngine] = None   # S3: DecisionEngine(关 2 出口升格)
    receipts: Any = None        # S4: ReceiptStore(凭证)
    evidence: Any = None        # S5: EvidenceCollector(证据)
    audit: Any = None           # S5: AuditSystem(审计因果链)

    def policy_fingerprint(self) -> str:
        """当前策略指纹(审计/凭证引用面;纯只读)。"""
        return self.policy.fingerprint()

    @staticmethod
    def principal_of(ctx: Any) -> Principal:
        """**临时**主体派生(S3-2-2;见 ``_TEMPORARY_PRINCIPAL``)。

        当前运行时无正式身份来源(M5/Principal 属 S4),故统一返回稳定的
        SYSTEM 主体——**不伪装** HUMAN/AGENT,也不把 approval 通道当作主体。
        """
        return _TEMPORARY_PRINCIPAL

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
        evaluation = await chain.evaluate_detailed(call, scope)
        # 2) 装配决策(纯;策略取自引擎当前策略——指纹随内容变)
        decision = await self.decisions.decide(
            evaluation, principal=principal or self.principal_of(ctx), call=call,
            policy=self.policy.current(scope),
            inputs_digest=inputs_digest, prior=prior, approval_ref=approval_ref)
        # 3) 治理证据事件(强同步;一次 authorize 恰一条)
        await self._emit_decision_issued(ctx, decision, call)
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
            "principal_id": p.id if p is not None else "pyharness-runtime",
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
