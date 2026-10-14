"""Pinned conversations share the existing persistent, tenant-scoped metadata."""
import httpx
import pytest

from pyharness import cli
from pyharness.application.accounts import Accounts
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


async def test_pin_archive_rename_and_restart(tmp_path):
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        sid = await app.service.create_session()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                base_url='http://127.0.0.1', headers={'X-PyHarness-Token': app._api_token}) as client:
            endpoint = f'/api/v1/sessions/{sid}/metadata'
            for invalid in (1, 'true', None, [], {}):
                assert (await client.post(endpoint, json={'pinned': invalid})).status_code == 422
            assert (await client.post(endpoint, json={'title': 'Pinned project', 'pinned': True})).status_code == 200
            row = (await client.get('/api/v1/sessions')).json()['sessions'][0]
            assert row['sid'] == sid and row['pinned'] is True and row['title'] == 'Pinned project'
            assert (await client.post(endpoint, json={'archived': True})).status_code == 200
            assert (await client.get('/api/v1/dashboard')).json()['sessions'] == []
            assert (await client.post(endpoint, json={'archived': False, 'title': 'Renamed project'})).status_code == 200
            row = (await client.get('/api/v1/dashboard')).json()['sessions'][0]
            assert row['pinned'] is True and row['title'] == 'Renamed project'
        assert not app.service._engines and not app.service._queues
    finally:
        await app.service.shutdown()
    restored = DesktopApp(cli.assemble_ctx(cfg))
    try:
        row = (await restored.service.platform.sessions())['sessions'][0]
        assert row['sid'] == sid and row['pinned'] is True and row['archived'] is False
        await restored.service.platform.session_metadata(sid, {'pinned': False})
        assert (await restored.service.platform.sessions())['sessions'][0]['pinned'] is False
    finally:
        await restored.service.shutdown()


@pytest.mark.parametrize('role,expected', [('owner', 200), ('member', 403), ('viewer', 403)])
async def test_pin_keeps_existing_metadata_permissions(tmp_path, role, expected):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    accounts = Accounts(app._storage_root())
    accounts.provision('pin-owner', 'synthetic-pin-owner-password', 'pin-team')
    accounts.provision('pin-member', 'synthetic-pin-member-password', 'pin-team')
    accounts.membership('pin-team', 'pin-member', role)
    own = app._service_registry.get('pin-team')
    other = app._service_registry.get('other-team')
    sid, foreign = await own.create_session(), await other.create_session()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/account/login', json={
                'username': 'pin-member', 'password': 'synthetic-pin-member-password'})).status_code == 200
            assert (await client.post(f'/api/v1/sessions/{sid}/metadata', json={'pinned': True})).status_code == expected
            assert (await client.post(f'/api/v1/sessions/{foreign}/metadata', json={'pinned': True})).status_code == 403
            rows = (await client.get('/api/v1/sessions')).json()['sessions']
            assert {r['sid'] for r in rows} == {sid}
            assert bool(rows[0].get('pinned')) is (expected == 200)
            assert not (await other.platform.sessions())['sessions'][0].get('pinned')
    finally:
        for service in app._service_registry.all():
            await service.shutdown()
