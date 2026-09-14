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
from pyharness.governance.decision import Decision, DecisionEngine, Principal
from pyharness.governance.policy import PolicyEngine


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

    async def authorize(self, call: Any, ctx: Any, *,
                        principal: Principal,
                        inputs_digest: str = "",
                        prior: Optional[Decision] = None,
                        approval_ref: Optional[int] = None) -> Decision:
        """唯一治理入口(S3-2-1;B4):编排求值 → 装配决策。

        调用链:``GuardChain.evaluate_detailed()`` → ``EvaluationResult`` →
        ``DecisionEngine.decide()`` → ``Decision``。

        参数:
            call    : 待裁决调用(鸭子 ToolCall;本层只透传给求值与装配)
            ctx     : 运行上下文(取 ``ctx.scope``;鸭子类型,治理层不 import core)
            principal: 决策主体(由外壳按通道派生,不可由客户端自报)
            inputs_digest: 授权↔执行绑定摘要(**入参字段**,本层不计算)
            prior   : 前一 Decision(审批重入表达继承关系;无则 None)
            approval_ref: 关联的审批 id(可选)

        **不传** ``approval_available`` / ``approval_verdict``(R1:该语义归
        ``approval.py``/executor,本层不得重复计算)。

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
        # 1) 求值(唯一真源;verdict 与 refs 来自同一次实际求值)
        evaluation = await chain.evaluate_detailed(call, getattr(ctx, "scope", None))
        # 2) 装配决策(纯;策略取自引擎当前策略——指纹随内容变)
        return await self.decisions.decide(
            evaluation, principal=principal, call=call,
            policy=self.policy.current(getattr(ctx, "scope", None)),
            inputs_digest=inputs_digest, prior=prior, approval_ref=approval_ref)


__all__ = ["GovernanceContext"]
