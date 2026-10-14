"""Engine events: complete migrated implementation."""
from __future__ import annotations
from typing import Any, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    pass

from pyharness.engine_types import log

def _sid_of(log_: Any) -> str:
    """会话 id 取值(`SessionLog.sid`;缺省 '?')。

    2026-09-21 修:此前三处订阅属主写 ``getattr(log_, 'session_id', '?')`` ——
    ``SessionLog`` 只有 ``.sid`` ⇒ 属主恒为 ``...:?``,**所有会话共用同一个属主串**
    (既是错误标识,又会让"按属主摘除"误伤其他会话)。同 PIT-16 的属性名错第二例。
    """
    return str(getattr(log_, "sid", "?") or "?")

def _tenant_of(log_: Any) -> str:
    """会话租户取值(订阅属主用;缺省 '' 表示默认/未声明)。

    2026-09-21 R12:属主串必须含**全部隔离维度**(租户 × 会话)—— 会话目录按租户
    分目录,同名 sid 跨租户并存合法;属主只含 sid 时,两租户同属主 ⇒ 关一个连坐另一个。
    """
    return str(getattr(log_, "tenant_id", "") or "")

def _EVENT_TYPES_OR_ALL() -> tuple:
    try:
        from pyharness.events.vocab import EVENT_TYPES as _ET
        return tuple(_ET)
    except Exception:                            # noqa: BLE001
        return ("session.created", "user.message", "agent.message",
                "agent.thinking", "tool.call", "tool.result", "guard.rejected",
                "guard.evaluated", "approval.requested", "approval.granted",
                "approval.denied", "approval.timeout", "llm.request",
                "llm.response", "llm.usage", "llm.chunk", "system.error",
                "session.finished", "session.recovered", "context.compacted",
                "segment.start", "segment.end", "fork.created")

_EVIDENCE_EVENT_TYPES: tuple[str, ...] = (
    "evidence.archived",                 # 证据工件(索引主来源)
    "segment.start", "segment.end",      # 段锚(把引用解析到 task)
    "decision.issued", "receipt.emitted",  # 锚 → seq(解析 decision_id/receipt_id)
)

