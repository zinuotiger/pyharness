"""多会话隔离对抗(e2e,R8):共享一条总线时必须**互不串场**。

真实装配(真 bus/真 store/真 SessionLog);每条断言背后是"两会话同时活着"的场景。
覆盖:落盘不串写 · 事件/任务不串场 · 订阅按属主隔离 · 关闭 A 不影响 B 的在途任务 ·
租户归属按**信封**判定且过滤判据两端一致。

修复前实测(R8-1):engine 落盘订阅**缺 sid 过滤** ⇒ A 的 JSONL 里出现 B 的
``session.created``(回放错序 + repair 误判空洞)。桌面壳的同款订阅一直有该过滤
——同一横切关注点两处实现、漏一处;现统一到 ``persistence.session_recorder``。
"""
from __future__ import annotations

import asyncio
import pathlib


from pyharness.bus import EventBus
from pyharness.config import load_settings
from pyharness.core.task_queue import TaskQueue
from pyharness.engine import build_spine


class _Runner:
    """按会话写自己的事件(可 gate 造慢任务,可 fail 造失败任务)。"""

    def __init__(self, session, *, gate=None, fail: bool = False) -> None:
        self.session = session
        self.gate = gate
        self.fail = fail
        self.calls: list[str] = []

    async def run_for_task(self, task):
        self.calls.append(task.id)
        if self.gate is not None:
            await self.gate.wait()
        await self.session.append(
            "agent.message", {"content": f"{self.session.sid}:{task.id}",
                              "model": "m"}, actor="agent", task_id=task.id)
        if self.fail:
            from pyharness.errors import PyHError
            raise PyHError("LLM-310", ctx={"hint": "injected"})
        return f"ok:{task.id}"

    async def cancel_current(self, task_id):        # noqa: ARG002
        return None


async def _mk(tmp_path, sid, bus, *, tenant_id=None, **kw):
    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "index.db")
    cfg.llm.fallback_models = []                   # 聚焦隔离,不起探针
    spine = await build_spine(cfg, sid=sid, sessions_dir=tmp_path / "sessions",
                              bus=bus, tenant_id=tenant_id)
    await spine.session.append("session.created", {"title": sid, "model": "m"},
                               actor="system", sync=True)
    runner = _Runner(spine.session, **kw)
    q = TaskQueue(spine.session, runner=runner)
    spine.task_queue = q
    return spine, q, runner


def _subs(bus, owner_part: str) -> int:
    rows = [s for lst in bus._by_type.values() for s in lst] + list(bus._wild)
    """按属主**子串**计数(属主形如 <role>:{tenant}:{sid},隔离维度可变)。"""
    return sum(1 for s in rows if owner_part in s.owner)


async def test_shared_bus_does_not_cross_write_session_logs(tmp_path):
    """**落盘不串写**:A/B 各自的 JSONL 只含本会话事件(R8-1 回归锚)。"""
    bus = EventBus()
    a, _, _ = await _mk(tmp_path, "s-isoa-0001", bus)
    b, _, _ = await _mk(tmp_path, "s-isob-0002", bus)
    await a.session.append("user.message", {"content": "from-A"}, actor="user",
                           sync=True)
    await b.session.append("user.message", {"content": "from-B"}, actor="user",
                           sync=True)
    try:
        fa = (tmp_path / "sessions" / "s-isoa-0001.jsonl").read_text(encoding="utf-8")
        fb = (tmp_path / "sessions" / "s-isob-0002.jsonl").read_text(encoding="utf-8")
        assert "s-isob-0002" not in fa, "A 的日志被写入了 B 的事件"
        assert "s-isoa-0001" not in fb, "B 的日志被写入了 A 的事件"
        assert fa.count('"session_id":"s-isoa-0001"') == 2, fa
        assert fb.count('"session_id":"s-isob-0002"') == 2, fb
    finally:
        await a.close()
        await b.close()


async def test_concurrent_tasks_and_close_do_not_cross_sessions(tmp_path):
    """**并发 + 关闭隔离**:并发任务各归其主;关闭 A 不摘 B 的订阅、不取消 B 的在途任务。"""
    bus = EventBus()
    a, qa, _ = await _mk(tmp_path, "s-conc-a", bus)
    b, qb, _ = await _mk(tmp_path, "s-conc-b", bus)
    gate = asyncio.Event()
    bs, qbs, _ = await _mk(tmp_path, "s-conc-b2", bus, gate=gate)
    try:
        # ① 并发跑:A/B 各两条任务
        ta = [await qa.submit(f"A{i}") for i in (1, 2)]
        tb = [await qb.submit(f"B{i}") for i in (1, 2)]
        res_a = await asyncio.wait_for(qa.wait_for(ta[0]), timeout=5)
        res_b = await asyncio.gather(*[asyncio.wait_for(qb.wait_for(t), timeout=5)
                                       for t in tb])
        assert res_a.ok and all(r.ok for r in res_b)

        def _answers(sp, other_sid):
            rows = [e.payload.get("content") for e in sp.session.events_after(0)
                    if e.type == "agent.message"]
            return rows, any(other_sid in str(x) for x in rows)

        rows_a, leak_a = _answers(a, "s-conc-b")
        rows_b, leak_b = _answers(b, "s-conc-a")
        assert not leak_a and not leak_b, (rows_a, rows_b)
        assert rows_a == ["s-conc-a:t-1", "s-conc-a:t-2"]
        assert rows_b == ["s-conc-b:t-1", "s-conc-b:t-2"]

        # ② 订阅按属主隔离;关闭 A 不误伤 B
        before_b = _subs(bus, "s-conc-b2")
        assert _subs(bus, "s-conc-a") > 0 and before_b > 0
        tb2 = await qbs.submit("B2-slow")
        waiter = asyncio.create_task(qbs.wait_for(tb2))
        for _ in range(300):
            if qbs.status().running == tb2:
                break
            await asyncio.sleep(0.01)
        await a.close()
        assert _subs(bus, "s-conc-a") == 0
        assert _subs(bus, "s-conc-b2") == before_b, "关闭 A 摘掉了 B 的订阅"
        assert qbs.status().running == tb2, "关闭 A 取消了 B 的在途任务"
        gate.set()
        r = await asyncio.wait_for(waiter, timeout=5)
        assert r.ok, "A 关闭后 B 的慢任务仍应完成"
    finally:
        await b.close()
        await bs.close()


async def test_tenant_attribution_is_per_envelope_and_fail_closed(tmp_path):
    """**租户不串场**:事件租户取自**自己的信封**;未知归属对非 default 客户端拒收。"""
    from pyharness.core.tenant_settings import (event_tenant_allowed,
                                                event_tenant_of)
    bus = EventBus()
    a, _, _ = await _mk(tmp_path, "s-tta-000001", bus, tenant_id="alpha")
    b, _, _ = await _mk(tmp_path, "s-ttb-000002", bus, tenant_id="beta")
    try:
        for env in a.session.events_after(0):
            assert env.tenant_id == "alpha"
        for env in b.session.events_after(0):
            assert env.tenant_id == "beta"
        env_a = list(a.session.events_after(0))[-1]
        env_b = list(b.session.events_after(0))[-1]
        assert event_tenant_of(env_a) == "alpha" and event_tenant_of(env_b) == "beta"
        # 过滤判据两端一致:同租户可收,异租户/未知拒收
        assert event_tenant_allowed("alpha", event_tenant_of(env_a)) is True
        assert event_tenant_allowed("beta", event_tenant_of(env_a)) is False
        assert event_tenant_allowed("beta", "") is False     # 未知 ⇒ fail-closed
        assert event_tenant_allowed("default", "") is True
    finally:
        await a.close()
        await b.close()


async def test_workspaces_are_distinct_and_exist(tmp_path):
    """**工作区不串场**:两会话根不同且都已创建(R1 的 F055 派生在多会话下成立)。"""
    bus = EventBus()
    a, _, _ = await _mk(tmp_path, "s-wsa-000001", bus)
    b, _, _ = await _mk(tmp_path, "s-wsb-000002", bus)
    try:
        ra = pathlib.Path(a.scope.policy.workspace_root)
        rb = pathlib.Path(b.scope.policy.workspace_root)
        assert ra != rb and ra.is_dir() and rb.is_dir()
        assert ra.name == "s-wsa-000001" and rb.name == "s-wsb-000002"
        assert ra.parent == rb.parent == tmp_path / "workspaces"
    finally:
        await a.close()
        await b.close()


# ============ R12:task_id 绑定的**并发二阶面**(修复自身不得引入串场)
async def test_task_id_binding_is_serial_and_never_bleeds(e2e_factory):
    """同一会话任务串行 ⇒ `ctx.task_id` 在任一时刻只属于**当前**任务。

    R11-6 把 `ctx.task_id` 设为按任务边界的**共享可变状态**(在途绑定、结束还原),
    本用例用"LLM 阻塞在途"把窗口固定住来验证:在途绑 t-1、结束即还原(不留残留,
    否则下一任务/闲聊会串到旧任务)。
    """
    import asyncio as _aio
    from types import SimpleNamespace as _SN

    from pyharness.core import llm as llm_mod

    s = await e2e_factory(None, sid="s-tid-000001")
    model = s.ctx.settings.llm.model
    saved = dict(llm_mod.adapters)
    gate = _aio.Event()

    class _Gated:
        def __init__(self) -> None:
            self.model = model

        async def chat(self, messages, tools=None, *, ctx):
            await gate.wait()                      # 停在"任务在途"窗口内
            usage = _SN(prompt_tokens=1, completion_tokens=1,
                        prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1)
            await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                       counters=getattr(ctx, "counters", None))
            resp = llm_mod.LLMResponse(content="done", tool_calls=None,
                                       usage=usage, model=self.model,
                                       finish_reason="stop")
            env = await ctx.session.append(
                "llm.response",
                {"model": self.model, "finish_reason": "stop",
                 "content": resp.content, "tool_calls": []}, actor="llm")
            resp.seq = env.seq
            return resp

        async def chat_stream(self, messages, tools=None, *, ctx):
            return await self.chat(messages, tools, ctx=ctx)

        async def ping(self):
            return 0.01

    llm_mod.adapters.clear()
    s.ctx.engine_spine.llm_runtime.registry[model] = _Gated()
    try:
        await s.boot()
        run_task = _aio.create_task(s.run("hold"))
        for _ in range(300):                        # 等进入在途窗口
            ag = s.ctx.engine_spine.active_agents.get(s.ctx.session.sid)
            if ag is not None and getattr(ag.ctx, "task_id", None):
                break
            await _aio.sleep(0.01)
        ag = s.ctx.engine_spine.active_agents.get(s.ctx.session.sid)
        assert ag is not None
        assert ag.ctx.task_id == s.last_task_id, \
            f"在途任务应绑定自己的 task_id,实际 {ag.ctx.task_id!r}"
        assert getattr(s.ctx, "task_id", None) in (None, ""), \
            "task_id 只能落在 agent ctx(不得污染外壳 ctx)"

        gate.set()
        assert (await _aio.wait_for(run_task, timeout=8)).ok
        assert getattr(ag.ctx, "task_id", None) in (None, ""), \
            "任务结束后必须还原(否则后续任务/闲聊会串到旧任务)"
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


# ============ R12-2:同 sid 跨租户时,落盘订阅属主必须**含租户**(否则互摘)
async def test_persistence_owner_is_tenant_scoped_on_shared_bus(tmp_path):
    """两租户用**同名 sid**(会话目录按租户分目录 ⇒ 合法)时,属主不得相同。

    修复前:`owner = f"persistence:{sid}"` ⇒ 两租户同名 sid **同属主**;
    删除/关闭其一即 `unsubscribe_all(owner)` ⇒ 摘掉**另一个租户**的落盘订阅
    (其会话从此静默不落盘)。这是 R8-4/R4-2b 的同类(属主未含隔离维度)。
    """

    from pyharness.desktop.sessions import DesktopSessionManager

    bus = EventBus()
    sid = "s-shared-ten-01"
    mgrs = []
    for tenant in ("alpha", "beta"):
        d = tmp_path / "tenants" / tenant / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        m = DesktopSessionManager(dir=d, bus=bus, config=None, tenant_id=tenant)
        mgrs.append(m)
    a, b = mgrs
    try:
        # 两租户各自"打开"同名 sid(直建文件以走 open 路径)
        for m in (a, b):
            (m.dir / f"{sid}.jsonl").write_text("", encoding="utf-8")
        await a.open_session(sid)
        await b.open_session(sid)
        owners = {s.owner for lst in bus._by_type.values() for s in lst}
        assert len([o for o in owners if o.startswith("persistence")]) == 2, \
            f"两租户同名 sid 的落盘属主必须可区分,实际 {sorted(owners)}"

        before_b = sum(1 for lst in bus._by_type.values() for s in lst
                       if s.owner == b._owners[sid])
        # 关 A(不删文件):不得摘掉 B 的订阅
        await a.shutdown_all()
        after_b = sum(1 for lst in bus._by_type.values() for s in lst
                      if s.owner == b._owners[sid])
        assert after_b == before_b and after_b > 0, "关闭 A 摘掉了 B 的落盘订阅"
    finally:
        await b.shutdown_all()
