"""Given an exact input source, when authority/disk fails, then no commit or host fallback."""
import os,io,json
from pathlib import Path
import pytest
from pyharness.errors import PyHError
from tests.unit.test_source_grants import grant_app,PID,GID
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_owned_inputs import context,command,INPUTS,WI,P,WS


async def _private_replacement(client,store,root,blob,url,monkeypatch,*,operation=None,enabled=lambda:True):
    outside=root.parent/'synthetic-external-private.txt';outside.write_bytes(b'synthetic foreign bytes')
    held=blob.with_suffix('.held');original_open=os.open;original_io=io.open;foreign_id=(outside.stat().st_dev,outside.stat().st_ino)
    mutated=False;foreign_reads=[]
    def substitute(name):
        nonlocal mutated
        if not mutated and enabled() and isinstance(name,(str,bytes,Path)) and Path(name)==blob:
            os.replace(blob,held);os.link(outside,blob);mutated=True
    class Stream:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):return getattr(self.value,name)
        def __enter__(self):self.value.__enter__();return self
        def __exit__(self,*args):return self.value.__exit__(*args)
        def read(self,*args):
            info=os.fstat(self.value.fileno())
            if (info.st_dev,info.st_ino)==foreign_id:foreign_reads.append(True)
            return self.value.read(*args)
    def opened(name,*args,**kwargs):substitute(name);return original_open(name,*args,**kwargs)
    def io_open(name,*args,**kwargs):substitute(name);return Stream(original_io(name,*args,**kwargs))
    monkeypatch.setattr(os,'open',opened);monkeypatch.setattr(io,'open',io_open)
    before=store.log_path.read_bytes();response=await operation() if operation else await client.get(url)
    assert response.status_code==503 and mutated,response.text
    assert foreign_reads==[],'private content validation consumed a substituted external descriptor'
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_private_descriptor_replacement_never_reads_foreign_bytes(git_repo,monkeypatch):
    app,client,root=git_repo;snapshot,_=await context(client,root)
    store=app.service.projects.store;blob=store._snapshot_content(snapshot['items'][0]['working']['digest'])
    await _private_replacement(client,store,root,blob,f'{P}/workspace-snapshots/{WS}',monkeypatch)


async def test_legacy_private_descriptor_replacement_never_reads_foreign_bytes(grant_app,monkeypatch):
    import hashlib
    from tests.unit.test_source_grants import URL,issue
    from tests.unit.test_source_snapshot import PREVIEW,SNAPS,BODY,SID
    app,client,root=grant_app
    assert (await client.post(URL,json=issue(root),headers={'Idempotency-Key':'grant'})).status_code==200
    preview=(await client.post(PREVIEW,json=BODY)).json()
    body={**BODY,'snapshot_id':SID,'confirmation_digest':preview['confirmation_digest'],'policy':'reject-secrets-utf8-v1'}
    assert (await client.post(SNAPS,json=body,headers={'Idempotency-Key':'legacy-capture'})).status_code==200
    store=app.service.projects.store;blob=store._snapshot_content(hashlib.sha256((root/'unchanged.txt').read_bytes()).hexdigest())
    await _private_replacement(client,store,root,blob,SNAPS,monkeypatch)


@pytest.mark.parametrize('orphan',[False,True])
async def test_private_capture_write_readback_never_reads_foreign_descriptor(grant_app,monkeypatch,orphan):
    import hashlib
    from tests.unit.test_source_grants import URL,issue
    from tests.unit.test_source_snapshot import PREVIEW,SNAPS,BODY,SID
    app,client,root=grant_app
    assert (await client.post(URL,json=issue(root),headers={'Idempotency-Key':'grant'})).status_code==200
    preview=(await client.post(PREVIEW,json=BODY)).json()
    body={**BODY,'snapshot_id':SID,'confirmation_digest':preview['confirmation_digest'],'policy':'reject-secrets-utf8-v1'}
    raw=(root/'unchanged.txt').read_bytes();store=app.service.projects.store;blob=store._snapshot_content(hashlib.sha256(raw).hexdigest())
    if orphan:blob.parent.mkdir(exist_ok=True);blob.write_bytes(raw)
    async def operation():return await client.post(SNAPS,json=body,headers={'Idempotency-Key':'capture'})
    await _private_replacement(client,store,root,blob,SNAPS,monkeypatch,operation=operation)


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind',['snapshot','manifest','input','append-readback'])
async def test_definition_descriptors_never_read_substituted_foreign_bytes(git_repo,monkeypatch,kind):
    from pyharness.application.workspace_owned_inputs import PrepareWorkspaceInput,derive_inputs
    from pyharness.application.workspace_store import canonical,digest_of
    from tests.unit.test_workspace_owned_inputs import MANIFESTS,CM
    app,client,root=git_repo;snapshot,cm=await context(client,root);store=app.service.projects.store;operation=None
    if kind=='snapshot':digest=snapshot['digest'];url=f'{P}/workspace-snapshots/{WS}'
    elif kind=='manifest':digest=cm['digest'];url=f'{MANIFESTS}/{CM}/revisions/1'
    elif kind=='input':
        receipt=(await client.post(INPUTS,json=command(cm),headers={'Idempotency-Key':'prepare'})).json()
        digest=receipt['digest'];url=INPUTS+'/'+WI
    else:
        resources,_=store._grant_state();cmd=PrepareWorkspaceInput.model_validate(command(cm))
        definition=derive_inputs(PID,cmd,resources[CM],resources[WS],'local-operator')
        digest=digest_of(canonical(definition.model_dump()));url=INPUTS
        async def operation():return await client.post(INPUTS,json=command(cm),headers={'Idempotency-Key':'prepare'})
    await _private_replacement(client,store,root,store._blob(digest),url,monkeypatch,operation=operation)


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind',['snapshot','manifest'])
async def test_explicit_private_file_read_checks_descriptor_after_access_gate(git_repo,monkeypatch,kind):
    from tests.unit.test_workspace_owned_inputs import MANIFESTS,CM
    app,client,root=git_repo;snapshot,_=await context(client,root);store=app.service.projects.store;armed=False
    method='_workspace_snapshot_access' if kind=='snapshot' else '_workspace_manifest_access';original=getattr(store,method)
    def access(*args,**kwargs):
        nonlocal armed
        value=original(*args,**kwargs);armed=True;return value
    monkeypatch.setattr(store,method,access)
    url=f'{P}/workspace-snapshots/{WS}/items/0/layers/working' if kind=='snapshot' else f'{MANIFESTS}/{CM}/revisions/1/items/0'
    blob=store._snapshot_content(snapshot['items'][0]['working']['digest'])
    await _private_replacement(client,store,root,blob,url,monkeypatch,enabled=lambda:armed)


@pytest.mark.controlled_process
async def test_partial_copy_failure_cleans_only_owned_temporary_tree(git_repo,monkeypatch):
    app,client,root=git_repo;_,cm=await context(client,root,is_dirty=True)
    from pyharness.application import workspace_materialization
    original=workspace_materialization.os.open;writes=0
    def opened(name,flags,*args,**kwargs):
        nonlocal writes
        if flags&os.O_WRONLY and 'input-staging' in Path(name).parts:
            writes+=1
            if writes==2:raise OSError('synthetic disk full')
        return original(name,flags,*args,**kwargs)
    monkeypatch.setattr(workspace_materialization.os,'open',opened)
    store=app.service.projects.store;before=store.log_path.read_bytes()
    response=await client.post(INPUTS,json=command(cm),headers={'Idempotency-Key':'partial'})
    assert response.status_code==503 and writes==2,response.text
    assert store.log_path.read_bytes()==before
    assert not list((store.root/'input-staging').iterdir())


@pytest.mark.controlled_process
@pytest.mark.parametrize('change',['digest','source_seq','project_revision','extra','schema'])
async def test_untrusted_preparation_references_never_commit(git_repo,change):
    app,client,root=git_repo;_,cm=await context(client,root);body=command(cm)
    if change=='digest':body['manifest_ref']['digest']='f'*64
    elif change=='source_seq':body['manifest_ref']['source_seq']+=1
    elif change=='project_revision':body['project_revision']+=1
    elif change=='extra':body['actor']='synthetic-forgery'
    else:body['schema_version']=True
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'forgery'})
    assert response.status_code in (409,422),response.text
    assert app.service.projects.store.log_path.read_bytes()==before


def test_closed_destination_fd_never_changes_foreign_link_mode(tmp_path,monkeypatch):
    import hashlib
    from pyharness.application.workspace_materialization import copy_inputs
    root=tmp_path/'owned-copy';root.mkdir();target=root/'file.txt';held=root/'held.txt'
    outside=tmp_path/'synthetic-readonly-outside';outside.write_bytes(b'synthetic outside');outside.chmod(0o400)
    mode=outside.stat().st_mode;original_fdopen=os.fdopen;mutated=False
    class Stream:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):return getattr(self.value,name)
        def __enter__(self):self.value.__enter__();return self
        def __exit__(self,*args):
            nonlocal mutated
            result=self.value.__exit__(*args)
            if not mutated and target.exists():os.replace(target,held);os.link(outside,target);mutated=True
            return result
    monkeypatch.setattr(os,'fdopen',lambda *a,**kw:Stream(original_fdopen(*a,**kw)))
    raw=b'synthetic approved';item={'path':'file.txt','input_index':0,'size_bytes':len(raw),'content_digest':hashlib.sha256(raw).hexdigest(),'native_mode':0o755,'applied_mode':0o600}
    error=None
    try:copy_inputs(root,[item],lambda _:raw,lambda:None)
    except PyHError as exc:error=exc
    assert mutated and outside.stat().st_mode==mode,'path chmod changed the substituted external inode'
    assert outside.read_bytes()==b'synthetic outside' and error is not None


@pytest.mark.controlled_process
@pytest.mark.parametrize('limit',['history','definition'])
async def test_preparation_quota_rejects_before_copy_or_commit(git_repo,monkeypatch,limit):
    app,client,root=git_repo;_,cm=await context(client,root);store=app.service.projects.store
    if limit=='history':
        assert (await client.post(INPUTS,json=command(cm),headers={'Idempotency-Key':'first'})).status_code==200
        monkeypatch.setattr(store,'MAX_CONTEXT_HISTORY',1)
    else:monkeypatch.setattr(store,'MAX_CONTEXT_BYTES',512)
    from pyharness.application import workspace_owned_input_store
    copies=[]
    def copy(*args):copies.append(True);raise AssertionError('quota must be checked before copying')
    monkeypatch.setattr(workspace_owned_input_store,'copy_inputs',copy)
    before=store.log_path.read_bytes()
    response=await client.post(INPUTS,json=command(cm,input_id='wi-2222222222222222'),headers={'Idempotency-Key':'bounded'})
    assert response.status_code==422 and response.json()['code']=='EVT-100',response.text
    assert copies==[] and store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_cancelled_preparation_cleans_owned_copy_without_commit(git_repo,monkeypatch):
    import asyncio
    from pyharness.application import workspace_owned_input_store
    app,client,root=git_repo;_,cm=await context(client,root);store=app.service.projects.store
    original=workspace_owned_input_store.copy_inputs;targets=[]
    def copy(*args):
        original(*args);targets.append(Path(args[0]));raise asyncio.CancelledError('synthetic caller cancelled')
    monkeypatch.setattr(workspace_owned_input_store,'copy_inputs',copy)
    before=store.log_path.read_bytes()
    # ASGI middleware owns disconnect handling; inject cancellation at the actual
    # preparation service seam instead of fabricating a network disconnect.
    from pyharness.application.workspace_owned_inputs import PrepareWorkspaceInput
    with pytest.raises(asyncio.CancelledError):await store.prepare_workspace_input(PID,PrepareWorkspaceInput.model_validate(command(cm)),command_id='cancelled',authorize=lambda:'local-operator')
    assert targets and all(not p.exists() for p in targets)
    assert store.log_path.read_bytes()==before and not list((store.root/'input-staging').iterdir())
