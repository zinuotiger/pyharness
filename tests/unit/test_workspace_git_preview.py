"""Given fixture-only Git repositories, preview exact selected layers without writes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat

import pytest
from tests.unit.test_source_grants import grant_app, issue, PID, GID, URL

PREVIEW = f'{URL}/{GID}/git-source-previews'
META = 'sg-aaaaaaaaaaaaaaaa'
import shutil
GIT = shutil.which('git')
if GIT is None:
    raise RuntimeError('Real Git executable is required for workspace integration tests')


def git(root, *args):
    # All writes (init/add/fixture commit/worktree setup) are confined to brand-new synthetic repos.
    env = {k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',
               GIT_AUTHOR_NAME='synthetic',GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='synthetic',GIT_COMMITTER_EMAIL='fixture@example.invalid')
    result = subprocess.run([GIT,'-c','core.hooksPath='+os.devnull,'-c','core.autocrlf=false','-C',str(root),*args],
                            env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result.stdout


@pytest.fixture
async def git_repo(grant_app):
    app, client, base = grant_app
    (base/'unchanged.txt').write_bytes(b'head\r\n')
    (base/'delete.txt').write_bytes(b'delete at worktree\r\n')
    git(base,'init','--initial-branch=fixture')
    git(base,'add','--','unchanged.txt','delete.txt')
    git(base,'commit','-m','synthetic fixture seed')
    return app, client, base


def dirty(root):
    (root/'unchanged.txt').write_bytes(b'staged\r\n')
    git(root,'add','--','unchanged.txt')
    (root/'unchanged.txt').write_bytes(b'\xef\xbb\xbfworking\r\n')
    (root/'delete.txt').unlink()  # own synthetic fixture only
    (root/'new.txt').write_bytes(b'untracked\r\n')
    (root/'.ai-coding').mkdir()
    (root/'.ai-coding/RULES.md').write_bytes(b'explicit dot rules\r\n')


def body(**changes):
    return {'schema_version':1,'expected_revision':1,
            'paths':['unchanged.txt','delete.txt','new.txt','.ai-coding/RULES.md'],
            'metadata_grant_id':None,'metadata_grant_revision':None,**changes}


@pytest.mark.controlled_process
@pytest.mark.parametrize('linked',[False,True])
async def test_git_layers_are_distinct_and_source_unchanged(git_repo, linked):
    app, client, base = git_repo
    root = base
    if linked:
        root = base.with_name('synthetic-linked')
        git(base,'worktree','add','-b','linked-fixture',str(root),'HEAD')
    dirty(root)
    assert (await client.post(URL,json=issue(root),headers={'Idempotency-Key':'source'})).status_code == 200
    command=body()
    if linked:
        denied=await client.post(PREVIEW,json=command)
        assert denied.status_code == 403  # .git cannot silently authorize an external common directory
        assert (await client.post(URL,json=issue(base/'.git',grant_id=META),headers={'Idempotency-Key':'metadata'})).status_code == 200
        command=body(metadata_grant_id=META,metadata_grant_revision=1)
    before_index=(base/'.git'/('worktrees/'+root.name+'/index' if linked else 'index')).read_bytes()
    before_work=(root/'unchanged.txt').read_bytes()
    before_log=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=command)
    assert response.status_code == 200,response.text
    data=response.json()
    assert data['status']=='git_layers_observed' and data['repository_kind']==('linked_worktree' if linked else 'ordinary')
    assert data['project_id']==PID and data['grant_id']==GID and data['file_count']==4
    assert data['persisted'] is False and data['executable'] is False
    changed=data['items'][0]
    for layer,raw in [('head',b'head\r\n'),('index',b'staged\r\n'),('working',b'\xef\xbb\xbfworking\r\n')]:
        assert changed[layer]['digest']==hashlib.sha256(raw).hexdigest()
        assert changed[layer]['size_bytes']==len(raw)
    assert data['items'][1]['working'] is None and data['items'][1]['deletion_marker'] is True
    assert data['items'][2]['selected_untracked'] is True and data['items'][2]['head'] is None and data['items'][2]['index'] is None
    assert data['items'][3]['path']=='.ai-coding/RULES.md'
    assert str(root) not in json.dumps(data) and 'working\r\n' not in json.dumps(data)
    assert app.service.projects.store.log_path.read_bytes()==before_log
    assert (root/'unchanged.txt').read_bytes()==before_work
    assert (base/'.git'/('worktrees/'+root.name+'/index' if linked else 'index')).read_bytes()==before_index
    assert not app.service._engines and not app.service._queues


async def issued(client, root):
    assert (await client.post(URL,json=issue(root),headers={'Idempotency-Key':'source'})).status_code == 200


@pytest.mark.controlled_process
@pytest.mark.parametrize('change',[{'schema_version':True},{'expected_revision':True},{'paths':[]},
    {'paths':['../outside']},{'extra':'reject'},{'metadata_grant_id':META},{'metadata_grant_revision':True}])
async def test_bad_commands_rejected_without_commit(git_repo, change):
    app,client,root=git_repo
    await issued(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=body(**change))
    assert response.status_code in (403,422)
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_secret_selection_never_reads_body_or_runs_git(git_repo, monkeypatch):
    from pyharness.application import workspace_git
    app,client,root=git_repo
    (root/'.env').write_bytes(b'password=synthetic-only')
    await issued(client,root)
    async def forbidden(*_,**__): raise AssertionError('rejected selection reached git')
    monkeypatch.setattr(workspace_git,'_git',forbidden)
    before=app.service.projects.store.log_path.read_bytes()
    result=await client.post(PREVIEW,json=body(paths=['.env']))
    assert result.status_code==403 and 'synthetic-only' not in result.text
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('layer',['index','working'])
async def test_secret_bytes_rejected_without_persistence(git_repo, layer):
    app,client,root=git_repo
    (root/'unchanged.txt').write_bytes(b'password=synthetic-only')
    if layer=='index':
        git(root,'add','--','unchanged.txt')
        (root/'unchanged.txt').write_bytes(b'harmless working bytes')
    await issued(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt']))
    assert response.status_code==403 and 'synthetic-only' not in response.text
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_source_config_filters_hooks_fsmonitor_never_execute(git_repo):
    app,client,root=git_repo
    marker=root/'must-not-execute'
    command=f'echo forbidden > "{marker.as_posix()}"'
    git(root,'config','core.fsmonitor',command)
    git(root,'config','filter.synthetic.clean',command)
    git(root,'config','filter.synthetic.smudge',command)
    git(root,'config','filter.synthetic.required','true')
    git(root,'config','diff.external',command)
    (root/'.gitattributes').write_text('*.txt filter=synthetic diff=synthetic\n')
    (root/'.git/hooks/pre-commit').write_text('#!/bin/sh\n'+command+'\n')
    (root/'unchanged.txt').write_bytes(b'working changed\r\n')
    await issued(client,root)
    before=(root/'.git/config').read_bytes()
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt']))
    assert response.status_code==200,response.text
    assert not marker.exists() and (root/'.git/config').read_bytes()==before


@pytest.mark.controlled_process
async def test_assume_unchanged_index_flag_is_explicitly_unsupported(git_repo):
    _,client,root=git_repo
    git(root,'update-index','--assume-unchanged','unchanged.txt')
    await issued(client,root)
    assert (await client.post(PREVIEW,json=body(paths=['unchanged.txt']))).status_code==403


@pytest.mark.controlled_process
@pytest.mark.parametrize('change',['edit','delete','index','same-metadata-edit'])
async def test_change_during_git_read_rejects_without_silent_resampling(git_repo, monkeypatch, change):
    from pyharness.application import workspace_git
    app,client,root=git_repo
    await issued(client,root)
    original=workspace_git._git
    changed=False
    async def read(*args,**kwargs):
        nonlocal changed
        result=await original(*args,**kwargs)
        if not changed:
            changed=True
            if change=='delete': (root/'unchanged.txt').unlink()
            elif change=='edit': (root/'unchanged.txt').write_bytes(b'changed during initial Git reading')
            elif change=='same-metadata-edit':
                path=root/'unchanged.txt';info=path.stat();path.write_bytes(b'evil\r\n')
                os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
            else:
                (root/'unchanged.txt').write_bytes(b'index changed during reading')
                git(root,'add','--','unchanged.txt')
        return result
    monkeypatch.setattr(workspace_git,'_git',read)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt']))
    assert response.status_code==409
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_clean_layers_have_comparable_git_mode_and_separate_physical_mode(git_repo):
    _,client,root=git_repo
    await issued(client,root)
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt']))
    assert response.status_code==200
    item=response.json()['items'][0]
    assert item['head']['mode']==item['index']['mode']==item['working']['mode']
    assert item['working']['native_mode']==stat.S_IMODE((root/'unchanged.txt').stat().st_mode)


@pytest.mark.controlled_process
async def test_unrelated_metadata_grant_is_not_reported_as_authority(git_repo):
    _,client,root=git_repo
    await issued(client,root)
    unrelated=root.with_name('unrelated-synthetic-metadata');unrelated.mkdir()
    assert (await client.post(URL,json=issue(unrelated,grant_id=META),headers={'Idempotency-Key':'metadata'})).status_code==200
    assert (await client.post(PREVIEW,json=body(paths=['unchanged.txt'],metadata_grant_id=META,metadata_grant_revision=1))).status_code==403


@pytest.mark.controlled_process
@pytest.mark.skipif(os.name!='nt',reason='Windows case-insensitive path boundary')
async def test_case_only_selected_path_cannot_masquerade_as_untracked(git_repo):
    _,client,root=git_repo
    await issued(client,root)
    response=await client.post(PREVIEW,json=body(paths=['UNCHANGED.txt']))
    assert response.status_code==403 or (response.status_code==200 and response.json()['items'][0]['head'] is not None
                                         and response.json()['items'][0]['selected_untracked'] is False)


@pytest.mark.controlled_process
@pytest.mark.skipif(os.name!='nt',reason='Windows junction boundary')
async def test_git_metadata_junction_rejected_before_git(git_repo, monkeypatch):
    from pyharness.application import workspace_git
    app,client,root=git_repo
    await issued(client,root)
    real=root/'.git-owned';(root/'.git').rename(real)
    result=subprocess.run(['cmd','/c','mklink','/J',str(root/'.git'),str(real)],capture_output=True,timeout=10)
    if result.returncode: pytest.skip('junction creation unavailable for synthetic fixture')
    async def forbidden(*_,**__): raise AssertionError('junction reached Git process')
    monkeypatch.setattr(workspace_git,'_git',forbidden)
    before=app.service.projects.store.log_path.read_bytes()
    assert (await client.post(PREVIEW,json=body(paths=['unchanged.txt']))).status_code==403
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_total_preview_deadline_releases_owner_lock(git_repo, monkeypatch):
    import asyncio
    from pyharness.application import workspace_git_store
    app,client,root=git_repo
    await issued(client,root)
    cleaned=False
    async def slow(*_,**__):
        nonlocal cleaned
        try: await asyncio.sleep(30)
        finally: cleaned=True
    monkeypatch.setattr(workspace_git_store.GitPreviewStore,'MAX_GIT_PREVIEW_SECONDS',.05,raising=False)
    monkeypatch.setattr(workspace_git_store,'preview_git_layers',slow)
    before=app.service.projects.store.log_path.read_bytes()
    response=await asyncio.wait_for(client.post(PREVIEW,json=body(paths=['unchanged.txt'])),.5)
    assert response.status_code==409 and cleaned
    assert not app.service.projects.store._lock.locked()
    assert (await client.get('/api/v1/projects')).status_code==200
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_all_selected_names_checked_before_any_body_or_git_read(git_repo, monkeypatch):
    from pyharness.application import workspace_git
    app,client,root=git_repo
    (root/'password=synthetic.txt').write_bytes(b'synthetic harmless bytes')
    await issued(client,root)
    calls=[]
    original=workspace_git.read_selected
    def read(*args,**kwargs):
        calls.append('body')
        return original(*args,**kwargs)
    async def forbidden(*_,**__):
        calls.append('git')
        raise AssertionError('disallowed selected name reached Git')
    monkeypatch.setattr(workspace_git,'read_selected',read)
    monkeypatch.setattr(workspace_git,'_git',forbidden)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt','password=synthetic.txt']))
    assert response.status_code==403
    assert calls==[]
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('phase',['producer-tail','authority-tail'])
async def test_synchronous_tail_over_budget_never_returns_success(git_repo, monkeypatch, phase):
    import asyncio
    import time
    from pyharness.application import workspace_git_store
    app,client,root=git_repo
    await issued(client,root)
    store=app.service.projects.store
    async def delayed(*_,**__):
        await asyncio.sleep(0)
        if phase=='producer-tail': time.sleep(.08)
        return {'synthetic':'must never be returned as success'}
    original=store._context_finish
    checks=0
    def finish(*args,**kwargs):
        nonlocal checks
        result=original(*args,**kwargs)
        checks+=1
        if checks==2 and phase=='authority-tail': time.sleep(.08)
        return result
    monkeypatch.setattr(workspace_git_store.GitPreviewStore,'MAX_GIT_PREVIEW_SECONDS',.03)
    monkeypatch.setattr(workspace_git_store,'preview_git_layers',delayed)
    monkeypatch.setattr(store,'_context_finish',finish)
    before=store.log_path.read_bytes()
    response=await asyncio.wait_for(client.post(PREVIEW,json=body(paths=['unchanged.txt'])),.5)
    assert response.status_code==409
    assert not store._lock.locked()
    assert store.log_path.read_bytes()==before
