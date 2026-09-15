"""tests/unit/test_governance_decision.py — S3-1 治理决策模型等价性与纯度测试。

覆盖 S3-1 设计测试矩阵 T1~T15。**只测试新模型,不修改旧系统行为**:
旧 ``tools_guard.Decision`` 三值、``danger_default_policy``、``approval`` 通道
白名单均作为**只读真源**被引用比对(见 T1/T11/T14/T15)。

本文件不涉及任何运行时接线:``DecisionEngine`` 全程无 I/O、无事件、无执行。
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from pyharness.core import approval as approval_mod
from pyharness.core import tools_guard
from pyharness.governance import Decision, Principal, Verdict
from pyharness.governance.decision import (HUMAN_CHANNELS, ApprovalChannel,
                                           DecisionEngine, EvaluationResult,
                                           PrincipalKind)
from pyharness.governance.policy import Policy, PolicyRule

_MODULE_PATH = (pathlib.Path(__file__).resolve().parents[2]
                / "pyharness" / "governance" / "decision.py")

# 现网真实 by 取值(tests/ 与 pyharness/ 实测;非文档推测)
_REAL_BY = ("system", "cli", "web", "acp", "desktop",
            "cli:alice", "web:bob", "cli:test", "cli:claire")
# approval.py::_require_human 明令拒绝的自报身份
_ILLEGAL_BY = ("", "alice", "hacker", "mallory", "llm:", "tool:", "plugin:")


def _engine() -> DecisionEngine:
    """确定性引擎:固定 id / 固定时钟(Δ-2)。"""
    return DecisionEngine(id_factory=lambda: "dec-fixed",
                          clock=lambda: "2026-09-14T00:00:00+00:00")


def _principal() -> Principal:
    return Principal(PrincipalKind.HUMAN, "alice", "cli")


# ---------------------------------------------------------------- T1 verdict 值域
def test_t1_verdict_matches_legacy_decision_values():
    assert {v.value for v in Verdict} == set(tools_guard._DECISIONS)
    assert {str(v) for v in Verdict} == {str(d) for d in tools_guard.Decision}


# ---------------------------------------------------------------- T2 字符串兼容
def test_t2_verdict_and_decision_compare_to_str():
    assert Verdict.ALLOW == "allow"
    assert Verdict.REJECT == "reject"
    assert Verdict.APPROVAL == "approval"
    d = Decision(decision_id="x", verdict=Verdict.REJECT, tool="fs.delete_file")
    assert d == "reject" and "reject" == d
    assert d == Verdict.REJECT and d != "allow" and d != "approval"
    # M3 兼容面:tools_executor 现有 `d == "reject"` / `d != "allow"` 语义
    assert (d == "reject") is True and (d != "allow") is True


# ---------------------------------------------------------------- T3 legacy 往返
@pytest.mark.parametrize("by", _REAL_BY)
def test_t3_principal_legacy_round_trip(by):
    p = Principal.from_legacy_by(by)
    assert p.to_legacy_by() == by
    assert Principal.from_legacy_by(p.to_legacy_by()) == p


def test_t3_principal_kinds_from_real_prefixes():
    assert Principal.from_legacy_by("system").kind is PrincipalKind.SYSTEM
    assert Principal.from_legacy_by("cli").kind is PrincipalKind.HUMAN
    assert Principal.from_legacy_by("cli:alice").channel == "cli"
    assert Principal.from_legacy_by("llm:c1").kind is PrincipalKind.AGENT
    assert Principal.from_legacy_by("tool:web.fetch").kind is PrincipalKind.TOOL
    assert Principal.from_legacy_by("plugin:hello_time").kind is PrincipalKind.PLUGIN


# ---------------------------------------------------------------- T4 Principal 不变量
def test_t4_principal_invariant_and_illegal_by():
    with pytest.raises(Exception):
        Principal(PrincipalKind.HUMAN, "alice")            # 缺 channel
    with pytest.raises(Exception):
        Principal(PrincipalKind.HUMAN, "llm:evil", "cli")  # 非人类前缀
    with pytest.raises(Exception):
        Principal(PrincipalKind.HUMAN, "alice", "http")    # 通道不在白名单
    for bad in _ILLEGAL_BY:
        with pytest.raises(Exception):
            Principal.from_legacy_by(bad)


# ---------------------------------------------------------------- T5 frozen
def test_t5_decision_and_principal_frozen():
    p = _principal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.id = "bob"                                        # type: ignore[misc]
    d = Decision(decision_id="x", verdict=Verdict.ALLOW, tool="t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.verdict = Verdict.REJECT                          # type: ignore[misc]


# ---------------------------------------------------------------- T6 eq / hash
def test_t6_equality_and_hash_contract():
    kw = dict(decision_id="d1", verdict=Verdict.REJECT, tool="t",
              policy_refs=("POL-A",), guard_ids=("g3",),
              policy_fingerprint="fp", inputs_digest="id",
              principal=_principal(), ts="T", approval_ref=1, receipt_id=None)
    a, b = Decision(**kw), Decision(**kw)
    assert a == b and hash(a) == hash(b)
    for field in ("decision_id", "verdict", "tool", "policy_refs", "guard_ids",
                  "policy_fingerprint", "inputs_digest", "principal", "ts",
                  "approval_ref"):
        changed = dict(kw)
        if field == "verdict":
            changed[field] = Verdict.ALLOW
        elif field == "approval_ref":
            changed[field] = 2
        elif field == "principal":
            changed[field] = Principal(PrincipalKind.SYSTEM, "system")
        else:
            changed[field] = "other" if not isinstance(kw[field], tuple) else ("X",)
        assert a != Decision(**changed), field
    # 跨类型:eq/hash 契约成立(hash 取 verdict)
    assert a == "reject" and hash(a) == hash("reject")
    assert a != "allow"
    assert a.__eq__(object()) is NotImplemented


# ---------------------------------------------------------------- T7 str / terminal
def test_t7_str_and_is_terminal_reject():
    assert str(Decision(decision_id="x", verdict=Verdict.REJECT, tool="t")) == "reject"
    assert Decision(decision_id="x", verdict=Verdict.REJECT,
                    tool="t").is_terminal_reject is True
    assert Decision(decision_id="x", verdict=Verdict.APPROVAL,
                    tool="t").is_terminal_reject is False


# ---------------------------------------------------------------- T8 确定性
async def test_t8_deterministic_same_inputs_same_decision():
    eng = _engine()
    ev = EvaluationResult("reject", ("g3",), ("POL-FS-1",))
    args = dict(principal=_principal(), tool="fs.delete_file",
                inputs_digest="abc", policy=Policy("builtin:v1", "1.0"))
    d1 = await eng.decide(ev, **args)
    d2 = await eng.decide(ev, **args)
    assert d1 == d2
    assert d1.decision_id == "dec-fixed" and d1.ts == "2026-09-14T00:00:00+00:00"


# ---------------------------------------------------------------- T9 callable identity
async def test_t9_no_callable_identity_dependency():
    """同声明式内容、不同 check 实现 → 指纹同 → Decision 同(T2/Δ 防护)。"""
    async def c1(call, scope):  # noqa: ANN001
        return ("allow", None)

    async def c2(call, scope):  # noqa: ANN001
        return ("allow", None)

    def mk(check):
        return Policy("builtin:v1", "1.0",
                      rules=(PolicyRule("g3", ("POL-FS-1",), None, check),))
    p1, p2 = mk(c1), mk(c2)
    assert p1.fingerprint == p2.fingerprint
    eng = _engine()
    ev = EvaluationResult("allow", ("g3",), ("POL-FS-1",))
    d1 = await eng.decide(ev, principal=_principal(), tool="t", policy=p1)
    d2 = await eng.decide(ev, principal=_principal(), tool="t", policy=p2)
    assert d1 == d2


# ---------------------------------------------------------------- T10 决策语义
async def test_t10_governance_decision_semantics():
    eng = _engine()
    P = dict(principal=_principal(), tool="t")
    # critical → reject;批准不可放宽(单调性)
    crit = Decision(decision_id="p", verdict=Verdict.REJECT, tool="t")
    d = await eng.decide(EvaluationResult("reject"), approval_verdict="granted",
                         prior=crit, **P)
    assert d.verdict is Verdict.REJECT
    # high:无通道 → reject
    assert (await eng.decide(EvaluationResult("approval"),
                             approval_available=False, **P)).verdict is Verdict.REJECT
    # high:有通道 → approval
    assert (await eng.decide(EvaluationResult("approval"),
                             approval_available=True, **P)).verdict is Verdict.APPROVAL
    # denied / timeout → 作废
    for v in ("denied", "timeout"):
        assert (await eng.decide(EvaluationResult("approval"),
                                 approval_verdict=v, **P)).verdict is Verdict.REJECT
    # granted → 保持 approval(执行侧重入校验)
    assert (await eng.decide(EvaluationResult("approval"),
                             approval_verdict="granted", **P)).verdict is Verdict.APPROVAL
    # allow 直通
    assert (await eng.decide(EvaluationResult("allow"), **P)).verdict is Verdict.ALLOW
    # 非法 verdict → 拒
    with pytest.raises(Exception):
        await eng.decide(EvaluationResult("maybe"), **P)


# ---------------------------------------------------------------- T11 旧映射等价
async def test_t11_equivalent_to_danger_default_policy():
    """新治理层对 danger × has_channel 的裁决 == 旧 danger_default_policy。"""
    eng = _engine()
    for danger in ("none", "low", "high", "critical", "bogus"):
        for has_channel in (True, False):
            legacy = str(tools_guard.danger_default_policy(
                danger, has_channel=has_channel))
            got = await eng.decide(
                EvaluationResult(legacy), principal=_principal(), tool="t",
                approval_available=has_channel)
            assert str(got.verdict) == legacy, (danger, has_channel)


# ---------------------------------------------------------------- T12 无副作用
async def test_t12_no_runtime_side_effects():
    eng = _engine()
    # 结构面:引擎只持有注入的 id/clock,无任何 I/O 依赖
    assert set(vars(eng)) <= {"_id_factory", "_clock"}
    d = await eng.decide(EvaluationResult("reject"), principal=_principal(), tool="t")
    assert isinstance(d, Decision)
    # 不提供执行/凭证 API(INV-G5)
    for banned in ("authorize", "execute", "emit", "append", "write"):
        assert not hasattr(eng, banned)


# ---------------------------------------------------------------- T13 依赖边界
def test_t13_ast_dependency_boundary():
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    forbidden = ("pyharness.core", "pyharness.engine", "pyharness.bus",
                 "pyharness.persistence")
    for m in mods:
        assert not m.startswith(forbidden), m
        assert m == "pyharness.errors" or m == "__future__" or m.startswith(
            "pyharness.governance") or "." not in m, m
    # 代码面不得引用 core 侧符号(docstring 的散文提及不算——AST 只看节点)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in ("tools_guard", "tools_executor", "session",
                                   "persistence"), node.id
        elif isinstance(node, ast.Attribute):
            assert node.attr not in ("append", "emit", "write"), node.attr
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "authorize"
                   for n in ast.walk(tree))


# ---------------------------------------------------------------- T14 通道白名单一致
def test_t14_channel_whitelist_matches_approval():
    assert HUMAN_CHANNELS == approval_mod._HUMAN_CHANNELS
    assert HUMAN_CHANNELS == approval_mod.CHANNELS


# ---------------------------------------------------------------- T15 旧枚举零回归
def test_t15_legacy_decision_enum_unchanged():
    assert issubclass(tools_guard.Decision, str)
    assert tools_guard.Decision.REJECT == "reject"
    assert tools_guard.Decision.ALLOW == "allow"
    assert tools_guard.Decision.APPROVAL == "approval"
    assert tuple(str(d) for d in tools_guard.Decision) == (
        "allow", "reject", "approval")
    assert tools_guard._DECISIONS == ("allow", "reject", "approval")


# ---------------------------------------------------------------- 门面与协议
def test_facade_exports_only_s3_1_surface():
    """门面导出面随阶段演进(M4 起含凭证);Evidence/Audit 仍未实现即不导出。"""
    import pyharness.governance as g
    assert {"Decision", "Verdict", "Principal"} <= set(g.__all__)
    # S4/M4:凭证已接线并导出
    assert {"DecisionReceipt", "ReceiptStore"} <= set(g.__all__)
    # S5/M6(第一步):证据数据契约与归档入口已导出
    assert {"Evidence", "EvidenceRef", "EvidenceCollector"} <= set(g.__all__)
    # Audit 属 S5 后续,尚未实现 ⇒ 不导出
    for out_of_scope in ("AuditSystem", "TraceabilityMatrix"):
        assert out_of_scope not in set(g.__all__)
        assert not hasattr(g, out_of_scope)


def test_approval_channel_is_protocol_only():
    assert getattr(ApprovalChannel, "_is_protocol", False) is True
    assert hasattr(ApprovalChannel, "request")
    assert hasattr(ApprovalChannel, "grant_binding")
    assert hasattr(ApprovalChannel, "cancel_all")
