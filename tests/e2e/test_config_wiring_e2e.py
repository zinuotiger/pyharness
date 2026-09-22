"""E2E:N1 配置接线 —— 声明值经**生产装配路径**到达运行时对象。

路径:``Settings`` → ``build_spine``/``assemble_real_engine`` → ``open_store`` →
``SessionStore`` → ``EngineSpine.start_flush_ticker``(定时器读 store 值)。
"""
from __future__ import annotations

import asyncio


from pyharness.core import llm as llm_mod


async def test_configured_flush_values_reach_the_store(e2e_factory):
    """装配出的 store 必须携带配置值(修复前恒为 DEFAULTS 0.5/64)。"""
    from pyharness.engine import assemble_real_engine

    s = await e2e_factory(None, sid="s-e2e-cfg-0001")
    cfg = s.ctx.settings
    cfg.log.jsonl.flush_interval_s = 2.5
    cfg.log.jsonl.flush_batch = 13

    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    try:
        from tests.conftest import ScriptedAdapter
        llm_mod.adapters[cfg.llm.model] = ScriptedAdapter(cfg.llm.model, [])
        ctx = await assemble_real_engine(
            cfg, sid="s-e2e-cfg-prod", sessions_dir=s.tmp / "sessions",
            channel="desktop")
        store = ctx.engine_spine.persistence
        try:
            assert store.flush_interval_s == 2.5, \
                f"配置未到达 store:{store.flush_interval_s}"
            assert store.flush_batch == 13, f"配置未到达 store:{store.flush_batch}"
            # 定时器读的是 store 值 ⇒ 断言"配置真正驱动运行时行为"
            assert await ctx.engine_spine.start_flush_ticker() is True
        finally:
            await ctx.engine_spine.close()
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


async def test_config_drives_ticker_interval(e2e_factory):
    """配置的间隔**真的**决定落盘节奏(短间隔 ⇒ 攒批事件更快落盘)。"""
    s = await e2e_factory(None, sid="s-e2e-cfg-tick")
    store = s.ctx.engine_spine.persistence
    store.flush_interval_s = 0.05              # 模拟"配置已生效"
    await s.boot()
    assert await s.ctx.engine_spine.start_flush_ticker() is True
    log_path = s.tmp / "sessions" / "s-e2e-cfg-tick.jsonl"
    await s.ctx.session.append("agent.message", {"content": "CFG-MARK"},
                               actor="agent")
    assert "CFG-MARK" not in log_path.read_text(encoding="utf-8")
    await asyncio.sleep(0.4)
    assert "CFG-MARK" in log_path.read_text(encoding="utf-8"), \
        "定时器应按 store 的间隔落盘"


async def test_default_config_still_uses_defaults(tmp_path):
    """回归:未配置时行为与修复前一致(0.5 / 64),不引入新语义。"""
    from tests.conftest import e2e_settings
    from pyharness.engine import assemble_real_engine

    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = e2e_settings(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    try:
        from tests.conftest import ScriptedAdapter
        llm_mod.adapters[cfg.llm.model] = ScriptedAdapter(cfg.llm.model, [])
        ctx = await assemble_real_engine(cfg, sid="s-e2e-cfg-default",
                                         sessions_dir=tmp_path / "sessions",
                                         channel="desktop")
        store = ctx.engine_spine.persistence
        try:
            assert store.flush_interval_s == 0.5
            assert store.flush_batch == 64
        finally:
            await ctx.engine_spine.close()
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)
