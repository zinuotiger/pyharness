"""INV 骨架 — events:事件词表含强同步三类、Envelope 必带 seq/ts
当前 RED:events.py 未实现。
"""
import pytest
pytestmark = pytest.mark.invariant

def test_strong_sync_event_types_present():
    """强同步三类事件族在词表内(approval 实际为 granted/denied/timeout)"""
    from pyharness.events import EVENT_TYPES  # noqa: RED 目标
    for t in ["guard.rejected", "approval.granted", "approval.denied", "approval.timeout"]:
        assert t in EVENT_TYPES, f"强同步事件 {t} 缺失"

def test_envelope_has_seq_and_ts():
    """Envelope 必带 seq/ts/type/session_id/actor 五要素"""
    from pyharness.events.envelope import Envelope  # noqa: RED 目标
    env = Envelope(seq=1, ts="2026-09-06T00:00:00.000000Z",
                   type="user.message", session_id="test-session-0001",
                   actor="user", payload={})
    assert env.seq == 1 and env.type == "user.message"
    assert env.session_id == "test-session-0001"
