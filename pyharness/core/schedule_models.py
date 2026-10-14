"""Schedule models: complete migrated implementation."""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional


log = logging.getLogger("pyharness.schedule")

JobKind = Literal["cron", "interval", "at"]

MIN_INTERVAL_SECONDS = 60          # interval 下限:F048 最小粒度 1 分钟

CRON_MAX_SCAN_DAYS = 366 * 400 + 1

DEFAULT_NIGHT_WINDOW = (23, 7)     # 深夜禁触窗:23:00 ≤ h 或 h < 7:00

_CRON_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))   # 分 时 日 月 周

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

_SEG_RE = re.compile(r"(\*|\d+)(?:-(\d+))?(?:/(\d+))?$")

def now() -> datetime:
    """模块统一时钟(本地时区朴素 datetime;测试经 monkeypatch 本函数注入)。"""
    return datetime.now()

def iso(dt: Optional[datetime]) -> Optional[str]:
    """datetime → ISO8601 本地串(None 透传;事件载荷用)。"""
    return dt.isoformat() if dt is not None else None

def parse_iso(text: Optional[str]) -> Optional[datetime]:
    """ISO8601 → 本地朴素 datetime;带时区偏移转本地;解析失败/空 → None。"""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:               # 带偏移 → 转本地后去时区(时间一律本地)
        dt = dt.astimezone().replace(tzinfo=None)
    return dt

@dataclass(frozen=True)
class CronSpec:
    """cron 表达式解析结果:5 原始字段(分 时 日 月 周;注册期已校验)。"""

    fields: tuple[str, ...]

@dataclass(frozen=True)
class IntervalSpec:
    """固定间隔(秒,≥60)。"""

    seconds: int

@dataclass(frozen=True)
class AtSpec:
    """绝对时间(一次性,触发后移除)。"""

    fire_at: datetime

@dataclass
class ScheduleJob:
    """注册表条目:job 定义 + 触发窗口状态(状态只由事件回放重建,INV-01)。

    spec: 解析后的 CronSpec/IntervalSpec/AtSpec(实现附加缓存,注册/重建时惰性填充;
    next_fire_at 快照等其余字段与数据表一一对应)。
    """

    name: str
    kind: JobKind
    expr: str
    template: dict                        # {intent 必填, meta 可选}
    is_risky: bool = False
    paused: bool = False
    next_fire_at: Optional[datetime] = None
    last_fired_at: Optional[datetime] = None
    created_seq: int = 0                  # = schedule.registered 事件 seq
    spec: Optional[Any] = None            # 解析缓存(重建/惰性)

@dataclass
class JobInfo:
    """list_jobs 输出快照(纯读;triggered/missed 由事件回放聚合)。"""

    name: str
    kind: str
    expr: str
    paused: bool
    next_fire_at: Optional[datetime]
    last_fired_at: Optional[datetime]
    triggered: int                        # schedule.trigger 事件计数
    missed: int                           # schedule.missed payload.missed 累计

# Preserve historical import and pickle identities through the compatibility facade.
CronSpec.__module__ = 'pyharness.core.schedule'
IntervalSpec.__module__ = 'pyharness.core.schedule'
AtSpec.__module__ = 'pyharness.core.schedule'
ScheduleJob.__module__ = 'pyharness.core.schedule'
JobInfo.__module__ = 'pyharness.core.schedule'
