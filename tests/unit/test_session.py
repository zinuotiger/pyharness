"""session 模块单测 — 契约:specs/session.py.md + EVENT-SCHEMA.md §3/§4 + DIS-CORE §3

覆盖面(任务要求全项):
    INV-01 append-only:方法面无 update/delete/改写;append 只追加;拒写零副作用
    INV-02 历史必由日志派生:derive_messages 唯一历史源,append 即历史变化,
          无第二份内存历史可不同步
    seq 连续无空洞:created 引导 seq=1,逐 +1;events_after 增量与全量一致
    INV-03 rebuild 一致:rebuild_from_log 后与缓存逐事件/派生逐条一致
    derive_messages reducer:user/assistant 映射、工具轮配对(GWT-S3-02)、
          修正覆盖(GWT-S3-03)、compacted 摘要、guard.rejected 不入上下文、
          max_tokens 头部截断
    events_after/events_between/get:增量读、闭区间切片、单条/空洞 None
    open_session:重启恢复、空文件=新会话、终态仍拒写、多会话过滤、空洞告警
    错误码:EVT-100/101/102/104/106 全路径 + 拒写零副作用

存储侧以内存 FakeStore 替身注入(bus 日志订阅者负责 record,replay/flush 即真源
语义);真 persistence 模块(阶段后续)实现后无需改本测试。
"""
import pytest

from pyharness import bus as B
from pyharness.bus import EventBus
from pyharness.core.session import SessionLog, open_session
from pyharness.errors import PyHError
from pyharness.events import Envelope, EVENT_TYPES

SID = "s-abc12345"
SID2 = "s-other99999"


# ===================================================================== 替身
class FakeStore:
    """persistence.SessionStore 内存替身:record(总线订阅者)/replay/flush。"""

    def __init__(self) -> None:
        self._rows: list[Envelope] = []
        self.flushed: list[int] = []

    def record(self, type_, payload) -> None:
        """总线日志订阅者回调(真实现 = JSONL append 句柄)。"""
        if isinstance(payload, Envelope):
            self._rows.append(payload)

    def replay(self):
        """回放:按记录序返回快照(坏行隔离 PERS-201 由真实现负责)。"""
        return list(self._rows)

    async def flush(self, seq: int) -> None:
        self.flushed.append(seq)


def _store_wired(sid: str = SID) -> tuple[SessionLog, FakeStore]:
    """装配:SessionLog + 总线 → 存储订阅者(事件一入内存即入真源队列)。"""
    store = FakeStore()
    bus = EventBus()
    for t in EVENT_TYPES:                      # 词表全量订阅(记录即真源)
        bus.subscribe(t, store.record, owner="persistence")
    log = SessionLog(sid=sid, persistence=store, bus=bus)
    return log, store


async def _bootstrap(log: SessionLog) -> None:
    """会话引导:首事件必须是 session.created(seq=1)。"""
    await log.append("session.created", {"title": "", "model": "deepseek-chat"},
                     actor="system")


# ===================================================================== INV-01
# 方法面钉死:append-only,无任何 update/delete/改写/清空 API(spec:方法面 grep 断言)
_FORBIDDEN_FRAGMENTS = ("update", "delete", "remove", "pop", "clear",
                        "rewrite", "overwrite", "mutate", "edit_log", "truncate_log")


def test_method_surface_append_only_inv01():
    """INV-01 方法面:类方法清单无 update/delete/改写;允许面恰为派生只读+append。"""
    public = {n for n in dir(SessionLog) if not n.startswith("_")}
    assert "append" in public
    allowed = {"append", "derive_messages", "derive_history", "events_after",
               "events_between", "get", "rebuild_from_log", "stats",
               "close_marker", "warn_hole"}   # warn_hole = spec 回放空洞告警(供 F031)
    assert public <= allowed, f"出现未预期公开方法: {public - allowed}"
    for frag in _FORBIDDEN_FRAGMENTS:
        assert not any(frag in n.lower() for n in public), \
            f"发现疑似改写 API: {frag}(INV-01 只追加铁律被破坏)"


def test_append_only_no_second_history_store_inv02():
    """INV-02 结构断言:除日志 _cache 与可弃 history_cache 外无第二份历史存储。"""
    log = SessionLog(sid=SID)                  # 纯内存模式
    data_attrs = {k: v for k, v in vars(log).items()
                  if isinstance(v, (list, dict, set))}
    # 唯一列表态历史:事件缓存 + seq 平行索引 + 声明区间;无独立"消息历史"副本
    assert set(data_attrs) <= {"_cache", "_seqs", "_folded", "_holes_warned"}
    assert "history_cache" in vars(log) and vars(log)["history_cache"] is None
    # 派生结果与日志事件一一联动:无独立可变消息存储
    assert not hasattr(log, "messages") and not hasattr(log, "history")


# ===================================================================== append
async def test_append_seq_monotonic_no_holes():
    """seq 从 1 起单调 +1(created 引导),无空洞;append 返回带 seq 的信封。"""
    log = SessionLog(sid=SID)
    e1 = await log.append("session.created",
                          {"title": "", "model": "deepseek-chat"}, actor="system")
    e2 = await log.append("session.renamed", {"new_title": "测试"},
                          actor="agent")
    e3 = await log.append("user.message", {"content": "你好"}, actor="user")
    assert [e1.seq, e2.seq, e3.seq] == [1, 2, 3]
    assert e1.type == "session.created" and e1.session_id == SID
    assert [ev.seq for ev in log.events_after()] == [1, 2, 3]      # 全量连续
    assert log.stats()["event_count"] == 3 and log.stats()["seq"] == 3


async def test_append_before_created_rejected_evt106():
    """事件先于 session.created → EVT-106;拒写零副作用。"""
    log = SessionLog(sid=SID)
    with pytest.raises(PyHError) as ei:
        await log.append("user.message", {"content": "你好"}, actor="user")
    assert ei.value.code == "EVT-106"
    assert log.stats()["event_count"] == 0, "EVT-106 后不得有任何事件入日志"


async def test_append_unknown_type_rejected_evt102():
    """未知类型(未注册词表)→ EVT-102;日志行数不变。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    with pytest.raises(PyHError) as ei:
        await log.append("ghost.event", {"content": "x"}, actor="system")
    assert ei.value.code == "EVT-102"
    assert log.stats()["event_count"] == 1, "EVT-102 拒写零副作用"


async def test_append_bad_payload_rejected_evt100():
    """payload 非法(空消息/缺字段)→ EVT-100,字段明细随 ctx;日志不变。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    with pytest.raises(PyHError) as ei:
        await log.append("user.message", {"content": ""}, actor="user")
    assert ei.value.code == "EVT-100"
    assert "content" in ei.value.ctx.get("detail", "")
    with pytest.raises(PyHError) as ei:        # 缺必填字段 payload
        await log.append("user.message", {}, actor="user")
    assert ei.value.code == "EVT-100"
    with pytest.raises(PyHError) as ei:        # 多余字段(extra=forbid)
        await log.append("user.message", {"content": "hi", "extra": 1},
                         actor="user")
    assert ei.value.code == "EVT-100"
    assert log.stats()["event_count"] == 1


async def test_append_finished_then_rejected_evt104():
    """finished 终态后任何 append → EVT-104;重复 finished 也被拒。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    e = await log.append("session.finished", {"reason": "idle"}, actor="system")
    assert e.seq == 2 and log.stats()["closed"] is True
    with pytest.raises(PyHError) as ei:
        await log.append("user.message", {"content": "再来"}, actor="user")
    assert ei.value.code == "EVT-104"
    with pytest.raises(PyHError) as ei:
        await log.append("session.finished", {"reason": "idle"}, actor="system")
    assert ei.value.code == "EVT-104"
    assert log.stats()["event_count"] == 2, "终态后拒写零副作用"


async def test_append_transient_rejected_evt100():
    """瞬时事件(llm.chunk)禁 append 入日志 → EVT-100(§1.3 通道纪律)。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    with pytest.raises(PyHError) as ei:
        await log.append("llm.chunk", {"delta": "碎"}, actor="llm")
    assert ei.value.code == "EVT-100"
    assert log.stats()["event_count"] == 1


async def test_append_created_only_once_evt106():
    """session.created 仅一条:二次 created → EVT-106(seq=1 首事件引导)。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    with pytest.raises(PyHError) as ei:
        await log.append("session.created", {"title": "x", "model": "m"},
                         actor="system")
    assert ei.value.code == "EVT-106"
    assert log.stats()["event_count"] == 1


async def test_append_badtarget_edited_feedback_evt100():
    """BadTarget:message_edited 目标必须存在且为 user.message;feedback 目标须为
    agent.message → 均 EVT-100 且日志不变(§3.2.2/§3.2.3)。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    with pytest.raises(PyHError) as ei:        # 目标不存在(seq 2 尚无)
        await log.append("user.message_edited",
                         {"target_seq": 2, "new_content": "改"}, actor="user")
    assert ei.value.code == "EVT-100"
    await log.append("user.message", {"content": "你好"}, actor="user")
    with pytest.raises(PyHError) as ei:        # 目标类型错(user.message ≠ agent.message)
        await log.append("user.feedback", {"target_seq": 2, "kind": "down"},
                         actor="user")
    assert ei.value.code == "EVT-100"
    await log.append("agent.message", {"content": "答"}, actor="agent")
    with pytest.raises(PyHError) as ei:        # edited 指向 agent.message 也拒
        await log.append("user.message_edited",
                         {"target_seq": 3, "new_content": "x"}, actor="user")
    assert ei.value.code == "EVT-100"
    await log.append("user.message_edited", {"target_seq": 2, "new_content": "x"},
                     actor="user")             # 合法修正放行
    assert log.stats()["event_count"] == 4
    assert log.get(4).payload["new_content"] == "x"


async def test_sync_flush_strong_sync_types():
    """强同步:user.message 自动 flush;普通事件不 flush;sync=True 强制 flush。"""
    store = FakeStore()
    log = SessionLog(sid=SID, persistence=store)   # 无总线:内存 + 存储双轨
    await log.append("session.created", {"title": "", "model": "m"},
                     actor="system")               # 普通:不 flush
    await log.append("llm.request", {"model": "m"}, actor="llm")  # 普通
    await log.append("user.message", {"content": "你好"}, actor="user")  # 强同步
    await log.append("llm.usage", {"model": "m", "in_tokens": 1, "out_tokens": 1},
                     actor="llm", sync=True)       # 显式强制
    assert store.flushed == [3, 4]


# ===================================================================== derive
async def _conversation(log: SessionLog) -> list[int]:
    """写入一段代表性会话:引导 + 用户 + 助手 + 工具轮 + guard 拒绝。"""
    await _bootstrap(log)
    seqs = []
    seqs.append((await log.append(
        "user.message", {"content": "帮我整理 D:/work"}, actor="user")).seq)
    seqs.append((await log.append(
        "llm.response", {"model": "deepseek-chat", "finish_reason": "stop",
                         "content": "好的,开始整理"}, actor="llm")).seq)
    seqs.append((await log.append(
        "llm.response", {"model": "deepseek-chat", "finish_reason": "tool_calls",
                         "content": "",
                         "tool_calls": [{"id": "call_1", "type": "function",
                                         "function": {"name": "list_dir",
                                                      "arguments": "{}"}}]},
        actor="llm")).seq)
    seqs.append((await log.append(
        "tool.result", {"name": "list_dir", "call_id": "call_1", "ok": True,
                        "summary": "42 项:3 文件夹,39 文件", "truncated": False},
        actor="tool")).seq)
    seqs.append((await log.append(
        "guard.rejected", {"tool": "shell_exec", "guard_id": "g-danger-cmd",
                           "reason": "rm -rf 高危"}, actor="tool")).seq)
    return seqs


async def test_derive_mapping_user_assistant_tool_pair_gwt_s3_02():
    """reducer:user→user、有 content 的 response→assistant、工具轮重建
    assistant.tool_calls + tool 配对(协议要求:tool 前须有含同 id 的
    assistant,否则端点 400——真链实测);guard.rejected 只留审计不进上下文。"""
    log = SessionLog(sid=SID)
    await _conversation(log)
    msgs = log.derive_messages()
    assert msgs == [
        {"role": "user", "content": "帮我整理 D:/work"},
        {"role": "assistant", "content": "好的,开始整理"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "list_dir", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1",
         "content": "42 项:3 文件夹,39 文件", "name": "list_dir"},
    ], f"GWT-S3-02 配对投影不符: {msgs}"
    # guard.rejected 不在结果;session.created 也不映射
    assert not any("guard" in str(m) for m in msgs)


async def test_derive_edited_overrides_gwt_s3_03():
    """修正覆盖:user.message(2) + message_edited(2) → derive 取新内容,
    日志仍含原文两行(取新版留旧痕,F063)。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    await log.append("user.message", {"content": "把 D:\\杂乱 按主题归类"},
                     actor="user")
    await log.append("user.message_edited",
                     {"target_seq": 2, "new_content": "把 D:\\work 归档"},
                     actor="user")
    msgs = log.derive_messages()
    assert msgs == [{"role": "user", "content": "把 D:\\work 归档"}]
    # 日志原文仍在:两行都在,内容未被改写
    evs = list(log.events_after())
    assert [ev.type for ev in evs] == ["session.created", "user.message",
                                       "user.message_edited"]
    assert evs[1].payload["content"] == "把 D:\\杂乱 按主题归类"
    assert evs[2].payload["new_content"] == "把 D:\\work 归档"
    assert log.stats()["event_count"] == 3


async def test_derive_multiple_edits_last_wins():
    """同一消息多次修正:reducer 取最后一次,原文三次全留痕。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    await log.append("user.message", {"content": "v0"}, actor="user")
    await log.append("user.message_edited", {"target_seq": 2, "new_content": "v1"},
                     actor="user")
    await log.append("user.message_edited", {"target_seq": 2, "new_content": "v2"},
                     actor="user")
    assert log.derive_messages() == [{"role": "user", "content": "v2"}]
    assert log.stats()["event_count"] == 4


async def test_derive_compacted_summary_system_message():
    """context.compacted → 摘要 system 消息(空洞合法化锚点,原文仍在日志)。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    await log.append("user.message", {"content": "你好"}, actor="user")
    await log.append("llm.response", {"model": "m", "finish_reason": "stop",
                                      "content": "回复"}, actor="llm")
    await log.append("context.compacted", {"ranges": [[2, 3]],
                                            "summary": "用户打招呼,已回复"},
                     actor="system")
    msgs = log.derive_messages()
    assert msgs[-1] == {"role": "system",
                        "content": "[已压缩 [[2, 3]]] 用户打招呼,已回复"}
    assert msgs[0]["role"] == "user"          # 折叠前原文仍可派生(压缩但不撒谎)
    ranges = log.stats()["folded_ranges"]
    assert [2, 3] in ranges, "compacted 声明区间应入 _folded 索引"


async def test_derive_truncate_head_max_tokens():
    """max_tokens 超窗:头部整条截断(旧消息先丢),最新一条恒保留;None 不截断。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    await log.append("user.message", {"content": "长" * 3000}, actor="user")
    await log.append("llm.response", {"model": "m", "finish_reason": "stop",
                                      "content": "短回复"}, actor="llm")
    full = log.derive_messages()
    assert len(full) == 2
    small = log.derive_messages(max_tokens=50)
    assert len(small) == 1 and small[0]["role"] == "assistant", \
        "超窗应丢更旧头部、保最新一条"
    mid = log.derive_messages(max_tokens=10_000)
    assert len(mid) == 2, "预算充足不得截断"


async def test_derive_history_alias_same_output():
    """derive_history 为 derive_messages 别名:同函数同输出(§3.5/§4.1)。"""
    log = SessionLog(sid=SID)
    await _conversation(log)
    assert SessionLog.derive_history is SessionLog.derive_messages
    assert log.derive_history() == log.derive_messages()


async def test_derive_only_changes_via_append_inv02():
    """INV-02 行为:历史只随日志事件变化——非映射事件(renamed)不改变派生,
    追加 user.message 才改变;无任何独立可写历史。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    await log.append("user.message", {"content": "你好"}, actor="user")
    before = log.derive_messages()
    await log.append("session.renamed", {"new_title": "随便"}, actor="user")
    assert log.derive_messages() == before     # renamed 不映射 → 派生不变
    await log.append("user.message", {"content": "第二问"}, actor="user")
    after = log.derive_messages()
    assert after != before and len(after) == 2  # 新事件 → 派生尾部新增
    assert after[-1] == {"role": "user", "content": "第二问"}


# ===================================================================== 读视图
async def test_events_after_incremental_gwt_s3_04():
    """增量读:events_after(last) 恰返回新事件;与全量切片一致(INV-03 配套)。"""
    log = SessionLog(sid=SID)
    await _conversation(log)
    all_evs = list(log.events_after())
    for k in range(len(all_evs)):
        tail = list(log.events_after(all_evs[k].seq))
        assert [e.seq for e in tail] == list(range(all_evs[k].seq + 1,
                                                    len(all_evs) + 1))
    assert list(log.events_after(0)) == all_evs      # 0 = 全量
    assert list(log.events_after(len(all_evs))) == []  # 尾部之后为空


async def test_events_between_closed_interval():
    """[lo, hi] 闭区间切片;lo > hi → 空迭代。"""
    log = SessionLog(sid=SID)
    await _conversation(log)
    got = list(log.events_between(2, 3))
    assert [e.seq for e in got] == [2, 3]
    assert [e.type for e in got] == ["user.message", "llm.response"]
    assert list(log.events_between(5, 1)) == []
    assert list(log.events_between(9, 10)) == []      # 越界空


async def test_get_single_and_hole_none():
    """get(seq):存在 → Envelope;越界/空洞 → None 不抛。"""
    log = SessionLog(sid=SID)
    await _conversation(log)
    env = log.get(2)
    assert isinstance(env, Envelope) and env.type == "user.message"
    assert log.get(0) is None and log.get(99) is None


# ===================================================================== rebuild
async def test_rebuild_from_log_invariant_inv03():
    """INV-03:rebuild_from_log 后缓存与重建前逐事件一致,派生逐条一致。"""
    log, store = _store_wired()
    await _conversation(log)
    before_evs = list(log.events_after())
    before_msgs = log.derive_messages()
    assert len(store.replay()) == len(before_evs), "总线订阅者应完整落真源"

    log.rebuild_from_log()

    after_evs = list(log.events_after())
    assert after_evs == before_evs, "rebuild 与缓存逐事件一致(INV-03)"
    assert log.derive_messages() == before_msgs
    assert log.stats() == {"session_id": SID, "seq": len(before_evs),
                           "event_count": len(before_evs), "closed": False,
                           "folded_ranges": [], "holes_warned": []}
    # append 后再 rebuild 仍一致(缓存纪律:append 即失效,可随时重建)
    await log.append("user.message", {"content": "追加一问"}, actor="user")
    log.rebuild_from_log()
    assert list(log.events_after()) == before_evs + [log.get(len(before_evs) + 1)]


# ===================================================================== 恢复
async def test_open_session_recovers_state():
    """重启恢复:open_session 重放后 _seq/事件/派生与崩溃前完全一致。"""
    log, store = _store_wired()
    await _conversation(log)
    before_msgs = log.derive_messages()
    before_evs = list(log.events_after())

    recovered = await open_session(SID, store)

    assert isinstance(recovered, SessionLog)
    assert recovered.sid == SID
    assert recovered.stats()["seq"] == len(before_evs)
    assert list(recovered.events_after()) == before_evs
    assert recovered.derive_messages() == before_msgs   # 上下文与崩溃前一致
    assert recovered.get(2).type == "user.message"
    # 恢复后仍可续写:seq 从最后完整点 +1,不重复不空洞
    e = await recovered.append("user.message", {"content": "重启后提问"},
                               actor="user")
    assert e.seq == len(before_evs) + 1


async def test_open_session_empty_store_is_new_session():
    """空存储 = 新会话(不抛):等 session.created,EVT-106 守卫首事件。"""
    recovered = await open_session(SID, FakeStore())
    assert recovered.stats()["seq"] == 0 and recovered.stats()["event_count"] == 0
    with pytest.raises(PyHError) as ei:
        await recovered.append("user.message", {"content": "hi"}, actor="user")
    assert ei.value.code == "EVT-106"
    e = await recovered.append("session.created",
                               {"title": "", "model": "m"}, actor="system")
    assert e.seq == 1


async def test_open_session_finished_still_rejects():
    """终态会话重启恢复后仍拒写(EVT-104);closed 状态随日志重放重建。"""
    log, store = _store_wired()
    await _bootstrap(log)
    await log.append("session.finished", {"reason": "timeout"}, actor="system")
    recovered = await open_session(SID, store)
    assert recovered.stats()["closed"] is True
    with pytest.raises(PyHError) as ei:
        await recovered.append("user.message", {"content": "x"}, actor="user")
    assert ei.value.code == "EVT-104"


async def test_open_session_filters_other_sessions():
    """多会话文件过滤:replay 混入他会话事件时按 sid 过滤,seq 只认本会话。"""
    store = FakeStore()
    # 手工构造两会话交错行(sid A 1..2 后混 B,再回 A)
    for env in (
        Envelope(seq=1, ts="2026-09-06T07:00:00.000001Z", type="session.created",
                 session_id=SID, actor="system",
                 payload={"title": "", "model": "m"}),
        Envelope(seq=2, ts="2026-09-06T07:00:00.000002Z", type="user.message",
                 session_id=SID, actor="user", payload={"content": "甲"}),
        Envelope(seq=1, ts="2026-09-06T07:00:00.000003Z", type="user.message",
                 session_id=SID2, actor="user", payload={"content": "乙"}),
        Envelope(seq=3, ts="2026-09-06T07:00:00.000004Z", type="user.message",
                 session_id=SID, actor="user", payload={"content": "丙"}),
    ):
        store.record(None, env)
    recovered = await open_session(SID, store)
    assert recovered.stats()["event_count"] == 3
    evs = list(recovered.events_after())
    assert [e.seq for e in evs] == [1, 2, 3]
    assert [e.session_id for e in evs] == [SID, SID, SID]
    # 他会话的"乙"被过滤;本会话 1..3 连续无空洞
    assert [e.payload["content"] for e in evs if e.type == "user.message"] \
        == ["甲", "丙"]


async def test_open_session_warn_hole_undeclared():
    """无 compacted 声明的 seq 空洞 → warn_hole 告警(F031),回放不中断、不回填。"""
    store = FakeStore()
    store.record(None, Envelope(
        seq=1, ts="2026-09-06T07:00:00.000001Z", type="session.created",
        session_id=SID, actor="system", payload={"title": "", "model": "m"}))
    store.record(None, Envelope(
        seq=3, ts="2026-09-06T07:00:00.000002Z", type="user.message",
        session_id=SID, actor="user", payload={"content": "跳号后"}))
    recovered = await open_session(SID, store)
    assert recovered.stats()["holes_warned"] == [3], "seq 空洞(缺 2)应告警"
    # 空洞不回填:_seq 从最后完整点续写
    e = await recovered.append("user.message", {"content": "继续"}, actor="user")
    assert e.seq == 4


async def test_rebuild_derived_cache_invalidation_on_append():
    """缓存纪律:append 即失效——同一 max_tokens 派生在 append 后反映新事件。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    await log.append("user.message", {"content": "q1"}, actor="user")
    assert len(log.derive_messages()) == 1
    assert log.history_cache is not None       # 尾部未变时缓存有效
    await log.append("user.message", {"content": "q2"}, actor="user")
    assert log.history_cache is None, "append 后 history_cache 必须整体失效(INV-03)"
    assert len(log.derive_messages()) == 2


async def test_close_marker_rejects_later_append():
    """close_marker 置位后(agent.close 场景)任何 append → EVT-104;不写事件。"""
    log = SessionLog(sid=SID)
    await _bootstrap(log)
    log.close_marker()
    assert log.stats()["closed"] is True
    with pytest.raises(PyHError) as ei:
        await log.append("user.message", {"content": "x"}, actor="user")
    assert ei.value.code == "EVT-104"
    assert log.stats()["event_count"] == 1, "close_marker 本身不写事件"
