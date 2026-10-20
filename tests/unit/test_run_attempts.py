"""S06 real control facts reserve attempts; drafts and queries never execute."""
import asyncio
import pytest
from tests.unit.test_source_grants import grant_app, PID
from tests.unit.test_product_tasks import prepared, post, body, ref, TASKS, PT

ATTEMPTS = f'/api/v1/projects/{PID}/run-attempts'
QUERY = f'/api/v1/projects/{PID}/run-submissions/by-key'
RA = 'ra-0123456789abcdef'


async def ready(client, source):
    _, _, binding = await prepared(client, source)
    result = await post(client, body(input_binding=binding))
    assert result.status_code == 200, result.text
    return result.json()


def submission(task, **changes):
    return {'schema_version': 1, 'attempt_id': RA, 'task_ref': ref(task, 'product_task_id'),
            'document_refs': [], 'citation_refs': [], 'delivery_ref': None, **changes}


async def submit(client, command, key='submit-one'):
    return await client.post(ATTEMPTS, json=command, headers={'Idempotency-Key': key})


async def test_attempt_commits_exact_input_once_and_queries_original_key_after_reopen(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, source = grant_app; task = await ready(client, source)
    command = submission(task); result = await submit(client, command)
    assert result.status_code == 200, result.text
    value = result.json(); store = app.service.projects.store; before = store.log_path.read_bytes()
    assert value['attempt_id'] == RA and value['status'] == 'reserved'
    assert value['selection'] == command and value['prerequisites']['input_binding'] == task['input_binding']
    assert value['attempt_reserved'] and value['persisted'] and not value['execution_authorized']
    assert value['dispatch_policy'] == 'explicit_governed_start'
    assert 'local-operator' not in result.text and str(source) not in result.text
    assert (await submit(client, command)).json() == value
    assert (await client.get(QUERY, params={'key': 'submit-one'})).json()['attempt'] == value
    assert (await client.get(ATTEMPTS)).json()['attempts'] == [value]
    assert store.log_path.read_bytes() == before and not app.service._engines and not app.service._queues
    await store.close(); app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    assert (await client.get(f'{ATTEMPTS}/{RA}')).json() == value
    assert (await client.get(QUERY, params={'key': 'submit-one'})).json()['attempt'] == value
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_concurrent_same_key_has_one_attempt_and_conflicts_never_append(grant_app):
    app, client, source = grant_app; task = await ready(client, source); command = submission(task)
    before = app.service.projects.store.log_path.read_bytes()
    results = await asyncio.gather(submit(client, command), submit(client, command))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert results[0].json() == results[1].json()
    store = app.service.projects.store; after = store.log_path.read_bytes()
    assert after.startswith(before) and len(after.splitlines()) == len(before.splitlines()) + 1
    assert (await submit(client, submission(task, attempt_id='ra-fedcba9876543210'))).status_code == 409
    assert (await submit(client, command, 'different-key')).status_code == 409
    assert store.log_path.read_bytes() == after


async def test_draft_and_stale_task_cannot_reserve_even_after_successful_preflight(grant_app):
    app, client, source = grant_app
    draft = (await post(client, body())).json(); store = app.service.projects.store
    before = store.log_path.read_bytes()
    assert (await submit(client, submission(draft))).status_code == 403
    assert store.log_path.read_bytes() == before
    _, _, binding = await prepared(client, source)
    task = (await post(client, body(expected_revision=1, input_binding=binding), 'bind')).json()
    checked = await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1, 'task_ref': ref(task, 'product_task_id')})
    assert checked.status_code == 200
    assert (await post(client, body(expected_revision=2, input_binding=binding, title='new'), 'revise')).status_code == 200
    before = store.log_path.read_bytes()
    assert (await submit(client, submission(task))).status_code == 409
    assert store.log_path.read_bytes() == before


async def test_unknown_after_durable_append_can_query_disk_without_unpoisoning_writer(grant_app, monkeypatch):
    from pyharness.errors import raise_code
    app, client, source = grant_app; task = await ready(client, source)
    store = app.service.projects.store; verify = store._verify_commit
    def lost_receipt(*args):
        verify(*args); raise_code('PERS-202', reason='synthetic_receipt_lost')
    monkeypatch.setattr(store, '_verify_commit', lost_receipt)
    assert (await submit(client, submission(task))).status_code == 503
    assert store._failed
    before = store.log_path.read_bytes()
    query = await client.get(QUERY, params={'key': 'submit-one'})
    assert query.status_code == 200, query.text
    assert query.json()['status'] == 'committed' and query.json()['attempt']['attempt_id'] == RA
    assert store._failed and store.log_path.read_bytes() == before
    assert (await submit(client, submission(task))).status_code == 503
    assert not app.service._engines and not app.service._queues


async def test_original_key_query_validates_query_and_reports_missing_without_writing(grant_app):
    app, client, _ = grant_app; store = app.service.projects.store; before = store.log_path.read_bytes()
    for key in ['never-submitted', '.', '..']:
        result = await client.get(QUERY, params={'key': key})
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'not_found' and result.json()['attempt'] is None
    for query in ['', '?key=a&key=b', '?key=a&other=1', '?key=../escape', '?key=' + 'a'*129]:
        result = await client.get(QUERY + query)
        assert result.status_code == 422, result.text
    assert store.log_path.read_bytes() == before


@pytest.mark.parametrize('change', [{'schema_version': True}, {'created_by': 'forged'}, {'tenant_id': 'victim'},
    {'status': 'completed'}, {'host_root': 'C:/private'}, {'execution_authorized': True}, {'attempt_id': '../escape'}])
async def test_forged_attempt_fields_have_zero_commit(grant_app, change):
    app, client, source = grant_app; task = await ready(client, source); store = app.service.projects.store
    before = store.log_path.read_bytes()
    assert (await submit(client, submission(task, **change))).status_code == 422
    assert store.log_path.read_bytes() == before and not app.service._engines and not app.service._queues
