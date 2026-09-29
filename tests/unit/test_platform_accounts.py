"""Two synthetic users exercise server-enforced account and tenant boundaries."""
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings
from pyharness.application.accounts import Accounts, AccountError


async def test_two_accounts_cannot_cross_session_run_artifact_memory_or_connection(tmp_path,adapter_factory):
    import asyncio
    import hashlib
    import json
    cfg=isolated_settings(tmp_path)
    adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    transport=httpx.ASGITransport(app=app.api)
    try:
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as a, \
                   httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as b:
            for client,name in [(a,'alice'),(b,'bob')]:
                assert (await client.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                    json={'username':name,'password':'synthetic-password-'+name,'tenant':'team-'+name})).status_code==201
                assert (await client.post('/api/account/login',json={'username':name,'password':'synthetic-password-'+name})).status_code==200
                assert (await client.post('/api/settings/models',json={'id':'only-'+name,'model':cfg.llm.model,
                    'base_url':'https://synthetic.invalid/v1','api_key':'synthetic-private-'+name})).status_code==200
            run_response=await a.post('/api/v1/runs',json={'text':'synthetic account-owned task'})
            assert run_response.status_code==200,run_response.text
            run=run_response.json()
            owner=app._service_registry.get('team-alice')
            assert (await asyncio.wait_for(owner._queues[run['session_id']].wait_for(run['task_id']),10)).ok
            assert (await a.get('/api/v1/runs/'+run['run_id'])).json()['status']=='completed'
            assert (await b.get('/api/sessions/'+run['session_id']+'/messages')).status_code==403
            for suffix in ('','/trace'):
                assert (await b.get('/api/v1/runs/'+run['run_id']+suffix)).status_code==404
            for action in ('cancel','retry'):
                assert (await b.post('/api/v1/runs/'+run['run_id']+'/'+action)).status_code==404
            assert (await b.get('/api/v1/runs')).json()['runs']==[]
            artifact=(await a.post('/api/v1/artifacts',json={'session_id':run['session_id'],
                'filename':'owned.txt','data_base64':'b3duZWQ='})).json()
            path='/api/v1/artifacts/'+artifact['artifact_id']
            assert (await a.get(path+'/download')).content==b'owned'
            assert artifact['sha256']==hashlib.sha256(b'owned').hexdigest()
            for suffix in ('','/preview','/download'):
                assert (await b.get(path+suffix)).status_code==404
            assert (await b.delete(path)).status_code==404
            assert (await a.post('/api/v1/memory',json={'op':'set','key':'shared-name','value':'alice-owned-memory'})).status_code==200
            assert 'alice-owned-memory' not in (await b.get('/api/v1/memory?key=shared-name')).text
            assert (await b.get('/api/v1/memory?key=shared-name',headers={'X-PyHarness-Tenant':'team-alice'})).status_code==403
            assert (await b.post('/api/v1/memory',json={'op':'delete','key':'shared-name'})).status_code==200
            assert (await a.get('/api/v1/memory?key=shared-name')).json()['result']=='alice-owned-memory'
            public=(await b.get('/api/v1/connections')).json()
            assert [p['id'] for p in public['models']['profiles']]==['only-bob']
            assert 'synthetic-private-' not in json.dumps(public)
            assert (await b.post('/api/v1/connections/model:only-alice/test')).status_code==404
            assert (await b.get('/api/v1/connections?tenant=team-alice')).status_code==403
            assert (await a.delete(path)).status_code==200
            assert (await a.get(path+'/download')).status_code==404
            trace=(await a.get('/api/v1/runs/'+run['run_id']+'/trace')).text
            assert 'synthetic-private-' not in trace
    finally:
        for service in app._service_registry.all(): await service.shutdown()


async def test_run_quota_does_not_block_cancellation(tmp_path,monkeypatch):
    from pyharness.desktop import accounts_api
    monkeypatch.setattr(accounts_api,'ACTIVE_RUN_QUOTA',0)
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as client:
            await client.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                json={'username':'quota-user','password':'synthetic-password-quota','tenant':'quota-team'})
            await client.post('/api/account/login',json={'username':'quota-user','password':'synthetic-password-quota'})
            response=await client.post('/api/v1/runs/missing/cancel')
            assert response.status_code==404
    finally:
        for service in app._service_registry.all(): await service.shutdown()


async def test_managed_team_cannot_run_preexisting_host_agent(tmp_path):
    from pyharness.errors import PyHError
    cfg=isolated_settings(tmp_path)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        service=app._service_registry.get('managed-team')
        platform=service.platform
        draft=platform.save_agent({'name':'legacy host','sandbox_profile':{'mode':'host_approved'}})
        platform.publish_agent(draft['agent_id'])
        Accounts(app._storage_root()).provision('team-user','synthetic-password-team','managed-team')
        with pytest.raises(PyHError) as caught:
            await platform.start_run({'text':'never execute','agent_id':draft['agent_id'],'agent_version':1})
        assert caught.value.code=='account_host_execution_forbidden'
        assert not service._engines
    finally:
        for service in app._service_registry.all(): await service.shutdown()


def test_password_storage_revocation_lockout_and_restart(tmp_path):
    accounts=Accounts(tmp_path)
    user=accounts.provision('synthetic-user','synthetic-password-unique','team-one')
    token=accounts.login('synthetic-user','synthetic-password-unique')
    assert Accounts(tmp_path).authenticate(token)['user_id']==user['user_id']
    raw=accounts.path.read_bytes()
    assert b'synthetic-password-unique' not in raw and token.encode() not in raw
    with pytest.raises(AccountError,match='last_owner_required'):
        accounts.membership('team-one','synthetic-user','viewer')
    accounts.logout(token)
    assert Accounts(tmp_path).authenticate(token) is None
    for _ in range(5):
        with pytest.raises(AccountError,match='invalid_credentials'):
            accounts.login('synthetic-user','synthetic-wrong-password')
    with pytest.raises(AccountError,match='login_rate_limited'):
        accounts.login('synthetic-user','synthetic-password-unique')


async def test_account_session_quota_and_artifact_isolation(tmp_path,monkeypatch):
    from pyharness.desktop import accounts_api
    monkeypatch.setattr(accounts_api,'SESSION_QUOTA',1,raising=False)
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    transport=httpx.ASGITransport(app=app.api)
    try:
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as a, \
                   httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as b:
            for client,name in [(a,'user-a'),(b,'user-b')]:
                assert (await client.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                    json={'username':name,'password':'synthetic-password-'+name,'tenant':name})).status_code==201
                assert (await client.post('/api/account/login',json={'username':name,'password':'synthetic-password-'+name})).status_code==200
            sid=(await a.post('/api/v1/sessions')).json()['sid']
            assert (await a.post('/api/v1/sessions')).status_code==429
            artifact=(await a.post('/api/v1/artifacts',json={'session_id':sid,'filename':'safe.txt',
                'data_base64':'c3ludGhldGlj'})).json()
            assert (await b.get('/api/v1/artifacts/'+artifact['artifact_id']+'/preview')).status_code==404
            assert (await b.get('/api/v1/artifacts/'+artifact['artifact_id']+'/download')).status_code==404
            assert (await b.post('/api/v1/artifacts',json={'session_id':sid,'filename':'evil.txt',
                'data_base64':'c3ludGhldGlj'})).status_code in (403,404)
    finally:
        for service in app._service_registry.all(): await service.shutdown()


async def test_accounts_login_logout_and_cross_tenant_denial(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    transport = httpx.ASGITransport(app=app.api)
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as admin:
            for name, tenant in [('alice','team-a'),('bob','team-b')]:
                response = await admin.post('/api/account/users',
                    headers={'X-PyHarness-Token':app._api_token},
                    json={'username':name,'password':'synthetic-password-'+name,'tenant':tenant})
                assert response.status_code == 201
                assert 'password' not in response.text
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as a, \
                   httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as b:
            for client, name in [(a,'alice'),(b,'bob')]:
                response = await client.post('/api/account/login',
                    json={'username':name,'password':'synthetic-password-'+name})
                assert response.status_code == 200
                assert 'HttpOnly' in response.headers['set-cookie']
                assert (await client.get('/')).status_code == 200
                assert 'pyharness_token=' not in (await client.get('/')).headers.get('set-cookie','')
            sid = (await a.post('/api/v1/sessions')).json()['sid']
            assert (await b.get('/api/sessions/'+sid+'/messages')).status_code == 403
            assert (await b.get('/api/v1/sessions',headers={'X-PyHarness-Tenant':'team-a'})).status_code == 403
            assert (await b.get('/api/v1/sessions')).json()['sessions'] == []
            assert (await b.post('/api/account/users',json={'username':'evil'})).status_code == 403
            assert (await a.post('/api/account/logout')).status_code == 200
            assert (await a.get('/api/v1/sessions')).status_code == 401
    finally:
        for service in app._service_registry.all():
            await service.shutdown()


async def test_role_downgrade_applies_to_existing_session(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    transport=httpx.ASGITransport(app=app.api)
    try:
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as owner, \
                   httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as viewer:
            for name in ['owner','viewer']:
                assert (await owner.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                    json={'username':name,'password':'synthetic-password-'+name,'tenant':'team-a'})).status_code == 201
            for client,name in [(owner,'owner'),(viewer,'viewer')]:
                assert (await client.post('/api/account/login',json={'username':name,
                    'password':'synthetic-password-'+name})).status_code == 200
            assert (await owner.post('/api/account/members',json={'username':'viewer','role':'viewer'})).status_code == 200
            assert (await viewer.get('/api/v1/sessions')).status_code == 200
            assert (await viewer.post('/api/v1/sessions')).status_code == 403
            assert (await viewer.post('/api/account/members',json={'username':'viewer','role':'owner'})).status_code == 403
            assert (await owner.post('/api/v1/sessions',headers={'Origin':'https://foreign.invalid'})).status_code == 403
    finally:
        for service in app._service_registry.all():
            await service.shutdown()


async def test_account_cannot_enable_host_capabilities_or_inherit_operator_model(tmp_path):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as client:
            await client.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                json={'username':'limited','password':'synthetic-password-limited','tenant':'limited'})
            await client.post('/api/account/login',json={'username':'limited','password':'synthetic-password-limited'})
            assert (await client.post('/api/v1/runs',json={'text':'must not inherit operator credentials'})).status_code==409
            assert (await client.post('/api/v1/agents',json={'name':'host','sandbox_profile':{'mode':'host_approved'}})).status_code==403
            assert (await client.post('/api/v1/connections',json={'type':'mcp','config':{'name':'unsafe','command':['unsafe']}})).status_code==403
            assert (await client.post('/api/settings/models',json={'id':'own','model':'synthetic','base_url':'https://example.invalid',
                'api_key':'synthetic-team-secret'})).status_code==200
            response=await client.get('/api/v1/connections')
            assert response.json()['models']['tenants']==['limited']
            assert 'synthetic-team-secret' not in response.text
    finally:
        for service in app._service_registry.all(): await service.shutdown()
