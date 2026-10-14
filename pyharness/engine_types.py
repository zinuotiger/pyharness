"""Engine types: complete migrated implementation."""
from __future__ import annotations
import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING
from pyharness.core.task_queue import Task, TaskQueue
from pyharness.core.task_runner import RunnerResult, TaskLifecycle, TaskRunner
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
from pyharness.bus import EventBus
from pyharness.bus.registry import Registry
from pyharness.core.agent import CHANNEL_UNDECLARED
from pyharness.core.agent_loop import AgentLoop
from pyharness.core import llm as llm_mod
from pyharness.core.scope import Scope
from pyharness.core.session import SessionLog

log = logging.getLogger("pyharness.engine")

@dataclass
class EngineStorage:
    """引擎存储挂载面:路径 + 懒激活的 spill/KV provider。"""

    sessions_dir: Path
    spill: SpillProvider | None = None
    kv: SessionKV | None = None

@dataclass
class EngineSpine:
    """会话级引擎装配束(替代无类型 SimpleNamespace)。"""

    session: SessionLog
    bus: EventBus
    registry: Registry
    scope: Scope
    llm: llm_mod.LLMClient
    tools: ToolExecutor
    guard: GuardChain
    approval: ApprovalProvider
    counters: llm_mod.UsageCounters
    sysprompt: SystemPromptAssembler
    compactor: Compactor
    goals: GoalManager
    todos: TodoManager
    ask: AskProvider
    skills: SkillManager
    plugins: Any
    plugin_state: dict
    tool_registry: ToolRegistry
    streaming: bool
    loop: AgentLoop
    settings: Settings
    storage: EngineStorage
    persistence: Any = None
    sys: Any = None
    active_agents: dict[str, Any] = field(default_factory=dict)
    headless: bool = False
    plan: Any = None
    schedule: Scheduler | None = None
    jobs: JobManager | None = None
    subagent: SubagentManager | None = None
    task_queue: TaskQueue | None = None
    task_lifecycle: TaskLifecycle | None = None
    external_runner: TaskRunner | None = None
    search_backend: Any = None
    budget: BudgetGate | None = None
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

    run_for_task: Callable[[Task], Awaitable[RunnerResult | None]]
    cancel_current: Callable[[str | None], Awaitable[None]]

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

# Preserve historical import and pickle identities through the compatibility facade.
EngineStorage.__module__ = 'pyharness.engine'
EngineSpine.__module__ = 'pyharness.engine'
EngineRunner.__module__ = 'pyharness.engine'
EngineContext.__module__ = 'pyharness.engine'
