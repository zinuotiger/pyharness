"""Product task service operations share the existing Projects authority and lifetime."""
from pyharness.core.lifecycle import admitted
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.context_manifest import ProductTaskId
from pyharness.application.product_task_contracts import ProductTaskCommand, TaskPreflight
from pyharness.errors import raise_code


class ProductTasks:
    @admitted
    async def product_tasks(self, project_id, body=None, command_id=None):
        self._authorize(write=True)
        project_id = self._workspace_id(project_id, ProjectId)
        authorize = lambda: self._authorize(write=True)
        if body is not None:
            return await self.store.save_product_task(project_id, ProductTaskCommand.model_validate(body),
                                                       command_id=command_id, authorize=authorize)
        rows = await self.store.product_tasks(project_id, authorize=authorize)
        return {'tasks': rows, 'total': len(rows)}

    @admitted
    async def product_task(self, project_id, task_id, revision):
        self._authorize(write=True)
        if type(revision) is not int or not 1 <= revision <= 200:
            raise_code('EVT-100', reason='product_task_revision_invalid')
        return await self.store.product_task(self._workspace_id(project_id, ProjectId),
            self._workspace_id(task_id, ProductTaskId), revision, authorize=lambda: self._authorize(write=True))

    @admitted
    async def product_task_preflight(self, project_id, task_id, body):
        self._authorize(write=True)
        project_id = self._workspace_id(project_id, ProjectId)
        task_id = self._workspace_id(task_id, ProductTaskId)
        command = TaskPreflight.model_validate(body)
        if command.task_ref.product_task_id != task_id:
            raise_code('GRD-403', reason='product_task_unavailable')
        return await self.store.product_task_preflight(project_id, command, authorize=lambda: self._authorize(write=True))
