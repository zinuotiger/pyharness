"""pyharness/core/tool_skill.py — skill.load/skill.list 工具登记(F073/#31)。

Provider 经 ctx.skills(SkillManager);缺位(装配缺失)→ CYC-999。danger=none
(只读本地技能文本,不触碰执行权限);skill 域=会话内自管理(SELF_DOMAINS),
strict 档恒可见。skill.load 落 skill.used 审计事件(body 不进日志)。
"""
from __future__ import annotations

import logging
from typing import Any

from pyharness.core.tools_registry import ToolDefinition  # noqa: F401
from pyharness.errors import raise_code

log = logging.getLogger("pyharness.tool_skill")


class _SkillHandle:
    """Provider handle:ctx.skills → load/list;skill.load 审计落 skill.used。"""

    async def handle(self, args: dict, ctx: Any) -> str:
        mgr = getattr(ctx, "skills", None)
        if mgr is None:
            raise_code("CYC-999", module="skill",
                       hint="ctx.skills 未装配(引擎未挂 SkillManager)")
        op = str(args.get("_op") or "")
        if op == "list":
            rows = mgr.list()
            if not rows:
                return "技能库为空"
            return "可用技能:\n" + "\n".join(
                f"- {r['name']}: {r['description']}" for r in rows)
        name = str(args.get("name") or "").strip()
        if not name:
            raise_code("EVT-100", field="name", hint="skill.load 缺技能名")
        data = mgr.load(name)                    # 未知名 → SKL-901
        try:                                     # 审计:装载留痕(body 不入日志)
            await ctx.session.append("skill.used", {"name": data["name"]},
                                     actor="tool")
        except Exception as e:                   # noqa: BLE001 审计失败不阻断
            log.debug("skill.used append failed: %s", type(e).__name__)
        return (f"技能已装载:{data['name']}\n"
                f"简介:{data['description']}\n"
                f"正文:\n{data['body']}")


def register(registry: Any) -> list[str]:
    """skill.load/skill.list 五要素登记 + Provider 绑定(engine 装配面调用)。"""
    handle = _SkillHandle()

    def _make_provider(op: str):
        async def _h(args: dict, ctx: Any) -> str:
            return await handle.handle({**(args or {}), "_op": op}, ctx)
        return _h

    registry.register_tool(
        ToolDefinition(
            name="skill.load", danger="none",
            description="装载技能正文到上下文(技能目录见系统提示词;任务匹配时"
                        "调用并按技能执行)",
            schema={"type": "object",
                    "properties": {"name": {"type": "string",
                                            "minLength": 1,
                                            "maxLength": 64,
                                            "description": "技能名(见系统提示词技能目录)"}},
                    "required": ["name"], "additionalProperties": False},
            timeout_s=300, owner="builtin", ctx_path="skills",
            version="1.0.0"),
        provider=_make_provider("load"))
    registry.register_tool(
        ToolDefinition(
            name="skill.list", danger="none",
            description="列出当前可用技能(名字+一句话)",
            schema={"type": "object", "properties": {},
                    "additionalProperties": False},
            timeout_s=60, owner="builtin", ctx_path="skills",
            version="1.0.0"),
        provider=_make_provider("list"))
    return ["skill.load", "skill.list"]
