"""Volatile, exact-runtime observations; no durable execution or replay authority."""
import asyncio,secrets
from contextlib import asynccontextmanager,suppress
from datetime import datetime,timezone

HEARTBEAT_INTERVAL_SECONDS=2.0
HEARTBEAT_EXPIRY_SECONDS=10.0


class ExecutionHeartbeat:
    def __init__(self):
        self.observer_id=secrets.token_hex(16)
        self.last_mono=None;self.last_utc=None;self.task=None

    def beat(self):
        self.last_mono=asyncio.get_running_loop().time()
        self.last_utc=datetime.now(timezone.utc).isoformat()

    async def _run(self):
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            self.beat()

    @asynccontextmanager
    async def monitor(self):
        if self.task is not None:
            from pyharness.errors import raise_code
            raise_code('GRD-402',reason='execution_heartbeat_already_owned')
        self.beat();self.task=asyncio.create_task(self._run(),name='product-execution-heartbeat')
        try:yield
        finally:await self.stop()

    async def stop(self):
        task=self.task;self.task=None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):await task

    def observation(self):
        age=None if self.last_mono is None else max(0.0,asyncio.get_running_loop().time()-self.last_mono)
        active=self.task is not None and not self.task.done()
        state=('not_observed' if age is None else 'expired' if age>HEARTBEAT_EXPIRY_SECONDS else 'fresh' if active else 'inactive')
        return dict(state=state,
            scope='service_event_loop',observer_id=self.observer_id,last_observed_at=self.last_utc,
            age_seconds=age,expires_after_seconds=HEARTBEAT_EXPIRY_SECONDS)


def pending_approval_observations(service,root,children):
    """Only requests owned by the already-validated root/child runtime scope."""
    tasks={root.sid:root.get(2).task_id}
    tasks.update({c['session_id']:'subagent:'+c['session_id'] for c in children if c['status'] in ('bound','running')})
    found={}
    for provider in service.all_approval_providers():
        pending=getattr(provider,'_pending',None)
        if not isinstance(pending,dict):continue
        for aid,req in list(pending.items()):
            sid=getattr(req,'session_id',None);log=getattr(req,'log',None)
            if sid not in tasks or getattr(req,'state',None)!='pending' or getattr(log,'sid',None)!=sid or getattr(log,'tenant_id',None)!=service.tenant_id:continue
            e=log.get(aid)
            if e is None or e.type!='approval.requested' or e.task_id!=tasks[sid] or e.payload['tool']!=getattr(req,'tool',None):continue
            found[(sid,aid)]=dict(session_id=sid,approval_id=aid,ttl_ms=e.payload['ttl_ms'])
    return list(sorted(found.values(),key=lambda r:(r['session_id'],r['approval_id'])))


def control_observation(projects,row,root,result):
    value=row['execution_authorization'];service=projects.service
    target=dict(execution_ref=dict(execution_id=value['execution_id'],revision=1,digest=row['digest'],source_seq=row['source_seq']),
        subject=projects._authorize(write=True),project_id=value['project_id'],tenant_id=service.tenant_id,
        session_id=value['session_id'],task_id=value['task_id'])
    queue=projects._owned_stop_queue(target)
    state=queue.status() if queue is not None else None
    owned=state is not None and (state.running==value['task_id'] or value['task_id'] in state.waiting)
    heartbeat=dict(state='not_observed',scope='service_event_loop',observer_id=None,last_observed_at=None,
        age_seconds=None,expires_after_seconds=HEARTBEAT_EXPIRY_SECONDS)
    waits=[]
    if owned:
        boundary=service._engines[value['session_id']].platform_runtime.product_execution
        heartbeat=boundary.heartbeat.observation()
        waits=pending_approval_observations(service,root,result['children'])
    terminal=result['status'] in ('completed','failed','cancelled')
    activity=('terminal' if terminal else 'interrupted' if not owned else
        'heartbeat_expired' if heartbeat['state']=='expired' else 'heartbeat_unavailable' if heartbeat['state']=='inactive' else 'waiting_approval' if waits else
        'queued' if value['task_id'] in state.waiting else 'running_unclassified')
    return dict(schema_version=1,service_status='connected',execution_owner='live' if owned else 'absent',
        activity=activity,heartbeat=heartbeat,approval_waits=waits[:400],approval_waits_total=len(waits),
        approval_waits_truncated=len(waits)>400,approval_coverage='partial' if result['children_truncated'] or any(c['status']=='unknown' for c in result['children']) else 'complete',
        replay_authorized=False,automatic_dispatch=False)
