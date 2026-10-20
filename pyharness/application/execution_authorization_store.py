"""Sole-log immutable authority. Repeated submissions only observe; never dispatch."""
import time
import uuid
from pyharness.application.execution_contracts import ExecutionAuthorization, ExecutionEnvironment
from pyharness.errors import PyHError, raise_code


class ExecutionAuthorizationStore:
    async def execution_for_attempt(self,project,ident,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            attempt=self._context_owned(resources,ident,'run_attempt',project,actor)
            self._attempt_summary(resources,attempt,actor,authorize,started)
            reference=self._s05_ref(attempt,'attempt_id','run_attempt')
            rows=[r for r in resources.values() if r.get('execution_authorization',{}).get('selection',{}).get('attempt_ref')==reference]
            if len(rows)>1:raise_code('PERS-202',reason='attempt_authorization_ambiguous')
            value=self._execution_summary(resources,rows[0],actor,authorize,started) if rows else None
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,attempt_ref=reference,
                status='authorized' if value else 'not_authorized',execution=value)

    async def _execution_read_state(self):
        """Full source validation over the same control bytes, without a writer/lease."""
        events=self._strict_control_events()
        resources,commands=self._project(events)
        from pyharness.application.execution_control_projection import ControlReadResources
        return ControlReadResources(resources,events),commands,events

    @staticmethod
    def _execution_request(project, command):
        from pyharness.application.workspace_store import canonical, digest_of
        from pyharness.application.continuation_execution_contracts import ContinuedStartExecution
        if isinstance(command,ContinuedStartExecution):
            return digest_of(canonical(dict(operation='execution.continue',project_id=project,command=command.continuation.command.model_dump())))
        return digest_of(canonical(dict(operation='execution.start', project_id=project, command=command.model_dump())))

    def _execution_attempt(self, resources, project, reference, actor):
        ref = reference.model_dump() if hasattr(reference, 'model_dump') else reference
        row = self._context_owned(resources, ref['attempt_id'], 'run_attempt', project, actor)
        if self._s05_ref(row, 'attempt_id', 'run_attempt') != ref:
            raise_code('EVT-101', reason='execution_attempt_reference')
        return row

    def _execution_confirmation(self, project, command, actor, attempt, environment):
        from pyharness.application.workspace_store import canonical, digest_of
        agent = command.agent_ref.model_dump()
        value = environment.model_dump()
        snapshot = value['agent_snapshot']
        import hashlib, json
        checksum = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        if (snapshot.get('status') != 'published' or snapshot.get('agent_id') != agent['agent_id']
                or snapshot.get('current_version') != agent['version'] or checksum != agent['sha256']):
            raise_code('EVT-101', reason='execution_agent_reference')
        return digest_of(canonical(dict(operation='execution.preview', tenant_id=self.tenant_id,
            actor_ref=actor, project_id=project, attempt_ref=command.attempt_ref.model_dump(),
            attempt_binding_digest=attempt['run_attempt']['binding_digest'], agent_ref=agent, environment=value)))

    def _execution_current(self, resources, project, command, actor, authorize, describe, started):
        row = self._execution_attempt(resources, project, command.attempt_ref, actor)
        from pyharness.application.continuation_binding import validate_continued_selection
        validate_continued_selection(command,row)
        from pyharness.application.task_graph_binding import validate_graph_selection
        validate_graph_selection(command,row)
        observed = self._attempt_summary(resources, row, actor, authorize, started)
        if observed['input_readiness'] != 'current':raise_code('EVT-101', reason='execution_context_stale')
        environment = ExecutionEnvironment.model_validate(describe())
        self._check_handoff_execution(resources,row,command,environment,actor,authorize)
        if row['run_attempt'].get('graph_node'):
            from pyharness.application.task_graph_binding import graph_node_plan
            _,plan=graph_node_plan(self,resources,project,row['run_attempt']['graph_node'],actor)
            if environment.model_dump()!=plan['environment']:raise_code('EVT-101',reason='graph_node_configuration_changed')
        confirmation = self._execution_confirmation(project, command, actor, row, environment)
        if authorize() != actor:raise_code('GRD-403', reason='execution_subject_changed')
        return row, environment, confirmation

    def _validate_execution_authorization(self, definition, resources, previous, event):
        value = definition.model_dump()
        try:
            if previous is not None:raise ValueError('immutable authorization')
            actor = value['created_by']; project = value['project_id']; command = definition.selection
            if actor != event.payload['actor_ref'] or value['submission_key'] != event.payload['command_id']:
                raise ValueError('subject or key mismatch')
            self._context_project(resources, project, value['project_revision'])
            row = self._execution_attempt(resources, project, command.attempt_ref, actor)
            from pyharness.application.continuation_binding import validate_continued_authorization
            validate_continued_authorization(self,definition,resources,row)
            from pyharness.application.task_graph_binding import validate_graph_authorization
            validate_graph_authorization(self,definition,resources,row)
            if (row['run_attempt']['project_revision'] != value['project_revision']
                    or row['run_attempt']['binding_digest'] != value['attempt_binding_digest']):
                raise ValueError('attempt binding mismatch')
            self._execution_historical_materials(resources, row['run_attempt'], actor)
            self._validate_handoff_execution_lease(resources,definition,row)
            self._context_quota(resources, 'execution_authorization', project)
            if any(r.get('execution_authorization', {}).get('selection', {}).get('attempt_ref') == command.attempt_ref.model_dump()
                   for r in resources.values()):raise ValueError('attempt already authorized')
            if value['selection']['confirmation_digest'] != self._execution_confirmation(project, command, actor, row, definition.environment):
                raise ValueError('confirmation mismatch')
            if self._execution_request(project, command) != event.payload['request_digest']:
                raise ValueError('request mismatch')
        except (ValueError, KeyError, PyHError):
            raise_code('PERS-202', reason='execution_authorization_binding')

    def _execution_historical_materials(self, resources, attempt, actor):
        """Validate against facts before this commit, without future wall-clock or host reads."""
        project = attempt['project_id']; prerequisites = attempt['prerequisites']
        parent = resources[project]
        if (parent['digest'] != prerequisites['project_digest'] or
                parent['source_seq'] != prerequisites['project_source_seq']):
            raise ValueError('project changed before execution authorization')
        task = self._s05_exact(resources, project, 'product_task', prerequisites['task_ref'], 'product_task_id', actor)['product_task']
        if task['retired'] or task['input_binding'] != prerequisites['input_binding']:
            raise ValueError('task changed before execution authorization')
        wi = self._task_bound_input(resources, task, actor)['workspace_input']
        self._input_manifest(resources, project, wi['manifest_ref'], actor,
                             manifest_schema=1 if wi['schema_version'] == 2 else 2)
        if wi['schema_version'] == 1:
            source = resources[wi['source_ref']['snapshot_id']]['workspace_snapshot']
            if self._workspace_latest(resources, source) != wi['source_ref']['snapshot_id']:
                raise ValueError('workspace snapshot changed before execution authorization')
        if self._attempt_context_refs(resources, project, attempt['selection'], actor).model_dump() != attempt['context']:
            raise ValueError('context changed before execution authorization')
        grants = set(self._input_grant_ids(wi))
        for ref in attempt['context']['document_refs']:
            grants.update(self._document_grants(resources, resources[ref['document_id']]['document'], actor))
        if any(resources[g]['grant']['revoked'] for g in grants):raise ValueError('source withdrawn before execution authorization')

    def _execution_summary(self, resources, row, actor, authorize, started):
        value = row['execution_authorization']; project = value['project_id']
        self._context_owned({value['execution_id']: row}, value['execution_id'], 'execution_authorization', project, actor)
        attempt = self._execution_attempt(resources, project, value['selection']['attempt_ref'], actor)
        self._attempt_summary(resources, attempt, actor, authorize, started)
        self._context_finish(authorize, actor, resources)
        result=dict(schema_version=1, execution_id=value['execution_id'], revision=1, project_id=project,
            project_revision=value['project_revision'], digest=row['digest'], source_seq=row['source_seq'],
            created_at=row['created_at'], request_digest=row['request_digest'], submission_key=value['submission_key'],
            attempt_ref=value['selection']['attempt_ref'], agent_ref=value['selection']['agent_ref'],
            boundary=value['environment']['boundary'], model_fingerprint=value['environment']['model_fingerprint'],
            session_id=value['session_id'], task_id=value['task_id'], status='authorized',
            persisted=True, execution_authorized=True, execution_started=False)
        if value['schema_version']==2:
            from pyharness.application.execution_continuation_store import continuation_receipt
            result['continuation']=continuation_receipt(value)
        if (business:=self._delivery_business_status(resources,row,actor,authorize)) is not None:
            result['business_acceptance']=business
        return result

    async def preview_execution(self, project, command, *, authorize, describe):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, _, _ = await self._execution_read_state()
            row, environment, confirmation = self._execution_current(resources, project, command, actor, authorize, describe, started)
            return dict(schema_version=1, project_id=project, attempt_ref=command.attempt_ref.model_dump(),
                agent_ref=command.agent_ref.model_dump(), boundary=environment.boundary,
                model_fingerprint=environment.model_fingerprint, confirmation_digest=confirmation,
                status='confirmation_required', persisted=False, execution_authorized=False)

    async def authorize_execution(self, project, command, *, command_id, authorize, describe):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); await self._open(initialize=False); resources, commands = self._grant_state()
            request = self._execution_request(project, command)
            self._context_envelope('execution_authorization', command.execution_id, 1, command_id, actor, request)
            if old := commands.get((actor, command_id)):
                if old[0] != request:raise_code('EVT-100', reason='idempotency_conflict')
                return self._execution_summary(resources, old[1], actor, authorize, started), False
            if command.execution_id in resources:raise_code('EVT-101', reason='execution_id_reserved')
            row, environment, confirmation = self._execution_current(resources, project, command, actor, authorize, describe, started)
            if confirmation != command.confirmation_digest:raise_code('EVT-101', reason='execution_preview_stale')
            if environment.boundary == 'host_unrestricted' and not command.confirm_host_boundary:
                raise_code('GRD-403', reason='execution_host_confirmation_required')
            resources=await self._reserve_handoff_budget(resources,row,command,environment,actor,authorize)
            from pyharness.application.task_graph_execution_contracts import GraphNodeStartExecution,GraphNodeExecutionAuthorization
            from pyharness.application.task_graph_binding import reserved_graph_identities
            _,reserved=reserved_graph_identities(resources)
            if command.execution_id in reserved and not isinstance(command,GraphNodeStartExecution):raise_code('GRD-403',reason='graph_execution_identity_reserved')
            model=GraphNodeExecutionAuthorization if isinstance(command,GraphNodeStartExecution) else ExecutionAuthorization
            definition = model(execution_id=command.execution_id, project_id=project,
                project_revision=row['run_attempt']['project_revision'], created_by=actor, submission_key=command_id,
                selection=command, attempt_binding_digest=row['run_attempt']['binding_digest'], environment=environment,
                session_id='s-pa-' + uuid.uuid4().hex, task_id='p-' + uuid.uuid4().hex)
            def gate():
                fresh, _ = self._grant_state()
                if command.execution_id in fresh:raise_code('EVT-101', reason='execution_id_reserved')
                if any(r.get('execution_authorization', {}).get('selection', {}).get('attempt_ref') == command.attempt_ref.model_dump()
                       for r in fresh.values()):raise_code('EVT-101', reason='attempt_already_authorized')
                exact, current, digest = self._execution_current(fresh, project, command, actor, authorize, describe, started)
                if digest != confirmation or current != environment:raise_code('EVT-101', reason='execution_preview_stale')
                self._context_quota(fresh, 'execution_authorization', project)
            gate()
            resources, row = await self._append_context(definition, 'execution_authorization', command.execution_id,
                command_id, actor, request, gate)
            try:return self._execution_summary(resources, row, actor, authorize, started), True
            except PyHError:raise_code('PERS-202', reason='execution_receipt_unavailable')

    async def execution_submission(self, project, key, *, authorize):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, commands, _ = await self._execution_read_state()
            self._context_project(resources, project)
            old = commands.get((actor, key)); row = old[1] if old else None
            if row is not None and (not row.get('execution_authorization') or row['execution_authorization']['project_id'] != project):
                raise_code('GRD-403', reason='execution_submission_unavailable')
            value = self._execution_summary(resources, row, actor, authorize, started) if row else None
            self._context_finish(authorize, actor, resources)
            return dict(schema_version=1, project_id=project, submission_key=key, status='committed' if row else 'not_found',
                execution=value, writer_requires_reopen=self._failed)

    async def read_product_session(self, sid, *, authorize, read):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, _, _ = await self._execution_read_state()
            rows = [r for r in resources.values() if r.get('execution_authorization', {}).get('session_id') == sid]
            if len(rows) != 1:raise_code('GRD-403', reason='product_execution_unavailable')
            row = rows[0]
            self._execution_summary(resources, row, actor, authorize, started)
            result = await read(self._execution_read_row(resources,row))
            # Account/root I/O and journal reads are not authorization cache entries.
            self._execution_summary(resources, row, actor, authorize, started)
            return result

    def checked_execution(self, project, ident, *, authorize, describe, expected_digest=None):
        """Synchronous last boundary check, without yielding between authority and use."""
        from pyharness.application.execution_effects import confirmed_events
        from pyharness.application.execution_contracts import StartExecution
        self._ensure_usable(); actor = authorize(); started = time.monotonic()
        control,_=self._cancellation_state()
        stop_target=self._context_owned(control,ident,'execution_authorization',project,actor)
        if expected_digest is not None and stop_target['digest']!=expected_digest:
            raise_code('GRD-403',reason='execution_authorization_changed')
        if any(r.get('execution_cancellation',{}).get('execution_ref',{}).get('execution_id')==ident for r in control.values()):
            import asyncio
            raise asyncio.CancelledError('execution_cancel_requested')
        resources, _ = self._project(confirmed_events(self._session))
        row = self._context_owned(resources, ident, 'execution_authorization', project, actor)
        if expected_digest is not None and row['digest'] != expected_digest:
            raise_code('GRD-403', reason='execution_authorization_changed')
        from pyharness.application.continuation_execution_contracts import ContinuedStartExecution
        from pyharness.application.task_graph_execution_contracts import GraphNodeStartExecution
        parser={1:StartExecution,2:ContinuedStartExecution,3:GraphNodeStartExecution}[row['execution_authorization']['schema_version']]
        command = parser.model_validate(row['execution_authorization']['selection'])
        attempt, environment, confirmation = self._execution_current(resources, project, command, actor, authorize, describe, started)
        if environment.model_dump() != row['execution_authorization']['environment'] or confirmation != command.confirmation_digest:
            raise_code('EVT-101', reason='execution_configuration_changed')
        return row, attempt, resources, actor, started

    async def read_product_child(self,binding,*,authorize,read):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=resources.get(binding['execution_id']);value=row.get('execution_authorization') if row else None
            if (value is None or binding['authorization_digest']!=row['digest'] or binding['authorization_seq']!=row['source_seq'] or
                    binding['subject_ref']!=actor or binding['root_session_id']!=value['session_id']):
                raise_code('GRD-403',reason='product_child_owner_unavailable')
            self._execution_summary(resources,row,actor,authorize,started)
            result=await read(self._execution_read_row(resources,row))
            self._execution_summary(resources,row,actor,authorize,started)
            return result

    async def capture_execution_materials(self, project, command, *, authorize, describe):
        from pyharness.application.execution_materials import captured_materials
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, _, _ = await self._execution_read_state()
            row, environment, confirmation = self._execution_current(resources, project, command, actor, authorize, describe, started)
            materials = captured_materials(self, resources, row, actor, authorize, started)
            self._execution_current(resources, project, command, actor, authorize, describe, started)
            return materials

    async def observe_execution(self, project, ident, *, authorize, read):
        started = time.monotonic()
        async with self._workspace_capture_lock(started):
            actor = authorize(); resources, _, _ = await self._execution_read_state()
            row = self._context_owned(resources, ident, 'execution_authorization', project, actor)
            summary = self._execution_summary(resources, row, actor, authorize, started)
            result = await read(self._execution_read_row(resources,row), summary)
            self._execution_summary(resources, row, actor, authorize, started)
            return result

    async def observe_execution_pair(self,project,idents,*,authorize,read):
        """One disposable original control snapshot; recheck BOTH after all reads."""
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();rows=[];views=[]
            for ident in idents:
                row=self._context_owned(resources,ident,'execution_authorization',project,actor)
                summary=self._execution_summary(resources,row,actor,authorize,started)
                attempt=self._execution_attempt(resources,project,row['execution_authorization']['selection']['attempt_ref'],actor)
                rows.append(row);views.append((self._execution_read_row(resources,row),summary,attempt['run_attempt']))
            result=await read(views)
            for row in rows:self._execution_summary(resources,row,actor,authorize,started)
            return result
