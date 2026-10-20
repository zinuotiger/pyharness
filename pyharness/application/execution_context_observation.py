"""Record a public prepared-request fact on the original root or child log."""
import secrets
from pydantic import ValidationError
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.application.execution_effects import flush_confirmed,confirmed_events
from pyharness.application.execution_delegation import own_lifecycle
from pyharness.core.context_derivation import messages_digest,request_context_metadata
from pyharness.core.llm_diagnostics import call_role,call_attempt
from pyharness.events.payload_context import PublicRequestView,ExecutionContextPayload
from pyharness.errors import raise_code

MAX_CONTEXT_OBSERVATIONS=1000


def source_content_state(boundary,ctx,events,messages):
    if ctx.session.sid!=boundary.receipt['session_id']:return 'delegated_context'
    inputs=[e.payload['content'] for e in events if e.type=='user.message' and e.task_id==str(ctx.task_id)]
    if len(inputs)!=1:return 'not_confirmed'
    present=any(m.get('role')=='user' and (m.get('content')==inputs[0] or
        type(m.get('content')) is list and any(type(p) is dict and p.get('type')=='text' and p.get('text')==inputs[0] for p in m['content'])) for m in messages)
    if not present:return 'not_confirmed'
    return 'injected_fixed_prompt' if boundary.materials['layout']=='inline' else 'available_as_workspace'


async def record_context(boundary,ctx,request,*,layer,request_seq,public_view):
    boundary._context(ctx);boundary.checked()
    if type(request) is not dict or type(request.get('messages')) is not list or type(request.get('model')) is not str:
        raise_code('EVT-100',reason='public_context_request_shape')
    events=await flush_confirmed(ctx.session);boundary.checked()
    phases=own_lifecycle(events,ctx.session.sid)
    if not phases or phases[-1].payload['phase']!='running':raise_code('GRD-403',reason='public_context_not_running')
    if sum(e.type=='execution.context' for e in events)>=MAX_CONTEXT_OBSERVATIONS:
        raise_code('EVT-104',reason='public_context_observation_limit')
    receipt=boundary.receipt;role=call_role.get();attempt=call_attempt.get()
    hashed=messages_digest(request['messages']);checkpoints=[e for e in events if e.type=='execution.checkpoint']
    checkpoint=checkpoints[-1] if checkpoints and checkpoints[-1].payload['materialized_messages_digest']==hashed else None
    ref=dict(checkpoint_id=checkpoint.payload['checkpoint_id'],source_seq=checkpoint.seq) if checkpoint is not None else None
    if role=='main_agent' and ref is None:raise_code('GRD-403',reason='public_context_checkpoint_required')
    if layer=='transport_payload':
        origin=next((e for e in events if e.seq==request_seq),None)
        if (origin is None or origin.type!='llm.request' or origin.actor!='llm' or origin.task_id!=str(ctx.task_id)
                or origin.payload['model']!=request['model'] or origin.payload['role']!=role or origin.payload['attempt']!=attempt):
            raise_code('PERS-202',reason='public_context_request_binding')
    metadata=request_context_metadata(ctx,request['messages']) or dict(history_window=dict(available=False),system_window=dict(available=False))
    try:
        view=PublicRequestView.model_validate(public_view)
        value=ExecutionContextPayload(schema_version=1,observation_id='co-'+secrets.token_hex(8),
            execution_id=receipt['execution_id'],authorization_digest=receipt['digest'],authorization_seq=receipt['source_seq'],
            prefix_seq=events[-1].seq,phase='prepared',layer=layer,model=view.model,model_digest=digest_of(canonical(request['model'])),
            method='chat_stream' if request.get('stream') is True else 'chat',role=role,attempt=attempt,request_seq=request_seq,
            checkpoint_ref=ref,messages_digest=hashed,public_view_digest=digest_of(canonical(view.model_dump())),public_view=view,
            derivation=dict(metadata,final_messages=len(request['messages'])),
            source_content_state=source_content_state(boundary,ctx,events,request['messages']),execution_authorized=False)
    except (ValidationError,ValueError):raise_code('EVT-100',reason='public_context_projection_invalid')
    event=await ctx.session.append('execution.context',value.model_dump(),actor='system',task_id=str(ctx.task_id),sync=True)
    if confirmed_events(ctx.session)[-1]!=event:raise_code('PERS-202',reason='public_context_unconfirmed')
    boundary.checked()


def validate_context_observations(events,execution_id,authorization_digest,authorization_seq,task):
    """Historical evidence checks use its original prefix, not today's ACL."""
    seen=set();requests={};checkpoint=None;phase=None;count=0
    try:
        for event in events:
            p=event.payload
            if event.type in ('execution.lifecycle','execution.child') and p.get('phase') in ('bound','running','completed','failed','cancelled','interrupted'):
                if event.type=='execution.lifecycle' or p.get('child_session_id')==event.session_id:phase=p['phase']
            elif event.type=='llm.request':requests[event.seq]=event
            elif event.type=='execution.checkpoint':checkpoint=event
            elif event.type=='execution.context':
                count+=1
                if (count>MAX_CONTEXT_OBSERVATIONS or phase!='running' or event.actor!='system' or event.task_id!=task
                        or p['execution_id']!=execution_id or p['authorization_digest']!=authorization_digest
                        or p['authorization_seq']!=authorization_seq or p['observation_id'] in seen
                        or not 2<=p['prefix_seq']<event.seq):raise ValueError('context identity or phase')
                seen.add(p['observation_id'])
                if ref:=p['checkpoint_ref']:
                    if (checkpoint is None or ref!=dict(checkpoint_id=checkpoint.payload['checkpoint_id'],source_seq=checkpoint.seq)
                            or checkpoint.seq>p['prefix_seq'] or checkpoint.payload['materialized_messages_digest']!=p['messages_digest']):
                        raise ValueError('context materialized checkpoint')
                if p['request_seq'] is not None:
                    origin=requests.get(p['request_seq'])
                    if (origin is None or origin.seq>p['prefix_seq'] or origin.actor!='llm' or origin.task_id!=task
                            or digest_of(canonical(origin.payload['model']))!=p['model_digest']
                            or origin.payload['role']!=p['role'] or origin.payload['attempt']!=p['attempt']):
                        raise ValueError('context transport provenance')
    except (ValueError,KeyError,TypeError):raise_code('PERS-202',reason='public_context_record_binding')
