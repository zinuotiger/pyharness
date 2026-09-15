# S6-2b_MIDPOINT_REVIEW.md — S6-2b 阶段中点冻结检查

> **阶段**：S6-2b Midpoint Review（**只读**，进入 S6-2b-3 / INV-04 前的阶段性冻结检查）
> **日期**：2026-09-15 ｜ **HEAD**：**`c44c9ce`** ｜ `main...origin/main [ahead 36]` ｜ worktree **clean**
> **本文件性质**：**只读审查**。**未创建测试 · 未修改代码 / Registry / Event Schema / Governance Core · 未进入 INV-04 · 未 commit / 未 push**。
> **核验方式**：全部结论**独立复算**（git 逐 commit 核验、重跑定向与全量、独立读 KEY-FINDINGS 与覆盖文档）。

---

## 0. 一句话结论

> **S6-2b 至今 5 个 commit 全部落在授权范围内；生产代码仅 `50c19ca` 一处且为已授权修复；冻结面逐项 0 改动；全量 `1683 passed / 2 skipped / 0 failed`；`P0` 级残余风险 = 0。**

---

## 1. Coverage Matrix 当前状态（INV-01 / INV-02 / INV-03）

| INV | Canonical 语义要点 | `covered` | `partial` | `gap` | `deferred` | 一致性判定 |
|---|---|---:|---:|---:|---:|---|
| **INV-01** | 日志只追加 / 历史必由日志派生 | **5** | **2** | **0** | **2** | ✅ 与 `S6-2b-1_FINAL_REVIEW.md` §7 **逐条一致** |
| **INV-02** | 无绕过 agent-loop 直调 llm | **1**<sup>※</sup> | **0** | **0** | **0** | ✅ 与 `S6-2a-P0-F_FINAL_REVIEW.md` §12 **一致** |

> <sup>※</sup> **量纲说明**：INV-02 未像 INV-01/03 那样拆成多条子性质；上表该列填 **1** 表示"**其单一语义面已 covered**"。**"三闸面"不计入本行** —— 它已被裁定**明确划归 KF-B**（独立登记），不混入 INV-02 的计分（详见 §1.2）。
| **INV-03** | rebuild 与缓存一致 | **8** | **0** | **0** | **3** | ✅ 与 `S6-2b-2_FINAL_REVIEW.md` §8 **逐条一致** |

### 1.1 INV-01 明细（5 / 2 / 0 / 2）

| 状态 | 子性质 |
|---|---|
| ✅ covered（5） | ①无改写 API（方法面）②**append 后文件 hash 不变**（物理面）③无第二份历史存储 ④历史只随 append 变化 ⑤派生视图可整体重建 |
| ⚠️ partial（2） | ⑥**唯一写入口（静态面）**——T3/T4 只覆盖「直接内容写盘」形式；漏报 `os.fdopen`（`core/kv.py:65` 实例，**非会话日志域**）· `sqlite3`（FTS 派生投影）· `shutil` · `os.replace` ⇒ **当前无违规，静态保证不完整**<br>⑦`derive` ≡ 逐事件重放（仅 goal/plan_mode 间接覆盖，`SessionLog.derive_messages()` 自身无编号化断言） |
| ⚪ deferred（2） | ⑧跨进程并发 append 的 append-only 编号化 · ⑨扫描器覆盖增强（**本轮只分析不增强**） |

### 1.2 INV-02 明细

| 项 | 状态 |
|---|---|
| **定义面**（`llm.chat` 唯一合法调用方 = agent-loop） | ✅ **covered** —— 4 重证据（3 静态 + 1 行为）+ 反证；`docs/KEY-FINDINGS.md` **KF-A = CLOSED** |
| **三闸面**（`auto_title` 是否受 budget/cancel 约束） | ❌ **不在 INV-02 的定义面内** —— 已由裁定**明确划归 KF-B**（**独立登记**，不混入 INV-02 的计分） |
| 编号化用例 | 5 条（`test_inv02_*`：3 静态 + 2 行为），落 `tests/invariants/test_inv_core.py` |

### 1.3 INV-03 明细（8 / 0 / 0 / 3）

| 状态 | 子性质 |
|---|---|
| ✅ covered（8） | 全量↔增量一致 · 二次 rebuild 无重复 · **编辑重放（双侧）** · cache invalidation · 重建 ≡ 重放结果 · **无副作用** · **确定性**（附 scope note） · 空/新/边界 session |
| ⚪ deferred（3） | D-1 `replay()` seq 去重 · D-2 `session_query` rebuild O(n) · D-3 跨进程 rebuild |

### 1.4 编号化用例总量（独立复算）

`tests/invariants/test_inv_core.py` = **12 条**：**INV-02 × 5** · **INV-01 × 4** · **INV-03 × 3** ✅

---

## 2. Checkpoint 核验（4 个）

| # | commit | 主题 | 文件数 | 变更量 | 含生产代码 | 生产文件 |
|---|---|---:|---|---:|---|---|
| 1 | **`50c19ca`** | `fix: restore F042 mini LLM endpoint` | 6 | **+686 / −3** | ✅ **是（已授权）** | `core/auto_title.py`（+2/−3）· `core/llm.py`（+13/−0） |
| 2 | **`c5fa851`** | `docs: freeze INV-02 remediation final review` | 2 | **+248 / −2** | ❌ 否 | — |
| 3 | **`9349596`** | `test: establish INV-01 invariant coverage` | 3 | **+603 / −0** | ❌ 否 | — |
| 4 | **`c44c9ce`** | `test: establish INV-03 invariant coverage` | 4 | **+792 / −0** | ❌ 否 | — |

### 2.1 逐项确认（人工指定）

| 检查 | 结果 |
|---|---|
| **是否存在未提交修改** | ✅ **否** —— `git status --porcelain` **空**（worktree clean） |
| **是否存在生产代码意外变更** | ✅ **否** —— 逐 commit 扫描 `4f05c95..HEAD`（**S6-1 Freeze 起点**）：**唯一**动过 `pyharness/` 的是 **`50c19ca`（2 文件）**，即**已授权的 KF-A 修复本体**，无任何意外变更 |
| **是否存在 Registry 修改** | ✅ **否** —— `docs/INVARIANT_REGISTRY.md` 自 `4f05c95` 起 **0 改动** |
| **是否存在 Event Schema 修改** | ✅ **否** —— `docs/EVENT-SCHEMA.md` **0 改动**；`pyharness/events` **0 改动** |
| **是否存在 Governance Core 修改** | ✅ **否** —— `pyharness/governance` **0 改动** |
| **L-3（`approval.py`）** | ✅ **0 改动**（未处理） |

### 2.2 S6-2 期间的完整 commit 序列（`4f05c95` → HEAD）

```
142765e  docs: freeze S6-2 P0 remediation design
50c19ca  fix: restore F042 mini LLM endpoint              ← 唯一含生产代码
c5fa851  docs: freeze INV-02 remediation final review
9349596  test: establish INV-01 invariant coverage
c44c9ce  test: establish INV-03 invariant coverage        ← HEAD
```

### 2.3 `4f05c95..HEAD` 的完整 diff 面（15 文件）

| 类别 | 文件 | 计数 |
|---|---|---:|
| 报告 / 分析文档 | `S6-2_COVERAGE_MATRIX` · `S6-2a-P0_CHANGE_REPORT` · `S6-2a-P0-F_{PLAN,CHANGE_REPORT,FINAL_REVIEW}` · `S6-2b-1_{CHANGE_REPORT,FINAL_REVIEW}` · `S6-2b-2_{COVERAGE_AUDIT,CHANGE_REPORT,FINAL_REVIEW}` | 10 |
| 登记 | `docs/KEY-FINDINGS.md`（+40/−0，附录 A） | 1 |
| 规格 | `docs/specs/llm.py.md`（+17，`mini` 小节） | 1 |
| **生产代码** | `pyharness/core/auto_title.py` · `pyharness/core/llm.py` | **2** |
| 测试 | `tests/invariants/test_inv_core.py` | 1 |
| **Registry / Event Schema / Governance Core** | **无** | **0** |

---

## 3. KEY-FINDINGS 核验

| 项 | 状态 | 证据（独立读取） |
|---|---|---|
| **KF-A** | ✅ **`CLOSED`** | `docs/KEY-FINDINGS.md:166` = `CLOSED(2026-09-15, commit 50c19ca) —— 仅限本条的"出口选择"违约;F042 其余 6 项与 KF-B 仍各自独立登记` |
| **KF-B** | ✅ **`OPEN`** | `docs/KEY-FINDINGS.md:179` = `` `OPEN` `` |
| **是否遗漏新增 Finding** | ✅ **无** —— 全文件仅 2 处状态行（166 / 179），**无第三个 Finding**；S6-2b-1 / S6-2b-2 两次审查均判定"**未发现真实生产缺陷**" ⇒ 无新增 KEY-FINDING |
| 附录 A 的登记口径 | ✅ 仅**追加**（`4f05c95..HEAD` 的 numstat = **+40/−0**），**未修改** §1~§6 任何既有条目 | — |

> **口径提醒**：`KF-A = CLOSED` **仅**表示"错误出口选择"这一违约点已消除（INV-02 GREEN）；**不表示** F042 其余 6 项或 KF-B 已解决。

---

## 4. Residual Risks 汇总（P0 / P1 / P2 / P3 重新分类）

> 分类口径：**P0** = 阻塞阶段推进的违约/安全问题 · **P1** = 资源或控制面缺口 · **P2** = 行为符合性/已声明风险 · **P3** = 可维护性/读数精度/明确延期项

### 🔴 P0 — **无**

| 项 | 说明 |
|---|---|
| — | **本阶段无 P0 级残余** —— 唯一曾经的 P0（KF-A / INV-02 违约）**已于 `50c19ca` 修复并判定 CLOSED**；截至本时点**无其他不变量违约、无安全问题** |

### 🟠 P1（1 项）

| # | 风险 | 状态 | 说明 |
|---|---|---|---|
| **P1-1** | **KF-B**：系统工具类 LLM 出口（`chat` 之外的 `summarize`/`json_chat`/`mini`）**不经**轮数/取消/预算三闸；且 `summarize(prompt, budget=400)` 的 `budget` **从未被执行** | **`OPEN`** | 已达成的判定：**独立于 INV-02**（不用 `llm.chat` ⇒ 不违约）；**未新建 INV 编号**；建议随 durability/reliability 阶段评估。**不在本阶段修复** |

### 🟡 P2（3 项）

| # | 风险 | 状态 | 说明 |
|---|---|---|---|
| **P2-1** | **F042 Behavior Compliance 6 项** | **DEFERRED** | `first[:200]→[:500]` · `≤24→TITLE_MAX=64` · failure fallback（"前 20 字符"）· empty fallback · `test_f042_title.py` · `auto_title` 模块 spec —— **均未处理**，且**未因新增 `mini()` 顺带修改** |
| **P2-2** | **L-3 trust path** | **未处理** | `approval._trust_hit` 命中即 `granted`（不落 `approval.requested`、不产生 Receipt）；`_enabled` **默认 False** ⇒ 默认配置不可达。S6 范畴为**只验证不修复**（修复需改 `approval.py`） |
| **P2-3** | **INV-01 子性质 #6「唯一写入口（静态面）」= `partial`** | **登记** | 扫描器漏报面：`os.fdopen`（`core/kv.py:65`）· `sqlite3`（FTS 派生投影）· `shutil` · `os.replace/rename`。**当前经独立全量枚举确认无违规**（皆非会话日志域或可重建投影）⇒ 属**覆盖范围限制**而非缺陷 |

### 🟢 P3（9 项）

| # | 风险 | 状态 | 说明 |
|---|---|---|---|
| **P3-1** | INV-01 子性质 #7 `derive ≡ 逐事件重放` 无**编号化**直接断言 | 登记 | 现由 goal/plan_mode 用例间接覆盖 |
| **P3-2** | `test_inv02_loop_is_the_only_dynamic_entry_dispatcher` **硬编码 `agent_loop.py:241` 行号** | 登记 | `agent_loop.py` 在该行**之前**的任何增删都会令其**误报 RED**（失败响亮、不影响正确性）；建议改为"恰 1 个派发点且文件 == `agent_loop.py`" |
| **P3-3** | `mini` 为新增公开 API，**全仓无 LLM 门面 Protocol**（鸭子类型） | 登记 | 理论误用面：以 `mini` 承载对话轮（丢 tools/轮语义）；类别由 spec + 3 条静态用例声明 |
| **P3-4** | INV-01 子性质 #8 跨进程 append-only **无编号化断言** | DEFERRED | `INV-07` 已覆盖单写者互斥 |
| **P3-5** | **D-1** `replay()` 无 seq 去重（与写路径 `_resolve_by_seq` 不对称） | DEFERRED | **可达性未达成**（rotation 用 `os.replace` 不复制 · repair 只截尾/隔离） |
| **P3-6** | **D-2** `session_query` rebuild 的 O(n) 性能面 | DEFERRED | 与正确性语义无关 |
| **P3-7** | **D-3** 跨进程 rebuild（= INV-03 确定性 scope note 的对应项） | DEFERRED | 需多进程测试设施 |
| **P3-8** | **FC-1~FC-14（19 处 / 13 文件）编号迁移** | `pending authorization` | S6-1 遗留清单；其中 **FC-8 / FC-13** 涉及 `test_tools_guard.py` 的 6 条**误标 INV-03** ⇒ 使**按编号读出的覆盖读数失真**（本轮新增的编号化用例**不受影响**） |
| **P3-9** | `tests/invariants/test_inv_{bus,config,events}.py` 头部"**当前 RED：模块未实现**"**失实** | 登记（= FC-11） | 三模块早已实现且用例全绿 |

---

## 5. 冻结与回归核验（独立复跑）

| 项 | 结果 |
|---|---|
| **全量** | ✅ **1685 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1683 passed / 2 skipped / 0 failed**（43.4s，exit 0） |
| `tests/invariants/` | ✅ **29 passed** |
| **Event Schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ |
| **生产代码** | 自 `4f05c95` 起仅 `50c19ca`（已授权）✅ |
| **Registry / Event Schema / Governance Core / L-3** | **逐项 0 改动** ✅ |
| **worktree** | **clean** ✅ |
| **branch** | `ahead 36 / behind 0`（**未 push**）✅ |
| **mutation 残留 / 临时文件** | 无（三轮 mutation 全部还原；`.bak` 备份已清理）✅ |

---

## 6. 结论与建议

| 项 | 结论 |
|---|---|
| **Coverage 状态是否一致** | ✅ **一致** —— INV-01（5/2/0/2）· INV-02（covered）· INV-03（8/0/0/3）与各自 Final Review **逐条吻合** |
| **checkpoint 是否干净** | ✅ 4 个 commit 全部在授权范围内；无未提交修改；无意外生产代码变更；冻结面 0 改动 |
| **KEY-FINDINGS 是否正确** | ✅ KF-A `CLOSED` · KF-B `OPEN` · **无遗漏新增** |
| **是否有 P0 残余** | ✅ **无** |
| **是否可进入 S6-2b-3 / INV-04** | ✅ **`READY`**（**不自动开始**，等待人工授权） |

**进入 S6-2b-3 前建议知悉（非阻塞）**：
1. **P2-3 / P3-8** 共同意味着"**按注释中的 INV 编号读覆盖**"仍不可信 —— S6-2b-3 若沿用编号化用例的方式（本文件所在 `test_inv_core.py` 的体例），不受影响；若依赖 `test_tools_guard.py` 的旧标注，则需先处理 FC-8/FC-13（**pending authorization**）。
2. **D-1（replay 去重）** 与 **P2-1（F042）** 均有明确归属，**不应混入** INV-04 范围。
3. INV-04 的审计可**复用**本阶段已验证的两件工具：`_wired_log`（真实 store 接线）与"**静态扫描 + 白名单 + 双向校验**"体例。

---

**S6-2b Midpoint Review 结束。结论：checkpoint 干净、覆盖一致、无 P0 残余、可进入 INV-04。未 commit、未 push。**
