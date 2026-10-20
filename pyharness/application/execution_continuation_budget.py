"""Conservative inherited allowances, including one potentially unflushed request."""
from decimal import Decimal,DecimalException,ROUND_CEILING,ROUND_FLOOR,localcontext
import math
from pyharness.application.continuation_contracts import ContinuationBudget
from pyharness.application.state_snapshot_contracts import StateBudgetLimits,StateUsage


def public_cost(value,*,up):
    result=float(value)
    if not math.isfinite(result):raise ValueError('unrepresentable continuation cost')
    observed=Decimal(str(result))
    if (up and observed<value) or (not up and observed>value):
        result=math.nextafter(result,math.inf if up else 0)
    if not math.isfinite(result):raise ValueError('unrepresentable continuation cost')
    return result


def continuation_budget(body,events,state_seq,definition):
    contract=body.continuation_contract;usage=body.budget.usage;limits=body.budget.limits
    if contract is None:return None,['continuation_contract_missing']
    if (not body.budget.counter_injected or usage.cost_est is None or not contract.pricing_known or any(
            e.type=='llm.usage' and e.seq<=state_seq and (e.payload.get('cost_est') is None or e.payload.get('input_unknown') is True)
            for e in events)):
        return None,['budget_observation_unconfirmed']
    # Request/error facts may be flushed, but the last ordinary llm.request
    # can still have been in the persistence buffer at a real process kill.
    potential=1+sum(e.type=='llm.request' and e.seq>state_seq for e in events)
    reserved_in=potential*contract.input_upper_bound;reserved_out=potential*contract.output_upper_bound
    try:
        # All captured numbers are finite binary64, and token/message bounds are
        # explicit. Retain decimal precision even for extreme configured prices.
        with localcontext() as arithmetic:
            arithmetic.prec=768
            journal_cost=sum((Decimal(str(e.payload['cost_est'])) for e in events
                if e.type=='llm.usage' and e.seq<=state_seq),Decimal(0))
            observed=max(Decimal(str(usage.cost_est)),journal_cost)
            cost=(Decimal(reserved_in)*Decimal(str(contract.input_rate))+Decimal(reserved_out)*Decimal(str(contract.output_rate))).quantize(Decimal('.000001'),rounding=ROUND_CEILING)
            cost_left=max(Decimal(0),Decimal(str(limits.max_cost_yuan))-observed-cost).quantize(Decimal('.000001'),rounding=ROUND_FLOOR)
            reserved_cost=public_cost(cost,up=True);remaining_cost=public_cost(cost_left,up=False)
            usage=usage.model_copy(update={'cost_est':public_cost(observed,up=True)})
    except (DecimalException,OverflowError,ValueError):
        return None,['budget_observation_unconfirmed']
    remaining=StateBudgetLimits(max_in_tokens=max(0,limits.max_in_tokens-usage.in_tokens-reserved_in),
        max_out_tokens=max(0,limits.max_out_tokens-usage.out_tokens-reserved_out),max_cost_yuan=remaining_cost,warn_ratio=limits.warn_ratio)
    rounds=max(0,definition.max_rounds-contract.completed_rounds-1)
    result=ContinuationBudget(observed_usage=usage,reserved_unconfirmed=StateUsage(in_tokens=reserved_in,out_tokens=reserved_out,cost_est=reserved_cost),
        remaining_limits=remaining,potential_unconfirmed_attempts=potential,remaining_rounds=rounds,tool_calls_used=contract.tool_calls_used,
        remaining_timeout_seconds=contract.remaining_timeout_seconds)
    blockers=[]
    if not remaining.max_in_tokens or not remaining.max_out_tokens or not remaining.max_cost_yuan:blockers.append('budget_exhausted')
    if not rounds:blockers.append('round_limit_exhausted')
    return result,blockers
