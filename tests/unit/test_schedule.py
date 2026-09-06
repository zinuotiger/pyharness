"""tests/unit/test_schedule.py — schedule 模块单测(F048 定时任务/词表外事件 §7)。

契约:docs/specs/schedule.py.md(权威)+ EVENT-SCHEMA §3.5.3(schedule.trigger)+
ERR.md §2.7(CFG-6xx 非法 cron 拒)/§2.4(BUSY)。本模块事件(schedule.registered/
updated/removed/blocked/missed)为词表外新增,按 §7 已登记(events.payload/vocab)。

覆盖面(任务要求全项):
    注册三种方式:cron/interval/at 首窗计算与事件载荷(created_seq=事件 seq,
        meta 不入事件);非法名 CFG-601、重名 BUSY、缺 intent EVT-100、未知 kind
        EVT-100、cron 段数/值域/语法 CFG-601、interval<60 CFG-601、at 已过去 CFG-601
    cron 字段匹配:表达式表驱动(* / 区间 / 列表 / 步长;周 0=周日)、next_fire 边界
        (GWT-F048-04:0 2 * * * 02:00 命中 / 02:01 不命中;2/30 类永不匹配 → None)
    触发入队(F043):_tick 到点 → schedule.trigger{job,cron,fired_at} + submit
        (intent + meta.source=schedule:<name>);trigger 先于 task.enqueued(GWT-08);
        at 一次性触发后自动 remove(GWT-07);interval 触发后按 last_fired 推进(GWT-05)
    管理命令:list_jobs(与事件计数一致 GWT-11)、pause/resume(暂停期零触发零入队、
        恢复续触发、幂等 GWT-10)、remove(移除后不再触发;未知 BUSY GWT-09)
    深夜禁触(23:00-7:00):危险 job → schedule.blocked 零入队;非危险照常触发;
        白天危险照常触发(GWT-06)
    is_risky 推导(F023):meta.tools 命中 high/critical → True;显式覆盖
    崩溃恢复:宕机错过仅 schedule.missed 无入队且不补跑;恢复后到点正常触发;
        重启后 list 与崩溃前一致(GWT-12);interval 只计宕机窗不重复计;at 过期
        记 missed 且不再触发;暂停 job 恢复期不核算;孤儿 updated/removed 防御跳过
    单 job 异常隔离:入队 QUE-001 → system.error 事件化,不中断其余 job
    分钟泵存活:GWT-F048-13(tick 异常后泵继续;stop 停闸)

时间纪律:全部经 monkeypatch schedule_mod.now 注入时钟(不真实 sleep);tick/recover
用 now_dt 显式注入。BUSY 为命名字面量(ERR §2.4),PyHError 直接构造(raise_code 会
改写未登记码),断言 ei.value.code == "BUSY"(test_goal/task_queue 同口径)。
"""
from types import SimpleNamespace
from datetime import datetime, timedelta

import asyncio

import pytest

import pyharness.core.schedule as S
from pyharness.core.schedule import (CronSpec, JobInfo, ScheduleJob, Scheduler,
                                     iso, parse_iso)
from pyharness.core.session import SessionLog
from pyharness.core.task_queue import TaskQueue
from pyharness.errors import PyHError

SID = "s-schedunit1"

# 固定基准时刻:2026-09-07(周一)10:00 本地(避开深夜窗;at/interval 亦以此时为锚)
BASE = datetime(2026, 9, 7, 10, 0, 0)
TMIN = datetime(2026, 9, 7, 0, 0, 0)   # 凌晨(深夜窗内)


# ===================================================================== 替身
class FakeQueue:
    """task_queue 替身:记录 submit 调用;可注入失败(PyHError → QUE-001 模拟)。"""

    def __init__(self, fail: bool = False, fail_intent: str = "") -> None:
        self.submits: list[tuple[str, dict]] = []   # (intent, meta)
        self.fail = fail
        self.fail_intent = fail_intent   # 仅该 intent 提交时失败
        self._n = 0

    async def submit(self, intent: str, *, meta=None, task_id=None) -> str:
        if self.fail or (self.fail_intent and intent == self.fail_intent):
            raise PyHError("QUE-001", ctx={"advice": "队列已满(测试注入)"})
        self._n += 1
        self.submits.append((intent, dict(meta or {})))
        return f"t-{self._n}"


class FakeSession:
    """轻量异步事件落点替身(同步记录 + seq 递增;不校验载荷,供写路径断言)。"""

    def __init__(self) -> None:
        self.events: list = []
        self._seq = 0

    async def append(self, type_, payload, *, actor, **kw):
        self._seq += 1
        env = SimpleNamespace(seq=self._seq, type=type_, actor=actor,
                              payload=dict(payload), session_id=SID)
        self.events.append(env)
        return env

    def of(self, type_) -> list:
        """按类型过滤事件(断言用,保序)。"""
        return [e for e in self.events if e.type == type_]

    def types(self) -> list:
        return [e.type for e in self.events]


class _Clock:
    """可变时钟:freeze 后模块 now() 恒返回当前值;测试改 .value 即推进时间。"""

    def __init__(self, value: datetime) -> None:
        self.value = value


@pytest.fixture
def clock(monkeypatch):
    """冻结 schedule_mod.now → 固定时刻(所有时间推导可控,不真实 sleep)。"""
    clk = _Clock(BASE)
    monkeypatch.setattr(S, "now", lambda: clk.value)
    return clk


def _mk(fail: bool = False, *, queue: FakeQueue = None) -> tuple[Scheduler, FakeSession, FakeQueue]:
    """装配:注入 session/队列(register/_tick/recover 全走注入面)。"""
    sess = FakeSession()
    q = queue if queue is not None else FakeQueue(fail=fail)
    return Scheduler(session=sess, task_queue=q, auto_ticker=False), sess, q


def _tpl(intent: str = "跑一次夜报", **meta) -> dict:
    """任务模板:{intent 必填, meta 可选}。"""
    return {"intent": intent, **({"meta": meta} if meta else {})}


# ===================================================================== 注册校验
async def test_register_writes_registered_event_and_job(clock):
    """注册(cron):schedule.registered 落事件(actor=system,meta 不入事件);
    内存 job 与载荷对齐(created_seq=事件 seq,next_fire_at=注册时首窗)。"""
    sched, sess, q = _mk()
    await sched.register("nightly.report", "cron", "0 2 * * *",
                         _tpl("生成夜间报表", tools=["fs.delete_file"]),
                         ctx=None)
    reg = sess.of("schedule.registered")
    assert len(reg) == 1
    ev = reg[0]
    assert ev.actor == "system"
    p = ev.payload
    assert p["name"] == "nightly.report" and p["kind"] == "cron"
    assert p["expr"] == "0 2 * * *"
    assert p["template"] == {"intent": "生成夜间报表"}   # meta 不入事件(INV-09)
    assert p["paused"] is False
    assert parse_iso(p["next_fire_at"]) == datetime(2026, 9, 8, 2, 0, 0)
    j = sched._jobs["nightly.report"]
    assert j.created_seq == ev.seq
    assert j.next_fire_at == parse_iso(p["next_fire_at"])
    assert j.is_risky is True                      # meta.tools 命中基线 high
    assert j.template["intent"] == "生成夜间报表"
    assert q.submits == []                         # 注册零入队


async def test_register_bad_name_cfg601():
    """名字非法(大写开头/超短)→ CFG-601 拒,零事件零注册。"""
    sched, sess, _ = _mk()
    for bad in ("Nightly", "9night", "a", "有中文"):
        with pytest.raises(PyHError) as ei:
            await sched.register(bad, "cron", "0 2 * * *", _tpl())
        assert ei.value.code == "CFG-601", repr(bad)
    assert sess.events == [] and sched._jobs == {}


async def test_register_duplicate_busy():
    """重名注册 → BUSY(提示先 remove;防隐式覆盖),原 job 不被覆盖(GWT-02)。"""
    sched, sess, _ = _mk()
    await sched.register("dup.job", "cron", "0 2 * * *", _tpl("原意图"))
    with pytest.raises(PyHError) as ei:
        await sched.register("dup.job", "interval", "120", _tpl("新意图"))
    assert ei.value.code == "BUSY"
    assert "remove" in ei.value.ctx["advice"]
    assert sched._jobs["dup.job"].template["intent"] == "原意图"
    assert len(sess.of("schedule.registered")) == 1


async def test_register_template_missing_intent_evt100():
    """模板缺 intent(空/缺键/非 dict)→ EVT-100,零事件。"""
    sched, sess, _ = _mk()
    for tpl in (None, {}, {"intent": ""}, {"intent": "   "}, {"meta": {}}, "str"):
        with pytest.raises(PyHError) as ei:
            await sched.register("test.x", "cron", "0 2 * * *", tpl)
        assert ei.value.code == "EVT-100", repr(tpl)
    assert sess.events == []


async def test_register_unknown_kind_evt100():
    """kind 非三值 → EVT-100(显式拒,不猜)。"""
    sched, sess, _ = _mk()
    with pytest.raises(PyHError) as ei:
        await sched.register("test.x", "monthly", "* * * * *", _tpl())
    assert ei.value.code == "EVT-100"
    assert sess.events == []


async def test_register_invalid_cron_cfg601_gwt01():
    """非法 cron(段数错/值域越界/语法错/列表段错)→ CFG-601 且零事件(GWT-01)。"""
    sched, sess, _ = _mk()
    bads = [
        "0 2 * *",                       # 4 段
        "0 2 * * * *",                   # 6 段
        "60 2 * * *",                    # 分越界
        "0 24 * * *",                    # 时越界
        "0 2 32 * *",                    # 日越界
        "0 2 * 13 *",                    # 月越界
        "0 2 * * 7",                     # 周越界(0-6)
        "0 2-1 * * *",                   # 区间倒序
        "0 2 */0 * *",                   # 步长 0
        "a 2 * * *",                     # 非数字
        "0 2 * * 1,,2",                  # 空列表段
        "0 2 * * * ",                    # 尾随空格 → 段数错
    ]
    for expr in bads:
        with pytest.raises(PyHError) as ei:
            await sched.register("bad", "cron", expr, _tpl())
        assert ei.value.code == "CFG-601", repr(expr)
    assert sess.events == [] and sched._jobs == {}


async def test_register_interval_min_gwt03(clock):
    """interval <60s(含非数字)→ CFG-601(GWT-03:interval 59s 拒);60s 通过。"""
    sched, sess, _ = _mk()
    for bad in ("59", "0", "-5", "abc", ""):
        with pytest.raises(PyHError) as ei:
            await sched.register("test.i", "interval", bad, _tpl())
        assert ei.value.code == "CFG-601", repr(bad)
    assert sess.events == []
    await sched.register("i60", "interval", "60", _tpl())
    assert parse_iso(sess.of("schedule.registered")[0].payload["next_fire_at"]) \
        == BASE + timedelta(seconds=60)


async def test_register_at_past_cfg601(clock):
    """at 时间已过去/非 ISO → CFG-601;未来 ISO 通过且一次性。"""
    sched, sess, _ = _mk()
    clock.value = datetime(2026, 9, 7, 10, 0, 0)
    for bad in ("2026-09-07T09:59:00",        # 已过去
                "2026-09-07T10:00:00",        # 恰当前(=now,非未来)
                "not-a-date", "2026/09/08 10:00"):
        with pytest.raises(PyHError) as ei:
            await sched.register("at", "at", bad, _tpl())
        assert ei.value.code == "CFG-601", repr(bad)
    assert sess.events == []
    await sched.register("at.future", "at", "2026-09-08T10:30:00", _tpl())
    p = sess.of("schedule.registered")[0].payload
    assert parse_iso(p["next_fire_at"]) == datetime(2026, 9, 8, 10, 30, 0)


# ===================================================================== cron 匹配
async def test_cron_match_field_table():
    """cron 单字段匹配表驱动:*/区间/列表/步长/周 0=周日(数据表权威)。"""
    sched, _, _ = _mk()
    # 字段值全部用合法表达式 → _cron_match(解析自 CronSpec)
    cases = [
        ("*", 0, True), ("*", 59, True),
        ("5", 5, True), ("5", 4, False),
        ("1-5", 1, True), ("1-5", 5, True), ("1-5", 6, False),
        ("1,3,7", 3, True), ("1,3,7", 2, False),
        ("*/15", 0, True), ("*/15", 15, True), ("*/15", 30, True),
        ("*/15", 7, False),
        ("1-20/5", 1, True), ("1-20/5", 6, True), ("1-20/5", 21, False),
        ("5,10-12/2,*/30", 5, True), ("5,10-12/2,*/30", 10, True),
        ("5,10-12/2,*/30", 12, True), ("5,10-12/2,*/30", 30, True),
        ("5,10-12/2,*/30", 7, False),
    ]
    for field_, v, want in cases:
        spec = CronSpec(fields=(field_, "*", "*", "*", "*"))
        assert sched._cron_match(spec, TMIN.replace(minute=v)) is want, \
            f"{field_} @ {v}"


async def test_cron_match_weekday_zero_sunday():
    """周字段 0=周日:2026-09-06(周日)命中 0;2026-09-07(周一)命中 1 不命中 0。"""
    sched, _, _ = _mk()
    sunday = datetime(2026, 9, 6, 10, 0, 0)     # 周日
    monday = datetime(2026, 9, 7, 10, 0, 0)     # 周一
    s0 = CronSpec(fields=("*", "*", "*", "*", "0"))
    s1 = CronSpec(fields=("*", "*", "*", "*", "1"))
    assert sched._cron_match(s0, sunday) is True
    assert sched._cron_match(s0, monday) is False
    assert sched._cron_match(s1, monday) is True


async def test_next_fire_boundaries_gwt04():
    """next_fire 边界(GWT-04):'0 2 * * *' 02:00 命中、02:01 不命中;
    永不匹配型(2/30 类)在扫描上限内无结果 → None(注册可、不触发)。"""
    sched, _, _ = _mk()
    job = ScheduleJob(name="j", kind="cron", expr="0 2 * * *",
                      template=_tpl(), spec=None)
    # 02:00:10 命中 → 触发候选;02:01 不命中
    assert sched.next_fire(job, datetime(2026, 9, 7, 1, 59, 0)) \
        == datetime(2026, 9, 7, 2, 0, 0)
    assert sched.next_fire(job, datetime(2026, 9, 7, 2, 0, 30)) \
        == datetime(2026, 9, 8, 2, 0, 0)
    # 永不匹配:2 月 30 日不存在 → None(不触发)
    never = ScheduleJob(name="n", kind="cron", expr="0 0 30 2 *",
                        template=_tpl(), spec=None)
    assert sched.next_fire(never, datetime(2026, 9, 7, 10, 0, 0)) is None


# ===================================================================== 触发入队
async def test_tick_cron_fires_submits_and_advances(clock):
    """到点 cron:_tick → schedule.trigger{job,cron,fired_at} 先落、再 submit
    (intent + meta.source=schedule:<name>);窗口前推、last_fired_at 更新;名单返回。"""
    sched, sess, q = _mk()
    clock.value = datetime(2026, 9, 7, 1, 59, 30)
    await sched.register("nightly", "cron", "0 2 * * *", _tpl("夜报生成"))
    fired = await sched._tick(None, now_dt=datetime(2026, 9, 7, 2, 0, 10))
    assert fired == ["nightly"]
    trig = sess.of("schedule.trigger")
    assert len(trig) == 1
    p = trig[0].payload
    assert p["job"] == "nightly" and p["cron"] == "0 2 * * *"
    assert parse_iso(p["fired_at"]) == datetime(2026, 9, 7, 2, 0, 10)
    assert q.submits == [("夜报生成", {"source": "schedule:nightly"})]
    j = sched._jobs["nightly"]
    assert j.last_fired_at == datetime(2026, 9, 7, 2, 0, 10)
    assert j.next_fire_at == datetime(2026, 9, 8, 2, 0, 0)
    # 02:01 不命中 → 零触发零入队(GWT-04)
    assert await sched._tick(None, now_dt=datetime(2026, 9, 7, 2, 1, 0)) == []
    assert len(q.submits) == 1


async def test_fire_order_trigger_before_enqueued_gwt08(clock):
    """GWT-08:schedule.trigger 先于 task.enqueued 落事件(真实队列;F043 FIFO)。"""
    clock.value = datetime(2026, 9, 7, 10, 4, 0)
    sess = FakeSession()
    q = TaskQueue(session=sess, max_queue=32)       # 不注入 runner(仅验证入队序)
    sched = Scheduler(session=sess, task_queue=q, auto_ticker=False)
    await sched.register("poll", "cron", "* * * * *", _tpl("轮询"))
    await sched._tick(None, now_dt=datetime(2026, 9, 7, 10, 5, 20))
    trig = sess.of("schedule.trigger")
    enq = sess.of("task.enqueued")
    assert len(trig) == 1 and len(enq) == 1
    assert trig[0].seq < enq[0].seq                # 留痕先于入队
    assert enq[0].payload["pos"] >= 1


async def test_tick_at_auto_removes_gwt07(clock):
    """GWT-07:at 一次性——到点触发后自动 remove(schedule.removed),不重复。"""
    sched, sess, q = _mk()
    await sched.register("once", "at", "2026-09-07T11:00:00", _tpl("一次性清理"))
    fired = await sched._tick(None, now_dt=datetime(2026, 9, 7, 11, 0, 30))
    assert fired == ["once"]
    assert len(sess.of("schedule.trigger")) == 1
    assert len(sess.of("schedule.removed")) == 1
    assert "once" not in sched._jobs
    # 后续 tick 零触发(已移除)
    assert await sched._tick(None, now_dt=datetime(2026, 9, 7, 11, 1, 0)) == []
    assert len(q.submits) == 1


async def test_tick_interval_advance_gwt05(clock):
    """GWT-05:interval 触发后按 last_fired 推进(600s);未到期不触发。"""
    sched, sess, q = _mk()
    await sched.register("beat", "interval", "600", _tpl("心跳"))
    assert sched._jobs["beat"].next_fire_at == BASE + timedelta(seconds=600)
    # 未到期:09:00 注册 → 首窗 BASE+600,09:59:59 前不触发
    before = datetime(2026, 9, 7, 10, 9, 59)
    assert await sched._tick(None, now_dt=before) == []
    assert q.submits == []
    # 到点触发 → next = last_fired + 600
    t1 = datetime(2026, 9, 7, 10, 10, 30)
    assert await sched._tick(None, now_dt=t1) == ["beat"]
    j = sched._jobs["beat"]
    assert j.last_fired_at == t1
    assert j.next_fire_at == t1 + timedelta(seconds=600)
    assert await sched._tick(None, now_dt=t1 + timedelta(seconds=599)) == []
    assert await sched._tick(None, now_dt=t1 + timedelta(seconds=600)) == ["beat"]
    assert len(q.submits) == 2


# ===================================================================== 深夜禁触
async def test_night_blocked_risky_gwt06(clock):
    """GWT-06:深夜(02:00)危险 job → schedule.blocked 留痕零入队并顺延;
    非危险照常触发;白天危险照常触发。"""
    sched, sess, q = _mk()
    clock.value = datetime(2026, 9, 7, 1, 50, 0)
    await sched.register("risky", "cron", "0 2 * * *",
                         _tpl("删库跑路", tools=["fs.delete_file"]))
    await sched.register("safe", "cron", "0 2 * * *", _tpl("普通备份"))
    fired = await sched._tick(None, now_dt=datetime(2026, 9, 7, 2, 0, 5))
    assert fired == ["safe"]                        # 只有非危险触发
    assert [e.payload["job"] for e in sess.of("schedule.blocked")] == ["risky"]
    blk = sess.of("schedule.blocked")[0].payload
    assert blk["reason"] == "night-window"
    assert len(sess.of("schedule.trigger")) == 1    # 仅 safe 的 trigger
    assert len(q.submits) == 1
    assert q.submits[0][1]["source"] == "schedule:safe"
    # risky 已顺延到次日 02:00(深夜不补触)
    assert sched._jobs["risky"].next_fire_at == datetime(2026, 9, 8, 2, 0, 0)
    # 07:00 边界(非深夜)危险照常触发
    clock.value = datetime(2026, 9, 7, 6, 55, 0)
    await sched.register("risky.morning", "cron", "0 7 * * *",
                         _tpl("晨跑", tools=["fs.delete_file"]))
    fired = await sched._tick(None, now_dt=datetime(2026, 9, 7, 7, 0, 5))
    assert fired == ["risky.morning"]
    assert len(q.submits) == 2


async def test_night_window_edges():
    """深夜窗边界:23:00 起(hour>=23)、7:00 止(hour<7)。"""
    sched, _, _ = _mk()
    assert sched._is_night(datetime(2026, 9, 7, 23, 0, 0)) is True
    assert sched._is_night(datetime(2026, 9, 7, 23, 59, 59)) is True
    assert sched._is_night(datetime(2026, 9, 7, 0, 0, 0)) is True
    assert sched._is_night(datetime(2026, 9, 7, 6, 59, 59)) is True
    assert sched._is_night(datetime(2026, 9, 7, 7, 0, 0)) is False
    assert sched._is_night(datetime(2026, 9, 7, 22, 59, 59)) is False


async def test_is_risky_derive_and_override():
    """F023 推导:meta.tools 命中 high/critical → True;低危/未声明 → False;
    显式 is_risky 覆盖推导。"""
    sched, _, _ = _mk()
    assert sched._derive_risky(_tpl("x", tools=["fs.delete_file"])) is True
    assert sched._derive_risky(_tpl("x", tools=["fs.delete_file", "ls"])) is True
    assert sched._derive_risky(_tpl("x", tools=["list_dir"])) is False
    assert sched._derive_risky(_tpl("x", tools="fs.delete_file")) is True  # 单串
    assert sched._derive_risky(_tpl("x")) is False
    assert sched._derive_risky(_tpl("x", tools=[])) is False
    assert sched._derive_risky({}) is False
    # 显式覆盖:危险工具模板标 False → 深夜不 block(经 register 验证)
    s2, sess, q = _mk()
    await s2.register("risky.low", "cron", "0 2 * * *",
                      _tpl("高危但显式标低", tools=["fs.delete_file"]),
                      is_risky=False)
    await s2.register("plain.high", "cron", "0 2 * * *", _tpl("高", tools=["x"]),
                      is_risky=True)
    assert s2._jobs["risky.low"].is_risky is False
    assert s2._jobs["plain.high"].is_risky is True
    assert len(q.submits) == 0 and sess.events


# ===================================================================== 管理命令
async def test_pause_resume_gwt10(clock):
    """GWT-10:暂停期零触发零入队;重复暂停幂等;恢复后续触发;重复恢复幂等。"""
    sched, sess, q = _mk()
    clock.value = datetime(2026, 9, 7, 9, 0, 0)
    await sched.register("beat", "interval", "600", _tpl("心跳"))
    await sched.pause("beat")
    j = sched._jobs["beat"]
    assert j.paused is True
    up = sess.of("schedule.updated")
    assert len(up) == 1
    assert up[0].payload["name"] == "beat" and up[0].payload["paused"] is True
    await sched.pause("beat")                       # 幂等:无新事件
    assert len(sess.of("schedule.updated")) == 1
    # 暂停期窗口到点:零触发零入队
    assert await sched._tick(None,
                             now_dt=datetime(2026, 9, 7, 10, 10, 30)) == []
    assert q.submits == []
    # 恢复:重臂 = 恢复时刻 + 600s(暂停期窗口不补跑),更新事件带 next_fire_at
    clock.value = datetime(2026, 9, 7, 10, 10, 30)
    await sched.resume("beat")
    j = sched._jobs["beat"]
    assert j.paused is False
    up = sess.of("schedule.updated")
    assert len(up) == 2
    assert up[1].payload["paused"] is False
    assert parse_iso(up[1].payload["next_fire_at"]) \
        == datetime(2026, 9, 7, 10, 20, 30)
    await sched.resume("beat")                      # 幂等:无新事件
    assert len(sess.of("schedule.updated")) == 2
    # 恢复后到点正常触发(10:20:30 窗;10:15 不触发)
    assert await sched._tick(None,
                             now_dt=datetime(2026, 9, 7, 10, 15, 0)) == []
    assert await sched._tick(None,
                             now_dt=datetime(2026, 9, 7, 10, 20, 30)) == ["beat"]
    assert q.submits == [("心跳", {"source": "schedule:beat"})]


async def test_pause_resume_unknown_busy():
    """pause/resume/remove 未知 job → BUSY(list_jobs 核对名字)。"""
    sched, _, _ = _mk()
    for op in ("pause", "resume", "remove"):
        with pytest.raises(PyHError) as ei:
            await getattr(sched, op)("ghost.job")
        assert ei.value.code == "BUSY", op


async def test_remove_gwt09_and_stops_firing(clock):
    """remove:写 schedule.removed 并摘除;移除后不再触发;重复 remove → BUSY。"""
    sched, sess, q = _mk()
    clock.value = datetime(2026, 9, 7, 1, 50, 0)
    await sched.register("gone", "cron", "0 2 * * *", _tpl("将被移除"))
    await sched.remove("gone")
    assert "gone" not in sched._jobs
    assert sess.of("schedule.removed")[0].payload == {"name": "gone"}
    with pytest.raises(PyHError) as ei:
        await sched.remove("gone")
    assert ei.value.code == "BUSY"
    assert await sched._tick(None, now_dt=datetime(2026, 9, 7, 2, 0, 5)) == []
    assert q.submits == []


async def test_list_jobs_gwt11(clock):
    """GWT-11:list_jobs 与事件计数一致(triggered=trigger 计数;missed=累计)。"""
    sched, sess, _ = _mk()
    clock.value = datetime(2026, 9, 7, 9, 55, 0)
    await sched.register("test.a", "cron", "*/5 * * * *", _tpl("每 5 分"))
    await sched.register("test.b", "interval", "600", _tpl("每 10 分"))
    await sched._tick(None, now_dt=datetime(2026, 9, 7, 10, 0, 30))   # a
    await sched._tick(None, now_dt=datetime(2026, 9, 7, 10, 5, 30))   # a+b
    await sched._tick(None, now_dt=datetime(2026, 9, 7, 10, 15, 30))  # a+b
    infos = await sched.list_jobs()
    by = {i.name: i for i in infos}
    assert set(by) == {"test.a", "test.b"}
    a = by["test.a"]
    assert isinstance(a, JobInfo)
    assert a.kind == "cron" and a.expr == "*/5 * * * *" and a.paused is False
    assert a.triggered == 3 and a.missed == 0
    assert by["test.b"].triggered == 2
    assert by["test.b"].last_fired_at == datetime(2026, 9, 7, 10, 15, 30)
    # missed 事件注入后累计
    await sess.append("schedule.missed",
                      {"job": "test.a", "missed": 2, "since": "x", "until": "y"},
                      actor="system")
    infos = await sched.list_jobs()
    assert {i.name: i.missed for i in infos}["test.a"] == 2


# ===================================================================== 崩溃恢复
async def test_recover_missed_not_caught_up_gwt12(clock):
    """GWT-12 崩溃恢复:宕机窗错过的 cron 只记 schedule.missed 无入队;
    恢复后到点正常触发;重启后 list 与崩溃前一致。"""
    clock.value = datetime(2026, 9, 7, 1, 50, 0)
    sess = FakeSession()
    q1 = FakeQueue()
    s1 = Scheduler(session=sess, task_queue=q1, auto_ticker=False)
    await s1.register("nightly", "cron", "0 2 * * *", _tpl("夜报"))
    # 02:00 正常触发一次(崩溃前)
    await s1._tick(None, now_dt=datetime(2026, 9, 7, 2, 0, 10))
    # —— 模拟进程崩溃:全新 Scheduler 实例,从会话事件重建 ——
    clock.value = datetime(2026, 9, 8, 2, 10, 0)     # 次日夜 02:10 恢复(错过 02:00)
    s2 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    missed_total = await s2.recover(None, now_dt=clock.value)
    assert missed_total == 1
    miss = sess.of("schedule.missed")
    assert len(miss) == 1
    assert miss[0].payload["job"] == "nightly"
    assert miss[0].payload["missed"] == 1
    assert parse_iso(miss[0].payload["since"]) == datetime(2026, 9, 8, 2, 0, 0)
    assert parse_iso(miss[0].payload["until"]) == clock.value
    # 宕机错过零入队(不补跑)
    assert all(not e.type.startswith("task.enqueued") for e in sess.events)
    j = s2._jobs["nightly"]
    assert j.next_fire_at == datetime(2026, 9, 9, 2, 0, 0)   # 重臂未来
    # 恢复后到点正常触发(补触发能力而非补跑)
    q2 = FakeQueue()
    s2.task_queue = q2
    fired = await s2._tick(None, now_dt=datetime(2026, 9, 9, 2, 0, 5))
    assert fired == ["nightly"] and q2.submits
    # list 与崩溃前一致:job 定义/计数(triggered=1,missed=1)可回放
    infos = await s2.list_jobs()
    assert len(infos) == 1
    # triggered = schedule.trigger 事件累计(崩溃前 1 + 恢复后 1 = 2,spec 计数语义)
    assert infos[0].triggered == 2 and infos[0].missed == 1
    assert infos[0].expr == "0 2 * * *" and infos[0].paused is False


async def test_recover_interval_missed_window_only(clock):
    """interval 恢复只计宕机窗(偏离 2 验证):已触发轮不重复记 missed。"""
    clock.value = datetime(2026, 9, 7, 8, 0, 0)
    sess = FakeSession()
    s1 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    await s1.register("beat", "interval", "600", _tpl("心跳"))
    # 08:10 首窗正常触发(崩溃前已执行一轮)
    await s1._tick(None, now_dt=datetime(2026, 9, 7, 8, 10, 30))
    # 崩溃后 08:25 恢复:错过 08:20 一轮(08:10 已触发不应计入)
    clock.value = datetime(2026, 9, 7, 8, 25, 0)
    s2 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    missed_total = await s2.recover(None, now_dt=clock.value)
    assert missed_total == 1
    miss = sess.of("schedule.missed")[0].payload
    assert miss["missed"] == 1                      # 只 1 轮,非自注册起全量
    assert parse_iso(miss["since"]) == datetime(2026, 9, 7, 8, 20, 30)
    j = s2._jobs["beat"]
    assert j.last_fired_at == datetime(2026, 9, 7, 8, 10, 30)
    assert j.next_fire_at == clock.value + timedelta(seconds=600)  # 08:35 起新周期


async def test_recover_future_window_kept(clock):
    """恢复时窗口未到(未错过)→ 保持快照 next_fire_at(崩溃前状态原样续跑)。"""
    clock.value = datetime(2026, 9, 7, 8, 0, 0)
    sess = FakeSession()
    s1 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    await s1.register("beat", "interval", "600", _tpl("心跳"))
    clock.value = datetime(2026, 9, 7, 8, 5, 0)     # 首窗 08:10 前崩溃
    s2 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    assert await s2.recover(None, now_dt=clock.value) == 0
    assert sess.of("schedule.missed") == []
    j = s2._jobs["beat"]
    assert j.next_fire_at == datetime(2026, 9, 7, 8, 10, 0)   # 快照保持
    # 恢复后 08:10 照常触发
    q = FakeQueue()
    s2.task_queue = q
    assert await s2._tick(None, now_dt=datetime(2026, 9, 7, 8, 10, 10)) == ["beat"]
    assert q.submits


async def test_recover_at_expired_missed_not_fired(clock):
    """宕机期 at 过期:记 missed 不再触发(next_fire_at=None 由 remove 清理)。"""
    clock.value = datetime(2026, 9, 7, 10, 0, 0)
    sess = FakeSession()
    s1 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    await s1.register("deadline", "at", "2026-09-07T12:00:00", _tpl("限时任务"))
    clock.value = datetime(2026, 9, 7, 13, 0, 0)    # 宕机跨过 12:00
    s2 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    assert await s2.recover(None, now_dt=clock.value) == 1
    miss = sess.of("schedule.missed")[0].payload
    assert miss["job"] == "deadline" and miss["missed"] == 1
    j = s2._jobs["deadline"]
    assert j.next_fire_at is None                   # 一次性已耗尽
    # remove 清理后 list 为空
    await s2.remove("deadline")
    assert await s2.list_jobs() == []


async def test_recover_paused_skipped_missed(clock):
    """恢复期暂停 job:不核算错过(保持暂停),resume 后正常续触发。"""
    clock.value = datetime(2026, 9, 7, 1, 50, 0)
    sess = FakeSession()
    s1 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    await s1.register("pz", "cron", "0 2 * * *", _tpl("暂停任务"))
    await s1.pause("pz")
    clock.value = datetime(2026, 9, 8, 2, 10, 0)    # 02:00 窗落在宕机期
    s2 = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    assert await s2.recover(None, now_dt=clock.value) == 0
    assert sess.of("schedule.missed") == []         # 暂停中不核算
    j = s2._jobs["pz"]
    assert j.paused is True
    # resume → 重臂未来 → tick 正常触发
    q = FakeQueue()
    s2.task_queue = q
    await s2.resume("pz")
    assert j.paused is False and j.next_fire_at == datetime(2026, 9, 9, 2, 0, 0)
    assert await s2._tick(None, now_dt=datetime(2026, 9, 9, 2, 0, 5)) == ["pz"]
    assert q.submits


async def test_rebuild_orphan_defense_gwt14():
    """GWT-14:孤儿 updated/removed(前无 registered)防御跳过,重建不中断。"""
    sess = FakeSession()
    await sess.append("schedule.updated", {"name": "ghost", "paused": True},
                      actor="system")
    await sess.append("schedule.removed", {"name": "ghost"}, actor="system")
    await sess.append("schedule.registered",
                      {"name": "real", "kind": "interval", "expr": "300",
                       "template": {"intent": "真实任务"}, "is_risky": False,
                       "paused": False,
                       "next_fire_at": "2026-09-07T10:05:00"}, actor="system")
    await sess.append("schedule.updated", {"name": "real", "paused": True},
                      actor="system")
    await sess.append("schedule.removed", {"name": "ghost2"}, actor="system")
    rebuilt = Scheduler._rebuild_from(sess.events)
    assert list(rebuilt._jobs) == ["real"]
    assert rebuilt._jobs["real"].paused is True
    assert rebuilt._jobs["real"].created_seq == 3


async def test_rebuild_registered_updated_removed_flow():
    """事件流 → 注册表一致:注册→暂停→恢复→移除 逐事件回放正确。"""
    sess = FakeSession()
    await sess.append("schedule.registered",
                      {"name": "j1", "kind": "cron", "expr": "0 9 * * *",
                       "template": {"intent": "晨报"}, "is_risky": False,
                       "paused": False,
                       "next_fire_at": "2026-09-08T09:00:00"}, actor="system")
    await sess.append("schedule.updated", {"name": "j1", "paused": True},
                      actor="system")
    await sess.append("schedule.updated",
                      {"name": "j1", "paused": False,
                       "next_fire_at": "2026-09-09T09:00:00"}, actor="system")
    m = Scheduler._rebuild_from(sess.events)
    j = m._jobs["j1"]
    assert j.kind == "cron" and j.expr == "0 9 * * *"
    assert j.paused is False
    assert j.next_fire_at == datetime(2026, 9, 9, 9, 0, 0)
    assert j.is_risky is False
    # 移除后不再出现
    sess.events.append(SimpleNamespace(type="schedule.removed", seq=4,
                                       payload={"name": "j1"}))
    m2 = Scheduler._rebuild_from(sess.events)
    assert m2._jobs == {}


# ===================================================================== 异常隔离
async def test_queue_full_error_eventized_and_isolated(clock):
    """入队失败(QUE-001)→ system.error 显式事件化;单 job 失败不中断其余。"""
    q = FakeQueue(fail_intent="队满任务")
    sched, sess, _ = _mk(queue=q)
    clock.value = datetime(2026, 9, 7, 9, 55, 0)
    await sched.register("ok.job", "cron", "*/5 * * * *", _tpl("正常任务"))
    await sched.register("full.job", "cron", "*/5 * * * *", _tpl("队满任务"))
    fired = await sched._tick(None, now_dt=datetime(2026, 9, 7, 10, 0, 30))
    assert fired == ["ok.job", "full.job"]
    errs = sess.of("system.error")
    assert len(errs) == 1
    assert errs[0].payload["code"] == "QUE-001"
    assert "schedule:full.job" in errs[0].payload["hint"]
    # ok.job 照常入队(隔离指不中断)
    assert len(q.submits) == 1 and q.submits[0][0] == "正常任务"
    # 下一窗口 full.job 若队列恢复仍可触发(窗口已推进,非静默丢)
    assert sched._jobs["full.job"].next_fire_at \
        == datetime(2026, 9, 7, 10, 5, 0)


async def test_ticker_survives_tick_exception_gwt13():
    """GWT-13:单轮 tick 异常 → CYC-999 事件化,泵存活继续下一轮;stop 停闸。"""
    sess = FakeSession()
    sched = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    ticks: list = []
    real_tick = sched._tick

    async def flaky_tick(ctx=None, *, now_dt=None):
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("tick boom")          # 未预期异常
        return await real_tick(ctx, now_dt=now_dt)

    sched._tick = flaky_tick
    calls = {"n": 0}

    async def fake_sleep(_delay):                    # 不真睡;两次唤醒后停闸
        calls["n"] += 1
        if calls["n"] >= 2:
            sched._stopped = True
        await asyncio.sleep(0)

    sched._sleep = fake_sleep
    await sched._ticker()
    assert len(ticks) == 2                           # 首轮崩后第二轮照常
    errs = sess.of("system.error")
    assert errs and errs[0].payload["code"] == "CYC-999"
    # 泵存活:第二轮真实 tick 可正常执行
    assert sess.types().count("system.error") == 1


async def test_ticker_wake_and_tick_via_boundary(clock):
    """泵醒来即 tick(F048 每分钟对齐);异常轮不退出;stop 后不再 tick。"""
    sess = FakeSession()
    sched = Scheduler(session=sess, task_queue=FakeQueue(), auto_ticker=False)
    clock.value = datetime(2026, 9, 7, 10, 59, 30)
    ticks = {"n": 0}

    async def counted_tick(ctx=None, *, now_dt=None):
        ticks["n"] += 1

    sched._tick = counted_tick

    async def fake_sleep(_delay):
        sched._stopped = True                        # 醒来即停(只验证一次 tick)

    sched._sleep = fake_sleep
    await sched._ticker()
    assert ticks["n"] == 1
    sched.stop()                                     # 幂等停闸
    await sched._ticker()
    assert ticks["n"] == 1


# ===================================================================== 真实会话
async def test_real_sessionlog_append_chain():
    """真实 SessionLog(全校验链):schedule.* 事件载荷过词表/模型校验可落盘。"""
    log = SessionLog(sid="s-schedlog1")              # 纯内存真会话
    await log.append("session.created", {"title": "", "model": "m"},
                     actor="system")
    sched = Scheduler(session=log, task_queue=FakeQueue(), auto_ticker=False)
    await sched.register("real.job", "cron", "0 9 * * *", _tpl("真会话任务"))
    await sched.pause("real.job")
    await sched.resume("real.job")
    await sched._tick(None, now_dt=datetime(2099, 1, 1, 9, 0, 10))
    await sched.remove("real.job")
    types = [e.type for e in log.events_after(0)]
    assert "schedule.registered" in types
    assert "schedule.updated" in types
    assert "schedule.trigger" in types
    assert "schedule.removed" in types
    stats = log.stats()
    assert stats["event_count"] == 6                 # created+4 管理+trigger
