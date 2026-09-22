"""安全:LLM 出网治理(GAP-1)——允许 / 拒绝 / 异常三态。

边界说明(为什么在这里断言):LLM 出网**不是工具调用**,不适用 g1–g7;适用面是
**会话级预算硬闸**。统一闸位于 ``LLMClient._chat_any`` → ``_egress_guard``,
五个公开出口(chat / chat_stream / mini / summarize / json_chat)全部经此。

强证据口径:"被拒"必须表现为**适配器零调用**(出网未发生),而不是只看异常类型。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyharness.core import llm as llm_mod
from pyharness.core.llm import LLMClient, LLMResponse
from pyharness.core.scope import BudgetExhausted


class CountingAdapter:
    """适配器替身:**计数出网次数**——"零出网"的强证据。"""

    def __init__(self, model: str = "test-model") -> None:
        self.model = model
        self.calls: list[str] = []

    def _resp(self) -> LLMResponse:
        return LLMResponse(content='{"ok": true}', tool_calls=None,
                           usage=SimpleNamespace(prompt_tokens=1,
                                                 completion_tokens=1,
                                                 prompt_cache_hit_tokens=0,
                                                 prompt_cache_miss_tokens=1),
                           model=self.model, finish_reason="stop")

    async def chat(self, messages, tools=None, *, ctx):
        self.calls.append("chat")
        return self._resp()

    async def chat_stream(self, messages, tools=None, *, ctx):
        self.calls.append("chat_stream")
        return self._resp()

    async def ping(self) -> float:
        self.calls.append("ping")
        return 0.01


class BudgetScope:
    """scope 替身:按构造参数决定 check_budget 放行还是抛。"""

    def __init__(self, state: str = "ok") -> None:
        self.state = state

    def check_budget(self):
        if self.state in ("paused", "exhausted"):
            raise BudgetExhausted(f"预算{self.state}")
        return self.state


@pytest.fixture
def llm_env():
    """注册计数适配器,返回 (client, adapter);用例结束恢复注册表。"""
    saved = dict(llm_mod.adapters)
    ad = CountingAdapter()
    llm_mod.adapters.clear()
    llm_mod.adapters[ad.model] = ad
    client = LLMClient(model=ad.model, registry=llm_mod.adapters)
    try:
        yield client, ad
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


# ------------------------------------------------------------------ 允许
async def test_allowed_egress_reaches_adapter(llm_env):
    """三态 ①:预算 ok ⇒ 五个出口**全部**真的出网。"""
    client, ad = llm_env
    ctx = SimpleNamespace(scope=BudgetScope("ok"))
    await client.chat([{"role": "user", "content": "hi"}], ctx=ctx)
    await client.chat_stream([{"role": "user", "content": "hi"}], ctx=ctx)
    await client.mini("t", ctx=ctx)
    await client.summarize("s", ctx=ctx)
    await client.json_chat("只回 JSON: {\"a\":1}", ctx=ctx)
    assert ad.calls.count("chat") == 4      # chat/chat_stream/mini/summarize/json_chat
    assert ad.calls.count("chat_stream") == 1
    assert len(ad.calls) == 5


# ------------------------------------------------------------------ 拒绝
@pytest.mark.parametrize("state", ["paused", "exhausted"])
@pytest.mark.parametrize("exit_", ["chat", "chat_stream", "mini",
                                   "summarize", "json_chat"])
async def test_denied_egress_never_reaches_adapter(llm_env, state, exit_):
    """三态 ②:预算 paused/exhausted ⇒ 五个出口**全部**零出网。

    修复前 ``mini`` / ``summarize`` / ``json_chat`` 可绕过该闸(它只在轮起点)。
    """
    client, ad = llm_env
    ctx = SimpleNamespace(scope=BudgetScope(state))
    with pytest.raises(BudgetExhausted):
        if exit_ in ("chat", "chat_stream"):
            await getattr(client, exit_)([{"role": "user", "content": "x"}],
                                         ctx=ctx)
        elif exit_ == "json_chat":
            await client.json_chat("只回 JSON", ctx=ctx)
        else:                                   # mini / summarize
            await getattr(client, exit_)("x", ctx=ctx)
    assert ad.calls == [], f"{exit_} 在预算 {state} 时仍出网: {ad.calls}"


async def test_denied_streak_is_stable(llm_env):
    """拒绝是**确定性**的:反复调用仍零出网(不是偶发拦截)。"""
    client, ad = llm_env
    ctx = SimpleNamespace(scope=BudgetScope("exhausted"))
    for _ in range(5):
        with pytest.raises(BudgetExhausted):
            await client.mini("t", ctx=ctx)
    assert ad.calls == []


# ------------------------------------------------------------------ 异常/边界
async def test_scope_absent_degrades_open_without_crashing(llm_env):
    """三态 ③:scope 缺失(轻装配/单测)⇒ 降级放行,不抛。

    这是**有意的**取舍并已记录:预算闸是配额面,缺配额不等于越权。
    """
    client, ad = llm_env
    ctx = SimpleNamespace()                 # 无 scope
    await client.mini("t", ctx=ctx)
    assert ad.calls == ["chat"]


async def test_ctx_none_degrades_open(llm_env):
    """ctx 为 None 同样降级放行(与 scope 缺失同源判定)。"""
    client, ad = llm_env
    await client.mini("t", ctx=None)
    assert ad.calls == ["chat"]


async def test_adapter_without_check_budget_is_tolerated(llm_env):
    """scope 存在但无 ``check_budget``(第三方/阶段装配)⇒ 视为放行,不 AttributeError。"""
    client, ad = llm_env
    ctx = SimpleNamespace(scope=SimpleNamespace())
    await client.mini("t", ctx=ctx)
    assert ad.calls == ["chat"]


# ------------------------------------------------- 工具治理链未被本改动破坏
async def test_tool_governance_chain_untouched(llm_env, authorize_call_sites):
    """GAP-1 不得改动工具治理链:``governance.authorize()`` 调用点仍**恰好 1 处**。

    用 **AST** 判定而非 grep —— docstring 里提到该符号不算调用点(本文件所在
    仓库已实际踩过这个坑)。
    """
    sites = authorize_call_sites()
    assert sites == ["pyharness/core/tools_executor.py"], \
        f"governance.authorize() 调用点被改动: {sites}"
