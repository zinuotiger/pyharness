"""Private no-store delivery endpoints on the existing authenticated router."""
import inspect
from fastapi import APIRouter,Body,Header,Request


def mount_deliveries(router,desktop):
    class PrivateDeliveryRoute(router.route_class):
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
    private=APIRouter(route_class=PrivateDeliveryRoute)

    @private.get('/projects/{project_id}/delivery-center')
    async def center(project_id:str):
        return await desktop.service.projects.delivery_center(project_id)

    @private.get('/projects/{project_id}/delivery-submissions/by-key')
    async def submitted(project_id:str,request:Request):
        from pyharness.errors import raise_code
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=1 or pairs[0][0]!='key':raise_code('EVT-100',reason='submission_query_invalid')
        return await desktop.service.projects.delivery_submission(project_id,pairs[0][1])

    @private.get('/projects/{project_id}/executions/{execution_id}/delivery-outputs')
    async def outputs(project_id:str,execution_id:str):
        return await desktop.service.projects.delivery_outputs(project_id,execution_id)

    @private.post('/projects/{project_id}/delivery-packages')
    async def freeze(project_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.save_delivery_package(project_id,body,key)

    @private.get('/projects/{project_id}/delivery-packages/{package_id}')
    async def package(project_id:str,package_id:str):
        return await desktop.service.projects.delivery_package(project_id,package_id)

    @private.post('/projects/{project_id}/handoff-documents')
    async def import_output(project_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.handoff_document_import(project_id,body,key)

    @private.post('/projects/{project_id}/handoff-document-previews')
    async def preview_pdf(project_id:str,body:dict=Body(...)):
        return await desktop.service.projects.handoff_document_import(project_id,body,preview=True)

    def handlers(kind):
        async def save(project_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
            return await desktop.service.projects.save_role_delivery(project_id,kind,body,key)
        async def get(project_id:str,ident:str):
            return await desktop.service.projects.role_delivery(project_id,kind,ident)
        return save,get
    for path,kind in [('role-contracts','role_contract'),('role-handoffs','role_handoff'),('handoff-acks','handoff_ack'),
                       ('delivery-acceptances','delivery_acceptance'),('delivery-exports','delivery_export')]:
        save,get=handlers(kind)
        private.add_api_route('/projects/{project_id}/'+path,save,methods=['POST'],name='save_'+kind)
        private.add_api_route('/projects/{project_id}/'+path+'/{ident}',get,methods=['GET'],name='get_'+kind)

    @private.get('/projects/{project_id}/delivery-exports/{export_id}/outputs/{index}')
    async def download(project_id:str,export_id:str,index:int):
        from fastapi.responses import Response
        from urllib.parse import quote
        raw,filename=await desktop.service.projects.delivery_export_bytes(project_id,export_id,index)
        return Response(raw,media_type='application/octet-stream',headers={
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''),
            'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'",
            'Cache-Control':'no-store'})

    router.include_router(private)
