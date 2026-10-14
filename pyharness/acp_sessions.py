"""Acp sessions: complete migrated implementation."""
from __future__ import annotations
import inspect
from typing import Any, Optional
from pyharness.errors import raise_code

from pyharness.acp_protocol import AcpState, log

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

def _task_enqueued_seq(log_: Any, task_id: str) -> Optional[int]:
    """submit 后定位本任务 task.enqueued 的 seq(chat seq_range 下界)。"""
    for env in _iter_log(log_):
        if env.type == "task.enqueued" and env.payload.get("task_id") == task_id:
            return env.seq
    return None                              # 队列异会话(偏离 10)→ 未知下界


async def close_owned_runtimes(ctx):
    runtimes = getattr(ctx, '_acp_runtimes', {})
    errors = []
    for sid, runtime in list(runtimes.items()):
        try:
            await runtime.engine_spine.close()
        except BaseException as exc:
            errors.append(exc)
        else:
            runtimes.pop(sid, None)
    if getattr(ctx, '_acp_owns_llm', False):
        try: await ctx.llm_runtime.aclose()
        except BaseException as exc: errors.append(exc)
    if errors:
        raise BaseExceptionGroup('ACP runtime cleanup failed', errors)
