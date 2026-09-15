# S5-3_FINAL_ARCHITECTURE_REVIEW.md — 治理审计层最终架构复核（知识冻结）

> **审查对象**：S5-3 checkpoint **`a53da99`** `feat: add governance audit causal chain`
> **审查日期**：2026-09-15 ｜ **审查性质**：**只读** · 未修改任何代码/测试/契约 · 未 commit
> **适用读者**：后续接手治理层（Governance Layer）的工程人员
> **术语约定**：英文术语后附中文解释，形如 `AuditSystem（审计系统）`
> **实测基线**：全量 **1655 passed / 2 skipped / 0 failed** · `EVENT_TYPES=77` / `SYNC_TYPES=14` / `TRANSIENT_TYPES=3`

---

## 1. S5-3 架构目标

### 1.1 为什么需要 `AuditSystem（审计系统）`

到 S5-2 为止，治理层已经具备**三种**能力：

| 能力 | 构件 | 回答 |
|---|---|---|
| 产生事实 | `Decision` + `decision.issued` | "**做了什么决定**" |
| 证明事实 | `Receipt` + `receipt.emitted` | "**这个决定可否离线核验**" |
| 组织证据 | `EvidenceCollector` + `evidence.archived` | "**这个任务**有哪些证据" |

但**四类问题仍无人回答**：

| # | 问题 | 为什么现有构件答不了 |
|---|---|---|
| 1 | "**这条决策**为什么发生？经过了哪些环节？" | `EvidenceCollector` 按**任务**组织，不按**决策**串联 |
| 2 | "依据**哪一版策略**？**谁**做的决定？" | 散落在 `decision.issued` 的多个字段里，需人工拼 |
| 3 | "**哪些调用被拒**？依据什么？" | 无任何构件产出拒绝清单 |
| 4 | "能证明**拦了且没执行**吗？"（`INV-05`） | 需要把"拒绝"与"`tool.result` 缺席"关联 —— 属审计语义 |

`AuditSystem` 就是这四类问题的**统一入口**。

### 1.2 `EvidenceCollector（证据收集器）` 存在哪些不足

| # | 不足 | 展开 |
|---|---|---|
| 1 | **事件面太窄** | 只订阅 **5 类**（`evidence.archived` / `segment.start` / `segment.end` / `decision.issued` / `receipt.emitted`）;**没有** `tool.result` / `guard.evaluated` / `guard.rejected` / `approval.*` ⇒ 因果链的关键环节**结构性缺失** |
| 2 | **组织轴不对** | 按**段（segment / task）**聚合;而因果问题的轴是**决策 / 调用**，跨段、跨调用都可能 |
| 3 | **数据形态不对** | 存的是 `Evidence（工件）`（引用 + 摘要）;因果链需要**原始事件事实**（`verdict` / `principal` / `digest` / `by`…） |
| 4 | **覆盖不全** | 只覆盖**被显式归档**的证据;审计须覆盖**全部**决策（含从未归档者） |
| 5 | **重启后为空** | 其索引是**实时订阅**累积的内存缓存;重启后须等事件重新到达才有内容 —— 而复盘恰恰发生在重启之后 |

**结论**：`EvidenceCollector` 的不足**不是实现缺陷**，而是**职责边界** —— 它按设计只解决"按任务找证据"。因果问题需要**另一个构件**，且**不能**建立在它之上（见 §4.4）。

### 1.3 `AuditSystem` 解决什么问题

| # | 解决 |
|---|---|
| 1 | **因果可还原** —— `causal_chain(decision_id)` 一次返回 9 类事件串联的完整链条 |
| 2 | **拒绝可清点** —— `denied_report()` 产出被拒调用清单，**两类来源可区分** |
| 3 | **执行可证伪** —— `executed` 字段关联 `tool.result` 的**有无**，构成 `INV-05` 所需的"拦了且没执行"的可证事实 |
| 4 | **重启可用** —— **replay-only（仅重放）** ⇒ 仅凭日志即可工作 |
| 5 | **缺失不静默** —— `missing` **显式**列出"应有而未找到"的环节，**绝不臆造** |

**它不是**：新真源、新缓存、新订阅者、新写点、新持久化。

---

## 2. 完整治理数据流

```
                          Event Log（事件日志,唯一真源）
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │  实时订阅(5类)              │  只读重放(全量)             │
        ▼                           ▼                           │
 EvidenceCollector（证据收集器）   AuditSystem（审计系统）          │
   · 段轴  · 内存索引(可重建)        · 因果轴  · 无状态             │
   · 唯一写点 archive()            · 无写点                       │
        │                           │                           │
        └─────────────┬─────────────┘                           │
                      ▼                                         │
        GovernanceContext（治理上下文,单实例聚合:               │
        policy / decisions / receipts / evidence / audit）        │
                      │                                         │
                      ▼                                         │
        Engine（引擎脊柱 → ctx.governance）                       │
                      │                                         │
                      └────── 工具调用时经 authorize() ──────────┘
                                （写 decision.issued 回事件日志）
```

### 2.1 哪些是**实时**（real-time）

| 构件 | 机制 | 订阅面 |
|---|---|---|
| **`EvidenceCollector`** | `bus.subscribe(…)` 实时消费并**累积内存索引** | **5 类**：`evidence.archived` / `segment.start` / `segment.end` / `decision.issued` / `receipt.emitted` |

**为什么需要实时**：按任务聚合要求"**事件一到就能查**"，且索引要覆盖**当前会话的全部历史**。

### 2.2 哪些是 **replay-only（仅重放）**

| 构件 | 机制 | 说明 |
|---|---|---|
| **`AuditSystem`** | 每次调用即时 `_read_events(session)` **全量重放** | **无订阅**、**无缓存**、**无状态** |

**为什么 replay-only**：见 §4.2。

### 2.3 哪些是**唯一真源**（source of truth）

| 构件 | 是真源？ | 说明 |
|---|---|---|
| **`Event Log`** | ✅ **唯一真源** | append-only JSONL;所有治理事实的物理载体 |
| `EvidenceCollector` 的索引 | ❌ **缓存** | 可由 `from_log` 完全重建 |
| `AuditSystem` | ❌ **视图** | 每次都从真源重算;本身不存储任何东西 |
| `GovernanceContext` / `Engine` | ❌ **持有者 / 装配层** | **不消费事件**（图中连线表示"被挂载"而非"订阅"） |

> **准确表述**：`Event Log` 之外的一切都是**派生**。图中 `GovernanceContext` / `Engine` 与 `Event Log` 之间**没有**直接的读/写箭头 —— 它们是**持有关系**，唯一的写回路径是工具调用时 `authorize()` 写入 `decision.issued`。

---

## 3. `Evidence` vs `Audit` 边界说明

### 3.1 `EvidenceCollector（证据收集器）`

| 维度 | 内容 |
|---|---|
| **组织轴** | **task / segment axis（任务段轴）** —— 按 `segment.start` / `segment.end` 划定区间 |
| **数据来源** | `evidence.archived` 事件（**唯一索引来源**，S5-2 裁定 D-1(a)） |
| **形态** | **derived index（派生索引）** —— 三个内存字典（证据索引 / 段索引 / 锚点序号索引） |
| **写入** | 唯一写点 `archive()` → `evidence.archived`（**普通攒批，非强同步**） |
| **订阅** | ✅ 5 类（实时） |
| **典型提问** | "`task_id = t-3` 这个任务有哪些证据？" |

### 3.2 `AuditSystem（审计系统）`

| 维度 | 内容 |
|---|---|
| **组织轴** | **decision / call axis（决策 / 调用轴）** —— 按 `decision_id` / `call_id` / `approval_id` / `receipt_id` 串联 |
| **数据来源** | **9 类**事件（`decision.issued` / `receipt.emitted` / `tool.call` / `tool.result` / `guard.evaluated` / `guard.rejected` / `approval.requested` / `approval.granted\|denied\|timeout` / `supersedes`） |
| **形态** | **causal reconstruction（因果重建）** —— 每次即时从日志重算，**不存储** |
| **写入** | **无写点**（`reconcile` 属 S5-4） |
| **订阅** | ❌ **无**（replay-only） |
| **典型提问** | "`decision_id = D-A` 这条决策为什么发生、经过了什么？" |

### 3.3 二者为何必须正交

```
任务维度（横向）：  t-1 ────证据─── 证据 ─── 证据
                     t-3 ────证据─── 证据
                     
决策维度（纵向）：  D-A ──► call ──► guard ──► receipt ──► result
                     D-B ──► call ──► guard ──► receipt
```

- **任务**是**容器**（一段时间内的全部活动）;**决策**是**链条**（一次调用从请求到结果的因果路径）。
- 一次决策可能横跨多个任务段（如子代理派生）;一个任务段也包含多条决策。
- 二者**无法互相表达** ⇒ 必须**两套索引/视图**。

### 3.4 唯一的共同点

两者**使用同一读法**（`_read_events(session)`：优先 `events_after(0)`、回落 `replay()` 的**鸭子类型只读重放**），但 **`audit.py` 完全不 import `evidence.py`**，**不共享任何状态**。

---

## 4. `AuditSystem` 设计原则

### 4.1 `Source of Truth（真源）`

**含义**：系统的事实 = 事件日志;其余一切是**派生**。

**在 S5-3 的体现**：
- `AuditSystem` **不持有**任何持久化;每次提问都回到日志;
- 输出中的每一段都带**真源 locator**（事件的 `seq`），可点回原始事件;
- **不引入**任何新的持久化通道（无文件、无 DB、无缓存文件）。

### 4.2 `Replayability（可重放性）`

**含义**：相同的事件序列 ⇒ 相同的派生结果（确定性）。

**在 S5-3 的体现**：
- **replay-only**：每次调用全量重放，**不使用**任何实时订阅;
- 因此**重启后自动可用**（不需要任何恢复逻辑）;
- **不依时序**：结果与"事件何时到达""谁先订阅"**无关**;
- 测试断言：**全新实例（无订阅历史）** 与订阅态得出**相同**结果。

**为什么审计**尤其需要可重放：事故复盘、离线核验、CI 对账 **都发生在系统重启之后**。

### 4.3 `Read-only Consumer（只读消费者）`

**含义**：消费者可以读事件流，**不得写事件**。

**在 S5-3 的体现**：
- `audit.py` 内**零写点**（AST 断言：无对**会话对象**的 `append`）;
- 行为断言：调用 `causal_chain` / `denied_report` 前后**事件数不变**;
- **不新增总线订阅者**（装配测试断言订阅 owner 中无 audit 相关项）。

**为什么重要**：只读消费者不会与真源形成写竞争，也不会改变被观察对象的语义（**观察者效应**）。

### 4.4 `No Second Truth（无第二事实源）`

**含义**：任何派生结构都**不得**成为"事实的另一个来源"。

**在 S5-3 的体现**：
- `AuditSystem` **无状态** ⇒ 结构上不可能成为真源;
- **刻意不依赖** `EvidenceCollector` 的索引 —— 若依赖，该索引会**事实上**升级为"审计的前提"，逼近第二真源;
- 与 `INV-G4`（只含引用与哈希）/ `INV-R5`（凭证非第二真源）/ `INV-E3`（索引可再派生）**同源**。

**反例警示**：若某天有人把"仅存在于某个缓存里的信息"作为**审计或判定的依据**，该缓存即成为第二真源，整个事件溯源的保证随之失效。

---

## 5. `causal_chain` 数据模型

**签名**：`causal_chain(decision_id: str, *, ctx=None) -> dict`（**11 键**）

| # | 字段 | 来源事件 | 作用 | 缺失语义 |
|---|---|---|---|---|
| 1 | `decision_id` | 入参 | 主键;整个链条的锚点 | —（入参恒在） |
| 2 | `call_id` | `decision.issued` 的 **`Envelope.trace.call_id`** | **串联全部环节的关键**（请求 / 执行 / 规则 / 审批都靠它配对） | 为空串 ⇒ 无法关联请求与执行（各自为 `None`） |
| 3 | `request` | `tool.call`（按 `call_id`） | **请求了什么**（`tool` + `seq`） | 有 `call_id` 却无 `tool.call` ⇒ `None` 且 **`missing += "tool.call"`** |
| 4 | `policy` | `decision.issued` | **依据哪版策略**（`fingerprint`）+ **哪些策略引用**（`refs`）+ **哪些规则命中**（`guards`） | 随 `decision.issued`;该事件缺失则整链终止 |
| 5 | `decision` | `decision.issued` | **结果 + 何时 + 谁**（`verdict` / `ts` / `seq` / `principal{kind,id,channel}`） | 同上（主键所在，缺失即返回） |
| 6 | `approval` | `approval.granted` / `denied` / `timeout`（按 `approval_ref`） | **审批结果**（`approval_id` / `outcome` / `by` / `seq`） | `approval_ref` 为空 ⇒ **恒 `None` 且不记缺失**（D1 本无审批结果）;非空却找不到结果事件 ⇒ **`missing += "approval"`** |
| 7 | `supersedes` | `decision.issued.supersedes` | **决策间的继承关系**（D2 → D1） | `None` = 无前序（非缺失） |
| 8 | `receipt` | `receipt.emitted`（按 `decision_id`） | **有无凭证、凭证是什么**（`receipt_id` / `kind` / `digest` / `prev_hash` / `seq`） | 若**应有凭证**而无 ⇒ **`missing += "receipt.emitted"`**;"应有"的判定见下 |
| 9 | `execution` | `tool.result`（按 `call_id`） | **执行了没有、结果如何**（`ok` + `seq`） | `None` 且 **不记缺失** —— 缺席本身即"未执行"这一事实（对 REJECT 是**正常**结果） |
| 10 | `denied` | 派生自 `verdict == "reject"` | 便捷判定（免调用方自行比较字符串） | — |
| 11 | `missing` | 派生 | **显式**列出"应有而未找到"的环节名 | 空列表 = 链条完整 |

### 5.1 "应有凭证"的判定规则（§5 第 8 行的关键）

```
verdict == "approval" AND approval_ref is None   ⇒ 这是 D1（审批请求决策）
                                                 ⇒ **不产生**凭证（INV-R7）
否则                                              ⇒ **应有**凭证
```

该规则与 `receipt.receipt_kind_of` **完全一致**，并由测试**逐例守卫**（`allow` / `reject` / `approval+None` / `approval+ref` 四例），**防止两处规则漂移**。

### 5.2 缺失语义的三条纪律

| # | 纪律 |
|---|---|
| 1 | **缺失必显式** —— 不返回"看起来正常"的链条;未命中环节一律 `None` 并登记 `missing` |
| 2 | **不臆造** —— 绝不用其他事件"推测"缺失环节的内容 |
| 3 | **区分"缺失"与"本无"** —— `execution` 对 REJECT 是**本无**（不入 `missing`）;`approval` 对 D1 是**本无**;只有"按规则应有"才入 `missing` |

---

## 6. `denied_report` 设计

**签名**：`denied_report(*, session_id: str = "", since_seq: int = 0, ctx=None) -> list[dict]`（**9 键**记录，按 `seq` 升序）

### 6.1 为什么区分两类来源

治理层的拒绝发生在**两个层级**（这正是 `ADR-015` 双轨事件的设计初衷）：

| 来源 `source` | 产生者 | 层级 | 回答的问题 |
|---|---|---|---|
| `"guard.rejected"` | `tools_guard._append_rejected`（**强同步**） | **规则级** | "**哪条规则**拦的？依据哪个 `policy_ref`？" |
| `"decision.issued"`（`verdict=reject`） | `GovernanceContext.authorize`（**强同步**） | **治理级** | "**治理层**作出了什么决定？依据哪些 `policy_refs`？" |

**若混为一条**：
- 丢失**规则级归因**能力（事后追查"到底是 g3 还是 g4 拦的"将无据可依）;
- 无法区分"某条规则拒绝"与"治理层整体拒绝"（二者可能同时存在）。

**若只保留一类**：`denied_report` 会**漏报**另一类拒绝 ⇒ 审计报告不完整。

### 6.2 `executed=False` 如何保证"拒绝调用没有执行"

**机制**：`executed` = **该 `call_id` 是否存在配对 `tool.result` 事件**。

```
被拒调用 cB
   │
   ├─ tool.call(cB)                     ← 有请求
   ├─ guard.rejected(cB) / decision.issued(D-B, reject, trace=cB)
   └─ tool.result(cB)  **不存在**        ← ★ 关键:没有结果 = 没执行
                                          ⇒ executed = False
```

**为什么这是可证事实**：在 PyHarness 的管道中，**执行必然产生 `tool.result`**（关 4 `finalize` 的硬约束:每调用恰一条终局事件）。因此：

- `tool.result` 存在 ⇔ 该调用**进入了执行**;
- 对拒绝记录而言 `executed == False` ⇒ **可证"拦了且没执行"**（`INV-05`）。

**反证的价值**：若某条拒绝记录的 `executed == True`，说明存在**绕过拒绝执**行的异常路径 —— 这是审计必须捕获的**高危信号**。

### 6.3 记录字段

| 键 | 说明 |
|---|---|
| `source` | 两类之一（**必须**） |
| `seq` | 拒绝事件的序号（排序键,可与真源对齐） |
| `tool` | 工具名 |
| `call_id` | 经 `Envelope.trace` 取;用于与 `tool.result` 配对 |
| `guard_id` | 规则级专有（治理级为空串） |
| `policy_ref` | 规则级专有 |
| `decision_id` | 治理级专有 |
| `reason` | 规则级取 `reason`;治理级取 `policy_refs` 拼接 |
| `executed` | 是否存在配对 `tool.result` |

---

## 7. 测试与不变量总结

### 7.1 三条审计不变量

| 不变量 | 含义 | 保护位置 |
|---|---|---|
| **INV-A1** | `causal_chain` 对**不存在**的 `decision_id` 返回**显式缺失**（`missing` 非空），**不臆造** | `tests/invariants/test_inv_governance.py` + 单元 T-7 |
| **INV-A2** | `causal_chain` / `denied_report` **纯只读**;S5-3 **无写点**（`reconcile` 属 S5-4 且仅写既有 `syscheck.fail`） | 同上 + 单元 T-6（事件数不变 + AST 无会话 `append`） |
| **INV-A3** | `denied_report` 每条拒绝**必须可追溯**到 `guard.rejected` **或** `decision.issued(verdict=reject)`;两类**可区分** | 同上 + 单元 T-4（`source` 两类齐备 + `executed=False`） |

**与既有不变量的关系**：
- `INV-A1` 与 `INV-R2`（`rebuild_from_log` 的"无对应决策则跳过"）**同口径**;
- `INV-A2` 与 `INV-G5`（治理层无执行/放行 API）**同族**;`reconcile` 的唯一写点与 `INV-E4`（`archive` 唯一写点）**同型**;
- `INV-A3` 落实 **`INV-05`**（"拦了且没执行"）的可证性。

### 7.2 replay-only 的四个证明

| # | 证明 | 断言位置 |
|---|---|---|
| 1 | **不订阅** | ① `audit.py` AST 内**无 `subscribe`**（T-9）;② 装配后**订阅 owner 集合中无 audit 相关项**（装配测试） |
| 2 | **不缓存** | **全新实例**（无任何订阅历史）⇒ 结果与订阅态**相同**（T-5） |
| 3 | **不写** | 调用前后**事件数不变**（T-6）+ AST 断言无对**会话对象**的 `append` |
| 4 | **不依赖 EvidenceCollector** | `audit.py` **零 import `evidence`**（T-9 依赖边界） |

### 7.3 测试规模

| 项 | 结果 |
|---|---|
| S5-3 专项 `test_governance_audit.py` | **19 passed** |
| Invariants（`tests/invariants/`） | **16 passed**（含 INV-A1/A2/A3） |
| **全量回归** | **1655 passed / 2 skipped / 0 failed** |

---

## 8. Deferred Debt

| # | 项 | 说明 | 边界 |
|---|---|---|---|
| **D-1** | **`causal_chain` / `denied_report` 为 O(n) 全量重放** | 每次调用遍历全量事件。**已裁定接受**（`H-6`）：v1.0 优先保证**审计正确性、可复现性与单一真源** | 若未来优化，**唯一允许**方向是加**一次性派生缓存**，且**必须**满足"删除后可由日志完全重建";**不得**引入任何**持久化**（否则违反 `INV-R5`） |
| **D-2** | **`denied_report.reason` 的展示口径** | 治理级来源的 `reason` 为 `policy_refs` 的**逗号拼接字符串**（非结构化） | 若需结构化，应改为列表 —— 但会改变已冻结的 **9 键扁平结构**，故本次未改。如需调整须作为**契约变更**处理 |

**未新增其他债务**。特别地，**未引入**任何缓存、订阅、持久化 —— `INV-R5 / INV-E3` 未被触碰。

---

## 9. 后续 S5-4 边界（**只规划，不实现**）

### 9.1 范围

| 新增 | 位置 |
|---|---|
| `AuditSystem.reconcile() -> list[str]` | `governance/audit.py`（**扩展**，不新建模块） |
| `AuditSystem.legacy_session_audit(session) -> dict` | 同上 |

### 9.2 各自解决什么问题

#### `reconcile()` —— **一致性自我检验**

| 检查项 | 检测什么 | 关联不变量 |
|---|---|---|
| `SEQ-GAP` | 事件序号出现空洞（未声明） | `INV-01` / 既有 `warn_hole` |
| `CACHE-STALE` | 派生视图与真源不一致 | `INV-03` |
| `NO-GUARD-EVENT` | 存在执行但没有对应的 `guard.evaluated` | `INV-04` |
| `NO-RECEIPT-FOR-GRANT` | 存在 `approval.granted` 但没有对应凭证 | **`INV-G2`**（运行时哨兵） |

**核心价值**：把**静态不变量**变成**可运行时自检的检查项**。这是 S5-3 的**被动查询**能力**无法**替代的 —— `causal_chain` 只呈现事实，`reconcile` **判定对错**。

**写点**：`reconcile()` 的结论经**既有** `syscheck.fail` 落事件（**本阶段唯一写点**;`syscheck.fail` 已注册，**不新增事件类型**）。

#### `legacy_session_audit()` —— **旧审计面兼容**

`pyharness/core/telemetry.py::session_audit(session) -> dict` 是既有调用点依赖的审计入口。治理层需要一个**兼容适配**，使旧调用点无需改动即可走治理层。

**硬约束（依赖注入）**：`governance → core` 被 `ADR-018:308` **禁止** ⇒ 必须经**装配层注入** `telemetry.session_audit`（治理层只持有 callable，不 import）—— 与 `chain_factory`（S2）/ `inputs_digest`（S4-P1-1）**同一先例**。

### 9.3 明确不做

| 项 | 归属 |
|---|---|
| `TraceabilityMatrix`（F 编号 ↔ 模块 ↔ 测试 ↔ 事件的生成式矩阵） | **S7**（冻结计划 §5.2） |
| 不变量与测试面重写（`INV-01~09` + `INV-G1~G6`） | **S6**（M8/M9） |
| `tests/acceptance/` / `tests/security/` 激活 | **S6** |
| 端到端 Demo（M10） | **S7** |

### 9.4 待明确的风险点

| # | 风险 | 缓解方向 |
|---|---|---|
| 1 | `reconcile` 成为"第二个写点"而失控 | 严格限定为**既有** `syscheck.fail`;**不得**新增事件类型;写点计数的专项测试 |
| 2 | 四类差异判定口径不清 ⇒ **假告警** | 每类差异须有**专项判定**与**正反例测试** |
| 3 | legacy 适配**结果不等价** | 断言 `legacy_session_audit(session) == telemetry.session_audit(session)` |

---

**本文件为只读审查产物**：未修改任何代码 / 测试 / 冻结契约;未 commit / push。生成依据为 S5-3 checkpoint **`a53da99`** 的实际代码与实测结果（全量 **1655 passed / 2 skipped / 0 failed**）。
