"""S04 current authority, concurrent revisions, ambiguous commits and both input kinds."""
import asyncio
from datetime import datetime, timezone
import hashlib
import pytest

from tests.unit.test_product_tasks import grant_app, body, post, prepared, ref, TASKS, PT, PID
from tests.unit.test_context_manifest_permissions import context_team, join
from tests.unit.test_workspace_git_preview import git_repo, git, dirty, META
from tests.unit.test_workspace_snapshots import capture, selection, manifest, setup, MANIFESTS
from tests.unit.test_workspace_owned_inputs import INPUTS, WI, command


async def check(client, task):
    return await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1,
                             'task_ref': ref(task, 'product_task_id')})


async def test_two_concurrent_cas_commands_have_one_winner(grant_app):
    app, client, _ = grant_app
    assert (await post(client, body())).status_code == 200
    results = await asyncio.gather(post(client, body(expected_revision=1, title='One'), 'one'),
                                    post(client, body(expected_revision=1, title='Two'), 'two'))
    assert sorted(r.status_code for r in results) == [200, 409]
    events = await app.service.projects.store.audit()
    assert sum(e.get('payload', {}).get('resource_kind') == 'task_definition' for e in events) == 2


async def test_team_member_can_create_but_no_other_subject_can_take_over(context_team):
    _, owner, member, accounts, _ = context_team
    assert (await post(owner, body())).status_code == 200
    assert (await member.get(f'{TASKS}/{PT}/revisions/1')).status_code == 403
    await join(member, accounts)
    assert (await member.get(TASKS)).json() == {'tasks': [], 'total': 0}
    assert (await post(member, body(expected_revision=1), 'take-over')).status_code == 403
    own = body(product_task_id='pt-1111111111111111')
    assert (await post(member, own, 'member-definition')).status_code == 200
    assert (await owner.get(f'{TASKS}/pt-1111111111111111/revisions/1')).status_code == 403


@pytest.mark.parametrize('role', ['viewer', 'remove'])
async def test_role_revocation_blocks_reads_writes_and_preflight(context_team, role):
    app, _, member, accounts, _ = context_team
    await join(member, accounts)
    response = await post(member, body())
    assert response.status_code == 200, response.text
    accounts.membership('context-team', 'ctx-member', role)
    store = app._service_registry.get('context-team').projects.store
    before = store.log_path.read_bytes()
    for result in [await member.get(TASKS), await member.get(f'{TASKS}/{PT}/revisions/1'),
                   await post(member, body()), await check(member, response.json())]:
        assert result.status_code in (401, 403)
    assert store.log_path.read_bytes() == before


async def test_waiting_command_rechecks_membership_after_lock(context_team):
    app, _, member, accounts, _ = context_team
    await join(member, accounts)
    store = app._service_registry.get('context-team').projects.store
    before = store.log_path.read_bytes()
    await store._lock.acquire()
    try:
        waiting = asyncio.create_task(post(member, body()))
        for _ in range(60):
            if store._lock._waiters:
                break
            await asyncio.sleep(.01)
        assert store._lock._waiters
        accounts.membership('context-team', 'ctx-member', 'remove')
    finally:
        store._lock.release()
    assert (await waiting).status_code in (401, 403)
    assert store.log_path.read_bytes() == before


async def test_unknown_commit_reopens_and_reuses_original_key_once(grant_app, monkeypatch):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.errors import raise_code
    app, client, _ = grant_app
    store = app.service.projects.store
    original = store._verify_commit
    def lost_ack(disk, event):
        original(disk, event)
        raise_code('PERS-202', reason='synthetic_lost_ack')
    monkeypatch.setattr(store, '_verify_commit', lost_ack)
    assert (await post(client, body())).status_code == 503
    before = store.log_path.read_bytes()
    await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    response = await post(client, body())
    assert response.status_code == 200, response.text
    assert response.json()['revision'] == 1
    assert (await post(client, body(title='changed'))).status_code == 409
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('change', ['project', 'manifest', 'task'])
async def test_prior_observation_cannot_authorize_after_version_change(grant_app, change):
    from tests.unit.test_context_manifests import MANIFESTS as SELECTED, manifest_command
    from tests.unit.test_source_snapshot import SID
    app, client, source = grant_app
    cm, _, binding = await prepared(client, source)
    response = await post(client, body(input_binding=binding))
    assert response.status_code == 200, response.text
    task = response.json()
    assert (await check(client, task)).status_code == 200
    if change == 'project':
        result = await client.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 1,
            'name': 'New project revision'}, headers={'Idempotency-Key': 'project-revise'})
    elif change == 'manifest':
        result = await client.post(SELECTED, json=manifest_command({'snapshot_digest': cm['snapshot_digest']}, expected_revision=1),
                                   headers={'Idempotency-Key': 'manifest-revise'})
    else:
        result = await post(client, body(expected_revision=1, title='New task revision', input_binding=binding), 'task-revise')
    assert result.status_code == 200, result.text
    before = app.service.projects.store.log_path.read_bytes()
    assert (await check(client, task)).status_code == 409
    historical = (await client.get(f'{TASKS}/{PT}/revisions/1')).json()
    assert historical['status'] == 'stale' and historical['input_binding'] == binding
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_private_input_corruption_fails_closed_without_repair(grant_app):
    app, client, source = grant_app
    _, wi, binding = await prepared(client, source)
    task = (await post(client, body(input_binding=binding))).json()
    store = app.service.projects.store
    path = store._snapshot_content(wi['files'][0]['content_digest'])
    path.write_bytes(b'synthetic corrupted private bytes')
    before = store.log_path.read_bytes()
    assert (await check(client, task)).status_code == 503
    assert path.read_bytes() == b'synthetic corrupted private bytes'
    assert store.log_path.read_bytes() == before


async def test_expiry_after_private_read_is_rechecked(grant_app, monkeypatch):
    from pyharness.application import snapshot_store
    app, client, source = grant_app
    _, _, binding = await prepared(client, source)
    task = (await post(client, body(input_binding=binding))).json()
    store = app.service.projects.store
    original = store._read_input_blob
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    def read(*args, **kwargs):
        data = original(*args, **kwargs)
        monkeypatch.setattr(snapshot_store, 'datetime', Future)
        return data
    monkeypatch.setattr(store, '_read_input_blob', read)
    before = store.log_path.read_bytes()
    assert (await check(client, task)).status_code == 403
    assert store.log_path.read_bytes() == before


@pytest.mark.controlled_process
@pytest.mark.parametrize('linked', [False, True])
async def test_git_dirty_and_linked_inputs_bind_without_source_write(git_repo, linked):
    app, client, base = git_repo
    root = base
    options = {}
    if linked:
        root = base.with_name('s04-linked')
        git(base, 'worktree', 'add', '-b', 's04-linked', str(root), 'HEAD')
        from tests.unit.test_source_grants import issue, URL
        response = await client.post(URL, json=issue(base / '.git', grant_id=META),
                                      headers={'Idempotency-Key': 'metadata'})
        assert response.status_code == 200, response.text
        options = {'metadata_grant_id': META, 'metadata_grant_revision': 1}
    dirty(root)
    await setup(client, root)
    before = {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in base.rglob('*') if p.is_file()}
    source_before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    snapshot, _ = await capture(client, selection(paths=['unchanged.txt', 'delete.txt', 'new.txt', '.ai-coding/RULES.md'], **options))
    cm_response = await client.post(MANIFESTS, json=manifest(snapshot), headers={'Idempotency-Key': 'manifest'})
    assert cm_response.status_code == 200, cm_response.text
    cm = cm_response.json()
    wi_response = await client.post(INPUTS, json=command(cm), headers={'Idempotency-Key': 'prepare'})
    assert wi_response.status_code == 200, wi_response.text
    wi = wi_response.json()
    response = await post(client, body(input_binding={'manifest_ref': ref(cm, 'manifest_id'), 'input_ref': ref(wi, 'input_id')}))
    assert response.status_code == 200, response.text
    assert (await check(client, response.json())).status_code == 200
    assert {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in base.rglob('*') if p.is_file()} == before
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()} == source_before
    assert not app.service._queues and not app.service._engines
