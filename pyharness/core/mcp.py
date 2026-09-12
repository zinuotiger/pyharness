"""pyharness/core/mcp.py — MCP 客户端最小实现(#30 Consumer 面)。

契约(MCP 2024-11-05 子集):initialize/notifications/initialized → tools/list →
tools/call(JSON-RPC 2.0,content 数组)。传输抽象:stdio(本地 server 进程)与
http(OpenAI 兼容 HTTP+JSON-RPC 端点)可注入(测试用 InlineTransport 脚本化)。

工具桥:list 返回的工具逐个编译为 ToolDefinition(name=mcp.<tool> 防命名冲突,
danger=high——MCP 工具是远端代码执行面,一律转审批)+ Provider 桥(参数透传
tools/call,响应 content[0].text 回喂)。strict 域(mcp.*)不可见,演示用 standard。

真链边界:真实 MCP server(filesystem/git 等)需外部进程,装配层挂载留 P3 探针;
本文件 = 协议 + 桥 + 注入式测试(零外部依赖)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.mcp")

DEFAULT_TIMEOUT_S = 30

_MCP_ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_ARCHITEW6432", "OS", "PYTHONIOENCODING", "PYTHONUTF8",
)


def _mcp_env() -> dict[str, str]:
    """MCP 子进程最小环境,避免默认继承宿主 API key/凭据。"""
    return {k: os.environ[k] for k in _MCP_ENV_ALLOWLIST if k in os.environ}


# ------------------------------------------------------------------ 传输
class Transport:
    """请求/响应传输基类(JSON-RPC 信封外)。"""

    async def request(self, method: str, params: dict) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class StdioTransport(Transport):
    """本地 MCP server 进程(stdio JSON-RPC;Windows 隐藏窗口)。"""

    def __init__(self, argv: list, *, timeout_s: int = DEFAULT_TIMEOUT_S) -> None:
        self._argv = list(argv)
        self._timeout = timeout_s
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._seq = 0
        self._stderr_task: Optional[asyncio.Task] = None
        self._request_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()

    async def _ensure(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=_mcp_env(), creationflags=flags)
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            log.debug("mcp stderr: %s", line.decode("utf-8", errors="replace").rstrip())

    async def request(self, method: str, params: dict) -> Any:
        timeout = False
        async with self._request_lock:
            await self._ensure()
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            self._seq += 1
            req_id = self._seq
            envelope = json.dumps({"jsonrpc": "2.0", "id": req_id,
                                   "method": method, "params": params},
                                  ensure_ascii=False)
            proc.stdin.write((envelope + "\n").encode("utf-8"))
            await proc.stdin.drain()
            if method.startswith("notifications/"):
                return None                     # JSON-RPC 通知无响应
            try:
                resp = await asyncio.wait_for(proc.stdout.readline(),
                                              timeout=self._timeout)
            except asyncio.TimeoutError:
                timeout = True
                data = {}
            else:
                if not resp:
                    raise_code("TLB-805", module="mcp", method=method,
                               hint="MCP server 已退出或关闭 stdout")
                try:
                    data = json.loads(resp.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise_code("TLB-805", module="mcp", method=method,
                               hint=f"MCP 返回非 JSON:{type(exc).__name__}")
        if timeout:
            await self.close()
            raise_code("TLB-805", module="mcp", method=method,
                       hint=f"MCP 请求超时(>{self._timeout}s),传输已关闭")
        if data.get("id") != req_id:
            raise_code("TLB-805", module="mcp", method=method,
                       hint=f"MCP 响应 id 不匹配:期望 {req_id},收到 {data.get('id')}")
        if "error" in data:
            raise_code("TLB-805", module="mcp",
                       hint=f"{method} 远端错误:{data['error']}")
        return data.get("result")

    async def close(self) -> None:
        async with self._close_lock:
            proc = self._proc
            self._proc = None
            if proc is None:
                return
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
                try:
                    await proc.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except (ProcessLookupError, asyncio.TimeoutError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
            if self._stderr_task is not None:
                self._stderr_task.cancel()
                try:
                    await self._stderr_task
                except asyncio.CancelledError:
                    self._stderr_task = None
                    raise
                except Exception:                # noqa: BLE001 stderr 排空失败不阻断
                    pass
                self._stderr_task = None


class InlineTransport(Transport):
    """脚本化传输(单测/探针:预置 responses 与调用记录,零网络)。"""

    def __init__(self, responses: Optional[dict] = None) -> None:
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, dict]] = []

    async def request(self, method: str, params: dict) -> Any:
        self.calls.append((method, params))
        if method in self.responses:
            return self.responses[method]
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "(默认文本响应)"}],
                    "isError": False}
        if method.startswith("notifications/"):
            return {"ok": True}                # 通知无响应信封
        raise_code("TLB-805", module="mcp", hint=f"未脚本化方法 {method}")


# ------------------------------------------------------------------ 客户端
class McpClient:
    """MCP 客户端:初始化握手 → tools/list → 工具桥(定义+Provider)。"""

    def __init__(self, name: str, transport: Transport, *,
                 tool_reg: Any = None, version: str = "1.0.0") -> None:
        self.name = name                       # server 名(工具前缀 mcp.<name>.*)
        self.transport = transport
        self.tool_reg = tool_reg
        self.version = version
        self.connected = False
        self._tools: list[dict] = []

    async def connect(self) -> dict:
        """MCP 握手:initialize → notifications/initialized → tools/list。"""
        init = await self.transport.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "pyharness", "version": self.version}})
        await self.transport.request("notifications/initialized", {})
        listed = await self.transport.request("tools/list", {})
        self._tools = list(listed.get("tools") or [])
        self.connected = True
        return {"server": (init or {}).get("serverInfo"),
                "tools": len(self._tools)}

    def register_into(self, tool_reg: Any) -> list[str]:
        """list 出的工具桥接进 core ToolRegistry(mcp.<name>.<tool> 前缀)。

        danger=high:远端工具=代码执行面,一律走审批;返回注册名清单。"""
        from pyharness.core.tools_registry import ToolDefinition
        registered: list[str] = []
        try:
            for t in self._tools:
                tool_name = f"mcp.{self.name}.{t.get('name')}"
                schema = t.get("inputSchema") or {"type": "object",
                                                  "properties": {}}
                tool_reg.register_tool(
                    ToolDefinition(name=tool_name, danger="high",
                                   description=(t.get("description")
                                                or f"MCP 远端工具 {tool_name}")[:190],
                                   schema=schema, timeout_s=DEFAULT_TIMEOUT_S,
                                   owner=f"mcp:{self.name}", ctx_path="mcp",
                                   version="1.0.0"),
                    provider=_McpProvider(self, t.get("name")))
                registered.append(tool_name)
        except Exception:
            for name in reversed(registered):
                try:
                    tool_reg.unregister_definition(name)
                except Exception:                # noqa: BLE001 回滚尽力
                    log.debug("mcp rollback unregister miss %s", name)
            raise
        self.tool_reg = tool_reg
        return registered

    async def call_tool(self, tool: str, args: dict) -> Any:
        """tools/call 透传(Provider 桥消费)。"""
        return await self.transport.request("tools/call",
                                            {"name": tool,
                                             "arguments": dict(args or {})})

    async def close(self) -> None:
        await self.transport.close()
        self.connected = False


class _McpProvider:
    """Provider handle:远端 tools/call 桥,响应 content[0].text 回喂。"""

    def __init__(self, client: McpClient, tool: str) -> None:
        self._client = client
        self._tool = tool

    async def handle(self, args: dict, ctx: Any) -> str:
        result = await self._client.call_tool(self._tool, args)
        if result.get("isError"):
            raise_code("TLB-805", module="mcp", tool=self._tool,
                       hint="远端执行错误")
        parts = []
        for item in result.get("content") or []:
            txt = item.get("text")
            if txt:
                parts.append(str(txt))
        return "\n".join(parts) if parts else "(无文本内容)"


__all__ = ["McpClient", "StdioTransport", "InlineTransport", "Transport"]
