"""Small ownership helpers for asynchronous public operations."""
from __future__ import annotations

import asyncio
from functools import wraps

from pyharness.errors import raise_code


def admitted(fn):
    """Track nested public calls until handoff; closing rejects new callers."""
    @wraps(fn)
    async def call(self, *args, **kwargs):
        task = asyncio.current_task()
        active = getattr(self, '_admissions', None)
        if active is None:
            active = self._admissions = {}
        if getattr(self, '_closing', False) and task not in active:
            raise_code('EVT-104', reason='owner_closing')
        active[task] = active.get(task, 0) + 1
        try:
            return await fn(self, *args, **kwargs)
        finally:
            active[task] -= 1
            if not active[task]: active.pop(task)
    return call


async def stop_admissions(owner):
    owner._closing = True
    tasks = [task for task in getattr(owner, '_admissions', {})
             if task is not asyncio.current_task()]
    for task in tasks:
        if not task.done() and not task.cancelling(): task.cancel()
    return await asyncio.gather(*tasks, return_exceptions=True)
