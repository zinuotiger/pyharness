"""Canonical tool and child result text conversion."""
import json
from typing import Any

def render_result_text(raw: Any) -> str:
    """Provider 原始返回 → 统一文本(dict/list JSON 序列化;str 原样;None → 空)。"""
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
