# S6-1_INV_CLASSIFICATION.md — INV-01~09 引用分类与编号口径勘察

> **阶段**：S6-1（INV 编号口径裁定 + 覆盖盘点）· **纯净分析产物**
> **日期**：2026-09-15 ｜ **基线 commit**：**`753b176`**（S6 Entry Plan 冻结）｜ worktree clean ｜ ahead 30
> **性质**：**只读勘察 + 分类**。本文件**未修改任何 `.py` / 测试 / schema / ADR / Governance Core**；**未 commit**；**未创建 `docs/INVARIANT_REGISTRY.md`**（按指令等待 review 授权）。
> **状态标注**：`IMPLEMENTED`=代码已写 ｜ `TESTED`=测试已跑 ｜ `VERIFIED`=验证路径已执行且通过 ｜ `DONE`=通过 GATE-04
> **本文件状态**：**分析完成 + 最终裁决已补录**。§1~§11 = 勘察与分类（**`VERIFIED`**，证据可逐条复核）；**§12 = 2026-09-15 人工裁决后的最终口径**（裁定的**执行**仍未开始：19 处迁移全部 `pending authorization`）。
> **S6-1 状态**：**未 `DONE`** —— 编号口径已裁定、覆盖矩阵已建，但 Registry 未创建、迁移未执行。

---

## 0. 实测口径（本报告全部数字的来源）

| 项 | 值 | 证据 |
|---|---|---|
| 扫描范围 | 全仓库，排除 `.git`/`.venv`/`build`/`dist`/`__pycache__`/`.pytest_cache`/`docs_html` | 脚本扫描 |
| 扫描形式 | `INV-0[1-9]\b`（两字符补零族） | 与 `INV-\d+` **逐文件计数完全一致** ⇒ 全库**不存在** `INV-1`（单位数）与 `INV-10+` 形态 |
| `INV-0x` 出现次数（**occurrences**） | **812**（非 tmp） | 含 `INV-01~09` 区间写法与 `INV-01/02` 双写 |
| 其中 `tmp/_staged.diff` | 84 次，**已排除** | 本机残留中间产物（`.gitignore` 已覆盖 `tmp/`），非仓库内容 |
| 按层分布 | **docs+根 md 517** · **`pyharness/` 193** · **`tests/` 95** · **`scripts/` 7** | 见 §1 |
| 基线全量回归 | **1671 passed / 2 skipped / 0 failed**（collected 1673，exit 0，43.9s） | `.venv/Scripts/python.exe -m pytest tests --ignore=tests/unit/test_desktop_native.py --ignore=tests/unit/test_shell_parity.py --junit-xml=...` + junit 节点计数 ⇒ **与 S6_ENTRY_PLAN §0 的 `1671/2/0` 逐字一致** |

> **计数口径声明**：本报告用**出现次数（occurrences）**而非"命中行数"。两者不同（例如 `session.py` 命中 6 行 / 8 次），差异来自同一行出现 ≥2 个 token 的双写（`INV-01/02`）与区间写法（`INV-01~09`）。

---

## 1. INV-01~09 当前引用总表

### 1.1 按编号

| ID | 出现次数 | 其中冲突语义 | 冲突语义所属编号族 |
|---|---:|---:|---|
| INV-01 | 234 | 0（**无冲突**，见 §4 注） | — |
| INV-02 | 47 | **9** | 族 B「会话拆分」 |
| INV-03 | 56 | **9** | 族 C「旧 guard 编号」 |
| INV-04 | 90 | 0 | — |
| INV-05 | 86 | 0 | — |
| INV-06 | 90 | 0 | — |
| INV-07 | 39 | **2** | 族 D「已废弃功能编号」 |
| INV-08 | 85 | **2** | 族 D「已废弃功能编号」 |
| INV-09 | 85 | 0 | — |
| **合计** | **812** | **22** | **9 个文件** |

> `INV-01` 的 234 中有 **33 次是区间写法 `INV-01~09`**（出现在 15 个文件），语义性引用为 **201** 次。

### 1.2 按层 × 编号

| 层 | INV-01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 小计 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `docs/` + 根 md | 137 | 30 | 28 | 67 | 67 | 57 | 27 | 48 | 56 | **517** |
| `pyharness/` | 70 | 11 | 12 | 14 | 10 | 18 | 8 | 32 | 18 | **193** |
| `tests/` | 26 | 5 | 14 | 8 | 9 | 13 | 4 | 5 | 11 | **95** |
| `scripts/` | 1 | 1 | 2 | 1 | 0 | 2 | 0 | 0 | 0 | **7** |
| **合计** | **234** | **47** | **56** | **90** | **86** | **90** | **39** | **85** | **85** | **812** |

### 1.3 引用最密集的文件（前 20，按出现次数）

| 次数 | 文件 |
|---:|---|
| 84 | `tmp/_staged.diff`（**已排除**，非仓库内容） |
| 50 | `docs/SECURITY.md`（§9 含 INV-01~09 **完整表**） |
| 44 | `docs/DIS-CORE.md`（:28 含 INV-01~09 **完整清单**） |
| 34 | `docs/PRD-Core.md`（**:838 权威表**） |
| 31 | `REFACTOR_PLAN.md` |
| 18 | `ARCHITECTURE_DECISION_RECORD.md` |
| 17 | `docs/specs/session.py.md` |
| 16 | `pyharness/core/schedule.py` |
| 15 | `docs/specs/schedule.py.md` · `pyharness/core/goal.py` |
| 14 | `S6_ENTRY_PLAN.md` · `docs/DIS-SEAM.md` · **`tests/unit/test_tools_guard.py`** |
| 13 | `GOVERNED_AGENT_RUNTIME_DESIGN.md` · `docs/EVENT-SCHEMA.md` · `docs/KEY-FINDINGS.md` · `pyharness/core/plan_mode.py` · **`pyharness/core/session.py`** |
| 12 | `docs/CONSTRAINTS-06-Testing.md` · `pyharness/core/tools_guard.py` · **`tests/unit/test_session.py`** |

---

## 2. 每个 INV 的语义来源（谁定义了它）

| ID | Canonical 语义（族 A） | 权威定义位置（可复核） |
|---|---|---|
| INV-01 | 日志只追加 / 历史必由日志派生 | `docs/PRD-Core.md:838`（"历史必由日志派生"）·`docs/CONSTRAINTS-06-Testing.md:62`（**合并**"日志只追加 / 历史必由日志派生"）·`docs/SECURITY.md:196,206` ·`docs/DIS-CORE.md:28` ·`docs/EVENT-SCHEMA.md:503` |
| INV-02 | 无绕过 agent-loop 直调 llm | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:63` ·`docs/SECURITY.md:197,208` ·`docs/DIS-CORE.md:28,46,186` ·`docs/PRD-Core.md:634` |
| INV-03 | rebuild 与缓存一致 | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:64` ·`docs/SECURITY.md:198,210` ·`docs/EVENT-SCHEMA.md:526` |
| INV-04 | 无 guard 事件即非法执行 | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:65` ·`docs/SECURITY.md:199,212` ·`ARCHITECTURE_DECISION_RECORD.md:175`（ADR-015 背景） |
| INV-05 | 拒绝后零副作用 | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:66` ·`docs/SECURITY.md:200,214` ·`ARCHITECTURE_DECISION_RECORD.md:318` |
| INV-06 | 执行 args = 日志 args | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:67` ·`docs/SECURITY.md:201,216` ·`docs/ADD.md:258`（ADR-002/003 背景） |
| INV-07 | 单进程 | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:68` ·`docs/SECURITY.md:202,218` ·`docs/ADD.md:164`（ADR-012 背景） |
| INV-08 | 阶段 import 方向 / 依赖方向 | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:69` ·`docs/SECURITY.md:203,220` ·`docs/ADD.md:236` ·`ARCHITECTURE_DECISION_RECORD.md:308`（ADR-018 扩展） |
| INV-09 | 日志无凭据 / 全出口脱敏 | `docs/PRD-Core.md:838` ·`docs/CONSTRAINTS-06-Testing.md:70` ·`docs/SECURITY.md:204,222` ·`docs/CFG.md:349` |

### 2.1 权威链的三个既有层次（同一份语义，三种载体）

1. **`docs/PRD-Core.md:838`（F018）** —— 声明为**最高权威**。`CONSTRAINTS-06` §7 自述："冲突以 PRD 为准"。
2. **`docs/CONSTRAINTS-06-Testing.md:51-74`（§7）** —— S6_ENTRY_PLAN 指定的"权威表"；与 PRD **唯一差异**是 INV-01 被写成**合并式**（"日志只追加 / 历史必由日志派生"），并在 `:53-58`/`:72-74` 记录了 2026-09-12 的一次**口径统一**及其**废弃清单**。
3. **`docs/SECURITY.md:190-223`（§9）** —— 现有**最完整的"注册表形态"文本**：每条含 名称 / 一句话含义 / 守护关系 / 详解 / 检测方法 / 违反后果。**这是 `docs/INVARIANT_REGISTRY.md` 的最佳蓝本**。

### 2.2 与 INV-0x 无关的第四份"不变量表"（不构成编号冲突，但须登记）

`docs/架构设计.md:189-202`（§8）列出 **8 条无编号规矩**（1 循环必须结束 … 8 guard 拒绝的调用真实函数未被执行）。`CONSTRAINTS-06` §5 验收条件仍指向它（"核心不变量测试清单（≥8 条，见架构设计.md §8）"）。
**判定**：它**不是** INV-0x 编号族，**不参与编号冲突**；但它是 `CONSTRAINTS-06` INV-01"只追加"条目与 INV-08"循环必终止"废弃说明的**历史来源**，须在 Registry 的 `Legacy Mapping` 字段登记。

---

## 3. INV-02 两套语义的详细分类（本阶段核心）

`INV-02` 全库 **47 次**（非 tmp），按语义分为三类：

- **A 类 = Canonical（无绕过 agent-loop 直调 llm）**：**37 次**
- **B 类 = 会话拆分语义（历史必由日志派生 / 无第二份状态）**：**9 次**
- **C 类 = Ambiguous（同处引用两个含义）**：**1 次**

### 3.1 B 类 —— 当前 Code/Test 语义（**建议映射 → INV-01**）

| # | 文件:行 | 原文（截断） | 当前语义 | 建议映射 |
|---|---|---|---|---|
| 1 | `pyharness/core/session.py:7` | `取新版留旧痕。消息历史 = 日志派生(INV-02):不存在第二份内存历史,derive_messages 是…` | 历史派生 / 无第二份历史 | → **INV-01** |
| 2 | `pyharness/core/session.py:177` | `"""日志 → 消息历史纯折叠(§3.5 reducer 唯一权威实现,INV-02)。"""` | 派生唯一实现 | → **INV-01** |
| 3 | `pyharness/acp.py:27` | `会话日志派生(read_events 同真源,INV-02 无第二份状态)。` | 无第二份状态 | → **INV-01** |
| 4 | `tests/unit/test_session.py:5` | `INV-02 历史必由日志派生:derive_messages 唯一历史源,append 即历史变化,` | 历史派生 | → **INV-01** |
| 5 | `tests/unit/test_session.py:174` | `"""INV-02 结构断言:除日志 _cache 与可弃 history_cache 外无第二份历史存储。"""` | 无第二份历史 | → **INV-01** |
| 6 | `tests/unit/test_session.py:435` | `"""INV-02 行为:历史只随日志事件变化——非映射事件(renamed)不改变派生,` | 派生只随日志变化 | → **INV-01** |
| 7 | `scripts/demo_phase1.py:5` | `2. 重启回放:从 JSONL 重建历史(INV-02 单一真源)` | 单一真源 | → **INV-01** |
| 8 | `docs/baseline/TEST_BASELINE.md:155` | ``> **INV 覆盖缺口**：`INV-02`（无第二状态）…`` | 无第二状态 | → **INV-01**（**基线快照，仅加注不改数**，见 §7） |
| 9 | `S6_ENTRY_PLAN.md:15` | `…**INV-02 = 历史必由日志派生**、**INV-03 = rebuild 一致**` | **本冲突的自述记录**（同一行同时含 A 类与 B 类） | **不改**（FROZEN 规划产物） |

**佐证 —— 族 B 是"把 canonical INV-01 拆成两条"而非"整体错位"：**

- `pyharness/core/session.py:5` 用 **INV-01 = 只追加**、`:7` 用 **INV-02 = 派生**、`:70/:143/:338/:396/:473` 用 **INV-03 = 缓存/rebuild**（**与 canonical INV-03 一致**）、`:37` 用 **INV-08 = 单向依赖**（**与 canonical INV-08 一致**）。
- ⇒ **B 族只重排了 canonical INV-01/INV-02 的边界**，INV-03 及之后的编号**未漂移**。这解释了为什么冲突面远小于"两套独立编号"的直觉。
- ⇒ **`session.py` 的 INV-01"只追加"是 canonical 合并式 INV-01 的子集** ⇒ **不需要迁移**；只有其 **INV-02** 是真正的语义冲突。

**关键反证（文档与代码不同源）：** `docs/specs/session.py.md` 描述的就是 `session.py`，但它**全程不用 INV-02**，且 `:233` 明写 `INV-01(历史必由日志派生)`、`:12` 写 `缓存纪律(INV-01/INV-03)`。
⇒ **同一模块的 spec 用 canonical 口径，实现在 B 族口径** —— 这是 `docs/specs/` 与代码 docstring 之间的**同源漂移**，不是 spec 的问题。

### 3.2 A 类 —— Canonical 语义（**保留，不迁移**）37 次

`REFACTOR_PLAN.md:56,162,271,392,512` · `CONSTRAINTS-06-Testing.md:63` · `DIS-CORE.md:28,46,108,186` · `PRD-Core.md:634,838` · `SECURITY.md:197,208` · `tests/unit/test_agent_loop.py:686` · `pyharness/core/agent_loop.py:6,229` · `pyharness/core/llm.py:5,651` · `pyharness/core/plan_mode.py:14` · `pyharness/core/subagent.py:50,799,805` · `docs/baseline/BASELINE_REPORT.md:126` · `docs/baseline/TEST_BASELINE.md:233` · `docs/specs/agent_loop.py.md:4,12,262` · `docs/specs/llm.py.md:4,8` · `docs/specs/llm_fallback.py.md:17` · `docs/specs/plan_mode.py.md:17` · `docs/specs/commands.py.md:4` · `docs/specs/session_query.py.md:19` · `docs/specs/subagent.py.md:16,152`

> **注意 `docs/specs/llm_fallback.py.md:17`**：该 spec 用 **canonical INV-02**（"均经 `chat_with_fallback` 出网"），而实现它的测试 `tests/unit/test_llm_fallback.py` 用 **族 D INV-07**（见 §4.3）—— 又一处 spec/测试不同源。

### 3.3 C 类 —— Ambiguous（**1 次**）

| 文件:行 | 原文 | 歧义点 | 建议映射 |
|---|---|---|---|
| `tests/unit/test_agent_loop.py:15` | `上下文唯一来源 = 会话日志派生(INV-01/INV-02 单测侧证据)。` | 句子主语是"日志派生"（=INV-01），但并列写了 INV-02 | → **INV-01**；该文件对 INV-02 的正当证据在 `:686`（`test_llm_chat_only_entry_point_inv02`，同时断言"日志派生的上下文"+"run_turn 之外无 llm.chat 路径"） |

**另两处"双写"经 token 核验后归入 INV-01（不是 INV-02 site）：**

- `pyharness/core/session.py:347` —— `无副作用、无第二份状态(INV-01/02)`：字面为 `INV-01/02`，**只匹配到 INV-01**；语义 = 派生 ⇒ 建议改为 **INV-01**（去掉 `/02`）。
- `REFACTOR_PLAN.md:146` —— `session.derive_messages(window) ← 日志现派生（INV-01/02）`：同上 ⇒ **INV-01**。

---

## 4. 编号之间的冲突关系

### ⚠️ 对 F-1 的重要修正：**不是两套口径，是四套**

`S6_ENTRY_PLAN.md:15`（F-1）把问题描述为"两套口径：CONSTRAINTS-06 vs session.py/test_session.py"。**实测不成立**。全库存在 **4 个语义族**，其中 **3 个与 canonical 冲突**：

| 族 | 名称 | 与 canonical 的关系 | 冲突编号 | 冲突次数 | 落点 |
|---|---|---|---:|---:|---|
| **A** | Canonical（spec 权威链） | — 基准 | — | — | `PRD-Core:838` / `SECURITY:190-223` / `CONSTRAINTS-06:60-70` / `DIS-CORE:28` / 全部 `docs/specs/*` |
| **B** | 会话拆分（session 局部） | canonical **INV-01 的再拆分** | **INV-02** | 9 | 3 源 4 测试 1 脚本 1 基线文档 |
| **C** | 旧 guard 编号（2026-09-12 前） | 已被 `CONSTRAINTS-06:53-58` 声明废弃 | **INV-03** | 9 | 1 测试 1 矩阵 1 脚本 |
| **D** | 已废弃功能编号 | 已被 `CONSTRAINTS-06:56-58,72-74` 逐条宣告"不是 INV" | **INV-07**、**INV-08** | 4 | 2 测试文件 |

### 4.1 冲突一：`INV-02`（族 B）—— canonical「无直调 llm」 vs 局部「历史派生」

- **Canonical 侧（37 次）**：`agent_loop.py`、`llm.py`、`plan_mode.py`、`subagent.py`、`docs/specs/*`、`DIS-CORE`、`SECURITY`、`PRD`。
- **B 族侧（9 次）**：`session.py:7,177`、`acp.py:27`、`test_session.py:5,174,435`、`demo_phase1.py:5`、`TEST_BASELINE.md:155`（+ `S6_ENTRY_PLAN:15` 的自述）。
- **性质**：**真冲突**（同一编号、两义）。但 B 族只影响 `INV-02`，其 `INV-01`（只追加）与 canonical **兼容**。
- **风险**：`tests/unit/test_session.py` 的模块 docstring `:4-8` **显式声明了 B 族口径**（`INV-01 append-only / INV-02 历史派生 / INV-03 rebuild`）—— 若不裁定，S6-2 新增的"INV-02 专项用例"会**同时**被两种含义解释。

### 4.2 冲突二：`INV-03`（族 C）—— canonical「rebuild 与缓存一致」 vs 旧「guard 单调拒绝 / 无放行」

| 文件:行 | 原文 | 当前语义 | 应映射 |
|---|---|---|---|
| `tests/unit/test_tools_guard.py:6` | `- INV-03 单调拒绝:一次 reject 后任何 guard 组合/顺序/后续挂载都不能翻回 allow;` | guard 单调 | **INV-04** |
| `tests/unit/test_tools_guard.py:197` | `"""三值枚举 {allow, reject, approval};无 bypass 第四值(INV-03 词表面)。"""` | 无 bypass | **INV-04** |
| `tests/unit/test_tools_guard.py:245` | `# ========== INV-03 单调` | 单调小节标题 | **INV-04** |
| `tests/unit/test_tools_guard.py:247` | `"""结构防线:GuardChain 无任何 bypass/放行/执行/翻回 API(INV-03 钉死)。"""` | 无放行 API | **INV-04** |
| `tests/unit/test_tools_guard.py:269` | `"""INV-03 行为面:同 call 反复求值恒 reject…` | 单调行为 | **INV-04** |
| `tests/unit/test_tools_guard.py:305` | `"""INV-03 核心:reject 后追加任何 allow 型 guard 到链尾…` | 单调核心 | **INV-04** |
| `CODE-MATRIX.md:35` | `- 安全核心:tools_guard 80 测试(INV-03 单调拒绝/INV-04 零副作用)` | 单调 + 零副作用 | **INV-04 / INV-05** |
| `scripts/demo_phase1.py:61` | `print("\n=== 3. guard 拒危险工具(INV-03/04)===")` | guard 拒绝 | **INV-04** |
| `scripts/demo_phase1.py:71` | `print(f"  guard 抛错: {e.code} — 拒绝路径成立(INV-03 无放行)")` | 无放行 | **INV-04** |

**文档级自证**：`docs/CONSTRAINTS-06-Testing.md:53-58` 明写"本表此前的 **INV-03/04/05/06/07/08** 语义与 PRD 的权威 INV 表不一致——曾把 **'guard 无放行** / 参数非法 / 强同步不丢 / 降级生效 / 循环必终止'记在这些编号上"。
⇒ 族 C 的 **INV-03 = guard 无放行 / 单调拒绝** 正是该废弃清单**逐字所列**的第一项。
⚠️ **精确性声明**：该文档**未逐条给出"哪个旧义对哪个编号"**，故"guard 无放行 = 旧 INV-03"是**由残留站点反推**（同文件另有 1 处同义用例标 INV-04，且 `tools_guard.py` 实现正文 7 处把单调面挂在 INV-04），**非**文档逐字明示。完整证据链与最终裁定见 **§12.3.2**。

**同文件内部亦不自洽（重要）**：`test_tools_guard.py` 的 `test_monotonic_api_surface_no_allow` **标 INV-04**（canonical ✓），而其余 5 处单调用例标 **INV-03**（族 C ✗）——**同一语义在同一文件里挂了两个编号**。

### 4.3 冲突三：`INV-07`（族 D）—— canonical「单进程」 vs 已废弃「降级链」

| 文件:行 | 原文 | 当前语义 | 应映射 |
|---|---|---|---|
| `tests/unit/test_llm_fallback.py:4` | `F013 降级链:LLM-302 直降备用(INV-07 载体:证明降级真发生)、LLM-303(exhausted)` | 降级链 | **移出 INV 族** → F013/F028 功能验收 |
| `tests/unit/test_llm_fallback.py:182` | `——降级真的发生(INV-07 载体:主 5xx/401 后请求走备用)。"""` | 降级链 | 同上 |

**文档级自证**：`docs/CONSTRAINTS-06-Testing.md:72-74` 逐字写明 —— *"降级链'主模型 5xx/401 后请求走备用'由 `tests/unit/test_llm_fallback.py` 覆盖,但它是**功能验收(F013/F028)**,不是 INV 编号——早期把它记作 **INV-07** 的做法**已废弃**。"*
⇒ **该声明点名了本文件、本编号**。判定：族 D-INV-07 **确认废弃**，无替代编号。

### 4.4 冲突四：`INV-08`（族 D）—— canonical「阶段 import 方向」 vs 已废弃「循环必终止」

| 文件:行 | 原文 | 当前语义 | 应映射 |
|---|---|---|---|
| `tests/unit/test_agent_loop.py:12` | `不变量:循环必终止(INV-08)、三闸独立触发且只读、错误码走 errors 域` | 循环必终止 | **移出 INV 族** → F007 循环护栏 |
| `tests/unit/test_agent_loop.py:259` | `循环必终止铁律(INV-08):任何输入在 max_turns 轮内结束,不存在无限工具轮。` | 循环必终止 | 同上 |

**文档级自证**：`docs/CONSTRAINTS-06-Testing.md:56-58` 明写 —— *"'循环必终止'**不是不变量编号**,而是 F007 循环护栏（由 `max_turns` 用例覆盖,见 `tests/unit/test_agent_loop.py`）。"*
⇒ 声明同样**点名了本文件**。判定：族 D-INV-08 **确认废弃**，无替代编号（对应用例 `test_max_turns_truncation_inv08` 应改名去编号）。

### 4.5 不构成冲突的两项（须显式登记，避免后续误判）

1. **INV-01「只追加」写法（族 B 的 INV-01）**：canonical 是**合并式**（`CONSTRAINTS-06:62` = "日志只追加 / 历史必由日志派生"），B 族的"只追加"是其**子集** ⇒ **无需迁移**。（例外：`session.py:5-7` 把本该同为 INV-01 的两义拆成 01/02 —— 只需消去 INV-02 这个编号。）
2. **`docs/架构设计.md:189-202` 的 8 条无编号规矩**：无 `INV-0x` token ⇒ 不参与编号冲突；仅在 Registry 的 `Legacy Mapping` 登记来源关系。

### 4.6 冲突全局关系图

```
canonical（族 A，权威=PRD-Core:838 → CONSTRAINTS-06:60-70 → SECURITY:190-223）
  INV-01 日志只追加 / 历史必由日志派生 ──┐
                                        └─ 族 B 把这一条**拆成两条**：
                                             INV-01(只追加)  ← 兼容，不改
                                             INV-02(历史派生) ← ✗ 撞 canonical INV-02
  INV-02 无绕过 agent-loop 直调 llm   ← 37 处正确使用（agent_loop/llm/subagent/plan_mode/specs）
  INV-03 rebuild 与缓存一致            ← ✗ 族 C 用 INV-03 表示「guard 单调/无放行」→ 应归 INV-04
  INV-04 无 guard 事件即非法执行
  INV-05 拒绝后零副作用
  INV-06 执行 args = 日志 args
  INV-07 单进程                        ← ✗ 族 D 用 INV-07 表示「降级链」→ 非 INV（F013/F028）
  INV-08 阶段 import 方向              ← ✗ 族 D 用 INV-08 表示「循环必终止」→ 非 INV（F007 护栏）
  INV-09 日志无凭据 / 全出口脱敏
```

---

## 5. 建议的 Canonical 编号方案（**待人工裁定，尚未实施**）

### 5.1 裁定建议 → ✅ **已由人工采纳**（最终口径见 §12.0 / §12.1）

| 项 | 建议 |
|---|---|
| **权威口径** | **以 `docs/CONSTRAINTS-06-Testing.md` §7（`:60-70`）为 S6 执行口径**（与 S6_ENTRY_PLAN Q-1 建议一致）；其上位权威为 `docs/PRD-Core.md:838`。 |
| **INV-01 表述** | 采用 CONSTRAINTS-06 的**合并式**："日志只追加 / 历史必由日志派生"。理由：代码中 `INV-01` 的两类用法（"只追加"与"由事件派生"）**共 234 处且语义自洽**，合并式可**零迁移**吸收二者。 |
| **INV-02** | 采用 canonical："无绕过 agent-loop 直调 llm"。**族 B 的 9 处改为 INV-01**。 |
| **INV-03** | 采用 canonical："rebuild 与缓存一致"。**族 C 的 9 处改为 INV-04**。 |
| **INV-07 / INV-08** | 采用 canonical："单进程" / "阶段 import 方向"。**族 D 的 4 处去编号**（改为 F013/F028、F007 引用）。 |
| **INV-04/05/06/09** | 不变（无冲突）。 |
| **不删历史** | 全部旧编号信息以 `Legacy Mapping` 字段保留（见 §5.2）。 |

### 5.2 Canonical 编号表（拟写入 Registry 的正式定义）

| ID | Name | Canonical Definition | Intent | Verification Target | Status（当前） |
|---|---|---|---|---|---|
| INV-01 | 日志只追加 / 历史必由日志派生 | 会话日志为 append-only 唯一真源；一切历史/派生视图均可由日志整体重建，不存在第二份权威消息列表 | 事件溯源的立身之本：回放/审计/恢复/一致性四能力同源 | 无 delete/update API；append 后文件 hash 不变；`derive` == 逐事件重放 | **established**（覆盖散落于 `tests/unit/*`） |
| INV-02 | 无绕过 agent-loop 直调 llm | 全库 `llm.chat` 唯一合法调用方 = agent-loop（`run_turn`） | 三闸（轮数/预算/取消）只在循环内，绕过循环=绕过闸 | 静态调用图 + `test_llm_chat_only_entry_point_inv02` | **migrated** |
| INV-03 | rebuild 与缓存一致 | `rebuild_from_log` 后缓存与重建前逐事件一致（含增量/编辑重放/二次 rebuild 无重复） | 派生视图可整体丢弃重建 | `test_rebuild_from_log_invariant_inv03` 等 | **migrated** |
| INV-04 | 无 guard 事件即非法执行 | 每个工具执行前必有同 `call_id` 的 `guard.evaluated`；guard 只有 allow/reject/approval 三值，无 bypass | guard 单调拒绝的结构防线 | `test_guard_from_config_injects_schema_validator` 等 | **established** |
| INV-05 | 拒绝后零副作用 | 任意 reject 后 Provider 调用计数 = 0，且无该 `call_id` 的 `tool.result` | "拦了且没执行"可证 | `test_executor_critical_delete_denied_zero_side_effect` 等 | **established** |
| INV-06 | 执行 args = 日志 args | `raw_args`(模型原话) 与强类型 `args`(实际执行) 双份存档且逐字段一致；真实函数收到的 == 日志 args | LLM 零信任；事后可还原事故 | `test_malformed_args_tlb803_zero_exec` 等 | **established** |
| INV-07 | 单进程 | 全库进程数 = 1；无 multiprocessing/跨进程 RPC（受 guard 的 subprocess 工具除外） | 单一事实源 seq 单调 | `test_cross_process_lock_blocks_second_writer` | **migrated** |
| INV-08 | 阶段 import 方向 | 阶段 N 不得 import 阶段 N+1 模块；脊柱严格无环；`governance/` 只依赖 `events`+`errors` | 依赖单向，外围可插拔 | `test_attach_shadowing_spine_member_tlb802` + ADR-018:308 | **migrated** |
| INV-09 | 日志无凭据 / 全出口脱敏 | 日志/事件/错误/spill/PTY 全出口无 32+ 位疑似密钥原文 | 凭据安全；错误分支是最难追查的泄露点 | `test_redact_masks_secrets` 等 | **established** |

> `Status` 取值沿用任务书：`established`（编号与语义已一致）/ `migrated`（本次裁定后需迁移引用）/ `gap`（无专项用例，见 §9）。

### 5.3 待人工裁定项（**已于 2026-09-15 全部裁定 —— 结论见 §12**）

| # | 事项 | 本轮裁定结果 |
|---|---|---|
| **R-1** | 权威口径（CONSTRAINTS-06 §7 + INV-01 合并式） | ✅ **采纳**（§12.0 P-1~P-5、§12.1） |
| **R-2** | INV-02 的 B 族 9 处 → INV-01：是否授权改注释 | ⛔ **本轮不授权** ⇒ 转 **Future Cleanup FC-1~4/FC-6/FC-10**（§12.4） |
| **R-3** | INV-03 的 C 族 9 处 → INV-04 | ✅ **语义裁定完成**（§12.3.2）；⛔ **迁移本轮不执行** ⇒ FC-8/FC-10/FC-13 |
| **R-4** | 失效编号（INV-07/INV-08）去编号 | ✅ **裁定：移出 INV 族**（§12.3.3/§12.3.4）；⛔ 迁移本轮不执行 ⇒ FC-7/FC-9 |
| **R-5** | 文件名口径 | ✅ **定为 `S6-1_INV_CLASSIFICATION.md`**；**不建** `S6-1_INV_ADJUDICATION.md`（裁决经人工授权完成，见 §12） |
| **R-6** | `test_inv_{bus,config,events}.py` 失实的"当前 RED"头部 | ⛔ 本轮不授权 ⇒ **FC-11** |

---

## 6. 需要迁移的引用清单

**总计 22 处 / 9 个文件**（= §1.1 的冲突总数）。**本阶段未执行任何一项**，仅列出。

> ⚠️ **本节已被 §12.2（Legacy Mapping Table）取代**：§12.2 增加了 `Status` 列并按裁决重新分组（待授权 19 处 / 仅加注 3 处）。本节保留为原始勘察记载，**以 §12.2 为准**。

### 6.1 → `INV-01`（族 B / 双写，共 10 行）

**A. 族 B 的 7 处操作性迁移**（`INV-02` 出现 → 改为 `INV-01`）

| 文件:行 | 类型 | 改法 |
|---|---|---|
| `pyharness/core/session.py:7` | 模块 docstring | `INV-02` → `INV-01` |
| `pyharness/core/session.py:177` | 方法 docstring | `INV-02` → `INV-01` |
| `pyharness/acp.py:27` | 模块 docstring | `INV-02` → `INV-01` |
| `tests/unit/test_session.py:5` | 模块 docstring | `INV-02 历史必由日志派生` → `INV-01 历史必由日志派生` |
| `tests/unit/test_session.py:174` | 测试 docstring | `INV-02` → `INV-01` |
| `tests/unit/test_session.py:435` | 测试 docstring | `INV-02` → `INV-01` |
| `scripts/demo_phase1.py:5` | 打印文案 | `INV-02` → `INV-01` |

**B. C 类歧义 1 处（同处引用两义）**

| 文件:行 | 类型 | 改法 |
|---|---|---|
| `tests/unit/test_agent_loop.py:15` | 模块 docstring | `(INV-01/INV-02 单测侧证据)` → `(INV-01 单测侧证据；INV-02 见 :686)` |

**C. 双写建议 2 处（token 核验为 INV-01，属"消歧"而非冲突）**

| 文件:行 | 类型 | 改法 |
|---|---|---|
| `pyharness/core/session.py:347` | 行内注释 | `(INV-01/02)` → `(INV-01)` |
| `REFACTOR_PLAN.md:146` | 架构图注释 | `（INV-01/02）` → `（INV-01）` —— **REFACTOR_PLAN 为历史审计产物，建议仅加注**（见 §7） |

**D. 族 B 中不改的 2 处（历史载体）**

| 文件:行 | 处置 |
|---|---|
| `docs/baseline/TEST_BASELINE.md:155` | 基线快照 ⇒ **只加注，不改数**（见 §7） |
| `S6_ENTRY_PLAN.md:15` | 冻结规划产物，**是本冲突的原始记录** ⇒ **不改**（修正写在 §4） |

### 6.2 → `INV-04`（族 C，9 处）

| 文件:行 | 类型 | 改法 |
|---|---|---|
| `tests/unit/test_tools_guard.py:6` | 模块 docstring | `INV-03 单调拒绝` → `INV-04 单调拒绝` |
| `tests/unit/test_tools_guard.py:197` | 测试 docstring | `INV-03` → `INV-04` |
| `tests/unit/test_tools_guard.py:245` | 小节标题注释 | `INV-03 单调` → `INV-04 单调` |
| `tests/unit/test_tools_guard.py:247` | 测试 docstring | `INV-03` → `INV-04` |
| `tests/unit/test_tools_guard.py:269` | 测试 docstring | `INV-03` → `INV-04` |
| `tests/unit/test_tools_guard.py:305` | 测试 docstring | `INV-03` → `INV-04` |
| `scripts/demo_phase1.py:61` | 打印文案 | `(INV-03/04)` → `(INV-04)` |
| `scripts/demo_phase1.py:71` | 打印文案 | `(INV-03 无放行)` → `(INV-04 无放行)` |
| `CODE-MATRIX.md:35` | 历史矩阵 | `(INV-03 单调拒绝/INV-04 零副作用)` → `(INV-04 单调拒绝/INV-05 零副作用)`；**建议加注而非改数**（见 §7） |

### 6.3 移出 INV 族（族 D，标注 4 处 + 函数名 1 处）

| 文件:行 | 现值 | 改法 |
|---|---|---|
| `tests/unit/test_llm_fallback.py:4` | `INV-07 载体` | → `F013/F028 功能验收（非 INV）` |
| `tests/unit/test_llm_fallback.py:182` | `INV-07 载体` | 同上 |
| `tests/unit/test_agent_loop.py:12` | `循环必终止(INV-08)` | → `循环必终止（F007 护栏，非 INV）` |
| `tests/unit/test_agent_loop.py:259` | `循环必终止铁律(INV-08)` | 同上 |
| `tests/unit/test_agent_loop.py`（函数名） | `test_max_turns_truncation_inv08` | 建议去 `_inv08`（**改名会动测试面，须授权**） |

### 6.4 迁移后预期状态（逐编号核算）

> **计数规则**：按**语义归属**计（一个 token 无论其文本是否改动，其含义只归属一个 Canonical ID）。**语义归属 ≠ 文本改动处数**——冻结文档中的历史记录不计入改动，但仍计入归属。

| ID | 迁移前 | 迁出 | 迁入 | 迁移后（语义归属） | 文本改动处数 |
|---|---:|---:|---:|---:|---:|
| INV-01 | 234 | 0 | **+10**（族 B 9 + 歧义 1） | **244** | 8（7 处族 B + 1 处歧义）+ 2 处消歧（§6.1-C） |
| INV-02 | 47 | **−10** | 0 | **37** | 7（族 B 操作站点，见 §6.1-A） |
| INV-03 | 56 | **−9** | 0 | **47** | 8（族 C 站点，见 §6.2） |
| INV-04 | 90 | **−1** | **+9** | **98** | 0（`CODE-MATRIX.md:35` 仅加注） |
| INV-05 | 86 | 0 | **+1** | **87** | 0（同上，仅加注） |
| INV-06 | 90 | 0 | 0 | **90** | 0 |
| INV-07 | 39 | **−2** | 0 | **37** | 2 |
| INV-08 | 85 | **−2** | 0 | **83** | 2（+1 函数名建议，须单独授权） |
| INV-09 | 85 | 0 | 0 | **85** | 0 |
| *（移出 INV 族）* | — | — | **+4** | **4** | — |

**闭合校验**：244+37+47+98+87+90+37+83+85 = **808**，+4 = **812** = §1.1 的总数 ✅

- **冲突次数 22 → 0**（唯余 `S6_ENTRY_PLAN.md:15` 的 1 处，属**有意保留的历史记录**）。
- **零行为变更**（全部为 docstring / 注释 / 打印文案 / 文档，无一处语句）。
- 基线 `1671 passed / 2 skipped / 0 failed` **必须不变**；`77 / 14 / 3`（事件/强同步/瞬态）**必须不变**。

---

## 7. 不应修改的历史记录

| 类别 | 文件 | 理由 |
|---|---|---|
| **FROZEN 架构记录** | `ARCHITECTURE_DECISION_RECORD.md`（全部 18 处 INV 引用**均为 canonical**，无冲突）· `ADR-013`~`ADR-020` | 冻结；ADR 只增不改（`docs/ADD.md` §3） |
| **S6 规划产物** | `S6_ENTRY_PLAN.md`（`:15` F-1 行同时含两义，**是冲突的原始记录**） | 已冻结（`753b176`）；其 F-1 表述的修正应写在**本报告**，不改原文 |
| **阶段历史报告** | `S1_CHANGE_REPORT.md` · `S2-*` · `S3-*` · `S4-*` · `S5-*`（含 `S5-3_FINAL_ARCHITECTURE_REVIEW.md:357-359` 的 CACHE-STALE→INV-03、NO-GUARD-EVENT→INV-04 映射，**均为 canonical，无冲突**） | 时点记录，改动=伪造历史 |
| **基线快照** | `docs/baseline/*`（`TEST_BASELINE.md:155` 是**唯一**冲突点，用 B 族 INV-02） | 带 `0cba75d` 基线标识的时点快照。**沿用 S2-5.1 先例（Tier C）：只加注、不改数** |
| **历史审计计划** | `REFACTOR_PLAN.md`（`:146` 为唯一冲突点）· `GOVERNED_AGENT_RUNTIME_DESIGN.md` | 历史审计产物（"未提交"基线）；建议**仅加注**指向 Registry |
| **历史矩阵** | `CODE-MATRIX.md:35` | 文档数字整体已严重滞后（S0 基线审计结论）；沿用 Tier C 先例**加注不改数** |
| **生成物** | `docs/architecture.html` / `.svg` · `docs_html/` | 渲染产物，非手写权威；如需一致由生成脚本处理 |

---

## 8. S6-2 的测试映射影响

### 8.1 对 S6-2 计划的直接影响

| S6-2 计划项（`S6_ENTRY_PLAN.md:153-163`） | 受本分类的影响 |
|---|---|
| **①在 `tests/invariants/test_inv_governance.py` 补 INV-G3 专项** | **不受影响**（INV-G* 与 INV-0x 是两族，无编号冲突）。确认：`test_inv_governance.py` 已覆盖 INV-G1/G2/G4/G5/G6 · INV-R1..R8 · INV-E3 · INV-A1..A6，**唯缺 INV-G3**（`EVENT-SCHEMA.md:492` = "`receipt.verify()` 在真源重放后结果不变"）⇒ 与 F-2 一致 |
| **②新增 `tests/invariants/test_inv_core.py` 承 INV-01~09** | **必须先落 §5 裁定**。否则新用例的"INV-02"会在 canonical 与族 B 之间摇摆。具体：INV-02 专项必须钉 **"`llm.chat` 唯一入口"**（canonical），**不得**钉"历史派生"（那是 INV-01） |
| 从 `tests/unit/*` **迁移/重述**断言、**不删除**原始用例 | 与 §6 的迁移清单存在**重叠面**：`test_session.py` 的 INV-02 三处、`test_tools_guard.py` 的 INV-03 六处。建议**先按 §6 订正编号，再迁移**，避免把错误编号复制进 `tests/invariants/` |
| 判据"**失败时能指出违反的具体不变量**" | 依赖编号唯一性 ⇒ **§5 裁定是前置条件** |

### 8.2 INV-01~09 ↔ 现有用例映射（S6-2 的迁移基线）

| ID | 现有编号化用例（文件::函数） | 所在目录 | S6-2 动作 |
|---|---|---|---|
| INV-01 | `test_session.py::test_method_surface_append_only_inv01`、`::test_finished_flush_failure_keeps_closed_but_log_empty`、`test_goal.py::test_rebuild_from_events_matches_live_state`、`test_agent_loop.py::test_single_turn_text_complete`、`test_compaction.py::test_compact_appends_declaration_only_append_only`、`test_plan_mode.py::test_real_session_full_lifecycle_events_valid` 等 | `unit/` | 重述入 `invariants/` |
| INV-02 | `test_agent_loop.py::test_llm_chat_only_entry_point_inv02`（**全库唯一一条**） | `unit/` | **重述 + 补强**（唯一用例，覆盖薄） |
| INV-03 | `test_session.py::test_rebuild_from_log_invariant_inv03`、`::test_rebuild_derived_cache_invalidation_on_append`、`::test_events_after_incremental_gwt_s3_04`、`test_session_query.py::test_rebuild_full_matches_incremental` | `unit/` | 重述（**排除** `test_tools_guard.py` 的 6 条**假标 INV-03**） |
| INV-04 | `test_engine.py::test_guard_from_config_injects_schema_validator`、`::test_s23_tc_policy_rules_carry_injected_params`、`test_tools_guard.py::test_monotonic_api_surface_no_allow`、`::test_evaluate_allow_single_evaluated_event`、`test_tools_executor.py`（模块级） | `unit/` | 重述 |
| INV-05 | `test_tools_guard.py::test_reject_event_pair_order_and_sync`、`::test_scope_hidden_reject_events_grd401`、`::test_reject_deterministic_not_flippable`、`test_tools_executor.py::test_scope_hidden_terminal_reject`、`test_tool_fs.py::test_executor_critical_delete_denied_zero_side_effect`、`test_cli.py::test_run_headless_rejected_listing` | `unit/` | 重述 |
| INV-06 | `test_tools_executor.py::test_param_wrong_type_zero_provider`、`::test_param_fail_extra_field_zero_provider`、`::test_happy_full_pipeline_event_order`、`test_tools_registry.py::test_malformed_args_tlb803_zero_exec`、`::test_non_identifier_property_name`、`test_llm.py::test_assistant_passthrough_raw_json`、`test_spill.py::test_handle_executes_read`、`test_tool_fs.py::test_write_over_1mb_contract_tlb803`、`test_tool_web.py::test_validate_args_search_contract` | `unit/` | 重述 |
| INV-07 | `test_persistence.py::test_cross_process_lock_blocks_second_writer`、`::test_replay_tolerates_crlf_leftover` | `unit/` | 重述（**排除** `test_llm_fallback.py` 的 2 条**假标 INV-07**） |
| INV-08 | `test_agent.py::test_attach_shadowing_spine_member_tlb802`、`test_cli.py::test_chat_pipe_full_path`、`test_commands.py::test_register_command_and_dispatch` | `unit/` | 重述（**排除** `test_agent_loop.py:12,259` 的**假标 INV-08**） |
| INV-09 | `test_config.py::test_redact_masks_secrets`、`::test_web_nonloopback_requires_token`、`test_acp.py::test_engine_error_detail_redacted_and_truncated`、`test_spill.py::test_redact_applied_before_disk`、`test_tool_fs.py::test_read_redact_applied`、`test_tool_web.py::test_fetch_redact_applied_direct_and_spill`、`test_desktop.py::test_render_tool_call_redacts_sensitive_args`、`test_schedule.py::test_register_writes_registered_event_and_job` | `unit/` | 重述 |

> **无一例外：INV-01~09 的编号化用例当前 100% 散落在 `tests/unit/`，`tests/invariants/` 内为 0** ⇒ 与 F-3 一致，是 M8 的正当重写对象。

---

## 9. 当前存在的 Coverage Gaps

### G-1 目录级（F-3 / F-4 / F-5 复核）

| # | 缺口 | 实测证据 |
|---|---|---|
| G-1.1 | **`tests/invariants/` 无任何 `INV-01~09` 标注** | 4 文件：`test_inv_bus.py`（2 用例）/`test_inv_config.py`（2）/`test_inv_events.py`（2）/`test_inv_governance.py`（8 函数，覆盖 INV-G/R/E/A）。**前三者零 `INV-0x`** |
| G-1.2 | **前三文件头部注释失实** | 均写"**当前 RED:bus.py 未实现 / config.py 未实现 / events.py 未实现**"——三模块**早已实现且用例全绿** |
| G-1.3 | **`tests/acceptance/` 空置** | 仅 `__init__.py`（F-4 确认） |
| G-1.4 | **`tests/security/` 不存在** | 目录缺失（F-5 确认） |
| G-1.5 | **`tests/invariants/test_invariants.py` 不存在** | 但 `CONSTRAINTS-06-Testing.md:88-89` 的目录规范仍指向它 |
| G-1.6 | **spec 引用了不存在的测试文件** | `docs/specs/session.py.md:233` 指向 `tests/acceptance/test_f009_session_log.py` 与 `tests/acceptance/test_f018_invariants.py` —— **两者均不存在** |

### G-2 编号级（S6-2 必须消除）

| # | 缺口 | 证据 |
|---|---|---|
| G-2.1 | **`INV-02` 只有 1 条专项用例** | `test_agent_loop.py::test_llm_chat_only_entry_point_inv02`；且该用例名下的断言**一半是 INV-01** |
| G-2.2 | **6 条"假标 INV-03"** | `test_tools_guard.py` 的 6 条 guard 单调用例标 INV-03（实为 INV-04）⇒ 会**虚增** INV-03 覆盖率、**掩盖** INV-04 的真实缺口 |
| G-2.3 | **2 条"假标 INV-07"** | `test_llm_fallback.py:4,182`（降级链，非 INV） |
| G-2.4 | **2 条"假标 INV-08"** | `test_agent_loop.py:12,259`（循环必终止，非 INV） |
| G-2.5 | **`test_tools_guard.py` 内部编号不自洽** | 同一"单调拒绝"语义：5 处标 INV-03、1 处标 INV-04 |

### G-3 治理不变量级（F-2 复核）

| # | 缺口 | 证据 |
|---|---|---|
| G-3.1 | **`INV-G3` 零标注/零专项** | `test_inv_governance.py` 覆盖 G1/G2/G4/G5/G6（无 G3）；`test_inv_r3_r4_r8_hash_tamper_chain` 覆盖的是 **INV-R3**（确定性），与 `INV-G3`（`verify()` 重放后不变）**不是同一条** |

### G-4 文档级（本阶段新发现，供 S6-5 收敛）

| # | 缺口 | 证据 |
|---|---|---|
| G-4.1 | **`docs/baseline/TEST_BASELINE.md` 内部不自洽** | `:155` 称 `INV-02`（**无第二状态**，B 族），`:233` 同文件把 `INV-02/03/06` 作为 canonical 缺口组 |
| G-4.2 | **spec 与实现/测试三处不同源** | `docs/specs/session.py.md`（canonical）↔ `pyharness/core/session.py`（族 B）↔ `tests/unit/test_session.py`（族 B）；`docs/specs/llm_fallback.py.md:17`（canonical INV-02）↔ `tests/unit/test_llm_fallback.py`（族 D INV-07） |
| G-4.3 | **`CODE-MATRIX.md:34` 声称"INV-01~09 不变量测试全绿（invariants/ 12 项）"** | 实测 `tests/invariants/` 共 **14 个测试函数**且**不含任何 INV-01~09 标注** ⇒ 该声称**不成立**（与 S0 基线审计"CODE-MATRIX 整体严重滞后"一致） |
| G-4.4 | **`docs/architecture.html:571` 写"事件词表 73 型 / 全量 1,419 项"** | 实测 **77 型 / 1671 passed** ⇒ 渲染产物滞后（非 INV 编号问题，但同属文档一致性） |

---

## 10. 本阶段边界声明（做了什么 / 没做什么）

**做了：**

- 全库 `INV-0x` 出现次数盘点（812 次 / 138 文件 / 4 层分布）
- `INV-02` 语义三分（37 canonical + 9 族 B + 1 ambiguous），逐处给出 `文件:行`
- 发现并证实**另有三族**（C/D 及 B 的 INV-01 兼容面），修正 F-1 的"两套口径"表述
- `INV-01~09` ↔ 现有用例映射表（供 S6-2 迁移）
- 覆盖缺口清单（目录级 / 编号级 / 治理级 / 文档级）
- 基线回归复现：**1671 passed / 2 skipped / 0 failed**（与 S6_ENTRY_PLAN 一致）

**没做（按任务书 Step 3/4 禁止或未授权）：**

- ❌ 未修改任何 `pyharness/**` 生产代码（含仅注释的改动）
- ❌ 未修改任何测试文件
- ❌ 未修改 Governance Core v1.0 / Event Schema / Decision / Receipt / Evidence / Audit 语义
- ❌ 未创建 `docs/INVARIANT_REGISTRY.md`（**等待 review 授权**）
- ❌ 未创建 acceptance tests / security tests；未修复 L-3 trust path；未解决 deferred debt
- ❌ 未进入 S6-2，未进入 S7
- ❌ **未 commit / 未 push**

**产出：** 本文件（`S6-1_INV_CLASSIFICATION.md`）**唯一**。中间分析数据（`inv_sites.txt` / `inv_coverage.txt` / `junit_s61.xml` 等）全部落在 `tmp/`（`.gitignore` 已覆盖，**不入库**）。

**worktree 状态：** `git status --porcelain` 为空（本文件为新增未跟踪文件）。

---

## 11. 下一步

> ⚠️ **本节已被 §12（最终裁决区，2026-09-15）取代。** 裁定结果见 **§5.3** 的更新表与 **§12**。以下保留原始记载。

1. ~~人工裁定 §5.3 的 R-1~R-6~~ → **已裁定**（§5.3 / §12）。
2. **创建 `docs/INVARIANT_REGISTRY.md`**（**尚未授权**）——以 `docs/SECURITY.md:190-223` 为蓝本，按任务书的 **9 字段**（ID / Name / Canonical Definition / Intent / Verification Target / Existing Evidence / Test Mapping / Legacy Mapping / Status）成表；**本报告 §12.1 已给出全部 7 个可填字段的正式初稿**（ID / Canonical Name / Canonical Definition / Evidence Source / Existing Test Evidence / Legacy Mapping / Coverage Gap）。
3. 迁移 §12.2 的 **19 处**引用（预计零行为变更），复跑全量确认 **1671/2/0** 与 **77/14/3** 不变。
4. 转入 **S6-2**（`tests/invariants/test_inv_core.py` + INV-G3 专项）。

**在收到授权前，本会话停止，不修改编号、不 commit、不 push。**

---
---

# §12 最终裁决区（Final Adjudication · 2026-09-15 人工裁定）

> 本节为**人工裁决后的最终口径**，取代 §5.1/§5.3 的"建议"与前文各处的"待裁定"。
> **本轮唯一产物 = 本文件**。Registry 未创建；代码/测试/schema/ADR 未改；未 commit/push。

## 12.0 裁决原则（正式确立）

| # | 原则 |
|---|---|
| **P-1** | 一个 Canonical INV ID 只能对应**一个**正式语义。 |
| **P-2** | 现有代码 / 测试中的 INV 标记**不能自动视为权威**，只能作为**历史证据**。 |
| **P-3** | 已废弃编号**必须**通过 `Legacy Mapping` 保留历史关系（不得删除历史）。 |
| **P-4** | **不允许**为保留旧编号而重新解释已废弃的语义。 |
| **P-5** | 后续 tests / acceptance / security / reports 全部以 Registry 为**唯一编号来源**。 |

**本轮授权范围**：完成 INV-01~09 最终语义分类、解决 INV-02/03/04/07/08 历史映射、建立 Canonical vs Legacy 映射表、更新本文件。
**本轮明令禁止**：创建 `INVARIANT_REGISTRY.md` · 改生产代码 · 改现有测试 · 改 Event Schema · 改 Governance Core / Audit / Receipt / Evidence · 修 L-3 · 建 acceptance/security tests · 进 S6-2 · commit · push。
⇒ **所有迁移动作的 `Status` 一律为 `pending authorization` / `deferred`，本轮均未执行。**

---

## 12.1 Canonical INV Table（INV-01 ~ INV-09）

### INV-01

| 字段 | 内容 |
|---|---|
| **ID** | INV-01 |
| **Canonical Name** | 日志只追加 / 历史必由日志派生（**合并式**，按 CONSTRAINTS-06 §7） |
| **Canonical Definition** | 会话 JSONL 日志是**唯一真源**且**只追加**（无 update/delete/改写/清空 API）；一切历史与派生形态（消息历史 / 统计 / UI 轨迹 / FTS 索引 / 预算 / todo）**均由日志派生**，不存在第二份权威状态；派生视图可**整体丢弃重建**。 |
| **Evidence Source** | `docs/CONSTRAINTS-06-Testing.md:62`（权威表，合并式）· `docs/PRD-Core.md:838`（上位权威，表述为"历史必由日志派生"）· `PRD-Core.md:308,357,360` · `docs/SECURITY.md:196,206` · `docs/EVENT-SCHEMA.md:13,503,629` · `docs/DIS-CORE.md:28,342,470` · `docs/SOP.md:51`（"INV-01 只追加 … INV-09 脱敏"） |
| **Existing Test Evidence** | `test_session.py::test_method_surface_append_only_inv01`（分句①）· `::test_append_only_no_second_history_store_inv02`（分句②）· `::test_finished_flush_failure_keeps_closed_but_log_empty` · `test_goal.py::test_rebuild_from_events_matches_live_state`（分句②）· `test_plan_mode.py::test_real_session_full_lifecycle_events_valid` · `test_compaction.py::test_compact_appends_declaration_only_append_only`（分句①）· `test_agent_loop.py::test_single_turn_text_complete` |
| **Legacy Mapping** | ← 旧 **INV-02**（session-split，"历史必由日志派生"）**并入本编号**；旧 **INV-01**（session-split，"只追加"）为本编号**第一分句**（兼容，无需迁移）。详见 §12.2 L-1 / L-2 |
| **Coverage Gap** | ① **`append` 后文件 hash 不变**的断言（`CONSTRAINTS-06:62` 明文要求）**全库无专项**；② **"除 session.py 外无 append 到消息列表路径"**（`PRD-Core.md:360` 明文要求）**无静态扫描用例**；③ `tests/invariants/` 内**零**编号化 INV-01 用例 |

### INV-02

| 字段 | 内容 |
|---|---|
| **ID** | INV-02 |
| **Canonical Name** | 无绕过 agent-loop 直调 llm |
| **Canonical Definition** | 全库 `llm.chat` 的**唯一合法调用方 = agent-loop（`run_turn`）**；任何模块禁止直连模型端点、禁止绕过循环（= 禁止绕过轮数/预算/取消三闸）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"无绕过 agent-loop 直调 llm"）· `PRD-Core.md:634`（"禁止任何模块绕过它直调 llm.chat"）· `docs/CONSTRAINTS-06-Testing.md:63` · `docs/SECURITY.md:197,208` · `docs/DIS-CORE.md:28,46,108,186` · `docs/specs/llm.py.md:4,8` · `docs/specs/agent_loop.py.md:4,12,262` · 实现 `pyharness/core/agent_loop.py:6,229` · `pyharness/core/llm.py:5,651` · `pyharness/core/subagent.py:50,799,805` · `pyharness/core/plan_mode.py:14` |
| **Existing Test Evidence** | `test_agent_loop.py::test_llm_chat_only_entry_point_inv02`（**全库唯一**；断言"日志派生的上下文"+"run_turn 之外无 llm.chat 路径"） |
| **Legacy Mapping** | **无**。旧 `INV-02 = 历史派生` **不并入本编号**，而是迁出至 **INV-01**（§12.2 L-1）。 |
| **Coverage Gap** | ① **仅 1 条用例**，且其中一半断言实为 INV-01；② 无"静态调用图 / 全库无第二 llm.chat 出口"的**扫描型**用例；③ `tests/invariants/` 内零编号化用例 |

### INV-03

| 字段 | 内容 |
|---|---|
| **ID** | INV-03 |
| **Canonical Name** | rebuild 与缓存一致 |
| **Canonical Definition** | `rebuild_from_log` / `rebuild_from_events` 后，派生缓存与重建前**逐事件（逐行）一致**；含**增量读一致**（`events_after(last)` == 全量切片）、**编辑重放**（`edited` 覆盖目标行）、**二次 rebuild 无重复**；`history_cache` 仅当日志尾部未变时有效，任何 `append` 后整体失效。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"rebuild 与缓存一致"）· `PRD-Core.md:308,360` · `docs/CONSTRAINTS-06-Testing.md:64` · `docs/SECURITY.md:198,210` · `docs/EVENT-SCHEMA.md:503,526` · `docs/DIS-CORE.md:433,471,481` · 实现 `pyharness/core/session.py:70,143,338,396,473` · `pyharness/core/session_query.py:32,57,678` |
| **Existing Test Evidence** | `test_session.py::test_rebuild_from_log_invariant_inv03` · `::test_rebuild_derived_cache_invalidation_on_append` · `::test_events_after_incremental_gwt_s3_04` · `test_session_query.py::test_rebuild_full_matches_incremental` |
| **Legacy Mapping** | ← **无迁入**。旧 `INV-03 = guard 单调拒绝 / 无放行`（族 C）**不属本编号**，已改判至 **INV-04**（§12.2 L-3）。 |
| **Coverage Gap** | ① 无编号化专项在 `tests/invariants/`；② **`test_tools_guard.py` 的 6 条假标 INV-03 会虚增本编号覆盖率**（编号修正前，"INV-03 覆盖"读数不可信） |

### INV-04

| 字段 | 内容 |
|---|---|
| **ID** | INV-04 |
| **Canonical Name** | 无 guard 事件即非法执行（**含 guard 单调拒绝 / 无 bypass**） |
| **Canonical Definition** | **(a) 执行前必有求值事实**：每个 `tool.result` / `tool.error` 之前必有**同 `call_id`** 的 `guard.evaluated`，缺失 = 非法执行；**(b) 结构单调性**：guard 决策**恰三值** `{allow, reject, approval}`，**无 bypass 第四值**，`GuardChain` **无任何** override / bypass / force-allow / set-allow / execute / 移除 / 重排 / 翻回 API，且**一次 reject 不可被后续挂载翻回 allow**（只增拒绝面）。 |
| **Evidence Source** | **实现（7 处）**：`pyharness/core/tools_guard.py:17`（"单调性三层结构防线(原则 3 / **INV-04**)"）· `:44` · `:622`（"翻回一次拒绝(**INV-04**)"）· `:688`（"恰一条 guard.evaluated(**INV-04**)"）· `:804`（"禁 bypass/放行/执行类方法面(**INV-04**)"）· `:808` · `:815` ｜ **spec**：`docs/DIS-SEAM.md:582`（"执行前必有 guard.evaluated(**INV-04**,缺失=非法执行)"）· `:640`（"**单调性审计点(INV-04)**")· `:814`（"**G4=INV-05**" ⇒ 零副作用归 INV-05，单调面必归 INV-04） ｜ **ADR**：`docs/ADD.md:21`（ADR-003"guard 单调拒绝,无 allow"）· `:33`（ADR-015"保留全量以维持 **INV-04** 与既有 76 用例")· `:117`（"单调性由 INV 断言"）· `ARCHITECTURE_DECISION_RECORD.md:175,181,196,199,201,207,208`（"这是 **INV-04 的载体**"×7） ｜ **权威表**：`PRD-Core.md:768,838` · `CONSTRAINTS-06:65` · `SECURITY.md:199,212` |
| **Existing Test Evidence** | `test_tools_guard.py::test_monotonic_api_surface_no_allow`（**已标 INV-04**）· `::test_evaluate_allow_single_evaluated_event` · 另有 6 条**同义但错标 INV-03**：`::test_decision_tri_value_exact_no_bypass` / `::test_guard_base_contract` / `::test_forbidden_bypass_api_attribute_error` / `::test_reject_deterministic_not_flippable` / `::test_register_after_reject_cannot_rescue` + 模块 docstring · `test_engine.py::test_guard_from_config_injects_schema_validator` · `::test_s23_tc_policy_rules_carry_injected_params` |
| **Legacy Mapping** | ← 旧 **INV-03**（"guard 无放行 / 单调拒绝"，族 C）**并入本编号**（§12.2 L-3）；`CODE-MATRIX.md:35` 的"INV-03 单调拒绝/INV-04 零副作用"两条**均需重指**（→ INV-04 / INV-05，§12.2 L-4） |
| **Coverage Gap** | ① 本编号**名下无任何用例位于 `tests/invariants/`**；② 当前覆盖由 **6 条错标 INV-03 的用例"隐形"承载** ⇒ **编号修正前，INV-04 的真实覆盖无法从编号读出**；③ 无"全库凡执行必经 `tools.execute`"的静态扫描用例 |
| **治理层对应** | `INV-G1`（任何工具执行前必有同 `call_id` 的 `decision.issued`；`GOVERNED_AGENT_RUNTIME_DESIGN.md:566` 明写"对齐 **INV-04**"）· `INV-G5`（治理层无执行/放行 API，对应 (b) 的治理层类比） |

### INV-05

| 字段 | 内容 |
|---|---|
| **ID** | INV-05 |
| **Canonical Name** | 拒绝后零副作用 |
| **Canonical Definition** | 任意 reject（scope / guard / critical / 审批 denied / timeout）后，**Provider 调用计数 = 0** 且**无该 `call_id` 的 `tool.result`**；拒绝须**强同步**落 `guard.rejected`（`sync=True`），落盘失败即 fail-closed 上抛。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"拒绝后零副作用"）· `PRD-Core.md:401,404,1771` · `docs/CONSTRAINTS-06-Testing.md:66` · `docs/SECURITY.md:200,214` · `docs/DIS-SEAM.md:582,650,814`（"G4=INV-05"）· 实现 `pyharness/core/tools_guard.py:13,689,721,832` · `pyharness/core/scope.py:278` · `pyharness/governance/receipt.py:188` |
| **Existing Test Evidence** | `test_tools_guard.py::test_reject_event_pair_order_and_sync` · `::test_scope_hidden_reject_events_grd401` · `::test_reject_deterministic_not_flippable`（亦涉 INV-04）· `test_tools_executor.py::test_scope_hidden_terminal_reject` · `test_tool_fs.py::test_executor_critical_delete_denied_zero_side_effect` · `test_cli.py::test_run_headless_rejected_listing` |
| **Legacy Mapping** | ← **部分迁入**：`CODE-MATRIX.md:35` 曾把"零副作用"标为 **INV-04**（§12.2 L-4） |
| **Coverage Gap** | ① 无编号化专项在 `tests/invariants/`；② "拒绝后零副作用"目前分散在 guard / executor / tool_fs / cli 四处，**无单一编号锚点** |

### INV-06

| 字段 | 内容 |
|---|---|
| **ID** | INV-06 |
| **Canonical Name** | 执行 args = 日志 args |
| **Canonical Definition** | `tool.call` **同时**存 `raw_args`（LLM 原始参数原文）与强类型 `args`（校验后实际执行）；**真实函数收到的参数 == 日志 `args` 逐字段一致**；畸形参数 → `TLB-803` 且 **Provider 零调用**、**不写 `tool.call`**。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"执行 args=日志 args"）· `PRD-Core.md:414,421,972,1858` · `docs/CONSTRAINTS-06-Testing.md:67` · `docs/SECURITY.md:201,216` · `docs/ADD.md:258,264,274`（ADR-002/003 背景）· 实现 `pyharness/core/tools_executor.py:12,22,335,450,461` · `pyharness/core/tools_registry.py:313,592` · `pyharness/core/llm.py:110,129` · `pyharness/errors.py:90` |
| **Existing Test Evidence** | `test_tools_executor.py::test_param_wrong_type_zero_provider` · `::test_param_fail_extra_field_zero_provider` · `::test_happy_full_pipeline_event_order` · `test_tools_registry.py::test_malformed_args_tlb803_zero_exec` · `::test_non_identifier_property_name` · `test_llm.py::test_assistant_passthrough_raw_json` · `::test_multi_calls_parsed_in_order` · `test_spill.py::test_handle_executes_read` · `test_tool_fs.py::test_write_over_1mb_contract_tlb803` · `test_tool_web.py::test_validate_args_search_contract` |
| **Legacy Mapping** | ← 旧义 **"参数非法"**（`CONSTRAINTS-06-Testing.md:54` 废弃清单所列；**库中已无残留站点**；归入本编号为**推定**，非文档明示 —— 见 §12.2 L-7） |
| **Coverage Gap** | ① 无编号化专项在 `tests/invariants/`；② `PRD-Core.md:421` 要求的 **"30 例畸形参数"** 语料规模未在单一用例中体现（现分散于多个用例） |

### INV-07

| 字段 | 内容 |
|---|---|
| **ID** | INV-07 |
| **Canonical Name** | 单进程 |
| **Canonical Definition** | 全库**进程数 = 1**，无 `multiprocessing` / 跨进程 RPC；唯一多进程出口 = **受 guard 的** `subprocess` / PTY；子 Agent / jobs / schedule 全为**协程级并发**，共享主会话日志与 seq 分配器；同一 `{sid}.jsonl` **单写者独占**。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"单进程"）· `PRD-Core.md:435,438` · `docs/CONSTRAINTS-06-Testing.md:68` · `docs/SECURITY.md:202,218` · `docs/ADD.md:164`（ADR-012）· `docs/MAP.md:190,233` · `docs/EVENT-SCHEMA.md:630` · 实现 `pyharness/persistence.py:7,137,192,297,728` · `pyharness/core/schedule.py:8,169,717` |
| **Existing Test Evidence** | `test_persistence.py::test_cross_process_lock_blocks_second_writer` · `::test_replay_tolerates_crlf_leftover` |
| **Legacy Mapping** | ← 旧 **INV-07 = 降级链**（族 D）**不并入本编号**，**移出 INV 族**（§12.2 L-5） |
| **Coverage Gap** | ① 无"全库无 `multiprocessing` import"的**静态扫描**用例；② 无编号化专项在 `tests/invariants/` |

### INV-08

| 字段 | 内容 |
|---|---|
| **ID** | INV-08 |
| **Canonical Name** | 阶段 import 方向 / 依赖方向 |
| **Canonical Definition** | **阶段 N 不得 import 阶段 N+1** 模块（核心脊柱严格无环）；**`governance/` 只允许依赖 `events` + `errors`**（ADR-018:308）；外围能力**只能经 `ctx` 注入**，禁止反向 import Consumer、禁止外围互引。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"阶段 import 方向"）· `PRD-Core.md:461,464,1920` · `docs/CONSTRAINTS-06-Testing.md:69` · `docs/SECURITY.md:203,220` · `docs/ADD.md:236,288`（ADR-010）· `ARCHITECTURE_DECISION_RECORD.md:308,323,336`（ADR-018 扩展）· `docs/MAP.md:190` · 实现 `pyharness/core/agent.py:32,81,345,350,357,459` · `pyharness/core/subagent.py:225,250,805` |
| **Existing Test Evidence** | `test_agent.py::test_attach_shadowing_spine_member_tlb802` · `test_cli.py::test_chat_pipe_full_path` · `test_commands.py::test_register_command_and_dispatch` · `S2-1` 的 **T3 AST 断言**（`governance/**` 零 `core.*` import）· `S2-3.2` 的 T-A/T-B |
| **Legacy Mapping** | ← 旧 **INV-08 = 循环必终止**（族 D）**不并入本编号**，**移出 INV 族**（§12.2 L-6） |
| **Coverage Gap** | ① **无独立的"阶段 N 不 import N+1"静态扫描用例**（现仅治理层方向有 AST 断言）；② 无编号化专项在 `tests/invariants/` |

### INV-09

| 字段 | 内容 |
|---|---|
| **ID** | INV-09 |
| **Canonical Name** | 日志无凭据 / 全出口脱敏 |
| **Canonical Definition** | 事件 / 错误 / 日志 / `spill` / PTY / UI 投影**全出口**均无 **32+ 位疑似密钥原文**（`sk-` 等前缀同样打码）；secret **只存引用**（`env:NAME` / `file:PATH`），明文永不落日志。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"日志无凭据"）· `PRD-Core.md:810,1783,1848` · `docs/CONSTRAINTS-06-Testing.md:70` · `docs/SECURITY.md:204,222` · `docs/CFG.md:15,182,349` · `docs/ERR.md:53,338` · `docs/EVENT-SCHEMA.md:633` · 实现 `pyharness/config.py:149,395,707` · `pyharness/core/spill.py:34,179,296` · `pyharness/acp.py:75,148` · `pyharness/desktop/projection.py:182` |
| **Existing Test Evidence** | `test_config.py::test_redact_masks_secrets` · `::test_web_nonloopback_requires_token` · `test_acp.py::test_engine_error_detail_redacted_and_truncated` · `test_spill.py::test_redact_applied_before_disk` · `test_tool_fs.py::test_read_redact_applied` · `test_tool_web.py::test_fetch_redact_applied_direct_and_spill` · `test_desktop.py::test_render_tool_call_redacts_sensitive_args` · `test_schedule.py::test_register_writes_registered_event_and_job` |
| **Legacy Mapping** | **无**（全库无冲突语义） |
| **Coverage Gap** | ① `PRD-Core.md:810` 要求 **"INV-09 全库 grep"** 的**全库正则扫描**用例**不存在**（现仅为逐出口的定向断言）；② 无编号化专项在 `tests/invariants/` |

> **INV-G 系列（治理层，另一编号族，不参与本表）**：`INV-G1~G6` 定义于 `docs/EVENT-SCHEMA.md:492`，现有覆盖见 `tests/invariants/test_inv_governance.py`（G1/G2/G4/G5/G6 + R1~R8 + E3 + A1~A6）。**唯一缺口 = `INV-G3`**（`receipt.verify()` 真源重放后结果不变），与 F-2 一致。

---

## 12.2 Legacy Mapping Table

> `Status` 取值：`pending authorization`（待授权迁移）· `deferred`（历史载体，仅加注）· `record-only`（仅登记，不动）· `compatible`（兼容，无需迁移）

| # | Legacy Reference | Legacy Meaning | Current Location | Canonical Destination | Migration Action | Status |
|---|---|---|---|---|---|---|
| **L-1** | `INV-02`（session-split） | 历史必由日志派生 / 无第二份状态 | `pyharness/core/session.py:7,177` · `pyharness/acp.py:27` · `tests/unit/test_session.py:5,174,435` · `scripts/demo_phase1.py:5` | **INV-01**（第二分句） | 注释 / docstring / 文案：`INV-02` → `INV-01`（7 处） | **pending authorization** |
| **L-2** | `INV-01`（session-split） | 日志只追加 | `session.py:5,79,271,274` · `test_session.py:4,154,161,170` · `message_edit.py:3,5` · `test_compaction.py:11,451` 等 | **INV-01**（第一分句） | **无需迁移**（canonical INV-01 为合并式，本义为其子集） | **compatible** |
| **L-3** | `INV-03`（旧 guard 编号） | guard 单调拒绝 / 无放行 / 无 bypass | `tests/unit/test_tools_guard.py:6,197,245,247,269,305` · `scripts/demo_phase1.py:61,71` | **INV-04**（结构单调面） | 注释 / 文案：`INV-03` → `INV-04`（8 处） | **pending authorization** |
| **L-4** | `INV-03` + `INV-04`（`CODE-MATRIX.md:35`） | "INV-03 单调拒绝 / INV-04 零副作用" | `CODE-MATRIX.md:35` | **INV-04** / **INV-05** | **加注不改数**（历史矩阵；沿用 S2-5.1 Tier C 先例） | **deferred** |
| **L-5** | `INV-07`（retired） | 降级链 / 降级生效（主模型 5xx/401 后走备用） | `tests/unit/test_llm_fallback.py:4,182` | **无（移出 INV 族）** → **F013 / F028** 功能验收 | 去编号，改标 `F013/F028`（2 处） | **pending authorization** |
| **L-6** | `INV-08`（retired） | 循环必终止（`max_turns` 护栏） | `tests/unit/test_agent_loop.py:12,259`（+ 函数名 `test_max_turns_truncation_inv08`） | **无（移出 INV 族）** → **F007** 循环护栏 | 去编号，改标 `F007`；函数名去 `_inv08`（1 处改名，须单独授权） | **pending authorization** |
| **L-7** | 旧义 **"参数非法 / 强同步不丢 / 降级生效"** | 参数不合规拦下不执行 / 强同步事件不丢 / 降级真发生 | **库中无残留站点**（仅见于 `docs/CONSTRAINTS-06-Testing.md:54-55` 的废弃清单） | 推定：参数非法→**INV-06**；强同步不丢→**无对应 INV**（F-SYNC-1 + CONSTRAINTS-04 承载）；降级生效→同 L-5 | **仅登记**（**推定归属，非文档明示**；`CONSTRAINTS-06` 未给出编号对位，不猜测） | **record-only** |
| **L-8** | 双写 `INV-01/02` | 同处引用两个含义 | `pyharness/core/session.py:347` · `REFACTOR_PLAN.md:146` | **INV-01** | 消歧：`(INV-01/02)` → `(INV-01)`；`REFACTOR_PLAN` 为历史产物 → **仅加注** | `session.py:347` **pending authorization** / `REFACTOR_PLAN` **deferred** |
| **L-9** | `INV-02`（session-split，历史载体） | 历史必由日志派生 | `docs/baseline/TEST_BASELINE.md:155` · `S6_ENTRY_PLAN.md:15` | **INV-01** | 前者**只加注不改数**（基线快照）；后者**不改**（冻结的冲突原始记录） | **deferred** / **record-only** |
| **L-10** | 区间写法 `INV-01~09` | 全集引用（非单一语义） | 15 个文件，33 次 | INV-01~09（整体） | **无需迁移** | **compatible** |

**统计**：Legacy 语义 4 类（L-1/L-3/L-5/L-6）· 可迁移站点 **22 处 / 9 文件** · 其中待授权 **19 处**（L-1 7 + L-3 8 + L-5 2 + L-6 2）· 仅加注/登记 **3 处**（L-4 1 · L-9 2）· 兼容无需迁移 `INV-01 只追加` 与区间写法（L-2/L-10）。

---

## 12.3 Conflict Resolution（最终处理方式）

### 12.3.1 INV-01 / INV-02 —— 拆分回并，**不新增编号**

**事实**：`session.py` 把 canonical INV-01 的**两个分句拆成两个槽位**，用掉了 INV-02。

**为什么历史 `INV-02 = historical derivation` 应映射回 canonical `INV-01`：**

1. **canonical INV-01 是合并式**：`CONSTRAINTS-06-Testing.md:62` 逐字为"日志只追加 / 历史必由日志派生"；`docs/SOP.md:51` 亦以"INV-01 只追加"指代它。⇒ "历史必由日志派生"**本就是 INV-01 的第二分句**，不是独立不变量。
2. **它是"拆分"而非"另一套编号"的结构证据**：`session.py` 的 `INV-03 = rebuild 一致` 与 canonical INV-03 **一致**，`INV-08 = 单向依赖` 与 canonical INV-08 **一致** ⇒ **只有 01/02 的边界被重画**，其后编号未漂移。⇒ 把 INV-02 号归还给 canonical 语义即可，**无需**为"派生"另立 ID。
3. **P-4 的约束**：为"历史派生"新分配一个 Canonical ID，等于**为保留旧编号而重新解释语义** ⇒ 违 P-4。
4. **权威链一致**：canonical INV-02（"无绕过 agent-loop 直调 llm"）在**实现侧有 5 处独立且自洽的使用**（`agent_loop.py:6,229`、`llm.py:5,651`、`subagent.py:50,799,805`、`plan_mode.py:14`）+ **spec 侧 11 处** ⇒ 该槽位归属明确、无可让渡空间。

**哪些现有测试只是 INV-01 的子性质（应标注为 INV-01 分句，而非新编号）：**

| 现有用例 | 实为 INV-01 的哪个分句 |
|---|---|
| `test_session.py::test_method_surface_append_only_inv01` | 分句①（只追加：方法面无 update/delete/改写） |
| `test_session.py::test_append_only_no_second_history_store_inv02` | 分句②（无第二份历史存储） |
| `test_session.py::test_derive_only_changes_via_append_inv02` | 分句②（历史只随日志事件变化） |
| `test_session.py::test_finished_flush_failure_keeps_closed_but_log_empty` | 分句①（终态拒写落点） |
| `test_goal.py::test_rebuild_from_events_matches_live_state` | 分句②（内存态 ≡ 日志派生态） |
| `test_plan_mode.py::test_real_session_full_lifecycle_events_valid` | 分句② |
| `test_compaction.py::test_compact_appends_declaration_only_append_only` | 分句①（原文零改动，只尾追一条） |
| `test_agent_loop.py::test_single_turn_text_complete` / `test_tool_round_then_text_counts` | 分句②（上下文由日志现派生） |

**需要未来补覆盖（而不是新增编号）的地方：**

1. **`append` 后文件 hash 不变**（`CONSTRAINTS-06:62` 明文断言）—— 实测 `tests/` 内**无任何**针对会话 JSONL 的 hash 不变断言。
2. **"除 `session.py` 外无 append 到消息列表路径"**（`PRD-Core.md:360`）—— 需**全库静态扫描**用例。
3. **`derive` 结果 == 逐事件重放** 的编号化专项（现仅在 goal/plan_mode 侧间接覆盖）。
4. 上述三项均应作为 **INV-01 的专项用例**进入 `tests/invariants/`，**不占新编号**。

### 12.3.2 INV-03 / INV-04 —— 单调面归 INV-04，**证据链裁定**

> 你要求"不要依据文件里的注释直接定案"。以下结论**不依赖任何被质疑的注释**，而由**实现正文 + spec + ADR + 同文件反例**四重证据得出。

**（1）当前真正应保留的 Canonical INV-03 = "rebuild 与缓存一致"**

| 证据 | 出处 |
|---|---|
| 权威表逐字 | `CONSTRAINTS-06-Testing.md:64` · `SECURITY.md:198` · `PRD-Core.md:838` |
| 协议详解 | `EVENT-SCHEMA.md:503,526`（纯函数约束 / `history_cache` 纪律）· `DIS-CORE.md:433,471,481` |
| 实现 | `session.py:70,143,338,396,473`（`history_cache` 失效与重建）· `session_query.py:32,57,678`（"重建与增量逐行一致"） |
| 测试 | `test_session.py::test_rebuild_from_log_invariant_inv03` · `test_session_query.py::test_rebuild_full_matches_incremental` |
| 独立性反证 | `INV-03` 在 `tests/invariants/` **与 guard 无关**；而 INV-04/05 的载体是 `tools_guard.py`（工具链），**与 rebuild 毫无语义交集** |

⇒ 保留不动。

**（2）当前真正应保留的 Canonical INV-04 = "无 guard 事件即非法执行" + 其结构面"guard 单调拒绝 / 无 bypass"**

**语义证据链（11 点，四类来源）：**

| 类 | 证据 | 说明了什么 |
|---|---|---|
| **实现（7 处，同一模块）** | `tools_guard.py:17`"单调性三层结构防线(原则 3 / **INV-04**)" · `:622`"翻回一次拒绝(**INV-04**)" · `:804`"禁 bypass/放行/执行类方法面(**INV-04**)" · `:808` · `:44` · `:688`"恰一条 guard.evaluated(**INV-04**)" · `:815`"evaluated 审计点(**INV-04**)" | **被判为"错标"的模块自己的正文把单调面无歧义地挂在 INV-04** |
| **spec** | `DIS-SEAM.md:640`"**单调性审计点(INV-04)**" · `:582`"执行前必有 guard.evaluated(**INV-04**,缺失=非法执行)" | 契约层同样把"单调性"与"INV-04"绑定 |
| **ADR / 冻结记录** | `ADD.md:21`（ADR-003 = "guard 单调拒绝,无 allow"，且 `ADD.md:117` 明写"**单调性由 INV 断言**"）· `ADD.md:33`（ADR-015：保留 `guard.evaluated` 全量以维持 **INV-04** 与 76 用例）· `ARCHITECTURE_DECISION_RECORD.md:175,181,196,199,201,207,208`（"这是 **INV-04 的载体**"） | 架构决策层把 guard 的结构面与事件面**统归于 INV-04** |
| **构造性反证（排除 INV-05）** | `DIS-SEAM.md:814`"**§6.2 G4=INV-05** 组件级展开" + `:582`"任一 `G_i=reject` ⇒ 终局，其后**零副作用(INV-05)**" | "零副作用"已被 INV-05 占位 ⇒ 单调面**只能**归 INV-04 |

**（3）`guard monotonic rejection` 应属于哪个 Canonical ID ⇒ `INV-04`**

理由：canonical INV-04 的**完整命题**是"**guard 是唯一且不可绕过的判定点**"，它有两个可验证面 —— **(a) 求值事实必存在**（执行前必有 `guard.evaluated`）与 **(b) 结构上不可绕过/不可翻回**（无 bypass API、拒绝单调）。仓库的实现、spec、ADR **一致地**把二者都记在 INV-04。⇒ 单调拒绝**是 INV-04 的一个面**，不是独立编号。

**（4）旧 `INV-03` / `INV-04` 各自如何进入 Legacy Mapping**

| 项 | 处置 |
|---|---|
| **旧 `INV-03` = guard 单调拒绝 / 无放行** | `CONSTRAINTS-06-Testing.md:53-58` 声明 `INV-03~08` 曾承载与 PRD 不一致的语义、并已按 PRD 对齐；其废弃清单**逐字含"guard 无放行"**。残留站点（`test_tools_guard.py` 等 8 处）**自证其槽位为 INV-03**。**⚠️ 精确性声明**：该文档**未逐条给出"哪个旧义对哪个编号"**，故"guard 无放行 = 旧 INV-03"是**由残留站点反推**，而**非**文档逐字明示。⇒ 进入 **L-3**，目标 **INV-04**，`pending authorization`。 |
| **旧 `INV-04`（"参数非法" / "零副作用"）** | ① 若指"参数非法"：**库中无残留站点**，仅见于 `CONSTRAINTS-06:54` 的废弃清单，编号对位不可确定 ⇒ 进入 **L-7**，`record-only`，归属**推定**为 INV-06。② 若指"零副作用"：该误标**仍存活**于 `CODE-MATRIX.md:35`，⇒ 进入 **L-4**，目标 **INV-05**，`deferred`（仅加注）。 |

**（5）同文件内部不自洽（须在编号修正后消除）**

`test_tools_guard.py` 对**同一语义**（单调拒绝 / 无 bypass）给出**两个编号**：`::test_monotonic_api_surface_no_allow` 标 **INV-04**（正确），其余 5 处标 **INV-03**（错误）。⇒ 修正后该文件的 INV-03 出现次数应为 **0**。

### 12.3.3 INV-07 —— 降级链**移出 INV 族**，但性质**确有价值**

| 项 | 结论 |
|---|---|
| **Retired legacy reference** | `tests/unit/test_llm_fallback.py:4`（"`INV-07` 载体"）、`:182`（"降级真的发生(`INV-07` 载体)"） |
| **历史语义** | "降级链：主模型 5xx/401 后请求走备用"（`LLM-302` 直降备用；`LLM-303` exhausted） |
| **文档级自证** | `CONSTRAINTS-06-Testing.md:72-74` **点名本文件**并逐字写明："它是**功能验收(F013/F028)**，**不是 INV 编号**——早期把它记作 **INV-07** 的做法**已废弃**。" |
| **性质是否仍有价值** | ✅ **有**。降级真发生是可观测、可回归的真实行为，且已被 `test_llm_fallback.py::test_302_direct_degrade_happens` 断言（用例本身**保留**，只去掉编号归属）。 |
| **非冲突纳入方式** | ① 编号用 **`F013` / `F028`**（功能验收），**不占 INV 编号**；② 事件留痕面（`degraded_from` 字段）已在 `docs/SOP.md:101` 作为 F013 的硬判据使用；③ 若 S6-3 acceptance 要纳入，作为 **F013 验收用例**进入 `tests/acceptance/`，**不新增 INV**。 |
| **不允许的做法（P-4）** | ❌ 不得把"降级链"重新定义为某个 Canonical INV，也不得把 INV-07 的槽位改作他用。 |

### 12.3.4 INV-08 —— 循环必终止**移出 INV 族**，但性质**确有价值**

| 项 | 结论 |
|---|---|
| **Retired legacy reference** | `tests/unit/test_agent_loop.py:12`（"循环必终止(`INV-08`)"）、`:259`（"循环必终止铁律(`INV-08`)"）；函数名 `test_max_turns_truncation_inv08` |
| **历史语义** | "任何输入在 `max_turns` 轮内结束，不存在无限工具轮" |
| **文档级自证** | `CONSTRAINTS-06-Testing.md:56-58` **点名本文件**并逐字写明："'循环必终止'**不是不变量编号**，而是 **F007 循环护栏**（由 `max_turns` 用例覆盖，见 `tests/unit/test_agent_loop.py`）。" |
| **性质是否仍有价值** | ✅ **有**。循环有界是该框架"死循环烧钱"的结构性解药，已被该用例断言（用例**保留**，只去掉编号归属）。 |
| **非冲突纳入方式** | ① 编号用 **`F007`**（循环护栏），**不占 INV 编号**；② 用例名去 `_inv08`（`test_max_turns_truncation`）；③ 若要提升为**不变量**，须走**新 ADR**（ADR 只增不改）并经人工裁定 —— **本轮不建议、不执行**。 |
| **不允许的做法（P-4）** | ❌ 不得为"循环必终止"新设 Canonical INV；❌ 不得用 INV-08 槽位承载它（canonical INV-08 = 阶段 import 方向）。 |

### 12.3.5 冲突处理的净结果

| 编号 | 处理 | 迁移前 | 迁移后（语义归属） |
|---|---|---:|---:|
| INV-01 | **吸收** session-split 的 INV-02（9）+ 歧义 1 处 | 234 | **244** |
| INV-02 | **归还**给 canonical 语义（"无直调 llm"）；B 9 + C 1 迁出 | 47 | **37** |
| INV-03 | **归还**给"rebuild 与缓存一致"；族 C 9 处迁出 | 56 | **47** |
| INV-04 | **吸收**旧 INV-03 的单调面（+9）；同时 1 处"零副作用"误标迁出 | 90 | **98** |
| INV-05 | **吸收** `CODE-MATRIX.md:35` 的"零副作用"误标（+1） | 86 | **87** |
| INV-06 | 无冲突 | 90 | **90** |
| INV-07 | **剥离**降级链（−2，移出 INV 族） | 39 | **37** |
| INV-08 | **剥离**循环必终止（−2，移出 INV 族） | 85 | **83** |
| INV-09 | 无冲突 | 85 | **85** |
| *（移出 INV 族）* | 降级链 2 + 循环必终止 2 | — | **4** |

**归属校验**：244+37+47+98+87+90+37+83+85 = **808**；808 + 4（移出族）= **812** ✅ 与 §1.1 总数闭合。
⇒ **冲突 22 → 0**（唯余 `S6_ENTRY_PLAN.md:15` 的 1 处，**属有意保留的历史记录**，见 L-9）。
⇒ 本表为**语义归属表**：一个 token 的**文本**编号可能未变（如 `CODE-MATRIX.md:35` 仍写 `INV-04`），但其**语义归属**已改判；反之族 C 的 9 处文本仍写 `INV-03`，语义已归 INV-04。

---

## 12.4 Future Cleanup（**本轮不执行**，待授权）

> 依裁决第六条：**不授权修改 `pyharness/**` 中的 docstring / comment**。以下为由此产生、且**必须登记以免遗失**的清单。

| # | 位置 | 拟改内容 | 为何延后 |
|---|---|---|---|
| **FC-1** | `pyharness/core/session.py:7` | `INV-02` → `INV-01`（模块 docstring） | 生产代码，本轮禁改 |
| **FC-2** | `pyharness/core/session.py:177` | `INV-02` → `INV-01`（方法 docstring） | 同上 |
| **FC-3** | `pyharness/core/session.py:347` | `(INV-01/02)` → `(INV-01)`（行内注释） | 同上 |
| **FC-4** | `pyharness/acp.py:27` | `INV-02` → `INV-01`（模块 docstring） | 同上 |
| **FC-5** | `pyharness/core/session.py:5,79,271,274` | 保持 `INV-01`（**不改**）—— 与合并式一致 | 无需动作，登记以免被误改 |
| **FC-6** | `tests/unit/test_session.py:5,174,435` | `INV-02` → `INV-01` | 测试文件，本轮禁改 |
| **FC-7** | `tests/unit/test_agent_loop.py:12,259` + 函数名 `test_max_turns_truncation_inv08` | 去 INV-08，改标 F007 护栏；函数名去 `_inv08` | 测试文件，本轮禁改；改名需单独授权 |
| **FC-8** | `tests/unit/test_tools_guard.py:6,197,245,247,269,305` | `INV-03` → `INV-04` | 测试文件，本轮禁改 |
| **FC-9** | `tests/unit/test_llm_fallback.py:4,182` | 去 INV-07，改标 F013/F028 | 测试文件，本轮禁改 |
| **FC-10** | `scripts/demo_phase1.py:5,61,71` | `INV-02`→`INV-01`；`INV-03`→`INV-04` | 脚本，本轮未授权 |
| **FC-11** | `tests/invariants/test_inv_{bus,config,events}.py` 头部 | 移除失实的"**当前 RED：模块未实现**" | 测试文件，本轮禁改（原 R-6） |
| **FC-12** | `docs/specs/session.py.md:233` | 引用不存在的 `tests/acceptance/test_f009_session_log.py` / `test_f018_invariants.py` | 文档，本轮未授权（G-1.6） |
| **FC-13** | `CODE-MATRIX.md:34,35` | `:34` "invariants/ 12 项" 失实；`:35` `INV-03 单调拒绝/INV-04 零副作用` → `INV-04/INV-05` | 历史矩阵 → **加注不改数**（L-4） |
| **FC-14** | `docs/baseline/TEST_BASELINE.md:155` | `INV-02（无第二状态）` → 加注指向 INV-01 | 基线快照 → **只加注**（L-9） |

**计**：**待授权编号迁移 19 处** = FC-1 · FC-2 · FC-4 · FC-6 · FC-7 · FC-8 · FC-9 · FC-10（对应 L-1 7 + L-3 8 + L-5 2 + L-6 2）。
**另计**（不属上述 19 处）：**消歧 1 处** FC-3（L-8）· **R-6 测试头部修正 3 文件** FC-11 · **仅加注 2 文件** FC-13/FC-14 · **登记不动 1 处** FC-5 · **待授权文档修正 1 处** FC-12。
⇒ 合计涉及 **13 个文件**（`session.py` · `acp.py` · `test_session.py` · `test_agent_loop.py` · `test_tools_guard.py` · `test_llm_fallback.py` · `demo_phase1.py` · `tests/invariants/test_inv_{bus,config,events}.py`〔3 文件〕· `CODE-MATRIX.md` · `docs/baseline/TEST_BASELINE.md` · `docs/specs/session.py.md`）。

---

## 12.5 结论与状态

| 项 | 状态 |
|---|---|
| INV-01~09 最终语义分类 | **完成**（§12.1） |
| Canonical vs Legacy 映射表 | **完成**（§12.2） |
| INV-02 / INV-03 / INV-04 / INV-07 / INV-08 历史映射 | **已裁定**（§12.3） |
| `S6-1_INV_CLASSIFICATION.md` 更新 | **完成**（本节） |
| `docs/INVARIANT_REGISTRY.md` | **未创建**（等待授权） |
| 迁移动作（19 处） | **未执行**（`pending authorization`） |
| 生产代码 / 测试 / schema / ADR / Governance Core | **未修改** |
| commit / push | **未执行** |

**`git status`**：仅 `?? S6-1_INV_CLASSIFICATION.md`（未跟踪）；**HEAD 仍 `753b176`**；worktree 其余部分 clean。

**本轮结束。等待 review 最终映射后，再授权建立 `docs/INVARIANT_REGISTRY.md`。**
