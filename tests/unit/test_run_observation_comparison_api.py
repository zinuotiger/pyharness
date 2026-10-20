"""Original attempts/queues, intentional failure, cold reads and real revocation."""
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_product_context_observation import completed
from tests.unit.test_run_attempts import submission,submit
from tests.unit.test_product_tasks import ref
from tests.unit.test_source_grants import PID,GID
from tests.unit.test_delivery_handoffs import delivery_team

URL='/api/v1/projects/'+PID+'/run-comparisons'


async def second(env,*,failure=False,agent_ref=None):
    projects=env.app.service.projects
    response=await submit(env.client,submission(env.task,attempt_id='ra-'+'2'*16),'comparison-second-attempt')
    assert response.status_code==200,response.text
    selection=dict(env.selection,attempt_ref=ref(response.json(),'attempt_id'),agent_ref=agent_ref or env.selection['agent_ref'])
    if failure:
        async def fail(ctx,result):raise RuntimeError('intentional comparison regression')
        env.controls.after_chat=fail
    preview=await projects.execution_preview(PID,selection)
    ident='ex-'+'2'*16
    await projects.start_execution(PID,dict(selection,execution_id=ident,confirmation_digest=preview['confirmation_digest']),'comparison-second-execution')
    return await wait_terminal(projects,ident)


async def test_real_execution_regression_is_reported_without_claiming_business_quality(running_product):
    env=running_product;a=await completed(env);b=await second(env,failure=True)
    assert a['status']=='completed' and b['status']=='failed'
    calls=env.adapter.n;store=env.app.service.projects.store;before=store.log_path.read_bytes()
    response=await env.client.post(URL,json=dict(schema_version=1,left_execution_id=a['execution_id'],right_execution_id=b['execution_id']))
    assert response.status_code==200,response.text
    value=response.json();assert value['comparison_status']=='aligned' and value['regressions']==[dict(kind='execution_regression',from_status='completed',to_status='failed')]
    assert value['business_quality_verdict']=='not_evaluated' and value['deltas']['cost_est'] is None
    assert value['left']['usage']['cost_est'] is None and value['right']['usage']['cost_est'] is None
    assert response.headers['cache-control']=='no-store' and env.adapter.n==calls and store.log_path.read_bytes()==before


async def test_different_published_validator_is_not_comparable(running_product):
    env=running_product;a=await completed(env);platform=env.app.service.platform
    agent=platform.save_agent(dict(name='Different declared validator',output_schema={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer']}))
    version=platform.publish_agent(agent['agent_id']);reference=dict(agent_id=version['agent_id'],version=version['version'],sha256=version['sha256'])
    b=await second(env,agent_ref=reference)
    response=await env.client.post(URL,json=dict(schema_version=1,left_execution_id=a['execution_id'],right_execution_id=b['execution_id']))
    assert response.status_code==200,response.text
    value=response.json();assert value['comparison_status']=='not_comparable' and value['incomparable_reasons']==['validators_differ']
    assert value['regressions']==[] and all(v is None for v in value['deltas'].values())


async def test_cold_comparison_opens_no_engine_queue_or_writer_and_source_withdrawal_blocks(running_product):
    env=running_product;a=await completed(env);b=await second(env);cfg=env.app.service.ctx.settings
    body=dict(schema_version=1,left_execution_id=a['execution_id'],right_execution_id=b['execution_id'])
    roots=(env.app.service.projects.store.log_path.parent,env.app.service._tenant_sessions_dir())
    await env.app.service.shutdown();journals={p:p.read_bytes() for root in roots for p in root.rglob('*.jsonl')}
    assert len(journals)>=3
    cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            response=await client.post(URL,json=body);assert response.status_code==200,response.text
            assert response.json()['comparison_status']=='aligned'
        assert not cold.service._engines and not cold.service._queues and cold.service.projects.store._store is None
        assert all(p.read_bytes()==raw for p,raw in journals.items())
    finally:await cold.service.shutdown()


async def test_second_read_cannot_return_first_subjects_data_after_real_membership_removal(delivery_team,monkeypatch):
    import pyharness.application.run_observation_comparison as comparison
    env=delivery_team;env.accounts.membership('delivery-team','delivery-stranger','owner')
    original=comparison.run_view;reads=[]
    async def revoke_after_second_read(*args,**kwargs):
        result=await original(*args,**kwargs);reads.append(True)
        if len(reads)==2:env.accounts.membership('delivery-team','delivery-owner','remove')
        return result
    monkeypatch.setattr(comparison,'run_view',revoke_after_second_read)
    response=await env.owner.post(URL,json=dict(schema_version=1,left_execution_id=env.result['execution_id'],right_execution_id=env.result['execution_id']))
    assert len(reads)==2 and response.status_code==403,response.text
    assert response.headers['cache-control']=='no-store' and 'public_output' not in response.text
