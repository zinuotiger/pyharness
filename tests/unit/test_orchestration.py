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
