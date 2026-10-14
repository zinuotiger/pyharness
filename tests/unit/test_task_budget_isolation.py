"""Correct-behavior regressions for audit BUG-01/02; no model network."""
import asyncio
from types import SimpleNamespace

import pytest

from pyharness import cli
from pyharness.core.llm import UsageCounters, OpenAICompatAdapter
from pyharness.core.scope import Scope, ScopePolicy, BudgetLimits, BudgetExhausted
from pyharness.core.subagent import SubagentManager
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


def limits(out=100):
    return BudgetLimits(max_in_tokens=1000, max_out_tokens=out, max_cost_yuan=10)


async def test_continuous_runs_have_fresh_budgets_and_session_keeps_usage(tmp_path, adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter = adapter_factory(cfg)
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        platform = app.service.platform
        agent = platform.save_agent({'name': 'budget regression', 'budget': {
            'input_tokens': 100, 'output_tokens': 4000, 'tool_calls': 20, 'cost': 100}})
        platform.publish_agent(agent['agent_id'])
        first = await platform.start_run({'text': 'first synthetic', 'agent_id': agent['agent_id'], 'agent_version': 1})
        queue = app.service._queues[first['session_id']]
        assert (await asyncio.wait_for(queue.wait_for(first['task_id']), 8)).ok
        calls = adapter.n
        second = await platform.start_run({'text': 'second synthetic', 'session_id': first['session_id']})
        assert (await asyncio.wait_for(queue.wait_for(second['task_id']), 8)).ok
        assert adapter.n == calls + 1
        spine = app.service._engines[first['session_id']]
        assert spine.counters.in_tokens == 200
        assert spine.scope._counters.task_total().in_tokens == 100
    finally:
        await app.service.shutdown()


def test_child_starts_at_zero_and_charges_parent_aggregate(tmp_path):
    session = UsageCounters()
    parent = session.new_task(limits())
    parent.task_add({'model': 'synthetic', 'in_tokens': 30, 'out_tokens': 30})
    scope = Scope(ScopePolicy(workspace_root=str(tmp_path)), limits(), 'parent', counters=parent)
    child = SubagentManager()._default_child_scope(scope, 'child', None, limits(25))
    assert child.budget_state() == 'ok'
    assert child._counters.task_total().in_tokens == 0
    child._counters.task_add({'model': 'synthetic', 'in_tokens': 10, 'out_tokens': 20})
    assert parent.out_tokens == 30  # Own usage does not include the child.
    assert parent.task_total().out_tokens == session.out_tokens == 50
    assert child._counters.task_total().out_tokens == 20
    child.release()
    scope.release()


def test_siblings_reserve_shared_ancestor_allowance_and_release_idempotently():
    session = UsageCounters()
    parent = session.new_task(limits())
    a, b = (parent.new_task(limits(80), parent=parent) for _ in range(2))
    first, release_a = a.reserve_output(80)
    second, release_b = b.reserve_output(80)
    assert (first, second) == (80, 20)
    with pytest.raises(BudgetExhausted):
        parent.reserve_output(1)
    a.task_add({'model': 'synthetic', 'out_tokens': 60})
    release_a(); release_a(); release_b()
    assert parent._reserved_out == 0
    remaining, release = b.reserve_output(80)
    assert remaining == 40
    release()


def test_sibling_input_and_priced_cost_are_reserved_before_transport():
    parent=UsageCounters().new_task(limits())
    a,b=(parent.new_task(limits(),parent=parent) for _ in range(2))
    _,release=a.reserve_output(20,input_tokens=900,input_rate=.001,output_rate=.2)
    with pytest.raises(BudgetExhausted): b.reserve_output(10,input_tokens=101)
    with pytest.raises(BudgetExhausted): b.reserve_output(10,input_tokens=10,input_rate=1)
    release()
    assert parent._reserved_in==0 and parent._reserved_cost==0


@pytest.mark.parametrize('ending', ['cancel', 'failure'])
async def test_adapter_releases_reservations_on_unsuccessful_requests(tmp_path, ending):
    cfg = isolated_settings(tmp_path)
    started = asyncio.Event()
    class Transport:
        async def complete(self, req):
            assert req['max_tokens'] == 25
            started.set()
            if ending == 'failure':
                raise asyncio.TimeoutError
            await asyncio.Event().wait()
    log = __import__('pyharness.core.session', fromlist=['SessionLog']).SessionLog('budget-cancel')
    await log.append('session.created', {'title': 'synthetic', 'model': cfg.llm.model}, actor='system')
    account = UsageCounters().new_task(limits(25))
    ctx = SimpleNamespace(config=cfg, session=log, counters=account)
    adapter = OpenAICompatAdapter(cfg.llm.model, cfg=cfg, transport=Transport())
    task = asyncio.create_task(adapter.chat([{'role': 'user', 'content': 'synthetic'}], ctx=ctx))
    await started.wait()
    if ending == 'cancel':
        task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], BaseException)
    assert account._reserved_out == 0
    assert account.task_total().out_tokens == 0


@pytest.mark.parametrize('method', ['chat', 'chat_stream'])
async def test_real_adapter_fallback_and_ancestor_settlement(tmp_path, method):
    from pyharness.core.llm import ProviderStatusError
    from pyharness.core.llm_fallback import FallbackChain
    from tests.unit.test_llm import _chunk, _raw, _usage
    cfg = isolated_settings(tmp_path)
    cfg.llm.retry.attempts = 1
    cfg.llm.degrade.enabled = True
    cfg.llm.fallback_models = ['qwen-max']
    session = UsageCounters()
    parent = session.new_task(limits(30))
    child = parent.new_task(limits(30), parent=parent)
    log = __import__('pyharness.core.session', fromlist=['SessionLog']).SessionLog('fallback-budget')
    await log.append('session.created',dict(title='synthetic',model=cfg.llm.model),actor='system')
    seen = []
    class Transport:
        def __init__(self, fail): self.fail = fail
        async def complete(self, req):
            seen.append((req['model'],req['max_tokens']))
            if self.fail: raise ProviderStatusError(401)
            return _raw('synthetic',usage=_usage(10,20))
        async def stream(self, req):
            seen.append((req['model'],req['max_tokens']))
            if self.fail: raise ProviderStatusError(401)
            yield _chunk('synthetic')
            yield _chunk(finish='stop')
            yield _chunk(usage=_usage(10,20))
    models = [cfg.llm.model,'qwen-max']
    adapters = {name:OpenAICompatAdapter(name,cfg=cfg,transport=Transport(i==0)) for i,name in enumerate(models)}
    chain = FallbackChain(adapters=adapters,config=cfg,chain=models)
    ctx = SimpleNamespace(config=cfg,session=log,counters=child)
    assert (await chain.chat_with_fallback([dict(role='user',content='synthetic')],ctx=ctx,method=method)).content == 'synthetic'
    assert seen == [(models[0],30),(models[1],30)]
    assert session.out_tokens == parent.task_total().out_tokens == child.out_tokens == 20
    assert session.requests == child.requests == 1
    assert all(a._reserved_out==a._reserved_in==0 and a._reserved_cost==0 for a in (parent,child))


@pytest.mark.parametrize('ending', ['failure','cancellation'])
async def test_interrupted_stream_charges_observed_output_once_and_releases(tmp_path, ending):
    from tests.unit.test_llm import _chunk
    from pyharness.core.llm import ProviderStatusError
    cfg = isolated_settings(tmp_path)
    started = asyncio.Event()
    class Transport:
        async def stream(self,req):
            yield _chunk('synthetic partial output')
            started.set()
            if ending=='failure': raise ProviderStatusError(503)
            await asyncio.Event().wait()
    session=UsageCounters(); parent=session.new_task(limits())
    child=parent.new_task(limits(25),parent=parent)
    log=__import__('pyharness.core.session',fromlist=['SessionLog']).SessionLog('partial-budget')
    await log.append('session.created',dict(title='synthetic',model=cfg.llm.model),actor='system')
    adapter=OpenAICompatAdapter(cfg.llm.model,cfg=cfg,transport=Transport())
    task=asyncio.create_task(adapter.chat_stream([dict(role='user',content='synthetic')],ctx=SimpleNamespace(config=cfg,session=log,counters=child)))
    await started.wait()
    if ending=='cancellation': task.cancel()
    assert isinstance((await asyncio.gather(task,return_exceptions=True))[0],BaseException)
    assert 0 < child.out_tokens == parent.task_total().out_tokens == session.out_tokens
    assert child.requests == session.requests == 1
    assert all(a._reserved_out==a._reserved_in==0 for a in (child,parent))
