"""Explicit new-attempt authorization on the original control owner only."""
import time,uuid
from pyharness.application.continuation_execution_contracts import (
    ContinuationBasis,ContinuedRunAttempt,ContinuedStartExecution,ContinuedExecutionAuthorization,continued_attempt_digest)
from pyharness.application.execution_contracts import ExecutionEnvironment
from pyharness.application.run_attempt_contracts import SubmitAttempt
from pyharness.errors import raise_code


def continuation_request(project,command):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='execution.continue',project_id=project,command=command.model_dump())))


def continuation_receipt(value):
    basis=value['selection'].get('continuation')
    if basis is None:return None
    return dict(execution_ref=basis['command']['execution_ref'],plan_digest=basis['plan']['plan_digest'],
        state={k:v for k,v in basis['plan']['state'].items() if k!='integrity'},budget=basis['plan']['budget'],
        lineage_journals=basis['plan'].get('lineage_journals',[]),source_observation='authorization_time',automatic_dispatch=False)


class ExecutionContinuationStore:
    async def authorize_continuation(self,project,command,*,command_id,authorize,inspect,describe):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.execution_materials import captured_materials
        started=time.monotonic();leases=[];handed_off=False
        async with self._workspace_capture_lock(started):
            try:
                actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
                request=continuation_request(project,command)
                self._context_envelope('execution_authorization',command.execution_id,1,command_id,actor,request)
                if old:=commands.get((actor,command_id)):
                    if old[0]!=request or old[1].get('execution_authorization',{}).get('schema_version')!=2:
                        raise_code('EVT-100',reason='idempotency_conflict')
                    return self._execution_summary(resources,old[1],actor,authorize,started),False,None,None
                from pyharness.application.task_graph_binding import reserved_graph_identities,graph_attempt_capacity
                reserved_a,reserved_e=reserved_graph_identities(resources)
                if command.attempt_id in reserved_a or command.execution_id in reserved_e:raise_code('GRD-403',reason='graph_node_identity_reserved')
                graph_attempt_capacity(self,resources,project,additional=1)
                if command.attempt_id in resources or command.execution_id in resources:
                    raise_code('EVT-101',reason='continuation_identity_reserved')
                for row in resources.values():
                    prior=row.get('run_attempt',{}).get('continuation')
                    if prior and prior['submission_key']==command_id and row['created_by']==actor:
                        raise_code('PERS-202',reason='continuation_authorization_unconfirmed')
                root=self._cancel_target(resources,project,command.execution_ref,actor)
                from pyharness.application.task_graph_control_checks import graph_basis
                origin_attempt=self._execution_attempt(resources,project,root['execution_authorization']['selection']['attempt_ref'],actor)
                if graph_basis(self,resources,origin_attempt) is not None:raise_code('GRD-403',reason='graph_continuation_requires_scheduler_admission')
                from pyharness.application.handoff_execution_store import handoff_contexts
                if handoff_contexts(self,resources,origin_attempt):raise_code('GRD-403',reason='handoff_continuation_requires_budget_admission')
                summary=self._execution_summary(resources,root,actor,authorize,started)
                context=await inspect(self._execution_read_row(resources,root),summary)
                plan=context['plan']
                if plan['plan_digest']!=command.plan_digest:raise_code('EVT-101',reason='continuation_plan_changed')
                if not plan['can_continue'] or plan['blockers']:raise_code('GRD-403',reason='continuation_not_permitted')
                basis=ContinuationBasis(command=command,submission_key=command_id,plan=plan)
                for row in resources.values():
                    prior=row.get('run_attempt',{}).get('continuation')
                    if (prior and prior['command']['execution_ref']==command.execution_ref.model_dump() and
                            prior['plan']['state']['state_digest']==basis.plan.state.state_digest):
                        raise_code('EVT-101',reason='continuation_checkpoint_reserved')
                source=root['execution_authorization'];environment=ExecutionEnvironment.model_validate(describe(summary['agent_ref']))
                if environment.model_dump()!=source['environment']:raise_code('EVT-101',reason='continuation_configuration_changed')
                if environment.boundary=='host_unrestricted' and not command.confirm_host_boundary:
                    raise_code('GRD-403',reason='execution_host_confirmation_required')
                internal='continuation-attempt-'+command.execution_id
                if command_id==internal or (actor,internal) in commands:raise_code('EVT-100',reason='continuation_attempt_key_reserved')
                leases=context['acquire']();context['verify']()
                origin=self._execution_attempt(resources,project,source['selection']['attempt_ref'],actor)
                selection=SubmitAttempt.model_validate(dict(origin['run_attempt']['selection'],attempt_id=command.attempt_id))
                prerequisites=self._checked_task_prerequisites(resources,project,selection.task_ref.model_dump(),actor,authorize,started)
                selected=self._attempt_context_refs(resources,project,selection.model_dump(),actor)
                attempt=ContinuedRunAttempt(attempt_id=command.attempt_id,project_id=project,project_revision=prerequisites.project_revision,
                    created_by=actor,submission_key=internal,selection=selection,prerequisites=prerequisites,context=selected,continuation=basis,
                    binding_digest=continued_attempt_digest(prerequisites.model_dump(),selected.model_dump(),basis.model_dump()))
                def source_gate():
                    fresh,_=self._grant_state();target=self._cancel_target(fresh,project,command.execution_ref,actor)
                    self._execution_summary(fresh,target,actor,authorize,started)
                    if describe(summary['agent_ref'])!=environment.model_dump():raise_code('EVT-101',reason='continuation_configuration_changed')
                    context['verify']();self._context_finish(authorize,actor,fresh)
                def attempt_gate():
                    source_gate();fresh,_=self._grant_state()
                    if command.attempt_id in fresh:raise_code('EVT-101',reason='continuation_attempt_reserved')
                    reserved_a,reserved_e=reserved_graph_identities(fresh)
                    if command.attempt_id in reserved_a or command.execution_id in reserved_e:raise_code('GRD-403',reason='graph_node_identity_reserved')
                    graph_attempt_capacity(self,fresh,project,additional=1)
                    self._context_quota(fresh,'run_attempt',project)
                    if (self._checked_task_prerequisites(fresh,project,selection.task_ref.model_dump(),actor,authorize,started)!=prerequisites or
                            self._attempt_context_refs(fresh,project,selection.model_dump(),actor)!=selected):
                        raise_code('EVT-101',reason='continuation_inputs_changed')
                attempt_request=digest_of(canonical(dict(operation='run_attempt.submit',project_id=project,command=selection.model_dump())))
                attempt_gate()
                resources,attempt_row=await self._append_context(attempt,'run_attempt',command.attempt_id,internal,actor,attempt_request,attempt_gate)
                reference=self._s05_ref(attempt_row,'attempt_id','run_attempt')
                start=ContinuedStartExecution(schema_version=1,attempt_ref=reference,agent_ref=summary['agent_ref'],execution_id=command.execution_id,
                    confirmation_digest='0'*64,confirm_host_boundary=command.confirm_host_boundary,continuation=basis)
                confirmation=self._execution_confirmation(project,start,actor,attempt_row,environment)
                start=ContinuedStartExecution.model_validate(dict(start.model_dump(),confirmation_digest=confirmation))
                authorization=ContinuedExecutionAuthorization(execution_id=command.execution_id,project_id=project,project_revision=prerequisites.project_revision,
                    created_by=actor,submission_key=command_id,selection=start,attempt_binding_digest=attempt.binding_digest,environment=environment,
                    session_id='s-pa-'+uuid.uuid4().hex,task_id='p-'+uuid.uuid4().hex)
                def authorization_gate():
                    source_gate();fresh,_=self._grant_state()
                    if command.execution_id in fresh:raise_code('EVT-101',reason='continuation_execution_reserved')
                    _,current,digest=self._execution_current(fresh,project,start,actor,authorize,lambda:describe(summary['agent_ref']),started)
                    if current!=environment or digest!=confirmation:raise_code('EVT-101',reason='continuation_preview_changed')
                    if any(r.get('execution_authorization',{}).get('selection',{}).get('attempt_ref')==reference for r in fresh.values()):
                        raise_code('EVT-101',reason='continuation_attempt_already_authorized')
                    self._context_quota(fresh,'execution_authorization',project)
                authorization_gate()
                resources,row=await self._append_context(authorization,'execution_authorization',command.execution_id,command_id,actor,request,authorization_gate)
                materials=captured_materials(self,resources,resources[command.attempt_id],actor,authorize,started)
                receipt=self._execution_summary(resources,row,actor,authorize,started)
                context['leases']=leases;context['authorization']=authorization
                source_gate();handed_off=True
                return receipt,True,materials,context
            finally:
                if not handed_off:
                    for lease in reversed(leases):lease.release()

    async def continuation_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state();self._context_project(resources,project)
            old=commands.get((actor,key));row=old[1] if old else None;execution=None;attempt=None
            if row is not None:
                if row.get('execution_authorization',{}).get('schema_version')!=2 or row['execution_authorization']['project_id']!=project:
                    raise_code('GRD-403',reason='continuation_submission_unavailable')
                execution=self._execution_summary(resources,row,actor,authorize,started)
            else:
                reserved=[r for r in resources.values() if r.get('run_attempt',{}).get('continuation',{}).get('submission_key')==key and r['created_by']==actor]
                if len(reserved)>1:raise_code('PERS-202',reason='continuation_submission_ambiguous')
                if reserved:
                    if reserved[0]['run_attempt']['project_id']!=project:raise_code('GRD-403',reason='continuation_submission_unavailable')
                    attempt=self._attempt_summary(resources,reserved[0],actor,authorize,started)
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if execution else 'attempt_reserved' if attempt else 'not_found',
                execution=execution,attempt=attempt,automatic_dispatch=False)
