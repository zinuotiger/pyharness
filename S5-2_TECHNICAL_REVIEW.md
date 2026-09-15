# S5-2_TECHNICAL_REVIEW.md — 证据派生与运行时装配（技术复核与归档）

> **审查对象**：commit **`f72651e`** `feat: derive and wire evidence index`
> **审查日期**：2026-09-15 ｜ **审查性质**：**只读** · 未修改任何代码/测试/契约 · 未 commit
> **适用读者**：后续接手治理层（Governance Layer）的工程人员
> **术语约定**：英文术语后附中文解释，形如 `EvidenceCollector（证据收集器）`

---

## 1. S5-2 目标

### 1.1 为什么需要 EvidenceCollector（证据收集器）

治理层要回答的不是"某条规则拒绝了一次调用"，而是**"某一次工具调用为什么被允许/拒绝、依据哪一版策略、有没有凭证、能不能追溯"**。

这些问题的答案**已经全部存在于事件日志**中（`decision.issued` / `receipt.emitted` / `approval.*` / `guard.*` / `segment.*`）。但如果每次提问都去**全量扫描日志**，会带来两个问题：

1. **成本**：每次问答 O(n) 全量遍历；
2. **语义缺失**：日志是**线性事件流**，没有"**这段证据属于哪个任务**"的现成索引。

`EvidenceCollector（证据收集器）` 解决的就是**第二类问题**：它把"事件流"按**任务段（segment）**组织成一个**可提问的视图**，让 `collect_for_task(task_id)` 能直接回答"这个任务的证据有哪几条"。

它**不收集新事实**——它只**索引已有事实**。

### 1.2 S5-1 与 S5-2 的关系

| 阶段 | 交付 | 类比 |
|---|---|---|
| **S5-1** | `EvidenceRef（证据引用）` / `Evidence（证据工件）` / `archive()（归档入口）` / `evidence.archived（证据归档事件）` 注册 | **定义"证据长什么样" + "怎么写入一条证据"** |
| **S5-2** | `on_event（事件消费）` / `from_log（日志重建）` / `collect_for_task（按任务聚合）` + engine 装配 | **定义"证据怎么被读出来、按任务组织"** |

一句话：**S5-1 是写入口（write path），S5-2 是读出口（read path）。** 二者共同构成证据能力的**完整闭环**，且 S5-2 **未改动 S5-1 的任何契约**（`archive()` 零改动）。

### 1.3 四者关系

```
Event Log（事件日志,唯一持久化真源）
        │  只读订阅 5 类事件
        ▼
EvidenceCollector（证据收集器,只读派生索引）
        │  被持有
        ▼
GovernanceContext（治理上下文,单实例聚合）
        │  挂载于
        ▼
Engine（引擎脊柱,ctx.governance）
```

| 角色 | 中文解释 | 职责 |
|---|---|---|
| `Event Log` | 事件日志 | **唯一真源**;append-only JSONL;所有治理事实的物理载体 |
| `EvidenceCollector` | 证据收集器 | **只读消费者**;把事件流组织成可提问的索引视图 |
| `GovernanceContext` | 治理上下文 | 治理层的**唯一挂载面**（policy / decisions / receipts / evidence / audit） |
| `Engine` | 引擎脊柱 | **装配层**;负责"构造 → 订阅 → 注入"三步接线 |

**关键**：数据流是**单向**的 —— `Event Log → EvidenceCollector → GovernanceContext`；**不存在反向写回**。

---

## 2. 架构变化前后对比

### 2.1 Before（S5-1）

```
archive()（归档入口）
   │
   ▼
event log（事件日志）
   │
   ▼
（无读出路径 —— 要查证据只能自己扫全量日志）
```

**问题**：`evidence.archived` 事件写进去了，但**没有任何组件在读它**；"某个任务的证据"这个问题**无法直接回答**。

### 2.2 After（S5-2）

```
event log（事件日志,真源）
   │  总线分发 5 类事件
   ▼
EvidenceCollector（证据收集器,只读索引）
   │  持有
   ▼
GovernanceContext（治理上下文）
   │  挂载
   ▼
engine（引擎脊柱）
```

### 2.3 数据流解释

**写入方向（不变，S5-1 已定）**

```
调用方 → EvidenceCollector.archive() → session.append("evidence.archived")
                                            │
                                            ▼
                                     event log（真源）
```

**读出方向（S5-2 新增）**

```
event log
   │  ① evidence.archived      → 证据索引（evidence index）
   │  ② segment.start / .end   → 段索引（segment index）
   │  ③ decision.issued / receipt.emitted → 锚点序号索引（anchor seq index）
   ▼
EvidenceCollector 的三个派生索引
   │
   ▼
collect_for_task(task_id) → tuple[Evidence, ...]
```

**核心变化**：从"**有写无读**"变为"**写读闭环**"，且**读路径完全只读**。

---

## 3. 核心模块说明

### 3.1 `evidence.py`（证据模块）

#### `EvidenceCollector.__init__`

| 项 | 内容 |
|---|---|
| **输入** | `session`（可选，会话日志句柄）· `id_factory` · `clock` |
| **输出** | 实例;初始化**三个空索引** |
| **责任** | 持有索引与注入设施 |
| **不负责** | 不做任何 I/O;不订阅（订阅由 engine 做） |

#### `on_event(env)` — 只读消费

| 项 | 内容 |
|---|---|
| **输入** | 一条事件（`Envelope`,信封：含 `type` / `seq` / `payload`） |
| **输出** | 无（原地更新索引） |
| **责任** | 按事件类型更新三个索引;**异常一律吞掉不外抛**（畸形事件不得影响总线） |
| **不负责** | **不写任何事件**（AST 实测 `append` 调用数 = 0）;不判定;不抛错 |

**订阅面（5 类）**：`evidence.archived` · `segment.start` · `segment.end` · `decision.issued` · `receipt.emitted`。
**不订阅** `tool.result` —— 它属"工具执行因果关系"，由 **S5-3 `AuditSystem`** 承担（避免在证据层提前引入审计语义）。

#### `from_log(session)` — 日志重建

| 项 | 内容 |
|---|---|
| **输入** | 会话日志句柄 |
| **输出** | **全新的** `EvidenceCollector`，索引与"实时订阅态"**逐条相等** |
| **责任** | 证明索引是**派生缓存**（可丢失、可重建） |
| **不负责** | 不改动任何事件;不依赖原实例的内存状态 |

#### `collect_for_task(task_id)` — 按任务聚合

| 项 | 内容 |
|---|---|
| **输入** | `task_id`（字符串） |
| **输出** | `tuple[Evidence, ...]`，按证据事件的 `seq` **升序**;未知/空 ⇒ `()` |
| **责任** | 把证据按**段锚（segment anchor）**归集到任务 |
| **不负责** | **不写**;不改索引;不抛错（未知任务返回空元组） |

#### （S5-1 保留）`archive(claim, refs, …)`

| 项 | 内容 |
|---|---|
| **责任** | 本模块**唯一写点**;发射前校验 `refs` 可解析（INV-E2） |
| **不负责** | **不维护索引** —— 索引只能由 `on_event` 建立（保证"索引 = 日志的投影"） |

### 3.2 `engine.py`（引擎脊柱）—— 三阶段装配

```python
# ① 构造（Construct）
evidence = EvidenceCollector(session=log_)

# ② 订阅（Subscribe）
if bus is not None:
    ev_owner = f"governance-evidence:{getattr(log_, 'session_id', '?')}"
    for t in _EVIDENCE_EVENT_TYPES:
        bus.subscribe(t, evidence.on_event, owner=ev_owner)

# ③ 注入（Inject）
spine = EngineSpine(..., governance=GovernanceContext(
    policy=gov_policy, decisions=DecisionEngine(),
    receipts=ReceiptStore(), evidence=evidence), ...)
```

| 阶段 | 中文解释 | 为什么必须在此顺序 |
|---|---|---|
| **① 构造** | 先建实例 | 订阅需要一个已存在的绑定方法 `on_event` |
| **② 订阅** | 再把 `on_event` 注册到总线 5 类事件上 | 必须在**任何事件产生之前**完成，否则**首事件丢失** |
| **③ 注入** | 最后挂进 `GovernanceContext` | 保证 `ctx.governance.evidence` 与订阅的是**同一个对象**（单实例） |

**装配顺序的实证**：真实 spine 探针 → `spine.governance.evidence` = `EvidenceCollector` · `ctx.governance is spine.governance` = `True` · 段索引 `{'t-1': [2, None]}` · `evidence_count() = 1` · `collect_for_task('t-1') = ['E1']`。

### 3.3 `context.py`（治理上下文）—— 只改类型，不改结构

| 改动 | 说明 |
|---|---|
| `evidence: Any = None` → `evidence: Optional[EvidenceCollector] = None` | **仅类型标注** |
| docstring 形状清单更新 | 反映 receipts / evidence 已接线，仅 `audit` 仍未接线 |

**为什么只改类型？**

1. **字段早已存在**（S5-1 的"形状占位"设计）—— **冻结形状**的目的是让装配层与测试能稳定断言 `ctx.governance.evidence`，而不是 `AttributeError`；
2. `GovernanceContext` 的结构（字段集、顺序、`authorize()` 签名）属**已冻结契约**，S5-2 无权改动；
3. 类型标注带来**静态可读性**（读代码即可知该字段承载什么），成本为零。

---

## 4. 索引设计说明

### 4.1 Evidence Index（证据索引）

| 项 | 内容 |
|---|---|
| **数据结构** | `dict[str, tuple[int, Evidence]]`：`evidence_id → (事件 seq, Evidence 对象)` |
| **来源** | `evidence.archived` 事件 |
| **用途** | `collect_for_task` 的**唯一数据源**;`evidence_count()` 自检 |
| **为何是缓存** | 每一条 `Evidence` 都能由**该事件本身**完整重建（`_evidence_of(payload, seq)`）—— 删除内存索引再 `from_log` 即可复原 |

### 4.2 Segment Index（任务段索引）

| 项 | 内容 |
|---|---|
| **数据结构** | `dict[str, list[Optional[int]]]`：`task_id → [start_seq, end_seq]`（未闭合时 `end = None`，视为右端开放） |
| **来源** | `segment.start` / `segment.end` 事件 |
| **用途** | **把任意事件 seq 解析到 task**（`_task_at(seq)`）—— 这是"按任务聚合"的基石 |
| **为何是缓存** | 段锚本身是**已持久化的事实**（`segment.start` 强同步落盘）;索引只是它的查询结构 |

### 4.3 Anchor Seq Index（锚点序号索引）

| 项 | 内容 |
|---|---|
| **数据结构** | `dict[str, int]`：`"decision_id:<id>" → seq`、`"receipt_id:<id>" → seq` |
| **来源** | `decision.issued` / `receipt.emitted` 事件 |
| **用途** | 把 `decision_id` / `receipt_id` 形式的引用**解析回其所在的事件序号**，从而经段索引归入 task |
| **为何是缓存** | 映射关系完全由事件流决定;无独立状态 |

### 4.4 三者合起来如何回答"某任务的证据"

```
collect_for_task("t-3")
   │
   ├─ 取 t-3 的段区间 [start_seq, end_seq]          ← 段索引
   │
   └─ 遍历证据索引：对每条 Evidence 的 refs：
        · kind="segment"     → locator 直接给出 task   ← 直配
        · kind="seq"         → seq ∈ 区间？            ← 段索引
        · kind="decision_id" → 查锚索引得 seq → 区间？  ← 锚索引 + 段索引
        · kind="receipt_id"  → 同上
        · kind="test"        → 外部引用,不参与归集
```

### 4.5 为什么是缓存而不是事实源（**本节最重要**）

**判定标准只有一条**：**删掉索引，能否从事件日志无损复原？**

| 索引 | 删掉后能否复原 | 结论 |
|---|---|---|
| 证据索引 | ✅ `from_log` 重建，逐条相等 | **缓存** |
| 段索引 | ✅ 同上 | **缓存** |
| 锚索引 | ✅ 同上 | **缓存** |

**因此**：

- 索引**不是** `Source of Truth（真源）` —— 真源只有 `Event Log`；
- 索引可以随时丢弃、重建、并行存在多份（每 session 一份）；
- 索引**不参与任何治理判定** —— 判定只依赖 `Decision` 与事件事实。

> **反例警示**：若某天有人把"仅存在于索引里的信息"作为判定依据（例如"索引里有 = 合法"），则索引**事实上**变成了第二真源，`INV-G2 / INV-R5` 立即被破坏。

---

## 5. 关键设计原则

### 5.1 Event Log is Source of Truth（事件日志是真实来源）

**含义**：系统的事实 = JSONL 里的事件;其余一切（内存缓存、索引、视图）都是**派生**。

**在 S5-2 的体现**：
- `EvidenceCollector` 的任何状态都能由日志重建;
- `Evidence` 事件载荷**只含引用**（`INV-G4`）—— 不含被引用事件的内容副本;
- 治理层不新建任何持久化通道（无文件、无 DB）。

### 5.2 Derived Index（派生索引）

**含义**：索引 = 从真源**计算**出来的查询结构;可有可无、可多可少。

**在 S5-2 的体现**：三个索引全部满足"删除后可无损复原"。

**价值**：把"如何提问"（索引结构）与"事实是什么"（事件）**解耦** —— 未来要新的查询维度（例如按工具名聚合），**加索引而不改事实**。

### 5.3 Replayability（可重放性）

**含义**：相同的事件序列 → 相同的派生状态（确定性）。

**在 S5-2 的体现**：`from_log(session)` 与"实时订阅"产生**逐条相等**的索引（测试 T-2 断言）。

**为什么重要**：它使 `crash → restart` 后治理视图自动恢复，**无需任何持久化补偿**。

### 5.4 Read-only Consumer（只读消费者）

**含义**：消费者可以读事件流，**不得写事件**。

**在 S5-2 的体现**：
- `on_event` 体内 **AST 实测零 `append`**;
- 测试 T-3 断言"喂完全部事件后**事件数不变**";
- 证据模块**唯一写点**仍是 `archive()` 的 `evidence.archived`（`INV-E4`）。

**为什么重要**：只读消费者不会与真源形成写竞争，也不会改变被观察对象的语义（观察者效应）。

---

## 6. 测试说明

| # | 测试 | 保护什么风险 |
|---|---|---|
| **T-1** | `test_t1_on_event_builds_index`（索引由订阅累积） | **订阅失效 / 漏索引**：若 `on_event` 未正确解析载荷，索引会为 0 |
| **T-2** | `test_t2_rebuild_from_log_matches_subscription_state` | **索引退化为事实源**：证明"删掉内存态也能复原"，守住 `INV-E3` |
| **T-3** | `test_t3_on_event_is_read_only` | **观察者效应**：只读消费者若偷偷写事件，会污染真源;双重证据（事件数不变 + AST 零 append） |
| **T-4 / T-5** | `test_t4_t5_aggregate_by_segment_anchor` | **聚合越界/漏归集**：四类 ref 是否都能归入正确 task;跨段不串 |
| **T-6** | `test_t6_unknown_or_empty_task_returns_empty` | **未定义输入**：未知/空 task 必须返回空元组而非抛错（否则调用方需处理异常） |
| **T-7** | `test_t7_exception_safe_on_malformed_events` | **畸形事件击穿**：坏载荷不得抛穿总线，也不得腐蚀已建索引 |
| **T-8** | `test_spine_wires_evidence_single_instance` + `test_subscription_delivers_events_to_index` | **装配顺序 / 单实例 / 空转接线**：证明"构造→订阅→注入"正确、事件真送达、`ctx.governance` 与 spine 同一对象 |
| **T-9** | `test_dependency_boundary`（AST） | **依赖反向污染**：`evidence.py` 若引入 `core` / `persistence` / `bus` / `engine`，治理层边界即破 |
| **INV-E3** | `tests/invariants/test_inv_governance.py` | **不变量级守护**：把"索引可再派生 + 只读"提升为跨模块不变量 |

**测试基线**：S5-2 专项 **28 passed** · invariants **15 passed** · **全量 1635 passed / 2 skipped / 0 failed**。

---

## 7. 风险复盘

### 7.1 S5-2 解决了什么问题

| # | 问题 | 解法 |
|---|---|---|
| 1 | 证据**有写无读**：`evidence.archived` 写入后无人消费 | `on_event` 订阅并建索引 |
| 2 | **无法按任务提问**："这个任务有哪些证据" 需全量扫日志 + 自行实现段区间匹配 | `collect_for_task` + 段索引 |
| 3 | **跨引用类型无法归集**：`decision_id` / `receipt_id` 形式的引用不在段区间内，无法定位 | 锚点序号索引把引用解析回事件 seq |
| 4 | **重启后视图丢失** | `from_log` 可完全重建（`INV-E3`） |
| 5 | **新订阅者可能污染真源** | 只读约束 + 零 `append` 的结构与行为双重断言（`INV-E4` / T-3） |
| 6 | **装配顺序错误导致首事件丢失** | 构造 → 订阅 → 注入 的显式顺序 + T-8 断言 |
| 7 | **依赖反向污染风险** | AST 边界守卫（T-9） |

### 7.2 S5-2 还没有解决什么

| # | 未解决问题 | 说明 |
|---|---|---|
| 1 | **因果链还原（causal chain）** | 目前能回答"**该任务有哪些证据**"，但**不能**回答"**某一条决策的完整因果链**"（请求 → 策略 → 决策 → 审批 → 凭证 → 执行 → 结果） |
| 2 | **被拒调用的报告** | 不能生成"所有被拒调用及其依据"的清单（审计需证"拦了且没执行"，`INV-05`） |
| 3 | **对账（reconcile）** | 不能自检 `SEQ-GAP` / `CACHE-STALE` / `NO-GUARD-EVENT` / `NO-RECEIPT-FOR-GRANT` |
| 4 | **旧审计面兼容** | `telemetry.session_audit` 的既有调用点尚无治理层适配入口 |
| 5 | **`tool.result` 维度的追溯** | 按 `Δ-1` **有意未订阅** —— 执行结果维度属审计语义 |
| 6 | **追溯矩阵** | `TraceabilityMatrix`（F 编号 ↔ 模块 ↔ 测试 ↔ 事件）属 **S7** |

### 7.3 为什么 S5-3 `AuditSystem` 仍然需要

**核心差异**：**Evidence 是"按任务聚合的索引"，Audit 是"按决策还原的因果链"。**

| 维度 | EvidenceCollector（S5-2 已有） | AuditSystem（S5-3 待建） |
|---|---|---|
| 提问方式 | "**这个任务**有哪些证据？" | "**这条决策**为什么发生、经过了什么？" |
| 组织轴 | **段锚（task / segment）** | **因果轴（`call_id` / `decision_id` / `approval_id`）** |
| 覆盖事件 | 5 类（证据 / 段 / 锚） | **更宽**：还需 `tool.call` / `tool.result` / `guard.evaluated` / `guard.rejected` / `approval.*` |
| 输出 | `tuple[Evidence, ...]`（**已持久化**的证据） | **即时派生**的因果链字典（含**未被归档为 Evidence** 的事件） |
| 写点 | `archive()`（证据归档） | `reconcile()` 的 `syscheck.fail`（**唯一**写点） |

**关键点**：**Evidence 只覆盖"被显式归档"的证据**；而审计要覆盖**所有**决策——包括**没有**被归档为 Evidence 的那些。因此 S5-3 **不能**基于 S5-2 的索引实现，必须**独立地 replay-only（只读重放）**地扫描事件流。

**另一必要性**：`INV-05` 要求"审计可证：拦了且没执行"。这需要把 `guard.rejected` 与 `tool.result` 的**缺席**关联起来——这正是审计语义，而非证据聚合语义。

> 这也解释了 `Δ-1`（S5-2 不订阅 `tool.result`）的正当性：把执行结果维度**留给审计**，避免在证据层提前固化审计语义。

---

## 8. 下一阶段建议（仅计划，不实施）

### S5-3 `AuditSystem`（审计系统）

| 项 | 内容 |
|---|---|
| **模块位置** | `pyharness/governance/audit.py`（ADR-018 冻结目录） |
| **接口**（冻结设计 §3.6） | `causal_chain(decision_id) -> dict` · `denied_report(*, session_id, since_seq=0) -> list[dict]` |
| **核心约束** | **replay-only（只读重放）**：不得依赖任何内存索引;`causal_chain` 对**不存在**的 `decision_id` 返回**显式缺失**（`INV-A1`，不臆造）;本步**无写点**（`INV-A2`） |
| **不做** | `reconcile()` / `legacy_session_audit()`（**S5-4**）· `TraceabilityMatrix`（**S7**） |
| **依赖方向** | `governance → events/errors`（禁止 `core` / `persistence` / `bus` / `engine`） |
| **前置** | S5-2 checkpoint `f72651e`（本步已就绪） |
| **预计规模** | 单模块 + 专项测试;`EVENT_TYPES` / `SYNC_TYPES` / `TRANSIENT_TYPES` **不变** |
| **风险点** | ① replay-only 的实现纪律（不得走内存索引）;② `causal_chain` 的**缺失语义**必须显式而非静默;③ `denied_report` 必须能区分"`guard.rejected` 拒绝"与"`decision.issued(verdict=reject)` 拒绝" |

**建议实施顺序**：`causal_chain` → `denied_report` → 装配（`engine.py` 注入 `GovernanceContext.audit`）→ 专项测试 → checkpoint。

---

**本文件为只读审查产物**：未修改任何代码 / 测试 / 冻结契约;未 commit / push。生成依据为 commit `f72651e` 的实际代码与实测结果。
