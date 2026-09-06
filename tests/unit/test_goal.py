"""tests/unit/test_goal.py — goal 模块单测(F047/F044/S-1/INV-01 契约)。

契约:docs/specs/goal.py.md(权威)+ EVENT-SCHEMA §3.5.2(goal.created/updated/
completed 字段/actor)+ SECURITY S-1(完成只认事件不认文本,completed 恒 system)。

覆盖面(任务要求全项):
    创建/更新/核对/放弃:goal_create(BUSY ≤8 / EVT-100 空描述)、goal_update
        (paused↔active/note/progress clamp/done 后 BUSY/词表外 EVT-100/幂等)、
        goal_check(打勾/报进度/claim_done 过核与追问)、goal_abandon(释放名额/
        终态幂等)、goal 命令分发(create/update/check/abandon/list + EVT-100/101)
    事件化:goal.created/updated actor=agent、goal.completed actor=system(S-1,
        无任何 agent 直写通道);内存状态与事件流一一对应
    事件回放重建(INV-01):rebuild_from_events 后注册表与日志派生状态逐字段一致;
        孤儿 updated 防御跳过;g-N 计数器续号不重号
    防跑偏注入:render_goal_segment(待核项/进度/空板零开销/max_tokens 截断)、
        round_end + drift_reminder(连续 3 轮静默出提醒;有活动/无活动目标不出)

错误断言:EVT-100/EVT-101 走 raise_code;BUSY 为命名字面量(ERR.md §2.11),
PyHError 直接构造,断言 ei.value.code == "BUSY"(与 test_agent 同口径)。
"""
from types import SimpleNamespace

import pytest

from pyharness import events as EV
from pyharness.bus import EventBus
from pyharness.core.goal import (CheckItem, CompletionVerdict, Goal,
                                 GoalCheck, GoalManager)
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError

SID = "s-goal-test01"


# ===================================================================== 替身
class FakeStore:
    """SessionStore 内存替身(总线日志订阅者 record;replay/flush 真源语义)。"""

    def __init__(self) -> None:
        self._rows: list = []
        self.flushed: list[int] = []

    def record(self, type_, payload) -> None:
        if isinstance(payload, EV.Envelope):
            self._rows.append(payload)

    def replay(self):
        return list(self._rows)

    async def flush(self, seq: int) -> None:
        self.flushed.append(seq)


class FakeSession:
    """轻量异步事件落点替身(同步记录 + seq 递增;goals 写路径验证用)。

    带 .events 属性供 _task_failed 事件扫描(与 SessionLog.events_after 同源语义)。
    """

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
        """按类型过滤事件(测试断言用)。"""
        return [e for e in self.events if e.type == type_]


# ===================================================================== 装配
def _wired(sid: str = SID) -> tuple[SessionLog, FakeStore]:
    """真实 SessionLog + 总线 → 存储订阅者(事件一入内存即入真源队列)。"""
    store = FakeStore()
    bus = EventBus()
    for t in EV.EVENT_TYPES:                   # 词表全量订阅(记录即真源)
        bus.subscribe(t, store.record, owner="persistence")
    log = SessionLog(sid=sid, persistence=store, bus=bus)
    return log, store


async def _bootstrap(log: SessionLog) -> None:
    """会话引导:首事件必须是 session.created(seq=1)。"""
    await log.append("session.created", {"title": "", "model": "deepseek-chat"},
                     actor="system")


def _ctx(**kw) -> SimpleNamespace:
    """命令分发 ctx 替身(会话实体:task_id 兜底关联 F044)。"""
    base = {"task_id": None}
    base.update(kw)
    return SimpleNamespace(**base)


# ===================================================================== 创建
async def test_goal_create_writes_created_event_and_registers():
    """创建:返回 g-1 活动 Goal;goal.created 落事件(actor=agent,载荷含 desc/
    task_id);内存注册表与事件同步(created_seq = 事件 seq)。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g = await m.goal_create("把桌面文件按主题归档", task_id="t-3")
    assert isinstance(g, Goal)
    assert g.id == "g-1" and g.status == "active" and g.desc == "把桌面文件按主题归档"
    assert g.task_id == "t-3"
    created = sess.of("goal.created")
    assert len(created) == 1
    ev = created[0]
    assert ev.actor == "agent"
    assert ev.payload["goal_id"] == "g-1"
    assert ev.payload["desc"] == "把桌面文件按主题归档"
    assert ev.payload["task_id"] == "t-3"
    assert g.created_seq == ev.seq                 # 溯源锚点 = 事件 seq
    assert m.list_active() == [g]


async def test_goal_create_blank_desc_evt100():
    """空描述(空串/纯空白)→ EVT-100,零事件零注册。"""
    sess = FakeSession()
    m = GoalManager(sess)
    for bad in ("", "   ", None):
        with pytest.raises(PyHError) as ei:
            await m.goal_create(bad)
        assert ei.value.code == "EVT-100", repr(bad)
    assert sess.events == [] and m._goals == {}


async def test_goal_create_without_session_evt100():
    """未注入 session(只读重建投影)写操作 → EVT-100 显式拒。"""
    m = GoalManager()
    with pytest.raises(PyHError) as ei:
        await m.goal_create("无会话目标")
    assert ei.value.code == "EVT-100"


async def test_goal_active_limit_eight_busy_then_abandon_releases():
    """活动目标 ≤8(F047 边界):第 9 个 → BUSY;abandon 释放名额后可再建。"""
    sess = FakeSession()
    m = GoalManager(sess)
    for i in range(8):
        await m.goal_create(f"子目标 {i}")
    with pytest.raises(PyHError) as ei:
        await m.goal_create("第 9 个活动目标")
    assert ei.value.code == "BUSY"
    assert "上限" in ei.value.ctx["advice"]
    await m.goal_abandon("g-1", reason="范围收缩")
    g9 = await m.goal_create("替代目标")
    assert g9.id == "g-9"                        # g-N 单调不重号


# ===================================================================== list/summary
async def test_list_active_order_and_excludes_terminal():
    """看板:active+paused 按 created_seq 升序;abandoned/done 不入板。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g1 = await m.goal_create("目标甲")
    g2 = await m.goal_create("目标乙")
    g3 = await m.goal_create("目标丙")
    await m.goal_update("g-1", status="paused")  # 活动态之一,仍在板
    await m.goal_abandon("g-2", reason="不做")   # 终态出板
    board = m.list_active()
    assert [g.id for g in board] == ["g-1", "g-3"]
    # done 出板:完整走核对完成 g-3
    g3.checklist.append(CheckItem("核对项"))
    r = await m.goal_check("g-3", checked=["核对项"], progress=1.0, claim_done=True)
    assert r.ok
    assert [g.id for g in m.list_active()] == ["g-1"]
    # summary 人读面
    s = g1.summary()
    assert "[g-1]" in s and "目标甲" in s and "paused" in s and "进度" in s


# ===================================================================== 更新
async def test_goal_update_pause_resume_note_progress_events():
    """更新:status 切 paused/resume、note、progress 均写 goal.updated(actor=agent)
    且内存与事件载荷一致;无变更项幂等不写事件。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("归档任务")
    await m.goal_update("g-1", status="paused")
    up = sess.of("goal.updated")
    assert len(up) == 1 and up[0].actor == "agent"
    assert up[0].payload["status"] == "paused"
    assert m._goals["g-1"].status == "paused"
    await m.goal_update("g-1", note="等审批", progress=0.6)
    up = sess.of("goal.updated")
    assert len(up) == 2
    p = up[1].payload
    assert p["note"] == "等审批" and p["progress"] == 0.6
    assert p["status"] == "paused"               # 载荷必携当前 status(模型词表)
    g = m._goals["g-1"]
    assert g.note == "等审批" and g.progress == 0.6
    await m.goal_update("g-1", status="active")  # resume
    assert m._goals["g-1"].status == "active"
    n_before = len(sess.events)
    await m.goal_update("g-1")                   # 全空 → 幂等
    assert len(sess.events) == n_before


async def test_goal_update_progress_clamped():
    """progress 越界夹到 [0,1](事件载荷与内存同值)。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("夹取测试")
    await m.goal_update("g-1", progress=1.5)
    await m.goal_update("g-1", progress=-0.3)
    ups = sess.of("goal.updated")
    assert ups[0].payload["progress"] == 1.0
    assert ups[1].payload["progress"] == 0.0
    assert m._goals["g-1"].progress == 0.0


async def test_goal_update_invalid_status_evt100():
    """词表外状态(open/done 直传)→ EVT-100,零事件;状态只收 active/paused/abandoned。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("状态测试")
    for bad in ("open", "in_progress", "done"):  # done 只能经核对完成写入
        with pytest.raises(PyHError) as ei:
            await m.goal_update("g-1", status=bad)
        assert ei.value.code == "EVT-100", bad
    assert len(sess.of("goal.updated")) == 0


async def test_goal_update_missing_goal_evt101():
    """goal_id 不存在 → EVT-101(updated 前必有 created 的引用校验)。"""
    m = GoalManager(FakeSession())
    with pytest.raises(PyHError) as ei:
        await m.goal_update("g-99", status="paused")
    assert ei.value.code == "EVT-101"


async def test_goal_update_done_goal_busy_and_no_event():
    """completed 后不可再 updated(EVENT-SCHEMA §3.5.2)→ BUSY,completed 后零新事件。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g = await m.goal_create("完成态目标")
    g.checklist.append(CheckItem("收尾"))
    r = await m.goal_check("g-1", checked=["收尾"], progress=1.0, claim_done=True)
    assert r.ok and g.status == "done"
    n_after_done = len(sess.events)
    with pytest.raises(PyHError) as ei:
        await m.goal_update("g-1", note="补记")
    assert ei.value.code == "BUSY"
    with pytest.raises(PyHError) as ei:
        await m.goal_update("g-1", status="active")
    assert ei.value.code == "BUSY"
    assert len(sess.events) == n_after_done      # 拒写零副作用


# ===================================================================== 核对/完成(S-1)
async def test_goal_check_records_progress_event():
    """核对(非 claim):progress 上报落 goal.updated;reply 回显进度百分比。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("进度上报")
    r = await m.goal_check("g-1", progress=0.4)
    assert isinstance(r, GoalCheck)
    assert r.ok and not r.claim_done and r.missing == []
    assert "40%" in r.reply
    up = sess.of("goal.updated")
    assert len(up) == 1 and up[0].payload["progress"] == 0.4
    assert up[0].actor == "agent"
    assert m._goals["g-1"].progress == 0.4
    # 进度越界在核对口同样 clamp
    await m.goal_check("g-1", progress=9)
    assert m._goals["g-1"].progress == 1.0
    assert sess.of("goal.updated")[-1].payload["progress"] == 1.0


async def test_goal_check_ticks_checklist_items():
    """核对打勾:checked 命中项置 done(瞬态工作数据),其余保持未勾。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g = await m.goal_create("三步走")
    g.checklist.append(CheckItem("步骤A"))
    g.checklist.append(CheckItem("步骤B"))
    r = await m.goal_check("g-1", checked=["步骤A", "不存在的项"])
    assert r.ok
    assert g.checklist[0].done is True
    assert g.checklist[1].done is False          # 未勾保持


async def test_claim_done_insufficient_progress_asks_once():
    """声称完成但 progress < 1.0 → 追问(ok=False,missing 明细),不落 completed。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("半途目标")
    r = await m.goal_check("g-1", progress=0.5, claim_done=True)
    assert not r.ok and r.claim_done
    assert any("进度" in x for x in r.missing)
    assert "追问" not in r.reply and "核对项未过" in r.reply
    assert sess.of("goal.completed") == []       # 证据不足:completed 永不落(S-1)
    assert m._goals["g-1"].status == "active"    # 状态不变


async def test_claim_done_unchecked_checklist_asks():
    """声称完成但 checklist 未全勾 → 追问列出未打勾项;无 goal.completed 事件。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g = await m.goal_create("带清单目标")
    g.checklist.append(CheckItem("子目标一"))
    g.checklist.append(CheckItem("子目标二"))
    r = await m.goal_check("g-1", checked=["子目标一"], progress=1.0,
                           claim_done=True)
    assert not r.ok
    assert any("子目标二" in x for x in r.missing)
    assert "子目标一" not in "\n".join(r.missing)  # 已勾不算缺口
    assert sess.of("goal.completed") == []
    assert m._goals["g-1"].status == "active"


async def test_claim_done_blocked_by_failed_related_task():
    """关联任务(F044)有 task.failed 记录 → 完成被拦,missing 点名任务。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("绑定任务的目标", task_id="t-7")
    await sess.append("task.failed", {"task_id": "t-7", "reason": "预算耗尽"},
                      actor="system")
    g = m._goals["g-1"]
    g.checklist.append(CheckItem("唯一核对项"))
    r = await m.goal_check("g-1", checked=["唯一核对项"], progress=1.0,
                           claim_done=True)
    assert not r.ok
    assert any("t-7" in x for x in r.missing)
    assert sess.of("goal.completed") == []


async def test_goal_check_happy_path_completed_actor_system():
    """全证据过核(进度 1.0 + 清单全勾 + 无 failed 任务)→ goal.completed 落事件,
    actor 恒 system(S-1),状态 done;重复 claim 幂等确认不追加事件。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g = await m.goal_create("完整目标")
    g.checklist.append(CheckItem("全勾项"))
    r = await m.goal_check("g-1", checked=["全勾项"], progress=1.0, claim_done=True)
    assert r.ok and r.reply == "核对通过,目标已完成"
    done = sess.of("goal.completed")
    assert len(done) == 1
    assert done[0].actor == "system"             # 完成只认事件不认文本(S-1)
    assert done[0].payload == {"goal_id": "g-1", "status": "done"}
    assert g.status == "done"
    # claim_done 前置的 progress updated 已落(INV-01:进度变化可回放)
    assert sess.of("goal.updated")[-1].payload["progress"] == 1.0
    # 已完成再核对:幂等确认,不写任何新事件
    n = len(sess.events)
    r2 = await m.goal_check("g-1", claim_done=True)
    assert r2.ok and r2.reply == "该目标已完成"
    assert len(sess.events) == n


async def test_mark_completed_idempotent():
    """框架侧 mark_completed:done 后再调用幂等,不重复落 completed。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g = await m.goal_create("幂等完成")
    await m.mark_completed(g)
    await m.mark_completed(g)
    assert len(sess.of("goal.completed")) == 1
    assert sess.of("goal.completed")[0].actor == "system"


def test_verify_completion_pure_function_direct():
    """verify_completion 纯函数直调:缺进度/缺勾/带 failed 任务 → 明细;全过 → ok。"""
    m = GoalManager()                            # 无 session:任务判定退化为无记录
    g = Goal(id="g-1", desc="纯函数核对", progress=1.0,
             checklist=[CheckItem("甲"), CheckItem("乙", done=True)])
    v = m.verify_completion(g)
    assert isinstance(v, CompletionVerdict)
    assert not v.ok and len(v.missing) == 1 and "甲" in v.missing[0]
    g.checklist[0].done = True
    assert m.verify_completion(g).ok             # 证据齐 → 过核


# ===================================================================== 放弃
async def test_goal_abandon_writes_event_and_idempotent():
    """放弃:写 goal.updated{status:abandoned,note}(actor=agent,词表事件非新类型);
    终态再放弃幂等返回不追加事件。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("将放弃目标")
    await m.goal_abandon("g-1", reason="用户改主意")
    up = sess.of("goal.updated")
    assert len(up) == 1
    assert up[0].payload["status"] == "abandoned"
    assert up[0].payload["note"] == "用户改主意"
    assert up[0].actor == "agent"
    assert m._goals["g-1"].status == "abandoned"
    n = len(sess.events)
    await m.goal_abandon("g-1")                  # 幂等:零新事件
    assert len(sess.events) == n


async def test_goal_abandon_missing_goal_evt101():
    """放弃不存在的 goal → EVT-101。"""
    m = GoalManager(FakeSession())
    with pytest.raises(PyHError) as ei:
        await m.goal_abandon("g-42")
    assert ei.value.code == "EVT-101"


# ===================================================================== 事件回放重建(INV-01)
async def test_rebuild_from_events_matches_live_state():
    """INV-01 溯源回放:全流程(建/暂停/进度/放弃/完成/绑定任务)后,rebuild 重建
    注册表与实时内存逐字段一致(created_seq/desc/status/progress/note/task_id);
    checklist 为瞬态工作数据(词表无字段),重建后为空属契约内。"""
    log, store = _wired()
    await _bootstrap(log)
    m = GoalManager(log)
    await m.goal_create("长任务甲", task_id="t-1")
    await m.goal_create("长任务乙")
    g1 = m._goals["g-1"]
    g1.checklist.append(CheckItem("甲一"))
    g1.checklist.append(CheckItem("甲二"))
    await m.goal_update("g-1", progress=0.5, note="进行中")
    await m.goal_update("g-2", status="paused")
    await m.goal_check("g-1", checked=["甲一", "甲二"], progress=1.0,
                       claim_done=True)          # g-1 完成
    await m.goal_abandon("g-2", reason="范围收缩")
    events = list(log.events_after())
    assert any(e.type == "goal.completed" for e in events)
    rebuilt = GoalManager.rebuild_from_events(events)
    assert set(rebuilt._goals) == {"g-1", "g-2"}
    for gid in ("g-1", "g-2"):
        live, rb = m._goals[gid], rebuilt._goals[gid]
        assert rb.id == live.id
        assert rb.desc == live.desc
        assert rb.status == live.status
        assert rb.progress == live.progress
        assert rb.note == live.note
        assert rb.task_id == live.task_id
        assert rb.created_seq == live.created_seq == \
            next(e.seq for e in events if e.type == "goal.created"
                 and e.payload["goal_id"] == gid)
    assert rebuilt._goals["g-1"].status == "done"
    assert rebuilt._goals["g-2"].status == "abandoned"
    # 与磁盘真源一致(总线落盘队列)
    rebuilt2 = GoalManager.rebuild_from_events(list(store.replay()))
    assert {gid: g.status for gid, g in rebuilt2._goals.items()} == \
        {gid: g.status for gid, g in rebuilt._goals.items()}


def test_rebuild_orphan_updated_skipped_defensively():
    """孤儿 goal.updated(前无 created,坏日志)→ 防御跳过不中断,注册表不受污染。"""
    evs = [
        SimpleNamespace(seq=5, type="goal.updated",
                        payload={"goal_id": "g-9", "status": "paused"}),
        SimpleNamespace(seq=7, type="goal.completed",
                        payload={"goal_id": "g-9", "status": "done"}),
    ]
    m = GoalManager.rebuild_from_events(evs)
    assert m._goals == {}                        # 无 created:全部防御跳过


def test_rebuild_only_created_completed_without_activity():
    """rebuild 只回放事件,不打点活动(漂移判定:会话从未有目标活动 → 不提醒)。"""
    evs = [SimpleNamespace(seq=2, type="goal.created",
                           payload={"goal_id": "g-1", "desc": "重建目标"})]
    m = GoalManager.rebuild_from_events(evs)
    assert m._last_goal_round is None
    assert m.drift_reminder() is None
    assert m.list_active()[0].desc == "重建目标"


async def test_rebuild_gid_counter_continues_without_collision():
    """重建后新建目标续号:现存最高 g-N 之后分配,不与历史重号(F058 折叠联动)。"""
    sess = FakeSession()
    m = GoalManager(sess)
    for i in range(3):
        await m.goal_create(f"旧目标 {i}")
    old_ids = {g.id for g in m.list_active()}
    assert old_ids == {"g-1", "g-2", "g-3"}
    # 重建(只读投影)→ 注入新落点 → 新建目标不重号
    rebuilt = GoalManager.rebuild_from_events(list(sess.events))
    assert rebuilt._goals.keys() == old_ids
    rebuilt.session = FakeSession()
    g = await rebuilt.goal_create("重建后新建")
    assert g.id == "g-4"
    assert g.id not in old_ids                   # 不与历史 id 冲突
    assert rebuilt._goals["g-4"].created_seq == rebuilt.session.events[-1].seq


# ===================================================================== 防跑偏注入(渲染/提醒)
async def test_render_goal_segment_empty_when_no_active():
    """无活动目标 → 空串(零开销不注入)。"""
    m = GoalManager(FakeSession())
    assert m.render_goal_segment() == ""


async def test_render_goal_segment_content_and_truncation():
    """渲染含 id/描述/进度/待核项/关联任务;超长按 max_tokens 截断。"""
    sess = FakeSession()
    m = GoalManager(sess)
    g1 = await m.goal_create("归档桌面", task_id="t-1")
    g1.checklist.append(CheckItem("扫描清单"))
    g1.checklist.append(CheckItem("移动文件"))
    await m.goal_create("清理下载目录")
    seg = m.render_goal_segment()
    assert seg.startswith("<goals>")
    assert "[g-1]" in seg and "归档桌面" in seg
    assert "0%" in seg                            # 进度渲染
    assert "扫描清单,移动文件" in seg              # 待核子目标
    assert "关联任务 t-1" in seg
    assert "清理下载目录" in seg
    short = m.render_goal_segment(max_tokens=30)
    assert len(short) <= 30


async def test_drift_reminder_after_three_quiet_rounds():
    """连续 3 轮无任何 goal 关联活动 → 提醒文本(软提示);有活动轮清零计数。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("主线目标")
    # 第 1 轮有 goal 活动(round_end 收到 goal.updated 事件)→ 静默计数清零
    ev = SimpleNamespace(type="goal.updated",
                         payload={"goal_id": "g-1", "status": "active"})
    m.round_end([ev])
    assert m.drift_reminder() is None             # 刚活动过:不提醒
    m.round_end([])                               # 第 2 轮静默
    m.round_end([])                               # 第 3 轮静默
    assert m.drift_reminder() is None             # 差一轮到阈值
    m.round_end([])                               # 第 4 轮静默:连续 3 轮(2/3/4)
    tip = m.drift_reminder()
    assert tip is not None and "提示" in tip and "偏离" in tip


async def test_drift_no_reminder_when_activity_every_round():
    """每轮都有 goal 事件 → 永不提醒。"""
    m = GoalManager(FakeSession())
    await m.goal_create("持续推进目标")
    for _ in range(6):
        m.round_end([SimpleNamespace(type="goal.updated",
                                     payload={"goal_id": "g-1"})])
        assert m.drift_reminder() is None


async def test_drift_no_reminder_without_active_goals():
    """无活动目标(全放弃/全完成)→ 不判定不提醒。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("短期目标")
    await m.goal_abandon("g-1", reason="不做")
    for _ in range(5):
        m.round_end([])
    assert m.drift_reminder() is None


async def test_drift_no_reminder_never_had_goal_activity():
    """会话从未有目标活动(_last_goal_round=None)→ 不提醒。"""
    m = GoalManager(FakeSession())
    evs = [SimpleNamespace(seq=1, type="goal.created",
                           payload={"goal_id": "g-1", "desc": "x"})]
    m = GoalManager.rebuild_from_events(evs)      # 重建不打活动点
    m.session = FakeSession()
    for _ in range(5):
        m.round_end([])
    assert m.drift_reminder() is None


async def test_round_end_task_enqueued_with_goal_meta_counts_as_activity():
    """携带 goal 溯源的任务入队(task.enqueued payload meta.goal_id)视为目标活动。"""
    m = GoalManager(FakeSession())
    await m.goal_create("目标")
    m.round_end([SimpleNamespace(type="task.enqueued",
                                 payload={"task_id": "t-1", "pos": 1,
                                          "meta": {"goal_id": "g-1"}})])
    m.round_end([])
    m.round_end([])
    assert m.drift_reminder() is None             # 任务活动清零静默计数
    m.round_end([])
    assert m.drift_reminder() is not None         # 再静默 3 轮出提醒
    # 无 goal 溯源的任务入队不算目标活动
    m2 = GoalManager(FakeSession())
    await m2.goal_create("目标2")
    m2.round_end([SimpleNamespace(type="task.enqueued",
                                  payload={"task_id": "t-2", "pos": 1})])
    for _ in range(3):
        m2.round_end([])
    assert m2.drift_reminder() is not None


# ===================================================================== 命令分发(PRD F047 验收直译)
async def test_goal_command_dispatch_full_flow():
    """goal(args, ctx) 五 op 直译:create(带 ctx.task_id 兜底)→ list → update →
    check → abandon,返回人读 summary/reply。"""
    sess = FakeSession()
    m = GoalManager(sess)
    out = await m.goal({"op": "create", "text": "整理项目文档"},
                       _ctx(task_id="t-9"))
    assert "[g-1]" in out and "整理项目文档" in out
    assert sess.of("goal.created")[0].payload["task_id"] == "t-9"  # ctx 兜底 F044
    out = await m.goal({"op": "list"}, _ctx())
    assert "[g-1]" in out and "整理项目文档" in out
    out = await m.goal({"op": "update", "id": "g-1", "status": "paused"}, _ctx())
    assert "paused" in out
    out = await m.goal({"op": "check", "id": "g-1", "progress": 0.3}, _ctx())
    assert "30%" in out
    out = await m.goal({"op": "abandon", "id": "g-1", "reason": "不需要"},
                       _ctx())
    assert "abandoned" in out
    out = await m.goal({"op": "list"}, _ctx())
    assert out == "无活动目标"


async def test_goal_command_unknown_op_and_missing_id_errors():
    """未知 op → EVT-100;list/create 之外缺 id/不存在 → EVT-101。"""
    sess = FakeSession()
    m = GoalManager(sess)
    await m.goal_create("命令错误测试")
    with pytest.raises(PyHError) as ei:
        await m.goal({"op": "nuke"}, _ctx())
    assert ei.value.code == "EVT-100"
    with pytest.raises(PyHError) as ei:
        await m.goal({"op": "check", "id": "g-999"}, _ctx())
    assert ei.value.code == "EVT-101"
    with pytest.raises(PyHError) as ei:
        await m.goal({"op": "update"}, _ctx())
    assert ei.value.code == "EVT-101"


async def test_goal_command_realtime_session_dispatch():
    """真实 SessionLog 全链:goal 命令事件经五步校验落盘(载荷模型过闸)。"""
    log, store = _wired()
    await _bootstrap(log)
    m = GoalManager(log)
    await m.goal({"op": "create", "text": "真实会话目标", "task_id": "t-1"}, _ctx())
    g = m._goals["g-1"]
    g.checklist.append(CheckItem("过闸核对"))
    r = await m.goal_check("g-1", checked=["过闸核对"], progress=1.0,
                           claim_done=True)
    assert r.ok
    events = [e for e in log.events_after() if e.type.startswith("goal.")]
    assert [e.type for e in events] == ["goal.created", "goal.updated",
                                        "goal.completed"]
    assert all(e.payload["goal_id"] == "g-1" for e in events)
    assert events[-1].actor == "system"          # completed 恒 system(S-1)
    assert store.replay() and store.replay()[-1].type == "goal.completed"
