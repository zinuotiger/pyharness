"""Explicit continuation admission; cold reads never create an execution owner."""
import time
from types import SimpleNamespace
from pyharness.application.continuation_execution_contracts import ContinueExecution
from pyharness.application.execution_contracts import ExecutionId,AgentVersionReference
from pyharness.application.execution_configuration import describe_execution
from pyharness.application.execution_continuation_plan import observe_continuation_plan
from pyharness.application.execution_materials import captured_materials
from pyharness.application.execution_recovery_plan import journal_basis
from pyharness.application.execution_state_reader import read_state_snapshot
from pyharness.application.execution_state_capture import state_regular
from pyharness.application.execution_correction_runtime import correction_ref
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.workspace_store import digest_of
from pyharness.core.lifecycle import admitted
from pyharness.core.sandbox import safe_path
from pyharness.errors import raise_code
from pyharness.persistence import session_data_paths,session_lock_path,session_lock_held,_FileLock


class ProductContinuations:
    async def _continuation_context(self,row,summary):
        store=self.store;service=self.service;directory=service._tenant_sessions_dir()
        plan=await observe_continuation_plan(self,row,summary)
        if not plan['can_continue'] or plan['blockers']:return dict(plan=plan)
        await self._execution_ready(SimpleNamespace(agent_ref=AgentVersionReference.model_validate(summary['agent_ref'])))
        root=await read_execution_log(directory,row,service.tenant_id);events=list(root.events_after(0))
        if len(plan['journals'])!=1 or journal_basis(root)!=plan['journals'][0]:raise_code('PERS-202',reason='continuation_journal_changed')
        if plan['state'] is None:raise_code('GRD-403',reason='continuation_state_unavailable')
        fact=next(e for e in events if e.seq==plan['state']['state_source_seq'])
        cp=next(e for e in events if e.seq==fact.payload['checkpoint_seq']);body=read_state_snapshot(service.platform,fact,cp)
        from pyharness.application.continuation_ancestry import read_continuation_ancestors
        ancestors=await read_continuation_ancestors(self,row)
        if [a['journal'] for a in ancestors]!=plan['lineage_journals']:raise_code('PERS-202',reason='continuation_lineage_changed')
        journals=[*plan['journals'],*plan['lineage_journals']]
        states=[dict(fact=fact,checkpoint=cp,body=body),*ancestors]
        files={};total=0
        for journal in journals:
            for path in session_data_paths(safe_path(directory,journal['session_id']+'.jsonl')):
                raw,_=state_regular(safe_path(directory,path.name),32*1048576);total+=len(raw)
                if total>64*1048576 or len(files)>=256:raise_code('EVT-100',reason='continuation_journal_limit')
                files[path]=digest_of(raw)
        actor=self._authorize(write=True);resources,_,_=await store._execution_read_state();started=time.monotonic()
        origin=store._execution_attempt(resources,summary['project_id'],row['execution_authorization']['selection']['attempt_ref'],actor)
        materials=captured_materials(store,resources,origin,actor,lambda:self._authorize(write=True),started)
        if materials['materials_digest']!=body.materials_digest:raise_code('PERS-202',reason='continuation_materials_binding')
        corrections=[correction_ref(r) for r in row.get('_trusted_corrections',[])]
        def verify():
            for path,digest in files.items():
                raw,_=state_regular(safe_path(directory,path.name),32*1048576)
                if digest_of(raw)!=digest:raise_code('PERS-202',reason='continuation_journal_changed')
            for state in states:
                actual=read_state_snapshot(service.platform,state['fact'],state['checkpoint'])
                if actual!=state['body']:raise_code('PERS-202',reason='continuation_state_changed')
            current,_=store._project(store._strict_control_events())
            target=store._cancel_target(current,summary['project_id'],plan['execution_ref'],self._authorize(write=True))
            store._execution_summary(current,target,actor,lambda:self._authorize(write=True),time.monotonic())
            if [correction_ref(r) for r in store._correction_history(current,target)]!=corrections:
                raise_code('EVT-101',reason='continuation_correction_changed')
            for ancestor in ancestors:
                reference={k:ancestor['summary'][k] for k in ('execution_id','revision','digest','source_seq')}
                target=store._cancel_target(current,summary['project_id'],reference,actor)
                store._execution_summary(current,target,actor,lambda:self._authorize(write=True),time.monotonic())
        def acquire():
            leases=[]
            try:
                for journal in sorted(journals,key=lambda j:j['session_id']):
                    path=session_lock_path(safe_path(directory,journal['session_id']+'.jsonl'))
                    if session_lock_held(path):raise_code('GRD-403',reason='continuation_journal_writer_active')
                    lease=_FileLock(path)
                    if not lease.try_acquire():raise_code('PERS-202',reason='continuation_journal_lease_unavailable')
                    leases.append(lease)
                return leases
            except BaseException:
                for lease in reversed(leases):lease.release()
                raise
        verify()
        return dict(plan=plan,body=body,events=events,replay_events=[*events,*(e for a in ancestors for e in a['events'])],
            old_materials=materials,row=row,verify=verify,acquire=acquire)

    @admitted
    async def continue_execution(self,project,ident,body,command_id):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        command=ContinueExecution.model_validate(body);key=self._workspace_id(command_id,SubmissionKey)
        if command.execution_ref.execution_id!=ident:raise_code('EVT-101',reason='continuation_execution_reference')
        describe=lambda ref:describe_execution(self,AgentVersionReference.model_validate(ref))
        receipt,first,materials,context=await self.store.authorize_continuation(project,command,command_id=key,
            authorize=lambda:self._authorize(write=True),inspect=self._continuation_context,describe=describe)
        if first:
            pending=self.service.__dict__.setdefault('_continuation_launches',{})
            pending[receipt['execution_id']]=context
            try:await self._launch_product_execution(receipt,materials)
            finally:
                pending.pop(receipt['execution_id'],None)
                for lease in reversed(context['leases']):lease.release()
        return await self.execution(project,receipt['execution_id'])

    @admitted
    async def continuation_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.continuation_submission(project,key,authorize=lambda:self._authorize(write=True))
