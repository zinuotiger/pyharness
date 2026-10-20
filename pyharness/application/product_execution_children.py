"""Product delegation adapts the real core runner; no second queue or result store."""
import copy,uuid
from dataclasses import asdict
from pyharness.application.accounts import current_actor
from pyharness.application.execution_child_boundary import ProductChildBoundary
from pyharness.application.execution_child_graph import child_admissions,event_digest,invocation_proof
from pyharness.application.execution_delegation import current_delegation,normalized,scope_digest,own_lifecycle
from pyharness.application.execution_effects import EffectRecorder,confirmed_events,confirmed_prefix_events,flush_confirmed
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.errors import raise_code


class ProductChildren:
    def __init__(self,spine,root):
        self.spine=spine;self.root=root;self.manager=spine.subagent;self.original=self.manager._runner
        self.runtimes={};self.logs={};self.root.children=self
        self.manager._runner=self;self.manager._child_id_factory=lambda:'s-pa-'+uuid.uuid4().hex
        original_spawn=self.manager.spawn
        async def spawn(spec,ctx=None):
            self.invocation()
            sid=await original_spawn(spec,ctx)
            h=self.manager._children.get(sid)
            if h is not None and h.task is not None:h.task.add_done_callback(lambda _:self.finalize_child(sid))
            return sid
        self.manager.spawn=spawn
        spine.platform_runtime.product_children=self

    def __getattr__(self,name):return getattr(self.original,name)

    def invocation(self):
        inv=current_delegation.get();self.root.checked()
        if inv is None or inv.binding.tool!='subagent.spawn' or inv.execution_id!=self.root.receipt['execution_id'] or inv.authorization_digest!=self.root.receipt['digest']:
            raise_code('GRD-403',reason='product_delegation_not_governed')
        boundary=getattr(inv.ctx.platform_runtime,'product_execution',None)
        if boundary is not self.root and getattr(boundary,'root',None) is not self.root:
            raise_code('GRD-403',reason='product_delegation_foreign_runtime')
        return inv

    def check_parent(self,p):
        confirmed_prefix_events(self.spine.session)
        rootlife=own_lifecycle(self.spine.session.events_after(0),self.spine.session.sid)
        if not rootlife or rootlife[-1].payload['phase']!='running':raise_code('GRD-403',reason='product_parent_not_running')
        sid=p['invoker_session_id']
        if sid!=self.spine.session.sid:
            parent=self.runtimes.get(sid);log=self.logs.get(sid)
            if parent is None or log is None:raise_code('GRD-403',reason='product_parent_not_live')
            confirmed_prefix_events(log)
            life=own_lifecycle(log.events_after(0),sid)
            if not life or life[-1].payload['phase']!='running':raise_code('GRD-403',reason='product_parent_not_running')

    async def admit(self,env):
        inv=self.invocation();ctx=inv.ctx;rootlog=self.spine.session;sid=env.sub_id
        parent_events=await flush_confirmed(ctx.session)
        root_events=parent_events if ctx.session is rootlog else await flush_confirmed(rootlog)
        row,_,_,actor,_=self.root.checked()
        prior=child_admissions(root_events,row)
        parent=prior.get(ctx.session.sid)
        spawns=[e for e in root_events if e.type=='subagent.spawned' and e.payload['sub_id']==sid]
        if (len(spawns)!=1 or env.session.stats()['seq']!=1 or env.session.tenant_id!=rootlog.tenant_id or
                env.parent_sid!=rootlog.sid or env.scope is None):raise_code('GRD-403',reason='product_child_unbound')
        p=dict(schema_version=1,execution_id=self.root.receipt['execution_id'],phase='authorized',
            authorization_digest=self.root.receipt['digest'],authorization_seq=self.root.receipt['source_seq'],subject_ref=actor,
            root_session_id=rootlog.sid,child_session_id=sid,invoker_session_id=ctx.session.sid,invoker_task_id=str(ctx.task_id),
            call_id=inv.binding.call_id,tool_call_seq=inv.binding.tool_call_seq,inputs_digest=inv.binding.inputs_digest,
            decision_seq=inv.decision_seq,effect_started_seq=inv.started_seq,spawn_seq=spawns[0].seq,
            parent_input_seq=spawns[0].payload['parent_seq'],parent_admission_seq=parent.seq if parent else None,
            depth=env.spec.depth,task_digest=digest_of(env.spec.task.encode()),spec_digest=digest_of(canonical(asdict(env.spec))),
            materials_digest=self.root.materials['materials_digest'],scope_digest=scope_digest(env.scope),
            parent_limits=normalized(ctx.scope.limits),child_limits=normalized(env.scope.limits),budget_ratio=env.spec.budget_ratio,
            admission_seq=None,admission_digest=None,reason=None)
        invocation_proof(p,parent_events,int(getattr(ctx,'subagent_depth',0)))
        self.check_parent(p)
        if sid in self.runtimes or sid in prior:raise_code('GRD-402',reason='product_child_already_bound')
        authorized=await rootlog.append('execution.child',p,actor='system',task_id=self.root.receipt['task_id'],sync=True)
        if confirmed_events(rootlog)[-1]!=authorized:raise_code('PERS-202',reason='product_child_authority_unconfirmed')
        self.root.checked();child_admissions(confirmed_events(rootlog),row)
        bound=dict(authorized.payload,phase='bound',admission_seq=authorized.seq,admission_digest=event_digest(authorized))
        event=await env.session.append('execution.child',bound,actor='system',task_id='subagent:'+sid,sync=True)
        if confirmed_events(env.session)[-1]!=event:raise_code('PERS-202',reason='product_child_binding_unconfirmed')
        from pyharness.application.platform_runtime import PlatformRuntime
        runtime=PlatformRuntime(self.spine.platform_runtime.platform,self.spine.platform_runtime.definition.model_copy(deep=True),sid)
        boundary=ProductChildBoundary(self.root,event.payload,env.scope)
        runtime.product_execution=boundary;runtime.product_children=self
        runtime.execution_effects=EffectRecorder(self.root.receipt['execution_id'],self.root.receipt['digest'],boundary.authorize)
        runtime.delegated_actor=copy.deepcopy(current_actor.get());runtime.delegated_task_lifecycle=runtime.run_task
        self.runtimes[sid]=runtime;self.logs[sid]=env.session

    def runtime_for(self,sid):
        runtime=self.runtimes.get(sid)
        if runtime is None:raise_code('GRD-403',reason='product_child_not_admitted')
        runtime.product_execution.checked()
        return runtime

    async def run_child(self,env):
        try:
            await self.admit(env)
            return await self.original.run_child(env)
        finally:self.finalize_child(env.sub_id)

    def finalize_child(self,sid):
        try:self.original.close_child(sid)
        finally:
            self.runtimes.pop(sid,None);self.logs.pop(sid,None)
