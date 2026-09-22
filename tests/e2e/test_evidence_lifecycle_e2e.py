"""E2E:GAP-08R + N3 —— 证据生命周期(段终态完备性)。

**冻结规则 R-E1**:一个**已关闭**的任务段,若段内存在 ≥1 条 ``decision.issued``,
则**必须恰有一条** ``evidence.archived`` 引用该段锚 —— 与终态类型无关。

修复前实测:``segment.end`` 5/5 落盘,``evidence.archived`` 仅 3/5
(异常/预算/取消三种终态为 0)。根因:生产者挂在 ``run_for_task`` 的**函数体**上,
而该函数跑在独立子任务里,三种终态下不执行到收尾。

本文件覆盖五种终态。前四种用**结构性注入**(在 ``loop.wake`` 的收尾处抛对应异常),
第五种用**真实 ``task_queue.cancel()``** —— 不做"结构性模拟即宣称真实路径"。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pyharness.core import llm as llm_mod
from pyharness.core.scope import BudgetExhausted
from pyharness.errors import PyHError


class _OneToolAdapter:
    """轮 1 调一个**会被治理**的工具(产生 decision + receipt);轮 2 收尾。"""

    def __init__(self, model: str) -> None:
        self.model = model
        self.n = 0

    async def chat(self, messages, tools=None, *, ctx):
        self.n += 1
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=1)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        if self.n == 1:
            tc = llm_mod.ToolCall(id="k1", index=0, name="fs.list_dir",
                                  raw_args={"path": "."}, raw_json='{"path":"."}')
            resp = llm_mod.LLMResponse(content="", tool_calls=[tc], usage=usage,
                                       model=self.model,
                                       finish_reason="tool_calls")
        else:
            resp = llm_mod.LLMResponse(content="done", tool_calls=None,
                                       usage=usage, model=self.model,
                                       finish_reason="stop")
        env = await ctx.session.append(
            "llm.response",
            {"model": self.model, "finish_reason": resp.finish_reason,
             "content": resp.content or "",
             "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.raw_json}
                            for c in (resp.tool_calls or [])]}, actor="llm")
        resp.seq = env.seq
        return resp

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self) -> float:
        return 0.01


class _TextOnlyAdapter(_OneToolAdapter):
    """永不发 tool_calls ⇒ 段内无治理决策(供"空段不归档"负向用)。"""

    async def chat(self, messages, tools=None, *, ctx):
        self.n += 1
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=1)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        resp = llm_mod.LLMResponse(content="hi", tool_calls=None,
                                   usage=usage, model=self.model,
                                   finish_reason="stop")
        env = await ctx.session.append(
            "llm.response",
            {"model": self.model, "finish_reason": "stop",
             "content": "hi", "tool_calls": []}, actor="llm")
        resp.seq = env.seq
        return resp


class _BlockingAdapter(_OneToolAdapter):
    """轮 1 调工具;**轮 2 永挂起** —— 供真实 cancel 用例制造在途窗口。"""

    async def chat(self, messages, tools=None, *, ctx):
        if self.n >= 1:
            self.n += 1
            await asyncio.sleep(3600)           # 挂起,等外部 cancel
        return await super().chat(messages, tools, ctx=ctx)


def _counts(session, ) -> dict:
    ev = list(session.events_after(0))
    t = [e.type for e in ev]
    return {
        "decision.issued": t.count("decision.issued"),
        "receipt.emitted": t.count("receipt.emitted"),
        "segment.end": t.count("segment.end"),
        "evidence.archived": t.count("evidence.archived"),
    }


async def _make(e2e_factory, sid, adapter_cls):
    saved = dict(llm_mod.adapters)
    s = await e2e_factory(None, sid=sid, channel="desktop")
    llm_mod.adapters.clear()
    llm_mod.adapters[s.ctx.settings.llm.model] = adapter_cls(
        s.ctx.settings.llm.model)
    return s, saved


async def _run(s, mode):
    """驱动一次 run;按 mode 制造对应终态。返回 pump 结局字符串。"""
    spine = s.ctx.engine_spine
    real = spine.loop.wake

    if mode in ("exception", "budget", "cancellation"):
        exc = {"exception": PyHError("LLM-310", ctx={}),
               "budget": BudgetExhausted("hard limit"),
               "cancellation": None}[mode]

        async def w(env, ctx=None, **kw):
            try:
                await real(env, ctx=ctx, **kw)
            finally:
                if exc is not None:
                    raise exc
                raise asyncio.CancelledError()
        spine.loop.wake = w
    elif mode == "abnormal":
        async def w(env, ctx=None, **kw):
            await real(env, ctx=ctx, **kw)
            return SimpleNamespace(reason="stall")
        spine.loop.wake = w

    await s.ctx.session.append("user.message", {"content": "do"},
                               actor="user", sync=True)
    from pyharness.core.task_queue import TaskQueue
    q = TaskQueue(s.ctx.session, runner=s.ctx.task_runner, max_queue=8)
    tid = await q.submit("do")
    try:
        res = await asyncio.wait_for(q.wait_for(tid), timeout=25)
        return f"ok={res.ok} code={getattr(res, 'code', None)}"
    except asyncio.CancelledError:
        return "CancelledError"
    except BudgetExhausted:
        return "BudgetExhausted"
    except Exception as e:                       # noqa: BLE001 结算观测
        return f"{type(e).__name__}:{str(e)[:50]}"


# ================================================= 五终态
@pytest.mark.parametrize("mode", ["success", "abnormal", "exception",
                                  "budget", "cancellation"])
async def test_every_terminal_state_archives_exactly_one_evidence(
        e2e_factory, mode):
    """**R-E1 回归锚**:五种终态下 决策=1 · 段关=1 · 证据=1 · 重复=0。"""
    s, saved = await _make(e2e_factory, f"s-e2e-ev-{mode[:6]}", _OneToolAdapter)
    try:
        await s.boot()
        outcome = await _run(s, mode)
        await asyncio.sleep(0.3)
        c = _counts(s.ctx.session)
        assert c["decision.issued"] == 1, f"{mode}({outcome}): {c}"
        assert c["segment.end"] == 1, f"{mode}({outcome}): 段未关闭 {c}"
        assert c["evidence.archived"] == 1, \
            f"{mode}({outcome}): 应恰一条证据 —— 修复前此处为 0;{c}"
        # 幂等:不产生重复工件
        from pyharness.engine import archive_task_evidence
        again = await archive_task_evidence(s.ctx.engine_spine, s.last_task_id)
        assert again is None, f"{mode}: 重复归档应被幂等闸拦下"
        assert _counts(s.ctx.session)["evidence.archived"] == 1
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


async def test_evidence_records_terminal_state_in_claim(e2e_factory):
    """工件**标注终态**(N3 要求):claim 内含 ``终态=`` 标量。"""
    s, saved = await _make(e2e_factory, "s-e2e-ev-claim", _OneToolAdapter)
    try:
        await s.boot()
        await _run(s, "success")
        await asyncio.sleep(0.3)
        arch = s.of("evidence.archived")
        assert arch, "应有证据工件"
        claim = arch[0].payload["claim"]
        assert "终态=" in claim, f"claim 应标注终态:{claim}"
        assert "success" in claim, claim
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


async def test_evidence_producer_runs_after_segment_close(e2e_factory):
    """**N3 回归锚**:工件在段关闭**之后**产生(seq 序),不再早于段关闭。"""
    s, saved = await _make(e2e_factory, "s-e2e-ev-order", _OneToolAdapter)
    try:
        await s.boot()
        await _run(s, "success")
        await asyncio.sleep(0.3)
        ev = {e.type: e.seq for e in s.ctx.session.events_after(0)
              if e.type in ("evidence.archived", "segment.end")}
        assert ev.get("evidence.archived") is not None, ev
        assert ev.get("segment.end") is not None, ev
        assert ev["evidence.archived"] > ev["segment.end"], \
            f"证据应在段关闭之后产生:{ev}"
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


async def test_segment_without_decisions_archives_nothing(e2e_factory):
    """负向:段内**无**治理决策 ⇒ 不归档(不为空段造工件)。"""
    s, saved = await _make(e2e_factory, "s-e2e-ev-none", _TextOnlyAdapter)
    try:
        await s.boot()
        assert (await s.run("say hi")).ok
        await asyncio.sleep(0.2)
        assert s.count("decision.issued") == 0
        assert s.count("evidence.archived") == 0
        assert s.count("segment.end") >= 1, "段仍应正常关闭"
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


# ================================================= 真实取消路径
async def test_real_task_queue_cancel_still_archives(e2e_factory):
    """**真实** ``task_queue.cancel()``(非结构性注入)下仍产出证据。

    做法:适配器第 2 轮永挂起,任务在途时调 ``queue.cancel(tid)``。
    """
    s, saved = await _make(e2e_factory, "s-e2e-ev-realcancel", _BlockingAdapter)
    try:
        await s.boot()
        await s.ctx.session.append("user.message", {"content": "do"},
                                   actor="user", sync=True)
        from pyharness.core.task_queue import TaskQueue
        q = TaskQueue(s.ctx.session, runner=s.ctx.task_runner, max_queue=8)
        tid = await q.submit("do")
        waited = 0.0
        while waited < 10.0 and not s.of("tool.result"):
            await asyncio.sleep(0.1)
            waited += 0.1
        assert s.of("tool.result"), "首轮工具应已执行(产生决策)"
        assert await q.cancel(tid, by="system") is True, "取消应被受理"
        try:
            await asyncio.wait_for(q.wait_for(tid), timeout=15)
        except Exception:                        # noqa: BLE001 取消终态结算
            pass
        await asyncio.sleep(0.4)
        c = _counts(s.ctx.session)
        assert c["decision.issued"] >= 1, c
        assert c["segment.end"] == 1, f"段应关闭:{c}"
        assert c["evidence.archived"] == 1, \
            f"真实取消下也应恰一条证据(修复前为 0):{c}"
        assert "cancelled" in s.of("evidence.archived")[0].payload["claim"], \
            "claim 应标注 cancelled 终态"
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)
