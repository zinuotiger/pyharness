"""Compaction projection: complete migrated implementation."""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Union
from pyharness.events import Envelope


log = logging.getLogger("pyharness.compaction")

DEFAULT_KEEP_RECENT_ROUNDS: int = 12     # 保留最近 12 轮原文

DEFAULT_WINDOW_RATIO: float = 0.75       # 触发①:派生 ≥75% 窗口

DEFAULT_MIN_NEW_ROUNDS: int = 10         # 触发②:距上次压缩新增 ≥10 轮

DEFAULT_SUMMARY_BUDGET: int = 400        # 每段摘要 token 预算

DEFAULT_WINDOW_TOKENS: int = 65536       # 窗口兜底(scope.window_tokens 数据源优先)

_DEGRADE_KEEP: frozenset = frozenset({
    "user.message", "agent.message", "session.renamed",
    "plan.proposed", "plan.approved", "plan.rejected",
    "plan.done", "plan.aborted", "goal.updated", "goal.completed",
    "todo.updated", "context.compacted",
})

_DEGRADE_MAX_CHARS: int = 600           # 降级摘要截断保底(spec)

_ASCII_WORD = re.compile(r"[A-Za-z0-9_./\\@:+-]+")

_CJK_RANGE = range(0x4E00, 0x9FFF + 1)

_MSG_OVERHEAD = 4                        # role/content 结构开销

@dataclass(frozen=True)
class FoldCandidate:
    """折叠候选:一个完整任务段(整段不切半)。"""

    task_id: str
    range: tuple[int, int]          # 段 [start_seq, end_seq] 闭区间
    start_ts: str = ""
    n_events: int = 0

@dataclass(frozen=True)
class SegmentSummary:
    """单段摘要产物(range + 摘要文本 + 降级标记 + token 数)。"""

    range: tuple[int, int]
    summary: str                    # ≤~800 字(spec;摘要预算 400 token 约束)
    degraded: bool = False          # True = 规则降级产物(非 LLM 摘要)
    tokens: int = 0

@dataclass(frozen=True)
class CompactReport:
    """压缩报告:fold 明细 + 前后 token + 保留轮数 + 触发原因。"""

    folds: list[SegmentSummary] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    kept_recent_rounds: int = DEFAULT_KEEP_RECENT_ROUNDS
    reason: str = "window:0 degraded"
    noop: bool = False

@dataclass(frozen=True)
class PrefixPlan:
    """KV 缓存前缀规划:稳定前缀终点 = 最新 compacted 之后(其后只有尾部追加)。"""

    stable_upto_seq: int = 0        # 稳定前缀终点 seq(0 = 无可复用锚)
    prefix_tokens: int = 0
    cacheable: bool = False
    blocks: list = field(default_factory=list)   # 历次折叠块 [[lo, hi], ...]

def _count_text_tokens(text: str) -> int:
    """纯文本 token 粗估(确定性;与 session._estimate_tokens 同口径):
    中文逐字 1 token,西文词按 4 字符 ≈ 1 token。仅供相对比较与声明留痕。"""
    cjk = sum(1 for ch in text if ord(ch) in _CJK_RANGE)
    words = sum(max(1, (len(w) + 3) // 4) for w in _ASCII_WORD.findall(text))
    return cjk + words

def estimate_tokens(value: Union[str, dict, list, tuple, None]) -> int:
    """token 估算入口:str = 文本;dict = 消息(取 content,含结构开销);
    list/tuple = 逐元素累加(消息列表每条 +_MSG_OVERHEAD)。纯确定性,无 IO。"""
    if value is None:
        return 0
    if isinstance(value, str):
        return _count_text_tokens(value) + _MSG_OVERHEAD
    if isinstance(value, dict):
        content = value.get("content", "") if value.get("role") else value
        if isinstance(content, (list, tuple)):
            import json
            content = json.dumps(list(content), ensure_ascii=False)
        return _count_text_tokens(str(content)) + _MSG_OVERHEAD
    return sum(estimate_tokens(item) for item in value)

def _payload_text(env: Envelope) -> str:
    """事件载荷 → 摘要/降级可用的正文文本(取 content/标题/描述等决策字段)。"""
    p = env.payload
    return (p.get("content") or p.get("new_title") or p.get("desc")
            or p.get("summary") or "")

def _projection(events: list[Envelope]) -> list[dict]:
    """区间事件 → 派生消息投影(会计/前缀规划用;语义 = session reducer 对区间的
    映射,EVENT-SCHEMA §4.2:user→user、llm.response(有 content)→assistant、
    空 content response + tool.result → tool 配对、context.compacted → system
    摘要消息)。纯函数,不建历史,只用于 token 差值口径与前缀估算。

    偏离:user.message_edited 的跨区间改写(target 可能在本区间外)不追,会计
    近似(声明留痕用途,误差仅影响 tokens_after 数值精度,不影响任何决策)。"""
    msgs: list[dict] = []
    pending: Optional[int] = None       # 待配对 tool 的空 content response seq
    for ev in events:
        t = ev.type
        if t == "user.message":
            msgs.append({"role": "user", "content": ev.payload["content"]})
        elif t == "context.compacted":
            msgs.append({"role": "system",
                         "content": f"[已压缩 {ev.payload['ranges']}] "
                                    f"{ev.payload.get('summary', '')}"})
        elif t == "llm.response":
            c = ev.payload.get("content") or ""
            if c:
                msgs.append({"role": "assistant", "content": c})
            else:
                pending = ev.seq
        elif t == "tool.result" and pending is not None:
            msgs.append({"role": "tool", "content": ev.payload["summary"],
                         "name": ev.payload["name"]})
            pending = None
        # 其余事件(guard.*/approval.*/plan.*/session.*/tool.call…)不进 LLM 投影
    return msgs

def render_segment(events: list[Envelope], max_chars: int = 6000) -> str:
    """任务段 → 可摘要文本(决策轮文本化;tool 结果截断,安全审计细节不入文本)。

    SECURITY.md §3.1:guard.*/approval.* 细节一律不进入摘要输入——模型摘要
    不可能编造其未见内容(结构上保证 guard 拒绝记录不进摘要)。"""
    lines: list[str] = []
    for ev in events:
        t = ev.type
        if t in ("guard.rejected", "guard.evaluated", "approval.requested",
                 "approval.granted", "approval.denied", "approval.timeout",
                 "tool.call"):
            continue                    # 安全审计/审批细节不进摘要(§3.1)
        if t == "user.message":
            lines.append(f"用户: {ev.payload['content']}")
        elif t == "agent.message":
            lines.append(f"助手: {ev.payload['content']}")
        elif t == "llm.response":
            c = ev.payload.get("content") or ""
            if c:
                lines.append(f"模型: {c}")
        elif t == "tool.result":
            summ = (ev.payload.get("summary") or "")[:200]   # 工具原始结果截断
            lines.append(f"工具[{ev.payload.get('name', '?')}]: {summ}")
        elif t == "plan.proposed":
            lines.append(f"计划目标: {ev.payload.get('goal', '')}")
        elif t == "goal.updated":
            gid = ev.payload.get("goal_id", "")
            st = ev.payload.get("status", "")
            lines.append(f"目标[{gid}]: {st} "
                         f"{ev.payload.get('note') or ev.payload.get('desc') or ''}")
        elif t == "todo.updated":
            todos = ev.payload.get("todos") or []
            # payload 已 model_dump 归一:todo 项为 dict(兼容对象形态)
            done_n = sum(1 for it in todos
                         if (it.get("done") if isinstance(it, dict)
                             else getattr(it, "done", False)))
            lines.append(f"待办更新: {done_n}/{len(todos)} 项完成")
        elif t == "context.compacted":
            lines.append(f"(此前折叠: {ev.payload['ranges']})")
    text = "\n".join(x for x in lines if x).strip()
    return text[:max_chars]

def render_compacted_message(env: Envelope) -> str:
    """compacted 声明的人类可读渲染(EVENT-SCHEMA §4.2 摘要代区间锚文本)。

    说明:会话 reducer 对 context.compacted 的实际插桩格式由 session.
    _fold_history 决定(`[已压缩 {ranges}] {summary}`,ranges 为 Python 列表字面
    量);本函数按 compaction 规格提供 [lo-hi] 紧凑形态,供压缩报告与
    kv_prefix_plan 前缀内容估算使用(与 reducer 无耦合,纯只读工具)。"""
    p = env.payload
    rng = ",".join(f"[{lo}-{hi}]" for lo, hi in p.get("ranges", []))
    return f"[已压缩 {rng}] {p.get('summary', '')}"

# Preserve historical import and pickle identities through the compatibility facade.
FoldCandidate.__module__ = 'pyharness.core.compaction'
SegmentSummary.__module__ = 'pyharness.core.compaction'
CompactReport.__module__ = 'pyharness.core.compaction'
PrefixPlan.__module__ = 'pyharness.core.compaction'
