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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal, Optional, Union

from pyharness.config import baseline_danger
from pyharness.core.tools_registry import NAME_RE
from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.schedule")

# ---------------------------------------------------------------- 常量(F048)
JobKind = Literal["cron", "interval", "at"]
"""三种触发方式:cron(5 字段,核心)/ interval(固定间隔秒)/ at(绝对时间,一次性)。"""

MIN_INTERVAL_SECONDS = 60          # interval 下限:F048 最小粒度 1 分钟
CRON_SCAN_MINUTES = 1440           # cron 下次匹配扫描上限(伪码 1440 次防死循环)
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


# ---------------------------------------------------------------- 内部 spec
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


# ---------------------------------------------------------------- 数据结构
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


# ------------------------------------------------------------------ Scheduler
class Scheduler:
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

    def _iter_session_events(self) -> list[Any]:
        """会话事件流只读遍历(session.replay/events_after 语义;计数与重建共用)。"""
        s = self.session
        if s is None:
            return []
        if hasattr(s, "events_after"):        # SessionLog 增量读(seq 升序)
            return list(s.events_after(0))
        if hasattr(s, "replay"):              # 存储层真源回放
            return list(s.replay())
        if hasattr(s, "events"):              # 测试替身/轻量落点
            return list(s.events)
        return []

    def _danger_level(self, tool: str) -> str:
        """单工具危险级解析(F023;未知工具视同 none,显式 override 由调用方把关)。"""
        if isinstance(self._danger, dict):
            return str(self._danger.get(tool, "none"))
        try:
            return str(self._danger(tool))
        except Exception:                     # noqa: BLE001 分级表异常不炸注册
            log.exception("danger lookup failed tool=%s", tool)
            return "none"

    # ========================================================== 表达式解析
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
            if dt is None or (require_future and dt <= now()):
                raise_code("CFG-601", hint="at 时间须为未来的 ISO8601",
                           advice="示例:2026-09-08T10:30:00")
            return AtSpec(fire_at=dt)
        raise_code("EVT-100", hint=f"kind 非法: {kind}",
                   advice="kind ∈ cron/interval/at")

    # 别名(与 spec 函数清单同名,供命令层/测试直接调用)
    _validate_spec = _parse_spec

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

    # ========================================================== 窗口推进
    def next_fire(self, job: ScheduleJob, now_dt: datetime) -> Optional[datetime]:
        """按 kind 计算下次触发(cron 逐分钟后扫 ≤1440;interval 按 last_fired 推进;
        at 一次性:fire_at 已过 → None,耗尽)。"""
        spec = self._spec_of(job)
        if job.kind == "cron":                # 从下一分钟向后扫到匹配分钟
            t = now_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
            for _ in range(CRON_SCAN_MINUTES):
                if self._cron_match(spec, t):
                    return t
                t += timedelta(minutes=1)
            return None                       # 上限内无匹配(如 2/30):不触发
        if job.kind == "interval":            # 固定间隔推进(≥60s)
            base = job.last_fired_at or now_dt
            return base + timedelta(seconds=spec.seconds)
        return spec.fire_at if spec.fire_at > now_dt else None   # at:已过即 None

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
                job.last_fired_at = now_dt        # 周期型:推进窗口
                job.next_fire_at = self.next_fire(job, now_dt)
                await self._fire(ctx, job, now_dt)
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
        now_dt = now_dt or now()
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

    # ========================================================== 分钟泵
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
            nxt = (now() + timedelta(minutes=1)).replace(second=0, microsecond=0)
            try:
                await self._sleep(max(1.0, (nxt - now()).total_seconds()))
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

    def stop(self) -> None:
        """停止泵(会话关闭/装配拆卸用):置停闸并取消 ticker 任务。"""
        self._stopped = True
        t = self._ticker_task
        if t is not None and not t.done():
            t.cancel()
        self._ticker_task = None


__all__ = [
    "Scheduler", "ScheduleJob", "JobInfo",
    "CronSpec", "IntervalSpec", "AtSpec", "JobKind",
    "NAME_RE", "now", "iso", "parse_iso",
    "MIN_INTERVAL_SECONDS", "CRON_SCAN_MINUTES", "DEFAULT_NIGHT_WINDOW",
]
