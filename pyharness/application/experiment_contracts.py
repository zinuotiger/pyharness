"""Fixed paired data selections. Preparation is not an execution or a budget grant."""
from typing import Annotated,Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import Digest
from pyharness.application.execution_contracts import AgentVersionReference
from pyharness.application.regression_contracts import DatasetReference,CaseReference,schema_manual_text
from pyharness.application.task_graph_contracts import TaskGraphLimits,TaskGraphNodeId
from pyharness.application.context_manifest import TaskVersionReference

ExperimentId=Annotated[str,Field(pattern=r'^exp-[0-9a-f]{16}$')]
PreparationId=Annotated[str,Field(pattern=r'^prp-[0-9a-f]{16}$')]


def explicit_true(value):
    if type(value) is not bool or value is not True:raise ValueError('actual boolean confirmation required')
    return value


def explicit_first_revision(value):
    if type(value) is not int or value!=1:raise ValueError('actual integer first revision required')
    return value


class PrepareExperiment(VersionOne):
    schema_version:Literal[1]=Field(...)
    experiment_id:ExperimentId
    expected_revision:int=Field(ge=0,le=199)
    project_revision:int=Field(ge=1)
    title:str=Field(min_length=1,max_length=100)
    dataset_ref:DatasetReference
    case_refs:list[CaseReference]=Field(min_length=1,max_length=10)
    left_agent_ref:AgentVersionReference
    right_agent_ref:AgentVersionReference
    concurrency:int=Field(ge=1,le=4)
    limits:TaskGraphLimits

    @field_validator('title')
    @classmethod
    def stored_title(cls,value):return schema_manual_text(value)

    @model_validator(mode='after')
    def exact_sides(self):
        if self.left_agent_ref==self.right_agent_ref:raise ValueError('two distinct published versions required')
        if len({r.case_id for r in self.case_refs})!=len(self.case_refs):raise ValueError('duplicate experiment case')
        return self


class ConfirmExperimentPreparation(PrepareExperiment):
    confirmed:bool
    confirmation_digest:Digest

    @field_validator('confirmed')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('explicit preparation confirmation required')
        return value


class PreparationReference(StrictModel):
    preparation_id:PreparationId
    revision:Literal[1]=1
    digest:Digest
    source_seq:int=Field(ge=1)
    _revision=field_validator('revision',mode='before')(explicit_first_revision)


class ExperimentReference(StrictModel):
    experiment_id:ExperimentId
    revision:int=Field(ge=1,le=200)
    digest:Digest
    source_seq:int=Field(ge=1)


class ExperimentCaseNodes(StrictModel):
    case_ref:CaseReference
    task_ref:TaskVersionReference
    left_node_id:TaskGraphNodeId
    right_node_id:TaskGraphNodeId
