"""Read fixed facts and apply finite checks; no runtime or release authority."""
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from pyharness.application.regression_assertions import evaluate_assertions,VERSION
from pyharness.application.regression_candidates import event_prefix_digest,public_text
from pyharness.application.run_observation_comparison import usage_metrics,alignment
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.context_observation import public_request
from pyharness.errors import raise_code


def checker_implementation():return digest_of(Path(__file__).with_name('regression_assertions.py').read_bytes())


def report_counts(measurements):
    execution=Counter(m['execution_status'] for m in measurements);quality=Counter(m['quality']['verdict'] for m in measurements)
    business=Counter(m['business_acceptance'] for m in measurements);total=len(measurements)
    return dict(execution_counts=dict(total=total,**{k:execution[k] for k in ('completed','failed','cancelled','unknown','interrupted','not_dispatched','nonterminal')}),
        quality_counts=dict(total=total,evaluable=quality['passed']+quality['failed'],**{k:quality[k] for k in ('passed','failed','unknown')}),
        business_counts=dict(total=total,evaluated=business['accepted']+business['rejected'],
            **{k:business[k] for k in ('accepted','rejected','invalidated','not_evaluated')}))


def measured_fact(projects,resources,row,summary,attempt,node,assertions,journals):
    root=journals[0];value=row['execution_authorization']
    life=[e.payload['phase'] for e in root if e.type=='execution.lifecycle'];phase=life[-1] if life else None
    status=phase if phase in ('completed','failed','cancelled','interrupted') else 'nonterminal'
    if phase=='bound' and any(e.type=='task.failed' and e.payload.get('reason')=='cancelled' for e in root):status='cancelled'
    effects=[e for events in journals for e in events if e.type=='execution.effect']
    unknown=False
    for events in journals:
        outcomes={e.payload['started_seq'] for e in events if e.type=='execution.effect' and e.payload['phase']!='started'}
        unknown=unknown or any(e.type=='execution.effect' and (e.payload['phase']=='unknown' or
            (e.payload['phase']=='started' and e.seq not in outcomes) or e.payload.get('file_observation')=='unavailable') for e in events)
    messages=[e.payload['content'] for e in root if e.type=='agent.message']
    view=public_request(dict(model='experiment-public-output',messages=[dict(role='assistant',content=v) for v in messages[-1:]]),layer='adapter_input')
    output,state=public_text(view,'assistant')
    complete=status=='completed' and not unknown and state=='available' and not view['withheld'] and not view['truncated']
    quality=evaluate_assertions(assertions,output=output,execution_status='unknown' if unknown else status,output_complete=complete)
    corrections=row.get('_trusted_corrections',[])
    p=attempt['prerequisites'];context=attempt['context'];snapshot=value['environment']['agent_snapshot']
    basis=dict(available=attempt['schema_version']==3 and not corrections,
        input_digest=digest_of(canonical(dict(task_ref=p['task_ref'],input_binding=p['input_binding'],
            document_refs=context['document_refs'],citation_refs=context['citation_refs']))),
        validator_digest=digest_of(canonical(dict(output_schema=snapshot.get('output_schema'),delivery_ref=context['delivery_ref']))))
    return dict(node_id=node['node_id'],agent_ref=node['agent_ref'],execution_ref=projects.store._s05_ref(row,'execution_id','execution_authorization'),
        execution_status='unknown' if unknown else status,unknown_effects=unknown,output_state=state,
        output_digest=digest_of(canonical(messages)) if messages else None,quality=quality,basis=basis,
        usage=usage_metrics(journals),business_acceptance=summary.get('business_acceptance','not_evaluated'))


def unavailable_fact(node,assertions,status='not_dispatched'):
    return dict(node_id=node['node_id'],agent_ref=node['agent_ref'],execution_ref=None,execution_status=status,
        unknown_effects=False,output_state='unavailable',output_digest=None,
        quality=evaluate_assertions(assertions,output=None,execution_status=status,output_complete=False),
        basis=dict(available=False,input_digest=None,validator_digest=None),usage=usage_metrics([]),business_acceptance='not_evaluated')


async def read_node(projects,resources,node,assertions,expected,*,fixed,actor,authorize,started,scan_budget,validation_resources):
    from pyharness.application.product_execution_sessions import read_execution_log
    from pyharness.application.execution_child_reader import read_child_log
    from pyharness.application.execution_child_graph import child_admissions
    store=projects.store;row=resources.get(node['execution_id'])
    if row is None or 'execution_authorization' not in row:
        if fixed and expected is not None:raise_code('EVT-101',reason='experiment_report_execution_prefix')
        return unavailable_fact(node,assertions),None
    summary=store._execution_summary(resources,row,actor,authorize,started)
    read_row=store._execution_read_row(resources,row)
    if fixed and expected is None:return unavailable_fact(node,assertions,'interrupted'),None
    directory=projects.service._tenant_sessions_dir();tenant=projects.service.tenant_id
    current=store._context_owned(validation_resources,node['execution_id'],'execution_authorization',row['execution_authorization']['project_id'],actor)
    if store._s05_ref(current,'execution_id','execution_authorization')!=store._s05_ref(row,'execution_id','execution_authorization'):
        raise_code('PERS-202',reason='experiment_report_execution_changed')
    store._execution_summary(validation_resources,current,actor,authorize,started)
    validated_row=store._execution_read_row(validation_resources,current)
    def physical_capacity(sid):
        from pyharness.persistence import session_data_paths
        try:scan_budget['physical_bytes']-=sum(p.stat().st_size for p in session_data_paths(directory/(sid+'.jsonl')))
        except OSError:raise_code('PERS-202',reason='experiment_report_log_unavailable')
        if scan_budget['physical_bytes']<0:raise_code('EVT-100',reason='experiment_report_physical_scan_limit')
    physical_capacity(current['execution_authorization']['session_id'])
    root=await read_execution_log(directory,validated_row,tenant);values=list(root.events_after(0))
    refs={p['session_id']:p for p in expected} if expected is not None else {}
    def prefix(sid,events):
        if fixed and sid not in refs:raise_code('EVT-101',reason='experiment_report_child_prefix')
        through=refs[sid]['observed_seq'] if fixed else events[-1].seq
        digest=event_prefix_digest(events,through)
        if fixed and digest!=refs[sid]['prefix_digest']:raise_code('EVT-101',reason='experiment_report_prefix_changed')
        selected=[e for e in events if e.seq<=through]
        scan_budget['events']-=len(selected)
        scan_budget['bytes']-=sum(len(canonical(e.model_dump(mode='json'))) for e in selected)
        if min(scan_budget.values())<0:raise_code('EVT-100',reason='experiment_report_aggregate_scan_limit')
        return selected,dict(session_id=sid,observed_seq=through,prefix_digest=digest)
    events,rootref=prefix(root.sid,values);journals=[events];prefixes=[rootref]
    admissions=child_admissions(events,read_row)
    if len(admissions)>31:raise_code('EVT-100',reason='experiment_report_child_limit')
    if fixed and set(refs)!={root.sid,*admissions}:raise_code('EVT-101',reason='experiment_report_child_coverage')
    for sid in sorted(admissions):
        physical_capacity(sid)
        child=await read_child_log(directory,validated_row,tenant,sid);child_events,ref=prefix(sid,list(child.events_after(0)))
        journals.append(child_events);prefixes.append(ref)
    attempt=store._execution_attempt(resources,row['execution_authorization']['project_id'],row['execution_authorization']['selection']['attempt_ref'],actor)['run_attempt']
    return measured_fact(projects,resources,read_row,summary,attempt,node,assertions,journals),prefixes


def case_comparison(reference,left,right):
    reasons=alignment(left['basis'],right['basis']);regressions=[]
    if not reasons:
        if left['quality']['verdict']=='passed' and right['quality']['verdict']=='failed':regressions.append(dict(case_ref=reference,kind='quality_regression'))
        if left['execution_status']=='completed' and right['execution_status']=='failed':regressions.append(dict(case_ref=reference,kind='execution_regression'))
    return dict(case_ref=reference,left=left,right=right,comparison_status='not_comparable' if reasons else 'aligned',incomparable_reasons=reasons),regressions
