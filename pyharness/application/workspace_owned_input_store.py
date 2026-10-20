"""Prepared input facts on the existing owner; working copies are disposable, never authority."""
import time,tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from pyharness.application.workspace_owned_inputs import WorkspaceInput,PrepareWorkspaceInput,derive_inputs
from pyharness.application.selected_owned_inputs import derive_selected_inputs
from pyharness.application.selected_input_support import SelectedInputSupport
from pyharness.application.workspace_materialization import copy_inputs,verify_inputs
from pyharness.core.sandbox import safe_path,WorkspaceInputPreparation
from pyharness.errors import PyHError,raise_code


class WorkspaceOwnedInputStore(SelectedInputSupport):
    def _input_manifest(self,resources,project_id,ref,actor,*,manifest_schema=2):
        row=self._context_owned(resources,ref['manifest_id'],'manifest',project_id,actor)
        if row['manifest']['schema_version']!=manifest_schema:raise_code('GRD-403',reason='workspace_input_manifest_unavailable')
        if any(row[k]!=ref[k] for k in ('digest','source_seq')) or row['manifest']['revision']!=ref['revision']:
            raise_code('EVT-101',reason='workspace_input_manifest_revision')
        return row

    def _validate_workspace_input_definition(self,definition,resources,previous,event):
        if definition.schema_version==2:return self._validate_selected_input_definition(definition,resources,previous,event)
        value=definition.model_dump()
        try:
            if previous is not None or value['created_by']!=event.payload['actor_ref']:raise ValueError('immutable prepared input owner')
            self._context_project(resources,value['project_id'],value['project_revision'])
            self._context_quota(resources,'workspace_input',value['project_id'])
            cm=self._input_manifest(resources,value['project_id'],value['manifest_ref'],value['created_by'])
            source=self._workspace_manifest_snapshot(resources,cm['manifest'],value['created_by'])
            if self._workspace_latest(resources,source['workspace_snapshot'])!=value['source_ref']['snapshot_id']:raise ValueError('superseded snapshot')
            if cm['manifest']['project_revision']!=value['project_revision']:raise ValueError('stale project')
            if value['task_ref']:self._context_task(resources,value['task_ref'],value['project_id'],value['project_revision'],value['created_by'])
            for ident in self._workspace_grant_ids(source['workspace_snapshot']):
                if resources[ident]['grant']['revoked']:raise ValueError('withdrawn source')
            command=PrepareWorkspaceInput.model_validate({k:value[k] for k in ('schema_version','input_id','project_revision','manifest_ref')})
            if derive_inputs(value['project_id'],command,cm,source,value['created_by']).model_dump()!=value:raise ValueError('derived input mismatch')
        except (PyHError,ValueError,KeyError):raise_code('PERS-202',reason='workspace_input_definition_binding')

    def _input_access(self,resources,value,actor):
        self._context_project(resources,value['project_id'])
        cm=self._context_owned(resources,value['manifest_ref']['manifest_id'],'manifest',value['project_id'],actor)
        selected=value['schema_version']==2
        if value['created_by']!=actor or cm['manifest']['schema_version']!=(1 if selected else 2):raise_code('GRD-403',reason='workspace_input_unavailable')
        if selected:source=self._selected_input_source(resources,value)
        else:
            source=self._workspace_manifest_snapshot(resources,value,actor)
            self._workspace_snapshot_access(resources,source,actor)
        if value['task_ref']:self._context_task(resources,value['task_ref'],value['project_id'],value['project_revision'],actor,exact=False)
        reasons=[]
        if resources[value['project_id']]['project']['revision']!=value['project_revision']:reasons.append('project_revision_changed')
        if cm['manifest']['revision']!=value['manifest_ref']['revision'] or cm['digest']!=value['manifest_ref']['digest'] or cm['source_seq']!=value['manifest_ref']['source_seq']:reasons.append('manifest_revision_superseded')
        if value['task_ref'] and resources[value['task_ref']['product_task_id']]['task_reference']['revision']!=value['task_ref']['revision']:reasons.append('task_revision_changed')
        if not selected and self._workspace_latest(resources,source['workspace_snapshot'])!=value['source_ref']['snapshot_id']:reasons.append('workspace_snapshot_superseded')
        return source,reasons

    @staticmethod
    def _input_grant_ids(value):
        ref=value['source_ref']
        return (ref['grant_id'],)+( (ref['metadata_grant_id'],) if ref.get('metadata_grant_id') else () )

    def _input_gate(self,resources,value,actor,authorize,started,*,current=True):
        if authorize()!=actor:raise_code('GRD-403',reason='workspace_input_subject_changed')
        source,reasons=self._input_access(resources,value,actor)
        if current and reasons:raise_code('EVT-101',reason='workspace_input_stale')
        ids=self._input_grant_ids(value)
        self._workspace_storage_geometry(resources[ids[0]],resources[ids[1]] if len(ids)>1 else None)
        self._context_finish(authorize,actor,resources,ids)
        if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='workspace_capture_time_limit')
        return reasons

    def _input_summary(self,resources,row,*,detail=True):
        from pyharness.application.workspace_store import canonical,digest_of
        value=row['workspace_input'];_,reasons=self._input_access(resources,value,value['created_by'])
        result={k:v for k,v in value.items() if k!='created_by'}
        result.update(status='stale' if reasons else 'input_prepared',stale_reasons=reasons,
            workspace_state='not_started',materialization_status='checked_at_commit',materialization_lifetime='per_explicit_use',
            persisted=True,executable=False,file_count=len(value['files']),deletion_count=len(value['deletions']),
            digest=row['digest'],source_seq=row['source_seq'],created_at=row['created_at'])
        if not detail:
            result.pop('files');result.pop('deletions')
        elif reasons:result.update(files=[],deletions=[])
        result['receipt_digest']=digest_of(canonical(result));return result

    def _read_input_blob(self,item,gate):
        from pyharness.application.source_snapshot import checked_private_bytes
        from pyharness.application.workspace_store import digest_of
        path=self._snapshot_content(item['content_digest']);gate()
        raw=checked_private_bytes(path,before_read=gate)
        if len(raw)!=item['size_bytes'] or digest_of(raw)!=item['content_digest']:
            raise_code('PERS-202',reason='workspace_input_content_digest')
        gate();return raw

    def _input_destination(self,root,resources,value):
        root=Path(root);safe_path(root,'owned-check');dest=root.resolve()
        for ident in self._input_grant_ids(value):
            if ident is None:continue
            source=Path(resources[ident]['grant']['canonical_root']).resolve(strict=True)
            if dest.is_relative_to(source) or source.is_relative_to(dest):raise_code('GRD-403',reason='workspace_input_source_overlap')
        for name in ('snapshot-content','blobs','control'):
            protected=(self.root/name).resolve()
            if dest.is_relative_to(protected) or protected.is_relative_to(dest):raise_code('GRD-403',reason='workspace_input_storage_overlap')

    async def prepare_workspace_input(self,project_id,command,*,command_id,authorize):
        from pyharness.application.workspace_store import canonical,digest_of
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=digest_of(canonical({'operation':'workspace_input.prepare','project_id':project_id,'command':command.model_dump()}))
            self._context_envelope('workspace_input',command.input_id,1,command_id,actor,request)
            old=commands.get((actor,command_id))
            if old:
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                result=self._input_summary(resources,old[1])
                self._input_gate(resources,old[1]['workspace_input'],actor,authorize,started,current=False)
                return result
            if command.input_id in resources:raise_code('EVT-101',reason='stale_revision')
            self._context_project(resources,project_id,command.project_revision)
            cm=self._input_manifest(resources,project_id,command.manifest_ref.model_dump(),actor,manifest_schema=1 if command.schema_version==2 else 2)
            if command.schema_version==2:
                self._manifest_access(resources,cm,actor)
                if self._manifest_summary(resources,cm)['status']!='current':raise_code('EVT-101',reason='workspace_input_stale')
                m=cm['manifest'];definition=derive_selected_inputs(project_id,command,cm,resources[m['snapshot_id']],resources[m['grant_id']],actor)
            else:
                source=self._workspace_manifest_access(resources,cm,actor)
                if self._workspace_manifest_summary(resources,cm)['status']!='current':raise_code('EVT-101',reason='workspace_input_stale')
                definition=derive_inputs(project_id,command,cm,source,actor)
            value=definition.model_dump()
            self._context_quota(resources,'workspace_input',project_id)
            if len(canonical(value))>self.MAX_CONTEXT_BYTES:raise_code('EVT-100',reason='workspace_input_manifest_limit')
            gate=lambda:self._input_gate(resources,value,actor,authorize,started)
            gate();base=safe_path(self.root,'input-staging');base.mkdir(mode=0o700,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='owned-',dir=base) as owned:
                target=Path(owned);target.resolve(strict=True).relative_to(base.resolve(strict=True))
                self._input_destination(target,resources,value)
                copy_inputs(target,value['files'],lambda item:self._read_input_blob(item,gate),gate)
                def commit_gate():
                    verify_inputs(target,value['files'],gate)
                    fresh,_=self._grant_state();self._input_gate(fresh,value,actor,authorize,started)
                    self._context_quota(fresh,'workspace_input',project_id)
                    if command.input_id in fresh:raise_code('EVT-101',reason='stale_revision')
                prospective={'workspace_input':value,'digest':digest_of(canonical(value)),'source_seq':sum(1 for _ in self._session.events_after(0))+1,'created_at':'2099-12-31T23:59:59.999999Z'}
                if len(canonical(self._input_summary(resources,prospective)))+1024>self.MAX_CONTEXT_BYTES:raise_code('EVT-100',reason='workspace_input_receipt_limit')
                commit_gate()
                resources,row=await self._append_context(definition,'workspace_input',command.input_id,command_id,actor,request,commit_gate)
            try:
                result=self._input_summary(resources,row)
                self._input_gate(resources,row['workspace_input'],actor,authorize,started,current=False)
            except PyHError:raise_code('PERS-202',reason='workspace_commit_receipt_unavailable')
            return result

    async def workspace_input(self,project_id,input_id,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,_=self._grant_state()
            row=self._context_owned(resources,input_id,'workspace_input',project_id,actor)
            result=self._input_summary(resources,row)
            self._input_gate(resources,row['workspace_input'],actor,authorize,started,current=False);return result

    async def workspace_inputs(self,project_id,query,*,authorize):
        from pyharness.application.workspace_input_pages import workspace_input_page
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,_=self._grant_state();self._context_project(resources,project_id)
            rows=[]
            for row in resources.values():
                value=row.get('workspace_input')
                if not value or value['project_id']!=project_id or value['created_by']!=actor:continue
                try:self._input_gate(resources,value,actor,authorize,started,current=False)
                except PyHError as exc:
                    if exc.code=='GRD-403':continue
                    raise
                rows.append(self._input_summary(resources,row,detail=False))
            result=workspace_input_page(rows,query,kind='inputs',project=project_id,actor=actor,watermark=sum(1 for _ in self._session.events_after(0)))
            for row in rows:self._input_gate(resources,resources[row['input_id']]['workspace_input'],actor,authorize,started,current=False)
            self._context_finish(authorize,actor,resources)
            if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='workspace_capture_time_limit')
            return result

    @asynccontextmanager
    async def workspace_input_transfer(self,project_id,input_id,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,_=self._grant_state()
            row=self._context_owned(resources,input_id,'workspace_input',project_id,actor);value=row['workspace_input'];active=True;bound=None;root_identity=None
            def gate():
                if not active:raise_code('GRD-403',reason='workspace_input_transfer_closed')
                self._input_gate(resources,value,actor,authorize,started)
            def verify():
                gate()
                if bound is not None:
                    safe_path(bound,'owned-check')
                    info=bound.stat()
                    if (info.st_dev,info.st_ino)!=root_identity:raise_code('PERS-202',reason='workspace_input_root_changed')
                    verify_inputs(bound,value['files'],gate)
                fresh,_=self._grant_state();self._input_gate(fresh,value,actor,authorize,started)
            def populate(target):
                nonlocal bound,root_identity
                if bound is not None:raise_code('GRD-403',reason='workspace_input_transfer_already_used')
                gate();self._input_destination(target,resources,value)
                bound=Path(target);info=bound.stat();root_identity=(info.st_dev,info.st_ino)
                copy_inputs(target,value['files'],lambda item:self._read_input_blob(item,gate),gate);verify()
            def destination(target):
                gate();self._input_destination(target,resources,value)
            verify()
            try:yield WorkspaceInputPreparation(row['digest'],populate,verify,destination)
            finally:active=False
