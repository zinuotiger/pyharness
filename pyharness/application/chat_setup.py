"""Chat setup operations; immutable versions, idempotent publishing and secrets."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import uuid
from pyharness.application.platform_models import AgentDefinition, AgentVersion
from pyharness.core.sandbox import fail
from pyharness.core.tenant_settings import TenantSettingsStore, _dpapi
from pyharness.core.remote_sandbox import validate_address


class ChatSetup:
    def __init__(self, platform):
        self.platform = platform

    def remote_settings(self, ident):
        row = self.platform.store.read()['preferences'].get('remote_connections', {}).get(ident)
        if not row: fail('remote_connection_not_found')
        path = self.platform.root / ('remote-' + row['secret_id'] + '.bin')
        raw = path.read_bytes()
        token = (_dpapi(raw, protect=False) if os.name == 'nt' else raw).decode()
        return row['address'], token

    def remotes(self):
        return {'connections': [{k: v for k, v in r.items() if k != 'secret_id'}
                for r in self.platform.store.read()['preferences'].get('remote_connections', {}).values()]}

    def assistants(self):
        data=self.platform.store.read()
        rows=[]
        for ident,agent in data['agents'].items():
            if agent['status']=='deprecated': continue
            for version in reversed(data['versions'].get(ident,[])):
                if data['deprecated_versions'].get(f"{ident}:{version['version']}"): continue
                rows.append(self.platform.version(ident,version['version'])['snapshot'])
                break
        return {'agents':rows}

    def save_remote(self, body):
        name = str(body.get('name', '')).strip()
        address = validate_address(str(body.get('address', '')))
        token = str(body.get('token', ''))
        if not 1 <= len(name) <= 100 or not 32 <= len(token) <= 4096 or not token.isascii() or any(c.isspace() or ord(c)<33 for c in token): fail('invalid_remote_connection')
        with self.platform.store.transaction() as data:
            remotes = data['preferences'].setdefault('remote_connections', {})
            ident = 'remote-' + hashlib.sha256((name + address).encode()).hexdigest()[:20]
            secret = uuid.uuid4().hex
            TenantSettingsStore._atomic_write(self.platform.root / ('remote-' + secret + '.bin'),
                _dpapi(token.encode(), protect=True) if os.name == 'nt' else token.encode())
            remotes[ident] = dict(id=ident, name=name, address=address, secret_id=secret)
        return {'id': ident, 'name': name, 'address': address}

    @staticmethod
    def fingerprint(agent):
        value = agent.model_dump()
        for key in ('agent_id', 'current_version', 'status'): value.pop(key)
        def normalize(v):
            if isinstance(v,dict): return {k:normalize(x) for k,x in v.items()}
            if isinstance(v,list): return [normalize(x) for x in v]
            return int(v) if isinstance(v,float) and v.is_integer() else v
        return hashlib.sha256(json.dumps(normalize(value), sort_keys=True).encode()).hexdigest()

    def create_use(self, body):
        key = str(body.get('idempotency_key', ''))
        if not 8 <= len(key) <= 100: fail('idempotency_key_required')
        agent = AgentDefinition.model_validate(body['definition'])
        # Freeze the currently selected profile in the published version.
        agent.model_connection = agent.model_connection or self.platform.service.tenant_state()['active']
        if agent.model_connection and not any(p['id']==agent.model_connection for p in self.platform.service.tenant_state()['profiles']):
            fail('model_connection_missing')
        self.platform.validate_execution_policy(agent)
        if agent.sandbox_profile.mode == 'disabled' and any(t.startswith(('exec.', 'proc.', 'fs.', 'file.')) for t in agent.allowed_tools):
            fail('chat_only_tools_forbidden')
        if agent.sandbox_profile.mode == 'remote': self.remote_settings(agent.sandbox_profile.remote_connection)
        if agent.mcp_connections and agent.sandbox_profile.mode != 'host_approved': fail('mcp_requires_explicit_host_approval')
        if agent.output_schema:
            from pyharness.core.tools_registry import _compile_model
            _compile_model('platform.output', agent.output_schema)
        digest = self.fingerprint(agent)
        # Save + publish + idempotency receipt in ONE configuration transaction.
        with self.platform.store.transaction() as data:
            receipts = data['preferences'].setdefault('chat_creations', {})
            if key in receipts:
                receipt = receipts[key]
                if receipt['digest'] != digest: fail('idempotency_conflict')
                return receipt['result']
            for ident, versions in data['versions'].items():
                if data['agents'].get(ident, {}).get('status') == 'deprecated': continue
                for version in reversed(versions):
                    if data['deprecated_versions'].get(f"{ident}:{version['version']}"): continue
                    if self.fingerprint(AgentDefinition.model_validate(version['snapshot'])) == digest:
                        result = {'agent_id': ident, 'version': version['version'], 'reused': True}
                        receipts[key] = {'digest': digest, 'result': result}
                        return result
            agent.agent_id = 'a-' + uuid.uuid4().hex
            agent.current_version, agent.status = 1, 'published'
            snapshot = agent.model_dump()
            version = AgentVersion(agent_id=agent.agent_id, version=1, snapshot=agent,
                sha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()).model_dump()
            data['agents'][agent.agent_id] = snapshot
            data['versions'][agent.agent_id] = [version]
            result = {'agent_id': agent.agent_id, 'version': 1, 'reused': False}
            receipts[key] = {'digest': digest, 'result': result}
            return result

    async def configuration(self, sid=None, agent_id=None, version=None):
        binding = None
        if sid:
            binding = self.platform.binding(await self.platform.service.require_session(sid))
        if binding:
            definition = binding['definition']
        elif agent_id:
            definition = self.platform.new_binding_version(agent_id, int(version))['snapshot']
        else: definition = AgentDefinition(name='通用助手').model_dump()
        from pyharness.application.accounts import Accounts
        host = not Accounts.manages_tenant(self.platform.service._storage_root(), self.platform.service.tenant_id)
        return {'bound': bool(binding), 'definition': definition, 'host_allowed': host,
                'remote_connections': self.remotes()['connections']}

    async def readiness(self, profile):
        from pyharness.application.platform_models import SandboxProfile
        profile = SandboxProfile.model_validate(profile)
        self.platform.validate_execution_policy(AgentDefinition(name='检查执行环境', sandbox_profile=profile))
        if profile.mode == 'disabled': return {'ready': True, 'reason': '只聊天，不开放执行或写文件'}
        if profile.mode == 'host_approved': return {'ready': True, 'reason': '使用当前电脑执行，没有操作系统级隔离'}
        if profile.mode == 'remote':
            return await self.platform.sandboxes.backends['remote'].health(profile.remote_connection)
        backend = self.platform.sandboxes.backends['isolated']
        health = await backend.health()
        if health['status'] != 'available':
            return {'ready': False, 'engine_connected': False, 'image_present': False,
                    'reason': 'Docker 引擎无法连接，请启动 Docker Desktop；也可主动选择只聊天或本机执行'}
        image = await backend._command(['image', 'inspect', profile.image], timeout=5)
        linux = await backend._command(['info', '--format', '{{.OSType}}'], timeout=5)
        present = image['exit_code'] == 0
        ready = present and linux['output'].strip() == 'linux'
        return {'ready': ready, 'engine_connected': True, 'image_present': present,
            'reason': '引擎与镜像就绪，实际容器创建仍需运行时确认' if ready else
                '请切换到 Linux 容器并下载所需镜像：docker pull ' + profile.image}

    async def switch(self, body):
        source = body.get('session_id')
        if source: await self.platform.service.require_session(source)
        selected=self.platform.new_binding_version(body['agent_id'], int(body['version']))
        self.platform.validate_execution_policy(AgentDefinition.model_validate(selected['snapshot']))
        if len(body.get('artifact_ids', []))>10: fail('artifact_limit')
        # Check every attachment BEFORE creating the new session.
        inputs = []
        for ident in body.get('artifact_ids', []):
            item, raw = await self.platform.artifact_content(ident)
            if item['session_id'] != source: fail('artifact_owner_mismatch')
            inputs.append((item, raw))
        sid = await self.platform.service.create_session()
        copies = []
        for item, raw in inputs:
            copies.append(await self.platform.upload({'session_id': sid, 'filename': item['filename'],
                'data_base64': base64.b64encode(raw).decode()}))
        return {'sid': sid, 'attachments': copies}
