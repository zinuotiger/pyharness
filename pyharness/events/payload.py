"""pyharness/events/payload.py — 各事件负载模型 (specs/events.py.md 词表清单)

按 EVENT-SCHEMA §3 词汇总表为每个事件实现一个 pydantic 负载模型
(extra="forbid",拒多余字段——F026 同纪律);57 事件词表 + llm.retry
落盘注册见 vocab.py 的 _CORE_EVENT_TYPES。

约定:✓=必填字段不带默认值;—=可选字段 Optional/显式默认;载荷内
禁字面换行(写盘 JSON 转义由上层负责);payload 模型不持有任何会话状态。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 所有负载模型的公共基类:拒绝多余字段(严格模式纪律)
class _PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


# =====================================================================
# A — 会话生命周期(EVENT-SCHEMA §3.1)
# =====================================================================
class SessionCreatedPayload(_PayloadBase):
    """session.created:首事件恒 seq=1;title 可为空串(F042 随后自动命名)。"""
    title: str
    model: str = Field(min_length=1)


class SessionRenamedPayload(_PayloadBase):
    """session.renamed:新标题 ≤64 字且非空;by=auto(user 显式)/auto(自动命名)。"""
    new_title: str = Field(min_length=1, max_length=64)
    by: Optional[str] = None          # "auto" | "user"(来源侧语义,不收紧)


class SessionFinishedPayload(_PayloadBase):
    """session.finished:终态只写一次(EVT-104 由状态机侧把关),reason 五枚举。"""
    reason: str                       # idle/timeout/budget/error/cancelled 由会话层语义约束


class SessionRecoveredPayload(_PayloadBase):
    """session.recovered:fixed 非空(修复动作清单),lost=被截断丢弃 seq 区间。"""
    fixed: list[str] = Field(min_length=1)
    backup: Optional[str] = None
    lost: Optional[list[int]] = None


# =====================================================================
# B — 用户输入侧(EVENT-SCHEMA §3.2)
# =====================================================================
class UserMessagePayload(_PayloadBase):
    """user.message:用户原始输入原样保存,content 非空(强同步)。"""
    content: str = Field(min_length=1)
    meta: Optional[dict] = None       # cli/web/acp 等来源通道信息


class UserMessageEditedPayload(_PayloadBase):
    """user.message_edited:只追加修正,target_seq 指向被改 user.message。"""
    target_seq: int = Field(ge=1)
    new_content: str = Field(min_length=1)


class UserFeedbackPayload(_PayloadBase):
    """user.feedback:对 agent.message 的评价,kind 三枚举,note ≤500 字。"""
    target_seq: int = Field(ge=1)
    kind: str = Field(pattern=r"^(up|down|flag)$")
    note: Optional[str] = Field(default=None, max_length=500)


class UserAttachmentImagePayload(_PayloadBase):
    """user.attachment.image:内容寻址附件;sha256=64 hex,mime 白名单四值。"""
    file_path: str = Field(min_length=1)
    mime: str = Field(pattern=r"^image/(jpeg|png|webp|gif)$")
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    w: int = Field(ge=1)
    h: int = Field(ge=1)
    size_bytes: Optional[int] = Field(default=None, ge=0)


class UserCommandPayload(_PayloadBase):
    """user.command:斜杠命令命中即写本事件(不产生 user.message),args 可空串。"""
    name: str = Field(min_length=1)
    args: Optional[str] = ""


# =====================================================================
# C — 模型侧(EVENT-SCHEMA §3.3)
# =====================================================================
class LlmRequestPayload(_PayloadBase):
    """llm.request:model=实际请求模型(降级链命中者);degraded_from 非空=降级事实。"""
    model: str = Field(min_length=1)
    degraded_from: Optional[str] = None
    prompt_tokens: Optional[int] = Field(default=None, ge=0)
    n_tools: Optional[int] = Field(default=None, ge=0)


class LlmResponsePayload(_PayloadBase):
    """llm.response:content 与 tool_calls 至少其一(空 content=工具调用轮)。"""
    model: str = Field(min_length=1)
    finish_reason: str = Field(min_length=1)   # stop/tool_calls/length/content_filter/…
    content: str = ""
    tool_calls: Optional[list] = None          # 原生 tool_calls(id/name/arguments 原文)

    @model_validator(mode="after")
    def _content_or_tool_calls(self) -> "LlmResponsePayload":
        """校验:纯文本与工具调用至少其一,双空视为坏载荷。"""
        if not self.content and not self.tool_calls:
            raise ValueError("content 与 tool_calls 至少其一")
        return self


class LlmUsagePayload(_PayloadBase):
    """llm.usage:计量事件;in/out_tokens ≥0(预算硬闸只用 token)。"""
    model: str = Field(min_length=1)
    in_tokens: int = Field(ge=0)
    out_tokens: int = Field(ge=0)
    cache_hit: Optional[int] = Field(default=0, ge=0)
    cost_est: Optional[float] = Field(default=None, ge=0)


class LlmErrorPayload(_PayloadBase):
    """llm.error:code 必为 LLM-3xx 注册码或 timeout;retryable 供 F028 依据。"""
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    attempt: Optional[int] = Field(default=None, ge=0)


class AgentMessagePayload(_PayloadBase):
    """agent.message:最终展示文本,content 非空(空响应不写)。"""
    content: str = Field(min_length=1)
    model: Optional[str] = None


class LlmChunkPayload(_PayloadBase):
    """llm.chunk:瞬时事件(仅总线,禁 append 入日志);delta 流式增量碎片。"""
    delta: str = Field(min_length=1)


class LlmRetryPayload(_PayloadBase):
    """llm.retry:F028 重试留痕(词表外扩展,§7 规则登记);attempt 0 起。"""
    attempt: int = Field(ge=0)
    delay_ms: int = Field(ge=0)
    model: Optional[str] = None


# =====================================================================
# D — 工具侧:guard 与审批链(EVENT-SCHEMA §3.4)
# =====================================================================
class ToolCallPayload(_PayloadBase):
    """tool.call:args=强校验后参数,raw_args=LLM 原始参数原文(INV-06 一致性审计)。"""
    name: str = Field(min_length=1)
    args: dict
    raw_args: dict
    call_id: str = Field(min_length=1)


class GuardEvaluatedPayload(_PayloadBase):
    """guard.evaluated:每调用必经;guard_ids 空列表=无命中。"""
    tool: str = Field(min_length=1)
    decision: str = Field(pattern=r"^(allow|deny|need_approval)$")
    guard_ids: list[str] = Field(default_factory=list)
    reasons: Optional[list[str]] = None


class GuardRejectedPayload(_PayloadBase):
    """guard.rejected:单调拒绝审计点(强同步);拒绝后零副作用。"""
    tool: str = Field(min_length=1)
    guard_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    policy_ref: Optional[str] = None


class ApprovalRequestedPayload(_PayloadBase):
    """approval.requested:ttl_ms 默认 120000(120s 安全默认);risk ∈ high/critical。"""
    tool: str = Field(min_length=1)
    args_summary: str = Field(min_length=1)
    ttl_ms: int = Field(default=120_000, gt=0)
    risk: Optional[str] = Field(default=None, pattern=r"^(high|critical)$")


class ApprovalOutcomePayload(_PayloadBase):
    """approval.granted/denied/timeout 三结果同构:approval_id=requested 的 seq。"""
    approval_id: int = Field(ge=1)
    by: str = Field(min_length=1)      # granted/denied=裁决人;timeout 恒 "system"
    ttl_ms: Optional[int] = Field(default=None, ge=0)


class ToolResultPayload(_PayloadBase):
    """tool.result:与 tool.call 以 call_id 配对;summary ≤2KB(大输出只放引用)。"""
    name: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    ok: bool
    summary: str = Field(min_length=1, max_length=2048)
    truncated: bool
    spill_ref: Optional[dict] = None   # {ref, chars, lines, preview(头500字符)}


class ToolErrorPayload(_PayloadBase):
    """tool.error:code=结构化码或 timeout(工具默认 60s);消息脱敏回喂。"""
    name: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


# =====================================================================
# E — 编排与系统侧(EVENT-SCHEMA §3.5)
# =====================================================================
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
    """plan.proposed:goal + steps ≤8 步;危险步标"审批点"(risk 语义由编排层解释)。"""
    goal: str = Field(min_length=1)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    selfcheck_ok: Optional[bool] = None


class PlanApprovedPayload(_PayloadBase):
    """plan.approved:plan_id = 对应 plan.proposed 的 seq。"""
    plan_id: int = Field(ge=1)
    who: Optional[str] = None          # 裁决人;24h 未确认自动 rejected(who=system)


class PlanRejectedPayload(_PayloadBase):
    """plan.rejected:拒绝后不再推进。"""
    plan_id: int = Field(ge=1)
    who: Optional[str] = None


class PlanExecStepPayload(_PayloadBase):
    """plan.exec.step:step_idx 0 起。"""
    plan_id: int = Field(ge=1)
    step_idx: int = Field(ge=0)
    action: str = Field(min_length=1)


class GoalCreatedPayload(_PayloadBase):
    """goal.created:goal_id + 可选描述;updated/completed 前必有本事件。"""
    goal_id: str = Field(min_length=1)
    desc: Optional[str] = None


class GoalUpdatedPayload(_PayloadBase):
    """goal.updated:status 四枚举(active/paused/done/abandoned)。"""
    goal_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(active|paused|done|abandoned)$")
    desc: Optional[str] = None
    note: Optional[str] = None


class GoalCompletedPayload(_PayloadBase):
    """goal.completed:completed 后不可再 updated(状态机侧)。"""
    goal_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(active|paused|done|abandoned)$")


class ScheduleTriggerPayload(_PayloadBase):
    """schedule.trigger:job + cron 表达式 + fired_at(ISO8601 UTC)。"""
    job: str = Field(min_length=1)
    cron: str = Field(min_length=1)
    fired_at: str = Field(min_length=1, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


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


class ContextCompactedPayload(_PayloadBase):
    """context.compacted(强同步):ranges=折叠闭区间列表,区间合法且互不重叠。"""
    ranges: list[list[int]] = Field(min_length=1)
    summary: str = Field(min_length=1)
    tokens_before: Optional[int] = Field(default=None, ge=0)
    tokens_after: Optional[int] = Field(default=None, ge=0)

    @field_validator("ranges")
    @classmethod
    def _ranges_sane(cls, v: list[list[int]]) -> list[list[int]]:
        """区间必须 [lo, hi](lo≤hi、lo≥1)且两两不重叠。"""
        ordered: list[list[int]] = []
        for pair in v:
            if len(pair) != 2:
                raise ValueError(f"区间须为 [lo, hi] 二元组:{pair}")
            lo, hi = pair
            if lo < 1:
                raise ValueError(f"seq 从 1 起,lo 非法:{pair}")
            if lo > hi:
                raise ValueError(f"lo 不得大于 hi:{pair}")
            ordered.append(pair)
        ordered.sort(key=lambda p: p[0])
        for (_, prev_hi), (cur_lo, _) in zip(ordered, ordered[1:]):
            if cur_lo <= prev_hi:
                raise ValueError("ranges 区间不得重叠")
        return v


class PluginInstalledPayload(_PayloadBase):
    """plugin.installed:api_version 不匹配拒装载(BUS-002 保留名校验在注册层)。"""
    plugin_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    api_version: str = Field(min_length=1)


class PluginUninstalledPayload(_PayloadBase):
    """plugin.uninstalled:非 running 态才可卸载(BusyUninstall)。"""
    plugin_id: str = Field(min_length=1)


class BusBackpressurePayload(_PayloadBase):
    """bus.backpressure:在途 >1000 才发;禁静默丢事件。"""
    sender: str = Field(min_length=1)
    dropped: int = Field(ge=0)
    sample: Optional[list] = Field(default=None, max_length=100)   # ≤100 死信样本


class SystemCancelledPayload(_PayloadBase):
    """system.cancelled:取消审计(防"以为停了还在跑")。"""
    what: str = Field(min_length=1)
    reason: Optional[str] = None


class SystemErrorPayload(_PayloadBase):
    """system.error:code 必注册(F019);hint 人读指引;ctx 结构化现场。"""
    code: str = Field(min_length=1)
    hint: str = Field(min_length=1)
    ctx: Optional[dict] = None


class TodoItem(_PayloadBase):
    """todo 单项:{id, text, done};done 重复幂等由状态层保证。"""
    id: int
    text: str = Field(min_length=1)
    done: bool


class TodoUpdatedPayload(_PayloadBase):
    """todo.updated:每任务 ≤20 项;compaction 清 done 项。"""
    task_id: str = Field(min_length=1)
    todos: list[TodoItem] = Field(default_factory=list, max_length=20)


class SyscheckFailPayload(_PayloadBase):
    """syscheck.fail:自检发现(SEQ-GAP/NO-GUARD-EVENT 等),失败不杀进程提示 repair。"""
    findings: list = Field(min_length=1)
    trigger: Optional[str] = None


__all__ = [name for name in globals()
           if name.endswith("Payload") or name in ("PlanStep", "TodoItem")]
