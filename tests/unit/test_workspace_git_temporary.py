"""Synthetic-only temporary geometry regression; staged outside frozen sources."""
from pathlib import Path
import pytest
from tests.unit.test_source_grants import grant_app
from tests.unit.test_workspace_git_preview import git_repo,git,issued,body,PREVIEW,META
from tests.unit.test_source_grants import GID,URL,issue


@pytest.mark.controlled_process
@pytest.mark.parametrize('where',['source','source-child','common','common-child'])
async def test_temporary_parent_inside_read_only_source_never_created(git_repo,monkeypatch,where):
    from pyharness.application import workspace_git
    app,client,base=git_repo
    root=base
    command=body(paths=['unchanged.txt'])
    if where.startswith('common'):
        root=base.with_name('synthetic-linked')
        git(base,'worktree','add','-b','linked-fixture',str(root),'HEAD')
        assert not (base/'.git').is_relative_to(root)
        assert (await client.post(URL,json=issue(base/'.git',grant_id=META),headers={'Idempotency-Key':'metadata'})).status_code==200
        command=body(paths=['unchanged.txt'],metadata_grant_id=META,metadata_grant_revision=1)
    await issued(client,root)
    parent=root if where.startswith('source') else base/'.git'
    if where.endswith('child'): parent=parent/'synthetic-temp';parent.mkdir()
    created=[];original=workspace_git.tempfile.TemporaryDirectory
    def directory(*args,**kwargs):
        created.append(kwargs['dir'])
        return original(*args,**kwargs)
    monkeypatch.setattr(workspace_git.tempfile,'gettempdir',lambda:str(parent))
    monkeypatch.setattr(workspace_git.tempfile,'TemporaryDirectory',directory)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=command)
    assert response.status_code==403
    assert created==[]
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
@pytest.mark.parametrize('outcome',['success','error','cancel'])
async def test_only_owned_separate_temporary_view_is_cleaned(git_repo,monkeypatch,outcome):
    import asyncio
    from pyharness.application import workspace_git
    from pyharness.errors import raise_code
    app,client,root=git_repo
    await issued(client,root)
    parent=root.parent
    sentinel=parent/'unrelated-synthetic-sentinel';sentinel.write_bytes(b'do not modify\r\n')
    views=[];original=workspace_git._git
    monkeypatch.setattr(workspace_git.tempfile,'gettempdir',lambda:str(parent))
    async def capture(view,*args,**kwargs):
        views.append(Path(view))
        assert view.parent==parent and view!=parent and not view.is_relative_to(root)
        if outcome=='error': raise_code('GRD-403',reason='synthetic read failure')
        if outcome=='cancel': raise asyncio.CancelledError()
        return await original(view,*args,**kwargs)
    monkeypatch.setattr(workspace_git,'_git',capture)
    if outcome=='cancel':
        # Exercise producer cancellation directly; HTTP middleware intentionally owns
        # disconnect handling and does not propagate a fabricated CancelledError.
        resources,_=app.service.projects.store._grant_state()
        command=workspace_git.GitPreviewCommand.model_validate(body(paths=['unchanged.txt']))
        with pytest.raises(asyncio.CancelledError): await workspace_git.preview_git_layers(resources[GID],None,command)
    else:
        response=await client.post(PREVIEW,json=body(paths=['unchanged.txt']))
        assert response.status_code==(200 if outcome=='success' else 403)
    assert views and all(not view.exists() for view in views)
    assert sentinel.read_bytes()==b'do not modify\r\n' and root.exists()
