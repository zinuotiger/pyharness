"""Given explicit capture authority, then bad input or final source drift commits nothing."""
from datetime import datetime,timezone
import os
import pytest
from tests.unit.test_source_grants import grant_app,GID
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_snapshots import selection,setup,capture,manifest,PREVIEW,CAPTURE,MANIFESTS,P,WS,CM


@pytest.mark.controlled_process
@pytest.mark.parametrize('change',[{'schema_version':True},{'project_revision':True},{'expected_revision':True},
    {'paths':['../escape']},{'paths':[]},{'paths':['unchanged.txt','unchanged.txt']},{'paths':['.git/config']},
    {'metadata_grant_revision':1},{'unexpected':'reject'}])
async def test_invalid_selection_denied_without_persisting(git_repo,change):
    app,client,root=git_repo;await setup(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(PREVIEW,json=selection(**change))).status_code in (403,422)
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('change',['edit','same-size-mtime','delete','index','head'])
async def test_source_change_after_private_writes_rejected_without_commit(git_repo,monkeypatch,change):
    from pyharness.application.snapshot_store import SnapshotStore
    from tests.unit.test_workspace_git_preview import git
    app,client,root=git_repo;await setup(client,root)
    preview=(await client.post(PREVIEW,json=selection())).json()
    original=SnapshotStore._write_snapshot_blob;changed=False
    def write(path,raw):
        nonlocal changed
        original(path,raw)
        if changed:return
        changed=True
        path=root/'unchanged.txt'
        if change=='delete':path.unlink()
        elif change=='same-size-mtime':
            info=path.stat();path.write_bytes(b'evil\r\n');os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
        elif change=='edit':path.write_bytes(b'changed after private writes')
        elif change=='index':
            path.write_bytes(b'new staged');git(root,'add','--','unchanged.txt')
        else:git(root,'update-ref','refs/heads/fixture','0'*40)
    monkeypatch.setattr(SnapshotStore,'_write_snapshot_blob',staticmethod(write))
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(CAPTURE,json={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'drift'})
    assert response.status_code in (403,409)
    assert changed and app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('phase',['source-read','root-metadata'])
async def test_final_io_crossing_expiry_denies_capture_without_commit(git_repo,monkeypatch,phase):
    from pyharness.application import workspace_git,snapshot_store
    app,client,root=git_repo;await setup(client,root)
    preview=(await client.post(PREVIEW,json=selection())).json()
    store=app.service.projects.store;written=False;crossed=False
    class Future(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2100,1,1,tzinfo=timezone.utc)
    original_write=store._write_snapshot_blob
    def write(*args):
        nonlocal written
        result=original_write(*args);written=True;return result
    target=workspace_git if phase=='source-read' else snapshot_store
    name='read_selected' if phase=='source-read' else 'check_root_metadata'
    original=getattr(target,name)
    def read(*args,**kwargs):
        nonlocal crossed
        result=original(*args,**kwargs)
        if written:
            crossed=True;monkeypatch.setattr(snapshot_store,'datetime',Future)
        return result
    monkeypatch.setattr(store,'_write_snapshot_blob',write);monkeypatch.setattr(target,name,read)
    before=store.log_path.read_bytes()
    response=await client.post(CAPTURE,json={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'expiry'})
    assert response.status_code==403 and crossed
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_old_confirmation_and_changed_key_body_never_alias(git_repo):
    app,client,root=git_repo;await setup(client,root)
    preview=(await client.post(PREVIEW,json=selection())).json()
    (root/'unchanged.txt').write_bytes(b'new live bytes')
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(CAPTURE,json={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'changed'})
    assert response.status_code==409 and app.service.projects.store.log_path.read_bytes()==before
    snapshot,body=await capture(client,key='committed')
    log=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(CAPTURE,json={**body,'snapshot_id':'ws-ffffffffffffffff'},headers={'Idempotency-Key':'committed'})).status_code==409
    assert app.service.projects.store.log_path.read_bytes()==log


@pytest.mark.controlled_process
@pytest.mark.parametrize('action',['revoke','archive','root-removed'])
async def test_withdrawal_denies_snapshot_manifest_and_private_file_reads(git_repo,action):
    app,client,root=git_repo;await setup(client,root)
    snapshot,body=await capture(client)
    mbody=manifest(snapshot)
    assert (await client.post(MANIFESTS,json=mbody,headers={'Idempotency-Key':'manifest'})).status_code==200
    if action=='revoke':
        assert (await client.post(f'/api/v1/source-grants/{GID}/revoke',json={'expected_revision':1},headers={'Idempotency-Key':'revoke'})).status_code==200
    elif action=='archive':
        assert (await client.post('/api/v1/projects',json={'project_id':snapshot['project_id'],'expected_revision':1,'name':'synthetic','archived':True},headers={'Idempotency-Key':'archive'})).status_code==200
    else:root.rename(root.with_name('synthetic-source-removed'))
    before=app.service.projects.store.log_path.read_bytes()
    for url in [f'{P}/workspace-snapshots/{WS}',f'{P}/workspace-snapshots/{WS}/items/0/layers/working',
                f'{MANIFESTS}/{CM}/revisions/1',f'{MANIFESTS}/{CM}/revisions/1/items/0']:
        assert (await client.get(url)).status_code==403
    assert (await client.post(CAPTURE,json=body,headers={'Idempotency-Key':'capture'})).status_code==403
    assert (await client.post(MANIFESTS,json=mbody,headers={'Idempotency-Key':'manifest'})).status_code==403
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('destination',['source','external-common'])
async def test_private_store_inside_authorized_git_source_denies_zero_writes(git_repo,monkeypatch,destination):
    from pyharness.application.workspace_store import WorkspaceStore
    from tests.unit.test_workspace_git_preview import git,META
    from tests.unit.test_source_grants import URL,issue
    app,client,base=git_repo;root=base;command=selection()
    if destination=='external-common':
        root=base.with_name('synthetic-linked');git(base,'worktree','add','-b','linked-fixture',str(root),'HEAD')
        assert (await client.post(URL,json=issue(base/'.git',grant_id=META),headers={'Idempotency-Key':'meta'})).status_code==200
        command=selection(metadata_grant_id=META,metadata_grant_revision=1)
    await setup(client,root)
    preview=(await client.post(PREVIEW,json=command)).json()
    original_store=app.service.projects.store
    await original_store.close()
    protected=root if destination=='source' else base/'.git'
    target=protected/'synthetic-private-control'
    assert not target.exists() and target.resolve().is_relative_to(base.parent.resolve())
    assert original_store.root.resolve().is_relative_to(base.parent.resolve())
    original_store.root.rename(target)  # fixture-owned synthetic state only, never a user runtime
    store=WorkspaceStore(target,tenant_id='default');app.service.projects.store=store
    writes=[];original=store._write_snapshot_blob
    def write(*args):writes.append(str(args[0]));return original(*args)
    monkeypatch.setattr(store,'_write_snapshot_blob',write)
    before=store.log_path.read_bytes()
    response=await client.post(CAPTURE,json={**command,'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'overlap'})
    assert response.status_code==403
    assert writes==[] and store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('destination',['source','external-common'])
async def test_workspace_manifest_moved_store_cannot_write_into_readonly_source(git_repo,monkeypatch,destination):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.core.tenant_settings import TenantSettingsStore
    from tests.unit.test_workspace_git_preview import git,META
    from tests.unit.test_source_grants import URL,issue
    app,client,base=git_repo;root=base;command=selection()
    if destination=='external-common':
        root=base.with_name('synthetic-linked');git(base,'worktree','add','-b','linked-fixture',str(root),'HEAD')
        assert (await client.post(URL,json=issue(base/'.git',grant_id=META),headers={'Idempotency-Key':'meta'})).status_code==200
        command=selection(metadata_grant_id=META,metadata_grant_revision=1)
    await setup(client,root);snapshot,_=await capture(client,command)
    old=app.service.projects.store;await old.close()
    protected=root if destination=='source' else base/'.git';target=protected/'synthetic-private-control'
    assert not target.exists() and target.resolve().is_relative_to(base.parent.resolve())
    assert old.root.resolve().is_relative_to(base.parent.resolve())
    old.root.rename(target)  # exact newly created synthetic fixture only
    store=WorkspaceStore(target,tenant_id='default');app.service.projects.store=store
    writes=[];original=TenantSettingsStore._atomic_write
    def write(path,raw):writes.append(path);return original(path,raw)
    monkeypatch.setattr(TenantSettingsStore,'_atomic_write',staticmethod(write))
    before=store.log_path.read_bytes()
    result=await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'readonly-source'})
    assert result.status_code==403 and not writes
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('operation',['preview','capture'])
async def test_lock_wait_budget_does_not_cancel_or_steal_holder(git_repo,monkeypatch,operation):
    import asyncio
    from pyharness.application.workspace_snapshot_store import WorkspaceSnapshotStore
    app,client,root=git_repo;await setup(client,root)
    command=selection();endpoint=PREVIEW
    if operation=='capture':
        preview=(await client.post(PREVIEW,json=command)).json()
        command={**command,'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']};endpoint=CAPTURE
    store=app.service.projects.store;before=store.log_path.read_bytes()
    monkeypatch.setattr(WorkspaceSnapshotStore,'MAX_WORKSPACE_CAPTURE_SECONDS',.03)
    await store._lock.acquire()
    try:
        response=await asyncio.wait_for(client.post(endpoint,json=command,headers={'Idempotency-Key':'deadline'}),.3)
        assert response.status_code==409
        assert store.log_path.read_bytes()==before
    finally:
        assert store._lock.locked()
        store._lock.release()
