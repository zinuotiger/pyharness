"""Snapshot commands share the existing control owner, lease and SessionLog."""
from datetime import datetime, timezone
import re

from pyharness.application.source_grants import parse_expiry
from pyharness.application.source_preflight import check_root_metadata
from pyharness.application.source_snapshot import SourceSnapshot, collect_selection, verify_selection, snapshot_summary, LIMIT,checked_private_bytes
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.events.payload import WorkspaceResourceCommittedPayload
from pyharness.errors import raise_code,PyHError


class SnapshotStore:
    def _snapshot_grant(self, resources, project_id, grant_id, expected_revision=None):
        row = resources.get(grant_id)
        grant = row.get('grant') if row else None
        if (grant is None or grant['project_id'] != project_id
                or resources[project_id]['project']['archived']):
            raise_code('GRD-403', reason='source_grant_unavailable')
        if expected_revision is not None and grant['revision'] != expected_revision:
            raise_code('EVT-101', reason='stale_revision')
        check_root_metadata(row)
        # Check time after potentially blocking root metadata I/O, not before it.
        if grant['revoked'] or parse_expiry(grant['expires_at']) <= datetime.now(timezone.utc):
            raise_code('GRD-403', reason='source_grant_unavailable')
        return row

    def _snapshot_content(self, digest):
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise_code('PERS-202', reason='snapshot_content_reference')
        path = self.root / 'snapshot-content' / (digest + '.bin')
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise_code('PERS-202', reason='snapshot_content_escape')
        return path

    def _verify_snapshot_content(self, snapshot):
        from pyharness.application.workspace_store import digest_of
        for file in snapshot.files:
            path = self._snapshot_content(file.sha256)
            try:
                if path.stat().st_size != file.size_bytes:
                    raise_code('PERS-202', reason='snapshot_content_size')
                data=checked_private_bytes(path)
            except OSError:
                raise_code('PERS-202', reason='snapshot_content_missing')
            except PyHError as error:
                if error.code=='PERS-202':raise
                raise_code('PERS-202',reason='snapshot_content_unavailable')
            if len(data) > LIMIT or digest_of(data) != file.sha256:
                raise_code('PERS-202', reason='snapshot_content_digest')

    async def preview_snapshot_context(self, project_id, grant_id, command, *, authorize):
        from pyharness.application.snapshot_context import build_context_preview
        async with self._lock:
            authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            row = resources.get(command.snapshot_id)
            snapshot = row.get('snapshot') if row else None
            if (snapshot is None or snapshot['project_id'] != project_id or snapshot['grant_id'] != grant_id
                    or snapshot['grant_revision'] != command.expected_revision):
                raise_code('GRD-403', reason='source_snapshot_unavailable')
            if row['digest'] != command.snapshot_digest:
                raise_code('EVT-101', reason='snapshot_digest_changed')
            result = build_context_preview(row)
            # Revalidate after private manifest/content I/O; no live source body is read.
            resources, _ = self._grant_state()
            authorize()
            self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            return result

    async def preview_snapshot(self, project_id, grant_id, command, *, authorize):
        async with self._lock:
            actor=authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            row = self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            def before_read():
                if authorize()!=actor: raise_code('GRD-403',reason='context_subject_changed')
                self._snapshot_grant(resources,project_id,grant_id,command.expected_revision)
                self._context_finish(authorize,actor,resources,(grant_id,))
            files, _, digest = collect_selection(row, command, before_read=before_read)
            resources, _ = self._grant_state()
            authorize()
            self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            return {'schema_version': 1, 'project_id': project_id, 'grant_id': grant_id,
                'revision': command.expected_revision, 'status': 'confirmation_required',
                'policy': 'reject-secrets-utf8-v1', 'confirmation_digest': digest,
                'file_count': len(files), 'total_size_bytes': sum(f.size_bytes for f in files)}

    async def snapshots(self, project_id, grant_id, *, authorize):
        async with self._lock:
            authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            self._snapshot_grant(resources, project_id, grant_id)
            rows = [snapshot_summary(row) for row in resources.values()
                    if row.get('snapshot', {}).get('grant_id') == grant_id]
            return {'snapshots': rows, 'total': len(rows)}

    async def capture_snapshot(self, project_id, grant_id, command, *, command_id, actor_ref, authorize):
        from pyharness.application.workspace_store import canonical, digest_of
        async with self._lock:
            authorize()
            await self._open(initialize=False)
            resources, commands = self._grant_state()
            row = self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            request_digest = digest_of(canonical({'operation': 'snapshot.capture', 'project_id': project_id,
                'grant_id': grant_id, 'command': command.model_dump()}))
            payload = WorkspaceResourceCommittedPayload(resource_kind='source_snapshot', resource_id=command.snapshot_id,
                revision=1, parent_revision=0, blob_ref='0'*64, digest='0'*64, command_id=command_id,
                request_digest=request_digest, actor_ref=actor_ref)
            old = commands.get((actor_ref, command_id))
            if old:
                if old[0] != request_digest:
                    raise_code('EVT-100', reason='idempotency_conflict')
                return snapshot_summary(old[1])
            if command.snapshot_id in resources:
                raise_code('EVT-101', reason='stale_revision')
            if sum(r.get('snapshot', {}).get('grant_id') == grant_id for r in resources.values()) >= 200:
                raise_code('EVT-100', reason='snapshot_count_limit')
            def before_read():
                if authorize()!=actor_ref: raise_code('GRD-403',reason='context_subject_changed')
                self._snapshot_grant(resources,project_id,grant_id,command.expected_revision)
                self._context_finish(authorize,actor_ref,resources,(grant_id,))
            files, contents, confirmation = collect_selection(row, command, before_read=before_read)
            if confirmation != command.confirmation_digest:
                raise_code('EVT-101', reason='snapshot_confirmation_changed')
            snapshot = SourceSnapshot(snapshot_id=command.snapshot_id, project_id=project_id, grant_id=grant_id,
                grant_revision=command.expected_revision, root_identity=row['grant']['root_identity'],
                confirmation_digest=confirmation, files=files, total_size_bytes=sum(f.size_bytes for f in files))
            data = canonical(snapshot.model_dump())
            if len(data) > 512 * 1024:
                raise_code('EVT-100', reason='snapshot_manifest_limit')
            digest = digest_of(data)
            # No content is written until the entire selection passes the content policy.
            for ref, raw in contents.items():
                self._write_snapshot_blob(self._snapshot_content(ref), raw)
            self._write_snapshot_blob(self._blob(digest), data)
            verify_selection(row, files, before_read=before_read)
            authorize()  # Role revocation during lock wait/I/O cannot reuse an old decision.
            resources, _ = self._grant_state()
            self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            verify_selection(row, files, before_read=before_read)
            # Re-read committed state after the final source I/O. The owner lock
            # serializes commands, but cannot freeze a grant's expiry clock.
            resources, _ = self._grant_state()
            authorize()
            self._snapshot_grant(resources, project_id, grant_id, command.expected_revision)
            payload = payload.model_copy(update={'blob_ref': digest, 'digest': digest}).model_dump()
            try:
                event = await self._session.append('workspace.resource_committed', payload, actor='user', sync=True)
                self._verify_commit(self._store, event)
                resources, _ = self._grant_state()  # exact disk + manifest + content readback
            except OSError:
                self._failed = True
                raise_code('PERS-202', reason='snapshot_commit_unconfirmed')
            except BaseException:
                self._failed = True
                raise
            return snapshot_summary(resources[command.snapshot_id])

    @staticmethod
    def _write_snapshot_blob(path, raw):
        try:
            if path.exists():
                if checked_private_bytes(path) != raw:
                    raise_code('PERS-202', reason='snapshot_blob_digest')
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                TenantSettingsStore._atomic_write(path, raw)
            if checked_private_bytes(path) != raw:
                raise_code('PERS-202', reason='snapshot_blob_unconfirmed')
        except OSError:
            raise_code('PERS-202', reason='snapshot_blob_write')
