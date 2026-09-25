"""pyharness/engine.py — 真实引擎装配(desktop/CLI 共用)。

一句话职责:从分层配置 Settings 装配可跑的真实引擎——store(JSONL 真源)→
SessionLog → AgentLoop(真 LLM 已注册)→ Scope(strict 最小权限)→
Registry → agent_loop runner seam(task_queue.run_for_task = 找 user.message
envelope → loop.wake),供桌面壳/交互 CLI 把"提问"真正变成 Agent 干活。

安全基线:scope 默认 strict(sandbox_level="strict" 禁高危工具);llm.api_key
走 secret-ref(env:/file:),绝不落盘明文;无 key 时降级明确报错不猜测。

偏离说明(契约以各 specs 为准,此处为装配层取舍):
1. 内置能力全量注册(非空注册表):fs.* 4 工具 + storage.spill/kv + goal/todo +
   web.search/fetch + exec.*/proc.* + user.ask + skill.* + schedule,共 20 个
   Definition 真实进注册表;strict 档下按 scope 域显隐(workspace 域 fs/storage
   放行、session/self 域恒可见、exec/web 需 basic 档或 allowlist),所以"模型看得见
   几个工具"由策略决定,不由装配决定。[2026-09-18:增 schedule 工具时校正计数
   ——此前本注释写 18,与枚举项数(19)及实测注册表均不符,属既有漂移。]
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

import asyncio
import copy
import hashlib
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from pyharness.bus import EventBus, bus_kwargs_of
from pyharness.bus.registry import Registry
from pyharness.core.agent import CHANNEL_UNDECLARED, create_agent
from pyharness.core.agent_loop import AgentLoop
from pyharness.core import llm as llm_mod
from pyharness.core.scope import (BudgetLimits, Scope, ScopePolicy,
                                  session_workspace)
from pyharness.core.session import open_session
from pyharness.errors import PyHError
from pyharness.persistence import flush_kwargs_of, open_store

log = logging.getLogger("pyharness.engine")


@dataclass
class EngineStorage:
    """引擎存储挂载面:路径 + 懒激活的 spill/KV provider。"""

    sessions_dir: Path
    spill: Any = None
    kv: Any = None


@dataclass
class EngineSpine:
    """会话级引擎装配束(替代无类型 SimpleNamespace)。"""

    session: Any
    bus: Any
    registry: Any
    scope: Any
    llm: Any
    tools: Any
    guard: Any
    approval: Any
    counters: Any
    sysprompt: Any
    compactor: Any
    goals: Any
    todos: Any
    ask: Any
    skills: Any
    plugins: Any
    plugin_state: dict
    tool_registry: Any
    streaming: bool
    loop: Any
    settings: Any
    storage: EngineStorage
    persistence: Any = None
    sys: Any = None
    active_agents: dict[str, Any] = field(default_factory=dict)
    headless: bool = False
    plan: Any = None
    schedule: Any = None
    jobs: Any = None
    subagent: Any = None
    task_queue: Any = None
    search_backend: Any = None
    budget: Any = None
    fts: Any = None
    llm_probe: Any = None                          # F033 健康探针后台任务(收尾须取消)
    bus_owners: list = field(default_factory=list)  # 本会话在总线上持有的订阅属主
    governance: Any = None                         # 治理层单实例(ADR-018;S2-3)
    # 通道身份(GAP-11):外壳装配期**显式声明**"谁在驱动本引擎"。
    # 三态:字符串 = 人类通道(cli/web/acp:<id>/desktop);``None`` = 显式 headless;
    # ``CHANNEL_UNDECLARED`` = 从未声明 ⇒ 不写 ``ctx.channel`` ⇒ 治理层
    # ``principal_of`` 见属性缺失即 APR-503 fail-closed(禁止静默降级为 system)。
    channel: Any = CHANNEL_UNDECLARED
    _plugins_ready: bool = False
    _auto_titled: bool = False
    _spill_provider: Any = None
    _kv_activated: bool = False
    _fts_entered: bool = False
    _mcp_clients: list[Any] = field(default_factory=list)
    _orchestration_ready: bool = False
    _schedule_started: bool = False
    _policy_announced: bool = False        # 装配期策略留痕幂等闸(F-01:每 spine 恰一次)
    _jobs_recovered: bool = False          # 崩溃 job 事件级恢复幂等闸(B1/R24:每 spine 恰一次)
    # 攒批落盘定时器(GAP-13):此前 ``flush_interval_s`` 只被存下、**无读取者**,
    # 而 persistence/cli 三处注释都宣称"0.5s 定时器兜底" —— 文档描述了一个不存在
    # 的机制。定时器由本 spine 持有(惰性启动、close 时先停),使攒批事件(非 SYNC
    # 的 tool.call/guard.evaluated/tool.result 等)的**落盘窗口有上界**。
    _flush_task: Any = None
    _flush_stop: Any = None

    async def start_flush_ticker(self) -> bool:
        """幂等启动攒批落盘定时器(间隔 = ``store.flush_interval_s``;GAP-13)。

        语义与边界:

        - **幂等**:已在跑(未 done)→ 返回 False,不重复起任务;
        - **无 store / 间隔 ≤0** → 不起(返回 False),不是错误;
        - **单次失败不杀定时器**:``store.flush()`` 抛 ``PERS-202`` 时只记日志,
          落盘故障状态由 ``SessionStore._fail_streak`` / ``_suspended`` 自行记账
          (拒新不丢旧),定时器继续按周期间隔重试 —— 崩溃窗口因此**有上界**;
        - **不吞取消**:收到 ``CancelledError`` 立即 re-raise(取消协议);
        - 停止方式 = ``close()`` 置位 ``_flush_stop`` 并 await,超时则 cancel。

        该定时器**不是第二写者**:它只调 store 自己的 ``flush()``,与强同步路径
        共用同一把攒批缓冲与 seq 校验(INV-07 单写者不变)。
        """
        if self._flush_task is not None and not self._flush_task.done():
            return False
        store = self.persistence
        if store is None:
            return False
        try:
            interval = float(getattr(store, "flush_interval_s", 0.5) or 0.5)
        except (TypeError, ValueError):
            interval = 0.5
        if interval <= 0:
            return False
        stop = asyncio.Event()
        self._flush_stop = stop

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                    return                       # 收到停止信号:正常退出
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    raise
                try:
                    r = store.flush()
                    if inspect.isawaitable(r):
                        await r
                except asyncio.CancelledError:
                    raise
                except Exception:                # noqa: BLE001 单次失败不杀定时器
                    log.warning("flush ticker 落盘失败(store 自行记账 PERS-202)",
                                exc_info=True)

        self._flush_task = asyncio.create_task(
            _loop(), name=f"flush-ticker:{getattr(self.session, 'sid', '?')}")
        return True

    async def close(self) -> None:
        """Stop all writers before releasing subscriptions, stores and connections."""
        task = getattr(self, '_close_task', None)
        if task is None:
            self._closing = True
            task = self._close_task = asyncio.create_task(self._close_owned())
        await asyncio.shield(task)

    async def _close_owned(self):
        errors = []
        async def attempt(fn, *args, **kwargs):
            try:
                value = fn(*args, **kwargs)
                if inspect.isawaitable(value):
                    await value
            except BaseException as exc:
                errors.append(exc)
        if self.schedule is not None:
            await attempt(getattr(self.schedule, 'aclose', None) or self.schedule.stop)
        if self.jobs is not None:
            await attempt(self.jobs._on_session_closing, 'session.closing', None)
        if self.subagent is not None:
            await attempt(self.subagent.detach)
        if self.task_queue is not None:
            await attempt(self.task_queue.shutdown, reason='session-close')
        if self.approval is not None:
            await attempt(getattr(self.approval, 'aclose', None) or self.approval.detach)
        for name in ('_flush_task', 'llm_probe'):
            task = getattr(self, name, None)
            if task is not None:
                task.cancel()
                result = await asyncio.gather(task, return_exceptions=True)
                errors.extend(e for e in result if isinstance(e, BaseException) and not isinstance(e, asyncio.CancelledError))
                setattr(self, name, None)
        self._flush_stop = None
        from pyharness.core import proc as proc_mod
        await attempt(asyncio.to_thread, proc_mod.close_session, str(getattr(self.session, 'sid', '')))
        while self._mcp_clients:
            await attempt(self._mcp_clients.pop().close)
        if self.fts is not None:
            await attempt(self.fts.detach, None)
            self.fts = None
        store = self.persistence
        if store is not None and getattr(self, 'owns_persistence', True):
            flush = getattr(store, 'flush', None) or getattr(store, 'flush_sync', None)
            if callable(flush): await attempt(flush)
            close = getattr(store, 'close', None)
            if callable(close): await attempt(close)
        if self.bus is not None:
            for owner in self.bus_owners:
                await attempt(self.bus.unsubscribe_all, owner)
            self.bus_owners = []
        if getattr(self, 'owns_llm_runtime', False):
            await attempt(self.llm_runtime.aclose)
        if errors:
            raise BaseExceptionGroup('engine cleanup failed', errors)


@dataclass
class EngineRunner:
    """task_queue runner 适配面。"""

    run_for_task: Callable
    cancel_current: Callable


@dataclass
class EngineContext:
    """桌面/CLI 消费的引擎上下文(字段显式化,保留鸭子兼容)。"""

    settings: Any
    bus: Any
    session: Any
    llm: Any
    scope: Any
    loop: Any
    tools: Any
    registry: Any
    guard: Any
    approval: Any
    counters: Any
    storage: EngineStorage
    engine_spine: EngineSpine
    task_runner: EngineRunner
    make_runner: Callable[[], EngineRunner]
    agent: Any = None
    task_queue: Any = None
    budget: Any = None
    governance: Any = None                 # 治理层单实例(ADR-018;S2-3 装配面)


# ---------------------------------------------------------------- 适配器注册
def register_default_llm(cfg: Any, *, force: bool = False,
                         counters: Any = None, registry: Any = None) -> str:
    """注册 cfg.llm.model 的真实适配器(OpenAI 兼容端点)。

    已注册同名模型 → 跳过(幂等;force=True 强制覆盖)。key 缺失 → 抛 LLM-304
    明确提示,不静默降级。counters:会话级 F029 计数器(engine 装配传入共享实例,
    使 adapter 计量入账与 scope 预算闸同源);缺省 = 适配器私有计数(旧行为)。
    返回注册的模型名。

    秘密纪律:api_key 是 "env:NAME"/"file:PATH" 引用字符串,值由适配器在
    secret_ref_ok 解析(CRED-701/702/703)——本函数不接触也不落盘明文。
    """
    llm_cfg = cfg.llm
    registry = llm_mod.adapters if registry is None else registry
    api_ref = str(llm_cfg.api_key or "")
    if not api_ref.startswith(("env:", "file:", "tenant:")):
        llm_mod.raise_code(
            "CRED-701", model=llm_cfg.model,
            advice="llm.api_key 须 env:NAME / file:PATH / tenant:租户:档案 秘密引用")

    credential_store, binding = None, None
    if api_ref.startswith('tenant:'):
        from pyharness.core.tenant_settings import TenantSettingsStore, tenants_dir, _STORES
        tenant = api_ref.split(':', 2)[1]
        root = tenants_dir(cfg.storage.root)
        credential_store = _STORES.get(tenant)
        if credential_store is None or credential_store.root.resolve() != root.resolve():
            credential_store = TenantSettingsStore(root)
        from types import MappingProxyType
        binding = MappingProxyType(credential_store.binding(api_ref, str(llm_cfg.base_url)))

    def actual_model(name):
        existing = registry.get(str(name)) or llm_mod.adapters.get(str(name))
        return str(existing.model) if isinstance(existing, llm_mod.OpenAICompatAdapter) else str(name)

    def key_for(model_name: str) -> str:
        if model_name in registry and not isinstance(registry[model_name], llm_mod.OpenAICompatAdapter):
            return model_name  # explicitly supplied deterministic/plugin adapter
        limits = llm_mod.TimeoutLimits.from_cfg(llm_cfg.timeout)
        identity = (api_ref, str(llm_cfg.base_url).rstrip('/'), model_name, repr(limits),
                    binding['revision'] if binding else '',
                    str(credential_store.root.resolve()) if credential_store else '')
        digest = hashlib.sha256(repr(identity).encode('utf-8')).hexdigest()[:20]
        return f'__connection_{digest}__{model_name}'

    main_model = actual_model(llm_cfg.model)
    main_key = key_for(main_model)
    fallback_models = [actual_model(m) for m in (getattr(llm_cfg, 'fallback_models', None) or [])]
    fallback_keys = [key_for(m) for m in fallback_models]
    for actual, registry_key in zip([main_model, *fallback_models], [main_key, *fallback_keys]):
        if registry_key not in registry or force:
            previous = registry.get(registry_key)
            if isinstance(previous, llm_mod.OpenAICompatAdapter) and previous._transport is not None:
                raise_code('CRED-703', reason='live_model_replacement_requires_runtime_close')
            registry[registry_key] = llm_mod.OpenAICompatAdapter(
                model=actual, triple=llm_mod.AdapterTriple(
                    base_url=str(llm_cfg.base_url), api_key_ref=api_ref, model=actual,
                    credential_store=credential_store, credential_binding=binding),
                limits=llm_mod.TimeoutLimits.from_cfg(llm_cfg.timeout), cfg=cfg)
    llm_cfg.model = main_key
    llm_cfg.fallback_models = fallback_keys
    return main_key


def _make_chain(cfg: Any, bus: Any, registry=None) -> Any:
    """FallbackChain 装配(F013/F028):adapters 引用 llm 注册表(后续注册立即可见)。

    config = Settings 权威(chain = llm.model + fallback_models;退避 llm.retry;
    降级 llm.degrade);bus 为告警/通知事件尽力出口。"""
    from pyharness.core.llm_fallback import FallbackChain
    return FallbackChain(adapters=llm_mod.adapters if registry is None else registry, config=cfg, bus=bus)


def _cfg_probe_interval(cfg: Any) -> float:
    """F033 探针周期取值(CFG `llm.probe.interval_s`,缺省 60s)。

    与 ``flush_kwargs_of``/``_cfg_max_arg_failures`` 同款:装配层解析配置后把**标量**
    注入组件,组件不 import config。
    """
    v = getattr(getattr(getattr(cfg, "llm", None), "probe", None), "interval_s", None)
    try:
        return float(v) if v is not None else 60.0
    except (TypeError, ValueError):
        return 60.0


def _policy_preset(cfg: Any) -> str:
    """security.policy.preset 读取(缺省 strict;任意 cfg 形态容错)。"""
    try:
        v = getattr(getattr(getattr(cfg, "security", None), "policy", None),
                    "preset", None)
    except Exception:                                  # noqa: BLE001 非 Settings 对象
        return "strict"
    return str(v) if v in ("locked", "readonly", "standard", "strict") else "strict"


def _cfg_max_arg_failures(cfg: Any) -> int:
    """F026 连败阈值取值(CFG `loop.max_arg_failures_per_round`,缺省由执行器常量兜底)。

    与 ``persistence.flush_kwargs_of`` 同款装配面:装配层解析配置后把**标量**注入
    组件,组件自身不 import config(N1"声明配置必须可达")。
    """
    from pyharness.core.tools_executor import DEFAULT_MAX_ARG_FAILURES
    v = getattr(getattr(cfg, "loop", None), "max_arg_failures_per_round", None)
    try:
        return int(v) if v is not None else DEFAULT_MAX_ARG_FAILURES
    except (TypeError, ValueError):
        return DEFAULT_MAX_ARG_FAILURES


def _apply_preset(cfg: Any, policy: Any, tool_reg: Any,
                  *, scope: Any = None) -> None:
    """权限预设编译(#39):按注册表实名字面量收紧/放宽,策略只紧不松(无 relax)。

    - locked:  全禁(仅对话,零工具)——deny = 全部注册名
    - readonly:保留只读/会话内自管理(fs 读/列表 + goal/todo + spill 读)
    - standard:sandbox_level=basic(放宽域约束;危险级仍由 guard/审批管)
    - strict:  默认最小权限(engine 既有行为,不改动)

    留痕(2026-09-12 修复):策略写入后经 scope.note_tighten 落 scope.updated 事件
    ——装配段是同步函数无法 await,故用同形只读补记,消除"切预设无审计痕"缺口。
    _deny 为空(strict/无注册名)时不留痕(与 tighten 幂等语义一致)。
    """
    def _audit(added: set) -> None:
        if scope is not None and added:
            try:
                scope.note_tighten(added, reason=f"preset:{preset_name}")
            except Exception:                      # noqa: BLE001 留痕失败不阻断装配
                log.warning("preset 留痕失败", exc_info=True)

    preset_name = _policy_preset(cfg)
    # Explicit local prohibitions apply at initial assembly and after preset resets.
    policy.deny_tools.update(getattr(getattr(getattr(cfg, "security", None),
        "policy", None), "deny_tools_extra", None) or [])
    names = [d.name for d in tool_reg.iter_definitions()]
    if preset_name == "locked":
        _deny = set(names)
        policy.deny_tools |= _deny
        _audit(_deny - set())
        return
    if preset_name == "readonly":
        keep = {"fs.read_file", "fs.list_dir", "goal", "todo", "storage.spill"}
        _deny = set(names) - (keep & set(names))
        policy.deny_tools |= _deny
        _audit(_deny)
        return
    if preset_name == "standard":
        policy.sandbox_level = "basic"                 # 放宽仅域约束
        # 同步可见性档:tool_exec 等按 security.sandbox.level 判定(两键脱节
        # 会造成审批已放行但工具 TLB-802——2026-09-08 exec 真链实测暴露)
        try:
            cfg.security.sandbox.level = "basic"
        except Exception:                              # noqa: BLE001 只尽力同步
            pass
        return
    # strict:保持默认(无操作)


# ---------------------------------------------------------------- spine 装配
async def build_spine(cfg: Any, *, sid: str, sessions_dir: Path,
                      bus: Optional[EventBus] = None,
                      channel: Any = CHANNEL_UNDECLARED,
                      tenant_id: Optional[str] = None) -> EngineSpine:
    """装配脊柱 8 模块束(create_agent 输入)+ 持久化三层。

    流程:store(JSONL 真源)→ SessionLog(事件源,总线分发)→ AgentLoop(真
    LLM)→ Scope(strict)→ Registry → spine。不 activate(agent 由调用方
    enter 后 submit)。llm/scope/loop 全部真实,零 mock。

    ``channel``(GAP-11):外壳身份声明,透传 ``build_runner_components``。
    ``tenant_id``(GAP-10):租户归属,交给 ``open_session`` 盖到每条事件信封。
    """
    bus = bus or EventBus(**bus_kwargs_of(cfg))  # F005 背压阈值由装配层注入(N1)
    # N1:攒批/定时落盘标量由**装配层**从 Settings 解析后注入(工厂不读 config)。
    store = open_store(sid, dir=sessions_dir, **flush_kwargs_of(cfg))
    try:
        log_ = await open_session(sid, store, tenant_id=tenant_id)  # async 工厂
        spine = build_runner_components(cfg, log_=log_, bus=bus,
                                        sessions_dir=sessions_dir,
                                        store=store, channel=channel)
        spine.persistence = store
        return spine
    except BaseException as primary:
        # Ownership transfers only when the complete spine is returned.
        try:
            store.close()
        except BaseException as cleanup_error:
            primary.add_note(f'store cleanup failed: {type(cleanup_error).__name__}')
        raise


async def _rollback_spine(spine: EngineSpine, primary: BaseException) -> None:
    """Finish unreturned assembly cleanup without replacing its original failure."""
    cleanup = asyncio.create_task(spine.close())
    while True:
        try:
            await asyncio.shield(cleanup)
            break
        except asyncio.CancelledError:
            if cleanup.done():
                if cleanup.cancelled():
                    primary.add_note('engine cleanup task was cancelled')
                    break
                # Retrieve any failure from the completed task on the next pass.
            primary.add_note('cancellation received while awaiting engine cleanup')
        except BaseException as cleanup_error:
            primary.add_note(f'engine cleanup failed: {type(cleanup_error).__name__}')
            break
    try:
        spine.scope.release()
    except BaseException as cleanup_error:
        primary.add_note(f'scope cleanup failed: {type(cleanup_error).__name__}')


async def _preload_plugins(spine: EngineSpine) -> None:
    """插件预载(async 入口用;load_plugin 需 await,同步装配段不碰)。

    扫 examples/plugins/* 逐个装载进该会话工具表;单个失败只告警不阻断
    (会话照常可用;事件 plugin.installed 落该会话日志=真实审计)。
    """
    from pyharness.core import plugin_loader as _pl
    plug_ctx = SimpleNamespace(
        session=getattr(spine, "session", None),
        bus=getattr(spine, "bus", None))
    pdir = Path(__file__).resolve().parents[1] / "examples" / "plugins"
    if not pdir.exists():
        await _preload_mcp(spine)
        return
    # `plugins.priority`（R24 接线）:同层无依赖插件的装载次序,**小者先**;
    # 未列入者按默认 0(平序回落到**名字序**,保持确定性)。该键此前零读取者。
    prio = dict(getattr(getattr(spine, "settings", None), "plugins", None)
                and (getattr(spine.settings.plugins, "priority", None) or {}) or {})
    for pd in sorted(pdir.glob("*/"),
                     key=lambda d: (int(prio.get(d.name, 0) or 0), d.name)):
        try:
            spec = _pl.load_spec(pd)
            await _pl.load_plugin(spine.plugins, spec, plug_ctx,
                                  spine.tool_registry,
                                  state=spine.plugin_state)
        except Exception as e:               # noqa: BLE001 预载失败不阻断
            log.warning("plugin preload failed %s: %s", pd.name,
                        type(e).__name__)
    await _preload_mcp(spine)


async def _preload_mcp(spine: EngineSpine) -> None:
    """装载配置中的 MCP stdio servers,并桥接工具到会话 registry。"""
    plugins = getattr(getattr(spine, "settings", None), "plugins", None)
    servers = list(getattr(plugins, "mcp_servers", None) or [])
    if not servers:
        return
    from pyharness.core.mcp import McpClient, StdioTransport
    for server in servers:
        if not getattr(server, "enabled", True):
            continue
        client = McpClient(server.name, StdioTransport(
            list(server.command), timeout_s=int(server.timeout_s)))
        try:
            await client.connect()
            client.register_into(spine.tool_registry, allowed_tools=server.allowed_tools)
            spine._mcp_clients.append(client)
        except Exception as e:                       # noqa: BLE001 单个 server 隔离
            await client.close()
            log.warning("mcp preload failed name=%s: %s", server.name,
                        type(e).__name__)



class _SpineCtx:
    """Live ctx for long-lived orchestration timers.

    Scheduler keeps this object for the whole session, so ``task_queue`` is
    resolved at fire time rather than being frozen to None during startup.
    """

    def __init__(self, spine: EngineSpine) -> None:
        self._spine = spine

    @property
    def session(self) -> Any:
        return self._spine.session

    @property
    def task_queue(self) -> Any:
        return self._spine.task_queue


async def activate_orchestration(spine: EngineSpine, *,
                                 task_queue: Any = None) -> None:
    """Activate plan/schedule/jobs/subagent capabilities once per spine.

    Subagent tool registration is idempotent.  Scheduler recovery starts the
    minute pump only after a queue is available when called from the shells.
    """
    if task_queue is not None:
        spine.task_queue = task_queue
        if spine.schedule is not None:
            spine.schedule.task_queue = task_queue
        if spine.jobs is not None:
            spine.jobs._tq = task_queue
    if not spine._orchestration_ready:
        if spine.subagent is not None:
            await spine.subagent.enter()
            await spine.subagent.announce()
        spine._orchestration_ready = True
    if spine.schedule is not None and not spine._schedule_started:
        await spine.schedule.recover(ctx=_SpineCtx(spine))
        spine._schedule_started = True


def _build_governance(cfg: Any, *, session: Any, bus: Any,
                      tool_reg: Any) -> tuple[Any, Any]:
    """治理层装配(S2-3):返回 ``(PolicyEngine, guard)``。

    **只一条 GuardChain**:``guard`` 取自 ``policy.chain``(由注入的 chain_factory
    产出),**禁止**另行直调 ``tools_guard.from_config``——否则 spine.guard 与
    policy.chain 是两个对象(T-B 断言钉死)。

    **注入参数同源**:同一份 ``inj`` 同时交给 ``describe_rules``(描述面)与
    ``chain_factory``(执行面)。两处都把注入参数**烘焙进 guard 闭包**,参数分歧
    ⇒ 治理层持有的规则与实际执行的链判定分歧(静默)。

    治理层无执行权(ADR-013 G-4 / INV-G5):运行期关 2 经
    ``ctx.governance.authorize``(S3-2-2 起),executor 不再直呼 ``ctx.guard.evaluate``。
    """
    from pyharness.core.tools_guard import describe_rules
    from pyharness.core.tools_guard import from_config as guard_from_config
    from pyharness.governance import PolicyEngine

    def _guard_factory(cfg_, *, session=None, bus=None, validator=None,
                       credential_paths=None, path_exists=None,
                       link_resolver=None, approval_channel=None):
        """装配层适配:治理层契约参数名 → ``tools_guard.from_config`` 实际形参名。

        治理层按设计用 ``credential_paths``(S2-1 设计 §3.2 契约);而
        ``tools_guard.from_config`` 的实际形参是 ``credentials``。二者名字不同,
        映射归**装配层**(治理层不 import tools_guard,ADR-018:308)。等同性:
        ``credentials=None`` 时 from_config 仍从 cfg 取默认清单,与 S1 行为一致。
        """
        return guard_from_config(cfg_, session=session, bus=bus,
                                 credentials=credential_paths,
                                 validator=validator, path_exists=path_exists,
                                 link_resolver=link_resolver,
                                 approval_channel=approval_channel)

    inj = dict(validator=tool_reg.validate_args, credential_paths=None,
               path_exists=None, link_resolver=None, approval_channel=True)
    policy = PolicyEngine.from_config(
        cfg, session=session, bus=bus,
        rules=describe_rules(**inj),
        chain_factory=_guard_factory, **inj)
    return policy, policy.chain


def build_runner_components(cfg: Any, *, log_: Any, bus: EventBus,
                            sessions_dir: Path,
                            store: Any = None,
                            attach_persistence: bool = True,
                            channel: Any = CHANNEL_UNDECLARED,
                            llm_runtime: Any = None) -> EngineSpine:
    """纯组件装配(复用外部已 open 的 SessionLog/bus/store)。

    desktop 多会话场景:会话已由 DesktopSessionManager open(log_/store/bus
    俱在),本函数只补 loop/scope/registry/tools/llm——避免第二 store 实例
    同写一 JSONL(双缓冲竞态)。build_spine = 本函数 + 自建 store/session,
    语义等价。

    attach_persistence:总线→存储落盘订阅开关。自建 spine(build_spine)须开;
    desktop 复用 manager 会话时 manager._attach_persistence 已订阅(owner=
    persistence:{sid}),重复订阅 → 每事件双写 + store 游标错乱(实测卡死),
    故 desktop 调用传 attach_persistence=False。

    channel(GAP-11):外壳身份声明(三态见 ``EngineSpine.channel``)。**每条
    生产路径都必须显式声明**——未声明时该引擎产出的任何决策都会在
    ``authorize()`` 处以 APR-503 fail-closed,这是有意为之(装配缺陷可见,
    不静默降级为 system 主体)。
    """
    # Connection registry keys and policy compilation are runtime-local state.
    cfg = copy.deepcopy(cfg)
    # Resolve the required workspace before subscribing capability owners.
    # A bad path must not leave approval listeners attached to a borrowed bus.
    ws_root = Path(getattr(cfg.storage, "workspaces_dir", "~/.pyharness/workspaces")
                   ).expanduser()
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace_root = session_workspace(str(ws_root), str(getattr(log_, "sid", "")))
    Path(workspace_root).mkdir(parents=True, exist_ok=True)
    # AgentLoop(真 cfg:max_turns=30 权威默认)
    loop = AgentLoop(cfg)

    # Scope(strict 最小权限 + cfg 预算);counters = 会话级共享用量计数器
    # (F029 report_usage 入账 → task_total 读数,F032 硬闸与 llm 同一实例)
    limits = BudgetLimits.from_cfg(cfg)
    policy = ScopePolicy()                     # 默认:strict / 空 deny / 空域名
    counters = llm_mod.UsageCounters()
    scope = Scope(policy, limits, session_id=str(getattr(log_, "sid", "")),
                  counters=counters, session=log_, bus=bus)

    # 预算门面(F032 只读消费面):与 Scope 共用同一 limits/counters 实例,
    # 仪表盘读数 = 循环强制终态判据(2026-09-12 修复:此前无人赋值 ctx.budget,
    # 导致 /api/budget 恒 disabled)
    from pyharness.core.budget import BudgetGate
    budget_gate = BudgetGate(limits=limits, counters=counters, scope=scope,
                             session_id=str(getattr(log_, "sid", "")))

    # Registry(能力三类索引,agent 装配用)
    registry = Registry(bus)

    # ---- 工具链(F008/F014/F015 真装配):fs.* 4 工具 + 四关执行器 + guard + 审批
    from pyharness.core.tools_registry import ToolRegistry
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core import tool_fs
    from pyharness.governance import (AuditSystem, DecisionEngine,
                                      EvidenceCollector, GovernanceContext,
                                      ReceiptStore)

    tool_reg = ToolRegistry()
    tool_fs.register(tool_reg)                 # fs.read_file/write_file/list_dir/delete_file
    from pyharness.core import spill as spill_mod
    spill_mod.register(tool_reg)               # storage.spill.read(F039 读工具,strict 可见)
    tools = ToolExecutor(tool_reg,                     # ctx.tools: schemas_for + execute(四关管道)
                         max_arg_failures=_cfg_max_arg_failures(cfg))
    # 内置 g1-g7(单调,事件落 session)——2026-09-14 S1-01 修复:此前直构
    # GuardChain(session,bus),绕过 from_config,导致三处装配缺口:
    #   ① validator 恒 None → g1 g-schema 在生产恒 allow(INV-04 内层复查失效);
    #   ② cfg.security.guards.disabled 永不生效(运维以为关了,实际仍在链上);
    #   ③ 凭据清单/path_exists/link_resolver 走默认,与 config 声明脱节。
    # 现经工厂装配并注入 validator=registry.validate_args(关1b 同源校验面)。
    # approval_channel=True:engine 装配面视同交互通道在位(与修复前 None 的
    # "视同有通道"语义等价);无通道场景仍由 approval 层 APR-501 兜底拒绝,
    # 故本项不改变 headless 行为。
    # 注意:不得用局部名 `policy` —— 上游已有 `policy = ScopePolicy()`(scope 的策略面),
    # 遮蔽它会让下面的 danger_marks/_apply_preset 写到 PolicyEngine 上而 scope 失效
    # (S2-3.2 实测:fs.delete_file 变可见)。故用 gov_policy。
    gov_policy, guard = _build_governance(cfg, session=log_, bus=bus,
                                          tool_reg=tool_reg)
    # N5:provider 缺省通道由**装配参数**决定(不再硬编码 "desktop")。
    # 未声明(CHANNEL_UNDECLARED)⇒ None(无缺省)。ctx 的声明才是权威;
    # 缺省仅供 user_choice/信任名单判定,**不**作为"ctx 未声明"的回退源。
    approval = ApprovalProvider(
        session=log_, bus=bus, config=cfg,
        channel=(None if channel is CHANNEL_UNDECLARED else channel))

    # danger 分级同源装配(F023):注册表 defn.danger → policy.danger_marks,
    # scope.can_use 据此拦 critical、schemas_for 据此过滤不可见工具
    marks = []
    for d in tool_reg.iter_definitions():
        lv = str(getattr(d, "danger", "none") or "none")
        if lv != "none":
            marks.append({"pattern": str(d.name), "level": lv})
    if marks:
        policy.danger_marks = marks
    # 权限预设档位(#39):locked/readonly/standard/strict(注册名已知后编译)
    # scope 传入 → 同步段补 scope.updated 审计(装配期无 await,见 note_tighten)
    _apply_preset(cfg, policy, tool_reg, scope=scope)
    # workspace 根(F055):**每会话专属根** {storage.workspaces_dir}/{sid}
    # (PRD-Core §5.6 F055 / CFG.md §3.6 / OPS.md / DEP.md 一致口径;未设则 fs.*
    # fail-closed)。2026-09-21 修:此前此处赋的是**裸** workspaces_dir → ①所有会话
    # 共用同一 fs 根,跨会话文件互相可见/可写(会话隔离失效);②子会话工厂按规格
    # 形态 base.parent/{sub_id} 推导时越出 workspaces 树且目录从未创建(子代理
    # 文件类工具全废)。派生一律走 scope.session_workspace 单点,不再就地拼接。
    policy.workspace_root = workspace_root
    # storage.spill 定位器:首个 agent 就绪时 SpillProvider.enter 挂载(见
    # _activate_storage_caps)——executor 关4 超长输出(>2KB)→ spill 私有区 +
    # ref 回喂(F039);激活失败时报 PERS-221 不崩(截断兜底)

    # LLM 门面(会话级;适配器须先 register_default_llm)+ 降级链(F013/F028):
    # fallback_models 非空 → FallbackChain 挂载(指数退避 + 单调降级 + BudgetGuard
    # 前置);chain.adapters 引用 llm 模块注册表,后续 register 补注册立即可见
    owns_llm_runtime = llm_runtime is None
    llm_runtime = llm_runtime or llm_mod.LLMRuntime()
    register_default_llm(cfg, registry=llm_runtime.registry)
    fb_models = list(getattr(getattr(cfg, "llm", None), "fallback_models", None) or [])
    llm_client = llm_mod.LLMClient(
        cfg, registry=llm_runtime.registry,
        chain=_make_chain(cfg, bus, llm_runtime.registry) if fb_models else None)
    # F033 健康探针:**回切的唯一驱动**。2026-09-21 修:`chain.probe_loop` 此前全库
    # 零生产调用者(只有单测启动过),而健康状态机只能被探针改回 healthy、降级链的
    # idx 也只在探针里归零 ⇒ **一次降级即永久降级**(进程生命周期内不回切主模型),
    # `llm.recovered` 永不发出。此处按进程/会话生命周期起任务,close 时取消。
    probe_task = None
    llm_chain = getattr(llm_client, "chain", None)
    if llm_chain is not None:
        try:
            probe_task = asyncio.get_running_loop().create_task(
                llm_chain.probe_loop(interval_s=_cfg_probe_interval(cfg)),
                name=f"llm-probe:{getattr(log_, 'sid', '?')}")
        except RuntimeError:                       # 无运行中事件循环(同步装配)
            log.warning("llm 探针未启动(无运行中事件循环):模型降级后不会自动回切")
    # F010 系统提示词装配器 / F058 压缩器:挂 ctx.sysprompt/ctx.compactor,
    # agent-loop 出网前装配、wake 请求边界压缩(模块完整,装配即真链)
    from pyharness.core.system_prompt import SystemPromptAssembler
    from pyharness.core.compaction import Compactor
    sysprompt = SystemPromptAssembler(config=cfg)
    compactor = Compactor.from_config(cfg)
    # F047 目标管理 / #32 待办:状态只由 goal.*/todo.updated 事件派生,管理器
    # 挂 ctx.goals/ctx.todos(工具 provider 消费面 + sysprompt 目标段数据源)
    from pyharness.core import tool_goal as _tool_goal
    from pyharness.core import tool_todo as _tool_todo
    from pyharness.core import tool_web as _tool_web
    from pyharness.core.goal import GoalManager
    from pyharness.core.todo import TodoManager
    _tool_goal.register(tool_reg)                # goal.*(会话内自管理,恒可见)
    _tool_todo.register(tool_reg)                # todo.*
    _tool_web.register(tool_reg)                 # web.search/fetch(域放行才可见)
    from pyharness.core import kv as _tool_kv
    _tool_kv.register(tool_reg)                  # storage.kv(#53 会话 KV)
    from pyharness.core import tool_exec as _tool_exec
    _tool_exec.register(tool_reg)                # exec.* 沙箱族(#28/#29/#35/#58)
    from pyharness.core import tool_ask as _tool_ask
    _tool_ask.register(tool_reg)                 # user.ask(#38 反问)
    from pyharness.core.skill import SkillManager
    from pyharness.core import tool_skill as _tool_skill
    _tool_skill.register(tool_reg)               # skill.load/list(F073 技能系统)
    from pyharness.core import tool_schedule as _tool_schedule
    _tool_schedule.register(tool_reg)            # schedule(F048 定时任务管理面)
    skill_roots = [Path(__file__).resolve().parents[1] / "skills"]
    try:
        extra_skill_dir = getattr(getattr(cfg, "skills", None), "dir", None)
        if extra_skill_dir:
            skill_roots.append(Path(str(extra_skill_dir)).expanduser())
    except Exception:                                    # noqa: BLE001
        pass
    skills = SkillManager(skill_roots, session=log_)     # 仓库 + 用户技能目录
    # ---- 插件系统:五态管理器(装载/热更/卸载经 async _preload_plugins 或
    #     桌面 API;同步装配段只建管理器,await 在 async 入口做)----
    from pyharness.bus.plugin import PluginManager
    plg_mgr = PluginManager()
    plg_state: dict = {}
    goals = GoalManager(session=log_)
    todos = TodoManager(session=log_)
    from pyharness.core.user_ask import AskProvider
    ask = AskProvider(session=log_, bus=bus)     # #38 反问宿主(pending/答复)
    from pyharness.core.plan_mode import PlanManager
    from pyharness.core.schedule import Scheduler
    from pyharness.core.tool_web import BingRssBackend, DuckDuckGoBackend
    plan = PlanManager()                         # F045/F046 计划模式
    schedule = Scheduler(session=log_, task_queue=None,
                         auto_ticker=True)       # F048 定时任务
    net = getattr(getattr(cfg, "security", None), "network", None)
    backend_name = str(getattr(net, "search_backend", "disabled"))
    endpoint = str(getattr(net, "search_endpoint", ""))
    search_ep = ""
    if backend_name == "bing":
        search_ep = endpoint or "https://cn.bing.com/search"
        search_backend = BingRssBackend(search_ep)
    elif backend_name == "duckduckgo":
        search_ep = endpoint or "https://html.duckduckgo.com/html/"
        search_backend = DuckDuckGoBackend(search_ep)
    else:
        search_backend = None
    # g5 外发闸:web.search 无 url 参数,以其后端 host 作目标域(P3:web.search 恒拒)
    # ——操作者在 allowed_domains 列入后端 host 后 web.search 方可通行;未配后端=空。
    if search_backend is not None:
        from urllib.parse import urlsplit
        try:
            policy.search_host = (urlsplit(search_ep).hostname or "").lower()
        except Exception:                            # noqa: BLE001 解析失败=空(恒拒)
            policy.search_host = ""

    # SessionLog 接总线落盘订阅(事件一入内存即入真源队列)
    engine_owners: list[str] = []              # 本会话在总线上持有的属主(收尾按属主摘除)
    if attach_persistence:
        if bus is not None:
            # 属主含**租户**(R12):多租户共用总线时,同名 sid 可能并存(会话目录按
            # 租户分目录)—— 属主只含 sid 会让两租户同属主,摘一个连坐另一个。
            owner = f"engine:{_tenant_of(log_)}:{_sid_of(log_)}"
            engine_owners.append(owner)
            for t in _EVENT_TYPES_OR_ALL():
                bus.subscribe(t, _record_to(store, _sid_of(log_)), owner=owner)
        if store is not None:
            log_._bus = bus                  # append → 分发 → 落盘闭环

    # 治理证据装配(S5-2b):**构造 → 订阅 → 注入**(顺序不可颠倒,否则首事件丢失)
    # ``EvidenceCollector`` 的索引是**只读派生缓存**(可由事件日志完全重建,INV-E3);
    # 它只**消费**事件、不产生事件——唯一写点仍是 ``archive()`` 的 evidence.archived。
    # 订阅 owner 与落盘订阅区分开,便于 deactivate 时各自摘除。
    evidence = EvidenceCollector(session=log_)
    if bus is not None:
        ev_owner = f"governance-evidence:{_tenant_of(log_)}:{_sid_of(log_)}"
        engine_owners.append(ev_owner)
        for t in _EVIDENCE_EVENT_TYPES:
            bus.subscribe(t, evidence.on_event, owner=ev_owner)

    # 治理审计装配(S5-3b):**只构造、只注入** —— ``AuditSystem`` 是 **replay-only**
    # (仅重放)的派生视图:**不订阅**任何事件、**不缓存**跨调用状态。**默认零写**;
    # 唯一写点是 ``reconcile(emit=True)`` 的既有 ``syscheck.fail``。
    # S5-4:旧审计面兼容适配**经注入**(治理层不得 import core,ADR-018:308)。
    from pyharness.core.telemetry import session_audit as _legacy_audit
    audit = AuditSystem(session=log_, legacy_audit=_legacy_audit)

    spine = EngineSpine(
        session=log_, bus=bus, registry=registry,
        scope=scope, llm=llm_client, tools=tools,
        guard=guard, approval=approval,
        governance=GovernanceContext(policy=gov_policy,
                                     decisions=DecisionEngine(),
                                     receipts=ReceiptStore(),
                                     evidence=evidence,
                                     audit=audit),
        counters=counters, sysprompt=sysprompt, compactor=compactor,
        goals=goals, todos=todos, ask=ask, skills=skills,
        plugins=plg_mgr, plugin_state=plg_state, tool_registry=tool_reg,
        streaming=bool(getattr(getattr(cfg, "loop", None), "streaming", False)),
        loop=loop, sys=None, settings=cfg,
        storage=EngineStorage(sessions_dir=sessions_dir),
        persistence=store, plan=plan, schedule=schedule,
        budget=budget_gate,
        search_backend=search_backend,
        llm_probe=probe_task,
        channel=channel)
    spine.owns_persistence = attach_persistence
    spine.llm_runtime = llm_runtime
    spine.owns_llm_runtime = owns_llm_runtime
    # 真实编排适配层:jobs/subagent 不再停在 runner=None 的模块单测状态。
    # SubagentManager 的 announce 使用 tool_registry 直接绑 Provider;模型经
    # 正常 ToolExecutor 看到 subagent.spawn。
    from pyharness.core.jobs import JobManager
    from pyharness.core.orchestration import (EngineIntentRunner,
                                              EngineSubagentRunner)
    from pyharness.core.subagent import SubagentManager
    spine.jobs = JobManager(session=log_, task_queue=None,
                            runner=EngineIntentRunner(spine), bus=bus)
    sub_runner = EngineSubagentRunner(spine, sessions_dir,
                                      evidence_producer=_evidence_producer)
    spine.subagent = SubagentManager(
        session=log_, runner=sub_runner, parent_scope=scope,
        tools=tool_reg, bus=bus, cfg=cfg,
        session_factory=sub_runner.child_session)
    # N3/M5-3:证据生产者订阅**段关闭**(而非 run_for_task 的函数体)。
    # 订阅面与落盘订阅 owner 区分开,便于 deactivate 时各自摘除。
    if bus is not None:
        prod_owner = f"governance-evidence-producer:{_tenant_of(log_)}:{_sid_of(log_)}"
        engine_owners.append(prod_owner)
        bus.subscribe(
            "segment.end", _evidence_producer(spine), owner=prod_owner)
    spine.bus_owners = engine_owners
    # R12-3:审批 → 队列联动接线(惰性取值:队列可能由 attach/外壳在**之后**挂上
    # spine.task_queue)。挂起审批时真挂起本会话队列,恢复时真恢复 —— 使
    # `queue.suspended/resumed` 事件与 `queue.status().paused` 一致。
    try:
        spine.approval._queue_getter = lambda: spine.task_queue
    except Exception:                            # noqa: BLE001 旧形状 provider:跳过
        log.debug("approval 队列联动未接线(provider 形状不支持)")
    return spine


def _sid_of(log_: Any) -> str:
    """会话 id 取值(`SessionLog.sid`;缺省 '?')。

    2026-09-21 修:此前三处订阅属主写 ``getattr(log_, 'session_id', '?')`` ——
    ``SessionLog`` 只有 ``.sid`` ⇒ 属主恒为 ``...:?``,**所有会话共用同一个属主串**
    (既是错误标识,又会让"按属主摘除"误伤其他会话)。同 PIT-16 的属性名错第二例。
    """
    return str(getattr(log_, "sid", "?") or "?")


def _tenant_of(log_: Any) -> str:
    """会话租户取值(订阅属主用;缺省 '' 表示默认/未声明)。

    2026-09-21 R12:属主串必须含**全部隔离维度**(租户 × 会话)—— 会话目录按租户
    分目录,同名 sid 跨租户并存合法;属主只含 sid 时,两租户同属主 ⇒ 关一个连坐另一个。
    """
    return str(getattr(log_, "tenant_id", "") or "")


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


_EVIDENCE_EVENT_TYPES: tuple[str, ...] = (
    "evidence.archived",                 # 证据工件(索引主来源)
    "segment.start", "segment.end",      # 段锚(把引用解析到 task)
    "decision.issued", "receipt.emitted",  # 锚 → seq(解析 decision_id/receipt_id)
)
"""治理证据订阅面(S5-2b):``EvidenceCollector.on_event`` 只**消费**这些事件以建
**只读派生索引**。**不订阅** ``tool.result``(属工具执行因果关系,由 S5-3 Audit 承担)。"""


def _record_to(store: Any, session_id: Optional[str] = None):
    """总线 → 存储订阅(**委托唯一实现** ``persistence.session_recorder``)。

    2026-09-21 R8 修:此前本函数**没有 sid 过滤** —— 共享总线上会把他会话事件串写进
    本会话 JSONL(实测:A 文件里出现 B 的 ``session.created``)。桌面壳的同款订阅早有
    该过滤并注明"过滤责任在订阅者",engine 侧长期缺失 ⇒ 同一横切关注点两处实现、
    其中一处漏了关键判据。现两处都走 ``session_recorder``。

    ``session_id`` 缺省(None/""):**不做过滤** —— 仅供单会话总线/测试替身使用;
    生产调用点(见 ``build_runner_components``)必须传真实 sid,故此处对缺省发出告警
    使"忘记传"可见(不再静默)。
    """
    from pyharness.persistence import session_recorder
    if not session_id:
        log.warning("落盘订阅未提供 sid:跳过跨会话过滤(仅适用于单会话总线)")
    return session_recorder(store, str(session_id or ""))


# ---------------------------------------------------------------- runner seam
def _owned_task_message(log_: Any, task: Any) -> Optional[Any]:
    """严格归属窗口:取 (上一个 task.enqueued, 本任务 enqueued] 内最后一条 user.message。

    修复(P1-1):旧实现取"enqueued 之前最后一条 user.message",会把 plan 步骤的
    /plan 旧消息顶给步骤执行(plan_mode 提交纯意图,不落 user.message),并发下还
    会让 schedule 任务拿到前台消息。窗口为空(纯意图任务)→ None,由调用方按
    task.intent 补写信号消息。只读遍历事件源,零副作用。
    """
    mark = int(getattr(task, "enqueued_seq", 0) or 0)
    if mark <= 0:
        return None
    prev_enq = 0
    env = None
    for e in log_.events_after(0):
        seq = int(getattr(e, "seq", 0) or 0)
        if seq > mark:
            break
        if e.type == "task.enqueued":
            if seq < mark:
                prev_enq = seq
                env = None                      # 新窗口:清空旧归属(前窗口消息不复用)
            continue                             # 本任务自身 enqueued:非消息
        if e.type == "user.message" and seq > prev_enq:
            env = e
    return env


def make_runner(spine: EngineSpine) -> EngineRunner:
    """task_queue runner seam:async run_for_task(task) → loop.wake(env)。

    task 的配套 user.message 已由 schedule.trigger/前台 submit 落盘(seq 已知);
    经严格归属窗口从事件源找该 envelope,窗口为空则按 task.intent 补写,交
    loop.wake 驱动真实处理。
    """
    log_ = spine.session
    loop = spine.loop

    async def run_for_task(task: Any) -> Any:
        # GAP-13:攒批落盘定时器惰性启动(幂等)。放在此处而非装配段,是因为装配
        # 段是同步函数、且此时可能尚无运行中的事件循环——本 seam 是 chat/run 的
        # 唯一生产驱动点,循环必已就绪。
        try:
            await spine.start_flush_ticker()
        except Exception as e:                 # noqa: BLE001 定时器起不来不阻断任务
            log.warning("flush ticker 启动失败: %s", type(e).__name__)
        # 懒预载:created 已落后首跑前装载示例插件(util.now;EVT-106 首事件
        # 约束 → 不可在 open_session 后立即 append,故延迟到 submit 时刻)
        if not getattr(spine, "_plugins_ready", False):
            try:
                await _preload_plugins(spine)
            except Exception as e:             # noqa: BLE001 预载失败不阻断
                log.debug("plugin lazy preload failed: %s", type(e).__name__)
            spine._plugins_ready = True
        # 装配期策略留痕(F-01):**必须在 session.created 之后**发射——装配函数
        # (_build_governance/activate_orchestration)运行时 seq=0,直接 append 会被
        # EVT-106 拒("事件须在 session.created 之后")并让整个装配抛出。本处是
        # 生产唯一驱动点(chat/run 均经 task_queue → 本 seam),此时会话已开启。
        # 幂等闸保证每 spine 恰一条;发射失败不阻断任务(与上方插件预载同款)。
        if not getattr(spine, "_policy_announced", False):
            _pol = getattr(getattr(spine, "governance", None), "policy", None)
            if _pol is not None:
                try:
                    await _pol.emit_assembled(reason="assembly")
                except Exception as e:         # noqa: BLE001 治理留痕失败不阻断任务
                    log.warning("policy.updated 装配留痕失败: %s", type(e).__name__)
            spine._policy_announced = True
        # 崩溃遗留 job 的**事件级**恢复(B1/R24):补写 `job.failed(reason=crash)`。
        # 放在本 seam(而非装配期):装配期 seq=0,append 会被 EVT-106 拒;此处是生产
        # 唯一驱动点且会话已开启 —— 与上方策略留痕同款。幂等闸保证每 spine 恰一次;
        # 失败只记日志不阻断任务(恢复属收尾)。
        if not getattr(spine, "_jobs_recovered", False):
            jobs = getattr(spine, "jobs", None)
            rec = getattr(jobs, "recover", None)
            if callable(rec):
                try:
                    await rec()
                except Exception as e:             # noqa: BLE001 恢复失败不阻断
                    log.warning("job 崩溃恢复失败: %s", type(e).__name__)
            spine._jobs_recovered = True
        intent = str(getattr(task, "intent", "") or "").strip()
        env = _owned_task_message(log_, task)   # 严格归属窗口(P1-1)
        if env is None and intent:
            # 窗口内无配套 user.message(plan 步等纯意图任务):意图本身即待执行
            # 指令,按 schedule 前例落 user.message(system 署名)驱动循环——
            # 提交者不落 message 则 agent 无从 derive 到该任务(schedule._fire 同款)。
            env = await log_.append("user.message", {"content": intent},
                                    actor="system",
                                    origin=f"task:{getattr(task, 'id', '')}",
                                    task_id=getattr(task, "id", None),
                                    sync=True)
        if env is None:
            raise PyHError("CYC-999", ctx={"hint": "runner 找不到对应 user.message 事件"})
        ag_ctx = await _agent_ctx_of(spine, env)
        # 当前任务关联(F044 弱耦合字符串引用;2026-09-21 R11-6 修):
        # goal/todo 工具与 llm.chunk 出口经 ``ctx.task_id`` 取"当前任务",而此前
        # **全库无任何写入点** ⇒ 恒 None ⇒ ``goal`` 的"所关联任务失败 → 目标状态
        # 推导"(goal.py:357)与"关联任务"渲染**从未生效**。任务在同一会话内串行
        # (单 running),故按任务边界设置/清除安全;每个会话(含子会话)各自持 ctx,
        # 不跨会话串。异常/取消路径经 finally 还原。
        prev_task_id = getattr(ag_ctx, "task_id", None)
        ag_ctx.task_id = getattr(task, "id", None)
        # KF-C(A4/R24):把**框架侧**"本轮来源"提到 ctx —— 到点触发那一轮此前**无任何
        # 通道**告知模型"这是定时触发" ⇒ 模型只会复述任务状态("提醒已设好"),而不是
        # 产出提醒本身。取值只取 `task.meta.source`(**受控**:由 schedule._fire 写入),
        # 不取任何模型/工具文本(注入防御);由 system_prompt 的受控段渲染。
        prev_src = getattr(ag_ctx, "turn_source", None)
        ag_ctx.turn_source = _turn_source_of(task)
        try:
            res = await loop.wake(env, ctx=ag_ctx)
        finally:
            ag_ctx.task_id = prev_task_id
            ag_ctx.turn_source = prev_src
        # F042/#7 自动标题:run 正常完成后命名一次(幂等;LLM 失败静默留空)
        if (res is not None and getattr(res, "reason", "") == "complete"
                and not getattr(spine, "_auto_titled", False)):
            try:
                from pyharness.core.auto_title import auto_title
                if await auto_title(ag_ctx):
                    spine._auto_titled = True
            except Exception as e:             # noqa: BLE001 命名失败不阻断对话
                log.debug("auto_title failed: %s", type(e).__name__)
        # 证据归档**不在此处**:触发点已移到段生命周期(segment.end 订阅,见
        # ``_evidence_producer``)。原因:本函数在五种终态中的三种(异常/预算/取消)
        # 不执行到收尾,放这里会漏归档;且此处早于段关闭(旧 N3)。
        return res

    async def cancel_current(task_id: Optional[str] = None) -> None:
        cur = loop.current
        if cur is not None:
            cur.cancelled = True
            # 协作式:由 loop 轮内 CancelledError 传播收尾
    return EngineRunner(run_for_task=run_for_task,
                        cancel_current=cancel_current)


# ---------------------------------------------------------------- 证据归档(GAP-8)
async def archive_task_evidence(spine: EngineSpine, task_id: str,
                                ctx: Any = None) -> Optional[str]:
    """任务段治理证据归档——``EvidenceCollector`` 的**生产生产者**(GAP-8)。

    修复前:``EvidenceCollector`` 已订阅真总线(4 类事件),但**唯一写点**
    ``archive()`` 在全库**零调用** ⇒ ``evidence.archived`` 永不产生、索引恒空、
    ``collect_for_task()`` 恒返回 ``()``。

    **产生规则(冻结)**——这是一个**设计裁定**,不是"为了有调用点而调用":

    1. **单位 = 任务段**(``task_id``)。段是回放/复算/预算审计的最小单位(F044),
       也正是 ``collect_for_task`` 的聚合键;证据按段组织才有查询语义。
    2. **触发条件 = 段内至少一条 ``decision.issued``**。治理证据回答的是"这次
       授权/拒绝发生在什么上下文、依据什么、能否复核",没有治理决策的段**不归档**
       ——"不发生"与"失败"是两件事,不为空段造工件。
    3. **只存引用,不复制事件内容**(INV-G4/E1):refs 指向段锚 + 段内每条决策
       (``decision_id``)与凭证(``receipt_id``)。事件日志仍是唯一真源。
    4. **幂等**:同一 ``task_id`` 只归档一次——判据取自**日志重放**(已存在指向该
       段锚的 ``evidence.archived`` 即跳过),故**重启后重放同样成立**,不依赖内存。
    5. **降级**:治理层未接线(纯内存/单测)、无段锚、引用构造失败 ⇒ 静默跳过,
       绝不阻断任务(证据是派生视图,不是主链前提)。

    返回 ``evidence_id``;未归档(不满足条件)返回 ``None``。
    """
    log_ = getattr(spine, "session", None)
    coll = getattr(getattr(spine, "governance", None), "evidence", None)
    if log_ is None or coll is None:
        return None
    try:
        events = list(log_.events_after(0))
        seg_locator = f"{task_id}:seg"
        # ① 幂等闸:日志重放判定(重启后仍成立)
        for e in events:
            if getattr(e, "type", None) != "evidence.archived":
                continue
            for r in (getattr(e, "payload", None) or {}).get("refs") or []:
                if (isinstance(r, dict) and r.get("kind") == "segment"
                        and str(r.get("locator")) == seg_locator):
                    return None
        # ② 段窗口:本任务 segment.start 之后的全部事件属该段
        start = next((int(e.seq) for e in events
                      if getattr(e, "type", None) == "segment.start"
                      and str((getattr(e, "payload", None) or {})
                              .get("task_id") or "") == str(task_id)), None)
        if start is None:
            return None
        seg = [e for e in events if int(getattr(e, "seq", 0) or 0) >= start]
        decisions = [e for e in seg
                     if getattr(e, "type", None) == "decision.issued"]
        if not decisions:
            return None                       # 无治理事实:不归档(非失败)
        receipts = [e for e in seg
                    if getattr(e, "type", None) == "receipt.emitted"]
        denied = [d for d in decisions
                  if str((getattr(d, "payload", None) or {})
                         .get("verdict") or "") == "reject"]
        refs: list[dict] = [{"kind": "segment", "locator": seg_locator}]
        refs += [{"kind": "decision_id",
                  "locator": str((getattr(d, "payload", None) or {})
                                 .get("decision_id") or "")}
                 for d in decisions]
        refs += [{"kind": "receipt_id",
                  "locator": str((getattr(r, "payload", None) or {})
                                 .get("receipt_id") or "")}
                 for r in receipts]
        refs = [r for r in refs if r["locator"]]          # 空 locator 不合法,剔除
        # 终态标量(N3/M5-3):``task.completed`` / ``task.failed`` **先于**
        # ``segment.end`` 落盘,故此刻可读。以**标量并入 claim** —— 不新增 payload
        # 字段(INV-G4「只存引用」;payload schema 变更属 H-3,不在本轮范围)。
        term = _terminal_state_of(seg, str(task_id))
        ev = await coll.archive(
            claim=f"task {task_id} 的治理事实可追溯(终态={term})",
            refs=refs, ctx=ctx or SimpleNamespace(session=log_),
            summary=(f"终态={term};决策 {len(decisions)} 条(拒绝 {len(denied)})、"
                     f"凭证 {len(receipts)} 条"))
        return getattr(ev, "evidence_id", None)
    except Exception:                          # noqa: BLE001 派生视图:绝不阻断主链
        log.warning("证据归档跳过 task=%s", task_id, exc_info=True)
        return None


def _terminal_state_of(seg_events: list, task_id: str) -> str:
    """段内终态标量(供 claim 标注;N3/M5-3)。

    ``task.completed`` → ``success``;``task.failed`` → ``cancelled`` 或
    ``error:<code>``;两者皆无(段被外力关闭)→ ``unknown``。
    """
    for e in reversed(seg_events):
        t = getattr(e, "type", None)
        if t == "task.completed":
            return "success"
        if t == "task.failed":
            p = getattr(e, "payload", None) or {}
            if str(p.get("reason") or "") == "cancelled":
                return "cancelled"
            return f"error:{p.get('error') or 'unknown'}"
    return "unknown"


def _evidence_producer(spine: EngineSpine):
    """段关闭时的证据生产者回调(**N3/M5-3 的触发点**)。

    **为什么不放在 ``run_for_task`` 的函数体里**:该函数跑在独立子任务中
    (``task_queue._run_task`` 的 ``create_task``),五种终态里有三种(异常/预算/取消)
    会让它**不执行到收尾**⇒ 段内有治理决策却零证据(实测 3/5)。而 ``segment.end``
    由 ``_run_task`` 自身 ``finally`` 中的 ``close_segment`` 落盘,``_run_task``
    **不被取消**(取消只作用于子任务)⇒ 实测 **5/5** 落盘。挂到该事件即继承此保证,
    并天然消除 N3(生产者不再早于段关闭)。

    幂等由 ``archive_task_evidence`` 内的日志重放闸保证;失败在该函数内吞掉
    (派生视图不阻断主链),故本回调不会影响段关闭。
    """
    async def _on_segment_end(type_: str, env: Any) -> None:
        if getattr(env, "type", None) != "segment.end":
            return
        task_id = str((getattr(env, "payload", None) or {}).get("task_id") or "")
        if not task_id:
            return
        await archive_task_evidence(spine, task_id)
    return _on_segment_end


def _turn_source_of(task: Any) -> Optional[str]:
    """本轮**框架侧来源**标记(KF-C/A4):只取 ``task.meta["source"]``,其余一律 None。

    受控取值(非模型文本):``schedule._fire`` 写入 ``{"source": "schedule:<name>"}``;
    普通用户轮无该键 → None(提示词里不出现该段)。
    """
    meta = getattr(task, "meta", None) or {}
    if not isinstance(meta, dict):
        return None
    src = str(meta.get("source") or "").strip()
    return src or None


async def _agent_ctx_of(spine: EngineSpine, env: Any) -> Any:
    """agent_loop 消费的 ctx = create_agent 装配的 Agent.ctx(spine 绑定)。

    Agent 与 loop 1:1;ctx.session/scope/llm 即 spine 组件——Agent.ctx 由
    create_agent 构造并绑定 loop。env 仅作输入(loop.wake 已携带)。
    首建 agent 时顺带激活 storage 能力(spill 定位器,幂等)。
    """
    sid = str(getattr(env, "session_id", "") or getattr(spine.session, "sid", "")
              or "")
    ag = spine.active_agents.get(sid) if hasattr(spine, "active_agents") else None
    if ag is None:
        ag = create_agent(sid, spine, spine.settings or _cfg_of(spine))
        spine.active_agents[sid] = ag
        await _activate_storage_caps(ag, spine)     # spill 首访激活(F039)
        await _activate_session_caps(ag, spine)     # FTS 检索能力激活(F057)
    return ag.ctx


async def _activate_storage_caps(ag: Any, spine: EngineSpine) -> None:
    """storage 能力激活(F039):SpillProvider enter(建私有目录+订阅清理)+挂定位器。

    ctx.storage 与 spine.storage 同对象引用 → 激活后 executor 关4 读
    ctx.storage.spill 立即可见。会话 1:1 单 agent,激活一次;失败记日志不阻断
    对话(未激活时超长输出走 PERS-221 截断兜底)。"""
    if getattr(spine, "_spill_provider", None) is not None:
        return
    try:
        from pyharness.core import spill as _spill_mod
        prov = _spill_mod.SpillProvider(ctx=ag.ctx)
        await prov.enter(ag.ctx)
        spine._spill_provider = prov
        storage = getattr(spine, "storage", None)
        if storage is not None:
            storage.spill = prov
        log.info("spill provider activated sid=%s", ag.session_id)
    except Exception as e:                         # noqa: BLE001 激活失败:截断兜底,不阻断
        log.warning("spill activate skipped: %s", type(e).__name__)
    # #53 会话 KV(SessionKV;同步 JSON 落盘,懒激活幂等)
    if getattr(spine, "_kv_activated", False):
        return
    try:
        from pyharness.core import kv as _kv_mod
        base = Path(str(getattr(getattr(spine, "storage", None),
                                "sessions_dir", "."))).expanduser()
        kv = _kv_mod.SessionKV(base / f"{ag.session_id}.kv.json",
                               session_id=ag.session_id)
        storage = getattr(spine, "storage", None)
        if storage is not None:
            storage.kv = kv
            spine._kv_activated = True
        log.info("session kv activated sid=%s", ag.session_id)
    except Exception as e:                         # noqa: BLE001 激活失败不阻断
        log.warning("kv activate skipped: %s", type(e).__name__)


async def _activate_session_caps(ag: Any, spine: Any) -> None:
    """会话检索能力激活(F057):开 FTS 派生索引 → 注册 session.fts_query 工具
    → 挂 ctx.session.fts 与 ctx.session_query(repair 落后对账读数面)。

    幂等(spine._fts_entered 旗标);失败(磁盘/权限/SQLite 无 FTS5)只告警不阻断
    ——索引是派生视图,可整体重建(原则 1)。db 路径与 CLI `search` 同源
    (storage.db_path)。2026-09-12 修复:此前 SessionQueryIndex 有完整实现+单测,
    但装配层零调用点 → LLM 调 session.fts_query 必抛 TLB-802"索引未挂载"。
    """
    if getattr(spine, "_fts_entered", False):
        return
    spine._fts_entered = True                      # 试过就不再重试(索引可选)
    idx = None
    try:
        from pyharness.core.session_query import SessionQueryIndex
        cfg = getattr(spine, "settings", None) or _cfg_of(spine)
        db = Path(str(getattr(getattr(cfg, "storage", None), "db_path",
                              "~/.pyharness/index.db"))).expanduser()
        sid = str(getattr(ag, "session_id", "")
                  or getattr(spine.session, "sid", ""))
        idx = SessionQueryIndex(db, sources={sid: spine.session})
        spine.fts = idx                           # ownership before first await
        await idx.enter(ag.ctx)                    # 开库建表 + 总线订阅
        await idx.announce(ag.ctx)                 # 注册 session.fts_query + 挂载
        spine.fts = idx
        ag.ctx.session_query = idx                 # F060 对账读数(repair 用)
        log.info("session fts activated sid=%s db=%s", sid, db)
    except BaseException as e:
        if idx is not None:
            cleanup = asyncio.create_task(idx.detach(None))
            cancelled = None
            while True:
                try:
                    await asyncio.shield(cleanup)
                    spine.fts = None
                    break
                except asyncio.CancelledError as cancellation:
                    if cleanup.done():
                        raise
                    cancelled = cancellation
                except BaseException as cleanup_error:
                    e.add_note(f'index cleanup failed: {type(cleanup_error).__name__}')
                    raise e
            if cancelled is not None:
                cancelled.add_note(f'index activation failed: {type(e).__name__}')
                raise cancelled from e
        if not isinstance(e, Exception):
            raise
        # Optional index failures remain visible; cancellation is never degraded.
        log.warning("fts activate skipped: %s", type(e).__name__)


def _cfg_of(spine: Any) -> Any:
    cfg = getattr(spine, "settings", None)
    if cfg is None:
        from pyharness.config import load_settings
        cfg = load_settings()
        spine.settings = cfg
    return cfg


# ---------------------------------------------------------------- 顶层装配
async def assemble_real_engine(cfg: Any, *, sid: str,
                               sessions_dir: Path,
                               channel: Any = CHANNEL_UNDECLARED,
                               tenant_id: Optional[str] = None) -> EngineContext:
    """一键真实引擎 —— **演示 / 探针 / e2e 装配入口**。

    **生产外壳不走本函数**（2026-09-21 R18 更正）：CLI/ACP 走 ``attach_engine_to_ctx``，
    Desktop/服务层走 ``build_runner_components``（per-session 懒装配，复用 manager 已
    open 的 log_/bus/store）。本函数在 ``pyharness/`` 内**零调用者**，实际使用方是
    ``scripts/demo_*`` / ``scripts/probe_*`` / ``scripts/e2e_engine.py``（F-08 已登记
    "死装配"；原 docstring 称 "desktop create_message 用" 与事实不符，本次更正）。

    返回 ctx:与 cli.assemble_ctx 同形状(session/bus/llm/scope/task_queue/
    make_runner)+ engine 脊柱。顺序:先 build_spine(会话级共享计数器就绪)→
    register_default_llm(counters=spine.counters)(适配器计量与 scope 预算闸
    同源)→ session.created 首事件、enter/close 生命周期由调用方负责。
    生产路径同样满足"同源"，但**靠另一条机制**：适配器构造时未注入 counters ⇒
    调用期回落 ``ctx.counters``，而 ``create_agent`` 无条件把 ``spine.counters``
    播到 agent ctx（``core/agent.py``）。

    channel(GAP-11):外壳身份声明。**生产外壳必须传**;未声明时该引擎的任何
    工具授权都会 APR-503 fail-closed。
    """
    from pyharness.core.task_queue import TaskQueue, queue_kwargs_of

    spine = await build_spine(cfg, sid=sid, sessions_dir=sessions_dir,
                              channel=channel, tenant_id=tenant_id)
    try:
        runner = make_runner(spine)
        queue = TaskQueue(session=spine.session, runner=runner,
                          **queue_kwargs_of(cfg))      # F043 队深由装配层注入(N1)
        spine.task_queue = queue
        ctx = EngineContext(
            settings=spine.settings, bus=spine.bus, session=spine.session,
            llm=spine.llm, scope=spine.scope, loop=spine.loop,
            tools=spine.tools, registry=spine.registry,
            guard=spine.guard, approval=spine.approval,
            counters=spine.counters,
            agent=None, task_runner=runner,
            make_runner=lambda: make_runner(spine),
            storage=spine.storage,     # 与 spine 同对象:spill 激活后立即可见
            engine_spine=spine, task_queue=queue, budget=spine.budget,
            governance=spine.governance)
        ctx.search_backend = spine.search_backend
        await activate_orchestration(spine, task_queue=queue)
        return ctx
    except BaseException as primary:
        await _rollback_spine(spine, primary)
        raise


async def attach_engine_to_ctx(ctx: Any, cfg: Any, *, log_: Any,
                               sessions_dir: Path, bus: Any = None,
                               store: Any = None,
                               channel: Optional[str] = None,
                               preload: bool = True) -> EngineSpine:
    """把真实引擎接进轻量 ctx(CLI/ACP 共用):补 spine、runner、真实队列。

    desktop 已按 per-session 懒装配自己的 spine;CLI/ACP 复用同一装配原语,
    避免轻量门面(ctx.task_queue=None / runner=None)实际不可用。调用方可传
    已 open 的 store(若总线落盘已有 owner,则 attach_persistence=False)。
    """
    from pyharness.core.task_queue import TaskQueue, queue_kwargs_of

    runtime = getattr(ctx, 'llm_runtime', None)
    if runtime is None:
        runtime = ctx.llm_runtime = llm_mod.LLMRuntime()
    spine = build_runner_components(
        cfg, log_=log_, bus=bus or getattr(ctx, "bus", None),
        sessions_dir=sessions_dir,
        store=store if store is not None else getattr(log_, "_persistence", None),
        attach_persistence=False, channel=channel, llm_runtime=runtime)
    try:
        ctx.engine_spine = spine  # ownership precedes the first await
        if preload:
            try:
                await _preload_plugins(spine)
                spine._plugins_ready = True
            except Exception:                       # noqa: BLE001 预载失败不阻断
                log.warning("cli/acp plugin preload skipped", exc_info=True)
        runner = make_runner(spine)
        ctx.engine_spine = spine
        ctx.llm = spine.llm
        ctx.scope = spine.scope
        ctx.tools = spine.tools
        ctx.guard = spine.guard
        ctx.governance = getattr(spine, "governance", None)   # 治理层单实例(S2-3)
        ctx.budget = spine.budget                      # F032 只读预算门面(仪表盘)
        ctx.search_backend = getattr(spine, "search_backend", None)
        ctx.approval = spine.approval
        try:
            spine.approval.set_default_channel(channel)   # N5:归一化覆写,不直写 _channel
        except Exception:                              # noqa: BLE001 只读门面:尽力
            pass
        ctx.task_runner = runner
        ctx.make_runner = lambda: runner
        ctx.task_queue = TaskQueue(session=log_, runner=runner,
                                   **queue_kwargs_of(cfg))   # F043 队深同源注入
        spine.task_queue = ctx.task_queue
        await activate_orchestration(spine, task_queue=ctx.task_queue)
        return spine

    except BaseException as primary:
        await _rollback_spine(spine, primary)
        raise
