"""Submission readiness and failures must be actionable and leave no invisible work."""
import asyncio
import httpx
import pytest

from pyharness import cli, web
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings


async def test_missing_model_is_422_before_creating_session_or_run(tmp_path):
    app = DesktopApp(cli.assemble_ctx(web.settings_for(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1',
                headers={'X-PyHarness-Token':app._api_token}) as client:
            readiness = (await client.get('/api/v1/chat/readiness')).json()
            assert readiness == {'ready':False,'code':'model_not_configured','model':None,'profile_id':None,'network_tested':False}
            response = await client.post('/api/v1/runs', json={'text':'synthetic unconfigured submission'})
            assert response.status_code == 422
            assert response.json()['code'] == 'model_not_configured'
            assert (await client.get('/api/v1/sessions')).json()['sessions'] == []
            assert (await client.get('/api/v1/runs')).json()['runs'] == []
            assert not app.service._engines
    finally:
        await app.service.shutdown()


async def test_missing_explicit_agent_connection_cannot_use_another_model(tmp_path, adapter_factory):
    cfg = isolated_settings(tmp_path);adapter_factory(cfg)
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        p = app.service.platform
        agent = p.save_agent({'name':'missing connection','model_connection':'missing'})
        p.publish_agent(agent['agent_id'])
        body = {'agent_id':agent['agent_id'],'agent_version':1,'text':'synthetic'}
        assert (await p.chat_readiness(body))['code'] == 'model_connection_missing'
        with pytest.raises(PyHError, match='model_connection_missing'):
            await p.start_run(body)
        assert (await p.sessions())['sessions'] == []
    finally:
        await app.service.shutdown()


async def test_runtime_start_error_has_durable_failed_run(tmp_path, adapter_factory, monkeypatch):
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    async def unavailable(*args, **kwargs):
        raise RuntimeError('synthetic-secret-must-not-be-persisted')
    try:
        monkeypatch.setattr(app.service, 'queue_for', unavailable)
        row=await app.service.platform.start_run({'text':'synthetic accepted task'})
        run=await app.service.platform.run(row['run_id'])
        assert run['status']=='failed' and run['error_code']=='runtime_start_failed'
        log=await app.service.require_session(row['session_id'])
        assert 'synthetic-secret-must-not-be-persisted' not in str(list(log.events_after(0)))
    finally:
        await app.service.shutdown()
    restored=DesktopApp(cli.assemble_ctx(cfg))
    try:
        assert (await restored.service.platform.run(row['run_id']))['status']=='failed'
    finally:
        await restored.service.shutdown()


async def test_readiness_never_invokes_model_and_success_tracks_run(tmp_path, adapter_factory):
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        assert (await app.service.platform.chat_readiness())['ready']
        assert not app.service._engines
        row=await app.service.platform.start_run({'text':'synthetic accepted task'})
        assert (await asyncio.wait_for(app.service._queues[row['session_id']].wait_for(row['task_id']),10)).ok
        assert (await app.service.platform.run(row['run_id']))['status']=='completed'
    finally:
        await app.service.shutdown()


async def test_browser_root_redirects_but_api_keeps_401(tmp_path):
    app=DesktopApp(cli.assemble_ctx(web.settings_for(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as client:
            response=await client.get('/',headers={'Accept':'text/html'})
            assert response.status_code==303 and response.headers['location']=='/login'
            assert (await client.get('/api/v1/chat/readiness')).status_code==401
            assert (await client.get('/api/v1/runs')).status_code==401
    finally:
        await app.service.shutdown()


async def test_team_readiness_matches_profile_requirement_even_with_test_adapter(tmp_path, adapter_factory):
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as client:
            await client.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                json={'username':'readiness-owner','password':'synthetic-password-readiness','tenant':'readiness-team'})
            await client.post('/api/account/login',json={'username':'readiness-owner','password':'synthetic-password-readiness'})
            readiness=(await client.get('/api/v1/chat/readiness')).json()
            assert not readiness['ready'] and readiness['code']=='tenant_model_profile_required'
            refused=await client.post('/api/v1/runs',json={'text':'synthetic'})
            assert refused.status_code==409 and refused.json()['code']==readiness['code']
            assert (await client.get('/api/v1/runs')).json()['runs']==[]
            assert (await client.get('/api/v1/sessions')).json()['sessions']==[]
            await client.post('/api/settings/models',json={'model':cfg.llm.model,'base_url':'http://127.0.0.1:1/v1','api_key':'synthetic-readiness-only'})
            assert (await client.get('/api/v1/chat/readiness')).json()['ready']
    finally:
        await app._service_registry.close_all()
