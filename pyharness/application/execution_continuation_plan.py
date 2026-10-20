"""Read-only exact-state preflight; no cold writer, queue or dispatch authority."""
import time
from pyharness.application.continuation_contracts import ContinuationPlan,ContinuationStateBasis
from pyharness.application.execution_contracts import AgentVersionReference
from pyharness.application.execution_configuration import describe_execution
from pyharness.application.execution_recovery_plan import observe_recovery_plan,journal_basis
from pyharness.application.execution_state_reader import read_state_snapshot
from pyharness.application.execution_state_capture import StateUnavailable
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.sandbox import safe_path
from pyharness.persistence import session_lock_path,session_lock_held
from pyharness.errors import PyHError,raise_code

SAFE_EFFECT_TOOLS={'fs.read_file','fs.list_dir','fs.write_file','fs.patch','file.read','file.list','file.write','file.patch','storage.kv'}
SUPERSEDING_FACTS={'llm.response','llm.usage','tool.call','execution.effect','agent.message','scope.updated','execution.correction_applied'}


def seal_continuation(summary,recovery,**facts):
    from pyharness.application.continuation_plan_codec import continuation_plan_digest
    value=ContinuationPlan(project_id=summary['project_id'],execution_ref={k:summary[k] for k in ('execution_id','revision','digest','source_seq')},
        plan_digest='0'*64,recovery_plan_digest=recovery['plan_digest'],**facts)
    return value.model_copy(update=dict(plan_digest=continuation_plan_digest(value.model_dump(mode='json',exclude={'plan_digest'})))).model_dump(mode='json')


def pending_correction(row,events,state_seq):
    from pyharness.application.execution_correction_runtime import correction_ref
    history=row.get('_trusted_corrections',[])
    if not history:return False
    latest=correction_ref(history[-1])
    return not any(e.type=='execution.correction_applied' and e.seq<state_seq and e.payload['correction_ref']==latest for e in events)


async def state_materials(projects,row,body):
    from pyharness.application.execution_materials import captured_materials
    store=projects.store;actor=projects._authorize(write=True);started=time.monotonic()
    resources,_,_=await store._execution_read_state()
    current=resources.get(body.execution_id)
    if current is None or current['digest']!=row['digest']:raise_code('PERS-202',reason='continuation_execution_changed')
    attempt=store._execution_attempt(resources,row['execution_authorization']['project_id'],
        row['execution_authorization']['selection']['attempt_ref'],actor)
    materials=captured_materials(store,resources,attempt,actor,lambda:projects._authorize(write=True),started)
    if body.workspace_backend!=row['execution_authorization']['environment']['agent_snapshot']['sandbox_profile']['mode']:
        raise_code('PERS-202',reason='continuation_state_backend_binding')
    if body.filesystem_capture!='files_only_legacy' and materials['materials_digest']==body.materials_digest:
        from pyharness.application.continuation_workspace import validate_reference_workspace
        validate_reference_workspace(body,materials)
    return materials['materials_digest']==body.materials_digest


async def observe_continuation_plan(projects,row,summary):
    recovery=await observe_recovery_plan(projects,row,summary)
    blockers=[b for b in recovery['continuation_blockers'] if b not in ('restore_state_unavailable','continuation_not_implemented','continuation_gate_required')]
    resources,_,_=await projects.store._execution_read_state()
    from pyharness.application.task_graph_control_checks import graph_basis
    attempt=projects.store._execution_attempt(resources,row['execution_authorization']['project_id'],row['execution_authorization']['selection']['attempt_ref'],projects._authorize(write=True))
    if graph_basis(projects.store,resources,attempt) is not None:blockers.append('graph_continuation_requires_scheduler_admission')
    from pyharness.application.handoff_execution_store import handoff_contexts
    if handoff_contexts(projects.store,resources,attempt):blockers.append('handoff_continuation_requires_budget_admission')
    state=None;writer=None;fingerprint=None;budget=None;lineage=[]
    service=projects.service;directory=service._tenant_sessions_dir()
    try:
        root=await read_execution_log(directory,row,service.tenant_id);events=list(root.events_after(0))
        if not recovery['journals'] or journal_basis(root)!=recovery['journals'][0]:raise_code('PERS-202',reason='continuation_journal_changed')
        try:
            from pyharness.application.continuation_ancestry import read_continuation_ancestors
            ancestors=await read_continuation_ancestors(projects,row);lineage=[a['journal'] for a in ancestors]
        except PyHError as exc:
            if exc.code!='PERS-202':raise
            blockers.append('lineage_unconfirmed')
        writer=any(session_lock_held(session_lock_path(safe_path(directory,b['session_id']+'.jsonl'))) for b in [*recovery['journals'],*lineage])
        if writer:blockers.append('journal_writer_active')
        if len(recovery['journals'])!=1:blockers.append('delegated_state_not_supported')
        cp=next((e for e in reversed(events) if e.type=='execution.checkpoint'),None)
        fact=next((e for e in reversed(events) if e.type=='execution.state_checkpoint' and cp is not None and e.payload['checkpoint_seq']==cp.seq),None)
        if fact is None or fact.payload['status']!='captured':blockers.append('state_unavailable')
        else:
            try:body=read_state_snapshot(service.platform,fact,cp)
            except (PyHError,StateUnavailable,OSError,ValueError):blockers.append('state_integrity_unconfirmed')
            else:
                p=fact.payload
                state=ContinuationStateBasis(session_id=body.session_id,checkpoint_id=body.checkpoint_id,checkpoint_seq=body.checkpoint_seq,
                    checkpoint_digest=body.checkpoint_digest,state_source_seq=fact.seq,state_digest=p['state_digest'],state_size=p['state_size'],
                    materials_digest=body.materials_digest,messages_digest=body.messages_digest,filesystem_capture=body.filesystem_capture,
                    file_count=len(body.files),directory_count=len(body.directories),kv_present=body.kv_present)
                resources,_,_=await projects.store._execution_read_state()
                reference={k:summary[k] for k in ('execution_id','revision','digest','source_seq')}
                if any(r.get('run_attempt',{}).get('continuation',{}).get('command',{}).get('execution_ref')==reference and
                        r['run_attempt']['continuation']['plan']['state']['state_digest']==state.state_digest for r in resources.values()):
                    blockers.append('checkpoint_reserved')
                if body.filesystem_capture=='files_only_legacy':blockers.append('state_filesystem_incomplete')
                if not await state_materials(projects,row,body):blockers.append('state_materials_changed')
                if any(e.seq>fact.seq and e.type in SUPERSEDING_FACTS for e in events):blockers.append('checkpoint_superseded')
                if pending_correction(row,events,fact.seq):blockers.append('pending_correction')
                if not body.budget.counter_injected:blockers.append('budget_observation_unconfirmed')
                from pyharness.application.execution_continuation_budget import continuation_budget
                from pyharness.application.platform_models import AgentDefinition
                definition=AgentDefinition.model_validate(row['execution_authorization']['environment']['agent_snapshot'])
                budget,budget_blockers=continuation_budget(body,events,fact.seq,definition);blockers.extend(budget_blockers)
                if definition.skills or definition.knowledge_sources or definition.require_evidence or definition.strict_knowledge:
                    blockers.append('auxiliary_state_not_supported')
                if body.continuation_contract is not None:
                    from pyharness.application.execution_state_contract import runtime_contract_digest
                    try:
                        if body.continuation_contract.runtime_digest!=runtime_contract_digest(service):blockers.append('runtime_contract_changed')
                    except (OSError,StateUnavailable):blockers.append('runtime_contract_unconfirmed')
        if any(e.type=='execution.effect' and e.payload['tool'] not in SAFE_EFFECT_TOOLS for e in events):blockers.append('unsupported_effect_state')
        try:
            current=describe_execution(projects,AgentVersionReference.model_validate(summary['agent_ref']))
            fingerprint=current['model_fingerprint']
            if current!=row['execution_authorization']['environment']:blockers.append('configuration_changed')
        except PyHError:blockers.append('configuration_unavailable')
        return seal_continuation(summary,recovery,execution_status=recovery['execution_status'],journals=recovery['journals'],lineage_journals=lineage,
            journal_writer_active=writer,state_available=state is not None,state=state,budget=budget,current_model_fingerprint=fingerprint,
            can_continue=not blockers,blockers=sorted(set(blockers)))
    except PyHError as exc:
        if exc.code!='PERS-202':raise
        return seal_continuation(summary,recovery,execution_status='unknown',journals=[],journal_writer_active=None,state_available=False,
            state=None,current_model_fingerprint=None,blockers=sorted(set([*blockers,'journal_unconfirmed','state_unavailable'])))
