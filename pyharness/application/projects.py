"""Project metadata commands, deliberately without implicit host-root authorization."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pyharness.application.accounts import Accounts, current_actor
from pyharness.application.workspace_contracts import ProjectId, ProjectRevision
from pyharness.application.workspace_store import WorkspaceStore
from pyharness.core.lifecycle import admitted, stop_admissions
from pyharness.errors import raise_code
from pyharness.application.product_tasks import ProductTasks
from pyharness.application.document_operations import DocumentOperations
from pyharness.application.run_attempts import RunAttempts
from pyharness.application.product_execution_sessions import ProductExecutionSessions
from pyharness.application.product_executions import ProductExecutions
from pyharness.application.product_execution_cancellation import ProductExecutionCancellation
from pyharness.application.product_execution_corrections import ProductExecutionCorrections
from pyharness.application.product_index_repairs import ProductIndexRepairs
from pyharness.application.product_continuations import ProductContinuations
from pyharness.application.product_deliveries import ProductDeliveries
from pyharness.application.product_writebacks import ProductWritebacks
from pyharness.application.product_observations import ProductObservations
from pyharness.application.product_regressions import ProductRegressions
from pyharness.application.product_experiments import ProductExperiments
from pyharness.application.product_task_graphs import ProductTaskGraphs


class ProjectCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    project_id: ProjectId
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    archived: bool = False


class Projects(ProductTasks, DocumentOperations, RunAttempts, ProductExecutionSessions, ProductExecutions,ProductExecutionCancellation,ProductExecutionCorrections,ProductIndexRepairs,ProductContinuations,ProductTaskGraphs,ProductDeliveries,ProductWritebacks,ProductObservations,ProductRegressions,ProductExperiments):
    def __init__(self, service):
        self.service = service
        self.store = WorkspaceStore(service.tenant_root() / 'workspace-v1', tenant_id=service.tenant_id)

    def _authorize(self, *, write=False, grant_write=False):
        self.service._ensure_open()
        # Location/runtime owner only; every handoff still checks the actual
        # requester, fixed resource grants and current original output bytes.
        self.store._delivery_service=self.service
        actor = current_actor.get()
        if actor is not None and actor.get('tenant') != self.service.tenant_id:
            raise_code('GRD-403', reason='project_tenant_mismatch')
        if Accounts.manages_tenant(self.service._storage_root(), self.service.tenant_id):
            roles = Accounts(self.service._storage_root()).teams(actor['user_id']) if actor else []
            role = next((row['role'] for row in roles if row['id'] == self.service.tenant_id), None)
            if role is None or (write and role not in {'owner', 'member'}) or (grant_write and role != 'owner'):
                raise_code('GRD-403', reason='project_forbidden')
        return actor['user_id'] if actor else 'local-operator'

    @admitted
    async def list(self):
        actor=self._authorize()
        rows = await self.store.projects_readonly()
        if self._authorize()!=actor:raise_code('GRD-403',reason='project_directory_subject_changed')
        return {'projects': rows, 'total': len(rows)}

    @admitted
    async def prepare_workspace_input(self,project_id,body,command_id):
        from pyharness.application.workspace_owned_inputs import PrepareWorkspaceInput
        from pyharness.application.selected_owned_inputs import PrepareSelectedInput
        self._authorize(write=True)
        try:
            model=PrepareSelectedInput if type(body.get('schema_version')) is int and body['schema_version']==2 else PrepareWorkspaceInput
            return await self.store.prepare_workspace_input(self._workspace_id(project_id,ProjectId),model.model_validate(body),
                command_id=command_id,authorize=lambda:self._authorize(write=True))
        except OSError:raise_code('PERS-202',reason='workspace_input_preparation_unavailable')

    @admitted
    async def selected_input_descriptor(self,project_id,manifest_id,revision):
        from pyharness.application.context_manifest import ManifestId
        self._authorize(write=True)
        return await self.store.selected_input_descriptor(self._workspace_id(project_id,ProjectId),self._workspace_id(manifest_id,ManifestId),
            revision,authorize=lambda:self._authorize(write=True))

    @admitted
    async def workspace_input(self,project_id,input_id):
        from pyharness.application.workspace_owned_inputs import WorkspaceInputId
        self._authorize(write=True)
        return await self.store.workspace_input(self._workspace_id(project_id,ProjectId),self._workspace_id(input_id,WorkspaceInputId),authorize=lambda:self._authorize(write=True))

    @admitted
    async def workspace_inputs(self,project_id,query):
        self._authorize(write=True)
        return await self.store.workspace_inputs(self._workspace_id(project_id,ProjectId),query,authorize=lambda:self._authorize(write=True))

    @admitted
    async def save(self, body, command_id):
        actor = self._authorize(write=True)
        command = ProjectCommand.model_validate(body)
        project = ProjectRevision(project_id=command.project_id, revision=command.expected_revision + 1,
                                  name=command.name, description=command.description, archived=command.archived)
        return await self.store.commit(project, expected_revision=command.expected_revision,
                                       command_id=command_id, actor_ref=actor)

    @admitted
    async def audit(self, **parameters):
        subject = self._authorize()
        if parameters:
            return await self.store.audit_page(subject=subject, **parameters)
        events = await self.store.audit()
        return {'events': events, 'total': len(events)}

    @admitted
    async def list_source_grants(self, project_id):
        self._authorize()
        from pydantic import TypeAdapter
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        rows = await self.store.source_grants(project_id)
        return {'grants': rows, 'total': len(rows)}

    @admitted
    async def issue_source_grant(self, project_id, body, command_id):
        actor = self._authorize(write=True, grant_write=True)
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import IssueGrant
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        command = IssueGrant.model_validate(body)
        return await self.store.issue_source_grant(project_id, command, command_id=command_id, actor_ref=actor)

    @admitted
    async def revoke_source_grant(self, grant_id, body, command_id):
        actor = self._authorize(write=True, grant_write=True)
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId, RevokeGrant
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        command = RevokeGrant.model_validate(body)
        return await self.store.revoke_source_grant(grant_id, command, command_id=command_id, actor_ref=actor)

    @admitted
    async def preflight_source_grant(self, project_id, grant_id, body):
        self._authorize()
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId
        from pyharness.application.source_preflight import PreflightGrant
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        command = PreflightGrant.model_validate(body)
        return await self.store.preflight_source_grant(project_id, grant_id, command)

    @admitted
    async def preview_source_selection(self, project_id, grant_id, body):
        self._authorize()
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId
        from pyharness.application.source_selection import SelectionPreview
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        command = SelectionPreview.model_validate(body)
        return await self.store.preview_source_selection(project_id, grant_id, command)

    @admitted
    async def preview_snapshot_context(self, project_id, grant_id, body):
        self._authorize(write=True)
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId
        from pyharness.application.snapshot_context import SnapshotContextPreview
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        return await self.store.preview_snapshot_context(project_id, grant_id, SnapshotContextPreview.model_validate(body),
            authorize=lambda: self._authorize(write=True))

    @admitted
    async def preview_snapshot(self, project_id, grant_id, body):
        self._authorize(write=True)
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId
        from pyharness.application.source_selection import SelectionPreview
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        return await self.store.preview_snapshot(project_id, grant_id, SelectionPreview.model_validate(body),
            authorize=lambda: self._authorize(write=True))

    @admitted
    async def capture_snapshot(self, project_id, grant_id, body, command_id):
        actor = self._authorize(write=True)
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId
        from pyharness.application.source_snapshot import CaptureSnapshot
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        return await self.store.capture_snapshot(project_id, grant_id, CaptureSnapshot.model_validate(body),
            command_id=command_id, actor_ref=actor, authorize=lambda: self._authorize(write=True))

    @admitted
    async def snapshots(self, project_id, grant_id):
        self._authorize()
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        return await self.store.snapshots(project_id, grant_id, authorize=lambda: self._authorize())

    async def close(self):
        await stop_admissions(self)
        await self.store.close()

    @admitted
    async def context_task_references(self, project_id, body=None, command_id=None):
        from pydantic import TypeAdapter
        from pyharness.application.context_manifest import TaskReferenceCommand
        self._authorize(write=True)
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        authorize = lambda: self._authorize(write=True)
        if body is not None:
            return await self.store.save_context_task_reference(project_id, TaskReferenceCommand.model_validate(body),
                command_id=command_id, authorize=authorize)
        rows = await self.store.context_task_references(project_id, authorize=authorize)
        return {'task_references': rows, 'total': len(rows)}

    @admitted
    async def context_manifests(self, project_id, body=None, command_id=None):
        from pydantic import TypeAdapter
        from pyharness.application.context_manifest import ManifestCommand
        self._authorize(write=True)
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        authorize = lambda: self._authorize(write=True)
        if body is not None:
            return await self.store.save_context_manifest(project_id, ManifestCommand.model_validate(body),
                command_id=command_id, authorize=authorize)
        rows = await self.store.context_manifests(project_id, authorize=authorize)
        return {'manifests': rows, 'total': len(rows)}

    @admitted
    async def context_manifest(self, project_id, manifest_id, revision):
        from pydantic import TypeAdapter
        from pyharness.application.context_manifest import ManifestId
        self._authorize(write=True)
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        manifest_id = TypeAdapter(ManifestId).validate_python(manifest_id, strict=True)
        if type(revision) is not int or not 1 <= revision <= 200:
            raise_code('EVT-100', reason='context_revision_invalid')
        return await self.store.context_manifest(project_id, manifest_id, revision, authorize=lambda: self._authorize(write=True))

    @admitted
    async def context_task_reference(self, project_id, task_id, revision):
        from pydantic import TypeAdapter
        from pyharness.application.context_manifest import ProductTaskId
        self._authorize(write=True)
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        task_id = TypeAdapter(ProductTaskId).validate_python(task_id, strict=True)
        if type(revision) is not int or not 1 <= revision <= 200:
            raise_code('EVT-100', reason='context_revision_invalid')
        return await self.store.context_task_reference(project_id, task_id, revision, authorize=lambda: self._authorize(write=True))

    @admitted
    async def preview_git_source(self, project_id, grant_id, body):
        from pydantic import TypeAdapter
        from pyharness.application.source_grants import GrantId
        from pyharness.application.workspace_git import GitPreviewCommand
        self._authorize(write=True)
        project_id = TypeAdapter(ProjectId).validate_python(project_id, strict=True)
        grant_id = TypeAdapter(GrantId).validate_python(grant_id, strict=True)
        return await self.store.preview_git_source(project_id, grant_id, GitPreviewCommand.model_validate(body),
                                                   authorize=lambda:self._authorize(write=True))

    @staticmethod
    def _workspace_id(value,model):
        from pydantic import TypeAdapter
        return TypeAdapter(model).validate_python(value,strict=True)

    @admitted
    async def preview_workspace_snapshot(self,project_id,grant_id,body):
        from pyharness.application.source_grants import GrantId
        from pyharness.application.workspace_snapshot import WorkspaceSelectionCommand
        self._authorize(write=True)
        return await self.store.preview_workspace_snapshot(self._workspace_id(project_id,ProjectId),self._workspace_id(grant_id,GrantId),
            WorkspaceSelectionCommand.model_validate(body),authorize=lambda:self._authorize(write=True))

    @admitted
    async def preview_git_candidates(self,project_id,grant_id,body):
        from pyharness.application.source_grants import GrantId
        from pyharness.application.workspace_git_inventory import GitInventoryCommand
        self._authorize(write=True)
        return await self.store.preview_git_candidates(self._workspace_id(project_id,ProjectId),self._workspace_id(grant_id,GrantId),
            GitInventoryCommand.model_validate(body),authorize=lambda:self._authorize(write=True))

    @admitted
    async def capture_workspace_snapshot(self,project_id,grant_id,body,command_id):
        from pyharness.application.source_grants import GrantId
        from pyharness.application.workspace_snapshot import CaptureWorkspaceSnapshot
        self._authorize(write=True)
        return await self.store.capture_workspace_snapshot(self._workspace_id(project_id,ProjectId),self._workspace_id(grant_id,GrantId),
            CaptureWorkspaceSnapshot.model_validate(body),command_id=command_id,authorize=lambda:self._authorize(write=True))

    @admitted
    async def workspace_snapshots(self,project_id,query=None):
        from pyharness.application.workspace_input_pages import WorkspaceInputQuery
        self._authorize(write=True)
        return await self.store.workspace_snapshots(self._workspace_id(project_id,ProjectId),query=query or WorkspaceInputQuery(),authorize=lambda:self._authorize(write=True))

    @admitted
    async def workspace_snapshot(self,project_id,snapshot_id,*,item_index=None,layer=None):
        from pyharness.application.workspace_snapshot import WorkspaceSnapshotId
        self._authorize(write=True)
        if item_index is not None and (type(item_index) is not int or not 0<=item_index<=199 or layer not in {'head','index','working'}):
            raise_code('EVT-100',reason='workspace_file_index_invalid')
        return await self.store.workspace_snapshot(self._workspace_id(project_id,ProjectId),self._workspace_id(snapshot_id,WorkspaceSnapshotId),
            item_index=item_index,layer=layer,authorize=lambda:self._authorize(write=True))

    @admitted
    async def workspace_manifests(self,project_id,body=None,command_id=None,*,query=None):
        from pyharness.application.workspace_manifest import WorkspaceManifestCommand
        from pyharness.application.workspace_input_pages import WorkspaceInputQuery
        self._authorize(write=True)
        project=self._workspace_id(project_id,ProjectId);authorize=lambda:self._authorize(write=True)
        if body is not None:
            return await self.store.save_workspace_manifest(project,WorkspaceManifestCommand.model_validate(body),command_id=command_id,authorize=authorize)
        return await self.store.workspace_manifests(project,query=query or WorkspaceInputQuery(),authorize=authorize)

    @admitted
    async def workspace_manifest(self,project_id,manifest_id,revision,*,item_index=None):
        from pyharness.application.context_manifest import ManifestId
        self._authorize(write=True)
        if type(revision) is not int or not 1<=revision<=200 or (item_index is not None and (type(item_index) is not int or not 0<=item_index<=199)):
            raise_code('EVT-100',reason='workspace_context_revision_invalid')
        return await self.store.workspace_manifest(self._workspace_id(project_id,ProjectId),self._workspace_id(manifest_id,ManifestId),revision,
            item_index=item_index,authorize=lambda:self._authorize(write=True))
