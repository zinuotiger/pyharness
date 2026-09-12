"""pyharness/core/telemetry.py — 本地审计导出(#66 Consumer 面,只本地不外发)。

一句话职责:会话事件流 → 审计报告(类型计数/用量折叠/工具与审批明细/耗时),
供 stats/budget 命令与桌面审计面板消费。数据源唯一 = 事件日志(INV-01 派生,
无第二份状态);敏感原文不导出:文本内容按行截断 + 配置脱敏回落。
"""
from __future__ import annotations

import json
from typing import Any, Optional

SUMMARY_MAX = 500


def _brief(text: Any, limit: int = SUMMARY_MAX) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[:limit] + "…"


def _redact(text: Any) -> str:
    """出口脱敏(复用 config.redact;不可用则截断兜底)。"""
    s = str(text or "")
    try:
        from pyharness.config import redact as _module_redact
        return str(_module_redact(s))
    except Exception:                                # noqa: BLE001 脱敏兜底
        return s


def session_audit(session: Any) -> dict:
    """事件日志 → 审计报告(纯只读派生)。"""
    events = list(session.events_after(0))
    counts: dict[str, int] = {}
    usage = {"requests": 0, "in_tokens": 0, "out_tokens": 0}
    tools: dict[str, int] = {}
    guards = {"rejected": 0, "evaluated": 0}
    approvals: dict[str, int] = {}
    errors: list[str] = []
    first_ts = last_ts = None
    for ev in events:
        counts[ev.type] = counts.get(ev.type, 0) + 1
        ts = getattr(ev, "ts", None)
        if ts is not None:
            first_ts = first_ts or ts
            last_ts = ts
        p = ev.payload or {}
        if ev.type == "llm.usage":
            usage["requests"] += 1
            usage["in_tokens"] += int(p.get("in_tokens") or 0)
            usage["out_tokens"] += int(p.get("out_tokens") or 0)
        elif ev.type == "tool.result":
            tools[p.get("name", "?")] = tools.get(p.get("name", "?"), 0) + 1
        elif ev.type == "guard.rejected":
            guards["rejected"] += 1
        elif ev.type == "guard.evaluated":
            guards["evaluated"] += 1
        elif ev.type.startswith("approval."):
            key = ev.type.split(".", 1)[1]
            approvals[key] = approvals.get(key, 0) + 1
        elif ev.type == "system.error":
            errors.append(_brief(p.get("hint") or p.get("code") or ""))
    return {
        "session_id": getattr(session, "sid", ""),
        "seq_max": events[-1].seq if events else 0,
        "event_types": counts,
        "llm_usage": usage,
        "tools": tools,
        "guards": guards,
        "approvals": approvals,
        "errors": errors[:10],
        "span": {"first_ts": first_ts, "last_ts": last_ts},
    }


def export_json(session: Any, *, pretty: bool = True) -> str:
    """审计报告 JSON 导出(本地文件/CLI 输出用;无外发面)。"""
    audit = session_audit(session)
    return json.dumps(audit, ensure_ascii=False, indent=2 if pretty else None)


def session_summary_lines(session: Any) -> list[str]:
    """人读摘要行(CLI stats/桌面审计卡)。"""
    a = session_audit(session)
    u = a["llm_usage"]
    lines = [
        f"会话 {a['session_id']}:{a['seq_max']} 事件",
        f"LLM 请求 {u['requests']} 次(in {u['in_tokens']}/out {u['out_tokens']} tokens)",
    ]
    if a["tools"]:
        lines.append("工具调用: " + ", ".join(
            f"{k}×{v}" for k, v in sorted(a["tools"].items())))
    if a["approvals"]:
        lines.append("审批: " + ", ".join(
            f"{k}×{v}" for k, v in sorted(a["approvals"].items())))
    if a["errors"]:
        lines.append("错误: " + "; ".join(a["errors"][:3]))
    return lines


__all__ = ["session_audit", "export_json", "session_summary_lines"]
