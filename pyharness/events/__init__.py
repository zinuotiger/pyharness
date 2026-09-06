"""pyharness/events — 事件模型 + 事件词表 (specs/events.py.md)

会话事件唯一的 pydantic 模型层:Envelope 信封强校验 + 57 事件负载词表注册
+ 五步校验链(任一失败拒写,不进内存/总线/日志);由 core/session.append
(F009)作为唯一写入口闸门调用。

模块布局(spec 规定):
    envelope.py — Envelope 信封模型 + validate/make 五步链 + SeqState + check_seq_gap
    vocab.py    — 词表注册(register_event_type/payload_model_for/validate_payload)
    payload.py  — 57 事件各自 payload 负载模型(按 EVENT-SCHEMA §3 字段级权威)
"""
from pyharness.events.envelope import (Envelope, SeqState, check_seq_gap,
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
    # 词表与注册
    "EVENT_TYPES", "register_event_type", "payload_model_for", "is_registered",
    "is_transient", "registered_event_types", "validate_payload",
    # 常量
    "ACTORS", "SYNC_TYPES", "TRANSIENT_TYPES",
]
