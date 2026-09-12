"""pyharness/core/kv.py — 会话级 KV 存储(storage.kv,#53 存储域最小落地)。

一句话职责:会话私有的小数据键值存取(get/set/delete/keys),JSON 文件落盘
(<sid>.kv.json,随会话目录;无 SQLite 依赖——通用小数据用文件足够,PRD 语义)。

设计:
- storage 域工具(strict 下 workspace 面可见,engine 已放行 storage.*);
- 单文件整体读写(值 ≤ 64KB 上限,防巨型值撑爆文件);set 覆盖幂等;
- 文件缺失 = 空表(首访自建);损坏 JSON 报 PERS-221(repair 语义,不静默清空);
- 事件溯源纪律:KV 是插件存储域(非会话日志),不进事件词表(与 DSH storage 域
  定位一致:通用小数据存取,审计走 fs 面)。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.kv")

MAX_VALUE_CHARS = 65536
TOOL_NAME = "storage.kv"
_OPS = ("get", "set", "delete", "keys")


class SessionKV:
    """会话级 KV(路径注入;lazy 载入,每次写后整体落盘)。"""

    def __init__(self, path: Path, session_id: str = "") -> None:
        self._path = Path(path)
        self._sid = session_id
        self._data: dict[str, str] = {}
        self._loaded = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 读写
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("KV 顶层必须是 JSON object")
            self._data = {k: str(v) for k, v in raw.items()}
        except (OSError, ValueError) as e:
            raise_code("PERS-221", module="kv", sid=self._sid,
                       hint=f"KV 文件损坏/不可读:{type(e).__name__};"
                            "用 repair 定位或删除该会话 kv 文件重建")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写:同目录临时文件 + os.replace(防半写)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                   prefix=".kv-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self._path)
        except OSError as e:
            raise_code("PERS-221", module="kv", sid=self._sid,
                       reason=type(e).__name__)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # ------------------------------------------------------------ 工具 op 面
    def apply_op(self, op: str, *, key: Optional[str] = None,
                 value: Optional[str] = None) -> str:
        """op ∈ get/set/delete/keys;返回用户可读文本(executor 关4 直用)。"""
        with self._lock:
            return self._apply_op_unlocked(op, key=key, value=value)

    def _apply_op_unlocked(self, op: str, *, key: Optional[str] = None,
                           value: Optional[str] = None) -> str:
        """锁内事务体:provider 在线程池并发调用时防丢失更新。"""
        if op not in _OPS:
            raise_code("EVT-100", hint=f"未知 storage.kv op: {op}",
                       advice=f"op ∈ {_OPS}")
        self._load()
        if op == "keys":
            ks = sorted(self._data)
            return "、".join(ks) if ks else "(空)"
        key = str(key or "").strip()
        if not key:
            raise_code("EVT-100", hint="storage.kv 缺 key 字段")
        if op == "get":
            v = self._data.get(key)
            return v if v is not None else f"(无此键: {key})"
        if op == "delete":
            existed = self._data.pop(key, None) is not None
            if existed:
                self._save()
            return f"已删除 {key}" if existed else f"(无此键: {key})"
        # set
        value = "" if value is None else str(value)
        if len(value) > MAX_VALUE_CHARS:
            raise_code("EVT-100", hint=f"KV 值超上限({MAX_VALUE_CHARS} 字符)")
        self._data[key] = value
        self._save()
        return f"已写入 {key}({len(value)} 字符)"


def register(registry: Any) -> list[str]:
    """五要素登记 + Provider 绑定(engine 装配面调用;Provider 经 ctx.storage.kv)。"""
    from pyharness.core.tools_registry import ToolDefinition
    registry.register_tool(ToolDefinition(
        name=TOOL_NAME, danger="none",
        description="会话级键值存取:set(key,value)/get(key)/delete(key)/keys;"
                    "跨轮持久(JSON 落盘),适合记偏好/中间结论等小数据",
        schema={"type": "object",
                "properties": {
                    "op": {"type": "string", "enum": list(_OPS)},
                    "key": {"type": "string", "maxLength": 256},
                    "value": {"type": "string", "maxLength": MAX_VALUE_CHARS}},
                "required": ["op"], "additionalProperties": False},
        timeout_s=10, owner="builtin", ctx_path="storage.kv", version="1.0.0"),
        provider=_KVHandle())
    return [TOOL_NAME]


class _KVHandle:
    """Provider handle:op 分派到 ctx.storage.kv(SessionKV)。"""

    def handle(self, args: dict, ctx: Any) -> str:
        kv = getattr(getattr(ctx, "storage", None), "kv", None)
        if kv is None:
            raise_code("CYC-999", module="storage.kv",
                       hint="ctx.storage.kv 未装配(引擎未激活 SessionKV),不可用")
        return kv.apply_op(args.get("op", ""), key=args.get("key"),
                           value=args.get("value"))


__all__ = ["SessionKV", "register", "TOOL_NAME", "MAX_VALUE_CHARS"]
