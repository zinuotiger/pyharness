"""Compare exact original facts; absent measurements and quality remain unknown."""
from collections import Counter
from datetime import datetime
from pyharness.application.workspace_store import canonical,digest_of


def fixed_basis(attempt,snapshot,corrections):
    # A continuation or correction changes the effective input history. Until
    # its full lineage has an immutable comparison contract, do not guess.
    if attempt['schema_version']!=1 or corrections:return dict(available=False)
    p=attempt['prerequisites'];context=attempt['context']
    return dict(available=True,input_digest=digest_of(canonical(dict(
        task_ref=p['task_ref'],input_binding=p['input_binding'],
        document_refs=context['document_refs'],citation_refs=context['citation_refs']))),
        validator_digest=digest_of(canonical(dict(output_schema=snapshot.get('output_schema'),
            delivery_ref=context['delivery_ref'],comparison_contract='fixed-declared-validation-v1'))))


def alignment(left,right):
    if not left['available'] or not right['available']:return ['input_alignment_unavailable']
    result=[]
    if left['input_digest']!=right['input_digest']:result.append('fixed_inputs_differ')
    if left['validator_digest']!=right['validator_digest']:result.append('validators_differ')
    return result


def usage_metrics(journals):
    usages=[];complete=True;requests=0
    for events in journals:
        req=Counter(e.payload['model'] for e in events if e.type=='llm.request')
        values=[e.payload for e in events if e.type=='llm.usage'];usages.extend(values)
        observed=Counter(v['model'] for v in values)
        requests+=sum(req.values())
        if req!=observed:complete=False
    complete=complete and bool(usages)
    input_known=complete and all(not v.get('input_unknown',False) for v in usages)
    price_known=input_known and all(v.get('cost_est') is not None for v in usages)
    return dict(request_records=requests,usage_records=len(usages),usage_complete=complete,
        input_tokens=sum(v['in_tokens'] for v in usages) if input_known else None,
        output_tokens=sum(v['out_tokens'] for v in usages) if complete else None,
        cost_est=sum(v['cost_est'] for v in usages) if price_known else None,
        usage_precision=('estimated' if any(v.get('estimated',False) for v in usages) else 'recorded') if complete else 'unavailable',
        price_status='recorded_estimate' if price_known else 'unavailable')


async def run_view(projects,row,summary,attempt):
    from pyharness.application.product_execution_sessions import read_execution_log
    from pyharness.application.execution_child_reader import read_child_log
    from pyharness.application.execution_child_graph import child_admissions
    from pyharness.application.product_executions import execution_projection
    from pyharness.core.context_observation import public_request
    service=projects.service;directory=service._tenant_sessions_dir()
    root=await read_execution_log(directory,row,service.tenant_id);events=list(root.events_after(0))
    journals=[events];prefixes=[dict(session_id=root.sid,observed_seq=events[-1].seq)]
    for sid in child_admissions(events,row):
        child=await read_child_log(directory,row,service.tenant_id,sid);values=list(child.events_after(0))
        journals.append(values);prefixes.append(dict(session_id=sid,observed_seq=values[-1].seq))
    projection=execution_projection(projects,row,summary,root)
    messages=[dict(role='assistant',content=e.payload['content']) for e in events if e.type=='agent.message']
    public=public_request(dict(model='comparison-public-output',messages=messages),layer='adapter_input')
    life=[e for e in events if e.type=='execution.lifecycle'];starts=[e for e in life if e.payload['phase']=='running']
    ends=[e for e in life if e.payload['phase'] in ('completed','failed','cancelled')]
    seconds=None
    if starts and ends:
        seconds=(datetime.fromisoformat(ends[-1].ts)-datetime.fromisoformat(starts[0].ts)).total_seconds()
        if seconds<0:seconds=None
    snapshot=row['execution_authorization']['environment']['agent_snapshot']
    return dict(execution_id=summary['execution_id'],attempt_ref=summary['attempt_ref'],agent_ref=summary['agent_ref'],
        model_fingerprint=summary['model_fingerprint'],status=projection['status'],
        business_acceptance=projection['business_acceptance'],unknown_effects=projection['unknown_effects'],
        duration_seconds=seconds,usage=usage_metrics(journals),journal_prefixes=prefixes,
        output_digest=digest_of(canonical(messages)),public_output=public,
        basis=fixed_basis(attempt,snapshot,row.get('_trusted_corrections',[])))


def compare_runs(left,right):
    reasons=alignment(left['basis'],right['basis']);regressions=[]
    terminal={'completed','failed','cancelled'}
    status='not_comparable' if reasons else 'aligned' if left['status'] in terminal and right['status'] in terminal else 'pending'
    if status=='aligned' and left['status']=='completed' and right['status']=='failed':
        regressions.append(dict(kind='execution_regression',from_status='completed',to_status='failed'))
    deltas={}
    for key in ('input_tokens','output_tokens','cost_est'):
        a=left['usage'][key];b=right['usage'][key]
        deltas[key]=b-a if status=='aligned' and a is not None and b is not None else None
    a=left['duration_seconds'];b=right['duration_seconds']
    deltas['duration_seconds']=b-a if status=='aligned' and a is not None and b is not None else None
    return dict(schema_version=1,comparison_status=status,incomparable_reasons=reasons,
        left=left,right=right,regressions=regressions,deltas=deltas,
        output_changed=left['output_digest']!=right['output_digest'] if status=='aligned' else None,
        business_quality_verdict='not_evaluated',validator_alignment_scope='declared_output_schema_and_fixed_delivery',
        validator_implementation_version='not_recorded',execution_authorized=False,persisted=False)
