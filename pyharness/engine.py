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
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING
from pyharness.core.task_queue import Task, TaskQueue
from pyharness.core.task_runner import RunnerResult, TaskLifecycle, TaskRunner
from pyharness.engine_types import (log, EngineStorage, EngineSpine, EngineRunner, EngineContext)
from pyharness.engine_policy import (_make_chain, _cfg_probe_interval, _policy_preset, _cfg_max_arg_failures, _apply_preset)
from pyharness.engine_components import (_build_governance, build_runner_components as _build_runner_components)
from pyharness.engine_plugins import (_preload_plugins, _preload_mcp, _SpineCtx, _cfg_of)
from pyharness.engine_events import (_sid_of, _tenant_of, _EVENT_TYPES_OR_ALL, _EVIDENCE_EVENT_TYPES)
from pyharness.engine_runner import (_owned_task_message, make_runner as _make_runner, archive_task_evidence, _terminal_state_of, _evidence_producer, _turn_source_of, _agent_ctx_of, _activate_storage_caps, _activate_session_caps)

if TYPE_CHECKING:
    from pyharness.config import Settings
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core.compaction import Compactor
    from pyharness.core.goal import GoalManager
    from pyharness.core.jobs import JobManager
    from pyharness.core.kv import SessionKV
    from pyharness.core.schedule import Scheduler
    from pyharness.core.skill import SkillManager
    from pyharness.core.spill import SpillProvider
    from pyharness.core.subagent import SubagentManager
    from pyharness.core.system_prompt import SystemPromptAssembler
    from pyharness.core.todo import TodoManager
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.tools_guard import GuardChain
    from pyharness.core.tools_registry import ToolRegistry
    from pyharness.core.user_ask import AskProvider
    from pyharness.core.budget import BudgetGate

from pyharness.bus import EventBus, bus_kwargs_of
from pyharness.bus.registry import Registry
from pyharness.core.agent import CHANNEL_UNDECLARED, create_agent
from pyharness.core.agent_loop import AgentLoop
from pyharness.core import llm as llm_mod
from pyharness.core.scope import (BudgetLimits, Scope, ScopePolicy,
                                  session_workspace)
from pyharness.core.session import open_session, SessionLog
from pyharness.errors import PyHError, raise_code
from pyharness.persistence import flush_kwargs_of, open_store


# ---------------------------------------------------------------- 适配器注册


    # strict:保持默认(无操作)


# ---------------------------------------------------------------- spine 装配
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


"""治理证据订阅面(S5-2b):``EvidenceCollector.on_event`` 只**消费**这些事件以建
**只读派生索引**。**不订阅** ``tool.result``(属工具执行因果关系,由 S5-3 Audit 承担)。"""


# ---------------------------------------------------------------- runner seam


# ---------------------------------------------------------------- 证据归档(GAP-8)


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


async def prepare_plugins(spine):
    """Host-facing idempotent plugin lifecycle boundary."""
    if not getattr(spine, '_plugins_ready', False):
        await _preload_plugins(spine)
        spine._plugins_ready = True

async def agent_context(spine, event):
    """Host-facing agent materialization boundary."""
    return await _agent_ctx_of(spine, event)

def apply_preset(config, policy, registry, *, scope=None):
    """Compile a permission preset through the engine's canonical policy logic."""
    return _apply_preset(config, policy, registry, scope=scope)


def compile_preset(spine, preset):
    """Prepare policy/settings without mutating the running session on failure."""
    import copy
    config=copy.deepcopy(spine.settings)
    policy=copy.deepcopy(spine.scope.policy)
    config.security.policy.preset=preset
    policy.deny_tools=set()
    policy.sandbox_level='strict'
    config.security.sandbox.level='strict'
    apply_preset(config,policy,spine.tool_registry)
    return config,policy


def make_runner(spine: EngineSpine) -> EngineRunner:
    """Create a runner with the configured startup hook owned by this facade."""
    return _make_runner(spine, preload_plugins=_preload_plugins)


def build_runner_components(cfg: Any, *, log_: Any, bus: EventBus,
                            sessions_dir: Path, store: Any = None,
                            attach_persistence: bool = True,
                            channel: Any = CHANNEL_UNDECLARED,
                            llm_runtime: Any = None, register_model: bool = True) -> EngineSpine:
    """Assemble tools while keeping model registration at its governed entry."""
    return _build_runner_components(cfg, log_=log_, bus=bus, sessions_dir=sessions_dir,
        store=store, attach_persistence=attach_persistence, channel=channel,
        llm_runtime=llm_runtime, register_model=register_model,
        register_llm=register_default_llm, record_to=_record_to, workspace_for=_workspace_for_session)


def _workspace_for_session(cfg, log_):
    """Derive and prepare the session workspace at the engine entry boundary."""
    ws_root = Path(getattr(cfg.storage, "workspaces_dir", "~/.pyharness/workspaces")
                   ).expanduser()
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace_root = session_workspace(str(ws_root), str(getattr(log_, "sid", "")))
    Path(workspace_root).mkdir(parents=True, exist_ok=True)
    # AgentLoop(真 cfg:max_turns=30 权威默认)
    return workspace_root
