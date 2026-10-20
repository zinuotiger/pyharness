"""Historical lineage checks on the existing control log, with no host/model I/O."""
from pyharness.application.continuation_execution_contracts import ContinuedStartExecution,continued_attempt_digest
from pyharness.errors import raise_code


def attempt_authority_digest(value):
    from pyharness.application.workspace_store import canonical,digest_of
    if value['schema_version']==3:
        from pyharness.application.task_graph_execution_contracts import graph_attempt_digest
        return graph_attempt_digest(value['prerequisites'],value['context'],value['graph_node'])
    if value['schema_version']==2:return continued_attempt_digest(value['prerequisites'],value['context'],value['continuation'])
    return digest_of(canonical(dict(prerequisites=value['prerequisites'],context=value['context'])))


def continuation_source(store,resources,basis,actor):
    plan=basis.plan;command=basis.command
    root=store._cancel_target(resources,plan.project_id,command.execution_ref,actor);value=root['execution_authorization']
    prior=value['selection'].get('continuation')
    expected=[*prior['plan']['journals'],*prior['plan'].get('lineage_journals',[])] if prior else []
    if [j.model_dump() for j in plan.lineage_journals]!=expected:raise_code('PERS-202',reason='continuation_lineage_binding')
    if (plan.state.session_id!=value['session_id'] or plan.current_model_fingerprint!=value['environment']['model_fingerprint'] or
            plan.execution_status not in ('interrupted','failed','cancelled') or
            command.attempt_id==value['selection']['attempt_ref']['attempt_id']):
        raise_code('PERS-202',reason='continuation_source_binding')
    return root


def validate_continued_attempt(store,definition,resources,event):
    value=definition.model_dump();basis=definition.continuation;actor=value['created_by']
    root=continuation_source(store,resources,basis,actor)
    source=root['execution_authorization'];origin=store._execution_attempt(resources,value['project_id'],source['selection']['attempt_ref'],actor)
    expected=dict(origin['run_attempt']['selection'],attempt_id=value['attempt_id'])
    if (value['selection']!=expected or value['project_revision']!=source['project_revision'] or
            value['submission_key']!='continuation-attempt-'+basis.command.execution_id):
        raise_code('PERS-202',reason='continued_attempt_binding')
    for row in resources.values():
        prior=row.get('run_attempt',{}).get('continuation')
        if (prior and prior['command']['execution_ref']==basis.command.execution_ref.model_dump() and
                prior['plan']['state']['state_digest']==basis.plan.state.state_digest):
            raise_code('PERS-202',reason='continuation_checkpoint_already_reserved')
        if prior and prior['submission_key']==basis.submission_key and row['created_by']==actor:
            raise_code('PERS-202',reason='continuation_submission_already_reserved')


def validate_continued_selection(command,attempt):
    basis=attempt['run_attempt'].get('continuation')
    if basis is None:
        if isinstance(command,ContinuedStartExecution):raise_code('GRD-403',reason='continuation_attempt_required')
    elif not isinstance(command,ContinuedStartExecution) or command.continuation.model_dump()!=basis:
        raise_code('GRD-403',reason='continued_attempt_requires_exact_lineage')


def validate_continued_authorization(store,definition,resources,attempt):
    value=definition.model_dump();is_continued=value['schema_version']==2
    if is_continued!=(attempt['run_attempt']['schema_version']==2):raise_code('PERS-202',reason='continued_authorization_schema')
    if not is_continued:return
    validate_continued_selection(definition.selection,attempt)
    root=continuation_source(store,resources,definition.selection.continuation,value['created_by'])
    source=root['execution_authorization']
    if (value['environment']!=source['environment'] or value['selection']['agent_ref']!=source['selection']['agent_ref'] or
            value['session_id']==source['session_id'] or value['task_id']==source['task_id'] or
            any(r.get('execution_authorization',{}).get('session_id')==value['session_id'] for r in resources.values())):
        raise_code('PERS-202',reason='continued_authorization_binding')
