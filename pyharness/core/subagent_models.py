"""Subagent models: complete migrated implementation."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
from pyharness.errors import PyHError
from pyharness.core.result_text import render_result_text


log = logging.getLogger("pyharness.subagent")

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

# Preserve historical import and pickle identities through the compatibility facade.
SubagentSpec.__module__ = 'pyharness.core.subagent'
ChildContext.__module__ = 'pyharness.core.subagent'
ChildHandle.__module__ = 'pyharness.core.subagent'
SubagentStatus.__module__ = 'pyharness.core.subagent'
