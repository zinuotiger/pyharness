# S6_ENTRY_PLAN.md — S6 入口闸门与执行计划（不变量与测试面）

> **阶段**：S6（M8 不变量与测试面 + M9 激活空置测试面）
> **日期**：2026-09-15 ｜ **基线 commit**：**`92c8eb6`**（S5-4 最终审查冻结）｜ **HEAD** `92c8eb6` ｜ worktree clean ｜ ahead 29
> **性质**：**仅规划与约束确认** —— 本文件**不实现任何 S6 功能**、**不修改代码/测试/schema/Governance Core**。
> **依据**：冻结计划 `ARCHITECTURE_DECISION_RECORD.md` §5.2 S6 行 · `docs/CONSTRAINTS-06-Testing.md`（INV-01~09 权威表）· `docs/EVENT-SCHEMA.md:492`（INV-G1~G6）· S5-4_FINAL_REVIEW.md §6.4
> **实测基线**：**1671 passed / 2 skipped / 0 failed** · `EVENT_TYPES=77` / `SYNC_TYPES=14` / `TRANSIENT_TYPES=3`

---

## 0. 入口勘察结论（实测,供裁决）

| # | 事实 | 影响 |
|---|---|---|
| **F-1** | **INV 编号存在两套口径**：`docs/CONSTRAINTS-06-Testing.md`（权威表）把 **INV-01 = 「日志只追加 / 历史必由日志派生」（合并）**、**INV-02 = 「无绕过 agent-loop 直调 llm」**;而 `pyharness/core/session.py` 与 `tests/unit/test_session.py` 采用 **INV-01 = append-only**、**INV-02 = 历史必由日志派生**、**INV-03 = rebuild 一致** | ⚠️ **S6 必须先裁定编号口径**,否则"M8 覆盖 INV-01~09"的用例会编码歧义编号 |
| **F-2** | **`INV-G3`（`receipt.verify()` 重放后不变）在 `tests/` 内零标注** —— 行为已由 `test_governance_receipt.py` 的 rebuild/verify 用例覆盖,但**未以不变量编号标注**,更未落入 `tests/invariants/` | S6 需补 **INV-G3 专项用例** |
| **F-3** | `tests/invariants/` 现为 **4 文件**：`test_inv_bus.py` · `test_inv_config.py` · `test_inv_events.py` · `test_inv_governance.py`。**前三者不含任何 `INV-0x` 标注** | INV-01~09 的编号化覆盖**不在** `tests/invariants/` 内（散落在 `tests/unit/*`）⇒ 正是 M8 要重写的对象 |
| **F-4** | `tests/acceptance/` **真空置**（仅 `__init__.py`） | M9 需激活 |
| **F-5** | **`tests/security/` 不存在** | M9 需**新建**（含"批准 A 执行 B 必拒"首例） |
| **F-6** | `tests/invariants/test_inv_governance.py` 已含 **INV-G1/G2/G4/G5/G6** + INV-A1~A6 + INV-E1~E4 | M8 只需**补 G3** 与**编号化整理**,不需重写全部 |

---

## 1. S6 目标

| # | 目标 | 说明 |
|---|---|---|
| **G-1** | **不变量测试面强化** | `tests/invariants/` 对 **INV-01~09** 与 **INV-G1~G6** 建立**编号化、有专项用例**的覆盖 |
| **G-2** | **acceptance tests（验收测试）** | 激活 `tests/acceptance/`：走**真实外壳**（非 mock 通道）的端到端验收用例 |
| **G-3** | **security tests（安全测试）** | 新建 `tests/security/`：聚焦"越权必拦"的安全断言 |
| **G-4** | **安全边界回归** | 确认既有安全断言**零放松**（CORE-03）;新增用例**不得**为过测试而改产品代码 |
| **G-5** | **Governed Agent Runtime（治理型 Agent 运行时）关键行为验证** | 端到端验证治理核心命题：**决策可追 / 凭证可验 / 越权必拦** |

**S6 的性质（冻结计划 §5.2）**：M8/M9 均为**测试**，**不触及安全主干**（"触及安全主干 = 否"）。

---

## 2. S6 Entry Gate（入口闸门）

| # | 闸门条件 | 当前状态 | 证据 |
|---|---|---|---|
| **E-1** | **S5-4 Freeze** | ✅ **已满足** | `92c8eb6 docs: freeze S5-4 final review`（S5-4 = PASS） |
| **E-2** | **worktree clean + 全量测试绿** | ✅ **已满足** | `git status --porcelain` 空;`1671 passed / 2 skipped / 0 failed` |
| **E-3** | **Governance Layer（治理层）7 个核心模块完整** | ✅ **已满足** | `governance/` = `__init__.py` · `context.py` · `decision.py` · `policy.py` · `receipt.py` · `evidence.py` · `audit.py` |
| **E-4** | **CORE-03 执行纪律** | ⬜ **S6 执行期约束** | 禁止为适配新形态而**放松**任何安全断言;禁止为"证明测试通过"而伪造问题 |

**附加闸门（本次勘察新发现,建议人工确认）**：

| # | 条件 | 说明 |
|---|---|---|
| **E-5** | **INV 编号口径裁定**（F-1） | 必须先确定以 `docs/CONSTRAINTS-06-Testing.md` 还是以代码/测试现状为权威（**建议:以 CONSTRAINTS-06 为准**,并同步订正代码 docstring 中的编号引用） |
| **E-6** | **事件 schema 冻结** | ✅ `77 / 14 / 3`;S6 **不得**新增/修改任何事件类型 |

**E-1~E-4 全部满足** ⇒ **S6 Entry = READY**（E-5 为**执行期第一步**,不阻塞进入）。

---

## 3. S6 DoD（完成定义）

| # | 交付 | 判据 |
|---|---|---|
| **D-1** | **`tests/invariants/` 覆盖 INV-01~09** | 九条**各有编号化专项用例**且绿;编号口径与 E-5 裁定一致 |
| **D-2** | **`tests/invariants/` 覆盖 INV-G1~G6** | 六条**各有专项用例**（现已有 G1/G2/G4/G5/G6 ⇒ **补 G3**） |
| **D-3** | **`tests/acceptance/` 非空** | ≥1 **真实用例**,走真实外壳（**无 mock 通道**）;覆盖治理核心命题之一（决策可追 / 凭证可验 / 越权必拦） |
| **D-4** | **`tests/security/` 建立且非空** | ≥1 真实用例;**必须含「批准 A 执行 B 必须被拒」** |
| **D-5** | **既有安全断言零放松** | 全量回归绿;且 S6 的 diff **不含**对既有安全断言的删除/弱化（逐条复核） |
| **D-6** | **KEY-FINDINGS 记录规则** | 若 S6 暴露**既有产品缺陷** ⇒ 记 `docs/KEY-FINDINGS.md`,**按 P 级分期**,**不阻塞**（冻结计划明文）;并与既有登记（F-SYNC-1 / R-7 / R-8 / R-9 / S1.5 / L-3）去重 |
| **D-7** | **全量回归** | ≥1671 passed / 0 failed;`77 / 14 / 3` 不变;新增用例数如实登记 |

---

## 4. S6 明确禁止事项

| # | 禁止 | 说明 |
|---|---|---|
| **N-1** | **不修改 Governance Core v1.0 冻结契约** | `policy.py` · `context.py` · `decision.py` · `receipt.py` · `evidence.py` · `audit.py` 的**语义**不得改（新增测试文件不受此限） |
| **N-2** | **不修改已冻结事件语义** | 不新增/改名/改通道事件类型;`EVENT_TYPES=77` / `SYNC_TYPES=14` / `TRANSIENT_TYPES=3` 保持不变 |
| **N-3** | **不新增 DAG / workflow** | 冻结点（ADR-017） |
| **N-4** | **不进入 S7 `TraceabilityMatrix`** | 追溯矩阵与端到端 Demo 属 S7 |
| **N-5** | **不提前解决 deferred debt** | `CACHE-STALE` · `syscheck.fail` 非强同步 · `reconcile` O(n) · F-SYNC-1 · R-7/R-8/R-9/S1.5 —— **除非 S6 明确证明其为本阶段阻塞项**（届时**先报告**,不自行修复） |
| **N-6** | **不为"证明测试通过"而伪造问题** | 禁止：把产品缺陷写成"预期行为";把测试改成迎合现状;把 `xfail`/`skip` 当作解决 |
| **N-7** | **不放松现有安全断言** | CORE-03;禁止删除/弱化既有 `guard` / `approval` / `scope` / 落盘安全断言 |
| **N-8** | **不修改核心生产代码**（默认） | S6 为测试阶段;若确需产品改动 ⇒ **停止并报告**,由人工裁定 |

---

## 5. 已知跨阶段风险：L-3 trust path（信任路径）

### 5.1 当前现状

| 项 | 内容 |
|---|---|
| **机制** | `approval.py::_trust_hit` 在信任名单命中时,**直接返回 `"granted"`** —— **不落 `approval.requested`、不经 `_settle`** |
| **后果 1** | `_ref_slots` 无记录 ⇒ `approval_ref_of(call_id)` 返回 `None` ⇒ D2 的 `approval_ref = None` |
| **后果 2** | `receipt_kind_of(D2)` 判定为 **D1 类**（`verdict=approval` 且 `approval_ref is None`）⇒ **该次真实执行不产生 Receipt** |
| **后果 3** | `approval.trust_*` 事件**未注册**（`approval.py:837` 明文降级为 debug 日志）⇒ 信任命中**零持久留痕** |
| **默认状态** | `_enabled` **默认 `False`**（`SECURITY §5.4`）⇒ **默认配置下不可达** |

### 5.2 风险

| 维度 | 评估 |
|---|---|
| **数据完整性** | **低** —— Receipt 缺失属"未产生证明",非"证明错误";不违反 `INV-G2`（该路径不产生 `approval.granted`） |
| **审计可追溯性** | **中** —— 一次真实执行在日志中**无 approval 证据、无 receipt**;事后无法从日志证明"该次执行曾获信任授权" |
| **不变量违反** | **无** —— 已核验：不违反 INV-G1/G2/G3/G4/G5/G6 与 INV-R1~R8 |
| **误读风险** | **中** —— 若把 M4 的凭证覆盖理解为"每次执行都有凭证",在信任开启时不成立 |

### 5.3 S6 是否验证

| 项 | 决定 |
|---|---|
| **S6 是否验证** | ✅ **是（验证现状,不改变行为）** —— 在 S6 建立**专项不变量用例**,断言 trust 路径的**当前语义**（"无 approval 请求 ⇒ 无 approval 凭证"是**一致语义**,不是 bug）,使该口径**显式、可回归** |
| **验证形式** | 归入 `tests/invariants/`（治理不变量面）;**不**归入 `tests/security/`（非"越权"命题） |

### 5.4 S6 是否修复

| 项 | 决定 |
|---|---|
| **S6 是否修复** | ❌ **否** —— S6 为测试阶段;修复需改 `approval.py`（生产代码）,超出 S6 范围 |
| **修复归属** | 独立阶段（建议与 durability/reliability 或 S7 后一并评估）;修复方案须含：为 trust 授予建立 identity **或** 明确将 trust 路径纳入"无凭证"的正式语义 |

### 5.5 什么条件下才允许进入修复

**四个条件同时满足**方允许启动修复：

1. **人工明确授权**"为 L-3 做产品改动"（S6 默认禁止改产品代码）;
2. 先**完成 S6 的口径钉死用例**（现状被测试固定,确保修复不会"顺手改语义"）;
3. 修复方案**不违反** `INV-G2`（"每条 `approval.granted` 必对应 receipt"）与"**不伪造 identity**"原则 —— 即不得为 trust 路径**虚构** approval_id;
4. 修复**不触** Governance Core 冻结契约（若必须触 ⇒ 走**新 ADR**,ADR 只增不改）。

---

## 6. S6 分阶段执行计划

> **通则（每阶段适用）**：只动**测试文件**与**本阶段文档**;禁止触 `pyharness/**` 生产代码（除非人工裁定）;每阶段结束跑**全量回归**并 **checkpoint**。

---

### S6-1 — INV 编号口径裁定 + 覆盖盘点（**文档阶段,零代码**）

| 项 | 内容 |
|---|---|
| **输入** | `docs/CONSTRAINTS-06-Testing.md`（INV-01~09 表）· `docs/EVENT-SCHEMA.md:492`（INV-G1~G6）· 全库 `INV-0x` 引用现状 |
| **修改范围** | **新增** `S6-1_INV_ADJUDICATION.md`（编号权威裁定 + **覆盖矩阵**：每条 INV ↔ 现有用例文件/用例名 ↔ 缺口）;**不改** 任何代码/测试 |
| **禁止触碰** | 全部 `pyharness/**` · 全部 `tests/**` · ADR · schema |
| **测试** | 无（本轮不改测试）;仅跑**基线全量**确认 `1671/2/0` |
| **验证** | 覆盖矩阵**逐条可追溯**（每格给出 `文件::用例名` 或标"缺口"） |
| **checkpoint** | `docs: adjudicate INV numbering and inventory coverage` |
| **退出条件** | **F-1 编号争议已裁定**（建议 CONSTRAINTS-06 为权威） + 覆盖矩阵完成 + 人工确认 |

---

### S6-2 — `tests/invariants/` 补强（INV-G3 + INV-01~09 编号化）

| 项 | 内容 |
|---|---|
| **输入** | S6-1 的裁定与覆盖矩阵 |
| **修改范围** | ① 在 `tests/invariants/test_inv_governance.py` **补 INV-G3 专项**;② **新增** `tests/invariants/test_inv_core.py`（承 INV-01~09 的编号化专项;可**迁移/重述**自 `tests/unit/*` 的既有断言,**不删除**原始用例） |
| **禁止触碰** | `pyharness/**` 全部;`tests/unit/**`（除必要的**编号注释**订正,须最小） |
| **测试** | `pytest tests/invariants -q` 全绿;全量回归 ≥1671 |
| **验证** | 每条 INV 有**编号化用例**且**失败时能指出违反的具体不变量** |
| **checkpoint** | `test: add numbered governance invariants coverage` |
| **退出条件** | D-1 / D-2 达成（INV-01~09 + INV-G1~G6 各有专项）;全量绿 |

---

### S6-3 — 激活 `tests/acceptance/`（验收测试）

| 项 | 内容 |
|---|---|
| **输入** | S6-2 完成 |
| **修改范围** | **新增** `tests/acceptance/test_governance_acceptance.py`:≥1 端到端用例,走**真实外壳**（`assemble_real_engine` / 真实 `SessionStore` + `EventBus` + 适配器链）;**禁用 mock 通道** |
| **禁止触碰** | `pyharness/**`;既有测试 |
| **测试** | 新用例绿;全量回归 |
| **验证** | 覆盖治理核心命题**至少两项**（建议：**决策可追**（`causal_chain` 还原真实调用链）+ **凭证可验**（`receipt.verify` 在重开日志后成立）） |
| **checkpoint** | `test: activate governance acceptance tests` |
| **退出条件** | D-3 达成（目录非空且用例走真实路径） |

---

### S6-4 — 新建 `tests/security/`（安全测试）

| 项 | 内容 |
|---|---|
| **输入** | S6-3 完成 |
| **修改范围** | **新建目录** `tests/security/`（含 `__init__.py`）+ `tests/security/test_governance_security.py`:≥1 用例,**必须含「批准 A 执行 B 必须被拒」**（现有 `_approval_binding` 绑定校验路径）;可选：critical 直拒零副作用 · scope-hidden 终局拒 |
| **禁止触碰** | `pyharness/**`;既有安全断言（**只增不改**） |
| **测试** | 新用例绿;全量回归 |
| **验证** | 每个安全用例在**无对应产品防护时必然失败**（反证有效性自检：临时禁用防护观察失败 → 恢复） |
| **checkpoint** | `test: add governance security tests` |
| **退出条件** | D-4 达成;"批准 A 执行 B 必拒"可用例证明 |

---

### S6-5 — 收敛、回归与 Freeze（收口）

| 项 | 内容 |
|---|---|
| **输入** | S6-2 / S6-3 / S6-4 全部完成 |
| **修改范围** | **新增** `S6_CHANGE_REPORT.md` + `S6_FINAL_REVIEW.md`;**不改** 测试与代码（除报告） |
| **禁止触碰** | `pyharness/**`;既有测试;ADR/schema |
| **测试** | **全量回归**（记录精确数字）+ `tests/invariants` / `tests/acceptance` / `tests/security` 三类汇总 |
| **验证** | ① D-1~D-7 逐条打勾;② **既有安全断言零放松**逐条复核（diff 级别）;③ 新暴露缺陷已按 P 级登记 `KEY-FINDINGS.md`（去重）;④ `77/14/3` 不变 |
| **checkpoint** | `docs: freeze S6 invariants and test surfaces` |
| **退出条件** | S6 = PASS 且满足 S7 Entry（`TraceabilityMatrix` + 端到端 Demo 的前置） |

---

## 7. 待人工确认项（进入 S6-1 前）

| # | 事项 | 建议 |
|---|---|---|
| **Q-1** | **INV 编号权威口径**（F-1）：以 `docs/CONSTRAINTS-06-Testing.md` 为准,还是以代码/测试现状为准？ | **建议以 CONSTRAINTS-06 为准**,并最小订正代码 docstring 的编号引用（作为 S6-1 的一部分或独立文档步骤） |
| **Q-2** | **INV-G3 专项**是否确认落在 `tests/invariants/test_inv_governance.py`？ | 建议是 |
| **Q-3** | `tests/invariants/test_inv_core.py` **新增文件**是否接受（vs 复用现有三文件）？ | 建议新增（编号化清晰、避免与 bus/config/events 领域混放） |
| **Q-4** | **L-3 的处置**：S6 **只验证**（不修复）是否确认？ | 建议确认（修复需改 `approval.py`,超 S6 范围） |
| **Q-5** | `tests/security/` **新建目录**是否接受（当前不存在）？ | 建议接受（M9 明文要求） |

---

**本文件为规划产物**：未修改任何 Python 代码 / 既有测试 / 事件 schema / Governance Core;未实现 S6 功能;未 push。

**等待人工对 S6 Entry Plan 的授权。**
