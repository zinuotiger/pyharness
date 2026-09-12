"""MCP 客户端单测 — 契约:core/mcp.py(#30,协议子集 + 工具桥)。

覆盖:握手(initialize→initialized→tools/list)、工具桥接进 core 注册表
(mcp.<name>.<tool> 前缀 + danger=high)、Provider 透传 tools/call 与文本回喂、
远端错误收敛 TLB-805、Stdio 传输最少面。全部走 InlineTransport,零网络。
"""
from __future__ import annotations

import sys

import pytest

from pyharness.core.mcp import (InlineTransport, McpClient, StdioTransport,
                                _mcp_env)
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError


def _transport(**responses) -> InlineTransport:
    return InlineTransport({
        "initialize": {"serverInfo": {"name": "fake", "version": "0.1"}},
        "tools/list": {"tools": [
            {"name": "add", "description": "加法",
             "inputSchema": {"type": "object",
                             "properties": {"a": {"type": "integer"},
                                            "b": {"type": "integer"}},
                             "required": ["a", "b"]}},
            {"name": "upper", "description": "大写"}]},
        **responses})


def test_stdio_env_allowlist_excludes_secrets(monkeypatch):
    """MCP stdio 子进程不能继承宿主密钥。"""
    monkeypatch.setenv("PYHARNESS_TEST_SECRET", "do-not-leak")
    env = _mcp_env()
    assert "PYHARNESS_TEST_SECRET" not in env
    assert "PATH" in env or "SYSTEMROOT" in env


@pytest.mark.asyncio
async def test_handshake_and_tool_bridge():
    tr = _transport()
    client = McpClient("calc", tr)
    meta = await client.connect()
    assert meta["tools"] == 2
    assert client.connected
    calls = [m for m, _ in tr.calls]
    assert calls == ["initialize", "notifications/initialized", "tools/list"]

    reg = ToolRegistry()
    names = client.register_into(reg)
    assert names == ["mcp.calc.add", "mcp.calc.upper"]
    defn = reg.lookup("mcp.calc.add")
    assert defn.danger == "high", "远端工具=代码执行面,必须转审批"
    await client.close()


@pytest.mark.asyncio
async def test_provider_call_roundtrip():
    tr = _transport()
    client = McpClient("calc", tr)
    await client.connect()
    reg = ToolRegistry()
    client.register_into(reg)
    # Provider 透传 tools/call(executor 同款解析:lookup_provider → handle)
    provider = reg.lookup_provider("mcp.calc.add")
    out = await provider.handle({"a": 1, "b": 2}, None)
    assert "默认文本响应" in out
    assert tr.calls[-1][0] == "tools/call"
    assert tr.calls[-1][1]["name"] == "add"


@pytest.mark.asyncio
async def test_remote_error_converges():
    tr = InlineTransport({
        "initialize": {"serverInfo": {"name": "s", "version": "0"}},
        "tools/list": {"tools": [{"name": "add", "description": "d"}]},
        "tools/call": {"content": [], "isError": True}})
    client = McpClient("calc", tr)
    await client.connect()
    reg = ToolRegistry()
    client.register_into(reg)
    provider = reg.lookup_provider("mcp.calc.add")
    with pytest.raises(PyHError) as e:
        await provider.handle({"a": 1}, None)
    assert e.value.code == "TLB-805"


@pytest.mark.asyncio
async def test_provider_text_content():
    tr = InlineTransport({
        "initialize": {"serverInfo": {"name": "s", "version": "0"}},
        "tools/list": {"tools": [{"name": "hi", "description": "d"}]},
        "tools/call": {"content": [{"type": "text", "text": "你好 MCP"}],
                       "isError": False}})
    client = McpClient("demo", tr)
    await client.connect()
    reg = ToolRegistry()
    client.register_into(reg)
    provider = reg.lookup_provider("mcp.demo.hi")
    out = await provider.handle({}, None)
    assert "你好 MCP" in out


@pytest.mark.asyncio
async def test_stdio_transport_real_subprocess_notification_and_timeout_path():
    """真实 stdio 子进程可握手;notification 不应错误等待响应。"""
    server = r'''
import json, sys
for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        result = {"serverInfo": {"name": "local", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "echo"}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "ok"}], "isError": False}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}), flush=True)
'''
    tr = StdioTransport([sys.executable, "-u", "-c", server], timeout_s=5)
    client = McpClient("local", tr)
    await client.connect()
    assert await client.call_tool("echo", {}) == {
        "content": [{"type": "text", "text": "ok"}], "isError": False}
    await client.close()


def test_register_into_rolls_back_on_bad_schema():
    """远端工具批量注册必须事务化:后续 schema 失败时撤销前面已注册工具。"""
    client = McpClient("bad", InlineTransport())
    client._tools = [
        {"name": "good", "description": "ok",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "broken", "description": "bad",
         "inputSchema": {"type": "array"}},
    ]
    reg = ToolRegistry()
    with pytest.raises(PyHError):
        client.register_into(reg)
    with pytest.raises(PyHError):
        reg.lookup("mcp.bad.good")
