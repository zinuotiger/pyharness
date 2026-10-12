"""One browser must retain its identity when switching loopback instances."""
import httpx
import pytest

from pyharness import cli
from pyharness.application.accounts import Accounts
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


@pytest.mark.parametrize('identity', ['operator', 'account', 'tenant'])
async def test_two_ports_keep_independent_browser_identities(tmp_path, identity):
    apps = [DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path / str(i)))) for i in range(2)]
    # A shared cookie jar models the browser's domain cookies across both ports.
    jar = httpx.Cookies()
    try:
        for i, app in enumerate(apps):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                                         base_url=f'http://127.0.0.1:{62041+i}') as client:
                client.cookies = jar
                if identity == 'operator':
                    response = await client.get(app.bootstrap_url('127.0.0.1', 62041+i))
                    assert response.status_code == 303
                elif identity == 'account':
                    Accounts(tmp_path / str(i)).provision('browser-user', 'synthetic-password', 'team')
                    response = await client.post('/api/account/login', json={
                        'username': 'browser-user', 'password': 'synthetic-password'})
                    assert response.status_code == 200
                else:
                    token = app._tenant_store().api_token('team')
                    response = await client.get('/?tenant=team&token=' + token)
                    assert response.status_code == 200
                jar = client.cookies
        for i, app in enumerate(apps):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                                         base_url=f'http://127.0.0.1:{62041+i}') as client:
                client.cookies = jar
                params = {'tenant': 'team'} if identity == 'tenant' else {}
                response = await client.post('/api/settings/models', params=params, json={
                    'label': f'instance-{i}', 'model': 'synthetic-model',
                    'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'synthetic-local-key'})
                assert response.status_code == 200, response.text
                assert (await client.get('/api/settings/models', params=params)).json()['profiles'][0]['label'] == f'instance-{i}'
                assert (await client.get('/api/settings/models', headers={
                    'X-PyHarness-Token': 'wrong-token'}, params=params)).status_code == (200 if identity != 'operator' else 401)
    finally:
        for app in apps:
            for service in app._service_registry.all():
                await service.shutdown()


async def test_scoped_operator_ignores_other_instances_legacy_account_cookie(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                                     base_url='http://127.0.0.1:62041') as client:
            client.cookies.set('pyharness_account', 'expired-legacy-account')
            assert (await client.get(app.bootstrap_url('127.0.0.1', 62041))).status_code == 303
            assert (await client.get('/api/account/me')).json() == {'mode': 'local_operator'}
            # An invalid scoped token must fail closed, even with a valid legacy token.
            client.cookies.clear()
            client.cookies.set('pyharness_token', app._api_token)
            client.cookies.set('pyharness_token_62041', 'expired-scoped-token')
            response = await client.get('/api/settings/models')
            assert response.status_code == 401 and response.json()['code'] == 'CRED-701'
    finally:
        for service in app._service_registry.all():
            await service.shutdown()
