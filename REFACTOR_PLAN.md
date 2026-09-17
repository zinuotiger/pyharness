# REFACTOR_PLAN.md — PyHarness → Governed Agent Runtime v1.0

> ⏱ **时点快照（Historical Baseline）** —— 本文是 **2026-09-14 的架构审计**，固化于
> `main` @ `0cba75d`。其 §0.2 的 W1~W5 问题清单**描述的是当时状态**：经 S1 阶段修复后，
> **W1/W2/W4/W5 已完成，W3 未做**（逐项见下方「§0.2.1 W1~W5 修复状态」）。
> **本文不代表当前状态**；现行能力与已知限制见 [LIMITATIONS.md](LIMITATIONS.md)。
>
> **本次仅追加本提示块与 §0.2.1 状态表**：原有结论、数字与行文**一字未改**（审计记录不得事后修饰）。

> 审计日期：2026-09-14
> 代码基线：`main` @ `0cba75d`（"sync: 审计修复(P0–P3) + Protocol v0.3 + 脱敏"），工作区干净
> 审计方式：只读。逐文件通读主链 + 4 路并行测绘 + 运行时校验（`EVENT_TYPES`/`SYNC_TYPES`/payload 模型实测计数）
> 本文性质：**架构审计与迁移方案**，不含任何代码改动，不产出实现。

---

## 0. 执行摘要

### 0.1 核心判断

**这不是一次重写，是一次"收编"。**

PyHarness 当前 ~30,900 行（`pyharness/` 62 个 `.py`），其中治理所必需的**执行机制几乎全部已存在**，但它们是**散落的、未命名的、且部分未接线的**。具体地：

| 治理原语 | 现状 | 证据 |
|---|---|---|
| 策略执行点（单调、不可绕过、唯一入口） | **已有** | `core/tools_guard.py` g1–g7 + `GuardChain.evaluate`（:672），`_FORBIDDEN_API` 结构性禁止放行 API（:629） |
| 策略标识（policy id） | **已有** | `policy_ref` 令牌：`POL-FS-1/2/3`、`POL-DGR-1`、`POL-CRED-1`、`POL-NET-1`、`POL-EXEC-1`、`POL-OVW-1`、`GRD-401`、`APR-501`、`TLB-803` |
| 人类在环裁决 | **已有且成熟** | `core/approval.py`：TTL→denied 安全默认、60s 合并、防重放 APR-503、`by` 由框架按通道打（:741） |
| 授权↔执行绑定指纹 | **已有（内存态）** | `tools_executor._approval_binding`（:218）sha1(tool+规范化参数) ⇄ `approval._grant_slots`（:217） |
| 决策留痕真源 | **已有且强** | append-only JSONL + `guard.evaluated`（每次求值恰一条，INV-04）+ `guard.rejected`（强同步，INV-05） |
| 血缘/可追溯 | **已有** | `trace.parent_seq` / `call_id` / `approval_id(=请求 seq)` / `task_id` / `segment.start-end` 段锚 |
| 确定性回放 | **已有** | `derive_messages` 纯函数 reducer（`session.py:318`）+ `open_session` 重放重建（:417） |
| **决策凭证（Receipt）** | **无** | 全库 `receipt` 命中 **0** |
| **显式治理层 / 策略版本治理** | **无** | 全库 `governance`/`治理` 命中 **0** |
| **运行时检查点（Checkpoint）** | **无** | 全库 `checkpoint` 命中 **0**（仅文档中"落地检查点"一词，非机制） |
| **证据链（Evidence，运行时）** | **无**（仅协议层纪律） | 全库 `evidence` 命中 **0**；`.ai-coding/evidence/` 只有 `.gitkeep` |
| **运行时追溯矩阵** | **无** | `traceability` 命中 **0**；F 编号↔代码↔测试的映射仅靠人肉维护文档 |

结论：**Governed Agent Runtime v1.0 = 给已有的执行机制"命名、收编、接线、留证"**。真正的净新增模块只有 4 个（Policy Engine、Decision Engine、Receipt Store、Evidence/Trace 索引），且都能架在现有事件真源上，不引入第二份状态（守住 INV-01）。

### 0.2 必须先修的 5 条（否则治理层建在沙上）

| # | 发现 | 证据 | 级别 |
|---|---|---|---|
| **W1** | **装配层绕过了策略工厂**：`tools_guard.from_config()`（:906）具备完整装配能力，但生产代码 `engine.py:474` 直接 `GuardChain(session=log_, bus=bus)`。后果：`validator=None` → **g1 g-schema 在生产恒为 allow**（:347-348）；`cfg.security.guards.disabled` 永不生效；`approval_channel=None` → 无通道场景被"视同有通道"。`from_config` 唯一调用点是 `scripts/demo_phase1.py:62` 的裸 `GuardChain()` | `engine.py:474`、`tools_guard.py:347,906` | **P0** |
| **W2** | **"paused→resume"是断的，且被测试替身掩盖**：`core/agent.py:412` 调 `await self.ctx.loop.resume()`，但 `AgentLoop` **无 `resume()` 方法**；前置条件 `loop.state == "paused"` 也永不成立（`agent_loop.py` 只赋值过 `"running"`(160)/`"idle"`(179)，`Literal` 中 `paused/stopping/terminated` 三态全为死态）。测试绿是因为 `tests/unit/test_agent.py:507,516` 用了自带 `resume()` 的 `FakeLoop` 替身 | `agent.py:412`、`agent_loop.py:63,160,179`、`test_agent.py:80` | **P1** |
| **W3** | **"预算耗尽"有两套互不兼容的信号**：`scope.check_budget()` 抛 `BudgetExhausted`（`scope.py:359`）→ 终态 `reason="budget"`；而 `llm_fallback.BudgetGuard.check` 抛 `PyHError("BUDGET-EXHAUSTED")`（:392）→ 落 `agent_loop.py:205` 的 PyHError 分支 → 终态 `reason="error"`。同一语义、两种码、两种终态 | `scope.py:359`、`llm_fallback.py:392`、`agent_loop.py:201,205` | **P1** |
| **W4** | **F026 轮内连败计数在主循环路径上完全不生效**：`reset_turn_failures`/`mark_turn_failure` 只在批量入口 `execute_tool_calls()`（`tools_executor.py:364`）被调用，而 `agent_loop.run_turn` 走的是**逐个** `run_step → execute`（`agent_loop.py:251`） | `agent_loop.py:251`、`tools_executor.py:364` | **P1** |
| **W5** | **关闭不可重入**：`session.append` 在**第 3 步先置** `self._closed = True`（:281），之后才在第 6 步校验、第 9 步落盘。校验失败（EVT-100/102）或落盘失败（PERS-202）后 `_closed` 永久为真；`agent.close` 回滚了 `self.state` 却**不回滚 `session._closed`**，重试直接 EVT-104 | `session.py:281,298,311`、`agent.py:307-314` | **P1** |

#### 0.2.1 W1~W5 修复状态（**2026-09-17 追加**，非原审计内容）

> 本节为该审计完成**之后**的修复进度，便于外部读者区分「当时的问题」与「现在的状态」。

| 问题 | 修复状态 | 依据 |
|---|---|---|
| **W1** 装配层绕 `from_config` → g1 `g-schema` 恒 allow（P0） | **已修** | S1-01：`engine.py` 改用 `GuardChain.from_config` 并注入 `validator=registry.validate_args`；`cfg.security.guards.disabled` 生效 |
| **W2** `agent.py` 调不存在的 `loop.resume()`（被 `FakeLoop` 替身掩盖） | **已修** | S1-02：删除不可达分支与 `paused` 死态；`AgentLoop` 收敛为 `idle`/`running` 两态（ADR-014） |
| **W3** 预算两套互不兼容信号（`BudgetExhausted`→`budget` vs `PyHError("BUDGET-EXHAUSTED")`→`error`） | **未做** | 两套信号在实测中仍并存；S1 范围裁定将其分流至独立阶段（登记为 S1 残留 R-9） |
| **W4** F026 轮内连败计数在主循环路径失效 | **已修** | S1-03：连败计数接入 `run_step` 路径，口径与批量入口一致 |
| **W5** `session.append` 关闭不可重入 | **已修** | S1-04：`_closed` 在 `finished` 校验失败时回滚（已入缓存后 flush 失败仍保持终态 = 刻意边界） |


### 0.3 迁移总览（渐进式，不停机重构）

```
Phase 0 架构整理      → 消灭 W1–W5 + 去重复原语           （净新增 0 模块，纯收口）
Phase 1 Governance    → 新增 pyharness/governance/ 包      （Policy/Decision/Principal）
Phase 2 Tool 治理接入  → 四关管道改为"向治理层要裁决"        （复用 guard 链，不推翻）
Phase 3 Decision Receipt → 裁决凭证落盘 + 可独立核验         （替换内存态 _grant_slots）
Phase 4 Evidence/Audit → 证据归档 + 运行时追溯矩阵 + 对账     （激活空置的 evidence/）
Phase 5 生产级 Demo   → 端到端治理场景 + 覆盖验收维度         （激活空置的 acceptance/）
```

**分阶段只增不改的总体纪律**：每个 Phase 结束时，`tests/invariants/` 全绿 + **INV-01/04/05/06 与新增治理不变量必须有专项测试**（当前 INV-02/03/06 无专项测试，见 §2.5）。

---

# 1. 当前系统架构

## 1.1 分层全景（代码实测，非文档声明）

与 `docs/MAP.md:16-61` 声明的四层基本一致，但**装配入口在代码里是单点的 `engine.py`**，而非 MAP 所称的 "bootstrap 启动 6 步"。

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 用户 / 外部程序   文本 · 斜杠命令 · JSON-RPC · HTTP/SSE · Qt Signal        │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ 只做协议适配，零业务逻辑
┌───────────────────────────────▼──────────────────────────────────────────┐
│ ④ 外壳层                                                                  │
│   CLI      pyharness/cli.py        1713 行  16 个子命令                     │
│   Web 桌面  desktop/app.py         1031 行  54 条 FastAPI 路由 + SSE        │
│            desktop/{sessions,projection,bridge,launcher,net}.py            │
│   Qt 桌面   desktop_native/{main_window,controller,app}.py  904/257/44     │
│   ACP 桥   pyharness/acp.py         676 行  JSON-RPC over stdio            │
│   —— 三壳共享同一业务门面 application/service.py(1043 行)                 │
│   —— 无自己的主循环；一切写操作过 session.append（无特权路径）              │
├──────────────────────────────────────────────────────────────────────────┤
│ ③ 编排能力层（经 ctx.* 挂载，可插拔）                                       │
│   core/task_queue.py  434  FIFO 单飞泵 + segment 段锚（F043/F044）          │
│   core/plan_mode.py   638  先方案后执行（F045/F046）+ 24h TTL              │
│   core/schedule.py    761  cron/interval 定时器 + 事件溯源恢复              │
│   core/jobs.py        513  后台 job（并发 ≤4）+ owner 授权 + 7 天清理       │
│   core/subagent.py / orchestration.py  子 Agent（递归深度 ≤3）             │
│   core/workflow.py     80  **顺序步骤**，无 DAG、无依赖调度                 │
│   core/{goal,todo}.py      事件派生的目标/待办                             │
├──────────────────────────────────────────────────────────────────────────┤
│ ② 核心脊柱（每次对话必经）                                                  │
│   engine.py           890  装配：store→SessionLog→AgentLoop→Scope→Registry │
│   core/agent.py       494  会话实体 + Ctx 门面（God Object）               │
│   core/agent_loop.py  411  三态机（实际 2 态）+ 三闸终态                    │
│   core/session.py     457  ★ append-only 唯一逻辑写口 + reducer 派生视图    │
│   core/scope.py       607  策略唯一数据源 + 预算硬闸（单调只紧不松）        │
│   core/tools_registry.py 635  Definition+Provider+Consumer 三件套索引       │
│   core/tools_executor.py 782  ★ 四关执行管道（无旁路）                      │
│   core/tools_guard.py 958  ★ g1–g7 单调拒绝链 + policy_ref                 │
│   core/approval.py    854  ★ 人类裁决（TTL/合并/信任/防重放/绑定指纹）      │
│   core/llm.py        1032  唯一 LLM 出口（请求/流式/计量/错误归一）          │
│   core/llm_fallback.py 413 降级链 + 退避 + BudgetGuard                     │
│   core/budget.py      125  预算只读门面（零状态零写）                       │
│   persistence.py      712  JSONL 物理原语（append/双速 flush/轮转/截断检测）│
│   repair.py           812  崩溃修复策略 + session.recovered 声明            │
├──────────────────────────────────────────────────────────────────────────┤
│ ① 事件与总线地基                                                            │
│   events/envelope.py  158  9 字段信封 + 五步校验链 + SeqState 分配器        │
│   events/vocab.py     283  词表注册表（★ 73 类型，实测）                    │
│   events/payload.py   605  71 个 Payload 模型（extra="forbid"）             │
│   bus/event_bus.py    427  emit/subscribe + 三模式分发 + 背压(同 sender FIFO)│
│   bus/plugin.py       358  五态生命周期 + 热插拔                            │
│   bus/registry.py      94  plugin/tool/capability 三类索引（脊柱名 RESERVED）│
└───────────────────────────────┬──────────────────────────────────────────┘
                                ▼
        ★ 会话事件日志 = JSONL append-only = 唯一真源（INV-01）
          消息历史 / UI 渲染 / FTS 索引 / 预算读数 全是它的派生视图
```

## 1.2 运行时主链：一次 submit 到终态

```text
外壳 submit(text)
  │
  ├─ core/agent.py:256  Agent.submit  → state: ready→busy
  │     └─ session.append("user.message", sync=True)          ← 强同步点①
  │
  ├─ core/task_queue.py:181  TaskQueue.submit(intent)
  │     ├─ append("task.enqueued")
  │     └─ 泵单例 _pump 启动
  │           └─ _run_task: append("task.started")
  │                └─ open_segment → append("segment.start", sync=True)  ← 段锚（F044）
  │
  ├─ engine.py:670  make_runner.run_for_task(task)
  │     ├─ engine.py:643  _owned_task_message：严格归属窗口找配套 user.message
  │     └─ agent_loop.wake(env, ctx)
  │
  ├─ core/agent_loop.py:345  wake
  │     ├─ idle → run(ctx)；running → 入队 FIFO；队满 → BUSY 强同步拒
  │     └─ 每轮起点：compactor.run_if_needed（F058 压缩）
  │
  ├─ core/agent_loop.py:182  _run_engine   ← 三态机主循环体
  │     └─ while True:
  │         ① _must_stop(ctx, run)   三闸只读判定：轮数(284) / 取消(286) / 预算(288)
  │         ② ctx.scope.check_budget()   超限抛 BudgetExhausted
  │         ③ run_turn(ctx, run)
  │              ├─ session.derive_messages(window)  ← 日志现派生（INV-01/02）
  │              ├─ sysprompt.assemble(hist, ctx)     ← 系统提示词 + 护栏段
  │              ├─ ctx.llm.chat/chat_stream(...)     ← ★ 全框架唯一 LLM 调用点
  │              │     └─ 落 llm.request / llm.response / llm.usage
  │              ├─ 纯文本 → append("agent.message") → return "complete"（自然终态）
  │              └─ tool_calls → for call in calls: run_step(call)   ← 逐个，非批量
  │                    └─ ctx.tools.execute(call, ctx)   ← 四关管道（§1.3）
  │                 → run.turn += 1 → 收敛指纹判定（stall_limit=3）
  │
  └─ _finish(ctx, run, reason) → RunResult（不写 session.finished）
       └─ agent.close → append("session.finished", sync=True)  ← 强同步点
```

**关键不变式（代码层面已强制）**：

- **INV-01** 唯一逻辑写口 = `SessionLog.append`（`session.py:257`）；类方法面无 update/delete（:79 注释 + 测试钉死）；"修正" = 追加 `user.message_edited`/`context.compacted`/`session.recovered`。
- **INV-02** 唯一 `llm.chat` 调用点 = `agent_loop.run_turn`（:236）；`import` 方向由 `MAP.md:190` 的拓扑序 + `tests/invariants/` 约束。
- **INV-04** 执行前必有 `guard.evaluated`：`GuardChain.evaluate` 每次求值恰落一条（:720/716），`ToolExecutor._require_wiring`（:520）在缺 session/scope/guard 时 **fail-closed 拒执行**。
- **INV-05** 拒绝零副作用：`guard.rejected` **强同步**落盘（`sync=True`，:816），落盘失败即 PERS-202 上抛——调用方 fail-closed，绝不带未落盘的拒绝继续。
- **INV-06** 说了什么↔做了什么：`tool.call` 同时存 `raw_args`（模型原话）与 `args`（校验后实际执行）（`tools_executor.py:440-444`）。

## 1.3 工具调用四关 + 治理接入点

这是全系统**唯一把"意图"变成"动作"的通道**，也是 Governed Runtime 的主战场。

```text
LLM 返回 tool_calls（raw JSON —— 不可信）
  │
  ▼ 关1a 契约 lookup            tools_executor.py:421-426
  │     registry.lookup(name)  失败 → TLB-802 → tool.error 回喂，零执行
  ▼ 关1b 参数先验后跑            tools_executor.py:427-437
  │     registry.validate_args 失败 → TLB-803（Provider 零调用，INV-06）
  │     → append("tool.call", {args, raw_args, call_id})        ← 审计锚点
  ▼ 关2a scope 前置             tools_executor.py:445-447
  │     scope.can_use(name)    False → _reject(GRD-401) → guard.evaluated + guard.rejected(sync)
  ▼ 关2b guard 单调链 ★核心      tools_executor.py:448-452 → tools_guard.py:672
  │     evaluate(call, scope):
  │        ① scope.can_use 兜底（GRD-401）
  │        ② 按 _BUILTIN_IDS 求值序逐 guard：
  │             g-schema        → TLB-803      （validator 注入；★ 当前恒 None，空转）
  │             g-fs-path       → POL-FS-1/2/3
  │             g-credential-read → POL-CRED-1
  │             g-exec         → POL-EXEC-1
  │             g-overwrite    → POL-OVW-1（已存在文件 → approval）
  │             g-net-outbound → POL-NET-1
  │             g-danger       → POL-DGR-1（critical→reject 不可审批；high→approval）殿后
  │        ③ 首个非 allow 即短路（waterfall）
  │        ④ approval 降级两闸：critical→reject；无通道→reject(APR-501)
  │        ⑤ 每求值恰一条 guard.evaluated（INV-04）；reject 再 guard.rejected(sync)（INV-05）
  ▼ 关2.5 审批                  tools_executor.py:537-573 → approval.py:234
  │     approval.request(call, summarize(args), ctx, binding=sha1(tool+args))
  │       → approval.requested(sync) → 等 human approve/deny / TTL 超时(=denied)
  │       → granted ⇒ **重入 guard 链**（不是放行！单调性高于人类意志，GRD-403）
  │       → 绑定指纹一致才执行（防"批准 A 执行 B"）
  ▼ 关3 Provider 执行           tools_executor.py:458-496
  │     同步 handler → 每次调用独立单工线程池（超时/取消后驱逐不复用）
  │     总超时 = defn.timeout_s or 60s → TLB-805
  │     CancelledError 不吞：已发生副作用写 partial 后 re-raise（F025）
  ▼ 关4 finalize              tools_executor.py:734-774
        输出 schema 校验 → 超长(>2KB)转 spill（F039）→ ≤2KB 摘要
        → append("tool.result", {ok, summary, truncated, spill_ref})
```

**治理接入点（Phase 2 唯一需要动手术的位置）**：`guard.evaluate` 的返回值目前是 `Decision`（allow/reject/approval）——一个**三值枚举**。Governed Runtime 需要它升级为一个**带因果的决策对象**（谁定的、依据哪条策略、证据是什么、可否核验）。现有 `policy_ref` 已经承载了"依据"，缺的是"结构化 + 落盘 + 可核验"。

## 1.4 模块清单（路径 / 职责 / 输入 / 输出 / 依赖）

### 核心脊柱

| 模块 | 行数 | 核心职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| `engine.py` | 890 | 从 Settings 装配可运行引擎 | `cfg`/`sid`/`sessions_dir` | `EngineSpine`/`EngineContext` | 全脊柱 + 20+ 处惰性 import |
| `core/agent.py` | 494 | 会话实体 + Ctx 门面 + 生命周期 | `submit(text)`/总线事件 | `user.message`/`session.finished`(sync)、`loop.wake` | errors（其余鸭子注入） |
| `core/agent_loop.py` | 411 | 三态机主循环；唯一 `llm.chat` 入口 | `ctx`/`Envelope` | `agent.message`/`system.error`/`system.cancelled`；`RunResult` | config/errors/events、`scope.BudgetExhausted`（模块底反向 import:82） |
| `core/session.py` | 457 | append-only 唯一写口 + reducer 派生视图 + 重放恢复 | `type/payload/actor/sync/trace` | `Envelope`、消息列表、事件迭代器 | errors/events（persistence 经 Protocol 注入，无反向 import） |
| `core/scope.py` | 607 | 策略唯一数据源 + 预算硬闸 + 窗口 | `cfg`/`session_id` | `BudgetState`、`BudgetExhausted`、`scope.updated`/`budget.paused` | errors、**`core.llm_fallback`（顶层 import，分层倒置）** |
| `core/tools_registry.py` | 635 | Definition+Provider 索引、参数校验、schema 生成 | Definition/Provider | `Definition`、schemas、校验后 args | errors/pydantic |
| `core/tools_executor.py` | 782 | 四关执行管道总闸（无旁路） | `ToolCall`/`ctx` | `tool.call`/`tool.result`/`tool.error`/`guard.*`、`ExecResult` | `tools_guard`、`tools_registry`、errors、`events.vocab` |
| `core/tools_guard.py` | 958 | g1–g7 单调拒绝链 + danger 分级 | `ToolCall`/`scope` | `Decision`、`guard.evaluated`/`guard.rejected`(sync) | errors（session/bus 注入，不 import executor/approval/provider） |
| `core/approval.py` | 854 | 人类裁决服务（TTL/合并/信任/防重放/绑定） | `request(call, args_summary, ctx, binding)` | `approval.*`(全 sync)、`queue.suspended/resumed` | errors、`events.vocab.is_registered` |
| `core/llm.py` | 1032 | 唯一 LLM 出口：请求/流式/计量/错误归一 | `messages`/`tools`/`ctx` | `LLMResponse`、`llm.request/response/usage`(chunk 上总线) | errors、httpx（可选） |
| `core/llm_fallback.py` | 413 | 降级链 + 退避 + 健康探针 + BudgetGuard | adapters/config/bus | `llm.retry`/`budget.paused`/`llm.recovered`、`PyHError("BUDGET-EXHAUSTED")` | config/errors（不 import llm.py） |
| `core/budget.py` | 125 | 预算只读门面（零状态零写） | limits/counters/scope | `BudgetSnapshot` | `scope.BudgetLimits` |
| `persistence.py` | 712 | JSONL 物理原语：append/双速 flush/原子写/轮转/截断检测 | `Envelope` | 单行 JSONL、`replay()` 迭代器 | errors、events |
| `repair.py` | 812 | 崩溃修复策略（备份→截尾→隔离→对账→声明） | 会话文件 | `RepairReport`、`session.recovered`(sync) | errors、events、**直接改 `SessionLog._bus`（:682）** |

### 编排 / 扩展 / 外壳

| 模块 | 行数 | 核心职责 | 备注 |
|---|---|---|---|
| `core/task_queue.py` | 434 | FIFO 单飞泵 + segment 段锚 | **段锚=回放最小单位，天然证据单元** |
| `core/plan_mode.py` | 638 | 先方案后执行 + 24h TTL | 顺序步骤，无并行 |
| `core/schedule.py` | 761 | cron/interval/at + `recover()` 事件溯源恢复 | 领域级恢复，非运行时 checkpoint |
| `core/jobs.py` | 513 | 后台 job（≤4 并发）+ owner 授权 | job 相互独立，无依赖图 |
| `core/workflow.py` | 80 | **顺序步骤编排（无 DAG）** | 与 orchestration.py 无调用关系 |
| `core/orchestration.py` | 180 | job/subagent → AgentLoop 的**适配层** | 不是调度器 |
| `core/session_query.py` | 991 | SQLite FTS5 派生索引（可整体重建） | 订阅事件增量索引 |
| `core/compaction.py` | 676 | 上下文压缩（只追加声明） | 折叠区间=合法 seq 空洞 |
| `core/spill.py` | 629 | 工具超长输出隔离区（会话私有） | 关4 消费 |
| `core/telemetry.py` | 100 | 事件流 → 审计报告（**纯只读聚合**） | 计数值，非不可篡改审计链 |
| `bus/event_bus.py` | 427 | 订阅/三模式分发/背压 | **不校验 payload（校验盲区）** |
| `application/service.py` | 1043 | 跨 shell 业务门面（God facade） | 承担 session/job/schedule/skill/plugin/preset/审批/预算全部编排 |
| `desktop/app.py` | 1031 | 54 条 FastAPI 路由 + SSE fan-out | 租户隔离半真实（见 §2.5） |
| `pyharness/ui/index.html` | 1233 | 内嵌前端（**非空目录**） | 仅被字符串存在性测试覆盖 |

## 1.5 事件真源：实测口径

**运行时校验结果**（`python -c` 直接读 `pyharness.events.vocab`）：

| 项 | 实测值 | 文档声称 | 一致性 |
|---|---|---|---|
| 事件类型总数 | **73** | `EVENT-SCHEMA.md:101`=57；`README.md:4`=73；`CODE-MATRIX.md:38`=64 | **四处口径打架** |
| Payload 模型数 | **71** | — | 与 73 差额 = 共享模型（approval 三结果复用一个） |
| 强同步类型 `SYNC_TYPES` | **11** | `EVENT-SCHEMA.md:608` 列 8 项 | **文档少列 3 项**（`approval.timeout`/`fork.created`/`context.compacted` 中实际含更多） |
| 瞬态类型（禁入日志） | **3**（`llm.chunk`/`config.updated`/`registry.updated`） | — | — |
| 信封字段数 | **9**（seq/ts/type/session_id/actor/origin/task_id/payload/trace） | `envelope.py:3,33` 反复写"十字段强校验" | **文档与代码差 1** |
| 治理相关事件类型 | **9**（`approval.{requested,granted,denied,timeout}`、`budget.paused`、`guard.evaluated`、`guard.rejected`、`scope.updated`、`syscheck.fail`） | — | 治理骨架已存在 |

**payload 校验强度**：写侧 `validate_payload` → `model(**payload).model_dump()`，全部 `extra="forbid"`；读侧 `persistence.replay`(:450) 与 `repair._parse_or_none`(:160) **同样校验**（好事）。**但总线 `emit` 不校验**（`event_bus.py:10-12` 明示"只中转不落盘"）——即"只走总线不落盘"的事件是校验盲区。71 个模型中**只有 2 个**有跨字段校验器（`LlmResponsePayload:104`、`ContextCompactedPayload:467`），`approval.*`/`goal.*` 的状态机约束缺位。

## 1.6 与文档声明架构的 6 处实质偏差

| # | 文档 | 代码 |
|---|---|---|
| D1 | `SOP.md` 与多篇 specs 引用 `tests/acceptance/test_f{nnn}_*.py` | `tests/acceptance/` **只有 `__init__.py`，0 测试文件**；`tests/e2e/`、`tests/fixtures/` 同样为空 |
| D2 | `SECURITY.md:190-223` 描述 INV-01~09 各自检测 | `tests/invariants/` 仅 3 文件 7 用例，文件头注释仍写"当前 RED：模块未实现"（早期骨架）；**INV-02/03/06 无专项测试** |
| D3 | `specs/approval.py.md:158` 引用 `tests/security/test_sec_*.py` | 仓库**无 `tests/security/` 目录** |
| D4 | `docs/specs/README.md:11` 规格 32 份 | `pyharness/` 62 个模块；specs 未覆盖 `engine/application/desktop_native/session_query/mcp/proc/pty/kv/workflow/telemetry/tenant_settings/skill*/orchestration/budget/auto_title` 等 |
| D5 | `EVENT-SCHEMA.md:101` 词表 57 | 实测 **73**；`budget.paused`/`scope.updated`/`skill.*`/`plan.*`/`schedule.*`/`user.question` 等 16 型已入库但文档未记 |
| D6 | `cli.py:6` docstring "14 个叶子子命令" | argparse 实际注册 **16**（漏 `workflow`/`skill`）；`metavar` 却写 16 |
| D7 | `CODE-MATRIX.md:11` "1298 passed" vs `README.md:4` "1400 passed" | 日志文件只有进度点，**无 "N passed" 汇总行可复核** |

> 偏差本身不致命，但**治理层要求"文档即契约"**。D1–D3 尤其危险：治理与安全测试目录是空的，意味着当前安全断言**没有专门的验收层**。

---

# 2. 当前代码健康度分析

## 2.1 架构优点（这些是资产，迁移中必须原样保留）

| # | 优点 | 证据 |
|---|---|---|
| A1 | **事件溯源执行得彻底**：真源唯一、派生视图可弃重建、无第二份状态 | `session.py:79`（方法面无 update/delete）；`open_session`:417 重放重建；`rebuild_from_log`:369 |
| A2 | **工具管道无旁路**：四关顺序不可换，`_require_wiring` fail-closed | `tools_executor.py:520-535` 缺 session/scope/guard 即 CYC-999 拒绝执行 |
| A3 | **guard 单调性是结构性的，不是约定** | `GuardChain._FORBIDDEN_API`（:629）用 `__getattr__` 让 `override/bypass/force_allow/execute` 抛 AttributeError；无移除/重排 API；插件 guard 只追加链尾（:742） |
| A4 | **审批语义严谨**：TTL→denied 安全默认、一次性消费、防重放、`by` 由框架打不可自报、granted≠放行 | `approval.py:519`（超时=timeout→不执行）、`:376`（APR-503 防重放）、`:741`（`_require_human` 白名单）、`tools_executor.py:563`（重入链） |
| A5 | **授权↔执行绑定**：批准的是"这一组参数"，不是"这个工具" | `tools_executor._approval_binding`:218 + `approval.grant_binding`:608 |
| A6 | **LLM 零信任落地**：参数先验后跑、输出按契约校验、raw_args 与 args 双份存档、三档超时 | `tools_executor.py:427-444`、`llm.py:86`（TimeoutLimits） |
| A7 | **崩溃修复不丢已落盘数据**：备份优先于一切动作，中部坏行隔离而非删除，隔离失败则原地保留 | `repair.py:459-461`（备份失败即中止）、`:545-549`（隔离写失败 → 原行保留） |
| A8 | **错误码纪律**：全系统唯一抛出入口 `raise_code`，未登记码会被改写为 CYC-999（迫使登记） | `errors.py`；`tools_guard.py:51-53` 记载了"BUSY 未登记码需直构"的先例 |
| A9 | **测试规模扎实**：1326 个 `def test_`，收集 1377 例；核心模块测试很厚 | `test_tools_guard.py`(1234 行/76)、`test_desktop.py`(1226/59)、`test_tools_registry.py`(556/51) |
| A10 | **ADR 已工程化**：12 条 ADR，固定七节，含"违反它的后果"，只增不改 | `docs/ADD.md:4,34,35`；GATE-01 把 ADR 作为契约核对依据 |

## 2.2 架构问题

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P-1 | **`Ctx` 是 Service Locator（God Object）** | `agent.py:76` 8 字段 + `create_agent` 追加 ~25 属性（:467-488）；任何能力缺失都以 `getattr(..., None)` 静默跳过 | 依赖关系不可静态分析；新增治理能力只能继续往 ctx 上挂 |
| P-2 | **`build_runner_components` 是 185 行上帝装配函数** | `engine.py:425-610`：内联装配 18 类组件、6 段惰性 import、策略分支、预设编译、落盘订阅 | 任何治理接线都要改这个函数 → 高风险单点 |
| P-3 | **`application/service.py` 是 1043 行 God facade** | 承担 session/job/schedule/subagent/skill/plugin/preset/审批/预算全部编排 | 外壳层改动传导面过大 |
| P-4 | **分层倒置：策略层依赖 LLM 编排层** | `scope.py:81` 顶层 `from pyharness.core.llm_fallback import BudgetState, TaskUsage`，仅为复用两个类型别名 | 与 docstring "scope 无下游脊柱依赖"（:14）矛盾；治理层的策略模块不应依赖 LLM 层 |
| P-5 | **编排能力名不副实** | `workflow.py:45` 就是一个 `for` 循环；全库**无 DAG、无依赖调度、无并行编排** | Governed Runtime 若承诺"工作流治理"，当前无可治理的对象 |
| P-6 | **策略引擎是"内嵌规则"而非"引擎"** | 无 `PolicyEngine` 类；规则硬编码在 guard 函数（`tools_guard.py:333-547`）+ `ScopePolicy`（`scope.py:158`）+ `preset` 四档（`engine.py:270`）；无策略加载器、无策略版本号、无策略清单 | "Governance Layer" 的核心缺口 |
| P-7 | **物理写口不唯一** | `SessionLog.append` 是唯一*逻辑*写口，但 bus→store 落盘适配器被**复制 5 份**（`engine.py:627`、`cli.py:602`、`desktop/sessions.py:151`、`orchestration.py:145`、`repair.py:700`），各自维护 `_SYNC` 元组；`repair` 还**绕过 SessionLog 直接 `store.append`** | INV-01 是逻辑约定而非结构强制；`_SYNC` 清单漂移风险 |
| P-8 | **异常吞掉是普遍模式** | `EngineSpine.close`:106-152 连续 8 处 `except Exception: log.warning`；`scope._record`:466、`guard._record`:843、`approval._soft_append`:797、`agent.detach_capability`:392 | 与 CONSTRAINTS-05 E-04（禁裸吞）精神相悖；治理层需要"审计写失败必须可见" |

## 2.3 重复代码清单

| 重复内容 | 位置 A | 位置 B | 风险 |
|---|---|---|---|
| 路径几何判定（workspace containment） | `tools_guard._inside_workspace`:234 | `tool_fs._under`:181 | 两处 POL-FS 语义必须人工对齐 |
| 路径三闸 POL-FS-1/2/3 | `tools_guard._resolve_geometry`:305 | `tool_fs.resolve_in_workspace`:205 | 同上 |
| 凭据读拦截 | `tools_guard.g_credential_read_check`:407 | `tool_fs._reject_credential`:241 | `_CRED_BASENAMES` 双份（guard:123 / tool_fs:96） |
| spill 写封装（PERS-221 兜底） | `tools_executor._spill_put`:721 | `tool_fs._spill_put`:268 ／ `tool_web._spill_put`:503 | **三份** |
| 二进制探测 | `tool_fs._looks_binary`:294 | `tool_web._looks_binary`:498 | 逐字重复 |
| 脱敏 | `tool_fs._redact`:148 | `tool_web._redact`:163 | — |
| token 估算启发式 | `session._estimate_tokens`:55 | `llm._est_tokens`:418 | 注释互认"同启发式" |
| danger 读取 | `tools_guard._danger_of`:217 | `approval._danger_of`:115 | — |
| bus→store 落盘订阅 | `engine._record_to`:627 | cli / desktop.sessions / orchestration / repair（共 5 份） | `_SYNC` 清单漂移 |
| 可重试码集 | `llm_fallback._RETRYABLE_CODES`:60 | `llm._RETRYABLE_CODES`:70 | 两处定义 |
| 预算状态判据 | `scope.budget_state`:322 | `budget._compute_state`:92 | 双口径漂移隐患 |
| 求值序清单 | `tools_guard._BUILTIN_IDS`:555 | `build_builtin_chain`:581 的列表 | 双份真源，无测试锚定一致 |
| exec 三份平行表 | `tool_exec._DEFS`:148 | `_PROVIDERS`:186、`_DANGER`:196 | 靠 name 字符串索引，新增工具须同步三处 |
| 两套 repair | `persistence.SessionStore.repair`:470（隔离仅内存行号，坏行仍在主文件） | `repair.repair_session`:380（物理抽离到 quarantine 文件） | 语义分歧 + 备份命名不同（`.bak-` vs `.corrupt-`） |

## 2.4 高耦合位置

```
engine.py:425-610  build_runner_components
   ├─ 依赖 18 类组件构造 ──→ 任何模块构造函数签名变更都波及此处
   ├─ 就地改写传入的 cfg（register_default_llm:221/246-247 改 llm_cfg.model 为注册表键）
   └─ 就地改 cfg.security.sandbox.level（:309）  ← 副作用污染调用方 Settings

core/agent.py:467-488  create_agent 追加 ~25 个 ctx 属性
   └─ 治理能力若继续往 ctx 挂 → ctx 无限膨胀

core/scope.py:81  顶层 import llm_fallback（分层倒置）
   └─ 策略层 ↔ LLM 编排层 双向概念耦合

repair.py:682,699-710  _DirectBus + 直接改 SessionLog._bus
   └─ 私有属性耦合，绕开总线装配

tools_executor.py:109-111  tools_guard ↔ tools_registry 同包互引
   └─ 注：tools_guard 明确不 import executor/approval/provider（:74），方向仍单向
```

## 2.5 潜在 Bug 风险（按严重度）

### P0 — 改变了安全语义，或防线实际失效

| # | 风险 | 证据 | 失效场景 |
|---|---|---|---|
| **B0-1** | **g1 g-schema 在生产恒 allow**（="内层复查防旁路"INV-04 保障不存在） | `engine.py:474` 未注入 validator → `tools_guard.py:347-348` 直接 `return ("allow", None)` | 任何绕过 executor 关 1b 的"内层直调"不会被 g1 抓出；g1 在 INV-04 意义上等同不存在 |
| **B0-2** | **`cfg.security.guards.disabled` 永不生效**：`from_config`(:906) 零生产调用点 | `engine.py:474` 直构 `GuardChain`；`from_config` 唯一调用点是 `scripts/demo_phase1.py:62` 的裸 `GuardChain()` | 运维以为关闭了某 guard，实际仍在链上；反向：以为开启了审计留痕（`guard.disabled` 事件），实际永不落盘 |
| **B0-3** | **`approval_channel=None` 被"视同有通道"** | `tools_guard.py:49` docstring 明示；`engine.py:475-476` 构造 `ApprovalProvider(channel="desktop")` 但 guard 链不知道 | guard 层对 danger=high 出 `approval` 决策而非 reject，通道可用性判定与实际通道脱节（headless 场景需靠 approval 层兜 APR-501） |
| **B0-4** | **`delete_file` 真实删除可达** | `tool_fs.py:457` `p.unlink()`；危险面靠 g2 critical 拒绝兜底 | 若策略被"显式下调"或测试直调，真实删除即执行（代码注释自认：`tool_fs.py:445-447`） |

### P1 — 语义断裂 / 死路径 / 数据正确性

| # | 风险 | 证据 |
|---|---|---|
| **B1-1** | `loop.resume()` 调用不存在的方法；`paused` 状态永不成立 | `agent.py:412` vs `agent_loop.py` 方法全集；且 `agent.py:40-43` docstring 自认前缀订阅"实际不命中任何会话事件"→ 该分支**既死、若触发即 AttributeError** |
| **B1-2** | 预算两种终态（见 §0.2 W3） | `scope.py:359` vs `llm_fallback.py:392` |
| **B1-3** | F026 连败计数在主链失效（见 §0.2 W4） | `agent_loop.py:251` 逐个 run_step vs `tools_executor.py:364` 批量入口 |
| **B1-4** | 关闭不可重入（见 §0.2 W5） | `session.py:281` 先置位，`:298/:311` 后才可能失败 |
| **B1-5** | **`_outcome_fingerprint` 直接下标取键**：`e.payload["name"]/["summary"]/["ok"]` | `agent_loop.py:302`；任何 `tool.result` 缺字段即 KeyError（该函数**每工具轮都跑**） |
| **B1-6** | **`probe_loop` 健康探针在真链路上不跑** | `llm_fallback.py:326` 定义完整，engine/CLI 装配段均未 `create_task` |
| **B1-7** | **`UsageCounters.task_total` 语义谬误**：docstring 称"任务级"，实际用会话级累计 | `llm.py:192-194`；"每任务预算"实为"每会话预算" |
| **B1-8** | **工具名 sanitize 有损双射可碰撞**：点号→下划线 | `llm.py:230,243`；若注册表同时有 `fs_read_file` 与 `fs.read_file`，响应名还原错工具 |
| **B1-9** | 审批展示**双重截断 + 整键丢失 + 嵌套参数内容全隐** | `tools_executor.summarize:198-215`（超 400 字符 `break`，后续键**完全消失**）→ CLI 再 `_trunc(...,160)`（`cli.py:1452`）；`_short_value:183-186` 把 dict/list 压成 `<dict N键>`；`_SUMMARY_PRIORITY:125-128` **不含 `code`/`input`/`args`/`script`**，`exec.python_run` 的 `code`、`exec.pty` 的 `input` 会被 400 预算挤出 |
| **B1-10** | 租户隔离**半真实**：`s-fork-*` 会话退化为客户端标签 | `desktop/app.py:35-36` 正则 `s-[0-9a-zA-Z]+` 遇 `-`/`.` 截断，而合法 sid 允许（`desktop/sessions.py:20`）；`:129-130` 未注册会话回落客户端头；无 sid 的读端点纯靠头 |
| **B1-11** | `task_queue._done` / `approval._grant_slots` / `tools_executor._rejected` **无界增长** | `task_queue.py:130`（只写不清）、`approval.py:217`（注释以"call_id 唯一"自辩，回避累积）、`tools_executor.py:251` |
| **B1-12** | 暂停有 1 个任务的竞态窗口 | `task_queue.py:215-222`（检查 `_suspended()` 后跨 await）vs `pause:315-318` |

### P2 — 契约不清 / 可维护性

| # | 风险 | 证据 |
|---|---|---|
| B2-1 | 词表规模四处口径打架（57/64/73/实测 73） | §1.5 |
| B2-2 | 强同步清单文档少列 | `EVENT-SCHEMA.md:608` vs `SYNC_TYPES` 实测 11 |
| B2-3 | 信封字段数 docstring 说 10、实际 9 | `envelope.py:3,33` vs `:41-49` |
| B2-4 | 词表外事件被代码显式承认（`guard.disabled`/`budget.paused`/`approval.trust_*`） | `tools_guard.py:38`、`scope.py:41`、`approval.py:56` — 均"未入 57 词表，append 会 EVT-102"，靠 `_record` 尽力而为 |
| B2-5 | 死代码 | `tools_executor._invoke`:679（无调用点）、`session_query._MIN_CJK/_MIN_LATIN`:100-101（仅 `__all__` 导出）、`llm_fallback.pick`:358 仅自用 |
| B2-6 | 正常 append **无 fsync** | `persistence.py:360,391,411` 仅 `write`+`flush`；真原子路径只在 `_rewrite_without_tail`:534 与 `_rotate`:590 |
| B2-7 | `offset=0` 仍可把文件清成 0 字节 | `persistence._rewrite_without_tail(0)`:546、`repair.truncate_tail(0)`:480；触发条件仅"整文件无换行"（有界，已文档化） |
| B2-8 | 治理/安全测试目录为空 | `tests/acceptance/`、`tests/e2e/`、`tests/fixtures/`、`tests/security/`（不存在） |
| B2-9 | INV-02/03/06 无专项不变量测试 | `tests/invariants/` 仅 7 用例，覆盖 INV-01 边缘 + F005/EVT-102 |
| B2-10 | `session_query.flush` 在事件循环线程内同步跑 SQLite 事务 | `session_query.py:685,704-717` → 阻塞事件循环 |
| B2-11 | `web_search` 绕过 fetch 侧逐跳 allowlist 复核 | `tool_web.py:290-330`；仅靠装配期 `search_host` 单点（`tools_guard.py:476-479`） |

---

# 3. 与 Governed Agent Runtime v1.0 的差距

> **总纲**：PyHarness 已具备"**执行层治理**"（enforcement），缺的是"**治理层**"（governance）——一个把散落的策略、决策、凭证、证据收编为**一等公民、可查询、可核验、可版本化**的显式架构层。
> 术语实测：`governance`=0、`receipt`=0、`evidence`=0、`checkpoint`=0、`traceability`=0、`audit`=25（仅 telemetry 聚合）、`policy`=142（内嵌规则）。

## 3.1 Governance Layer 差距

### 3.1.1 Policy Engine — **半成品（规则内嵌，无引擎）**

| 应有能力 | 现状 | 差距 |
|---|---|---|
| 策略作为一等对象 | 无 `PolicyEngine`/`Policy` 类；规则硬编码在 7 个 guard 函数（`tools_guard.py:333-547`）+ `ScopePolicy`（`scope.py:158`）+ preset 四档（`engine.py:270`） | 需抽象 `Policy` 对象 + 注册表 |
| 策略可声明/可加载 | 仅 `config.yaml` 的 `security.*` 键 + `DANGER_TABLE`（`plan_mode.py:90`） | 无策略文件格式、无策略加载器 |
| 策略版本治理 | **无**。`GuardChain.chain_version()`（:772）仅是内存内递增计数，不落盘、不随策略内容变化（只随挂载/禁用次数） | 需策略指纹（内容哈希）+ 版本号落盘 |
| 策略与执行解耦 | 部分耦合：`scope` 依赖 `llm_fallback`（分层倒置）；guard 内自含路径几何（与 `tool_fs` 重复） | 需把策略判定收敛到单点，`tool_fs` 消费而非重复实现 |
| 策略只紧不松（单调） | **已有且珍贵**：`Scope.tighten`（:293）只追加 deny、无 relax API；guard 链只增链尾 | 保留 ✅ |
| 策略可外部化/可插拔 | **弱**：`register_guard_hook`（:879）提供插件 guard 挂载，但 guard 是代码模块而非策略声明 | 需策略声明式接入（如 YAML → guard 规则） |

### 3.1.2 Decision Engine — **不存在（只有三值枚举）**

现有 `Decision`（`tools_guard.py:127`）是 `StrEnum(allow/reject/approval)`——**一个值，不是一次决策**。缺失的是"决策"作为**有因果、有主体、有依据、可核验的实体**：

| 应有要素 | 现状 | 差距 |
|---|---|---|
| 决策主体（谁定的） | guard 出决策，但 `guard.evaluated` payload 只有 `{tool, decision, guard_ids, reasons}`（:794-801）——**没有"决策由谁/以何身份作出"** | 需注入 Principal |
| 决策依据（凭哪条策略） | **已有雏形**：`policy_ref`（POL-FS-1 等）+ `guard_ids` | 需升级为"策略 ID + 策略版本 + 命中条款"结构化 |
| 决策输入快照 | `tool.call` 存了 `args`/`raw_args`（:440） | 需与决策 ID 关联（当前靠 `trace.call_id` 弱关联） |
| 决策唯一 ID | 无。`guard.rejected` 靠 `trace.call_id` 配对 | 需 `decision_id` 主键 |
| 决策可核验 | 无 | 见 3.1.3 |
| 决策生命周期（issued→approved→executed→revoked） | 隐含在事件序列里，无显式状态机 | 需显式状态 |

### 3.1.3 Decision Receipt — **完全不存在（0 命中）**

这是 v1.0 的**标志性新增**。当前最接近的是：

- `approval._grant_slots`（:217）：内存内 `call_id → 绑定指纹`，**不落盘、不可独立核验、无界增长**；
- `tools_executor._approval_binding`（:218）：sha1(tool+规范化参数)——**算法已就绪**，缺的只是"把它变成一个持久化、可离线核验的凭证"。

| 应有要素 | 现状 | 差距 |
|---|---|---|
| 凭证工件（可独立携带） | 无 | 需 `DecisionReceipt` 模型 + 序列化 |
| 完整性（防篡改） | append-only 日志提供"顺序不可悔" | 需内容哈希 + 前序哈希链（或签名） |
| 可独立核验（离线） | 无 | 需 `verify(receipt) -> bool` |
| 与执行绑定 | **已有算法**（binding 指纹） | 需落盘 + 生命周期 |
| 人类裁决凭证 | `approval.*` 事件带 `by`/`ttl_ms`，`approval_id`=请求 seq | 需合并为单凭证 |

### 3.1.4 Permission Control — **已有且较强**

| 能力 | 现状 | 证据 |
|---|---|---|
| 工具级权限 | ✅ `Scope.can_use`（`scope.py:274`）+ 域前缀可见性 + critical 终局 | 单调只紧不松 |
| 危险分级 | ✅ L0–L4（`tools_guard.py:101`）+ critical 不可审批 | — |
| job 侧权限 | ✅ owner 授权 `_authorize`（`jobs.py:193`） | — |
| skill 安装权限 | ✅ 需 `approved_by`（`skill_registry.py:251-253`） | — |
| **资源级权限**（配额/并发/时间窗） | ⚠️ 仅预算（token/成本）；无并发上限、无速率窗、无时间窗 | job 并发 ≤4 为硬编码（`jobs.py:180`） |
| **数据级权限**（租户/会话数据边界） | ⚠️ 半真实（见 B1-10） | — |

### 3.1.5 Human Approval — **已有且成熟**（本项差距最小）

保留 `approval.py` 全部语义。仅需治理层化：
- 把"审批"从 `core/` 的模块提升为治理层的 **HITL 通道**之一；
- `by` 的通道身份（`cli:<user>`/`web:<会话>`/`acp:<client>`/`desktop`，:741）→ 升级为 **Principal**；
- 审批展示需修 B1-9（整键丢失/嵌套隐藏）——**这是安全缺陷，不只是体验问题**。

### 3.1.6 Audit Trail — **已有且强（但只有"日志"，没有"审计模型"）**

| 能力 | 现状 | 差距 |
|---|---|---|
| 不可篡改事实源 | ✅ append-only + seq/ts 框架分配 + 11 类强同步 | — |
| 事件关联键 | ✅ `seq`/`trace.parent_seq`/`call_id`/`approval_id`/`task_id`/`session_id` | — |
| 审计查询模型 | ⚠️ `telemetry.session_audit`（:30）只是**类型计数聚合**（"guard.rejected ×3"），无"谁在何时因何被拦、结果如何"的因果链 | 需治理审计视图 |
| 对账（自检） | ⚠️ `syscheck.fail` 已入词表（73 型之一），事件语义为 SEQ-GAP/CACHE-STALE/NO-GUARD-EVENT（`EVENT-SCHEMA.md:478`），但**生产无调度点** | 需接线 + 新 checker |
| 审计不可抵赖 | ✅ `by` 框架打、LLM/工具/插件无权自报（S-2 防线） | — |

### 3.1.7 Evidence System — **不存在（运行时）**

| 层面 | 现状 | 证据 |
|---|---|---|
| 协议层纪律 | ✅ 完整：`Claim → Implementation → Test → Result → Evidence → Limitation` 六级，每条 CND 有 Required Evidence | `.ai-coding/PROTOCOL.md:291-302,143-227` |
| 归档目录 | ⚠️ **已建但空置** | `.ai-coding/evidence/` 仅 `.gitkeep` |
| 运行时证据（系统产生、可复核） | ❌ 无。`goal.verify_completion`（`goal.py:346`）的"证据"只是 `progress≥1` + 勾选 + 无 failed | 非可追溯证据 |
| 证据与决策绑定 | ❌ 无 | — |

### 3.1.8 治理层差距小结

| 治理要素 | 成熟度（0–5） | 结论 |
|---|---|---|
| Permission Control | **4** | 保留 + 补资源级/数据级 |
| Human Approval | **4.5** | 保留 + 提升为治理层 HITL |
| Audit Trail（事实源） | **4.5** | 保留；补审计**模型** |
| Policy Engine | **1.5** | 需新建（复用 guard 语义，勿重写） |
| Decision Engine | **1** | 需新建（现有 Decision 枚举升格） |
| Decision Receipt | **0** | 全新 |
| Evidence System | **1**（协议层 4，运行时 0） | 需新建运行时证据链 |

## 3.2 Runtime 能力差距

| 能力 | 现状 | 差距 | 证据 |
|---|---|---|---|
| **State Management** | ⚠️ 状态散布：`Agent.state`（init/ready/busy/stopping/closed）+ `LoopState`（声明 5 实为 2）+ `TaskQueue._running` + `Scope` 状态机 + `plan/schedule/job` 各自状态 | 无**统一运行时状态模型**；无状态迁移的显式契约 | `agent.py:228,268,274,303,333`；`agent_loop.py:63,160,179` |
| **Failure Recovery** | ✅ 较全：repair 全管线、job 取消归一化、schedule 重臂、compaction 降级、LLM 降级链 | 缺"**恢复后的一致性判定**"（怎么知道恢复到了哪个点、还差什么） | `repair.py:380`；`schedule.py:606` |
| **Checkpoint** | ❌ **无**。仅 `ScopeSnapshot`（`scope.py:184`，策略+预算值拷贝）与 `SeqState`（seq 水位） | 无会话级/run 级检查点；崩溃恢复成本 = **O(全事件重放)** | `open_session:417` 全量 `replay()` |
| **Replay** | ✅ 有：`replay()` / `events_after` / `events_between` / `derive_messages` 纯函数 / `rebuild_from_events` | 缺**增量回放**与**回放到指定检查点**的能力 | `persistence.py:430`；各管理器 `rebuild_from_events` |
| **可恢复的中断**（pause/resume） | ❌ **断裂**：`paused` 状态不存在、`resume()` 方法不存在、审批等待靠 `queue.suspended` 硬扛 | 无法"暂停一个 run 并在进程重启后继续" | B1-1 |
| 幂等/去重 | ⚠️ 局部：`apr_id` 一次性消费、`GRD-402` 防重放、`_rejected` 集 | 无全局幂等键；`_rejected` 不落盘（重启即忘） | `approval.py:376`；`tools_executor.py:251` |

## 3.3 Engineering 能力差距

| 能力 | 文档层 | 运行时/代码层 | 差距 |
|---|---|---|---|
| **ADR** | ✅ 12 条，固定七节，只增不改（`ADD.md:4,34,35`）；GATE-01 以 ADR 为契约核对依据（`PROTOCOL.md:241-244`） | ❌ 代码/事件中无 ADR 引用 | 需把 ADR 编号与模块/决策关联（如 `DecisionReceipt.adr_refs`） |
| **Claims（断言纪律）** | ✅ CORE-01"无执行证据不得声称完成"（`PROTOCOL.md:104-110`）；报告 13 字段固定（§11） | ⚠️ 报告是人工产物，无机器可校验载体 | 需 claim 的结构化载体（可被 GATE-04 机器检查） |
| **Evidence** | ✅ 六级链条 + 每条 CND 的 Required Evidence | ❌ 目录空置；运行时零证据 | 见 3.1.7 |
| **Traceability** | ✅ `trace.parent_seq` 配对协议（`EVENT-SCHEMA.md:82-96`）；模块 docstring 标注 spec + F 编号 | ❌ 无"F 编号 ↔ 代码 ↔ 测试 ↔ 事件"的**可查询**矩阵；`IMPACT-MATRIX.md` 靠手工维护 | 需生成式追溯矩阵（从测试名/事件/模块 docstring 抽取） |
| 测试与验收 | ✅ 三层测试纪律 + 不变量优先（RED→GREEN） | ❌ `acceptance/`、`e2e/`、`security/` 空置；INV-02/03/06 无专项 | 见 B2-8/B2-9 |
| 文档一致性 | ✅ 有仲裁链与维护纪律（`MAP.md:212`） | ❌ 词表/字段/子命令数多处脱节（§1.6、B2-1~B2-3） | 需一致性校验器（文档数字 vs 运行时真值） |

---

# 4. 模块迁移计划

## 4.1 保留（无需修改，作为治理层的地基）

| 模块 | 为什么保留 |
|---|---|
| `events/{envelope,vocab,payload}.py` | 73 型词表 + 五步校验 + `extra="forbid"` 已是治理事件的最佳载体。治理层**只新增事件类型**，不改动既有校验链 |
| `core/session.py` | append-only 唯一逻辑写口 + reducer。治理层的一切写入仍必经 `append`（INV-01 不动摇） |
| `persistence.py` / `repair.py` | 真源物理层与崩溃修复。治理层只消费 `replay()` |
| `core/tools_guard.py` 的**单调拒绝语义** | `_FORBIDDEN_API` 结构性防放行 + `_BUILTIN_IDS` 求值序 + g1–g7 判定逻辑**全部保留**；只改"装配方式"与"决策产物形态" |
| `core/approval.py` | TTL/合并/信任/防重放/`by` 语义全部保留；只提升为治理层 HITL 通道 + 修展示缺陷 |
| `core/tools_executor.py` 的**四关结构** | 关 1a/1b/2a/2b/2.5/3/4 的顺序与 fail-closed 语义保留；只在关 2b 出口处升级决策对象 |
| `core/scope.py` | 单调收紧（`tighten`/无 relax）+ 预算硬闸保留；只解掉与 `llm_fallback` 的分层倒置 |
| `bus/*` | 总线地基 |
| `tests/unit/test_tools_guard.py`(76 例) / `test_tools_registry.py`(51) / `test_desktop.py`(59) | 回归安全网，**不得为了新架构而重写**（CORE-03：先判定"有意行为 vs 缺陷"） |

## 4.2 重构（保留语义，调整形态/接线）

| 模块 | 重构内容 | 依据 |
|---|---|---|
| `engine.py` | ① `build_runner_components`（:425-610）拆为 4–5 个装配子函数：`_build_runtime` / `_build_tools_pipeline` / `_build_llm` / `_build_orchestration` / `_attach_persistence`。② 改用 `GuardChain.from_config(cfg, ...)` 并注入 `validator=registry.validate_args`、`credential_paths`、`path_exists`、`link_resolver`、`approval_channel=通道在位判定`。③ 停止就地改写 `cfg.llm.model` / `cfg.security.sandbox.level` | 修 W1/B0-1/B0-2/B0-3；解 P-2 |
| `core/tools_guard.py` | ① 决策出口从 `Decision`(StrEnum) 升级为 `Decision`(对象：`verdict` + `policy_refs` + `guard_ids` + `principal` + `decision_id` + `receipt_ref`)。② `evaluate` 保持 waterfall/单调/降级两闸语义不变。③ 把 `_resolve_geometry` 与 `tool_fs` 的重复实现收敛为单点（`tool_fs` 消费 guard 的判定） | 解 P-6、§3.1.2；消 §2.3 前两行重复 |
| `core/tools_executor.py` | ① 关 2b 消费新 `Decision` 对象；② 关 2.5 审批绑定指纹改为落盘凭证（Phase 3）；③ 删死代码 `_invoke`(:679)；④ `_finalize` 补齐超时/异常路径的 `elapsed_ms`；⑤ `summarize` 修 B1-9（优先级表补 `code/input/args/script`；嵌套值展开关键键；超预算改为"截断+标注"而非"整键丢失"） | 修 B1-9；解 §3.1.5 |
| `core/agent_loop.py` | ① 实现真正的 `paused` 状态 + `resume()`（对齐 DIS §1.4 承诺），或**明确删除** `paused/stopping/terminated` 三态声明并同步改 `agent.py:412`（二选一，不得留死代码）；② `_must_stop` 闸 3 删除死判据 `"paused"`；③ `_outcome_fingerprint` 改 `payload.get(...)`（修 B1-5）；④ `BudgetExhausted` 与 `PyHError("BUDGET-EXHAUSTED")` 收敛为单一信号（修 W3）；⑤ F026 连败计数接入 `run_step` 路径（修 W4） | 修 W2/W3/W4/B1-1/B1-5 |
| `core/session.py` | ① `append` 的 `_closed` 置位移到**落盘成功之后**（修 W5/B1-4）；② 与 `agent.close` 协同：回滚 state 时同步回滚 `_closed`（或引入显式事务边界）；③ 收敛与 `llm._est_tokens` 的重复估算 | 修 W5、B1-4 |
| `core/scope.py` | ① 解分层倒置：`BudgetState`/`TaskUsage` 移入中立模块（如 `core/contracts.py`），`scope` 与 `llm_fallback` 都从那里 import；② `budget_state` 与 `budget._compute_state` 收敛为单点判据 | 解 P-4、§2.3 |
| `core/agent.py` | ① 删除 `_on_bus_event` 中调用不存在方法的 `resume` 分支（或随 `LoopState` 一起实现）；② 停止向 `Ctx` 无限追加属性——治理相关内容集中到 `ctx.governance` 单实例（`GovernedContext`），避免 Service Locator 继续膨胀 | 修 B1-1；解 P-1 |
| `desktop/app.py` | ① 修 `_SESSION_ID_IN_PATH` 正则（支持 `-`/`.`），使 `s-fork-*` 走服务端派生租户（修 B1-10）；② 未注册会话**不回落客户端头**，默认拒绝或回落 `default` 但显式留痕 | 修 B1-10 |
| `core/telemetry.py` | 从"类型计数聚合"升级为**治理审计视图**（决策因果链：谁/何时/因何/结果），保留 `session_audit` 兼容 | §3.1.6 |
| `core/workflow.py` | 若 v1.0 承诺工作流治理：需引入真实 DAG（节点+依赖+并行+失败策略），否则在文档中明确降级为"顺序步骤编排器" | 解 P-5 |
| `tests/invariants/` | 重写为覆盖 **INV-01~09 全量**；现有 3 文件 7 用例为早期骨架（文件头仍写"当前 RED：模块未实现"） | 解 B2-9 |

## 4.3 新增（治理层净新增，共 4 个模块 + 3 个测试面）

| 新增模块 | 职责 | 架在什么之上 |
|---|---|---|
| `pyharness/governance/policy.py` | **Policy Engine**：`Policy` 一等对象（id/version/fingerprint/rules/scope）、策略注册表、策略加载器（YAML→规则）、策略版本治理（内容哈希落盘）。**判定逻辑复用 `tools_guard` 的 g1–g7，不重写** | `tools_guard` 函数对 + `config.security.*` |
| `pyharness/governance/decision.py` | **Decision Engine**：`Principal`（主体：user/agent/tool/plugin + 通道身份）、`Decision`（决策对象：verdict + policy_refs + inputs_digest + principal + decision_id + ts）、决策生命周期状态机（issued→approved→executed/revoked） | 现有 `Decision` 枚举 + `guard.evaluated` 载荷 |
| `pyharness/governance/receipt.py` | **Decision Receipt**：`DecisionReceipt` 模型 + 序列化 + 内容哈希 + `verify()` 离线核验；把 `approval._grant_slots` + `_approval_binding` 升级为持久化凭证 | `approval.*` 事件 + binding 指纹算法 |
| `pyharness/governance/evidence.py` | **Evidence System**：证据工件模型（claim/test/result/artifact/limitation）、证据与决策/任务的绑定、归档到 `.ai-coding/evidence/`（激活现空置目录）、运行时追溯矩阵（F 编号↔模块↔测试↔事件） | `.ai-coding/PROTOCOL.md` §10 链条 + `segment.start/end` 段锚 |

**新增事件类型**（词表只增不改，`EVENT-SCHEMA.md:588`）：

| 事件 | 时机 | 强同步 |
|---|---|---|
| `policy.updated` | 策略装配/收紧 | 是 |
| `decision.issued` | guard 求值出决策 | 是 |
| `receipt.emitted` | 凭证生成 | 是 |
| `evidence.archived` | 证据归档 | 否 |
| `governance.denied` | 治理层拒绝（可复用 `guard.rejected`，视设计定） | 是 |

**新增测试面**：

| 目录 | 内容 |
|---|---|
| `tests/acceptance/` | 按 F 编号的验收测试（激活空置目录，对齐 `SOP.md` 承诺） |
| `tests/security/` | 安全攻击测试（T-SEC-03/09/10，激活 `specs/approval.py.md:158` 承诺） |
| `tests/invariants/` | 补齐 INV-01~09 + 新增治理不变量（如 INV-G1：每个 `tool.result` 必有前置 `decision.issued`） |

## 4.4 废弃（应删除的设计）

| 废弃项 | 理由 | 证据 |
|---|---|---|
| `tools_executor._invoke` | 死代码，零调用点 | `tools_executor.py:679` |
| `session_query._MIN_CJK` / `_MIN_LATIN` | 死常量，仅 `__all__` 导出 | `session_query.py:100-101` |
| `LoopState` 中的 `paused/stopping/terminated`（或实现之） | 声明但永不赋值——**二选一，不得留死态** | `agent_loop.py:63` |
| `approval._deny_no_channel` | docstring 自认"签名标注 `-> str` 但恒不返回"（死返回面） | `approval.py:309,47` |
| `persistence.SessionStore.repair` | 与 `repair.repair_session` 语义分歧（隔离只是内存行号，坏行仍在主文件）→ 收敛为单一 repair 真源 | `persistence.py:470` vs `repair.py:380` |
| 5 份重复的 bus→store 落盘适配器 | 收敛为单一 `PersistenceSubscription`（含单一 `_SYNC` 真源，从 `SYNC_TYPES` 派生而非手写） | `engine.py:627`/`cli.py:602`/`desktop/sessions.py:151`/`orchestration.py:145`/`repair.py:700` |
| `repair._DirectBus` + 直接改 `SessionLog._bus` | 私有属性 hack → 应经公开注入点 | `repair.py:682,699-710` |
| `llm_fallback.pick`（若无外部用途） | 仅被自身调用 | `llm_fallback.py:358` |
| `tool_exec._DEFS`/`_PROVIDERS`/`_DANGER` 三份平行表 | 收敛为单一注册时声明（`register()` 内一处定义） | `tool_exec.py:148,186,196` |

---

# 5. 升级路线（渐进式演进，不推翻重写）

## 总原则

1. **每个 Phase 结束都必须全绿**（`pytest` 1326+ 用例 + invariants），且**每个 Phase 只做一类事**。
2. **不新增第二份状态**：治理层的一切持久化都写成事件（守住 INV-01）。
3. **不重写已有判定逻辑**：guard 的 g1–g7、approval 的 TTL/防重放、四关管道结构 **语义冻结**。
4. **每个 Phase 必须产出**：`STATUS / Impact Analysis / Triggered CND / Implementation / Tests / Evidence / Limitations / Regression`（按 `PROTOCOL.md` §11）。
5. **CND 自然触发，不为验证规则而制造问题**（v0.3 FROZEN/OBSERVE 纪律）。

---

## Phase 0 — 架构整理（消灭沙上建塔）

**目标**：在不引入任何治理新概念的前提下，把"已有机制但未接线/有断点/重复"的部分收口。Phase 0 结束时，系统行为**对外不可见地**变得可靠。

**修改文件**

| 文件 | 改动 |
|---|---|
| `engine.py` | 改用 `GuardChain.from_config(cfg, validator=tool_reg.validate_args, credential_paths=…, path_exists=…, link_resolver=…, approval_channel=<通道在位判定>)`；停止就地改写 `cfg` |
| `engine.py` | `build_runner_components` 按职责拆分为 4–5 个子函数（纯重构，不改行为） |
| `core/agent_loop.py` | ① 实现 `resume()` + 真实 `paused` 态（**或**删除三态死声明 + 同步清理 `agent.py:412`）；② `_outcome_fingerprint` 改 `.get()`；③ F026 连败接入 `run_step` |
| `core/session.py` | `_closed` 置位改到落盘成功之后 |
| `core/agent.py` | 回滚 state 时同步 `session` 状态；清理 `resume` 死分支 |
| `core/tools_executor.py` | 删 `_invoke`；`_finalize` 补 `elapsed_ms` |
| `core/scope.py` | `BudgetState`/`TaskUsage` 移入中立模块，解分层倒置 |
| `persistence.py` / `repair.py` | 收敛为一套 repair（保留 `repair.repair_session`，`SessionStore.repair` 转薄委托或删除） |
| 5 处落盘适配器 | 收敛为单一实现，`_SYNC` 从 `SYNC_TYPES` 派生 |

**为什么**：这 5 项（W1–W5）会让"治理层看到的策略/预算/关闭语义"失真。**如果先建治理层再修这些，治理层会把错误语义固化**。例如 g1 空转意味着"策略执行点"有 1/7 是假的；两种预算终态意味着"预算治理"读到的终态不一致。

**风险**：
- 中：`engine.py` 是唯一的装配单点，拆分与重接线会同时触达所有外壳（CLI/桌面/ACP/原生）。**缓解**：先补 `engine` 装配的验收测试（当前 `test_engine_flow.py` 仅 1 个大用例、`test_engine.py` 存在但薄），把"装配后关键接线在位"断言先立起来。
- 中：`paused/resume` 二选一。选"实现"会新增并发面（CND-01 自然触发）；选"删除"要确认无外部依赖者。**缓解**：先 grep 全库（已确认仅 `agent.py:412` 一处）。
- 低：`_closed` 时序修复会改变 `append` 的失败副作用顺序。**缓解**：这是提升（失败不再污染状态），补幂等重试测试。

**验证方式**
- `pytest tests/unit/test_agent_loop.py tests/unit/test_session.py tests/unit/test_engine*.py tests/unit/test_approval.py -q`
- 新增断言：`GuardChain.from_config(...)` 后的 `enabled_guard_ids()` 与 `cfg.security.guards.disabled` 一致；g1 在注入 validator 后**能**拒绝非法参数
- 端到端：`pyharness run "读一下 README"`（真链 smoke）+ `tests/invariants/` 全绿
- 出口判据：W1–W5 **全部消失**，且无新增红

---

## Phase 1 — Governance Layer（引入 4 个新模块，零行为变更）

**目标**：新增 `pyharness/governance/` 包（`policy.py` / `decision.py` / `receipt.py` 骨架 / `evidence.py` 骨架），**但不改变任何现有执行路径**——治理层此时是"旁挂的观察者 + 可选的权威源"。

**修改文件**

| 文件 | 改动 |
|---|---|
| `pyharness/governance/__init__.py` | 新建包 |
| `governance/policy.py` | `Policy`（id/version/fingerprint/rules）、`PolicyRegistry`、`from_config()`、**策略指纹 = 内容哈希**（替代 `chain_version` 的内存计数） |
| `governance/decision.py` | `Principal`（主体模型）、`Decision` 对象（verdict + policy_refs + guard_ids + inputs_digest + principal + decision_id + ts） |
| `governance/receipt.py` | `DecisionReceipt` 模型 + `to_dict/from_dict`（先不落盘） |
| `governance/evidence.py` | `Evidence` 模型 + `Claim` 模型（对齐 `PROTOCOL.md` §10 六级链条） |
| `events/payload.py` + `events/vocab.py` | 注册 `policy.updated` / `decision.issued`（只增不改，遵循 `EVENT-SCHEMA.md:588` 演进规则） |
| `bus/registry.py` | 新增 `governance` 索引类（与 plugin/tool/capability 并列） |

**为什么**：把 3.1 的缺口补齐为**显式命名的一等对象**。命名本身就是治理的一部分——`Decision` 从"一个枚举值"变成"一次有主体的决策"，才能谈凭证与追溯。此阶段**只建模型与注册表**，不改 `tools_guard`/`tools_executor`，因此零回归风险。

**风险**：
- 低（新增包，不动既有路径）。**唯一风险**是"模型设计错误导致 Phase 2 返工"。**缓解**：`Decision` 的字段必须能 100% 承载现有 `guard.evaluated` + `guard.rejected` + `approval.*` 的全部信息（对照 `payload.py:177` 的 `ApprovalRequestedPayload` 四字段与 `guard.evaluated` 四字段做覆盖核对）。
- 低：策略指纹若与现有 `preset` 语义冲突。**缓解**：Phase 1 只做"读出并计算指纹"，不接管决策。

**验证方式**
- 单元测试：`tests/unit/test_governance_policy.py`、`test_governance_decision.py`
- **一致性测试**（关键）：对同一组 `(tool, args, scope)`，`governance.policy` 推导的判定结果必须与 `tools_guard` 的实际判定**逐例一致**（这证明"复用而非重写"）
- 新增不变量：`INV-G0`：`Decision` 对象可完整重建 `guard.evaluated` 载荷，且信息不丢失
- 出口判据：治理层可"只读复述"每一条已有 guard 决策，且结果与生产一致

---

## Phase 2 — Tool 治理接入（四关管道向治理层要裁决）

**目标**：让**所有**工具调用从"guard 出枚举值"变为"治理层出带因果的决策对象"。这是唯一触及安全主干的 Phase。

**修改文件**

| 文件 | 改动 |
|---|---|
| `core/tools_guard.py` | `evaluate()` 返回 `Decision` **对象**（保留 waterfall/单调/降级两闸）；`guard.evaluated` 载荷扩展为含 `decision_id`（经 `trace` 携带，规避 `extra="forbid"`，同 `tools_guard.py:36-39` 既有先例）；保留向后兼容：`__eq__`/`__str__` 使 `d == "reject"` 仍成立（`tools_executor.py:449` 不需要改） |
| `core/tools_executor.py` | 关 2b 消费 `Decision` 对象；决策 ID 与 `tool.call`（`trace.call_id`）双向关联；`_reject`（:575）与 `_approval_round`（:537）统一改走治理决策出口 |
| `core/agent.py` | `ctx.governance` 单实例挂载（**不再向 Ctx 追加散字段**） |
| `engine.py` | 装配 `ctx.governance`（治理层唯一装配点，从 Phase 0 拆出的子函数挂入） |

**为什么**：这是"Governed"的实质落地——**决策有主体、有依据、有 ID、可被引用**。之所以放在 Phase 2（而非更晚），是因为 Phase 3 的 Receipt 必须有 `decision_id` 作为主键；没有决策 ID，凭证无从锚定。

**风险**：
- **高**：`tools_guard` 是安全主干，`GuardChain.evaluate` 被 76 个单元测试 + 全部工具调用路径依赖。**缓解**：
  1. 先立**等价性测试**——对决策对象序列化后的 `verdict`/`policy_refs`，逐例比对 Phase 1 的只读复述结果；
  2. `Decision` 对象实现 `__eq__`/`__str__` 使既有 `d == "reject"` 断言**全部继续通过**（`tools_executor.py:449,563` 是 StrEnum 直接比较）；
  3. 遵循 CORE-03：任何变红的测试先判定"有意行为 vs 缺陷"；
  4. 按 CORE-02 高影响信号清单 → **全量回归**（改了 `tools_guard`，命中"安全边界/权限"高影响信号）。
- **高**：`guard.evaluated` 载荷加字段会触发 `extra="forbid"` 的 EVT-100（既有先例：`call_id` 只能进 `trace`）。**缓解**：新字段一律走 `Envelope.trace`；若确需入 payload，走 `EVENT-SCHEMA.md:588` 的"只加可选字段"演进规则并**同步词表版本号**。

**验证方式**
- 全量回归：`pytest -q`（1326+ 用例；本 Phase 必跑全量）
- 等价性：新增 `tests/acceptance/test_decision_equivalence.py`——随机生成 N 组 `(tool, args, scope)`，断言"Phase 2 决策对象的 verdict+policy_refs" == "Phase 1 只读复述"
- 治理不变量：`INV-G1`：每个 `tool.result` 之前必有同 `call_id` 的 `decision.issued`
- 真链：`pyharness run "写一个文件到 workspace"`（触发 g7→approval→执行）；`pyharness run "删除 workspace 里的 x"`（触发 g2 critical→reject，审计可证"拦了且没执行"）
- 出口判据：所有工具调用路径产生治理决策对象；无测试为"适配新形态"而放松安全断言

---

## Phase 3 — Decision Receipt（裁决凭证化）

**目标**：把"批准了什么"从**内存态**变为**持久化、可离线核验的凭证**。

**修改文件**

| 文件 | 改动 |
|---|---|
| `governance/receipt.py` | 完整实现：`DecisionReceipt`（decision_id + principal + verdict + policy_refs + **inputs_digest** + prev_receipt_hash + ts + signature）、`emit()`、`verify()` |
| `core/approval.py` | `_grant_slots`（:217，内存 + 无界增长）→ 落盘凭证；`grant_binding`（:608）从凭证查询；`binding` 指纹（已有）作为 `inputs_digest` 来源 |
| `core/tools_executor.py` | `_approval_round`（:537）的绑定校验改为**凭证核验**（而非仅内存比对）；`_approval_binding`（:218）保留为 digest 算法 |
| `events/payload.py` + `vocab.py` | 注册 `receipt.emitted`（强同步） |
| `core/telemetry.py` | 审计视图增加"凭证链"（每条凭证 ↔ 其决策 ↔ 其执行） |

**为什么**：当前 `_grant_slots` 是**唯一认证"批准与执行同一参数"的机制**，但它不落盘、不可离线核验、无界增长（B1-11）。若进程重启，"谁批准了什么"只留在事件里而没有可核验工件。Receipt 让"授权↔执行"的绑定性从**运行时内存事实**升级为**可归档的凭证**——这是 Governed Runtime 与普通 Agent 框架最显著的分界。

**风险**：
- 中高：改动审批校验路径（安全主干）。**缓解**：`verify()` 失败必须 **fail-closed**（拒绝执行）；保留旧路径为 feature-flag 或双写一段时间，用等价测试证明行为不变。
- 中：凭证与事件真源的一致性——**凭证不得成为第二份状态**（INV-01）。**缓解**：凭证本身以事件形式落盘（`receipt.emitted`），`verify()` 从事件真源重放核验；内存只做缓存。
- 中：`prev_receipt_hash` 引入哈希链 → 并发写序问题（CND-01 自然触发）。**缓解**：链的锚定以 `seq` 为序（真源已有全序），哈希只做完整性不做排序。

**验证方式**
- 单元：`test_governance_receipt.py`——篡改凭证任一字段 → `verify()` 必须 False
- 集成：审批通过 → 执行 → 凭证落盘；重启会话 → `verify()` 仍成立（证明不依赖内存）
- 攻击用例（**激活 `tests/security/`**）："批准参数 A、实际执行参数 B" → 必须被拒（GRD-403 语义的凭证版）
- 治理不变量：`INV-G2`：每个 `approval.granted` 必对应一条 `receipt.emitted`；`INV-G3`：`receipt.verify()` 在真源重放后结果不变（幂等）
- 出口判据：`_grant_slots` 无界增长消失；凭证可离线核验

---

## Phase 4 — Evidence 与 Audit（证据链 + 审计模型 + 对账）

**目标**：把"证据"从协议层纪律落到运行时；把"审计"从计数聚合成因果链；把 `syscheck` 接上调度。

**修改文件**

| 文件 | 改动 |
|---|---|
| `governance/evidence.py` | 完整实现：`Evidence`（claim/test/result/artifact/limitation）、归档到 `.ai-coding/evidence/`（激活空置目录）、证据与决策/任务（`segment.start/end` 段锚）绑定 |
| `core/telemetry.py` | 治理审计视图：按 `decision_id` 串起"请求 → 策略 → 决策 → 审批 → 凭证 → 执行 → 结果"完整因果链；保留 `session_audit` 兼容 |
| `core/session_query.py` | 支撑"按 F 编号/决策 ID/策略 ID 检索"的索引维度 |
| `governance/traceability.py`（或并入 evidence） | **运行时追溯矩阵生成器**：从模块 docstring（已含 spec + F 编号）、测试名、事件类型，抽取并生成"F 编号 ↔ 模块 ↔ 测试 ↔ 事件"矩阵；**加一致性校验**（文档数字 vs 运行时真值，自动抓 B2-1/B2-2/B2-3/D6 这类脱节） |
| `core/agent_loop.py` 或新 checker | `syscheck` 调度点（`syscheck.fail` 已在词表 73 型内，`EVENT-SCHEMA.md:478`） |
| `tests/invariants/` | 重写覆盖 INV-01~09 全量（现有 7 用例为早期骨架） |
| `tests/acceptance/` | 按 F 编号补验收测试（激活空置目录） |

**为什么**：3.1.6/3.1.7/3.3 的差距本质是"**有事实，无模型**"。事件日志已经记录了全部事实，但没人能**查询**"这次拒绝的政治依据是什么、谁定的、影响了哪个任务、有无证据"。Phase 4 把真源变成可用的治理视图——**不新增真源，只加索引与视图**。

**风险**：
- 中：追溯矩阵若靠手工维护会立刻腐化（`IMPACT-MATRIX.md` 已是教训）。**必须生成而非手写**。**缓解**：矩阵产物标 `AUTO-GENERATED`，并加"漂移即测试红"的守卫。
- 中：证据归档引入文件写入（`.ai-coding/evidence/`）→ 需保证"证据不落第二份真源"且不违反公开仓库脱敏纪律（**memory: 公开仓库禁止真实姓名/本机路径/调试日志**）。**缓解**：证据归档只存**引用**（seq/decision_id/事件摘要 + 脱敏后的工件路径），原文仍在 JSONL 真源。
- 低：`syscheck` 调度点引入周期性任务（CND-01/CND-05 可能自然触发）。
- 低：INV 测试重写可能暴露既有缺陷（**这是好事**，但会打断"全绿"节奏）。**缓解**：先把暴露的问题记入 `KEY-FINDINGS.md`，按 P 级分阶段修，不阻塞 Phase 完成。

**验证方式**
- `pytest tests/invariants/ -m invariant -q`（全绿且覆盖 INV-01~09）
- 追溯矩阵一致性：故意把 `CODE-MATRIX.md` 的测试数改错 → 校验必须失败
- 审计因果链：给定一次被拒的工具调用，能一条命令还原"谁/何时/因何/依据哪条策略/结果如何"
- 端到端：`kill -9` 后 `pyharness repair` + 重放，证据与凭证仍可核验
- 出口判据：`.ai-coding/evidence/` 非空且有真实归档；`syscheck` 在生产链路上有调度点

---

## Phase 5 — 生产级 Demo

**目标**：一个**端到端可演示、可复现、可核验**的治理场景，作为 v1.0 的对外证据。

**修改文件**

| 文件 | 改动 |
|---|---|
| `scripts/` | 新增治理端到端探针（对齐既有 `probe_*.py`/`e2e_*.py` 惯例）：一条命令跑完"策略命中 → 决策 → 审批 → 凭证 → 执行 → 证据归档 → 审计还原" |
| `tests/acceptance/` | 该场景的验收测试（F 编号绑定） |
| `README.md` / `docs/MAP.md` | 架构图与状态刷新（**架构变了，派生视图必须同步**，`MAP.md:212` 维护纪律） |
| `docs/ADD.md` | 为治理层新增 ADR（ADR-013+）：为什么 policy/decision/receipt 是独立层而非 guard 的字段 |
| `.ai-coding/PROTOCOL.md` | **仅在自然触发缺口后**记 PIC，经人工 Review 才动（v0.3 FROZEN 纪律） |

**为什么**：v1.0 的验收标准是"**能用一句话说清治理做了什么，并当场跑出来**"。这也是当前项目缺失最严重的一环——`acceptance/`、`e2e/`、`security/` 全空，`README` 声称的 "1400 passed" 无汇总行可复核（D7）。

**风险**：
- 低（不改主干）。**主要风险是"Demo 与真实架构脱节"**——即 Demo 走捷径而真实路径没有治理。**缓解**：Demo **必须**走真实外壳（`pyharness run` 或桌面），禁止专用 mock 通道；这同时回应 PIT-10"mock 太假"。
- 低：文档刷新滞后。**缓解**：Phase 5 出口判据包含"MAP/README/ADD 与代码一致，且一致性校验器通过"。

**验证方式**
- Demo 可重复执行（两次运行结果一致：决策/凭证/证据可核验）
- 场景覆盖：
  1. **策略命中与拒绝**：critical 工具 → reject，审计可证"拦了且没执行"（INV-05）
  2. **人类审批闭环**：high 工具 → 审批 → 凭证 → 执行；"批准 A 执行 B"被拒
  3. **崩溃恢复**：`kill -9` → repair → 重放 → 凭证/证据仍可核验
  4. **治理负例**：篡改凭证 → `verify()` False；绕过 executor 直调 → 被 g1/g2 抓出
- 出口判据：全量测试绿 + `tests/acceptance|security|invariants` 均有实测用例 + 文档一致性校验通过

---

## 5.6 里程碑与风险总表

| Phase | 净新增模块 | 触及安全主干 | 主要风险 | 关键出口判据 |
|---|---|---|---|---|
| 0 架构整理 | 0 | 是（engine/guard 装配） | 装配单点回归 | W1–W5 消失，全绿 |
| 1 Governance Layer | 4（骨架） | 否（旁挂） | 模型设计返工 | 只读复述与生产判定逐例一致 |
| 2 Tool 治理接入 | 0 | **是（guard/executor）** | 主干回归 + 载荷校验拒写 | 决策等价性 + `INV-G1` + 全量回归 |
| 3 Decision Receipt | 0 | **是（审批校验）** | fail-closed 破坏可用性 | 篡改必拒 + 重启仍可核验 + `INV-G2/G3` |
| 4 Evidence/Audit | 1（traceability） | 否 | 矩阵腐化、暴露既有缺陷 | `acceptance/security/invariants` 非空全绿 |
| 5 生产级 Demo | 0 | 否 | Demo 走捷径 | 端到端可复现 + 文档一致 |

**建议节奏**：Phase 0 与 Phase 1 可合并为一次交付（Phase 0 收口 + Phase 1 只建模型，风险互补）；Phase 2 与 Phase 3 必须**分两次**交付（都动安全主干，合并会放大回归面）。

---

# 附：本次审计的方法与局限

**方法**：4 路并行只读测绘（core 主链 / 治理与持久化 / UI 与测试 / 文档与契约）+ 审计人直读 9 个主链文件（`engine`/`agent`/`agent_loop`/`session`/`task_queue`/`tools_executor`/`tools_guard`/`approval`/`scope`/`budget`）+ 运行时校验（`EVENT_TYPES`/`SYNC_TYPES`/payload 模型计数）。

**局限**：
1. 本审计**未运行测试套件**（只读约束）。`README.md:4` 的 "1400 passed / 2 skipped" 与 `CODE-MATRIX.md:11` 的 "1298" 存在冲突且无汇总日志可复核（§1.6 D7）——**建议在 Phase 0 之前先跑一次全量并留证**。
2. 行号以 `0cba75d` 工作树为准；部分子代理报告的行号与直读值存在 ±1 行漂移（文件尾空行差异），引用时以文件内符号名为准。
3. 本审计为**架构级**，未对每个模块做行级安全审计；`tool_web`(801)/`session_query`(991)/`application/service`(1043) 等大模块只覆盖了与治理相关的面。
4. 未评估**性能**（如 `open_session` 全量重放在大会话下的成本、`session_query.flush` 阻塞事件循环），Phase 4 引入索引时应一并测量。

**待人工决策点**（不猜测，列出供确认）：
1. **`paused/resume` 二选一**：实现真实暂停（新增并发面），还是删除三态声明（承认"暂停"不在 v1.0 范围）？
2. **`workflow` 是否进入 v1.0 治理范围**？若是，需引入真实 DAG（当前仅顺序步骤，`workflow.py:45`）；若否，建议在文档中明确降级。
3. **Receipt 的完整性强度**：哈希链（防篡改，无需外部依赖）还是数字签名（更强，需密钥治理）？
4. **`Decision` 载荷扩展走 `trace` 还是走 payload + 词表版本升级**？（前者零风险但语义藏在信封；后者语义显式但要动词表）
5. **策略外部化的粒度**：仅 config 键（现状）→ YAML 策略文件 → 可插拔策略插件，v1.0 做到哪一级？
