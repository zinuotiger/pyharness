"""Acp protocol: complete migrated implementation."""
from __future__ import annotations
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Optional, TextIO, Union
from pyharness.errors import PyHError


log = logging.getLogger("pyharness.acp")

PROTOCOL_VERSION = "1.0"

BRIDGE_METHODS: list[str] = ["initialize", "chat", "approve",
                             "read_events", "shutdown"]

MAX_EVENT_LIMIT = 500          # read_events 单批上限(锁死)

DEFAULT_EVENT_LIMIT = 200      # read_events 缺省批量

_HOT_CODES = frozenset({"GRD-401", "GRD-403", "LLM-310", "PERS-202"})

_SENSITIVE_KEY_HINTS = ("secret", "token", "apikey", "api_key", "password",
                        "credential", "private", "key")

@dataclass
class JsonRpcRequest:
    """入站请求:jsonrpc 恒 "2.0";id 缺失/None = 通知(不回响应)。"""

    jsonrpc: str = "2.0"
    id: Optional[Union[int, str]] = None
    method: str = ""
    params: dict = field(default_factory=dict)

@dataclass
class AcpState:
    """连接会话态(桥进程内):cursor = 已投递事件最大 seq(游标订阅只进不退)。

    _bound_log 为实现附加(偏离 10:会话同柄缓存,非 spec 字段);bridge_ref 记装配
    引用(ctx/shell),当前未消费,保留字段供上层审计。
    """

    client_id: str
    session_id: Optional[str] = None
    cursor: int = 0
    busy: bool = False
    bridge_ref: Any = None
    notify_shutdown: bool = False          # shutdown 请求/通知 → 净退旗标
    _bound_log: Any = field(default=None, repr=False)

def _write_line(out: Optional[TextIO], obj: dict) -> None:
    """协议行写 stdout:每次写+flush(双工实时);buffer 优先(UTF-8 字节)。"""
    stream = out if out is not None else sys.stdout
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    buf = getattr(stream, "buffer", None)
    if buf is not None:                    # 真实 stdout:UTF-8 字节,免控制台编码坑
        buf.write(line.encode("utf-8"))
        buf.flush()
    else:                                  # StringIO 等注入替身:文本直写
        stream.write(line)
        stream.flush()

async def _readline_async(stream: Any) -> Optional[str]:
    """异步逐行读 stdin(to_thread 让出事件循环,审批 TTL 等定时器不饿死)。"""

    def _read() -> str:
        try:
            return stream.readline()
        except (ValueError, OSError):
            return ""                       # 流损坏按 EOF 处理(serve 记日志退 1)

    line = await asyncio.to_thread(_read)
    if not line:                            # EOF(上游关闭管道)→ 净退
        return None
    return line

def error_resp(rid: Optional[Union[int, str]], code: int, message: str,
               data: Optional[dict] = None) -> dict:
    """JSON-RPC 错误响应(标准码直构;data 可选)。"""
    body: dict[str, Any] = {"code": code, "message": message}
    if data:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": body}

def _redact(ctx: dict) -> dict:
    """ctx 脱敏(INV-09):疑似敏感键打码,值截断 200 字符,嵌套只留类型标记。"""
    out: dict[str, Any] = {}
    for k, v in (ctx or {}).items():
        if any(hint in str(k).lower() for hint in _SENSITIVE_KEY_HINTS):
            out[str(k)] = "[redacted]"
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[str(k)] = str(v)[:200] if isinstance(v, str) else v
        else:
            out[str(k)] = f"<{type(v).__name__}:{len(v)}>" if hasattr(
                v, "__len__") else f"<{type(v).__name__}>"
    return out

def engine_error(rid: Optional[Union[int, str]], e: PyHError) -> dict:
    """引擎错 → -32603 壳 + data={code,message,advice}(ADR-011 远端只回 code+advice)。

    热路径码(GRD-401/403/LLM-310/PERS-202)不加 detail;其余码的 ctx 经 _redact
    后才进 detail——远端绝不见 ctx 敏感值/密钥原文。
    """
    body: dict[str, Any] = {"code": e.code, "message": e.message,
                            "advice": e.spec.advice}
    if e.code not in _HOT_CODES:
        body["detail"] = _redact(e.ctx or {})
    return error_resp(rid, -32603, e.message, body)

def _is_id_type(v: Any) -> bool:
    """JSON-RPC id 合法型:int|str(禁 bool——bool 是 int 子类,非合法 id)。"""
    return isinstance(v, (int, str)) and not isinstance(v, bool)

def parse_request(line: str,
                  *, write: Optional[TextIO] = None) -> Optional[JsonRpcRequest]:
    """行解析(协议错 → 标准码响应就地写出并返回 None,调用方 continue)。

    -32700:非法 JSON;-32600:信封非法(非对象/jsonrpc 版本/method 类型/
    id 类型);-32602:params 非对象。id 缺失/None = 通知(id=None)。
    """
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as e:
        _write_line(write, error_resp(None, -32700, "Parse error",
                                      {"detail": str(e)[:300]}))
        return None
    if (not isinstance(raw, dict)
            or raw.get("jsonrpc") != "2.0"
            or not isinstance(raw.get("method"), str)
            or not raw.get("method")
            or ("id" in raw and raw["id"] is not None
                and not _is_id_type(raw["id"]))):
        rid = raw.get("id") if isinstance(raw, dict) and _is_id_type(
            raw.get("id")) else None
        _write_line(write, error_resp(rid, -32600, "Invalid Request"))
        return None
    params = raw.get("params")
    if params is not None and not isinstance(params, dict):
        _write_line(write, error_resp(raw.get("id"), -32602,
                                      "Invalid params"))
        return None
    return JsonRpcRequest(jsonrpc="2.0", id=raw.get("id"),
                          method=raw["method"], params=params or {})

# Preserve historical import and pickle identities through the compatibility facade.
JsonRpcRequest.__module__ = 'pyharness.acp'
AcpState.__module__ = 'pyharness.acp'
