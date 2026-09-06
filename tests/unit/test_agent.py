"""agent 模块单测 — 契约:specs/agent.py.md(权威)+ DIS-CORE §2 + PRD-Core §2.3/§2.5

覆盖面(任务要求全项):
    create_agent:ctx 硬接线注入、一会话一 agent 占位/重名 BUSY、headless 解析
    enter:新会话 created 首事件 seq=1、幂等、恢复会话不二次 created、
          closed 后 enter → BUSY、失败回滚
    submit:user.message 强同步落盘先于 loop.wake(GWT-A2-01)、空消息 EVT-100、
          未 enter/closed 后 → BUSY、headless 自动 close(GWT-A2-02)、
          交互模式不写 finished 直至显式 close(GWT-A2-03)
    close:finished 唯一归属/幂等双触发、资源释放(摘订阅/注销/占位)、
          finished 落盘失败回滚可重入、逆序 detach 全部 caps
    attach/announce/detach:白名单 TLB-802(GWT-A2-04 attach("llm") 拒)、
          重复 TLB-801、遮蔽脊柱成员 TLB-802、expose_to_llm 注册/注销、
          registry.updated 留痕广播、detach 幂等、announce 静默
    _on_bus_event:异会话过滤、approval.granted → paused loop resume
    snapshot / 多 Agent 隔离(各自事件流 seq 独立、关一个不扰另一个)

loop/tools/llm/scope 属阶段后续模块,以替身注入(与 test_session 同风格);
总线与注册表用真实 EventBus/Registry。BUSY 为字面量码(ERR.md §2.11),
断言 ei.value.code == "BUSY"(PyHError 直接构造,不经 raise_code 兜底)。
"""
import types

import pytest

from pyharness.bus import EventBus, Registry
from pyharness.core.agent import Agent, Ctx, create_agent
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError
from pyharness.events import EVENT_TYPES, Envelope

SID1 = "s-agent-0001"
SID2 = "s-agent-0002"
MODEL = "deepseek-chat"


# ===================================================================== 替身
class FakeStore:
    """SessionStore 内存替身(总线日志订阅者负责 record;replay/flush 即真源语义)。

    与 test_session.FakeStore 相同,附加 sid 过滤(共享总线多会话时只收本会话)。
    """

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self._rows: list[Envelope] = []
        self.flushed: list[int] = []

    def record(self, type_, payload) -> None:
        """总线订阅者回调:只收本会话事件(真实现 = 按会话文件分流)。"""
        if isinstance(payload, Envelope) and payload.session_id == self.sid:
            self._rows.append(payload)

    def replay(self):
        return list(self._rows)

    async def flush(self, seq: int) -> None:
        self.flushed.append(seq)


class FakeLoop:
    """agent_loop 阶段前替身:state/wake/cancel/resume/pending 契约面。"""

    def __init__(self, state: str = "idle", result=None) -> None:
        self.state = state
        self.pending = []
        self.woken: list[Envelope] = []
        self.cancel_calls: list[str] = []
        self.resumes = 0
        self._result = result          # wake 返回值(模拟同步完成的 RunResult)

    async def wake(self, env):
        self.woken.append(env)
        return self._result            # None = 入队场景;对象 = idle 直接 run 完

    async def cancel(self, reason: str = "cancelled") -> None:
        self.cancel_calls.append(reason)
        self.state = "idle"

    async def resume(self) -> None:
        self.resumes += 1
        self.state = "running"


class FakeTools:
    """tools 注册表阶段前替身:register_definition/unregister_definition 记录。"""

    def __init__(self) -> None:
        self.registered: list = []
        self.unregistered: list[str] = []

    def register_definition(self, defn) -> None:
        self.registered.append(defn)

    def unregister_definition(self, name: str) -> None:
        self.unregistered.append(name)


class FakeDef:
    """Definition 替身(announce 只消费 name;暴露给 LLM 的编译注册属 tools 层)。"""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeIface:
    """CapNamespace 替身(Definition + Provider 束的最小契约面)。"""

    def __init__(self, name: str = "cap.demo", expose_to_llm: bool = True) -> None:
        self.expose_to_llm = expose_to_llm
        self.definition = FakeDef(name)


def _cfg(**extra) -> types.SimpleNamespace:
    """分层配置替身:仅消费 llm.model(created payload)与可选 headless。"""
    return types.SimpleNamespace(llm=types.SimpleNamespace(model=MODEL), **extra)


def _wired_log(sid: str, bus: EventBus, store: FakeStore) -> SessionLog:
    """SessionLog 装配:总线分发 → 存储订阅者(事件一入内存即入真源队列)。"""
    for t in EVENT_TYPES:
        bus.subscribe(t, store.record, owner="persistence")
    return SessionLog(sid=sid, persistence=store, bus=bus)


def _make_stack(sid: str = SID1, *, loop=None, tools=None, headless: bool = False,
                cfg=None):
    """独立装配栈:bus/registry 独立(隔离测试另用共享变体)。"""
    bus = EventBus()
    registry = Registry(bus)
    store = FakeStore(sid)
    log = _wired_log(sid, bus, store)
    spine = types.SimpleNamespace(
        session=log, bus=bus, registry=registry, persistence=store,
        scope=None, llm=None,
        tools=tools if tools is not None else FakeTools(),
        loop=loop if loop is not None else FakeLoop(),
        sys=None, storage=None, active_agents={}, headless=headless)
    return spine, log, store, bus, registry


async def _make_agent(sid: str = SID1, *, loop=None, tools=None,
                      headless: bool = False, cfg=None, enter: bool = True):
    """装配 + create_agent(+默认 enter)。"""
    spine, log, store, bus, registry = _make_stack(
        sid, loop=loop, tools=tools, headless=headless, cfg=cfg)
    ag = create_agent(sid, spine, cfg or _cfg())
    if enter:
        await ag.enter()
    return ag, spine, log, store, bus, registry


def _envelopes(log: SessionLog) -> list[Envelope]:
    return list(log.events_after())


def _finished_count(log: SessionLog) -> int:
    return sum(1 for ev in _envelopes(log) if ev.type == "session.finished")


def _busy(ei) -> None:
    """BUSY 断言(ei = pytest.raises 上下文):字面量码,不经 raise_code 兜底。"""
    assert ei.value.code == "BUSY"


# ===================================================================== 工厂
async def test_create_agent_wires_ctx_and_occupies_session():
    """create_agent:ctx 硬接线(脊柱模块逐名注入)+ active_agents 占位。"""
    ag, spine, log, store, bus, registry = await _make_agent(enter=False)
    assert isinstance(ag, Agent)
    assert ag.state == "init"
    assert ag.session_id == SID1 and len(ag.agent_id) == 32   # uuid4().hex
    assert ag.ctx is not None and isinstance(ag.ctx, Ctx)
    # ctx 门面:会话/总线/注册表/能力挂载枢纽逐名注入
    assert ag.ctx.session is log
    assert ag.ctx.bus is bus and ag.ctx.registry is registry
    assert ag.ctx.loop is spine.loop and ag.ctx.tools is spine.tools
    assert ag.ctx.config is not None and ag.ctx.host is not None
    # 一会话一 agent:占位登记已生效;headless 回落 False(交互默认)
    assert spine.active_agents[SID1] is ag
    assert ag.headless is False
    assert ag.caps == {} and ag.state == "init"


async def test_create_agent_duplicate_session_busy():
    """同会话二次 create → BUSY(防双 loop 竞争 seq);首句柄不受影响。"""
    ag, spine, *_ = await _make_agent(enter=True)
    with pytest.raises(PyHError) as ei:
        create_agent(SID1, spine, _cfg())
    _busy(ei)
    assert spine.active_agents[SID1] is ag          # 占位未被覆盖


async def test_create_agent_headless_resolution():
    """headless 解析:spine.headless 注入(CFG.md R8:无配置键,外壳上下文判定)。"""
    ag, *_ = await _make_agent(headless=True, enter=False)
    assert ag.headless is True


# ===================================================================== enter
async def test_enter_bootstraps_created_as_first_event_seq1():
    """enter 激活:新会话 created 首事件 seq=1,payload title/model 按契约。"""
    ag, spine, log, store, bus, registry = await _make_agent()
    evs = _envelopes(log)
    assert len(evs) == 1
    assert evs[0].type == "session.created" and evs[0].seq == 1
    assert evs[0].payload == {"title": "", "model": MODEL}
    # 注册表可寻址(plugin:{agent_id})、订阅按属主登记、互斥抢占
    assert registry.lookup("plugin", ag.agent_id) is ag
    assert ag.state == "ready"
    assert ag._session_lock is True
    assert spine.active_agents[SID1] is ag
    assert _agent_sub_count(bus, ag.agent_id) == 1


async def test_enter_idempotent_single_created():
    """enter 幂等:二次调用直接返回,created 仅一条(EVT-106 守卫)。"""
    ag, _, log, *_ = await _make_agent()
    await ag.enter()
    assert ag.state == "ready"
    assert _finished_count(log) == 0
    assert sum(1 for _ in _envelopes(log)) == 1     # 仍只有 created


async def test_enter_on_restored_session_no_second_created():
    """恢复会话(日志已有 created)→ enter 不二次引导 created(正常不可达守卫)。"""
    spine, log, *_ = _make_stack(SID1)
    await log.append("session.created", {"title": "老会话", "model": MODEL},
                     actor="system")
    ag = create_agent(SID1, spine, _cfg())
    await ag.enter()
    evs = _envelopes(log)
    assert sum(1 for e in evs if e.type == "session.created") == 1
    assert evs[0].seq == 1 and ag.state == "ready"


async def test_enter_after_close_busy():
    """closed 后 enter → BUSY(句柄终局,建议新开会话)。"""
    ag, *_ = await _make_agent()
    await ag.close(reason="idle")
    with pytest.raises(PyHError) as ei:
        await ag.enter()
    _busy(ei)


async def test_enter_failure_rolls_back_occupancy():
    """enter 失败(注册表拒绝)回滚:占位释放,可重试不残留半激活。"""

    class PoisonRegistry(Registry):
        def register(self, kind, key, obj):
            raise PyHError("TLB-801", ctx={"kind": kind, "key": key,
                                           "detail": "注入失败"})

    bus = EventBus()
    store = FakeStore(SID1)
    log = _wired_log(SID1, bus, store)
    spine = types.SimpleNamespace(
        session=log, bus=bus, registry=PoisonRegistry(bus),
        persistence=store, scope=None, llm=None, tools=FakeTools(),
        loop=FakeLoop(), sys=None, storage=None, active_agents={})
    ag = create_agent(SID1, spine, _cfg())
    with pytest.raises(PyHError) as ei:
        await ag.enter()
    assert ei.value.code == "TLB-801"
    assert SID1 not in spine.active_agents          # 占位已回滚释放
    assert ag._session_lock is False and ag.state == "init"


# ===================================================================== submit
async def test_submit_user_message_sync_before_loop_wake_gwt_a2_01():
    """GWT-A2-01:submit 后 user.message 在 loop.wake 前已强同步落盘。"""
    loop = FakeLoop(result=types.SimpleNamespace(
        reason="complete", run_id="r1", turns=1, last_seq=2))
    ag, _, log, store, *_ = await _make_agent(loop=loop)
    result = await ag.submit("你好")
    assert result.reason == "complete"
    assert len(loop.woken) == 1
    env = loop.woken[0]
    assert env.type == "user.message" and env.seq == 2
    assert env.payload["content"] == "你好"
    # 强同步①:user.message 已落盘(flush 含 seq 2)才进入 loop
    assert 2 in store.flushed
    evs = _envelopes(log)
    assert [e.type for e in evs] == ["session.created", "user.message"]
    # 交互模式:run 结束(loop idle)回 READY,不写 finished(GWT-A2-03 前半)
    assert ag.state == "ready"
    assert _finished_count(log) == 0


async def test_submit_empty_message_evt100():
    """空消息 → EVT-100(信封层前置拒绝);日志零副作用。"""
    ag, _, log, *_ = await _make_agent()
    with pytest.raises(PyHError) as ei:
        await ag.submit("   ")
    assert ei.value.code == "EVT-100"
    assert len(_envelopes(log)) == 1                # 仅 created


async def test_submit_before_enter_busy():
    """未 enter(init)直接 submit → BUSY(状态机:enter 激活后方可 submit)。"""
    ag, *_ = await _make_agent(enter=False)
    with pytest.raises(PyHError) as ei:
        await ag.submit("你好")
    _busy(ei)


async def test_submit_after_close_busy():
    """closed 后 submit → BUSY 显式拒(不排队不静默,建议新开会话)。"""
    ag, *_ = await _make_agent()
    await ag.close(reason="idle")
    with pytest.raises(PyHError) as ei:
        await ag.submit("再来一轮")
    _busy(ei)


async def test_headless_auto_close_on_run_end_gwt_a2_02():
    """GWT-A2-02:headless 模式 run 结束自动 close——恰一条 finished(complete),
    state=closed,再 submit → BUSY。"""
    loop = FakeLoop(result=types.SimpleNamespace(
        reason="complete", run_id="r1", turns=1, last_seq=2))
    ag, _, log, *_ = await _make_agent(loop=loop, headless=True)
    await ag.submit("你好")
    assert ag.state == "closed"
    assert _finished_count(log) == 1
    fin = [e for e in _envelopes(log) if e.type == "session.finished"][0]
    assert fin.payload["reason"] == "complete"
    with pytest.raises(PyHError) as ei:
        await ag.submit("还来?")
    _busy(ei)


async def test_interactive_no_finished_until_explicit_close_gwt_a2_03():
    """GWT-A2-03:交互模式两次 submit finished 计数 == 0 直到显式 close。"""
    loop = FakeLoop(result=types.SimpleNamespace(
        reason="complete", run_id="r1", turns=1, last_seq=3))
    ag, _, log, *_ = await _make_agent(loop=loop)
    await ag.submit("第一问")
    await ag.submit("第二问")
    assert ag.state == "ready"
    assert _finished_count(log) == 0                # 显式 close 前绝不写 finished
    await ag.close(reason="idle")
    assert _finished_count(log) == 1
    assert ag.state == "closed"


# ===================================================================== close
async def test_close_idempotent_double_trigger_single_finished():
    """close 幂等:双触发(外壳/repair)安全——finished 至多一条(EVT-104)。"""
    ag, _, log, *_ = await _make_agent()
    await ag.close(reason="idle")
    await ag.close(reason="error")                  # 二次调用直接返回
    assert ag.state == "closed"
    assert _finished_count(log) == 1
    assert _envelopes(log)[-1].payload["reason"] == "idle"


async def test_close_cancels_inflight_run_then_finished():
    """close 有在途 run(running)→ 先 loop.cancel(reason=close) 再写 finished。"""
    loop = FakeLoop(state="running")
    ag, _, log, *_ = await _make_agent(loop=loop)
    await ag.close(reason="cancelled")
    assert loop.cancel_calls == ["close"]           # 声明式取消(loop 收尾)
    assert _finished_count(log) == 1
    assert _envelopes(log)[-1].payload["reason"] == "cancelled"
    assert ag.state == "closed"


async def test_close_releases_resources():
    """close 清理:摘订阅/注销句柄/释放会话占位与互斥 → closed 终局。"""
    ag, spine, log, store, bus, registry = await _make_agent()
    await ag.close(reason="idle")
    assert ag.state == "closed"
    assert _agent_sub_count(bus, ag.agent_id) == 0  # 摘订阅(owner 精确摘除)
    with pytest.raises(PyHError) as ei:             # 注册表句柄已释放
        registry.lookup("plugin", ag.agent_id)
    assert ei.value.code == "TLB-801"
    assert SID1 not in spine.active_agents          # 会话互斥已释放(可新开)
    assert ag._session_lock is False
    snap = ag.snapshot()
    assert snap["state"] == "closed" and snap["seq"] == 2


async def test_close_finished_failure_rolls_back_state():
    """close 落盘 finished 失败(日志已终态 → EVT-104)→ 回滚 state 可重入。

    日志终态(已有 finished)说明存在绕过 close 的写口(INV-01 内部 bug);
    此时 close 须上抛 EVT-104 且把 state 回滚,使 repair 后重试仍可驱动,
    而非卡死在 stopping 被幂等短路。
    """
    spine, log2, *_ = _make_stack(SID2)
    await log2.append("session.created", {"title": "", "model": MODEL},
                      actor="system")
    await log2.append("session.finished", {"reason": "idle"}, actor="system")
    ag2 = create_agent(SID2, spine, _cfg())
    await ag2.enter()                               # 恢复终态会话:enter 仍可占位
    with pytest.raises(PyHError) as ei:
        await ag2.close(reason="idle")              # finished 落盘 → EVT-104
    assert ei.value.code == "EVT-104"
    assert ag2.state == "ready"                     # 回滚:重试可再次驱动 close
    assert SID2 in spine.active_agents              # 失败路径不误释放占位


async def test_close_reverse_detaches_caps_in_order():
    """close 终局驱动逆序 detach 全部已挂载能力(后挂先摘,DIS-SEAM §2.4)。"""
    tools = FakeTools()
    ag, _, log, *_ = await _make_agent(tools=tools)
    iface_a = FakeIface(name="cap.agent", expose_to_llm=True)
    iface_u = FakeIface(name="cap.ui", expose_to_llm=False)
    await ag.attach_capability("agent", iface_a)
    await ag.attach_capability("ui", iface_u)
    assert ag.caps.keys() == {"agent", "ui"}
    await ag.close(reason="idle")
    # 逆序摘除:先 ui(announce 时 expose=False 未注册 defn),后 agent
    assert tools.unregistered == ["cap.agent"]      # ui 未 expose → 无注销项
    assert ag.caps == {}                            # 全部摘净
    assert not hasattr(ag.ctx, "ui") and not hasattr(ag.ctx, "agent")
    assert ag.state == "closed"
    assert _finished_count(log) == 1


# ========================================================== attach/announce/detach
async def test_attach_agent_ok_attach_llm_tlb802_gwt_a2_04():
    """GWT-A2-04:attach("agent") 成功而 attach("llm") 抛 TLB-802(白名单)。"""
    ag, *_ = await _make_agent()
    iface = FakeIface(name="cap.plan", expose_to_llm=True)
    await ag.attach_capability("agent", iface)
    assert ag.caps["agent"] is iface
    assert ag.ctx.agent is iface                    # 挂载后 ctx.{ns} 立即可消费
    # 白名单外(脊柱对象名)→ TLB-802,ctx.llm 不被触碰
    with pytest.raises(PyHError) as ei:
        await ag.attach_capability("llm", FakeIface(name="cap.llm"))
    assert ei.value.code == "TLB-802"
    assert ag.ctx.llm is None and "llm" not in ag.caps


async def test_attach_duplicate_namespace_tlb801():
    """ns 重复挂载 → TLB-801(先 detach 再挂,不留脏数据)。"""
    ag, *_ = await _make_agent()
    await ag.attach_capability("agent", FakeIface(name="cap.a"))
    with pytest.raises(PyHError) as ei:
        await ag.attach_capability("agent", FakeIface(name="cap.a2"))
    assert ei.value.code == "TLB-801"
    assert ag.caps["agent"].definition.name == "cap.a"   # 原挂载未被覆盖


async def test_attach_shadowing_spine_member_tlb802():
    """挂载遮蔽脊柱成员(ctx.session 等)→ TLB-802(INV-08 cap 不可覆盖)。"""
    ag, *_ = await _make_agent()
    for ns in ("session", "tools"):
        with pytest.raises(PyHError) as ei:
            await ag.attach_capability(ns, FakeIface(name=f"cap.{ns}"))
        assert ei.value.code == "TLB-802"
        assert ns not in ag.caps                    # 拒绝零副作用


async def test_announce_silent_when_not_attached():
    """announce 无此能力(attach 前调用)→ 静默返回 None。"""
    ag, *_ = await _make_agent()
    assert await ag.announce("ghost") is None
    assert ag.ctx.tools.registered == []


async def test_announce_registers_definition_when_exposed():
    """announce:expose_to_llm → Definition 进 tools 注册表(LLM 立即可见)。"""
    ag, *_ = await _make_agent()
    tools = ag.ctx.tools
    iface_exposed = FakeIface(name="cap.fs", expose_to_llm=True)
    iface_hidden = FakeIface(name="cap.kv", expose_to_llm=False)
    await ag.attach_capability("agent", iface_exposed)
    assert tools.registered == [iface_exposed.definition]
    await ag.attach_capability("ui", iface_hidden)
    assert len(tools.registered) == 1               # 未声明 expose → 不进注册表


async def test_registry_updated_attach_del_broadcasts():
    """attach/detach 留痕:registry.updated 广播(op attach/del,瞬时总线语义)。"""
    ag, _, log, store, bus, *_ = await _make_agent()
    seen = []

    def collector(type_, payload):
        if payload.get("kind") == "capability":
            seen.append((payload["op"], payload["key"]))

    bus.subscribe("registry.updated", collector, owner="test-collector")
    await ag.attach_capability("agent", FakeIface(name="cap.plan"))
    await ag.detach_capability("agent")
    assert seen == [("attach", "agent"), ("del", "agent")]
    assert len(_envelopes(log)) == 1                # 瞬时事件不入会话日志


async def test_detach_capability_idempotent_cleans_ctx_and_tools():
    """detach:注销 expose_to_llm 定义 + 摘 ctx 属性 + caps 清空;再 detach 幂等。"""
    tools = FakeTools()
    ag, *_ = await _make_agent(tools=tools)
    await ag.attach_capability("agent", FakeIface(name="cap.tool", expose_to_llm=True))
    await ag.detach_capability("agent")
    assert tools.unregistered == ["cap.tool"]
    assert not hasattr(ag.ctx, "agent")
    assert ag.caps == {}
    await ag.detach_capability("agent")             # 幂等:未挂载 → 直接返回
    assert tools.unregistered == ["cap.tool"]


# ================================================================ 事件订阅
async def test_on_bus_event_ignores_foreign_session():
    """前缀订阅兜底过滤:异会话事件不触发 resume。"""
    loop = FakeLoop(state="paused")
    ag, *_ = await _make_agent(loop=loop)
    foreign = types.SimpleNamespace(session_id=SID2, seq=99)
    await ag._on_bus_event("approval.granted", foreign)
    assert loop.resumes == 0


async def test_on_bus_event_approval_granted_resumes_paused_loop():
    """approval.granted + loop paused → loop.resume(重入 guard 链起点)。"""
    loop = FakeLoop(state="paused")
    ag, *_ = await _make_agent(loop=loop)
    own = types.SimpleNamespace(session_id=SID1, seq=9)
    await ag._on_bus_event("approval.granted", own)
    assert loop.resumes == 1
    # 非恢复事件(事实已在日志):只做可见性钩子,不改状态
    await ag._on_bus_event("user.message", own)
    assert loop.resumes == 1


# ================================================================ 只读/隔离
async def test_snapshot_shape():
    """snapshot:只读句柄查询字段齐全(agent_id/session/state/loop/seq/queue/caps)。"""
    loop = FakeLoop()
    ag, _, log, *_ = await _make_agent(loop=loop)
    await ag.attach_capability("agent", FakeIface(name="cap.plan"))
    snap = ag.snapshot()
    assert snap["agent_id"] == ag.agent_id
    assert snap["session_id"] == SID1
    assert snap["state"] == "ready" and snap["loop_state"] == "idle"
    assert snap["seq"] == 1 and snap["queue_len"] == 0
    assert snap["caps"] == ["agent"]


async def test_multi_agent_isolation_shared_bus():
    """多 Agent 隔离(共享总线/注册表):各自事件流 seq 独立,关一个不扰另一个。"""
    bus = EventBus()
    registry = Registry(bus)
    store1, store2 = FakeStore(SID1), FakeStore(SID2)
    log1 = _wired_log(SID1, bus, store1)
    log2 = _wired_log(SID2, bus, store2)
    shared_tools = FakeTools()
    spine1 = types.SimpleNamespace(
        session=log1, bus=bus, registry=registry, persistence=store1,
        scope=None, llm=None, tools=shared_tools, loop=FakeLoop(),
        sys=None, storage=None, active_agents={}, headless=False)
    spine2 = types.SimpleNamespace(
        session=log2, bus=bus, registry=registry, persistence=store2,
        scope=None, llm=None, tools=shared_tools, loop=FakeLoop(),
        sys=None, storage=None, active_agents={}, headless=False)
    ag1 = create_agent(SID1, spine1, _cfg())
    ag2 = create_agent(SID2, spine2, _cfg())
    await ag1.enter()
    await ag2.enter()
    # 各自 created seq=1,事件流互不污染
    assert [e.seq for e in _envelopes(log1)] == [1]
    assert [e.seq for e in _envelopes(log2)] == [1]
    assert registry.lookup("plugin", ag1.agent_id) is ag1
    assert registry.lookup("plugin", ag2.agent_id) is ag2
    # 关掉 ag1:finished 只写进 log1;ag2 保持 ready 且零 finished
    await ag1.close(reason="idle")
    assert _finished_count(log1) == 1
    assert _finished_count(log2) == 0
    assert ag2.state == "ready" and ag2._session_lock is True
    # 同会话 active agent 双占位仍被防:ag2 会话再 create → BUSY
    with pytest.raises(PyHError) as ei:
        create_agent(SID2, spine2, _cfg())
    _busy(ei)


# ------------------------------------------------------------------ 内部辅助
def _agent_sub_count(bus: EventBus, owner: str) -> int:
    """统计某 owner 的总线订阅数(精确 + 通配;bus 内部表只读扫描)。"""
    n = sum(1 for lst in bus._by_type.values() for s in lst if s.owner == owner)
    n += sum(1 for s in bus._wild if s.owner == owner)
    return n
