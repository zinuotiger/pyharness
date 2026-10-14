"""ScheduleCalendar operations, separated from facade assembly."""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Optional, Union
from pyharness.errors import raise_code
from pyharness.core.schedule_models import MIN_INTERVAL_SECONDS, CRON_MAX_SCAN_DAYS, _CRON_BOUNDS, _SEG_RE, parse_iso, CronSpec, IntervalSpec, AtSpec, ScheduleJob

class ScheduleCalendar:
    """ScheduleCalendar responsibility; state is owned by the composing facade."""

    def _parse_spec(self, kind: str, expr: str, *,
                    require_future: bool = True) -> Union[CronSpec, IntervalSpec, AtSpec]:
        """按 kind 解析表达式(require_future=False 供回放重建 at 已过期场景)。"""
        if kind == "cron":                    # F048 核心:5 字段 cron
            if expr != expr.strip() or "\t" in expr:
                raise_code("CFG-601", hint=f"cron 表达式含首尾空白/制表符: {expr!r}",
                           advice="格式:分 时 日 月 周(如 '0 2 * * *'),禁首尾空白")
            parts = expr.split()
            if len(parts) != 5:               # 段数错误
                raise_code("CFG-601", hint=f"cron 须 5 字段: {expr}",
                           advice="格式:分 时 日 月 周(如 '0 2 * * *')")
            self._check_cron_fields(parts)
            return CronSpec(fields=tuple(parts))
        if kind == "interval":                # 固定间隔(秒)
            secs = self._as_int(expr)
            if secs is None or secs < MIN_INTERVAL_SECONDS:
                raise_code("CFG-601", hint=f"interval 须 ≥{MIN_INTERVAL_SECONDS} 秒: {expr}",
                           advice="F048 最小粒度 1 分钟")
            return IntervalSpec(seconds=secs)
        if kind == "at":                      # 绝对时间(一次性)
            dt = parse_iso(expr)
            if dt is None or (require_future and dt <= self._now()):
                raise_code("CFG-601", hint="at 时间须为未来的 ISO8601",
                           advice="示例:2026-09-08T10:30:00")
            return AtSpec(fire_at=dt)
        raise_code("EVT-100", hint=f"kind 非法: {kind}",
                   advice="kind ∈ cron/interval/at")

    @staticmethod
    def _as_int(expr: Any) -> Optional[int]:
        """整数解析(纯数字,含正负;非整数串 → None)。"""
        try:
            v = int(str(expr).strip())
        except (TypeError, ValueError):
            return None
        return v

    def _check_cron_fields(self, parts: list[str]) -> None:
        """5 字段逐一校验:段语法(数值/区间/列表/步长)与值域(越界 → CFG-601)。"""
        for field_, (lo, hi) in zip(parts, _CRON_BOUNDS):
            for seg in field_.split(","):
                if not seg or not _SEG_RE.match(seg):
                    raise_code("CFG-601", hint=f"cron 段非法: {field_!r}",
                               advice="段 ∈ 数值/a-b/a,b/*/*/n/a-b/n")
                m = _SEG_RE.match(seg)
                if m.group(1) == "*":
                    step = int(m.group(3) or 1)
                    if step < 1:
                        raise_code("CFG-601", hint=f"cron 步长非法: {seg}")
                    continue
                a = int(m.group(1))
                b = int(m.group(2)) if m.group(2) else a
                step = int(m.group(3) or 1)
                if a < lo or b > hi or a > b or step < 1:   # 值域/序/步长
                    raise_code("CFG-601", hint=f"cron 值域越界: {seg}"
                               f"(字段 {lo}-{hi})")

    @staticmethod
    def _field_match(field_: str, v: int) -> bool:
        """单字段匹配:*,a,a-b,列表,*/n,a-b/n 任一命中即真(注册期已校验语法)。"""
        for seg in field_.split(","):
            m = _SEG_RE.match(seg)
            if not m:
                continue                     # 防御:注册期已拒,绝不 True 误放
            step = int(m.group(3) or 1)
            if m.group(1) == "*":            # '*' 或 '*/n'
                if v % step == 0:
                    return True
                continue
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else a
            if a <= v <= b and (v - a) % step == 0:
                return True
        return False

    def _cron_match(self, spec: CronSpec, dt: datetime) -> bool:
        """cron 分钟匹配:五字段逐一命中(本地时钟;周 0=周日,数据表权威)。"""
        vals = (dt.minute, dt.hour, dt.day, dt.month, (dt.weekday() + 1) % 7)
        for field_, v in zip(spec.fields, vals):
            if not self._field_match(field_, v):
                return False
        return True

    def _spec_of(self, job: ScheduleJob) -> Union[CronSpec, IntervalSpec, AtSpec]:
        """job.spec 惰性解析(重建路径 at 已过期场景容错,require_future=False)。"""
        if job.spec is None:
            job.spec = self._parse_spec(job.kind, job.expr, require_future=False)
        return job.spec

    def next_fire(self, job: ScheduleJob, now_dt: datetime) -> Optional[datetime]:
        """按 kind 计算下次触发(cron 按日扫描;interval 按 last_fired 推进;
        at 一次性:fire_at 已过 → None,耗尽)。

        cron 无未来触发点(如 2/30)→ CFG-601 显式失败:注册期即拒,运行期不再
        产生"已注册 + next_fire_at=None + 永不触发"的静默状态(BUG-1 修复)。
        """
        spec = self._spec_of(job)
        if job.kind == "cron":
            return self._cron_next_fire(spec, job.expr, now_dt)
        if job.kind == "interval":            # 固定间隔推进(≥60s)
            base = job.last_fired_at or now_dt
            return base + timedelta(seconds=spec.seconds)
        return spec.fire_at if spec.fire_at > now_dt else None   # at:已过即 None

    def _cron_next_fire(self, spec: CronSpec, expr: str,
                        now_dt: datetime) -> datetime:
        """cron 下次触发:按日推进,命中日取当日最早的匹配 (时,分)。

        **不再逐分钟扫描**——旧实现上限 1440 分钟(24h),使"下次触发在 24h 之外"
        的合法表达式(工作日/每周/每月)一律得到 None → 永不触发(BUG-1)。此处
        按日推进把上限提到完整格里高利周期(CRON_MAX_SCAN_DAYS),整月不匹配时
        直接跳到下月 1 日,故 400 年上限的实际代价极小。

        界内无匹配 ⇒ 该表达式永远无触发点 → CFG-601 显式失败(不返回 None)。
        """
        minute_f, hour_f, dom_f, month_f, dow_f = spec.fields
        hours = [h for h in range(24) if self._field_match(hour_f, h)]
        minutes = [mi for mi in range(60) if self._field_match(minute_f, mi)]
        start = now_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        cursor = start.replace(hour=0, minute=0)
        for _ in range(CRON_MAX_SCAN_DAYS):
            if not self._field_match(month_f, cursor.month):
                # 整月不匹配:跳到下月 1 日,不逐日空转
                cursor = (cursor.replace(day=1)
                          + timedelta(days=32)).replace(day=1)
                continue
            if (self._field_match(dom_f, cursor.day)
                    and self._field_match(dow_f, (cursor.weekday() + 1) % 7)):
                for h in hours:                # 升序遍历 → 首个 ≥start 即当日最早
                    for mi in minutes:
                        cand = cursor.replace(hour=h, minute=mi)
                        if cand >= start:
                            return cand
            cursor += timedelta(days=1)
        raise_code("CFG-601", expr=expr,
                   hint=f"cron 无未来触发点(400 年内无匹配): {expr}",
                   advice="检查日/月组合是否存在(如 2 月 30 日不存在)")

    def _first_fire(self, kind: str, expr: str,
                    now_dt: datetime) -> Optional[datetime]:
        """注册时首次触发窗(= 未来最近一次触发;spec 伪码 _first_fire 落地)。"""
        spec = self._parse_spec(kind, expr, require_future=False)
        probe = ScheduleJob(name="", kind=kind, expr=expr, template={}, spec=spec)
        return self.next_fire(probe, now_dt)

    def _due(self, job: ScheduleJob, now_dt: datetime) -> bool:
        """触发窗判定:next_fire_at 已到(cron 额外要求当前分钟命中表达式)。"""
        if job.next_fire_at is None:
            return False
        if now_dt < job.next_fire_at:
            return False
        if job.kind == "cron":                # 分钟对齐:当前分钟须命中
            return self._cron_match(self._spec_of(job), now_dt)
        return True                           # interval/at:过窗即到点

    def _is_night(self, dt: datetime) -> bool:
        """深夜窗判定:23:00 ≤ hour 或 hour < 7:00(默认;可配 night_window)。"""
        return dt.hour >= self._night_start or dt.hour < self._night_end

