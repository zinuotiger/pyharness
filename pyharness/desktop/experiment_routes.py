"""Bounded private experiment endpoints reuse the existing authenticated router."""
import inspect,re
from fastapi import APIRouter,Request,Header
from pyharness.application.regression_assertions import strict_json
from pyharness.desktop.regression_routes import regression_body
from pyharness.errors import raise_code


def mount_experiments(router,desktop):
    class PrivateExperimentRoute(router.route_class):
        def get_route_handler(self):
            handler=super().get_route_handler()
            async def private(request):
                try:response=await handler(request)
                except Exception as exc:
                    convert=next((desktop.api.exception_handlers[c] for c in type(exc).__mro__ if c in desktop.api.exception_handlers),None)
                    if convert is None:raise
                    response=convert(request,exc)
                    if inspect.isawaitable(response):response=await response
                response.headers['Cache-Control']='no-store';return response
            return private
    private=APIRouter(route_class=PrivateExperimentRoute)
    def no_query(request):
        if request.query_params:raise_code('EVT-100',reason='experiment_query')

    @private.post('/projects/{project_id}/experiment-preparation-previews')
    async def preview(project_id:str,request:Request):
        no_query(request)
        return await desktop.service.projects.experiment_preparation_preview(project_id,await regression_body(request))

    @private.post('/projects/{project_id}/experiments')
    async def prepare(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request)
        return await desktop.service.projects.prepare_experiment(project_id,await regression_body(request),idempotency_key)

    @private.get('/projects/{project_id}/experiments/{experiment_id}')
    async def detail(project_id:str,experiment_id:str,request:Request):
        pairs=list(request.query_params.multi_items());revision=None
        if pairs:
            if len(pairs)!=1 or pairs[0][0]!='revision' or not re.fullmatch(r'[1-9][0-9]{0,2}',pairs[0][1]) or int(pairs[0][1])>200:
                raise_code('EVT-100',reason='experiment_revision_query')
            revision=int(pairs[0][1])
        return await desktop.service.projects.experiment(project_id,experiment_id,revision)

    @private.get('/projects/{project_id}/experiment-submissions/{key}')
    async def submission(project_id:str,key:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_submission(project_id,key)

    @private.get('/projects/{project_id}/experiments')
    async def definitions(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_directory(project_id,'experiment_definition')

    @private.get('/projects/{project_id}/experiment-runs')
    async def runs(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_directory(project_id,'experiment_run')

    @private.get('/projects/{project_id}/experiment-reports')
    async def reports(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_directory(project_id,'experiment_report')

    @private.get('/projects/{project_id}/experiment-command-submissions/{key}')
    async def command_submission(project_id:str,key:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_command_submission(project_id,key)
    @private.post('/projects/{project_id}/experiment-run-previews')
    async def run_preview(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_run_preview(project_id,await regression_body(request))

    @private.post('/projects/{project_id}/experiment-runs')
    async def run_start(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request);return await desktop.service.projects.start_experiment_run(project_id,await regression_body(request),idempotency_key)

    @private.get('/projects/{project_id}/experiment-runs/{run_id}')
    async def run_detail(project_id:str,run_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_run(project_id,run_id)

    @private.get('/projects/{project_id}/experiment-runs/{run_id}/control')
    async def run_control(project_id:str,run_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_run_control(project_id,run_id)

    @private.post('/projects/{project_id}/experiment-run-resume-previews')
    async def resume_preview(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_resume_preview(project_id,await regression_body(request))

    @private.post('/projects/{project_id}/experiment-run-resumptions')
    async def resume(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request);return await desktop.service.projects.resume_experiment_run(project_id,await regression_body(request),idempotency_key)

    @private.post('/projects/{project_id}/experiment-cancellations')
    async def cancel(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request);return await desktop.service.projects.cancel_experiment_run(project_id,await regression_body(request),idempotency_key)

    @private.post('/projects/{project_id}/experiment-report-previews')
    async def report_preview(project_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_report_preview(project_id,await regression_body(request))

    @private.post('/projects/{project_id}/experiment-reports')
    async def report_save(project_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key')):
        no_query(request);return await desktop.service.projects.save_experiment_report(project_id,await regression_body(request),idempotency_key)

    @private.get('/projects/{project_id}/experiment-reports/{report_id}')
    async def report_detail(project_id:str,report_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_report(project_id,report_id)

    @private.post('/projects/{project_id}/experiment-reports/{report_id}/export-previews')
    async def report_export_preview(project_id:str,report_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_report(project_id,report_id,await regression_body(request))

    @private.post('/projects/{project_id}/experiment-reports/{report_id}/exports')
    async def report_export(project_id:str,report_id:str,request:Request):
        no_query(request);return await desktop.service.projects.experiment_report(project_id,report_id,await regression_body(request),confirmed=True)

    router.include_router(private)
