"""engine 装配层测试 — 契约:engine.py 装配语义(desktop/CLI 共用脊柱)。

历史背景:engine.py 0% 覆盖——装配层没有直接测试,而"装配即真链"的接线点
全在这里(F010 sysprompt / F032 counters / F039 spill / F058 compactor /
F027 streaming / F013 fallback chain)。本文件锁定这些装配不变量,防回归。

不变量(装配即正确):
- spine 组件完备:counters/sysprompt/compactor 必在岗;streaming 读 cfg.loop
- 工具面:fs.* 4 + storage.spill 读工具注册,strict 下 schemas_for 可见
- spill 激活:首次 _agent_ctx_of 建 agent 时激活 provider(幂等),storage.spill
  挂载后 executor 关4 可用
- assemble_real_engine:ctx 形状含 counters,storage 与 spine.storage 同对象
"""
from __future__ import annotations

import asyncio

import pytest

from pyharness.config import Settings, load_settings
from pyharness.engine import (_activate_storage_caps, _agent_ctx_of,
                              assemble_real_engine, build_spine)


def _cfg(tmp_path) -> Settings:
    """临时存储根 Settings(绝不触碰真实 ~/.pyharness)。"""
    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "pyharness.db")
    return cfg


def _sessions_dir(tmp_path):
    """open_store 要求 Path 对象(mkdir 面);与 cfg.storage.sessions_dir 同路径。"""
    import pathlib
    return pathlib.Path(tmp_path) / "sessions"


def _names_of(schemas) -> set:
    out = set()
    for s in schemas or []:
        fn = (s or {}).get("function") or {}
        if fn.get("name"):
            out.add(fn["name"])
    return out


@pytest.mark.asyncio
async def test_build_spine_components_present(tmp_path):
    """脊柱装配完备性:counters/sysprompt/compactor/streaming + spill 读工具。"""
    cfg = _cfg(tmp_path)
    spine = await build_spine(cfg, sid="s-eng-test-000001",
                              sessions_dir=_sessions_dir(tmp_path))
    try:
        # F032 计数与预算闸同源:scope._counters is spine.counters
        assert spine.counters is not None
        assert spine.scope._counters is spine.counters
        # F010 / F058 装配器在岗(agent-loop 消费面)
        assert spine.sysprompt is not None
        assert spine.compactor is not None
        # F027 默认关(非流式);cfg 开则 spine 透传
        assert spine.streaming is False
        # 工具面:fs.* 可见子集 + storage.spill 读工具(engine 注册);
        # fs.delete_file = critical 危险级 → strict 下不可见(guard 设计,非 bug)
        vis = _names_of(spine.tools.schemas_for(spine.scope))
        for need in ("fs.read_file", "fs.write_file", "fs.list_dir",
                     "storage.spill"):
            assert need in vis, f"strict 可见工具缺 {need};实际={sorted(vis)}"
        assert "fs.delete_file" not in vis, \
            "critical 级工具不应在 strict 下可见(can_use 单调拒绝)"
        # 初始 storage.spill 未激活(懒)
        assert spine.storage.spill is None
        # #39 预设默认 strict:goal/todo/storage.kv 等自管理/存储域可见
        for need in ("goal", "todo", "storage.kv"):
            assert need in vis, f"工具 {need} 应可见;实际={sorted(vis)}"
        assert spine.goals is not None and spine.todos is not None
    finally:
        _close_spine(spine)


@pytest.mark.asyncio
async def test_streaming_flag_pass_through(tmp_path):
    """cfg.loop.streaming=True → spine.streaming=True(桌面 SSE 装配路径)。"""
    cfg = _cfg(tmp_path)
    cfg.loop.streaming = True
    spine = await build_spine(cfg, sid="s-eng-stream-000001",
                              sessions_dir=_sessions_dir(tmp_path))
    try:
        assert spine.streaming is True
    finally:
        _close_spine(spine)


@pytest.mark.asyncio
async def test_agent_ctx_of_activates_spill_once(tmp_path):
    """spill 激活:首建 agent → provider 挂载(storage.spill 立即可见);幂等。"""
    cfg = _cfg(tmp_path)
    spine = await build_spine(cfg, sid="s-eng-spill-000001",
                              sessions_dir=_sessions_dir(tmp_path))
    try:
        env = _env_of(spine)
        ctx1 = await _agent_ctx_of(spine, env)          # 首次:建 agent + 激活
        prov = spine.storage.spill
        assert prov is not None, "spill provider 应在首建 agent 时激活"
        # 激活一次(幂等):第二次不再新建
        ctx2 = await _agent_ctx_of(spine, env)
        assert ctx2 is ctx1
        assert spine.storage.spill is prov
        # 会话级 agent 1:1 占位
        assert spine.active_agents.get(spine.session.sid) is not None
        # executor 消费面可用:put 落盘 + ref(需要 ctx 已具 config/bus/session)
        meta = await prov.put("x" * 3000, kind="output")
        assert isinstance(meta, dict)
        assert meta.get("ref"), f"put 应返回含 ref 的元数据:{meta}"
    finally:
        _close_spine(spine)


@pytest.mark.asyncio
async def test_assemble_real_engine_ctx_shape(tmp_path):
    """一键装配 ctx 形状:counters 在岗;storage 与 spine 同对象;脊柱齐备。"""
    cfg = _cfg(tmp_path)
    ctx = await assemble_real_engine(
        cfg, sid="s-eng-asmb-000001",
        sessions_dir=_sessions_dir(tmp_path))
    try:
        spine = ctx.engine_spine
        assert ctx.counters is spine.counters
        assert ctx.storage is spine.storage               # spill 激活后同对象可见
        for attr in ("session", "bus", "llm", "scope", "loop", "tools",
                     "registry", "guard", "approval", "task_runner"):
            assert getattr(ctx, attr, None) is not None, f"ctx.{attr} 缺失"
        assert ctx.agent is None                            # 懒建(首次 submit)
        # 降级链装配:fallback_models 非空默认 → llm._chain 在岗
        assert getattr(ctx.llm, "_chain", None) is not None
    finally:
        _close_spine(spine)


def _env_of(spine) -> object:
    """runner 同形 env:携带 session_id 的鸭子信封。"""
    class _Env:
        session_id = spine.session.sid
        seq = 1
        type = "user.message"
        payload = {"content": "probe"}
    return _Env()


def _close_spine(spine) -> None:
    """尽力收尾:释放 scope 占位(不等待任务排水)。"""
    try:
        if getattr(spine, "scope", None) is not None:
            spine.scope.release()
    except Exception:                                    # noqa: BLE001 收尾尽力
        pass
