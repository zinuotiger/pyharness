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


# ============================================ 模型面文本(O-2:回喂可行动性)
def test_model_message_prefers_call_site_advice():
    """**调用点 advice 优先于错误码表的"处置"话术**(O-2)。

    背景:``spec.advice`` 是**运维处置**列(如 "tool.error 回喂 LLM 自查,不静默"),
    对模型无可行动性;而调用点写好的现场指引此前被整条丢弃。
    """
    try:
        raise_code("TLB-802", path="no-such-dir",
                   advice="目录不存在或路径不是目录,先 list_dir 父目录侦查")
    except PyHError as e:
        m = e.to_model_message()
        assert "目录不存在或路径不是目录" in m, m
        assert "回喂 LLM 自查" not in m, f"不得回喂运维话术:{m}"


def test_model_message_falls_back_to_call_site_hint():
    """无 advice 时用调用点 hint 作建议(仍优先于错误码表的处置话术)。"""
    try:
        raise_code("TLB-802", tool="nope", hint="查工具名拼写与注册表")
    except PyHError as e:
        m = e.to_model_message()
        assert "查工具名拼写与注册表" in m, m
        assert "回喂 LLM 自查" not in m, m


def test_model_message_fallback_and_no_duplicate_detail():
    """无任何调用点指引时回落错误码表;且已作建议的文本不再重复进"明细"。"""
    try:
        raise_code("TLB-802", tool="nope")
    except PyHError as e:
        m = e.to_model_message()
        assert "工具未注册" in m and "建议:" in m, m

    try:
        raise_code("TLB-803", hint="字段 a 应为 int")
    except PyHError as e:
        m = e.to_model_message()
        assert m.count("字段 a 应为 int") == 1, f"建议/明细重复:{m}"
