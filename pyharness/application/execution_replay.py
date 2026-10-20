"""Historical execution graph validation; later facts cannot lend earlier authority."""
from pyharness.application.execution_effects import invocation_decision
from pyharness.core.invocation_binding import InvocationBinding
from pyharness.errors import PyHError, raise_code


def validate_execution_graph(events, row):
    value = row['execution_authorization']; root = events[1].payload
    phase = 'bound'; starts = {}; completed = set(); call_seqs = set()
    identity = ('execution_id','authorization_digest','authorization_seq','subject_ref','schema_version')
    effect_identity = ('execution_id','tool','call_id','tool_call_seq','inputs_digest','decision_seq')
    try:
        for index, event in enumerate(events[2:], 2):
            p = event.payload
            if event.type == 'execution.lifecycle':
                if (event.actor != 'system' or event.task_id != value['task_id'] or
                        any(p[k] != root[k] for k in identity)):raise ValueError('lifecycle identity')
                target = p['phase']
                if target == 'running' and phase == 'bound':
                    enqueued = [e for e in events[:index] if e.type == 'task.enqueued' and e.payload['task_id'] == value['task_id']]
                    started = [e for e in events[:index] if e.type == 'task.started' and e.payload['task_id'] == value['task_id']]
                    if len(enqueued) != 1 or len(started) != 1:raise ValueError('running without one real queued task')
                elif target in ('failed','cancelled','interrupted') and phase in ('bound','running'):pass
                elif target == 'completed' and phase == 'running':pass
                else:raise ValueError('lifecycle transition')
                phase = target
            elif event.type == 'execution.effect':
                if (phase != 'running' or event.actor != 'system' or event.task_id != value['task_id'] or
                        p['execution_id'] != value['execution_id']):raise ValueError('effect outside execution')
                if p['phase'] == 'started':
                    if p['tool_call_seq'] in call_seqs:raise ValueError('duplicate effect invocation')
                    binding = InvocationBinding(p['call_id'], p['tool_call_seq'], p['tool'], p['inputs_digest'])
                    decision = invocation_decision(events[:index], binding, value['task_id'])
                    if p['decision_seq'] != decision.seq:raise ValueError('effect decision mismatch')
                    starts[event.seq] = p; call_seqs.add(p['tool_call_seq'])
                else:
                    from pyharness.application.execution_file_effects import validate_file_observation
                    validate_file_observation(p,events[:index])
                    before = starts.get(p['started_seq'])
                    if (before is None or p['started_seq'] in completed or
                            any(p[k] != before[k] for k in effect_identity)):raise ValueError('effect outcome mismatch')
                    completed.add(p['started_seq'])
            elif event.type.startswith('task.'):
                if p.get('task_id') != value['task_id']:raise ValueError('foreign queued task')
    except (ValueError, KeyError, PyHError):
        raise_code('PERS-202', reason='product_execution_graph_invalid')
    from pyharness.application.execution_child_graph import child_admissions
    child_admissions(events,row)
    from pyharness.application.execution_checkpoints import validate_checkpoints
    validate_checkpoints(events,value['execution_id'],row['digest'],row['source_seq'],value['task_id'])
    from pyharness.application.execution_state_reader import validate_state_checkpoints
    validate_state_checkpoints(events,value['execution_id'],row['digest'],row['source_seq'],value['task_id'])
    from pyharness.application.execution_correction_runtime import validate_correction_applications
    validate_correction_applications(events,row)
    from pyharness.application.execution_context_observation import validate_context_observations
    validate_context_observations(events,value['execution_id'],row['digest'],row['source_seq'],value['task_id'])
