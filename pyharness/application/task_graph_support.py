"""Historical graph bindings and currently authorized fixed-reference views."""
from pyharness.application.task_graph_contracts import TaskGraphCommand
from pyharness.application.run_attempt_contracts import AttemptContext
from pyharness.errors import PyHError,raise_code


class TaskGraphSupport:
    def _validate_task_graph(self,definition,resources,previous,event):
        try:
            self._s05_base(definition.model_dump(),resources,previous,event,'task_graph')
            for node in definition.nodes:
                raw=node.model_dump();row=self._s05_exact(resources,definition.project_id,'product_task',raw['task_ref'],'product_task_id',definition.created_by)
                task=row['product_task']
                if task['retired'] or task['input_binding'] is None or task['project_revision']!=definition.project_revision:
                    raise ValueError('graph task not executable')
                wi=self._task_bound_input(resources,task,definition.created_by)['workspace_input']
                if any(resources[g]['grant']['revoked'] for g in self._input_grant_ids(wi)):
                    raise ValueError('withdrawn graph input')
                context=self._attempt_context_refs(resources,definition.project_id,raw,definition.created_by)
                self._handoff_context_binding(resources,definition.project_id,context.model_dump(),definition.created_by,node.agent_ref.model_dump())
            from pyharness.application.workspace_store import canonical,digest_of
            command=TaskGraphCommand(**{k:getattr(definition,k) for k in TaskGraphCommand.model_fields if k!='expected_revision'},expected_revision=definition.revision-1)
            request=digest_of(canonical(dict(operation='task_graph.save',project_id=definition.project_id,command=command.model_dump())))
            if request!=event.payload['request_digest']:raise ValueError('graph command binding')
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='task_graph_definition_binding')

    def _graph_node_current(self,resources,project,node,actor,authorize,started):
        raw=node.model_dump();prerequisites=self._checked_task_prerequisites(resources,project,raw['task_ref'],actor,authorize,started)
        context=self._attempt_context_refs(resources,project,raw,actor)
        self._handoff_context_binding(resources,project,context.model_dump(),actor,node.agent_ref.model_dump())
        reasons,grants=self._attempt_observe(resources,prerequisites.model_dump(),context.model_dump(),actor,authorize,started)
        if reasons:raise_code('EVT-101',reason='task_graph_context_stale')
        return prerequisites,context,grants

    def _graph_revision(self,resources,project,ident,revision,actor):
        self._context_owned(resources,ident,'task_graph',project,actor)
        for event in self._history_events(resources):
            p=event.payload
            if (event.type=='workspace.resource_committed' and p['resource_kind']=='task_graph' and p['resource_id']==ident and p['revision']==revision):
                return dict(task_graph=self._read_definition(p).model_dump(),digest=p['digest'],source_seq=event.seq,
                    created_at=event.ts,created_by=p['actor_ref'],request_digest=p['request_digest'])
        raise_code('GRD-403',reason='task_graph_unavailable')

    def _graph_summary(self,resources,row,actor,authorize,started):
        from pyharness.application.task_graph_contracts import TaskGraphRevision
        graph=TaskGraphRevision.model_validate(row['task_graph']);project=graph.project_id
        self._context_owned({graph.graph_id:row},graph.graph_id,'task_graph',project,actor)
        current=self._context_project(resources,project);reasons=[];grants=set()
        if current['revision']!=graph.project_revision:reasons.append('project_revision_changed')
        for node in graph.nodes:
            task=self._product_task_revision(resources,project,node.task_ref.product_task_id,node.task_ref.revision,actor)
            if self._s05_ref(task,'product_task_id','product_task')!=node.task_ref.model_dump():raise_code('PERS-202',reason='task_graph_task_binding')
            prerequisites=dict(project_id=project,task_ref=node.task_ref.model_dump(),input_binding=task['product_task']['input_binding'])
            context=AttemptContext(**{k:getattr(node,k) for k in AttemptContext.model_fields})
            stale,node_grants=self._attempt_observe(resources,prerequisites,context.model_dump(),actor,authorize,started)
            reasons.extend(node.node_id+':'+reason for reason in stale);grants.update(node_grants)
        self._context_finish(authorize,actor,resources,grants)
        return dict(graph.model_dump(exclude={'created_by'}),digest=row['digest'],source_seq=row['source_seq'],
            created_at=row['created_at'],request_digest=row['request_digest'],persisted=True,status='retired' if graph.retired else 'defined',
            executable=False,execution_authorized=False,input_readiness='stale' if reasons else 'current',stale_reasons=list(dict.fromkeys(reasons)))
