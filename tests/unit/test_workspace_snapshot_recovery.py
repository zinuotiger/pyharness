"""Given private immutable Git captures, partial failures never invent acceptance."""
import hashlib
import json
import subprocess
import sys
import pytest
from tests.unit.test_source_grants import grant_app,PID
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_snapshots import selection,setup,capture,manifest,PREVIEW,CAPTURE,MANIFESTS,P,WS,CM


@pytest.mark.controlled_process
async def test_private_content_corrupted_by_later_manifest_write_never_commits(git_repo,monkeypatch):
    from pyharness.core.tenant_settings import TenantSettingsStore
    app,client,root=git_repo;await setup(client,root)
    preview=(await client.post(PREVIEW,json=selection())).json()
    store=app.service.projects.store;original=TenantSettingsStore._atomic_write;corrupted=False
    def write(path,raw):
        nonlocal corrupted
        original(path,raw)
        if path.parent==store.root/'blobs':
            files=list((store.root/'snapshot-content').glob('*.bin'))
            assert files
            files[0].write_bytes(b'synthetic corruption after initial readback');corrupted=True
    monkeypatch.setattr(TenantSettingsStore,'_atomic_write',staticmethod(write))
    before=store.log_path.read_bytes()
    response=await client.post(CAPTURE,json={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'corrupt'})
    assert response.status_code==503 and corrupted
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('fault',['before-append','after-disk-commit'])
async def test_unknown_capture_reopens_with_exact_original_key_body(git_repo,monkeypatch,fault):
    from pyharness.application.workspace_store import WorkspaceStore
    app,client,root=git_repo;await setup(client,root)
    preview=(await client.post(PREVIEW,json=selection())).json()
    body={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']}
    store=app.service.projects.store;before=store.log_path.read_bytes()
    if fault=='before-append':
        async def append(*_,**__):raise OSError('synthetic full disk')
        monkeypatch.setattr(store._session,'append',append)
    else:
        def verify(*_):raise OSError('synthetic unconfirmed disk receipt')
        monkeypatch.setattr(store,'_verify_commit',verify)
    assert (await client.post(CAPTURE,json=body,headers={'Idempotency-Key':'unknown'})).status_code==503
    assert store._failed
    if fault=='before-append':assert store.log_path.read_bytes()==before
    else:assert store.log_path.read_bytes()!=before
    await store.close()
    monkeypatch.undo()
    app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    response=await client.post(CAPTURE,json=body,headers={'Idempotency-Key':'unknown'})
    assert response.status_code==200,response.text
    log=app.service.projects.store.log_path.read_bytes()
    events=[json.loads(line) for line in log.splitlines()]
    assert len([e for e in events if e.get('payload',{}).get('resource_kind')=='workspace_snapshot'])==1
    assert (await client.post(CAPTURE,json=body,headers={'Idempotency-Key':'unknown'})).json()==response.json()
    assert app.service.projects.store.log_path.read_bytes()==log


@pytest.mark.controlled_process
async def test_independent_owner_reject_then_replay_and_rebuild_exact(git_repo):
    app,client,root=git_repo;await setup(client,root)
    snapshot,_=await capture(client)
    m=(await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})).json()
    store=app.service.projects.store;before=store.log_path.read_bytes()
    probe='''import asyncio,json,sys
from pathlib import Path
from pyharness.application.workspace_store import WorkspaceStore
from pyharness.errors import PyHError
async def run():
 s=WorkspaceStore(Path(sys.argv[1]),tenant_id='default')
 try:
  row=await s.workspace_manifest(sys.argv[2],sys.argv[3],1,authorize=lambda:'local-operator')
  rebuilt=s._project(list(s._store.replay()))
  assert rebuilt[0][sys.argv[3]]['digest']==row['digest']
  print(json.dumps({'row':row,'rebuilt':True}))
 except PyHError as e:print(json.dumps({'error':e.code}));sys.exit(3)
 finally:await s.close()
asyncio.run(run())'''
    argv=[sys.executable,'-B','-c',probe,str(store.root),PID,CM]
    blocked=subprocess.run(argv,capture_output=True,text=True,timeout=20)
    assert blocked.returncode==3 and json.loads(blocked.stdout)['error']=='PERS-202'
    await store.close()
    restored=subprocess.run(argv,capture_output=True,text=True,timeout=20)
    assert restored.returncode==0,restored.stderr
    assert json.loads(restored.stdout)=={'row':m,'rebuilt':True}
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('fault',['before-append','after-disk-commit'])
async def test_unknown_workspace_manifest_retains_original_key_and_body_after_restart(git_repo,monkeypatch,fault):
    from pyharness.application.workspace_store import WorkspaceStore
    app,client,root=git_repo;await setup(client,root)
    snapshot,_=await capture(client);command=manifest(snapshot)
    store=app.service.projects.store;before=store.log_path.read_bytes()
    if fault=='before-append':
        async def append(*_,**__):raise OSError('synthetic append unavailable')
        monkeypatch.setattr(store._session,'append',append)
    else:
        def verify(*_):raise OSError('synthetic exact disk receipt unavailable')
        monkeypatch.setattr(store,'_verify_commit',verify)
    assert (await client.post(MANIFESTS,json=command,headers={'Idempotency-Key':'unknown-manifest'})).status_code==503
    assert store._failed
    assert (store.log_path.read_bytes()==before)==(fault=='before-append')
    await store.close();monkeypatch.undo()
    app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    response=await client.post(MANIFESTS,json=command,headers={'Idempotency-Key':'unknown-manifest'})
    assert response.status_code==200,response.text
    exact=await client.get(f'{MANIFESTS}/{CM}/revisions/1');assert exact.json()==response.json()
    log=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(MANIFESTS,json=command,headers={'Idempotency-Key':'unknown-manifest'})).json()==response.json()
    assert app.service.projects.store.log_path.read_bytes()==log
    assert sum(json.loads(line).get('payload',{}).get('resource_kind')=='context_manifest' for line in log.splitlines())==1
    assert not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
async def test_workspace_pagination_restarts_and_withdrawn_source_invalidates_cursor(git_repo):
    from pyharness.application.workspace_store import WorkspaceStore
    from tests.unit.test_source_grants import GID
    app,client,root=git_repo;await setup(client,root)
    await capture(client);await capture(client,snapshot_id='ws-2222222222222222',key='second')
    first=await client.get(P+'/workspace-snapshots?limit=1');assert first.status_code==200
    first=first.json();assert first['has_more']
    store=app.service.projects.store;log=store.log_path.read_bytes();await store.close()
    app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    query={'limit':'1','cursor':first['next_cursor']}
    second=await client.get(P+'/workspace-snapshots',params=query);assert second.status_code==200,second.text
    assert second.json()['source_watermark']==first['source_watermark'] and second.json()['view_digest']==first['view_digest']
    assert second.json()['snapshots'][0]['source_seq']>first['snapshots'][0]['source_seq']
    assert store.log_path.read_bytes()==log
    assert (await client.post(f'/api/v1/source-grants/{GID}/revoke',json={'expected_revision':1},headers={'Idempotency-Key':'withdraw-source'})).status_code==200
    assert (await client.get(P+'/workspace-snapshots',params=query)).status_code==409
    fresh=(await client.get(P+'/workspace-snapshots')).json();assert fresh['snapshots']==[] and fresh['total']==0
