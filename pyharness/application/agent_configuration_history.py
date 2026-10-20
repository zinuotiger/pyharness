"""Configuration rollback references the original immutable Agent versions."""
from __future__ import annotations
import copy
from contextlib import contextmanager, ExitStack
from pyharness.application.platform_agents import _validated_agent, _publish_into, _version_in_data
from pyharness.application.configuration_dependencies import dependencies
from pyharness.application.configuration_policy import permission_increases
from pyharness.core import configuration_revision_contracts as c
from pyharness.errors import PyHError


def _reference(version):
    return {name: version[name] for name in ('agent_id', 'version', 'sha256')}


def _policy(definition):
    return {k: v for k, v in definition.model_dump().items()
            if k not in ('agent_id', 'status', 'current_version')}


class AgentConfigurationHistory:
    def __init__(self, platform):
        self.platform = platform
        self.service = platform.service

    def _check_policy(self, definition, view):
        self.platform.validate_execution_policy(definition)
        actor = view.authority()
        if type(actor) is dict and actor.get('mode') == 'tenant_account' and (
                definition.sandbox_profile.mode == 'host_approved' or definition.mcp_connections
                or definition.executor == 'codex' or 'codex.run' in definition.allowed_tools):
            c.reject('account_host_execution_forbidden')

    @contextmanager
    def _access(self, authorize, *, needs_dependencies=True):
        if not callable(authorize):
            c.reject('configuration_authority_required')
        authorize()
        # Acquire P then M, but keep M owned until P has actually committed.
        # Only the original platform file is written by an Agent restoration.
        with ExitStack() as guards:
            with self.platform.store.transaction() as data:
                authorize()
                view = (guards.enter_context(self.service.credentials.configuration_dependency_view(
                    self.service.tenant_id, authorize=authorize)) if needs_dependencies else None)
                yield data, view
                authorize()
            authorize()

    def _state(self, resource, data, view):
        c.key(resource)
        row = data['agents'].get(resource)
        if row is None:
            c.reject('configuration_resource_not_found')
        definition = _validated_agent(row)
        binding, issues = dependencies(self.platform, data, definition, view)
        public = view.public(definition.model_dump())
        version = _version_in_data(data, resource, definition.current_version) if definition.current_version else None
        if definition.status == 'published' and (not version or public != version['snapshot']):
            c.reject('configuration_published_state_integrity')
        if definition.status != 'published':
            issues.append('current_agent_'+definition.status)
        state = {'tenant_id': self.service.tenant_id, 'resource_kind': 'agent',
                 'resource_id': resource, 'snapshot': _policy(definition),
                 'agent_ref': _reference(version) if version else None,
                 'agent_status': definition.status, '_binding': binding,
                 'issues': sorted(set(issues)), 'execution_authorized': False}
        state['source_sha256'] = c.digest(state)
        return state

    def current(self, resource, *, authorize):
        with self._access(authorize) as (data, view):
            state = self._state(resource, data, view)
            return c.public_tree({k: v for k, v in state.items() if not k.startswith('_')})

    def _revision(self, resource, ident, data):
        c.key(resource)
        value = c.history(data['preferences'])['revisions'].get(ident) if type(ident) is str else None
        if value is None:
            c.reject('configuration_revision_not_found')
        value = c.checked_revision(value, ident, self.service.tenant_id, 'agent', resource)
        reference = value['snapshot'].get('agent_ref')
        if (set(value['snapshot']) != {'agent_ref'} or type(reference) is not dict
                or set(reference) != {'agent_id', 'version', 'sha256'}
                or reference['agent_id'] != resource or type(reference['version']) is not int
                or reference['version'] < 1):
            c.reject('configuration_revision_integrity')
        version = _version_in_data(data, resource, reference['version'])
        if _reference(version) != reference:
            c.reject('configuration_revision_integrity')
        return value, version

    def history(self, resource, *, authorize):
        c.key(resource)
        with self._access(authorize, needs_dependencies=False) as (data, _):
            values = [self._revision(resource, ident, data)[0]
                      for ident, row in c.history(data['preferences'])['revisions'].items()
                      if row.get('resource_id') == resource]
            return {'resource_kind': 'agent', 'resource_id': resource,
                    'revisions': [c.public_revision(v) for v in sorted(values, key=lambda v: v['number'])],
                    'commands': c.recent_commands(c.history(data['preferences']), self.service.tenant_id, 'agent', resource),
                    'execution_authorized': False}

    def capture(self, resource, body, *, authorize):
        c.request(body, {'command_key', 'source_sha256'})
        with self._access(authorize) as (data, view):
            request_digest = c.digest({'tenant': self.service.tenant_id, 'resource': resource, 'body': body})
            previous = c.command(c.history(data['preferences']), body['command_key'], 'capture_agent', request_digest)
            if previous is not None:
                return previous
            state = self._state(resource, data, view)
            if state['agent_status'] != 'published' or not state['agent_ref']:
                c.reject('configuration_agent_not_active')
            if state['source_sha256'] != body['source_sha256']:
                c.reject('configuration_source_stale')
            view.public(body)
            history_ = c.history(data['preferences'], create=True)
            if len(history_['revisions']) >= c.MAX_REVISIONS:
                c.reject('configuration_revision_history_limit')
            prior = [v['number'] for v in history_['revisions'].values() if v['resource_id'] == resource]
            value = c.revision(self.service.tenant_id, 'agent', resource, max(prior, default=0)+1,
                {'agent_ref': state['agent_ref']}, state['source_sha256'], state['_binding'])
            history_['revisions'][value['revision_id']] = value
            result = {'status': 'captured', 'revision': c.public_revision(value),
                      'historical_result': True, 'execution_authorized': False}
            c.save_command(history_, body['command_key'], 'capture_agent', request_digest, result)
            authorize()
            return result

    def _preview(self, resource, ident, data, view):
        value, version = self._revision(resource, ident, data)
        state = self._state(resource, data, view)
        target = _validated_agent(version['snapshot'])
        binding, issues = dependencies(self.platform, data, target, view)
        public_target = view.public(_policy(target))
        if state['agent_status'] != 'published':
            issues.append('current_agent_'+state['agent_status'])
        if data['deprecated_versions'].get(f'{resource}:{version["version"]}'):
            issues.append('target_version_deprecated')
        if binding != value['_binding']:
            issues.append('target_dependency_changed')
        try:
            self._check_policy(target, view)
        except PyHError as error:
            issues.append(error.code)
        result = c.preview(state, {**value, 'snapshot': public_target}, issues=sorted(set(issues)),
            increases=permission_increases(state['snapshot'], public_target))
        result['current_agent_ref'] = state['agent_ref']
        result['target_agent_ref'] = _reference(version)
        result['runtime_checks'] = binding['runtime_checks']
        result['runtime_checks_verified'] = False if binding['runtime_checks'] else None
        result['preview_sha256'] = c.digest({k: v for k, v in result.items() if k != 'preview_sha256'})
        return result

    def preview(self, resource, ident, *, authorize):
        with self._access(authorize) as (data, view):
            return self._preview(resource, ident, data, view)

    def apply(self, resource, ident, body, *, authorize):
        c.confirm(body)
        with self._access(authorize) as (data, view):
            request_digest = c.digest({'tenant': self.service.tenant_id,
                'resource': resource, 'revision': ident, 'body': body})
            previous = c.command(c.history(data['preferences']), body['command_key'], 'apply_agent', request_digest)
            if previous is not None:
                return previous
            view.public(body)
            preview = self._preview(resource, ident, data, view)
            if not preview['compatible']:
                c.reject('configuration_restore_not_active')
            if body['preview_sha256'] != preview['preview_sha256']:
                c.reject('configuration_preview_stale')
            if preview['permission_increases'] and body['confirm_permission_increase'] is not True:
                c.reject('configuration_permission_confirmation_required')
            value, selected = self._revision(resource, ident, data)
            target = _validated_agent(copy.deepcopy(selected['snapshot']))
            self._check_policy(target, view)
            target.current_version = data['agents'][resource]['current_version']
            data['agents'][resource] = target.model_dump()
            version = _publish_into(data, resource)
            result = {'status': 'applied', 'revision': c.public_revision(value),
                      'agent_ref': _reference(version), 'historical_result': True,
                      'old_attempts_changed': False, 'external_effects_rolled_back': False,
                      'execution_authorized': False}
            c.save_command(c.history(data['preferences'], create=True), body['command_key'],
                           'apply_agent', request_digest, result)
            authorize()
            return result

    def command(self, command_key, *, authorize):
        with self._access(authorize, needs_dependencies=False) as (data, _):
            value = c.history(data['preferences'])['commands'].get(c.key(command_key))
            if value is None:
                c.reject('configuration_command_not_found')
            return c.public_tree(c.checked_command(value)['result'])
