"""Real ASGI auth/account chain, entirely fixture-owned synthetic users and data."""
import asyncio
import httpx
import pytest

from tests.unit.test_context_manifests import grant_app, captured, save, manifest_command, task_command, TASKS, MANIFESTS, PID, MID


@pytest.fixture
async def context_team(grant_app):
    from pyharness.application.accounts import Accounts
    app, operator, source = grant_app
    accounts = Accounts(app.service._storage_root())
    clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') for _ in range(2)]
    owner, member = clients
    try:
        for name, tenant, client in [('ctx-owner', 'context-team', owner), ('ctx-member', 'other-team', member)]:
            assert (await operator.post('/api/account/users', json={'username': name, 'password': 'synthetic-only-password', 'tenant': tenant})).status_code == 201
            assert (await client.post('/api/account/login', json={'username': name, 'password': 'synthetic-only-password'})).status_code == 200
        assert (await save(owner, '/api/v1/projects', {'project_id': PID, 'expected_revision': 0, 'name': 'synthetic'}, 'project')).status_code == 200
        snapshot = await captured(owner, source)
        yield app, owner, member, accounts, snapshot
    finally:
        for client in clients: await client.aclose()
        for service in app._service_registry.all(): await service.shutdown()


async def join(member, accounts, role='member'):
    accounts.membership('context-team', 'ctx-member', role)
    assert (await member.post('/api/account/login', json={'username': 'ctx-member', 'password': 'synthetic-only-password', 'tenant': 'context-team'})).status_code == 200


async def test_cross_tenant_and_same_team_private_ownership(context_team):
    app, owner, member, accounts, snapshot = context_team
    task = (await save(owner, TASKS, task_command(), 'owner-task')).json()
    row = (await save(owner, MANIFESTS, manifest_command(snapshot, task), 'owner-manifest')).json()
    assert row['status'] == 'current'
    assert (await member.get(f'{MANIFESTS}/{MID}/revisions/1')).status_code == 403
    await join(member, accounts)
    assert (await member.get(MANIFESTS)).json() == {'manifests': [], 'total': 0}
    assert (await member.get(TASKS)).json() == {'task_references': [], 'total': 0}
    before = app._service_registry.get('context-team').projects.store.log_path.read_bytes()
    assert (await member.get(f'{MANIFESTS}/{MID}/revisions/1')).status_code == 403
    assert (await save(member, TASKS, task_command(expected_revision=1), 'steal-task')).status_code == 403
    assert (await save(member, MANIFESTS, manifest_command(snapshot, task, manifest_id='cm-bbbbbbbbbbbbbbbb'), 'steal-ref')).status_code == 403
    assert (await save(member, MANIFESTS, manifest_command(snapshot, expected_revision=1), 'steal-id')).status_code == 403
    assert app._service_registry.get('context-team').projects.store.log_path.read_bytes() == before
    own_task = (await save(member, TASKS, task_command(product_task_id='pt-bbbbbbbbbbbbbbbb'), 'member-task')).json()
    own_body = manifest_command(snapshot, own_task, manifest_id='cm-bbbbbbbbbbbbbbbb')
    own = await save(member, MANIFESTS, own_body, 'member-manifest')
    assert own.status_code == 200
    assert (await owner.get(f'{MANIFESTS}/cm-bbbbbbbbbbbbbbbb/revisions/1')).status_code == 403
    assert (await member.get(MANIFESTS)).json()['total'] == 1
    assert not app._service_registry.get('context-team')._engines


@pytest.mark.parametrize('role', ['viewer', 'remove'])
async def test_role_revocation_blocks_get_post_and_receipt(context_team, role):
    app, owner, member, accounts, snapshot = context_team
    await join(member, accounts)
    body = manifest_command(snapshot)
    assert (await save(member, MANIFESTS, body, 'own')).status_code == 200
    accounts.membership('context-team', 'ctx-member', role)
    before = app._service_registry.get('context-team').projects.store.log_path.read_bytes()
    for response in [await member.get(MANIFESTS), await member.get(f'{MANIFESTS}/{MID}/revisions/1'),
                     await member.get(TASKS), await save(member, MANIFESTS, body, 'own'),
                     await save(member, TASKS, task_command(), 'task')]:
        assert response.status_code in (401, 403)
    assert app._service_registry.get('context-team').projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('operation', ['get', 'list', 'manifest', 'task'])
async def test_waiting_operation_rechecks_role_after_owner_lock(context_team, operation):
    app, owner, member, accounts, snapshot = context_team
    await join(member, accounts)
    body = manifest_command(snapshot)
    assert (await save(member, MANIFESTS, body, 'own')).status_code == 200
    store = app._service_registry.get('context-team').projects.store
    before = store.log_path.read_bytes()
    await store._lock.acquire()
    try:
        if operation == 'get': operation_task = asyncio.create_task(member.get(f'{MANIFESTS}/{MID}/revisions/1'))
        elif operation == 'list': operation_task = asyncio.create_task(member.get(MANIFESTS))
        else: operation_task = asyncio.create_task(save(member, TASKS if operation == 'task' else MANIFESTS,
                                                       task_command() if operation == 'task' else {**body, 'expected_revision': 1}, 'waiting'))
        for _ in range(50):
            if store._lock._waiters: break
            await asyncio.sleep(.01)
        assert store._lock._waiters
        accounts.membership('context-team', 'ctx-member', 'remove')
    finally: store._lock.release()
    assert (await operation_task).status_code in (401, 403)
    assert store.log_path.read_bytes() == before


@pytest.mark.parametrize('operation', ['get', 'list'])
async def test_root_metadata_io_cannot_bypass_final_role_check(context_team, monkeypatch, operation):
    from pyharness.application import snapshot_store
    app, owner, member, accounts, snapshot = context_team
    await join(member, accounts)
    assert (await save(member, MANIFESTS, manifest_command(snapshot), 'own')).status_code == 200
    original = snapshot_store.check_root_metadata
    def check(row):
        result = original(row)
        accounts.membership('context-team', 'ctx-member', 'remove')
        return result
    monkeypatch.setattr(snapshot_store, 'check_root_metadata', check)
    before = app._service_registry.get('context-team').projects.store.log_path.read_bytes()
    response = await member.get(MANIFESTS if operation == 'list' else f'{MANIFESTS}/{MID}/revisions/1')
    assert response.status_code == 403
    assert app._service_registry.get('context-team').projects.store.log_path.read_bytes() == before
