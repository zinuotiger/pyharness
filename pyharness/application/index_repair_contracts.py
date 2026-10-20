"""A bounded derived-index command and its original-owner audit resource."""
from typing import Annotated,Literal
from pydantic import Field,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.execution_contracts import ExecutionReference
from pyharness.application.recovery_contracts import RecoveryPlan,IndexObservation
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.workspace_contracts import ProjectId,Digest

IndexRepairId=Annotated[str,Field(pattern=r'^er-[0-9a-f]{16}$')]


class RepairIndex(VersionOne):
    schema_version:Literal[1]=Field(...)
    repair_id:IndexRepairId
    execution_ref:ExecutionReference
    plan_digest:Digest


class IndexRepairRevision(VersionOne):
    repair_id:IndexRepairId
    revision:int=Field(ge=1,le=2)
    phase:Literal['started','succeeded','failed','unknown']
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    command:RepairIndex
    plan:RecoveryPlan
    index_scope_digest:Digest
    fault_digest:Digest
    attempt:int=Field(ge=1,le=2)
    index_observation:IndexObservation|None=None
    error_code:Literal['PERS-202','repair_cancelled']|None=None

    @model_validator(mode='after')
    def coherent(self):
        if (self.command.repair_id!=self.repair_id or self.command.execution_ref!=self.plan.execution_ref or
                self.command.plan_digest!=self.plan.plan_digest or self.project_id!=self.plan.project_id):raise ValueError('repair plan binding')
        if self.plan.index_observation.scope_digest!=self.index_scope_digest:raise ValueError('repair index scope binding')
        if self.revision==1:
            if self.phase!='started' or self.index_observation is not None or self.error_code is not None:raise ValueError('repair start fields')
        elif self.phase=='started' or self.index_observation is None:raise ValueError('repair terminal fields')
        elif self.phase=='succeeded':
            if self.index_observation.state!='current' or self.error_code is not None:raise ValueError('repair success not observed')
        elif self.error_code is None:raise ValueError('repair failure code required')
        return self
