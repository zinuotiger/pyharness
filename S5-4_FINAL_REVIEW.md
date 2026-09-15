# S5-4_FINAL_REVIEW.md — 治理对账与旧审计面兼容适配（最终阶段审查）

> **审查对象**：S5-4 checkpoint **`e263b28`** `feat: add governance reconciliation and legacy audit adapter`
> **审查日期**：2026-09-15 ｜ **审查性质**：**只读** · 未修改代码/测试/契约 · 未新增 checkpoint · 未 push
> **适用读者**：后续接手治理层与 S6 阶段的工程人员
> **术语约定**：英文术语后附中文解释，形如 `AuditSystem（审计系统）`
> **实测基线**：**1671 passed / 2 skipped / 0 failed** · `EVENT_TYPES=77` / `SYNC_TYPES=14` / `TRANSIENT_TYPES=3`

---

## 0. 判定摘要

| 项 | 结论 |
|---|---|
| **S5-4 Final Review** | # **PASS** |
| **是否建议正式 Freeze S5-4** | ✅ **建议 Freeze** |
| **是否满足进入 S6 的入口条件** | ✅ **满足** |
| **阻塞项** | **无** |
| **S6 Entry Gate** | 见 §6.4 |

---

## 1. Governance Core v1.0 Freeze Boundary（治理核心 v1.0 冻结边界）

### 1.1 S5-4 的改动面（实测）

`git diff --name-only dada674..HEAD` = **恰 5 项**：

```
S5-4_CHANGE_REPORT.md
pyharness/engine.py
pyharness/governance/audit.py
tests/invariants/test_inv_governance.py
tests/unit/test_governance_audit.py
```

⇒ **S5-4 未触碰任何冻结契约文件**。

### 1.2 冻结项的最后改动 commit（实测）

| 冻结文件 | 最后改动 | 是否被 S5-4 触碰 |
|---|---|---|
| `governance/policy.py` | `e23484a`（S2/S3-1 骨架） | ❌ 未触碰（自 S3-1 起**从未再改**） |
| `governance/decision.py` | `ccc58c3`（S3-2-1 授权边界） | ❌ 未触碰 |
| `governance/receipt.py` | `f191c85`（M4 凭证） | ❌ 未触碰 |
| `governance/evidence.py` | `f72651e`（S5-2 证据索引） | ❌ 未触碰 |
| `governance/context.py` | `a53da99`（S5-3b **仅类型标注**） | ❌ 未触碰（S5-4 未改） |
| `events/payload.py` · `events/vocab.py` | `8454d46`（S5-1 事件注册） | ❌ 未触碰 |

⇒ **冻结语义无间接破坏**：S5-4 的全部改动落在 `engine.py` 的**装配面**（注入一行）与 `audit.py` 的**新增方法**上。

### 1.3 已冻结事件语义未变（实测）

| 事件 | registered | ∈ `SYNC_TYPES` | 说明 |
|---|---|---|---|
| `decision.issued` | ✅ True | ✅ **True** | 强同步（M3 冻结） |
| `receipt.emitted` | ✅ True | ✅ **True** | 强同步（M4 冻结） |
| `evidence.archived` | ✅ True | ❌ **False** | 普通攒批（M6 冻结;`INV-E3`） |
| `syscheck.fail` | ✅ True | ❌ **False** | 普通攒批（既有;S5-4 **首次使用**，语义未改） |

**计数**：`77 / 14 / 3` —— 与 S5-4 前**完全一致**（S5-4 **零 schema 变更**）。

---

## 2. Audit Layer Boundary（审计层边界）

### 2.1 `causal_chain()` 必须保持零写 —— ✅ 满足

| 证据 | 结果 |
|---|---|
| **行为**：调用后事件数 | 实测 `15 → 15` **不变** |
| **结构（AST）**：`audit.py` 内对会话对象的 `append` 调用点 | **仅 `reconcile` 一处**（见 §2.5） |
| 既有回归 | S5-3 的只读用例全绿（§4.1） |

### 2.2 `denied_report()` 必须保持零写 —— ✅ 满足

同上（行为 + AST 双重证据）。

### 2.3 `legacy_session_audit()` 不得直接依赖 `core.*` —— ✅ 满足

| 证据 | 结果 |
|---|---|
| `audit.py` 的 import 集合 | `__future__` · `dataclasses` · `typing` · **`pyharness.errors`** · **`pyharness.events.envelope`** · **`pyharness.governance.receipt`** |
| `pyharness.core*` 命中 | **NONE** |
| `telemetry` 命中 | **NONE** |
| 机制 | 经 **注入** 的 `legacy_audit` callable 转调（装配层注入 `core.telemetry.session_audit`） |

**注意**：`events.envelope` 的新导入是 **`governance → events`** 方向（ADR-018 **允许**的方向），用于复用既有 `check_seq_gap`（**不新建第二套空洞检测**）。

### 2.4 `audit.py` 保持零 `core` import —— ✅ 满足

见 §2.3（AST 实测 `core 命中 = NONE`）。**同时** `subscribe` 调用 = **False**（仍不订阅任何事件）。

### 2.5 `reconcile()` 是唯一受控写点 —— ✅ 满足

**AST 实测**：全文件中「对会话对象的 `append`」**仅出现在 `reconcile` 方法内**（`append 调用点(方法) = ['reconcile']`）。

### 2.6 只有显式 `emit=True` 且 findings 非空时才允许产生一条既有 `syscheck.fail` —— ✅ 满足

**行为实测（真实日志）**：

```
只读面零写        = True (15 -> 15)          ← causal_chain ×2 + denied_report
emit=False 零写   = True | findings=2        ← ★ findings 非空仍零写
emit=True 写一条  = True | syscheck=1        ← 恰一条
幂等(findings 同) = True                     ← 二次 emit 不引入新发现
```

**判据实现**：`if emit and findings:` —— 两个条件**同时**成立才进入写分支;写入 `{"findings": [...], "trigger": "governance.reconcile"}`,类型为**既有** `syscheck.fail`（**不新增事件类型**）。

### 2.7 `emit=False` 必须严格零写 —— ✅ 满足（★核心断言）

见 §2.6 第二行;**T-4（★）** 专测「findings 非空 + 默认 `emit=False` ⇒ 零写入」。

---

## 3. S5-4 不变量（逐条：实现 / 测试覆盖 / 证据）

### 3.1 `INV-A4` — Determinism（确定性）

| 项 | 内容 |
|---|---|
| **实现** | `reconcile` **无状态**;每次即时重放;findings 生成后 `findings.sort()` 保证顺序稳定 |
| **测试覆盖** | `test_t7_deterministic_across_instances`（不同实例 ⇒ findings 完全相等）;`test_t6_reconcile_is_idempotent`（连续两次 ⇒ 相同）;`test_inv_a4_a5_a6_reconcile_and_legacy`（不变量文件） |
| **证据** | 实测 `f == f2 == f3`（三次调用一致） |

### 3.2 `INV-A5` — Default Read-Only + Unique Controlled Write Point（默认只读 + 唯一受控写点）

| 项 | 内容 |
|---|---|
| **实现** | `causal_chain` / `denied_report` **零写**;`reconcile` 默认 `emit=False` 零写;唯一写动作 = `emit=True` 时的既有 `syscheck.fail` |
| **测试覆盖** | `test_t4_default_emit_is_false_zero_writes`（★）· `test_t4_emit_true_writes_exactly_one_syscheck_fail` · `test_t5_emit_true_but_clean_log_writes_nothing` · `test_t12_schema_unchanged` |
| **证据** | 行为实测四行（§2.6）+ **AST 断言**：全会话 `append` 仅 `reconcile` 一处 |

### 3.3 `INV-A6` — Legacy Equivalence + Injection Discipline（兼容等价 + 注入纪律）

| 项 | 内容 |
|---|---|
| **实现** | `legacy_session_audit` **透传**注入的 callable;未注入 ⇒ `CYC-999` fail-closed |
| **测试覆盖** | `test_t9_legacy_delegates_with_same_session`（同一 session 转发）· `test_t9_legacy_result_equals_telemetry`（**结果等价**）· `test_t10_legacy_not_injected_is_fail_closed` · `test_t11_no_core_telemetry_import`（AST） |
| **证据** | AST：`telemetry` 命中 NONE · `pyharness.core*` 命中 NONE;装配测试：真实 spine 下 `legacy_session_audit(ctx.session) == telemetry.session_audit(ctx.session)` |

**三条不变量与既有不变量的关系**：`INV-A4` ↔ `INV-A1`（确定性/不臆造）同族;`INV-A5` 是 `INV-A2`（纯只读）的**精确化**，与 `INV-E4`（`archive` 唯一写点）同型;`INV-A6` 是 `ADR-018:308` 在兼容适配场景的具体化。

---

## 4. Regression Safety（回归安全）

### 4.1 S5-3 既有只读回归

| 项 | 结果 |
|---|---|
| `tests/unit/test_governance_audit.py`（S5-3 19 例 + S5-4 15 例） | **34 passed** |
| 只读面筛选（`causal_chain` / `denied_report` 相关） | **22 passed**（含参数化） |
| 不变量文件 | **17 passed**（含 INV-A1~A6） |

### 4.2 `causal_chain` / `denied_report` 无行为变化

| 证据 | 结果 |
|---|---|
| **签名未变** | `causal_chain(decision_id, *, ctx=None) -> dict` · `denied_report(*, session_id="", since_seq=0, ctx=None) -> list[dict]` |
| **输出结构未变** | 11 键 / 9 键，逐一断言（T-1 / T-4 既有用例） |
| **既有用例** | 全绿（未修改任何断言） |
| **改动面** | `reachable`：S5-4 **只新增**方法;既有方法体**零改动** |

### 4.3 事件 schema 未变

`EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` —— **与 S5-4 前完全一致**。

★ **唯一**被调整的既有测试断言：`test_t6_no_side_effects` 的 AST 扫描**收窄**为「仅两个只读方法内不得写」（Δ-2，已人工接受）——**行为断言（事件数不变）保持不变**，新写点边界由 `INV-A5` + T-4/T-5/T-6 独立覆盖，**非放松**。

---

## 5. Deferred Debt（延期债务,仅登记与风险评估）

### D-1：`CACHE-STALE` 暂缓

| 项 | 内容 |
|---|---|
| **未实现的内容** | `reconcile` 的第四类检查 —— 比对"派生视图 vs 从日志重算"（如凭证券链头、证据索引条数） |
| **延期理由** | **避免 `AuditSystem` 第一阶段依赖 `EvidenceCollector` 内部结构**（若依赖,该索引会事实上成为"对账的前提",逼近第二真源,威胁 `INV-R5 / INV-E3`） |
| **风险等级** | **低** —— 现有三类检查已覆盖"日志内部一致性";缺失的是"**缓存**与真源"的一致性 |
| **暴露面** | 若某派生缓存静默腐化（如 `ReceiptStore._last_hash` 与日志不符）,当前 `reconcile` **不会报**;但该腐化会经 `verify_chain` / 后续 `receipt.emitted` 的 `prev_hash` 显现 |
| **解除前置** | `EvidenceCollector` 提供**稳定的只读快照面**（当前只有 `evidence_count()` / `collect_for_task()`;`ReceiptStore` 亦需类似面） |
| **建议归属** | S6（测试面）或独立 durability 阶段 |

### D-2：`syscheck.fail` 非强同步

| 项 | 内容 |
|---|---|
| **事实** | `syscheck.fail` **不在 `SYNC_TYPES`**（普通攒批）⇒ 崩溃可丢 |
| **为何可接受** | findings 是**建议性**事实:**可重跑 `reconcile` 复得**;不被任何不变量依赖;不参与任何检查（故幂等成立） |
| **风险等级** | **低** —— 与 `evidence.archived` 同型（非强同步的派生性事实） |
| **与 F-SYNC-1 的关系** | 该事件**不享用**强同步的 fail-closed 保证;但因其可再派生,**不构成数据完整性缺口** |
| **建议** | 维持现状;若未来要求"对账历史必须持久可证",再评估升为强同步（**须**走 ADR 变更） |

### D-3：`reconcile` 为 O(n) 全量重放

| 项 | 内容 |
|---|---|
| **事实** | 每次调用即时全量重放（与 `causal_chain` / `denied_report` 同源） |
| **为何可接受** | 对账是**低频按需**行为（人/CI 触发），非热路径;v1.0 优先**正确性 / 可复现性 / 单一真源** |
| **风险等级** | **低** —— 会话规模增长时线性变慢 |
| **唯一允许的优化方向** | 加**一次性派生缓存**,且**必须**满足"删除后可由日志完全重建";**不得**引入任何**持久化**（否则造出第二真源，违反 `INV-R5`） |
| **建议** | 维持现状;S7 引入索引时一并测量 |

**另登记（跨阶段,不属 S5-4）**：Freeze Declaration 的 **L-3**（信任名单命中路径不产生凭证且无持久审批留痕）—— 建议在 **S6 钉死口径**。

---

## 6. S5-4 Freeze 判定

### 6.1 判定

# S5-4 = PASS

| 支撑 | 说明 |
|---|---|
| **冻结边界** | ✅ S5-4 改动面恰 5 项,**未触碰任何冻结契约文件**;冻结事件语义（`decision.issued` / `receipt.emitted` / `evidence.archived`）与计数（77/14/3）**完全未变** |
| **审计层边界** | ✅ 七项要求全部满足（§2）;`causal_chain`/`denied_report` **零写**、`reconcile` **唯一受控写点**、`emit=False` **严格零写**、**零 `core` import**、**不订阅** |
| **不变量** | ✅ `INV-A4 / A5 / A6` 三条各有实现 + 测试覆盖 + 实测证据（§3） |
| **回归安全** | ✅ S5-3 只读面全绿;两方法**签名与输出结构零变化**;schema **零变化**（§4） |
| **债务** | ✅ 三项**均为低风险**且有明确解除路径（§5）;**无 P0/P1 阻塞项** |

### 6.2 是否建议正式 Freeze S5-4

# ✅ 建议 Freeze

**理由**：S5-4 的全部交付（`reconcile` / `legacy_session_audit`）已达成、已被测试与不变量双重钉死、且**未改变任何已冻结语义**;其延期债务**不影响治理正确性**（`CACHE-STALE` 只影响"缓存腐化"这一额外检测面;另两项为性能与持久性权衡）。

### 6.3 是否满足进入 S6 的入口条件

# ✅ 满足

| 前置 | 状态 |
|---|---|
| S5-4 = PASS | ✅ |
| worktree clean | ✅（HEAD `e263b28`,ahead 28,未 push） |
| 全量回归 | ✅ **1671 passed / 2 skipped / 0 failed** |
| 事件 schema 冻结 | ✅ `77 / 14 / 3` |
| 治理核心冻结契约未破坏 | ✅ |
| M6（Evidence）+ M7（Audit）交付判据 | ✅ 均可证（证据按任务聚合 · 因果链可还原 · 对账可自检） |

### 6.4 S6 Entry Gate（S6 入口闸门）

**S6 范围**（冻结计划 §5.2 S6 行）：**M8** 不变量与测试面 + **M9** 激活空置测试面。

| # | 闸门条件（Entry） | 状态 |
|---|---|---|
| **E-1** | S5-4 已 Freeze（checkpoint `e263b28`） | ✅ |
| **E-2** | worktree clean;全量绿;schema 冻结 | ✅ |
| **E-3** | 治理层 7 模块齐备（`__init__` / `context` / `decision` / `policy` / `receipt` / `evidence` / `audit`） | ✅ |
| **E-4** | 既有 `tests/invariants/` 不因 S6 改动而放松（**CORE-03**：禁止为适配新形态放松安全断言） | ⬜ S6 执行纪律 |

| # | S6 交付（DoD） |
|---|---|
| **D-1** | `tests/invariants/` **重写覆盖 INV-01~09 + INV-G1~G6**（当前 4 文件：bus / config / events / governance —— 需补齐 INV-01~09 的系统性覆盖） |
| **D-2** | 激活空置面：`tests/acceptance/`、`tests/security/` **各 ≥1 真实用例**,含「**批准 A 执行 B 必须被拒**」 |
| **D-3** | 六条 `INV-G1~G6` 均有**专项用例**且绿 |
| **D-4** | 若暴露**既有缺陷** ⇒ 记入 `docs/KEY-FINDINGS.md` 并按 P 级分期,**不阻塞**（冻结计划明文） |

| # | S6 入口附带事项（建议,非阻塞） |
|---|---|
| **N-1** | 在 S6 钉死 **L-3**（信任路径无凭证/无留痕）的口径（专项不变量测试） |
| **N-2** | `F-SYNC-1` 的 durability boundary（`write + fh.flush()`,不含 fsync/WAL/power-loss）继续作为**显式范围限定**;`F-SYNC-1` 对 **S4 凭证**仍是 C 级阻塞,对 S6 无阻塞 |
| **N-3** | 结转三项债务（§5）;`CACHE-STALE` 若有稳定快照面可在 S6 内补 |
| **N-4** | `TraceabilityMatrix` 属 **S7**（不得提前实现） |

---

## 7. 只读确认

未修改任何代码 / 测试 / 冻结契约;未新增 checkpoint;未 commit;未 push;worktree 仍 **clean**（HEAD `e263b28`）。

**本文件为只读审查产物**，生成依据为 S5-4 checkpoint 的实际代码与实测结果。
