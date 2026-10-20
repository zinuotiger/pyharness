"""Formal S07 HTTP surface over real auth/owner/queue; scripted inference only."""
import asyncio
import pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_source_grants import PID

BASE=f'/api/v1/projects/{PID}'


async def command(env):
    preview=await env.app.service.projects.execution_preview(PID,env.selection)
    return dict(env.selection,execution_id='ex-'+'a'*16,confirmation_digest=preview['confirmation_digest'])


async def test_http_preview_has_no_dispatch_or_persistence(running_product):
    env=running_product;before=env.app.service.projects.store.log_path.read_bytes()
    result=await env.client.post(BASE+'/execution-previews',json=env.selection)
    assert result.status_code==200,result.text
    assert result.headers.get('cache-control')=='no-store'
    assert result.json()['status']=='confirmation_required' and not result.json()['execution_authorized']
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0
    assert not env.app.service._engines and not env.app.service._queues


async def test_http_same_key_starts_once_and_original_key_reads_without_reexecution(running_product):
    env=running_product;body=await command(env)
    results=await asyncio.gather(*[env.client.post(BASE+'/executions',json=body,
        headers={'Idempotency-Key':'http-start'}) for _ in range(2)])
    assert [r.status_code for r in results]==[200,200],[r.text for r in results]
    assert all(r.headers.get('cache-control')=='no-store' for r in results)
    terminal=await wait_terminal(env.app.service.projects,body['execution_id'])
    assert terminal['status']=='completed' and env.adapter.n==1
    before=env.app.service.projects.store.log_path.read_bytes()
    query=await env.client.get(BASE+'/execution-submissions/by-key',params={'key':'http-start'})
    assert query.status_code==200 and query.json()['status']=='committed'
    assert query.json()['execution']['execution_id']==body['execution_id']
    observation=await env.client.get(BASE+'/executions/'+body['execution_id'])
    assert observation.status_code==200 and observation.json()['status']=='completed'
    assert observation.headers.get('cache-control')=='no-store'
    assert str(env.source) not in observation.text and 'storage_path' not in observation.text
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==1


@pytest.mark.parametrize('query',['','key=a&key=b','key=a&extra=b'])
async def test_http_original_key_query_rejects_ambiguous_parameters(running_product,query):
    env=running_product;before=env.app.service.projects.store.log_path.read_bytes()
    result=await env.client.get(BASE+'/execution-submissions/by-key'+('?' + query if query else ''))
    assert result.status_code==422,result.text
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0


@pytest.mark.parametrize('change',[{'created_by':'other-owner'},{'schema_version':True}])
async def test_http_forged_execution_authority_is_zero_dispatch(running_product,change):
    env=running_product;body=await command(env);before=env.app.service.projects.store.log_path.read_bytes()
    result=await env.client.post(BASE+'/executions',json=dict(body,**change),headers={'Idempotency-Key':'forged-http'})
    assert result.status_code==422,result.text
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0
    assert not env.app.service._engines and not env.app.service._queues


async def test_http_cancel_stops_actual_model_and_repeats_original_key_read_only(running_product):
    env=running_product;entered=asyncio.Event();resume=asyncio.Event()
    async def held(ctx,response):entered.set();await resume.wait()
    env.controls.after_chat=held;body=await command(env)
    start=await env.client.post(BASE+'/executions',json=body,headers={'Idempotency-Key':'http-held-model'})
    assert start.status_code==200,start.text
    try:
        await asyncio.wait_for(entered.wait(),5);value=start.json()
        stop=dict(schema_version=1,cancellation_id='ec-'+'a'*16,
            execution_ref={k:value[k] for k in ('execution_id','revision','digest','source_seq')})
        cancelled=await env.client.post(BASE+'/execution-cancellations',json=stop,headers={'Idempotency-Key':'http-stop'})
        assert cancelled.status_code==200,cancelled.text
        assert cancelled.headers.get('cache-control')=='no-store'
        terminal=await wait_terminal(env.app.service.projects,body['execution_id'])
        assert terminal['status']=='cancelled' and env.adapter.n==1
        before=env.app.service.projects.store.log_path.read_bytes()
        query=await env.client.get(BASE+'/cancellation-submissions/by-key',params={'key':'http-stop'})
        assert query.status_code==200 and query.json()['cancellation']['execution_stopped'] is True
        repeated=await env.client.post(BASE+'/execution-cancellations',json=stop,headers={'Idempotency-Key':'http-stop'})
        assert repeated.status_code==200 and repeated.json()['execution_stopped'] is True
        assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==1
    finally:resume.set()


@pytest.mark.parametrize('query',['key=','key=%2F','key=%00'])
async def test_http_original_key_values_are_validated_and_errors_never_cached(running_product,query):
    env=running_product;before=env.app.service.projects.store.log_path.read_bytes()
    result=await env.client.get(BASE+'/execution-submissions/by-key?'+query)
    assert result.status_code==422 and result.headers.get('cache-control')=='no-store',result.text
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0


async def test_http_missing_start_key_and_operator_auth_are_zero_dispatch(running_product):
    import httpx
    env=running_product;body=await command(env);before=env.app.service.projects.store.log_path.read_bytes()
    missing=await env.client.post(BASE+'/executions',json=body)
    assert missing.status_code==422 and missing.headers.get('cache-control')=='no-store'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app.api),base_url='http://127.0.0.1') as anonymous:
        refused=await anonymous.post(BASE+'/execution-previews',json=env.selection)
        assert refused.status_code in (401,403) and refused.headers.get('cache-control')=='no-store'
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0


async def test_http_conflicting_start_keys_never_dispatch_a_second_attempt(running_product):
    env=running_product;body=await command(env)
    started=await env.client.post(BASE+'/executions',json=body,headers={'Idempotency-Key':'one-http-start'})
    assert started.status_code==200,started.text
    await wait_terminal(env.app.service.projects,body['execution_id'])
    before=env.app.service.projects.store.log_path.read_bytes()
    for key in ('one-http-start','other-http-start'):
        response=await env.client.post(BASE+'/executions',json=dict(body,execution_id='ex-'+'b'*16),headers={'Idempotency-Key':key})
        assert response.status_code==409 and response.headers.get('cache-control')=='no-store',response.text
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==1


async def test_http_attempt_discovers_its_exact_authorization_without_local_hint_or_dispatch(running_product):
    env=running_product;path=BASE+'/run-attempts/'+env.attempt['attempt_id']+'/execution'
    before=env.app.service.projects.store.log_path.read_bytes()
    absent=await env.client.get(path)
    assert absent.status_code==200,absent.text
    assert absent.json()['status']=='not_authorized' and absent.json()['execution'] is None
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0
    body=await command(env)
    started=await env.client.post(BASE+'/executions',json=body,headers={'Idempotency-Key':'discover-http'})
    assert started.status_code==200,started.text
    await wait_terminal(env.app.service.projects,body['execution_id'])
    before=env.app.service.projects.store.log_path.read_bytes()
    found=await env.client.get(path)
    assert found.status_code==200 and found.headers.get('cache-control')=='no-store'
    assert found.json()['status']=='authorized' and found.json()['execution']['execution_id']==body['execution_id']
    assert found.json()['execution']['attempt_ref']==env.selection['attempt_ref']
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==1


async def test_http_control_discovery_has_no_writer_and_recovers_existing_stop(running_product,monkeypatch):
    from tests.unit.test_product_execution_cancellation import command as stop_command
    env=running_product;path=BASE+'/run-attempts/'+env.attempt['attempt_id']+'/execution-control'
    before=env.app.service.projects.store.log_path.read_bytes()
    empty=await env.client.get(path)
    assert empty.status_code==200 and empty.json()['status']=='not_authorized'
    assert empty.json()['execution_ref'] is None and empty.json()['cancellation'] is None
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0
    body=await command(env)
    started=await env.client.post(BASE+'/executions',json=body,headers={'Idempotency-Key':'control-existing-start'})
    assert started.status_code==200,started.text
    await wait_terminal(env.app.service.projects,body['execution_id'])
    stopped=await env.client.post(BASE+'/execution-cancellations',json=stop_command(started.json()),headers={'Idempotency-Key':'control-existing-stop'})
    assert stopped.status_code==200,stopped.text
    def forbidden(*args,**kwargs):raise AssertionError('control opened writer or source')
    monkeypatch.setattr(env.app.service.projects.store,'_open',forbidden)
    monkeypatch.setattr(env.app.service.projects.store,'_verify_snapshot_content',forbidden)
    monkeypatch.setattr(env.app.service.projects.store,'_verify_workspace_content',forbidden)
    before=env.app.service.projects.store.log_path.read_bytes()
    found=await env.client.get(path)
    assert found.status_code==200,found.text
    assert found.json()['cancellation']['submission_key']=='control-existing-stop'
    assert found.json()['cancellation']['execution_stopped'] is True
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==1


@pytest.mark.parametrize('state',['withdrawn','archived','body_unavailable'])
async def test_http_control_discovery_keeps_stop_available_without_source_read(running_product,monkeypatch,state):
    from tests.unit.test_source_grants import GID
    from tests.unit.test_product_execution_cancellation import command as stop_command
    env=running_product;entered=asyncio.Event();resume=asyncio.Event()
    async def held(ctx,response):entered.set();await resume.wait()
    env.controls.after_chat=held;body=await command(env)
    started=await env.client.post(BASE+'/executions',json=body,headers={'Idempotency-Key':'control-discovery-start'})
    assert started.status_code==200,started.text
    value=started.json()
    try:
        await asyncio.wait_for(entered.wait(),5)
        if state=='withdrawn':
            response=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
                headers={'Idempotency-Key':'withdraw-for-controls'})
            assert response.status_code==200
        if state=='archived':
            response=await env.client.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=1,name='archived',archived=True),
                headers={'Idempotency-Key':'archive-for-controls'})
            assert response.status_code==200
        def forbidden(*args):raise AssertionError('control discovery read source content')
        monkeypatch.setattr(env.app.service.projects.store,'_verify_snapshot_content',forbidden)
        monkeypatch.setattr(env.app.service.projects.store,'_verify_workspace_content',forbidden)
        before=env.app.service.projects.store.log_path.read_bytes()
        control=await env.client.get(BASE+'/run-attempts/'+env.attempt['attempt_id']+'/execution-control')
        assert control.status_code==200,control.text
        assert control.headers.get('cache-control')=='no-store'
        assert control.json()['control_only'] is True and control.json()['status']=='authorized'
        assert control.json()['execution_ref']=={k:value[k] for k in ('execution_id','revision','digest','source_seq')}
        assert 'result_summary' not in control.text and str(env.source) not in control.text
        assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==1
    finally:
        await env.app.service.projects.cancel_execution(PID,stop_command(value),'cleanup-control-discovery')
        resume.set()
