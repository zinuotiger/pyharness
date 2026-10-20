"""Explicit subsets coexist; schema and source lineage are never inferred from Git."""
import hashlib,os
from datetime import datetime,timezone
import pytest
from tests.unit.test_selected_owned_inputs import grant_app,selected_manifest,DESCRIPTOR,INPUTS,WI,PID,command,RAW
from tests.unit.test_context_manifests import MANIFESTS,manifest_command,save
from tests.unit.test_source_snapshot import PREVIEW,SNAPS,BODY,SID,command_for
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_owned_inputs import context


async def test_selected_subsets_coexist_without_latest_git_snapshot_rule(grant_app):
    app,client,source=grant_app;cm=await selected_manifest(client,source)
    old=await client.post(INPUTS,json=command(cm,schema_version=2),headers={'Idempotency-Key':'first'});assert old.status_code==200
    selection={**BODY,'paths':['unselected.txt']};p=await client.post(PREVIEW,json=selection);assert p.status_code==200
    s=await client.post(SNAPS,json={**selection,'snapshot_id':'ss-bbbbbbbbbbbbbbbb','confirmation_digest':p.json()['confirmation_digest'],'policy':'reject-secrets-utf8-v1'},headers={'Idempotency-Key':'other-subset'});assert s.status_code==200,s.text
    new_cm=await save(client,MANIFESTS,manifest_command({'snapshot_digest':s.json()['digest']},snapshot_id=s.json()['snapshot_id'],manifest_id='cm-bbbbbbbbbbbbbbbb'),'other-cm');assert new_cm.status_code==200,new_cm.text
    new=await client.post(INPUTS,json=command(new_cm.json(),schema_version=2,input_id='wi-bbbbbbbbbbbbbbbb'),headers={'Idempotency-Key':'second'});assert new.status_code==200,new.text
    assert (await client.get(INPUTS+'/'+WI)).json()==old.json()
    assert (await client.get(DESCRIPTOR)).status_code==200
    page=(await client.get(INPUTS+'?limit=1')).json();assert page['total']==2 and page['has_more']
    second=(await client.get(INPUTS+'?limit=1&cursor='+page['next_cursor'])).json()
    assert second['view_digest']==page['view_digest'] and len({page['inputs'][0]['input_id'],second['inputs'][0]['input_id']})==2


@pytest.mark.controlled_process
async def test_mixed_input_schemas_share_stable_bounded_list_but_not_manifest_kind(git_repo):
    app,client,source=git_repo;_,git_cm=await context(client,source)
    git_input=await client.post(INPUTS,json=command(git_cm),headers={'Idempotency-Key':'git-input'});assert git_input.status_code==200,git_input.text
    before=app.service.projects.store.log_path.read_bytes()
    wrong=await client.post(INPUTS,json=command(git_cm,schema_version=2,input_id='wi-2222222222222222'),headers={'Idempotency-Key':'wrong-kind'})
    assert wrong.status_code==403 and app.service.projects.store.log_path.read_bytes()==before
    capture=await client.post(SNAPS,json=await command_for(client),headers={'Idempotency-Key':'selected-capture'});assert capture.status_code==200,capture.text
    cm=await save(client,MANIFESTS,manifest_command({'snapshot_id':SID,'snapshot_digest':capture.json()['digest']},manifest_id='cm-bbbbbbbbbbbbbbbb'),'selected-cm');assert cm.status_code==200,cm.text
    selected=await client.post(INPUTS,json=command(cm.json(),schema_version=2,input_id='wi-2222222222222222'),headers={'Idempotency-Key':'selected-input'});assert selected.status_code==200,selected.text
    page=(await client.get(INPUTS+'?limit=1')).json();tail=(await client.get(INPUTS+'?limit=1&cursor='+page['next_cursor'])).json()
    assert page['total']==2 and page['view_digest']==tail['view_digest'] and {page['inputs'][0]['schema_version'],tail['inputs'][0]['schema_version']}=={1,2}
    assert (await client.get(INPUTS+'/'+WI)).json()==git_input.json()


async def test_selected_expiry_on_actual_private_body_read_cannot_commit(grant_app,monkeypatch):
    from pyharness.application import source_snapshot,snapshot_store
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store
    original=source_snapshot.checked_private_bytes;blob=store._snapshot_content(hashlib.sha256(RAW).hexdigest());expired=False;reads=0
    class Clock:
        @classmethod
        def now(cls,tz):return datetime(2100 if expired else 2026,1,1,tzinfo=timezone.utc)
    def read(path,**kwargs):
        nonlocal expired,reads
        raw=original(path,**kwargs)
        if path==blob and kwargs.get('before_read') is not None:reads+=1;expired=True
        return raw
    monkeypatch.setattr(snapshot_store,'datetime',Clock);monkeypatch.setattr(source_snapshot,'checked_private_bytes',read)
    before=store.log_path.read_bytes();r=await client.post(INPUTS,json=command(cm,schema_version=2),headers={'Idempotency-Key':'expiry-body'})
    assert reads==1 and expired and r.status_code==403,r.text
    assert store.log_path.read_bytes()==before and not list((store.root/'input-staging').iterdir())


@pytest.mark.parametrize('operation',['descriptor','prepare'])
async def test_selected_private_fd_replacement_never_reads_foreign(grant_app,monkeypatch,operation):
    from tests.unit.test_workspace_input_boundaries import _private_replacement
    app,client,source=grant_app;cm=await selected_manifest(client,source);store=app.service.projects.store
    async def prepare():return await client.post(INPUTS,json=command(cm,schema_version=2),headers={'Idempotency-Key':'link'})
    await _private_replacement(client,store,source,store._snapshot_content(hashlib.sha256(RAW).hexdigest()),DESCRIPTOR,monkeypatch,operation=prepare if operation=='prepare' else None)


@pytest.mark.parametrize('field',['digest','source_seq','revision'])
async def test_selected_forged_manifest_pin_never_copies_or_commits(grant_app,monkeypatch,field):
    from pyharness.application import workspace_owned_input_store
    app,client,source=grant_app;cm=await selected_manifest(client,source);body=command(cm,schema_version=2);store=app.service.projects.store
    body['manifest_ref'][field]='0'*64 if field=='digest' else body['manifest_ref'][field]+1
    def forbidden(*a):raise AssertionError('forged pin started materialization')
    monkeypatch.setattr(workspace_owned_input_store,'copy_inputs',forbidden);log=store.log_path.read_bytes()
    r=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'forged-pin'})
    assert r.status_code==409 and store.log_path.read_bytes()==log,r.text
