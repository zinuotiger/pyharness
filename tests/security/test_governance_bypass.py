"""安全:治理绕过(governance bypass)。

证明点：**不存在绕过治理的执行路径**。撤掉任一必需装配件 ⇒ 执行 fail-closed，
且 Provider **零调用**。
"""
from __future__ import annotations

import pytest

from pyharness.core.tools_guard import GuardChain, ToolCall
from pyharness.errors import PyHError


async def test_missing_governance_blocks_execution(sec):
    """去掉 ctx.governance ⇒ CYC-999，且 Provider 未被调用。"""
    sec.ctx.governance = None
    with pytest.raises(PyHError) as ei:
        await sec.executor.execute(
            ToolCall(name="fs.write_file",
                     raw_args={"path": "a.txt", "content": "x"}, call_id="s1"),
            sec.ctx)
    assert ei.value.code == "CYC-999"
    assert sec.provider_calls("fs.write_file") == 0
    assert not (sec.ws / "a.txt").exists()


async def test_missing_guard_blocks_execution(sec):
    """去掉 ctx.guard ⇒ CYC-999（无 guard.evaluated 的执行非法,INV-04）。"""
    sec.ctx.guard = None
    with pytest.raises(PyHError) as ei:
        await sec.executor.execute(
            ToolCall(name="fs.write_file",
                     raw_args={"path": "b.txt", "content": "x"}, call_id="s2"),
            sec.ctx)
    assert ei.value.code == "CYC-999"
    assert sec.provider_calls("fs.write_file") == 0
    assert not (sec.ws / "b.txt").exists()


async def test_missing_session_blocks_execution(sec):
    """去掉 ctx.session ⇒ CYC-999（事件落点缺失 ⇒ 无审计痕 ⇒ 拒绝）。"""
    sec.ctx.session = None
    with pytest.raises(PyHError) as ei:
        await sec.executor.execute(
            ToolCall(name="fs.write_file",
                     raw_args={"path": "c.txt", "content": "x"}, call_id="s3"),
            sec.ctx)
    assert ei.value.code == "CYC-999"
    assert sec.provider_calls("fs.write_file") == 0


async def test_missing_scope_blocks_execution(sec):
    sec.ctx.scope = None
    with pytest.raises(PyHError) as ei:
        await sec.executor.execute(
            ToolCall(name="fs.list_dir", raw_args={"path": "."}, call_id="s4"),
            sec.ctx)
    assert ei.value.code == "CYC-999"
    assert sec.provider_calls("fs.list_dir") == 0


def test_guard_chain_has_no_bypass_api():
    """结构防线：GuardChain **不得**暴露任何放行/绕过/执行类方法。"""
    chain = GuardChain()
    forbidden = ("override", "bypass", "force_allow", "set_allow", "allow",
                 "execute", "run", "mark_allowed", "clear_reject",
                 "release_decision")
    for name in forbidden:
        with pytest.raises(AttributeError):
            getattr(chain, name)


async def test_reject_cannot_be_flipped_by_re_evaluation(sec):
    """单调性：被拒事实不因**再次求值**而翻转为放行（同调用同策略）。"""
    from pyharness.governance.decision import Verdict

    call = ToolCall(name="fs.write_file",
                    raw_args={"path": "../ESC.txt", "content": "x"},
                    call_id="s5")
    d1 = await sec.gov.authorize(call, sec.ctx)
    assert d1.verdict is Verdict.REJECT
    d2 = await sec.gov.authorize(call, sec.ctx)
    assert d2.verdict is Verdict.REJECT
    assert sec.provider_calls("fs.write_file") == 0
    assert not (sec.tmp / "ESC.txt").exists()
