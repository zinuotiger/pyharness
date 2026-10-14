"""Narrow dotted configuration access, shared by query and spill."""
from typing import Any

def _cfg_holder(ctx: Any) -> Any:
    """取配置持有者:spec 伪码经 ctx.cfg;本项目装配(Ctx)为 ctx.config(双形态都收)。"""
    if ctx is None:
        return None
    for name in ("cfg", "config"):
        holder = getattr(ctx, name, None)
        if holder is not None:
            return holder
    return None


def _cfg(holder: Any, dotted: str, default: Any = None) -> Any:
    """点分键取值,双形态兼容:dict(点分整键或逐层嵌套)与属性链(pydantic Settings)。"""
    if holder is None:
        return default
    parts = dotted.split(".")
    if isinstance(holder, dict):
        if dotted in holder:
            return holder[dotted]
        cur: Any = holder
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur
    cur = holder
    for p in parts:
        cur = getattr(cur, p, None)
        if cur is None:
            return default
    return cur
