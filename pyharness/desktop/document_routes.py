"""Authenticated S05 routes: metadata and text JSON only, never active PDF/HTML."""
from fastapi import Body, Header, Request


def mount_documents(router, desktop):
    from pyharness.application.workspace_input_pages import parse_workspace_query
    def projects():
        return desktop.service.projects

    @router.get('/projects/{project_id}/documents')
    async def documents(project_id: str, request: Request):
        return await projects().documents(project_id, query=parse_workspace_query(list(request.query_params.multi_items())))

    @router.post('/projects/{project_id}/documents')
    async def save_document(project_id: str, body: dict = Body(...), key: str = Header(..., alias='Idempotency-Key')):
        return await projects().documents(project_id, body=body, command_id=key)

    @router.post('/projects/{project_id}/document-pdf-previews')
    async def pdf_preview(project_id: str, body: dict = Body(...)):
        return await projects().document_pdf_preview(project_id, body)

    @router.get('/projects/{project_id}/documents/{document_id}/revisions/{revision}')
    async def document(project_id: str, document_id: str, revision: int):
        return await projects().document(project_id, document_id, revision)

    @router.get('/projects/{project_id}/citations')
    async def citations(project_id: str, request: Request):
        return await projects().document_references(project_id, 'citation', query=parse_workspace_query(list(request.query_params.multi_items())))

    @router.post('/projects/{project_id}/citations')
    async def save_citation(project_id: str, body: dict = Body(...), key: str = Header(..., alias='Idempotency-Key')):
        return await projects().document_references(project_id, 'citation', body=body, command_id=key)

    @router.get('/projects/{project_id}/citations/{citation_id}')
    async def citation(project_id: str, citation_id: str):
        return await projects().document_reference(project_id, 'citation', citation_id, 1)

    @router.get('/projects/{project_id}/delivery-definitions')
    async def deliveries(project_id: str, request: Request):
        return await projects().document_references(project_id, 'delivery_definition', query=parse_workspace_query(list(request.query_params.multi_items())))

    @router.post('/projects/{project_id}/delivery-definitions')
    async def save_delivery(project_id: str, body: dict = Body(...), key: str = Header(..., alias='Idempotency-Key')):
        return await projects().document_references(project_id, 'delivery_definition', body=body, command_id=key)

    @router.get('/projects/{project_id}/delivery-definitions/{delivery_id}/revisions/{revision}')
    async def delivery(project_id: str, delivery_id: str, revision: int):
        return await projects().document_reference(project_id, 'delivery_definition', delivery_id, revision)
