"""Application lifecycle for one explicitly authorized, non-replayed product run."""
import asyncio
from pyharness.application.execution_configuration import describe_execution, compiled_configuration
from pyharness.application.execution_contracts import AgentVersionReference
from pyharness.application.execution_effects import confirmed_events, flush_confirmed
from pyharness.application.execution_materials import captured_materials
from pyharness.application.execution_workspace import workspace_plan,execution_prompt
from pyharness.errors import raise_code


class ProductExecutionBoundary:
    def __init__(self, projects, receipt, materials):
        self.projects = projects; self.receipt = receipt; self.materials = materials
        self.workspace_plan=workspace_plan(materials)
        self.deadline = None
        from pyharness.application.execution_liveness import ExecutionHeartbeat
        self.heartbeat=ExecutionHeartbeat()

    def checked(self):
        if self.deadline is not None and asyncio.get_running_loop().time() >= self.deadline:
            raise TimeoutError('execution_deadline_expired')
        ref = AgentVersionReference.model_validate(self.receipt['agent_ref'])
        checked=self.projects.store.checked_execution(self.receipt['project_id'], self.receipt['execution_id'],
            authorize=lambda: self.projects._authorize(write=True), describe=lambda: describe_execution(self.projects, ref),
            expected_digest=self.receipt['digest'])
        from pyharness.application.task_graph_control_checks import check_live_graph
        check_live_graph(self.projects,checked[2],checked[1])
        return checked

    def authorize(self):
        self.checked()

    async def create_sandbox(self,runtime,ctx):
        self._context(ctx);self.checked()
        ref=AgentVersionReference.model_validate(self.receipt['agent_ref'])
        async with self.projects.store.execution_preparation(self.receipt['project_id'],self.receipt['execution_id'],
                self.materials,authorize=lambda:self.projects._authorize(write=True),
                describe=lambda:describe_execution(self.projects,ref),expected_digest=self.receipt['digest']) as preparation:
            return await runtime.platform.sandboxes.create_for_attempt(ctx.session.sid+'~'+str(ctx.task_id),
                runtime.definition.sandbox_profile,preparation,authorize=self.authorize)

    def _context(self, ctx):
        if ctx.session.sid != self.receipt['session_id'] or str(ctx.task_id) != self.receipt['task_id']:
            raise_code('GRD-403', reason='product_execution_context_mismatch')

    async def append_phase(self, ctx, phase, reason=None):
        self._context(ctx)
        bound = ctx.session.get(2)
        if (bound is None or bound.type != 'execution.lifecycle' or bound.payload['phase'] != 'bound' or
                bound.payload['authorization_digest'] != self.receipt['digest']):
            raise_code('PERS-202', reason='product_execution_unbound')
        payload = dict(bound.payload, phase=phase, reason=reason)
        event = await ctx.session.append('execution.lifecycle', payload, actor='system', task_id=str(ctx.task_id), sync=True)
        if confirmed_events(ctx.session)[-1] != event:raise_code('PERS-202', reason='execution_lifecycle_unconfirmed')

    async def before_model_attempt(self, model, method, ctx):
        self._context(ctx)
        events = await flush_confirmed(ctx.session)
        self.checked()
        phases = [e.payload['phase'] for e in events if e.type == 'execution.lifecycle']
        if phases != ['bound', 'running']:raise_code('GRD-403', reason='execution_not_running')
        _, expected = compiled_configuration(self.projects, AgentVersionReference.model_validate(self.receipt['agent_ref']))
        cfg = expected.llm
        from pyharness.application.execution_model_identity import model_configuration_admission
        configuration_ok,model_ok=model_configuration_admission(ctx.config.llm,cfg,self.projects.service.llm_runtime.registry,model)
        if not configuration_ok:raise_code('GRD-403', reason='execution_model_configuration_mismatch')
        if not model_ok:raise_code('GRD-403', reason='execution_model_mismatch')
        from pyharness.application.execution_state_contract import verify_state_contract
        verify_state_contract(self,ctx,events)

    async def run_task(self, event, ctx, invoke):
        async with self.heartbeat.monitor():
            return await self._run_monitored_task(event,ctx,invoke)

    def timeout_for_task(self,ctx):return ctx.platform_runtime.definition.timeout

    async def _run_monitored_task(self,event,ctx,invoke):
        self._context(ctx)
        if self.deadline is not None:raise_code('GRD-402',reason='execution_already_started')
        self.deadline = asyncio.get_running_loop().time() + self.timeout_for_task(ctx)
        try:
            async with asyncio.timeout_at(self.deadline):
                result = await self._run_task(event,ctx,invoke)
                self.checked()
        except BaseException as exc:
            phase = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
            code = ('execution_cancelled' if isinstance(exc,asyncio.CancelledError) else
                'execution_deadline_expired' if isinstance(exc,TimeoutError) else
                str(getattr(exc, 'code', 'execution_failed'))[:100])
            try:await self.append_phase(ctx,phase,code)
            except BaseException as secondary:exc.add_note('execution terminal receipt unavailable: ' + type(secondary).__name__)
            raise
        reason = getattr(result, 'reason', None)
        phase = 'completed' if reason == 'complete' else 'cancelled' if reason == 'cancelled' else 'failed'
        await self.append_phase(ctx,phase,None if reason=='complete' else 'runner_cancelled' if reason=='cancelled' else
            getattr(result,'error_code',None) or ('LLM-305' if reason=='budget' else 'runner_stopped'))
        return result

    async def _run_task(self, event, ctx, invoke):
        self._context(ctx)
        row, attempt, resources, actor, started = self.checked()
        actual = captured_materials(self.projects.store, resources, attempt, actor,
            lambda: self.projects._authorize(write=True), started)
        expected = dict(execution_id=self.receipt['execution_id'], authorization_digest=self.receipt['digest'],
            authorization_seq=self.receipt['source_seq'], materials_digest=actual['materials_digest'])
        if (actual['materials_digest'] != self.materials['materials_digest'] or
                event.payload.get('content') != execution_prompt(actual) or
                (event.payload.get('meta') or {}).get('product_execution') != expected):
            raise_code('GRD-403', reason='execution_input_binding_changed')
        if [e.payload['phase'] for e in await flush_confirmed(ctx.session) if e.type == 'execution.lifecycle'] != ['bound']:
            raise_code('GRD-402', reason='execution_already_started')
        self.checked()
        await self.append_phase(ctx, 'running')
        return await invoke()
