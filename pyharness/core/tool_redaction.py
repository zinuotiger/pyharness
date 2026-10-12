"""Shared tool output redaction hook."""
import logging
from typing import Any
log = logging.getLogger(__name__)

def _redact(ctx: Any, text: str) -> str:
    """出口脱敏(ctx.redact 注入面;未接线/失败 → 原样,读路径不因脱敏中断)。"""
    fn = getattr(ctx, "redact", None)
    if not callable(fn):
        return text
    try:
        return fn(text)
    except Exception as exc:                     # noqa: BLE001 脱敏故障不阻断读取
        log.warning("ctx.redact 执行失败,原样返回: %s", exc)
        return text
