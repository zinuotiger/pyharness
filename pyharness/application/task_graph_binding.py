"""Exact graph-node control bindings; these references never grant source reads."""
from pyharness.application.task_graph_execution_contracts import GraphNodeStartExecution
from pyharness.errors import raise_code


def node_keys(reference,node_id):
    suffix=reference['graph_run_id']+'-'+node_id
    return 'graph-attempt-'+suffix,'graph-execution-'+suffix


def graph_node_plan(store,resources,project,basis,actor):
    value=basis.model_dump() if hasattr(basis,'model_dump') else basis
    reference=value['graph_run_ref'];row=store._context_owned(resources,reference['graph_run_id'],'task_graph_run',project,actor)
    if store._s05_ref(row,'graph_run_id','task_graph_run')!=reference:raise_code('PERS-202',reason='graph_node_run_binding')
    nodes=[n for n in row['task_graph_run']['nodes'] if n['node_id']==value['node_id']]
    if len(nodes)!=1:raise_code('PERS-202',reason='graph_node_plan_missing')
    return row,nodes[0]


def validate_graph_attempt(store,definition,resources,event):
    value=definition.model_dump();basis=value['graph_node'];_,plan=graph_node_plan(store,resources,value['project_id'],basis,value['created_by'])
    key,_=node_keys(basis['graph_run_ref'],basis['node_id'])
    intents=[r for r in resources.values() if r.get('task_graph_dispatch',{}).get('graph_run_ref')==basis['graph_run_ref'] and r['task_graph_dispatch']['node_id']==basis['node_id']]
    if (len(intents)!=1 or intents[0]['source_seq']!=basis['intent_seq'] or intents[0]['digest']!=basis['intent_digest']):
        raise_code('PERS-202',reason='graph_dispatch_intent_binding')
    expected=dict(plan['context'],attempt_id=plan['attempt_id'],task_ref=plan['prerequisites']['task_ref'])
    if (value['attempt_id']!=plan['attempt_id'] or value['selection']!=expected or value['prerequisites']!=plan['prerequisites'] or
            value['context']!=plan['context'] or value['submission_key']!=key):raise_code('PERS-202',reason='graph_attempt_plan_binding')


def validate_graph_selection(command,attempt):
    basis=attempt['run_attempt'].get('graph_node')
    if basis is None:
        if isinstance(command,GraphNodeStartExecution):raise_code('GRD-403',reason='graph_attempt_required')
    elif not isinstance(command,GraphNodeStartExecution) or command.graph_node.model_dump()!=basis:
        raise_code('GRD-403',reason='graph_node_requires_exact_admission')


def validate_graph_authorization(store,definition,resources,attempt):
    value=definition.model_dump();is_graph=value['schema_version']==3
    if is_graph!=(attempt['run_attempt']['schema_version']==3):raise_code('PERS-202',reason='graph_authorization_schema')
    if not is_graph:return
    validate_graph_selection(definition.selection,attempt)
    basis=definition.selection.graph_node;_,plan=graph_node_plan(store,resources,value['project_id'],basis,value['created_by'])
    _,key=node_keys(basis.graph_run_ref.model_dump(),basis.node_id)
    if (value['execution_id']!=plan['execution_id'] or value['environment']!=plan['environment'] or
            value['selection']['agent_ref']!=plan['agent_ref'] or value['submission_key']!=key):raise_code('PERS-202',reason='graph_execution_plan_binding')


def reserved_graph_identities(resources,project=None):
    attempts=set();executions=set()
    for row in resources.values():
        run=row.get('task_graph_run')
        if not run or (project is not None and run['project_id']!=project):continue
        attempts.update(node['attempt_id'] for node in run['nodes']);executions.update(node['execution_id'] for node in run['nodes'])
    return attempts,executions


def graph_attempt_capacity(store,resources,project,*,additional=0):
    attempts,_=reserved_graph_identities(resources,project)
    attempts.update(row['run_attempt']['attempt_id'] for row in resources.values() if row.get('run_attempt',{}).get('project_id')==project)
    if len(attempts)+additional>store.MAX_CONTEXT_HISTORY:raise_code('EVT-100',reason='graph_node_attempt_capacity')
