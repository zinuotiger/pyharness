"""INV 骨架 — EventBus:背压阈值 1000(F005)、未注册类型拒发(EVT-102)、强同步恒 sequential
当前 RED:bus.py 未实现。
"""
import pytest
pytestmark = pytest.mark.invariant

def test_bus_default_backpressure_1000():
    """F005 锁定:默认背压阈值=1000"""
    from pyharness.bus import EventBus  # noqa: RED 目标
    bus = EventBus()
    assert bus._backpressure_limit == 1000

def test_emit_unregistered_type_rejected():
    """EVT-102:未注册类型 emit → 抛错(先注册才能发)"""
    from pyharness.bus import EventBus
    bus = EventBus()
    with pytest.raises(Exception):
        bus.emit("ghost.event", {})

def test_register_type_then_emit_ok():
    """注册后 emit 不抛"""
    from pyharness.bus import EventBus
    bus = EventBus()
    bus.register_type("user.message", dict)
    bus.emit("user.message", {"text": "hi"})
