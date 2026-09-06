"""INV 骨架 — config:env 前缀 PH_、敏感项只存引用(env:NAME)
当前 RED:config.py 未实现。
"""
import pytest
pytestmark = pytest.mark.invariant

def test_env_prefix_ph():
    """PH_ 前缀白名单:能加载默认配置,env 覆盖走白名单"""
    import os
    from pyharness.config import load_settings  # noqa: RED 目标
    os.environ["PH_LOOP_MAX_TURNS"] = "7"
    try:
        cfg = load_settings()
        assert cfg is not None
        # PH_ env 映射到 loop.max_turns(白名单内生效)
        assert cfg.loop.max_turns == 7
    finally:
        del os.environ["PH_LOOP_MAX_TURNS"]

def test_secret_stored_as_reference():
    """C4:敏感项只存 env:NAME 引用,不存字面量"""
    from pyharness.config import validate_rules  # noqa: RED 目标
    assert callable(validate_rules)
