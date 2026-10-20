"""Real account middleware with synthetic users; private inputs stay owner/project scoped."""
import os
from pathlib import Path
import pytest
from tests.unit.test_source_grants import grant_app,PID
from tests.unit.test_context_manifest_permissions import context_team,join
from tests.unit.test_workspace_snapshot_permissions import workspace_team
from tests.unit.test_workspace_snapshots import capture,manifest,MANIFESTS
from tests.unit.test_workspace_owned_inputs import command,INPUTS,WI


async def owner_input(client):
    snapshot,_=await capture(client,key='input-workspace-capture')
    cm_response=await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})
    assert cm_response.status_code==200,cm_response.text
    body=command(cm_response.json());response=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})
    assert response.status_code==200,response.text
    return body,response.json()


@pytest.mark.controlled_process
async def test_other_tenant_and_team_member_cannot_read_owner_input(workspace_team):
    _,owner,member,accounts,_,service=workspace_team;body,_=await owner_input(owner)
    assert (await member.get(INPUTS+'/'+WI)).status_code==403
    await join(member,accounts)
    assert (await member.get(INPUTS+'/'+WI)).status_code==403
    page=(await member.get(INPUTS)).json();assert page['total']==0 and page['inputs']==[]
    before=service.projects.store.log_path.read_bytes()
    assert (await member.post(INPUTS,json={**body,'input_id':'wi-2222222222222222'},headers={'Idempotency-Key':'forged-owner'})).status_code==403
    assert service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('role',['viewer','remove'])
async def test_role_loss_denies_input_reads_lists_and_original_receipt(workspace_team,role):
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts);body,_=await owner_input(member)
    accounts.membership('context-team','ctx-member',role);before=service.projects.store.log_path.read_bytes()
    for path in [INPUTS,INPUTS+'/'+WI]:assert (await member.get(path)).status_code in (401,403)
    assert (await member.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})).status_code in (401,403)
    assert service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_role_revoked_after_commit_returns_unknown_and_reopens_exact(workspace_team,monkeypatch):
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    snapshot,_=await capture(member)
    cm=(await member.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})).json();body=command(cm)
    store=service.projects.store;original=store._verify_commit
    def verify(event_store,event):
        original(event_store,event)
        if event.payload.get('resource_kind')=='workspace_input':accounts.membership('context-team','ctx-member','viewer')
    monkeypatch.setattr(store,'_verify_commit',verify)
    response=await member.post(INPUTS,json=body,headers={'Idempotency-Key':'unknown'})
    assert response.status_code==503 and 'files' not in response.json(),response.text
    log=store.log_path.read_bytes();monkeypatch.undo();accounts.membership('context-team','ctx-member','member')
    reconciled=await member.get(INPUTS+'/'+WI);assert reconciled.status_code==200
    assert (await member.post(INPUTS,json=body,headers={'Idempotency-Key':'unknown'})).json()==reconciled.json()
    assert store.log_path.read_bytes()==log


@pytest.mark.controlled_process
@pytest.mark.parametrize('duplicate',[False,True])
async def test_summary_root_io_role_loss_never_returns_preparation_receipt(workspace_team,monkeypatch,duplicate):
    _,_,member,accounts,_,service=workspace_team;await join(member,accounts)
    snapshot,_=await capture(member)
    cm=(await member.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})).json();body=command(cm)
    if duplicate:assert (await member.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})).status_code==200
    store=service.projects.store;before=store.log_path.read_bytes();original=store._input_summary;triggered=False
    def summary(resources,row,**kwargs):
        nonlocal triggered
        result=original(resources,row,**kwargs)
        # Ignore the prospective receipt budget calculation; arm only a committed fact.
        if any(r.get('workspace_input',{}).get('input_id')==WI for r in resources.values()):
            accounts.membership('context-team','ctx-member','viewer');triggered=True
        return result
    monkeypatch.setattr(store,'_input_summary',summary)
    response=await member.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})
    assert triggered and response.status_code==(403 if duplicate else 503),response.text
    assert 'files' not in response.json()
    after=store.log_path.read_bytes();assert (after==before)==duplicate
    monkeypatch.undo();accounts.membership('context-team','ctx-member','member')
    assert (await member.get(INPUTS+'/'+WI)).status_code==200
    assert store.log_path.read_bytes()==after
