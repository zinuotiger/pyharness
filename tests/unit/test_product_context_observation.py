"""Original queue -> recorded public request -> guarded read-only context API."""
import json
import hashlib
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.accounts import current_actor
from pyharness.errors import PyHError
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_product_execution_sessions import private_run,execution_ready,grant_app
from tests.unit.test_source_grants import PID,GID

BASE='/api/v1/projects/'+PID


async def completed(env,ident='ex-'+'c'*16):
    projects=env.app.service.projects;preview=await projects.execution_preview(PID,env.selection)
    await projects.start_execution(PID,dict(env.selection,execution_id=ident,confirmation_digest=preview['confirmation_digest']),'public-context-start')
    result=await wait_terminal(projects,ident)
    assert result['status']=='completed',result
    return result


async def test_real_original_queue_records_the_context_given_to_the_actual_adapter(running_product):
    env=running_product;result=await completed(env);url=BASE+'/executions/'+result['execution_id']+'/contexts'
    response=await env.client.get(url);assert response.status_code==200,response.text
    data=response.json();assert data['context_status']=='available' and len(data['items'])==1
    item=data['items'][0];detail=await env.client.get(url+'/'+item['observation_id'])
    assert detail.status_code==200,detail.text
    value=detail.json();assert value['layer']=='adapter_input' and value['phase']=='prepared'
    assert json.loads(value['public_view_canonical'])==value['public_view']
    assert hashlib.sha256(value['public_view_canonical'].encode()).hexdigest()==value['public_view_digest']
    assert value['derivation']['history_window']['available'] and value['derivation']['system_window']['available']
    assert value['source_content_state']=='injected_fixed_prompt'
    actual=next(m['content'] for m in env.messages[0] if m['role']=='user')
    public=next(m['content'] for m in value['public_view']['messages'] if m['role']=='user')
    assert actual.startswith('Execute the objective') and public.startswith('Execute the objective')
    journal=env.app.service.surface_mgr()._logs[result['session_id']]
    facts=[e for e in journal.events_after(0) if e.type=='execution.context']
    assert len(facts)==1 and facts[0].payload['observation_id']==item['observation_id']
    assert facts[0].task_id==result['task_id'] and facts[0].payload['execution_id']==result['execution_id']
    assert env.adapter.n==1 and response.headers['cache-control']=='no-store' and detail.headers['cache-control']=='no-store'


async def test_other_actual_subject_cannot_read_private_context_even_for_same_tenant(running_product):
    env=running_product;result=await completed(env)
    token=current_actor.set(dict(tenant='default',user_id='another-actual-user'))
    try:
        with pytest.raises(PyHError) as error:await env.app.service.projects.execution_contexts(PID,result['execution_id'])
        assert error.value.code=='GRD-403'
    finally:current_actor.reset(token)


async def test_current_source_withdrawal_blocks_the_public_copy_without_more_model_calls(running_product):
    env=running_product;result=await completed(env);url=BASE+'/executions/'+result['execution_id']+'/contexts'
    response=await env.client.get(url);assert response.status_code==200,response.text
    ident=response.json()['items'][0]['observation_id'];calls=env.adapter.n
    withdrawn=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-context-source'})
    assert withdrawn.status_code==200,withdrawn.text
    response=await env.client.get(url+'/'+ident)
    assert response.status_code==403 and response.headers['cache-control']=='no-store',response.text
    assert env.adapter.n==calls


async def test_cold_context_reads_open_no_writer_engine_queue_and_preserve_journals(running_product):
    env=running_product;result=await completed(env);cfg=env.app.service.ctx.settings
    roots=(env.app.service.projects.store.log_path.parent,env.app.service._tenant_sessions_dir())
    await env.app.service.shutdown()
    journals={p:p.read_bytes() for root in roots for p in root.rglob('*.jsonl')};assert len(journals)>=2
    cold=DesktopApp(cli.assemble_ctx(cfg));url=BASE+'/executions/'+result['execution_id']+'/contexts'
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            response=await client.get(url);assert response.status_code==200,response.text
            ident=response.json()['items'][0]['observation_id']
            assert (await client.get(url+'/'+ident)).status_code==200
        assert not cold.service._engines and not cold.service._queues and cold.service.projects.store._store is None
        assert env.adapter.n==1 and all(p.read_bytes()==body for p,body in journals.items())
    finally:await cold.service.shutdown()


async def test_missing_historical_observation_is_unavailable_and_is_never_reconstructed(private_run):
    env=private_run;before=env.journal._persistence.path.read_bytes()
    response=await env.client.get(BASE+'/executions/'+env.receipt['execution_id']+'/contexts')
    assert response.status_code==200,response.text
    data=response.json();assert data['context_status']=='unavailable' and data['items']==[]
    assert data['reason']=='no_recorded_context' and env.journal._persistence.path.read_bytes()==before
    assert not env.app.service._engines and not env.app.service._queues


@pytest.mark.parametrize('running_product',[[dict(id='inspect-child',name='subagent.spawn',args=dict(task='Public delegated observation task'))]],indirect=True)
async def test_actual_child_context_keeps_its_own_session_and_rejects_foreign_session_selection(running_product):
    env=running_product;result=await completed(env);url=BASE+'/executions/'+result['execution_id']+'/contexts'
    response=await env.client.get(url);assert response.status_code==200,response.text
    children=response.json()['child_sessions'];assert len(children)==1
    child=children[0]['session_id'];response=await env.client.get(url,params=dict(session_id=child))
    assert response.status_code==200,response.text
    data=response.json();assert len(data['items'])==1 and data['session_id']==child
    detail=await env.client.get(url+'/'+data['items'][0]['observation_id'],params=dict(session_id=child))
    assert detail.status_code==200 and detail.json()['session_id']==child,detail.text
    assert 'Public delegated observation task' in json.dumps(detail.json()['public_view'])
    denied=await env.client.get(url,params=dict(session_id='s-pa-'+'f'*32))
    assert denied.status_code==403 and env.adapter.n==3,denied.text
