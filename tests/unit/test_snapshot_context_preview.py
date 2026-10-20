"""GIVEN a committed synthetic copy WHEN previewing context THEN bind exact immutable refs."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.conftest import isolated_process_environment
from tests.unit.test_source_grants import grant_app, issue, URL, PID, GID
from tests.unit.test_source_snapshot import SNAPS, BODY, SID, command_for

CONTEXT = f'{URL}/{GID}/snapshot-context-previews'


async def captured(client, source):
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})).status_code == 200
    command = await command_for(client)
    result = await client.post(SNAPS, json=command, headers={'Idempotency-Key': 'capture'})
    assert result.status_code == 200
    return {'schema_version': 1, 'expected_revision': 1, 'snapshot_id': SID, 'snapshot_digest': result.json()['digest']}


async def test_context_exact_immutable_refs_zero_source_body_reads_or_commits(grant_app, monkeypatch):
    from pyharness.application import source_snapshot
    from pyharness.application.workspace_store import canonical, digest_of
    app, client, source = grant_app
    body = await captured(client, source)
    store = app.service.projects.store
    before_log = store.log_path.read_bytes()
    before_files = {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'}
    # External synthetic editor changes live source; the preview must use the old immutable copy.
    raw = b'external synthetic edit\r\n'
    (source / 'unchanged.txt').write_bytes(raw)
    original_open = Path.open
    original_descriptor_open = os.open
    def guarded_open(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(source.resolve()), 'context preview opened live source contents'
        return original_open(path, *args, **kwargs)
    def guarded_descriptor(path, *args, **kwargs):
        assert not Path(path).resolve().is_relative_to(source.resolve()), 'context preview opened live source descriptor'
        return original_descriptor_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', guarded_open)
        patch.setattr(os, 'open', guarded_descriptor)
        patch.setattr(source_snapshot, 'read_selected', lambda *args: pytest.fail('live source read'))
        response = await client.post(CONTEXT, json=body)
        again = await client.post(CONTEXT, json=body)
    assert response.status_code == 200
    data = response.json()
    assert data == again.json()
    assert set(data) == {'schema_version', 'project_id', 'grant_id', 'grant_revision', 'snapshot_id', 'snapshot_revision', 'snapshot_digest', 'source_seq', 'policy', 'status', 'items', 'file_count', 'total_size_bytes', 'context_digest'}
    assert data['status'] == 'context_observed'
    assert data['snapshot_digest'] == body['snapshot_digest'] and data['snapshot_id'] == SID
    assert data['project_id'] == PID and data['grant_id'] == GID and data['grant_revision'] == 1
    assert data['source_seq'] == 4 and data['snapshot_revision'] == 1
    assert data['file_count'] == 1 and data['total_size_bytes'] == 11
    assert data['items'] == [{'input_index': 0, 'path': 'unchanged.txt', 'size_bytes': 11, 'content_digest': hashlib.sha256(b'unchanged\r\n').hexdigest()}]
    manifest = {key: value for key, value in data.items() if key != 'context_digest'}
    assert data['context_digest'] == digest_of(canonical(manifest))
    assert str(source) not in response.text and 'local-operator' not in response.text
    assert 'device' not in response.text and 'inode' not in response.text and 'external synthetic edit' not in response.text
    assert store.log_path.read_bytes() == before_log
    assert {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'} == before_files
    assert (source / 'unchanged.txt').read_bytes() == raw
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('change', [
    {'schema_version': True}, {'expected_revision': True}, {'schema_version': 2},
    {'snapshot_id': 'ss-invalid'}, {'snapshot_digest': 'invalid'}, {'source_path': 'synthetic-only'},
    {'actor': 'forged'}, {'tenant': 'forged'}, {'paths': ['unchanged.txt']},
], ids=['bool-version', 'bool-revision', 'unknown-version', 'bad-id', 'bad-digest', 'path', 'actor', 'tenant', 'paths'])
async def test_context_strict_input_rejects_without_commit(grant_app, change):
    app, client, source = grant_app
    body = await captured(client, source)
    before = app.service.projects.store.log_path.read_bytes()
    assert (await client.post(CONTEXT, json={**body, **change})).status_code == 422
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('change,status', [({'snapshot_id': 'ss-1111111111111111'}, 403), ({'snapshot_digest': 'a' * 64}, 409), ({'expected_revision': 2}, 409)])
async def test_context_snapshot_binding_and_digest_reject(grant_app, change, status):
    app, client, source = grant_app
    body = await captured(client, source)
    before = app.service.projects.store.log_path.read_bytes()
    assert (await client.post(CONTEXT, json={**body, **change})).status_code == status
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('fault', ['revoke', 'archive', 'corrupt', 'expiry'])
async def test_context_current_authorization_and_disk_integrity(grant_app, monkeypatch, fault):
    from pyharness.application import snapshot_store
    app, client, source = grant_app
    body = await captured(client, source)
    store = app.service.projects.store
    if fault == 'revoke':
        assert (await client.post(f'/api/v1/source-grants/{GID}/revoke', json={'schema_version': 1, 'expected_revision': 1}, headers={'Idempotency-Key': 'revoke'})).status_code == 200
        body['expected_revision'] = 2  # Current CAS, so this exercises revoked authorization itself.
    elif fault == 'archive':
        assert (await client.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 1, 'name': 'synthetic', 'archived': True}, headers={'Idempotency-Key': 'archive'})).status_code == 200
    elif fault == 'corrupt':
        content = next((store.root / 'snapshot-content').glob('*.bin'))
        content.write_bytes(b'corrupt synthetic content')
    else:
        class Expired(datetime):
            @classmethod
            def now(cls, tz=None): return datetime(2100, 1, 1, tzinfo=timezone.utc)
        monkeypatch.setattr(snapshot_store, 'datetime', Expired)
    before = store.log_path.read_bytes()
    response = await client.post(CONTEXT, json=body)
    assert response.status_code == (503 if fault == 'corrupt' else 403)
    assert store.log_path.read_bytes() == before


async def test_context_cross_grant_parent_binding_rejects(grant_app):
    app, client, source = grant_app
    body = await captured(client, source)
    second = 'sg-1111111111111111'
    assert (await client.post(URL, json={**issue(source), 'grant_id': second}, headers={'Idempotency-Key': 'grant-two'})).status_code == 200
    before = app.service.projects.store.log_path.read_bytes()
    response = await client.post(f'{URL}/{second}/snapshot-context-previews', json=body)
    assert response.status_code == 403
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_context_expiry_after_private_io_rejects_without_commit(grant_app, monkeypatch):
    from pyharness.application import snapshot_context, snapshot_store
    app, client, source = grant_app
    body = await captured(client, source)
    original = snapshot_context.build_context_preview
    class Expired(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2100, 1, 1, tzinfo=timezone.utc)
    def after_read(row):
        result = original(row)
        monkeypatch.setattr(snapshot_store, 'datetime', Expired)
        return result
    monkeypatch.setattr(snapshot_context, 'build_context_preview', after_read)
    before = app.service.projects.store.log_path.read_bytes()
    assert (await client.post(CONTEXT, json=body)).status_code == 403
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_context_real_http_role_matrix_and_tenant_isolation(grant_app):
    import httpx
    from pyharness.application.accounts import Accounts
    app, operator, source = grant_app
    accounts = Accounts(app.service._storage_root())
    transport = httpx.ASGITransport(app=app.api)
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as owner, httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as member:
            assert (await member.post(CONTEXT, json={})).status_code == 401
            for client, user, tenant in [(owner, 'context-owner', 'context-team'), (member, 'context-member', 'other-team')]:
                assert (await operator.post('/api/account/users', json={'username': user, 'password': 'synthetic-only-password', 'tenant': tenant})).status_code == 201
                assert (await client.post('/api/account/login', json={'username': user, 'password': 'synthetic-only-password'})).status_code == 200
            assert (await owner.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 0, 'name': 'synthetic'}, headers={'Idempotency-Key': 'project'})).status_code == 200
            body = await captured(owner, source)
            assert (await owner.post(CONTEXT, json=body)).status_code == 200
            assert (await member.post(CONTEXT, json=body)).status_code == 403
            accounts.membership('context-team', 'context-member', 'member')
            assert (await member.post('/api/account/login', json={'username': 'context-member', 'password': 'synthetic-only-password', 'tenant': 'context-team'})).status_code == 200
            assert (await member.post(CONTEXT, json=body)).status_code == 200
            accounts.membership('context-team', 'context-member', 'viewer')
            assert (await member.post(CONTEXT, json=body)).status_code == 403
            accounts.membership('context-team', 'context-member', 'remove')
            assert (await member.post(CONTEXT, json=body)).status_code in (401, 403)
    finally:
        for service in app._service_registry.all():
            await service.shutdown()
