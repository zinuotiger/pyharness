"""Server-authored preparation and definition records on the original journal."""
from typing import Literal
from pydantic import Field,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.task_graph_contracts import TaskGraphReference
from pyharness.application.regression_records import RegressionRecord
from pyharness.application.experiment_contracts import (ExperimentId,PreparationId,
    ConfirmExperimentPreparation,PreparationReference,ExperimentCaseNodes)


class ExperimentPreparation(VersionOne):
    preparation_id:PreparationId
    revision:Literal[1]=1
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    selection:ConfirmExperimentPreparation
    plan_digest:Digest

    @model_validator(mode='after')
    def selected_basis(self):
        if self.project_revision!=self.selection.project_revision or self.plan_digest!=self.selection.confirmation_digest:
            raise ValueError('preparation differs from confirmed plan')
        return self


class ExperimentRevision(RegressionRecord):
    experiment_id:ExperimentId
    selection:ConfirmExperimentPreparation
    preparation_ref:PreparationReference
    graph_ref:TaskGraphReference
    case_nodes:list[ExperimentCaseNodes]=Field(min_length=1,max_length=10)
    dataset_total:int=Field(ge=1,le=32)

    @model_validator(mode='after')
    def exact_cases(self):
        if self.experiment_id!=self.selection.experiment_id or [v.case_ref for v in self.case_nodes]!=self.selection.case_refs:
            raise ValueError('experiment fixed cases differ')
        if self.dataset_total<len(self.case_nodes):raise ValueError('dataset count differs')
        if len({v.task_ref.product_task_id for v in self.case_nodes})!=len(self.case_nodes):
            raise ValueError('distinct prepared case tasks required')
        names=[name for v in self.case_nodes for name in (v.left_node_id,v.right_node_id)]
        if len(set(names))!=len(names):raise ValueError('duplicate comparison node')
        return self


EXPERIMENT_MODELS={'experiment_preparation':(ExperimentPreparation,'preparation_id'),
    'experiment_definition':(ExperimentRevision,'experiment_id')}
from pyharness.application.experiment_run_contracts import EXPERIMENT_RUN_MODELS
EXPERIMENT_MODELS.update(EXPERIMENT_RUN_MODELS)
from pyharness.application.experiment_report_contracts import ExperimentReport
EXPERIMENT_MODELS['experiment_report']=(ExperimentReport,'report_id')
