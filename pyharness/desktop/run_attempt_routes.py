"""Thin, per-request tenant-resolved routes; submission keys are query values."""
from fastapi import Body, Header, Request, Response
from pyharness.errors import raise_code


def mount_run_attempts(router, desktop):
    def projects():return desktop.service.projects

    @router.get('/projects/{project_id}/run-attempts')
    async def run_attempts(project_id: str, request: Request, response: Response):
        from pyharness.application.workspace_input_pages import parse_workspace_query
        response.headers['Cache-Control'] = 'no-store'
        return await projects().run_attempts(project_id, parse_workspace_query(list(request.query_params.multi_items())))

    @router.post('/projects/{project_id}/run-attempts')
    async def submit_attempt(project_id: str, response: Response, body: dict = Body(...),
                             idempotency_key: str = Header(..., alias='Idempotency-Key')):
        response.headers['Cache-Control'] = 'no-store'
        return await projects().run_attempts(project_id, body=body, command_id=idempotency_key)

    @router.get('/projects/{project_id}/run-submissions/by-key')
    async def run_submission(project_id: str, request: Request, response: Response):
        pairs = list(request.query_params.multi_items())
        if len(pairs) != 1 or pairs[0][0] != 'key':raise_code('EVT-100', reason='submission_query_invalid')
        response.headers['Cache-Control'] = 'no-store'
        return await projects().run_submission(project_id, pairs[0][1])

    @router.get('/projects/{project_id}/run-attempts/{attempt_id}')
    async def run_attempt(project_id: str, attempt_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return await projects().run_attempt(project_id, attempt_id)
