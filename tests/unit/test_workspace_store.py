"""GIVEN a real control log WHEN a project commits THEN disk is the only authority."""
import json
import pytest
from pyharness.application.workspace_store import WorkspaceStore
from pyharness.application.workspace_contracts import ProjectRevision
from pyharness.errors import PyHError


def definition(revision=1, name='synthetic'):
    return ProjectRevision(project_id='p-0123456789abcdef', revision=revision, name=name)


async def test_project_commit_rebuild_and_idempotency(tmp_path):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.workspace_contracts import ProjectRevision
    root = tmp_path / 'workspace'
    store = WorkspaceStore(root, tenant_id='default')
    project = ProjectRevision(project_id='p-0123456789abcdef', revision=1, name='synthetic')
    first = await store.commit(project, expected_revision=0, command_id='create-1', actor_ref='operator')
    again = await store.commit(project, expected_revision=0, command_id='create-1', actor_ref='operator')
    assert again == first
    assert first['project']['name'] == 'synthetic'
    assert first['source_seq'] == 2
    path = store.log_path
    before = path.read_bytes()
    assert [json.loads(line)['type'] for line in before.splitlines()] == [
        'session.created', 'workspace.resource_committed']
    await store.close()
    assert path.read_bytes() == before
    restored = WorkspaceStore(root, tenant_id='default')
    try:
        assert (await restored.projects()) == [first]
        assert await restored.commit(project, expected_revision=0, command_id='create-1', actor_ref='operator') == first
        assert path.read_bytes() == before
    finally:
        await restored.close()


async def test_swallowed_recorder_failure_cannot_report_commit(tmp_path, monkeypatch):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.workspace_contracts import ProjectRevision
    from pyharness.errors import PyHError
    store = WorkspaceStore(tmp_path, tenant_id='default')
    await store.projects()
    async def fail_record(*args, **kwargs):
        raise OSError('synthetic disk failure before enqueue')
    monkeypatch.setattr(store._store, 'append', fail_record)
    try:
        with pytest.raises(PyHError, match='PERS-202'):
            await store.commit(ProjectRevision(project_id='p-0123456789abcdef', revision=1, name='synthetic'),
                               expected_revision=0, command_id='create', actor_ref='operator')
        with pytest.raises(PyHError, match='PERS-202'):
            await store.projects()
    finally:
        await store.close()


async def test_blob_io_error_is_coded_without_committing(tmp_path, monkeypatch):
    from pyharness.core.tenant_settings import TenantSettingsStore
    store = WorkspaceStore(tmp_path, tenant_id='default')
    await store.projects()
    def failed_write(*args):
        raise OSError('synthetic disk full')
    monkeypatch.setattr(TenantSettingsStore, '_atomic_write', failed_write)
    try:
        with pytest.raises(PyHError, match='PERS-202'):
            await store.commit(definition(), expected_revision=0, command_id='x', actor_ref='operator')
        assert len(await store.audit()) == 1
    finally:
        await store.close()
