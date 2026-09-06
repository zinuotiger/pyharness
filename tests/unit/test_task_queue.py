"""task_queue 模块单测 — 契约:specs/task_queue.py.md(F043/F044/F025/F015)+ EVENT-SCHEMA
§3.5.1/§3.5.3 + ERR.md §2.11(QUE-001)

覆盖面:
    FIFO 顺序 + pos:enqueued pos 1 起;等待序 = 提交序;drain 序 = 提交序
    单 running(F043):并发观测 max_active==1;任务 N+1 的 started 必晚于 N 的终态
    段围栏(F044):segment.start/end 配对(start_seq 回指)、段内事件带 task_id、
        闲聊(无 task_id)不入段、segment.start 强同步(flush 落盘观测)、嵌套开段/不配对关段 EVT-100
    暂停/恢复(F015):暂停不弹任务、恢复自动接续、重复挂起合并(单 suspended)、多原因、
        resumed 仅全解除后单次、非法 resume 幂等、空原因 EVT-100
    取消(F025):running 取消 → system.cancelled + failed(cancelled) + 队首自动接 +
        wait_for code=cancelled;waiting 摘除;未知/终态幂等 False
    终态等待:成功 TaskResult、超时 BUSY(任务不受牵连可再等)、未知 EVT-100、
        暂停期 wait_for(timeout=None) 不饿死
    错误面:空意图 EVT-100、队满 QUE-001、未装配执行器 CYC-999、runner 失败分级
    状态查询:status.running/waiting/paused/reasons/depth 与事件流一致(GWT-F043-03)

执行器/存储全为注入替身——本模块不触 agent_loop/llm/真实持久化;时间相关断言
一律用事件 gate/终态等待驱动,不用裸 sleep 碰运气。
"""
import asyncio

import pytest

from pyharness.core.session import SessionLog
from pyharness.core.task_queue import QueueStatus, TaskQueue
from pyharness.errors import PyHError

SID = "s-taskqu01"   # Envelope.session_id 需 ≥8 字符


# ===================================================================== 替身
class Store:
    """SessionStore 替身:只记录强同步 flush(seq)——观测 segment.start 先落盘。"""

    def __init__(self) -> None:
        self.flushed: list[int] = []

    def replay(self):
        return iter(())

    async def flush(self, seq: int) -> None:
        self.flushed.append(seq)


class FakeRunner:
    """执行器替身:并发/顺序观测 + 门控 + 故障注入(F043/F025 单测替身)。

    gate: 非 None 时每次 run_for_task 先 await gate.wait()(set 后全放行);
    exc: run_for_task 抛出的异常(PyHError → failed(error=code)/裸异常 → CYC-999);
    sleep: 每次执行强制让出时间片(若泵并行放行,active 必在此重叠);
    段内模拟 agent_loop 副作用:append agent.message 且 envelope 绑定 task_id(F044)。
    cancel_current: 记录调用(取消传播主通道 = 队列对执行子任务的协作取消)。
    """

    def __init__(self, session, *, gate=None, exc=None, log_events=True,
                 sleep: float = 0.0, exc_once: bool = False) -> None:
        self.session = session
        self.gate = gate
        self.exc = exc
        self.exc_once = exc_once           # exc 只触发一次(首任务失败,后续成功)
        self.log_events = log_events
        self.sleep = sleep
        self.runs: list = []             # (task_id, intent) 执行序(FIFO 断言)
        self.active = 0                  # 当前在跑数(串行泵下恒 ≤1)
        self.max_active = 0              # 并发上限观测(单 running 断言)
        self.entered = asyncio.Event()   # 首个任务已进入执行(同步点)
        self.cancel_calls: list = []     # cancel_current 调用记录

    def cancel_current(self, task_id):   # 协作钩子:记录即可(传播靠队列兜底)
        self.cancel_calls.append(task_id)

    async def run_for_task(self, task):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.runs.append((task.id, task.intent))
        self.entered.set()
        try:
            if self.gate is not None:
                await self.gate.wait()          # 长任务模拟(阻塞至放行/取消)
            if self.sleep:
                await asyncio.sleep(self.sleep)  # 让出:若并发,此处必重叠
            if self.log_events:                  # 段内事件:envelope 绑 task_id
                await self.session.append("agent.message",
                    {"content": f"[{task.id}] ok", "model": "mock"},
                    actor="agent", task_id=task.id)
            if self.exc is not None:
                exc = self.exc
                if self.exc_once:
                    self.exc = None              # 仅首任务注入故障(后续任务照常)
                raise exc
        finally:
            self.active -= 1


# ===================================================================== 工具
async def _new_session(persistence=None) -> SessionLog:
    """内存会话(SessionLog 纯内存模式;可选注入 Store 观测强同步 flush)。"""
    s = SessionLog(sid=SID, persistence=persistence)
    await s.append("session.created", {"title": "", "model": "mock"}, actor="system")
    return s


def _events(s) -> list:
    """会话全量事件(seq 升序)。"""
    return list(s.events_after(0))


def _types(s) -> list:
    """事件流摘要 (type, payload.task_id) 序列(断言顺序用)。"""
    return [(e.type, (e.payload or {}).get("task_id")) for e in _events(s)]


def _tasks_events(s, *types) -> list:
    """按类型过滤的 (seq, type, payload) 三元组(段围栏/顺序断言用)。"""
    return [(e.seq, e.type, e.payload)
            for e in _events(s) if e.type in types]


async def _drain_ok(q: TaskQueue, ids: list) -> None:
    """逐任务等待终态(超时护栏防悬挂),断言全部成功。"""
    for tid in ids:
        res = await asyncio.wait_for(q.wait_for(tid), timeout=3.0)
        assert res.ok, f"任务 {tid} 应成功,实际 code={res.code}"


# ===================================================================== 测试
async def test_submit_auto_id_and_enqueued_pos():
    """F043:submit 尾插返回 task_id;enqueued pos 1 起递增(waiting 位置)。

    先暂停队列保证无 pump 弹出(waiting 计数确定),再逐条 submit。
    """
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s), max_queue=32)
    await q.pause("test-hold")
    ids = [await q.submit(f"意图-{i}") for i in range(3)]
    assert ids == ["t-1", "t-2", "t-3"]                 # t-N 单调(auto)
    enq = [e for e in _tasks_events(s, "task.enqueued")]
    assert [(e[2]["task_id"], e[2]["pos"]) for e in enq] == \
        [("t-1", 1), ("t-2", 2), ("t-3", 3)]            # pos 1 起 = waiting 位置
    # 显式 task_id 供 plan/schedule 溯源(原样返回,不占 auto 序号)
    tid = await q.submit("plan 步", task_id="plan:1:0")
    assert tid == "plan:1:0"
    assert "plan:1:0" in [w for w in q.status().waiting]
    # 清理:drain(取消等待任务 + 恢复,泵退场)
    for tid_ in ["t-1", "t-2", "t-3", "plan:1:0"]:
        assert await q.cancel(tid_) is True
    await q.resume("test-hold")
    st = q.status()
    assert st.running is None and st.waiting == [] and st.depth == 0


async def test_submit_empty_intent_rejected():
    """F043 异常表:空/纯空白意图 → EVT-100,不入队。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s))
    for bad in ("", "   ", "\n\t"):
        with pytest.raises(PyHError) as ei:
            await q.submit(bad)
        assert ei.value.code == "EVT-100"
    assert q.status().depth == 0


async def test_queue_full_rejects_que001():
    """F043 边界:waiting ≥ max_queue(32 锁死默认,测试用小值)拒新 QUE-001。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s), max_queue=2)
    await q.pause("test-hold")
    a = await q.submit("A")
    b = await q.submit("B")
    with pytest.raises(PyHError) as ei:
        await q.submit("C")                              # waiting 已 =2 → 拒
    assert ei.value.code == "QUE-001"
    assert "QUE-001" in str(ei.value)
    # 拒新不静默:队列状态未变,等待任务仍在
    assert q.status().waiting == [a, b]
    assert await q.cancel(a) and await q.cancel(b)
    await q.resume("test-hold")                          # 泵唤醒后队空退场


async def test_fifo_order_serial_single_running():
    """F043 核心:FIFO 顺序 + 同一时刻仅一个 running。

    paused 下入队保证 enqueued 全序;resume 后逐任务 drain;断言事件流严格串行
    (任务 N 的终态先于任务 N+1 的 started)且执行器并发观测 max_active == 1。
    """
    s = await _new_session()
    runner = FakeRunner(s, sleep=0.02)                   # 让出时间片暴露并发
    q = TaskQueue(s, runner=runner)
    await q.pause("test-hold")
    ids = [await q.submit(f"任务-{i}") for i in range(3)]
    await q.resume("test-hold")
    await _drain_ok(q, ids)                              # FIFO drain:按提交序等待

    assert runner.runs == [(ids[0], "任务-0"), (ids[1], "任务-1"), (ids[2], "任务-2")]
    assert runner.max_active == 1                        # 同一时刻仅一个 running
    st = q.status()
    assert st.running is None and st.waiting == []       # 全 drain,泵已退场

    # 事件流全序:enq×3 → (started, seg.start, completed, seg.end)×N 逐任务
    seq = [(e.type, e.payload["task_id"])
           for e in _events(s)
           if e.type in ("task.enqueued", "task.started", "task.completed",
                         "task.failed", "segment.start", "segment.end")]
    assert seq == [
        ("task.enqueued", ids[0]), ("task.enqueued", ids[1]), ("task.enqueued", ids[2]),
        ("task.started", ids[0]), ("segment.start", ids[0]),
        ("task.completed", ids[0]), ("segment.end", ids[0]),
        ("task.started", ids[1]), ("segment.start", ids[1]),
        ("task.completed", ids[1]), ("segment.end", ids[1]),
        ("task.started", ids[2]), ("segment.start", ids[2]),
        ("task.completed", ids[2]), ("segment.end", ids[2]),
    ]
    # 串行性硬断言:任务 N+1 的 started 必晚于任务 N 的终态(GWT-F043-01)
    by_task: dict = {}
    for e in _events(s):
        if e.type in ("task.started", "task.completed", "task.failed"):
            by_task.setdefault(e.payload["task_id"], []).append(e.seq)
    for prev, nxt in zip(ids, ids[1:]):
        assert by_task[prev][-1] < by_task[nxt][0]       # 终态 seq < 下个 started seq


async def test_segment_fence_pairing_and_task_id_bound():
    """F044:段围栏——segment.start/end 配对(start_seq 回指);段内事件带 task_id;
    闲聊(无 task_id,先于任务)不入任何段;段闭区间互不重叠。"""
    s = await _new_session()
    chat = await s.append("user.message", {"content": "闲聊,无任务上下文"},
                          actor="user", sync=True)       # GWT-F044-01:不入段
    assert chat.task_id is None
    runner = FakeRunner(s)                               # 段内写 agent.message(task_id)
    q = TaskQueue(s, runner=runner)
    a = await q.submit("任务A")
    b = await q.submit("任务B")
    await _drain_ok(q, [a, b])

    starts = {e.payload["task_id"]: e.seq
              for e in _events(s) if e.type == "segment.start"}
    seg_ranges = {}
    for e in _events(s):
        if e.type == "segment.end":
            lo = e.payload["start_seq"]                  # = 对应 segment.start 的 seq
            assert lo == starts[e.payload["task_id"]]
            assert lo <= e.seq                           # start_seq 在 end 之前
            seg_ranges[e.payload["task_id"]] = (lo, e.seq)
    assert set(seg_ranges) == {a, b}                     # 每任务一段,恰一对
    # 段闭区间互不重叠(F044:段不嵌套)
    rngs = sorted(seg_ranges.values())
    assert rngs[0][1] < rngs[1][0]
    # 段内全部事件带 task_id(信封层 agent.message / payload 层终态事件)
    lo, hi = seg_ranges[a]
    inside = list(s.events_between(lo, hi))
    assert inside[0].type == "segment.start" and inside[-1].type == "segment.end"
    for env in inside:
        assert env.task_id == a or env.payload.get("task_id") == a
    # 闲聊 seq 不在任何段闭区间内(先于全部任务事件)
    assert chat.seq < lo and chat.seq < rngs[0][0]
    # 终态事件每任务至多一个;started 前必有 enqueued
    for tid in (a, b):
        finals = [e.type for e in _events(s)
                  if e.type in ("task.completed", "task.failed")
                  and e.payload["task_id"] == tid]
        assert finals == ["task.completed"]


async def test_segment_start_strong_sync_flushed():
    """F044/F043:segment.start 强同步(sync=True)——flush 必含段锚 seq;
    task.enqueued/started/completed、segment.end 普通不触发 flush。"""
    store = Store()
    s = await _new_session(persistence=store)
    q = TaskQueue(s, runner=FakeRunner(s))
    tid = await q.submit("任务")
    res = await asyncio.wait_for(q.wait_for(tid), timeout=3.0)
    assert res.ok
    events = {e.type: e for e in _events(s)}
    # 段锚先落盘:flush 记录了 segment.start 的 seq
    assert events["segment.start"].seq in store.flushed
    for t in ("task.enqueued", "task.started", "task.completed", "segment.end"):
        assert events[t].seq not in store.flushed        # 普通落盘(攒批)


async def test_open_segment_nesting_and_unpaired_close_rejected():
    """F044 异常表:同 task_id 嵌套开段 → EVT-100;不配对关段(未开/重复关)→ EVT-100。"""
    s = await _new_session()
    q = TaskQueue(s, runner=None)                        # 纯段 API 测试,无需执行器
    start_seq = await q.open_segment("job:1")
    assert isinstance(start_seq, int) and start_seq >= 1
    with pytest.raises(PyHError) as ei:
        await q.open_segment("job:1")                    # 嵌套开段(未关)拒
    assert ei.value.code == "EVT-100"
    await q.close_segment("job:1", start_seq)            # 配对关段 OK
    with pytest.raises(PyHError) as ei:
        await q.close_segment("job:1", start_seq)        # 重复关段 → 不配对
    assert ei.value.code == "EVT-100"
    with pytest.raises(PyHError) as ei:
        await q.close_segment("job:ghost", 3)            # 从未开段即关
    assert ei.value.code == "EVT-100"


async def test_pause_stops_consumption_resume_continues():
    """F015/GWT-F043-02:暂停只停消费不停运行——running 任务不受影响,新任务不弹;
    裁决返回(resume)后自动接续,无超时饿死。"""
    s = await _new_session()
    gate = asyncio.Event()
    q = TaskQueue(s, runner=FakeRunner(s, gate=gate))
    a = await q.submit("A")                              # 直接开跑并阻塞在 gate
    # 等 A 进入执行(轮询 status,gate 阻塞期 running 恒为 a)
    for _ in range(300):
        if q.status().running == a:
            break
        await asyncio.sleep(0.01)
    assert q.status().running == a
    b = await q.submit("B")
    await q.pause("approval-pending")                    # 审批等待:暂停消费
    # 暂停期:不弹 B(即使 pump 每轮循环都不放行),A 运行不受影响
    for _ in range(50):
        await asyncio.sleep(0.01)
    st = q.status()
    assert st.paused and st.pause_reasons == ["approval-pending"]
    assert st.running == a and st.waiting == [b]         # B 仍在 waiting
    started_b = [e for e in _events(s)
                 if e.type == "task.started" and e.payload["task_id"] == b]
    assert started_b == []                               # 暂停期零放行
    gate.set()                                           # A 完成
    res_a = await asyncio.wait_for(q.wait_for(a), timeout=3.0)
    assert res_a.ok
    # A 终态后 B 仍因暂停不弹(泵停在 suspended 分支)
    for _ in range(50):
        await asyncio.sleep(0.01)
    assert q.status().running is None and q.status().waiting == [b]
    await q.resume("approval-pending")                   # 裁决返回:自动接续
    res_b = await asyncio.wait_for(q.wait_for(b), timeout=3.0)
    assert res_b.ok
    assert q.status().depth == 0


async def test_repeat_pause_merge_and_multi_reason():
    """EVENT-SCHEMA §3.5.3:重复挂起合并(先 suspended 后 resumed,事件各一次);
    多原因计数;全部解除才写 queue.resumed;非法 resume 幂等无事件。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s))
    await q.pause("approval-pending")
    await q.pause("approval-pending")                    # 同原因重复挂起:仅计数
    await q.pause("budget")                              # 异原因:仍不重复发事件
    sus = [e for e in _events(s) if e.type == "queue.suspended"]
    assert len(sus) == 1                                 # 合并:事件只发一次
    assert sus[0].payload["reason"] == "approval-pending"
    st = q.status()
    assert st.paused and st.pause_reasons == ["approval-pending", "budget"]
    await q.resume("no-such-reason")                     # 未挂起原因:幂等无事件
    await q.resume("approval-pending")                   # 该原因计数 2→1:仍挂起
    assert q.status().paused
    assert [e for e in _events(s) if e.type == "queue.resumed"] == []
    await q.resume("approval-pending")                   # 2→移除:budget 仍在
    assert q.status().paused and q.status().pause_reasons == ["budget"]
    assert [e for e in _events(s) if e.type == "queue.resumed"] == []
    await q.resume("budget")                             # 全解除:resumed 一次 + 序在 suspended 后
    assert not q.status().paused
    resumed = [e for e in _events(s) if e.type == "queue.resumed"]
    assert len(resumed) == 1
    assert resumed[0].payload["reason"] == "budget"
    assert sus[0].seq < resumed[0].seq                   # 先 suspended 后 resumed


async def test_pause_empty_reason_rejected():
    """pause 空原因 → EVT-100,且不污染挂起计数(状态一致性)。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s))
    with pytest.raises(PyHError) as ei:
        await q.pause("")
    assert ei.value.code == "EVT-100"
    assert q.status().paused is False and q.status().pause_reasons == []


async def test_cancel_waiting_removes_and_events():
    """F025:waiting 任务取消 → 摘除 + task.failed(cancelled),不产生 started/段事件。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s))
    await q.pause("test-hold")
    a = await q.submit("A")
    b = await q.submit("B")
    assert await q.cancel(b) is True                     # waiting 摘除
    assert q.status().waiting == [a]
    failed_b = [e for e in _events(s)
                if e.type == "task.failed" and e.payload["task_id"] == b]
    assert len(failed_b) == 1
    assert failed_b[0].payload["reason"] == "cancelled"
    assert [e for e in _events(s)
            if e.type in ("task.started", "segment.start")
            and e.payload["task_id"] == b] == []         # 从未开跑:无段
    assert await q.cancel(b) is False                    # 已摘除:幂等 False(防重放)
    await q.resume("test-hold")                          # 泵唤醒:A 执行完退场
    res_a = await asyncio.wait_for(q.wait_for(a), timeout=3.0)
    assert res_a.ok


async def test_cancel_running_propagates_and_queue_continues():
    """GWT-F025-01/GWT-F043-02:running 取消 → system.cancelled 审计 + 归一化
    failed(cancelled)(cancel_current 钩子被调)+ 队首自动接续;wait_for 收 cancelled。"""
    s = await _new_session()
    gate = asyncio.Event()
    runner = FakeRunner(s, gate=gate)
    q = TaskQueue(s, runner=runner)
    a = await q.submit("A")
    for _ in range(300):                                 # 等 A 进入执行(gate 阻塞)
        if runner.entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert runner.entered.is_set()
    b = await q.submit("B")
    assert q.status().running == a and q.status().waiting == [b]
    assert await q.cancel(a, by="user") is True          # running 取消
    assert runner.cancel_calls == [a]                    # runner 协作钩子被调(F025)
    cc = [e for e in _events(s) if e.type == "system.cancelled"]
    assert len(cc) == 1 and cc[0].payload["what"] == f"task:{a}"   # 取消审计留痕
    assert cc[0].payload["reason"] == "user"
    res_a = await asyncio.wait_for(q.wait_for(a), timeout=3.0)
    assert res_a.ok is False and res_a.code == "cancelled"         # 归一化终态
    assert res_a.duration_ms >= 0
    # 队首自动接:B 已 started,且晚于 A 的 failed 终态
    for _ in range(300):
        if any(e.type == "task.started" and e.payload["task_id"] == b
               for e in _events(s)):
            break
        await asyncio.sleep(0.01)
    evs = _events(s)
    a_failed = next(e.seq for e in evs
                    if e.type == "task.failed" and e.payload["task_id"] == a)
    b_started = next(e.seq for e in evs
                     if e.type == "task.started" and e.payload["task_id"] == b)
    assert a_failed < b_started                          # 接队首自动执行
    assert q.status().running == b
    gate.set()                                           # 放行 B 完成
    res_b = await asyncio.wait_for(q.wait_for(b), timeout=3.0)
    assert res_b.ok
    assert q.status().running is None and q.status().waiting == []


async def test_cancel_running_fallback_without_hook():
    """F025 兜底:runner 无 cancel_current 钩子时,队列取消执行子任务传播取消。"""
    s = await _new_session()
    gate = asyncio.Event()
    runner = FakeRunner(s, gate=gate)
    runner.cancel_current = None                         # 显式移除协作钩子
    q = TaskQueue(s, runner=runner)
    a = await q.submit("A")
    for _ in range(300):
        if runner.entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert await q.cancel(a) is True
    res = await asyncio.wait_for(q.wait_for(a), timeout=3.0)
    assert res.ok is False and res.code == "cancelled"
    assert q.status().running is None                    # 泵存活且已清位
    gate.set()


async def test_cancel_unknown_or_terminal_idempotent():
    """F025:取消不存在/已终态任务 → False,零事件(防重放)。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s))
    assert await q.cancel("t-ghost") is False
    tid = await q.submit("任务")
    await asyncio.wait_for(q.wait_for(tid), timeout=3.0)  # 已终态
    count = len(_events(s))
    assert await q.cancel(tid) is False
    assert len(_events(s)) == count                      # 终态后取消零事件


async def test_wait_for_result_and_timeout_busy():
    """GWT-F043-04/wait_for 异常表:成功返 TaskResult;超时 BUSY 显式拒且
    shield 语义——任务不受牵连,超时后仍可再等并成功。"""
    s = await _new_session()
    gate = asyncio.Event()
    q = TaskQueue(s, runner=FakeRunner(s, gate=gate))
    a = await q.submit("慢任务")
    for _ in range(300):                                 # 等任务进入执行(阻塞中)
        if q.status().running == a:
            break
        await asyncio.sleep(0.01)
    with pytest.raises(PyHError) as ei:
        await q.wait_for(a, timeout=0.05)                # 执行未结束 → 超时
    assert ei.value.code == "BUSY"
    gate.set()                                           # 放行:任务本身未被取消
    res = await asyncio.wait_for(q.wait_for(a), timeout=3.0)
    assert res.ok is True and res.code is None
    assert res.duration_ms >= 0
    assert isinstance(res.summary, type(None))


async def test_wait_for_unknown_task_evt100():
    """wait_for 未知 task_id → EVT-100(防永久悬挂,偏离 6)。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s))
    with pytest.raises(PyHError) as ei:
        await q.wait_for("ghost-task", timeout=0.05)
    assert ei.value.code == "EVT-100"


async def test_wait_for_paused_queue_no_starvation():
    """GWT-F043-04:审批挂起期 wait_for(timeout=None) 不超时饿死——等待者挂起,
    任务不丢不超时;恢复后照常完成。"""
    s = await _new_session()
    gate = asyncio.Event()
    q = TaskQueue(s, runner=FakeRunner(s, gate=gate))
    a = await q.submit("A")
    for _ in range(300):
        if q.status().running == a:
            break
        await asyncio.sleep(0.01)
    b = await q.submit("B")
    await q.pause("approval-pending")                    # 审批等待:B 暂停放行
    waiter = asyncio.create_task(q.wait_for(b))          # timeout=None 永久等
    for _ in range(50):
        await asyncio.sleep(0.01)
    assert not waiter.done()                             # 挂起期不超时不饿死
    gate.set()                                           # A 完成;B 仍被暂停拦着
    for _ in range(50):
        await asyncio.sleep(0.01)
    assert not waiter.done()                             # 暂停优先:仍不弹 B
    await q.resume("approval-pending")                   # 裁决返回 → B 执行
    res = await asyncio.wait_for(waiter, timeout=3.0)
    assert res.ok is True


async def test_unassembled_runner_fails_fast_cyc999():
    """未装配执行器:任务按 CYC-999 快速失败(防 S-1 伪造已执行),队列不假成功。"""
    s = await _new_session()
    q = TaskQueue(s, runner=None)                        # 编排地基未接线
    a = await q.submit("任务A")
    b = await q.submit("任务B")
    ra = await asyncio.wait_for(q.wait_for(a), timeout=3.0)
    rb = await asyncio.wait_for(q.wait_for(b), timeout=3.0)
    assert ra.ok is False and ra.code == "CYC-999"
    assert rb.ok is False and rb.code == "CYC-999"
    failed = [e for e in _events(s) if e.type == "task.failed"]
    assert len(failed) == 2
    assert all(e.payload["error"] == "CYC-999" for e in failed)


async def test_runner_failure_classification():
    """_run_task 失败分级:PyHError → failed(error=原码);裸异常 → CYC-999。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(
        s, exc=PyHError("LLM-310", ctx={"hint": "mock"}), exc_once=True))
    a = await q.submit("业务失败")
    ra = await asyncio.wait_for(q.wait_for(a), timeout=3.0)
    assert ra.ok is False and ra.code == "LLM-310"
    ev = [e for e in _events(s)
          if e.type == "task.failed" and e.payload["task_id"] == a][0]
    assert ev.payload["reason"] == "error" and ev.payload["error"] == "LLM-310"
    assert ev.payload["task_id"] == a
    # 队列继续:下一任务照常跑(失败任务已事件化,不中断队列)
    b = await q.submit("下一个")
    rb = await asyncio.wait_for(q.wait_for(b), timeout=3.0)
    assert rb.ok is True
    # 裸异常 → CYC-999
    s2 = await _new_session()
    q2 = TaskQueue(s2, runner=FakeRunner(s2, exc=RuntimeError("boom")))
    c = await q2.submit("未预期")
    rc = await asyncio.wait_for(q2.wait_for(c), timeout=3.0)
    assert rc.ok is False and rc.code == "CYC-999"


async def test_status_consistent_with_events():
    """GWT-F043-03:status.running/waiting/paused/depth 与事件流一致(派生口径)。"""
    s = await _new_session()
    gate = asyncio.Event()
    q = TaskQueue(s, runner=FakeRunner(s, gate=gate))
    a = await q.submit("A")
    for _ in range(300):
        if q.status().running == a:
            break
        await asyncio.sleep(0.01)
    b = await q.submit("B")
    c = await q.submit("C")
    st = q.status()
    assert isinstance(st, QueueStatus)
    assert st.running == a and st.waiting == [b, c]      # FIFO 等待序
    assert st.depth == 2                                 # 队深 = waiting 数
    assert st.paused is False and st.pause_reasons == []
    # 事件流一致:enqueued pos = 该任务在 waiting 中的位置;running = 已 started 未终态
    enq_b = [e for e in _events(s)
             if e.type == "task.enqueued" and e.payload["task_id"] == b][0]
    assert enq_b.payload["pos"] == st.waiting.index(b) + 1
    gate.set()
    await _drain_ok(q, [a, b, c])
    st = q.status()
    assert st.running is None and st.waiting == [] and st.depth == 0


async def test_waiting_cancel_wakes_waiter():
    """waiting 取消:等在该任务上的 wait_for 立即收到 failed(cancelled)(settle 唤醒)。"""
    s = await _new_session()
    q = TaskQueue(s, runner=FakeRunner(s))
    await q.pause("test-hold")
    tid = await q.submit("将被取消")
    waiter = asyncio.create_task(q.wait_for(tid))
    for _ in range(50):
        if not waiter.done():
            await asyncio.sleep(0.01)
        else:
            break
    assert await q.cancel(tid) is True                   # waiting 摘除 + settle
    res = await asyncio.wait_for(waiter, timeout=3.0)
    assert res.ok is False and res.code == "cancelled"
    await q.resume("test-hold")                          # 泵唤醒退场
    assert q.status().waiting == []
