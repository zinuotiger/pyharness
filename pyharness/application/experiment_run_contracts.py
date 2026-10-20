"""An explicit bounded grant funds one original paired graph, never a new scheduler."""
from typing import Annotated,Literal
from pydantic import Field,model_validator,field_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.experiment_contracts import ExperimentReference,explicit_true,explicit_first_revision
from pyharness.application.task_graph_contracts import TaskGraphLimits,TaskGraphNodeId
from pyharness.application.task_graph_run_contracts import StartTaskGraph
from pyharness.application.execution_contracts import AgentVersionReference
from pyharness.application.context_manifest import TaskVersionReference
from pyharness.application.run_attempt_contracts import SubmissionKey

ExperimentRunId=Annotated[str,Field(pattern=r'^er-[0-9a-f]{16}$')]
ExperimentCancellationId=Annotated[str,Field(pattern=r'^ecx-[0-9a-f]{16}$')]


class PreviewExperimentRun(VersionOne):
    schema_version:Literal[1]=Field(...)
    experiment_run_id:ExperimentRunId
    experiment_ref:ExperimentReference
    budget_grant:TaskGraphLimits
    confirm_host_boundary:bool=False


class StartExperimentRun(PreviewExperimentRun):
    confirmed:Literal[True]
    confirmation_digest:Digest
    _confirmation=field_validator('confirmed',mode='before')(explicit_true)


class ExperimentRunReference(StrictModel):
    experiment_run_id:ExperimentRunId
    revision:Literal[1]
    digest:Digest
    source_seq:int=Field(ge=1)
    _revision=field_validator('revision',mode='before')(explicit_first_revision)


class ResumeExperimentRun(VersionOne):
    schema_version:Literal[1]=Field(...)
    experiment_run_ref:ExperimentRunReference


class ConfirmExperimentResumption(ResumeExperimentRun):
    confirmed:Literal[True]
    confirmation_digest:Digest
    _confirmation=field_validator('confirmed',mode='before')(explicit_true)


class CancelExperimentRun(ResumeExperimentRun):
    cancellation_id:ExperimentCancellationId


class ExperimentNodeManifest(StrictModel):
    node_id:TaskGraphNodeId
    depends_on:list[TaskGraphNodeId]=Field(max_length=20)
    agent_ref:AgentVersionReference
    task_ref:TaskVersionReference
    boundary:Literal['disabled','host_unrestricted','container','remote']
    model_fingerprint:Digest
    allocated_budget:TaskGraphLimits


class ExperimentGraphPlan(StrictModel):
    confirmation_digest:Digest
    concurrency:int=Field(ge=1,le=4)
    limits:TaskGraphLimits
    allocated_budget:TaskGraphLimits
    nodes:list[ExperimentNodeManifest]=Field(min_length=2,max_length=20)

    @model_validator(mode='after')
    def bounded_allocation(self):
        if len({n.node_id for n in self.nodes})!=len(self.nodes):raise ValueError('duplicate funded node')
        for key in TaskGraphLimits.model_fields:
            if sum(getattr(n.allocated_budget,key) for n in self.nodes)!=getattr(self.allocated_budget,key):
                raise ValueError('funded allocation sum')
            if getattr(self.allocated_budget,key)>getattr(self.limits,key):raise ValueError('funded graph limit')
        return self


class ExperimentRunAuthorization(VersionOne):
    experiment_run_id:ExperimentRunId
    revision:Literal[1]=1
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    selection:StartExperimentRun
    graph_command:StartTaskGraph
    graph_plan:ExperimentGraphPlan

    @model_validator(mode='after')
    def bound_grant(self):
        if self.experiment_run_id!=self.selection.experiment_run_id:raise ValueError('funded run identity')
        if self.graph_command.confirmation_digest!=self.graph_plan.confirmation_digest:raise ValueError('funded graph plan')
        if self.graph_command.confirm_host_boundary!=self.selection.confirm_host_boundary:raise ValueError('funded boundary')
        for key in TaskGraphLimits.model_fields:
            if not getattr(self.graph_plan.allocated_budget,key)<=getattr(self.selection.budget_grant,key)<=getattr(self.graph_plan.limits,key):
                raise ValueError('explicit grant must cover allocation within graph limits')
        if any(n.boundary=='host_unrestricted' for n in self.graph_plan.nodes) and not self.selection.confirm_host_boundary:
            raise ValueError('funded host boundary unconfirmed')
        return self


class ExperimentCancellation(CancelExperimentRun):
    revision:Literal[1]=1
    project_id:ProjectId
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey


EXPERIMENT_RUN_MODELS={'experiment_run':(ExperimentRunAuthorization,'experiment_run_id'),
    'experiment_cancellation':(ExperimentCancellation,'cancellation_id')}
