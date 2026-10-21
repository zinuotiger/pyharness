"""GIVEN a persisted synthetic grant WHEN preflight THEN only root metadata is observed."""
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest
from tests.unit.test_source_grants import grant_app, issue, PID, GID, URL

PREFLIGHT = f'{URL}/{GID}/preflight'
BODY = {'schema_version': 1, 'expected_revision': 1}


async def test_http_preflight_matches_without_writes_or_source_reads(grant_app, monkeypatch):
    app, client, source = grant_app
    issued = await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    assert issued.status_code == 200
    row = issued.json()
    store = app.service.projects.store
    before = {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'}
    original = Path.open
    def checked_open(path, *args, **kwargs):
        assert not path.is_relative_to(source), 'preflight must not read source contents'
        return original(path, *args, **kwargs)
    import os
    original_scan = os.scandir
    def checked_scan(path):
        assert not Path(path).is_relative_to(source), 'preflight must not enumerate source'
        return original_scan(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', checked_open)
        patch.setattr('os.scandir', checked_scan)
        response = await client.post(PREFLIGHT, json=BODY)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {'schema_version', 'project_id', 'grant_id', 'revision', 'root_identity',
                           'checked_at', 'status', 'digest', 'source_seq'}
    assert result['status'] == 'metadata_match'
    assert result['project_id'] == PID and result['grant_id'] == GID and result['revision'] == 1
    for key in ('root_identity', 'digest', 'source_seq'):
        assert result[key] == row[key]
    assert datetime.fromisoformat(result['checked_at'].replace('Z', '+00:00')).utcoffset() == timezone.utc.utcoffset(None)
    assert str(source) not in response.text
    after = {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'}
    assert after == before
    assert (source / 'unchanged.txt').read_bytes() == b'unchanged\r\n'
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('empty_log', [False, True])
async def test_unknown_grant_does_not_initialize_control_storage(tmp_path, empty_log):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.source_preflight import PreflightGrant
    from pyharness.errors import PyHError
    store = WorkspaceStore(tmp_path / 'empty-control', tenant_id='default')
    if empty_log:
        store.log_path.parent.mkdir(parents=True)
        store.log_path.write_bytes(b'')
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    try:
        with pytest.raises(PyHError, match='GRD-403'):
            await store.preflight_source_grant(PID, GID, PreflightGrant(**BODY))
        await store.close()
        after = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        assert after == before
    finally:
        await store.close()


async def test_malformed_body_errors_never_echo_private_input(grant_app):
    _, client, source = grant_app
    for body in ([str(source)], str(source), 123, True, None):
        response = await client.post(PREFLIGHT, content=json.dumps(body), headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert str(source) not in response.text
        assert 'input' not in response.json()
        serialized = json.dumps(response.json(), ensure_ascii=False)
        assert json.dumps(str(source), ensure_ascii=False) not in serialized
        assert '"input"' not in serialized


@pytest.mark.parametrize('body', [
    {}, {'expected_revision': 1}, {'schema_version': 1},
    {'schema_version': True, 'expected_revision': 1}, {'schema_version': '1', 'expected_revision': 1},
    {'schema_version': 2, 'expected_revision': 1}, {'schema_version': 1, 'expected_revision': True},
    {'schema_version': 1, 'expected_revision': 0}, {'schema_version': 1, 'expected_revision': -1},
    {'schema_version': 1, 'expected_revision': '1'}, {'schema_version': 1, 'expected_revision': 1.0},
    *[{**BODY, field: 'forged'} for field in ('source_path', 'root_identity', 'actor', 'actor_ref',
                                             'tenant', 'tenant_id', 'actions', 'identity', 'approved_by')],
])
async def test_strict_body_rejected_before_metadata(grant_app, monkeypatch, body):
    app, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    def forbidden(row):
        raise AssertionError('invalid request reached metadata')
    monkeypatch.setattr('pyharness.application.source_preflight.check_root_metadata', forbidden)
    before = app.service.projects.store.log_path.read_bytes()
    response = await client.post(PREFLIGHT, json=body)
    assert response.status_code == 422
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('condition', ['revoked', 'expired', 'archived', 'stale', 'cross-project', 'unknown'])
async def test_unavailable_grants_are_denied_without_metadata(grant_app, monkeypatch, condition):
    app, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    body, endpoint = dict(BODY), PREFLIGHT
    if condition == 'revoked':
        await client.post(f'/api/v1/source-grants/{GID}/revoke', json={'expected_revision': 1}, headers={'Idempotency-Key': 'revoke'})
        body['expected_revision'] = 2
    elif condition == 'expired':
        class Future(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2100, 1, 1, tzinfo=timezone.utc)
        monkeypatch.setattr('pyharness.application.source_grant_store.datetime', Future)
    elif condition == 'archived':
        await client.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 1,
            'name': 'synthetic', 'archived': True}, headers={'Idempotency-Key': 'archive'})
    elif condition == 'stale':
        await client.post(f'/api/v1/source-grants/{GID}/revoke', json={'expected_revision': 1}, headers={'Idempotency-Key': 'revoke'})
    elif condition == 'cross-project':
        other = 'p-ffffffffffffffff'
        await client.post('/api/v1/projects', json={'project_id': other, 'expected_revision': 0,
            'name': 'synthetic'}, headers={'Idempotency-Key': 'other'})
        endpoint = PREFLIGHT.replace(PID, other)
    else:
        endpoint = PREFLIGHT.replace(GID, 'sg-ffffffffffffffff')
    def forbidden(row):
        raise AssertionError('unavailable grant reached metadata')
    monkeypatch.setattr('pyharness.application.source_preflight.check_root_metadata', forbidden)
    before = app.service.projects.store.log_path.read_bytes()
    response = await client.post(endpoint, json=body)
    assert response.status_code == (409 if condition == 'stale' else 403), response.text
    assert response.json()['reason'] == ('stale_revision' if condition == 'stale' else 'source_grant_unavailable')
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('replacement', ['missing', 'directory', 'file'])
async def test_root_metadata_drift_is_denied(grant_app, replacement):
    app, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    # Preserve the synthetic original; new directory cannot reuse its inode.
    preserved = source.with_name('preserved-synthetic')
    source.rename(preserved)
    if replacement == 'directory':
        source.mkdir()
    elif replacement == 'file':
        source.write_bytes(b'synthetic non-directory')
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    response = await client.post(PREFLIGHT, json=BODY)
    assert response.status_code == 403
    reason = 'source_root_identity_changed' if replacement == 'directory' else 'source_root_unavailable'
    assert response.json()['reason'] == reason
    assert str(source) not in response.text
    assert store.log_path.read_bytes() == before
    assert (preserved / 'unchanged.txt').read_bytes() == b'unchanged\r\n'


@pytest.mark.parametrize('corruption', ['torn', 'invalid', 'blob', 'missing-blob', 'disk-cache'])
async def test_preflight_corrupt_disk_fails_closed_without_repair(grant_app, corruption):
    app, client, source = grant_app
    row = (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).json()
    store = app.service.projects.store
    target = store._blob(row['digest']) if 'blob' in corruption else store.log_path
    if corruption == 'missing-blob':
        target.rename(target.with_suffix('.preserved'))
    else:
        if corruption == 'blob':
            bad = b'corrupt synthetic blob'
        elif corruption == 'disk-cache':
            lines = target.read_bytes().splitlines(keepends=True)
            item = json.loads(lines[0]); item['payload']['title'] = 'changed synthetic title'
            bad = json.dumps(item).encode() + b'\n' + b''.join(lines[1:])
        else:
            bad = target.read_bytes() + (b'{torn' if corruption == 'torn' else b'broken\n')
        target.write_bytes(bad)
    response = await client.post(PREFLIGHT, json=BODY)
    assert response.status_code == 503
    assert response.json()['code'] == 'PERS-202'
    if corruption != 'missing-blob':
        assert target.read_bytes() == bad
    else:
        assert not target.exists()
    assert (source / 'unchanged.txt').read_bytes() == b'unchanged\r\n'


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind,ancestor', [('junction', False), ('junction', True), ('symlink', False)])
async def test_real_windows_link_replacement_is_denied(grant_app, kind, ancestor):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows link replacement test')
    _, client, source = grant_app
    if ancestor:
        parent = source.parent / 'synthetic-ancestor'
        parent.mkdir()
        source.rename(parent / source.name)
        source = parent / source.name
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    replaced = source.parent if ancestor else source
    preserved = replaced.with_name('preserved-link-target')
    replaced.rename(preserved)
    if kind == 'junction':
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(replaced), str(preserved)], capture_output=True, timeout=10)
        if result.returncode:
            pytest.skip('junction unavailable; no privilege expansion attempted')
        assert replaced.is_junction()
    else:
        try:
            replaced.symlink_to(preserved, target_is_directory=True)
        except OSError:
            pytest.skip('symlink privilege unavailable; no privilege expansion attempted')
    response = await client.post(PREFLIGHT, json=BODY)
    assert response.status_code == 403 and response.json()['reason'] == 'source_root_unavailable'
    assert str(source) not in response.text


async def test_restart_and_second_owner_rejection(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.source_preflight import PreflightGrant
    from pyharness.errors import PyHError
    app, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    store = app.service.projects.store
    rival = WorkspaceStore(store.root, tenant_id='default')
    try:
        with pytest.raises(PyHError, match='PERS-202'):
            await rival.preflight_source_grant(PID, GID, PreflightGrant(**BODY))
        before = store.log_path.read_bytes()
        await store.close()
        result = await rival.preflight_source_grant(PID, GID, PreflightGrant(**BODY))
        assert result['status'] == 'metadata_match'
        assert rival.log_path.read_bytes() == before
    finally:
        await rival.close()


async def test_two_tenants_same_ids_have_distinct_preflight(tmp_path):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.workspace_contracts import ProjectRevision
    from pyharness.application.source_grants import IssueGrant, RevokeGrant
    from pyharness.application.source_preflight import PreflightGrant
    from pyharness.errors import PyHError
    stores = [WorkspaceStore(tmp_path / tenant, tenant_id=tenant) for tenant in ('alpha', 'beta')]
    try:
        results = []
        for i, store in enumerate(stores):
            source = tmp_path / f'source-{i}'
            source.mkdir()
            await store.commit(ProjectRevision(project_id=PID, revision=1, name='synthetic'), expected_revision=0, command_id='project', actor_ref='operator')
            await store.issue_source_grant(PID, IssueGrant(**issue(source)), command_id='issue', actor_ref='operator')
            results.append(await store.preflight_source_grant(PID, GID, PreflightGrant(**BODY)))
        assert results[0]['root_identity'] != results[1]['root_identity']
        await stores[0].revoke_source_grant(GID, RevokeGrant(expected_revision=1), command_id='revoke', actor_ref='operator')
        with pytest.raises(PyHError, match='GRD-403'):
            await stores[0].preflight_source_grant(PID, GID, PreflightGrant(schema_version=1, expected_revision=2))
        assert (await stores[1].preflight_source_grant(PID, GID, PreflightGrant(**BODY)))['status'] == 'metadata_match'
    finally:
        for store in stores:
            await store.close()


async def test_real_http_roles_and_membership_revocation(grant_app):
    import httpx
    from pyharness.application.accounts import Accounts
    app, operator, source = grant_app
    transport = httpx.ASGITransport(app=app.api)
    accounts = Accounts(app.service._storage_root())
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as owner, \
                   httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as member:
            assert (await member.post(PREFLIGHT, json=BODY)).status_code == 401
            for client, name, tenant in [(owner, 'preflight-owner', 'preflight-team'), (member, 'preflight-member', 'other-team')]:
                assert (await operator.post('/api/account/users', json={'username': name, 'password': 'synthetic-only-password', 'tenant': tenant})).status_code == 201
                assert (await client.post('/api/account/login', json={'username': name, 'password': 'synthetic-only-password'})).status_code == 200
            assert (await owner.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 0, 'name': 'synthetic'}, headers={'Idempotency-Key': 'project'})).status_code == 200
            assert (await owner.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 200
            assert (await owner.post(PREFLIGHT, json=BODY)).status_code == 200
            assert (await member.post(PREFLIGHT, json=BODY)).status_code == 403
            accounts.membership('preflight-team', 'preflight-member', 'member')
            assert (await member.post('/api/account/login', json={'username': 'preflight-member', 'password': 'synthetic-only-password', 'tenant': 'preflight-team'})).status_code == 200
            for role in ('member', 'viewer'):
                accounts.membership('preflight-team', 'preflight-member', role)
                assert (await member.post(PREFLIGHT, json=BODY)).status_code == 200
                assert (await member.post(URL, json=issue(source), headers={'Idempotency-Key': 'expand'})).status_code == 403
            accounts.membership('preflight-team', 'preflight-member', 'remove')
            assert (await member.post(PREFLIGHT, json=BODY)).status_code in (401, 403)
            assert (await owner.post(PREFLIGHT, json=BODY)).status_code == 200
    finally:
        for service in app._service_registry.all():
            await service.shutdown()
