"""InteractionService operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
import inspect
from typing import Any, Optional
from pyharness.application.session_access import _session_module
if TYPE_CHECKING:
    pass
from pyharness.errors import raise_code
from pyharness.core.lifecycle import admitted

class InteractionService:
    """InteractionService responsibility; state is owned by the composing facade."""

    @admitted
    async def pending_approvals(self) -> dict:
        out: list[dict] = []
        seen: set[tuple] = set()
        for p in self.all_approval_providers():
            pending = getattr(p, "_pending", None)
            if not isinstance(pending, dict):
                continue
            for aid, req in pending.items():
                identity = (getattr(req, 'session_id', ''), aid)
                if identity in seen:
                    continue
                seen.add(identity)
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
            elif callable(getattr(p, "owns_initializing", None)):
                if p.owns_initializing(aid, sid=sid):
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

    @admitted
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
            runner = getattr(getattr(spine, 'subagent', None), '_runner', None)
            for child_provider in getattr(runner, 'ask_providers', {}).values():
                if id(child_provider) not in seen:
                    seen.add(id(child_provider))
                    out.append(child_provider)
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

    @admitted
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

    @admitted
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

