"""session 编辑出口单测 — 契约:core/session.py edit_message(F063)。

覆盖:编辑追加 user.message_edited、投影 derive_messages 取新版留旧痕、
非法目标 EVT-101、空内容 EVT-100、last_user_message 定位。
"""
from __future__ import annotations

import pytest

from pyharness.core.message_edit import edit_user_message, last_user_message
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError


async def _session() -> SessionLog:
    s = SessionLog(sid="s-edit-000001")
    await s.append("session.created", {"title": "", "model": "mock"},
                   actor="system")
    return s


@pytest.mark.asyncio
async def test_edit_updates_projection():
    s = await _session()
    env = await s.append("user.message", {"content": "旧版问题"}, actor="user",
                         sync=True)
    await edit_user_message(s, env.seq, "新版问题")
    msgs = s.derive_messages()
    assert msgs[-1]["content"] == "新版问题"
    assert not any(m.get("content") == "旧版问题" for m in msgs)
    # 编辑事件入日志(append-only 留痕)
    types = [e.type for e in s.events_after(0)]
    assert "user.message_edited" in types


@pytest.mark.asyncio
async def test_edit_multiple_cumulative():
    s = await _session()
    e1 = await s.append("user.message", {"content": "v1"}, actor="user")
    await edit_user_message(s, e1.seq, "v2")
    await edit_user_message(s, e1.seq, "v3")
    msgs = s.derive_messages()
    assert msgs[-1]["content"] == "v3"


@pytest.mark.asyncio
async def test_edit_invalid_target():
    s = await _session()
    # 目标不存在
    with pytest.raises(PyHError) as e1:
        await edit_user_message(s, 999, "x")
    assert e1.value.code == "EVT-101"
    # 目标存在但不是用户消息(agent.message)
    await s.append("agent.message", {"content": "ai 回复"}, actor="agent")
    with pytest.raises(PyHError) as e2:
        await edit_user_message(s, 2, "改 AI 消息")
    assert e2.value.code == "EVT-101"


@pytest.mark.asyncio
async def test_edit_empty_rejected():
    s = await _session()
    env = await s.append("user.message", {"content": "hi"}, actor="user")
    with pytest.raises(PyHError) as e:
        await edit_user_message(s, env.seq, "   ")
    assert e.value.code == "EVT-100"


@pytest.mark.asyncio
async def test_last_user_message():
    s = await _session()
    await s.append("user.message", {"content": "第一条"}, actor="user")
    await s.append("user.message", {"content": "第二条"}, actor="user")
    last = last_user_message(s)
    assert last["content"] == "第二条"
