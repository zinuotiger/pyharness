"""Owned, bounded stdio transport for the supported Codex app-server protocol."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import re
from typing import Any


class CodexDisconnected(RuntimeError):
    def __init__(self, message):
        self.public_message = str(message)[:2000]
        super().__init__(self.public_message)


class CodexRpcError(CodexDisconnected):
    def __init__(self, code, message):
        from pyharness.config import redact
        self.code = code
        self.public_message = redact(str(message))[:2000]
        super().__init__('Codex RPC rejected '+str(code)+': '+self.public_message)


def codex_command(executable: str | None = None) -> list[str]:
    selected = executable or os.environ.get('PYHARNESS_CODEX_BIN')
    if not selected:
        local = Path(__file__).resolve().parents[2]/'tooling'/'codex'/'runtime.json'
        if local.is_file():
            selected = json.loads(local.read_text(encoding='utf-8-sig')).get('executable')
    if selected:
        path = Path(selected).expanduser()
        if not path.is_absolute() or not path.is_file() or (os.name=='nt' and path.suffix.lower() not in {'.exe','.js'}):
            raise FileNotFoundError('Codex executable must be an existing absolute executable or CLI entrypoint')
        if path.suffix.lower()=='.js':
            node = shutil.which('node')
            if not node:
                raise FileNotFoundError('Node.js is required for the official npm Codex entrypoint')
            return [node,str(path),'app-server']
        executable = str(path)
    else:
        executable = shutil.which('codex')
    if not executable:
        raise FileNotFoundError('Codex CLI is not installed')
    # npm shims cannot be launched directly by CreateProcess on Windows.
    if os.name == 'nt' and Path(executable).suffix.lower() in {'.cmd', '.bat'}:
        return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', executable, 'app-server']
    return [executable, 'app-server']


class CodexTransport:
    def __init__(self, command: list[str] | None = None, *, timeout: float = 30, executable: str | None = None):
        self.command = command
        self.executable = executable
        self.timeout = timeout
        self.process: asyncio.subprocess.Process | None = None
        self.events: asyncio.Queue[dict[str, Any] | BaseException] = asyncio.Queue(512)
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._closed = False

    async def connect(self):
        if self.process or self._closed:
            raise CodexDisconnected('transport cannot be reused')
        command = self.command or codex_command(self.executable)
        if self.command is None:
            version_process = await asyncio.create_subprocess_exec(*command[:-1],'--version',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:
                version_output,_ = await asyncio.wait_for(version_process.communicate(),5)
            except BaseException:
                version_process.kill()
                await version_process.communicate()
                raise
            matched = re.search(r'(\d+)\.(\d+)\.(\d+)',version_output.decode(errors='replace'))
            if not matched or tuple(map(int,matched.groups())) < (0,162,1):
                raise CodexDisconnected('Codex CLI 0.162.1 or newer is required; configure the project-local client')
            self.client_version = version_output.decode(errors='replace').strip()[:100]
            # Supported read-only CLI inventory does not start MCP providers.
            # Explicit per-server overrides are necessary: an empty table merges
            # with inherited configuration instead of deleting its entries.
            inventory = await asyncio.create_subprocess_exec(*command[:-1],'mcp','list','--json',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:
                raw,_ = await asyncio.wait_for(inventory.communicate(),10)
            except BaseException:
                inventory.kill()
                await inventory.communicate()
                raise
            if inventory.returncode:
                raise CodexDisconnected('Cannot inspect inherited Codex capabilities')
            names = [entry['name'] for entry in json.loads(raw) if entry.get('enabled',True)]
            overrides = []
            for name in names:
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',name):
                    raise CodexDisconnected('Unsupported inherited MCP server name')
                # Plugin-supplied entries may not exist in the base TOML layer.
                # Supply a valid, disabled definition instead of creating an
                # incomplete transport with a dotted enabled-only override.
                overrides.extend(['-c','mcp_servers.'+name+'={enabled=false,command='+json.dumps(sys.executable)+'}'])
            command = [*command[:-1],*overrides,'-c','features.apps=false','-c','web_search="disabled"',command[-1]]
            checked = await asyncio.create_subprocess_exec(*command[:-1],'mcp','list','--json',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:
                effective_raw,_ = await asyncio.wait_for(checked.communicate(),10)
            except BaseException:
                checked.kill()
                await checked.communicate()
                raise
            if checked.returncode or any(s.get('enabled',True) for s in json.loads(effective_raw)):
                raise CodexDisconnected('Inherited MCP capabilities could not be disabled')
            self.disabled_mcp_servers = tuple(s['name'] for s in json.loads(effective_raw))
        self.process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=4*1024*1024,
            env={**os.environ,'PATH':str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')})
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._drain_stderr())
        try:
            result = await self.request('initialize', {'clientInfo': {
                'name': 'pyharness', 'title': 'PyHarness', 'version': '0.1.0'},
                'capabilities':{'experimentalApi':True}})
            await self.send({'method': 'initialized', 'params': {}})
            return result
        except BaseException:
            await self.close()
            raise

    async def _drain_stderr(self):
        # Drain without leaking account/config secrets into logs or blocking the child.
        while await self.process.stderr.read(8192):
            pass

    async def send(self, message: dict):
        async with self._write_lock:
            if self._closed or not self.process or self.process.returncode is not None:
                raise CodexDisconnected('Codex process is closed')
            self.process.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b'\n')
            await self.process.stdin.drain()

    async def request(self, method: str, params: dict):
        self._next_id += 1
        ident = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[ident] = future
        try:
            await self.send({'id': ident, 'method': method, 'params': params})
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self._pending.pop(ident, None)

    async def _read(self):
        failure = CodexDisconnected('Codex stream ended before completion')
        try:
            while raw := await self.process.stdout.readline():
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('invalid protocol message')
                ident = message.get('id')
                if 'method' not in message and ident in self._pending:
                    waiter = self._pending[ident]
                    if not waiter.done():
                        if 'error' in message:
                            waiter.set_exception(CodexRpcError(message['error'].get('code'),message['error'].get('message','')))
                        else:
                            waiter.set_result(message.get('result', {}))
                elif 'method' in message:
                    # Overflow is a connection failure, never silent loss of a terminal.
                    self.events.put_nowait(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = CodexDisconnected('Codex protocol failure: ' + type(exc).__name__)
        finally:
            for waiter in self._pending.values():
                if not waiter.done():
                    waiter.set_exception(failure)
            if self.events.full():
                # Discarding buffered progress is safe only when the entire turn fails.
                while not self.events.empty():
                    self.events.get_nowait()
            self.events.put_nowait(failure)

    async def next_event(self) -> dict:
        value = await self.events.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self):
        if self._closed:
            return
        self._closed = True
        proc = self.process
        if proc:
            if proc.stdin:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                if os.name == 'nt':
                    # Fixed integer child PID; terminate the owned npm shim and descendants.
                    killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(proc.pid), '/T', '/F',
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()
                else:
                    proc.kill()
                await proc.wait()
        for task in (self._reader, self._stderr):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self._reader, self._stderr) if t), return_exceptions=True)
        if proc:
            await proc.communicate()  # Reap and close all Windows pipe handles too.

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.close()
