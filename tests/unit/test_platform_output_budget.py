from types import SimpleNamespace
import pytest
from pyharness.core.llm import UsageCounters
from pyharness.core.scope import BudgetExhausted
from pyharness.errors import PyHError
from tests.unit.test_llm import _adapter, _ctx, _session, _raw, _usage


async def test_platform_adapter_caps_remaining_output_and_blocks_exhaustion():
    counters = UsageCounters(out_tokens=60)
    session = await _session()
    ctx = _ctx(session, counters=counters)
    ctx.platform_runtime = SimpleNamespace(definition=SimpleNamespace(budget=SimpleNamespace(output_tokens=64)))
    adapter = _adapter(result=_raw('done', usage=_usage(7,4)))
    await adapter.chat([{'role':'user','content':'synthetic'}], ctx=ctx)
    assert adapter._transport.requests[0]['max_tokens'] == 4
    assert counters.out_tokens == 64
    with pytest.raises(BudgetExhausted):
        await adapter.chat([{'role':'user','content':'never sent'}], ctx=ctx)
    assert len(adapter._transport.requests) == 1


async def test_platform_adapter_missing_counter_fails_closed():
    ctx = _ctx(await _session())
    ctx.platform_runtime = SimpleNamespace(definition=SimpleNamespace(budget=SimpleNamespace(output_tokens=64)))
    adapter = _adapter(result=_raw('unused'))
    with pytest.raises(PyHError, match='CFG-601'):
        await adapter.chat([{'role':'user','content':'never sent'}], ctx=ctx)
    assert not adapter._transport.requests
