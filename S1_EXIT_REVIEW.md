# S1_EXIT_REVIEW.md — S1 Exit Review（S1 出口审查）

> **审查对象**：S1「Runtime Integrity Repair」的交付物与工作区状态
> **基线**：`0cba75d`（HEAD 未变）｜ **审查日期**：2026-09-14
> **依据**：[ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) · [REFACTOR_PLAN.md](REFACTOR_PLAN.md) · [S1_CHANGE_REPORT.md](S1_CHANGE_REPORT.md)
> **性质**：**只读审查**——本次未修改任何代码，未进入 S2。
> **方法**：不采信交付报告自述；全部结论由 `git diff` / `git status` / 运行时 `import` 校验 / pytest 实跑 独立复算。

---

## 0. 判决

# **CONDITIONAL PASS**

**一句话理由**：任务书定义的 **S1-01~04 四项全部完成**，每项都有可复现的通过测试，**无谎报**；但**冻结的 ADR §5.2 所定义的 S1 出口判据未全部满足**（5 条中 1 条未达：③ 预算单信号），且 **S0 冻结登记的 3 项只完成 1 项**，故按 ADR 自身的**门禁口径** S1 未闭合、S2 前置不满足。

**通关条件（3 条，见 §10）**：① 就 S1 范围作出人工裁定并**记录**（补做 or 修订 ADR）；② 补齐 S0 登记（ADR 回填 `ADD.md` + `EVENT-SCHEMA` 占位）；③ 修正变更报告的逐文件数字。

---

## 1. 审查方法与证据

| 证据 | 命令/来源 | 用途 |
|---|---|---|
| 改动面 | `git status --short` · `git diff --numstat` · `git diff pyharness/` | 检查越界改动、无关修改 |
| ADR 注册状态 | `grep ADR-013 docs/ADD.md` · `sed -n '5p' docs/ADD.md` | 检查 S0 登记完成度 |
| 词表事实 | `pyharness.events.vocab.is_registered(...)` 运行时校验 | 核验 R-1 |
| 测试通过性 | `pytest -k "<新增/改写用例名>"` + 全量跑 | 核验每项证据 |
| 门禁口径 | `ARCHITECTURE_DECISION_RECORD.md` §5.2（S0/S1 行）、§5.3 | 判定出口 |

---

## 2. 检查 1 — S1 既定任务是否完成

**存在两个"S1 既定任务"口径，且两者范围不同**——这是本次审查最重要的发现。

### 口径 A：任务书（S1-01 ~ S1-04）

| 项 | 内容 | 状态 | 证据（§3 详列） |
|---|---|---|---|
| S1-01 | 修复 `engine.py` 绕过 `GuardChain.from_config`，保 API 兼容、加生产路径测试、验证 `guards.disabled` 生效 | ✅ **完成** | `engine.py:474-483`；2 个测试 |
| S1-02 | 按 ADR-014 删除/隔离 `paused`/`resume` 死态依赖，不实现新暂停系统 | ✅ **完成** | `agent_loop.py:63-70,327-330,391-397`；`agent.py:305-307,404-414`；4 个测试 |
| S1-03 | 修复 F026 连败计数主链失效 | ✅ **完成** | `agent_loop.py:255-261,269-315`；2 个测试 |
| S1-04 | 修复 Agent close 状态一致性 | ✅ **完成** | `session.py:279-337`；2 个测试 |

**口径 A 结论：4/4 完成。**

### 口径 B：ADR §5.2 的 S1 行（原文）

> `ARCHITECTURE_DECISION_RECORD.md:411`
> **工作项**：`M1：W1–W5 五项修复 + engine.build_runner_components 按职责拆分为 5 个子函数 + 5 处重复落盘适配器收敛 + 两套 repair 收敛 + 死代码清理（含 _invoke、死态依赖）`
> **出口判据**：`① g1 注入 validator 后能拒绝非法参数；② cfg.security.guards.disabled 生效；③ 预算单信号；④ 全量回归绿；⑤ AgentLoop 无 resume 且测试改为 hasattr 负断言`

| ADR-S1 工作项 | 状态 | 独立核验 |
|---|---|---|
| W1 接线收口（from_config） | ✅ | 同口径 A 的 S1-01 |
| W2 死态清理 | ✅ | 同 S1-02 |
| **W3 预算单信号** | ❌ **未做** | `llm_fallback.py` 仍抛 `PyHError("BUDGET-EXHAUSTED")`；`scope.py` 仍抛 `BudgetExhausted` |
| W4 F026 | ✅ | 同 S1-03 |
| W5 close 一致性 | ✅ | 同 S1-04 |
| `build_runner_components` 拆分为 5 个子函数 | ❌ **未做** | `grep -c "def _build_" pyharness/engine.py` → **0** |
| 5 处重复落盘适配器收敛 | ❌ **未做** | `engine.py:639 _record_to` 与 `desktop/sessions.py:137 _attach_persistence` 等仍各自独立 |
| 两套 repair 收敛 | ❌ **未做** | `persistence.py:470 SessionStore.repair` 与 `repair.py:380 repair_session` **并存** |
| 死代码清理（含 `_invoke`） | ⚠️ **部分** | 死态已清（S1-02）；**`_invoke` 仍在**（`tools_executor.py:679`） |

**口径 B 结论：工作项 4/9 完成；出口判据 4/5 满足（③ 未达）。**

### 2.1 差异表（为什么两个口径不一致）

| 维度 | 口径 A（任务书） | 口径 B（ADR §5.2） |
|---|---|---|
| 来源 | 用户 S1 指令（后出、更具体） | 冻结的架构决策记录（先出、更宽） |
| 工作项数 | 4 | 9 |
| 出口判据 | 未单独定义 | 5 条 |
| 覆盖关系 | A ⊂ B（A 是 B 的子集：W1/W2/W4/W5） | 含 W3 + 3 项收敛/拆分 + 死代码 |

**裁定**：两者都是"S1 既定任务"，**不能以 A 的完成宣称 B 的完成**。变更报告已如实标注差异（其 §9 把③标为未做并登记 R-9），**但 ADR §5.2 的 S1 行本身未被修订**，故冻结记录与实际执行**脱节**——违反 ADR 自身的 G-6「文档即契约」。

---

## 3. 检查 2 — 每项任务的测试证据

全部 10 个用例**已独立实跑确认存在且通过**（`pytest -k` 命中，全量跑零红）。

| 项 | 用例（文件:行） | 断言要点 | 结果 |
|---|---|---|---|
| S1-01 | `test_engine.py::test_guard_from_config_injects_schema_validator`（新增） | 缺 `path` 的 `fs.read_file` → `evaluate` **reject**；合规 → **allow** | ✅ |
| S1-01 | `test_engine.py::test_guard_from_config_applies_cfg_disabled`（新增） | 越界 `fs.list_dir`：默认 **reject**；`disabled=["g-fs-path"]` 后 **allow** 且 `enabled_guard_ids()` 不含该项 | ✅ |
| S1-02 | `test_agent.py:515::test_agent_loop_has_no_resume_api`（新增） | `not hasattr(AgentLoop, "resume")` | ✅ |
| S1-02 | `test_agent.py:528::test_on_bus_event_approval_granted_does_not_mutate`（改写） | `approval.granted` 不改 loop 状态 | ✅ |
| S1-02 | `test_agent_loop.py:508::test_loop_state_space_is_reachable_only`（新增） | `set(get_args(LoopState)) == {"idle","running"}` | ✅ |
| S1-02 | `test_agent_loop.py:521::test_wake_accepts_from_any_reachable_state`（替换旧 `test_wake_stopping_terminated_busy`） | 可运行态 wake 正常 | ✅ |
| S1-02 | `test_agent_loop.py` `test_must_stop_three_gates_readonly`（改写） | `warn` 不拦、`exhausted` 触发 budget | ✅ |
| S1-03 | `test_agent_loop.py:706::test_turn_failure_streak_terminates_turn_on_single_step_path`（新增） | 3 调用同工具 guard 失败 → **只执行 2 次**；`fail_counts==2` | ✅ |
| S1-03 | `test_agent_loop.py:730::test_turn_failure_streak_not_counted_for_other_failures`（新增） | 超时类失败 → 执行 3 次、`fail_counts=={}` | ✅ |
| S1-04 | `test_session.py:98::test_finished_validation_failure_rolls_back_closed`（新增） | `EVT-100` **且 `_closed is False`** → 重试成功 → 再 append 仍 `EVT-104` | ✅ |
| S1-04 | `test_session.py:126::test_finished_flush_failure_keeps_closed_but_log_empty`（新增） | `PERS-202` 后 `_closed is True` 且 `store._rows == []` | ✅ |

**全量回归**（独立复跑）：**1452 collected / 1450 passed / 2 skipped / 0 failed**，exit 0，34.5 s。相对基线 `0cba75d`（1444/1442/2/0）**+8 例**，与变更报告一致。

**证据充分性判定**：四项修复**均有行为级（非仅"代码存在"）的通过测试**，符合 CORE-01「无执行证据不得声称完成」。

---

## 4. 检查 3 — 是否存在未完成但被误认为完成的项目

| 检查点 | 结论 |
|---|---|
| S1-01 ~ S1-04 是否有谎报 | ❌ **无**。四项均有实跑通过的用例，代码改动与报告描述一致 |
| 是否有"测试替身掩盖"的残留 | ❌ **无新增**；且 S1-02 主动**移除**了 `FakeLoop.resume`（消除既有掩盖），方向正确 |
| 是否有"报告声称完成而实际未做" | ⚠️ **有一处框架性问题**（非谎报，但足以误导）：变更报告 §9 标题为 **"出口判据核对（S1 Gate）"**，读者会理解为"ADR 的 S1 门已通过"；实际该表**列出 6 行**（ADR 只有 5 条判据），并把工作项「F026 连败」混入判据列，且**未在结论中明确宣告"S1 Gate 未通过"** |
| 报告的事实性错误 | ⚠️ **§1 逐文件增删数字有 8/11 行错误**（详见 §4.1）——报告内部与 `git` 不一致 |

### 4.1 变更报告 §1 数字勘误（独立复算）

报告 §1 表的逐文件列取自 `git diff --stat` 的"总变动行数"，被误当作 additions。`git diff --numstat` 实际值：

| 文件 | 报告值 | **实际（numstat）** | 判定 |
|---|---|---|---|
| `pyharness/core/tools_guard.py` | +14 / −1 | **+12 / −2** | ✗ |
| `pyharness/engine.py` | +16 / −4 | **+14 / −2** | ✗ |
| `pyharness/core/agent_loop.py` | +63 / −22 | **+52 / −11** | ✗ |
| `pyharness/core/agent.py` | +21 / −20 | **+11 / −10** | ✗ |
| `pyharness/core/session.py` | +90 / −45 | **+58 / −32** | ✗ |
| `docs/DIS-CORE.md` | +4 / −2 | **+3 / −1** | ✗ |
| `docs/MAP.md` | +3 / −3 | +3 / −3 | ✓ |
| `tests/unit/test_engine.py` | +74 / −0 | +74 / −0 | ✓ |
| `tests/unit/test_agent_loop.py` | +103 / −15 | **+92 / −11** | ✗ |
| `tests/unit/test_agent.py` | +47 / −25 | **+32 / −15** | ✗ |
| `tests/unit/test_session.py` | +85 / −0 | +85 / −0 | ✓ |
| **合计** | **+436 / −87** | **+436 / −87** | ✅（总数正确，逐行错） |

**等级**：文档准确性缺陷（**不影响代码与门禁判定**），但**必须修正**——它落在"证据"栏位，而证据必须可信。

---

## 5. 检查 4 — 是否有超出 S1 范围的修改

| 改动 | 是否超出 S1 范围 | 判定 |
|---|---|---|
| 6 个 `pyharness/**` 源文件 | 否——严格对应 S1-01~04 | ✅ |
| 4 个 `tests/unit/**` 测试文件 | 否——对应四项的测试证据 | ✅ |
| `docs/MAP.md`、`docs/DIS-CORE.md` | **是（阶段错位）**：ADR §5.2 把"`MAP.md`/`DIS-CORE.md` 措辞修正"列为 **S0 交付物 ②**（`ARCHITECTURE_DECISION_RECORD.md:410`），本次在 S1 内完成 | ⚠️ **错阶段但无害**——内容正确、为 ADR-014 第 5 项所要求；建议在记录口径上归入 S0 |

**逐行复核**：`git diff pyharness/` 全文已阅——所有 hunk 均落在四项之内（`LoopState`/`F026`/`_must_stop`/`wake`、`_on_bus_event`/`close`、`_closed` 回滚、`from_config` 注入位 + engine 装配）。**未发现任何越界代码改动。**

**未进入 Governance Layer**（自查 + 复核）：无 `pyharness/governance/**`（`git status` 无该路径）；未新增事件类型（词表仍 **73** 型，运行时校验）；未动 3 处接缝。

---

## 6. 检查 5 — R-1 / R-7 / R-8 / R-9 登记审查

| # | 登记内容 | 独立核验 | 判定 |
|---|---|---|---|
| **R-1** | `guard.disabled` 事件不在 73 型词表 → `disable()` 审计留痕降级为本地日志 | **事实正确**：运行时 `is_registered("guard.disabled") is False`，词表 73 型；`guard.evaluated`/`guard.rejected` 均为 True | ✅ **登记正确** |
| **R-7** | `desktop_native/**` 因缺 PySide6 不可测 | **事实正确**；且**明确标注为"基线遗留（B-1），非 S1 范围"** | ✅ **登记正确**（未冒充 S1 残留） |
| **R-8** | `agent.submit` 首次唤醒疑缺 `ctx`（基线 V-1） | **事实正确**：`agent.py` 的 `submit` 调 `loop.wake(env)` 不传 ctx；`_ctx` 仅在 `run()` 内绑定。标注"S1 未处理，仍待核实"，未结论化 | ✅ **登记正确**（诚实，未越界下结论） |
| **R-9** | 预算两套信号未收敛（`BudgetExhausted` vs `PyHError("BUDGET-EXHAUSTED")`） | **事实正确**：`llm_fallback.py:392` 直构 `PyHError("BUDGET-EXHAUSTED")`；`scope.py:359` 抛 `BudgetExhausted`。报告标注**"未做"**并建议 S1.5 或并入 S2 前置 | ✅ **登记正确** |

**登记质量评价**：四条**登记准确、分级合理、未夸大也未隐瞒**。特别是 **R-7/R-8 被正确归为"基线遗留 / 不在 S1 范围"而非本阶段缺陷**，避免了把既有问题算作 S1 成果或 S1 罪责。**唯一可改进**：R-9 与 ADR §5.2 的 ③ 是同一件事，报告未把它与 **S1 出口判据未达**这一门禁结论显式挂钩（仅在 §9 表中标 ⏸）。

---

## 7. 检查 6 — 工作区是否存在与 S1 无关的修改

**已修改（tracked，11 个）**：全部与 S1 相关（见 §5 逐项判定）。

**未跟踪（untracked，6 项）**：

| 路径 | 性质 | 与 S1 的关系 |
|---|---|---|
| `REFACTOR_PLAN.md` | 审计阶段产物（S-1） | 无关（先前阶段） |
| `GOVERNED_AGENT_RUNTIME_DESIGN.md` | 设计阶段产物 | 无关 |
| `ARCHITECTURE_DECISION_RECORD.md` | 冻结阶段产物（S0） | 无关（S0） |
| `S1_CHANGE_REPORT.md` | **S1 交付物** | 相关 |
| `docs/baseline/`（4 篇） | 基线阶段产物（S0 前置） | 无关 |
| `tmp/` | 测试原始留证 + 早前临时文件 | 部分相关（S1 证据） |

### 7.1 ⚠️ 关键发现：**S0/S1 全部产出未提交，无 VCS 锚点**

- `git rev-parse HEAD` = **`0cba75d`（与基线一致，未前进）**；
- 全部 S0/S1 成果（3 份设计文档 + 基线四件套 + S1 报告 + 11 文件改动）**均未 commit**；
- 影响：① 变更报告声称的回退方式（"`git checkout` 上述文件"）**对 tracked 文件有效**，但对未跟踪的交付物**无历史可回**；② S1 与 S0 的产出边界只能靠文档描述区分，**无提交粒度可证**；③ 进入 S2 后一旦继续叠加，回退粒度将变粗。

**判定**：不构成"无关修改"，但构成**过程风险**，应作为通关条件之一（§10 条件 ③）。

---

## 8. 检查 7 — S1 是否满足进入 S2 的前置条件

ADR §5.3 明确规定：**"必须串行：S0 → S1 → S2 → S3 → S4"**。故 S2 的进入条件是 **S0 完成 AND S1 出口判据全达**。

| 前置 | 要求 | 实际 | 满足？ |
|---|---|---|---|
| **S0①** | ADR-013~018 回填 `docs/ADD.md`（索引 + 正文 + 头部条数 12→18） | **未回填**——`grep ADR-013 docs/ADD.md` 无命中；`ADD.md:5` 仍写"12 条 ADR" | ❌ |
| **S0②** | `MAP.md`/`DIS-CORE.md` 措辞修正 | ✅ 已做（在 S1 内完成） | ✅ |
| **S0③** | `EVENT-SCHEMA.md` 预登记 4 个新事件类型占位 | **未做** | ❌ |
| **S1⑤** | 全量回归绿 | ✅ 1450 passed / 0 failed | ✅ |
| **S1①** | g1 validator 生效 | ✅ | ✅ |
| **S1②** | `guards.disabled` 生效 | ✅ | ✅ |
| **S1③** | 预算单信号 | ❌ 未做（R-9） | ❌ |
| **S1④** | `AgentLoop` 无 resume + `hasattr` 负断言 | ✅ | ✅ |
| S1 工作项 | 9 项 | 完成 4 项 | ❌ |

### 8.1 结论：**S2 前置不满足**

1. **S0 未闭合**：3 项中 2 项未做。按 §5.3 串行纪律，S0 未完成时 S1/S2 均不应启动——**S1 实际是"越序启动"**（其 S1-02 顺带完成了 S0②）。
2. **S1 出口判据未全达**：③ 未做。
3. **ADR 自身即写明**："Phase 0 未完成前不得开始 Phase 2（否则错误语义被固化）"——而 S2（治理骨架 + `PolicyEngine`）正是"Phase 2"的落地。
4. **额外约束**：S2 要实现 ADR-018 冻结的 `governance/` 接口契约；若 ADR-013~018 未进 `ADD.md` 权威注册表（S0①），则"接口冻结"**只存在于会话记录中**，无法作为实现期契约核对依据（GATE-01 要求核对"既有测试/规格/**ADR**"）。

---

## 9. 缺陷清单（按严重度）

| # | 缺陷 | 级别 | 影响 | 证据 |
|---|---|---|---|---|
| **F-1** | **S2 前置不满足**：S0 未闭合（S0①③ 未做）+ S1 出口判据 ③ 未达 | **P0（门禁）** | 按 §5.3 串行纪律不得进入 S2 | §8 |
| **F-2** | **ADR §5.2 的 S1 行与实际执行脱节**（9 工作项做了 4；未修订冻结记录） | **P1（治理）** | 违反 G-6"文档即契约"；冻结记录不再描述现实 | §2.1 |
| **F-3** | **S0① 未做**：ADR-013~018 未回填 `docs/ADD.md`（仍"12 条 ADR"） | **P1（治理）** | ADR 权威注册表缺失 6 条；且 `ARCHITECTURE_DECISION_RECORD §0.3` 自己规定"禁止形成两套并行 ADR 注册表"——**当前正处该状态** | `ADD.md:5`；`grep ADR-013` 无命中 |
| **F-4** | **S0/S1 全部产出未提交**（HEAD 仍 `0cba75d`） | **P1（过程）** | 无 VCS 锚点；未跟踪交付物无回退历史 | `git rev-parse HEAD` |
| **F-5** | **变更报告 §1 逐文件数字错误**（8/11 行；总数正确） | P2（文档） | 证据栏位不可信 | §4.1 |
| **F-6** | 变更报告 §9 标题"出口判据核对（S1 Gate）"未宣告门禁未通过；表内混入工作项（6 行 vs ADR 5 条） | P2（文档） | 易被读作"S1 Gate 已通过" | `S1_CHANGE_REPORT.md` §9 |
| **F-7** | `docs/MAP.md`/`DIS-CORE.md` 改动属 **S0②** 交付物却在 S1 完成 | P3（过程） | 阶段归属不清 | `ARCHITECTURE_DECISION_RECORD.md:410` |
| **F-8** | `_invoke` 死代码未清（ADR-S1 工作项列明"含 `_invoke`"） | P3（代码整洁） | 与 ADR-S1 工作项清单不符 | `tools_executor.py:679` |

**无 P0 级代码缺陷**：四项修复本身正确、有测试、无回归。

---

## 10. 判决与通关条件

### 判决：**CONDITIONAL PASS**

**通过的依据**：
- 任务书 S1-01~04 **四项全部完成**，每项均有**行为级**通过测试（10 例，独立实跑确认）；
- **无谎报、无替身掩盖残留**（且主动移除了既有的 `FakeLoop.resume` 掩盖）；
- **无越界代码改动**；未进入 Governance Layer（词表仍 73 型，无 `governance/`）；
- 全量回归 **1450 passed / 0 failed**，相对基线 +8 例；
- 残留登记（R-1/R-7/R-8/R-9）**准确、分级合理、未夸大未隐瞒**。

**不能判 PASS 的依据**：
- **F-1**：按冻结文档自身的门禁口径，S1 出口判据 ③（预算单信号）未达，工作项 9 项完成 4 项；
- **F-3**：S0 冻结登记未闭合，`ADD.md` 仍为 12 条 ADR——**当前正处于 `ARCHITECTURE_DECISION_RECORD §0.3` 明令禁止的"两套并行 ADR 注册表"状态**；
- 故 **S2 前置不满足**（§8）。

### 通关条件（3 条，缺一不可）

| # | 条件 | 动作 | 属谁 |
|---|---|---|---|
| **C-1** | **就 S1 范围作出人工裁定并记录**——二选一：**(a)** 补做 ADR-S1 剩余工作项（W3 预算单信号 + `build_runner_components` 拆分 + 落盘适配器收敛 + 两套 repair 收敛 + `_invoke` 清理），作为 **S1.5**；**(b)** 修订 ADR §5.2 的 S1 行，把实际范围缩为四项、其余移入命名阶段。**注意 ADR 只增不改 → (b) 须新建 ADR 取代或经人工 Review 记录**（`ARCHITECTURE_DECISION_RECORD §0.3` / `ADD.md:35`） | 人工 Review + 文档 | 决策者 |
| **C-2** | **补齐 S0 登记**：① ADR-013~018 回填 `docs/ADD.md`（§2 索引 + §4 正文 + 头部 `12 条`→`18 条`）；② `EVENT-SCHEMA.md` 预登记 4 个新事件类型占位。完成后 S0 才闭合、S2 才有可核对的接口契约 | 文档改动 | 执行 |
| **C-3** | **修正 `S1_CHANGE_REPORT.md` §1 逐文件数字**（改用 `git diff --numstat` 实际值），并在 §9 显式宣告"S1 Gate **未通过**（③ 未达）；S2 前置不满足" | 文档改动 | 执行 |

**另建议（非阻塞）**：④ 在进入 S2 前**提交 S0/S1 产出**（至少 S1 的 11 个文件改动 + 报告），建立回退锚点（F-4）；⑤ 修正 F-6/F-7/F-8。

### 通关后预期
C-1 + C-2 完成后，S1 门禁可重评为 **PASS**，且 S2 前置（S0 闭合 + S1 判据全达）随之成立。

---

## 附：证据索引

| 证据 | 路径/命令 |
|---|---|
| 改动面 | `git status --short` · `git diff --numstat` · `git diff pyharness/`（全文已阅） |
| HEAD | `git rev-parse HEAD` → `0cba75db...`（= 基线） |
| ADR 注册 | `grep "ADR-013" docs/ADD.md` → 无命中；`sed -n '5p' docs/ADD.md` → "12 条 ADR" |
| 词表/R-1 | `is_registered("guard.disabled") is False`；词表 73 型 |
| 死代码 | `grep -n "_invoke" pyharness/core/tools_executor.py` → `:679` |
| 两套 repair | `persistence.py:470` · `repair.py:380` |
| 拆分未做 | `grep -c "def _build_" pyharness/engine.py` → `0` |
| 全量回归 | `tmp/baseline/junit_s1_final.xml` → tests=1452 / failures=0 / errors=0 / skipped=2 |
| 新增/改写用例 | 10 例，见 §3；`tests/unit/{test_engine,test_agent,test_agent_loop,test_session}.py` |
| 门禁口径 | `ARCHITECTURE_DECISION_RECORD.md:410`（S0 行）· `:411`（S1 行）· §5.3 |
