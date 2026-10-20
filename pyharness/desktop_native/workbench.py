"""One lazy loopback workbench borrowing the native registry and asyncio loop."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import secrets
import socket

import uvicorn

from pyharness.application import ApplicationServiceRegistry
from pyharness.application.accounts import current_actor
from pyharness.desktop.app import DesktopApp
from pyharness.events import EVENT_TYPES


class _SharedDesktopApp(DesktopApp):
    def __init__(self, controller):
        registry = getattr(controller, '_registry', None)
        if not isinstance(registry, ApplicationServiceRegistry):
            raise ValueError('native_workbench_requires_owned_registry')
        self._native_registry = registry
        self._native_bus_owner = 'native-workbench-' + secrets.token_hex(16)
        super().__init__(controller.ctx)

    def _create_service_registry(self, ctx):
        return self._native_registry

    def _subscribe_all(self, bus):
        return [bus.subscribe(name, self.hub.forward, owner=self._native_bus_owner)
                for name in (*EVENT_TYPES, 'plugin.*')]

    def detach(self):
        self.stopping.set()
        bus = getattr(self.ctx, 'bus', None)
        if bus is not None:
            bus.unsubscribe_all(self._native_bus_owner)
        self._sub = None
        self.hub.close_all()
        if self.server is not None:
            self.server.should_exit = True

    async def _shutdown_async(self):
        # The native controller alone owns shared service shutdown.
        self.detach()


class _EmbeddedServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # QApplication/qasync owns main-thread signals and application exit.
        yield


class NativeWorkbenchBridge:
    """Explicitly enabled HTTP details; no separate service or execution owner."""

    def __init__(self, controller):
        self.controller = controller
        self.desktop = None
        self._socket = None
        self._task = None
        self._server = None
        self._loop = None
        self._lock = asyncio.Lock()
        self._close_task = None
        self._dead = False
        self.base_url = ''

    async def start(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError('native_workbench_loop_changed')
        self._loop = loop
        async with self._lock:
            if self._dead:
                raise RuntimeError('native_workbench_closed')
            if self._task is not None:
                if self._task.done():
                    self._dead = True
                    raise RuntimeError('native_workbench_server_retired')
                return self.base_url
            try:
                self.desktop = _SharedDesktopApp(self.controller)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket = sock
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(('127.0.0.1', 0))
                sock.listen(64)
                sock.setblocking(False)
                port = sock.getsockname()[1]
                config = uvicorn.Config(self.desktop.api, host='127.0.0.1', port=port,
                    log_config=None, access_log=False, proxy_headers=False,
                    timeout_graceful_shutdown=5, limit_concurrency=64)
                self._server = _EmbeddedServer(config)
                self.desktop.server = self._server
                self.desktop.loop = loop
                self._task = asyncio.create_task(self._server.serve(sockets=[sock]),
                                                 name='native-workbench-server')
                deadline = loop.time() + 15
                while not self._server.started:
                    if self._task.done():
                        await self._task
                        raise RuntimeError('native_workbench_start_failed')
                    if self._dead or loop.time() >= deadline:
                        raise RuntimeError('native_workbench_start_unconfirmed')
                    await asyncio.sleep(.01)
                self.base_url = f'http://127.0.0.1:{port}'
                return self.base_url
            except BaseException as error:
                self._dead = True
                if self.desktop is not None:
                    self.desktop.detach()
                try:
                    await self._stop_task()
                except BaseException as cleanup_error:
                    error.add_note('workbench cleanup: ' + type(cleanup_error).__name__)
                finally:
                    if self._socket is not None:
                        self._socket.close()
                raise

    def url_for_current_identity(self):
        if self._dead or not self.base_url or self._task is None or self._task.done():
            raise RuntimeError('native_workbench_not_ready')
        # A native tenant selector is not an authenticated account. Never pass
        # a host bootstrap credential when a tenant/account context is active.
        if self.controller.tenant_id != 'default' or current_actor.get() is not None:
            return self.base_url + '/login'
        return self.desktop.bootstrap_url('127.0.0.1', self._socket.getsockname()[1])

    async def close(self):
        self._dead = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned(),
                                                   name='native-workbench-close')
        await asyncio.shield(self._close_task)

    async def _close_owned(self):
        async with self._lock:
            try:
                if self.desktop is not None:
                    self.desktop.detach()
                await self._stop_task()
            finally:
                if self._socket is not None:
                    self._socket.close()
                if self.desktop is not None:
                    self.desktop.detach()
                    self.desktop.server = None
                    self.desktop.loop = None
                self.base_url = ''

    async def _stop_task(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._task is None:
            return
        done, _ = await asyncio.wait({self._task}, timeout=8)
        if not done:
            self._task.cancel()
            done, _ = await asyncio.wait({self._task}, timeout=2)
        if not done:
            raise RuntimeError('native_workbench_shutdown_unconfirmed')
        if not self._task.cancelled():
            await self._task
