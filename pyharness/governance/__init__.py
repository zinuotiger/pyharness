"""pyharness/governance/__init__.py — 治理层包门面(S2-1 骨架)。

治理层(ADR-013)是**授权层**,不是执行层:
    - 只回答"允许不允许、依据什么、谁做的决定、有无凭证";
    - **无任何执行/放行 API**(INV-G5;执行权归 ``core/tools_executor`` 四关管道);
    - 唯一强制点不变(G-4);治理层只描述策略、算指纹、发治理事件。

**依赖方向**(ADR-018:308,强制,有测试断言——见
``tests/unit/test_governance_policy.py::test_governance_import_direction``):
    本包**只允许** import ``pyharness.events`` 与 ``pyharness.errors``(+ 标准库
    与本包内模块);**禁止** import ``pyharness.core.*``(tools_guard/executor/
    scope/llm)与 ``pyharness.bus.plugin``。规则与执行对象一律**注入**(Δ-4)。

**本步(S2-1)只含 policy/context 两个模块**;``decision.py`` / ``receipt.py`` /
``evidence.py`` / ``audit.py`` 属 S3~S5(ADR-018 冻结目录),**本步不创建**。
"""
from __future__ import annotations

from pyharness.governance.context import GovernanceContext
from pyharness.governance.policy import (POLICY_OPS, EVENT_POLICY_UPDATED,
                                         Policy, PolicyEngine, PolicyRegistry,
                                         PolicyRule, compute_fingerprint)

__all__ = [
    "GovernanceContext",
    "Policy", "PolicyEngine", "PolicyRegistry", "PolicyRule",
    "compute_fingerprint", "POLICY_OPS", "EVENT_POLICY_UPDATED",
]
