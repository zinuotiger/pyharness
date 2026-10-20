"""Given prepared input facts, when interrupted or superseded, then replay exact and never execute."""
import json
import pytest
from pyharness.application.workspace_store import WorkspaceStore
from pyharness.errors import PyHError
from tests.unit.test_source_grants import grant_app,PID
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_owned_inputs import context,command,INPUTS,WI,MANIFESTS,CM


@pytest.mark.controlled_process
@pytest.mark.parametrize('fault',['before-append','after-append'])
async def test_unknown_preparation_reopens_original_key_body_exactly(git_repo,monkeypatch,fault):
    app,client,root=git_repo;_,cm=await context(client,root);body=command(cm)
    store=app.service.projects.store;before=store.log_path.read_bytes()
    if fault=='before-append':
        async def append(*args,**kwargs):raise OSError('synthetic lost append')
        monkeypatch.setattr(store._session,'append',append)
    else:
        def verify(*args):raise OSError('synthetic lost readback')
        monkeypatch.setattr(store,'_verify_commit',verify)
    response=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'original'})
    assert response.status_code==503 and store._failed,response.text
    assert (store.log_path.read_bytes()==before)==(fault=='before-append')
    assert not list((store.root/'input-staging').iterdir())
    await store.close();monkeypatch.undo();app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    reopened=await client.get(INPUTS+'/'+WI)
    assert reopened.status_code==(403 if fault=='before-append' else 200)
    # This is an explicit original request, never an automatic recovery replay.
    retried=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'original'})
    assert retried.status_code==200,retried.text
    log=app.service.projects.store.log_path.read_bytes();events=[json.loads(line) for line in log.splitlines()]
    assert sum(e.get('payload',{}).get('resource_kind')=='workspace_input' for e in events)==1
    assert (await client.post(INPUTS,json=body,headers={'Idempotency-Key':'original'})).json()==retried.json()
    assert app.service.projects.store.log_path.read_bytes()==log and not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
async def test_superseded_manifest_stales_input_without_corrupting_replay(git_repo):
    app,client,root=git_repo;_,cm=await context(client,root);body=command(cm)
    receipt=(await client.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})).json()
    updated={'schema_version':2,'manifest_id':CM,'expected_revision':1,'project_revision':1,
        'source_ref':{k:cm['source_ref'][k] for k in ('snapshot_id','revision','digest','source_seq')},'task_ref':None}
    response=await client.post(MANIFESTS,json=updated,headers={'Idempotency-Key':'manifest-r2'})
    assert response.status_code==200,response.text
    old=(await client.get(INPUTS+'/'+WI)).json()
    assert old['status']=='stale' and old['files']==[] and old['deletions']==[] and 'manifest_revision_superseded' in old['stale_reasons']
    store=app.service.projects.store;log=store.log_path.read_bytes()
    with pytest.raises(PyHError):
        async with store.workspace_input_transfer(PID,WI,authorize=lambda:'local-operator'):raise AssertionError('stale transfer yielded')
    await store.close();app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    assert (await client.get(INPUTS+'/'+WI)).json()==old
    duplicate=await client.post(INPUTS,json=body,headers={'Idempotency-Key':'prepare'})
    assert duplicate.status_code==200 and duplicate.json()==old
    assert app.service.projects.store.log_path.read_bytes()==log
    assert receipt['digest']==old['digest'] and receipt['source_seq']==old['source_seq']


@pytest.mark.controlled_process
async def test_empty_input_page_final_budget_is_enforced(git_repo,monkeypatch):
    from pyharness.application import workspace_owned_input_store,workspace_input_pages
    app,client,root=git_repo;await context(client,root);store=app.service.projects.store
    clock=[100.0];original=workspace_input_pages.workspace_input_page
    monkeypatch.setattr(workspace_owned_input_store.time,'monotonic',lambda:clock[0])
    def page(*args,**kwargs):
        result=original(*args,**kwargs);clock[0]+=store.MAX_WORKSPACE_CAPTURE_SECONDS;return result
    monkeypatch.setattr(workspace_input_pages,'workspace_input_page',page)
    before=store.log_path.read_bytes();response=await client.get(INPUTS)
    assert response.status_code==409 and response.json()['code']=='EVT-104',response.text
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_input_owner_lease_and_stable_pagination_reopen(git_repo):
    app,client,root=git_repo;_,cm=await context(client,root)
    for n,ident in enumerate([WI,'wi-2222222222222222']):
        response=await client.post(INPUTS,json=command(cm,input_id=ident),headers={'Idempotency-Key':f'prepare-{n}'})
        assert response.status_code==200,response.text
    page=(await client.get(INPUTS+'?limit=1')).json();assert page['page_count']==1 and page['total']==2 and page['has_more']
    assert 'files' not in page['inputs'][0] and 'deletions' not in page['inputs'][0]
    store=app.service.projects.store;other=WorkspaceStore(store.root,tenant_id='default')
    with pytest.raises(PyHError) as exc:await other._open(initialize=False)
    assert exc.value.ctx['reason']=='workspace_busy';await other.close()
    log=store.log_path.read_bytes();await store.close();app.service.projects.store=WorkspaceStore(store.root,tenant_id='default')
    next_page=(await client.get(INPUTS+'?limit=1&cursor='+page['next_cursor'])).json()
    assert next_page['inputs'][0]['input_id']=='wi-2222222222222222' and not next_page['has_more']
    assert app.service.projects.store.log_path.read_bytes()==log
