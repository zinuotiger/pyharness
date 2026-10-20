"""Real synthetic account middleware and current actor checks; no user runtime data."""
from pathlib import Path
import pytest
from tests.unit.test_context_manifest_permissions import context_team,join
from tests.unit.test_source_grants import grant_app,PID,GID,URL,issue
from tests.unit.test_workspace_git_preview import git_repo,git,META
from tests.unit.test_workspace_snapshots import setup,capture,selection,manifest,PREVIEW,CAPTURE,MANIFESTS,P,WS,CM,TASK


@pytest.fixture
async def workspace_team(context_team):
    app,owner,member,accounts,_=context_team
    service=app._service_registry.get('context-team')
    resources,_=service.projects.store._grant_state()
    root=Path(resources[GID]['grant']['canonical_root'])
    (root/'unchanged.txt').write_bytes(b'head\r\n');(root/'delete.txt').write_bytes(b'delete\r\n')
    git(root,'init','--initial-branch=fixture');git(root,'add','--','unchanged.txt','delete.txt');git(root,'commit','-m','synthetic seed')
    yield app,owner,member,accounts,root,service


@pytest.mark.controlled_process
async def test_cross_tenant_team_and_task_ownership_cannot_read_private_bytes(workspace_team):
    app,owner,member,accounts,root,service=workspace_team
    snapshot,_=await capture(owner,key='owner-workspace-capture')
    task=(await owner.post(P+'/context-task-references',json={'schema_version':1,'product_task_id':TASK,
        'expected_revision':0,'project_revision':1,'title':'Synthetic owner task','retired':False},headers={'Idempotency-Key':'task'})).json()
    ref={k:task[k] for k in ['product_task_id','revision','digest','source_seq']}
    command=manifest(snapshot,task_ref=ref)
    assert (await owner.post(MANIFESTS,json=command,headers={'Idempotency-Key':'manifest'})).status_code==200
    paths=[f'{P}/workspace-snapshots/{WS}',f'{P}/workspace-snapshots/{WS}/items/0/layers/working',
           f'{MANIFESTS}/{CM}/revisions/1',f'{MANIFESTS}/{CM}/revisions/1/items/0']
    for path in paths: assert (await member.get(path)).status_code==403
    await join(member,accounts)
    # New workspace list contract is paged; private ownership still exposes zero rows.
    for url,kind in [(P+'/workspace-snapshots','snapshots'),(MANIFESTS,'manifests')]:
        page=(await member.get(url)).json()
        assert page[kind]==[] and page['total']==page['page_count']==0
        assert page['has_more'] is False and page['next_cursor'] is None
    for path in paths: assert (await member.get(path)).status_code==403
    own,_=await capture(member,snapshot_id='ws-eeeeeeeeeeeeeeee',key='member-capture')
    before=service.projects.store.log_path.read_bytes()
    assert (await member.post(MANIFESTS,json=manifest(own,manifest_id='cm-ffffffffffffffff',task_ref=ref),headers={'Idempotency-Key':'wrong-task'})).status_code==403
    assert service.projects.store.log_path.read_bytes()==before
    assert not service._engines and not service._queues


@pytest.mark.controlled_process
@pytest.mark.parametrize('role',['viewer','remove'])
async def test_current_role_revocation_denies_files_reads_and_old_receipts(workspace_team,role):
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    snapshot,body=await capture(member)
    command=manifest(snapshot)
    assert (await member.post(MANIFESTS,json=command,headers={'Idempotency-Key':'manifest'})).status_code==200
    accounts.membership('context-team','ctx-member',role)
    before=service.projects.store.log_path.read_bytes()
    for path in [P+'/workspace-snapshots',f'{P}/workspace-snapshots/{WS}/items/0/layers/head',MANIFESTS,f'{MANIFESTS}/{CM}/revisions/1/items/0']:
        assert (await member.get(path)).status_code in (401,403)
    assert (await member.post(CAPTURE,json=body,headers={'Idempotency-Key':'capture'})).status_code in (401,403)
    assert (await member.post(MANIFESTS,json=command,headers={'Idempotency-Key':'manifest'})).status_code in (401,403)
    assert service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_revocation_during_private_capture_write_never_commits(workspace_team,monkeypatch):
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    preview=(await member.post(PREVIEW,json=selection())).json()
    store=service.projects.store;original=store._write_snapshot_blob;removed=False
    def write(*args):
        nonlocal removed
        result=original(*args)
        if not removed:accounts.membership('context-team','ctx-member','remove');removed=True
        return result
    monkeypatch.setattr(store,'_write_snapshot_blob',write)
    before=store.log_path.read_bytes()
    response=await member.post(CAPTURE,json={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'withdrawn'})
    assert response.status_code==403 and removed
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_revocation_during_git_read_prevents_first_private_write(workspace_team,monkeypatch):
    from pyharness.application import workspace_git
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    preview=(await member.post(PREVIEW,json=selection())).json()
    store=service.projects.store;original_git=workspace_git._git;original_write=store._write_snapshot_blob
    removed=False;writes=[]
    async def read(*args,**kwargs):
        nonlocal removed
        result=await original_git(*args,**kwargs)
        if not removed:accounts.membership('context-team','ctx-member','remove');removed=True
        return result
    def write(*args):writes.append(str(args[0]));return original_write(*args)
    monkeypatch.setattr(workspace_git,'_git',read);monkeypatch.setattr(store,'_write_snapshot_blob',write)
    before=store.log_path.read_bytes()
    response=await member.post(CAPTURE,json={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'revoked-before-copy'})
    assert response.status_code==403 and removed
    assert writes==[] and store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_cross_project_snapshot_and_manifest_references_denied(git_repo):
    app,client,root=git_repo;await setup(client,root)
    snapshot,_=await capture(client)
    other='p-ffffffffffffffff'
    assert (await client.post('/api/v1/projects',json={'project_id':other,'expected_revision':0,'name':'Synthetic other'},headers={'Idempotency-Key':'other'})).status_code==200
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.get(f'/api/v1/projects/{other}/workspace-snapshots/{WS}')).status_code==403
    assert (await client.post(f'/api/v1/projects/{other}/workspace-context-manifests',json=manifest(snapshot),headers={'Idempotency-Key':'cross-project'})).status_code==403
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_retired_task_denies_old_workspace_context_and_original_receipt(git_repo):
    app,client,root=git_repo;await setup(client,root);snapshot,_=await capture(client)
    body={'schema_version':1,'product_task_id':TASK,'expected_revision':0,'project_revision':1,'title':'Synthetic target','retired':False}
    task=(await client.post(P+'/context-task-references',json=body,headers={'Idempotency-Key':'target'})).json()
    command=manifest(snapshot,task_ref={k:task[k] for k in ['product_task_id','revision','digest','source_seq']})
    assert (await client.post(MANIFESTS,json=command,headers={'Idempotency-Key':'manifest'})).status_code==200
    assert (await client.post(P+'/context-task-references',json={**body,'expected_revision':1,'retired':True},headers={'Idempotency-Key':'retire'})).status_code==200
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.get(f'{MANIFESTS}/{CM}/revisions/1')).status_code==403
    assert (await client.get(f'{MANIFESTS}/{CM}/revisions/1/items/0')).status_code==403
    assert (await client.post(MANIFESTS,json=command,headers={'Idempotency-Key':'manifest'})).status_code==403
    assert (await client.get(MANIFESTS)).json()['manifests']==[]
    assert app.service.projects.store.log_path.read_bytes()==before and not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind',['snapshot','manifest'])
async def test_expiry_during_final_private_byte_read_never_returns_body(git_repo,monkeypatch,kind):
    from datetime import datetime,timezone
    from pyharness.application import snapshot_store
    app,client,root=git_repo;await setup(client,root);snapshot,_=await capture(client)
    assert (await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})).status_code==200
    store=app.service.projects.store;target=store._snapshot_content(snapshot['items'][0]['working']['digest'])
    from pyharness.application import source_snapshot
    original=source_snapshot.checked_private_bytes;reads=0;expired=False;final_reads=0
    class Future(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2100,1,1,tzinfo=timezone.utc) if expired else datetime.now(tz)
    def read(path,**kwargs):
        nonlocal reads,expired,final_reads
        raw=original(path,**kwargs)
        if path==target:
            reads+=1
            if kwargs.get('before_read') is not None:final_reads+=1;expired=True
        return raw
    monkeypatch.setattr(snapshot_store,'datetime',Future);monkeypatch.setattr(source_snapshot,'checked_private_bytes',read)
    url=f'{P}/workspace-snapshots/{WS}/items/0/layers/working' if kind=='snapshot' else f'{MANIFESTS}/{CM}/revisions/1/items/0'
    response=await client.get(url)
    assert response.status_code==403 and reads>=2 and expired
    assert final_reads==1
    assert response.content!=b'head\r\n'


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind',['snapshot','manifest','task'])
async def test_post_commit_role_loss_is_unknown_without_leaking_receipt(workspace_team,monkeypatch,kind):
    from pyharness.application.workspace_store import WorkspaceStore
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    if kind=='snapshot':
        preview=(await member.post(PREVIEW,json=selection())).json()
        body={**selection(),'snapshot_id':WS,'confirmation_digest':preview['confirmation_digest']};url=CAPTURE;resource='workspace_snapshot'
    elif kind=='manifest':
        snapshot,_=await capture(member);body=manifest(snapshot);url=MANIFESTS;resource='context_manifest'
    else:
        body={'schema_version':1,'product_task_id':TASK,'expected_revision':0,'project_revision':1,'title':'Synthetic member reference','retired':False}
        url=P+'/context-task-references';resource='task_definition'
    store=service.projects.store;original=store._session.append
    async def append(*args,**kwargs):
        event=await original(*args,**kwargs)
        if event.payload.get('resource_kind')==resource:accounts.membership('context-team','ctx-member','remove')
        return event
    monkeypatch.setattr(store._session,'append',append)
    result=await member.post(url,json=body,headers={'Idempotency-Key':'post-commit-unknown'})
    assert result.status_code==503 and 'digest' not in result.json()
    log=store.log_path.read_bytes()
    assert sum(__import__('json').loads(line).get('payload',{}).get('resource_kind')==resource for line in log.splitlines())==1
    await store.close();monkeypatch.undo();accounts.membership('context-team','ctx-member','member')
    service.projects.store=WorkspaceStore(store.root,tenant_id='context-team')
    confirmed=await member.post(url,json=body,headers={'Idempotency-Key':'post-commit-unknown'})
    assert confirmed.status_code==200,confirmed.text
    assert service.projects.store.log_path.read_bytes()==log


@pytest.mark.controlled_process
@pytest.mark.parametrize('change',['revoke','expire'])
async def test_external_metadata_grant_remains_required_for_copied_history(git_repo,monkeypatch,change):
    from datetime import datetime,timezone
    from pyharness.application import snapshot_store
    app,client,base=git_repo
    root=base.with_name('synthetic-linked');git(base,'worktree','add','-b','linked-fixture',str(root),'HEAD')
    assert (await client.post(URL,json=issue(base/'.git',grant_id=META,expires_at='2030-01-01T00:00:00Z'),headers={'Idempotency-Key':'meta'})).status_code==200
    await setup(client,root)
    snapshot,_=await capture(client,selection(metadata_grant_id=META,metadata_grant_revision=1))
    assert (await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})).status_code==200
    if change=='revoke':
        assert (await client.post(f'/api/v1/source-grants/{META}/revoke',json={'expected_revision':1},headers={'Idempotency-Key':'revoke-meta'})).status_code==200
    else:
        class Future(datetime):
            @classmethod
            def now(cls,tz=None):return datetime(2031,1,1,tzinfo=timezone.utc)
        monkeypatch.setattr(snapshot_store,'datetime',Future)
    for path in [f'{P}/workspace-snapshots/{WS}',f'{P}/workspace-snapshots/{WS}/items/0/layers/working',f'{MANIFESTS}/{CM}/revisions/1']:
        assert (await client.get(path)).status_code==403
    assert (await client.get(P+'/workspace-snapshots')).json()['total']==0
    assert (await client.get(MANIFESTS)).json()['total']==0
