"""Stop only the exact owned live execution; observation never creates a runner."""
from types import SimpleNamespace
from pyharness.application.execution_contracts import CancelExecution
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.run_attempt_contracts import SubmissionKey,AttemptId
from pyharness.core.lifecycle import admitted
from pyharness.errors import PyHError,raise_code


class ProductExecutionCancellation:
    @admitted
    async def execution_control_for_attempt(self,project,ident):
        self._authorize(write=True)
        project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,AttemptId)
        value=await self.store.execution_control_for_attempt(project,ident,authorize=lambda:self._authorize(write=True))
        if value['cancellation'] is not None:
            value['cancellation']=await self._cancellation_observation(project,value['cancellation'])
        return value

    def _owned_stop_queue(self,target):
        service=self.service;sid=target['session_id'];spine=service._engines.get(sid)
        runtime=getattr(spine,'platform_runtime',None);boundary=getattr(runtime,'product_execution',None)
        bound=getattr(boundary,'receipt',None);ref=target['execution_ref']
        if not isinstance(bound,dict):return None
        if (any(bound.get(k)!=v for k,v in ref.items()) or bound.get('session_id')!=sid or
                bound.get('task_id')!=target['task_id'] or bound.get('project_id')!=target['project_id'] or
                getattr(runtime,'session_id',None)!=sid or service.tenant_id!=target['tenant_id'] or
                getattr(runtime,'platform',None) is not service.platform or
                getattr(getattr(spine,'session',None),'sid',None)!=sid or
                getattr(getattr(spine,'session',None),'tenant_id',None)!=target['tenant_id'] or
                self._authorize(write=True)!=target['subject']):return None
        return service._queues.get(sid)

    async def _signal_execution_stop(self,target,queue,by):
        waiting=target['task_id'] in queue.status().waiting
        signalled=await queue.cancel(target['task_id'],by=by)
        if signalled and waiting and self._owned_stop_queue(target) is queue:
            # Queue.cancel removes a waiting task before its first await, so
            # no runner can publish a competing lifecycle terminal for it.
            spine=self.service._engines[target['session_id']]
            ctx=SimpleNamespace(session=spine.session,task_id=target['task_id'])
            await spine.platform_runtime.product_execution.append_phase(ctx,'cancelled','cancelled_before_start')

    async def _cancellation_observation(self,project,receipt):
        receipt,target=await self.store.cancellation_target(project,receipt['cancellation_id'],authorize=lambda:self._authorize(write=True))
        queue=self._owned_stop_queue(target)
        stopped=None;terminal='unknown'
        if queue is not None:
            state=queue.status()
            if target['task_id']==state.running or target['task_id'] in state.waiting:
                stopped=False;terminal='running' if target['task_id']==state.running else 'queued'
            else:
                try:result=await queue.wait_for(target['task_id'],timeout=0)
                except PyHError:pass
                else:
                    stopped=True;terminal='completed' if result.ok else 'cancelled' if result.code=='cancelled' else 'failed'
        # Queue futures/summary text never replace source authorization or enter
        # this minimal control response, even after sources were withdrawn.
        if self._authorize(write=True)!=target['subject']:raise_code('GRD-403',reason='execution_cancel_subject_changed')
        return dict(receipt,execution_stopped=stopped,terminal_observation=terminal)

    @admitted
    async def cancel_execution(self,project,body,command_id):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        command=CancelExecution.model_validate(body);key=self._workspace_id(command_id,SubmissionKey)
        authorize=lambda:self._authorize(write=True)
        old=await self.store.execution_cancellation_submission(project,key,authorize=authorize)
        if old['status']=='committed':
            if old['cancellation']['request_digest']!=self.store._cancellation_request(project,command):
                raise_code('EVT-100',reason='idempotency_conflict')
            return await self._cancellation_observation(project,old['cancellation'])
        target=await self.store.execution_stop_target(project,command.execution_ref,authorize=authorize)
        try:receipt,first=await self.store.cancel_execution(project,command,command_id=key,authorize=authorize)
        except PyHError as primary:
            if primary.code=='PERS-202':
                # Failure to acknowledge control persistence must not trap a
                # previously verified owned task. Signal only this exact live
                # runtime; keep the original persistence failure visible.
                try:
                    queue=self._owned_stop_queue(target)
                    if queue is not None:await self._signal_execution_stop(target,queue,'product_execution_cancel_unconfirmed')
                except BaseException as secondary:primary.add_note('stop signal unavailable: '+type(secondary).__name__)
            raise
        if first:
            _,target=await self.store.cancellation_target(project,receipt['cancellation_id'],authorize=authorize)
            queue=self._owned_stop_queue(target)
            if queue is not None:await self._signal_execution_stop(target,queue,'product_execution_cancel')
        return await self._cancellation_observation(project,receipt)

    @admitted
    async def cancellation_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        value=await self.store.execution_cancellation_submission(project,key,authorize=lambda:self._authorize(write=True))
        if value['status']=='committed':value['cancellation']=await self._cancellation_observation(project,value['cancellation'])
        return value
