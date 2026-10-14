"""pyharness/core/workflow.py — 工作流编排器(#45 Consumer 面)。

一句话职责:把意图序列(编排脚本/LLM 生成的步骤)依序交给任务队列逐条执行,
每步落 workflow.step 事件(started/done/failed,词表字段级)供 UI 进度与断点续跑。

submit_intent = 外壳注入适配器(desktop/CLI 各自:append user.message →
TaskQueue.submit → wait_for),本模块不持有执行细节;失败默认继续(记录),
stop_on_fail=True 则首败即停(编排语义二选一)。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.workflow")


def queue_submit_adapter(session: Any, queue: Any) -> Callable[[str], Awaitable]:
    """TaskQueue 适配器:user.message 强同步落盘 → submit → wait_for。

    与 engine runner seam 同构(调用方负责首落盘,队列承载编排),返回
    (ok: bool, summary: str)。"""

    async def _run(intent: str, *, workflow_step=None) -> tuple:
        await session.append("user.message", {"content": intent},
                             actor="user", sync=True)
        tid = await queue.submit(intent)
        if workflow_step is not None:
            await record_workflow_task(session, workflow_step, tid)
        res = await queue.wait_for(tid)
        result = (bool(getattr(res, "ok", False)), str(getattr(res, "summary", "") or "完成"))
        return (*result,tid) if workflow_step is not None else result
    return _run


class WorkflowRunner:
    """顺序编排器:步骤意图逐条投递,workflow.step 事件留痕。"""

    def __init__(self, session: Any, submit_intent: Callable[[str], Awaitable],
                 *, name: str = "main", bind_steps: bool = False) -> None:
        self.session = session
        self._submit = submit_intent
        self.name = name
        self.bind_steps = bind_steps

    async def run(self, steps: list, *, stop_on_fail: bool = False) -> list[dict]:
        """执行步骤序列;返回 [(index, intent, ok, summary)] 结果集。"""
        if not steps:
            raise_code("EVT-100", field="steps", hint="workflow 步骤为空")
        results: list[dict] = []
        for idx, intent in enumerate(steps):
            intent = str(intent or "").strip()
            if not intent:
                continue
            await self._step_event(idx, intent, "started")
            ok, summary, task_id = False, "", None
            try:
                if self.bind_steps:
                    result = await self._submit(intent, workflow_step=(self.name, idx, intent))
                    ok, summary = result[:2]
                    task_id = result[2] if len(result)>2 else None
                else:
                    ok, summary = await self._submit(intent)
            except Exception:                        # noqa: BLE001 单步异常记录
                # Transport exceptions may contain headers, URLs or credentials.
                # Detailed diagnostics belong to their existing bounded owner.
                summary = 'workflow_step_failed'
            await self._step_event(idx, intent,
                                   "done" if ok else "failed", summary[:2000], task_id=task_id)
            results.append({"index": idx, "intent": intent, "ok": ok,
                            "summary": summary})
            if stop_on_fail and not ok:
                break
        return results

    async def _step_event(self, idx: int, action: str, state: str,
                          note: Optional[str] = None, *, task_id=None) -> None:
        # state 保持枚举纯净(started/done/failed);note 仅日志,不进载荷
        if note:
            log.debug("workflow step note wf=%s idx=%s state=%s: %s",
                      self.name, idx, state, note[:300])
        await self.session.append("workflow.step",
                                  {"wf_name": self.name, "step_idx": idx,
                                   "action": action[:200], "state": state,
                                   **({'task_id':task_id} if task_id else {})},
                                  actor="system")


async def record_workflow_task(session, step, task_id):
    """Append the actual queue identity while the submitted step is still running."""
    name, idx, intent = step
    await session.append('workflow.step', {'wf_name':name, 'step_idx':idx,
        'action':intent[:200], 'state':'started', 'task_id':task_id}, actor='system')


__all__ = ["WorkflowRunner", "queue_submit_adapter", "record_workflow_task"]
