"""pyharness/core/auto_title.py — 会话自动标题(F042/#7 Consumer 面)。

run 正常完成后由 engine 钩子调用:取首条用户消息 → LLM 生成 ≤24 字标题 →
session.renamed(new_title, by="auto") 落盘(append-only;列表/提示词侧按最近
renamed 派生)。已命名会话幂等(日志有 renamed 即不再命名)。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pyharness.errors import PyHError

log = logging.getLogger("pyharness.auto_title")

TITLE_MAX = 64


def _first_user_text(ctx: Any) -> str:
    for ev in ctx.session.events_after(0):
        if ev.type == "user.message":
            return str(ev.payload.get("content") or "")[:500]
    return ""


async def auto_title(ctx: Any) -> Optional[str]:
    """为会话生成标题并落盘(session.renamed);失败/已命名 → None(幂等)。"""
    if any(e.type == "session.renamed" for e in ctx.session.events_after(0)):
        return None
    first = _first_user_text(ctx)
    if not first:
        return None
    prompt = (f"给这段会话起一个简洁中文标题,≤24 字,只输出标题本身,"
              f"不要引号/句号/解释:\n用户第一条消息: {first}")
    try:
        resp = await ctx.llm.chat([{"role": "user", "content": prompt}],
                                  tools=None, ctx=ctx)
    except PyHError as e:                        # LLM 失败:标题留空(下次再试)
        log.info("auto_title skipped code=%s", e.code)
        return None
    title = (resp.content or "").strip().strip('"“”\' ').splitlines()[0:1]
    title = (title[0] if title else "").strip()[:TITLE_MAX]
    if not title:
        return None
    await ctx.session.append("session.renamed",
                             {"new_title": title, "by": "auto"},
                             actor="system")
    return title


__all__ = ["auto_title"]
