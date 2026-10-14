"""Tenant-scoped client for the remote Docker worker (no host fallback)."""
from __future__ import annotations
import asyncio
import ipaddress
from urllib.parse import urlsplit
import uuid
import httpx
from pyharness.core.sandbox import fail


def validate_address(address):
    parsed = urlsplit(address)
    loopback = parsed.hostname == 'localhost'
    try: loopback |= ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError: pass
    if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback)) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        fail('remote_address_requires_https')
    return address.rstrip('/')


class RemoteSandboxBackend:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport
        self.handles = {}

    async def request(self, connection, method, path, body=None):
        address, token = self.settings(connection)
        if not token: fail('remote_credential_missing')
        try:
            async with httpx.AsyncClient(base_url=validate_address(address),
                    headers={'Authorization': 'Bearer ' + token}, timeout=15,
                    follow_redirects=False, trust_env=False, transport=self.transport) as client:
                response = await client.request(method, path, json=body)
                if response.status_code >= 400:
                    fail('remote_request_failed', 'HTTP ' + str(response.status_code))
                return response.json()
        except (httpx.HTTPError, ValueError):
            fail('remote_disconnected', '远程服务连接中断；未改用本机执行')

    async def health(self, connection):
        return await self.request(connection, 'GET', '/v1/health')

    async def create(self, record, root, profile):
        from pyharness.core.workspace_codec import pack_files
        task = uuid.uuid4().hex
        self.handles[record.sandbox_id] = (profile.remote_connection, task, root, profile)
        await self.request(profile.remote_connection, 'POST', '/v1/tasks/' + task,
            {'profile': profile.model_dump(), 'files': pack_files(root)})

    async def execute(self, record, command, *, python=False):
        connection, task, root, profile = self.handles[record.sandbox_id]
        operation = uuid.uuid4().hex
        try:
            await self.request(connection, 'POST', f'/v1/tasks/{task}/execute',
                {'command': command, 'python': python, 'operation': operation})
            async with asyncio.timeout(profile.timeout + 20):
                while True:
                    result = await self.request(connection, 'GET', '/v1/tasks/' + task)
                    if result['operation'] != operation: fail('remote_operation_mismatch')
                    if result['status'] == 'completed': return result['result']
                    if result['status'] in ('failed', 'cancelled'): fail('remote_execution_failed')
                    await asyncio.sleep(.2)
        except BaseException:
            # A disconnected client never resubmits code. Best effort cancellation;
            # worker timeout/lease cleanup remains authoritative if unreachable.
            try: await asyncio.shield(self.destroy(record))
            except Exception: pass
            raise

    async def download(self, record):
        from pyharness.core.workspace_codec import unpack_files
        connection, task, root, profile = self.handles[record.sandbox_id]
        result = await self.request(connection, 'POST', f'/v1/tasks/{task}/files', {})
        # Replace the local mirror only after validating every remote path/content.
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory(dir=root.parent) as temporary:
            stage = __import__('pathlib').Path(temporary)
            unpack_files(stage, result['files'])
            for item in list(root.iterdir()):
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
            for item in stage.iterdir(): shutil.move(str(item), str(root / item.name))

    async def upload(self, record):
        from pyharness.core.workspace_codec import pack_files
        connection, task, root, profile = self.handles[record.sandbox_id]
        await self.request(connection, 'POST', f'/v1/tasks/{task}/files', {'files': pack_files(root)})

    async def file_operation(self, record, name, args):
        connection, task, *_ = self.handles[record.sandbox_id]
        return (await self.request(connection, 'POST', f'/v1/tasks/{task}/file-operation', {'name': name, 'args': args}))['value']

    async def destroy(self, record):
        handle = self.handles.get(record.sandbox_id)
        if handle:
            await self.request(handle[0], 'DELETE', '/v1/tasks/' + handle[1])
            self.handles.pop(record.sandbox_id, None)
