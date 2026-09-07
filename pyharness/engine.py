"""pyharness/engine.py — 真实引擎装配(desktop/CLI 共用)。

一句话职责:从分层配置 Settings 装配可跑的真实引擎——store(JSONL 真源)→
SessionLog → AgentLoop(真 LLM 已注册)→ Scope(strict 最小权限)→
Registry → agent_loop runner seam(task_queue.run_for_task = 找 user.message
envelope → loop.wake),供桌面壳/交互 CLI 把"提问"真正变成 Agent 干活。

安全基线:scope 默认 strict(sandbox_level="strict" 禁高危工具);llm.api_key
走 secret-ref(env:/file:),绝不落盘明文;无 key 时降级明确报错不猜测。

偏离说明(契约以各 specs 为准,此处为装配层取舍):
1. 纯对话装配:默认不注册任何工具 Provider(registry 空)。DeepSeek 纯聊轮
   不返回 tool_calls → 不触发工具分支;若模型请求工具,registry 寻址失败 →
   TLB-801 事件留痕后回喂(模型可见"工具不可用",可自然改口)。演示场景
   足够,工具域 F014/F015 全链由 tests 覆盖,装配层不必为演示硬接。
2. Scope 预算:limits 从 cfg.budget 编译(BudgetLimits.from_cfg);会话级
   budget 闸(F032 计费)与 scope.check_budget(轮闸)在此不挂 Counters——
   会话级预算由 desktop 的 budget_dashboard 只读派生(llm.usage 折叠),
   不重复装配写侧(见 desktop.py.md 偏离 3)。
3. run_for_task 契约:task_queue.Task 有 .text/.seq/.meta;本装配从
   session.events_after() 找 seq 匹配的 user.message envelope 交 loop.wake。
   wake 内部 pending 入队 + idle 拉起排水 run——queue.submit 与 loop 之间
   的事件源即 JSONL 真源,无旁路队列(INV-01 append-only 不变式)。
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from pyharness.bus import EventBus
from pyharness.bus.registry import Registry
from pyharness.core.agent import create_agent
from pyharness.core.agent_loop import AgentLoop
from pyharness.core import llm as llm_mod
from pyharness.core.scope import BudgetLimits, Scope, ScopePolicy
from pyharness.core.session import open_session
from pyharness.errors import PyHError
from pyharness.persistence import open_store

log = logging.getLogger("pyharness.engine")


# ---------------------------------------------------------------- 适配器注册
def register_default_llm(cfg: Any, *, force: bool = False) -> str:
    """注册 cfg.llm.model 的真实适配器(OpenAI 兼容端点)。

    已注册同名模型 → 跳过(幂等;force=True 强制覆盖)。key 缺失 → 抛 LLM-304
    明确提示,不静默降级。返回注册的模型名。

    秘密纪律:api_key 是 "env:NAME"/"file:PATH" 引用字符串,值由适配器在
    secret_ref_ok 解析(CRED-701/702/703)——本函数不接触也不落盘明文。
    """
    llm_cfg = cfg.llm
    if llm_mod.adapters.get(llm_cfg.model) and not force:
        return llm_cfg.model
    if not str(llm_cfg.api_key or "").startswith(("env:", "file:")):
        llm_mod.raise_code(
            "CRED-701", model=llm_cfg.model,
            advice="llm.api_key 须 env:NAME / file:PATH 秘密引用(禁明文配置)")
    triple = llm_mod.AdapterTriple(base_url=str(llm_cfg.base_url),
                                   api_key_ref=str(llm_cfg.api_key),
                                   model=str(llm_cfg.model))
    llm_mod.register_adapter(
        llm_cfg.model,
        lambda: llm_mod.OpenAICompatAdapter(
            model=str(llm_cfg.model), triple=triple,
            limits=llm_mod.TimeoutLimits.from_cfg(llm_cfg.timeout),
            cfg=cfg))
    # 降级链模型(若有)一并注册同名适配器(同端点不同名;F028 语义由
    # llm_fallback 编排层驱动,装配层只保证可寻址)
    for m in getattr(llm_cfg, "fallback_models", None) or []:
        if m and not llm_mod.adapters.get(m):
            llm_mod.register_adapter(
                m, lambda m=m: llm_mod.OpenAICompatAdapter(
                    model=m, triple=llm_mod.AdapterTriple(
                        base_url=str(llm_cfg.base_url),
                        api_key_ref=str(llm_cfg.api_key),
                        model=m),
                    limits=llm_mod.TimeoutLimits.from_cfg(llm_cfg.timeout),
                    cfg=cfg))
    return str(llm_cfg.model)


# ---------------------------------------------------------------- spine 装配
async def build_spine(cfg: Any, *, sid: str, sessions_dir: Path,
                      bus: Optional[EventBus] = None) -> SimpleNamespace:
    """装配脊柱 8 模块束(create_agent 输入)+ 持久化三层。

    流程:store(JSONL 真源)→ SessionLog(事件源,总线分发)→ AgentLoop(真
    LLM)→ Scope(strict)→ Registry → spine。不 activate(agent 由调用方
    enter 后 submit)。llm/scope/loop 全部真实,零 mock。
    """
    bus = bus or EventBus()
    store = open_store(sid, dir=sessions_dir)
    log_ = await open_session(sid, store)    # async 工厂(SessionLog 装配)
    spine = build_runner_components(cfg, log_=log_, bus=bus,
                                    sessions_dir=sessions_dir,
                                    store=store)
    spine.persistence = store
    return spine


def build_runner_components(cfg: Any, *, log_: Any, bus: EventBus,
                            sessions_dir: Path,
                            store: Any = None,
                            attach_persistence: bool = True) -> SimpleNamespace:
    """纯组件装配(复用外部已 open 的 SessionLog/bus/store)。

    desktop 多会话场景:会话已由 DesktopSessionManager open(log_/store/bus
    俱在),本函数只补 loop/scope/registry/tools/llm——避免第二 store 实例
    同写一 JSONL(双缓冲竞态)。build_spine = 本函数 + 自建 store/session,
    语义等价。

    attach_persistence:总线→存储落盘订阅开关。自建 spine(build_spine)须开;
    desktop 复用 manager 会话时 manager._attach_persistence 已订阅(owner=
    persistence:{sid}),重复订阅 → 每事件双写 + store 游标错乱(实测卡死),
    故 desktop 调用传 attach_persistence=False。
    """
    # AgentLoop(真 cfg:max_turns=30 权威默认)
    loop = AgentLoop(cfg)

    # Scope(strict 最小权限 + cfg 预算)
    limits = BudgetLimits.from_cfg(cfg)
    policy = ScopePolicy()                     # 默认:strict / 空 deny / 空域名
    scope = Scope(policy, limits, session_id=str(getattr(log_, "session_id", "")),
                  session=log_, bus=bus)

    # Registry(空工具表;纯聊装配,见偏离 1)
    registry = Registry(bus)
    from pyharness.core.tools_registry import ToolRegistry
    tools = ToolRegistry()                   # 空注册表:schemas_for → [],零 Provider

    # LLM 门面(会话级;适配器须先 register_default_llm)
    llm_client = llm_mod.LLMClient(cfg)

    # SessionLog 接总线落盘订阅(事件一入内存即入真源队列)
    if attach_persistence:
        if bus is not None:
            owner = f"engine:{getattr(log_, 'session_id', '?')}"
            for t in _EVENT_TYPES_OR_ALL():
                bus.subscribe(t, _record_to(store), owner=owner)
        if store is not None:
            log_._bus = bus                  # append → 分发 → 落盘闭环

    spine = SimpleNamespace(
        session=log_, bus=bus, registry=registry, persistence=store,
        scope=scope, llm=llm_client, tools=tools,  # 空 ToolRegistry(schemas_for 可调)
        loop=loop, sys=None, settings=cfg,
        storage=SimpleNamespace(sessions_dir=sessions_dir),
        active_agents={}, headless=False)
    return spine


def _EVENT_TYPES_OR_ALL() -> tuple:
    try:
        from pyharness.events.vocab import EVENT_TYPES as _ET
        return tuple(_ET)
    except Exception:                            # noqa: BLE001
        return ("session.created", "user.message", "agent.message",
                "agent.thinking", "tool.call", "tool.result", "guard.rejected",
                "guard.evaluated", "approval.requested", "approval.granted",
                "approval.denied", "approval.timeout", "llm.request",
                "llm.response", "llm.usage", "llm.chunk", "system.error",
                "session.finished", "session.recovered", "context.compacted",
                "segment.start", "segment.end", "fork.created")


def _record_to(store: Any):
    """总线 → 存储订阅(强同步三类即写即刷;其余批量落盘由 store 自管)。"""
    _SYNC = ("user.message", "guard.rejected", "approval.requested",
             "approval.granted", "approval.denied", "approval.timeout",
             "session.finished", "session.recovered", "segment.start",
             "fork.created", "context.compacted")

    async def _record(type_: str, payload: Any) -> None:
        await store.append(payload, sync=type_ in _SYNC)
    return _record


# ---------------------------------------------------------------- runner seam
def make_runner(spine: SimpleNamespace) -> Any:
    """task_queue runner seam:async run_for_task(task) → loop.wake(env)。

    task 的 text 已由 create_message 落盘为 user.message(seq 已知);
    从事件源找该 envelope(按 seq),交 loop.wake 驱动真实处理。
    """
    log_ = spine.session
    loop = spine.loop

    async def run_for_task(task: Any) -> Any:
        want = int(getattr(task, "seq", 0) or 0)
        env = None
        if want > 0:                           # 首选:按 seq 精确定位
            for e in log_.events_after(0):
                if int(getattr(e, "seq", 0)) == want:
                    env = e
                    break
        if env is None:                        # 兜底:最近一条 user.message
            for e in reversed(list(log_.events_after(0))):
                if getattr(e, "type", "") == "user.message":
                    env = e
                    break
        if env is None:
            raise PyHError("CYC-999", ctx={"hint": "runner 找不到对应 user.message 事件"})
        return await loop.wake(env, ctx=_agent_ctx_of(spine, env))

    async def cancel_current(task_id: Optional[str] = None) -> None:
        cur = loop.current
        if cur is not None:
            cur.cancelled = True
            # 协作式:由 loop 轮内 CancelledError 传播收尾
    return SimpleNamespace(run_for_task=run_for_task,
                           cancel_current=cancel_current)


def _agent_ctx_of(spine: SimpleNamespace, env: Any) -> Any:
    """agent_loop 消费的 ctx = create_agent 装配的 Agent.ctx(spine 绑定)。

    Agent 与 loop 1:1;ctx.session/scope/llm 即 spine 组件——Agent.ctx 由
    create_agent 构造并绑定 loop。env 仅作输入(loop.wake 已携带)。
    """
    sid = str(getattr(env, "session_id", "") or spine.session.session_id or "")
    ag = spine.active_agents.get(sid) if hasattr(spine, "active_agents") else None
    if ag is None:
        ag = create_agent(sid, spine, spine.settings or _cfg_of(spine))
        spine.active_agents[sid] = ag
    return ag.ctx


def _cfg_of(spine: SimpleNamespace) -> Any:
    cfg = getattr(spine, "settings", None)
    if cfg is None:
        from pyharness.config import load_settings
        cfg = load_settings()
        spine.settings = cfg
    return cfg


# ---------------------------------------------------------------- 顶层装配
async def assemble_real_engine(cfg: Any, *, sid: str,
                               sessions_dir: Path) -> SimpleNamespace:
    """一键真实引擎(desktop create_message 用)。

    返回 ctx:与 cli.assemble_ctx 同形状(session/bus/llm/scope/task_queue/
    make_runner)+ engine 脊柱。调用方负责:register_default_llm(cfg) 先行、
    session.created 首事件、enter/close 生命周期。
    """
    register_default_llm(cfg)
    spine = await build_spine(cfg, sid=sid, sessions_dir=sessions_dir)
    ctx = SimpleNamespace(
        settings=cfg, bus=spine.bus, session=spine.session,
        llm=spine.llm, scope=spine.scope, loop=spine.loop,
        tools=spine.tools, registry=spine.registry,
        agent=None, task_runner=make_runner(spine),
        make_runner=lambda: make_runner(spine),
        storage=SimpleNamespace(sessions_dir=sessions_dir),
        engine_spine=spine)
    return ctx
