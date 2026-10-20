"""Model revisions share the original settings transaction and secret authority."""
from __future__ import annotations
import copy
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit
from pyharness.core import configuration_revision_contracts as c
from pyharness.core.tenant_identity import normalize_tenant_id, normalize_profile_id
from pyharness.errors import PyHError


def _snapshot(row, known=()):
    result = c.public_tree({k: row[k] for k in c.MODEL_FIELDS if k in row}, known)
    if (type(result.get('id')) is not str or type(result.get('model')) is not str
            or not 1 <= len(result['model']) <= 200
            or type(result.get('base_url')) is not str):
        c.reject('configuration_invalid_model')
    try:
        url = urlsplit(result['base_url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            c.reject('configuration_private_endpoint')
    except ValueError:
        c.reject('configuration_invalid_model')
    if 'temperature' in result and (type(result['temperature']) not in (int, float) or not 0 <= result['temperature'] <= 1.5):
        c.reject('configuration_invalid_model')
    if 'max_tokens' in result and (type(result['max_tokens']) is not int or not 1 <= result['max_tokens'] <= 32768):
        c.reject('configuration_invalid_model')
    if 'supports_vision' in result and type(result['supports_vision']) is not bool:
        c.reject('configuration_invalid_model')
    fallbacks = result.get('fallback_models', [])
    if type(fallbacks) is not list or len(fallbacks) > 10 or any(type(v) is not str or not 1 <= len(v) <= 200 for v in fallbacks):
        c.reject('configuration_invalid_model')
    return result


def _increases(current, target, active, resource):
    result = []
    if active != resource:
        result.append('active_model_connection_changed')
    for name in ('model', 'base_url'):
        if current.get(name) != target.get(name):
            result.append(name+'_changed')
    if 'max_tokens' not in current or 'max_tokens' not in target:
        result.append('legacy_output_limit_requires_confirmation')
    elif target['max_tokens'] > current['max_tokens']:
        result.append('max_tokens_increased')
    if set(target.get('fallback_models', [])) - set(current.get('fallback_models', [])):
        result.append('fallback_models_added')
    if target.get('supports_vision') and not current.get('supports_vision'):
        result.append('vision_capability_added')
    return result


class _ConfigurationDependencyView:
    """Read-only metadata view whose original model transaction remains owned."""
    def __init__(self, store, tenant, doc, values, authorize):
        self._store, self._tenant, self._doc = store, tenant, doc
        self._values, self._authorize, self._closed = values, authorize, False

    def _check(self):
        if self._closed:
            c.reject('configuration_dependency_view_closed')
        return self._authorize()

    def authority(self):
        return self._check()

    def public(self, value):
        self._check()
        return c.public_tree(value, tuple(self._values.values()))

    def base_fingerprint(self, value):
        self._check()
        return c.base_fingerprint(value, tuple(self._values.values()))

    def model(self, profile_id=None):
        self._check()
        selected = profile_id or self._doc.get('active')
        if not selected:
            return {'profile_id': None, 'credential_status': 'unversioned_base_configuration',
                    'sha256': c.digest({'tenant': self._tenant, 'profile': None})}
        if type(selected) is not str:
            c.reject('configuration_invalid_resource')
        selected = normalize_profile_id(selected)
        state = self._store._model_configuration_state(self._tenant, selected, self._doc)
        execution = {k: state['snapshot'][k] for k in ('model', 'base_url', 'temperature',
            'max_tokens', 'fallback_models', 'supports_vision') if k in state['snapshot']}
        return {'profile_id': selected, 'credential_status': state['credential_status'],
                'sha256': c.digest({'tenant': self._tenant, 'profile': selected,
                    'execution': execution, '_binding': state['_binding']})}

    def service(self, reference, endpoint):
        self._check()
        prefix = 'service:'+self._tenant+':'
        status, binding = 'invalid_reference', {}
        if type(reference) is str and reference.startswith(prefix):
            ident = reference[len(prefix):]
            rows = self._doc.get('service_credentials', {})
            row = rows.get(ident) if type(rows) is dict else None
            if type(row) is dict and row.get('endpoint') == endpoint and self._values.get('service:'+ident):
                revision = row.get('credential_revision')
                if type(revision) is str and re.fullmatch(r'[a-f0-9]{32}', revision):
                    status = 'valid'
                    binding = {'tenant': self._tenant, 'service_id': ident,
                               'endpoint': endpoint, 'revision': revision}
                else:
                    status = 'unversioned_reference'
        return {'credential_status': status, 'sha256': c.digest(binding)}

    def remote(self, root, record):
        """Validate the original versioned remote blob without exporting its token."""
        self._check()
        status, binding = 'invalid_reference', {}
        if type(record) is dict and type(record.get('secret_id')) is str and re.fullmatch(r'[a-f0-9]{32}', record['secret_id']):
            try:
                from pyharness.core.sandbox import safe_path, read_regular
                from pyharness.core.tenant_settings import _dpapi
                raw = read_regular(safe_path(root, 'remote-'+record['secret_id']+'.bin'), 16384)
                token = (_dpapi(raw, protect=False) if os.name == 'nt' else raw).decode('utf-8')
                if not 32 <= len(token) <= 4096 or not token.isascii() or any(c.isspace() or ord(c) < 33 for c in token):
                    raise ValueError('invalid remote credential')
                self._values['configuration-remote:'+record['secret_id']] = token
                binding = self.public({'tenant': self._tenant, 'id': record['id'],
                                       'address': record['address'], 'revision': record['secret_id']})
                status = 'valid'
            except (PyHError, ValueError, TypeError, KeyError, OSError):
                pass
        return {'credential_status': status, 'sha256': c.digest(binding)}


class ModelConfigurationHistory:
    @contextmanager
    def configuration_dependency_view(self, tenant_id, *, authorize):
        """Keep model bindings stable through an owned outer platform commit.

        Caller acquires the platform transaction first, then this original model
        transaction, and releases this view only after the platform commits.
        Secret values stay inside the core view and never become a return value.
        """
        with self._configuration_access(tenant_id, 'configuration-dependency', authorize) as (tenant, _, doc):
            view = _ConfigurationDependencyView(self, tenant, doc, self._load_secrets(tenant), authorize)
            try:
                yield view
            finally:
                view._closed = True
                view._values = {}

    def configuration_public_metadata(self, tenant_id, value, *, authorize):
        """Check known tenant secrets without exporting them to application code."""
        with self.configuration_dependency_view(tenant_id, authorize=authorize) as view:
            return view.public(value)

    def configuration_base_fingerprint(self, tenant_id, value, *, authorize):
        with self.configuration_dependency_view(tenant_id, authorize=authorize) as view:
            return view.base_fingerprint(value)

    @contextmanager
    def _configuration_access(self, tenant_id, profile_id, authorize):
        if not callable(authorize):
            c.reject('configuration_authority_required')
        authorize()
        if type(tenant_id) is not str or type(profile_id) is not str:
            c.reject('configuration_invalid_resource')
        tenant = normalize_tenant_id(tenant_id)
        profile = normalize_profile_id(profile_id)
        with self._transaction(tenant):
            authorize()
            yield tenant, profile, self._load_doc(tenant)
            authorize()

    def _model_configuration_state(self, tenant, profile, doc):
        rows = [p for p in doc['profiles'] if type(p) is dict and p.get('id') == profile]
        if len(rows) > 1:
            c.reject('configuration_resource_ambiguous')
        values, credential_status = {}, 'unavailable'
        try:
            values = self._load_secrets(tenant)
        except PyHError:
            pass  # Only status is exposed; invalid storage cannot authorize restore.
        row = rows[0] if rows else None
        if row is None:
            binding, snapshot = {}, {}
            credential_status = 'resource_missing'
        else:
            snapshot = _snapshot(row, tuple(values.values()))
            binding = {'profile_id': profile, 'revision': row.get('credential_revision', ''),
                       'endpoint': row['base_url'], 'source': self._mask_ref(row)}
            if binding['source'] in ('env', 'file'):
                credential_status = 'unversioned_external_reference'
            elif values.get(profile) and binding['revision'] and binding['source'] == 'encrypted':
                credential_status = 'valid'
        source = {'tenant_id': tenant, 'resource_kind': 'model', 'resource_id': profile,
                  'snapshot': snapshot, '_binding': binding, 'active_profile': doc.get('active', ''),
                  'credential_status': credential_status, 'updated_at': row.get('updated_at') if row else None}
        source['source_sha256'] = c.digest(source)
        return source

    def model_configuration_current(self, tenant_id, profile_id, *, authorize):
        with self._configuration_access(tenant_id, profile_id, authorize) as (tenant, profile, doc):
            state = self._model_configuration_state(tenant, profile, doc)
            if not state['snapshot']:
                c.reject('configuration_resource_not_found')
            return c.public_tree({k: v for k, v in state.items() if not k.startswith('_')})

    def model_configuration_dependency(self, tenant_id, profile_id, *, authorize):
        """Opaque exact model/credential binding, independent of labels/activation."""
        with self.configuration_dependency_view(tenant_id, authorize=authorize) as view:
            return view.model(profile_id)

    def _model_configuration_revision(self, tenant, profile, ident, doc):
        value = c.history(doc)['revisions'].get(ident) if type(ident) is str else None
        if value is None:
            c.reject('configuration_revision_not_found')
        value = c.checked_revision(value, ident, tenant, 'model', profile)
        if _snapshot(value['snapshot']) != value['snapshot'] or value['snapshot']['id'] != profile:
            c.reject('configuration_revision_integrity')
        return value

    def model_configuration_history(self, tenant_id, profile_id, *, authorize):
        with self._configuration_access(tenant_id, profile_id, authorize) as (tenant, profile, doc):
            rows = [self._model_configuration_revision(tenant, profile, ident, doc)
                    for ident, value in c.history(doc)['revisions'].items()
                    if type(value) is dict and value.get('resource_id') == profile]
            return {'resource_kind': 'model', 'resource_id': profile,
                    'revisions': [c.public_revision(v) for v in sorted(rows, key=lambda v: v['number'])],
                    'commands': c.recent_commands(c.history(doc), tenant, 'model', profile),
                    'execution_authorized': False}

    def capture_model_configuration(self, tenant_id, profile_id, body, *, authorize):
        c.request(body, {'command_key', 'source_sha256'})
        with self._configuration_access(tenant_id, profile_id, authorize) as (tenant, profile, doc):
            request_digest = c.digest({'tenant': tenant, 'resource': profile, 'body': body})
            previous = c.command(c.history(doc), body['command_key'], 'capture_model', request_digest)
            if previous is not None:
                return previous
            state = self._model_configuration_state(tenant, profile, doc)
            if not state['snapshot']:
                c.reject('configuration_resource_not_found')
            if state['source_sha256'] != body['source_sha256']:
                c.reject('configuration_source_stale')
            c.public_tree(body, tuple(self._load_secrets(tenant).values()))
            history_ = c.history(doc, create=True)
            if len(history_['revisions']) >= c.MAX_REVISIONS:
                c.reject('configuration_revision_history_limit')
            prior = [v['number'] for v in history_['revisions'].values() if v['resource_id'] == profile]
            value = c.revision(tenant, 'model', profile, max(prior, default=0)+1,
                               state['snapshot'], state['source_sha256'], state['_binding'])
            history_['revisions'][value['revision_id']] = value
            result = {'status': 'captured', 'revision': c.public_revision(value),
                      'historical_result': True, 'execution_authorized': False}
            c.save_command(history_, body['command_key'], 'capture_model', request_digest, result)
            authorize()
            self._save_doc(tenant, doc)
            return result

    def _model_configuration_preview(self, tenant, profile, ident, doc):
        value = self._model_configuration_revision(tenant, profile, ident, doc)
        state = self._model_configuration_state(tenant, profile, doc)
        issues = []
        if not state['snapshot']:
            issues.append('resource_missing')
        if state['credential_status'] != 'valid':
            issues.append('credential_'+state['credential_status'])
        if value['_binding'] != state['_binding']:
            issues.append('credential_binding_changed')
        result = c.preview(state, value, issues=issues,
            increases=_increases(state['snapshot'], value['snapshot'], state['active_profile'], profile))
        result['active_profile_before'] = state['active_profile']
        result['active_profile_after'] = profile
        # Activation choice is part of the exact preview, not a hidden second write.
        result['preview_sha256'] = c.digest({k: v for k, v in result.items() if k != 'preview_sha256'})
        return result

    def preview_model_configuration(self, tenant_id, profile_id, ident, *, authorize):
        with self._configuration_access(tenant_id, profile_id, authorize) as (tenant, profile, doc):
            return self._model_configuration_preview(tenant, profile, ident, doc)

    def apply_model_configuration(self, tenant_id, profile_id, ident, body, *, authorize):
        c.confirm(body)
        with self._configuration_access(tenant_id, profile_id, authorize) as (tenant, profile, doc):
            request_digest = c.digest({'tenant': tenant, 'resource': profile, 'revision': ident, 'body': body})
            previous = c.command(c.history(doc), body['command_key'], 'apply_model', request_digest)
            if previous is not None:
                return previous
            c.public_tree(body, tuple(self._load_secrets(tenant).values()))
            preview = self._model_configuration_preview(tenant, profile, ident, doc)
            if not preview['compatible']:
                c.reject('configuration_restore_not_active')
            if body['preview_sha256'] != preview['preview_sha256']:
                c.reject('configuration_preview_stale')
            if preview['permission_increases'] and body['confirm_permission_increase'] is not True:
                c.reject('configuration_permission_confirmation_required')
            value = self._model_configuration_revision(tenant, profile, ident, doc)
            row = next(p for p in doc['profiles'] if p['id'] == profile)
            for name in c.MODEL_FIELDS:
                if name == 'id':
                    continue
                if name in value['snapshot']:
                    row[name] = copy.deepcopy(value['snapshot'][name])
                else:
                    row.pop(name, None)
            row['updated_at'] = datetime.now(timezone.utc).isoformat()
            doc['active'] = profile
            state = self._model_configuration_state(tenant, profile, doc)
            if state['credential_status'] != 'valid' or state['_binding'] != value['_binding']:
                c.reject('configuration_restore_not_active')
            result = {'status': 'applied', 'revision': c.public_revision(value),
                      'profile_id': profile, 'active': profile,
                      'new_source_sha256': state['source_sha256'], 'historical_result': True,
                      'old_attempts_changed': False, 'external_effects_rolled_back': False,
                      'execution_authorized': False}
            c.save_command(c.history(doc, create=True), body['command_key'], 'apply_model', request_digest, result)
            authorize()
            self._save_doc(tenant, doc)  # Sole commit: model metadata + receipt; secret file untouched.
            return result

    def model_configuration_command(self, tenant_id, command_key, *, authorize):
        # No profile is required to observe an already committed command.
        with self._configuration_access(tenant_id, 'configuration-query', authorize) as (_, _, doc):
            value = c.history(doc)['commands'].get(c.key(command_key))
            if value is None:
                c.reject('configuration_command_not_found')
            return c.public_tree(c.checked_command(value)['result'])
