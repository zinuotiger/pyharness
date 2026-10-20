"""GIVEN an existing synthetic project WHEN explicitly authorized THEN read grants persist."""
import json
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings

PID = 'p-0123456789abcdef'
GID = 'sg-0123456789abcdef'
URL = f'/api/v1/projects/{PID}/source-grants'


@pytest.fixture
async def grant_app(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path / 'app')))
    source = tmp_path / 'synthetic-source'
    source.mkdir()
    (source / 'unchanged.txt').write_bytes(b'unchanged\r\n')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1',
                                headers={'X-PyHarness-Token': app._api_token}) as client:
        response = await client.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 0, 'name': 'synthetic'},
                                     headers={'Idempotency-Key': 'project'})
        assert response.status_code == 200
        yield app, client, source
    await app.service.shutdown()


def issue(source, **changes):
    return {'schema_version': 1, 'grant_id': GID, 'expected_revision': 0, 'source_path': str(source),
            'actions': ['read'], 'expires_at': '2099-01-01T00:00:00Z', **changes}


async def test_explicit_issue_returns_only_summary_and_persists(grant_app):
    app, c, source = grant_app
    before = (source / 'unchanged.txt').read_bytes()
    response = await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    assert response.status_code == 200, response.text
    row = response.json()
    assert set(row) == {'grant_id', 'revision', 'root_identity', 'actions', 'expires_at', 'revoked', 'digest', 'source_seq'}
    assert row['grant_id'] == GID and row['revision'] == 1 and row['source_seq'] == 3
    assert row['actions'] == ['read'] and row['revoked'] is False
    assert len(row['root_identity']) == 64
    listing = (await c.get(URL)).json()
    assert listing == {'grants': [row], 'total': 1}
    audit = (await c.get('/api/v1/workspace/audit')).json()
    assert audit['events'][0]['type'] == 'session.created'
    assert audit['events'][-1]['payload']['resource_kind'] == 'source_grant'
    assert str(source) not in json.dumps(audit)
    assert not app.service._engines and not app.service._queues
    assert (source / 'unchanged.txt').read_bytes() == before
    from pyharness.application.workspace_store import WorkspaceStore
    store = app.service.projects.store
    root = store.root
    await store.close()
    restored = WorkspaceStore(root, tenant_id='default')
    try:
        assert await restored.source_grants(PID) == [row]
    finally:
        await restored.close()


async def test_revoke_is_cas_idempotent_and_denies_new_access(grant_app):
    app, c, source = grant_app
    body = issue(source)
    first = await c.post(URL, json=body, headers={'Idempotency-Key': 'issue'})
    assert first.status_code == 200
    assert (await c.post(URL, json=body, headers={'Idempotency-Key': 'issue'})).json() == first.json()
    endpoint = f'/api/v1/source-grants/{GID}/revoke'
    revoked = await c.post(endpoint, json={'expected_revision': 1}, headers={'Idempotency-Key': 'revoke'})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()['revision'] == 2 and revoked.json()['revoked'] is True
    assert revoked.json()['root_identity'] == first.json()['root_identity']
    assert (await c.post(endpoint, json={'expected_revision': 1}, headers={'Idempotency-Key': 'revoke'})).json() == revoked.json()
    assert (await c.post(endpoint, json={'expected_revision': 1}, headers={'Idempotency-Key': 'stale'})).status_code == 409
    assert (await c.post(endpoint, json={'expected_revision': 2}, headers={'Idempotency-Key': 'revoke'})).status_code == 409
    from pyharness.errors import PyHError
    with pytest.raises(PyHError, match='GRD-403'):
        await app.service.projects.store.require_source_grant(GID)
    assert (await c.get(URL)).json()['grants'] == [revoked.json()]
    assert (await c.get('/api/v1/projects')).json()['total'] == 1
    assert (source / 'unchanged.txt').read_bytes() == b'unchanged\r\n'


@pytest.mark.parametrize('role', ['member', 'viewer'])
async def test_team_members_can_read_but_cannot_expand_roots(grant_app, role):
    from pyharness.application.accounts import Accounts, current_actor
    from pyharness.errors import PyHError
    app, c, source = grant_app
    accounts = Accounts(app.service._storage_root())
    # Synthetic account DB fixture; no passwords or real accounts are read.
    with accounts.db() as db:
        db.execute('INSERT INTO teams VALUES (?,?)', ('default', 'synthetic'))
        db.execute('INSERT INTO members VALUES (?,?,?)', ('synthetic-user', 'default', role))
    token = current_actor.set({'tenant': 'default', 'user_id': 'synthetic-user'})
    try:
        assert (await app.service.projects.list_source_grants(PID))['total'] == 0
        with pytest.raises(PyHError, match='GRD-403'):
            await app.service.projects.issue_source_grant(PID, issue(source), 'issue')
        with pytest.raises(PyHError, match='GRD-403'):
            await app.service.projects.revoke_source_grant(GID, {'expected_revision': 1}, 'revoke')
        with accounts.db() as db:
            db.execute('DELETE FROM members WHERE user_id=?', ('synthetic-user',))
        with pytest.raises(PyHError, match='GRD-403'):
            await app.service.projects.list_source_grants(PID)
    finally:
        current_actor.reset(token)


@pytest.mark.parametrize('corruption', ['torn', 'invalid', 'blob'])
async def test_grant_access_rechecks_disk_and_fails_closed(grant_app, corruption):
    app, c, source = grant_app
    row = (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).json()
    store = app.service.projects.store
    target = store._blob(row['digest']) if corruption == 'blob' else store.log_path
    bad = b'broken' if corruption == 'blob' else target.read_bytes() + (b'{torn' if corruption == 'torn' else b'broken\n')
    target.write_bytes(bad)
    assert (await c.get(URL)).status_code == 503
    assert target.read_bytes() == bad


def test_commit_event_version_rejects_bool():
    from pydantic import ValidationError
    from pyharness.events.payload import WorkspaceResourceCommittedPayload
    with pytest.raises(ValidationError):
        WorkspaceResourceCommittedPayload(schema_version=True, resource_kind='source_grant', resource_id=GID,
            revision=1, parent_revision=0, blob_ref='0'*64, digest='0'*64,
            command_id='issue', request_digest='0'*64, actor_ref='operator')


async def test_replay_rejects_grant_root_mutation(grant_app):
    from pyharness.application.source_grants import SourceGrant
    from pyharness.application.workspace_store import canonical, digest_of
    from pyharness.errors import PyHError
    app, c, source = grant_app
    row = (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).json()
    store = app.service.projects.store
    grant = json.loads(store._blob(row['digest']).read_bytes())
    # A typed but illegal second revision is not a legitimate revocation.
    invalid = SourceGrant.model_validate({**grant, 'revision': 2, 'expires_at': '2098-01-01T00:00:00Z'})
    data = canonical(invalid.model_dump())
    digest = digest_of(data)
    store._blob(digest).write_bytes(data)
    await store._session.append('workspace.resource_committed', {
        'schema_version': 1, 'resource_kind': 'source_grant', 'resource_id': GID, 'revision': 2,
        'parent_revision': 1, 'blob_ref': digest, 'digest': digest, 'command_id': 'bad-revision',
        'request_digest': '0'*64, 'actor_ref': 'local-operator'}, actor='user', sync=True)
    with pytest.raises(PyHError, match='PERS-202'):
        await store.source_grants(PID)


async def test_private_blob_identity_cannot_disagree_with_root(grant_app):
    from pyharness.application.source_grants import SourceGrant
    from pydantic import ValidationError
    app, c, source = grant_app
    row = (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).json()
    grant = json.loads(app.service.projects.store._blob(row['digest']).read_bytes())
    with pytest.raises(ValidationError):
        SourceGrant.model_validate({**grant, 'root_identity': '0'*64})


@pytest.mark.parametrize('changes', [
    {'schema_version': True}, {'schema_version': 2}, {'schema_version': '1'},
    {'actor_ref': 'forged'}, {'approved_by': 'forged'}, {'tenant_id': 'victim'},
    {'root_identity': '0'*64}, {'revoked': False}, {'project_id': PID},
    {'grant_id': '../bad'}, {'grant_id': 'sg-ABCDEF0123456789'}, {'expected_revision': True},
    {'expected_revision': 1}, {'actions': ['write']}, {'actions': ['select']},
    {'actions': ['read', 'read']}, {'actions': 'read'}, {'source_path': 123},
    {'expires_at': '2099-01-01'}, {'expires_at': '2099-01-01T00:00:00+08:00'},
    {'expires_at': 'not-time'}, {'expires_at': '2000-01-01T00:00:00Z'},
])
async def test_invalid_grant_http_input_has_no_commit(grant_app, changes):
    app, c, source = grant_app
    response = await c.post(URL, json=issue(source, **changes), headers={'Idempotency-Key': 'invalid'})
    assert response.status_code == 422, response.text
    assert str(source) not in response.text
    assert (await c.get(URL)).json()['total'] == 0
    assert (await c.get('/api/v1/workspace/audit')).json()['total'] == 2
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('path', ['relative', 'C:relative', '//server/share', '//?/C:/private',
    '//./C:/private', 'C:/private:stream', 'C:/NUL', 'C:/CON.txt', 'C:/bad.', 'C:/bad ',
    'C:/x/../y', 'C:/x/./y', 'file:///C:/private', 'C:/missing-source', 'C:/', 'C:/bad\x00'])
async def test_unsafe_or_nonexistent_roots_are_rejected(grant_app, path):
    _, c, _ = grant_app
    response = await c.post(URL, json=issue(path), headers={'Idempotency-Key': 'invalid'})
    assert response.status_code == 422, response.text
    assert path not in response.text


async def test_root_normalization_file_rejection_and_durable_conflict(grant_app):
    app, c, source = grant_app
    response = await c.post(URL, json=issue(source / 'unchanged.txt'), headers={'Idempotency-Key': 'file'})
    assert response.status_code == 422
    first = await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    assert first.status_code == 200
    other = 'sg-abcdef0123456789'
    normalized = str(source).replace('\\', '/')
    second = await c.post(URL, json=issue(normalized, grant_id=other), headers={'Idempotency-Key': 'other'})
    assert second.status_code == 200
    assert first.json()['root_identity'] == second.json()['root_identity']
    assert (await c.post(URL, json=issue(source, grant_id=other), headers={'Idempotency-Key': 'issue'})).status_code == 409
    assert (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'different-key'})).status_code == 409
    assert (await c.post(URL, json=issue(source))).status_code == 422
    assert (await c.post(URL, json=issue(source), headers={'Idempotency-Key': '../bad'})).status_code == 422
    assert not app.service._engines and not app.service._queues


async def test_expired_grant_status_denies_but_summary_and_revoke_remain(grant_app, monkeypatch):
    from datetime import datetime, timezone
    from pyharness.errors import PyHError
    import pyharness.application.source_grant_store as module
    app, c, source = grant_app
    assert (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 200
    assert (await app.service.projects.store.require_source_grant(GID))['revoked'] is False
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(module, 'datetime', Future)
    with pytest.raises(PyHError, match='GRD-403'):
        await app.service.projects.store.require_source_grant(GID)
    assert (await c.get(URL)).json()['total'] == 1
    assert (await c.post(f'/api/v1/source-grants/{GID}/revoke', json={'expected_revision': 1},
                         headers={'Idempotency-Key': 'revoke'})).status_code == 200


async def test_two_tenants_same_grant_ids_are_isolated(tmp_path):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.workspace_contracts import ProjectRevision
    from pyharness.application.source_grants import IssueGrant, RevokeGrant
    from pyharness.errors import PyHError
    stores = [WorkspaceStore(tmp_path / t, tenant_id=t) for t in ('alpha', 'beta')]
    try:
        for i, store in enumerate(stores):
            source = tmp_path / f'source-{i}'
            source.mkdir()
            await store.commit(ProjectRevision(project_id=PID, revision=1, name='synthetic'),
                expected_revision=0, command_id='project', actor_ref='operator')
            await store.issue_source_grant(PID, IssueGrant.model_validate(issue(source)), command_id='issue', actor_ref='operator')
        assert (await stores[0].source_grants(PID))[0]['root_identity'] != (await stores[1].source_grants(PID))[0]['root_identity']
        await stores[0].revoke_source_grant(GID, RevokeGrant(expected_revision=1), command_id='revoke', actor_ref='operator')
        with pytest.raises(PyHError, match='GRD-403'):
            await stores[0].require_source_grant(GID)
        assert not (await stores[1].require_source_grant(GID))['revoked']
    finally:
        for store in stores:
            await store.close()


async def test_source_link_boundary_rejected(grant_app):
    app, c, source = grant_app
    link = source.parent / 'source-link'
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'symlink privilege unavailable: {exc.winerror}')
    assert (await c.post(URL, json=issue(link), headers={'Idempotency-Key': 'link'})).status_code == 422
    assert (source / 'unchanged.txt').read_bytes() == b'unchanged\r\n'


@pytest.mark.controlled_process
async def test_real_windows_junction_boundary_rejected(grant_app):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows junction test')
    _, c, source = grant_app
    link = source.parent / 'junction'
    result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(source)], capture_output=True, timeout=10)
    if result.returncode:
        pytest.skip('junction unavailable; no privilege expansion attempted')
    assert link.is_junction()
    assert (await c.post(URL, json=issue(link), headers={'Idempotency-Key': 'junction'})).status_code == 422


async def test_web_exposes_explicit_authorization_entry(grant_app):
    _, c, _ = grant_app
    page = await c.get('/')
    assert '/assets/host-adapters/source-grants.js' in page.text
    module = await c.get('/assets/host-adapters/source-grants.js')
    assert module.status_code == 200
    assert 'Idempotency-Key' in module.text
    projects = await c.get('/assets/host-adapters/projects.js')
    assert 'data-project-action="source-grants"' in projects.text


async def test_real_http_roles_revocation_and_cross_tenant(grant_app):
    from pyharness.application.accounts import Accounts
    app, operator, source = grant_app
    transport = httpx.ASGITransport(app=app.api)
    accounts = Accounts(app.service._storage_root())
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as owner, \
                   httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as member:
            for client, name, tenant in [(owner, 'grant-owner', 'grant-team'), (member, 'grant-member', 'other-team')]:
                response = await operator.post('/api/account/users', json={'username': name,
                    'password': 'synthetic-only-password', 'tenant': tenant})
                assert response.status_code == 201
                assert (await client.post('/api/account/login', json={'username': name,
                    'password': 'synthetic-only-password'})).status_code == 200
            body = {'project_id': PID, 'name': 'synthetic', 'expected_revision': 0}
            assert (await owner.post('/api/v1/projects', json=body, headers={'Idempotency-Key': 'project'})).status_code == 200
            assert (await owner.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 200
            assert (await member.get(URL)).status_code == 403
            accounts.membership('grant-team', 'grant-member', 'member')
            # Switch via explicit login to the team; old tenant cannot select via header.
            assert (await member.post('/api/account/login', json={'username': 'grant-member',
                'password': 'synthetic-only-password', 'tenant': 'grant-team'})).status_code == 200
            assert (await member.get(URL)).json()['total'] == 1
            assert (await member.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 403
            assert (await member.post(f'/api/v1/source-grants/{GID}/revoke', json={'expected_revision': 1},
                headers={'Idempotency-Key': 'revoke'})).status_code == 403
            accounts.membership('grant-team', 'grant-member', 'viewer')
            assert (await member.get(URL)).status_code == 200
            assert (await member.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 403
            accounts.membership('grant-team', 'grant-member', 'remove')
            assert (await member.get(URL)).status_code in (401, 403)
            assert (await owner.get(URL)).json()['total'] == 1
            for service in app._service_registry.all():
                assert not service._engines and not service._queues
    finally:
        for service in app._service_registry.all():
            await service.shutdown()


async def test_old_audit_cursor_keeps_watermark_across_grant_commit(grant_app):
    _, c, source = grant_app
    first = (await c.get('/api/v1/workspace/audit?pagination_version=1&limit=1')).json()
    assert first['total'] == 2
    assert (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 200
    second = (await c.get('/api/v1/workspace/audit', params={'pagination_version': 1, 'limit': 1,
                                                          'cursor': first['next_cursor']})).json()
    assert second['total'] == 2 and second['source_watermark'] == first['source_watermark']
    assert second['events'][0]['resource_kind'] == 'project'
    refreshed = (await c.get('/api/v1/workspace/audit?pagination_version=1')).json()
    assert refreshed['total'] == 3 and refreshed['events'][-1]['resource_kind'] == 'source_grant'


async def test_grant_recorder_failure_is_not_success(grant_app, monkeypatch):
    from pyharness.errors import PyHError
    app, c, source = grant_app
    store = app.service.projects.store
    await store.projects()
    async def fail(*args, **kwargs):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(store._store, 'append', fail)
    response = await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    assert response.status_code == 503
    with pytest.raises(PyHError, match='PERS-202'):
        await store.source_grants(PID)
    assert (source / 'unchanged.txt').read_bytes() == b'unchanged\r\n'


async def test_concurrent_grant_issue_only_one_commit(grant_app):
    import asyncio
    from pyharness.application.source_grants import IssueGrant
    from pyharness.errors import PyHError
    app, _, source = grant_app
    store = app.service.projects.store
    results = await asyncio.gather(*(store.issue_source_grant(PID, IssueGrant.model_validate(issue(source)),
        command_id=k, actor_ref='operator') for k in ('first', 'second')), return_exceptions=True)
    assert sum(isinstance(row, dict) for row in results) == 1
    assert sum(isinstance(row, PyHError) and row.code == 'EVT-101' for row in results) == 1
    assert len(await store.audit()) == 3


async def test_archived_and_missing_project_do_not_grant(grant_app):
    app, c, source = grant_app
    missing = URL.replace(PID, 'p-0000000000000000')
    assert (await c.post(missing, json=issue(source), headers={'Idempotency-Key': 'missing'})).status_code == 403
    assert (await c.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 1,
        'name': 'synthetic', 'archived': True}, headers={'Idempotency-Key': 'archive'})).status_code == 200
    assert (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'archive-grant'})).status_code == 403
    assert (await c.get(URL)).json()['total'] == 0


@pytest.mark.parametrize('body', [{'expected_revision': True}, {'expected_revision': 0},
    {'expected_revision': 1, 'actor_ref': 'forged'}, {'expected_revision': 1, 'schema_version': 2}])
async def test_revoke_strict_schema(grant_app, body):
    _, c, source = grant_app
    assert (await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 200
    assert (await c.post(f'/api/v1/source-grants/{GID}/revoke', json=body,
                         headers={'Idempotency-Key': 'invalid'})).status_code == 422
    assert (await c.get(URL)).json()['grants'][0]['revision'] == 1
