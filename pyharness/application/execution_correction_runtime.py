"""Apply trusted root supplements only before the next logical context is built."""
from pyharness.application.execution_effects import flush_confirmed,confirmed_events
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.application.execution_delegation import own_lifecycle
from pyharness.errors import raise_code


def correction_ref(row):
    value=row['execution_correction']
    return dict(correction_id=value['correction_id'],revision=value['revision'],digest=row['digest'],source_seq=row['source_seq'])


def supplement(row):
    value=row['execution_correction']
    return ('Trusted task supplement '+value['correction_id']+' revision '+str(value['revision'])+
        '. This revision replaces earlier supplements in this document. It does not change tool, budget, data or access permissions.\n'+value['text'])


def trusted_version(row,reference):
    found=[r for r in row.get('_trusted_corrections',[]) if correction_ref(r)==reference]
    if len(found)!=1:raise_code('PERS-202',reason='correction_control_reference_unavailable')
    return found[0]


def validate_correction_applications(events,row):
    value=row['execution_authorization'];task=value['task_id'];reference=dict(execution_id=value['execution_id'],revision=1,digest=row['digest'],source_seq=row['source_seq'])
    seen=set();previous=0;result={};byseq={e.seq:e for e in events}
    for i,event in enumerate(events):
        if event.type!='execution.correction_applied':continue
        p=event.payload;life=own_lifecycle(events[:i],event.session_id)
        version=trusted_version(row,p['correction_ref']);definition=version['execution_correction'];key=(definition['correction_id'],definition['revision'])
        message=byseq.get(p['user_message_seq']);text=supplement(version)
        if (event.session_id!=value['session_id'] or event.actor!='system' or event.task_id!=task or
                not life or life[-1].payload['phase']!='running' or p['execution_id']!=value['execution_id'] or
                p['authorization_digest']!=row['digest'] or p['authorization_seq']!=row['source_seq'] or
                definition['execution_ref']!=reference or definition['created_by']!=value['created_by'] or
                key in seen or definition['revision']<=previous or message is None or message.seq>=event.seq or
                message.type!='user.message' or message.actor!='user' or message.task_id!=task or
                message.payload['content']!=text or (message.payload.get('meta') or {}).get('product_correction')!=p['correction_ref'] or
                p['text_digest']!=digest_of(text.encode('utf-8'))):
            raise_code('PERS-202',reason='execution_correction_application_invalid')
        checkpoint=next((e for e in events[i+1:] if e.type=='execution.checkpoint'),None)
        if checkpoint is not None and checkpoint.payload['materialized_messages_digest']!=p['materialized_messages_digest']:
            raise_code('PERS-202',reason='execution_correction_context_digest')
        seen.add(key);previous=definition['revision'];result[key]=event
    return result


async def prepare_correction(boundary,ctx):
    # Root supplements are deliberately scoped; an in-flight child keeps its
    # admitted task. Root sees the new supplement at its own next boundary.
    if hasattr(boundary,'root'):return
    boundary._context(ctx);boundary.checked();receipt=boundary.receipt
    row,_,versions=await boundary.projects.store.execution_correction_versions(receipt['project_id'],receipt['execution_id'],
        authorize=lambda:boundary.projects._authorize(write=True))
    boundary.checked();events=await flush_confirmed(ctx.session)
    applications=validate_correction_applications(events,row)
    boundary._round_correction_ref=None
    if not versions:return
    version=row['_trusted_corrections'][-1];reference=correction_ref(version);definition=version['execution_correction']
    key=(definition['correction_id'],definition['revision'])
    if key not in applications:
        matches=[e for e in events if e.type=='user.message' and (e.payload.get('meta') or {}).get('product_correction')==reference]
        if len(matches)>1:raise_code('PERS-202',reason='correction_orphan_ambiguous')
        text=supplement(version)
        if matches:
            message=matches[0]
            if message.actor!='user' or message.task_id!=str(ctx.task_id) or message.payload['content']!=text:
                raise_code('PERS-202',reason='correction_orphan_changed')
        else:
            boundary.checked()
            message=await ctx.session.append('user.message',dict(content=text,meta=dict(product_correction=reference)),
                actor='user',task_id=str(ctx.task_id),sync=True)
            if message not in confirmed_events(ctx.session):raise_code('PERS-202',reason='correction_message_unconfirmed')
    boundary._round_correction_ref=reference


async def materialize_correction(boundary,ctx,messages):
    reference=getattr(boundary,'_round_correction_ref',None)
    if reference is None or hasattr(boundary,'root'):return messages
    boundary._context(ctx);boundary.checked();receipt=boundary.receipt
    row,_,_=await boundary.projects.store.execution_correction_versions(receipt['project_id'],receipt['execution_id'],
        authorize=lambda:boundary.projects._authorize(write=True))
    version=trusted_version(row,reference);events=await flush_confirmed(ctx.session)
    applications=validate_correction_applications(events,row)
    text=supplement(version)
    # This is a projection of the confirmed control version and its applied
    # fact, keeping an active supplement visible after ordinary history trims.
    if not any(m.get('role')=='user' and m.get('content')==text for m in messages):
        messages=[*messages,dict(role='user',content=text)]
    boundary.checked()
    key=(reference['correction_id'],reference['revision'])
    if key not in applications:
        matches=[e for e in events if e.type=='user.message' and (e.payload.get('meta') or {}).get('product_correction')==reference]
        if (len(matches)!=1 or matches[0].actor!='user' or matches[0].task_id!=str(ctx.task_id) or matches[0].payload['content']!=text):
            raise_code('PERS-202',reason='correction_context_message_unconfirmed')
        event=await ctx.session.append('execution.correction_applied',dict(schema_version=1,execution_id=receipt['execution_id'],
            authorization_digest=receipt['digest'],authorization_seq=receipt['source_seq'],correction_ref=reference,
            user_message_seq=matches[0].seq,text_digest=digest_of(text.encode('utf-8')),
            materialized_messages_digest=digest_of(canonical(messages))),actor='system',task_id=str(ctx.task_id),sync=True)
        actual=confirmed_events(ctx.session)
        if event not in actual:raise_code('PERS-202',reason='correction_application_unconfirmed')
        validate_correction_applications(actual,row);boundary.checked()
    return messages
