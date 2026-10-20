"""Reject unsupported metadata/content and strict preview contract violations."""
import json
import pytest
from pydantic import ValidationError
from tests.unit.test_workspace_git_preview import git_repo,git,issued,body,PREVIEW
from tests.unit.test_source_grants import grant_app


@pytest.mark.controlled_process
@pytest.mark.parametrize('condition',['alternates','promisor','sparse','large-blob','response-limit','metadata-limit'])
async def test_git_limits_and_unsupported_features_zero_commit(git_repo, monkeypatch, condition):
    from pyharness.application import workspace_git
    app,client,root=git_repo
    if condition=='alternates':
        (root/'.git/objects/info/alternates').write_text('synthetic unauthorized object directory\n')
    elif condition=='promisor':
        (root/'.git/objects/pack/synthetic.promisor').write_bytes(b'')
    elif condition=='sparse':
        git(root,'update-index','--skip-worktree','unchanged.txt')
    elif condition=='large-blob':
        (root/'unchanged.txt').write_bytes(b'x'*(10*1024*1024+1));git(root,'add','--','unchanged.txt')
        (root/'unchanged.txt').write_bytes(b'harmless working')
    elif condition=='response-limit': monkeypatch.setattr(workspace_git,'MAX_RESPONSE_BYTES',256)
    else: monkeypatch.setattr(workspace_git,'MAX_METADATA_FILES',1)
    await issued(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt']))
    assert response.status_code in (403,422)
    assert app.service.projects.store.log_path.read_bytes()==before


@pytest.mark.controlled_process
async def test_strict_preview_models_and_digest_match_real_service(git_repo):
    from pyharness.application.workspace_git_contracts import GitSourcePreview
    from pyharness.application.workspace_store import canonical,digest_of
    _,client,root=git_repo
    await issued(client,root)
    response=await client.post(PREVIEW,json=body(paths=['unchanged.txt']))
    assert response.status_code==200,response.text
    value=response.json();model=GitSourcePreview.model_validate(value)
    without=dict(value);digest=without.pop('observation_digest')
    assert digest_of(canonical(without))==digest
    for change in ({'schema_version':True},{'source_seq':True},{'unique_content_size_bytes':0},{'extra':'reject'},
                   {'persisted':0},{'executable':0}):
        with pytest.raises(ValidationError): GitSourcePreview.model_validate({**value,**change})
    assert model.items[0].working.native_mode is not None
