"""Strong intent before file effects, durable observed outcomes, no blind retry."""
import time
from pathlib import Path
from pyharness.application.writeback_records import WritebackApplication
from pyharness.application.writeback_support import wb_request,approval_digest,merge_selection
from pyharness.application.writeback_io import LockedTargets,observe_target
from pyharness.errors import raise_code


class WritebackApplications:
    async def apply_merge_plan(self,project,command,*,command_id,authorize,service):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application import writeback_io
        started=time.monotonic();kind='writeback_application';ident=command.application_id
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=wb_request(project,'apply',command.model_dump())
            self._context_envelope(kind,ident,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                origin=old[1].get(kind)
                if not origin or origin['project_id']!=project:raise_code('GRD-403',reason='writeback_submission_unavailable')
                self._context_project(resources,project)
                return self._wb_commit_summary(resources[origin['selection']['application_id']],kind,actor,authorize)
            if ident in resources:raise_code('EVT-101',reason='writeback_application_exists')
            planrow=self._wb_exact(resources,project,'merge_plan',command.plan_ref.model_dump());plan=planrow['merge_plan']
            if plan['created_by']!=actor:raise_code('GRD-403',reason='merge_plan_private')
            if approval_digest(planrow)!=command.approval_digest:raise_code('EVT-101',reason='writeback_approval_changed')
            if any(r.get(kind,{}).get('selection',{}).get('plan_ref')==command.plan_ref.model_dump() for r in resources.values()):
                raise_code('EVT-101',reason='merge_plan_already_admitted')
            result_key='writeback-result-'+digest_of(canonical(dict(key=command_id,application_id=ident)))[:32]
            if (actor,result_key) in commands:raise_code('EVT-100',reason='writeback_result_key_conflict')
            selected=merge_selection(plan['selection'])
            current,files,observed=self._wb_collect(resources,project,selected,actor,authorize,service,started)
            if current['status']!='mergeable' or current['confirmation_digest']!=plan['basis_digest'] or files!=plan['files']:
                raise_code('EVT-101',reason='writeback_target_or_plan_changed')
            def gate():
                if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='writeback_application_time_limit')
                fresh,_,_=self._delivery_read_snapshot();self._wb_current(fresh,project,selected,actor,authorize,service)
            for digest,raw in observed.items():
                if self._wb_blob(digest,gate)!=raw:raise_code('PERS-202',reason='writeback_plan_blob_changed')
            grant,_,_,_,_=self._wb_current(resources,project,selected,actor,authorize,service)
            root=Path(grant['writeback_grant']['canonical_root'])
            value=WritebackApplication(project_id=project,project_revision=plan['project_revision'],created_by=actor,
                submission_key=command_id,revision=1,selection=command,origin_key=command_id,status='admitted')
            with LockedTargets(root,plan['files'],gate) as locked:
                def intent_gate():
                    fresh,keys=self._grant_state();self._context_quota(fresh,kind,project)
                    if ident in fresh or (actor,result_key) in keys or any(r.get(kind,{}).get('selection',{}).get('plan_ref')==command.plan_ref.model_dump() for r in fresh.values()):
                        raise_code('EVT-101',reason='merge_plan_already_admitted')
                    gate()
                intent_gate()
                # An append/ack failure here exits the handles without touching
                # a target. A cold admitted intent is observationally unknown.
                _,intent=await self._append_context(value,kind,ident,command_id,actor,request,intent_gate)
                attempted=set();operation_error=None
                for index,item in enumerate(plan['files']):
                    try:
                        gate();attempted.add(index)
                        writeback_io.apply_one(locked,item,observed.get(item['merged_digest']))
                    except Exception as exc:
                        operation_error=type(exc).__name__;break
                effects=[]
                for index,item in enumerate(plan['files']):
                    before=item['target']['sha256'] if item['target'] else None;expected=item['merged_digest']
                    effect=dict(target_path=item['target_path'],action=item['action'],before_digest=before,
                        expected_digest=expected,observed_digest=None,status='not_applied' if index not in attempted else 'unknown')
                    try:
                        gate()
                        if item['target_path'] in locked.streams:raw=locked.read(item['target_path'])
                        else:_,raw=observe_target(root,item['target_path'],gate)
                        after=digest_of(raw) if raw is not None else None;effect['observed_digest']=after
                        effect['status']='verified' if after==expected else 'not_applied' if after==before else 'unknown'
                    except Exception:pass
                    effects.append(effect)
                if all(e['status']=='verified' for e in effects):status='completed'
                elif any(e['status']=='verified' and e['action']!='none' for e in effects):status='partial'
                elif any(e['status']=='unknown' for e in effects):status='unknown'
                else:status='failed'
                outcome=WritebackApplication.model_validate(dict(value.model_dump(),revision=2,submission_key=result_key,status=status,effects=effects))
                def result_gate():
                    fresh,keys=self._grant_state();row=fresh.get(ident)
                    if row!=intent or (actor,result_key) in keys:raise_code('PERS-202',reason='writeback_outcome_binding_changed')
                    # Recording the outcome of this already admitted operation
                    # confers no new read/write authority, even after withdrawal.
                _,result=await self._append_context(outcome,kind,ident,result_key,actor,request,result_gate)
                return self._wb_commit_summary(result,kind,actor,authorize)
