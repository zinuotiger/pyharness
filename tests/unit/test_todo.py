"""todo 管理器单测 — 契约:core/todo.py(#32 待办清单)。

覆盖:op 分发(add/list/done/remove/clear_done)、事件落点(todo.updated 整表
快照)、id 定位异常(EVT-101)、非法 op/缺 text/超上限(EVT-100)、事件重建
(last-wins 重放 + id 续号)、渲染面。
"""
from __future__ import annotations

import pytest

from pyharness.core.todo import DEFAULT_TASK_ID, MAX_ITEMS, TodoManager
from pyharness.errors import PyHError


class _FakeSession:
    """记录型会话替身:append 记录事件,返回带 seq 的鸭子信封。"""

    def __init__(self) -> None:
        self.events: list = []
        self._seq = 0

    async def append(self, type_: str, payload: dict, *, actor: str,
                     sync: bool = False, trace: dict = None) -> object:
        self._seq += 1
        env = _Env(self._seq, type_, dict(payload))
        self.events.append(env)
        return env


class _Env:
    def __init__(self, seq, type_, payload) -> None:
        self.seq = seq
        self.type = type_
        self.payload = payload


@pytest.mark.asyncio
async def test_add_list_done_flow():
    sess = _FakeSession()
    mgr = TodoManager(session=sess)
    out1 = await mgr.apply_op("add", text="读 specs/agent_loop.py.md")
    assert "[1]" in out1
    out2 = await mgr.apply_op("add", text="跑 agent_loop 单测")
    assert "共 2 项" in out2
    # 事件整表快照(事件溯源):2 条 todo.updated
    assert len(sess.events) == 2
    assert sess.events[-1].type == "todo.updated"
    assert len(sess.events[-1].payload["todos"]) == 2
    # list 只读(不产生事件)
    listed = await mgr.apply_op("list")
    assert "读 specs" in listed and "(2)" in listed
    assert len(sess.events) == 2
    # done
    out3 = await mgr.apply_op("done", todo_id=1)
    assert "已完成" in out3
    assert mgr.active_count() == 1
    assert mgr.list_items()[0]["done"] is True
    # done 幂等
    await mgr.apply_op("done", todo_id=1)
    assert mgr.active_count() == 1


@pytest.mark.asyncio
async def test_remove_and_clear_done():
    sess = _FakeSession()
    mgr = TodoManager(session=sess)
    await mgr.apply_op("add", text="a")
    await mgr.apply_op("add", text="b")
    await mgr.apply_op("done", todo_id=1)
    out = await mgr.apply_op("remove", todo_id=1)
    assert "已移除" in out
    assert len(mgr.list_items()) == 1               # 剩 b(id=2,active)
    await mgr.apply_op("add", text="c")             # c id=3
    await mgr.apply_op("done", todo_id=3)           # c done
    cl = await mgr.apply_op("clear_done")           # 只清 c,b 仍 active
    assert "1 项已完成" in cl and "剩 1 项" in cl
    assert [x["text"] for x in mgr.list_items()] == ["b"]


@pytest.mark.asyncio
async def test_error_codes():
    sess = _FakeSession()
    mgr = TodoManager(session=sess)
    with pytest.raises(PyHError) as e1:
        await mgr.apply_op("frobnicate")
    assert e1.value.code == "EVT-100"
    with pytest.raises(PyHError) as e2:
        await mgr.apply_op("done", todo_id=99)
    assert e2.value.code == "EVT-101"
    with pytest.raises(PyHError) as e3:
        await mgr.apply_op("add", text="   ")
    assert e3.value.code == "EVT-100"
    # 未注入 session:写操作拒(只读实例)
    ro = TodoManager(session=None)
    with pytest.raises(PyHError) as e4:
        await ro.apply_op("add", text="x")
    assert e4.value.code == "EVT-100"


@pytest.mark.asyncio
async def test_max_items_cap():
    sess = _FakeSession()
    mgr = TodoManager(session=sess, max_items=2)
    await mgr.apply_op("add", text="a")
    await mgr.apply_op("add", text="b")
    with pytest.raises(PyHError) as e:
        await mgr.apply_op("add", text="c")
    assert e.value.code == "EVT-100"


def test_rebuild_from_events():
    sess = _FakeSession()

    async def seed():
        mgr = TodoManager(session=sess)
        await mgr.apply_op("add", text="旧项")
        await mgr.apply_op("add", text="次项")
        await mgr.apply_op("done", todo_id=1)
    import asyncio
    asyncio.run(seed())
    rebuilt = TodoManager.rebuild_from_events(sess.events)
    items = rebuilt.list_items()
    assert len(items) == 2
    assert items[0]["done"] is True
    # id 续号:重建后新 id 不重号
    asyncio.run(_post_rebuild_add(rebuilt))
    assert any(x["id"] == 3 for x in rebuilt.list_items())


async def _post_rebuild_add(mgr: TodoManager) -> None:
    sess = _FakeSession()
    mgr.session = sess
    await mgr.apply_op("add", text="新项")


def test_task_scoped_and_default():
    sess = _FakeSession()

    async def seed():
        mgr = TodoManager(session=sess)
        await mgr.apply_op("add", text="主任务项")
        await mgr.apply_op("add", text="子任务项", task_id="t-2")
    import asyncio
    asyncio.run(seed())
    assert len(TodoManager.rebuild_from_events(sess.events)
               .list_items(DEFAULT_TASK_ID)) == 1
