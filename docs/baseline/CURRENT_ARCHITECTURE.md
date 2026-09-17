# CURRENT_ARCHITECTURE.md — 当前架构（As-Built，S0 基线）

> ⏱ **时点快照（Historical Baseline）** —— 本文固化于 `0cba75d` @ 2026-09-14，描述**当时**的
> 代码实际形态。**其中部分问题已在此后阶段修复**（例如「g1 `g-schema` 恒 allow（P0）」
> **已由 S1 修复**）。**本文不代表当前状态。**
> 现行能力与已知限制见 [LIMITATIONS.md](../../LIMITATIONS.md)。
>
> **本次仅追加本提示块**：原有结论、数字与行文**一字未改**（阶段记录不得事后修饰）。

> **基线标识**：`0cba75d` @ 2026-09-14
> **性质**：**实测描述**——本文描述的是**代码实际形态**，不是 `docs/MAP.md` 声明的设计形态。两者不一致处在 §6 逐条列出。
> **关联**：[BASELINE_REPORT.md](BASELINE_REPORT.md) · [TEST_BASELINE.md](TEST_BASELINE.md) · [CODE_METRICS.md](CODE_METRICS.md)

---

## 1. 分层全景（As-Built）

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ ④ 外壳层（三壳 + 一桥；共享 ApplicationService）                              │
│   CLI        cli.py 1713 行 · 16 子命令                                       │
│   Web 桌面    desktop/{app,sessions,projection,bridge,launcher,net,constants} │
│              app.py 1031 行 · 54 条 FastAPI 路由 + SSE fan-out                │
│   Qt 桌面     desktop_native/{main_window,controller,app} 904/257/44          │
│              ⚠️ 本环境缺 PySide6 → 不可 import（0% 覆盖）                     │
│   ACP 桥     acp.py 676 行 · JSON-RPC over stdio（5 方法）                    │
│   业务门面    application/service.py 1043 行（God facade）                    │
├──────────────────────────────────────────────────────────────────────────────┤
│ ③ 编排能力层（经 ctx.* 挂载）                                                 │
│   task_queue 434  FIFO 单飞泵 + segment.start/end 段锚（强同步）              │
│   plan_mode 638   顺序步骤 + 24h TTL      schedule 761  cron/interval + 恢复  │
│   jobs 513        ≤4 并发，job 间无依赖    subagent + orchestration 适配层     │
│   workflow 80     ★顺序 for 循环，无 DAG   goal/todo  事件派生状态            │
├──────────────────────────────────────────────────────────────────────────────┤
│ ② 核心脊柱                                                                   │
│   engine.py 890          装配（build_runner_components 185 行上帝函数）        │
│   core/agent.py 494      会话实体 + Ctx 门面（~33 属性，Service Locator）      │
│   core/agent_loop.py 411 三态机（★实为 2 态）+ 三闸终态                       │
│   core/session.py 457    ★ append-only 唯一逻辑写口 + reducer                 │
│   core/scope.py 607      策略唯一数据源 + 预算硬闸（单调只紧不松）             │
│   core/tools_registry.py 635   Definition/Provider 索引 + 参数校验             │
│   core/tools_executor.py 782   ★ 四关执行管道（唯一强制点）                    │
│   core/tools_guard.py 958      ★ g1–g7 单调拒绝链 + policy_ref                │
│   core/approval.py 854         ★ 人类裁决（TTL/合并/信任/防重放/绑定指纹）     │
│   core/llm.py 1032             唯一 llm.chat 调用点 + 计量                     │
│   core/llm_fallback.py 413     降级链 + BudgetGuard                          │
│   core/budget.py 125           预算只读门面（零状态零写）                      │
│   persistence.py 712 / repair.py 812   JSONL 物理层 + 崩溃修复                │
├──────────────────────────────────────────────────────────────────────────────┤
│ ① 事件与总线地基                                                             │
│   events/{envelope 158, vocab 283, payload 605}  9 字段信封 + 五步校验 + 73 型 │
│   bus/{event_bus 427, plugin 358, registry 94}   分发/热插拔/三类索引          │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       ▼
       ★ 会话事件日志 = JSONL append-only = 唯一真源（INV-01）
         消息历史 / UI 投影 / FTS 索引 / 预算读数 / 审计计数 全是它的派生视图
```

---

## 2. 运行时主链（As-Built）

```text
外壳 submit
  └─ agent.submit → session.append("user.message", sync=True)   ← 强同步①
       └─ task_queue.submit → 泵单例 _pump → _run_task
            ├─ append("task.started")
            └─ open_segment → append("segment.start", sync=True) ← 段锚
                 └─ engine.make_runner.run_for_task
                      ├─ _owned_task_message：严格归属窗口定位配套 user.message
                      └─ agent_loop.wake(env, ctx)
                           └─ run(ctx) → _run_engine（while True）
                                ├─ ① _must_stop 三闸：轮数 / 取消 / 预算
                                ├─ ② scope.check_budget()
                                └─ ③ run_turn
                                     ├─ session.derive_messages(window)   ← 现派生
                                     ├─ sysprompt.assemble(hist, ctx)
                                     ├─ ctx.llm.chat/chat_stream          ← 唯一出口
                                     ├─ 纯文本 → append("agent.message") → "complete"
                                     └─ tool_calls → 逐个 run_step → ctx.tools.execute  ★四关
                      └─ _finish → RunResult
       └─ agent.close → append("session.finished", sync=True)     ← 强同步②
```

---

## 3. 工具调用四关（As-Built）

```text
关1a 契约 lookup            tools_executor.py:421-426   → TLB-802 → tool.error 回喂
关1b 参数先验后跑           tools_executor.py:427-437   → TLB-803（Provider 零调用）
     → append("tool.call", {args, raw_args, call_id})    ← INV-06 双份存档
关2a scope 前置             tools_executor.py:445-447   → GRD-401
关2b guard 单调链 ★         tools_executor.py:448-452 → tools_guard.py:672
     求值序（_BUILTIN_IDS）：g-schema → g-fs-path → g-credential-read
                            → g-exec → g-overwrite → g-net-outbound → g-danger
     首个非 allow 即短路；每次求值恰一条 guard.evaluated（INV-04）
     reject → guard.rejected（sync=True，INV-05）
关2.5 审批                  tools_executor.py:537-573 → approval.py:234
     approval.requested(sync) → granted/denied/TTL→timeout
     granted ⇒ 重入 guard 链（非放行）+ inputs_digest 绑定校验
关3 Provider 执行           tools_executor.py:458-496
     同步 handler 走独立单工线程池（超时/取消后驱逐不复用）
     总超时 = defn.timeout_s or 60s → TLB-805；取消写 partial 后 re-raise
关4 finalize                tools_executor.py:734-774
     输出 schema 校验 → >2KB 转 spill → ≤2KB 摘要 → append("tool.result")
```

**已存在的治理原语（实测）**：

| 原语 | 位置 | 状态 |
|---|---|---|
| 单调拒绝（无 bypass） | `tools_guard.py:629` `_FORBIDDEN_API` + `__getattr__` | ✅ 结构性强制 |
| 策略引用令牌 | `policy_ref`：`POL-FS-1/2/3`、`POL-DGR-1`、`POL-CRED-1`、`POL-NET-1`、`POL-EXEC-1`、`POL-OVW-1`、`GRD-401`、`APR-501`、`TLB-803` | ✅ |
| 授权↔执行绑定指纹 | `tools_executor._approval_binding:218` ⇄ `approval._grant_slots:217` | ✅ 算法在；**内存态、不落盘、无界** |
| 主体身份 | `approval._require_human:741` 的 `by="cli:alice"` 白名单 | ⚠️ 自由字符串，非结构化 |
| 决策载体 | `Decision`（StrEnum：allow/reject/approval） | ⚠️ 三值枚举，无主体/依据/ID |
| 审计事实源 | append-only + 11 类强同步 + trace 四键 | ✅ |
| 审计模型 | `telemetry.session_audit:30`（类型计数聚合） | ⚠️ 非因果链 |

---

## 4. 事件真源（As-Built 实测口径）

| 项 | 实测值 | 说明 |
|---|---|---|
| 信封字段 | **9**：`seq / ts / type / session_id / actor / origin / task_id / payload / trace` | docstring 称"十字段"，实为 9（`envelope.py:3,33` vs `:41-49`） |
| 事件类型 | **73** | `vocab.py` 运行时计数 |
| 强同步类型 | **11** | `user.message`、`guard.rejected`、`approval.{requested,granted,denied,timeout}`、`session.{finished,recovered}`、`segment.start`、`fork.created`、`context.compacted` |
| 瞬态类型 | **3** | `llm.chunk` / `config.updated` / `registry.updated`（禁入日志） |
| Payload 模型 | **71** | 全部 `extra="forbid"`；仅 2 个有跨字段校验器 |
| 写侧校验 | `validate_payload` → `model(**payload).model_dump()` | — |
| 读侧校验 | `persistence.replay:450` + `repair._parse_or_none:160` | ✅ 会校验 |
| 总线校验 | ❌ **不校验**（`event_bus.py:10-12` 明示"只中转不落盘"） | 校验盲区 |

---

## 5. 依赖方向（As-Built）

```text
外壳 → application/service → engine → {session, scope, tools_executor, llm, task_queue}
session → persistence（经 Protocol 注入，无反向 import）
tools_executor → {tools_guard, tools_registry, errors, events.vocab}
tools_guard   → errors（session/bus 注入；不 import executor/approval/provider）✅ 单向
approval      → errors, events.vocab（不 import guard/executor）✅ 单向
scope         → errors, ⚠️ core.llm_fallback（顶层 import，分层倒置）
agent_loop    → {config, errors, events, core.scope.BudgetExhausted}（模块底反向 import:82）
```

**已知方向问题**：
- `scope.py:81` 顶层 import `llm_fallback`（策略层依赖 LLM 编排层）——与 `scope.py:14` docstring 自述"无下游脊柱依赖"矛盾；
- `repair.py:682,699-710` 直接篡改 `SessionLog._bus`（私有属性耦合）；
- 物理写口不唯一：`bus→store` 落盘适配器被复制 **5 份**（`engine.py:627` / `cli.py:602` / `desktop/sessions.py:151` / `orchestration.py:145` / `repair.py:700`），且 `repair` 绕过 `SessionLog` 直接 `store.append`。

---

## 6. 与 `docs/MAP.md` 声明架构的偏差（As-Built vs As-Designed）

| # | MAP.md 声明 | 实测 | 性质 |
|---|---|---|---|
| D1 | "三态机 idle/running/terminated"（`MAP.md:39,226`） | `AgentLoop` 仅 2 态可达（`idle`/`running`）；`paused/stopping/terminated` 永不赋值 | ⚠️ 实现小于声明 |
| D2 | "启动 6 步 bootstrap"（`MAP.md:161`） | 实际唯一装配入口是 `engine.build_runner_components` + `assemble_real_engine` / `attach_engine_to_ctx` | ⚠️ 命名不同 |
| D3 | "核心脊柱 8 模块"含 `tools` 为注册表 | 实际拆为 `tools_registry` + `tools_executor` + `tools_guard` 三文件 | ⚠️ 1 模块 → 3 文件 |
| D4 | "外围 40+ 能力" | `engine.py` 实际注册 **18 个 Definition**（fs×4、storage×2、goal/todo、web×2、exec/proc、user.ask、skill×2 等） | ⚠️ 数量口径不同 |
| D5 | "guard 链 g1 schema → g2 danger → g3 …"（`MAP.md:132-133`） | 实际求值序已重排为 g-schema → g3 → g4 → g6 → g7 → g5 → **g-danger 殿后**（`_BUILTIN_IDS`） | ⚠️ MAP 滞后 |
| D6 | "事件词表 57"（`EVENT-SCHEMA.md:101`） | **73** | ⚠️ 滞后 |
| D7 | `SOP.md`/specs 引用的 `tests/acceptance/test_f{nnn}_*.py` | 目录为空（0 文件） | ❌ 承诺未落地 |
| D8 | `specs/approval.py.md:158` 引用的 `tests/security/` | 目录不存在 | ❌ 承诺未落地 |
| D9 | `security.policy.preset` 四档（`DSH功能对照表.md:122`） | 实现存在（`engine._apply_preset:270`），但装配未走 `GuardChain.from_config` → **`security.guards.disabled` 不生效** | ⚠️ 半接线 |
| D10 | "危险工具被 guard 拒" | ⚠️ g1 的 `validator` 恒 None → **g-schema 在生产恒为 allow** | ❌ 防线部分失效 |

---

## 7. 治理能力现状（As-Built 评分）

> 用于 v1.0 差距定位；详细差距分析见 [REFACTOR_PLAN.md](../../REFACTOR_PLAN.md) §3。

| 治理要素 | 成熟度（0–5） | 现状 |
|---|---|---|
| Permission Control | **4** | 工具级权限完整（`Scope.can_use` + 域可见性 + critical 终局）；缺资源级/数据级 |
| Human Approval | **4.5** | TTL/合并/信任/防重放/绑定指纹齐全 |
| Audit Trail（事实源） | **4.5** | append-only + 强同步 + trace 四键；缺审计**模型** |
| Policy Engine | **1.5** | 无 `PolicyEngine` 类；规则内嵌于 7 个 guard 函数 + `ScopePolicy` + preset |
| Decision Engine | **1** | `Decision` 是三值枚举；无主体/依据/ID/生命周期 |
| Decision Receipt | **0** | `receipt` 全库命中 0；`_grant_slots` 内存态不可核验 |
| Evidence System | **1**（协议层 4 / 运行时 0） | `.ai-coding/evidence/` 仅 `.gitkeep` |
| Runtime Checkpoint | **0** | `checkpoint` 命中 0；恢复 = O(全事件) 重放 |
| Runtime 统一状态模型 | **1** | 状态散布于 `Agent.state` / `LoopState` / `TaskQueue` / `Scope` / plan/schedule/job |
| 运行时 Traceability | **0** | `traceability` 命中 0；F↔模块↔测试↔事件矩阵靠人肉维护 |

**一句话**：**执行层治理（enforcement）已具备且较强；治理层（governance）不存在。** 这正是 v1.0 要补的层。
