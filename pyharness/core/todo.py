"""pyharness/core/todo.py — 会话级待办 TodoManager(#32 清单项)。

职责一句话:事件源派生的待办表(todo.updated 为唯一写事件,append-only 真源),
LLM 经 todo.* 工具消费(ctx.todos),外壳/桌面经同一管理器读看板。

设计约束:
- 状态只由事件派生:每次变更 append 一条 todo.updated {task_id, todos[]}(整表
  快照语义 = 简易事件溯源;EVENT-SCHEMA TodoUpdatedPayload 字段级权威);
- 单任务会话语义:task_id 缺省 "main"(与 goal 关联任务同口径,1 会话 1 任务);
- 每任务 ≤20 项(词表 max_length 同源);done 项保留在表内(compaction 清空);
- append 强类型二次校验(词表层),payload 非法即 EVT-100 拒写——不落脏事件。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.todo")

DEFAULT_TASK_ID = "main"
MAX_ITEMS = 20
"""每任务待办上限(与 TodoUpdatedPayload.todos max_length=20 同源)。"""

_OPS = ("add", "list", "done", "remove", "clear_done")


class TodoManager:
    """会话级待办注册表:add/list/done/remove/clear_done + 事件重建。

    session = SessionLog 或同型 async append 协议(写事件落点);缺失时写操作
    EVT-100 拒(只读重建实例)。_todos 为 {task_id: [TodoItem 型 dict]} 内存态,
    可由事件全量重建(INV-01 不持第二份事实源)。"""

    def __init__(self, session: Any = None, *, max_items: int = MAX_ITEMS) -> None:
        self.session = session
        self._max_items = int(max_items)
        self._todos: dict[str, list[dict]] = {}
        self._id_seq: int = 0

    # ============================================================ 只读看板
    def list_items(self, task_id: str = DEFAULT_TASK_ID) -> list[dict]:
        """某任务待办(副本,只读;按添加序)。"""
        return [dict(x) for x in self._todos.get(task_id, [])]

    def active_count(self, task_id: str = DEFAULT_TASK_ID) -> int:
        """未完成项计数(看板/预算徽章数据源)。"""
        return sum(1 for x in self._todos.get(task_id, []) if not x.get("done"))

    # ============================================================ 写操作
    async def apply_op(self, op: str, *, text: Optional[str] = None,
                       todo_id: Optional[int] = None,
                       task_id: Optional[str] = None) -> str:
        """op 分发(LLM 工具面):返回用户可读 summary 文本(executor 关4 直用)。

        op ∈ add/list/done/remove/clear_done;非法 op → EVT-100;id 不存在 →
        EVT-101(与 goal 定位异常同族)。list 为只读(不 append)。"""
        tid = task_id or DEFAULT_TASK_ID
        if op not in _OPS:
            raise_code("EVT-100", hint=f"未知 todo op: {op}",
                       advice=f"op ∈ {_OPS}")
        if op == "list":
            return self._render(tid)
        sess = self._require_session()
        items = self._todos.setdefault(tid, [])
        if op == "add":
            text = str(text or "").strip()
            if not text:
                raise_code("EVT-100", hint="todo add 缺 text", field="text")
            if len(items) >= self._max_items:
                raise_code("EVT-100", hint=f"待办超上限({self._max_items})",
                           advice="先 done/remove 再添加")
            self._id_seq += 1
            items.append({"id": self._id_seq, "text": text, "done": False})
            await self._emit(tid)
            return f"已添加待办 [{self._id_seq}] {text}(共 {len(items)} 项)"
        # 其余 op 先定位 id(不存在 → EVT-101)
        if op == "done":
            for x in items:
                if x["id"] == int(todo_id or -1):
                    x["done"] = True
                    await self._emit(tid)
                    return f"待办 [{x['id']}] {x['text']} 已完成"
            raise_code("EVT-101", todo_id=todo_id, hint="todo id 不存在")
        if op == "remove":
            for i, x in enumerate(items):
                if x["id"] == int(todo_id or -1):
                    items.pop(i)
                    await self._emit(tid)
                    return f"已移除待办 [{x['id']}] {x['text']}"
            raise_code("EVT-101", todo_id=todo_id, hint="todo id 不存在")
        if op == "clear_done":
            kept = [x for x in items if not x.get("done")]
            dropped = len(items) - len(kept)
            self._todos[tid] = kept
            await self._emit(tid)
            return f"已清理 {dropped} 项已完成待办(剩 {len(kept)} 项)"
        raise_code("EVT-100", hint=f"未知 todo op: {op}")  # pragma: no cover

    # ============================================================ 事件落点
    async def _emit(self, task_id: str) -> None:
        """todo.updated 强类型落盘(词表二次校验;失败上抛不静默)。"""
        await self._require_session().append(
            "todo.updated",
            {"task_id": task_id, "todos": list(self._todos.get(task_id, []))},
            actor="tool")

    def _require_session(self) -> Any:
        if self.session is None:
            raise_code("EVT-100", hint="TodoManager 未注入 session,无法写事件",
                       advice="构造时注入 SessionLog 或同型 append 协议对象")
        return self.session

    # ============================================================ 渲染
    def _render(self, task_id: str = DEFAULT_TASK_ID) -> str:
        items = self._todos.get(task_id, [])
        if not items:
            return "当前无待办"
        lines = [f"- [{'x' if x['done'] else ' '}] ({x['id']}) {x['text']}"
                 for x in items]
        return "\n".join(lines)

    # ============================================================ 事件重建
    @classmethod
    def rebuild_from_events(cls, events: list[Any]) -> "TodoManager":
        """从事件流重建(崩溃恢复/新 ctx 接旧会话):逐条 todo.updated 重放
        (last-wins 整表覆盖);未注入 session(只读实例,写操作将拒)。"""
        mgr = cls(session=None)
        for ev in events:
            if getattr(ev, "type", "") != "todo.updated":
                continue
            p = getattr(ev, "payload", {}) or {}
            tid = p.get("task_id") or DEFAULT_TASK_ID
            raw = p.get("todos") or []
            mgr._todos[tid] = [dict(x) for x in raw]
            for x in mgr._todos[tid]:           # id 续号(单调不重号)
                mgr._id_seq = max(mgr._id_seq, int(x.get("id") or 0))
        return mgr


__all__ = ["TodoManager", "DEFAULT_TASK_ID", "MAX_ITEMS"]
