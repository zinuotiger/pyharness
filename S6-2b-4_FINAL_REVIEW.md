# S6-2b-4_FINAL_REVIEW.md — INV-05 覆盖最终审查

> **阶段**：S6-2b-4（INV-05 覆盖审计 + 缺失测试补充）Final Review
> **审查对象 checkpoint**：**`4f74f93`**（`test: establish INV-05 invariant coverage`）
> **日期**：2026-09-15 ｜ `main...origin/main [ahead 41]` ｜ worktree **clean**
> **性质**：**只读整理**。本文件由既有 checkpoint 与其阶段文档汇总而成，**未修改生产代码 / tests / Registry / Event Schema / Governance Core**；**未进入 INV-06**；**未 commit、未 push**（生成后等待提交授权）。
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-05，**已含 F-2 的来源分列**，commit `5cd66c9`）
> **测试基线**（复核）：`tests/invariants` = **48 passed** · 全量 **1704 collected / 0 failures / 0 errors / 2 skipped = 1702 passed / 2 skipped / 0 failed** · `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`

---

## 1. INV-05 最终状态

**INV-05 = 拒绝后零副作用**

> Canonical Definition（`5cd66c9` 后）：任意 reject（scope / guard / critical / 审批 `denied` / `timeout`）后，**Provider 调用计数 = 0** 且**无该 `call_id` 的 `tool.result`**。**拒绝事实须强同步可证，按拒绝来源分列**：**guard 链来源**（scope / guard / critical）⇒ 强同步落 **`guard.rejected`**（`sync=True`）；**审批来源**（审批 `denied` / `timeout`）⇒ 强同步落 **`approval.denied`** / **`approval.timeout`**（`sync=True`）。**不引入统一的 rejection event**。落盘失败即 fail-closed 上抛。

```
INV-05 = 拒绝后零副作用
  ├─ covered  : 8    C1 · C2 · C3(按来源分列) · C4 · C5 · G-1 · G-2 · G-3
  ├─ partial  : 0
  ├─ gap      : 0
  └─ deferred : 0
```

| # | 子性质 | 终态 | 决定性证据 |
|---|---|---|---|
| **C1** | 任意 reject 后 Provider 调用计数 = 0 | ✅ covered | **T-1**（5 类来源）· **T-3** · **T-4**；**M-C5 实证** |
| **C2** | 无该 `call_id` 的 `tool.result` | ✅ covered | **T-1** · **T-3** · **T-4** |
| **C3** | 拒绝事实强同步可证（**按来源分列**） | ✅ covered | **guard 侧**：T-1/T-2 + **M-C2 实证**；**审批侧**：T-4 + **M-C6 实证** |
| **C4** | 落盘失败 ⇒ fail-closed 上抛 | ✅ covered | **T-3**（executor **端到端**） |
| **C5** | 覆盖全部 5 类拒绝来源 | ✅ covered | **T-1** 单点矩阵（scope-hidden / guard / critical / approval-denied / approval-timeout）+ **T-4**（审批 2 类） |
| **G-1** | `tests/invariants/` 编号化专项 | ✅ **closed** | 4 个编号化函数 / 9 条执行用例 |
| **G-2** | 单一编号锚点 | ✅ **closed** | T-1 覆盖 guard 类、T-4 覆盖审批类 |
| **G-3** | 审批来源强同步可证 | ✅ covered（**Phase A 原判 partial → 经自查更正**） | `tests/unit/test_approval.py::test_deny_flow` / `::test_timeout_ttl_expiry`（**真实 `ApprovalProvider`**）+ **T-4 编号化锚点** |

**与 Registry `Coverage Gap` 的对应**：记载的 ①（无编号化专项）与 ②（无单一编号锚点）**本轮双双关闭**。

---

## 2. T-1 ~ T-4 覆盖证明

**落点**：`tests/invariants/test_inv_core.py` 的 INV-05 段（**+244 / −0 单 hunk 纯追加**，插入于 `@830`）

| # | 用例 | 参数化 | 关键断言 | 覆盖子性质 |
|---|---|---|---|---|
| **T-1** | `test_inv05_reject_sources_zero_side_effect_and_strong_sync` | **5 类** | ① `prov.calls == 0` ② `tool.result == []` ③ `r.ok is False` ④ `guard.evaluated` 恰一条且 decision ∈ {deny, need_approval} ⑤ **强同步锚点按来源分列**：guard 类 ⇒ `guard.rejected` 恰一条且 `sync is True`；审批类 ⇒ **无 `guard.rejected`** + `decision.issued` 恰一条且 `sync is True` | C1 · C2 · C5 · G-1 · G-2 |
| **T-2** | `test_inv05_guard_reject_strong_sync_contrast` | — | `guard.evaluated[0]["sync"] is False` **对照** `guard.rejected[0]["sync"] is True`；`guard_id`/`policy_ref` 齐备；`trace.call_id` 配对 | C3（guard 侧） |
| **T-3** | `test_inv05_reject_flush_failure_does_not_reach_provider` | — | `_FailSess("guard.rejected")` ⇒ `execute()` **上抛**；`prov.calls == 0`；无 `tool.result`；`guard.evaluated` **已先落** | C4（端到端） |
| **T-4** | `test_inv05_approval_reject_real_provider_strong_sync` | **2 类**（denied / timeout） | **真实 `ApprovalProvider`**（`bus=None` ⇒ 裁决直接消费）+ TTL 经 `config` 注入 40ms；断言 零副作用 · 无 `tool.result` · **无 `guard.rejected`** · `approval.*` 恰一条且 `sync is True` · `approval.requested` 亦 `sync is True` | C3（审批侧） · G-3 |

**独立断言计数复核**（对段内文本计次）：`prov.calls == 0` **×4** · `tool.result` 否定 **×3** · `sync is True` **×5** · `is False` **×1**（对照面）· `not guard.rejected` **×2**（审批来源）· `pytest.raises` **×1**（T-3）⇒ **断言非空转、非模板化**。

**未重复建设**（只建映射）：C1/C2 的既有断言（`test_guard_reject_critical_zero_side_effects` · `test_scope_hidden_terminal_reject` · `test_verdict_not_granted_no_execute` · `test_executor_critical_delete_denied_zero_side_effect`）· C4 的 guard 侧（`test_append_failure_fail_closed`）· C5 的额外来源（APR-501 / GRD-403 / 绑定不符）· **审批侧强同步**（`test_approval.py::test_deny_flow` / `::test_timeout_ttl_expiry`）。

---

## 3. Mutation M-C2 / M-C5 / M-C6 结果

> 备份 `tmp/*05.bak`（gitignore 内）；**全部已执行 → 验证 RED → 立即还原 → 核验无残留**

| # | 注入内容 | 目标测试 | 结果 | 恢复 |
|---|---|---|---|---|
| **M-C2** | `tools_guard._append_rejected` 的 `sync=True` → **`sync=False`** | **T-1[scope-hidden / guard / critical]** + **T-2** | ✅ **4 RED**（`assert False is True` ⇒ 直接命中 C3）；**T-1 的 2 个审批来源仍 GREEN**（其锚点是 `decision.issued`）· **T-3 / T-4 不受影响** | ✅ |
| **M-C5** | executor 的 `d == "reject"` 分支**不再 `return`**（继续下落至关 3） | **T-1[scope-hidden / guard / critical]** + **T-2** | ✅ **4 RED**，断言显示 **`prov.calls == 1`** ⇒ 直接命中 **C1（Provider 零调用）**；审批来源不受影响 | ✅ |
| **M-C6** | `approval._emit_verdict` 的 `sync=True` → **`sync=False`** | **T-4[denied] + T-4[timeout]** | ✅ **双 RED**（审批拒绝事实未强同步） | ✅ |

**残留核验（独立复验）**：

| 检查 | 结果 |
|---|---|
| `grep -c "MUTATION"` 于 `tools_guard.py` / `tools_executor.py` / `approval.py` | **0 / 0 / 0** ✅ |
| `git status --porcelain pyharness` | **空** ✅ |
| 临时备份 `tmp/*05.bak` 与 `pyharness/core/_mut_*.py` | **均不存在** ✅ |
| 语法 / 复跑 | `ast.parse` 通过 · `-k inv05` = **9 passed** · `tests/invariants` = **48 passed** ✅ |

**三项 mutation 各有明确专属目标**，且副作用面清晰（M-C2 只影响 guard 侧 sync；M-C5 只影响 guard 拒绝短路；M-C6 只影响审批裁决落盘）。

---

## 4. 全量回归结果

| 项 | 结果 |
|---|---|
| `tests/invariants/` | ✅ **48 passed** |
| **全量** | ✅ **1704 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1702 passed / 2 skipped / 0 failed** |
| 对比本阶段基线（`4a41068`：1695 / 1693） | **+9 = 新增 INV-05 用例** ⇒ **零既有回归**（0 failed 不变） |
| **schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |
| 本阶段包含 Step 0 的 Registry 改动 | ✅ 已记录（`5cd66c9`，+2/−1），**未影响任何测试**（定义文本改动） |

**S6-2b-4 的 commit 序列**：

```
5cd66c9  docs: update INV-05 canonical definition for rejection sources   (+2 / −1)   ← Step 0
4f74f93  test: establish INV-05 invariant coverage                        (+670 / −0) ← HEAD
```

---

## 5. Registry 一致性确认

**逐条对照**（Registry 定义 ↔ 测试断言）：

| Registry 条款（`5cd66c9` 后） | 对应测试 |
|---|---|
| 任意 reject 后 **Provider 调用计数 = 0** | T-1（5 类）· T-3 · T-4 |
| **无该 `call_id` 的 `tool.result`** | T-1 · T-3 · T-4 |
| **guard 链来源** ⇒ `guard.rejected`（`sync=True`） | T-1[guard 类 ×3] · T-2 |
| **审批来源** ⇒ `approval.denied` / `approval.timeout`（`sync=True`） | T-4 |
| **不引入统一的 rejection event** | T-1[审批类] 与 T-4 均断言**无** `guard.rejected` |
| **落盘失败即 fail-closed 上抛** | T-3 |

⇒ ✅ **逐条对应，无缺口、无超范围断言**。

**Registry 的 F-2 变更已按 §6 规则留痕**：`docs/INVARIANT_REGISTRY.md` §6.1 变更记录含 2026-09-15 一行（变更内容 / 依据 = `S6-2b-4_COVERAGE_AUDIT.md` §2.4 F-2 / 状态 `established`），且明确"其余字段未改"。

**Registry 在本阶段之后未再改动**（Phase F 与 R-1 修正均未触碰 Registry）。

---

## 6. 冻结面确认

**相对 `5cd66c9`（Step 0）与 `4f74f93`（HEAD）** —— 逐项 **0 改动**：

| 面 | 改动数 |
|---|---:|
| `pyharness/`（生产代码） | **0** |
| `pyharness/governance`（Governance Core） | **0** |
| `pyharness/events` + `docs/EVENT-SCHEMA.md`（Event Schema） | **0** |
| `pyharness/core/approval.py`（L-3） | **0** |
| `docs/INVARIANT_REGISTRY.md`（Phase F 与 R-1 期间） | **0** |

**其中 `docs/INVARIANT_REGISTRY.md` 在 Step 0（`5cd66c9`）确有改动**，但**严格限定为 INV-05 的 `Canonical Definition` 一处 + §6.1 变更记录一行**（`+2 / −1`）；其它 INV 条目 **0 改动**（已逐行核验）。

**运行时冻结值**：`EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3`（全程不变）。

**本阶段未处理**：KF-B · FC-8 · INV-06。

---

## 7. Residual Limitations

### 🟡 R-2（本阶段遗留，**中**）— Registry 的 INV-05 `Existing Test Evidence` 未同步

| 项 | 内容 |
|---|---|
| **现象** | Registry 的 INV-05 `Existing Test Evidence` 字段**仍只列 guard 侧 6 条**（`test_reject_event_pair_order_and_sync` 等），**未含**：① 审批侧真实路径证据（`test_approval.py::test_deny_flow` / `::test_timeout_ttl_expiry`）；② 本轮新增的 4 个编号化用例 |
| **原因** | Step 0 经人工**限定为"仅修改 INV-05 的 `Canonical Definition`"** ⇒ 该字段按令未动 |
| **影响** | **不致误判**（定义与测试本身一致），但**该字段的覆盖读数不完整** —— 仅读 Registry 会低估 INV-05 的现有证据面 |
| **建议** | 另行授权补登该字段（**改动面同样限定一处**，并按 §6.1 追加变更记录） |

### 🟠 KF-B（P1，`OPEN`）

系统工具类 LLM 出口（`summarize` / `json_chat` / `mini`）**不经**轮数 / 取消 / 预算三闸；且 `summarize(prompt, budget=400)` 的 `budget` **从未被执行**。**独立于 INV-02/05**，未新建 INV 编号，**本阶段未处理**。

> **边界提醒**：INV-05（"拒绝后 Provider 零调用"）与 KF-B（"系统工具出口不受三闸"）**相邻但不同**，本阶段**未混同**。

### 🟢 FC-8（P3，`pending authorization`）

`tests/unit/test_tools_guard.py` 的 **5 条误标 INV-03**（实为 INV-04）未修正 ⇒ 使"按编号读出的覆盖读数"失真。**本阶段依令未处理**。

### 🟢 其它历史遗留（P3）

| # | 项 | 状态 |
|---|---|---|
| **R-1** | `S6-2b-4_COVERAGE_AUDIT.md` 的 G-3 错误判定 | ✅ **已修正**（文首 `Revision / Correction` + 5 处内联标记；Phase A 原判**逐字保留**） |
| **O-1** | `tools_executor._reject` 死代码（deprecated rejection path） | **Technical Debt 已登记**（`S6-2b-4_CHANGE_REPORT.md` §9）· **暂不删除** |
| **P3-2** | `test_inv02_loop_is_the_only_dynamic_entry_dispatcher` 硬编码 `agent_loop.py:241` 行号 | 前序阶段遗留（S6-2a-P0-F 审查 R-1），**未处理** |
| **P3-8** | **FC-1~FC-14**（19 处 / 13 文件）编号迁移 | `pending authorization`（其中 FC-8 / FC-13 同上） |
| **P3-9** | `tests/invariants/test_inv_{bus,config,events}.py` 头部"当前 RED：模块未实现"**失实**（= FC-11） | 登记 |
| — | **无 P0 / P1 级残余** | KF-B 为 P1 但**已明确划归独立议题** |

---

## 8. 最终判定

> # ✅ **S6-2b-4 = PASS**

| 标签 | 值 |
|---|---|
| **INV-05** | **`covered` × 8 · `partial` 0 · `gap` 0 · `deferred` 0** |
| **C3 / G-1 / G-2 / G-3** | 全部达成（G-3 经**自查更正**为 `covered`） |
| **生产代码 / Event Schema / Governance Core / L-3** | 均 **零改动** |
| **Registry** | 仅 Step 0 的 **INV-05 定义一处**（已按 §6 留痕）；其余 **0** |
| **KF-B** | **`OPEN`**（未处理） |
| **INV-06** | **未进入** |

**判定依据**：① T-1~T-4 **逐条映射**子性质且**各有 M-\* 实证**；② C1~C5 + G-1~G-3 **全部达成**；③ Registry 定义与测试**逐条一致**；④ 全量 **1702 / 2 / 0**（**+9 零回归**）；⑤ 冻结面**逐项 0 改动**（Registry 例外已限定并留痕）；⑥ 残余限制**逐条登记**（R-2 为中、其余低）。

---

## 9. 边界声明

**本文件性质**：**只读整理**。
**未做**：❌ 修改生产代码 · ❌ 修改 tests · ❌ 修改 Registry · ❌ 修改 Event Schema / Governance Core · ❌ 进入 INV-06 · ❌ commit · ❌ push。

**worktree**：`?? S6-2b-4_FINAL_REVIEW.md`（仅本文件）；**HEAD 仍 `4f74f93`**，ahead 41。

---

**S6-2b-4 Final Review 结束。判定 = PASS。等待提交授权。**
