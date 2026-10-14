"""Engine runner: complete migrated implementation."""
from __future__ import annotations
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, TYPE_CHECKING
from pyharness.core.task_runner import TaskLifecycle
if TYPE_CHECKING:
    pass
from pyharness.core.agent import create_agent
from pyharness.errors import PyHError

from pyharness.engine_types import EngineRunner, EngineSpine, log
from pyharness.engine_plugins import _cfg_of, _preload_plugins

def _owned_task_message(log_: Any, task: Any) -> Optional[Any]:
    """严格归属窗口:取 (上一个 task.enqueued, 本任务 enqueued] 内最后一条 user.message。

    修复(P1-1):旧实现取"enqueued 之前最后一条 user.message",会把 plan 步骤的
    /plan 旧消息顶给步骤执行(plan_mode 提交纯意图,不落 user.message),并发下还
    会让 schedule 任务拿到前台消息。窗口为空(纯意图任务)→ None,由调用方按
    task.intent 补写信号消息。只读遍历事件源,零副作用。
    """
    mark = int(getattr(task, "enqueued_seq", 0) or 0)
    if mark <= 0:
        return None
    prev_enq = 0
    env = None
    for e in log_.events_after(0):
        seq = int(getattr(e, "seq", 0) or 0)
        if seq > mark:
            break
        if e.type == "task.enqueued":
            if seq < mark:
                prev_enq = seq
                env = None                      # 新窗口:清空旧归属(前窗口消息不复用)
            continue                             # 本任务自身 enqueued:非消息
        if e.type == "user.message" and seq > prev_enq:
            env = e
    return env

def make_runner(spine: EngineSpine, *, preload_plugins=_preload_plugins) -> EngineRunner:
    """task_queue runner seam:async run_for_task(task) → loop.wake(env)。

    task 的配套 user.message 已由 schedule.trigger/前台 submit 落盘(seq 已知);
    经严格归属窗口从事件源找该 envelope,窗口为空则按 task.intent 补写,交
    loop.wake 驱动真实处理。
    """
    log_ = spine.session
    loop = spine.loop

    async def run_for_task(task: Any) -> Any:
        # GAP-13:攒批落盘定时器惰性启动(幂等)。放在此处而非装配段,是因为装配
        # 段是同步函数、且此时可能尚无运行中的事件循环——本 seam 是 chat/run 的
        # 唯一生产驱动点,循环必已就绪。
        try:
            await spine.start_flush_ticker()
        except Exception as e:                 # noqa: BLE001 定时器起不来不阻断任务
            log.warning("flush ticker 启动失败: %s", type(e).__name__)
        # 懒预载:created 已落后首跑前装载示例插件(util.now;EVT-106 首事件
        # 约束 → 不可在 open_session 后立即 append,故延迟到 submit 时刻)
        if not getattr(spine, "_plugins_ready", False):
            try:
                await preload_plugins(spine)
            except Exception as e:             # noqa: BLE001 预载失败不阻断
                log.debug("plugin lazy preload failed: %s", type(e).__name__)
            spine._plugins_ready = True
        # 装配期策略留痕(F-01):**必须在 session.created 之后**发射——装配函数
        # (_build_governance/activate_orchestration)运行时 seq=0,直接 append 会被
        # EVT-106 拒("事件须在 session.created 之后")并让整个装配抛出。本处是
        # 生产唯一驱动点(chat/run 均经 task_queue → 本 seam),此时会话已开启。
        # 幂等闸保证每 spine 恰一条;发射失败不阻断任务(与上方插件预载同款)。
        if not getattr(spine, "_policy_announced", False):
            _pol = getattr(getattr(spine, "governance", None), "policy", None)
            if _pol is not None:
                try:
                    await _pol.emit_assembled(reason="assembly")
                except Exception as e:         # noqa: BLE001 治理留痕失败不阻断任务
                    log.warning("policy.updated 装配留痕失败: %s", type(e).__name__)
            spine._policy_announced = True
        # 崩溃遗留 job 的**事件级**恢复(B1/R24):补写 `job.failed(reason=crash)`。
        # 放在本 seam(而非装配期):装配期 seq=0,append 会被 EVT-106 拒;此处是生产
        # 唯一驱动点且会话已开启 —— 与上方策略留痕同款。幂等闸保证每 spine 恰一次;
        # 失败只记日志不阻断任务(恢复属收尾)。
        if not getattr(spine, "_jobs_recovered", False):
            jobs = getattr(spine, "jobs", None)
            rec = getattr(jobs, "recover", None)
            if callable(rec):
                try:
                    await rec()
                except Exception as e:             # noqa: BLE001 恢复失败不阻断
                    log.warning("job 崩溃恢复失败: %s", type(e).__name__)
            spine._jobs_recovered = True
        intent = str(getattr(task, "intent", "") or "").strip()
        env = _owned_task_message(log_, task)   # 严格归属窗口(P1-1)
        if env is None and intent:
            # 窗口内无配套 user.message(plan 步等纯意图任务):意图本身即待执行
            # 指令,按 schedule 前例落 user.message(system 署名)驱动循环——
            # 提交者不落 message 则 agent 无从 derive 到该任务(schedule._fire 同款)。
            env = await log_.append("user.message", {"content": intent},
                                    actor="system",
                                    origin=f"task:{getattr(task, 'id', '')}",
                                    task_id=getattr(task, "id", None),
                                    sync=True)
        if env is None:
            raise PyHError("CYC-999", ctx={"hint": "runner 找不到对应 user.message 事件"})
        ag_ctx = await _agent_ctx_of(spine, env)
        # 当前任务关联(F044 弱耦合字符串引用;2026-09-21 R11-6 修):
        # goal/todo 工具与 llm.chunk 出口经 ``ctx.task_id`` 取"当前任务",而此前
        # **全库无任何写入点** ⇒ 恒 None ⇒ ``goal`` 的"所关联任务失败 → 目标状态
        # 推导"(goal.py:357)与"关联任务"渲染**从未生效**。任务在同一会话内串行
        # (单 running),故按任务边界设置/清除安全;每个会话(含子会话)各自持 ctx,
        # 不跨会话串。异常/取消路径经 finally 还原。
        prev_task_id = getattr(ag_ctx, "task_id", None)
        ag_ctx.task_id = getattr(task, "id", None)
        # KF-C(A4/R24):把**框架侧**"本轮来源"提到 ctx —— 到点触发那一轮此前**无任何
        # 通道**告知模型"这是定时触发" ⇒ 模型只会复述任务状态("提醒已设好"),而不是
        # 产出提醒本身。取值只取 `task.meta.source`(**受控**:由 schedule._fire 写入),
        # 不取任何模型/工具文本(注入防御);由 system_prompt 的受控段渲染。
        prev_src = getattr(ag_ctx, "turn_source", None)
        ag_ctx.turn_source = _turn_source_of(task)
        try:
            lifecycle: TaskLifecycle | None = getattr(spine, 'task_lifecycle', None)
            if lifecycle is None:
                res = await loop.wake(env, ctx=ag_ctx)
            else:
                res = await lifecycle.run_task(env, ag_ctx, lambda: loop.wake(env, ctx=ag_ctx))
        finally:
            ag_ctx.task_id = prev_task_id
            ag_ctx.turn_source = prev_src
        # F042/#7 自动标题:run 正常完成后命名一次(幂等;LLM 失败静默留空)
        if (res is not None and getattr(res, "reason", "") == "complete"
                and not getattr(spine, "_auto_titled", False)):
            try:
                from pyharness.core.auto_title import auto_title
                if await auto_title(ag_ctx):
                    spine._auto_titled = True
            except Exception as e:             # noqa: BLE001 命名失败不阻断对话
                log.debug("auto_title failed: %s", type(e).__name__)
        # 证据归档**不在此处**:触发点已移到段生命周期(segment.end 订阅,见
        # ``_evidence_producer``)。原因:本函数在五种终态中的三种(异常/预算/取消)
        # 不执行到收尾,放这里会漏归档;且此处早于段关闭(旧 N3)。
        return res

    async def cancel_current(task_id: Optional[str] = None) -> None:
        cur = loop.current
        if cur is not None:
            cur.cancelled = True
            # 协作式:由 loop 轮内 CancelledError 传播收尾
    return EngineRunner(run_for_task=run_for_task,
                        cancel_current=cancel_current)

async def archive_task_evidence(spine: EngineSpine, task_id: str,
                                ctx: Any = None) -> Optional[str]:
    """任务段治理证据归档——``EvidenceCollector`` 的**生产生产者**(GAP-8)。

    修复前:``EvidenceCollector`` 已订阅真总线(4 类事件),但**唯一写点**
    ``archive()`` 在全库**零调用** ⇒ ``evidence.archived`` 永不产生、索引恒空、
    ``collect_for_task()`` 恒返回 ``()``。

    **产生规则(冻结)**——这是一个**设计裁定**,不是"为了有调用点而调用":

    1. **单位 = 任务段**(``task_id``)。段是回放/复算/预算审计的最小单位(F044),
       也正是 ``collect_for_task`` 的聚合键;证据按段组织才有查询语义。
    2. **触发条件 = 段内至少一条 ``decision.issued``**。治理证据回答的是"这次
       授权/拒绝发生在什么上下文、依据什么、能否复核",没有治理决策的段**不归档**
       ——"不发生"与"失败"是两件事,不为空段造工件。
    3. **只存引用,不复制事件内容**(INV-G4/E1):refs 指向段锚 + 段内每条决策
       (``decision_id``)与凭证(``receipt_id``)。事件日志仍是唯一真源。
    4. **幂等**:同一 ``task_id`` 只归档一次——判据取自**日志重放**(已存在指向该
       段锚的 ``evidence.archived`` 即跳过),故**重启后重放同样成立**,不依赖内存。
    5. **降级**:治理层未接线(纯内存/单测)、无段锚、引用构造失败 ⇒ 静默跳过,
       绝不阻断任务(证据是派生视图,不是主链前提)。

    返回 ``evidence_id``;未归档(不满足条件)返回 ``None``。
    """
    log_ = getattr(spine, "session", None)
    coll = getattr(getattr(spine, "governance", None), "evidence", None)
    if log_ is None or coll is None:
        return None
    try:
        events = list(log_.events_after(0))
        seg_locator = f"{task_id}:seg"
        # ① 幂等闸:日志重放判定(重启后仍成立)
        for e in events:
            if getattr(e, "type", None) != "evidence.archived":
                continue
            for r in (getattr(e, "payload", None) or {}).get("refs") or []:
                if (isinstance(r, dict) and r.get("kind") == "segment"
                        and str(r.get("locator")) == seg_locator):
                    return None
        # ② 段窗口:本任务 segment.start 之后的全部事件属该段
        start = next((int(e.seq) for e in events
                      if getattr(e, "type", None) == "segment.start"
                      and str((getattr(e, "payload", None) or {})
                              .get("task_id") or "") == str(task_id)), None)
        if start is None:
            return None
        seg = [e for e in events if int(getattr(e, "seq", 0) or 0) >= start]
        decisions = [e for e in seg
                     if getattr(e, "type", None) == "decision.issued"]
        if not decisions:
            return None                       # 无治理事实:不归档(非失败)
        receipts = [e for e in seg
                    if getattr(e, "type", None) == "receipt.emitted"]
        denied = [d for d in decisions
                  if str((getattr(d, "payload", None) or {})
                         .get("verdict") or "") == "reject"]
        refs: list[dict] = [{"kind": "segment", "locator": seg_locator}]
        refs += [{"kind": "decision_id",
                  "locator": str((getattr(d, "payload", None) or {})
                                 .get("decision_id") or "")}
                 for d in decisions]
        refs += [{"kind": "receipt_id",
                  "locator": str((getattr(r, "payload", None) or {})
                                 .get("receipt_id") or "")}
                 for r in receipts]
        refs = [r for r in refs if r["locator"]]          # 空 locator 不合法,剔除
        # 终态标量(N3/M5-3):``task.completed`` / ``task.failed`` **先于**
        # ``segment.end`` 落盘,故此刻可读。以**标量并入 claim** —— 不新增 payload
        # 字段(INV-G4「只存引用」;payload schema 变更属 H-3,不在本轮范围)。
        term = _terminal_state_of(seg, str(task_id))
        ev = await coll.archive(
            claim=f"task {task_id} 的治理事实可追溯(终态={term})",
            refs=refs, ctx=ctx or SimpleNamespace(session=log_),
            summary=(f"终态={term};决策 {len(decisions)} 条(拒绝 {len(denied)})、"
                     f"凭证 {len(receipts)} 条"))
        return getattr(ev, "evidence_id", None)
    except Exception:                          # noqa: BLE001 派生视图:绝不阻断主链
        log.warning("证据归档跳过 task=%s", task_id, exc_info=True)
        return None

def _terminal_state_of(seg_events: list, task_id: str) -> str:
    """段内终态标量(供 claim 标注;N3/M5-3)。

    ``task.completed`` → ``success``;``task.failed`` → ``cancelled`` 或
    ``error:<code>``;两者皆无(段被外力关闭)→ ``unknown``。
    """
    for e in reversed(seg_events):
        t = getattr(e, "type", None)
        if t == "task.completed":
            return "success"
        if t == "task.failed":
            p = getattr(e, "payload", None) or {}
            if str(p.get("reason") or "") == "cancelled":
                return "cancelled"
            return f"error:{p.get('error') or 'unknown'}"
    return "unknown"

def _evidence_producer(spine: EngineSpine):
    """段关闭时的证据生产者回调(**N3/M5-3 的触发点**)。

    **为什么不放在 ``run_for_task`` 的函数体里**:该函数跑在独立子任务中
    (``task_queue._run_task`` 的 ``create_task``),五种终态里有三种(异常/预算/取消)
    会让它**不执行到收尾**⇒ 段内有治理决策却零证据(实测 3/5)。而 ``segment.end``
    由 ``_run_task`` 自身 ``finally`` 中的 ``close_segment`` 落盘,``_run_task``
    **不被取消**(取消只作用于子任务)⇒ 实测 **5/5** 落盘。挂到该事件即继承此保证,
    并天然消除 N3(生产者不再早于段关闭)。

    幂等由 ``archive_task_evidence`` 内的日志重放闸保证;失败在该函数内吞掉
    (派生视图不阻断主链),故本回调不会影响段关闭。
    """
    async def _on_segment_end(type_: str, env: Any) -> None:
        if getattr(env, "type", None) != "segment.end":
            return
        task_id = str((getattr(env, "payload", None) or {}).get("task_id") or "")
        if not task_id:
            return
        await archive_task_evidence(spine, task_id)
    return _on_segment_end

def _turn_source_of(task: Any) -> Optional[str]:
    """本轮**框架侧来源**标记(KF-C/A4):只取 ``task.meta["source"]``,其余一律 None。

    受控取值(非模型文本):``schedule._fire`` 写入 ``{"source": "schedule:<name>"}``;
    普通用户轮无该键 → None(提示词里不出现该段)。
    """
    meta = getattr(task, "meta", None) or {}
    if not isinstance(meta, dict):
        return None
    src = str(meta.get("source") or "").strip()
    return src or None

async def _agent_ctx_of(spine: EngineSpine, env: Any) -> Any:
    """agent_loop 消费的 ctx = create_agent 装配的 Agent.ctx(spine 绑定)。

    Agent 与 loop 1:1;ctx.session/scope/llm 即 spine 组件——Agent.ctx 由
    create_agent 构造并绑定 loop。env 仅作输入(loop.wake 已携带)。
    首建 agent 时顺带激活 storage 能力(spill 定位器,幂等)。
    """
    sid = str(getattr(env, "session_id", "") or getattr(spine.session, "sid", "")
              or "")
    ag = spine.active_agents.get(sid) if hasattr(spine, "active_agents") else None
    if ag is None:
        ag = create_agent(sid, spine, spine.settings or _cfg_of(spine))
        spine.active_agents[sid] = ag
        await _activate_storage_caps(ag, spine)     # spill 首访激活(F039)
        await _activate_session_caps(ag, spine)     # FTS 检索能力激活(F057)
    return ag.ctx

async def _activate_storage_caps(ag: Any, spine: EngineSpine) -> None:
    """storage 能力激活(F039):SpillProvider enter(建私有目录+订阅清理)+挂定位器。

    ctx.storage 与 spine.storage 同对象引用 → 激活后 executor 关4 读
    ctx.storage.spill 立即可见。会话 1:1 单 agent,激活一次;失败记日志不阻断
    对话(未激活时超长输出走 PERS-221 截断兜底)。"""
    if getattr(spine, "_spill_provider", None) is not None:
        return
    try:
        from pyharness.core import spill as _spill_mod
        prov = _spill_mod.SpillProvider(ctx=ag.ctx)
        await prov.enter(ag.ctx)
        spine._spill_provider = prov
        storage = getattr(spine, "storage", None)
        if storage is not None:
            storage.spill = prov
        log.info("spill provider activated sid=%s", ag.session_id)
    except Exception as e:                         # noqa: BLE001 激活失败:截断兜底,不阻断
        log.warning("spill activate skipped: %s", type(e).__name__)
    # #53 会话 KV(SessionKV;同步 JSON 落盘,懒激活幂等)
    if getattr(spine, "_kv_activated", False):
        return
    try:
        from pyharness.core import kv as _kv_mod
        base = Path(str(getattr(getattr(spine, "storage", None),
                                "sessions_dir", "."))).expanduser()
        kv = _kv_mod.SessionKV(base / f"{ag.session_id}.kv.json",
                               session_id=ag.session_id)
        storage = getattr(spine, "storage", None)
        if storage is not None:
            storage.kv = kv
            spine._kv_activated = True
        log.info("session kv activated sid=%s", ag.session_id)
    except Exception as e:                         # noqa: BLE001 激活失败不阻断
        log.warning("kv activate skipped: %s", type(e).__name__)

async def _activate_session_caps(ag: Any, spine: Any) -> None:
    """会话检索能力激活(F057):开 FTS 派生索引 → 注册 session.fts_query 工具
    → 挂 ctx.session.fts 与 ctx.session_query(repair 落后对账读数面)。

    幂等(spine._fts_entered 旗标);失败(磁盘/权限/SQLite 无 FTS5)只告警不阻断
    ——索引是派生视图,可整体重建(原则 1)。db 路径与 CLI `search` 同源
    (storage.db_path)。2026-09-12 修复:此前 SessionQueryIndex 有完整实现+单测,
    但装配层零调用点 → LLM 调 session.fts_query 必抛 TLB-802"索引未挂载"。
    """
    if getattr(spine, "_fts_entered", False):
        return
    spine._fts_entered = True                      # 试过就不再重试(索引可选)
    idx = None
    try:
        from pyharness.core.session_query import SessionQueryIndex
        cfg = getattr(spine, "settings", None) or _cfg_of(spine)
        db = Path(str(getattr(getattr(cfg, "storage", None), "db_path",
                              "~/.pyharness/index.db"))).expanduser()
        sid = str(getattr(ag, "session_id", "")
                  or getattr(spine.session, "sid", ""))
        idx = SessionQueryIndex(db, sources={sid: spine.session})
        spine.fts = idx                           # ownership before first await
        await idx.enter(ag.ctx)                    # 开库建表 + 总线订阅
        await idx.announce(ag.ctx)                 # 注册 session.fts_query + 挂载
        spine.fts = idx
        ag.ctx.session_query = idx                 # F060 对账读数(repair 用)
        log.info("session fts activated sid=%s db=%s", sid, db)
    except BaseException as e:
        if idx is not None:
            cleanup = asyncio.create_task(idx.detach(None))
            cancelled = None
            while True:
                try:
                    await asyncio.shield(cleanup)
                    spine.fts = None
                    break
                except asyncio.CancelledError as cancellation:
                    if cleanup.done():
                        raise
                    cancelled = cancellation
                except BaseException as cleanup_error:
                    e.add_note(f'index cleanup failed: {type(cleanup_error).__name__}')
                    raise e
            if cancelled is not None:
                cancelled.add_note(f'index activation failed: {type(e).__name__}')
                raise cancelled from e
        if not isinstance(e, Exception):
            raise
        # Optional index failures remain visible; cancellation is never degraded.
        log.warning("fts activate skipped: %s", type(e).__name__)
