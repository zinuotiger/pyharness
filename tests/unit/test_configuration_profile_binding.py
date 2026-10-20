"""Published Agent configuration comes from its selected model profile."""
import pytest
from pyharness import cli
from pyharness.application.service import ApplicationService
from pyharness.application.execution_config import compile_execution
from tests.support import isolated_settings


@pytest.mark.parametrize('tenant', ['default', 'e04team'])
@pytest.mark.parametrize('legacy', [False, True])
async def test_selected_profile_parameters_do_not_inherit_an_unrelated_active_profile(tmp_path, tenant, legacy):
    cfg = isolated_settings(tmp_path)
    cfg.llm.temperature = 0.35
    cfg.llm.max_tokens = 5555
    original = cfg.model_dump()
    service = ApplicationService(cli.assemble_ctx(cfg), tenant_id=tenant)
    try:
        for ident, temperature, limit in [('active', 1.2, 777), ('selected', 0.2, 3333)]:
            service.save_model_profile({'id': ident, 'model': 'synthetic-'+ident,
                'base_url': 'http://127.0.0.1:1/'+ident,
                'api_key': 'synthetic-e04-'+ident,
                'temperature': temperature, 'max_tokens': limit,
                'fallback_models': ['synthetic-fallback-'+ident]})
        service.activate_model_profile('active')
        if legacy:
            with service.credentials._transaction(tenant):
                doc = service.credentials._load_doc(tenant)
                selected = next(p for p in doc['profiles'] if p['id'] == 'selected')
                selected.pop('temperature')
                selected.pop('max_tokens')
                service.credentials._save_doc(tenant, doc)
        platform = service.platform
        agent = platform.save_agent({'name': 'Selected configuration fixture',
            'model_connection': 'selected', 'sandbox_profile': {'mode': 'disabled'}})
        version = platform.publish_agent(agent['agent_id'])
        sid = await service.create_session()
        log = await service.require_session(sid)
        await log.append('user.message', {'content': 'Metadata compilation only.',
            'meta': {'platform': {'definition': version['snapshot']}}}, actor='user')
        compiled, definition = compile_execution(service, log)
        assert definition.model_connection == 'selected'
        assert compiled.llm.model == 'synthetic-selected'
        assert compiled.llm.base_url == 'http://127.0.0.1:1/selected'
        assert compiled.llm.api_key == 'tenant:'+tenant+':selected'
        assert compiled.llm.fallback_models == []  # Original formal execution policy.
        assert compiled.llm.temperature == (0.35 if legacy else 0.2)
        assert compiled.llm.max_tokens == (5555 if legacy else 3333)
        assert service.effective_settings().llm.temperature == 1.2
        assert service.effective_settings().llm.max_tokens == 777
        assert cfg.model_dump() == original
        assert not service._engines and not service._queues
    finally:
        await service.shutdown()
