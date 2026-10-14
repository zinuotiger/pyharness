"""Engine plugins: complete migrated implementation."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    pass

from pyharness.engine_types import EngineSpine, log

async def _preload_plugins(spine: EngineSpine) -> None:
    """插件预载(async 入口用;load_plugin 需 await,同步装配段不碰)。

    扫 examples/plugins/* 逐个装载进该会话工具表;单个失败只告警不阻断
    (会话照常可用;事件 plugin.installed 落该会话日志=真实审计)。
    """
    from pyharness.core import plugin_loader as _pl
    plug_ctx = SimpleNamespace(
        session=getattr(spine, "session", None),
        bus=getattr(spine, "bus", None))
    pdir = Path(__file__).resolve().parents[1] / "examples" / "plugins"
    if not pdir.exists():
        await _preload_mcp(spine)
        return
    # `plugins.priority`（R24 接线）:同层无依赖插件的装载次序,**小者先**;
    # 未列入者按默认 0(平序回落到**名字序**,保持确定性)。该键此前零读取者。
    prio = dict(getattr(getattr(spine, "settings", None), "plugins", None)
                and (getattr(spine.settings.plugins, "priority", None) or {}) or {})
    for pd in sorted(pdir.glob("*/"),
                     key=lambda d: (int(prio.get(d.name, 0) or 0), d.name)):
        try:
            spec = _pl.load_spec(pd)
            await _pl.load_plugin(spine.plugins, spec, plug_ctx,
                                  spine.tool_registry,
                                  state=spine.plugin_state)
        except Exception as e:               # noqa: BLE001 预载失败不阻断
            log.warning("plugin preload failed %s: %s", pd.name,
                        type(e).__name__)
    await _preload_mcp(spine)

async def _preload_mcp(spine: EngineSpine) -> None:
    """装载配置中的 MCP stdio servers,并桥接工具到会话 registry。"""
    plugins = getattr(getattr(spine, "settings", None), "plugins", None)
    servers = list(getattr(plugins, "mcp_servers", None) or [])
    if not servers:
        return
    from pyharness.core.mcp import McpClient, StdioTransport
    for server in servers:
        if not getattr(server, "enabled", True):
            continue
        client = McpClient(server.name, StdioTransport(
            list(server.command), timeout_s=int(server.timeout_s)))
        try:
            await client.connect()
            client.register_into(spine.tool_registry, allowed_tools=server.allowed_tools)
            spine._mcp_clients.append(client)
        except Exception as e:                       # noqa: BLE001 单个 server 隔离
            await client.close()
            log.warning("mcp preload failed name=%s: %s", server.name,
                        type(e).__name__)

class _SpineCtx:
    """Live ctx for long-lived orchestration timers.

    Scheduler keeps this object for the whole session, so ``task_queue`` is
    resolved at fire time rather than being frozen to None during startup.
    """

    def __init__(self, spine: EngineSpine) -> None:
        self._spine = spine

    @property
    def session(self) -> Any:
        return self._spine.session

    @property
    def task_queue(self) -> Any:
        return self._spine.task_queue

def _cfg_of(spine: Any) -> Any:
    cfg = getattr(spine, "settings", None)
    if cfg is None:
        from pyharness.config import load_settings
        cfg = load_settings()
        spine.settings = cfg
    return cfg

# Preserve historical import and pickle identities through the compatibility facade.
_SpineCtx.__module__ = 'pyharness.engine'
