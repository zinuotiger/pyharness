"""agent_loop 模块单测 — 契约:specs/agent_loop.py.md + DIS-CORE §1 + PARAMETER-ANCHOR

覆盖面(任务要求全项):
    GWT-L1-01 自然结束:纯文本 1 次 llm.chat、agent.message 落盘、回 idle、快照
    GWT-L1-02 轮数上限:max_turns=3 恒 tool_calls → 恰 3 次、reason=max_turns、无第 4 次
    收敛终止:恒返同一 tool.result → 连续 stall_limit 轮同指纹 → reason=stall
    取消传播(F025):工具步中 cancel → system.cancelled 落盘、子任务取消、回 idle、
        无悬空协程;LLM 调用中 cancel → 协作式跑完,闸2 下一轮起点触发 cancelled
    入队/拒新:running 期 wake 入队(FIFO 消费)、队满 BUSY + system.error、非 idle 拒
    LLM 异常降级路径:LLM-310 → system.error + reason=error;未预期 → CYC-999
    turn/step 计数:工具轮计数、单轮多工具步顺序执行、指纹派生自会话日志(INV-01)
    不变量:循环必终止(INV-08)、三闸独立触发且只读、错误码走 errors 域

LLM/工具/scope 全为注入替身——本模块不触真实 API;llm.chat 唯一经 run_turn 调用,
上下文唯一来源 = 会话日志派生(INV-01/INV-02 单测侧证据)。
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from pyharness.core.agent_loop import (AgentLoop, BudgetExhausted,
                                       RunContext, RunResult)
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError
from pyharness.events import Envelope

SID = "s-agntloop1"


# ===================================================================== 替身
def _make_resp(content: str = "", tool_calls=None, seq: int = 0):
    """LLMResponse 替身(content/tool_calls/seq;seq 由 FakeLLM 落盘后回填)。"""
    return SimpleNamespace(content=content, tool_calls=tool_calls,
                           model="mock-llm", finish_reason="mock", seq=seq)


def _make_call(name: str = "mock_tool", call_id: str = "call_1"):
    """ToolCall 替身(name/raw_args/call_id;tools.execute 消费)。"""
    return SimpleNamespace(name=name, raw_args={}, call_id=call_id, id=call_id)


class FakeLLM:
    """脚本化 LLM 替身:chat 依序弹响应;gate 可阻塞(测取消/并发)。

    忠实模拟 llm 层副作用:每次 chat 先落 llm.request/llm.response 事件(真实 llm
    层职责),故 resp.seq = 本次 llm.response 的日志 seq(供 trace 父关联)。
    gate: 非 None 时对第 gate_from 次起的调用先 await(阻塞);gate_from=0 = 全部阻塞。
    repeat_last: 响应耗尽后重复最后一条(恒 tool_calls 场景用);否则回落纯文本。
    """

    def __init__(self, responses=None, *, gate: asyncio.Event = None,
                 gate_from: int = 0, history: bool = True,
                 repeat_last: bool = False, raise_exc: Exception = None) -> None:
        self.responses = list(responses or [])
        self.gate = gate
        self.gate_from = gate_from
        self.history = history                # 是否落 llm.response 日志
        self.repeat_last = repeat_last
        self.raise_exc = raise_exc            # chat 即抛(测异常路径)
        self.calls: list[list] = []           # (hist, tools) 记录(进入即记)
        self.n = 0                            # 已成功返回的调用数

    async def chat(self, hist, tools=None):
        self.calls.append((hist, tools))      # 进入即记:阻塞中也可见(测试同步点)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.gate is not None and self.n >= self.gate_from:
            await self.gate.wait()            # 第 gate_from 次起阻塞至放行
        idx = self.n
        self.n += 1
        if idx < len(self.responses):
            resp = self.responses[idx]
        elif self.repeat_last and self.responses:
            resp = self.responses[-1]
        else:
            resp = _make_resp(content="兜底纯文本")
        if self.history:                      # llm 层副作用:request/response 落日志
            await self._session.append("llm.request",
                                 {"model": "mock-llm", "prompt_tokens": 1},
                                 actor="llm")
            payload = {"model": "mock-llm", "finish_reason": "stop"}
            if resp.tool_calls:
                payload["content"] = ""
                payload["tool_calls"] = [
                    {"id": c.id, "name": c.name, "arguments": "{}"}
                    for c in resp.tool_calls]
            else:
                payload["content"] = resp.content or "兜底文本"
            env = await self._session.append("llm.response", payload, actor="llm")
            resp.seq = env.seq
        return resp


class FakeTools:
    """工具执行替身:execute 落 tool.call + tool.result 事件(tools 层职责),
    可 gate(测取消传播);summaries = 逐次摘要脚本(空 = 恒 "ok")。"""

    def __init__(self, *, gate: asyncio.Event = None, summaries=None,
                 raise_exc: Exception = None, before: list = None) -> None:
        self.gate = gate
        self.summaries = list(summaries or [])
        self.raise_exc = raise_exc            # execute 抛(测引擎兜底)
        self.before = before or []            # execute 先跑(如置 started 标志)
        self.executed: list = []              # 已执行 call 记录

    def schemas_for(self, scope):
        return [{"type": "function", "function": {"name": "mock_tool"}}]

    async def execute(self, call, ctx):
        self.executed.append(call)
        for fn in self.before:
            r = fn()                      # 同步取值或启动协程
            if hasattr(r, "__await__"):
                await r
        await ctx.session.append("tool.call",
                           {"name": call.name, "args": {}, "raw_args": {},
                            "call_id": call.call_id}, actor="tool")
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.gate is not None:
            await self.gate.wait()
        i = len(self.executed) - 1
        summary = self.summaries[i] if i < len(self.summaries) else "ok"
        await ctx.session.append("tool.result",
                           {"name": call.name, "call_id": call.call_id,
                            "ok": True, "summary": summary, "truncated": False},
                           actor="tool")


class FakeScope:
    """scope 替身:window_tokens / check_budget / budget_state 三闸接口。"""

    def __init__(self, *, window_tokens=None, budget: str = "ok",
                 check_raise: Exception = None) -> None:
        self.window_tokens = window_tokens
        self.budget = budget                  # ok/warn/paused/exhausted
        self.check_raise = check_raise

    def check_budget(self):
        if self.check_raise is not None:
            raise self.check_raise

    def budget_state(self):
        return self.budget


class FakeCtx:
    """Agent(ctx)替身:聚合 session/llm/tools/scope + input_seq(loop 只认契约)。"""

    def __init__(self, session, llm, tools, scope=None, input_seq=0) -> None:
        self.session = session
        self.llm = llm
        self.tools = tools
        self.scope = scope or FakeScope()
        self.input_seq = input_seq
        llm._session = session                # llm 落日志副作用需要会话句柄


# ===================================================================== 装配
async def _session() -> SessionLog:
    """纯内存会话引导(首事件 session.created,seq=1;测试基线)。"""
    s = SessionLog(sid=SID)
    await s.append("session.created", {"title": "", "model": "mock-llm"},
                   actor="system")
    return s


async def _user_message(s: SessionLog, text: str) -> Envelope:
    """user.message 强同步落盘(外壳 submit 前置的替身)。"""
    return await s.append("user.message", {"content": text}, actor="user",
                          sync=True)


def _tail(s: SessionLog, type_: str) -> list[Envelope]:
    return [ev for ev in s.events_after() if ev.type == type_]


def _agent_texts(s: SessionLog) -> list[str]:
    return [ev.payload["content"] for ev in _tail(s, "agent.message")]


async def _wait_for(predicate, timeout_s: float = 5.0) -> None:
    """忙等谓词成立(测试同步点;asyncio 单线程,让步调度即可)。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError("等待超时")
        await asyncio.sleep(0)


# ===================================================================== 自然结束
async def test_single_turn_text_complete():
    """GWT-L1-01:纯文本 → llm.chat 恰 1 次、agent.message 落盘、回 idle、快照。"""
    s = await _session()
    llm = FakeLLM([_make_resp(content="你好,我是 mock")])
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    result = await loop.wake(env, ctx)

    assert isinstance(result, RunResult)
    assert result.reason == "complete"
    assert result.turns == 0                  # 纯文本终态轮不占工具轮计数
    assert len(result.run_id) == 32 and result.last_seq == s.stats()["seq"]
    assert llm.n == 1                         # 恰 1 次 LLM 调用
    assert _agent_texts(s) == ["你好,我是 mock"]
    # INV-01:上下文唯一来源 = 会话日志派生(首轮即含用户输入)
    assert any(m["role"] == "user" and m["content"] == "hi"
               for m in llm.calls[0][0])
    # 回 idle + current 清空 + 快照(外壳/审计只读句柄)
    assert loop.state == "idle" and loop.current is None
    snap = loop.state_snapshot()
    assert snap["state"] == "idle" and snap["current_run_id"] is None
    assert snap["turn"] == 0 and snap["queue_len"] == 0
    assert snap["last_reason"] == "complete"


async def test_text_reply_trace_parent_seq():
    """agent.message 携带父关联 trace.parent_seq = llm.response 的 seq。"""
    s = await _session()
    llm = FakeLLM([_make_resp(content="带 seq 的回复")])
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env = await _user_message(s, "hi")
    await loop.wake(env, ctx)
    msg = _tail(s, "agent.message")[0]
    resp = _tail(s, "llm.response")[0]
    assert msg.trace == {"parent_seq": resp.seq}


# ===================================================================== 轮数上限
async def test_max_turns_truncation_inv08():
    """GWT-L1-02:max_turns=3 恒 tool_calls → 恰 3 次 LLM、reason=max_turns、无第 4 次。

    循环必终止铁律(INV-08):任何输入在 max_turns 轮内结束,不存在无限工具轮。
    """
    s = await _session()
    tools = FakeTools(summaries=["r1", "r2", "r3"])   # 指纹逐轮变化,排除收敛闸
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()])], repeat_last=True)
    ctx = FakeCtx(s, llm, tools)
    loop = AgentLoop(max_turns=3)
    env = await _user_message(s, "一直调工具")

    result = await loop.wake(env, ctx)

    assert result.reason == "max_turns"
    assert result.turns == 3
    assert llm.n == 3                         # 恰好 3 次,无第 4 次
    assert len(tools.executed) == 3
    assert loop.state == "idle"
    # 超限终态不写 finished(归属 agent.close);无纯文本轮故无 agent.message
    assert not _tail(s, "session.finished")
    assert not _tail(s, "agent.message")


async def test_max_turns_default_is_30():
    """权威默认 30(PARAMETER-ANCHOR/spec F007:max_turns 默认 30,非 10)。"""
    loop = AgentLoop()
    assert loop.max_turns == 30
    assert loop.stall_limit == 3 and loop.queue_limit == 10


# ===================================================================== 收敛终止
async def test_stall_convergence_same_result():
    """恒返同一 tool.result → 连续 stall_limit=3 轮同指纹 → reason=stall(防空转)。"""
    s = await _session()
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()])], repeat_last=True)
    tools = FakeTools()                       # 恒 "ok" 摘要:指纹不变
    ctx = FakeCtx(s, llm, tools)
    loop = AgentLoop(max_turns=10, stall_limit=3)
    env = await _user_message(s, "空转")

    result = await loop.wake(env, ctx)

    # 同指纹:第2轮 streak=1、第3轮=2、第4轮=3 → stall(首轮为基准不算)
    assert result.reason == "stall"
    assert result.turns == 4 and llm.n == 4
    assert loop.state == "idle"


async def test_stall_limit_three_and_fingerprint_diff_no_stall():
    """收敛阈值默认 3;指纹逐轮不同(日志派生)则收敛闸永不触发,轮数闸兜底封顶。"""
    loop = AgentLoop()
    assert loop.stall_limit == 3
    s = await _session()
    # 40 个互不相同摘要:默认 max_turns=30 轮内指纹恒变 → 收敛不触发
    tools = FakeTools(summaries=[f"r{i}" for i in range(40)])
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()])], repeat_last=True)
    ctx = FakeCtx(s, llm, tools)
    env = await _user_message(s, "正常多轮")

    result = await loop.wake(env, ctx)

    assert result.reason == "max_turns"
    assert result.turns == 30 and llm.n == 30  # 30 轮封顶(默认值),非收敛
    assert loop.state == "idle"


# ===================================================================== 取消传播(F025)
async def test_cancel_during_tool_step():
    """GWT-L1-04 工具步中 cancel:system.cancelled 落盘、子任务取消、回 idle、无悬空。"""
    s = await _session()
    started = asyncio.Event()
    tool_gate = asyncio.Event()
    tools = FakeTools(gate=tool_gate, before=[started.set])
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()])])
    ctx = FakeCtx(s, llm, tools)
    loop = AgentLoop()
    env = await _user_message(s, "慢工具")
    run_task = asyncio.create_task(loop.wake(env, ctx))
    await started.wait()                      # 工具已进入执行(gate 内阻塞)

    await loop.cancel()                       # 声明式取消(置位+留痕+传播)

    # system.cancelled 已(强同步)落盘,what 指认 run
    cancels = _tail(s, "system.cancelled")
    assert len(cancels) == 1
    assert cancels[0].payload["reason"] == "cancelled"
    assert str(cancels[0].payload["what"]).startswith("run:")
    # 子任务已取消并清登记:无悬空协程
    assert loop._child_tasks == set()
    # 协作式取消:run 任务以 CancelledError 收尾(不吞),finally 回 idle
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert loop.state == "idle" and loop.current is None
    assert loop.state_snapshot()["current_run_id"] is None
    assert not _tail(s, "session.finished")   # finished 归属 agent.close


async def test_cancel_between_turns_gate2():
    """LLM 调用中 cancel(非子任务,协作式):置位后闸2 于下一轮起点触发
    → RunResult(reason=cancelled),run 正常返回(不抛 CancelledError)。"""
    s = await _session()
    gate = asyncio.Event()
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()]),
                   _make_resp(tool_calls=[_make_call()])],
                  gate=gate, gate_from=1)     # 第 1 次直放,第 2 次起阻塞
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop(max_turns=10)

    async def _drive():
        env = await _user_message(s, "两轮")
        return await loop.wake(env, ctx)

    task = asyncio.create_task(_drive())
    await _wait_for(lambda: len(llm.calls) >= 2)   # 第 2 次 chat 已阻塞在 gate
    await loop.cancel()                       # 第 2 轮 LLM 调用中取消(置位)
    gate.set()                                # 放行:本调用协作式跑完
    result = await task                       # 下轮起点闸2 → cancelled 终态

    assert result.reason == "cancelled"
    assert llm.n == 2                         # 第 2 次调用已执行完(协作式)
    assert len(_tail(s, "system.cancelled")) == 1
    assert loop.state == "idle"


async def test_cancel_idle_noop():
    """无在途 run 时 cancel = 无事可取消(幂等:无事件、不抛、状态不变)。"""
    s = await _session()
    loop = AgentLoop()
    loop._ctx = FakeCtx(s, FakeLLM(), FakeTools())
    await loop.cancel()
    assert loop.state == "idle" and loop.current is None
    assert not _tail(s, "system.cancelled")


async def test_cancel_stops_drain_keeps_pending():
    """取消后排水停止:队内剩余输入保留(不静默丢),回 idle 等下次唤醒。"""
    s = await _session()
    started = asyncio.Event()
    tool_gate = asyncio.Event()
    tools = FakeTools(gate=tool_gate, before=[started.set])
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()])])
    ctx = FakeCtx(s, llm, tools)
    loop = AgentLoop()
    env1 = await _user_message(s, "第一件")
    env2 = await _user_message(s, "第二件")

    async def _drive():
        return await loop.wake(env1, ctx)

    task = asyncio.create_task(_drive())
    await started.wait()                      # 第一件工具执行中
    await asyncio.sleep(0)
    assert await loop.wake(env2, ctx) is None  # running:第二件入队
    assert loop.state_snapshot()["queue_len"] == 1

    await loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert loop.state == "idle"
    snap = loop.state_snapshot()
    assert snap["queue_len"] == 1             # 队内输入保留,不静默丢
    assert loop.pending[0].seq == env2.seq    # FIFO 原序


# ===================================================================== 入队/拒新/互斥
async def test_wake_enqueue_fifo_drain():
    """running 期 wake 入队(FIFO);当前 run 结束自动取下一件,上下文与入队序一致。"""
    s = await _session()
    gate = asyncio.Event()
    llm = FakeLLM([_make_resp(content="A"), _make_resp(content="B")],
                  gate=gate)                  # 全阻塞:测试期间保持 running
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env1 = await _user_message(s, "第一件")
    env2 = await _user_message(s, "第二件")

    async def _drive():
        return await loop.wake(env1, ctx)

    task = asyncio.create_task(_drive())
    await _wait_for(lambda: len(llm.calls) >= 1)  # 第一件正阻塞在 LLM 调用
    assert await loop.wake(env2, ctx) is None  # running:入队即返(UI 事件驱动)
    snap = loop.state_snapshot()
    assert snap["queue_len"] == 1
    assert snap["pending_inputs"][0]["seq"] == env2.seq

    gate.set()                                # 放行:排水按 FIFO 消费两件
    result = await task
    assert result.reason == "complete"
    assert _agent_texts(s) == ["A", "B"]      # 上下文与入队序一致
    assert loop.state == "idle"
    assert loop.state_snapshot()["queue_len"] == 0


async def test_wake_queue_full_busy():
    """队满(>queue_limit)→ system.error[BUSY] 强同步 + BUSY 拒新,不静默丢。"""
    s = await _session()
    gate = asyncio.Event()
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()]),
                   _make_resp(content="完成1"),
                   _make_resp(content="完成2"),
                   _make_resp(content="完成3")],
                  gate=gate)
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop(queue_limit=2)
    envs = [await _user_message(s, f"第{i}件") for i in range(1, 5)]

    async def _drive():
        return await loop.wake(envs[0], ctx)

    task = asyncio.create_task(_drive())
    await _wait_for(lambda: len(llm.calls) >= 1)
    assert await loop.wake(envs[1], ctx) is None
    assert await loop.wake(envs[2], ctx) is None
    assert loop.state_snapshot()["queue_len"] == 2    # 队满

    with pytest.raises(PyHError) as ei:       # 第 4 条:显式拒新
        await loop.wake(envs[3], ctx)
    assert ei.value.code == "BUSY"
    errs = _tail(s, "system.error")
    assert len(errs) == 1 and errs[0].payload["code"] == "BUSY"
    assert loop.state_snapshot()["queue_len"] == 2    # 队深不超限

    gate.set()
    result = await task
    assert result.reason == "complete"
    # 消费序:第1件(工具轮+文本)→ 第2件 → 第3件;第4件被拒未进 run
    assert len(_tail(s, "user.message")) == 4
    assert _agent_texts(s) == ["完成1", "完成2", "完成3"]
    assert loop.state == "idle"


async def test_run_busy_when_state_not_idle():
    """非 idle 时并发 run(锁前置)→ BUSY(输入已在队内/拒新,等当前 run 结束)。"""
    s = await _session()
    gate = asyncio.Event()
    llm = FakeLLM([_make_resp(content="A")], gate=gate)
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    task = asyncio.create_task(loop.wake(env, ctx))
    await _wait_for(lambda: len(llm.calls) >= 1)
    with pytest.raises(PyHError) as ei:
        await loop.run(ctx)                   # 直接并发 run → BUSY
    assert ei.value.code == "BUSY"
    gate.set()
    await task
    assert loop.state == "idle"


async def test_wake_stopping_terminated_busy():
    """stopping/terminated 态 wake → BUSY 拒(会话正在关闭/已结束)。"""
    s = await _session()
    loop = AgentLoop()
    loop._ctx = FakeCtx(s, FakeLLM(), FakeTools())
    env = await _user_message(s, "hi")
    for bad in ("stopping", "terminated"):
        loop.state = bad
        with pytest.raises(PyHError) as ei:
            await loop.wake(env)
        assert ei.value.code == "BUSY"


# ===================================================================== LLM 异常降级路径
async def test_llm_chain_error_reason_error():
    """llm.chat 抛 LLM-310(链尾全败)→ system.error(LLM-310)留痕 → reason=error。"""
    s = await _session()
    llm = FakeLLM(raise_exc=PyHError("LLM-310", ctx={"detail": "主备模型全败"}))
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    result = await loop.wake(env, ctx)

    assert result.reason == "error"
    errs = _tail(s, "system.error")
    assert len(errs) == 1 and errs[0].payload["code"] == "LLM-310"
    assert "LLM-310" in errs[0].payload["hint"]
    assert loop.state == "idle"


async def test_unexpected_exception_cyc999():
    """未预期异常(非 PyHError)→ CYC-999 system.error + reason=error,run 不抛。"""
    s = await _session()
    llm = FakeLLM(raise_exc=RuntimeError("boom"))
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    result = await loop.wake(env, ctx)

    assert result.reason == "error"
    errs = _tail(s, "system.error")
    assert len(errs) == 1 and errs[0].payload["code"] == "CYC-999"
    assert loop.state == "idle"


async def test_tool_execute_escape_cyc999():
    """tools.execute 违反契约上抛(未事件化)→ 引擎兜底 CYC-999,不挂死会话。"""
    s = await _session()
    tools = FakeTools(raise_exc=RuntimeError("工具层崩溃"))
    llm = FakeLLM([_make_resp(tool_calls=[_make_call()])])
    ctx = FakeCtx(s, llm, tools)
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    result = await loop.wake(env, ctx)

    assert result.reason == "error"
    assert _tail(s, "system.error")[0].payload["code"] == "CYC-999"
    assert loop.state == "idle"


# ===================================================================== 预算闸(F032)
async def test_budget_exhausted_gate3():
    """闸3 预算:budget_state()=exhausted → reason=budget(强制终态,无再给机会)。"""
    s = await _session()
    llm = FakeLLM([_make_resp(content="不该到")])
    ctx = FakeCtx(s, llm, FakeTools(), scope=FakeScope(budget="exhausted"))
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    result = await loop.wake(env, ctx)

    assert result.reason == "budget"
    assert llm.n == 0                         # 闸前置:零 LLM 调用
    assert loop.state == "idle"


async def test_budget_exhausted_raised_by_check_budget():
    """scope.check_budget() 抛 BudgetExhausted → reason=budget(异常防御路径)。"""
    s = await _session()
    llm = FakeLLM([_make_resp(content="不该到")])
    ctx = FakeCtx(s, llm, FakeTools(),
                  scope=FakeScope(check_raise=BudgetExhausted("预算超限")))
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    result = await loop.wake(env, ctx)

    assert result.reason == "budget"
    assert loop.state == "idle"


# ===================================================================== turn/step 计数
async def test_tool_round_then_text_counts():
    """mock LLM 先 tool_call 后纯文本:工具轮计数 turns=1、单轮多工具步顺序执行、
    工具结果经会话日志进入后续上下文(INV-01)。"""
    s = await _session()
    tools = FakeTools()
    llm = FakeLLM([
        _make_resp(tool_calls=[_make_call(name="t_a", call_id="c1"),
                               _make_call(name="t_b", call_id="c2")]),  # 单轮 2 步
        _make_resp(content="任务完成"),
    ])
    ctx = FakeCtx(s, llm, tools)
    loop = AgentLoop(max_turns=5)
    env = await _user_message(s, "查两个东西")

    result = await loop.wake(env, ctx)

    assert result.reason == "complete"
    assert llm.n == 2 and result.turns == 1   # 1 个工具轮(含 2 步)
    assert [c.name for c in tools.executed] == ["t_a", "t_b"]   # 顺序执行
    assert [r.payload["name"] for r in _tail(s, "tool.result")] == ["t_a", "t_b"]
    # 指纹 = (name, summary, ok) 元组序列,派生自会话日志(INV-01 无第二份状态)
    fp = loop._outcome_fingerprint(ctx, 0)
    assert fp == (("t_a", "ok", True), ("t_b", "ok", True))
    assert _agent_texts(s) == ["任务完成"]
    # 第 2 轮上下文含第 1 轮工具结果(历史 = 日志折叠)
    hist2 = llm.calls[1][0]
    assert any(m.get("role") == "tool" for m in hist2)


async def test_must_stop_three_gates_readonly():
    """_must_stop 三闸各自独立触发且纯只读(不写事件、不改 run)。"""
    s = await _session()
    ctx = FakeCtx(s, FakeLLM(), FakeTools())
    loop = AgentLoop(max_turns=3)
    run = RunContext(run_id=uuid.uuid4().hex, input_seq=1)
    run.turn = 3                              # 闸1 轮数
    assert loop._must_stop(ctx, run) == "max_turns"
    run.turn = 2
    run.cancelled = True                      # 闸2 取消(声明式置位)
    assert loop._must_stop(ctx, run) == "cancelled"
    run.cancelled = False
    assert loop._must_stop(ctx, run) is None
    ctx.scope.budget = "paused"               # 闸3 预算(paused 亦触发)
    assert loop._must_stop(ctx, run) == "budget"
    ctx.scope.budget = "ok"
    assert loop._must_stop(ctx, run) is None
    # 只读:判定后 run 状态与日志零变化
    assert run.reason is None and run.turn == 2
    assert not _tail(s, "system.error") and not _tail(s, "system.cancelled")


async def test_empty_content_no_tools_llm399():
    """llm 返回双空响应(契约破坏)→ LLM-399 → reason=error(模型域未预期)。"""
    s = await _session()
    llm = FakeLLM([_make_resp(content="", tool_calls=None)],
                  history=False)              # 载荷非法,llm 层本应拦截不落日志
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env = await _user_message(s, "hi")

    result = await loop.wake(env, ctx)

    assert result.reason == "error"
    assert _tail(s, "system.error")[0].payload["code"] == "LLM-399"
    assert loop.state == "idle"


async def test_llm_chat_only_entry_point_inv02():
    """INV-02 行为证据:llm.chat 每次调用都携带会话派生历史;跨输入上下文连续,
    不存在第二份消息源(run_turn 之外无任何 llm.chat 调用路径)。"""
    s = await _session()
    llm = FakeLLM([_make_resp(content="hi"), _make_resp(content="再次")])
    ctx = FakeCtx(s, llm, FakeTools())
    loop = AgentLoop()
    env1 = await _user_message(s, "一")
    await loop.wake(env1, ctx)
    env2 = await _user_message(s, "二")
    await loop.wake(env2, ctx)

    assert len(llm.calls) == 2
    users0 = [m["content"] for m in llm.calls[0][0] if m["role"] == "user"]
    users1 = [m["content"] for m in llm.calls[1][0] if m["role"] == "user"]
    assert users0 == ["一"]                   # 首轮上下文只含自己的输入
    assert users1 == ["一", "二"]             # 次轮含全部输入(日志折叠,INV-01)
    assert any(m["role"] == "assistant" for m in llm.calls[1][0])  # 上轮回复入上下文
