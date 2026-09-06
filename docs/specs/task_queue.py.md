# specs/task_queue.py.md — 编码规格

> **模块文件**:`pyharness/core/task_queue.py` | **功能编号**:F043(顺序任务队列)· F044(每任务独立日志段)· F025(取消传播联动)· F015(审批等待暂停联动) | **权威口径**:PRD-Core §5.5 F043/F044(功能与验收伪代码权威)、EVENT-SCHEMA §3.5.1(task.*/segment.* 事件字段级权威)、EVENT-SCHEMA §3.5.3(queue.suspended/resumed)、ERR.md §2.11(QUE-001)/§2.2(EVT-1xx)/§2.10(CYC-999)、DIS-CORE §0.2(全局约定:ctx 注入/INV-01/INV-07)
> **一句话**:会话级 FIFO 任务队列——一次用户意图=一个任务,同一时刻仅一个 running、其余 waiting(F043 顺序执行,一个做完下一个);每个任务执行期由泵写 segment.start(强同步)/segment.end 围出**独立日志段**(F044,以 task_id 切分查询/回放/预算);暂停/恢复/取消/状态查询全部事件化;plan_mode(F046)/schedule(F048)/jobs(F051) 均经本模块入队复用纪律。

## 模块职责

1. **顺序执行(F043)**:`submit(intent)` 尾插入队并立即返回 task_id,写 `task.enqueued{task_id,pos}`;泵 `_pump()` 是唯一放行口——同一时刻仅一个 running,其余 waiting,`running` 结束(成功/失败/取消)后队首自动接续;**并发上限硬约束:max_running=1**(PRD F043 验收伪代码 `while self._q and self._running is None`),jobs/subagent 的并发(≤4/≤8)由各自模块承担,本队列不放开。
2. **每任务独立日志段(F044)**:`_run_task` 在每个任务开始时写 `task.started`(普通)后立即写 `segment.start`(**强同步 sync=True**,段锚先落盘再执行,崩溃后回放段不悬空,EVENT-SCHEMA §8.1),结束时写 `task.completed/task.failed` 与 `segment.end{start_seq}` 配对;段查询走 `session.events_between(start_seq,end_seq)`(session 模块,本模块只引用);**段不嵌套**(子 Agent 独立 session);compaction(F058) 以整段为最小折叠单位,不切半。
3. **队列状态可查**:`status()` 返回 running 当前任务、waiting 全列表、暂停原因、队深——用户可见"后面还有几个"(F043 设计理由);终态事件每任务至多一个(started 前必有 enqueued)。
4. **暂停/恢复不饿死(F015 联动)**:审批等待(approval-pending)/预算暂停(budget)调用 `pause(reason)`;挂起只停消费不停运行,等待不超时、任务不丢;重复挂起合并(先 suspended 后 resumed,EVENT-SCHEMA §3.5.3);全部原因解除后才写 `queue.resumed`。
5. **取消(F025)**:`cancel(task_id)`——running 任务传播取消(不吞 re-raise,`system.cancelled` 审计后置 running=None,队首自动接);waiting 任务直接摘除;取消统一归一化为 `task.failed{reason:"cancelled"}`。
6. **队深上限**:waiting ≥32 拒新(`QUE-001`,功能特性码,ERR §2.11);拒绝是显式事件化拒绝,不静默丢请求。

## 依赖

- **依赖方向**(§0.3 拓扑):位于 agent-loop 之上、编排/外壳层;消费 `session.append`(事件)、`agent_loop.run_for_task(t)`(F007 三态机,绑定 task_id)、`errors`(PyHError,F020)。
- **消费方**:plan_mode.plan_execute(F046 逐步入队+wait_for)、schedule._fire(F048 到点入队任务模板)、命令层/CLI(用户 submit)、UI 队列视图、compaction(F058 以段为折叠单位)。
- **外部依赖**:`asyncio`(泵与等待,单进程协程并发原则 5/INV-07);事件词表 task.enqueued/started/completed/failed + segment.start/end + queue.suspended/resumed + system.cancelled(EVENT-SCHEMA §3.5.1/§3.5.3/§3.5.6);不 import 外围能力,入队者(plan/schedule/CLI)经 ctx/参数注入(INV-08)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `Task` | dataclass | `id: str`(t-N 单调,可显式指定供 plan/schedule 溯源)/ `intent: str` / `meta: dict\|None` / `enqueued_seq: int`;终态只以事件为准,任务对象不含状态位(INV-01) |
| `TaskResult` | dataclass | `ok: bool` / `code: str\|None`(失败错误码或 "cancelled") / `summary: str\|None` / `duration_ms: int`;wait_for 返回 |
| `QueueStatus` | dataclass | `running: str\|None` / `waiting: list[str]` / `paused: bool` / `pause_reasons: list[str]` / `depth: int` |
| `_q` | deque[Task] | waiting 队列,尾插头弹;**len(_q) ≥ 32 → QUE-001 拒新** |
| `_running` | Task\|None | 执行中任务;同一时刻至多一个(F043 硬约束 max_running=1) |
| `_pause_reasons` | Counter[str] | 挂起原因计数;计数>0 即 suspended;resume 只减计数,**重复挂起合并** |
| `_pump_task` | Task\|None | 泵协程句柄(单例,防多泵并发弹任务) |
| `_futures` | dict[str, Future] | task_id → 终态 Future,wait_for 填充 |
| 事件 | — | task.enqueued/started/completed/failed、segment.start(**强同步**)/end、queue.suspended/resumed、system.cancelled |

## 类与函数清单

### `async def submit(self, intent: str, *, meta: dict | None = None, task_id: str | None = None) -> str` — 入队(F043)

**功能一句话**:用户意图尾插入队并返回 task_id;队深 ≥32 抛 QUE-001;写 task.enqueued{pos};泵未在跑则启动泵单例——入队后即由队列接管执行。

```python
async def submit(self, intent, *, meta=None, task_id=None):
    if not intent or not intent.strip():                 # 空意图拒入队(白名单前置)
        raise PyHError("EVT-100", ctx={"hint": "空任务意图,submit 拒绝"})
    if len(self._q) >= 32:                               # 队深上限(F043 边界:满拒)
        raise PyHError("QUE-001", ctx={"advice": "队列已满(≥32),请等当前任务结束或取消"})
    t = Task(id=task_id or f"t-{self._seq.next()}",      # 显式 task_id 供 plan/schedule 溯源
             intent=intent, meta=meta)
    self._q.append(t)                                    # FIFO:尾插
    await session.append("task.enqueued",
        {"task_id": t.id, "pos": len(self._q)}, actor="system")
    if self._running is None and self._pump_task is None:
        self._pump_task = asyncio.create_task(self._pump())   # 泵单例(防重入)
    return t.id
```

**参数表**:`intent`=任务意图文本(一次用户意图=一个任务);`meta`=附加上下文(如 plan 步/schedule 来源);`task_id`=可选显式 id。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 队深 ≥32 拒新 | QUE-001 | 等待/取消后重试;查 F043 边界 |
| `PyHError` | 空意图 | EVT-100 | 入队前校验意图非空 |

**关联测试**:test_f043_queue(FIFO 顺序/队满 QUE-001/pos 递增)、GWT-F043-01(同一时刻仅一个 running)。

### `async def _pump(self) -> None` — 单飞泵(F043 核心)

**功能一句话**:唯一执行放行口——暂停期不弹任务(等裁决不饿死),队空即退场由下次 submit 唤醒;否则队首 popleft 置 running 并执行,finally 清位使队首自动接续。

```python
async def _pump(self):
    while True:
        if self._suspended():                            # 暂停期:只停消费,不丢队不超时
            await self._resume_evt.wait()                # resume 时置位唤醒(非忙等)
            self._resume_evt.clear(); continue
        if not self._q:                                  # 队空:泵退场;下次 submit 重启泵
            self._pump_task = None; return
        t = self._q.popleft(); self._running = t         # 队首接跑
        try:
            await self._run_task(t)                      # 执行(内部含段锚与终态事件)
        except Exception:                                # 泵自身兜底:单任务失败已事件化
            log.exception("task pump crash task=%s", t.id)   # CYC-999 不吞但保泵活
        finally:
            self._running = None                         # 释放 → while 自动接队首(F043)
```

**参数表**:无(内部)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 兜底 Exception | _run_task 之外未预期异常 | CYC-999 | 记录堆栈仅本地;泵继续,不中断后续任务 |

**关联测试**:test_f043_queue(串行性:任务 N+1 的 started 必晚于任务 N 的终态)、GWT-F043-02(running 取消后队首自动接)。

### `async def _run_task(self, t: Task) -> None` — 单任务执行 + 段锚围栏(F043/F044)

**功能一句话**:任务生命周期执行器——started → segment.start(强同步) → agent_loop.run_for_task(绑定 task_id)→ 终态事件(completed/failed,每任务至多一个)→ segment.end 配对关闭日志段。

```python
async def _run_task(self, t):
    await session.append("task.started", {"task_id": t.id}, actor="system")
    start_seq = await session.append("segment.start", {"task_id": t.id},
                                     actor="system", sync=True)   # 强同步:段锚先落,崩溃不悬空
    try:
        await agent_loop.run_for_task(t)                 # F007 三态机;整段内所有事件带 task_id
        await session.append("task.completed",
            {"task_id": t.id, "reason": "ok"}, actor="system")
    except asyncio.CancelledError:                       # F025:取消不吞,归一化 failed(cancelled)
        await session.append("task.failed", {"task_id": t.id,
            "reason": "cancelled", "error": "cancelled"}, actor="system")
        raise                                            # 传播取消(不吞)
    except PyHError as e:                                # 业务失败:事件化并继续队列(F043)
        await session.append("task.failed", {"task_id": t.id,
            "reason": "error", "error": e.code}, actor="system")
    finally:
        await session.append("segment.end", {"task_id": t.id,
            "start_seq": start_seq}, actor="system")      # 配对关闭;段=回放最小单位(F058)
```

**参数表**:`t`=已弹出的 Task。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `asyncio.CancelledError` | cancel 传播 | task.failed(cancelled) | 归一化记录后 re-raise;队首自动接 |
| `PyHError` | run_for_task 业务失败 | 原码(如 LLM-310) | 事件化 reason=error;队列继续 |
| 未预期异常 | 非 PyHError | CYC-999 | 堆栈仅本地;任务按 failed(error) 记录 |

**关联测试**:test_f044_segment(段边界 start_seq/end_seq 配对、段不嵌套、segment.start 崩溃后不悬空)、GWT-F044-01(闲聊无 task_id 不入段)。

### `async def pause(self, reason: str, *, by: str = "system") -> None` — 队列挂起(F015 联动)

**功能一句话**:审批等待/预算暂停时挂起队列消费——只停弹任务、运行中任务不受影响,等待不超时饿死;重复挂起合并(同一原因计数累加,事件只发一次)。

```python
async def pause(self, reason, *, by="system"):
    self._pause_reasons[reason] += 1                     # 重复挂起合并(计数,不重复发事件)
    if self._suspended(): return                         # 已挂:仅计数,event 不重复(EVT 词表)
    await session.append("queue.suspended",
        {"reason": reason, "by": by}, actor="system")
    self._resume_evt.clear()                             # 泵在 wait() 处阻塞
```

### `async def resume(self, reason: str, *, by: str = "system") -> None` — 队列恢复(F015 联动)

**功能一句话**:解除一个挂起原因——计数减一;仍有其他原因则保持挂起;全部解除才写 queue.resumed 并唤醒泵。

```python
async def resume(self, reason, *, by="system"):
    if self._pause_reasons.get(reason, 0) <= 0:          # 未挂起的 reason:幂等,无事件
        return
    if self._pause_reasons[reason] > 1:                  # 该原因多层挂起:仅减计数
        self._pause_reasons[reason] -= 1; return
    del self._pause_reasons[reason]                      # 减到 0:移除该原因
    if self._pause_reasons:                              # 仍有其他原因 → 维持挂起
        return
    await session.append("queue.resumed",
        {"reason": reason, "by": by}, actor="system")
    self._resume_evt.set()                               # 唤醒泵(先 resumed 后 resumed 事件序)
```

**参数表**(pause/resume):`reason`=approval-pending/budget 等;`by`=发起者。**异常表**:无(状态化;非法 resume 幂等无害)。**关联测试**:test_f043_queue(暂停不弹任务、裁决返回自动接续、重复挂起只一对 suspended/resumed 事件)、GWT-F015 联动(审批等待期队列暂停无超时饿死)。

### `async def cancel(self, task_id: str, *, by: str = "system") -> bool` — 任务取消(F025)

**功能一句话**:running 任务→传播取消(不吞)并留 system.cancelled 审计,队列自动接队首;waiting 任务→直接摘除并记 failed(cancelled);不存在/已终态→幂等 False。

```python
async def cancel(self, task_id, *, by="system"):
    if self._running and self._running.id == task_id:    # running:取消传播给 agent_loop
        await session.append("system.cancelled",
            {"what": f"task:{task_id}", "reason": by}, actor="system")
        agent_loop.cancel_current(task_id)               # F025:取消 re-raise 不吞(F043 接队首)
        return True
    for i, w in enumerate(self._q):                      # waiting:摘除,无需通知循环
        if w.id == task_id:
            del self._q[i]
            await session.append("task.failed", {"task_id": task_id,
                "reason": "cancelled"}, actor="system")  # 取消归一化为 failed(cancelled)
            return True
    return False                                         # 已终态/不存在:幂等 False(防重放)
```

**参数表**:`task_id`=目标任务;`by`=取消发起者(用户/系统)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 无 | 取消已终态/不存在任务 | — | 返回 False,不产生事件 |

**关联测试**:test_f043_queue(running 取消→队首自动接;waiting 取消→队列少一)、GWT-F025-01(取消 re-raise 不吞、system.cancelled 审计)。

### `def status(self) -> QueueStatus` — 队列状态查询(F043)

**功能一句话**:返回 running/waiting/paused/depth 全貌,供 UI/CLI 查询——用户可见"后面还有几个"。

```python
def status(self):
    return QueueStatus(
        running=self._running.id if self._running else None,
        waiting=[t.id for t in self._q],                 # FIFO 顺序展示
        paused=bool(self._pause_reasons),                # 挂起中(含原因明细)
        pause_reasons=sorted(self._pause_reasons),
        depth=len(self._q))
```

**参数表**:无。**异常表**:无(纯读,内存状态+事件派生一致)。**关联测试**:GWT-F043-03(status.running/waiting 与事件流一致)、UI 队列视图。

### `async def wait_for(self, task_id: str, *, timeout: float | None = None) -> TaskResult` — 外部等待任务终态(F046/jobs 复用)

**功能一句话**:plan 执行器(F046)等在入队任务上拿结果;任务终态事件填充 Future 后返回 TaskResult;timeout=None 永久等(审批挂起不饿死),超时抛 BUSY 显式拒——等待不影响任务本身(shield)。

```python
async def wait_for(self, task_id, *, timeout=None):
    fut = self._futures.get(task_id)                     # 每 task_id 一个终态 Future
    if fut is None:
        fut = asyncio.get_running_loop().create_future()
        self._futures[task_id] = fut
    try:
        if timeout is None:
            await asyncio.shield(fut)                    # 等待可被外部取消,任务不受牵连
        else:
            await asyncio.wait_for(asyncio.shield(fut), timeout)
    except asyncio.TimeoutError:
        raise PyHError("BUSY", ctx={"hint": f"等待任务 {task_id} 超时",
                                    "advice": "查任务是否仍 running/挂起"})
    return self._futures.pop(task_id).result()           # TaskResult(ok/code/summary)
```

**参数表**:`task_id`=已入队任务;`timeout`=秒(默认无限,审批等待不超时饿死)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 等待超时 | BUSY | 查任务状态;不取消任务本身(shield) |

**关联测试**:test_f046_plan_exec(逐步入队+wait_for 串行)、GWT-F043-04(wait_for 在暂停期不超时)。

### `async def open_segment(task_id: str) -> int` / `async def close_segment(task_id: str, start_seq: int) -> None` — 段锚开闭(F044 验收直译)

**功能一句话**:F044 验收伪代码原样落地——open 写 segment.start(强同步)返回其 seq;close 写 segment.end 配对;供 _run_task 与任何需按段围栏的调用方使用;段查询经 `session.events_between(start_seq, end_seq)`(session 模块,回放/复算/预算审计)。

```python
async def open_segment(task_id):
    return (await session.append("segment.start", {"task_id": task_id},
                                 actor="system", sync=True)).seq   # 强同步:返回段锚 seq

async def close_segment(task_id, start_seq):
    await session.append("segment.end",
        {"task_id": task_id, "start_seq": start_seq}, actor="system")
    # 段查询:session.events_between(start_seq, end_seq) — 供回放/复算/预算审计(F044)
```

**参数表**:`task_id`=段归属任务;`start_seq`=open_segment 返回的 seq。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 段不配对/嵌套开段(子 Agent 场景) | EVT-100 | 子 Agent 用独立 session(段不嵌套,EVENT-SCHEMA §3.5.1) |

**关联测试**:test_f044_segment(段边界/查询/嵌套禁)、compaction 段级折叠配套(F058)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.5 F043/F044(验收伪代码) | 功能与验收权威 |
| EVENT-SCHEMA.md | §3.5.1(task.*/segment.* 字段/校验/强同步)、§3.5.3(queue.*)、§8.1 落盘矩阵 | 事件字段与落盘权威 |
| ERR.md | §2.11 QUE-001、§2.2 EVT-1xx、§2.10 CYC-999、§2.4 BUSY | 错误码契约 |
| DIS-CORE.md | §1 agent-loop(run_for_task 绑定 task_id)、§3.3.3 events_between | 依赖方接口 |
| plan_mode.py.md / schedule.py.md / jobs.py.md | 本规格同族 | 消费方:逐步入队/wait_for、到点入队、JOB-001 并发边界 |
| SECURITY.md | S-1 伪造已执行、T-1 篡改日志 | 终态只认事件不认文本(本模块终态全事件化) |
