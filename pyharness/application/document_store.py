"""Versioned reading on the existing owner; private snapshots never execute content."""
import time
from pathlib import Path
from pyharness.application.document_contracts import DocumentRevision
from pyharness.application.document_support import DocumentSupport, FILE_METADATA_KEYS
from pyharness.application.document_text import parse_text
from pyharness.application.document_pdf import parse_pdf
from pyharness.application.source_snapshot import read_selected, check_content
from pyharness.application.workspace_input_pages import workspace_input_page
from pyharness.core.sandbox import safe_path
from pyharness.errors import PyHError, raise_code


class DocumentStore(DocumentSupport):
    def _pdf_preview(self, resources, project, command, actor, authorize):
        self._context_project(resources, project, command.project_revision)
        grant = self._snapshot_grant(resources, project, command.grant_id, command.grant_revision)
        self._workspace_storage_geometry(grant, None)
        if not command.path.lower().endswith('.pdf'):
            raise_code('EVT-100', reason='document_pdf_required')
        check_content(command.path.encode())
        try:
            metadata = self._document_metadata(grant['grant']['canonical_root'], command.path)
        except OSError:
            raise_code('GRD-403', reason='document_source_unavailable')
        if metadata['size_bytes'] > 2 * 1024**2:
            raise_code('EVT-100', reason='document_pdf_size_limit')
        self._snapshot_grant(resources, project, command.grant_id, command.grant_revision)
        self._context_finish(authorize, actor, resources, (command.grant_id,))
        return grant, metadata, self._pdf_confirmation(project, command.project_revision, actor, grant, metadata)

    async def document_pdf_preview(self, project, command, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, _ = self._grant_state()
            _, metadata, digest = self._pdf_preview(resources, project, command, actor, authorize)
            if time.monotonic() - started >= self.MAX_WORKSPACE_CAPTURE_SECONDS:
                raise_code('EVT-104', reason='document_observation_time_limit')
            return {'schema_version': 1, 'project_id': project, 'project_revision': command.project_revision,
                    'grant_id': command.grant_id, 'grant_revision': command.grant_revision,
                    'path': command.path, 'size_bytes': metadata['size_bytes'], 'confirmation_digest': digest,
                    'status': 'confirmation_required', 'policy': 'private-pdf-text-only-v1', 'persisted': False}

    async def save_document(self, project, command, *, command_id, authorize):
        from pyharness.application.document_contracts import PdfPreview
        from pyharness.application.workspace_store import canonical, digest_of
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, commands = self._grant_state()
            request = digest_of(canonical({'operation': 'document', 'project_id': project, 'command': command.model_dump()}))
            ident = command.document_id
            self._context_envelope('document', ident, command.expected_revision+1, command_id, actor, request)
            self._context_project(resources, project, command.project_revision)
            if old := commands.get((actor, command_id)):
                if old[0] != request:
                    raise_code('EVT-100', reason='idempotency_conflict')
                gate = lambda: self._document_gate(resources, old[1], actor, authorize, started)
                self._document_raw(old[1]['document'], gate)
                return self._document_summary(old[1], gate())

            def base_gate():
                if authorize() != actor:
                    raise_code('GRD-403', reason='document_subject_changed')
                fresh, _ = self._grant_state()
                self._context_project(fresh, project, command.project_revision)
                prior = fresh.get(ident)
                if prior:
                    self._context_owned(fresh, ident, 'document', project, actor)
                    if prior['document']['schema_version']==2:raise_code('EVT-100',reason='handoff_document_immutable')
                    if prior['document']['retired']:
                        raise_code('GRD-403', reason='document_retired')
                if (prior['document']['revision'] if prior else 0) != command.expected_revision:
                    raise_code('EVT-101', reason='stale_revision')
                self._context_quota(fresh, 'document', project)
                self._context_finish(authorize, actor, fresh)
                return fresh

            base_gate(); source = command.source.model_dump()
            if source['kind'] == 'workspace_input':
                wi, item = self._document_input(resources, project, source, actor)
                value = wi['workspace_input']
                if value['project_revision'] != command.project_revision:
                    raise_code('EVT-101', reason='document_project_revision')
                def source_gate():
                    fresh = base_gate()
                    return self._input_gate(fresh, value, actor, authorize, started)
                raw = self._read_input_blob(item, source_gate)
                parsed = parse_text(raw)
                if value['source_ref']['source_kind'] == 'source_snapshot':
                    snapshot = resources[value['source_ref']['snapshot_id']]['snapshot']
                    observed = next(f for f in snapshot['files'] if f['path'] == item['path'])
                    source_metadata = {k: observed[k] for k in FILE_METADATA_KEYS}
                else:
                    grant_id = self._input_grant_ids(value)[0]
                    try:
                        source_metadata = self._document_metadata(resources[grant_id]['grant']['canonical_root'], item['path'])
                    except FileNotFoundError:
                        source_metadata = None
                    except OSError:
                        raise_code('GRD-403', reason='document_source_unavailable')
            else:
                preview = PdfPreview(project_revision=command.project_revision,
                    **{k: source[k] for k in ('grant_id', 'grant_revision', 'path')})
                grant, metadata, confirmation = self._pdf_preview(resources, project, preview, actor, authorize)
                if confirmation != source['confirmation_digest']:
                    raise_code('EVT-101', reason='document_confirmation_changed')
                def source_gate():
                    fresh = base_gate()
                    _, _, digest = self._pdf_preview(fresh, project, preview, actor, authorize)
                    if digest != confirmation:
                        raise_code('EVT-101', reason='document_confirmation_changed')
                file, raw = read_selected(Path(grant['grant']['canonical_root']), source['path'], before_read=source_gate)
                if {k: getattr(file, k) for k in FILE_METADATA_KEYS} != metadata:
                    raise_code('EVT-101', reason='document_confirmation_changed')
                parsed = await parse_pdf(safe_path(self.root, 'document-staging'), raw)
                source_gate()
                source = {'kind': 'pdf_grant', 'grant_id': preview.grant_id, 'grant_revision': preview.grant_revision,
                          'grant_digest': grant['digest'], 'grant_source_seq': grant['source_seq'],
                          'root_identity': grant['grant']['root_identity'], 'file': file.model_dump(),
                          'confirmation_digest': confirmation}
                source_metadata = metadata
                self._write_snapshot_blob(self._snapshot_content(file.sha256), raw)
            definition = DocumentRevision(document_id=ident, revision=command.expected_revision+1,
                project_id=project, project_revision=command.project_revision, created_by=actor,
                title=command.title, source=source, source_metadata=source_metadata,
                content_digest=digest_of(raw), size_bytes=len(raw),
                retired=command.retired, **parsed)
            source_gate()
            resources, row = await self._append_context(definition, 'document', ident, command_id, actor, request, source_gate)
            try:
                gate = lambda: self._document_gate(resources, row, actor, authorize, started)
                self._document_raw(row['document'], gate)
                return self._document_summary(row, gate())
            except PyHError:
                raise_code('PERS-202', reason='workspace_commit_receipt_unavailable')

    async def document(self, project, ident, revision, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, _ = self._grant_state()
            row = self._s05_history(resources, project, ident, revision, 'document', actor)
            gate = lambda: self._document_gate(resources, row, actor, authorize, started)
            self._document_raw(row['document'], gate)
            return self._document_summary(row, gate())

    async def documents(self, project, query, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, _ = self._grant_state()
            self._context_project(resources, project)
            rows = []; retained = []
            for row in resources.values():
                value = row.get('document')
                if not value or value['project_id'] != project or value['created_by'] != actor:
                    continue
                if authorize() != actor:
                    raise_code('GRD-403', reason='document_subject_changed')
                gate = lambda row=row: self._document_gate(resources, row, actor, authorize, started)
                try:
                    self._document_raw(value, gate)
                    rows.append(self._document_summary(row, gate(), detail=False)); retained.append(row)
                except PyHError as exc:
                    if exc.code != 'GRD-403':
                        raise
            result = workspace_input_page(rows, query, kind='documents', project=project, actor=actor,
                                           watermark=sum(1 for _ in self._session.events_after(0)))
            grants = set()
            for row in retained:
                self._document_gate(resources, row, actor, authorize, started)
                grants.update(self._document_grants(resources, row['document'], actor))
            self._context_finish(authorize, actor, resources, grants)
            return result
