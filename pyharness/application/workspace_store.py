"""Tenant-private control SessionLog and immutable product-definition blobs.

Only committed events confer authority. No engine, queue, model or host-root access.
A failed append poisons this owner until close/reopen; ambiguous commits are never
reported as accepted using SessionLog's in-memory cache.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from pathlib import Path

from pyharness.bus import EventBus
from pyharness.core.session import open_session
from pyharness.core.tenant_settings import TenantSettingsStore, normalize_tenant_id
from pyharness.errors import raise_code
from pyharness.events import EVENT_TYPES
from pyharness.events.payload import WorkspaceResourceCommittedPayload
from pyharness.persistence import _FileLock, detect_truncation, open_store, session_data_paths, session_recorder
from pyharness.application.workspace_contracts import ProjectRevision
from pyharness.application.workspace_audit import AuditQuery, page_events
from pyharness.application.source_grants import SourceGrant
from pyharness.application.source_grant_store import SourceGrantStore
from pyharness.application.source_snapshot import SourceSnapshot,checked_private_bytes
from pyharness.application.snapshot_store import SnapshotStore
from pyharness.application.context_manifest import ContextManifest, TaskContextReference
from pyharness.application.context_manifest_store import ContextManifestStore
from pyharness.application.workspace_git_store import GitPreviewStore
from pyharness.application.workspace_snapshot import WorkspaceSnapshot
from pyharness.application.workspace_snapshot_store import WorkspaceSnapshotStore
from pyharness.application.workspace_manifest import ContextManifestV2
from pyharness.application.workspace_manifest_store import WorkspaceManifestStore
from pyharness.application.workspace_inventory_store import WorkspaceInventoryStore
from pyharness.application.workspace_owned_inputs import WorkspaceInput
from pyharness.application.workspace_owned_input_store import WorkspaceOwnedInputStore
from pyharness.application.product_task_contracts import ProductTask
from pyharness.application.product_task_store import ProductTaskStore
from pyharness.application.document_contracts import DocumentRevision, CitationRevision, DeliveryRevision
from pyharness.application.document_store import DocumentStore
from pyharness.application.document_references import DocumentReferences
from pyharness.application.run_attempt_contracts import RunAttempt
from pyharness.application.run_attempt_store import RunAttemptStore
from pyharness.application.execution_contracts import ExecutionAuthorization,ExecutionCancellation
from pyharness.application.execution_authorization_store import ExecutionAuthorizationStore
from pyharness.application.execution_preparation import ExecutionPreparationStore
from pyharness.application.execution_cancellation_store import ExecutionCancellationStore
from pyharness.application.correction_contracts import CorrectionRevision
from pyharness.application.execution_correction_store import ExecutionCorrectionStore
from pyharness.application.index_repair_contracts import IndexRepairRevision
from pyharness.application.execution_index_repair_store import ExecutionIndexRepairStore
from pyharness.application.execution_continuation_store import ExecutionContinuationStore
from pyharness.application.delivery_store import DeliveryStore
from pyharness.application.writeback_store import WritebackStore
from pyharness.application.writeback_records import WRITEBACK_MODELS
from pyharness.application.writeback_support import wb_id
from pyharness.application.delivery_records import DELIVERY_MODELS,delivery_record_id
from pyharness.application.handoff_budget_contracts import HandoffBudget
from pyharness.application.task_graph_store import TaskGraphStore
from pyharness.application.task_graph_contracts import TaskGraphRevision
from pyharness.application.task_graph_run_contracts import TaskGraphRunAuthorization,GraphDispatch,GraphCancellation
from pyharness.application.regression_records import REGRESSION_MODELS
from pyharness.application.regression_store import RegressionStore
from pyharness.application.experiment_records import EXPERIMENT_MODELS
from pyharness.application.experiment_store import ExperimentStore


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkspaceStore(SourceGrantStore, SnapshotStore, ContextManifestStore, GitPreviewStore, WorkspaceSnapshotStore, WorkspaceManifestStore, WorkspaceInventoryStore, WorkspaceOwnedInputStore, ProductTaskStore, DocumentStore, DocumentReferences, RunAttemptStore, ExecutionAuthorizationStore, ExecutionPreparationStore,ExecutionCancellationStore,ExecutionCorrectionStore,ExecutionIndexRepairStore,ExecutionContinuationStore,TaskGraphStore,DeliveryStore,WritebackStore,RegressionStore,ExperimentStore):
    SID = 's-workspace-control-v1'
    MAX_BLOB_BYTES = 32768
    MAX_PROJECTS = 200

    def __init__(self, root: Path, *, tenant_id: str):
        self.root = Path(root)
        self.tenant_id = normalize_tenant_id(tenant_id)
        self.log_path = self.root / 'control' / f'{self.SID}.jsonl'
        self._lock = asyncio.Lock()
        self._store = None
        self._session = None
        self._lease = None
        self._closed = False
        self._failed = False

    def _ensure_usable(self):
        if self._closed:
            raise_code('EVT-104', reason='workspace_closed')
        if self._failed:
            raise_code('PERS-202', reason='workspace_reopen_required')

    async def _open(self, *, initialize=True):
        return await self._open_projected(initialize=initialize,project=self._validate_log)

    async def _open_projected(self, *, initialize, project):
        self._ensure_usable()
        if self._store is not None:
            return
        if not initialize:
            try:
                exists = self.log_path.stat().st_size > 0
            except FileNotFoundError:
                exists = False
            except OSError:
                raise_code('PERS-202', reason='workspace_log_unavailable')
            if not exists:
                raise_code('GRD-403', reason='source_grant_unavailable')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lease = _FileLock(self.root / '.control-owner.lock')
        if not lease.try_acquire():
            raise_code('PERS-202', reason='workspace_busy')
        store = None
        try:
            store = open_store(self.SID, dir=self.log_path.parent)
            for path in session_data_paths(self.log_path):
                if detect_truncation(path) is not None:
                    raise_code('PERS-202', reason='workspace_log_incomplete')
            events = list(store.replay())
            if store._quarantine:
                raise_code('PERS-202', reason='workspace_log_corrupt')
            self._validate_control_sequence(events)
            project(events)
            if not events and not initialize:
                raise_code('GRD-403', reason='source_grant_unavailable')
            session = await open_session(self.SID, store, tenant_id=self.tenant_id)
            bus = EventBus()
            recorder = session_recorder(store, self.SID)
            for type_ in EVENT_TYPES:
                bus.subscribe(type_, recorder, owner='workspace-control')
            session._bus = bus
            if not events:
                event = await session.append('session.created', {'title': 'workspace-control', 'model': 'control-only'}, actor='system', sync=True)
                self._verify_commit(store, event)
            self._store, self._session, self._lease = store, session, lease
        except BaseException:
            if store is not None:
                store.close()
            lease.release()
            raise

    def _validate_log(self, events):
        self._validate_control_sequence(events)
        self._project(events)

    def _validate_control_sequence(self, events):
        for seq, event in enumerate(events, 1):
            if event.seq != seq or event.session_id != self.SID or event.tenant_id != self.tenant_id:
                raise_code('PERS-202', reason='workspace_log_identity_or_sequence')
            if event.type != ('session.created' if seq == 1 else 'workspace.resource_committed'):
                raise_code('PERS-202', reason='workspace_log_type')

    @staticmethod
    def _verify_commit(store, event):
        # EventBus isolates subscriber errors; an empty flush is not a receipt.
        rows = list(store.replay())
        if store._quarantine or not rows or rows[-1].model_dump() != event.model_dump():
            raise_code('PERS-202', reason='workspace_commit_unconfirmed')

    def _blob(self, digest: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise_code('EVT-100', field='blob_ref')
        path = self.root / 'blobs' / f'{digest}.json'
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise_code('PERS-202', reason='workspace_blob_escape')
        return path

    def _read_definition(self, payload):
        path = self._blob(payload['digest'])
        try:
            limit = 512 * 1024 if payload['resource_kind'] in {'source_snapshot', 'context_manifest', 'workspace_snapshot', 'workspace_input', 'document', 'delivery_definition','index_repair','task_graph','task_graph_run','experiment_report','delivery_package','role_handoff','delivery_acceptance','delivery_export','writeback_grant','merge_plan','writeback_application'} else self.MAX_BLOB_BYTES
            if path.stat().st_size > limit:
                raise_code('PERS-202', reason='workspace_blob_limit')
            data = checked_private_bytes(path,limit=limit)
        except OSError:
            raise_code('PERS-202', reason='workspace_blob_missing')
        if digest_of(data) != payload['digest']:
            raise_code('PERS-202', reason='workspace_blob_digest')
        try:
            model = {'project': ProjectRevision, 'source_grant': SourceGrant,
                     'source_snapshot': SourceSnapshot, 'context_manifest': ContextManifest,
                     'task_definition': TaskContextReference,'workspace_snapshot':WorkspaceSnapshot,'workspace_input':WorkspaceInput,
                     'document':DocumentRevision,'citation':CitationRevision,'delivery_definition':DeliveryRevision,
                     'run_attempt':RunAttempt,'execution_authorization':ExecutionAuthorization,
                     'execution_cancellation':ExecutionCancellation,'execution_correction':CorrectionRevision,'index_repair':IndexRepairRevision,
                     'task_graph':TaskGraphRevision,'task_graph_run':TaskGraphRunAuthorization,'task_graph_dispatch':GraphDispatch,'task_graph_cancellation':GraphCancellation}.get(payload['resource_kind'])
            if payload['resource_kind']=='context_manifest' and json.loads(data).get('schema_version')==2:
                model=ContextManifestV2
            if payload['resource_kind']=='document' and json.loads(data).get('schema_version')==2:
                from pyharness.application.handoff_document_contracts import HandoffDocumentRevision
                model=HandoffDocumentRevision
            if payload['resource_kind']=='workspace_input' and json.loads(data).get('schema_version')==2:
                from pyharness.application.selected_owned_inputs import SelectedWorkspaceInput
                model=SelectedWorkspaceInput
            if payload['resource_kind'] == 'task_definition' and json.loads(data).get('schema_version') == 2:
                model = ProductTask
            if payload['resource_kind'] in ('run_attempt','execution_authorization') and json.loads(data).get('schema_version')==2:
                from pyharness.application.continuation_execution_contracts import ContinuedRunAttempt,ContinuedExecutionAuthorization
                model=ContinuedRunAttempt if payload['resource_kind']=='run_attempt' else ContinuedExecutionAuthorization
            if payload['resource_kind'] in ('run_attempt','execution_authorization') and json.loads(data).get('schema_version')==3:
                from pyharness.application.task_graph_execution_contracts import GraphRunAttempt,GraphNodeExecutionAuthorization
                model=GraphRunAttempt if payload['resource_kind']=='run_attempt' else GraphNodeExecutionAuthorization
            if payload['resource_kind'] in DELIVERY_MODELS:
                model=DELIVERY_MODELS[payload['resource_kind']][0]
            if payload['resource_kind']=='handoff_budget':model=HandoffBudget
            if payload['resource_kind'] in WRITEBACK_MODELS:model=WRITEBACK_MODELS[payload['resource_kind']][0]
            if payload['resource_kind'] in REGRESSION_MODELS:model=REGRESSION_MODELS[payload['resource_kind']][0]
            if payload['resource_kind'] in EXPERIMENT_MODELS:model=EXPERIMENT_MODELS[payload['resource_kind']][0]
            if model is None:
                raise ValueError('unsupported resource')
            project = model.model_validate_json(data)
        except ValueError:
            raise_code('PERS-202', reason='workspace_blob_schema')
        ident = getattr(project,EXPERIMENT_MODELS[payload['resource_kind']][1]) if payload['resource_kind'] in EXPERIMENT_MODELS else getattr(project,REGRESSION_MODELS[payload['resource_kind']][2]) if payload['resource_kind'] in REGRESSION_MODELS else wb_id(project,payload['resource_kind']) if payload['resource_kind'] in WRITEBACK_MODELS else project.budget_id if payload['resource_kind']=='handoff_budget' else delivery_record_id(project,payload['resource_kind']) if payload['resource_kind'] in DELIVERY_MODELS else getattr(project, {'project': 'project_id', 'source_grant': 'grant_id', 'source_snapshot': 'snapshot_id',
                                  'context_manifest': 'manifest_id', 'task_definition': 'product_task_id',
                                  'workspace_snapshot':'snapshot_id','workspace_input':'input_id',
                                  'document':'document_id','citation':'citation_id','delivery_definition':'delivery_id',
                                  'run_attempt':'attempt_id','execution_authorization':'execution_id',
                                  'execution_cancellation':'cancellation_id','execution_correction':'correction_id','index_repair':'repair_id',
                                  'task_graph':'graph_id','task_graph_run':'graph_run_id','task_graph_dispatch':'dispatch_id','task_graph_cancellation':'cancellation_id'}[payload['resource_kind']])
        if ident != payload['resource_id'] or project.revision != payload['revision']:
            raise_code('PERS-202', reason='workspace_blob_reference')
        if isinstance(project, SourceSnapshot):
            self._verify_snapshot_content(project)
        if isinstance(project,WorkspaceSnapshot):
            self._verify_workspace_content(project)
        return project

    def _project(self, events):
        projects, commands, regression_history = {}, {}, {}
        for event in events:
            if event.type != 'workspace.resource_committed':
                continue
            payload = event.payload
            project = self._read_definition(payload)
            kind = payload['resource_kind'] if payload['resource_kind'] in DELIVERY_MODELS or payload['resource_kind'] in WRITEBACK_MODELS or payload['resource_kind'] in REGRESSION_MODELS or payload['resource_kind'] in EXPERIMENT_MODELS or payload['resource_kind']=='handoff_budget' else {'project': 'project', 'source_grant': 'grant', 'source_snapshot': 'snapshot',
                    'context_manifest': 'manifest', 'task_definition': 'task_reference','workspace_snapshot':'workspace_snapshot','workspace_input':'workspace_input',
                    'document':'document','citation':'citation','delivery_definition':'delivery_definition',
                    'run_attempt':'run_attempt','execution_authorization':'execution_authorization',
                    'execution_cancellation':'execution_cancellation','execution_correction':'execution_correction','index_repair':'index_repair',
                    'task_graph':'task_graph','task_graph_run':'task_graph_run','task_graph_dispatch':'task_graph_dispatch','task_graph_cancellation':'task_graph_cancellation'}[payload['resource_kind']]
            if isinstance(project, ProductTask):
                kind = 'product_task'
            ident = payload['resource_id']
            previous = projects.get(ident)
            if previous and kind not in previous:
                raise_code('PERS-202', reason='workspace_resource_kind_changed')
            revision = previous[kind]['revision'] if previous else 0
            key = (payload['actor_ref'], payload['command_id'])
            if payload['parent_revision'] != revision or key in commands:
                raise_code('PERS-202', reason='workspace_projection_conflict')
            if kind in DELIVERY_MODELS:
                self._validate_delivery_record(project,projects,previous,event,kind)
            if kind=='handoff_budget':self._validate_handoff_budget(project,projects,previous,event)
            if kind in WRITEBACK_MODELS:self._validate_writeback_record(project,projects,previous,event,kind)
            if kind in REGRESSION_MODELS:self._validate_regression_record(project,projects,previous,event,kind,regression_history)
            if kind in EXPERIMENT_MODELS:self._validate_experiment_record(project,projects,previous,event,kind,regression_history)
            if kind in {'manifest', 'task_reference'}:
                if isinstance(project,ContextManifestV2):
                    self._validate_workspace_manifest_definition(project,projects,previous,event)
                else:
                    self._validate_context_definition(project, projects, previous, event)
            if kind == 'product_task':
                self._validate_product_task_definition(project, projects, previous, event)
            if kind == 'document':
                self._validate_document_definition(project, projects, previous, event)
            if kind == 'citation':
                self._validate_citation_definition(project, projects, previous, event)
            if kind == 'delivery_definition':
                self._validate_delivery_definition(project, projects, previous, event)
            if kind == 'run_attempt':
                self._validate_run_attempt_definition(project, projects, previous, event)
            if kind == 'execution_authorization':
                self._validate_execution_authorization(project, projects, previous, event)
            if kind == 'execution_cancellation':
                self._validate_execution_cancellation(project,projects,previous,event)
            if kind=='execution_correction':
                self._validate_execution_correction(project,projects,previous,event)
            if kind=='index_repair':
                self._validate_index_repair(project,projects,previous,event)
            if kind in {'task_graph_dispatch','task_graph_cancellation'}:
                self._validate_graph_control(project,projects,previous,event,kind)
            if kind=='task_graph_run':
                self._validate_task_graph_run(project,projects,previous,event)
            if kind=='task_graph':
                self._validate_task_graph(project,projects,previous,event)
            if kind=='workspace_snapshot':
                self._validate_workspace_snapshot_definition(project,projects,previous,event)
            if kind=='workspace_input':
                self._validate_workspace_input_definition(project,projects,previous,event)
            if kind == 'snapshot':
                grant = projects.get(project.grant_id, {}).get('grant')
                parent = projects.get(project.project_id, {}).get('project')
                if (previous is not None or grant is None or parent is None or parent['archived']
                        or grant['revoked'] or grant['project_id'] != project.project_id
                        or grant['root_identity'] != project.root_identity
                        or grant['revision'] != project.grant_revision):
                    raise_code('PERS-202', reason='workspace_snapshot_binding')
            if kind == 'grant':
                parent = projects.get(project.project_id, {}).get('project')
                current = project.model_dump()
                if parent is None or (previous is None and (parent['archived'] or project.revoked)):
                    raise_code('PERS-202', reason='workspace_grant_project')
                if previous is None:
                    if project.approved_by != payload['actor_ref']:
                        raise_code('PERS-202', reason='workspace_grant_subject')
                elif (previous['grant']['revoked'] or not project.revoked
                      or any(current[k] != v for k, v in previous['grant'].items()
                             if k not in {'revision', 'revoked'})):
                    raise_code('PERS-202', reason='workspace_grant_transition')
            row = {kind: project.model_dump(), 'digest': payload['digest'],
                   'source_seq': event.seq, 'created_at': event.ts, 'created_by': payload['actor_ref']}
            if kind in DELIVERY_MODELS or kind in WRITEBACK_MODELS or kind in REGRESSION_MODELS or kind in EXPERIMENT_MODELS or kind in {'handoff_budget','run_attempt','execution_authorization','execution_cancellation','execution_correction','index_repair','task_graph','task_graph_run','task_graph_dispatch','task_graph_cancellation'}:row['request_digest'] = payload['request_digest']
            projects[ident] = row
            if kind in REGRESSION_MODELS or kind in EXPERIMENT_MODELS or kind in {'product_task','task_graph'}:regression_history[(kind,ident,project.revision)]=row
            commands[key] = (payload['request_digest'], row)
        return projects, commands

    async def projects(self):
        async with self._lock:
            await self._open()
            projects, _ = self._project(self._session.events_after(0))
            return [row for row in projects.values() if 'project' in row]

    async def projects_readonly(self):
        """Directory used by observation UI; no initialization or writer lease."""
        async with self._lock:
            self._ensure_usable();found=False
            for path in session_data_paths(self.log_path):
                try:path.stat();found=True
                except FileNotFoundError:continue
                except OSError:raise_code('PERS-202',reason='workspace_log_unavailable')
            if not found:return []
            resources,_,_=await self._execution_read_state()
            return [row for row in resources.values() if 'project' in row]

    async def audit(self):
        async with self._lock:
            await self._open()
            return [event.model_dump(mode='json') for event in self._session.events_after(0)]

    async def audit_page(self, *, subject: str, **parameters):
        query = AuditQuery.model_validate(parameters)
        async with self._lock:
            await self._open()
            # Read disk, not the SessionLog cache: corruption remains fail closed.
            for path in session_data_paths(self.log_path):
                if detect_truncation(path) is not None:
                    raise_code('PERS-202', reason='workspace_log_incomplete')
            events = list(self._store.replay())
            if self._store._quarantine:
                raise_code('PERS-202', reason='workspace_log_corrupt')
            self._validate_log(events)
            key_path = self.root / '.audit-cursor-key'
            try:
                if not key_path.exists():
                    if query.cursor:
                        raise_code('EVT-100', reason='audit_cursor_invalid')
                    TenantSettingsStore._atomic_write(key_path, secrets.token_bytes(32))
                key = key_path.read_bytes()
                if len(key) != 32:
                    raise_code('PERS-202', reason='audit_key_unavailable')
            except OSError:
                raise_code('PERS-202', reason='audit_key_unavailable')
            return page_events([e.model_dump(mode='json') for e in events], query,
                               tenant=self.tenant_id, subject=subject, key=key)

    async def commit(self, project: ProjectRevision, *, expected_revision: int, command_id: str, actor_ref: str):
        async with self._lock:
            await self._open()
            data = canonical(project.model_dump())
            digest = digest_of(data)
            request_digest = digest_of(canonical({'project': project.model_dump(), 'expected_revision': expected_revision}))
            payload = WorkspaceResourceCommittedPayload(
                resource_kind='project', resource_id=project.project_id,
                revision=project.revision, parent_revision=expected_revision,
                blob_ref=digest, digest=digest, command_id=command_id,
                request_digest=request_digest, actor_ref=actor_ref).model_dump()
            projects, commands = self._project(self._session.events_after(0))
            old_command = commands.get((actor_ref, command_id))
            if old_command:
                if old_command[0] != request_digest:
                    raise_code('EVT-100', reason='idempotency_conflict')
                return old_command[1]
            previous = projects.get(project.project_id)
            actual = previous['project']['revision'] if previous else 0
            if actual != expected_revision:
                raise_code('EVT-101', reason='stale_revision')
            if previous is None and sum('project' in row for row in projects.values()) >= self.MAX_PROJECTS:
                raise_code('EVT-100', reason='workspace_project_limit')
            path = self._blob(digest)
            if len(data) > self.MAX_BLOB_BYTES:
                raise_code('EVT-100', reason='workspace_blob_limit')
            try:
                if path.exists():
                    if checked_private_bytes(path,limit=self.MAX_BLOB_BYTES) != data:
                        raise_code('PERS-202', reason='workspace_blob_digest')
                else:
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    TenantSettingsStore._atomic_write(path, data)
            except OSError:
                raise_code('PERS-202', reason='workspace_blob_write')
            try:
                event = await self._session.append('workspace.resource_committed', payload, actor='user', sync=True)
                self._verify_commit(self._store, event)
            except BaseException:
                self._failed = True
                raise
            projects, _ = self._project(self._session.events_after(0))
            return projects[project.project_id]

    async def close(self):
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._store is not None:
                    self._store.close()
            finally:
                if self._lease is not None:
                    self._lease.release()
