# specs/subagent.py.md — 编码规格

> **模块文件**:`pyharness/core/subagent.py` | **功能编号**:F049(子 Agent 进程内派发)· 联动 F044(子会话=独立 session,段不嵌套)/F007(agent-loop 驱动子任务)/F032(子预算=父×1/4,计入父任务)/F014·F015(同权 guard,无父级担保)/F025(取消传播)/F051(与后台 jobs 的边界) | **权威口径**:PRD-Core §5.5 F049(功能与验收伪代码权威)、EVENT-SCHEMA §3.5.3(subagent.spawned/joined/failed 字段级权威,消费方注释\"主会话只记委派/回收事实\")/§2.4(trace 语义父)、SECURITY.md §6.2(最小权限:子 agent 只继承任务所需凭据子集)/§3.5(子 agent 污染回传:同权过 guard、预算 1/4、深度≤3、summary≤2KB 以数据身份入主会话)、DIS-SEAM §2.4(能力生命周期 enter→announce→detach 状态机)、ERR.md §2.2(EVT-1xx)/§2.9(TLB-8xx)/§2.11(BUSY、JOB-001);冲突以 PRD-Core 为准
> **一句话**:主 agent 派生子 agent(F049)——子任务跑在**独立 session**(自己的 JSONL、自己的 seq、自己的 workspace 根,F044 段不嵌套),与主会话共享进程/凭据口/guard 策略;主会话只落 `subagent.spawned/joined/failed` 三件委派事实(结果摘要 ≤2KB 以**数据身份**回主会话,不是第二指令源);并发 ≤8、递归深度 ≤3、子预算=父×1/4 且计入父任务;危险操作**同权过 guard、无父级担保**;模块自身按 DIS-SEAM §2.4 以能力 Provider 形态接入,`enter→announce→detach` 生命周期齐全,detach/会话关闭走 **child-first 清理**(先杀光在途子任务并落终态事件,再摘父侧接线)。

## 模块职责

1. **进程内子会话派发(F049 核心)**:`spawn(spec)` 校验约束(深度 ≤3/工具子集存在/预算 ≤ 父预算 1/4/并发 ≤8)后创建**独立子会话**(`Session.spawn(parent=父 id, budget=父预算×budget.subagent_ratio=0.25)`),子会话 seq 从 1 起、事件落子会话自己的 JSONL(父会话文件只记委派/回收三事件,EVENT-SCHEMA §3.5.3);在父会话写 `subagent.spawned(sub_id, parent_seq=发起轮锚, task)`(普通落盘)。
2. **生命周期 = 能力 Provider 三动作(DIS-SEAM §2.4)**:`enter`(装载:并发信号量≤8、登记事件类型)→ `announce`(注册 Definition `subagent.spawn` 并入 tools 注册表供 LLM 可见、挂 `ctx.agent.subagent`)→ `detach`(幂等逆操作:先 child-first 取消全部在途子任务→摘订阅→注销工具→unmount)。enter 抛错回 detached 不 announce;announce 中途失败回滚已做步骤;detach 抛错仍继续摘除(半卸>僵尸)。
3. **子任务执行与回收**:`_run_child` 把子意图交给 `agent_loop.run(sub_session, intent=spec.task, tools=spec.tools_subset)`(F007;子 session 有自己 idle/running 三态,不占用父会话 running);成功→`summarize(out, 2_000)` 摘要→父会话写 `subagent.joined(sub_id, summary)`;失败(PyHError/LLM-310/预算耗尽)→父会话写 `subagent.failed(sub_id, summary=含错误码原因)`。`join(sub_id, timeout)` 供主循环 await 回收结果,超时返回 None 由调用方决定继续/取消。
4. **安全边界(子=分工非特权,SECURITY §3.5/§6.2)**:①子会话 scope = 父策略**快照且只可更紧**(继承 deny_tools/allowed_domains;spec.tools_subset/deny_extra 只收窄,无\"父级担保\"通道);②子任务内任何危险操作照走 F014/F015(同权 guard,审批照样弹);③凭据最小权限:spec.creds_allowed 列出子任务所需凭据名子集,未列出者子侧不可读(F016 读口按 spec 过滤);④子摘要(≤2KB)回主会话后以 tool-result 同等**数据身份**进入派生历史,永远成不了 system 指令;⑤child-first 清理:任何拆除路径(用户取消/父会话关闭/模块 detach)先终止全部在途子任务,防止子任务在父会话 finished 后回写 joined → EVT-104。
5. **约束常量**:`MAX_DEPTH=3`、`MAX_CONCURRENT=8`(并发满→BUSY 拒新,语义同 JOB-001 族)、`DEFAULT_BUDGET_RATIO=0.25`(读 config `budget.subagent_ratio`)、`SUMMARY_MAX_CHARS=2_000`、child workspace 根=`~/.pyharness/workspaces/{sub_id}/`(F055 每会话独立根)。

## 依赖

- **依赖方向**:subagent 位于编排层(agent-loop 之上);消费 `Session.spawn`(F009 门面创建子会话)、`agent_loop.run`(F007,唯一 LLM 入口纪律 INV-02 由它保证)、`session.append`(父会话三事件)、`session.replay/events_between`(父会话轮锚)、`scope`(策略快照)、`credentials`(F016 按 spec 过滤读口);被 agent(close 前 shutdown 钩子)、system-prompt/agent-loop(调度入口)、compaction(F058:子会话段独立、不掺入父折叠)消费。
- **消费方**:agent-loop(F049 委派决策的执行入口)、命令层(`/spawn` 类调试命令可选)、UI(子任务时间线:父会话 spawned/joined + 按 sub_id 打开子会话轨迹)。
- **外部依赖**:errors(PyHError,F020)、事件词表 subagent.spawned/joined/failed(EVENT-SCHEMA §3.5.3)、EventBus 订阅;不依赖 jobs/workflow 实现(平级能力,INV-08 阶段 import 方向)。
- **钩子契约**:注册 `ctx.hooks.on_close`(agent.close 在 append `session.finished` **之前**回调,与 agent.py.md \"close 先 cancel 在途 run 再 append finished\" 同序);子 Agent/父会话关闭时由该钩子触发 child-first 清理并先落子终态事件,否则父 finished 后回写会被 EVT-104 拒(见 职责 5)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `SubagentSpec` | dataclass | `task: str`(委派意图)/ `tools_subset: list[str]\\|None`(只收窄父工具面;None=继承父可用集)/ `deny_extra: list[str]`(追加收紧,默认[])/ `budget_ratio: float=0.25`(≤config budget.subagent_ratio)/ `depth: int=0`(父派发时传子;递归闸 ≤3)/ `creds_allowed: list[str]`(凭据名子集,默认[]=仅继承公开无密能力)/ `notify: bool=True`(完成回写 joined) |
| `ChildState` | Literal | `spawning / running / joining / done / failed / cancelled` |
| `ChildHandle` | dataclass | `sub_id: str`(s-subxxxx)/ `parent_sid: str` / `parent_seq: int`(spawned 事件 seq,joined/failed 的 trace 锚)/ `spec: SubagentSpec` / `session`(子 Session 实体)/ `task: asyncio.Task` / `state: ChildState` / `result: Future`(join 等待)/ `summary: str\\|None` / `error_code: str\\|None` / `started_ts: str` |
| `SubagentStatus` | dataclass | `running: int` / `active_children: list[{sub_id,state,age_s}]` / `limit: int=8` |
| `_sem` | asyncio.Semaphore | 并发闸:acquire 于 spawn,release 于子任务终态(≤8,PRD F049 边界) |
| `_children` | dict[str, ChildHandle] | sub_id 索引;**只登记本进程在途子任务**,已回收即摘(历史可经事件回放重建,INV-01) |
| 事件 | — | subagent.spawned(parent_seq/task)/ joined(summary ≤2KB)/ failed(summary=原因含码);actor=`agent`;落盘=普通;父会话文件 |

## 类与函数清单

### `async def enter(self, ctx) -> None` — 能力装载(DIS-SEAM §2.4 第①动作)

**功能一句话**:装载资源:初始化并发信号量(8)、登记三个事件类型 schema(subagent.* 入总线注册表,防 announce 后 emit 撞 EVT-102);enter 失败必须回 detached 且不 announce。

```python
async def enter(self, ctx):
    if self._entered:
        return                                    # 幂等:重复 enter 静默通过
    self._sem = asyncio.Semaphore(MAX_CONCURRENT) # 并发 ≤8(F049 边界)
    bus.register_type("subagent.spawned")         # 先登记事件类型 schema
    bus.register_type("subagent.joined")
    bus.register_type("subagent.failed")
    self._entered = True                          # 全部成功才置位
```

**参数表**:`ctx`=会话上下文(总线/注册表句柄)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 总线 register_type 失败(类型已存在等) | EVT-102 | 检查词表重复注册;enter 整体失败→状态回 detached,不 announce |

**关联测试**:test_f049_subagent(深度闸/预算 1/4/同权 guard)、DIS-SEAM GWT(enter 抛错→detached 无 announce)。

### `async def announce(self, ctx) -> None` — 对外宣布(DIS-SEAM §2.4 第②动作,五步)

**功能一句话**:注册 Definition `subagent.spawn`(schema=SubagentSpec 的 LLM 可见子集,danger=none——派发本身不越权,子任务内动作仍过 guard)、挂订阅、`tools.register_tool` 暴露给 LLM、mount `ctx.agent.subagent`;任一步失败回滚已做步骤并回 detached。

```python
async def announce(self, ctx):
    done = []
    try:
        defn = Definition(name="subagent.spawn", ns="ctx.agent",
                schema=SubagentSpecView, danger="none",
                desc="派生子 agent 并行处理可隔离子任务,返回 ≤2KB 摘要")
        tools.register_tool(defn); done.append("tool")       # ① ③ LLM 可见
        locator.mount("ctx.agent.subagent", self); done.append("mount")
        bus.subscribe("session.closing", self._on_session_closing, owner="cap:subagent")
        await session.append("registry.updated", op="attach", kind="capability",
                             key="ctx.agent.subagent", actor="plugin")
    except PyHError:
        for step in done: self._rollback(step)               # 半装必回滚
        self._entered = False; raise                          # 回 detached
```

**参数表**:`ctx`=会话上下文。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 工具名冲突/ns 保留 | TLB-801/BUS-002 | 换名或先 detach;announce 回滚后重试 |

**关联测试**:DIS-SEAM §2.4 生命周期测试(announce 中途失败→无残留订阅/无 tool.registered)。

### `async def detach(self, ctx) -> None` — 能力摘除,child-first(DIS-SEAM §2.4 第③动作)

**功能一句话**:幂等逆操作——**先 child-first**:取消全部在途子任务、await 其清理、在父会话 finished 前落终态事件;再摘订阅、注销工具、unmount;任何一步抛错仍继续摘除(半卸>僵尸),失败标记 broken 告警。

```python
async def detach(self, ctx):
    if not self._entered and not self._children:
        return                                     # 幂等
    for h in list(self._children.values()):        # ① child-first:先杀孩子
        await self.cancel(h.sub_id, by="system", reason="parent-detach")
    await asyncio.gather(*[h.task for h in self._children.values()],
                         return_exceptions=True)   # 等全部真正退出
    bus.unsubscribe_all(owner="cap:subagent")      # ② 摘订阅
    tools.unregister("subagent.spawn")             # ③ 注销工具
    locator.unmount("ctx.agent.subagent")          # ④ unmount
    self._entered, self._children = False, {}      # 半卸优先于僵尸
```

**参数表**:`ctx`=会话上下文。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 任一拆除步失败 | CYC-999 | 记 broken 告警;detach 不中断,资源留待进程退出回收 |

**关联测试**:DIS-SEAM §2.4(detach 幂等:重复调用安全);test_f049(关闭时无孤儿子任务)。

### `async def spawn(self, spec: SubagentSpec, ctx) -> str` — 派生子任务(F049 验收直译)

**功能一句话**:F049 核心入口:四道约束闸(深度 ≤3/并发 ≤8/工具子集在注册表/预算 = 父×ratio 且计入父任务)全过→建独立子会话→父会话落 `subagent.spawned`→登记 ChildHandle 并起 `_run_child` 协程;返回 `sub_id`。

```python
async def spawn(self, spec, ctx):
    if spec.depth > MAX_DEPTH:                     # ① 递归闸 ≤3
        raise PyHError("BUSY", ctx={"hint": f"深度 {spec.depth} 超上限 3",
            "advice": "子任务不得再嵌套超过 3 层;考虑平铺任务或合入父意图"})
    for t in (spec.tools_subset or []):            # ② 工具子集必须已注册
        if not tools.has(t): raise PyHError("TLB-802", ctx={"tool": t})
    await self._sem.acquire()                      # ③ 并发 ≤8(不占用父 running)
    budget = ctx.scope.budget * spec.budget_ratio  # ④ 子预算=父×1/4 默认(F032)
    sub = Session.spawn(parent=ctx.session.id,     # 独立 session:自 seq=1/自 JSONL
                        budget=budget, policy=ctx.scope.snapshot())
    env = await session.append("subagent.spawned", # 父会话只记委派事实(普通落盘)
        {"sub_id": sub.id, "parent_seq": ctx.round_seq, "task": spec.task})
    h = ChildHandle(sub_id=sub.id, parent_sid=ctx.session.id, parent_seq=env.seq,
                    spec=spec, session=sub, state="spawning",
                    result=asyncio.get_event_loop().create_future())
    self._children[sub.id] = h
    h.task = asyncio.create_task(self._run_child(h, ctx)); h.state = "running"
    return sub.id
```

**参数表**:`spec`=SubagentSpec;`ctx`=父会话上下文。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 递归深度 >3 | BUSY | 平铺任务或合入父意图 |
| `PyHError` | tools_subset 含未注册工具 | TLB-802 | 核对注册表;删幻觉名 |
| `PyHError` | 并发已达 8 | BUSY | join 若干已完成子任务后再派 |
| `PyHError` | 父会话已 finished/closed | EVT-104 | 新开会话;不在终态后派发 |
| `PyHError` | 派发前校验失败(spec 非法) | EVT-100 | 修 spec(空 task/负 budget_ratio) |

**关联测试**:test_f049_subagent(深度闸/预算 1/4)、GWT-F049-01(spawn→父日志有 subagent.spawned 且子 JSONL 独立)。

### `async def _run_child(self, h: ChildHandle, ctx) -> None` — 子会话执行与回收(F049 核心)

**功能一句话**:把委派意图交给 `agent_loop.run` 在**子会话**上执行(共享进程内 guard/审批/预算纪律,INV-02 不破);终态→摘要(≤2KB)→父会话写 `subagent.joined`;PyHError/取消→`subagent.failed`;任何分支 finally 释放并发信号量并唤醒 join 等待者。

```python
async def _run_child(self, h, ctx):
    try:
        out = await agent_loop.run(h.session, intent=h.spec.task,  # F007 子循环
                    tools=h.spec.tools_subset,
                    creds_allowed=h.spec.creds_allowed)             # 凭据最小权限
        summary = summarize(out, SUMMARY_MAX_CHARS)                 # ≤2KB
        if h.spec.notify:                                           # 回收写父会话
            await session.append("subagent.joined",
                {"sub_id": h.sub_id, "summary": summary},
                trace={"parent_seq": h.parent_seq})                 # §2.4 trace 锚
        h.summary, h.state = summary, "done"; h.result.set_result(summary)
    except PyHError as e:                                           # 结构化失败
        h.error_code, h.state = e.code, "failed"
        if h.spec.notify:
            await session.append("subagent.failed",
                {"sub_id": h.sub_id, "summary": f"[{e.code}] {e.advice}"},
                trace={"parent_seq": h.parent_seq})
        h.result.set_exception(e)
    except asyncio.CancelledError:                                  # F025 取消传播
        h.state = "cancelled"; h.result.cancel(); raise             # re-raise 不吞
    finally:
        self._sem.release(); self._children.pop(h.sub_id, None)     # 释放闸+摘登记
```

**参数表**:`h`=ChildHandle;`ctx`=父上下文(事件通道)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 子任务失败(预算耗尽/LLM-310/guard 终局) | 原码透传 | 父侧读 subagent.failed 原因决定重试/放弃 |
| `asyncio.CancelledError` | 父取消/超时/会话关闭 | — | re-raise;终态事件由 cancel() 统一落 |
| `PyHError` | 父会话已 finished 无法回写 joined | EVT-104 | 已由 child-first 清理避免;兜底只记本地日志 |

**关联测试**:test_f049(joined 摘要 ≤2KB 且带 trace.parent_seq)、test_f025(取消后无悬挂子任务)。

### `async def join(self, sub_id: str, *, timeout: float | None = None, ctx) -> str | None` — 结果回收(主循环侧)

**功能一句话**:主循环 await 子任务终态取回摘要;成功→summary(≤2KB);失败→raise 原 PyHError(回喂可行动文本);timeout→None(调用方决定轮询或取消);子不存在→EVT-101。

```python
async def join(self, sub_id, *, timeout=None, ctx):
    h = self._children.get(sub_id)                 # 不在册 → 已回收或 id 错
    if h is None:
        raise PyHError("EVT-101", ctx={"hint": f"sub_id {sub_id} 不存在或已回收",
            "advice": "spawn 返回值才是合法 sub_id;结果只可 join 一次"})
    try:
        return await asyncio.wait_for(h.result, timeout)
    except asyncio.TimeoutError:
        return None                                # 调用方:再等/取消/先干别的
```

**参数表**:`sub_id`=spawn 返回值;`timeout`=秒,None=无限;`ctx`=父上下文。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | sub_id 不在册/已回收 | EVT-101 | 用 spawn 返回值;结果单次消费 |
| `PyHError` | 子任务失败(原样上抛) | 原码 | 按 to_llm_text 建议修正重派 |

**关联测试**:test_f049(join 成功路径/超时返回 None/失败上抛原码)。

### `async def cancel(self, sub_id: str, *, by: str = "system", reason: str = "user-cancel") -> bool` — 取消子任务(F025 传播)

**功能一句话**:对在途子任务取消并归一化:子循环收 CancelledError 自清理→父会话落 `subagent.failed(summary=[cancelled])` 或 system.cancelled;已终态返回 False;幂等。

```python
async def cancel(self, sub_id, *, by="system", reason="user-cancel"):
    h = self._children.get(sub_id)
    if h is None or h.state in ("done", "failed", "cancelled"):
        return False                               # 已终态/不存在 → 幂等 False
    h.task.cancel()                                # 沿 await 链传播 CancelledError
    await session.append("system.cancelled", what=f"subagent:{sub_id}",
                         reason=reason, actor="system")   # 声明式取消(父会话)
    return True
```

**参数表**:`sub_id`、`by`=取消发起方身份、`reason`=取消原因。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 父会话 finished 后补写取消事件 | EVT-104 | child-first 清理保证先杀后终态;兜底仅日志 |

**关联测试**:test_f025_cancellation(事件/释放/re-raise)、test_f049(取消后并发闸正确释放)。

### `def status(self) -> SubagentStatus` — 并发与在途查询

**功能一句话**:给主循环/UI 的只读快照:running 计数(≤8)、各在途子任务 sub_id/state/已运行秒;零副作用纯查询。

```python
def status(self):
    now = time.time()
    return SubagentStatus(
        running=len(self._children),
        active_children=[{"sub_id": h.sub_id, "state": h.state,
                          "age_s": int(now - h.started_ts)}
                         for h in self._children.values()],
        limit=MAX_CONCURRENT)
```

**参数表**:无。**异常表**:无(纯读,不抛)。**关联测试**:test_f049(并发满时 status.running==8)。

### `async def _on_session_closing(self, type_, payload) -> None` — child-first 清理钩子(父会话关闭前)

**功能一句话**:父会话关闭(agent.close 在 append `session.finished` 前回调本钩子):先取消全部在途子任务并 await,逐个落 `subagent.failed(reason=parent-closed)` 终态事件,随后才允许 finished 落盘——从结构上杜绝\"父已终态、子还在跑/回写 EVT-104\"。

```python
async def _on_session_closing(self, type_, payload):
    if not self._children:
        return                                     # 无在途 → 无事可做
    for h in list(self._children.values()):
        h.task.cancel()                            # ① 杀孩子(取消传播 F025)
    await asyncio.gather(*(h.task for h in self._children.values()),
                         return_exceptions=True)   # ② 等清理完成(含各自 failed 落盘)
    for h in list(self._children.values()):        # ③ 补漏:确保每条都有终态声明
        if h.state not in ("done", "failed", "cancelled"):
            await session.append("subagent.failed", {"sub_id": h.sub_id,
                "summary": "[parent-closed] 父会话关闭,子任务终止"},
                trace={"parent_seq": h.parent_seq})
    self._children.clear()                         # ④ 摘净后才交还 close 流程
```

**参数表**:`type_/payload`=总线回调签名。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 补写 failed 时父会话已 finished | EVT-104 | 钩子注册序保证先于此;异常仅日志告警 |

**关联测试**:test_f049(父 close→子先 cancelled/ failed→无孤儿任务)、EVENT-SCHEMA §3.5.3 事件序断言。

## 关联文档

- PRD-Core.md §5.5 F049(功能与验收伪代码权威)、§2.3(ctx.agent 命名空间)、§4.5(原则 5 单进程协程派发)
- EVENT-SCHEMA.md §3.5.3(subagent.spawned/joined/failed 字段级)、§2.4(trace 语义父)、§8.3(跨会话边界:子会话独立文件)
- SECURITY.md §6.2(凭据最小权限:按 spec 过滤)、§3.5(子 agent 污染回传防线)、§5(审批流:子任务危险操作照样弹审批)
- ERR.md §2.2/§2.9/§2.11(EVT-1xx/TLB-8xx/BUSY)、§5.2(错误传播链)
- DIS-SEAM.md §2.4(能力生命周期 enter→announce→detach 状态机与回滚语义)
- CFG.md(`budget.subagent_ratio=0.25`、固定约束\"深度≤3/并发≤8/无父级担保\")
- specs/task_queue.py.md(F043 队列与 F049 的边界:子会话不占父队列)、specs/agent.py.md(能力挂载/close 钩子序)
