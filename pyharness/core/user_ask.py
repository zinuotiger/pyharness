"""pyharness/core/user_ask.py — agent 反问用户(带选项,#38 Provider 面)。

一句话职责:工具轮内 agent 抛问题 → 事件落盘(user.question,ask_id=seq)→
外壳(桌面/CLI)轮询 pending → 人答复(user.answer)→ 本 Provider settle 并
把答复文本回喂 LLM。语义与 approval 同构(请求事件即事实,答复事件强同步),
但无权限语义(反问不拦执行,只是要信息)——所以独立成面,不并入 guard。

阻塞口径:request() 等待答复至多 ttl_s(默认 120s,事件载荷可下调);超时返回
占位提示(不抛错——对话继续,LLM 据"用户未答复"改口或换路径)。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.user_ask")

DEFAULT_TTL_S = 120


class AskProvider:
    """会话级反问宿主:request(挂 pending+落事件+等待)/answer(裁决落事件)。"""

    def __init__(self, *, session: Any = None, bus: Any = None) -> None:
        self._session = session
        self._bus = bus
        self._pending: dict[int, asyncio.Event] = {}
        self._answers: dict[int, dict] = {}

    # ------------------------------------------------------------ 请求面
    async def request(self, question: str, options: Optional[list] = None,
                      *, ttl_s: int = DEFAULT_TTL_S) -> str:
        """抛问题给用户:落 user.question → 等待答复 → 返回答复文本。

        options 为拍板候选(≤10 项);答复可能不在候选中(自由文本)。
        会话未接线(EVT-100)或等待超时(占位文本,不抛)两种收敛。"""
        sess = self._session
        if sess is None:
            raise_code("EVT-100", hint="AskProvider 未注入 session(user.ask 不可用)")
        env = await sess.append(
            "user.question",
            {"question": str(question)[:1000],
             "options": [str(o)[:200] for o in (options or [])][:10],
             "ttl_s": max(5, min(3600, int(ttl_s)))},
            actor="agent", sync=True)
        ask_id = env.seq
        waiter = asyncio.Event()
        self._pending[ask_id] = waiter
        try:
            try:
                await asyncio.wait_for(waiter.wait(), timeout=max(5, int(ttl_s)))
            except asyncio.TimeoutError:
                log.info("user.ask timeout ask_id=%s", ask_id)
                return "(用户未在时限内答复,如需继续请说明或自行决策)"
            ans = self._answers.pop(ask_id, {})
            choice = ans.get("choice")
            if choice is not None and choice:
                return f"用户选择: {choice}"
            return f"用户答复: {ans.get('text') or '(空)'}"
        finally:
            self._pending.pop(ask_id, None)

    # ------------------------------------------------------------ 答复面
    def answer(self, ask_id: int, *, choice: Optional[str] = None,
               text: Optional[str] = None, by: str = "user") -> bool:
        """桌面/CLI 裁决桥:答复落盘(user.answer 强同步)+ settle 等待者。

        未知 ask_id(已答/超时/过期)→ 返回 False(幂等忽略,防重放)。"""
        aid = int(ask_id)
        waiter = self._pending.pop(aid, None)
        if waiter is None:
            return False
        choice_v = (str(choice or "").strip())[:500] or None
        text_v = (str(text or "").strip())[:2000] or None
        if not choice_v and not text_v:
            raise_code("EVT-100", field="answer",
                       hint="答复须给 choice 或 text 至少其一")
        if self._session is not None:
            coro = self._write_answer_and_settle(
                aid, waiter, choice_v, text_v, by)
            try:
                asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                coro.close()
                self._pending[aid] = waiter
                return False
        else:
            self._finish_answer(aid, waiter, choice_v, text_v)
        return True

    async def answer_async(self, ask_id: int, *, choice: Optional[str] = None,
                           text: Optional[str] = None, by: str = "user") -> bool:
        """异步答复:先强同步落 user.answer,再唤醒等待者并返回成功。"""
        aid = int(ask_id)
        waiter = self._pending.pop(aid, None)
        if waiter is None:
            return False
        choice_v = (str(choice or "").strip())[:500] or None
        text_v = (str(text or "").strip())[:2000] or None
        if not choice_v and not text_v:
            raise_code("EVT-100", field="answer",
                       hint="答复须给 choice 或 text 至少其一")
        if self._session is not None:
            try:
                await self._session.append(
                    "user.answer",
                    {"ask_id": aid, "choice": choice_v, "text": text_v,
                     "by": str(by or "user")[:64]},
                    actor="user", sync=True)
            except Exception:
                self._pending[aid] = waiter       # 落盘失败:恢复可重试状态
                raise
        self._finish_answer(aid, waiter, choice_v, text_v)
        return True

    async def _write_answer_and_settle(self, aid: int, waiter: asyncio.Event,
                                       choice: Optional[str], text: Optional[str],
                                       by: str) -> None:
        """同步 answer 的后台强同步路径:失败时恢复 waiter 供再次答复。"""
        try:
            await self._session.append(
                "user.answer",
                {"ask_id": aid, "choice": choice, "text": text,
                 "by": str(by or "user")[:64]},
                actor="user", sync=True)
        except Exception as exc:                 # noqa: BLE001 异步后台入口
            self._pending[aid] = waiter
            log.warning("user.answer 留痕失败: %s", exc)
            return
        self._finish_answer(aid, waiter, choice, text)

    def _finish_answer(self, aid: int, waiter: asyncio.Event,
                       choice: Optional[str], text: Optional[str]) -> None:
        self._answers[aid] = {"choice": choice, "text": text}
        if not waiter.is_set():
            waiter.set()

    # ------------------------------------------------------------ 只读
    def pending_list(self) -> list[dict]:
        """未决反问清单(桌面/CLI 轮询源;排序稳定按 ask_id)。"""
        out = []
        sess = self._session
        if sess is None:
            return out
        try:
            evs = list(sess.events_after(0))
        except Exception:                            # noqa: BLE001
            return out
        for ev in evs:
            if ev.type != "user.question":
                continue
            p = ev.payload or {}
            if int(ev.seq) in self._pending:
                out.append({"ask_id": ev.seq,
                            "question": p.get("question", ""),
                            "options": list(p.get("options") or []),
                            "ttl_s": p.get("ttl_s", DEFAULT_TTL_S)})
        return out

__all__ = ["AskProvider", "DEFAULT_TTL_S"]
