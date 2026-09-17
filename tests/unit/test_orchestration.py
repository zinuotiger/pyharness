from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyharness.config import Settings, load_settings
from pyharness.core import llm as llm_mod
from pyharness.core.subagent import SubagentSpec
from pyharness.engine import assemble_real_engine


def _cfg(tmp_path: Path) -> Settings:
    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "pyharness.db")
    return cfg


class _Adapter:
    model = "deepseek-chat"

    async def chat(self, messages, tools=None, *, ctx):
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=10)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        resp = llm_mod.LLMResponse(content="done", tool_calls=None,
                                   usage=usage, model=self.model,
                                   finish_reason="stop")
        env = await ctx.session.append(
            "llm.response",
            {"model": self.model, "finish_reason": "stop", "content": "done",
             "tool_calls": []}, actor="llm")
        resp.seq = env.seq
        return resp

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self):
        return 0.01


@pytest.mark.asyncio
async def test_jobs_runner_executes_real_agent_loop(tmp_path):
    cfg = _cfg(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters[cfg.llm.model] = _Adapter()
    ctx = None
    try:
        ctx = await assemble_real_engine(cfg, sid="s-job-run-000001",
                                         sessions_dir=Path(tmp_path) / "sessions")
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        ctx.owner = "cli:test"
        jid = await ctx.engine_spine.jobs.start("background task", ctx)
        for _ in range(100):
            st = await ctx.engine_spine.jobs.status(jid, by="cli:test")
            if st.state in ("completed", "failed"):
                break
            await asyncio.sleep(0.02)
        assert st.state == "completed", st
        assert any(e.type == "job.completed" for e in ctx.session.events_after(0))
        assert any(e.task_id == "job:j-1" for e in ctx.session.events_after(0))
    finally:
        if ctx is not None:
            await ctx.engine_spine.close()
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


@pytest.mark.asyncio
async def test_subagent_runner_executes_persisted_child(tmp_path):
    cfg = _cfg(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters[cfg.llm.model] = _Adapter()
    ctx = None
    try:
        sessions = Path(tmp_path) / "sessions"
        ctx = await assemble_real_engine(cfg, sid="s-sub-run-000001",
                                         sessions_dir=sessions)
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        mgr = ctx.engine_spine.subagent
        sub_id = await mgr.spawn(SubagentSpec(task="child task", depth=1),
                                 SimpleNamespace(round_seq=4))
        summary = await mgr.join(sub_id, timeout=5)
        assert summary == "done"
        assert (sessions / f"{sub_id}.jsonl").exists()
        assert any(e.type == "subagent.joined"
                   for e in ctx.session.events_after(0))
    finally:
        if ctx is not None:
            await ctx.engine_spine.close()
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


# ================================================================ RT-GOV-01
# 运行时 ctx 的治理接线回归面。
#
# 缺陷:_runtime_ctx(orchestration.py)遗漏 `governance` 键,而
# tools_executor._require_wiring 在关 1 之前硬要求它非 None ⇒ **子 Agent 与
# 无队列 IntentRunner 的每一次工具调用 fail-closed(CYC-999)**;且因该检查早于
# `tool.call` 发射,事件日志里连 tool.call 都没有,只留一条 system.error。
#
# 为什么既有用例拦不住:`test_subagent_runner_executes_persisted_child` 虽用真实
# runner,但其 LLM 替身从不发 tool_call ⇒ `tools.execute` 从未被进入。**故本组
# 判据必须是"工具真的执行了",而不是"子任务返回了文本"。**
#
# 三条路径 = wake() 的全部调用点:
#   ① 主 Agent   engine._agent_ctx_of → create_agent          (对照,缺陷期即正常)
#   ② 子 Agent   EngineSubagentRunner.run_child → _runtime_ctx
#   ③ 编排兜底   EngineIntentRunner.run(task_queue=None) → _runtime_ctx


class _ToolCallingAdapter:
    """首轮发一次工具调用,其后返回纯文本(驱动真实 tools.execute 管道)。"""

    model = "deepseek-chat"

    def __init__(self, tool: str = "util.now") -> None:
        self._tool = tool
        self._turns = 0

    async def _respond(self, ctx):
        self._turns += 1
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=10)
        if self._turns == 1:
            tc = llm_mod.ToolCall(id="tc1", index=0, name=self._tool,
                                  raw_args={}, raw_json="{}")
            calls, content, finish = [tc], "", "tool_calls"
        else:
            calls, content, finish = [], "done", "stop"
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        env = await ctx.session.append(
            "llm.response",
            {"model": self.model, "finish_reason": finish, "content": content,
             "tool_calls": ([{"id": "tc1", "name": self._tool,
                              "arguments": "{}"}] if self._turns == 1 else [])},
            actor="llm")
        resp = llm_mod.LLMResponse(content=content, tool_calls=calls,
                                   usage=usage, model=self.model,
                                   finish_reason=finish)
        resp.seq = env.seq
        return resp

    async def chat(self, messages, tools=None, *, ctx):
        return await self._respond(ctx)

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self._respond(ctx)

    async def ping(self):
        return 0.01


def _types(events) -> list:
    return [e.type for e in events]


def _cyc999(events) -> list:
    return [e for e in events
            if e.type == "system.error"
            and (e.payload or {}).get("code") == "CYC-999"]


def _tool_results(events) -> list:
    return [e for e in events if e.type == "tool.result"]


def _assert_tool_ran(events, *, label: str) -> None:
    """共同判据:进入治理闸、工具执行成功、无 CYC-999。"""
    ts = _types(events)
    assert not _cyc999(events), \
        f"{label}: 出现 CYC-999(装配缺件)⇒ 工具调用未达治理闸:{ts}"
    assert "tool.call" in ts, f"{label}: 缺 tool.call ⇒ 管道未启动:{ts}"
    assert "decision.issued" in ts, \
        f"{label}: 缺 decision.issued ⇒ 未经过 authorize 单入口:{ts}"
    results = _tool_results(events)
    assert results, f"{label}: 缺 tool.result ⇒ 工具未执行:{ts}"
    assert (results[0].payload or {}).get("name") == "util.now", \
        f"{label}: tool.result 名字不符:{results[0].payload}"


@pytest.mark.asyncio
async def test_runtime_ctx_propagates_governance_singleton(tmp_path):
    """结构面:_runtime_ctx 必须原样透传 spine 的治理实例(不自建、不漏挂)。

    身份同一性断言 —— 治理层是**单实例**(ADR-018);此处若新建或漏挂,
    父子会话会各自持有不同的策略面。
    """
    from pyharness.core.orchestration import _runtime_ctx

    cfg = _cfg(tmp_path)
    ctx = await assemble_real_engine(cfg, sid="s-rtgov-struct-01",
                                     sessions_dir=tmp_path / "sessions")
    try:
        spine = ctx.engine_spine
        assert spine.governance is not None, "真实装配必须产出治理实例"
        bare = SimpleNamespace(settings=cfg, session=spine.session,
                               scope=spine.scope, tools=spine.tools,
                               bus=spine.bus, guard=spine.guard,
                               approval=spine.approval)
        for label, sr in (("有治理", spine), ("无治理", bare)):
            built = _runtime_ctx(sr, spine.session, spine.scope, spine.loop,
                                 task_id="t-1", owner="system")
            assert built.governance is getattr(sr, "governance", None), \
                f"{label}: governance 未原样透传"
    finally:
        await ctx.engine_spine.close()


@pytest.mark.asyncio
async def test_child_agent_tool_execution_reaches_governance(tmp_path):
    """② 子 Agent:真实 EngineSubagentRunner 下工具调用必须真正执行。

    缺陷期 RED:子会话只有 system.error(CYC-999),无 tool.call/tool.result。
    """
    cfg = _cfg(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters[cfg.llm.model] = _ToolCallingAdapter()
    ctx = None
    try:
        sessions = tmp_path / "sessions"
        ctx = await assemble_real_engine(cfg, sid="s-rtgov-child-01",
                                         sessions_dir=sessions)
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        spine = ctx.engine_spine
        from pyharness import engine as _eng
        await _eng._preload_plugins(spine)          # util.now 会话可用

        mgr = spine.subagent
        sub_id = await mgr.spawn(SubagentSpec(task="use a tool", depth=1),
                                 SimpleNamespace(round_seq=4))
        await mgr.join(sub_id, timeout=8)

        from pyharness.persistence import open_store
        store = open_store(sub_id, dir=sessions)
        try:
            child_events = list(store.replay())
        finally:
            store.close()

        _assert_tool_ran(child_events, label="子 Agent")
        parent_types = _types(ctx.session.events_after(0))
        assert "subagent.spawned" in parent_types, parent_types
        assert "subagent.joined" in parent_types, parent_types
    finally:
        if ctx is not None:
            await ctx.engine_spine.close()
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


@pytest.mark.asyncio
async def test_orchestration_fallback_tool_execution_reaches_governance(tmp_path):
    """③ 编排兜底:task_queue=None 时 IntentRunner 走 _run_on_session。

    缺陷期 RED:job 以 CYC-999 失败,日志无 tool.call/tool.result。
    """
    cfg = _cfg(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters[cfg.llm.model] = _ToolCallingAdapter()
    ctx = None
    try:
        ctx = await assemble_real_engine(cfg, sid="s-rtgov-intent-01",
                                         sessions_dir=tmp_path / "sessions")
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        spine = ctx.engine_spine
        from pyharness import engine as _eng
        await _eng._preload_plugins(spine)
        spine.task_queue = None                     # 强制走兜底分支
        ctx.owner = "cli:test"

        jid = await spine.jobs.start("use a tool", ctx)
        st = None
        for _ in range(150):
            st = await spine.jobs.status(jid, by="cli:test")
            if st.state in ("completed", "failed"):
                break
            await asyncio.sleep(0.02)
        assert st is not None and st.state == "completed", st

        _assert_tool_ran(list(ctx.session.events_after(0)), label="编排兜底")
    finally:
        if ctx is not None:
            await ctx.engine_spine.close()
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


@pytest.mark.asyncio
async def test_main_agent_tool_execution_reaches_governance(tmp_path):
    """① 主 Agent(对照):task_queue 路径工具调用正常执行。

    缺陷期即 GREEN —— 它是"本应长这样"的形态锚,防止修复后三条路径被改成
    同一副错误形态。
    """
    cfg = _cfg(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters[cfg.llm.model] = _ToolCallingAdapter()
    ctx = None
    try:
        ctx = await assemble_real_engine(cfg, sid="s-rtgov-main-01",
                                         sessions_dir=tmp_path / "sessions")
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        spine = ctx.engine_spine
        from pyharness import engine as _eng
        await _eng._preload_plugins(spine)
        await ctx.session.append("user.message", {"content": "use a tool"},
                                 actor="user", sync=True)
        queued = await spine.task_queue.submit("use a tool")
        res = await spine.task_queue.wait_for(queued)

        assert bool(getattr(res, "ok", False)), res
        _assert_tool_ran(list(ctx.session.events_after(0)), label="主 Agent")
    finally:
        if ctx is not None:
            await ctx.engine_spine.close()
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


@pytest.mark.asyncio
async def test_runtime_ctx_still_fails_closed_without_governance(tmp_path):
    """fail-closed 不得被削弱:spine 无治理实例 ⇒ 仍 CYC-999,工具零执行。

    与本组其它用例互补:本次修的是"**有**治理却漏挂",不是"把检查去掉"。
    """
    cfg = _cfg(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters[cfg.llm.model] = _ToolCallingAdapter()
    ctx = None
    try:
        ctx = await assemble_real_engine(cfg, sid="s-rtgov-nogov-01",
                                         sessions_dir=tmp_path / "sessions")
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        spine = ctx.engine_spine
        from pyharness import engine as _eng
        await _eng._preload_plugins(spine)
        spine.governance = None                     # 模拟未装配治理的脊柱
        spine.task_queue = None
        ctx.owner = "cli:test"

        jid = await spine.jobs.start("use a tool", ctx)
        st = None
        for _ in range(150):
            st = await spine.jobs.status(jid, by="cli:test")
            if st.state in ("completed", "failed"):
                break
            await asyncio.sleep(0.02)

        events = list(ctx.session.events_after(0))
        assert _cyc999(events), f"缺治理必须 fail-closed(CYC-999):{_types(events)}"
        assert not _tool_results(events), "缺治理时工具不得被执行"
    finally:
        if ctx is not None:
            await ctx.engine_spine.close()
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)
