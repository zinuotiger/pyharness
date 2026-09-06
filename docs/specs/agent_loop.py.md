# specs/agent_loop.py.md — 编码规格

> **模块文件**:`pyharness/core/agent_loop.py` | **功能编号**:F007(核心)· F017 · F025 · F032(联动) | **权威口径**:PRD-Core §2.2(模块1)/§5.2 F007 / DIS-CORE §1(伪代码级)
> **一句话**:三态机循环驱动器——驱动"用户输入 → LLM →(工具→LLM)ⁿ → 终态",轮数/收敛/预算/取消四类终态闸在此强制,全框架唯一允许调 `llm.chat` 的入口(INV-02)。

## 模块职责

1. **主循环驱动**:消费 `user.message`(已强同步落盘)→ 每轮从会话日志现派生上下文(DIS §0.2 口径:run=外壳一次 submit 触发;轮=run 内一次 LLM 调用及其工具) → `llm.chat` → 纯文本即终态 / 工具调用逐个执行后进入下一轮。
2. **三态机 + 单 running 互斥**:顶层三态 `idle / running / terminated`(PRD F007);`paused / stopping` 为 DIS 显式化的 running 受控子状态,服务审批/取消审计(不改变顶层三态语义);`state + Lock` 双保险,同会话同时仅一个 running。
3. **终态闸强制**(全部只读判定、收尾归主循环,判定与执行分离 = 终态无旁路):闸1 轮数 `turn >= max_turns` → `max_turns`(默认 30,可配置,阶段1 验收测试用 mock 小值 3);闸2 取消 `run.cancelled` → `cancelled`;闸3 预算 `scope.budget_state() ∈ {paused, exhausted}` → `budget`(F032 硬闸,阶段1 默认恒 allow,钩子先就位)。
4. **收敛终止**:连续 `stall_limit`(默认 3)轮工具结果指纹不变 → `stall`,防空转烧钱。
5. **不变量承载**:INV-02(本模块是 `llm.chat` 唯一合法调用方,绕过即架构违规);历史每轮从日志派生(INV-01),本模块不持有任何"消息列表"第二份状态。
6. **终态收尾分工**(DIS 细化 §0.2.6):`session.finished` 全生命周期至多一条(EVT-104)且只由 `agent.close` 写;本模块 `_finish` 只返回 `RunResult(reason)`,headless 模式下外壳在 run 结束后自动 `close(reason)` → finished 落盘;交互会话跨 run 存活,run 自然结束后回 idle 等下一输入。

## 依赖

- **依赖方向**(单向,INV-08,启动按拓扑序硬接线):`agent_loop → {agent, llm, session, scope}`;session → persistence;本模块**禁止** import 外围能力(工具/子代理等一律经 `ctx.*` 注入调用)。
- 全局注入对象(PRD §5.0):`ctx`(Agent 会话实体,聚合 session/llm/tools/scope/loop)、`bus`(事件总线,分发由 session.append 内部完成)、`log`。
- 关联模块:llm(F012/F013:超时/退避/降级内部消化)、session(F009:derive_messages + append)、scope(F032 预算闸、窗口 tokens)、tools(F008:schemas_for + execute 四关内聚)。

## 类与函数清单

### 关键数据结构(字段级)

| 字段 | 类型 | 规则 |
|---|---|---|
| `state` | `Literal["idle","running","paused","stopping","terminated"]` | 顶层三态 + 受控子态(§状态机);idle 才能起新 run |
| `pending` | `deque[Envelope]` | running 期输入队列,FIFO;上限 `queue_limit=10`,队深超限拒新(BUSY 错误事件),不静默丢 |
| `current` | `Optional[RunContext]` | 执行中 run;idle 时为 None |
| `max_turns` / `stall_limit` | int = 30 / 3 | 轮数上限/收敛阈值,全进配置(`cfg.loop.*`),阶段1 默认值即上述 |
| `queue_limit` | int = 10 | 队深上限(§边界) |
| `headless` | bool = False | run 结束自动关会话(外壳注入,决定 finished 归属路径) |
| `_child_tasks` | `set[asyncio.Task]` | run 派生工具/审批等子任务登记,cancel 时统一传播(F025) |
| `RunContext` | run_id/input_seq/turn/reason/stall_streak/cancelled | 单 run:uuid4;输入信封 seq;轮计数;终态原因;收敛计数;取消标志 |
| `RunResult` | run_id/reason/turns/last_seq | reason ∈ {complete, max_turns, budget, cancelled, stall, timeout, error, close} |

### 状态机(ASCII + 转移表)

```text
 submit(队空)             submit(队满)→system.error[BUSY] 拒新
   ▼                            │
  IDLE ────────► RUNNING ◄──────┴─ 队非空→取下一件(仍 RUNNING)
   ▲  ▲              │   │
   │  │ 审批/预算恢复 │   ▼ 取消/超时         会话开→IDLE
   │  │◄── PAUSED ──┘  STOPPING ─清理完成─┤
   │  └ 自然完成/三闸且队空 ────────────────┘ └─会话关→TERMINATED
   └── close(任意态)──────────────────────────► TERMINATED(终局)
```

| 当前→目标 | 触发 | 动作与事件 |
|---|---|---|
| idle→running | wake 且队空 | 消费输入(user.message 已强同步落盘);新 RunContext |
| running→running | run 结束且队非空 | 取下一 pending 输入,新 RunContext,继续 |
| running→paused | 审批(F015)/预算暂停 | 挂起;approval.requested / budget.paused 事件 |
| paused→running | granted 且策略复核过 | 重入 guard 链起点(审批 ≠ 放行,原则 3) |
| running→stopping | 用户/超时取消 | `cancel()`:system.cancelled 强同步 + 子任务传播 |
| stopping→idle/terminated | 清理完成 | 会话开 → idle;会话关 → finished(close 写) |
| 任意→terminated | close(任意态) | session.finished 仅一次(EVT-104) |

### `async def run(ctx: Agent) -> RunResult` — 主循环入口(F007)

**功能**:`wake` 拉起的主循环;断言 idle + 拿锁防重入;逐轮三闸前置判定;异常分级收尾;`finally` 无条件回 idle、清 current。

**参数表**:`ctx` = Agent(聚合 session/llm/tools/scope + loop)。**返回**:`RunResult`(run_id, reason, turns, last_seq);本函数不写 session.finished(归属 agent.close,DIS §0.2.6)。

```python
async def run(self, ctx):                        # 前置:state==idle;Lock 防重入
    async with self.lock:
        if self.state != "idle": raise PyHError("BUSY", ctx={"advice": "loop 忙,输入已入队或被拒"})
        run = RunContext(run_id=uuid4().hex, input_seq=ctx.input_seq,
                         turn=0, stall_streak=0, cancelled=False)
        self.state, self.current = "running", run
    try:
        while True:                              # 轮 = 一次 LLM 调用及其全部工具
            if (r := self._must_stop(ctx, run)) is not None:      # 三闸前置
                return self._finish(ctx, run, r)
            ctx.scope.check_budget()             # F032:超限抛 BudgetExhausted→budget
            outcome = await self.run_turn(ctx, run)               # 单轮(含工具步)
            if outcome is not None: return self._finish(ctx, run, outcome)
    except asyncio.CancelledError:               # F025:不吞;外层(close/cancel)收尾
        run.cancelled = True; raise
    except BudgetExhausted:  return self._finish(ctx, run, "budget")
    except MaxTurnsExceeded: return self._finish(ctx, run, "max_turns")
    except PyHError as e:                        # LLM-310 等结构化错误
        await ctx.session.append("system.error",
            {"code": e.code, "message": e.to_llm_text()}, actor="system")
        return self._finish(ctx, run, "error")
    except Exception as e:                       # 未预期兜底:CYC-999,堆栈仅本地
        log.exception("loop fatal", run_id=run.run_id)
        await ctx.session.append("system.error",
            {"code": "CYC-999", "message": str(e)}, actor="system")
        return self._finish(ctx, run, "error")
    finally:
        self.current = None; self.state = "idle"   # 回 idle 等下一输入/队内下一件
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError("BUSY")` | 非 idle 时并发 run(锁前置) | BUSY | 输入已在队内/拒新;等当前 run 结束 |
| `asyncio.CancelledError` | 取消传播(F025) | —(re-raise) | 置 cancelled;外层 close 写 finished(cancelled) |
| `BudgetExhausted` | scope 预算超限 | —(终态 reason=budget) | 强制终态,无"再给一次机会" |
| `MaxTurnsExceeded` | 轮数闸触发 | —(reason=max_turns) | 强制终态 |
| `PyHError(LLM-3xx…)` | llm.chat 链尾全败等 | LLM-301/302/303/304/310 | system.error 留痕 → reason=error |
| 任意 `Exception` | 未预期 | CYC-999 | system.error 留痕 → reason=error;堆栈仅本地 |

**关联测试**:GWT-L1-01(纯文本 1 次 llm.chat、agent.message 落盘、回 idle、headless finished(complete))、GWT-L1-02(max_turns=3 恒 tool_calls → 恰 3 次、reason=max_turns、无第 4 次)。

### `async def run_turn(ctx, run) -> Optional[str]` — 单轮驱动器

**功能**:一轮 = 派生历史 → llm.chat → 纯文本直接终态 / tool_calls 逐工具步执行 → 轮计数 +1 → 收敛指纹判定;返回 None 表示"继续下一轮",返回终态原因字符串即收尾。

**参数表**:`ctx`、`run`(当前 RunContext)。**返回**:`None | reason(str)`。

```python
async def run_turn(self, ctx, run):
    hist = ctx.session.derive_messages(ctx.scope.window_tokens)   # 日志现派生(INV-01)
    resp = await ctx.llm.chat(hist, tools=ctx.tools.schemas_for(ctx.scope))
    # llm 内部已做:三档超时(F017)→退避(F028)→降级(F013);llm.request/usage 已落日志
    if not resp.tool_calls:                      # 纯文本 → 自然终态
        await ctx.session.append("agent.message",
            {"content": resp.content}, actor="agent",
            trace={"parent_seq": resp.seq})
        return "complete"                        # finished 由外壳 close(reason) 落
    for call in resp.tool_calls:                 # 校验/guard/审批/Provider 全在 tools.execute 内
        await self.run_step(ctx, run, call)      # 单步失败→事件化回喂,不中断本轮
    run.turn += 1
    fp = self._outcome_fingerprint(ctx)          # 指纹=(name,summary,ok) 元组序列
    run.stall_streak = run.stall_streak + 1 if fp == run.last_fp else 0
    run.last_fp = fp
    if run.stall_streak >= self.stall_limit:
        return "stall"                           # 连续 3 轮无新信息 → 空转封顶
    return None                                  # 下一轮
```

**异常表**:同 `run()`(向上传播,由 run 统一收尾);工具错误**不**在此上抛(见 run_step)。

**关联测试**:GWT-L1-02 轮数上限;收敛终止用"恒返同一 tool.result"mock 断言 reason=stall。

### `async def run_step(ctx, run, call) -> None` — 单工具步

**功能**:单个 tool_call 经 `ctx.tools.execute` 走完"查 Definition → pydantic 强校验(F026)→ scope 查权 → guard 链(F014)→ 审批(F015)→ Provider 执行 → 结果 spill 截断(F039)";执行失败已由 tools 层事件化(`tool.error`/`guard.rejected`)回喂 LLM,本轮不中断。

**参数表**:`ctx`、`run`、`call`(llm.response 解析出的 ToolCall,含 name/raw_args/call_id)。

```python
async def run_step(self, ctx, run, call):
    t = asyncio.create_task(ctx.tools.execute(call, ctx))   # 登记子任务(取消可传播)
    self._child_tasks.add(t)
    try:
        await t                                          # 60s 工具超时在 tools 内部(F017)
    except asyncio.CancelledError:                       # 协作式取消:已开始工具允许
        run.cancelled = True                             # 执行完并留 partial=true(F025)
        raise                                            # 不吞;取消继续传播
    finally:
        self._child_tasks.discard(t)
    # 注:TLB-802(未注册)/TLB-803(参数校验败)/TLB-805(执行异常)已由 tools.execute
    # 捕获并写 tool.error(actor=tool)回喂 LLM,不向主循环抛——本轮继续
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `asyncio.CancelledError` | 取消传播 | —(re-raise) | run.cancelled=True;已发生副作用如实留 partial |
| 其余异常 | 工具层任何失败 | 已事件化(TLB-802/803/805 等) | 回喂 LLM 自纠,本轮不中断 |

### `def _must_stop(ctx, run) -> Optional[str]` — 三闸只读判定

**功能**:每轮起点的三闸判定(轮数/取消/预算);纯只读、不写事件、不执行收尾——判定与执行分离保证终态无旁路。

```python
def _must_stop(self, ctx, run):                # 只读;收尾归主循环统一执行
    if run.turn >= self.max_turns:             # 闸1 轮数(默认 30,可配置)
        return "max_turns"
    if run.cancelled:                          # 闸2 取消(声明式置位,F025)
        return "cancelled"
    if ctx.scope.budget_state() in ("paused", "exhausted"):   # 闸3 预算(F032)
        return "budget"
    return None
```

**异常表**:无(预算事件在 scope 内落)。**关联测试**:GWT-L1-02/03/04 三闸各自独立触发断言。

### `async def _finish(ctx, run, reason) -> RunResult` — 收尾(不写 finished)

**功能**:汇总 RunResult(run_id/reason/turns/last_seq);**不写** `session.finished`——finished 唯一归属 agent.close(EVT-104);headless 由外壳在 run 返回后调 close(reason)。

```python
async def _finish(self, ctx, run, reason):
    run.reason = reason
    last_seq = ctx.session.current_seq()       # 日志当前最大 seq(派生事实,无第二份状态)
    log.info("run finished", run_id=run.run_id, reason=reason,
             turns=run.turn, last_seq=last_seq)
    return RunResult(run_id=run.run_id, reason=reason,
                     turns=run.turn, last_seq=last_seq)
```

**异常表**:无(纯汇总)。reason 语义与 close 枚举对齐:{complete, idle, timeout, budget, max_turns, cancelled, error, stall, close}。

### `async def cancel(reason="cancelled") -> None` — 取消传播协议(F025)

**功能**:声明式取消——置位 → 强同步留痕(system.cancelled)→ 取消子任务 → gather 等待收尾 → 清登记;不吞 CancelledError;append 失败不被日志故障阻塞。

```python
async def cancel(self, reason="cancelled"):
    run = self.current
    if run is None: return                     # 无在途 run:无事可取消
    run.cancelled = True                       # 置位(闸2 下一轮即触发;正在跑的步骤协作式)
    try:
        await ctx.session.append("system.cancelled",
            {"run_id": run.run_id, "reason": reason}, actor="system", sync=True)
    except PyHError as e:                      # 强同步失败:PERS-202
        log.error("cancel log failed", code=e.code)   # 取消不被日志故障阻塞
    for t in self._child_tasks:                # 传播:取消派生子任务
        if not t.done(): t.cancel()
    await asyncio.gather(*self._child_tasks, return_exceptions=True)
    self._child_tasks.clear()                  # 已开始工具如实留 partial=true
```

**参数表**:`reason ∈ {"cancelled","timeout","close"}`。**异常表**:append 强同步失败 → PERS-202(记日志,取消继续)。**关联测试**:GWT-L1-04(工具 sleep 60s → cancel → system.cancelled 已强同步落盘、3s 内回 idle、无悬空协程)。

### `async def wake(env) -> RunResult | None` — 输入唤醒/入队

**功能**:外壳(agent.submit)在 user.message 强同步落盘后调用;loop 空闲 → 直接 create_task(run) 并返回;running → 入 pending 队列(FIFO,上下文与入队序一致);队列满(>10)→ system.error[BUSY] 拒新;stopping/terminated → BUSY 拒。

```python
async def wake(self, env):
    if self.state in ("stopping", "terminated"):
        raise PyHError("BUSY", ctx={"advice": "会话正在关闭/已结束,请新开会话"})
    if self.state == "idle":                   # 空闲:直接拉起
        return await self._spawn_run(env)
    if len(self.pending) >= self.queue_limit:  # 队满:显式拒新,不静默丢
        await ctx.session.append("system.error",
            {"code": "BUSY", "advice": "输入队列已满(>10),请稍后重试"},
            actor="system", sync=True)
        raise PyHError("BUSY", ctx={"advice": "队列已满"})
    self.pending.append(env)                   # running:入队;当前 run 结束后自动取下一件
    return None
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError("BUSY")` | 队列满(>10)/会话关闭中 | BUSY | 拒新不静默;等 run 结束或新开会话 |

**关联测试**:GWT-L1-03(慢工具 5s + 并发 submit 12 条 → 10 入队、后 2 条 BUSY、FIFO 消费、上下文与入队序一致)。

### `def state_snapshot() -> dict` — 只读状态查询

**功能**:外壳/审计只读句柄:`{state, current_run_id, turn, queue_len, pending_inputs, last_reason}`;禁止由此路径修改状态。

**关联测试**:GWT-L1-01 断言回 idle 后 snapshot.state=="idle" 且 current is None。

## 边界与限制

1. **单 running 双保险**:state 字面量 + asyncio.Lock;running 中来输入仅入队,队深 >10 拒新,不静默丢。
2. **三闸封顶**:30 轮 / 3 轮收敛 / 预算硬闸;命中即终态,无"再给一次机会"通道。
3. **协作式取消**:已开始的工具允许执行完并留 partial=true;不杀进程;会话开则回 idle。
4. **INV-02**:本模块是全框架 `llm.chat` 唯一合法调用方;任何绕过路径 = 架构违规(测试钉死)。
5. **finished 单次归属**:仅 agent.close 写(EVT-104);headless = run 结束自动 close;本模块任何路径不直接写 finished。

## 关联事件(本模块直接产生/消费)

| 事件 | 方向 | 时机 |
|---|---|---|
| `user.message` | 消费(唤醒输入) | submit 已强同步落盘后 wake |
| `agent.message` | 产生 | 纯文本终态轮 |
| `system.cancelled` | 产生(强同步) | cancel()/取消传播 |
| `system.error` | 产生 | BUSY 拒新 / LLM-310 / CYC-999 / PERS-202 |
| `llm.request/response/usage/error` | 间接(经 llm 模块) | 每轮 llm.chat 内部 |

## 关联测试(汇总)

GWT-L1-01 自然结束 · GWT-L1-02 轮数上限 · GWT-L1-03 入队拒新 · GWT-L1-04 取消传播(均 `tests/acceptance/test_f007_agent_loop.py`);里程碑:阶段1 CLI 单轮对话 → JSONL 落盘 → 重启回放。

## 关联文档

- PRD-Core.md §2.2(模块1 职责/不可换理由)、§5.2 F007/F017/F025/F032、§2.4 数据流步 4-9
- DIS-CORE.md §1(本模块伪代码级唯一权威)、§0.2 会话/run/轮口径
- EVENT-SCHEMA.md §2(信封)、§3(C 模型侧/D 工具侧事件字段)
- ERR.md §2.10/§2.11(BUSY 字面量、CYC-999)、§3 明细(LLM-3xx 处置)
- ADD.md ADR-005;CFG.md(loop 相关配置项)
