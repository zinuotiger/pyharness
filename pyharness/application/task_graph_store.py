"""Versioned DAG definitions on the original control owner, without dispatch."""
import time
from pyharness.application.task_graph_support import TaskGraphSupport
from pyharness.application.task_graph_run_store import TaskGraphRunStore
from pyharness.application.task_graph_contracts import TaskGraphRevision
from pyharness.application.workspace_input_pages import workspace_input_page
from pyharness.errors import PyHError,raise_code


from pyharness.application.task_graph_control_store import TaskGraphControlStore
from pyharness.application.task_graph_node_store import TaskGraphNodeStore


class TaskGraphStore(TaskGraphSupport,TaskGraphRunStore,TaskGraphControlStore,TaskGraphNodeStore):
    async def save_task_graph(self,project,command,*,command_id,authorize,describe):
        from pyharness.application.workspace_store import canonical,digest_of
        started=time.monotonic();ident=command.graph_id
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=digest_of(canonical(dict(operation='task_graph.save',project_id=project,command=command.model_dump())))
            self._context_envelope('task_graph',ident,command.expected_revision+1,command_id,actor,request)
            self._context_project(resources,project,command.project_revision)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                return self._graph_summary(resources,old[1],actor,authorize,started)
            versions={}
            def gate():
                fresh,_=self._grant_state();self._context_project(fresh,project,command.project_revision)
                prior=fresh.get(ident)
                if prior:
                    self._context_owned(fresh,ident,'task_graph',project,actor)
                    if prior['task_graph']['retired']:raise_code('GRD-403',reason='task_graph_retired')
                if (prior['task_graph']['revision'] if prior else 0)!=command.expected_revision:raise_code('EVT-101',reason='stale_revision')
                self._context_quota(fresh,'task_graph',project);grants=set()
                for node in command.nodes:
                    _,_,owned=self._graph_node_current(fresh,project,node,actor,authorize,started);grants.update(owned)
                    version=describe(node.agent_ref)
                    if node.node_id in versions and versions[node.node_id]!=version:raise_code('EVT-101',reason='task_graph_agent_changed')
                    versions[node.node_id]=version
                self._context_finish(authorize,actor,fresh,grants)
            gate()
            definition=TaskGraphRevision(**command.model_dump(exclude={'expected_revision'}),project_id=project,revision=command.expected_revision+1,created_by=actor)
            resources,row=await self._append_context(definition,'task_graph',ident,command_id,actor,request,gate)
            try:return self._graph_summary(resources,row,actor,authorize,started)
            except PyHError:raise_code('PERS-202',reason='workspace_commit_receipt_unavailable')

    async def task_graph(self,project,ident,revision,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._graph_revision(resources,project,ident,revision,actor)
            return self._graph_summary(resources,row,actor,authorize,started)

    async def task_graph_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state();self._context_project(resources,project)
            old=commands.get((actor,key));row=old[1] if old else None
            if row and (not row.get('task_graph') or row['task_graph']['project_id']!=project):raise_code('GRD-403',reason='task_graph_submission_unavailable')
            graph=self._graph_summary(resources,row,actor,authorize,started) if row else None
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if row else 'not_found',graph=graph,execution_authorized=False,writer_requires_reopen=self._failed)

    async def task_graphs(self,project,query,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,events=await self._execution_read_state();self._context_project(resources,project);rows=[]
            for row in resources.values():
                graph=row.get('task_graph')
                if not graph or graph['project_id']!=project or graph['created_by']!=actor:continue
                if authorize()!=actor:raise_code('GRD-403',reason='task_graph_subject_changed')
                try:rows.append(self._graph_summary(resources,row,actor,authorize,started))
                except PyHError as exc:
                    if exc.code!='GRD-403':raise
            self._context_finish(authorize,actor,resources)
            if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='task_graph_observation_time_limit')
            return workspace_input_page(rows,query,kind='graphs',project=project,actor=actor,watermark=len(events))
