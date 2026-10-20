"""Git preview reuses current control authority without appending any facts."""
import asyncio
import time
from pyharness.application.workspace_git import preview_git_layers
from pyharness.errors import raise_code


class GitPreviewStore:
    MAX_GIT_PREVIEW_SECONDS = 25
    async def preview_git_source(self, project_id, grant_id, command, *, authorize):
        started = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._preview_git_source_locked(project_id, grant_id, command, authorize=authorize, started=started),
                self.MAX_GIT_PREVIEW_SECONDS,
            )
        except TimeoutError:
            raise_code('EVT-104', reason='git_preview_time_limit')

    async def _preview_git_source_locked(self, project_id, grant_id, command, *, authorize, started):
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            source = self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            metadata = None
            if command.metadata_grant_id:
                metadata = self._snapshot_grant(resources, project_id, command.metadata_grant_id, command.metadata_grant_revision)
            self._context_finish(authorize, actor, resources, (grant_id,)+((command.metadata_grant_id,) if metadata else ()))
            if time.monotonic() - started >= self.MAX_GIT_PREVIEW_SECONDS:
                raise_code('EVT-104', reason='git_preview_time_limit')
            def before_body_read():
                if authorize()!=actor:raise_code('GRD-403',reason='git_preview_subject_changed')
                self._snapshot_grant(resources,project_id,grant_id,command.expected_revision)
                if metadata:self._snapshot_grant(resources,project_id,command.metadata_grant_id,command.metadata_grant_revision)
                self._context_finish(authorize,actor,resources,(grant_id,)+((command.metadata_grant_id,) if metadata else ()))
                if time.monotonic()-started>=self.MAX_GIT_PREVIEW_SECONDS:raise_code('EVT-104',reason='git_preview_time_limit')
            result = await preview_git_layers(source, metadata, command,before_body_read=before_body_read)
            fresh, _ = self._grant_state()
            self._snapshot_grant(fresh, project_id, grant_id, command.expected_revision)
            if metadata:
                self._snapshot_grant(fresh, project_id, command.metadata_grant_id, command.metadata_grant_revision)
            self._context_finish(authorize, actor, fresh, (grant_id,)+((command.metadata_grant_id,) if metadata else ()))
            # Event-loop timers cannot preempt synchronous filesystem/authority I/O.
            # Refuse success after that tail overruns the observation budget.
            if time.monotonic() - started >= self.MAX_GIT_PREVIEW_SECONDS:
                raise_code('EVT-104', reason='git_preview_time_limit')
            return result
