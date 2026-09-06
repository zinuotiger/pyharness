# specs/jobs.py.md — 编码规格

> **模块文件**:`pyharness/core/jobs.py` | **功能编号**:F051(后台 jobs 核心)· 联动 F043/F044(经任务段跑,事件带 task_id=job:\<id\>)/F007(agent-loop 驱动 job)/F032(超预算直接 kill)/F025(取消归一 failed(cancelled))/F015(审批挂起等主会话)/F040(todo 进度数据源)/F011(共享会话日志) | **权威口径**:PRD-Core §5.5 F051(功能与验收伪代码权威)、EVENT-SCHEMA §3.5.3(job.started/completed/failed 字段级权威)+§3.5.1(task_id=job:\<id\> 关联段)、ERR.md §2.11(JOB-001 并发≥4 拒新/TO-301)、SECURITY.md §5(通道身份:R8 headless 无通道即拒;job 无交互通道)、§8.1(审计全集含 job 事件)、CFG.md(固定约束\"jobs 并发≤4、结果 7 天清理\")、DIS-CORE §1/§2(agent-loop 编排);冲突以 PRD-Core 为准
> **一句话**:脱离前台会话运行的**后台 job 管理器(F051)**——`start/status/cancel/logs` 四入口,长任务不占对话:发出后可继续聊/关会话,稍后查结果;job 与前台**共享同一会话 JSONL 日志**,其事件带 `task_id="job:<id>"` 使 job 自成日志段(F044)可切片回放/审计/预算;并发 ≤4(JOB-001)、崩溃/超预算自动失败并告警、结果 7 天清理;job **不可交互**(需审批的操作挂起等主会话,headless 直接 denied,R8);**owner 授权模型:job_id 不是机密——查询/取消只认提交方 owner 身份(by),不知道 id 不等于有权,知道 id 也不等于有权**。

## 模块职责

1. **长任务提交(F051)**:`start(intent, ctx)` 校验并发 <4(满→JOB-001 拒新)→ 构造 `Job(id="j-N", owner=ctx.owner)`→父会话落 `job.started(job_id)`(task_id=job:\<id\> 由段机制携带)→ `asyncio.create_task(_run)` 协程并发跑(原则 5,单进程单循环,不占父会话 running 态——父可继续对话)。
2. **job 执行与终态**:`_run` 在 job 自己的任务上下文(task_id="job:\<id\>")上驱动 `agent_loop.run`(F007):成功→`job.completed(job_id, elapsed_ms)`;PyHError(含超预算被杀、LLM-310)→`job.failed(job_id, elapsed_ms, reason=码)`;CancelledError→归一 `job.failed(reason=cancelled)`(F025,不吞);全部事件普通落盘,与前台共享日志即\"全留痕\"(审计/回放/预算按 task_id 过滤)。
3. **进度与状态可查**:`status(job_id, by)` 返回 state(queued/running/suspended/failed/completed/cancelled)+进度——进度**从日志派生**(INV-01):由该 job 段内 `todo.updated`(F040)清单推导百分比,无 todo 则按已完成 llm 轮数/事件数估算,不维护第二份状态。
4. **owner 授权(非 id 保密)**:`Job.owner` = 提交方身份(会话绑定用户 id,如 `cli:alice`/`acp:<client>`/`web:<session>`,由通道上下文打,非 LLM 自报);`status/cancel/logs/list_owned` 全部先验 `by==owner or by=="system"`,失败→GRD-401(scope-hidden 语义);`job_id="j-N"` 顺序可猜、**明确不是机密**,泄露 id 不给任何读取权——防\"猜 id 偷看他人 job\"。
5. **不可交互纪律(SECURITY §5/R8)**:job 内部需要审批(danger≥high)时——有主会话通道:挂起等主会话裁决(队列 suspended 语义,审批期 job 暂停不超时饿死);无通道(headless/会话已关):直接 denied,无\"静默等待/自动同意\"路径;job 自己永远不能裁决。
6. **取消与超预算 kill**:`cancel(job_id, by)` 沿 await 链传播 CancelledError(F025)→`system.cancelled`+`job.failed(cancelled)`;预算硬闸 F032:job 每轮用量超限→直接 kill(reason=budget,不做暂停审批——后台无人盯,PRD F051 边界)。
7. **结果清理**:job 终态后句柄保留 7 天(结果可查),超期 `reap_expired()` 摘句柄(事件仍留日志,审计不删);进程/会话关闭时运行中 job 被 child-first 取消并落 failed(reason=session-closed),与 subagent 同序(先落终态再 finished)。

## 依赖

- **依赖方向**:jobs 位于编排层(agent-loop 之上);消费 `agent_loop.run`(F007 唯一 LLM 入口)、`session.append`(job 事件+共享日志)、`session.events_between`(段切片:logs/status 进度)、`task_queue.open_segment/close_segment`(F044 段锚,task_id=job:\<id\>)、`scope/budget`(F032 每轮预检)、`todo` 状态(F040 进度源);被命令层(CLI `job` 子命令 F064)/UI(通知)/subagent 平级引用。
- **消费方**:CLI `pyharness job start|status|cancel|logs`(F064)、桌面 UI(完成通知/进度条)、agent-loop(完成后可选通知主会话)、审计(按 task_id 切 job 段)。
- **外部依赖**:errors(PyHError)、事件词表 job.started/completed/failed+task.*/segment.*(EVENT-SCHEMA §3.5.1/§3.5.3)、EventBus(完成通知广播);不依赖 schedule/workflow 实现(它们提交到本管理器或 task_queue,INV-08)。
- **钩子契约**:同 subagent.py——注册 `ctx.hooks.on_close`(agent.close 在 `session.finished` 前回调),child-first 取消全部在途 job 并先落 `job.failed(session-closed)`,否则父 finished 后回写撞 EVT-104。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `Job` | dataclass | `id: str`(j-N 单调)/ `owner: str`(提交方身份,授权主体)/ `intent: str` / `state: str`(queued/running/suspended/failed/completed/cancelled)/ `task_id: str="job:"+id`(段与事件归属)/ `created_seq`/`started_seq`/`finished_seq: int` / `started_ts: str` / `elapsed_ms: int\\|None` / `reason: str\\|None`(failed 原因:码或 cancelled/budget)/ `progress: float\\|None`(0..1,从段内 todo 派生)/ `notify_to: str\\|None`(完成通知目标会话/通道)/ `expires_at: float`(终态+7 天) |
| `JobStatus` | dataclass | `job_id/state/owner/progress: float\\|None/todo_summary: str/elapsed_ms/log_tail: list[dict](最近 ≤10 条事件摘要)/error: str\\|None` |
| `_running` | dict[str, Job] | 在途+7 天内句柄注册表(id→Job);终态事件仍在 JSONL,句柄过期即摘(INV-01 可重建) |
| `_limit` | int = 4 | 并发上限(PRD F051 固定 4;JOB-001 满拒) |
| `_retention_days` | int = 7 | 结果保留期 |
| 事件 | — | job.started/job.completed/job.failed(elapsed_ms, reason)+段事件 task.*/segment.*(task_id=job:\<id\>);actor=system;落盘=普通;共享父会话 JSONL |

## 类与函数清单

### `async def start(self, intent: str, ctx, *, meta: dict | None = None) -> str` — 提交后台 job(F051 验收直译)

**功能一句话**:校验并发 <4(JOB-001)→ 建 Job(owner=ctx.owner)→ 落 `job.started` → 起协程 `_run`;返回 job_id(j-N,顺序可猜非机密);父会话 running 不受影响可继续对话。

```python
async def start(self, intent, ctx, *, meta=None):
    if len(self._running) >= self._limit:          # 并发 ≤4:满拒(JOB-001)
        raise PyHError("JOB-001", ctx={"running": len(self._running),
            "advice": "先等若干 job 终态或取消后再提交"})
    j = Job(id=f"j-{self._seq.next()}", owner=ctx.owner, intent=intent,
            state="queued", task_id=f"job:j-{self._seq.cur}",
            notify_to=getattr(ctx, "owner_channel", None))
    self._running[j.id] = j                        # 先登记(事件失败可回滚)
    try:
        await session.append("job.started",
            {"job_id": j.id, "task_id": j.task_id}, actor="system")
    except PyHError:
        self._running.pop(j.id, None); raise       # 事件没落成 = 没启动
    j.started_ts = now_iso(); j.state = "running"
    j.task = asyncio.create_task(self._run(j, ctx))  # 协程并发,不占父 running
    return j.id
```

**参数表**:`intent`=job 目标文本;`ctx`=父会话上下文(owner/预算/事件通道);`meta`=附加(notify 目标等)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 并发 ≥4 | JOB-001 | 等终态/取消旧 job 后重试 |
| `PyHError` | intent 空/超长(>64KB) | EVT-100 | 校验输入 |
| `PyHError` | 父会话 closed/finished | EVT-104 | 新开会话再提交 |

**关联测试**:test_f051_jobs(并发上限/失败告警/查询)、GWT-F051-01(第 5 个并发 job 被 JOB-001 拒)。

### `async def _run(self, j: Job, ctx) -> None` — job 执行体(段围栏+F032+F025)

**功能一句话**:为 job 开日志段(segment.start,强同步锚)→ 在 job 任务上下文上跑 `agent_loop.run` → 各终态分支归一落 `job.completed/failed`(含 elapsed_ms)→ 关段;任何异常不得逃逸(兜底 failed CYC-999)。

```python
async def _run(self, j, ctx):
    start_seq = await task_queue.open_segment(j.task_id)     # F044 段锚,强同步
    t0 = time.monotonic()
    try:
        await agent_loop.run(ctx, intent=j.intent,           # F007;task_id 已绑段
                             task_id=j.task_id, budget=ctx.budget)
        j.state = "completed"                                # 成功分支
        reason = "ok"
    except PyHError as e:                                    # 结构化失败分支
        j.state, reason = "failed", e.code                   # 含 budget/LLM-310
    except asyncio.CancelledError:                           # 取消分支(F025)
        j.state, reason = "failed", "cancelled"; raise       # re-raise 不吞
    except Exception:                                        # 兜底分支
        j.state, reason = "failed", "CYC-999"
    finally:
        j.elapsed_ms = int((time.monotonic() - t0) * 1000)
        j.expires_at = time.time() + self._retention_days * 86400
        ev = "job.completed" if j.state == "completed" else "job.failed"
        await session.append(ev, {"job_id": j.id, "elapsed_ms": j.elapsed_ms,
                                  "reason": reason}, actor="system")
        await task_queue.close_segment(j.task_id, start_seq) # 关段
    await self._notify(j)                                    # 完成通知
```

**参数表**:`j`=Job;`ctx`=父上下文。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 段内运行失败(透传) | 原码入 reason | 查 job.failed.reason 定位 |
| `PyHError` | 超预算 | budget | 后台无人盯,直接 kill(F032 边界) |
| `asyncio.CancelledError` | cancel/会话关闭 | cancelled | 已声明 system.cancelled,不可续跑 |

**关联测试**:test_f051(失败告警/elapsed_ms/段闭区间完整)、test_f044(段不嵌套:job 段与主任务段平级)。

### `async def status(self, job_id: str, *, by: str) -> JobStatus` — 状态/进度查询(owner 授权)

**功能一句话**:`by` 非 owner 且非 system → GRD-401(知道 job_id 不等于有权);授权后组装 JobStatus:state + 进度(**从段内 todo.updated 派生**,INV-01)+ 最近日志尾;job 不存在/超期 → EVT-101。

```python
async def status(self, job_id, *, by):
    j = self._get(job_id)                            # 不存在/已清理 → EVT-101
    self._authorize(j, by)                           # owner 或 system;否则 GRD-401
    todos = await self._segment_todos(j)             # 段内 todo.updated 最新清单
    progress = None
    if todos:                                        # 进度=完成项占比(F040 派生)
        done = sum(1 for t in todos if t["done"])
        progress = round(done / len(todos), 3)
    tail = await self._log_tail(j, n=10)             # events_between 切片
    return JobStatus(job_id=j.id, state=j.state, owner=j.owner,
                     progress=progress, todo_summary=f"{done}/{len(todos)} 项",
                     elapsed_ms=j.elapsed_ms, log_tail=tail, error=j.reason)
```

**参数表**:`job_id`;`by`=请求方身份(通道上下文)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | by ≠ owner 且非 system | GRD-401 | 用提交方会话查询;job_id 非机密不构成授权 |
| `PyHError` | job 不存在/已超期清理 | EVT-101 | 核对 job_id;job 事件仍可经日志按 task_id 查 |

**关联测试**:test_f051(查询)、GWT-F051-02(非 owner 查他人 job → GRD-401 且无泄漏)。

### `async def cancel(self, job_id: str, *, by: str) -> bool` — 取消后台 job(F025 归一)

**功能一句话**:owner 授权后取消在途 job:传播 CancelledError→写 `system.cancelled(what=job:\<id\>)`→`_run` 落 `job.failed(cancelled)`;已终态/不存在幂等返回 False;job_id 猜中不可取消他人 job。

```python
async def cancel(self, job_id, *, by):
    j = self._get(job_id)                            # 不存在 → EVT-101
    self._authorize(j, by)                           # owner 授权闸(GRD-401)
    if j.state in ("completed", "failed", "cancelled"):
        return False                                 # 终态幂等
    await session.append("system.cancelled",
        {"what": j.task_id, "reason": "user-cancel"}, actor="system")
    if j.task: j.task.cancel()                       # 沿 await 链传播(F025)
    return True
```

**参数表**:`job_id`;`by`=请求方身份。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 越权取消 | GRD-401 | 仅 owner/system 可取消 |
| `PyHError` | job 不存在 | EVT-101 | 核对 id |

**关联测试**:test_f025(取消事件/释放/re-raise)、test_f051(cancel→job.failed(cancelled))。

### `async def logs(self, job_id: str, *, after_seq: int = 0, by: str) -> list[Envelope]` — 最近日志(owner 授权)

**功能一句话**:owner 授权后从**共享会话 JSONL** 按 `task_id=job:\<id\>` 切片返回该 job 段事件(seq 升序,after_seq 增量拉取),供 CLI/UI 追踪;越权/不存在同上拒。

```python
async def logs(self, job_id, *, after_seq=0, by):
    j = self._get(job_id); self._authorize(j, by)
    # events_between 以段 [start_seq..end_seq] 为界,按 task_id 过滤(job 专用切片)
    return [e for e in session.events_after(after_seq)
            if e.task_id == j.task_id]               # 只读投影,绝不写日志
```

**参数表**:`job_id`;`after_seq`=增量起点;`by`=身份。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 越权/不存在 | GRD-401/EVT-101 | 同上 |

**关联测试**:test_f051(logs 切片边界=段闭区间、增量 after_seq 语义)。

### `async def _notify(self, j: Job) -> None` — 完成通知

**功能一句话**:job 终态后发完成通知:总线广播 job.completed/failed(UI/CLI 通知订阅者收),若登记 `notify_to` 目标会话则注入一条可见摘要(目标仍活才写,finished 后跳过只记日志)。

```python
async def _notify(self, j):
    await bus.emit(j.state, {"job_id": j.id, "elapsed_ms": j.elapsed_ms,
                             "reason": j.reason})   # UI/CLI 订阅者收(瞬时广播)
    if j.notify_to and j.state == "completed":
        if not session.is_closed():                  # 目标会话活着才写
            await session.append("agent.message",
                {"content": f"[后台任务 {j.id} 完成] {j.intent[:120]}"})
```

**参数表**:`j`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 目标会话 finished 后补写 | EVT-104 | 跳过通知仅记日志(通知非强同步事实) |

**关联测试**:test_f051(完成通知;会话关闭后不补写)。

### `def list_owned(self, by: str) -> list[str]` — 我的 job 列表(owner 授权)

**功能一句话**:返回 `by` 名下全部在册 job_id(含终态 7 天内),供 UI \"我的任务\"面板;by=system 列全部。

```python
def list_owned(self, by):
    return [jid for jid, j in self._running.items()
            if j.owner == by or by == "system"]      # 纯读,不抛
```

**参数表**:`by`=请求方身份。**异常表**:无。**关联测试**:test_f051(list 只含 owner 名下)。

### `async def reap_expired(self) -> int` — 结果 7 天清理

**功能一句话**:摘除全部终态超过 7 天的句柄(内存注册表瘦身;事件永久留 JSONL 不删,审计/重放不受影响);返回清理数;由管理器定时器/会话空闲期调用。

```python
async def reap_expired(self):
    now = time.time(); reaped = []
    for jid, j in list(self._running.items()):
        if j.state in ("completed", "failed", "cancelled") \
                and j.expires_at and j.expires_at < now:
            reaped.append(jid); self._running.pop(jid, None)   # 只摘句柄
    return len(reaped)
```

**参数表**:无。**异常表**:无(纯内存清理;底层日志不可变)。**关联测试**:test_f051(7 天后句柄消失、日志事件仍在)。

### `async def _on_session_closing(self, type_, payload) -> None` — child-first 清理钩子

**功能一句话**:父会话关闭前(agent.close 回调序同 subagent.py):取消全部在途 job→await→补落 `job.failed(session-closed)` 终态→摘净;保证 job 终态事件永远先于 `session.finished`。

```python
async def _on_session_closing(self, type_, payload):
    live = [j for j in self._running.values() if j.state == "running"]
    if not live:
        return
    for j in live:                                   # ① 全部取消
        await session.append("system.cancelled", {"what": j.task_id,
            "reason": "session-closed"}, actor="system")
        j.task.cancel()
    await asyncio.gather(*(j.task for j in live),
                         return_exceptions=True)     # ② 等 _run 落各自 failed
```

**参数表**:`type_/payload`=总线回调签名。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 补写撞 finished | EVT-104 | 钩子序保证先于此;兜底日志 |

**关联测试**:test_f051(会话关闭→在途 job 全部 failed(session-closed),无孤儿协程)。

## 关联文档

- PRD-Core.md §5.5 F051(功能与验收伪代码权威)、§5.5 F044(段:job 事件带 task_id)、§2.3(ctx.agent 命名空间)
- EVENT-SCHEMA.md §3.5.3(job.started/completed/failed 字段级)、§3.5.1(task_id=job:\<id\> 关联段)、§8.1(普通落盘)
- SECURITY.md §5(通道身份/R8:job 无交互通道 headless 即拒)、§8.1(审计全集:job 事件入审计)、§4.5(审批等待:job 挂起等主会话)
- ERR.md §2.11(JOB-001/TO-301)、§2.2(EVT-1xx)、§2.5(GRD-401)、§2.10(CYC-999)
- CFG.md(固定约束\"jobs 并发≤4、结果 7 天清理(F051)\";headless 判定 R8 无配置开关)
- specs/task_queue.py.md(F043 队列与段 API 复用:jobs 经 open_segment/close_segment 而非占用前台队)、specs/subagent.py.md(child-first 清理同序)、specs/commands.py.md(CLI job 子命令 F064)
