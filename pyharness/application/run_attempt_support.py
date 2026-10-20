"""Historical attempt bindings and currently authorized exact receipts."""
from pyharness.application.run_attempt_contracts import AttemptContext
from pyharness.errors import PyHError, raise_code


class RunAttemptSupport:
    @staticmethod
    def _attempt_binding_digest(prerequisites, context):
        from pyharness.application.workspace_store import canonical, digest_of
        return digest_of(canonical({'prerequisites': prerequisites, 'context': context}))

    def _attempt_context_refs(self, resources, project, selection, actor):
        documents, citations = {}, {}
        revision = self._context_project(resources, project)['revision']
        def document(ref):
            row = self._s05_exact(resources, project, 'document', ref, 'document_id', actor)
            value = row['document']
            if value['retired'] or value['project_revision'] != revision or value['capability'] != 'locatable':
                raise_code('EVT-101', reason='attempt_document_not_current')
            prior = documents.get(ref['document_id'])
            if prior is not None and prior != ref:
                raise_code('EVT-101', reason='attempt_document_version_conflict')
            documents[ref['document_id']] = ref
        def citation(ref):
            row = self._s05_exact(resources, project, 'citation', ref, 'citation_id', actor)
            if row['citation']['project_revision'] != revision:
                raise_code('EVT-101', reason='attempt_citation_not_current')
            self._citation_document(resources, project, row['citation']['locator'], actor, current=True)
            document(row['citation']['locator']['document_ref'])
            citations[ref['citation_id']] = ref
        for ref in selection['document_refs']:document(ref)
        for ref in selection['citation_refs']:citation(ref)
        if selection['delivery_ref'] is not None:
            delivery = self._s05_exact(resources, project, 'delivery_definition', selection['delivery_ref'], 'delivery_id', actor)
            value = delivery['delivery_definition']
            if value['retired'] or value['project_revision'] != revision or value['task_ref'] != selection['task_ref']:
                raise_code('EVT-101', reason='attempt_delivery_binding')
            _, rows = self._delivery_dependencies(resources, value, actor, current=True)
            for row in rows:citation(self._s05_ref(row, 'citation_id', 'citation'))
        if len(documents) > 20 or len(citations) > 20:
            raise_code('EVT-100', reason='attempt_context_limit')
        return AttemptContext(document_refs=[documents[k] for k in sorted(documents)],
            citation_refs=[citations[k] for k in sorted(citations)], delivery_ref=selection['delivery_ref'])

    def _validate_run_attempt_definition(self, definition, resources, previous, event):
        value = definition.model_dump(); p = value['prerequisites']; actor = value['created_by']
        try:
            if previous is not None or p['tenant_id'] != self.tenant_id or actor != event.payload['actor_ref']:
                raise ValueError('immutable attempt owner')
            if value['submission_key'] != event.payload['command_id']:
                raise ValueError('submission key mismatch')
            self._context_project(resources, value['project_id'], value['project_revision'])
            self._context_quota(resources, 'run_attempt', value['project_id'])
            project = resources[value['project_id']]
            if p['project_digest'] != project['digest'] or p['project_source_seq'] != project['source_seq']:
                raise ValueError('project binding mismatch')
            task = self._s05_exact(resources, value['project_id'], 'product_task', p['task_ref'], 'product_task_id', actor)
            t = task['product_task']
            if t['retired'] or t['input_binding'] is None or t['input_binding'] != p['input_binding'] or t['project_revision'] != value['project_revision']:
                raise ValueError('task/input mismatch')
            wi = self._task_bound_input(resources, t, actor)['workspace_input']
            if any(resources[g]['grant']['revoked'] for g in self._input_grant_ids(wi)):
                raise ValueError('withdrawn input')
            self._input_manifest(resources, value['project_id'], wi['manifest_ref'], actor,
                                 manifest_schema=1 if wi['schema_version'] == 2 else 2)
            if wi['schema_version'] == 1:
                source = resources[wi['source_ref']['snapshot_id']]['workspace_snapshot']
                if self._workspace_latest(resources, source) != wi['source_ref']['snapshot_id']:
                    raise ValueError('superseded workspace snapshot')
            if self._attempt_context_refs(resources, value['project_id'], value['selection'], actor).model_dump() != value['context']:
                raise ValueError('context binding mismatch')
            for ref in value['context']['document_refs']:
                doc = resources[ref['document_id']]['document']
                if any(resources[g]['grant']['revoked'] for g in self._document_grants(resources, doc, actor)):
                    raise ValueError('withdrawn document input')
            from pyharness.application.continuation_binding import attempt_authority_digest,validate_continued_attempt
            if attempt_authority_digest(value) != value['binding_digest']:
                raise ValueError('binding digest mismatch')
            if value['schema_version']==2:validate_continued_attempt(self,definition,resources,event)
            if value['schema_version']==3:
                from pyharness.application.task_graph_binding import validate_graph_attempt
                validate_graph_attempt(self,definition,resources,event)
            from pyharness.application.workspace_store import canonical, digest_of
            request = digest_of(canonical({'operation': 'run_attempt.submit', 'project_id': value['project_id'], 'command': value['selection']}))
            if request != event.payload['request_digest']:
                raise ValueError('command digest mismatch')
        except (ValueError, KeyError, PyHError):
            raise_code('PERS-202', reason='run_attempt_definition_binding')

    def _attempt_observe(self, resources, prerequisites, context, actor, authorize, started):
        """Exact captured inputs only; current checks may label stale but never rebase."""
        p = prerequisites; project = p['project_id']; ref = p['task_ref']
        task = self._product_task_revision(resources, project, ref['product_task_id'], ref['revision'], actor)
        if self._s05_ref(task, 'product_task_id', 'product_task') != ref or task['product_task']['input_binding'] != p['input_binding']:
            raise_code('PERS-202', reason='attempt_task_binding')
        reasons = self._task_access(resources, task, actor, authorize, started)
        wi = self._task_bound_input(resources, task['product_task'], actor)['workspace_input']
        gate = lambda: self._input_gate(resources, wi, actor, authorize, started, current=False)
        grants = set(self._input_grant_ids(wi))
        for item in wi['files']:self._read_input_blob(item, gate)
        for ref in context['document_refs']:
            row = self._s05_history(resources, project, ref['document_id'], ref['revision'], 'document', actor)
            if self._s05_ref(row, 'document_id', 'document') != ref:
                raise_code('PERS-202', reason='attempt_document_binding')
            check = lambda: self._document_gate(resources, row, actor, authorize, started)
            self._document_raw(row['document'], check)
            reasons.extend('document_' + r for r in check())
            grants.update(self._document_grants(resources, row['document'], actor))
        for ref in context['citation_refs']:
            row = self._s05_exact(resources, project, 'citation', ref, 'citation_id', actor)
            observed = self._citation_summary(resources, row, actor, authorize, started)
            if observed['status'] != 'current':reasons.append('citation_' + observed['status'])
        if context['delivery_ref'] is not None:
            ref = context['delivery_ref']
            row = self._s05_history(resources, project, ref['delivery_id'], ref['revision'], 'delivery_definition', actor)
            if self._s05_ref(row, 'delivery_id', 'delivery_definition') != ref:
                raise_code('PERS-202', reason='attempt_delivery_binding')
            observed = self._delivery_summary(resources, row, actor, authorize, started)
            if observed['status'] != 'defined':reasons.append('delivery_' + observed['status'])
        gate(); self._task_access(resources, task, actor, authorize, started)
        self._context_finish(authorize, actor, resources, grants)
        return list(dict.fromkeys(reasons)), grants

    def _attempt_summary(self, resources, row, actor, authorize, started):
        value = row['run_attempt']; project = value['project_id']
        self._context_owned({value['attempt_id']: row}, value['attempt_id'], 'run_attempt', project, actor)
        p = value['prerequisites']
        reasons, grants = self._attempt_observe(resources, p, value['context'], actor, authorize, started)
        result = {k: v for k, v in value.items() if k not in {'created_by', 'prerequisites'}}
        result['prerequisites'] = {k: v for k, v in p.items() if k not in {'tenant_id', 'subject_ref'}}
        self._context_finish(authorize, actor, resources, grants)
        return {**result, 'digest': row['digest'], 'source_seq': row['source_seq'], 'created_at': row['created_at'],
                'request_digest': row['request_digest'], 'status': 'reserved', 'persisted': True,
                'attempt_reserved': True, 'execution_authorized': False, 'execution_started': False,
                'input_readiness': 'stale' if reasons else 'current', 'stale_reasons': reasons}
