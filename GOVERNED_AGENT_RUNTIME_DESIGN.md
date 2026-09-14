# GOVERNED_AGENT_RUNTIME_DESIGN.md

> **PyHarness Governed Agent Runtime v1.0 — Architecture Proposal**
> 日期：2026-09-14 ｜ 基线：`main` @ `0cba75d`
> 性质：**设计提案（Design Proposal）**——只定义目标架构、迁移映射与接口契约，**不含任何实现，不修改任何代码**。
> 输入依据：[REFACTOR_PLAN.md](REFACTOR_PLAN.md)（2026-09-14 架构审计）
> 遵循纪律：`.ai-coding/PROTOCOL.md` v0.3（FROZEN/OBSERVE）、INV-01~09、`docs/ADD.md` 12 条 ADR

---

## 0. 设计原则与红线

### 0.1 六条设计原则

| # | 原则 | 含义 | 来源 |
|---|---|---|---|
| G-1 | **收编，不搬迁** | 已有机制（guard/approval/events）是**资产**，用适配与提级收编为显式层；禁止为了"架构好看"而搬运成熟代码 | REFACTOR_PLAN §0.1 |
| G-2 | **不新增第二真源** | 治理层的一切持久化都写成事件，落在既有 append-only JSONL 上；凭证、证据、审计视图全部是**派生或引用** | INV-01 |
| G-3 | **规则实现与规则治理分离** | g1–g7 的**判定逻辑**冻结不动（是规则库）；新增的是**规则之上**的策略对象、版本、决策对象与凭证 | G-1 |
| G-4 | **强制点不搬家** | 唯一把"意图"变"动作"的关口仍是 `tools_executor` 四关管道；治理层**不持有执行权** | REFACTOR_PLAN §1.3 |
| G-5 | **单调性不可逆** | 治理层继承 guard 的单调拒绝语义：只加严、无 relax、无 bypass、审批通过不等于放行 | ADR-003 |
| G-6 | **文档即契约** | 接口一旦冻结进 specs，变更须走 `EVENT-SCHEMA.md:588` 演进规则（只增不改） | ADR-010 |

### 0.2 一条不可协商的红线

> **治理层授权，执行层强制。**
> `Governance Layer` 回答"**允许不允许、依据什么、谁做的决定**"；`Tool Executor` 回答"**怎么强制执行这个决定**"。
> 治理层**没有**任何执行、放行、翻案 API（延续 `tools_guard.py:629` 的 `_FORBIDDEN_API` 结构防线）。
> 若治理层能执行，它就同时成了决策者与执行者——审计独立性丧失，这是本设计最不能犯的错。

---

## 1. 目标架构图

### 1.1 分层总图

新增内容以 **`★`** 标记；其余为现有模块，仅调整接线或出口形态。

```text
                            ┌─────────────────────────────────────────────────┐
                            │              外部主体 (Principal)                │
                            │  user(cli:alice / web:s-xxx / acp:c1) · agent    │
                            │  tool · plugin · system                          │
                            └───────────────────────┬─────────────────────────┘
                                                    │
┌───────────────────────────────────────────────────▼────────────────────────────────┐
│ ④ 外壳层   CLI(16 子命令) · Web桌面(54 路由+SSE) · Qt桌面 · ACP(JSON-RPC)             │
│    ── 无特权路径；主体身份由此层派生（cli:<user> 等），不信任客户端自报                  │
└───────────────────────────────────────────────────┬────────────────────────────────┘
                                                    │ submit / 订阅事件流
┌───────────────────────────────────────────────────▼────────────────────────────────┐
│ AGENT RUNTIME                                                                      │
│   core/agent_loop.py   三态机主循环（唯一 llm.chat 入口）                            │
│   core/agent.py        会话实体 + ctx 门面                                           │
│   core/task_queue.py   FIFO 单飞泵 + ★ segment 段锚（= 治理证据单元边界）              │
│   core/schedule.py / jobs.py / subagent.py / orchestration.py                       │
│                                                                                    │
│   WORKFLOW   core/workflow.py                                                      │
│     v1.0 定位：**顺序步骤编排器**（无 DAG）。治理粒度 = **每个步骤**：                 │
│     每个 step 是一个 governed task（自有 segment + 决策 + 凭证），                    │
│     因此"工作流被治理"无需引入 DAG 引擎。                                            │
└────────┬──────────────────────────────────────────────────────────┬────────────────┘
         │ 意图（tool_calls，raw JSON，不可信）                        │ 只读视图查询
         ▼                                                          │
┌─────────────────────────────────────────────────────────┐          │
│ TOOL EXECUTOR   core/tools_executor.py  —— 强制点（不搬家）│          │
│   关1a 契约 lookup → 关1b 参数先验后跑（INV-06）           │          │
│   关2  授权  ◀──★ 本关出口由"三值枚举"升格为"决策对象"      │          │
│   关2.5 人类审批（HITL）                                   │          │
│   关3  Provider 执行（线程池/超时/取消 partial）            │          │
│   关4  finalize（输出校验 → spill → tool.result）          │          │
└────────┬────────────────────────────────────────────────┘          │
         │ ★ authorize(call, ctx) -> Decision                        │
         ▼                                                          │
╔════════════════════════════════════════════════════════════════════╪════════════════╗
║ GOVERNANCE LAYER  ★  ——授权，不执行                                  │                ║
║                                                                    │                ║
║  ┌───────────────────────────┐        ┌──────────────────────────────────────┐      ║
║  │  POLICY ENGINE            │        │  DECISION ENGINE                     │      ║
║  │  ★ governance/policy.py    │───────▶│  ★ governance/decision.py            │      ║
║  │                           │  规则   │                                      │      ║
║  │  • Policy(id,version,fp)  │  与序   │  • 继承 GuardChain 的 waterfall 求值   │      ║
║  │  • PolicyRegistry         │        │  • 输出 Decision 对象（非枚举）        │      ║
║  │  • 策略指纹 = 内容哈希     │        │  • 注入 Principal（谁在做决定）        │      ║
║  │  • 规则库 = g1–g7（复用）  │        │  • decision_id 主键                   │      ║
║  └────────────┬──────────────┘        └───────┬──────────────────────┬───────┘      ║
║               │ 读配置                        │ HITL                 │ 落事件        ║
║        ┌──────▼──────┐              ┌─────────▼────────────┐  ┌──────▼─────────┐  ║
║        │ config.yaml │              │ APPROVAL CHANNEL     │  │ DECISION       │  ║
║        │ security.*  │              │ core/approval.py     │  │ RECEIPT        │  ║
║        └─────────────┘              │ (复用，不搬迁)        │  │ ★ receipt.py   │  ║
║                                     │ TTL/合并/防重放/绑定  │  │ • 凭证模型     │  ║
║                                     └──────────────────────┘  │ • 内容哈希     │  ║
║                                                               │ • 前序哈希链   │  ║
║  ┌──────────────────────────────┐                             │ • verify()     │  ║
║  │  EVIDENCE SYSTEM             │   ┌─────────────────────────┴──────────────┐  ║
║  │  ★ governance/evidence.py     │   │  AUDIT SYSTEM                          │  ║
║  │  • Evidence 工件（只存引用）   │◀──│  ★ 治理审计视图（telemetry 提级）        │  ║
║  │  • Claim/Test/Result/Limitation│  │  • 因果链：决策→审批→凭证→执行→结果     │  ║
║  │  • 追溯矩阵（F↔模块↔测试↔事件）│  │  • 对账：syscheck 调度点                │  ║
║  └──────────────────────────────┘   └────────────────────────────────────────┘  ║
╚═══════════════════════════════════════════════════╤═════════════════════════════════╝
                                                    │ 一切写入必经 append
                                                    ▼
                    ★ 会话事件日志 JSONL append-only = 唯一真源（INV-01 不动摇）
       decision.issued · receipt.emitted · guard.evaluated · guard.rejected · approval.*
       segment.start/end · tool.call/result/error · policy.updated · evidence.archived
```

**读图要点**：

1. **治理层是一个"横切权威层"**，不是新增的一层业务逻辑。它对上只暴露 `authorize()`，对下只写事件。
2. **唯一的执行关口仍在 `tools_executor` 关 2**。治理层换掉的是这个关口的**产出物形态**（枚举 → 决策对象），不是关口位置。
3. **`workflow` 无需 DAG 也能被治理**：治理粒度下移到 `task_queue` 的 segment，每个步骤天然带决策与凭证。
4. **`receipt` 与 `evidence` 都指向事件真源**，自身不存真相——`receipt` 是"可离线核验的引用"，`evidence` 是"引用 + 索引"。

### 1.2 一次工具调用的治理时序

```text
LLM ──tool_calls──▶ AgentLoop.run_turn（run_step，逐个）
                          │
                          ▼
                    ToolExecutor.execute(call, ctx)
                          │
        关1a/1b  契约 + 参数校验（不变）── 失败 → tool.error 回喂
                          │
        关2      ★ ctx.governance.authorize(call, ctx)  ────────────────────────┐
                          │                                                    │
                          │        ┌───────────────────────────────────────────▼──────────┐
                          │        │ DecisionEngine.evaluate(call, scope, principal)       │
                          │        │  ① PolicyEngine.resolve(call) → 命中的 Policy 集合    │
                          │        │  ② waterfall 求值（复用 GuardChain 语义，顺序不变）    │
                          │        │  ③ 首个非 allow → 短路                                │
                          │        │  ④ approval 降级两闸（critical→reject；无通道→reject） │
                          │        │  ⑤ 产出 Decision{decision_id, verdict, policy_refs,   │
                          │        │       inputs_digest, principal, ts}                   │
                          │        │  ⑥ append("decision.issued", …, sync=True)   ← INV-G1  │
                          │        └──────────────────┬────────────────────────────────┘
                          │                           │
                          │        verdict == approval │
                          │                           ▼
                          │              ApprovalChannel.request(…)   ← 复用 approval.py
                          │                 · approval.requested(sync)
                          │                 · 等 granted / denied / TTL=denied
                          │                 · granted ⇒ ★ ReceiptStore.emit(approval_receipt)
                          │                 · ★ 重入 DecisionEngine.evaluate（单调性高于人类意志）
                          │                           │
                          │◀──────────────────────────┘
                          │  Decision{verdict=allow}
                          ▼
        关3     Provider 执行（不变：线程池/超时/取消 partial）
                          │
        关4     finalize → tool.result（不变）→ ★ EvidenceCollector.on_result(…)
```

### 1.3 层间契约（谁调用谁）

| 调用方 | 被调方 | 契约 | 方向 |
|---|---|---|---|
| `tool_executor` 关 2 | `governance.authorize()` | 请求授权，取回 `Decision` | 单向 ↓ |
| `decision_engine` | `policy_engine.resolve()` | 取命中策略（含 guard 规则与 policy_ref） | 内部 |
| `decision_engine` | `approval_channel.request()` | HITL 裁决（复用 `approval.py`） | 内部 |
| `decision_engine` | `session.append()` | 落 `decision.issued`（强同步） | 单向 ↓ |
| `receipt_store` | `session.append()` | 落 `receipt.emitted`（强同步） | 单向 ↓ |
| `evidence_collector` | `session.events_after()` | 只读订阅事件（**不写除 evidence.archived 外的事件**） | 只读 ↑ |
| `audit_system` | `session.replay()` | 只读回放 + 因果链重建 | 只读 ↑ |
| `policy_engine` | `config` | 只读配置 | 单向 ↓ |
| **禁止** | — | 治理层 → `tool_registry`/`provider`（无执行权）；治理层 → `llm` | 红线 |

**INV-08 方向校验**：`governance/` 只允许依赖 `events`（读写）与 `errors`；**不得** import `tools_executor` / `llm` / `bus.plugin`。`tools_executor` 允许 import `governance`（消费授权结果），反向禁止。

### 1.4 与现状相比，新增的接缝只有 3 处

| # | 接缝 | 位置 | 改动量 |
|---|---|---|---|
| S1 | 关 2 出口形态：枚举 → 决策对象 | `tools_executor.py:448-452` | 小（返回类型升级；`Decision` 保留 `__eq__`/`__str__` 兼容 `d == "reject"`） |
| S2 | 装配点：`ctx.governance` 单实例挂载 | `engine.py`（Phase 0 已拆出的装配子函数） | 小（一处挂载，替代散字段） |
| S3 | 事件类型注册：`policy.updated` / `decision.issued` / `receipt.emitted` / `evidence.archived` | `events/vocab.py` + `payload.py` | 中（须遵守只增不改 + 词表版本升级） |

**除此之外，执行路径一行不改。** 这是"收编而非重写"的可验证判据。

---

## 2. 现有模块迁移映射

### 2.1 逐模块映射表

> 迁移方式图例：
> **保留原位** = 文件与语义均不动 ｜ **原位提级** = 文件不动，出口/调用方升级 ｜ **抽接口** = 从既有代码抽出 Protocol ｜ **新建** = 新增文件 ｜ **收敛** = 合并重复实现

| 当前模块 | 保留位置 | 新位置 | 迁移方式 | 依据 / 备注 |
|---|---|---|---|---|
| `core/tools_guard.py` — `Guard` 基类、`g_*_match/check`、`_resolve_geometry`、`_inside_workspace` | ✅ 全部保留在原文件 | 概念上属 `governance/policy_rules.py`（**建议 v1.1 再物理搬迁**） | **保留原位** | 这是**规则库**，是资产；76 个单测钉死。v1.0 只添加"被 PolicyEngine 引用"的注册入口 |
| `core/tools_guard.py` — `GuardChain.evaluate`（waterfall 编排 + 降级两闸 + 审计事件） | — | `governance/decision.py` `DecisionEngine.evaluate` | **原位提级 + 委托** | 编排逻辑搬到治理层（它产出的是"决策"）；`GuardChain` 保留为薄兼容门面，内部委托 DecisionEngine。**求值序/短路/降级语义逐行不变** |
| `core/tools_guard.py` — `_BUILTIN_IDS` / `build_builtin_chain` / `from_config` | — | `governance/policy.py` `PolicyEngine.from_config()` / `PolicyRegistry` | **原位提级** | 修 W1：装配层改用治理层工厂并注入 `validator`/`credential_paths`/`path_exists`/`link_resolver`/`approval_channel` |
| `core/tools_guard.py` — `register_guard_hook` / `match_guard_by_hook` / `_PLUGIN_HOOKS` | — | `governance/policy.py` `PolicyRegistry.register_rule()` | **原位提级** | 插件 guard 挂载 → 策略注册；**只增链尾的单调性保留** |
| `core/approval.py`（854 行，全套 TTL/合并/信任/防重放/绑定/身份白名单） | ✅ **整体保留在原文件** | 仅**实现** `governance/decision.py: ApprovalChannel` Protocol | **抽接口（不搬迁）** | ⚠️ **纠正提案示例"approval.py → governance/decision.py"**：见 §2.2-A |
| `core/tools_executor.py`（四关管道） | ✅ 保留原位 | — | **原位提级** | 见 §2.2-A 同款理由（强制点不搬家） |
| `core/tools_executor.py` — `_approval_binding`（sha1 绑定指纹） | — | `governance/receipt.py` `inputs_digest()` 算法来源 | **原位保留 + 治理层引用** | 算法已就绪，只把它变成凭证字段 |
| `core/approval.py` — `_grant_slots`（内存态绑定，无界增长） | — | `governance/receipt.py` `ReceiptStore` | **收敛/替换** | 修 B1-11：内存态 → 落盘凭证 + 查询 |
| `core/approval.py` — `_require_human`（通道身份白名单） | — | `governance/decision.py` `Principal` 的构造来源 | **原位保留 + 提级** | `by="cli:alice"` → `Principal(kind=human, channel=cli, id=alice)` |
| `core/session.py`（唯一逻辑写口 + reducer + 重放） | ✅ 保留原位 | — | **保留原位** | 治理层唯一写路径。**不动一行**（除 Phase 0 的 `_closed` 时序修复） |
| `pyharness/events/*`（envelope/vocab/payload，73 型 + 五步校验） | ✅ 保留原位 | — | **保留原位 + 只增** | 新增 4 个事件类型，遵守 `EVENT-SCHEMA.md:588` |
| `session` 事件流（guard.\*/approval.\*/tool.\*/segment.\*） | — | `governance/evidence.py` `EvidenceCollector`（**派生索引**） | **改为派生视图** | ⚠️ **纠正提案示例"session events → governance/evidence.py"**：见 §2.2-B |
| `core/telemetry.py` — `session_audit`（类型计数聚合） | ✅ 保留（向后兼容） | `governance/audit.py` `AuditSystem.causal_chain()` | **原位提级 + 适配** | `session_audit` 转为 `AuditSystem` 的兼容适配器 |
| `core/scope.py` — `ScopePolicy` / `can_use` / `tighten`（单调收紧） | ✅ 保留原位 | `ScopePolicy` 成为 `Policy` 的**会话绑定实例** | **原位提级** | 策略唯一数据源不变；新增"绑定到哪个 Policy 版本"的引用 |
| `core/scope.py` — `BudgetState` / `TaskUsage` 顶层 import `llm_fallback`（分层倒置） | — | `core/contracts.py`（中立类型模块） | **抽取** | 修 P-4，解治理层的反向依赖 |
| `core/llm_fallback.py` — `BudgetGuard.check` 抛 `PyHError("BUDGET-EXHAUSTED")` | — | 与 `scope.BudgetExhausted` 收敛为单一信号 | **收敛** | 修 W3/B1-2，否则"预算治理"读到两种终态 |
| `core/budget.py`（只读门面） | ✅ 保留原位 | 预算成为 Policy 的**资源域** | **原位提级** | `_compute_state` 与 `scope.budget_state` 收敛为单点判据 |
| `engine.py` — `build_runner_components`(185 行) | — | 拆为 `_build_runtime` / `_build_governance` / `_build_tools` / `_build_llm` / `_attach_persistence` | **重构（纯拆分）** | 解 P-2；治理装配进入 `_build_governance` 单一入口 |
| `core/agent.py` — `Ctx`（God Object，~33 属性） | ✅ 保留 | 新增 `ctx.governance` **单实例**，替代散字段 | **原位提级** | 解 P-1：治理内容不再逐个挂 ctx |
| `core/agent_loop.py` — `_must_stop` 三闸 / `RunResult` | ✅ 保留原位 | — | **保留原位** | 除 Phase 0 的 W2/W3/W4 修复外不动 |
| `core/task_queue.py` — `segment.start/end` 段锚 | ✅ 保留原位 | `EvidenceCollector` 以 segment 为**证据单元边界** | **原位提级** | 段锚已存在，只被治理层命名与引用 |
| `core/workflow.py`（顺序步骤，无 DAG） | ✅ 保留原位 | 每个 step 成为 governed task | **保留原位** | v1.0 不引入 DAG；治理粒度 = 步骤 |
| `persistence.py` / `repair.py` | ✅ 保留原位 | — | **保留原位**（收敛两套 repair） | 治理层只消费 `replay()` |
| `bus/registry.py` | ✅ 保留原位 | 新增 `governance` 索引类 | **只增** | 与 plugin/tool/capability 并列 |
| 5 处重复的 bus→store 落盘适配器 | — | 单一 `PersistenceSubscription`（`_SYNC` 从 `SYNC_TYPES` 派生） | **收敛** | 解 P-7；否则治理事件可能漏配强同步 |

### 2.2 明确"不做搬迁"的 4 处（含对提案示例的纠正）

#### A. `approval.py` **不**迁到 `governance/decision.py`

**提案示例**："`approval.py` → `governance/decision.py`"。
**设计结论：不搬迁，改为"抽接口 + 原位保留"。**

理由：
1. `approval.py` 是 **854 行的成熟模块**，含 TTL 定时器、60s 合并批、信任名单、防重放（APR-503）、跨会话隔离、身份白名单——这些是**传输与时序语义**，与"决策"是两件事。
2. 它已有完整测试面（`test_approval.py` + 76 个 guard 用例交叉覆盖）。搬迁会造成大面积回归，而收益仅是"目录好看"——违反 G-1。
3. 架构上它**本就不该是决策引擎**：审批是"决策的一个输入/通道"，不是决策本身。正确关系是 `DecisionEngine` **依赖** `ApprovalChannel` 接口，而 `approval.py` 恰好实现它。

**正确做法**：在 `governance/decision.py` 定义 `ApprovalChannel` Protocol（3 个方法），`approval.py` 保持原位并满足该协议（鸭子类型，无需 import 治理层）。

#### B. `session events` **不**迁到 `governance/evidence.py`

**提案示例**："`session events` → `governance/evidence.py`"。
**设计结论：事件留在原处；`evidence.py` 是其上的派生索引。**

理由：
1. 事件日志是 **INV-01 的唯一真源**，`MAP.md:57-60` 与 ADR-001 都锁定。把它"迁移"到证据层，等于**制造第二真源**——直接违反 G-2 与 INV-01。
2. 证据的语义是"**支持某个 claim 的引用**"，不是"事件本身"。证据层应当**引用** `seq`/`decision_id`/`segment`，而非复制内容。
3. 若把事件搬到 evidence，`session.replay`、`session_query`（FTS）、`telemetry`、所有 `events_after` 消费者全部要改道——这是重写，不是收编。

**正确做法**：`evidence.py` 定义 `Evidence`（claim + 引用 + 摘要）与 `EvidenceCollector`（**只读**订阅事件生成索引）；原文仍在 JSONL。

#### C. `tools_guard.py` 判定逻辑**不**搬迁

同上：`g1–g7` 与路径几何是**规则实现**，被 76 个测试钉死、被插件 hook 引用。v1.0 只在治理层**引用**它们（`PolicyEngine` 把它们注册为规则），物理搬迁留到 v1.1 并单独走一次 CND-07（重构/架构变更）。

#### D. `tools_executor.py` **不**搬迁

四关管道是**唯一强制点**（G-4）。把它移入治理层，就等于让治理层获得执行编排权——违反 §0.2 红线。

### 2.3 新增事件类型清单（词表 73 → 77，只增不改）

| 事件 | 载荷字段（草案） | 强同步 | 时机 | 依据 |
|---|---|---|---|---|
| `policy.updated` | `policy_id` / `version` / `fingerprint` / `op(add\|tighten\|disable)` / `added` / `reason` | **是** | 策略装配 / 会话级收紧 | 替代 `scope.updated` 的策略语义面（`scope.updated` 保留兼容） |
| `decision.issued` | `decision_id` / `tool` / `verdict` / `policy_refs` / `guard_ids` / `principal` / `inputs_digest` | **是** | 每次治理求值（含 allow） | 承载 INV-G1；与 `guard.evaluated` 并存（后者保留为规则级细粒度审计） |
| `receipt.emitted` | `receipt_id` / `decision_id` / `kind(approval\|decision)` / `digest` / `prev_hash` | **是** | 凭证生成 | 承载 INV-G2/G3 |
| `evidence.archived` | `evidence_id` / `claim` / `refs(seq\|decision_id\|segment)` / `artifact_path` | 否 | 证据归档 | 承载 INV-G4 |

> **载荷校验约束提醒**：`events/payload.py` 全部 `extra="forbid"`。若某字段无法入载荷模型，须经 `Envelope.trace` 携带（既有先例：`tools_guard.py:36-39` 的 `call_id`、`approval.py:36-40` 的 `channel`）。设计时**优先扩 payload + 升词表版本**（语义显式），仅当该字段属"传输元数据"时才走 trace。

---

## 3. 核心接口设计

> **本节只定义接口契约（签名 + 语义），不含任何实现。**
> 全部以 `Protocol` / `ABC` / `@dataclass` 声明形式给出；`...` 表示"实现留待编码阶段"。

### 3.1 主体（Principal）

```python
# governance/decision.py
class PrincipalKind(StrEnum):
    HUMAN = "human"      # cli / web / acp / desktop 通道
    AGENT = "agent"      # 会话内的 agent（LLM 意图）
    TOOL = "tool"        # 工具自身（无自主意志的代理者）
    PLUGIN = "plugin"    # 插件
    SYSTEM = "system"    # 框架内部（超时、清理、恢复）

@dataclass(frozen=True)
class Principal:
    """决策主体。取代现有 `by: str` 自由字符串（approval.py:741 的通道白名单是其雏形）。"""
    kind: PrincipalKind
    id: str                 # "alice" / "s-abc123" / "c1" / "plugin:hello_time"
    channel: str | None     # cli / web / acp / desktop / None
    # 不变量：kind=HUMAN 时 channel 必须 ∈ CHANNELS，且 id 不得以 "llm:"/"tool:" 开头
    #        （延续 S-2 假冒审批结构防线，approval.py:747）

    @classmethod
    def from_legacy_by(cls, by: str) -> "Principal": ...
    def to_legacy_by(self) -> str: ...     # 兼容既有 approval.* 事件的 by 字段
```

### 3.2 Policy Engine

```python
# governance/policy.py
@dataclass(frozen=True)
class PolicyRule:
    """单条策略规则。`check` 即现有 g1–g7 的 check 函数（复用，不重写）。"""
    rule_id: str                    # "g-fs-path" / "g-danger" / 插件 rule id
    policy_refs: tuple[str, ...]    # ("POL-FS-1","POL-FS-2","POL-FS-3")
    match: Callable[[ToolCall], bool]
    check: Callable[[ToolCall, Scope], Awaitable[tuple[str, str | None]]]
    forced: bool = False            # 恒在不可关（对应 FORCED_GUARDS）

@dataclass(frozen=True)
class Policy:
    """策略集：一等对象。取代"规则散落在 guard 函数里 + chain_version 内存计数"。"""
    policy_id: str                  # "builtin:v1" / "tenant:acme:v3"
    version: str                    # 语义版本
    fingerprint: str                # ★ 内容哈希（规则 id + 求值序 + policy_refs + 参数）
    rules: tuple[PolicyRule, ...]   # 求值序 = 元组序（对齐 _BUILTIN_IDS）
    scope_selector: dict            # 适用面（会话/租户/角色）

    def with_tightened(self, added: Iterable[str], reason: str) -> "Policy": ...
    # 不变量：无 relax/un_tighten（延续 scope.tighten 单调性，G-5）

class PolicyEngine(Protocol):
    """策略的装配、解析、版本治理。**只提供策略，不做决策。**"""

    def from_config(self, cfg: Any, *, session: Any, bus: Any,
                    validator: Callable | None,
                    credential_paths: Iterable[str] | None,
                    path_exists: Callable | None,
                    link_resolver: Callable | None,
                    approval_channel: bool | None) -> "PolicyEngine": ...
    # ↑ 修 W1：装配层必须经此入口（engine.py 改用之），使 g1 的 validator 真正注入

    def resolve(self, call: ToolCall, scope: Scope) -> Policy: ...
    """返回命中的 Policy（含求值序与 policy_refs）。纯只读。"""

    def current(self, scope: Scope) -> Policy: ...
    """会话当前绑定的策略（含 tighten 后的累积 deny）。"""

    def fingerprint(self, policy: Policy) -> str: ...
    """策略指纹（内容哈希）。用于审批重入复核与凭证引用。"""

    def registry(self) -> "PolicyRegistry": ...

class PolicyRegistry(Protocol):
    """规则注册表。插件规则只增链尾（延续 register_plugin_guard 单调性）。"""
    def register_rule(self, rule: PolicyRule) -> None: ...   # 重名 → TLB-801
    def rules(self) -> tuple[PolicyRule, ...]: ...
    def emit_updated(self, policy: Policy, op: str, reason: str) -> Awaitable[None]: ...
```

### 3.3 Decision Engine

```python
# governance/decision.py
class Verdict(StrEnum):
    ALLOW = "allow"
    REJECT = "reject"
    APPROVAL = "approval"
    # 兼容：Verdict.REJECT == "reject" 成立 → tools_executor.py:449 的 `d == "reject"` 无需改

@dataclass(frozen=True)
class Decision:
    """★ 治理层的核心产物：一次有主体、有依据、有 ID、可引用的决策。

    取代现有 Decision 三值枚举（tools_guard.py:127）——枚举是"值"，本对象是"事件"。
    """
    decision_id: str                    # 主键（uuid；凭证与审计的锚点）
    verdict: Verdict
    tool: str
    policy_refs: tuple[str, ...]        # ("POL-OVW-1",) —— 已有语义
    guard_ids: tuple[str, ...]          # 命中的规则 id
    policy_fingerprint: str             # ★ 依据的是哪一版策略
    inputs_digest: str                  # ★ sha1(tool + canonical_json(args))（= 现有 binding 算法）
    principal: Principal                # ★ 谁在决定
    ts: str                             # ISO8601 UTC
    approval_ref: int | None = None     # approval_id（= approval.requested 的 seq）
    receipt_id: str | None = None       # 若已出凭证

    # 兼容面（保证 S1 接缝零回归）
    def __eq__(self, other: object) -> bool: ...      # 与 str/StrEnum 比较成立
    def __str__(self) -> str: ...                     # 返回 verdict 字面量
    @property
    def is_terminal_reject(self) -> bool: ...

class DecisionEngine(Protocol):
    """决策编排：Policy.resolve → waterfall 求值 → HITL → 产出 Decision。

    **复用** GuardChain.evaluate 的 waterfall/短路/降级两闸语义（逐行等价），
    产出物由枚举升格为 Decision。
    """

    def __init__(self, policy_engine: PolicyEngine,
                 approval: "ApprovalChannel",
                 receipts: "ReceiptStore",
                 session: Any, bus: Any) -> None: ...

    async def evaluate(self, call: ToolCall, scope: Scope,
                       principal: Principal) -> Decision: ...
    """完整求值。不变量：
       - 每次求值恰好 append 一条 decision.issued（sync=True）—— INV-G1
       - 首个非 allow 即短路（waterfall，单调）
       - critical → reject（不可审批）；无通道 → reject(APR-501)
       - verdict != ALLOW 时同时落 guard.rejected(sync=True)（INV-05 保留）
    """

    async def reauthorize(self, call: ToolCall, scope: Scope,
                          principal: Principal,
                          prior: Decision) -> Decision: ...
    """审批 granted 后的重入求值（单调性高于人类意志，GRD-403 语义）。
       不变量：verdict=reject → 批准作废；approval → 需 inputs_digest 与凭证一致。
    """

    def explain(self, decision_id: str) -> dict: ...
    """只读：还原"谁/何时/因何/依据哪版策略/结果如何"。供 AuditSystem 与外壳。"""

class ApprovalChannel(Protocol):
    """HITL 通道契约。**由 core/approval.py 原位实现（鸭子类型，不 import 治理层）。**"""

    async def request(self, call: ToolCall, args_summary: str, ctx: Any, *,
                      binding: str | None = None) -> str: ...
    """返回 "granted" / "denied" / "timeout"。无通道 → 抛 APR-501。"""

    def grant_binding(self, call_id: str) -> str | None: ...
    """已批准调用的 inputs_digest（重入校验面）。"""

    async def cancel_all(self, reason: str) -> None: ...
```

### 3.4 Decision Receipt

```python
# governance/receipt.py
@dataclass(frozen=True)
class DecisionReceipt:
    """★ 可离线核验的裁决凭证。

    与现有最接近物（approval._grant_slots，内存态、无界、不可核验）的关键差异：
    ① 持久化（以 receipt.emitted 事件落真源）② 内容哈希（防篡改）
    ③ 前序哈希链（防挪移/删除）④ verify() 可离线运行
    """
    receipt_id: str
    decision_id: str
    kind: Literal["decision", "approval"]
    verdict: Verdict
    tool: str
    inputs_digest: str                  # 授权与执行绑定的核心（防"批准 A 执行 B"）
    principal: Principal
    policy_fingerprint: str
    approval_ref: int | None
    ts: str
    content_hash: str                   # H(all fields above)
    prev_hash: str | None               # 前一条 receipt 的 content_hash（链）
    signature: str | None = None        # v1.0 留空；v1.1 可接密钥签名

class ReceiptStore(Protocol):
    """凭证的生成、持久化、查询、核验。"""

    async def emit(self, decision: Decision, *,
                   approval_verdict: str | None = None) -> DecisionReceipt: ...
    """生成凭证 → append("receipt.emitted", sync=True) → 返回。
       不变量 INV-G2：每个 approval.granted 必对应一条 receipt。
    """

    def get(self, receipt_id: str) -> DecisionReceipt | None: ...
    def of_decision(self, decision_id: str) -> tuple[DecisionReceipt, ...]: ...

    def verify(self, receipt: DecisionReceipt) -> bool: ...
    """★ 离线核验：重算 content_hash + 校验 prev_hash 链。
       不变量 INV-G3：从事件真源重放后 verify() 结果不变（幂等）。
       fail-closed：verify() 返回 False ⇒ 调用方必须拒绝执行（沿用 INV-05 纪律）。
    """

    def digest_of(self, call: ToolCall, args: dict) -> str: ...
    """inputs_digest 算法（= 现有 tools_executor._approval_binding:218，复用而非重写）。"""
```

### 3.5 Evidence System

```python
# governance/evidence.py
@dataclass(frozen=True)
class Evidence:
    """★ 证据工件：支持某个 claim 的**引用集合** + 摘要。
    **只存引用，不复制事件内容**（INV-G4 —— 否则制造第二真源）。"""
    evidence_id: str
    claim: str                          # 被支持的断言（对齐 PROTOCOL §10 的 Claim）
    refs: tuple[EvidenceRef, ...]       # 指向真源的引用
    summary: str                        # 脱敏摘要（公开仓库纪律）
    artifact_path: str | None          # 归档路径（.ai-coding/evidence/…）
    ts: str

@dataclass(frozen=True)
class EvidenceRef:
    kind: Literal["seq", "decision_id", "receipt_id", "segment", "test"]
    locator: str                        # "s-abc:412" / "dec-…" / "t-3:seg" / "tests/…::test_x"

class EvidenceCollector(Protocol):
    """从事件流**只读**派生证据与追溯索引。"""

    async def on_event(self, env: Envelope) -> None: ...
    """订阅 decision.issued / receipt.emitted / segment.start|end / tool.result。
       不变量：只读；除 evidence.archived 外不写任何事件。"""

    async def archive(self, claim: str, refs: Iterable[EvidenceRef]) -> Evidence: ...
    """归档 → append("evidence.archived")。"""

    def collect_for_task(self, task_id: str) -> tuple[Evidence, ...]: ...
    """按任务/段聚合证据（段锚是天然边界，task_queue.py:404-429）。"""

class TraceabilityMatrix(Protocol):
    """★ 运行时追溯矩阵：F 编号 ↔ 模块 ↔ 测试 ↔ 事件。**生成而非手写**（IMPACT-MATRIX 的教训）。"""

    def build(self) -> dict: ...
    def check_consistency(self) -> list[str]: ...
    """返回文档与运行时真值的差异清单（可自动抓出词表数字打架、
       子命令数不符等脱节）。空列表 = 一致。"""
```

### 3.6 Audit System

```python
# governance/audit.py
class AuditSystem(Protocol):
    """治理审计视图。**是派生视图，不是新真源。**"""

    def causal_chain(self, decision_id: str) -> dict: ...
    """★ 因果链：请求(tool.call) → 策略(policy) → 决策(decision.issued)
       → 审批(approval.*) → 凭证(receipt.emitted) → 执行(tool.result) → 结果。
       替代 telemetry.session_audit 的"类型计数"（telemetry.py:30）。
    """

    def denied_report(self, *, session_id: str, since_seq: int = 0) -> list[dict]: ...
    """所有被拒调用及其依据（审计可证"拦了且没执行"，INV-05）。"""

    def reconcile(self) -> list[str]: ...
    """对账：SEQ-GAP / CACHE-STALE / NO-GUARD-EVENT / NO-RECEIPT-FOR-GRANT。
       结论经 syscheck.fail 落事件（73 型中已存在，当前无调度点）。"""

    def legacy_session_audit(self, session: Any) -> dict: ...
    """兼容适配：转调 telemetry.session_audit，保证既有调用点不破。"""
```

### 3.7 治理上下文（单一挂载点）

```python
# governance/context.py
@dataclass
class GovernanceContext:
    """★ ctx.governance 单实例。避免治理内容继续向 Ctx 追加散字段（解 P-1）。"""
    policy: PolicyEngine
    decisions: DecisionEngine
    receipts: ReceiptStore
    evidence: EvidenceCollector
    audit: AuditSystem

    async def authorize(self, call: ToolCall, ctx: Any) -> Decision: ...
    """tool_executor 关 2 的唯一调用入口（S1 接缝）。"""

    def principal_of(self, ctx: Any) -> Principal: ...
    """从外壳注入的通道身份派生 Principal（不信任客户端自报）。"""
```

### 3.8 治理不变量（新增，须有专项测试）

| ID | 不变量 | 检测方式 |
|---|---|---|
| **INV-G1** | 任何工具执行前必有同 `call_id` 的 `decision.issued` | 事件序校验（对齐 INV-04） |
| **INV-G2** | 每条 `approval.granted` 必对应一条 `receipt.emitted` | 事件计数配对 |
| **INV-G3** | `receipt.verify()` 在真源重放后结果不变 | 重放幂等测试 |
| **INV-G4** | 治理层不存第二真源：`evidence`/`receipt` 只含引用与哈希 | 结构断言（无 payload 全文副本） |
| **INV-G5** | 治理层无执行/放行 API（延续 `_FORBIDDEN_API`） | `hasattr` 断言 AttributeError |
| **INV-G6** | 策略单调：`fingerprint` 变化只体现为"新增规则/收紧"，无删除 | 策略版本 diff 断言 |

---

## 4. v1.0 最小可交付范围

> **目标定位**：让 PyHarness 具备"**决策可追、凭证可验、越权必拦**"的治理闭环——即从 Agent Framework 变为 Governed Agent Runtime。**不追求完整治理平台。**

### 4.1 必做（Must — v1.0 的定义）

| # | 交付项 | 验收判据 | 依赖 |
|---|---|---|---|
| M1 | **Phase 0 接线收口**（W1–W5） | g1 validator 真注入；`from_config` 成为唯一装配入口；预算单信号；F026 在主链生效；关闭可重入 | 无（前置） |
| M2 | **Policy 一等对象 + 内容哈希指纹** | `PolicyEngine.fingerprint(policy)` 随规则内容变化；`policy.updated` 落盘 | M1 |
| M3 | **Decision 对象**（decision_id / principal / policy_refs / inputs_digest / policy_fingerprint） | 每次工具调用产生 `decision.issued`；`Decision.__eq__("reject")` 为真（零回归） | M2 |
| M4 | **DecisionReceipt：emit + verify + 落盘** | 篡改任一字段 → `verify()` False；重启会话后 `verify()` 仍成立 | M3 |
| M5 | **Principal 取代裸字符串 by** | 审批身份白名单逻辑保留；`Principal ⇄ by` 双向往返无损 | M3 |
| M6 | **EvidenceCollector：段锚证据聚合 + 只存引用** | 按 `task_id` 可聚合一条完整证据链；无 payload 全文副本（INV-G4） | M3 |
| M7 | **AuditSystem.causal_chain：一条命令还原一次拒绝** | 给定 `decision_id` 还原"谁/何时/因何/依据哪版策略/结果" | M3+M4 |
| M8 | **治理不变量测试 INV-G1..G6** | `tests/invariants/` 全绿且覆盖 | M3+M4+M6 |
| M9 | **激活空置测试面**：`tests/acceptance/` + `tests/security/` | 至少各 1 条真实用例（含"批准 A 执行 B"必须被拒） | M4 |
| M10 | **端到端治理 Demo**（走真实外壳，无 mock 通道） | 场景：策略拒绝 / 审批闭环 / 崩溃恢复后凭证仍可验 / 篡改凭证必拒 | M1–M9 |

### 4.2 明确不做（Out of Scope — 防范围蔓延）

| 不做 | 为什么 | 何时做 |
|---|---|---|
| **策略外部化为 YAML/可插拔插件** | v1.0 策略来源仍是 `config.security.*` + 内置规则；外部化是 v1.1 | v1.1 |
| **Workflow DAG 引擎** | 治理粒度已下移到步骤（segment），无 DAG 也能治理；引入 DAG 是编排能力而非治理能力 | v1.2（若需要） |
| **Checkpoint / 快照回滚** | 这是**运行时**能力（非治理能力），且恢复成本问题需独立设计；v1.0 用"replay + receipt 重放核验"承担恢复一致性 | v1.1 |
| **`paused/resume` 完整运行时暂停** | 需先做二选一决策（实现 or 删除声明）。v1.0 仅要求：**不得留死代码**（实现或删除皆可） | Phase 0 决策 |
| **数字签名 / 密钥治理** | v1.0 用内容哈希 + 前序哈希链（无外部依赖）；签名需密钥体系 | v1.1 |
| **多租户治理策略** | 租户隔离本身尚需修（B1-10），治理层不叠加 | v1.1 |
| **g1–g7 物理搬迁到 governance/** | 保留原位（§2.2-C）；搬迁单独走 CND-07 | v1.1 |
| **`approval.py` 搬迁** | 保持原位（§2.2-A） | 永不做 |

### 4.3 v1.0 完成定义（Definition of Done）

> **一句话**：**任意一次工具调用，都能回答"谁在什么策略下做了什么决定、凭什么、有无凭证、能否独立核验"，并且这套回答本身是防篡改的。**

逐条判据：

1. **可追**：`decision_id` 贯穿 `tool.call → decision.issued → (approval.*) → receipt.emitted → tool.result`，无断链（INV-G1）。
2. **可验**：`receipt.verify()` 离线可跑；篡改任一字段返回 False；从事件重放后结果不变（INV-G3）。
3. **必拦**：critical 工具 reject 且审计可证"拦了且没执行"（INV-05 保留）；"批准 A 执行 B"被拒。
4. **无第二真源**：`evidence`/`receipt` 只含引用与哈希（INV-G4）。
5. **无回归**：既有 1326+ 用例全绿；**没有任何测试为"适配新形态"而放松安全断言**。
6. **无死层**：治理层无执行 API（INV-G5）；无 `paused` 之类的死状态。
7. **文档同步**：`MAP.md`/`ADD.md`(新 ADR)/`EVENT-SCHEMA.md`(词表 77 型) 与代码一致，且 `TraceabilityMatrix.check_consistency()` 返回空列表。

### 4.4 交付物清单

| 类别 | 交付物 |
|---|---|
| 新增模块 | `pyharness/governance/{__init__,context,policy,decision,receipt,evidence,audit}.py`（7 文件） |
| 新增事件 | `policy.updated` / `decision.issued` / `receipt.emitted` / `evidence.archived`（词表 73→77） |
| 改动模块 | `engine.py`(拆分+接线) · `tools_guard.py`(出口升格) · `tools_executor.py`(关2 消费 Decision) · `agent.py`(ctx.governance) · `session.py`(时序修复) · `agent_loop.py`(W2/W3/W4) · `scope.py`(解倒置) · `approval.py`(仅 _grant_slots→凭证) · `telemetry.py`(适配) · `desktop/app.py`(租户正则) |
| 新增测试 | `tests/acceptance/` · `tests/security/` · `tests/invariants/`(INV-G1..G6) · `tests/unit/test_governance_*.py` |
| 文档 | `GOVERNED_AGENT_RUNTIME_DESIGN.md`(本文) · 新 ADR-013+ · `MAP.md`/`EVENT-SCHEMA.md` 刷新 |
| 演示 | `scripts/probe_governance_e2e.py`（对齐既有 `probe_*.py` 惯例） |

**工作量粗估**（供排期参考，非承诺）：M1 最重（装配单点 + 4 处语义修复）；M3 风险最高（动安全主干，须全量回归）；M4–M7 相对独立可并行；M8–M10 收尾。

---

## 5. 风险、回退与与既有路线的映射

### 5.1 主要风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 关 2 出口形态变更导致工具路径回归 | **高** | `Decision` 实现 `__eq__`/`__str__` 兼容 `d == "reject"`；先建**等价性测试**（新对象 verdict 逐例等于旧枚举）；按 CORE-02 全量回归 |
| `decision.issued` 载荷扩字段触发 `extra="forbid"` EVT-100 | **高** | 新字段优先进 payload + **同步升词表版本**；纯传输元数据才走 `trace`（既有先例） |
| 治理层被写成"第二真源"（凭证/证据存全文） | 中高 | INV-G4 结构断言 + 代码评审红线；`evidence` 只存引用 |
| `receipt.verify()` 破坏可用性（fail-closed 误拒） | 中高 | 双写过渡期：旧路径与新凭证并行，等价测试通过后再切换 |
| 装配拆分触及所有外壳 | 中 | 先补 `engine` 装配验收断言（当前 `test_engine_flow.py` 仅 1 大用例） |
| 追溯矩阵腐化（手写即失效） | 中 | **生成式**（`TraceabilityMatrix.build()`）+ 漂移即测试红 |
| 暴露既有缺陷打断"全绿"节奏 | 中 | 记入 `KEY-FINDINGS.md`，按 P 级分阶段修，**不阻塞阶段门** |

### 5.2 回退策略

原则：**每一步都可独立回退，且回退不需改回数据。** 因为治理层的一切产物都是**事件**（只增不改），回退 = 停止订阅/停止消费，事件留在真源无害。

| 阶段 | 回退动作 |
|---|---|
| M1 接线收口 | 单点 revert（不涉数据格式） |
| M3 决策对象 | `Decision.__eq__` 兼容层保证旧代码可用；必要时 `ctx.governance.authorize` 退回直接调 `GuardChain.evaluate` |
| M4 凭证 | 关闭 `receipt.emitted` 写入，回退 `_grant_slots` 内存路径（事件保留，可后续补核验） |
| M6/M7 证据与审计 | 纯只读派生，停用即无影响 |

### 5.3 与 REFACTOR_PLAN Phase 的映射

| 本文交付项 | REFACTOR_PLAN Phase | 说明 |
|---|---|---|
| M1 | **Phase 0** 架构整理 | 前置，必须先完成 |
| M2 + M3 | **Phase 1 + Phase 2** | 策略对象 + 决策对象；Phase 2 触及安全主干，须独立交付 |
| M4 + M5 | **Phase 3** Decision Receipt | 凭证化 |
| M6 + M7 + M8 | **Phase 4** Evidence 与 Audit | 证据链 + 审计模型 + 不变量 |
| M9 + M10 | **Phase 5** 生产级 Demo | 验收与演示 |
| 4.2 中的 Out of Scope | — | 明确推迟：策略外部化 / DAG / Checkpoint / 签名 → v1.1+ |

> **v1.0 与 REFACTOR_PLAN 的差异**：REFACTOR_PLAN 的 Phase 2/3 停留在"做什么"；本文把它们收敛为**接口契约 + 最小范围 + DoD**，并明确划出**不做什么**——这是从"迁移方案"到"可执行 v1.0"的关键一步。

---

## 附：需要人工确认的决策点（不猜测）

1. **`paused/resume` 二选一**：实现真实暂停（新增并发面，触发 CND-01），还是删除三态死声明、承认 v1.0 不含运行时暂停？（Phase 0 前必须定）
2. **Receipt 完整性强度**：内容哈希 + 前序哈希链（v1.0 建议，零外部依赖）还是数字签名（需密钥治理，建议 v1.1）？
3. **`decision.issued` 与 `guard.evaluated` 的关系**：并存（前者"决策级"、后者"规则级"，审计更细但事件量翻倍）还是用前者取代后者（事件精简但破坏 INV-04 的既有语义与测试）？**建议并存，`guard.evaluated` 降级为"仅命中规则时记录"**。
4. **`workflow` 在 v1.0 的定位**：确认为"顺序步骤编排器（每步被治理）"，不引入 DAG？
5. **`policy.updated` 与既有 `scope.updated` 的关系**：`scope.updated` 保留（会话级收紧）还是由 `policy.updated` 统一承载？（涉及既有 `scope.note_tighten` 的兼容面）
