# S5-3_TECHNICAL_REVIEW.md — 治理审计视图（技术复核与归档）

> **审查对象**：S5-3 单次 checkpoint（`S5-3a` 审计模块 + `S5-3b` engine 装配）
> **审查日期**：2026-09-15 ｜ **审查性质**：**只读** · 未修改任何代码/测试/契约 · 未 push
> **适用读者**：后续接手治理层（Governance Layer）的工程人员
> **术语约定**：英文术语后附中文解释，形如 `AuditSystem（审计系统）`

---

## 1. S5-3 目标

### 1.1 为什么需要 `AuditSystem（审计系统）`

治理层已经能**产生**事实（`decision.issued`）与**证明**事实（`receipt.emitted`），也能**按任务**组织证据（`EvidenceCollector`）。但它还**不能回答两类问题**：

| 问题 | 例子 | 现状 |
|---|---|---|
| **因果问题** | "`decision_id=D-A` 这条决策，**为什么**发生？经过了**哪些环节**？依据**哪一版策略**？谁做的？**有没有**凭证？**执行了没有**？" | 无组件能一次回答;须人工跨 9 类事件手工拼 |
| **拒绝问题** | "本会话**哪些调用被拒了**？**依据什么**？能证明**拦了且没执行**吗？" | 无组件能产出该清单 |

`AuditSystem` 就是这两类问题的**统一入口**。

它不是**新真源**，也不是**新缓存** —— 它是**纯派生视图（derived view）**：每次提问都从事件日志**即时重算**。

### 1.2 S5-2 与 S5-3 的关系

| 阶段 | 交付 | 回答的问题 | 组织轴 |
|---|---|---|---|
| **S5-1** | `EvidenceRef（证据引用）` / `Evidence（证据工件）` / `archive()（归档入口）` | "证据**长什么样**、怎么**写入**" | — |
| **S5-2** | `on_event（事件消费）` / `collect_for_task（按任务聚合）` | "**这个任务**有哪些证据？" | **段轴（segment）** |
| **S5-3** | `causal_chain（因果链）` / `denied_report（拒绝报告）` | "**这条决策**为什么发生？" / "**哪些**被拒？" | **因果轴（causal）** |

**关键**：S5-3 **不建立在** S5-2 之上 —— 它**独立重放**日志，并与 `evidence.py` **零依赖**。

### 1.3 四者关系

```
Event Log（事件日志,唯一真源）
        │  只读重放 _read_events()
        ▼
AuditSystem（审计系统,纯派生视图,无状态）
        │  被持有
        ▼
GovernanceContext（治理上下文,单实例聚合）
        │  挂载于
        ▼
Engine（引擎脊柱,ctx.governance）
```

| 角色 | 中文解释 | 职责 |
|---|---|---|
| `Event Log` | 事件日志 | **唯一真源**;append-only JSONL |
| `AuditSystem` | 审计系统 | **只读派生**;每次提问即时重放;无状态、无订阅、无写点 |
| `GovernanceContext` | 治理上下文 | 治理层**唯一挂载面**（policy / decisions / receipts / evidence / audit） |
| `Engine` | 引擎脊柱 | **装配层**;只做"构造 → 注入"两步（**无订阅**） |

---

## 2. `AuditSystem` 架构

### 2.1 形态

`AuditSystem` 是一个 **frozen dataclass（冻结数据类）**，只持有一个字段：`session`。

```
AuditSystem（审计系统）
  ├─ session : 会话日志句柄（鸭子类型;可为 None ⇒ 降级）
  │
  ├─ _events(ctx)          → 即时全量重放（唯一数据来源）
  ├─ _trace_call_id(ev)    → 从 Envelope.trace 取 call_id
  ├─ causal_chain(id)      → 因果链字典（11 键）
  └─ denied_report(...)    → 被拒清单（9 键记录）
```

**无状态的含义**：两次调用之间**不保留任何东西**;同一次调用内部也不缓存中间结果。因此：

- 多实例可并存且结果一致;
- 重启后自动可用（不需要任何恢复逻辑）;
- 不存在"缓存与真源不一致"这一类 bug。

### 2.2 装配（S5-3b）

```python
# engine.py 内（与 EvidenceCollector 并列但**刻意不同**）
evidence = EvidenceCollector(session=log_)          # S5-2b:构造 + 订阅 5 类
audit    = AuditSystem(session=log_)                # S5-3b:只构造,**不订阅**
spine = EngineSpine(..., governance=GovernanceContext(
    policy=..., decisions=..., receipts=...,
    evidence=evidence, audit=audit), ...)
```

| 构件 | 订阅 | 原因 |
|---|---|---|
| `EvidenceCollector` | **5 类** | 需要**实时**累积索引以支持按任务聚合 |
| `AuditSystem` | **无** | 提问是**低频按需**的;重放即可,且必须重启可用 |

### 2.3 与 `GovernanceContext` / `Engine` 的关系

- `GovernanceContext.audit` 是**唯一挂载点**（与 policy / decisions / receipts / evidence 并列）;
- 装配后 `ctx.governance.audit is spine.governance.audit`（单实例，已由装配测试断言）;
- `EngineSpine` / `EngineContext` 的**结构未变** —— 只多了一个已存在的字段被填上实例。

---

## 3. `Event Log → AuditSystem` 数据流

```
Event Log（事件日志）
   │
   │  ① 只读重放  _read_events(session)
   │       · 优先 session.events_after(0)
   │       · 回落 session.replay()
   ▼
Replay Engine（重放引擎）
   │      ——**不是新组件**：即上面那个读取函数;每次调用即时执行
   ▼
AuditSystem（审计系统）
   │
   ├──► 按 decision_id 定位 ──► 按 call_id 串联 ──► 按 approval_ref 关联
   │        │                       │                      │
   │        │                       │                      └─► approval.* 结果
   │        │                       └─► tool.call / tool.result / guard.*
   │        └─► receipt.emitted（按 decision_id）
   ▼
Causal Chain（因果链,单条决策的完整链条）
   │
   ▼
Audit Report（审计报告,被拒调用清单 + 依据）
```

| 模块 | 中文含义 | 说明 |
|---|---|---|
| `Event Log` | 事件日志 | append-only JSONL;唯一真源 |
| `Replay Engine` | 重放引擎 | **非新组件**;即 `_read_events(session)`;每次调用即时全量重放 |
| `AuditSystem` | 审计系统 | 纯派生视图;无状态 |
| `Causal Chain` | 因果链 | 单条 `decision_id` 的"请求 → 策略 → 决策 → 审批 → 凭证 → 执行"字典 |
| `Audit Report` | 审计报告 | 被拒调用及依据清单 |

**数据流是单向的**:`Event Log → Replay → AuditSystem → 结果`;**不存在反向写回**。

---

## 4. replay-only 原因

### 4.1 含义

`replay-only（仅重放）` = 该组件**不使用任何实时订阅**，所有信息都在**提问那一刻**从事件日志重算。

### 4.2 为什么不订阅

| # | 理由 | 展开 |
|---|---|---|
| 1 | **重启可用性** | 订阅态索引在**重启后为空**（直到事件重新到达）;审计必须在**仅凭日志**的条件下工作 —— 事故复盘、离线核验都发生在重启之后 |
| 2 | **必须覆盖订阅面之外** | 因果链需要 `tool.result` / `guard.evaluated` / `guard.rejected` / `approval.*`;**这些不在** `EvidenceCollector` 的订阅面内（`Δ-1` 有意让执行结果维度归审计） |
| 3 | **零副作用** | 不新增订阅者 ⇒ 不产生 EVT-103 噪声、不与真源形成写竞争、无生命周期耦合 |
| 4 | **可复现** | 同一日志 ⇒ 同一结果;不依**订阅先后**、不依**事件到达时序** |
| 5 | **成本可接受** | 审计是**低频按需**问答（人 / CI 触发），非热路径;O(n) 重放可接受（`H-6` 已裁定接受） |

### 4.3 代价与其边界

**代价**：每次调用 O(n) 全量重放。

**边界（重要）**：若未来要优化，**唯一允许**的方向是加**一次性派生缓存**，且该缓存**必须**满足"删除后可由日志完全重建";**不得**引入任何**持久化**（那会造出第二真源，直接违反 `INV-G4 / INV-R5`）。

---

## 5. `causal_chain` 数据模型

### 5.1 输入 / 输出

| 项 | 内容 |
|---|---|
| **输入** | `decision_id: str`（必需）· `ctx`（可选，覆盖构造期注入的 session） |
| **输出** | `dict`（**11 键**，全部为可 JSON 序列化的基础类型 / `None`） |
| **责任** | 由 `decision_id` 还原完整因果链，每段带**真源 locator**（`seq`） |
| **不负责** | **不臆造**未命中环节;不写;不抛（未知 id ⇒ 全段 `None` + `missing` 非空） |

### 5.2 字段表

| 键 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `decision_id` | `str` | 入参 | 主键 |
| `call_id` | `str` | `decision.issued.trace.call_id` | 串联全部环节的**关键** |
| `request` | `{tool, seq}` \| `None` | `tool.call`（按 `call_id`） | 请求了什么;缺失 ⇒ 记 `missing` |
| `policy` | `{fingerprint, refs, guards}` | `decision.issued` | 依据哪版策略 / 哪些规则 / 哪些 policy_ref |
| `decision` | `{verdict, ts, seq, principal{kind,id,channel}}` | `decision.issued` | 结果 + 何时 + 谁 |
| `approval` | `{approval_id, outcome, by, seq}` \| `None` | `approval.granted/denied/timeout`（按 `approval_ref`） | 审批结果;`approval_ref` 为空 ⇒ 恒 `None`（D1） |
| `supersedes` | `str` \| `None` | `decision.issued.supersedes` | 前序 `decision_id`（D1 ← D2） |
| `receipt` | `{receipt_id, kind, digest, prev_hash, seq}` \| `None` | `receipt.emitted`（按 `decision_id`） | 有无凭证、凭证是什么 |
| `execution` | `{ok, seq}` \| `None` | `tool.result`（按 `call_id`） | 执行了没有、结果如何 |
| `denied` | `bool` | `verdict == "reject"` | 便捷判定 |
| `missing` | `list[str]` | 派生 | **显式**列出"应有而未找到"的段名 |

### 5.3 缺失语义（本设计的核心纪律）

```
decision.issued 缺失          ⇒ 立即返回,missing = ["decision.issued"]（不抛）
tool.call 缺失（有 call_id）  ⇒ request = None,missing += "tool.call"
approval_ref 非空但无结果事件 ⇒ approval = None,missing += "approval"
应有凭证而未找到             ⇒ receipt = None,missing += "receipt.emitted"
execution 缺失                ⇒ execution = None,**不入 missing**（被拒的唯一合法态）
```

**"应有凭证"的判定规则**：与 `receipt.receipt_kind_of` **同一规则** —— `verdict == "approval"` **且** `approval_ref is None`（即 D1，审批请求决策）⇒ **不产生**凭证。该一致性由测试**逐例守卫**（防止两处规则漂移）。

**`execution` 为何不入 `missing`**：`tool.result` 的**缺席**本身就是"未执行"这一事实（对 REJECT 而言是**正常**结果，而非缺失）。若把它当缺失，会产生大量假告警。

---

## 6. `denied_report` 数据模型

### 6.1 输入 / 输出

| 项 | 内容 |
|---|---|
| **输入** | `session_id: str = ""`（空 = 不过滤）· `since_seq: int = 0`（增量下界）· `ctx`（可选） |
| **输出** | `list[dict]`（**9 键**记录），按事件 `seq` **升序** |
| **责任** | 列出**所有被拒调用及其依据**，使审计可证"**拦了且没执行**"（`INV-05`） |
| **不负责** | 不改判定;不写;不产生新事件 |

### 6.2 字段表

| 键 | 类型 | 说明 |
|---|---|---|
| `source` | `"guard.rejected"` \| `"decision.issued"` | ★ **两类来源必须可区分** |
| `seq` | `int` | 该拒绝事件的序号（也是排序键） |
| `tool` | `str` | 工具名 |
| `call_id` | `str` | 经 `Envelope.trace` 取;用于与 `tool.result` 配对 |
| `guard_id` | `str` | 规则级来源专有（`decision.issued` 来源为空串） |
| `policy_ref` | `str` | 规则级来源专有 |
| `decision_id` | `str` | 治理级来源专有 |
| `reason` | `str` | 规则级取 `reason`;治理级取 `policy_refs` 拼接 |
| `executed` | `bool` | 该 `call_id` **是否存在**配对 `tool.result` |

### 6.3 两类来源的语义

| 来源 | 产生者 | 语义 | 层级 |
|---|---|---|---|
| `guard.rejected` | `tools_guard._append_rejected`（**强同步**） | 某条规则拒绝了本次调用，依据 `policy_ref` | **规则级**（`INV-05` 的留痕） |
| `decision.issued`（`verdict=reject`） | `GovernanceContext.authorize`（**强同步**） | 治理层给出的**决策级**结论 | **治理级**（`INV-G1` 的载体） |

**为什么必须区分**：二者回答不同粒度的审计问题 —— "**哪条规则**拦的"（规则级）与"**治理层**作出了什么决定"（治理级）。混为一条会丢失规则级归因能力（这正是 `ADR-015` 双轨事件的设计初衷）。

**`executed` 的作用**：它把"拒绝"与"是否真的没有执行"关联起来。对每条拒绝记录，`executed == False` 即构成 `INV-05` 所需的**可证事实**（"拦了且没执行"）。

---

## 7. 与 `EvidenceCollector` 对比

| 维度 | `EvidenceCollector（证据收集器）` | `AuditSystem（审计系统）` |
|---|---|---|
| **组织轴** | **段轴（task / segment）** | **因果轴（call_id / decision_id / approval_id / receipt_id）** |
| **典型提问** | "**这个任务**有哪些证据？" | "**这条决策**为什么发生、经过了什么？" |
| **订阅** | ✅ 5 类（`evidence.archived` / `segment.*` / `decision.issued` / `receipt.emitted`） | ❌ **无** |
| **状态** | 内存索引（**缓存**，可由 `from_log` 重建） | **无状态** |
| **写点** | `archive()` → `evidence.archived`（唯一写点） | **无**（`reconcile` 属 S5-4） |
| **事件覆盖** | 5 类 | 9 类（含 `tool.*` / `guard.*` / `approval.*`） |
| **数据形态** | `Evidence`（工件 + 引用） | **原始事件事实** |
| **覆盖范围** | 仅**被显式归档**的证据 | **全部**决策（含从未归档者） |
| **不变量** | `INV-E1~E4` | `INV-A1~A3` |

### 7.1 为什么审计**不能**复用证据索引

| # | 理由 |
|---|---|
| 1 | **事件面不足** —— 证据索引只覆盖 5 类;**没有** `tool.result` / `guard.*` / `approval.*`，因果链的关键环节缺失 |
| 2 | **数据形态不同** —— 它存 `Evidence` 工件;因果链需要原始事实（`verdict` / `principal` / `digest` / `by`…） |
| 3 | **组织轴不同** —— 它按**段**聚合;审计按**因果**串联，跨段、跨调用都可能 |
| 4 | **覆盖不全** —— 只覆盖**被归档**的证据;审计须覆盖**全部**决策 |
| 5 | **耦合风险** —— 若审计依赖 S5-2 的内存索引，该索引会**事实上**升级为"审计的前提"，逼近**第二真源**，威胁 `INV-R5 / INV-E3` |

**结论**：二者**职责正交、实现独立**。共同点仅在于**读法一致**（都用 `_read_events` 的鸭子类型只读重放），但**不共享状态**。

---

## 8. 测试设计说明

| # | 测试 | 保护什么风险 |
|---|---|---|
| **T-1** 因果链完整性（四场景） | ALLOW / REJECT / D1 / D2 **逐段**断言（`request` / `policy` / `decision` / `approval` / `supersedes` / `receipt` / `execution`） | **漏段 / 错段**：任何环节未被正确串联即失败 |
| **T-2** 审批关联 | D2 的 `approval.approval_id == decision.issued.approval_ref == granted.approval_id`;D1 ⇒ `approval is None` | **审批链断裂**：`approval_ref` 与结果事件对不上 |
| **T-3** 凭证关联 | `receipt.digest` 来自该 `decision_id` 的 `receipt.emitted`;D1 ⇒ `receipt is None` | **凭证错挂**：凭证挂到别的决策上 |
| **T-4** `denied_report` 两类来源 | `source` 必须出现两类;`guard_id` / `policy_ref` 与 `decision_id` 各守其域;`executed == False` | **混淆/漏类**（`INV-A3`）与 **"拦了却执行了"** 的漏检 |
| **T-5** replay 一致性 | 同日志两次调用结果相等;全新实例（**无订阅历史**）结果相同 | **隐性状态依赖**：若实现偷用缓存/订阅，全新实例会得出不同结果 |
| **T-6** 零副作用 | 调用前后**事件数不变** + AST 断言无**会话对象** `append` | **观察者效应**：只读组件若写事件会污染真源（`INV-A2`） |
| **T-7** 缺失语义 | 未知 `decision_id` ⇒ `missing == ["decision.issued"]` 且各段 `None`;应有凭证而无 ⇒ 显式登记 | **臆造**（`INV-A1`）：返回"看起来正常"的链是最危险的失败模式 |
| **T-8** 边界 | `session=None` ⇒ 降级不抛;`since_seq` 增量过滤;`session_id` 过滤 | **未定义输入**导致调用方必须处理异常 |
| **T-9** 依赖边界（AST） | `audit.py` 无 `core`/`persistence`/`bus`/`engine` import;**无 `subscribe`** | **依赖反向污染** + **违反 replay-only** |
| **T-10** 装配 | `ctx.governance.audit` 与 `spine.governance.audit` **同一实例**;**订阅 owner 中无 audit 相关项** | **装配错误 / 悄悄新增订阅** |
| **规则一致性** | `_receipt_expected` 与 `receipt.receipt_kind_of` 逐例一致 | **规则漂移**：两处"是否应有凭证"的判定分叉 |

**测试事实**：S5-3 专项 **19 passed** · invariants **16 passed** · 全量 **1655 passed / 2 skipped / 0 failed**。

---

## 9. 风险复盘

### 9.1 S5-3 解决了什么问题

| # | 问题 | 解法 |
|---|---|---|
| 1 | **无法回答"这条决策为什么发生"** | `causal_chain` 一次返回 9 类事件串联的完整链条 |
| 2 | **无法产出"被拒清单"** | `denied_report` + `source` 区分两类来源 + `executed` 佐证 |
| 3 | **无法证明"拦了且没执行"**（`INV-05`） | `executed` = 是否存在配对 `tool.result` |
| 4 | **重启后审计不可用** | replay-only ⇒ 仅凭日志即可工作 |
| 5 | **跨引用类型无法串联**（`decision_id` / `call_id` / `approval_id` / `receipt_id`） | 四类键各由明确来源解析（见 §5.2 / §6.2） |
| 6 | **缺失被静默** | `missing` 显式列出;未知 id 不抛不臆造（`INV-A1`） |
| 7 | **审计可能污染真源** | 无写点 + 零副作用双重断言（`INV-A2`） |

### 9.2 S5-3 还没有解决什么

| # | 未解决问题 | 归属 |
|---|---|---|
| 1 | **对账（`reconcile`）** —— 无法自检 `SEQ-GAP` / `CACHE-STALE` / `NO-GUARD-EVENT` / `NO-RECEIPT-FOR-GRANT` | **S5-4** |
| 2 | **旧审计面兼容（`legacy_session_audit`）** —— `telemetry.session_audit` 的既有调用点尚无治理层适配入口 | **S5-4**（须经**注入**，不得 import `core.telemetry`） |
| 3 | **追溯矩阵（`TraceabilityMatrix`）** —— F 编号 ↔ 模块 ↔ 测试 ↔ 事件的生成式矩阵 | **S7**（冻结计划 §5.2） |
| 4 | **性能（O(n) 重放）** | `deferred debt`（裁定接受，不引入持久化缓存） |
| 5 | **`denied_report.reason` 的展示口径**（治理级来源为 `policy_refs` 拼接） | `deferred debt`（如需结构化应改列表，本次按冻结的扁平 9 键实现） |
| 6 | **`cause` 的三层细粒度**（`guard.evaluated` 的逐规则参与情况**未纳入**因果链的独立段） | 现由 `policy.guards` 承载（命中面）;完整规则级明细仍需直接读 `guard.evaluated` |

### 9.3 为什么 S5-4 仍然需要

**核心差异**：**S5-3 是"查询能力的构建"（view），S5-4 是"一致性的自我检验"（integrity check）+ 旧面兼容。**

| 项 | S5-3 现状 | S5-4 待建 |
|---|---|---|
| **提问方式** | "给我这条决策的链条"（**被动查询**） | "**系统自己**是否一致？"（**主动对账**） |
| **写点** | **无** | `reconcile()` 的结论经**既有** `syscheck.fail` 落事件（**本阶段唯一写点**） |
| **判定** | 只呈现事实，不判定对错 | **判定** 四类差异（`SEQ-GAP` / `CACHE-STALE` / `NO-GUARD-EVENT` / `NO-RECEIPT-FOR-GRANT`） |
| **旧面兼容** | 无 | `legacy_session_audit()` → **注入** `telemetry.session_audit`（不得 import `core`） |

**关键点**：`reconcile` 的 `NO-RECEIPT-FOR-GRANT` 是 `INV-G2` 的**运行时哨兵** —— 把"每条 `approval.granted` 必对应一条凭证"从**静态不变量**变成**可运行时自检的检查项**。这是 S5-3 的查询能力**无法**替代的。

---

## 10. 后续 S5-4 边界

| 项 | 内容 |
|---|---|
| **模块** | `pyharness/governance/audit.py`（**扩展**，不新建模块） |
| **新增接口** | `reconcile() -> list[str]` · `legacy_session_audit(session) -> dict` |
| **写点** | **唯一**：`reconcile()` 的结论经**既有** `syscheck.fail` 落事件（`INV-A2` 的延伸） |
| **依赖注入** | `legacy_session_audit` 必须经**装配层注入** `telemetry.session_audit`（`governance → core` 被 ADR-018:308 禁止）—— 与 `chain_factory`（S2）/ `inputs_digest`（S4-P1-1）**同一先例** |
| **不做** | `TraceabilityMatrix`（**S7**） |
| **预计 schema 影响** | **无**（`syscheck.fail` 已注册；本步不新增事件类型） |
| **风险点** | ① `reconcile` 的**写点**必须严格限定为既有事件;② 四类差异的判定口径须逐条明确（避免假告警）;③ legacy 适配必须**结果等价**（不得改变 `telemetry.session_audit` 的输出） |

---

**本文件为只读审查产物**：未修改任何代码 / 测试 / 冻结契约;未 commit / push。生成依据为 S5-3 的实际代码与实测结果（全量 **1655 passed / 2 skipped / 0 failed**）。
