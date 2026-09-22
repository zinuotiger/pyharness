"""不变量:Channel Contract Uniqueness（通道契约唯一性）—— N5。

**性质**:给定同一 ctx 通道状态,**所有层**必须给出**同一判定**:
"是不是人类通道、是哪一个";不可接受状态必须**所有层一致 fail-closed**。

**机制**:唯一实现 = ``core.channel``(核心层直接消费);治理层因 ADR-018:308 不得
import ``core.*``,经其本包 ``Principal.from_legacy_by`` 消费 —— 二者逐状态一致由
**本文件**的真值表钉死(沿用 ``decision.HUMAN_CHANNELS`` 的"受守卫的重复"先例)。

**证据**:下表即运行时证据(真调各层,不做字符串匹配)。

**负向**:让任一层恢复"自行 fallback / falsy 合并 / 精确名匹配" ⇒ 本文件必须 RED。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyharness.core.approval import ApprovalProvider
from pyharness.core.channel import (HUMAN_CHANNELS,
                                    normalize_channel, resolve_channel)
from pyharness.errors import PyHError
from pyharness.governance import DecisionEngine, GovernanceContext

_MISSING = object()

# (标签, 原始值) —— 七状态(A~G)。MISSING 用哨兵表示"属性缺失"。
STATES = [
    ("A missing", _MISSING),
    ("B explicit None", None),
    ("C empty string", ""),
    ("D desktop", "desktop"),
    ("E acp:client-7", "acp:client-7"),
    ("F invalid", "hacker"),
    ("G unknown(non-str)", 123),
]

# 期望语义(冻结):
#   fail-closed = MISSING/INVALID/UNKNOWN
#   headless    = 显式 None
#   interactive = 白名单名或 "<name>:<id>"
EXPECTED = {
    "A missing": "fail-closed",
    "B explicit None": "headless",
    "C empty string": "fail-closed",
    "D desktop": "interactive:desktop",
    "E acp:client-7": "interactive:acp",
    "F invalid": "fail-closed",
    "G unknown(non-str)": "fail-closed",
}


def _ctx(state):
    ns = SimpleNamespace(headless=False)
    if state is not _MISSING:
        ns.channel = state
    return ns


def _gov():
    return GovernanceContext(policy=None, decisions=DecisionEngine())


# ---------------------------------------------------------------- 真值表
@pytest.mark.parametrize("label,state", STATES)
def test_normalize_matches_frozen_table(label, state):
    """第 0 层(规范实现)与冻结表一致。"""
    if state is _MISSING:
        st = resolve_channel(_ctx(state))
    else:
        st = normalize_channel(state)
    want = EXPECTED[label]
    got = ("fail-closed" if not st.is_acceptable
           else "headless" if st.is_headless
           else f"interactive:{st.name}")
    assert got == want, f"{label}: 规范实现={got} 冻结表={want}"


@pytest.mark.parametrize("label,state", STATES)
def test_governance_layer_agrees(label, state):
    """治理层判定 = 冻结表(经 ``Principal.from_legacy_by`` 消费同一契约)。"""
    gc = _gov()
    want = EXPECTED[label]
    if want == "fail-closed":
        with pytest.raises(PyHError) as ei:
            gc.principal_of(_ctx(state))
        assert ei.value.code == "APR-503", label
        return
    p = gc.principal_of(_ctx(state))
    if want == "headless":
        assert str(p.kind) == "system" and p.id == "system", label
    else:
        name = want.split(":", 1)[1]
        assert str(p.kind) == "human", label
        assert p.channel == name, f"{label}: 通道应为 {name},实际 {p.channel}"


@pytest.mark.parametrize("label,state", STATES)
def test_approval_layer_agrees(label, state):
    """审批层判定 = 冻结表(与治理层逐状态一致;修复前二者相反)。"""
    prov = ApprovalProvider(session=None, bus=None)
    want = EXPECTED[label]
    if want == "fail-closed":
        with pytest.raises(PyHError) as ei:
            prov._ensure_channel(_ctx(state))
        assert ei.value.code == "APR-503", label
        return
    ch = prov._ensure_channel(_ctx(state))
    if want == "headless":
        assert ch is None, label
    else:
        assert ch == want.split(":", 1)[1], f"{label}: 实际 {ch}"


@pytest.mark.parametrize("label,state", STATES)
def test_service_layer_agrees(label, state):
    """服务层:交互通道归一化;其余一律拒绝(falsy 提升已消除)。"""
    from pyharness.application import ApplicationService
    want = EXPECTED[label]
    if want != "interactive" and not want.startswith("interactive:"):
        with pytest.raises(PyHError) as ei:
            ApplicationService(SimpleNamespace(), channel=state
                               if state is not _MISSING else "")
        assert ei.value.code == "APR-503", label
        return
    svc = ApplicationService(SimpleNamespace(), channel=state)
    assert svc.channel == want.split(":", 1)[1], label


# ------------------------------------------------- 跨层一致性(核心断言)
@pytest.mark.parametrize("label,state", STATES)
def test_all_layers_agree_on_same_state(label, state):
    """**唯一性**:同一状态在治理层与审批层得到**同一类别**判定。

    这是 N5 的核心断言 —— 修复前 MISSING 在审批层=desktop(人类)而在治理层=拒绝。
    """
    gc = _gov()
    prov = ApprovalProvider(session=None, bus=None)

    def _verdict(fn):
        try:
            return ("ok", fn())
        except PyHError as e:
            return ("fail", e.code)

    gov_kind, gov_val = _verdict(lambda: gc.principal_of(_ctx(state)))
    app_kind, app_val = _verdict(lambda: prov._ensure_channel(_ctx(state)))

    assert gov_kind == app_kind, \
        f"{label}: 治理层={gov_kind}({gov_val}) 审批层={app_kind}({app_val}) —— 分裂!"
    if gov_kind == "fail":
        assert gov_val == app_val == "APR-503", label
    elif EXPECTED[label] == "headless":
        assert gov_val.id == "system" and app_val is None, label
    else:
        name = EXPECTED[label].split(":", 1)[1]
        assert gov_val.channel == name == app_val, label


# ------------------------------------------------- 无 falsy 回退(负向性质)
def test_empty_string_is_not_silently_degraded():
    """``""`` **不得**经 falsy 合并降级为 system(修复前为 system)。"""
    gc = _gov()
    with pytest.raises(PyHError) as ei:
        gc.principal_of(_ctx(""))
    assert ei.value.code == "APR-503"


def test_provider_default_never_overrides_missing_channel():
    """provider 缺省通道**不得**作为 ctx 未声明时的回退(修复前被硬编码 desktop 劫持)。"""
    prov = ApprovalProvider(session=None, bus=None, channel="desktop")
    assert prov._channel == "desktop"            # 缺省已归一化保留
    with pytest.raises(PyHError) as ei:
        prov._ensure_channel(_ctx(_MISSING))     # ctx 未声明 ⇒ 必须 fail-closed
    assert ei.value.code == "APR-503"


def test_whitelist_sources_agree():
    """三处通道白名单必须一致(受守卫的重复)。"""
    from pyharness.core.approval import CHANNELS
    from pyharness.governance.decision import HUMAN_CHANNELS as GOV_WHITELIST
    assert tuple(CHANNELS) == tuple(HUMAN_CHANNELS) == tuple(GOV_WHITELIST)


@pytest.mark.parametrize("bad", ["test", "hacker", "", None, "CLI", "desktop:",
                                 " acp"])
def test_service_rejects_non_whitelisted_channel(bad):
    """**收紧记录**:服务层不再接受任意非空串作通道。

    修复前 ``str(channel or "desktop")`` 会原样保留 ``"test"``/``"hacker"`` 这类
    非白名单串 —— 而该值会作为 ``by=`` 传给审批,在 ``_require_human`` 处才会
    APR-503(潜伏缺陷:构造成功、审批时才炸)。现构造期即 fail-closed。
    """
    from pyharness.application import ApplicationService
    with pytest.raises(PyHError) as ei:
        ApplicationService(SimpleNamespace(), channel=bad)
    assert ei.value.code == "APR-503"
