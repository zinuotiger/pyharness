"""Real ordinary/linked dirty inputs feed the same reader without host body rereads."""
import hashlib
import pytest
from tests.unit.test_workspace_git_preview import git_repo, git, dirty, META
from tests.unit.test_workspace_snapshots import capture, selection, manifest, setup, MANIFESTS
from tests.unit.test_workspace_owned_inputs import INPUTS, command
from tests.unit.test_source_grants import grant_app, issue, URL, PID
from tests.unit.test_product_tasks import ref
from tests.unit.test_documents import DOCS, DOC


@pytest.mark.controlled_process
@pytest.mark.parametrize('linked', [False, True])
async def test_git_documents_preserve_all_source_bytes_and_observe_later_missing_or_changed(git_repo, linked, monkeypatch):
    app, client, base = git_repo
    root = base; options = {}
    if linked:
        root = base.with_name('s05-linked')
        git(base, 'worktree', 'add', '-b', 's05-linked', str(root), 'HEAD')
        result = await client.post(URL, json=issue(base/'.git', grant_id=META), headers={'Idempotency-Key': 'metadata'})
        assert result.status_code == 200, result.text
        options = {'metadata_grant_id': META, 'metadata_grant_revision': 1}
    dirty(root); await setup(client, root)
    snapshot, _ = await capture(client, selection(paths=['unchanged.txt', 'delete.txt', 'new.txt', '.ai-coding/RULES.md'], **options))
    result = await client.post(MANIFESTS, json=manifest(snapshot), headers={'Idempotency-Key': 'manifest'})
    assert result.status_code == 200, result.text
    cm = result.json()
    result = await client.post(INPUTS, json=command(cm), headers={'Idempotency-Key': 'prepare'})
    assert result.status_code == 200, result.text
    wi = result.json(); index = next(i for i, row in enumerate(wi['files']) if row['path'] == 'unchanged.txt')
    hashes = lambda: {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before = hashes()
    result = await client.post(DOCS, json={'schema_version': 1, 'document_id': DOC, 'expected_revision': 0,
        'project_revision': 1, 'title': 'Git document', 'source': {'kind': 'workspace_input', 'input_ref': ref(wi, 'input_id'), 'file_index': index}}, headers={'Idempotency-Key': 'document'})
    assert result.status_code == 200, result.text
    doc = result.json(); assert doc['status'] == 'current' and doc['live_source_freshness'] == 'metadata_only'
    assert hashes() == before
    from pyharness.application import source_snapshot
    monkeypatch.setattr(source_snapshot, 'read_selected', lambda *a, **k: pytest.fail('reader opened live source body'))
    assert (await client.get(f'{DOCS}/{DOC}/revisions/1')).json() == doc
    (root/'unchanged.txt').write_bytes(b'synthetic later source body with different size')
    old = (await client.get(f'{DOCS}/{DOC}/revisions/1')).json()
    assert old['status'] == 'stale' and old['blocks'] == doc['blocks']
    (root/'unchanged.txt').unlink()
    old = (await client.get(f'{DOCS}/{DOC}/revisions/1')).json()
    assert old['status'] == 'missing' and old['blocks'] == []
