"""Formal task revisions on the sole control SessionLog, with exact input bindings."""
import time
from datetime import datetime, timezone

from pyharness.application.product_task_contracts import ProductTask, RunAttemptPrerequisites
from pyharness.errors import PyHError, raise_code


class ProductTaskStore:
    def _task_bound_input(self, resources, value, actor):
        binding = value['input_binding']
        row = self._context_owned(resources, binding['input_ref']['input_id'], 'workspace_input', value['project_id'], actor)
        wi = row['workspace_input']
        if (any(row[k] != binding['input_ref'][k] for k in ('digest', 'source_seq'))
                or wi['revision'] != binding['input_ref']['revision']):
            raise_code('EVT-101', reason='product_task_input_revision')
        if wi['manifest_ref'] != binding['manifest_ref'] or wi['project_revision'] != value['project_revision']:
            raise_code('EVT-101', reason='product_task_input_binding')
        return row

    def _validate_product_task_definition(self, definition, resources, previous, event):
        """Historical validation uses facts at event time, never today's wall-clock/roles."""
        value = definition.model_dump()
        try:
            self._context_project(resources, value['project_id'], value['project_revision'])
            self._context_quota(resources, 'product_task', value['project_id'])
            if value['created_by'] != event.payload['actor_ref']:
                raise ValueError('creator mismatch')
            if previous:
                prior = previous.get('product_task')
                if (not prior or prior['retired'] or prior['project_id'] != value['project_id']
                        or prior['created_by'] != value['created_by']):
                    raise ValueError('immutable task ownership or retired task')
            if value['input_binding']:
                wi = self._task_bound_input(resources, value, value['created_by'])['workspace_input']
                ref = value['input_binding']['manifest_ref']
                cm = self._context_owned(resources, ref['manifest_id'], 'manifest', value['project_id'], value['created_by'])
                if (cm['manifest']['revision'] != ref['revision']
                        or any(cm[k] != ref[k] for k in ('digest', 'source_seq'))
                        or any(resources[g]['grant']['revoked'] for g in self._input_grant_ids(wi))):
                    raise ValueError('stale or withdrawn task input')
        except (ValueError, KeyError, PyHError):
            raise_code('PERS-202', reason='product_task_definition_binding')

    def _task_access(self, resources, row, actor, authorize, started):
        value = row['product_task']
        self._context_owned({value['product_task_id']: row}, value['product_task_id'],
                            'product_task', value['project_id'], actor)
        project = self._context_project(resources, value['project_id'])
        reasons = []
        if project['revision'] != value['project_revision']:
            reasons.append('project_revision_changed')
        latest = resources[value['product_task_id']]['product_task']
        if latest['revision'] != value['revision']:
            reasons.append('task_revision_superseded')
        if latest['retired'] and not value['retired']:
            reasons.append('task_retired')
        if value['input_binding']:
            wi = self._task_bound_input(resources, value, actor)
            reasons.extend(self._input_gate(resources, wi['workspace_input'], actor, authorize, started, current=False))
        self._context_finish(authorize, actor, resources)
        return list(dict.fromkeys(reasons))

    def _product_task_summary(self, row, reasons):
        value = {k: v for k, v in row['product_task'].items() if k != 'created_by'}
        status = ('retired' if value['retired'] else 'stale' if reasons else
                  'definition_ready' if value['input_binding'] else 'draft')
        return {**value, 'digest': row['digest'], 'source_seq': row['source_seq'],
                'created_at': row['created_at'], 'status': status, 'stale_reasons': reasons,
                'executable': False, 'attempt_reserved': False}

    async def save_product_task(self, project_id, command, *, command_id, authorize):
        from pyharness.application.workspace_store import canonical, digest_of
        started = time.monotonic()
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, commands = self._grant_state()
            self._context_project(resources, project_id)
            request = digest_of(canonical({'operation': 'product_task', 'project_id': project_id,
                                           'command': command.model_dump()}))
            ident = command.product_task_id
            self._context_envelope('task_definition', ident, command.expected_revision + 1, command_id, actor, request)
            old = commands.get((actor, command_id))
            if old:
                if old[0] != request:
                    raise_code('EVT-100', reason='idempotency_conflict')
                reasons = self._task_access(resources, old[1], actor, authorize, started)
                return self._product_task_summary(old[1], reasons)
            prior = resources.get(ident)
            if prior:
                self._context_owned(resources, ident, 'product_task', project_id, actor)
            values = command.model_dump(exclude={'expected_revision'})
            definition = ProductTask(**values, project_id=project_id,
                                     revision=command.expected_revision + 1, created_by=actor)

            def gate():
                if authorize() != actor:
                    raise_code('GRD-403', reason='product_task_subject_changed')
                fresh, _ = self._grant_state()
                self._context_project(fresh, project_id, command.project_revision)
                previous = fresh.get(ident)
                if previous:
                    self._context_owned(fresh, ident, 'product_task', project_id, actor)
                    if previous['product_task']['retired']:
                        raise_code('GRD-403', reason='product_task_retired')
                if (previous['product_task']['revision'] if previous else 0) != command.expected_revision:
                    raise_code('EVT-101', reason='stale_revision')
                self._context_quota(fresh, 'product_task', project_id)
                value = definition.model_dump()
                if value['input_binding']:
                    wi = self._task_bound_input(fresh, value, actor)
                    self._input_gate(fresh, wi['workspace_input'], actor, authorize, started)
                self._context_finish(authorize, actor, fresh)

            gate()
            resources, row = await self._append_context(definition, 'task_definition', ident,
                                                        command_id, actor, request, gate)
            try:
                reasons = self._task_access(resources, row, actor, authorize, started)
            except PyHError:
                raise_code('PERS-202', reason='workspace_commit_receipt_unavailable')
            return self._product_task_summary(row, reasons)

    async def product_tasks(self, project_id, *, authorize):
        started = time.monotonic()
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            self._context_project(resources, project_id)
            result, grants = [], set()
            for row in resources.values():
                value = row.get('product_task')
                if not value or value['project_id'] != project_id or value['created_by'] != actor:
                    continue
                # Do not swallow an account/subject failure as a withdrawn input.
                if authorize() != actor:
                    raise_code('GRD-403', reason='product_task_subject_changed')
                try:
                    reasons = self._task_access(resources, row, actor, authorize, started)
                except PyHError as exc:
                    if exc.code == 'GRD-403':
                        continue
                    raise
                result.append(self._product_task_summary(row, reasons))
                if value['input_binding']:
                    wi = self._task_bound_input(resources, value, actor)['workspace_input']
                    grants.update(self._input_grant_ids(wi))
            self._context_finish(authorize, actor, resources, grants)
            return result

    def _product_task_revision(self, resources, project_id, task_id, revision, actor):
        self._context_owned(resources, task_id, 'product_task', project_id, actor)
        for event in self._history_events(resources):
            p = event.payload
            if (event.type == 'workspace.resource_committed' and p['resource_kind'] == 'task_definition'
                    and p['resource_id'] == task_id and p['revision'] == revision):
                definition = self._read_definition(p)
                return {'product_task': definition.model_dump(), 'digest': p['digest'],
                        'source_seq': event.seq, 'created_at': event.ts}
        raise_code('GRD-403', reason='product_task_unavailable')

    async def product_task(self, project_id, task_id, revision, *, authorize):
        started = time.monotonic()
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            row = self._product_task_revision(resources, project_id, task_id, revision, actor)
            reasons = self._task_access(resources, row, actor, authorize, started)
            return self._product_task_summary(row, reasons)

    def _checked_task_prerequisites(self, resources, project_id, ref, actor, authorize, started):
        """Shared predicate; caller holds the owner lock, never a cached permission token."""
        row = self._context_owned(resources, ref['product_task_id'], 'product_task', project_id, actor)
        value = row['product_task']
        if value['revision'] != ref['revision'] or any(row[k] != ref[k] for k in ('digest', 'source_seq')):
            raise_code('EVT-101', reason='product_task_revision')
        if value['retired'] or not value['input_binding']:
            raise_code('GRD-403', reason='product_task_not_ready')
        if self._task_access(resources, row, actor, authorize, started):
            raise_code('EVT-101', reason='product_task_stale')
        wi = self._task_bound_input(resources, value, actor)['workspace_input']
        gate = lambda: self._input_gate(resources, wi, actor, authorize, started)
        for item in wi['files']:
            self._read_input_blob(item, gate)
        fresh, _ = self._grant_state()
        if self._task_access(fresh, row, actor, authorize, started):
            raise_code('EVT-101', reason='product_task_stale')
        self._input_gate(fresh, wi, actor, authorize, started)
        project = fresh[project_id]
        return RunAttemptPrerequisites(tenant_id=self.tenant_id, subject_ref=actor,
            project_id=project_id, project_revision=value['project_revision'],
            project_digest=project['digest'], project_source_seq=project['source_seq'],
            task_ref=ref, input_binding=value['input_binding'])

    async def product_task_preflight(self, project_id, command, *, authorize):
        """Read-only observation; attempt submission invokes the same predicate anew."""
        from pyharness.application.workspace_store import canonical, digest_of
        started = time.monotonic()
        async with self._lock:
            actor = authorize()
            await self._open(initialize=False)
            resources, _ = self._grant_state()
            prerequisites = self._checked_task_prerequisites(resources, project_id, command.task_ref.model_dump(), actor, authorize, started)
            public = prerequisites.model_dump(exclude={'tenant_id', 'subject_ref'})
            return {'schema_version': 1, 'status': 'prerequisites_checked', 'prerequisites': public,
                    'binding_digest': digest_of(canonical(prerequisites.model_dump())),
                    'checked_at': datetime.now(timezone.utc).isoformat(), 'subject_bound': True,
                    'execution_authorized': False, 'attempt_reserved': False, 'persisted': False}
