"""pyharness/core/tool_goal.py — goal.* 工具登记(F047 Consumer 面)。

GoalManager(状态机/事件)已完整;本文件只做五要素登记 + Provider 绑定
(Definition/Provider 分离,executor 关3 契约)。Provider 经 ctx.goals 消费,
ctx.goals 缺位(装配缺失)→ CYC-999(内部装配 bug,不猜测)。

op 面:create/update/check/abandon/list(与 GoalManager.goal 验收伪代码同源);
danger=none(会话内部状态管理,无外部副作用,不需要审批)。
"""
from __future__ import annotations

from typing import Any

from pyharness.core.tools_registry import ToolDefinition  # noqa: F401
from pyharness.errors import raise_code

TOOL_NAME = "goal"

_OPS_HINT = "op ∈ create/update/check/abandon/list"

_DEFINITION = ToolDefinition(
    name=TOOL_NAME,
    danger="none",
    description="目标管理:create 建目标(text)、update 改状态/备注(id,status,note)、"
                "check 核对进度(id,checked[],progress,claim_done)、abandon 放弃、"
                "list 看板;活动目标会注入系统提示词软提醒,完成前须先核对",
    schema={"type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["create", "update", "check",
                                                  "abandon", "list"]},
                "text": {"type": "string", "maxLength": 2000},
                "id": {"type": "string", "maxLength": 64},
                "status": {"type": "string"},
                "note": {"type": "string", "maxLength": 2000},
                "progress": {"type": "number", "minimum": 0, "maximum": 1},
                "checked": {"type": "array", "items": {"type": "string"},
                            "maxItems": 50},
                "claim_done": {"type": "boolean"},
                "task_id": {"type": "string", "maxLength": 64}},
            "required": ["op"], "additionalProperties": False},
    timeout_s=10,
    owner="builtin",
    ctx_path="goals",
    version="1.0.0",
)


class _GoalHandle:
    """Provider handle:op 分派到 ctx.goals(GoalManager.goal 异步分发器)。"""

    async def handle(self, args: dict, ctx: Any) -> str:
        mgr = getattr(ctx, "goals", None)
        if mgr is None:
            raise_code("CYC-999", module="tool_goal",
                       hint="ctx.goals 未装配(引擎未挂 GoalManager),goal.* 不可用")
        return await mgr.goal(args, ctx)          # 返回用户可读 summary 文本


def register(registry: Any) -> list[str]:
    """五要素登记 + Provider 绑定(engine 装配面调用)。"""
    registry.register_tool(_DEFINITION, provider=_GoalHandle())
    return [TOOL_NAME]


__all__ = ["register", "TOOL_NAME"]
