"""Engine-facing adapters for background jobs and subagents.

The orchestration modules intentionally depend on injected runners.  This module
is the missing production adapter: it turns one intent into a real AgentLoop
run while preserving the caller's session/scope boundary.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from pyharness.core.agent_loop import AgentLoop


def _last_text(session: Any, after_seq: int) -> str:
    for env in reversed(list(session.events_after(after_seq))):
        if getattr(env, "type", "") not in ("agent.message", "llm.response"):
            continue
        content = (getattr(env, "payload", {}) or {}).get("content")
        if content:
            return str(content)
    return ""


def _runtime_ctx(spine: Any, session: Any, scope: Any, loop: Any, *,
                 task_id: Optional[str], owner: Optional[str],
                 depth: int = 0) -> Any:
    """Build the ctx consumed by AgentLoop for one independent run.

    Parent-session jobs reuse the parent goal/todo managers.  Child sessions get
    their own managers so their event projections cannot mix with the parent.
    Guard/approval remain the parent engine instances; approval is deliberately
    routed through the owning session's UI/bridge, as required by jobs/subagent
    security rules.  depth = 本会话所处递归层级(主=0,子=spec.depth):经
    ctx.subagent_depth 供 subagent.spawn 计算 d+1,使递归链硬上限 ≤3 生效。
    """
    if session is getattr(spine, "session", None):
        goals = getattr(spine, "goals", None)
        todos = getattr(spine, "todos", None)
        ask = getattr(spine, "ask", None)
    else:
        from pyharness.core.goal import GoalManager
        from pyharness.core.todo import TodoManager
        from pyharness.core.user_ask import AskProvider
        goals = GoalManager(session=session)
        todos = TodoManager(session=session)
        ask = AskProvider(session=session, bus=getattr(spine, "bus", None))

    return SimpleNamespace(
        config=getattr(spine, "settings", None),
        session=session,
        scope=scope,
        llm=getattr(spine, "llm", None),
        tools=getattr(spine, "tools", None),
        loop=loop,
        sysprompt=getattr(spine, "sysprompt", None),
        compactor=getattr(spine, "compactor", None),
        streaming=False,
        bus=getattr(spine, "bus", None),
        registry=getattr(spine, "registry", None),
        guard=getattr(spine, "guard", None),
        approval=getattr(spine, "approval", None),
        counters=getattr(spine, "counters", None),
        storage=getattr(spine, "storage", None),
        goals=goals,
        todos=todos,
        ask=ask,
        skills=getattr(spine, "skills", None),
        search_backend=getattr(spine, "search_backend", None),
        owner=owner,
        task_id=task_id,
        subagent_depth=int(depth),
    )


async def _run_on_session(spine: Any, session: Any, scope: Any, intent: str, *,
                          task_id: Optional[str], origin: str,
                          owner: Optional[str], depth: int = 0) -> tuple[Any, int]:
    loop = AgentLoop(getattr(spine, "settings", None))
    env = await session.append(
        "user.message", {"content": intent}, actor="user",
        origin=origin, task_id=task_id, sync=True)
    ctx = _runtime_ctx(spine, session, scope, loop, task_id=task_id,
                       owner=owner, depth=depth)
    result = await loop.wake(env, ctx=ctx)
    return result, int(getattr(env, "seq", 0) or 0)


class EngineIntentRunner:
    """JobManager runner: execute one background intent on the shared session.

    经会话 TaskQueue 单飞泵提交(与交互轮次共享同一 loop/泵),避免另起 AgentLoop
    直驱父会话造成"同会话并发 LLM 驱动 + 共享 counters 预算串扰"(EngineIntentRunner
    绕过单飞泵,2026-09-13 修复)。无队列装配(轻装配/单测)时兜底直驱。入队用队列
    自动 task_id(勿传 job id:JobManager 已为该 job id 开段,复用会撞"段已打开")。
    """

    def __init__(self, spine: Any) -> None:
        self.spine = spine

    async def run(self, ctx: Any, *, intent: str, task_id: str,
                  budget: Any = None) -> Any:
        tq = getattr(self.spine, "task_queue", None)
        session = getattr(self.spine, "session", None)
        if tq is not None and session is not None:
            # 先落 user.message(泵 runner 按 enqueued_seq 定位),再入队 + 等终态
            await session.append("user.message", {"content": intent}, actor="user",
                                 origin="job", task_id=task_id, sync=True)
            queued = await tq.submit(intent)          # 队列自动 t-N(避开 job 段)
            res = await tq.wait_for(queued)
            ok = bool(getattr(res, "ok", False))
            reason = "complete" if ok else str(getattr(res, "code", "") or "error")
            return SimpleNamespace(reason=reason)     # JobManager 读 .reason 归一
        result, _ = await _run_on_session(
            self.spine, session, getattr(self.spine, "scope", None),
            intent, task_id=task_id, origin="job",
            owner=getattr(ctx, "owner", None))
        return result


class EngineSubagentRunner:
    """SubagentManager runner: execute a child in its isolated session."""

    def __init__(self, spine: Any, sessions_dir: Any) -> None:
        self.spine = spine
        self.sessions_dir = sessions_dir
        self._stores: dict[str, Any] = {}

    def child_session(self, sub_id: str) -> Any:
        """Create a persisted child SessionLog with an isolated bus.

        The child must not share the parent bus: the parent engine persists all
        events seen on its bus, so child events would otherwise be duplicated
        into the parent JSONL.
        """
        from pyharness.bus import EventBus
        from pyharness.core.session import SessionLog
        from pyharness.events.vocab import EVENT_TYPES, SYNC_TYPES
        from pyharness.persistence import open_store

        store = open_store(sub_id, dir=self.sessions_dir)
        child_bus = EventBus()

        async def _record(type_: str, payload: Any) -> None:
            await store.append(payload, sync=type_ in SYNC_TYPES)

        for type_ in EVENT_TYPES:
            child_bus.subscribe(type_, _record, owner=f"subagent-store:{sub_id}")
        self._stores[sub_id] = store
        return SessionLog(sid=sub_id, persistence=store, bus=child_bus)

    async def run_child(self, env: Any) -> str:
        try:
            result, start_seq = await _run_on_session(
                self.spine, env.session, env.scope or getattr(self.spine, "scope", None),
                env.spec.task, task_id=f"subagent:{env.sub_id}",
                origin=f"subagent:{env.sub_id}", owner="system",
                depth=int(getattr(env.spec, "depth", 0)))
            text = _last_text(env.session, start_seq)
            if text:
                return text
            return str(getattr(result, "reason", "") or "")
        finally:
            store = self._stores.pop(getattr(env, "sub_id", ""), None)
            if store is not None:
                try:
                    flush = getattr(store, "flush", None)
                    if callable(flush):
                        maybe = flush()
                        if hasattr(maybe, "__await__"):
                            await maybe
                except Exception:
                    pass
                try:
                    store.close()
                except Exception:
                    pass


__all__ = ["EngineIntentRunner", "EngineSubagentRunner"]
