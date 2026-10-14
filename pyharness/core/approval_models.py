"""Approval models: complete migrated implementation."""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional
if TYPE_CHECKING:  # 仅类型标注:executor 传入的 ToolCall 鸭子契约,运行期不依赖
    pass


logger = logging.getLogger("pyharness.approval")

CHANNELS: tuple[str, ...] = ("cli", "web", "acp", "desktop")

VERDICTS: tuple[str, ...] = ("granted", "denied", "timeout")

TERMINAL_STATES: frozenset = frozenset(VERDICTS)

_OUTCOME_EVENTS: dict[str, str] = {
    "granted": "approval.granted",
    "denied": "approval.denied",
    "timeout": "approval.timeout",
}

_HUMAN_CHANNELS: tuple[str, ...] = ("cli", "web", "acp", "desktop")

DEFAULT_TRUST_MAX: int = 100

_TRUST_SET = "approval.trust_set"

_TRUST_ADDED = "approval.trust_added"

_TRUST_HIT = "approval.trust_hit"

_TRUST_CLEARED = "approval.trust_cleared"

def _danger_of(call: Any) -> str:
    """读工具契约 danger(只有 high 能进审批;critical 已在 guard 层转 reject)。"""
    d = getattr(getattr(call, "defn", None), "danger", None)
    return d if d in ("high", "critical") else "high"

def _norm_value(v: Any) -> Any:
    """canonical_json 值归一(键排序/类型归一:bool→int、整值 float→int、嵌套递归)。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, dict):
        return {str(k): _norm_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm_value(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v

def canonical_json(args: Any) -> str:
    """参数规范化序列化(键排序、值类型归一;合并与信任共用的指纹原料)。"""
    return json.dumps(_norm_value(args), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)

@dataclass
class ApprovalRequest:
    """等待者记录(规格数据结构表)。

    approval_id = 请求事件 seq(防重放唯一键);合并等待者(60s 批内后到者)无事件,
    approval_id=None,挂到 lead 的批等同一裁决(batch 级联)。
    """

    tool: str = ""
    call_id: str = ""
    args_summary: str = ""
    danger: str = "high"
    ttl_ms: int = 120_000
    channel: Optional[str] = None          # cli/web/acp
    by: Optional[str] = None               # 裁决人(裁决时由框架打)
    state: str = "pending"                 # pending/granted/denied/timeout 一次性迁移
    approval_id: Optional[int] = None      # = 请求事件 seq(lead 才有)
    fingerprint: str = ""                  # 规范化指纹(合并批 + 信任共键)
    binding: str = ""                      # 执行侧绑定指纹(executor 传入;授权↔执行一致性)
    session_id: str = ""                   # 请求所属会话(事件落点路由)
    log: Any = None                        # 会话日志(事件落点;经 ctx 注入)
    waiter: Any = None                     # asyncio Future(裁决/超时/取消先到者解决)
    timer: Any = None                      # TTL asyncio.TimerHandle(仅 lead)
    batch: list = field(default_factory=list)  # 所属 60s 批(含自身;lead 恒 batch[0])
    batch_mono: float = 0.0                # 批创建单调时钟(60s 窗口判定)
    decision_inflight: Optional[str] = None  # 单次决定的预留，早于任何 await
    completion: Any = None                 # provider 拥有的完成任务，API/关闭共同等待

    @property
    def is_terminal(self) -> bool:
        """终态判定(终态后不再接受裁决,一次性消费)。"""
        return self.state in TERMINAL_STATES

@dataclass
class TrustEntry:
    """会话级信任记录(指纹 → 条目;FIFO 淘汰;不随 fork 继承)。"""

    tool: str = ""
    fingerprint: str = ""
    by: str = ""                           # 首次 granted 的裁决人
    added_seq: int = 0                     # 记录时 lead 的 approval_id(FIFO 序)
    hits: int = 0                          # 命中计数(审计)
    session_id: str = ""

# Preserve historical import and pickle identities through the compatibility facade.
ApprovalRequest.__module__ = 'pyharness.core.approval'
TrustEntry.__module__ = 'pyharness.core.approval'
