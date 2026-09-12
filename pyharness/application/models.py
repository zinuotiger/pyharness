"""Stable view models shared by Web and native desktop shells.

The application service remains the single business entry point.  These models
describe the JSON-safe payloads exchanged across its UI adapters.  They are
intentionally permissive about extra fields so event projections can evolve
without coupling either shell to implementation details.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ViewModel(BaseModel):
    """Base DTO: unknown projection fields are preserved."""

    model_config = ConfigDict(extra="allow")


class SessionView(ViewModel):
    sid: str
    title: str = ""
    preview: str = ""


class MessageView(ViewModel):
    role: str
    content: str = ""
    seq: int | None = None


class JobView(ViewModel):
    job_id: str
    state: str = "unknown"
    owner: str = ""
    todo_summary: str = ""
    elapsed_ms: int = 0
    progress: float = 0.0
    error: str | None = None


class ScheduleView(ViewModel):
    name: str
    kind: str = "cron"
    expr: str = ""
    paused: bool = False
    next_fire_at: str | None = None
    triggered: int = 0
    missed: int = 0


class SubagentView(ViewModel):
    sub_id: str
    state: str = "unknown"
    age_s: float = 0.0


class SkillView(ViewModel):
    name: str
    description: str = ""
    dir: str = ""


class RegistrySkillView(ViewModel):
    name: str
    latest: str = ""
    versions: list[str] = Field(default_factory=list)
    description: str = ""
    sha256: str = ""
    source: str = ""


class PluginView(ViewModel):
    id: str
    state: str = "unknown"
    version: str = "?"
    tools: list[str] = Field(default_factory=list)
    session: str = ""


class ApprovalView(ViewModel):
    approval_id: int
    tool: str = ""
    args_summary: str = ""
    risk: str = "high"
    ttl_ms: int | None = None
    session_id: str = ""


class AskView(ViewModel):
    ask_id: int
    question: str = ""
    options: list[Any] = Field(default_factory=list)
    session_id: str = ""


class AttachmentView(ViewModel):
    file_path: str
    mime: str = "image/png"
    sha256: str = "0" * 64
    w: int = 1
    h: int = 1
    size_bytes: int | None = None


class WorkflowStepView(ViewModel):
    intent: str


class WorkflowResultView(ViewModel):
    ok: bool
    sid: str
    results: list[dict[str, Any]] = Field(default_factory=list)


class PresetView(ViewModel):
    ok: bool
    preset: Literal["strict", "standard", "readonly", "locked"] | None = None
    sessions: int = 0
    error: str = ""


# Shared UI capability contract.  Parity tests compare the Web and Qt shells
# against this list rather than against each other's implementation details.
SHELL_CAPABILITIES: tuple[str, ...] = (
    "session.list",
    "session.create",
    "chat.send",
    "chat.stream",
    "chat.edit_last_user",
    "chat.resend_last_user",
    "chat.feedback_last_agent",
    "chat.attachment",
    "approval.decide",
    "ask.answer",
    "jobs.list",
    "jobs.start",
    "jobs.cancel",
    "schedule.list",
    "schedule.action",
    "subagent.list",
    "subagent.spawn",
    "subagent.cancel",
    "skill.list",
    "skill.registry_search",
    "skill.install",
    "skill.rollback",
    "skill.remove",
    "plugin.list",
    "plugin.action",
    "preset.set",
    "workflow.run",
    "telemetry.read",
    "tenant.switch",
    "settings.model_profiles",
)


__all__ = [
    "ApprovalView",
    "AskView",
    "AttachmentView",
    "JobView",
    "MessageView",
    "PluginView",
    "PresetView",
    "RegistrySkillView",
    "SHELL_CAPABILITIES",
    "ScheduleView",
    "SessionView",
    "SkillView",
    "SubagentView",
    "ViewModel",
    "WorkflowResultView",
    "WorkflowStepView",
]
