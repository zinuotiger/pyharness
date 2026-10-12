"""Timeline and SSE projection helpers."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from pyharness.core.tenant_settings import (event_tenant_allowed,
                                            event_tenant_of)
from pyharness.events import Envelope

from pyharness.application.event_projection import (TimelineNode, _env_dict, _jsonable, _redact_dict, _redact_value, _trunc, approval_node, derive_timeline, redact, redact_args, render_timeline_node)

log = logging.getLogger("pyharness.desktop.projection")



@dataclass(eq=False)
class StreamClient:
    """一个 SSE 连接的订阅游标:重连后前端带 last_seq 续拉补发(F065 边界)。

    eq=False:dataclass 默认按字段生成 __eq__ 会使 __hash__ 置 None(dataclass 纪律),
    而本类要进 hub.clients:set 做成员去重/遍历——须保留对象同一性哈希。
    """

    sid: str = ""                         # 目标会话;"" = 全部会话
    tenant: str = "default"               # 租户过滤键
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    last_seq: int = 0                     # 已投递最大 seq(断线续拉游标)
    dead: bool = False                    # hub.close_all 置位 → 消费端净退


class _HubSession:
    """register(client) 的异步上下文:进入注册、退出注销(含 closing 中断)。"""

    def __init__(self, hub: "EventStreamHub", client: StreamClient) -> None:
        self._hub = hub
        self._client = client

    async def __aenter__(self) -> StreamClient:
        self._hub.clients.add(self._client)
        return self._client

    async def __aexit__(self, *exc: Any) -> None:
        self._hub.clients.discard(self._client)


class EventStreamHub:
    """总线 → SSE fan-out:写锁保证 seq 顺序广播(EVT-103 由总线隔离订阅者异常)。

    forward(type_, payload) 作为 EventBus 订阅者被调用:Envelope(日志分发,payload=信封)
    按会话过滤投递并推进游标;裸 dict(瞬时 llm.chunk 直推)广播给全部客户端(流式碎片刻
    画在哪个会话由前端按自身 task 上下文过滤)。瞬时事件天然不入日志 → 时间线只取持久。
    """

    def __init__(self) -> None:
        self.clients: set[StreamClient] = set()
        self._seq_locks: dict[str, asyncio.Lock] = {}   # per-session 写锁(含 "_all")

    def register(self, client: StreamClient) -> _HubSession:
        """注册订阅游标(SSE 连接建立时);退出上下文即注销。"""
        return _HubSession(self, client)

    def _lock_for(self, sid: str) -> asyncio.Lock:
        """懒建会话写锁(forward 恒在事件循环内执行,Lock 可安全创建)。"""
        return self._seq_locks.setdefault(sid or "_all", asyncio.Lock())

    async def forward(self, type_: str, payload: Any) -> None:
        """总线订阅者:按 seq 序把事件扇出到匹配客户端队列(队列无界不阻塞总线)。"""
        if not _has_envelope_scope(payload):
            sid, seq = None, None           # 无会话作用域:兼容旧广播
        else:
            sid = _env_session_id(payload)  # 持久/带作用域瞬时事件按会话过滤
            seq = getattr(payload, "seq", None)
        text = _serialize_event(type_, payload)
        # 租户归属:优先**落盘信封**(GAP-10,免疫重启/跨租户 sid 撞名),回落进程内映射
        event_tenant = event_tenant_of(payload, session_id=sid or "")
        async with self._lock_for(sid):
            for c in tuple(self.clients):
                if c.dead:
                    continue
                if not event_tenant_allowed(c.tenant, event_tenant):
                    continue                # 租户过滤:禁止跨租户事件泄漏(单点判据)
                if c.sid and sid and c.sid != sid:
                    continue                # 会话过滤:只投本会话客户端
                c.queue.put_nowait((type_, text))
                if seq is not None and isinstance(seq, int) and seq > c.last_seq:
                    c.last_seq = seq        # 游标只进不退(断线续拉用)

    def close_all(self) -> None:
        """优雅停服:全部客户端置 dead 并投 None 哨兵 → SSE 生成器净退。"""
        for c in tuple(self.clients):
            c.dead = True
            try:
                c.queue.put_nowait(None)
            except Exception:               # noqa: BLE001 队列已关等本地异常
                log.debug("SSE client queue already closed", exc_info=True)
        self.clients.clear()


# ============================================================ 事件工具(鸭子)
def _has_envelope_scope(payload: Any) -> bool:
    """信封鸭子判定:带 session_id/seq/type 的对象视为持久信封(否则裸瞬时载荷)。"""
    return hasattr(payload, "session_id") or (
        isinstance(payload, dict) and payload.get("session_id"))


def _env_session_id(env: Any) -> Optional[str]:
    """信封 → session_id(鸭子;裸 dict 也兼容)。"""
    sid = getattr(env, "session_id", None)
    if sid is None and isinstance(env, dict):
        sid = env.get("session_id")
    return str(sid) if sid else None




def _serialize_event(type_: str, payload: Any) -> str:
    """事件 → SSE data 行 JSON(ensure_ascii=False,紧凑;中文原样直读)。"""
    if callable(getattr(payload, "model_dump", None)):
        body = _env_dict(payload)
    elif isinstance(payload, dict):
        if "seq" in payload or "type" in payload:
            body = dict(payload)
            body.setdefault("type", type_)
        else:
            meta = {k: payload[k] for k in ("session_id", "task_id")
                    if payload.get(k) is not None}
            inner = {k: v for k, v in payload.items() if k not in meta}
            body = {"type": type_, **meta, "payload": inner}
    else:
        body = {"type": type_, "payload": dict(payload or {})}
    if type_ == 'llm.error' or (type_ == 'system.error' and
            str(body.get('payload', {}).get('code', '')).startswith('LLM-')):
        from pyharness.core.llm_diagnostics import public_diagnostics
        p = body.get('payload', {})
        context = p.get('ctx') or {}
        d = public_diagnostics(p.get('diagnostics') or context.get('diagnostics'),
            code=p.get('code'), role=context.get('role', 'other'))
        body['payload'] = {'code': d['code'], 'message': d['summary'], 'diagnostics': d}
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))




# ============================================================ 脱敏渲染工具










# ============================================================ 时间线派生(纯函数)
