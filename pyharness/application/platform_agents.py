"""PlatformAgents operations, separated from facade assembly."""
from __future__ import annotations
import copy
import difflib
import hashlib
import json
import uuid
from pyharness.application.platform_models import AgentDefinition, AgentVersion
from pyharness.core.sandbox import fail
from pyharness.application.platform_templates import TEMPLATES


def _validated_agent(body):
    agent = AgentDefinition.model_validate(body)
    from pyharness.application.order_review import policy
    if policy(agent.output_schema) is not None and (agent.strict_knowledge or agent.executor!='engine'):
        fail('order_review_requires_engine_and_non_strict_knowledge')
    if agent.output_schema:
        from pyharness.core.tools_registry import _compile_model
        _compile_model('platform.output', agent.output_schema)
    if agent.mcp_connections and agent.sandbox_profile.mode != 'host_approved':
        fail('mcp_requires_explicit_host_approval')
    return agent


def _publish_into(data, ident):
    """Original publication primitive, within an already owned transaction."""
    if ident not in data['agents']:
        fail('agent_not_found')
    agent = AgentDefinition.model_validate(data['agents'][ident])
    agent.current_version += 1
    agent.status = 'published'
    snap = agent.model_dump()
    version = AgentVersion(agent_id=ident, version=agent.current_version, snapshot=agent,
        sha256=hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()).model_dump()
    data['versions'].setdefault(ident, []).append(version)
    data['agents'][ident] = snap
    return version


def _version_in_data(data, ident, number):
    for version in data['versions'].get(ident, []):
        if version['version'] == number:
            checksum = hashlib.sha256(json.dumps(version['snapshot'], sort_keys=True).encode()).hexdigest()
            if checksum != version['sha256']:
                fail('agent_version_integrity_failed')
            return version
    fail('agent_version_not_found')


class PlatformAgents:
    """PlatformAgents responsibility; state is owned by the composing facade."""

    def agents(self):
        data = self.store.read()
        return {'agents':list(data['agents'].values()), 'templates':copy.deepcopy(TEMPLATES)}

    def validate_execution_policy(self, definition):
        if definition.executor == 'codex':
            # A locally signed-in CLI is a host capability, never a tenant credential.
            if definition.sandbox_profile.mode != 'host_approved':
                fail('codex_requires_explicit_host_approval')
            if 'codex.run' not in definition.allowed_tools or definition.permission_policy != 'standard':
                fail('codex_requires_governed_tool')
            if definition.review_mode != 'manual' or definition.mcp_connections:
                fail('codex_configuration_unsupported')
        from pyharness.application.accounts import Accounts
        if Accounts.manages_tenant(self.service._storage_root(), self.service.tenant_id):
            if definition.sandbox_profile.mode == 'host_approved' or definition.mcp_connections:
                fail('account_host_execution_forbidden')

    def save_agent(self, body, ident=None):
        agent = _validated_agent(body)
        with self.store.transaction() as data:
            if ident:
                old = data['agents'].get(ident)
                if not old:
                    fail('agent_not_found')
                agent.agent_id, agent.current_version = ident, old['current_version']
            else:
                agent.agent_id, agent.current_version = 'a-' + uuid.uuid4().hex, 0
            agent.status = 'draft'
            data['agents'][agent.agent_id] = agent.model_dump()
        return agent.model_dump()

    def publish_agent(self, ident):
        with self.store.transaction() as data:
            return _publish_into(data, ident)

    def agent_action(self, ident, action, body=None):
        data = self.store.read()
        if ident not in data['agents']:
            fail('agent_not_found')
        if action == 'versions':
            return {'versions':data['versions'].get(ident, [])}
        if action == 'copy':
            version = (body or {}).get('version')
            source = data['agents'][ident]
            if version:
                source = self.version(ident, int(version))['snapshot']
            source = {**source, 'name':source['name'] + ' 副本'}
            return self.save_agent(source)
        if action == 'publish':
            return self.publish_agent(ident)
        if action == 'diff':
            left = self.version(ident, int((body or {}).get('from', 1)))['snapshot']
            right = self.version(ident, int((body or {}).get('to', data['agents'][ident]['current_version'])))['snapshot']
            return {'diff': ''.join(difflib.unified_diff(
                json.dumps(left, ensure_ascii=False, indent=2, sort_keys=True).splitlines(True),
                json.dumps(right, ensure_ascii=False, indent=2, sort_keys=True).splitlines(True),
                fromfile='previous', tofile='selected'))}
        if action == 'deprecate-version':
            number = int((body or {}).get('version', 0))
            self.version(ident, number)
            with self.store.transaction() as current:
                current['deprecated_versions'][f'{ident}:{number}'] = True
            return {'ok': True}
        with self.store.transaction() as current:
            if action == 'delete':
                if current['versions'].get(ident):
                    fail('published_agent_cannot_delete')
                del current['agents'][ident]
            elif action == 'deprecate':
                current['agents'][ident]['status'] = 'deprecated'
            else:
                fail('invalid_action')
        return {'ok':True}

    def version(self, ident, number):
        return _version_in_data(self.store.read(), ident, number)

    def new_binding_version(self, ident, number):
        data=self.store.read()
        if data['agents'].get(ident,{}).get('status')=='deprecated' or data['deprecated_versions'].get(f'{ident}:{number}'):
            fail('agent_version_deprecated')
        return self.version(ident,number)

