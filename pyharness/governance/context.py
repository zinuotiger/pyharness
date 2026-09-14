"""pyharness/governance/context.py — 治理层单实例上下文(S2-1 骨架)。

一句话职责:治理层的**唯一挂载面**——``ctx.governance``(ADR-018 契约冻结:
单实例挂载,**禁止**向 ``Ctx`` 追加治理散字段)。

M2 形状(本步只挂 policy):
    policy     : PolicyEngine      ← 已实现(S2-1)
    decisions  : Any = None        ← S3: DecisionEngine
    receipts   : Any = None        ← S4: ReceiptStore
    evidence   : Any = None        ← S5: EvidenceCollector
    audit      : Any = None        ← S5: AuditSystem

**``authorize()`` 不在本步声明**(Δ-5):它是 ``tool_executor`` 关 2 的唯一入口,
返回类型 ``Decision`` 属 S3 Decision 模型——M2 声明它要么引用不存在的类型、
要么是半成品。**偏离登记见 S2-1 报告**;S3 补齐时同步接 ``tool_executor.py:449``。

四字段先以 ``None`` 占位而非省略,是为了**冻结形状**(ADR-018 §目录冻结):
装配层与测试可稳定断言 ``ctx.governance.decisions is None``("尚未接线"),
而不是 ``AttributeError``——缺失可见,不静默。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyharness.governance.policy import PolicyEngine


@dataclass
class GovernanceContext:
    """治理层上下文(M2 只挂 policy;其余为 S3~S5 的形状占位)。"""

    policy: PolicyEngine
    # ---- S3~S5 形状占位(None = "尚未接线";不得在 M2 实现其类型)----
    decisions: Any = None       # S3: DecisionEngine(关 2 出口升格)
    receipts: Any = None        # S4: ReceiptStore(凭证)
    evidence: Any = None        # S5: EvidenceCollector(证据)
    audit: Any = None           # S5: AuditSystem(审计因果链)

    def policy_fingerprint(self) -> str:
        """当前策略指纹(审计/凭证引用面;纯只读)。"""
        return self.policy.fingerprint()


__all__ = ["GovernanceContext"]
