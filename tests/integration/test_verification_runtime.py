"""Independent local verification: real service, queue, storage and MCP.

Only the model adapter and a failing disk write boundary are controlled doubles.
"""
import asyncio
from pathlib import Path
import sys

import httpx
import pytest

from pyharness import cli
from pyharness.application.service import ApplicationService
from pyharness.config import McpServerCfg, Settings
from pyharness.desktop import DesktopApp
from pyharness.errors import PyHError


def local_settings(tmp_path):
    cfg = Settings()
    for name, rel in {'root': '.', 'sessions_dir': 'sessions',
                      'workspaces_dir': 'workspaces', 'spill_dir': 'spill',
                      'db_path': 'index.db'}.items():
        setattr(cfg.storage, name, str(tmp_path / rel))
    cfg.llm.fallback_models = []
    cfg.plugins.dir = str(tmp_path / 'plugins')
    cfg.skills.dir = str(tmp_path / 'skills')
    return cfg


class BrokenWriter:
    def __init__(self, real):
        self.real = real

    def write(self, value):
        raise OSError('verification: controlled disk write failure')

    def __getattr__(self, name):
        return getattr(self.real, name)


async def test_api_roundtrip_and_normal_restart(tmp_path, adapter_factory):
    cfg = local_settings(tmp_path)
    adapter_factory(cfg)
    app = DesktopApp(ctx=cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                base_url='http://127.0.0.1',
                headers={'X-PyHarness-Token': app._api_token}) as client:
            created = await client.post('/api/sessions')
            assert created.status_code == 200
            sid = created.json()['sid']
            response = await client.post(f'/api/sessions/{sid}/messages',
                                         json={'text': '独立验收 synthetic hello'})
            assert response.status_code == 200
            result = await app.service._queues[sid].wait_for(
                response.json()['task_id'], timeout=10)
            assert result.ok
            messages = (await client.get(f'/api/sessions/{sid}/messages')).json()
            assert any(m['content'] == 'done' for m in messages['messages'])
    finally:
        await app.service.shutdown()
    restarted = ApplicationService(cli.assemble_ctx(cfg))
    try:
        recovered = await restarted.session_messages(sid)
        assert recovered['messages'] == messages['messages']
        events = list((await restarted.require_session(sid)).events_after(0))
        assert any(e.type == 'task.completed' for e in events)
        assert any(e.type == 'segment.end' for e in events)
    finally:
        await restarted.shutdown()


async def test_api_storage_failure_and_queue_rejection_are_not_success(
        tmp_path, adapter_factory):
    cfg = local_settings(tmp_path)
    adapter = adapter_factory(cfg)
    app = DesktopApp(ctx=cli.assemble_ctx(cfg))
    original = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                base_url='http://127.0.0.1',
                headers={'X-PyHarness-Token': app._api_token}) as client:
            sid = (await client.post('/api/sessions')).json()['sid']
            queue = await app.service.queue_for(sid)
            store = app.service._engines[sid].persistence
            original = store._fh
            store._fh = BrokenWriter(original)
            failed = await client.post(f'/api/sessions/{sid}/messages',
                                       json={'text': 'must not execute'})
            assert failed.status_code == 500, failed.text
            assert 'PERS-202' in failed.text
            assert queue.status().depth == 0 and adapter.n == 0
            store._fh = original
            await store.flush()
            await queue.pause('verification')
            queue._max_queue = 1
            await queue.submit('accepted waiting')
            rejected = await client.post(f'/api/sessions/{sid}/messages',
                                         json={'text': 'queue must reject'})
            assert rejected.status_code == 503, rejected.text
            assert 'QUE-001' in rejected.text
            assert queue.status().depth == 1 and adapter.n == 0
    finally:
        if original is not None:
            store._fh = original
        await app.service.shutdown()


async def test_rejected_enqueue_leaves_no_waiting_task(tmp_path, adapter_factory):
    cfg = local_settings(tmp_path)
    adapter = adapter_factory(cfg)
    service = ApplicationService(cli.assemble_ctx(cfg))
    try:
        sid = await service.create_session()
        log = await service.require_session(sid)
        queue = await service.queue_for(sid)
        await log.append('session.finished', {'reason': 'done'}, actor='system')
        with pytest.raises(PyHError) as error:
            await queue.submit('must not remain queued')
        assert error.value.code == 'EVT-104'
        assert queue.status().depth == 0
        assert adapter.n == 0
    finally:
        await service.shutdown()


async def test_suspended_store_recovery_never_acknowledges_a_lost_message(
        tmp_path, adapter_factory):
    cfg = local_settings(tmp_path)
    adapter_factory(cfg)
    service = ApplicationService(cli.assemble_ctx(cfg))
    original = None
    try:
        sid = await service.create_session()
        await service.queue_for(sid)
        store = service._engines[sid].persistence
        original = store._fh
        store._fh = BrokenWriter(original)
        for _ in range(3):
            with pytest.raises(PyHError):
                await service.create_message(sid, 'disk unavailable')
        assert store._suspended
        store._fh = original
        try:
            submitted = await service.create_message(sid, 'RECOVERY MUST NOT LOSE THIS')
        except PyHError as exc:
            assert exc.code == 'PERS-202'
            await store.flush()  # 正常恢复入口；重试必须真正落盘且只出现一次。
            submitted = await service.create_message(sid, 'RECOVERY MUST NOT LOSE THIS')
            assert (await service._queues[sid].wait_for(submitted['task_id'], timeout=10)).ok
            await store.flush()
            assert sum(e.type == 'user.message' and
                       e.payload['content'] == 'RECOVERY MUST NOT LOSE THIS'
                       for e in store.replay()) == 1
        else:
            await service._queues[sid].wait_for(submitted['task_id'], timeout=10)
            await store.flush()
            assert any(e.type == 'user.message' and
                       e.payload['content'] == 'RECOVERY MUST NOT LOSE THIS'
                       for e in store.replay()), 'successful submission lost its user message'
    finally:
        if original is not None:
            store._fh = original
        await service.shutdown()


async def test_cancelled_admission_cannot_execute_behind_running_task(
        tmp_path, adapter_factory):
    cfg = local_settings(tmp_path)
    adapter = adapter_factory(cfg)
    entered, release, admitting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_chat = adapter.chat

    async def slow_model(messages, tools=None, *, ctx):
        entered.set()
        await release.wait()
        return await real_chat(messages, tools, ctx=ctx)

    adapter.chat = slow_model
    service = ApplicationService(cli.assemble_ctx(cfg))
    admission = None
    try:
        sid = await service.create_session()
        queue = await service.queue_for(sid)
        first = await queue.submit('first')
        await asyncio.wait_for(entered.wait(), 5)

        async def hold_admission(event):
            if event.payload['task_id'] == 't-2':
                admitting.set()
                await asyncio.Event().wait()

        service.ctx.bus.subscribe('task.enqueued', hold_admission,
                                  owner='verification-admission')
        admission = asyncio.create_task(queue.submit('unaccepted second'))
        await asyncio.wait_for(admitting.wait(), 5)
        release.set()
        assert (await queue.wait_for(first, timeout=5)).ok
        first_calls = adapter.n  # 正常任务还可能通过 mini 出口生成标题。
        with pytest.raises(PyHError) as error:
            await queue.wait_for('t-2', timeout=0.05)
        assert error.value.code == 'BUSY'
        admission.cancel()
        with pytest.raises(asyncio.CancelledError):
            await admission
        assert queue.status().depth == 0 and adapter.n == first_calls
        assert not any(e.type == 'task.started' and e.payload['task_id'] == 't-2'
                       for e in (await service.require_session(sid)).events_after(0))
        cancelled = await queue.wait_for('t-2', timeout=0.1)
        assert not cancelled.ok and cancelled.code == 'cancelled'
        assert 't-2' not in queue._futures
    finally:
        release.set()
        if admission is not None and not admission.done():
            admission.cancel()
            await asyncio.gather(admission, return_exceptions=True)
        await service.shutdown()


async def test_storage_failure_during_task_settles_waiter(tmp_path, adapter_factory):
    cfg = local_settings(tmp_path)
    adapter = adapter_factory(cfg)
    entered, release = asyncio.Event(), asyncio.Event()
    real_chat = adapter.chat

    async def held_model(messages, tools=None, *, ctx):
        entered.set()
        await release.wait()
        return await real_chat(messages, tools, ctx=ctx)

    adapter.chat = held_model
    service = ApplicationService(cli.assemble_ctx(cfg))
    original = None
    try:
        sid = await service.create_session()
        submitted = await service.create_message(sid, 'in-flight disk failure')
        await asyncio.wait_for(entered.wait(), 5)
        queue = service._queues[sid]
        log = await service.require_session(sid)
        store = service._engines[sid].persistence
        original = store._fh
        store._fh = BrokenWriter(original)
        for _ in range(3):
            with pytest.raises(PyHError):
                await log.append('user.message', {'content': 'disk fault'}, actor='user', sync=True)
        release.set()
        result = await queue.wait_for(submitted['task_id'], timeout=2)
        assert not result.ok and result.code == 'PERS-202'
        assert submitted['task_id'] not in queue._futures
        assert queue._exec is None
    finally:
        release.set()
        if original is not None:
            store._fh = original
            await store.flush()
        await service.shutdown()


@pytest.mark.controlled_process
async def test_mcp_configuration_uses_real_process_and_closes_it(
        tmp_path, adapter_factory):
    cfg = local_settings(tmp_path)
    cfg.plugins.mcp_servers = [McpServerCfg(name='verifyecho', command=[sys.executable,
        str(Path(__file__).resolve().parents[2] / 'scripts/mcp_echo_server.py')])]
    cfg.security.policy.preset = 'standard'
    adapter_factory(cfg, [{'id': 'verify-mcp', 'name': 'mcp.verifyecho.echo',
                          'args': {'text': 'controlled MCP 你好'}}])
    service = ApplicationService(cli.assemble_ctx(cfg))
    try:
        sid = await service.create_session()
        _, spine = await service.public_spine_for(sid)
        client = spine._mcp_clients[0]
        process = client.transport._proc
        assert process.returncode is None
        submitted = await service.create_message(sid, 'call controlled MCP')
        for _ in range(200):
            pending = spine.approval._pending
            if pending:
                break
            await asyncio.sleep(0.01)
        assert pending, 'MCP high danger tool must require approval'
        log = await service.require_session(sid)
        assert not any(e.type == 'tool.result' for e in log.events_after(0))
        await spine.approval.approve_async(next(iter(pending)), by='desktop')
        assert (await spine.task_queue.wait_for(submitted['task_id'], timeout=10)).ok
        results = [e for e in log.events_after(0) if e.type == 'tool.result']
        assert len(results) == 1 and 'controlled MCP 你好' in str(results[0].payload)
        audit = await service.governance_audit(sid, reconcile=True)
        assert not audit['findings']
    finally:
        await service.shutdown()
    assert process.returncode is not None
