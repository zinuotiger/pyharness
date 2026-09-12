"""Application service layer shared by desktop shells and future native UI."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from pyharness.application.models import SHELL_CAPABILITIES
from pyharness.core.approval import ApprovalProvider
from pyharness.core.skill import SkillManager
from pyharness.core.task_queue import TaskQueue
from pyharness.core.tenant_settings import (
    TenantSettingsStore,
    normalize_tenant_id,
    register_session_tenant,
    register_tenant_store,
    unregister_session_tenant,
)
from pyharness.errors import raise_code
from pyharness.events.vocab import validate_payload

log = logging.getLogger("pyharness.application.service")


def _session_module():
    """Lazy import avoids desktop package <-> app shell import cycles."""
    from pyharness.desktop import sessions
    return sessions


def _as_dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(vars(value))


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class ApplicationService:
    """Session-scoped business facade independent from HTTP and UI frameworks."""

    def __init__(self, ctx: Any, *, channel: str = "desktop",
                 tenant_id: str = "default") -> None:
        self.ctx = ctx
        self.channel = str(channel or "desktop")
        self.tenant_id = normalize_tenant_id(tenant_id)
        self._surface: Any = None
        self._logs: dict[str, Any] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._queue_locks: dict[str, asyncio.Lock] = {}
        self._queues: dict[str, TaskQueue] = {}
        self._approvals: dict[str, ApprovalProvider] = {}
        self._engines: dict[str, Any] = {}
        self._settings_store = TenantSettingsStore(
            self._storage_root() / "tenants")
        register_tenant_store(self.tenant_id, self._settings_store)

    def _storage_root(self) -> Path:
        cfg = _session_module()._cfg_of(self.ctx)
        root = getattr(getattr(cfg, "storage", None), "root", None)
        return Path(str(root or "~/.pyharness")).expanduser()

    def tenant_root(self) -> Path:
        return self._storage_root() / "tenants" / self.tenant_id

    def effective_settings(self) -> Any:
        """Settings overridden by this tenant's active model profile."""
        cfg = _session_module()._cfg_of(self.ctx)
        profile = self._settings_store.active_profile(self.tenant_id)
        if profile is None or not hasattr(cfg, "model_dump"):
            return cfg
        raw = cfg.model_dump()
        llm = dict(raw.get("llm") or {})
        llm.update({
            "model": profile["model"],
            "base_url": profile["base_url"],
            "api_key": f"tenant:{self.tenant_id}:{profile['id']}",
            "temperature": profile.get("temperature", llm.get("temperature", 0.7)),
            "max_tokens": profile.get("max_tokens", llm.get("max_tokens", 4096)),
            "fallback_models": list(profile.get("fallback_models") or []),
        })
        raw["llm"] = llm
        from pyharness.config import Settings
        return Settings.model_validate(raw)

    def tenant_state(self) -> dict:
        return self._settings_store.state(self.tenant_id)

    def save_model_profile(self, body: dict) -> dict:
        result = self._settings_store.upsert_profile(self.tenant_id, body)
        result["restart_required"] = bool(self._engines)
        return result

    def activate_model_profile(self, profile_id: str) -> dict:
        result = self._settings_store.activate_profile(self.tenant_id, profile_id)
        result["restart_required"] = bool(self._engines)
        return result

    def delete_model_profile(self, profile_id: str) -> dict:
        result = self._settings_store.delete_profile(self.tenant_id, profile_id)
        result["restart_required"] = bool(self._engines)
        return result

    def _surface_ctx(self) -> Any:
        if self.tenant_id == "default":
            return self.ctx
        try:
            ctx = copy.copy(self.ctx)
        except Exception:                              # noqa: BLE001
            ctx = SimpleNamespace(**dict(vars(self.ctx)))
        try:
            ctx.session = None
        except Exception:                              # noqa: BLE001
            pass
        return ctx

    @staticmethod
    def capabilities() -> dict:
        """Return the shared UI capability contract.

        Both shells expose this through their own adapter so feature drift is
        visible at runtime, not only in tests.
        """
        rows = list(SHELL_CAPABILITIES)
        return {"capabilities": rows, "count": len(rows)}

    def surface_mgr(self) -> Any:
        if self._surface is None:
            ctx = self._surface_ctx()
            sm = _session_module()
            sessions_dir = (sm._sessions_dir_of(ctx)
                            if self.tenant_id == "default"
                            else self.tenant_root() / "sessions")
            self._surface = sm._surface_of(
                ctx, bus=getattr(ctx, "bus", None),
                config=self.effective_settings(),
                sessions_dir=sessions_dir)
            if isinstance(self._surface, sm.DesktopSessionManager):
                self._surface._ctx_repair = getattr(ctx, "repair", None)
        return self._surface

    async def require_session(self, sid: str) -> Any:
        sm = _session_module()
        sid = sm.validate_session_id(sid)
        lock = self._session_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            if sid in self._logs:
                register_session_tenant(sid, self.tenant_id)
                return self._logs[sid]
            log_ = await sm._await(self.surface_mgr().open_session(sid))
            self._logs[sid] = log_
            register_session_tenant(sid, self.tenant_id)
            return log_

    async def queue_for(self, sid: str, log_: Any = None) -> TaskQueue:
        if log_ is None:
            log_ = await self.require_session(sid)
        lock = self._queue_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            q = self._queues.get(sid)
            if q is None:
                runner = await self._engine_runner_for(sid, log_)
                q = TaskQueue(session=log_, runner=runner)
                self._queues[sid] = q
                spine = self._engines.get(sid)
                if spine is not None:
                    from pyharness import engine as _eng
                    await _eng.activate_orchestration(spine, task_queue=q)
            return q

    async def _engine_runner_for(self, sid: str, log_: Any) -> Any:
        spine = self._engines.get(sid)
        from pyharness import engine as _eng
        from pyharness.engine import make_runner as _eng_make_runner
        cfg = self.effective_settings()
        _eng.register_default_llm(cfg)
        if spine is None:
            store = getattr(self.surface_mgr(), "_stores", {}).get(sid)
            sessions_dir = (_session_module()._sessions_dir_of(self.ctx)
                            if self.tenant_id == "default"
                            else self.tenant_root() / "sessions")
            spine = _eng.build_runner_components(
                cfg, log_=log_, bus=getattr(self.ctx, "bus", None),
                sessions_dir=sessions_dir, store=store,
                attach_persistence=False)
            self._engines[sid] = spine
        if not getattr(spine, "_plugins_ready", False):
            await _eng._preload_plugins(spine)
            spine._plugins_ready = True
        if self.tenant_id == "default":
            if getattr(self.ctx, "approval", None) is None:
                self.ctx.approval = spine.approval
            if getattr(self.ctx, "guard", None) is None:
                self.ctx.guard = spine.guard
        return _eng_make_runner(spine)

    def runner_seam(self) -> Any:
        ctx = self.ctx
        make = getattr(ctx, "make_runner", None)
        if callable(make):
            return make()
        return getattr(ctx, "task_runner", None)

    def owner_for(self, sid: str) -> str:
        if self.tenant_id == "default":
            return f"{self.channel}:{sid}"
        return f"{self.channel}:{self.tenant_id}:{sid}"

    async def public_spine_for(self, sid: str) -> tuple[Any, Any]:
        log_ = await self.require_session(sid)
        spine = self._engines.get(sid)
        if spine is None:
            from pyharness import engine as _eng
            store = getattr(self.surface_mgr(), "_stores", {}).get(sid)
            sessions_dir = (_session_module()._sessions_dir_of(self.ctx)
                            if self.tenant_id == "default"
                            else self.tenant_root() / "sessions")
            spine = _eng.build_runner_components(
                self.effective_settings(), log_=log_,
                bus=getattr(self.ctx, "bus", None),
                sessions_dir=sessions_dir, store=store,
                attach_persistence=False)
            self._engines[sid] = spine
            await _eng.activate_orchestration(spine)
        return log_, spine

    def approval_for(self, sid: str, log_: Any) -> ApprovalProvider:
        provider = self._approvals.get(sid)
        if provider is None:
            ctx_approval = (getattr(self.ctx, "approval", None)
                            if self.tenant_id == "default" else None)
            if ctx_approval is not None and not self._approvals:
                return ctx_approval
            provider = ApprovalProvider(session=log_, bus=None,
                                        config=_session_module()._cfg_of(self.ctx))
            self._approvals[sid] = provider
        return provider

    def all_approval_providers(self) -> list:
        seen, out = set(), []
        ctx_approval = (getattr(self.ctx, "approval", None)
                        if self.tenant_id == "default" else None)
        for p in list(self._approvals.values()) + ([ctx_approval] if ctx_approval else []):
            if p is not None and id(p) not in seen:
                seen.add(id(p))
                out.append(p)
        return out

    async def create_session(self) -> str:
        mgr = self.surface_mgr()
        fn = getattr(mgr, "create", None)
        if not callable(fn):
            raise_code("CYC-999", hint="会话门面未实现 create(缺 DesktopSessionManager)")
        sid = fn()
        if hasattr(sid, "__await__"):
            sid = await sid
        self._logs[sid] = await self.require_session(sid)
        register_session_tenant(sid, self.tenant_id)
        return str(sid)

    async def delete_session(self, sid: str) -> dict:
        """Delete an idle session and all of its tenant-scoped files."""
        sid = _session_module().validate_session_id(sid)
        queue = self._queues.get(sid)
        if queue is not None:
            status = queue.status()
            if status.running or status.waiting or status.paused:
                raise_code("BUSY", session_id=sid,
                           advice="会话仍有运行/等待任务,完成或取消后再删除")
        spine = self._engines.get(sid)
        if spine is not None:
            await spine.close()
            self._engines.pop(sid, None)
        self._approvals.pop(sid, None)
        self._queues.pop(sid, None)
        self._queue_locks.pop(sid, None)
        self._logs.pop(sid, None)
        result = await self.surface_mgr().delete_session(sid)
        self._session_locks.pop(sid, None)
        unregister_session_tenant(sid)
        return {"ok": True, **result}

    @staticmethod
    def attachment_payloads(attachments: Any) -> list[dict]:
        payloads: list[dict] = []
        for ref in (attachments or []):
            if not isinstance(ref, dict):
                raise_code("EVT-100", field="attachments", advice="附件须 dict 引用")
            payload = {"file_path": str(ref.get("file_path") or ref.get("ref") or ""),
                       "mime": ref.get("mime") or "image/png",
                       "sha256": ref.get("sha256") or "0" * 64,
                       "w": ref.get("w") or 1, "h": ref.get("h") or 1,
                       "size_bytes": ref.get("size_bytes")}
            payloads.append(validate_payload("user.attachment.image", payload))
        return payloads

    async def create_message(self, sid: str, text: str, *,
                             attachments: Any = None) -> dict:
        text = str(text or "").strip()
        if not text:
            raise_code("EVT-100", advice="消息不能为空")
        log_ = await self.require_session(sid)
        payloads = self.attachment_payloads(attachments)
        queue = await self.queue_for(sid, log_)
        env = await log_.append("user.message", {"content": text},
                                actor="user", origin=self.channel, sync=True)
        for payload in payloads:
            await log_.append("user.attachment.image", payload, actor="user")
        task_id = await queue.submit(text, meta={"channel": self.channel,
                                                 "session_id": sid})
        return {"task_id": task_id, "user_seq": env.seq}

    async def list_jobs(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        owner = self.owner_for(sid)
        rows = [self._job_dict(await spine.jobs.status(job_id, by=owner))
                for job_id in spine.jobs.list_owned(owner)]
        return {"jobs": rows, "count": len(rows)}

    @staticmethod
    def _job_dict(status: Any) -> dict:
        return _json_safe(_as_dict(status))

    async def start_job(self, sid: str, intent: str) -> str:
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

    async def job_status(self, sid: str, job_id: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        return {"job": self._job_dict(
            await spine.jobs.status(job_id, by=self.owner_for(sid)))}

    async def cancel_job(self, sid: str, job_id: str) -> bool:
        _, spine = await self.public_spine_for(sid)
        return bool(await spine.jobs.cancel(job_id, by=self.owner_for(sid)))

    async def list_schedules(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        rows = [_json_safe(_as_dict(j)) for j in await spine.schedule.list_jobs()]
        return {"schedules": rows, "count": len(rows)}

    async def schedule_action(self, sid: str, action: str, *,
                              name: str = "", kind: str = "cron",
                              expr: str = "", intent: str = "",
                              is_risky: Optional[bool] = None) -> dict:
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
        elif action in {"pause", "resume", "remove"}:
            if not name:
                raise_code("EVT-100", field="name", advice=f"schedule {action} 需要 name")
            await getattr(spine.schedule, action)(name, ctx=ctx)
        else:
            raise_code("EVT-100", field="action",
                       advice="action 须为 add/pause/resume/remove")
        return {"ok": True, "action": action, "name": name}

    async def list_subagents(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        return {"status": _json_safe(_as_dict(spine.subagent.status()))}

    async def spawn_subagent(self, sid: str, task: str, *,
                             tools_subset: Any = None,
                             deny_extra: Any = None,
                             budget_ratio: float = 0.25,
                             notify: bool = True) -> str:
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

    async def cancel_subagent(self, sid: str, sub_id: str) -> bool:
        _, spine = await self.public_spine_for(sid)
        return bool(await spine.subagent.cancel(sub_id, by=self.owner_for(sid)))

    async def shutdown(self) -> None:
        surface = self._surface
        if isinstance(surface, _session_module().DesktopSessionManager):
            try:
                await surface.shutdown_all()
            except Exception:                          # noqa: BLE001
                pass
        seen: set[int] = set()
        spines = list(self._engines.values())
        if self.tenant_id == "default":
            spines.append(getattr(self.ctx, "engine_spine", None))
        for spine in spines:
            if spine is None or id(spine) in seen:
                continue
            seen.add(id(spine))
            try:
                await spine.close()
            except Exception:                          # noqa: BLE001
                pass

    # ------------------------------------------------------------ read models
    async def list_sessions(self) -> dict:
        lst = self.surface_mgr().list()
        if callable(lst) or hasattr(lst, "__await__"):
            lst = await _session_module()._await(lst)

        def _title_of(sid: str) -> str:
            log_ = self._logs.get(str(sid))
            if log_ is None:
                return ""
            title = ""
            for ev in log_.events_after(0):
                if ev.type == "session.created":
                    title = ev.payload.get("title") or title
                elif ev.type == "session.renamed":
                    title = ev.payload.get("new_title") or title
            return str(title)

        sessions: list[dict] = []
        for item in (lst or []):
            row = item if isinstance(item, dict) else {"sid": str(item)}
            sid = str(row.get("sid") or item)
            register_session_tenant(sid, self.tenant_id)
            if not row.get("title"):
                row["title"] = _title_of(sid)
            sessions.append(row)
        return {"sessions": sessions, "count": len(sessions)}

    async def session_messages(self, sid: str, after_seq: int = 0) -> dict:
        log_ = await self.require_session(sid)
        msgs = log_.derive_messages()
        stats_fn = getattr(log_, "stats", None)
        to_seq = int((stats_fn().get("seq") or 0)) if callable(stats_fn) else 0
        return {"sid": sid, "after_seq": int(after_seq), "to_seq": to_seq,
                "messages": msgs}

    async def session_timeline(self, sid: str, after_seq: int = 0,
                               kinds: str = "") -> dict:
        log_ = await self.require_session(sid)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int):
            raise_code("EVT-100", field="after_seq", value=after_seq,
                       advice="after_seq 须 int(seq 游标)")
        from pyharness.desktop.projection import derive_timeline
        nodes = derive_timeline(
            log_.events_after(after_seq=max(0, int(after_seq))),
            kinds=str(kinds or ""))
        return {"sid": sid, "base_seq": max(0, int(after_seq)),
                "nodes": [asdict(n) for n in nodes]}

    async def event_detail(self, sid: str, seq: int) -> dict:
        log_ = await self.require_session(sid)
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise_code("EVT-100", field="seq", value=seq, advice="seq 须 int")
        env = log_.get(int(seq))
        if env is None:
            raise_code("EVT-100", sid=sid, seq=seq,
                       advice="seq 不存在(空洞/坏行隔离/越界)")
        from pyharness.desktop.projection import _env_dict
        return {"event": _env_dict(env)}

    async def telemetry_report(self, sid: str) -> dict:
        log_ = await self.require_session(sid)
        from pyharness.core import telemetry as _tel
        try:
            return {"sid": str(sid), "ok": True,
                    "audit": _tel.session_audit(log_)}
        except Exception as e:                         # noqa: BLE001
            return {"sid": str(sid), "ok": False,
                    "error": f"{type(e).__name__}:{e}"[:200]}

    # ------------------------------------------------------------ edit / feedback
    @staticmethod
    def _last_env_of(log_: Any, type_: str):
        for ev in reversed(list(log_.events_after(0))):
            if ev.type == type_:
                return ev
        return None

    async def resend_text(self, log_: Any, sid: str, text: str) -> dict:
        text = str(text or "").strip()
        if not text:
            raise_code("EVT-100", advice="重发内容为空")
        env = await log_.append("user.message", {"content": text},
                                actor="user", origin=self.channel, sync=True)
        queue = await self.queue_for(sid, log_)
        task_id = await queue.submit(text, meta={"channel": self.channel,
                                                 "session_id": sid})
        return {"task_id": task_id, "user_seq": env.seq}

    async def edit_message(self, sid: str, seq: int, text: str,
                           *, resend: bool = False) -> dict:
        text = str(text or "").strip()
        log_ = await self.require_session(sid)
        from pyharness.core.message_edit import edit_user_message
        env = await edit_user_message(log_, int(seq), text)
        out: dict = {"ok": True, "edited_seq": env.seq, "target_seq": int(seq)}
        if resend:
            out["resend"] = await self.resend_text(log_, sid, text)
        return out

    async def resend_message(self, sid: str, seq: int) -> dict:
        log_ = await self.require_session(sid)
        env = log_.get(int(seq))
        if env is None or env.type != "user.message":
            raise_code("EVT-101", seq=int(seq), hint="目标不是 user.message,无法重发")
        return await self.resend_text(log_, sid, env.payload.get("content", ""))

    async def feedback(self, sid: str, seq: int, kind: str,
                       note: str = "") -> dict:
        kind = str(kind or "")
        if kind not in ("up", "down", "flag"):
            raise_code("EVT-100", field="kind", kind=kind,
                       advice="kind 取值 up/down/flag")
        log_ = await self.require_session(sid)
        env = log_.get(int(seq))
        if env is None or env.type != "agent.message":
            raise_code("EVT-101", seq=int(seq), hint="反馈目标须是 agent.message")
        await log_.append("user.feedback",
                          {"target_seq": int(seq), "kind": kind,
                           "note": str(note or "")[:500] or None},
                          actor="user", sync=True)
        return {"ok": True, "target_seq": int(seq), "kind": kind}

    async def edit_last_user(self, sid: str, text: str,
                             *, resend: bool = False) -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "user.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有可编辑的用户消息")
        return await self.edit_message(sid, env.seq, text, resend=resend)

    async def resend_last_user(self, sid: str) -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "user.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有可重发的用户消息")
        return await self.resend_message(sid, env.seq)

    async def feedback_last_agent(self, sid: str, kind: str,
                                  note: str = "") -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "agent.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有 agent 回复可反馈")
        return await self.feedback(sid, env.seq, kind, note)

    # ------------------------------------------------------------ approvals / asks
    async def pending_approvals(self) -> dict:
        out: list[dict] = []
        seen: set[int] = set()
        for p in self.all_approval_providers():
            pending = getattr(p, "_pending", None)
            if not isinstance(pending, dict):
                continue
            for aid, req in pending.items():
                if aid in seen:
                    continue
                seen.add(aid)
                out.append({"approval_id": aid,
                            "tool": getattr(req, "tool", ""),
                            "args_summary": getattr(req, "args_summary", ""),
                            "risk": getattr(req, "danger", "high"),
                            "ttl_ms": getattr(req, "ttl_ms", None),
                            "session_id": getattr(req, "session_id", "")})
        out.sort(key=lambda x: (str(x.get("session_id") or ""), x["approval_id"]))
        return {"pending": out, "count": len(out)}

    @staticmethod
    def _approval_sid_of(provider: Any, aid: int) -> str:
        pending = getattr(provider, "_pending", None)
        req = pending.get(int(aid)) if isinstance(pending, dict) else None
        return str(getattr(req, "session_id", "") or getattr(
            getattr(req, "log", None), "sid", "") or "")

    def provider_owning(self, aid: int, *, sid: Optional[str] = None) -> Any:
        matches = []
        for p in self.all_approval_providers():
            pending = getattr(p, "_pending", None)
            if isinstance(pending, dict) and int(aid) in pending:
                psid = self._approval_sid_of(p, aid)
                if sid is None or psid == sid:
                    matches.append(p)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise_code("EVT-101", approval_id=aid,
                       hint="多个会话存在同 approval_id;请使用 /api/approvals/{sid}/{aid}")
        if sid is not None:
            raise_code("APR-503", approval_id=aid, session_id=sid,
                       hint="该会话下不存在此 approval_id")
        ctx_approval = getattr(self.ctx, "approval", None)
        if ctx_approval is not None:
            return ctx_approval
        raise_code("APR-503", approval_id=aid,
                   hint="未知/已裁决的 approval_id;同一审批至多一个结果(防重放)")

    async def decide_approval(self, aid: int, decision: str, *,
                              sid: Optional[str] = None) -> dict:
        if sid is not None:
            sid = _session_module().validate_session_id(sid)
        if isinstance(aid, bool) or not isinstance(aid, int):
            raise_code("EVT-100", field="aid", aid=aid, advice="aid 须 int")
        if decision not in {"approve", "deny"}:
            raise_code("EVT-100", field="decision", decision=decision,
                       advice="decision 取值 approve/deny")
        provider = self.provider_owning(aid, sid=sid)
        fn = (getattr(provider, f"{decision}_async", None)
              or getattr(provider, decision, None))
        if not callable(fn):
            raise_code("CYC-999", hint="审批裁决入口未装配")
        res = fn(int(aid), by=self.channel)
        if inspect.isawaitable(res):
            await res
        return {"ok": True, "approval_id": int(aid)}

    def ask_providers(self) -> list:
        seen, out = set(), []
        for spine in list(self._engines.values()):
            p = getattr(spine, "ask", None)
            if p is not None and id(p) not in seen:
                seen.add(id(p))
                out.append(p)
        spine = getattr(self.ctx, "engine_spine", None)
        p = getattr(spine, "ask", None)
        if p is not None and id(p) not in seen:
            out.append(p)
        return out

    def sid_of_ask(self, provider: Any) -> str:
        for sid, spine in list(self._engines.items()):
            if getattr(spine, "ask", None) is provider:
                return str(sid)
        return ""

    async def pending_asks(self) -> dict:
        out: list[dict] = []
        for p in self.ask_providers():
            for item in (p.pending_list() if hasattr(p, "pending_list") else []):
                item = dict(item)
                item["session_id"] = self.sid_of_ask(p) or getattr(
                    getattr(p, "_session", None), "sid", "")
                out.append(item)
        out.sort(key=lambda x: (str(x.get("session_id") or ""), x["ask_id"]))
        return {"pending": out, "count": len(out)}

    async def answer_ask(self, ask_id: int, *, choice: Any = None,
                         text: Any = None, sid: Optional[str] = None) -> dict:
        if sid is not None:
            sid = _session_module().validate_session_id(sid)
        matches = []
        for p in self.ask_providers():
            pending = getattr(p, "_pending", None)
            if isinstance(pending, dict) and int(ask_id) in pending:
                psid = self.sid_of_ask(p) or getattr(
                    getattr(p, "_session", None), "sid", "")
                if sid is None or str(psid) == sid:
                    matches.append(p)
        if len(matches) > 1:
            raise_code("EVT-101", ask_id=ask_id,
                       hint="多个会话存在同 ask_id;请指定会话")
        if len(matches) == 1:
            provider = matches[0]
            fn = getattr(provider, "answer_async", None) or provider.answer
            res = fn(int(ask_id), choice=choice, text=text, by=self.channel)
            ok = await res if inspect.isawaitable(res) else res
            if ok:
                return {"ok": True, "ask_id": int(ask_id), "session_id": sid}
        raise_code("APR-503", approval_id=int(ask_id),
                   hint="未知/已答复的 ask_id")

    # ------------------------------------------------------------ attachments / workflow / budget
    async def upload_attachment(self, sid: str, attachment: dict) -> dict:
        log_ = await self.require_session(sid)
        for payload in self.attachment_payloads([attachment]):
            await log_.append("user.attachment.image", payload, actor="user")
        return {"ok": True, "sid": str(sid)}

    async def run_workflow(self, sid: str, steps: list, *,
                           name: str = "desktop",
                           stop_on_fail: bool = False) -> dict:
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

    async def budget_dashboard(self, sid: str) -> dict:
        log_ = await self.require_session(sid)
        usages = [e for e in log_.events_after() if e.type == "llm.usage"]
        used_in = sum(int(u.payload.get("in_tokens", 0)) for u in usages)
        used_out = sum(int(u.payload.get("out_tokens", 0)) for u in usages)
        base = {"sid": sid, "used_in_tokens": used_in,
                "used_out_tokens": used_out,
                "recent": [dict(u.payload) for u in usages[-20:]]}
        budget = None
        try:                                       # 优先本会话自己装配的预算门面
            spine, _log2 = await self.public_spine_for(sid)
            budget = getattr(spine, "budget", None)
        except Exception:                          # noqa: BLE001 读面降级到 ctx
            budget = None
        if budget is None:
            budget = getattr(self.ctx, "budget", None)
        snap = getattr(budget, "snapshot", None)
        if not callable(snap):
            return {**base, "disabled": True,
                    "reason": "budget-gate-unassembled", "limit_cny": None,
                    "used_cny": None, "warned": False, "ratio": 0.0}
        st = snap(sid)
        if inspect.isawaitable(st):
            st = await st
        limit = float(getattr(st, "limit_cny", 0) or 0)
        used = float(getattr(st, "used_cny", 0) or 0)
        ratio = (used / limit) if limit else 0.0
        cfg = _session_module()._cfg_of(self.ctx)
        warn = float(getattr(getattr(cfg, "budget", None), "warn_ratio", 0.8))
        return {**base, "disabled": False, "limit_cny": limit, "used_cny": used,
                "warned": ratio >= warn, "ratio": round(ratio, 4)}

    # ------------------------------------------------------------ plugins / preset
    def plugin_roots(self) -> list[Path]:
        roots = ([Path(__file__).resolve().parents[2] / "examples" / "plugins"]
                 if self.tenant_id == "default" else [self.tenant_root() / "plugins"])
        if self.tenant_id == "default":
            cfg = _session_module()._cfg_of(self.ctx)
            try:
                extra = getattr(getattr(cfg, "plugins", None), "dir", None)
                if extra:
                    roots.append(Path(str(extra)).expanduser())
            except Exception:                          # noqa: BLE001
                pass
        return roots

    @staticmethod
    def _plug_ctx_for(spine: Any) -> Any:
        return SimpleNamespace(session=getattr(spine, "session", None),
                               bus=getattr(spine, "bus", None))

    async def list_plugins(self) -> dict:
        out: list[dict] = []
        for sid, spine in self._engines.items():
            mgr = getattr(spine, "plugins", None)
            if mgr is None:
                continue
            for pid, rec in dict(getattr(spine, "plugin_state", {})).items():
                spec = rec.get("spec")
                out.append({
                    "id": pid,
                    "state": mgr.state(pid),
                    "version": getattr(spec, "manifest", {}).get("version", "?"),
                    "tools": list(rec.get("tool_names", ())),
                    "session": str(sid),
                })
        return {"plugins": out, "count": len(out)}

    async def plugin_action(self, kind: str, body: dict) -> dict:
        pid = str((body or {}).get("id") or "")
        if not pid:
            return {"ok": False, "error": "缺插件 id"}
        if not self._engines:
            return {"ok": False, "error": "无活动会话(先新建/打开会话)"}
        from pyharness.core import plugin_loader as _pl
        from pyharness.desktop.sessions import _plugin_within
        pdir = ((Path(__file__).resolve().parents[2] / "examples" / "plugins")
                if self.tenant_id == "default" else (self.tenant_root() / "plugins")) / pid
        results: list[dict] = []
        for sid, spine in list(self._engines.items()):
            mgr = spine.plugins
            try:
                if kind == "load":
                    d = Path(str(body.get("dir") or pdir)).expanduser().resolve()
                    if not d.exists():
                        return {"ok": False, "error": f"插件目录不存在:{d}"}
                    if not any(_plugin_within(d, r) for r in self.plugin_roots()):
                        return {"ok": False,
                                "error": "插件目录必须在配置的插件根目录内"}
                    spec = _pl.load_spec(d)
                    await _pl.load_plugin(mgr, spec, self._plug_ctx_for(spine),
                                          spine.tool_registry,
                                          state=spine.plugin_state)
                elif kind == "unload":
                    rec = spine.plugin_state.get(pid)
                    if rec is None:
                        results.append({"session": str(sid), "ok": False,
                                        "error": "未安装"})
                        continue
                    await _pl.unload_plugin(mgr, rec["spec"],
                                            self._plug_ctx_for(spine),
                                            spine.tool_registry,
                                            state=spine.plugin_state)
                elif kind == "reload":
                    rec = spine.plugin_state.get(pid)
                    if rec is None:
                        results.append({"session": str(sid), "ok": False,
                                        "error": "未安装"})
                        continue
                    await _pl.reload_plugin(
                        mgr, rec["spec"], self._plug_ctx_for(spine),
                        spine.tool_registry, state=spine.plugin_state,
                        pkg_dir=Path(rec.get("path", "")).parent or pdir)
                else:
                    return {"ok": False, "error": f"未知操作:{kind}"}
                results.append({"session": str(sid), "ok": True,
                                "state": mgr.state(pid)})
            except Exception as e:                     # noqa: BLE001
                results.append({"session": str(sid), "ok": False,
                                "error": f"{type(e).__name__}:{e}"[:200]})
        ok_all = all(r.get("ok") for r in results) and bool(results)
        return {"ok": ok_all, "results": results,
                "hint": "新会话(重启程序)自动预载 examples/plugins/ 全部插件"}

    @staticmethod
    def _reset_preset_state(spine: Any) -> None:
        from pyharness import engine as _eng
        cfg = spine.settings
        cfg.security.policy.preset = "strict"
        p = spine.scope.policy
        p.deny_tools = set()
        try:
            p.sandbox_level = "strict"
        except Exception:                              # noqa: BLE001
            pass
        try:
            cfg.security.sandbox.level = "strict"
        except Exception:                              # noqa: BLE001
            pass
        _eng._apply_preset(cfg, p, spine.tool_registry)

    async def set_preset(self, body: dict) -> dict:
        preset = str((body or {}).get("preset") or "")
        valid = ("strict", "standard", "readonly", "locked")
        if preset not in valid:
            return {"ok": False, "error": f"非法档位:{preset}", "valid": list(valid)}
        if not self._engines:
            return {"ok": False, "error": "无活动会话(先新建/打开会话)"}
        from pyharness import engine as _eng
        done = 0
        for spine in list(self._engines.values()):
            try:
                spine.settings.security.policy.preset = preset
                p = spine.scope.policy
                p.deny_tools = set()
                try:
                    p.sandbox_level = "strict"
                except Exception:                      # noqa: BLE001
                    pass
                try:
                    spine.settings.security.sandbox.level = "strict"
                except Exception:                      # noqa: BLE001
                    pass
                _eng._apply_preset(spine.settings, p, spine.tool_registry)
                done += 1
            except Exception as e:                     # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}:{e}"[:200]}
        return {"ok": True, "preset": preset, "sessions": done,
                "hint": "exec.* 等危险工具:standard 档可见,strict 档隐藏"}

    def skill_installer(self) -> Any:
        from pyharness.core.skill_registry import SkillInstaller
        cfg = _session_module()._cfg_of(self.ctx)
        sk = getattr(cfg, "skills", None)
        root = (Path(str(getattr(sk, "dir", "~/.pyharness/skills"))).expanduser()
                if self.tenant_id == "default"
                else self.tenant_root() / "skills")
        return SkillInstaller(
            root,
            max_package_bytes=int(getattr(sk, "max_package_bytes", 5 * 1024 * 1024)),
            max_files=int(getattr(sk, "max_files", 200)))

    async def search_skills(self, query: str,
                            registry_url: str = "") -> dict:
        cfg = _session_module()._cfg_of(self.ctx)
        url = str(registry_url or getattr(getattr(cfg, "skills", None),
                                          "registry_url", "") or "").strip()
        if not url:
            raise_code("CFG-601", field="skills.registry_url",
                       advice="未配置 Skill Registry URL")
        rows = await self.skill_installer().search(query, url)
        return {"registry_url": url, "skills": rows, "count": len(rows)}

    async def install_skill(self, sid: str, name: str, *, version: str = "",
                            registry_url: str = "",
                            approved_by: str = "user") -> dict:
        cfg = _session_module()._cfg_of(self.ctx)
        url = str(registry_url or getattr(getattr(cfg, "skills", None),
                                          "registry_url", "") or "").strip()
        if not url:
            raise_code("CFG-601", field="skills.registry_url",
                       advice="未配置 Skill Registry URL")
        result = await self.skill_installer().install(
            name, registry_url=url, version=version or None,
            approved_by=approved_by)
        log_ = await self.require_session(sid)
        await log_.append("skill.installed", {
            "name": result["name"], "version": result["version"],
            "sha256": result["sha256"], "source": result["source"],
            "approved_by": approved_by}, actor="user", sync=True)
        self.skills_mgr().scan()
        return result

    async def remove_skill(self, sid: str, name: str, *,
                           approved_by: str = "user") -> dict:
        result = self.skill_installer().remove(name, approved_by=approved_by)
        if result.get("removed"):
            log_ = await self.require_session(sid)
            await log_.append("skill.removed", {
                "name": name, "version": result.get("version", ""),
                "approved_by": approved_by}, actor="user", sync=True)
            self.skills_mgr().scan()
        return result

    async def rollback_skill(self, sid: str, name: str, version: str, *,
                             approved_by: str = "user") -> dict:
        result = self.skill_installer().rollback(
            name, version, approved_by=approved_by)
        log_ = await self.require_session(sid)
        await log_.append("skill.rollback", {
            "name": name, "from_version": result.get("from_version", ""),
            "to_version": version, "approved_by": approved_by},
            actor="user", sync=True)
        self.skills_mgr().scan()
        return result

    def skill_versions(self, name: str) -> list[str]:
        return self.skill_installer().versions(name)

    def skills_mgr(self) -> SkillManager:
        for spine in self._engines.values():
            mgr = getattr(spine, "skills", None)
            if mgr is not None:
                return mgr
        if self.tenant_id != "default":
            return SkillManager([self.tenant_root() / "skills"])
        roots = [Path(__file__).resolve().parents[2] / "skills"]
        cfg = _session_module()._cfg_of(self.ctx)
        try:
            extra = getattr(getattr(cfg, "skills", None), "dir", None)
            if extra:
                roots.append(Path(str(extra)).expanduser())
        except Exception:                              # noqa: BLE001
            pass
        return SkillManager(roots)

    async def list_skills(self) -> dict:
        mgr = self.skills_mgr()
        rows = mgr.list()
        return {"skills": rows, "count": len(rows), "catalog": mgr.render_catalog()}

    async def skill_detail(self, name: str) -> dict:
        data = self.skills_mgr().load(str(name))
        return {"ok": True, "name": data["name"],
                "description": data["description"], "dir": data["dir"],
                "body": data["body"]}

    async def reload_skills(self) -> dict:
        counts = []
        for spine in self._engines.values():
            mgr = getattr(spine, "skills", None)
            if mgr is not None:
                counts.append({"session": str(getattr(
                    getattr(spine, "session", None), "session_id", "?")),
                    "count": mgr.scan()})
        if not counts:
            counts.append({"direct": True, "count": self.skills_mgr().scan()})
        return {"ok": True, "results": counts,
                "hint": "新技能立即可被 agent skill.load"}
