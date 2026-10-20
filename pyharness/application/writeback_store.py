"""Explicit target grants and immutable merge plans on the original control owner."""
import time
from datetime import datetime,timezone
from pathlib import Path
from pyharness.application.writeback_support import WritebackSupport,wb_request,require_write_owner,merge_selection
from pyharness.application.writeback_previews import WritebackPreviews,preview_diff
from pyharness.application.writeback_application_store import WritebackApplications
from pyharness.application.writeback_records import WriteGrant,MergePlan,MergeExport,WRITEBACK_MODELS
from pyharness.application.source_grants import authorize_root,parse_expiry
from pyharness.core.sandbox import safe_path
from pyharness.errors import raise_code


class WritebackStore(WritebackSupport,WritebackPreviews,WritebackApplications):
    async def writeback_source_options(self,project,ident,*,authorize,service):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.delivery_contracts import DeliveryPackageCommand
        from pyharness.application.delivery_support import delivery_ref,selected_outputs
        from pyharness.application.product_execution_sessions import read_execution_events
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._context_owned(resources,ident,'delivery_package',project,actor)
            self._delivery_package_gate(resources,row,actor,authorize,service)
            execution,attempt,_=self._delivery_package_basis(resources,project,DeliveryPackageCommand.model_validate(row['delivery_package']['selection']),actor)
            task=self._s05_exact(resources,project,'product_task',attempt['prerequisites']['task_ref'],'product_task_id',actor)['product_task']
            wi=self._task_bound_input(resources,task,actor)['workspace_input'];paths={f['path'] for f in wi['files']}
            events=read_execution_events(service._tenant_sessions_dir(),execution,service.tenant_id);last={}
            for event in events:
                if event.type=='execution.effect' and event.payload['phase']=='returned' and event.payload.get('file_observation')=='observed':
                    for change in event.payload.get('file_changes') or []:last[change['path']]=(event,change)
            deletions=[dict(session_id=execution['execution_authorization']['session_id'],source_seq=e.seq,
                record_digest=digest_of(canonical(e.payload)),path=name) for name,(e,c) in last.items() if name in paths and c['kind']=='deleted']
            fresh,_,_=self._delivery_read_snapshot();self._delivery_package_gate(fresh,row,actor,authorize,service)
            return dict(schema_version=1,project_id=project,package_ref=delivery_ref(row,'delivery_package'),
                baseline_files=[dict(path=f['path'],sha256=f['content_digest'],size_bytes=f['size_bytes']) for f in wi['files']],
                outputs=selected_outputs(row['delivery_package']),deletions=deletions,execution_authorized=False)

    async def issue_writeback_grant(self,project,command,*,command_id,authorize,service):
        from pyharness.application.delivery_identity import recipient_id
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();require_write_owner(service,actor);await self._open(initialize=False)
            resources,commands=self._grant_state();request=wb_request(project,'grant',command.model_dump())
            self._context_envelope('writeback_grant',command.grant_id,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                return self._wb_commit_summary(old[1],'writeback_grant',actor,authorize)
            geometry=authorize_root(command.root_path);root=Path(geometry['canonical_root']);private=service._storage_root().resolve()
            if root.is_relative_to(private) or private.is_relative_to(root):raise_code('GRD-403',reason='writeback_private_storage_overlap')
            for name in command.paths:safe_path(root,name)
            value=WriteGrant(project_id=project,project_revision=command.project_revision,created_by=actor,
                submission_key=command_id,revision=1,selection=command,recipient_id=recipient_id(service,command.recipient_username),**geometry)
            def gate():
                fresh,_=self._grant_state();self._context_project(fresh,project,command.project_revision)
                self._context_quota(fresh,'writeback_grant',project);require_write_owner(service,actor)
                if authorize()!=actor:raise_code('GRD-403',reason='writeback_subject_changed')
                if command.grant_id in fresh:raise_code('EVT-101',reason='writeback_grant_exists')
                if recipient_id(service,command.recipient_username)!=value.recipient_id or authorize_root(command.root_path)!=geometry:
                    raise_code('EVT-101',reason='writeback_grant_target_changed')
                if parse_expiry(command.expires_at)<=datetime.now(timezone.utc):raise_code('GRD-403',reason='writeback_grant_expired')
            gate();_,row=await self._append_context(value,'writeback_grant',command.grant_id,command_id,actor,request,gate)
            return self._wb_commit_summary(row,'writeback_grant',actor,authorize)

    async def revoke_writeback_grant(self,project,ident,command,*,command_id,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();require_write_owner(service,actor);await self._open(initialize=False)
            resources,commands=self._grant_state();request=wb_request(project,'revoke',dict(command.model_dump(),grant_id=ident))
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                return self._wb_commit_summary(old[1],'writeback_grant',actor,authorize)
            old=self._context_owned(resources,ident,'writeback_grant',project,actor)['writeback_grant']
            if old['revoked'] or old['revision']!=command.expected_revision:raise_code('EVT-101',reason='writeback_grant_revision')
            value=WriteGrant.model_validate(dict(old,revision=2,revoked=True,submission_key=command_id))
            def gate():
                fresh,_=self._grant_state();self._context_project(fresh,project)
                if authorize()!=actor:raise_code('GRD-403',reason='writeback_subject_changed')
                require_write_owner(service,actor)
                if self._context_owned(fresh,ident,'writeback_grant',project,actor)['writeback_grant']!=old:
                    raise_code('EVT-101',reason='writeback_grant_revision')
            gate();_,row=await self._append_context(value,'writeback_grant',ident,command_id,actor,request,gate)
            return self._wb_commit_summary(row,'writeback_grant',actor,authorize)

    async def preview_writeback(self,project,command,*,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            return self._wb_collect(resources,project,command,actor,authorize,service,started)[0]

    async def save_merge_plan(self,project,command,*,command_id,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=wb_request(project,'plan',command.model_dump());ident=command.plan_id
            self._context_envelope('merge_plan',ident,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                return self._wb_commit_summary(old[1],'merge_plan',actor,authorize,
                    gate=lambda:self._wb_current(self._delivery_read_snapshot()[0],project,merge_selection(command),actor,authorize,service))
            selected=merge_selection(command);preview,files,blobs=self._wb_collect(resources,project,selected,actor,authorize,service,started)
            if preview['status']!='mergeable':raise_code('EVT-101',reason='writeback_conflict_or_validation_failed')
            if preview['confirmation_digest']!=command.confirmation_digest:raise_code('EVT-101',reason='writeback_preview_changed')
            value=MergePlan(project_id=project,project_revision=command.project_revision,created_by=actor,submission_key=command_id,
                selection=command,files=files,basis_digest=preview['confirmation_digest'])
            def current():
                fresh,_=self._grant_state();self._wb_current(fresh,project,selected,actor,authorize,service)
            for digest,raw in blobs.items():current();self._write_snapshot_blob(self._snapshot_content(digest),raw)
            def gate():
                fresh,_=self._grant_state();self._context_quota(fresh,'merge_plan',project)
                if ident in fresh:raise_code('EVT-101',reason='merge_plan_exists')
                new,_,_=self._wb_collect(fresh,project,selected,actor,authorize,service,started)
                if new['status']!='mergeable' or new['confirmation_digest']!=command.confirmation_digest:
                    raise_code('EVT-101',reason='writeback_preview_changed')
                for digest in blobs:self._wb_blob(digest,current)
            gate();_,row=await self._append_context(value,'merge_plan',ident,command_id,actor,request,gate)
            return self._wb_commit_summary(row,'merge_plan',actor,authorize,
                gate=lambda:self._wb_current(self._delivery_read_snapshot()[0],project,selected,actor,authorize,service))

    async def authorize_merge_export(self,project,command,*,command_id,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=wb_request(project,'export',command.model_dump());ident=command.export_id
            self._context_envelope('merge_export',ident,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                row=old[1]
            else:
                plan=self._wb_exact(resources,project,'merge_plan',command.plan_ref.model_dump())['merge_plan']
                value=MergeExport(project_id=project,project_revision=plan['project_revision'],created_by=actor,submission_key=command_id,selection=command)
                def gate():
                    fresh,_=self._grant_state();self._context_quota(fresh,'merge_export',project)
                    if ident in fresh:raise_code('EVT-101',reason='merge_export_exists')
                    target=self._wb_exact(fresh,project,'merge_plan',command.plan_ref.model_dump())['merge_plan']
                    if target['created_by']!=actor:raise_code('GRD-403',reason='merge_plan_private')
                    self._wb_current(fresh,project,merge_selection(target['selection']),actor,authorize,service)
                gate();_,row=await self._append_context(value,'merge_export',ident,command_id,actor,request,gate)
            def receipt_gate():
                fresh,_=self._grant_state();target=self._wb_exact(fresh,project,'merge_plan',command.plan_ref.model_dump())['merge_plan']
                self._wb_current(fresh,project,merge_selection(target['selection']),actor,authorize,service)
            return self._wb_commit_summary(row,'merge_export',actor,authorize,gate=receipt_gate)

    async def merge_export_bytes(self,project,ident,index,*,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            value=self._context_owned(resources,ident,'merge_export',project,actor)['merge_export']
            plan=self._wb_exact(resources,project,'merge_plan',value['selection']['plan_ref'])['merge_plan']
            def gate():
                fresh,_,_=self._delivery_read_snapshot()
                self._context_owned(fresh,ident,'merge_export',project,actor)
                self._wb_current(fresh,project,merge_selection(plan['selection']),actor,authorize,service)
            gate()
            if type(index) is not int or not 0<=index<len(plan['files']):raise_code('EVT-100',reason='merge_export_index')
            item=plan['files'][index]
            if item['merged_digest'] is None:raise_code('EVT-100',reason='merge_export_is_deletion')
            raw=self._wb_blob(item['merged_digest'],gate);gate()
            return raw,Path(item['target_path']).name

    async def read_writeback(self,project,kind,ident,*,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();self._context_project(resources,project)
            row=resources.get(ident,{});value=row.get(kind)
            if not value or value['project_id']!=project or actor not in (value['created_by'],value.get('recipient_id')):
                raise_code('GRD-403',reason='writeback_resource_private')
            result=self._wb_summary(row,kind)
            if kind=='merge_plan':
                selected=merge_selection(value['selection'])
                def gate():
                    fresh,_,_=self._delivery_read_snapshot();self._wb_current(fresh,project,selected,actor,authorize,service)
                gate();files=[]
                for item in value['files']:
                    current=self._wb_blob(item['target']['sha256'],gate) if item['target'] else None
                    merged=self._wb_blob(item['merged_digest'],gate) if item['merged_digest'] else None
                    files.append(dict(item,diff=preview_diff(current,merged)))
                result['files']=files;gate()
            self._context_finish(authorize,actor,resources)
            return result

    async def writeback_center(self,project,*,authorize):
        from pyharness.application.workspace_store import canonical
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();self._context_project(resources,project)
            items={kind:[] for kind in WRITEBACK_MODELS}
            for row in resources.values():
                kind=next((k for k in WRITEBACK_MODELS if k in row),None)
                if kind is None:continue
                value=row[kind]
                if value['project_id']==project and actor in (value['created_by'],value.get('recipient_id')):
                    items[kind].append(dict(resource_id=value['selection'][WRITEBACK_MODELS[kind][1]],revision=value['revision'],digest=row['digest'],source_seq=row['source_seq']))
            self._context_finish(authorize,actor,resources);result=dict(schema_version=1,project_id=project,items=items,execution_authorized=False)
            if len(canonical(result))>self.MAX_CONTEXT_BYTES:raise_code('EVT-100',reason='writeback_inventory_limit')
            return result

    async def writeback_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state();self._context_project(resources,project)
            old=commands.get((actor,key));receipt=None
            if old:
                kind=next((k for k in WRITEBACK_MODELS if k in old[1]),None)
                if kind is None or old[1][kind]['project_id']!=project:raise_code('GRD-403',reason='writeback_submission_unavailable')
                row=old[1];value=row[kind];ident=value['selection'][WRITEBACK_MODELS[kind][1]]
                if kind=='writeback_application':row=resources[ident]
                receipt=dict(resource_kind=kind,resource_id=ident,revision=row[kind]['revision'],digest=row['digest'],source_seq=row['source_seq'],request_digest=old[0],persisted=True)
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if receipt else 'not_found',receipt=receipt,execution_authorized=False)
