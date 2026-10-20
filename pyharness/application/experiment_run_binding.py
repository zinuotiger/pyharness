"""Immutable funding proofs; minimal cancellation reads never open source bodies."""
from pyharness.application.experiment_run_contracts import PreviewExperimentRun
from pyharness.application.experiment_plan import experiment_ref
from pyharness.errors import PyHError,raise_code


def graph_identity(tenant,actor,project,run_id):
    from pyharness.application.workspace_store import canonical,digest_of
    suffix=digest_of(canonical([tenant,actor,project,run_id]))[:16]
    return 'gr-'+suffix,'experiment-graph-'+suffix


def run_confirmation(tenant,actor,project,selection,plan):
    from pyharness.application.workspace_store import canonical,digest_of
    raw=PreviewExperimentRun.model_validate({k:getattr(selection,k) for k in PreviewExperimentRun.model_fields}).model_dump()
    return digest_of(canonical(dict(operation='experiment.run',tenant_id=tenant,actor=actor,
        project_id=project,selection=raw,graph_plan=plan.model_dump())))


def run_request(project,selection,kind='experiment.start'):
    from pyharness.application.task_graph_control_store import control_request
    return control_request(project,kind,selection.model_dump())


def validate_run_record(store,definition,resources,previous,event,kind,history=None):
    try:
        value=definition.model_dump();actor=value['created_by'];project=value['project_id']
        if previous or actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id']:
            raise ValueError('experimental control owner')
        if not resources.get(project,{}).get('project'):raise ValueError('experimental control project')
        store._context_quota(resources,kind,project)
        if kind=='experiment_run':
            store._context_project(resources,project,definition.project_revision)
            ident,key=graph_identity(store.tenant_id,actor,project,definition.experiment_run_id)
            if (definition.graph_command.graph_run_id!=ident or ident in resources or
                    definition.selection.confirmation_digest!=run_confirmation(store.tenant_id,actor,project,definition.selection,definition.graph_plan) or
                    run_request(project,definition.selection)!=event.payload['request_digest']):raise ValueError('experimental funding binding')
            if history is not None:
                ref=definition.selection.experiment_ref
                row=history.get(('experiment_definition',ref.experiment_id,ref.revision))
                if (row is None or experiment_ref(row,'experiment_definition')!=ref.model_dump() or
                        row['experiment_definition']['created_by']!=actor or row['experiment_definition']['project_id']!=project or
                        row['experiment_definition']['project_revision']!=definition.project_revision or
                        row['experiment_definition']['graph_ref']!=definition.graph_command.graph_ref.model_dump()):
                    raise ValueError('experimental definition binding')
                expected={}
                sel=row['experiment_definition']['selection']
                for binding in row['experiment_definition']['case_nodes']:
                    for side in ('left','right'):expected[binding[side+'_node_id']]=(binding['task_ref'],sel[side+'_agent_ref'])
                actual={n.node_id:(n.task_ref.model_dump(),n.agent_ref.model_dump()) for n in definition.graph_plan.nodes}
                if actual!=expected or any(n.depends_on for n in definition.graph_plan.nodes):raise ValueError('experimental pair coverage')
        else:
            ref=definition.experiment_run_ref.model_dump();row=control_run(store,resources,project,ref,actor)
            if cancel_requested(resources,ref):raise ValueError('experimental cancellation duplicate')
            if run_request(project,definition,kind='experiment.cancel')!=event.payload['request_digest']:
                # Server ownership fields are not client command fields.
                from pyharness.application.experiment_run_contracts import CancelExperimentRun
                command=CancelExperimentRun.model_validate({k:value[k] for k in CancelExperimentRun.model_fields})
                if run_request(project,command,'experiment.cancel')!=event.payload['request_digest']:raise ValueError('experimental cancellation request')
    except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='experiment_run_history_binding')


def control_run(store,resources,project,reference,actor):
    if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='experiment_control_project_unavailable')
    row=store._context_owned(resources,reference['experiment_run_id'],'experiment_run',project,actor)
    if experiment_ref(row,'experiment_run')!=reference:raise_code('EVT-101',reason='experiment_run_reference')
    return row


def cancel_requested(resources,reference):
    return any(r.get('experiment_cancellation',{}).get('experiment_run_ref')==reference for r in resources.values())


def matching_graph(store,resources,row):
    value=row['experiment_run'];ident,key=graph_identity(store.tenant_id,value['created_by'],value['project_id'],value['experiment_run_id'])
    graph=resources.get(ident)
    if graph is None:return None
    try:
        run=graph['task_graph_run']
        if (run['selection']!=value['graph_command'] or run['created_by']!=value['created_by'] or
                run['project_id']!=value['project_id'] or run['submission_key']!=key or graph['source_seq']<=row['source_seq'] or
                run['allocated_budget']!=value['graph_plan']['allocated_budget']):raise ValueError('funded graph mismatch')
        nodes=[dict(node_id=n['node_id'],depends_on=n['depends_on'],agent_ref=n['agent_ref'],task_ref=n['prerequisites']['task_ref'],
            boundary=n['environment']['boundary'],model_fingerprint=n['environment']['model_fingerprint'],allocated_budget=n['allocated_budget']) for n in run['nodes']]
        if nodes!=value['graph_plan']['nodes']:raise ValueError('funded node mismatch')
    except (ValueError,KeyError):raise_code('PERS-202',reason='experiment_funded_graph_binding')
    return graph


def validate_funded_graph(store,definition,resources,event):
    ident=definition.graph_run_id
    for row in resources.values():
        if row.get('experiment_run',{}).get('graph_command',{}).get('graph_run_id')!=ident:continue
        candidate=dict(resources)
        candidate[ident]=dict(task_graph_run=definition.model_dump(),digest=event.payload['digest'],source_seq=event.seq)
        matching_graph(store,candidate,row)
