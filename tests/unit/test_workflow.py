"""workflow 编排器单测 — 契约:core/workflow.py(#45)。

覆盖:步骤依序执行(started/done 事件序列)、失败步记录并继续(默认)、
stop_on_fail 即停、空步骤 EVT-100、queue_submit_adapter 落盘+wait_for 面。
执行用 TaskQueue + FakeRunner(确定性,零 LLM)。
"""
from __future__ import annotations

import asyncio

import pytest

from pyharness.core.session import SessionLog
from pyharness.core.task_queue import TaskQueue
from pyharness.core.workflow import WorkflowRunner, queue_submit_adapter
from pyharness.errors import PyHError


class _FakeRunner:
    """runner 替身:成功或单次故障注入,段内落 agent.message。"""

    def __init__(self, session, fail_first: bool = False) -> None:
        self.session = session
        self.fail_first = fail_first
        self.failed = False

    async def run_for_task(self, task):
        if self.fail_first and not self.failed:
            self.failed = True
            raise PyHError("CYC-999", ctx={"hint": "注入失败"})
        await self.session.append("agent.message",
                                  {"content": f"[{task.id}] ok"}, actor="agent",
                                  task_id=task.id)


async def _session() -> SessionLog:
    s = SessionLog(sid="s-wf-000001")
    await s.append("session.created", {"title": "", "model": "mock"},
                   actor="system")
    return s


def _wf_types(s) -> list:
    return [(e.payload.get("step_idx"), e.payload.get("state"))
            for e in s.events_after(0) if e.type == "workflow.step"]


@pytest.mark.asyncio
async def test_steps_run_in_order():
    s = await _session()
    q = TaskQueue(s, runner=_FakeRunner(s), max_queue=16)
    runner = WorkflowRunner(s, queue_submit_adapter(s, q), name="demo")
    results = await runner.run(["第一步", "第二步", "第三步"])
    assert len(results) == 3 and all(r["ok"] for r in results)
    assert [r["index"] for r in results] == [0, 1, 2]
    # workflow.step 事件:每步 started+done
    steps = _wf_types(s)
    assert len(steps) == 6
    assert steps[0] == (0, "started") and steps[1] == (0, "done")


@pytest.mark.asyncio
async def test_failure_continues_by_default():
    s = await _session()
    q = TaskQueue(s, runner=_FakeRunner(s, fail_first=True), max_queue=16)
    runner = WorkflowRunner(s, queue_submit_adapter(s, q))
    results = await runner.run(["会失败", "能成功"])
    assert results[0]["ok"] is False
    assert results[1]["ok"] is True
    states = [st for _, st in _wf_types(s)]
    assert any(st.startswith("fail") for st in states)
    assert states.count("done") == 1


@pytest.mark.asyncio
async def test_stop_on_fail():
    s = await _session()
    q = TaskQueue(s, runner=_FakeRunner(s, fail_first=True), max_queue=16)
    runner = WorkflowRunner(s, queue_submit_adapter(s, q))
    results = await runner.run(["会失败", "不跑"], stop_on_fail=True)
    assert len(results) == 1 and results[0]["ok"] is False


@pytest.mark.asyncio
async def test_empty_steps_rejected():
    s = await _session()
    q = TaskQueue(s, runner=_FakeRunner(s), max_queue=16)
    runner = WorkflowRunner(s, queue_submit_adapter(s, q))
    with pytest.raises(PyHError) as e:
        await runner.run([])
    assert e.value.code == "EVT-100"
