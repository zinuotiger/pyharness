"""安全:拒绝后执行(deny → execute)与重放(GRD-402)。

证明点：**被拒调用零副作用**，且**同一 call_id 不可重放**以绕过拒绝。
"""
from __future__ import annotations

import pytest

from pyharness.core.tools_guard import ToolCall
from pyharness.errors import PyHError


async def test_policy_reject_leaves_zero_side_effect(sec):
    """g3 路径几何拒绝：越界文件**不存在**，Provider **零调用**。"""
    target = sec.tmp / "ESCAPED.txt"
    assert not target.exists()
    res = await sec.executor.execute(
        ToolCall(name="fs.write_file",
                 raw_args={"path": "../ESCAPED.txt", "content": "PWNED"},
                 call_id="d1"), sec.ctx)
    assert res.ok is False
    assert res.summary.startswith("guard 拒绝")
    assert sec.provider_calls("fs.write_file") == 0        # ★ 零执行强证据
    assert not target.exists()                             # ★ 零副作用强证据
    assert len(sec.session.events_after(0)) > 0
    assert [e.type for e in sec.session.events_after(0)].count("tool.result") == 0


async def test_critical_tool_reject_leaves_file_intact(sec):
    """critical 工具(注册即拒)：文件内容**原样**，Provider **零调用**。"""
    victim = sec.ws / "victim.txt"
    victim.write_text("DO NOT DELETE", encoding="utf-8")
    res = await sec.executor.execute(
        ToolCall(name="fs.delete_file", raw_args={"path": "victim.txt"},
                 call_id="d2"), sec.ctx)
    assert res.ok is False
    assert sec.provider_calls("fs.delete_file") == 0
    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "DO NOT DELETE"


async def test_rejected_call_id_cannot_be_replayed(sec):
    """GRD-402 防重放：已裁决(拒绝)的 call_id 再次执行 → 拒绝上抛。"""
    call = ToolCall(name="fs.write_file",
                    raw_args={"path": "../R.txt", "content": "x"},
                    call_id="replay-1")
    first = await sec.executor.execute(call, sec.ctx)
    assert first.ok is False
    with pytest.raises(PyHError) as ei:
        await sec.executor.execute(call, sec.ctx)
    assert ei.value.code == "GRD-402"
    assert sec.provider_calls("fs.write_file") == 0
    assert not (sec.tmp / "R.txt").exists()


async def test_scope_hidden_tool_is_rejected(sec):
    """scope 前置(不可见工具)→ 终局拒绝,Provider 零调用(GRD-401)。"""
    from pyharness.core.tools_guard import Decision

    call = ToolCall(name="fs.delete_file", raw_args={"path": "victim.txt"},
                    call_id="d3")
    d, guard_ids, refs = await sec.guard._evaluate_full(call, sec.scope)
    assert d is Decision.REJECT
    assert guard_ids == ("scope-hidden",)
    assert refs == ("GRD-401",)
    assert sec.provider_calls("fs.delete_file") == 0
