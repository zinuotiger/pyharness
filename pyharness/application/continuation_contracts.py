"""Private preflight facts are neither a lease nor permission to replay tools."""
from typing import Literal
from pydantic import Field
from pyharness.application.document_contracts import VersionOne
from pyharness.application.execution_contracts import ExecutionReference,ExecutionSessionId
from pyharness.application.workspace_contracts import Digest,ProjectId
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.recovery_contracts import JournalBasis
from pyharness.application.state_snapshot_contracts import StateBudgetLimits,StateUsage

ContinuationBlocker=Literal['execution_live','execution_completed','journal_unconfirmed','child_journal_unconfirmed',
    'journal_scope_truncated','artifact_scope_truncated','unknown_effects','security_refusal','checkpoint_missing',
    'continuation_not_implemented','journal_writer_active','state_unavailable','state_integrity_unconfirmed',
    'state_filesystem_incomplete','state_materials_changed','checkpoint_superseded','configuration_changed',
    'configuration_unavailable','delegated_state_not_supported','pending_correction','unsupported_effect_state',
    'continuation_contract_missing','budget_observation_unconfirmed','budget_exhausted','round_limit_exhausted',
    'runtime_contract_changed','runtime_contract_unconfirmed','auxiliary_state_not_supported','continuation_chain_not_supported','lineage_unconfirmed','checkpoint_reserved']

class ContinuationBudget(StrictModel):
    observed_usage:StateUsage
    reserved_unconfirmed:StateUsage
    remaining_limits:StateBudgetLimits
    potential_unconfirmed_attempts:int=Field(ge=1)
    remaining_rounds:int=Field(ge=0)
    tool_calls_used:int=Field(ge=0)
    remaining_timeout_seconds:float=Field(gt=0,le=600,allow_inf_nan=False)
    basis:Literal['runtime_estimate_and_unconfirmed_request_reservation']='runtime_estimate_and_unconfirmed_request_reservation'

class ContinuationStateBasis(StrictModel):
    session_id:ExecutionSessionId
    checkpoint_id:str=Field(pattern=r'^ck-[0-9a-f]{16}$')
    checkpoint_seq:int=Field(ge=1)
    checkpoint_digest:Digest
    state_source_seq:int=Field(ge=1)
    state_digest:Digest
    state_size:int=Field(ge=1,le=32*1048576)
    materials_digest:Digest
    messages_digest:Digest
    filesystem_capture:Literal['files_only_legacy','files_and_directories','no_workspace']
    file_count:int=Field(ge=0,le=4096)
    directory_count:int=Field(ge=0,le=4096)
    kv_present:bool
    integrity:Literal['verified']='verified'
    capture_scope:Literal['session_local_state']='session_local_state'

class ContinuationPlan(VersionOne):
    project_id:ProjectId
    execution_ref:ExecutionReference
    plan_digest:Digest
    recovery_plan_digest:Digest
    execution_status:Literal['preparing','queued','running','interrupted','completed','failed','cancelled','unknown']
    journals:list[JournalBasis]=Field(max_length=401)
    lineage_journals:list[JournalBasis]=Field(default_factory=list,max_length=20)
    journal_writer_active:bool|None
    state_available:bool
    state:ContinuationStateBasis|None
    budget:ContinuationBudget|None=None
    current_model_fingerprint:Digest|None
    blockers:list[ContinuationBlocker]=Field(max_length=32)
    can_continue:bool=False
    continuation_authorized:Literal[False]=False
    automatic_dispatch:Literal[False]=False
    replay_authorized:Literal[False]=False
    atomic_snapshot:Literal[False]=False
