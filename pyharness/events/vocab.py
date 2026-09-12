"""pyharness/events/vocab.py — 事件词表注册 (specs/events.py.md)

_EVENT_REGISTRY 类型 → payload 模型注册表;register_event_type/payload_model_for/
validate_payload 三函数 = 校验链第 2、3 步。70 个核心类型在模块导入期一次性注册
(EVENT-SCHEMA §3 的 57 基线 + 词表外扩展:llm.retry/plan.done/plan.aborted/
schedule.registered/updated/removed/blocked/missed,按 §7 规则登记);
插件运行时以 plugin.<id>.<name> 命名空间注册,只增不改。

抛错一律 raise_code(EVT-1xx),禁裸 raise str。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ValidationError

from pyharness.errors import raise_code
from pyharness.events.payload import (  # noqa: F401 — 模型类仅供注册引用
    AgentMessagePayload,
    ApprovalOutcomePayload,
    ApprovalRequestedPayload,
    BudgetPausedPayload,
    BusBackpressurePayload,
    ConfigUpdatedPayload,
    ContextCompactedPayload,
    ForkCreatedPayload,
    GoalCompletedPayload,
    GoalCreatedPayload,
    GoalUpdatedPayload,
    GuardEvaluatedPayload,
    GuardRejectedPayload,
    JobCompletedPayload,
    JobFailedPayload,
    JobStartedPayload,
    LlmChunkPayload,
    LlmErrorPayload,
    LlmRequestPayload,
    LlmResponsePayload,
    LlmRetryPayload,
    LlmUsagePayload,
    PlanApprovedPayload,
    PlanAbortedPayload,
    PlanDonePayload,
    PlanExecStepPayload,
    PlanProposedPayload,
    PlanRejectedPayload,
    PluginInstalledPayload,
    PluginUninstalledPayload,
    QueueResumedPayload,
    QueueSuspendedPayload,
    ScheduleBlockedPayload,
    ScheduleMissedPayload,
    ScheduleRegisteredPayload,
    ScheduleRemovedPayload,
    ScheduleTriggerPayload,
    ScheduleUpdatedPayload,
    ScopeUpdatedPayload,
    SegmentEndPayload,
    SegmentStartPayload,
    SessionCreatedPayload,
    SessionFinishedPayload,
    SessionRecoveredPayload,
    SessionRenamedPayload,
    SkillInstalledPayload,
    SkillRemovedPayload,
    SkillRolledBackPayload,
    SkillUsedPayload,
    SubagentFailedPayload,
    SubagentJoinedPayload,
    SubagentSpawnedPayload,
    SyscheckFailPayload,
    SystemCancelledPayload,
    SystemErrorPayload,
    TaskCompletedPayload,
    TaskEnqueuedPayload,
    TaskFailedPayload,
    TaskStartedPayload,
    TodoUpdatedPayload,
    ToolCallPayload,
    ToolErrorPayload,
    ToolResultPayload,
    UserAnswerPayload,
    UserAttachmentImagePayload,
    UserCommandPayload,
    UserFeedbackPayload,
    UserMessageEditedPayload,
    UserMessagePayload,
    UserQuestionPayload,
    WorkflowStepPayload,
)

# actor 六枚举(Envelope.actor Literal 唯一取值源;单一真源在 vocab.py)
ACTORS = ("user", "agent", "llm", "tool", "system", "plugin")

# 强同步事件族:落盘成功才返回(EVENT-SCHEMA §1.2/§8.1 强同步矩阵)
SYNC_TYPES = frozenset({
    "user.message", "guard.rejected",
    "approval.requested", "approval.granted", "approval.denied", "approval.timeout",
    "session.finished", "session.recovered", "segment.start",
    "fork.created", "context.compacted",
})

# 瞬时事件(仅总线,禁 append 入日志;registry.updated 为总线内部广播,无 payload 模型)
TRANSIENT_TYPES = frozenset({"llm.chunk", "registry.updated", "config.updated"})

_EVENT_REGISTRY: dict[str, type[BaseModel]] = {}
_TRANSIENT: set[str] = set(TRANSIENT_TYPES)


def register_event_type(type_: str, model: type[BaseModel], *,
                        transient: bool = False) -> None:
    """词表注册(框架 70 类型启动期注册;插件运行时注册 plugin.<id>.<name>)。

    重复注册拒绝(EVT-102,§3.8 只增不改):破坏性变更 = 新类型名,旧类型冻结。
    """
    if type_ in _EVENT_REGISTRY:
        raise_code("EVT-102", type_=type_, detail="重复注册,事件类型只增不改")
    _EVENT_REGISTRY[type_] = model
    if transient:
        _TRANSIENT.add(type_)


def payload_model_for(type_: str) -> Optional[type[BaseModel]]:
    """查类型 → payload 模型;未注册返回 None(由调用方按 EVT-102 拒)。"""
    return _EVENT_REGISTRY.get(type_)


def is_registered(type_: str) -> bool:
    """类型是否已注册(校验链第 2 步前置判断)。"""
    return type_ in _EVENT_REGISTRY


def is_transient(type_: str) -> bool:
    """是否瞬时事件(仅总线;session.append 须拒 transient 入日志)。"""
    return type_ in _TRANSIENT


def registered_event_types() -> tuple[str, ...]:
    """当前注册的全部事件名(插入序);自检/测试用。"""
    return tuple(_EVENT_REGISTRY)


# EVENT_TYPES 公开别名:词表全量(类型 → payload 模型),自检/审计/上层读取用
EVENT_TYPES = _EVENT_REGISTRY


def summarize_validation(e: ValidationError) -> str:
    """ValidationError → 字段明细文本(如 "content: String should have at least 1 character")。

    只取 loc+msg,绝不携带输入值(input 可能含敏感原文)。
    """
    parts: list[str] = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = str(err.get("msg", ""))
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)[:2000]


def validate_payload(type_: str, payload: dict) -> dict:
    """payload 强类型二次校验(EVENT-SCHEMA §2.2 第 3 步)。

    成功返回规范化 dict(model_dump);失败 → EVT-100 带字段明细。
    """
    model = payload_model_for(type_)
    if model is None:
        raise_code("EVT-102", type_=type_)
    try:
        return model(**payload).model_dump()
    except ValidationError as e:
        # 未注册已在上面抛 EVT-102;此处只可能是字段非法 → EVT-100
        raise_code("EVT-100", type_=type_, detail=summarize_validation(e))


# ------------------------------------------------------------------ 核心词表
# 70 事件类型全量入册(EVENT-SCHEMA §3 的 57 A-E 分组权威 + 词表外扩展 13 项
# 按 §7 登记:llm.retry F028 / plan.done·plan.aborted F046 / schedule.registered·
# updated·removed·blocked·missed F048;PARAMETER-ANCHOR 基线 57,扩展只增不改)。
# 元组元素:(type, payload_model, transient)
_CORE_EVENT_TYPES: tuple[tuple[str, type[BaseModel], bool], ...] = (
    # ---- A 会话生命周期(§3.1)
    ("session.created", SessionCreatedPayload, False),
    ("session.renamed", SessionRenamedPayload, False),
    ("session.finished", SessionFinishedPayload, False),
    ("session.recovered", SessionRecoveredPayload, False),
    # ---- B 用户输入侧(§3.2)
    ("user.message", UserMessagePayload, False),
    ("user.message_edited", UserMessageEditedPayload, False),
    ("user.feedback", UserFeedbackPayload, False),
    ("user.attachment.image", UserAttachmentImagePayload, False),
    ("user.command", UserCommandPayload, False),
    # ---- C 模型侧(§3.3)
    ("llm.request", LlmRequestPayload, False),
    ("llm.response", LlmResponsePayload, False),
    ("llm.usage", LlmUsagePayload, False),
    ("llm.error", LlmErrorPayload, False),
    ("agent.message", AgentMessagePayload, False),
    ("llm.chunk", LlmChunkPayload, True),          # 瞬时,仅总线
    ("llm.retry", LlmRetryPayload, False),         # F028 落盘留痕(词表外扩展)
    # ---- D 工具侧:guard 与审批链(§3.4)
    ("tool.call", ToolCallPayload, False),
    ("guard.evaluated", GuardEvaluatedPayload, False),
    ("guard.rejected", GuardRejectedPayload, False),   # 强同步三类之一
    ("approval.requested", ApprovalRequestedPayload, False),  # 强同步三类之一
    ("approval.granted", ApprovalOutcomePayload, False),      # 强同步
    ("approval.denied", ApprovalOutcomePayload, False),       # 强同步
    ("approval.timeout", ApprovalOutcomePayload, False),      # 强同步
    ("tool.result", ToolResultPayload, False),
    ("tool.error", ToolErrorPayload, False),
    # ---- E 编排与任务段(§3.5.1-3.5.3)
    ("task.enqueued", TaskEnqueuedPayload, False),
    ("task.started", TaskStartedPayload, False),
    ("task.completed", TaskCompletedPayload, False),
    ("task.failed", TaskFailedPayload, False),
    ("segment.start", SegmentStartPayload, False),   # 强同步(段锚先落)
    ("segment.end", SegmentEndPayload, False),
    ("plan.proposed", PlanProposedPayload, False),
    ("plan.approved", PlanApprovedPayload, False),
    ("plan.rejected", PlanRejectedPayload, False),
    ("plan.exec.step", PlanExecStepPayload, False),
    # 词表外扩展(§7 规则登记;specs/plan_mode.py.md F046 终态事件,PRD 伪代码用词)
    ("plan.done", PlanDonePayload, False),
    ("plan.aborted", PlanAbortedPayload, False),
    ("goal.created", GoalCreatedPayload, False),
    ("goal.updated", GoalUpdatedPayload, False),
    ("goal.completed", GoalCompletedPayload, False),
    ("schedule.trigger", ScheduleTriggerPayload, False),
    # schedule.* 词表外扩展(specs/schedule.py.md F048,EVENT-SCHEMA §7 登记,
    # llm.retry 同款先例;§3.5.3 字段表待文档管线同步——见规格"词表外新增")
    ("schedule.registered", ScheduleRegisteredPayload, False),
    ("schedule.updated", ScheduleUpdatedPayload, False),
    ("schedule.removed", ScheduleRemovedPayload, False),
    ("schedule.blocked", ScheduleBlockedPayload, False),
    ("schedule.missed", ScheduleMissedPayload, False),
    ("job.started", JobStartedPayload, False),
    ("job.completed", JobCompletedPayload, False),
    ("job.failed", JobFailedPayload, False),
    ("subagent.spawned", SubagentSpawnedPayload, False),
    ("subagent.joined", SubagentJoinedPayload, False),
    ("subagent.failed", SubagentFailedPayload, False),
    ("workflow.step", WorkflowStepPayload, False),
    ("queue.suspended", QueueSuspendedPayload, False),
    ("queue.resumed", QueueResumedPayload, False),
    # ---- E 系统侧(§3.5.4-3.5.6)
    ("fork.created", ForkCreatedPayload, False),         # 强同步(分叉锚必须持久)
    ("context.compacted", ContextCompactedPayload, False),  # 强同步(空洞合法化声明)
    ("plugin.installed", PluginInstalledPayload, False),
    ("plugin.uninstalled", PluginUninstalledPayload, False),
    ("bus.backpressure", BusBackpressurePayload, False),
    ("system.cancelled", SystemCancelledPayload, False),
    ("system.error", SystemErrorPayload, False),
    ("todo.updated", TodoUpdatedPayload, False),
    ("syscheck.fail", SyscheckFailPayload, False),
    # F032/F014 策略与预算事件(F032 预算闸/BudgetGuard 与 scope 单调收紧同源落盘)
    ("scope.updated", ScopeUpdatedPayload, False),
    ("budget.paused", BudgetPausedPayload, False),
    # #38 agent 反问(F063 词表外新增:user.question/answer 对,ask_id=question seq)
    ("user.question", UserQuestionPayload, False),
    ("user.answer", UserAnswerPayload, False),
    # F073 技能系统装载审计(skill.load 工具调用留痕;目录注入另走 sysprompt 段)
    ("skill.used", SkillUsedPayload, False),
    ("skill.installed", SkillInstalledPayload, False),
    ("skill.removed", SkillRemovedPayload, False),
    ("skill.rollback", SkillRolledBackPayload, False),
    # 配置热更审计(瞬时,仅总线:无会话维度,不进 JSONL)
    ("config.updated", ConfigUpdatedPayload, True),
)


def _install_core_vocabulary() -> None:
    """框架 70 类型启动期注册(模块导入即完成;只增不改)。"""
    for type_, model, transient in _CORE_EVENT_TYPES:
        register_event_type(type_, model, transient=transient)


_install_core_vocabulary()

__all__ = [
    "ACTORS", "SYNC_TYPES", "TRANSIENT_TYPES",
    "EVENT_TYPES", "register_event_type", "payload_model_for", "is_registered",
    "is_transient", "registered_event_types", "validate_payload",
    "summarize_validation",
]
