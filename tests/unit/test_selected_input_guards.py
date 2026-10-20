"""Synthetic selected-source authority, persistence and failure boundaries."""
import asyncio,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import pytest
from pyharness.application.workspace_store import WorkspaceStore
from pyharness.errors import PyHError
from tests.unit.test_selected_owned_inputs import grant_app,selected_manifest,DESCRIPTOR,INPUTS,WI,PID,GID,RAW,command
from tests.unit.test_context_manifests import MANIFESTS,MID,manifest_command,save,task_command,TASKS
from tests.unit.test_context_manifest_permissions import context_team,join


async def prepared(client,source):
    cm=await selected_manifest(client,source);body=command(cm,schema_version=2)
    r=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'prepared'});assert r.status_code==200,r.text
    return cm,body,r.json()


@pytest.mark.parametrize('operation',['descriptor','prepare','get','list'])
async def test_selected_root_metadata_cross_expiry_returns_no_receipt_or_commit(grant_app,monkeypatch,operation):
    from pyharness.application import snapshot_store
    app,client,source=grant_app;cm,body,_=await prepared(client,source);store=app.service.projects.store;before=store.log_path.read_bytes()
    expired=False;original=snapshot_store.check_root_metadata
    class Clock:
        @classmethod
        def now(cls,tz):return datetime(2100 if expired else 2026,1,1,tzinfo=timezone.utc)
    def check(row):
        nonlocal expired
        result=original(row);expired=True;return result
    monkeypatch.setattr(snapshot_store,'datetime',Clock);monkeypatch.setattr(snapshot_store,'check_root_metadata',check)
    if operation=='prepare':r=await client.post(INPUTS,json={**body,'input_id':'wi-2222222222222222'},headers={'Idempotency-Key':'expired'})
    else:r=await client.get({'descriptor':DESCRIPTOR,'get':INPUTS+'/'+WI,'list':INPUTS}[operation])
    assert expired and r.status_code==(200 if operation=='list' else 403),r.text
    if operation=='list':assert r.json()['inputs']==[] and r.json()['total']==0
    assert not {'files','descriptor_digest','receipt_digest'}&set(r.json()) and store.log_path.read_bytes()==before
    # Future expiry must not invalidate the committed event history itself.
    resources,_=store._grant_state();assert resources[WI]['workspace_input']['schema_version']==2


@pytest.mark.parametrize('fault',['before-append','after-append'])
async def test_selected_unknown_reopen_preserves_original_idempotency(grant_app,monkeypatch,fault):
    app,client,source=grant_app;cm=await selected_manifest(client,source);body=command(cm,schema_version=2);store=app.service.projects.store;before=store.log_path.read_bytes()
    if fault=='before-append':
        async def append(*a,**k):raise OSError('synthetic lost append')
        monkeypatch.setattr(store._session,'append',append)
    else:
        def verify(*a,**k):raise OSError('synthetic lost receipt')
        monkeypatch.setattr(store,'_verify_commit',verify)
    r=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'original'});assert r.status_code==503 and store._failed,r.text
    assert (store.log_path.read_bytes()==before)==(fault=='before-append') and not list((store.root/'input-staging').iterdir())
    await store.close();monkeypatch.undo();app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    assert (await client.get(INPUTS+'/'+WI)).status_code==(403 if fault=='before-append' else 200)
    retry=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'original'});assert retry.status_code==200,retry.text
    log=app.service.projects.store.log_path.read_bytes()
    assert sum(json.loads(l).get('payload',{}).get('resource_kind')=='workspace_input' for l in log.splitlines())==1
    assert (await client.post(INPUTS,json=body,headers={'Idempotency-Key':'original'})).json()==retry.json()
    conflict=await client.post(INPUTS,json={**body,'input_id':'wi-2222222222222222'},headers={'Idempotency-Key':'original'})
    assert conflict.status_code==409 and conflict.json()['code']=='EVT-100'
    assert app.service.projects.store.log_path.read_bytes()==log


async def test_selected_manifest_superseded_is_stale_reopenable_not_executable(grant_app):
    app,client,source=grant_app;cm,body,receipt=await prepared(client,source);store=app.service.projects.store
    snapshot={'snapshot_id':cm['snapshot_id'],'snapshot_digest':cm['snapshot_digest']}
    updated=await save(client,MANIFESTS,manifest_command(snapshot,expected_revision=1),'manifest-r2');assert updated.status_code==200,updated.text
    old=(await client.get(INPUTS+'/'+WI)).json();assert old['status']=='stale' and old['files']==old['deletions']==[] and old['stale_reasons']==['manifest_revision_superseded']
    assert (await client.get(DESCRIPTOR)).status_code==409
    with pytest.raises(PyHError):
        async with store.workspace_input_transfer(PID,WI,authorize=lambda:'local-operator'):raise AssertionError('stale transfer yielded')
    log=store.log_path.read_bytes();await store.close();app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    assert (await client.get(INPUTS+'/'+WI)).json()==old
    assert (await client.post(INPUTS,json=body,headers={'Idempotency-Key':'prepared'})).json()==old
    assert old['digest']==receipt['digest'] and app.service.projects.store.log_path.read_bytes()==log


async def test_selected_private_manifest_and_shared_authorized_snapshot(context_team):
    app,owner,member,accounts,snapshot=context_team
    cm=(await save(owner,MANIFESTS,manifest_command(snapshot),'owner-cm')).json();body=command(cm,schema_version=2)
    assert (await member.get(DESCRIPTOR)).status_code==403
    await join(member,accounts)
    assert (await member.get(DESCRIPTOR)).status_code==403
    assert (await member.post(INPUTS,json=body,headers={'Idempotency-Key':'steal'})).status_code==403
    # The existing SourceGrant permits team reading; the member creates its own CM.
    own=await save(member,MANIFESTS,manifest_command(snapshot,manifest_id='cm-bbbbbbbbbbbbbbbb'),'member-cm');assert own.status_code==200,own.text
    path=DESCRIPTOR.replace(MID,'cm-bbbbbbbbbbbbbbbb');assert (await member.get(path)).status_code==200
    r=await member.post(INPUTS,json=command(own.json(),schema_version=2),headers={'Idempotency-Key':'own'});assert r.status_code==200,r.text
    assert (await owner.get(INPUTS+'/'+WI)).status_code==403
    store=app._service_registry.get('context-team').projects.store;log=store.log_path.read_bytes();accounts.membership('context-team','ctx-member','viewer')
    for url in (path,INPUTS+'/'+WI,INPUTS):assert (await member.get(url)).status_code==403
    assert store.log_path.read_bytes()==log


@pytest.mark.parametrize('operation',['descriptor','prepare','get'])
async def test_selected_root_io_final_role_loss(context_team,monkeypatch,operation):
    from pyharness.application import snapshot_store
    app,owner,member,accounts,snapshot=context_team;await join(member,accounts)
    cm=(await save(member,MANIFESTS,manifest_command(snapshot),'member-cm')).json();body=command(cm,schema_version=2)
    if operation=='get':assert (await member.post(INPUTS,json=body,headers={'Idempotency-Key':'first'})).status_code==200
    store=app._service_registry.get('context-team').projects.store;log=store.log_path.read_bytes();original=snapshot_store.check_root_metadata;removed=False
    def check(row):
        nonlocal removed
        result=original(row);accounts.membership('context-team','ctx-member','remove');removed=True;return result
    monkeypatch.setattr(snapshot_store,'check_root_metadata',check)
    r=await member.post(INPUTS,json=body,headers={'Idempotency-Key':'revoked'}) if operation=='prepare' else await member.get(DESCRIPTOR if operation=='descriptor' else INPUTS+'/'+WI)
    assert removed and r.status_code==403,r.text
    assert 'files' not in r.json() and store.log_path.read_bytes()==log


@pytest.mark.parametrize('limit',['history','definition'])
async def test_selected_limits_before_copy(grant_app,monkeypatch,limit):
    from pyharness.application import workspace_owned_input_store
    app,client,source=grant_app;cm,body,_=await prepared(client,source);store=app.service.projects.store
    monkeypatch.setattr(store,'MAX_CONTEXT_HISTORY' if limit=='history' else 'MAX_CONTEXT_BYTES',1 if limit=='history' else 512)
    copies=[]
    def copy(*args):copies.append(True);raise AssertionError('limit must precede copy')
    monkeypatch.setattr(workspace_owned_input_store,'copy_inputs',copy);log=store.log_path.read_bytes()
    r=await client.post(INPUTS,json={**body,'input_id':'wi-2222222222222222'},headers={'Idempotency-Key':'limited'})
    assert r.status_code==422 and copies==[] and store.log_path.read_bytes()==log,r.text


async def test_selected_cancelled_copy_cleans_only_own_temp(grant_app,monkeypatch):
    from pyharness.application import workspace_owned_input_store
    from pyharness.application.selected_owned_inputs import PrepareSelectedInput
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store;original=workspace_owned_input_store.copy_inputs;targets=[]
    def copy(*args):original(*args);targets.append(Path(args[0]));raise asyncio.CancelledError('synthetic cancellation')
    monkeypatch.setattr(workspace_owned_input_store,'copy_inputs',copy);before=store.log_path.read_bytes()
    with pytest.raises(asyncio.CancelledError):await store.prepare_workspace_input(PID,PrepareSelectedInput.model_validate(command(cm,schema_version=2)),command_id='cancel',authorize=lambda:'local-operator')
    assert targets and all(not p.exists() for p in targets) and store.log_path.read_bytes()==before
    assert (source/'unselected.txt').read_bytes()==b'synthetic unselected'


@pytest.mark.parametrize('kind',['content','definition'])
async def test_selected_corruption_blocks_reopen_without_new_facts(grant_app,kind):
    app,client,source=grant_app;cm,_,receipt=await prepared(client,source);store=app.service.projects.store
    blob=store._snapshot_content(hashlib.sha256(RAW).hexdigest()) if kind=='content' else store._blob(receipt['digest'])
    blob.write_bytes(b'synthetic corrupted');log=store.log_path.read_bytes();await store.close();app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    r=await client.get(INPUTS+'/'+WI);assert r.status_code==503 and 'files' not in r.json(),r.text
    assert app.service.projects.store.log_path.read_bytes()==log


async def test_selected_descriptor_budget_tail_and_owner_lock(grant_app,monkeypatch):
    from pyharness.application import selected_input_support
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store
    other=WorkspaceStore(store.root,tenant_id='default')
    with pytest.raises(PyHError) as error:await other._open(initialize=False)
    assert error.value.ctx['reason']=='workspace_busy';await other.close()
    clock=[100.];original=selected_input_support.SelectedInputDescriptor.model_dump
    monkeypatch.setattr(selected_input_support.time,'monotonic',lambda:clock[0])
    def dump(self,*a,**k):result=original(self,*a,**k);clock[0]+=store.MAX_WORKSPACE_CAPTURE_SECONDS;return result
    monkeypatch.setattr(selected_input_support.SelectedInputDescriptor,'model_dump',dump);log=store.log_path.read_bytes()
    r=await client.get(DESCRIPTOR);assert r.status_code==409 and r.json()['code']=='EVT-104',r.text
    assert store.log_path.read_bytes()==log
