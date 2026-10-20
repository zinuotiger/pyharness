"""Real original settings/secret files back configuration revisions and replay."""
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.errors import PyHError


def profile():
    return {'id': 'model-one', 'label': 'Synthetic model', 'model': 'fixture-model',
        'base_url': 'http://127.0.0.1:1', 'api_key': 'synthetic-original-e04-secret',
        'temperature': 0.2, 'max_tokens': 1000, 'fallback_models': []}


def allow():
    return None  # Explicit caller authority in this storage-layer test.


def captured(store):
    current = store.model_configuration_current('alpha', 'model-one', authorize=allow)
    receipt = store.capture_model_configuration('alpha', 'model-one',
        {'command_key': 'capture-original', 'source_sha256': current['source_sha256']}, authorize=allow)
    return receipt['revision']['revision_id']


def restore_body(preview):
    return {'command_key': 'restore-original', 'preview_sha256': preview['preview_sha256'],
        'confirm_apply': True, 'confirm_permission_increase': True}


def test_restoration_is_one_original_commit_and_cold_same_key_never_reapplies(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    dependency = store.model_configuration_dependency('alpha', 'model-one', authorize=allow)
    ident = captured(store)
    changed = {k: v for k, v in profile().items() if k != 'api_key'}
    store.upsert_profile('alpha', {**changed, 'temperature': 0.8, 'max_tokens': 500})
    assert store.model_configuration_dependency('alpha', 'model-one', authorize=allow) != dependency
    metadata = tmp_path/'alpha'/'models.json'
    before = json.loads(metadata.read_text('utf-8'))
    secret = store._secret_path('alpha', before)
    secret_before = secret.read_bytes()
    preview = store.preview_model_configuration('alpha', 'model-one', ident, authorize=allow)
    assert preview['compatible'] is True
    assert preview['permission_increases']
    body = restore_body(preview)
    with pytest.raises(PyHError):
        store.apply_model_configuration('alpha', 'model-one', ident,
            {**body, 'confirm_permission_increase': False}, authorize=allow)
    assert json.loads(metadata.read_text('utf-8')) == before
    result = store.apply_model_configuration('alpha', 'model-one', ident, body, authorize=allow)
    assert result['status'] == 'applied'
    assert store.active_profile('alpha')['temperature'] == 0.2
    assert store.active_profile('alpha')['max_tokens'] == 1000
    assert json.loads(metadata.read_text('utf-8'))['secret_generation'] == before['secret_generation']
    assert secret.read_bytes() == secret_before
    assert store.resolve_ref('tenant:alpha:model-one') == profile()['api_key']
    assert store.model_configuration_dependency('alpha', 'model-one', authorize=allow) == dependency
    committed = metadata.read_bytes()
    restored = TenantSettingsStore(tmp_path)
    assert restored.model_configuration_command('alpha', body['command_key'], authorize=allow) == result
    assert restored.apply_model_configuration('alpha', 'model-one', ident, body, authorize=allow) == result
    assert metadata.read_bytes() == committed
    with pytest.raises(PyHError):
        restored.apply_model_configuration('alpha', 'model-one', ident,
            {**body, 'command_key': 'new-key-old-preview'}, authorize=allow)
    assert metadata.read_bytes() == committed


def test_rotated_credential_is_not_replaced_by_the_current_secret_to_restore_history(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    ident = captured(store)
    store.upsert_profile('alpha', {**profile(), 'api_key': 'synthetic-new-e04-secret'})
    metadata = tmp_path/'alpha'/'models.json'
    before = metadata.read_bytes()
    preview = store.preview_model_configuration('alpha', 'model-one', ident, authorize=allow)
    assert preview['compatible'] is False
    assert preview['issues']
    with pytest.raises(PyHError):
        store.apply_model_configuration('alpha', 'model-one', ident, restore_body(preview), authorize=allow)
    assert metadata.read_bytes() == before
    history = store.model_configuration_history('alpha', 'model-one', authorize=allow)
    encoded = json.dumps(history)
    assert profile()['api_key'] not in encoded and 'synthetic-new-e04-secret' not in encoded
    assert profile()['api_key'].encode() not in before and b'synthetic-new-e04-secret' not in before
    assert store.resolve_ref('tenant:alpha:model-one') == 'synthetic-new-e04-secret'


def test_denied_authority_and_boolean_confusion_leave_original_metadata_unchanged(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    ident = captured(store)
    metadata = tmp_path/'alpha'/'models.json'
    before = metadata.read_bytes()
    def denied():
        raise PermissionError('explicitly denied test authority')
    with pytest.raises(PermissionError):
        store.model_configuration_history('alpha', 'model-one', authorize=denied)
    preview = store.preview_model_configuration('alpha', 'model-one', ident, authorize=allow)
    with pytest.raises(PyHError):
        store.apply_model_configuration('alpha', 'model-one', ident,
            {**restore_body(preview), 'confirm_apply': 1}, authorize=allow)
    assert metadata.read_bytes() == before


@pytest.mark.parametrize('withdraw_at', [3, 4])
def test_withdrawal_before_or_after_the_actual_commit_is_observed_without_replay(tmp_path, withdraw_at):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    ident = captured(store)
    preview = store.preview_model_configuration('alpha', 'model-one', ident, authorize=allow)
    body = restore_body(preview)
    metadata = tmp_path/'alpha'/'models.json'
    before = metadata.read_bytes()
    calls = 0
    def withdraw():
        nonlocal calls
        calls += 1
        if calls >= withdraw_at:
            raise PermissionError('authority withdrawn')
    with pytest.raises(PermissionError):
        store.apply_model_configuration('alpha', 'model-one', ident, body, authorize=withdraw)
    if withdraw_at == 3:
        assert metadata.read_bytes() == before
        with pytest.raises(PyHError):
            store.model_configuration_command('alpha', body['command_key'], authorize=allow)
    else:
        committed = metadata.read_bytes()
        assert committed != before
        result = store.model_configuration_command('alpha', body['command_key'], authorize=allow)
        assert result['status'] == 'applied'
        assert store.apply_model_configuration('alpha', 'model-one', ident, body, authorize=allow) == result
        assert metadata.read_bytes() == committed


def test_two_real_stores_capture_one_command_and_another_tenant_cannot_query_it(tmp_path):
    first, second = TenantSettingsStore(tmp_path), TenantSettingsStore(tmp_path)
    first.upsert_profile('alpha', profile())
    first.upsert_profile('beta', profile())
    other_metadata = tmp_path/'beta'/'models.json'
    other_before = other_metadata.read_bytes()
    current = first.model_configuration_current('alpha', 'model-one', authorize=allow)
    body = {'command_key': 'shared-capture', 'source_sha256': current['source_sha256']}
    start = threading.Barrier(2)
    def capture(store):
        start.wait(timeout=10)
        return store.capture_model_configuration('alpha', 'model-one', body, authorize=allow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(capture, first), pool.submit(capture, second)
        assert a.result(timeout=20) == b.result(timeout=20)
    assert len(first.model_configuration_history('alpha', 'model-one', authorize=allow)['revisions']) == 1
    with pytest.raises(PyHError):
        second.model_configuration_command('beta', body['command_key'], authorize=allow)
    assert other_metadata.read_bytes() == other_before


@pytest.mark.controlled_process
def test_independent_interpreter_cannot_enter_the_original_model_transaction(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    before = (tmp_path/'alpha'/'models.json').read_bytes()
    script = '''import json,sys
from pathlib import Path
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.errors import PyHError
try:
 TenantSettingsStore(Path(sys.argv[1])).model_configuration_current('alpha','model-one',authorize=lambda:None)
except PyHError as error:
 print(json.dumps({'result':error.code}))
else:
 print(json.dumps({'result':'available'}))
'''
    def attempt():
        child = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, str(tmp_path)],
            capture_output=True, text=True, encoding='utf-8', timeout=20)
        assert child.returncode == 0, child.stderr
        return json.loads(child.stdout)['result']
    with store._transaction('alpha'):
        assert attempt() == 'CRED-703'
    assert attempt() == 'available'
    assert (tmp_path/'alpha'/'models.json').read_bytes() == before


def test_corrupt_revision_is_rejected_instead_of_becoming_a_new_restore_authority(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    ident = captured(store)
    metadata = tmp_path/'alpha'/'models.json'
    doc = json.loads(metadata.read_text('utf-8'))
    doc['configuration_history']['revisions'][ident]['snapshot']['temperature'] = 1.4
    metadata.write_text(json.dumps(doc), encoding='utf-8')
    before = metadata.read_bytes()
    with pytest.raises(PyHError):
        store.preview_model_configuration('alpha', 'model-one', ident, authorize=allow)
    assert metadata.read_bytes() == before


def test_public_configuration_text_never_exports_a_known_tenant_secret(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    before = (tmp_path/'alpha'/'models.json').read_bytes()
    with pytest.raises(PyHError):
        store.configuration_public_metadata('alpha', {'prompt': profile()['api_key']}, authorize=allow)
    assert store.configuration_public_metadata('alpha', {'prompt': 'Public instructions'}, authorize=allow) == {'prompt': 'Public instructions'}
    assert (tmp_path/'alpha'/'models.json').read_bytes() == before


def test_base_policy_fingerprint_omits_secret_values_but_retains_real_parameter_limits(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    first = {'llm': {'api_key': 'first-direct-value', 'max_tokens': 3000},
             'shell': {'token': 'first-shell-value'}}
    second = {'llm': {'api_key': 'second-direct-value', 'max_tokens': 3000},
              'shell': {'token': 'second-shell-value'}}
    digest = store.configuration_base_fingerprint('alpha', first, authorize=allow)
    assert store.configuration_base_fingerprint('alpha', second, authorize=allow) == digest
    second['llm']['max_tokens'] = 4000
    assert store.configuration_base_fingerprint('alpha', second, authorize=allow) != digest


def test_dependency_view_is_read_only_and_cannot_outlive_the_original_transaction(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    before = (tmp_path/'alpha'/'models.json').read_bytes()
    with store.configuration_dependency_view('alpha', authorize=allow) as view:
        assert view.model()['profile_id'] == 'model-one'
        assert view.model()['credential_status'] == 'valid'
        assert view.public({'prompt': 'Public instructions'}) == {'prompt': 'Public instructions'}
        with pytest.raises(PyHError):
            view.public({'prompt': profile()['api_key']})
    with pytest.raises(PyHError):
        view.model()
    assert (tmp_path/'alpha'/'models.json').read_bytes() == before


def test_service_secret_has_an_opaque_revision_while_original_legacy_resolution_still_works(tmp_path):
    store = TenantSettingsStore(tmp_path)
    endpoint = 'http://127.0.0.1:1'
    reference = store.set_service_secret('alpha', 'remote_rag:fixture', endpoint, 'synthetic-service-first')
    with store.configuration_dependency_view('alpha', authorize=allow) as view:
        first = view.service(reference, endpoint)
        assert first['credential_status'] == 'valid'
        assert view.service(reference.replace('alpha', 'beta'), endpoint)['credential_status'] != 'valid'
    store.set_service_secret('alpha', 'remote_rag:fixture', endpoint, 'synthetic-service-second')
    with store.configuration_dependency_view('alpha', authorize=allow) as view:
        assert view.service(reference, endpoint) != first
    assert store.resolve_service_secret(reference, 'alpha', endpoint) == 'synthetic-service-second'
    with store._transaction('alpha'):
        doc = store._load_doc('alpha')
        doc['service_credentials']['remote_rag:fixture'].pop('credential_revision')
        store._save_doc('alpha', doc)
    assert store.resolve_service_secret(reference, 'alpha', endpoint) == 'synthetic-service-second'
    with store.configuration_dependency_view('alpha', authorize=allow) as view:
        assert view.service(reference, endpoint)['credential_status'] == 'unversioned_reference'


def test_recent_commands_are_bounded_scoped_and_future_history_schema_is_refused(tmp_path):
    store = TenantSettingsStore(tmp_path)
    store.upsert_profile('alpha', profile())
    store.upsert_profile('alpha', {**profile(), 'id': 'other-model'})
    source = store.model_configuration_current('alpha', 'model-one', authorize=allow)['source_sha256']
    for index in range(23):
        store.capture_model_configuration('alpha', 'model-one', {'command_key': 'bounded-'+str(index),
            'source_sha256': source}, authorize=allow)
    other = store.model_configuration_current('alpha', 'other-model', authorize=allow)['source_sha256']
    store.capture_model_configuration('alpha', 'other-model', {'command_key': 'other-resource',
        'source_sha256': other}, authorize=allow)
    history = store.model_configuration_history('alpha', 'model-one', authorize=allow)
    assert len(history['revisions']) == 23 and len(history['commands']) == 20
    assert [r['command_key'] for r in history['commands']] == ['bounded-'+str(i) for i in range(22, 2, -1)]
    assert all(r['created_at'] and r['historical_result'] is True for r in history['commands'])
    with store._transaction('alpha'):
        doc = store._load_doc('alpha')
        doc['configuration_history']['schema_version'] = 2
        store._save_doc('alpha', doc)
    before = (tmp_path/'alpha'/'models.json').read_bytes()
    with pytest.raises(PyHError):
        store.model_configuration_history('alpha', 'model-one', authorize=allow)
    assert (tmp_path/'alpha'/'models.json').read_bytes() == before
