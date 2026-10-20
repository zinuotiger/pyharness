"""Product-attempt operations on the existing Projects authority/lifetime."""
from pyharness.core.lifecycle import admitted
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.run_attempt_contracts import AttemptId, SubmitAttempt, SubmissionKey


class RunAttempts:
    @admitted
    async def run_attempts(self, project, query=None, body=None, command_id=None):
        self._authorize(write=True); project = self._workspace_id(project, ProjectId)
        authorize = lambda: self._authorize(write=True)
        if body is not None:
            command = SubmitAttempt.model_validate(body)
            key = self._workspace_id(command_id, SubmissionKey)
            return await self.store.submit_run_attempt(project, command, command_id=key, authorize=authorize)
        from pyharness.application.workspace_input_pages import WorkspaceInputQuery
        return await self.store.run_attempts(project, query or WorkspaceInputQuery(), authorize=authorize)

    @admitted
    async def run_attempt(self, project, ident):
        self._authorize(write=True)
        return await self.store.run_attempt(self._workspace_id(project, ProjectId),
            self._workspace_id(ident, AttemptId), authorize=lambda: self._authorize(write=True))

    @admitted
    async def run_submission(self, project, key):
        self._authorize(write=True)
        return await self.store.run_submission(self._workspace_id(project, ProjectId),
            self._workspace_id(key, SubmissionKey), authorize=lambda: self._authorize(write=True))
