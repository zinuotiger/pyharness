"""Strict product-control resource payloads; composed by the public facade."""
from __future__ import annotations
from typing import Literal
from pydantic import ConfigDict, Field, field_validator, model_validator
from pyharness.events.payload_base import _PayloadBase

class _WorkspaceCommittedPayload(_PayloadBase):
    """Control fact: typed project revision in an immutable digest-addressed blob."""
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1] = 1

    revision: int = Field(ge=1)
    parent_revision: int = Field(ge=0)
    blob_ref: str = Field(pattern=r'^[0-9a-f]{64}$')
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    command_id: str = Field(pattern=r'^[A-Za-z0-9._-]{1,128}$')
    request_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    actor_ref: str = Field(min_length=1, max_length=128)

    @field_validator('schema_version', mode='before')
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('unsupported control version')
        return value

    @model_validator(mode='after')
    def coherent_reference(self):
        if self.blob_ref != self.digest or self.revision != self.parent_revision + 1:
            raise ValueError('incoherent resource reference')
        return self


class WorkspaceProjectCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['project']
    resource_id: str = Field(pattern=r'^p-[0-9a-f]{16}$')


class WorkspaceSourceGrantCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['source_grant']
    resource_id: str = Field(pattern=r'^sg-[0-9a-f]{16}$')


class WorkspaceSourceSnapshotCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['source_snapshot']
    resource_id: str = Field(pattern=r'^ss-[0-9a-f]{16}$')

    @model_validator(mode='after')
    def immutable_revision(self):
        if self.revision != 1 or self.parent_revision != 0:
            raise ValueError('snapshot commits are immutable first revisions')
        return self


class WorkspaceContextManifestCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['context_manifest']
    resource_id: str = Field(pattern=r'^cm-[0-9a-f]{16}$')


class WorkspaceGitSnapshotCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['workspace_snapshot']
    resource_id: str = Field(pattern=r'^ws-[0-9a-f]{16}$')


class WorkspaceInputCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['workspace_input']
    resource_id: str = Field(pattern=r'^wi-[0-9a-f]{16}$')


class WorkspaceTaskDefinitionCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['task_definition']
    resource_id: str = Field(pattern=r'^pt-[0-9a-f]{16}$')


class WorkspaceDocumentCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['document']
    resource_id: str = Field(pattern=r'^doc-[0-9a-f]{16}$')


class WorkspaceCitationCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['citation']
    resource_id: str = Field(pattern=r'^cit-[0-9a-f]{16}$')


class WorkspaceDeliveryCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['delivery_definition']
    resource_id: str = Field(pattern=r'^del-[0-9a-f]{16}$')


class WorkspaceRunAttemptCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['run_attempt']
    resource_id: str = Field(pattern=r'^ra-[0-9a-f]{16}$')


class WorkspaceExecutionCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['execution_authorization']
    resource_id: str = Field(pattern=r'^ex-[0-9a-f]{16}$')


class WorkspaceCancellationCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['execution_cancellation']
    resource_id: str = Field(pattern=r'^ec-[0-9a-f]{16}$')


class WorkspaceCorrectionCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['execution_correction']
    resource_id:str=Field(pattern=r'^cr-[0-9a-f]{16}$')


class WorkspaceIndexRepairCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['index_repair']
    resource_id:str=Field(pattern=r'^er-[0-9a-f]{16}$')
    revision:int=Field(ge=1,le=2)


class WorkspaceTaskGraphCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['task_graph']
    resource_id:str=Field(pattern=r'^tg-[0-9a-f]{16}$')
    revision:int=Field(ge=1,le=200)


class WorkspaceTaskGraphRunCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['task_graph_run']
    resource_id:str=Field(pattern=r'^gr-[0-9a-f]{16}$')


class WorkspaceGraphDispatchCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['task_graph_dispatch']
    resource_id:str=Field(pattern=r'^gd-[0-9a-f]{16}$')


class WorkspaceGraphCancellationCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['task_graph_cancellation']
    resource_id:str=Field(pattern=r'^gc-[0-9a-f]{16}$')


class WorkspaceDeliveryPackageCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['delivery_package']
    resource_id: str = Field(pattern=r'^dp-[0-9a-f]{16}$')


class WorkspaceRoleContractCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind: Literal['role_contract']
    resource_id: str = Field(pattern=r'^rc-[0-9a-f]{16}$')


class WorkspaceRoleHandoffCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['role_handoff']
    resource_id: str = Field(pattern=r'^ho-[0-9a-f]{16}$')


class WorkspaceHandoffAckCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['handoff_ack']
    resource_id: str = Field(pattern=r'^ha-[0-9a-f]{16}$')


class WorkspaceDeliveryAcceptanceCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['delivery_acceptance']
    resource_id: str = Field(pattern=r'^av-[0-9a-f]{16}$')


class WorkspaceDeliveryExportCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['delivery_export']
    resource_id: str = Field(pattern=r'^xp-[0-9a-f]{16}$')


class WorkspaceHandoffBudgetCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind: Literal['handoff_budget']
    resource_id: str = Field(pattern=r'^hb-[0-9a-f]{16}$')


class WorkspaceWriteGrantCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['writeback_grant']
    resource_id:str=Field(pattern=r'^wg-[0-9a-f]{16}$')


class WorkspaceMergePlanCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['merge_plan']
    resource_id:str=Field(pattern=r'^mp-[0-9a-f]{16}$')


class WorkspaceWritebackApplicationCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['writeback_application']
    resource_id:str=Field(pattern=r'^wa-[0-9a-f]{16}$')


class WorkspaceMergeExportCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['merge_export']
    resource_id:str=Field(pattern=r'^we-[0-9a-f]{16}$')


class WorkspaceRegressionCaseCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['regression_case']
    resource_id:str=Field(pattern=r'^case-[0-9a-f]{16}$')
    revision:int=Field(ge=1,le=200)


class WorkspaceRegressionDatasetCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['regression_dataset']
    resource_id:str=Field(pattern=r'^ds-[0-9a-f]{16}$')
    revision:int=Field(ge=1,le=200)


class WorkspaceExperimentPreparationCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['experiment_preparation']
    resource_id:str=Field(pattern=r'^prp-[0-9a-f]{16}$')


class WorkspaceExperimentDefinitionCommittedPayload(_WorkspaceCommittedPayload):
    resource_kind:Literal['experiment_definition']
    resource_id:str=Field(pattern=r'^exp-[0-9a-f]{16}$')
    revision:int=Field(ge=1,le=200)


class WorkspaceExperimentRunCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['experiment_run']
    resource_id:str=Field(pattern=r'^er-[0-9a-f]{16}$')


class WorkspaceExperimentCancellationCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['experiment_cancellation']
    resource_id:str=Field(pattern=r'^ecx-[0-9a-f]{16}$')


class WorkspaceExperimentReportCommittedPayload(WorkspaceSourceSnapshotCommittedPayload):
    resource_kind:Literal['experiment_report']
    resource_id:str=Field(pattern=r'^epr-[0-9a-f]{16}$')


class WorkspaceResourceCommittedPayload(_WorkspaceCommittedPayload):
    """Flat compatibility envelope with explicit strict resource dispatch."""
    resource_kind: Literal['project', 'source_grant', 'source_snapshot', 'context_manifest', 'task_definition', 'workspace_snapshot', 'workspace_input', 'document', 'citation', 'delivery_definition', 'run_attempt', 'execution_authorization','execution_cancellation','execution_correction','index_repair','task_graph','task_graph_run','task_graph_dispatch','task_graph_cancellation','delivery_package','role_contract','role_handoff','handoff_ack','delivery_acceptance','delivery_export','handoff_budget','writeback_grant','merge_plan','writeback_application','merge_export','regression_case','regression_dataset','experiment_preparation','experiment_definition','experiment_run','experiment_cancellation','experiment_report']
    resource_id: str

    @model_validator(mode='after')
    def typed_resource(self):
        model = {'project': WorkspaceProjectCommittedPayload,
                 'source_grant': WorkspaceSourceGrantCommittedPayload,
                 'source_snapshot': WorkspaceSourceSnapshotCommittedPayload,
                 'context_manifest': WorkspaceContextManifestCommittedPayload,
                 'task_definition': WorkspaceTaskDefinitionCommittedPayload,
                 'workspace_snapshot':WorkspaceGitSnapshotCommittedPayload,
                 'workspace_input':WorkspaceInputCommittedPayload,
                 'document':WorkspaceDocumentCommittedPayload,
                 'citation':WorkspaceCitationCommittedPayload,
                 'delivery_definition':WorkspaceDeliveryCommittedPayload,
                 'run_attempt':WorkspaceRunAttemptCommittedPayload,
                 'execution_authorization':WorkspaceExecutionCommittedPayload,
                 'execution_cancellation':WorkspaceCancellationCommittedPayload,
                 'execution_correction':WorkspaceCorrectionCommittedPayload,
                 'index_repair':WorkspaceIndexRepairCommittedPayload,
                 'task_graph':WorkspaceTaskGraphCommittedPayload,
                 'task_graph_run':WorkspaceTaskGraphRunCommittedPayload,
                 'task_graph_dispatch':WorkspaceGraphDispatchCommittedPayload,
                 'task_graph_cancellation':WorkspaceGraphCancellationCommittedPayload,
                 'delivery_package':WorkspaceDeliveryPackageCommittedPayload,
                 'role_contract':WorkspaceRoleContractCommittedPayload,
                 'role_handoff':WorkspaceRoleHandoffCommittedPayload,
                 'handoff_ack':WorkspaceHandoffAckCommittedPayload,
                 'delivery_acceptance':WorkspaceDeliveryAcceptanceCommittedPayload,
                 'delivery_export':WorkspaceDeliveryExportCommittedPayload,
                 'handoff_budget':WorkspaceHandoffBudgetCommittedPayload,
                 'writeback_grant':WorkspaceWriteGrantCommittedPayload,'merge_plan':WorkspaceMergePlanCommittedPayload,
                 'writeback_application':WorkspaceWritebackApplicationCommittedPayload,'merge_export':WorkspaceMergeExportCommittedPayload,
                 'regression_case':WorkspaceRegressionCaseCommittedPayload,'regression_dataset':WorkspaceRegressionDatasetCommittedPayload,
                 'experiment_preparation':WorkspaceExperimentPreparationCommittedPayload,'experiment_definition':WorkspaceExperimentDefinitionCommittedPayload,
                 'experiment_run':WorkspaceExperimentRunCommittedPayload,'experiment_cancellation':WorkspaceExperimentCancellationCommittedPayload,
                 'experiment_report':WorkspaceExperimentReportCommittedPayload}[self.resource_kind]
        model.model_validate(self.model_dump())
        return self



_WorkspaceCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceProjectCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceSourceGrantCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceSourceSnapshotCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceContextManifestCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceGitSnapshotCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceInputCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceTaskDefinitionCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceDocumentCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceCitationCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceDeliveryCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceRunAttemptCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceExecutionCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceCancellationCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceCorrectionCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceTaskGraphCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceTaskGraphRunCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceResourceCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceRegressionCaseCommittedPayload.__module__ = 'pyharness.events.payload'
WorkspaceRegressionDatasetCommittedPayload.__module__ = 'pyharness.events.payload'
