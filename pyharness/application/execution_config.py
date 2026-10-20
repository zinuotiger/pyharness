"""Compile published application policy to engine configuration in one place."""
from __future__ import annotations
from typing import TYPE_CHECKING
from pyharness.application.platform_models import AgentDefinition
from pyharness.config import Settings
from pyharness.core.session import SessionLog
from pyharness.errors import raise_code
if TYPE_CHECKING:
    from pyharness.application.service import ApplicationService


def compile_execution(service: ApplicationService, log_: SessionLog) -> tuple[Settings, AgentDefinition | None]:
    cfg = service.effective_settings()
    from pyharness.application.platform_service import PlatformService
    platform_binding = PlatformService.binding(log_)
    platform_definition = None
    if platform_binding:
        from pyharness.application.platform_models import AgentDefinition
        platform_definition = AgentDefinition.model_validate(platform_binding['definition'])
        service.platform.validate_execution_policy(platform_definition)
        cfg.loop.max_turns = platform_definition.max_rounds
        cfg.budget.task.max_in_tokens = platform_definition.budget.input_tokens
        cfg.budget.task.max_out_tokens = platform_definition.budget.output_tokens
        cfg.budget.task.max_cost_yuan = platform_definition.budget.cost
        cfg.security.policy.preset = platform_definition.permission_policy
        cfg.plugins.enabled = []
        cfg.plugins.mcp_servers = []
        if platform_definition.mcp_connections:
            from pyharness.config import McpServerCfg
            configured=service.platform.connection_definitions()
            for name in platform_definition.mcp_connections:
                connection=configured.get('mcp:'+name)
                if connection is None or not connection['config']['enabled']:
                    raise_code('CFG-601',reason='agent_mcp_connection_disabled')
                cfg.plugins.mcp_servers.append(McpServerCfg.model_validate(connection['config']))
        if platform_definition.executor == 'codex':
            cfg.llm.fallback_models = []
        elif platform_definition.model_connection:
            profiles = service.tenant_state()['profiles']
            profile = next((p for p in profiles if p['id'] == platform_definition.model_connection), None)
            if profile is None:
                raise_code('CRED-701', reason='agent_model_connection_missing')
            from pyharness.application.model_profile_configuration import profile_values
            from pyharness.application.session_access import _session_module
            defaults = _session_module()._cfg_of(service.ctx).llm.model_dump()
            for name, value in profile_values(profile, service.tenant_id, defaults).items():
                setattr(cfg.llm, name, value)
            cfg.llm.fallback_models = []
    return cfg, platform_definition
