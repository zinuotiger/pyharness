"""pyharness/core/tool_ask.py — user.ask 工具登记(#38 Consumer 面)。

Provider 经 ctx.ask(AskProvider);缺位(装配缺失)→ CYC-999。danger=none
(反问不触碰执行权限,只需信息);会话内自管理域(user.*),strict 恒可见。
"""
from __future__ import annotations

from typing import Any

from pyharness.core.tools_registry import ToolDefinition  # noqa: F401
from pyharness.errors import raise_code

TOOL_NAME = "user.ask"


class _AskHandle:
    """Provider handle:question/options → AskProvider.request(阻塞至答复/超时)。"""

    async def handle(self, args: dict, ctx: Any) -> str:
        provider = getattr(ctx, "ask", None)
        if provider is None:
            raise_code("CYC-999", module="user.ask",
                       hint="ctx.ask 未装配(引擎未挂 AskProvider),无法反问")
        q = str(args.get("question") or "").strip()
        if not q:
            raise_code("EVT-100", field="question", hint="反问缺问题文本")
        return await provider.request(
            q, options=args.get("options"),
            ttl_s=int(args.get("ttl_s") or 120))


def register(registry: Any) -> list[str]:
    """五要素登记 + Provider 绑定(engine 装配面调用)。"""
    registry.register_tool(
        ToolDefinition(
            name=TOOL_NAME, danger="none",
            description="向用户提问并等待答复(带选项拍板/自由文本);用于决策前置、"
                        "信息缺口补齐;答复会作为工具结果回喂",
            schema={"type": "object",
                    "properties": {
                        "question": {"type": "string", "minLength": 1,
                                     "maxLength": 1000},
                        "options": {"type": "array", "items": {"type": "string",
                                                              "maxLength": 200},
                                    "maxItems": 10},
                        "ttl_s": {"type": "integer", "minimum": 5,
                                  "maximum": 3600}},
                    "required": ["question"], "additionalProperties": False},
            timeout_s=300, owner="builtin", ctx_path="ask",
            version="1.0.0"),
        provider=_AskHandle())
    return [TOOL_NAME]


__all__ = ["register", "TOOL_NAME"]
