"""pyharness/core/subagent.py — 子 Agent 进程内派发 (specs/subagent.py.md 契约;F049,阶段 4)

一句话:主 agent 派生子 agent(F049)——子任务跑在**独立 session**(自己的 JSONL/seq/
workspace 根,F044 段不嵌套),与主会话共享进程/凭据口/guard 策略;主会话只落
``subagent.spawned/joined/failed`` 三件委派事实(结果摘要 ≤2KB 以**数据身份**回主
会话,不是第二指令源);并发 ≤8、递归深度 ≤3、子预算=父×1/4 且计入父任务;危险操作
**同权过 guard、无父级担保**;模块自身按 DIS-SEAM §2.4 以能力 Provider 形态接入
(enter→announce→detach),detach/会话关闭走 **child-first 清理**(先杀光在途子任务
并落终态事件,再摘父侧接线)。

安全边界(子=分工非特权,SECURITY §3.5/§6.2):①子会话 scope = 父策略快照且只可更紧
(继承 deny_tools/allowed_domains;spec.tools_subset/deny_extra 只收窄,无"父级担保"
通道);②子任务内任何危险操作照走 F014/F015 同权 guard;③凭据最小权限
(spec.creds_allowed 子集,透传给子循环 F016 读口过滤);④子摘要(≤2KB)以 tool-result
同等数据身份入主会话,永远成不了 system 指令(reducer 不把 subagent.* 映射为消息,
F049 的 LLM 可见摘要经 spawn 工具的 tool.result 路径回喂,见偏离 8);⑤child-first
清理:任何拆除路径(用户取消/父会话关闭/模块 detach)先终止全部在途子任务,防止子
任务在父会话 finished 后回写 joined → EVT-104。

偏离说明(契约=specs/subagent.py.md;以下为与既有实现/规格冲突处的取舍,均列理由,
与 task_queue.py/goal.py/agent.py 同款先例,已入 docstring 供审查):
1. ``Session.spawn(parent=, budget=, policy=)`` 工厂不存在(SessionLog 无 spawn 面,
   agent.enter 才写 session.created)→ 子会话经注入的 **session_factory** seam 创建
   (默认 SessionLog 纯内存 + 就地引导 session.created,seq=1),父策略/预算缩放传给
   child_scope_factory(默认自 scope.from_snapshot 同源值拷贝构造独立 Scope,workspace
   根换成 storage.workspaces_dir/{sub_id},不注册全局 _scopes 表——子 scope 生命周期
   随 ChildHandle,release 由装配层随子会话关闭处理)。
2. 父会话绑定:spec 伪码事件全经 ctx.* 取服务;实际代码库能力模块(goal/task_queue)
   先例 = 构造期绑定 session(父会话 SessionLog),spawn/join/cancel/status 的事件写口
   用绑定会话,ctx 只作 scope/tools/bus/轮锚的附加来源(允许缺省)。
3. 预算表达:spec 伪码 ``ctx.scope.budget * spec.budget_ratio`` 为单一标量;实际 scope
   落地为 BudgetLimits(max_in_tokens/max_out_tokens/max_cost_yuan/warn_ratio)→ 子预算
   = 父 limits 逐字段 × ratio(下限 1),"计入父任务"的用量回写(meter 层 task 级聚合)
   属装配层接线:本模块提供 ChildContext.budget_limits 供子循环 scope 同源取用,并留
   charge_hook 注入位(默认 None = 纯分配记账;真 wire 由装配层把子用量 report 进父
   任务计数器,防 8 个子任务各 1/4 超父总盘)。
4. 并发闸语义:spec 结构表 _sem=Semaphore(acquire 排队)与其异常表"并发满→BUSY 拒新"
   矛盾 → 用**计数闸**(单线程事件循环,计数增减间无 await,原子):满(≥limit)→ BUSY
   拒新,不排队;子任务终态释放。max_concurrent 可注入(默认 8)便于测试。
5. 深度闸语义:spec.depth 即"本子任务所处层级"(主会话=0,首层=1,…≤3);除上界
   (depth > 3 → BUSY)外补**递增闸**(spec.depth < 自身层级+1 → BUSY,防平级/降级派发
   绕过递归闸);非 int → EVT-100。子执行器拿到 env.spec.depth,装配层据其构造下一层
   manager(depth=spec.depth),形成 ≤3 的硬链。
6. ChildHandle.started_ts 用 epoch 秒(float):spec 表型 str 与 status 伪码
   ``int(now - h.started_ts)`` 减法矛盾,取数值语义。
7. agent_loop.run(sub_session, intent=, tools=) 面不存在(实际 agent_loop.run(ctx) 是
   排水监督器,无 intent 入口)→ 子执行器经构造注入 **runner seam**(async
   run_child(env) -> Any,env=ChildContext{session/spec/scope/budget_limits}),
   task_queue.runner 同款先例;未装配 runner → 子任务 CYC-999 快速失败(防 S-1"伪造
   已执行",宁失败不静默假完成)。F007 唯一 LLM 入口纪律(INV-02)由 runner 内部保证。
8. F049 工具路径:announce 注册 ``subagent.spawn`` 工具(Definition danger=none +
   Provider 适配器),Provider.handle = spawn + join,返回 ≤2KB 摘要——LLM 视角一次
   工具调用即"派发并回收",摘要经 executor 的 tool.result 管道以 tool 数据身份进主
   会话派生历史(reducer §4.2 不映射 subagent.* 事件,故摘要不入主历史则永不可见)。
9. enter/announce/detach 的 ctx 可为 None(装配层已构造期注入 bus/tools 时);事件类型
   登记按 EventBus 实例弱引用去重(同 bus 重复 enter/重入幂等,防 register_type 二次
   注册撞 EVT-102;tools_registry._registered_buses 同款先例)。
10. 终态事件写口统一 ``_write_event``:actor=agent/origin=cap:subagent;EVT-104(父会话
    已 finished)兜底只记本地日志不阻断子回收(spec _run_child 异常表同语义);spawned
    落盘失败则 EVT-104 上抛(派发闸,spec spawn 异常表)。
11. "session.closing" 为规范预关钩子事件名(词表外,无触发方;agent.close 目前先
    cancel 在途 run 再 append finished,能力级 pre-finished 清理钩子属阶段 5 装配)——
    announce 按 spec 挂订阅 owner=cap:subagent;detach 自身即 child-first,兜底无孤儿;
    测试直接 emit 或调用 _on_session_closing 驱动。

错误全走 raise_code(EVT-1xx/TLB-8xx)/字面量 BUSY(PyHError 直构,未登记码先例),
禁裸 raise str。
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from pyharness.errors import PyHError, raise_code, wrap_unexpected
from pyharness.core.session import SessionLog

log = logging.getLogger("pyharness.subagent")

# ------------------------------------------------------------------ 约束常量
# 参数锚定(PARAMETER-ANCHOR/CFG.md 锁死;禁自创数字):并发 ≤8/深度 ≤3/预算 1/4/摘要 ≤2KB
MAX_DEPTH: int = 3
MAX_CONCURRENT: int = 8
DEFAULT_BUDGET_RATIO: float = 0.25      # config budget.subagent_ratio 默认(CFG.md)
SUMMARY_MAX_CHARS: int = 2_000          # 摘要 ≤2KB(以数据身份回主会话)
TOOL_NAME: str = "subagent.spawn"       # LLM 可见工具名(Definition.name 锚)
SUBA_OWNER: str = "cap:subagent"        # 订阅/注册属主(agent 同款 owner 语义)
CHILD_PREFIX: str = "s-sub"             # 子会话 id 前缀(s-subxxxx,≥8 字符)
EVENT_TYPES: tuple[str, ...] = ("subagent.spawned", "subagent.joined",
                                "subagent.failed")
_TERMINAL: frozenset = frozenset(("done", "failed", "cancelled"))


def _raise_busy(**ctx: Any) -> None:
    """字面量 BUSY 上抛(BUSY = 无域前缀字面量码,ERR.md §2.11)。

    raise_code 会把未登记码改写为 CYC-999(语义不符)→ 按 agent.py/scope.py 同款
    先例直接构造 PyHError——code 字段保持字面量 BUSY,供调用方/测试按 .code 断言。
    """
    raise PyHError("BUSY", ctx=dict(ctx))


# ------------------------------------------------------------------ 数据结构
ChildState = Literal["spawning", "running", "joining", "done", "failed",
                     "cancelled"]


@dataclass
class SubagentSpec:
    """子任务委派规格(字段规则以 specs/subagent.py.md 数据结构表为契约)。

    task = 委派意图;tools_subset = 只收窄父工具面(None=继承父可用集);deny_extra =
    追加收紧(并入子 scope deny,默认[]);budget_ratio = 子预算比例(≤config
    budget.subagent_ratio);depth = 本子任务所处层级(主会话=0,首层=1,递归闸 ≤3);
    creds_allowed = 凭据名子集(默认[]=仅继承公开无密能力,F016 读口按此过滤);
    notify = 完成是否回写 joined/failed 事件。
    """

    task: str
    tools_subset: Optional[list[str]] = None
    deny_extra: list[str] = field(default_factory=list)
    budget_ratio: float = DEFAULT_BUDGET_RATIO
    depth: int = 0
    creds_allowed: list[str] = field(default_factory=list)
    notify: bool = True


@dataclass
class ChildContext:
    """子执行器消费面(runner seam 入参;偏离 7:替代不存在的 agent_loop.run 签名)。

    session = 独立子 SessionLog;spec = 委派规格;scope = 子 scope(预算=父×ratio
    快照收紧);budget_limits = 缩放后预算(审计/装配取用);creds = 凭据读口过滤
    壳(装配层按 spec.creds_allowed 构造,F016;本模块不 import credentials)。
    """

    sub_id: str
    session: Any                        # SessionLog:独立 JSONL/seq
    spec: SubagentSpec
    scope: Any = None                   # 子 Scope 或 None(未接线)
    budget_limits: Any = None           # 缩放后 BudgetLimits(父×ratio,下限 1)
    parent_sid: str = ""
    creds: Any = None                   # F016 最小权限读口(装配层注入)


@dataclass
class ChildHandle:
    """在途子任务句柄(spec 数据结构表;只登记在途,已回收即摘,INV-01)。

    parent_seq = spawned 事件 seq(joined/failed 的 trace.parent_seq 锚);result =
    join 等待 Future;started_ts = epoch 秒(偏离 6);slot = 并发闸持有标记(防
    取消注入早于任务启动时信号量泄漏,见 spawn/_finalize)。
    """

    sub_id: str
    parent_sid: str
    parent_seq: int
    spec: SubagentSpec
    session: Any
    task: Optional[asyncio.Task] = None
    state: ChildState = "spawning"
    result: Any = None                  # asyncio.Future(join 等待)
    summary: Optional[str] = None
    error_code: Optional[str] = None
    started_ts: float = 0.0
    env: Optional[ChildContext] = None  # 子执行器消费面
    budget_limits: Any = None           # 缩放后预算(审计断言用)
    scope: Any = None                   # 子 Scope(同 env.scope)
    slot: bool = False                  # 并发闸持有标记(_finalize 防双放)


@dataclass
class SubagentStatus:
    """并发与在途查询快照(status 返回,纯读零副作用)。"""

    running: int = 0
    active_children: list = field(default_factory=list)   # {sub_id,state,age_s}
    limit: int = MAX_CONCURRENT


# ------------------------------------------------------------------ 文本辅助
def render_result_text(raw: Any) -> str:
    """子执行器原始返回 → 统一文本(dict/list JSON 序列化;str 原样;None → 空)。"""
    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        try:
            return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(raw)
    return str(raw)


def summarize(out: Any, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """子结果摘要化(≤max_chars,截断补 …;恒非空——joined payload summary 必填)。

    摘要由框架从执行输出截断生成,不由子 LLM 直接回写(防注入操纵摘要,SECURITY
    §5 同 executor.summarize 语义);空输出给占位记号,保证 joined 事件合法。
    """
    text = render_result_text(out)
    if not text:
        return "…"                      # 空输出占位(非空摘要契约)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _scale_limits(limits: Any, ratio: float) -> Any:
    """父 BudgetLimits × ratio → 子预算(逐字段缩放,下限 1;None → None)。

    鸭子构造:优先实例化真实 BudgetLimits(lazy import,装配期才需要);不可导入时
    退回 SimpleNamespace 同字段(单测/隔离环境仍可断言属性)。
    """
    if limits is None:
        return None
    try:
        from pyharness.core.scope import BudgetLimits  # lazy:INV-08 单向装配
        return BudgetLimits(
            max_in_tokens=max(1, int(limits.max_in_tokens * ratio)),
            max_out_tokens=max(1, int(limits.max_out_tokens * ratio)),
            max_cost_yuan=float(limits.max_cost_yuan) * ratio,
            warn_ratio=getattr(limits, "warn_ratio", 0.8))
    except Exception:  # noqa: BLE001 装配期 scope 缺席 → 鸭子回落
        return type("ScaledLimits", (), {
            "max_in_tokens": max(1, int(limits.max_in_tokens * ratio)),
            "max_out_tokens": max(1, int(limits.max_out_tokens * ratio)),
            "max_cost_yuan": float(limits.max_cost_yuan) * ratio,
            "warn_ratio": getattr(limits, "warn_ratio", 0.8)})()


# ------------------------------------------------------------------ SubagentManager
class SubagentManager:
    """子 Agent 进程内派发管理器(F049;编排层,绑定父会话)。

    生命周期 = DIS-SEAM §2.4 能力三动作(enter→announce→detach,均可 None ctx/幂等);
    核心入口 spawn(spec, ctx) 四道约束闸(深度 ≤3/工具子集在册/并发 ≤8/预算=父×ratio
    且计入父任务)全过 → 建独立子会话 → 父会话落 subagent.spawned → 登记 ChildHandle
    并起 _run_child 协程;join/cancel/status 供主循环回收/取消/查询;子任务终态 →
    摘要(≤2KB)→ subagent.joined / PyHError → subagent.failed;child-first 清理在
    detach/_on_session_closing 统一收口(先杀光在途子任务并落终态事件,再摘父侧接线)。

    构造注入(INV-08 装配纪律):session = 父会话 SessionLog(事件写口,必填用于
    spawn);runner = 子执行器 seam(偏离 7);parent_scope/tools/bus = ctx 缺省来源
    (spawn 亦可经 ctx 现取);cfg = 分层配置(Settings,读 budget.subagent_ratio 上限);
    depth = 本 manager 所处递归层级(主会话=0);session_factory/child_scope_factory/
    child_id_factory = 子会话/子 scope/子 id 构造 seam(默认实现见下,测试注入替身);
    max_concurrent/max_depth/budget_ratio/summary_max_chars = 约束闸参数(默认取常量)。
    """

    # EventBus 实例弱引用去重:同 bus 重复 register_type 会 EVT-102(偏离 9)
    _typed_buses: "weakref.WeakKeyDictionary[Any, None]" = weakref.WeakKeyDictionary()

    def __init__(self, session: Optional[Any] = None, *,
                 runner: Any = None, parent_scope: Any = None,
                 tools: Any = None, bus: Any = None, cfg: Any = None,
                 depth: int = 0,
                 max_concurrent: int = MAX_CONCURRENT,
                 max_depth: int = MAX_DEPTH,
                 budget_ratio: Optional[float] = None,
                 summary_max_chars: int = SUMMARY_MAX_CHARS,
                 session_factory: Optional[Callable[[str], Any]] = None,
                 child_scope_factory: Optional[Callable[..., Any]] = None,
                 child_id_factory: Optional[Callable[[], str]] = None,
                 charge_hook: Optional[Callable[..., Any]] = None,
                 owner: str = SUBA_OWNER) -> None:
        self._session: Any = session            # 父会话(事件写口)
        self._runner: Any = runner              # 子执行器 seam(见偏离 7)
        self._parent_scope: Any = parent_scope  # 父 scope(预算/策略快照源)
        self._tools: Any = tools                # 工具注册表(tools_subset 在册校验)
        self._bus: Any = bus                    # 总线(类型登记/订阅/留痕)
        self._cfg: Any = cfg                    # Settings(读 budget.subagent_ratio)
        self._depth: int = int(depth)           # 本 manager 递归层级(主=0)
        self._max_concurrent: int = int(max_concurrent)
        self._max_depth: int = int(max_depth)
        # 预算比例上限:显式 > cfg.budget.subagent_ratio > 常量 0.25(PARAMETER-ANCHOR)
        self._budget_ratio: float = self._resolve_budget_ratio(
            budget_ratio, cfg) if budget_ratio is None else float(budget_ratio)
        self._summary_max_chars: int = int(summary_max_chars)
        self._session_factory = session_factory or self._default_session
        self._child_scope_factory = child_scope_factory or self._default_child_scope
        self._child_id_factory = child_id_factory or self._default_sub_id
        self._charge_hook: Any = charge_hook    # 子用量计入父任务钩子(偏离 3)
        self._owner: str = owner                # 订阅属主(默认 cap:subagent)
        self._entered: bool = False             # enter 完成标记(幂等闸)
        self._announced: bool = False           # announce 完成标记(幂等闸)
        self._children: dict[str, ChildHandle] = {}
        self._sem = asyncio.Semaphore(self._max_concurrent)  # 结构表字段(计数闸见偏离 4)
        self._active: int = 0                   # 并发计数(真源;BUSY 拒新判定)
        self._sub_seq = itertools.count(1)      # 子 id 单调源
        self._mount_ns: Any = None              # announce mount 目标(agent 命名空间)
        self._mount_prev: Any = None            # mount 前旧值(detach 恢复)
        self._announce_ctx: Any = None          # announce 时的 ctx(供 detach 无参摘除)

    # ============================================================ 内部辅助
    @staticmethod
    def _resolve_budget_ratio(budget_ratio: Optional[float],
                              cfg: Any) -> float:
        """比例上限解析:cfg.budget.subagent_ratio → 常量 0.25(cfg 缺席容错)。"""
        if cfg is not None:
            try:
                v = getattr(getattr(cfg, "budget", None), "subagent_ratio", None)
                if isinstance(v, (int, float)) and float(v) > 0:
                    return float(v)
            except Exception:  # noqa: BLE001 第三方配置对象 getattr 钩子异常
                pass
        return DEFAULT_BUDGET_RATIO

    def _ensure_session(self) -> Any:
        """父会话前置:未绑定 → EVT-100(spawn/join/status 的事件写口依赖)。"""
        if self._session is None:
            raise_code("EVT-100", hint="SubagentManager 未绑定父会话",
                       advice="构造时注入父会话 SessionLog(ctx.agent.subagent 挂载点)")
        return self._session

    def _default_sub_id(self) -> str:
        """子会话 id:s-subxxxx 单调(s-sub0001…;≥8 字符满足 Envelope 校验)。"""
        return f"{CHILD_PREFIX}{next(self._sub_seq):04d}"

    def _default_session(self, sub_id: str) -> SessionLog:
        """默认子会话工厂:独立内存 SessionLog(自带 JSONL 由装配注 persistence)。"""
        return SessionLog(sid=sub_id)

    async def _bootstrap_child(self, child: Any, sub_id: str) -> None:
        """子会话引导:写 session.created(seq=1,首事件 EVT-106 守卫)。

        SessionLog 无 Session.spawn 工厂(偏离 1):created 引导后子会话才 open,
        子循环/执行器的后续 append 才能过校验链第 5 步。model 沿父会话主模型。
        """
        await child.append("session.created",
                           {"title": f"subagent {sub_id}",
                            "model": self._parent_model()}, actor="system")

    def _parent_model(self) -> str:
        """父会话主模型(created payload model;缺失回落默认名)。"""
        sess = self._session
        if sess is not None and hasattr(sess, "events_after"):
            for ev in sess.events_after(0):
                if ev.type == "session.created":
                    m = (ev.payload or {}).get("model")
                    if m:
                        return m
        return "deepseek-chat"

    def _default_child_scope(self, parent_scope: Any, sub_id: str,
                             child_session: Any, limits: Any,
                             deny_extra: Optional[list[str]] = None) -> Any:
        """默认子 scope 工厂:父策略快照值拷贝 + deny_extra 只收窄 + 独立根。

        workspace 根 = storage.workspaces_dir/{sub_id}(F055 每会话独立根;从父根
        推导同目录族);预算 limits 用缩放后的子预算(父×ratio,下限 1);spec.deny_
        extra 并入子策略 deny_tools(只紧不松,无父级担保通道)。Scope 直接构造不
        注册全局表——子 scope 生命周期随 handle(偏离 1)。
        """
        if parent_scope is None:
            return None
        snap = getattr(parent_scope, "snapshot", None)
        if not callable(snap):
            return None
        try:
            from pathlib import Path
            from pyharness.core.scope import Scope, ScopePolicy
            s = snap()
            pol = ScopePolicy(**s.policy.model_dump())      # 值拷贝(COW 起点)
            base = Path(str(s.policy.workspace_root or
                             "~/.pyharness/workspaces")).expanduser()
            pol.workspace_root = str(base.parent / sub_id)  # 独立根(F055)
            pol.deny_tools |= set(deny_extra or ())         # deny 只收窄(单调)
            return Scope(policy=pol, limits=limits,
                         session_id=sub_id,
                         window_tokens=getattr(s, "window_tokens", 64000),
                         window_ratio=getattr(s, "window_ratio", 0.75),
                         session=child_session,
                         counters=None, bus=getattr(self, "_bus", None))
        except Exception as exc:                            # noqa: BLE001
            log.warning("子 scope 构造失败 sub=%s: %s(降级 None)", sub_id, exc)
            return None

    # ============================================================ 事件写口
    async def _write_event(self, type_: str, payload: dict, *, actor: str,
                           trace: Optional[dict] = None,
                           swallow104: bool = True) -> Any:
        """父会话事件写口:actor/origin 统一打点;EVT-104 兜底只记日志(偏离 10)。

        子终态回写(joined/failed/cancelled 审计)在父会话已 finished 时被 EVT-104
        拒——child-first 清理保证结构上先于此;真发生(时序漏洞)只记日志不阻断
        子回收(spec _run_child 异常表语义)。spawned 落盘不吞(派发闸,调方直用
        session.append,见 spawn)。
        """
        sess = self._ensure_session()
        try:
            return await sess.append(type_, payload, actor=actor,
                                     trace=trace, origin=SUBA_OWNER)
        except PyHError as e:
            if e.code == "EVT-104" and swallow104:
                log.warning("subagent 终态回写被拒(EVT-104 父已终态),仅本地日志"
                            " type=%s sub_id=%s", type_, payload.get("sub_id"))
                return None
            raise

    # ============================================================ DIS-SEAM §2.4 ①
    async def enter(self, ctx: Any = None) -> None:
        """能力装载(DIS-SEAM §2.4 第①动作):登记 subagent.* 事件类型 schema。

        幂等:已 enter 直接返回(重复 enter 静默通过)。事件类型按 EventBus 实例
        弱引用去重——同 bus 二次 enter(含 detach 后重入)不撞 register_type 的
        EVT-102(偏离 9);登记失败(真冲突/总线异常)→ 回 detached 不 announce。
        """
        if self._entered:
            return
        bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                             or self._bus)
        if bus is not None:
            self._register_bus_types(bus)
        self._entered = True
        log.info("subagent capability entered session=%s",
                 getattr(self._session, "sid", None))

    def _register_bus_types(self, bus: Any) -> None:
        """EventBus 事件类型登记(弱引用去重;失败上抛 → enter 失败回 detached)。"""
        try:
            if bus in self._typed_buses:
                return                          # 该总线已登记(重入/多 manager 共享)
        except TypeError:                       # 不可弱引用对象:退化为逐次尝试
            pass
        for t in EVENT_TYPES:
            bus.register_type(t, None)          # 二次注册 EVT-102 → 上抛
        try:
            self._typed_buses[bus] = None
        except TypeError:                       # 同上:仅本次登记失败,不阻断
            pass

    # ============================================================ DIS-SEAM §2.4 ②
    def definition(self) -> Any:
        """subagent.spawn 工具 Definition(announce 第③步注册给 LLM)。

        danger=none:派发本身不越权,子任务内动作仍过各自 guard(F014/F015 同权,
        无父级担保);approval=never 与 none 级一致(ToolDefinition 契约)。schema 为
        JSON-Schema dict(注册时编译 F026);depth 不进 LLM 参数(递归层级由装配层
        依 ctx 推 depth=self.depth+1,防 LLM 谎报层级绕过递归闸)。
        """
        from pyharness.core.tools_registry import ToolDefinition
        return ToolDefinition(
            name=TOOL_NAME,
            description="派生子 agent 并行处理可隔离子任务(独立 session,预算=父×1/4,"
                        "深度≤3),完成返回 ≤2KB 摘要",
            schema={"type": "object",
                    "properties": {
                        "task": {"type": "string",
                                 "description": "委派意图(可隔离子任务描述)"},
                        "tools_subset": {"type": "array", "items": {"type": "string"},
                                         "description": "只收窄子任务可用工具(可选)"},
                        "deny_extra": {"type": "array", "items": {"type": "string"},
                                       "description": "追加收紧工具名(可选)"},
                        "creds_allowed": {"type": "array", "items": {"type": "string"},
                                          "description": "子任务所需凭据名子集(可选)"},
                        "notify": {"type": "boolean",
                                   "description": "完成是否回写 joined 事件(默认 true)"}},
                    "required": ["task"]},
            danger="none", approval="never", owner=self._owner,
            ctx_path="ctx.agent.subagent", version="1.0.0")

    async def announce(self, ctx: Any = None) -> None:
        """对外宣布(DIS-SEAM §2.4 第②动作,五步):注册工具 → 挂订阅 → mount →
        registry.updated 留痕;任一步失败回滚已做步骤并回 detached(半装必回滚)。

        已 announce 幂等返回。mount 目标 = ctx.agent 命名空间(装配层未来 locator
        宿主;未挂 agent 命名空间时日志降级不阻断——工具已注册,主循环仍可经
        tools.execute 触达)。
        """
        if self._announced:
            return
        done: list[str] = []
        try:
            ctx = ctx or self._announce_ctx
            bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                                 or self._bus)
            tools = self._tools if ctx is None else (getattr(ctx, "tools", None)
                                                     or self._tools)
            if tools is not None:
                # ① ③ LLM 可见:register_tool 绑 Provider(spawn+join 适配器,偏离 8)
                register = getattr(tools, "register_tool", None) or \
                    getattr(tools, "register_definition", None)
                if callable(register):
                    try:
                        register(self.definition(), provider=SubagentSpawnProvider(self))
                    except TypeError:
                        register(self.definition())     # 无 provider 面的注册表别名
                    done.append("tool")
            if bus is not None:
                # ② 挂订阅:session.closing 预关钩子(规范预留,偏离 11)
                bus.subscribe("session.closing", self._on_session_closing,
                              owner=self._owner)
                done.append("sub")
            # ④ mount ctx.agent.subagent(定位器;可缺省降级)
            mounted = self._mount(ctx, "subagent")
            if mounted:
                done.append("mount")
            if bus is not None:
                # ⑤ registry.updated 留痕(瞬时事件,仅总线)
                await bus.emit("registry.updated",
                               {"op": "attach", "kind": "capability",
                                "key": "ctx.agent.subagent"})
            self._announce_ctx = ctx
            self._announced = True
            log.info("subagent capability announced session=%s",
                     getattr(self._session, "sid", None))
        except BaseException:
            for step in reversed(done):         # 半装必回滚(逆序)
                try:
                    self._rollback(step, ctx)
                except Exception:               # noqa: BLE001 回滚自身失败不吞原错
                    log.exception("announce 回滚失败 step=%s", step)
            self._entered = False               # 回 detached(可重入重试)
            raise

    def _rollback(self, step: str, ctx: Any) -> None:
        """announce 单步回滚(逆序摘除已做步骤;TLB-802 = 已摘,幂等静默)。"""
        if step == "tool":
            tools = self._tools if ctx is None else (getattr(ctx, "tools", None)
                                                     or self._tools)
            if tools is not None:
                unreg = getattr(tools, "unregister_definition", None) or \
                    getattr(tools, "unregister", None)
                if callable(unreg):
                    try:
                        unreg(TOOL_NAME)
                    except PyHError as e:
                        if e.code != "TLB-802":
                            log.warning("announce rollback 注销失败 code=%s", e.code)
        elif step == "sub":
            bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                                 or self._bus)
            if bus is not None:
                bus.unsubscribe_all(owner=self._owner)
        elif step == "mount":
            self._unmount(ctx)

    # ============================================================ DIS-SEAM §2.4 ③
    async def detach(self, ctx: Any = None) -> None:
        """能力摘除,child-first(DIS-SEAM §2.4 第③动作):先杀光在途子任务并落
        终态事件,再摘订阅/注销工具/unmount;单步失败记 broken 告警仍继续摘除
        (半卸 > 僵尸);幂等(重复调用安全)。
        """
        if not self._entered and not self._announced and not self._children:
            return                             # 幂等:无装载无在途 → 无事可做
        # ① child-first:先杀孩子(取消传播 F025 + 终态事件 + 等全部真正退出)
        try:
            await self._cancel_all_children(reason="parent-detach")
        except PyHError as e:
            log.warning("subagent detach child-first 失败 code=%s(broken 告警)",
                        e.code)
        ctx = ctx or self._announce_ctx
        bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                             or self._bus)
        tools = self._tools if ctx is None else (getattr(ctx, "tools", None)
                                                 or self._tools)
        broken: list[str] = []
        # ② 摘订阅(owner 精确摘除;幂等)
        if bus is not None:
            try:
                bus.unsubscribe_all(owner=self._owner)
            except Exception as exc:           # noqa: BLE001 半卸 > 僵尸
                log.warning("detach 摘订阅失败: %s", exc)
                broken.append("sub")
        # ③ 注销工具(TLB-802 = 已摘,幂等静默)
        if tools is not None:
            unreg = getattr(tools, "unregister_definition", None) or \
                getattr(tools, "unregister", None)
            if callable(unreg):
                try:
                    unreg(TOOL_NAME)
                except PyHError as e:
                    if e.code != "TLB-802":
                        log.warning("detach 注销工具失败 code=%s", e.code)
                        broken.append("tool")
                except Exception as exc:       # noqa: BLE001
                    log.warning("detach 注销工具异常: %s", exc)
                    broken.append("tool")
        # ④ unmount(ctx.agent.subagent)
        try:
            self._unmount(ctx)
        except Exception as exc:               # noqa: BLE001
            log.warning("detach unmount 失败: %s", exc)
            broken.append("mount")
        # ⑤ registry.updated op=del 留痕(尽力而为)
        if bus is not None and not broken:
            try:
                await bus.emit("registry.updated",
                               {"op": "del", "kind": "capability",
                                "key": "ctx.agent.subagent"})
            except Exception:                  # noqa: BLE001
                log.warning("detach registry.updated 留痕失败")
        self._announced = False
        self._entered = False
        self._children.clear()
        if broken:
            log.warning("subagent detach 部分失败(broken 标记): %s", broken)

    def _mount(self, ctx: Any, key: str) -> bool:
        """locator.mount:把本 manager 挂到 ctx.agent 命名空间的 subagent 键。

        挂载前记录旧值(detach 恢复);ctx.agent 命名空间未建(装配层 locator 未
        接线)→ 日志降级返回 False(工具面仍可用,不阻断 announce)。
        """
        if ctx is None:
            return False
        ns = getattr(ctx, "agent", None)
        if ns is None:
            log.warning("ctx.agent 命名空间未挂载,ctx.agent.subagent 未 mount"
                        " (tools.execute 面仍可用)")
            return False
        self._mount_ns = ns
        self._mount_prev = getattr(ns, key, None)
        try:
            setattr(ns, key, self)
            return True
        except Exception as exc:               # noqa: BLE001 只读/受限目标
            log.warning("mount ctx.agent.%s 失败: %s", key, exc)
            self._mount_ns = None
            self._mount_prev = None
            return False

    def _unmount(self, ctx: Any = None) -> None:
        """locator.unmount 逆操作:仅当现值仍为本 manager 时摘除并恢复旧值。"""
        ns = self._mount_ns
        if ns is None and ctx is not None:
            ns = getattr(ctx, "agent", None)
        if ns is None:
            return
        key = "subagent"
        if getattr(ns, key, None) is self:
            if self._mount_prev is None:
                try:
                    delattr(ns, key)
                except Exception:              # noqa: BLE001
                    pass
            else:
                try:
                    setattr(ns, key, self._mount_prev)
                except Exception:              # noqa: BLE001
                    pass
        self._mount_ns = None
        self._mount_prev = None

    # ============================================================ F049 派发核心
    def _parent_anchor(self, ctx: Any) -> int:
        """发起轮锚(spawned payload.parent_seq):ctx.round_seq(spec 命名)→ 主循环
        run.input_seq → 父会话当前 seq;均无 → EVT-100(父会话未引导,EVT-106 语义)。
        """
        if ctx is not None:
            rs = getattr(ctx, "round_seq", None)
            if isinstance(rs, int) and rs >= 1:
                return rs
            loop = getattr(ctx, "loop", None)
            cur = getattr(loop, "current", None) if loop is not None else None
            iq = getattr(cur, "input_seq", None) if cur is not None else None
            if isinstance(iq, int) and iq >= 1:
                return iq
        sess = self._session
        if sess is not None and hasattr(sess, "stats"):
            last = sess.stats().get("seq", 0)
            if last >= 1:
                return last
        raise_code("EVT-100", hint="父会话未引导或无发起轮锚(parent_seq 需 ≥1)",
                   advice="子派发须在父会话 created 后、运行轮内发起")

    def _validate_spec(self, spec: SubagentSpec) -> None:
        """派发前 spec 校验(异常表:非法 → EVT-100):空 task/非正值或超上限
        budget_ratio/tools_subset 与 deny_extra、creds_allowed 类型/层级非 int。"""
        if not isinstance(spec, SubagentSpec):
            raise_code("EVT-100", hint="spec 须为 SubagentSpec 实例")
        if not spec.task or not str(spec.task).strip():
            raise_code("EVT-100", field="task", hint="委派任务为空,spawn 拒绝")
        if not isinstance(spec.budget_ratio, (int, float)) \
                or not (0 < float(spec.budget_ratio) <= self._budget_ratio):
            raise_code("EVT-100", field="budget_ratio",
                       ratio=spec.budget_ratio, cap=self._budget_ratio,
                       hint=f"预算比例须 ∈ (0, {self._budget_ratio}](≤config "
                            f"budget.subagent_ratio;默认父×1/4)")
        if spec.tools_subset is not None and not isinstance(spec.tools_subset, list):
            raise_code("EVT-100", field="tools_subset",
                       hint="tools_subset 须为 list[str] 或 None")
        for t in spec.tools_subset or []:
            if not isinstance(t, str):
                raise_code("EVT-100", field="tools_subset", tool=t,
                           hint="tools_subset 元素须为工具名字符串")
        if not isinstance(spec.deny_extra, list) \
                or not isinstance(spec.creds_allowed, list):
            raise_code("EVT-100", hint="deny_extra/creds_allowed 须为 list")
        if not isinstance(spec.depth, int):
            raise_code("EVT-100", field="depth", depth=spec.depth,
                       hint="depth 须为 int(递归层级)")

    async def _acquire_slot(self) -> None:
        """并发闸(BUSY 拒新,不排队;偏离 4):满 → BUSY,语义同 JOB-001 族。"""
        if self._active >= self._max_concurrent:
            raise PyHError("BUSY", ctx={
                "hint": f"子 agent 并发已达上限 {self._max_concurrent}",
                "advice": "先 join/取消若干在途子任务再派发(F049 并发边界 ≤8)"})
        self._active += 1                       # 计数增减间无 await → 原子

    def _release_slot(self) -> None:
        """并发闸释放(子任务终态调用;防双放由调用方 slot 标记兜底)。"""
        if self._active > 0:
            self._active -= 1

    async def spawn(self, spec: SubagentSpec, ctx: Any = None) -> str:
        """F049 核心入口:四道约束闸(深度/工具子集在册/并发/预算)全过 → 建独立
        子会话 → 父会话落 subagent.spawned → 登记 ChildHandle 并起 _run_child;
        返回 sub_id(join 的合法凭据,结果只可 join 一次)。

        异常:BUSY(深度 >3/层级不递增/并发满)、TLB-802(工具子集含未注册名)、
        EVT-100(spec 非法)、EVT-104(父会话已 finished——session.append 校验链拒)。
        任一步失败:并发闸已占则释放,零残留(无脏登记/无孤儿会话引导)。
        """
        self._ensure_session()
        self._validate_spec(spec)
        if spec.depth > self._max_depth:        # ① 递归闸上界 ≤3(PRD F049)
            raise PyHError("BUSY", ctx={
                "hint": f"子任务深度 {spec.depth} 超上限 {self._max_depth}",
                "advice": "子任务不得再嵌套超过 3 层;考虑平铺任务或合入父意图"})
        if spec.depth < self._depth + 1:        # 递归闸递增链(偏离 5)
            raise PyHError("BUSY", ctx={
                "hint": f"子任务深度 {spec.depth} 未达本层 {self._depth} + 1",
                "advice": "深度必须逐层 +1(平级/降级派发会绕过递归闸)"})
        # ② 工具子集必须已注册(幻觉名拦截;tools 未接线 → 日志降级跳过)
        tools = self._tools
        if ctx is not None:
            tools = tools or getattr(ctx, "tools", None)
        if tools is not None:
            has = getattr(tools, "has", None)
            for t in spec.tools_subset or []:
                if has is not None and not has(t):
                    raise_code("TLB-802", tool=t,
                               advice="tools_subset 含未注册工具;核对注册表,删幻觉名")
        # ④ 预算 = 父 × spec.budget_ratio(计入父任务:charge_hook 注入位,偏离 3)
        parent_scope = self._parent_scope
        if ctx is not None:
            parent_scope = parent_scope or getattr(ctx, "scope", None)
        limits = None
        if parent_scope is not None:
            limits = _scale_limits(getattr(parent_scope, "limits", None),
                                   float(spec.budget_ratio))
        # ③ 并发闸(满 → BUSY;随后任何失败路径都释放,零残留)
        await self._acquire_slot()
        try:
            sub_id = self._child_id_factory()
            child = self._session_factory(sub_id)
            child_scope = None
            factory = self._child_scope_factory
            if callable(factory):
                child_scope = factory(parent_scope, sub_id, child, limits,
                                      spec.deny_extra)
            anchor = self._parent_anchor(ctx)
            # 子会话引导(独立 seq=1;真实 JSONL 由装配注 persistence)
            await self._bootstrap_child(child, sub_id)
            # 父会话只记委派事实(普通落盘;actor=agent,EVENT-SCHEMA §3.5.3)
            env = await self._session.append(
                "subagent.spawned",
                {"sub_id": sub_id, "parent_seq": anchor, "task": spec.task},
                actor="agent", origin=SUBA_OWNER)
            h = ChildHandle(sub_id=sub_id, parent_sid=self._session.sid,
                            parent_seq=env.seq, spec=spec, session=child,
                            state="spawning",
                            result=asyncio.get_running_loop().create_future(),
                            started_ts=time.time(),
                            budget_limits=limits, scope=child_scope,
                            slot=True)
            h.env = ChildContext(sub_id=sub_id, session=child, spec=spec,
                                 scope=child_scope, budget_limits=limits,
                                 parent_sid=self._session.sid)
            self._children[sub_id] = h
            h.task = asyncio.create_task(self._run_child(h))
            h.state = "running"
            log.info("subagent spawned sub=%s parent=%s depth=%d task=%.60s",
                     sub_id, self._session.sid, spec.depth, spec.task)
            return sub_id
        except BaseException:
            self._release_slot()                # 派发失败:闸归还(零残留)
            raise

    # ============================================================ 子任务执行回收
    async def _invoke_child(self, h: ChildHandle) -> Any:
        """子执行器调用 seam(偏离 7):runner.run_child(env) 异步/同步兼容。

        未装配 runner → CYC-999 快速失败(防 S-1"伪造已执行":宁失败不静默假完成,
        task_queue._run_runner 同款)。F007 唯一 LLM 入口纪律(INV-02)由 runner
        内部保证——子任务任何 LLM/工具动作都只能发生在 runner 提供的子循环内。
        """
        runner = self._runner
        if runner is None:
            raise_code("CYC-999", hint="SubagentManager 未装配子会话执行器",
                       advice="装配层注入 agent_loop 适配 runner(INV-02 纪律)")
        fn = getattr(runner, "run_child", runner)
        if not callable(fn):
            raise_code("CYC-999", hint=f"子执行器不可调用: {type(runner).__name__}")
        res = fn(h.env)
        if inspect.isawaitable(res):
            res = await res
        if self._charge_hook is not None:       # 子用量计入父任务(偏离 3)
            try:
                r = self._charge_hook(h.env)
                if inspect.isawaitable(r):
                    await r
            except Exception as exc:            # noqa: BLE001 记账失败不阻断回收
                log.warning("charge_hook 失败 sub=%s: %s", h.sub_id, exc)
        return res

    async def _run_child(self, h: ChildHandle) -> None:
        """子会话执行与回收(F049 核心):委派意图交子执行器(独立 session 上跑,
        不占用父会话 running)→ 终态摘要(≤2KB)→ subagent.joined;PyHError/取消 →
        subagent.failed / cancelled;finally 释放并发闸 + 摘登记(已回收即摘)。

        取消分支不吞:re-raise CancelledError 沿 await 链传播(F025);终态审计事件
        由 cancel()/_cancel_all_children() 统一落(见规格 _run_child 注释)。
        """
        try:
            out = await self._invoke_child(h)           # F007 子循环(seam)
            summary = summarize(out, self._summary_max_chars)   # ≤2KB(数据身份)
            if h.spec.notify:                           # 回收写父会话(普通落盘)
                await self._write_event(
                    "subagent.joined",
                    {"sub_id": h.sub_id, "summary": summary},
                    actor="agent", trace={"parent_seq": h.parent_seq})
            h.summary, h.state = summary, "done"
            if h.result is not None and not h.result.done():
                h.result.set_result(summary)
        except asyncio.CancelledError:                  # F025 取消传播:不吞
            h.state = "cancelled"
            if h.result is not None and not h.result.done():
                h.result.cancel()                       # 唤醒 join 等待者(取消态)
            raise
        except PyHError as e:                           # 结构化失败:原码透传
            h.error_code, h.state = e.code, "failed"
            if h.spec.notify:
                await self._write_event(
                    "subagent.failed",
                    {"sub_id": h.sub_id,
                     "summary": summarize(
                         f"[{e.code}] {e.spec.advice}", self._summary_max_chars)},
                    actor="agent", trace={"parent_seq": h.parent_seq})
            if h.result is not None and not h.result.done():
                h.result.set_exception(e)               # join 上抛原 PyHError
        except Exception as e:                          # 未预期兜底:CYC-999
            err = wrap_unexpected(e, "subagent")
            h.error_code, h.state = err.code, "failed"
            if h.spec.notify:
                await self._write_event(
                    "subagent.failed",
                    {"sub_id": h.sub_id,
                     "summary": summarize(
                         f"[{err.code}] {err.spec.advice}", self._summary_max_chars)},
                    actor="agent", trace={"parent_seq": h.parent_seq})
            if h.result is not None and not h.result.done():
                h.result.set_exception(err)
        finally:
            self._finalize(h)                           # 释放闸 + 摘登记(幂等)

    def _finalize(self, h: ChildHandle) -> None:
        """句柄收尾(幂等):并发闸归还(防双放:slot 标记) + 从在途表摘除。"""
        if h.slot:
            h.slot = False
            self._release_slot()
        if self._children.get(h.sub_id) is h:
            self._children.pop(h.sub_id, None)

    # ============================================================ 结果回收
    async def join(self, sub_id: str, *, timeout: Optional[float] = None,
                   ctx: Any = None) -> Optional[str]:
        """主循环 await 子任务终态取回摘要:成功 → summary(≤2KB);失败 → raise 原
        PyHError(回喂可行动文本);timeout → None(调用方决定再等/取消/先干别的);
        子不存在/已回收 → EVT-101(spawn 返回值才是合法 sub_id;结果只可 join 一次)。
        """
        h = self._children.get(sub_id)
        if h is None:
            raise_code("EVT-101", sub_id=sub_id,
                       hint="sub_id 不存在或已回收",
                       advice="spawn 返回值才是合法 sub_id;结果只可 join 一次")
        if h.state in ("spawning", "running"):
            h.state = "joining"                 # 等待期状态(ChildState 词表)
        try:
            return await asyncio.wait_for(h.result, timeout)
        except asyncio.TimeoutError:
            return None                         # 调用方:再等/取消/先干别的

    # ============================================================ 取消(F025)
    async def cancel(self, sub_id: str, *, by: str = "system",
                     reason: str = "user-cancel") -> bool:
        """取消在途子任务并归一化:system.cancelled 审计(父会话)→ 任务 cancel 传播
        → await 其清理(终态由子循环自记 state=cancelled)→ 取消注入早于任务启动
        (停 spawning)则补漏终态;已终态/不存在 → 幂等 False。
        """
        h = self._children.get(sub_id)
        if h is None or h.state in _TERMINAL:
            return False                        # 已终态/不存在 → 幂等 False
        if h.task is not None and not h.task.done():
            h.task.cancel()                     # 沿 await 链传播 CancelledError
        await self._write_event(                # 声明式取消审计(父会话)
            "system.cancelled",
            {"what": f"subagent:{sub_id}", "reason": reason},
            actor="system")
        if h.task is not None:
            await asyncio.gather(h.task, return_exceptions=True)  # 等清理完成
        if self._children.get(sub_id) is h and h.state not in _TERMINAL:
            h.state = "cancelled"               # 取消早于任务启动:补漏终态
            self._finalize(h)                   # 释放闸 + 摘登记(防泄漏)
        return True

    # ============================================================ 状态查询
    def status(self) -> SubagentStatus:
        """并发与在途查询(纯读,零副作用):running ≤limit,各在途 sub_id/state/age。"""
        now = time.time()
        return SubagentStatus(
            running=len(self._children),
            active_children=[{"sub_id": h.sub_id, "state": h.state,
                              "age_s": int(now - h.started_ts)}
                             for h in self._children.values()],
            limit=self._max_concurrent)

    # ============================================================ child-first 清理
    async def _cancel_all_children(self, *, reason: str) -> None:
        """child-first 清理主体:先逐子在途任务声明式取消(审计 + cancel),await
        全部退出,再补漏(取消注入早于任务启动的 spawning 句柄落 subagent.failed
        终态事件 + 释放闸);摘净后才交还调用方(结构上杜绝"父已终态、子还在跑/
        回写 EVT-104")。
        """
        hs = list(self._children.values())
        if not hs:
            return
        for h in hs:
            if h.state in _TERMINAL:
                continue
            if h.task is not None and not h.task.done():
                h.task.cancel()                 # ① 杀孩子(取消传播 F025)
            await self._write_event(            # 终态审计(声明式取消;swallow104)
                "system.cancelled",
                {"what": f"subagent:{h.sub_id}", "reason": reason},
                actor="system")
        await asyncio.gather(*(h.task for h in hs if h.task is not None),
                             return_exceptions=True)      # ② 等全部真正退出
        for h in list(self._children.values()): # ③ 补漏:每条非终态都有终态声明
            if h.state not in _TERMINAL:
                await self._write_event(
                    "subagent.failed",
                    {"sub_id": h.sub_id,
                     "summary": f"[parent-closed] 父会话关闭,子任务终止({reason})"},
                    actor="agent",
                    trace={"parent_seq": h.parent_seq})
                h.state = "cancelled"
            self._finalize(h)                   # ④ 释放闸 + 摘净后才交还

    async def _on_session_closing(self, type_: str,
                                  payload: Any = None) -> None:
        """父会话关闭前 child-first 钩子(bus 订阅签名;announce 挂于 session.closing,
        规范预关钩子,偏离 11):先取消全部在途子任务并 await,落终态事件,随后才
        允许 finished 落盘——从结构上杜绝"父已终态、子还在跑/回写 EVT-104"。
        """
        await self._cancel_all_children(reason="parent-closed")

    # 便捷别名:装配层/测试可直接调 _on_session_closing 语义(类型过滤占位)
    async def close_children(self, *, reason: str = "parent-closed") -> None:
        """child-first 清理公开入口(detach/_on_session_closing 共用;装配层可在
        agent.close 落 finished 前显式触发)。"""
        await self._cancel_all_children(reason=reason)


# ------------------------------------------------------------------ 工具 Provider
class SubagentSpawnProvider:
    """subagent.spawn 工具 Provider 适配器(DIS-SEAM §2.3 Consumer 管道内执行)。

    handle = spawn + join:LLM 一次工具调用即"派发并回收",返回 ≤2KB 摘要——摘要经
    executor 关4 的 tool.result 管道以 tool 数据身份进入主会话派生历史(偏离 8);
    子任务失败(join 上抛原 PyHError)→ executor 落 tool.error 回喂,不中断主循环。
    深度由装配层按 manager 层级推(depth = manager.depth+1),不入 LLM 参数(防谎报
    层级绕过递归闸,见 definition)。
    """

    def __init__(self, manager: SubagentManager) -> None:
        self._mgr = manager

    async def handle(self, args: dict, ctx: Any = None) -> str:
        """Provider 执行面(async;executor 关3 直接协程 wait_for)。"""
        spec = SubagentSpec(
            task=str(args.get("task") or ""),
            tools_subset=args.get("tools_subset"),
            deny_extra=list(args.get("deny_extra") or []),
            budget_ratio=float(args.get("budget_ratio")
                               or DEFAULT_BUDGET_RATIO),
            depth=int(args.get("depth") or self._mgr._depth + 1),
            creds_allowed=list(args.get("creds_allowed") or []),
            notify=bool(args.get("notify", True)))
        sub_id = await self._mgr.spawn(spec, ctx)       # 四道闸内聚(spawn 校验)
        return await self._mgr.join(sub_id, ctx=ctx)    # 摘要 ≤2KB(join 到终态)


__all__ = [
    "SubagentManager", "SubagentSpec", "ChildContext", "ChildHandle",
    "SubagentStatus", "SubagentSpawnProvider", "ChildState",
    "MAX_DEPTH", "MAX_CONCURRENT", "DEFAULT_BUDGET_RATIO",
    "SUMMARY_MAX_CHARS", "TOOL_NAME", "SUBA_OWNER", "summarize",
]

# 向后兼容别名(装配层两种拼写消费)
Subagent = SubagentManager
