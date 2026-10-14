"""Cli runtime: complete migrated implementation."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from pyharness.errors import raise_code
from pyharness.cli_output import log, ShellCtx


def _wire_queue(ns: SimpleNamespace) -> None:
    """任务队列接线(执行器注入面:runner=None 时任务按 CYC-999 快速失败,防
    S-1 伪造已执行;agent_loop.run_for_task 由引擎装配注入)。"""
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core.task_queue import TaskQueue, queue_kwargs_of
    ns.task_queue = TaskQueue(
        session=ns.session, runner=None,
        **queue_kwargs_of(getattr(ns, "settings", None)))  # F043 队深同源注入
    try:
        ns.approval = ApprovalProvider(session=ns.session, bus=ns.bus)
    except Exception:                          # noqa: BLE001 审批服务装配失败不阻断
        log.warning("approval provider 装配失败,审批降级为无通道自动拒", exc_info=True)

