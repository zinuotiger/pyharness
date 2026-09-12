"""pyharness/acp.py — ACP 自动化桥:JSON-RPC 2.0 over stdio (specs/acp.py.md 契约;阶段 6)

功能编号:F066(ACP 桥,DSH ACP 的 Python 等价)· F064(CLI acp 子命令装配面)· ADR-005(单进程)。
形态:外部程序(IDE/脚本/桌面端)用 stdin 喂 JSON-RPC 请求行、从 stdout 读响应行的长驻进程;
逐行读、一次一请求**串行**处理(处理完才读下一条);协议独占 stdout(日志一律 stderr);
approve 允许远程人类经桥下发裁决(channel="acp:<client>"),裁决仍走引擎 ApprovalProvider
(guard/预算/防重放语义引擎侧全真,桥无特权);read_events 提供游标式事件订阅(断线重连从
返回的 to_seq 续拉);引擎错误以 F019 码进 error.data.code,协议错误用 JSON-RPC 标准码。

方法面:initialize(能力协商/会话绑定)/ chat(结构化驱动,wait 默认 true 阻塞)/ approve(远程
人类裁决)/ read_events(游标订阅)/ shutdown(净退 0);无 id 请求 = 通知,不响应(仅告警日志,
shutdown 通知置净退旗标)。忙拒:chat 进行中再来 chat → -32000(data.code=BUSY)。

偏离说明(契约=specs/acp.py.md;以下为与既有实现/规格冲突处的取舍,均列理由,与 commands.py/
task_queue.py 同款先例,已入 docstring 供审查):
1. 会话门面未落地:伪码假定 ctx.session 提供 async create()/open_session(sid)(阶段 6 cli
   bootstrap 尚未实现;仓库既有真面 = core/session.SessionLog + 模块函数 open_session(sid,
   persistence))→ 本实现按鸭子类型支持两种注入形状:(a) 门面(有 open_session/create)→
   委托(未来 bootstrap 注入后与伪码一致);(b) ctx.session 即已绑定会话日志(sid 匹配)→ 直用;
   两者皆无 → raise EVT-106 明确报错,**绝不静默自建会话**(防 S-1"伪造已执行")。
2. append 调用面:伪码 `ctx.session.append("user.message", content=text, ...)` 为键参直传假想
   面;真实 SessionLog.append(type, payload: dict, *, actor, ...) 为五步校验链唯一写口 →
   本实现传 payload dict {"content": text}(payload 模型 EVENT-SCHEMA §3.2 权威)。
3. TaskResult 聚合:伪码 res.reason/res.summary()/res.events_of() 为假想队列面;真实
   TaskQueue.wait_for 返回 TaskResult(ok/code/summary/duration_ms)→ reason 映射
   complete/cancelled/error/queued;guard.rejected/approval.* 清单与终局文本按 task_id 从
   会话日志派生(read_events 同真源,INV-02 无第二份状态)。
4. BUSY 码未登记(errors.register_default_codes 无 "BUSY";task_queue.py 同款先例直接构造
   PyHError("BUSY"))且 raise_code 会把未登记码改写为 CYC-999 → 本实现直接构造 PyHError
   ("BUSY"),dispatch 特判 e.code == "BUSY" → -32000(data.code=BUSY),兑现 spec 异常表
   (并发 chat → -32000 而非 -32603)。
5. 通知副作用:伪码"其余方法通知仅执行副作用(如 chat 通知=丢结果)"与伪码本体(仅日志 +
   shutdown 旗标)冲突 → 按伪码本体:通知一律只记告警日志,chat 通知**不触发引擎副作用**
   (无 id 无法回报结果,宁丢请求不丢结果)。
6. 事件信封序列化:真实 Envelope 无 envelope_dict() 面 → model_dump(exclude_none=True)
   (pydantic v2 官方序列化口,字段与 EVENT-SCHEMA §2.1 十字段一致)。
7. approve 等待:伪码 await ctx.approval.approve(...);真实 ApprovalProvider.approve/deny 为
   **同步**裁决入口(裁决事件经其后台任务强同步落盘,与 CLI prompt_approval 同款语义)→
   本实现同步/异步兼容(inspect.isawaitable 判别),返回即"已受理";APR-503/GRD-403 等引擎
   错同步上抛走 engine_error 路径。approval.granted/denied 落盘与响应先后由 provider 后台
   任务保证(与 cli 语义一致,非桥可改)。
8. repair 前置未落地:伪码 ensure_repaired(ctx, sid)(pyharness.repair 模块尚不存在)→ 本实现
   预留 ctx.repair.ensure_repaired(sid) 扩展点,未装配即跳过(PERS-201 坏行仍由 open_session
   回放记跳隔离,不阻断;真装配注入后自动生效)。
9. 忙拒可达性:serve 串行读循环下第二请求不可能在首 chat 完成前被读到 → st.busy 为防御性
   不变量(并发/嵌入调用方经 dispatch 直入时生效),非串行主循环路径;单测经 st.busy 预置覆盖。
10. 会话同柄假设:桥内 append/队列/事件读取共用同一会话实例(st._bound_log 缓存,INV-03 缓存
    一致防双实例漂移);多会话切换的队列重绑属未来 bootstrap 装配职责,不在桥内越权。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional, TextIO, Union

from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.acp")

# ------------------------------------------------------------ 协议常量
PROTOCOL_VERSION = "1.0"
BRIDGE_METHODS: list[str] = ["initialize", "chat", "approve",
                             "read_events", "shutdown"]
MAX_EVENT_LIMIT = 500          # read_events 单批上限(锁死)
DEFAULT_EVENT_LIMIT = 200      # read_events 缺省批量

# 热路径引擎码:错误只留 code+advice,不加 detail(ADR-011 远端最小化)
_HOT_CODES = frozenset({"GRD-401", "GRD-403", "LLM-310", "PERS-202"})

# 疑似敏感键名(redact 命中即打码,INV-09 不泄密)
_SENSITIVE_KEY_HINTS = ("secret", "token", "apikey", "api_key", "password",
                        "credential", "private", "key")


# ------------------------------------------------------------ 数据结构
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


# ------------------------------------------------------------ 流式 IO 辅助
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


# ------------------------------------------------------------ 响应构造
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


# ------------------------------------------------------------ 会话门面适配
async def _maybe_await(res: Any) -> Any:
    """同步/异步兼容调用(引擎各面注入形状不一,commands.py/task_queue.py 同款先例)。"""
    if inspect.isawaitable(res):
        return await res
    return res


async def _create_session(ctx: Any) -> str:
    """新建会话(initialize 无 sessionId 路径):委托 ctx.session.create()。

    门面缺 create(如 ctx.session 即已绑定日志)→ EVT-106 明确报错(偏离 1:
    绝不静默自建,防伪造会话状态)。
    """
    mgr = getattr(ctx, "session", None)
    fn = getattr(mgr, "create", None)
    if not callable(fn):
        raise_code("EVT-106", hint="会话门面未装配 create(桥无法新建会话);"
                   "显式传 sessionId 绑定既有会话")
    sid = await _maybe_await(fn())
    if not isinstance(sid, str) or not sid.strip():
        raise_code("EVT-106", session_id=sid,
                   hint="会话门面 create 返回非法 sid(须非空字符串)")
    return sid


async def _open_session(ctx: Any, sid: str) -> Any:
    """绑定既有会话(重放重建):委托 ctx.session.open_session(sid)。

    两种注入形状(偏离 1):(a) 门面 open_session → 委托;(b) ctx.session 即已绑定
    会话日志且 sid 匹配 → 直用;均不可达 → EVT-106(先 initialize 或注入门面)。
    """
    mgr = getattr(ctx, "session", None)
    if mgr is None:
        raise_code("EVT-106", session_id=sid,
                   hint="会话未装配(ctx.session=None);先 initialize")
    fn = getattr(mgr, "open_session", None)
    if callable(fn):
        return await _maybe_await(fn(sid))
    if getattr(mgr, "sid", None) == sid:   # ctx.session 即当前会话日志
        return mgr
    raise_code("EVT-106", session_id=sid,
               hint="会话门面未装配 open_session 且 ctx.session 未绑定该 sid;"
                    "先 initialize 或注入门面")


async def _ensure_bound(ctx: Any, st: AcpState,
                        sid_param: Any) -> tuple[Any, str]:
    """解析当前操作会话:同柄复用,异柄重开;未初始化/无参数 → EVT-106。"""
    sid = sid_param if sid_param is not None else st.session_id
    if not sid:
        raise_code("EVT-106", hint="会话未就绪:先 initialize"
                   "(或请求带 sessionId)")
    if not isinstance(sid, str) or not sid.strip():
        raise_code("EVT-100", field="sessionId", session_id=sid,
                   hint="sessionId 须非空字符串")
    log_ = st._bound_log                     # 同柄缓存(INV-03,偏离 10)
    if log_ is None or getattr(log_, "sid", None) != sid:
        log_ = await _open_session(ctx, sid)
        st._bound_log = log_
    return log_, sid


def _closed_of(log_: Any) -> bool:
    """会话是否终态:stats()["closed"] 权威;缺 stats 面回退私有位(纯只读判读)。"""
    stats = getattr(log_, "stats", None)
    if callable(stats):
        try:
            return bool(stats().get("closed", False))
        except Exception:                    # noqa: BLE001 门面异常 → 视为未终态
            pass
    return bool(getattr(log_, "_closed", False))


def _last_seq_of(log_: Any) -> int:
    """会话当前最大 seq(seq_range 上界;stats 缺面回退尾事件遍历)。"""
    stats = getattr(log_, "stats", None)
    if callable(stats):
        try:
            return int(stats().get("seq", 0) or 0)
        except Exception:                    # noqa: BLE001
            pass
    last = 0
    for env in _iter_log(log_):
        last = env.seq
    return last


def _iter_log(log_: Any):
    """会话事件迭代(events_after(0) 全量;门面无该面时为空)。"""
    ea = getattr(log_, "events_after", None)
    if callable(ea):
        yield from ea(0)
    return


def _env_dict(env: Any) -> dict:
    """Envelope → 协议事件 dict(偏离 6:model_dump 序列化,十字段全量)。"""
    dump = getattr(env, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    # 兜底:手工投影(非 pydantic 信封的注入替身)
    return {k: getattr(env, k) for k in
            ("seq", "ts", "type", "session_id", "actor", "origin",
             "task_id", "payload", "trace")
            if getattr(env, k, None) is not None}


# ------------------------------------------------------------ 方法实现
async def _ensure_repaired(ctx: Any, sid: str) -> None:
    """启动/绑定会话的 repair 前置(F060;偏离 8:pyharness.repair 未落地)。

    装配方注入 ctx.repair.ensure_repaired(sid) 后自动生效(损坏先修再 open);
    未装配 = 跳过——PERS-201 坏行仍由 open_session 回放记跳隔离,不阻断启动。
    """
    repair = getattr(ctx, "repair", None)
    fn = getattr(repair, "ensure_repaired", None)
    if callable(fn):
        await _maybe_await(fn(sid))


def _set_channel(ctx: Any, st: AcpState) -> None:
    """ctx.channel = "acp:<client_id>":桥即人类审批通道(非 headless)。

    命令面/审批面据此判定交互通道存在(R8/APR-501);仅当 ctx 允许赋值时生效
    (未装配/只读 ctx 静默跳过,装配层语义由 bootstrap 保证)。
    """
    try:
        ctx.channel = f"acp:{st.client_id}"
    except Exception:                        # noqa: BLE001 只读门面:跳过
        log.debug("ctx.channel 赋值失败(只读门面?),bridge=%s", st.client_id)


def _engine_model(ctx: Any) -> str:
    """能力清单 model:ctx.config.llm.model(未装配配置 → 空串,不阻塞协商)。"""
    cfg = getattr(ctx, "config", None)
    llm = getattr(cfg, "llm", None)
    model = getattr(llm, "model", None)
    return str(model) if model else ""


async def cmd_initialize(ctx: Any, st: AcpState, params: dict) -> dict:
    """能力协商(initialize):能力清单 + 会话绑定/新建 + 通道确立。

    sessionId 参数 → 绑定既有会话(repair 前置 → open → 终态 EVT-104 拒);
    缺省 → 新建会话(session.created 由门面 create 落,created=True)。
    st.cursor 复位 0(新连接游标从 0 起);ctx.channel = "acp:<client>"。
    """
    params = params or {}
    sid = params.get("sessionId")
    if sid is not None and (not isinstance(sid, str) or not sid.strip()):
        raise_code("EVT-100", field="sessionId", session_id=sid,
                   hint="sessionId 须非空字符串(缺省则新建会话)")
    if sid:
        await _ensure_repaired(ctx, sid)      # 损坏先 repair(F060 前置)
        log_ = await _open_session(ctx, sid)  # 绑定既有(重放重建)
        if _closed_of(log_):                  # 终态会话不可续 chat
            raise_code("EVT-104", session_id=sid,
                       advice="会话已终态(session.finished);请另开新 sessionId")
        created = False
    else:
        sid = await _create_session(ctx)      # 新建会话(created seq=1)
        created = True
    st.session_id = sid
    st.cursor = 0
    st._bound_log = None                      # 绑定柄下次操作惰性解析
    _set_channel(ctx, st)                     # 桥 = 审批通道(非 headless)
    return {"protocolVersion": PROTOCOL_VERSION,
            "methods": list(BRIDGE_METHODS),
            "events": {"cursor": True},
            "session": {"id": sid, "created": created},
            "engine": {"model": _engine_model(ctx),
                       "headless_channel": False}}


def _task_enqueued_seq(log_: Any, task_id: str) -> Optional[int]:
    """submit 后定位本任务 task.enqueued 的 seq(chat seq_range 下界)。"""
    for env in _iter_log(log_):
        if env.type == "task.enqueued" and env.payload.get("task_id") == task_id:
            return env.seq
    return None                              # 队列异会话(偏离 10)→ 未知下界


async def _ensure_engine_queue(ctx: Any, log_: Any, sid: str) -> Any:
    """ACP 会话队列惰性装配:轻量 ctx(无队列/异会话队列)时接真实引擎。

    原 cli.acp 只注入轻量门面,队列/runner 从未接线;这里把 engine 装配
    原语搬到 ACP chat 前,使 initialize → chat 也能走真实 AgentLoop。
    """
    cfg = getattr(ctx, "settings", None) or getattr(ctx, "config", None)
    q = getattr(ctx, "task_queue", None)
    if q is not None and (not hasattr(q, "_session")
                          or getattr(q, "_session", None) is log_):
        return q
    if cfg is None:
        return q
    from pathlib import Path
    from pyharness import engine as _eng
    storage = getattr(ctx, "storage", None)
    sessions_dir = getattr(storage, "sessions_dir", None)
    if not sessions_dir:
        try:
            sessions_dir = cfg.storage.sessions_dir
        except Exception:                    # noqa: BLE001 配置缺键:不装配
            return q
    await _eng.attach_engine_to_ctx(
        ctx, cfg, log_=log_,
        sessions_dir=Path(str(sessions_dir)).expanduser(),
        bus=getattr(ctx, "bus", None),
        store=getattr(log_, "_persistence", None))
    return getattr(ctx, "task_queue", None)


def _chat_summary(log_: Any, task_id: str, enq_seq: Optional[int],
                  res: Any, reason: str) -> dict:
    """阻塞 chat 终局摘要(偏离 3:从会话日志派生,无第二份状态)。

    final:TaskResult.summary 优先 → 任务段内最后 agent.message/llm.response
    内容兜底 → 终态码文本;rejected = 段内 guard.rejected payload 清单;
    approvals = 段内 approval.* payload 清单(事件带 task_id 或窗口内无主事件)。
    """
    after = enq_seq if enq_seq is not None else 0
    final = res.summary if getattr(res, "summary", None) else None
    rejected: list[dict] = []
    approvals: list[dict] = []
    for env in _iter_log(log_):
        if env.seq <= after:
            continue
        tid = env.task_id
        if tid is not None and tid != task_id:
            continue                          # 异任务事件隔离(串行队列下少见)
        if env.type == "guard.rejected":
            rejected.append(dict(env.payload))
        elif env.type.startswith("approval."):
            approvals.append(dict(env.payload))
        elif final is None and env.type in ("agent.message", "llm.response"):
            content = (env.payload or {}).get("content")
            if content:
                final = content               # 最后一条非空展示文本即终局
    if final is None:
        final = str(getattr(res, "code", None) or reason)
    return {"final": final, "rejected": rejected, "approvals": approvals}


async def cmd_chat(ctx: Any, st: AcpState, params: dict) -> dict:
    """结构化驱动主方法(chat,F066):wait=true 阻塞至终态,wait=false 秒回 task_id。

    忙锁:st.busy 期间再来 chat → PyHError("BUSY")(dispatch 特判 -32000,偏离 4);
    空 text → EVT-100;会话经 sessionId 显式指定或 initialize 绑定;user.message
    强同步落盘(origin="acp:<client>",审计同 CLI)后 submit → wait_for。
    """
    params = params or {}
    if st.busy:                               # 防御性忙拒(偏离 9;并发入口生效)
        raise PyHError("BUSY", ctx={"advice":
                                    "上一条 chat 未完成;等 read_events 就绪或 wait=false"})
    text = str(params.get("text") or "").strip()
    if not text:
        raise_code("EVT-100", field="text",
                   advice="text 必填(非空字符串)")
    log_, sid = await _ensure_bound(ctx, st, params.get("sessionId"))
    append = getattr(log_, "append", None)
    if not callable(append):
        raise_code("EVT-106", session_id=sid,
                   hint="会话无 append 写面(只读投影);无法发起 chat")
    queue = await _ensure_engine_queue(ctx, log_, sid)
    if queue is None or not callable(getattr(queue, "submit", None)):
        raise_code("CYC-999", hint="桥未装配任务队列(ctx.task_queue.submit)")
    st.busy = True
    try:
        # 强同步 user.message(审计同 CLI);origin 记桥通道溯源
        await _maybe_await(append("user.message", {"content": text},
                                  actor="user", origin=f"acp:{st.client_id}",
                                  sync=True))
        task_id = await _maybe_await(queue.submit(
            text, meta={"channel": f"acp:{st.client_id}", "session_id": sid}))
        enq_seq = _task_enqueued_seq(log_, task_id)
        if not params.get("wait", True):      # 异步:read_events 跟进结果
            return {"task_id": task_id, "reason": "queued", "summary": {},
                    "seq_range": [enq_seq, enq_seq] if enq_seq is not None else None}
        wait_for = getattr(queue, "wait_for", None)
        if not callable(wait_for):
            raise_code("CYC-999", hint="队列未装配 wait_for;任务已入队"
                       "(任务仍会执行,结果经 read_events 消费)")
        res = await _maybe_await(wait_for(task_id))     # 阻塞至终态(串行语义)
        reason = ("complete" if res.ok else
                  ("cancelled" if getattr(res, "code", None) == "cancelled"
                   else "error"))
        summary = _chat_summary(log_, task_id, enq_seq, res, reason)
        hi = _last_seq_of(log_)
        seq_range = [enq_seq, hi] if enq_seq is not None else None
        return {"task_id": task_id, "reason": reason,
                "summary": summary, "seq_range": seq_range}
    finally:
        st.busy = False


async def cmd_approve(ctx: Any, st: AcpState, params: dict) -> dict:
    """远程人类审批(approve,F066):approval_id + decision 交裁决入口。

    by 缺省 "acp:<client>";裁决仍走 ctx.approval 引擎裁决入口(approve/deny 同步
    面,偏离 7:await 兼容),guard 重入链/防重放(APR-503)/预算语义引擎侧全真,
    桥无特权。裁决结果事件由 provider 强同步落盘后,read_events 可读。
    """
    params = params or {}
    aid = params.get("approval_id")
    decision = params.get("decision")
    if not isinstance(aid, int) or isinstance(aid, bool):
        raise_code("EVT-100", field="approval_id", approval_id=aid,
                   hint="approval_id 须 int(审批请求事件 seq)")
    if decision not in {"approve", "deny"}:
        raise_code("EVT-100", field="decision", decision=decision,
                   hint="decision 须 approve|deny")
    by = params.get("by")
    if by is not None and (not isinstance(by, str) or not by.strip()):
        raise_code("EVT-100", field="by", hint="by 须非空字符串(缺省 acp:<client>)")
    by = by or f"acp:{st.client_id}"
    provider = getattr(ctx, "approval", None)
    fn = getattr(provider, decision, None)    # approve/deny 同构入口
    if not callable(fn):
        raise_code("CYC-999", hint="桥未装配审批裁决入口(ctx.approval.approve/deny)")
    await _maybe_await(fn(aid, by=by))        # APR-503/GRD-403 等同步上抛
    return {"ok": True, "approval_id": aid, "decision": decision}


async def cmd_read_events(ctx: Any, st: AcpState, params: dict) -> dict:
    """事件游标订阅(read_events):从游标读持久事件批,返回 to_seq 供断线续拉。

    after_seq/cursor 显式或取 st.cursor(单调推进);limit ≤500(锁死);空批
    from_seq=to_seq=当前游标;瞬时事件(如 llm.chunk)天然不在日志(append 拒写),
    本方法只给持久事件——真源派生,只追加流按 seq 序。
    """
    params = params or {}
    log_, sid = await _ensure_bound(ctx, st, params.get("sessionId"))
    after_raw = params.get("after_seq", params.get("cursor", st.cursor))
    if not isinstance(after_raw, int) or isinstance(after_raw, bool):
        raise_code("EVT-100", field="after_seq", value=after_raw,
                   hint="after_seq/cursor 须 int(seq 游标)")
    after = max(0, after_raw)
    try:
        limit = int(params.get("limit", DEFAULT_EVENT_LIMIT))
    except (TypeError, ValueError):
        raise_code("EVT-100", field="limit", value=params.get("limit"),
                   hint=f"limit 须 int(≤{MAX_EVENT_LIMIT})")
    limit = max(0, min(limit, MAX_EVENT_LIMIT))
    batch: list[dict] = []
    last = after
    ea = getattr(log_, "events_after", None)
    if limit > 0 and callable(ea):
        for env in ea(after_seq=after):       # 只追加流按 seq 序
            batch.append(_env_dict(env))
            last = env.seq
            if len(batch) >= limit:
                break
    st.cursor = max(st.cursor, last)          # 游标只进不退(单连接单调)
    return {"from_seq": after, "to_seq": last, "events": batch,
            "has_more": len(batch) == limit and limit > 0}


# ------------------------------------------------------------ 通知与分派
async def notify(ctx: Any, st: AcpState, req: JsonRpcRequest) -> None:
    """通知(无 id)处理:不响应;shutdown 通知置净退旗标(serve 尾检查退出)。

    其余方法通知只记告警日志,**不触发引擎副作用**(偏离 5:无 id 无法回报
    结果,chat 通知=结果无处安放,宁丢请求不丢结果)。
    """
    log.warning("acp 收到通知(无 id),不响应: %s", req.method)
    if req.method == "shutdown":
        st.notify_shutdown = True             # serve 循环检查后 return 0


async def dispatch(ctx: Any, st: AcpState, req: JsonRpcRequest) -> dict:
    """方法分派:四法 + shutdown;未知 → -32601;错误全走标准码(禁裸 raise 跨出)。"""
    m, p, rid = req.method, req.params, req.id
    try:
        if m == "initialize":
            r = await cmd_initialize(ctx, st, p)
        elif m == "chat":
            r = await cmd_chat(ctx, st, p)          # 唯一长驻;锁下执行
        elif m == "approve":
            r = await cmd_approve(ctx, st, p)
        elif m == "read_events":
            r = await cmd_read_events(ctx, st, p)
        elif m == "shutdown":
            st.notify_shutdown = True               # 回响应后净退 0
            return {"jsonrpc": "2.0", "id": rid, "result": {"shutdown": True}}
        else:
            return error_resp(rid, -32601, "Method not found", {"method": m})
        return {"jsonrpc": "2.0", "id": rid, "result": r}
    except PyHError as e:
        if e.code == "BUSY":                        # 偏离 4:忙拒 → 标准码 -32000
            return error_resp(rid, -32000, "Busy",
                              {"code": "BUSY", "advice": e.ctx.get("advice")})
        return engine_error(rid, e)                 # F019 码进 data.code
    except asyncio.TimeoutError:
        return error_resp(rid, -32000, "Busy/Timeout", {"code": "BUSY"})
    except Exception as e:                          # noqa: BLE001 未预期兜底
        log.error("acp dispatch 未预期异常 method=%s", m, exc_info=True)
        return error_resp(rid, -32603, "Internal error",
                          {"code": "CYC-999", "detail": f"{type(e).__name__}: {e}"[:300]})


# ------------------------------------------------------------ 行解析
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


# ------------------------------------------------------------ 主循环
async def serve(ctx: Any, *, client_id: Optional[str] = None,
                stdin: Any = None, stdout: Any = None) -> int:
    """桥主循环(F066 验收直译):逐行读 stdin,一次一请求串行,净退 0。

    client_id 缺省 "acp:<pid>";stdin/stdout 缺省 sys.stdin/sys.stdout(单测注入
    StringIO 替身);EOF/空行 → 净退 0;shutdown(请求回响应后/通知)净退 0;
    OSError(stdin/stdout 断)→ 记日志退 1;空行容忍(编辑器/echo 尾随换行)。
    """
    st = AcpState(client_id=client_id or f"acp:{os.getpid()}")
    inp = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    try:
        while True:
            line = await _readline_async(inp)   # EOF → None → 净退 0
            if line is None:
                return 0
            line = line.strip()
            if not line:
                continue                        # 空行容忍
            req = parse_request(line, write=out)
            if req is None:
                continue                        # 协议错已回标准码
            if req.id is None:                  # 通知:处理不响应(推进净退旗标)
                await notify(ctx, st, req)
                if st.notify_shutdown:
                    return 0
                continue
            resp = await dispatch(ctx, st, req)  # 一次一请求串行
            _write_line(out, resp)
            if st.notify_shutdown:               # shutdown 请求已回响应 → 净退 0
                return 0
    except OSError as e:
        log.error("acp stdin/stdout 断(OSError): %s", e)
        return 1


class AcpBridge:
    """外壳装配面(cli.py.md:AcpBridge(ctx, channel="acp:cli").serve())。

    channel 形如 "acp:cli"/"acp:<client>":serve 时剥 "acp:" 前缀作 client_id
    (缺省 client_id 为 "acp:<pid>" 语义保持)。
    """

    def __init__(self, ctx: Any, *, channel: Optional[str] = None) -> None:
        self.ctx = ctx
        self.channel = channel

    async def serve(self, *, client_id: Optional[str] = None,
                    stdin: Any = None, stdout: Any = None) -> int:
        """委托模块 serve;未显式 client_id 时取 channel 后缀。"""
        cid = client_id
        if cid is None and self.channel:
            cid = (self.channel[4:] if str(self.channel).startswith("acp:")
                   else str(self.channel))
        return await serve(self.ctx, client_id=cid,
                           stdin=stdin, stdout=stdout)


__all__ = [
    "BRIDGE_METHODS", "PROTOCOL_VERSION", "JsonRpcRequest", "AcpState",
    "serve", "AcpBridge", "parse_request", "dispatch", "notify",
    "cmd_initialize", "cmd_chat", "cmd_approve", "cmd_read_events",
    "engine_error", "error_resp",
]
