"""Explicit preparation through original task and graph commands; no dispatch here."""
import asyncio
from pyharness.application.experiment_contracts import PrepareExperiment,ConfirmExperimentPreparation,ExperimentId
from pyharness.application.experiment_support import graph_command
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.workspace_contracts import ProjectId
from pyharness.core.lifecycle import admitted
from pyharness.errors import raise_code
from pyharness.application.product_experiment_runs import ProductExperimentRuns
from pyharness.application.product_experiment_reports import ProductExperimentReports
from pyharness.application.product_experiment_directory import ProductExperimentDirectory


def fixed_ref(value,key):return {k:value[k] for k in (key,'revision','digest','source_seq')}


class ProductExperiments(ProductExperimentRuns,ProductExperimentReports,ProductExperimentDirectory):
    @admitted
    async def experiment_preparation_preview(self,project,body):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        selected=PrepareExperiment.model_validate(body)
        return await self.store.experiment_preparation_preview(project,selected,authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def prepare_experiment(self,project,body,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        selected=ConfirmExperimentPreparation.model_validate(body)
        lock=self.service.__dict__.setdefault('_experiment_preparation_lock',asyncio.Lock())
        async with lock:
            authorize=lambda:self._authorize(write=True)
            old=await self.store.experiment_submission(project,key,authorize=authorize,projects=self)
            if old['preparation'] is not None and old['preparation']['selection']!=selected.model_dump():
                raise_code('EVT-100',reason='idempotency_conflict')
            if old['status']=='committed':return old['receipt']
            if old['writer_requires_reopen']:raise_code('PERS-202',reason='workspace_reopen_required')
            plan=await self.store.experiment_preparation_preview(project,selected,authorize=authorize,projects=self)
            if plan['confirmation_digest']!=selected.confirmation_digest:raise_code('EVT-101',reason='experiment_plan_changed')
            preparation=await self.store.ensure_experiment_preparation(project,selected,command_id=key,authorize=authorize,projects=self)
            task_refs={}
            for index,item in enumerate(plan['tasks']):
                await self.store.experiment_preparation_preview(project,selected,authorize=authorize,projects=self)
                ref=await self.store.experiment_existing_task(project,item['command'],authorize=authorize)
                if ref is None:
                    task=await self.product_tasks(project,item['command'],'ep-task-'+preparation['preparation_id']+'-'+str(index))
                    ref=fixed_ref(task,'product_task_id')
                task_refs[item['command']['product_task_id']]=ref
            await self.store.experiment_preparation_preview(project,selected,authorize=authorize,projects=self)
            command=graph_command(selected,plan,task_refs)
            graph=await self.task_graphs(project,body=command.model_dump(),command_id='ep-graph-'+preparation['preparation_id'])
            bindings=[dict(case_ref=item['case_ref'],task_ref=task_refs[item['command']['product_task_id']],
                left_node_id='c'+str(index)+'_left',right_node_id='c'+str(index)+'_right') for index,item in enumerate(plan['tasks'])]
            return await self.store.finish_experiment(project,selected,preparation,fixed_ref(graph,'graph_id'),bindings,
                plan['dataset_total'],authorize=authorize,projects=self)

    @admitted
    async def experiment(self,project,ident,revision=None):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExperimentId)
        if revision is not None and (type(revision) is not int or not 1<=revision<=200):raise_code('EVT-100',reason='experiment_revision')
        return await self.store.experiment_record(project,ident,revision,authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def experiment_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.experiment_submission(project,key,authorize=lambda:self._authorize(write=True),projects=self)
