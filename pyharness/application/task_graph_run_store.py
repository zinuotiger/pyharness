"""Read-only graph compilation on the existing control owner."""
import time,secrets,uuid
from pyharness.application.task_graph_contracts import TaskGraphRevision,TaskGraphLimits
from pyharness.application.task_graph_run_contracts import CompiledGraphNode,GraphNodePlan,TaskGraphRunAuthorization,total_allocation
from pyharness.application.execution_contracts import ExecutionEnvironment
from pyharness.errors import raise_code


def graph_confirmation(tenant,actor,project,reference,graph,nodes):
    from pyharness.application.workspace_store import canonical,digest_of
    basis=dict(operation='task_graph.preview',tenant_id=tenant,subject_ref=actor,project_id=project,
        graph_ref=reference,concurrency=graph.concurrency,limits=graph.limits.model_dump(),
        nodes=[{k:getattr(node,k) for k in CompiledGraphNode.model_fields} for node in nodes])
    normalized=dict(basis,nodes=[CompiledGraphNode.model_validate(node).model_dump() for node in basis['nodes']])
    return digest_of(canonical(normalized))


class TaskGraphRunStore:
    async def task_graph_runs(self,project,query,*,authorize):
        from pyharness.application.workspace_input_pages import workspace_input_page
        from pyharness.errors import PyHError
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,events=await self._execution_read_state();self._context_project(resources,project);rows=[]
            for row in resources.values():
                value=row.get('task_graph_run')
                if not value or value['project_id']!=project or value['created_by']!=actor:continue
                try:rows.append(self._graph_run_authority(resources,row,actor,authorize,started))
                except PyHError as exc:
                    if exc.code=='GRD-403':continue
                    raise
            self._context_finish(authorize,actor,resources)
            return workspace_input_page(rows,query,kind='graph_runs',project=project,actor=actor,watermark=len(events))

    def _validate_task_graph_run(self,definition,resources,previous,event):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.task_graph_binding import graph_attempt_capacity,reserved_graph_identities
        from pyharness.errors import PyHError
        try:
            value=definition.model_dump();actor=definition.created_by;project=definition.project_id
            if previous is not None or actor!=event.payload['actor_ref'] or definition.tenant_id!=self.tenant_id or definition.submission_key!=event.payload['command_id']:
                raise ValueError('graph run owner')
            self._context_project(resources,project,definition.project_revision);self._context_quota(resources,'task_graph_run',project)
            reference=definition.selection.graph_ref.model_dump();row=self._s05_exact(resources,project,'task_graph',reference,'graph_id',actor)
            graph=TaskGraphRevision.model_validate(row['task_graph'])
            if graph.retired or graph.project_revision!=definition.project_revision or graph.concurrency!=definition.concurrency or graph.limits!=definition.limits:
                raise ValueError('graph run definition scope')
            plans={node.node_id:node for node in definition.nodes}
            if set(plans)!={n.node_id for n in graph.nodes}:raise ValueError('graph node coverage')
            attempts,executions=reserved_graph_identities(resources)
            for node in graph.nodes:
                plan=plans[node.node_id];p=plan.prerequisites;parent=resources[project]
                task=self._s05_exact(resources,project,'product_task',node.task_ref.model_dump(),'product_task_id',actor)['product_task']
                if (plan.depends_on!=node.depends_on or plan.agent_ref!=node.agent_ref or p.task_ref!=node.task_ref or
                        p.project_digest!=parent['digest'] or p.project_source_seq!=parent['source_seq'] or p.input_binding.model_dump()!=task['input_binding'] or
                        plan.context!=self._attempt_context_refs(resources,project,node.model_dump(),actor) or
                        plan.attempt_id in resources or plan.execution_id in resources or plan.attempt_id in attempts or plan.execution_id in executions):
                    raise ValueError('graph node plan binding')
            graph_attempt_capacity(self,resources,project,additional=len(definition.nodes))
            expected=graph_confirmation(self.tenant_id,actor,project,reference,graph,definition.nodes)
            if definition.selection.confirmation_digest!=expected:raise ValueError('graph preview binding')
            request=digest_of(canonical(dict(operation='task_graph.start',project_id=project,command=definition.selection.model_dump())))
            if request!=event.payload['request_digest']:raise ValueError('graph run request binding')
            from pyharness.application.experiment_run_binding import validate_funded_graph
            validate_funded_graph(self,definition,resources,event)
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='task_graph_run_binding')

    def _graph_run_authority(self,resources,row,actor,authorize,started):
        value=row['task_graph_run'];project=value['project_id']
        self._context_owned({value['graph_run_id']:row},value['graph_run_id'],'task_graph_run',project,actor)
        graph_row=self._graph_revision(resources,project,value['selection']['graph_ref']['graph_id'],value['selection']['graph_ref']['revision'],actor)
        if self._s05_ref(graph_row,'graph_id','task_graph')!=value['selection']['graph_ref']:raise_code('PERS-202',reason='graph_run_definition_reference')
        self._graph_summary(resources,graph_row,actor,authorize,started)
        self._context_finish(authorize,actor,resources)
        public={k:v for k,v in value.items() if k not in {'created_by','tenant_id','nodes'}}
        public['nodes']=[dict(node_id=n['node_id'],depends_on=n['depends_on'],attempt_id=n['attempt_id'],execution_id=n['execution_id'],
            agent_ref=n['agent_ref'],allocated_budget=n['allocated_budget']) for n in value['nodes']]
        return dict(public,digest=row['digest'],source_seq=row['source_seq'],created_at=row['created_at'],request_digest=row['request_digest'],persisted=True,graph_run_authorized=True)

    async def authorize_task_graph_run(self,project,command,*,command_id,authorize,describe):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.task_graph_binding import graph_attempt_capacity,reserved_graph_identities
        started=time.monotonic();ident=command.graph_run_id
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            self._experiment_graph_admission(resources,project,command,command_id,actor)
            request=digest_of(canonical(dict(operation='task_graph.start',project_id=project,command=command.model_dump())))
            self._context_envelope('task_graph_run',ident,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request or 'task_graph_run' not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                return self._graph_run_authority(resources,old[1],actor,authorize,started),False
            if ident in resources:raise_code('EVT-101',reason='graph_run_identity_reserved')
            reference=command.graph_ref.model_dump();graph,nodes,allocated,confirmation=self._compile_task_graph(resources,project,reference,actor,authorize,describe,started)
            if command.confirmation_digest!=confirmation:raise_code('EVT-101',reason='task_graph_preview_changed')
            if any(n.environment.boundary=='host_unrestricted' for n in nodes) and not command.confirm_host_boundary:raise_code('GRD-403',reason='task_graph_host_confirmation_required')
            graph_attempt_capacity(self,resources,project,additional=len(nodes))
            used_attempts,used_executions=reserved_graph_identities(resources);used=set(resources)|used_attempts|used_executions
            def fresh_id(prefix):
                for _ in range(64):
                    value=prefix+secrets.token_hex(8)
                    if value not in used:used.add(value);return value
                raise_code('EVT-104',reason='task_graph_identity_generation_failed')
            plans=[GraphNodePlan(**node.model_dump(),attempt_id=fresh_id('ra-'),execution_id=fresh_id('ex-')) for node in nodes]
            definition=TaskGraphRunAuthorization(graph_run_id=ident,tenant_id=self.tenant_id,project_id=project,project_revision=graph.project_revision,
                created_by=actor,submission_key=command_id,selection=command,session_id='s-pg-'+uuid.uuid4().hex,graph_task_id='p-'+uuid.uuid4().hex,
                nodes=plans,concurrency=graph.concurrency,limits=graph.limits,allocated_budget=allocated)
            def gate():
                fresh,_=self._grant_state()
                self._experiment_graph_admission(fresh,project,command,command_id,actor)
                if ident in fresh:raise_code('EVT-101',reason='graph_run_identity_reserved')
                current,current_nodes,current_allocated,digest=self._compile_task_graph(fresh,project,reference,actor,authorize,describe,started)
                if current!=graph or current_nodes!=nodes or current_allocated!=allocated or digest!=confirmation:raise_code('EVT-101',reason='task_graph_preview_changed')
                self._context_quota(fresh,'task_graph_run',project);graph_attempt_capacity(self,fresh,project,additional=len(nodes))
                reserved_a,reserved_e=reserved_graph_identities(fresh)
                if any(n.attempt_id in fresh or n.execution_id in fresh or n.attempt_id in reserved_a or n.execution_id in reserved_e for n in plans):
                    raise_code('EVT-101',reason='graph_node_identity_reserved')
            gate();resources,row=await self._append_context(definition,'task_graph_run',ident,command_id,actor,request,gate)
            return self._graph_run_authority(resources,row,actor,authorize,started),True

    async def task_graph_run(self,project,ident,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();row=self._context_owned(resources,ident,'task_graph_run',project,actor)
            return self._graph_run_authority(resources,row,actor,authorize,started)

    async def task_graph_run_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state();self._context_project(resources,project)
            old=commands.get((actor,key));row=old[1] if old else None
            if row and ('task_graph_run' not in row or row['task_graph_run']['project_id']!=project):raise_code('GRD-403',reason='graph_run_submission_unavailable')
            value=self._graph_run_authority(resources,row,actor,authorize,started) if row else None
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if row else 'not_found',graph_run=value,automatic_dispatch=False)

    def _compile_task_graph(self,resources,project,reference,actor,authorize,describe,started):
        from pyharness.application.workspace_store import canonical,digest_of
        row=self._s05_exact(resources,project,'task_graph',reference,'graph_id',actor)
        graph=TaskGraphRevision.model_validate(row['task_graph'])
        if graph.retired:raise_code('GRD-403',reason='task_graph_retired')
        self._context_project(resources,project,graph.project_revision);nodes=[];grants=set()
        for node in graph.nodes:
            prerequisites,context,owned=self._graph_node_current(resources,project,node,actor,authorize,started);grants.update(owned)
            environment=ExecutionEnvironment.model_validate(describe(node.agent_ref))
            from pyharness.application.task_graph_run_contracts import node_allocation
            nodes.append(CompiledGraphNode(node_id=node.node_id,depends_on=node.depends_on,agent_ref=node.agent_ref,
                prerequisites=prerequisites,context=context,environment=environment,allocated_budget=node_allocation(environment)))
        allocated=total_allocation(nodes)
        if any(getattr(allocated,key)>getattr(graph.limits,key) for key in TaskGraphLimits.model_fields):
            raise_code('EVT-100',reason='task_graph_budget_allocation_exceeded')
        self._context_finish(authorize,actor,resources,grants)
        if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='task_graph_compilation_time_limit')
        return graph,nodes,allocated,graph_confirmation(self.tenant_id,actor,project,reference,graph,nodes)

    async def task_graph_run_preview(self,project,command,*,authorize,describe):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            reference=command.graph_ref.model_dump();graph,nodes,allocated,digest=self._compile_task_graph(resources,project,reference,actor,authorize,describe,started)
            return dict(schema_version=1,project_id=project,graph_ref=reference,title=graph.title,status='preflight_checked',
                confirmation_digest=digest,concurrency=graph.concurrency,limits=graph.limits.model_dump(),allocated_budget=allocated.model_dump(),
                nodes=[dict(node_id=n.node_id,depends_on=n.depends_on,agent_ref=n.agent_ref.model_dump(),task_ref=n.prerequisites.task_ref.model_dump(),
                    boundary=n.environment.boundary,model_fingerprint=n.environment.model_fingerprint,allocated_budget=n.allocated_budget.model_dump()) for n in nodes],
                execution_authorized=False,automatic_dispatch=False,persisted=False,business_acceptance='not_evaluated',cost_observation='runtime_estimate')
