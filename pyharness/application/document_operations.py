"""Admitted document operations use the Projects identity/role boundary."""
from pyharness.core.lifecycle import admitted
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.document_contracts import (
    DocumentId, CitationId, DeliveryId, DocumentCommand, PdfPreview, CitationCommand, DeliveryCommand)
from pyharness.errors import raise_code


class DocumentOperations:
    @admitted
    async def documents(self, project_id, *, query=None, body=None, command_id=None):
        from pyharness.application.workspace_input_pages import WorkspaceInputQuery
        self._authorize(write=True)
        project = self._workspace_id(project_id, ProjectId)
        authorize = lambda: self._authorize(write=True)
        if body is not None:
            return await self.store.save_document(project, DocumentCommand.model_validate(body), command_id=command_id, authorize=authorize)
        return await self.store.documents(project, query or WorkspaceInputQuery(), authorize=authorize)

    @admitted
    async def document_pdf_preview(self, project_id, body):
        self._authorize(write=True)
        return await self.store.document_pdf_preview(self._workspace_id(project_id, ProjectId),
            PdfPreview.model_validate(body), authorize=lambda: self._authorize(write=True))

    @admitted
    async def document(self, project_id, document_id, revision):
        self._authorize(write=True)
        if type(revision) is not int or not 1 <= revision <= 200:
            raise_code('EVT-100', reason='document_revision_invalid')
        return await self.store.document(self._workspace_id(project_id, ProjectId),
            self._workspace_id(document_id, DocumentId), revision, authorize=lambda: self._authorize(write=True))

    @admitted
    async def document_references(self, project_id, kind, *, query=None, body=None, command_id=None):
        from pyharness.application.workspace_input_pages import WorkspaceInputQuery
        self._authorize(write=True)
        if kind not in {'citation', 'delivery_definition'}:
            raise_code('EVT-100', reason='document_reference_kind_invalid')
        project = self._workspace_id(project_id, ProjectId)
        authorize = lambda: self._authorize(write=True)
        if body is not None:
            model = CitationCommand if kind == 'citation' else DeliveryCommand
            return await self.store.save_document_reference(project, kind, model.model_validate(body), command_id=command_id, authorize=authorize)
        return await self.store.document_references(project, kind, query or WorkspaceInputQuery(), authorize=authorize)

    @admitted
    async def document_reference(self, project_id, kind, ident, revision):
        self._authorize(write=True)
        if kind not in {'citation', 'delivery_definition'} or type(revision) is not int or not 1 <= revision <= (1 if kind == 'citation' else 200):
            raise_code('EVT-100', reason='document_reference_revision_invalid')
        model = CitationId if kind == 'citation' else DeliveryId
        return await self.store.document_reference(self._workspace_id(project_id, ProjectId), kind,
            self._workspace_id(ident, model), revision, authorize=lambda: self._authorize(write=True))
