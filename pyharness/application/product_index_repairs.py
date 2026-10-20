"""Exact private journal leases and only the original FTS rebuild operation."""
from pathlib import Path
from types import SimpleNamespace
from pyharness.application.execution_contracts import ExecutionId
from pyharness.application.index_repair_contracts import RepairIndex
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.workspace_store import digest_of,canonical
from pyharness.application.execution_recovery_plan import observe_recovery_plan,index_observation,index_scope_path,journal_basis
from pyharness.application.execution_child_reader import read_child_log
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.core.lifecycle import admitted
from pyharness.core.sandbox import safe_path,read_regular
from pyharness.errors import raise_code
from pyharness.persistence import _FileLock,session_data_paths,session_lock_path,session_lock_held


def index_path(service):return index_scope_path(service)


class ProductIndexRepairs:
    async def _index_repair_context(self,row,summary):
        service=self.service;plan=await observe_recovery_plan(self,row,summary)
        directory=service._tenant_sessions_dir();path=index_path(service);scope=digest_of(canonical(str(path)))
        if plan['index_observation']['scope_digest']!=scope:raise_code('GRD-403',reason='index_repair_scope_changed')
        files={};total=0
        for basis in plan['journals']:
            main=safe_path(directory,basis['session_id']+'.jsonl')
            for item in session_data_paths(main):
                item=safe_path(directory,item.name);raw=read_regular(item,32*1048576);total+=len(raw)
                if total>64*1048576 or len(files)>=256:raise_code('EVT-100',reason='index_repair_journal_limit')
                files[item]=digest_of(raw)
        root=await read_execution_log(directory,row,service.tenant_id);events=list(root.events_after(0))
        for basis in plan['journals']:
            log=root if basis['session_id']==root.sid else await read_child_log(directory,row,service.tenant_id,basis['session_id'])
            if journal_basis(log)!=basis:raise_code('PERS-202',reason='index_repair_plan_journal_changed')
        def verify():
            if digest_of(canonical(str(index_path(service))))!=scope:raise_code('GRD-403',reason='index_repair_scope_changed')
            for item,digest in files.items():
                if digest_of(read_regular(item,32*1048576))!=digest:raise_code('PERS-202',reason='index_repair_journal_changed')
        def acquire():
            leases=[]
            try:
                for basis in sorted(plan['journals'],key=lambda b:b['session_id']):
                    lock_path=session_lock_path(safe_path(directory,basis['session_id']+'.jsonl'))
                    if session_lock_held(lock_path):raise_code('GRD-403',reason='index_repair_journal_writer_active')
                    lease=_FileLock(lock_path)
                    if not lease.try_acquire():raise_code('PERS-202',reason='index_repair_journal_lease_unavailable')
                    leases.append(lease)
                return leases
            except BaseException:
                for lease in reversed(leases):lease.release()
                raise
        verify()
        return dict(plan=plan,path=path,scope_digest=scope,files=files,events=events,verify=verify,acquire=acquire,
            observe=lambda:index_observation(service,events))

    async def _perform_index_repair(self,context):
        from pyharness.core.session_query import SessionQueryIndex
        context['verify']();index=SessionQueryIndex(db_path=context['path'])
        try:
            await index.enter()
            sid=context['events'][0].session_id
            index.attach_source(sid,SimpleNamespace(replay=lambda:iter(context['events'])))
            await index.rebuild(session_id=sid)
        finally:await index.detach()
        context['verify']();return context['observe']()

    @admitted
    async def repair_execution_index(self,project,ident,body,command_id):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        command=RepairIndex.model_validate(body);key=self._workspace_id(command_id,SubmissionKey)
        if command.execution_ref.execution_id!=ident:raise_code('EVT-101',reason='index_repair_execution_reference')
        return await self.store.apply_index_repair(project,command,command_id=key,authorize=lambda:self._authorize(write=True),
            inspect=self._index_repair_context,perform=self._perform_index_repair)

    @admitted
    async def index_repair_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.index_repair_submission(project,key,authorize=lambda:self._authorize(write=True))

    @admitted
    async def index_repairs(self,project,ident):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        return await self.store.index_repairs(project,ident,authorize=lambda:self._authorize(write=True))
