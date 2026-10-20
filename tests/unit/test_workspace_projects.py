"""GIVEN authenticated synthetic HTTP clients WHEN editing projects THEN audited revisions persist."""
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


@pytest.fixture
async def app(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    yield app
    await app.service.shutdown()


async def test_project_http_create_revision_archive_and_audit(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') as c:
        assert (await c.get('/api/v1/projects')).status_code == 401
        c.headers['X-PyHarness-Token'] = app._api_token
        body = {'project_id':'p-0123456789abcdef','expected_revision':0, 'name':'合成项目'}
        response = await c.post('/api/v1/projects', json=body, headers={'Idempotency-Key':'synthetic-create'})
        assert response.status_code == 200, response.text
        row = response.json()
        assert row['project']['revision'] == 1
        assert (await c.post('/api/v1/projects', json=body, headers={'Idempotency-Key':'synthetic-create'})).json() == row
        assert (await c.get('/api/v1/projects')).json()['projects'] == [row]
        body.update(expected_revision=1, name='合成项目新版', archived=True)
        updated = await c.post('/api/v1/projects', json=body, headers={'Idempotency-Key':'synthetic-archive'})
        assert updated.status_code == 200, updated.text
        assert updated.json()['project']['revision'] == 2
        audit = (await c.get('/api/v1/workspace/audit')).json()
        assert audit['total'] == 3
        assert audit['events'][0]['type'] == 'session.created'
        assert audit['events'][2]['payload']['actor_ref'] == 'local-operator'
        assert (await c.get('/api/v1/sessions')).json()['sessions'] == []
        assert not app.service._queues and not app.service._engines
        assert not list(app.service._tenant_sessions_dir().glob('*.jsonl'))


async def test_project_page_exposes_real_module_and_navigation(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1',
                                 headers={'X-PyHarness-Token':app._api_token}) as c:
        page = await c.get('/')
        assert 'data-nav="projects"' in page.text
        assert '/assets/host-adapters/projects.js' in page.text
        module = await c.get('/assets/host-adapters/projects.js')
        assert module.status_code == 200
        assert 'Idempotency-Key' in module.text


@pytest.mark.parametrize('overrides', [
    {'tenant_id':'victim'}, {'actor_ref':'forged'}, {'host_root':'C:/private'},
    {'project_id':'../escape'}, {'expected_revision':True}, {'name':'   '},
    {'schema_version':2}, {'description':'x' * 2001}])
async def test_invalid_or_forged_project_input_has_no_commit(app, overrides):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1',
                                 headers={'X-PyHarness-Token':app._api_token}) as c:
        response = await c.post('/api/v1/projects', json={
            'project_id':'p-0123456789abcdef','expected_revision':0,'name':'synthetic',**overrides},
            headers={'Idempotency-Key':'forged'})
        assert response.status_code == 422
        assert (await c.get('/api/v1/projects')).json()['total'] == 0
        assert (await c.get('/api/v1/workspace/audit')).json()['total'] == 1
        assert not app.service._queues and not app.service._engines


async def test_http_conflict_cannot_overwrite_new_revision(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1',
                                 headers={'X-PyHarness-Token':app._api_token}) as c:
        body = {'project_id':'p-0123456789abcdef','expected_revision':0,'name':'synthetic'}
        assert (await c.post('/api/v1/projects', json=body, headers={'Idempotency-Key':'create'})).status_code == 200
        for key in ('create', 'stale'):
            response = await c.post('/api/v1/projects', json={**body,'name':'new body'}, headers={'Idempotency-Key':key})
            assert response.status_code == 409, response.text
        assert (await c.get('/api/v1/projects')).json()['projects'][0]['project']['name'] == 'synthetic'


async def test_direct_cross_tenant_actor_rejected(app):
    from pyharness.application.accounts import current_actor
    from pyharness.errors import PyHError
    token = current_actor.set({'tenant':'victim','user_id':'forged'})
    try:
        with pytest.raises(PyHError, match='GRD-403'):
            await app.service.projects.list()
        with pytest.raises(PyHError, match='GRD-403'):
            await app.service.projects.save({'project_id':'p-0123456789abcdef','expected_revision':0,'name':'synthetic'}, 'forged')
    finally:
        current_actor.reset(token)
