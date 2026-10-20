"""Synthetic account, role, project and metadata boundaries through real ASGI."""
import os,subprocess
from pathlib import Path
import pytest
from tests.unit.test_workspace_snapshot_permissions import workspace_team
from tests.unit.test_context_manifest_permissions import context_team,join
from tests.unit.test_source_grants import grant_app,GID
from tests.unit.test_workspace_git_preview import git_repo,git
from tests.unit.test_workspace_snapshots import setup,P
from tests.unit.test_workspace_git_inventory import INVENTORY,command


@pytest.mark.controlled_process
async def test_inventory_subject_cursor_and_cross_tenant_are_not_authority(workspace_team):
    _,owner,member,accounts,_,service=workspace_team
    assert (await member.post(INVENTORY,json=command())).status_code==403
    first=await owner.post(INVENTORY,json=command(limit=1));assert first.status_code==200,first.text
    await join(member,accounts);before=service.projects.store.log_path.read_bytes()
    assert (await member.post(INVENTORY,json=command(limit=1,cursor=first.json()['next_cursor']))).status_code==409
    assert (await member.post(INVENTORY,json=command())).status_code==200
    assert service.projects.store.log_path.read_bytes()==before and not service._engines and not service._queues


@pytest.mark.controlled_process
@pytest.mark.parametrize('role',['viewer','remove'])
async def test_inventory_role_change_denies_old_cursor(workspace_team,role):
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    first=await member.post(INVENTORY,json=command(limit=1));assert first.status_code==200
    accounts.membership('context-team','ctx-member',role);before=service.projects.store.log_path.read_bytes()
    assert (await member.post(INVENTORY,json=command(limit=1,cursor=first.json()['next_cursor']))).status_code in (401,403)
    assert service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_inventory_page_build_role_change_denies_response(workspace_team,monkeypatch):
    from pyharness.application.workspace_git_inventory import GitInventoryCandidate
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    original=GitInventoryCandidate.page;changed=False
    def page(candidate):
        nonlocal changed
        result=original(candidate);accounts.membership('context-team','ctx-member','remove');changed=True;return result
    monkeypatch.setattr(GitInventoryCandidate,'page',page)
    before=service.projects.store.log_path.read_bytes()
    assert (await member.post(INVENTORY,json=command())).status_code==403 and changed
    assert service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_inventory_project_revision_and_cross_project_binding(git_repo):
    app,client,root=git_repo;await setup(client,root)
    other='p-ffffffffffffffff'
    assert (await client.post('/api/v1/projects',json={'project_id':other,'expected_revision':0,'name':'Synthetic other'},headers={'Idempotency-Key':'other'})).status_code==200
    assert (await client.post(P,json={})).status_code in (404,405)  # no write route hidden behind preview
    assert (await client.post('/api/v1/projects',json={'project_id':P.rsplit('/',1)[-1],'expected_revision':1,'name':'Synthetic r2'},headers={'Idempotency-Key':'r2'})).status_code==200
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(INVENTORY,json=command())).status_code==409
    assert (await client.post(f'/api/v1/projects/{other}/source-grants/{GID}/git-input-candidates',json=command())).status_code==403
    assert (await client.post(INVENTORY,json=command(project_revision=2))).status_code==200
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_inventory_never_reads_ordinary_candidate_bodies(git_repo,monkeypatch):
    app,client,root=git_repo;await setup(client,root)
    (root/'.gitignore').write_text('ignored.txt\n',encoding='utf-8')
    (root/'ignored.txt').write_bytes(b'synthetic ignored content')
    original=Path.read_bytes;reads=[]
    def read(path):
        if path.is_relative_to(root) and '.git' not in path.relative_to(root).parts:
            reads.append(path);raise AssertionError('Harness read a worktree body during metadata inventory')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    original_open=os.open
    def open_file(name,*args,**kwargs):
        path=Path(name) if isinstance(name,(str,Path)) else None
        if path is not None and path.is_relative_to(root) and '.git' not in path.relative_to(root).parts and path.name!='.gitignore':
            reads.append(path);raise AssertionError('ordinary candidate body opened')
        return original_open(name,*args,**kwargs)
    # Explicit ignore-rule metadata uses its checked descriptor; ordinary
    # candidate bodies may not use either generic or low-level file readers.
    monkeypatch.setattr(os,'open',open_file)
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(INVENTORY,json=command(include_ignored=True))).status_code==200
    assert reads==[] and app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_inventory_hardlinked_candidate_is_masked_not_read(git_repo):
    app,client,root=git_repo;await setup(client,root)
    os.link(root/'unchanged.txt',root/'hardlinked-copy.txt')
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command());assert response.status_code==200,response.text
    value=response.json();assert value['excluded_required_count']==1
    excluded=[i for i in value['items'] if i['status']=='excluded']
    assert len(excluded)==2 and all(i['path'] is None and i['reason']=='hard_link' for i in excluded)
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('boundary',['hardlinked-rule','rule-size','directory-link'])
async def test_inventory_rule_boundaries_precede_git_rule_reads(git_repo,monkeypatch,boundary):
    from pyharness.application import workspace_git
    app,client,root=git_repo;await setup(client,root)
    if boundary=='hardlinked-rule':os.link(root/'unchanged.txt',root/'.gitignore')
    elif boundary=='rule-size':(root/'.gitignore').write_bytes(b'#'+b'x'*(512*1024))
    else:
        target=root.parent/'synthetic-outside';target.mkdir();(target/'.gitignore').write_bytes(b'synthetic outside rule')
        link=root/'synthetic-junction'
        assert target.resolve().is_relative_to(root.parent.resolve()) and not target.resolve().is_relative_to(root.resolve())
        if os.name=='nt':
            result=subprocess.run(['cmd','/d','/c','mklink','/J',str(link),str(target)],capture_output=True,timeout=10)
            assert result.returncode==0
        else:link.symlink_to(target,target_is_directory=True)
    original=workspace_git._git;rule_reads=[]
    async def git_read(*args,**kwargs):
        if '--others' in args[3]:rule_reads.append(args[3])
        return await original(*args,**kwargs)
    monkeypatch.setattr(workspace_git,'_git',git_read)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command())
    assert response.status_code==(422 if boundary=='rule-size' else 403),response.text
    assert rule_reads==[] and app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_rule_replacement_cannot_make_native_git_read_external_body(git_repo,monkeypatch):
    from pyharness.application import workspace_git
    app,client,root=git_repo;await setup(client,root)
    (root/'new.txt').write_bytes(b'synthetic ordinary body')
    rule=root/'.gitignore';rule.write_bytes(b'# synthetic safe rule\n')
    outside=root.parent/'synthetic-external-rule.txt';outside.write_bytes(b'new.txt\n')
    held=root/'original-rule.held'
    assert rule.resolve().is_relative_to(root.resolve()) and held.resolve().is_relative_to(root.resolve())
    assert outside.resolve().is_relative_to(root.parent.resolve()) and not outside.resolve().is_relative_to(root.resolve())
    original=workspace_git._git;mutated=False;observed=[]
    async def read(*args,**kwargs):
        nonlocal mutated
        if not mutated and '--others' in args[3]:
            os.replace(rule,held);os.link(outside,rule);mutated=True
        result=await original(*args,**kwargs)
        if '--others' in args[3]:observed.append(result)
        return result
    monkeypatch.setattr(workspace_git,'_git',read)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command())
    assert mutated and observed
    # Merely rejecting the response cannot undo an external native read. The
    # external rule would hide new.txt; native enumeration must use safe bytes.
    assert b'new.txt\0' in observed[0],'native Git consumed the substituted external ignore rule'
    assert response.status_code in (403,409),response.text
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_windows_case_variant_ignore_rule_fails_closed(git_repo):
    app,client,root=git_repo;await setup(client,root)
    (root/'.GITIGNORE').write_bytes(b'new.txt\n');(root/'new.txt').write_bytes(b'synthetic candidate')
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command())
    if os.name=='nt':assert response.status_code==403,response.text
    else:
        assert response.status_code==200,response.text
        assert any(i['path']=='new.txt' for i in response.json()['items'])
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_replaced_rule_descriptor_is_rejected_before_byte_read(git_repo,monkeypatch):
    app,client,root=git_repo;await setup(client,root)
    rule=root/'.gitignore';rule.write_bytes(b'# synthetic safe\n')
    outside=root.parent/'synthetic-outside-descriptor.txt';outside.write_bytes(b'synthetic-external-rule\n')
    held=root/'synthetic-held-rule.txt';assert held.resolve().is_relative_to(root.resolve())
    external_id=(outside.stat().st_dev,outside.stat().st_ino)
    original_open=os.open;original_fdopen=os.fdopen;mutated=False;external_reads=[]
    def open_file(name,*args,**kwargs):
        nonlocal mutated
        if not mutated and Path(name)==rule:
            os.replace(rule,held);os.link(outside,rule);mutated=True
        return original_open(name,*args,**kwargs)
    class Stream:
        def __init__(self,stream):self.stream=stream
        def __getattr__(self,name):return getattr(self.stream,name)
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def read(self,*args):
            info=os.fstat(self.stream.fileno())
            if (info.st_dev,info.st_ino)==external_id:external_reads.append(True)
            return self.stream.read(*args)
    monkeypatch.setattr(os,'open',open_file)
    monkeypatch.setattr(os,'fdopen',lambda *args,**kwargs:Stream(original_fdopen(*args,**kwargs)))
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command())
    assert response.status_code==403 and mutated,response.text
    assert external_reads==[] and app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_nested_repository_boundary_is_not_flattened_into_candidates(git_repo):
    app,client,root=git_repo;await setup(client,root)
    nested=root/'synthetic-nested-repo';nested.mkdir()
    git(nested,'init','--initial-branch=nested-fixture')
    (nested/'nested.txt').write_bytes(b'synthetic nested repository material')
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INVENTORY,json=command())
    assert response.status_code==403,response.text
    assert 'synthetic-nested-repo/nested.txt' not in response.text
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('replacement',['foreign-inode','second-link'])
def test_metadata_descriptor_must_be_checked_before_first_byte(tmp_path,monkeypatch,replacement):
 from pyharness.application import workspace_git
 from pyharness.errors import PyHError
 root=tmp_path/'owned-metadata';root.mkdir();source=root/'index';source.write_bytes(b'synthetic original metadata')
 foreign=tmp_path/'synthetic-outside-metadata';held=root/'held-original';opened=[];reads=[]
 if replacement=='foreign-inode':foreign.write_bytes(b'synthetic external metadata sentinel')
 original_open=os.open;original_fdopen=os.fdopen
 def open_file(name,*args,**kwargs):
  if Path(name)==source:
   if replacement=='foreign-inode':os.replace(source,held);os.link(foreign,source)
   else:os.link(source,foreign)
  descriptor=original_open(name,*args,**kwargs)
  if Path(name)==source:opened.append(descriptor)
  return descriptor
 class Stream:
  def __init__(self,value):self.value=value
  def __getattr__(self,name):return getattr(self.value,name)
  def __enter__(self):self.value.__enter__();return self
  def __exit__(self,*args):return self.value.__exit__(*args)
  def read(self,*args):
   if self.value.fileno() in opened:reads.append(True)
   return self.value.read(*args)
 monkeypatch.setattr(os,'open',open_file)
 monkeypatch.setattr(os,'fdopen',lambda *args,**kwargs:Stream(original_fdopen(*args,**kwargs)))
 error=None
 try:workspace_git._metadata(root,'index')
 except PyHError as exc:error=exc
 assert reads==[],'metadata bytes were read before validating the opened descriptor'
 assert error is not None,'changed or hardlinked descriptor was accepted'
 assert error.code=='EVT-101' and error.ctx.get('reason')=='git_metadata_changed'
