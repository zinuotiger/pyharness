"""Timeline and SSE projection helpers."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from pyharness.core.tenant_settings import tenant_for_session
from pyharness.events import Envelope

from .constants import SSE_HEARTBEAT_S, TIMELINE_KINDS, _SENSITIVE_HINTS

log = logging.getLogger("pyharness.desktop.projection")

@dataclass
class TimelineNode:
    """轨迹面板一帧(事件 → 时间线节点;kind/severity 集合见模块 docstring 偏离 11)。"""

    seq: int
    ts: str
    kind: str
    actor: str
    title: str
    detail: dict = field(default_factory=dict)
    severity: str = "info"                # info | warn | danger | success


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
        event_tenant = tenant_for_session(sid or "") if sid else ""
        async with self._lock_for(sid):
            for c in tuple(self.clients):
                if c.dead:
                    continue
                if c.tenant and event_tenant and c.tenant != event_tenant:
                    continue                # 租户过滤:禁止跨租户事件泄漏
                if c.tenant and not event_tenant and c.tenant != "default":
                    continue                # 未知会话非默认租户 fail-closed
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


def _env_dict(env: Any) -> dict:
    """Envelope/鸭子 → JSON 安全 dict(十字段;与 acp._env_dict 同款先例)。"""
    dump = getattr(env, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    if isinstance(env, dict):
        return dict(env)
    return {k: getattr(env, k) for k in
            ("seq", "ts", "type", "session_id", "actor", "origin",
             "task_id", "payload", "trace")
            if getattr(env, k, None) is not None}


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
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _jsonable(value: Any) -> Any:
    """任意值 → JSON 安全树(dataclass/dict/list/标量递归;桥序列化兜底)。"""
    if isinstance(value, TimelineNode):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Envelope):
        return _env_dict(value)
    return value


# ============================================================ 脱敏渲染工具
def _trunc(value: str, n: int = 160) -> str:
    """文本截断(超长加省略号,防刷屏/上下文爆炸)。"""
    value = str(value)
    return value if len(value) <= n else value[: n - 1] + "…"


def _redact_dict(d: dict, *, depth: int = 0) -> dict:
    """参数脱敏(INV-09):敏感键打码、长串截断、容器限深限长——渲染永不因单事件炸。"""
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = str(k)
        if any(hint in key.lower() for hint in _SENSITIVE_HINTS):
            out[key] = "[redacted]"
        elif isinstance(v, dict):
            out[key] = _redact_dict(v, depth=depth + 1) if depth < 2 else "<dict>"
        elif isinstance(v, (list, tuple)):
            items = [_redact_value(x, depth + 1) for x in v[:8]]
            out[key] = items + [f"…共{len(v)}项"] if len(v) > 8 else items
        elif isinstance(v, str):
            out[key] = _trunc(v, 120)
        elif isinstance(v, (int, float, bool)) or v is None:
            out[key] = v
        else:
            out[key] = f"<{type(v).__name__}>"
    return out


def _redact_value(v: Any, depth: int = 0) -> Any:
    """单值脱敏(容器内递归用)。"""
    if isinstance(v, dict):
        return _redact_dict(v, depth=depth)
    if isinstance(v, (list, tuple)):
        return [_redact_value(x, depth) for x in v[:8]]
    if isinstance(v, str):
        return _trunc(v, 120)
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return f"<{type(v).__name__}>"


def redact_args(payload: dict) -> dict:
    """tool.call 参数脱敏:工具名 + 校验后 args 脱敏 + raw_args 打码对照(INV-06 审计友好)。"""
    p = payload or {}
    name = p.get("name") or p.get("tool") or "?"
    out: dict[str, Any] = {"tool": str(name)}
    args = p.get("args")
    if isinstance(args, dict):
        out["args"] = _redact_dict(args)
    elif args is not None:
        out["args"] = _trunc(str(args), 200)
    raw = p.get("raw_args")
    if isinstance(raw, dict):
        out["raw_args_masked"] = _redact_dict(raw)
    elif raw is not None:
        out["raw_args_masked"] = _trunc(str(raw), 200)
    return out


def redact(payload: dict) -> dict:
    """通用载荷脱敏(fallback 渲染/事件详情用;禁吐 ctx 敏感值,ERR §1.4)。"""
    return _redact_dict(dict(payload or {}))


# ============================================================ 时间线派生(纯函数)
def approval_node(ev: Envelope, base: dict) -> TimelineNode:
    """approval.* 事件 → 轨迹帧(granted/denied/timeout 同构;approval_id=requested seq)。"""
    p = ev.payload or {}
    verdict = ev.type.rsplit(".", 1)[-1]          # requested|granted|denied|timeout
    if verdict == "requested":
        title, sev = "审批请求", "info"
    elif verdict == "granted":
        title, sev = "审批通过", "success"
    elif verdict == "denied":
        title, sev = "审批拒绝", "danger"
    else:
        title, sev = "审批超时(自动拒绝)", "warn"
    detail: dict[str, Any] = {}
    if verdict == "requested":
        detail = {"tool": p.get("tool"), "args_summary": p.get("args_summary"),
                  "risk": p.get("risk"), "ttl_ms": p.get("ttl_ms")}
    else:
        detail = {"approval_id": p.get("approval_id"), "by": p.get("by")}
    return TimelineNode(kind="approval", title=title, severity=sev,
                        detail={k: v for k, v in detail.items() if v is not None},
                        **base)


def render_timeline_node(ev: Envelope) -> TimelineNode:
    """事件 → 时间线一帧(纯函数;payload 字段缺失按 get 兜底,渲染永不中断整条线)。

    类型映射(轨迹 = 日志投影 INV-01):user.message→user;agent.message→message;
    tool.call→tool(脱敏参数);guard.rejected→guard(danger);approval.*→approval;
    llm.usage→budget;session.recovered→recovery;session.created/finished→recovery;
    其余持久事件 fallback→info(瞬时 llm.chunk 被 append 拒写,天然不在日志)。
    """
    p = ev.payload if hasattr(ev, "payload") else {}
    p = dict(p or {})
    base = {"seq": ev.seq, "ts": str(getattr(ev, "ts", "")),
            "actor": getattr(ev, "actor", "system")}
    t = getattr(ev, "type", "")
    if t == "user.message":
        return TimelineNode(kind="user", title="用户",
                            detail={"text": _trunc(p.get("content", ""), 400)}, **base)
    if t == "agent.message":
        return TimelineNode(kind="message", title="AI 回复", severity="success",
                            detail={"text": p.get("content", "")}, **base)
    if t == "tool.call":
        name = p.get("name") or p.get("tool") or "?"
        return TimelineNode(kind="tool", title=f"调用 {name}",
                            detail=redact_args(p), **base)
    if t == "guard.rejected":
        return TimelineNode(kind="guard", title="guard 拦截", severity="danger",
                            detail={"guard": p.get("guard_id") or p.get("guard"),
                                    "reason": p.get("reason"),
                                    "policy_ref": p.get("policy_ref")}, **base)
    if t.startswith("approval."):
        return approval_node(ev, base)
    if t == "llm.usage":
        return TimelineNode(
            kind="budget", title="用量",
            severity="warn" if p.get("out_tokens") else "info",
            detail={"model": p.get("model"), "in_tokens": p.get("in_tokens"),
                    "out_tokens": p.get("out_tokens"),
                    "cost_est": p.get("cost_est")}, **base)
    if t == "session.recovered":
        return TimelineNode(kind="recovery", title="崩溃修复声明", severity="warn",
                            detail={"fixed": p.get("fixed", []),
                                    "lost": p.get("lost") or p.get("quarantined"),
                                    "backup": p.get("backup")}, **base)
    if t in ("session.created", "session.finished"):
        detail: dict[str, Any] = {}
        if t == "session.finished":
            detail["reason"] = p.get("reason")
        return TimelineNode(kind="recovery", title=t, detail=detail, **base)
    # 其余持久事件兜底渲染(redact 防敏感原文进面板)
    return TimelineNode(kind="info", title=str(t), detail=redact(p), **base)


def derive_timeline(events: Iterable[Envelope], kinds: str = "") -> list[TimelineNode]:
    """事件流(seq 升序)→ 时间线节点序列;kinds = 逗号过滤(空 = 全部)。

    纯函数:输入只读事件迭代器,无副作用、不读存储;轨迹面板逐帧回放数据源的唯一派生口。
    """
    want = {k for k in kinds.split(",") if k} if kinds else None
    nodes: list[TimelineNode] = []
    for ev in events:
        node = render_timeline_node(ev)
        if want is None or node.kind in want:
            nodes.append(node)
    return nodes


