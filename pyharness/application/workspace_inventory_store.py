"""Ephemeral candidate metadata using the existing owner and authorization gates."""
import asyncio,time
from pyharness.application.workspace_git_inventory import GitInventoryCandidate
from pyharness.application.workspace_git import preview_git_layers
from pyharness.errors import raise_code


class WorkspaceInventoryStore:
    async def preview_git_candidates(self,project_id,grant_id,command,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False)
            resources,_=self._grant_state();self._context_project(resources,project_id,command.project_revision)
            source=self._snapshot_grant(resources,project_id,grant_id,command.expected_revision)
            metadata=self._snapshot_grant(resources,project_id,command.metadata_grant_id,command.metadata_grant_revision) if command.metadata_grant_id else None
            self._workspace_storage_geometry(source,metadata)
            remaining=self.MAX_WORKSPACE_CAPTURE_SECONDS-(time.monotonic()-started)
            if remaining<=0:raise_code('EVT-104',reason='workspace_capture_time_limit')
            candidate=GitInventoryCandidate(command,actor=actor,tenant=self.tenant_id)
            try:await asyncio.wait_for(preview_git_layers(source,metadata,command,inventory=candidate),remaining)
            except TimeoutError:raise_code('EVT-104',reason='workspace_capture_time_limit')
            page=candidate.page()
            self._workspace_capture_gate(project_id,grant_id,command,actor,authorize,candidate,started)
            return page
