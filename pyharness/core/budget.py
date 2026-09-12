"""pyharness/core/budget.py — 预算门面(只读快照面,BudgetGate)。

一句话职责:把**已在链上**的预算数据源(Scope 预算硬闸的 BudgetLimits + 会话级
UsageCounters,均来自 F032 装配)聚合为一个只读门面,供外壳的预算仪表盘与
`/api/budget` 查询面消费——不引入第二套预算状态,不新增写路径。

设计纪律(原则 1:单一真源):
- 计数真源 = events 日志的 llm.usage 事件(UsageCounters 是它的投影,可整体重建);
  BudgetGate 只读 `counters` 与 `limits`,自身零状态、零写入、零事件。
- 与 Scope.budget_state() **同源同判定**:同一个 counters 实例 + 同一份 limits
  (engine 装配时注入同一对象),因此门面读数与 agent-loop 的强制终态判据一致,
  不会出现"仪表盘说没超、循环说超了"的双口径。
- 硬闸/终态归 agent-loop + Scope(见 scope.py/L4);本模块**不拦不抛**。

历史缺口(2026-09-12 修复):装配层从未赋值 ctx.budget,导致
`ApplicationService.budget_dashboard` 恒回 {"disabled": true, "reason":
"budget-gate-unassembled"},桌面预算仪表盘只有 token 报表、没有成本闸位。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pyharness.core.scope import BudgetLimits


@dataclass(frozen=True)
class BudgetSnapshot:
    """预算只读快照(值拷贝;frozen 防门面被当状态机改)。"""

    sid: str
    state: str                      # ok / warn / exhausted(与 scope 同判定)
    limit_cny: float                # 成本上限(元)
    used_cny: float                 # 已用估算成本(元)
    ratio: float                    # used_cny / limit_cny(limit=0 → 0.0)
    warn_ratio: float               # 告警比例(默认 0.8)
    warned: bool                    # ratio >= warn_ratio
    limit_in_tokens: int
    limit_out_tokens: int
    used_in_tokens: int
    used_out_tokens: int
    requests: int

    def model_dump(self) -> dict:
        return {"sid": self.sid, "state": self.state,
                "limit_cny": self.limit_cny, "used_cny": self.used_cny,
                "ratio": self.ratio, "warn_ratio": self.warn_ratio,
                "warned": self.warned,
                "limit_in_tokens": self.limit_in_tokens,
                "limit_out_tokens": self.limit_out_tokens,
                "used_in_tokens": self.used_in_tokens,
                "used_out_tokens": self.used_out_tokens,
                "requests": self.requests}


class BudgetGate:
    """会话预算门面(只读):counters + limits → snapshot / state。

    用法(外壳只读消费):
        snap = gate.snapshot(sid); snap.limit_cny, snap.used_cny, snap.ratio
    `scope` 可选注入:注入时 state 直接取 Scope.budget_state()(含 warn/exhausted
    迁移语义与 budget.paused 事件副作用),否则由本门面按同一判据纯计算(不落事件)。
    """

    def __init__(self, *, limits: Any = None, counters: Any = None,
                 scope: Optional[Any] = None, session_id: str = "") -> None:
        self.limits = limits if limits is not None else BudgetLimits()
        self.counters = counters
        self.scope = scope
        self.session_id = session_id

    # ------------------------------------------------------------ 只读读数
    def _usage(self) -> tuple[int, int, float, int]:
        """(in, out, cost, requests) 现读;counters 缺失 → 全零(不猜)。"""
        c = self.counters
        if c is None:
            return 0, 0, 0.0, 0
        return (int(getattr(c, "in_tokens", 0) or 0),
                int(getattr(c, "out_tokens", 0) or 0),
                float(getattr(c, "cost_est", 0.0) or 0.0),
                int(getattr(c, "requests", 0) or 0))

    def state(self) -> str:
        """与 Scope.budget_state() 同判定:注入 scope 时直接复用其状态机。"""
        if self.scope is not None:
            try:
                return str(self.scope.budget_state())
            except Exception:                            # noqa: BLE001 只读降级
                pass
        return self._compute_state()

    def _compute_state(self) -> str:
        used_in, used_out, used_cny, _ = self._usage()
        lim = self.limits
        if (used_out >= getattr(lim, "max_out_tokens", 0)
                or used_in >= getattr(lim, "max_in_tokens", 0)
                or used_cny >= float(getattr(lim, "max_cost_yuan", 0.0))):
            return "exhausted"
        warn_ratio = float(getattr(lim, "warn_ratio", 0.8))
        if (used_out >= warn_ratio * getattr(lim, "max_out_tokens", 0)
                or used_cny >= warn_ratio * float(getattr(lim, "max_cost_yuan", 0.0))):
            return "warn"
        return "ok"

    def snapshot(self, sid: Optional[str] = None) -> BudgetSnapshot:
        """只读快照(幂等;不写日志、不落事件、不改任何状态)。"""
        used_in, used_out, used_cny, requests = self._usage()
        lim = self.limits
        limit_cny = float(getattr(lim, "max_cost_yuan", 0.0) or 0.0)
        warn_ratio = float(getattr(lim, "warn_ratio", 0.8))
        ratio = (used_cny / limit_cny) if limit_cny else 0.0
        return BudgetSnapshot(
            sid=str(sid or self.session_id or ""),
            state=self.state(),
            limit_cny=limit_cny, used_cny=round(used_cny, 6),
            ratio=round(ratio, 4), warn_ratio=warn_ratio,
            warned=ratio >= warn_ratio,
            limit_in_tokens=int(getattr(lim, "max_in_tokens", 0) or 0),
            limit_out_tokens=int(getattr(lim, "max_out_tokens", 0) or 0),
            used_in_tokens=used_in, used_out_tokens=used_out,
            requests=requests)


__all__ = ["BudgetGate", "BudgetSnapshot"]
