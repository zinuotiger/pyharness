"""pyharness/desktop.py — Windows 桌面壳:pywebview + FastAPI 同进程 (specs/desktop.py.md 契约;阶段 6)

功能编号:F065(Windows 桌面程序)· ADR-012(pywebview 壳 + FastAPI 同进程,不建 SPA 不开浏览器)·
ADR-001(窗口 = 只读事件投影)· ADR-005(单进程不被外壳破坏)· ADR-011(/api 响应复用错误码)。

一句话职责:双击即用的桌面壳——pywebview 主线程开窗(1280×800)加载 FastAPI 同进程本地服务
(`127.0.0.1:随机端口`);API 只读投影(会话列表/对话流/SSE 事件流/**Agent 轨迹时间线回放**/
预算仪表盘)或经引擎门面写(task_queue.submit 提问、approval.approve 审批、session.append 无特权);
`DesktopBridge`(js_api)与 SSE 双通道驱动前端;窗口关闭 = 优雅停服(强同步事件已落盘,uvicorn
停止,进程退出)。CLI/桌面/ACP 同一事件流的不同投影。

轨迹时间线 = 事件溯源最直观证明:面板逐帧渲染的是 JSONL 真源派生节点(用户说了啥 / LLM 想与做 /
guard 拦截 / 审批 / 用量 / 恢复声明),非聊天视图、绝不直写存储(ADR-012 只读投影不变式)。

依赖方向(单向):errors.raise_code ← events(词表/信封)← core/session(events_after/derive_messages)←
core/task_queue(submit)← core/approval(approve/deny)← bus(EventBus.subscribe)← persistence(open_store);
外壳装配复读 cli.assemble_ctx(偏离 3);pywebview 惰性导入(无显示环境仅缺 GUI 启动,模块导入零副作用)。

偏离说明(契约 = specs/desktop.py.md;以下为与伪码/既有实现冲突处的取舍,均列理由,与 cli.py/
acp.py 同款先例,已入 docstring 供审查):
1. 会话门面未落地(同 acp.py 偏离 1):伪码假想 ctx.session 提供 create()/open_session(sid)
   → 本模块按鸭子类型支持三种注入形状:(a) 门面(有 create/open_session/list)→ 委托;(b)
   ctx.session 即已绑定会话日志(sid 匹配)→ 直用(单会话投影);(c) ctx.session=None → 惰性
   装配本模块 DesktopSessionManager(真 persistence 落盘,自动回写 ctx.session)。三者皆无
   可用持久化面 → EVT-106 明确报错,**绝不静默自建**。
2. 伪码把全部 API handler 画成模块级裸函数(session_timeline(sid,...) 等),但函数体隐式引用
   ctx/app——真实 FastAPI 路由需要 app 上下文 → 实现为 DesktopApp 方法(路由 add_api_route 以
   绑定方法注册,路径参数/查询参数注解齐备),时间线派生核心抽为模块级纯函数 render_timeline_node/
   derive_timeline(单测直接打);session_timeline/stream_sse 等 spec 名保留为 DesktopApp 同名方法。
3. ctx 装配复读 cli._load_settings/assemble_ctx(spec 依赖表引 pyharness.bootstrap/assemble_ctx,
   该装配模块未落盘,cli.py 偏离 11 同款先例)——desktop 的 assemble_desktop_ctx 惰性 import
   pyharness.cli,复用其配置管线与门面形状(settings/bus/session/task_queue/approval/storage),
   不重复实现第四份装配。pywebview 仅 _load_webview 内惰性导入。
4. 总线订阅:伪码 `bus.subscribe("#", ...)` 的 "#" 全事件通配在真实 EventBus(段级 `前缀.*`
   通配)不存在 → 改为逐精确类型订阅 events 词表全部 64 类型 + `plugin.*` 段通配(覆盖插件
   命名空间;不叠通配防重复投递),owner="desktop" 统一摘除。
5. run_uvicorn:伪码 `server.run()`(asyncio.run 内部自建自毁 loop)拿不到运行中事件循环 →
   本实现手工 new_event_loop + set_event_loop + run_until_complete(server.serve(...))——同样单
   worker、禁 reload、阻塞至 should_exit;多出来的 loop 抓取(app.loop)是 DesktopBridge._run
   跨线程 run_coroutine_threadsafe 的前提(webview 线程 → 引擎事件循环)。
6. SSE 响应:sse-starlette 未装配 → 自研 ASGI 流(Starlette StreamingResponse + media_type=
   text/event-stream,spec 明示"或自研 ASGI 流");心跳注释行保活、断开即净退同伪码。
   注册生命周期修正:伪码 `async with hub.register(me): return EventSourceResponse(...)`
   在视图返回时即注销游标(ASGI 流开始前)→ 收不到事件;本实现把 register 移入生成器
   内部,注册覆盖整个响应体迭代期(首个 anext 进入、净退/断连/异常自动注销)。
7. DesktopBridge 方法返回 JSON **字符串**(伪码 _run 即 json.dumps);引擎错/超时转本地错误体
   {"code","advice"}(ADR-011,远端不吐 ctx 敏感值)。BUSY 码未登记(acp.py 偏离 4 同款)直接构造。
8. 启动自检/附件/预算:pyharness.repair / attach_image / budget 模块未装配 → repair 经
   ctx.repair.ensure_repaired 鸭子跳过(acp 偏离 8 同款);预算无闸返回 disabled 标志(伪码异常表
   CFG-601 行);附件端点在 payload 模型允许时尽力落 user.attachment.image,失败 EVT-100 拒写。
9. approval 决策入口:伪码假想 await ctx.approval.approve(...);真实 ApprovalProvider.approve/deny
   为同步入口(acp 偏离 7 同款,同步/异步兼容);by="desktop"(伪码字面量)。审批请求的通道/执行
   runner 注入依赖引擎装配层(agent_loop.run_for_task 未来 seam),本模块按 ctx 鸭子接线并留注入点。
10. 窗口生命周期细节:pywebview 关窗事件在 webview.start() 阻塞期间触发 → closing 事件只置
    stopping 旗标(request_shutdown),真正停服统一在 webview.start() 返回后调 shutdown_gracefully()
    (幂等);错误提示在无显示环境降级为 stderr 输出(错误框依赖 GUI,打包 exe 后仍可另接 MessageBox)。
11. TimelineNode.severity/kind 枚举:伪码数据结构表 kind 集合未含 fallback 的 "info"、severity
    未列默认 → 本实现 kind ∈ {user,thought,message,tool,guard,approval,budget,recovery,error,info},
    severity ∈ {info,warn,danger,success},fallback 渲染 kind="info" severity="info"(以伪码行为为准)。
12. session_messages 对话流:伪码未展开;derive_messages 是全量 reducer(无 per-message seq 面)→
    返回完整派生消息 + to_seq(会话当前 seq),增量对话由 SSE 事件流承担,不伪造部分折叠。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import socket
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional, Union

import uvicorn
from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from pyharness.core.approval import ApprovalProvider
from pyharness.core.session import SessionLog, open_session
from pyharness.core.task_queue import TaskQueue
from pyharness.errors import PyHError, raise_code
from pyharness.events import EVENT_TYPES, Envelope
from pyharness.events.vocab import TRANSIENT_TYPES

log = logging.getLogger("pyharness.desktop")

# ---------------------------------------------------------------- 常量
WINDOW_TITLE = "PyHarness"
WINDOW_WIDTH = 1280                       # F065 窗口尺寸(伪码锁定)
WINDOW_HEIGHT = 800
HOST = "127.0.0.1"                        # 仅回环(PRD F065 边界:禁绑非回环)
SSE_HEARTBEAT_S = 15.0                    # SSE 心跳注释行间隔(伪码 wait_for 超时)
BRIDGE_TIMEOUT_S = 60.0                   # js_api 跨线程调用超时(超时→本地 BUSY 错误体)
LISTEN_TIMEOUT_S = 15.0                   # uvicorn 就绪探测超时(失败退 1 不弹空窗)
CHANNEL = "desktop"                       # 审批裁决者通道名(by="desktop",伪码字面量)
WARN_RATIO = 0.8                          # 预算 warn 80% 阈值(PARAMETER-ANCHOR)

# 轨迹面板 6 类核心节点之外的全部合法 kind(含伪码 fallback 的 info,见偏离 11)
TIMELINE_KINDS: frozenset = frozenset({
    "user", "thought", "message", "tool", "guard", "approval",
    "budget", "recovery", "error", "info"})
# 疑似敏感键名(redact 命中即打码,INV-09 不泄密;与 acp._SENSITIVE_KEY_HINTS 同族)
_SENSITIVE_HINTS = ("secret", "token", "apikey", "api_key", "password",
                    "credential", "private", "key")
# HTTP 状态映射(ADR-011:4xx 客户端 / 5xx 引擎;与 spec 各函数异常表对齐)
_STATUS_FOR_CODE: dict[str, int] = {
    "EVT-100": 400, "EVT-101": 409, "EVT-102": 400, "EVT-104": 409,
    "EVT-105": 400, "EVT-106": 404,
    "APR-501": 403, "APR-503": 404, "GRD-403": 409,
    "QUE-001": 503, "PERS-202": 500,
}


# ---------------------------------------------------------------- 数据结构
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
        if type_ in TRANSIENT_TYPES or not _has_envelope_scope(payload):
            sid, seq = None, None           # 瞬时/裸载荷:全客户端广播(流式)
        else:
            sid = _env_session_id(payload)  # 持久事件按会话过滤
            seq = getattr(payload, "seq", None)
        text = _serialize_event(type_, payload)
        async with self._lock_for(sid):
            for c in tuple(self.clients):
                if c.dead:
                    continue
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
                pass
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
    body = _env_dict(payload) if _has_envelope_scope(payload) else {
        "type": type_, "payload": dict(payload or {})}
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


# ============================================================ 端口与后台服务
def pick_free_port() -> int:
    """127.0.0.1 随机空闲端口(bind 0 取系统分配 → 释放交 uvicorn;竞窗极小,F065 边界)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def run_uvicorn(app: "DesktopApp", port: int) -> None:
    """后台线程 serve:单 worker、禁 reload(seq 单调前提 ADR-005);阻塞至 should_exit。

    偏离 5:手工持 loop(server.serve 在自建 loop 上运行)并把 app.loop 暴露给
    DesktopBridge._run 跨线程投递;线程收尾复位 app.server/app.loop。
    PyInstaller --windowed 打包:进程无 console 句柄 → sys.stderr 为 None,
    uvicorn 默认 formatter 调 isatty() 崩(AttributeError)→ 无 stderr 时
    log_config=None 跳过 dictConfig(用标准 logging,日志进兜底 handler)。
    """
    kw: dict = dict(log_level="warning", workers=1)
    if sys.stderr is None:                    # windowed/frozen 无 stderr:禁用 uvicorn 自配日志
        kw["log_config"] = None
    config = uvicorn.Config(app.api, host=HOST, port=port, **kw)
    server = uvicorn.Server(config)
    app.server = server
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.loop = loop
    try:
        loop.run_until_complete(server.serve(sockets=None))
    except Exception:                       # noqa: BLE001 服务线程异常仅日志(域外 CYC-999)
        log.exception("uvicorn serve 异常退出 port=%s", port)
    finally:
        app.loop = None
        app.server = None
        asyncio.set_event_loop(None)
        loop.close()


def _probe_port(port: int) -> bool:
    """127.0.0.1:port 是否可连(就绪探测;失败静默返回 False)。"""
    try:
        with socket.create_connection((HOST, port), timeout=0.3):
            return True
    except OSError:
        return False


def wait_until_listening(port: int, timeout: float = LISTEN_TIMEOUT_S) -> bool:
    """同步就绪探测(webview 开窗前阻塞;失败 → 调用方退 1 不弹空窗)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _probe_port(port):
            return True
        time.sleep(0.05)
    return False


async def wait_listening_async(app: "DesktopApp", port: int,
                               timeout: float = LISTEN_TIMEOUT_S) -> None:
    """异步就绪探测(run_desktop 用;超时抛 CYC-999 由调用方收口)。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if _probe_port(port):
            return
        if getattr(app, "stopping", None) is not None and app.stopping.is_set():
            raise_code("CYC-999", op="listening", port=port,
                       hint="桌面已请求关闭,取消启动")
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.05)
    raise_code("CYC-999", op="listening", port=port,
               hint="uvicorn 就绪超时(端口/环境不可用),已取消启动")


# ============================================================ 会话解析(三种形状)
async def _await(res: Any) -> Any:
    """同步/异步兼容调用(引擎各面注入形状不一,cli/acp 同款先例)。"""
    if inspect.isawaitable(res):
        return await res
    return res


def _cfg_of(ctx: Any) -> Any:
    """配置读取:cli 门面存 settings,测试门面存 config,两键兼容。"""
    return getattr(ctx, "settings", None) or getattr(ctx, "config", None)


def _sessions_dir_of(ctx: Any) -> Optional[Path]:
    """会话日志目录(ctx.storage.sessions_dir 或 settings.storage.sessions_dir)。"""
    storage = getattr(ctx, "storage", None)
    d = getattr(storage, "sessions_dir", None)
    if d is None:
        cfg = _cfg_of(ctx)
        st = getattr(cfg, "storage", None)
        d = getattr(st, "sessions_dir", None)
    return Path(d).expanduser() if d else None


class _SingleLogSurface:
    """形状 (b):ctx.session 即已绑定会话日志 → 单会话投影(其余 sid → EVT-106)。"""

    def __init__(self, log: Any) -> None:
        self._log = log
        self.sid = getattr(log, "sid", "")

    async def create(self) -> str:
        raise_code("EVT-106", hint="ctx.session 为已绑定日志,桌面无法新建会话;"
                   "注入会话门面(create/open_session)后支持多会话")

    async def open_session(self, sid: str) -> Any:
        if sid == self.sid:
            return self._log
        raise_code("EVT-106", session_id=sid,
                   hint=f"ctx.session 仅绑定会话 {self.sid};注入会话门面后支持切换")

    def list(self) -> list[dict]:
        return [_summarize_log(self._log)]


class DesktopSessionManager:
    """形状 (c):真 persistence 会话门面(desktop 自装配)——多会话磁盘真源管理。

    create:新建 sid → open_store + open_session(session.created seq=1,EVT-106 引导);
    open_session:文件不存在 → EVT-106;存在 → repair 鸭子前置(F060 未装配跳过) →
    重放重建 + 总线落盘订阅(owner=f"persistence:{sid}",cli._attach_log_persistence 同款);
    list:扫描目录 *.jsonl,头行(session.created)取标题,尾行取终态(不整文件重放)。
    shutdown_all:全量 flush + 关句柄 + 摘订阅(优雅停服兜底)。
    """

    def __init__(self, *, dir: Path, bus: Any, config: Any = None,
                 settings: Any = None) -> None:
        self.dir = Path(dir)
        self.bus = bus
        self.config = config or settings
        self._logs: dict[str, SessionLog] = {}
        self._owners: dict[str, str] = {}
        self._stores: dict[str, Any] = {}

    # ------------------------------------------------ 内部
    def _path(self, sid: str) -> Path:
        return self.dir / f"{sid}.jsonl"

    def _attach_persistence(self, log_: SessionLog, store: Any, sid: str) -> None:
        """总线 → 存储订阅(事件一入内存即入真源队列;强同步三类即写即刷 F011)。"""
        if self.bus is None:
            return
        owner = f"persistence:{sid}"

        async def _record(type_: str, payload: Any) -> None:
            await store.append(payload, sync=type_ in (
                "user.message", "guard.rejected", "approval.requested",
                "approval.granted", "approval.denied", "approval.timeout",
                "session.finished", "session.recovered", "segment.start",
                "fork.created", "context.compacted"))

        for t in EVENT_TYPES:               # 词表逐精确类型订阅(段通配防重复,见偏离 4)
            self.bus.subscribe(t, _record, owner=owner)
        log_._bus = self.bus                # append → 分发 → 落盘闭环
        self._owners[sid] = owner

    # ------------------------------------------------ 门面方法
    async def create(self) -> str:
        """新建会话:sid → store → open_session → 总线落盘订阅 → session.created 首事件。"""
        sid = f"s-{uuid.uuid4().hex[:12]}"  # Envelope.session_id min_length=8
        store = await self._open_store(sid)
        log_ = await open_session(sid, store)      # 空日志回放(文件刚建)
        self._attach_persistence(log_, store, sid)
        await log_.append("session.created", {"title": "", "model": self._default_model()},
                          actor="system", sync=True)
        self._logs[sid] = log_
        self._stores[sid] = store
        return sid

    def _default_model(self) -> str:
        cfg = self.config
        llm = getattr(cfg, "llm", None)
        model = getattr(llm, "model", None)
        return str(model) if model else "unknown"

    async def open_session(self, sid: str) -> SessionLog:
        """打开既有会话(重放重建 + 落盘订阅);文件不存在 → EVT-106。"""
        if sid in self._logs:               # 同柄复用(INV-03 缓存一致防双实例漂移)
            return self._logs[sid]
        if not self._path(sid).exists():
            raise_code("EVT-106", session_id=sid,
                       hint="会话不存在(日志缺失);先 `session list` 确认 sid 或新建会话")
        await self._ensure_repaired(sid)    # 损坏先修再 open(F060;未装配跳过)
        store = await self._open_store(sid)
        log_ = await open_session(sid, store)      # 重放重建(坏行 PERS-201 记跳隔离)
        self._attach_persistence(log_, store, sid)
        self._logs[sid] = log_
        self._stores[sid] = store
        return log_

    async def _open_store(self, sid: str) -> Any:
        """open_store 工厂(异步外壳保持调用面统一;PERS-202 上抛)。"""
        from pyharness.persistence import open_store
        return open_store(sid, dir=self.dir)

    async def _ensure_repaired(self, sid: str) -> None:
        """repair 前置(F060;ctx.repair.ensure_repaired 未装配 → 跳过,见偏离 8)。"""
        repair = getattr(self, "_ctx_repair", None)
        fn = getattr(repair, "ensure_repaired", None)
        if callable(fn):
            res = fn(sid)
            if hasattr(res, "__await__"):
                await res

    def list(self) -> list[dict]:
        """会话摘要清单:只读首/尾行 + 行数,不整文件重放(老会话 O(1) 扫描)。"""
        out: list[dict] = []
        if not self.dir.exists():
            return out
        for path in sorted(self.dir.glob("*.jsonl")):
            sid = path.stem
            if not sid.startswith("s-"):        # 轮转段 .N.jsonl / 备份非会话文件跳过
                continue
            summary: dict[str, Any] = {"sid": sid, "title": "", "finished": False}
            try:
                with open(path, "rb") as f:
                    lines = f.readlines()
                summary["lines"] = len(lines)
                summary["size_bytes"] = path.stat().st_size
                if lines:
                    head = json.loads(lines[0])
                    if head.get("type") == "session.created":
                        summary["title"] = (head.get("payload") or {}).get("title", "")
                    tail = json.loads(lines[-1])
                    summary["finished"] = tail.get("type") == "session.finished"
            except Exception:               # noqa: BLE001 文件坏/竞态 → 摘要留最小
                log.warning("desktop list 读 %s 失败(摘要降级)", path.name, exc_info=True)
            out.append(summary)
        out.sort(key=lambda s: s["sid"])
        return out

    def logs(self) -> list[SessionLog]:
        """已打开会话日志清单(优雅停服 flush 用)。"""
        return list(self._logs.values())

    def shutdown_all(self) -> None:
        """收尾:全量 flush + 关句柄 + 摘落盘订阅(幂等;正常路径强同步事件已落盘)。"""
        for sid, store in self._stores.items():
            try:
                store.flush()
            except Exception:               # noqa: BLE001 收尾刷盘失败不掩盖退出
                log.warning("desktop flush sid=%s 失败", sid, exc_info=True)
            try:
                store.close()
            except Exception:               # noqa: BLE001
                pass
        if self.bus is not None:
            for owner in set(self._owners.values()):
                try:
                    self.bus.unsubscribe_all(owner)
                except Exception:           # noqa: BLE001
                    pass
        self._stores.clear()
        self._owners.clear()
        self._logs.clear()


def _summarize_log(log_: Any) -> dict:
    """单会话日志摘要(形状 b 投影;stats 鸭子读取)。"""
    stats_fn = getattr(log_, "stats", None)
    stats = stats_fn() if callable(stats_fn) else {}
    return {"sid": getattr(log_, "sid", ""),
            "title": "", "finished": bool(stats.get("closed", False)),
            "seq": stats.get("seq", 0), "event_count": stats.get("event_count", 0)}


def _surface_of(ctx: Any, *, bus: Any, config: Any = None,
                sessions_dir: Optional[Path] = None) -> Any:
    """会话面解析(偏离 1):门面 → 委托;绑定日志 → 单会话;None → 自装配管理器。"""
    surface = getattr(ctx, "session", None)
    if surface is None:
        if sessions_dir is None:
            raise_code("EVT-106", hint="会话未装配(ctx.session=None)且无持久化目录;"
                       "注入会话门面或先配置 storage.sessions_dir")
        mgr = DesktopSessionManager(dir=sessions_dir, bus=bus, config=config)
        try:
            ctx.session = mgr               # 惰性回写:引擎门面后续可复用(acp 同款)
        except Exception:                   # noqa: BLE001 只读 ctx:仅本地持有
            pass
        return mgr
    if callable(getattr(surface, "open_session", None)):
        return surface                      # (a) 门面(create/open_session/list)
    if getattr(surface, "sid", None):
        return _SingleLogSurface(surface)   # (b) 已绑定会话日志
    raise_code("EVT-106", hint="会话门面形状非法(无 open_session 亦无 sid);"
               "注入 create/open_session 门面")


# ============================================================ DesktopApp
class DesktopApp:
    """桌面程序总装(F065 内核入口):FastAPI 装配 + 总线 → SSE fan-out + 会话面。

    字段(spec 数据结构表):api / url / bridge / hub / ctx / stopping / server / loop;
    实现附加:_surface(会话解析面)/ _logs(会话同柄缓存)/ _queues(per-session TaskQueue)/
    _approvals(per-session ApprovalProvider)/ _sub(总线订阅登记)。
    """

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.hub = EventStreamHub()
        self.bridge: Optional[DesktopBridge] = None
        self.server: Any = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stopping = threading.Event()
        self.url: str = ""
        self._sub: Any = None
        self._graceful_done: bool = False        # 优雅停服幂等标记(双击关闭只走一次)
        self._logs: dict[str, Any] = {}          # sid → SessionLog(同柄,INV-03)
        self._queues: dict[str, TaskQueue] = {}
        self._approvals: dict[str, ApprovalProvider] = {}
        self._engines: dict[str, Any] = {}       # sid → 引擎 spine(懒装配)
        self.api = FastAPI(title="PyHarness Desktop", docs_url=None, redoc_url=None)
        # 会话面惰性解析(首用才装配管理器;EVT-106 提前暴露装配缺面)
        self._surface: Any = None
        self.mount_api()
        bus = getattr(ctx, "bus", None)
        if bus is not None:
            self._sub = self._subscribe_all(bus)

    # ------------------------------------------------ 总线订阅(偏离 4)
    def _subscribe_all(self, bus: Any) -> list:
        """全事件 fan-out:词表 64 精确类型 + plugin.* 段通配(owner=desktop)。"""
        subs = []
        for t in EVENT_TYPES:
            subs.append(bus.subscribe(t, self.hub.forward, owner="desktop"))
        subs.append(bus.subscribe("plugin.*", self.hub.forward, owner="desktop"))
        return subs

    def _surface_mgr(self) -> Any:
        """惰性会话面(构造不碰磁盘;首用解析并缓存)。"""
        if self._surface is None:
            ctx = self.ctx
            self._surface = _surface_of(
                ctx, bus=getattr(ctx, "bus", None), config=_cfg_of(ctx),
                sessions_dir=_sessions_dir_of(ctx))
            # 自装配管理器接线 ctx.repair 鸭子(未装配 → ensure_repaired 跳过)
            if isinstance(self._surface, DesktopSessionManager):
                self._surface._ctx_repair = getattr(ctx, "repair", None)
        return self._surface

    # ------------------------------------------------ API 装配
    def mount_api(self) -> None:
        """注册全部端点(只读投影 + 引擎门面写 + SSE)+ PyHError 统一错误体(ADR-011)。"""
        a = self.api
        a.add_api_route("/", self._index_page, methods=["GET"])
        a.add_api_route("/api/sessions", self.create_session, methods=["POST"])
        a.add_api_route("/api/sessions", self.list_sessions, methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/messages", self.session_messages,
                        methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/messages", self.create_message,
                        methods=["POST"])
        a.add_api_route("/api/sessions/{sid}/timeline", self.session_timeline,
                        methods=["GET"])                            # 轨迹(核心)
        a.add_api_route("/api/sessions/{sid}/event/{seq}", self.event_detail,
                        methods=["GET"])
        a.add_api_route("/api/stream", self.stream_sse, methods=["GET"])  # SSE
        a.add_api_route("/api/approvals/pending", self.pending_approvals,
                        methods=["GET"])
        a.add_api_route("/api/approvals/{aid}", self.decide_approval,
                        methods=["POST"])                           # 审批弹窗裁决
        a.add_api_route("/api/budget/{sid}", self.budget_dashboard, methods=["GET"])
        a.add_api_route("/api/attachments", self.upload_attachment, methods=["POST"])
        a.add_exception_handler(PyHError, self._pyhe_handler)
        a.add_exception_handler(Exception, self._unexpected_handler)

    # ------------------------------------------------ 统一错误体
    @staticmethod
    def _detail_of(e: PyHError) -> Optional[dict]:
        """错误 detail:ctx 脱敏后透出(ERR §1.4:禁 ctx 敏感值原文)。"""
        if not e.ctx:
            return None
        return redact(e.ctx)

    async def _pyhe_handler(self, request: Request, exc: PyHError) -> JSONResponse:
        """PyHError → ApiErrorBody{code,advice,detail} + HTTP 状态映射(ADR-011)。"""
        status = _STATUS_FOR_CODE.get(exc.code, 500)
        advice = (exc.ctx.get("advice") or exc.ctx.get("hint")
                  or exc.spec.advice or exc.message)
        body: dict[str, Any] = {"code": exc.code, "advice": str(advice),
                                "detail": self._detail_of(exc)}
        return JSONResponse(status_code=status, content=body)

    async def _unexpected_handler(self, request: Request, exc: Exception) -> JSONResponse:
        """未预期兜底 → CYC-999(堆栈仅本地 debug;远端只回码+建议)。"""
        log.error("desktop api 未预期异常 path=%s", request.url.path, exc_info=True)
        return JSONResponse(status_code=500,
                            content={"code": "CYC-999",
                                     "advice": "引擎内部错误(见本地日志)",
                                     "detail": {"type": type(exc).__name__}})

    # ------------------------------------------------ 页面(前端入口)
    @staticmethod
    def _index_page() -> HTMLResponse:
        """根路由:加载内嵌前端页(会话列表/对话/轨迹时间线/审批/预算)。

        前端文件打包为 data 资源:源码运行读 pyharness/ui/index.html;PyInstaller
        打包时 --add-data 携带同相对路径(_MEIPASS 下亦命中)。缺失 → 兜底提示页。
        """
        try:
            import importlib.resources as _ir
            html = _ir.files("pyharness.ui").joinpath("index.html").read_text(
                encoding="utf-8")
        except Exception:                       # noqa: BLE001 资源缺失兜底
            from pathlib import Path as _P
            p = _P(__file__).resolve().parent / "ui" / "index.html"
            try:
                html = p.read_text(encoding="utf-8")
            except Exception:                   # noqa: BLE001
                html = ("<html><body style='background:#0f1420;color:#dbe4f5;"
                        "font-family:sans-serif;display:flex;align-items:center;"
                        "justify-content:center;height:100vh'>"
                        "<div><h2>PyHarness Desktop</h2>"
                        "<p>前端资源缺失(ui/index.html 未随包携带)</p></div></body></html>")
        return HTMLResponse(html)

    # ------------------------------------------------ 会话解析(端点共用)
    async def _require_session(self, sid: str) -> Any:
        """解析会话日志(同柄缓存):未 open → EVT-106 守卫(伪码 ensure_open_session)。"""
        if not isinstance(sid, str) or not sid.strip():
            raise_code("EVT-100", field="sid", sid=sid, advice="sid 须非空字符串")
        if sid in self._logs:               # 同柄复用(INV-03)
            return self._logs[sid]
        log_ = await _await(self._surface_mgr().open_session(sid))
        self._logs[sid] = log_
        return log_

    async def _queue_for(self, sid: str, log_: Any) -> TaskQueue:
        """per-session TaskQueue(runner = per-session 真实引擎;真 LLM 已注册)。"""
        q = self._queues.get(sid)
        if q is None:
            runner = await self._engine_runner_for(sid, log_)
            q = TaskQueue(session=log_, runner=runner)
            self._queues[sid] = q
        return q

    async def _engine_runner_for(self, sid: str, log_: Any) -> Any:
        """per-session 引擎 runner(懒装配):复用 manager 已 open 的 log_/bus,
        补 loop/scope/llm/空工具表 + 总线落盘订阅 → engine.make_runner。

        无 DEEPSEEK_API_KEY 时注册失败 → 回落 CYC-999 runner(任务快速失败,
        前端收到结构化错误提示,不悬挂)——装配层降级,见 engine 偏离说明。
        """
        spine = self._engines.get(sid)
        if spine is None:
            from pyharness import engine as _eng
            from pyharness.engine import make_runner as _eng_make_runner
            cfg = self.ctx.settings
            _eng.register_default_llm(cfg)          # 幂等;无 key → CRED-701
            store = getattr(self._surface_mgr(), "_stores", {}).get(sid)
            spine = _eng.build_runner_components(
                cfg, log_=log_, bus=getattr(self.ctx, "bus", None),
                sessions_dir=_sessions_dir_of(self.ctx), store=store,
                attach_persistence=False)   # manager 已订阅落盘;重复订=双写卡死
            self._engines[sid] = spine
            # 审批接线:engine ApprovalProvider → ctx.approval(桌面裁决端点
            # _provider_owning 经 ctx.approval 兜底定位;否则弹窗 A/B 打来 APR-503)
            if getattr(self.ctx, "approval", None) is None:
                self.ctx.approval = spine.approval
            if getattr(self.ctx, "guard", None) is None:
                self.ctx.guard = spine.guard
        return _eng_make_runner(spine)

    def _runner_seam(self) -> Any:
        """执行器注入点(引擎装配层):ctx.make_runner()/ctx.task_runner 优先,None 缺省。"""
        ctx = self.ctx
        make = getattr(ctx, "make_runner", None)
        if callable(make):
            return make()
        return getattr(ctx, "task_runner", None)

    def _approval_for(self, sid: str, log_: Any) -> ApprovalProvider:
        """per-session ApprovalProvider;ctx.approval(装配注入,单会话形态)优先。

        多会话各 provider 以 bus=None 构造(偏离 1 注):裁决结果事件仍由 sess.append
        经会话总线分发到 hub(SSE 可见),provider 侧按 approval.py 偏离 3 直接消费
        on_verdict——避免共享总线把 A 会话裁决扇到 B provider 触发跨会话 APR-503
        system.error 噪音(approval.on_verdict 未知 id 兜底路径)。
        """
        provider = self._approvals.get(sid)
        if provider is None:
            ctx_approval = getattr(self.ctx, "approval", None)
            if ctx_approval is not None and not self._approvals:
                return ctx_approval          # 装配注入的全会话 provider(单会话形态)
            provider = ApprovalProvider(session=log_, bus=None,
                                        config=_cfg_of(self.ctx))
            self._approvals[sid] = provider
        return provider

    def _all_approval_providers(self) -> list:
        """pending 聚合:per-session providers + ctx.approval(去重)。"""
        seen, out = set(), []
        ctx_approval = getattr(self.ctx, "approval", None)
        for p in list(self._approvals.values()) + ([ctx_approval] if ctx_approval else []):
            if p is not None and id(p) not in seen:
                seen.add(id(p))
                out.append(p)
        return out

    def request_shutdown(self) -> None:
        """关窗事件入口:置 stopping 旗标(幂等);真正停服在 webview.start() 返回后。"""
        self.stopping.set()

    # ============================================================ 端点实现
    async def create_session(self) -> dict:
        """新建会话(前端 + 按钮):门面 create → 新 sid(created seq=1 已落盘)。"""
        mgr = self._surface_mgr()
        fn = getattr(mgr, "create", None)
        if not callable(fn):
            raise_code("CYC-999", hint="会话门面未实现 create(缺 DesktopSessionManager)")
        sid = fn()
        if hasattr(sid, "__await__"):
            sid = await sid
        # 新会话立即登记进同柄缓存(避免首条消息再走 open_session)
        log_ = await self._require_session(sid)
        self._logs[sid] = log_
        return {"sid": str(sid)}

    async def list_sessions(self) -> dict:
        """会话列表投影:门面 list(自装配管理器扫目录;绑定日志单条)。"""
        lst = self._surface_mgr().list()
        if callable(lst) or hasattr(lst, "__await__"):
            lst = await _await(lst)
        sessions = [s if isinstance(s, dict) else {"sid": str(s)}
                    for s in (lst or [])]
        return {"sessions": sessions, "count": len(sessions)}

    async def session_messages(self, sid: str, after_seq: int = 0) -> dict:
        """对话流(derive_messages 派生,聊天气泡源):全量 reducer + to_seq。
        after_seq 接受但折叠为全量(见偏离 12:增量由 SSE 承担)。"""
        log_ = await self._require_session(sid)
        msgs = log_.derive_messages()
        to_seq = 0
        stats_fn = getattr(log_, "stats", None)
        if callable(stats_fn):
            to_seq = int((stats_fn().get("seq") or 0))
        return {"sid": sid, "after_seq": int(after_seq), "to_seq": to_seq,
                "messages": msgs}

    async def session_timeline(self, sid: str, after_seq: int = 0,
                               kinds: str = "") -> dict:
        """Agent 轨迹时间线(F065 核心):events_after 派生 6 类节点,可增量/过滤。

        after_seq = 增量游标(断连续拉,排他下界);kinds = 逗号 kind 过滤(空 = 全部)。
        只读派生,绝不直写(ADR-012);瞬时事件不在日志 → 时间线天然只含持久事件。
        """
        log_ = await self._require_session(sid)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int):
            raise_code("EVT-100", field="after_seq", value=after_seq,
                       advice="after_seq 须 int(seq 游标)")
        nodes = derive_timeline(log_.events_after(after_seq=max(0, int(after_seq))),
                                kinds=str(kinds or ""))
        return {"sid": sid, "base_seq": max(0, int(after_seq)),
                "nodes": [asdict(n) for n in nodes]}

    async def event_detail(self, sid: str, seq: int) -> dict:
        """单事件详情(事件溯源"点开看现场";seq 空洞/越界 → EVT-100 404)。"""
        log_ = await self._require_session(sid)
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise_code("EVT-100", field="seq", value=seq, advice="seq 须 int")
        env = log_.get(int(seq))
        if env is None:
            raise_code("EVT-100", sid=sid, seq=seq,
                       advice="seq 不存在(空洞/坏行隔离/越界);时间线为真源可回放")
        return {"event": _env_dict(env)}

    async def stream_sse(self, request: Request, sid: str = "",
                         after_seq: int = 0) -> StreamingResponse:
        """SSE 事件流:注册 StreamClient(游标 after_seq)→ 总线事件按 seq 序 fan-out;
        断开 → 客户端带 last_seq 重连续拉(F065 边界);15s 心跳注释行保活。"""
        me = StreamClient(sid=str(sid or ""), last_seq=max(0, int(after_seq)))

        async def gen():
            # 注册生命周期必须覆盖响应体实际迭代期:ASGI/uvicorn 在视图函数返回后才
            # 流式读取 body,若像伪码那样在视图内 `async with register` 再 return,
            # __aexit__ 会在流开始前就把游标注销 → SSE 只见心跳收不到事件。故注册
            # 移入生成器内部:首个 anext 进入注册,生成器净退/断连/异常时 __aexit__
            # 自动注销(行为契约修正,偏离 6 同条)。
            async with self.hub.register(me):
                while not self.stopping.is_set():
                    if await request.is_disconnected():
                        break                       # 前端断开 → 回收游标
                    try:
                        item = await asyncio.wait_for(me.queue.get(),
                                                      timeout=SSE_HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"          # 心跳注释行(SSE 保活)
                        continue
                    if item is None:                # hub.close_all 哨兵 → 净退
                        break
                    type_, text = item
                    yield f"event: {type_}\ndata: {text}\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"})

    async def create_message(self, sid: str, body: dict = Body(default={})) -> dict:
        """提问(经门面写,无特权路径):user.message 强同步 → submit → {task_id,user_seq}。

        页面永不直写存储(ADR-001/012);写结果经总线 → SSE 回流渲染。空文本 EVT-100 拒写。
        """
        body = body or {}
        text = str(body.get("text") or "").strip()
        if not text:
            raise_code("EVT-100", advice="消息不能为空")
        log_ = await self._require_session(sid)
        env = await log_.append("user.message", {"content": text},
                                actor="user", origin=CHANNEL, sync=True)
        await self._append_attachments(log_, body.get("attachments"))
        queue = await self._queue_for(sid, log_)
        task_id = await queue.submit(text, meta={"channel": CHANNEL,
                                                 "session_id": sid})
        return {"task_id": task_id, "user_seq": env.seq}

    async def _append_attachments(self, log_: Any, attachments: Any) -> None:
        """F061 图片附件寻址(尽力):payload 模型不允许的附件 → EVT-100 拒写。"""
        for ref in (attachments or []):
            if not isinstance(ref, dict):
                raise_code("EVT-100", field="attachments", advice="附件须 dict 引用")
            payload = {"file_path": str(ref.get("file_path") or ref.get("ref") or ""),
                       "mime": ref.get("mime") or "image/png",
                       "sha256": ref.get("sha256") or "0" * 64,
                       "w": ref.get("w") or 1, "h": ref.get("h") or 1,
                       "size_bytes": ref.get("size_bytes")}
            await log_.append("user.attachment.image", payload, actor="user")

    async def decide_approval(self, aid: int,
                              body: dict = Body(default={})) -> dict:
        """审批弹窗裁决桥:approve/deny(by="desktop");桥无特权(重入 guard 链仍生效)。

        裁决经自属会话 provider(或 ctx.approval);未知 aid → APR-503(404+忽略,防重放)。
        """
        body = body or {}
        decision = body.get("decision")
        if isinstance(aid, bool) or not isinstance(aid, int):
            raise_code("EVT-100", field="aid", aid=aid, advice="aid 须 int(请求事件 seq)")
        if decision not in {"approve", "deny"}:
            raise_code("EVT-100", field="decision", decision=decision,
                       advice="decision 取值 approve/deny")
        provider = self._provider_owning(aid)
        fn = getattr(provider, decision, None)      # approve/deny 同构入口(偏离 9)
        if not callable(fn):
            raise_code("CYC-999", hint="审批裁决入口未装配(ctx.approval.approve/deny)")
        res = fn(int(aid), by=CHANNEL)              # APR-503/GRD-403 同步上抛
        if hasattr(res, "__await__"):
            await res
        return {"ok": True, "approval_id": int(aid)}

    def _provider_owning(self, aid: int) -> Any:
        """按 approval_id 定位裁决 provider(审批请求事件 seq 会话内唯一;防跨会话重放)。"""
        for p in self._all_approval_providers():
            pending = getattr(p, "_pending", None)
            if isinstance(pending, dict) and int(aid) in pending:
                return p
        ctx_approval = getattr(self.ctx, "approval", None)
        if ctx_approval is not None:
            return ctx_approval
        raise_code("APR-503", approval_id=aid,
                   hint="未知/已裁决的 approval_id;同一审批至多一个结果(防重放)")

    async def pending_approvals(self) -> dict:
        """未决审批清单(审批弹窗轮询源):跨 per-session provider 聚合,按 approval_id 升序。"""
        out: list[dict] = []
        seen: set[int] = set()
        for p in self._all_approval_providers():
            pending = getattr(p, "_pending", None)
            if not isinstance(pending, dict):
                continue
            for aid, req in pending.items():
                if aid in seen:
                    continue
                seen.add(aid)
                out.append({"approval_id": aid,
                            "tool": getattr(req, "tool", ""),
                            "args_summary": getattr(req, "args_summary", ""),
                            "risk": getattr(req, "danger", "high"),
                            "ttl_ms": getattr(req, "ttl_ms", None),
                            "session_id": getattr(req, "session_id", "")})
        out.sort(key=lambda x: x["approval_id"])
        return {"pending": out, "count": len(out)}

    async def budget_dashboard(self, sid: str) -> dict:
        """预算仪表盘(只读派生:llm.usage 事件折叠,零写;无预算闸 → disabled 标志)。"""
        log_ = await self._require_session(sid)
        usages = [e for e in log_.events_after() if e.type == "llm.usage"]
        used_in = sum(int(u.payload.get("in_tokens", 0)) for u in usages)
        used_out = sum(int(u.payload.get("out_tokens", 0)) for u in usages)
        base = {"sid": sid, "used_in_tokens": used_in, "used_out_tokens": used_out,
                "recent": [dict(u.payload) for u in usages[-20:]]}
        budget = getattr(self.ctx, "budget", None)
        snap = getattr(budget, "snapshot", None)
        if not callable(snap):                  # 无预算闸装配(CFG-601 行 → disabled)
            return {**base, "disabled": True, "reason": "budget-gate-unassembled",
                    "limit_cny": None, "used_cny": None,
                    "warned": False, "ratio": 0.0}
        st = snap(sid)
        if hasattr(st, "__await__"):
            st = await st
        limit = float(getattr(st, "limit_cny", 0) or 0)
        used = float(getattr(st, "used_cny", 0) or 0)
        ratio = (used / limit) if limit else 0.0
        return {**base, "disabled": False, "limit_cny": limit, "used_cny": used,
                "warned": ratio >= WARN_RATIO, "ratio": round(ratio, 4)}

    async def upload_attachment(self, body: dict = Body(default={})) -> dict:
        """F061 图片上传入口(桌面界面可选):需已开会话 + 附件载荷合法 → 入队投影。"""
        body = body or {}
        sid = body.get("sid")
        log_ = await self._require_session(sid)
        await self._append_attachments(log_, [body.get("attachment", {})])
        return {"ok": True, "sid": str(sid)}

    # ============================================================ 优雅停服
    def shutdown_gracefully(self) -> None:
        """窗口关闭 = 优雅停服(F065 边界;幂等:双击关闭只走一次)。

        序:stopping 置位 → 停事件 fan-out → hub 关闭(SSE 客户端收 close)→ 兜底 flush
        (强同步事件已随 append 落盘)→ server.should_exit=True 停 uvicorn → webview.destroy。
        幂等判据用 _graceful_done(伪码 `if stopping.is_set(): return` 会在 closing 事件
        先置位时让首次 graceful 空转——伪码缺陷,以行为契约修正,见模块 docstring)。
        """
        if self._graceful_done:
            return
        self._graceful_done = True
        self.stopping.set()
        bus = getattr(self.ctx, "bus", None)
        if bus is not None and self._sub:
            try:
                bus.unsubscribe_all("desktop")      # 停事件 fan-out
            except Exception:                       # noqa: BLE001
                pass
        self._sub = None
        self.hub.close_all()                        # SSE 客户端收 close,前端可重开
        self._flush_persistence()                   # 兜底 flush(正常路径事件已落盘)
        if self.server is not None:
            try:
                self.server.should_exit = True      # uvicorn 线程自然退出
            except Exception:                       # noqa: BLE001
                pass
        self._destroy_webview()

    def _flush_persistence(self) -> None:
        """兜底 flush:ctx.persistence.flush_sync(装配面)/SessionStore.flush/管理器收尾。

        正常路径强同步三类事件已随 append 落盘(F011),此处只兜底攒批缓冲;同步面直接
        调,异步面无 loop 可 await(uvicorn 事件循环在独立线程)→ 记日志降级不阻断退出。
        """
        ctx = self.ctx
        persist = getattr(ctx, "persistence", None)
        if persist is not None:
            flush_sync = getattr(persist, "flush_sync", None)
            fn = flush_sync if callable(flush_sync) else (
                getattr(persist, "flush", None) if callable(getattr(persist, "flush", None))
                else None)
            if fn is not None:
                try:
                    res = fn()
                    if inspect.isawaitable(res):
                        log.debug("desktop 收尾 flush 为异步面,交由装配层定时刷盘")
                except Exception:                       # noqa: BLE001 兜底失败不阻断退出
                    log.warning("desktop 收尾 flush 失败", exc_info=True)
        surface = getattr(self, "_surface", None)
        if isinstance(surface, DesktopSessionManager):
            surface.shutdown_all()

    @staticmethod
    def _destroy_webview() -> None:
        """webview.destroy 兜底(已销毁则忽略,本地异常不跨边界阻止进程退出)。"""
        try:
            wv = _load_webview()
            if wv is not None:
                wv.destroy()
        except Exception:                           # noqa: BLE001
            pass


# ============================================================ pywebview 桥
class DesktopBridge:
    """pywebview js_api:webview 线程方法 → 引擎事件循环跨线程投递(run_coroutine_threadsafe)。

    暴露 window.submit(text)/approve(aid,decision)/replay_frame(sid,from_seq)/
    list_sessions();方法薄壳无业务,返回 JSON 字符串(错误 = code 体 ADR-011)。桥无特权:
    裁决仍过 guard/预算引擎链(单调性高于人类意志)。_pending_calls 登记在途调用供审计。
    """

    def __init__(self, app: DesktopApp) -> None:
        self.app = app
        self._pending_calls: dict[str, float] = {}      # call_id → 发起时刻(在途登记)

    def _run(self, coro: Any) -> str:
        """跨线程投递并阻塞取结果(≤60s);PyHError → code 体;超时 → BUSY(不悬挂)。"""
        app = self.app
        loop = app.loop
        if loop is None or not loop.is_running():
            if inspect.iscoroutine(coro):
                coro.close()                # 未投递协程显式关闭,防 'never awaited' 告警
            return json.dumps({"code": "BUSY",
                               "advice": "引擎服务未就绪(loop 未运行),稍后再试"},
                              ensure_ascii=False)
        call_id = uuid.uuid4().hex[:8]
        self._pending_calls[call_id] = time.monotonic()
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            data = fut.result(timeout=BRIDGE_TIMEOUT_S)
            return json.dumps(_jsonable(data), ensure_ascii=False)
        except PyHError as e:                           # 引擎错:远端只回 code+advice
            advice = (e.ctx.get("advice") or e.ctx.get("hint") or e.spec.advice)
            return json.dumps({"code": e.code, "advice": str(advice)},
                              ensure_ascii=False)
        except asyncio.TimeoutError:                    # 引擎忙:本地错误体(提示稍后再试)
            return json.dumps({"code": "BUSY", "advice": "引擎繁忙,稍后再试"},
                              ensure_ascii=False)
        except Exception as exc:                        # noqa: BLE001 未预期兜底
            log.error("bridge 调用未预期异常", exc_info=True)
            return json.dumps({"code": "CYC-999",
                               "advice": f"{type(exc).__name__}: 引擎内部错误"},
                              ensure_ascii=False)
        finally:
            self._pending_calls.pop(call_id, None)

    # ---------------- js_api 方法(薄壳,无业务;前端 window.<name> 直调)
    def submit(self, sid: str, text: str) -> str:
        """前端回车/发送按钮 → create_message(门面写,无特权)。"""
        return self._run(self.app.create_message(str(sid), {"text": str(text)}))

    def approve(self, aid: Any, decision: str) -> str:
        """审批弹窗按钮(approve/deny;桥无特权:裁决仍过引擎链)。"""
        try:
            aid_int = int(aid)
        except (TypeError, ValueError):
            return json.dumps({"code": "EVT-100", "advice": "aid 须为数字"},
                              ensure_ascii=False)
        return self._run(self.app.decide_approval(aid_int, {"decision": str(decision)}))

    def replay_frame(self, sid: str, from_seq: Any) -> str:
        """轨迹面板逐帧回放取帧(session_timeline 增量续拉)。"""
        try:
            fseq = int(from_seq)
        except (TypeError, ValueError):
            fseq = 0
        return self._run(self.app.session_timeline(str(sid), after_seq=fseq))

    def list_sessions(self) -> str:
        """会话列表(前端启动拉取)。"""
        return self._run(self.app.list_sessions())

    def on_close(self) -> None:
        """窗口关闭事件:优雅停服入口(与 window.events.closing 双保险,幂等)。"""
        self.app.request_shutdown()


# ============================================================ 窗口启动
def _load_webview():
    """惰性导入 pywebview(无显示环境模块导入零副作用;启动路径才需 GUI 运行时)。"""
    try:
        import webview
        return webview
    except Exception as exc:                            # noqa: BLE001
        log.error("pywebview 导入失败(需 WebView2 运行时): %s", exc)
        return None


def _create_window(wv: Any, app: DesktopApp, bridge: DesktopBridge):
    """建窗 + 挂关窗事件(events.closing 置 stopping;webview.start 返回后优雅停服)。"""
    window = wv.create_window(WINDOW_TITLE, app.url, js_api=bridge,
                              width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
    try:
        window.events.closing += lambda: app.request_shutdown()
    except Exception:                                   # noqa: BLE001 假窗/旧版无事件面
        log.debug("window.events.closing 不可用,关窗停服由 bridge.on_close 承担")
    return window


def _bootstrap_desktop(cfg_path: Optional[str]) -> Any:
    """装配桌面 ctx(配置 → 总线 → 门面;CFG-601 失败即中止,不弹窗)。"""
    from pyharness import cli as _cli                   # 复读 cli 装配管线(偏离 3)
    cfg = _cli._load_settings(cfg_path)
    return _cli.assemble_ctx(cfg)


def assemble_desktop_ctx(cfg_path: Optional[str] = None) -> SimpleNamespace:
    """桌面 ctx 装配面(与 cli.assemble_ctx 同形状;session=None 由 DesktopApp 惰性补)。"""
    return _bootstrap_desktop(cfg_path)


def main(argv: Optional[list[str]] = None) -> int:
    """进程入口(pyharness-desktop):装配 ctx → uvicorn 后台线程 → webview 开窗 → 关窗优雅停服。

    返回 int 退出码(0 正常 / 1 启动失败:CFG-601 配置错、端口/WebView2 不可用、就绪超时)。
    argv 未用(桌面无 CLI 参数,保留签名对称)。非主线程调 webview 会 RuntimeError(文档注明)。
    """
    try:
        ctx = _bootstrap_desktop(None)
    except PyHError as e:                               # CFG-601 等装配错
        advice = e.ctx.get("advice") or e.ctx.get("hint") or e.spec.advice
        print(f"{e.code}:{advice}", file=__import__("sys").stderr)
        return 1
    app = DesktopApp(ctx=ctx)
    try:
        port = pick_free_port()
    except OSError:
        log.exception("pick_free_port 失败")
        return 1
    app.url = f"http://{HOST}:{port}"
    threading.Thread(target=run_uvicorn, args=(app, port),
                     name="desktop-uvicorn", daemon=True).start()
    if not wait_until_listening(port):
        log.error("uvicorn 就绪超时 port=%s(不弹空窗,退出 1)", port)
        app.shutdown_gracefully()
        return 1
    wv = _load_webview()
    if wv is None:                                      # WebView2/环境缺失:退 1
        print("pywebview 不可用:请安装 Microsoft Edge WebView2 Runtime 后重试",
              file=__import__("sys").stderr)
        app.shutdown_gracefully()
        return 1
    bridge = DesktopBridge(app)
    app.bridge = bridge
    _create_window(wv, app, bridge)
    try:
        wv.start(debug=False)                           # 阻塞至全部窗口关闭
    except Exception:                                   # noqa: BLE001 窗口生命周期未预期
        log.exception("webview.start 未预期退出")
        app.shutdown_gracefully()
        return 1
    app.shutdown_gracefully()                           # 关窗 = 优雅停服
    return 0


async def run_desktop(ctx: Any) -> int:
    """CLI desktop 子命令桥(cli.py 调用):复用已装配 ctx 走同一 DesktopApp 生命周期。

    等价入口:`uv run pyharness desktop`;桌面必须主线程启动(RuntimeError 域外)。
    """
    app = DesktopApp(ctx=ctx)
    port = pick_free_port()
    app.url = f"http://{HOST}:{port}"
    threading.Thread(target=run_uvicorn, args=(app, port),
                     name="desktop-uvicorn", daemon=True).start()
    await wait_listening_async(app, port)
    wv = _load_webview()
    if wv is None:
        app.shutdown_gracefully()
        raise_code("CYC-999", hint="pywebview 不可用(需 WebView2 Runtime)")
    bridge = DesktopBridge(app)
    app.bridge = bridge
    _create_window(wv, app, bridge)
    try:
        wv.start(debug=False)                           # 阻塞至全部窗口关闭(主线程)
    finally:
        app.shutdown_gracefully()
    return 0


__all__ = [
    # 常量
    "WINDOW_TITLE", "WINDOW_WIDTH", "WINDOW_HEIGHT", "HOST",
    "TIMELINE_KINDS", "CHANNEL", "WARN_RATIO",
    # 数据结构
    "TimelineNode", "StreamClient", "EventStreamHub", "DesktopBridge",
    "DesktopApp", "DesktopSessionManager",
    # 纯函数/核心
    "render_timeline_node", "derive_timeline", "approval_node", "redact_args",
    "redact", "pick_free_port", "run_uvicorn", "wait_until_listening",
    "wait_listening_async",
    # 入口
    "main", "run_desktop", "assemble_desktop_ctx",
]
