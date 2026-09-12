"""插件装载器单测 — 契约:core/plugin_loader.py(#50/#51/#52)。

覆盖:发现(discover)、单文件装载(MANIFEST+register_tools 桥)、安装/激活/
工具注册、热卸载(工具注销+plugin.uninstalled)、热重载(HMR:改代码重载生效)、
状态查询。测试插件在 tmp 目录内现写(不污染仓库)。
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from pyharness.bus.plugin import PluginManager
from pyharness.core.plugin_loader import (discover_plugins, load_plugin,
                                          load_spec, reload_plugin,
                                          unload_plugin)
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError

PLUGIN_SRC = '''\
"""测试插件(版本可热更)。"""
from datetime import datetime

MANIFEST = {"id": "%(pid)s", "version": "%(version)s", "api_version": "1",
            "requires": []}


async def _echo(args, ctx):
    return args.get("text", "hello") + "-v%(version)s"


def register_tools(registry):
    from pyharness.core.tools_registry import ToolDefinition
    registry.register_tool(ToolDefinition(
        name="util.echo", danger="none",
        description="回显文本(测试插件)",
        schema={"type": "object",
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False},
        timeout_s=5, owner="plugin:%(pid)s", ctx_path="util",
        version="%(version)s"), provider=_echo)
    return ["util.echo"]
'''


class _SessionStub:
    """manager 留痕用最小 session(append 记录)。"""

    def __init__(self) -> None:
        self.notes: list = []

    async def append(self, type_, payload=None, **kw):
        self.notes.append((type_, payload))


class _Ctx:
    def __init__(self) -> None:
        self.session = _SessionStub()


def _write_plugin(tmp_path, pid: str, version: str) -> object:
    d = tmp_path / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.py").write_text(PLUGIN_SRC % {"pid": pid, "version": version},
                                 encoding="utf-8")
    return d


def _setup(tmp_path) -> tuple:
    mgr = PluginManager()
    ctx = _Ctx()
    tool_reg = ToolRegistry()
    state: dict = {}
    return mgr, ctx, tool_reg, state


@pytest.mark.asyncio
async def test_discover_and_load_hello(tmp_path):
    d = _write_plugin(tmp_path, "hello", "1.0.0")
    found = discover_plugins(tmp_path)
    assert [p.name for p in found] == ["hello"]
    spec = load_spec(d)
    assert spec.manifest["id"] == "hello"
    assert spec.manifest["api_version"] == "1"


@pytest.mark.asyncio
async def test_load_activate_registers_tool(tmp_path):
    d = _write_plugin(tmp_path, "hello", "1.0.0")
    mgr, ctx, tool_reg, state = _setup(tmp_path)
    spec = load_spec(d)
    await load_plugin(mgr, spec, ctx, tool_reg, state=state)
    assert mgr.state("hello") == "active"
    # 工具桥接进 core 注册表(executor 可执行面)
    assert tool_reg.lookup("util.echo") is not None
    assert "util.echo" in state["hello"]["tool_names"]
    # plugin.installed 留痕
    types = [t for t, _ in ctx.session.notes]
    assert "plugin.installed" in types
    # 卸载:工具注销 + 状态回落
    await unload_plugin(mgr, spec, ctx, tool_reg, state=state)
    assert mgr.state("hello") == "absent"
    with pytest.raises(Exception):
        tool_reg.lookup("util.echo")
    assert "plugin.uninstalled" in [t for t, _ in ctx.session.notes]


@pytest.mark.asyncio
async def test_reload_picks_new_code(tmp_path):
    d = _write_plugin(tmp_path, "hello", "1.0.0")
    mgr, ctx, tool_reg, state = _setup(tmp_path)
    spec = load_spec(d)
    await load_plugin(mgr, spec, ctx, tool_reg, state=state)
    # HMR:改版本重载 → 工具 provider 行为变化 + 注册面刷新
    (d / "plugin.py").write_text(
        PLUGIN_SRC % {"pid": "hello", "version": "2.0.0"}, encoding="utf-8")
    await reload_plugin(mgr, spec, ctx, tool_reg, state=state, pkg_dir=d)
    assert mgr.state("hello") == "active"
    assert state["hello"]["spec"].manifest["version"] == "2.0.0"
    # 工具定义已是新契约(version 字段可查)
    defn = tool_reg.lookup("util.echo")
    assert defn is not None
    assert getattr(defn, "version", "") == "2.0.0"


@pytest.mark.asyncio
async def test_load_real_example_plugin():
    """仓库示例插件(hello_time)可装载并注册 util.now。"""
    from pathlib import Path
    example = (Path(__file__).resolve().parents[2] / "examples" / "plugins"
               / "hello_time")
    assert example.exists(), "示例插件路径缺失"
    mgr, ctx, tool_reg, state = _setup(None)
    spec = load_spec(example)
    await load_plugin(mgr, spec, ctx, tool_reg, state=state)
    assert mgr.state("hello_time") == "active"
    assert tool_reg.lookup("util.now") is not None
    await unload_plugin(mgr, spec, ctx, tool_reg, state=state)


@pytest.mark.asyncio
async def test_async_register_tools_is_awaited(tmp_path):
    """插件契约允许 async register_tools,loader 必须 await 而不是 list(coroutine)。"""
    d = _write_plugin(tmp_path, "hello", "1.0.0")
    src = (d / "plugin.py").read_text(encoding="utf-8")
    (d / "plugin.py").write_text(
        src.replace("def register_tools(registry):",
                    "async def register_tools(registry):"),
        encoding="utf-8")
    mgr, ctx, tool_reg, state = _setup(tmp_path)
    spec = load_spec(d)
    await load_plugin(mgr, spec, ctx, tool_reg, state=state)
    assert state["hello"]["tool_names"] == ["util.echo"]
    assert tool_reg.lookup("util.echo") is not None


def test_manifest_id_must_match_directory(tmp_path):
    """MANIFEST.id 与目录名不一致会导致两套状态键,装载期必须拒绝。"""
    before = set(sys.modules)
    d = _write_plugin(tmp_path, "hello", "1.0.0")
    src = (d / "plugin.py").read_text(encoding="utf-8")
    (d / "plugin.py").write_text(
        src.replace('"id": "hello"', '"id": "wrong"'), encoding="utf-8")
    with pytest.raises(PyHError) as e:
        load_spec(d)
    assert e.value.code == "BUS-003"
    assert not any(name.startswith("pyh_plugin.hello.")
                   for name in set(sys.modules) - before)
