"""Exact generated artifact references from the original journal and physical bytes."""
import hashlib,re
from pyharness.application.execution_delegation import own_lifecycle
from pyharness.application.platform_models import ArtifactRecord
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.sandbox import safe_path,read_regular
from pyharness.errors import raise_code


def generated_receipt_records(log,task):
    """Validate every original generated record without reading artifact bytes."""
    events=list(log.events_after(0));seen=set()
    for i,event in enumerate(events):
        if event.type!='platform.artifact' or event.payload['action']!='generated':continue
        record=event.payload['record']
        try:item=ArtifactRecord.model_validate(record)
        except ValueError:raise_code('PERS-202',reason='execution_artifact_record_invalid')
        phases=own_lifecycle(events[:i],log.sid)
        if (event.actor!='system' or event.task_id!=task or item.session_id!=log.sid or
                item.run_id!=log.sid+'~'+task or item.model_dump()!=record or item.artifact_id in seen or
                item.download_allowed or not item.approval_required or
                not re.fullmatch(r'f-[0-9a-f]{32}',item.artifact_id) or item.storage_path!=item.artifact_id or
                not re.fullmatch(r'[0-9a-f]{64}',item.sha256) or not phases or phases[-1].payload['phase']!='running'):
            raise_code('PERS-202',reason='execution_artifact_binding_invalid')
        seen.add(item.artifact_id)
        yield event,item


def artifact_receipts(platform,log,task,execution):
    result=[]
    for event,item in generated_receipt_records(log,task):
        record=event.payload['record']
        integrity='unavailable'
        try:
            raw=read_regular(safe_path(platform.root/'artifacts',item.storage_path),32*1048576)
            integrity='verified' if len(raw)==item.size and hashlib.sha256(raw).hexdigest()==item.sha256 else 'mismatch'
            if item.manifest_sha256 is not None:
                manifest=read_regular(safe_path(platform.root/'artifacts',item.storage_path+'.manifest'),32*1048576)
                if hashlib.sha256(manifest).hexdigest()!=item.manifest_sha256:integrity='mismatch'
        except (OSError,ValueError):pass
        except Exception as exc:
            if getattr(exc,'code',None) not in ('path_rejected','file_not_regular','file_too_large'):raise
        result.append(dict(execution_id=execution,session_id=log.sid,task_id=task,artifact_id=item.artifact_id,
            source_seq=event.seq,record_digest=digest_of(canonical(record)),filename=item.filename,size=item.size,
            sha256=item.sha256,integrity=integrity,download_allowed=item.download_allowed,
            approval_required=item.approval_required,validation_status=item.validation_status))
    return result


async def delegated_receipts(projects,row,root):
    """Read each exact admitted child, without starting it or mixing task identities."""
    from pyharness.application.execution_child_graph import child_admissions
    from pyharness.application.execution_child_reader import read_child_log
    from pyharness.errors import PyHError
    service=projects.service;value=row['execution_authorization']
    admissions=child_admissions(list(root.events_after(0)),row)
    children=[];artifacts=[];uncertain=len(admissions)>400
    for sid,admission in list(admissions.items())[-400:]:
        if projects._authorize(write=True)!=value['created_by']:raise_code('GRD-403',reason='execution_child_subject_changed')
        item=dict(session_id=sid,invoker_session_id=admission.payload['invoker_session_id'],
            depth=admission.payload['depth'],admission_seq=admission.seq,status='unknown')
        try:
            log=await read_child_log(service._tenant_sessions_dir(),row,service.tenant_id,sid)
            events=list(log.events_after(0));life=own_lifecycle(events,sid)
            item['status']=life[-1].payload['phase']
            if any(e.type=='subagent.failed' and e.payload['sub_id']==sid for e in events):item['status']='failed'
            if item['status'] in ('bound','running'):
                spine=service._engines.get(root.sid);manager=getattr(spine,'subagent',None)
                h=getattr(manager,'_children',{}).get(sid)
                if h is None or h.task is None or h.task.done():item['status']='interrupted'
            artifacts.extend(artifact_receipts(service.platform,log,'subagent:'+sid,value['execution_id']))
            effects=[e for e in events if e.type=='execution.effect']
            ended={e.payload['started_seq'] for e in effects if e.payload['phase']!='started'}
            uncertain=uncertain or any(e.payload['phase']=='unknown' or e.payload.get('file_observation')=='unavailable' or
                (e.payload['phase']=='started' and e.seq not in ended) for e in effects)
        except PyHError as exc:
            if exc.code!='PERS-202':raise
            item.update(status='unknown',observation_error='child_journal_unconfirmed');uncertain=True
        children.append(item)
    return dict(children=children,artifacts=artifacts,unknown_effects=uncertain,
        children_total=len(admissions),children_truncated=len(admissions)>400)
