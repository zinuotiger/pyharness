"""Existing channel/trajectory and project-control capabilities share one app."""
import httpx
import pytest
from pyharness import cli
from pyharness.application.accounts import Accounts
from pyharness.desktop.app import DesktopApp
from pyharness.events import EVENT_TYPES
from pyharness.events.vocab import SYNC_TYPES
from tests.support import isolated_settings


async def close(app):
    for service in app._service_registry.all():
        await service.shutdown()


async def test_one_authenticated_app_retains_channels_projects_and_trajectory(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    Accounts(tmp_path).provision_operator('unified-admin', 'synthetic-unified-password')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') as client:
            assert (await client.get('/api/v1/projects')).status_code == 401
            assert (await client.get('/api/v1/channels')).status_code == 401
            assert (await client.post('/api/account/login', json={'username':'unified-admin','password':'synthetic-unified-password'})).status_code == 200
            body = {'project_id':'p-1111111111111111','expected_revision':0,'name':'Synthetic unified project'}
            saved = await client.post('/api/v1/projects', json=body, headers={'Idempotency-Key':'unified-create'})
            assert saved.status_code == 200
            assert (await client.get('/api/v1/projects')).json()['total'] == 1
            assert (await client.get('/api/v1/channels')).status_code == 200
            sid = (await client.post('/api/v1/sessions')).json()['sid']
            assert (await client.get('/api/v1/sessions/'+sid+'/trajectory')).status_code == 200
            html = (await client.get('/')).text
            for resource in ('projects.js','source-grants.js','context-manifests.js','owned-inputs.js','channels.js','run-trace.js'):
                assert html.count('/assets/host-adapters/'+resource) == 1
                assert (await client.get('/assets/host-adapters/'+resource)).status_code == 200
            assert 'data-nav="projects"' in html and '...channelUI.actions' in html
            assert len(app.service._engines) == len(app.service._queues) == 0
            assert (await client.post('/api/account/logout')).status_code == 200
            assert (await client.get('/api/v1/projects')).status_code == 401
            assert (await client.get('/api/v1/channels')).status_code == 401
    finally:
        await close(app)


def test_event_union_and_route_registration_have_no_duplicate_owner(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    def flattened(routes):
        for route in routes:
            nested = getattr(route, 'original_router', None)
            if nested is not None:
                yield from flattened(nested.routes)
            else:
                yield route
    routes = [(method, route.path) for route in flattened(app.api.routes)
              for method in getattr(route, 'methods', ())]
    assert len(routes) == len(set(routes))
    assert ('GET','/api/v1/projects') in routes
    assert ('GET','/api/v1/channels') in routes
    assert ('GET','/api/v1/executors/codex') in routes
    assert ('GET','/api/v1/sessions/{sid}/trajectory') in routes
    assert {'executor.event','workspace.resource_committed'} <= set(EVENT_TYPES)
    assert 'workspace.resource_committed' in SYNC_TYPES


async def test_project_replay_and_password_login_survive_app_reopen(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    Accounts(tmp_path).provision_operator('unified-admin', 'synthetic-unified-password')
    expected = await app.service.projects.save({'project_id':'p-2222222222222222','expected_revision':0,'name':'Persisted synthetic'},'create')
    await close(app)
    reopened = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        assert (await reopened.service.projects.list())['projects'][0] == expected
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened.api), base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/account/login',json={'username':'unified-admin','password':'synthetic-unified-password'})).status_code == 200
            assert (await client.get('/api/v1/projects')).json()['total'] == 1
    finally:
        await close(reopened)
