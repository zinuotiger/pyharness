"""Agent rollback references original versions and creates a new publication."""
import copy
import json
import sys
import subprocess
import asyncio
import httpx
from types import SimpleNamespace
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings


def allow():
    return None


@pytest.fixture
async def resource(tmp_path, monkeypatch):
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(cli.assemble_ctx(cfg))
    effects = {'http_clients': 0, 'processes': 0, 'providers': 0}
    def no_client(*args, **kwargs):
        effects['http_clients'] += 1
        raise AssertionError('Configuration metadata must not construct a network client')
    async def no_process(*args, **kwargs):
        effects['processes'] += 1
        raise AssertionError('Configuration metadata must not start a process')
    async def no_provider(*args, **kwargs):
        effects['providers'] += 1
        raise AssertionError('Configuration metadata must not invoke a Provider')
    from pyharness.core.tool_invocation import ToolInvocation
    monkeypatch.setattr(httpx, 'AsyncClient', no_client)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', no_process)
    monkeypatch.setattr(ToolInvocation, '_run_original_provider', no_provider)
    profile = {'id': 'synthetic', 'model': cfg.llm.model,
        'base_url': 'http://127.0.0.1:1', 'api_key': 'synthetic-agent-history-secret',
        'temperature': 0.2, 'max_tokens': 1000}
    app.service.save_model_profile(profile)
    platform = app.service.platform
    draft = platform.save_agent({'name': 'Original broader policy', 'permission_policy': 'standard',
        'allowed_tools': ['skill.load'], 'max_rounds': 12, 'model_connection': 'synthetic'})
    original = platform.publish_agent(draft['agent_id'])
    try:
        yield SimpleNamespace(app=app, cfg=cfg, profile=profile, platform=platform,
            ident=draft['agent_id'], original=original)
    finally:
        await app.service.shutdown()
        assert effects == {'http_clients': 0, 'processes': 0, 'providers': 0}


def bench(resource):
    from pyharness.application.agent_configuration_history import AgentConfigurationHistory
    return AgentConfigurationHistory(resource.platform)


def capture(resource):
    owner = bench(resource)
    current = owner.current(resource.ident, authorize=allow)
    receipt = owner.capture(resource.ident, {'command_key': 'agent-capture',
        'source_sha256': current['source_sha256']}, authorize=allow)
    return owner, receipt['revision']['revision_id']


def narrow(resource):
    resource.platform.save_agent({**resource.original['snapshot'], 'name': 'Current narrow policy',
        'permission_policy': 'strict', 'allowed_tools': [], 'max_rounds': 4}, resource.ident)
    return resource.platform.publish_agent(resource.ident)


def body(preview):
    return {'command_key': 'agent-restore', 'preview_sha256': preview['preview_sha256'],
        'confirm_apply': True, 'confirm_permission_increase': True}


async def test_restore_publishes_version_three_and_cold_same_key_never_publishes_four(resource):
    owner, ident = capture(resource)
    second = narrow(resource)
    first_before, second_before = copy.deepcopy(resource.original), copy.deepcopy(second)
    path = resource.platform.root/'configuration.json'
    preview = owner.preview(resource.ident, ident, authorize=allow)
    assert preview['compatible'] is True and preview['permission_increases']
    before = path.read_bytes()
    models = resource.app.service.tenant_root()/'models.json'
    models_before = models.read_bytes()
    with pytest.raises(PyHError):
        owner.apply(resource.ident, ident, {**body(preview), 'confirm_permission_increase': False}, authorize=allow)
    assert path.read_bytes() == before
    result = owner.apply(resource.ident, ident, body(preview), authorize=allow)
    assert result['status'] == 'applied' and result['agent_ref']['version'] == 3
    assert resource.platform.version(resource.ident, 1) == first_before
    assert resource.platform.version(resource.ident, 2) == second_before
    assert resource.platform.version(resource.ident, 3)['snapshot']['permission_policy'] == 'standard'
    assert models.read_bytes() == models_before
    assert not resource.app.service._engines and not resource.app.service._queues
    committed = path.read_bytes()
    from pyharness.application.agent_configuration_history import AgentConfigurationHistory
    cold = DesktopApp(cli.assemble_ctx(resource.cfg))
    try:
        restored = AgentConfigurationHistory(cold.service.platform)
        assert restored.command(body(preview)['command_key'], authorize=allow) == result
        assert restored.apply(resource.ident, ident, body(preview), authorize=allow) == result
        assert path.read_bytes() == committed
    finally:
        await cold.service.shutdown()


@pytest.mark.parametrize('change', ['secret', 'draft', 'deprecated'])
async def test_incompatible_credential_draft_or_retired_version_never_overwrites_current_state(resource, change):
    owner, ident = capture(resource)
    narrow(resource)
    if change == 'secret':
        resource.app.service.save_model_profile({**resource.profile, 'api_key': 'synthetic-rotated-agent-secret'})
    elif change == 'draft':
        resource.platform.save_agent({**resource.original['snapshot'], 'name': 'Unpublished user draft'}, resource.ident)
    else:
        resource.platform.agent_action(resource.ident, 'deprecate-version', {'version': 1})
    path = resource.platform.root/'configuration.json'
    before = path.read_bytes()
    preview = owner.preview(resource.ident, ident, authorize=allow)
    assert preview['compatible'] is False and preview['issues']
    with pytest.raises(PyHError):
        owner.apply(resource.ident, ident, body(preview), authorize=allow)
    assert path.read_bytes() == before
    serialized = json.dumps(owner.history(resource.ident, authorize=allow))
    assert resource.profile['api_key'] not in serialized and 'synthetic-rotated-agent-secret' not in serialized


async def test_stale_preview_and_refused_authority_do_not_modify_configuration(resource):
    owner, ident = capture(resource)
    narrow(resource)
    preview = owner.preview(resource.ident, ident, authorize=allow)
    resource.platform.save_agent({**resource.original['snapshot'], 'name': 'Another current policy'}, resource.ident)
    resource.platform.publish_agent(resource.ident)
    path = resource.platform.root/'configuration.json'
    before = path.read_bytes()
    with pytest.raises(PyHError):
        owner.apply(resource.ident, ident, body(preview), authorize=allow)
    def denied():
        raise PermissionError('authority withdrawn')
    with pytest.raises(PermissionError):
        owner.history(resource.ident, authorize=denied)
    assert path.read_bytes() == before


async def test_restoring_exact_model_parameters_reestablishes_the_original_agent_dependency(resource):
    store = resource.app.service.credentials
    current = store.model_configuration_current('default', 'synthetic', authorize=allow)
    model = store.capture_model_configuration('default', 'synthetic',
        {'command_key': 'capture-model-for-agent', 'source_sha256': current['source_sha256']}, authorize=allow)
    owner, ident = capture(resource)
    narrow(resource)
    changed = {k: v for k, v in resource.profile.items() if k != 'api_key'}
    store.upsert_profile('default', {**changed, 'temperature': 0.9, 'max_tokens': 500})
    assert owner.preview(resource.ident, ident, authorize=allow)['compatible'] is False
    model_id = model['revision']['revision_id']
    preview = store.preview_model_configuration('default', 'synthetic', model_id, authorize=allow)
    store.apply_model_configuration('default', 'synthetic', model_id,
        {**body(preview), 'command_key': 'restore-model-for-agent'}, authorize=allow)
    assert owner.preview(resource.ident, ident, authorize=allow)['compatible'] is True


async def test_skill_dependency_reads_fresh_physical_content_without_reloading_formal_engines(resource):
    path = resource.app.service.skill_catalog_root/'sample'/'SKILL.md'
    path.parent.mkdir(parents=True)
    original = b'---\nname: sample\ndescription: captured dependency\n---\nORIGINAL-SKILL'
    path.write_bytes(original)
    resource.platform.save_agent({**resource.original['snapshot'], 'skills': ['sample']}, resource.ident)
    resource.platform.publish_agent(resource.ident)
    owner, ident = capture(resource)
    narrow(resource)
    assert owner.preview(resource.ident, ident, authorize=allow)['compatible'] is True
    path.write_bytes(original.replace(b'ORIGINAL-SKILL', b'CHANGED-SKILL'))
    assert owner.preview(resource.ident, ident, authorize=allow)['compatible'] is False
    path.write_bytes(original)
    assert owner.preview(resource.ident, ident, authorize=allow)['compatible'] is True
    assert not resource.app.service._engines


async def test_managed_mcp_configuration_change_is_detected_without_starting_a_process(resource):
    config = {'name': 'fixture', 'command': [sys.executable, '-B', 'UNEXECUTED-CONFIGURATION-FIXTURE.py'],
              'timeout_s': 2}
    resource.platform.save_connection({'type': 'mcp', 'config': config})
    resource.platform.save_agent({**resource.original['snapshot'], 'mcp_connections': ['fixture'],
        'sandbox_profile': {'mode': 'host_approved'}}, resource.ident)
    resource.platform.publish_agent(resource.ident)
    owner, ident = capture(resource)
    narrow(resource)
    preview = owner.preview(resource.ident, ident, authorize=allow)
    assert preview['compatible'] is True
    assert 'mcp_process_and_protocol' in preview['runtime_checks']
    assert preview['runtime_checks_verified'] is False
    resource.platform.save_connection({'type': 'mcp', 'config': {**config, 'timeout_s': 3}})
    assert owner.preview(resource.ident, ident, authorize=allow)['compatible'] is False
    assert not resource.platform.connection_clients and not resource.platform.actions


async def test_knowledge_provider_and_service_credential_generations_are_bound_without_network(resource):
    source = await resource.platform.add_knowledge({'name': 'Local dependency', 'text': 'Owned synthetic evidence'})
    resource.platform.save_agent({**resource.original['snapshot'], 'knowledge_sources': [source['source_id']],
        'require_evidence': True}, resource.ident)
    resource.platform.publish_agent(resource.ident)
    owner, local = capture(resource)
    config = {'type': 'remote_rag', 'name': 'fixture', 'endpoint': 'http://127.0.0.1:1',
              'bearer_token': 'synthetic-knowledge-secret-one'}
    remote = resource.platform.save_connection(config)['connection_id']
    with resource.platform.store.transaction() as data:
        data['connections'][remote]['config']['enabled'] = True
    resource.platform.knowledge_action('', 'provider', {'provider': remote})
    assert owner.preview(resource.ident, local, authorize=allow)['compatible'] is False
    current = owner.current(resource.ident, authorize=allow)
    saved = owner.capture(resource.ident, {'command_key': 'capture-remote-knowledge',
        'source_sha256': current['source_sha256']}, authorize=allow)
    remote_revision = saved['revision']['revision_id']
    assert owner.preview(resource.ident, remote_revision, authorize=allow)['compatible'] is True
    resource.platform.save_connection({**config, 'bearer_token': 'synthetic-knowledge-secret-two'})
    assert owner.preview(resource.ident, remote_revision, authorize=allow)['compatible'] is False
    serialized = json.dumps(owner.history(resource.ident, authorize=allow))
    assert 'synthetic-knowledge-secret-one' not in serialized
    assert 'synthetic-knowledge-secret-two' not in serialized


async def test_remote_reference_uses_original_versioned_blob_but_never_connects_or_exports_the_token(resource):
    token = 'synthetic-remote-token-'+'x'*40
    fields = {'name': 'Owned remote', 'address': 'http://127.0.0.1:1', 'token': token}
    remote = resource.platform.chat_setup.save_remote(fields)
    resource.platform.save_agent({**resource.original['snapshot'],
        'sandbox_profile': {'mode': 'remote', 'remote_connection': remote['id']}}, resource.ident)
    resource.platform.publish_agent(resource.ident)
    owner, ident = capture(resource)
    narrow(resource)
    preview = owner.preview(resource.ident, ident, authorize=allow)
    assert preview['compatible'] is True and preview['runtime_checks_verified'] is False
    resource.platform.chat_setup.save_remote({**fields, 'token': 'synthetic-remote-token-'+'y'*40})
    assert owner.preview(resource.ident, ident, authorize=allow)['compatible'] is False
    assert token not in json.dumps(owner.history(resource.ident, authorize=allow))


async def test_account_scoped_authority_cannot_borrow_local_operator_host_capabilities(resource):
    resource.platform.save_agent({**resource.original['snapshot'],
        'sandbox_profile': {'mode': 'host_approved'}}, resource.ident)
    resource.platform.publish_agent(resource.ident)
    owner, ident = capture(resource)
    narrow(resource)
    account = lambda: {'mode': 'tenant_account', 'tenant': 'default', 'role': 'owner'}
    preview = owner.preview(resource.ident, ident, authorize=account)
    assert preview['compatible'] is False
    assert 'account_host_execution_forbidden' in preview['issues']
    before = (resource.platform.root/'configuration.json').read_bytes()
    with pytest.raises(PyHError):
        owner.apply(resource.ident, ident, body(preview), authorize=account)
    assert (resource.platform.root/'configuration.json').read_bytes() == before


@pytest.mark.controlled_process
async def test_model_lease_covers_the_actual_platform_commit_then_releases(resource, monkeypatch):
    from pyharness.core.tenant_settings import TenantSettingsStore
    owner, ident = capture(resource)
    narrow(resource)
    preview = owner.preview(resource.ident, ident, authorize=allow)
    settings_root = resource.app.service.credentials.root
    metadata = resource.app.service.tenant_root()/'models.json'
    before = metadata.read_bytes()
    script = '''import json,sys
from pathlib import Path
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.errors import PyHError
try:
 TenantSettingsStore(Path(sys.argv[1])).upsert_profile('default',json.loads(sys.argv[2]))
except PyHError as error:
 print(json.dumps({'result':error.code}))
else:
 print(json.dumps({'result':'written'}))
'''
    def rotate():
        child = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
            str(settings_root), json.dumps({**resource.profile, 'api_key': 'synthetic-child-rotation'})],
            capture_output=True, text=True, encoding='utf-8', timeout=20)
        assert child.returncode == 0, child.stderr
        return json.loads(child.stdout)['result']
    original = TenantSettingsStore._atomic_write
    observed = []
    def commit(path, data):
        if path == resource.platform.root/'configuration.json':
            observed.append(rotate())
            assert metadata.read_bytes() == before
        return original(path, data)
    monkeypatch.setattr(TenantSettingsStore, '_atomic_write', staticmethod(commit))
    assert owner.apply(resource.ident, ident, body(preview), authorize=allow)['status'] == 'applied'
    assert observed == ['CRED-703'] and metadata.read_bytes() == before
    assert rotate() == 'written'
    assert metadata.read_bytes() != before


@pytest.mark.controlled_process
async def test_actual_agent_history_obeys_the_original_cross_process_platform_lock(resource):
    script = '''import asyncio,json,sys
from pathlib import Path
from pyharness import cli
from pyharness.config import Settings
from pyharness.desktop.app import DesktopApp
from pyharness.application.agent_configuration_history import AgentConfigurationHistory
from pyharness.errors import PyHError
async def main():
 app=DesktopApp(cli.assemble_ctx(Settings.model_validate_json(Path(sys.argv[1]).read_text('utf-8'))))
 try:
  try: AgentConfigurationHistory(app.service.platform).current(sys.argv[2],authorize=lambda:None)
  except PyHError as error: print(json.dumps({'result':error.code}))
  else: print(json.dumps({'result':'available'}))
 finally: await app.service.shutdown()
asyncio.run(main())
'''
    config = resource.platform.root.parent/'owned-child-settings.json'
    config.write_text(resource.cfg.model_dump_json(), encoding='utf-8')
    before = (resource.platform.root/'configuration.json').read_bytes()
    def attempt():
        child = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
            str(config), resource.ident], capture_output=True, text=True, encoding='utf-8', timeout=20)
        assert child.returncode == 0, child.stderr
        return json.loads(child.stdout)['result']
    with resource.platform.store.transaction():
        assert attempt() == 'platform_busy'
    assert attempt() == 'available'
    assert (resource.platform.root/'configuration.json').read_bytes() == before


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'escape'])
async def test_remote_blob_damage_or_forged_reference_rejects_restore_without_outside_read(resource, damage, monkeypatch):
    remote = resource.platform.chat_setup.save_remote({'name': 'Bound remote',
        'address': 'http://127.0.0.1:1', 'token': 'synthetic-owned-remote-'+'z'*40})
    resource.platform.save_agent({**resource.original['snapshot'],
        'sandbox_profile': {'mode': 'remote', 'remote_connection': remote['id']}}, resource.ident)
    resource.platform.publish_agent(resource.ident)
    owner, ident = capture(resource)
    narrow(resource)
    with resource.platform.store.transaction() as data:
        row = data['preferences']['remote_connections'][remote['id']]
        path = resource.platform.root/('remote-'+row['secret_id']+'.bin')
        assert path.resolve().is_relative_to(resource.platform.root.resolve())
        if damage == 'missing': path.unlink()
        elif damage == 'corrupt': path.write_bytes(b'not-an-original-credential-blob')
        else: row['secret_id'] = '../outside-owned-sentinel'
    from pyharness.core import sandbox
    original = sandbox.read_regular
    reads = []
    def read(path, limit):
        reads.append(path.resolve())
        assert path.resolve().is_relative_to(resource.platform.root.resolve())
        return original(path, limit)
    monkeypatch.setattr(sandbox, 'read_regular', read)
    before = (resource.platform.root/'configuration.json').read_bytes()
    preview = owner.preview(resource.ident, ident, authorize=allow)
    assert preview['compatible'] is False and 'remote_credential_not_active' in preview['issues']
    with pytest.raises(PyHError):
        owner.apply(resource.ident, ident, body(preview), authorize=allow)
    assert (resource.platform.root/'configuration.json').read_bytes() == before
    if damage == 'escape': assert reads == []


async def test_authority_loss_after_agent_commit_leaves_one_queryable_receipt_without_republishing(resource, monkeypatch):
    from pyharness.core.tenant_settings import TenantSettingsStore
    owner, ident = capture(resource)
    narrow(resource)
    preview = owner.preview(resource.ident, ident, authorize=allow)
    original, committed = TenantSettingsStore._atomic_write, False
    def commit(path, data):
        nonlocal committed
        result = original(path, data)
        if path == resource.platform.root/'configuration.json': committed = True
        return result
    def authority():
        if committed: raise PermissionError('authority lost after actual platform commit')
    monkeypatch.setattr(TenantSettingsStore, '_atomic_write', staticmethod(commit))
    with pytest.raises(PermissionError):
        owner.apply(resource.ident, ident, body(preview), authorize=authority)
    before = (resource.platform.root/'configuration.json').read_bytes()
    receipt = owner.command(body(preview)['command_key'], authorize=allow)
    assert receipt['agent_ref']['version'] == 3
    assert owner.apply(resource.ident, ident, body(preview), authorize=allow) == receipt
    assert (resource.platform.root/'configuration.json').read_bytes() == before
