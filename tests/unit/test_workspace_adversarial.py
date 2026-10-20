"""Synthetic control-log adversarial integration: no model or user sessions."""
import asyncio
import subprocess
import sys
import pytest
from pyharness.application.workspace_store import WorkspaceStore, canonical, digest_of
from pyharness.application.workspace_contracts import ProjectRevision
from pyharness.errors import PyHError


def definition(name='synthetic'):
    return ProjectRevision(project_id='p-0123456789abcdef', revision=1, name=name)


async def test_project_capacity_rejects_new_resource_not_existing_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(WorkspaceStore, 'MAX_PROJECTS', 1, raising=False)
    store = WorkspaceStore(tmp_path, tenant_id='default')
    try:
        await store.commit(definition(), expected_revision=0, command_id='first', actor_ref='operator')
        other = ProjectRevision(project_id='p-abcdef0123456789', revision=1, name='other')
        with pytest.raises(PyHError, match='EVT-100'):
            await store.commit(other, expected_revision=0, command_id='other', actor_ref='operator')
        changed = ProjectRevision(project_id='p-0123456789abcdef', revision=2, name='changed')
        assert (await store.commit(changed, expected_revision=1, command_id='edit', actor_ref='operator'))['project']['revision'] == 2
        assert len(await store.projects()) == 1
    finally:
        await store.close()


async def test_concurrent_cas_and_changed_body_idempotency(tmp_path):
    store = WorkspaceStore(tmp_path, tenant_id='default')
    try:
        results = await asyncio.gather(*[
            store.commit(definition(name), expected_revision=0, command_id=name, actor_ref='operator')
            for name in ('first', 'second')], return_exceptions=True)
        assert sum(isinstance(row, dict) for row in results) == 1
        assert sum(isinstance(row, PyHError) and row.code == 'EVT-101' for row in results) == 1
        winner = next(row for row in results if isinstance(row, dict))['project']['name']
        with pytest.raises(PyHError) as conflict:
            await store.commit(definition('changed'), expected_revision=0, command_id=winner, actor_ref='operator')
        assert conflict.value.ctx['reason'] == 'idempotency_conflict'
        assert len(await store.audit()) == 2
    finally:
        await store.close()


async def test_orphan_blob_is_not_a_project_and_two_tenants_do_not_mix(tmp_path):
    for tenant in ('alpha', 'beta'):
        store = WorkspaceStore(tmp_path / tenant, tenant_id=tenant)
        try:
            raw = canonical(definition().model_dump())
            blobs = tmp_path / tenant / 'blobs'
            blobs.mkdir(parents=True)
            (blobs / (digest_of(raw) + '.json')).write_bytes(raw)
            assert await store.projects() == []
            await store.commit(definition(tenant), expected_revision=0, command_id='same-key', actor_ref='operator')
            assert (await store.projects())[0]['project']['name'] == tenant
        finally:
            await store.close()
    wrong = WorkspaceStore(tmp_path / 'alpha', tenant_id='beta')
    try:
        with pytest.raises(PyHError, match='PERS-202'):
            await wrong.projects()
    finally:
        await wrong.close()


@pytest.mark.parametrize('corruption', ['blob', 'truncated', 'invalid_line'])
async def test_corruption_fails_closed_without_repairing_bytes(tmp_path, corruption):
    store = WorkspaceStore(tmp_path, tenant_id='default')
    row = await store.commit(definition(), expected_revision=0, command_id='create', actor_ref='operator')
    await store.close()
    target = tmp_path / 'blobs' / (row['digest'] + '.json') if corruption == 'blob' else store.log_path
    bad = b'tampered' if corruption == 'blob' else target.read_bytes() + (b'{torn' if corruption == 'truncated' else b'{bad-json}\n')
    target.write_bytes(bad)
    reopened = WorkspaceStore(tmp_path, tenant_id='default')
    try:
        with pytest.raises(PyHError, match='PERS-202'):
            await reopened.projects()
        assert target.read_bytes() == bad
    finally:
        await reopened.close()


@pytest.mark.controlled_process
async def test_independent_process_writer_rejected_and_lock_released(tmp_path):
    store = WorkspaceStore(tmp_path, tenant_id='default')
    await store.projects()
    probe = tmp_path / 'probe.py'
    probe.write_text("import asyncio, sys\nfrom pathlib import Path\nfrom pyharness.application.workspace_store import WorkspaceStore\nfrom pyharness.errors import PyHError\nasync def main():\n s=WorkspaceStore(Path(sys.argv[1]), tenant_id='default')\n try:\n  await s.projects()\n  print('OPEN_OK')\n except PyHError as e:\n  print(e.code)\n finally:\n  await s.close()\nasyncio.run(main())\n", encoding='utf-8')
    try:
        first = subprocess.run([sys.executable, str(probe), str(tmp_path)], capture_output=True, text=True, timeout=10)
        assert first.returncode == 0, first.stderr
        assert first.stdout.strip() == 'PERS-202'
    finally:
        await store.close()
    second = subprocess.run([sys.executable, str(probe), str(tmp_path)], capture_output=True, text=True, timeout=10)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == 'OPEN_OK'
