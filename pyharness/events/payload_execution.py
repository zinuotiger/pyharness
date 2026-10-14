"""Payload execution: complete migrated implementation."""
from __future__ import annotations
from typing import Optional, Literal
from pydantic import Field, model_validator

from pyharness.events.payload_base import _PayloadBase

class LlmRequestPayload(_PayloadBase):
    """llm.request:model=实际请求模型(降级链命中者);degraded_from 非空=降级事实。"""
    model: str = Field(min_length=1)
    degraded_from: Optional[str] = None
    prompt_tokens: Optional[int] = Field(default=None, ge=0)
    n_tools: Optional[int] = Field(default=None, ge=0)
    role: Optional[str] = None
    attempt: Optional[int] = Field(default=None, ge=1)

class LlmResponsePayload(_PayloadBase):
    """llm.response:content 与 tool_calls 至少其一(空 content=工具调用轮)。"""
    model: str = Field(min_length=1)
    role: Optional[str] = None
    request_seq: Optional[int] = Field(default=None, ge=1)
    finish_reason: str = Field(min_length=1)   # stop/tool_calls/length/content_filter/…
    content: str = ""
    tool_calls: Optional[list] = None          # 原生 tool_calls(id/name/arguments 原文)

    @model_validator(mode="after")
    def _content_or_tool_calls(self) -> "LlmResponsePayload":
        """校验:纯文本与工具调用至少其一,双空视为坏载荷。"""
        if not self.content and not self.tool_calls:
            raise ValueError("content 与 tool_calls 至少其一")
        return self

class ExecutorEventPayload(_PayloadBase):
    """Public execution facts only; raw reasoning is never accepted by adapters."""
    executor: Literal['codex']
    action: str = Field(min_length=1, max_length=40)
    record: dict

class PlatformResourcePayload(_PayloadBase):
    """Platform resource lifecycle fact, stored in its owning SessionLog."""
    action: str = Field(min_length=1, max_length=40)
    record: dict
    command_summary: str | None = None
    exit_code: int | None = None

class LlmUsagePayload(_PayloadBase):
    """llm.usage:计量事件;in/out_tokens ≥0(预算硬闸只用 token)。"""
    model: str = Field(min_length=1)
    in_tokens: int = Field(ge=0)
    out_tokens: int = Field(ge=0)
    cache_hit: Optional[int] = Field(default=0, ge=0)
    cost_est: Optional[float] = Field(default=None, ge=0)
    estimated: bool = False
    input_unknown: bool = False

class LlmErrorPayload(_PayloadBase):
    """llm.error:code 必为 LLM-3xx 注册码或 timeout;retryable 供 F028 依据。"""
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    diagnostics: Optional[dict] = None
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
    details: Optional[dict] = None

class ApprovalReviewedPayload(_PayloadBase):
    approval_id: int
    call_id: str
    reviewer: str
    decision: Literal['approve', 'deny', 'manual']
    reason: str
    invocation_sha256: str
    context_sha256: str
    scope: dict

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

# Preserve historical import and pickle identities through the compatibility facade.
LlmRequestPayload.__module__ = 'pyharness.events.payload'
LlmResponsePayload.__module__ = 'pyharness.events.payload'
ExecutorEventPayload.__module__ = 'pyharness.events.payload'
PlatformResourcePayload.__module__ = 'pyharness.events.payload'
LlmUsagePayload.__module__ = 'pyharness.events.payload'
LlmErrorPayload.__module__ = 'pyharness.events.payload'
AgentMessagePayload.__module__ = 'pyharness.events.payload'
LlmChunkPayload.__module__ = 'pyharness.events.payload'
LlmRetryPayload.__module__ = 'pyharness.events.payload'
ToolCallPayload.__module__ = 'pyharness.events.payload'
GuardEvaluatedPayload.__module__ = 'pyharness.events.payload'
GuardRejectedPayload.__module__ = 'pyharness.events.payload'
ApprovalRequestedPayload.__module__ = 'pyharness.events.payload'
ApprovalReviewedPayload.__module__ = 'pyharness.events.payload'
ApprovalOutcomePayload.__module__ = 'pyharness.events.payload'
ToolResultPayload.__module__ = 'pyharness.events.payload'
ToolErrorPayload.__module__ = 'pyharness.events.payload'
