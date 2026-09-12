"""引擎插件预载单测 — build_spine/桌面 runner 装配后示例插件可用(F073/#50)。

契约:engine.build_spine → 示例插件 hello_time 预载 → util.now 注册且 strict
档可见(util 自管理域);plugin.installed 事件入会话日志(真 SessionLog+vocab
校验,非 stub);spine.plugins/plugin_state/tool_registry 字段在位。
"""
from __future__ import annotations

import asyncio

from pyharness.config import load_settings
from pyharness.engine import build_spine


def test_engine_preloads_plugins(tmp_path):
    cfg = load_settings()
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.db_path = str(tmp_path / "ph.db")
    spine = asyncio.run(build_spine(
        cfg, sid="s-plug-test",
        sessions_dir=tmp_path / "sessions",
        bus=None))
    try:
        assert getattr(spine, "plugins", None) is not None
        assert getattr(spine, "plugin_state", None) is not None
        assert getattr(spine, "tool_registry", None) is not None
        # 懒预载语义:created 落盘后(首跑时刻)装载 → 复刻 runner 时序
        async def _assemble_like_runner():
            from pyharness.engine import _preload_plugins
            await spine.session.append("session.created",
                                       {"title": "", "model": "test"},
                                       actor="system", sync=True)
            await _preload_plugins(spine)
        asyncio.run(_assemble_like_runner())
        assert spine.plugins.state("hello_time") == "active", \
            "示例插件应预载为 active"
        rec = spine.plugin_state.get("hello_time", {})
        assert "util.now" in rec.get("tool_names", ())
        # strict 档可见(util 域 = 会话自管理)
        vis = {((s or {}).get("function") or {}).get("name")
               for s in spine.tools.schemas_for(spine.scope)}
        assert "util.now" in vis, "util.now 应 strict 可见"
        # plugin.installed 事件经真实 SessionLog+vocab 校验落盘(非 stub)
        types = [e.type for e in spine.session.events_after(0)]
        assert "plugin.installed" in types
    finally:
        import asyncio as _a
        try:
            _a.run(spine.session.close()) if hasattr(
                spine.session, "close") else None
        except Exception:
            pass
