# S1_SCOPE_ADJUDICATION.md — S1 工程范围裁定报告

> **阶段**：S1 Scope Adjudication Gate（S1 ↔ S2 之间的范围裁定）
> **日期**：2026-09-14 ｜ 基线 `0cba75d`（HEAD 未变）
> **依据**：[S1_EXIT_REVIEW.md](S1_EXIT_REVIEW.md) · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) · [REFACTOR_PLAN.md](REFACTOR_PLAN.md) · [S1_CHANGE_REPORT.md](S1_CHANGE_REPORT.md)
> **性质**：**只做范围裁定**——未修改任何 `.py`、未修改测试、未重构、未实现 W3、未收敛 repair、未拆 `build_runner_components`、未删 `_invoke`、未进入 Governance Layer、未创建 Decision Receipt、未创建 Evidence Runtime。
> **产物**：[ADR-019-s1-scope-adjudication.md](ADR-019-s1-scope-adjudication.md)（本裁定的正式 ADR 文本）+ 本报告。

---

## 0. 裁定结论

# S1 Scope Decision: **ACCEPT**
（确认 S1 实施范围为 **S1-01~04 四项**；§5.2 剩余 5 项移出 S1 并按依赖分流）

# S2 Entry Condition: **NOT MET**

**理由摘要**：范围歧义（Exit Review 的 **F-2**）**已由 ADR-019 正式裁定并记录**，故"范围"这一项不再阻塞；但 **S0 冻结登记（F-3）** 与 **报告数字（F-5/F-6）** 两项**本次未处理**（本阶段仅授权范围裁定），且 ADR §5.3 规定 **S0 → S1 → S2 必须串行**、S2 需以 `ADD.md` 中已登记的 ADR 作为契约核对依据（GATE-01）。故 **S2 前置仍不满足**。

**ACCEPT 不是顺着倾向给的结论**：对 5 项逐项做了代码依赖核验（§2），确认**没有任何一项是 S2 的硬前置**，故收窄不产生依赖缺口。唯一与 S2 强相关的「落盘适配器」亦可在 S2 内以 ≈4 行解决（§2.3）。

---

## 1. ADR 编号核验（先验证，不假设 019 可用）

| 核验项 | 命令 / 结果 | 结论 |
|---|---|---|
| `ADD.md` 已登记条数 | `grep -c "^| ADR-" docs/ADD.md` → **12**；末条 `docs/ADD.md:308` = `### ADR-012` | 注册表 = 001~012 |
| 全库是否已有 ADR-013~029 | `grep -rn "ADR-01[3-9]\|ADR-02[0-9]" --include="*.md" .`（排除冻结记录）→ 仅命中本会话文档中"ADR-013+"的**承诺性引用**（设计/计划里的"新 ADR-013+"），**无任何 ADR 定义占用** | 013~029 无实际占用 |
| 013~018 的归属 | 由 `ARCHITECTURE_DECISION_RECORD.md` §2 **预留**（ADR-013~018），但**未回填注册表** | 预留块，不可占用 |
| 头部条数声明 | `docs/ADD.md:5` = "**12 条 ADR** 全部「已接受」" | 与索引一致 |

**裁定取号：ADR-019**（跳过预留块 013~018，避开编号冲突）。**实测 `ADR-019` 在全库零命中**，可用。

> **登记状态如实标注**：ADR-019 **待登记**。本报告**未**回填 `docs/ADD.md`——因为只插入 019 会使注册表出现 `012 → 019` 的**编号断层**（013~018 缺位），比当前"12 条自洽"更糟，且直接违反冻结记录 §0.3「禁止形成两套并行 ADR 注册表」。正确去向 = **C-2**（013~018 与 019 一并回填）。

---

## 2. 逐项独立分析

分析方法：对每一项分别核验 **① 代码依赖（谁在调用/谁在未来会依赖）② 是否 S2 硬前置 ③ 是否影响 Governance Layer 正确性 ④ 可否安全延后 ⑤ 延后去向 ⑥ 是否需单独 ADR**。

### 2.1 W3 — 预算单信号

**事实（实测）**
- `scope.check_budget()`（`scope.py:350-362`）超限抛 `BudgetExhausted`（裸 `Exception`，**非** PyHError）；
- `llm_fallback.BudgetGuard.check`（`llm_fallback.py:381-399`）抛 **`PyHError("BUDGET-EXHAUSTED")`**（**未登记码直构**，同 `BUSY` 先例）；
- 调用点：`BudgetGuard.check(ctx)` 于 `llm_fallback.py:228`（**每请求前置**）与 `:262`（**每次重试前**）——`BudgetGuard` **从不被实例化**，`check` 按类方法调用；
- 落点：`agent_loop._run_engine` 捕 `BudgetExhausted` → `reason="budget"`（`:201`）；捕 `PyHError` → `system.error` + `reason="error"`（`:205`）。
- **可达性**：`_must_stop` 闸3 与 `scope.check_budget()` 均在 `run_turn` **之前**执行，故同一轮内正常路径不可达；**但重试路径可达**——首次尝试已消耗 token 使读数越限，重试前 `BudgetGuard.check` 抛错 → 同一"预算耗尽"被判为 `error` 而非 `budget`。

| 维度 | 判定 |
|---|---|
| S2 硬前置？ | **否**。S2（`PolicyEngine` + `governance` 骨架，ADR-018）不读 run 终态；预算执法仍在 `_must_stop`，不经 guard/decision |
| 影响 Governance 正确性？ | **间接**——S5（Audit/Evidence）读 run 终态做审计视图时才需要它单一化 |
| 可安全延后？ | **是** |
| 延后去向 | **S1.5**（与 S1 同性质：运行时终态分类修复） |
| 需单独 ADR？ | **建议是**。它改变**可观测的终态语义**（`error`→`budget`）并**引入/统一错误码**，属 `ADR-011`（错误码对外契约）域；且 `BUDGET-EXHAUSTED` 目前是未登记码直构（违登记纪律），需一次显式契约决策 |

### 2.2 `build_runner_components` 拆 5 子函数

**事实**：`engine.py` 该函数约 185 行、内联装配 ~18 类组件；`grep -c "def _build_" engine.py` → **0**（未拆）。ADR-013 的「3 处新增接缝」中第 ② 处为 `ctx.governance` 单实例装配点——**落点正在此函数**。

| 维度 | 判定 |
|---|---|
| S2 硬前置？ | **否**。挂 `ctx.governance` **无需**先拆函数——在现有函数内加一段装配即可 |
| 影响 Governance 正确性？ | **否**（装配可达性/可读性，非语义） |
| 可安全延后？ | **是**。风险仅"函数继续增长 ~10 行" |
| 延后去向 | **S2 内顺手做**（S2 必然要改该函数以挂治理上下文，届时按职责切分是自然动作）；若作为**独立重构**则走 `CND-07`（重构/架构变更） |
| 需单独 ADR？ | **否**。纯结构重构、行为不变 |

### 2.3 5 处落盘适配器收敛 —— **本次分析修正了既有结论**

**事实（实测，与先前审计口径不同）**：`bus→store` 落盘订阅确有 **5 处**（`engine._record_to:639`、`cli.py:602-617`、`desktop/sessions.py:137`、`orchestration.py:138-145`、`repair._StoreSink:700`）。**但"各自手写强同步清单"的只有 2 处**：

| 站点 | 强同步清单来源 | 状态 |
|---|---|---|
| `engine.py:641-644` | **手写 `_SYNC = (...)`**（11 项） | ❌ 硬编码 |
| `desktop/sessions.py:151-155` | **手写内联 tuple**（同 11 项） | ❌ 硬编码 |
| `cli.py:611-617` | `from pyharness.events.vocab import SYNC_TYPES as _EVT_SYNC` | ✅ 已单一真源 |
| `orchestration.py:138-145` | `from ... import SYNC_TYPES` | ✅ 已单一真源 |
| `repair._StoreSink` | 显式 `sync=True`（恒强同步） | ✅ 更保守，无须收敛 |

> **修正**：先前 REFACTOR_PLAN §2.3 与 S1_EXIT_REVIEW §2 的表述"5 处各自维护独立 `_SYNC` 元组"**不准确**——准确表述是"5 处订阅站点重复，其中 **2 处**重写了强同步清单"。风险面因此**显著小于**先前判断，收敛成本 ≈ **4 行**（两处改为 `SYNC_TYPES`）。

**与 S2 的关系（关键）**：S2 按设计新增 `policy.updated` 与 `decision.issued`，**两者均要求强同步**（ADR-015/018）。新增仅在 `SYNC_TYPES`（`events/vocab.py:96`）登记后，3 处派生站点**自动生效**；**2 处硬编码站点不会**——若漏改，这两个治理事件在 **engine 与 desktop 两条主生产路径**上将退化为**批量落盘**，崩溃时可能丢失决策事件 → **治理审计链断裂**。

| 维度 | 判定 |
|---|---|
| S2 硬前置？ | **否**（非硬）——S2 也可手动把新事件名补进那 2 个元组，功能可达 |
| 影响 Governance 正确性？ | **是**（若不处理：治理强同步事件在两主路径失去 sync 落盘 → 审计链断裂风险） |
| 可安全延后？ | **移出 S1：是**；**完全不做：否** |
| 延后去向 | **S2 内必修的收纳动作**（成本≈4 行），**不得静默略过** |
| 需单独 ADR？ | **否**。ADR-018 已确立"不得引入第二套"的单一真源纪律，本项是其应用 |

### 2.4 两套 repair 收敛

**事实（实测）**：`persistence.SessionStore.repair`（`persistence.py:470`）与 `repair.repair_session`（`repair.py:380`）并存，语义分歧（前者"隔离"仅内存行号、坏行仍在主文件；后者物理抽离到 quarantine 文件；备份命名亦不同）。**调用点核验**：`store.repair()` 的调用**全部在 `tests/unit/test_persistence.py`（10 处）**，`pyharness/` 与 `scripts/` 中**无生产调用点** → 生产路径走 `repair.repair_session`（F060 管线）。`docs/specs/persistence.py.md` 记载了 repair 的语义（§4 状态机、备份→截断→隔离→重写）。

| 维度 | 判定 |
|---|---|
| S2 硬前置？ | **否**（S2 不触 repair） |
| 影响 Governance 正确性？ | **间接**——治理依赖"repair 后日志一致"；两实现不一致属**既有**隐患，非 S2 引入 |
| 可安全延后？ | **是** |
| 延后去向 | **S1.5 或 S6**（须先与 `docs/specs/persistence.py.md` 对齐，再决定"删除测试专用实现"或"薄委托"） |
| 需单独 ADR？ | **否**（若收敛为删除测试专用实现）；**若改变生产行为则需轻量记录** |

### 2.5 `_invoke` 死代码清理 —— **本次分析修正了既有结论**

**事实（实测）**：`tools_executor._invoke`（`:679`）零调用点。**但它被规格记载**：
- `docs/specs/tools_executor.py.md:75` —— 伪代码 `self._invoke, defn, args, ctx), timeout=...`（spec 的关3 写法）；
- `docs/specs/tools_executor.py.md:180` —— 函数表列出 `def _invoke(defn, args, ctx)`；
- `docs/docs_html/tools_executor.py.html:178,319` —— 同源产出。

即：**实现改走 `_run_provider` 后，`_invoke` 才成为零调用点**。它**不是纯死代码，而是规格漂移的残留**。

> **修正**：REFACTOR_PLAN §4.4 与 §2.3、S1_EXIT_REVIEW §9 的 F-8 把 `_invoke` 归为"死代码清理"**不准确**——其"清理"实质是**规格处置**（更新 spec 以反映 `_run_provider`，或在实现处记录偏离），删除前必须先动 `specs/tools_executor.py.md`。

| 维度 | 判定 |
|---|---|
| S2 硬前置？ | **否** |
| 影响 Governance 正确性？ | **否** |
| 可安全延后？ | **是**（但**不可**按"无害删死代码"处理） |
| 延后去向 | **S2 或 S6**，且**必须先解决 spec 引用**（改 spec 或记录偏离） |
| 需单独 ADR？ | **建议是**（涉规格契约变更；若仅"补记偏离"则可轻量） |

### 2.6 逐项结论汇总

| # | 项 | S2 硬前置 | 影响 Gov 正确性 | 可延后 | 承接阶段 | 需单独 ADR |
|---|---|---|---|---|---|---|
| 1 | W3 预算单信号 | 否 | 间接（S5） | 是 | **S1.5** | 建议是 |
| 2 | 拆 5 子函数 | 否 | 否 | 是 | **S2 内顺手** / CND-07 | 否 |
| 3 | 落盘适配器收敛 | **否**（非硬） | **是** | 移出 S1 是；完全不做**否** | **S2 内必修**（≈4 行） | 否 |
| 4 | 两套 repair 收敛 | 否 | 间接 | 是 | **S1.5 / S6** | 否 |
| 5 | `_invoke` 清理 | 否 | 否 | 是 | **S2 / S6**（先处理 spec） | 建议是 |

**结论：无一项构成 S2 硬前置** → 收窄 S1 不产生依赖缺口。

---

## 3. 裁定依据

1. **范围口径应以"工作性质"划分，而非"文档格子"**。S1-01~04 是**行为修复**（缺陷可复现 → 修复 → 行为级测试证明）；移出的 5 项是**结构重构 / 契约变更 / 规格处置**（验收形态不同）。混作一格是本歧义的根因（ADR-019 背景 §3 源）。
2. **无依赖缺口**（§2 逐项验证）：5 项均非 S2 硬前置。
3. **成本最优**：把结构收敛推到它**本来就要发生的阶段**（S2 改装配函数 / S2 加强同步事件），避免为重构单开一轮。
4. **符合两条冻结纪律**：G-7（"没做"必须是决定——本裁定把每项的承接阶段显式命名）；G-6（以 ADR 记录取代关系，而非沉默漂移）。
5. **不扩大、不缩小**：四项**不增**（未把 5 项并入）；§5.2 的 5 项**不删**（各有承接阶段，非丢弃）。

---

## 4. 本次裁定对既有结论的两处修正

审查中发现先前分析有两处**表述不准确**，在此如实修正（影响的是"风险面估计"，不改变裁定结论）：

| # | 先前表述（出处） | 实测修正 | 影响 |
|---|---|---|---|
| **M-1** | "5 处落盘适配器**各自维护**独立 `_SYNC` 元组"（`REFACTOR_PLAN §2.3`、`S1_EXIT_REVIEW §2`） | **仅 2 处**硬编码（`engine.py:641`、`desktop/sessions.py:151`）；另外 3 处已从 `SYNC_TYPES` 派生 | 风险面**缩小**；收敛成本 ≈4 行（非"5 处统一"） |
| **M-2** | `_invoke` = "死代码"（`REFACTOR_PLAN §4.4`、`S1_EXIT_REVIEW F-8`） | `_invoke` 被 `specs/tools_executor.py.md:75,180` 记载 → **规格漂移残留**，非纯死代码 | 处置方式从"删除"变为"**先处理 spec**"；F-8 的定性需修正 |

> 两处修正**不改变** Exit Review 的判决（CONDITIONAL PASS）与本次裁定（ACCEPT），但**改变了后续动作的定义**：M-1 使该项从"5 处统一重构"降级为"S2 内 4 行收纳"；M-2 使 `_invoke` 从"可随手删"升级为"需先动 spec"。

---

## 5. S2 前置条件清单（哪些是 / 哪些不是）

**是 S2 前置（必须满足）**

| # | 条件 | 当前状态 | 归属 |
|---|---|---|---|
| **P-1** | **S0 冻结登记闭合**：ADR-013~018（+ 本 ADR-019）回填 `docs/ADD.md`（§2 索引 + §4 正文 + 头部 12→19 条）；`EVENT-SCHEMA.md` 预登记 4 个新事件类型占位 | ❌ **未做**（`ADD.md` 仍 12 条） | **C-2** |
| **P-2** | **S1 门禁闭合**（依 ADR-019 裁定后的出口判据 ①、②、④、⑤） | ✅ **已满足**（见 §6.2） | 本裁定 |
| **P-3** | **S2 内必须处理落盘适配器的 sync 收敛**（否则治理强同步事件在两主路径失去 sync 落盘） | ⬜ 未到期（S2 执行时） | **S2 内** |
| P-4 | S1 范围歧义有正式记录（G-6） | ✅ **已满足**（ADR-019） | 本裁定 |
| P-5 | `S1_CHANGE_REPORT.md` 数字修正（F-5/F-6） | ❌ **未做** | **C-3** |

**不是 S2 前置（可延后）**

| 项 | 承接阶段 |
|---|---|
| W3 预算单信号 | S1.5 |
| `build_runner_components` 拆分 | S2 内顺手 / CND-07 |
| 两套 repair 收敛 | S1.5 / S6 |
| `_invoke` 规格处置 | S2 / S6 |
| 基线缺口 B-1（缺 PySide6）、R-8（`agent.submit` ctx 疑点） | 基线遗留，另案 |

---

## 6. 裁定后的门禁状态

### 6.1 S1 门禁（依 ADR-019 裁定后的判据）

| 判据 | 状态 | 证据 |
|---|---|---|
| ① g1 注入 validator 后能拒绝非法参数 | ✅ | `tests/unit/test_engine.py::test_guard_from_config_injects_schema_validator` |
| ② `cfg.security.guards.disabled` 生效 | ✅ | `…::test_guard_from_config_applies_cfg_disabled` |
| ~~③ 预算单信号~~ | **已移出**（→ S1.5，ADR-019 裁定 3） | — |
| ④ 全量回归绿 | ✅ | `tmp/baseline/junit_s1_final.xml`：1452 / **0 failed** / 0 error / 2 skipped |
| ⑤ `AgentLoop` 无 resume + `hasattr` 负断言 | ✅ | `test_agent.py::test_agent_loop_has_no_resume_api`、`test_agent_loop.py::test_loop_state_space_is_reachable_only` |

→ **S1 门禁（范围口径已由 ADR-019 明确）= 满足**。

### 6.2 S2 门禁

**NOT MET** —— 阻塞于 **P-1（S0 登记未闭合）** 与 **P-5（报告数字未修正）**；范围歧义（P-4）已解除，不再是阻塞项。

> 依据 `ARCHITECTURE_DECISION_RECORD.md` §5.3「**必须串行 S0 → S1 → S2 → S3 → S4**」，以及 GATE-01 要求以**既有测试/规格/ADR** 为契约核对依据——S2 实现的 `governance/` 接口（ADR-018 冻结）若未进入 `ADD.md`，则"接口冻结"只存在于会话记录，无法作为实现期的契约依据。

---

## 7. 本阶段的边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 不修改 `.py` | ✅ 未修改（`git status` 无 `.py` 新增改动） |
| 不修改测试 | ✅ 未修改 |
| 不重构代码 | ✅ 未重构 |
| 不实现 W3 | ✅ 未实现 |
| 不实现 repair 收敛 | ✅ 未实现 |
| 不拆 `build_runner_components` | ✅ 未拆 |
| 不删除 `_invoke` | ✅ 未删 |
| 不进入 Governance Layer | ✅ 未进入（无 `pyharness/governance/**`） |
| 不创建 Decision Receipt / Evidence Runtime | ✅ 未创建 |
| 未修改 FROZEN 记录 | ✅ **未编辑** `ARCHITECTURE_DECISION_RECORD.md`（对 §5.2 S1 行的取代关系仅**声明**于 ADR-019） |
| 未新增并行 ADR 冲突注册表 | ✅ **未回填 `ADD.md`**（只插 019 会造 `012→019` 断层，故留待 C-2 与 013~018 一并回填；ADR-019 已声明其登记归属） |
| 未执行 S1.5 代码修复 | ✅ 未执行 |

**本阶段新增文件（均为文档）**：`ADR-019-s1-scope-adjudication.md` · `S1_SCOPE_ADJUDICATION.md`

---

## 8. 后续动作（按序）

| 序 | 动作 | 性质 | 归属 | 阻塞 S2？ |
|---|---|---|---|---|
| 1 | **C-2**：ADR-013~018 + ADR-019 回填 `docs/ADD.md`（索引 + 正文 + 头部条数）；`EVENT-SCHEMA.md` 预登记 4 个新事件占位 | 文档 | 执行 | **是**（P-1） |
| 2 | **C-3**：修正 `S1_CHANGE_REPORT.md` §1 逐文件数字（改 `git diff --numstat` 实值），并在 §9 显式宣告 Gate 口径已由 ADR-019 裁定 | 文档 | 执行 | 否（但建议先做） |
| 3 | 修正 M-1/M-2 的表述（`REFACTOR_PLAN §2.3/§4.4`、`S1_EXIT_REVIEW F-8`） | 文档 | 执行 | 否 |
| 4 | 提交 S0/S1 产出，建立回退锚点（Exit Review F-4） | 过程 | 决策者 | 建议 |
| 5 | 启动 **S2**（治理骨架 + `PolicyEngine`）——**仅在第 1、2 项完成后** | 代码 | 执行 | — |
| 6 | 排期 **S1.5**（W3 + repair 收敛 + `_invoke` 规格处置） | 代码 | 决策者 | 否 |
| 7 | 评估 M-1/M-2 与 W3/`_invoke` 是否需各自独立 ADR | 文档 | 决策者 | 否 |

---

## 附：裁定所依据的关键实测证据

| 事实 | 命令 / 位置 |
|---|---|
| `ADD.md` 12 条、末条 ADR-012 | `grep -c "^| ADR-" docs/ADD.md` → 12；`docs/ADD.md:308` |
| `ADD.md` 头部条数 | `docs/ADD.md:5` = "12 条 ADR" |
| 全库无 ADR-013~029 定义 | `grep -rn "ADR-01[3-9]\|ADR-02[0-9]" --include="*.md" .`（排冻结记录后仅承诺性引用） |
| 硬编码 sync 清单仅 2 处 | `grep -rn "_SYNC\|SYNC_TYPES" pyharness/` → `engine.py:641`、`desktop/sessions.py:151` 手写；`cli.py:611`、`orchestration.py:138` 派生 |
| `BudgetGuard.check` 调用点 | `llm_fallback.py:228`（每请求）、`:262`（每次重试） |
| `BudgetGuard` 未被实例化 | `grep -rn "BudgetGuard(" pyharness/` → 无命中 |
| `SessionStore.repair` 仅测试调用 | `grep -rn "\.repair(" pyharness/ scripts/ tests/` → 仅 `tests/unit/test_persistence.py`（10 处） |
| `_invoke` 被 spec 记载 | `docs/specs/tools_executor.py.md:75,180` · `docs/docs_html/tools_executor.py.html:178,319` |
| `_invoke` 零调用点 | `grep -n "_invoke" pyharness/core/tools_executor.py` → 仅定义处 `:679` |
| `build_runner_components` 未拆 | `grep -c "def _build_" pyharness/engine.py` → 0 |
| 全量回归 | `tmp/baseline/junit_s1_final.xml`：tests=1452 / failures=0 / errors=0 / skipped=2 |
