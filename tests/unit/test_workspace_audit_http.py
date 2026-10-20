"""GIVEN real authenticated API WHEN requesting v1 pages THEN no execution occurs."""
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings
from pyharness.application.accounts import Accounts


@pytest.fixture
async def app(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    yield app
    await app.service.shutdown()


async def test_versioned_http_audit_authorizes_each_page_and_preserves_legacy(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') as client:
        path='/api/v1/workspace/audit'
        assert (await client.get(path, params={'pagination_version':1})).status_code == 401
        client.headers['X-PyHarness-Token']=app._api_token
        for revision in range(3):
            response=await client.post('/api/v1/projects', json={'project_id':'p-0123456789abcdef',
                'expected_revision':revision, 'name':'synthetic'}, headers={'Idempotency-Key':f'rev-{revision}'})
            assert response.status_code == 200
        legacy=(await client.get(path)).json()
        assert set(legacy) == {'events','total'} and legacy['total'] == 4
        first=await client.get(path, params={'pagination_version':1, 'limit':2})
        assert first.status_code == 200
        first=first.json()
        assert first['page_count'] == 2 and first['total'] == 4 and first['has_more']
        assert 'tenant_id' not in str(first) and str(app.service.tenant_root()) not in str(first)
        params={'pagination_version':1,'limit':2,'cursor':first['next_cursor']}
        second=(await client.get(path,params=params)).json()
        assert [e['seq'] for e in second['events']] == [3,4]
        assert not second['has_more'] and second['next_cursor'] is None
        client.headers.pop('X-PyHarness-Token')
        assert (await client.get(path,params=params)).status_code == 401
        assert not app.service._queues and not app.service._engines
        assert (await app.service.list_sessions())['sessions'] == []


@pytest.mark.parametrize('params', [
    {'limit':'2'}, {'pagination_version':'2'}, {'pagination_version':'true'},
    {'pagination_version':'1','limit':'0'}, {'pagination_version':'1','limit':'201'},
    {'pagination_version':'1','limit':'1.0'}, {'pagination_version':'1','cursor':''},
    {'pagination_version':'1','cursor':'forged'}, {'pagination_version':'1','project_id':'../escape'},
    {'pagination_version':'1','offset':'2'},
    [('pagination_version','1'),('limit','1'),('limit','2')],
])
async def test_http_audit_rejects_malformed_query_without_execution(app, params):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',
                                headers={'X-PyHarness-Token':app._api_token}) as client:
        result=await client.get('/api/v1/workspace/audit',params=params)
        assert result.status_code == 422, result.text
        assert not app.service._queues and not app.service._engines


async def test_cross_tenant_http_audit_requires_tenant_authentication(app):
    # Existing PlatformRoute consumes tenant before workspace query validation.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',
                                headers={'X-PyHarness-Token':app._api_token}) as client:
        result=await client.get('/api/v1/workspace/audit',params={'pagination_version':1,'tenant':'victim'})
        assert result.status_code == 401
        assert result.json()['code'] == 'CRED-701'
        assert not app.service._queues and not app.service._engines


async def test_real_account_subject_binding_and_membership_revocation(app, tmp_path):
    accounts = Accounts(tmp_path)
    accounts.provision('audit-owner', 'synthetic-owner-password', 'audit-team')
    accounts.provision('audit-viewer', 'synthetic-viewer-password', 'other-team')
    accounts.membership('audit-team','audit-viewer','viewer')
    transport=httpx.ASGITransport(app=app.api)
    try:
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as owner, \
                   httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as viewer:
            for client,name in ((owner,'owner'),(viewer,'viewer')):
                response=await client.post('/api/account/login',json={'username':'audit-'+name,
                    'password':'synthetic-'+name+'-password','tenant':'audit-team'})
                assert response.status_code == 200
            for rev in range(3):
                response=await owner.post('/api/v1/projects',json={'project_id':'p-0123456789abcdef',
                    'expected_revision':rev,'name':'synthetic'},headers={'Idempotency-Key':f'rev-{rev}'})
                assert response.status_code == 200, response.text
            path='/api/v1/workspace/audit'
            first=(await viewer.get(path,params={'pagination_version':1,'limit':2})).json()
            params={'pagination_version':1,'limit':2,'cursor':first['next_cursor']}
            # Owner has access to same data but cannot reuse another subject's cursor.
            assert (await owner.get(path,params=params)).status_code == 422
            assert (await viewer.get(path,params=params)).status_code == 200
            accounts.membership('audit-team','audit-viewer','remove')
            assert (await viewer.get(path,params=params)).status_code == 401
            service=app._service_registry.get('audit-team')
            assert not service._queues and not service._engines
    finally:
        for service in app._service_registry.all():
            await service.shutdown()
