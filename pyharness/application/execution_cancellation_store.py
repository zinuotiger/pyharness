"""Immutable stop intents on the existing owner, without requiring source contents."""
import time
from pyharness.application.execution_control_projection import ExecutionControlProjection
from pyharness.application.execution_contracts import CancelExecution,ExecutionCancellation
from pyharness.errors import PyHError,raise_code


class ExecutionCancellationStore(ExecutionControlProjection):
    async def execution_control_for_attempt(self,project,ident,*,authorize):
        async with self._workspace_capture_lock(time.monotonic()):
            actor=authorize();resources,_=self._cancellation_state()
            if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='execution_control_unavailable')
            attempt=self._context_owned(resources,ident,'run_attempt',project,actor)
            reference=self._s05_ref(attempt,'attempt_id','run_attempt')
            rows=[r for r in resources.values() if r.get('execution_authorization',{}).get('selection',{}).get('attempt_ref')==reference]
            if len(rows)>1:raise_code('PERS-202',reason='attempt_authorization_ambiguous')
            execution=None;cancellation=None
            if rows:
                row=rows[0];value=row['execution_authorization']
                execution=dict(execution_id=value['execution_id'],revision=1,digest=row['digest'],source_seq=row['source_seq'])
                self._cancel_target(resources,project,execution,actor)
                stops=[r for r in resources.values() if r.get('execution_cancellation',{}).get('execution_ref')==execution]
                if len(stops)>1:raise_code('PERS-202',reason='execution_stop_ambiguous')
                if stops:cancellation=self._cancellation_summary(resources,stops[0],actor,authorize)
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,control_only=True,project_id=project,attempt_ref=reference,
                status='authorized' if execution else 'not_authorized',execution_ref=execution,cancellation=cancellation)

    async def execution_session_stop_target(self,sid,*,authorize):
        """Locate existing control authority without opening private source blobs."""
        async with self._workspace_capture_lock(time.monotonic()):
            actor=authorize();resources,_=self._cancellation_state()
            rows=[r for r in resources.values() if r.get('execution_authorization',{}).get('session_id')==sid]
            if len(rows)!=1:raise_code('GRD-403',reason='product_stop_unavailable')
            row=rows[0];value=row['execution_authorization'];project=value['project_id']
            reference=dict(execution_id=value['execution_id'],revision=1,digest=row['digest'],source_seq=row['source_seq'])
            self._cancel_target(resources,project,reference,actor)
            self._context_finish(authorize,actor,resources)
            return row,dict(execution_ref=reference,session_id=sid,task_id=value['task_id'],subject=actor,
                tenant_id=self.tenant_id,project_id=project)

    @staticmethod
    def _cancellation_request(project,command):
        from pyharness.application.workspace_store import canonical,digest_of
        return digest_of(canonical(dict(operation='execution.cancel',project_id=project,command=command.model_dump())))

    def _cancel_target(self,resources,project,reference,actor):
        ref=reference.model_dump() if hasattr(reference,'model_dump') else reference
        if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='execution_cancel_unavailable')
        row=self._context_owned(resources,ref['execution_id'],'execution_authorization',project,actor)
        expected=dict(execution_id=row['execution_authorization']['execution_id'],revision=1,digest=row['digest'],source_seq=row['source_seq'])
        if ref!=expected:raise_code('EVT-101',reason='execution_cancel_reference')
        return row

    def _validate_execution_cancellation(self,definition,resources,previous,event):
        value=definition.model_dump();actor=value['created_by'];project=value['project_id']
        try:
            self._cancel_target(resources,project,value['execution_ref'],actor)
            command=CancelExecution(schema_version=1,cancellation_id=value['cancellation_id'],execution_ref=definition.execution_ref)
            if (previous is not None or actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id'] or
                    self._cancellation_request(project,command)!=event.payload['request_digest'] or
                    any(r.get('execution_cancellation',{}).get('execution_ref')==value['execution_ref'] for r in resources.values())):
                raise ValueError('immutable stop binding')
            self._context_quota(resources,'execution_cancellation',project)
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='execution_cancellation_binding')

    def _cancellation_summary(self,resources,row,actor,authorize):
        value=row['execution_cancellation'];project=value['project_id']
        self._context_owned({value['cancellation_id']:row},value['cancellation_id'],'execution_cancellation',project,actor)
        self._cancel_target(resources,project,value['execution_ref'],actor)
        self._context_finish(authorize,actor,resources)
        return dict(schema_version=1,cancellation_id=value['cancellation_id'],revision=1,project_id=project,
            execution_ref=value['execution_ref'],digest=row['digest'],source_seq=row['source_seq'],created_at=row['created_at'],
            submission_key=value['submission_key'],request_digest=row['request_digest'],persisted=True,
            status='cancellation_requested',execution_stopped=None)

    async def cancel_execution(self,project,command,*,command_id,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize()
            await self._open_projected(initialize=False,project=self._project_cancellation_control)
            resources,commands=self._cancellation_state();request=self._cancellation_request(project,command)
            self._context_envelope('execution_cancellation',command.cancellation_id,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request or 'execution_cancellation' not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                return self._cancellation_summary(resources,old[1],actor,authorize),False
            definition=ExecutionCancellation(**command.model_dump(),project_id=project,created_by=actor,submission_key=command_id)
            def gate():
                fresh,_=self._cancellation_state()
                self._cancel_target(fresh,project,command.execution_ref,actor)
                if command.cancellation_id in fresh:raise_code('EVT-101',reason='cancellation_id_reserved')
                if any(r.get('execution_cancellation',{}).get('execution_ref')==command.execution_ref.model_dump() for r in fresh.values()):
                    raise_code('EVT-101',reason='execution_already_cancel_requested')
                self._context_quota(fresh,'execution_cancellation',project)
                self._context_finish(authorize,actor,fresh)
            gate()
            resources,row=await self._append_context_projected(definition,'execution_cancellation',command.cancellation_id,
                command_id,actor,request,gate,self._cancellation_state)
            return self._cancellation_summary(resources,row,actor,authorize),True

    async def execution_cancellation_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands=self._cancellation_state()
            if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='execution_cancel_unavailable')
            old=commands.get((actor,key));row=old[1] if old else None
            if row is not None and (not row.get('execution_cancellation') or row['execution_cancellation']['project_id']!=project):
                raise_code('GRD-403',reason='execution_cancel_unavailable')
            value=self._cancellation_summary(resources,row,actor,authorize) if row else None
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if row else 'not_found',
                cancellation=value,writer_requires_reopen=self._failed)

    async def cancellation_target(self,project,ident,*,authorize):
        async with self._workspace_capture_lock(time.monotonic()):
            actor=authorize();resources,_=self._cancellation_state()
            row=self._context_owned(resources,ident,'execution_cancellation',project,actor)
            receipt=self._cancellation_summary(resources,row,actor,authorize)
            target=self._cancel_target(resources,project,row['execution_cancellation']['execution_ref'],actor)
            value=target['execution_authorization']
            self._context_finish(authorize,actor,resources)
            return receipt,dict(execution_ref=receipt['execution_ref'],session_id=value['session_id'],task_id=value['task_id'],
                subject=actor,tenant_id=self.tenant_id,project_id=project)

    async def execution_stop_target(self,project,reference,*,authorize):
        async with self._workspace_capture_lock(time.monotonic()):
            actor=authorize();resources,_=self._cancellation_state()
            row=self._cancel_target(resources,project,reference,actor);value=row['execution_authorization']
            self._context_finish(authorize,actor,resources)
            return dict(execution_ref=reference.model_dump(),session_id=value['session_id'],task_id=value['task_id'],
                subject=actor,tenant_id=self.tenant_id,project_id=project)
