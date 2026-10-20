"""Durable reservations and read-only unknown-commit reconciliation on the sole log."""
import time
from pyharness.application.run_attempt_contracts import RunAttempt
from pyharness.application.run_attempt_support import RunAttemptSupport
from pyharness.application.workspace_input_pages import workspace_input_page
from pyharness.errors import PyHError, raise_code
from pyharness.persistence import detect_truncation, session_data_paths


class RunAttemptStore(RunAttemptSupport):
    async def _attempt_read_state(self):
        if self._closed:
            raise_code('EVT-104', reason='workspace_closed')
        if not self._failed:
            await self._open(initialize=False)
        elif self._store is None or self._session is None or self._lease is None:
            raise_code('PERS-202', reason='workspace_reopen_required')
        for path in session_data_paths(self.log_path):
            if detect_truncation(path) is not None:
                raise_code('PERS-202', reason='workspace_log_incomplete')
        events = list(self._store.replay())
        if self._store._quarantine:
            raise_code('PERS-202', reason='workspace_log_corrupt')
        self._validate_log(events)
        if not self._failed and [e.model_dump() for e in events] != [e.model_dump() for e in self._session.events_after(0)]:
            raise_code('PERS-202', reason='workspace_log_changed')
        # Deliberately do not clear _failed or change the SessionLog write cache.
        resources, commands = self._project(events)
        return resources, commands, events

    async def submit_run_attempt(self, project, command, *, command_id, authorize):
        from pyharness.application.workspace_store import canonical, digest_of
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False)
            resources, commands = self._grant_state(); self._context_project(resources, project)
            selection = command.model_dump()
            request = digest_of(canonical({'operation': 'run_attempt.submit', 'project_id': project, 'command': selection}))
            self._context_envelope('run_attempt', command.attempt_id, 1, command_id, actor, request)
            old = commands.get((actor, command_id))
            if old:
                if old[0] != request:raise_code('EVT-100', reason='idempotency_conflict')
                return self._attempt_summary(resources, old[1], actor, authorize, started)
            from pyharness.application.task_graph_binding import reserved_graph_identities,graph_attempt_capacity
            reserved,_=reserved_graph_identities(resources)
            if command.attempt_id in reserved:raise_code('GRD-403',reason='graph_attempt_identity_reserved')
            graph_attempt_capacity(self,resources,project,additional=1)
            if command.attempt_id in resources:
                raise_code('EVT-101', reason='attempt_id_already_reserved')
            p = self._checked_task_prerequisites(resources, project, selection['task_ref'], actor, authorize, started)
            context = self._attempt_context_refs(resources, project, selection, actor)
            definition = RunAttempt(attempt_id=command.attempt_id, project_id=project,
                project_revision=p.project_revision, created_by=actor, submission_key=command_id,
                selection=command, prerequisites=p, context=context,
                binding_digest=self._attempt_binding_digest(p.model_dump(), context.model_dump()))
            def gate():
                if authorize() != actor:raise_code('GRD-403', reason='attempt_subject_changed')
                fresh, _ = self._grant_state()
                if command.attempt_id in fresh:raise_code('EVT-101', reason='attempt_id_already_reserved')
                reserved,_=reserved_graph_identities(fresh)
                if command.attempt_id in reserved:raise_code('GRD-403',reason='graph_attempt_identity_reserved')
                graph_attempt_capacity(self,fresh,project,additional=1)
                self._context_quota(fresh, 'run_attempt', project)
                checked = self._checked_task_prerequisites(fresh, project, selection['task_ref'], actor, authorize, started)
                exact = self._attempt_context_refs(fresh, project, selection, actor)
                if checked != p or exact != context:raise_code('EVT-101', reason='attempt_binding_changed')
                reasons, grants = self._attempt_observe(fresh, p.model_dump(), context.model_dump(), actor, authorize, started)
                if reasons:raise_code('EVT-101', reason='attempt_context_stale')
                self._context_finish(authorize, actor, fresh, grants)
            gate()
            resources, row = await self._append_context(definition, 'run_attempt', command.attempt_id, command_id, actor, request, gate)
            try:return self._attempt_summary(resources, row, actor, authorize, started)
            except PyHError:raise_code('PERS-202', reason='workspace_commit_receipt_unavailable')

    async def run_attempt(self, project, ident, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, _, _ = await self._attempt_read_state()
            self._context_project(resources, project)
            row = self._context_owned(resources, ident, 'run_attempt', project, actor)
            return self._attempt_summary(resources, row, actor, authorize, started)

    async def run_submission(self, project, key, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, commands, _ = await self._attempt_read_state()
            self._context_project(resources, project)
            command = commands.get((actor, key))
            row = command[1] if command else None
            if row is not None and (not row.get('run_attempt') or row['run_attempt']['project_id'] != project):
                raise_code('GRD-403', reason='submission_unavailable')
            result = self._attempt_summary(resources, row, actor, authorize, started) if row else None
            self._context_finish(authorize, actor, resources)
            return {'schema_version': 1, 'project_id': project, 'submission_key': key,
                    'status': 'committed' if row else 'not_found', 'attempt': result,
                    'writer_requires_reopen': self._failed, 'execution_authorized': False}

    async def run_attempts(self, project, query, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, _, events = await self._attempt_read_state()
            self._context_project(resources, project); rows = []; grants = set()
            for row in resources.values():
                value = row.get('run_attempt')
                if not value or value['project_id'] != project or value['created_by'] != actor:continue
                if authorize() != actor:raise_code('GRD-403', reason='attempt_subject_changed')
                try:summary = self._attempt_summary(resources, row, actor, authorize, started)
                except PyHError as exc:
                    if exc.code == 'GRD-403':continue
                    raise
                rows.append(summary)
                wi = resources[value['prerequisites']['input_binding']['input_ref']['input_id']]['workspace_input']
                grants.update(self._input_grant_ids(wi))
                for ref in value['context']['document_refs']:
                    doc = self._s05_history(resources, project, ref['document_id'], ref['revision'], 'document', actor)
                    grants.update(self._document_grants(resources, doc['document'], actor))
            result = workspace_input_page(rows, query, kind='attempts', project=project, actor=actor, watermark=len(events))
            self._context_finish(authorize, actor, resources, grants)
            if time.monotonic() - started >= self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104', reason='attempt_observation_time_limit')
            return result
