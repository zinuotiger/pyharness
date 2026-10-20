"""S07: every real adapter attempt, including system helpers and retries, is gated."""
from types import SimpleNamespace
import pytest
from pyharness.core.llm import LLMClient
from pyharness.core.llm_fallback import FallbackChain
from pyharness.errors import PyHError
from tests.unit.test_llm_fallback import FakeAdapter, _ctx, _cfg, MAIN, BACKUP


class StreamingAdapter(FakeAdapter):
    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)


async def request(client, name, ctx):
    if name in ('chat', 'chat_stream'):
        return await getattr(client, name)([dict(role='user', content='private task')], ctx=ctx)
    if name == 'json_chat':return await client.json_chat('return JSON', ctx=ctx)
    return await getattr(client, name)('private task', ctx=ctx)


@pytest.mark.parametrize('method', ['chat', 'chat_stream', 'mini', 'summarize', 'json_chat'])
@pytest.mark.parametrize('with_chain', [False, True])
async def test_all_facade_methods_deny_before_adapter(method, with_chain):
    cfg = _cfg(); cfg.llm.retry.base_delay_s = 0.001
    adapter = StreamingAdapter(resp=SimpleNamespace(content='{}', tool_calls=None, model=MAIN))
    chain = FallbackChain(adapters={MAIN: adapter, BACKUP: adapter}, config=cfg) if with_chain else None
    client = LLMClient(cfg, registry={MAIN: adapter}, chain=chain)
    checks = []
    async def denied(model, operation, ctx):
        checks.append((model, operation)); raise PyHError('GRD-403')
    ctx = _ctx(config=cfg); ctx.platform_runtime = SimpleNamespace(before_model_attempt=denied)
    with pytest.raises(PyHError, match='GRD-403'):await request(client, method, ctx)
    assert len(checks) == 1 and adapter.chats == []


async def test_retry_rechecks_authority_and_never_degrades_after_denial():
    cfg = _cfg(); cfg.llm.retry.base_delay_s = 0.001
    adapter = StreamingAdapter(chat_script=[PyHError('LLM-301')])
    backup = StreamingAdapter()
    chain = FallbackChain(adapters={MAIN: adapter, BACKUP: backup}, config=cfg)
    ctx = _ctx(config=cfg); checks = []
    async def boundary(model, method, context):
        checks.append(model)
        if len(checks) == 2:raise PyHError('GRD-403')
    ctx.platform_runtime = SimpleNamespace(before_model_attempt=boundary)
    with pytest.raises(PyHError, match='GRD-403'):
        await chain.chat_with_fallback([], ctx=ctx)
    assert checks == [MAIN, MAIN] and len(adapter.chats) == 1 and backup.chats == []


async def test_each_fallback_adapter_is_bound_separately():
    cfg = _cfg(); main = StreamingAdapter(always=PyHError('LLM-302')); backup = StreamingAdapter()
    chain = FallbackChain(adapters={MAIN: main, BACKUP: backup}, config=cfg)
    ctx = _ctx(config=cfg); checks = []
    async def boundary(model, method, context):checks.append((model, method))
    ctx.platform_runtime = SimpleNamespace(before_model_attempt=boundary)
    await chain.chat_with_fallback([], ctx=ctx, method='chat_stream')
    assert checks == [(MAIN, 'chat_stream'), (BACKUP, 'chat_stream')]
    assert len(main.chats) == len(backup.chats) == 1


@pytest.mark.parametrize('method', ['chat', 'chat_stream'])
async def test_permission_change_while_logging_request_stops_actual_transport(method, monkeypatch):
    from tests.unit.test_llm import FakeTransport, _session, _ctx as transport_ctx, _raw, _chunk
    from pyharness.core.llm import OpenAICompatAdapter, UsageCounters
    session = await _session(); ctx = transport_ctx(session, counters=UsageCounters())
    state = {'allowed': True}; checks = []
    async def boundary(model, operation, context):
        checks.append(state['allowed'])
        if not state['allowed']:raise PyHError('GRD-403')
    ctx.platform_runtime = SimpleNamespace(before_model_attempt=boundary,
        definition=SimpleNamespace(budget=SimpleNamespace(output_tokens=20000)))
    append = session.append
    async def changed(type_, payload, **kwargs):
        event = await append(type_, payload, **kwargs)
        if type_ == 'llm.request':state['allowed'] = False
        return event
    monkeypatch.setattr(session, 'append', changed)
    transport = FakeTransport(result=_raw('{}'), chunks=[_chunk('{}', finish='stop')])
    adapter = OpenAICompatAdapter(MAIN, transport=transport)
    client = LLMClient(model=MAIN, registry={MAIN: adapter})
    with pytest.raises(PyHError, match='GRD-403'):await request(client, method, ctx)
    assert checks == [True, False] and transport.requests == []
