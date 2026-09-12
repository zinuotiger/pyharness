"""pyharness/engine.py — 真实引擎装配(desktop/CLI 共用)。

一句话职责:从分层配置 Settings 装配可跑的真实引擎——store(JSONL 真源)→
SessionLog → AgentLoop(真 LLM 已注册)→ Scope(strict 最小权限)→
Registry → agent_loop runner seam(task_queue.run_for_task = 找 user.message
envelope → loop.wake),供桌面壳/交互 CLI 把"提问"真正变成 Agent 干活。

安全基线:scope 默认 strict(sandbox_level="strict" 禁高危工具);llm.api_key
走 secret-ref(env:/file:),绝不落盘明文;无 key 时降级明确报错不猜测。

偏离说明(契约以各 specs 为准,此处为装配层取舍):
1. 内置能力全量注册(非空注册表):fs.* 4 工具 + storage.spill/kv + goal/todo +
   web.search/fetch + exec.*/proc.* + user.ask + skill.*,共 18 个 Definition 真实
   进注册表;strict 档下按 scope 域显隐(workspace 域 fs/storage 放行、session/
   self 域恒可见、exec/web 需 basic 档或 allowlist),所以"模型看得见几个工具"
   由策略决定,不由装配决定。[2026-09-12 更正:此前本注释写"默认不注册任何工具
   Provider(registry 空)",与同一函数 L427-486 的实现相反,属过期说明。]
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

import hashlib
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

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
    _plugins_ready: bool = False
    _auto_titled: bool = False
    _spill_provider: Any = None
    _kv_activated: bool = False
    _fts_entered: bool = False
    _mcp_clients: list[Any] = field(default_factory=list)
    _orchestration_ready: bool = False
    _schedule_started: bool = False

    async def close(self) -> None:
        """关闭会话拥有的外部资源(MCP 子进程/调度泵/子任务/派生索引)。"""
        if self.fts is not None:
            try:
                await self.fts.detach(None)
            except Exception:                        # noqa: BLE001 收尾尽力
                log.warning("fts detach failed", exc_info=True)
            self.fts = None
        if self.schedule is not None:
            try:
                self.schedule.stop()
            except Exception:                        # noqa: BLE001 收尾尽力
                log.warning("schedule stop failed", exc_info=True)
        if self.subagent is not None:
            try:
                await self.subagent.detach()
            except Exception:                        # noqa: BLE001 收尾尽力
                log.warning("subagent detach failed", exc_info=True)
        while self._mcp_clients:
            client = self._mcp_clients.pop()
            try:
                await client.close()
            except Exception:                        # noqa: BLE001 收尾尽力
                log.warning("mcp client close failed", exc_info=True)
        store = self.persistence
        if store is not None:
            try:
                flush = getattr(store, "flush", None) or getattr(store, "flush_sync", None)
                if callable(flush):
                    result = flush()
                    if inspect.isawaitable(result):
                        await result
            except Exception:                        # noqa: BLE001 收尾尽力
                log.warning("persistence flush failed", exc_info=True)
            try:
                close = getattr(store, "close", None)
                if callable(close):
                    close()
            except Exception:                        # noqa: BLE001 收尾尽力
                log.warning("persistence close failed", exc_info=True)


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


# ---------------------------------------------------------------- 适配器注册
def register_default_llm(cfg: Any, *, force: bool = False,
                         counters: Any = None) -> str:
    """注册 cfg.llm.model 的真实适配器(OpenAI 兼容端点)。

    已注册同名模型 → 跳过(幂等;force=True 强制覆盖)。key 缺失 → 抛 LLM-304
    明确提示,不静默降级。counters:会话级 F029 计数器(engine 装配传入共享实例,
    使 adapter 计量入账与 scope 预算闸同源);缺省 = 适配器私有计数(旧行为)。
    返回注册的模型名。

    秘密纪律:api_key 是 "env:NAME"/"file:PATH" 引用字符串,值由适配器在
    secret_ref_ok 解析(CRED-701/702/703)——本函数不接触也不落盘明文。
    """
    llm_cfg = cfg.llm
    api_ref = str(llm_cfg.api_key or "")
    if not api_ref.startswith(("env:", "file:", "tenant:")):
        llm_mod.raise_code(
            "CRED-701", model=llm_cfg.model,
            advice="llm.api_key 须 env:NAME / file:PATH / tenant:租户:档案 秘密引用")

    def key_for(model_name: str) -> str:
        # 租户档案必须使用独立适配器键;否则两个租户配置同名模型会复用
        # 第一个租户的 base_url/key,形成跨租户凭据泄漏。
        if not api_ref.startswith("tenant:"):
            return model_name
        digest = hashlib.sha256(
            f"{api_ref}:{llm_cfg.base_url}:{model_name}".encode("utf-8")).hexdigest()[:16]
        return f"__tenant_{digest}__{model_name}"

    main_model = str(llm_cfg.model)
    main_key = key_for(main_model)
    fallback_keys = [key_for(str(m))
                     for m in (getattr(llm_cfg, "fallback_models", None) or [])]
    if llm_mod.adapters.get(main_key) and not force:
        llm_cfg.model = main_key
        llm_cfg.fallback_models = fallback_keys
        return main_key
    triple = llm_mod.AdapterTriple(base_url=str(llm_cfg.base_url),
                                   api_key_ref=api_ref, model=main_model)
    llm_mod.register_adapter(
        main_key,
        lambda: llm_mod.OpenAICompatAdapter(
            model=main_model, triple=triple,
            limits=llm_mod.TimeoutLimits.from_cfg(llm_cfg.timeout),
            cfg=cfg, counters=counters))
    for actual, registry_key in zip(
            getattr(llm_cfg, "fallback_models", None) or [], fallback_keys):
        actual = str(actual)
        if registry_key and not llm_mod.adapters.get(registry_key):
            llm_mod.register_adapter(
                registry_key,
                lambda actual=actual, registry_key=registry_key:
                    llm_mod.OpenAICompatAdapter(
                        model=actual,
                        triple=llm_mod.AdapterTriple(
                            base_url=str(llm_cfg.base_url),
                            api_key_ref=api_ref, model=actual),
                        limits=llm_mod.TimeoutLimits.from_cfg(llm_cfg.timeout),
                        cfg=cfg, counters=counters))
    llm_cfg.model = main_key
    llm_cfg.fallback_models = fallback_keys
    return main_key


def _make_chain(cfg: Any, bus: Any) -> Any:
    """FallbackChain 装配(F013/F028):adapters 引用 llm 注册表(后续注册立即可见)。

    config = Settings 权威(chain = llm.model + fallback_models;退避 llm.retry;
    降级 llm.degrade);bus 为告警/通知事件尽力出口。"""
    from pyharness.core.llm_fallback import FallbackChain
    return FallbackChain(adapters=llm_mod.adapters, config=cfg, bus=bus)


def _policy_preset(cfg: Any) -> str:
    """security.policy.preset 读取(缺省 strict;任意 cfg 形态容错)。"""
    try:
        v = getattr(getattr(getattr(cfg, "security", None), "policy", None),
                    "preset", None)
    except Exception:                                  # noqa: BLE001 非 Settings 对象
        return "strict"
    return str(v) if v in ("locked", "readonly", "standard", "strict") else "strict"


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
                      bus: Optional[EventBus] = None) -> EngineSpine:
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
    for pd in sorted(pdir.glob("*/")):
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
            client.register_into(spine.tool_registry)
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


def build_runner_components(cfg: Any, *, log_: Any, bus: EventBus,
                            sessions_dir: Path,
                            store: Any = None,
                            attach_persistence: bool = True) -> EngineSpine:
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

    # Scope(strict 最小权限 + cfg 预算);counters = 会话级共享用量计数器
    # (F029 report_usage 入账 → task_total 读数,F032 硬闸与 llm 同一实例)
    limits = BudgetLimits.from_cfg(cfg)
    policy = ScopePolicy()                     # 默认:strict / 空 deny / 空域名
    counters = llm_mod.UsageCounters()
    scope = Scope(policy, limits, session_id=str(getattr(log_, "session_id", "")),
                  counters=counters, session=log_, bus=bus)

    # 预算门面(F032 只读消费面):与 Scope 共用同一 limits/counters 实例,
    # 仪表盘读数 = 循环强制终态判据(2026-09-12 修复:此前无人赋值 ctx.budget,
    # 导致 /api/budget 恒 disabled)
    from pyharness.core.budget import BudgetGate
    budget_gate = BudgetGate(limits=limits, counters=counters, scope=scope,
                             session_id=str(getattr(log_, "session_id", "")))

    # Registry(能力三类索引,agent 装配用)
    registry = Registry(bus)

    # ---- 工具链(F008/F014/F015 真装配):fs.* 4 工具 + 四关执行器 + guard + 审批
    from pyharness.core.tools_registry import ToolRegistry
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.tools_guard import GuardChain
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core import tool_fs

    tool_reg = ToolRegistry()
    tool_fs.register(tool_reg)                 # fs.read_file/write_file/list_dir/delete_file
    from pyharness.core import spill as spill_mod
    spill_mod.register(tool_reg)               # storage.spill.read(F039 读工具,strict 可见)
    tools = ToolExecutor(tool_reg)             # ctx.tools: schemas_for + execute(四关管道)
    guard = GuardChain(session=log_, bus=bus)  # 内置 g1-g7(单调,事件落 session)
    approval = ApprovalProvider(session=log_, bus=bus, config=cfg,
                                channel="desktop")   # 审批请求入 pending,桌面轮询

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
    # workspace 根(F055):scope.policy.workspace_root,未设则 fs.* 全部 fail-closed
    ws_root = Path(getattr(cfg.storage, "workspaces_dir", "~/.pyharness/workspaces")
                   ).expanduser()
    ws_root.mkdir(parents=True, exist_ok=True)
    policy.workspace_root = str(ws_root)
    # storage.spill 定位器:首个 agent 就绪时 SpillProvider.enter 挂载(见
    # _activate_storage_caps)——executor 关4 超长输出(>2KB)→ spill 私有区 +
    # ref 回喂(F039);激活失败时报 PERS-221 不崩(截断兜底)

    # LLM 门面(会话级;适配器须先 register_default_llm)+ 降级链(F013/F028):
    # fallback_models 非空 → FallbackChain 挂载(指数退避 + 单调降级 + BudgetGuard
    # 前置);chain.adapters 引用 llm 模块注册表,后续 register 补注册立即可见
    fb_models = list(getattr(getattr(cfg, "llm", None), "fallback_models", None) or [])
    llm_client = llm_mod.LLMClient(
        cfg, chain=_make_chain(cfg, bus) if fb_models else None)
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
    if backend_name == "bing":
        search_backend = BingRssBackend(endpoint or
                                        "https://cn.bing.com/search")
    elif backend_name == "duckduckgo":
        search_backend = DuckDuckGoBackend(
            endpoint or "https://html.duckduckgo.com/html/")
    else:
        search_backend = None

    # SessionLog 接总线落盘订阅(事件一入内存即入真源队列)
    if attach_persistence:
        if bus is not None:
            owner = f"engine:{getattr(log_, 'session_id', '?')}"
            for t in _EVENT_TYPES_OR_ALL():
                bus.subscribe(t, _record_to(store), owner=owner)
        if store is not None:
            log_._bus = bus                  # append → 分发 → 落盘闭环

    spine = EngineSpine(
        session=log_, bus=bus, registry=registry,
        scope=scope, llm=llm_client, tools=tools,
        guard=guard, approval=approval,
        counters=counters, sysprompt=sysprompt, compactor=compactor,
        goals=goals, todos=todos, ask=ask, skills=skills,
        plugins=plg_mgr, plugin_state=plg_state, tool_registry=tool_reg,
        streaming=bool(getattr(getattr(cfg, "loop", None), "streaming", False)),
        loop=loop, sys=None, settings=cfg,
        storage=EngineStorage(sessions_dir=sessions_dir),
        persistence=store, plan=plan, schedule=schedule,
        budget=budget_gate,
        search_backend=search_backend)
    # 真实编排适配层:jobs/subagent 不再停在 runner=None 的模块单测状态。
    # SubagentManager 的 announce 使用 tool_registry 直接绑 Provider;模型经
    # 正常 ToolExecutor 看到 subagent.spawn。
    from pyharness.core.jobs import JobManager
    from pyharness.core.orchestration import (EngineIntentRunner,
                                              EngineSubagentRunner)
    from pyharness.core.subagent import SubagentManager
    spine.jobs = JobManager(session=log_, task_queue=None,
                            runner=EngineIntentRunner(spine), bus=bus)
    sub_runner = EngineSubagentRunner(spine, sessions_dir)
    spine.subagent = SubagentManager(
        session=log_, runner=sub_runner, parent_scope=scope,
        tools=tool_reg, bus=bus, cfg=cfg,
        session_factory=sub_runner.child_session)
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
        # 瞬时类型(llm.chunk 等)以 dict 上总线,不得进入 append-only JSONL。
        if not hasattr(payload, "model_dump_json"):
            return
        await store.append(payload, sync=type_ in _SYNC)
    return _record


# ---------------------------------------------------------------- runner seam
def make_runner(spine: EngineSpine) -> EngineRunner:
    """task_queue runner seam:async run_for_task(task) → loop.wake(env)。

    task 的 text 已由 create_message 落盘为 user.message(seq 已知);
    从事件源找该 envelope(按 seq),交 loop.wake 驱动真实处理。
    """
    log_ = spine.session
    loop = spine.loop

    async def run_for_task(task: Any) -> Any:
        # 懒预载:created 已落后首跑前装载示例插件(util.now;EVT-106 首事件
        # 约束 → 不可在 open_session 后立即 append,故延迟到 submit 时刻)
        if not getattr(spine, "_plugins_ready", False):
            try:
                await _preload_plugins(spine)
            except Exception as e:             # noqa: BLE001 预载失败不阻断
                log.debug("plugin lazy preload failed: %s", type(e).__name__)
            spine._plugins_ready = True
        mark = int(getattr(task, "enqueued_seq", 0) or 0)
        env = None
        if mark > 0:                           # task.enqueued 之前的最后一条 user.message
            for e in log_.events_after(0):
                if (int(getattr(e, "seq", 0)) <= mark
                        and getattr(e, "type", "") == "user.message"):
                    env = e
        if env is None:                        # 兜底:最近一条 user.message
            for e in reversed(list(log_.events_after(0))):
                if getattr(e, "type", "") == "user.message":
                    env = e
                    break
        if env is None:
            raise PyHError("CYC-999", ctx={"hint": "runner 找不到对应 user.message 事件"})
        ag_ctx = await _agent_ctx_of(spine, env)
        res = await loop.wake(env, ctx=ag_ctx)
        # F042/#7 自动标题:run 正常完成后命名一次(幂等;LLM 失败静默留空)
        if (res is not None and getattr(res, "reason", "") == "complete"
                and not getattr(spine, "_auto_titled", False)):
            try:
                from pyharness.core.auto_title import auto_title
                if await auto_title(ag_ctx):
                    spine._auto_titled = True
            except Exception as e:             # noqa: BLE001 命名失败不阻断对话
                log.debug("auto_title failed: %s", type(e).__name__)
        return res

    async def cancel_current(task_id: Optional[str] = None) -> None:
        cur = loop.current
        if cur is not None:
            cur.cancelled = True
            # 协作式:由 loop 轮内 CancelledError 传播收尾
    return EngineRunner(run_for_task=run_for_task,
                        cancel_current=cancel_current)


async def _agent_ctx_of(spine: EngineSpine, env: Any) -> Any:
    """agent_loop 消费的 ctx = create_agent 装配的 Agent.ctx(spine 绑定)。

    Agent 与 loop 1:1;ctx.session/scope/llm 即 spine 组件——Agent.ctx 由
    create_agent 构造并绑定 loop。env 仅作输入(loop.wake 已携带)。
    首建 agent 时顺带激活 storage 能力(spill 定位器,幂等)。
    """
    sid = str(getattr(env, "session_id", "") or spine.session.session_id or "")
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
    try:
        from pyharness.core.session_query import SessionQueryIndex
        cfg = getattr(spine, "settings", None) or _cfg_of(spine)
        db = Path(str(getattr(getattr(cfg, "storage", None), "db_path",
                              "~/.pyharness/index.db"))).expanduser()
        sid = str(getattr(ag, "session_id", "")
                  or getattr(spine.session, "session_id", ""))
        idx = SessionQueryIndex(db, sources={sid: spine.session})
        await idx.enter(ag.ctx)                    # 开库建表 + 总线订阅
        await idx.announce(ag.ctx)                 # 注册 session.fts_query + 挂载
        spine.fts = idx
        ag.ctx.session_query = idx                 # F060 对账读数(repair 用)
        log.info("session fts activated sid=%s db=%s", sid, db)
    except Exception as e:                         # noqa: BLE001 索引失败不阻断对话
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
                               sessions_dir: Path) -> EngineContext:
    """一键真实引擎(desktop create_message 用)。

    返回 ctx:与 cli.assemble_ctx 同形状(session/bus/llm/scope/task_queue/
    make_runner)+ engine 脊柱。顺序:先 build_spine(会话级共享计数器就绪)→
    register_default_llm(counters=spine.counters)(适配器计量与 scope 预算闸
    同源)→ session.created 首事件、enter/close 生命周期由调用方负责。
    """
    from pyharness.core.task_queue import TaskQueue

    spine = await build_spine(cfg, sid=sid, sessions_dir=sessions_dir)
    register_default_llm(cfg, counters=spine.counters)
    runner = make_runner(spine)
    queue = TaskQueue(session=spine.session, runner=runner)
    ctx = EngineContext(
        settings=cfg, bus=spine.bus, session=spine.session,
        llm=spine.llm, scope=spine.scope, loop=spine.loop,
        tools=spine.tools, registry=spine.registry,
        guard=spine.guard, approval=spine.approval,
        counters=spine.counters,
        agent=None, task_runner=runner,
        make_runner=lambda: make_runner(spine),
        storage=spine.storage,     # 与 spine 同对象:spill 激活后立即可见
        engine_spine=spine, task_queue=queue, budget=spine.budget)
    ctx.search_backend = spine.search_backend
    await activate_orchestration(spine, task_queue=queue)
    return ctx


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
    from pyharness.core.task_queue import TaskQueue

    spine = build_runner_components(
        cfg, log_=log_, bus=bus or getattr(ctx, "bus", None),
        sessions_dir=sessions_dir,
        store=store if store is not None else getattr(log_, "_persistence", None),
        attach_persistence=False)
    register_default_llm(cfg, counters=spine.counters)   # 幂等;适配器先注则跳过
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
    ctx.budget = spine.budget                      # F032 只读预算门面(仪表盘)
    ctx.search_backend = getattr(spine, "search_backend", None)
    ctx.approval = spine.approval
    try:
        spine.approval._channel = channel          # None 也必须显式覆盖 headless
    except Exception:                              # noqa: BLE001 只读门面:尽力
        pass
    ctx.task_runner = runner
    ctx.make_runner = lambda: runner
    ctx.task_queue = TaskQueue(session=log_, runner=runner)
    await activate_orchestration(spine, task_queue=ctx.task_queue)
    return spine
