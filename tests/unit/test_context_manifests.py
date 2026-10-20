"""GIVEN synthetic committed sources WHEN binding context THEN persist exact bounded versions."""
import json
from pathlib import Path

import pytest

from tests.unit.test_source_grants import grant_app, PID, GID
from tests.unit.test_source_snapshot import SID
from tests.unit.test_snapshot_context_preview import captured, CONTEXT

TASKS = f'/api/v1/projects/{PID}/context-task-references'
MANIFESTS = f'/api/v1/projects/{PID}/context-manifests'
TID = 'pt-aaaaaaaaaaaaaaaa'
MID = 'cm-aaaaaaaaaaaaaaaa'


def task_command(**changes):
    return {'schema_version': 1, 'product_task_id': TID, 'expected_revision': 0,
            'project_revision': 1, 'title': 'synthetic context target', 'retired': False, **changes}


def manifest_command(snapshot, task=None, **changes):
    return {'schema_version': 1, 'manifest_id': MID, 'expected_revision': 0, 'project_revision': 1,
            'grant_id': GID, 'grant_revision': 1, 'snapshot_id': SID, 'snapshot_digest': snapshot['snapshot_digest'],
            'task_ref': ({k: task[k] for k in ('product_task_id', 'revision', 'digest', 'source_seq')} if task else None), **changes}


async def save(client, url, body, key):
    return await client.post(url, json=body, headers={'Idempotency-Key': key})


async def test_manifest_project_only_persists_and_preview_stays_readonly(grant_app):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    result = await save(client, MANIFESTS, manifest_command(snapshot), 'manifest')
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['manifest_id'] == MID and data['revision'] == 1 and data['project_revision'] == 1
    assert data['task_ref'] is None and data['binding_mode'] == 'project_only'
    assert data['status'] == 'current' and data['executable'] is False
    assert data['file_count'] == 1 and data['items'][0]['path'] == 'unchanged.txt'
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    assert (await client.get(f'{MANIFESTS}/{MID}/revisions/1')).json() == data
    assert (await save(client, MANIFESTS, manifest_command(snapshot), 'manifest')).json() == data
    assert (await client.post(CONTEXT, json=snapshot)).status_code == 200
    assert store.log_path.read_bytes() == before
    assert not app.service._engines and not app.service._queues


async def test_manifest_exact_task_reference_and_task_version_stale(grant_app):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    task = await save(client, TASKS, task_command(), 'target')
    assert task.status_code == 200, task.text
    target = task.json()
    assert target['mode'] == 'context_reference_only' and target['executable'] is False
    body = manifest_command(snapshot, target)
    result = await save(client, MANIFESTS, body, 'bound')
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'current'
    changed = await save(client, TASKS, task_command(expected_revision=1, title='new revision'), 'target-v2')
    assert changed.status_code == 200 and changed.json()['revision'] == 2
    old = await client.get(f'{MANIFESTS}/{MID}/revisions/1')
    assert old.status_code == 200 and old.json()['status'] == 'stale'
    assert old.json()['items'] == [] and 'task_revision_changed' in old.json()['stale_reasons']
    assert (await save(client, MANIFESTS, body, 'stale-create')).status_code == 409
    refreshed = await save(client, MANIFESTS, manifest_command(snapshot, changed.json(), expected_revision=1), 'refresh')
    assert refreshed.status_code == 200 and refreshed.json()['revision'] == 2
    assert refreshed.json()['status'] == 'current' and refreshed.json()['task_ref']['revision'] == 2


async def test_manifest_project_revision_stale_and_replay_not_corrupt(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, source = grant_app
    snapshot = await captured(client, source)
    result = await save(client, MANIFESTS, manifest_command(snapshot), 'manifest')
    assert result.status_code == 200
    changed = await save(client, '/api/v1/projects', {'project_id': PID, 'expected_revision': 1, 'name': 'synthetic revision'}, 'project-v2')
    assert changed.status_code == 200
    stale = await client.get(f'{MANIFESTS}/{MID}/revisions/1')
    assert stale.status_code == 200 and stale.json()['status'] == 'stale'
    assert 'project_revision_changed' in stale.json()['stale_reasons']
    store = app.service.projects.store
    await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id=store.tenant_id)
    assert (await client.get(f'{MANIFESTS}/{MID}/revisions/1')).json() == stale.json()
    assert (await save(client, MANIFESTS, manifest_command(snapshot, project_revision=2, expected_revision=1), 'refresh')).status_code == 200


@pytest.mark.parametrize('change', [dict(project_revision=2), dict(grant_revision=2), dict(snapshot_digest='b'*64)])
async def test_manifest_mismatched_versions_zero_commit(grant_app, change):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    before = app.service.projects.store.log_path.read_bytes()
    result = await save(client, MANIFESTS, manifest_command(snapshot, **change), 'mismatch')
    assert result.status_code == 409
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_context_audit_can_read_committed_snapshot_types(grant_app):
    app, client, source = grant_app
    await captured(client, source)
    response = await client.get('/api/v1/workspace/audit?pagination_version=1')
    assert response.status_code == 200, response.text
    assert response.json()['events'][-1]['resource_kind'] == 'source_snapshot'
