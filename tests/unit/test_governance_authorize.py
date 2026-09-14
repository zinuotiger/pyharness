"""tests/unit/test_governance_authorize.py — S3-2-1 专项测试。

覆盖:S3-2-1 设计(B1/B4)与 R1 所有权裁定。
只测试新增/改造的接缝;**不修改任何旧代码行为**:
旧 ``GuardChain.evaluate()`` 的签名/返回类型/求值语义由等价性测试钉死。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import types

import pytest

from pyharness.core import tools_guard
from pyharness.governance import Decision, DecisionEngine, Principal, Verdict
from pyharness.governance.context import GovernanceContext
from pyharness.governance.decision import EvaluationResult, PrincipalKind
from pyharness.governance.policy import Policy, PolicyEngine, PolicyRegistry

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GUARD_SRC = _ROOT / "pyharness" / "core" / "tools_guard.py"
_CTX_SRC = _ROOT / "pyharness" / "governance" / "context.py"


# ---------------------------------------------------------------- 替身
class _Sess:
    """session.append 同型替身(记录事件;无 I/O)。"""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def append(self, type_: str, payload: dict, *, actor: str = "",
                     sync: bool = False, trace=None):
        self.events.append((type_, payload, sync))


class _Scope:
    """Scope 替身:只需 can_use(guard 前置契约)。"""

    def __init__(self, allow: bool = True) -> None:
        self._allow = allow

    def can_use(self, name: str) -> bool:
        return self._allow


class _Defn:
    def __init__(self, danger: str) -> None:
        self.danger = danger


class _RejectGuard(tools_guard.Guard):
    """确定性拒绝 guard(精确控制 guard_id / policy_ref)。"""

    def __init__(self, gid: str, policy_ref: str, match: bool = True) -> None:
        self.id = gid
        self._ref = policy_ref
        self._match = match

    def match(self, call) -> bool:
        return self._match

    async def check(self, call, scope):
        return ("reject", self._ref)


def _call(name: str, danger: str = "none", call_id: str = "c1"):
    return tools_guard.ToolCall(name=name, raw_args={}, call_id=call_id,
                                defn=_Defn(danger))


def _chain(guards=None, *, session=None, allow_scope=True) -> tools_guard.GuardChain:
    if guards is None:
        return tools_guard.GuardChain(session=session)
    return tools_guard.GuardChain(chain=list(guards), session=session)


# ================================================================ B1 等价性
_B1_CASES = [
    ("custom.tool", "none", True),      # 全链 allow
    ("custom.tool", "critical", True),  # g-danger 直拒
    ("custom.tool", "none", False),     # scope 前置终局拒
]


@pytest.mark.parametrize("name,danger,allow_scope", _B1_CASES)
async def test_t1_evaluate_and_detailed_agree(name, danger, allow_scope):
    """两个 API 共享同一求值实现 → verdict 逐例一致。"""
    sess = _Sess()
    ch = _chain(session=sess)
    call = _call(name, danger)
    scope = _Scope(allow_scope)
    legacy = await ch.evaluate(call, scope)
    detailed = await ch.evaluate_detailed(call, scope)
    assert str(legacy) == detailed.verdict
    assert isinstance(legacy, tools_guard.Decision)     # 旧返回类型不变


async def test_t2_detailed_refs_come_from_same_evaluation():
    """refs 与 verdict 来自同一次求值:scope 前置 → 精确 refs。"""
    sess = _Sess()
    ch = _chain(session=sess)
    d = await ch.evaluate_detailed(_call("custom.tool"), _Scope(False))
    assert d.verdict == "reject"
    assert d.guard_ids == ("scope-hidden",)
    assert d.policy_refs == ("GRD-401",)
    assert [e[0] for e in sess.events] == ["guard.evaluated", "guard.rejected"]
    assert sess.events[1][2] is True                    # guard.rejected 强同步


async def test_t3_detailed_refs_for_real_rule_hit():
    """真实 guard 命中 → guard_ids/policy_refs 对应命中面(非空、真实)。"""
    sess = _Sess()
    ch = _chain(session=sess)
    d = await ch.evaluate_detailed(_call("custom.tool", "critical"), _Scope())
    assert d.verdict == "reject"
    assert d.guard_ids == ("g-danger",)
    assert d.policy_refs == ("POL-DGR-1",)


async def test_t4_detailed_allow_lists_enabled_guards():
    sess = _Sess()
    ch = _chain(session=sess)
    d = await ch.evaluate_detailed(_call("custom.tool"), _Scope())
    assert d.verdict == "allow"
    assert d.guard_ids == tuple(ch.enabled_guard_ids())
    assert d.policy_refs == ()


async def test_t5_custom_chain_exact_refs():
    sess = _Sess()
    g = _RejectGuard("g-test", "POL-TEST-1")
    ch = _chain([g], session=sess)
    d = await ch.evaluate_detailed(_call("custom.tool"), _Scope())
    assert (d.verdict, d.guard_ids, d.policy_refs) == (
        "reject", ("g-test",), ("POL-TEST-1",))


# ================================================================ B1 结构守卫
def test_t6_single_waterfall_implementation():
    """`_evaluate_full` 是唯一 waterfall:`evaluate` / `evaluate_detailed`
    体内必须只有对它的调用,不得各带一份循环/审计。"""
    tree = ast.parse(_GUARD_SRC.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "GuardChain")
    fns = {n.name: n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)}
    assert "_evaluate_full" in fns
    for wrapper in ("evaluate", "evaluate_detailed"):
        body = fns[wrapper]
        assert not any(isinstance(n, (ast.For, ast.AsyncFor))
                       for n in ast.walk(body)), f"{wrapper} 不得含求值循环"
        assert not any(
            isinstance(n, ast.Attribute) and n.attr in ("_audit",
                                                        "_append_rejected")
            for n in ast.walk(body)), f"{wrapper} 不得自行审计"
        calls = {n.func.attr for n in ast.walk(body) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)}
        assert "_evaluate_full" in calls, f"{wrapper} 必须复用 _evaluate_full"


def test_t7_no_instance_cache_of_last_evaluation():
    """不得缓存"上一次求值"(实例属性)。"""
    tree = ast.parse(_GUARD_SRC.read_text(encoding="utf-8"))
    offenders = [n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)
                 and n.attr in ("_last_eval", "_last_evaluation",
                                "_last_result")]
    assert offenders == []


# ================================================================ B4 / R1
def _make_gc(chain, *, with_decisions=True) -> GovernanceContext:
    eng = PolicyEngine(policy=Policy("builtin:v1", "1.0"),
                       registry=PolicyRegistry(), chain=chain)
    dec = DecisionEngine(id_factory=lambda: "dec-fixed",
                         clock=lambda: "2026-09-14T00:00:00+00:00")
    return GovernanceContext(policy=eng, decisions=dec if with_decisions else None)


def _principal() -> Principal:
    return Principal(PrincipalKind.HUMAN, "alice", "cli")


async def test_t8_authorize_end_to_end_allow():
    sess = _Sess()
    gc = _make_gc(_chain(session=sess))
    ctx = types.SimpleNamespace(scope=_Scope())
    d = await gc.authorize(_call("custom.tool"), ctx, principal=_principal())
    assert isinstance(d, Decision)
    assert d.verdict is Verdict.ALLOW
    assert d.guard_ids == tuple(gc.policy.chain.enabled_guard_ids())
    assert d.policy_fingerprint == gc.policy.current().fingerprint
    assert d.supersedes is None
    assert d.principal == _principal()
    # ctx 无 session → 发射降级(不写事件);有 session 的一一对应见 T18
    assert all(t != "decision.issued" for t, _, _ in sess.events)


async def test_t9_authorize_reject_preserves_guard_semantics():
    """critical → reject 由 GuardChain 拥有;authorize 不重算。"""
    gc = _make_gc(_chain(session=_Sess()))
    ctx = types.SimpleNamespace(scope=_Scope())
    d = await gc.authorize(_call("custom.tool", "critical"), ctx,
                           principal=_principal())
    assert d.verdict is Verdict.REJECT
    assert d.guard_ids == ("g-danger",)
    assert d.policy_refs == ("POL-DGR-1",)


async def test_t10_authorize_fail_closed_when_unwired():
    ctx = types.SimpleNamespace(scope=_Scope())
    gc = _make_gc(_chain(session=_Sess()), with_decisions=False)
    with pytest.raises(Exception):
        await gc.authorize(_call("custom.tool"), ctx, principal=_principal())
    gc2 = _make_gc(_chain(session=_Sess()))
    gc2.policy._chain = None                            # 链未接线
    with pytest.raises(Exception):
        await gc2.authorize(_call("custom.tool"), ctx, principal=_principal())


def test_t11_authorize_does_not_pass_approval_params():
    """R1 守卫:authorize 调 decide() 时不得传 approval_available/approval_verdict。"""
    tree = ast.parse(_CTX_SRC.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "decide"]
    assert calls, "authorize 必须调用 decide()"
    for c in calls:
        names = {k.arg for k in c.keywords}
        assert "approval_available" not in names
        assert "approval_verdict" not in names


def test_t12_context_dependency_boundary():
    tree = ast.parse(_CTX_SRC.read_text(encoding="utf-8"))
    mods: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.append(n.module)
    for m in mods:
        assert not m.startswith(("pyharness.core", "pyharness.engine",
                                 "pyharness.bus", "pyharness.persistence")), m
        assert (m == "pyharness.errors" or m == "__future__" or "." not in m
                or m.startswith("pyharness.governance")), m


# ================================================================ supersedes
async def test_t13_supersedes_linkage():
    """prior=D001 → D002.supersedes == D001.decision_id;两者独立。"""
    seq = iter(["D001", "D002"])
    eng = PolicyEngine(policy=Policy("builtin:v1", "1.0"),
                       registry=PolicyRegistry(), chain=_chain(session=_Sess()))
    gc = GovernanceContext(policy=eng, decisions=DecisionEngine(
        id_factory=lambda: next(seq), clock=lambda: "2026-09-14T00:00:00+00:00"))
    ctx = types.SimpleNamespace(scope=_Scope())
    d1 = await gc.authorize(_call("custom.tool", "critical"), ctx,
                            principal=_principal())
    assert d1.supersedes is None and d1.decision_id == "D001"
    d2 = await gc.authorize(_call("custom.tool"), ctx, principal=_principal(),
                            prior=d1, approval_ref=7)
    assert d2.supersedes == d1.decision_id
    assert d2 != d1 and d2.decision_id != d1.decision_id
    assert d2.approval_ref == 7
    # 无 attempt counter
    assert not hasattr(d2, "attempt")


def test_t14_supersedes_default_is_none_and_hash_stable():
    a = Decision(decision_id="a", verdict=Verdict.ALLOW, tool="t")
    b = Decision(decision_id="a", verdict=Verdict.ALLOW, tool="t")
    assert a.supersedes is None and a == b and hash(a) == hash(b)
    c = Decision(decision_id="a", verdict=Verdict.ALLOW, tool="t",
                 supersedes="prev")
    assert c != a and c == c


# ================================================================ 并发
async def test_t15_concurrent_evaluations_do_not_cross_contaminate():
    """两个并发求值不得交叉污染(A 的 verdict + B 的 refs)。"""
    sess = _Sess()
    ch = _chain(session=sess)
    crit = _call("custom.tool", "critical", call_id="A")
    ok = _call("custom.tool", "none", call_id="B")
    # 交替多次,放大竞态窗口
    for _ in range(5):
        ra, rb = await asyncio.gather(
            ch.evaluate_detailed(crit, _Scope()),
            ch.evaluate_detailed(ok, _Scope()))
        assert (ra.verdict, ra.guard_ids) == ("reject", ("g-danger",))
        assert rb.verdict == "allow" and rb.guard_ids != ("g-danger",)
        assert rb.policy_refs == ()


async def test_t16_concurrent_authorize_isolated():
    gc = _make_gc(_chain(session=_Sess()))
    ctx = types.SimpleNamespace(scope=_Scope())
    results = await asyncio.gather(
        gc.authorize(_call("custom.tool", "critical", call_id="A"), ctx,
                     principal=_principal()),
        gc.authorize(_call("custom.tool", "none", call_id="B"), ctx,
                     principal=_principal()))
    assert results[0].verdict is Verdict.REJECT
    assert results[0].guard_ids == ("g-danger",)
    assert results[1].verdict is Verdict.ALLOW
    assert results[1].policy_refs == ()


# ================================================================ 契约稳定
def test_t17_evaluation_result_shape_unchanged():
    er = EvaluationResult(verdict="allow", guard_ids=("g",), policy_refs=("P",))
    assert (er.verdict, er.guard_ids, er.policy_refs) == ("allow", ("g",), ("P",))
    assert EvaluationResult("allow").guard_ids == ()


# ============================================ S3-2-2:decision.issued 发射
async def test_t18_authorize_emits_one_decision_issued():
    """一次 authorize → 恰一条 decision.issued(sync=True,call_id 走 trace)。"""
    sess = _Sess()
    gc = _make_gc(_chain(session=sess))
    ctx = types.SimpleNamespace(scope=_Scope(), session=sess)
    d = await gc.authorize(_call("custom.tool", call_id="cA"), ctx,
                           principal=_principal())
    evs = [e for e in sess.events if e[0] == "decision.issued"]
    assert len(evs) == 1
    _, payload, sync = evs[0]
    assert sync is True
    assert payload["decision_id"] == d.decision_id
    assert payload["verdict"] == "allow"
    assert payload["principal_kind"] == "human"       # 显式传入者
    assert payload["principal_id"] == "alice"
    assert payload["principal_channel"] == "cli"
    assert payload["supersedes"] is None
    # call_id 走 trace,不入 payload
    assert "call_id" not in payload


async def test_t19_authorize_emits_for_reject_too():
    """未传 principal → 临时 SYSTEM 身份模型;reject 同样恰一条事件。"""
    sess = _Sess()
    gc = _make_gc(_chain(session=sess))
    ctx = types.SimpleNamespace(scope=_Scope(allow=False), session=sess)
    d = await gc.authorize(_call("custom.tool"), ctx)
    assert d.verdict is Verdict.REJECT
    evs = [e for e in sess.events if e[0] == "decision.issued"]
    assert len(evs) == 1 and evs[0][1]["verdict"] == "reject"
    assert evs[0][1]["guard_ids"] == ["scope-hidden"]
    assert evs[0][1]["principal_kind"] == "system"    # 临时身份(非 human/agent)
    assert evs[0][1]["principal_id"] == "pyharness-runtime"
