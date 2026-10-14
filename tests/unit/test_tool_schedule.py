"""tests/unit/test_tool_schedule.py — schedule Agent Tool 单测(F048 Consumer 面)。

契约:tool_schedule 只做**登记 + 委托**——表达式校验/持久化/ticker/恢复全部由
现有 Scheduler 承担(唯一调度真源)。本文件证明:
    1. Engine 装配后 schedule 工具进注册表且绑定 Provider(agent 可见)
    2. create/list/pause/resume/remove 五个 op 均到达**现有 Scheduler**
    3. create 成功产生 schedule.registered(事件真源由 Scheduler 写)
    4. 非法参数(缺参/非法表达式/未知 op)一律 EVT-100/CFG-601 拒绝且**零事件**
    5. 会话隔离成立(A 的 job 不出现在 B)
    6. 工具不绕过 Scheduler:未知 op 不走 getattr 反射分派
    7. 工具不绕过治理管道:AgentLoop → tool 调用仍产出
       guard.evaluated / decision.issued / receipt.emitted
    8. 端到端闭环(fake adapter):AgentLoop → schedule tool → Scheduler →
       schedule.registered → agent.message

时间纪律:经 monkeypatch schedule_mod.now 冻结时钟;闭环用例用离线 fake adapter
(零网络),同 test_engine_flow.py 手法。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

import pyharness.core.schedule as S
from pyharness.core import llm as llm_mod
from pyharness.core.schedule import Scheduler
from pyharness.core.task_queue import TaskQueue
from pyharness.core.tool_schedule import TOOL_NAME, _ScheduleHandle, register
from pyharness.errors import PyHError

SID = "s-toolsched01"
BASE = datetime(2026, 9, 18, 19, 56, 0)      # 周五傍晚(BUG-1 场景同基准)


# ===================================================================== 替身
class FakeSession:
    """轻量异步事件落点替身(同步记录 + seq 递增)。"""

    def __init__(self, sid: str = SID) -> None:
        self.events: list = []
        self._seq = 0
        self.session_id = sid

    async def append(self, type_, payload, *, actor, **kw):
        self._seq += 1
        env = SimpleNamespace(seq=self._seq, type=type_, actor=actor,
                              payload=dict(payload), session_id=self.session_id)
        self.events.append(env)
        return env

    def of(self, type_) -> list:
        return [e for e in self.events if e.type == type_]

    def types(self) -> list:
        return [e.type for e in self.events]


class FakeQueue:
    async def submit(self, intent, *, meta=None, task_id=None):
        return "t-1"


def _mk(sid: str = SID):
    """装配:真 Scheduler(事件化) + 替身会话/队列;ctx 仅有 schedule 面。"""
    sess = FakeSession(sid)
    q = FakeQueue()
    sched = Scheduler(session=sess, task_queue=q, auto_ticker=False)
    ctx = SimpleNamespace(schedule=sched, session=sess, task_queue=q)
    return sched, sess, ctx


@pytest.fixture
def clock(monkeypatch):
    """冻结 schedule_mod.now → 2026-09-18 19:56(周五),时间推导可控。"""
    monkeypatch.setattr(S, "now", lambda: BASE)


async def _call(ctx, **args) -> dict:
    """经 Provider.handle 调一次工具,返回解析后的结构化结果。"""
    out = await _ScheduleHandle().handle(args, ctx)
    return json.loads(out)


# ===================================================================== 1 注册
def test_register_binds_definition_and_provider():
    """register():五要素进注册表且绑 Provider(name/danger/ctx_path 契约)。"""
    reg = _RecordingRegistry()
    names = register(reg)
    assert names == [TOOL_NAME]
    defn, provider = reg.entries[TOOL_NAME]
    assert defn.name == "schedule" and defn.danger == "low"
    assert defn.ctx_path == "schedule" and defn.owner == "builtin"
    assert isinstance(provider, _ScheduleHandle)
    # schema 契约:op 枚举齐五值,create 必需的四个字段有声明
    props = defn.schema["properties"]
    assert set(props["op"]["enum"]) == {"create", "list", "pause",
                                        "resume", "remove"}
    assert {"name", "kind", "expr", "intent", "is_risky"} <= set(props)


class _RecordingRegistry:
    def __init__(self) -> None:
        self.entries: dict[str, tuple] = {}

    def register_tool(self, defn, *, provider=None):
        self.entries[defn.name] = (defn, provider)
        return defn.name


# ===================================================================== 2/3 create
async def test_create_reaches_scheduler_and_writes_registered(clock):
    """create → 现有 Scheduler.register → schedule.registered(唯一真源)。"""
    sched, sess, ctx = _mk()
    out = await _call(ctx, op="create", name="morning", kind="cron",
                      expr="0 8 * * *", intent="提醒我开始工作")
    assert out["ok"] is True and out["op"] == "create"
    assert out["name"] == "morning" and out["expr"] == "0 8 * * *"
    assert out["next_fire_at"] == "2026-09-19T08:00:00"     # 次日 08:00
    reg = sess.of("schedule.registered")
    assert len(reg) == 1
    assert reg[0].payload["name"] == "morning"
    assert reg[0].payload["template"] == {"intent": "提醒我开始工作"}
    assert reg[0].actor == "system"                          # 由 Scheduler 写
    # 确实是**同一个** Scheduler 实例(不绕过):只读面看到同一 job
    assert [j.name for j in await sched.list_jobs(ctx=ctx)] == ["morning"]


async def test_create_is_risky_is_monotonic(clock):
    """is_risky 单调:只能升级为危险,False 不得下调(交回 Scheduler 推导)。"""
    _, sess, ctx = _mk()
    await _call(ctx, op="create", name="job_a", kind="cron", expr="0 8 * * *",
                intent="x", is_risky=True)
    assert sess.of("schedule.registered")[0].payload["is_risky"] is True
    _, sess2, ctx2 = _mk("s-toolsched02")
    await _call(ctx2, op="create", name="job_b", kind="cron", expr="0 8 * * *",
                intent="x", is_risky=False)
    assert sess2.of("schedule.registered")[0].payload["is_risky"] is False


# ===================================================================== 4 其余 op
async def test_list_pause_resume_remove_reach_scheduler(clock):
    """list/pause/resume/remove 四个 op 均到达同一 Scheduler 实例。"""
    sched, sess, ctx = _mk()
    await _call(ctx, op="create", name="job1", kind="interval", expr="600",
                intent="巡检")
    lst = await _call(ctx, op="list")
    assert lst["count"] == 1 and lst["jobs"][0]["name"] == "job1"
    assert lst["jobs"][0]["paused"] is False

    assert (await _call(ctx, op="pause", name="job1"))["ok"] is True
    assert sched._jobs["job1"].paused is True
    assert sess.of("schedule.updated")[-1].payload["paused"] is True
    assert (await _call(ctx, op="list"))["jobs"][0]["paused"] is True

    assert (await _call(ctx, op="resume", name="job1"))["ok"] is True
    assert sched._jobs["job1"].paused is False
    assert sess.of("schedule.updated")[-1].payload["paused"] is False

    assert (await _call(ctx, op="remove", name="job1"))["ok"] is True
    assert "job1" not in sched._jobs
    assert sess.of("schedule.removed")[-1].payload["name"] == "job1"
    assert (await _call(ctx, op="list"))["count"] == 0


# ===================================================================== 5 非法参数
async def test_invalid_params_produce_no_schedule_events(clock):
    """非法参数一律拒绝且**零 schedule 事件**(不产生错误的任务定义)。"""
    _, sess, ctx = _mk()
    cases = [
        ({"op": "create", "kind": "cron", "expr": "0 8 * * *",
          "intent": "x"}, "EVT-100"),                       # 缺 name
        ({"op": "create", "name": "job", "expr": "0 8 * * *",
          "intent": "x"}, "EVT-100"),                       # 缺 kind
        ({"op": "create", "name": "job", "kind": "cron",
          "intent": "x"}, "EVT-100"),                       # 缺 expr
        ({"op": "create", "name": "job", "kind": "cron",
          "expr": "0 8 * * *"}, "EVT-100"),                 # 缺 intent
        ({"op": "create", "name": "job", "kind": "cron",
          "expr": "0 8 * *", "intent": "x"}, "CFG-601"),    # cron 段数错
        ({"op": "create", "name": "job", "kind": "cron",
          "expr": "0 0 30 2 *", "intent": "x"}, "CFG-601"),  # 无未来触发点
        ({"op": "create", "name": "job", "kind": "bogus",
          "expr": "x", "intent": "x"}, "EVT-100"),          # kind 非法
        ({"op": "create", "name": "BAD NAME", "kind": "cron",
          "expr": "0 8 * * *", "intent": "x"}, "CFG-601"),  # name 形态非法
        ({"op": "pause"}, "EVT-100"),                       # 缺 name
        ({"op": "remove"}, "EVT-100"),                      # 缺 name
        ({"op": "explode", "name": "job"}, "EVT-100"),      # 未知 op
        ({"op": ""}, "EVT-100"),                            # 空 op
    ]
    for args, code in cases:
        with pytest.raises(PyHError) as ei:
            await _call(ctx, **args)
        assert ei.value.code == code, f"{args} → {ei.value.code}(期望 {code})"
    # 全部拒绝:除 schedule.* 外不得留下任何调度事件
    assert [t for t in sess.types() if t.startswith("schedule")] == []


async def test_unknown_op_is_not_reflected_onto_scheduler(clock):
    """未知 op 不得经 getattr 反射命中 Scheduler 的任意方法(关 4/CND-04)。"""
    sched, sess, ctx = _mk()
    before = set(dir(sched))
    with pytest.raises(PyHError):
        await _call(ctx, op="stop", name="x")       # Scheduler.stop() 真实存在
    assert set(dir(sched)) == before
    assert sched._stopped is False                  # 未被反射调用


# ===================================================================== 6 会话隔离
async def test_session_isolation_between_schedulers(clock):
    """A 会话建的 job 只落在 A 的事件源,B 不受影响(事件源绑定)。"""
    a_sched, a_sess, a_ctx = _mk("s-toolschedA1")
    b_sched, b_sess, b_ctx = _mk("s-toolschedB1")
    await _call(a_ctx, op="create", name="only_a", kind="cron",
                expr="0 8 * * *", intent="A 的任务")
    assert (await _call(a_ctx, op="list"))["count"] == 1
    assert (await _call(b_ctx, op="list"))["count"] == 0
    assert [t for t in b_sess.types() if t.startswith("schedule")] == []
    assert "only_a" not in b_sched._jobs


async def test_missing_ctx_schedule_raises_cyc999(clock):
    """装配缺失(ctx.schedule 未挂)→ CYC-999,不静默降级。"""
    with pytest.raises(PyHError) as ei:
        await _ScheduleHandle().handle({"op": "list"}, SimpleNamespace())
    assert ei.value.code == "CYC-999"


# ===================================================================== 7/8 引擎闭环
class ScriptedAdapter:
    """离线适配器:轮 1 调 schedule 工具建任务;轮 2 起纯文本终态。"""

    model = "deepseek-chat"

    def __init__(self) -> None:
        self.n = 0
        self.seen_tools: list[str] = []

    async def chat(self, messages, tools=None, *, ctx):
        self.n += 1
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=10)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        if tools:
            self.seen_tools += [(t.get("function") or {}).get("name")
                                for t in tools]
        if self.n == 1:
            raw = json.dumps({"op": "create", "name": "morning",
                              "kind": "cron", "expr": "0 8 * * *",
                              "intent": "提醒我开始工作"}, ensure_ascii=False)
            calls = [llm_mod.ToolCall(id="c1", index=0, name="schedule",
                                      raw_args=json.loads(raw), raw_json=raw)]
            resp = llm_mod.LLMResponse(content="", tool_calls=calls,
                                       usage=usage, model=self.model,
                                       finish_reason="tool_calls")
        else:
            resp = llm_mod.LLMResponse(content="已为你设置每天 08:00 的提醒。",
                                       tool_calls=None, usage=usage,
                                       model=self.model, finish_reason="stop")
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


@pytest.mark.asyncio
async def test_agent_loop_to_schedule_tool_closed_loop(tmp_path):
    """闭环:AgentLoop → schedule tool → Scheduler → schedule.registered →
    agent.message;且工具调用仍走完整治理管道(不绕过)。"""
    from pyharness import cli
    from pyharness.engine import assemble_real_engine

    cfg = cli._load_settings(None)
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "pyharness.db")

    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    fake = ScriptedAdapter()
    llm_mod.adapters[cfg.llm.model] = fake
    ctx = None
    try:
        ctx = await assemble_real_engine(cfg, sid="s-toolschedloop1",
                                         sessions_dir=tmp_path / "sessions", channel="cli")
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        intent = "帮我建一个每天早上 8 点的定时任务"
        await ctx.session.append("user.message", {"content": intent},
                                 actor="user", sync=True)
        q = ctx.task_queue
        tid = await q.submit(intent)
        res = await asyncio.wait_for(q.wait_for(tid), timeout=20.0)
        assert res.ok, f"闭环应成功,实际 code={getattr(res, 'code', res)}"

        types = [e.type for e in ctx.session.events_after(0)]
        # ① 工具确实进了 LLM 视野并被调用
        assert "schedule" in fake.seen_tools
        assert "tool.call" in types
        # ② 到达现有 Scheduler:唯一真源事件由 Scheduler 写
        assert "schedule.registered" in types
        reg = [e for e in ctx.session.events_after(0)
               if e.type == "schedule.registered"][0]
        assert reg.payload["name"] == "morning"
        assert reg.payload["expr"] == "0 8 * * *"
        # ③ 治理管道未被绕过:授权/决策/凭证齐全
        for need in ("guard.evaluated", "decision.issued", "receipt.emitted"):
            assert need in types, f"治理事件缺失:{need};实际={types}"
        assert "guard.rejected" not in types
        # ④ 回到 AgentLoop 产出用户可见回复
        assert "agent.message" in types
        # ⑤ 任务落到真实 Scheduler(spine 同一实例)
        jobs = await ctx.engine_spine.schedule.list_jobs(ctx=ctx.engine_spine)
        assert [j.name for j in jobs] == ["morning"]
        ctx.engine_spine.schedule.stop()
    finally:
        try:
            if ctx is not None:
                try:
                    await ctx.engine_spine.close()
                finally:
                    ctx.scope.release()
        finally:
            llm_mod.adapters.clear()
            llm_mod.adapters.update(saved)
