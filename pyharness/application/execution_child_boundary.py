"""A delegated turn inherits its root authority, deadline and captured workspace."""
import asyncio
from pyharness.application.execution_boundary import ProductExecutionBoundary
from pyharness.application.execution_configuration import compiled_configuration
from pyharness.application.execution_contracts import AgentVersionReference
from pyharness.application.execution_delegation import own_lifecycle,scope_digest
from pyharness.application.execution_effects import confirmed_events,flush_confirmed
from pyharness.application.workspace_store import digest_of
from pyharness.errors import raise_code


class ProductChildBoundary(ProductExecutionBoundary):
    def __init__(self,root,payload,scope):
        super().__init__(root.projects,root.receipt,root.materials)
        self.root=root;self.payload=payload;self.scope=scope;self.deadline=root.deadline;self.entered=False

    def _context(self,ctx):
        sid=self.payload['child_session_id']
        if ctx.session.sid!=sid or str(ctx.task_id)!='subagent:'+sid or ctx.scope is not self.scope:
            raise_code('GRD-403',reason='product_child_context_changed')

    def checked(self):
        result=self.root.checked()
        if scope_digest(self.scope)!=self.payload['scope_digest']:raise_code('GRD-403',reason='product_child_scope_changed')
        self.root.children.check_parent(self.payload)
        return result

    async def append_phase(self,ctx,phase,reason=None):
        self._context(ctx)
        event=await ctx.session.append('execution.child',dict(self.payload,phase=phase,reason=reason),
            actor='system',task_id=str(ctx.task_id),sync=True)
        if confirmed_events(ctx.session)[-1]!=event:raise_code('PERS-202',reason='child_lifecycle_unconfirmed')

    async def before_model_attempt(self,model,method,ctx):
        self._context(ctx);events=await flush_confirmed(ctx.session);self.checked()
        if [e.payload['phase'] for e in own_lifecycle(events,ctx.session.sid)]!=['bound','running']:
            raise_code('GRD-403',reason='product_child_not_running')
        _,cfg=compiled_configuration(self.projects,AgentVersionReference.model_validate(self.receipt['agent_ref']))
        from pyharness.application.execution_model_identity import model_configuration_admission
        configuration_ok,model_ok=model_configuration_admission(ctx.config.llm,cfg.llm,self.projects.service.llm_runtime.registry,model)
        if not configuration_ok or not model_ok:
            raise_code('GRD-403',reason='product_child_model_changed')
        from pyharness.application.execution_state_contract import verify_state_contract
        verify_state_contract(self,ctx,events)

    async def run_task(self,event,ctx,invoke):
        self._context(ctx);self.checked()
        if self.entered or digest_of(event.payload['content'].encode())!=self.payload['task_digest']:
            raise_code('GRD-403',reason='product_child_input_changed')
        self.entered=True
        try:
            async with asyncio.timeout_at(self.root.deadline):
                await flush_confirmed(ctx.session);self.checked()
                await self.append_phase(ctx,'running')
                result=await invoke()
                self.checked()
        except BaseException as exc:
            phase='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed'
            reason='execution_cancelled' if phase=='cancelled' else 'execution_deadline_expired' if isinstance(exc,TimeoutError) else str(getattr(exc,'code','child_execution_failed'))[:100]
            try:await self.append_phase(ctx,phase,reason)
            except BaseException as secondary:exc.add_note('child terminal unavailable: '+type(secondary).__name__)
            raise
        reason=getattr(result,'reason',None)
        await self.append_phase(ctx,'completed' if reason=='complete' else 'cancelled' if reason=='cancelled' else 'failed',
            None if reason=='complete' else getattr(result,'error_code',None) or ('LLM-305' if reason=='budget' else 'runner_stopped'))
        return result
