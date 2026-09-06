"""subagent 模块单测 — 契约:specs/subagent.py.md(F049)+ EVENT-SCHEMA §3.5.3
(subagent.spawned/joined/failed)+ PARAMETER-ANCHOR(并发 ≤8/深度 ≤3/预算 1/4/摘要 2KB)
+ DIS-SEAM §2.4(enter→announce→detach 生命周期与回滚)

覆盖面:
    派发:F049 spawn → 父日志 subagent.spawned(sub_id/parent_seq=发起轮锚/task)+
        返回 sub_id;父会话只记委派三事件(不掺子会话事件)
    独立 session:子 SessionLog 自 seq=1 起/自 session.created/事件不落父日志
    (F044 段不嵌套);子 workspace 根独立(F055)/子策略快照继承且 deny 只收窄
    摘要回传:joined summary ≤2KB(截断带 …)、join 返回同一摘要;摘要以数据身份
        入主会话(tool 数据,非 system 指令:父派生历史不含其文本)
    失败:PyHError → subagent.failed(摘要含 [码])+ join 上抛原码;未装配执行器
        CYC-999 快速失败;notify=False 不写回写事件
    并发上限:满 → BUSY 拒新、终态释放闸、status.running 计数
    深度上限:>3 → BUSY;层级不递增 → BUSY(平级/降级拒)
    预算继承:子 limits = 父 × spec.budget_ratio(默认 1/4);ratio > 配置上限/≤0
        → EVT-100;deny_extra 并入子策略
    child-first:detach/_on_session_closing/close_children 先杀在途子任务并落终态
        审计,无孤儿、无闸泄漏;cancel 幂等 False / 已终态 False
    join:成功摘要 / 超时 None / 未知 sub_id EVT-101 / 二次 join EVT-101
    status:running/active_children{sub_id,state,age_s}/limit 纯读快照
    生命周期:enter 幂等+事件类型登记;announce 五步(tool/订阅/mount/留痕)+幂等+
        中途失败回滚;detach 幂等 + 可重入;spawn 工具 Provider 适配器(spawn+join)

执行器/scope/tools 全为注入替身——本模块不触 agent_loop/llm/真实持久化;时间相关
断言一律用事件 gate/终态等待驱动,不用裸 sleep 碰运气。
"""
import asyncio
import types
from typing import Any

import pytest

from pyharness.bus.event_bus import EventBus
from pyharness.core.scope import (BudgetLimits, ScopePolicy, ScopeSnapshot)
from pyharness.core.session import SessionLog
from pyharness.core.subagent import (CHILD_PREFIX, MAX_DEPTH, SUMMARY_MAX_CHARS,
                                     TOOL_NAME, SubagentManager, SubagentSpec,
                                     SubagentSpawnProvider, summarize)
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError

SID = "s-submain1"   # Envelope.session_id 需 ≥8 字符

DEEP = "deepseek-chat"


# ===================================================================== 替身
class Store:
    """SessionStore 替身:记录强同步 flush(seq)——观测 created/finished 先落盘。"""

    def __init__(self) -> None:
        self.flushed: list[int] = []

    def replay(self):
        return iter(())

    async def flush(self, seq: int) -> None:
        self.flushed.append(seq)


class SuccessRunner:
    """立即成功执行器:向子会话写 agent.message(模拟 F007 子循环产物)再返回输出。

    out: run_child 返回的原始输出(dict/str/…);log_events=False 时不写子事件。
    """

    def __init__(self, out: Any = "列目录完成:3 层 47 项", log_events: bool = True):
        self.out = out
        self.log_events = log_events
        self.envs: list = []                 # 记录子执行上下文(独立会话断言)

    async def run_child(self, env):
        self.envs.append(env)
        if self.log_events:
            await env.session.append(
                "agent.message",
                {"content": f"[{env.sub_id}] ok", "model": "mock"},
                actor="agent")
        return self.out


class GatedRunner:
    """门控执行器:run_child 先 await gate.wait()(长任务模拟;可被协作取消)。

    started 事件标记"首个子任务已进入执行";exc 在放行后注入故障;子事件在
    gate 后写——取消落在 gate 内 ⇒ 子会话只有 created,证明"先杀光"生效。
    """

    def __init__(self, gate: asyncio.Event = None, out: str = "gated-ok",
                 exc: Exception = None) -> None:
        self.gate = gate if gate is not None else asyncio.Event()
        self.out = out
        self.exc = exc
        self.started = asyncio.Event()
        self.envs: list = []

    async def run_child(self, env):
        self.envs.append(env)
        self.started.set()
        await self.gate.wait()               # 阻塞至放行/取消
        if self.exc is not None:
            raise self.exc
        return self.out


class FailRunner:
    """故障执行器:run_child 恒定抛注入异常(结构化失败/未预期两路)。"""

    def __init__(self, err: Exception) -> None:
        self.err = err

    async def run_child(self, env):
        raise self.err


class FakeTools:
    """spawn 侧工具注册表替身:只提供 has()(tools_subset 在册校验消费面)。"""

    def __init__(self, names=()):
        self._names = set(names)

    def has(self, name: str) -> bool:
        return name in self._names


def _fake_scope(limits: BudgetLimits = None,
                policy: ScopePolicy = None) -> types.SimpleNamespace:
    """父 scope 替身:limits(BudgetLimits)+ snapshot()(ScopeSnapshot 值拷贝源)。

    子 scope 工厂(specs 默认实现)经 snapshot().policy 拷贝父策略并独立收紧,
    workspace 根按子 id 独立(F055)——此处用真实 scope 数据类构造快照。
    """
    lim = limits or BudgetLimits(max_in_tokens=2_000_000,
                                 max_out_tokens=50_000,
                                 max_cost_yuan=1.0, warn_ratio=0.8)
    pol = policy or ScopePolicy(role="user", deny_tools=set(),
                                danger_marks=[], allowed_domains=set(),
                                workspace_root="C:/Users/LENOVO/.pyharness/"
                                              "workspaces/s-submain1",
                                sandbox_level="strict")
    snap = ScopeSnapshot(policy=pol, limits=lim, window_tokens=65536,
                         window_ratio=0.75, taken_at="2026-09-07T00:00:00Z")
    return types.SimpleNamespace(limits=lim, snapshot=lambda: snap, policy=pol)


def _ctx(**kw) -> types.SimpleNamespace:
    """父上下文替身(鸭子):scope/tools/bus/agent/round_seq 均可缺省。"""
    base = dict(scope=None, tools=None, bus=None, round_seq=None,
                agent=None, loop=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


async def _parent(store: Store = None) -> SessionLog:
    """内存父会话(created seq=1 + user.message seq=2,发起轮锚真实存在)。"""
    s = SessionLog(sid=SID, persistence=store)
    await s.append("session.created", {"title": "", "model": DEEP},
                   actor="system")
    await s.append("user.message", {"content": "整理桌面文件"}, actor="user")
    return s


def _events(s) -> list:
    """会话全量事件(seq 升序)。"""
    return list(s.events_after(0))


def _types(s) -> list:
    """父会话事件类型序列(断言"只记委派三事件"用)。"""
    return [e.type for e in _events(s)]


def _find(s, type_: str) -> list:
    """按类型过滤事件列表。"""
    return [e for e in _events(s) if e.type == type_]


async def _spawn_ok(mgr: SubagentManager, task: str = "列目录",
                    depth: int = 1, **kw) -> str:
    """便捷 spawn(默认成功执行器场景参数),返回 sub_id。"""
    spec = SubagentSpec(task=task, depth=depth, **kw)
    return await mgr.spawn(spec, _ctx())


async def _drain_children(mgr: SubagentManager, reason: str = "test") -> None:
    """清理门控子任务(child-first;防 pytest 悬挂任务告警)。"""
    await mgr.close_children(reason=reason)


def _busy(ei) -> None:
    """BUSY 断言(ei = pytest.raises 上下文):字面量码,不经 raise_code 兜底。"""
    assert ei.value.code == "BUSY"


# ===================================================================== 派发
async def test_spawn_writes_spawned_and_returns_sub_id():
    """F049 验收直译:spawn → 父日志 subagent.spawned(sub_id/parent_seq=发起轮锚/
    task),返回 s-subxxxx sub_id;子任务在独立 session 上跑,父日志只有委派事实。"""
    sess = await _parent()
    runner = SuccessRunner()
    mgr = SubagentManager(session=sess, runner=runner, parent_scope=_fake_scope())
    ctx = _ctx(round_seq=2, tools=FakeTools(names=["read_file"]))
    spec = SubagentSpec(task="把 D:/work 按类型归档", depth=1,
                        tools_subset=["read_file"])
    sub_id = await mgr.spawn(spec, ctx)
    assert sub_id.startswith(CHILD_PREFIX) and len(sub_id) >= 8

    spawned = _find(sess, "subagent.spawned")
    assert len(spawned) == 1
    p = spawned[0].payload
    assert p["sub_id"] == sub_id
    assert p["parent_seq"] == 2              # 发起轮锚 = user.message 所在轮
    assert p["task"] == "把 D:/work 按类型归档"
    assert spawned[0].actor == "agent"       # 触发者=agent(EVENT-SCHEMA §3.5.3)

    # 父会话只记委派/回收三事件 + 会话基础事件(不掺子会话运行事件)
    summary = await mgr.join(sub_id)
    assert summary == runner.out
    assert _types(sess) == ["session.created", "user.message",
                            "subagent.spawned", "subagent.joined"]


async def test_child_runs_in_independent_session():
    """F044/F049:子任务跑独立 session——子日志自 seq=1(created 首事件)、事件不落
    父日志;父会话文件只记委派事实;子执行器拿到独立 ChildContext。"""
    sess = await _parent()
    runner = SuccessRunner()
    mgr = SubagentManager(session=sess, runner=runner)
    sub_id = await _spawn_ok(mgr)
    summary = await mgr.join(sub_id)

    child = runner.envs[0].session
    assert child is not sess and child.sid == sub_id
    ce = _events(child)
    assert ce[0].type == "session.created" and ce[0].seq == 1
    assert [e.type for e in ce] == ["session.created", "agent.message"]
    # 父会话事件不含子运行事件(agent.message 只存在于子日志)
    assert "agent.message" not in _types(sess)
    assert summary == runner.out


async def test_spawn_parent_anchor_fallback_to_loop_input_seq():
    """发起轮锚解析:ctx.round_seq → 主循环 run.input_seq → 父会话当前 seq。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    ctx = _ctx(loop=types.SimpleNamespace(current=types.SimpleNamespace(
        input_seq=2)))
    sub_id = await mgr.spawn(SubagentSpec(task="t", depth=1), ctx)
    assert _find(sess, "subagent.spawned")[0].payload["parent_seq"] == 2
    await mgr.join(sub_id)


async def test_joined_carries_trace_parent_seq_anchored_to_spawned():
    """EVENT-SCHEMA §2.4/§3.5.3:joined 的 trace.parent_seq = spawned 事件 seq
    (语义父锚);sub_id 同值互补。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    sub_id = await _spawn_ok(mgr)
    await mgr.join(sub_id)
    spawned = _find(sess, "subagent.spawned")[0]
    joined = _find(sess, "subagent.joined")[0]
    assert joined.trace == {"parent_seq": spawned.seq}
    assert joined.payload["sub_id"] == sub_id


async def test_summary_capped_2kb_and_data_identity():
    """摘要 ≤2KB:超长截断补 …;join 返回的摘要即 joined payload 的 summary;
    joined 文本以数据身份入日志,派生历史不出现为 system 指令。"""
    sess = await _parent()
    long_out = "行" * 5000
    mgr = SubagentManager(session=sess, runner=SuccessRunner(out=long_out))
    sub_id = await _spawn_ok(mgr)
    summary = await mgr.join(sub_id)
    assert len(summary) == SUMMARY_MAX_CHARS and summary.endswith("…")
    joined = _find(sess, "subagent.joined")[0]
    assert joined.payload["summary"] == summary

    # 数据身份:摘要不在派生历史的 system/assistant 文本里(reducer 不映射
    # subagent.* 事件;LLM 可见路径 = spawn 工具的 tool.result,非第二指令源)
    hist = sess.derive_messages()
    assert all("行" not in str(m.get("content", "")) for m in hist)
    assert [m["role"] for m in hist] == ["user"]     # 只有 user.message 入派生


async def test_summary_renders_dict_output_and_empty_placeholder():
    """摘要化:dict 输出 JSON 序列化;空输出给占位(joined payload summary 必填)。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess,
                          runner=SuccessRunner(out={"moved": 42, "dirs": 3}))
    sub_id = await _spawn_ok(mgr)
    summary = await mgr.join(sub_id)
    assert '"moved":42' in summary and '"dirs":3' in summary

    mgr2 = SubagentManager(session=sess, runner=SuccessRunner(out=None))
    sub2 = await _spawn_ok(mgr2)
    assert (await mgr2.join(sub2)) == "…"


async def test_spawn_spec_validation_evt100():
    """派发前校验:空 task / budget_ratio 越界(≤0 或 >config 上限)→ EVT-100。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner(),
                          budget_ratio=0.25)
    for bad in (SubagentSpec(task="   ", depth=1),
                SubagentSpec(task="t", depth=1, budget_ratio=0.0),
                SubagentSpec(task="t", depth=1, budget_ratio=-0.1),
                SubagentSpec(task="t", depth=1, budget_ratio=0.5),
                SubagentSpec(task="t", depth=1, tools_subset=["ok", 1])):
        with pytest.raises(PyHError) as ei:
            await mgr.spawn(bad, _ctx(tools=FakeTools(names=["ok"])))
        assert ei.value.code == "EVT-100"
    assert mgr.status().running == 0          # 零残留(无闸泄漏/无脏登记)


async def test_spawn_after_parent_finished_evt104_no_residue():
    """父会话已 finished 后派发 → EVT-104 拒(session.append 校验链),闸归还零残留,
    父日志无 spawned。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    await sess.append("session.finished", {"reason": "idle"}, actor="system")
    with pytest.raises(PyHError) as ei:
        await mgr.spawn(SubagentSpec(task="t", depth=1), _ctx())
    assert ei.value.code == "EVT-104"
    assert mgr.status().running == 0 and mgr._active == 0
    assert not _find(sess, "subagent.spawned")


# ===================================================================== 失败回收
async def test_child_failure_writes_failed_and_join_raises_original():
    """子任务结构化失败:父日志 subagent.failed(摘要含 [码]);join 上抛原码
    (回喂可行动文本);闸释放。"""
    sess = await _parent()
    err = PyHError("LLM-310", ctx={"hint": "链尾全败,降级链耗尽"})
    mgr = SubagentManager(session=sess, runner=FailRunner(err))
    sub_id = await _spawn_ok(mgr)
    with pytest.raises(PyHError) as ei:
        await mgr.join(sub_id)
    assert ei.value.code == "LLM-310"
    failed = _find(sess, "subagent.failed")
    assert len(failed) == 1
    assert failed[0].payload["sub_id"] == sub_id
    assert failed[0].payload["summary"].startswith("[LLM-310]")
    assert failed[0].trace == {"parent_seq": _find(sess, "subagent.spawned")[0].seq}
    assert mgr.status().running == 0          # 终态释放并发闸


async def test_runner_missing_quick_fail_cyc999():
    """未装配子执行器(runner=None)→ 子任务 CYC-999 快速失败(防 S-1 伪造已执行),
    落 subagent.failed;join 上抛 CYC-999;不悬挂。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess)       # 无 runner
    sub_id = await _spawn_ok(mgr)
    with pytest.raises(PyHError) as ei:
        await mgr.join(sub_id)
    assert ei.value.code == "CYC-999"
    failed = _find(sess, "subagent.failed")
    assert len(failed) == 1 and "CYC-999" in failed[0].payload["summary"]
    assert mgr.status().running == 0


async def test_unexpected_child_exception_wrapped_cyc999():
    """子任务未预期异常兜底:归 CYC-999(failed 摘要含码),join 上抛。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess,
                          runner=FailRunner(RuntimeError("boom")))
    sub_id = await _spawn_ok(mgr)
    with pytest.raises(PyHError) as ei:
        await mgr.join(sub_id)
    assert ei.value.code == "CYC-999"
    assert _find(sess, "subagent.failed")[0].payload["summary"].startswith(
        "[CYC-999]")


async def test_notify_false_skips_join_failed_events():
    """notify=False:成功/失败都不回写 joined/failed(父日志只有 spawned);
    摘要仍可经 join 取回,失败仍上抛原码。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    sub_id = await _spawn_ok(mgr, notify=False)
    summary = await mgr.join(sub_id)
    assert summary == "列目录完成:3 层 47 项"
    assert _types(sess) == ["session.created", "user.message",
                            "subagent.spawned"]

    sess2 = await _parent()
    err = PyHError("LLM-304", ctx={"hint": "业务性失败"})
    mgr2 = SubagentManager(session=sess2, runner=FailRunner(err))
    sub2 = await _spawn_ok(mgr2, notify=False)
    with pytest.raises(PyHError) as ei:
        await mgr2.join(sub2)
    assert ei.value.code == "LLM-304"
    assert "subagent.failed" not in _types(sess2)


# ===================================================================== join
async def test_join_timeout_returns_none_child_keeps_running():
    """join 超时 → None(调用方决定再等/取消);子任务仍在跑不受牵连。"""
    sess = await _parent()
    gate = asyncio.Event()
    mgr = SubagentManager(session=sess, runner=GatedRunner(gate=gate))
    sub_id = await _spawn_ok(mgr)
    try:
        assert await mgr.join(sub_id, timeout=0.05) is None
        assert mgr.status().running == 1      # 子仍在途(状态 joining/running)
    finally:
        assert await mgr.cancel(sub_id) is True   # 清理(取消传播 F025)
        await _drain_children(mgr)


async def test_join_unknown_and_double_join_evt101():
    """sub_id 不存在/已回收 → EVT-101;结果只可 join 一次(spawn 返回值才是合法
    sub_id)——成功 join 后句柄已摘(只登记在途,INV-01),二次 join 报 EVT-101。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    with pytest.raises(PyHError) as ei:
        await mgr.join("s-sub9999")
    assert ei.value.code == "EVT-101"
    sub_id = await _spawn_ok(mgr)
    assert (await mgr.join(sub_id)) == "列目录完成:3 层 47 项"
    with pytest.raises(PyHError) as ei:
        await mgr.join(sub_id)
    assert ei.value.code == "EVT-101"         # 已回收(终态即摘)


# ===================================================================== 并发上限
async def test_concurrency_limit_busy_and_slot_release():
    """并发 ≤8(F049 边界):满 → BUSY 拒新(不排队);终态(取消/完成)释放闸后可
    再派;status.running 与在途一致。"""
    sess = await _parent()
    gate = asyncio.Event()
    mgr = SubagentManager(session=sess, runner=GatedRunner(gate=gate),
                          max_concurrent=2)
    ids = []
    for i in range(2):
        ids.append(await _spawn_ok(mgr, task=f"并行任务{i}"))
    with pytest.raises(PyHError) as ei:
        await _spawn_ok(mgr, task="第三个")
    _busy(ei)
    st = mgr.status()
    assert st.running == 2 and st.limit == 2
    assert {c["state"] for c in st.active_children} == {"running"}

    # 取消一个 → 闸释放(计数/在途同步下降),可再派
    assert await mgr.cancel(ids[0]) is True
    assert mgr.status().running == 1
    ids.append(await _spawn_ok(mgr, task="补位"))
    assert mgr.status().running == 2
    with pytest.raises(PyHError) as ei:
        await _spawn_ok(mgr, task="第四个")
    _busy(ei)
    # child-first 收尾:全部取消,闸清零,无孤儿
    await _drain_children(mgr)
    assert mgr.status().running == 0 and mgr._active == 0


async def test_default_limit_is_8():
    """默认并发上限 = 8(PARAMETER-ANCHOR 锁死),status.limit 反映。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    assert mgr.status().limit == 8


# ===================================================================== 深度上限
async def test_depth_gate_busy():
    """递归深度 ≤3:depth > 3 → BUSY;层级必须逐层 +1(平级/降级 → BUSY)。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner(), depth=0)
    for bad in (4, 99):
        with pytest.raises(PyHError) as ei:
            await mgr.spawn(SubagentSpec(task="t", depth=bad), _ctx())
        _busy(ei)
        assert "深度" in ei.value.ctx.get("hint", "")
    # 平级(0)/降级(-1)→ BUSY(递归闸递增链,防绕过)
    for bad in (0, -1):
        with pytest.raises(PyHError) as ei:
            await mgr.spawn(SubagentSpec(task="t", depth=bad), _ctx())
        _busy(ei)
    # 三层内合法
    sub = await mgr.spawn(SubagentSpec(task="t", depth=3), _ctx())
    assert (await mgr.join(sub)) == "列目录完成:3 层 47 项"


async def test_depth_chain_from_deeper_manager():
    """子 manager(depth=2)只可派 depth=3(链 ≤3);depth=2 平级被拒。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner(), depth=2)
    with pytest.raises(PyHError) as ei:
        await mgr.spawn(SubagentSpec(task="t", depth=2), _ctx())
    _busy(ei)
    sub = await mgr.spawn(SubagentSpec(task="孙任务", depth=3), _ctx())
    assert (await mgr.join(sub)) == "列目录完成:3 层 47 项"
    # depth 上限常量(锁死 3)
    assert MAX_DEPTH == 3


# ===================================================================== 预算继承
async def test_budget_inheritance_quarter_and_scope_snapshot():
    """F032:子预算 = 父 ×1/4(默认 ratio 0.25,下限 1);子 scope 继承父策略快照
    (deny/allowed_domains 值拷贝)且 workspace 根独立(F055)、deny_extra 只收窄。"""
    sess = await _parent()
    parent_scope = _fake_scope()
    mgr = SubagentManager(session=sess, runner=SuccessRunner(),
                          parent_scope=parent_scope)
    sub_id = await _spawn_ok(mgr, deny_extra=["fs.delete_file"])
    h = mgr._children[sub_id]
    # 预算 1/4:2_000_000/50_000/1.0 → 500_000/12_500/0.25
    assert h.budget_limits.max_in_tokens == 500_000
    assert h.budget_limits.max_out_tokens == 12_500
    assert abs(h.budget_limits.max_cost_yuan - 0.25) < 1e-9
    # 子 scope:策略快照继承 + deny 只收窄(无父级担保通道)
    child_scope = h.scope
    assert child_scope.limits.max_in_tokens == 500_000
    assert "fs.delete_file" in child_scope.policy.deny_tools   # deny_extra 并入
    assert child_scope.policy.allowed_domains == parent_scope.policy.allowed_domains
    assert sub_id in child_scope.policy.workspace_root          # F055 独立根
    assert child_scope.session_id == sub_id
    await mgr.join(sub_id)


async def test_budget_ratio_scaling_uses_spec_ratio():
    """子预算 = 父 × spec.budget_ratio(非恒 1/4):ratio=0.125 → 八分之一。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner(),
                          parent_scope=_fake_scope())
    sub_id = await _spawn_ok(mgr, budget_ratio=0.125)
    h = mgr._children[sub_id]
    assert h.budget_limits.max_in_tokens == 250_000
    assert h.budget_limits.max_out_tokens == 6_250
    await mgr.join(sub_id)


# ===================================================================== 取消(F025)
async def test_cancel_semantics_and_audit():
    """cancel:在途子任务 → True + system.cancelled 审计(what=subagent:id);子任务
    真正被取消(子日志无完成事件);终态/不存在二次 cancel → 幂等 False。"""
    sess = await _parent()
    gate = asyncio.Event()
    runner = GatedRunner(gate=gate)
    mgr = SubagentManager(session=sess, runner=runner)
    sub_id = await _spawn_ok(mgr)
    child_sess = mgr._children[sub_id].session    # 提前捕获子日志(取消后句柄已摘)
    await runner.started.wait()                   # 子已进入 gate(确定性同步点)
    assert await mgr.cancel(sub_id, reason="user-cancel") is True
    cancelled = _find(sess, "system.cancelled")
    assert len(cancelled) == 1
    assert cancelled[0].payload["what"] == f"subagent:{sub_id}"
    assert cancelled[0].payload["reason"] == "user-cancel"
    # 取消于 gate 内 ⇒ 子日志只有 created(无完成事件 = 先杀光,非跑完)
    assert [e.type for e in _events(child_sess)] == ["session.created"]
    assert mgr.status().running == 0 and mgr._active == 0
    # 幂等:已终态/不存在 → False
    assert await mgr.cancel(sub_id) is False
    assert await mgr.cancel("s-sub0000") is False


async def test_cancel_releases_slot_and_child_not_completed():
    """取消后闸释放(计数=0)且子任务未跑完(门控内被协作取消,无完成副作用)。"""
    sess = await _parent()
    gate = asyncio.Event()
    runner = GatedRunner(gate=gate)
    mgr = SubagentManager(session=sess, runner=runner)
    sub_id = await _spawn_ok(mgr)
    await runner.started.wait()              # 子已进入执行(gate 内)
    assert await mgr.cancel(sub_id) is True
    assert mgr._active == 0
    # 释放后可再派(闸正确归还)
    sub2 = await _spawn_ok(mgr)              # 会阻塞在 gate;清理掉
    await _drain_children(mgr, reason="cleanup")
    assert mgr.status().running == 0 and mgr._active == 0


# ===================================================================== child-first
async def test_session_closing_child_first_no_orphans():
    """父会话关闭钩子(_on_session_closing):先杀光在途子任务(cancel + 终态审计)
    → await 退出;摘净后无孤儿、无闸泄漏、子任务未跑完(子日志只有 created)。"""
    sess = await _parent()
    gate = asyncio.Event()
    runner = GatedRunner(gate=gate)
    mgr = SubagentManager(session=sess, runner=runner)
    ids = [await _spawn_ok(mgr, task=f"关前任务{i}") for i in range(3)]
    child_logs = {i: mgr._children[i].session for i in ids}   # 取消前捕获
    await runner.started.wait()              # 至少一个子已进入 gate
    await mgr._on_session_closing("session.closing", None)
    assert mgr.status().running == 0 and mgr._active == 0
    assert not mgr._children                 # 在途表摘净
    audits = _find(sess, "system.cancelled")
    assert {a.payload["what"] for a in audits} == \
        {f"subagent:{i}" for i in ids}       # 每条在途子任务都有取消审计
    # 事件序:spawned → cancelled 审计(终态先于父 finished 落盘)
    types_ = _types(sess)
    assert types_.count("subagent.spawned") == 3
    # 子任务真正被杀:门控内取消,无完成事件(父无 joined)
    assert "subagent.joined" not in types_
    for log_ in child_logs.values():
        assert [e.type for e in _events(log_)] == ["session.created"]


async def test_detach_is_child_first_and_idempotent():
    """detach:先杀在途子任务(终态审计)→ 摘订阅/注销工具/unmount;重复 detach
    幂等;子无孤儿。"""
    sess = await _parent()
    gate = asyncio.Event()
    runner = GatedRunner(gate=gate)
    bus = EventBus()
    tools = ToolRegistry(bus=bus)
    agent_ns = types.SimpleNamespace()
    ctx = _ctx(bus=bus, tools=tools, agent=agent_ns)
    mgr = SubagentManager(session=sess, runner=runner)
    await mgr.enter(ctx)
    await mgr.announce(ctx)
    ids = [await _spawn_ok(mgr) for _ in range(2)]
    await runner.started.wait()
    await mgr.detach(ctx)
    # 工具/订阅/mount 全部摘除
    assert not tools.has(TOOL_NAME)
    assert not hasattr(agent_ns, "subagent")
    assert not bus._by_type.get("session.closing")
    # child-first:全部取消并落审计;闸/在途清零
    assert mgr.status().running == 0 and mgr._active == 0
    assert len(_find(sess, "system.cancelled")) == 2
    # 幂等
    await mgr.detach(ctx)
    assert mgr._entered is False and not mgr._announced


# ===================================================================== 生命周期
def _cap_stack():
    """能力生命周期测试装配:独立 EventBus + ToolRegistry + agent 命名空间。"""
    bus = EventBus()
    tools = ToolRegistry(bus=bus)
    agent_ns = types.SimpleNamespace()
    ctx = _ctx(bus=bus, tools=tools, agent=agent_ns)
    return ctx, bus, tools, agent_ns


async def test_enter_announce_detach_full_lifecycle():
    """DIS-SEAM §2.4:enter(登记 subagent.* 类型)→ announce(工具入注册表 +
    session.closing 订阅 + mount ctx.agent.subagent + registry.updated)→ detach
    逆序摘除;enter/announce/detach 均幂等,detach 后可重入。"""
    sess = await _parent()
    ctx, bus, tools, agent_ns = _cap_stack()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    await mgr.enter(ctx)
    assert mgr._entered
    for t in ("subagent.spawned", "subagent.joined", "subagent.failed"):
        assert t in bus._types                # 事件类型 schema 已登记(防 EVT-102)
    await mgr.enter(ctx)                      # 幂等:重复 enter 静默通过
    assert tools.count() == 0

    await mgr.announce(ctx)
    assert tools.has(TOOL_NAME)               # LLM 可见工具已注册
    prov = tools.lookup_provider(TOOL_NAME)
    assert isinstance(prov, SubagentSpawnProvider)
    assert mgr.definition().danger == "none"  # 派发本身不越权(子任务内动作仍过 guard)
    assert mgr.definition().approval == "never"
    subs = bus._by_type.get("session.closing", [])
    assert any(s.owner == "cap:subagent" for s in subs)
    assert agent_ns.subagent is mgr           # locator.mount
    await mgr.announce(ctx)                   # announce 幂等
    assert tools.count() == 1

    await mgr.detach(ctx)
    assert not tools.has(TOOL_NAME)
    assert not bus._by_type.get("session.closing")
    assert not hasattr(agent_ns, "subagent")
    await mgr.detach(ctx)                     # detach 幂等
    # detach 后可重入(detached → enter → announce 状态机闭合)
    await mgr.enter(ctx)
    await mgr.announce(ctx)
    assert tools.has(TOOL_NAME)
    await mgr.detach(ctx)


async def test_enter_failure_stays_detached_no_announce():
    """DIS-SEAM GWT:enter 抛错(register_type 撞 EVT-102)→ 回 detached 不 announce
    (状态保持 detached;工具/订阅均未挂)。"""
    ctx, bus, _, _ = _cap_stack()
    for t in ("subagent.spawned", "subagent.joined", "subagent.failed"):
        bus.register_type(t, None)            # 预占:再登记 → EVT-102
    mgr = SubagentManager(session=None)
    # 弱引用去重表里没有该 bus → 走 register_type → 撞 EVT-102
    with pytest.raises(PyHError) as ei:
        await mgr.enter(ctx)
    assert ei.value.code == "EVT-102"
    assert mgr._entered is False              # 回 detached(不 announce 的前置态)
    assert not mgr._announced
    # 未修复前重试仍失败(弱引用表只记成功登记;状态保持 detached,修复后可入)
    with pytest.raises(PyHError) as ei:
        await mgr.enter(ctx)
    assert ei.value.code == "EVT-102"
    assert mgr._entered is False


async def test_announce_mid_failure_rolls_back_registered_tool():
    """DIS-SEAM §2.4:announce 中途失败(订阅步抛错)→ 回滚已做步骤(工具注销),
    回 detached,无残留订阅。"""
    sess = await _parent()

    class FakeBusRaisingSub:
        """register_type 正常、subscribe 恒抛(注入 announce 第②步失败)。"""

        def __init__(self):
            self.types = set()

        def register_type(self, t, model=None):
            self.types.add(t)

        def subscribe(self, pattern, handler, *, owner="anonymous", when=None):
            raise RuntimeError("subscribe boom")

    bus = FakeBusRaisingSub()
    tools = ToolRegistry()                    # 真实注册表(工具登记可回滚)
    ctx = _ctx(bus=bus, tools=tools, agent=None)
    mgr = SubagentManager(session=sess)
    await mgr.enter(ctx)
    with pytest.raises(RuntimeError):
        await mgr.announce(ctx)
    assert not tools.has(TOOL_NAME)           # 工具已回滚注销(无残留注册)
    assert mgr._entered is False              # 回 detached
    assert not mgr._announced


async def test_announce_tool_conflict_raises_tlb801():
    """工具名冲突(重名)→ announce 抛 TLB-801,回 detached,无订阅/无 mount。"""
    ctx, bus, tools, agent_ns = _cap_stack()
    from pyharness.core.subagent import SubagentManager as SM
    mgr = SM(session=None)
    tools.register_tool(mgr.definition(),     # 预占 subagent.spawn(重名冲突)
                        provider=object())
    with pytest.raises(PyHError) as ei:
        await mgr.announce(ctx)
    assert ei.value.code == "TLB-801"
    assert mgr._entered is False
    assert not bus._by_type.get("session.closing")
    assert not hasattr(agent_ns, "subagent")


# ===================================================================== 工具 Provider
async def test_spawn_tool_provider_handle_full_roundtrip():
    """subagent.spawn 工具 Provider:handle = spawn + join,返回 ≤2KB 摘要(LLM
    一次工具调用即派发并回收);子任务失败经 join 上抛原码。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    provider = SubagentSpawnProvider(mgr)
    summary = await provider.handle({"task": "归档 D:/work", "depth": 1},
                                    _ctx(round_seq=2))
    assert summary == "列目录完成:3 层 47 项"
    spawned = _find(sess, "subagent.spawned")[0]
    assert spawned.payload["task"] == "归档 D:/work"
    assert _find(sess, "subagent.joined")[0].payload["summary"] == summary

    sess2 = await _parent()
    err = PyHError("TLB-802", ctx={"tool": "ghost_tool"})
    mgr2 = SubagentManager(session=sess2, runner=FailRunner(err))
    with pytest.raises(PyHError) as ei:
        await SubagentSpawnProvider(mgr2).handle(
            {"task": "t", "depth": 1}, _ctx())
    assert ei.value.code == "TLB-802"         # join 上抛原码(executor 落 tool.error)


async def test_spawn_provider_tools_subset_gate():
    """Provider 组装 spec 时 tools_subset 过在册闸:幻觉工具名 → TLB-802。"""
    sess = await _parent()
    mgr = SubagentManager(session=sess, runner=SuccessRunner())
    with pytest.raises(PyHError) as ei:
        await mgr.spawn(SubagentSpec(task="t", depth=1,
                                     tools_subset=["ghost_tool"]),
                        _ctx(tools=FakeTools(names=["read_file"])))
    assert ei.value.code == "TLB-802"
    assert mgr.status().running == 0          # 闸前拒,零占用


# ===================================================================== status
async def test_status_snapshot_fields():
    """status:running/active_children{sub_id,state,age_s}/limit 纯读快照。"""
    sess = await _parent()
    gate = asyncio.Event()
    mgr = SubagentManager(session=sess, runner=GatedRunner(gate=gate),
                          max_concurrent=8)
    assert mgr.status().running == 0 and mgr.status().active_children == []
    sub_id = await _spawn_ok(mgr)
    st = mgr.status()
    assert st.running == 1 and st.limit == 8
    info = st.active_children[0]
    assert info["sub_id"] == sub_id and info["state"] == "running"
    assert info["age_s"] >= 0
    await mgr.cancel(sub_id)
    assert mgr.status().running == 0


# ===================================================================== 摘要化纯函数
def test_summarize_unit():
    """摘要化纯函数:截断带记号 / JSON 序列化 / 空占位。"""
    assert len(summarize("a" * 3000)) == SUMMARY_MAX_CHARS
    assert summarize("a" * 3000).endswith("…")
    assert summarize("短文本") == "短文本"
    assert '"k":1' in summarize({"k": 1})
    assert summarize(None) == "…"
    assert summarize("") == "…"
    assert summarize(["a", "b"]) == '["a","b"]'
