# F34_CHANGE_REPORT

> **性质**：F-34 完整变更报告（代码 + 测试 + 文档闭环 + 一致性收敛）。
> **上游**：`docs/decisions/ADR-022-rejection-feedback-contract.md`（**Accepted**）
> **基线**：`HEAD = ebbb444` · 全量 **1741 collected / 1739 passed / 0 failed / 2 skipped** · schema 77/14/3
> **终值**：全量 **1751 collected / 1749 passed / 0 failed / 0 errors / 2 skipped**（**+10**）· schema **77/14/3 不变** · 覆盖 79%
> **状态**：`DONE`（GATE-04 通过）· **未 commit、未 push**

---

## 1. 修改文件

### 1.1 生产代码（2 文件，**+89 / −5**）

| 文件 | +/− | 职责 |
|---|---|---|
| `pyharness/core/session.py` | **+82 / −5** | reducer（`_fold_history`）：① `guard.rejected` → 配对 tool 消息；② `approval.denied`/`timeout` → 经 `approval_id` 反查 `approval.requested` 配对；③ `approval.requested` 建索引（不入上下文）；④ `paired` 跟踪；⑤ `_drop_unpaired_tool_calls` 终局兜底；⑥ 模块级 `_APPROVAL_REJECT_NOTES` / `_guard_reject_note`（**脱敏**文本） |
| `pyharness/core/tools_executor.py` | **+7 / −0** | APR-501 分支增发 `tool.error`（**复用既有 `_on_error` 通道**） |

### 1.2 测试（1 文件，**+253 / −0**）

| 文件 | 内容 |
|---|---|
| `tests/invariants/test_inv_core.py` | **T-1 ~ T-9 + T-4b = 10 用例** + 本组私有助手 |

### 1.3 文档（7 文件，**+42 / −17**）

| 文件 | +/− | 内容 |
|---|---|---|
| `docs/DIS-CORE.md` | **+26 / −6** | §4.2 reducer 伪码：补齐既有 `tool.error` 漂移 + 新增拒绝配对分支 + 终局兜底；`:5` 权威声明改非耦合表述 |
| `docs/ADD.md` | **+6 / −4** | ADR-022 登记（条数 21→22、索引、§2.1 标题/引言/路径表） |
| `docs/PRD-Core.md` | **+4 / −1** | `:304` 规则修订为"**不以原形**进入，但经派生层生成**配对 tool 消息**" |
| `docs/KEY-FINDINGS.md` | **+3 / −3** | 3 处 ADR 条数耦合引用 → 非耦合表述 |
| `docs/README.md` | **+1 / −1** | 同上（1 处） |
| `README.md` | **+1 / −1** | 同上（1 处，原为陈旧值 20） |
| `GOVERNED_AGENT_RUNTIME_DESIGN.md` | **+1 / −1** | 同上（1 处） |

### 1.4 新建（2 文件）

| 文件 | 说明 |
|---|---|
| `docs/decisions/ADR-022-rejection-feedback-contract.md` | ADR 全文（Accepted；D-1~D-9、Consequences、Migration Impact） |
| `F34_CHANGE_REPORT.md` | 本报告 |

**F-34 合计：10 个已跟踪文件改（+384 / −22）+ 2 个新建。**

---

## 2. 修改原因

### 2.1 缺陷（F-34）

被拒绝的 tool call 在**派生给 LLM 的历史**中失去配对：reducer 只映射 `tool.result` / `tool.error`，而 `guard.*` / `approval.*` 被显式排除；执行器在关 2 reject 与关 2.5 审批非放行时**直接返回 `ExecResult(ok=False)` 且不写任何 tool 事件** ⇒ `assistant.tool_calls` **悬空** ⇒ 同文件两处注释所指的**端点 400**（会话中断）。

**实测**（FV-004 guard 拒绝 / FV-006 审批拒绝，两条路径均复现）：
```
assistant tool_calls ids = {'fv1'} · 配对 tool 消息 ids = 无 · **悬空** = {'fv1'}
```

### 2.2 逐阶段理由

| 阶段 | 改动 | 理由 |
|---|---|---|
| **代码** | `guard.rejected` → 配对 | 该事件 `trace` **直接携带 `call_id`**；拒绝事实已在事件层，只需在**投影层**补配对（E-1/E-2） |
| | `approval.denied`/`timeout` → 反查 | 其 `trace` 只有 `{"kind":"approval.verdict"}`、**不带 `call_id`** ⇒ 须经 `approval_id == approval.requested.seq` 反查 |
| | APR-501 → 复用 `tool.error` | 该路径**不发 `approval.requested`**（零事件零等待）⇒ **无可关联事件**；经**既有**通道补配对：零新事件、不动审计（ADR-022 D-7(a)） |
| | `_drop_unpaired_tool_calls` | 旧日志/反查失败时投影仍可能悬空；兜底保证恒为 provider 合法形态，且**只作用于投影、不动日志**（INV-01） |
| | 脱敏文本 | `SECURITY §6.4`：只含工具名 + `guard_id`/`policy_ref`，**不含参数原文与凭据** |
| **文档闭环** | `PRD-Core.md:304` | PRD-Core 是**唯一权威规格**；ADR-022 的决策须在其成文规则处定型（保持 PRD 权威地位，不改 INV-01/04/05） |
| | `ADD.md` | ADR-022 登记；避免出现"ADR 已存在却未登记"的**两套并行注册表**（S1 的 P-1/C-2 教训） |
| | `DIS-CORE.md` §4.2 | ① 你批准的 `tool.error` **文档漂移修复**；② A+ 的拒绝分支须在同一伪码体现，否则改完文档仍与现实不符 |
| **一致性收敛** | 7 处 ADR 条数 | ADR 条数是**动态量**（本会话已 12→20→21→22），被复写在 7 处无关文档中 ⇒ 每次新增 ADR 都制造漂移（实测 12/20/22 **三个值并存**）。按你的要求改为**非耦合表达**（指向注册表或去量词），**不做数字替换** |

---

## 3. 测试结果

| 口径 | 结果 |
|---|---|
| **新增用例** | **T-1 ~ T-9 + T-4b = 10**，全绿 |
| **全量回归** | **1751 collected / 1749 passed / 0 failed / 0 errors / 2 skipped** |
| 基线 | 1741 / 1739 / 2 |
| **净变化** | **+10**（恰为新增用例）⇒ **零回归** |
| 覆盖率 | 79%（17,962 语句） |
| `EVENT_TYPES / SYNC_TYPES / TRANSIENT` | **77 / 14 / 3 —— 不变** |
| 功能复验 | guard 拒绝后 `配对 tool 消息 ids = {'fv4'}`、**悬空 = 无**；content = `fs.read_file 调用被拒绝:g-fs-path`（脱敏） |

### 3.1 用例清单

| # | 用例 | 判据 |
|---|---|---|
| T-1 | guard 拒绝配对 | 无悬空 + 配对 tool 消息 + content 含 `g-fs-path` 且**不含参数原文** |
| T-2 | 审批 denied 配对 | 经 `approval_id` 反查成功 |
| T-3 | 审批 timeout 配对 | 同 T-2 |
| **T-4** | APR-501 配对（**reducer 侧**） | `tool.error` → 配对 |
| **T-4b** | APR-501（**执行器产出侧**） | 真实执行器在 APR-501 下**确实发出** `tool.error`，Provider 零调用 |
| T-5 | **对照/零回归锚** | 正常工具调用派生历史**同形** |
| T-6 | **审计不变**（INV-05） | `guard.rejected` 事件名/载荷字段集/actor **逐字不变** |
| T-7 | **归属不变**（ADR-021） | 拒绝投影在**本会话**，不出现在另一会话 |
| T-8 | **判据自检** | 拒绝事件缺 `trace.call_id` 时，悬空被**兜底剥离**（证明 T-1 非恒真） |
| T-9 | **反查兜底**（D-8） | `approval.denied` 无对应 `requested` ⇒ **不抛错**、降级剥离 |

> **T-4b 系实施期补测**：设计期 T-4 只覆盖 reducer 侧（合成 `tool.error`），**不覆盖 D-7(a) 的执行器产出** ⇒ 否则 M-C 无法被捕获。

### 3.2 Mutation 结果

| # | 变异 | RED | 结论 |
|---|---|---|---|
| **M-A** | 撤 `guard.rejected` 配对分支 | T-1, T-7 | 有专属鉴别力 |
| **M-B** | 撤 `approval.denied/timeout` 反查分支 | T-2, T-3 | 有专属鉴别力 |
| **M-C** ⭐ | 撤执行器侧 APR-501 `tool.error` 路由 | **仅 T-4b** | **T-4b 的必要性被证明**（T-4 抓不到） |
| **M-D** | 撤终局兜底（`return msgs`） | T-8, T-9 | 有专属鉴别力 |

**4/4 全 RED**，各由其专属用例捕获；还原后 GREEN，`grep MUTATION` = **0**。

> **M-E（脱敏）未执行** —— `_guard_reject_note` **结构上只读 `tool`/`guard_id`/`policy_ref`**，无路径引入参数原文 ⇒ 脱敏是**构造性保证**，非仅断言。

---

## 4. 文档一致性收敛结果

### 4.1 陈耦合引用已清零

`grep -rn "条 ADR"` 现**仅命中 `docs/ADD.md` 自身**（其头部/概述的**权威计数**，应保留）。5 个目标活文档**已全部去除**条数耦合。

| 文件 | 行 | 改后 |
|---|---|---|
| `docs/DIS-CORE.md` | `:5` | `ADD.md 的 ADR 索引(见其 §2)为"为什么"` |
| `docs/KEY-FINDINGS.md` | `:6` | `把 ADR 与 6 条原则讲成"人话"`（去量词） |
| `docs/KEY-FINDINGS.md` | `:30` | `ADD.md 收录的 ADR 同一套论证模板` |
| `docs/KEY-FINDINGS.md` | `:130` | `第 1 节权威来源(ADR 索引与正文,…)` |
| `docs/README.md` | `:11` | `ADD.md(ADR 索引与正文,每条含"违反后果")` |
| `README.md` | `:90` | 同上（原为陈旧值 **20**） |
| `GOVERNED_AGENT_RUNTIME_DESIGN.md` | `:7` | `` `docs/ADD.md` 的 ADR 注册表 `` |

### 4.2 结构完好性

| # | 检查 | 结果 |
|---|---|---|
| 1 | `docs/ADD.md` 注册表 | **22 条索引**、`ADR-001~022` **连续无缺** |
| 2 | 全库相对链接（含本轮 6 个文件） | **断链 = 0** |
| 3 | 文本守卫 / 不变量测试 | **全绿**（`test_events.py` + `tests/invariants/`） |
| 4 | 事件 schema | **77 / 14 / 3 不变** |
| 5 | ADR 文件 ↔ 登记双向核验 | ADR-019/020/021/**022 全部已登记** |

### 4.3 `TECH-ANCHOR.md:106` 正确排除

该处为 `≥8 条 ADR` —— **门槛式表述**，在 22 条时**依然为真**，**非陈旧引用**，故不在收敛范围。

---

## 5. 未修改范围（约束核验）

| 约束 | 核验 | 结果 |
|---|---|---|
| 不新增 event type | `EVENT_TYPES` = **77**（未变） | ✓ |
| 不改变 audit event | `guard.rejected`/`approval.denied` 发射点/强同步/载荷/落点**零改动**；`governance/audit.py` **未改** | ✓ |
| 不改变 `guard.rejected` / `approval.denied` 产生逻辑 | `tools_guard.py` **未改**；`approval.py` **非 F-34 所改**（其改动属**上一轮 Stage 1** 的 F-28 一级） | ✓ |
| 保持 INV-01 | 兜底**只作用于投影**，日志仍 append-only（T-6/T-7） | ✓ |
| 保持 INV-04 / INV-05 | canonical 定义未动；`docs/INVARIANT_REGISTRY.md` **变更 = 0** | ✓ |
| 保持单一真源（E-4） | 零新事件；拒绝事实仍只在既有事件里 | ✓ |
| 保持归属（E-5） | 投影随事件落该会话（T-7） | ✓ |
| **不修改历史 / 冻结文档** | `ARCHITECTURE_DECISION_RECORD.md` · `S1_EXIT_REVIEW.md` · `S1_SCOPE_ADJUDICATION.md` · `REFACTOR_PLAN.md` · `.ai-coding/PROTOCOL.md` · `docs/INVARIANT_REGISTRY.md` —— **全部变更 = 0** | ✓ |

**明确未改**：`governance/audit.py` · `docs/INVARIANT_REGISTRY.md` · `tools_guard.py` · `governance/*` · `events/*`（词表/payload）· `persistence.py` · `.ai-coding/PROTOCOL.md` · `ARCHITECTURE_DECISION_RECORD.md`

---

## 6. 未授权文件核验（**结论：无越界**）

| 类别 | 文件数 | 核验方式 | 结果 |
|---|---|---|---|
| **F-34 授权面** | 10 已跟踪 + 2 新建 | 逐文件扫描 `ADR-022`/`F-34` 标记 | **全部 > 0** ✓ |
| **非本轮（F1 / RT-GOV-01 / Stage 1）** | 11 已跟踪 | 同上，**必须 = 0** | **全部 = 0** ✓ |

⇒ **不存在未授权文件修改**。工作树中另有 **15 个未跟踪文件**（F1×4 / F27×3 / F34_CHANGE_REPORT / 审计与规划报告×3 / RT-GOV-01_REPORT / `docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md` / `docs/governance.html` / `docs/decisions/ADR-022`），其中仅 `ADR-022` 与 `F34_CHANGE_REPORT` 属本轮。

> ⚠️ **`docs/governance.html` 与 `docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md` 未被 `.gitignore` 覆盖**，且**归属 portfolio 仓库、非 PyHarness 资产** ⇒ 提交时**显式列文件**，**禁 `git add -A`**。

---

## 7. 已知残留

| # | 项 | 级别 | 说明 |
|---|---|---|---|
| **R-1** | APR-501 的 `tool.error` 使该路径**新增一次事件发射** | P3 | 仅换出口；判定语义与返回类型不变（ADR-022 D-7(a) 留痕） |
| **R-2** | T-4 与 T-4b 的**分工**须记住 | — | T-4 = reducer 侧；T-4b = 执行器产出侧。二者缺一不可（M-C 的教训） |
| **R-3** | ADR-021 遗留的 `docs/decisions/` 目录约定 | — | ADR-021 的 **A-6** 已裁定"暂不迁移 019/020"；ADR storage convention 待**另立 ADR** |
| **R-4** | **U-1 / U-2 已闭合** | — | `PRD-Core.md:304` 与 `docs/ADD.md` 均已完成（见 §1.3） |

---

## 8. 实施过程中的两次操作事故（如实登记）

### 8.1 F-27 的同类事故（对比）

还原目标误指向"改动前"快照 ⇒ 覆盖回退。**教训**：变异测试的还原目标必须是**改动后**状态。

### 8.2 本轮事故：行尾被静默改写

变异驱动器 `_restore()` 用 `write_text`（默认换行转换）写回。`session.py` / `tools_executor.py` 在工作树中是 **LF**，被转成 **CRLF** ⇒ 还原后与快照**逐字节不一致**（内容与 `git diff --numstat` 均正常）。

**恢复**：以 `newline="\n"` 重写 ⇒ 与快照**逐字节一致**（已核验）。

**教训（与 8.1 并列，值得写入项目经验）**：**本仓库文件行尾不统一**（部分 LF、部分 CRLF）⇒ 还原/重写**必须显式指定 `newline`**（或先探测原文件行尾），否则会静默改写行尾。

---

## 9. 回滚说明

| 级 | 手段 |
|---|---|
| **R-1**（未提交，最常用） | 从 `tmp/f34_post/` 逐文件复制回原位（**必须 `newline="\n"`**）；再撤销 `test_inv_core.py` / `DIS-CORE.md` / `PRD-Core.md` / `ADD.md` / 收敛 5 文件的追加 |
| **R-2**（已提交） | **逆序** `git revert <C2> <C1>` |
| **R-3** | ⚠️ `git reset --hard` 会**一并丢弃**工作树中 F1 / RT-GOV-01 / Stage 1 的未提交改动 ⇒ 仅在确认无其他价值时使用 |

---

## 10. 提交建议（**本阶段不执行**）

### C1 —— `docs:`（8 文件）

```
docs/decisions/ADR-022-rejection-feedback-contract.md   (新建)
docs/PRD-Core.md            (+4 / −1)
docs/ADD.md                 (+6 / −4)
docs/DIS-CORE.md            (+26 / −6)
docs/KEY-FINDINGS.md        (+3 / −3)
docs/README.md              (+1 / −1)
README.md                   (+1 / −1)
GOVERNED_AGENT_RUNTIME_DESIGN.md  (+1 / −1)
```

### C2 —— `fix:`（3 文件）

```
pyharness/core/session.py        (+82 / −5)
pyharness/core/tools_executor.py (+7 / −0)
tests/invariants/test_inv_core.py (+253 / −0)
```

**纪律**：显式列文件 · **禁 `git add -A`**（工作树含前三批未提交改动）· **不署 AI 名** · 提交前**排除** `docs/governance.html` 与 `docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md`。

**F-28 第二级保持独立**，不进入本次提交。

---

## 11. 待你裁定

| # | 项 |
|---|---|
| 1 | 是否按 §10 的 **C1 / C2** 两段式提交 |
| 2 | 是否将本报告纳入 C1 或单独提交（当前为未跟踪） |
| 3 | 是否同轮**评审** F-28 第二级（已确认：同轮评审、独立交付） |
