"""Private metadata/context reads on the shared application authorization surface."""
import inspect
from fastapi import APIRouter,Request,Body
from pyharness.errors import raise_code


def mount_observations(router,desktop):
    class PrivateObservationRoute(router.route_class):
        def get_route_handler(self):
            original=super().get_route_handler()
            async def private(request):
                try:response=await original(request)
                except Exception as exc:
                    handler=next((desktop.api.exception_handlers[c] for c in type(exc).__mro__ if c in desktop.api.exception_handlers),None)
                    if handler is None:raise
                    response=handler(request,exc)
                    if inspect.isawaitable(response):response=await response
                response.headers['Cache-Control']='no-store'
                return response
            return private
    private=APIRouter(route_class=PrivateObservationRoute)
    def session(request):
        pairs=list(request.query_params.multi_items())
        if len(pairs)>1 or pairs and pairs[0][0]!='session_id':raise_code('EVT-100',reason='public_context_query')
        return pairs[0][1] if pairs else None

    @private.get('/projects/{project_id}/executions/{execution_id}/contexts')
    async def contexts(project_id:str,execution_id:str,request:Request):
        return await desktop.service.projects.execution_contexts(project_id,execution_id,session(request))

    @private.post('/projects/{project_id}/permission-previews')
    async def permission(project_id:str,request:Request,body:dict=Body(...)):
        if request.query_params:raise_code('EVT-100',reason='permission_preview_query')
        return await desktop.service.projects.permission_preview(project_id,body)

    @private.post('/projects/{project_id}/run-comparisons')
    async def comparison(project_id:str,request:Request,body:dict=Body(...)):
        if request.query_params:raise_code('EVT-100',reason='run_comparison_query')
        return await desktop.service.projects.compare_executions(project_id,body)

    @private.get('/projects/{project_id}/executions/{execution_id}/contexts/{observation_id}')
    async def context(project_id:str,execution_id:str,observation_id:str,request:Request):
        return await desktop.service.projects.execution_contexts(project_id,execution_id,session(request),observation_id)

    router.include_router(private)
