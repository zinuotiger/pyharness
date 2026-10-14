"""Pure public event/timeline projection; independent of desktop hosting."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from pyharness.events import Envelope
_SENSITIVE_HINTS = ("secret", "token", "apikey", "api_key", "password",
                    "credential", "private", "key")

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


envelope_dict = _env_dict
