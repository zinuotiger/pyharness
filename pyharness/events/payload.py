"""pyharness/events/payload.py — 各事件负载模型 (specs/events.py.md 词表清单)

按 EVENT-SCHEMA §3 词汇总表为每个事件实现一个 pydantic 负载模型
(extra="forbid",拒多余字段——F026 同纪律);77 事件词表(77 payload 模型) + llm.retry
落盘注册见 vocab.py 的 _CORE_EVENT_TYPES。

约定:✓=必填字段不带默认值;—=可选字段 Optional/显式默认;载荷内
禁字面换行(写盘 JSON 转义由上层负责);payload 模型不持有任何会话状态。
"""
from __future__ import annotations

from typing import Any, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 证据引用种类(与 ``governance.evidence.REF_KINDS`` 同源;events 不得反向 import
# governance,故本地声明 + **一致性测试**守卫——见
# tests/unit/test_governance_evidence.py::test_ref_kinds_match_evidence_module)

# 所有负载模型的公共基类:拒绝多余字段(严格模式纪律)


# =====================================================================
# A — 会话生命周期(EVENT-SCHEMA §3.1)
# =====================================================================


# =====================================================================
# B — 用户输入侧(EVENT-SCHEMA §3.2)
# =====================================================================


# =====================================================================
# C — 模型侧(EVENT-SCHEMA §3.3)
# =====================================================================


# =====================================================================
# D — 工具侧:guard 与审批链(EVENT-SCHEMA §3.4)
# =====================================================================


# =====================================================================
# E — 编排与系统侧(EVENT-SCHEMA §3.5)
# =====================================================================


# =====================================================================
# schedule.* 词表外扩展(specs/schedule.py.md F048;EVENT-SCHEMA §7 规则登记,
# llm.retry 同款先例)——schedule.trigger 之外 5 个调度生命周期事件:
# 定义注册/暂停恢复/移除/深夜禁触发/宕机错过,全部普通落盘(§8.1)。
# =====================================================================
    # None 仅表示 at 一次性已耗尽。cron 不产生 None:无未来触发点(如 2/30)
    # 的表达式在注册期即被 CFG-601 拒绝,不落"可注册但永不触发"的静默态。


from pyharness.events.payload_base import (_EVIDENCE_REF_KINDS, _PayloadBase)
from pyharness.events.payload_session import (SessionCreatedPayload, SessionRenamedPayload, SessionFinishedPayload, SessionRecoveredPayload, UserMessagePayload, UserMessageEditedPayload, UserFeedbackPayload, UserAttachmentImagePayload, UserCommandPayload)
from pyharness.events.payload_execution import (LlmRequestPayload, LlmResponsePayload, ExecutorEventPayload, PlatformResourcePayload, LlmUsagePayload, LlmErrorPayload, AgentMessagePayload, LlmChunkPayload, LlmRetryPayload, ToolCallPayload, GuardEvaluatedPayload, GuardRejectedPayload, ApprovalRequestedPayload, ApprovalReviewedPayload, ApprovalOutcomePayload, ToolResultPayload, ToolErrorPayload)
from pyharness.events.payload_orchestration import (TaskEnqueuedPayload, TaskStartedPayload, TaskCompletedPayload, TaskFailedPayload, SegmentStartPayload, SegmentEndPayload, PlanStep, PlanProposedPayload, PlanApprovedPayload, PlanRejectedPayload, PlanExecStepPayload, PlanDonePayload, PlanAbortedPayload, GoalCreatedPayload, GoalUpdatedPayload, GoalCompletedPayload, ScheduleTriggerPayload, ScheduleRegisteredPayload, ScheduleUpdatedPayload, ScheduleRemovedPayload, ScheduleBlockedPayload, ScheduleMissedPayload, JobStartedPayload, JobCompletedPayload, JobFailedPayload, SubagentSpawnedPayload, SubagentJoinedPayload, SubagentFailedPayload, WorkflowStepPayload, QueueSuspendedPayload, QueueResumedPayload, ForkCreatedPayload)
from pyharness.events.payload_governance import (ContextCompactedPayload, PluginInstalledPayload, PluginUninstalledPayload, BusBackpressurePayload, SystemCancelledPayload, SystemErrorPayload, TodoItem, TodoUpdatedPayload, SyscheckFailPayload, BudgetPausedPayload, UserQuestionPayload, UserAnswerPayload, ScopeUpdatedPayload, PolicyUpdatedPayload, DecisionIssuedPayload, ReceiptEmittedPayload, EvidenceArchivedPayload, SkillInstalledPayload, SkillRemovedPayload, SkillRolledBackPayload, SkillUsedPayload, ConfigUpdatedPayload)

__all__ = [name for name in globals()
           if name.endswith("Payload") or name in ("PlanStep", "TodoItem")]
