"""Strict observation of exact execution facts; no writer, repair or dispatch."""
from pathlib import Path
from pyharness.application.execution_checkpoints import prefix_digest
from pyharness.application.execution_child_graph import child_admissions
from pyharness.application.execution_child_reader import read_child_log
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.product_executions import execution_projection
from pyharness.application.execution_artifact_receipts import delegated_receipts
from pyharness.application.execution_liveness import control_observation
from pyharness.application.recovery_contracts import RecoveryPlan,IndexObservation
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.errors import PyHError


def journal_basis(log):
    events=list(log.events_after(0))
    return dict(session_id=log.sid,observed_seq=events[-1].seq,prefix_digest=prefix_digest(events))


def security_refusal(events):
    return any(e.type in ('guard.rejected','approval.denied','approval.timeout') for e in events)


def state_available(projects,events,checkpoint):
    from pyharness.application.execution_state_reader import read_state_snapshot
    if checkpoint is None:return False
    cp=next((e for e in events if e.seq==checkpoint['source_seq']),None)
    fact=next((e for e in events if e.type=='execution.state_checkpoint' and e.payload['checkpoint_seq']==checkpoint['source_seq']),None)
    if fact is None or fact.payload['status']!='captured':return False
    try:body=read_state_snapshot(projects.service.platform,fact,cp)
    except (OSError,PyHError):return False
    return body.filesystem_capture in ('files_and_directories','no_workspace')


def index_scope_path(service):return Path(service.effective_settings().storage.db_path).expanduser().resolve()


def index_observation(service,events):
    from pyharness.core.query_projection import read_max_seq,is_indexable
    expected=max((e.seq for e in events if is_indexable(e.type,e)),default=0)
    path=index_scope_path(service);scope=digest_of(canonical(str(path)))
    if not path.exists():return IndexObservation(state='not_built',scope_digest=scope,expected_content_seq=expected,observed_content_seq=0)
    try:
        actual=read_max_seq(path,events[0].session_id)
        return IndexObservation(state='current' if actual==expected else 'stale',scope_digest=scope,expected_content_seq=expected,observed_content_seq=actual)
    except (OSError,PyHError):
        return IndexObservation(state='unavailable',scope_digest=scope,expected_content_seq=expected,error_code='PERS-202')


def sealed_plan(summary,**facts):
    reference={k:summary[k] for k in ('execution_id','revision','digest','source_seq')}
    value=RecoveryPlan(project_id=summary['project_id'],execution_ref=reference,plan_digest='0'*64,**facts)
    digest=digest_of(canonical(value.model_dump(mode='json',exclude={'plan_digest'})))
    return value.model_copy(update={'plan_digest':digest}).model_dump(mode='json')


async def observe_recovery_plan(projects,row,summary):
    service=projects.service;directory=service._tenant_sessions_dir()
    try:
        root=await read_execution_log(directory,row,service.tenant_id)
        events=list(root.events_after(0));result=execution_projection(projects,row,summary,root)
        delegated=await delegated_receipts(projects,row,root)
        unknown=result['unknown_effects'] or delegated.pop('unknown_effects')
        result['artifacts'].extend(delegated.pop('artifacts'));result.update(delegated)
        control=control_observation(projects,row,root,result)
        admissions=child_admissions(events,row);journals=[journal_basis(root)];blockers=[]
        for sid in list(admissions)[-400:]:
            try:
                child=await read_child_log(directory,row,service.tenant_id,sid)
                journals.append(journal_basis(child))
                if security_refusal(child.events_after(0)):blockers.append('security_refusal')
            except PyHError as exc:
                if exc.code!='PERS-202':raise
                blockers.append('child_journal_unconfirmed');unknown=True
        if len(admissions)>400:blockers.append('journal_scope_truncated')
        if unknown:blockers.append('unknown_effects')
        if control['execution_owner']=='live':blockers.append('execution_live')
        if result['status']=='completed':blockers.append('execution_completed')
        if security_refusal(events) or str(result['execution_reason'] or '').startswith(('GRD-','APR-')):
            blockers.append('security_refusal')
        checkpoints=[e for e in events if e.type=='execution.checkpoint'];latest=None
        if checkpoints:
            e=checkpoints[-1];latest={k:e.payload[k] for k in ('checkpoint_id','prefix_seq','prefix_digest','materialized_messages_digest')};latest['source_seq']=e.seq
        else:blockers.append('checkpoint_missing')
        artifacts=result['artifacts']
        if any(a['integrity']!='verified' for a in artifacts):
            unknown=True;blockers.append('unknown_effects')
        if len(artifacts)>1000:blockers.append('artifact_scope_truncated')
        available=state_available(projects,events,latest)
        if not available:blockers.append('restore_state_unavailable')
        blockers.append('continuation_gate_required')
        return sealed_plan(summary,execution_status=result['status'],journals=journals,journals_total=1+len(admissions),journals_truncated=len(admissions)>400,
            latest_checkpoint=latest,artifacts=[{k:a[k] for k in ('artifact_id','session_id','source_seq','record_digest','sha256','integrity')} for a in artifacts[:1000]],
            artifacts_total=len(artifacts),artifacts_truncated=len(artifacts)>1000,index_observation=index_observation(service,events),
            unknown_effects=unknown,restore_state_available=available,continuation_blockers=sorted(set(blockers)))
    except PyHError as exc:
        if exc.code!='PERS-202':raise
        return sealed_plan(summary,execution_status='unknown',journals=[],journals_total=0,journals_truncated=False,
            latest_checkpoint=None,artifacts=[],artifacts_total=0,artifacts_truncated=False,index_observation=IndexObservation(state='unavailable',error_code='PERS-202'),
            unknown_effects=True,continuation_blockers=['journal_unconfirmed','restore_state_unavailable','continuation_gate_required'])
