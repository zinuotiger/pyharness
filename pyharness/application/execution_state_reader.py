"""Strict companion graph and current private-blob integrity; no restore authority."""
from pyharness.application.state_snapshot_contracts import ExecutionStateSnapshot,MAX_STATE_BYTES
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.application.execution_state_capture import state_regular,StateUnavailable
from pyharness.core.sandbox import safe_path
from pyharness.errors import raise_code


def validate_state_checkpoints(events,execution_id,authorization_digest,authorization_seq,task):
    from pyharness.application.execution_delegation import own_lifecycle
    seen=set()
    for index,event in enumerate(events):
        if event.type!='execution.state_checkpoint':continue
        p=event.payload;prefix=events[:index];life=own_lifecycle(prefix,event.session_id)
        cp=next((e for e in prefix if e.seq==p['checkpoint_seq']),None)
        if (cp is None or cp.type!='execution.checkpoint' or cp.payload['checkpoint_id']!=p['checkpoint_id'] or
                digest_of(canonical(cp.model_dump(mode='json')))!=p['checkpoint_digest'] or cp.session_id!=event.session_id or cp.task_id!=task or
                p['checkpoint_id'] in seen or not life or life[-1].payload['phase']!='running' or event.actor!='system' or event.task_id!=task or
                p['execution_id']!=execution_id or p['authorization_digest']!=authorization_digest or p['authorization_seq']!=authorization_seq):
            raise_code('PERS-202',reason='execution_state_checkpoint_binding_invalid')
        seen.add(p['checkpoint_id'])


def read_state_snapshot(platform,event,checkpoint):
    p=event.payload
    if p['status']!='captured':raise_code('PERS-202',reason='execution_state_unavailable')
    path=safe_path(platform.root/'execution-state',p['state_digest'])
    try:raw,_=state_regular(path,MAX_STATE_BYTES)
    except (OSError,StateUnavailable):raise_code('PERS-202',reason='execution_state_blob_unavailable')
    if checkpoint is None:raise_code('PERS-202',reason='execution_state_checkpoint_missing')
    if len(raw)!=p['state_size'] or digest_of(raw)!=p['state_digest']:raise_code('PERS-202',reason='execution_state_blob_integrity')
    try:body=ExecutionStateSnapshot.model_validate_json(raw)
    except ValueError:raise_code('PERS-202',reason='execution_state_blob_schema')
    if (any(getattr(body,k)!=p[k] for k in ('execution_id','authorization_digest','authorization_seq','checkpoint_id','checkpoint_seq','checkpoint_digest','capture_scope','replay_authorized'))
            or body.session_id!=event.session_id or body.task_id!=event.task_id or len(body.files)!=p['file_count'] or body.kv_present!=p['kv_present']
            or body.messages_digest!=checkpoint.payload['materialized_messages_digest']):
        raise_code('PERS-202',reason='execution_state_blob_binding')
    return body


def state_checkpoint_projection(platform,events):
    facts=[e for e in events if e.type=='execution.state_checkpoint'];rows=[]
    latest=next((e for e in reversed(facts) if e.payload['status']=='captured'),None)
    for event in facts[-100:]:
        row=dict(event.payload,source_seq=event.seq,created_at=event.ts,session_id=event.session_id,task_id=event.task_id,
            current_integrity='not_checked' if event.payload['status']=='captured' else 'not_applicable')
        if event is latest:
            try:
                cp=next(e for e in events if e.seq==event.payload['checkpoint_seq']);read_state_snapshot(platform,event,cp)
                row['current_integrity']='verified'
            except Exception:row['current_integrity']='unconfirmed'
        rows.append(row)
    return dict(state_checkpoints=rows,state_checkpoints_total=len(facts),state_checkpoints_truncated=len(facts)>100)
