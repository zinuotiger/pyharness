"""Real asyncio deadline/cancellation regressions; no model or network calls."""
import asyncio

import pytest

from pyharness.config import Settings
from pyharness.core.llm_fallback import FallbackChain
from pyharness.errors import PyHError


@pytest.mark.parametrize("failed_ping", [False, True])
async def test_probe_completion_racing_cancel_does_not_leave_background_task(failed_ping):
    task = None

    class Adapter:
        async def ping(self):
            # Cancel the owner at the same loop boundary that completes ping.
            # Python 3.11 wait_for used to consume this external cancellation.
            asyncio.get_running_loop().call_soon(task.cancel)
            if failed_ping:
                raise PyHError("LLM-303")
            return 0.01

    chain = FallbackChain(adapters={"probe": Adapter()},
        config=Settings(llm={"model": "probe", "fallback_models": []}))
    task = asyncio.create_task(chain.probe_loop(interval_s=60))
    try:
        done, pending = await asyncio.wait({task}, timeout=0.5)
        assert task in done, "probe lost cancellation when ping completed"
        assert not pending and task.cancelled()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_real_probe_deadline_marks_failure_and_releases_adapter(monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr("pyharness.core.llm_fallback.asyncio.timeout",
                        lambda seconds: real_timeout(0.001))
    released = asyncio.Event()

    class Adapter:
        async def ping(self):
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

    chain = FallbackChain(adapters={"probe": Adapter()},
        config=Settings(llm={"model": "probe", "fallback_models": []}))
    task = asyncio.create_task(chain.probe_loop(interval_s=60))
    try:
        async with real_timeout(1):
            await released.wait()
            while chain.health["probe"].state == "healthy":
                await asyncio.sleep(0)
        assert chain.health["probe"].state == "degraded"
        assert not task.done(), "probe timeout should mark failure, not kill the loop"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
