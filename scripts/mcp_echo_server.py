"""Minimal MCP stdio echo server for the repository's real-chain probe."""
from __future__ import annotations

import json
import sys


def _reply(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result or {}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        req_id = req.get("id")
        if method.startswith("notifications/"):
            continue
        if method == "initialize":
            _reply(req_id, {"protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "echo", "version": "1.0.0"}})
        elif method == "tools/list":
            _reply(req_id, {"tools": [{
                "name": "echo", "description": "Echo the input text",
                "inputSchema": {"type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"]}}]})
        elif method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            _reply(req_id, {"content": [{"type": "text",
                                         "text": str(args.get("text", ""))}],
                            "isError": False})
        else:
            _reply(req_id, error={"code": -32601, "message": "method not found"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
