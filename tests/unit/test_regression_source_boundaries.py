"""Actual accounts, independent rejection history and historical calls stay data."""
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_run_observation_comparison_api import second
from tests.unit.test_delivery_handoffs import delivery_team,handoff,ack,acceptance,post
from tests.unit.test_source_grants import PID
from tests.unit.test_product_tasks import ref
from tests.unit.test_regression_dataset_api import BASE,CID,confirmed_case,save_case,saved,reference


async def test_independent_business_rejection_is_fixed_even_after_a_later_passing_review(delivery_team):
    env=delivery_team;await handoff(env);await ack(env)
    rejected=await post(env.reviewer,'/delivery-acceptances',acceptance(env,'fail'),'independent-regression-fail')
    assert rejected.status_code==200 and rejected.json()['business_acceptance']=='rejected',rejected.text
    calls=env.adapter.n
    response=await env.owner.post(BASE+'/failure-candidates',json=dict(schema_version=1,execution_id=env.result['execution_id']))
    assert response.status_code==200,response.text
    candidate=response.json();assert candidate['origin']['failure_kind']=='business_failed'
    assert candidate['origin']['acceptance_ref']==ref(rejected.json(),'acceptance_id')
    for client in (env.reviewer,env.stranger):
        denied=await client.post(BASE+'/failure-candidates',json=dict(schema_version=1,execution_id=env.result['execution_id']))
        assert denied.status_code==403 and denied.headers['cache-control']=='no-store',denied.text
    response=await env.owner.post(BASE+'/regression-cases',json=confirmed_case(candidate),headers={'Idempotency-Key':'business-case'})
    assert response.status_code==200,response.text
    original=response.json()
    passed=acceptance(env);passed['acceptance_id']='av-'+'5'*16
    response=await post(env.reviewer,'/delivery-acceptances',passed,'independent-later-pass')
    assert response.status_code==200 and response.json()['business_acceptance']=='passed',response.text
    history=await env.owner.get(BASE+'/regression-cases/'+CID,params={'revision':1})
    assert history.status_code==200 and history.json()==original,history.text
    edited=dict(original['selection'],expected_revision=1,title='基于历史失败的新期望')
    response=await env.owner.post(BASE+'/regression-cases',json=edited,headers={'Idempotency-Key':'edit-historical-business-case'})
    assert response.status_code==200 and response.json()['revision']==2,response.text
    history=await env.owner.get(BASE+'/regression-cases/'+CID,params={'revision':1})
    assert history.status_code==200 and history.json()==original,history.text
    assert env.adapter.n==calls


async def test_real_membership_removed_after_candidate_read_cannot_return_private_data(delivery_team,monkeypatch):
    import pyharness.application.product_regressions as module
    env=delivery_team;await handoff(env);await ack(env)
    response=await post(env.reviewer,'/delivery-acceptances',acceptance(env,'fail'),'late-fail')
    assert response.status_code==200,response.text
    env.accounts.membership('delivery-team','delivery-stranger','owner')
    original=module.failure_candidate_view
    async def revoke(projects,row,summary):
        result=await original(projects,row,summary)
        env.accounts.membership('delivery-team','delivery-owner','remove')
        return result
    monkeypatch.setattr(module,'failure_candidate_view',revoke)
    calls=env.adapter.n
    response=await env.owner.post(BASE+'/failure-candidates',json=dict(schema_version=1,execution_id=env.result['execution_id']))
    assert response.status_code==403 and response.headers['cache-control']=='no-store',response.text
    assert env.adapter.n==calls


@pytest.mark.parametrize('running_product',[[dict(id='historic-read',name='fs.read_file',args={'path':'unchanged.txt'})]],indirect=True)
async def test_original_historical_tool_event_is_never_replayed_by_preview_or_case_save(running_product):
    env=running_product
    async def fail_after_tool(ctx,result):
        if env.adapter.n>=2:raise RuntimeError('intentional failure after original tool event')
    env.controls.after_chat=fail_after_tool
    failure=await second(env);assert failure['status']=='failed'
    original=env.app.service.surface_mgr()._logs[failure['session_id']]
    events=list(original.events_after(0));calls=[e for e in events if e.type=='tool.call']
    assert calls
    response=await env.client.post(BASE+'/failure-candidates',json=dict(schema_version=1,execution_id=failure['execution_id']))
    assert response.status_code==200,response.text
    candidate=response.json();assert candidate['root_tool_call_count']==len(calls)
    model_calls=env.adapter.n;through=candidate['origin']['observed_seq']
    response=await save_case(env,confirmed_case(candidate),'historical-call-is-data')
    assert response.status_code==200,response.text
    assert env.adapter.n==model_calls and list(original.events_after(0))[-1].seq==through
    assert len([e for e in original.events_after(0) if e.type=='tool.call'])==len(calls)


async def test_forged_reference_duplicate_cases_and_foreign_project_cannot_enter_a_dataset(running_product):
    env=running_product;_,case=await saved(env);store=env.app.service.projects.store
    new_project='p-'+'2'*16
    response=await env.client.post('/api/v1/projects',json=dict(project_id=new_project,expected_revision=0,name='Other project'),
        headers={'Idempotency-Key':'other-project'})
    assert response.status_code==200,response.text
    before=store.log_path.read_bytes();body=dict(schema_version=1,dataset_id='ds-'+'2'*16,expected_revision=0,
        project_revision=1,title='必须固定原版本',case_refs=[reference(case,'case_id')],confirmed=True)
    scenarios=[(BASE,dict(body,case_refs=[dict(reference(case,'case_id'),digest='0'*64)]),409),
        (BASE,dict(body,case_refs=[reference(case,'case_id')]*2),422),
        ('/api/v1/projects/'+new_project,body,403)]
    for i,(base,command,status) in enumerate(scenarios):
        response=await env.client.post(base+'/regression-datasets',json=command,headers={'Idempotency-Key':'bad-dataset-'+str(i)})
        assert response.status_code==status,response.text
        assert store.log_path.read_bytes()==before
