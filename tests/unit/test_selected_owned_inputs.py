"""Given immutable non-Git selected files, prepare only exact private CMv1 inputs."""
import hashlib,json
import pytest
from tests.unit.test_source_grants import grant_app,PID,GID
from tests.unit.test_snapshot_context_preview import captured
from tests.unit.test_context_manifests import MANIFESTS,MID,manifest_command,save
from tests.unit.test_workspace_owned_inputs import INPUTS,WI,command


DESCRIPTOR=f'{MANIFESTS}/{MID}/revisions/1/input-descriptor'
RAW=b'\xef\xbb\xbfsynthetic selected\r\n'


async def selected_manifest(client,source):
    assert not (source/'.git').exists()
    (source/'unchanged.txt').write_bytes(RAW)
    (source/'unselected.txt').write_bytes(b'synthetic unselected')
    snapshot=await captured(client,source)
    response=await save(client,MANIFESTS,manifest_command(snapshot),'manifest')
    assert response.status_code==200,response.text
    return response.json()


async def test_selected_input_descriptor_is_exact_readonly_and_has_no_git_fields(grant_app,monkeypatch):
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store
    from pyharness.application import source_snapshot
    from pyharness.application.workspace_store import canonical,digest_of
    original=source_snapshot.read_selected
    def read(root,*args,**kwargs):
        assert root.resolve()!=source.resolve(),'descriptor read live source body'
        return original(root,*args,**kwargs)
    monkeypatch.setattr(source_snapshot,'read_selected',read)
    before=store.log_path.read_bytes();response=await client.get(DESCRIPTOR)
    assert response.status_code==200,response.text
    value=response.json();assert value['status']=='selected_inputs_observed' and value['persisted'] is False and value['executable'] is False
    assert value['manifest_ref']=={k:cm[k] for k in ['manifest_id','revision','digest','source_seq']}
    assert value['source_ref']['source_kind']=='source_snapshot' and value['source_ref']['snapshot_id']==cm['snapshot_id']
    assert value['source_ref']['digest']==cm['snapshot_digest'] and value['source_ref']['source_seq']==cm['snapshot_source_seq']
    assert value['source_ref']['grant_id']==GID and not {'head_oid','index_digest','metadata_grant_id','captured_project_revision'}&set(value['source_ref'])
    assert value['policy']=='private-selected-copy-v1' and value['deletions']==[] and value['deletion_count']==0
    assert [{k:v for k,v in row.items() if k not in ['native_mode','applied_mode']} for row in value['files']]==cm['items']
    assert all(row['applied_mode']==0o600 for row in value['files'])
    raw={k:v for k,v in value.items() if k!='descriptor_digest'};assert digest_of(canonical(raw))==value['descriptor_digest']
    assert store.log_path.read_bytes()==before and not app.service._engines and not app.service._queues


async def test_selected_preparation_uses_committed_bytes_after_host_edit_and_reopens(grant_app,monkeypatch):
    from pyharness.application.workspace_store import WorkspaceStore,canonical,digest_of
    from pyharness.application import source_snapshot
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store
    original=source_snapshot.read_selected
    def read(root,*args,**kwargs):
        assert root.resolve()!=source.resolve(),'selected preparation fell back to live host'
        return original(root,*args,**kwargs)
    monkeypatch.setattr(source_snapshot,'read_selected',read)
    (source/'unchanged.txt').write_bytes(b'synthetic later live bytes')
    body=command(cm,schema_version=2);before=store.log_path.read_bytes()
    response=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'selected-prepare'})
    assert response.status_code==200,response.text
    value=response.json();assert value['schema_version']==2 and value['policy']=='private-selected-copy-v1'
    assert value['source_ref']['source_kind']=='source_snapshot' and value['manifest_ref']==body['manifest_ref']
    assert value['files'][0]['content_digest']==hashlib.sha256(RAW).hexdigest() and value['deletions']==[]
    assert [i['path'] for i in value['files']]==['unchanged.txt'] and value['workspace_state']=='not_started' and value['executable'] is False
    assert value['materialization_lifetime']=='per_explicit_use' and not list((store.root/'input-staging').iterdir())
    raw={k:v for k,v in value.items() if k!='receipt_digest'};assert digest_of(canonical(raw))==value['receipt_digest']
    log=store.log_path.read_bytes();assert log.startswith(before) and log!=before
    events=[json.loads(line) for line in log.splitlines()];commits=[e for e in events if e.get('payload',{}).get('resource_kind')=='workspace_input']
    assert len(commits)==1 and commits[0]['payload']['schema_version']==1
    assert (await client.get(INPUTS+'/'+WI)).json()==value
    assert (await client.post(INPUTS,json=body,headers={'Idempotency-Key':'selected-prepare'})).json()==value
    await store.close();app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    assert (await client.get(INPUTS+'/'+WI)).json()==value
    assert app.service.projects.store.log_path.read_bytes()==log and not app.service._engines and not app.service._queues


async def test_selected_transfer_is_exact_private_copy_and_never_runs_a_command(grant_app,tmp_path):
    from pyharness.core.sandbox import SandboxManager
    from pyharness.core.sandbox_models import SandboxProfile
    from tests.unit.test_workspace_input_transfer import SyntheticBackend
    app,client,source=grant_app;cm=await selected_manifest(client,source)
    response=await client.post(INPUTS,json=command(cm,schema_version=2),headers={'Idempotency-Key':'selected-prepare'})
    assert response.status_code==200,response.text
    (source/'unchanged.txt').write_bytes(b'synthetic later host edit')
    backend=SyntheticBackend();manager=SandboxManager(tmp_path/'owned-sandboxes',docker=backend);store=app.service.projects.store;log=store.log_path.read_bytes()
    async with store.workspace_input_transfer(PID,WI,authorize=lambda:'local-operator') as transfer:
        record=await manager.create('synthetic-selected-run',SandboxProfile(mode='isolated',workspace_mode='empty'),prepare_inputs=transfer)
        root=manager.workspace(record.sandbox_id)
        assert {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}=={'unchanged.txt'}
        assert (root/'unchanged.txt').read_bytes()==RAW
    await manager.destroy(record.sandbox_id)
    assert len(backend.created)==len(backend.destroyed)==1 and backend.executions==0 and not root.exists()
    assert store.log_path.read_bytes()==log


async def test_original_git_input_schema_still_refuses_plain_manifest(grant_app):
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store;before=store.log_path.read_bytes()
    response=await client.post(INPUTS,json=command(cm),headers={'Idempotency-Key':'wrong-source-kind'})
    assert response.status_code==403 and store.log_path.read_bytes()==before


@pytest.mark.parametrize('extra',[{'destination':'C:/synthetic-outside'},{'source_ref':{'head_oid':'a'*40}},{'actor':'forged'},{'schema_version':True}])
async def test_selected_command_never_accepts_forged_authority_or_destination(grant_app,extra):
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store;before=store.log_path.read_bytes()
    response=await client.post(INPUTS,json={**command(cm,schema_version=2),**extra},headers={'Idempotency-Key':'forged'})
    assert response.status_code==422 and store.log_path.read_bytes()==before
