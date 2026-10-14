"""Background ownership for the existing WorkflowRunner, TaskQueue and SessionLog."""
from __future__ import annotations
import asyncio
import hashlib
import json
import uuid
from pyharness.application.platform_projection import summary
from pyharness.core.sandbox import fail
from pyharness.core.workflow import WorkflowRunner, record_workflow_task


class PlatformWorkflows:
    def __init__(self, platform):
        self.platform = platform
        self.tasks = {}
        self.logs = {}
        self.lock = asyncio.Lock()

    async def list(self):
        rows = []
        service = self.platform.service
        for session in (await service.list_sessions())['sessions']:
            log = await service.require_session(session['sid'])
            local = {}
            for event in log.events_after(0):
                p = event.payload
                spec = (p.get('meta') or {}).get('workflow') if event.type == 'user.message' else None
                if isinstance(spec, dict):
                    local[spec['workflow_id']] = {**spec, 'session_id':session['sid'],
                        'created_at':event.ts, 'step_states':{}, 'status':'running'}
                if event.type == 'workflow.step' and p.get('wf_name') in local:
                    local[p['wf_name']]['step_states'][p['step_idx']] = p['state']
                if event.type == 'system.cancelled' and p.get('what') in local:
                    local[p['what']]['status'] = 'cancelled'
            for ident, row in local.items():
                states = list(row['step_states'].values())
                row['completed_steps'] = states.count('done')
                if row['status'] != 'cancelled':
                    if 'failed' in states:
                        row['status'] = 'failed'
                    elif row['completed_steps'] == row['total_steps']:
                        row['status'] = 'completed'
                    elif ident not in self.tasks or self.tasks[ident].done():
                        row['status'] = 'failed'
                        row['error_code'] = 'runtime_interrupted'
                rows.append(row)
        return {'workflows':rows}

    async def start(self, body):
        self.platform.service._ensure_open()
        steps = body.get('steps')
        key = body.get('idempotency_key')
        if (not isinstance(steps,list) or not 1 <= len(steps) <= 20
                or any(not isinstance(s,str) or not s.strip() or len(s)>4000 for s in steps)
                or not isinstance(key,str) or not 1 <= len(key) <= 64):
            fail('invalid_workflow')
        if set(body) - {'steps','idempotency_key','session_id','agent_id','agent_version','name'}:
            fail('invalid_workflow')
        fingerprint = hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        async with self.lock:
            rows = (await self.list())['workflows']
            for row in rows:
                if row['idempotency_key'] == key:
                    if row['fingerprint'] != fingerprint:
                        fail('workflow_idempotency_conflict')
                    return row
            if sum(not t.done() for t in self.tasks.values()) >= 4:
                fail('workflow_busy')
            sid = await self.platform.schedule_session(body)
            log = await self.platform.service.require_session(sid)
            ident = 'wf-' + uuid.uuid4().hex
            record = {'workflow_id':ident,'idempotency_key':key,'fingerprint':fingerprint,
                      'name':summary(body.get('name') or '工作流',100),'total_steps':len(steps)}
            await log.append('user.message',{'content':'工作流已提交',
                'meta':{'workflow':record}},actor='user',sync=True)
            self.logs[ident] = log

            async def submit(intent, *, workflow_step=None):
                from pyharness.core.lifecycle import admitted
                @admitted
                async def enqueue(owner):
                    run = await self.platform.start_run({'session_id':sid,'text':intent})
                    if workflow_step is not None:
                        await record_workflow_task(log, workflow_step, run['task_id'])
                    return run
                admission = asyncio.create_task(enqueue(self.platform.service))
                try:
                    run = await asyncio.shield(admission)
                    queue = self.platform.service._queues[sid]
                    result = await queue.wait_for(run['task_id'])
                except asyncio.CancelledError:
                    run = await admission
                    queue = self.platform.service._queues[sid]
                    await queue.cancel(run['task_id'])
                    await queue.wait_for(run['task_id'])
                    raise
                return result.ok, '完成' if result.ok else '步骤失败', run['task_id']

            async def execute():
                try:
                    await WorkflowRunner(log,submit,name=ident,bind_steps=True).run(steps,stop_on_fail=True)
                except asyncio.CancelledError:
                    await log.append('system.cancelled',{'what':ident,'reason':'workflow_cancelled'},
                                     actor='system',sync=True)
                    raise

            self.tasks[ident] = asyncio.create_task(execute())
            self.tasks[ident].add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            return {**record,'session_id':sid,'status':'running'}

    async def wait(self, ident):
        task = self.tasks.get(ident)
        if task:
            await asyncio.shield(task)

    async def cancel(self, ident):
        row = next((r for r in (await self.list())['workflows'] if r['workflow_id']==ident),None)
        if row is None:
            fail('workflow_not_found')
        task = self.tasks.get(ident)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        await self._record_early_cancel(ident)
        return {'ok':True}

    async def _record_early_cancel(self, ident):
        task, log = self.tasks.get(ident), self.logs.get(ident)
        if task and task.cancelled() and log is not None:
            if not any(e.type=='system.cancelled' and e.payload.get('what')==ident for e in log.events_after(0)):
                await log.append('system.cancelled',{'what':ident,'reason':'workflow_cancelled'},actor='system',sync=True)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for ident in self.tasks:
            await self._record_early_cancel(ident)
        self.tasks.clear()
        self.logs.clear()
