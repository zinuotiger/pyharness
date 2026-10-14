"""OrchestrationService operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
from types import SimpleNamespace
from typing import Any, Optional
from pyharness.application.message_projection import _as_dict, _json_safe
if TYPE_CHECKING:
    pass
from pyharness.errors import raise_code
from pyharness.core.lifecycle import admitted

class OrchestrationService:
    """OrchestrationService responsibility; state is owned by the composing facade."""

    @admitted
    async def list_jobs(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        owner = self.owner_for(sid)
        rows = [self._job_dict(await spine.jobs.status(job_id, by=owner))
                for job_id in spine.jobs.list_owned(owner)]
        return {"jobs": rows, "count": len(rows)}

    @staticmethod
    def _job_dict(status: Any) -> dict:
        return _json_safe(_as_dict(status))

    @admitted
    async def start_job(self, sid: str, intent: str) -> str:
        self._ensure_open()
        intent = str(intent or "").strip()
        if not intent:
            raise_code("EVT-100", field="intent", advice="job intent 不能为空")
        log_ = await self.require_session(sid)
        await self.queue_for(sid, log_)
        spine = self._engines[sid]
        owner = self.owner_for(sid)
        ctx = SimpleNamespace(owner=owner, owner_channel=self.channel,
                              scope=getattr(spine, "scope", None),
                              tools=getattr(spine, "tools", None), budget=None)
        return await spine.jobs.start(intent, ctx)

    @admitted
    async def job_status(self, sid: str, job_id: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        return {"job": self._job_dict(
            await spine.jobs.status(job_id, by=self.owner_for(sid)))}

    @admitted
    async def cancel_job(self, sid: str, job_id: str) -> bool:
        _, spine = await self.public_spine_for(sid)
        return bool(await spine.jobs.cancel(job_id, by=self.owner_for(sid)))

    @admitted
    async def list_schedules(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        rows = [_json_safe(_as_dict(j)) for j in await spine.schedule.list_jobs()]
        return {"schedules": rows, "count": len(rows)}

    @admitted
    async def schedule_action(self, sid: str, action: str, *,
                              name: str = "", kind: str = "cron",
                              expr: str = "", intent: str = "",
                              is_risky: Optional[bool] = None) -> dict:
        self._ensure_open()
        action = str(action or "").strip().lower()
        name = str(name or "").strip()
        log_ = await self.require_session(sid)
        _, spine = await self.public_spine_for(sid)
        ctx = SimpleNamespace(session=getattr(spine, "session", None),
                              task_queue=getattr(spine, "task_queue", None))
        if action == "add":
            expr = str(expr or "").strip()
            intent = str(intent or "").strip()
            if not name or not expr or not intent:
                raise_code("EVT-100", advice="schedule add 需要 name/expr/intent")
            await self.queue_for(sid, log_)
            await spine.schedule.register(
                name, str(kind or "cron").strip().lower(), expr,
                {"intent": intent}, is_risky=is_risky, ctx=ctx)
        elif action == 'edit':
            await spine.schedule.edit(name,kind,expr,intent,ctx=ctx)
        elif action == 'run_now':
            await spine.schedule.run_now(name,ctx=ctx)
        elif action in {"pause", "resume", "remove"}:
            if not name:
                raise_code("EVT-100", field="name", advice=f"schedule {action} 需要 name")
            await getattr(spine.schedule, action)(name, ctx=ctx)
        else:
            raise_code("EVT-100", field="action",
                       advice="action 须为 add/pause/resume/remove")
        return {"ok": True, "action": action, "name": name}

    @admitted
    async def list_subagents(self, sid: str) -> dict:
        from pyharness.application.platform_projection import subtask_history
        log_ = await self.require_session(sid)
        spine = self._engines.get(sid)
        status = (_json_safe(_as_dict(spine.subagent.status())) if spine is not None else
                  {'running': 0, 'limit': 8, 'active_children': []})
        history = subtask_history(sid, log_.events_after(0), status['active_children'])
        waiting = {item['session_id'] for item in (await self.pending_approvals())['pending']}
        for row in history:
            if row['status'] == 'running' and row['session_id'] in waiting:
                row['status'] = 'waiting_approval'
            if row['error_code'] == 'runtime_interrupted':
                # A silent child still owns durable terminal facts in its log.
                # Do not construct an engine or replay work to recover history.
                from pyharness.application.platform_projection import summary
                child_log = await self.require_session(row['session_id'])
                for event in child_log.events_after(0):
                    if event.type in {'subagent.joined','subagent.failed'} and event.payload.get('sub_id') == row['session_id']:
                        row.update(status='completed' if event.type=='subagent.joined' else 'failed',
                            finished_at=event.ts,summary=summary(event.payload.get('summary','')),error_code=None)
        return {"status": status, 'history': history}

    @admitted
    async def spawn_subagent(self, sid: str, task: str, *,
                             tools_subset: Any = None,
                             deny_extra: Any = None,
                             budget_ratio: float = 0.25,
                             notify: bool = True) -> str:
        self._ensure_open()
        from pyharness.core.subagent import SubagentSpec
        task = str(task or "").strip()
        if not task:
            raise_code("EVT-100", field="task", advice="subagent task 不能为空")
        log_ = await self.require_session(sid)
        await self.queue_for(sid, log_)
        spine = self._engines[sid]
        spec = SubagentSpec(task=task, tools_subset=tools_subset,
                            deny_extra=list(deny_extra or []),
                            budget_ratio=float(budget_ratio), depth=1,
                            notify=bool(notify))
        ctx = SimpleNamespace(round_seq=int(log_.stats().get("seq", 1)),
                              owner=self.owner_for(sid),
                              scope=getattr(spine, "scope", None),
                              tools=getattr(spine, "tools", None),
                              bus=getattr(spine, "bus", None))
        return await spine.subagent.spawn(spec, ctx)

    @admitted
    async def cancel_subagent(self, sid: str, sub_id: str) -> bool:
        _, spine = await self.public_spine_for(sid)
        return bool(await spine.subagent.cancel(sub_id, by=self.owner_for(sid)))

    @admitted
    async def memory_action(self, body: dict) -> dict:
        """Explicit team namespace backed by the existing SessionKV owner."""
        import re
        from pyharness.core.kv import SessionKV
        if not isinstance(body,dict) or set(body)-{'op','key','value'}:
            raise_code('EVT-100',field='memory')
        op=body.get('op')
        key=body.get('key')
        value=body.get('value')
        if op not in {'get','set','delete','keys'}:
            raise_code('EVT-100',field='memory.op')
        from pyharness.application.accounts import Accounts, current_actor
        actor = current_actor.get()
        if actor is not None and actor.get('tenant') != self.tenant_id:
            raise_code('GRD-403', reason='memory_tenant_mismatch')
        if Accounts.manages_tenant(self._storage_root(), self.tenant_id):
            roles = Accounts(self._storage_root()).teams(actor['user_id']) if actor else []
            role = next((r['role'] for r in roles if r['id']==self.tenant_id),None)
            if role is None or (op in {'set','delete'} and role!='owner'):
                raise_code('GRD-403', reason='team_memory_read_only')
        if op!='keys' and (not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',key)):
            raise_code('EVT-100',field='memory.key')
        if op=='set' and (not isinstance(value,str) or len(value)>4096):
            raise_code('EVT-100',field='memory.value')
        if not hasattr(self,'_team_memory'):
            self._team_memory=SessionKV(self.tenant_root()/'memory.kv.json',session_id='team:'+self.tenant_id)
        kv=self._team_memory
        if op=='set':
            keys=kv.apply_op('keys')
            names=[] if keys=='(空)' else keys.split('、')
            if key not in names and len(names)>=100:
                raise_code('EVT-100',field='memory.quota')
        return {'scope':'team','result':kv.apply_op(op,key=key,value=value)}

    @admitted
    async def upload_attachment(self, sid: str, attachment: dict) -> dict:
        self._ensure_open()
        log_ = await self.require_session(sid)
        for payload in self.attachment_payloads([attachment], sid=sid):
            await log_.append("user.attachment.image", payload, actor="user")
        return {"ok": True, "sid": str(sid)}

    @admitted
    async def run_workflow(self, sid: str, steps: list, *,
                           name: str = "desktop",
                           stop_on_fail: bool = False) -> dict:
        self._ensure_open()
        if not isinstance(steps, list):
            raise_code("EVT-100", field="steps", advice="steps 须为非空字符串数组")
        log_ = await self.require_session(sid)
        queue = await self.queue_for(sid, log_)
        from pyharness.core.workflow import WorkflowRunner, queue_submit_adapter
        runner = WorkflowRunner(log_, queue_submit_adapter(log_, queue),
                                name=str(name or self.channel))
        results = await runner.run(steps, stop_on_fail=bool(stop_on_fail))
        return {"ok": all(r["ok"] for r in results), "sid": sid,
                "results": results}

