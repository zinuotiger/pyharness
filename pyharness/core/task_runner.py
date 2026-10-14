"""Executor and lifecycle contracts. Core does not import platform policy."""
from __future__ import annotations

from typing import Awaitable, Callable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from pyharness.core.task_queue import Task
    from pyharness.core.session import SessionLog
    from pyharness.events import Envelope


class RunnerResult(Protocol):
    reason: str


class TaskRunner(Protocol):
    async def run_for_task(self, task: Task) -> RunnerResult | None: ...
    async def cancel_current(self, task_id: str | None = None) -> None: ...


class ExecutionContext(Protocol):
    session: SessionLog
    task_id: str | None


class TaskLifecycle(Protocol):
    async def run_task(self, event: Envelope, context: ExecutionContext,
                      invoke: Callable[[], Awaitable[RunnerResult | None]]) -> RunnerResult | None: ...
