# ADR-020：治理层策略事件边界与策略放宽纪律——`policy.updated` 与 `scope.updated` 分工

> **本文件是 ADR-020 的正式文本。**
> **编号核验（2026-09-14 实测）**：`docs/ADD.md` 索引实际登记 **19 条**（ADR-001~019）；全库扫描 `ADR-020` **无任何定义占用**（仅在本会话文档中作为"建议创建"被引用）。故取号 **020**，且不占用任何既有编号。
> **状态**：已接受（人工裁定 + S2-1 验证） | **日期**：2026-09-14 | **落点**：S2（= M2）及其后续 S3~S6 | **原则映射**：G-5（单调性）/ G-6（文档即契约）/ G-7（范围即架构）
> **依据**：2026-09-14 人工裁定 Q1/Q2/Q3/Q4 与 Δ-1~Δ-6；[S2-1_EXIT_REVIEW.md](S2-1_EXIT_REVIEW.md)（**PASS**）· [S2-1_CHANGE_REPORT.md](S2-1_CHANGE_REPORT.md)

---

## 背景

S2（= M2）的交付物之一是 **`policy.updated` 事件**（`ARCHITECTURE_DECISION_RECORD.md:412`）。而冻结设计给出的两处文本，在落地前暴露为**不可实现/会产生语义重叠**：

1. **设计 §2.3** 定 `policy.updated` 的 `op` = `add | tighten | disable`；
2. **设计 §3.2** 给 `Policy` 定义了 `with_tightened(added, reason)`。

**实测的事实（运行期核验）**：

| 事件 | 已注册 | 强同步 | 语义 |
|---|---|---|---|
| **`scope.updated`** | **✅ True**（`vocab.py:255`，payload `ScopeUpdatedPayload` = `{op, added, reason}`） | False | **运行时 scope 单调收紧**；发射点 `scope.py:304`（`tighten`）与 `:318`（`note_tighten`，engine 装配期预设档位用，`engine.py:279` 有既有修复注释） |
| `policy.updated` | ❌ False（S2-2 才注册） | — | 设计给定 |

**冲突**：若 `policy.updated` 保留 `op="tighten"`，则策略收紧会**同时**产生 `scope.updated` 与 `policy.updated` **两条并行审计事件**——违反单一真源，并直接污染 S5 的审计因果链（同一事实两条记录，无从判定哪条权威）。同理，`Policy.with_tightened` 会把"运行时 scope 收紧"错误地建模为 **Policy 对象变化**，与 `scope.updated` 的既有语义争夺同一职责。

**另一处缺口**：`guard.disabled` **从未注册**（`is_registered` = False）→ 关闭 guard 的审计留痕只落本地日志、不进事件链（S1 评审登记的 **R-1**）。而 `PolicyEngine.from_config()` 恰好会执行 guard 关闭，冻结设计又给 `policy.updated` 留有"治理动作"语义——是关闭该缺口的自然位置。

**还有一处派生约束**：`ADR-018:308` 冻结了 **"`governance/` 只允许依赖 `pyharness.events` 与 `pyharness.errors`"**。设计 §3.2 的 `PolicyEngine.from_config(...)` 隐含"自行调用 `tools_guard.from_config` 并自建 g1–g7 的 `PolicyRule`"——**与上述冻结约束直接冲突**（治理层不得反向依赖 core）。

上述四项需在 S2 落码前定型。2026-09-14 经人工裁定（Q1/Q2/Q3/Q4 + Δ-1~Δ-6），并于 S2-1 骨架实现 + 独立出口审查（**PASS**）后定型为本 ADR。

---

## 决策

### Δ-1｜`policy.updated` 的 `op` 集固定为 `{add, enable, disable}`

`policy.updated.op ∈ {add, enable, disable}`。

**`tighten` 归 `scope.updated`**——`policy.updated` 与 `scope.updated` **不得表达同一种策略变化**。
合法 `op` 之外的值一律拒（实现为 `CYC-999`，S2-1 用例 T6 锁定）。

### Δ-2｜`Policy` 不负责 runtime scope 收紧；删除 `Policy.with_tightened` 设计

- **`scope.updated` 保留运行时 scope 单调收紧语义**（既有发射点与载荷不变）；
- **`Policy` 的职责收敛为"策略描述"**（规则集 / 求值序 / 版本 / 指纹 / 治理动作），**不承担 runtime scope 收紧**；
- **删除 `Policy.with_tightened`**（设计 §3.2 该行由本 ADR 取代）；
- 治理动作具体化为 **`Policy.with_rule_disabled(rule_id, *, config_ref)`** 与 **`Policy.with_rule_enabled(rule_id)`**。

### Δ-3｜INV-G6 更新：策略放宽纪律

**INV-G6**（更新后表述）：

> **策略状态变化必须经过显式治理动作。** 任何**放宽/禁用**行为必须同时满足：
> ① 产生 **`policy.updated`** 事件；
> ② 携带 **`config_ref`**（谁在何处声明了这次放宽）；
> ③ **可审计追踪**。
> **禁止静默放宽。**

**配套**：`guard.disabled` **不新增为独立事件**——guard 关闭统一经 **`policy.updated` 的 `op="disable"`** 表达，**必须进入治理事件链**（由此消除 S1 评审的 R-1 缺口）。`forced` 类规则（对应 `FORCED_GUARDS`）恒在不可禁。

### Δ-4｜`PolicyEngine` 采用注入式装配（派生自 ADR-018 依赖方向约束）

`PolicyEngine.from_config()` **不自行构造执行对象、不 import `tools_guard`**，而是接受装配层注入的：

- **`chain_factory`**（生产值 = `tools_guard.from_config`）——执行对象的构造入口；
- **`rules`**（规则描述数据，生产值 = `tools_guard.describe_rules()` 的产物）——规则面。

**治理层不直接依赖 `core` / `tools_guard`；治理层只负责：policy 描述 · policy fingerprint · policy version · policy event。** `tools_guard` 负责规则与执行。

> 本项**不是独立架构选择**，而是 ADR-018:308 冻结约束的**唯一可行实现**（治理层只许依赖 `events`/`errors`）。其在设计层面的表现是**签名文本变更**：`from_config` 增加 `chain_factory` 与 `rules` 两个注入位。
> **本 ADR 不修改 ADR-018**，仅记录其在本步的落地形态。

### Δ-6｜`config_ref` 继续走 `Envelope.trace`，不修改 payload schema

`config_ref` 作为**传输元数据**经 `Envelope.trace` 携带，**不进入 `policy.updated` 的 payload**——沿用本仓库既有先例（`tools_guard.py:801/817` 的 `call_id`、`approval.py:280` 的 `channel`，均为"payload 模型不容 → 走 trace"）。

`policy.updated` 的 payload 字段因此保持设计 §2.3 的**六字段**：
`{policy_id, version, fingerprint, op, added, reason}`。

### 本 ADR 的范围边界（纪律）

- **不修改 `ADR-018`**，**不修改 `ARCHITECTURE_DECISION_RECORD.md`**（该文件整体 FROZEN）；
- 依 `docs/ADD.md:35`「ADR 只增不改，推翻则新建取代」，本 ADR 是**取代记录**，既有 ADR 文本保持原貌；
- **本 ADR 不承载阶段实现偏离**（阶段偏离属实现报告的登记范畴，见 [S2-1_CHANGE_REPORT.md](S2-1_CHANGE_REPORT.md) §4）；
- 本 ADR 声明**取代** `GOVERNED_AGENT_RUNTIME_DESIGN.md` 的**两处文本**：§2.3 的 `op` 枚举、§3.2 的 `Policy.with_tightened`（该文件非 FROZEN）。

---

## 后果

**收益**：
1. **事件边界清晰**：`policy.updated`（治理层策略集/版本/开关）与 `scope.updated`（会话级运行时收紧）各司其职，**同一种变化只有一条事件**——S5 审计因果链不再被重叠记录污染；
2. **放宽可审计**：INV-G6 把"谁放松了安全"从"本地日志"提升为**事件链事实**，顺带关闭 S1 的 R-1 缺口；
3. **依赖方向可静态校验**：Δ-4 使 `governance/` 的 import 图可被断言（S2-1 的 T3 已用 AST + 运行期传递闭包验证**零 `core`/`bus`**）；
4. **payload 不膨胀**：Δ-6 让 `policy.updated` 保持六字段，避免为传输元数据扩模型（也就避免了后续 `extra="forbid"` 的连锁调整）。

**代价**：
1. **与冻结设计文本不一致**：设计 §2.3/§3.2 的两处表述已成历史文本，读者须并读本 ADR（本 ADR 已声明取代）；
2. **实现者需理解分工**：新增 `op="enable"`（设计未列）与 `op="disable"` 的 `config_ref` 强制，均须在实现与测试中体现；
3. **注入式装配增加一层间接**：装配层必须显式把 `chain_factory` / `rules` 传进治理层（治理层无法"自己找"执行对象）——这是方向约束的必然代价。

---

## 违反它的后果（具体场景）

1. **若 `policy.updated` 保留 `op="tighten"`**：每次会话级收紧会产生**两条**审计事件（`scope.updated` + `policy.updated`）。S5 的因果链查询将无法判定"这次收紧的权威记录是哪条"；若两者字段有细微差异（如 `added` 排序/截断不同），**同一事实出现两个版本**——正是 ADR-001 与 ADR-018 的"单一真源"要消灭的形态。
2. **若不强制 `config_ref`**：`op="disable"` 可在无任何声明来源的情况下关闭一条 guard —— "谁在何时放松了安全"在事件链里**无据可查**；与 S1 的 R-1 相比，只是把"缺口从日志搬到事件"，**没有真正可审计**。
3. **若 `PolicyEngine` 自行 import `tools_guard`**：直接违反 `ADR-018:308`。后果不是抽象的"耦合变差"，而是 ADR-018 已写明的具体失败模式：**治理层获得执行编排视角**，为"治理层开始执行"打开缺口（红线 G-4），并形成 `core ↔ governance` 的循环依赖。
4. **若把 `config_ref` 塞进 payload**：`policy.updated` 的 payload 模型（S2-2 落地，`extra="forbid"`）必须同步扩字段；任何已落盘的历史事件回放、以及 S2-2 之后对模型的演进都会受制于这个**纯传输字段**——重演本仓库在 `call_id`/`channel` 上已经踩过的坑（`tools_guard.py:36-39`、`approval.py:36-40` 的偏离说明即为该教训的记录）。

---

## 备选方案对比

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A.（采纳）** `policy.updated` 管治理层策略集/版本/开关；`scope.updated` 保留运行时收紧；`op = {add, enable, disable}`；guard 关闭并入 `op=disable`；`from_config` 注入式；`config_ref` 走 trace | 事件边界清晰、单一真源、放宽可审计、方向可静态校验、payload 不膨胀 | 与冻结设计两处文本不一致（须并读本 ADR）；装配层多一层注入 | ✅ **采纳** |
| B. `policy.updated` 保留 `op="tighten"`，与 `scope.updated` 并存 | 忠于设计文本 | **两条事件表达同一变化**；污染 S5 因果链；违背 Q1「禁止重叠」 | ❌ |
| C. `Policy.with_tightened` 保留，由 Policy 承载运行时收紧 | 忠于设计 §3.2 | 与 `scope.updated` 争同一职责；`Policy` 越界为运行时状态容器 | ❌ |
| D. 保留 `guard.disabled` 独立事件，`policy.updated` 不承载 disable | 职责单一 | 需**新增一个事件类型**（词表 +1）；且"策略集变化"被拆到两处，读者需拼两条事件才能还原"谁关了 guard" | ❌ |
| E. `from_config` 自行 import `tools_guard`（放弃注入） | 调用方少传两个参数 | **直接违反 ADR-018:308**；打开"治理层执行"缺口；形成循环依赖 | ❌ |
| F. `config_ref` 进 payload | 语义显式 | 迫使 payload 模型扩字段；与既有两处先例（`call_id`/`channel` 走 trace）不一致 | ❌ |

---

## 关联

- **`ADR-018`**（治理层目录与接口契约冻结）——Δ-4 是其依赖方向约束的**落地形态**；**本 ADR 不修改它**
- **`ADR-013`**（治理层作为独立权威层 · 授权与执行分离）——Δ-4 的方向约束源自其 G-4 红线
- **`ADR-015`**（决策事件双轨并存）——本 ADR 处理的是**同一层内的另一条边界**：策略事件 vs scope 事件
- **`ADR-019`**（S1 范围裁定）——本 ADR 的**体例先例**（增量记录 + 声明取代 + 不修改冻结历史）
- `ARCHITECTURE_DECISION_RECORD.md` §5.2 S2 行（S2 = M2 的出口判据）· 该文件 §0.1/ADR-018 的**新增事件类型**冻结项（`policy.updated`）
- **`GOVERNED_AGENT_RUNTIME_DESIGN.md` §2.3 / §3.2** —— 本 ADR **声明取代**其 `op` 枚举与 `Policy.with_tightened` 两处文本
- [S2-1_EXIT_REVIEW.md](S2-1_EXIT_REVIEW.md)（PASS；Δ-1~Δ-4/Δ-6 与代码逐条一致）· [S2-1_CHANGE_REPORT.md](S2-1_CHANGE_REPORT.md) · [S2-1_GOVERNANCE_SKELETON_DESIGN.md](S2-1_GOVERNANCE_SKELETON_DESIGN.md)（Δ 的来源）· [S2_ARCHITECTURE_READINESS_REVIEW.md](S2_ARCHITECTURE_READINESS_REVIEW.md)（Q1/Q2 的提出）
- **实现锚点**：`pyharness/governance/policy.py`（`POLICY_OPS` / `Policy.with_rule_disabled` / `PolicyEngine.from_config` / `PolicyRegistry.emit_updated`）· `tests/unit/test_governance_policy.py`（T4/T6/T7/T9）
