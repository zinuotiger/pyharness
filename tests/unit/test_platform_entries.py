"""Existing Platform entry points use real logs, FTS and server authorization."""
import json
from types import SimpleNamespace

import httpx
import pytest

from pyharness import cli
from pyharness.application.accounts import Accounts
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


async def test_history_edit_feedback_use_append_only_log_and_survive_restart(tmp_path):
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        sid = await app.service.create_session()
        log = await app.service.require_session(sid)
        user = await log.append('user.message', {'content': 'original cranberry'}, actor='user')
        reply = await log.append('agent.message', {'content': 'public answer'}, actor='agent')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                base_url='http://127.0.0.1', headers={'X-PyHarness-Token': app._api_token}) as client:
            response = await client.get('/api/v1/history/search', params={'q': 'cranberry'})
            assert response.status_code == 200, response.text
            assert any(h['session_id'] == sid and h['seq'] == user.seq for h in response.json()['hits'])
            response = await client.post(f'/api/v1/sessions/{sid}/messages/{user.seq}/edit',
                                        json={'text': 'corrected raspberry'})
            assert response.status_code == 200, response.text
            assert log.get(user.seq).payload['content'] == 'original cranberry'
            assert (await client.get('/api/v1/history/search', params={'q': 'cranberry'})).json()['hits'] == []
            assert (await client.get('/api/v1/history/search', params={'q': 'raspberry'})).json()['hits']
            response = await client.post(f'/api/v1/sessions/{sid}/messages/{reply.seq}/feedback',
                                        json={'kind': 'up', 'note': 'synthetic acceptance'})
            assert response.status_code == 200, response.text
            assert (await client.get(f'/api/v1/sessions/{sid}/activity')).status_code == 200
        assert not app.service._engines and not app.service._queues  # no model/runtime side effect
    finally:
        await app.service.shutdown()
    restored = DesktopApp(cli.assemble_ctx(cfg))
    try:
        result = await restored.service.platform.history_search('raspberry', sid)
        assert result['hits'] and not result['timed_out']
        events = list((await restored.service.require_session(sid)).events_after(0))
        assert any(e.type == 'user.feedback' and e.payload['target_seq'] == reply.seq for e in events)
        await restored.service.delete_session(sid)
        assert (await restored.service.platform.history_search('raspberry'))['hits'] == []
    finally:
        await restored.service.shutdown()


@pytest.mark.parametrize('role,expected', [('owner', 200), ('member', 200), ('viewer', 403)])
async def test_entry_roles_and_foreign_sessions_are_enforced(tmp_path, role, expected):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    accounts = Accounts(app._storage_root())
    accounts.provision('custodian', 'synthetic-password-owner', 'team-one')
    accounts.provision('participant', 'synthetic-password-member', 'team-one')
    accounts.membership('team-one', 'participant', role)
    accounts.provision('outsider', 'synthetic-password-other', 'team-other')
    own = app._service_registry.get('team-one')
    other = app._service_registry.get('team-other')
    sid = await own.create_session()
    log = await own.require_session(sid)
    u = await log.append('user.message', {'content': 'cranberry own'}, actor='user')
    a = await log.append('agent.message', {'content': 'answer'}, actor='agent')
    foreign = await other.create_session()
    foreign_log = await other.require_session(foreign)
    fu = await foreign_log.append('user.message', {'content': 'cranberry foreign'}, actor='user')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/account/login', json={
                'username': 'participant', 'password': 'synthetic-password-member'})).status_code == 200
            result = await client.get('/api/v1/history/search', params={'q': 'cranberry'})
            assert result.status_code == 200, result.text
            assert {h['session_id'] for h in result.json()['hits']} == {sid}
            assert 'foreign' not in result.text
            for action, seq, body in [('edit', u.seq, {'text': 'updated'}), ('feedback', a.seq, {'kind': 'up'})]:
                response = await client.post(f'/api/v1/sessions/{sid}/messages/{seq}/{action}', json=body)
                assert response.status_code == expected, response.text
            # The tenant-scoped store does not reveal another tenant's IDs.
            assert (await client.get('/api/v1/history/search', params={'q': 'cranberry', 'session_id': foreign})).status_code == 404
            # Session-path routes additionally reject foreign IDs in middleware.
            assert (await client.get(f'/api/v1/sessions/{foreign}/activity')).status_code == 403
            assert (await client.post(f'/api/v1/sessions/{foreign}/messages/{fu.seq}/edit', json={'text': 'forbidden'})).status_code == 403
            assert foreign_log.get(fu.seq).payload['content'] == 'cranberry foreign'
            assert len([e for e in foreign_log.events_after(0) if e.type == 'user.message_edited']) == 0
    finally:
        for service in app._service_registry.all():
            await service.shutdown()


async def test_plugin_lifecycle_never_loads_into_platform_engine(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    service = app.service
    service._engines['synthetic'] = SimpleNamespace(platform_runtime=object())
    try:
        result = await service.plugin_action('load', {'id': 'synthetic'})
        assert result['ok'] is False
        assert 'Platform' in json.dumps(result, ensure_ascii=False)
    finally:
        service._engines.clear()
        await service.shutdown()
