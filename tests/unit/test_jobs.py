"""jobs 模块单测 — 契约:specs/jobs.py.md(F051/F025/F032/F040/F044)+ EVENT-SCHEMA
§3.5.3(job.started/completed/failed 字段级)+ ERR.md §2.11(JOB-001)+ SECURITY §5
(owner 授权:job_id 非机密,查询/取消只认 owner)

覆盖面:
    start:返回 j-N 单调 id;job.started 落盘(actor=system,信封 task_id=job:<id>);
        空/超长 intent EVT-100;ctx.owner 缺失 EVT-100;父会话已 finished → EVT-104
        且登记回滚;返回不阻塞父会话(可继续对话)
    执行链:segment.start 先于执行、job.completed/failed 先于 segment.end;
        成功 elapsed_ms ≥ 0;runner 返回 reason(budget/max_turns…)归一 failed;
        PyHError → failed(码);裸异常 → CYC-999;未装配 runner → CYC-999
    并发:在途 4 个时第 5 个 start → JOB-001;任一终态释放额度后可再提交
    status:state/progress(段内 todo.updated 最新清单派生)/todo_summary/log_tail≤10/
        error;无 todo → progress=None、"0/0 项";未知 job → EVT-101
    owner 授权:非 owner 查/取消/取日志 → GRD-401 且无泄漏;system 放行;list_owned
        只含 by 名下;job_id 猜中 ≠ 有权
    cancel:running → True + system.cancelled(user-cancel) + 终态 failed(cancelled);
        终态后再 cancel → False 幂等;未知 → EVT-101;任务未开始即取消也落终态
    logs:段切片只含本 job 事件(信封/payload task_id 并集)、seq 升序、after_seq
        增量语义;不泄漏他人 job 事件
    通知:bus 广播 job.completed/failed;completed + notify_to → agent.message 注入;
        会话已 finished 不补写
    会话关闭钩子:在途 job 全落 failed(session-closed),终态事件先于 session.finished
    清理:终态超期句柄被 reap_expired 摘除、事件仍留日志;在途/未超期不摘

执行器/存储全为注入替身——本模块不触真实 agent_loop/llm/持久化;并发观测用门控
runner,取消/终态一律 await 任务收尾(不用裸 sleep 碰运气)。
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from pyharness.bus import EventBus
from pyharness.core.jobs import JobManager
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError

SID = "s-jobtest01"   # Envelope.session_id 需 ≥8 字符


def _ctx(owner: str = "alice", **kw) -> SimpleNamespace:
    """通道上下文替身:owner=提交方身份,owner_channel=完成通知目标通道。"""
    base = {"owner": owner, "owner_channel": f"web:{owner}", "budget": None}
    base.update(kw)
    return SimpleNamespace(**base)


# ===================================================================== 替身
class FakeRunner:
    """执行器替身(spec _run 契约:run(ctx, *, intent, task_id, budget))。

    gate: 非 None 时每次 run 先 await gate.wait()(长任务/并发闸:set 前全阻塞,
        取消在此注入);todos_seq: 段内逐条落 todo.updated(进度派生源);
    message: 段内落一条 agent.message(执行副作用);reason: run 返回值
        (None=成功;带 .reason 的对象按 RunResult 语义归一);exc: 抛出的异常
        (PyHError → failed(码)/裸异常 → CYC-999)。
    """

    def __init__(self, session, *, gate=None, todos_seq=None, message=True,
                 reason=None, exc=None) -> None:
        self.session = session
        self.gate = gate
        self.todos_seq = todos_seq or []
        self.message = message
        self.reason = reason
        self.exc = exc
        self.entered = asyncio.Event()
        self.calls: list = []

    async def run(self, ctx, *, intent, task_id, budget=None):
        self.calls.append({"task_id": task_id, "intent": intent, "budget": budget})
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()               # 阻塞点:取消/放行都经此
        if self.exc is not None:
            raise self.exc
        for todos in self.todos_seq:             # 进度源:段内 todo.updated
            await self.session.append("todo.updated",
                                      {"task_id": task_id, "todos": todos},
                                      actor="agent", task_id=task_id)
        if self.message:
            await self.session.append("agent.message",
                                      {"content": f"[{task_id}] ok", "model": "mock"},
                                      actor="agent", task_id=task_id)
        return self.reason


# ===================================================================== 工具
async def _new_session() -> SessionLog:
    """内存会话(SessionLog 纯内存模式:无 bus/持久化,append 即落内存)。"""
    s = SessionLog(sid=SID)
    await s.append("session.created", {"title": "", "model": "mock"}, actor="system")
    return s


def _events(s) -> list:
    """会话全量事件(seq 升序)。"""
    return list(s.events_after(0))


def _types(s) -> list:
    """事件流 (type, payload.reason/…) 摘要序列。"""
    return [(e.type, (e.payload or {}).get("reason")) for e in _events(s)]


def _job_events(s, task_id: str) -> list:
    """日志侧 job 切片(与 logs 同口径:信封/payload task_id 并集,seq 升序)。"""
    return [e for e in _events(s)
            if e.task_id == task_id or (e.payload or {}).get("task_id") == task_id]


async def _settle(mgr: JobManager, job_id: str, timeout: float = 5.0) -> None:
    """等 job 底层任务收尾(终态已落;超时护栏防悬挂)。"""
    j = mgr._running[job_id]
    assert j.task is not None, f"job {job_id} 任务未创建"
    await asyncio.wait_for(
        asyncio.gather(j.task, return_exceptions=True), timeout)


async def _wait_entered(runner: FakeRunner, timeout: float = 2.0) -> None:
    """等 runner 进入执行(长任务已阻塞于 gate 的同步点)。"""
    await asyncio.wait_for(runner.entered.wait(), timeout)


def _failed_reason_events(s) -> list:
    """全部 job.failed 事件的 (job_id, reason) 序列。"""
    return [(e.payload.get("job_id"), e.payload.get("reason"))
            for e in _events(s) if e.type == "job.failed"]


# ===================================================================== start
async def test_start_returns_monotonic_ids_and_records_started_event():
    """F051:start 返回 j-N 单调 id;job.started 落盘(actor=system,信封带 task_id)。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    jid1 = await mgr.start("任务甲", _ctx())
    jid2 = await mgr.start("任务乙", _ctx())
    assert (jid1, jid2) == ("j-1", "j-2")
    starts = [e for e in _events(s) if e.type == "job.started"]
    assert [e.payload["job_id"] for e in starts] == ["j-1", "j-2"]
    assert all(e.actor == "system" for e in starts)
    assert [e.task_id for e in starts] == ["job:j-1", "job:j-2"]  # 信封归属
    assert mgr._running[jid1].state == "running"
    await _settle(mgr, jid1)
    await _settle(mgr, jid2)


async def test_start_does_not_block_parent_conversation():
    """长任务在跑时父会话照常可写(共享 JSONL,互不阻塞)。"""
    s = await _new_session()
    gate = asyncio.Event()                        # 不放行 → job 长跑
    mgr = JobManager(s, runner=FakeRunner(s, gate=gate))
    jid = await mgr.start("慢任务", _ctx())
    await _wait_entered(mgr._runner)
    # 父会话可继续对话:事件与 job 事件交错于同一日志(seq 单调)
    await s.append("user.message", {"content": "继续聊"}, actor="user")
    seqs = [e.seq for e in _events(s)]
    assert seqs == sorted(seqs)
    assert mgr._running[jid].state == "running"   # job 仍在跑,互不影响
    gate.set()
    await _settle(mgr, jid)
    assert mgr._running[jid].state == "completed"


async def test_start_validates_intent_and_owner():
    """空/超长 intent → EVT-100;ctx.owner 缺失(非通道身份)→ EVT-100。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    with pytest.raises(PyHError) as ei:
        await mgr.start("   ", _ctx())
    assert ei.value.code == "EVT-100"
    with pytest.raises(PyHError) as ei:
        await mgr.start("x" * (64 * 1024 + 1), _ctx())
    assert ei.value.code == "EVT-100"
    with pytest.raises(PyHError) as ei:           # owner 必须由通道上下文注入
        await mgr.start("任务", SimpleNamespace(owner=None))
    assert ei.value.code == "EVT-100"
    assert mgr._running == {}


async def test_start_on_finished_session_evt104_and_rollback():
    """父会话 finished 后 start → EVT-104,登记回滚(事件没落成 = 没启动)。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    await s.append("session.finished", {"reason": "idle"}, actor="system")
    with pytest.raises(PyHError) as ei:
        await mgr.start("迟到任务", _ctx())
    assert ei.value.code == "EVT-104"
    assert mgr._running == {}                     # 无脏句柄


# ===================================================================== 执行链
async def test_run_success_event_chain_and_segment():
    """成功链:job.started → segment.start → (执行) → job.completed → segment.end。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    jid = await mgr.start("打包发布", _ctx())
    await _settle(mgr, jid)
    evs = [e for e in _events(s)]
    types = [e.type for e in evs]
    segs = [i for i, t in enumerate(types) if t == "segment.start"]
    ends = [i for i, t in enumerate(types) if t == "segment.end"]
    comp = types.index("job.completed")
    assert segs and ends and comp < ends[0]       # 终态事件先于 segment.end
    assert comp > segs[0]                          # 段锚先于执行收尾
    # segment.start/end 配对(segment.end.start_seq 回指 segment.start 的 seq)
    st = evs[segs[0]].payload
    en = evs[ends[0]].payload
    assert en["task_id"] == st["task_id"] == f"job:{jid}" and en["start_seq"] == evs[segs[0]].seq
    # job.completed 载荷(模型:job_id + elapsed_ms,无 reason)
    done = evs[comp]
    assert done.payload["job_id"] == jid and done.payload["elapsed_ms"] >= 0
    assert mgr._running[jid].state == "completed"
    assert mgr._running[jid].expires_at is not None


async def test_run_failure_pyheerror_reason_is_code():
    """runner 抛 PyHError → job.failed(reason=错误码),状态 failed、error=码。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(
        s, exc=PyHError("LLM-310", ctx={"hint": "mock 链尾全败"})))
    jid = await mgr.start("会失败的活", _ctx())
    await _settle(mgr, jid)
    fails = [e for e in _events(s) if e.type == "job.failed"]
    assert fails and fails[0].payload["reason"] == "LLM-310"
    st = await mgr.status(jid, by="alice")
    assert (st.state, st.error) == ("failed", "LLM-310")


async def test_run_unexpected_exception_cyc999():
    """runner 抛裸异常 → failed(CYC-999)兜底,异常不外逃。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s, exc=RuntimeError("boom")))
    jid = await mgr.start("会崩的活", _ctx())
    await _settle(mgr, jid)                       # 任务正常收尾(不外逃)
    assert (mgr._running[jid].state, mgr._running[jid].reason) == ("failed", "CYC-999")


async def test_run_budget_kill_reason_budget():
    """F032 超预算:runner 返回 RunResult(reason=budget) → 直接 kill(budget)。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(
        s, reason=SimpleNamespace(reason="budget")))
    jid = await mgr.start("超预算的活", _ctx())
    await _settle(mgr, jid)
    st = await mgr.status(jid, by="alice")
    assert (st.state, st.error) == ("failed", "budget")
    assert any(e.payload.get("reason") == "budget"
               for e in _events(s) if e.type == "job.failed")


async def test_run_no_runner_fast_fail_cyc999():
    """未装配执行器 → CYC-999 快速失败(防"伪造已执行")。"""
    s = await _new_session()
    mgr = JobManager(s)                           # runner=None
    jid = await mgr.start("无执行器的活", _ctx())
    await _settle(mgr, jid)
    assert (mgr._running[jid].state, mgr._running[jid].reason) == ("failed", "CYC-999")


# ===================================================================== 并发
async def test_concurrency_limit_job001_and_release():
    """JOB-001:在途 4 个时第 5 个被拒;任一终态释放额度后可再提交。"""
    s = await _new_session()
    gate = asyncio.Event()
    mgr = JobManager(s, runner=FakeRunner(s, gate=gate))
    started = [await mgr.start(f"并发任务{i}", _ctx()) for i in range(4)]
    await asyncio.sleep(0)                        # 让 4 个任务都进入 gate
    assert mgr._active_count() == 4
    with pytest.raises(PyHError) as ei:           # 第 5 个并发 → JOB-001
        await mgr.start("第 5 个", _ctx())
    assert ei.value.code == "JOB-001"
    gate.set()                                    # 放行 → 4 个先后终态
    for jid in started:
        await _settle(mgr, jid)
    assert mgr._active_count() == 0
    jid5 = await mgr.start("终态后可再提交", _ctx())   # 额度释放 → 放行
    await _settle(mgr, jid5)
    assert mgr._running[jid5].state == "completed"


async def test_concurrency_limit_custom_value():
    """并发上限可注入(装配方按需收紧;默认 4)。"""
    s = await _new_session()
    gate = asyncio.Event()
    mgr = JobManager(s, runner=FakeRunner(s, gate=gate), limit=2)
    j1 = await mgr.start("a", _ctx())
    j2 = await mgr.start("b", _ctx())
    await asyncio.sleep(0)
    with pytest.raises(PyHError) as ei:
        await mgr.start("c", _ctx())
    assert ei.value.code == "JOB-001"
    gate.set()
    for jid in (j1, j2):
        await _settle(mgr, jid)


# ===================================================================== status
async def test_status_progress_derived_from_todo():
    """进度从段内 todo.updated 最新清单派生(done/总数);多次更新取最新。"""
    s = await _new_session()
    todos_a = [{"id": 1, "text": "扫描", "done": True},
               {"id": 2, "text": "归档", "done": False},
               {"id": 3, "text": "报告", "done": False}]
    todos_b = [{"id": 1, "text": "扫描", "done": True},
               {"id": 2, "text": "归档", "done": True},
               {"id": 3, "text": "报告", "done": True}]
    mgr = JobManager(s, runner=FakeRunner(s, todos_seq=[todos_a, todos_b]))
    jid = await mgr.start("归档任务", _ctx())
    await _settle(mgr, jid)
    st = await mgr.status(jid, by="alice")
    assert st.job_id == jid and st.state == "completed"
    assert st.progress == 1.0 and st.todo_summary == "3/3 项"
    # 另起一个只有单次更新的 job,验证中间态数值(1/3)
    s2 = await _new_session()
    mgr2 = JobManager(s2, runner=FakeRunner(s2, todos_seq=[todos_a]))
    jid2 = await mgr2.start("进行中任务", _ctx())
    await _settle(mgr2, jid2)
    st2 = await mgr2.status(jid2, by="alice")
    assert st2.progress == round(1 / 3, 3) and st2.todo_summary == "1/3 项"
    assert st2.log_tail and len(st2.log_tail) <= 10
    assert {"seq", "type", "payload"} <= set(st2.log_tail[-1])


async def test_status_no_todo_progress_none():
    """段内无 todo → progress=None、todo_summary "0/0 项";log_tail ≤10。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s, message=False))
    jid = await mgr.start("无清单任务", _ctx())
    await _settle(mgr, jid)
    st = await mgr.status(jid, by="alice")
    assert st.progress is None and st.todo_summary == "0/0 项"
    assert len(st.log_tail) <= 10


async def test_status_unknown_job_evt101():
    """job 不存在/已清理 → EVT-101(任何请求方同码,不泄漏存在性以外信息)。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    for by in ("alice", "bob", "system"):
        with pytest.raises(PyHError) as ei:
            await mgr.status("j-999", by=by)
        assert ei.value.code == "EVT-101"


# ===================================================================== owner 授权
async def test_owner_isolation_status_and_system_bypass():
    """非 owner 查他人 job → GRD-401 且无泄漏;system 放行。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s, message=False))
    jid = await mgr.start("alice 的秘密任务", _ctx(owner="alice"))
    await _settle(mgr, jid)
    with pytest.raises(PyHError) as ei:           # 知道 id ≠ 有权
        await mgr.status(jid, by="bob")
    assert ei.value.code == "GRD-401"
    assert "秘密" not in str(ei.value)            # scope-hidden:不泄漏 intent
    assert ei.value.ctx.get("intent") is None
    st = await mgr.status(jid, by="system")       # system 旁路
    assert st.state == "completed"
    st = await mgr.status(jid, by="alice")        # owner 本尊
    assert st.state == "completed"


async def test_owner_isolation_list_owned():
    """list_owned 只列 by 名下(含终态 7 天内);system 列全部。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    alice_ids = [await mgr.start(f"alice{i}", _ctx(owner="alice"))
                 for i in range(2)]
    bob_id = await mgr.start("bob 的活", _ctx(owner="bob"))
    assert set(mgr.list_owned("alice")) == set(alice_ids)
    assert bob_id not in mgr.list_owned("alice")  # 他人 job 不可见
    assert set(mgr.list_owned("bob")) == {bob_id}
    assert set(mgr.list_owned("system")) == set(alice_ids) | {bob_id}
    for jid in alice_ids + [bob_id]:
        await _settle(mgr, jid)


# ===================================================================== cancel
async def test_cancel_running_job_normalizes_failed_cancelled():
    """F025:cancel(running) → True + system.cancelled + job.failed(cancelled)。"""
    s = await _new_session()
    gate = asyncio.Event()
    mgr = JobManager(s, runner=FakeRunner(s, gate=gate))
    jid = await mgr.start("长任务", _ctx())
    await _wait_entered(mgr._runner)
    assert await mgr.cancel(jid, by="alice") is True
    await _settle(mgr, jid)
    cancels = [e for e in _events(s) if e.type == "system.cancelled"]
    assert cancels and cancels[0].payload["what"] == f"job:{jid}"
    fails = [e for e in _events(s) if e.type == "job.failed"]
    assert fails[-1].payload["reason"] == "cancelled"
    st = await mgr.status(jid, by="alice")
    assert (st.state, st.error) == ("failed", "cancelled")


async def test_cancel_before_task_starts_still_terminates():
    """取消落在任务尚未开始窗口 → 终态仍落(旗标兜底,不悬挂不占并发)。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    jid = await mgr.start("刚提交就被取消", _ctx())
    # 不 await entered:紧接取消,任务可能尚未开始执行
    assert await mgr.cancel(jid, by="alice") is True
    await _settle(mgr, jid)
    st = await mgr.status(jid, by="alice")
    assert (st.state, st.error) == ("failed", "cancelled")
    assert mgr._active_count() == 0               # 不占并发额度


async def test_cancel_terminal_idempotent_false():
    """已终态 job 再 cancel → False 幂等(防重放)。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s))
    jid = await mgr.start("秒完任务", _ctx())
    await _settle(mgr, jid)
    assert mgr._running[jid].terminal
    assert await mgr.cancel(jid, by="alice") is False


async def test_cancel_authorization_and_unknown():
    """越权取消 → GRD-401;未知 job → EVT-101。"""
    s = await _new_session()
    gate = asyncio.Event()
    mgr = JobManager(s, runner=FakeRunner(s, gate=gate))
    jid = await mgr.start("他人任务", _ctx(owner="alice"))
    await _wait_entered(mgr._runner)
    with pytest.raises(PyHError) as ei:           # 猜中 id 也不可取消他人 job
        await mgr.cancel(jid, by="mallory")
    assert ei.value.code == "GRD-401"
    with pytest.raises(PyHError) as ei:
        await mgr.cancel("j-404", by="alice")
    assert ei.value.code == "EVT-101"
    gate.set()
    await _settle(mgr, jid)
    assert mgr._running[jid].state == "completed"  # 越权取消零副作用


# ===================================================================== logs
async def test_logs_slice_boundary_and_after_seq():
    """logs:段闭区间切片只含本 job 事件(seq 升序);after_seq 增量拉取。"""
    s = await _new_session()
    runner = FakeRunner(s)
    mgr = JobManager(s, runner=runner)
    jid1 = await mgr.start("任务一", _ctx(owner="alice"))
    await _settle(mgr, jid1)
    jid2 = await mgr.start("任务二", _ctx(owner="alice"))
    await _settle(mgr, jid2)
    # job 切片:只含本 job 归属事件(信封/payload task_id 并集),互不掺入
    logs1 = await mgr.logs(jid1, by="alice")
    logs2 = await mgr.logs(jid2, by="alice")
    assert logs1 and logs2
    assert logs1[-1].seq < logs2[0].seq          # 先后提交 → 段切片严格分界
    tags1 = {e.task_id or (e.payload or {}).get("task_id") for e in logs1}
    assert tags1 == {f"job:{jid1}"}               # 不泄漏他人 job 事件
    seqs1 = [e.seq for e in logs1]
    assert seqs1 == sorted(seqs1)
    # after_seq:增量拉取 = 全量 - 起点前
    first = logs1[0].seq
    tail = await mgr.logs(jid1, after_seq=first, by="alice")
    assert tail == logs1[1:]
    # 段锚在切片内(闭区间回放:segment.start/end 都在)
    assert any(e.type == "segment.start" for e in logs1)
    assert any(e.type == "segment.end" for e in logs1)


async def test_logs_authorization():
    """logs 越权 → GRD-401;未知 → EVT-101。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s, message=False))
    jid = await mgr.start("任务", _ctx(owner="alice"))
    await _settle(mgr, jid)
    with pytest.raises(PyHError) as ei:
        await mgr.logs(jid, by="bob")
    assert ei.value.code == "GRD-401"
    with pytest.raises(PyHError) as ei:
        await mgr.logs("j-404", by="alice")
    assert ei.value.code == "EVT-101"


# ===================================================================== 通知
async def test_notify_bus_and_message_injection():
    """终态总线广播(job.completed/job.failed);completed+notify_to 注入摘要。"""
    s = await _new_session()
    bus = EventBus()
    seen = []
    bus.subscribe("job.*", lambda t, p: seen.append((t, p)), owner="test")
    mgr = JobManager(s, runner=FakeRunner(s, message=False), bus=bus)
    jid = await mgr.start("通知任务", _ctx(owner="alice"))  # owner_channel=web:alice
    await _settle(mgr, jid)
    assert any(t == "job.completed" and p["job_id"] == jid for t, p in seen)
    msgs = [e for e in _events(s)
            if e.type == "agent.message" and "[后台任务" in e.payload["content"]]
    assert len(msgs) == 1 and jid in msgs[0].payload["content"]
    assert jid[:2] in msgs[0].payload["content"]
    # failed 也广播,但不注入摘要消息
    s2 = await _new_session()
    seen2 = []
    bus2 = EventBus()
    bus2.subscribe("job.*", lambda t, p: seen2.append((t, p)), owner="test")
    mgr2 = JobManager(s2, runner=FakeRunner(
        s2, exc=RuntimeError("x"), message=False), bus=bus2)
    jid2 = await mgr2.start("失败通知任务", _ctx(owner="alice"))
    await _settle(mgr2, jid2)
    assert any(t == "job.failed" and p["job_id"] == jid2 for t, p in seen2)
    msgs2 = [e for e in _events(s2)
             if e.type == "agent.message" and "[后台任务" in e.payload["content"]]
    assert msgs2 == []                            # 失败不注入可见摘要


async def test_notify_skipped_when_session_finished():
    """会话已 finished 后不补写通知(仅日志;EVT-104 兜底)。"""
    s = await _new_session()
    mgr = JobManager(s, runner=FakeRunner(s, message=False))
    await s.append("session.finished", {"reason": "idle"}, actor="system")
    before = len(_events(s))
    # 直接驱动 _notify:completed + notify_to,但会话已终态 → 跳过不抛
    await mgr._notify(SimpleNamespace(
        id="j-9", state="completed", notify_to="web:alice",
        intent="通知测试", elapsed_ms=5, reason=None))
    assert len(_events(s)) == before              # 无任何补写


# ===================================================================== 会话关闭
async def test_session_closing_cancels_all_inflight():
    """child-first:会话关闭 → 在途 job 全落 failed(session-closed),先于 finished。"""
    s = await _new_session()
    gate = asyncio.Event()
    mgr = JobManager(s, runner=FakeRunner(s, gate=gate))
    started = [await mgr.start(f"在途任务{i}", _ctx()) for i in range(3)]
    await asyncio.sleep(0)                        # 让任务进入执行
    await mgr._on_session_closing("agent.close", {"reason": "idle"})
    for jid in started:
        j = mgr._running[jid]
        assert (j.state, j.reason) == ("failed", "session-closed")
    fails = _failed_reason_events(s)
    assert sorted(fails) == sorted((jid, "session-closed") for jid in started)
    # 终态事件先于 session.finished(钩子序;finished 后回写会撞 EVT-104)
    await s.append("session.finished", {"reason": "idle"}, actor="system")
    assert mgr._active_count() == 0               # 无孤儿协程/无在途
    after_finished = len(_events(s))
    for jid in started:
        # 已终态 → cancel 幂等 False(零副作用,不写任何新事件)
        assert await mgr.cancel(jid, by="alice") is False
    assert len(_events(s)) == after_finished      # finished 后无任何回写


# ===================================================================== 清理
async def test_reap_expired_removes_handles_keeps_events():
    """7 天清理:终态超期句柄被摘、事件永留 JSONL;在途/未超期不摘。"""
    s = await _new_session()
    runner = FakeRunner(s)                        # 初始无门:首个 job 正常完成
    mgr = JobManager(s, runner=runner)
    done_id = await mgr.start("已完成", _ctx())
    await _settle(mgr, done_id)
    gate = asyncio.Event()
    runner.gate = gate                            # 后续 job 挂门(在途模拟)
    live_id = await mgr.start("仍在跑", _ctx())
    await _wait_entered(runner)
    # 未超期 → 不摘;在途 → 不摘
    assert await mgr.reap_expired() == 0
    assert done_id in mgr._running and live_id in mgr._running
    # 强制过期(终态 + 超 retention)→ 摘句柄
    mgr._running[done_id].expires_at = time.time() - 1
    assert await mgr.reap_expired() == 1
    assert done_id not in mgr._running
    assert live_id in mgr._running                # 在途句柄不受影响
    with pytest.raises(PyHError) as ei:           # 已清理 → EVT-101
        await mgr.status(done_id, by="alice")
    assert ei.value.code == "EVT-101"
    # 事件仍在 JSONL(审计/重放不受影响,INV-01)
    assert any(e.type == "job.completed" and e.payload["job_id"] == done_id
               for e in _events(s))
    # 收尾:放行并结算在途 job
    gate.set()
    await _settle(mgr, live_id)
