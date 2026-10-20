"""Actual synthetic account chain and final I/O boundaries for read-only Git previews."""
from datetime import datetime,timezone
from pathlib import Path
import pytest

from tests.unit.test_context_manifest_permissions import context_team,join
from tests.unit.test_source_grants import grant_app, PID, GID
from tests.unit.test_workspace_git_preview import git,git_repo,issued,body,PREVIEW


@pytest.mark.controlled_process
@pytest.mark.parametrize('role',['member','viewer','remove'])
async def test_git_role_matrix_and_final_revocation(context_team, role):
    app,owner,member,accounts,_=context_team
    store=app._service_registry.get('context-team').projects.store
    root=Path(store._grant_state()[0][GID]['grant']['canonical_root'])
    git(root,'init','--initial-branch=fixture');git(root,'add','--','unchanged.txt');git(root,'commit','-m','synthetic fixture seed')
    await join(member,accounts)
    if role!='member': accounts.membership('context-team','ctx-member',role)
    before=store.log_path.read_bytes()
    response=await member.post(PREVIEW,json=body(paths=['unchanged.txt']))
    assert response.status_code==200 if role=='member' else response.status_code in (401,403)
    assert store.log_path.read_bytes()==before
    assert not app._service_registry.get('context-team')._engines


@pytest.mark.controlled_process
async def test_membership_removed_during_git_io_denies_response(context_team, monkeypatch):
    from pyharness.application import workspace_git
    app,_,member,accounts,_=context_team
    store=app._service_registry.get('context-team').projects.store
    root=Path(store._grant_state()[0][GID]['grant']['canonical_root'])
    git(root,'init','--initial-branch=fixture');git(root,'add','--','unchanged.txt');git(root,'commit','-m','synthetic fixture seed')
    await join(member,accounts)
    original=workspace_git._git
    async def read(*args,**kwargs):
        result=await original(*args,**kwargs)
        accounts.membership('context-team','ctx-member','remove')
        return result
    monkeypatch.setattr(workspace_git,'_git',read)
    before=store.log_path.read_bytes()
    assert (await member.post(PREVIEW,json=body(paths=['unchanged.txt']))).status_code==403
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_expiry_during_git_io_denies_zero_commit(git_repo, monkeypatch):
    from pyharness.application import workspace_git,snapshot_store
    app,client,root=git_repo
    await issued(client,root)
    class Future(datetime):
        @classmethod
        def now(cls,tz=None): return datetime(2100,1,1,tzinfo=timezone.utc)
    original=workspace_git._git
    async def read(*args,**kwargs):
        result=await original(*args,**kwargs)
        monkeypatch.setattr(snapshot_store,'datetime',Future)
        return result
    monkeypatch.setattr(workspace_git,'_git',read)
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(PREVIEW,json=body(paths=['unchanged.txt']))).status_code==403
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('condition',['revoked','archived','expired','stale'])
async def test_unavailable_authority_never_reaches_git(git_repo, monkeypatch, condition):
    from pyharness.application import workspace_git,snapshot_store
    app,client,root=git_repo
    await issued(client,root)
    if condition=='revoked':
        assert (await client.post(f'/api/v1/source-grants/{GID}/revoke',json={'expected_revision':1},headers={'Idempotency-Key':'revoke'})).status_code==200
    elif condition=='archived':
        assert (await client.post('/api/v1/projects',json={'project_id':PID,'expected_revision':1,'name':'synthetic','archived':True},headers={'Idempotency-Key':'archive'})).status_code==200
    elif condition=='expired':
        class Future(datetime):
            @classmethod
            def now(cls,tz=None): return datetime(2100,1,1,tzinfo=timezone.utc)
        monkeypatch.setattr(snapshot_store,'datetime',Future)
    async def forbidden(*_,**__): raise AssertionError('invalid authority reached git')
    monkeypatch.setattr(workspace_git,'_git',forbidden)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt'],expected_revision=2 if condition=='stale' else 1))
    assert response.status_code in (403,409)
    assert app.service.projects.store.log_path.read_bytes()==before
