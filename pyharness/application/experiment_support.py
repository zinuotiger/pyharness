"""Historical binding checks are independent of current privacy or platform state."""
from pyharness.application.experiment_plan import (experiment_ref,experiment_request,
    preparation_identity,preparation_plan)
from pyharness.application.regression_support import regression_ref,source_metadata
from pyharness.application.task_graph_contracts import TaskGraphCommand,TaskGraphNode
from pyharness.application.product_task_contracts import ProductTaskCommand
from pyharness.errors import PyHError,raise_code


def fixed_sources(store,resources,project,selection,actor,lookup):
    def exact(kind,reference):
        ref=reference.model_dump();key='dataset_id' if kind=='regression_dataset' else 'case_id'
        row=lookup(kind,ref[key],ref['revision'])
        if (row is None or row[kind]['project_id']!=project or row[kind]['created_by']!=actor or
                regression_ref(row,kind)!=ref):raise_code('EVT-101',reason='experiment_fixed_source_reference')
        return row
    dataset=exact('regression_dataset',selection.dataset_ref);cases=[];attempts=[]
    for ref in selection.case_refs:
        case=exact('regression_case',ref);origin=case['regression_case']['selection']['origin']
        source=source_metadata(store,resources,project,origin,actor)
        attempt=store._execution_attempt(resources,project,source['execution_authorization']['selection']['attempt_ref'],actor)
        cases.append(case);attempts.append(attempt)
    return dataset,cases,attempts


def graph_command(selection,plan,task_refs):
    nodes=[]
    for draft in plan['node_drafts']:
        values={k:v for k,v in draft.items() if k in TaskGraphNode.model_fields}
        nodes.append(dict(values,task_ref=task_refs[draft['task_id']]))
    return TaskGraphCommand(schema_version=1,graph_id=plan['graph_id'],project_revision=selection.project_revision,
        expected_revision=0,title=selection.title,nodes=nodes,concurrency=selection.concurrency,limits=selection.limits,retired=False)


def finish_key(preparation_id):return 'experiment-finish-'+preparation_id[4:]


def finish_request(definition):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='experiment.finish',definition=definition.model_dump())))


def check_definition_plan(store,resources,value,preparation,plan,lookup):
    from pyharness.application.workspace_store import canonical
    project=value['project_id'];actor=value['created_by'];ref=value['preparation_ref']
    if (experiment_ref(preparation,'experiment_preparation')!=ref or
            preparation['experiment_preparation']['selection']!=value['selection'] or
            value['submission_key']!=finish_key(ref['preparation_id']) or value['dataset_total']!=plan['dataset_total']):
        raise ValueError('experiment preparation basis')
    task_refs={}
    for binding,task_plan in zip(value['case_nodes'],plan['tasks'],strict=True):
        ref_task=binding['task_ref'];task=lookup('product_task',ref_task['product_task_id'],ref_task['revision'])
        if (task is None or task['product_task']['created_by']!=actor or task['product_task']['project_id']!=project or
                store._s05_ref(task,'product_task_id','product_task')!=ref_task or
                task_plan['command']['product_task_id']!=ref_task['product_task_id'] or
                {k:task['product_task'][k] for k in ProductTaskCommand.model_fields if k!='expected_revision'}!=
                {k:v for k,v in task_plan['command'].items() if k!='expected_revision'}):raise ValueError('prepared task differs')
        index=len(task_refs)
        if binding['left_node_id']!='c'+str(index)+'_left' or binding['right_node_id']!='c'+str(index)+'_right':
            raise ValueError('paired node differs')
        task_refs[ref_task['product_task_id']]=ref_task
    ref_graph=value['graph_ref'];graph=lookup('task_graph',ref_graph['graph_id'],ref_graph['revision'])
    expected=graph_command(preparation['experiment_preparation']['selection_model'],plan,task_refs)
    if (graph is None or graph['task_graph']['created_by']!=actor or graph['task_graph']['project_id']!=project or
            store._s05_ref(graph,'graph_id','task_graph')!=ref_graph or graph['source_seq']<=value['preparation_ref']['source_seq'] or
            canonical({k:graph['task_graph'][k] for k in TaskGraphCommand.model_fields if k!='expected_revision'})!=
            canonical(expected.model_dump(exclude={'expected_revision'}))):raise ValueError('prepared graph differs')


def validate_experiment_record(store,definition,resources,previous,event,kind,history):
    if kind=='experiment_report':
        from pyharness.application.experiment_report_binding import validate_report_record
        return validate_report_record(store,definition,resources,previous,event,history)
    if kind in {'experiment_run','experiment_cancellation'}:
        from pyharness.application.experiment_run_binding import validate_run_record
        return validate_run_record(store,definition,resources,previous,event,kind,history)
    from pyharness.application.experiment_contracts import ConfirmExperimentPreparation
    try:
        value=definition.model_dump();project=value['project_id'];actor=value['created_by'];selection=definition.selection
        store._s05_base(value,resources,previous,event,kind)
        if value['submission_key']!=event.payload['command_id']:raise ValueError('experiment command identity')
        lookup=lambda k,i,r:history.get((k,i,r))
        dataset,cases,attempts=fixed_sources(store,resources,project,selection,actor,lookup)
        plan=preparation_plan(store,project,selection,dataset,cases,attempts,actor)
        if selection.confirmation_digest!=plan['confirmation_digest']:raise ValueError('preparation plan digest')
        if kind=='experiment_preparation':
            if previous or value['preparation_id']!=preparation_identity(store.tenant_id,actor,project,value['submission_key']):
                raise ValueError('preparation identity')
            prior=resources.get(selection.experiment_id,{}).get('experiment_definition')
            if (prior['revision'] if prior else 0)!=selection.expected_revision:raise ValueError('preparation target revision')
            if experiment_request(project,selection)!=event.payload['request_digest']:raise ValueError('preparation request')
        else:
            ref=value['preparation_ref'];p=lookup('experiment_preparation',ref['preparation_id'],1)
            if p is None:raise ValueError('preparation missing')
            p=dict(p,experiment_preparation=dict(p['experiment_preparation'],selection_model=ConfirmExperimentPreparation.model_validate(p['experiment_preparation']['selection'])))
            check_definition_plan(store,resources,value,p,plan,lookup)
            if finish_request(definition)!=event.payload['request_digest']:raise ValueError('experiment request')
    except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='experiment_history_binding')
