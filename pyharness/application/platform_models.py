"""Validated platform DTOs. Execution facts are projected from SessionLog."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pyharness.core.sandbox_models import SandboxProfile, SandboxRecord


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)




class Budget(Model):
    input_tokens: int = Field(default=20000, ge=1, le=1000000)
    output_tokens: int = Field(default=4000, ge=1, le=100000)
    tool_calls: int = Field(default=20, ge=1, le=200)
    cost: float = Field(default=1, gt=0, le=100)


class AgentDefinition(Model):
    review_mode: Literal['manual', 'automatic'] = 'manual'
    agent_id: str = ''
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    system_prompt: str = Field(default='', max_length=12000)
    model_connection: str | None = None
    memory_scope: Literal['session','team'] = 'session'
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    mcp_connections: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=30)
    knowledge_sources: list[str] = Field(default_factory=list, max_length=30)
    require_evidence: bool = False
    strict_knowledge: bool = False
    permission_policy: Literal['strict', 'standard'] = 'strict'
    sandbox_profile: SandboxProfile = Field(default_factory=SandboxProfile)
    budget: Budget = Field(default_factory=Budget)
    max_rounds: int = Field(default=8, ge=1, le=50)
    timeout: int = Field(default=120, ge=1, le=600)
    approval_rules: list[str] = Field(default_factory=lambda: ['file_write', 'command'])
    output_schema: dict = Field(default_factory=dict)
    status: Literal['draft', 'published', 'deprecated'] = 'draft'
    current_version: int = Field(default=0, ge=0)

    @field_validator('approval_rules')
    @classmethod
    def validate_approval_rules(cls, rules):
        from pyharness.application.approval_policy import validate_rules
        return validate_rules(rules)


class AgentVersion(Model):
    model_config = ConfigDict(extra='forbid', frozen=True)
    agent_id: str
    version: int = Field(ge=1)
    snapshot: AgentDefinition
    sha256: str
    created_at: str = Field(default_factory=utcnow)


class Run(Model):
    run_id: str
    session_id: str
    task_id: str = ''
    name: str = ''
    agent_id: str | None = None
    agent_version: int | None = None
    status: Literal['queued', 'running', 'waiting_approval', 'paused', 'retrying', 'completed', 'failed', 'cancelled'] = 'queued'
    current_step: int = 0
    total_steps: int = 0
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    retry_count: int = 0
    token_usage: int = 0
    estimated_cost: float | None = None
    sandbox_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_diagnostics: dict | None = None
    error_kind: str | None = None
    usage_status: str = "unknown"
    usage_unknown_requests: int = 0


class RunStep(Model):
    step_id: str
    run_id: str
    name: str
    error_code: str | None = None
    status: Literal['pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled', 'skipped']
    started_at: str | None = None
    finished_at: str | None = None


class ArtifactRecord(Model):
    artifact_id: str
    run_id: str | None = None
    session_id: str
    step_id: str | None = None
    filename: str
    media_type: str = 'application/octet-stream'
    size: int = Field(ge=0)
    sha256: str
    storage_path: str
    preview_type: str = 'text'
    download_allowed: bool = False
    approval_required: bool = True
    retention_until: str | None = None
    created_at: str = Field(default_factory=utcnow)
    manifest_sha256: str | None = None
    validation_status: Literal['not_run', 'passed', 'failed'] = 'not_run'




class TraceSpan(Model):
    span_id: str
    run_id: str | None = None
    parent_span_id: str | None = None
    type: str
    name: str
    status: str
    started_at: str
    finished_at: str | None = None
    duration_ms: float | None = None
    token_usage: int = 0
    cost: float | None = None
    input_summary: str = ''
    sandbox_id: str | None = None
    output_summary: str = ''
    error_diagnostics: dict | None = None
    error_code: str | None = None


class HealthSummary(Model):
    component: str
    status: str
    detail: str = ''
    checked_at: str = Field(default_factory=utcnow)


class KnowledgeSource(Model):
    source_id: str
    name: str
    provider: str = 'exact_keyword'
    status: str = 'indexed'
    version: int = 1
    size: int = 0
    sha256: str
    updated_at: str = Field(default_factory=utcnow)


class ConnectionRecord(Model):
    connection_id: str
    name: str
    type: str
    status: str = 'not_configured'
    scopes: list[str] = Field(default_factory=list)
    last_test: str | None = None
    last_used: str | None = None
    agents: list[str] = Field(default_factory=list)


class RunRequest(Model):
    text: str = Field(min_length=1,max_length=32000)
    session_id: str | None = None
    agent_id: str | None = None
    agent_version: int | None = Field(default=None,ge=1)
    retry_count: int = Field(default=0,ge=0,le=1)
    artifact_ids: list[str] = Field(default_factory=list,max_length=10)
    use_vision: bool = False
