"""Thin formal task routes using the existing workspace error/auth/lifetime adapter."""
from fastapi import Body, Header


def mount_product_tasks(router, desktop):
    @router.get('/projects/{project_id}/product-tasks')
    async def product_tasks(project_id: str):
        return await desktop.service.projects.product_tasks(project_id)

    @router.post('/projects/{project_id}/product-tasks')
    async def save_product_task(project_id: str, body: dict = Body(...),
                                idempotency_key: str = Header(..., alias='Idempotency-Key')):
        return await desktop.service.projects.product_tasks(project_id, body, idempotency_key)

    @router.get('/projects/{project_id}/product-tasks/{task_id}/revisions/{revision}')
    async def product_task(project_id: str, task_id: str, revision: int):
        return await desktop.service.projects.product_task(project_id, task_id, revision)

    @router.post('/projects/{project_id}/product-tasks/{task_id}/preflight')
    async def preflight_product_task(project_id: str, task_id: str, body: dict = Body(...)):
        return await desktop.service.projects.product_task_preflight(project_id, task_id, body)
