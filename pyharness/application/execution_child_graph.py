"""Historical delegation references, with sequence numbers scoped to their journal."""
import math
from pyharness.application.execution_delegation import own_lifecycle,spec_value
from pyharness.application.execution_effects import invocation_decision
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.invocation_binding import InvocationBinding
from pyharness.errors import PyHError,raise_code


def event_digest(event):return digest_of(canonical(event.model_dump(mode='json')))


def invocation_proof(p,events,parent_depth):
    byseq={e.seq:e for e in events};call=byseq[p['tool_call_seq']];start=byseq[p['effect_started_seq']]
    if call.session_id!=p['invoker_session_id'] or call.type!='tool.call':raise ValueError('invoker journal')
    binding=InvocationBinding(p['call_id'],p['tool_call_seq'],'subagent.spawn',p['inputs_digest'])
    decision=invocation_decision([e for e in events if e.seq<start.seq],binding,p['invoker_task_id'])
    life=own_lifecycle([e for e in events if e.seq<start.seq],call.session_id)
    expected=dict(execution_id=p['execution_id'],phase='started',tool='subagent.spawn',call_id=p['call_id'],
        tool_call_seq=call.seq,inputs_digest=p['inputs_digest'],decision_seq=decision.seq)
    if (not life or life[-1].payload['phase']!='running' or start.type!='execution.effect' or start.actor!='system' or
            start.task_id!=p['invoker_task_id'] or any(start.payload.get(k)!=v for k,v in expected.items()) or
            decision.seq!=p['decision_seq']):raise ValueError('delegation effect prefix')
    input_event=byseq[p['parent_input_seq']]
    if input_event.type!='user.message' or input_event.task_id!=p['invoker_task_id'] or input_event.seq>=call.seq:
        raise ValueError('delegation input anchor')
    spec=spec_value(call.payload['args'],parent_depth)
    if digest_of(canonical(spec))!=p['spec_digest'] or digest_of(spec['task'].encode())!=p['task_digest']:
        raise ValueError('delegation intent changed')
    if spec['depth']!=p['depth'] or spec['budget_ratio']!=p['budget_ratio']:raise ValueError('delegation spec')


def child_admissions(events,row):
    value=row['execution_authorization'];result={};calls=set();root=value['session_id']
    try:
        for index,event in enumerate(events):
            if event.type!='execution.child':continue
            p=event.payload;prior=events[:index];phases=[e.payload['phase'] for e in prior if e.type=='execution.lifecycle']
            if (p['phase']!='authorized' or phases!=['bound','running'] or event.actor!='system' or event.task_id!=value['task_id'] or
                    p['root_session_id']!=root or p['execution_id']!=value['execution_id'] or
                    p['authorization_digest']!=row['digest'] or p['authorization_seq']!=row['source_seq'] or
                    p['subject_ref']!=value['created_by']):raise ValueError('root admission identity')
            if p['child_session_id'] in result or (p['invoker_session_id'],p['tool_call_seq']) in calls:raise ValueError('duplicate delegation')
            parent=next((e for e in result.values() if e.seq==p['parent_admission_seq']),None)
            if p['invoker_session_id']==root:
                if parent is not None or p['depth']!=1 or p['invoker_task_id']!=value['task_id']:raise ValueError('root caller')
                invocation_proof(p,prior,0)
                if p['effect_started_seq']>=p['spawn_seq']:raise ValueError('root spawn order')
            elif (parent is None or parent.payload['child_session_id']!=p['invoker_session_id'] or
                    p['depth']!=parent.payload['depth']+1 or p['invoker_task_id']!='subagent:'+p['invoker_session_id']):
                raise ValueError('nested parent')
            spawn=next(e for e in prior if e.seq==p['spawn_seq'])
            if (spawn.type!='subagent.spawned' or spawn.actor!='agent' or spawn.payload['sub_id']!=p['child_session_id'] or
                    spawn.payload['parent_seq']!=p['parent_input_seq'] or digest_of(spawn.payload['task'].encode())!=p['task_digest']):
                raise ValueError('actual spawn')
            a=p['parent_limits'];b=p['child_limits'];ratio=p['budget_ratio']
            if parent is not None:
                if a!=parent.payload['child_limits']:raise ValueError('nested parent budget changed')
            else:
                budget=value['environment']['agent_snapshot']['budget']
                if (a['max_in_tokens']!=budget['input_tokens'] or a['max_out_tokens']!=budget['output_tokens'] or
                        a['max_cost_yuan']!=budget['cost']):raise ValueError('published parent budget changed')
            if (b['max_in_tokens']!=max(1,int(a['max_in_tokens']*ratio)) or
                    b['max_out_tokens']!=max(1,int(a['max_out_tokens']*ratio)) or
                    not math.isclose(b['max_cost_yuan'],a['max_cost_yuan']*ratio,rel_tol=1e-12) or b['warn_ratio']!=a['warn_ratio']):
                raise ValueError('child budget')
            material=next(e.payload['meta']['product_execution']['materials_digest'] for e in prior
                if e.type=='user.message' and e.payload.get('meta',{}).get('product_execution'))
            if p['materials_digest']!=material:raise ValueError('delegation materials')
            result[p['child_session_id']]=event;calls.add((p['invoker_session_id'],p['tool_call_seq']))
    except (ValueError,KeyError,StopIteration,PyHError):raise_code('PERS-202',reason='product_delegation_graph_invalid')
    return result


def validate_child_events(events,admission):
    try:
        if len(events)<2:raise ValueError('missing child binding')
        p=admission.payload;sid=p['child_session_id'];task='subagent:'+sid
        expected=dict(p,phase='bound',admission_seq=admission.seq,admission_digest=event_digest(admission))
        if (events[0].type!='session.created' or events[1].type!='execution.child' or events[1].payload!=expected or
                events[1].actor!='system' or events[1].task_id!=task):raise ValueError('child binding')
        phase='bound';starts={};ends=set()
        for i,event in enumerate(events[2:],2):
            if event.type=='execution.child':
                target=event.payload['phase']
                if (event.actor!='system' or event.task_id!=task or
                        any(event.payload[k]!=v for k,v in expected.items() if k not in ('phase','reason'))):raise ValueError('child lifecycle identity')
                if target=='running' and phase=='bound':
                    inputs=[e for e in events[:i] if e.type=='user.message' and e.task_id==task]
                    segments=[e for e in events[:i] if e.type=='segment.start' and e.payload['task_id']==task]
                    if len(inputs)!=1 or len(segments)!=1 or digest_of(inputs[0].payload['content'].encode())!=p['task_digest']:
                        raise ValueError('child never entered its real turn')
                elif target in ('failed','cancelled') and phase in ('bound','running'):pass
                elif target=='completed' and phase=='running':pass
                else:raise ValueError('child lifecycle transition')
                phase=target
            elif event.type=='execution.effect':
                q=event.payload
                if phase!='running' or event.actor!='system' or event.task_id!=task or q['execution_id']!=p['execution_id']:
                    raise ValueError('child effect identity')
                if q['phase']=='started':
                    if q['tool_call_seq'] in {v['tool_call_seq'] for v in starts.values()}:raise ValueError('duplicate child effect')
                    decision=invocation_decision(events[:i],InvocationBinding(q['call_id'],q['tool_call_seq'],q['tool'],q['inputs_digest']),task)
                    if decision.seq!=q['decision_seq']:raise ValueError('child effect decision')
                    starts[event.seq]=q
                else:
                    from pyharness.application.execution_file_effects import validate_file_observation
                    validate_file_observation(q,events[:i])
                    before=starts.get(q['started_seq'])
                    if before is None or q['started_seq'] in ends or any(before[k]!=q[k] for k in ('tool','call_id','tool_call_seq','inputs_digest','decision_seq')):
                        raise ValueError('child effect outcome')
                    ends.add(q['started_seq'])
            elif event.type in ('execution.lifecycle','execution.correction_applied') or event.type.startswith('task.'):raise ValueError('child is not a root queue')
        from pyharness.application.execution_checkpoints import validate_checkpoints
        validate_checkpoints(events,p['execution_id'],p['authorization_digest'],p['authorization_seq'],task)
        from pyharness.application.execution_state_reader import validate_state_checkpoints
        validate_state_checkpoints(events,p['execution_id'],p['authorization_digest'],p['authorization_seq'],task)
        from pyharness.application.execution_context_observation import validate_context_observations
        validate_context_observations(events,p['execution_id'],p['authorization_digest'],p['authorization_seq'],task)
    except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='product_child_graph_invalid')
