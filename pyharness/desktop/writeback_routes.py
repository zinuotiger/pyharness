"""Private writeback routes on the existing authenticated project router."""
import inspect
from fastapi import APIRouter,Body,Header,Request


def mount_writebacks(router,desktop):
    class PrivateWritebackRoute(router.route_class):
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
    private=APIRouter(route_class=PrivateWritebackRoute)

    def handlers(path,kind,operation):
        async def save(project_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
            return await getattr(desktop.service.projects,operation)(project_id,body,key)
        async def read(project_id:str,ident:str):
            return await desktop.service.projects.read_writeback(project_id,kind,ident)
        private.add_api_route('/projects/{project_id}/'+path,save,methods=['POST'],name='save_'+kind)
        private.add_api_route('/projects/{project_id}/'+path+'/{ident}',read,methods=['GET'],name='read_'+kind)
    handlers('writeback-grants','writeback_grant','issue_writeback_grant')
    handlers('merge-plans','merge_plan','save_merge_plan')
    handlers('writeback-applications','writeback_application','apply_merge_plan')
    handlers('merge-plan-exports','merge_export','authorize_merge_export')

    @private.post('/projects/{project_id}/writeback-grants/{grant_id}/revoke')
    async def revoke(project_id:str,grant_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.revoke_writeback_grant(project_id,grant_id,body,key)

    @private.post('/projects/{project_id}/merge-previews')
    async def preview(project_id:str,body:dict=Body(...)):
        return await desktop.service.projects.preview_writeback(project_id,body)

    @private.get('/projects/{project_id}/writeback-center')
    async def center(project_id:str):return await desktop.service.projects.writeback_center(project_id)

    @private.get('/projects/{project_id}/delivery-packages/{package_id}/merge-source-options')
    async def sources(project_id:str,package_id:str):return await desktop.service.projects.writeback_source_options(project_id,package_id)

    @private.get('/projects/{project_id}/writeback-submissions/by-key')
    async def submission(project_id:str,request:Request):
        from pyharness.errors import raise_code
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=1 or pairs[0][0]!='key':raise_code('EVT-100',reason='submission_query_invalid')
        return await desktop.service.projects.writeback_submission(project_id,pairs[0][1])

    @private.get('/projects/{project_id}/merge-plan-exports/{export_id}/files/{index}')
    async def download(project_id:str,export_id:str,index:int):
        from fastapi.responses import Response
        from urllib.parse import quote
        raw,filename=await desktop.service.projects.merge_export_bytes(project_id,export_id,index)
        return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''),
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'"})
    router.include_router(private)
