"""Thin authenticated adapter for product-control metadata."""
import re
import inspect
from fastapi import APIRouter, Body, Depends, Header, Request
from fastapi.responses import JSONResponse,Response
from pydantic import ValidationError
from pyharness.desktop.platform_api import PlatformRoute
from pyharness.errors import PyHError
from pyharness.errors import raise_code


def mount_workspace(desktop):
    class WorkspaceRoute(PlatformRoute):
        @property
        def service_owner(self):
            return desktop.service

        def get_route_handler(self):
            handler = super().get_route_handler()
            async def checked(request):
                try:
                    return await handler(request)
                except PyHError as exc:
                    status = {'CRED-701': 401, 'GRD-403': 403, 'PERS-202': 503, 'EVT-104': 409, 'EVT-101': 409}.get(exc.code, 422)
                    reason = exc.ctx.get('reason', '')
                    if reason == 'idempotency_conflict':
                        status = 409
                    return JSONResponse({'code': exc.code, 'message': '项目命令未被接受', 'reason': reason}, status_code=status)
                except ValidationError:
                    return JSONResponse({'code': 'EVT-100', 'message': '项目字段不合法'}, status_code=422)
            async def private_directory(request):
                if request.method!='GET' or request.url.path!='/api/v1/projects':return await checked(request)
                try:response=await checked(request)
                except Exception as exc:
                    handler=next((desktop.api.exception_handlers[t] for t in type(exc).__mro__ if t in desktop.api.exception_handlers),None)
                    if handler is None:raise
                    response=handler(request,exc)
                    if inspect.isawaitable(response):response=await response
                response.headers['Cache-Control']='no-store'
                return response
            return private_directory

    router = APIRouter(prefix='/api/v1', route_class=WorkspaceRoute,
                       dependencies=[Depends(desktop._require_api_auth)])

    @router.get('/projects')
    async def projects():
        return await desktop.service.projects.list()

    @router.post('/projects')
    async def save_project(body: dict = Body(...), idempotency_key: str = Header(..., alias='Idempotency-Key')):
        return await desktop.service.projects.save(body, idempotency_key)

    @router.get('/projects/{project_id}/source-grants')
    async def source_grants(project_id: str):
        return await desktop.service.projects.list_source_grants(project_id)

    @router.post('/projects/{project_id}/source-grants')
    async def issue_source_grant(project_id: str, body: dict = Body(...),
                                 idempotency_key: str = Header(..., alias='Idempotency-Key')):
        return await desktop.service.projects.issue_source_grant(project_id, body, idempotency_key)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/preflight')
    async def preflight_source_grant(project_id: str, grant_id: str, body: dict = Body(...)):
        return await desktop.service.projects.preflight_source_grant(project_id, grant_id, body)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/selection-previews')
    async def preview_source_selection(project_id: str, grant_id: str, body: dict = Body(...)):
        return await desktop.service.projects.preview_source_selection(project_id, grant_id, body)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/snapshot-context-previews')
    async def preview_snapshot_context(project_id: str, grant_id: str, body: dict = Body(...)):
        return await desktop.service.projects.preview_snapshot_context(project_id, grant_id, body)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/git-source-previews')
    async def preview_git_source(project_id: str, grant_id: str, body: dict = Body(...)):
        return await desktop.service.projects.preview_git_source(project_id, grant_id, body)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/snapshot-previews')
    async def preview_snapshot(project_id: str, grant_id: str, body: dict = Body(...)):
        return await desktop.service.projects.preview_snapshot(project_id, grant_id, body)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/snapshots')
    async def capture_snapshot(project_id: str, grant_id: str, body: dict = Body(...),
                               idempotency_key: str = Header(..., alias='Idempotency-Key')):
        return await desktop.service.projects.capture_snapshot(project_id, grant_id, body, idempotency_key)

    @router.get('/projects/{project_id}/source-grants/{grant_id}/snapshots')
    async def snapshots(project_id: str, grant_id: str):
        return await desktop.service.projects.snapshots(project_id, grant_id)

    @router.post('/source-grants/{grant_id}/revoke')
    async def revoke_source_grant(grant_id: str, body: dict = Body(...),
                                  idempotency_key: str = Header(..., alias='Idempotency-Key')):
        return await desktop.service.projects.revoke_source_grant(grant_id, body, idempotency_key)

    @router.get('/workspace/audit')
    async def audit(request: Request):
        pairs = list(request.query_params.multi_items())
        if not pairs:
            return await desktop.service.projects.audit()
        parameters = dict(pairs)
        if (len(parameters) != len(pairs) or parameters.get('pagination_version') != '1'
                or set(parameters) - {'pagination_version', 'limit', 'cursor', 'project_id'}):
            raise_code('EVT-100', reason='audit_query_invalid')
        for field in ('pagination_version', 'limit'):
            if field in parameters:
                if not re.fullmatch(r'[0-9]{1,3}', parameters[field]):
                    raise_code('EVT-100', reason='audit_query_invalid')
                parameters[field] = int(parameters[field])
        return await desktop.service.projects.audit(**parameters)

    @router.get('/projects/{project_id}/context-task-references')
    async def context_task_references(project_id: str):
        return await desktop.service.projects.context_task_references(project_id)

    @router.post('/projects/{project_id}/context-task-references')
    async def save_context_task_reference(project_id: str, body: dict = Body(...),
                                          idempotency_key: str = Header(..., alias='Idempotency-Key')):
        return await desktop.service.projects.context_task_references(project_id, body, idempotency_key)

    @router.get('/projects/{project_id}/context-task-references/{task_id}/revisions/{revision}')
    async def context_task_reference(project_id: str, task_id: str, revision: int):
        return await desktop.service.projects.context_task_reference(project_id, task_id, revision)

    @router.get('/projects/{project_id}/context-manifests')
    async def context_manifests(project_id: str):
        return await desktop.service.projects.context_manifests(project_id)

    @router.post('/projects/{project_id}/context-manifests')
    async def save_context_manifest(project_id: str, body: dict = Body(...),
                                    idempotency_key: str = Header(..., alias='Idempotency-Key')):
        return await desktop.service.projects.context_manifests(project_id, body, idempotency_key)

    @router.get('/projects/{project_id}/context-manifests/{manifest_id}/revisions/{revision}')
    async def context_manifest(project_id: str, manifest_id: str, revision: int):
        return await desktop.service.projects.context_manifest(project_id, manifest_id, revision)

    @router.get('/projects/{project_id}/context-manifests/{manifest_id}/revisions/{revision}/input-descriptor')
    async def selected_input_descriptor(project_id: str, manifest_id: str, revision: int):
        return await desktop.service.projects.selected_input_descriptor(project_id,manifest_id,revision)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/workspace-snapshot-previews')
    async def preview_workspace_snapshot(project_id: str,grant_id: str,body: dict=Body(...)):
        return await desktop.service.projects.preview_workspace_snapshot(project_id,grant_id,body)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/git-input-candidates')
    async def preview_git_candidates(project_id: str,grant_id: str,body: dict=Body(...)):
        return await desktop.service.projects.preview_git_candidates(project_id,grant_id,body)

    @router.post('/projects/{project_id}/source-grants/{grant_id}/workspace-snapshots')
    async def capture_workspace_snapshot(project_id: str,grant_id: str,body: dict=Body(...),
                                         idempotency_key: str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.capture_workspace_snapshot(project_id,grant_id,body,idempotency_key)

    @router.get('/projects/{project_id}/workspace-snapshots')
    async def workspace_snapshots(project_id: str,request: Request):
        from pyharness.application.workspace_input_pages import parse_workspace_query
        return await desktop.service.projects.workspace_snapshots(project_id,parse_workspace_query(list(request.query_params.multi_items())))

    @router.get('/projects/{project_id}/workspace-snapshots/{snapshot_id}')
    async def workspace_snapshot(project_id: str,snapshot_id: str):
        return await desktop.service.projects.workspace_snapshot(project_id,snapshot_id)

    @router.get('/projects/{project_id}/workspace-snapshots/{snapshot_id}/items/{item_index}/layers/{layer}')
    async def workspace_snapshot_file(project_id: str,snapshot_id: str,item_index: int,layer: str):
        raw,digest=await desktop.service.projects.workspace_snapshot(project_id,snapshot_id,item_index=item_index,layer=layer)
        return Response(raw,media_type='application/octet-stream',headers={'X-PyHarness-Content-Digest':digest,'Cache-Control':'no-store'})

    @router.get('/projects/{project_id}/workspace-context-manifests')
    async def workspace_manifests(project_id: str,request: Request):
        from pyharness.application.workspace_input_pages import parse_workspace_query
        return await desktop.service.projects.workspace_manifests(project_id,query=parse_workspace_query(list(request.query_params.multi_items())))

    @router.post('/projects/{project_id}/workspace-context-manifests')
    async def save_workspace_manifest(project_id: str,body: dict=Body(...),idempotency_key: str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.workspace_manifests(project_id,body,idempotency_key)

    @router.get('/projects/{project_id}/workspace-context-manifests/{manifest_id}/revisions/{revision}')
    async def workspace_manifest(project_id: str,manifest_id: str,revision: int):
        return await desktop.service.projects.workspace_manifest(project_id,manifest_id,revision)

    @router.get('/projects/{project_id}/workspace-context-manifests/{manifest_id}/revisions/{revision}/items/{item_index}')
    async def workspace_manifest_file(project_id: str,manifest_id: str,revision: int,item_index: int):
        raw,digest=await desktop.service.projects.workspace_manifest(project_id,manifest_id,revision,item_index=item_index)
        return Response(raw,media_type='application/octet-stream',headers={'X-PyHarness-Content-Digest':digest,'Cache-Control':'no-store'})

    @router.post('/projects/{project_id}/workspace-inputs')
    async def prepare_workspace_input(project_id:str,body:dict=Body(...),idempotency_key:str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.prepare_workspace_input(project_id,body,idempotency_key)

    @router.get('/projects/{project_id}/workspace-inputs')
    async def workspace_inputs(project_id:str,request:Request):
        from pyharness.application.workspace_input_pages import parse_workspace_query
        return await desktop.service.projects.workspace_inputs(project_id,parse_workspace_query(list(request.query_params.multi_items())))

    @router.get('/projects/{project_id}/workspace-inputs/{input_id}')
    async def workspace_input(project_id:str,input_id:str):
        return await desktop.service.projects.workspace_input(project_id,input_id)

    from .product_task_routes import mount_product_tasks
    mount_product_tasks(router, desktop)
    from .document_routes import mount_documents
    mount_documents(router, desktop)
    from .run_attempt_routes import mount_run_attempts
    mount_run_attempts(router, desktop)
    from .execution_routes import mount_executions
    mount_executions(router,desktop)
    from .task_graph_routes import mount_task_graphs
    mount_task_graphs(router,desktop)
    from .delivery_routes import mount_deliveries
    mount_deliveries(router,desktop)
    from .writeback_routes import mount_writebacks
    mount_writebacks(router,desktop)
    from .observation_routes import mount_observations
    mount_observations(router,desktop)
    from .regression_routes import mount_regressions
    mount_regressions(router,desktop)
    from .experiment_routes import mount_experiments
    mount_experiments(router,desktop)
    desktop.api.include_router(router)
