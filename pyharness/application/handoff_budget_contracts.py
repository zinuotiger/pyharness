"""Immutable whole-consumer reservations; integer money prevents split rounding."""
from pydantic import Field
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.execution_contracts import ExecutionId,ExecutionReference,AttemptReference,AgentVersionReference
from pyharness.application.delivery_contracts import HandoffReference,RoleContractReference,DeliveryPackageReference
from pyharness.application.run_attempt_contracts import SubmissionKey


class HandoffAllowance(StrictModel):
    input_tokens:int=Field(ge=0)
    output_tokens:int=Field(ge=0)
    tool_calls:int=Field(ge=0)
    cost_microyuan:int=Field(ge=0)
    rounds:int=Field(ge=0)
    timeout_seconds:int=Field(ge=0)


class HandoffBudget(VersionOne):
    budget_id:str=Field(pattern=r'^hb-[0-9a-f]{16}$')
    revision:int=Field(default=1,ge=1,le=1)
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    parent_execution_ref:ExecutionReference
    attempt_ref:AttemptReference
    execution_id:ExecutionId
    agent_ref:AgentVersionReference
    role_ref:RoleContractReference
    package_ref:DeliveryPackageReference
    handoff_refs:list[HandoffReference]=Field(min_length=1,max_length=20)
    parent_limits:HandoffAllowance
    observed:HandoffAllowance
    capacity:HandoffAllowance
    reserved:HandoffAllowance
    observation_digest:Digest
