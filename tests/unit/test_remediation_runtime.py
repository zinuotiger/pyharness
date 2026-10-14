import asyncio
from types import SimpleNamespace

import pytest

from pyharness import persistence
from pyharness.core.tools_executor import ToolExecutor
from pyharness.core.tools_guard import GuardChain, ToolCall
from tests.unit.test_tools_executor import AsyncSess, FakeScope, make_ctx, make_registry, mk_defn


async def test_F15_cancel_current_owns_task(tmp_path):
    entered, finished = asyncio.Event(), asyncio.Event()
    async def handle(args, ctx):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
    reg = make_registry(mk_defn(), providers={'fs.read_file': handle})
    sess = AsyncSess()
    ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess, validator=reg.validate_args))
    ex = ToolExecutor(reg)
    task = asyncio.create_task(ex.execute(ToolCall(name='fs.read_file', raw_args={'path':'x'}, call_id='cancel-1'), ctx=ctx))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert await ex.cancel_current() is True
        assert await ex.cancel_current() is False
        with pytest.raises(asyncio.CancelledError): await task
        assert finished.is_set()
        assert ex._active is None
        assert len(sess.of('tool.result')) == 1
        assert sess.of('tool.result')[0]['payload']['ok'] is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_F15_sync_success_releases_pool(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pyharness.core import tools_executor as module
    pools = []
    class Pool(ThreadPoolExecutor):
        def __init__(self, **kw):
            super().__init__(**kw)
            pools.append(self)
    monkeypatch.setattr(module, 'ThreadPoolExecutor', Pool)
    reg = make_registry(mk_defn(), providers={'fs.read_file': lambda args,ctx:'done'})
    ex = ToolExecutor(reg)
    try:
        assert await ex._run_provider(mk_defn(), {}, SimpleNamespace()) == 'done'
        assert pools[0]._shutdown
        assert all(not t.is_alive() for t in pools[0]._threads)
    finally:
        for pool in pools: pool.shutdown(wait=True)


async def test_F14_replay_does_not_acquire_writer(e2e_factory):
    session = await e2e_factory(sid='s-replay-remediation')
    await session.boot()
    baseline = {key: entry[1] for key, entry in persistence._LOCKS.items()}
    for _ in range(3):
        assert 'session.created' in await session.replay()
    assert {key: entry[1] for key, entry in persistence._LOCKS.items()} == baseline


async def test_F08_counter_belongs_to_current_context():
    from pyharness.core import llm
    a, b = llm.UsageCounters(), llm.UsageCounters()
    adapter = llm.OpenAICompatAdapter('synthetic', counters=a)
    ctx = SimpleNamespace(session=AsyncSess(), counters=b)
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=7)
    await adapter.report_usage(usage, 'synthetic', ctx=ctx)
    assert a.task_total().in_tokens == 0
    assert b.task_total().in_tokens == 12


def test_F08_same_model_different_endpoint_has_distinct_adapter(tmp_path):
    from pyharness.core import llm
    from pyharness.engine import register_default_llm
    from tests.support import isolated_settings
    saved = dict(llm.adapters)
    try:
        a, b = isolated_settings(tmp_path/'a'), isolated_settings(tmp_path/'b')
        a.llm.base_url = 'http://127.0.0.1:8'
        b.llm.base_url = 'http://127.0.0.1:9'
        ka = register_default_llm(a)
        kb = register_default_llm(b)
        assert ka != kb
        assert register_default_llm(a) == ka
    finally:
        llm.adapters.clear()
        llm.adapters.update(saved)


async def test_R04_standalone_spine_closes_owned_transport(tmp_path):
    from pyharness.core import llm
    from pyharness.engine import assemble_real_engine
    from tests.support import isolated_settings
    cfg = isolated_settings(tmp_path)
    cfg.llm.fallback_models = []
    saved = dict(llm.adapters)
    llm.adapters.clear()
    class Transport:
        closed = False
        async def aclose(self): self.closed = True
    transport = Transport()
    try:
        ctx = await assemble_real_engine(cfg, sid='s-owned-transport', sessions_dir=tmp_path/'sessions', channel='cli')
        adapter = ctx.llm.require()
        adapter._transport = transport
        await ctx.engine_spine.close()
        assert transport.closed
        await ctx.engine_spine.close()
    finally:
        llm.adapters.clear()
        llm.adapters.update(saved)
