"""pyharness/core/message_edit.py — 消息编辑/重发出口(F063,#13)。

SessionLog 公开方法面被 INV-01 钉死(append-only 只读派生 + append),故编辑
语义以本模块承载:经公开 append/get 追加 user.message_edited(投影 _fold_history
自动取新版留旧痕),不新增 SessionLog 方法面——INV-01 铁律不受触碰。
"""
from __future__ import annotations

from typing import Any, Optional

from pyharness.errors import raise_code


async def edit_user_message(session: Any, target_seq: int,
                            new_content: str) -> Any:
    """修正一条 user.message(编辑):追加 edited 事件,强同步落盘。

    target 必须是已落盘 user.message(EVT-101);空内容 EVT-100。原文仍在日志
    (append-only),审计可重建;派生视图取新版。"""
    if not str(new_content or "").strip():
        raise_code("EVT-100", field="new_content", hint="修正内容不能为空")
    ev = session.get(int(target_seq))
    if ev is None or ev.type != "user.message":
        raise_code("EVT-101", seq=int(target_seq),
                   hint="目标不是可编辑的 user.message"
                        "(编辑面=用户消息;投影修正由 _fold_history 应用)")
    return await session.append("user.message_edited",
                                {"target_seq": int(target_seq),
                                 "new_content": str(new_content)},
                                actor="user", sync=True)


def last_user_message(session: Any) -> Optional[dict]:
    """最近一条 user.message 摘要(重发用;只读,无状态)。"""
    for ev in reversed(list(session.events_after(0))):
        if ev.type == "user.message":
            return {"seq": ev.seq, "content": ev.payload.get("content", "")}
    return None


__all__ = ["edit_user_message", "last_user_message"]
