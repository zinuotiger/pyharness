"""Explicit product start on the existing owner, actual queue and engine runner."""
import asyncio
import copy
from types import SimpleNamespace
from pyharness.application.accounts import current_actor
from pyharness.application.execution_boundary import ProductExecutionBoundary
from pyharness.application.execution_configuration import describe_execution
from pyharness.application.execution_contracts import PreviewExecution, StartExecution, ExecutionId
from pyharness.application.execution_effects import EffectRecorder, confirmed_events
from pyharness.application.execution_workspace import execution_prompt
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.run_attempt_contracts import SubmissionKey,AttemptId
from pyharness.application.workspace_contracts import ProjectId
from pyharness.core.lifecycle import admitted
from pyharness.errors import PyHError, raise_code


def execution_projection(projects, row, summary, log):
    value = row['execution_authorization']; service = projects.service
    events = list(log.events_after(0)); phases = [e.payload['phase'] for e in events if e.type == 'execution.lifecycle']
    queue = service._queues.get(value['session_id']); live = []
    if queue is not None:
        state = queue.status(); live = [state.running, *state.waiting]
    launching = getattr(service, '_execution_launches', {}).get(value['execution_id'])
    active_launch = launching is not None and not launching.done()
    phase = phases[-1]
    status = phase if phase in ('completed','failed','cancelled') else (
        'running' if phase == 'running' and value['task_id'] in live else
        'queued' if value['task_id'] in live else 'preparing' if active_launch else 'interrupted')
    if phase=='bound' and value['task_id'] not in live and any(
            e.type=='task.failed' and e.payload['task_id']==value['task_id'] and e.payload['reason']=='cancelled' for e in events):
        status='cancelled'
    effects = [dict(e.payload, source_seq=e.seq, created_at=e.ts) for e in events if e.type == 'execution.effect']
    outcomes = {e['started_seq'] for e in effects if e['phase'] != 'started'}
    unknown = any(e['phase'] == 'unknown' or (e['phase'] == 'started' and e['source_seq'] not in outcomes) for e in effects)
    from pyharness.application.execution_artifact_receipts import artifact_receipts
    artifacts=artifact_receipts(service.platform,log,value['task_id'],value['execution_id'])
    unknown=unknown or any(e.get('file_observation')=='unavailable' for e in effects) or any(a['integrity']!='verified' for a in artifacts)
    answers = [e.payload['content'] for e in events if e.type == 'agent.message']
    text = '\n\n'.join(answers)
    life=[e for e in events if e.type=='execution.lifecycle']
    from pyharness.application.execution_checkpoints import checkpoint_projection
    from pyharness.application.execution_state_reader import state_checkpoint_projection
    return dict(summary, status=status, execution_reason=life[-1].payload['reason'],execution_started='running' in phases, observed_seq=events[-1].seq,
        business_acceptance=summary.get('business_acceptance','not_evaluated'), effects=effects[-400:], unknown_effects=unknown,artifacts=artifacts,
        result_summary=text[:8000], result_truncated=len(text)>8000,**checkpoint_projection(events),**state_checkpoint_projection(service.platform,events))


def require_material_reader(definition,materials):
    if materials['layout']=='workspace':
        tools=set(definition['allowed_tools'])
        if definition['sandbox_profile']['mode']=='disabled' or not (
            tools&{'fs.read_file','file.read','exec.shell_run','exec.python_run'} or
            (definition['executor']=='codex' and definition['codex_tools']=='native')):
            raise_code('GRD-403',reason='execution_context_reader_required')


class ProductExecutions:
    @admitted
    async def execution_continuation_plan(self,project,ident):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        from pyharness.application.execution_continuation_plan import observe_continuation_plan
        async def read(row,summary):return await observe_continuation_plan(self,row,summary)
        return await self.store.observe_execution(project,ident,authorize=lambda:self._authorize(write=True),read=read)

    @admitted
    async def execution_recovery_plan(self,project,ident):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        from pyharness.application.execution_recovery_plan import observe_recovery_plan
        async def read(row,summary):return await observe_recovery_plan(self,row,summary)
        return await self.store.observe_execution(project,ident,authorize=lambda:self._authorize(write=True),read=read)

    @admitted
    async def execution_for_attempt(self,project,ident):
        self._authorize(write=True)
        project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,AttemptId)
        return await self.store.execution_for_attempt(project,ident,authorize=lambda:self._authorize(write=True))

    @admitted
    async def execution_submission(self,project,key):
        self._authorize(write=True)
        project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.execution_submission(project,key,authorize=lambda:self._authorize(write=True))

    async def _execution_ready(self, command):
        result = await self.service.platform.chat_readiness(dict(agent_id=command.agent_ref.agent_id,
            agent_version=command.agent_ref.version))
        if not result['ready']:raise PyHError(result['code'])

    @admitted
    async def execution_preview(self, project, body):
        self._authorize(write=True); project = self._workspace_id(project, ProjectId)
        command = PreviewExecution.model_validate(body); await self._execution_ready(command)
        describe = lambda: describe_execution(self, command.agent_ref)
        authorize = lambda: self._authorize(write=True)
        materials = await self.store.capture_execution_materials(project, command, authorize=authorize, describe=describe)
        result = await self.store.preview_execution(project, command, authorize=authorize, describe=describe)
        return dict(result, context_layout=materials['layout'], prompt_characters=materials['prompt_characters'],
            file_count=len(materials['bundle']['files']), document_count=len(materials['bundle']['documents']),
            workspace_policy='captured-input-only-v1')

    @admitted
    async def start_execution(self, project, body, command_id):
        self._authorize(write=True); project = self._workspace_id(project, ProjectId)
        command = StartExecution.model_validate(body); key = self._workspace_id(command_id, SubmissionKey)
        # Original-key reconciliation is read-only even if model setup changed.
        observed = await self.store.execution_submission(project, key, authorize=lambda: self._authorize(write=True))
        if observed['status'] == 'committed':
            if observed['execution']['request_digest'] != self.store._execution_request(project, command):
                raise_code('EVT-100', reason='idempotency_conflict')
            return await self.execution(project, observed['execution']['execution_id'])
        await self._execution_ready(command)
        describe = lambda: describe_execution(self, command.agent_ref); authorize = lambda: self._authorize(write=True)
        materials = await self.store.capture_execution_materials(project, command, authorize=authorize, describe=describe)
        definition=describe()['agent_snapshot']
        require_material_reader(definition,materials)
        receipt, first = await self.store.authorize_execution(project, command, command_id=key, authorize=authorize, describe=describe)
        if first:await self._launch_product_execution(receipt, materials)
        return await self.execution(project, receipt['execution_id'])

    async def _launch_product_execution(self, receipt, materials):
        service = self.service; sid = receipt['session_id']; task = receipt['task_id']
        from pyharness.application.continuation_runtime import product_execution_boundary
        boundary = product_execution_boundary(self,receipt,materials)
        prompt=execution_prompt(materials)
        launches = service.__dict__.setdefault('_execution_launches', {})
        launches[receipt['execution_id']] = asyncio.current_task()
        log = None
        try:
            row, _, _, actor, _ = boundary.checked(); value = row['execution_authorization']
            # Prepare the immutable runtime identity before admitting queue work.
            # Keep the original byte/path checks and per-Service cache; snapshot
            # unavailability remains optional, exactly as at the model boundary.
            from pyharness.application.execution_state_contract import runtime_contract_digest
            from pyharness.application.execution_state_capture import StateUnavailable
            try:runtime_contract_digest(service)
            except (StateUnavailable,PyHError,OSError,ValueError,TypeError):pass
            boundary.checked()
            binding = dict(schema_version=1, execution_id=receipt['execution_id'], phase='bound',
                authorization_digest=receipt['digest'], authorization_seq=receipt['source_seq'], subject_ref=actor, reason=None)
            log = await service.surface_mgr().create_product_session(sid, binding, task_id=task)
            boundary.checked()
            definition = value['environment']['agent_snapshot']
            meta = dict(task_id=task, request_id=value['submission_key'], name=materials['bundle']['task']['title'],
                input_length=len(prompt), artifact_ids=[], use_vision=False, vision_profile=None, vision_model=None,
                agent_id=definition['agent_id'], agent_version=definition['current_version'], definition=definition,
                retry_count=0, actor=copy.deepcopy(current_actor.get()))
            product = dict(execution_id=receipt['execution_id'], authorization_digest=receipt['digest'],
                authorization_seq=receipt['source_seq'], materials_digest=materials['materials_digest'])
            await log.append('user.message', dict(content=prompt, meta=dict(platform=meta, product_execution=product)),
                actor='user', task_id=task, sync=True)
            confirmed_events(log); boundary.checked()
            if hasattr(boundary,'basis'):
                from pyharness.application.execution_state_restore import restore_state_kv
                restore_state_kv(service._tenant_sessions_dir(),sid,boundary.body,boundary.authorize)
            queue = await service._queue_for_owned(sid, log)
            spine = service._engines[sid]
            spine.platform_runtime.product_execution = boundary
            from pyharness.application.execution_file_runtime import configure_context_reads
            configure_context_reads(spine)
            spine.platform_runtime.execution_effects = EffectRecorder(receipt['execution_id'], receipt['digest'], boundary.authorize)
            if hasattr(boundary,'basis'):
                from pyharness.application.continuation_runtime import ContinuationEffectRecorder
                spine.platform_runtime.execution_effects=ContinuationEffectRecorder(boundary)
            from pyharness.application.product_execution_children import ProductChildren
            ProductChildren(spine,boundary)
            spine._auto_titled = True
            boundary.checked()
            await queue.submit(prompt, task_id=task, meta=dict(channel=service.channel, session_id=sid))
        except BaseException as exc:
            if log is not None and not any(e.type == 'task.enqueued' for e in confirmed_events(log)):
                ctx = SimpleNamespace(session=log, task_id=task)
                try:await boundary.append_phase(ctx, 'failed', str(getattr(exc, 'code', 'runtime_start_failed'))[:100])
                except BaseException as secondary:exc.add_note('start failure receipt unavailable: ' + type(secondary).__name__)
            raise
        finally:
            launches.pop(receipt['execution_id'], None)

    @admitted
    async def execution(self, project, ident):
        self._authorize(write=True); project = self._workspace_id(project, ProjectId)
        ident = self._workspace_id(ident, ExecutionId)
        async def read(row, summary):
            try:
                log = await read_execution_log(self.service._tenant_sessions_dir(), row, self.service.tenant_id)
                result=execution_projection(self, row, summary, log)
                from pyharness.application.execution_artifact_receipts import delegated_receipts
                delegated=await delegated_receipts(self,row,log)
                result['artifacts'].extend(delegated.pop('artifacts'))
                child_unknown=delegated.pop('unknown_effects')
                result['unknown_effects']=result['unknown_effects'] or child_unknown or any(
                    a['integrity']!='verified' for a in result['artifacts'])
                result.update(delegated)
                from pyharness.application.execution_liveness import control_observation
                result['control_observation']=control_observation(self,row,log,result)
                return result
            except PyHError as exc:
                if exc.code != 'PERS-202':raise
                return dict(summary, status='unknown', execution_started=None, observed_seq=None, effects=None,
                    unknown_effects=True, business_acceptance='not_evaluated', observation_error='execution_journal_unconfirmed')
        return await self.store.observe_execution(project, ident, authorize=lambda: self._authorize(write=True), read=read)
