"""governance/policy 单测(S2-1)— 契约:ADR-018 + S2-1_GOVERNANCE_SKELETON_DESIGN.md

覆盖面:
    T1 指纹稳定性:同配置两次装配指纹相同;任一**声明式**字段变则指纹变;
    T2 指纹排除函数对象(match/check 换实现 → 指纹不变);R-A 的直接防护;
    T3 依赖方向:governance/** 只 import events/errors/标准库/本包(ADR-018:308);
    T4 治理动作:禁用必须带 config_ref;forced 规则不可禁;幂等;
    T5 注册表:同 rule_id 重复注册 → TLB-801;登记序 = 求值序;
    T6 emit_updated:op 白名单;op=disable 缺 config_ref 拒;词表未注册 → 降级 False;
    T7 params 含 callable → 拒(防指纹失稳边界);
    T8 describe_rules:7 条、序 = _BUILTIN_IDS、policy_refs/forced 正确(只读投影);
    T9 装配接缝:describe_rules → PolicyEngine 可用,且治理层未 import core。

本文件是 S2-1 的验收证据;S2-1 出口判据 = T1~T7 全绿(M2 判据 ①)。
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from pyharness.core import tools_guard
from pyharness.errors import PyHError
from pyharness.governance import (GovernanceContext, Policy, PolicyEngine,
                                  PolicyRegistry, PolicyRule,
                                  compute_fingerprint)
from pyharness.governance.policy import EVENT_POLICY_UPDATED, POLICY_OPS

GOV_DIR = pathlib.Path(__file__).resolve().parents[2] / "pyharness" / "governance"


# ==================================================================== 替身
def _cfg(disabled=()):
    """鸭子 cfg:security.guards.disabled(只读消费面)。"""
    return SimpleNamespace(security=SimpleNamespace(
        guards=SimpleNamespace(disabled=list(disabled))))


def _rule(rid="r1", refs=("POL-X-1",), forced=False):
    """规则描述替身(函数对象每次新建 → 用于 T2 证明其不入指纹)。"""
    return PolicyRule(rule_id=rid, policy_refs=refs, forced=forced,
                      match=lambda call: True,
                      check=lambda call, scope: ("allow", None))


def _policy(rules=(), disabled=frozenset(), params=None, pid="builtin:v1"):
    return Policy(policy_id=pid, version="1.0.0", rules=tuple(rules),
                  disabled=disabled, params=params or {})


# ================================================ T1 指纹稳定性(M2 判据 ①)
def test_t1_fingerprint_stable_for_same_declarative_content():
    """同一声明式内容两次构造 → 指纹相同(不含函数对象,见 T2)。"""
    a = _policy([_rule("r1"), _rule("r2", refs=("POL-Y-1",))])
    b = _policy([_rule("r1"), _rule("r2", refs=("POL-Y-1",))])
    assert a.fingerprint == b.fingerprint != ""


def test_t1_fingerprint_changes_with_content():
    """任一**声明式**字段变 → 指纹必须变(policy_id/version/rule_id/求值序/
    policy_refs/forced/disabled/params 各验一次)。"""
    base = _policy([_rule("r1"), _rule("r2")])
    fp = base.fingerprint

    assert _policy([_rule("r1"), _rule("r2")], pid="builtin:v2").fingerprint != fp
    assert Policy(policy_id="builtin:v1", version="1.0.1",
                  rules=(_rule("r1"), _rule("r2"))).fingerprint != fp
    # 求值序(元组序 = 求值序,顺序变即指纹变)
    assert _policy([_rule("r2"), _rule("r1")]).fingerprint != fp
    # rule_id / policy_refs / forced
    assert _policy([_rule("r1"), _rule("r3")]).fingerprint != fp
    assert _policy([_rule("r1"), _rule("r2", refs=("POL-Y-1",))]).fingerprint != fp
    assert _policy([_rule("r1"), _rule("r2", forced=True)]).fingerprint != fp
    # disabled 面
    assert _policy([_rule("r1"), _rule("r2")],
                   disabled=frozenset({"r2"})).fingerprint != fp
    # params(声明式值)
    assert _policy([_rule("r1"), _rule("r2")],
                   params={"approval_channel": True}).fingerprint != fp


# ================================================ T2 指纹排除函数对象(R-A)
def test_t2_fingerprint_excludes_callables():
    """match/check 换实现但声明式内容相同 → 指纹**相同**。

    这是 R-A 的直接防护:若把函数对象纳入哈希(repr/id),指纹每次装配都变,
    "指纹随内容变化"会退化为"每次都变",M2 判据 ① 即失效。
    """
    a = PolicyRule(rule_id="r1", policy_refs=("POL-X-1",),
                   match=lambda c: True, check=lambda c, s: ("allow", None))
    b = PolicyRule(rule_id="r1", policy_refs=("POL-X-1",),
                   match=lambda c: False, check=lambda c, s: ("reject", None))
    assert a.match is not b.match                # 确为不同对象
    assert compute_fingerprint(policy_id="p", version="1", rules=[a],
                               disabled=(), params={}) == \
        compute_fingerprint(policy_id="p", version="1", rules=[b],
                            disabled=(), params={})


# ================================================ T3 依赖方向(ADR-018:308)
def _imported_pyharness_modules(path: pathlib.Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return {m for m in out if m.startswith("pyharness")}


def test_t3_governance_import_direction():
    """governance/** 的项目内 import **只允许** events / errors / 本包。

    禁止 core.*(tools_guard/executor/scope/llm)与 bus.plugin——治理层不得反向
    依赖,也不得用 TYPE_CHECKING 变通(保留 import 即保留方向依赖)。
    """
    allowed_prefixes = ("pyharness.events", "pyharness.errors",
                        "pyharness.governance")
    files = sorted(GOV_DIR.glob("*.py"))
    assert files, f"governance 目录应含模块:{GOV_DIR}"
    bad: list = []
    for f in files:
        for mod in _imported_pyharness_modules(f):
            if not mod.startswith(allowed_prefixes):
                bad.append(f"{f.name}: {mod}")
    assert not bad, f"治理层出现越界依赖(ADR-018:308):{bad}"


# ================================================ T4 治理动作(Δ-2/Δ-3)
def test_t4_disable_requires_config_ref():
    """禁用必须携带 config_ref(INV-G6:禁止静默放宽)。"""
    p = _policy([_rule("r1")])
    with pytest.raises(PyHError) as ei:
        p.with_rule_disabled("r1", config_ref="")
    assert ei.value.code == "CFG-601"
    with pytest.raises(PyHError):
        p.with_rule_disabled("r1", config_ref="   ")


def test_t4_forced_rule_cannot_be_disabled():
    """forced 规则恒在不可禁(对应 FORCED_GUARDS)。"""
    p = _policy([_rule("g-schema", forced=True)])
    with pytest.raises(PyHError) as ei:
        p.with_rule_disabled("g-schema", config_ref="security.guards.disabled")
    assert ei.value.code == "CFG-601"


def test_t4_unknown_rule_and_idempotence():
    """规则不在策略内 → CFG-601;重复禁用幂等(不产生新对象)。"""
    p = _policy([_rule("r1"), _rule("r2")])
    with pytest.raises(PyHError) as ei:
        p.with_rule_disabled("nope", config_ref="cfg")
    assert ei.value.code == "CFG-601"

    p2 = p.with_rule_disabled("r1", config_ref="cfg")
    assert p2 is not p and "r1" in p2.disabled
    assert p2.fingerprint != p.fingerprint          # 禁用面变化 → 指纹变化
    assert p2.with_rule_disabled("r1", config_ref="cfg") is p2   # 幂等
    assert len(p2.enabled_rules()) == 1
    # 启用回退(对称治理动作)
    p3 = p2.with_rule_enabled("r1")
    assert p3.disabled == frozenset() and p3.with_rule_enabled("r1") is p3


# ================================================ T5 注册表单调性
def test_t5_registry_duplicate_and_order():
    """同 rule_id 重复注册 → TLB-801;登记序 = 求值序(元组序)。"""
    reg = PolicyRegistry()
    reg.register_rule(_rule("a"))
    reg.register_rule(_rule("b"))
    reg.register_rule(_rule("c"))
    assert [r.rule_id for r in reg.rules()] == ["a", "b", "c"]
    with pytest.raises(PyHError) as ei:
        reg.register_rule(_rule("b"))
    assert ei.value.code == "TLB-801"
    # 无移除/重排 API(延续单调性)
    for forbidden in ("unregister", "remove", "reorder", "clear"):
        assert not hasattr(reg, forbidden)


# ================================================ T6 emit_updated(Δ-1/Δ-3/Q3)
async def test_t6_emit_updated_validates_op_and_config_ref():
    """op 白名单(去 tighten)+ op=disable 必带 config_ref。"""
    reg = PolicyRegistry()
    p = _policy([_rule("r1")])
    with pytest.raises(PyHError) as ei:
        await reg.emit_updated(p, "tighten", reason="x", config_ref="c")
    assert ei.value.code == "CYC-999"            # Δ-1:tighten 归 scope.updated
    with pytest.raises(PyHError) as ei2:
        await reg.emit_updated(p, "disable", reason="x")
    assert ei2.value.code == "CFG-601"           # Δ-3:禁止静默放宽


async def test_t6_emit_updated_degrades_when_unregistered():
    """词表未注册(S2-1 常态)→ 降级返回 False、不抛;注册(S2-2)后同路径生效。"""
    from pyharness.events.vocab import is_registered
    reg = PolicyRegistry()
    p = _policy([_rule("r1")])
    if not is_registered(EVENT_POLICY_UPDATED):          # S2-1 阶段前提
        assert await reg.emit_updated(p, "add", reason="assembly") is False
        assert await reg.emit_updated(p, "disable", reason="x",
                                      config_ref="cfg") is False
    assert POLICY_OPS == frozenset({"add", "enable", "disable"})   # Δ-1


# ================================================ T7 params 边界(R-A)
def test_t7_callable_param_rejected():
    """params 含 callable → 构造期即拒(防指纹失稳)。"""
    with pytest.raises(PyHError) as ei:
        _policy([_rule("r1")], params={"bad": lambda: 1})
    assert ei.value.code == "CYC-999"
    # 集合类参数归一为有序(序不稳定会让同配置算出不同指纹)
    a = _policy([_rule("r1")], params={"d": {"b", "a"}})
    b = _policy([_rule("r1")], params={"d": ["a", "b"]})
    assert a.fingerprint == b.fingerprint


# ================================================ T8 describe_rules(只读投影)
async def test_t8_describe_rules_projection():
    """g1-g7 描述:7 条、求值序 = _BUILTIN_IDS、policy_refs/forced 正确。"""
    descs = tools_guard.describe_rules()
    assert [d.rule_id for d in descs] == list(tools_guard._BUILTIN_IDS)
    assert len(descs) == 7
    by_id = {d.rule_id: d for d in descs}
    assert by_id["g-fs-path"].policy_refs == ("POL-FS-1", "POL-FS-2", "POL-FS-3")
    assert by_id["g-schema"].policy_refs == ("TLB-803",)
    assert by_id["g-overwrite"].policy_refs == ("POL-OVW-1",)
    assert by_id["g-schema"].forced is True and by_id["g-danger"].forced is True
    assert by_id["g-fs-path"].forced is False
    # 只读投影:调一条描述的 check,行为与既有 guard 一致(**未改判定**)
    allowed = SimpleNamespace(policy=SimpleNamespace(allowed_domains=set()))
    call = SimpleNamespace(name="web.fetch", raw_args={"url": "http://x/"},
                           safe_args={"url": "http://x/"}, call_id="c")
    d, ref = await by_id["g-net-outbound"].check(call, allowed)
    assert (d, ref) == ("reject", "POL-NET-1")   # 空 allowlist → 拒(禁外发默认)


# ================================================ T9 装配接缝(Δ-4 注入式)
def test_t9_engine_from_descriptors_and_injection():
    """describe_rules → PolicyEngine.from_config(rules=...) 可用;chain_factory
    注入式生效;未注入 chain_factory → chain is None(S2-1 常态)。"""
    descs = tools_guard.describe_rules()
    eng = PolicyEngine.from_config(_cfg(), rules=descs,
                                   approval_channel=True)
    p = eng.policy
    assert [r.rule_id for r in p.rules] == list(tools_guard._BUILTIN_IDS)
    assert eng.chain is None                     # 未注入 → 未接线
    assert eng.fingerprint() == p.fingerprint != ""
    assert eng.resolve(None, None) is p          # M2:恒为 current
    # 同配置再装配 → 指纹相同(T1 在装配层的复证)
    eng2 = PolicyEngine.from_config(_cfg(), rules=tools_guard.describe_rules(),
                                    approval_channel=True)
    assert eng2.fingerprint() == eng.fingerprint()

    # cfg 声明的禁用面进入初始 disabled(只读消费)
    eng3 = PolicyEngine.from_config(_cfg(["g-fs-path"]), rules=descs)
    assert "g-fs-path" in eng3.policy.disabled

    # chain_factory 注入:治理层不 import tools_guard,由装配层传入
    seen: dict = {}

    def _factory(cfg, **kw):
        seen.update(kw)
        return "FAKE_CHAIN"

    eng4 = PolicyEngine.from_config(_cfg(), rules=descs,
                                   chain_factory=_factory,
                                   validator=object(), approval_channel=False)
    assert eng4.chain == "FAKE_CHAIN"
    assert set(seen) >= {"session", "bus", "validator", "credential_paths",
                         "path_exists", "link_resolver", "approval_channel"}
    assert eng4.fingerprint() != eng.fingerprint()   # 注入面变 → 指纹变


def test_t9_governance_context_m2_shape():
    """GovernanceContext M2 形状:只挂 policy;其余为 None 占位(缺失可见)。"""
    eng = PolicyEngine.from_config(_cfg(), rules=tools_guard.describe_rules())
    g = GovernanceContext(policy=eng)
    assert g.policy is eng
    assert g.decisions is None and g.receipts is None
    assert g.evidence is None and g.audit is None
    assert not hasattr(g, "authorize")           # Δ-5:M2 不声明(偏离登记)
    assert g.policy_fingerprint() == eng.fingerprint()
