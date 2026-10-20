"""Explicit experiment admission delegates to the existing graph lifecycle."""
import asyncio
from pyharness.application.experiment_run_contracts import (PreviewExperimentRun,StartExperimentRun,
    ResumeExperimentRun,ConfirmExperimentResumption,CancelExperimentRun,ExperimentRunId)
from pyharness.application.experiment_run_binding import graph_identity
from pyharness.application.experiment_plan import experiment_ref
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.workspace_contracts import ProjectId
from pyharness.core.lifecycle import admitted
from pyharness.errors import PyHError,raise_code


class ProductExperimentRuns:
    def _experiment_lock(self):return self.service.__dict__.setdefault('_experiment_run_admission_lock',asyncio.Lock())

    def _experiment_scope(self,project):
        self._authorize(write=True);return self._workspace_id(project,ProjectId)

    @admitted
    async def experiment_run_preview(self,project,body):
        project=self._experiment_scope(project);command=PreviewExperimentRun.model_validate(body)
        return await self.store.experiment_run_preview(project,command,authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def start_experiment_run(self,project,body,key):
        project=self._experiment_scope(project);key=self._workspace_id(key,SubmissionKey);command=StartExperimentRun.model_validate(body)
        async with self._experiment_lock():
            ref,first=await self.store.register_experiment_run(project,command,key,authorize=lambda:self._authorize(write=True),projects=self)
            if first:
                row,_,_=await self.store.experiment_run_record(project,command.experiment_run_id,authorize=lambda:self._authorize(write=True),projects=self)
                value=row['experiment_run'];_,original_key=graph_identity(self.service.tenant_id,value['created_by'],project,command.experiment_run_id)
                await self.start_task_graph(project,value['graph_command'],original_key)
            return await self.experiment_run(project,command.experiment_run_id)

    @admitted
    async def experiment_run(self,project,ident):
        project=self._experiment_scope(project);ident=self._workspace_id(ident,ExperimentRunId)
        row,graph,cancelled=await self.store.experiment_run_record(project,ident,authorize=lambda:self._authorize(write=True),projects=self)
        value=row['experiment_run'];result=dict(value,digest=row['digest'],source_seq=row['source_seq'],created_at=row['created_at'],
            persisted=True,automatic_dispatch=False,cancel_requested=cancelled,business_acceptance='not_evaluated',
            status='cancelled' if cancelled else 'registered_not_dispatched',nodes=[],graph_run_ref=None)
        result.pop('created_by')
        from pyharness.application.workspace_store import canonical
        result['record_canonical']=canonical(value).decode('utf-8');result['request_digest']=row['request_digest']
        if graph:
            observation=await self.task_graph_run(project,graph['task_graph_run']['graph_run_id'])
            result.update(status=observation['status'],nodes=observation['nodes'],scheduler_active=observation['scheduler_active'],
                graph_run_ref=self.store._s05_ref(graph,'graph_run_id','task_graph_run'),runtime_error=observation['runtime_error'])
            result['cancel_requested']=cancelled or observation['cancel_requested']
            if cancelled and result['status']!='completed':result['status']='cancelling' if observation['scheduler_active'] else 'cancelled'
        return result

    @admitted
    async def experiment_resume_preview(self,project,body):
        project=self._experiment_scope(project);command=ResumeExperimentRun.model_validate(body)
        preview,_=await self.store.experiment_resume_preview(project,command.experiment_run_ref,authorize=lambda:self._authorize(write=True),projects=self)
        return preview

    @admitted
    async def resume_experiment_run(self,project,body,key):
        project=self._experiment_scope(project);self._workspace_id(key,SubmissionKey);command=ConfirmExperimentResumption.model_validate(body)
        async with self._experiment_lock():
            preview,value=await self.store.experiment_resume_preview(project,command.experiment_run_ref,authorize=lambda:self._authorize(write=True),projects=self)
            if preview['confirmation_digest']!=command.confirmation_digest:raise_code('EVT-101',reason='experiment_resume_preview_changed')
            if preview['status']=='confirmation_required':
                _,original_key=graph_identity(self.service.tenant_id,value['created_by'],project,value['experiment_run_id'])
                await self.start_task_graph(project,value['graph_command'],original_key)
            return await self.experiment_run(project,value['experiment_run_id'])

    @admitted
    async def experiment_run_control(self,project,ident):
        project=self._experiment_scope(project);ident=self._workspace_id(ident,ExperimentRunId)
        return await self.store.experiment_run_control(project,ident,authorize=lambda:self._authorize(write=True))

    @admitted
    async def cancel_experiment_run(self,project,body,key):
        project=self._experiment_scope(project);key=self._workspace_id(key,SubmissionKey);command=CancelExperimentRun.model_validate(body)
        async with self._experiment_lock():
            try:receipt,row,graph=await self.store.cancel_experiment_run(project,command,key,authorize=lambda:self._authorize(write=True))
            except PyHError as exc:
                if exc.code=='PERS-202':
                    try:await self._reduce_failed_experiment_stop(project,command.experiment_run_ref)
                    except PyHError:pass
                raise
            if graph:
                ref=self.store._s05_ref(graph,'graph_run_id','task_graph_run')
                control=await self.task_graph_run_control(project,ref['graph_run_id'])
                if not control['cancel_requested']:
                    await self.cancel_task_graph(project,dict(schema_version=1,cancellation_id='gc-'+command.cancellation_id[4:],graph_run_ref=ref),
                        'experiment-cancel-'+command.cancellation_id)
            return dict(schema_version=1,project_id=project,cancellation_id=command.cancellation_id,
                experiment_run_ref=experiment_ref(row,'experiment_run'),digest=receipt['digest'],source_seq=receipt['source_seq'],
                persisted=True,cancel_requested=True,execution_stopped=None,automatic_dispatch=False)

    async def _reduce_failed_experiment_stop(self,project,reference):
        control=await self.store.experiment_run_control(project,reference.experiment_run_id,authorize=lambda:self._authorize(write=True))
        if control['experiment_run_ref']!=reference.model_dump() or control['graph_run_ref'] is None:return
        graph_ref=control['graph_run_ref'];owner=getattr(self.service,'_task_graph_owners',{}).get(graph_ref['graph_run_id'])
        if owner is None or owner.reference!=graph_ref or owner.actor!=self._authorize(write=True) or owner.task is None:return
        if not owner.task.done():
            owner.task.cancel();await asyncio.gather(owner.task,return_exceptions=True)
        # A durable intent may already have closed the scheduler while its queue
        # stop failed. Re-signal only exact owned targets through the original
        # permission-reducing routine; never claim the failed receipt committed.
        await owner.stop_children()
