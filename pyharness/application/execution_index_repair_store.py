"""Two-attempt repair audit on the sole control owner; never replay old tools."""
import asyncio,time
from pyharness.application.index_repair_contracts import IndexRepairRevision,RepairIndex
from pyharness.errors import PyHError,raise_code


def repair_fault(plan,scope):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='index.repair',execution_ref=plan['execution_ref'],
        journals=plan['journals'],index_scope_digest=scope)))


class ExecutionIndexRepairStore:
    @staticmethod
    def _index_repair_request(project,command):
        from pyharness.application.workspace_store import canonical,digest_of
        return digest_of(canonical(dict(operation='execution.index_repair',project_id=project,command=command.model_dump())))

    @staticmethod
    def _index_repair_result_request(definition):
        from pyharness.application.workspace_store import canonical,digest_of
        return digest_of(canonical(dict(operation='execution.index_repair.result',definition=definition.model_dump())))

    @staticmethod
    def _index_repair_result_key(ident):return 'index-repair-result-'+ident

    @staticmethod
    def _index_repair_rows(resources,reference):
        return [r for r in resources.values() if r.get('index_repair',{}).get('command',{}).get('execution_ref')==reference]

    def _validate_index_repair(self,definition,resources,previous,event):
        from pyharness.application.workspace_store import canonical,digest_of
        value=definition.model_dump();actor=value['created_by'];project=value['project_id'];p=event.payload
        try:
            root=self._cancel_target(resources,project,definition.command.execution_ref,actor)
            if root['execution_authorization']['project_revision']!=value['project_revision']:raise ValueError('repair project revision')
            self._context_project(resources,project,value['project_revision'])
            plan=value['plan'];basis={k:v for k,v in plan.items() if k!='plan_digest'}
            if (digest_of(canonical(basis))!=plan['plan_digest'] or not plan['journals'] or
                    plan['journals'][0]['session_id']!=root['execution_authorization']['session_id'] or
                    repair_fault(plan,value['index_scope_digest'])!=value['fault_digest'] or actor!=p['actor_ref']):raise ValueError('repair basis')
            if plan['unknown_effects'] or plan['journals_truncated'] or plan['artifacts_truncated'] or any(
                    b in plan['continuation_blockers'] for b in ('execution_live','security_refusal','journal_unconfirmed','child_journal_unconfirmed')):
                raise ValueError('unsafe repair basis')
            if value['revision']==1:
                if previous is not None or p['command_id']!=value['submission_key'] or self._index_repair_request(project,definition.command)!=p['request_digest']:
                    raise ValueError('repair start request')
                prior=[r for r in resources.values() if r.get('index_repair',{}).get('fault_digest')==value['fault_digest']]
                if len(prior)>=2 or value['attempt']!=len(prior)+1:raise ValueError('repair attempt limit')
                if plan['index_observation']['state']=='current':raise ValueError('index already current')
            else:
                if previous is None or previous['index_repair']['phase']!='started':raise ValueError('repair result predecessor')
                old=previous['index_repair']
                if any(value[k]!=v for k,v in old.items() if k not in ('revision','phase','index_observation','error_code')):
                    raise ValueError('repair frozen start changed')
                if p['command_id']!=self._index_repair_result_key(value['repair_id']) or self._index_repair_result_request(definition)!=p['request_digest']:
                    raise ValueError('repair result request')
            self._context_quota(resources,'index_repair',project)
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='index_repair_binding')

    def _index_repair_summary(self,resources,row,actor,authorize,started):
        value=row['index_repair'];project=value['project_id']
        self._context_owned({value['repair_id']:row},value['repair_id'],'index_repair',project,actor)
        root=self._cancel_target(resources,project,value['command']['execution_ref'],actor)
        self._execution_summary(resources,root,actor,authorize,started);self._context_finish(authorize,actor,resources)
        return dict(schema_version=1,repair_id=value['repair_id'],revision=value['revision'],phase=value['phase'],attempt=value['attempt'],
            project_id=project,execution_ref=value['command']['execution_ref'],plan_digest=value['command']['plan_digest'],fault_digest=value['fault_digest'],
            index_observation=value['index_observation'],error_code=value['error_code'],submission_key=value['submission_key'],
            digest=row['digest'],source_seq=row['source_seq'],request_digest=self._index_repair_request(project,RepairIndex.model_validate(value['command'])),
            persisted=True,automatic_dispatch=False,continuation_authorized=False)

    async def apply_index_repair(self,project,command,*,command_id,authorize,inspect,perform):
        started=time.monotonic();leases=[]
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=self._index_repair_request(project,command)
            self._context_envelope('index_repair',command.repair_id,1,command_id,actor,request)
            old=commands.get((actor,command_id))
            if old:
                if old[0]!=request or 'index_repair' not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                row=resources[old[1]['index_repair']['repair_id']]
                return self._index_repair_summary(resources,row,actor,authorize,started)
            if command.repair_id in resources:raise_code('EVT-101',reason='index_repair_id_reserved')
            result_key=self._index_repair_result_key(command.repair_id)
            if command_id==result_key or (actor,result_key) in commands:raise_code('EVT-100',reason='index_repair_result_key_reserved')
            root=self._cancel_target(resources,project,command.execution_ref,actor)
            summary=self._execution_summary(resources,root,actor,authorize,started)
            context=await inspect(self._execution_read_row(resources,root),summary)
            plan=context['plan'];fault=repair_fault(plan,context['scope_digest'])
            prior=[r for r in resources.values() if r.get('index_repair',{}).get('fault_digest')==fault]
            if len(prior)>=2:raise_code('GRD-403',reason='index_repair_limit_reached')
            if plan['plan_digest']!=command.plan_digest:raise_code('EVT-101',reason='index_repair_plan_changed')
            if (plan['unknown_effects'] or plan['journals_truncated'] or plan['artifacts_truncated'] or
                    plan['index_observation']['state']=='current' or any(b in plan['continuation_blockers'] for b in
                    ('execution_live','security_refusal','journal_unconfirmed','child_journal_unconfirmed'))):
                raise_code('GRD-403',reason='index_repair_not_permitted')
            try:
                leases=context['acquire']()
                context['verify']()
                definition=IndexRepairRevision(repair_id=command.repair_id,revision=1,phase='started',project_id=project,
                    project_revision=root['execution_authorization']['project_revision'],created_by=actor,submission_key=command_id,
                    command=command,plan=plan,index_scope_digest=context['scope_digest'],fault_digest=fault,attempt=len(prior)+1)
                def gate():
                    fresh,_=self._grant_state();target=self._cancel_target(fresh,project,command.execution_ref,actor)
                    self._execution_summary(fresh,target,actor,authorize,started);context['verify']()
                    self._context_finish(authorize,actor,fresh)
                gate()
                resources,row=await self._append_context(definition,'index_repair',command.repair_id,command_id,actor,request,gate)
                # The start is confirmed before any derived-index mutation.
                gate();error=None;cancelled=None
                try:after=await perform(context)
                except asyncio.CancelledError as exc:
                    cancelled=exc;error='repair_cancelled';after=context['observe']()
                except Exception:
                    error='PERS-202';after=context['observe']()
                gate()
                phase='unknown' if cancelled else 'succeeded' if after.state=='current' else 'failed'
                result=definition.model_copy(update=dict(revision=2,phase=phase,index_observation=after,error_code=None if phase=='succeeded' else error or 'PERS-202'))
                result=IndexRepairRevision.model_validate(result.model_dump())
                resources,row=await self._append_context(result,'index_repair',command.repair_id,result_key,actor,self._index_repair_result_request(result),gate)
                if cancelled is not None:raise cancelled
                return self._index_repair_summary(resources,row,actor,authorize,started)
            finally:
                for lease in reversed(leases):lease.release()

    async def index_repair_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state();self._context_project(resources,project)
            old=commands.get((actor,key));row=old[1] if old else None
            if row is not None:
                if not row.get('index_repair') or row['index_repair']['project_id']!=project:raise_code('GRD-403',reason='index_repair_submission_unavailable')
                row=resources[row['index_repair']['repair_id']]
            result=self._index_repair_summary(resources,row,actor,authorize,started) if row else None
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if row else 'not_found',repair=result)

    async def index_repairs(self,project,ident,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            root=self._context_owned(resources,ident,'execution_authorization',project,actor)
            summary=self._execution_summary(resources,root,actor,authorize,started)
            ref={k:summary[k] for k in ('execution_id','revision','digest','source_seq')}
            rows=sorted(self._index_repair_rows(resources,ref),key=lambda r:r['source_seq'])
            result=[self._index_repair_summary(resources,r,actor,authorize,started) for r in rows[-200:]]
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,execution_ref=ref,repairs=result,total=len(rows),truncated=len(rows)>200)
