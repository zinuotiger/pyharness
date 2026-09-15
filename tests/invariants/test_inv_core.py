"""tests/invariants/test_inv_core.py — 核心不变量:INV-02(无绕过 agent-loop 直调 llm)。

冻结依据:`docs/INVARIANT_REGISTRY.md`(Canonical INV-02)· `docs/PRD-Core.md:838,634`
· `docs/CONSTRAINTS-06-Testing.md:63` · `docs/DIS-CORE.md:186`
· `docs/PRD-Core.md:1251`(F042 指定出口 = `ctx.llm.mini`)。

来源:本文件由 S6-2a-P0-F(KF-A 修复)建立,是 INV-02 的**正式不变量测试资产**;
S6-2b 将在**同一文件**扩展 INV-01 / INV-03~09 的编号化用例(不另建第二套)。

边界:静态扫描对象 = 运行时包 `pyharness/`。`scripts/` 下的人工探针(e2e / probe)
不参与装配、不被 `pyharness` import,不在 INV-02 的运行时边界内。

**类别式边界**(非例外名单):`chat`/`chat_stream` = Agent Loop **对话出口**(唯一合法
调用方 = `agent_loop`);`mini`/`summarize`/`json_chat` = **System Tool LLM 出口**,
不受 INV-02 的调用方约束,但其成员由 `LLMClient` 公开面显式枚举。
"""
from __future__ import annotations

import ast
import pathlib
import types

import pytest

pytestmark = pytest.mark.invariant

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PKG = _ROOT / "pyharness"

# Agent Loop 对话出口 —— INV-02 点名的两个方法
_CONVERSATION_ENTRIES = ("chat", "chat_stream")


def _iter_sources():
    for p in sorted(_PKG.rglob("*.py")):
        yield p, p.read_text(encoding="utf-8")


# ================================================================ INV-02 · 静态面
def test_inv02_no_direct_conversation_entry_call():
    """INV-02(静态):包内**无任何**直接 `.chat(` / `.chat_stream(` 属性调用。

    agent-loop 经 `getattr(ctx.llm, chat_fn)` 动态派发(见下一条),故任何**直接**
    属性调用都等价于"非循环调用方" —— 正是 KF-A 的违约形态
    (修复前 `auto_title.py:36` 直调 `ctx.llm.chat`;见 `S6-2a-P0_CHANGE_REPORT.md`)。
    """
    offenders = []
    for p, src in _iter_sources():
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in _CONVERSATION_ENTRIES):
                offenders.append(f"{p.relative_to(_ROOT).as_posix()}:{n.lineno}"
                                 f" .{n.func.attr}()")
    assert not offenders, (
        "INV-02 违约:出现绕过 agent-loop 的对话出口直调 -> " + ", ".join(offenders))


def test_inv02_loop_is_the_only_dynamic_entry_dispatcher():
    """INV-02(静态):`getattr(ctx.llm, <method>)` 动态派发**仅**允许在 agent_loop 内。

    这是 agent-loop 调用对话出口的真实形态(`agent_loop.py:240-241`),也是
    "唯一合法调用方 = agent-loop"这一表述的**可验证实现形式**。
    """
    sites = []
    for p, src in _iter_sources():
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "getattr" and n.args
                    and isinstance(n.args[0], ast.Attribute)
                    and n.args[0].attr == "llm"):
                sites.append(f"{p.relative_to(_ROOT).as_posix()}:{n.lineno}")
    assert sites == ["pyharness/core/agent_loop.py:241"], (
        "INV-02 违约:llm 门面的动态派发点不在 agent_loop 唯一位置 -> " + str(sites))


def test_inv02_mini_is_system_tool_entry_not_chat_wrapper():
    """INV-02(类别边界):`mini` 属 System Tool 出口,不得是 `self.chat` 的换名包装。

    静态核验 `LLMClient.mini` 的函数体:必须经 `_chat_any` 复用既有链路(与
    `summarize`/`json_chat` 同类,单一真源),**不得**调用 `chat`/`chat_stream`。
    """
    tree = ast.parse((_PKG / "core" / "llm.py").read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "mini"), None)
    assert fn is not None, "LLMClient.mini 缺失(System Tool LLM 出口未实现)"
    called = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Attribute):
                called.add(n.func.attr)
            elif isinstance(n.func, ast.Name):
                called.add(n.func.id)
    assert "_chat_any" in called, "mini 必须经 _chat_any 复用既有链路(单一真源)"
    overlap = {"chat", "chat_stream"} & called
    assert not overlap, f"mini 被实现为 chat 的换名包装 -> {sorted(overlap)}"


# ================================================================ INV-02 · 行为面
class _RecordingLLM:
    """记录型门面替身:统计各出口被调次数;对话出口被调即断言失败。"""

    def __init__(self, text: str = "会话标题"):
        self.calls = {"mini": 0, "chat": 0, "chat_stream": 0}
        self.prompts: list[str] = []
        self._text = text

    async def mini(self, prompt, *, ctx):
        self.calls["mini"] += 1
        self.prompts.append(prompt)
        return self._text

    async def chat(self, messages, tools=None, *, ctx):
        self.calls["chat"] += 1
        raise AssertionError("auto_title 不得调用 chat(INV-02:仅 agent-loop 可调)")

    async def chat_stream(self, messages, tools=None, *, ctx):
        self.calls["chat_stream"] += 1
        raise AssertionError("auto_title 不得调用 chat_stream(INV-02)")


class _FakeSession:
    """最小会话替身:`events_after` / `append` 足以驱动 auto_title。"""

    def __init__(self, first: str = "帮我整理这个文件夹"):
        self.events = [types.SimpleNamespace(type="user.message",
                                             payload={"content": first}),
                       types.SimpleNamespace(type="agent.message",
                                             payload={"content": "好"})]
        self.appended: list[tuple] = []

    def events_after(self, seq):
        return list(self.events) if seq == 0 else []

    async def append(self, type_, payload, actor=None, **kw):
        self.appended.append((type_, payload, actor))
        self.events.append(types.SimpleNamespace(type=type_, payload=payload))
        return types.SimpleNamespace(seq=len(self.events))


async def test_inv02_auto_title_uses_mini_and_never_chat():
    """INV-02(行为):auto_title 走 System Tool 出口 ⇒ `mini` 恰 1 次、`chat` 0 次。"""
    from pyharness.core.auto_title import auto_title

    llm = _RecordingLLM("整理文件夹笔记")
    sess = _FakeSession()
    ctx = types.SimpleNamespace(llm=llm, session=sess)

    title = await auto_title(ctx)

    assert title == "整理文件夹笔记"
    assert llm.calls["mini"] == 1, f"mini 调用次数应为 1 -> {llm.calls}"
    assert llm.calls["chat"] == 0, f"chat 调用次数应为 0 -> {llm.calls}"
    assert llm.calls["chat_stream"] == 0
    assert sess.appended == [("session.renamed",
                              {"new_title": "整理文件夹笔记", "by": "auto"},
                              "system")]
    assert isinstance(llm.prompts[0], str)   # 单 prompt 契约(非 messages 列表)


async def test_inv02_auto_title_idempotent_skips_llm():
    """INV-02 / F042:日志已有 `session.renamed` ⇒ 幂等返回 None 且**不再调 LLM**。"""
    from pyharness.core.auto_title import auto_title

    llm = _RecordingLLM()
    sess = _FakeSession()
    sess.events.append(types.SimpleNamespace(type="session.renamed",
                                             payload={"new_title": "旧标题"}))
    ctx = types.SimpleNamespace(llm=llm, session=sess)

    assert await auto_title(ctx) is None
    assert llm.calls["mini"] == 0 and llm.calls["chat"] == 0
    assert sess.appended == []
