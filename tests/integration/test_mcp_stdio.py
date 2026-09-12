from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pyharness.core.mcp import McpClient, StdioTransport
from pyharness.core.tools_registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_mcp_stdio_real_process_chain():
    server = ROOT / "scripts" / "mcp_echo_server.py"
    client = McpClient("echo", StdioTransport(
        [sys.executable, str(server)], timeout_s=10))
    reg = ToolRegistry()
    try:
        info = await client.connect()
        assert info["server"]["name"] == "echo"
        assert client.register_into(reg) == ["mcp.echo.echo"]
        provider = reg.lookup_provider("mcp.echo.echo")
        assert await provider.handle({"text": "ok"}, None) == "ok"
    finally:
        await client.close()
