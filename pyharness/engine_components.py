"""Engine components: complete migrated implementation."""
from __future__ import annotations
import asyncio
import copy
from pathlib import Path
from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    pass
from pyharness.bus import EventBus
from pyharness.bus.registry import Registry
from pyharness.core.agent import CHANNEL_UNDECLARED
from pyharness.core.agent_loop import AgentLoop
from pyharness.core import llm as llm_mod
from pyharness.core.scope import BudgetLimits, Scope, ScopePolicy, session_workspace

from pyharness.engine_types import EngineSpine, EngineStorage, log
from pyharness.engine_policy import _apply_preset, _cfg_max_arg_failures, _cfg_probe_interval, _make_chain
from pyharness.engine_events import _EVENT_TYPES_OR_ALL, _EVIDENCE_EVENT_TYPES, _sid_of, _tenant_of
from pyharness.engine_runner import _evidence_producer


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
                            llm_runtime: Any = None, register_model: bool = True, register_llm=None, record_to=None, workspace_for=None) -> EngineSpine:
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
    workspace_root = workspace_for(cfg, log_)
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
    if register_model:
        register_llm(cfg, registry=llm_runtime.registry)
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
                bus.subscribe(t, record_to(store, _sid_of(log_)), owner=owner)
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
