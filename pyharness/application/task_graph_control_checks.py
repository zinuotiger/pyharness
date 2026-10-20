"""Source-independent graph stop scope, plus live scheduler ownership reduction."""
from pyharness.errors import raise_code


def validate_minimal_graph_run(store,definition,resources,event,previous):
    value=definition.model_dump();actor=value['created_by'];project=value['project_id']
    from pyharness.application.task_graph_control_store import control_request
    if (previous or definition.tenant_id!=store.tenant_id or actor!=event.payload['actor_ref'] or
            definition.submission_key!=event.payload['command_id'] or
            control_request(project,'task_graph.start',value['selection'])!=event.payload['request_digest']):
        raise_code('PERS-202',reason='graph_run_control_binding')
    store._context_project(resources,project,definition.project_revision)
    from pyharness.application.experiment_run_binding import validate_funded_graph
    validate_funded_graph(store,definition,resources,event)
    for node in definition.nodes:
        p=node.prerequisites;parent=resources[project]
        if p.project_digest!=parent['digest'] or p.project_source_seq!=parent['source_seq']:
            raise_code('PERS-202',reason='graph_run_project_binding')


def graph_basis(store,resources,attempt):
    value=attempt['run_attempt'];seen=set()
    for _ in range(21):
        if value.get('graph_node'):return value['graph_node']
        lineage=value.get('continuation')
        if not lineage:return None
        ident=lineage['command']['execution_ref']['execution_id']
        if ident in seen:raise_code('PERS-202',reason='graph_continuation_cycle')
        seen.add(ident);source=resources.get(ident,{}).get('execution_authorization')
        if source is None:raise_code('PERS-202',reason='graph_continuation_parent_missing')
        value=store._execution_attempt(resources,source['project_id'],source['selection']['attempt_ref'],source['created_by'])['run_attempt']
    raise_code('PERS-202',reason='graph_continuation_depth')


def check_live_graph(projects,resources,attempt):
    basis=graph_basis(projects.store,resources,attempt)
    if basis is None:return
    reference=basis['graph_run_ref'];run=projects.store._graph_control_run(resources,attempt['run_attempt']['project_id'],reference,attempt['created_by'])
    if projects.store._graph_cancelled(resources,reference):
        import asyncio
        raise asyncio.CancelledError('task_graph_cancel_requested')
    owner=getattr(projects.service,'_task_graph_owners',{}).get(reference['graph_run_id'])
    if (owner is None or owner.reference!=reference or owner.closed or owner.task.done() or
            owner.actor!=attempt['created_by'] or basis['node_id'] not in owner.active):
        raise_code('GRD-403',reason='graph_scheduler_not_active')
    if len(owner.active)>run['task_graph_run']['concurrency']:raise_code('GRD-403',reason='graph_concurrency_exceeded')
