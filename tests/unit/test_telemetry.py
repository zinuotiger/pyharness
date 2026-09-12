"""telemetry 本地审计单测 — 契约:core/telemetry.py(#66)。

覆盖:类型计数、llm.usage 折叠、工具明细、guard/approval 统计、system.error
收集、JSON 导出与摘要行。纯只读派生(事件日志为唯一数据源)。
"""
from __future__ import annotations

import pytest

from pyharness.core.session import SessionLog
from pyharness.core.telemetry import export_json, session_audit, \
    session_summary_lines


async def _session() -> SessionLog:
    s = SessionLog(sid="s-tel-000001")
    await s.append("session.created", {"title": "", "model": "mock"},
                   actor="system")
    await s.append("user.message", {"content": "查天气"}, actor="user", sync=True)
    await s.append("llm.request", {"model": "deepseek-chat", "n_tools": 2},
                   actor="llm")
    await s.append("llm.usage",
                   {"model": "deepseek-chat", "in_tokens": 100,
                    "out_tokens": 25, "cache_hit": 0, "cost_est": 0.001},
                   actor="llm")
    await s.append("tool.result",
                   {"name": "web.search", "call_id": "c1", "ok": True,
                    "summary": "ok", "truncated": False, "spill_ref": None},
                   actor="tool")
    await s.append("guard.rejected",
                   {"tool": "fs.delete_file", "guard_id": "g-danger",
                    "reason": "danger-critical"},
                   actor="tool", sync=True)
    await s.append("approval.requested",
                   {"tool": "exec.shell_run",
                    "args_summary": "rm -rf /", "risk": "high",
                    "ttl_ms": 120000},
                   actor="tool", sync=True)
    await s.append("approval.denied", {"approval_id": 1, "by": "user"},
                   actor="system", sync=True)
    await s.append("system.error", {"code": "CYC-999",
                                    "hint": "测试错误留痕"}, actor="system")
    return s


@pytest.mark.asyncio
async def test_audit_counts_and_folds():
    s = await _session()
    a = session_audit(s)
    assert a["session_id"] == "s-tel-000001"
    assert a["event_types"]["llm.usage"] == 1
    assert a["event_types"]["user.message"] == 1
    assert a["llm_usage"] == {"requests": 1, "in_tokens": 100, "out_tokens": 25}
    assert a["tools"] == {"web.search": 1}
    assert a["guards"] == {"rejected": 1, "evaluated": 0}
    assert a["approvals"] == {"requested": 1, "denied": 1}
    assert a["errors"] and "测试错误留痕" in a["errors"][0]
    assert a["seq_max"] == s.stats()["seq"]


@pytest.mark.asyncio
async def test_export_and_summary():
    s = await _session()
    text = export_json(s)
    assert '"session_id": "s-tel-000001"' in text
    lines = session_summary_lines(s)
    joined = "\n".join(lines)
    assert "LLM 请求 1 次" in joined
    assert "web.search×1" in joined
    assert "denied×1" in joined
