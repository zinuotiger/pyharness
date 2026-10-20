"""DesktopHandlers operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
import asyncio
import secrets
from pathlib import Path
from typing import Any, Optional
from fastapi import Body, Header, Request
from fastapi.responses import StreamingResponse
from pyharness.application import ApplicationService
from pyharness.errors import raise_code
from .projection import StreamClient
if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    pass

class DesktopHandlers:
    """DesktopHandlers responsibility; state is owned by the composing facade."""

    async def create_session(self) -> dict:
        sid = await self.service.create_session()
        return {"sid": sid}

    async def list_jobs(self, sid: str) -> dict:
        return await self.service.list_jobs(sid)

    async def start_job(self, sid: str, body: dict = Body(default={})) -> dict:
        job_id = await self.service.start_job(sid, (body or {}).get("intent"))
        return {"ok": True, "job_id": job_id}

    async def job_status(self, sid: str, job_id: str) -> dict:
        return await self.service.job_status(sid, job_id)

    async def cancel_job(self, sid: str, job_id: str) -> dict:
        cancelled = await self.service.cancel_job(sid, job_id)
        return {"ok": True, "cancelled": cancelled}

    async def list_schedules(self, sid: str) -> dict:
        return await self.service.list_schedules(sid)

    async def schedule_action(self, sid: str,
                              body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.schedule_action(
            sid, body.get("action"), name=body.get("name"),
            kind=body.get("kind") or "cron", expr=body.get("expr"),
            intent=body.get("intent"), is_risky=body.get("is_risky"))

    async def list_subagents(self, sid: str) -> dict:
        return await self.service.list_subagents(sid)

    async def spawn_subagent(self, sid: str,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        ratio = body.get("budget_ratio")
        sub_id = await self.service.spawn_subagent(
            sid, body.get("task"), tools_subset=body.get("tools_subset"),
            deny_extra=body.get("deny_extra"),
            budget_ratio=float(ratio) if ratio is not None else 0.25,
            notify=bool(body.get("notify", True)))
        return {"ok": True, "sub_id": sub_id}

    async def cancel_subagent(self, sid: str, sub_id: str) -> dict:
        cancelled = await self.service.cancel_subagent(sid, sub_id)
        return {"ok": True, "cancelled": cancelled}

    def _plugin_roots(self) -> list[Path]:
        return self.service.plugin_roots()

    @staticmethod
    def _plug_ctx_for(spine: Any) -> Any:
        return ApplicationService._plug_ctx_for(spine)

    async def list_plugins(self) -> dict:
        return await self.service.list_plugins()

    async def plugin_load(self, body: dict) -> dict:
        return await self.service.plugin_action("load", body)

    async def plugin_unload(self, body: dict) -> dict:
        return await self.service.plugin_action("unload", body)

    async def plugin_reload(self, body: dict) -> dict:
        return await self.service.plugin_action("reload", body)

    @staticmethod
    def _reset_preset_state(spine: Any) -> None:
        return ApplicationService._reset_preset_state(spine)

    async def set_preset(self, body: dict) -> dict:
        return await self.service.set_preset(body)

    def _skills_mgr(self) -> Any:
        return self.service.skills_mgr()

    async def capabilities(self) -> dict:
        return self.service.capabilities()

    async def tenant_state(self) -> dict:
        return self.service.tenant_state()

    async def list_model_profiles(self) -> dict:
        return self.service.tenant_state()

    async def save_model_profile(self,
                                 body: dict = Body(default={})) -> dict:
        return self.service.save_model_profile(body or {})

    async def activate_model_profile(self, profile_id: str) -> dict:
        return self.service.activate_model_profile(profile_id)

    async def delete_model_profile(self, profile_id: str) -> dict:
        return self.service.delete_model_profile(profile_id)

    async def list_skills(self) -> dict:
        return await self.service.list_skills()

    async def skill_detail(self, name: str) -> dict:
        return await self.service.skill_detail(name)

    async def reload_skills(self, body: dict = None) -> dict:
        return await self.service.reload_skills()

    async def search_skills(self, q: str = "", registry_url: str = "") -> dict:
        return await self.service.search_skills(q, registry_url)

    async def install_skill(self, sid: str,
                            body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.install_skill(
            sid, str(body.get("name") or ""), version=str(body.get("version") or ""),
            registry_url=str(body.get("registry_url") or ""),
            approved_by="web-ui")

    async def rollback_skill(self, sid: str, name: str,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.rollback_skill(
            sid, name, str(body.get("version") or ""), approved_by="web-ui")

    async def remove_skill(self, sid: str, name: str,
                           body: dict = Body(default={})) -> dict:
        return await self.service.remove_skill(
            sid, name, approved_by="web-ui")

    async def skill_versions(self, name: str) -> dict:
        versions = self.service.skill_versions(name)
        return {"name": name, "versions": versions, "count": len(versions)}

    async def list_sessions(self) -> dict:
        return await self.service.list_sessions()

    async def delete_session(self, sid: str) -> dict:
        return await self.service.delete_session(sid)

    async def session_messages(self, sid: str, after_seq: int = 0) -> dict:
        return await self.service.session_messages(sid, after_seq)

    async def session_timeline(self, sid: str, after_seq: int = 0,
                               kinds: str = "") -> dict:
        return await self.service.session_timeline(sid, after_seq, kinds)

    async def event_detail(self, sid: str, seq: int) -> dict:
        return await self.service.event_detail(sid, seq)

    async def telemetry_report(self, sid: str) -> dict:
        return await self.service.telemetry_report(sid)



    async def stream_sse(self, request: Request, sid: str = "",
                         after_seq: int = 0) -> StreamingResponse:
        """SSE 事件流:注册 StreamClient(游标 after_seq)→ 总线事件按 seq 序 fan-out;
        断开 → 客户端带 last_seq 重连续拉(F065 边界);15s 心跳注释行保活。"""
        from pyharness.application.product_execution_sessions import reject_product_mutation
        reject_product_mutation(sid)
        me = StreamClient(sid=str(sid or ""), tenant=self.current_tenant(),
                          last_seq=max(0, int(after_seq)))

        async def gen():
            # 注册生命周期必须覆盖响应体实际迭代期:ASGI/uvicorn 在视图函数返回后才
            # 流式读取 body,若像伪码那样在视图内 `async with register` 再 return,
            # __aexit__ 会在流开始前就把游标注销 → SSE 只见心跳收不到事件。故注册
            # 移入生成器内部:首个 anext 进入注册,生成器净退/断连/异常时 __aexit__
            # 自动注销(行为契约修正,偏离 6 同条)。
            async with self.hub.register(me):
                while not self.stopping.is_set():
                    if await request.is_disconnected():
                        break                       # 前端断开 → 回收游标
                    try:
                        item = await asyncio.wait_for(me.queue.get(),
                                                      timeout=self._sse_heartbeat_seconds())
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"          # 心跳注释行(SSE 保活)
                        continue
                    if item is None:                # hub.close_all 哨兵 → 净退
                        break
                    type_, text = item
                    yield f"event: {type_}\ndata: {text}\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"})

    async def create_message(self, sid: str, body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.create_message(
            sid, body.get("text"), attachments=body.get("attachments"))

    async def decide_approval_for(self, sid: str, aid: int,
                                  body: dict = Body(default={})) -> dict:
        return await self.service.decide_approval(
            int(aid), (body or {}).get("decision"), sid=sid)

    async def decide_approval(self, aid: int,
                              body: dict = Body(default={})) -> dict:
        return await self.service.decide_approval(
            int(aid), (body or {}).get("decision"))

    async def _decide_approval(self, aid: int, body: dict, *,
                               sid: Optional[str] = None) -> dict:
        return await self.service.decide_approval(
            int(aid), (body or {}).get("decision"), sid=sid)

    @staticmethod
    def _approval_sid_of(provider: Any, aid: int) -> str:
        return ApplicationService._approval_sid_of(provider, aid)

    def _provider_owning(self, aid: int, *, sid: Optional[str] = None) -> Any:
        return self.service.provider_owning(aid, sid=sid)

    async def edit_message(self, sid: str, seq: int,
                           body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.edit_message(
            sid, int(seq), body.get("text"), resend=bool(body.get("resend")))

    async def resend_message(self, sid: str, seq: int) -> dict:
        return await self.service.resend_message(sid, int(seq))

    async def feedback(self, sid: str, seq: int,
                       body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.feedback(
            sid, int(seq), body.get("kind"), body.get("note") or "")

    async def webhook_ingest(self, body: dict = Body(default={}),
                             x_webhook_token: Optional[str] = Header(default=None),
                             authorization: Optional[str] = Header(default=None)) -> dict:
        body = body or {}
        self._webhook_authorize(body, self._api_token,
                                x_webhook_token=x_webhook_token,
                                authorization=authorization)
        text = str(body.get("content") or body.get("text") or "").strip()
        if not text:
            raise_code("EVT-100", advice="webhook content 不能为空")
        sid = str(body.get("session_id") or "")
        if not sid or sid not in self._logs:
            sid = await self.service.create_session()
        log_ = await self.service.require_session(sid)
        resend = await self.service.resend_text(log_, sid, text)
        return {"sid": sid, **resend}

    def _webhook_token(self) -> str:
        return self._api_token

    @staticmethod
    def _webhook_authorize(body: dict, expected: str, *,
                           x_webhook_token: Optional[str] = None,
                           authorization: Optional[str] = None) -> None:
        if not expected:
            return
        given = str(body.get("token") or x_webhook_token or "")
        if not given and authorization and authorization.lower().startswith("bearer "):
            given = authorization[7:].strip()
        if not given or not secrets.compare_digest(given, expected):
            raise_code("CRED-701", reason="webhook-token",
                       hint="webhook token 缺失或不匹配(拒绝注入)")

    @staticmethod
    def _last_env_of(log_: Any, type_: str):
        return ApplicationService._last_env_of(log_, type_)

    async def edit_last_user(self, sid: str,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.edit_last_user(
            sid, body.get("text"), resend=bool(body.get("resend")))

    async def resend_last_user(self, sid: str) -> dict:
        return await self.service.resend_last_user(sid)

    async def feedback_last_agent(self, sid: str,
                                  body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.feedback_last_agent(
            sid, body.get("kind"), body.get("note") or "")

    async def pending_approvals(self) -> dict:
        return await self.service.pending_approvals()

    def _ask_providers(self) -> list:
        return self.service.ask_providers()

    def _sid_of_ask(self, provider: Any) -> str:
        return self.service.sid_of_ask(provider)

    async def pending_asks(self) -> dict:
        return await self.service.pending_asks()

    async def answer_ask_for(self, sid: str, ask_id: int,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.answer_ask(
            int(ask_id), choice=body.get("choice"), text=body.get("text"), sid=sid)

    async def answer_ask(self, ask_id: int,
                         body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.answer_ask(
            int(ask_id), choice=body.get("choice"), text=body.get("text"))

    async def _answer_ask(self, ask_id: int, body: dict, *,
                          sid: Optional[str] = None) -> dict:
        return await self.service.answer_ask(
            int(ask_id), choice=body.get("choice"), text=body.get("text"), sid=sid)

    async def budget_dashboard(self, sid: str) -> dict:
        return await self.service.budget_dashboard(sid)

    async def upload_attachment(self, body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.upload_attachment(
            body.get("sid"), body.get("attachment", {}))

    async def run_workflow(self, sid: str,
                           body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.run_workflow(
            sid, body.get("steps"), name=body.get("name") or "desktop",
            stop_on_fail=bool(body.get("stop_on_fail")))

