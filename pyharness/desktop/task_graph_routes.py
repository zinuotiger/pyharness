"""Private graph definition routes; the original project owner remains authority."""
import inspect
from fastapi import APIRouter,Body,Header,Request
from pyharness.application.workspace_input_pages import parse_workspace_query
from pyharness.errors import raise_code


def mount_task_graphs(router,desktop):
    class PrivateGraphRoute(router.route_class):
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
    private=APIRouter(route_class=PrivateGraphRoute)
    @private.get('/projects/{project_id}/task-graphs')
    async def graphs(project_id:str,request:Request):
        return await desktop.service.projects.task_graphs(project_id,query=parse_workspace_query(list(request.query_params.multi_items())))
    @private.post('/projects/{project_id}/task-graphs')
    async def save(project_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.task_graphs(project_id,body=body,command_id=key)
    @private.post('/projects/{project_id}/task-graph-previews')
    async def preview(project_id:str,body:dict=Body(...)):
        return await desktop.service.projects.task_graph_run_preview(project_id,body)
    @private.post('/projects/{project_id}/task-graph-runs')
    async def start(project_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.start_task_graph(project_id,body,key)
    @private.get('/projects/{project_id}/task-graph-runs')
    async def runs(project_id:str,request:Request):
        return await desktop.service.projects.task_graph_runs(project_id,parse_workspace_query(list(request.query_params.multi_items())))
    @private.get('/projects/{project_id}/task-graph-runs/{graph_run_id}')
    async def observed(project_id:str,graph_run_id:str):
        return await desktop.service.projects.task_graph_run(project_id,graph_run_id)
    @private.get('/projects/{project_id}/task-graph-runs/{graph_run_id}/control')
    async def minimal_control(project_id:str,graph_run_id:str):
        return await desktop.service.projects.task_graph_run_control(project_id,graph_run_id)
    @private.post('/projects/{project_id}/task-graph-cancellations')
    async def cancel(project_id:str,body:dict=Body(...),key:str=Header(...,alias='Idempotency-Key')):
        return await desktop.service.projects.cancel_task_graph(project_id,body,key)
    @private.get('/projects/{project_id}/task-graph-run-submissions/by-key')
    async def run_submitted(project_id:str,request:Request):
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=1 or pairs[0][0]!='key':raise_code('EVT-100',reason='submission_query_invalid')
        return await desktop.service.projects.task_graph_run_submission(project_id,pairs[0][1])
    @private.get('/projects/{project_id}/task-graph-cancellation-submissions/by-key')
    async def cancel_submitted(project_id:str,request:Request):
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=1 or pairs[0][0]!='key':raise_code('EVT-100',reason='submission_query_invalid')
        return await desktop.service.projects.task_graph_cancellation_submission(project_id,pairs[0][1])
    @private.get('/projects/{project_id}/task-graphs/{graph_id}/revisions/{revision}')
    async def graph(project_id:str,graph_id:str,revision:int):
        return await desktop.service.projects.task_graph(project_id,graph_id,revision)
    @private.get('/projects/{project_id}/task-graph-submissions/by-key')
    async def submitted(project_id:str,request:Request):
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=1 or pairs[0][0]!='key':raise_code('EVT-100',reason='submission_query_invalid')
        return await desktop.service.projects.task_graph_submission(project_id,pairs[0][1])
    router.include_router(private)
