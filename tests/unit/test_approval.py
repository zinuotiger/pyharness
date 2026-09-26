"""approval 模块单测 — 契约:specs/approval.py.md(权威)+ EVENT-SCHEMA §3.4 + DIS-SEAM §6.2

覆盖面(任务要求全项 + 规格模块级测试):
    grant/deny/timeout 三结果:approve/deny/真实 TTL 超时各返回对应字面量,
        事件序 approval.requested→(queue.suspended)→结果事件→(queue.resumed)
    TTL 过期:ttl_ms 覆盖 → 返回 timeout + approval.timeout(by=system);过期后
        迟到 granted → APR-503 忽略(不重复执行)
    headless 拒:无通道 → APR-501 直接拒,零 approval.requested、零等待
    防重放:同一 approval_id 二次裁决 → APR-503(system.error/抛错),waiter 单次解决
    强同步事件:approval.requested/granted/denied/timeout 均 sync 落盘(flush 留痕)
    请求去重(60s 合并):同参并发挂批仅一条 approval.requested,裁决级联全体;
        同参终态后新请求开新批;异参各自成批独立裁决
    取消/APR-502:cancel_all/detach 未决全置 denied,无悬挂 Future
    信任名单:默认关(逐次询问);enable 后同参命中 granted 不再询问;clear_trust 计数;
        headless enable → APR-501
    身份防线:llm:/tool:/plugin: 前缀裁决 → APR-503;canonical_json 归一

测试装配与 test_agent 同风格:真实 EventBus + SessionLog(FakeStore 强同步 flush +
总线订阅落盘),审批请求/裁决全链路经总线分发;asyncio_mode=auto。
"""
import asyncio
import types

import pytest

from pyharness.bus import EventBus
from pyharness.core.approval import ApprovalProvider, canonical_json
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError
from pyharness.events import EVENT_TYPES, Envelope

SID = "s-approval-1"          # 会话 id(Envelope min_length=8)
MODEL = "deepseek-chat"
SUMMARY = "fs.write_file: 写入 a.txt(测试摘要,policy_ref=demo)"


# ===================================================================== 替身
class FakeStore:
    """SessionStore 内存替身(总线订阅者负责 record;flush 记录强同步 seq)。

    与 test_agent.FakeStore 相同,附加 sid 过滤(共享总线多会话时只收本会话)。
    """

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self._rows: list[Envelope] = []
        self.flushed: list[int] = []

    def record(self, type_, payload) -> None:
        if isinstance(payload, Envelope) and payload.session_id == self.sid:
            self._rows.append(payload)

    def replay(self):
        return list(self._rows)

    async def flush(self, seq: int) -> None:
        self.flushed.append(seq)


def _make_stack(sid: str = SID, *, channel: str = "cli",
                headless: bool = False) -> tuple:
    """独立装配栈:真实 EventBus + SessionLog(总线落盘订阅)+ ApprovalProvider。

    provider 构造即订阅 approval.granted/denied/timeout → on_verdict(owner=approval);
    store.record 先注册,保证事件行先于裁决落库。
    """
    bus = EventBus()
    store = FakeStore(sid)
    for t in EVENT_TYPES:
        bus.subscribe(t, store.record, owner="persistence")
    log = SessionLog(sid=sid, persistence=store, bus=bus)
    prov = ApprovalProvider(session=log, bus=bus, channel=channel,
                            headless=headless)
    return prov, log, store, bus


async def _boot(log: SessionLog) -> None:
    """会话引导:session.created(seq=1;此后事件校验链 EVT-106 开闸)。"""
    await log.append("session.created", {"title": "", "model": MODEL},
                     actor="system")


def _ctx(log: SessionLog, *, channel: str = "cli", headless: bool = False,
         sid: str = SID) -> types.SimpleNamespace:
    """request 的 ctx 替身:装配注入面 = session + channel + headless(外壳装配契约)。"""
    return types.SimpleNamespace(session=log, channel=channel, headless=headless,
                                 session_id=sid)


def _call(name: str = "fs.write_file", args: dict = None, *,
          call_id: str = "call_1", danger: str = "high") -> types.SimpleNamespace:
    """ToolCall 鸭子替身(与 tools_guard.ToolCall 同构:name/args/raw_args/call_id/defn)。"""
    a = dict(args or {"path": "a.txt", "mode": "w"})
    return types.SimpleNamespace(name=name, raw_args=dict(a), args=dict(a),
                                 call_id=call_id, parent_seq=3,
                                 defn=types.SimpleNamespace(danger=danger))


async def _wait_pending(prov: ApprovalProvider, n: int = 1,
                        timeout: float = 3.0) -> None:
    """轮询等 request 进入等待(lead 登记 _pending 后 approve 才有对象)。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while prov.pending_count() < n:
        if loop.time() > deadline:
            pytest.fail(f"审批未在 {timeout}s 内进入等待(pending={prov.pending_count()})")
        await asyncio.sleep(0)


def _by_type(store: FakeStore, type_: str) -> list[Envelope]:
    return [e for e in store.replay() if e.type == type_]


async def _wait_events(store: FakeStore, type_: str, n: int = 1,
                       timeout: float = 3.0) -> list[Envelope]:
    """轮询等事件到达(结果事件经 approve 后台任务落盘+总线分发,异步到达)。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while len(_by_type(store, type_)) < n:
        if loop.time() > deadline:
            pytest.fail(f"{type_} 未在 {timeout}s 内到达 {n} 条"
                        f"(现 {len(_by_type(store, type_))} 条)")
        await asyncio.sleep(0)
    return _by_type(store, type_)


async def _wait_flushed(store: FakeStore, seq: int, timeout: float = 3.0) -> None:
    """轮询等强同步 flush 留痕(会话层分发先于 flush:结果事件 flush 在后台任务收尾)。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while seq not in store.flushed:
        if loop.time() > deadline:
            pytest.fail(f"seq={seq} 未在 {timeout}s 内强同步 flush"
                        f"(现 flushed={sorted(store.flushed)})")
        await asyncio.sleep(0)


async def _settle_tasks(timeout: float = 0.5) -> None:
    """让 fire-and-forget 任务(queue.resumed 等软落盘)跑完,防跨测试悬留。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)


# ==================================================================== 三结果
async def test_grant_flow_events_and_strong_sync():
    """granted:approve(alice) → request 返回 granted;事件序与强同步 flush 全断言。

    T-SEC-10 同款事件序:approval.requested → queue.suspended → approval.granted
    → queue.resumed;by=alice 由框架打(actor=user);approval 事件全部强同步落盘。
    """
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    req_evs = _by_type(store, "approval.requested")
    assert len(req_evs) == 1
    aid = req_evs[0].seq                                  # approval_id=请求事件 seq
    assert prov.pending_count() == 1
    assert req_evs[0].payload["tool"] == "fs.write_file"
    assert req_evs[0].payload["args_summary"] == SUMMARY
    assert req_evs[0].payload["ttl_ms"] == 120_000        # TTL 默认 120s(参数锚定)
    assert req_evs[0].payload["risk"] == "high"
    assert req_evs[0].actor == "tool"
    assert req_evs[0].trace["channel"] == "cli"           # channel 经 trace 携带(偏离 1)
    assert req_evs[0].trace["call_id"] == "call_1"

    prov.approve(aid, by="cli:alice")
    assert await asyncio.wait_for(task, 5) == "granted"
    assert prov.pending_count() == 0

    granted = await _wait_events(store, "approval.granted")
    assert len(granted) == 1
    assert granted[0].payload["approval_id"] == aid
    assert granted[0].payload["by"] == "cli:alice"
    assert granted[0].actor == "user"
    # 事件序(按 seq 单调):requested → suspended → granted → resumed
    await _wait_events(store, "queue.resumed")
    seq_order = [(e.seq, e.type) for e in store.replay()
                 if e.type.startswith(("approval.", "queue."))]
    types_ = [t for _, t in seq_order]
    assert types_ == ["approval.requested", "queue.suspended",
                      "approval.granted", "queue.resumed"]
    assert seq_order == sorted(seq_order)                 # seq 单调连续
    # 强同步:approval.* 结果事件 seq 必须出现在 flush 留痕(成功才返回)
    await _wait_flushed(store, granted[0].seq)
    flushed = set(store.flushed)
    for e in store.replay():
        if e.type.startswith("approval."):
            assert e.seq in flushed, f"{e.type} seq={e.seq} 未强同步 flush"
    assert 1 not in flushed                                # session.created 普通落盘


async def test_deny_flow():
    """denied:deny(bob) → request 返回 denied;approval.denied 事件 by=bob。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    prov.deny(aid, by="web:bob")
    assert await asyncio.wait_for(task, 5) == "denied"
    denied = await _wait_events(store, "approval.denied")
    assert len(denied) == 1
    assert denied[0].payload["approval_id"] == aid
    assert denied[0].payload["by"] == "web:bob"
    assert denied[0].actor == "user"
    assert prov.pending_count() == 0
    assert _by_type(store, "approval.granted") == []       # 只落一个结果
    await _wait_flushed(store, denied[0].seq)              # 强同步


async def test_timeout_ttl_expiry():
    """TTL 过期:ttl_ms 覆盖(40ms)→ 返回 timeout;approval.timeout(by=system)。

    T-SEC-03 同款:approval.requested → approval.timeout,超时=denied 安全默认,
    状态为 timeout;过期后迟到 granted → APR-503 忽略(不重复执行)。
    """
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(
        prov.request(_call(), SUMMARY, _ctx(log), ttl_ms=40))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    assert await asyncio.wait_for(task, 5) == "timeout"
    to = await _wait_events(store, "approval.timeout")
    assert len(to) == 1
    assert to[0].payload["approval_id"] == aid
    assert to[0].payload["by"] == "system"                 # timeout by 恒 system
    assert to[0].actor == "system"
    assert to[0].payload["ttl_ms"] == 40                   # 透传请求 TTL
    await _wait_flushed(store, to[0].seq)                  # 强同步
    assert prov.pending_count() == 0
    # 迟到裁决 = 重放:approve 直接 APR-503(已消费);on_verdict 再投 → system.error
    with pytest.raises(PyHError) as ei:
        prov.approve(aid, by="cli:alice")
    assert ei.value.code == "APR-503"
    await prov.on_verdict("approval.granted",
                          {"approval_id": aid, "by": "alice"})
    errs = _by_type(store, "system.error")
    assert errs and errs[-1].payload["code"] == "APR-503"
    assert _by_type(store, "approval.granted") == []       # 无第二次执行/结果


# ================================================================== headless
async def test_headless_aprid_501_direct_deny():
    """headless 拒:R8/异常表 — 无通道 → APR-501 直接拒;零 approval.requested 零等待。

    DIS-SEAM §6.2 G2 同款:不发 approval.requested;request 立即抛错(pending=0,
    无悬挂);queue.suspended 也不落。
    """
    prov, log, store, _ = _make_stack(channel=None, headless=True)
    await _boot(log)
    ctx = _ctx(log, channel=None, headless=True)
    with pytest.raises(PyHError) as ei:
        await prov.request(_call(), SUMMARY, ctx)
    assert ei.value.code == "APR-501"
    assert prov.pending_count() == 0
    assert _by_type(store, "approval.requested") == []
    assert _by_type(store, "queue.suspended") == []
    # ctx 显式注入优先于构造缺省(外壳装配):交互 ctx 即使 provider headless 也放行
    prov2, log2, store2, _ = _make_stack(channel="cli", headless=False)
    await _boot(log2)
    t = asyncio.create_task(prov2.request(_call(), SUMMARY,
                                          _ctx(log2, channel=None, headless=True)))
    with pytest.raises(PyHError) as ei2:
        await t
    assert ei2.value.code == "APR-501"
    assert _by_type(store2, "approval.requested") == []


# ================================================================== 防重放
async def test_replay_aprid_503_and_unknown():
    """防重放(APR-503):已消费 id 再 approve/deny → 抛 APR-503;未知 id 同样拒。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    prov.approve(aid, by="cli:alice")
    assert await asyncio.wait_for(task, 5) == "granted"
    # 同一 id 二次裁决(approve/deny)→ APR-503
    with pytest.raises(PyHError) as ei:
        prov.approve(aid, by="web:bob")
    assert ei.value.code == "APR-503"


@pytest.mark.asyncio
async def test_concurrent_approve_only_one_outcome():
    """并发批准同一审批只允许一个请求进入落盘,另一个立即 APR-503。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    results = await asyncio.gather(
        prov.approve_async(aid, by="cli:alice"),
        prov.approve_async(aid, by="cli:alice"),
        return_exceptions=True)
    assert await asyncio.wait_for(task, 5) == "granted"
    assert len(_by_type(store, "approval.granted")) == 1
    assert sum(isinstance(r, PyHError) and r.code == "APR-503"
               for r in results) == 1
    with pytest.raises(PyHError) as ei2:
        prov.deny(aid, by="web:bob")
    assert ei2.value.code == "APR-503"
    # 未知 id → APR-503
    with pytest.raises(PyHError) as ei3:
        prov.approve(99999, by="cli:alice")
    assert ei3.value.code == "APR-503"
    # on_verdict 直投重放 → system.error(APR-503),无第二 tool.result/结果事件
    await prov.on_verdict("approval.denied", {"approval_id": aid, "by": "bob"})
    errs = _by_type(store, "system.error")
    assert errs and errs[-1].payload["code"] == "APR-503"
    assert len(_by_type(store, "approval.granted")) == 1   # 结果不被重放改写
    assert len(_by_type(store, "approval.denied")) == 0


async def test_forged_verdict_identity_rejected():
    """假冒审批防线(S-2 白名单收紧):llm:/tool:/plugin:/任意自报身份 or 空 by →
    APR-503,零事件。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    for bad in ("llm:assistant", "tool:shell_exec", "plugin:echo",
                "hacker", "alice", "", "sys:admin"):
        with pytest.raises(PyHError) as ei:
            prov.approve(aid, by=bad)
        assert ei.value.code == "APR-503"
    assert _by_type(store, "approval.granted") == []       # 零事件落盘
    prov.deny(aid, by="cli:claire")                        # 人类通道照常可用
    assert await asyncio.wait_for(task, 5) == "denied"


# ========================================================== 60s 合并防轰炸
async def test_merge_same_fingerprint_single_request():
    """R13 合并:同工具同参(键序无关)并发 → 仅一条 approval.requested;裁决级联。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    call_a = _call(args={"path": "a.txt", "mode": "w"})
    call_b = _call(args={"mode": "w", "path": "a.txt"})    # 键序不同 → 同一指纹
    t1 = asyncio.create_task(prov.request(call_a, SUMMARY, _ctx(log)))
    await _wait_pending(prov, 1)
    t2 = asyncio.create_task(prov.request(call_b, SUMMARY, _ctx(log)))
    t3 = asyncio.create_task(prov.request(call_a, SUMMARY, _ctx(log)))
    await asyncio.sleep(0.05)                              # 让后两者完成挂批
    assert prov.pending_count() == 1                       # 合并等待者不新增 pending
    assert len(_by_type(store, "approval.requested")) == 1  # 不新增请求事件
    aid = _by_type(store, "approval.requested")[0].seq
    prov.approve(aid, by="cli:alice")
    # 每个等待者各自配对同一裁决结果(各自独立重入 guard 链由 executor 负责)
    assert await asyncio.wait_for(t1, 5) == "granted"
    assert await asyncio.wait_for(t2, 5) == "granted"
    assert await asyncio.wait_for(t3, 5) == "granted"
    assert len(_by_type(store, "approval.granted")) == 1   # 批量裁决一次
    assert prov.pending_count() == 0


async def test_merge_window_new_batch_after_terminal_and_diff_args():
    """合并窗口边界:批终态后同参新请求开新批;异参各自成批、独立裁决。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    # ① 批 1:同参两次(合并),deny 终态
    t1 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid1 = _by_type(store, "approval.requested")[0].seq
    t1b = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await asyncio.sleep(0.02)
    assert len(_by_type(store, "approval.requested")) == 1
    prov.deny(aid1, by="web:bob")
    assert await asyncio.wait_for(t1, 5) == "denied"
    assert await asyncio.wait_for(t1b, 5) == "denied"
    # ② 批已终态 → 同参再来 = 开新批(新 approval.requested,不挂旧批)
    t2 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    assert len(_by_type(store, "approval.requested")) == 2
    aid2 = _by_type(store, "approval.requested")[-1].seq
    assert aid2 != aid1
    # ③ 异参在批 2 pending 期 → 独立成批(不同指纹不合并)
    t3 = asyncio.create_task(
        prov.request(_call(args={"path": "other.txt", "mode": "w"}),
                     "写 other.txt", _ctx(log)))
    await _wait_pending(prov, 2)
    assert len(_by_type(store, "approval.requested")) == 3
    # 独立裁决:批 2 deny,批 3 grant
    prov.deny(aid2, by="web:bob")
    assert await asyncio.wait_for(t2, 5) == "denied"
    aid3 = _by_type(store, "approval.requested")[-1].seq
    prov.approve(aid3, by="cli:alice")
    assert await asyncio.wait_for(t3, 5) == "granted"
    assert prov.pending_count() == 0


# ========================================================== 取消与 detach
async def test_cancel_all_aprid_502_no_dangling():
    """cancel_all(APR-502):未决全置 denied,无悬挂 Future;恢复队列。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    t1 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    t2 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await asyncio.sleep(0.02)
    prov.cancel_all(reason="detach")
    assert await asyncio.wait_for(t1, 5) == "denied"
    assert await asyncio.wait_for(t2, 5) == "denied"       # 批内级联,无悬挂
    assert prov.pending_count() == 0
    prov.cancel_all()                                      # 幂等
    assert _by_type(store, "approval.denied") == []        # 取消不留孤儿结果事件
    await _settle_tasks()
    assert _by_type(store, "queue.resumed")                # 恢复队列(F043)


async def test_detach_idempotent():
    """detach:取消全部等待 + 摘订阅 + 清信任;幂等;随 ctx.close() 调用。"""
    prov, log, store, bus = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    prov.detach()
    assert await asyncio.wait_for(task, 5) == "denied"
    assert prov.pending_count() == 0
    prov.detach()                                          # 幂等
    assert _by_type(store, "approval.granted") == []
    assert _by_type(store, "approval.denied") == []        # detach 不留孤儿结果事件


async def test_request_task_cancelled_no_dangling():
    """F025:等待中 request 任务被取消 → 批全置 denied 后按取消协议重抛,无悬挂。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert prov.pending_count() == 0
    assert _by_type(store, "approval.requested")           # 仅 requested 留痕
    assert _by_type(store, "approval.granted") == []
    assert _by_type(store, "approval.timeout") == []       # TTL 已取消,不落孤儿


# ================================================================ 信任名单
async def test_trustlist_disabled_by_default():
    """信任名单默认关:同参二次请求逐次询问(两条 approval.requested)。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    for _ in range(2):
        task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
        await _wait_pending(prov)
        aid = _by_type(store, "approval.requested")[-1].seq
        prov.approve(aid, by="cli:alice")
        assert await asyncio.wait_for(task, 5) == "granted"
    assert len(_by_type(store, "approval.requested")) == 2
    assert prov.trust_count() == 0


async def test_trustlist_hit_skips_reask_and_clear():
    """信任命中(仅交互+开):granted 后同参再次请求 → 直接 granted,不再询问。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    prov.enable_trust(True, by="cli:alice")
    assert prov.trust_count() == 0
    # 首次:正常审批并记住
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    prov.approve(aid, by="cli:alice")
    assert await asyncio.wait_for(task, 5) == "granted"
    assert prov.trust_count() == 1
    # 二次:同指纹命中 → 跳过再次询问(无新事件,无等待,直接 granted)
    assert await prov.request(_call(), SUMMARY, _ctx(log)) == "granted"
    assert len(_by_type(store, "approval.requested")) == 1
    assert prov.pending_count() == 0
    # 异参(指纹不同)仍须重新审批
    task2 = asyncio.create_task(
        prov.request(_call(args={"path": "b.txt", "mode": "w"}), "写 b.txt",
                     _ctx(log)))
    await _wait_pending(prov)
    assert len(_by_type(store, "approval.requested")) == 2
    aid2 = _by_type(store, "approval.requested")[-1].seq
    prov.deny(aid2, by="web:bob")
    assert await asyncio.wait_for(task2, 5) == "denied"
    # clear_trust:清除后同参须重新审批;返回清除条数
    assert prov.clear_trust() == 1
    assert prov.trust_count() == 0
    task3 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    assert len(_by_type(store, "approval.requested")) == 3
    aid3 = _by_type(store, "approval.requested")[-1].seq
    prov.deny(aid3, by="web:bob")
    assert await asyncio.wait_for(task3, 5) == "denied"
    # 关闭开关:重新逐次询问
    prov.enable_trust(True, by="cli:alice")
    task4 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid4 = _by_type(store, "approval.requested")[-1].seq
    prov.approve(aid4, by="cli:alice")
    assert await asyncio.wait_for(task4, 5) == "granted"
    prov.enable_trust(False, by="cli:alice")
    task5 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    assert len(_by_type(store, "approval.requested")) == 5  # 关后重新询问
    aid5 = _by_type(store, "approval.requested")[-1].seq
    prov.deny(aid5, by="web:bob")
    assert await asyncio.wait_for(task5, 5) == "denied"


async def test_trustlist_headless_never_enabled():
    """headless 信任永不生效:headless provider enable_trust(True) → APR-501。"""
    prov, log, _, _ = _make_stack(channel=None, headless=True)
    await _boot(log)
    with pytest.raises(PyHError) as ei:
        prov.enable_trust(True, by="cli:alice")
    assert ei.value.code == "APR-501"
    assert not prov._enabled


# ================================================================ 指纹/杂项
def test_canonical_json_normalization():
    """指纹原料归一:键排序、bool/int/float 归一(1 == 1.0 == True 语义同参)。"""
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert canonical_json({"n": 1}) == canonical_json({"n": 1.0})
    assert canonical_json({"f": True}) == canonical_json({"f": 1})
    assert canonical_json({"x": {"y": [1, 2]}}) == canonical_json({"x": {"y": [1.0, 2]}})
    assert canonical_json({"a": 1}) != canonical_json({"a": 2})


async def test_approval_id_equals_requested_seq_and_request_dedupe_key():
    """approval_id = 请求事件 seq(防重放唯一键);pending_count 反映未决 lead。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    req_ev = _by_type(store, "approval.requested")[0]
    assert next(iter(prov._pending)) == req_ev.seq         # approval_id=seq
    assert prov.pending_count() == 1
    prov.deny(req_ev.seq, by="web:bob")
    assert await asyncio.wait_for(task, 5) == "denied"
    assert prov.pending_count() == 0
    await _settle_tasks()


# ============================= RT-FIX-STAGE1 · F-28(审批条目跨会话归属)
# 缺陷:provider 按 spine **共享**,而 ① `_pending` 以**事件 seq** 为键(子会话 seq
# 自 1 重数 ⇒ 与父会话同键空间,后到者**静默覆盖**先到者);② `_merge` 只按**工具+
# 参数指纹**合并(子会话同指纹请求会**并入父批**,被父的裁决唤醒 ⇒ 跨会话授权污染)。
# `ApprovalRequest` 本就带 `session_id`,信息已在,只是键忽略了它。

async def _two_session_stack():
    """一个 provider 服务两个会话(父/子同构:各自 EventBus + FakeStore + SessionLog)。"""
    prov, logA, storeA, _busA = _make_stack("s-approval-a1")
    await _boot(logA)
    busB = EventBus()
    storeB = FakeStore("s-approval-b1")
    for t in EVENT_TYPES:
        busB.subscribe(t, storeB.record, owner="persistence")
    logB = SessionLog(sid="s-approval-b1", persistence=storeB, bus=busB)
    await _boot(logB)
    # 让 B 的 seq 与 A 错开:两个全新会话的下一次 append 都是 seq=2,F-28 的跨会话
    # seq 碰撞守卫会因此 fail-closed(APR-503)。本用例考的是"指纹批是否跨会话合并",
    # 故先给 B 一条填充事件,使其 approval.requested 落在不同 seq。
    await logB.append("user.message", {"content": "filler"}, actor="user")
    return prov, logA, storeA, logB, storeB


async def test_cross_session_same_fingerprint_not_merged():
    """跨会话同指纹:各自开批,**不得**并入同一批(修复前 B 挂进 A 的批)。"""
    prov, logA, storeA, logB, storeB = await _two_session_stack()
    t1 = asyncio.create_task(
        prov.request(_call(), SUMMARY, _ctx(logA, sid="s-approval-a1")))
    await _wait_pending(prov, 1)

    t2 = asyncio.create_task(
        prov.request(_call(call_id="call_2"), SUMMARY, _ctx(logB, sid="s-approval-b1")))
    try:
        await _wait_pending(prov, 2)
        assert prov.pending_count() == 2, "跨会话同指纹必须各自成批"
        assert len(_by_type(storeB, "approval.requested")) == 1, \
            "子会话应自有请求事件(修复前被并入父批、不新增事件)"
        assert len(_by_type(storeA, "approval.requested")) == 1
        # 批隔离:两条 lead 的 session_id 不同、批次对象也不同
        a_req = _by_type(storeA, "approval.requested")[0].seq
        b_req = _by_type(storeB, "approval.requested")[0].seq
        assert prov._pending[a_req].session_id == "s-approval-a1"
        assert prov._pending[b_req].session_id == "s-approval-b1"
        assert prov._pending[a_req].batch is not prov._pending[b_req].batch
    finally:
        for t in (t1, t2):
            t.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        await _settle_tasks()


async def test_same_session_same_fingerprint_still_merged():
    """回归守卫:同会话同指纹**仍合并**(F-28 的键改动不得破坏 R13 防轰炸语义)。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    t1 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov, 1)
    t2 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await asyncio.sleep(0.05)
    try:
        assert prov.pending_count() == 1, "同会话同指纹应合并为一批"
        assert len(_by_type(store, "approval.requested")) == 1
    finally:
        for t in (t1, t2):
            t.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        await _settle_tasks()


async def test_cross_session_pending_seq_collision_refused():
    """同 seq 已被**另一会话**占用 ⇒ fail-closed 拒绝覆盖(APR-503),原条目不变。"""
    prov, log, _store, _ = _make_stack()
    await _boot(log)                                  # seq=1
    nxt = int(log.stats().get("seq", 0)) + 1          # 下一次 append 将占用的 seq
    foreign = types.SimpleNamespace(session_id="s-other-tenant",
                                    approval_id=nxt, is_terminal=False)
    prov._pending[nxt] = foreign
    try:
        with pytest.raises(PyHError) as ei:
            await prov.request(_call(), SUMMARY, _ctx(log))
        assert ei.value.code == "APR-503", ei.value.code
        assert prov._pending[nxt] is foreign, "原请求条目不得被覆盖"
    finally:
        prov._pending.pop(nxt, None)


async def test_same_session_pending_slot_not_treated_as_collision():
    """守卫只拦**异会话**:同会话占用同槽位仍按原语义放行(不误报碰撞)。"""
    prov, log, _store, _ = _make_stack()
    await _boot(log)
    nxt = int(log.stats().get("seq", 0)) + 1
    same = types.SimpleNamespace(session_id=SID, approval_id=nxt,
                                 is_terminal=False)
    prov._pending[nxt] = same
    t = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    try:
        # 注意:不能用 pending_count 等登记——预置条目已使计数为 1。改以"任务未上抛"
        # 为准:异会话会在此处 APR-503 上抛,同会话则继续 await 裁决。
        await asyncio.sleep(0.05)
        assert not t.done() or t.exception() is None, \
            f"同会话不得被判为碰撞上抛:{t.exception()}"
        assert prov._pending.get(nxt) is not same, "同会话:新请求应正常登记"
    finally:
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        await _settle_tasks()


# ============ R12-3:审批 → 队列联动必须**真挂起**(事件宣称 == 运行时事实)
async def _pending_provider(queue=None):
    """建 (session, queue, provider, 触发未决审批的 task)。"""
    import asyncio as _a
    from types import SimpleNamespace as _SN

    from pyharness.config import load_settings
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core.session import SessionLog
    from pyharness.core.task_queue import TaskQueue, TaskResult

    s = SessionLog(sid="s-r123-000001")
    await s.append("session.created", {"title": "", "model": "m"}, actor="system")
    q = queue if queue is not None else TaskQueue(s, runner=None)
    prov = ApprovalProvider(session=s, bus=None, config=load_settings(),
                            channel="desktop", queue_getter=lambda: q)
    ctx = _SN(channel="desktop", session=s)
    call = _SN(name="fs.write_file", call_id="c1", danger="high", raw_args={})
    task = _a.create_task(prov.request(call, "path=note.txt", ctx))
    for _ in range(300):
        if prov._pending:
            break
        await _a.sleep(0.01)
    assert prov._pending, "前置:应有未决审批"
    return s, q, prov, task


async def test_pending_approval_really_pauses_queue():
    """**修复前失败**:审批只落 `queue.suspended` 事件,**从不驱动队列** ⇒
    `queue.status().paused` 恒 False(事件宣称与运行时事实不一致;
    `delete_session` 的 BUSY 守卫在等审批期间因此失效)。"""
    s, q, prov, task = await _pending_provider()
    try:
        types = [e.type for e in s.events_after(0)]
        assert types.count("queue.suspended") == 1, "挂起事件恰一条"
        assert q.status().paused is True, "队列必须**真挂起**(与事件一致)"

        aid = next(iter(prov._pending))
        prov.approve(aid, by="desktop")
        await asyncio.wait_for(task, timeout=5)
        types = [e.type for e in s.events_after(0)]
        assert types.count("queue.resumed") == 1, "恢复事件恰一条(无双写)"
        assert q.status().paused is False, "末决必须真恢复"
    finally:
        if not task.done():
            task.cancel()


async def test_pending_approval_blocks_next_task_until_settled():
    """**挂起的行为价值**:等审批期间**排队任务不得开跑**;裁决后自动接续。

    修复前:队列未真挂起(仅事件),该保护**只靠"单 running"偶然成立**;本用例把它
    变成可断言事实(修复前会看到 t-1 在审批未决时就执行完)。
    """
    import asyncio as _a

    from pyharness.core.task_queue import TaskQueue, TaskResult

    ran: list[str] = []

    class _Runner:
        async def run_for_task(self, task):
            ran.append(task.id)
            return TaskResult(ok=True)

        async def cancel_current(self, task_id):    # noqa: ARG002
            return None

    async def _build():
        from pyharness.core.session import SessionLog
        s = SessionLog(sid="s-r123-000002")
        await s.append("session.created", {"title": "", "model": "m"},
                       actor="system")
        return s, TaskQueue(s, runner=_Runner())

    s, q = await _build()
    _s, _q, prov, task = await _pending_provider(queue=q)
    try:
        t1 = await q.submit("排队任务")
        waiter = _a.create_task(q.wait_for(t1))
        for _ in range(60):                        # 给泵一个机会(它应因挂起而不弹)
            await _a.sleep(0.01)
        assert ran == [], f"审批未决期间不得开跑排队任务,实际 {ran}"
        assert waiter.done() is False

        aid = next(iter(prov._pending))
        prov.approve(aid, by="desktop")
        await _a.wait_for(task, timeout=5)
        r = await _a.wait_for(waiter, timeout=5)
        assert r.ok and ran == [t1], "裁决后应自动接续执行"
    finally:
        if not task.done():
            task.cancel()


# ============ R12-4:在途等审批 × 取消任务(确定性 gate,无随机 sleep 兜底)
async def test_cancel_inflight_task_with_pending_approval():
    """① 审批→denied(APR-502) ② 队列→resumed ③ 等待者→释放 ④ suspended/resumed 严格配对。

    并核对维度:task_id 归属、approval_id(=请求事件 seq)、终态唯一、事件顺序、持久化。
    """
    import asyncio as _a
    from types import SimpleNamespace as _SN

    from pyharness.config import load_settings
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core.session import SessionLog
    from pyharness.core.task_queue import TaskQueue, TaskResult

    s = SessionLog(sid="s-r124-000001")
    await s.append("session.created", {"title": "", "model": "m"}, actor="system")
    prov = ApprovalProvider(session=s, bus=None, config=load_settings(),
                            channel="desktop")
    entered = _a.Event()
    outcome: dict = {}

    class _Runner:
        """模拟 executor 的审批步:真调 provider.request 后**阻塞在裁决等待**。"""

        async def run_for_task(self, task):
            ctx = _SN(channel="desktop", session=s, approval=prov)
            call = _SN(name="fs.write_file", call_id="c1", danger="high",
                       raw_args={})
            entered.set()                       # gate:已进入审批等待
            outcome["verdict"] = await prov.request(call, "path=note.txt", ctx)
            return "unreached"                  # 取消路径不会到这里

        async def cancel_current(self, task_id):   # noqa: ARG002
            return None

    q = TaskQueue(s, runner=_Runner())
    prov._queue_getter = lambda: q               # 与生产同形的接线
    tid = await q.submit("在途等审批")
    waiter = _a.create_task(q.wait_for(tid))
    await _a.wait_for(entered.wait(), timeout=3)     # gate 同步点(非概率)
    for _ in range(300):
        if prov._pending:
            break
        await _a.sleep(0.01)
    assert prov._pending, "前置:审批未决"
    assert q.status().paused is True, "前置:队列已因审批挂起"
    aid = next(iter(prov._pending))
    req = prov._pending[aid]

    # ---- 取消在途任务
    assert await q.cancel(tid, by="test") is True
    r = await _a.wait_for(waiter, timeout=5)

    # ③ 等待者释放(task 级 cancelled 终态)
    assert r.ok is False and r.code == "cancelled", r
    # ① 审批置 denied(APR-502),无悬挂 Future
    assert req.state == "denied" and req.by == "system", (req.state, req.by)
    assert prov.pending_count() == 0
    # ② 队列恢复(不得卡在挂起态)
    assert q.status().paused is False and q.status().running is None
    # ④ 事件严格配对 + 终态唯一 + 顺序
    types = [e.type for e in s.events_after(0)]
    assert types.count("queue.suspended") == types.count("queue.resumed") == 1, types
    assert types.index("queue.suspended") < types.index("queue.resumed"), types
    assert types.count("task.failed") == 1 and "task.completed" not in types, types
    # approval_id 与请求事件 seq 对齐(identity 来自真实事件,非新造 uuid)
    assert isinstance(aid, int) and aid >= 1
    assert any(e.seq == aid and e.type == "approval.requested"
               for e in s.events_after(0)), "approval_id 应等于 approval.requested 的 seq"
    # 持久化:终态可在日志重放中看到(内存日志即真源投影)
    replay = [e.type for e in s.events_after(0)]
    assert "task.enqueued" in replay and "task.started" in replay


# ============ R12-5:审批 TTL 超时 × 队列恢复(同步恢复路径 + 配对)
async def test_approval_ttl_timeout_resumes_queue_and_releases_waiter():
    """TTL 到点无人裁决 → denied(timeout) + 队列恢复 + 等待者释放 + 事件配对。

    覆盖 R12-3 新增的**同步**恢复路径(`_maybe_resume_soft` → `_spawn(queue.resume)`)。
    """
    import asyncio as _a
    from types import SimpleNamespace as _SN

    from pyharness.config import load_settings
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core.session import SessionLog
    from pyharness.core.task_queue import TaskQueue, TaskResult

    s = SessionLog(sid="s-r125-000001")
    await s.append("session.created", {"title": "", "model": "m"}, actor="system")
    prov = ApprovalProvider(session=s, bus=None, config=load_settings(),
                            channel="desktop")
    entered = _a.Event()

    class _Runner:
        async def run_for_task(self, task):
            ctx = _SN(channel="desktop", session=s, approval=prov)
            call = _SN(name="fs.write_file", call_id="c1", danger="high",
                       raw_args={})
            entered.set()
            await prov.request(call, "path=note.txt", ctx,
                                      ttl_ms=80)     # 极短 TTL:到点自动 timeout
            return TaskResult(ok=True)  # Runner explicitly handles the decision

        async def cancel_current(self, task_id):   # noqa: ARG002
            return None

    q = TaskQueue(s, runner=_Runner())
    prov._queue_getter = lambda: q
    tid = await q.submit("等审批超时")
    waiter = _a.create_task(q.wait_for(tid))
    await _a.wait_for(entered.wait(), timeout=3)
    for _ in range(300):
        if prov._pending:
            break
        await _a.sleep(0.01)
    assert q.status().paused is True, "前置:队列已挂起"

    r = await _a.wait_for(waiter, timeout=6)     # TTL 到点后任务应正常收尾
    assert r.ok is True, r                        # 裁决=timeout → executor 解读为不执行

    assert prov.pending_count() == 0
    assert q.status().paused is False, "TTL 超时必须恢复队列"
    types = [e.type for e in s.events_after(0)]
    assert types.count("queue.suspended") == types.count("queue.resumed") == 1, types
    assert types.count("approval.denied") == 1 or types.count("approval.timeout") == 1, types


# ============ R12-6:审批的队列联动必须**按会话隔离**(共享总线/同进程多会话)
async def test_approval_queue_link_is_per_session():
    """A 的审批只挂起**A 的队列**;B 的队列不受影响(反之亦然)。

    校验 R12-3 新接线的作用域:provider 逐个会话持有 `queue_getter` ⇒ 挂起/恢复
    不得跨会话串场(否则一个会话等审批会把另一个会话的队列也冻住)。
    """
    import asyncio as _a
    from types import SimpleNamespace as _SN

    from pyharness.config import load_settings
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core.session import SessionLog
    from pyharness.core.task_queue import TaskQueue, TaskResult

    cfg = load_settings()
    sA = SessionLog(sid="s-r126-a00001")
    sB = SessionLog(sid="s-r126-b00002")
    for s in (sA, sB):
        await s.append("session.created", {"title": "", "model": "m"},
                       actor="system")
    qA = TaskQueue(sA, runner=None)
    qB = TaskQueue(sB, runner=None)
    provA = ApprovalProvider(session=sA, bus=None, config=cfg,
                             channel="desktop", queue_getter=lambda: qA)
    entered = _a.Event()

    async def _ask(prov, s, tag):
        ctx = _SN(channel="desktop", session=s)
        call = _SN(name="fs.write_file", call_id=f"c-{tag}", danger="high",
                   raw_args={})
        entered.set()
        return await prov.request(call, "path=note.txt", ctx)

    tA = _a.create_task(_ask(provA, sA, "a"))
    await _a.wait_for(entered.wait(), timeout=3)
    for _ in range(300):
        if provA._pending:
            break
        await _a.sleep(0.01)
    assert provA._pending and provA._suspended is True
    # A 挂起; B 必须**不受影响**
    assert qA.status().paused is True, "A 的队列应挂起"
    assert qB.status().paused is False, "B 的队列不得被 A 的审批挂起"
    assert [e.type for e in sB.events_after(0)].count("queue.suspended") == 0, \
        "B 的日志不得出现 queue.suspended(跨会话串场)"

    aid = next(iter(provA._pending))
    provA.approve(aid, by="desktop")
    await _a.wait_for(tA, timeout=5)
    assert qA.status().paused is False and qB.status().paused is False
    assert [e.type for e in sB.events_after(0)].count("queue.resumed") == 0
