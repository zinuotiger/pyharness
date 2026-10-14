"""E2E:N2-a/N2-b 子代理可达性与证据 —— **真实 ``subagent.spawn`` 工具路径**。

本文件分两段:
  A(N2-a,本轮 M5-2):默认 strict 档下 ``subagent.spawn`` **可达**并真实派生子会话;
  B(N2-b,本轮 M5-4):父/子会话各自的治理证据链完整。

**禁止**直调 ``SubagentManager.spawn`` API 伪造子会话 —— 一律经 LLM tool_calls
走四关管道,以证明"LLM 视角真的能用到"。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


from pyharness.core import llm as llm_mod


class _ChildAwareAdapter:
    """按 session 分派脚本:父轮 1 派子任务;子轮 1 调 ``fs.list_dir``,轮 2 收尾。"""

    def __init__(self, model: str, parent_sid: str) -> None:
        self.model = model
        self.parent = parent_sid
        self.n: dict[str, int] = {}

    async def chat(self, messages, tools=None, *, ctx):
        sid = getattr(getattr(ctx, "session", None), "sid", "?")
        self.n[sid] = self.n.get(sid, 0) + 1
        k = self.n[sid]
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=1)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        tc = None
        if sid == self.parent and k == 1:
            tc = llm_mod.ToolCall(id="sp", index=0, name="subagent.spawn",
                                  raw_args={"task": "list the workspace"},
                                  raw_json=json.dumps({"task": "list the workspace"}))
        elif sid != self.parent and k == 1:
            tc = llm_mod.ToolCall(id="c1", index=0, name="fs.list_dir",
                                  raw_args={"path": "."}, raw_json='{"path":"."}')
        if tc is not None:
            resp = llm_mod.LLMResponse(content="", tool_calls=[tc], usage=usage,
                                       model=self.model,
                                       finish_reason="tool_calls")
        else:
            resp = llm_mod.LLMResponse(content="done", tool_calls=None,
                                       usage=usage, model=self.model,
                                       finish_reason="stop")
        env = await ctx.session.append(
            "llm.response",
            {"model": self.model, "finish_reason": resp.finish_reason,
             "content": resp.content or "",
             "tool_calls": [{"id": c.id, "name": c.name,
                             "arguments": c.raw_json}
                            for c in (resp.tool_calls or [])]}, actor="llm")
        resp.seq = env.seq
        return resp

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self) -> float:
        return 0.01


def _read_types(p) -> list[str]:
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line)["type"])
    return out


async def _drive_subagent(e2e_factory, sid: str, *, preset=None):
    """经真实工具路径派生一个子代理;返回 (session, 子会话事件类型统计)。"""
    saved = dict(llm_mod.adapters)
    s = await e2e_factory(None, sid=sid)          # 先建会话以获得 cfg/model
    llm_mod.adapters.clear()
    s.ctx.engine_spine.llm_runtime.registry[s.ctx.settings.llm.model] = _ChildAwareAdapter(
        s.ctx.settings.llm.model, sid)
    try:
        await s.boot()
        assert (await s.run("spawn a subagent")).ok
        manager = s.ctx.engine_spine.subagent
        tasks = [h.task for h in manager._children.values() if h.task is not None]
        assert s.of("subagent.spawned"), "真实子任务已经派发"
        if not tasks:
            assert s.of("subagent.joined") or s.of("subagent.failed"), "已摘除的任务必须有终态"
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        sessions_dir = s.tmp / "sessions"
        children = [f for f in sessions_dir.glob("*.jsonl") if f.stem != sid]
        stats = {}
        for f in children:
            t = _read_types(f)
            stats[f.stem] = {
                "decision.issued": t.count("decision.issued"),
                "receipt.emitted": t.count("receipt.emitted"),
                "guard.evaluated": t.count("guard.evaluated"),
                "tool.call": t.count("tool.call"),
                "tool.result": t.count("tool.result"),
                "tool.error": t.count("tool.error"),
                "segment.start": t.count("segment.start"),
                "segment.end": t.count("segment.end"),
                "evidence.archived": t.count("evidence.archived"),
            }
        return s, children, stats
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


# ================================================== A · 可达性(N2-a)
async def test_subagent_reachable_under_default_strict(e2e_factory):
    """**N2-a 回归锚**:默认 strict 档下,真实工具路径可派生子会话。

    修复前:``can_use`` 为 False ⇒ GRD-401 ⇒ ``tool.result`` 0、``subagent.spawned`` 0、
    子会话文件 0 个。
    """
    s, children, _stats = await _drive_subagent(e2e_factory, "s-e2e-sub-strict")
    assert s.ctx.scope.policy.sandbox_level == "strict", "本用例须在默认档下运行"
    assert s.of("guard.rejected") == [], "不应被 scope-hidden 拒绝"
    assert s.of("tool.result"), "subagent.spawn 应产生 tool.result"
    assert s.of("subagent.spawned"), "应落 subagent.spawned 事实"
    assert children, "应真实派生一个子会话(独立 JSONL)"


async def test_subagent_spawn_not_scope_hidden(e2e_factory):
    """显式反例:同一路径**不得**出现 ``scope-hidden`` / ``GRD-401``。"""
    s, _children, _stats = await _drive_subagent(e2e_factory, "s-e2e-sub-nosh")
    pol = [e.payload.get("policy_ref") for e in s.of("guard.rejected")]
    assert "GRD-401" not in pol, f"subagent.spawn 不应被判 scope-hidden:{pol}"


# ================================================== B · 证据链(N2-b)
async def test_parent_and_child_both_have_evidence(e2e_factory):
    """**N2-b 回归锚**:父会话与**子会话**都必须形成完整治理证据链。

    修复前实测:子会话 ``decision.issued=2 / receipt.emitted=2`` 但
    ``segment.start=0 / segment.end=0 / evidence.archived=0``。
    """
    s, children, stats = await _drive_subagent(e2e_factory, "s-e2e-sub-evidence")
    assert children, "应有子会话"
    child = stats[children[0].stem]
    assert child["decision.issued"] >= 1, f"子会话应产生治理决策:{child}"
    assert child["receipt.emitted"] >= 1, f"子会话应产生凭证:{child}"
    assert child["segment.start"] >= 1, f"子会话应有自己的任务段:{child}"
    assert child["segment.end"] >= 1, f"子会话的段应关闭:{child}"
    assert child["evidence.archived"] >= 1, f"子会话应产出证据工件:{child}"

    parent = {
        "decision.issued": s.count("decision.issued"),
        "segment.end": s.count("segment.end"),
        "evidence.archived": s.count("evidence.archived"),
    }
    assert parent["decision.issued"] >= 1, parent
    assert parent["segment.end"] >= 1, parent
    assert parent["evidence.archived"] >= 1, parent


# ==================================== B2 · 子工具**真的执行成功**(N10 回归锚)
async def test_child_tool_call_actually_succeeds_in_child_workspace(e2e_factory):
    """**N10 回归锚**:子会话里的 ``fs.list_dir`` 必须**真跑成功**,不是只落事件。

    2026-09-21 修前实测:子 workspace 根被算成 ``{workspaces_dir}/../{sub_id}``
    (越出 workspaces 树)且**无人 mkdir** ⇒ 治理判 allow、provider 抛 TLB-802
    「目录不存在」⇒ 子会话只有 ``tool.error``、没有 ``tool.result``;而当时的断言
    只数 ``decision/receipt/segment`` 事件,全绿——**"事件存在 ≠ 能力可用"**。
    故本用例把"子工具终局事件是真 result 而非 error"钉成契约。
    """
    s, children, stats = await _drive_subagent(e2e_factory, "s-e2e-sub-toolok")
    assert children, "应有子会话"
    child = stats[children[0].stem]
    assert child["tool.call"] >= 1, f"子会话应发起工具调用:{child}"
    assert child["tool.error"] == 0, \
        f"子会话工具不得失败(修前即 TLB-802 目录不存在):{child}"
    assert child["tool.result"] >= 1, f"子会话应落真 tool.result:{child}"

    # 结构性佐证:子 workspace 根落在 workspaces 树内且真实存在
    from pathlib import Path
    root = Path(s.ctx.scope.policy.workspace_root)
    tree = root.parent
    sub_id = s.of("subagent.spawned")[0].payload["sub_id"]
    child_root = tree / sub_id
    assert child_root.is_dir(), f"子 workspace 根应先被创建:{child_root}"


# ================================================== C · 父侧可追溯(M5-4 验收点)
async def test_parent_can_trace_child_result_via_joined(e2e_factory):
    """父会话经 ``subagent.joined`` 获得对子会话结果的**可追踪摘要**。

    这是 M5-4 明列的验收点:子会话产出证据之外,父侧必须有一条可读的回收事实。
    """
    s, children, _stats = await _drive_subagent(e2e_factory, "s-e2e-sub-joined")
    assert children, "应有子会话"
    sp = s.of("subagent.spawned")
    assert sp, "应落 subagent.spawned(派发事实)"
    assert sp[0].payload.get("sub_id"), f"spawned 应带 sub_id:{sp[0].payload}"

    joined = s.of("subagent.joined")
    assert joined, "应落 subagent.joined(回收事实)"
    payload = joined[0].payload
    assert payload.get("sub_id"), f"joined 应带 sub_id:{payload}"
    summary = str(payload.get("summary") or "")
    assert summary, f"joined 应携带可追踪摘要:{payload}"
    assert len(summary) <= 2048, "摘要 ≤2KB(F049 契约)"
