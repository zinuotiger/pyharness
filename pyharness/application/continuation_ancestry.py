"""Exact original ancestor journals and captured states, with current source access."""
import time
from pyharness.application.execution_recovery_plan import journal_basis,security_refusal
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.execution_state_reader import read_state_snapshot
from pyharness.application.execution_child_graph import child_admissions
from pyharness.application.product_executions import execution_projection
from pyharness.errors import raise_code


async def read_continuation_ancestors(projects,row):
    from pyharness.application.execution_continuation_plan import state_materials,SAFE_EFFECT_TOOLS
    store=projects.store;actor=projects._authorize(write=True);resources,_,_=await store._execution_read_state()
    result=[];seen={row['execution_authorization']['session_id']};cursor=row
    while cursor['execution_authorization']['schema_version']==2:
        if len(result)>=20:raise_code('PERS-202',reason='continuation_lineage_limit')
        basis=cursor['execution_authorization']['selection']['continuation'];plan=basis['plan'];project=plan['project_id']
        source=store._cancel_target(resources,project,basis['command']['execution_ref'],actor)
        summary=store._execution_summary(resources,source,actor,lambda:projects._authorize(write=True),time.monotonic())
        source=store._execution_read_row(resources,source)
        log=await read_execution_log(projects.service._tenant_sessions_dir(),source,projects.service.tenant_id);events=list(log.events_after(0))
        journal=journal_basis(log)
        if log.sid in seen or plan['journals']!=[journal]:raise_code('PERS-202',reason='continuation_ancestor_journal_changed')
        seen.add(log.sid)
        if child_admissions(events,source) or security_refusal(events) or execution_projection(projects,source,summary,log)['unknown_effects']:
            raise_code('PERS-202',reason='continuation_ancestor_unknown')
        if any(e.type=='execution.effect' and e.payload['tool'] not in SAFE_EFFECT_TOOLS for e in events):
            raise_code('PERS-202',reason='continuation_ancestor_effect_unsupported')
        state=plan['state'];fact=next((e for e in events if e.seq==state['state_source_seq']),None)
        if fact is None or fact.type!='execution.state_checkpoint' or fact.payload['state_digest']!=state['state_digest']:
            raise_code('PERS-202',reason='continuation_ancestor_state_changed')
        cp=next((e for e in events if e.seq==fact.payload['checkpoint_seq']),None)
        body=read_state_snapshot(projects.service.platform,fact,cp)
        if not await state_materials(projects,source,body):raise_code('PERS-202',reason='continuation_ancestor_materials_changed')
        result.append(dict(row=source,summary=summary,log=log,events=events,journal=journal,fact=fact,checkpoint=cp,body=body))
        cursor=source
    return result
