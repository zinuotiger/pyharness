"""Git input capture on the existing owner, immutable content and SessionLog protocol."""
import asyncio
import time
from pathlib import Path
from contextlib import asynccontextmanager
from pyharness.application.workspace_capture import collect_workspace_git
from pyharness.application.workspace_snapshot import WorkspaceSnapshot,change_flags
from pyharness.errors import PyHError,raise_code


class WorkspaceSnapshotStore:
    MAX_WORKSPACE_SNAPSHOTS=200
    MAX_WORKSPACE_CAPTURE_SECONDS=25

    @asynccontextmanager
    async def _workspace_capture_lock(self,started):
        remaining=self.MAX_WORKSPACE_CAPTURE_SECONDS-(time.monotonic()-started)
        if remaining<=0: raise_code('EVT-104',reason='workspace_capture_time_limit')
        try: await asyncio.wait_for(self._lock.acquire(),remaining)
        except TimeoutError: raise_code('EVT-104',reason='workspace_capture_time_limit')
        try: yield
        finally: self._lock.release()

    def _workspace_quota(self,resources,project_id):
        if sum(row.get('workspace_snapshot',{}).get('project_id')==project_id for row in resources.values())>=self.MAX_WORKSPACE_SNAPSHOTS:
            raise_code('EVT-100',reason='workspace_snapshot_history_limit')

    @staticmethod
    def _workspace_grant_ids(snapshot):
        o=snapshot['observation']
        return (o['grant_id'],)+((o['metadata_grant_id'],) if o['metadata_grant_id'] else ())

    def _workspace_snapshot_access(self,resources,row,actor):
        value=row['workspace_snapshot'];project=value['project_id']
        self._context_owned({value['snapshot_id']:row},value['snapshot_id'],'workspace_snapshot',project,actor)
        self._context_project(resources,project)
        o=value['observation']
        source=self._snapshot_grant(resources,project,o['grant_id'])
        if (source['grant']['revision']!=o['grant_revision'] or source['source_seq']!=o['source_seq']
                or source['grant']['root_identity']!=value['root_identity']):
            raise_code('GRD-403',reason='workspace_snapshot_source_unavailable')
        if o['metadata_grant_id']:
            meta=self._snapshot_grant(resources,project,o['metadata_grant_id'])
            if (meta['grant']['revision']!=o['metadata_grant_revision'] or meta['source_seq']!=value['metadata_grant_source_seq']
                    or meta['grant']['root_identity']!=value['metadata_root_identity']):
                raise_code('GRD-403',reason='workspace_snapshot_source_unavailable')

    @staticmethod
    def _workspace_latest(resources,value):
        matching=[row for row in resources.values() if 'workspace_snapshot' in row
            and row['workspace_snapshot']['project_id']==value['project_id']
            and row['workspace_snapshot']['created_by']==value['created_by']
            and row['workspace_snapshot']['observation']['grant_id']==value['observation']['grant_id']]
        return max(matching,key=lambda row:row['source_seq'])['workspace_snapshot']['snapshot_id']

    def _workspace_snapshot_summary(self,resources,row,*,detail=True):
        from pyharness.application.workspace_store import canonical,digest_of
        value=row['workspace_snapshot'];o=value['observation']
        reasons=[]
        if resources[value['project_id']]['project']['revision']!=value['project_revision']: reasons.append('project_revision_changed')
        if self._workspace_latest(resources,value)!=value['snapshot_id']: reasons.append('workspace_snapshot_superseded')
        public={k:v for k,v in value.items() if k not in {'created_by','observation','source_identity_digest'}}
        public.update({k:v for k,v in o.items() if k not in {'source_seq','status','scope','persisted','executable','items'}})
        public.update(items=[{**item,**change_flags(item)} for item in o['items']],
            grant_source_seq=o['source_seq'],source_seq=row['source_seq'],digest=row['digest'],created_at=row['created_at'],
            selected_untracked_paths=[item['path'] for item in o['items'] if item['selected_untracked']],
            total_size_bytes=sum(item['working']['size_bytes'] for item in o['items'] if item['working']),
            status='stale' if reasons else 'captured',stale_reasons=reasons,persisted=True,executable=False,live_source_freshness='not_asserted')
        if not detail:
            for field in ('items','tracked_paths','selected_untracked_paths'): public.pop(field)
        public['receipt_digest']=digest_of(canonical(public))
        return public

    def _verify_workspace_content(self,snapshot):
        from pyharness.application.workspace_store import digest_of
        from pyharness.application.source_snapshot import checked_private_bytes
        seen={}
        for item in snapshot.observation.items:
            for layer in (item.head,item.index,item.working):
                if layer is not None: seen[layer.digest]=layer.size_bytes
        for digest,size in seen.items():
            try:
                path=self._snapshot_content(digest)
                if path.stat().st_size!=size: raise_code('PERS-202',reason='workspace_content_size')
                raw=checked_private_bytes(path)
            except OSError: raise_code('PERS-202',reason='workspace_content_missing')
            except PyHError as error:
                if error.code=='PERS-202':raise
                raise_code('PERS-202',reason='workspace_content_unavailable')
            if len(raw)!=size or digest_of(raw)!=digest: raise_code('PERS-202',reason='workspace_content_digest')

    def _validate_workspace_snapshot_definition(self,definition,resources,previous,event):
        value=definition.model_dump();o=value['observation']
        try:
            self._context_project(resources,value['project_id'],value['project_revision'])
            self._workspace_quota(resources,value['project_id'])
            if previous is not None or value['created_by']!=event.payload['actor_ref']: raise ValueError('immutable capture owner')
            for ident,revision,seq,identity in [(o['grant_id'],o['grant_revision'],o['source_seq'],value['root_identity']),
                (o['metadata_grant_id'],o['metadata_grant_revision'],value['metadata_grant_source_seq'],value['metadata_root_identity'])]:
                if ident is None: continue
                row=resources.get(ident,{});grant=row.get('grant',{})
                if (grant.get('project_id')!=value['project_id'] or grant.get('revoked') or grant.get('revision')!=revision
                        or row.get('source_seq')!=seq or grant.get('root_identity')!=identity): raise ValueError('capture source binding')
        except (ValueError,PyHError): raise_code('PERS-202',reason='workspace_capture_definition_binding')

    @staticmethod
    def _workspace_confirmation(candidate,source,metadata,command):
        from pyharness.application.workspace_store import canonical,digest_of
        return digest_of(canonical({'project_revision':command.project_revision,'observation':candidate.observation,
            'identity_digest':candidate.identity_digest,'root_identity':source['grant']['root_identity'],
            'metadata_root_identity':metadata['grant']['root_identity'] if metadata else None,
            'metadata_source_seq':metadata['source_seq'] if metadata else None,'tracked_paths':list(candidate.tracked_paths)}))

    async def _collect_workspace_candidate(self,resources,project_id,grant_id,command,started,actor,authorize):
        self._context_project(resources,project_id,command.project_revision)
        source=self._snapshot_grant(resources,project_id,grant_id,command.expected_revision)
        meta=self._snapshot_grant(resources,project_id,command.metadata_grant_id,command.metadata_grant_revision) if command.metadata_grant_id else None
        self._workspace_storage_geometry(source,meta)
        remaining=self.MAX_WORKSPACE_CAPTURE_SECONDS-(time.monotonic()-started)
        if remaining<=0: raise_code('EVT-104',reason='workspace_capture_time_limit')
        def before_body_read():
            if authorize()!=actor: raise_code('GRD-403',reason='workspace_capture_subject_changed')
            self._context_project(resources,project_id,command.project_revision)
            self._snapshot_grant(resources,project_id,grant_id,command.expected_revision)
            if command.metadata_grant_id:
                self._snapshot_grant(resources,project_id,command.metadata_grant_id,command.metadata_grant_revision)
            self._workspace_storage_geometry(source,meta)
            self._context_finish(authorize,actor,resources,self._workspace_grant_ids({'observation':{'grant_id':grant_id,'metadata_grant_id':command.metadata_grant_id}}))
            if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS: raise_code('EVT-104',reason='workspace_capture_time_limit')
        try: candidate=await asyncio.wait_for(collect_workspace_git(source,meta,command,before_body_read=before_body_read),remaining)
        except TimeoutError: raise_code('EVT-104',reason='workspace_capture_time_limit')
        return candidate,source,meta

    def _workspace_storage_geometry(self,source,metadata):
        private=self.root.resolve(strict=True)
        for row in (source,metadata):
            if row is None: continue
            protected=Path(row['grant']['canonical_root']).resolve(strict=True)
            if private.is_relative_to(protected) or protected.is_relative_to(private):
                raise_code('GRD-403',reason='workspace_storage_source_overlap')

    def _workspace_capture_gate(self,project_id,grant_id,command,actor,authorize,candidate,started):
        if authorize()!=actor: raise_code('GRD-403',reason='workspace_capture_subject_changed')
        try: candidate.verify()
        except (OSError,ValueError): raise_code('GRD-403',reason='workspace_source_unavailable')
        fresh,_=self._grant_state()
        self._context_project(fresh,project_id,command.project_revision)
        self._snapshot_grant(fresh,project_id,grant_id,command.expected_revision)
        if command.metadata_grant_id:
            self._snapshot_grant(fresh,project_id,command.metadata_grant_id,command.metadata_grant_revision)
        self._context_finish(authorize,actor,fresh,(grant_id,)+((command.metadata_grant_id,) if command.metadata_grant_id else ()))
        if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS: raise_code('EVT-104',reason='workspace_capture_time_limit')
        return fresh

    async def preview_workspace_snapshot(self,project_id,grant_id,command,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False)
            resources,_=self._grant_state()
            candidate,source,meta=await self._collect_workspace_candidate(resources,project_id,grant_id,command,started,actor,authorize)
            self._workspace_capture_gate(project_id,grant_id,command,actor,authorize,candidate,started)
            result={**candidate.observation,'status':'workspace_snapshot_confirmation_required','project_revision':command.project_revision,
                'tracked_paths':list(candidate.tracked_paths),'scope':'tracked_plus_selected_untracked',
                'confirmation_digest':self._workspace_confirmation(candidate,source,meta,command)}
            from pyharness.application.workspace_store import canonical
            if len(canonical(result))>self.MAX_CONTEXT_BYTES: raise_code('EVT-100',reason='workspace_preview_bytes_limit')
            return result

    async def capture_workspace_snapshot(self,project_id,grant_id,command,*,command_id,authorize):
        from pyharness.application.workspace_store import canonical,digest_of
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False)
            request=digest_of(canonical({'operation':'workspace_snapshot.capture','project_id':project_id,'grant_id':grant_id,'command':command.model_dump()}))
            self._context_envelope('workspace_snapshot',command.snapshot_id,1,command_id,actor,request)
            resources,commands=self._grant_state();self._context_project(resources,project_id)
            old=commands.get((actor,command_id))
            if old:
                if old[0]!=request: raise_code('EVT-100',reason='idempotency_conflict')
                self._workspace_snapshot_access(resources,old[1],actor)
                self._context_finish(authorize,actor,resources,self._workspace_grant_ids(old[1]['workspace_snapshot']))
                return self._workspace_snapshot_summary(resources,old[1])
            if command.snapshot_id in resources: raise_code('EVT-101',reason='stale_revision')
            self._workspace_quota(resources,project_id)
            candidate,source,meta=await self._collect_workspace_candidate(resources,project_id,grant_id,command,started,actor,authorize)
            confirmation=self._workspace_confirmation(candidate,source,meta,command)
            if confirmation!=command.confirmation_digest: raise_code('EVT-101',reason='workspace_confirmation_changed')
            definition=WorkspaceSnapshot(snapshot_id=command.snapshot_id,project_id=project_id,project_revision=command.project_revision,
                created_by=actor,root_identity=source['grant']['root_identity'],metadata_root_identity=meta['grant']['root_identity'] if meta else None,
                metadata_grant_source_seq=meta['source_seq'] if meta else None,observation=candidate.observation,
                tracked_paths=list(candidate.tracked_paths),confirmation_digest=confirmation,source_identity_digest=candidate.identity_digest)
            if len(canonical(definition.model_dump()))>self.MAX_CONTEXT_BYTES: raise_code('EVT-100',reason='workspace_capture_manifest_limit')
            # Include public per-layer flags and selected-untracked names in the
            # pre-write budget. An unreadable oversized receipt must never commit.
            prospective={'workspace_snapshot':definition.model_dump(),'source_seq':sum(1 for _ in self._session.events_after(0))+1,
                'digest':digest_of(canonical(definition.model_dump())),'created_at':'2099-12-31T23:59:59.999999Z'}
            public=self._workspace_snapshot_summary({**resources,command.snapshot_id:prospective},prospective)
            if len(canonical(public))+1024>self.MAX_CONTEXT_BYTES: raise_code('EVT-100',reason='workspace_capture_receipt_limit')
            self._workspace_storage_geometry(source,meta)
            self._workspace_capture_gate(project_id,grant_id,command,actor,authorize,candidate,started)
            for digest,raw in candidate.contents.items(): self._write_snapshot_blob(self._snapshot_content(digest),raw)
            def gate():
                # A later immutable write must not leave an earlier referenced
                # content blob corrupt at the commit decision.
                self._verify_workspace_content(definition)
                fresh=self._workspace_capture_gate(project_id,grant_id,command,actor,authorize,candidate,started)
                if command.snapshot_id in fresh: raise_code('EVT-101',reason='stale_revision')
                self._workspace_quota(fresh,project_id)
            resources,row=await self._append_context(definition,'workspace_snapshot',command.snapshot_id,command_id,actor,request,gate)
            try:
                self._workspace_snapshot_access(resources,row,actor)
                self._context_finish(authorize,actor,resources,self._workspace_grant_ids(row['workspace_snapshot']))
            except PyHError:
                # The fact already committed. Deny the receipt, but never report
                # a pre-commit 403 that would discard the caller's original key.
                raise_code('PERS-202',reason='workspace_commit_receipt_unavailable')
            return self._workspace_snapshot_summary(resources,row)

    async def workspace_snapshots(self,project_id,*,authorize,query):
        async with self._lock:
            actor=authorize();await self._open(initialize=False)
            resources,_=self._grant_state();self._context_project(resources,project_id)
            rows=[];ids=[]
            for row in resources.values():
                value=row.get('workspace_snapshot')
                if not value or value['project_id']!=project_id or value['created_by']!=actor: continue
                try: self._workspace_snapshot_access(resources,row,actor)
                except PyHError as exc:
                    if exc.code=='GRD-403': continue
                    raise
                rows.append(self._workspace_snapshot_summary(resources,row,detail=False));ids.extend(self._workspace_grant_ids(value))
            self._context_finish(authorize,actor,resources,ids)
            from pyharness.application.workspace_input_pages import workspace_input_page
            return workspace_input_page(rows,query,kind='snapshots',project=project_id,actor=actor,watermark=sum(1 for _ in self._session.events_after(0)))

    async def workspace_snapshot(self,project_id,snapshot_id,*,authorize,item_index=None,layer=None):
        async with self._lock:
            actor=authorize();await self._open(initialize=False)
            resources,_=self._grant_state()
            row=self._context_owned(resources,snapshot_id,'workspace_snapshot',project_id,actor)
            self._workspace_snapshot_access(resources,row,actor)
            result=self._workspace_snapshot_summary(resources,row)
            if item_index is not None:
                items=row['workspace_snapshot']['observation']['items']
                if not 0<=item_index<len(items) or layer not in {'head','index','working'} or items[item_index][layer] is None:
                    raise_code('GRD-403',reason='workspace_file_unavailable')
                file=items[item_index][layer]
                from pyharness.application.source_snapshot import checked_private_bytes
                def before_read():
                    self._workspace_snapshot_access(resources,row,actor)
                    self._context_finish(authorize,actor,resources,self._workspace_grant_ids(row['workspace_snapshot']))
                try: raw=checked_private_bytes(self._snapshot_content(file['digest']),before_read=before_read)
                except OSError: raise_code('PERS-202',reason='workspace_content_missing')
                from pyharness.application.workspace_store import digest_of
                if len(raw)!=file['size_bytes'] or digest_of(raw)!=file['digest']: raise_code('PERS-202',reason='workspace_content_digest')
                result=(raw,file['digest'])
                self._workspace_snapshot_access(resources,row,actor)
            self._context_finish(authorize,actor,resources,self._workspace_grant_ids(row['workspace_snapshot']))
            return result
