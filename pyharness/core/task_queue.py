"""pyharness/core/task_queue.py — 会话级 FIFO 顺序任务队列 (specs/task_queue.py.md 契约;阶段 4)

功能编号:F043(顺序任务队列)· F044(每任务独立日志段)· F025(取消传播联动)· F015(审批等待暂停联动)。
一句话:一次用户意图 = 一个任务;同一时刻仅一个 running、其余 waiting(F043,一个做完下一个);
每个任务执行期由泵写 segment.start(强同步)/segment.end 围出独立日志段(F044,以 task_id 切分
查询/回放/预算);暂停/恢复/取消/状态查询全部事件化;plan_mode(F046)/schedule(F048)/jobs(F051)
均经本模块入队复用纪律(编排地基)。

本模块为纯编排内核:消费 session.append(事件)、注入 runner(执行器,阶段 4 agent_loop 的
run_for_task seam)、errors(PyHError/F020);不 import agent_loop/llm/scope 等外围能力(INV-08),
执行器一律经构造参数注入,入队者(plan/schedule/CLI)只认 submit/wait_for 面。

偏离说明(契约=specs/task_queue.py.md;以下为与既有实现/规格冲突处的取舍,均列理由,
与 agent.py/commands.py 同款先例,已入模块 docstring 供审查):
1. agent_loop.run_for_task(t)/cancel_current(task_id) 是阶段 4 agent_loop 的未来接口
   (当前 agent_loop.py 仅提供 run(ctx)/wake(env, ctx) 旧面,见 specs/agent_loop.py.md)→
   本模块以注入 runner seam 实现执行:构造参数 runner 需提供 async run_for_task(task)
   (Task 对象,内含 id/intent/meta,runner 自行绑定 envelope.task_id);未注入 runner 时
   任务按 CYC-999 快速失败(防 S-1"伪造已执行":宁失败不静默假完成)。不 import agent_loop。
2. 取消传播(F025)实现:cancel(running) 先落 system.cancelled 审计,再尽力调用
   runner.cancel_current(task_id)(若存在,允许同步/异步),最后兜底取消"执行子任务"
   (self._exec,包裹 run_for_task 的独立 asyncio.Task)——协作式取消把 CancelledError 注入
   执行协程,runner 内部 re-raise 不吞语义由 agent_loop 自身保证;spec 伪码在泵内联布局下
   re-raise 会直接杀死泵单例(与"running 取消后队首自动接"GWT-F043-02 互斥),故泵吸收
   任务级取消并自动接队首;"取消不吞"由 system.cancelled + task.failed(cancelled) 事件、
   cancel() 返回 True、wait_for 返回 code="cancelled" 三通道表达。
3. pause() 伪码"先计数再判 _suspended()"恒真导致 queue.suspended 永不落盘(spec 伪码 bug)
   → 按"挂起状态从无到有(全部计数 0→1)才发 queue.suspended;已挂期间任何新 pause
   (同/异原因)仅计数合并不发事件"实现——与 spec 注释"已挂:仅计数,event 不重复"
   及 EVENT-SCHEMA §3.5.3"重复挂起合并"一致(先判后计数)。
4. queue.resumed 的 payload 模型(EVENT-SCHEMA §3.5.3 权威,extra=forbid)无 by 字段——
   伪码 resumed 带 by 会 EVT-100 拒写 → resumed 事件只落 reason;resume(by=...) 参数
   保留签名对称但事件不带 by(与 EVENT-SCHEMA 字段级权威一致)。
5. QUE-001 为 ERR §2.11 功能特性码,errors.py 的 register_default_codes 未收录;raise_code
   会把未登记码改写为 CYC-999(语义不符)→ 按 BUSY 同款先例直接构造 PyHError("QUE-001")
   (code 字段保持字面量 QUE-001,供调用方/测试按 .code 断言)。
6. wait_for 增加 _done 终态缓存:伪码"pop 后 result"使终态任务的二次 wait_for 悬挂(新 future
   永不落);缓存 + 未知 task_id 显式 EVT-100(防挂死)保证重复等待幂等;_futures 只记在途等待。
7. 构造签名含 session/runner/max_queue(默认 32,与 CFG 锁死/loop.task_queue_max 一致)——
   spec 数据结构表未给构造参数;按 INV-08 注入纪律落地(session 事件源 + runner 执行器)。

数据纪律:队列自身的状态位(running/_q/_pause_reasons)为可弃重建的执行态;任务终态一律以
事件为准(INV-01),Task 对象不含状态位;status()/wait_for 结果均由事件流派生/配对。
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.task_queue")

# 队深上限默认值(CFG 锁死 32;PARAMETER-ANCHOR 配置默认值列)
DEFAULT_MAX_QUEUE = 32


# ------------------------------------------------------------------ 数据结构
@dataclass
class Task:
    """队列任务条目:一次用户意图 = 一个任务(F043)。

    id = t-N 单调(auto)或显式指定(供 plan/schedule 溯源);meta = 附加上下文
    (plan 步/schedule 来源);enqueued_seq = 其 task.enqueued 事件的 seq(段归属锚)。
    终态只以事件为准,任务对象不含状态位(INV-01)。
    """

    id: str
    intent: str
    meta: Optional[dict] = None
    enqueued_seq: int = 0


@dataclass
class TaskResult:
    """任务终态结果(wait_for 返回):ok/code/summary/duration_ms。"""

    ok: bool
    code: Optional[str] = None       # 失败错误码或 "cancelled";成功为 None
    summary: Optional[str] = None    # 失败摘要(脱敏);成功缺省 None
    duration_ms: int = 0             # 执行耗时(毫秒;waiting 期取消为 0)


@dataclass
class QueueStatus:
    """队列状态快照(status 返回,纯读):供 UI/CLI 展示"后面还有几个"(F043)。"""

    running: Optional[str] = None
    waiting: list = field(default_factory=list)          # FIFO 顺序(task_id 列表)
    paused: bool = False
    pause_reasons: list = field(default_factory=list)    # 排序后的原因明细
    depth: int = 0


# ------------------------------------------------------------------ TaskQueue
class TaskQueue:
    """会话级 FIFO 顺序任务队列(F043/F044 编排地基)。

    私有字段(spec 清单):_q(waiting deque,尾插头弹)、_running(执行中任务,至多一个)、
    _pause_reasons(Counter,计数>0 即 suspended)、_pump_task(泵单例句柄)、_futures
    (task_id → 终态 Future,wait_for 填充);实现附加:_done(终态 TaskResult 缓存,幂等
    等待)、_exec(当前执行子任务,取消目标)、_segments(已开段 task_id 集合,段配对闸)、
    _cancel_pending(取消请求在子任务创建前到达的传递旗标)、_id_counter(t-N 单调源)。
    """

    def __init__(self, session: Any, *, runner: Any = None,
                 max_queue: int = DEFAULT_MAX_QUEUE) -> None:
        """构造队列。

        session = SessionLog 事件源(append 唯一写口);runner = 执行器,须提供 async
        run_for_task(task)(阶段 4 agent_loop 接口 seam,见偏离 1),可选 cancel_current
        (task_id);max_queue = waiting 队深上限(默认 32,CFG 锁死;满 → QUE-001)。
        """
        self._session = session
        self._runner = runner
        self._max_queue: int = int(max_queue)
        self._q: Deque[Task] = deque()
        self._running: Optional[Task] = None
        self._pause_reasons: Counter[str] = Counter()
        self._resume_evt = asyncio.Event()               # 泵挂起唤醒(非忙等)
        self._pump_task: Optional[asyncio.Task] = None   # 泵单例句柄(防多泵)
        self._exec: Optional[asyncio.Task] = None        # 执行子任务(取消目标)
        self._futures: dict[str, asyncio.Future] = {}    # 在途等待(task_id → Future)
        self._done: dict[str, TaskResult] = {}           # 终态缓存(重复等待幂等)
        self._segments: set[str] = set()                 # 已开段 task_id(配对闸,F044)
        self._cancel_pending: Optional[str] = None       # 取消旗标(见 _run_task)
        self._id_counter = itertools.count(1)            # t-N 单调源

    # ============================================================ 内部辅助
    def _suspended(self) -> bool:
        """挂起判定:任一原因计数 > 0 即 suspended(重复挂起合并)。"""
        return bool(self._pause_reasons)

    def _next_id(self) -> str:
        """自动 task_id:t-N 单调(self._seq.next() 语义,itertools 计数实现)。"""
        return f"t-{next(self._id_counter)}"

    @staticmethod
    def _ms_since(t0: float) -> int:
        """耗时毫秒(perf_counter 差,仅计量,不参与事件字段)。"""
        return int((time.perf_counter() - t0) * 1000)

    def _settle(self, task_id: str, result: TaskResult) -> None:
        """终态记账:结果入 _done 缓存 + 填充在途 Future(如有等待者)。

        与终态事件 append 同点调用(每任务至多一个终态);_done 供 wait_for 幂等读取,
        _futures 记录在途等待(settle 后移除,防泄漏;等待者持本地引用不受影响)。
        """
        self._done[task_id] = result
        fut = self._futures.pop(task_id, None)
        if fut is not None and not fut.done():
            fut.set_result(result)

    async def _request_cancel(self, task_id: str) -> None:
        """请求取消执行中任务:先给 runner 协作通道,再兜底取消执行子任务。

        runner.cancel_current(task_id) 存在则先调用(同步/异步兼容,失败记日志不阻断);
        随后无论 runner 是否已处理,都向 self._exec(包裹 run_for_task 的子任务)注入
        协作取消——F025 语义:取消信号必达执行协程,由 agent_loop 自身 re-raise 不吞。
        """
        runner = self._runner
        cc = getattr(runner, "cancel_current", None) if runner is not None else None
        if callable(cc):
            try:
                res = cc(task_id)
                if inspect.isawaitable(res):
                    await res
            except Exception:  # noqa: BLE001 协作钩子失败不阻断兜底取消
                log.exception("runner.cancel_current failed task=%s", task_id)
        child = self._exec
        if child is not None and not child.done():
            child.cancel()                       # 兜底传播:不依赖 runner 实现细节

    # ============================================================ 入队(F043)
    async def submit(self, intent: str, *, meta: Optional[dict] = None,
                     task_id: Optional[str] = None) -> str:
        """用户意图尾插入队并返回 task_id(F043)。

        空意图 EVT-100 拒;队深(仅 waiting)≥ max_queue 抛 QUE-001;写 task.enqueued
        {pos};泵未在跑(或已退出)则启动泵单例——入队后即由队列接管执行。
        """
        if not intent or not intent.strip():      # 空意图拒入队(白名单前置)
            raise_code("EVT-100", hint="空任务意图,submit 拒绝")
        if len(self._q) >= self._max_queue:       # 队深上限(F043 边界:满拒)
            raise PyHError("QUE-001", ctx={"advice":
                f"队列已满(≥{self._max_queue}),请等当前任务结束或取消"})
        t = Task(id=task_id or self._next_id(),   # 显式 task_id 供 plan/schedule 溯源
                 intent=intent, meta=meta)
        self._q.append(t)                         # FIFO:尾插
        env = await self._session.append(
            "task.enqueued", {"task_id": t.id, "pos": len(self._q)}, actor="system")
        t.enqueued_seq = env.seq
        # 泵单例(防重入):running 非空或泵仍在跑(含 done 的旧句柄)不重启
        if self._running is None and (self._pump_task is None
                                      or self._pump_task.done()):
            self._pump_task = asyncio.create_task(self._pump())
        return t.id

    # ============================================================ 泵(F043)
    async def _pump(self) -> None:
        """单飞泵:唯一执行放行口(F043 核心)。

        暂停期不弹任务(等裁决不饿死,resume 置位唤醒);队空即退场由下次 submit 唤醒;
        否则队首 popleft 置 running 并执行,finally 清位使队首自动接续。任务级取消
        (CancelledError)已在 _run_task 归一化事件化,泵吸收后继续;其余未预期异常
        记堆栈仅本地(保泵活,队列继续);泵自身被外部取消则让路(不在此吞)。
        """
        while True:
            if self._suspended():                 # 暂停期:只停消费,不丢队不超时
                await self._resume_evt.wait()     # resume 时置位唤醒(非忙等)
                self._resume_evt.clear()
                continue
            if not self._q:                       # 队空:泵退场;下次 submit 重启泵
                self._pump_task = None
                return
            t = self._q.popleft()
            self._running = t                     # 队首接跑
            try:
                await self._run_task(t)           # 执行(内部含段锚与终态事件)
            except asyncio.CancelledError:
                # 泵自身被外部取消:不伪造任务终态,让路(任务级取消在 _run_task 内已吸收)
                raise
            except Exception:                     # 泵自身兜底:单任务失败已事件化
                log.exception("task pump crash task=%s", t.id)   # CYC-999 不吞但保泵活
            finally:
                self._running = None              # 释放 → while 自动接队首(F043)

    # ============================================================ 单任务执行
    async def _run_task(self, t: Task) -> None:
        """单任务生命周期执行器(F043/F044)。

        started → segment.start(强同步,经 open_segment) → runner.run_for_task
        (执行子任务,可独立取消)→ 终态事件(completed/failed,每任务至多一个)→
        segment.end 配对关闭。失败分级:PyHError 原码 / 未预期 CYC-999 / 取消
        cancelled(归一化后泵吸收,接队首);终态落事件即 _settle(唤醒 wait_for)。
        """
        await self._session.append("task.started", {"task_id": t.id}, actor="system")
        # 强同步段锚:先落盘再执行,崩溃后回放段不悬空(F044)
        start_seq = await self.open_segment(t.id)
        t0 = time.perf_counter()
        # 执行子任务:协作取消注入点(F025,取消不伤泵);runner 内部事件带 task_id 由
        # agent_loop 自绑(本模块只引用 segment 围栏,不越权写执行期事件)
        child = asyncio.create_task(self._run_runner(t))
        self._exec = child
        try:
            # 取消请求在子任务创建前已到达(如 started/segment.start 落盘窗口)→ 补取消
            if self._cancel_pending == t.id:
                self._cancel_pending = None
                child.cancel()
            await child
            await self._session.append("task.completed",
                {"task_id": t.id, "reason": "ok"}, actor="system")
            self._settle(t.id, TaskResult(ok=True, duration_ms=self._ms_since(t0)))
        except asyncio.CancelledError:
            if child.cancelled():                 # 取消源 = 执行子任务(API cancel)
                # F025:归一化 failed(cancelled);事件 + settle 双通道表达"取消不吞"
                await self._session.append("task.failed", {"task_id": t.id,
                    "reason": "cancelled", "error": "cancelled"}, actor="system")
                self._settle(t.id, TaskResult(ok=False, code="cancelled",
                    summary="cancelled", duration_ms=self._ms_since(t0)))
                # 不向泵 re-raise:取消已事件化,泵自动接队首(GWT-F043-02,见偏离 2)
            else:
                raise                             # 泵自身被外部取消:让路
        except PyHError as e:                     # 业务失败:事件化并继续队列(F043)
            await self._session.append("task.failed", {"task_id": t.id,
                "reason": "error", "error": e.code}, actor="system")
            self._settle(t.id, TaskResult(ok=False, code=e.code, summary=e.code,
                duration_ms=self._ms_since(t0)))
        except Exception as e:                    # 未预期:归 CYC-999,堆栈仅本地
            log.exception("task failed unexpected task=%s", t.id)
            await self._session.append("task.failed", {"task_id": t.id,
                "reason": "error", "error": "CYC-999"}, actor="system")
            self._settle(t.id, TaskResult(ok=False, code="CYC-999",
                summary=f"{type(e).__name__}: {e}"[:200], duration_ms=self._ms_since(t0)))
        finally:
            self._exec = None
            await self.close_segment(t.id, start_seq)   # 配对关闭;段=回放最小单位(F058)

    async def _run_runner(self, t: Task) -> None:
        """执行器调用(子任务体):runner 须可调用 run_for_task(task)。

        未装配 runner 时按 CYC-999 快速失败(防 S-1 伪造已执行);接受对象方法或
        裸可调用(同步/异步兼容,INV-08:执行器经构造注入,本模块不 import 外围)。
        """
        runner = self._runner
        if runner is None:
            raise_code("CYC-999", hint="队列未装配执行器(run_for_task),任务拒绝执行")
        fn = getattr(runner, "run_for_task", runner)
        if not callable(fn):
            raise_code("CYC-999", hint=f"执行器不可调用: {type(runner).__name__}")
        res = fn(t)
        if inspect.isawaitable(res):
            await res

    # ============================================================ 暂停/恢复
    async def pause(self, reason: str, *, by: str = "system") -> None:
        """队列挂起(F015 联动):审批等待/预算暂停时停消费——只停弹任务,运行中任务
        不受影响,等待不超时饿死。

        重复挂起合并:已挂期间任何新 pause(同/异原因)仅计数累加,不发事件;仅挂起
        状态"从无到有"(全部计数 0→1)才写 queue.suspended——先 suspended 后 resumed
        事件序,resume 全解除才写 resumed(EVENT-SCHEMA §3.5.3,见偏离 3)。
        """
        if not reason or not reason.strip():      # 空原因拒(防计数污染挂起态)
            raise_code("EVT-100", hint="pause 原因不能为空(approval-pending/budget 等)")
        if self._suspended():                     # 已挂:仅计数合并,event 不重复(EVT 词表)
            self._pause_reasons[reason] += 1
            return
        self._pause_reasons[reason] += 1          # 首挂起(0→1):发事件 + 泵阻塞
        await self._session.append("queue.suspended",
            {"reason": reason, "by": by}, actor="system")
        self._resume_evt.clear()                  # 泵在 wait() 处阻塞

    async def resume(self, reason: str, *, by: str = "system") -> None:
        """解除一个挂起原因:计数减一;仍有其他原因保持挂起;全部解除才写
        queue.resumed 并唤醒泵。未挂起的 reason 幂等无害(无事件)。

        by 参数保留签名对称;queue.resumed payload 模型无 by 字段(偏离 4),事件只落 reason。
        """
        if self._pause_reasons.get(reason, 0) <= 0:    # 未挂起的 reason:幂等,无事件
            return
        if self._pause_reasons[reason] > 1:            # 该原因多层挂起:仅减计数
            self._pause_reasons[reason] -= 1
            return
        del self._pause_reasons[reason]                # 减到 0:移除该原因
        if self._pause_reasons:                        # 仍有其他原因 → 维持挂起
            return
        await self._session.append("queue.resumed",
            {"reason": reason}, actor="system")        # 全解除才写 resumed(偏离 4)
        self._resume_evt.set()                         # 唤醒泵(先 resumed 后放行)

    # ============================================================ 取消(F025)
    async def cancel(self, task_id: str, *, by: str = "system") -> bool:
        """任务取消(F025):running → system.cancelled 审计 + 传播取消,队首自动接;
        waiting → 直接摘除并记 failed(cancelled);不存在/已终态 → 幂等 False。
        """
        if self._running is not None and self._running.id == task_id:
            # running:取消审计留痕(强同步语义由调用方/日志层保证;失败不阻断取消)
            try:
                await self._session.append("system.cancelled",
                    {"what": f"task:{task_id}", "reason": by}, actor="system")
            except PyHError as e:                      # 审计失败不阻塞取消传播
                log.error("cancel audit failed code=%s task=%s", e.code, task_id)
            self._cancel_pending = task_id             # 先置旗标再触发(见 _run_task)
            await self._request_cancel(task_id)
            return True
        for i, w in enumerate(self._q):                # waiting:摘除,无需通知循环
            if w.id == task_id:
                del self._q[i]
                await self._session.append("task.failed", {"task_id": task_id,
                    "reason": "cancelled"}, actor="system")   # 取消归一化 failed(cancelled)
                self._settle(task_id, TaskResult(ok=False, code="cancelled",
                    summary="cancelled"))
                return True
        return False                                   # 已终态/不存在:幂等 False(防重放)

    # ============================================================ 状态查询
    def status(self) -> QueueStatus:
        """队列状态快照(F043,纯读):running/waiting/paused/depth 全貌,供 UI/CLI
        查询——用户可见"后面还有几个"(waiting FIFO 序)。"""
        return QueueStatus(
            running=self._running.id if self._running else None,
            waiting=[t.id for t in self._q],           # FIFO 顺序展示
            paused=bool(self._pause_reasons),          # 挂起中(含原因明细)
            pause_reasons=sorted(self._pause_reasons),
            depth=len(self._q))

    # ============================================================ 终态等待
    async def wait_for(self, task_id: str, *,
                       timeout: Optional[float] = None) -> TaskResult:
        """外部等待任务终态(F046/jobs 复用):终态缓存/Future 填充后返回 TaskResult;
        timeout=None 永久等(审批挂起不饿死),超时抛 BUSY 显式拒——等待不影响任务本身
        (shield)。未知 task_id 显式 EVT-100(防永久悬挂,偏离 6)。
        """
        done = self._done.get(task_id)                 # 已终态:直接返回(幂等)
        if done is not None:
            return done
        known = (self._running is not None and self._running.id == task_id) \
            or any(w.id == task_id for w in self._q) or task_id in self._futures
        if not known:                                  # 从未入队/已结算但缓存被清理
            raise_code("EVT-100", task_id=task_id,
                       hint="wait_for 目标任务不存在(未入队或非本队列任务)")
        fut = self._futures.get(task_id)
        if fut is None:                                # 每 task_id 一个终态 Future
            fut = asyncio.get_running_loop().create_future()
            self._futures[task_id] = fut
        try:
            if timeout is None:
                await asyncio.shield(fut)              # 等待可被外部取消,任务不受牵连
            else:
                await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            raise PyHError("BUSY", ctx={"hint": f"等待任务 {task_id} 超时",
                                        "advice": "查任务是否仍 running/挂起"})
        return fut.result()                            # TaskResult(ok/code/summary)

    # ============================================================ 段锚(F044)
    async def open_segment(self, task_id: str) -> int:
        """开段:写 segment.start(强同步)返回其 seq(F044 验收直译)。

        段锚先落盘再执行(崩溃后回放段不悬空);同一 task_id 嵌套开段 → EVT-100
        (segment.start/end 必须一一配对,子 Agent 用独立 session 段不嵌套)。
        """
        if task_id in self._segments:
            raise_code("EVT-100", task_id=task_id,
                       hint="段已打开:segment.start/end 必须一一配对,禁嵌套开段")
        env = await self._session.append("segment.start",
            {"task_id": task_id}, actor="system", sync=True)   # 强同步:段锚先落
        self._segments.add(task_id)
        return env.seq

    async def close_segment(self, task_id: str, start_seq: int) -> None:
        """关段:写 segment.end 与 open_segment 的 start_seq 配对(F044)。

        段查询经 session.events_between(start_seq, end_seq) 闭区间(回放/复算/预算审计);
        未开段即关(不配对)→ EVT-100。
        """
        if task_id not in self._segments:
            raise_code("EVT-100", task_id=task_id, start_seq=start_seq,
                       hint="段未打开:close_segment 须配对已 open 的段")
        await self._session.append("segment.end",
            {"task_id": task_id, "start_seq": start_seq}, actor="system")
        self._segments.discard(task_id)


__all__ = [
    "TaskQueue", "Task", "TaskResult", "QueueStatus", "DEFAULT_MAX_QUEUE",
]
