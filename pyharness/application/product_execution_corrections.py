"""Current-creator correction commands, with exact read-only application receipts."""
from pyharness.application.correction_contracts import SubmitCorrection
from pyharness.application.execution_contracts import ExecutionId
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.execution_correction_runtime import correction_ref,validate_correction_applications
from pyharness.core.lifecycle import admitted
from pyharness.errors import raise_code


class ProductExecutionCorrections:
    def _correction_live(self,row,actor):
        value=row['execution_authorization']
        target=dict(execution_ref=dict(execution_id=value['execution_id'],revision=1,digest=row['digest'],source_seq=row['source_seq']),
            subject=actor,project_id=value['project_id'],tenant_id=self.service.tenant_id,session_id=value['session_id'],task_id=value['task_id'])
        queue=self._owned_stop_queue(target)
        if queue is None:return False
        state=queue.status()
        return value['task_id']==state.running or value['task_id'] in state.waiting

    async def _execution_correction_view(self,project,ident):
        async def read(row,summary):
            log=await read_execution_log(self.service._tenant_sessions_dir(),row,self.service.tenant_id)
            events=list(log.events_after(0));applications=validate_correction_applications(events,row)
            history=row['_trusted_corrections'];latest=history[-1]['execution_correction']['revision'] if history else 0
            result=[]
            for r in history:
                value=r['execution_correction'];key=(value['correction_id'],value['revision']);applied=applications.get(key)
                public={k:v for k,v in value.items() if k not in ('created_by','project_revision')}
                public.update(digest=r['digest'],source_seq=r['source_seq'],created_at=r['created_at'],request_digest=r['request_digest'],
                    persisted=True,application_status='applied' if applied else 'superseded' if value['revision']<latest else 'pending',
                    application_source_seq=applied.seq if applied else None,application_scope='root_context',
                    application_observation='context_prepared' if applied else None)
                result.append(public)
            return dict(schema_version=1,project_id=project,execution_id=ident,corrections=result,total=len(result),
                current_revision=latest,can_submit=self._correction_live(row,self._authorize(write=True)),
                execution_ref={k:summary[k] for k in ('execution_id','revision','digest','source_seq')})
        return await self.store.observe_execution(project,ident,authorize=lambda:self._authorize(write=True),read=read)

    @admitted
    async def submit_execution_correction(self,project,ident,body,command_id):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        command=SubmitCorrection.model_validate(body);key=self._workspace_id(command_id,SubmissionKey)
        if command.execution_ref.execution_id!=ident:raise_code('EVT-101',reason='correction_execution_reference')
        value=await self.store.save_execution_correction(project,command,command_id=key,authorize=lambda:self._authorize(write=True),live=self._correction_live)
        view=await self._execution_correction_view(project,ident)
        for row in view['corrections']:
            if all(row[k]==value[k] for k in ('correction_id','revision','digest','source_seq')):return row
        raise_code('PERS-202',reason='correction_receipt_unavailable')

    @admitted
    async def execution_corrections(self,project,ident):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        return await self._execution_correction_view(project,ident)

    @admitted
    async def correction_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        value=await self.store.execution_correction_submission(project,key,authorize=lambda:self._authorize(write=True))
        if value['status']=='committed':
            receipt=value['correction'];view=await self._execution_correction_view(project,receipt['execution_ref']['execution_id'])
            found=next((r for r in view['corrections'] if all(r[k]==receipt[k] for k in ('correction_id','revision','digest','source_seq'))),None)
            if found is None:raise_code('PERS-202',reason='correction_receipt_unavailable')
            value['correction']=found
        return value
