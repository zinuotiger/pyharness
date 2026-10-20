"""Internal graph admission creates the original typed attempt, never a queue."""
import time
from pyharness.application.run_attempt_contracts import SubmitAttempt
from pyharness.application.task_graph_execution_contracts import GraphRunAttempt,GraphNodeBasis,graph_attempt_digest
from pyharness.application.task_graph_binding import graph_node_plan,node_keys
from pyharness.errors import raise_code


class TaskGraphNodeStore:
    async def submit_graph_node(self,project,basis,*,authorize,live):
        from pyharness.application.workspace_store import canonical,digest_of
        started=time.monotonic();basis=GraphNodeBasis.model_validate(basis)
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            _,plan=graph_node_plan(self,resources,project,basis,actor)
            command=SubmitAttempt(**dict(plan['context'],attempt_id=plan['attempt_id'],task_ref=plan['prerequisites']['task_ref']))
            key,_=node_keys(basis.graph_run_ref.model_dump(),basis.node_id)
            request=digest_of(canonical(dict(operation='run_attempt.submit',project_id=project,command=command.model_dump())))
            if commands.get((actor,key)) or plan['attempt_id'] in resources:raise_code('EVT-101',reason='graph_node_attempt_already_reserved')
            definition=GraphRunAttempt(attempt_id=plan['attempt_id'],project_id=project,project_revision=plan['prerequisites']['project_revision'],
                created_by=actor,submission_key=key,selection=command,prerequisites=plan['prerequisites'],context=plan['context'],graph_node=basis,
                binding_digest=graph_attempt_digest(plan['prerequisites'],plan['context'],basis.model_dump()))
            def gate():
                fresh,_=self._grant_state();root,_=graph_node_plan(self,fresh,project,basis,actor)
                self._graph_run_authority(fresh,root,actor,authorize,started);live()
                if self._graph_cancelled(fresh,basis.graph_run_ref.model_dump()):raise_code('GRD-403',reason='task_graph_cancelled')
                if plan['attempt_id'] in fresh:raise_code('EVT-101',reason='graph_node_attempt_already_reserved')
                current=self._checked_task_prerequisites(fresh,project,command.task_ref.model_dump(),actor,authorize,started)
                if current.model_dump()!=plan['prerequisites']:raise_code('EVT-101',reason='graph_node_prerequisites_changed')
                self._context_finish(authorize,actor,fresh)
            gate();resources,row=await self._append_context(definition,'run_attempt',plan['attempt_id'],key,actor,request,gate)
            return self._attempt_summary(resources,row,actor,authorize,started)
