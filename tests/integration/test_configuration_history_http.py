"""Real ASGI/account/storage paths; no API response or authority stubs."""
import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from pyharness import cli
from pyharness.application.accounts import Accounts
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


def path(kind='model', resource='synthetic'):
    return '/api/v1/configuration-history/'+kind+'/'+resource


def apply_body(preview, key='http-apply'):
    return {'command_key': key, 'preview_sha256': preview['preview_sha256'],
            'confirm_apply': True, 'confirm_permission_increase': True}


@pytest.fixture
async def environment(tmp_path):
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(cli.assemble_ctx(cfg))
    profile = {'id': 'synthetic', 'label': 'Root HTTP model', 'model': cfg.llm.model,
        'base_url': 'http://127.0.0.1:1', 'api_key': 'synthetic-http-history-secret',
        'temperature': 0.2, 'max_tokens': 1000}
    app.service.save_model_profile(profile)
    draft = app.service.platform.save_agent({'name': 'HTTP configuration Agent',
        'permission_policy': 'standard', 'max_rounds': 12, 'model_connection': 'synthetic'})
    app.service.platform.publish_agent(draft['agent_id'])
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                base_url='http://127.0.0.1', headers={'X-PyHarness-Token': app._api_token}) as client:
            yield SimpleNamespace(app=app, cfg=cfg, client=client, root=tmp_path,
                profile=profile, agent=draft, accounts=Accounts(tmp_path))
    finally:
        for service in app._service_registry.all():
            await service.shutdown()


async def capture(client, base, key='http-capture'):
    response = await client.get(base+'/current')
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    response = await client.post(base+'/revisions', json={'command_key': key,
        'source_sha256': response.json()['source_sha256']})
    assert response.status_code == 200, response.text
    return response.json()['revision']['revision_id']


async def test_model_restore_and_historical_command_use_original_settings_and_zero_engines(environment):
    env = environment; base = path(); ident = await capture(env.client, base)
    update = {k: v for k, v in env.profile.items() if k != 'api_key'}
    env.app.service.save_model_profile({**update, 'temperature': 0.8, 'max_tokens': 500})
    response = await env.client.get(base+'/revisions/'+ident+'/preview')
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['compatible'] and preview['permission_increases']
    target = base+'/revisions/'+ident+'/applications'
    before = (env.app.service.tenant_root()/'models.json').read_bytes()
    denied = await env.client.post(target, json={**apply_body(preview), 'confirm_permission_increase': False})
    assert denied.status_code == 422 and set(denied.json()) == {'code'}
    assert (env.app.service.tenant_root()/'models.json').read_bytes() == before
    result = await env.client.post(target, json=apply_body(preview))
    assert result.status_code == 200, result.text
    assert env.app.service.effective_settings().llm.temperature == 0.2
    committed = (env.app.service.tenant_root()/'models.json').read_bytes()
    assert (await env.client.get(base+'/commands/http-apply')).json() == result.json()
    assert (await env.client.post(target, json=apply_body(preview))).json() == result.json()
    assert (env.app.service.tenant_root()/'models.json').read_bytes() == committed
    other = await env.client.get(path(resource='another')+'/commands/http-apply')
    assert other.status_code == 404 and set(other.json()) == {'code'}
    assert env.profile['api_key'] not in (await env.client.get(base+'/revisions')).text
    assert not env.app.service._engines and not env.app.service._queues


async def test_agent_http_restore_generates_a_new_original_version(environment):
    env = environment; base = path('agent', env.agent['agent_id'])
    ident = await capture(env.client, base)
    env.app.service.platform.save_agent({**env.agent, 'permission_policy': 'strict', 'max_rounds': 4}, env.agent['agent_id'])
    env.app.service.platform.publish_agent(env.agent['agent_id'])
    preview = (await env.client.get(base+'/revisions/'+ident+'/preview')).json()
    response = await env.client.post(base+'/revisions/'+ident+'/applications', json=apply_body(preview))
    assert response.status_code == 200, response.text
    assert response.json()['agent_ref']['version'] == 3
    assert env.app.service.platform.version(env.agent['agent_id'], 1)['version'] == 1
    assert env.app.service.platform.version(env.agent['agent_id'], 2)['version'] == 2
    assert (await env.client.get(base+'/commands/http-apply')).json() == response.json()
    assert not env.app.service._engines and not env.app.service._queues


async def test_committed_agent_history_and_receipt_survive_a_missing_current_secret_file(environment):
    env = environment; base = path('agent', env.agent['agent_id'])
    ident = await capture(env.client, base)
    preview = (await env.client.get(base+'/revisions/'+ident+'/preview')).json()
    response = await env.client.post(base+'/revisions/'+ident+'/applications', json=apply_body(preview))
    assert response.status_code == 200, response.text
    platform_path = env.app.service.platform.root/'configuration.json'
    before = platform_path.read_bytes()
    store = env.app.service.credentials
    secret = store._secret_path('default', store._load_doc('default'))
    assert secret.resolve().is_relative_to(env.root.resolve())
    secret.unlink()  # Only this test's synthetic credential file.
    query = await env.client.get(base+'/commands/http-apply')
    assert query.status_code == 200 and query.json() == response.json()
    history = await env.client.get(base+'/revisions')
    assert history.status_code == 200, history.text
    assert history.json()['commands'][0]['command_key'] == 'http-apply'
    assert history.json()['commands'][0]['created_at']
    assert [v['revision_id'] for v in history.json()['revisions']] == [ident]
    unavailable = await env.client.get(base+'/current')
    # Original committed-secret corruption is CRED-703 / 500, not a client validation error.
    assert unavailable.status_code == 500 and unavailable.json() == {'code': 'CRED-703'}
    assert platform_path.read_bytes() == before
    assert not env.app.service._engines and not env.app.service._queues


async def test_command_key_cannot_store_a_known_secret_in_the_public_history(environment):
    env = environment; base = path()
    state = (await env.client.get(base+'/current')).json()
    metadata = env.app.service.tenant_root()/'models.json'
    before = metadata.read_bytes()
    response = await env.client.post(base+'/revisions', json={
        'command_key': env.profile['api_key'], 'source_sha256': state['source_sha256']})
    assert response.status_code == 422 and env.profile['api_key'] not in response.text
    assert metadata.read_bytes() == before


@pytest.mark.parametrize('role', ['owner', 'member', 'viewer'])
async def test_current_real_account_role_and_tenant_override_presented_operator_header(environment, role):
    env = environment
    env.accounts.provision('owner-kept', 'synthetic-owner-kept-password', 'team-owned')
    env.accounts.provision('tested-user', 'synthetic-tested-user-password', 'home-tested')
    env.accounts.membership('team-owned', 'tested-user', role)
    team = env.app._service_registry.get('team-owned')
    team.save_model_profile({**env.profile, 'label': 'Team owned HTTP model'})
    login = await env.client.post('/api/account/login', json={'username': 'tested-user',
        'password': 'synthetic-tested-user-password', 'tenant': 'team-owned'})
    assert login.status_code == 200, login.text
    current = await env.client.get(path()+'/current')
    if role == 'owner':
        assert current.status_code == 200, current.text
        assert current.json()['snapshot']['label'] == 'Team owned HTTP model'
        await capture(env.client, path(), 'team-capture')
    else:
        assert current.status_code == 403
        assert (await env.client.get(path()+'/revisions')).status_code == 403
        assert (await env.client.post(path()+'/revisions', json={'mode': 'local_operator'})).status_code == 403
    forged = await env.client.get(path()+'/current', headers={'X-PyHarness-Tenant': 'default'})
    assert forged.status_code == 403 and 'Root HTTP model' not in forged.text


@pytest.mark.parametrize('bad', ['boolean', 'fields', 'invalid-json', 'oversize'])
async def test_bad_input_is_private_and_leaves_original_metadata_unchanged(environment, bad):
    env = environment; base = path(); ident = await capture(env.client, base)
    preview = (await env.client.get(base+'/revisions/'+ident+'/preview')).json()
    target = base+'/revisions/'+ident+'/applications'
    metadata = env.app.service.tenant_root()/'models.json'
    before = metadata.read_bytes()
    if bad == 'boolean':
        response = await env.client.post(target, json={**apply_body(preview), 'confirm_apply': 1})
    elif bad == 'fields':
        response = await env.client.post(target, json={**apply_body(preview), 'actor': 'forged-operator-private-value'})
    elif bad == 'invalid-json':
        response = await env.client.post(target, content=b'not-json-private-value')
    else:
        response = await env.client.post(target, content=b'x'*2049)
    assert response.status_code == 422, response.text
    assert set(response.json()) == {'code'} and 'private-value' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert metadata.read_bytes() == before


async def test_operator_cookie_revocation_during_body_read_cannot_fall_back_to_operator_header(environment):
    env = environment; base = path()
    env.accounts.provision_operator('local-owner', 'synthetic-local-owner-password')
    login = await env.client.post('/api/account/login', json={'username': 'local-owner',
        'password': 'synthetic-local-owner-password'})
    assert login.status_code == 200, login.text
    current = (await env.client.get(base+'/current')).json()
    raw = json.dumps({'command_key': 'revoked-capture', 'source_sha256': current['source_sha256']}).encode()
    entered, release = asyncio.Event(), asyncio.Event()
    async def chunks():
        yield raw[:10]
        entered.set()
        await release.wait()
        yield raw[10:]
    metadata = env.app.service.tenant_root()/'models.json'
    before = metadata.read_bytes()
    task = asyncio.create_task(env.client.post(base+'/revisions', content=chunks()))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        token = env.client.cookies.get('pyharness_account')
        env.accounts.logout(token)
        release.set()
        response = await asyncio.wait_for(task, 10)
        assert response.status_code == 403, response.text
        assert 'Root HTTP model' not in response.text and set(response.json()) == {'code'}
        assert metadata.read_bytes() == before
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
