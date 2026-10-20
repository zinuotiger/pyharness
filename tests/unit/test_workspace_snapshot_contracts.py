"""Given durable Git definitions, malformed scope or references never validate."""
import json
import pytest
from pydantic import ValidationError
from tests.unit.test_source_grants import grant_app
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_snapshots import setup,capture,manifest,P,MANIFESTS


@pytest.mark.controlled_process
async def test_complete_tracked_definition_and_strict_snapshot_shape(git_repo):
    from pyharness.application.workspace_snapshot import WorkspaceSnapshot
    app,client,root=git_repo;await setup(client,root)
    snapshot,_=await capture(client)
    store=app.service.projects.store
    value=json.loads(store._blob(snapshot['digest']).read_text(encoding='utf-8'))
    assert WorkspaceSnapshot.model_validate(value).snapshot_id==snapshot['snapshot_id']
    for change in ({'tracked_paths':[]},{'schema_version':True},{'revision':True},{'executable':False},{'root_identity':'invalid'}):
        with pytest.raises(ValidationError):WorkspaceSnapshot.model_validate({**value,**change})


@pytest.mark.controlled_process
async def test_lists_are_small_summaries_with_exact_receipt_digest(git_repo):
    from pyharness.application.workspace_store import canonical,digest_of
    _,client,root=git_repo;await setup(client,root)
    snapshot,_=await capture(client)
    assert (await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})).status_code==200
    s=await client.get(P+'/workspace-snapshots');m=await client.get(MANIFESTS)
    assert s.status_code==m.status_code==200,(s.text,m.text)
    snapshots=s.json()['snapshots'];manifests=m.json()['manifests']
    assert len(snapshots)==len(manifests)==1
    for value in [snapshots[0],manifests[0]]:
        assert 'items' not in value and 'tracked_paths' not in value and 'selected_untracked_paths' not in value
        raw=dict(value);expected=raw.pop('receipt_digest')
        assert digest_of(canonical(raw))==expected


@pytest.mark.controlled_process
async def test_workspace_lists_have_stable_pages_and_reject_changed_views(git_repo):
    app,client,root=git_repo;await setup(client,root)
    first,_=await capture(client)
    assert (await client.post(MANIFESTS,json=manifest(first),headers={'Idempotency-Key':'manifest-1'})).status_code==200
    second,_=await capture(client,snapshot_id='ws-2222222222222222',key='capture-2')
    assert (await client.post(MANIFESTS,json=manifest(second,manifest_id='cm-2222222222222222'),headers={'Idempotency-Key':'manifest-2'})).status_code==200
    for url,key in [(P+'/workspace-snapshots','snapshots'),(MANIFESTS,'manifests')]:
        page=(await client.get(url,params={'limit':'1'})).json()
        assert len(page[key])==1 and page['total']==2 and page['page_count']==1 and page['has_more']
        assert page['next_cursor']
        if key=='snapshots': assert page[key][0]['source_seq']<second['source_seq']
        again=(await client.get(url,params={'limit':'1','cursor':page['next_cursor']})).json()
        assert len(again[key])==1 and not again['has_more'] and again['next_cursor'] is None
        assert page[key][0]['source_seq']<again[key][0]['source_seq']
        assert again['source_watermark']==page['source_watermark'] and again['view_digest']==page['view_digest']
        assert (await client.get(url,params={'limit':'2','cursor':page['next_cursor']})).status_code==422
        assert (await client.get(url,params={'limit':'1','cursor':page['next_cursor']+'bad'})).status_code==422
        changed=(await client.post('/api/v1/projects',json={'project_id':first['project_id'],'expected_revision':1,'name':'Synthetic changed'},headers={'Idempotency-Key':'project-change-'+key}))
        if key=='snapshots': assert changed.status_code==200
        else: assert changed.status_code==409
        if key=='snapshots':
            assert (await client.get(url,params={'limit':'1','cursor':page['next_cursor']})).status_code==409


@pytest.mark.controlled_process
@pytest.mark.parametrize('query',['limit=0','limit=51','limit=1&limit=2','unknown=1','cursor=bad'])
async def test_workspace_page_query_is_strict(git_repo,query):
    _,client,root=git_repo;await setup(client,root)
    for url in [P+'/workspace-snapshots',MANIFESTS]:
        assert (await client.get(url+'?'+query)).status_code==422


@pytest.mark.controlled_process
async def test_public_capture_limit_rejects_before_any_private_write(git_repo,monkeypatch):
    from tests.unit.test_workspace_snapshots import selection,PREVIEW,CAPTURE
    app,client,root=git_repo;await setup(client,root)
    paths=['unchanged.txt','delete.txt']
    for n in range(30):
        name=f'{n:02}-'+('synthetic-data-'*5)+'end.txt';(root/name).write_bytes(b'synthetic same bytes');paths.append(name)
    command=selection(paths=paths);first,_=await capture(client,command)
    store=app.service.projects.store
    private_size=len(store._blob(first['digest']).read_bytes())
    assert len(json.dumps(first).encode())>private_size+500
    monkeypatch.setattr(store,'MAX_CONTEXT_BYTES',private_size+500)
    preview=await client.post(PREVIEW,json=command);assert preview.status_code==200,preview.text
    writes=[];original=store._write_snapshot_blob
    def write(*args):writes.append(args[0]);return original(*args)
    monkeypatch.setattr(store,'_write_snapshot_blob',write)
    before=store.log_path.read_bytes()
    result=await client.post(CAPTURE,json={**command,'snapshot_id':'ws-3333333333333333','confirmation_digest':preview.json()['confirmation_digest']},headers={'Idempotency-Key':'bounded-public'})
    assert result.status_code==422 and not writes
    assert store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_workspace_preview_response_byte_limit(git_repo,monkeypatch):
    from tests.unit.test_workspace_snapshots import selection,PREVIEW
    app,client,root=git_repo;await setup(client,root)
    monkeypatch.setattr(app.service.projects.store,'MAX_CONTEXT_BYTES',500)
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(PREVIEW,json=selection())).status_code==422
    assert app.service.projects.store.log_path.read_bytes()==before
