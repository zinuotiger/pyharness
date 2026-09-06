"""bus 模块单测 — 契约:specs/bus.py.md 全量 + PARAMETER-ANCHOR(F005 背压 1000)

覆盖面(对应 PRD F001-F006):
    EventBus:精确/通配订阅、注册序、两参/单参 handler、when 谓词、卸载摘除、
              三模式分发(sequential/waterfall/parallel)、STOP 短路、
              强同步事件恒 sequential、EVT-102 类型闸、EVT-103 异常隔离、
              背压 FIFO 与丢弃计数(bus.backpressure 可观测)
    Registry:三类索引往返、重名 TLB-801、保留名 BUS-002、缺失查询/注销不静默、
              registry.updated 留痕(op=add/del,F003)
    PluginManager:install/activate/deactivate/uninstall 五态生命周期、依赖拓扑、
              BUS-002/BUS-003、激活失败回滚、热卸载、emit_as origin 注入

注意:总线只中转不落盘——payload 不校验;类型闸与 events.vocab 词表联动,
词表 57 核心类型开箱即发(见 test_emit_vocab_core_type_linked)。
"""
import asyncio
import time

import pytest
from pydantic import BaseModel

from pyharness import bus as B
from pyharness.bus import (Capability, EventBus, PluginManager, Registry,
                           STOP, Subscription)
from pyharness.errors import PyHError

# 类型注册用的 payload 模型(总线不校验,仅作 schema 声明占位)
class PingPayload(BaseModel):
    msg: str = ""


def _mk_manifest(pid: str, **over) -> dict:
    """构造合法 manifest 骨架(能力可经 capabilities 键注入)。"""
    m = {"id": pid, "version": "0.1.0", "api_version": "1",
         "requires": [], "capabilities": []}
    m.update(over)
    return m


class _FakeSession:
    """ctx.session 桩:append 记录 (type_, payload),供 F004 留痕断言。"""

    def __init__(self) -> None:
        self.events: list = []

    async def append(self, type_: str, **payload) -> None:
        self.events.append((type_, dict(payload)))


class _FakeCtx:
    """AgentCtx 鸭子桩(bus 仅消费 ctx.session.append)。"""

    def __init__(self) -> None:
        self.session = _FakeSession()


# =====================================================================
# EventBus — 订阅与分发(F001/F002)
# =====================================================================
def test_subscribe_returns_subscription():
    """Subscription 字段:精确/通配旗标、owner/when/seq 注册序。"""
    bus = EventBus()
    seen = []
    sub1 = bus.subscribe("user.message", lambda p: seen.append(p), owner="a",
                         when=lambda p: True)
    sub2 = bus.subscribe("tool.*", lambda p: seen.append(p), owner="b")
    assert isinstance(sub1, Subscription)
    assert sub1.pattern == "user.message" and sub1.wildcard is False
    assert sub2.pattern == "tool.*" and sub2.wildcard is True
    assert sub1.owner == "a" and sub2.owner == "b"
    assert sub1.when is not None and sub2.when is None
    assert sub1.seq < sub2.seq                       # 注册序单调


async def test_emit_exact_dispatch_two_arg_handler():
    """精确订阅:两参 handler 收 (type_, payload);统计 delivered。"""
    bus = EventBus()
    got = []
    bus.subscribe("user.message", lambda t, p: got.append((t, p)))
    stats = await bus.emit("user.message", {"content": "hi"})
    assert got == [("user.message", {"content": "hi"})]
    assert stats == {"delivered": 1, "errored": 0}


async def test_emit_one_arg_handler():
    """单参 handler 收 payload(签名 (payload))。"""
    bus = EventBus()
    got = []
    bus.subscribe("user.message", lambda p: got.append(p))
    await bus.emit("user.message", {"content": "x"})
    assert got == [{"content": "x"}]


async def test_dispatch_registration_order():
    """同 pattern 命中按注册序执行(先订先达)。"""
    bus = EventBus()
    order = []
    bus.subscribe("tool.result", lambda t, p: order.append("first"), owner="a")
    bus.subscribe("tool.result", lambda t, p: order.append("second"), owner="b")
    await bus.emit("tool.result", {"name": "fs", "ok": True})
    assert order == ["first", "second"]


async def test_wildcard_segment_and_exact_first():
    """通配段级前缀命中;精确先、通配后(即使精确后订)。"""
    bus = EventBus()
    order = []
    bus.subscribe("tool.*", lambda t, p: order.append("wild"), owner="w")  # 先订
    bus.subscribe("tool.call", lambda t, p: order.append("exact"), owner="e")
    await bus.emit("tool.call", {"name": "fs_read"})
    assert order == ["exact", "wild"]               # 精确先,与注册序无关
    # 段级边界:通配不命中无共同前缀的类型(toolbox.* 语义)
    bus2 = EventBus()
    got = []
    bus2.subscribe("tool.*", lambda t, p: got.append(t))
    await bus2.emit("llm.chunk", {"delta": "a"})    # 词表类型,非 tool 前缀
    assert got == []


def test_emit_unregistered_type_evt102_failfast():
    """未注册类型 emit → EVT-102(创建协程前同步拒投,fail-fast)。"""
    bus = EventBus()
    with pytest.raises(PyHError) as ei:
        bus.emit("ghost.event", {})                  # 无需 await 即抛
    assert ei.value.code == "EVT-102"


async def test_emit_vocab_core_type_linked():
    """词表联动:57 核心类型(bus 未逐个 register_type)开箱即发。"""
    bus = EventBus()
    got = []
    bus.subscribe("agent.message", lambda t, p: got.append(p))
    stats = await bus.emit("agent.message", {"content": "ok"})
    assert stats == {"delivered": 1, "errored": 0}
    assert got == [{"content": "ok"}]


async def test_register_type_then_emit_custom_type():
    """register_type 后自定义类型(插件命名空间)可发。"""
    bus = EventBus()
    got = []
    bus.register_type("plugin.echo.ping", PingPayload)
    bus.subscribe("plugin.echo.ping", lambda t, p: got.append(p))
    stats = await bus.emit("plugin.echo.ping", {"msg": "hi"})
    assert stats["delivered"] == 1 and got == [{"msg": "hi"}]


async def test_register_type_duplicate_evt102():
    """同类型二次注册 → EVT-102(事件类型只增不改)。"""
    bus = EventBus()
    bus.register_type("plugin.echo.ping", PingPayload)
    with pytest.raises(PyHError) as ei:
        bus.register_type("plugin.echo.ping", PingPayload)
    assert ei.value.code == "EVT-102"


async def test_emit_stats_empty_no_subs():
    """无订阅:统计 {delivered:0, errored:0}(sequential)。"""
    bus = EventBus()
    stats = await bus.emit("user.message", {"content": "x"})
    assert stats == {"delivered": 0, "errored": 0}


async def test_sequential_error_isolation_evt103():
    """订阅者异常隔离(EVT-103):不扩散、不中断他人,errored 可观测。"""
    bus = EventBus()
    got = []

    def boom(t, p):                                  # noqa: ARG001
        raise ValueError("订阅者崩溃")

    bus.subscribe("user.message", boom, owner="bad")
    bus.subscribe("user.message", lambda t, p: got.append(p), owner="good")
    stats = await bus.emit("user.message", {"content": "x"})
    assert stats == {"delivered": 1, "errored": 1}
    assert got == [{"content": "x"}]                  # 好订阅者未被中断


async def test_when_predicate_filter():
    """when 谓词 truthy 才投递;payload 原样传递(只读过滤)。"""
    bus = EventBus()
    got = []
    bus.subscribe("user.command", lambda p: got.append(p),
                  when=lambda p: p.get("allow") is True)
    await bus.emit("user.command", {"name": "run", "allow": False})
    assert got == []
    stats = await bus.emit("user.command", {"name": "run", "allow": True})
    assert stats["delivered"] == 1 and got == [{"name": "run", "allow": True}]


async def test_when_predicate_exception_isolated():
    """谓词抛异常 = 该订阅跳过记 EVT-103,其余订阅者不受影响。"""
    bus = EventBus()
    got = []

    def bad_when(p):                                 # noqa: ARG001
        raise RuntimeError("谓词坏")

    bus.subscribe("user.message", lambda p: got.append("bad"), when=bad_when)
    bus.subscribe("user.message", lambda p: got.append("good"), owner="good")
    stats = await bus.emit("user.message", {"content": "x"})
    assert got == ["good"]
    assert stats == {"delivered": 1, "errored": 0}    # 谓词跳过不计 errored


async def test_unsubscribe_all_by_owner():
    """按属主精确摘除(精确+通配);返回条数;幂等重复卸载返回 0。"""
    bus = EventBus()
    got = []
    bus.subscribe("user.message", lambda p: got.append(("a1", p)), owner="A")
    bus.subscribe("user.*", lambda p: got.append(("a2", p)), owner="A")
    bus.subscribe("user.message", lambda p: got.append(("b", p)), owner="B")
    assert bus.unsubscribe_all("A") == 2
    assert bus.unsubscribe_all("A") == 0              # 幂等
    got.clear()
    await bus.emit("user.message", {"content": "x"})
    assert got == [("b", {"content": "x"})]           # B 订阅未被误伤


async def test_unsubscribe_only_own_owner():
    """卸载不伤他人订阅;通配索引同步摘除。"""
    bus = EventBus()
    got = []
    bus.subscribe("tool.*", lambda p: got.append(("a", p)), owner="A")
    bus.subscribe("tool.*", lambda p: got.append(("b", p)), owner="B")
    bus.unsubscribe_all("A")
    await bus.emit("tool.result", {"name": "n", "ok": True})
    assert got == [("b", {"name": "n", "ok": True})]


# =====================================================================
# EventBus — 三模式分发
# =====================================================================
async def test_waterfall_chains_payload():
    """waterfall:前 handler 返回值 = 后 handler 入参;result 收尾。"""
    bus = EventBus()
    seen = []
    bus.subscribe("plugin.echo.ping", lambda t, p: {**p, "n": p.get("n", 0) + 1})
    bus.subscribe("plugin.echo.ping", lambda t, p: seen.append(p["n"]))
    bus.register_type("plugin.echo.ping", PingPayload)
    stats = await bus.emit("plugin.echo.ping", {"msg": "hi"}, mode="waterfall")
    assert seen == [1]
    assert stats["result"] == {"msg": "hi", "n": 1}
    assert stats["delivered"] == 2 and stats["errored"] == 0


async def test_waterfall_stop_shortcircuit():
    """STOP 短路:其后订阅不再执行;result 停在 STOP 前值。"""
    bus = EventBus()
    order = []
    bus.register_type("plugin.echo.ping", PingPayload)
    bus.subscribe("plugin.echo.ping",
                  lambda t, p: order.append("a") or {**p, "n": 1})
    bus.subscribe("plugin.echo.ping", lambda t, p: order.append("b") or STOP)
    bus.subscribe("plugin.echo.ping", lambda t, p: order.append("c") or p)
    stats = await bus.emit("plugin.echo.ping", {"msg": "x"}, mode="waterfall")
    assert order == ["a", "b"]                        # c 未执行(短路)
    assert stats["result"] == {"msg": "x", "n": 1}
    assert stats["delivered"] == 2


async def test_waterfall_void_handler_keeps_acc():
    """void handler(None 返回)不改变 acc,仍计 delivered。"""
    bus = EventBus()
    bus.register_type("plugin.echo.ping", PingPayload)
    bus.subscribe("plugin.echo.ping", lambda t, p: None)     # void
    bus.subscribe("plugin.echo.ping", lambda t, p: {**p, "n": 2})
    stats = await bus.emit("plugin.echo.ping", {"msg": "x"}, mode="waterfall")
    assert stats["result"] == {"msg": "x", "n": 2}
    assert stats["delivered"] == 2 and stats["errored"] == 0


async def test_parallel_delivers_all_async_handlers():
    """parallel:全部匹配订阅并发投递(含 async handler)。"""
    bus = EventBus()
    got = []
    async def h1(t, p):                                  # noqa: ARG001
        await asyncio.sleep(0.02)
        got.append("h1")
    bus.subscribe("user.message", h1)
    bus.subscribe("user.message", lambda p: got.append("h2"))
    stats = await bus.emit("user.message", {"content": "x"}, mode="parallel")
    assert sorted(got) == ["h1", "h2"]
    assert stats == {"delivered": 2, "errored": 0}


async def test_parallel_error_isolated_per_sub():
    """parallel:单个订阅者异常不影响他人投递;errored 计数。"""
    bus = EventBus()

    async def bad(p):                                    # noqa: ARG001
        raise ValueError("parallel 崩溃")

    got = []
    bus.subscribe("user.message", bad, owner="bad")
    bus.subscribe("user.message", lambda p: got.append(p), owner="good")
    stats = await bus.emit("user.message", {"content": "x"}, mode="parallel")
    assert stats == {"delivered": 1, "errored": 1}
    assert got == [{"content": "x"}]


async def test_parallel_sync_handlers_concurrent():
    """同步 handler 经 to_thread 并发(墙钟 < 串行和);非强同步类型。"""
    bus = EventBus()
    bus.register_type("plugin.par.work", PingPayload)
    bus.subscribe("plugin.par.work", lambda p: time.sleep(0.2), owner="a")
    bus.subscribe("plugin.par.work", lambda p: time.sleep(0.2), owner="b")
    t0 = time.perf_counter()
    await bus.emit("plugin.par.work", {"msg": "x"}, mode="parallel")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.35                               # 串行应 ≈0.4+(user.message 等强同步类型除外)


async def test_strong_sync_type_forced_sequential():
    """强同步事件(user.message)恒 sequential:parallel 被压制(墙钟 ≈ 串行和)。"""
    bus = EventBus()

    async def slow(p):                                   # noqa: ARG001
        await asyncio.sleep(0.15)

    bus.subscribe("user.message", slow, owner="a")
    bus.subscribe("user.message", slow, owner="b")
    t0 = time.perf_counter()
    await bus.emit("user.message", {"content": "x"}, mode="parallel")
    elapsed = time.perf_counter() - t0
    assert elapsed >= 0.28                              # 两段 0.15 串行


async def test_strong_sync_type_waterfall_suppressed():
    """强同步事件 waterfall 被压制:不得链式改值,订阅者收原始 payload。"""
    bus = EventBus()
    seen = []
    bus.subscribe("user.message", lambda t, p: {**p, "chained": True})
    bus.subscribe("user.message", lambda t, p: seen.append(p))
    await bus.emit("user.message", {"content": "x"}, mode="waterfall")
    assert seen == [{"content": "x"}]                    # 未被前一 handler 改写


# =====================================================================
# EventBus — emit_ordered 背压(F005)
# =====================================================================
async def test_emit_ordered_unregistered_rejected():
    """emit_ordered 前置类型校验:未注册 → EVT-102,队列不受污染。"""
    bus = EventBus()
    with pytest.raises(PyHError) as ei:
        await bus.emit_ordered("feeder", "ghost.event", {})
    assert ei.value.code == "EVT-102"
    assert bus._queues == {} and bus.dropped == {}       # 无脏队列/计数


async def test_emit_ordered_fifo_strict():
    """同 sender 并发入队:严格 FIFO 送达,首个调用方兼 drainer 汇总。"""
    bus = EventBus()
    seen = []
    bus.register_type("plugin.echo.ping", PingPayload)

    async def rec(t, p):                                 # noqa: ARG001
        await asyncio.sleep(0.02)                        # 拉长 drainer 挂起窗,
        seen.append(p["seq"])                            # 保证各生产者入队在其间完成

    bus.subscribe("plugin.echo.ping", rec)

    async def prod(i):
        return await bus.emit_ordered("feeder", "plugin.echo.ping", {"seq": i})

    results = await asyncio.gather(*(prod(i) for i in range(1, 6)))
    assert seen == [1, 2, 3, 4, 5]                       # FIFO 不乱序
    assert sum(r.get("delivered", 0) for r in results) == 5
    assert sum(1 for r in results if r.get("delivered") == 5) == 1
    assert all(r.get("queued") is True for r in results if r.get("delivered") == 0)


async def test_emit_ordered_backpressure_drop():
    """背压(F005):队列满 = 拒新不丢旧;dropped 计数 + bus.backpressure 可观测。"""
    bus = EventBus(backpressure_limit=5)
    seen = []
    bp_events = []

    async def slow(t, p):                                # noqa: ARG001
        await asyncio.sleep(0.3)                         # 拉开入队窗口
        seen.append(p["seq"])

    bus.subscribe("plugin.echo.ping", slow)
    bus.subscribe("bus.backpressure", lambda p: bp_events.append(p))
    bus.register_type("plugin.echo.ping", PingPayload)

    async def prod(i):
        return await bus.emit_ordered("feeder", "plugin.echo.ping", {"seq": i})

    results = await asyncio.gather(*(prod(i) for i in range(1, 8)))  # 7 件,上限 5
    assert seen == [1, 2, 3, 4, 5, 6]                    # 旧件全达(不丢旧)
    assert bus.dropped == {"feeder": 1}                  # 1 件被拒(拒新)
    assert sum(r.get("delivered", 0) for r in results) == 6
    dropped_call = [r for r in results if r.get("dropped") is True]
    assert len(dropped_call) == 1                        # 被拒调用返回 dropped 旗标
    assert bp_events == [{"sender": "feeder", "dropped": 1}]  # 背压事件可观测
    assert len(bus._queues["feeder"]) == 0               # 队列已排空(旧件全达)


async def test_emit_ordered_default_limit_1000():
    """F005/PARAMETER-ANCHOR:默认背压阈值 1000;构造可配。"""
    assert EventBus().backpressure_limit == 1000
    assert EventBus(backpressure_limit=3).backpressure_limit == 3
    # 阈值判据与配置同源:len(q) >= limit 即拒新
    bus = EventBus(backpressure_limit=0)
    stats = await bus.emit_ordered("s1", "user.message", {"content": "x"})
    assert stats.get("dropped") is True and bus.dropped == {"s1": 1}


# =====================================================================
# Registry — 三类索引(F003)
# =====================================================================
def _reg_with_recorder():
    """EventBus+Registry + registry.updated 记录器(同步订阅者)。"""
    bus = EventBus()
    updates = []
    bus.subscribe("registry.updated", lambda p: updates.append(p))
    return Registry(bus), updates


def test_registry_register_lookup_roundtrip():
    """plugin/tool/capability 三类索引写入与查询往返。"""
    reg, _ = _reg_with_recorder()
    plugin_obj, tool_obj, cap_obj = object(), object(), object()
    reg.register("plugin", "echo", plugin_obj)
    reg.register("tool", "fs.read", tool_obj)
    reg.register("capability", "ctx.echo", cap_obj)
    assert reg.lookup("plugin", "echo") is plugin_obj
    assert reg.lookup("tool", "fs.read") is tool_obj
    assert reg.lookup("capability", "ctx.echo") is cap_obj


def test_registry_duplicate_rejected_tlb801():
    """同 kind 同 key 二次注册 → TLB-801,原值不动、无脏留痕。"""
    reg, updates = _reg_with_recorder()
    a, b = object(), object()
    reg.register("plugin", "echo", a)
    with pytest.raises(PyHError) as ei:
        reg.register("plugin", "echo", b)
    assert ei.value.code == "TLB-801"
    assert reg.lookup("plugin", "echo") is a            # 未被覆盖
    assert [u["op"] for u in updates] == ["add"]        # 重复注册无第二次留痕
    # 不同 kind 同名键不冲突
    reg.register("tool", "echo", b)
    assert reg.lookup("tool", "echo") is b


def test_registry_kind_invalid_rejected():
    """kind 越界 → TLB-801(非法注册),禁裸 KeyError。"""
    reg, _ = _reg_with_recorder()
    with pytest.raises(PyHError) as ei:
        reg.register("spine", "x", object())
    assert ei.value.code == "TLB-801"


def test_registry_reserved_plugin_names_bus002():
    """plugin 保留名(脊柱八模块+子系统名)→ BUS-002,注册表不被污染。"""
    reg, updates = _reg_with_recorder()
    reserved = ("agent-loop", "agent", "session", "llm", "tools", "scope",
                "persistence", "system-prompt", "system_prompt",
                "guard", "approval", "credentials")
    for name in reserved:
        with pytest.raises(PyHError) as ei:
            reg.register("plugin", name, object())
        assert ei.value.code == "BUS-002", name
    assert updates == []                                 # 拒绝零留痕


def test_registry_lookup_missing_raises():
    """缺失 key 查询不静默:tool → TLB-802;plugin/capability → TLB-801。"""
    reg, _ = _reg_with_recorder()
    with pytest.raises(PyHError) as ei:
        reg.lookup("tool", "no.such.tool")
    assert ei.value.code == "TLB-802"
    with pytest.raises(PyHError) as ei:
        reg.lookup("plugin", "no.such.plugin")
    assert ei.value.code == "TLB-801"
    with pytest.raises(PyHError) as ei:
        reg.lookup("capability", "no.such.cap")
    assert ei.value.code == "TLB-801"


def test_registry_unregister_and_events():
    """注销 → 摘除 + op=del 留痕;再查询/再注销不静默。"""
    reg, updates = _reg_with_recorder()
    reg.register("plugin", "echo", object())
    reg.register("tool", "fs.read", object())
    reg.unregister("plugin", "echo")
    with pytest.raises(PyHError):                        # 摘后查询必失败
        reg.lookup("plugin", "echo")
    with pytest.raises(PyHError) as ei:                  # 重复注销不静默
        reg.unregister("plugin", "echo")
    assert ei.value.code == "TLB-801"
    assert "unregister-unknown" in str(ei.value.ctx.get("detail", ""))
    ops = [(u["op"], u["kind"], u["key"]) for u in updates]
    assert ("add", "plugin", "echo") in ops
    assert ("del", "plugin", "echo") in ops
    assert ("add", "tool", "fs.read") in ops
    assert reg.lookup("tool", "fs.read") is not None     # 未误伤他键


def test_registry_updated_broadcast_payload():
    """registry.updated 瞬时留痕载荷 {op,kind,key}(F003)。"""
    reg, updates = _reg_with_recorder()
    reg.register("capability", "ctx.echo", object())
    reg.unregister("capability", "ctx.echo")
    assert updates == [{"op": "add", "kind": "capability", "key": "ctx.echo"},
                       {"op": "del", "kind": "capability", "key": "ctx.echo"}]


# =====================================================================
# PluginManager — 安装与校验(F006)
# =====================================================================
def _pm() -> tuple:
    """独立 bus+registry+manager 三元组(每测试隔离)。"""
    bus = EventBus()
    reg = Registry(bus)
    pm = PluginManager(registry=reg)
    return bus, reg, pm


async def test_pm_install_registers_and_appends():
    """install:状态 installed、registry 有插件记录、plugin.installed 留痕。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    await pm.install(_mk_manifest("echo"), ctx)
    assert pm.state("echo") == "installed"
    record = reg.lookup("plugin", "echo")
    assert record.id == "echo" and record.state == "installed"
    assert ctx.session.events == [
        ("plugin.installed", {"plugin_id": "echo", "version": "0.1.0",
                              "api_version": "1"})]


async def test_pm_install_reserved_bus002():
    """插件 id = 保留名 → BUS-002;无注册残留、无留痕。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    with pytest.raises(PyHError) as ei:
        await pm.install(_mk_manifest("agent"), ctx)
    assert ei.value.code == "BUS-002"
    assert pm.state("agent") == "absent"
    assert ctx.session.events == []


async def test_pm_install_api_mismatch_bus003():
    """api_version 不匹配 → BUS-003 拒装载。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    with pytest.raises(PyHError) as ei:
        await pm.install(_mk_manifest("echo", api_version="2"), ctx)
    assert ei.value.code == "BUS-003"


async def test_pm_install_missing_dep_bus003():
    """依赖缺失 → BUS-003(先装依赖),无半装态。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    with pytest.raises(PyHError) as ei:
        await pm.install(_mk_manifest("app", requires=["ghost-dep"]), ctx)
    assert ei.value.code == "BUS-003"
    assert pm.state("app") == "absent"


async def test_pm_install_self_require_cycle_bus003():
    """自依赖/循环依赖 → BUS-003 检出。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    with pytest.raises(PyHError) as ei:
        await pm.install(_mk_manifest("loop", requires=["loop"]), ctx)
    assert ei.value.code == "BUS-003"
    assert "循环依赖" in str(ei.value.ctx.get("detail", ""))


async def test_pm_install_dep_must_be_active():
    """依赖须 active 才能被依赖(BUS-003);activate 依赖后可装。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    await pm.install(_mk_manifest("base"), ctx)          # 装了但未激活
    with pytest.raises(PyHError) as ei:
        await pm.install(_mk_manifest("app", requires=["base"]), ctx)
    assert ei.value.code == "BUS-003"
    await pm.activate("base", ctx)
    await pm.install(_mk_manifest("app", requires=["base"]), ctx)
    assert pm.state("app") == "installed"


async def test_pm_illegal_state_transitions_bus003():
    """非法状态迁移 → BUS-003:未知激活/装后即卸(须先激活)/二次激活/二次停用。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    # absent → activating 非法
    with pytest.raises(PyHError) as ei:
        await pm.activate("ghost", ctx)
    assert ei.value.code == "BUS-003"
    await pm.install(_mk_manifest("echo"), ctx)
    # installed → deactivating 非法(须先 activate)
    with pytest.raises(PyHError) as ei:
        await pm.deactivate("echo", ctx)
    assert ei.value.code == "BUS-003"
    await pm.activate("echo", ctx)
    # active → activating 非法(二次激活)
    with pytest.raises(PyHError) as ei:
        await pm.activate("echo", ctx)
    assert ei.value.code == "BUS-003"
    await pm.deactivate("echo", ctx)
    # inactive → deactivating 非法(二次停用)
    with pytest.raises(PyHError) as ei:
        await pm.deactivate("echo", ctx)
    assert ei.value.code == "BUS-003"


# =====================================================================
# PluginManager — 激活/停用/热卸载(F004)
# =====================================================================
def _cap_echo(calls: list, tool_obj: object = None) -> Capability:
    """echo 插件能力:tool 键 + plugin.echo.ping 订阅(owner=插件 id)。"""
    def sub_h(t, p):                                     # noqa: ARG001
        calls.append(p)

    return Capability(id="cap:echo.ping", tool_keys=["echo.ping"],
                      tool=tool_obj or object(),
                      subscriptions=[("plugin.echo.ping", sub_h)])


async def test_pm_activate_deactivate_lifecycle():
    """activate:tool/capability 双键注册 + 订阅装载;deactivate:逆序摘净。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    calls = []
    await pm.install(_mk_manifest("echo", capabilities=[_cap_echo(calls)]), ctx)
    await pm.activate("echo", ctx)
    assert pm.state("echo") == "active"
    assert reg.lookup("tool", "echo.ping") is not None   # tool 键已注册
    assert reg.lookup("capability", "cap:echo.ping") is not None
    # 订阅生效(插件激活期间事件可达)
    bus.register_type("plugin.echo.ping", PingPayload)
    await bus.emit("plugin.echo.ping", {"msg": "hi"})
    assert calls == [{"msg": "hi"}]
    # deactivate:摘订阅 + 注销工具/能力,插件记录保留
    await pm.deactivate("echo", ctx)
    assert pm.state("echo") == "inactive"
    with pytest.raises(PyHError):
        reg.lookup("tool", "echo.ping")
    with pytest.raises(PyHError):
        reg.lookup("capability", "cap:echo.ping")
    calls.clear()
    await bus.emit("plugin.echo.ping", {"msg": "again"})
    assert calls == []                                   # 订阅已摘净
    assert reg.lookup("plugin", "echo").state == "inactive"  # 记录仍在(可重激活)


async def test_pm_reactivate_after_deactivate():
    """inactive → activating → active 重激活合法(F006 inactive→activating)。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    calls = []
    await pm.install(_mk_manifest("echo", capabilities=[_cap_echo(calls)]), ctx)
    await pm.activate("echo", ctx)
    await pm.deactivate("echo", ctx)
    await pm.activate("echo", ctx)
    assert pm.state("echo") == "active"
    bus.register_type("plugin.echo.ping", PingPayload)
    await bus.emit("plugin.echo.ping", {"msg": "x"})
    assert calls == [{"msg": "x"}]


async def test_pm_activate_failure_rollback():
    """activate 失败(entry 导入失败)回滚 installed,不留半激活态/脏注册。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    manifest = _mk_manifest("broken",
                            entry="pyharness.bus.__no_such_plugin_module",
                            capabilities=[_cap_echo([])])
    await pm.install(manifest, ctx)
    with pytest.raises(ModuleNotFoundError):
        await pm.activate("broken", ctx)
    assert pm.state("broken") == "installed"             # 回滚,非半激活
    with pytest.raises(PyHError):                        # 无脏 tool 键
        reg.lookup("tool", "echo.ping")


async def test_pm_uninstall_requires_deactivate():
    """active 卸载 → BusyUninstall(BUSY 语义);deactivate 后卸载成功。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    await pm.install(_mk_manifest("echo", capabilities=[_cap_echo([])]), ctx)
    await pm.activate("echo", ctx)
    with pytest.raises(B.BusyUninstall) as ei:           # 运行中拒绝
        await pm.uninstall("echo", ctx)
    assert ei.value.pid == "echo"
    assert pm.state("echo") == "active"                  # 拒绝不改状态
    await pm.deactivate("echo", ctx)
    await pm.uninstall("echo", ctx)
    assert pm.state("echo") == "absent"                  # 记录摘除
    with pytest.raises(PyHError):                        # 二次卸载不静默
        await pm.uninstall("echo", ctx)
    assert ctx.session.events[0][0] == "plugin.installed"
    assert ctx.session.events[-1] == ("plugin.uninstalled", {"plugin_id": "echo"})


async def test_pm_uninstall_never_activated():
    """装后未激活即卸载(installed → uninstalled 直通)可执行。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    await pm.install(_mk_manifest("echo"), ctx)
    await pm.uninstall("echo", ctx)
    assert pm.state("echo") == "absent"
    assert ctx.session.events[-1] == ("plugin.uninstalled", {"plugin_id": "echo"})


async def test_pm_reinstall_after_uninstall():
    """热插拔:F004 重装——uninstall 后同 id 可再 install/activate。"""
    bus, reg, pm = _pm()
    ctx = _FakeCtx()
    calls = []
    await pm.install(_mk_manifest("echo", capabilities=[_cap_echo(calls)]), ctx)
    await pm.activate("echo", ctx)
    await pm.deactivate("echo", ctx)
    await pm.uninstall("echo", ctx)
    await pm.install(_mk_manifest("echo", capabilities=[_cap_echo(calls)]), ctx)
    await pm.activate("echo", ctx)
    assert pm.state("echo") == "active"
    bus.register_type("plugin.echo.ping", PingPayload)
    await bus.emit("plugin.echo.ping", {"msg": "x"})
    assert calls == [{"msg": "x"}]


# =====================================================================
# PluginManager — emit_as(插件身份发事件)
# =====================================================================
async def test_pm_emit_as_injects_origin():
    """emit_as 自动补 origin=plugin:<id>;类型未注册 → EVT-102。"""
    bus, reg, pm = _pm()
    got = []
    bus.register_type("plugin.echo.ping", PingPayload)
    bus.subscribe("plugin.echo.ping", lambda t, p: got.append(p))
    stats = await pm.emit_as("echo", "plugin.echo.ping", {"msg": "hi"})
    assert stats["delivered"] == 1
    assert got == [{"msg": "hi", "origin": "plugin:echo"}]
    with pytest.raises(PyHError) as ei:
        await pm.emit_as("echo", "plugin.ghost.fire", {})
    assert ei.value.code == "EVT-102"


# =====================================================================
# 常量与导出
# =====================================================================
def test_bus_exports_and_stop_sentinel():
    """包导出面完整;STOP 为模块级哨兵(身份比较恒稳)。"""
    for name in ("EventBus", "Subscription", "Registry", "PluginManager",
                 "PluginHost", "Capability", "STOP", "API_VERSION"):
        assert hasattr(B, name), name
    assert repr(STOP) == "<STOP>"
    assert STOP is STOP
