# F1 Release Preparation

> 性质：提交前**准备与评审**。本次**未执行** commit / push，**未修改**任何生产代码、测试、specs、Protocol、Methodology，**未修复**任何 DEFERRED finding，**未新增** finding。
> 上游：`F1_CHECKPOINT.md`（阶段冻结）· `F1-R1C_FINAL_REVIEW.md`

---

## 1. Change Inventory

### 1.1 总览

| 文件 | 状态 | +/− | 归属阶段 |
|---|---|---|---|
| `pyharness/cli.py` | modified | +30 / −10 | **F1-R1A (D1)** + **F1-R1B (D1b)** |
| `pyharness/core/commands.py` | modified | +10 / −5 | **F1-R1C (D1d-A)** |
| `tests/unit/test_cli.py` | modified | +275 / −31 | **F1-R1A + R1B + R1C 累计** |
| `F1-R1C_FINAL_REVIEW.md` | untracked（新） | — | F1-R1C 交付文档 |
| `F1_CHECKPOINT.md` | untracked（新） | — | F1 阶段冻结文档 |
| `F1_RELEASE_NOTES_DRAFT.md` / `F1_RELEASE_PREPARATION.md` | untracked（新） | — | 本次 Release Prep 交付文档 |
| `docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md` | untracked（**F1 开始前既存**） | — | **非 PyHarness 资产**（冻结） |
| `docs/governance.html` | untracked（**F1 开始前既存**） | — | **归属 portfolio 仓库，非 PyHarness 代码** |

**F1 未新增任何生产文件**（`pyharness/`、`tests/` 下无新增/删除文件）。

### 1.2 按文件 × 阶段 × 目的 × 验证

#### F1-R1A（D1 — 渲染层级）

| 文件 | 变更点 | 目的 | 已验证 |
|---|---|---|---|
| `pyharness/cli.py` | `_normalize_payload`（3 处 hunk） | 识别信封形状并解包内层 `payload`；瞬时类型不受影响 | ✓ |
| `pyharness/cli.py` | `_text_card`（1 处 hunk） | `task.completed` 卡片增补真实完成理由 | ✓ |
| `tests/unit/test_cli.py` | 导入 `TRANSIENT_TYPES` | 供渲染测试区分瞬时/落盘事件 | ✓ |
| `tests/unit/test_cli.py` | `_new_inmem_session(bus=)` | 允许测试经真实 `SessionLog` 投递 Envelope | ✓ |
| `tests/unit/test_cli.py` | `TestRenderEvents`（10 处 hunk） | 事件改由真实 `SessionLog` 投递（与生产同形），卡片断言绑定真实字段 | ✓ |

验证：真产品 `run` 前后对照 + `--json` 形状 + mutation（3 向检出）。

#### F1-R1B（D1b — 审批提示 contract）

| 文件 | 变更点 | 目的 | 已验证 |
|---|---|---|---|
| `pyharness/cli.py` | `prompt_approval`（4 处 hunk） | ① 仅 `approval.requested` 进入提问（结果事件不提问）；② 审批身份取自 `Envelope.seq` | ✓ |
| `tests/unit/test_cli.py` | `_approval_envelope` + `TestPromptApproval` 改写 + 结果事件参数化用例 + `TestApprovalLiveChain` | 替身改真实 Envelope；新增结果事件不提示用例；新增组件级活链用例 | ✓ |

验证：真 tty APPROVE / DENY / TTL 三场景 JSONL 判据 + mutation（3 向检出）。

#### F1-R1C（D1d-A — `/exit` 生命周期）

| 文件 | 变更点 | 目的 | 已验证 |
|---|---|---|---|
| `pyharness/core/commands.py` | `cmd_exit`（2 处 hunk，功能性 1 行） | 会话终态留痕改为 best-effort；外壳退出不再依赖 `ctx.agent` | ✓ |
| `tests/unit/test_cli.py` | 导入 `commands` + `_tmp_cfg` + `TestExitLifecycleRealWiring` + `TestExitFinalFlushPersistence` | 生产装配路径（不注入 FakeAgent）下验证退出旗标与最终落盘 | ✓ |

验证：真 tty `chat` 进程退出 + `_flush_session` marker + JSONL 17/17 + replay + mutation（2 failed）。

### 1.3 验证总账

| 口径 | 结果 |
|---|---|
| 目标测试文件 | 92 passed |
| 全量回归 | 1712 collected / 1712 passed / 0 failed / 0 errors / 2 skipped |
| 相对 F1 前基线 | 1704 → 1712（净增 8 条回归用例） |
| Mutation proof | D1 / D1b / D1d-A 三面均具备鉴别力 |
| 真产品证据 | D1 / D1b / D1d-A 三面均有 |

---

## 2. Commit Strategy Recommendation

### 2.1 拆分可行性（已实测 hunk 分布）

| 文件 | 可否按 finding 拆分 | 依据 |
|---|---|---|
| `pyharness/cli.py` | **可干净拆分** | hunk 1–4 全部落在 `_normalize_payload` / `_text_card`（D1）；hunk 5–8 全部落在 `prompt_approval`（D1b）——**函数边界与 finding 边界完全对齐** |
| `tests/unit/test_cli.py` | **需 hunk 级选择** | `TestRenderEvents` 区段可独立成组（D1）；但 R1B 与 R1C 的新增用例**在同一区域连续**，需在单个大 hunk 内做切分 |

### 2.2 建议：**方案 B（按 finding 拆分）**，共 4 个 commit

| # | Commit 主题 | 生产文件 | 测试片段 | 对应 finding |
|---|---|---|---|---|
| C1 | `fix(cli): render Envelope payload layer` | `cli.py`（`_normalize_payload`、`_text_card`） | 导入 `TRANSIENT_TYPES`；`_new_inmem_session(bus=)`；`TestRenderEvents` | D1 / R1A |
| C2 | `fix(cli): bind approval prompt to canonical identity` | `cli.py`（`prompt_approval`） | `_approval_envelope`；`TestPromptApproval`；`TestApprovalLiveChain` | D1b / R1B |
| C3 | `fix(commands): decouple /exit from ctx.agent` | `commands.py`（`cmd_exit`） | 导入 `commands`；`_tmp_cfg`；`TestExitLifecycleRealWiring`；`TestExitFinalFlushPersistence` | D1d-A / R1C |
| C4 | `docs: add F1 audit, review and checkpoint records` | — | — | F1 文档 |

**理由**：① 一个 commit ↔ 一个 CLOSED finding ↔ 一条 Ledger 记录 ↔ 一组 mutation proof，可追溯、可二分定位；② 与阶段报告结构（R1A/R1B/R1C）一一对应；③ 回退粒度最小。

**C1/C2/C3 中间态均为绿**（三处改动互不依赖：C2 的身份取值与类型门不依赖 C1；C3 在独立文件）：需在每一步后跑一次目标测试文件确认。

**执行提示（不含操作）**：拆分需 `git add -p` 的 hunk 切分（test_cli.py 的 R1B/R1C 交界处需用 `s` 再切）；若不使用交互式暂存，可改为**先生成逐 commit 补丁再按序 `git apply --cached`**——两种方式均须先备份当前工作树。

### 2.3 备选：方案 A（单个 F1 累计 commit）

**适用条件**：不希望交互式暂存、或希望最小化操作风险。

**可接受性**：三个缺陷同属**外壳层运行时契约**主题，且已作为整体完成回归（1712/0/0/2）与真产品验证；单 commit 不会造成语义混杂。

**代价**：无法按 finding 二分定位；Ledger 与提交历史不再一一对应。

### 2.4 明确不纳入本次提交

| 文件 | 原因 |
|---|---|
| `docs/governance.html` | **归属 portfolio 仓库**，非 PyHarness 代码或功能模块；发布到 portfolio 仅为面试演示用途 |
| `docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md` | 与上者同源的 Runtime Governance Methodology 资产，**冻结**中；归属与去留需人工决定，不应随 F1 提交进入 PyHarness |

> 二者均为 **F1 开始前既存**的未跟踪文件，非本次产生。

---

## 3. Public Repository Hygiene Review

| 检查项 | 结果 | 依据 |
|---|---|---|
| 本机绝对路径 | **无** | 本次 diff 全部新增行扫描无命中；3 份 F1 文档扫描无命中 |
| 用户名 / 个人信息 | **无** | 同上 |
| API key / 密钥 | **无真实密钥** | F1 新增测试仅用假凭据字面量 `sk-test-unused`（用于通过装配闸，不出网）；仓库既有 `sk-abcdefgh…` 出现于 `tests/unit/test_spill.py`，为**脱敏测试用假值** |
| TEMP 文件 | **未入仓库** | 15 个诊断产物全部位于**系统 TEMP**（仓库之外） |
| 诊断日志 | **未入仓库** | 仓库内 `*.log` 均命中 `.gitignore:20`（该节注释明示"仓库是 PUBLIC"） |
| 测试 session 数据 | **未入仓库** | 仓库内无 `*.jsonl`；F1 新增测试经 `tmp_path` + 重定向存储域，**不写入真实用户目录** |
| `tmp/` 目录 | **已忽略** | 命中 `.gitignore:26`（该节注释明示"含本机路径…勿入库"） |
| 其他根目录杂项 | **已忽略/未跟踪** | `collect_only.txt`、`pytest_final.txt`、`*.docx`、`pytest_*.log`、`probe_*.log` 逐一 `git check-ignore` 命中 |

**结论**：本次待提交内容**通过公开仓库卫生检查**，可直接进入提交评审。

**提示**：§2.4 列出的两个未跟踪 `docs/` 文件若被误 `git add .`，会进入 PyHarness 仓库——建议提交时**显式列出文件**，避免 `git add .` / `git add -A`。

---

## 4. Release Notes Draft

已生成独立文档：**`F1_RELEASE_NOTES_DRAFT.md`**（F1 目标 / 已关闭 finding / 核心修复 / 验证证据 / Deferred 列表 / 已知限制）。

---

## 5. Remaining Risks

| # | 风险 | 影响 | 现有缓解 |
|---|---|---|---|
| R1 | **未提交**：F1 累计变更仍在工作树 | 误操作（`checkout` / `reset`）可致丢失 | 无自动化；建议尽快确定提交策略 |
| R2 | **无 E2E 用例**：F1 的真实验证走独立脚本与真 tty 手驱动 | 回归无法自动覆盖端到端，未来改动可能静默回退 | 已有 mutation proof + 单测；E2E 沉淀属独立任务 |
| R3 | **持久化丢失窗口仍在** | 非强同步事件仅靠会话收尾 flush；强杀进程仍丢尾部 | 见 D1d-C（DEFERRED） |
| R4 | **`ctx.agent` 长期口径未定** | `/new` 仍静默失效（NEW-CMD-001）；规格未同步（SPEC-DRIFT-001） | 已隔离为独立 finding |
| R5 | **默认 `pytest` 口径仍不可直接运行** | 环境缺 PySide6 → collection 中断，需补 `--ignore` | 非 F1 范围（F0 已记录） |
| R6 | **审批场景需放开沙箱档位** | 真实审批路径的回归需非默认配置 | 已在验证中显式使用临时配置 |
| R7 | **文档与代码存在已知漂移** | 规格 `commands.py.md` 仍描述旧闸门 | 见 SPEC-DRIFT-001（DEFERRED，待人工 review） |

---

## 6. Next Stage Entry Conditions

| # | 条件 |
|---|---|
| 1 | **先决**：确定 F1 累计变更的提交策略（§2.2 方案 B 或 §2.3 方案 A）并完成提交，避免新改动与 F1 混杂 |
| 2 | 提交时**显式列出文件**，排除 §2.4 的两个非 PyHarness 资产 |
| 3 | 任何下一阶段须**单一 finding 立项**（D1d-B / D1d-C / D1c / NEW-CMD-001 不得捆绑） |
| 4 | 须明确**授权文件范围**与**禁止清单**（沿用 F1 Checkpoint §6 边界口径） |
| 5 | 须重走 LOAD → UNDERSTAND → IMPACT → PLAN 并通过 GATE-01（READY） |
| 6 | 验证须含 **mutation proof + 真产品入口证据**（F1 已建立该标准） |
| 7 | 涉及 `ctx.agent` 长期口径时，**须先完成 SPEC-DRIFT-001 人工 review**，不得以修复驱动规格改写 |
| 8 | Protocol v0.3 / INV registry / Methodology 保持冻结；如确需变更，须走独立评审，不在功能修复阶段夹带 |

**建议的人工排序**（本次不做决定）：D3（用户可直接感知、面最小）→ D1d-B（与已改文件同源）→ NEW-CMD-001 + SPEC-DRIFT-001（合并为一次 commands 契约面 review）→ D1d-C → D1c。

---

## 附录：本次操作一致性声明

- 未修改任何生产代码、测试、specs、Protocol、Methodology、README。
- 未修复任何 DEFERRED finding；未新增 finding；未主动重扫仓库。
- 未执行任何 git 写操作（无 `add`、无 `commit`、无 `push`）。
- 未新增、未删除仓库中的任何文件；仅新增 2 份 Release Prep 文档。

```
git status --short
 M pyharness/cli.py                 ← F1-R1A + F1-R1B
 M pyharness/core/commands.py       ← F1-R1C
 M tests/unit/test_cli.py           ← F1-R1A + R1B + R1C 累计
?? F1-R1C_FINAL_REVIEW.md           ← F1-R1C 交付
?? F1_CHECKPOINT.md                 ← F1 阶段冻结
?? F1_RELEASE_NOTES_DRAFT.md        ← 本次
?? F1_RELEASE_PREPARATION.md        ← 本次
?? docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md   ← 既存,非 PyHarness 资产
?? docs/governance.html                          ← 既存,归属 portfolio

git log -1
d2ccd45 docs: refine S6-2b release presentation
```

**未 commit、未 push。**
