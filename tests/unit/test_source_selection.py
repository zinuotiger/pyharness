"""GIVEN explicit synthetic names WHEN previewed THEN only selected metadata is observed."""
from pathlib import Path
import os
import pytest
from tests.unit.test_source_grants import grant_app, issue, PID, GID, URL

PREVIEW = f'{URL}/{GID}/selection-previews'
BODY = {'schema_version': 1, 'expected_revision': 1, 'paths': ['unchanged.txt']}


async def test_selected_metadata_has_no_source_reads_scans_or_writes(grant_app, monkeypatch):
    app, client, source = grant_app
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})).status_code == 200
    store = app.service.projects.store
    before = {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'}
    info = (source / 'unchanged.txt').stat()
    old_open, old_scan, old_os_open = Path.open, os.scandir, os.open
    def opened(path, *a, **kw):
        assert not path.is_relative_to(source), 'source content must not be opened'
        return old_open(path, *a, **kw)
    def scanned(path):
        assert not Path(path).is_relative_to(source), 'source must not be scanned'
        return old_scan(path)
    def os_opened(path, *a, **kw):
        assert not Path(path).is_relative_to(source), 'source fd must not be opened'
        return old_os_open(path, *a, **kw)
    with monkeypatch.context() as m:
        m.setattr(Path, 'open', opened); m.setattr(os, 'scandir', scanned); m.setattr(os, 'open', os_opened)
        response = await client.post(PREVIEW, json=BODY)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'metadata_observed'
    assert data['items'] == [{'input_index': 0, 'status': 'accepted', 'path': 'unchanged.txt', 'size_bytes': info.st_size, 'mtime_ns': str(info.st_mtime_ns)}]
    assert data['summary'] == {'requested': 1, 'accepted': 1, 'excluded': 0, 'total_size_bytes': info.st_size}
    assert set(data) == {'schema_version', 'project_id', 'grant_id', 'revision', 'checked_at', 'status', 'items', 'summary', 'digest', 'source_seq'}
    assert str(source) not in response.text and 'local-operator' not in response.text
    assert before == {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'}
    assert (source / 'unchanged.txt').read_bytes() == b'unchanged\r\n'
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('name', ['../outside.txt', '/absolute/private.txt', 'C:/private/file',
    '//server/share', 'folder/../file', './file', 'folder\\file', 'file:ads', 'NUL.txt',
    'name.', 'name ', 'a//b', 'folder/', 'bad\x00name', 'bad\nname'])
async def test_unsafe_names_are_excluded_without_echo(grant_app, name):
    _, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    response = await client.post(PREVIEW, json={**BODY, 'paths': [name, 'unchanged.txt']})
    assert response.status_code == 200, response.text
    assert response.json()['items'][0] == {'input_index': 0, 'status': 'excluded', 'reason': 'path_rejected'}
    assert response.json()['summary']['accepted'] == 1


@pytest.mark.parametrize('name', ['.env', '.env.local', 'x/.git/config', 'credentials.yaml',
    '.ssh/id_rsa', '.aws/config', 'id_ed25519', 'keys/private.pem', 'cookies.sqlite',
    'browser-profile/Default/Preferences', 'tokens.json', 'secrets.yml'])
async def test_sensitive_names_excluded_before_source_metadata(grant_app, monkeypatch, name):
    _, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    old_stat = Path.stat
    def checked(path, *a, **kw):
        assert path == source or not path.is_relative_to(source), 'sensitive selection touched source metadata'
        return old_stat(path, *a, **kw)
    with monkeypatch.context() as m:
        m.setattr(Path, 'stat', checked)
        response = await client.post(PREVIEW, json={**BODY, 'paths': [name]})
    assert response.status_code == 200, response.text
    assert response.json()['items'] == [{'input_index': 0, 'status': 'excluded', 'reason': 'sensitive_name'}]


async def test_types_missing_duplicates_and_size_budget(grant_app):
    _, client, source = grant_app
    (source / '.ai-coding').mkdir(); (source / '.ai-coding/rule.md').write_text('rule')
    with (source / 'large.bin').open('wb') as f: f.truncate(10 * 1024 * 1024 + 1)
    with (source / 'limit.bin').open('wb') as f: f.truncate(10 * 1024 * 1024)
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    paths = ['missing', '.ai-coding', 'large.bin', 'unchanged.txt', 'UNCHANGED.TXT', 'limit.bin', '.ai-coding/rule.md']
    response = await client.post(PREVIEW, json={**BODY, 'paths': paths})
    assert response.status_code == 200, response.text
    data = response.json()
    assert [i.get('reason') for i in data['items']] == ['file_unavailable', 'not_regular', 'file_too_large', None, 'duplicate_path', 'selection_size_limit', None]
    assert data['summary'] == {'requested': 7, 'accepted': 2, 'excluded': 5, 'total_size_bytes': 15}


@pytest.mark.parametrize('paths', [[], ['x'] * 201, 'file', [None], [True], [1], [''], ['x' * 1025]])
async def test_strict_selection_bounds(grant_app, paths):
    _, client, _ = grant_app
    assert (await client.post(PREVIEW, json={**BODY, 'paths': paths})).status_code == 422


async def test_maximum_selection_is_bounded(grant_app):
    _, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    response = await client.post(PREVIEW, json={**BODY, 'paths': [f'missing-{i}' for i in range(200)]})
    assert response.status_code == 200
    assert len(response.json()['items']) == 200
    assert response.json()['summary'] == {'requested': 200, 'accepted': 0, 'excluded': 200, 'total_size_bytes': 0}


# Re-exercise the established HTTP security contract against the new route.
# These are real service/log/account calls, not mocked successful responses.
def selection_contract(monkeypatch):
    from tests.unit import test_source_preflight as contract
    monkeypatch.setattr(contract, 'PREFLIGHT', PREVIEW)
    monkeypatch.setattr(contract, 'BODY', BODY)
    return contract


@pytest.mark.parametrize('condition', ['revoked', 'expired', 'archived', 'stale', 'cross-project', 'unknown'])
async def test_grant_admission_contract(grant_app, monkeypatch, condition):
    contract = selection_contract(monkeypatch)
    def forbidden(*a): raise AssertionError('unavailable grant reached source metadata')
    monkeypatch.setattr('pyharness.application.source_selection.observe_selection', forbidden)
    await contract.test_unavailable_grants_are_denied_without_metadata(grant_app, monkeypatch, condition)


@pytest.mark.parametrize('replacement', ['missing', 'directory', 'file'])
async def test_root_drift_contract(grant_app, monkeypatch, replacement):
    await selection_contract(monkeypatch).test_root_metadata_drift_is_denied(grant_app, replacement)


@pytest.mark.parametrize('corruption', ['torn', 'invalid', 'blob', 'missing-blob', 'disk-cache'])
async def test_disk_integrity_contract(grant_app, monkeypatch, corruption):
    await selection_contract(monkeypatch).test_preflight_corrupt_disk_fails_closed_without_repair(grant_app, corruption)


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind,ancestor', [('junction', False), ('junction', True), ('symlink', False)])
async def test_root_link_contract(grant_app, monkeypatch, kind, ancestor):
    await selection_contract(monkeypatch).test_real_windows_link_replacement_is_denied(grant_app, kind, ancestor)


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind', ['junction', 'symlink'])
async def test_selected_ancestor_link_rejected(grant_app, kind):
    import subprocess
    _, client, source = grant_app
    target = source.parent / 'synthetic-target'; target.mkdir()
    (target / 'selected.txt').write_bytes(b'not to read')
    link = source / 'linked'
    if kind == 'junction' and os.name == 'nt':
        p = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True, timeout=10)
        if p.returncode: pytest.skip('junction unavailable')
        assert link.is_junction()
    else:
        try: link.symlink_to(target, target_is_directory=True)
        except OSError: pytest.skip('symlink privilege unavailable')
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    response = await client.post(PREVIEW, json={**BODY, 'paths': ['linked/selected.txt']})
    assert response.status_code == 200
    assert response.json()['items'][0]['reason'] == 'path_rejected'
    assert (target / 'selected.txt').read_bytes() == b'not to read'


async def test_real_http_roles_contract(grant_app, monkeypatch):
    await selection_contract(monkeypatch).test_real_http_roles_and_membership_revocation(grant_app)


@pytest.mark.parametrize('body', [{}, {'schema_version': 1, 'paths': ['x']},
    {'expected_revision': 1, 'paths': ['x']}, {**BODY, 'expected_revision': True},
    {**BODY, 'expected_revision': 0}, {**BODY, 'expected_revision': '1'},
    {**BODY, 'schema_version': True}, {**BODY, 'schema_version': 2},
    *[{**BODY, f: 'private forged input'} for f in ['source_path','root_identity','actor','tenant','identity','approved_by']]])
async def test_strict_envelope(grant_app, body):
    _, client, _ = grant_app
    response = await client.post(PREVIEW, json=body)
    assert response.status_code == 422
    assert 'private forged input' not in response.text


@pytest.mark.parametrize('name', ['credentials.txt', 'token.txt', 'api_key.json', '.npmrc', '.netrc', '.kube/config', 'my-private.key', 'chrome-profile/Default/Preferences'])
async def test_additional_credential_names_never_observed(grant_app, name):
    _, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    response = await client.post(PREVIEW, json={**BODY, 'paths': [name]})
    assert response.status_code == 200
    assert response.json()['items'][0]['reason'] == 'sensitive_name'


async def test_mtime_exact_decimal_survives_browser_json(grant_app):
    _, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'issue'})
    result = (await client.post(PREVIEW, json=BODY)).json()
    assert result['items'][0]['mtime_ns'] == str((source / 'unchanged.txt').stat().st_mtime_ns)


@pytest.mark.parametrize('empty', [False, True])
async def test_empty_control_remains_uninitialized(tmp_path, empty):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.source_selection import SelectionPreview
    from pyharness.errors import PyHError
    store = WorkspaceStore(tmp_path / 'private', tenant_id='default')
    if empty:
        store.log_path.parent.mkdir(parents=True); store.log_path.write_bytes(b'')
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    try:
        with pytest.raises(PyHError, match='GRD-403'):
            await store.preview_source_selection(PID, GID, SelectionPreview(**BODY))
    finally: await store.close()
    assert before == {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


async def test_two_tenant_selection_isolation_restart_and_owner(tmp_path):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.workspace_contracts import ProjectRevision
    from pyharness.application.source_grants import IssueGrant, RevokeGrant
    from pyharness.application.source_selection import SelectionPreview
    from pyharness.errors import PyHError
    stores = [WorkspaceStore(tmp_path / t, tenant_id=t) for t in ['alpha','beta']]
    rival = WorkspaceStore(tmp_path / 'alpha', tenant_id='alpha')
    command = SelectionPreview(**BODY)
    try:
        for n,s in enumerate(stores):
            src=tmp_path / f'source-{n}';src.mkdir();(src/'unchanged.txt').write_bytes(b'x' * (n+1))
            await s.commit(ProjectRevision(project_id=PID, revision=1, name='synthetic'), expected_revision=0, command_id='project', actor_ref='operator')
            await s.issue_source_grant(PID, IssueGrant(**issue(src)), command_id='grant', actor_ref='operator')
            assert (await s.preview_source_selection(PID,GID,command))['summary']['total_size_bytes'] == n+1
        with pytest.raises(PyHError, match='PERS-202'):
            await rival.preview_source_selection(PID,GID,command)
        before=stores[0].log_path.read_bytes()
        await stores[0].close()
        assert (await rival.preview_source_selection(PID,GID,command))['summary']['total_size_bytes'] == 1
        assert rival.log_path.read_bytes() == before
        await rival.revoke_source_grant(GID,RevokeGrant(expected_revision=1),command_id='revoke',actor_ref='operator')
        with pytest.raises(PyHError, match='GRD-403'):
            await rival.preview_source_selection(PID,GID,SelectionPreview(**{**BODY,'expected_revision':2}))
        assert (await stores[1].preview_source_selection(PID,GID,command))['summary']['total_size_bytes'] == 2
    finally:
        for s in [*stores,rival]:await s.close()
