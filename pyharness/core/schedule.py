"""pyharness/core/schedule.py — 进程内持久化定时器 (specs/schedule.py.md 契约;F048)

功能编号:F048(定时任务)· F043(到点入队)· F011(持久化联动)· F014/F015(危险模板深夜
禁触发)。一句话:cron 表达式(及固定间隔 interval/绝对时间 at 两种扩展)注册任务模板,
到点把模板作为新任务入队(F043,复用 guard/预算/日志纪律);list/remove/pause/resume
全事件化;job 定义与状态随会话持久化(事件溯源),崩溃恢复后**继续未来触发**,宕机期
错过的触发**只记 missed 不补跑**(PRD F048 边界);默认禁深夜(23:00-7:00)触发危险类
模板;最小粒度 1 分钟,单进程协程(INV-07)。事件 schedule.registered/updated/removed/
blocked/missed 为词表外新增(trigger 已收编 EVENT-SCHEMA §3.5.3),已按 §7 规则登记
(payload 模型 + vocab,llm.retry 同款先例);文档字段表同步留给文档管线。

偏离说明(契约=specs/schedule.py.md;以下为与规格伪码冲突处的取舍,均列理由,与
goal.py/task_queue.py 同款先例,已入模块 docstring 供审查):
1. 事件先落、内存后对齐(goal.py 偏离 2 同款,INV-01 更严):伪码"先入内存后落事件",
   若 append 校验拒写(终态/载荷非法)会留下"内存有、日志无"的脏态;本实现所有写
   路径先 await session.append 成功,再把内存字段对齐到事件载荷,拒绝时零副作用。
   created_seq = schedule.registered 事件 env.seq(与 ctx.session.next_seq() 同值,
   且与 goal.created 同构,回放重建口径一致)。
2. _rebuild_from 在 spec 三类事件(registered/updated/removed)回放之外,补扫
   schedule.trigger 事件重建 last_fired_at 并据其前推 next_fire_at:仅按注册快照重建
   时,已触发过多次的 interval/cron job 会拿"注册时刻的首窗快照"当下一窗,恢复时把
   宕机前已正常触发的窗口重复记 missed(如每 10min 的 job 触发两轮后宕机 → 误报
   missed=2)。以最后一次 trigger.fired_at 为锚,missed 只计真正落在宕机窗内的机会,
   与 PRD"宕机期错过的触发机会只记 missed"及 GWT-F048-12"重启后 list 与崩溃前一致"
   对齐(无 trigger 事件的 job 行为与伪码完全一致)。
3. recover 的"重臂"分两档:窗口未到(next_fire_at 仍未来)→ 保持快照(崩溃前状态
   原样续跑,list_jobs 与崩溃前一致);窗口已过(宕机错过)→ 记 missed 后显式从 now
   重臂(interval:now+间隔;cron:now 之后下一匹配;at:fire_at 或 None),last_fired_at
   保留供列表展示(最近触发仍是崩溃前那轮)。伪码对 interval 无条件按 now 重臂会把
   未错过的首窗推后一个周期;若不清 last_fired 又 arm 到过去 → 恢复后立刻补触发
   一次,与"错过不补跑"矛盾(PRD 边界权威)。
4. resume 以 last_fired=None 的探针重臂(interval = now+间隔):暂停期窗口不补跑、
   恢复即未来触发;last_fired_at 字段不改动(展示仍为真实最近触发)。伪码未用探针,
   若以 stale last_fired 为基会 arm 到过去 → resume 后立刻补触发 pause 期窗口,
   与暂停语义相悖。
5. BUSY 为 ERR §2.4 功能特性码,errors.register_default_codes 未收录;raise_code 会把
   未登记码改写为 CYC-999 → 按 task_queue 偏离 5/goal 同款先例直接构造
   PyHError("BUSY")(.code 字面量 BUSY,供测试/调用方断言);CFG-601/EVT-100/CYC-999
   为已登记码,全走 raise_code(任务要求"错误全走 raise_code"的边界说明)。
6. 深夜窗取构造参数 night_window=(23,7) 默认 23:00≤h 或 h<7:00(spec 常量可配
   schedule.night_window;装配方把配置值传入即可,本模块不直接读 config 防硬耦合)。
7. cron 扫描上限按伪码 1440 分钟(一天内必命中的日级表达式;周/月跨度表达式属 F048
   分钟对齐语义之外,需跨天匹配的周期用 interval/at 表达——伪码同样受此上限)。
8. 周日映射按数据表"周 0-6,0=周日"实现(cron 域标准);伪码直用 dt.weekday()
   (Monday=0)与字段表矛盾,取字段表权威:cron_wday = (dt.weekday()+1) % 7。
9. next_fire/trigger 事件时间一律本地时区朴素 datetime(spec:时间一律本地时区;
   payload fired_at 正则只校 ISO 前缀),与信封 ts(UTC Z)分离。

依赖方向(INV-08):本模块 → errors / events(vocab 登记事件类型经 session.append 写入)、
task_queue(经构造注入,不 import 实现)、tools_registry.NAME_RE(工具同名规)、
config.baseline_danger(F023 危险分级默认表);不 import agent_loop/llm/plan。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Literal, Optional, Union

from pyharness.config import baseline_danger
from pyharness.core.tools_registry import NAME_RE
from pyharness.errors import PyHError, raise_code


# ---------------------------------------------------------------- 常量(F048)
"""三种触发方式:cron(5 字段,核心)/ interval(固定间隔秒)/ at(绝对时间,一次性)。"""

# cron 下次触发扫描上限(**按日推进**,非逐分钟)。取一个完整格里高利周期
# (400 年)加一天:凡在 400 年内至少能匹配一次的表达式,其真实未来触发点必
# 落在界内;界内无匹配 ⇒ 该表达式**永远不存在**触发点(如 2/30)→ 显式失败。
# 上限的代价只在不可能表达式上付(常见表达式首日即命中,循环立即返回)。


# ---------------------------------------------------------------- 内部 spec


# ---------------------------------------------------------------- 数据结构


# ------------------------------------------------------------------ Scheduler
from pyharness.core.schedule_models import (log, JobKind, MIN_INTERVAL_SECONDS, CRON_MAX_SCAN_DAYS, DEFAULT_NIGHT_WINDOW, _CRON_BOUNDS, _ISO_RE, _SEG_RE, now, iso, parse_iso, CronSpec, IntervalSpec, AtSpec, ScheduleJob, JobInfo)

from pyharness.core.schedule_calendar import ScheduleCalendar
from pyharness.core.schedule_recovery import ScheduleRecovery

class Scheduler(ScheduleCalendar, ScheduleRecovery):
    """会话级进程内定时器(F048):注册表 + 分钟对齐 ticker,全事件化。

    消费 session.append/replay(schedule.* 事件与恢复回放)、task_queue.submit
    (F043 到点入队,经构造注入)、危险分级(F023,is_risky 推导);被命令层
    (register/list/remove/pause/resume 命令)与会话启动装配(recover)消费。

    私有字段: _jobs(name → ScheduleJob,事件重建,INV-01)/ _stopped(ticker 停闸)/
    _ticker_task(分钟泵单例句柄,INV-07 单协程)/ _sleep(泵睡眠注入,测试用)。
    """

    def __init__(self, session: Any = None, task_queue: Any = None, *,
                 night_window: tuple[int, int] = DEFAULT_NIGHT_WINDOW,
                 danger: Optional[Union[dict, Callable[[str], str]]] = None,
                 auto_ticker: bool = True) -> None:
        """构造调度器。

        session = SessionLog 事件源(append 唯一写口;None 时须经 ctx 提供);
        task_queue = TaskQueue 或同型 async submit(intent, *, meta) 协议对象
        (F043 到点入队;None 时触发即 CYC-999 事件化,不静默);
        night_window = 深夜禁触窗 (start_hour, end_hour) 默认 (23, 7);
        danger = 工具 → 危险级解析(F023):dict 或 callable,None = config.baseline_danger;
        auto_ticker = recover 是否自动启动分钟泵(单测注入时钟时可关)。
        """
        self.session = session
        self.task_queue = task_queue
        self._night_start, self._night_end = night_window
        self._danger: Union[dict, Callable[[str], str]] = (
            danger if danger is not None else baseline_danger)
        self._auto_ticker = bool(auto_ticker)
        self._jobs: dict[str, ScheduleJob] = {}
        self._stopped = False
        self._ticker_task: Optional[asyncio.Task] = None
        self._sleep: Callable[..., Any] = asyncio.sleep   # 泵睡眠(测试注入)

    # ========================================================== 注入解析
    def _session_ctx(self, ctx: Any) -> Any:
        """事件源解析:构造注入优先,ctx.session 兜底(命令层装配便利)。"""
        if self.session is not None:
            return self.session
        if ctx is not None:
            s = getattr(ctx, "session", None) or getattr(ctx, "_session", None)
            if s is not None:
                return s
        raise_code("EVT-100", hint="Scheduler 未注入 session,无法写 schedule.* 事件",
                   advice="构造时注入 SessionLog 或同型 append 协议对象")

    def _queue_ctx(self, ctx: Any) -> Any:
        """入队目标解析:构造注入优先,ctx.task_queue 兜底。"""
        if self.task_queue is not None:
            return self.task_queue
        if ctx is not None:
            q = getattr(ctx, "task_queue", None)
            if q is not None:
                return q
        return None

    def _iter_session_events(self):
        from pyharness.core.event_reader import session_events
        return session_events(self.session)

    def _danger_level(self, tool: str) -> str:
        """单工具危险级解析(F023;未知工具视同 none,显式 override 由调用方把关)。"""
        if isinstance(self._danger, dict):
            return str(self._danger.get(tool, "none"))
        try:
            return str(self._danger(tool))
        except Exception:                     # noqa: BLE001 分级表异常不炸注册
            log.exception("danger lookup failed tool=%s", tool)
            return "none"

    def _now(self):
        return now()

    # ========================================================== 表达式解析

    # 别名(与 spec 函数清单同名,供命令层/测试直接调用)
    _validate_spec = ScheduleCalendar._parse_spec


    # ========================================================== 窗口推进


    # ========================================================== 注册(F048)
    def _derive_risky(self, template: dict) -> bool:
        """is_risky 推导(F023):模板 meta.tools 声明任一工具为 high/critical → 危险。

        模板未声明工具 → False(无从分级,安全由显式 is_risky 覆盖兜底)。
        """
        if not isinstance(template, dict):
            return False
        meta = template.get("meta")
        if not isinstance(meta, dict):
            return False
        tools = meta.get("tools")
        if isinstance(tools, str):
            tools = [tools]
        if not isinstance(tools, list):
            return False
        for tool in tools:
            if self._danger_level(str(tool)) in ("high", "critical"):
                return True
        return False

    async def register(self, name: str, kind: str, expr: str, template: dict, *,
                       is_risky: Optional[bool] = None, ctx: Any = None) -> None:
        """注册/更新调度 job(F048)。

        非法表达式(CFG-601)/重名(BUSY)/模板缺 intent(EVT-100)/名字非法(CFG-601)
        一律拒;合法则写 schedule.registered 持久化定义(事件=唯一真源 INV-01)。
        """
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise_code("CFG-601", hint=f"job 名非法: {name}",
                       advice="小写字母开头,字母/数字/下划线/点,总长 2~64")
        if name in self._jobs:                # 重名:显式拒,不隐式覆盖
            raise PyHError("BUSY", ctx={
                "hint": f"job {name} 已存在",
                "advice": "先 remove 再注册(防隐式覆盖)"})
        spec = self._validate_spec(kind, expr)          # 非法 → CFG-601(EVT-100)
        if not isinstance(template, dict) or not isinstance(
                template.get("intent"), str) or not template["intent"].strip():
            raise_code("EVT-100", hint="template 缺 intent 字段",
                       advice="任务模板必须含非空意图文本")
        risky = self._derive_risky(template) if is_risky is None else bool(is_risky)
        sess = self._session_ctx(ctx)
        first = self._first_fire(kind, expr, now())
        # 事件先落,内存后对齐(偏离 1:append 拒写时零副作用,INV-01 更严)
        env = await sess.append("schedule.registered", {
            "name": name, "kind": kind, "expr": expr,
            "template": {"intent": template["intent"]},   # meta 不入事件(INV-09)
            "is_risky": risky, "paused": False,
            "next_fire_at": iso(first)}, actor="system")
        job = ScheduleJob(name=name, kind=kind, expr=expr,
                          template=dict(template), is_risky=risky, paused=False,
                          next_fire_at=first, created_seq=env.seq, spec=spec)
        self._jobs[name] = job                # 事件落定 → 注册表对齐

    # ========================================================== 查询
    def _count_from_events(self) -> dict[str, dict[str, int]]:
        """triggered/missed 计数聚合(schedule.trigger 计数;missed 事件 payload
        missed 累计)——内存不存第二份(INV-01),list_jobs 每次回放聚合。"""
        counts: dict[str, dict[str, int]] = {}
        for e in self._iter_session_events():
            t = getattr(e, "type", "")
            p = getattr(e, "payload", {}) or {}
            if t == "schedule.trigger":
                c = counts.setdefault(p.get("job", ""),
                                      {"triggered": 0, "missed": 0})
                c["triggered"] += 1
            elif t == "schedule.missed":
                c = counts.setdefault(p.get("job", ""),
                                      {"triggered": 0, "missed": 0})
                c["missed"] += int(p.get("missed", 0))
        return counts

    async def list_jobs(self, ctx: Any = None) -> list[JobInfo]:
        """列出全部 job 快照(F048 list;计数由事件回放聚合)。"""
        counts = self._count_from_events()
        out: list[JobInfo] = []
        for j in self._jobs.values():
            c = counts.get(j.name, {"triggered": 0, "missed": 0})
            out.append(JobInfo(name=j.name, kind=j.kind, expr=j.expr,
                               paused=j.paused, next_fire_at=j.next_fire_at,
                               last_fired_at=j.last_fired_at,
                               triggered=c["triggered"], missed=c["missed"]))
        return out

    # ========================================================== 管理命令
    def _require(self, name: str) -> ScheduleJob:
        """定位 job;不存在 → BUSY 显式拒(幂等不静默)。"""
        j = self._jobs.get(name)
        if j is None:
            raise PyHError("BUSY", ctx={"hint": f"job {name} 不存在",
                                        "advice": "用 list_jobs 查现有 job"})
        return j

    async def remove(self, name: str, ctx: Any = None) -> None:
        """移除 job(F048):写 schedule.removed;不存在 → BUSY。

        tick 快照遍历中移除安全(遍历在 list 快照上);at 一次性任务触发后自动走此。
        """
        self._require(name)                   # 不存在 → BUSY
        sess = self._session_ctx(ctx)
        await sess.append("schedule.removed", {"name": name}, actor="system")
        self._jobs.pop(name, None)            # 事件落定 → 内存摘除(INV-01)

    async def edit(self, name: str, kind: str, expr: str, intent: str, ctx: Any = None) -> None:
        """Replace a definition atomically via one existing registration fact.

        Already submitted executions retain their original intent. No remove /
        re-register window can lose the schedule when persistence fails.
        """
        old=self._require(name)
        spec=self._validate_spec(kind,expr)
        if not isinstance(intent,str) or not intent.strip():
            raise_code('EVT-100',field='intent')
        template={'intent':intent.strip()}
        first=self._first_fire(kind,expr,now())
        risky=old.is_risky or self._derive_risky(template)
        sess=self._session_ctx(ctx)
        event=await sess.append('schedule.registered',{'name':name,'kind':kind,'expr':expr,
            'template':template,'is_risky':risky,'paused':old.paused,'next_fire_at':iso(first)},actor='system',sync=True)
        self._jobs[name]=ScheduleJob(name=name,kind=kind,expr=expr,template=template,
            is_risky=risky,paused=old.paused,next_fire_at=first,last_fired_at=old.last_fired_at,
            created_seq=event.seq,spec=spec)

    async def run_now(self, name: str, ctx: Any = None) -> None:
        """Explicit immediate execution through the same Schedule → TaskQueue path."""
        job=self._require(name)
        await self._fire(ctx,job,now())

    async def pause(self, name: str, ctx: Any = None) -> None:
        """暂停:到点不触发但保留定义与窗口;重复暂停幂等(无事件)。"""
        j = self._require(name)               # 不存在 → BUSY
        if j.paused:
            return                            # 幂等(重复暂停无事件)
        sess = self._session_ctx(ctx)
        await sess.append("schedule.updated",
                          {"name": name, "paused": True}, actor="system")
        j.paused = True                       # 事件落定 → 内存对齐

    async def resume(self, name: str, ctx: Any = None) -> None:
        """恢复:继续按重臂后的 next_fire_at 触发;重复恢复幂等。"""
        j = self._require(name)
        if not j.paused:
            return                            # 幂等(重复恢复无事件)
        sess = self._session_ctx(ctx)
        # interval 恢复 = 从恢复时刻起算新周期(暂停期窗口不补跑,偏离 4):
        # 以 last_fired=None 的探针计算,避免 append 前改动内存字段(事件先落纪律)
        probe = ScheduleJob(name=j.name, kind=j.kind, expr=j.expr,
                            template=j.template, spec=j.spec)
        nxt = self.next_fire(probe, now())
        await sess.append("schedule.updated", {
            "name": name, "paused": False, "next_fire_at": iso(nxt)},
            actor="system")
        j.paused = False                      # 事件落定 → 内存对齐
        j.next_fire_at = nxt

    # ========================================================== 触发(F048)
    async def _fire(self, ctx: Any, job: ScheduleJob, fired_at: datetime) -> None:
        """触发执行(F048 I/O):先写 schedule.trigger 留痕再 submit 任务模板(F043)。

        模板意图经队列执行,复用 guard/预算/日志纪律;入队失败(如 QUE-001)不静默,
        写 system.error 显式事件化,下一触发窗重试。
        """
        sess = self._session_ctx(ctx)
        await sess.append("schedule.trigger", {
            "job": job.name, "cron": job.expr,
            "fired_at": fired_at.isoformat()}, actor="system")
        q = self._queue_ctx(ctx)
        if q is None:                         # 未装配队列:宁失败不假装入队
            raise_code("CYC-999", hint=f"schedule:{job.name} 未注入 task_queue,"
                       "到点任务无法入队")
        intent = job.template["intent"]
        await sess.append("user.message", {"content": intent}, actor="user",
                           origin=f"schedule:{job.name}", sync=True)
        try:
            await q.submit(intent, meta={"source": f"schedule:{job.name}"})
        except PyHError as e:                 # QUE-001 队满等:显式事件化不吞
            try:
                await sess.append("system.error", {
                    "code": e.code, "hint": f"schedule:{job.name} 入队失败"},
                    actor="system")
            except Exception:                 # noqa: BLE001 留痕失败只记日志
                log.exception("schedule error event failed job=%s", job.name)

    async def _tick(self, ctx: Any = None, *, now_dt: Optional[datetime] = None
                    ) -> list[str]:
        """每分钟检查(F048 核心):暂停跳过 → 未到期跳过 → 深夜+危险 blocked 留痕
        → at 一次性触发后移除 → cron/interval 触发并推进窗口;返回触发名单。

        单 job 异常隔离:任一 job 失败记 system.error 不中断其余(tick 快照遍历)。
        """
        now_dt = now_dt or now()
        sess = self._session_ctx(ctx)
        fired: list[str] = []
        for job in list(self._jobs.values()):     # 快照遍历(tick 中可 remove/注册)
            if job.paused:
                continue
            if not self._due(job, now_dt):
                continue
            try:
                if self._is_night(now_dt) and job.is_risky:
                    # 深夜禁触危险类模板:blocked 留痕 + 顺延下次,零入队(GWT-06)
                    await sess.append("schedule.blocked", {
                        "job": job.name, "reason": "night-window"}, actor="system")
                    job.next_fire_at = self.next_fire(job, now_dt)
                    continue
                if job.kind == "at":              # 一次性:触发后移除(不重复)
                    await self._fire(ctx, job, now_dt)
                    fired.append(job.name)
                    await self.remove(job.name, ctx)
                    continue
                # 周期型:先 fire 成功再推进窗口(P2:此前内存窗口先推、事件后落,
                # _fire 失败时崩溃恢复按事件重推 → 重复/错位触发)
                await self._fire(ctx, job, now_dt)
                job.last_fired_at = now_dt
                job.next_fire_at = self.next_fire(job, now_dt)
                fired.append(job.name)
            except PyHError as e:                 # 单 job 异常隔离(原码事件化)
                log.error("schedule tick job=%s code=%s", job.name, e.code)
                try:
                    await sess.append("system.error", {
                        "code": e.code, "hint": f"schedule:{job.name} tick 失败"},
                        actor="system")
                except Exception:                 # noqa: BLE001
                    log.exception("schedule error event failed job=%s", job.name)
            except Exception:                     # 未预期:记日志,不中断其余 job
                log.exception("schedule tick unexpected job=%s", job.name)
        return fired

    # ========================================================== 崩溃恢复


    # ========================================================== 分钟泵


    def stop(self) -> None:
        """停止泵(会话关闭/装配拆卸用):置停闸并取消 ticker 任务。"""
        self._stopped = True
        t = self._ticker_task
        if t is not None and not t.done():
            t.cancel()

    async def aclose(self):
        task = self._ticker_task
        self.stop()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._ticker_task = None


__all__ = [
    "Scheduler", "ScheduleJob", "JobInfo",
    "CronSpec", "IntervalSpec", "AtSpec", "JobKind",
    "NAME_RE", "now", "iso", "parse_iso",
    "MIN_INTERVAL_SECONDS", "CRON_MAX_SCAN_DAYS", "DEFAULT_NIGHT_WINDOW",
]
