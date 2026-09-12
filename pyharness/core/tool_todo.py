"""pyharness/core/tool_todo.py — todo.* 工具登记(#32 清单项 Consumer 面)。

TodoManager(事件源待办表)见 core/todo.py;本文件五要素登记 + Provider 绑定。
op 面:add/list/done/remove/clear_done;danger=none(会话内部状态,零外部副作用)。
ctx.todos 缺位(装配缺失)→ CYC-999(不猜测)。
"""
from __future__ import annotations

from typing import Any

from pyharness.core.tools_registry import ToolDefinition  # noqa: F401
from pyharness.errors import raise_code

TOOL_NAME = "todo"

_DEFINITION = ToolDefinition(
    name=TOOL_NAME,
    danger="none",
    description="待办清单管理:add(text) 添加、list 查看、done(id) 完成、"
                "remove(id) 移除、clear_done 清理已完成;多步任务先列待办再逐步执行",
    schema={"type": "object",
            "properties": {
                "op": {"type": "string",
                       "enum": ["add", "list", "done", "remove", "clear_done"]},
                "text": {"type": "string", "maxLength": 500},
                "id": {"type": "integer", "minimum": 1},
                "task_id": {"type": "string", "maxLength": 64}},
            "required": ["op"], "additionalProperties": False},
    timeout_s=10,
    owner="builtin",
    ctx_path="todos",
    version="1.0.0",
)


class _TodoHandle:
    """Provider handle:op 分派到 ctx.todos(TodoManager.apply_op)。"""

    async def handle(self, args: dict, ctx: Any) -> str:
        mgr = getattr(ctx, "todos", None)
        if mgr is None:
            raise_code("CYC-999", module="tool_todo",
                       hint="ctx.todos 未装配(引擎未挂 TodoManager),todo.* 不可用")
        return await mgr.apply_op(
            args.get("op", ""),
            text=args.get("text"),
            todo_id=args.get("id"),
            task_id=args.get("task_id") or getattr(ctx, "task_id", None))


def register(registry: Any) -> list[str]:
    """五要素登记 + Provider 绑定(engine 装配面调用)。"""
    registry.register_tool(_DEFINITION, provider=_TodoHandle())
    return [TOOL_NAME]


__all__ = ["register", "TOOL_NAME"]
