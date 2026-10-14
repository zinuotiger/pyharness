"""Task models: complete migrated implementation."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Optional


log = logging.getLogger("pyharness.task_queue")

DEFAULT_MAX_QUEUE = 32

def queue_kwargs_of(cfg: Any) -> dict:
    """由 Settings **鸭子取值**解析队深标量(F043 边界)。

    本模块**不 import** ``pyharness.config``(依赖注入纪律):只按属性读取。缺键 ⇒
    返回 ``{}``,``TaskQueue`` 回落模块常量 32,行为与修复前一致。同
    ``persistence.flush_kwargs_of`` / ``bus.bus_kwargs_of`` 先例。

    2026-09-21 修:``loop.task_queue_max`` 在 CFG §3.2 声明可调(1-1024)且带范围
    校验,但生产 5 处装配点一律用默认值 ⇒ **死配置**(本模块 docstring 曾写
    "CFG 锁死 32",与 CFG 表冲突;以 CFG 表为准 → 接线)。
    """
    v = getattr(getattr(cfg, "loop", None), "task_queue_max", None)
    return {"max_queue": v} if v is not None else {}

@dataclass
class Task:
    """队列任务条目:一次用户意图 = 一个任务(F043)。

    id = t-N 单调(auto)或显式指定(供 plan/schedule 溯源);meta = 附加上下文
    (plan 步/schedule 来源);enqueued_seq = 其 task.enqueued 事件的 seq(段归属锚)。
    终态只以事件为准,任务对象不含状态位(INV-01)。
    """

    id: str
    intent: str
    meta: Optional[dict] = None
    enqueued_seq: int = 0

@dataclass
class TaskResult:
    """任务终态结果(wait_for 返回):ok/code/summary/duration_ms。"""

    ok: bool
    code: Optional[str] = None       # 失败错误码或 "cancelled";成功为 None
    summary: Optional[str] = None    # 失败摘要(脱敏);成功缺省 None
    duration_ms: int = 0             # 执行耗时(毫秒;waiting 期取消为 0)

@dataclass
class QueueStatus:
    """队列状态快照(status 返回,纯读):供 UI/CLI 展示"后面还有几个"(F043)。"""

    running: Optional[str] = None
    waiting: list = field(default_factory=list)          # FIFO 顺序(task_id 列表)
    paused: bool = False
    pause_reasons: list = field(default_factory=list)    # 排序后的原因明细
    depth: int = 0

# Preserve historical import and pickle identities through the compatibility facade.
Task.__module__ = 'pyharness.core.task_queue'
TaskResult.__module__ = 'pyharness.core.task_queue'
QueueStatus.__module__ = 'pyharness.core.task_queue'
