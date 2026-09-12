"""Real stdio MCP chain probe: initialize -> tools/list -> tools/call."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pyharness.core.mcp import McpClient, StdioTransport
from pyharness.core.tools_registry import ToolRegistry


async def main() -> int:
    server = ROOT / "scripts" / "mcp_echo_server.py"
    reg = ToolRegistry()
    client = McpClient("echo", StdioTransport(
        [sys.executable, str(server)], timeout_s=10))
    try:
        info = await client.connect()
        names = client.register_into(reg)
        provider = reg.lookup_provider("mcp.echo.echo")
        text = await provider.handle({"text": "mcp-real-chain-ok"}, None)
        print("server=", info.get("server"))
        print("tools=", names)
        print("call=", text)
        ok = text == "mcp-real-chain-ok" and names == ["mcp.echo.echo"]
        print("MCP_REAL_CHAIN_PASS" if ok else "MCP_REAL_CHAIN_FAIL")
        return 0 if ok else 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
