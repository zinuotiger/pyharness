"""Given synthetic Git only, when captured, then exact private bytes and control facts survive."""
import hashlib
import json
import pytest
from tests.unit.test_source_grants import grant_app,PID,GID,URL,issue
from tests.unit.test_workspace_git_preview import git_repo,git,dirty,META

WS='ws-0123456789abcdef'
CM='cm-aaaaaaaaaaaaaaaa'
TASK='pt-bbbbbbbbbbbbbbbb'
P=f'/api/v1/projects/{PID}'
PREVIEW=f'{URL}/{GID}/workspace-snapshot-previews'
CAPTURE=f'{URL}/{GID}/workspace-snapshots'
MANIFESTS=P+'/workspace-context-manifests'


def selection(**changes):
    return {'schema_version':1,'expected_revision':1,'project_revision':1,'paths':['unchanged.txt','delete.txt'],
            'metadata_grant_id':None,'metadata_grant_revision':None,**changes}


async def setup(client,root):
    assert (await client.post(URL,json=issue(root),headers={'Idempotency-Key':'source'})).status_code==200


async def capture(client,command=None,*,snapshot_id=WS,key='capture'):
    command=command or selection()
    preview=await client.post(PREVIEW,json=command)
    assert preview.status_code==200,preview.text
    data=preview.json()
    assert data['status']=='workspace_snapshot_confirmation_required'
    body={**command,'snapshot_id':snapshot_id,'confirmation_digest':data['confirmation_digest']}
    response=await client.post(CAPTURE,json=body,headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    return response.json(),body


def manifest(snapshot,**changes):
    return {'schema_version':2,'manifest_id':CM,'expected_revision':0,'project_revision':1,
        'source_ref':{'snapshot_id':snapshot['snapshot_id'],'revision':1,'digest':snapshot['digest'],'source_seq':snapshot['source_seq']},
        'task_ref':None,**changes}


@pytest.mark.controlled_process
@pytest.mark.parametrize('linked',[False,True])
async def test_clean_capture_exact_bytes_and_idempotent_commit(git_repo,linked):
    app,client,base=git_repo;root=base;command=selection()
    if linked:
        root=base.with_name('synthetic-linked');git(base,'worktree','add','-b','linked-fixture',str(root),'HEAD')
        assert (await client.post(URL,json=issue(base/'.git',grant_id=META),headers={'Idempotency-Key':'meta'})).status_code==200
        command=selection(metadata_grant_id=META,metadata_grant_revision=1)
    await setup(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    source=(root/'unchanged.txt').read_bytes()
    snapshot,body=await capture(client,command)
    assert snapshot['revision']==1 and snapshot['persisted'] is True and snapshot['executable'] is False
    assert snapshot['scope']=='tracked_plus_selected_untracked' and snapshot['tracked_paths']==['delete.txt','unchanged.txt']
    assert snapshot['status']=='captured' and snapshot['live_source_freshness']=='not_asserted'
    assert not snapshot['items'][0]['staged'] and not snapshot['items'][0]['unstaged']
    for layer in ['head','index','working']:
        response=await client.get(f'{P}/workspace-snapshots/{WS}/items/0/layers/{layer}')
        assert response.status_code==200 and response.content==source
        assert response.headers['X-PyHarness-Content-Digest']==hashlib.sha256(source).hexdigest()
    exact=(await client.get(f'{P}/workspace-snapshots/{WS}')).json()
    assert exact==snapshot
    log=app.service.projects.store.log_path.read_bytes()
    assert log.startswith(before) and log!=before
    duplicate=await client.post(CAPTURE,json=body,headers={'Idempotency-Key':'capture'})
    assert duplicate.status_code==200 and duplicate.json()==snapshot
    assert app.service.projects.store.log_path.read_bytes()==log
    assert (root/'unchanged.txt').read_bytes()==source
    assert not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
async def test_dirty_versions_deletion_and_only_selected_untracked(git_repo):
    app,client,root=git_repo;dirty(root)
    (root/'not-selected.txt').write_bytes(b'never enter context')
    await setup(client,root)
    snapshot,_=await capture(client,selection(paths=['unchanged.txt','delete.txt','new.txt','.ai-coding/RULES.md']))
    item=snapshot['items'][0]
    assert item['staged'] and item['unstaged']
    for layer,raw in [('head',b'head\r\n'),('index',b'staged\r\n'),('working',b'\xef\xbb\xbfworking\r\n')]:
        assert item[layer]['digest']==hashlib.sha256(raw).hexdigest()
        response=await client.get(f'{P}/workspace-snapshots/{WS}/items/0/layers/{layer}')
        assert response.status_code==200 and response.content==raw
    assert snapshot['items'][1]['deletion_marker'] and snapshot['items'][1]['working'] is None
    assert snapshot['selected_untracked_paths']==['new.txt','.ai-coding/RULES.md']
    assert 'not-selected' not in json.dumps(snapshot)
    response=await client.post(MANIFESTS,json=manifest(snapshot),headers={'Idempotency-Key':'manifest'})
    assert response.status_code==200,response.text
    value=response.json()
    assert value['schema_version']==2 and value['source_ref']['snapshot_id']==WS
    assert [i['input_index'] for i in value['items']]==[0,2,3]
    assert value['status']=='current' and value['executable'] is False
    assert (await client.get(f'{MANIFESTS}/{CM}/revisions/1')).json()==value
    assert (await client.get(f'{MANIFESTS}/{CM}/revisions/1/items/0')).content==b'\xef\xbb\xbfworking\r\n'
    assert (await client.get(f'{P}/context-manifests')).json()['total']==0  # schema1 compatibility
    assert (await client.get(f'{P}/context-manifests/{CM}/revisions/1')).status_code==403
    assert not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
async def test_missing_tracked_file_denies_without_commit(git_repo):
    app,client,root=git_repo;await setup(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=selection(paths=['unchanged.txt']))
    assert response.status_code==422
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_project_task_refresh_and_new_selection_stales_old_manifest(git_repo):
    app,client,root=git_repo;await setup(client,root)
    snapshot,_=await capture(client)
    taskbody={'schema_version':1,'product_task_id':TASK,'expected_revision':0,'project_revision':1,'title':'Synthetic target','retired':False}
    task=await client.post(P+'/context-task-references',json=taskbody,headers={'Idempotency-Key':'task'})
    assert task.status_code==200
    task=task.json();ref={k:task[k] for k in ['product_task_id','revision','digest','source_seq']}
    command=manifest(snapshot,task_ref=ref)
    response=await client.post(MANIFESTS,json=command,headers={'Idempotency-Key':'manifest'})
    assert response.status_code==200,response.text
    assert (await client.post('/api/v1/projects',json={'project_id':PID,'expected_revision':1,'name':'Synthetic r2'},headers={'Idempotency-Key':'project-r2'})).status_code==200
    old=(await client.get(f'{MANIFESTS}/{CM}/revisions/1')).json()
    assert old['status']=='stale' and old['items']==[] and 'project_revision_changed' in old['stale_reasons']
    assert (await client.get(f'{MANIFESTS}/{CM}/revisions/1/items/0')).status_code==409
    taskbody.update(expected_revision=1,project_revision=2,title='Synthetic target r2')
    task=(await client.post(P+'/context-task-references',json=taskbody,headers={'Idempotency-Key':'task-r2'})).json()
    old=(await client.get(f'{MANIFESTS}/{CM}/revisions/1')).json()
    assert 'task_revision_changed' in old['stale_reasons']
    ref={k:task[k] for k in ['product_task_id','revision','digest','source_seq']}
    response=await client.post(MANIFESTS,json=manifest(snapshot,expected_revision=1,project_revision=2,task_ref=ref),headers={'Idempotency-Key':'manifest-r2'})
    assert response.status_code==200 and response.json()['status']=='current'
    (root/'selected-later.txt').write_bytes(b'explicitly selected later')
    newer,_=await capture(client,selection(project_revision=2,paths=['unchanged.txt','delete.txt','selected-later.txt']),snapshot_id='ws-cccccccccccccccc',key='capture-new')
    old=(await client.get(f'{MANIFESTS}/{CM}/revisions/2')).json()
    assert old['status']=='stale' and old['items']==[] and 'workspace_snapshot_superseded' in old['stale_reasons']
    assert (await client.get(f'{MANIFESTS}/{CM}/revisions/2/items/0')).status_code==409
    assert newer['source_seq']>snapshot['source_seq']
    assert not app.service._engines and not app.service._queues


@pytest.mark.controlled_process
async def test_raw_byte_changes_do_not_infer_git_working_mode_policy(git_repo):
    app,client,root=git_repo
    git(root,'update-index','--chmod=+x','unchanged.txt');git(root,'commit','-m','synthetic executable fixture')
    await setup(client,root)
    snapshot,_=await capture(client)
    item=snapshot['items'][0]
    assert item['head']['mode']==item['index']['mode']==0o100755
    assert item['head']['digest']==item['index']['digest']==item['working']['digest']
    assert item['staged'] is False and item['unstaged'] is False
    assert snapshot['change_basis']=='raw_layer_comparison'
    assert item['working_mode_differs']==(item['working']['mode']!=item['index']['mode'])
