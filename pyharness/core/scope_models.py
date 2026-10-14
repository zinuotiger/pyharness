"""Scope models: complete migrated implementation."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Optional


log = logging.getLogger("pyharness.scope")

DEFAULT_WINDOW_TOKENS: int = 64_000

DEFAULT_WINDOW_RATIO: float = 0.75

DEFAULT_WARN_RATIO: float = 0.8

DEFAULT_ROLE: str = "user"

WORKSPACE_DOMAINS: frozenset = frozenset({"fs", "workspace", "storage"})

ALLOWLIST_DOMAINS: frozenset = frozenset({"web", "net"})

SELF_DOMAINS: frozenset = frozenset({"goal", "todo", "plan", "util", "user",
                                     "skill", "session", "schedule",
                                     "subagent"})

RESTRICTED_DOMAINS: frozenset = frozenset({"exec", "proc"})

DEFAULT_DANGER_MARKS: tuple[dict[str, str], ...] = (
    {"pattern": "fs.delete_file", "level": "critical"},
    {"pattern": "exec.*", "level": "high"},
    {"pattern": "net.*", "level": "high"},
)

_DANGER_RANK: dict[str, int] = {"none": 0, "low": 1, "high": 2, "critical": 3}

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
    # workspace 外**只读**例外目录(A2/R24 接线 `security.policy.read_extra_dirs`):
    # 仅对**读类**工具放行,写类一律仍拒;两层(g3 与 provider)同判。默认为空 = 无例外。
    read_extra_dirs: list[str] = field(default_factory=list)
    sandbox_level: str = "strict"                  # strict/basic/off(F054)
    allowed_tools: Optional[set[str]] = None        # None inherits; empty permits none

    def model_dump(self) -> dict:
        """值拷贝序列化(ScopePolicy(**model_dump()) 可逆;快照/继承用)。"""
        return {"role": self.role,
                "deny_tools": set(self.deny_tools),
                "allowed_tools": None if self.allowed_tools is None else set(self.allowed_tools),
                "danger_marks": [dict(m) for m in self.danger_marks],
                "allowed_domains": set(self.allowed_domains),
                "workspace_root": self.workspace_root,
                "read_extra_dirs": list(self.read_extra_dirs),
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

class BudgetExhausted(Exception):
    """F032 预算硬闸信号:scope.check_budget() 超限抛出 → agent-loop 终态 reason=budget。

    不继承 PyHError:不得被 agent_loop 的 PyHError 域捕获逻辑吞掉(本地定义注
    明 scope.py 落地后改 import,见偏离 8)。预算事件预算本由 budget_state 落
    budget.paused,本异常只作循环控制信号。
    """

# Preserve historical import and pickle identities through the compatibility facade.
BudgetLimits.__module__ = 'pyharness.core.scope'
ScopePolicy.__module__ = 'pyharness.core.scope'
ScopeSnapshot.__module__ = 'pyharness.core.scope'
BudgetExhausted.__module__ = 'pyharness.core.scope'
