"""Bounded extension observations; never an execution/approval authority.

Only discover owns a process. Previews register schema on an isolated original
registry, never access a Provider and never send tools/call. Requested records
are durable no-replay markers, including after cancellation/cold restart.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from datetime import datetime, timezone

from pyharness.config import McpServerCfg
from pyharness.core.context_observation import _PublicCapture, _PRIVATE_TEXT
from pyharness.core.mcp import McpClient, StdioTransport
from pyharness.core.sandbox import fail
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError

_KEY = re.compile(r'[A-Za-z0-9_-]{1,80}')
_NAME = re.compile(r'[a-z][a-z0-9_]{1,40}')
_MAX_BYTES = 65536
_MAX_RECORDS = 128


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _key(value):
    if type(value) is not str or not _KEY.fullmatch(value):
        fail('invalid_diagnostic_key')
    return value


def _safe_schema(value):
    """Finite private-content refusal, without rewriting schema property names."""
    raw = _encoded(value)
    if len(raw) > _MAX_BYTES:
        fail('diagnostic_schema_limit')
    pending = [value]
    count = 0
    while pending:
        item = pending.pop()
        count += 1
        if count > 2048:
            fail('diagnostic_schema_limit')
        if type(item) is str and _PRIVATE_TEXT.search(item):
            fail('diagnostic_private_content')
        if type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return value


class _ObservedTransport(StdioTransport):
    def __init__(self, argv, *, timeout_s):
        super().__init__(argv, timeout_s=timeout_s)
        self.owned_process = None

    async def _ensure(self):
        await super()._ensure()
        self.owned_process = self._proc


class McpWorkbench:
    def __init__(self, platform):
        self.platform = platform

    def _config(self, source, name):
        if type(name) is not str or not _NAME.fullmatch(name):
            fail('invalid_connection_name')
        if source == 'managed':
            row = self.platform.store.read()['connections'].get('mcp:' + name)
            cfg = row['config'] if row and row.get('type') == 'mcp' else None
        elif source == 'runtime':
            rows = [m.model_dump() for m in
                    self.platform.service.effective_settings().plugins.mcp_servers
                    if m.name == name]
            if len(rows) > 1:
                fail('diagnostic_configuration_ambiguous')
            cfg = rows[0] if rows else None
        else:
            fail('invalid_connection_source')
        if cfg is None:
            fail('connection_not_found')
        return McpServerCfg.model_validate(cfg).model_dump()

    def _identity(self, source, name, cfg):
        return _digest({'tenant': self.platform.service.tenant_id,
                        'source': source, 'name': name, 'config': cfg})

    def preview(self, source, name, *, authorize):
        authorize()
        cfg = self._config(source, name)
        return {'source': source, 'name': name, 'status': 'configuration_only',
                'config_sha256': self._identity(source, name, cfg),
                'enabled': cfg['enabled'], 'timeout_s': min(30, cfg['timeout_s']),
                'allowed_tools': copy.deepcopy(cfg['allowed_tools']),
                'host_process_confirmation_required': True,
                'execution_authorized': False,
                'warning': '发现会启动配置的宿主进程；不执行 tools/call，不代表任务审批。'}

    def _current(self, record):
        try:
            cfg = self._config(record['source'], record['name'])
            return record['config_sha256'] == self._identity(record['source'], record['name'], cfg)
        except (PyHError, ValueError, TypeError, KeyError):
            return False

    def _record(self, key):
        row = self.platform.store.read()['preferences'].get('mcp_diagnostics', {}).get(_key(key))
        if not row:
            fail('diagnostic_not_found')
        return row

    def result(self, key, *, authorize):
        authorize()
        row = self._record(key)
        public = {k: copy.deepcopy(v) for k, v in row.items() if k != '_definitions'}
        # Persisted intent alone proves neither liveness nor success.
        if public['status'] == 'requested':
            public['status'] = 'unconfirmed'
        public['stale'] = not self._current(row)
        public['connected_now'] = False
        public['execution_authorized'] = False
        return public

    @staticmethod
    def _compile(name, definitions, allowed):
        client = McpClient(name, None)
        client._tools = copy.deepcopy(definitions)
        registry = ToolRegistry()
        client.register_into(registry, allowed_tools=allowed)
        return registry

    def arguments(self, key, tool, arguments, *, authorize):
        authorize()
        row = self._record(key)
        if row['status'] != 'completed' or not self._current(row):
            fail('diagnostic_not_active')
        if type(arguments) is not dict or len(_encoded(arguments)) > _MAX_BYTES:
            fail('invalid_diagnostic_arguments')
        registry = self._compile(row['name'], row['_definitions'], row['allowed_tools'])
        registry.validate_args(tool, arguments)
        return {'valid': True, 'tool': tool, 'schema_sha256': row['schema_sha256'],
                'arguments_retained': False, 'execution_authorized': False,
                'danger': registry.lookup(tool).danger,
                'required_path': 'original task → scope/guard → approval → effect receipt'}

    async def discover(self, source, name, body, *, authorize):
        authorize()
        if type(body) is not dict or len(_encoded(body)) > 2048:
            fail('invalid_diagnostic_request')
        if set(body) != {'command_key', 'confirm_host_process', 'config_sha256'}:
            fail('invalid_diagnostic_request')
        key = _key(body['command_key'])
        if body['confirm_host_process'] is not True:
            fail('host_process_confirmation_required')
        cfg = self._config(source, name)
        identity = self._identity(source, name, cfg)
        existing = self.platform.store.read()['preferences'].get('mcp_diagnostics', {}).get(key)
        if existing:
            if any(existing.get(k) != v for k, v in
                   {'source': source, 'name': name, 'config_sha256': body['config_sha256']}.items()):
                fail('diagnostic_key_conflict')
            return self.result(key, authorize=authorize)
        if body['config_sha256'] != identity:
            fail('diagnostic_configuration_stale')
        if not cfg['enabled']:
            fail('diagnostic_connection_not_active')
        lock = self.platform.locks.setdefault('mcp-diagnostic', asyncio.Lock())
        if lock.locked():
            fail('diagnostic_busy')
        async with lock:
            # No await between current authorization/config checks and intent.
            authorize()
            if self._identity(source, name, self._config(source, name)) != identity:
                fail('diagnostic_configuration_stale')
            with self.platform.store.transaction() as data:
                rows = data['preferences'].setdefault('mcp_diagnostics', {})
                if key in rows:
                    fail('diagnostic_key_conflict')
                if len(rows) >= _MAX_RECORDS:
                    fail('diagnostic_history_limit')
                rows[key] = {'command_key': key, 'source': source, 'name': name,
                             'config_sha256': identity, 'status': 'requested',
                             'requested_at': datetime.now(timezone.utc).isoformat()}
            return await self._probe(key, cfg, authorize)

    async def _probe(self, key, cfg, authorize):
        transport = _ObservedTransport(cfg['command'], timeout_s=min(30, cfg['timeout_s']))
        client = McpClient(cfg['name'], transport)
        result = {'status': 'failed'}
        task = asyncio.current_task()
        action = 'mcp-diagnostic:' + key
        self.platform.actions[action] = task
        try:
            async with asyncio.timeout(min(35, cfg['timeout_s'] * 2 + 2)):
                await client.connect()
                if client.tools_listing.get('nextCursor'):
                    fail('diagnostic_partial_listing')
                definitions = _safe_schema(client._tools)
                if len(definitions) > 64 or any(type(t) is not dict for t in definitions):
                    fail('diagnostic_schema_limit')
                registry = self._compile(cfg['name'], definitions, cfg['allowed_tools'])
                public = _PublicCapture(())
                server = client.initialization.get('serverInfo') or {}
                if type(server) is not dict:
                    fail('diagnostic_invalid_server')
                result = {'status': 'completed',
                          'protocol_status': 'verified' if client.initialization.get('protocolVersion') else 'legacy_unverified',
                          'protocol_version': client.initialization.get('protocolVersion'),
                          'server': {k: public.text(server[k]) for k in ('name', 'version') if k in server},
                          'schema_sha256': _digest(definitions),
                          'tools': [{'name': d.name, 'danger': d.danger,
                                     'schema': copy.deepcopy(d.schema)} for d in registry.iter_definitions()],
                          '_definitions': copy.deepcopy(definitions),
                          'allowed_tools': copy.deepcopy(cfg['allowed_tools'])}
        except asyncio.CancelledError:
            result = {'status': 'unconfirmed', 'error_code': 'diagnostic_cancelled'}
            raise
        except Exception as exc:
            result = {'status': 'failed', 'error_code': exc.code if isinstance(exc, PyHError) else 'diagnostic_failed'}
        finally:
            try:
                await client.close()
            finally:
                owned = transport.owned_process
                result['carrier_closed'] = owned is None or owned.returncode is not None
                if not result['carrier_closed']:
                    result = {'status': 'unconfirmed', 'error_code': 'diagnostic_cleanup_unconfirmed',
                              'carrier_closed': False}
                result['finished_at'] = datetime.now(timezone.utc).isoformat()
                # Cleanup metadata is allowed after revocation; no private result
                # is returned until current identity is checked below.
                try:
                    with self.platform.store.transaction() as data:
                        data['preferences']['mcp_diagnostics'][key].update(result)
                finally:
                    self.platform.actions.pop(action, None)
        authorize()
        return self.result(key, authorize=authorize)
