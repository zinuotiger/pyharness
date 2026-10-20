"""Explicit product execution endpoints; tenant, actor and authority stay server-owned."""
import inspect
from fastapi import APIRouter,Body,Header,Request
from pyharness.errors import raise_code


def mount_executions(router,desktop):
    class PrivateExecutionRoute(router.route_class):
        def get_route_handler(self):
            original=super().get_route_handler()
            async def private(request):
                try:response=await original(request)
                except Exception as exc:
                    handler=next((desktop.api.exception_handlers[c] for c in type(exc).__mro__
                        if c in desktop.api.exception_handlers),None)
                    if handler is None:raise
                    response=handler(request,exc)
                    if inspect.isawaitable(response):response=await response
                response.headers['Cache-Control']='no-store'
                return response
            return private
    private=APIRouter(route_class=PrivateExecutionRoute)
    def projects():return desktop.service.projects
    def key(request):
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=1 or pairs[0][0]!='key':raise_code('EVT-100',reason='submission_query_invalid')
        return pairs[0][1]

    @private.post('/projects/{project_id}/execution-previews')
    async def preview(project_id:str,body:dict=Body(...)):
        return await projects().execution_preview(project_id,body)

    @private.get('/projects/{project_id}/run-attempts/{attempt_id}/execution')
    async def for_attempt(project_id:str,attempt_id:str):
        return await projects().execution_for_attempt(project_id,attempt_id)

    @private.get('/projects/{project_id}/run-attempts/{attempt_id}/execution-control')
    async def control_for_attempt(project_id:str,attempt_id:str):
        return await projects().execution_control_for_attempt(project_id,attempt_id)

    @private.post('/projects/{project_id}/executions')
    async def start(project_id:str,body:dict=Body(...),submission_key:str=Header(...,alias='Idempotency-Key')):
        return await projects().start_execution(project_id,body,submission_key)

    @private.post('/projects/{project_id}/executions/{execution_id}/corrections')
    async def correction(project_id:str,execution_id:str,body:dict=Body(...),submission_key:str=Header(...,alias='Idempotency-Key')):
        return await projects().submit_execution_correction(project_id,execution_id,body,submission_key)

    @private.get('/projects/{project_id}/executions/{execution_id}/corrections')
    async def corrections(project_id:str,execution_id:str):
        return await projects().execution_corrections(project_id,execution_id)

    @private.get('/projects/{project_id}/correction-submissions/by-key')
    async def correction_submission(project_id:str,request:Request):
        return await projects().correction_submission(project_id,key(request))

    @private.get('/projects/{project_id}/execution-submissions/by-key')
    async def submission(project_id:str,request:Request):
        return await projects().execution_submission(project_id,key(request))

    @private.get('/projects/{project_id}/executions/{execution_id}')
    async def observation(project_id:str,execution_id:str):
        return await projects().execution(project_id,execution_id)

    @private.get('/projects/{project_id}/executions/{execution_id}/recovery-plan')
    async def recovery_plan(project_id:str,execution_id:str):
        return await projects().execution_recovery_plan(project_id,execution_id)

    @private.get('/projects/{project_id}/executions/{execution_id}/continuation-plan')
    async def continuation_plan(project_id:str,execution_id:str):
        return await projects().execution_continuation_plan(project_id,execution_id)

    @private.post('/projects/{project_id}/executions/{execution_id}/continuations')
    async def continuation(project_id:str,execution_id:str,body:dict=Body(...),submission_key:str=Header(...,alias='Idempotency-Key')):
        return await projects().continue_execution(project_id,execution_id,body,submission_key)

    @private.get('/projects/{project_id}/continuation-submissions/by-key')
    async def continuation_submission(project_id:str,request:Request):
        return await projects().continuation_submission(project_id,key(request))

    @private.post('/projects/{project_id}/executions/{execution_id}/index-repairs')
    async def repair_index(project_id:str,execution_id:str,body:dict=Body(...),submission_key:str=Header(...,alias='Idempotency-Key')):
        return await projects().repair_execution_index(project_id,execution_id,body,submission_key)

    @private.get('/projects/{project_id}/executions/{execution_id}/index-repairs')
    async def index_repairs(project_id:str,execution_id:str):
        return await projects().index_repairs(project_id,execution_id)

    @private.get('/projects/{project_id}/index-repair-submissions/by-key')
    async def index_repair_submission(project_id:str,request:Request):
        return await projects().index_repair_submission(project_id,key(request))

    @private.post('/projects/{project_id}/execution-cancellations')
    async def cancel(project_id:str,body:dict=Body(...),submission_key:str=Header(...,alias='Idempotency-Key')):
        return await projects().cancel_execution(project_id,body,submission_key)

    @private.get('/projects/{project_id}/cancellation-submissions/by-key')
    async def cancelled(project_id:str,request:Request):
        return await projects().cancellation_submission(project_id,key(request))

    router.include_router(private)
