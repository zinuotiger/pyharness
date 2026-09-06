"""llm_fallback 模块单测 — 契约:specs/llm_fallback.py.md + DIS-CORE §4.3/§4.4 + CFG §3.1/§3.5

覆盖面(任务要求全项 + spec 关联):
    F013 降级链:LLM-302 直降备用(INV-07 载体:证明降级真发生)、LLM-303(exhausted)
        耗尽降、LLM-304 不降直抛、双败 → LLM-310(err_hist)、降级后 idx 前移(后续
        请求直上备用)、单会话降级 >5 次告警(system.error)、degrade.enabled=false
        主败即 LLM-310、连续限流阈值判定(2 次降 / 1 次不降)
    F028 退避:429×3 后成功 → llm.retry 3 条且 delay 1/2/4s±30%;耗尽 → LLM-303
        交降级;每次重试前 BudgetGuard 复查(预算中途耗尽即停,不烧钱)
    F033 探针:3 败 down / 1 败仅 degraded / 2 好回切 + llm.recovered、pick 跳过
        down、主模型 down 不跳(保底尝试)、探针回切 idx 归零(主模型恢复自动回切)
    F032 BudgetGuard:超限 → BUDGET-EXHAUSTED + budget.paused 落盘;80% → warn +
        budget.warn 事件;正常 ok;report 汇总

LLM/计数器/会话/总线全为注入替身——本模块不触真实 API、不落真实日志(spec:主备
Provider 注入式,测试全 mock)。错误码断言走 errors 域类(LLMError)。
"""
import asyncio
import logging
from types import SimpleNamespace

import pytest

from pyharness.config import Settings
from pyharness.core.llm_fallback import (AdapterHealth, BudgetGuard,
                                         FallbackChain, TaskUsage)
from pyharness.errors import LLMError, PyHError

log = logging.getLogger(__name__)

MAIN = "deepseek-chat"
BACKUP = "qwen-max"


# ===================================================================== 替身
def _cfg(*, llm=None, budget=None) -> Settings:
    """Settings 快照:只覆盖给定子键,其余走 L1 默认(键缺省必可加载)。"""
    kw = {}
    if llm is not None:
        kw["llm"] = llm
    if budget is not None:
        kw["budget"] = budget
    return Settings(**kw)


def _err(code: str, **ctx) -> PyHError:
    """直接构造 PyHError 模拟适配器上抛(生产侧由 llm.py 经 raise_code 归一)。"""
    return PyHError(code, ctx=ctx)


class RecSession:
    """会话事件记录替身:append 记录 (type/payload/actor),不落盘不校验。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, type_, payload, *, actor, **kw):
        self.events.append({"type": type_, "payload": payload, "actor": actor})
        return SimpleNamespace(seq=len(self.events))

    def of(self, type_: str) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


class FakeCounters:
    """UsageCounters 替身:task_total 可逐步脚本化(usage_seq)以模拟中途超限。"""

    def __init__(self, usage: TaskUsage = None, *, usage_seq=None,
                 streak: int = 0) -> None:
        self.usage = usage or TaskUsage()
        self.usage_seq = list(usage_seq or [])
        self.streak = streak
        self.degrade_log: list[tuple[str, str]] = []

    def task_total(self) -> TaskUsage:
        if self.usage_seq:
            return self.usage_seq.pop(0)
        return self.usage

    def rate_limit_streak(self) -> int:
        return self.streak

    def degrade_add(self, failed: str, to: str) -> int:
        self.degrade_log.append((failed, to))
        return len(self.degrade_log)


class RecBus:
    """同步假总线:emit 直接记录(type, payload)(真实 EventBus.emit 返回协程,
    本假总线模拟同步消费,便于断言;fire-and-forget 逻辑在 _fire_emit 已覆盖)。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, type_: str, payload: dict):
        self.events.append((type_, payload))

    def of(self, type_: str) -> list[tuple[str, dict]]:
        return [e for e in self.events if e[0] == type_]


class FakeAdapter:
    """可脚本化适配器替身(LLMAdapter 契约)。

    chat_script: 依序弹出;元素为异常 → 上抛,否则原样返回(响应对象);空 → 返回
        resp 或(always 非 None 时)恒抛 always(模拟持续 401 等)。
    ping_results: 依序弹出;异常 → 探针失败;空 → 0.01。
    """

    def __init__(self, *, chat_script=None, always: Exception = None,
                 ping_results=None, resp=None, name: str = "mock") -> None:
        self.chat_script = list(chat_script or [])
        self.always = always
        self.ping_results = list(ping_results or [])
        self.resp = resp or SimpleNamespace(
            content=f"resp-{name}", tool_calls=None, model=name)
        self.chats: list[tuple] = []
        self.pings: int = 0

    async def chat(self, messages, tools=None, *, ctx):
        self.chats.append((list(messages), tools))
        if self.chat_script:
            item = self.chat_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.always is not None:
            raise self.always
        return self.resp

    async def ping(self) -> float:
        self.pings += 1
        if self.ping_results:
            r = self.ping_results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return 0.01


def _ctx(session: RecSession = None, counters: FakeCounters = None,
         bus: RecBus = None, config: Settings = None) -> SimpleNamespace:
    """会话实体替身:config/session/counters/bus 四要素(spec ctx 契约)。"""
    return SimpleNamespace(session=session or RecSession(),
                           counters=counters or FakeCounters(),
                           bus=bus,
                           config=config or _cfg())


def _chain(adapters: dict, config: Settings = None, *,
           bus: RecBus = None) -> FallbackChain:
    """装配 FallbackChain:chain 按 config 拼装(主 + fallback_models)。"""
    return FallbackChain(adapters=adapters, config=config or _cfg(), bus=bus)


def _rig(adapters: dict, *, llm=None, budget=None, bus: RecBus = None,
         idx: int = 0) -> tuple:
    """装配共享同一 Settings 的 (config, chain, ctx) 三件套。

    请求路径只读 ctx.config(attempts/enabled/预算),chain.config 仅用于拼链名——
    两者必须同源,否则定制参数(如 retry.attempts=2)会因 ctx 回落默认而错位。
    """
    cfg = _cfg(llm=llm, budget=budget)
    chain = _chain(adapters, config=cfg, bus=bus)
    chain.idx = idx
    return cfg, chain, _ctx(config=cfg)


async def _wait_for(predicate, timeout_s: float = 5.0) -> None:
    """忙等谓词成立(测试同步点;asyncio 单线程,让步调度即可)。"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() > deadline:
            raise TimeoutError("等待超时")
        await asyncio.sleep(0)


# ===================================================================== F013 降级链
async def test_302_direct_degrade_happens():
    """GWT-L4-02 前半:主模型 302(认证)→ 直降备用,备用收到同一请求体并返回
    ——降级真的发生(INV-07 载体:主 5xx/401 后请求走备用)。"""
    main = FakeAdapter(always=_err("LLM-302"))
    backup = FakeAdapter(name=BACKUP)
    ctx = _ctx()
    chain = _chain({MAIN: main, BACKUP: backup}, bus=RecBus())

    msgs = [{"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "mock_tool"}}]
    resp = await chain.chat_with_fallback(msgs, tools=tools, ctx=ctx)

    # 备用真正被调用且收到同一请求体 → 降级发生
    assert len(main.chats) == 1 and len(backup.chats) == 1
    assert backup.chats[0][0] == msgs and backup.chats[0][1] is tools
    assert resp.content == f"resp-{BACKUP}"
    assert chain.idx == 1                              # idx 前移:后续请求从备用起
    assert ctx.counters.degrade_log == [(MAIN, BACKUP)]  # 降级留痕
    assert not ctx.session.of("llm.retry")             # 302 不重试
    assert chain.bus.of("system.error") == []          # 降级 1 次 < 5:无告警


async def test_302_degrade_persists_next_request_starts_backup():
    """降级结果对后续请求生效:第 2 次请求直上备用,主模型不再被尝试(职责 3)。"""
    main = FakeAdapter(always=_err("LLM-302"))
    backup = FakeAdapter(name=BACKUP)
    ctx = _ctx()
    chain = _chain({MAIN: main, BACKUP: backup})

    await chain.chat_with_fallback([{"role": "user", "content": "q1"}], ctx=ctx)
    assert chain.idx == 1
    resp2 = await chain.chat_with_fallback(
        [{"role": "user", "content": "q2"}], ctx=ctx)

    assert len(main.chats) == 1                        # 第 2 次不再打主模型
    assert len(backup.chats) == 2
    assert resp2.content == f"resp-{BACKUP}"
    assert len(ctx.counters.degrade_log) == 1          # 不再重复降级


async def test_303_exhausted_degrades_to_backup():
    """429(303)×attempts 耗尽 → LLM-303(exhausted)交降级 → 备用接管。"""
    main = FakeAdapter(chat_script=[_err("LLM-303"), _err("LLM-303")])
    backup = FakeAdapter(name=BACKUP)
    _, chain, ctx = _rig({MAIN: main, BACKUP: backup},
                         llm={"retry": {"attempts": 2}})

    resp = await chain.chat_with_fallback(
        [{"role": "user", "content": "hi"}], ctx=ctx)

    assert chain.idx == 1
    assert len(main.chats) == 2                        # 退避 2 次尝试后耗尽
    assert len(backup.chats) == 1
    assert resp.content == f"resp-{BACKUP}"
    assert ctx.counters.degrade_log == [(MAIN, BACKUP)]
    retries = ctx.session.of("llm.retry")
    assert len(retries) == 1 and retries[0]["payload"]["attempt"] == 0


async def test_304_business_error_no_degrade_raise():
    """LLM-304(参数类/业务错):不重试不降级,直接上抛;idx 不动、备用零调用。"""
    main = FakeAdapter(always=_err("LLM-304", detail="bad request"))
    backup = FakeAdapter(name=BACKUP)
    ctx = _ctx()
    chain = _chain({MAIN: main, BACKUP: backup})

    with pytest.raises(PyHError) as ei:
        await chain.chat_with_fallback(
            [{"role": "user", "content": "hi"}], ctx=ctx)

    assert ei.value.code == "LLM-304"
    assert chain.idx == 0
    assert len(main.chats) == 1                        # 单次尝试,无退避
    assert len(backup.chats) == 0
    assert not ctx.session.of("llm.retry")
    assert ctx.counters.degrade_log == []


async def test_double_fail_raises_llm310_with_err_hist():
    """GWT-L4-02 双败:主 302 + 备用 303 耗尽 → LLM-310 终态,err_hist 携带全链失败史。"""
    main = FakeAdapter(always=_err("LLM-302"))
    backup = FakeAdapter(chat_script=[_err("LLM-303"), _err("LLM-303")])
    _, chain, ctx = _rig({MAIN: main, BACKUP: backup},
                         llm={"retry": {"attempts": 2}})

    with pytest.raises(PyHError) as ei:
        await chain.chat_with_fallback(
            [{"role": "user", "content": "hi"}], ctx=ctx)

    assert ei.value.code == "LLM-310"
    assert ei.value.ctx["err_hist"] == [
        (MAIN, "LLM-302"), (BACKUP, "LLM-303")]
    assert len(main.chats) == 1 and len(backup.chats) == 2


async def test_degrade_disabled_main_fail_is_llm310():
    """CFG llm.degrade.enabled=false:降级总开关关 → 主模型败即 LLM-310,不动备用。"""
    main = FakeAdapter(always=_err("LLM-302"))
    backup = FakeAdapter(name=BACKUP)
    _, chain, ctx = _rig({MAIN: main, BACKUP: backup},
                         llm={"degrade": {"enabled": False}})

    with pytest.raises(PyHError) as ei:
        await chain.chat_with_fallback(
            [{"role": "user", "content": "hi"}], ctx=ctx)

    assert ei.value.code == "LLM-310"
    assert len(main.chats) == 1 and len(backup.chats) == 0
    assert chain.idx == 0 and ctx.counters.degrade_log == []


async def test_llm310_probe_down_backup_skipped():
    """探针 down 的备用被降级循环跳过(不浪费请求):主 302 → 备用 down → LLM-310。"""
    main = FakeAdapter(always=_err("LLM-302"))
    backup = FakeAdapter(name=BACKUP)
    ctx = _ctx()
    chain = _chain({MAIN: main, BACKUP: backup})
    for _ in range(3):
        chain.health[BACKUP].mark(False)               # 探针 3 败 → down
    assert chain.health[BACKUP].is_down()

    with pytest.raises(PyHError) as ei:
        await chain.chat_with_fallback(
            [{"role": "user", "content": "hi"}], ctx=ctx)

    assert ei.value.code == "LLM-310"
    assert len(backup.chats) == 0                      # down 者零请求
    assert ei.value.ctx["err_hist"] == [(MAIN, "LLM-302")]


async def test_may_degrade_threshold_two_not_one():
    """test_f013 关联:连续限流达阈值(默认 2)才降级,1 次不降(302 无条件降)。"""
    chain = _chain({MAIN: FakeAdapter(), BACKUP: FakeAdapter()})
    e302 = _err("LLM-302")
    e303 = _err("LLM-303")
    e303x = _err("LLM-303", exhausted=True)

    assert chain._may_degrade(e302, _ctx()) is True    # 认证错:无条件降
    assert chain._may_degrade(e303x, _ctx()) is True   # 退避耗尽:降
    assert chain._may_degrade(e303, _ctx(counters=FakeCounters(streak=1))) is False
    assert chain._may_degrade(e303, _ctx(counters=FakeCounters(streak=2))) is True
    assert chain._may_degrade(_err("LLM-304"), _ctx()) is False
    assert chain._may_degrade(_err("LLM-301"), _ctx()) is False


async def test_degrade_alert_after_5_per_session():
    """test_f013 关联:单会话降级 >5 次 → system.error 告警事件(第 6 次起)。"""
    chain = _chain({MAIN: FakeAdapter(), BACKUP: FakeAdapter()}, bus=RecBus())
    ctx = _ctx()
    for i in range(5):
        chain._record_degradation(MAIN, BACKUP, ctx)
    assert chain.bus.of("system.error") == []          # 5 次内不告警

    chain._record_degradation(MAIN, BACKUP, ctx)       # 第 6 次 → 告警
    alerts = chain.bus.of("system.error")
    assert len(alerts) == 1
    assert alerts[0][1]["code"] == "LLM-310"
    assert "6" in alerts[0][1]["advice"]


# ===================================================================== F028 退避
async def _patch_deterministic_backoff(monkeypatch, recorded: list) -> None:
    """退避确定性替身:asyncio.sleep 记录延迟秒数并立即返回;抖动系数固定 1.0
    (去抖动,让断言拿到名义延迟 1/2/4s)。"""
    async def _fake(delay):
        recorded.append(delay)

    monkeypatch.setattr("pyharness.core.llm_fallback.asyncio.sleep", _fake)
    monkeypatch.setattr("pyharness.core.llm_fallback.random.uniform",
                        lambda a, b: 1.0)


async def test_retry_429x3_then_success_delays_1_2_4(monkeypatch):
    """GWT-L4-03:429×3 后成功 → llm.retry 3 条,delay 递增 1/2/4s(±30% 已去抖动)。"""
    sleeps: list[float] = []
    await _patch_deterministic_backoff(monkeypatch, sleeps)
    main = FakeAdapter(name=MAIN, chat_script=[_err("LLM-303"), _err("LLM-303"),
                                               _err("LLM-303")])   # 3 败后成功
    backup = FakeAdapter(name=BACKUP)
    _, chain, ctx = _rig({MAIN: main, BACKUP: backup})   # attempts 默认 4

    resp = await chain.chat_with_fallback(
        [{"role": "user", "content": "hi"}], ctx=ctx)

    assert resp.content == f"resp-{MAIN}"               # 退避后主模型成功,未降级
    assert chain.idx == 0 and len(backup.chats) == 0
    assert sleeps == [1.0, 2.0, 4.0]                    # 基数 1s ×2
    retries = ctx.session.of("llm.retry")
    assert len(retries) == 3
    assert [r["payload"]["attempt"] for r in retries] == [0, 1, 2]
    assert [r["payload"]["delay_ms"] for r in retries] == [1000, 2000, 4000]
    assert all(r["actor"] == "llm" for r in retries)
    assert len(main.chats) == 4                         # 3 败 + 1 成功


async def test_retry_delay_jitter_bounds(monkeypatch):
    """抖动 ±30% 边界:真实 random 下每次 sleep ∈ [0.7d, 1.3d](F028,1s × ±30%)。"""
    sleeps: list[float] = []
    async def _fake(delay):                     # 只 patch sleep:抖动保持真实
        sleeps.append(delay)

    monkeypatch.setattr("pyharness.core.llm_fallback.asyncio.sleep", _fake)
    main = FakeAdapter(name=MAIN, chat_script=[_err("LLM-303")])  # 1 败后成功
    _, chain, ctx = _rig({MAIN: main, BACKUP: FakeAdapter(name=BACKUP)},
                         llm={"retry": {"attempts": 2}})

    await chain.chat_with_fallback([{"role": "user", "content": "hi"}],
                                   ctx=ctx)

    assert len(sleeps) == 1
    assert 0.7 <= sleeps[0] <= 1.3
    assert len(main.chats) == 2                         # 1 败 + 1 成功


async def test_budget_recheck_during_retry_stops_burn():
    """防烧钱:重试前 BudgetGuard 复查,预算中途耗尽 → 即停(BUDGET-EXHAUSTED),
    不进入下一次请求(第 3 次 check 超限)。"""
    main = FakeAdapter(chat_script=[_err("LLM-303"), _err("LLM-303")])
    ctx = _ctx(counters=FakeCounters(
        usage_seq=[TaskUsage(), TaskUsage(),
                   TaskUsage(out_tokens=100)]),         # 前 2 次 ok,重试前 exhausted
        config=_cfg(llm={"retry": {"attempts": 2}},
                    budget={"task": {"max_out_tokens": 100}}))
    chain = _chain({MAIN: main, BACKUP: FakeAdapter(name=BACKUP)})

    with pytest.raises(PyHError) as ei:
        await chain.chat_with_fallback(
            [{"role": "user", "content": "hi"}], ctx=ctx)

    assert ei.value.code == "BUDGET-EXHAUSTED"
    assert len(main.chats) == 1                         # 第 2 次尝试被预算闸拦下
    paused = ctx.session.of("budget.paused")
    assert len(paused) == 1 and paused[0]["payload"]["state"] == "exhausted"


# ===================================================================== F033 探针
async def test_adapter_health_machine_3_fail_down_2_ok_recover():
    """test_f033_probe:连 3 败 down(防抖动)、连 2 好回切 healthy;1 败仅 degraded。"""
    h = AdapterHealth()
    assert h.state == "healthy" and not h.is_down()

    h.mark(False)
    assert h.state == "degraded"                        # 1 败仅 degraded
    assert h.fail_streak == 1 and not h.is_down()
    h.mark(False)
    assert h.state == "degraded"                        # 2 败仍未 down
    h.mark(False)
    assert h.state == "down" and h.is_down()            # 3 败 down
    h.mark(False)
    assert h.state == "down"                            # down 后继续败仍 down

    h.mark(True)
    assert h.state == "down"                            # 1 好未回切(防抖动)
    h.mark(True)
    assert h.state == "healthy" and not h.is_down()     # 2 好回切
    assert h.ok_streak == 2 and h.fail_streak == 0


async def test_adapter_health_degraded_single_ok_heals():
    """degraded(瞬时 1 败)单次成功即解除回 healthy(spec 伪码缺口补全,偏离 6)。"""
    h = AdapterHealth()
    h.mark(False)
    assert h.state == "degraded"
    h.mark(True)
    assert h.state == "healthy"


async def test_pick_skips_down_keeps_main_when_all_down():
    """pick():跳过探针 down 的备用;主模型 down 不跳(保底尝试);全 down → idx 元素。"""
    chain = _chain({MAIN: FakeAdapter(), BACKUP: FakeAdapter()})
    assert chain.pick() == MAIN

    for _ in range(3):
        chain.health[BACKUP].mark(False)               # 备用 down
    assert chain.pick() == MAIN                        # 跳过 down 备用

    for _ in range(3):
        chain.health[MAIN].mark(False)                 # 主也 down
    assert chain.pick() == MAIN                        # 全 down:仍走 idx 当前元素


async def test_probe_loop_marks_and_recovers(monkeypatch):
    """探针周期驱动:3 败 → down、2 好 → healthy + llm.recovered;不计用量不落事件。"""
    main = FakeAdapter(name=MAIN, ping_results=[
        _err("LLM-303"), _err("LLM-303"), _err("LLM-303"),  # 3 败 → down
        None, None])                                     # 2 好 → healthy(回切)
    backup = FakeAdapter(name=BACKUP)
    ctx = _ctx()
    chain = _chain({MAIN: main, BACKUP: backup}, bus=RecBus())

    task = asyncio.create_task(chain.probe_loop(interval_s=0.01))
    try:
        # 等 down→healthy 翻转完成(不能只等 healthy——初始就是 healthy,
        # 会立即返回导致 recovered 事件未及发出)
        await _wait_for(lambda: len(chain.bus.of("llm.recovered")) == 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert chain.health[MAIN].state == "healthy"
    assert chain.health[BACKUP].state == "healthy"      # 备用 ping 恒好
    # 恢复翻转 → llm.recovered 通知(尽力而为,仅一次)
    rec = chain.bus.of("llm.recovered")
    assert len(rec) == 1 and rec[0][1] == {"model": MAIN}
    # 探针不计 F029 用量、不落 llm.retry/任何会话事件
    assert ctx.session.events == []
    assert main.pings == 5 and backup.pings >= 1


async def test_probe_loop_timeout_marks_fail(monkeypatch):
    """探针 ping 超时(5s wait_for)同判失败(偏离 7):TimeoutError → mark(False),
    连续超时累计到 down。"""
    async def _always_timeout(coro, timeout=None):
        coro.close()                          # 关闭被吞的 ping 协程(防泄漏)
        await asyncio.sleep(0)
        raise asyncio.TimeoutError()

    monkeypatch.setattr("pyharness.core.llm_fallback.asyncio.wait_for",
                        _always_timeout)
    main = FakeAdapter(name=MAIN)                       # ping 本身不再被真正调用
    chain = _chain({MAIN: main, BACKUP: FakeAdapter(name=BACKUP)})

    task = asyncio.create_task(chain.probe_loop(interval_s=0.01))
    try:
        # 两适配器都需累计 3 败 down(每个周期先 main 后 backup)
        await _wait_for(lambda: (chain.health[MAIN].is_down()
                                 and chain.health[BACKUP].is_down()))
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert chain.health[MAIN].state == "down"
    assert chain.health[BACKUP].state == "down"         # 两适配器同判失败
    assert main.pings == 0                              # wait_for 外层即抛,ping 未执行


async def test_probe_recovery_resets_idx_to_main():
    """探针回切:降级态(idx=1)下主模型 3 败 down → 2 好 healthy → idx 归零,
    后续请求自动回主模型(职责 4 / DIS-CORE §4.4 恢复自动回切)。"""
    main = FakeAdapter(name=MAIN, ping_results=[_err("LLM-303")] * 3 + [None, None])
    backup = FakeAdapter(name=BACKUP)
    ctx = _ctx()
    chain = _chain({MAIN: main, BACKUP: backup}, bus=RecBus())
    chain.idx = 1                                       # 此前已降级到备用
    for _ in range(3):
        chain.health[MAIN].mark(False)                  # 主模型探针 down
    assert chain.pick() == BACKUP                       # down 的主模型被跳开

    task = asyncio.create_task(chain.probe_loop(interval_s=0.01))
    try:
        await _wait_for(lambda: chain.idx == 0)         # 2 好回切 → idx 归零
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert chain.health[MAIN].state == "healthy"
    # 回切后请求直走主模型(降级真的回切)
    resp = await chain.chat_with_fallback(
        [{"role": "user", "content": "hi"}], ctx=ctx)
    assert resp.content == f"resp-{MAIN}"
    assert len(main.chats) == 1 and len(backup.chats) == 0


# ===================================================================== F032 BudgetGuard
async def test_budget_ok_warn_exhausted_and_report():
    """F032:正常 ok;out 达 80% → warn + budget.warn 事件;达 100% → exhausted
    抛错 + budget.paused 落盘;report 汇总读数。"""
    cfg = _cfg(budget={"task": {"max_out_tokens": 100}})
    session = RecSession()

    # ok(10/100 < 80%)
    assert await BudgetGuard.check(_ctx(
        session=session, counters=FakeCounters(usage=TaskUsage(out_tokens=10)),
        config=cfg)) == "ok"
    assert session.events == []

    # warn(90/100 ≥ 80%):budget.warn 事件,不拦截
    bus = RecBus()
    assert await BudgetGuard.check(_ctx(
        session=session, counters=FakeCounters(usage=TaskUsage(out_tokens=90)),
        bus=bus, config=cfg)) == "warn"
    assert len(bus.of("budget.warn")) == 1
    assert bus.of("budget.warn")[0][1] == {"used_out": 90}
    assert session.of("budget.paused") == []           # warn 不落 paused

    # exhausted(100/100 ≥ 硬闸):BUDGET-EXHAUSTED + budget.paused(exhausted) 落盘
    with pytest.raises(PyHError) as ei:
        await BudgetGuard.check(_ctx(
            session=session, counters=FakeCounters(usage=TaskUsage(out_tokens=100)),
            bus=bus, config=cfg))
    assert ei.value.code == "BUDGET-EXHAUSTED"
    assert ei.value.ctx["used"] == {"in_tokens": 0, "out_tokens": 100,
                                    "cost_est": 0.0}
    paused = session.of("budget.paused")
    assert len(paused) == 1
    assert paused[0]["payload"]["state"] == "exhausted"
    assert paused[0]["payload"]["used"]["out_tokens"] == 100
    assert paused[0]["actor"] == "system"

    # 输入超限同样 exhausted(默认 max_in_tokens=200 万,用超默认值触发)
    with pytest.raises(PyHError):
        await BudgetGuard.check(_ctx(
            counters=FakeCounters(usage=TaskUsage(in_tokens=2_000_001)), config=cfg))
    with pytest.raises(PyHError):
        await BudgetGuard.check(_ctx(
            counters=FakeCounters(usage=TaskUsage(cost_est=2.0)), config=cfg))

    # report:读数汇总(used/limit/pct_out)
    rep = BudgetGuard.report(_ctx(
        counters=FakeCounters(usage=TaskUsage(out_tokens=50)), config=cfg))
    assert rep["used"]["out_tokens"] == 50
    assert rep["limit"]["max_out_tokens"] == 100
    assert rep["pct_out"] == 50.0


async def test_budget_check_gates_request_entry():
    """预算闸前置:请求进入即查——已超限的任务不发出任何请求(适配器零调用)。"""
    main = FakeAdapter(always=_err("LLM-302"))
    backup = FakeAdapter(name=BACKUP)
    ctx = _ctx(counters=FakeCounters(usage=TaskUsage(out_tokens=100)),
               config=_cfg(budget={"task": {"max_out_tokens": 100}}))
    chain = _chain({MAIN: main, BACKUP: backup})

    with pytest.raises(PyHError) as ei:
        await chain.chat_with_fallback(
            [{"role": "user", "content": "hi"}], ctx=ctx)

    assert ei.value.code == "BUDGET-EXHAUSTED"
    assert len(main.chats) == 0 and len(backup.chats) == 0
    assert chain.idx == 0
