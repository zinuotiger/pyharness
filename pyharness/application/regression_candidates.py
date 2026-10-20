"""Read original failed facts, with bounded public text and no replay authority."""
import hashlib
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.application.regression_contracts import FailureOrigin,public_evidence_text_allowed
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.product_executions import execution_projection
from pyharness.core.context_observation import public_request
from pyharness.errors import raise_code


def event_prefix_digest(events,through):
    digest=hashlib.sha256();digest.update(b'[');size=2;count=0
    for event in events:
        if event.seq>through:break
        raw=canonical(event.model_dump(mode='json'));size+=len(raw)+1;count+=1
        if count>10000 or size>16*1024*1024:raise_code('EVT-100',reason='regression_source_limit')
        if count>1:digest.update(b',')
        digest.update(raw)
    if not count or count!=through:raise_code('EVT-101',reason='regression_source_prefix_unavailable')
    digest.update(b']');return digest.hexdigest()


def public_text(view,role):
    messages=[m['content'] for m in view.get('messages',[]) if m.get('role')==role and type(m.get('content')) is str]
    if not messages:return None,'unavailable'
    text=messages[-1];state='withheld' if '[withheld:' in text else 'truncated' if view.get('truncated') or len(text)>2048 else 'available'
    if not public_evidence_text_allowed(text):return None,'withheld'
    return text[:2048],state


def independent_failure_reference(projects,row):
    from pyharness.application.delivery_support import delivery_ref
    store=projects.store;resources,_,_=store._delivery_read_snapshot()
    reference=store._s05_ref(row,'execution_id','execution_authorization')
    packages=[r for r in resources.values() if r.get('delivery_package',{}).get('selection',{}).get('execution_ref')==reference]
    if not packages:raise_code('EVT-101',reason='regression_acceptance_unavailable')
    package=max(packages,key=lambda r:r['source_seq']);fixed=delivery_ref(package,'delivery_package')
    reviews=[r for r in resources.values() if r.get('delivery_acceptance',{}).get('selection',{}).get('package_ref')==fixed]
    if not reviews:raise_code('EVT-101',reason='regression_acceptance_unavailable')
    review=max(reviews,key=lambda r:r['source_seq'])
    if store._acceptance_verdict(review['delivery_acceptance'])!='rejected':raise_code('EVT-101',reason='regression_acceptance_changed')
    return delivery_ref(review,'delivery_acceptance')


async def failure_candidate_view(projects,row,summary):
    service=projects.service
    log=await read_execution_log(service._tenant_sessions_dir(),row,service.tenant_id)
    return candidate_from_events(projects,row,summary,list(log.events_after(0)))


def candidate_from_events(projects,row,summary,events):
    from types import SimpleNamespace
    service=projects.service;value=row['execution_authorization']
    log=SimpleNamespace(sid=value['session_id'],events_after=lambda _:iter(events))
    projection=execution_projection(projects,row,summary,log)
    if projection['status']=='failed':kind='execution_failed';acceptance=None
    elif projection['business_acceptance']=='rejected':kind='business_failed';acceptance=independent_failure_reference(projects,row)
    else:raise_code('EVT-100',reason='regression_failure_required')
    origin=FailureOrigin(tenant_id=service.tenant_id,subject_ref=value['created_by'],
        execution_ref=projects.store._s05_ref(row,'execution_id','execution_authorization'),
        session_id=value['session_id'],observed_seq=events[-1].seq,prefix_digest=event_prefix_digest(events,events[-1].seq),
        failure_kind=kind,acceptance_ref=acceptance).model_dump()
    candidate=public_candidate(value['project_id'],origin,events)
    return dict(candidate,candidate_digest=digest_of(canonical(candidate)),status='confirmation_required',
        persisted=False,execution_authorized=False,historical_tools_replayed=False)


def public_candidate(project,origin,events):
    facts=[e for e in events if e.type=='execution.context' and e.payload['role']=='main_agent']
    input_text,input_state=public_text(facts[-1].payload['public_view'],'user') if facts else (None,'unavailable')
    answers=[dict(role='assistant',content=e.payload['content']) for e in events if e.type=='agent.message']
    public=public_request(dict(model='regression-public-output',messages=answers[-1:]),layer='adapter_input')
    output_text,output_state=public_text(public,'assistant')
    return dict(schema_version=1,project_id=project,origin=origin,
        observed_input_text=input_text,observed_input_state=input_state,
        observed_output_text=output_text,observed_output_state=output_state,
        root_tool_call_count=sum(e.type=='tool.call' for e in events),
        policy='bounded-public-regression-v1',assertion_version='regression-assertions-v1')
