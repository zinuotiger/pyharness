"""Bounded configuration metadata contracts; no execution or secret resolver."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pyharness.core.context_observation import _PRIVATE_TEXT

MAX_REVISIONS = 128
MAX_COMMANDS = 256
MODEL_FIELDS = ('id', 'label', 'provider', 'model', 'base_url', 'temperature',
                'max_tokens', 'fallback_models', 'supports_vision')


def reject(code):
    # Avoid importing sandbox/config assembly while TenantSettingsStore loads.
    from pyharness.core.sandbox import fail
    fail(code)


def encoded(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError, UnicodeError):
        reject('configuration_invalid_metadata')
    if len(raw) > 65536:
        reject('configuration_metadata_limit')
    return raw


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def key(value):
    if (type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value)
            or _PRIVATE_TEXT.search(value)):
        reject('configuration_invalid_key')
    return value


def request(body, fields):
    if type(body) is not dict or set(body) != set(fields) or len(encoded(body)) > 2048:
        reject('configuration_invalid_request')
    key(body['command_key'])


def public_tree(value, known_secrets=()):
    encoded(value)
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 2048 or depth > 12:
            reject('configuration_metadata_limit')
        if type(item) is str:
            if (_PRIVATE_TEXT.search(item) or any(secret and secret in item for secret in known_secrets)
                    or any(ord(c) < 32 and c not in '\n\r\t' for c in item)):
                reject('configuration_private_content')
        elif type(item) is dict:
            if any(type(k) is not str for k in item):
                reject('configuration_invalid_metadata')
            pending.extend((k, depth+1) for k in item)
            pending.extend((v, depth+1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth+1) for v in item)
        elif item is not None and type(item) not in (bool, int, float):
            reject('configuration_invalid_metadata')
    return copy.deepcopy(value)


def base_fingerprint(value, known_secrets=()):
    """Fingerprint base policy while omitting secret values, including raw keys.

    Actual selected-model credentials have a separate original revision binding.
    This finite masking is deliberately not a DLP or authentication mechanism.
    """
    encoded(value)
    sensitive = re.compile(r'(?i)api.?key|secret|password|passphrase|authorization|cookie|(?:^|_)token$')
    nodes = 0
    def masked(item, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 2048 or depth > 12:
            reject('configuration_metadata_limit')
        if type(item) is dict:
            if any(type(k) is not str for k in item):
                reject('configuration_invalid_metadata')
            return {k: '[secret-reference-omitted]' if sensitive.search(k)
                    else masked(v, depth+1) for k, v in item.items()}
        if type(item) is list:
            return [masked(v, depth+1) for v in item]
        if type(item) is str and (_PRIVATE_TEXT.search(item)
                or any(secret and secret in item for secret in known_secrets)):
            return '[secret-reference-omitted]'
        return item
    return digest(masked(value))


def history(doc, *, create=False):
    value = doc.get('configuration_history')
    if value is None:
        value = {'schema_version': 1, 'revisions': {}, 'commands': {}}
        if create:
            doc['configuration_history'] = value
    if (type(value) is not dict or set(value) != {'schema_version', 'revisions', 'commands'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['revisions']) is not dict or type(value['commands']) is not dict
            or len(value['revisions']) > MAX_REVISIONS or len(value['commands']) > MAX_COMMANDS
            or any(type(v) is not dict for v in value['revisions'].values())
            or any(type(v) is not dict for v in value['commands'].values())):
        reject('configuration_schema_unsupported')
    return value


def revision(tenant, kind, resource, number, snapshot, source_sha256, binding):
    value = {'schema_version': 1, 'tenant_id': tenant, 'resource_kind': kind,
             'resource_id': resource, 'revision_id': 'cr-'+uuid.uuid4().hex,
             'number': number, 'created_at': datetime.now(timezone.utc).isoformat(),
             'snapshot': public_tree(snapshot), 'source_sha256': source_sha256,
             '_binding': copy.deepcopy(binding)}
    value['sha256'] = digest(value)
    return value


def checked_revision(value, ident, tenant, kind, resource):
    fields = {'schema_version', 'tenant_id', 'resource_kind', 'resource_id', 'revision_id',
              'number', 'created_at', 'snapshot', 'source_sha256', '_binding', 'sha256'}
    if (type(value) is not dict or set(value) != fields or value['schema_version'] != 1
            or type(value['schema_version']) is not int or value['revision_id'] != ident
            or type(ident) is not str or not re.fullmatch(r'cr-[a-f0-9]{32}', ident)
            or value['tenant_id'] != tenant or value['resource_kind'] != kind
            or value['resource_id'] != resource or type(value['number']) is not int
            or not 1 <= value['number'] <= MAX_REVISIONS
            or type(value['created_at']) is not str or len(value['created_at']) > 64
            or type(value['snapshot']) is not dict or type(value['_binding']) is not dict
            or type(value['source_sha256']) is not str
            or not re.fullmatch(r'[a-f0-9]{64}', value['source_sha256'])
            or value['sha256'] != digest({k: v for k, v in value.items() if k != 'sha256'})):
        reject('configuration_revision_integrity')
    return value


def public_revision(value):
    return public_tree({k: v for k, v in value.items() if not k.startswith('_')})


def changes(current, target):
    return [{'field': k, 'before': copy.deepcopy(current.get(k)), 'after': copy.deepcopy(target.get(k))}
            for k in sorted(set(current) | set(target)) if current.get(k) != target.get(k)]


def preview(current, target, *, issues, increases):
    value = {'schema_version': 1, 'resource_kind': target['resource_kind'],
             'resource_id': target['resource_id'], 'revision_id': target['revision_id'],
             'current_source_sha256': current['source_sha256'], 'target_sha256': target['sha256'],
             'compatible': not issues, 'issues': list(issues), 'permission_increases': list(increases),
             'changes': changes(current['snapshot'], target['snapshot']),
             'execution_authorized': False, 'persisted': False,
             'old_attempts_changed': False, 'external_effects_rolled_back': False}
    value['preview_sha256'] = digest(value)
    return value


def command(history_, command_key, operation, request_digest):
    value = history_['commands'].get(key(command_key))
    if value is not None:
        checked_command(value)
        if (value['operation'] != operation or value['request_digest'] != request_digest):
            reject('configuration_command_conflict')
        return public_tree(value['result'])
    return None


def checked_command(value):
    fields = {'operation', 'request_digest', 'result', 'sha256'}
    if (type(value) is not dict or set(value) not in (fields, fields | {'created_at'})
                or type(value['result']) is not dict
                or ('created_at' in value and (type(value['created_at']) is not str or len(value['created_at']) > 64))
                or value['sha256'] != digest({k: v for k, v in value.items() if k != 'sha256'})):
        reject('configuration_command_integrity')
    public_tree(value['result'])
    return value


def save_command(history_, command_key, operation, request_digest, result):
    if len(history_['commands']) >= MAX_COMMANDS:
        reject('configuration_command_history_limit')
    value = {'operation': operation, 'request_digest': request_digest, 'result': public_tree(result),
             'created_at': datetime.now(timezone.utc).isoformat()}
    value['sha256'] = digest(value)
    history_['commands'][key(command_key)] = value


def recent_commands(history_, tenant, kind, resource):
    result = []
    for command_key, raw in history_['commands'].items():
        value = checked_command(raw)
        revision_ = value['result'].get('revision')
        if (type(revision_) is dict and revision_.get('tenant_id') == tenant
                and revision_.get('resource_kind') == kind and revision_.get('resource_id') == resource):
            result.append({'command_key': key(command_key), 'operation': value['operation'],
                'created_at': value.get('created_at'), 'status': value['result'].get('status'),
                'revision_id': revision_['revision_id'], 'historical_result': True})
    return public_tree(result[-20:][::-1])


def confirm(body):
    request(body, {'command_key', 'preview_sha256', 'confirm_apply', 'confirm_permission_increase'})
    if body['confirm_apply'] is not True or type(body['confirm_permission_increase']) is not bool:
        reject('configuration_confirmation_required')
