"""pyharness/core/scope.py — 会话级策略作用域 (specs/scope.py.md 契约;阶段 1 模块)

功能编号:F014(scope 前置)· F032(预算硬闸)· F021(配置)· F054/F055(沙箱/
workspace 联动)· F059(fork 快照继承)。
权威口径:specs/scope.py.md(编码契约)、DIS-CORE §6(can_use/budget_state/
build_scope 伪代码权威)、ERR.md §2.5(GRD-401)/§2.7(CFG-601)、
EVENT-SCHEMA(budget.paused/scope.updated)。

职责一句话:权限边界(deny/危险标记/域名 allowlist)、预算硬闸(F032)、上下文
窗口、workspace 根的策略唯一数据源——guard 单调拒绝链(原则 3)与 LLM 零信任
预算闸的前置读表;scope 创建/绑定/遮蔽层(fork)/预算联动/注册隔离单点实现,
策略只紧不松(单调:无 relax/un-tighten API)。

依赖方向:scope 无下游脊柱依赖(纯策略容器),只读消费 config/errors/计数器;
被 agent-loop(预算拦截:budget_state/check_budget/window_tokens)、agent
(ctx.scope 挂载)、llm_fallback.BudgetGuard(预算闸,同数据源口径)消费。
计数器对象由装配层经构造注入(scope 本模块只读,report_usage 唯一写入)。

偏离说明(相对 spec 伪码;契约=spec,偏离均列理由):
1. 配置键映射:spec 伪码读 cfg.scope.*(role/deny_tools/allowed_domains/
   workspace_root/sandbox_level),但 config.py(实际权威,9 域 schema)无
   scope.* 域 → build_scope 编译自真实键面:role 无源取默认 "user";deny_tools
   ← security.policy.deny_tools_extra(只增基集);allowed_domains ←
   security.network.allowed_domains;sandbox_level ← security.sandbox.level;
   workspace_root ← storage.workspaces_dir/<session_id>(F055 fresh workspace);
   危险分级 ← security.tool_danger_extra(前置优先)+ 内置默认表(见 7)。
2. 空 deny 越权判定口径:config 层 L1 默认 deny_extra=[] 且无法区分"未配置"
   与"显式空"(pydantic extra=ignore)→ "空 deny=越权"落为 _validate_minimal
   规则:deny 空 **且** 无任何 critical 级危险禁项 → CFG-601(此时 scope 无
   任何防线=全放行,最小权限违约);域名通配过宽 = allowed_domains 含 "*" 一律
   CFG-601(最严最简,防 allow-all;字面域名不受限)。默认内置表含
   fs.delete_file=critical(tool_fs 规格"注册即拒"同源)→ 默认配置天然有防线。
3. 注入式装配:spec 伪码经闭包 ctx 访问 counters/session/bus;而 agent-loop
   同步调用 ctx.scope.check_budget()(无参)→ counters/session/bus 改为构造期
   注入(全部可选;None 时预算按零读数=ok、事件降级日志)——与 llm_fallback
   "注入式装配"同款先例。
4. 事件出口 fire-and-forget:SessionLog.append 为 async,scope 判定面为同步
   (agent-loop 三闸每轮同步调)→ budget.paused/scope.updated 在有运行中事件
   循环时 create_task 排程投递(失败只记日志),无循环/未接线 → 日志降级
   (尽力而为,llm_fallback._fire_emit 同款);budget.warn 走 bus 尽力出口。
   注意:budget.paused/scope.updated 尚未入 events 词表(57 锁定类型之外,与
   llm_fallback budget.paused 现状一致;真实 SessionLog.append 会 EVT-102
   拒写)——词表/payload 模型扩展属 events 模块后续阶段门,本模块以注入日志
   对象为事件出口,测试全替身覆盖。
5. BudgetLimits 字段名对齐 config.py 权威键面(max_in_tokens/max_out_tokens/
   max_cost_yuan/warn_ratio),非 spec 伪码短名(in/out/cost);窗口与告警比例
   配置化:window_tokens ← cfg.loop.max_context_tokens(默认 65536,spec 伪码
   64_000 仅作兜底),window_ratio ← cfg.loop.compact.trigger_ratio(默认 0.75,
   与 F058 同源)。ScopeSnapshot 增 window_tokens/window_ratio 字段以支持
   F059 fork 完整继承(spec 快照伪码只列 window_tokens/taken_at,属补全)。
6. BUSY 未登记码直构 PyHError("BUSY")(agent_loop 偏离 5 同款先例:raise_code
   会把未登记码改写为 CYC-999,语义不符);已登记码(CFG-601/CYC-999)全走
   raise_code(全系统唯一抛出入口纪律)。
7. strict 沙箱域判定为"工具名前缀域"判定(can_use 只有工具名,无路径参数):
   fs./workspace. 视为 workspace 域内(g-fs-path 单点路径约束)→ strict 放行;
   web./net. 需 policy.allowed_domains 非空才可见(allowlist 网络;URL 级仍由
   g-net-outbound 复核);其余域(exec. 等)strict 下不可见——与 DIS §6.6.2
   "strict 沙箱默认仅 workspace 文件 + allowlist 网络"一致;域前缀表为模块
   常量(WORKSPACE_DOMAINS/ALLOWLIST_DOMAINS),工具注册表落地后同源。
8. check_budget 为 spec 函数清单之外的补充 API——agent-loop 消费契约(F032
   闸 2 每轮同步前置检查):超限抛 BudgetExhausted 循环信号异常。BudgetExhausted
   不继承 PyHError(agent_loop 本地定义注:scope.py 落地后改 import;不得被
   PyHError 域捕获逻辑吞掉 → 终态 reason=budget 而非 error);ERR 已登记 LLM-305
   (预算超限)语义=PyHError 域,用于其它路径,非本信号。
9. BudgetState 返回面为 ok/warn/exhausted(DIS §6.3.2 伪码权威):"paused" 是
   budget.paused **事件**名与审批续额预留字面量,本阶段无审批注入 → 状态机不
   产出 paused 返回;check_budget 对 paused/exhausted 一视同仁(防御)。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import PyHError, raise_code
from pyharness.core.llm_fallback import BudgetState, TaskUsage  # noqa: F401 类型复用

log = logging.getLogger("pyharness.scope")

# ------------------------------------------------------------------ 常量
DEFAULT_WINDOW_TOKENS: int = 64_000
"""上下文窗口兜底(spec 伪码 64k;实际默认取 cfg.loop.max_context_tokens,见偏离 5)。"""

DEFAULT_WINDOW_RATIO: float = 0.75
"""窗口收紧阈值(与 F058 compaction.trigger_ratio 同源,可配)。"""

DEFAULT_WARN_RATIO: float = 0.8
"""预算 warn 阈值 80%(F032 默认;实际取 cfg.budget.warn_ratio)。"""

DEFAULT_ROLE: str = "user"
"""会话角色兜底(config 无 scope.role 键,见偏离 1)。"""

# strict 沙箱下视作"workspace 域内"的工具名前缀(g3 g-fs-path 单点路径约束)
WORKSPACE_DOMAINS: frozenset = frozenset({"fs", "workspace"})
# 需域名 allowlist(非空)才在 strict 下可见的工具名前缀(g5 g-net-outbound URL 级复核)
ALLOWLIST_DOMAINS: frozenset = frozenset({"web", "net"})

# 内置危险分级默认表(F023 同源示意;can_use 只消费 critical——不可审批直接不可用;
# high 级转审批由 guard 链 g-danger 按工具定义处理,scope 层不拦)。
# fs.delete_file=critical 与 tool_fs 规格"danger=critical 注册即拒"同源(审计锚点)。
DEFAULT_DANGER_MARKS: tuple[dict[str, str], ...] = (
    {"pattern": "fs.delete_file", "level": "critical"},
    {"pattern": "exec.*", "level": "high"},
    {"pattern": "net.*", "level": "high"},
)

_DANGER_RANK: dict[str, int] = {"none": 0, "low": 1, "high": 2, "critical": 3}


# ---------------------------------------------------------------- 数据结构
@dataclass
class BudgetLimits:
    """预算硬闸上限(F032/CFG budget.task 编译;warn_ratio 为告警比例)。

    字段名对齐 config.py BudgetTaskCfg 权威键面(见偏离 5);值全部来自 config,
    启动编译后运行期只读(预算键禁热更 → 无漂移,CFG-608 语义)。
    """

    max_in_tokens: int = 2_000_000     # 输入 ≤200 万 token
    max_out_tokens: int = 50_000       # 输出 ≤5 万 token(硬闸主判据)
    max_cost_yuan: float = 1.0         # 估算成本 ≤¥1(F032 默认,CONSTRAINTS-07 C-01)
    warn_ratio: float = DEFAULT_WARN_RATIO   # 80% 提醒,只提醒不拦

    @classmethod
    def from_cfg(cls, cfg: Any) -> "BudgetLimits":
        """从 Settings 编译(L1 缺省必可加载;键缺省回落默认值)。"""
        t = getattr(cfg, "budget", None).task if getattr(cfg, "budget", None) else None
        if t is None:
            return cls()
        return cls(
            max_in_tokens=int(getattr(t, "max_in_tokens", 2_000_000)),
            max_out_tokens=int(getattr(t, "max_out_tokens", 50_000)),
            max_cost_yuan=float(getattr(t, "max_cost_yuan", 1.0)),
            warn_ratio=float(getattr(cfg.budget, "warn_ratio", DEFAULT_WARN_RATIO)),
        )

    def model_dump(self) -> dict:
        """值拷贝序列化(快照/审计用;与 pydantic model_dump 同名同语义)。"""
        return {"max_in_tokens": self.max_in_tokens,
                "max_out_tokens": self.max_out_tokens,
                "max_cost_yuan": self.max_cost_yuan,
                "warn_ratio": self.warn_ratio}


@dataclass
class ScopePolicy:
    """会话策略(role/deny/危险分级/域名 allowlist/workspace 根/沙箱级别)。

    从 config+会话编译(最小权限);fork 经 ScopeSnapshot 值拷贝继承(F059)。
    deny_tools 是策略显式禁令(只增,运行期经 tighten 追加);danger_marks 是
    分级查表(critical → can_use 终局 False);两者互为防线(见偏离 2)。
    """

    role: str = DEFAULT_ROLE                       # 会话角色(提示词变量源)
    deny_tools: set[str] = field(default_factory=set)       # 显式禁止表
    danger_marks: list[dict[str, str]] = field(default_factory=list)  # 分级规则
    allowed_domains: set[str] = field(default_factory=set)  # 域名 allowlist
    workspace_root: str = ""                       # workspace 根(F055)
    sandbox_level: str = "strict"                  # strict/basic/off(F054)

    def model_dump(self) -> dict:
        """值拷贝序列化(ScopePolicy(**model_dump()) 可逆;快照/继承用)。"""
        return {"role": self.role,
                "deny_tools": set(self.deny_tools),
                "danger_marks": [dict(m) for m in self.danger_marks],
                "allowed_domains": set(self.allowed_domains),
                "workspace_root": self.workspace_root,
                "sandbox_level": self.sandbox_level}


@dataclass(frozen=True)
class ScopeSnapshot:
    """全策略只读快照(fork/审批复核/审计用)。

    frozen=True:任何顶层字段赋值 → FrozenInstanceError(结构不可变);
    内嵌 policy/limits 均为值拷贝——快照不随原 scope 后续 tighten 变化。
    """

    policy: ScopePolicy
    limits: BudgetLimits
    window_tokens: int = DEFAULT_WINDOW_TOKENS
    window_ratio: float = DEFAULT_WINDOW_RATIO
    taken_at: str = ""                             # 审计时间戳(ISO 8601)


# ------------------------------------------------------------ 循环信号异常
class BudgetExhausted(Exception):
    """F032 预算硬闸信号:scope.check_budget() 超限抛出 → agent-loop 终态 reason=budget。

    不继承 PyHError:不得被 agent_loop 的 PyHError 域捕获逻辑吞掉(本地定义注
    明 scope.py 落地后改 import,见偏离 8)。预算事件预算本由 budget_state 落
    budget.paused,本异常只作循环控制信号。
    """


# ---------------------------------------------------------------- 会话注册表
_scopes: dict[str, "Scope"] = {}
"""session_id → Scope 一对一注册表(注册隔离:每会话恰一 scope,仅 release 解绑)。

脊柱八模块保留名(scope)的注册/卸载隔离归 bus/registry(BUS-002,已预留);
本表只保证"会话级一对一"与重复 create 拒绝(F021)。
"""


def _now_iso() -> str:
    """UTC 审计时间戳(ISO 8601,秒精度)。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh_workspace(root: str, session_id: str) -> str:
    """F055 fresh workspace:storage.workspaces_dir/<session_id>(不落盘,仅策略值)。"""
    return str(Path(root).expanduser() / session_id)


def _load_danger_marks(cfg: Any) -> list[dict[str, str]]:
    """危险分级表编译:cfg extra(精确名,前置优先)∪ 内置默认表。

    security.tool_danger_extra 只许上调(validate_rules 已按基线复核),前置保证
    精确名规则覆盖同名前缀通配;can_use 只消费 critical(终局),high 属分级
    信息(审批由 guard 链按工具定义处理)。
    """
    extra = getattr(getattr(cfg, "security", None), "tool_danger_extra", None) or {}
    marks = [{"pattern": str(tool), "level": str(lvl)}
             for tool, lvl in sorted(extra.items())]
    marks.extend({"pattern": str(m["pattern"]), "level": str(m["level"])}
                 for m in DEFAULT_DANGER_MARKS)
    return marks


# ------------------------------------------------------------------ Scope
class Scope:
    """会话级策略作用域实例(策略唯一数据源;单调收紧,无 relax)。

    状态机(§6.4):UNBOUND ─build_scope→ BOUND(默认最小权限)─guard 拒绝后
    tighten→ HARDENED(只紧不松,至会话结束无回退)─release→ RELEASED;
    启动失败/配置越权 → CFG-601(拒绝启动会话)。预算为子状态:超任一硬闸 →
    budget.paused 事件 + exhausted(agent-loop 强制终态 reason=budget)。

    counters/session/bus 构造期注入(偏离 3):counters 只读(task_total),
    session 为事件落点,None = 未接线(事件日志降级)。
    """

    def __init__(self, policy: ScopePolicy, limits: BudgetLimits,
                 session_id: str, *,
                 window_tokens: Optional[int] = None,
                 window_ratio: Optional[float] = None,
                 counters: Any = None, session: Any = None,
                 bus: Any = None, parent_snap: Optional[ScopeSnapshot] = None) -> None:
        self.policy: Optional[ScopePolicy] = policy
        self.limits: Optional[BudgetLimits] = limits
        self.session_id: str = session_id
        self.window_tokens: int = window_tokens or DEFAULT_WINDOW_TOKENS
        self.window_ratio: float = window_ratio or DEFAULT_WINDOW_RATIO
        self.parent_snap: Optional[ScopeSnapshot] = parent_snap  # 遮蔽层父快照(F059)
        self._counters: Any = counters              # 任务级用量聚合器(只读)
        self._session: Any = session                # 事件落点(可 None)
        self._bus: Any = bus                        # warn 告警尽力出口(可 None)
        self._budget_state: BudgetState = "ok"      # 事件去重水位(非状态真源)
        self._tasks: set[asyncio.Task] = set()      # fire-and-forget 任务登记

    # ============================================================ 前置查权
    def can_use(self, tool_name: str) -> bool:
        """guard 单调链 scope 前置(F014/DIS §6.3.1):deny/strict 域外/critical
        任一命中 → False;调用方在 False 时写 guard.rejected(GRD-401,scope-hidden)
        强同步,Provider 零执行(INV-05)。high 危险此处放行,转审批(F015)。"""
        self._ensure_bound()
        p = self.policy
        if tool_name in p.deny_tools:               # 显式禁止(只增,单调)
            return False
        if (p.sandbox_level == "strict"
                and not self._inside_workspace_domain(tool_name)):
            return False                            # strict:仅 workspace 域 + allowlist 网络
        if self._danger_mark(tool_name) == "critical":
            return False                            # critical 不可审批,本层即终局
        return True

    # ============================================================ 单调收紧
    def tighten(self, deny: set[str], *, reason: str) -> None:
        """策略只紧不松:追加 deny,写 scope.updated 事件(留痕可审计)。

        幂等:deny ⊆ 现有 → 直接返回不产生事件(防刷日志);只追加(单调),无
        relax/un_tighten API——放宽在结构上不可能(GWT-S6-01:hasattr 断言
        AttributeError)。reason 必填(sandbox strict/guard 拒绝等触发源)。
        """
        self._ensure_bound()
        if not deny or deny <= self.policy.deny_tools:
            return                                  # 无新增:幂等返回,不落事件
        self.policy.deny_tools |= deny              # 只追加(单调)
        self._record("scope.updated", {"op": "tighten", "added": sorted(deny),
                                       "reason": reason})

    # ============================================================ 预算硬闸
    def budget_state(self) -> BudgetState:
        """预算硬闸(F032/DIS §6.3.2):读计数器判 ok/warn/exhausted。

        硬闸只用 token 数(out/in/cost 任一 ≥ 上限即 exhausted,F032 判定以
        token 为主);warn=80%(只提醒不拦)。状态迁移事件化:首达 exhausted →
        budget.paused 事件落日志;首达 warn → bus budget.warn(尽力而为)。
        agent-loop._must_stop 对 paused/exhausted 强制终态(reason=budget)。
        计数器 None(未注入)→ 按零读数返回 ok(事件降级,偏离 3)。
        """
        self._ensure_bound()
        used = self._usage()
        lim = self.limits
        if (used.out_tokens >= lim.max_out_tokens
                or used.in_tokens >= lim.max_in_tokens
                or used.cost_est >= lim.max_cost_yuan):
            if self._budget_state != "exhausted":   # 首达才落事件(防刷)
                self._record("budget.paused",
                             {"state": "exhausted", "used": self._used_dict(used)})
                self._budget_state = "exhausted"
            return "exhausted"
        if used.out_tokens >= lim.warn_ratio * lim.max_out_tokens:
            if self._budget_state != "warn":
                self._emit_bus("budget.warn", {"used_out": used.out_tokens})
                self._budget_state = "warn"
            return "warn"
        self._budget_state = "ok"
        return "ok"

    def check_budget(self) -> BudgetState:
        """agent-loop 每轮预算前置检查(F032 闸 2):超限抛 BudgetExhausted 信号。

        同步签名(agent-loop 同步调用);exhausted → raise BudgetExhausted
        (循环控制信号,非 PyHError——终态 reason=budget,见偏离 8);
        paused 为防御性同判;ok/warn 原样返回不拦。
        """
        st = self.budget_state()
        if st in ("paused", "exhausted"):
            raise BudgetExhausted(
                f"任务预算{st}:硬闸超限,详见 budget.paused 事件(agent-loop 终态 "
                f"reason=budget)")
        return st

    # ============================================================ 窗口判定
    def within_window(self, hist_tokens: int) -> bool:
        """窗口余量判定(F058 压缩触发同源):历史仍在窗口内(余量≥25%)为 True。

        纯只读判定,与 window_tokens/window_ratio 单一数据源联动;release 后
        仍可用(窗口为纯配置标量,不随策略释放)。
        """
        return hist_tokens < self.window_ratio * self.window_tokens

    # ============================================================ 只读快照
    def snapshot(self) -> ScopeSnapshot:
        """全策略不可变只读快照(fork/审批复核/审计)。

        值拷贝:快照不随原 scope 后续 tighten 变化;ScopeSnapshot frozen=True,
        任何字段赋值 → FrozenInstanceError(结构不可变)。
        """
        self._ensure_bound()
        return ScopeSnapshot(
            policy=ScopePolicy(**self.policy.model_dump()),   # 值拷贝(COW 起点)
            limits=BudgetLimits(**self.limits.model_dump()),
            window_tokens=self.window_tokens,
            window_ratio=self.window_ratio,
            taken_at=_now_iso())

    # ============================================================ 解绑释放
    def release(self) -> None:
        """会话关闭/异常时解绑:从会话作用域表摘除 + 清内存策略引用。

        幂等:重复 release 无害;只摘除本 scope(防误释放他 scope)。
        凭据从未进入 ScopePolicy(读取走 F016 单口),此处无脱敏负担(INV-09);
        release 后查询(budget_state/can_use/tighten/snapshot)→ CYC-999 拒。
        """
        sid = self.session_id
        if sid in _scopes and _scopes[sid] is self:
            del _scopes[sid]                        # 解绑(注册隔离收尾)
        self.policy = None                          # 引用清空(GC 可回收)
        self.limits = None

    # ============================================================ 内部辅助
    def _ensure_bound(self) -> None:
        """RELEASED 后查询拒(CYC-999:会话已释放仍被使用=未预期调用路径)。"""
        if self.policy is None or self.limits is None:
            raise_code("CYC-999",
                       hint=f"scope 已 release(session={self.session_id});"
                            "会话已关闭,查询被拒(重新 build_scope)")

    def _usage(self) -> TaskUsage:
        """读注入计数器任务级聚合;未注入/返回空 → 零读数(事件降级,见偏离 3)。"""
        c = self._counters
        if c is None:
            log.debug("scope=%s 未注入计数器,预算按零读数(ok)", self.session_id)
            return TaskUsage()
        used = c.task_total()
        return used if used is not None else TaskUsage()

    @staticmethod
    def _used_dict(used: Any) -> dict:
        """用量序列化:优先 TaskUsage.model_dump,缺省按字段兜底(鸭子类型)。"""
        if hasattr(used, "model_dump"):
            return used.model_dump()
        return {"in_tokens": getattr(used, "in_tokens", 0),
                "out_tokens": getattr(used, "out_tokens", 0),
                "cost_est": getattr(used, "cost_est", 0.0)}

    def _inside_workspace_domain(self, tool_name: str) -> bool:
        """strict 沙箱域判定(偏离 7):工具名前缀域 ∈ workspace 域 → True;
        ∈ allowlist 域且域名 allowlist 非空 → True(g5 再按 URL 复核);否则 False。"""
        domain = tool_name.split(".", 1)[0] if "." in tool_name else tool_name
        if domain in WORKSPACE_DOMAINS:
            return True
        if domain in ALLOWLIST_DOMAINS and self.policy.allowed_domains:
            return True
        return False

    def _danger_mark(self, tool_name: str) -> str:
        """危险分级查表(fnmatch 前缀规则,命中即返;表与 F023 guard 同源加载)。

        critical → can_use False(不可审批,§6.3.1);high → 放行转审批(F015);
        未命中 → "none"。cfg extra 精确名规则前置(先命中先返)保证覆盖默认通配。
        """
        for rule in self.policy.danger_marks:
            if fnmatchcase(tool_name, str(rule["pattern"])):
                return str(rule["level"])
        return "none"

    def _record(self, type_: str, payload: dict) -> None:
        """策略/预算事件落日志(scope.updated/budget.paused 留痕,审计用)。

        SessionLog.append 为 async、scope 判定面为同步(偏离 4):append 返回
        awaitable 且有运行中事件循环 → create_task 排程投递(fire-and-forget,
        失败只记日志);append 同步(FakeSession)→ 直接记录;未接线 → 日志降级。
        """
        sess = self._session
        if sess is None:
            log.debug("scope=%s 未接线事件出口,%s 未记录", self.session_id, type_)
            return
        try:
            r = sess.append(type_, payload, actor="system")
        except Exception as exc:                    # noqa: BLE001 事件失败不阻断策略主路径
            log.warning("scope append %s 失败: %s", type_, exc)
            return
        if inspect.isawaitable(r):
            try:
                asyncio.get_running_loop()
            except RuntimeError:                    # 无运行中事件循环:同步/未装配上下文
                log.warning("scope=%s 无运行中事件循环,%s 未投递",
                            self.session_id, type_)
                return
            task = asyncio.create_task(self._safe_append(r, type_))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _safe_append(self, coro: Any, type_: str) -> None:
        """异步落点包装:append 失败只记日志,不向调用方传播(尽力而为)。"""
        try:
            await coro
        except Exception as exc:                     # noqa: BLE001 EVT-102(词表外)等
            log.warning("scope append %s 异步失败: %s", type_, exc)

    def _emit_bus(self, type_: str, payload: dict) -> None:
        """bus 尽力而为出口(budget.warn 告警;未接线/失败 → 日志降级,不阻断)。"""
        bus = self._bus
        if bus is None:
            return
        try:
            r = bus.emit(type_, payload)
            if inspect.isawaitable(r):
                try:
                    asyncio.get_running_loop().create_task(r)
                except RuntimeError:                # 无运行中事件循环:日志降级
                    log.warning("scope=%s 无运行中事件循环,%s 未投递",
                                self.session_id, type_)
        except Exception as exc:                     # noqa: BLE001 未注册类型等
            log.warning("scope emit %s 失败: %s", type_, exc)


# ------------------------------------------------------------------ 工厂
def _validate_minimal(p: ScopePolicy) -> None:
    """最小权限编译校验(F021;越权配置 → CFG-601 列字段,拒启动会话)。

    两种越权(见偏离 2):
    1) 空 deny 且无任何 critical 禁项 = scope 无防线(全放行)→ 拒;
    2) allowed_domains 含通配 "*" = allow-all/过宽(域名 allowlist 只收字面
       域名,URL 级匹配由 g5 做)→ 拒。
    """
    if not p.deny_tools and not any(
            _DANGER_RANK.get(str(m.get("level")), 0) >= _DANGER_RANK["critical"]
            for m in p.danger_marks):
        raise_code("CFG-601", reason="越权",
                   fields=["security.policy.deny_tools_extra"],
                   detail="deny 空且无 critical 禁项=scope 无防线(最小权限须留"
                          "至少一道拒绝面;默认禁高危组)")
    for d in sorted(p.allowed_domains):
        if "*" in d:
            raise_code("CFG-601", reason="越权",
                       fields=["security.network.allowed_domains"],
                       detail=f"域名通配过宽:{d}(allowlist 只收字面域名,禁 "
                              "allow-all/通配)")


def build_scope(cfg: Any, session_id: str, *,
                session: Any = None, counters: Any = None,
                bus: Any = None) -> Scope:
    """作用域创建与绑定(F021/DIS §6.3.3):编译默认最小权限策略并注册到会话。

    cfg = Settings(或含 security/budget/loop/storage 域的分层配置对象);
    每会话恰一 scope:session_id 已在册 → BUSY 拒(注册隔离;仅 release 解绑)。
    越权配置(空 deny 无防线/域名通配过宽)→ CFG-601 列非法字段,拒绝启动。
    session/counters/bus 为注入式装配可选接线(偏离 3)。

    异常:PyHError(BUSY,重复 build)/ConfigError(CFG-601,配置越权)。
    """
    if session_id in _scopes:
        raise PyHError("BUSY", ctx={
            "session_id": session_id,
            "advice": "该会话已有活动 scope;重复 create active scope 被拒"
                      "(注册隔离,每会话恰一 scope)"})
    sandbox = getattr(getattr(cfg, "security", None), "sandbox", None)
    p = ScopePolicy(
        role=DEFAULT_ROLE,                           # config 无 scope.role 键(偏离 1)
        deny_tools=set(getattr(
            getattr(getattr(cfg, "security", None), "policy", None),
            "deny_tools_extra", None) or []),        # 基集外只增(安全默认)
        danger_marks=_load_danger_marks(cfg),        # 内置分级表 + extra 上调
        allowed_domains=set(getattr(
            getattr(getattr(cfg, "security", None), "network", None),
            "allowed_domains", None) or []),
        workspace_root=_fresh_workspace(
            getattr(getattr(cfg, "storage", None), "workspaces_dir",
                    "~/.pyharness/workspaces"),
            session_id),                             # F055 fresh workspace
        sandbox_level=getattr(sandbox, "level", "strict") or "strict")  # 默认 strict
    _validate_minimal(p)                             # 越权拒绝(CFG-601)
    window = getattr(getattr(cfg, "loop", None), "max_context_tokens", None)
    ratio = getattr(getattr(getattr(cfg, "loop", None), "compact", None),
                    "trigger_ratio", None)
    s = Scope(p, BudgetLimits.from_cfg(cfg), session_id,
              window_tokens=int(window or DEFAULT_WINDOW_TOKENS),
              window_ratio=float(ratio or DEFAULT_WINDOW_RATIO),
              session=session, counters=counters, bus=bus)
    _scopes[session_id] = s                          # 注册(绑定;scope.updated 留痕)
    return s


def from_snapshot(snap: ScopeSnapshot, session_id: str, *,
                  session: Any = None, counters: Any = None,
                  bus: Any = None) -> Scope:
    """遮蔽层创建(F059 fork 继承):从父快照拷贝策略独立演进。

    子层 = 父策略快照 + 独立注册;子层 tighten 只追加本层 deny,不透写父层;
    父会话后续 tighten 不影响子会话(遮蔽,无透写,COW 起点)。
    重复创建 → BUSY(与 build_scope 同注册隔离)。
    """
    if session_id in _scopes:
        raise PyHError("BUSY", ctx={
            "session_id": session_id,
            "advice": "子会话已有活动 scope;重复 fork 被拒(查重复 fork 路径)"})
    p = ScopePolicy(**snap.policy.model_dump())      # 深拷贝策略(COW 起点)
    s = Scope(p, BudgetLimits(**snap.limits.model_dump()),
              session_id,
              window_tokens=snap.window_tokens,
              window_ratio=snap.window_ratio,
              counters=counters, session=session, bus=bus,
              parent_snap=snap)                     # 遮蔽层记父快照(F059)
    _scopes[session_id] = s                          # 独立注册(隔离于父表)
    return s


def active_scope(session_id: str) -> Optional[Scope]:
    """只读查询:会话当前绑定 scope;未绑定/已释放返回 None(审计/外壳用)。"""
    return _scopes.get(session_id)


__all__ = [
    # 数据
    "BudgetLimits", "ScopePolicy", "ScopeSnapshot",
    # 预算类型/信号(llm_fallback 同源复用;BudgetExhausted 由 agent-loop 反向 import)
    "BudgetState", "TaskUsage", "BudgetExhausted",
    # 核心
    "Scope", "build_scope", "from_snapshot", "active_scope",
    # 常量(域前缀表,工具注册表落地后同源)
    "WORKSPACE_DOMAINS", "ALLOWLIST_DOMAINS", "DEFAULT_DANGER_MARKS",
    "DEFAULT_WINDOW_TOKENS", "DEFAULT_WINDOW_RATIO",
]
