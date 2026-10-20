"""Version, permission and unknown-commit boundaries around real attempt facts."""
import asyncio
from datetime import datetime, timezone
import pytest
from tests.unit.test_source_grants import grant_app, PID, GID
from tests.unit.test_run_attempts import ready, submission, submit, ATTEMPTS, QUERY, RA
from tests.unit.test_documents import document, DOCS, DELIVERIES
from tests.unit.test_document_boundaries import citation
from tests.unit.test_product_tasks import body, post, ref, TASKS, PT
from tests.unit.test_context_manifest_permissions import context_team, join
from tests.unit.test_document_adversarial import member_document
from tests.unit.test_workspace_owned_inputs import INPUTS


async def with_context(client, source):
    doc, edit = await document(client, source); cite, _ = await citation(client, doc)
    wi = (await client.get(INPUTS + '/' + doc['source']['input_ref']['input_id'])).json()
    binding = {'manifest_ref': wi['manifest_ref'], 'input_ref': ref(wi, 'input_id')}
    task = (await post(client, body(input_binding=binding))).json()
    delivery = {'schema_version': 1, 'delivery_id': 'del-0123456789abcdef', 'expected_revision': 0,
        'project_revision': 1, 'task_ref': ref(task, 'product_task_id'), 'retired': False,
        'items': [{'item_id': 'report', 'title': 'Report', 'required': True,
                   'acceptance_criteria': ['Cite the exact input'], 'citation_refs': [ref(cite, 'citation_id')]}]}
    result = await client.post(DELIVERIES, json=delivery, headers={'Idempotency-Key': 'delivery'})
    assert result.status_code == 200, result.text
    return task, doc, cite, result.json(), edit, delivery


async def test_delivery_expands_exact_citations_and_documents_and_old_attempt_never_rebases(grant_app):
    app, client, source = grant_app
    task, doc, cite, delivery, edit, _ = await with_context(client, source)
    command = submission(task, delivery_ref=ref(delivery, 'delivery_id'))
    result = await submit(client, command); assert result.status_code == 200, result.text
    value = result.json()
    assert value['context']['document_refs'] == [ref(doc, 'document_id')]
    assert value['context']['citation_refs'] == [ref(cite, 'citation_id')]
    assert value['context']['delivery_ref'] == ref(delivery, 'delivery_id')
    assert (await client.post(DOCS, json={**edit, 'expected_revision': 1, 'title': 'new doc'}, headers={'Idempotency-Key': 'new-doc'})).status_code == 200
    before = app.service.projects.store.log_path.read_bytes()
    old = (await client.get(f'{ATTEMPTS}/{RA}')).json()
    assert old['context'] == value['context'] and old['input_readiness'] == 'stale'
    assert old['binding_digest'] == value['binding_digest'] and not old['execution_started']
    assert (await submit(client, submission(task, attempt_id='ra-1111111111111111', delivery_ref=ref(delivery, 'delivery_id')), 'stale-context')).status_code == 409
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('change', ['project', 'manifest', 'grant', 'bytes'])
async def test_preflight_does_not_authorize_submission_after_changes(grant_app, change):
    from tests.unit.test_context_manifests import MANIFESTS, manifest_command
    from tests.unit.test_product_tasks import prepared
    app, client, source = grant_app; cm, wi, binding = await prepared(client, source)
    task = (await post(client, body(input_binding=binding))).json()
    assert (await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1, 'task_ref': ref(task, 'product_task_id')})).status_code == 200
    if change == 'project':
        result = await client.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 1, 'name': 'new'}, headers={'Idempotency-Key': 'change'})
    elif change == 'manifest':
        result = await client.post(MANIFESTS, json=manifest_command({'snapshot_digest': cm['snapshot_digest']}, expected_revision=1), headers={'Idempotency-Key': 'change'})
    elif change == 'grant':
        result = await client.post(f'/api/v1/source-grants/{GID}/revoke', json={'schema_version': 1, 'expected_revision': 1}, headers={'Idempotency-Key': 'change'})
    else:
        path = app.service.projects.store._snapshot_content(wi['files'][0]['content_digest'])
        path.write_bytes(b'corrupted synthetic input'); result = None
    if result is not None:assert result.status_code == 200, result.text
    store = app.service.projects.store; before = store.log_path.read_bytes()
    response = await submit(client, submission(task))
    assert response.status_code in (403, 409, 503), response.text
    assert store.log_path.read_bytes() == before
    if change == 'bytes':assert path.read_bytes() == b'corrupted synthetic input'


@pytest.mark.parametrize('damage', ['truncated', 'corrupt_blob'])
async def test_unknown_query_never_turns_corrupt_or_truncated_disk_into_missing(grant_app, monkeypatch, damage):
    from pyharness.errors import raise_code
    app, client, source = grant_app; task = await ready(client, source); store = app.service.projects.store
    original = store._verify_commit
    def lost(*args):original(*args); raise_code('PERS-202', reason='synthetic_lost')
    monkeypatch.setattr(store, '_verify_commit', lost)
    assert (await submit(client, submission(task))).status_code == 503
    if damage == 'truncated':
        path = store.log_path; path.write_bytes(path.read_bytes() + b'{"unfinished":')
    else:
        import json
        payload = json.loads(store.log_path.read_bytes().splitlines()[-1])['payload']
        path = store._blob(payload['digest']); path.write_bytes(b'corrupted immutable definition')
    before = path.read_bytes(); log = store.log_path.read_bytes()
    result = await client.get(QUERY, params={'key': 'submit-one'})
    assert result.status_code == 503, result.text
    assert path.read_bytes() == before and store.log_path.read_bytes() == log and store._failed


async def member_ready(context_team):
    app, member, accounts, store, doc = await member_document(context_team)
    wi = (await member.get(INPUTS + '/' + doc['source']['input_ref']['input_id'])).json()
    task = await post(member, body(input_binding={'manifest_ref': wi['manifest_ref'], 'input_ref': ref(wi, 'input_id')}))
    assert task.status_code == 200, task.text
    return app, member, accounts, store, task.json()


async def test_member_attempt_privacy_against_team_owner_and_other_tenant(context_team):
    app, owner, member, accounts, _ = context_team
    _, member, _, store, task = await member_ready(context_team)
    assert (await submit(member, submission(task))).status_code == 200
    assert (await owner.get(f'{ATTEMPTS}/{RA}')).status_code == 403
    assert (await owner.get(ATTEMPTS)).json()['total'] == 0
    assert (await owner.get(QUERY, params={'key': 'submit-one'})).json()['status'] == 'not_found'
    accounts.membership('context-team', 'ctx-member', 'remove')
    assert (await member.post('/api/account/login', json={'username': 'ctx-member', 'password': 'synthetic-only-password', 'tenant': 'other-team'})).status_code == 200
    before = store.log_path.read_bytes()
    assert (await member.get(f'{ATTEMPTS}/{RA}')).status_code == 403
    assert store.log_path.read_bytes() == before


@pytest.mark.parametrize('role', ['viewer', 'remove'])
async def test_role_loss_rejects_submit_list_detail_and_original_query(context_team, role):
    _, member, accounts, store, task = await member_ready(context_team)
    assert (await submit(member, submission(task))).status_code == 200
    accounts.membership('context-team', 'ctx-member', role); before = store.log_path.read_bytes()
    responses = [await submit(member, submission(task)), await member.get(ATTEMPTS),
                 await member.get(f'{ATTEMPTS}/{RA}'), await member.get(QUERY, params={'key': 'submit-one'})]
    assert all(r.status_code in (401, 403) for r in responses)
    assert store.log_path.read_bytes() == before


async def test_lock_wait_rechecks_membership(context_team):
    _, member, accounts, store, task = await member_ready(context_team)
    before = store.log_path.read_bytes(); await store._lock.acquire()
    try:
        waiting = asyncio.create_task(submit(member, submission(task)))
        for _ in range(60):
            if store._lock._waiters:break
            await asyncio.sleep(.01)
        assert store._lock._waiters
        accounts.membership('context-team', 'ctx-member', 'viewer')
    finally:store._lock.release()
    assert (await waiting).status_code == 403 and store.log_path.read_bytes() == before


async def test_private_io_tail_rechecks_membership(context_team, monkeypatch):
    _, member, accounts, store, task = await member_ready(context_team)
    before = store.log_path.read_bytes(); original = store._read_input_blob
    def read(*args, **kwargs):
        value = original(*args, **kwargs); accounts.membership('context-team', 'ctx-member', 'viewer'); return value
    monkeypatch.setattr(store, '_read_input_blob', read)
    assert (await submit(member, submission(task))).status_code == 403
    assert store.log_path.read_bytes() == before


async def test_input_io_tail_utc_expiry_is_not_a_submission(grant_app, monkeypatch):
    from pyharness.application import snapshot_store
    app, client, source = grant_app; task = await ready(client, source); store = app.service.projects.store
    original = store._read_input_blob
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):return datetime(2100, 1, 1, tzinfo=timezone.utc)
    def read(*args, **kwargs):
        value = original(*args, **kwargs); monkeypatch.setattr(snapshot_store, 'datetime', Future); return value
    monkeypatch.setattr(store, '_read_input_blob', read); before = store.log_path.read_bytes()
    assert (await submit(client, submission(task))).status_code == 403
    assert store.log_path.read_bytes() == before


async def test_attempt_pages_are_bounded_scope_bound_and_restart_stable(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, source = grant_app; task = await ready(client, source)
    for i in range(3):
        result = await submit(client, submission(task, attempt_id=f'ra-{i:016x}'), 'page-' + str(i))
        assert result.status_code == 200, result.text
    first = (await client.get(ATTEMPTS + '?limit=1')).json(); assert first['total'] == 3
    store = app.service.projects.store; before = store.log_path.read_bytes(); await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    second = await client.get(ATTEMPTS, params={'limit': '1', 'cursor': first['next_cursor']})
    assert second.status_code == 200 and second.json()['attempts'][0]['attempt_id'] != first['attempts'][0]['attempt_id']
    assert (await client.get(ATTEMPTS, params={'limit': '2', 'cursor': first['next_cursor']})).status_code == 422
    assert (await client.get(ATTEMPTS + '?limit=51')).status_code == 422
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_unpersisted_append_cache_is_not_a_receipt_and_missing_query_does_not_repair(grant_app, monkeypatch):
    app, client, source = grant_app; task = await ready(client, source); store = app.service.projects.store
    original = store._store.append
    async def drop(event, **kwargs):
        if event.type == 'workspace.resource_committed' and event.payload['resource_kind'] == 'run_attempt':return
        return await original(event, **kwargs)
    monkeypatch.setattr(store._store, 'append', drop); before = store.log_path.read_bytes()
    assert (await submit(client, submission(task))).status_code == 503
    assert store._failed and store.log_path.read_bytes() == before
    result = await client.get(QUERY, params={'key': 'submit-one'})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'not_found' and result.json()['attempt'] is None
    assert result.json()['writer_requires_reopen'] and store._failed
    assert (await client.get(ATTEMPTS)).json()['total'] == 0
    assert store.log_path.read_bytes() == before


async def test_attempt_submission_and_queries_are_denied_after_lifetime_closes(grant_app):
    app, client, source = grant_app; task = await ready(client, source)
    await app.service.projects.close()
    before = app.service.projects.store.log_path.read_bytes()
    for result in [await submit(client, submission(task)), await client.get(QUERY, params={'key': 'submit-one'}), await client.get(ATTEMPTS)]:
        assert result.status_code == 409, result.text
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_context_reference_only_is_not_an_executable_product_task(grant_app):
    from tests.unit.test_context_manifests import TASKS as REFERENCES, task_command
    app, client, _ = grant_app
    result = await client.post(REFERENCES, json=task_command(), headers={'Idempotency-Key': 'reference-only'})
    assert result.status_code == 200, result.text
    store = app.service.projects.store; before = store.log_path.read_bytes()
    assert (await submit(client, submission(result.json()))).status_code == 403
    assert store.log_path.read_bytes() == before and not app.service._engines and not app.service._queues


async def test_same_actor_cross_project_attempt_and_key_do_not_transfer_authority(grant_app):
    app, client, source = grant_app; task = await ready(client, source)
    assert (await submit(client, submission(task))).status_code == 200
    other = 'p-fedcba9876543210'
    assert (await client.post('/api/v1/projects', json={'project_id': other, 'expected_revision': 0, 'name': 'Other project'}, headers={'Idempotency-Key': 'other-project'})).status_code == 200
    root = f'/api/v1/projects/{other}'; store = app.service.projects.store; before = store.log_path.read_bytes()
    assert (await client.get(root + '/run-attempts/' + RA)).status_code == 403
    assert (await client.get(root + '/run-submissions/by-key', params={'key': 'submit-one'})).status_code == 403
    assert (await client.post(root + '/run-attempts', json=submission(task, attempt_id='ra-9999999999999999'), headers={'Idempotency-Key': 'other-submit'})).status_code == 403
    assert (await client.get(root + '/run-attempts')).json()['total'] == 0
    assert store.log_path.read_bytes() == before


async def test_submission_does_not_read_live_source_bodies(grant_app, monkeypatch):
    from pyharness.application import source_snapshot
    app, client, source = grant_app; task = await ready(client, source)
    monkeypatch.setattr(source_snapshot, 'read_selected', lambda *a, **k: pytest.fail('submission opened a live source body'))
    result = await submit(client, submission(task)); assert result.status_code == 200, result.text
    assert (await client.get(QUERY, params={'key':'submit-one'})).json()['status'] == 'committed'
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('change', ['missing_version', 'duplicate_reference', 'too_many_references'])
async def test_invalid_selection_shape_does_not_create_a_fact(grant_app, change):
    app, client, source = grant_app; task = await ready(client, source); command = submission(task)
    if change == 'missing_version':command.pop('schema_version')
    else:
        value = {'document_id':'doc-0000000000000000','revision':1,'digest':'0'*64,'source_seq':1}
        command['document_refs'] = [value, value] if change == 'duplicate_reference' else [{**value,'document_id':f'doc-{i:016x}'} for i in range(21)]
    before = app.service.projects.store.log_path.read_bytes()
    assert (await submit(client, command)).status_code == 422
    assert app.service.projects.store.log_path.read_bytes() == before
