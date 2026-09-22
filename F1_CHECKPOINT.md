# F1 Checkpoint — Stage Freeze

> 阶段：F1（Core Runtime Path Audit）阶段冻结
> 性质：**只读审查与记录**。本次未修改任何生产代码、测试、specs、INV registry、Protocol、Methodology；未 commit、未 push。
> 上游文档：`F1-R1C_FINAL_REVIEW.md`（D1d-A 收口）· F1-R1A / F1-R1B 阶段报告
> 说明：本文档只**整理已发现的问题**，不重新扫描代码、不新增 finding。

---

## 1. F1 当前阶段状态

| 阶段 | 状态 |
|---|---|
| F1（Core Runtime Path Audit，入口 → 主链闭环验证） | **CLOSED**（CLOSED_LOOP_CONFIRMED） |
| F1-R1A（D1 Runtime Output Contract Fix） | **CLOSED** |
| F1-R1B（D1b Approval Runtime Contract Fix） | **CLOSED** |
| F1-R1C-0（D1d Root-Cause Investigation） | **CLOSED** |
| F1-R1C-1（D1d-A Minimal Fix） | **CLOSED** |
| F1-R1C Final Review | **CLOSED** |
| F1 Checkpoint / Stage Freeze | **本次执行** |

**冻结声明**：F1 阶段自本 Checkpoint 起进入冻结态。任何未在 §4 列出的工作，均需先取得**独立范围授权**方可开展。

---

## 2. F1-R1A / R1B / R1C 状态

### 2.1 F1-R1A — D1

| 项 | 内容 |
|---|---|
| 目标 | 恢复 CLI 事件渲染的 canonical payload 形状 |
| 生产改动 | `pyharness/cli.py`：`_normalize_payload` 解包 Envelope；`_text_card` 的 `task.completed` 增补 `reason` |
| 测试改动 | `tests/unit/test_cli.py`：`_feed` 改由真实 `SessionLog` 投递 Envelope；卡片断言绑定真实字段 |
| 验证 | 真产品 `run` 改前 `[工具] ? {}` / `[结果] None` → 改后真实工具名与结果；`--json` 恢复 `{"type":…,"payload":<内层载荷>}`；mutation M1/M2/M3 均被检出 |
| 状态 | **CLOSED** |

### 2.2 F1-R1B — D1b

| 项 | 内容 |
|---|---|
| 目标 | CLI 审批提示取得 canonical approval identity；结果事件不得触发人工审批 |
| 生产改动 | `pyharness/cli.py`：`prompt_approval` 增加事件类型门；`aid` 取自 `Envelope.seq` |
| 测试改动 | `tests/unit/test_cli.py`：`TestPromptApproval` 改用真实 Envelope；新增结果事件参数化用例（3）；新增 `TestApprovalLiveChain`（2） |
| 验证 | 真 tty APPROVE / DENY / TTL 三场景 JSONL 判据全部满足；mutation M1/M2/M3 均被检出 |
| 状态 | **CLOSED** |

### 2.3 F1-R1C — D1d-A

| 项 | 内容 |
|---|---|
| 目标 | `/exit` 不依赖 `ctx.agent` 即可终止 interactive loop |
| 生产改动 | `pyharness/core/commands.py`：`cmd_exit` 的 `ctx.agent` 由**退出闸门**改为**尽力而为的终态留痕**（功能性改动 1 行） |
| 测试改动 | `tests/unit/test_cli.py`：新增 `TestExitLifecycleRealWiring`、`TestExitFinalFlushPersistence`（不注入 FakeAgent） |
| 验证 | 真产品 `/exit` 后进程退出；`_flush_session` marker 直接观测；JSONL 17/17 零缺失；replay 历史完整；mutation M1 → 2 failed；全量 1712/0/0/2 |
| 状态 | **CLOSED** |

---

## 3. Closed Findings

| ID | 名称 | 根因 | 证据状态 | 状态 |
|---|---|---|---|---|
| **D1** | CLI 实时事件渲染层级错误 | `SessionLog._dispatch` 投递 Envelope，而 `_normalize_payload` 未解包内层 `payload`；渲染面按错误层级取值 | 真产品运行 + `--json` 形状 + mutation 三向检出 | **CLOSED**（F1-R1A） |
| **D1b** | CLI 审批提示 runtime contract | ① identity 取自载荷（`approval_id`/`id`），canonical 实为 `Envelope.seq`；② `startswith("approval.")` 通配分发使结果事件也进入提问分支 | 真 tty 三场景 JSONL 判据 + mutation 三向检出 | **CLOSED**（F1-R1B） |
| **D1d-A** | `/exit` 生命周期闸门 | `cmd_exit` 以 `ctx.agent is None` 充当**退出闸门**，而生产 CLI 路径从不装配 `ctx.agent` → 退出旗标永不置位 → loop 永不返回 → `cli_main` finally 的 `_flush_session` 不执行 | 12/12 证据项（见 Final Review §13.1），含 marker 级前后对照 | **CLOSED**（F1-R1C） |

---

## 4. Deferred Findings

> **DEFERRED ≠ 已修复。** 以下全部为**未处理**项，仅完成记录与隔离。

| ID | 名称 | 类别 |
|---|---|---|
| **D1d-B** | Failure Visibility：`ctx.render is None` 使失败信息静默丢弃 | 可用性 / 可观测性 |
| **D1d-C** | Dead Configuration：`log.jsonl.flush_interval_s` 无消费者；docstring 所称"引擎定时 flush 任务"不存在；丢失窗口 ≤ `flush_batch`(64) 条 | 持久化可靠性 |
| **D1c** | 重复 `ApprovalProvider` 订阅 → 合法裁决被记 `system.error(APR-503)` 幽灵事件 | 审计事实污染 |
| **NEW-CMD-001** | 斜杠 `/new` 在全部壳中静默 no-op | 命令可用性 |
| **SPEC-DRIFT-001** | commands 规格与实现不一致（规格本身编码了缺陷闸门） | 契约/文档漂移 |
| **X2** | `_render_events` 以 `startswith("approval.")` 通配分发（分发设计） | 架构风险 |
| **X3** | CLI 裁决身份为通道级（`by="cli"`），无用户归属 | 身份模型 |
| **X4** | 陈旧审批作答可能产生 `system.error(APR-503)` | 边界行为（见下方注） |
| **X5** | `_pending` 以 seq 为键，结构上允许单 provider 承载多会话并产生 seq 碰撞 | 架构风险 |
| **X6** | desktop（拉取）与 CLI/ACP（推送）取同一 identity 的路径分歧 | 架构风险 |
| **X7** | `__all__` / specs 文档同步面 | 文档 |
| **D2** | 正常控制流（TLB-802 查重）被记为 ERROR，污染真实 stderr | 可诊断性 |
| **D3** | `budget` / `stats` 实测 BROKEN（`_scan_usage` 无单文件容错） | 功能可用性 |
| **D4** | 装配层注释漂移（工具数量 18 vs 实测 19） | 文档 |
| **D5** | `build_runner_components` 内审批通道硬编码 `channel="desktop"` | 装配风险 |

**关于 X4 的注**：F1-R1B 已关闭其中「结果事件触发提问」这一路径；残余的「TTL 与用户作答竞态」未经验证，故仍列 DEFERRED（**状态澄清，非新增 finding**）。

**关于 X1 的说明**：X1（`cli.py` 中死分支 `payload.get("id")`）**已随 D1b 的修复一并消除**（本次核验：`grep 'payload.get("id")' pyharness/cli.py` 无命中）。故 X1 不在上表 DEFERRED 之列，其状态记为本表下方的“随 D1b 连带消除”，不单独回归验证。

**F0 阶段更广清单的处理**：F0（Runtime Surface Mapping）另有独立发现面（dead feature 群、桌面层死簇、租户隔离正则、默认 pytest collection error、全域 E2E=0 等），**保持在其原阶段报告中**，本次不重列、不重扫。

---

## 5. Finding Ledger

| ID | 名称 | 根因（如已确认） | 证据状态 | 状态 | 可否进入下一阶段 | 当前代码修改授权 |
|---|---|---|---|---|---|---|
| D1 | CLI 渲染层级错误 | `_normalize_payload` 未解包 Envelope | 已确认 + 真产品 + mutation | **CLOSED** | 是（已关闭，无需再入） | 已用尽（F1-R1A） |
| D1b | 审批提示 identity/事件门 | 取错容器 + 通配分发 | 已确认 + 真 tty 三场景 + mutation | **CLOSED** | 是 | 已用尽（F1-R1B） |
| D1d-A | `/exit` 生命周期闸门 | `ctx.agent` 被当作退出闸门 | 已确认 + 12/12 证据项 | **CLOSED** | 是 | 已用尽（F1-R1C） |
| D1d-B | render None 静默失败 | 已确认（`ctx.render is None` → 静默 return） | 代码级确认；未单独回归 | **DEFERRED** | 需独立立项 | **无** |
| D1d-C | flush 定时消费缺失 | 已确认（`flush_interval_s` 无读取点） | 静态确认；影响面已量化（≤63 条） | **DEFERRED** | 需独立立项 | **无** |
| D1c | 重复 ApprovalProvider 订阅 | 已定位（`cli.py` `_wire_queue` + `engine.py` 装配各建一个，同一总线） | 代码路径确认 + 三场景幽灵事件实测 | **DEFERRED** | 需独立立项 | **无** |
| NEW-CMD-001 | `/new` 静默 no-op | 已确认（与 D1d-A 同源：`ctx.agent` 恒 None） | 静态确认（全壳均未装配 `ctx.agent`） | **DEFERRED** | 需独立立项 | **无** |
| SPEC-DRIFT-001 | 规格/实现漂移 | 已确认（规格 `commands.py.md` 仍写旧闸门） | 行级定位（:205-207） | **DEFERRED** | 需独立人工 review | **无** |
| X2–X3, X5–X7 | 见 §4 | 部分已定位 | 静态确认 | **DEFERRED** | 需独立立项 | **无** |
| X4 | 陈旧作答 → APR-503 | 部分已缓解 | 部分验证 | **DEFERRED** | 需独立立项 | **无** |
| X1 | 死分支 `payload.get("id")` | — | 已消除（grep 无命中） | **RESOLVED（随 D1b 连带）** | 不适用 | 不适用 |
| D2–D5 | 见 §4 | 已确认（F0/F1 结转） | 各自见原报告 | **DEFERRED** | 需独立立项 | **无** |

**Ledger 口径**：`当前代码修改授权 = 无` 表示该 finding **不得**在未取得新范围授权的情况下被修改，包括"顺手修"。

---

## 6. Scope Boundaries

### 6.1 NEW-CMD-001 与 D1d-A 的边界

| 维度 | D1d-A | NEW-CMD-001 |
|---|---|---|
| 共同根源假设 | `ctx.agent is None` | `ctx.agent is None`（同源） |
| 影响面 | `/exit` **生命周期终止** + `cli_main` finally 的 final flush + 尾部事件持久化 | 目前**仅证明** `/new` 静默 no-op（会话未切换、无反馈） |
| 后果等级 | 事件真源不完整（触及 INV-01 意图面） | 静默无操作（无数据损坏、无生命周期异常） |
| 状态 | **CLOSED** | **DEFERRED** |
| 修复合并性 | — | **不得与 D1d-A 合并为同一修复任务** |

**记录**：`NEW-CMD-001` 是 **`/new` command lifecycle / command availability** 问题；两者**共享根源假设**，但**后果与验证口径完全不同**，必须独立立项。

### 6.2 SPEC-DRIFT-001 的边界

```
SPEC-DRIFT-001 = implementation / specification drift
```

**本次不修改**（且未修改）：

| 不得修改项 | 原因 |
|---|---|
| `docs/specs/commands.py.md` | 契约面；其修订须与 `ctx.agent` 长期口径一并决定 |
| Protocol v0.3 | 冻结态；本阶段无授权 |
| INV registry | canonical 面；须走独立评审 |
| canonical invariant | 同上 |
| Methodology | 冻结态；本阶段无授权 |

**结论**：上述全部**等待独立人工 review**，本 Checkpoint 不得代为处理。

---

## 7. Specification Drift

| 项 | 内容 |
|---|---|
| ID | `SPEC-DRIFT-001` |
| 位置 | `docs/specs/commands.py.md:203-209`（`cmd_exit` 伪码块），关键行为行 **:205-207** |
| spec 原描述 | `if ctx.agent is None: return "当前外壳不支持 /exit"` → `await ctx.agent.finish_session(...)` |
| 当前实现 | `if ctx.agent is not None: await ctx.agent.finish_session(...)`（best-effort）→ 随后无条件走 `ctx.shell.request_exit(0)` |
| 差异性质 | **行为性差异**（非注释/格式）；规格本身将该闸门编码为规范行为，即**缺陷的源头** |
| 关联 | 同文件 `:181` 附近的 `cmd_new` 段亦含同类编码（见 NEW-CMD-001） |
| 本阶段处置 | **只记录，不修改** |
| 建议 | 与 NEW-CMD-001 合并为一次「commands 契约面修订」人工 review |

---

## 8. Protocol Freeze Status

| 项 | 状态 |
|---|---|
| Protocol v0.3 | **FROZEN**（本次未修改） |
| 本阶段是否发生自我升级 | **否** |
| 本阶段是否触发 CND | **否**（CND 仅自然触发） |
| 缺口记录方式 | 只记录 PIC（finding），不改协议 |
| INV registry / canonical invariant | **未修改** |
| Methodology | **未修改** |

**声明**：F1 阶段新增的两个未决项（NEW-CMD-001、SPEC-DRIFT-001）**不构成协议修改理由**；协议冻结状态在本次 Checkpoint 中保持不变。

---

## 9. Working Tree Status

### 9.1 修改文件与归属

| 文件 | 归属 | mtime |
|---|---|---|
| `pyharness/cli.py` | F1-R1A（D1）+ F1-R1B（D1b） | 14:22:47 |
| `pyharness/core/commands.py` | F1-R1C-1（D1d-A） | 19:57:40 |
| `tests/unit/test_cli.py` | F1-R1A + R1B + R1C-1 累计 | 19:58:59 |

`git diff --stat`：3 files changed, 315 insertions(+), 46 deletions(-)。

### 9.2 未跟踪文件

| 文件 | 来源 |
|---|---|
| `F1-R1C_FINAL_REVIEW.md` | 本阶段（F1-R1C）交付文档 |
| `docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md` | **F1 开始前既存**，非本次产生 |
| `docs/governance.html` | **F1 开始前既存**，非本次产生 |

### 9.3 意外修改 / 泄漏 / 进程检查

| 检查项 | 结果 |
|---|---|
| 是否有意外修改（本 Checkpoint 期间写入仓库） | **无**（全部 py 文件 mtime 早于本次审查） |
| 修改文件是否越界 | **无**（仅上述 3 个，均在 F1 已授权范围内） |
| 临时诊断进程残留 | **0** |
| 用户桌面进程 | 1 个 `pyharness-desktop.exe`，**未受影响** |
| 本机绝对路径泄漏（本机路径 / 用户名） | 3 个未跟踪文档**全部干净**（`F1-R1C_FINAL_REVIEW.md` / Methodology / governance.html） |
| 系统 TEMP 诊断产物 | 15 个（wrapper / driver / mutation / 日志），**全部位于仓库之外**，对仓库无影响 |
| commit / push | **0 / 0**（HEAD 仍为 `d2ccd45`） |

### 9.4 未提交状态说明

F1 累计工作（R1A/R1B/R1C）**尚未提交**。是否提交、以何种粒度提交，属**下一阶段决策**，本次 Checkpoint 不代为决定，也未执行任何 git 写操作。

---

## 10. Next-Stage Entry Conditions

进入任何下一阶段（无论 F1-R1D、F1-R1C-2 或其他）前，必须满足：

| # | 条件 |
|---|---|
| 1 | 明确**单一** finding 作为阶段目标（不得捆绑：D1d-B / D1d-C / D1c / NEW-CMD-001 各自独立立项） |
| 2 | 明确该阶段的**授权文件范围**（生产文件 + 测试文件逐一列出） |
| 3 | 明确**禁止清单**（沿用本 Checkpoint §6 的边界口径） |
| 4 | 重新走 LOAD → UNDERSTAND → IMPACT → PLAN，并通过 GATE-01（READY）后方可实施 |
| 5 | 明确验证口径：至少包含 mutation proof + 真产品入口证据（F1 已建立该标准） |
| 6 | 若涉及 `ctx.agent` 的长期口径：**必须先完成 SPEC-DRIFT-001 的人工 review**，不得以修复驱动规格改写 |
| 7 | 先决定 F1 累计未提交工作的处置（提交 / 保留），再叠加新改动，避免混杂 |

**建议的下一阶段独立主题**（供人工排序，本次不做决定）：

1. **D3**（`budget`/`stats` BROKEN）— 用户可直接感知、修复面最小
2. **D1d-B**（失败可见性）— 与已改文件同源，但须独立授权
3. **NEW-CMD-001 + SPEC-DRIFT-001** — 建议合并为一次「commands 契约面」人工 review
4. **D1d-C**（持久化可靠性）— 影响面较大，需独立设计
5. **D1c**（审计事实污染）— 装配层缺陷，需评估与桌面路径的交互

---

## 附录：本 Checkpoint 的一致性声明

- 本次**未**修改任何生产代码、测试代码、specs、INV registry、Protocol v0.3、Methodology、README。
- 本次**未**修复 NEW-CMD-001 / D1d-B / D1d-C / D1c。
- 本次**未**新增 finding、**未**主动重新扫描仓库（仅对既有结论做事实核对）。
- 本次**未** commit、**未** push。
- **未重新打开 D1d-A**；D1d-A 保持 CLOSED。

```
F1-R1C-0            = CLOSED
F1-R1C-1            = CLOSED
F1-R1C Final Review = CLOSED
D1d-A               = CLOSED

D1d-B               = DEFERRED
D1d-C               = DEFERRED
D1c                 = DEFERRED
NEW-CMD-001         = DEFERRED
SPEC-DRIFT-001      = DEFERRED
X2–X7               = DEFERRED
D2–D5               = DEFERRED
```
