"""pyharness/events — 事件模型 + 事件词表 (specs/events.py.md)

会话事件唯一的 pydantic 模型层:Envelope 信封强校验 + 74 事件负载词表注册
+ 五步校验链(任一失败拒写,不进内存/总线/日志);由 core/session.append
(F009)作为唯一写入口闸门调用。

模块布局(spec 规定):
    envelope.py — Envelope 信封模型 + validate/make 五步链 + SeqState + check_seq_gap
    vocab.py    — 词表注册(register_event_type/payload_model_for/validate_payload)
    payload.py  — 74 事件 / 72 payload 模型各自负载模型(按 EVENT-SCHEMA §3 字段级权威)
"""
from pyharness.events.envelope import (DECLARE_TYPES, Envelope, SeqState,
                                       call_id_of,
                                       check_seq_gap, declared_ranges,
                                       make_envelope, validate_envelope)
from pyharness.events.payload import (  # noqa: F401 — 负载模型面向上层/测试再导出
    PlanStep,
    TodoItem,
)
from pyharness.events.vocab import (ACTORS, EVENT_TYPES, SYNC_TYPES,
                                    TRANSIENT_TYPES, is_registered,
                                    is_transient, payload_model_for,
                                    register_event_type, registered_event_types,
                                    validate_payload)

__all__ = [
    # 信封与校验链
    "Envelope", "SeqState", "make_envelope", "validate_envelope", "check_seq_gap",
    "DECLARE_TYPES", "declared_ranges", "call_id_of",
    # 词表与注册
    "EVENT_TYPES", "register_event_type", "payload_model_for", "is_registered",
    "is_transient", "registered_event_types", "validate_payload",
    # 常量
    "ACTORS", "SYNC_TYPES", "TRANSIENT_TYPES",
]
