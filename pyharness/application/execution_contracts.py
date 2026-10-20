"""Exact product execution commands; clients select references, never authority."""
import hashlib
import json
from typing import Annotated, Literal
from pydantic import Field, model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.platform_models import AgentDefinition
from pyharness.application.run_attempt_contracts import AttemptId, SubmissionKey
from pyharness.application.workspace_contracts import ProjectId, Digest
from pyharness.application.workspace_manifest import StrictModel

ExecutionId = Annotated[str, Field(pattern=r'^ex-[0-9a-f]{16}$')]
ExecutionSessionId = Annotated[str, Field(pattern=r'^s-pa-[0-9a-f]{32}$')]
CancellationId = Annotated[str, Field(pattern=r'^ec-[0-9a-f]{16}$')]


class ExecutionReference(StrictModel):
    execution_id: ExecutionId
    revision: int = Field(ge=1,le=1)
    digest: Digest
    source_seq: int = Field(ge=1)


class CancelExecution(VersionOne):
    schema_version: Literal[1] = Field(...)
    cancellation_id: CancellationId
    execution_ref: ExecutionReference


class ExecutionCancellation(CancelExecution):
    revision: int = Field(default=1,ge=1,le=1)
    project_id: ProjectId
    created_by: str = Field(min_length=1,max_length=128)
    submission_key: SubmissionKey


class AttemptReference(StrictModel):
    attempt_id: AttemptId
    revision: int = Field(ge=1, le=1)
    digest: Digest
    source_seq: int = Field(ge=1)


class AgentVersionReference(StrictModel):
    agent_id: str = Field(pattern=r'^a-[0-9a-f]{32}$')
    version: int = Field(ge=1)
    sha256: Digest


class PreviewExecution(VersionOne):
    schema_version: Literal[1] = Field(...)
    attempt_ref: AttemptReference
    agent_ref: AgentVersionReference


class StartExecution(PreviewExecution):
    execution_id: ExecutionId
    confirmation_digest: Digest
    confirm_host_boundary: bool = False


class ExecutionEnvironment(StrictModel):
    agent_snapshot: dict
    model_fingerprint: Digest
    boundary: Literal['disabled', 'host_unrestricted', 'container', 'remote']

    @model_validator(mode='after')
    def known_boundary(self):
        agent = AgentDefinition.model_validate(self.agent_snapshot)
        expected = {'disabled': 'disabled', 'host_approved': 'host_unrestricted',
                    'isolated': 'container', 'remote': 'remote'}[agent.sandbox_profile.mode]
        if expected != self.boundary: raise ValueError('incoherent execution boundary')
        return self


class ExecutionAuthorization(VersionOne):
    execution_id: ExecutionId
    revision: int = Field(default=1, ge=1, le=1)
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=128)
    submission_key: SubmissionKey
    selection: StartExecution
    attempt_binding_digest: Digest
    environment: ExecutionEnvironment
    session_id: ExecutionSessionId
    task_id: str = Field(pattern=r'^p-[0-9a-f]{32}$')

    @model_validator(mode='after')
    def exact_agent(self):
        agent = AgentDefinition.model_validate(self.environment.agent_snapshot)
        reference = self.selection.agent_ref
        checksum = hashlib.sha256(json.dumps(self.environment.agent_snapshot, sort_keys=True).encode()).hexdigest()
        if (self.selection.execution_id != self.execution_id or agent.status != 'published'
                or reference.agent_id != agent.agent_id or reference.version != agent.current_version
                or reference.sha256 != checksum): raise ValueError('published Agent snapshot mismatch')
        if self.environment.boundary == 'host_unrestricted' and not self.selection.confirm_host_boundary:
            raise ValueError('host execution requires explicit operator confirmation')
        return self
