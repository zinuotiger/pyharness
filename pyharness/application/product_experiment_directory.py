"""Metadata recovery never implicitly retries a command."""
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.experiment_plan import experiment_ref
from pyharness.core.lifecycle import admitted
from pyharness.errors import PyHError


class ProductExperimentDirectory:
    @admitted
    async def experiment_directory(self,project,kind):
        project=self._experiment_scope(project)
        return await self.store.experiment_directory(project,kind,authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def experiment_command_submission(self,project,key):
        project=self._experiment_scope(project);key=self._workspace_id(key,SubmissionKey)
        row,kind,reopen=await self.store.experiment_command_record(project,key,authorize=lambda:self._authorize(write=True))
        receipt=None;status='committed' if row else 'not_found';control_only=False;selection=None
        if kind=='experiment_preparation':
            old=await self.experiment_submission(project,key);status=old['status'];receipt=old['receipt'];selection=old['preparation']['selection']
        elif kind=='experiment_run':
            try:receipt=await self.experiment_run(project,row[kind]['experiment_run_id'])
            except PyHError as exc:
                if exc.code!='GRD-403':raise
                receipt=await self.experiment_run_control(project,row[kind]['experiment_run_id']);control_only=True
        elif kind=='experiment_report':receipt=await self.experiment_report(project,row[kind]['report_id'])
        elif kind=='experiment_cancellation':
            value=row[kind]
            control=await self.experiment_run_control(project,value['experiment_run_ref']['experiment_run_id'])
            receipt=dict(schema_version=1,project_id=project,cancellation_id=value['cancellation_id'],experiment_run_ref=value['experiment_run_ref'],
                digest=row['digest'],source_seq=row['source_seq'],persisted=True,cancel_requested=control['cancel_requested'],automatic_dispatch=False)
        self._authorize(write=True)
        return dict(schema_version=1,project_id=project,submission_key=key,status=status,resource_kind=kind,receipt=receipt,
            selection=selection,control_only=control_only,writer_requires_reopen=reopen,automatic_dispatch=False)
