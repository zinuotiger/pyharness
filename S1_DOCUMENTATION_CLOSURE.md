# S1_DOCUMENTATION_CLOSURE.md — S1 文档收口（C-2 + C-3）执行与核验报告

> **阶段**：S1 Documentation Closure Gate（C-2 + C-3）
> **日期**：2026-09-14 ｜ 基线 `0cba75d`（HEAD 未变）
> **依据**：[S1_EXIT_REVIEW.md](S1_EXIT_REVIEW.md) · [S1_SCOPE_ADJUDICATION.md](S1_SCOPE_ADJUDICATION.md) · [ADR-019-s1-scope-adjudication.md](ADR-019-s1-scope-adjudication.md) · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) · [REFACTOR_PLAN.md](REFACTOR_PLAN.md) · [S1_CHANGE_REPORT.md](S1_CHANGE_REPORT.md)
> **性质**：**仅文档收口**——未修改任何 `.py`、未修改测试、未执行 W3、未拆 `build_runner_components`、未收敛 repair、未删 `_invoke`、未修改 `ARCHITECTURE_DECISION_RECORD.md`、未修改 ADR-019 历史内容、未创建 Governance runtime / Decision Receipt 实现 / Evidence 实现、**未进入 S2**。

---

## 0. 结论

| 项 | 结论 |
|---|---|
| **C-2**（补齐 S0/S1 文档登记） | **PASS** |
| **C-3**（修正 `S1_CHANGE_REPORT.md` 事实错误） | **PASS** |
| **S1 Documentation Closure** | **PASS** |
| **S2 Entry Condition** | **NOT MET** |

**NOT MET 的唯一理由**：C-2 执行中暴露一处**治理事件命名冲突**（本次任务书给出的 4 个名称与冻结记录不一致，其中 3 个不同，且遗漏 `policy.updated`），而 **`policy.updated` 恰是 S2 自身的交付物**。该冲突**必须由人工确认后方可进入 S2**（详见 §3）。**其余 S2 前置条件（ADR §5.3 串行、P-1~P-5）均已满足**（§4）。

**本阶段到此停止，等待人工确认。**

---

## 1. C-2 执行内容

### 1.1 `docs/ADD.md` 回填（ADR-013~019）

| 动作 | 位置 | 内容 |
|---|---|---|
| 顶部条数 | `docs/ADD.md:5` | `12 条 ADR` → **`19 条 ADR`**，并注明 001~012 定稿 2026-09-06、013~019 治理期追加 2026-09-14 |
| §1 概述 | §1 正文 | `12 条 ADR 中,…` → `19 条 ADR 中,…`；追加一句说明 013~019 为治理期追加、正文见 §2.1 所列文件 |
| §2 索引表 | 表末 | **追加 7 行**（ADR-013~019），**保持原 3 列格式**（编号｜标题｜决策一句话） |
| **新增 §2.1** | §2 索引表之后 | 「ADR-013~019 正文位置」——给出正文所属**文件路径**（markdown 相对链接），并声明**本文件是唯一 Registry**、禁止另建第二套索引表 |
| §3 阅读约定 | 「编号与落点」条 | 追加：ADR-013~019 的落点为**治理期**（S0~S7 开发顺序），不对应 F 编号 |

**路径供给**（要求「为 013～019 提供正确文件路径」）：

| 编号 | 正文文件 | 链接 |
|---|---|---|
| ADR-013 ~ ADR-018 | `ARCHITECTURE_DECISION_RECORD.md` §2「ADR 列表」（每条 `### ADR-01N:…` 独立小节） | `../ARCHITECTURE_DECISION_RECORD.md` |
| ADR-019 | `ADR-019-s1-scope-adjudication.md`（全文） | `../ADR-019-s1-scope-adjudication.md` |

> **为何不创建 ADR-013~018 的独立文件**：其正文已存在于冻结记录 `ARCHITECTURE_DECISION_RECORD.md` §2，另建文件＝复制同一内容（必然漂移），且违反「不修改既有冻结 ADR 的历史事实」的精神。故按「索引在 ADD.md、正文就地」收纳，并显式声明**唯一 Registry**。

### 1.2 `docs/EVENT-SCHEMA.md` 预登记

新增 **§3.6 F — 治理层侧（Governance v1.0;预登记,未实现）**（插于 §3.5.6 之后、§4 之前），沿用原格式（表格 + 四要素风格）与 **§7 规则 6「E 组先例：schema 已定义可先冻结」**机制。同步把 §3 引言的「按 A-E 分组」改为「按 **A-F** 分组（F 组为治理层预登记,见 §3.6）」。

登记内容（**仅占位，零实现**）：

| 事件（冻结名） | 通道 | 承载不变式 |
|---|---|---|
| `policy.updated` | 强同步 | — |
| `decision.issued` | 强同步 | INV-G1 |
| `receipt.emitted` | 强同步 | INV-G2 / INV-G3 |
| `evidence.archived` | 普通（攒批） | INV-G4 |

§3.6 明确三件事：① **状态=预登记**，未入词表（实测 73 型），实现前 `append` 一律 `EVT-102` 拒写；② **命名权威**=冻结记录 §0.1 + ADR-018「契约冻结清单」；③ 实现时须走 §7 规则 5「词表登记制」并升词表版本（73→77）。

**未做**（严守边界）：未改 `events/vocab.py`、未改 `events/payload.py`、未改 `EventBus`、未改任何 runtime。

---

## 2. C-3 执行内容

### 2.1 §1 逐文件数字勘误

将 §1 改动表的增删列改为 **`git diff --numstat` 实测值**（此前 8/11 行误取 `git diff --stat` 的**总变动行数**当 additions）：

| 文件 | 原（错误） | 现（numstat 实测） |
|---|---|---|
| `docs/DIS-CORE.md` | +4 / −2 | **+3 / −1** |
| `docs/MAP.md` | +3 / −3 | +3 / −3（本就正确） |
| `pyharness/core/agent.py` | +21 / −20 | **+11 / −10** |
| `pyharness/core/agent_loop.py` | +63 / −22 | **+52 / −11** |
| `pyharness/core/session.py` | +90 / −45 | **+58 / −32** |
| `pyharness/core/tools_guard.py` | +14 / −1 | **+12 / −2** |
| `pyharness/engine.py` | +16 / −4 | **+14 / −2** |
| `tests/unit/test_agent.py` | +47 / −25 | **+32 / −15** |
| `tests/unit/test_agent_loop.py` | +103 / −15 | **+92 / −11** |
| `tests/unit/test_engine.py` | +74 / −0 | +74 / −0（本就正确） |
| `tests/unit/test_session.py` | +85 / −0 | +85 / −0（本就正确） |
| **合计** | +436 / −87 | **+436 / −87（不变）** |

一并修正：§1 概览行「11（**6** 源 + 4 测试 + 2 文档）」→「11（**5** 源 + 4 测试 + 2 文档）」（5+4+2=11；原写 6 源实为 12，自相矛盾）。
新增 **§1.1 数字口径与勘误**：给出复现命令（带 11 文件 pathspec）、勘误说明、以及**范围界定**（本表=S1 改动集 11 文件；C-2 另加的 `ADD.md`/`EVENT-SCHEMA.md` 为文档登记，不在本表内）。

### 2.2 Gate/结论表述纠正

| 位置 | 追加/修正 |
|---|---|
| §0 结论 | 新增「**⚠️ Gate 状态（勿据此进入 S2）**」：① S1 出口审查 = **CONDITIONAL PASS**（非 PASS）；② S1 Scope Adjudication = **ACCEPT**；③ **C-2/C-3 完成前不得进入 S2**；④ 报告中"并入 S2 前置"之类表述一律以 ADR-019 分流结论为准 |
| §9 出口判据核对 | 重写为**裁定后判据**（①②④⑤ 四条，全部满足；③ 已移出→S1.5）；新增「审查结论」：CONDITIONAL PASS + **S2 前置 NOT MET** + 明示**本报告不含、也不支持"可直接进入 S2"的任何表述** |
| §9 R-9 行 | 「建议作为 S1.5 或**并入 S2 前置**」→ **「移出 S1，承接阶段 = S1.5」**（依 ADR-019：非 S2 硬前置），消除"S2 前置"的误导 |

> 全文复查：`grep -n "S2" S1_CHANGE_REPORT.md` 原仅 R-9 一处涉及，已按 ADR-019 修正；无其他"可进入 S2"类表述。

---

## 3. ⚠️ 治理事件命名冲突（S2 NOT MET 的唯一理由）

### 3.1 冲突事实

本次任务书列出的 4 个新事件名，与冻结记录**不一致**（3/4 不同，且遗漏一个）：

| # | 任务书给出的名称 | **冻结记录中的名称** | 一致？ |
|---|---|---|---|
| 1 | `decision.issued` | `decision.issued` | ✅ |
| 2 | `decision.receipt` | **`receipt.emitted`** | ❌ |
| 3 | `evidence.recorded` | **`evidence.archived`** | ❌ |
| 4 | `governance.audit` | **（不存在此名）** | ❌ |
| 5 | （未列） | **`policy.updated`** | ❌ **遗漏** |

**冻结来源**（权威）：`ARCHITECTURE_DECISION_RECORD.md:22`（§0.1 锁定项）+ ADR-018「契约冻结清单」，并见 `GOVERNED_AGENT_RUNTIME_DESIGN.md:626`——四处一致，均为 `policy.updated` / `decision.issued` / `receipt.emitted` / `evidence.archived`。

### 3.2 本次处置（按任务书的显式规则）

任务书明确：「如果冻结设计中的最终事件名称与上述名称存在差异，**以已经冻结的 ADR / architecture record 为准，不自行创造新名称**」。故本次**登记了冻结名**（4 个：含 `policy.updated`，不含任务书列的另外 3 名），并在 `EVENT-SCHEMA.md` §3.6 写死一条防混淆声明：

> **命名权威**：本组名称以…ADR-018「契约冻结清单」为准。**任何早期草案中的别名（如 `decision.receipt` / `evidence.recorded` / `governance.audit`）不成立**；若见诸他处，以本表为准。

### 3.3 为何这阻塞 S2

- **S2 的交付物正是 `policy.updated`**（ADR §5.2 S2 行：`…; policy.updated 事件`）。任务书的清单**根本没有 `policy.updated`**——意味着人工心智模型与冻结记录在 **S2 自身的事件上**就不一致。
- 若带着该分歧进入 S2，可能出现两种错误：**(a)** 按人工清单实现，则 S2 不产出 `policy.updated`，与 ADR-018 冻结契约冲突；**(b)** 按冻结记录实现，则产出了人工未预期的第 4 个事件。
- 该分歧**不能由文档收口解决**（本阶段的授权只到"登记"，不包含"改冻结契约"）——**必须人工裁定**：确认冻结名有效，或另立 ADR 修订事件名（ADR 只增不改，须新建取代）。

---

## 4. 独立核验（9 项）

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| **1** | `ADD.md` 是否**连续登记** ADR-001~019 | ✅ **连续无跳号** | `grep -o "^\| ADR-[0-9]*" docs/ADD.md` → `ADR-001…ADR-019` 顺序完整（另 2 处命中来自 §2.1 路径表的 `ADR-013 ~ ADR-018` 与 `ADR-019` 行；**索引行恰为 19 条**） |
| **2** | 顶部数量是否为 **19** | ✅ | `docs/ADD.md:5` = "**19 条 ADR** 全部「已接受」(ADR-001~012 定稿…013~019 治理期追加…)" |
| **3** | ADR-013~019 的**文件是否存在** | ✅ 全部存在 | `ARCHITECTURE_DECISION_RECORD.md`（013~018 正文）· `ADR-019-s1-scope-adjudication.md`（019 全文）—— 两者均 `-f` 通过；链接为相对路径 `../…`（自 `docs/` 出发有效） |
| **4** | 是否存在**第二套 ADR Registry** | ✅ **不存在** | `grep -rln "^\| ADR-0" --include="*.md" .` → **仅 `docs/ADD.md`** 一处含 ADR 索引表；§2.1 显式声明「本文件是唯一注册表，禁止另建第二套索引表」 |
| **5** | `EVENT-SCHEMA.md` 是否存在**四个预登记事件** | ✅ 四个齐备 | §3.6 F 组（`docs/EVENT-SCHEMA.md:479` 起）：`policy.updated`(1 处) · `decision.issued`(2) · `receipt.emitted`(2) · `evidence.archived`(1)（多处=表格行 + 不变式行） |
| **6** | `S1_CHANGE_REPORT.md` 数字与 `git diff --numstat` **完全一致** | ✅ **逐项一致** | 脚本比对 11 文件 → **不一致：无**；合计 `+436 / −87` 与报告一致 |
| **7** | `git status` 是否只有**允许的文档修改** | ✅ | 修改 13 项 = S1 的 11 项（含 `docs/MAP.md`/`docs/DIS-CORE.md`）+ 本阶段 `docs/ADD.md`、`docs/EVENT-SCHEMA.md`（**均为文档**）；未跟踪 = 会话文档 + `docs/baseline/` + `tmp/` |
| **8** | 是否有任何 `.py` 被修改（**本阶段**） | ✅ **无** | `git diff --numstat -- '*.py'` → **9 个文件**，与 S1 改动集**完全相同**（tools_guard +12/−2 · engine +14/−2 · agent_loop +52/−11 · agent +11/−10 · session +58/−32 · test_agent +32/−15 · test_agent_loop +92/−11 · test_engine +74/−0 · test_session +85/−0）。本阶段若动过 `.py`，这些数字必变 |
| **9** | 是否修改了 **FROZEN** 的 `ARCHITECTURE_DECISION_RECORD.md` | ✅ **未修改** | 其 mtime = `15:04`，**早于**本阶段全部产物（`ADR-019…` 15:34 · `ADD.md`/`EVENT-SCHEMA.md` 15:37 · `S1_CHANGE_REPORT.md` 15:38）；且本阶段未对其发出任何编辑 |

**附加核验**：`pyharness/governance/` 仍**不存在**（未创建 Governance runtime）；`ADR-019-s1-scope-adjudication.md` 内容未被本阶段改动（mtime 15:34 = 上一阶段产出，本阶段未编辑）。

---

## 5. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 不修改任何 `.py` | ✅ 未修改（核验 #8） |
| 不修改测试 | ✅ 未修改（同 #8） |
| 不执行 W3 | ✅ 未执行 |
| 不拆 `build_runner_components` | ✅ 未拆（`grep -c "def _build_" engine.py` 仍为 0） |
| 不收敛 repair | ✅ 未收敛（`persistence.py:470` 与 `repair.py:380` 仍并存） |
| 不删除 `_invoke` | ✅ 未删（`tools_executor.py:679` 仍在） |
| 不修改 `ARCHITECTURE_DECISION_RECORD.md` | ✅ 未修改（核验 #9） |
| 不修改 ADR-019 历史内容 | ✅ 未修改 |
| 不创建 Governance runtime | ✅ `pyharness/governance/` 不存在 |
| 不创建 Decision Receipt 实现 / Evidence 实现 | ✅ 未创建（仅文档占位登记） |
| 不进入 S2 | ✅ 未进入 |

**本阶段新增/修改文件（均为文档）**：`docs/ADD.md`（修改）· `docs/EVENT-SCHEMA.md`（修改）· `S1_CHANGE_REPORT.md`（修改）· `S1_DOCUMENTATION_CLOSURE.md`（新增）

---

## 6. S2 进入条件判定

### 6.1 前置条件逐项（依 [S1_SCOPE_ADJUDICATION.md](S1_SCOPE_ADJUDICATION.md) §5）

| 前置 | 要求 | 本阶段后状态 |
|---|---|---|
| **P-1** | S0 冻结登记闭合：ADR-013~018（+019）回填 `ADD.md`；`EVENT-SCHEMA.md` 预登记 4 个新事件 | ✅ **已满足**（C-2） |
| **P-2** | S1 门禁闭合（ADR-019 裁定后判据 ①②④⑤） | ✅ 已满足 |
| **P-3** | S2 内必须处理落盘适配器 sync 收敛（`engine.py:641` / `desktop/sessions.py:151`） | ⬜ **S2 执行时的动作**（非进入条件；ADR-019 已写明不得静默略过） |
| **P-4** | S1 范围歧义有正式记录（G-6） | ✅ 已满足（ADR-019） |
| **P-5** | `S1_CHANGE_REPORT.md` 数字修正（F-5/F-6） | ✅ **已满足**（C-3） |
| **P-6（本次新增）** | **治理事件命名冲突经人工确认** | ❌ **未满足**（§3） |

### 6.2 判定

# S2 Entry Condition: **NOT MET**

**理由**：`P-1`~`P-5` **全部满足**（ADR §5.3 串行链 S0→S1 已闭合），但 **`P-6` 未满足**——治理事件命名冲突必须人工裁定；且 `policy.updated` **正是 S2 的交付物**，在名称未确认前进入 S2 会产出人工未预期或与冻结契约冲突的事件。

**解除条件（二选一，均需人工）**：
1. **确认冻结名有效**（`policy.updated` / `decision.issued` / `receipt.emitted` / `evidence.archived`）→ 记录确认后 `P-6` 解除，S2 可启动；
2. **改冻结契约**（若人工确认应为 `decision.receipt` / `evidence.recorded` / `governance.audit` 等）→ 因 ADR 只增不改，须**新建 ADR 取代** ADR-018 的契约冻结清单，同步改 `ARCHITECTURE_DECISION_RECORD` 的**声明式指示**（不改其正文），再重做 C-2.2 的登记。

**另建议（非阻塞）**：进入 S2 前**提交 S0/S1 产出**，建立回退锚点（Exit Review **F-4**）。

### 6.3 P-6 修订记录（Amendment，2026-09-14 追加）

> **本小节为增量修订记录**：**不修改** §6.1/§6.2 的历史结论（两者是裁定前的准确状态），只**追加**人工裁定后的状态。

| 项 | 内容 |
|---|---|
| **原状态（§6.2，裁定前）** | `P-6` = **未满足** → `S2 Entry Condition = NOT MET`；解除条件二选一（确认冻结名 / 改冻结契约） |
| **人工裁定（2026-09-14）** | **以冻结架构契约中的事件名称为唯一权威名称**。最终四名：`decision.issued` · `receipt.emitted` · `evidence.archived` · `policy.updated`。同时确认 `decision.receipt`、`evidence.recorded`、`governance.audit` **均不成立**；**不因任务书中的错误命名修改冻结架构**；**不新建 ADR 修改 ADR-018**；**不修改 `ARCHITECTURE_DECISION_RECORD.md`**。→ 走「解除条件 1」，且裁定**未要求任何文档改动**（C-2.2 已按冻结名登记） |
| **最终状态** | **`P-6` = SATISFIED** → `S2 Entry Condition = MET`（见 [S1_FINAL_EXIT_GATE.md](S1_FINAL_EXIT_GATE.md) §1 与 §2 检查 5） |
| **裁定正式记录位置** | [S1_FINAL_EXIT_GATE.md](S1_FINAL_EXIT_GATE.md) §1；本小节为其在本文（C-2/C-3 报告）中的对应修订 |

**一致性说明**：§6.1 表内 `P-6` 行、§6.2 判定、§7 待确认清单第 1 项均为**裁定前**状态，**保持原貌不改**；其现状以本小节为准。

---

## 7. 停止点

**本阶段到此停止。** 已请求人工确认，不再执行任何后续动作（**不启动 S2**、不执行 W3、不重构、不改代码）。

**待人工确认清单**：
1. **P-6 治理事件命名**：确认冻结名（§3.1 右列）还是改契约（左列）？
2. 是否提交 S0/S1 产出以建立回退锚点？
3. 是否授权启动 S2（须待 1 解除）？

**留证**：本报告所依赖的全部核验命令已在 §4 逐项给出，可独立复跑。

---

## 附：本阶段产物

| 文件 | 动作 | 核验 |
|---|---|---|
| `docs/ADD.md` | 回填 ADR-013~019（顶部 19 条 + §2 索引 7 行 + §2.1 路径表 + §3 落点注） | 核验 #1 #2 #3 #4 |
| `docs/EVENT-SCHEMA.md` | 新增 §3.6 F 组预登记（4 冻结事件）+ §3 引言 A-E→A-F | 核验 #5 |
| `S1_CHANGE_REPORT.md` | §1 数字勘误 + §1.1 口径说明 + §0/§9 Gate 状态纠正 | 核验 #6 |
| `S1_DOCUMENTATION_CLOSURE.md` | 本报告 | — |
