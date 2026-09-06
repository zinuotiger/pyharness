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

    prov.approve(aid, by="alice")
    assert await asyncio.wait_for(task, 5) == "granted"
    assert prov.pending_count() == 0

    granted = await _wait_events(store, "approval.granted")
    assert len(granted) == 1
    assert granted[0].payload["approval_id"] == aid
    assert granted[0].payload["by"] == "alice"
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
    prov.deny(aid, by="bob")
    assert await asyncio.wait_for(task, 5) == "denied"
    denied = await _wait_events(store, "approval.denied")
    assert len(denied) == 1
    assert denied[0].payload["approval_id"] == aid
    assert denied[0].payload["by"] == "bob"
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
        prov.approve(aid, by="alice")
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
    prov.approve(aid, by="alice")
    assert await asyncio.wait_for(task, 5) == "granted"
    # 同一 id 二次裁决(approve/deny)→ APR-503
    with pytest.raises(PyHError) as ei:
        prov.approve(aid, by="bob")
    assert ei.value.code == "APR-503"
    with pytest.raises(PyHError) as ei2:
        prov.deny(aid, by="bob")
    assert ei2.value.code == "APR-503"
    # 未知 id → APR-503
    with pytest.raises(PyHError) as ei3:
        prov.approve(99999, by="alice")
    assert ei3.value.code == "APR-503"
    # on_verdict 直投重放 → system.error(APR-503),无第二 tool.result/结果事件
    await prov.on_verdict("approval.denied", {"approval_id": aid, "by": "bob"})
    errs = _by_type(store, "system.error")
    assert errs and errs[-1].payload["code"] == "APR-503"
    assert len(_by_type(store, "approval.granted")) == 1   # 结果不被重放改写
    assert len(_by_type(store, "approval.denied")) == 0


async def test_forged_verdict_identity_rejected():
    """假冒审批防线(S-2):llm:/tool:/plugin: 前缀 or 空 by → APR-503,零事件。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    for bad in ("llm:assistant", "tool:shell_exec", "plugin:echo", ""):
        with pytest.raises(PyHError) as ei:
            prov.approve(aid, by=bad)
        assert ei.value.code == "APR-503"
    assert _by_type(store, "approval.granted") == []       # 零事件落盘
    prov.deny(aid, by="claire")                            # 人类通道照常可用
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
    prov.approve(aid, by="alice")
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
    prov.deny(aid1, by="bob")
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
    prov.deny(aid2, by="bob")
    assert await asyncio.wait_for(t2, 5) == "denied"
    aid3 = _by_type(store, "approval.requested")[-1].seq
    prov.approve(aid3, by="alice")
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
        prov.approve(aid, by="alice")
        assert await asyncio.wait_for(task, 5) == "granted"
    assert len(_by_type(store, "approval.requested")) == 2
    assert prov.trust_count() == 0


async def test_trustlist_hit_skips_reask_and_clear():
    """信任命中(仅交互+开):granted 后同参再次请求 → 直接 granted,不再询问。"""
    prov, log, store, _ = _make_stack()
    await _boot(log)
    prov.enable_trust(True, by="alice")
    assert prov.trust_count() == 0
    # 首次:正常审批并记住
    task = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid = _by_type(store, "approval.requested")[0].seq
    prov.approve(aid, by="alice")
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
    prov.deny(aid2, by="bob")
    assert await asyncio.wait_for(task2, 5) == "denied"
    # clear_trust:清除后同参须重新审批;返回清除条数
    assert prov.clear_trust() == 1
    assert prov.trust_count() == 0
    task3 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    assert len(_by_type(store, "approval.requested")) == 3
    aid3 = _by_type(store, "approval.requested")[-1].seq
    prov.deny(aid3, by="bob")
    assert await asyncio.wait_for(task3, 5) == "denied"
    # 关闭开关:重新逐次询问
    prov.enable_trust(True, by="alice")
    task4 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    aid4 = _by_type(store, "approval.requested")[-1].seq
    prov.approve(aid4, by="alice")
    assert await asyncio.wait_for(task4, 5) == "granted"
    prov.enable_trust(False, by="alice")
    task5 = asyncio.create_task(prov.request(_call(), SUMMARY, _ctx(log)))
    await _wait_pending(prov)
    assert len(_by_type(store, "approval.requested")) == 5  # 关后重新询问
    aid5 = _by_type(store, "approval.requested")[-1].seq
    prov.deny(aid5, by="bob")
    assert await asyncio.wait_for(task5, 5) == "denied"


async def test_trustlist_headless_never_enabled():
    """headless 信任永不生效:headless provider enable_trust(True) → APR-501。"""
    prov, log, _, _ = _make_stack(channel=None, headless=True)
    await _boot(log)
    with pytest.raises(PyHError) as ei:
        prov.enable_trust(True, by="alice")
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
    prov.deny(req_ev.seq, by="bob")
    assert await asyncio.wait_for(task, 5) == "denied"
    assert prov.pending_count() == 0
    await _settle_tasks()
