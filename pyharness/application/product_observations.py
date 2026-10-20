"""Authenticated projections over original execution facts; no runtime creation."""
from typing import Annotated
from pydantic import Field
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.execution_contracts import ExecutionId,ExecutionSessionId
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.application.execution_child_reader import read_child_log
from pyharness.application.execution_child_graph import child_admissions
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.lifecycle import admitted
from pyharness.errors import raise_code

ObservationId=Annotated[str,Field(pattern=r'^co-[0-9a-f]{16}$')]


def confirmation(events,payload):
    if payload['request_seq'] is None:return 'unavailable_for_adapter'
    for event in events:
        if event.seq<=payload['prefix_seq'] or event.payload.get('request_seq')!=payload['request_seq']:continue
        if event.type=='llm.response':return 'response_recorded'
        if event.type=='llm.error':return 'error_recorded'
    return 'not_confirmed'


class ProductObservations:
    @admitted
    async def compare_executions(self,project,body):
        from pyharness.application.observation_contracts import CompareExecutions
        from pyharness.application.run_observation_comparison import run_view,compare_runs
        self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        command=CompareExecutions.model_validate(body)
        async def read(views):
            left=await run_view(self,*views[0]);right=await run_view(self,*views[1])
            return dict(compare_runs(left,right),project_id=project)
        return await self.store.observe_execution_pair(project,(command.left_execution_id,command.right_execution_id),
            authorize=lambda:self._authorize(write=True),read=read)

    @admitted
    async def permission_preview(self,project,body):
        from pyharness.application.observation_contracts import PermissionPreview
        from pyharness.application.execution_contracts import PreviewExecution
        from pyharness.application.execution_configuration import compiled_configuration,describe_execution
        from pyharness.application.permission_observation import predict_permission
        actor=self._authorize(write=True);project=self._workspace_id(project,ProjectId)
        command=PermissionPreview.model_validate(body)
        selection=PreviewExecution.model_validate(command.model_dump(include={'schema_version','attempt_ref','agent_ref'}))
        authorize=lambda:self._authorize(write=True)
        snapshot,cfg=compiled_configuration(self,command.agent_ref)
        expected=describe_execution(self,command.agent_ref)
        describe=lambda:describe_execution(self,command.agent_ref)
        materials=await self.store.capture_execution_materials(project,selection,authorize=authorize,describe=describe)
        if describe()!=expected:raise_code('EVT-101',reason='permission_configuration_changed')
        result=await predict_permission(snapshot,cfg,materials,command.tool_name,command.arguments)
        # Never hold the original control lock over a Guard await. Re-read and
        # validate the exact basis after it, including fresh grant/Actor checks.
        current=await self.store.capture_execution_materials(project,selection,authorize=authorize,describe=describe)
        if authorize()!=actor:raise_code('GRD-403',reason='permission_subject_changed')
        if describe()!=expected or current['materials_digest']!=materials['materials_digest']:
            raise_code('EVT-101',reason='permission_basis_changed')
        return dict(result,project_id=project,attempt_ref=command.attempt_ref.model_dump(),
            agent_ref=command.agent_ref.model_dump(),materials_digest=materials['materials_digest'],
            policy_fingerprint=expected['model_fingerprint'],persisted=False)

    @admitted
    async def execution_contexts(self,project,ident,session_id=None,observation_id=None):
        self._authorize(write=True);project=self._workspace_id(project,ProjectId);ident=self._workspace_id(ident,ExecutionId)
        if session_id is not None:session_id=self._workspace_id(session_id,ExecutionSessionId)
        if observation_id is not None:observation_id=self._workspace_id(observation_id,ObservationId)
        async def read(row,summary):
            directory=self.service._tenant_sessions_dir()
            root=await read_execution_log(directory,row,self.service.tenant_id);root_events=list(root.events_after(0))
            admissions=child_admissions(root_events,row)
            children=[dict(session_id=sid,depth=event.payload['depth']) for sid,event in admissions.items()]
            sid=session_id or root.sid
            if sid==root.sid:log=root
            else:
                if sid not in admissions:raise_code('GRD-403',reason='public_context_session_unavailable')
                log=await read_child_log(directory,row,self.service.tenant_id,sid)
            events=list(log.events_after(0));facts=[e for e in events if e.type=='execution.context']
            common=dict(schema_version=1,project_id=project,execution_id=ident,session_id=sid,
                attempt_ref=summary['attempt_ref'],agent_ref=summary['agent_ref'],execution_authorized=False)
            if observation_id is not None:
                fact=next((e for e in facts if e.payload['observation_id']==observation_id),None)
                if fact is None:raise_code('GRD-403',reason='public_context_observation_unavailable')
                return dict(common,**fact.payload,task_id=fact.task_id,source_seq=fact.seq,created_at=fact.ts,
                    public_view_canonical=canonical(fact.payload['public_view']).decode('utf-8'),
                    dispatch_confirmation=confirmation(events,fact.payload),event_digest=digest_of(canonical(fact.model_dump(mode='json'))))
            items=[]
            for event in facts[-20:]:
                p=event.payload
                items.append(dict(observation_id=p['observation_id'],source_seq=event.seq,created_at=event.ts,
                    layer=p['layer'],model=p['model'],method=p['method'],role=p['role'],attempt=p['attempt'],phase=p['phase'],
                    counts=p['public_view']['counts'],truncated=p['public_view']['truncated'],withheld=p['public_view']['withheld'],
                    public_view_digest=p['public_view_digest'],source_content_state=p['source_content_state'],
                    dispatch_confirmation=confirmation(events,p)))
            return dict(common,context_status='available' if facts else 'unavailable',reason=None if facts else 'no_recorded_context',
                items=items,total=len(facts),truncated=len(facts)>20,child_sessions=children)
        return await self.store.observe_execution(project,ident,authorize=lambda:self._authorize(write=True),read=read)
