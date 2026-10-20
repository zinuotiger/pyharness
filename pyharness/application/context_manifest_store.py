"""Context definitions on the existing control owner, JSONL facts and immutable blobs."""
from pyharness.application.context_manifest import ContextManifest, TaskContextReference, task_identity
from pyharness.application.snapshot_context import build_context_preview
from pyharness.application.source_snapshot import checked_private_bytes
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.errors import raise_code,PyHError
from pyharness.events.payload import WorkspaceResourceCommittedPayload


class ContextManifestStore:
    MAX_CONTEXT_HISTORY = 200
    MAX_CONTEXT_BYTES = 512 * 1024

    @staticmethod
    def _context_finish(authorize, actor, resources, grant_ids=()):
        """Recheck subject after all disk/root I/O, then UTC after account I/O."""
        from datetime import timezone
        from pyharness.application import snapshot_store
        from pyharness.application.source_grants import parse_expiry
        if authorize() != actor:
            raise_code('GRD-403', reason='context_subject_changed')
        now = snapshot_store.datetime.now(timezone.utc)
        for ident in set(grant_ids):
            grant = resources[ident]['grant']
            if grant['revoked'] or parse_expiry(grant['expires_at']) <= now:
                raise_code('GRD-403', reason='source_grant_unavailable')

    @staticmethod
    def _context_project(resources, project_id, revision=None):
        parent = resources.get(project_id, {}).get('project')
        if parent is None or parent['archived']:
            raise_code('GRD-403', reason='context_project_unavailable')
        if revision is not None and parent['revision'] != revision:
            raise_code('EVT-101', reason='context_project_revision')
        return parent

    @staticmethod
    def _context_owned(resources, ident, kind, project_id, actor):
        row = resources.get(ident)
        value = row.get(kind) if row else None
        if value is None or value['project_id'] != project_id or value['created_by'] != actor:
            raise_code('GRD-403', reason='context_resource_unavailable')
        return row

    def _context_task(self, resources, ref, project_id, project_revision, actor, *, exact=True):
        row = self._context_owned(resources, ref['product_task_id'], 'task_reference', project_id, actor)
        value = row['task_reference']
        if value['retired']:
            raise_code('GRD-403', reason='context_task_retired')
        if exact and (value['revision'] != ref['revision'] or row['digest'] != ref['digest']
                      or row['source_seq'] != ref['source_seq'] or value['project_revision'] != project_revision):
            raise_code('EVT-101', reason='context_task_revision')
        return row

    def _validate_context_definition(self, definition, resources, previous, event):
        """Validate at event time; later versions cause stale, never corrupt old history."""
        from pyharness.errors import PyHError
        value = definition.model_dump()
        try:
            self._context_project(resources, value['project_id'], value['project_revision'])
            if value['created_by'] != event.payload['actor_ref']:
                raise ValueError('creator mismatch')
            kind = 'manifest' if isinstance(definition, ContextManifest) else 'task_reference'
            self._context_quota(resources, kind, value['project_id'])
            prior = previous.get(kind) if previous else None
            if prior and prior['schema_version']!=1:
                raise ValueError('context version cannot be downgraded')
            if prior and (prior['created_by'] != value['created_by'] or prior['project_id'] != value['project_id']):
                raise ValueError('immutable ownership')
            if kind == 'task_reference':
                if prior and prior['retired']:
                    raise ValueError('retired target cannot be revived')
                return
            if prior and task_identity(prior['task_ref']) != task_identity(value['task_ref']):
                raise ValueError('immutable target identity')
            grant = resources.get(value['grant_id'], {}).get('grant')
            snaprow = resources.get(value['snapshot_id'], {})
            snapshot = snaprow.get('snapshot')
            if (not grant or grant['project_id'] != value['project_id'] or grant['revoked']
                    or grant['revision'] != value['grant_revision'] or not snapshot
                    or snapshot['project_id'] != value['project_id'] or snapshot['grant_id'] != value['grant_id']
                    or snapshot['grant_revision'] != value['grant_revision']
                    or snaprow['digest'] != value['snapshot_digest'] or snaprow['source_seq'] != value['snapshot_source_seq']):
                raise ValueError('source binding')
            observed = build_context_preview(snaprow)
            if value['items'] != observed['items'] or value['total_size_bytes'] != observed['total_size_bytes']:
                raise ValueError('context differs from committed source')
            if value['task_ref']:
                self._context_task(resources, value['task_ref'], value['project_id'], value['project_revision'], value['created_by'])
        except (ValueError, PyHError):
            raise_code('PERS-202', reason='context_definition_binding')

    def _context_quota(self, resources, kind, project_id):
        # revision continuity makes the sum count ALL historical commits, not latest IDs.
        if sum(row[kind]['revision'] for row in resources.values()
               if kind in row and row[kind]['project_id'] == project_id) >= self.MAX_CONTEXT_HISTORY:
            raise_code('EVT-100', reason='context_history_limit')

    @staticmethod
    def _context_envelope(kind, ident, revision, command_id, actor, request_digest, digest='0'*64):
        return WorkspaceResourceCommittedPayload(resource_kind=kind, resource_id=ident, revision=revision,
            parent_revision=revision - 1, blob_ref=digest, digest=digest, command_id=command_id,
            actor_ref=actor, request_digest=request_digest).model_dump()

    async def _append_context(self, definition, kind, ident, command_id, actor, request_digest, gate):
        return await self._append_context_projected(definition,kind,ident,command_id,actor,request_digest,gate,self._grant_state)

    async def _append_context_projected(self, definition, kind, ident, command_id, actor, request_digest, gate, read_state):
        from pyharness.application.workspace_store import canonical, digest_of
        raw = canonical(definition.model_dump())
        limit = self.MAX_CONTEXT_BYTES if kind in {'context_manifest','workspace_snapshot','workspace_input','document','delivery_definition','index_repair','task_graph','task_graph_run','experiment_report','delivery_package','role_handoff','delivery_acceptance','delivery_export','writeback_grant','merge_plan','writeback_application'} else self.MAX_BLOB_BYTES
        if len(raw) > limit:
            raise_code('EVT-100', reason='context_blob_limit')
        digest = digest_of(raw)
        payload = self._context_envelope(kind, ident, definition.revision, command_id, actor, request_digest, digest)
        path = self._blob(digest)
        try:
            if path.exists():
                if checked_private_bytes(path,limit=limit) != raw:
                    raise_code('PERS-202', reason='workspace_blob_digest')
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                TenantSettingsStore._atomic_write(path, raw)
            # Never append a permanent reference to an unconfirmed immutable blob.
            if checked_private_bytes(path,limit=limit) != raw:
                raise_code('PERS-202', reason='workspace_blob_unconfirmed')
        except OSError:
            raise_code('PERS-202', reason='workspace_blob_write')
        # Disk/account/root I/O may cross expiry or revocation. Recheck after blob write.
        gate()
        try:
            event = await self._session.append('workspace.resource_committed', payload, actor='user', sync=True)
            self._verify_commit(self._store, event)
            resources, _ = read_state()
            return resources, resources[ident]
        except OSError:
            self._failed = True
            raise_code('PERS-202', reason='workspace_commit_unconfirmed')
        except BaseException:
            self._failed = True
            raise

    def _task_reference_summary(self, resources, row):
        value = {k: v for k, v in row['task_reference'].items() if k != 'created_by'}
        parent = resources[value['project_id']]['project']
        current = (parent['revision'] == value['project_revision']
                   and resources[value['product_task_id']]['task_reference']['revision'] == value['revision'])
        status = 'retired' if value['retired'] else ('current' if current else 'stale')
        return {**value, 'digest': row['digest'], 'source_seq': row['source_seq'], 'created_at': row['created_at'],
                'status': status, 'executable': False}

    async def save_context_task_reference(self, project_id, command, *, command_id, authorize):
        from pyharness.application.workspace_store import canonical, digest_of
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            request_digest = digest_of(canonical({'operation': 'context_task_reference', 'project_id': project_id, 'command': command.model_dump()}))
            self._context_envelope('task_definition', command.product_task_id, command.expected_revision + 1, command_id, actor, request_digest)
            resources, commands = self._grant_state()
            self._context_project(resources, project_id)
            old = commands.get((actor, command_id))
            if old:
                if old[0] != request_digest:
                    raise_code('EVT-100', reason='idempotency_conflict')
                current = self._context_owned(resources, command.product_task_id, 'task_reference', project_id, actor)
                if current['task_reference']['retired']:
                    # The retirement command itself has a safe exact receipt.
                    if not old[1]['task_reference']['retired']:
                        raise_code('GRD-403', reason='context_task_retired')
                self._context_finish(authorize, actor, resources)
                return self._task_reference_summary(resources, old[1])
            prior = resources.get(command.product_task_id)
            if prior:
                self._context_owned(resources, command.product_task_id, 'task_reference', project_id, actor)
            definition = TaskContextReference(project_id=project_id, project_revision=command.project_revision,
                product_task_id=command.product_task_id, revision=command.expected_revision + 1,
                title=command.title, retired=command.retired, created_by=actor)
            def gate():
                if authorize() != actor:
                    raise_code('GRD-403', reason='context_subject_changed')
                fresh, _ = self._grant_state()
                self._context_project(fresh, project_id, command.project_revision)
                previous = fresh.get(command.product_task_id)
                if previous:
                    self._context_owned(fresh, command.product_task_id, 'task_reference', project_id, actor)
                    if previous['task_reference']['retired']:
                        raise_code('GRD-403', reason='context_task_retired')
                if (previous['task_reference']['revision'] if previous else 0) != command.expected_revision:
                    raise_code('EVT-101', reason='stale_revision')
                self._context_quota(fresh, 'task_reference', project_id)
                self._context_finish(authorize, actor, fresh)
            gate()
            resources, row = await self._append_context(definition, 'task_definition', command.product_task_id,
                command_id, actor, request_digest, gate)
            try: self._context_finish(authorize,actor,resources)
            except PyHError: raise_code('PERS-202',reason='workspace_commit_receipt_unavailable')
            return self._task_reference_summary(resources, row)

    async def context_task_references(self, project_id, *, authorize):
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            self._context_project(resources, project_id)
            if authorize() != actor:
                raise_code('GRD-403', reason='context_subject_changed')
            return [self._task_reference_summary(resources, row) for row in resources.values()
                    if 'task_reference' in row and row['task_reference']['project_id'] == project_id
                    and row['task_reference']['created_by'] == actor]

    async def context_task_reference(self, project_id, task_id, revision, *, authorize):
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            self._context_project(resources, project_id)
            current = self._context_owned(resources, task_id, 'task_reference', project_id, actor)['task_reference']
            if current['retired'] and current['revision'] != revision:
                raise_code('GRD-403', reason='context_task_retired')
            for event in self._session.events_after(0):
                if (event.type == 'workspace.resource_committed' and event.payload['resource_kind'] == 'task_definition'
                        and event.payload['resource_id'] == task_id and event.payload['revision'] == revision):
                    value = self._read_definition(event.payload).model_dump()
                    row = {'task_reference': value, 'digest': event.payload['digest'], 'source_seq': event.seq,
                           'created_at': event.ts}
                    self._context_finish(authorize, actor, resources)
                    return self._task_reference_summary(resources, row)
            raise_code('GRD-403', reason='context_resource_unavailable')

    def _manifest_access(self, resources, row, actor):
        value = row['manifest']
        if value['schema_version']!=1:
            raise_code('GRD-403',reason='context_resource_unavailable')
        self._context_owned({value['manifest_id']: row}, value['manifest_id'], 'manifest', value['project_id'], actor)
        self._context_project(resources, value['project_id'])
        if value['task_ref']:
            self._context_task(resources, value['task_ref'], value['project_id'], value['project_revision'], actor, exact=False)
        # Root metadata and UTC check come LAST after reading all private definitions.
        self._snapshot_grant(resources, value['project_id'], value['grant_id'])

    def _manifest_summary(self, resources, row):
        value = {k: v for k, v in row['manifest'].items() if k != 'created_by'}
        reasons = []
        if resources[value['project_id']]['project']['revision'] != value['project_revision']:
            reasons.append('project_revision_changed')
        if value['task_ref'] and resources[value['task_ref']['product_task_id']]['task_reference']['revision'] != value['task_ref']['revision']:
            reasons.append('task_revision_changed')
        if resources[value['manifest_id']]['manifest']['revision'] != value['revision']:
            reasons.append('manifest_revision_superseded')
        return {**value, 'items': [] if reasons else value['items'], 'file_count': len(value['items']),
                'digest': row['digest'], 'source_seq': row['source_seq'], 'created_at': row['created_at'],
                'status': 'stale' if reasons else 'current', 'stale_reasons': reasons,
                'binding_mode': 'context_task_reference' if value['task_ref'] else 'project_only', 'executable': False}

    async def save_context_manifest(self, project_id, command, *, command_id, authorize):
        from pyharness.application.workspace_store import canonical, digest_of
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            request_digest = digest_of(canonical({'operation': 'context_manifest', 'project_id': project_id, 'command': command.model_dump()}))
            self._context_envelope('context_manifest', command.manifest_id, command.expected_revision + 1, command_id, actor, request_digest)
            resources, commands = self._grant_state()
            self._context_project(resources, project_id)
            old = commands.get((actor, command_id))
            if old:
                if old[0] != request_digest:
                    raise_code('EVT-100', reason='idempotency_conflict')
                if authorize() != actor:
                    raise_code('GRD-403', reason='context_subject_changed')
                self._manifest_access(resources, old[1], actor)
                self._context_finish(authorize, actor, resources, (old[1]['manifest']['grant_id'],))
                return self._manifest_summary(resources, old[1])
            snapshot_row = resources.get(command.snapshot_id)
            snapshot = snapshot_row.get('snapshot') if snapshot_row else None
            if not snapshot or snapshot['project_id'] != project_id or snapshot['grant_id'] != command.grant_id:
                raise_code('GRD-403', reason='source_snapshot_unavailable')
            observed = build_context_preview(snapshot_row)
            definition = ContextManifest(manifest_id=command.manifest_id, revision=command.expected_revision + 1,
                project_id=project_id, project_revision=command.project_revision, grant_id=command.grant_id,
                grant_revision=command.grant_revision, snapshot_id=command.snapshot_id, snapshot_digest=command.snapshot_digest,
                snapshot_source_seq=snapshot_row['source_seq'], task_ref=command.task_ref,
                items=observed['items'], total_size_bytes=observed['total_size_bytes'], created_by=actor)
            def gate():
                if authorize() != actor:
                    raise_code('GRD-403', reason='context_subject_changed')
                fresh, _ = self._grant_state()
                self._context_project(fresh, project_id, command.project_revision)
                previous = fresh.get(command.manifest_id)
                if previous:
                    self._context_owned(fresh, command.manifest_id, 'manifest', project_id, actor)
                    if previous['manifest']['schema_version']!=1:
                        raise_code('EVT-101',reason='context_schema_identity')
                    if task_identity(previous['manifest']['task_ref']) != task_identity(definition.model_dump()['task_ref']):
                        raise_code('EVT-101', reason='context_target_identity')
                if (previous['manifest']['revision'] if previous else 0) != command.expected_revision:
                    raise_code('EVT-101', reason='stale_revision')
                self._context_quota(fresh, 'manifest', project_id)
                current_snapshot = fresh.get(command.snapshot_id, {})
                if current_snapshot.get('digest') != command.snapshot_digest:
                    raise_code('EVT-101', reason='snapshot_digest_changed')
                if snapshot['grant_revision'] != command.grant_revision:
                    raise_code('EVT-101', reason='context_grant_revision')
                if command.task_ref:
                    self._context_task(fresh, command.task_ref.model_dump(), project_id, command.project_revision, actor)
                self._snapshot_grant(fresh, project_id, command.grant_id, command.grant_revision)
                self._context_finish(authorize, actor, fresh, (command.grant_id,))
            gate()
            resources, row = await self._append_context(definition, 'context_manifest', command.manifest_id,
                command_id, actor, request_digest, gate)
            return self._manifest_summary(resources, row)

    async def context_manifest(self, project_id, manifest_id, revision, *, authorize):
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            self._context_owned(resources, manifest_id, 'manifest', project_id, actor)
            row = None
            for event in self._session.events_after(0):
                if (event.type == 'workspace.resource_committed' and event.payload['resource_kind'] == 'context_manifest'
                        and event.payload['resource_id'] == manifest_id and event.payload['revision'] == revision):
                    definition = self._read_definition(event.payload)
                    row = {'manifest': definition.model_dump(), 'digest': event.payload['digest'], 'source_seq': event.seq,
                           'created_at': event.ts, 'created_by': event.payload['actor_ref']}
                    break
            if row is None:
                raise_code('GRD-403', reason='context_resource_unavailable')
            if authorize() != actor:
                raise_code('GRD-403', reason='context_subject_changed')
            self._manifest_access(resources, row, actor)
            self._context_finish(authorize, actor, resources, (row['manifest']['grant_id'],))
            return self._manifest_summary(resources, row)

    async def context_manifests(self, project_id, *, authorize):
        from pyharness.errors import PyHError
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            self._context_project(resources, project_id)
            result = []
            for row in resources.values():
                if ('manifest' not in row or row['manifest']['schema_version']!=1 or row['manifest']['project_id'] != project_id
                        or row['manifest']['created_by'] != actor):
                    continue
                if authorize() != actor:
                    raise_code('GRD-403', reason='context_subject_changed')
                try:
                    self._manifest_access(resources, row, actor)
                except PyHError as exc:
                    if exc.code == 'GRD-403':
                        continue  # exact GET remains denied; list reveals no withdrawn source metadata
                    raise
                item = self._manifest_summary(resources, row)
                item.pop('items')
                result.append(item)
            # Later rows' blocking root checks must not let earlier rows outlive grants.
            self._context_finish(authorize, actor, resources, (item['grant_id'] for item in result))
            return result
