"""Strict read-only recovery diagnostics; plans never confer execution authority."""
from typing import Literal
from pydantic import Field
from pyharness.application.document_contracts import VersionOne
from pyharness.application.execution_contracts import ExecutionReference,ExecutionSessionId
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.workspace_manifest import StrictModel

Blocker=Literal['execution_live','execution_completed','journal_unconfirmed','child_journal_unconfirmed',
    'journal_scope_truncated','artifact_scope_truncated','unknown_effects','security_refusal',
    'checkpoint_missing','restore_state_unavailable','continuation_not_implemented','continuation_gate_required']


class JournalBasis(StrictModel):
    session_id: ExecutionSessionId
    observed_seq: int = Field(ge=1)
    prefix_digest: Digest


class CheckpointBasis(StrictModel):
    checkpoint_id: str = Field(pattern=r'^ck-[0-9a-f]{16}$')
    source_seq: int = Field(ge=1)
    prefix_seq: int = Field(ge=1)
    prefix_digest: Digest
    materialized_messages_digest: Digest


class IndexObservation(StrictModel):
    state: Literal['current','stale','not_built','unavailable']
    scope_digest: Digest | None = None
    expected_content_seq: int | None = Field(default=None,ge=0)
    observed_content_seq: int | None = Field(default=None,ge=0)
    error_code: Literal['PERS-202'] | None = None


class ArtifactBasis(StrictModel):
    artifact_id: str = Field(pattern=r'^f-[0-9a-f]{32}$')
    session_id: ExecutionSessionId
    source_seq: int = Field(ge=1)
    record_digest: Digest
    sha256: Digest
    integrity: Literal['verified','mismatch','unavailable']


class RecoveryPlan(VersionOne):
    plan_digest: Digest
    project_id: ProjectId
    execution_ref: ExecutionReference
    execution_status: Literal['preparing','queued','running','interrupted','completed','failed','cancelled','unknown']
    journals: list[JournalBasis] = Field(max_length=401)
    journals_total: int = Field(ge=0)
    journals_truncated: bool
    latest_checkpoint: CheckpointBasis | None
    artifacts: list[ArtifactBasis] = Field(max_length=1000)
    artifacts_total: int = Field(ge=0)
    artifacts_truncated: bool
    index_observation: IndexObservation
    unknown_effects: bool
    continuation_blockers: list[Blocker] = Field(max_length=20)
    continuation_authorized: Literal[False] = False
    automatic_dispatch: Literal[False] = False
    repair_authorized: Literal[False] = False
    repair_implementation: Literal['pending','available'] = 'available'
    restore_state_available: bool = False
    atomic_snapshot: Literal[False] = False
