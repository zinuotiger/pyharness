"""Trusted correction revisions on the existing control owner and immutable blobs."""
import time
from pyharness.application.correction_contracts import SubmitCorrection,CorrectionRevision
from pyharness.errors import PyHError,raise_code


class ExecutionCorrectionStore:
    @staticmethod
    def _correction_request(project,command):
        from pyharness.application.workspace_store import canonical,digest_of
        return digest_of(canonical(dict(operation='execution.correct',project_id=project,command=command.model_dump())))

    def _validate_execution_correction(self,definition,resources,previous,event):
        value=definition.model_dump();actor=value['created_by'];project=value['project_id']
        try:
            root=self._cancel_target(resources,project,value['execution_ref'],actor)
            if root['execution_authorization']['project_revision']!=value['project_revision']:raise ValueError('project version')
            self._context_project(resources,project,value['project_revision'])
            if actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id']:raise ValueError('creator or command')
            if previous:
                old=previous['execution_correction']
                if any(old[k]!=value[k] for k in ['created_by','project_id','project_revision','execution_ref']):raise ValueError('correction ownership')
            elif any(r.get('execution_correction',{}).get('execution_ref')==value['execution_ref'] for r in resources.values()):
                raise ValueError('execution already has a correction document')
            command=SubmitCorrection(schema_version=1,correction_id=value['correction_id'],expected_revision=value['revision']-1,
                execution_ref=definition.execution_ref,text=value['text'])
            if self._correction_request(project,command)!=event.payload['request_digest']:raise ValueError('request digest')
            self._context_quota(resources,'execution_correction',project)
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='execution_correction_binding')

    def _correction_summary(self,resources,row,actor,authorize,started):
        value=row['execution_correction'];project=value['project_id']
        self._context_owned({value['correction_id']:row},value['correction_id'],'execution_correction',project,actor)
        root=self._cancel_target(resources,project,value['execution_ref'],actor)
        self._execution_summary(resources,root,actor,authorize,started)
        self._context_finish(authorize,actor,resources)
        return dict(schema_version=1,correction_id=value['correction_id'],revision=value['revision'],project_id=project,
            execution_ref=value['execution_ref'],text=value['text'],digest=row['digest'],source_seq=row['source_seq'],
            created_at=row['created_at'],submission_key=value['submission_key'],request_digest=row['request_digest'],persisted=True)

    def _correction_history(self,resources,root):
        value=root['execution_authorization'];reference=dict(execution_id=value['execution_id'],revision=1,digest=root['digest'],source_seq=root['source_seq'])
        rows=[]
        for event in self._history_events(resources):
            p=event.payload
            if event.type!='workspace.resource_committed' or p['resource_kind']!='execution_correction':continue
            definition=self._read_definition(p)
            if definition.execution_ref.model_dump()!=reference:continue
            rows.append(dict(execution_correction=definition.model_dump(),digest=p['digest'],source_seq=event.seq,
                created_at=event.ts,created_by=p['actor_ref'],request_digest=p['request_digest']))
        return sorted(rows,key=lambda row:row['execution_correction']['revision'])

    def _execution_read_row(self,resources,row):
        return dict(row,_trusted_corrections=self._correction_history(resources,row))

    async def save_execution_correction(self,project,command,*,command_id,authorize,live):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=self._correction_request(project,command)
            self._context_envelope('execution_correction',command.correction_id,command.expected_revision+1,command_id,actor,request)
            old=commands.get((actor,command_id))
            if old:
                if old[0]!=request or 'execution_correction' not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                return self._correction_summary(resources,old[1],actor,authorize,started)
            root=self._cancel_target(resources,project,command.execution_ref,actor)
            self._execution_summary(resources,root,actor,authorize,started)
            current=resources.get(command.correction_id)
            if current and 'execution_correction' not in current:raise_code('EVT-101',reason='correction_id_reserved')
            revision=current['execution_correction']['revision'] if current else 0
            if command.expected_revision!=revision:raise_code('EVT-101',reason='correction_revision_conflict')
            definition=CorrectionRevision(schema_version=1,correction_id=command.correction_id,revision=revision+1,
                project_id=project,project_revision=root['execution_authorization']['project_revision'],execution_ref=command.execution_ref,
                text=command.text,created_by=actor,submission_key=command_id)
            def gate():
                fresh,_=self._grant_state();checked=self._cancel_target(fresh,project,command.execution_ref,actor)
                self._execution_summary(fresh,checked,actor,authorize,started)
                self._context_project(fresh,project,definition.project_revision)
                prior=fresh.get(command.correction_id)
                if (prior['execution_correction']['revision'] if prior else 0)!=revision:raise_code('EVT-101',reason='correction_revision_conflict')
                if any(r.get('execution_correction',{}).get('execution_ref')==command.execution_ref.model_dump() and
                       r['execution_correction']['correction_id']!=command.correction_id for r in fresh.values()):
                    raise_code('EVT-101',reason='correction_document_already_exists')
                self._context_quota(fresh,'execution_correction',project)
                if not live(checked,actor):raise_code('GRD-403',reason='correction_requires_live_execution')
                self._context_finish(authorize,actor,fresh)
            gate()
            resources,row=await self._append_context(definition,'execution_correction',command.correction_id,command_id,actor,request,gate)
            return self._correction_summary(resources,row,actor,authorize,started)

    async def execution_correction_versions(self,project,ident,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._context_owned(resources,ident,'execution_authorization',project,actor)
            summary=self._execution_summary(resources,row,actor,authorize,started)
            history=self._correction_history(resources,row)
            result=[self._correction_summary(resources,r,actor,authorize,started) for r in history]
            self._context_finish(authorize,actor,resources)
            return self._execution_read_row(resources,row),summary,result

    async def execution_correction_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state();self._context_project(resources,project)
            old=commands.get((actor,key));row=old[1] if old else None
            if row is not None and (not row.get('execution_correction') or row['execution_correction']['project_id']!=project):
                raise_code('GRD-403',reason='correction_submission_unavailable')
            value=self._correction_summary(resources,row,actor,authorize,started) if row else None
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if row else 'not_found',
                correction=value,writer_requires_reopen=self._failed)
