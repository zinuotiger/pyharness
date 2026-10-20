"""Authenticated graph definition operations; no model readiness or execution."""
from pydantic import TypeAdapter,Field
from typing import Annotated
from pyharness.application.task_graph_contracts import TaskGraphCommand,TaskGraphId
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.workspace_input_pages import WorkspaceInputQuery
from pyharness.application.task_graph_run_contracts import PreviewTaskGraph
from pyharness.core.lifecycle import admitted
from pyharness.errors import raise_code


class ProductTaskGraphs:
    @admitted
    async def task_graph_cancellation_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.task_graph_cancellation_submission(project,key,authorize=lambda:self._authorize(write=True))

    @admitted
    async def task_graph_runs(self,project,query):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        return await self.store.task_graph_runs(project,query,authorize=lambda:self._authorize(write=True))

    @admitted
    async def start_task_graph(self,project,body,command_id):
        import asyncio
        lock=self.service.__dict__.setdefault('_task_graph_admission_lock',asyncio.Lock())
        async with lock:return await self._start_task_graph(project,body,command_id)

    async def _start_task_graph(self,project,body,command_id):
        from pyharness.application.task_graph_run_contracts import StartTaskGraph
        from pyharness.application.task_graph_runtime import GraphOwner,run_owned_graph,graph_observation
        import asyncio
        actor=self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        command=StartTaskGraph.model_validate(body);key=self._workspace_id(command_id,SubmissionKey)
        owners=self.service.__dict__.setdefault('_task_graph_owners',{})
        old=await self.store.task_graph_run_submission(project,key,authorize=lambda:self._authorize(write=True))
        if old['status']=='committed':
            from pyharness.application.task_graph_control_store import control_request
            if old['graph_run']['request_digest']!=control_request(project,'task_graph.start',command.model_dump()):raise_code('EVT-100',reason='idempotency_conflict')
            return await graph_observation(self,old['graph_run'])
        if sum(not o.closed and not o.task.done() for o in owners.values())>=4:raise_code('EVT-104',reason='graph_active_limit')
        receipt,first=await self.store.authorize_task_graph_run(project,command,command_id=key,authorize=lambda:self._authorize(write=True),describe=self._describe_graph_execution)
        if first:
            owner=GraphOwner(self,receipt,actor);owners[command.graph_run_id]=owner
            owner.task=asyncio.create_task(run_owned_graph(self,owner))
        return await graph_observation(self,receipt)

    @admitted
    async def task_graph_run_control(self,project,ident):
        from pyharness.application.task_graph_contracts import TaskGraphRunId
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,TaskGraphRunId)
        return await self.store.task_graph_run_control(project,ident,authorize=lambda:self._authorize(write=True))

    @admitted
    async def task_graph_run(self,project,ident):
        from pyharness.application.task_graph_contracts import TaskGraphRunId
        from pyharness.application.task_graph_runtime import graph_observation
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,TaskGraphRunId)
        authority=await self.store.task_graph_run(project,ident,authorize=lambda:self._authorize(write=True))
        return await graph_observation(self,authority)

    @admitted
    async def task_graph_run_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.task_graph_run_submission(project,key,authorize=lambda:self._authorize(write=True))

    @admitted
    async def cancel_task_graph(self,project,body,command_id):
        from pyharness.application.task_graph_run_contracts import CancelTaskGraph
        actor=self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(command_id,SubmissionKey)
        command=CancelTaskGraph.model_validate(body)
        owner=getattr(self.service,'_task_graph_owners',{}).get(command.graph_run_ref.graph_run_id)
        try:row,run=await self.store.cancel_task_graph(project,command,command_id=key,authorize=lambda:self._authorize(write=True))
        except Exception as exc:
            from pyharness.errors import PyHError
            if isinstance(exc,PyHError) and exc.code=='PERS-202' and owner is not None and owner.actor==actor and owner.reference==command.graph_run_ref.model_dump() and not owner.task.done():
                owner.task.cancel()
                import asyncio
                await asyncio.gather(owner.task,return_exceptions=True)
            raise
        if owner is not None and owner.reference==command.graph_run_ref.model_dump() and not owner.task.done():
            owner.task.cancel()
            import asyncio
            await asyncio.gather(owner.task,return_exceptions=True)
        from pyharness.application.task_graph_runtime import GraphOwner
        if owner is None:
            proxy=GraphOwner(self,dict(run['task_graph_run'],digest=run['digest'],source_seq=run['source_seq']),row['created_by'])
            await proxy.stop_children()
        return dict(schema_version=1,project_id=project,cancellation_id=command.cancellation_id,graph_run_ref=command.graph_run_ref.model_dump(),
            digest=row['digest'],source_seq=row['source_seq'],request_digest=row['request_digest'],persisted=True,cancel_requested=True,execution_stopped=None,automatic_dispatch=False)

    def _describe_graph_execution(self,reference):
        import math
        from pyharness.application.execution_configuration import compiled_configuration
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.core.llm_usage import _price_table
        self._graph_agent_version(reference);snapshot,cfg=compiled_configuration(self,reference)
        prices=_price_table(cfg.llm.usage.unit_price);models=[cfg.llm.model,*(cfg.llm.fallback_models or [])]
        if not all(model in prices and all(math.isfinite(rate) and rate>=0 for rate in prices[model].values()) for model in models):
            raise_code('EVT-100',reason='task_graph_model_pricing_unconfirmed')
        try:fingerprint=digest_of(canonical(cfg.model_dump(mode='json')))
        except ValueError:raise_code('EVT-100',reason='task_graph_configuration_unavailable')
        return dict(agent_snapshot=snapshot,model_fingerprint=fingerprint,
            boundary={'disabled':'disabled','host_approved':'host_unrestricted','isolated':'container','remote':'remote'}[snapshot['sandbox_profile']['mode']])

    @admitted
    async def task_graph_run_preview(self,project,body):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);command=PreviewTaskGraph.model_validate(body)
        return await self.store.task_graph_run_preview(project,command,authorize=lambda:self._authorize(write=True),describe=self._describe_graph_execution)

    def _graph_agent_version(self,reference):
        self._authorize(write=True)
        version=self.service.platform.new_binding_version(reference.agent_id,reference.version)
        if version['sha256']!=reference.sha256:raise_code('EVT-101',reason='task_graph_agent_version')
        return version

    @admitted
    async def task_graphs(self,project,*,body=None,command_id=None,query=None):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        if body is None:return await self.store.task_graphs(project,query or WorkspaceInputQuery(),authorize=lambda:self._authorize(write=True))
        command=TaskGraphCommand.model_validate(body);key=self._workspace_id(command_id,SubmissionKey)
        return await self.store.save_task_graph(project,command,command_id=key,authorize=lambda:self._authorize(write=True),describe=self._graph_agent_version)

    @admitted
    async def task_graph(self,project,ident,revision):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,TaskGraphId)
        revision=TypeAdapter(Annotated[int,Field(ge=1,le=200,strict=True)]).validate_python(revision)
        return await self.store.task_graph(project,ident,revision,authorize=lambda:self._authorize(write=True))

    @admitted
    async def task_graph_submission(self,project,key):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);key=self._workspace_id(key,SubmissionKey)
        return await self.store.task_graph_submission(project,key,authorize=lambda:self._authorize(write=True))
