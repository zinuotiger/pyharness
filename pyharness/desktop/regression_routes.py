"""Bounded private regression data endpoints; no model or tool execution route."""
import inspect
import re
from fastapi import APIRouter,Request,Header
from pyharness.application.regression_assertions import strict_json
from pyharness.errors import raise_code


async def regression_body(request,*,max_bytes=65536):
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>max_bytes:raise_code('EVT-100',reason='regression_request_limit')
        data.extend(chunk)
    try:
        body=strict_json(data.decode('utf-8'))
        if type(body) is not dict:raise ValueError('object required')
        return body
    except (ValueError,RecursionError,UnicodeError):raise_code('EVT-100',reason='regression_request_invalid')


def mount_regressions(router,desktop):
    class PrivateRegressionRoute(router.route_class):
        def get_route_handler(self):
            original=super().get_route_handler()
            async def private(request):
                try:response=await original(request)
                except Exception as exc:
                    handler=next((desktop.api.exception_handlers[c] for c in type(exc).__mro__ if c in desktop.api.exception_handlers),None)
                    if handler is None:raise
                    response=handler(request,exc)
                    if inspect.isawaitable(response):response=await response
                response.headers['Cache-Control']='no-store';return response
            return private
    private=APIRouter(route_class=PrivateRegressionRoute)

    def revision(request):
        pairs=list(request.query_params.multi_items())
        if not pairs:return None
        if (len(pairs)!=1 or pairs[0][0]!='revision' or not re.fullmatch(r'[1-9][0-9]{0,2}',pairs[0][1])
                or int(pairs[0][1])>200):raise_code('EVT-100',reason='regression_revision_query')
        return int(pairs[0][1])

    def no_query(request):
        if request.query_params:raise_code('EVT-100',reason='regression_query')

    @private.post('/projects/{project_id}/failure-candidates')
    async def candidate(project_id:str,request:Request):
        no_query(request)
        return await desktop.service.projects.failure_candidate(project_id,await regression_body(request))

    @private.post('/projects/{project_id}/regression-cases')
    async def save_case(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request)
        return await desktop.service.projects.save_regression_case(project_id,await regression_body(request),idempotency_key)

    @private.get('/projects/{project_id}/regression-cases')
    async def cases(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.regression_cases(project_id)

    @private.get('/projects/{project_id}/regression-cases/{case_id}')
    async def case(project_id:str,case_id:str,request:Request):
        return await desktop.service.projects.regression_case(project_id,case_id,revision(request))

    @private.post('/projects/{project_id}/regression-datasets')
    async def save_dataset(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request)
        return await desktop.service.projects.save_regression_dataset(project_id,await regression_body(request),idempotency_key)

    @private.get('/projects/{project_id}/regression-datasets')
    async def datasets(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.regression_datasets(project_id)

    @private.get('/projects/{project_id}/regression-datasets/{dataset_id}')
    async def dataset(project_id:str,dataset_id:str,request:Request):
        return await desktop.service.projects.regression_dataset(project_id,dataset_id,revision(request))

    @private.post('/projects/{project_id}/regression-datasets/{dataset_id}/export-previews')
    async def export_preview(project_id:str,dataset_id:str,request:Request):
        no_query(request)
        return await desktop.service.projects.regression_export_preview(project_id,dataset_id,await regression_body(request))

    @private.post('/projects/{project_id}/regression-datasets/{dataset_id}/exports')
    async def export(project_id:str,dataset_id:str,request:Request):
        no_query(request)
        return await desktop.service.projects.regression_export(project_id,dataset_id,await regression_body(request))

    @private.post('/projects/{project_id}/regression-dataset-import-previews')
    async def import_preview(project_id:str,request:Request):
        no_query(request)
        return await desktop.service.projects.regression_import_preview(project_id,await regression_body(request,max_bytes=196608))

    @private.post('/projects/{project_id}/regression-dataset-imports')
    async def imported(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request)
        return await desktop.service.projects.import_regression_dataset(project_id,await regression_body(request,max_bytes=196608),idempotency_key)

    @private.get('/projects/{project_id}/regression-submissions/{key}')
    async def submission(project_id:str,key:str,request:Request):
        no_query(request);return await desktop.service.projects.regression_submission(project_id,key)

    router.include_router(private)
