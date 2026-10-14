"""Payload orchestration: complete migrated implementation."""
from __future__ import annotations
from typing import Optional
from pydantic import Field

from pyharness.events.payload_base import _PayloadBase

class TaskEnqueuedPayload(_PayloadBase):
    """task.enqueued:入队位置 1 起(队深 <32,满则 QUE-001 拒)。"""
    task_id: str = Field(min_length=1)
    pos: int = Field(ge=1)

class TaskStartedPayload(_PayloadBase):
    """task.started:同一时刻仅一个 running(队列泵)。"""
    task_id: str = Field(min_length=1)

class TaskCompletedPayload(_PayloadBase):
    """task.completed:每任务至多一个终态事件。"""
    task_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)

class TaskFailedPayload(_PayloadBase):
    """task.failed:reason=完成/失败原因(失败可含错误码)。"""
    task_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    error: Optional[str] = None        # 结构化错误文本(脱敏)

class SegmentStartPayload(_PayloadBase):
    """segment.start:段锚先落(强同步),崩溃后段不悬空。"""
    task_id: str = Field(min_length=1)

class SegmentEndPayload(_PayloadBase):
    """segment.end:start_seq=对应 segment.start 的 seq(查询闭区间)。"""
    task_id: str = Field(min_length=1)
    start_seq: int = Field(ge=1)

class PlanStep(_PayloadBase):
    """plan 单步:{action, tool, expected, risk};风险缺省 low。"""
    action: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    risk: str = "low"

class PlanProposedPayload(_PayloadBase):
    """plan.proposed:goal + steps ≤8 步;危险步标"审批点"(risk 语义由编排层解释)。

    expires_at = 方案 24h 作废截止(ISO8601 UTC;specs/plan_mode.py.md F045 边界,
    伪码载荷字段,§7 只加可选——rebuild 后过期判定仍需该值)。
    """
    goal: str = Field(min_length=1)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    selfcheck_ok: Optional[bool] = None
    expires_at: Optional[str] = None        # 24h TTL 截止;缺省(旧日志)由 ts+24h 兜底

class PlanApprovedPayload(_PayloadBase):
    """plan.approved:plan_id = 对应 plan.proposed 的 seq。"""
    plan_id: int = Field(ge=1)
    who: Optional[str] = None          # 裁决人;24h 未确认自动 rejected(who=system)

class PlanRejectedPayload(_PayloadBase):
    """plan.rejected:拒绝后不再推进。

    reason = 拒绝原因载体(修订意见/expired/revised:step:N;specs/plan_mode.py.md
    伪码载荷,§7 只加可选——rejected 回修订对话的可审计修订意见)。
    """
    plan_id: int = Field(ge=1)
    who: Optional[str] = None
    reason: Optional[str] = None

class PlanExecStepPayload(_PayloadBase):
    """plan.exec.step:step_idx 0 起。"""
    plan_id: int = Field(ge=1)
    step_idx: int = Field(ge=0)
    action: str = Field(min_length=1)

class PlanDonePayload(_PayloadBase):
    """plan.done:方案全部步骤成功(词表外扩展,§7 规则登记;specs/plan_mode.py.md)。

    终态只认事件(S-1):done/aborted 触发者恒 system,由执行器/过期清理写入。
    """
    plan_id: int = Field(ge=1)

class PlanAbortedPayload(_PayloadBase):
    """plan.aborted:执行中止(重试耗尽/用户中止/headless 安全默认),step=中止步(0 起)。

    词表外扩展(§7 规则登记;PRD F046 验收伪代码使用的终态事件)。
    """
    plan_id: int = Field(ge=1)
    step: int = Field(ge=0)

class GoalCreatedPayload(_PayloadBase):
    """goal.created:goal_id + 可选描述;updated/completed 前必有本事件。

    task_id = F044 关联任务(目标可绑定产生它的任务,specs/goal.py.md 伪码载荷)。
    """
    goal_id: str = Field(min_length=1)
    desc: Optional[str] = None
    task_id: Optional[str] = None

class GoalUpdatedPayload(_PayloadBase):
    """goal.updated:status 四枚举(active/paused/done/abandoned)。

    progress = 0..1 进度上报(specs/goal.py.md 核对/重建契约:rebuild 需从事件
    payload 还原 progress,故事件必须携带该字段;goal.py.md 伪码载荷同款)。
    """
    goal_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(active|paused|done|abandoned)$")
    desc: Optional[str] = None
    note: Optional[str] = None
    progress: Optional[float] = Field(default=None, ge=0.0, le=1.0)

class GoalCompletedPayload(_PayloadBase):
    """goal.completed:completed 后不可再 updated(状态机侧)。"""
    goal_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(active|paused|done|abandoned)$")

class ScheduleTriggerPayload(_PayloadBase):
    """schedule.trigger:job + cron 表达式 + fired_at(ISO8601 UTC)。"""
    job: str = Field(min_length=1)
    cron: str = Field(min_length=1)
    fired_at: str = Field(min_length=1, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

class ScheduleRegisteredPayload(_PayloadBase):
    """schedule.registered:job 定义 + 首次触发窗快照(事件 = 注册表唯一真源,INV-01)。

    template 只持久化 intent(spec 伪码契约;meta 不入事件——可能含敏感编排信息,
    且 rebuild 仅需 intent 即可到点 submit,INV-09 外松内紧)。
    """
    name: str = Field(min_length=1)
    kind: str = Field(pattern=r"^(cron|interval|at)$")
    expr: str = Field(min_length=1)
    template: dict = Field(min_length=1)
    is_risky: bool
    paused: bool
    next_fire_at: Optional[str] = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

class ScheduleUpdatedPayload(_PayloadBase):
    """schedule.updated:暂停/恢复状态迁移;resume 附带重臂后的 next_fire_at。"""
    name: str = Field(min_length=1)
    paused: bool
    next_fire_at: Optional[str] = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

class ScheduleRemovedPayload(_PayloadBase):
    """schedule.removed:job 移除留痕(重建时该名不再出现)。"""
    name: str = Field(min_length=1)

class ScheduleBlockedPayload(_PayloadBase):
    """schedule.blocked:深夜窗(23:00-7:00)禁触危险类模板——blocked 留痕零入队。"""
    job: str = Field(min_length=1)
    reason: str = Field(min_length=1)

class ScheduleMissedPayload(_PayloadBase):
    """schedule.missed:宕机期错过的触发机会,只留痕不补跑(PRD F048 边界)。

    missed = 错过触发机会数(合并计数);since/until = 错过窗口 [持久化 next_fire_at,
    now],ISO8601。
    """
    job: str = Field(min_length=1)
    missed: int = Field(ge=1)
    since: str = Field(min_length=1,
                       pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
    until: str = Field(min_length=1,
                       pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

class JobStartedPayload(_PayloadBase):
    """job.started:后台 job;completed/failed 前必有 started。"""
    job_id: str = Field(min_length=1)

class JobCompletedPayload(_PayloadBase):
    """job.completed:elapsed_ms 可选耗时。"""
    job_id: str = Field(min_length=1)
    elapsed_ms: Optional[int] = Field(default=None, ge=0)

class JobFailedPayload(_PayloadBase):
    """job.failed:取消归一化为 failed(cancelled)(F025)。"""
    job_id: str = Field(min_length=1)
    elapsed_ms: Optional[int] = Field(default=None, ge=0)
    reason: Optional[str] = None       # 超预算 kill → "budget"(F032)

class SubagentSpawnedPayload(_PayloadBase):
    """subagent.spawned:parent_seq=主会话发起轮 seq(子 Agent 独立 session)。"""
    sub_id: str = Field(min_length=1)
    parent_seq: int = Field(ge=1)
    task: Optional[str] = None

class SubagentJoinedPayload(_PayloadBase):
    """subagent.joined:结果摘要;joined/failed 前必有 spawned。"""
    sub_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)

class SubagentFailedPayload(_PayloadBase):
    """subagent.failed:失败原因可含错误码。"""
    sub_id: str = Field(min_length=1)
    summary: Optional[str] = None

class WorkflowStepPayload(_PayloadBase):
    """workflow.step:断点续跑/UI 进度;wf_name 须已注册(编排层校验)。"""
    wf_name: str = Field(min_length=1)
    step_idx: int = Field(ge=0)
    action: str = Field(min_length=1)
    state: Optional[str] = None
    task_id: Optional[str] = Field(default=None, min_length=1, max_length=200)

class QueueSuspendedPayload(_PayloadBase):
    """queue.suspended:审批等待/预算暂停;先 suspended 后 resumed。"""
    reason: str = Field(min_length=1)
    by: Optional[str] = None

class QueueResumedPayload(_PayloadBase):
    """queue.resumed:裁决返回/预算恢复。"""
    reason: str = Field(min_length=1)

class ForkCreatedPayload(_PayloadBase):
    """fork.created(强同步):new_session_id 的 seq 从 1 起;base_seq 必须已落盘。"""
    new_session_id: str = Field(min_length=1)
    base_seq: int = Field(ge=1)
    reason: Optional[str] = None

# Preserve historical import and pickle identities through the compatibility facade.
TaskEnqueuedPayload.__module__ = 'pyharness.events.payload'
TaskStartedPayload.__module__ = 'pyharness.events.payload'
TaskCompletedPayload.__module__ = 'pyharness.events.payload'
TaskFailedPayload.__module__ = 'pyharness.events.payload'
SegmentStartPayload.__module__ = 'pyharness.events.payload'
SegmentEndPayload.__module__ = 'pyharness.events.payload'
PlanStep.__module__ = 'pyharness.events.payload'
PlanProposedPayload.__module__ = 'pyharness.events.payload'
PlanApprovedPayload.__module__ = 'pyharness.events.payload'
PlanRejectedPayload.__module__ = 'pyharness.events.payload'
PlanExecStepPayload.__module__ = 'pyharness.events.payload'
PlanDonePayload.__module__ = 'pyharness.events.payload'
PlanAbortedPayload.__module__ = 'pyharness.events.payload'
GoalCreatedPayload.__module__ = 'pyharness.events.payload'
GoalUpdatedPayload.__module__ = 'pyharness.events.payload'
GoalCompletedPayload.__module__ = 'pyharness.events.payload'
ScheduleTriggerPayload.__module__ = 'pyharness.events.payload'
ScheduleRegisteredPayload.__module__ = 'pyharness.events.payload'
ScheduleUpdatedPayload.__module__ = 'pyharness.events.payload'
ScheduleRemovedPayload.__module__ = 'pyharness.events.payload'
ScheduleBlockedPayload.__module__ = 'pyharness.events.payload'
ScheduleMissedPayload.__module__ = 'pyharness.events.payload'
JobStartedPayload.__module__ = 'pyharness.events.payload'
JobCompletedPayload.__module__ = 'pyharness.events.payload'
JobFailedPayload.__module__ = 'pyharness.events.payload'
SubagentSpawnedPayload.__module__ = 'pyharness.events.payload'
SubagentJoinedPayload.__module__ = 'pyharness.events.payload'
SubagentFailedPayload.__module__ = 'pyharness.events.payload'
WorkflowStepPayload.__module__ = 'pyharness.events.payload'
QueueSuspendedPayload.__module__ = 'pyharness.events.payload'
QueueResumedPayload.__module__ = 'pyharness.events.payload'
ForkCreatedPayload.__module__ = 'pyharness.events.payload'
