# 共享 fixtures:pytest 自动发现

# ---------------------------------------------------------------------------
# E2E / Acceptance 共用夹具（真实引擎装配，零网络）
#
# 口径（tests/e2e 与 tests/acceptance 共同纪律）：装配**真实**
# ``assemble_real_engine``（真 store / 真 SessionLog / 真 GuardChain / 真
# ToolExecutor / 真持久化），**仅** LLM 适配器为脚本替身——这是无网络环境下唯一
# 可能的替换点，且它不在被测链路上（被测的是"工具调用 → 治理 → 执行 → 事件 →
# 落盘 → 审计"这一段）。
#
# 禁止：把 unit 测试搬进 e2e/acceptance 冒充闭环；用 mock 替换
# governance / tools / persistence。
# ---------------------------------------------------------------------------
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.config import Settings, load_settings          # noqa: E402
from pyharness.core import llm as llm_mod                     # noqa: E402
from pyharness.core.task_queue import TaskQueue                # noqa: E402
from pyharness.engine import assemble_real_engine              # noqa: E402


class ScriptedAdapter:
    """脚本化 LLM 适配器：第 1 轮回给定 tool_calls，第 2 轮起回纯文本终态。

    按真实适配器契约落 ``llm.response`` / ``llm.usage`` 事件并计入
    ``ctx.counters``——使 E2E 的预算/计量读数与真实链同源。
    """

    def __init__(self, model: str, calls: list[dict] | None = None) -> None:
        self.model = model
        self.calls = list(calls or [])
        self.n = 0
        self.sys_contents: list[str] = []

    async def chat(self, messages, tools=None, *, ctx) -> llm_mod.LLMResponse:
        self.n += 1
        sys_msg = next((m for m in messages if m.get("role") == "system"), None)
        if sys_msg is not None:
            self.sys_contents.append(sys_msg["content"])
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=100)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        if self.n == 1 and self.calls:
            tcs = [llm_mod.ToolCall(id=c["id"], index=i, name=c["name"],
                                    raw_args=c["args"],
                                    raw_json=json.dumps(c["args"],
                                                        ensure_ascii=False))
                   for i, c in enumerate(self.calls)]
            resp = llm_mod.LLMResponse(content="", tool_calls=tcs, usage=usage,
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
                            for c in (resp.tool_calls or [])]},
            actor="llm")
        resp.seq = env.seq
        return resp

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self) -> float:
        return 0.01


def e2e_settings(tmp: Path, *, preset: str | None = None) -> Settings:
    cfg = load_settings()
    cfg.storage.root = str(tmp)
    cfg.storage.sessions_dir = str(tmp / "sessions")
    cfg.storage.workspaces_dir = str(tmp / "workspaces")
    cfg.storage.spill_dir = str(tmp / "spill")
    cfg.storage.db_path = str(tmp / "index.db")
    if preset is not None:
        cfg.security.policy.preset = preset      # 例:"standard" 让 exec.* 可达
    return cfg


@pytest.fixture
def adapter_factory():
    """注入脚本适配器，用例结束恢复全局注册表（防跨用例污染）。"""
    saved: list[dict] = []

    def _make(cfg: Settings, calls: list[dict] | None = None):
        if not saved:
            saved.append(dict(llm_mod.adapters))
        llm_mod.adapters.clear()
        ad = ScriptedAdapter(cfg.llm.model, calls)
        llm_mod.adapters[cfg.llm.model] = ad
        return ad

    yield _make
    if saved:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved[0])


class E2ESession:
    """一次 E2E 会话句柄：引擎 ctx + 落盘读回 + 事件查询。"""

    def __init__(self, tmp: Path, sid: str, ctx, adapter: ScriptedAdapter) -> None:
        self.tmp = tmp
        self.sid = sid
        self.ctx = ctx
        self.adapter = adapter
        self.last_task_id: str = ""

    # ------------------------------------------------------------ 事件视图
    def events(self) -> list:
        return list(self.ctx.session.events_after(0))

    def types(self) -> list[str]:
        return [e.type for e in self.events()]

    def of(self, type_: str) -> list:
        return [e for e in self.events() if e.type == type_]

    def count(self, type_: str) -> int:
        return len(self.of(type_))

    # ------------------------------------------------------------ 驱动
    async def boot(self, *, title: str = "") -> None:
        await self.ctx.session.append(
            "session.created",
            {"title": title, "model": self.ctx.settings.llm.model},
            actor="system", sync=True)

    async def run(self, intent: str, *, timeout: float = 25.0):
        await self.ctx.session.append("user.message", {"content": intent},
                                      actor="user", sync=True)
        q = TaskQueue(self.ctx.session, runner=self.ctx.task_runner, max_queue=8)
        tid = await q.submit(intent)
        self.last_task_id = tid
        return await asyncio.wait_for(q.wait_for(tid), timeout=timeout)

    # ------------------------------------------------------------ 持久化读回
    async def replay(self) -> list[str]:
        from pyharness.core.session import open_session
        from pyharness.persistence import open_store
        store = self.ctx.engine_spine.persistence
        flush = getattr(store, "flush", None)
        if callable(flush):
            r = flush()
            if hasattr(r, "__await__"):
                await r
        store2 = open_store(self.sid, dir=self.tmp / "sessions")
        log2 = await open_session(self.sid, store2)
        return [e.type for e in log2.events_after(0)]

    async def close(self) -> None:
        try:
            await self.ctx.engine_spine.close()
        except Exception:                        # noqa: BLE001 收尾尽力
            pass
        try:
            self.ctx.scope.release()
        except Exception:                        # noqa: BLE001 收尾尽力
            pass


@pytest.fixture
def authorize_call_sites():
    """AST 精确判定 ``ctx.governance.authorize(...)`` 的**生产调用点**。

    为什么不用 ``grep``:docstring/注释里出现 ``governance.authorize()`` 会被
    grep 误判为调用点(本仓库已实际发生一次)。AST 只看真实 Call 节点,且要求
    接收者是名为 ``governance`` 的属性 ⇒ 既不会漏,也不会被文档字符串骗过。
    """
    import ast as _ast

    def _scan(root: Path | None = None) -> list[str]:
        base = (root or Path(__file__).resolve().parents[1]) / "pyharness"
        out: set[str] = set()
        for p in sorted(base.rglob("*.py")):
            tree = _ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for n in _ast.walk(tree):
                if not isinstance(n, _ast.Call):
                    continue
                f = n.func
                if (isinstance(f, _ast.Attribute) and f.attr == "authorize"
                        and isinstance(f.value, _ast.Attribute)
                        and f.value.attr == "governance"):
                    out.add(str(p.relative_to(base.parent)).replace("\\", "/"))
        return sorted(out)

    return _scan


@pytest.fixture
async def e2e_factory(tmp_path, adapter_factory):
    """装配真实引擎并返回 E2ESession；用例结束自动 close。

    async 生成器夹具（``asyncio_mode=auto``）——收尾必须回到事件循环里执行
    （spine.close 要 await），不能靠同步 teardown 里再起 loop。
    """
    made: list[E2ESession] = []

    async def _make(calls=None, *, sid: str, channel="desktop",
                    preset: str | None = None):
        cfg = e2e_settings(tmp_path, preset=preset)
        ad = adapter_factory(cfg, calls)
        ctx = await assemble_real_engine(cfg, sid=sid,
                                         sessions_dir=tmp_path / "sessions",
                                         channel=channel)
        s = E2ESession(tmp_path, sid, ctx, ad)
        made.append(s)
        return s

    yield _make
    for s in made:
        await s.close()
