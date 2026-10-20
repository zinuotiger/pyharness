"""Ordinary and linked dirty Git inputs reserve actual attempts without source writes."""
import hashlib
import pytest
from tests.unit.test_source_grants import grant_app, issue, URL, PID
from tests.unit.test_workspace_git_preview import git_repo, git, dirty, META
from tests.unit.test_workspace_snapshots import capture, selection, manifest, setup, MANIFESTS
from tests.unit.test_workspace_owned_inputs import INPUTS, command
from tests.unit.test_product_tasks import body, post, ref
from tests.unit.test_run_attempts import submit, submission, QUERY


@pytest.mark.controlled_process
@pytest.mark.parametrize('linked', [False, True])
async def test_dirty_git_attempt_pins_exact_input_and_reopens_without_source_change(git_repo, linked):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, base = git_repo; root = base; options = {}
    if linked:
        root = base.with_name('s06-linked')
        git(base, 'worktree', 'add', '-b', 's06-linked', str(root), 'HEAD')
        result = await client.post(URL, json=issue(base / '.git', grant_id=META), headers={'Idempotency-Key': 'metadata'})
        assert result.status_code == 200, result.text
        options = {'metadata_grant_id': META, 'metadata_grant_revision': 1}
    dirty(root); await setup(client, root)
    hashes = lambda folder: {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
    before_base, before_root = hashes(base), hashes(root)
    snapshot, _ = await capture(client, selection(paths=['unchanged.txt','delete.txt','new.txt','.ai-coding/RULES.md'], **options))
    result = await client.post(MANIFESTS, json=manifest(snapshot), headers={'Idempotency-Key': 'manifest'})
    assert result.status_code == 200, result.text
    cm = result.json(); result = await client.post(INPUTS, json=command(cm), headers={'Idempotency-Key': 'prepare'})
    assert result.status_code == 200, result.text
    wi = result.json(); assert wi['schema_version'] == 1 and wi['deletions']
    task = await post(client, body(input_binding={'manifest_ref': ref(cm,'manifest_id'),'input_ref': ref(wi,'input_id')}))
    assert task.status_code == 200, task.text
    result = await submit(client, submission(task.json()))
    assert result.status_code == 200, result.text
    attempt = result.json(); assert attempt['prerequisites']['input_binding']['input_ref'] == ref(wi,'input_id')
    store = app.service.projects.store; log = store.log_path.read_bytes(); await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    assert (await client.get(QUERY, params={'key':'submit-one'})).json()['attempt'] == attempt
    assert app.service.projects.store.log_path.read_bytes() == log
    assert hashes(base) == before_base and hashes(root) == before_root
    assert not app.service._engines and not app.service._queues
