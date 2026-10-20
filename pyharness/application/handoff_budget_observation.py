"""Completed-source allowances use actual confirmed usage, never crash estimates."""
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR,localcontext
from datetime import datetime
import math
from pyharness.application.handoff_budget_contracts import HandoffAllowance
from pyharness.application.product_execution_sessions import read_execution_events
from pyharness.errors import raise_code


def consumer_allowance(snapshot):
    budget=snapshot['budget']
    return HandoffAllowance(input_tokens=budget['input_tokens'],output_tokens=budget['output_tokens'],
        tool_calls=budget['tool_calls'],cost_microyuan=int((Decimal(str(budget['cost']))*1000000).to_integral_value(rounding=ROUND_CEILING)),
        rounds=snapshot['max_rounds'],timeout_seconds=snapshot['timeout'])


def parent_allowance(service,row):
    from pyharness.application.workspace_store import canonical,digest_of
    from pyharness.application.execution_state_reader import read_state_snapshot
    from pyharness.application.execution_delegation import own_lifecycle
    from pyharness.application.execution_child_graph import child_admissions,validate_child_events,invocation_proof
    from pyharness.application.execution_child_reader import strict_child_events
    value=row['execution_authorization'];directory=service._tenant_sessions_dir()
    events=read_execution_events(directory,row,service.tenant_id)
    life=own_lifecycle(events,value['session_id'])
    if not life or life[-1].payload['phase']!='completed':raise_code('GRD-403',reason='handoff_parent_not_completed')
    facts=[e for e in events if e.type=='execution.state_checkpoint' and e.payload['status']=='captured']
    if not facts:raise_code('GRD-403',reason='handoff_budget_observation_unknown')
    fact=facts[-1];checkpoint=next((e for e in events if e.seq==fact.payload['checkpoint_seq']),None)
    body=read_state_snapshot(service.platform,fact,checkpoint);contract=body.continuation_contract
    if not body.budget.counter_injected or body.budget.usage.cost_est is None or contract is None or not contract.pricing_known:
        raise_code('GRD-403',reason='handoff_budget_observation_unknown')
    admissions=child_admissions(events,row)
    if len(admissions)>400:raise_code('GRD-403',reason='handoff_budget_observation_limit')
    logs={value['session_id']:events}
    def child(sid,seen):
        if sid in logs:return logs[sid]
        if sid in seen or len(seen)>3:raise_code('PERS-202',reason='handoff_child_budget_cycle')
        admission=admissions[sid];rows=strict_child_events(directory,sid,service.tenant_id);validate_child_events(rows,admission)
        parent=child(admission.payload['invoker_session_id'],seen|{sid})
        try:invocation_proof(admission.payload,parent,admission.payload['depth']-1)
        except (ValueError,KeyError):raise_code('PERS-202',reason='handoff_child_budget_binding')
        if own_lifecycle(rows,sid)[-1].payload['phase']!='completed':raise_code('GRD-403',reason='handoff_child_budget_unknown')
        logs[sid]=rows;return rows
    for sid in admissions:child(sid,set())
    usages=[e for rows in logs.values() for e in rows if e.type=='llm.usage']
    if (not usages or any(e.type=='llm.error' for rows in logs.values() for e in rows) or
            any(e.payload.get('cost_est') is None or e.payload.get('input_unknown') is True for e in usages)):
        raise_code('GRD-403',reason='handoff_budget_observation_unknown')
    with localcontext() as arithmetic:
        arithmetic.prec=768
        before=[e for e in events if e.type=='llm.usage' and e.seq<=fact.seq]
        after=[e for e in events if e.type=='llm.usage' and e.seq>fact.seq]
        others=[e for sid,rows in logs.items() if sid!=value['session_id'] for e in rows if e.type=='llm.usage']
        def amount(rows,key):return sum(int(e.payload.get(key,0)) for e in rows)
        def cost(rows):return sum((Decimal(str(e.payload['cost_est'])) for e in rows),Decimal(0))
        observed_cost=max(Decimal(str(body.budget.usage.cost_est)),cost(before))+cost(after)+cost(others)
        observed_in=max(body.budget.usage.in_tokens,amount(before,'in_tokens'))+amount(after,'in_tokens')+amount(others,'in_tokens')
        observed_out=max(body.budget.usage.out_tokens,amount(before,'out_tokens'))+amount(after,'out_tokens')+amount(others,'out_tokens')
        limit_cost=Decimal(str(body.budget.limits.max_cost_yuan))
        capacity_cost=int((max(Decimal(0),limit_cost-observed_cost)*1000000).to_integral_value(rounding=ROUND_FLOOR))
        observed_money=int((observed_cost*1000000).to_integral_value(rounding=ROUND_CEILING))
        # Floor limits too: an integer reservation never borrows a fractional unit.
        money_limit=int((limit_cost*1000000).to_integral_value(rounding=ROUND_FLOOR))
    definition=value['environment']['agent_snapshot'];limits=consumer_allowance(definition).model_dump()
    limits.update(input_tokens=body.budget.limits.max_in_tokens,output_tokens=body.budget.limits.max_out_tokens,cost_microyuan=money_limit)
    rounds=max(contract.completed_rounds,sum(e.type=='llm.response' and e.seq<=fact.seq for e in events))+sum(e.type=='llm.response' and e.seq>fact.seq for e in events)+sum(e.type=='llm.response' for sid,rows in logs.items() if sid!=value['session_id'] for e in rows)
    tools=max(contract.tool_calls_used,sum(e.type=='tool.call' and e.seq<=fact.seq for e in events))+sum(e.type=='tool.call' and e.seq>fact.seq for e in events)+sum(e.type=='tool.call' for sid,rows in logs.items() if sid!=value['session_id'] for e in rows)
    limits['timeout_seconds']=math.floor(contract.remaining_timeout_seconds)
    elapsed=0.0
    for sid,rows in logs.items():
        phases=own_lifecycle(rows,sid);running=next((e for e in phases if e.payload['phase']=='running'),None)
        if running is None:raise_code('GRD-403',reason='handoff_budget_time_unknown')
        if sid==value['session_id']:running=fact
        elapsed+=max(0.0,(datetime.fromisoformat(phases[-1].ts)-datetime.fromisoformat(running.ts)).total_seconds())
    spent=dict(input_tokens=observed_in,output_tokens=observed_out,tool_calls=tools,cost_microyuan=observed_money,rounds=rounds,timeout_seconds=math.ceil(elapsed))
    capacity={key:max(0,limits[key]-spent[key]) for key in limits}
    capacity['cost_microyuan']=min(capacity['cost_microyuan'],capacity_cost)
    witness=digest_of(canonical(dict(logs={sid:[e.model_dump(mode='json') for e in rows] for sid,rows in logs.items()},state_digest=fact.payload['state_digest'])))
    return HandoffAllowance(**limits),HandoffAllowance(**spent),HandoffAllowance(**capacity),witness
