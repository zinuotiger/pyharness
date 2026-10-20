"""Metadata-only compilation of the exact published execution configuration."""
from types import SimpleNamespace
from pyharness.application.execution_config import compile_execution
from pyharness.application.workspace_store import canonical, digest_of


def compiled_configuration(projects, agent_ref):
    service = projects.service
    version = service.platform.new_binding_version(agent_ref.agent_id, agent_ref.version)
    snapshot = version['snapshot']
    event = SimpleNamespace(type='user.message', payload=dict(meta=dict(platform=dict(definition=snapshot))))
    cfg, _ = compile_execution(service, SimpleNamespace(events_after=lambda _: iter([event])))
    return snapshot, cfg


def describe_execution(projects, agent_ref):
    snapshot, cfg = compiled_configuration(projects, agent_ref)
    return dict(agent_snapshot=snapshot, model_fingerprint=digest_of(canonical(cfg.model_dump(mode='json'))),
        boundary={'disabled':'disabled', 'host_approved':'host_unrestricted', 'isolated':'container', 'remote':'remote'}[snapshot['sandbox_profile']['mode']])
