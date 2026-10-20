"""Given immutable commits, reopening/rebuilding uses only the control SessionLog."""
import json
import subprocess
import sys

import pytest
from tests.conftest import isolated_process_environment
from tests.unit.test_context_manifests import grant_app, captured, save, manifest_command, task_command, TASKS, MANIFESTS, MID, PID


async def test_old_task_receipt_reports_superseded_revision(grant_app):
    _, client, source = grant_app
    await captured(client, source)
    original = task_command()
    assert (await save(client, TASKS, original, 'r1')).status_code == 200
    assert (await save(client, TASKS, task_command(expected_revision=1, title='version two'), 'r2')).status_code == 200
    receipt = await save(client, TASKS, original, 'r1')
    assert receipt.status_code == 200 and receipt.json()['revision'] == 1
    assert receipt.json()['status'] == 'stale' and receipt.json()['executable'] is False


async def test_task_exact_history_readback_and_retirement_receipt(grant_app):
    _, client, source = grant_app
    await captured(client, source)
    first = (await save(client, TASKS, task_command(), 'r1')).json()
    assert (await save(client, TASKS, task_command(expected_revision=1, title='version two'), 'r2')).status_code == 200
    response = await client.get(f'{TASKS}/{first["product_task_id"]}/revisions/1')
    assert response.status_code == 200 and response.json()['digest'] == first['digest']
    assert response.json()['status'] == 'stale'
    retired = (await save(client, TASKS, task_command(expected_revision=2, retired=True), 'retire')).json()
    assert (await client.get(f'{TASKS}/{first["product_task_id"]}/revisions/1')).status_code == 403
    assert (await client.get(f'{TASKS}/{first["product_task_id"]}/revisions/3')).json() == retired


@pytest.mark.controlled_process
async def test_independent_owner_reject_then_reopen_and_rebuild_exact(grant_app):
    app, client, source = grant_app
    snapshot = await captured(client, source)
    target = (await save(client, TASKS, task_command(), 'target')).json()
    row = (await save(client, MANIFESTS, manifest_command(snapshot, target), 'manifest')).json()
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    probe = """
import asyncio,json,sys
from pathlib import Path
from pyharness.application.workspace_store import WorkspaceStore
from pyharness.errors import PyHError
async def run():
 s=WorkspaceStore(Path(sys.argv[1]),tenant_id='default')
 try:
  row=await s.context_manifest(sys.argv[2],sys.argv[3],1,authorize=lambda:'local-operator')
  rebuilt=s._project(list(s._store.replay()))
  assert rebuilt[0][sys.argv[3]]['digest']==row['digest']
  print(json.dumps({'row':row,'rebuilt':True}))
 except PyHError as e: print(json.dumps({'error':e.code}));sys.exit(3)
 finally: await s.close()
asyncio.run(run())
"""
    argv = [sys.executable, '-B', '-c', probe, str(store.root), PID, MID]
    blocked = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    assert blocked.returncode == 3 and json.loads(blocked.stdout)['error'] == 'PERS-202'
    await store.close()
    restored = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    assert restored.returncode == 0, restored.stderr
    assert json.loads(restored.stdout) == {'row': row, 'rebuilt': True}
    assert store.log_path.read_bytes() == before


@pytest.mark.parametrize('fault', ['before-append', 'after-disk-commit'])
async def test_oserror_is_storage_failure_and_unknown_recovery_is_exact(grant_app, monkeypatch, fault):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, source = grant_app
    snapshot = await captured(client, source)
    store = app.service.projects.store
    before = store.log_path.read_bytes()
    if fault == 'before-append':
        async def append(*_, **__): raise OSError('synthetic full disk')
        monkeypatch.setattr(store._session, 'append', append)
    else:
        def verify(*_): raise OSError('synthetic receipt disk error')
        monkeypatch.setattr(store, '_verify_commit', verify)
    body = manifest_command(snapshot)
    assert (await save(client, MANIFESTS, body, 'disk-fault')).status_code == 503
    assert store._failed
    if fault == 'before-append': assert store.log_path.read_bytes() == before
    else: assert len(store.log_path.read_bytes().splitlines()) == len(before.splitlines()) + 1
    assert (await save(client, MANIFESTS, body, 'disk-fault')).status_code == 503
    await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id=store.tenant_id)
    if fault == 'before-append': assert (await client.get(MANIFESTS)).json() == {'manifests': [], 'total': 0}
    assert (await save(client, MANIFESTS, body, 'disk-fault')).status_code == 200
    assert len(store.log_path.read_bytes().splitlines()) == len(before.splitlines()) + 1
    assert not app.service._engines and not app.service._queues
