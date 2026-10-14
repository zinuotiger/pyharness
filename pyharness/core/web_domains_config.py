"""Web domains config: complete migrated implementation."""
from __future__ import annotations
from typing import Any
from pyharness.core.web_policy import log


def _cfg_holder(ctx: Any) -> Any:
    """配置持有者:ctx.cfg 优先,ctx.config 兼容(装配形态两收)。"""
    for name in ("cfg", "config"):
        holder = getattr(ctx, name, None)
        if holder is not None:
            return holder
    return None

def _cfg_read(ctx: Any, dotted: str, default: Any) -> Any:
    """点分键取值,双形态兼容(tool_fs/spill 同款):dict(平铺点分或嵌套)与
    pydantic 属性链;未接线/缺键回落默认(运行面不因未接线崩溃,阈值有界默认)。"""
    holder = _cfg_holder(ctx)
    if holder is None:
        log.debug("ctx.cfg/config 未接线,配置键 %s 回落默认 %r", dotted, default)
        return default
    if isinstance(holder, dict):
        if dotted in holder:                    # 平铺点分键形态
            return holder[dotted]
        cur: Any = holder
        for part in dotted.split("."):          # 嵌套 dict 形态
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
    cur = holder                                # pydantic/属性形态
    for part in dotted.split("."):
        nxt = getattr(cur, part, None)
        if nxt is None:
            return default
        cur = nxt
    return cur

def _allowed_domains(ctx: Any) -> list[str]:
    """scope.policy.allowed_domains 读取(默认空 = 禁一切外发,spec 伪码权威)。

    ctx.scope 可能直挂 ScopePolicy(裸 policy 形态)或挂 policy 属性(Scope 形态),
    两形态同收;scope 未接线 → [] 全禁(fail-closed,与 allowlist 默认一致)。
    """
    scope = getattr(ctx, "scope", None)
    if scope is None:
        return []
    pol = getattr(scope, "policy", None)
    if pol is None:
        pol = scope                            # 裸 policy 直挂 ctx.scope
    allowed = getattr(pol, "allowed_domains", None)
    if not allowed:
        return []
    return [str(d) for d in allowed]
