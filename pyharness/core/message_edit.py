"""pyharness/core/message_edit.py — 消息编辑/重发出口(F063,#13)。

SessionLog 公开方法面被 INV-01 钉死(append-only 只读派生 + append),故编辑
语义以本模块承载:经公开 append/get 追加 user.message_edited(投影 _fold_history
自动取新版留旧痕),不新增 SessionLog 方法面——INV-01 铁律不受触碰。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pyharness.errors import PyHError, raise_code

# EDT-001 = ERR.md §2.11 功能特性码(errors.py 故意不登记)⇒ 必须**直接构造**
# 保住字面码;用 raise_code 会被静默改写为 CYC-999(见 R19)。
EDIT_WINDOW_CODE = "EDT-001"


def _edit_window_s(session: Any) -> int:
    """编辑窗秒数(功能键 `loop.message_edit_window_s`;取不到 → L1 默认 1800)。

    R24:该键此前**零读取者**(L-20 死配置)⇒ 文档承诺的 `EDT-001`「编辑超 30 分钟
    窗拒写」在实现里**根本不存在**。现接到唯一编辑入口。
    """
    from pyharness.config import DEFAULTS

    default = int(DEFAULTS["loop"]["message_edit_window_s"])
    for holder in (getattr(session, "_cfg", None),
                   getattr(session, "settings", None),
                   getattr(session, "ctx", None)):
        loop = getattr(holder, "loop", None) if holder is not None else None
        win = getattr(loop, "message_edit_window_s", None)
        if win is not None:
            return int(win)
    return default


def _age_s(ts: Any) -> Optional[float]:
    """事件 ts(UTC ISO 毫秒 Z)距今秒数;不可解析 → None(不拦,如实降级)。"""
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


async def edit_user_message(session: Any, target_seq: int,
                            new_content: str) -> Any:
    """修正一条 user.message(编辑):追加 edited 事件,强同步落盘。

    target 必须是已落盘 user.message(EVT-101);空内容 EVT-100;**超出编辑窗
    `loop.message_edit_window_s`(默认 1800s)→ `EDT-001` 拒写**(R24 接线;此前该
    窗只有文档承诺、实现缺失)。原文仍在日志(append-only),审计可重建。
    """
    if not str(new_content or "").strip():
        raise_code("EVT-100", field="new_content", hint="修正内容不能为空")
    ev = session.get(int(target_seq))
    if ev is None or ev.type != "user.message":
        raise_code("EVT-101", seq=int(target_seq),
                   hint="目标不是可编辑的 user.message"
                        "(编辑面=用户消息;投影修正由 _fold_history 应用)")
    window = _edit_window_s(session)
    age = _age_s(getattr(ev, "ts", None))
    if window > 0 and age is not None and age > window:
        raise PyHError(EDIT_WINDOW_CODE, ctx={
            "seq": int(target_seq), "age_s": round(age, 3), "window_s": window,
            "advice": f"超过编辑窗 {window}s(loop.message_edit_window_s);"
                      "如需改历史请新发一条用户消息"})
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
