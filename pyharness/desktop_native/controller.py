"""Async controller bridging ApplicationService to Qt signals."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import QObject, Signal

from pyharness.application import ApplicationService, ApplicationServiceRegistry
from pyharness.core.tenant_settings import normalize_tenant_id, tenant_for_session
from pyharness.events import EVENT_TYPES

log = logging.getLogger("pyharness.desktop_native.controller")


class NativeController(QObject):
    """Qt-facing asynchronous controller; contains no HTTP or WebView logic."""

    event_received = Signal(str, object)
    chunk_received = Signal(object)
    sessions_changed = Signal()
    jobs_changed = Signal()
    schedules_changed = Signal()
    subagents_changed = Signal()
    error = Signal(str)

    def __init__(self, ctx: Any = None, *, service: Any = None,
                 parent: Any = None, tenant_id: str = "default") -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.tenant_id = normalize_tenant_id(tenant_id)
        self._provided_service = service
        self._registry = (None if service is not None else
                          ApplicationServiceRegistry(
                              ctx, channel="desktop-native"))
        self.service = service or self._registry.get(self.tenant_id)
        self._subs: list[Any] = []
        self.selected_sid: str = ""
        bus = getattr(ctx, "bus", None) if ctx is not None else None
        if bus is not None:
            for type_ in EVENT_TYPES:
                try:
                    self._subs.append(bus.subscribe(
                        type_, self._on_bus_event, owner="desktop-native"))
                except Exception:                      # noqa: BLE001
                    log.debug("native bus subscribe skipped %s", type_)

    @property
    def default_registry_url(self) -> str:
        cfg = getattr(self.ctx, "settings", None) or getattr(self.ctx, "config", None)
        try:
            return str(getattr(getattr(cfg, "skills", None),
                               "registry_url", "") or "")
        except Exception:                              # noqa: BLE001
            return ""

    def _event_allowed(self, payload: Any) -> bool:
        sid = (getattr(payload, "session_id", "") if not isinstance(payload, dict)
               else (payload or {}).get("session_id"))
        if not sid:
            return self.tenant_id == "default"
        owner = tenant_for_session(str(sid))
        return not owner or owner == self.tenant_id

    def _on_bus_event(self, type_: str, payload: Any) -> None:
        if not self._event_allowed(payload):
            return
        if type_ == "llm.chunk":
            self.chunk_received.emit(dict(payload or {}))
        elif type_.startswith("job."):
            self.jobs_changed.emit()
        elif type_.startswith("schedule."):
            self.schedules_changed.emit()
        elif type_.startswith("subagent."):
            self.subagents_changed.emit()
        elif type_ in {"session.created", "session.renamed", "session.finished"}:
            self.sessions_changed.emit()
        self.event_received.emit(type_, payload)

    async def close(self) -> None:
        bus = getattr(self.ctx, "bus", None) if self.ctx is not None else None
        if bus is not None:
            try:
                bus.unsubscribe_all("desktop-native")
            except Exception:                          # noqa: BLE001
                pass
        self._subs.clear()
        if self._registry is not None:
            await self._registry.close_all()
        else:
            shutdown = getattr(self.service, "shutdown", None)
            if callable(shutdown):
                await shutdown()

    def switch_tenant(self, tenant_id: str) -> dict:
        tenant = normalize_tenant_id(tenant_id)
        self.tenant_id = tenant
        self.service = (self._provided_service if self._provided_service is not None
                        else self._registry.get(tenant))
        self.sessions_changed.emit()
        return {"ok": True, "tenant_id": tenant}

    def tenant_state(self) -> dict:
        return self.service.tenant_state()

    def save_model_profile(self, body: dict) -> dict:
        return self.service.save_model_profile(body or {})

    def activate_model_profile(self, profile_id: str) -> dict:
        return self.service.activate_model_profile(profile_id)

    def delete_model_profile(self, profile_id: str) -> dict:
        return self.service.delete_model_profile(profile_id)

    # ------------------------------------------------------------ capabilities
    def capabilities(self) -> dict:
        return self.service.capabilities()

    # ------------------------------------------------------------ sessions/chat
    async def list_sessions(self) -> dict:
        return await self.service.list_sessions()

    async def create_session(self) -> str:
        sid = await self.service.create_session()
        self.selected_sid = str(sid)
        self.sessions_changed.emit()
        return str(sid)

    async def messages(self, sid: str) -> dict:
        return await self.service.session_messages(sid)

    async def send_message(self, sid: str, text: str,
                           attachments: Any = None) -> dict:
        return await self.service.create_message(
            sid, text, attachments=attachments)

    async def edit_last_user(self, sid: str, text: str, *,
                             resend: bool = True) -> dict:
        return await self.service.edit_last_user(sid, text, resend=resend)

    async def resend_last_user(self, sid: str) -> dict:
        return await self.service.resend_last_user(sid)

    async def feedback_last_agent(self, sid: str, kind: str,
                                  note: str = "") -> dict:
        return await self.service.feedback_last_agent(sid, kind, note)

    async def upload_attachment(self, sid: str,
                                attachment: dict) -> dict:
        return await self.service.upload_attachment(sid, attachment)

    async def timeline(self, sid: str, after_seq: int = 0) -> dict:
        return await self.service.session_timeline(sid, after_seq)

    async def budget(self, sid: str) -> dict:
        return await self.service.budget_dashboard(sid)

    async def telemetry(self, sid: str) -> dict:
        return await self.service.telemetry_report(sid)

    # ------------------------------------------------------------ interactions
    async def pending_approvals(self) -> dict:
        return await self.service.pending_approvals()

    async def decide_approval(self, aid: int, decision: str, *,
                              sid: str = "") -> dict:
        return await self.service.decide_approval(
            int(aid), decision, sid=sid or None)

    async def pending_asks(self) -> dict:
        return await self.service.pending_asks()

    async def answer_ask(self, ask_id: int, *, choice: Any = None,
                         text: Any = None, sid: str = "") -> dict:
        return await self.service.answer_ask(
            int(ask_id), choice=choice, text=text, sid=sid or None)

    # ------------------------------------------------------------ orchestration
    async def jobs(self, sid: str) -> dict:
        return await self.service.list_jobs(sid)

    async def start_job(self, sid: str, intent: str) -> str:
        job_id = await self.service.start_job(sid, intent)
        self.jobs_changed.emit()
        return job_id

    async def cancel_job(self, sid: str, job_id: str) -> bool:
        result = await self.service.cancel_job(sid, job_id)
        self.jobs_changed.emit()
        return result

    async def schedules(self, sid: str) -> dict:
        return await self.service.list_schedules(sid)

    async def schedule_action(self, sid: str, **kwargs: Any) -> dict:
        result = await self.service.schedule_action(sid, **kwargs)
        self.schedules_changed.emit()
        return result

    async def subagents(self, sid: str) -> dict:
        return await self.service.list_subagents(sid)

    async def spawn_subagent(self, sid: str, task: str, **kwargs: Any) -> str:
        sub_id = await self.service.spawn_subagent(sid, task, **kwargs)
        self.subagents_changed.emit()
        return sub_id

    async def cancel_subagent(self, sid: str, sub_id: str) -> bool:
        result = await self.service.cancel_subagent(sid, sub_id)
        self.subagents_changed.emit()
        return result

    # ------------------------------------------------------------ extensions
    async def skills(self) -> dict:
        return await self.service.list_skills()

    async def skill_detail(self, name: str) -> dict:
        return await self.service.skill_detail(name)

    async def search_skills(self, query: str, registry_url: str = "") -> dict:
        return await self.service.search_skills(query, registry_url)

    async def install_skill(self, sid: str, name: str, *, version: str = "",
                            registry_url: str = "",
                            approved_by: str = "user") -> dict:
        return await self.service.install_skill(
            sid, name, version=version, registry_url=registry_url,
            approved_by=approved_by)

    async def remove_skill(self, sid: str, name: str,
                           approved_by: str = "user") -> dict:
        return await self.service.remove_skill(sid, name,
                                               approved_by=approved_by)

    async def rollback_skill(self, sid: str, name: str, version: str,
                             approved_by: str = "user") -> dict:
        return await self.service.rollback_skill(
            sid, name, version, approved_by=approved_by)

    def skill_versions(self, name: str) -> list[str]:
        return self.service.skill_versions(name)

    async def plugins(self) -> dict:
        return await self.service.list_plugins()

    async def plugin_action(self, action: str, body: dict) -> dict:
        return await self.service.plugin_action(action, body)

    async def set_preset(self, preset: str) -> dict:
        return await self.service.set_preset({"preset": preset})

    async def run_workflow(self, sid: str, steps: list[str], *,
                           name: str = "native",
                           stop_on_fail: bool = False) -> dict:
        return await self.service.run_workflow(
            sid, steps, name=name, stop_on_fail=stop_on_fail)
