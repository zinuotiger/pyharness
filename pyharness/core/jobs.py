"""pyharness/core/jobs.py — 后台 job 管理器 (specs/jobs.py.md 契约;阶段 4 F051)

功能编号:F051(后台 jobs 核心)· 联动 F007(agent-loop 驱动 job)/F011(共享会话日志)/
F025(取消归一 failed(cancelled))/F032(超预算直接 kill)/F040(todo 进度源)/F044(段锚,
事件带 task_id=job:<id> 自成日志段)。
一句话:脱离前台会话运行的后台 job——start/status/cancel/logs 四入口;长任务不占对话
(发出后可继续聊/关会话稍后查);job 与前台**共享同一会话 JSONL**,事件带
task_id="job:<id>" 使 job 自成日志段(F044)可切片回放/审计/预算;并发 ≤4(JOB-001)、
崩溃/超预算自动失败并告警、结果 7 天清理;job 不可交互;owner 授权模型:job_id 不是
机密——查询/取消只认提交方 owner 身份(by),不知道 id ≠ 有权,知道 id ≠ 有权。

偏离说明(契约=specs/jobs.py.md;以下为与规格伪码冲突处的取舍,均列理由,与
task_queue.py/schedule.py 同款先例,已入模块 docstring 供审查):
1. 执行器 seam:伪码直调 agent_loop.run(ctx, intent=…, task_id=…, budget=…) 是
   阶段 4 agent_loop 的未来接口(当前 agent_loop.py 仅 run(ctx)/wake 旧面,见
   task_queue.py 偏离 1)→ 执行器经构造注入(鸭子类型,INV-08 不 import 外围):
   runner 须提供 async run(ctx, *, intent, task_id, budget=None);未注入 runner 时
   job 按 CYC-999 快速失败(防 S-1"伪造已执行",task_queue 同款纪律)。另:真实
   agent_loop.run 失败不 raise 而返回带 reason 的 RunResult(budget/max_turns/error/
   stall…)→ _run 在 runner 返回带 reason 的对象时按 reason 归一:complete/None →
   completed,其余 → job.failed(reason)。
2. 并发口径:伪码 if len(self._running) >= limit 会把"已终态未超期"的 7 天句柄也计
   入并发 → 4 个 job 先后完成后(句柄未摘)第 5 个仍被 JOB-001 拒,与"发出后可继续
   聊、陆续提交多个后台任务"及"并发"本义矛盾 → 并发只数在途(queued/running/
   suspended);_running 注册表保留终态句柄(7 天内可查)但不占并发额度。
3. JOB-001 为 ERR §2.11 功能特性码,errors.register_default_codes 未收录;raise_code
   会把未登记码改写为 CYC-999(语义不符)→ 按伪码原样直接构造 PyHError("JOB-001")
   (code 字段保持字面量 JOB-001,供测试按 .code 断言;QUE-001/BUSY 同款先例);
   EVT-100/EVT-101/EVT-104/GRD-401/CYC-999 等已登记码全走 raise_code。
4. job.* 事件载荷以 EVENT-SCHEMA §3.5.3 payload 模型为准(extra=forbid):job.started
   载荷仅 {job_id}、job.completed 载荷仅 {job_id, elapsed_ms}(模型无 reason 字段)
   → 伪码把 task_id 塞 job.started 载荷、completed 事件带 reason 都会 EVT-100 拒写:
   task_id 一律改走信封 task_id= 参数,job.completed 事件不落 reason。
5. logs/_log_tail 切片口径:伪码只按信封 e.task_id == job:<id> 过滤,但
   task_queue.open_segment/close_segment 落盘的 segment.start/end(EVT §3.5.1:
   task_id 在 payload)与 todo.updated(F040:task_id 在 payload)只带载荷级 task_id →
   取并集(信封 task_id 或 payload.task_id 命中 job task_id 即入切片),否则段锚/
   进度源事件会被 logs 切片漏掉,与"段闭区间切片回放/审计"验收冲突。
6. 终态写盘防扰:job 终态事件 append / segment.end 落在 finally(取消/兜底路径共用);
   若会话恰已 finished(EVT-104,钩子序竞态兜底)或落盘失败(PERS-202)→ 记日志跳过
   不抛——否则会顶替 CancelledError/原异常;终态仍可经共享 JSONL 尾部审计。
7. 段锚配对容错:取消若落在段未开出的窗口(正在 await open_segment)→ started_seq
   未置位,finally 跳过 close_segment(伪码无条件 close 会撞 EVT-100"段未打开")。
8. 取消原因区分:伪码 _run 的 CancelledError 分支一律 reason=cancelled,而"会话关闭
   落 failed(reason=session-closed)"(职责 7)需区分来源 → cancel API 取消走
   cancelled;_on_session_closing 取消前先给 job 打 _cancel_reason="session-closed"
   旗标,_run 取消分支按旗标落 reason。
9. _notify 总线广播用事件类型名 job.completed/job.failed(伪码 emit(j.state) 的
   "completed"/"failed" 未注册事件,总线 EVT-102 拒发);通知注入 agent.message 用
   actor=system(伪码漏 actor;框架侧代发的完成摘要,非 agent-loop 产物)。
10. 段内进度:伪码 status 无 todo 分支引用未定义的 done/len → 缺省 done=0、"0/0 项"、
    progress=None(与伪码一致);"无 todo 按已完成 llm 轮数/事件数估算"无分母定义
    不引入拍脑袋系数(数据表权威:progress 从段内 todo 派生,None=未知)。
11. start 校验提交方身份:ctx.owner 缺失/为空 → EVT-100 拒(owner 必须由通道上下文
    打,非 LLM 自报);owner 缺失会造出无人可查的孤儿 job(system 之外无授权主体)。
12. 段内事件写口经注入 session:runner 假件/未来 agent_loop 在同一 SessionLog 上以
    task_id=job:<id> 追加执行期事件(与 task_queue 测试替身同款纪律);本模块只认
    session.append 面,不越权管 runner 内部事件。
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from pyharness.core.task_queue import TaskQueue
from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.jobs")

# ------------------------------------------------------------------ 常量(F051)
# 并发上限 / 结果保留期:PRD F051 + CFG 固定约束(PARAMETER-ANCHOR 锁死;装配方
# 可经构造参数覆盖,默认值与锁死值一致,本模块不读 config 防硬耦合)
DEFAULT_JOB_LIMIT = 4             # 并发 ≤4(满 → JOB-001 拒新)
DEFAULT_RETENTION_DAYS = 7        # 结果 7 天清理(reap_expired 摘句柄)
MAX_INTENT_CHARS = 64 * 1024      # intent 上限(超长 → EVT-100)

_TERMINAL_STATES = ("completed", "failed", "cancelled")   # 终态集(reap/幂等判定)
_ACTIVE_STATES = ("queued", "running", "suspended")       # 在途集(占并发额度)
# 成功归一 reason(runner 返回 RunResult 形态时的白名单;其余 reason 一律 failed)
_OK_REASONS = (None, "", "ok", "complete")


def _now_iso() -> str:
    """Job.started_ts 打点:UTC ISO8601(仅展示字段,信封 ts 仍由框架统一打)。"""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- 数据结构
@dataclass
class Job:
    """后台 job 句柄(spec 数据结构表字段级)。

    id = j-N 单调(顺序可猜、**非机密**——授权只认 owner);owner = 提交方身份
    (会话绑定用户 id,如 cli:alice/acp:<client>/web:<session>,由通道上下文打);
    task_id 恒 "job:"+id(段与事件归属锚);seq 锚可弃重建(created_seq=job.started
    事件 seq、started_seq=segment.start seq、finished_seq=终态事件 seq,INV-01:
    事件是唯一真源,句柄字段只是日志位置的镜像);expires_at = 终态 + 7 天,
    超期仅摘句柄、事件永留 JSONL。
    """

    id: str
    owner: str
    intent: str
    state: str = "queued"                      # queued/running/suspended/failed/completed/cancelled
    task_id: str = ""                          # "job:"+id(段与事件归属)
    created_seq: int = 0                       # job.started 事件 seq
    started_seq: int = 0                       # segment.start 事件 seq(段锚)
    finished_seq: int = 0                      # 终态事件 seq
    started_ts: Optional[str] = None           # 启动打点(展示)
    elapsed_ms: Optional[int] = None           # 执行耗时(终态后置位)
    reason: Optional[str] = None               # failed 原因:码/budget/cancelled/session-closed
    progress: Optional[float] = None           # 0..1(段内 todo 派生;查询时镜像)
    notify_to: Optional[str] = None            # 完成通知目标会话/通道
    expires_at: Optional[float] = None         # 终态时间 + retention(7 天)
    # ---- 运行期附加(init=False,不进构造签名)----
    task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)
    _cancel_reason: Optional[str] = field(default=None, init=False, repr=False)
    _cancel_pending: bool = field(default=False, init=False, repr=False)
    _begun: bool = field(default=False, init=False, repr=False)

    @property
    def terminal(self) -> bool:
        """终态判定(幂等/清理共用):completed/failed/cancelled。"""
        return self.state in _TERMINAL_STATES


@dataclass
class JobStatus:
    """job 状态快照(status 返回,纯读):state + 派生进度 + 最近日志尾。"""

    job_id: str
    state: str
    owner: str
    progress: Optional[float] = None           # 0..1;段内无 todo → None
    todo_summary: str = ""                     # "N/M 项"(M=0 → "0/0 项")
    elapsed_ms: Optional[int] = None
    log_tail: list = field(default_factory=list)   # 最近 ≤10 条事件摘要(list[dict])
    error: Optional[str] = None                # failed 原因(码/cancelled/budget/…)


# ---------------------------------------------------------------- JobManager
class JobManager:
    """后台 job 管理器(F051):start/status/cancel/logs 四入口 + owner 授权 + 清理。

    构造注入(INV-08):session = SessionLog(共享会话日志,唯一事件写口);
    task_queue = TaskQueue(段锚服务 open_segment/close_segment;None → 内部自建,
    与前台队列共享会话但段 id 命名空间不冲突:job:<id> vs t-N);
    runner = 执行器 seam(未来 agent_loop 的 intent 面;None → job 快速 CYC-999);
    bus = EventBus(完成通知广播,可选);limit/retention_days 默认取锁死常量。

    授权模型:status/cancel/logs 先 _get(不存在 → EVT-101)再 _authorize(非 owner
    且非 system → GRD-401,scope-hidden 不泄漏);list_owned 只列 by 名下纯读。
    job_id 顺序可猜、非机密:猜中 id 不给任何读取权。
    """

    def __init__(self, session: Any, task_queue: Optional[TaskQueue] = None, *,
                 runner: Any = None, bus: Any = None,
                 limit: int = DEFAULT_JOB_LIMIT,
                 retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self._session = session
        self._tq = task_queue if task_queue is not None else TaskQueue(
            session=session)                   # 内部自建:仅用其段锚面(不 submit)
        self._runner = runner                  # 执行器 seam(未来 agent_loop)
        self._bus = bus                        # 完成通知总线(可选)
        self._limit: int = int(limit)
        self._retention_days: int = int(retention_days)
        self._running: dict[str, Job] = {}     # id → Job(在途 + 7 天内终态句柄)
        self._ids = itertools.count(1)         # j-N 单调源(顺序可猜,非机密)

    # ============================================================ 内部辅助
    def _active_count(self) -> int:
        """在途 job 数(并发口径 = queued/running/suspended,见偏离 2)。"""
        return sum(1 for j in self._running.values() if j.state in _ACTIVE_STATES)

    def _get(self, job_id: str) -> Job:
        """注册表取句柄;不存在/已超期清理 → EVT-101(事件仍可经日志按 task_id 查)。"""
        j = self._running.get(job_id)
        if j is None:
            raise_code("EVT-101", job_id=job_id,
                       hint="job 不存在或已超期清理:核对 job_id;"
                            "job 事件仍可经共享 JSONL 按 task_id 查询")
        return j

    @staticmethod
    def _authorize(j: Job, by: str) -> None:
        """owner 授权闸:by == owner 或 system 放行;否则 GRD-401(scope-hidden)。

        job_id 不是机密——知道 id ≠ 有权;错误只给最小现场,不泄漏 intent/owner。
        """
        if by == j.owner or by == "system":
            return
        raise_code("GRD-401", job_id=j.id,
                   advice="job_id 非机密:查询/取消只认提交方 owner 身份(by),"
                          "请用提交方会话查询/取消")

    def _session_closed(self) -> bool:
        """目标会话是否已终态(通知注入前置;SessionLog 以 finished/close_marker 判定)。"""
        is_closed = getattr(self._session, "is_closed", None)
        if callable(is_closed):
            try:
                return bool(is_closed())
            except Exception:  # noqa: BLE001 防御:第三方对象钩子异常按未关闭处理
                pass
        try:
            return bool(self._session.stats().get("closed"))
        except Exception:  # noqa: BLE001
            return False

    def _job_slice(self, j: Job) -> list:
        """job 日志切片:共享 JSONL 中归属该 job 的事件(seq 升序)。

        口径 = 信封 task_id 或 payload.task_id 命中 j.task_id 的并集(偏离 5):
        本模块事件/未来 agent_loop 事件带信封 task_id;segment.start/end 与
        todo.updated 按 EVENT-SCHEMA 只带 payload.task_id——两者都是 job 段事实。
        """
        tid = j.task_id
        return [e for e in self._session.events_after(0)
                if e.task_id == tid or (e.payload or {}).get("task_id") == tid]

    def _job_events_after(self, j: Job, after_seq: int) -> list:
        """job 切片增量读(seq > after_seq,升序;logs 增量拉取用)。"""
        return [e for e in self._job_slice(j) if e.seq > after_seq]

    async def _segment_todos(self, j: Job) -> list:
        """段内 todo 最新清单:取该 job 段内最后一条 todo.updated 的 todos(F040)。

        进度从日志派生(INV-01),不维护第二份状态;无 todo → 空清单(进度未知)。
        """
        todos: list = []
        for e in self._session.events_after(0):
            if e.type == "todo.updated" \
                    and (e.payload or {}).get("task_id") == j.task_id:
                todos = e.payload.get("todos") or []
        return todos

    async def _log_tail(self, j: Job, n: int = 10) -> list:
        """最近 ≤n 条事件摘要(事件摘要 = seq/ts/type/actor/任务归属/载荷)。"""
        return [{"seq": e.seq, "ts": e.ts, "type": e.type, "actor": e.actor,
                 "task_id": e.task_id, "payload": e.payload}
                for e in self._job_slice(j)[-n:]]

    # ============================================================ 提交(F051)
    async def start(self, intent: str, ctx: Any, *,
                    meta: Optional[dict] = None) -> str:
        """提交后台 job(伪码验收直译):校验 → 登记 → 落 job.started → 起协程。

        并发 <4(满 → JOB-001 拒新,等终态/取消后再试);intent 空/超长 → EVT-100;
        父会话 closed/finished → job.started 落盘被 EVT-104 拒(事件失败 = 没启动,
        回滚登记)。owner/owner_channel 取自 ctx(通道上下文注入,见偏离 11)。
        返回 job_id(j-N,顺序可猜非机密);父会话 running 态不受影响,可继续对话。
        """
        if not intent or not intent.strip():      # 空意图拒提交(白名单前置)
            raise_code("EVT-100", hint="空 job 意图,start 拒绝")
        if len(intent) > MAX_INTENT_CHARS:        # 超长拒提交
            raise_code("EVT-100", hint=f"job 意图超长(>{MAX_INTENT_CHARS} 字符)")
        owner = getattr(ctx, "owner", None)
        if not owner or not str(owner).strip():   # 身份缺失 → 孤儿 job(偏离 11)
            raise_code("EVT-100", hint="ctx.owner 缺失:提交方身份必须由通道上下文"
                       "注入(cli:alice/acp:<client>/web:<session>),非 LLM 自报")
        if self._active_count() >= self._limit:   # 并发 ≤4:满拒(偏离 2/3)
            raise PyHError("JOB-001", ctx={
                "running": self._active_count(), "limit": self._limit,
                "advice": "先等若干 job 终态或取消后再提交"})
        n = next(self._ids)
        j = Job(id=f"j-{n}", owner=owner, intent=intent, state="queued",
                task_id=f"job:j-{n}")
        j.notify_to = (meta or {}).get("notify_to") \
            if isinstance(meta, dict) else None
        if not j.notify_to:
            j.notify_to = getattr(ctx, "owner_channel", None)
        self._running[j.id] = j                   # 先登记(事件失败可回滚)
        try:
            env = await self._session.append(     # task_id 走信封(偏离 4)
                "job.started", {"job_id": j.id}, actor="system", task_id=j.task_id)
        except PyHError:
            self._running.pop(j.id, None)         # 事件没落成 = 没启动
            raise
        j.created_seq = env.seq
        j.started_ts = _now_iso()
        j.state = "running"                       # 落盘成功 → 转 running
        # 协程并发(原则 5):不占父会话 running 态,父可继续对话
        j.task = asyncio.create_task(self._run(j, ctx))
        # 取消竞态:task.cancel() 落在任务尚未开始执行前会使协程体永不运行,
        # finally 的终态事件全丢(注册表永挂 running 占并发)→ 统一以
        # _cancel_pending 旗标表达取消,_run 入口自查转 CancelledError(finally
        # 仍落终态);任务已开始(_begun)则由 cancel 直接 task.cancel() 传播。
        log.info("job started id=%s owner=%s intent_len=%d",
                 j.id, j.owner, len(intent))
        return j.id

    # ============================================================ 执行体
    async def _run(self, j: Job, ctx: Any) -> None:
        """job 执行体(段围栏 + 终态归一):开段 → 跑 runner → 各终态分支落事件。

        成功 → job.completed;PyHError(含预算/LLM-310)→ job.failed(reason=码);
        CancelledError → job.failed(reason=cancelled|session-closed)(F025,re-raise
        不吞);未预期 → job.failed(CYC-999)兜底;任何异常不得逃逸出 finally 之外
        (终态事件 append/关段失败仅记日志,见偏离 6/7)。终态事件恒先于
        session.finished(钩子序 child-first,同 subagent 纪律)。
        """
        j._begun = True                           # 执行起点标记(cancel 分发判定)
        t0 = time.monotonic()
        reason = "ok"
        try:
            if j._cancel_pending:                 # 早到取消(任务创建前):自查转取消
                j._cancel_pending = False
                raise asyncio.CancelledError()    # finally 仍会落 failed(cancelled)
            # F044 段锚:先落盘再执行,崩溃后段不悬空(task_queue 强同步开段)
            j.started_seq = await self._tq.open_segment(j.task_id)
            result = await self._run_runner(j, ctx)
            rr = getattr(result, "reason", None)
            if rr in _OK_REASONS:                 # 成功(含 runner 无 reason 返回)
                j.state, reason = "completed", "ok"
            else:                                 # RunResult 式非成功(reason 归一)
                j.state, reason = "failed", rr    # budget/max_turns/error/stall/…
        except PyHError as e:                     # 结构化失败(含预算/LLM-310)
            j.state, reason = "failed", e.code
        except asyncio.CancelledError:            # F025 取消:不吞,re-raise
            j.state = "failed"
            reason = j._cancel_reason or "cancelled"   # session-closed vs user-cancel
            raise
        except Exception as e:                    # 兜底:CYC-999,堆栈仅本地
            log.exception("job crashed id=%s", j.id)
            j.state, reason = "failed", "CYC-999"
        finally:
            # 终态记账:耗时 + 7 天过期锚 + 终态事件 + 关段(顺序不可换)
            j.elapsed_ms = int((time.monotonic() - t0) * 1000)
            j.expires_at = time.time() + self._retention_days * 86400
            if j.state != "completed":
                j.reason = reason                 # completed 无原因字段(偏离 4)
            ev = "job.completed" if j.state == "completed" else "job.failed"
            try:
                payload: dict = {"job_id": j.id, "elapsed_ms": j.elapsed_ms}
                if ev == "job.failed":
                    payload["reason"] = reason    # job.failed 载荷模型含 reason
                env = await self._session.append(ev, payload, actor="system",
                                                 task_id=j.task_id)
                j.finished_seq = env.seq
            except PyHError as e:                 # EVT-104/PERS-202:终态仅记日志
                log.error("job terminal append failed code=%s job=%s",
                          e.code, j.id)
            if j.started_seq:                     # 段已开出才配对关闭(偏离 7)
                try:
                    await self._tq.close_segment(j.task_id, j.started_seq)
                except PyHError as e:
                    log.error("job close segment failed code=%s job=%s",
                              e.code, j.id)
            await self._notify(j)                 # 完成通知(终态后广播/注入)

    async def _run_runner(self, j: Job, ctx: Any) -> Any:
        """执行器调用(注入 seam,见偏离 1):runner.run(ctx, intent=…, task_id=…)。

        未装配 runner → CYC-999 快速失败(防伪造已执行);同步/异步返回兼容。
        """
        runner = self._runner
        if runner is None:
            raise_code("CYC-999", job_id=j.id,
                       hint="JobManager 未装配执行器(runner):job 拒绝执行,"
                            "防伪造已执行(task_queue 同款纪律)")
        fn = getattr(runner, "run", runner)
        if not callable(fn):
            raise_code("CYC-999", job_id=j.id,
                       hint=f"执行器不可调用: {type(runner).__name__}")
        budget = getattr(ctx, "budget", None)
        res = fn(ctx, intent=j.intent, task_id=j.task_id, budget=budget)
        if inspect.isawaitable(res):
            return await res
        return res

    # ============================================================ 状态查询
    async def status(self, job_id: str, *, by: str) -> JobStatus:
        """状态/进度查询(owner 授权):state + 派生进度 + 最近日志尾。

        进度从段内 todo.updated 清单推导(done/总数);无 todo → None、0/0 项;
        job 不存在 → EVT-101;by 非 owner 且非 system → GRD-401(知道 id ≠ 有权)。
        """
        j = self._get(job_id)
        self._authorize(j, by)
        todos = await self._segment_todos(j)
        done, progress = 0, None
        if todos:                                 # 进度 = 完成项占比(F040 派生)
            done = sum(1 for t in todos if t.get("done"))
            progress = round(done / len(todos), 3)
        j.progress = progress                     # 查询结果镜像(非权威,INV-01)
        tail = await self._log_tail(j, n=10)
        return JobStatus(job_id=j.id, state=j.state, owner=j.owner,
                         progress=progress,
                         todo_summary=f"{done}/{len(todos)} 项",
                         elapsed_ms=j.elapsed_ms, log_tail=tail,
                         error=j.reason)

    async def logs(self, job_id: str, *, after_seq: int = 0,
                   by: str) -> list:
        """最近日志(owner 授权):共享会话 JSONL 按 job task_id 切片(seq 升序)。

        after_seq = 增量起点(排他下界);越权 GRD-401 / 不存在 EVT-101 同 status;
        只读投影,绝不写日志。
        """
        j = self._get(job_id)
        self._authorize(j, by)
        return self._job_events_after(j, after_seq)

    # ============================================================ 取消(F025)
    async def cancel(self, job_id: str, *, by: str) -> bool:
        """取消后台 job(owner 授权):传播 CancelledError → 归一 job.failed。

        在途 → 先落 system.cancelled 审计再取消任务(沿 await 链传播,F025);
        已终态 → 幂等 False;不存在 → EVT-101;job_id 猜中不可取消他人 job
        (GRD-401)。任务创建前到达的取消以 _cancel_pending 旗标补投(防竞态丢取消)。
        """
        j = self._get(job_id)
        self._authorize(j, by)
        if j.terminal:
            return False                          # 终态幂等(防重放)
        await self._session.append("system.cancelled",
                                   {"what": j.task_id, "reason": "user-cancel"},
                                   actor="system", task_id=j.task_id)
        j._cancel_pending = True                  # 早到取消统一旗标(入口自查兜底)
        if j.task is not None and not j.task.done() and j._begun:
            j.task.cancel()                       # 任务已开始执行 → 沿 await 链传播
        return True

    # ============================================================ 列表/清理
    def list_owned(self, by: str) -> list:
        """我的 job 列表(owner 授权):by 名下全部在册 job_id(含终态 7 天内)。

        by=system 列全部;纯读不抛。
        """
        return [jid for jid, j in self._running.items()
                if j.owner == by or by == "system"]

    async def reap_expired(self) -> int:
        """结果 7 天清理:摘除全部终态超期句柄(事件永留 JSONL,审计/重放不受影响)。

        由管理器定时器/会话空闲期调用;纯内存清理,返回清理数。
        """
        now = time.time()
        reaped: list[str] = []
        for jid, j in list(self._running.items()):
            if j.terminal and j.expires_at is not None and j.expires_at < now:
                reaped.append(jid)
                self._running.pop(jid, None)      # 只摘句柄,不删日志
        if reaped:
            log.info("reaped expired job handles=%s", reaped)
        return len(reaped)

    # ============================================================ 会话关闭钩子
    async def _on_session_closing(self, type_: Any, payload: Any) -> None:
        """child-first 清理钩子(agent.close 在 append session.finished **前**回调)。

        取消全部在途 job → await 各自落 failed 终态(先落终态再 finished,否则
        finished 后回写撞 EVT-104);取消原因落 session-closed(偏离 8)。
        """
        live = [j for j in self._running.values() if j.state in _ACTIVE_STATES]
        if not live:
            return
        for j in live:                            # ① 全部取消(先审计后 cancel)
            j._cancel_reason = "session-closed"
            j._cancel_pending = True              # 未开始者由入口自查兜底
            try:
                await self._session.append("system.cancelled",
                                           {"what": j.task_id,
                                            "reason": "session-closed"},
                                           actor="system", task_id=j.task_id)
            except PyHError as e:                 # EVT-104 竞态兜底:仅记日志
                log.error("session-closing audit failed code=%s job=%s",
                          e.code, j.id)
            if j.task is not None and not j.task.done() and j._begun:
                j.task.cancel()                   # 已开始执行 → 沿 await 链传播
        tasks = [j.task for j in live if j.task is not None]
        if tasks:
            await asyncio.gather(*tasks,          # ② 等 _run 各自落 failed 终态
                                 return_exceptions=True)

    # ============================================================ 完成通知
    async def _notify(self, j: Job) -> None:
        """完成通知:总线广播终态 + (completed 且 notify_to)注入可见摘要。

        目标会话已 finished → 跳过补写仅记日志(通知非强同步事实,EVT-104 兜底)。
        """
        if self._bus is not None:
            ev = "job.completed" if j.state == "completed" else "job.failed"
            try:
                await self._bus.emit(ev, {"job_id": j.id,
                                          "elapsed_ms": j.elapsed_ms,
                                          "reason": j.reason})
            except PyHError as e:                 # 广播失败不影响终态事实
                log.error("job notify bus failed code=%s job=%s", e.code, j.id)
        if not (j.notify_to and j.state == "completed"):
            return
        if self._session_closed():
            return                                # 目标已终态:不补写(仅日志)
        try:
            await self._session.append(
                "agent.message",
                {"content": f"[后台任务 {j.id} 完成] {j.intent[:120]}"},
                actor="system")
        except PyHError as e:                     # EVT-104 竞态:跳过
            log.error("job notify append failed code=%s job=%s", e.code, j.id)


__all__ = [
    "JobManager", "Job", "JobStatus",
    "DEFAULT_JOB_LIMIT", "DEFAULT_RETENTION_DAYS", "MAX_INTENT_CHARS",
]
