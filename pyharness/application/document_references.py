"""Persisted exact citations and delivery definitions, without execution/acceptance."""
import time
from pyharness.application.document_contracts import CitationRevision, DeliveryRevision
from pyharness.application.workspace_input_pages import workspace_input_page
from pyharness.errors import PyHError, raise_code


class DocumentReferences:
    def _reference_grants(self, resources, row, kind, actor):
        if kind == 'citation':
            value = row[kind]
            doc, _, _ = self._citation_document(resources, value['project_id'], value['locator'], actor)
            return set(self._document_grants(resources, doc['document'], actor))
        value = row[kind]
        task, citations = self._delivery_dependencies(resources, value, actor, current=False)
        binding = task['product_task']['input_binding']
        grants = set()
        if binding:
            wi = self._task_bound_input(resources, task['product_task'], actor)['workspace_input']
            grants.update(self._input_grant_ids(wi))
        for citation in citations:
            grants.update(self._reference_grants(resources, citation, 'citation', actor))
        return grants

    def _citation_document(self, resources, project, locator, actor, *, current=False):
        ref = locator['document_ref']
        row = (self._s05_exact(resources, project, 'document', ref, 'document_id', actor) if current else
               self._s05_history(resources, project, ref['document_id'], ref['revision'], 'document', actor))
        if self._s05_ref(row, 'document_id', 'document') != ref:
            raise_code('EVT-101', reason='citation_document_revision')
        if current and resources[ref['document_id']]['document']['revision'] != ref['revision']:
            raise_code('EVT-101', reason='citation_document_superseded')
        block = next((b for b in row['document']['blocks'] if b['block_id'] == locator['block_id']), None)
        if block is None or not block['start'] <= locator['start'] < locator['end'] <= block['end']:
            raise_code('EVT-101', reason='citation_range_mismatch')
        quote = block['text'][locator['start']-block['start']:locator['end']-block['start']]
        from pyharness.application.workspace_store import digest_of
        if digest_of(quote.encode()) != locator['quote_digest']:
            raise_code('EVT-101', reason='citation_quote_mismatch')
        return row, block, quote

    def _validate_citation_definition(self, definition, resources, previous, event):
        value = definition.model_dump()
        try:
            self._s05_base(value, resources, previous, event, 'citation')
            row, _, _ = self._citation_document(resources, value['project_id'], value['locator'], value['created_by'], current=True)
            if row['document']['retired'] or row['document']['project_revision'] != value['project_revision']:
                raise ValueError('retired/stale citation source')
        except (ValueError, KeyError, PyHError):
            raise_code('PERS-202', reason='citation_definition_binding')

    def _citation_summary(self, resources, row, actor, authorize, started):
        value = row['citation']; project = value['project_id']
        self._context_owned({value['citation_id']: row}, value['citation_id'], 'citation', project, actor)
        doc, block, quote = self._citation_document(resources, project, value['locator'], actor)
        gate = lambda: self._document_gate(resources, doc, actor, authorize, started)
        self._document_raw(doc['document'], gate)
        observed = self._document_summary(doc, gate())
        result = {k: v for k, v in value.items() if k != 'created_by'}
        return {**result, 'digest': row['digest'], 'source_seq': row['source_seq'], 'created_at': row['created_at'],
                'status': observed['status'], 'stale_reasons': observed['stale_reasons'],
                'quote': None if observed['status'] == 'missing' else quote,
                'page': block['page'], 'paragraph': block['paragraph'], 'offset_unit': 'unicode-codepoint',
                'export_authorized': False}

    def _delivery_dependencies(self, resources, value, actor, *, current):
        project = value['project_id']; task_ref = value['task_ref']
        if current:
            task = self._s05_exact(resources, project, 'product_task', task_ref, 'product_task_id', actor)
        else:
            task = self._product_task_revision(resources, project, task_ref['product_task_id'], task_ref['revision'], actor)
            if self._s05_ref(task, 'product_task_id', 'product_task') != task_ref:
                raise_code('EVT-101', reason='delivery_task_revision')
        if task['product_task']['retired']:
            raise_code('GRD-403', reason='delivery_task_retired')
        if current and task['product_task']['project_revision'] != value['project_revision']:
            raise_code('EVT-101', reason='delivery_task_project_revision')
        refs = [ref for item in value['items'] for ref in item['citation_refs']]
        citations = [self._s05_exact(resources, project, 'citation', ref, 'citation_id', actor) for ref in refs]
        return task, citations

    def _validate_delivery_definition(self, definition, resources, previous, event):
        value = definition.model_dump()
        try:
            self._s05_base(value, resources, previous, event, 'delivery_definition')
            if len({x['item_id'] for x in value['items']}) != len(value['items']):
                raise ValueError('duplicate delivery item')
            task, citations = self._delivery_dependencies(resources, value, value['created_by'], current=True)
            if previous and previous['delivery_definition']['task_ref']['product_task_id'] != value['task_ref']['product_task_id']:
                raise ValueError('immutable delivery task identity')
            for citation in citations:
                self._citation_document(resources, value['project_id'], citation['citation']['locator'], value['created_by'], current=True)
        except (ValueError, KeyError, PyHError):
            raise_code('PERS-202', reason='delivery_definition_binding')

    def _delivery_summary(self, resources, row, actor, authorize, started):
        value = row['delivery_definition']; project = value['project_id']
        self._context_owned({value['delivery_id']: row}, value['delivery_id'], 'delivery_definition', project, actor)
        task, citations = self._delivery_dependencies(resources, value, actor, current=False)
        reasons = self._task_access(resources, task, actor, authorize, started)
        for citation in citations:
            summary = self._citation_summary(resources, citation, actor, authorize, started)
            if summary['status'] != 'current':
                reasons.append('citation_' + summary['status'])
        if resources[value['delivery_id']]['delivery_definition']['revision'] != value['revision']:
            reasons.append('delivery_revision_superseded')
        if resources[value['delivery_id']]['delivery_definition']['retired']:
            reasons.append('delivery_retired')
        result = {k: v for k, v in value.items() if k != 'created_by'}
        self._task_access(resources, task, actor, authorize, started)
        grants = self._reference_grants(resources, row, 'delivery_definition', actor)
        self._context_finish(authorize, actor, resources, grants)
        return {**result, 'digest': row['digest'], 'source_seq': row['source_seq'], 'created_at': row['created_at'],
                'status': 'retired' if 'delivery_retired' in reasons else 'stale' if reasons else 'defined',
                'stale_reasons': list(dict.fromkeys(reasons)), 'acceptance_status': 'not_evaluated',
                'execution_authorized': False, 'export_authorized': False}

    async def save_document_reference(self, project, kind, command, *, command_id, authorize):
        from pyharness.application.workspace_store import canonical, digest_of
        key, model = ('citation_id', CitationRevision) if kind == 'citation' else ('delivery_id', DeliveryRevision)
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, commands = self._grant_state()
            ident = getattr(command, key)
            request = digest_of(canonical({'operation': kind, 'project_id': project, 'command': command.model_dump()}))
            self._context_envelope(kind, ident, command.expected_revision+1, command_id, actor, request)
            self._context_project(resources, project)
            summarize = self._citation_summary if kind == 'citation' else self._delivery_summary
            if old := commands.get((actor, command_id)):
                if old[0] != request:
                    raise_code('EVT-100', reason='idempotency_conflict')
                return summarize(resources, old[1], actor, authorize, started)
            definition = model(**command.model_dump(exclude={'expected_revision'}),
                               revision=command.expected_revision+1, project_id=project, created_by=actor)
            value = definition.model_dump()

            def gate():
                if authorize() != actor:
                    raise_code('GRD-403', reason='document_subject_changed')
                fresh, _ = self._grant_state()
                self._context_project(fresh, project, command.project_revision)
                prior = fresh.get(ident)
                if prior:
                    self._context_owned(fresh, ident, kind, project, actor)
                    if kind == 'citation' or prior[kind].get('retired'):
                        raise_code('GRD-403', reason='document_reference_retired_or_immutable')
                if (prior[kind]['revision'] if prior else 0) != command.expected_revision:
                    raise_code('EVT-101', reason='stale_revision')
                self._context_quota(fresh, kind, project)
                if kind == 'citation':
                    doc, _, _ = self._citation_document(fresh, project, value['locator'], actor, current=True)
                    check = lambda: self._document_gate(fresh, doc, actor, authorize, started)
                    self._document_raw(doc['document'], check)
                    if check() or doc['document']['capability'] != 'locatable' or doc['document']['project_revision'] != command.project_revision:
                        raise_code('EVT-101', reason='citation_source_stale')
                else:
                    task, citations = self._delivery_dependencies(fresh, value, actor, current=True)
                    if prior and prior[kind]['task_ref']['product_task_id'] != value['task_ref']['product_task_id']:
                        raise_code('EVT-101', reason='delivery_task_identity')
                    if self._task_access(fresh, task, actor, authorize, started):
                        raise_code('EVT-101', reason='delivery_task_stale')
                    for citation in citations:
                        if self._citation_summary(fresh, citation, actor, authorize, started)['status'] != 'current':
                            raise_code('EVT-101', reason='delivery_citation_stale')
                    # Citation I/O can cross input expiry. Recheck the task last.
                    if self._task_access(fresh, task, actor, authorize, started):
                        raise_code('EVT-101', reason='delivery_task_stale')
                prospective = {kind: value}
                grants = self._reference_grants(fresh, prospective, kind, actor)
                self._context_finish(authorize, actor, fresh, grants)

            gate()
            resources, row = await self._append_context(definition, kind, ident, command_id, actor, request, gate)
            try:
                return summarize(resources, row, actor, authorize, started)
            except PyHError:
                raise_code('PERS-202', reason='workspace_commit_receipt_unavailable')

    async def document_reference(self, project, kind, ident, revision, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, _ = self._grant_state()
            row = self._s05_history(resources, project, ident, revision, kind, actor)
            summarize = self._citation_summary if kind == 'citation' else self._delivery_summary
            return summarize(resources, row, actor, authorize, started)

    async def document_references(self, project, kind, query, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, _ = self._grant_state()
            self._context_project(resources, project)
            summarize = self._citation_summary if kind == 'citation' else self._delivery_summary
            rows = []; retained = []
            for row in resources.values():
                value = row.get(kind)
                if not value or value['project_id'] != project or value['created_by'] != actor:
                    continue
                if authorize() != actor:
                    raise_code('GRD-403', reason='document_subject_changed')
                try:
                    summary = summarize(resources, row, actor, authorize, started)
                    # Lists are metadata only; the exact GET reauthorizes private text.
                    summary.pop('quote', None)
                    rows.append(summary); retained.append(row)
                except PyHError as exc:
                    if exc.code != 'GRD-403':
                        raise
            result = workspace_input_page(rows, query, kind='citations' if kind == 'citation' else 'deliveries',
                project=project, actor=actor, watermark=sum(1 for _ in self._session.events_after(0)))
            grants = set()
            for row in retained:
                summarize(resources, row, actor, authorize, started)
                grants.update(self._reference_grants(resources, row, kind, actor))
            self._context_finish(authorize, actor, resources, grants)
            return result
