"""Ready-node scheduler over original product executions; reads never restart it."""
import asyncio
from pyharness.core.lifecycle import admitted
from pyharness.application.execution_configuration import describe_execution
from pyharness.application.task_graph_binding import node_keys
from pyharness.application.task_graph_execution_contracts import GraphNodeStartExecution
from pyharness.application.execution_contracts import AgentVersionReference
from pyharness.errors import PyHError,raise_code


class GraphOwner:
    def __init__(self,projects,authority,actor):
        self.projects=projects;self.authority=authority;self.actor=actor
        self.reference={k:authority[k] for k in ('graph_run_id','revision','digest','source_seq')}
        self.active=set();self.completed=set();self.stop_targets={};self.closed=False;self.task=None;self.error=None

    def check(self):
        if self.closed or self.task.done() or self.projects._authorize(write=True)!=self.actor:
            raise_code('GRD-403',reason='graph_owner_unavailable')
        resources,_=self.projects.store._cancellation_state()
        self.projects.store._graph_control_run(resources,self.authority['project_id'],self.reference,self.actor)
        if (self.projects.store._graph_cancelled(resources,self.reference) or
                self.projects.store._experiment_graph_stop_requested(resources,self.reference)):
            raise asyncio.CancelledError('task_graph_cancel_requested')

    def ready(self,node):
        self.check()
        if node['node_id'] not in self.active or not set(node['depends_on'])<=self.completed:
            raise_code('GRD-403',reason='graph_node_not_ready')
        if len(self.active)>self.authority['concurrency']:raise_code('GRD-403',reason='graph_concurrency_exceeded')

    async def node(self,node):
        p=self.projects;project=self.authority['project_id'];self.ready(node)
        basis=await p.store.append_graph_dispatch(project,self.reference,node['node_id'],authorize=lambda:p._authorize(write=True),ready=lambda:self.ready(node))
        attempt=await p.store.submit_graph_node(project,basis,authorize=lambda:p._authorize(write=True),live=lambda:self.ready(node))
        selection=dict(schema_version=1,attempt_ref={k:attempt[k] for k in ('attempt_id','revision','digest','source_seq')},agent_ref=node['agent_ref'],
            execution_id=node['execution_id'],confirmation_digest='0'*64,confirm_host_boundary=self.authority['selection']['confirm_host_boundary'],graph_node=basis)
        command=GraphNodeStartExecution.model_validate(selection)
        await p._execution_ready(command)
        describe=lambda:describe_execution(p,AgentVersionReference.model_validate(node['agent_ref']))
        authorize=lambda:p._authorize(write=True)
        preview=await p.store.preview_execution(project,command,authorize=authorize,describe=describe)
        command=command.model_copy(update={'confirmation_digest':preview['confirmation_digest']})
        materials=await p.store.capture_execution_materials(project,command,authorize=authorize,describe=describe)
        from pyharness.application.product_executions import require_material_reader
        require_material_reader(describe()['agent_snapshot'],materials)
        self.ready(node)
        _,key=node_keys(self.reference,node['node_id'])
        receipt,first=await p.store.authorize_execution(project,command,command_id=key,authorize=authorize,describe=describe)
        if not first:raise_code('PERS-202',reason='graph_node_dispatch_not_first')
        self.stop_targets[receipt['execution_id']]=dict(session_id=receipt['session_id'],task_id=receipt['task_id'],project_id=project,tenant_id=p.service.tenant_id,subject=self.actor,execution_ref={k:receipt[k] for k in ('execution_id','revision','digest','source_seq')})
        await p._launch_product_execution(receipt,materials)
        while True:
            self.check();result=await p.execution(project,node['execution_id'])
            if result['status'] in {'completed','failed','cancelled','interrupted','unknown'}:
                queue=p.service._queues.get(result['session_id'])
                if queue is not None and result['task_id'] in (queue.status().running,*queue.status().waiting):
                    await asyncio.sleep(.02);continue
                if result['status']!='completed' or result['unknown_effects']:raise_code('EVT-104',reason='graph_node_not_confirmed_complete')
                self.completed.add(node['node_id']);return
            await asyncio.sleep(.02)

    async def stop_children(self):
        targets=dict(self.stop_targets)
        try:
            resources,_=self.projects.store._cancellation_state()
            from pyharness.application.task_graph_control_checks import graph_basis
            for row in resources.values():
                value=row.get('execution_authorization')
                if not value or value['created_by']!=self.actor:continue
                attempt=self.projects.store._execution_attempt(resources,value['project_id'],value['selection']['attempt_ref'],self.actor)
                basis=graph_basis(self.projects.store,resources,attempt)
                if basis is None or basis['graph_run_ref']!=self.reference:continue
                targets[value['execution_id']]=dict(session_id=value['session_id'],task_id=value['task_id'],project_id=value['project_id'],tenant_id=self.projects.service.tenant_id,
                    subject=self.actor,execution_ref=self.projects.store._s05_ref(row,'execution_id','execution_authorization'))
        except PyHError:
            # These receipts only reduce permissions for exact already-owned
            # queues. They cannot read sources, create attempts or grant I/O.
            if not targets:raise
        for target in targets.values():
            queue=self.projects._owned_stop_queue(target)
            if queue is not None:await self.projects._signal_execution_stop(target,queue,'task_graph_stopped')

    async def run(self):
        pending={n['node_id']:n for n in self.authority['nodes']};running={}
        try:
            while pending or running:
                self.check()
                for ident in sorted(list(pending)):
                    node=pending[ident]
                    if len(running)>=self.authority['concurrency']:break
                    if not set(node['depends_on'])<=self.completed:continue
                    self.active.add(ident);pending.pop(ident);running[ident]=asyncio.create_task(self.node(node))
                if not running:raise_code('EVT-104',reason='graph_no_confirmed_ready_nodes')
                done,_=await asyncio.wait(list(running.values()),return_when=asyncio.FIRST_COMPLETED)
                for ident,task in list(running.items()):
                    if task not in done:continue
                    task.result();running.pop(ident);self.active.remove(ident)
        except BaseException as exc:
            self.error='cancelled' if isinstance(exc,asyncio.CancelledError) else str(getattr(exc,'code','graph_runtime_failed'))
            for task in running.values():task.cancel()
            await asyncio.gather(*running.values(),return_exceptions=True)
            try:await self.stop_children()
            except BaseException as secondary:exc.add_note('graph stop unavailable: '+type(secondary).__name__)
        finally:self.closed=True


@admitted
async def run_owned_graph(projects,owner):
    await owner.run()


async def stop_task_graphs(service):
    owners=list(getattr(service,'_task_graph_owners',{}).values())
    for owner in owners:
        if not owner.task.done():owner.task.cancel()
    await asyncio.gather(*(owner.task for owner in owners),return_exceptions=True)


async def graph_observation(projects,authority):
    project=authority['project_id'];resources,_,_=await projects.store._execution_read_state()
    reference={k:authority[k] for k in ('graph_run_id','revision','digest','source_seq')}
    cancelled=projects.store._graph_cancelled(resources,reference);nodes=[]
    owner=getattr(projects.service,'_task_graph_owners',{}).get(reference['graph_run_id']);live=owner is not None and not owner.closed and not owner.task.done()
    for plan in authority['nodes']:
        row=resources.get(plan['execution_id']);node=dict(plan,status='pending' if live else 'interrupted',unknown_effects=False)
        if row and 'execution_authorization' in row:
            result=await projects.execution(project,plan['execution_id'])
            node.update(status=result['status'],unknown_effects=result['unknown_effects'],execution_ref=projects.store._s05_ref(row,'execution_id','execution_authorization'),
                observed_seq=result['observed_seq'],result_summary=result.get('result_summary',''),artifacts=result.get('artifacts',[]))
        nodes.append(node)
    states={n['status'] for n in nodes}
    if states=={'completed'} and not any(n['unknown_effects'] for n in nodes):status='completed'
    elif cancelled:status='cancelling' if states&{'running','queued','preparing'} else 'cancelled'
    elif live:status='running'
    elif 'unknown' in states or any(n['unknown_effects'] for n in nodes):status='unknown'
    elif 'failed' in states:status='failed'
    else:status='interrupted'
    return dict(authority,nodes=nodes,status=status,business_acceptance='not_evaluated',scheduler_active=live,automatic_dispatch=False,
        cancel_requested=cancelled,cost_observation='runtime_estimate',runtime_error=owner.error if owner else None)
