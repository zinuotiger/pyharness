"""Regression data commands on the original authenticated project surface."""
import time
from typing import Annotated
from pydantic import Field
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.regression_contracts import PreviewFailure,SaveCase,SaveDataset,CaseId,DatasetId
from pyharness.application.regression_candidates import failure_candidate_view
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.errors import raise_code
from pyharness.core.lifecycle import admitted


class ProductRegressions:
    @admitted
    async def failure_candidate(self,project,body):
        actor=self._authorize(write=True);project=self._workspace_id(project,ProjectId);started=time.monotonic()
        command=PreviewFailure.model_validate(body)
        async def read(row,summary):
            value=await failure_candidate_view(self,row,summary)
            self.store._regression_fresh_source(project,value['origin'],actor,lambda:self._authorize(write=True),started)
            return value
        return await self.store.observe_execution(project,command.execution_id,
            authorize=lambda:self._authorize(write=True),read=read)

    @admitted
    async def save_regression_case(self,project,body,command_id):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        return await self.store.save_regression(project,SaveCase.model_validate(body),'regression_case',
            command_id=command_id,authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def save_regression_dataset(self,project,body,command_id):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        return await self.store.save_regression(project,SaveDataset.model_validate(body),'regression_dataset',
            command_id=command_id,authorize=lambda:self._authorize(write=True),projects=self)

    async def _read_regression(self,project,ident,revision,kind):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        ident=self._workspace_id(ident,CaseId if kind=='regression_case' else DatasetId)
        if revision is not None:revision=self._workspace_id(revision,Annotated[int,Field(ge=1,le=200)])
        return await self.store.regression_record(project,ident,revision,kind,
            authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def regression_case(self,project,ident,revision=None):
        return await self._read_regression(project,ident,revision,'regression_case')

    @admitted
    async def regression_dataset(self,project,ident,revision=None):
        return await self._read_regression(project,ident,revision,'regression_dataset')

    @admitted
    async def regression_cases(self,project):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        return await self.store.regression_records(project,'regression_case',authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def regression_datasets(self,project):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        return await self.store.regression_records(project,'regression_dataset',authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def regression_export_preview(self,project,ident,body):
        from pyharness.application.regression_transfer_contracts import DatasetExportPreview
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,DatasetId)
        return await self.store.regression_export(project,ident,DatasetExportPreview.model_validate(body),
            authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def regression_export(self,project,ident,body):
        from pyharness.application.regression_transfer_contracts import ExportDataset
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,DatasetId)
        return await self.store.regression_export(project,ident,ExportDataset.model_validate(body),
            authorize=lambda:self._authorize(write=True),projects=self,confirmed=True)

    @admitted
    async def regression_import_preview(self,project,body):
        from pyharness.application.regression_transfer_contracts import DatasetImportPreview
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        preview,_=await self.store.regression_import_basis(project,DatasetImportPreview.model_validate(body),
            authorize=lambda:self._authorize(write=True),projects=self)
        return preview

    @admitted
    async def import_regression_dataset(self,project,body,command_id):
        from pyharness.application.regression_transfer_contracts import ImportDataset
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);command=ImportDataset.model_validate(body)
        preview,selection=await self.store.regression_import_basis(project,command,
            authorize=lambda:self._authorize(write=True),projects=self,for_commit=True)
        if preview['confirmation_digest']!=command.confirmation_digest:
            raise_code('EVT-101',reason='regression_import_changed')
        return await self.store.save_regression(project,selection,'regression_dataset',command_id=command_id,
            authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def regression_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.regression_submission(project,key,authorize=lambda:self._authorize(write=True),projects=self)
