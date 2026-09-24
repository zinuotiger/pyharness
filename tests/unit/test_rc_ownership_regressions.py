"""RC review regressions: cleanup retains ownership until handoff completes."""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from pyharness import engine, persistence
from pyharness.core.mcp import StdioTransport
from tests.support import isolated_settings


@pytest.mark.controlled_process
async def test_stdio_close_survives_repeated_cancellation(monkeypatch):
    tr = StdioTransport([sys.executable, '-I', '-B', '-u', '-c',
                         'import sys,time; sys.stdin.read(); time.sleep(.1)'])
    await tr._ensure()
    proc, stderr = tr._proc, tr._stderr_task
    entered, release = asyncio.Event(), asyncio.Event()
    real_wait = proc.wait
    async def wait():
        entered.set()
        await release.wait()
        return await real_wait()
    monkeypatch.setattr(proc, 'wait', wait)
    first = asyncio.create_task(tr.close())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        assert tr._proc is proc, 'unfinished close discarded its process owner'
        assert not first.done(), 'cancelled waiter abandoned cleanup'
        second = asyncio.create_task(tr.close())
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert proc.returncode is not None
        assert stderr.done()
        assert tr._proc is None and tr._stderr_task is None
        await tr.close()
    finally:
        release.set()
        await asyncio.gather(*([first, second] if second else [first]),
                             return_exceptions=True)
        if proc.returncode is None:
            proc.kill()
        await real_wait()
        stderr.cancel()
        await asyncio.gather(stderr, return_exceptions=True)


def record_stores(monkeypatch):
    stores = []
    real_open = engine.open_store
    def opening(*args, **kwargs):
        store = real_open(*args, **kwargs)
        stores.append(store)
        return store
    monkeypatch.setattr(engine, 'open_store', opening)
    return stores


def assert_released(store):
    assert store._fh.closed
    assert str(persistence.session_lock_path(store.path)) not in persistence._LOCKS


async def test_build_spine_workspace_failure_releases_store(tmp_path, monkeypatch):
    cfg = isolated_settings(tmp_path)
    workspace = tmp_path/'not-a-directory'
    workspace.write_text('synthetic', encoding='utf-8')
    cfg.storage.workspaces_dir = str(workspace)
    stores = record_stores(monkeypatch)
    bus = engine.EventBus()
    bus.subscribe('approval.denied', lambda *args: None, owner='unrelated-session')
    try:
        with pytest.raises(FileExistsError):
            await engine.build_spine(cfg, sid='s-rc-workspace',
                                     sessions_dir=tmp_path/'sessions', channel='cli', bus=bus)
        assert len(stores) == 1
        assert_released(stores[0])
        assert [sub.owner for group in bus._by_type.values() for sub in group] == ['unrelated-session']
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize('cancelled', [False, True])
@pytest.mark.parametrize('cleanup_fails', [False, True])
async def test_build_spine_open_failure_preserves_primary_and_releases_store(
        tmp_path, monkeypatch, cancelled, cleanup_fails):
    cfg = isolated_settings(tmp_path)
    stores = record_stores(monkeypatch)
    primary = asyncio.CancelledError('opening cancelled') if cancelled else RuntimeError('opening failed')
    async def failing_open(*args, **kwargs):
        if cleanup_fails:
            real_close = stores[-1].close
            def closing():
                real_close()
                raise OSError('injected close diagnostic')
            monkeypatch.setattr(stores[-1], 'close', closing)
        raise primary
    monkeypatch.setattr(engine, 'open_session', failing_open)
    try:
        with pytest.raises(type(primary)) as error:
            await engine.build_spine(cfg, sid='s-rc-open',
                                     sessions_dir=tmp_path/'sessions', channel='cli')
        assert error.value is primary
        assert_released(stores[0])
        if cleanup_fails:
            assert any('cleanup' in note for note in primary.__notes__)
    finally:
        for store in stores:
            persistence.SessionStore.close(store)


@pytest.mark.parametrize('cancelled', [False, True])
async def test_assemble_activation_failure_rolls_back_owned_spine(
        tmp_path, monkeypatch, adapter_factory, cancelled):
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    spines = []
    actual = engine.activate_orchestration
    primary = asyncio.CancelledError('activate cancelled') if cancelled else RuntimeError('activate failed')
    async def fail(spine, **kwargs):
        spines.append(spine)
        await actual(spine, **kwargs)
        raise primary
    monkeypatch.setattr(engine, 'activate_orchestration', fail)
    try:
        with pytest.raises(type(primary)) as error:
            await engine.assemble_real_engine(cfg, sid='s-rc-activation',
                                              sessions_dir=tmp_path/'sessions', channel='cli')
        assert error.value is primary
        spine = spines[0]
        assert_released(spine.persistence)
        assert spine.schedule._ticker_task is None
        assert spine.task_queue._closing
        assert spine.scope.policy is None
    finally:
        for spine in spines:
            await spine.close()
            spine.scope.release()


async def test_attach_activation_failure_does_not_close_borrowed_store(
        tmp_path, monkeypatch, adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    store = persistence.open_store('s-rc-borrowed', dir=tmp_path/'sessions')
    log = await engine.open_session('s-rc-borrowed', store)
    ctx = SimpleNamespace(bus=engine.EventBus())
    primary = RuntimeError('activate failed')
    async def fail(spine, **kwargs):
        spine.task_queue = kwargs['task_queue']
        raise primary
    monkeypatch.setattr(engine, 'activate_orchestration', fail)
    try:
        with pytest.raises(RuntimeError) as error:
            await engine.attach_engine_to_ctx(ctx, cfg, log_=log,
                sessions_dir=tmp_path/'sessions', store=store, channel='cli', preload=False)
        assert error.value is primary
        assert not store._fh.closed
        assert ctx.engine_spine.task_queue._closing
        assert ctx.engine_spine.scope.policy is None
    finally:
        if getattr(ctx, 'engine_spine', None):
            await ctx.engine_spine.close()
            ctx.engine_spine.scope.release()
        if getattr(ctx, 'llm_runtime', None):
            await ctx.llm_runtime.aclose()
        store.close()


@pytest.mark.controlled_process
async def test_stdio_failed_cleanup_can_be_retried(monkeypatch):
    tr = StdioTransport([sys.executable, '-I', '-B', '-u', '-c',
                         'import sys,time; sys.stdin.read(); time.sleep(.1)'])
    await tr._ensure()
    proc, stderr = tr._proc, tr._stderr_task
    real_wait = proc.wait
    async def failed_wait():
        raise OSError('injected wait failure')
    monkeypatch.setattr(proc, 'wait', failed_wait)
    try:
        with pytest.raises(OSError, match='injected wait failure'):
            await tr.close()
        assert tr._proc is proc and tr._stderr_task is stderr
        monkeypatch.setattr(proc, 'wait', real_wait)
        await tr.close()
        assert proc.returncode is not None and stderr.done()
        assert tr._proc is None and tr._stderr_task is None
    finally:
        monkeypatch.setattr(proc, 'wait', real_wait)
        if proc.returncode is None:
            proc.kill()
        await real_wait()
        stderr.cancel()
        await asyncio.gather(stderr, return_exceptions=True)


@pytest.mark.parametrize('cleanup_fails', [False, True])
async def test_activation_rollback_resists_second_cancel_and_preserves_primary(
        tmp_path, monkeypatch, adapter_factory, cleanup_fails):
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    entered, release = asyncio.Event(), asyncio.Event()
    spines = []
    primary = RuntimeError('original activation error')
    async def fail(spine, **kwargs):
        spine.task_queue = kwargs['task_queue']
        spines.append(spine)
        actual_close = spine.close
        async def close():
            entered.set()
            await release.wait()
            await actual_close()
            if cleanup_fails:
                raise OSError('injected cleanup diagnostic')
        monkeypatch.setattr(spine, 'close', close)
        raise primary
    monkeypatch.setattr(engine, 'activate_orchestration', fail)
    task = asyncio.create_task(engine.assemble_real_engine(cfg, sid='s-rc-rollback',
        sessions_dir=tmp_path/'sessions', channel='cli'))
    try:
        async with asyncio.timeout(3):
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(RuntimeError) as error:
                await task
        assert error.value is primary
        assert_released(spines[0].persistence)
        assert spines[0].scope.policy is None
        if cleanup_fails:
            assert any('engine cleanup failed' in note for note in primary.__notes__)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        for spine in spines:
            await engine.EngineSpine.close(spine)
            spine.scope.release()
