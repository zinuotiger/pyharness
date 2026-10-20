"""New-attempt lineage; schema-one intent/authorization bytes remain unchanged."""
from typing import Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.continuation_contracts import ContinuationPlan
from pyharness.application.document_contracts import VersionOne
from pyharness.application.execution_contracts import ExecutionReference,ExecutionId,StartExecution,ExecutionAuthorization
from pyharness.application.run_attempt_contracts import AttemptId,SubmissionKey,RunAttempt
from pyharness.application.workspace_contracts import Digest
from pyharness.application.workspace_manifest import StrictModel


class ContinueExecution(VersionOne):
    schema_version:Literal[1]=Field(...)
    execution_ref:ExecutionReference
    plan_digest:Digest
    attempt_id:AttemptId
    execution_id:ExecutionId
    confirm_host_boundary:bool=False


class ContinuationBasis(StrictModel):
    command:ContinueExecution
    submission_key:SubmissionKey
    plan:ContinuationPlan

    @model_validator(mode='after')
    def exact_basis(self):
        from pyharness.application.continuation_plan_codec import continuation_plan_digest
        p=self.plan;c=self.command
        if (p.execution_ref!=c.execution_ref or p.plan_digest!=c.plan_digest or c.execution_id==c.execution_ref.execution_id or
                continuation_plan_digest(p.model_dump(mode='json',exclude={'plan_digest'}))!=p.plan_digest):
            raise ValueError('continuation plan/command binding')
        if (not p.can_continue or p.blockers or not p.state_available or p.state is None or p.budget is None or
                len(p.journals)!=1 or p.journal_writer_active is not False or
                p.journals[0].session_id!=p.state.session_id or not p.state.checkpoint_seq<p.state.state_source_seq<=p.journals[0].observed_seq):
            raise ValueError('continuation requires exact available state and no blockers')
        sessions=[j.session_id for j in [*p.journals,*p.lineage_journals]]
        if len(set(sessions))!=len(sessions):raise ValueError('continuation lineage session collision')
        b=p.budget
        if not b.remaining_rounds or not all((b.remaining_limits.max_in_tokens,b.remaining_limits.max_out_tokens,b.remaining_limits.max_cost_yuan)):
            raise ValueError('continuation allowance exhausted')
        return self


def continued_attempt_digest(prerequisites,context,basis):
    from pyharness.application.workspace_store import canonical,digest_of
    base=digest_of(canonical(dict(prerequisites=prerequisites,context=context)))
    return digest_of(canonical(dict(base_binding_digest=base,continuation=basis)))


class ContinuedRunAttempt(RunAttempt):
    schema_version:Literal[2]=2
    continuation:ContinuationBasis

    @field_validator('schema_version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int or value!=2:raise ValueError('continued attempt requires integer version two')
        return value

    @model_validator(mode='after')
    def lineage(self):
        if self.attempt_id!=self.continuation.command.attempt_id or self.project_id!=self.continuation.plan.project_id:
            raise ValueError('continued attempt identity')
        return self


class ContinuedStartExecution(StartExecution):
    continuation:ContinuationBasis

    @model_validator(mode='after')
    def lineage(self):
        c=self.continuation.command
        if (self.execution_id!=c.execution_id or self.attempt_ref.attempt_id!=c.attempt_id or
                self.confirm_host_boundary!=c.confirm_host_boundary):raise ValueError('continued start identity')
        return self


class ContinuedExecutionAuthorization(ExecutionAuthorization):
    schema_version:Literal[2]=2
    selection:ContinuedStartExecution

    @field_validator('schema_version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int or value!=2:raise ValueError('continued authorization requires integer version two')
        return value

    @model_validator(mode='after')
    def lineage(self):
        basis=self.selection.continuation
        if (self.submission_key!=basis.submission_key or self.project_id!=basis.plan.project_id or
                self.session_id==basis.plan.state.session_id):raise ValueError('continued authorization identity')
        return self
