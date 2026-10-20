"""Version-two context source binding on the same SessionLog and immutable blobs."""
from pyharness.application.workspace_manifest import ContextManifestV2,WorkspaceSourceReference
from pyharness.application.workspace_snapshot import working_items
from pyharness.application.context_manifest import task_identity
from pyharness.errors import PyHError,raise_code


class WorkspaceManifestStore:
    @staticmethod
    def _workspace_source_reference(row):
        s=row['workspace_snapshot'];o=s['observation']
        return WorkspaceSourceReference(snapshot_id=s['snapshot_id'],revision=1,digest=row['digest'],source_seq=row['source_seq'],
            captured_project_revision=s['project_revision'],grant_id=o['grant_id'],grant_revision=o['grant_revision'],grant_source_seq=o['source_seq'],
            root_identity=s['root_identity'],metadata_grant_id=o['metadata_grant_id'],metadata_grant_revision=o['metadata_grant_revision'],
            metadata_grant_source_seq=s['metadata_grant_source_seq'],metadata_root_identity=s['metadata_root_identity'],
            head_oid=o['head_oid'],index_digest=o['index_digest'],confirmation_digest=s['confirmation_digest']).model_dump()

    def _workspace_manifest_snapshot(self,resources,value,actor):
        ref=value['source_ref']
        row=self._context_owned(resources,ref['snapshot_id'],'workspace_snapshot',value['project_id'],actor)
        if self._workspace_source_reference(row)!=ref:
            raise_code('PERS-202',reason='workspace_context_source_binding')
        return row

    def _validate_workspace_manifest_definition(self,definition,resources,previous,event):
        value=definition.model_dump()
        try:
            self._context_project(resources,value['project_id'],value['project_revision'])
            self._context_quota(resources,'manifest',value['project_id'])
            if value['created_by']!=event.payload['actor_ref']: raise ValueError('creator mismatch')
            prior=previous.get('manifest') if previous else None
            if previous and (not prior or prior['schema_version']!=2 or prior['created_by']!=value['created_by']
                    or prior['project_id']!=value['project_id'] or task_identity(prior['task_ref'])!=task_identity(value['task_ref'])
                    or prior['source_ref']['grant_id']!=value['source_ref']['grant_id']): raise ValueError('immutable context ownership/target/source')
            row=self._workspace_manifest_snapshot(resources,value,value['created_by']);snapshot=row['workspace_snapshot']
            if self._workspace_latest(resources,snapshot)!=snapshot['snapshot_id']: raise ValueError('superseded source')
            for ident in self._workspace_grant_ids(snapshot):
                if resources[ident]['grant']['revoked']: raise ValueError('withdrawn source')
            items=working_items(snapshot)
            if value['items']!=items or value['total_size_bytes']!=sum(i['size_bytes'] for i in items): raise ValueError('derived context items')
            if value['task_ref']:
                self._context_task(resources,value['task_ref'],value['project_id'],value['project_revision'],value['created_by'])
        except (ValueError,PyHError): raise_code('PERS-202',reason='workspace_context_definition_binding')

    def _workspace_manifest_access(self,resources,row,actor):
        value=row['manifest']
        self._context_owned({value['manifest_id']:row},value['manifest_id'],'manifest',value['project_id'],actor)
        if value['schema_version']!=2: raise_code('GRD-403',reason='workspace_context_unavailable')
        self._context_project(resources,value['project_id'])
        if value['task_ref']:
            self._context_task(resources,value['task_ref'],value['project_id'],value['project_revision'],actor,exact=False)
        source=self._workspace_manifest_snapshot(resources,value,actor)
        self._workspace_snapshot_access(resources,source,actor)
        return source

    def _workspace_manifest_summary(self,resources,row,*,detail=True):
        from pyharness.application.workspace_store import canonical,digest_of
        value={k:v for k,v in row['manifest'].items() if k!='created_by'}
        reasons=[]
        if resources[value['project_id']]['project']['revision']!=value['project_revision']: reasons.append('project_revision_changed')
        if value['task_ref'] and resources[value['task_ref']['product_task_id']]['task_reference']['revision']!=value['task_ref']['revision']:
            reasons.append('task_revision_changed')
        if resources[value['manifest_id']]['manifest']['revision']!=value['revision']: reasons.append('manifest_revision_superseded')
        source=resources[value['source_ref']['snapshot_id']]['workspace_snapshot']
        if self._workspace_latest(resources,source)!=source['snapshot_id']: reasons.append('workspace_snapshot_superseded')
        result={**value,'items':[] if reasons else value['items'],'file_count':len(value['items']),
            'digest':row['digest'],'source_seq':row['source_seq'],'created_at':row['created_at'],
            'status':'stale' if reasons else 'current','stale_reasons':reasons,
            'binding_mode':'context_task_reference' if value['task_ref'] else 'project_only','executable':False}
        if not detail: result.pop('items')
        result['receipt_digest']=digest_of(canonical(result))
        return result

    async def save_workspace_manifest(self,project_id,command,*,command_id,authorize):
        from pyharness.application.workspace_store import canonical,digest_of
        async with self._lock:
            actor=authorize();await self._open(initialize=False)
            request=digest_of(canonical({'operation':'workspace_context_manifest','project_id':project_id,'command':command.model_dump()}))
            self._context_envelope('context_manifest',command.manifest_id,command.expected_revision+1,command_id,actor,request)
            resources,commands=self._grant_state();self._context_project(resources,project_id)
            old=commands.get((actor,command_id))
            if old:
                if old[0]!=request: raise_code('EVT-100',reason='idempotency_conflict')
                source=self._workspace_manifest_access(resources,old[1],actor)
                self._context_finish(authorize,actor,resources,self._workspace_grant_ids(source['workspace_snapshot']))
                return self._workspace_manifest_summary(resources,old[1])
            snapshot=self._context_owned(resources,command.source_ref.snapshot_id,'workspace_snapshot',project_id,actor)
            ref=command.source_ref
            if snapshot['digest']!=ref.digest or snapshot['source_seq']!=ref.source_seq:
                raise_code('EVT-101',reason='workspace_context_source_revision')
            items=working_items(snapshot['workspace_snapshot'])
            definition=ContextManifestV2(manifest_id=command.manifest_id,revision=command.expected_revision+1,
                project_id=project_id,project_revision=command.project_revision,source_ref=self._workspace_source_reference(snapshot),
                task_ref=command.task_ref,items=items,total_size_bytes=sum(i['size_bytes'] for i in items),created_by=actor)
            def gate():
                fresh,_=self._grant_state();self._context_project(fresh,project_id,command.project_revision)
                previous=fresh.get(command.manifest_id)
                if previous:
                    self._context_owned(fresh,command.manifest_id,'manifest',project_id,actor)
                    prior=previous['manifest']
                    if (prior['schema_version']!=2 or task_identity(prior['task_ref'])!=task_identity(definition.model_dump()['task_ref'])
                            or prior['source_ref']['grant_id']!=definition.source_ref.grant_id):
                        raise_code('EVT-101',reason='workspace_context_identity')
                if (previous['manifest']['revision'] if previous else 0)!=command.expected_revision:
                    raise_code('EVT-101',reason='stale_revision')
                self._context_quota(fresh,'manifest',project_id)
                source=self._workspace_manifest_access(fresh,{'manifest':definition.model_dump()},actor)
                captured=source['workspace_snapshot'];observation=captured['observation']
                self._workspace_storage_geometry(fresh[observation['grant_id']],fresh[observation['metadata_grant_id']] if observation['metadata_grant_id'] else None)
                if self._workspace_latest(fresh,source['workspace_snapshot'])!=ref.snapshot_id:
                    raise_code('EVT-101',reason='workspace_snapshot_superseded')
                if command.task_ref:
                    self._context_task(fresh,command.task_ref.model_dump(),project_id,command.project_revision,actor)
                self._context_finish(authorize,actor,fresh,self._workspace_grant_ids(source['workspace_snapshot']))
            gate()
            resources,row=await self._append_context(definition,'context_manifest',command.manifest_id,command_id,actor,request,gate)
            try:
                source=self._workspace_manifest_access(resources,row,actor)
                self._context_finish(authorize,actor,resources,self._workspace_grant_ids(source['workspace_snapshot']))
            except PyHError: raise_code('PERS-202',reason='workspace_commit_receipt_unavailable')
            return self._workspace_manifest_summary(resources,row)

    async def workspace_manifests(self,project_id,*,authorize,query):
        async with self._lock:
            actor=authorize();await self._open(initialize=False)
            resources,_=self._grant_state();self._context_project(resources,project_id)
            rows=[];ids=[]
            for row in resources.values():
                value=row.get('manifest')
                if not value or value['schema_version']!=2 or value['project_id']!=project_id or value['created_by']!=actor: continue
                try: source=self._workspace_manifest_access(resources,row,actor)
                except PyHError as exc:
                    if exc.code=='GRD-403': continue
                    raise
                rows.append(self._workspace_manifest_summary(resources,row,detail=False));ids.extend(self._workspace_grant_ids(source['workspace_snapshot']))
            self._context_finish(authorize,actor,resources,ids)
            from pyharness.application.workspace_input_pages import workspace_input_page
            return workspace_input_page(rows,query,kind='manifests',project=project_id,actor=actor,watermark=sum(1 for _ in self._session.events_after(0)))

    async def workspace_manifest(self,project_id,manifest_id,revision,*,authorize,item_index=None):
        async with self._lock:
            actor=authorize();await self._open(initialize=False)
            resources,_=self._grant_state()
            self._context_owned(resources,manifest_id,'manifest',project_id,actor)
            row=None
            for event in self._session.events_after(0):
                p=event.payload
                if (event.type=='workspace.resource_committed' and p['resource_kind']=='context_manifest'
                        and p['resource_id']==manifest_id and p['revision']==revision):
                    row={'manifest':self._read_definition(p).model_dump(),'digest':p['digest'],'source_seq':event.seq,
                         'created_at':event.ts,'created_by':p['actor_ref']};break
            if row is None: raise_code('GRD-403',reason='workspace_context_unavailable')
            source=self._workspace_manifest_access(resources,row,actor)
            result=self._workspace_manifest_summary(resources,row)
            if item_index is not None:
                if result['status']!='current': raise_code('EVT-101',reason='workspace_context_stale')
                file=next((item for item in row['manifest']['items'] if item['input_index']==item_index),None)
                if not file: raise_code('GRD-403',reason='workspace_context_file_unavailable')
                from pyharness.application.source_snapshot import checked_private_bytes
                def before_read():
                    current=self._workspace_manifest_access(resources,row,actor)
                    self._context_finish(authorize,actor,resources,self._workspace_grant_ids(current['workspace_snapshot']))
                try: raw=checked_private_bytes(self._snapshot_content(file['content_digest']),before_read=before_read)
                except OSError: raise_code('PERS-202',reason='workspace_content_missing')
                from pyharness.application.workspace_store import digest_of
                if len(raw)!=file['size_bytes'] or digest_of(raw)!=file['content_digest']: raise_code('PERS-202',reason='workspace_content_digest')
                result=(raw,file['content_digest'])
                self._workspace_manifest_access(resources,row,actor)
            self._context_finish(authorize,actor,resources,self._workspace_grant_ids(source['workspace_snapshot']))
            return result
