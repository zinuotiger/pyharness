"""S07 effects: real persisted intent precedes a provider, uncertainty is not replayed."""
import asyncio
from types import SimpleNamespace

import pytest

from pyharness.application.session_store import DesktopSessionManager
from pyharness.bus import EventBus
from pyharness.errors import PyHError


@pytest.fixture
async def effect_env(tmp_path, request):
    from pyharness.application.execution_effects import EffectRecorder
    from pyharness.core.invocation_binding import InvocationBinding, current_invocation
    manager = DesktopSessionManager(dir=tmp_path, bus=EventBus())
    sid = await manager.create()
    session = manager._logs[sid]
    ctx = SimpleNamespace(session=session, task_id='p-effect-test')
    execution = 'ex-' + '1' * 16
    await session.append('execution.lifecycle', dict(schema_version=1,
        execution_id=execution, phase='bound', authorization_digest='a' * 64,
        authorization_seq=3, subject_ref='local-operator', reason=None),
        actor='system', task_id=ctx.task_id, sync=True)
    if getattr(request, 'param', True):
        await session.append('execution.lifecycle', dict(schema_version=1,
            execution_id=execution, phase='running', authorization_digest='a' * 64,
            authorization_seq=3, subject_ref='local-operator', reason=None),
            actor='system', task_id=ctx.task_id, sync=True)
    call = await session.append('tool.call', dict(name='fs.write_file',
        args={'path': 'out.txt'}, raw_args={'path': 'out.txt'}, call_id='c-effect'),
        actor='tool', task_id=ctx.task_id, trace={'call_id': 'c-effect'})
    from pyharness.core.execution_result import _approval_binding
    digest = _approval_binding(SimpleNamespace(name='fs.write_file'), {'path': 'out.txt'})
    await session.append('decision.issued', dict(decision_id='d-effect', verdict='allow',
        tool='fs.write_file', inputs_digest=digest, principal_kind='user',
        principal_id='local-operator'), actor='system', task_id=ctx.task_id,
        trace={'call_id': 'c-effect'}, sync=True)
    binding = InvocationBinding(call_id='c-effect', tool_call_seq=call.seq,
        tool='fs.write_file', inputs_digest=digest)
    checks = []
    def authorize():
        checks.append(True)
    recorder = EffectRecorder(execution, 'a' * 64, authorize)
    token = current_invocation.set(binding)
    try:
        yield SimpleNamespace(ctx=ctx, recorder=recorder, checks=checks,
            session=session, store=manager._stores[sid], manager=manager,
            binding=binding, current=current_invocation)
    finally:
        current_invocation.reset(token)
        await manager.shutdown_all()


async def test_provider_observes_confirmed_intent_and_result_is_a_reference(effect_env):
    env = effect_env
    async def provider():
        disk = list(env.store.replay())
        assert disk[-1].type == 'execution.effect'
        assert disk[-1].payload['phase'] == 'started'
        return 'private result text'
    assert await env.recorder.invoke(env.ctx, provider) == 'private result text'
    effects = [e for e in env.store.replay() if e.type == 'execution.effect']
    assert [e.payload['phase'] for e in effects] == ['started', 'returned']
    assert effects[-1].payload['started_seq'] == effects[0].seq
    assert effects[-1].payload['result_digest']
    assert 'private result text' not in str([e.payload for e in effects])
    assert len(env.checks) >= 2


async def test_lost_persistence_ack_is_zero_provider_calls(effect_env, monkeypatch):
    env = effect_env
    original = env.store.append
    def drop(event, *args, **kwargs):
        if event.type != 'execution.effect':
            return original(event, *args, **kwargs)
    monkeypatch.setattr(env.store, 'append', drop)
    calls = []
    async def provider(): calls.append(True)
    with pytest.raises(PyHError):
        await env.recorder.invoke(env.ctx, provider)
    assert calls == []
    assert not any(e.type == 'execution.effect' for e in env.store.replay())


@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_failure_after_start_is_unknown_and_cannot_be_replayed(effect_env, failure):
    env = effect_env
    calls = []
    async def provider():
        calls.append(True)
        raise failure()
    with pytest.raises(failure):
        await env.recorder.invoke(env.ctx, provider)
    effects = [e for e in env.store.replay() if e.type == 'execution.effect']
    assert [e.payload['phase'] for e in effects] == ['started', 'unknown']
    with pytest.raises(PyHError):
        await env.recorder.invoke(env.ctx, provider)
    assert len(calls) == 1


async def test_missing_governed_binding_and_subject_denial_are_zero_calls(effect_env):
    env = effect_env
    calls = []
    async def provider(): calls.append(True)
    token = env.current.set(None)
    try:
        with pytest.raises(PyHError): await env.recorder.invoke(env.ctx, provider)
    finally:
        env.current.reset(token)
    def denied(): raise PyHError('GRD-403')
    env.recorder.authorize = denied
    with pytest.raises(PyHError): await env.recorder.invoke(env.ctx, provider)
    assert calls == []
    assert not any(e.type == 'execution.effect' for e in env.store.replay())


async def test_different_call_binding_cannot_borrow_previous_decision(effect_env):
    from dataclasses import replace
    env = effect_env
    calls = []
    async def provider(): calls.append(True)
    token = env.current.set(replace(env.binding, inputs_digest='0' * 40))
    try:
        with pytest.raises(PyHError): await env.recorder.invoke(env.ctx, provider)
    finally:
        env.current.reset(token)
    assert calls == []


async def test_corrupt_tail_preserved_without_repair_or_execution(effect_env):
    env = effect_env
    with env.store.path.open('ab') as stream: stream.write(b'{broken')
    before = env.store.path.read_bytes()
    calls = []
    async def provider(): calls.append(True)
    with pytest.raises(PyHError): await env.recorder.invoke(env.ctx, provider)
    assert calls == []
    assert env.store.path.read_bytes() == before


async def test_two_recorders_cannot_replay_one_call(effect_env):
    from pyharness.application.execution_effects import EffectRecorder
    env = effect_env
    other = EffectRecorder(env.recorder.execution_id, env.recorder.authorization_digest, lambda: None)
    calls = []
    async def provider():
        calls.append(True)
        await asyncio.sleep(0)
        return 'once'
    results = await asyncio.gather(env.recorder.invoke(env.ctx, provider),
        other.invoke(env.ctx, provider), return_exceptions=True)
    assert sum(isinstance(x, PyHError) for x in results) == 1
    assert calls == [True]


async def test_actual_executor_runtime_chain_records_governed_provider(effect_env, tmp_path):
    from tests.unit.test_tools_executor import mk_defn, make_registry, make_ctx, FakeScope
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.tools_guard import GuardChain, ToolCall
    from pyharness.application.platform_runtime import PlatformRuntime
    from pyharness.application.platform_models import AgentDefinition
    env = effect_env
    calls = []
    async def provider(args, ctx):
        calls.append(args['count'])
        assert list(env.store.replay())[-1].payload['phase'] == 'started'
        return args['count'] + 1
    definition = mk_defn(name='calc.add', schema={'type': 'object',
        'properties': {'count': {'type': 'integer'}}, 'required': ['count']})
    registry = make_registry(definition, providers={'calc.add': provider})
    ctx = make_ctx(env.session, FakeScope(str(tmp_path)), chain=GuardChain(session=env.session))
    ctx.task_id = env.ctx.task_id
    runtime = PlatformRuntime(SimpleNamespace(), AgentDefinition(name='effect test', allowed_tools=['calc.add']), env.session.sid)
    runtime.execution_effects = env.recorder
    ctx.platform_runtime = runtime
    token = env.current.set(None)
    try:
        result = await ToolExecutor(registry).execute(ToolCall(name='calc.add',
            raw_args={'count': 7}, call_id='actual-chain'), ctx)
        assert result.ok and result.summary == '8'
        assert env.current.get() is None
    finally:
        env.current.reset(token)
    effects = [e for e in env.store.replay() if e.type == 'execution.effect']
    assert calls == [7]
    assert [e.payload['call_id'] for e in effects] == ['actual-chain', 'actual-chain']
    assert effects[0].payload['decision_seq'] < effects[0].seq


async def test_durable_approval_required_for_an_approval_decision(effect_env):
    env = effect_env
    await env.session.append('decision.issued', dict(decision_id='d-unapproved', verdict='approval',
        tool='fs.write_file', inputs_digest=env.binding.inputs_digest, principal_kind='user',
        principal_id='local-operator'), actor='system', trace={'call_id': env.binding.call_id}, sync=True)
    calls = []
    async def provider(): calls.append(True)
    with pytest.raises(PyHError): await env.recorder.invoke(env.ctx, provider)
    assert calls == []


@pytest.mark.parametrize('changes', [dict(schema_version=True), dict(tool_call_seq=True),
    dict(phase='returned'), dict(result_digest='b' * 64), dict(actor='user')])
def test_effect_payload_rejects_incoherent_or_forged_shape(changes):
    from pydantic import ValidationError
    from pyharness.events.payload_product_execution import ExecutionEffectPayload
    value = dict(schema_version=1, execution_id='ex-' + '1' * 16,
        phase='started', tool='fs.write_file', call_id='c1', tool_call_seq=4,
        inputs_digest='a' * 40, decision_seq=5)
    with pytest.raises(ValidationError): ExecutionEffectPayload.model_validate(dict(value, **changes))


@pytest.mark.parametrize('effect_env', [False], indirect=True)
async def test_bound_authority_without_running_lifecycle_is_zero_provider(effect_env):
    calls = []
    async def provider():calls.append(True)
    with pytest.raises(PyHError):await effect_env.recorder.invoke(effect_env.ctx, provider)
    assert calls == []
