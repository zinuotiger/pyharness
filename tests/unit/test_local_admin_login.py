"""Local password administrators use the original operator workspace."""
import httpx
import pytest

from pyharness import cli, web
from pyharness.application.accounts import Accounts, AccountError
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


def test_local_admin_setup_is_persistent_private_and_does_not_promote_existing_users(tmp_path):
    accounts = Accounts(tmp_path)
    accounts.provision('team-owner', 'synthetic-team-password', 'team')
    with pytest.raises(AccountError, match='account_exists'):
        accounts.provision_operator('team-owner', 'synthetic-admin-password')
    user = accounts.provision_operator('local-admin', 'synthetic-admin-password')
    assert user['role'] == 'operator' and user['tenant'] == 'default'
    assert not Accounts.manages_tenant(tmp_path, 'default')
    assert accounts.teams(user['user_id']) == []
    token = accounts.login('local-admin', 'synthetic-admin-password')
    assert Accounts(tmp_path).authenticate(token) == user
    assert b'synthetic-admin-password' not in accounts.path.read_bytes()
    assert token.encode() not in accounts.path.read_bytes()
    with pytest.raises(AccountError, match='invalid_credentials'):
        accounts.login('local-admin', 'synthetic-admin-password', 'team')
    with pytest.raises(AccountError, match='password_length'):
        accounts.provision_operator('too-short', 'short')


async def test_password_admin_sees_existing_operator_data_and_logout_revokes_session(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    accounts = Accounts(tmp_path)
    accounts.provision_operator('local-admin', 'synthetic-admin-password')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                                     base_url='http://127.0.0.1:60460') as client:
            # Create data using the previous launcher authentication mechanism.
            assert (await client.get(app.bootstrap_url('127.0.0.1', 60460))).status_code == 303
            sid = (await client.post('/api/v1/sessions')).json()['sid']
            client.cookies.set('pyharness_token', 'legacy-launcher-cookie', domain='127.0.0.1')
            response = await client.post('/api/account/login', json={
                'username':'local-admin', 'password':'synthetic-admin-password'})
            assert response.status_code == 200
            assert 'HttpOnly' in response.headers['set-cookie']
            assert 'pyharness_token_60460' not in client.cookies
            assert 'pyharness_token' not in client.cookies
            token = client.cookies.get('pyharness_account_60460')
            assert (await client.get('/api/account/me')).json() == {
                'mode':'local_operator', 'username':'local-admin'}
            page = await client.get('/')
            assert page.status_code == 200
            assert 'pyharness_token' not in page.headers.get('set-cookie', '')
            sessions = (await client.get('/api/v1/sessions')).json()['sessions']
            assert sid in [row['sid'] for row in sessions]
            assert (await client.get('/classic')).status_code == 200
            assert (await client.get('/api/settings/models')).status_code == 200
            assert (await client.post('/api/account/logout')).status_code == 200
            assert accounts.authenticate(token) is None
            assert (await client.get('/api/settings/models')).status_code == 401
            client.cookies.set('pyharness_account_60460', token)
            assert (await client.get('/api/settings/models')).status_code == 401
    finally:
        for service in app._service_registry.all():
            await service.shutdown()


async def test_expired_admin_and_forged_role_never_get_operator_access(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    accounts = Accounts(tmp_path)
    accounts.provision_operator('local-admin', 'synthetic-admin-password')
    accounts.provision('team-owner', 'synthetic-team-password', 'team')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                                     base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/account/users', json={
                'username':'forged-admin', 'password':'synthetic-password',
                'tenant':'default', 'role':'operator'})).status_code == 403
            assert (await client.post('/api/account/login', json={
                'username':'local-admin', 'password':'synthetic-wrong-password'})).status_code == 401
            assert (await client.post('/api/account/login', headers={'Origin':'https://foreign.invalid'}, json={
                'username':'local-admin', 'password':'synthetic-admin-password'})).status_code == 403
            assert (await client.post('/api/account/login', json={
                'username':'local-admin', 'password':'synthetic-admin-password'})).status_code == 200
            assert (await client.get('/')).status_code == 200
            with accounts.db() as db:
                db.execute('UPDATE sessions SET expires=0')
            assert (await client.get('/api/settings/models')).status_code == 401
            assert (await client.post('/api/account/login', json={
                'username':'team-owner', 'password':'synthetic-team-password', 'role':'operator'})).status_code == 200
            assert (await client.get('/classic')).status_code == 403
            assert (await client.post('/api/account/members', json={
                'username':'team-owner', 'role':'operator'})).status_code == 422
            assert (await client.get('/api/account/me')).json()['account']['role'] == 'owner'
    finally:
        for service in app._service_registry.all():
            await service.shutdown()


def test_create_admin_command_does_not_start_server_or_print_password(tmp_path, monkeypatch, capsys):
    import getpass
    monkeypatch.setattr(getpass, 'getpass', lambda prompt: 'synthetic-setup-password')
    assert web.main(['--data-dir',str(tmp_path),'--create-admin','local-admin']) == 0
    output = capsys.readouterr().out
    assert 'local-admin' in output and 'synthetic-setup-password' not in output
    accounts = Accounts(tmp_path)
    assert accounts.authenticate(accounts.login('local-admin', 'synthetic-setup-password'))['role'] == 'operator'
