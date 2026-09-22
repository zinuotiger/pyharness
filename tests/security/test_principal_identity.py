"""安全:主体身份(principal identity)——伪造、非法通道、上下文丢失。

证明点(GAP-9 / GAP-11 的安全面)：

1. 自报身份**无权**冒充人类(GAP-11 前的既有防线);
2. 非法通道名 ⇒ ``APR-503`` fail-closed;
3. **通道属性缺失 ≠ headless** ⇒ ``APR-503`` fail-closed(绝不静默降级为 system);
4. 决策载荷携带的主体与运行时通道**同源**(声明什么就是什么,不反推)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyharness.core.tools_guard import ToolCall
from pyharness.errors import PyHError
from pyharness.governance.decision import Principal, PrincipalKind


# ------------------------------------------------------------ 伪造主体
@pytest.mark.parametrize("forged", ["hacker", "sys:admin", "", None, 42,
                                    "llm:", "tool:", "plugin:", "cli:",
                                    "desktop:"])
def test_forged_or_malformed_by_is_rejected(forged):
    """未知前缀 / 空 / 非字符串 / **前缀后缺 id** ⇒ ``APR-503``。

    注意区分:``llm:<id>`` 这类**合法非人类前缀**不是伪造(见下一条)——它们被
    解析为 AGENT/TOOL/PLUGIN 主体,真正的防线是"它们**无权**成为 HUMAN"。
    本条只覆盖**格式非法**的取值。
    """
    with pytest.raises(PyHError) as ei:
        Principal.from_legacy_by(forged)
    assert ei.value.code == "APR-503"


@pytest.mark.parametrize("by,kind,pid", [
    ("llm:gpt", PrincipalKind.AGENT, "gpt"),
    ("tool:exec", PrincipalKind.TOOL, "exec"),
    ("plugin:evil", PrincipalKind.PLUGIN, "evil"),
])
def test_non_human_prefixes_parse_but_never_become_human(by, kind, pid):
    """合法非人类前缀 ⇒ 解析为对应**非 HUMAN** 主体(绝不升级为人类)。"""
    p = Principal.from_legacy_by(by)
    assert p.kind is kind
    assert p.id == pid
    assert p.kind is not PrincipalKind.HUMAN
    assert p.channel is None


def test_human_principal_requires_whitelisted_channel():
    """HUMAN 主体必须在人类通道白名单内,且 id 不得带非人类前缀。"""
    assert Principal(PrincipalKind.HUMAN, "alice", "cli").channel == "cli"
    with pytest.raises(PyHError):
        Principal(PrincipalKind.HUMAN, "alice", "llm")       # 非白名单通道
    with pytest.raises(PyHError):
        Principal(PrincipalKind.HUMAN, "llm:alice", "cli")   # 非人类前缀


# ------------------------------------------------ 通道伪造 / 缺失 / 非法
async def test_illegal_channel_value_fails_closed(sec):
    """非法通道名(非白名单字符串)⇒ 授权 fail-closed,Provider 零调用。"""
    sec.ctx.channel = "hacker"
    with pytest.raises(PyHError) as ei:
        await sec.executor.execute(
            ToolCall(name="fs.list_dir", raw_args={"path": "."}, call_id="p1"),
            sec.ctx)
    assert ei.value.code == "APR-503"
    assert sec.provider_calls("fs.list_dir") == 0
    assert not (sec.ws / "x").exists()


async def test_absent_channel_attribute_fails_closed(sec):
    """**属性缺失** ≠ headless ⇒ APR-503(禁止静默降级为 system 主体)。"""
    del sec.ctx.channel                      # 从未声明
    with pytest.raises(PyHError) as ei:
        await sec.executor.execute(
            ToolCall(name="fs.list_dir", raw_args={"path": "."}, call_id="p2"),
            sec.ctx)
    assert ei.value.code == "APR-503"
    assert sec.provider_calls("fs.list_dir") == 0


async def test_explicit_headless_is_system_principal(sec):
    """显式声明 ``channel=None`` 是**合法**的 headless:SYSTEM 主体,且能执行。

    与上一条对照:两条用例的差别只在"声明了 None"与"没声明"——
    前者是合法语义,后者是装配缺陷。这正是 GAP-11 三态的全部意义。
    """
    from pyharness.governance.decision import Verdict

    sec.ctx.channel = None
    d = await sec.gov.authorize(
        ToolCall(name="fs.list_dir", raw_args={"path": "."}, call_id="p3"),
        sec.ctx)
    assert d.verdict is Verdict.ALLOW
    assert d.principal.kind is PrincipalKind.SYSTEM
    assert d.principal.id == "system"


# -------------------------------------------------- 主体与运行时通道同源
async def test_decision_principal_matches_declared_channel(sec):
    """三个通道各声明一次:决策载荷里的主体必须**等于**声明值(不反推、不默认)。"""
    from pyharness.governance.decision import Verdict

    for ch, kind, pid in (("desktop", PrincipalKind.HUMAN, "desktop"),
                          ("cli", PrincipalKind.HUMAN, "cli"),
                          ("acp:client-7", PrincipalKind.HUMAN, "client-7"),
                          (None, PrincipalKind.SYSTEM, "system")):
        sec.ctx.channel = ch
        sec.session._ev.clear()
        d = await sec.gov.authorize(
            ToolCall(name="fs.list_dir", raw_args={"path": "."}, call_id="p4"),
            sec.ctx)
        assert d.verdict is Verdict.ALLOW
        assert d.principal.kind is kind, f"channel={ch}"
        assert d.principal.id == pid, f"channel={ch}"
        payload = [e for e in sec.session.events_after(0)
                   if e.type == "decision.issued"][0].payload
        assert payload["principal_kind"] == str(kind)
        assert payload["principal_id"] == pid


async def test_principal_is_not_inferred_from_approval_verdict(sec):
    """主体**不得**由 approval/verdict 反推:headless 下即便有 granted 也仍是 SYSTEM。"""
    from pyharness.governance.decision import Verdict

    sec.ctx.channel = None                    # 明确无人类通道
    sec.ctx.approval = SimpleNamespace(
        approval_ref_of=lambda cid: None,
        grant_binding=lambda cid: None)
    d = await sec.gov.authorize(
        ToolCall(name="fs.write_file",
                 raw_args={"path": "a.txt", "content": "x"}, call_id="p5"),
        sec.ctx, prior=None)
    assert d.verdict is Verdict.ALLOW or d.verdict is Verdict.REJECT
    assert d.principal.kind is PrincipalKind.SYSTEM, \
        "无通道时主体必须仍是 SYSTEM,不得因工具/审批状态被提升为 HUMAN"
