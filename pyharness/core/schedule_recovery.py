"""ScheduleRecovery operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyharness.core.schedule import Scheduler
import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional
from pyharness.errors import PyHError
from pyharness.core.schedule_models import log, iso, parse_iso, ScheduleJob

class ScheduleRecovery:
    """ScheduleRecovery responsibility; state is owned by the composing facade."""

    def _missed_window(self, job: ScheduleJob, now_dt: datetime) -> bool:
        """宕机窗判定:持久化 next_fire_at 已过(窗口落进宕机期)→ 错过。"""
        return (job.next_fire_at is not None and job.next_fire_at <= now_dt)

    def _missed_count(self, job: ScheduleJob, now_dt: datetime) -> int:
        """宕机窗内错过的触发机会数(合并计数;PRD:只记 missed 不补跑)。

        cron:窗口 [next_fire_at, now_dt] 内命中表达式且到点的分钟数(上限 10 万分钟
        防极端长宕机死循环);interval:过窗周期数;at:1(一次性)。
        """
        nxt = job.next_fire_at
        if nxt is None:
            return 0
        if job.kind == "at":
            return 1
        spec = self._spec_of(job)
        if job.kind == "interval":
            span = (now_dt - nxt).total_seconds()
            return max(1, int(span // spec.seconds) + 1)
        t = nxt.replace(second=0, microsecond=0)
        cap = now_dt.replace(second=0, microsecond=0)
        count = 0
        guard = 0
        while t <= cap and guard < 100_000:       # 100k 分钟 ≈ 70 天窗口
            if self._cron_match(spec, t):
                count += 1
            t += timedelta(minutes=1)
            guard += 1
        return max(1, count)

    async def recover(self, ctx: Any = None, *,
                      now_dt: Optional[datetime] = None) -> int:
        """崩溃恢复(F048 …/恢复):回放 schedule.* 重建注册表;宕机期错过的触发
        只记 schedule.missed 不补跑;窗口重臂后启动分钟 ticker;返回 missed 总数。

        窗口未到(next_fire_at 仍未来)→ 保持快照原样续跑(与崩溃前一致);
        窗口已过 → 记 missed 并把 last_fired 清零、从 now 重臂(偏离 3)。
        """
        now_dt = now_dt or self._now()
        sess = self._session_ctx(ctx)
        # 事件重建注册表(INV-01):重建含 trigger 派生的 last_fired_at/前推窗口
        rebuilt = type(self)._rebuild_from(self._iter_session_events())
        self._jobs = rebuilt._jobs
        missed_total = 0
        for j in self._jobs.values():
            if j.paused:                        # 暂停中:不核算错过
                continue
            if self._missed_window(j, now_dt):  # 宕机窗内错过 → 只留痕不补跑
                await sess.append("schedule.missed", {
                    "job": j.name, "missed": self._missed_count(j, now_dt),
                    "since": iso(j.next_fire_at), "until": iso(now_dt)},
                    actor="system")
                missed_total += 1
                spec = self._spec_of(j)
                if j.kind == "interval":
                    # 错过窗已消费:显式从 now 重臂(不依赖 stale last_fired,不补跑)
                    j.next_fire_at = now_dt + timedelta(seconds=spec.seconds)
                else:
                    j.next_fire_at = self.next_fire(j, now_dt)
                    if j.kind == "at" and j.next_fire_at is None:
                        continue            # 一次性 at 已彻底过期:由 remove 清理
            # 窗口未到:保持快照(next_fire_at 未来),继续未来触发
        # BUG-1 自愈:旧版扫描窗为 24h,使下次触发在 24h 之外的合法 cron(工作日/
        # 每周/每月)落盘 next_fire_at=null 并永不触发。这类 job 无触发历史,回放
        # 不会重算窗口 → 在此按新口径重算并显式落 schedule.updated(事件即真源)。
        for j in self._jobs.values():
            if j.paused or j.kind != "cron" or j.next_fire_at is not None:
                continue
            try:
                nxt = self.next_fire(j, now_dt)
            except PyHError as e:               # 真无触发点(如 2/30 的历史遗留)
                await sess.append("system.error", {
                    "code": e.code,
                    "hint": f"schedule:{j.name} 无未来触发点,已跳过"},
                    actor="system")
                continue
            await sess.append("schedule.updated", {
                "name": j.name, "paused": False, "next_fire_at": iso(nxt)},
                actor="system")
            j.next_fire_at = nxt

        if self._auto_ticker:
            self._ensure_ticker(ctx)
        return missed_total

    @classmethod
    def _rebuild_from(cls, events: list[Any]) -> "Scheduler":
        """事件回放重建(INV-01,内部):顺序重放 schedule.registered/updated/removed
        重建 _jobs;孤儿 updated/removed 防御跳过;补扫 schedule.trigger 重建
        last_fired_at 并前推 next_fire_at(偏离 2:missed 只计宕机窗)。

        返回全新注册表实例(类方法,不触碰 self 状态)。
        """
        m = cls()                               # 清空后按事件重建(无 session:只读)
        for e in events:
            t = getattr(e, "type", "")
            p = getattr(e, "payload", {}) or {}
            if t == "schedule.registered":
                m._jobs[p["name"]] = ScheduleJob(
                    name=p["name"], kind=p["kind"], expr=p["expr"],
                    template={"intent": p["template"]["intent"]},
                    is_risky=bool(p.get("is_risky", False)),
                    paused=bool(p.get("paused", False)),
                    next_fire_at=parse_iso(p.get("next_fire_at")),
                    created_seq=getattr(e, "seq", 0))
            elif t == "schedule.updated":       # 暂停/恢复状态迁移
                j = m._jobs.get(p.get("name"))
                if j is None:
                    log.warning("orphan schedule.updated seq=%s", getattr(e, "seq", "?"))
                    continue                    # 孤儿(坏日志):防御跳过
                j.paused = bool(p.get("paused", j.paused))
                nxt = parse_iso(p.get("next_fire_at"))
                if nxt is not None:
                    j.next_fire_at = nxt
            elif t == "schedule.removed":       # 移除留痕
                m._jobs.pop(p.get("name"), None)
        # 第二遍:以最后一次 trigger.fired_at 为锚重建 last_fired_at/前推 next_fire_at
        last_trigger: dict[str, datetime] = {}
        for e in events:
            p = getattr(e, "payload", {}) or {}
            if getattr(e, "type", "") == "schedule.trigger":
                dt = parse_iso(p.get("fired_at"))
                if dt is not None and (dt > last_trigger.get(p.get("job"), datetime.min)):
                    last_trigger[p.get("job")] = dt
        for name, job in m._jobs.items():
            fired = last_trigger.get(name)
            if fired is None or job.kind in ("at",) or job.paused:
                continue                        # 无触发历史/一次性/暂停:快照即权威
            job.last_fired_at = fired
            job.spec = m._parse_spec(job.kind, job.expr, require_future=False)
            if job.kind == "interval":
                # 确定性前推:上一触发 + 间隔(宕机窗 = 上一窗之后)
                job.next_fire_at = fired + timedelta(seconds=job.spec.seconds)
            else:
                # cron:fired 之后最近的匹配分钟
                job.next_fire_at = m.next_fire(job, fired)
        return m                                # 内存态 ≡ 日志派生态(INV-01)

    @classmethod
    def rebuild_for_session(cls, session: Any, *,
                            task_queue: Any = None,
                            auto_ticker: bool = False) -> "Scheduler":
        """事件回放重建并绑定会话(命令层/装配层入口,INV-01)。"""
        events: list[Any] = []
        if session is not None:
            ea = getattr(session, "events_after", None)
            if callable(ea):
                events = list(ea(0))
            elif hasattr(session, "replay"):
                events = list(session.replay())
        m = cls._rebuild_from(events)
        m.session = session
        m.task_queue = task_queue
        m._auto_ticker = bool(auto_ticker)
        m._stopped = not auto_ticker
        m._ticker_task = None
        return m

    def _ensure_ticker(self, ctx: Any = None) -> None:
        """启动/复用分钟泵单协程(INV-07 单写者;已跑不重复起)。"""
        if self._stopped:
            return
        if self._ticker_task is not None and not self._ticker_task.done():
            return
        self._ticker_task = asyncio.get_running_loop().create_task(
            self._ticker(ctx))

    async def _ticker(self, ctx: Any = None) -> None:
        """常驻协程:睡到下一分钟边界后 _tick(F048 最小粒度 1 分钟);单轮异常不
        退出泵(记 system.error 后继续下一轮)。"""
        while not self._stopped:
            nxt = (self._now() + timedelta(minutes=1)).replace(second=0, microsecond=0)
            try:
                await self._sleep(max(1.0, (nxt - self._now()).total_seconds()))
            except asyncio.CancelledError:
                raise                       # 外部停止:让路
            try:
                await self._tick(ctx)       # 每分钟检查一次(F048)
            except asyncio.CancelledError:
                raise
            except Exception as e:          # noqa: BLE001 单轮异常不杀泵
                log.exception("schedule tick failed: %s", e)
                try:
                    await self._session_ctx(ctx).append("system.error", {
                        "code": "CYC-999", "hint": "schedule tick 异常已隔离"},
                        actor="system")
                except Exception:           # noqa: BLE001 留痕失败不阻断泵存活
                    log.exception("schedule tick error event failed")

