"""Deterministic paired tasks over exact already-captured source data, never tools."""
from pyharness.application.experiment_contracts import PrepareExperiment
from pyharness.application.product_task_contracts import ProductTaskCommand
from pyharness.application.run_attempt_contracts import AttemptContext
from pyharness.errors import raise_code


def experiment_id_for(prefix,tenant,actor,project,selection,part):
    from pyharness.application.workspace_store import canonical,digest_of
    return prefix+digest_of(canonical(dict(tenant=tenant,actor=actor,project=project,
        experiment_id=selection.experiment_id,revision=selection.expected_revision+1,part=part)))[:16]


def preparation_identity(tenant,actor,project,key):
    from pyharness.application.workspace_store import canonical,digest_of
    return 'prp-'+digest_of(canonical(dict(tenant=tenant,actor=actor,project=project,key=key)))[:16]


def preparation_plan(store,project,command,dataset,cases,attempts,actor):
    from pyharness.application.workspace_store import canonical,digest_of
    selected=PrepareExperiment.model_validate(command.model_dump(include=set(PrepareExperiment.model_fields)))
    raw=selected.model_dump();available=dataset['regression_dataset']['selection']['case_refs']
    if any(ref not in available for ref in raw['case_refs']):raise_code('EVT-101',reason='experiment_case_not_in_dataset')
    tasks=[];drafts=[]
    for index,(case,attempt) in enumerate(zip(cases,attempts,strict=True)):
        value=case['regression_case'];source=attempt['run_attempt'];ref=raw['case_refs'][index]
        ident=experiment_id_for('pt-',store.tenant_id,actor,project,selected,dict(case_ref=ref))
        task=ProductTaskCommand(schema_version=2,product_task_id=ident,expected_revision=0,
            project_revision=selected.project_revision,title=value['selection']['title'],
            objective=value['selection']['input_text'],input_binding=source['prerequisites']['input_binding'],
            acceptance_criteria=['Satisfy fixed regression assertions; independent business acceptance remains separate.'],retired=False)
        context=AttemptContext(**dict(source['context'],delivery_ref=None)).model_dump()
        tasks.append(dict(case_ref=ref,command=task.model_dump()))
        for side in ('left','right'):
            drafts.append(dict(context,node_id='c'+str(index)+'_'+side,depends_on=[],side=side,
                task_id=ident,case_ref=ref,agent_ref=raw[side+'_agent_ref'],label=value['selection']['title'][:85]+' / '+side))
    result=dict(schema_version=1,project_id=project,experiment_id=selected.experiment_id,
        revision=selected.expected_revision+1,title=selected.title,dataset_ref=raw['dataset_ref'],case_refs=raw['case_refs'],
        dataset_total=len(available),selected_count=len(cases),excluded_count=len(available)-len(cases),
        graph_id=experiment_id_for('tg-',store.tenant_id,actor,project,selected,'paired-graph'),
        concurrency=selected.concurrency,limits=raw['limits'],tasks=tasks,node_drafts=drafts,
        status='confirmation_required',persisted=False,execution_authorized=False,
        input_policy='explicit-manual-case-with-original-captured-input-v1')
    confirmation=digest_of(canonical(dict(operation='experiment.prepare',tenant_id=store.tenant_id,
        subject_ref=actor,project_id=project,selection=raw,plan=result)))
    return dict(result,confirmation_digest=confirmation)


def experiment_request(project,selection):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='experiment.prepare',project_id=project,selection=selection.model_dump())))


def experiment_ref(row,kind):
    from pyharness.application.experiment_records import EXPERIMENT_MODELS
    key=EXPERIMENT_MODELS[kind][1];v=row[kind]
    return {key:v[key],'revision':v['revision'],'digest':row['digest'],'source_seq':row['source_seq']}


def experiment_summary(row):
    from pyharness.application.workspace_store import canonical
    return dict(row['experiment_definition'],digest=row['digest'],source_seq=row['source_seq'],created_at=row['created_at'],
        record_canonical=canonical(row['experiment_definition']).decode('utf-8'),persisted=True,status='prepared',execution_authorized=False)
