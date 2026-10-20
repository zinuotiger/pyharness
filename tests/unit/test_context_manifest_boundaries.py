"""Synthetic context persistence: denied operations leave control facts unchanged."""
from datetime import datetime, timezone
import asyncio
import json

import pytest

from tests.unit.test_context_manifests import (
    grant_app, captured, save, manifest_command, task_command, TASKS, MANIFESTS, MID, TID, PID, GID,
)


@pytest.mark.parametrize('change', [
    {'schema_version': True}, {'expected_revision': True}, {'project_revision': True},
    {'grant_revision': True}, {'extra': 'not accepted'}, {'created_by': 'another-user'},
    {'manifest_id': '../escape'}, {'snapshot_digest': 'invalid'},
])
async def test_strict_manifest_command_zero_commit(grant_app, change):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    before = app.service.projects.store.log_path.read_bytes()
    assert (await save(client, MANIFESTS, manifest_command(snapshot, **change), 'strict')).status_code == 422
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('revision', 2), ('digest', 'c'*64), ('source_seq', 999)])
async def test_forged_task_version_denied(grant_app, field, value):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    task = (await save(client, TASKS, task_command(), 'target')).json()
    before = app.service.projects.store.log_path.read_bytes()
    assert (await save(client, MANIFESTS, manifest_command(snapshot, {**task, field: value}), 'forged')).status_code == 409
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('initial_task', [False, True])
async def test_target_identity_cannot_change_on_refresh(grant_app, initial_task):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    first = (await save(client, TASKS, task_command(), 'target')).json()
    assert (await save(client, MANIFESTS, manifest_command(snapshot, first if initial_task else None), 'first')).status_code == 200
    second = (await save(client, TASKS, task_command(product_task_id='pt-bbbbbbbbbbbbbbbb'), 'second-target')).json()
    before = app.service.projects.store.log_path.read_bytes()
    response = await save(client, MANIFESTS, manifest_command(snapshot, second, expected_revision=1), 'rebind')
    assert response.status_code == 409
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('withdrawal', ['archive', 'revoke', 'retire', 'expire', 'root-missing'])
async def test_withdrawal_denies_exact_get_and_idempotent_receipt(grant_app, monkeypatch, withdrawal):
    from pyharness.application import snapshot_store
    app, client, source = grant_app
    snapshot = await captured(client, source)
    target = (await save(client, TASKS, task_command(), 'target')).json()
    body = manifest_command(snapshot, target)
    assert (await save(client, MANIFESTS, body, 'manifest')).status_code == 200
    if withdrawal == 'archive':
        response = await save(client, '/api/v1/projects', {'project_id': PID, 'expected_revision': 1, 'name': 'synthetic', 'archived': True}, 'archive')
        assert response.status_code == 200
    elif withdrawal == 'revoke':
        assert (await save(client, f'/api/v1/source-grants/{GID}/revoke', {'expected_revision': 1}, 'revoke')).status_code == 200
    elif withdrawal == 'retire':
        assert (await save(client, TASKS, task_command(expected_revision=1, retired=True), 'retire')).status_code == 200
    elif withdrawal == 'root-missing':
        source.rename(source.with_name('synthetic-renamed'))  # fixture-owned synthetic directory
    else:
        class Future(datetime):
            @classmethod
            def now(cls, tz=None): return datetime(2100, 1, 1, tzinfo=timezone.utc)
        monkeypatch.setattr(snapshot_store, 'datetime', Future)
    before = app.service.projects.store.log_path.read_bytes()
    assert (await client.get(f'{MANIFESTS}/{MID}/revisions/1')).status_code == 403
    assert (await save(client, MANIFESTS, body, 'manifest')).status_code == 403
    listing = await client.get(MANIFESTS)
    assert listing.status_code == 403 if withdrawal == 'archive' else listing.json() == {'manifests': [], 'total': 0}
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('operation', ['write', 'read'])
async def test_expiry_after_final_private_io_rejects(grant_app, monkeypatch, operation):
    from pyharness.application import snapshot_store
    from pyharness.core.tenant_settings import TenantSettingsStore
    app, client, source = grant_app
    snapshot = await captured(client, source)
    store = app.service.projects.store
    class Future(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2100, 1, 1, tzinfo=timezone.utc)
    crossed = False
    if operation == 'write':
        original = TenantSettingsStore._atomic_write
        def write(path, raw):
            nonlocal crossed
            original(path, raw)
            if json.loads(raw).get('manifest_id') == MID:
                crossed = True
                monkeypatch.setattr(snapshot_store, 'datetime', Future)
        monkeypatch.setattr(TenantSettingsStore, '_atomic_write', write)
    else:
        assert (await save(client, MANIFESTS, manifest_command(snapshot), 'first')).status_code == 200
        original = store._read_definition
        def read(payload):
            nonlocal crossed
            result = original(payload)
            if payload['resource_kind'] == 'context_manifest':
                crossed = True
                monkeypatch.setattr(snapshot_store, 'datetime', Future)
            return result
        monkeypatch.setattr(store, '_read_definition', read)
    before = store.log_path.read_bytes()
    response = (await save(client, MANIFESTS, manifest_command(snapshot), 'expiry')) if operation == 'write' else await client.get(f'{MANIFESTS}/{MID}/revisions/1')
    assert crossed and response.status_code == 403
    assert store.log_path.read_bytes() == before


async def test_retirement_terminal_and_creator_stable(grant_app):
    app, client, source = grant_app
    await captured(client, source)
    assert (await save(client, TASKS, task_command(), 'target')).status_code == 200
    retired = task_command(expected_revision=1, retired=True)
    result = await save(client, TASKS, retired, 'retire')
    assert result.status_code == 200 and result.json()['status'] == 'retired'
    assert (await save(client, TASKS, retired, 'retire')).json() == result.json()
    before = app.service.projects.store.log_path.read_bytes()
    assert (await save(client, TASKS, task_command(expected_revision=2), 'revive')).status_code == 403
    assert (await save(client, TASKS, task_command(), 'target')).status_code == 403
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_all_historical_revisions_count_towards_quota(grant_app, monkeypatch):
    from pyharness.application.context_manifest_store import ContextManifestStore
    app, client, source = grant_app
    snapshot = await captured(client, source)
    monkeypatch.setattr(ContextManifestStore, 'MAX_CONTEXT_HISTORY', 2)
    for revision in range(2):
        assert (await save(client, MANIFESTS, manifest_command(snapshot, expected_revision=revision), f'r{revision}')).status_code == 200
    before = app.service.projects.store.log_path.read_bytes()
    assert (await save(client, MANIFESTS, manifest_command(snapshot, expected_revision=2), 'over')).status_code == 422
    assert (await save(client, MANIFESTS, manifest_command(snapshot, manifest_id='cm-bbbbbbbbbbbbbbbb'), 'new-id')).status_code == 422
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_manifest_byte_bound_does_not_relax_old_types(grant_app, monkeypatch):
    from pyharness.application.context_manifest_store import ContextManifestStore
    app, client, source = grant_app
    snapshot = await captured(client, source)
    store = app.service.projects.store
    assert store.MAX_BLOB_BYTES == 32768
    monkeypatch.setattr(ContextManifestStore, 'MAX_CONTEXT_BYTES', 128)
    before = store.log_path.read_bytes()
    assert (await save(client, MANIFESTS, manifest_command(snapshot), 'large')).status_code == 422
    assert store.log_path.read_bytes() == before


@pytest.mark.parametrize('damage', ['manifest-blob', 'task-blob', 'torn-log'])
async def test_corrupt_control_facts_fail_closed(grant_app, damage):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    task = (await save(client, TASKS, task_command(), 'target')).json()
    manifest = (await save(client, MANIFESTS, manifest_command(snapshot, task), 'manifest')).json()
    store = app.service.projects.store
    path = store.log_path if damage == 'torn-log' else store._blob((task if damage == 'task-blob' else manifest)['digest'])
    bad = path.read_bytes() + b'{torn' if damage == 'torn-log' else b'corrupt synthetic blob'
    path.write_bytes(bad)
    assert (await client.get(f'{MANIFESTS}/{MID}/revisions/1')).status_code == 503
    assert (await client.get(MANIFESTS)).status_code == 503
    assert path.read_bytes() == bad


async def test_unknown_commit_requires_reopen_and_exact_original_retry(grant_app, monkeypatch):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.errors import raise_code
    app, client, source = grant_app
    snapshot = await captured(client, source)
    store = app.service.projects.store
    before = len(store.log_path.read_bytes().splitlines())
    def unconfirmed(*_): raise_code('PERS-202', reason='synthetic_receipt_unknown')
    monkeypatch.setattr(store, '_verify_commit', unconfirmed)
    body = manifest_command(snapshot)
    assert (await save(client, MANIFESTS, body, 'unknown')).status_code == 503
    assert len(store.log_path.read_bytes().splitlines()) == before + 1  # actual append, unknown receipt
    assert (await save(client, MANIFESTS, body, 'unknown')).status_code == 503  # poisoned owner
    await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id=store.tenant_id)
    response = await save(client, MANIFESTS, body, 'unknown')
    assert response.status_code == 200 and response.json()['revision'] == 1
    assert len(store.log_path.read_bytes().splitlines()) == before + 1
    assert (await save(client, MANIFESTS, {**body, 'expected_revision': 1}, 'unknown')).status_code == 409


async def test_two_competing_creates_one_commit(grant_app):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    before = len(app.service.projects.store.log_path.read_bytes().splitlines())
    responses = await asyncio.gather(*(save(client, MANIFESTS, manifest_command(snapshot), key) for key in ('a', 'b')))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(app.service.projects.store.log_path.read_bytes().splitlines()) == before + 1


@pytest.mark.parametrize('kind', ['task', 'manifest'])
@pytest.mark.parametrize('fault', ['missing', 'wrong'])
async def test_blob_must_be_read_back_before_commit(grant_app, monkeypatch, kind, fault):
    from pyharness.core.tenant_settings import TenantSettingsStore
    app, client, source = grant_app
    snapshot = await captured(client, source)
    store = app.service.projects.store
    original = TenantSettingsStore._atomic_write
    def write(path, raw):
        value = json.loads(raw)
        if 'manifest_id' in value or value.get('mode') == 'context_reference_only':
            if fault == 'wrong': original(path, b'{}')
            return
        original(path, raw)
    monkeypatch.setattr(TenantSettingsStore, '_atomic_write', write)
    before = store.log_path.read_bytes()
    response = await save(client, TASKS if kind == 'task' else MANIFESTS,
                          task_command() if kind == 'task' else manifest_command(snapshot), 'bad-blob')
    assert response.status_code == 503
    assert store.log_path.read_bytes() == before


@pytest.mark.parametrize('kind', ['task', 'manifest'])
async def test_role_withdrawal_during_final_replay_zero_commit(grant_app, monkeypatch, kind):
    import httpx
    from pyharness.application.accounts import Accounts
    app, operator, source = grant_app
    accounts = Accounts(app.service._storage_root())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') as owner:
        assert (await operator.post('/api/account/users', json={'username': 'context-owner', 'password': 'synthetic-only-password', 'tenant': 'ctx-team'})).status_code == 201
        assert (await owner.post('/api/account/login', json={'username': 'context-owner', 'password': 'synthetic-only-password'})).status_code == 200
        assert (await save(owner, '/api/v1/projects', {'project_id': PID, 'expected_revision': 0, 'name': 'synthetic'}, 'project')).status_code == 200
        snapshot = await captured(owner, source)
        # Keep another owner so tenant management survives removal of this actor.
        assert (await operator.post('/api/account/users', json={'username': 'context-other', 'password': 'synthetic-only-password', 'tenant': 'other-team'})).status_code == 201
        accounts.membership('ctx-team', 'context-other', 'owner')
        store = app._service_registry.get('ctx-team').projects.store
        original = store._grant_state
        calls = 0
        def replay():
            nonlocal calls
            result = original()
            calls += 1
            if calls == 3: accounts.membership('ctx-team', 'context-owner', 'remove')
            return result
        monkeypatch.setattr(store, '_grant_state', replay)
        before = store.log_path.read_bytes()
        response = await save(owner, TASKS if kind == 'task' else MANIFESTS,
                              task_command() if kind == 'task' else manifest_command(snapshot), 'role-late')
        assert calls >= 3 and response.status_code == 403
        assert store.log_path.read_bytes() == before
    for service in app._service_registry.all(): await service.shutdown()


async def test_list_expiry_during_later_row_cannot_leak_earlier_summary(grant_app, monkeypatch):
    from pyharness.application import snapshot_store
    app, client, source = grant_app
    snapshot = await captured(client, source)
    for ident in (MID, 'cm-bbbbbbbbbbbbbbbb'):
        assert (await save(client, MANIFESTS, manifest_command(snapshot, manifest_id=ident), ident)).status_code == 200
    original = snapshot_store.check_root_metadata
    calls = 0
    class Future(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2100, 1, 1, tzinfo=timezone.utc)
    def check(row):
        nonlocal calls
        result = original(row)
        calls += 1
        if calls == 2: monkeypatch.setattr(snapshot_store, 'datetime', Future)
        return result
    monkeypatch.setattr(snapshot_store, 'check_root_metadata', check)
    before = app.service.projects.store.log_path.read_bytes()
    response = await client.get(MANIFESTS)
    assert calls >= 2
    assert response.status_code == 403 or response.json() == {'manifests': [], 'total': 0}
    assert app.service.projects.store.log_path.read_bytes() == before
