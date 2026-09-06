"""errors.py 单测 — 契约:41 码对齐 ERR.md、域类按前缀抛、唯一出口 raise_code"""
import pytest
from pyharness import errors as E
from pyharness.errors import raise_code, PyHError

def test_default_codes_registered():
    """ERR.md §2 权威 41 码,注册表应 ≥41 且全部含六要素"""
    assert len(E.ERRORS) >= 41
    for code, spec in E.ERRORS.items():
        assert spec.code == code
        assert spec.name, f"{code} 缺 name"
        assert spec.retryable is not None, f"{code} 缺 retryable"
        assert spec.action, f"{code} 缺 action"
        assert spec.fatal is not None, f"{code} 缺 fatal"

def test_raise_code_known():
    """已知码抛出 PyHError 且 code/message 正确"""
    with pytest.raises(PyHError) as ei:
        raise_code("BUS-002", msg="测试")
    assert ei.value.code == "BUS-002"

def test_raise_code_unknown_fallback():
    """未登记码 → CYC-999 兜底,不崩"""
    with pytest.raises(PyHError) as ei:
        raise_code("NOT-A-REAL-CODE-999")
    assert ei.value.code == "CYC-999"

def test_domain_class_mapping():
    """域类按码前缀抛:LLM-xxx → LLMError,GRD-xxx → GuardError"""
    pairs = [("LLM-301", E.LLMError), ("GRD-401", E.GuardError),
             ("TLB-801", E.ToolError), ("APR-501", E.ApprovalError),
             ("PERS-201", E.PersistenceError), ("EVT-101", E.EventError),
             ("CFG-601", E.ConfigError), ("CRED-701", E.CredentialError),
             ("BUS-002", E.BusError), ("CYC-999", E.CycleError)]
    for code, cls in pairs:
        with pytest.raises(cls) as ei:
            raise_code(code)
        assert isinstance(ei.value, PyHError)

def test_to_user_message_no_leak():
    """用户消息不含内部堆栈痕迹"""
    try:
        raise_code("TLB-802", msg="x")
    except PyHError as e:
        m = e.to_user_message()
        assert "Traceback" not in m
        assert m, "用户消息非空"
