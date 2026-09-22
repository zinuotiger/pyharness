"""tests/unit/test_schedule_nl_phase2.py — Phase 2 定时任务闭环(确定性验证)。

Phase 2 的**自然语言理解**由真实 LLM 承担,其验证见 ``scripts/probe_schedule_nl.py``
(真实链路探针,默认不跑)。本文件用**离线 fake adapter** 覆盖与 LLM 无关的
**闭环与边界**,即无论 LLM 怎么翻译都必须成立的部分:

    1. 闭环:会话内 NL 请求 → schedule tool → Scheduler → 到点 trigger →
       task.enqueued → AgentLoop → **同一会话**产生 agent.message
    2. session isolation:A 会话的任务只触发到 A
    3. governance:定时任务到点后产生的 tool call 仍走 guard/decision/receipt
    4. restart recovery:重启(重建 spine)后任务仍在且能到点触发
    5. 非法参数由 **Scheduler** 拒绝执行链(CFG-601 tool.error),零 schedule.registered

时间纪律:用离线 fake adapter 驱动 AgentLoop;到点用 Scheduler._tick(now_dt=...)
显式注入,不真实等待。文件落盘经真实 store,故 dispose 时用临时目录。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from pyharness import cli
from pyharness.core import llm as llm_mod
from pyharness.core.session import open_session
from pyharness.core.task_queue import TaskQueue
from pyharness.engine import assemble_real_engine, build_runner_components
from pyharness.persistence import open_store


def _cfg(tmp_path):
    cfg = cli._load_settings(None)
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "pyharness.db")
    return cfg


def _tc(name: str, args: dict, i: int = 0):
    raw = json.dumps(args, ensure_ascii=False)
    return llm_mod.ToolCall(id=f"c{i}", index=i, name=name, raw_args=args,
                            raw_json=raw)


class ScriptedAdapter:
    """轮 1 产出**预设的**工具调用(模拟 LLM 对自然语言的翻译结果);

    其后所有轮次返回纯文本终态——故到点触发的那次 run 也能正常收尾。
    可传多个 scripted 调用列表(每轮取一个;耗尽后纯文本)。
    """

    model = "deepseek-chat"

    def __init__(self, scripted: list[list] | None = None) -> None:
        self.scripted = list(scripted or [])
        self.n = 0

    async def chat(self, messages, tools=None, *, ctx):
        self.n += 1
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=10)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        calls = self.scripted.pop(0) if self.scripted else None
        if calls:
            resp = llm_mod.LLMResponse(content="", tool_calls=calls,
                                       usage=usage, model=self.model,
                                       finish_reason="tool_calls")
        else:
            resp = llm_mod.LLMResponse(content="[FAKE] 已处理。", tool_calls=None,
                                       usage=usage, model=self.model,
                                       finish_reason="stop")
        env = await ctx.session.append(
            "llm.response",
            {"model": self.model, "finish_reason": resp.finish_reason,
             "content": resp.content or "",
             "tool_calls": [{"id": c.id, "name": c.name,
                             "arguments": c.raw_json}
                            for c in (resp.tool_calls or [])]},
            actor="llm")
        resp.seq = env.seq
        return resp

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self) -> float:
        return 0.01


class _llm_swap:
    """把 fake adapter 装进 llm 注册表(装配幂等跳过),退出时还原。"""

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.saved: dict = {}

    def __enter__(self):
        self.saved = dict(llm_mod.adapters)
        llm_mod.adapters.clear()
        llm_mod.adapters[self.adapter.model] = self.adapter
        return self.adapter

    def __exit__(self, *exc):
        llm_mod.adapters.clear()
        llm_mod.adapters.update(self.saved)
        return False


async def _boot(cfg, sid: str, text: str):
    """装配真实引擎 + 落 session.created/user.message(不建队列、不 submit)。"""
    ctx = await assemble_real_engine(cfg, sid=sid,
                                     sessions_dir=pathlib.Path(
                                         cfg.storage.sessions_dir), channel="cli")
    await ctx.session.append("session.created",
                             {"title": "", "model": cfg.llm.model},
                             actor="system", sync=True)
    await ctx.session.append("user.message", {"content": text}, actor="user",
                             sync=True)
    return ctx


async def _drain(q, timeout: float = 25.0) -> None:
    """等到当前队列里的任务全部落终态(含泵接续的后续任务)。"""
    for _ in range(20):
        st = q.status()
        pending = list(st.waiting) + ([st.running] if st.running else [])
        if not pending:
            await asyncio.sleep(0.05)
            st = q.status()
            pending = list(st.waiting) + ([st.running] if st.running else [])
            if not pending:
                return
        for tid in pending:
            try:
                await asyncio.wait_for(q.wait_for(tid), timeout=timeout)
            except asyncio.TimeoutError:
                return


CREATE = {"op": "create", "name": "daily_work", "kind": "cron",
          "expr": "0 8 * * *", "intent": "提醒我开始工作"}


# ============================================================= 1. 闭环 + 到点回话
@pytest.mark.asyncio
async def test_scheduled_fire_returns_agent_message_to_same_session(tmp_path):
    """闭环:会话内建任务 → 到点 trigger → task.enqueued → AgentLoop →
    **同一会话**产生 agent.message。"""
    cfg = _cfg(tmp_path)
    with _llm_swap(ScriptedAdapter([[ _tc("schedule", CREATE) ]])):
        ctx = await _boot(cfg, "s-p2loop00001", "每天早上8点提醒我开始工作")
        q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
        await q.submit("每天早上8点提醒我开始工作")
        await _drain(q)

        evs = list(ctx.session.events_after(0))
        reg = [e for e in evs if e.type == "schedule.registered"]
        assert len(reg) == 1, "应经 schedule 工具注册一条任务"
        p = reg[0].payload
        assert (p["kind"], p["expr"]) == ("cron", "0 8 * * *")
        assert p["template"]["intent"] == "提醒我开始工作"
        first_msgs = [e for e in evs if e.type == "agent.message"]
        assert first_msgs, "创建后应有 agent.message 确认"

        # 到点:显式驱动一次 tick(不真实等待到 08:00)
        spine = ctx.engine_spine
        spine.schedule.stop()                 # 停掉真实泵,避免与本处 tick 竞争
        job = spine.schedule._jobs["daily_work"]
        sctx = SimpleNamespace(session=spine.session, task_queue=spine.task_queue)
        await spine.schedule._tick(sctx, now_dt=job.next_fire_at)

        evs2 = list(ctx.session.events_after(0))
        assert "schedule.trigger" in [e.type for e in evs2], "到点应写 schedule.trigger"
        assert [e for e in evs2 if e.type == "task.enqueued"], "应入队"
        await _drain(q)
        evs3 = list(ctx.session.events_after(0))
        msgs = [e for e in evs3 if e.type == "agent.message"]
        assert len(msgs) > len(first_msgs), \
            "到点后应在**同一会话**新增 agent.message"


# ============================================================= 2. 会话隔离
@pytest.mark.asyncio
async def test_scheduled_trigger_stays_in_own_session(tmp_path):
    """A 会话的任务只触发到 A;B 的事件源看不到任何 schedule.*。"""
    cfg = _cfg(tmp_path)
    with _llm_swap(ScriptedAdapter([[ _tc("schedule", CREATE) ]])):
        a_ctx = await _boot(cfg, "s-p2isoA00001", "每天早上8点提醒我开始工作")
        aq = TaskQueue(a_ctx.session, runner=a_ctx.task_runner, max_queue=8)
        await aq.submit("每天早上8点提醒我开始工作")
        await _drain(aq)

        b_ctx = await _boot(cfg, "s-p2isoB00001", "你好")
        assert "daily_work" not in b_ctx.engine_spine.schedule._jobs
        assert [e for e in b_ctx.session.events_after(0)
                if e.type.startswith("schedule")] == []

        spine = a_ctx.engine_spine
        spine.schedule.stop()
        sctx = SimpleNamespace(session=spine.session, task_queue=spine.task_queue)
        await spine.schedule._tick(sctx,
                                   now_dt=spine.schedule._jobs["daily_work"].next_fire_at)
        assert "schedule.trigger" in [e.type for e in a_ctx.session.events_after(0)]
        assert [e for e in b_ctx.session.events_after(0)
                if e.type.startswith("schedule")] == []
        b_ctx.engine_spine.schedule.stop()


# ============================================================= 3. 治理管道
@pytest.mark.asyncio
async def test_scheduled_run_tool_calls_still_governed(tmp_path):
    """到点任务里产生的 tool call 仍走 guard/decision/receipt(不绕过治理)。"""
    cfg = _cfg(tmp_path)
    scripted = [
        [_tc("schedule", {"op": "create", "name": "daily_audit",
                          "kind": "cron", "expr": "0 8 * * *",
                          "intent": "列一下工作区目录"})],
        [_tc("fs.list_dir", {"path": "."})],      # 到点那次 run 调一个真实工具
    ]
    with _llm_swap(ScriptedAdapter(scripted)):
        ctx = await _boot(cfg, "s-p2gov000001", "每天早上8点列一下工作区目录")
        q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
        await q.submit("每天早上8点列一下工作区目录")
        await _drain(q)

        spine = ctx.engine_spine
        spine.schedule.stop()
        sctx = SimpleNamespace(session=spine.session, task_queue=spine.task_queue)
        await spine.schedule._tick(
            sctx, now_dt=spine.schedule._jobs["daily_audit"].next_fire_at)
        await _drain(q)

        types = [e.type for e in ctx.session.events_after(0)]
        for need in ("guard.evaluated", "decision.issued", "receipt.emitted"):
            assert need in types, f"治理事件缺失:{need};实际={types}"
        assert "tool.result" in types


# ============================================================= 4. 重启恢复
@pytest.mark.asyncio
async def test_restart_recovery_keeps_job_and_can_fire(tmp_path):
    """重启(= 用同一会话事件源重建 spine)后任务仍在,且仍能到点触发。"""
    cfg = _cfg(tmp_path)
    with _llm_swap(ScriptedAdapter([[ _tc("schedule", CREATE) ]])):
        ctx = await _boot(cfg, "s-p2rst000001", "每天早上8点提醒我开始工作")
        q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
        await q.submit("每天早上8点提醒我开始工作")
        await _drain(q)
        ctx.engine_spine.schedule.stop()
        sdir = pathlib.Path(cfg.storage.sessions_dir)
        # 落盘:持久化是异步批量写,_drain 完成不保证已刷盘(重启前须显式 flush)
        store1 = ctx.engine_spine.persistence
        await store1.flush()
        if hasattr(store1, "close"):
            store1.close()

        # —— 重启:丢掉内存 spine,从同一 JSONL 事件源重新装配 ——
        store = open_store("s-p2rst000001", dir=sdir)
        log_ = await open_session("s-p2rst000001", store)
        spine2 = build_runner_components(cfg, log_=log_, bus=None,
                                         sessions_dir=sdir, store=store,
                                         attach_persistence=False)
        q2 = TaskQueue(session=log_, runner=ctx.task_runner, max_queue=8)
        from pyharness.engine import activate_orchestration
        await activate_orchestration(spine2, task_queue=q2)   # 内含 recover
        assert "daily_work" in spine2.schedule._jobs, "重启后任务应被回放恢复"
        job = spine2.schedule._jobs["daily_work"]
        assert job.next_fire_at is not None

        spine2.schedule.stop()
        before = len([e for e in log_.events_after(0) if e.type == "schedule.trigger"])
        sctx = SimpleNamespace(session=log_, task_queue=q2)
        await spine2.schedule._tick(sctx, now_dt=job.next_fire_at)
        after = len([e for e in log_.events_after(0) if e.type == "schedule.trigger"])
        assert after == before + 1, "重启后到点仍应触发"


# ============================================================= 5. 非法参数
@pytest.mark.asyncio
async def test_illegal_tool_args_rejected_by_scheduler(tmp_path):
    """LLM 产出非法结构(4 字段 cron)→ 由 Scheduler 拒绝:CFG-601 tool.error,
    且**零 schedule.registered**(校验不被绕过,也不落错误任务)。"""
    cfg = _cfg(tmp_path)
    bad = {"op": "create", "name": "broken", "kind": "cron",
           "expr": "0 8 * *", "intent": "坏的"}      # cron 只有 4 字段
    with _llm_swap(ScriptedAdapter([[ _tc("schedule", bad) ]])):
        ctx = await _boot(cfg, "s-p2bad000001", "每天8点提醒我")
        q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
        await q.submit("每天8点提醒我")
        await _drain(q)

        evs = list(ctx.session.events_after(0))
        assert [e for e in evs if e.type == "schedule.registered"] == []
        errs = [e for e in evs if e.type == "tool.error"]
        assert errs and errs[0].payload["code"] == "CFG-601", \
            f"应由 Scheduler 显式拒绝;实际={[e.type for e in evs]}"
        assert "broken" not in ctx.engine_spine.schedule._jobs
        ctx.engine_spine.schedule.stop()
