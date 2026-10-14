"""Llm usage: complete migrated implementation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional
if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    pass

from pyharness.core.llm_types import ToolCall, UsageCounters, _l1_settings

def estimate_cost(model: str, usage: Any, prices: dict, hit_discount: float = 0.1) -> float:
    """成本估算(ADI §8.2,元/百万):未命中输入 + 命中输入×折扣 + 输出;只进事件/报表(N14)。

    usage 无缓存字段厂商按全价估算(保守);prices 缺模型直取 KeyError 属调用方先查表(偏离 4)。
    hit_discount 默认 0.1(缓存命中官方约 1/10 价)——原默认 1.0 使命中按全价计,成本恒高估
    (P3:estimate_cost 折扣)。调用方未传折扣时取真实折扣,而非全价。
    """
    p = prices[model]                              # {in_per_million, out_per_million}
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    if not (hit or miss):                          # 无缓存字段厂商:prompt 全按未命中
        miss = getattr(usage, "prompt_tokens", 0) or 0
    billed_in = miss + hit * hit_discount          # 命中按折扣价(官方约 1/10)
    out = getattr(usage, "completion_tokens", 0) or 0
    return billed_in / 1e6 * p["in_per_million"] + out / 1e6 * p["out_per_million"]

def _price_table(unit_price: Any) -> dict:
    """config 单价表(pydantic UnitPriceCfg 或 dict)→ 纯 dict {模型: {in,out}}。"""
    out: dict = {}
    for name, p in (unit_price or {}).items():
        if isinstance(p, dict):
            out[name] = {"in_per_million": float(p["in_per_million"]),
                         "out_per_million": float(p["out_per_million"])}
        else:                                      # pydantic 模型:attr 取数
            out[name] = {"in_per_million": float(p.in_per_million),
                         "out_per_million": float(p.out_per_million)}
    return out

def _llm_cfg_from_ctx(ctx: Any, fallback: Any = None) -> Any:
    """逐级解析 llm 配置段:ctx.config.llm → 注入 cfg.llm → L1 默认(键缺省必可加载)。"""
    cfg = getattr(ctx, "config", None)
    llm = getattr(cfg, "llm", None) if cfg is not None else None
    if llm is None and fallback is not None:
        llm = getattr(fallback, "llm", None)
    if llm is None:
        llm = _l1_settings().llm
    return llm

def _request_payload(model: str, degraded_from: Optional[str], tools: Any) -> dict:
    """llm.request payload(EVENT-SCHEMA 字段级;降级来源非空才带)。"""
    from pyharness.core.llm_diagnostics import call_role, call_attempt
    p: dict = {"model": model, "n_tools": len(tools or []),
               "role": call_role.get(), "attempt": call_attempt.get()}
    if degraded_from:
        p["degraded_from"] = degraded_from
    return p

def _response_payload(model: str, finish_reason: str, content: str,
                      calls: list[ToolCall], request_seq=None) -> dict:
    """llm.response payload:content 与 tool_calls 至少其一;arguments 原文回填(INV-06)。"""
    from pyharness.core.llm_diagnostics import call_role
    p: dict = {"model": model, "finish_reason": finish_reason, "content": content,
               "role": call_role.get(), "request_seq": request_seq}
    if calls:
        p["tool_calls"] = [{"id": c.id, "name": c.name, "arguments": c.raw_json}
                           for c in calls]
    return p

async def report_usage(usage: Any, model: str, *, ctx: Any,
                       counters: Optional[UsageCounters] = None) -> Any:
    """F029 token 计量:每成功请求落 llm.usage 事件 + 更新 UsageCounters(F029 唯一写入)。

    usage 事件即事实,计数器可整体重建(INV-01);预算硬闸判据 = **token 与 cost 并列**
    (out/in/cost_est 任一 ≥ 上限即 exhausted;cost 由**软约束**单价表估算,上限见 CFG §3.1
    `budget.task.max_cost_yuan` —— 实测「纯成本超限」即拦,R21 更正了旧措辞「只读 token 数」);
    价格表缺模型 → cost_est 记 0 照常记账(偏离 4);缺失缓存字段按 0,不做厂商猜。
    """
    llm_cfg = _llm_cfg_from_ctx(ctx)
    unit_price = getattr(getattr(llm_cfg, "usage", None), "unit_price", None) or {}
    prices = _price_table(unit_price)
    cost: Optional[float] = None
    if model in prices:                            # 缺单价表不炸记账(仅报表,偏离 4)
        cost = estimate_cost(model, usage, prices)
    payload = {
        "model": model,                            # 实际服务模型(降级后记备用,不记主名)
        "in_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "out_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "cache_hit": int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0),
        "cost_est": cost,
        "estimated": bool(getattr(usage, 'estimated', False)),
        "input_unknown": bool(getattr(usage, 'input_unknown', False)),
    }
    ev = await ctx.session.append("llm.usage", payload, actor="llm")
    cnt = counters if counters is not None else getattr(ctx, "counters", None)
    if cnt is not None:
        cnt.task_add(ev.payload)                   # 预算硬闸读 token(F032)
    return ev
