"""示例插件:hello_time(#50/#51 实装示例,装载器演示用)。

契约见 core/plugin_loader.py:单文件插件 = MANIFEST + register_tools(桥 core
ToolRegistry)。本插件注册 util.now 工具(取当前时间;util 域 = 会话自管理域,
strict 下可见,零审批)。面试演示:装插件 → 工具即出现在 agent 工具表。
"""
from __future__ import annotations

from datetime import datetime

from pyharness.core.tools_registry import ToolDefinition

MANIFEST = {
    "id": "hello_time",
    "version": "1.0.0",
    "api_version": "1",
    "requires": [],
}


async def _now(args: dict, ctx) -> str:
    """util.now Provider:ISO 时间 + 本地可读格式。"""
    now = datetime.now()
    return (now.strftime("%Y-%m-%d %H:%M:%S")
            + f" (UTC {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')})")


def register_tools(registry) -> list:
    """桥接 core ToolRegistry:返回注册名清单(卸载时逐名注销)。"""
    registry.register_tool(
        ToolDefinition(
            name="util.now", danger="none",
            description="取当前日期时间(本地 + UTC);需要时间/时钟类回答时调用",
            schema={"type": "object",
                    "properties": {}, "additionalProperties": False},
            timeout_s=5, owner=f"plugin:{MANIFEST['id']}",
            ctx_path="util", version=MANIFEST["version"]),
        provider=_now)
    return ["util.now"]
