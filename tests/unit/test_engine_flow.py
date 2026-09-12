"""engine 全链离线冒烟 — 装配即真链的一次真实跑(无网络,fake LLM 传输)。

场景:assemble_real_engine + TaskQueue(engine runner seam)→ 提交一段用户意图 →
agent-loop 真实执行:① sysprompt 注入(首条消息 role=system,护栏段恒末)
② 工具轮(goal.create + todo.add 双调用)经四关管道(guard/executor)
③ 第二轮到纯文本终态 ④ llm.usage 入账共享计数器(预算闸数据源)
⑤ spill provider / 会话 KV 首建激活。

fake adapter 注入 llm 注册表前于 assemble(register 幂等跳过),全程零网络。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from pyharness.config import Settings, load_settings
from pyharness.core import llm as llm_mod
from pyharness.core.task_queue import TaskQueue
from pyharness.engine import assemble_real_engine


def _cfg(tmp_path) -> Settings:
    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "pyharness.db")
    return cfg


class FakeAdapter:
    """离线适配器:轮 1 回 goal+todo 双工具调用;轮 2+ 纯文本终态。

    同时在每轮断言系统提示词装配(sysprompt 注入是 #4 的验收点)。"""

    model = "deepseek-chat"

    def __init__(self, model: str = "deepseek-chat") -> None:
        self.model = model
        self.calls = 0
        self.sys_contents: list[str] = []

    async def chat(self, messages, tools=None, *, ctx) -> llm_mod.LLMResponse:
        self.calls += 1
        sys_msg = next((m for m in messages if m.get("role") == "system"), None)
        if sys_msg is not None:                 # 对话轮:断言系统提示词装配
            self.sys_contents.append(sys_msg["content"])
            content = sys_msg["content"]
            assert "[禁止操作]" in content and "[工作区]" in content, \
                "护栏段(guard)必须已装配进 system content"
            tool_names = {(t.get("function") or {}).get("name")
                          for t in (tools or []) if t}
            assert {"goal", "todo"}.issubset(tool_names), \
                f"goal/todo 工具应注册进 schema;实际={sorted(tool_names)}"
            await ctx.session.append("llm.request",
                                     {"model": self.model,
                                      "n_tools": len(tools or [])},
                                     actor="llm")
        usage = __import__("types").SimpleNamespace(
            prompt_tokens=120, completion_tokens=45,
            prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=120)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        if self.calls == 1:                       # 轮 1:工具轮(goal + todo)
            calls = [
                llm_mod.ToolCall(id="c1", index=0, name="goal",
                                 raw_args={"op": "create",
                                           "text": "整理面试弹药库"},
                                 raw_json=json.dumps(
                                     {"op": "create",
                                      "text": "整理面试弹药库"}, ensure_ascii=False)),
                llm_mod.ToolCall(id="c2", index=1, name="todo",
                                 raw_args={"op": "add",
                                           "text": "列演示大纲"},
                                 raw_json=json.dumps({"op": "add",
                                                      "text": "列演示大纲"},
                                                     ensure_ascii=False)),
            ]
            resp = llm_mod.LLMResponse(content="", tool_calls=calls,
                                       usage=usage, model=self.model,
                                       finish_reason="tool_calls")
        else:                                     # 轮 2+:纯文本终态
            resp = llm_mod.LLMResponse(
                content="好,目标已建、待办已记,随时开干。",
                tool_calls=None, usage=usage, model=self.model,
                finish_reason="stop")
        env = await ctx.session.append(
            "llm.response",
            {"model": self.model, "finish_reason": resp.finish_reason,
             "content": resp.content or "",
             "tool_calls": [{"id": c.id, "name": c.name,
                             "arguments": c.raw_json}
                            for c in (resp.tool_calls or [])]},
            actor="llm")
        resp.seq = env.seq
        return resp

    async def chat_stream(self, messages, tools=None, *, ctx) -> llm_mod.LLMResponse:
        return await self.chat(messages, tools, ctx=ctx)   # 冒烟走同链路

    async def ping(self) -> float:
        return 0.01


@pytest.mark.asyncio
async def test_engine_full_chain_offline(tmp_path):
    cfg = _cfg(tmp_path)
    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    fake = FakeAdapter(cfg.llm.model)
    llm_mod.adapters[cfg.llm.model] = fake          # 先注入 → assemble 幂等跳过
    ctx = None
    try:
        ctx = await assemble_real_engine(
            cfg, sid="s-eng-flow-000001",
            sessions_dir=__import__("pathlib").Path(tmp_path) / "sessions")
        # 会话引导(desktop manager 职责):首事件 created(seq=1,EVT-106)
        await ctx.session.append("session.created",
                                 {"title": "", "model": cfg.llm.model},
                                 actor="system", sync=True)
        # 用户输入由调用方先落盘(create_message 同款:user.message 强同步),
        # TaskQueue 只承载执行编排;runner 按最近 user.message 唤醒 loop
        intent = "帮我建个目标并记个待办,然后总结"
        await ctx.session.append("user.message", {"content": intent},
                                 actor="user", sync=True)
        q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
        tid = await q.submit(intent)
        res = await asyncio.wait_for(q.wait_for(tid), timeout=8.0)
        assert res.ok, f"全链 run 应成功,实际 code={getattr(res, 'code', res)}"
        evs = list(ctx.session.events_after(0))
        types = [e.type for e in evs]
        # ① 系统提示词:对话两轮均注入且含护栏段;第三轮 = F042 自动标题(单 user 消息)
        assert fake.calls == 3, "两轮对话 + 一轮自动标题"
        assert len(fake.sys_contents) == 2
        assert "当前活动目标" in fake.sys_contents[1], \
            "F047:目标创建后第二轮 sysprompt 应注入活动目标板"
        # ② 工具真执行:goal.create → goal.created;todo.add → todo.updated
        assert "tool.call" in types and "tool.result" in types
        assert "goal.created" in types, "goal.create 工具应落 goal.created 事件"
        assert "todo.updated" in types, "todo.add 工具应落 todo.updated 事件"
        # ③ 纯文本终态 + 预算计量
        assert "agent.message" in types
        assert "session.renamed" in types, "F042/#7:run 完成后应自动命名落盘"
        counters = ctx.counters
        assert counters.requests >= 2 and counters.in_tokens >= 200, \
            "llm.usage 应入账共享计数器(F029)"
        assert ctx.scope.budget_state() == "ok"
        # ④ spill / KV 首建激活(engine._activate_storage_caps)
        assert ctx.storage.spill is not None, "spill provider 应已激活"
        assert ctx.storage.kv is not None, "会话 KV 应已激活(#53)"
        # ⑤ guard 事件链:goal/todo danger=none → 无 guard.rejected
        assert "guard.rejected" not in types
        # ⑥ 会话目标/待办可见(管理器与事件同步;管理器在 engine_spine)
        spine = ctx.engine_spine
        acts = spine.goals.list_active()
        assert len(acts) == 1 and acts[0].desc == "整理面试弹药库"
        assert spine.todos.active_count() == 1
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)
        if ctx is not None:
            try:
                ctx.scope.release()
            except Exception:                      # noqa: BLE001 收尾尽力
                pass
