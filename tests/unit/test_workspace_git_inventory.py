"""Explicit metadata discovery cannot capture, execute, or preapprove source bytes."""
import hashlib,json
import pytest
from tests.unit.test_source_grants import grant_app,GID,URL,issue
from tests.unit.test_workspace_git_preview import git_repo,dirty,git,META
from tests.unit.test_workspace_snapshots import setup,P

INVENTORY=f'{URL}/{GID}/git-input-candidates'


def command(**changes):
    return {'schema_version':1,'project_revision':1,'expected_revision':1,
        'metadata_grant_id':None,'metadata_grant_revision':None,'include_ignored':False,'limit':25,'cursor':None,**changes}


def source_hashes(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


@pytest.mark.controlled_process
async def test_explicit_inventory_metadata_only_and_masked_ignored_names(git_repo,monkeypatch):
    from pyharness.application import workspace_git
    app,client,root=git_repo;dirty(root)
    (root/'.gitignore').write_text('ignored.txt\n.env\n',encoding='utf-8')
    (root/'ignored.txt').write_bytes(b'synthetic ignored text')
    (root/'.env').write_bytes(b'password=synthetic-only-never-read')
    await setup(client,root)
    store=app.service.projects.store;before=store.log_path.read_bytes();source=source_hashes(root)
    def no_candidate_body(*_,**__):raise AssertionError('metadata inventory read a candidate body')
    monkeypatch.setattr(workspace_git,'read_selected',no_candidate_body)
    response=await client.post(INVENTORY,json=command())
    assert response.status_code==200,response.text
    value=response.json();rows=value['items'];by_path={r['path']:r for r in rows if r['path'] is not None}
    # Git must consume .gitignore rules to classify candidates; Harness must not
    # read/capture candidate byte content or claim that content is safe.
    assert value['status']=='git_candidates_observed' and value['body_policy']=='git_metadata_and_ignore_rules_only'
    assert value['content_safety']=='not_evaluated' and value['persisted'] is False and value['executable'] is False
    assert value['required_tracked_count']==2 and value['total']==5 and value['page_count']==5 and not value['has_more']
    assert by_path['unchanged.txt']['required'] and by_path['unchanged.txt']['head']['git_oid']!=by_path['unchanged.txt']['index']['git_oid']
    assert by_path['delete.txt']['working'] is None and by_path['delete.txt']['deletion_marker']
    assert by_path['new.txt']['origin']=='untracked' and not by_path['new.txt']['required']
    assert 'ignored.txt' not in by_path and '.env' not in response.text and 'synthetic-only-never-read' not in response.text
    ignored=await client.post(INVENTORY,json=command(include_ignored=True));assert ignored.status_code==200,ignored.text
    rows=ignored.json()['items'];assert any(r['path']=='ignored.txt' and r['origin']=='ignored' for r in rows)
    rejected=[r for r in rows if r['status']=='excluded'];assert len(rejected)==1 and rejected[0]['path'] is None and rejected[0]['reason']=='sensitive_name'
    assert store.log_path.read_bytes()==before and source_hashes(root)==source
    assert not (store.root/'snapshot-content').exists() and not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
async def test_inventory_pages_stable_and_new_candidate_invalidates_cursor(git_repo):
    _,client,root=git_repo;await setup(client,root)
    first=await client.post(INVENTORY,json=command(limit=1));assert first.status_code==200,first.text
    first=first.json();assert first['total']==2 and first['has_more']
    second=await client.post(INVENTORY,json=command(limit=1,cursor=first['next_cursor']));assert second.status_code==200,second.text
    second=second.json();assert not second['has_more'] and second['next_cursor'] is None
    assert second['view_digest']==first['view_digest'] and first['items'][0]['entry_index']<second['items'][0]['entry_index']
    (root/'new-candidate.txt').write_bytes(b'synthetic added after first page')
    assert (await client.post(INVENTORY,json=command(limit=1,cursor=first['next_cursor']))).status_code==409
    fresh=(await client.post(INVENTORY,json=command())).json();assert fresh['total']==3


@pytest.mark.controlled_process
async def test_linked_inventory_requires_exact_external_metadata_authority(git_repo):
    app,client,base=git_repo;root=base.with_name('synthetic-linked')
    git(base,'worktree','add','-b','inventory-fixture',str(root),'HEAD')
    assert (await client.post(URL,json=issue(base/'.git',grant_id=META),headers={'Idempotency-Key':'meta'})).status_code==200
    await setup(client,root);before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(INVENTORY,json=command())).status_code==403
    response=await client.post(INVENTORY,json=command(metadata_grant_id=META,metadata_grant_revision=1))
    assert response.status_code==200,response.text
    assert response.json()['repository_kind']=='linked_worktree' and response.json()['metadata_grant_id']==META
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('changes',[{'schema_version':True},{'include_ignored':'false'},{'limit':0},{'limit':51},{'cursor':'bad'},{'paths':['unchanged.txt']},{'actor_ref':'forged'}])
async def test_inventory_command_is_strict(git_repo,changes):
    app,client,root=git_repo;await setup(client,root);before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(INVENTORY,json=command(**changes))).status_code==422
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('change',['add-name','working-metadata','index'])
async def test_inventory_generation_change_rejects_without_commit(git_repo,monkeypatch,change):
    from pyharness.application import workspace_git
    app,client,root=git_repo;await setup(client,root)
    original=workspace_git._git;changed=False;enumerations=0
    async def read(*args,**kwargs):
        nonlocal changed,enumerations
        result=await original(*args,**kwargs)
        if '--others' in args[3]:enumerations+=1
        # A metadata-only observation may legitimately start after an edit.
        # Mutate after the file stat and before final verification instead.
        boundary=2 if change=='working-metadata' else 1
        if not changed and '--others' in args[3] and enumerations==boundary:
            changed=True
            if change=='add-name':(root/'late-candidate.txt').write_bytes(b'synthetic late name')
            elif change=='working-metadata':(root/'unchanged.txt').write_bytes(b'synthetic changed metadata')
            else:
                (root/'unchanged.txt').write_bytes(b'synthetic new index');git(root,'add','--','unchanged.txt')
        return result
    monkeypatch.setattr(workspace_git,'_git',read)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command())
    assert response.status_code==409 and changed
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('target',['main','metadata'])
async def test_inventory_final_root_metadata_expiry_denies_page(git_repo,monkeypatch,target):
    from datetime import datetime,timezone
    from pyharness.application import snapshot_store
    app,client,base=git_repo;root=base;body=command()
    if target=='metadata':
        root=base.with_name('synthetic-linked');git(base,'worktree','add','-b','inventory-expiry',str(root),'HEAD')
        assert (await client.post(URL,json=issue(base/'.git',grant_id=META),headers={'Idempotency-Key':'meta'})).status_code==200
        body=command(metadata_grant_id=META,metadata_grant_revision=1)
    await setup(client,root)
    original=snapshot_store.check_root_metadata;calls=0;crossed=False
    class Future(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2100,1,1,tzinfo=timezone.utc)
    def read(row):
        nonlocal calls,crossed
        result=original(row)
        if row['grant']['grant_id']==(GID if target=='main' else META):
            calls+=1
            if calls>=2:crossed=True;monkeypatch.setattr(snapshot_store,'datetime',Future)
        return result
    monkeypatch.setattr(snapshot_store,'check_root_metadata',read)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=body)
    assert response.status_code==403 and crossed
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_inventory_over_count_rejects_instead_of_truncating(git_repo):
    app,client,root=git_repo;await setup(client,root)
    for n in range(2001):(root/f'synthetic-{n:04}.txt').write_bytes(b'synthetic candidate')
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command(limit=50))
    assert response.status_code==422 and response.json()['reason']=='git_candidate_count_limit'
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_inventory_cursor_reopens_without_writing_control_facts(git_repo):
    from pyharness.application.workspace_store import WorkspaceStore
    app,client,root=git_repo;await setup(client,root)
    first=(await client.post(INVENTORY,json=command(limit=1))).json();assert first['has_more']
    store=app.service.projects.store;before=store.log_path.read_bytes();await store.close()
    app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    response=await client.post(INVENTORY,json=command(limit=1,cursor=first['next_cursor']))
    assert response.status_code==200 and response.json()['view_digest']==first['view_digest']
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('boundary',['expiry','budget'])
async def test_inventory_page_build_is_inside_final_gate(git_repo,monkeypatch,boundary):
    from datetime import datetime,timezone
    from pyharness.application import snapshot_store
    from pyharness.application.workspace_git_inventory import GitInventoryCandidate
    app,client,root=git_repo;await setup(client,root)
    store=app.service.projects.store;original=GitInventoryCandidate.page;crossed=False
    class Future(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2100,1,1,tzinfo=timezone.utc)
    def page(candidate):
        nonlocal crossed
        result=original(candidate);crossed=True
        if boundary=='expiry':monkeypatch.setattr(snapshot_store,'datetime',Future)
        else:monkeypatch.setattr(store,'MAX_WORKSPACE_CAPTURE_SECONDS',0)
        return result
    monkeypatch.setattr(GitInventoryCandidate,'page',page)
    before=store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command())
    assert response.status_code==(403 if boundary=='expiry' else 409) and crossed
    if boundary=='budget':assert response.json()['reason']=='workspace_capture_time_limit'
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('directory',['','nested'])
async def test_self_ignored_rule_metadata_change_invalidates_cursor(git_repo,directory):
    app,client,root=git_repo;await setup(client,root)
    folder=root/directory;folder.mkdir(exist_ok=True)
    rules=folder/'.gitignore';rules.write_text('.gitignore\nignored.txt\n',encoding='utf-8')
    if directory:(folder/'candidate.txt').write_bytes(b'synthetic candidate')
    first=await client.post(INVENTORY,json=command(limit=1));assert first.status_code==200,first.text
    before=app.service.projects.store.log_path.read_bytes()
    rules.write_text('.gitignore\nignored.txt\n# synthetic same classification changed metadata\n',encoding='utf-8')
    response=await client.post(INVENTORY,json=command(limit=1,cursor=first.json()['next_cursor']))
    assert response.status_code==409,response.text
    assert app.service.projects.store.log_path.read_bytes()==before
