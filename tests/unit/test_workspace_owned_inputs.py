"""Given committed synthetic inputs, when preparing, then only exact private working bytes."""
import hashlib,json
import pytest
from tests.unit.test_source_grants import grant_app,PID,GID,URL,issue
from tests.unit.test_workspace_git_preview import git_repo,git,dirty
from tests.unit.test_workspace_snapshots import setup,capture,selection,manifest,MANIFESTS,WS,CM,P

WI='wi-0123456789abcdef'
INPUTS=P+'/workspace-inputs'


async def context(client,root,*,is_dirty=False):
    if is_dirty:dirty(root)
    await setup(client,root)
    snapshot,_=await capture(client,selection(paths=['unchanged.txt','delete.txt','new.txt','.ai-coding/RULES.md']) if is_dirty else selection())
    response=await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})
    assert response.status_code==200,response.text
    return snapshot,response.json()


def command(cm,**changes):
    return {'schema_version':1,'input_id':WI,'project_revision':cm['project_revision'],
        'manifest_ref':{k:cm[k] for k in ['manifest_id','revision','digest','source_seq']},**changes}


@pytest.mark.controlled_process
async def test_preparation_persists_exact_manifest_and_private_working_inputs(git_repo,monkeypatch):
    app,client,root=git_repo;snapshot,cm=await context(client,root,is_dirty=True)
    (root/'unselected.txt').write_bytes(b'synthetic never selected')
    (root/'unchanged.txt').write_bytes(b'synthetic later host dirty content')
    from pyharness.application import source_snapshot
    original=source_snapshot.read_selected
    def read(private,name,**kwargs):
        assert private.resolve()!=root.resolve(),'preparation fell back to host content'
        return original(private,name,**kwargs)
    monkeypatch.setattr(source_snapshot,'read_selected',read)
    before=app.service.projects.store.log_path.read_bytes()
    body=command(cm);response=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='input_prepared' and result['workspace_state']=='not_started'
    assert result['executable'] is False and result['manifest_ref']==body['manifest_ref'] and result['source_ref']==cm['source_ref']
    assert [i['path'] for i in result['files']]==['unchanged.txt','new.txt','.ai-coding/RULES.md']
    assert result['deletions']==[{'input_index':1,'path':'delete.txt'}]
    assert result['files'][0]['content_digest']==hashlib.sha256(b'\xef\xbb\xbfworking\r\n').hexdigest()
    after=app.service.projects.store.log_path.read_bytes();assert after.startswith(before) and after!=before
    assert (await client.get(INPUTS+'/'+WI)).json()==result
    repeated=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})
    assert repeated.status_code==200 and repeated.json()==result
    assert app.service.projects.store.log_path.read_bytes()==after
    assert not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
async def test_preparation_never_accepts_client_paths_or_destination(git_repo):
    app,client,root=git_repo;_,cm=await context(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(INPUTS,json=command(cm,paths=['../synthetic-outside'],destination='C:/synthetic-outside'),headers={'Idempotency-Key':'bad'})
    assert response.status_code==422,response.text
    assert app.service.projects.store.log_path.read_bytes()==before


def test_input_commit_envelope_is_typed_and_immutable():
    from pyharness.events.payload import WorkspaceResourceCommittedPayload
    from pydantic import ValidationError
    body={'resource_kind':'workspace_input','resource_id':WI,'schema_version':1,'revision':1,'parent_revision':0,
          'blob_ref':'a'*64,'digest':'a'*64,'command_id':'synthetic-prep','request_digest':'b'*64,'actor_ref':'synthetic-owner'}
    assert WorkspaceResourceCommittedPayload.model_validate(body).resource_id==WI
    for changes in [{'resource_id':'ws-0123456789abcdef'},{'revision':2,'parent_revision':1}]:
        with pytest.raises(ValidationError):WorkspaceResourceCommittedPayload.model_validate({**body,**changes})
