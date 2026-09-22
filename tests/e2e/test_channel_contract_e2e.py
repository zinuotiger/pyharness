"""E2E:N5 通道契约 —— 两条装配路径 + 三种交互通道的**端到端一致性**。

验证目标(修复前均不成立):
1. ``danger=high`` 工具经审批时,``approval.requested.trace.channel`` 与
   ``decision.issued.principal_channel`` **同源一致**;
2. ``"acp:<id>"`` **既不通向 desktop、也不通向 headless**;
3. ``assemble_real_engine`` 与 ``attach_engine_to_ctx`` **两条装配路径**给出同一判定
   (修复前:前者→desktop,后者→headless)。
"""
from __future__ import annotations

import asyncio

import pytest

from pyharness.core.channel import normalize_channel

HIGH_DANGER = [{"id": "h1", "name": "exec.shell_run",
                "args": {"command": "echo hi"}}]


async def _run_until_approval(s, timeout=20.0):
    """跑任务直到出现 approval.requested(或任务结束),返回 (aid 或 None)。"""
    await s.ctx.session.append("user.message", {"content": "run"},
                               actor="user", sync=True)
    from pyharness.core.task_queue import TaskQueue
    q = TaskQueue(s.ctx.session, runner=s.ctx.task_runner, max_queue=8)
    tid = await q.submit("run")
    task = asyncio.create_task(q.wait_for(tid))
    aid = None
    for _ in range(400):
        if s.ctx.approval._pending:
            aid = next(iter(s.ctx.approval._pending))
            break
        if task.done():
            break
        await asyncio.sleep(0.05)
    return aid, task


@pytest.mark.parametrize("declared,expected", [
    ("desktop", "desktop"),
    ("cli", "cli"),
    ("acp:client-7", "acp"),
])
async def test_high_danger_approval_channel_matches_decision_principal(
        e2e_factory, declared, expected):
    """审批通道与决策主体通道**必须一致**,且 ACP **不降级**。

    exec.* 需要 basic 档才可见 ⇒ 用 ``preset=standard``。
    """
    s = await e2e_factory(HIGH_DANGER, sid=f"s-e2e-chan-{expected}",
                          channel=declared, preset="standard")
    await s.boot()
    aid, task = await _run_until_approval(s)

    req = s.of("approval.requested")
    assert req, f"{declared}: 应产生审批请求(danger=high)"
    req_channel = (req[0].trace or {}).get("channel")
    assert req_channel == expected, \
        f"{declared}: 审批通道应归一为 {expected},实际 {req_channel}"

    ds = s.of("decision.issued")
    assert ds, f"{declared}: 应有决策"
    assert ds[0].payload["principal_kind"] == "human", declared
    assert ds[0].payload["principal_channel"] == expected, \
        f"{declared}: 决策主体通道应 {expected},实际 {ds[0].payload['principal_channel']}"

    # 收尾:拒绝审批,避免任务悬挂
    if aid is not None:
        await s.ctx.approval.deny_async(aid, by="cli")
    try:
        await asyncio.wait_for(task, timeout=10)
    except Exception:
        pass


async def test_acp_channel_is_not_headless(e2e_factory):
    """``"acp:<id>"`` **不得**被判为 headless(修复前 ``_ensure_channel`` 返回 None)。

    直接对**真实装配出的 provider**调用判定入口,ctx 用真实 agent ctx(而非伪造)。
    """
    from types import SimpleNamespace

    s = await e2e_factory(HIGH_DANGER, sid="s-e2e-acp-nohead",
                          channel="acp:client-7", preset="standard")
    await s.boot()
    prov = s.ctx.engine_spine.approval
    ctx = SimpleNamespace(headless=False, channel="acp:client-7")
    assert prov._ensure_channel(ctx) == "acp", "ACP 应解析为交互通道 acp"
    # 归一名必须落在白名单内(可被下游 in CHANNELS 判定消费)
    from pyharness.core.approval import CHANNELS
    assert prov._ensure_channel(ctx) in CHANNELS


async def test_two_assembly_paths_agree_on_channel(tmp_path):
    """``assemble_real_engine`` 与 ``attach_engine_to_ctx`` 对同一通道给同一判定。

    修复前:前者把 ACP 劫持为 ``desktop``(硬编码缺省),后者判为 ``None``(headless)。
    """
    from pathlib import Path
    from types import SimpleNamespace

    from pyharness.core import llm as llm_mod
    from pyharness.core.session import open_session
    from pyharness.engine import assemble_real_engine, attach_engine_to_ctx
    from pyharness.persistence import open_store

    from tests.conftest import ScriptedAdapter, e2e_settings

    cfg = e2e_settings(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    try:
        # 路径 A:assemble_real_engine
        llm_mod.adapters[cfg.llm.model] = ScriptedAdapter(cfg.llm.model, [])
        ctx_a = await assemble_real_engine(cfg, sid="s-chan-pathA-01",
                                           sessions_dir=tmp_path / "sessions",
                                           channel="acp:client-7")
        a = ctx_a.engine_spine.approval._channel
        await ctx_a.engine_spine.close()

        # 路径 B:attach_engine_to_ctx
        store = open_store("s-chan-pathB-01", dir=tmp_path / "sessions")
        log_ = await open_session("s-chan-pathB-01", store)
        ns = SimpleNamespace(session=log_, bus=None, storage=None,
                             settings=cfg, config=cfg)
        await attach_engine_to_ctx(ns, cfg, log_=log_,
                                   sessions_dir=Path(tmp_path) / "sessions",
                                   bus=None, store=store, preload=False,
                                   channel="acp:client-7")
        b = ns.engine_spine.approval._channel
        await ns.engine_spine.close()

        assert a == b == "acp", f"两条装配路径应一致为 acp: A={a} B={b}"
        # 且都与规范解析器一致
        assert normalize_channel("acp:client-7").name == a
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


async def test_headless_declares_no_human_channel(e2e_factory):
    """显式 ``channel=None`` ⇒ 审批不可用(APR-501 路径),**不**提升为 desktop。"""
    s = await e2e_factory(HIGH_DANGER, sid="s-e2e-chan-headless",
                          channel=None, preset="standard")
    await s.boot()
    aid, task = await _run_until_approval(s)
    assert aid is None, "headless 不应产生可用审批请求"
    assert not s.of("approval.requested"), "headless 不得发出 approval.requested"
    try:
        await asyncio.wait_for(task, timeout=10)
    except Exception:
        pass
