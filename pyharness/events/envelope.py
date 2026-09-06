"""pyharness/events/envelope.py — 事件信封模型与五步校验链 (specs/events.py.md)

Envelope 十字段强校验(model_validate 失败 → EVT-100)+ validate_envelope/
make_envelope(五步校验链唯一实现)+ SeqState(per-session seq 分配器)+
check_seq_gap(回放空洞自检,供 F031 warn_hole)。

纪律:seq/ts 只由框架统一分配(make_envelope),LLM/工具/插件无权自报;
任何校验失败拒写(不进内存/总线/日志);抛错一律 raise_code(EVT-1xx)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from pyharness.errors import raise_code
from pyharness.events.vocab import (ACTORS, SYNC_TYPES, TRANSIENT_TYPES,
                                    is_registered, summarize_validation,
                                    validate_payload)

# ts 正则(ISO8601 UTC,微秒,末尾 Z)——EVENT-SCHEMA §2.1
_TS_PATTERN = r"^\d{4}-\d{2}-\d{2}T.*Z$"


def _utc_now_ts() -> str:
    """框架统一打点:UTC 微秒 ISO8601,以 Z 结尾(禁外部自报)。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z")


class Envelope(BaseModel):
    """事件信封:十字段强校验(PRD §3.2 / EVENT-SCHEMA §2.1)。

    冻结信封(extra="forbid" + frozen=True):拒多余字段、禁事后改字段;
    payload 按 type 二次强校验在校验链第 3 步完成。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=1)                    # 会话内单调 +1,框架分配
    ts: str = Field(pattern=_TS_PATTERN)      # ISO8601 UTC 微秒,框架打
    type: str                                 # 词表枚举;未注册 → EVT-102
    session_id: str = Field(min_length=8)     # 形如 s-abc12345;fork 新 id
    actor: Literal["user", "agent", "llm", "tool", "system", "plugin"]
    origin: Optional[str] = None              # 插件/能力 id(如 plugin:echo)
    task_id: Optional[str] = None             # 所属任务段;无任务上下文为空
    payload: dict = Field(default_factory=dict)
    trace: Optional[dict] = None              # 语义父关联,建议 {"parent_seq": int}

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: str) -> str:
        """框架统一打(禁外部自报):ts 必须 UTC 且以 Z 结尾。"""
        if not v.endswith("Z"):
            raise ValueError("ts 必须 UTC 且以 Z 结尾")
        return v


def validate_envelope(raw: dict, seq_state: "SeqState",
                      session_open: bool = True) -> Envelope:
    """五步校验链唯一实现,顺序不可交换;任一失败拒写(EVENT-SCHEMA §2.2)。

    1 信封字段(类型/枚举/正则)→ EVT-100;2 类型注册 → EVT-102;
    3 payload 强校验 → EVT-100;4 seq 连续性 → EVT-101;
    5 会话状态:未 created 即写(且非 session.created)→ EVT-106。
    (终态写违规 EVT-104 由 session.append 状态机侧把关,INV-01)
    """
    try:
        env = Envelope.model_validate(raw)
    except ValidationError as e:
        raise_code("EVT-100", detail=summarize_validation(e))
    # 2 类型必须已注册(EVT-102)
    if not is_registered(env.type):
        raise_code("EVT-102", type_=env.type)
    # 3 payload 二次强校验 → 规范化(冻结信封:model_copy 换 payload,不原地改)
    canonical = validate_payload(env.type, env.payload)
    env = env.model_copy(update={"payload": canonical})
    # 4 seq 必须 = _next_seq + 1(EVT-101)
    expected = seq_state.next_seq(env.session_id)
    if env.seq != expected:
        raise_code("EVT-101", session_id=env.session_id,
                   expected=expected, got=env.seq)
    # 5 会话状态:未 created 且本事件不是 opening 的 session.created → EVT-106
    if not session_open and env.type != "session.created":
        raise_code("EVT-106", type_=env.type, hint="首事件必须是 session.created(seq=1)")
    return env


def make_envelope(session_id: str, type_: str, actor: str, payload: dict, *,
                  origin: Optional[str] = None, task_id: Optional[str] = None,
                  trace: Optional[dict] = None,
                  seq_state: Optional["SeqState"] = None) -> Envelope:
    """框架打点构造器:seq/ts 由框架统一分配(§3.1-5),再走 validate_envelope 全链。

    调用方(会话层)在 append 成功落盘后调用 seq_state.commit(env) 记账;
    seq_state.open 指示会话"已 created 且未 finished"。
    """
    if seq_state is None:
        raise_code("EVT-100", detail="make_envelope 缺 seq_state:seq 只能由框架分配")
    raw: dict[str, Any] = {
        "seq": seq_state.next_seq(session_id),   # 框架唯一打点(防伪造乱序)
        "ts": _utc_now_ts(),
        "type": type_,
        "session_id": session_id,
        "actor": actor,
        "origin": origin,
        "task_id": task_id,
        "payload": payload,
        "trace": trace,
    }
    return validate_envelope(raw, seq_state,
                             session_open=session_id in seq_state.open)


class SeqState:
    """per-session seq 分配器与空洞口径(§3.4)。

    内存 _next 从日志 max 重建(rebuild);append 恒 max+1;compaction/fork/repair
    空洞只解释不回填。open = 已 created 且未 finished(由会话层经 rebuild 开闸)。
    """

    def __init__(self) -> None:
        self._next: dict[str, int] = {}     # session_id → 下个可用 seq
        self.open: set[str] = set()         # 已 created 且未 finished

    def rebuild(self, session_id: str, max_seq: int) -> None:
        """启动/repair 后从日志重建:max_seq 为已落盘最大 seq,并开闸会话。"""
        self._next[session_id] = max_seq + 1
        self.open.add(session_id)

    def next_seq(self, session_id: str) -> int:
        """期望的下一个 seq;无记录 → 1(session.created 首事件)。"""
        return self._next.get(session_id, 1)

    def commit(self, env: Envelope) -> None:
        """validate 通过并落盘成功后记账:_next = env.seq + 1。"""
        self._next[env.session_id] = env.seq + 1


def check_seq_gap(events: list[int], declared: list[tuple[int, int]]) -> list[int]:
    """回放空洞自检:无 compacted/recovered 声明覆盖的 seq 空洞列表(供 warn_hole)。

    declared = 合法空洞闭区间声明(如 context.compacted.ranges);返回未声明空洞,
    升序;不中断回放。
    """
    declared_holes: set[int] = set()
    for lo, hi in declared:
        declared_holes.update(range(lo, hi + 1))
    actual = set(events)
    expected = set(range(1, max(events) + 1)) if events else set()
    return sorted(expected - actual - declared_holes)


__all__ = [
    "ACTORS", "SYNC_TYPES", "TRANSIENT_TYPES", "Envelope",
    "validate_envelope", "make_envelope", "SeqState", "check_seq_gap",
]
