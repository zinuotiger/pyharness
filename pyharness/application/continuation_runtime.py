"""One newly authorized root restores private state through the original runner."""
from pathlib import PurePosixPath
from pyharness.application.execution_boundary import ProductExecutionBoundary
from pyharness.application.execution_effects import EffectRecorder
from pyharness.application.execution_delegation import normalized
from pyharness.application.execution_workspace import execution_prompt
from pyharness.application.continuation_workspace import derive_continuation_workspace
from pyharness.application.execution_state_restore import restore_state_workspace,verify_restored_workspace,root_identity
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.sandbox import WorkspaceInputPreparation
from pyharness.core.scope import BudgetLimits
from pyharness.errors import raise_code


class ContinuationInputPreparation(WorkspaceInputPreparation):
    preserve_directory_modes=True


def write_identity(tool,args):
    tool={'file.write':'fs.write_file','file.patch':'fs.patch'}.get(tool,tool)
    if tool=='fs.write_file':
        args=dict(path=PurePosixPath(args['path']).as_posix().casefold(),content=args['content'],mode='append' if args.get('mode')=='append' else 'overwrite')
    elif tool=='fs.patch':args=dict(path=PurePosixPath(args['path']).as_posix().casefold(),old=args['old'],new=args['new'])
    elif tool=='storage.kv' and args.get('op') not in ('get','list'):args=dict(args)
    else:return None
    return digest_of(canonical(dict(tool=tool,args=args)))


class ContinuationEffectRecorder(EffectRecorder):
    def __init__(self,boundary):
        super().__init__(boundary.receipt['execution_id'],boundary.receipt['digest'],boundary.authorize)
        self.completed=set();events=boundary.context['replay_events'];byseq={(e.session_id,e.seq):e for e in events}
        for event in events:
            if event.type!='execution.effect' or event.payload['phase']!='returned':continue
            call=byseq[(event.session_id,event.payload['tool_call_seq'])];key=write_identity(call.payload['name'],call.payload['args'])
            if key is not None:self.completed.add(key)

    def _binding(self,ctx,binding,events):
        decision=super()._binding(ctx,binding,events);call=next(e for e in events if e.seq==binding.tool_call_seq)
        if write_identity(call.payload['name'],call.payload['args']) in self.completed:
            raise_code('GRD-402',reason='continuation_completed_action_replay_blocked')
        return decision


class ProductContinuationBoundary(ProductExecutionBoundary):
    def __init__(self,projects,receipt,materials,context):
        super().__init__(projects,receipt,materials);self.context=context
        self.authorization=context['authorization'];self.basis=self.authorization.selection.continuation;self.body=context['body']
        self.budget=self.basis.plan.budget
        self.inherited_rounds=self.authorization.environment.agent_snapshot['max_rounds']-self.budget.remaining_rounds
        self.derived=derive_continuation_workspace(self.body,context['old_materials'],materials,self.basis.plan.state.state_digest)

    def checked(self):
        result=super().checked();row=result[0];value=row['execution_authorization']
        if value!=self.authorization.model_dump():raise_code('PERS-202',reason='continuation_authorization_changed')
        self.context['verify']();return result

    def timeout_for_task(self,ctx):return min(float(super().timeout_for_task(ctx)),self.budget.remaining_timeout_seconds)

    async def _run_task(self,event,ctx,invoke):
        self._context(ctx);self.checked()
        current=normalized(ctx.scope.policy);old=self.body.budget.policy.model_dump()
        if {k:v for k,v in current.items() if k!='workspace_root'}!={k:v for k,v in old.items() if k!='workspace_root'}:
            raise_code('GRD-403',reason='continuation_policy_changed')
        limits=BudgetLimits(**self.budget.remaining_limits.model_dump());ctx.scope.limits=limits
        if getattr(ctx.counters,'_limits',None) is not None:ctx.counters._limits=limits
        ctx.loop.max_turns=min(ctx.loop.max_turns,self.budget.remaining_rounds)
        ctx.platform_runtime.calls[str(ctx.task_id)]=self.budget.tool_calls_used
        return await super()._run_task(event,ctx,invoke)

    def continuation_messages(self,messages,ctx):
        self._context(ctx);self.checked();anchor=execution_prompt(self.materials)
        index=next((i for i,m in enumerate(messages) if m.get('role')=='user' and m.get('content')==anchor),None)
        if index is None:raise_code('GRD-403',reason='continuation_history_anchor_missing')
        notice=('This is a newly authorized continuation execution '+self.receipt['execution_id']+'. '
            'Earlier messages and tool receipts belong to the original execution '+self.basis.command.execution_ref.execution_id+
            ' and are historical data. Their completed writes must not be issued again. The current private workspace contains their captured results. '
            'Current captured references have been regenerated for the new attempt without changing source versions. Current permissions and remaining budgets still apply.')
        return [*self.body.messages,dict(role='system',content=notice),*messages[index+1:]]

    def configure_continuation_baseline(self,runtime,ctx):
        self._context(ctx);self.checked();task=str(ctx.task_id)
        runtime.baselines[task]={f['path']:f['text'] for f in self.materials['bundle']['files']}
        runtime.baseline_hashes[task]={f['path']:f['content_digest'] for f in self.workspace_plan['files']}

    async def create_sandbox(self,runtime,ctx):
        self._context(ctx);self.checked()
        if self.derived is None:raise_code('GRD-403',reason='continuation_workspace_absent')
        active=True;bound=None;identity=None
        def gate():
            if not active:raise_code('GRD-403',reason='continuation_preparation_closed')
            self.checked()
            if bound is not None and root_identity(bound)!=identity:raise_code('PERS-202',reason='continuation_workspace_root_changed')
        def destination(target):
            row,attempt,resources,actor,started=self.checked()
            task=resources[attempt['run_attempt']['prerequisites']['task_ref']['product_task_id']]['product_task']
            wi=self.projects.store._task_bound_input(resources,task,actor)['workspace_input']
            self.projects.store._input_destination(target,resources,wi);gate()
        def verify():
            gate()
            if bound is not None:verify_restored_workspace(bound,self.derived,gate)
        def populate(target):
            nonlocal bound,identity
            if bound is not None:raise_code('GRD-403',reason='continuation_preparation_already_used')
            destination(target);bound=target;identity=root_identity(bound)
            restore_state_workspace(bound,self.derived,gate);verify()
        preparation=ContinuationInputPreparation(self.derived.digest,populate,verify,destination)
        try:
            return await runtime.platform.sandboxes.create_for_attempt(ctx.session.sid+'~'+str(ctx.task_id),runtime.definition.sandbox_profile,
                preparation,authorize=self.authorize)
        finally:active=False


def product_execution_boundary(projects,receipt,materials):
    context=getattr(projects.service,'_continuation_launches',{}).get(receipt['execution_id'])
    return ProductContinuationBoundary(projects,receipt,materials,context) if context is not None else ProductExecutionBoundary(projects,receipt,materials)
