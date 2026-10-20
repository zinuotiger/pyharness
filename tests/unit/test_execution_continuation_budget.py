"""Unknown in-flight requests consume allowance instead of resetting it."""
from types import SimpleNamespace
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_execution_liveness import held
from tests.unit.test_execution_state_snapshots import state_blob

LARGE=dict(script=[],agent=dict(budget=dict(input_tokens=100000,output_tokens=20000,tool_calls=20,cost=2)))

@pytest.mark.parametrize('running_product',[LARGE],indirect=True)
async def test_each_possible_unconfirmed_request_is_reserved_in_inherited_limits(running_product):
    from pyharness.application.execution_continuation_budget import continuation_budget
    from pyharness.application.state_snapshot_contracts import ExecutionStateSnapshot
    env=running_product;_,release=await held(env)
    try:
        ctx=env.controls.ctx;events=list(ctx.session.events_after(0));fact=next(e for e in events if e.type=='execution.state_checkpoint')
        body=ExecutionStateSnapshot.model_validate(state_blob(env,fact));prefix=[e for e in events if e.seq<=fact.seq]
        first,blockers=continuation_budget(body,prefix,fact.seq,ctx.platform_runtime.definition)
        assert not blockers and first.potential_unconfirmed_attempts==1
        assert first.remaining_limits.max_out_tokens==20000-body.continuation_contract.output_upper_bound
        assert first.remaining_limits.max_in_tokens==100000-body.continuation_contract.input_upper_bound
        assert first.reserved_unconfirmed.cost_est>0 and first.remaining_limits.max_cost_yuan<2
        request=SimpleNamespace(type='llm.request',seq=fact.seq+1,payload=dict(model=ctx.config.llm.model))
        second,blockers=continuation_budget(body,[*prefix,request],fact.seq,ctx.platform_runtime.definition)
        assert not blockers and second.potential_unconfirmed_attempts==2
        assert second.reserved_unconfirmed.out_tokens==2*first.reserved_unconfirmed.out_tokens
        assert second.remaining_limits.max_out_tokens<first.remaining_limits.max_out_tokens
    finally:release.set()


async def test_inflight_request_can_exhaust_remaining_allowance_without_free_restart(running_product):
    from pyharness.application.execution_continuation_budget import continuation_budget
    from pyharness.application.state_snapshot_contracts import ExecutionStateSnapshot
    env=running_product;_,release=await held(env)
    try:
        ctx=env.controls.ctx;events=list(ctx.session.events_after(0));fact=next(e for e in events if e.type=='execution.state_checkpoint')
        body=ExecutionStateSnapshot.model_validate(state_blob(env,fact))
        budget,blockers=continuation_budget(body,[e for e in events if e.seq<=fact.seq],fact.seq,ctx.platform_runtime.definition)
        assert budget.remaining_limits.max_out_tokens==0 and 'budget_exhausted' in blockers
    finally:release.set()


@pytest.mark.parametrize('running_product',[LARGE],indirect=True)
async def test_unknown_recorded_cost_never_becomes_a_known_zero(running_product):
    from pyharness.application.execution_continuation_budget import continuation_budget
    from pyharness.application.state_snapshot_contracts import ExecutionStateSnapshot
    env=running_product;_,release=await held(env)
    try:
        ctx=env.controls.ctx;fact=next(e for e in ctx.session.events_after(0) if e.type=='execution.state_checkpoint')
        body=ExecutionStateSnapshot.model_validate(state_blob(env,fact))
        unknown=SimpleNamespace(type='llm.usage',seq=fact.seq-1,payload=dict(cost_est=None,in_tokens=1,out_tokens=1,input_unknown=False))
        budget,blockers=continuation_budget(body,[unknown],fact.seq,ctx.platform_runtime.definition)
        assert budget is None and 'budget_observation_unconfirmed' in blockers
    finally:release.set()


def numeric_state(rate=0, *, limit=2):
    from pyharness.application.state_snapshot_contracts import StateBudgetLimits,StateUsage
    return SimpleNamespace(continuation_contract=SimpleNamespace(pricing_known=True,input_rate=rate,output_rate=rate,
        input_upper_bound=1024,output_upper_bound=1024,completed_rounds=0,tool_calls_used=0,remaining_timeout_seconds=20),
        budget=SimpleNamespace(counter_injected=True,usage=StateUsage(in_tokens=0,out_tokens=0,cost_est=0),
            limits=StateBudgetLimits(max_in_tokens=10000,max_out_tokens=10000,max_cost_yuan=limit,warn_ratio=.8)))


@pytest.mark.parametrize('rate',[1e20,1e300])
def test_large_finite_price_yields_exhausted_budget_instead_of_decimal_error(rate):
    from pyharness.application.execution_continuation_budget import continuation_budget
    budget,blockers=continuation_budget(numeric_state(rate),[],1,SimpleNamespace(max_rounds=8))
    assert budget is not None and budget.remaining_limits.max_cost_yuan==0 and 'budget_exhausted' in blockers


def test_unrepresentable_reserved_price_blocks_instead_of_infinity_or_zero():
    from pyharness.application.execution_continuation_budget import continuation_budget
    budget,blockers=continuation_budget(numeric_state(1e308),[],1,SimpleNamespace(max_rounds=8))
    assert budget is None and 'budget_observation_unconfirmed' in blockers


def test_known_tiny_journal_cost_is_not_refunded_by_rounded_scope_observation():
    from pyharness.application.execution_continuation_budget import continuation_budget
    known=SimpleNamespace(type='llm.usage',seq=1,payload=dict(cost_est=1e-8,input_unknown=False))
    budget,blockers=continuation_budget(numeric_state(limit=1),[known],2,SimpleNamespace(max_rounds=8))
    assert not blockers and budget.observed_usage.cost_est>=1e-8
    assert budget.remaining_limits.max_cost_yuan==.999999
