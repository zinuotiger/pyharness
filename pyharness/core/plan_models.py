"""Plan models: complete migrated implementation."""
from __future__ import annotations
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional


log = logging.getLogger("pyharness.plan_mode")

MAX_STEPS = 8                 # 方案步骤上限(超限拒写不落盘)

PLAN_TTL_HOURS = 24           # 方案 24h 未确认作废(过期自动 rejected)

MAX_RETRY = 2                 # 单步失败重试上限(仍败默认中止)

PLAN_PROMPT = (
    "你是任务拆解器。把用户目标拆成不超过 max_steps 步的可执行方案,每步必须包含:"
    "action(动作,动词短语)、tool(工具名,选可用工具)、expected(预期结果,可核对)。"
    "只输出 JSON,不要任何解释。步骤须按执行顺序排列,依赖前置。"
)

DANGER_TABLE: tuple[dict[str, str], ...] = (
    {"pattern": "fs.delete_file", "level": "critical"},
    {"pattern": "exec.*", "level": "high"},
    {"pattern": "net.*", "level": "high"},
)

PlanStatus = Literal[
    "draft", "pending", "approved", "executing",
    "completed", "rejected", "aborted",
]

_ACTIVE_STATUSES = ("pending", "approved", "executing")

RiskLevel = Literal["none", "low", "high", "critical"]

def _utcnow() -> datetime:
    """当前 UTC aware 时间(会话事件 ts 同口径;避免 naive/aware 比较 TypeError)。"""
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    """datetime → ISO8601 UTC 串(Z 结尾,与 Envelope.ts 同形)。"""
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z")

def _parse_dt(s: Any) -> Optional[datetime]:
    """ISO8601 串(含 Z 结尾)→ aware datetime;空/非法返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None

@dataclass
class Step:
    """方案单步:action/tool/expected/risk(intent = 该步执行意图)。

    risk 由 F023 同源表打标:high → 执行时独立审批("审批点"),critical → 本层不可
    执行(selfcheck 软标,展示用);intent 缺省由 action/tool/expected 拼装(事件
    载荷不带 intent,回放重建时兜底走此缺省,Step(**s) 兼容事件派生步骤)。
    """

    action: str
    tool: str
    expected: str
    risk: RiskLevel = "low"
    intent: str = ""

    def __post_init__(self) -> None:
        """intent 缺省拼装:用 <tool> <action>: <expected>(F045 执行意图)。"""
        if not self.intent:
            self.intent = f"用 {self.tool} {self.action}: {self.expected}"

    def to_dict(self) -> dict:
        """值拷贝(plan_revise 单步编辑用;模型转 dict 零共享)。"""
        return asdict(self)

@dataclass
class Plan:
    """会话级方案(id = plan.proposed 事件 seq;状态只由事件回放重建,INV-01)。

    who = 裁决人(approve/reject 后置位;24h 过期自动 rejected 时 who=system)。
    """

    id: int
    goal: str
    steps: list[Step]                    # ≤8 步(propose 强校验)
    selfcheck_ok: bool = True            # 可执行性/依赖自检结论(展示用不阻断)
    why: str = ""                        # selfcheck 未过原因(空 = 通过)
    status: PlanStatus = "pending"       # draft 仅在 proposed 落盘前在途
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None  # created + 24h;过期自动作废
    who: Optional[str] = None

    def summary(self) -> str:
        """用户可读一行摘要(UI/命令层展示待批方案用)。"""
        steps_txt = " → ".join(f"{i}.{s.action}({s.tool})" for i, s in
                               enumerate(self.steps))
        check = "自检通过" if self.selfcheck_ok else f"自检未过:{self.why}"
        return (f"[plan {self.id}] {self.goal} | {self.status} | {check}\n"
                f"  步骤({len(self.steps)}):{steps_txt}")

# Preserve historical import and pickle identities through the compatibility facade.
Step.__module__ = 'pyharness.core.plan_mode'
Plan.__module__ = 'pyharness.core.plan_mode'
