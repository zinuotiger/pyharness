"""S04 formal tasks bind exact prepared inputs without submitting an execution."""
import json
import pytest

from tests.unit.test_source_grants import grant_app, PID, GID
from tests.unit.test_selected_owned_inputs import selected_manifest
from tests.unit.test_workspace_owned_inputs import INPUTS, WI, command

PT = 'pt-fedcba9876543210'
TASKS = f'/api/v1/projects/{PID}/product-tasks'


def body(**changes):
    return {'schema_version': 2, 'product_task_id': PT, 'expected_revision': 0,
            'project_revision': 1, 'title': 'Synthetic task',
            'objective': 'Review the exact synthetic input.',
            'acceptance_criteria': ['Report the observed content.'],
            'input_binding': None, 'retired': False, **changes}


async def post(client, value, key='task-create'):
    return await client.post(TASKS, json=value, headers={'Idempotency-Key': key})


def ref(value, identity):
    return {identity: value[identity], **{k: value[k] for k in ('revision', 'digest', 'source_seq')}}


async def prepared(client, source):
    cm = await selected_manifest(client, source)
    response = await client.post(INPUTS, json=command(cm, schema_version=2),
                                 headers={'Idempotency-Key': 'prepare'})
    assert response.status_code == 200, response.text
    wi = response.json()
    return cm, wi, {'manifest_ref': ref(cm, 'manifest_id'), 'input_ref': ref(wi, 'input_id')}


async def test_formal_draft_persists_reopens_and_has_no_execution(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, _ = grant_app
    response = await post(client, body())
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['mode'] == 'product_task' and value['status'] == 'draft'
    assert value['objective'] == body()['objective']
    assert value['executable'] is False and value['attempt_reserved'] is False
    assert (await post(client, body())).json() == value
    assert (await client.get(TASKS)).json() == {'tasks': [value], 'total': 1}
    store = app.service.projects.store
    log = store.log_path.read_bytes()
    await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    assert (await client.get(f'{TASKS}/{PT}/revisions/1')).json() == value
    assert app.service.projects.store.log_path.read_bytes() == log
    assert not app.service._engines and not app.service._queues


async def test_exact_selected_input_binding_and_readonly_prerequisites(grant_app):
    app, client, source = grant_app
    cm, wi, binding = await prepared(client, source)
    response = await post(client, body(input_binding=binding))
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['status'] == 'definition_ready' and value['input_binding'] == binding
    store = app.service.projects.store
    log = store.log_path.read_bytes()
    result = await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1,
                               'task_ref': ref(value, 'product_task_id')})
    assert result.status_code == 200, result.text
    observed = result.json()
    assert observed['status'] == 'prerequisites_checked'
    assert observed['prerequisites']['task_ref'] == ref(value, 'product_task_id')
    assert observed['prerequisites']['input_binding'] == binding
    assert observed['execution_authorized'] is False and observed['attempt_reserved'] is False
    assert observed['prerequisites']['project_revision'] == 1
    assert len(observed['binding_digest']) == 64
    assert str(source) not in result.text and 'local-operator' not in result.text
    assert store.log_path.read_bytes() == log
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('change', [
    {'schema_version': True}, {'schema_version': 1}, {'tenant_id': 'victim'},
    {'created_by': 'forged'}, {'mode': 'context_reference_only'},
    {'expected_revision': True}, {'project_revision': True}, {'objective': '   '},
    {'acceptance_criteria': []}, {'acceptance_criteria': ['   ']},
    {'title': '   '}, {'host_root': 'C:/private'}, {'objective': 'x' * 8001}])
async def test_forged_formal_commands_have_no_commit(grant_app, change):
    app, client, _ = grant_app
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    response = await post(client, body(**change))
    assert response.status_code == 422, response.text
    assert store.log_path.read_bytes() == before


async def test_cas_idempotency_retirement_and_exact_history(grant_app):
    _, client, _ = grant_app
    first = (await post(client, body())).json()
    assert (await post(client, body(title='different'))).status_code == 409
    changed = body(expected_revision=1, title='Revision two')
    second = await post(client, changed, 'task-revise')
    assert second.status_code == 200, second.text
    assert (await post(client, body(), 'stale')).status_code == 409
    historic = (await client.get(f'{TASKS}/{PT}/revisions/1')).json()
    assert historic['title'] == first['title'] and historic['status'] == 'stale'
    retired = await post(client, body(expected_revision=2, retired=True), 'retire')
    assert retired.status_code == 200, retired.text
    assert retired.json()['status'] == 'retired'
    assert (await post(client, body(expected_revision=3), 'revive')).status_code == 403
    assert (await post(client, body(expected_revision=2, retired=True), 'retire')).json() == retired.json()


async def test_legacy_reference_cannot_be_promoted_or_preflighted(grant_app):
    _, client, _ = grant_app
    legacy = await client.post(f'/api/v1/projects/{PID}/context-task-references',
        json={'product_task_id': PT, 'expected_revision': 0, 'project_revision': 1,
              'title': 'Legacy only'}, headers={'Idempotency-Key': 'legacy'})
    assert legacy.status_code == 200, legacy.text
    assert (await post(client, body(expected_revision=1), 'promote')).status_code == 403
    response = await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1,
                                 'task_ref': ref(legacy.json(), 'product_task_id')})
    assert response.status_code == 403
    assert (await client.get(TASKS)).json()['total'] == 0
    assert (await client.get(f'/api/v1/projects/{PID}/context-task-references')).json()['total'] == 1


@pytest.mark.parametrize('field', ['digest', 'source_seq', 'revision'])
async def test_input_reference_must_be_exact(grant_app, field):
    app, client, source = grant_app
    _, _, binding = await prepared(client, source)
    binding['input_ref'][field] = '0' * 64 if field == 'digest' else 99
    before = app.service.projects.store.log_path.read_bytes()
    assert (await post(client, body(input_binding=binding))).status_code == (422 if field == 'revision' else 409)
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_grant_revocation_denies_task_and_prerequisites(grant_app):
    app, client, source = grant_app
    _, _, binding = await prepared(client, source)
    task = (await post(client, body(input_binding=binding))).json()
    assert (await client.post(f'/api/v1/source-grants/{GID}/revoke', json={'expected_revision': 1},
                             headers={'Idempotency-Key': 'withdraw'})).status_code == 200
    before = app.service.projects.store.log_path.read_bytes()
    response = await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1,
                                 'task_ref': ref(task, 'product_task_id')})
    assert response.status_code == 403
    assert (await client.get(f'{TASKS}/{PT}/revisions/1')).status_code == 403
    assert (await client.get(TASKS)).json()['total'] == 0
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_corrupt_control_log_is_preserved_and_not_reported_ready(grant_app):
    app, client, _ = grant_app
    assert (await post(client, body())).status_code == 200
    path = app.service.projects.store.log_path
    damaged = path.read_bytes() + b'{synthetic-broken\n'
    path.write_bytes(damaged)
    assert (await client.get(TASKS)).status_code == 503
    assert path.read_bytes() == damaged


async def test_input_from_another_project_is_not_a_task_binding(grant_app):
    app, client, source = grant_app
    _, _, binding = await prepared(client, source)
    other = 'p-2222222222222222'
    assert (await client.post('/api/v1/projects', json={'project_id': other, 'expected_revision': 0,
        'name': 'Other synthetic project'}, headers={'Idempotency-Key': 'other-project'})).status_code == 200
    before = app.service.projects.store.log_path.read_bytes()
    result = await client.post(f'/api/v1/projects/{other}/product-tasks', json=body(input_binding=binding),
                               headers={'Idempotency-Key': 'cross-project'})
    assert result.status_code == 403
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('extra', [{'schema_version': True}, {'tenant_id': 'forged'},
    {'actor_ref': 'forged'}, {'attempt_id': 'forged'}, {'execute': True}, {'binding_digest': 'a' * 64}])
async def test_preflight_rejects_forged_attempt_authority(grant_app, extra):
    app, client, _ = grant_app
    task = (await post(client, body())).json()
    before = app.service.projects.store.log_path.read_bytes()
    result = await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1,
        'task_ref': ref(task, 'product_task_id'), **extra})
    assert result.status_code == 422, result.text
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_preflight_reads_only_committed_private_bodies(grant_app, monkeypatch):
    from pathlib import Path
    app, client, source = grant_app
    _, _, binding = await prepared(client, source)
    task = (await post(client, body(input_binding=binding))).json()
    (source / 'unchanged.txt').write_bytes(b'later synthetic host edit')
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(source.resolve()), 'preflight reopened a live source body'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    result = await client.post(f'{TASKS}/{PT}/preflight', json={'schema_version': 1,
                               'task_ref': ref(task, 'product_task_id')})
    assert result.status_code == 200, result.text
    assert not app.service._engines and not app.service._queues
