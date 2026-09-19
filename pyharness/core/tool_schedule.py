"""pyharness/core/tool_schedule.py — schedule Agent Tool 登记(F048 Consumer 面)。

Scheduler(事件化调度 / 分钟泵 / 崩溃恢复)已完整;本文件只做五要素登记 +
Provider 绑定(Definition/Provider 分离,executor 关3 契约)。Provider 经
``ctx.schedule`` 消费**现有 Scheduler**,不自行实现任何调度逻辑——表达式校验、
持久化、ticker、恢复一律由 Scheduler 唯一承担(唯一调度真源,INV-01)。

op 面:create / list / pause / resume / remove(与 Scheduler 公开面同源)。

判险:danger="low"。该工具只在**会话内写一条 job 定义**,调用时**不执行**任何
外部动作;到点后的真实执行仍走 task_queue → AgentLoop → tools 四关管道(含
high/critical 工具自身的审批与 guard),故它不是绕过治理的延迟执行入口。

参数缺失纪律:**不猜**。create 缺 name/kind/expr/intent 一律 EVT-100 拒绝,并在
advice 指明缺失项与"信息不足先用 user.ask 向用户确认"——是否向用户提问由
AgentLoop 决定,本工具不直接调用 user.ask。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pyharness.core.tools_registry import ToolDefinition  # noqa: F401
from pyharness.errors import raise_code

TOOL_NAME = "schedule"

_OPS = ("create", "list", "pause", "resume", "remove")
_MISSING_ADVICE = "补齐后重试;信息不足时先用 user.ask 向用户确认,不要猜测"

_DEFINITION = ToolDefinition(
    name=TOOL_NAME,
    danger="low",
    description="定时任务管理:create 建任务、list 查看、pause/resume 暂停恢复、"
                "remove 删除。kind 三选一:cron(表达式如 0 8 * * * 即每天 08:00)、"
                "interval(纯秒数如 3600)、at(ISO 如 2026-09-20T08:00:00);"
                "intent=到点要执行的内容",
    schema={"type": "object",
            "properties": {
                "op": {"type": "string", "enum": list(_OPS),
                       "description": "create 建任务 / list 查看 / pause 暂停 / "
                                      "resume 恢复 / remove 删除"},
                "name": {"type": "string", "minLength": 2, "maxLength": 64,
                         "description": "任务名(必填,仅 create 用):小写字母开头,"
                                        "只可含 a-z 0-9 下划线 点号,2-64 字符;"
                                        "禁中文、禁大写、禁连字符"},
                "kind": {"type": "string", "enum": ["cron", "interval", "at"],
                         "description": "cron=按日历重复(每天/每周/每月);"
                                        "interval=固定间隔重复;at=只触发一次"},
                "expr": {"type": "string", "minLength": 1, "maxLength": 128,
                         "description": "cron→'分 时 日 月 周'五字段(如 '0 8 * * *' "
                                        "每天 08:00;'0 8 * * 1-5' 工作日 08:00);"
                                        "interval→纯秒数(如 '3600',最小 60);"
                                        "at→ISO8601(如 '2026-09-20T08:00:00',须为未来)"},
                "intent": {"type": "string", "minLength": 1, "maxLength": 2000,
                           "description": "到点时要执行的内容(任务正文,可用中文),"
                                          "如 '提醒我开始工作'。**必须取自用户的原话**;"
                                          "用户若没说清要做什么(例如只说'设个提醒'),"
                                          "绝不可自行编造内容,应先用 user.ask 问清楚再建"},
                "is_risky": {"type": "boolean",
                             "description": "仅在需要把任务标记为危险时传 true;"
                                            "不需要时省略该字段,不要传 false"},
            },
            "required": ["op"], "additionalProperties": False},
    timeout_s=10,
    owner="builtin",
    ctx_path="schedule",
    version="1.0.0",
)


def _iso(dt: Optional[Any]) -> Optional[str]:
    """datetime → ISO8601(JSON 可序列化;None 透传)。"""
    return dt.isoformat() if dt is not None else None


def _dump(obj: dict) -> str:
    """结构化返回(JSON 文本,供 AgentLoop 解析与转述)。"""
    return json.dumps(obj, ensure_ascii=False)


def _require_name(args: dict, op: str) -> str:
    """name 必填校验(不猜):缺失即 EVT-100。"""
    name = str(args.get("name") or "").strip()
    if not name:
        raise_code("EVT-100", field="name", hint=f"{op} 缺必填参数:name",
                   advice=_MISSING_ADVICE)
    return name


class _ScheduleHandle:
    """Provider handle:op 分派到 ctx.schedule(现有 Scheduler)。"""

    async def handle(self, args: dict, ctx: Any) -> str:
        sched = getattr(ctx, "schedule", None)
        if sched is None:
            raise_code("CYC-999", module="tool_schedule",
                       hint="ctx.schedule 未装配(引擎未挂 Scheduler),schedule 不可用")
        op = str(args.get("op") or "").strip()
        if op == "create":
            return await self._create(sched, args, ctx)
        if op == "list":
            return await self._list(sched, ctx)
        # 显式分派表(不用 getattr(op) —— 外部输入不得直接选方法,关 4/CND-04)
        fn = {"pause": sched.pause, "resume": sched.resume,
              "remove": sched.remove}.get(op)
        if fn is None:
            raise_code("EVT-100", field="op",
                       advice=f"op ∈ {'/'.join(_OPS)}")
        name = _require_name(args, op)
        await fn(name, ctx=ctx)
        return _dump({"ok": True, "op": op, "name": name})

    async def _create(self, sched: Any, args: dict, ctx: Any) -> str:
        """建任务:参数完整性检查后交 Scheduler.register(校验/持久化归其所有)。"""
        name = _require_name(args, "create")
        missing = [f for f in ("kind", "expr", "intent")
                   if not str(args.get(f) or "").strip()]
        if missing:
            raise_code("EVT-100", field=missing[0],
                       hint=f"create 缺必填参数:{', '.join(missing)}",
                       advice=_MISSING_ADVICE)
        kind = str(args["kind"]).strip()
        expr = str(args["expr"]).strip()
        intent = str(args["intent"]).strip()
        # is_risky **单调**:只能升级为危险,不能下调。本工具不传 meta(INV-09),
        # 故非显式声明时 Scheduler 的推导恒为 False;若允许传 False,等于让调用方
        # 单方面关掉"深夜禁触危险模板"这一安全默认 → 故 False/缺省一律交回推导。
        override = True if args.get("is_risky") is True else None
        await sched.register(name, kind, expr, {"intent": intent},
                             is_risky=override, ctx=ctx)
        info = next((j for j in await sched.list_jobs(ctx=ctx)
                     if j.name == name), None)
        return _dump({"ok": True, "op": "create", "name": name, "kind": kind,
                      "expr": expr, "paused": False,
                      "next_fire_at": _iso(info.next_fire_at) if info else None})

    async def _list(self, sched: Any, ctx: Any) -> str:
        rows = [{"name": j.name, "kind": j.kind, "expr": j.expr,
                 "paused": j.paused, "next_fire_at": _iso(j.next_fire_at),
                 "last_fired_at": _iso(j.last_fired_at),
                 "triggered": j.triggered, "missed": j.missed}
                for j in await sched.list_jobs(ctx=ctx)]
        return _dump({"ok": True, "op": "list", "count": len(rows), "jobs": rows})


def register(registry: Any) -> list[str]:
    """五要素登记 + Provider 绑定(engine 装配面调用)。"""
    registry.register_tool(_DEFINITION, provider=_ScheduleHandle())
    return [TOOL_NAME]


__all__ = ["register", "TOOL_NAME"]
