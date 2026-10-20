"""Actual public output observations through strict original execution readers."""
import hashlib
from pyharness.application.delivery_contracts import DeliveryOutputReference
from pyharness.application.execution_artifact_receipts import artifact_receipts
from pyharness.application.execution_delegation import own_lifecycle
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.errors import raise_code


def _outputs(platform,log,task,execution,*,selected=None):
    from pyharness.application.workspace_store import canonical,digest_of
    events=list(log.events_after(0));life=own_lifecycle(events,log.sid)
    if not life or life[-1].payload['phase']!='completed':
        raise_code('EVT-101',reason='delivery_execution_not_completed')
    effects=[e for e in events if e.type=='execution.effect']
    ended={e.payload['started_seq'] for e in effects if e.payload['phase']!='started'}
    if any(e.payload['phase']=='unknown' or e.payload.get('file_observation')=='unavailable' or
           (e.payload['phase']=='started' and e.seq not in ended) for e in effects):
        raise_code('EVT-101',reason='delivery_execution_effects_unknown')
    found=[];observed=log
    if selected is not None:
        from types import SimpleNamespace
        from pyharness.application.execution_artifact_receipts import generated_receipt_records
        # Check the complete original metadata first, including duplicate IDs
        # and real lifecycle identity. Only the granted file bodies are opened.
        list(generated_receipt_records(log,task))
        wanted={r['source_seq'] for r in selected if r['session_id']==log.sid and r['kind']=='artifact'}
        filtered=[e for e in events if not(e.type=='platform.artifact' and e.payload['action']=='generated') or e.seq in wanted]
        observed=SimpleNamespace(sid=log.sid,events_after=lambda seq:iter(e for e in filtered if e.seq>seq))
    for item in artifact_receipts(platform,observed,task,execution):
        reference=DeliveryOutputReference(kind='artifact',execution_id=execution,session_id=log.sid,
            task_id=task,source_seq=item['source_seq'],record_digest=item['record_digest'],
            sha256=item['sha256'],size_bytes=item['size'],artifact_id=item['artifact_id'])
        found.append(dict(reference=reference.model_dump(),filename=item['filename'],
            integrity=item['integrity'],preview_capability='requires_explicit_export',text_preview=None))
    for index,event in enumerate(events):
        if event.type!='agent.message':continue
        before=own_lifecycle(events[:index],log.sid)
        # Existing engine messages omit task_id and link the preceding public
        # final response by trace. Accept that historical shape only inside the
        # already validated sole-task execution, with its real causal response.
        parent=(event.trace or {}).get('parent_seq')
        response=next((e for e in events[:index] if e.seq==parent),None)
        legacy=(event.task_id is None and response is not None and response.type=='llm.response'
                and response.actor=='llm' and not response.payload.get('tool_calls'))
        if event.actor!='agent' or (event.task_id!=task and not legacy) or not before or before[-1].payload['phase']!='running':
            raise_code('PERS-202',reason='delivery_message_binding_invalid')
        if selected is not None and not any(r['session_id']==log.sid and r['kind']=='message' and r['source_seq']==event.seq for r in selected):continue
        raw=event.payload['content'].encode('utf-8')
        if len(raw)>32*1024*1024:raise_code('EVT-100',reason='delivery_message_limit')
        reference=DeliveryOutputReference(kind='message',execution_id=execution,session_id=log.sid,
            task_id=task,source_seq=event.seq,record_digest=digest_of(canonical(event.payload)),
            sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
        text=event.payload['content']
        found.append(dict(reference=reference.model_dump(),filename=None,integrity='verified',
            preview_capability='text',text_preview=text[:16000],preview_truncated=len(text)>16000))
    return found


async def actual_outputs(service,row,check,*,selected=None):
    """The caller supplies actual request authorization before any private read."""
    from pyharness.application.execution_child_graph import child_admissions
    from pyharness.application.execution_child_reader import read_child_log
    check();value=row['execution_authorization']
    root=await read_execution_log(service._tenant_sessions_dir(),row,service.tenant_id)
    check();found=_outputs(service.platform,root,value['task_id'],value['execution_id'],selected=selected)
    admissions=child_admissions(list(root.events_after(0)),row)
    if len(admissions)>400:raise_code('EVT-100',reason='delivery_child_limit')
    selected_sids={s['session_id'] for s in selected} if selected is not None else None
    for sid in admissions:
        check()
        # A frozen package observes its selected children; it never imports a
        # different child's context or silently broadens the granted selection.
        if selected_sids is not None and sid not in selected_sids:continue
        log=await read_child_log(service._tenant_sessions_dir(),row,service.tenant_id,sid)
        found.extend(_outputs(service.platform,log,'subagent:'+sid,value['execution_id'],selected=selected))
    check()
    if len(found)>400:raise_code('EVT-100',reason='delivery_output_inventory_limit')
    return found


def verify_selected(outputs,selected):
    from pyharness.application.workspace_store import canonical
    indexed={canonical(o['reference']):o for o in outputs}
    for reference in selected:
        item=indexed.get(canonical(reference))
        if item is None:raise_code('EVT-101',reason='delivery_output_reference_mismatch')
        if item['integrity']!='verified':raise_code('EVT-101',reason='delivery_output_bytes_changed')


def current_selected(service,row,selected):
    """Synchronous final gate: original root/child validation and checked file I/O."""
    from types import SimpleNamespace
    from pyharness.application.product_execution_sessions import read_execution_events
    from pyharness.application.execution_child_graph import child_admissions,validate_child_events,invocation_proof
    from pyharness.application.execution_child_reader import strict_child_events
    value=row['execution_authorization'];directory=service._tenant_sessions_dir()
    root=read_execution_events(directory,row,service.tenant_id)
    admissions=child_admissions(root,row);cache={value['session_id']:root}
    def read(sid,seen):
        if sid in cache:return cache[sid]
        if sid not in admissions:raise_code('GRD-403',reason='delivery_foreign_output_session')
        if sid in seen or len(seen)>3:
            raise_code('PERS-202',reason='delivery_child_binding_missing')
        admission=admissions[sid]
        events=strict_child_events(directory,sid,service.tenant_id)
        validate_child_events(events,admission)
        parent=read(admission.payload['invoker_session_id'],seen|{sid})
        try:invocation_proof(admission.payload,parent,admission.payload['depth']-1)
        except (ValueError,KeyError):raise_code('PERS-202',reason='delivery_child_caller_changed')
        cache[sid]=events
        return events
    outputs=[]
    for sid in {s['session_id'] for s in selected}:
        events=read(sid,set());task=value['task_id'] if sid==value['session_id'] else 'subagent:'+sid
        log=SimpleNamespace(sid=sid,events_after=lambda seq,rows=events:iter(e for e in rows if e.seq>seq))
        outputs.extend(_outputs(service.platform,log,task,value['execution_id'],selected=selected))
    verify_selected(outputs,selected)
    return outputs


def output_bytes(service,row,reference):
    """Recheck physical bytes through guarded readers; never use a client path."""
    from pyharness.core.sandbox import safe_path,read_regular
    from pyharness.application.product_execution_sessions import read_execution_events
    from pyharness.application.execution_child_reader import strict_child_events
    current_selected(service,row,[reference])
    if reference['kind']=='artifact':
        raw=read_regular(safe_path(service.platform.root/'artifacts',reference['artifact_id']),32*1048576)
    else:
        value=row['execution_authorization'];sid=reference['session_id'];directory=service._tenant_sessions_dir()
        events=(read_execution_events(directory,row,service.tenant_id) if sid==value['session_id'] else
            strict_child_events(directory,sid,service.tenant_id))
        event=next((e for e in events if e.seq==reference['source_seq']),None)
        if event is None or event.type!='agent.message':raise_code('PERS-202',reason='delivery_output_unavailable')
        raw=event.payload['content'].encode('utf-8')
    if len(raw)!=reference['size_bytes'] or hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise_code('EVT-101',reason='delivery_output_bytes_changed')
    return raw
