"""GIVEN explicit synthetic files WHEN confirmed THEN private immutable bytes commit."""
import hashlib
import pytest
from tests.unit.test_source_grants import grant_app, issue, PID, GID, URL

PREVIEW = f'{URL}/{GID}/snapshot-previews'
SNAPS = f'{URL}/{GID}/snapshots'
BODY = {'schema_version': 1, 'expected_revision': 1, 'paths': ['unchanged.txt']}
SID = 'ss-0123456789abcdef'


async def test_confirmed_snapshot_commits_private_bytes_and_reads_back(grant_app):
    app, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})
    before = (source / 'unchanged.txt').read_bytes()
    preview = await client.post(PREVIEW, json=BODY)
    assert preview.status_code == 200, preview.text
    assert preview.json()['status'] == 'confirmation_required'
    command = {**BODY, 'snapshot_id': SID, 'confirmation_digest': preview.json()['confirmation_digest'], 'policy': 'reject-secrets-utf8-v1'}
    result = await client.post(SNAPS, json=command, headers={'Idempotency-Key': 'capture'})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['snapshot_id'] == SID and data['capture_stability'] == 'stable_observed'
    assert data['file_count'] == 1 and data['total_size_bytes'] == len(before)
    store = app.service.projects.store
    assert (store.root / 'snapshot-content' / (hashlib.sha256(before).hexdigest() + '.bin')).read_bytes() == before
    assert str(source) not in result.text and 'local-operator' not in result.text and before.decode() not in result.text
    assert (await client.get(SNAPS)).json() == {'snapshots': [data], 'total': 1}
    audit = (await client.get('/api/v1/workspace/audit')).json()
    assert audit['events'][-1]['payload']['resource_kind'] == 'source_snapshot'
    assert 'unchanged.txt' not in str(audit)
    assert (source / 'unchanged.txt').read_bytes() == before
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('raw', [b'api_key = synthetic-value', b'password: "synthetic"',
    b'{"access_token":"synthetic"}', b'Authorization: Bearer synthetic-value',
    (b'-----BEGIN RSA ' + b'PRIVATE KEY-----'), b'sk-' + b'A' * 40,
    b'https://synthetic:password@example.invalid/', b'ghp_' + b'x' * 36,
    b'AKIA' + b'A' * 16, b'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.synthetic',
    b'\xff\xfe', b'normal\x00binary', b'normal\x01control'], ids=[f'policy-{i}' for i in range(13)])
async def test_content_policy_rejects_before_any_private_write(grant_app, raw):
    app, client, source = grant_app
    (source / 'selected.txt').write_bytes(raw)
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})
    store = app.service.projects.store
    before = {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'}
    response = await client.post(PREVIEW, json={**BODY, 'paths': ['unchanged.txt', 'selected.txt']})
    assert response.status_code == 403
    assert response.json()['reason'] == 'snapshot_content_rejected'
    assert before == {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob('*') if p.is_file() and p.suffix != '.lock'}
    assert 'synthetic-value' not in response.text
    assert (source / 'selected.txt').read_bytes() == raw


async def command_for(client, **changes):
    response = await client.post(PREVIEW, json=BODY)
    assert response.status_code == 200, response.text
    return {**BODY, 'snapshot_id': SID, 'confirmation_digest': response.json()['confirmation_digest'],
            'policy': 'reject-secrets-utf8-v1', **changes}


async def test_changes_during_final_log_validation_are_rejected(grant_app, monkeypatch):
    app, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})
    command = await command_for(client)
    store = app.service.projects.store
    original = store._grant_state
    def racing_state():
        result = original()
        if (store.root / 'snapshot-content').exists():
            (source / 'unchanged.txt').write_bytes(b'changed externally')
        return result
    monkeypatch.setattr(store, '_grant_state', racing_state)
    before = store.log_path.read_bytes()
    response = await client.post(SNAPS, json=command, headers={'Idempotency-Key': 'capture'})
    assert response.status_code == 409
    assert store.log_path.read_bytes() == before
