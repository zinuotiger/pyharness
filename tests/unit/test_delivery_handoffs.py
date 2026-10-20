"""Real authenticated subjects, independent acceptance and separately granted export."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.accounts import Accounts
from tests.support import isolated_settings
from tests.unit.test_run_attempts import ready,submission,submit
from tests.unit.test_delivery_packages import BASE,PACKAGES,DEL,outputs,package
from tests.unit.test_product_tasks import ref
from tests.unit.test_source_grants import PID,GID


async def post(client,path,body,key):
    return await client.post(BASE+path,json=body,headers={'Idempotency-Key':key})


@pytest.fixture
async def delivery_team(tmp_path,adapter_factory,request):
    # Default scenarios retain the original no-tool producer. Explicit new
    # file-effect cases use the existing real approval routes and guard.
    options=getattr(request,'param',{})
    cfg=isolated_settings(tmp_path/'app');adapter=adapter_factory(cfg,options.get('script',[]))
    app=DesktopApp(cli.assemble_ctx(cfg));accounts=Accounts(app.service._storage_root())
    users={name:accounts.provision(name,'synthetic-only-password','delivery-team') for name in ['delivery-owner','delivery-reviewer','delivery-stranger']}
    for name in ['delivery-reviewer','delivery-stranger']:accounts.membership('delivery-team',name,'member')
    clients=[httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') for _ in users]
    owner,reviewer,stranger=clients
    source=tmp_path/'source';source.mkdir();(source/'unchanged.txt').write_bytes(b'unchanged\r\n')
    try:
        for name,client in zip(users,clients):
            response=await client.post('/api/account/login',json=dict(username=name,password='synthetic-only-password',tenant='delivery-team'))
            assert response.status_code==200,response.text
        assert (await owner.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=0,name='delivery-team'),headers={'Idempotency-Key':'project'})).status_code==200
        task=await ready(owner,source)
        response=await post(owner,'/delivery-definitions',dict(schema_version=1,delivery_id=DEL,expected_revision=0,
            project_revision=1,task_ref=ref(task,'product_task_id'),items=[dict(item_id='report',title='Final result',required=True,acceptance_criteria=['Explain the input'],citation_refs=[])]),'definition')
        assert response.status_code==200,response.text
        delivery=response.json();attempt=await submit(owner,submission(task,delivery_ref=ref(delivery,'delivery_id')))
        assert attempt.status_code==200,attempt.text
        service=app._service_registry.get('delivery-team')
        service.save_model_profile(dict(id='synthetic-delivery-model',label='Synthetic adapter only',provider='OpenAI',
            model=cfg.llm.model,base_url='http://127.0.0.1:1',api_key='synthetic-not-a-real-credential',fallback_models=[]))
        agent=service.platform.save_agent(dict(name='Producer',allowed_tools=sorted({c['name'] for c in options.get('script',[])}),**options.get('agent',{})));version=service.platform.publish_agent(agent['agent_id'])
        selection=dict(schema_version=1,attempt_ref=ref(attempt.json(),'attempt_id'),agent_ref=dict(agent_id=version['agent_id'],version=version['version'],sha256=version['sha256']))
        preview=await post(owner,'/execution-previews',selection,'unused-preview-key')
        assert preview.status_code==200,preview.text
        command=dict(selection,execution_id='ex-'+'b'*16,confirmation_digest=preview.json()['confirmation_digest'])
        response=await post(owner,'/executions',command,'producer-execution');assert response.status_code==200,response.text
        for _ in range(300):
            if options.get('script'):
                pending=await owner.get('/api/v1/approvals');assert pending.status_code==200,pending.text
                for item in pending.json()['approvals']:
                    if item['status']!='pending':continue
                    approved=await owner.post('/api/v1/approvals/'+item['id']+'/approve',json={k:item[k] for k in ('session_id','call_id','run_id')})
                    assert approved.status_code==200,approved.text
            response=await owner.get(BASE+'/executions/'+command['execution_id']);assert response.status_code==200,response.text
            result=response.json()
            if result['status']=='completed' and service._queues[result['session_id']].status().running is None:break
            await asyncio.sleep(.01)
        assert result['status']=='completed'
        from pyharness.application.execution_effects import flush_confirmed
        await flush_confirmed(service.surface_mgr()._logs[result['session_id']])
        env=SimpleNamespace(app=app,client=owner,service=service,owner=owner,reviewer=reviewer,stranger=stranger,
            accounts=accounts,users=users,adapter=adapter,result=result,delivery=delivery,source=source,task=task,selection=selection)
        found=await outputs(env,result);env.output=found['outputs'][0]['reference']
        response=await owner.post(PACKAGES,json=package(result,delivery,env.output),headers={'Idempotency-Key':'package'})
        assert response.status_code==200,response.text
        env.package=response.json();yield env
    finally:
        for client in clients:await client.aclose()
        for service in app._service_registry.all():await service.shutdown()


def role(env,**changes):
    return dict(schema_version=1,role_id='rc-'+'1'*16,expected_revision=0,project_revision=1,title='Independent reviewer',
        parent_execution_ref=ref(env.result,'execution_id'),recipient_username='delivery-reviewer',role='reviewer',**changes)


async def handoff(env,recipient='delivery-reviewer'):
    response=await post(env.owner,'/role-contracts',dict(role(env),recipient_username=recipient),'role')
    assert response.status_code==200,response.text
    env.role=response.json()
    command=dict(schema_version=1,handoff_id='ho-'+'2'*16,role_ref=ref(env.role,'role_id'),
        package_ref=ref(env.package,'package_id'),outputs=[env.output],minimum_context='Inspect the selected final report only.')
    response=await post(env.owner,'/role-handoffs',command,'handoff')
    assert response.status_code==200,response.text
    env.handoff=response.json()
    return env.handoff


async def ack(env,client=None):
    response=await post(client or env.reviewer,'/handoff-acks',dict(schema_version=1,ack_id='ha-'+'3'*16,
        handoff_ref=ref(env.handoff,'handoff_id')),'ack')
    assert response.status_code==200,response.text
    return response.json()


def acceptance(env,verdict='pass'):
    return dict(schema_version=1,acceptance_id='av-'+'4'*16,handoff_ref=ref(env.handoff,'handoff_id'),
        package_ref=ref(env.package,'package_id'),decisions=[dict(item_id='report',criterion_index=0,verdict=verdict,note='Inspected exact selected text')])


async def test_explicit_named_handoff_independent_acceptance_and_separate_export(delivery_team):
    env=delivery_team;store=env.service.projects.store;calls=env.adapter.n
    assert (await env.reviewer.get(PACKAGES+'/'+env.package['package_id'])).status_code==403
    grant=await handoff(env)
    assert grant['execution_authorized'] is False
    path='/role-handoffs/'+grant['handoff_id']
    assert (await env.stranger.get(BASE+path)).status_code==403
    before=store.log_path.read_bytes();response=await env.reviewer.get(BASE+path)
    assert response.status_code==200,response.text
    assert response.json()['acknowledged'] is False and response.json()['outputs'][0]['text_preview']=='done'
    assert store.log_path.read_bytes()==before
    command=acceptance(env)
    assert (await post(env.reviewer,'/delivery-acceptances',command,'unacked')).status_code==403
    await ack(env)
    assert (await post(env.owner,'/delivery-acceptances',command,'self-review')).status_code==403
    response=await post(env.reviewer,'/delivery-acceptances',command,'independent-review')
    assert response.status_code==200,response.text
    accepted=response.json();assert accepted['business_acceptance']=='passed'
    assert (await env.reviewer.get(BASE+'/delivery-exports/xp-'+'5'*16+'/outputs/0')).status_code==403
    export=dict(schema_version=1,export_id='xp-'+'5'*16,purpose='delivery',handoff_ref=ref(grant,'handoff_id'),
        package_ref=ref(env.package,'package_id'),outputs=[env.output],acceptance_ref=ref(accepted,'acceptance_id'))
    response=await post(env.owner,'/delivery-exports',export,'grant-export');assert response.status_code==200,response.text
    download=BASE+'/delivery-exports/'+export['export_id']+'/outputs/0'
    assert (await env.stranger.get(download)).status_code==403
    response=await env.reviewer.get(download)
    assert response.status_code==200 and response.content==b'done'
    assert response.headers['cache-control']=='no-store' and response.headers['x-content-type-options']=='nosniff'
    assert env.adapter.n==calls


@pytest.mark.parametrize('revoke',['membership','source','role'])
async def test_handoff_read_rechecks_current_grants_and_preserves_history(delivery_team,revoke):
    env=delivery_team;grant=await handoff(env);await ack(env)
    if revoke=='membership':env.accounts.membership('delivery-team','delivery-reviewer','remove')
    if revoke=='source':
        response=await env.owner.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'revoke'})
        assert response.status_code==200,response.text
    if revoke=='role':
        response=await post(env.owner,'/role-contracts',dict(role(env),expected_revision=1,retired=True),'retire-role')
        assert response.status_code==200,response.text
    store=env.service.projects.store;before=store.log_path.read_bytes()
    response=await env.reviewer.get(BASE+'/role-handoffs/'+grant['handoff_id'])
    assert response.status_code in (401,403,409),response.text
    assert store.log_path.read_bytes()==before
    resources,_,_=await store._execution_read_state()
    assert grant['handoff_id'] in resources


async def test_same_account_cannot_become_independent_by_agent_name(delivery_team):
    env=delivery_team;await handoff(env,'delivery-owner');await ack(env,env.owner)
    before=env.service.projects.store.log_path.read_bytes()
    response=await post(env.owner,'/delivery-acceptances',acceptance(env),'self-acceptance')
    assert response.status_code==403,response.text
    assert env.service.projects.store.log_path.read_bytes()==before
