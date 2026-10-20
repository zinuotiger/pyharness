"""Exact confirmed journal anchors at actual logical model-round boundaries."""
import secrets
from pyharness.application.execution_effects import flush_confirmed,confirmed_events
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.errors import raise_code


def effect_observation(events):
    starts={e.seq for e in events if e.type=='execution.effect' and e.payload['phase']=='started'}
    ends={e.payload['started_seq']:e.payload for e in events if e.type=='execution.effect' and e.payload['phase']!='started'}
    pending=len(starts-set(ends));unknown=sum(p['phase']=='unknown' or p.get('file_observation')=='unavailable' for p in ends.values())
    return dict(effect_count=len(starts),pending_effect_count=pending,unknown_effect_count=unknown,
        effect_replay_blocked=bool(pending+unknown))


def prefix_digest(events):
    return digest_of(canonical([e.model_dump(mode='json') for e in events]))


async def append_model_checkpoint(boundary,ctx,messages):
    boundary._context(ctx);boundary.checked()
    from pyharness.application.execution_state_capture import capture_state,append_state_checkpoint
    captured=await capture_state(boundary,ctx,messages)
    events=await flush_confirmed(ctx.session);boundary.checked()
    receipt=boundary.receipt
    payload=dict(schema_version=1,checkpoint_id='ck-'+secrets.token_hex(8),execution_id=receipt['execution_id'],
        authorization_digest=receipt['digest'],authorization_seq=receipt['source_seq'],boundary='before_model',
        prefix_seq=events[-1].seq,prefix_digest=prefix_digest(events),
        materialized_messages_digest=digest_of(canonical(messages)),observation_scope='session_effects',
        replay_authorized=False,**effect_observation(events))
    event=await ctx.session.append('execution.checkpoint',payload,actor='system',task_id=str(ctx.task_id),sync=True)
    actual=confirmed_events(ctx.session)
    if actual[-1]!=event:raise_code('PERS-202',reason='execution_checkpoint_unconfirmed')
    await append_state_checkpoint(boundary,ctx,event,captured)
    boundary.checked()
    return event


def validate_checkpoints(events,execution_id,authorization_digest,authorization_seq,task):
    from pyharness.application.execution_delegation import own_lifecycle
    seen=set()
    for i,event in enumerate(events):
        if event.type!='execution.checkpoint':continue
        p=event.payload;prefix=events[:i];life=own_lifecycle(prefix,event.session_id)
        if (not prefix or not life or life[-1].payload['phase']!='running' or event.actor!='system' or event.task_id!=task or
                p['checkpoint_id'] in seen or p['execution_id']!=execution_id or
                p['authorization_digest']!=authorization_digest or p['authorization_seq']!=authorization_seq or
                p['prefix_seq']!=prefix[-1].seq or p['prefix_digest']!=prefix_digest(prefix) or
                any(p[k]!=v for k,v in effect_observation(prefix).items())):
            raise_code('PERS-202',reason='execution_checkpoint_binding_invalid')
        seen.add(p['checkpoint_id'])


def checkpoint_projection(events):
    facts=[dict(e.payload,source_seq=e.seq,created_at=e.ts,session_id=e.session_id,task_id=e.task_id)
        for e in events if e.type=='execution.checkpoint']
    return dict(checkpoints=facts[-100:],checkpoints_total=len(facts),checkpoints_truncated=len(facts)>100,
        current_session_effect_observation=effect_observation(events))
