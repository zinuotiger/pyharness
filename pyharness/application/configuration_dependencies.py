"""Read declared dependencies under the original platform and model locks."""
from __future__ import annotations
import hashlib
from urllib.parse import urlsplit
from pyharness.application.session_access import _session_module
from pyharness.config import McpServerCfg
from pyharness.core import configuration_revision_contracts as c
from pyharness.core.skill import SkillManager
from pyharness.errors import PyHError


def dependencies(platform, data, definition, model_view):
    service = platform.service
    cfg = _session_module()._cfg_of(service.ctx)
    result = {'base_public_fingerprint': None, 'models': None, 'mcp': {},
              'skills': {}, 'knowledge': {}, 'knowledge_provider': None,
              'remote': None, 'runtime_checks': []}
    issues = []
    if definition.executor == 'engine':
        result['models'] = model_view.model(definition.model_connection)
        if result['models']['credential_status'] != 'valid':
            issues.append('model_credential_'+result['models']['credential_status'])
    else:
        # The original Codex host authorization remains a fresh task preflight.
        # No CLI authentication or process execution is attempted by a preview.
        result['runtime_checks'].append('codex_host_authority_and_authentication')
    if definition.sandbox_profile.mode == 'remote':
        record = data['preferences'].get('remote_connections', {}).get(definition.sandbox_profile.remote_connection)
        result['remote'] = model_view.remote(platform.root, record)
        if result['remote']['credential_status'] != 'valid':
            issues.append('remote_credential_not_active')
        result['runtime_checks'].append('remote_sandbox_authentication_and_availability')
    for name in definition.mcp_connections:
        row = data['connections'].get('mcp:'+name)
        try:
            if not row or row.get('type') != 'mcp':
                raise ValueError('missing managed connection')
            config = McpServerCfg.model_validate(row['config']).model_dump()
            if not config['enabled']:
                raise ValueError('disabled managed connection')
            result['mcp'][name] = c.digest(config)
            if 'mcp_process_and_protocol' not in result['runtime_checks']:
                result['runtime_checks'].append('mcp_process_and_protocol')
        except (ValueError, TypeError, KeyError):
            issues.append('mcp_dependency_unavailable')
    if definition.skills:
        try:
            manager = SkillManager([service.skill_catalog_root]).frozen_copy(definition.skills)
            for name in definition.skills:
                snapshot = manager.snapshot(name)
                result['skills'][name] = {key: snapshot[key] for key in ('source_sha256', 'loaded_sha256')}
        except (PyHError, ValueError, TypeError, OSError):
            issues.append('skill_dependency_unavailable')
    for ident in definition.knowledge_sources:
        row = data['knowledge'].get(ident)
        if (type(row) is not dict or type(row.get('text')) is not str
                or hashlib.sha256(row['text'].encode('utf-8')).hexdigest() != row.get('sha256')):
            issues.append('knowledge_dependency_unavailable')
        else:
            result['knowledge'][ident] = {key: row.get(key) for key in ('source_id', 'version', 'sha256')}
    if (definition.knowledge_sources or definition.require_evidence or definition.strict_knowledge
            or any(name.startswith(('knowledge.', 'rag.')) for name in definition.allowed_tools)):
        provider = data['preferences'].get('knowledge_provider', 'exact_keyword')
        result['knowledge_provider'] = {'provider': provider}
        if provider != 'exact_keyword':
            row = data['connections'].get(provider)
            try:
                if not row or row.get('type') != 'remote_rag' or not row['config']['enabled']:
                    raise ValueError('unavailable knowledge provider')
                config = row['config']
                url = urlsplit(config['endpoint'])
                if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError('invalid knowledge endpoint')
                secret = (model_view.service(config['secret_ref'], config['endpoint'])
                          if config.get('secret_ref') else {'credential_status': 'not_required'})
                if secret['credential_status'] not in ('valid', 'not_required'):
                    issues.append('knowledge_credential_not_active')
                result['knowledge_provider'].update(config_sha256=c.digest(config), credential=secret)
                result['runtime_checks'].append('remote_knowledge_authentication_and_availability')
            except (ValueError, TypeError, KeyError):
                issues.append('knowledge_provider_not_active')
    result['base_public_fingerprint'] = model_view.base_fingerprint(cfg.model_dump(mode='json'))
    return result, sorted(set(issues))
