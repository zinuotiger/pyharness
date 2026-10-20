"""SourceGrant commands on the existing WorkspaceStore owner and control log."""
from datetime import datetime, timezone

from pyharness.application.source_grants import SourceGrant, authorize_root, grant_summary, parse_expiry
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.events.payload import WorkspaceResourceCommittedPayload
from pyharness.errors import raise_code
from pyharness.persistence import detect_truncation, session_data_paths


class SourceGrantStore:
    def _grant_state(self):
        for path in session_data_paths(self.log_path):
            if detect_truncation(path) is not None:
                raise_code('PERS-202', reason='workspace_log_incomplete')
        events = list(self._store.replay())
        if self._store._quarantine:
            raise_code('PERS-202', reason='workspace_log_corrupt')
        self._validate_log(events)
        cached = self._session.events_after(0)
        if [e.model_dump() for e in events] != [e.model_dump() for e in cached]:
            raise_code('PERS-202', reason='workspace_log_changed')
        return self._project(events)

    async def require_source_grant(self, grant_id):
        """Status predicate only, not a file-read or tool authorization token."""
        async with self._lock:
            await self._open()
            resources, _ = self._grant_state()
            row = resources.get(grant_id)
            grant = row.get('grant') if row else None
            if (grant is None or grant['revoked']
                    or parse_expiry(grant['expires_at']) <= datetime.now(timezone.utc)
                    or resources[grant['project_id']]['project']['archived']):
                raise_code('GRD-403', reason='source_grant_unavailable')
            return grant_summary(row)

    async def preflight_source_grant(self, project_id, grant_id, command):
        from pyharness.application.source_preflight import check_root_metadata
        return await self._observe_source_grant(project_id, grant_id, command, check_root_metadata)

    async def preview_source_selection(self, project_id, grant_id, command):
        from pyharness.application.source_selection import observe_selection
        return await self._observe_source_grant(project_id, grant_id, command,
                                                lambda row: observe_selection(row, command))

    async def _observe_source_grant(self, project_id, grant_id, command, observe):
        async with self._lock:
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            row = resources.get(grant_id)
            grant = row.get('grant') if row else None
            if (grant is None or grant['project_id'] != project_id
                    or resources[project_id]['project']['archived']):
                raise_code('GRD-403', reason='source_grant_unavailable')
            if grant['revision'] != command.expected_revision:
                raise_code('EVT-101', reason='stale_revision')
            if grant['revoked'] or parse_expiry(grant['expires_at']) <= datetime.now(timezone.utc):
                raise_code('GRD-403', reason='source_grant_unavailable')
            return observe(row)

    async def revoke_source_grant(self, grant_id, command, *, command_id, actor_ref):
        from pyharness.application.workspace_store import canonical, digest_of
        async with self._lock:
            await self._open()
            request_digest = digest_of(canonical({'operation': 'source_grant.revoke', 'grant_id': grant_id,
                                                  'command': command.model_dump()}))
            WorkspaceResourceCommittedPayload(resource_kind='source_grant', resource_id=grant_id,
                revision=command.expected_revision + 1, parent_revision=command.expected_revision,
                blob_ref='0'*64, digest='0'*64, command_id=command_id,
                request_digest=request_digest, actor_ref=actor_ref)
            resources, commands = self._grant_state()
            old = commands.get((actor_ref, command_id))
            if old:
                if old[0] != request_digest:
                    raise_code('EVT-100', reason='idempotency_conflict')
                return grant_summary(old[1])
            previous = resources.get(grant_id, {}).get('grant')
            if previous is None:
                raise_code('GRD-403', reason='source_grant_unavailable')
            if previous['revision'] != command.expected_revision or previous['revoked']:
                raise_code('EVT-101', reason='stale_revision')
            grant = SourceGrant.model_validate({**previous, 'revision': previous['revision'] + 1, 'revoked': True})
            return await self._append_grant(grant, expected_revision=command.expected_revision,
                command_id=command_id, request_digest=request_digest, actor_ref=actor_ref)

    async def source_grants(self, project_id):
        async with self._lock:
            await self._open()
            resources, _ = self._grant_state()
            if project_id not in resources or 'project' not in resources[project_id]:
                raise_code('GRD-403', reason='source_project_unavailable')
            return [grant_summary(row) for row in resources.values()
                    if 'grant' in row and row['grant']['project_id'] == project_id]

    async def issue_source_grant(self, project_id, command, *, command_id, actor_ref):
        from pyharness.application.workspace_store import canonical, digest_of
        async with self._lock:
            await self._open()
            request_digest = digest_of(canonical({'operation': 'source_grant.issue', 'project_id': project_id,
                                                  'command': command.model_dump()}))
            # Validate the command envelope before looking up durable idempotency.
            WorkspaceResourceCommittedPayload(resource_kind='source_grant', resource_id=command.grant_id,
                revision=1, parent_revision=0, blob_ref='0'*64, digest='0'*64,
                command_id=command_id, request_digest=request_digest, actor_ref=actor_ref)
            resources, commands = self._grant_state()
            old = commands.get((actor_ref, command_id))
            if old:
                if old[0] != request_digest:
                    raise_code('EVT-100', reason='idempotency_conflict')
                return grant_summary(old[1])
            parent = resources.get(project_id, {}).get('project')
            if parent is None or parent['archived']:
                raise_code('GRD-403', reason='source_project_unavailable')
            if command.grant_id in resources:
                raise_code('EVT-101', reason='stale_revision')
            if sum('grant' in row and row['grant']['project_id'] == project_id for row in resources.values()) >= 200:
                raise_code('EVT-100', reason='source_grant_limit')
            if parse_expiry(command.expires_at) <= datetime.now(timezone.utc):
                raise_code('EVT-100', reason='source_grant_expired')
            grant = SourceGrant(grant_id=command.grant_id, project_id=project_id, revision=1,
                **authorize_root(command.source_path), actions=command.actions,
                expires_at=command.expires_at, approved_by=actor_ref)
            return await self._append_grant(grant, expected_revision=0, command_id=command_id,
                                            request_digest=request_digest, actor_ref=actor_ref)

    async def _append_grant(self, grant, *, expected_revision, command_id, request_digest, actor_ref):
        from pyharness.application.workspace_store import canonical, digest_of
        data = canonical(grant.model_dump())
        digest = digest_of(data)
        payload = WorkspaceResourceCommittedPayload(resource_kind='source_grant', resource_id=grant.grant_id,
            revision=grant.revision, parent_revision=expected_revision, blob_ref=digest, digest=digest,
            command_id=command_id, request_digest=request_digest, actor_ref=actor_ref).model_dump()
        path = self._blob(digest)
        try:
            if path.exists():
                if path.read_bytes() != data:
                    raise_code('PERS-202', reason='workspace_blob_digest')
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                TenantSettingsStore._atomic_write(path, data)
        except OSError:
            raise_code('PERS-202', reason='workspace_blob_write')
        try:
            event = await self._session.append('workspace.resource_committed', payload, actor='user', sync=True)
            self._verify_commit(self._store, event)
        except BaseException:
            self._failed = True
            raise
        resources, _ = self._project(self._session.events_after(0))
        return grant_summary(resources[grant.grant_id])
