"""Delivery operations retain the authenticated Projects principal."""
from pyharness.core.lifecycle import admitted
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.execution_contracts import ExecutionId
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.delivery_contracts import DeliveryPackageCommand,PackageId


class ProductDeliveries:
    @admitted
    async def delivery_submission(self,project,key):
        self._authorize(write=True)
        return await self.store.delivery_submission(self._workspace_id(project,ProjectId),self._workspace_id(key,SubmissionKey),authorize=lambda:self._authorize(write=True))

    @admitted
    async def delivery_center(self,project):
        self._authorize(write=True)
        return await self.store.delivery_center(self._workspace_id(project,ProjectId),authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def handoff_document_import(self,project,body,command_id=None,*,preview=False):
        from pyharness.application.handoff_document_contracts import HandoffDocumentCommand
        self._authorize(write=True)
        return await self.store.handoff_document_import(self._workspace_id(project,ProjectId),
            HandoffDocumentCommand.model_validate(body),command_id=None if preview else self._workspace_id(command_id,SubmissionKey),
            authorize=lambda:self._authorize(write=True),service=self.service,preview=preview)

    @admitted
    async def save_role_delivery(self,project,kind,body,command_id):
        from pyharness.application.delivery_records import DELIVERY_MODELS
        from pyharness.errors import raise_code
        self._authorize(write=True)
        if kind not in DELIVERY_MODELS or kind=='delivery_package':raise_code('EVT-100',reason='delivery_kind_invalid')
        command=DELIVERY_MODELS[kind][1].model_validate(body)
        return await self.store.save_role_delivery(self._workspace_id(project,ProjectId),kind,command,
            command_id=self._workspace_id(command_id,SubmissionKey),authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def role_delivery(self,project,kind,ident):
        from pyharness.application.delivery_records import DELIVERY_MODELS
        from pyharness.errors import raise_code
        from typing import Annotated
        from pydantic import Field
        self._authorize(write=True)
        if kind not in DELIVERY_MODELS or kind=='delivery_package':raise_code('EVT-100',reason='delivery_kind_invalid')
        prefix=DELIVERY_MODELS[kind][3]
        ident=self._workspace_id(ident,Annotated[str,Field(pattern=r'^'+prefix+r'-[0-9a-f]{16}$')])
        return await self.store.role_delivery(self._workspace_id(project,ProjectId),kind,ident,
            authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def delivery_export_bytes(self,project,ident,index):
        from pyharness.application.delivery_contracts import ExportId
        self._authorize(write=True)
        return await self.store.delivery_export_bytes(self._workspace_id(project,ProjectId),
            self._workspace_id(ident,ExportId),index,authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def delivery_outputs(self,project,ident):
        self._authorize(write=True)
        return await self.store.delivery_outputs(self._workspace_id(project,ProjectId),
            self._workspace_id(ident,ExecutionId),authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def save_delivery_package(self,project,body,command_id):
        self._authorize(write=True)
        return await self.store.save_delivery_package(self._workspace_id(project,ProjectId),
            DeliveryPackageCommand.model_validate(body),command_id=self._workspace_id(command_id,SubmissionKey),
            authorize=lambda:self._authorize(write=True),service=self.service)

    @admitted
    async def delivery_package(self,project,ident):
        self._authorize(write=True)
        return await self.store.delivery_package(self._workspace_id(project,ProjectId),
            self._workspace_id(ident,PackageId),authorize=lambda:self._authorize(write=True),service=self.service)
