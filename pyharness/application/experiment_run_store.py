"""One funding receipt before original dispatch; cold reads only observe."""
import time
from pyharness.application.experiment_plan import experiment_ref
from pyharness.application.experiment_run_contracts import (ExperimentGraphPlan,ExperimentRunAuthorization,
    ExperimentCancellation,PreviewExperimentRun)
from pyharness.application.experiment_run_binding import (graph_identity,run_confirmation,run_request,
    control_run,cancel_requested,matching_graph)
from pyharness.application.task_graph_run_contracts import StartTaskGraph
from pyharness.errors import raise_code


class ExperimentRunStore:
    def _experiment_run_current(self,resources,row,actor,authorize,started,projects):
        value=row['experiment_run'];ref=value['selection']['experiment_ref'];project=value['project_id']
        self._context_owned(resources,value['experiment_run_id'],'experiment_run',project,actor)
        definition=self._s05_history(resources,project,ref['experiment_id'],ref['revision'],'experiment_definition',actor)
        if experiment_ref(definition,'experiment_definition')!=ref:raise_code('PERS-202',reason='experiment_definition_reference')
        self._experiment_check_definition(resources,definition,actor,authorize,started,projects)
        matching_graph(self,resources,row)
        self._context_finish(authorize,actor,resources)

    def _experiment_funding_plan(self,resources,project,selection,actor,authorize,started,projects):
        ref=selection.experiment_ref
        definition=self._s05_history(resources,project,ref.experiment_id,ref.revision,'experiment_definition',actor)
        if experiment_ref(definition,'experiment_definition')!=ref.model_dump():raise_code('EVT-101',reason='experiment_definition_reference')
        self._experiment_check_definition(resources,definition,actor,authorize,started,projects)
        graph_ref=definition['experiment_definition']['graph_ref']
        graph,nodes,allocated,digest=self._compile_task_graph(resources,project,graph_ref,actor,authorize,projects._describe_graph_execution,started)
        plan=ExperimentGraphPlan(confirmation_digest=digest,concurrency=graph.concurrency,limits=graph.limits,
            allocated_budget=allocated,nodes=[dict(node_id=n.node_id,depends_on=n.depends_on,agent_ref=n.agent_ref,
            task_ref=n.prerequisites.task_ref,boundary=n.environment.boundary,model_fingerprint=n.environment.model_fingerprint,
            allocated_budget=n.allocated_budget) for n in nodes])
        ident,key=graph_identity(self.tenant_id,actor,project,selection.experiment_run_id)
        command=StartTaskGraph(schema_version=1,graph_ref=graph_ref,graph_run_id=ident,
            confirmation_digest=digest,confirm_host_boundary=selection.confirm_host_boundary)
        funding=run_confirmation(self.tenant_id,actor,project,selection,plan)
        from pyharness.application.experiment_run_contracts import StartExperimentRun
        confirmed=StartExperimentRun(**PreviewExperimentRun.model_validate({k:getattr(selection,k) for k in PreviewExperimentRun.model_fields}).model_dump(),
            confirmed=True,confirmation_digest=funding)
        # The record validator checks all four grant dimensions and host boundary before any append.
        candidate=ExperimentRunAuthorization(experiment_run_id=selection.experiment_run_id,project_id=project,
            project_revision=definition['experiment_definition']['project_revision'],created_by=actor,submission_key='preview-only',
            selection=confirmed,graph_command=command,graph_plan=plan)
        self._experiment_graph_capacity(candidate,graph,nodes,key)
        return candidate

    def _experiment_graph_capacity(self,candidate,graph,nodes,key):
        from pyharness.application.workspace_store import canonical
        from pyharness.application.task_graph_run_contracts import TaskGraphRunAuthorization,GraphNodePlan
        preview=TaskGraphRunAuthorization(graph_run_id=candidate.graph_command.graph_run_id,tenant_id=self.tenant_id,
            project_id=candidate.project_id,project_revision=graph.project_revision,created_by=candidate.created_by,
            submission_key=key,selection=candidate.graph_command,session_id='s-pg-'+'0'*32,graph_task_id='p-'+'0'*32,
            nodes=[GraphNodePlan(**n.model_dump(),attempt_id='ra-'+format(i,'016x'),execution_id='ex-'+format(i,'016x')) for i,n in enumerate(nodes)],
            concurrency=graph.concurrency,limits=graph.limits,allocated_budget=candidate.graph_plan.allocated_budget)
        if len(canonical(preview.model_dump()))>self.MAX_CONTEXT_BYTES:
            raise_code('EVT-100',reason='experiment_original_graph_blob_limit')
        if len(canonical(candidate.model_dump()))+128>self.MAX_BLOB_BYTES:
            raise_code('EVT-100',reason='experiment_funding_blob_limit')

    async def experiment_run_preview(self,project,selection,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            candidate=self._experiment_funding_plan(resources,project,selection,actor,authorize,started,projects)
            return dict(schema_version=1,project_id=project,experiment_run_id=selection.experiment_run_id,
                experiment_ref=selection.experiment_ref.model_dump(),graph_plan=candidate.graph_plan.model_dump(),
                budget_grant=selection.budget_grant.model_dump(),confirmation_digest=candidate.selection.confirmation_digest,
                status='confirmation_required',persisted=False,automatic_dispatch=False,business_acceptance='not_evaluated')

    async def register_experiment_run(self,project,selection,key,*,authorize,projects):
        started=time.monotonic();kind='experiment_run';ident=selection.experiment_run_id
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state()
            request=run_request(project,selection);self._context_envelope(kind,ident,1,key,actor,request)
            if old:=commands.get((actor,key)):
                if old[0]!=request or kind not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                self._experiment_run_current(resources,old[1],actor,authorize,started,projects)
                return experiment_ref(old[1],kind),False
            await self._open(initialize=False);resources,commands=self._grant_state()
            candidate=self._experiment_funding_plan(resources,project,selection,actor,authorize,started,projects)
            if candidate.selection!=selection:raise_code('EVT-101',reason='experiment_run_preview_changed')
            definition=candidate.model_copy(update={'submission_key':key})
            def gate():
                fresh,_=self._grant_state()
                if ident in fresh or definition.graph_command.graph_run_id in fresh:raise_code('EVT-101',reason='experiment_run_identity_reserved')
                current=self._experiment_funding_plan(fresh,project,selection,actor,authorize,started,projects)
                if current!=candidate:raise_code('EVT-101',reason='experiment_run_preview_changed')
                self._context_quota(fresh,kind,project)
            gate();_,row=await self._append_context(definition,kind,ident,key,actor,request,gate)
            return experiment_ref(row,kind),True

    async def experiment_run_record(self,project,ident,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._context_owned(resources,ident,'experiment_run',project,actor)
            self._experiment_run_current(resources,row,actor,authorize,started,projects)
            graph=matching_graph(self,resources,row)
            return row,graph,cancel_requested(resources,experiment_ref(row,'experiment_run'))

    async def experiment_resume_preview(self,project,reference,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=control_run(self,resources,project,reference.model_dump(),actor)
            self._experiment_run_current(resources,row,actor,authorize,started,projects)
            value=row['experiment_run'];exists=matching_graph(self,resources,row) is not None
            if cancel_requested(resources,reference.model_dump()):raise_code('EVT-101',reason='experiment_cancel_requested')
            if self._failed:raise_code('PERS-202',reason='workspace_reopen_required')
            current=self._experiment_funding_plan(resources,project,
                PreviewExperimentRun.model_validate({k:value['selection'][k] for k in PreviewExperimentRun.model_fields}),actor,authorize,started,projects)
            if current.graph_plan.model_dump()!=value['graph_plan'] or current.graph_command.model_dump()!=value['graph_command']:
                raise_code('EVT-101',reason='experiment_run_configuration_changed')
            from pyharness.application.workspace_store import canonical,digest_of
            digest=digest_of(canonical(dict(operation='experiment.resume',tenant_id=self.tenant_id,actor=actor,project=project,
                experiment_run_ref=reference.model_dump(),graph_command=value['graph_command'],graph_exists=exists)))
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,experiment_run_ref=reference.model_dump(),confirmation_digest=digest,
                status='observe_only' if exists else 'confirmation_required',automatic_dispatch=False),value

    async def experiment_run_control(self,project,ident,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_=self._cancellation_state()
            row=self._context_owned(resources,ident,'experiment_run',project,actor);ref=experiment_ref(row,'experiment_run')
            control_run(self,resources,project,ref,actor);graph=matching_graph(self,resources,row)
            if authorize()!=actor:raise_code('GRD-403',reason='experiment_control_subject_changed')
            return dict(schema_version=1,project_id=project,experiment_run_ref=ref,control_only=True,
                submission_key=row['experiment_run']['submission_key'],request_digest=row['request_digest'],
                graph_run_ref=self._s05_ref(graph,'graph_run_id','task_graph_run') if graph else None,
                cancel_requested=cancel_requested(resources,ref),automatic_dispatch=False)

    async def cancel_experiment_run(self,project,command,key,*,authorize):
        started=time.monotonic();kind='experiment_cancellation';ident=command.cancellation_id
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open_projected(initialize=False,project=self._project_cancellation_control)
            resources,commands=self._cancellation_state();ref=command.experiment_run_ref.model_dump()
            row=control_run(self,resources,project,ref,actor);request=run_request(project,command,'experiment.cancel')
            self._context_envelope(kind,ident,1,key,actor,request)
            if old:=commands.get((actor,key)):
                if old[0]!=request or kind not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                return old[1],row,matching_graph(self,resources,row)
            definition=ExperimentCancellation(**command.model_dump(),project_id=project,created_by=actor,submission_key=key)
            def gate():
                fresh,_=self._cancellation_state();control_run(self,fresh,project,ref,actor)
                if authorize()!=actor:raise_code('GRD-403',reason='experiment_control_subject_changed')
                if ident in fresh or cancel_requested(fresh,ref):raise_code('EVT-101',reason='experiment_cancel_already_requested')
                self._context_quota(fresh,kind,project)
            gate();_,receipt=await self._append_context_projected(definition,kind,ident,key,actor,request,gate,self._cancellation_state)
            return receipt,row,matching_graph(self,resources,row)

    def _experiment_graph_admission(self,resources,project,command,key,actor):
        for row in resources.values():
            value=row.get('experiment_run')
            if not value or value['graph_command']['graph_run_id']!=command.graph_run_id:continue
            _,expected=graph_identity(self.tenant_id,value['created_by'],value['project_id'],value['experiment_run_id'])
            if (value['created_by']!=actor or value['project_id']!=project or key!=expected or
                    command.model_dump()!=value['graph_command'] or cancel_requested(resources,experiment_ref(row,'experiment_run'))):
                raise_code('GRD-403',reason='experiment_funded_graph_admission')

    def _experiment_graph_stop_requested(self,resources,reference):
        for row in resources.values():
            value=row.get('experiment_run')
            if value and value['graph_command']['graph_run_id']==reference['graph_run_id']:
                return cancel_requested(resources,experiment_ref(row,'experiment_run'))
        return False
