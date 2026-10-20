"""Strong dispatch and minimal cancellation facts on the original control log."""
import time,secrets
from pyharness.application.task_graph_run_contracts import GraphDispatch,GraphCancellation
from pyharness.errors import PyHError,raise_code


def control_request(project,kind,command):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation=kind,project_id=project,command=command)))


class TaskGraphControlStore:
    async def task_graph_cancellation_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands=self._cancellation_state()
            if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='graph_control_project_unavailable')
            old=commands.get((actor,key));row=old[1] if old else None
            if row and ('task_graph_cancellation' not in row or row['task_graph_cancellation']['project_id']!=project):raise_code('GRD-403',reason='graph_cancel_submission_unavailable')
            if row:
                value=row['task_graph_cancellation'];self._graph_control_run(resources,project,value['graph_run_ref'],actor)
                result=dict(value,digest=row['digest'],source_seq=row['source_seq'],request_digest=row['request_digest'],persisted=True)
                result.pop('created_by')
            else:result=None
            if authorize()!=actor:raise_code('GRD-403',reason='graph_control_subject_changed')
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if row else 'not_found',cancellation=result,control_only=True,automatic_dispatch=False)

    async def task_graph_run_control(self,project,ident,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_=self._cancellation_state()
            row=self._context_owned(resources,ident,'task_graph_run',project,actor)
            reference=self._s05_ref(row,'graph_run_id','task_graph_run')
            if authorize()!=actor:raise_code('GRD-403',reason='graph_control_subject_changed')
            return dict(schema_version=1,project_id=project,control_only=True,graph_run_ref=reference,
                submission_key=row['task_graph_run']['submission_key'],request_digest=row['request_digest'],
                cancel_requested=self._graph_cancelled(resources,reference),automatic_dispatch=False)

    def _graph_control_run(self,resources,project,reference,actor):
        if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='graph_control_project_unavailable')
        row=self._context_owned(resources,reference['graph_run_id'],'task_graph_run',project,actor)
        if self._s05_ref(row,'graph_run_id','task_graph_run')!=reference:raise_code('EVT-101',reason='graph_run_reference')
        return row

    def _graph_cancelled(self,resources,reference):
        return any(r.get('task_graph_cancellation',{}).get('graph_run_ref')==reference for r in resources.values())

    def _validate_graph_control(self,definition,resources,previous,event,kind):
        value=definition.model_dump();actor=value['created_by'];project=value['project_id'];ref=value['graph_run_ref']
        try:
            if previous or actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id']:raise ValueError('control owner')
            run=self._graph_control_run(resources,project,ref,actor)['task_graph_run']
            if kind=='task_graph_cancellation':
                if self._graph_cancelled(resources,ref):raise ValueError('graph already stopped')
                self._context_quota(resources,kind,project)
            if kind=='task_graph_dispatch':
                if self._graph_cancelled(resources,ref) or value['node_id'] not in {n['node_id'] for n in run['nodes']}:raise ValueError('node dispatch scope')
                if any(r.get(kind,{}).get('graph_run_ref')==ref and r[kind]['node_id']==value['node_id'] for r in resources.values()):raise ValueError('node already dispatched')
            selected={k:v for k,v in value.items() if k not in {'revision','project_id','created_by','submission_key'}}
            if control_request(project,kind,selected)!=event.payload['request_digest']:raise ValueError('control command')
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='graph_control_binding')

    async def append_graph_dispatch(self,project,reference,node,*,authorize,ready):
        started=time.monotonic();kind='task_graph_dispatch';ident='gd-'+secrets.token_hex(8)
        key='graph-dispatch-'+reference['graph_run_id']+'-'+node
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            if (actor,key) in commands:raise_code('EVT-101',reason='graph_dispatch_already_reserved')
            definition=GraphDispatch(dispatch_id=ident,project_id=project,created_by=actor,submission_key=key,graph_run_ref=reference,node_id=node)
            selected=definition.model_dump(exclude={'revision','project_id','created_by','submission_key'})
            request=control_request(project,kind,selected)
            def gate():
                fresh,_=self._grant_state();run=self._graph_control_run(fresh,project,reference,actor)
                if authorize()!=actor or self._graph_cancelled(fresh,reference):raise_code('GRD-403',reason='graph_dispatch_stopped')
                self._graph_run_authority(fresh,run,actor,authorize,started);ready()
                if any(r.get(kind,{}).get('graph_run_ref')==reference and r[kind]['node_id']==node for r in fresh.values()):raise_code('EVT-101',reason='graph_dispatch_already_reserved')
            gate();_,row=await self._append_context(definition,kind,ident,key,actor,request,gate)
            return dict(graph_run_ref=reference,node_id=node,intent_seq=row['source_seq'],intent_digest=row['digest'])

    async def cancel_task_graph(self,project,command,*,command_id,authorize):
        started=time.monotonic();kind='task_graph_cancellation';selection=command.model_dump();ref=selection['graph_run_ref']
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open_projected(initialize=False,project=self._project_cancellation_control)
            resources,commands=self._cancellation_state();run=self._graph_control_run(resources,project,ref,actor)
            request=control_request(project,kind,selection)
            self._context_envelope(kind,command.cancellation_id,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request or kind not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                return old[1],run
            if command.cancellation_id in resources:raise_code('EVT-101',reason='graph_cancellation_identity_reserved')
            definition=GraphCancellation(**selection,project_id=project,created_by=actor,submission_key=command_id)
            def gate():
                fresh,_=self._cancellation_state();self._graph_control_run(fresh,project,ref,actor)
                if authorize()!=actor:raise_code('GRD-403',reason='graph_cancel_subject_changed')
                if command.cancellation_id in fresh:raise_code('EVT-101',reason='graph_cancellation_identity_reserved')
                if self._graph_cancelled(fresh,ref):raise_code('EVT-101',reason='graph_already_cancel_requested')
                self._context_quota(fresh,kind,project)
            gate()
            _,row=await self._append_context_projected(definition,kind,command.cancellation_id,command_id,actor,request,gate,self._cancellation_state)
            return row,run
