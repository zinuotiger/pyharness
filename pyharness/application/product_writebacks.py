"""Authenticated writeback facade; no new runtime, database or host fallback."""
from typing import Annotated
from pydantic import Field
from pyharness.core.lifecycle import admitted
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.writeback_contracts import (
    WriteGrantId,MergeExportId,IssueWriteGrant,RevokeWriteGrant,MergePreviewCommand,SaveMergePlan,ApplyMergePlan,AuthorizeMergeExport)
from pyharness.application.writeback_records import WRITEBACK_MODELS
from pyharness.errors import raise_code


class ProductWritebacks:
    @admitted
    async def writeback_source_options(self,project,ident):
        from pyharness.application.delivery_contracts import PackageId
        self._authorize(write=True)
        return await self.store.writeback_source_options(self._workspace_id(project,ProjectId),self._workspace_id(ident,PackageId),
            authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def issue_writeback_grant(self,project,body,command_id):
        self._authorize(grant_write=True)
        return await self.store.issue_writeback_grant(self._workspace_id(project,ProjectId),IssueWriteGrant.model_validate(body),
            command_id=self._workspace_id(command_id,SubmissionKey),authorize=lambda:self._authorize(grant_write=True),service=self.service)

    @admitted
    async def revoke_writeback_grant(self,project,ident,body,command_id):
        self._authorize(grant_write=True)
        return await self.store.revoke_writeback_grant(self._workspace_id(project,ProjectId),self._workspace_id(ident,WriteGrantId),
            RevokeWriteGrant.model_validate(body),command_id=self._workspace_id(command_id,SubmissionKey),
            authorize=lambda:self._authorize(grant_write=True),service=self.service)

    @admitted
    async def preview_writeback(self,project,body):
        self._authorize(write=True)
        return await self.store.preview_writeback(self._workspace_id(project,ProjectId),MergePreviewCommand.model_validate(body),
            authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def save_merge_plan(self,project,body,command_id):
        self._authorize(write=True)
        return await self.store.save_merge_plan(self._workspace_id(project,ProjectId),SaveMergePlan.model_validate(body),
            command_id=self._workspace_id(command_id,SubmissionKey),authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def apply_merge_plan(self,project,body,command_id):
        self._authorize(write=True)
        return await self.store.apply_merge_plan(self._workspace_id(project,ProjectId),ApplyMergePlan.model_validate(body),
            command_id=self._workspace_id(command_id,SubmissionKey),authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def authorize_merge_export(self,project,body,command_id):
        self._authorize(write=True)
        return await self.store.authorize_merge_export(self._workspace_id(project,ProjectId),AuthorizeMergeExport.model_validate(body),
            command_id=self._workspace_id(command_id,SubmissionKey),authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def read_writeback(self,project,kind,ident):
        self._authorize(write=True)
        if kind not in WRITEBACK_MODELS:raise_code('EVT-100',reason='writeback_kind')
        prefix=WRITEBACK_MODELS[kind][2];value=Annotated[str,Field(pattern=r'^'+prefix+r'-[0-9a-f]{16}$')]
        return await self.store.read_writeback(self._workspace_id(project,ProjectId),kind,self._workspace_id(ident,value),
            authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def merge_export_bytes(self,project,ident,index):
        self._authorize(write=True)
        return await self.store.merge_export_bytes(self._workspace_id(project,ProjectId),self._workspace_id(ident,MergeExportId),index,
            authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def writeback_center(self,project):
        self._authorize(write=True)
        return await self.store.writeback_center(self._workspace_id(project,ProjectId),authorize=lambda:self._authorize(write=True))

    @admitted
    async def writeback_submission(self,project,key):
        self._authorize(write=True)
        return await self.store.writeback_submission(self._workspace_id(project,ProjectId),self._workspace_id(key,SubmissionKey),authorize=lambda:self._authorize(write=True))
