"""GIVEN synthetic selections WHEN boundary conditions change THEN fail closed.

Acceptance coverage for the existing R1 contract; these are not new-feature REDs.
Only expiry time and fault scheduling are substituted, not HTTP/store/log logic.
"""
from datetime import datetime, timezone
import hashlib
import os

import pytest

from pyharness.application.source_snapshot import LIMIT
from tests.unit.test_source_grants import grant_app, issue, PID, GID, URL
from tests.unit.test_source_snapshot import BODY, PREVIEW, SNAPS, SID, command_for


@pytest.mark.parametrize('operation,final_check', [('preview', 2), ('capture', 3), ('list', 1)])
async def test_expiry_after_final_root_metadata_io_rejects(grant_app, monkeypatch, operation, final_check):
    """GIVEN valid admission WHEN root metadata IO crosses expiry THEN no success."""
    from pyharness.application import snapshot_store as target

    app, client, source = grant_app
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})).status_code == 200
    command = await command_for(client)
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    original = target.check_root_metadata
    calls = 0

    class Expired(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)

    def root_check(row):
        nonlocal calls
        result = original(row)
        calls += 1
        if calls == final_check:
            monkeypatch.setattr(target, 'datetime', Expired)
        return result

    monkeypatch.setattr(target, 'check_root_metadata', root_check)
    if operation == 'list':
        response = await client.get(SNAPS)
    else:
        response = await client.post(PREVIEW if operation == 'preview' else SNAPS,
            json=BODY if operation == 'preview' else command, headers={'Idempotency-Key': 'capture'})
    assert calls == final_check
    assert response.status_code == 403
    assert response.json()['reason'] == 'source_grant_unavailable'
    assert store.log_path.read_bytes() == before
    assert (source / 'unchanged.txt').read_bytes() == b'unchanged\r\n'


async def test_final_reread_rejects_same_size_same_mtime_content_change(grant_app, monkeypatch):
    """GIVEN unchanged metadata WHEN bytes change on final reread THEN hash rejects."""
    from pyharness.application import snapshot_store as target

    app, client, source = grant_app
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})).status_code == 200
    command = await command_for(client)
    path = source / 'unchanged.txt'
    info = path.stat()
    original_bytes = path.read_bytes()
    replacement = b'x' * len(original_bytes)
    verify = target.verify_selection
    calls = 0

    def change_before_last_read(row, files, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(replacement)  # External synthetic editor, never application writeback.
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            assert path.stat().st_size == info.st_size
            assert path.stat().st_mtime_ns == info.st_mtime_ns
        return verify(row, files, **kwargs)

    monkeypatch.setattr(target, 'verify_selection', change_before_last_read)
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    response = await client.post(SNAPS, json=command, headers={'Idempotency-Key': 'capture'})
    assert response.status_code == 409
    assert calls == 2
    assert store.log_path.read_bytes() == before
    assert path.read_bytes() == replacement
    assert (await client.get(SNAPS)).json() == {'snapshots': [], 'total': 0}


@pytest.mark.parametrize('kind', ['file-count', 'byte-count'])
async def test_exact_selection_upper_bound_commits_with_exact_disk_manifest(grant_app, kind):
    """GIVEN exactly 200 files or 10MiB WHEN confirmed THEN counts and bytes agree."""
    from pyharness.application.source_snapshot import SourceSnapshot

    app, client, source = grant_app
    if kind == 'file-count':
        selected = {f'item-{i:03}.txt': f'synthetic item {i}\r\n'.encode() for i in range(200)}
    else:
        # Long uninterrupted alphanumerics are intentionally secret-like under
        # config.redact; use bounded plain-text lines for a size-only boundary.
        selected = {'large.txt': b'x\n' * (LIMIT // 2)}
    for name, raw in selected.items():
        (source / name).write_bytes(raw)
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})).status_code == 200
    body = {**BODY, 'paths': list(selected)}
    preview = await client.post(PREVIEW, json=body)
    assert preview.status_code == 200
    assert preview.json()['file_count'] == len(selected)
    assert preview.json()['total_size_bytes'] == sum(map(len, selected.values()))
    command = {**body, 'snapshot_id': SID, 'confirmation_digest': preview.json()['confirmation_digest'],
               'policy': 'reject-secrets-utf8-v1'}
    response = await client.post(SNAPS, json=command, headers={'Idempotency-Key': 'capture'})
    assert response.status_code == 200
    summary = response.json()
    store = app.service.projects.store
    manifest = SourceSnapshot.model_validate_json((store.root / 'blobs' / (summary['digest'] + '.json')).read_bytes())
    assert [file.path for file in manifest.files] == list(selected)
    assert summary['file_count'] == len(manifest.files) == len(selected)
    assert manifest.total_size_bytes == summary['total_size_bytes'] == sum(map(len, selected.values()))
    for file in manifest.files:
        raw = selected[file.path]
        assert hashlib.sha256(raw).hexdigest() == file.sha256
        assert (store.root / 'snapshot-content' / (file.sha256 + '.bin')).read_bytes() == raw
        assert (source / file.path).read_bytes() == raw
    listing = await client.get(SNAPS)
    assert listing.status_code == 200
    assert listing.json() == {'snapshots': [summary], 'total': 1}
    assert not app.service._engines and not app.service._queues


async def test_sensitive_filename_rejected_before_any_content_open(grant_app, monkeypatch):
    """GIVEN sensitive names WHEN a mixed group is selected THEN no body opens."""
    from pyharness.application import source_snapshot as target

    app, client, source = grant_app
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})).status_code == 200
    store = app.service.projects.store
    before = store.log_path.read_bytes()

    def forbidden(*args):
        pytest.fail('rejected selection opened source contents')

    monkeypatch.setattr(target, 'read_selected', forbidden)
    response = await client.post(PREVIEW, json={**BODY, 'paths': ['unchanged.txt', '.env.local']})
    assert response.status_code == 403
    assert '.env.local' not in response.text and str(source) not in response.text
    assert store.log_path.read_bytes() == before
    assert not (store.root / 'snapshot-content').exists()


@pytest.mark.parametrize('fault', ['before-append', 'after-durable-append'])
async def test_unknown_commit_requires_reopen_and_disk_reconciliation(grant_app, monkeypatch, fault):
    """GIVEN commit uncertainty WHEN owner reopens THEN only disk facts are listed."""
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.errors import raise_code

    app, client, source = grant_app
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})).status_code == 200
    command = await command_for(client)
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    if fault == 'before-append':
        async def append(type_, payload, *, actor, sync):
            raise OSError('synthetic append failure')
        monkeypatch.setattr(store._session, 'append', append)
    else:
        original = store._verify_commit
        def readback_error(disk, event):
            original(disk, event)  # First prove the event really reached disk.
            raise_code('PERS-202', reason='synthetic_readback_failure')
        monkeypatch.setattr(store, '_verify_commit', readback_error)
    response = await client.post(SNAPS, json=command, headers={'Idempotency-Key': 'capture'})
    assert response.status_code == 503
    assert store._failed
    assert (await client.get(SNAPS)).status_code == 503
    raw = (source / 'unchanged.txt').read_bytes()
    content = store.root / 'snapshot-content' / (hashlib.sha256(raw).hexdigest() + '.bin')
    assert content.read_bytes() == raw  # Orphans are retained, never treated as success.
    after = store.log_path.read_bytes()
    assert (after == before) == (fault == 'before-append')
    await store.close()
    restored = WorkspaceStore(store.root, tenant_id='default')
    try:
        listing = await restored.snapshots(PID, GID, authorize=lambda: None)
        assert listing['total'] == (0 if fault == 'before-append' else 1)
        if listing['total']:
            summary = listing['snapshots'][0]
            assert summary['snapshot_id'] == SID
            assert summary['confirmation_digest'] == command['confirmation_digest']
            assert summary['source_seq'] == 4
        else:
            assert listing['snapshots'] == []
        assert content.read_bytes() == raw
        assert store.log_path.read_bytes() == after
    finally:
        await restored.close()
