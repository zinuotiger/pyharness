# S6-2b-4_CHANGE_REPORT.md — INV-05 缺失测试补充（Phase F）

> **阶段**：S6-2b-4 Phase F（INV-05 Coverage Implementation）
> **日期**：2026-09-15 ｜ **基线 commit**：`5cd66c9`（Step 0：INV-05 定义按 F-2 调整）｜ HEAD `5cd66c9` ｜ ahead 40
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-05，**已含 F-2 的来源分列**）
> **改动面**：**仅 1 个测试文件**（`tests/invariants/test_inv_core.py`，**+244 / −0 纯追加**）· **生产代码零改动**
> **定向**：`tests/invariants` = **48 passed** ｜ `-k inv05` = **9 passed**
> **全量**：**1704 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1702 passed / 2 skipped / 0 failed**
> **对比基线**：1695 collected / 1693 passed ⇒ **+9 = 新增用例，零既有回归**
> **schema**：`EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`（**不变**）

---

## 1. Phase A 审计结果摘要（含一处**自查更正**）

> 详见 `S6-2b-4_COVERAGE_AUDIT.md`

- **INV-05 拆 5 条子性质（C1~C5）+ 3 条结构性（G-1~G-3）**
- Phase A 初判：`covered` 4（C1 · C2 · C4 · C5）· `partial` 2（C3 · G-3）· `gap` 2（G-1 · G-2）

### ⚠️ 1.1 自查更正：**G-3 的初判错误**

Phase A 把 **G-3（审批来源强同步可证）** 判为 `partial` + "**高假绿风险**"。

**实测推翻**：`tests/unit/test_approval.py::test_deny_flow`（:194）与 `::test_timeout_ttl_expiry`（:213）**已用真实 `ApprovalProvider` + 真实 `SessionLog` + 真实 `EventBus`** 走 deny/timeout，并各自断言 `await _wait_flushed(store, seq)`；而 `FakeStore.flush(seq)` **仅**由 `SessionLog._flush` 在 `sync or type in SYNC_TYPES` 时调用 ⇒ **该断言即"强同步已落盘"的证明**。真实 Provider 若停发或改弱同步，这两个用例会 **RED**。

**⇒ G-3 更正为 `covered`（approval 模块层）**，Phase F 的目标随之**收窄**为"补 **INV-05 编号化锚点**"，而非重复建一套真实 Provider 强同步测试。

---

## 2. T-1 ~ T-4 实施内容

全部落在 `tests/invariants/test_inv_core.py` 新增的 **INV-05 段**。

**新增本地夹具**（只用生产公开 API）：

| 辅助 | 作用 |
|---|---|
| `_FailSess(_Sess)` | `append` 对**指定事件类型**抛错（模拟强同步落盘失败，C4 端到端用） |
| `_FakeApproval` | 审批裁决替身（逐次脚本）；**不发射** `approval.*`（真实 Provider 强同步由 T-4 验证） |
| `_wait(pred)` | 轮询等待（审批请求已落 / 裁决已产生） |
| `_inv05_scope(root, allowed)` | scope 替身；`allowed=[]` ⇒ `can_use` 恒 False（scope-hidden 场景） |
| `_inv05_registry(prov, name, danger)` | 按场景装配 `ToolRegistry` |
| `_inv05_reject(case, tmp_path)` | **按 5 类场景**装备并执行一次必然被拒的调用 |

| # | 用例 | 实施要点 |
|---|---|---|
| **T-1** | `test_inv05_reject_sources_zero_side_effect_and_strong_sync`（**参数化 5 类**） | 每类断言：① `prov.calls == 0` ② `tool.result == []` ③ `r.ok is False` ④ `guard.evaluated` 恰一条且 decision ∈ {deny, need_approval} ⑤ **强同步锚点按来源分列**：guard 类 ⇒ `guard.rejected` 恰一条且 `sync is True`；审批类 ⇒ **无 `guard.rejected`** + `decision.issued` 恰一条且 `sync is True` |
| **T-2** | `test_inv05_guard_reject_strong_sync_contrast` | `guard.evaluated[0]["sync"] is False` **对照** `guard.rejected[0]["sync"] is True`；并验 `guard_id`/`policy_ref` 齐备 + `trace.call_id` 配对 |
| **T-3** | `test_inv05_reject_flush_failure_does_not_reach_provider` | `_FailSess("guard.rejected")` ⇒ `execute()` **上抛**；断言 `prov.calls == 0` + 无 `tool.result` + `guard.evaluated` **已先落** |
| **T-4** | `test_inv05_approval_reject_real_provider_strong_sync`（**参数化 denied / timeout**） | **真实 `ApprovalProvider`**（`bus=None` ⇒ 裁决直接消费）+ TTL 经 `config` 注入（timeout 路径 40ms）；断言 ① 零副作用 ② 无 `tool.result` ③ **无 `guard.rejected`** ④ **`approval.denied`/`approval.timeout` 恰一条且 `sync is True`** ⑤ `approval.requested` 亦 `sync is True` |

**T-4 的非重复声明**（写入 docstring）：既有 `test_approval.py::test_deny_flow` / `::test_timeout_ttl_expiry` 已在 **approval 模块层**用真实 Provider 证明强同步；本用例**只补 INV-05 的编号化锚点**。

---

## 3. 每个测试对应的 Canonical INV-05 子性质

| 用例 | C1 零调用 | C2 无 tool.result | C3 强同步（**按来源分列**） | C4 fail-closed | C5 全来源 | G-1 | G-2 |
|---|:---:|:---:|---|---:|:---:|:---:|:---:|
| **T-1** | ✅ | ✅ | **guard 类**：`guard.rejected.sync`；**审批类**：无 `guard.rejected` + `decision.issued.sync` | — | ✅（5 类参数化） | ✅ | ✅ |
| **T-2** | ✅ | — | ✅ **guard 侧 + 对照面** | — | — | ✅ | ✅ |
| **T-3** | ✅ | ✅ | — | ✅（**executor 端到端**） | — | ✅ | ✅ |
| **T-4** | ✅ | ✅ | ✅ **审批侧**（`approval.*.sync`） | — | ✅（审批 2 类） | ✅ | ✅ |

---

## 4. 已有测试为何没有重复建设

本轮**只新增 4 个函数（9 条执行用例）**：

| 既有已充分覆盖 | 既有用例（**未重复**） |
|---|---|
| C1/C2 · critical 拒零副作用 | `test_tools_executor.py::test_guard_reject_critical_zero_side_effects` |
| C1/C2 · scope-hidden | `::test_scope_hidden_terminal_reject` |
| C1/C2 · 审批 denied/timeout（**替身**） | `::test_verdict_not_granted_no_execute`（参数化） |
| C1/C2 · 真实文件未被删 | `test_tool_fs.py::test_executor_critical_delete_denied_zero_side_effect` |
| C3 · guard 侧强同步 | `test_tools_guard.py::test_reject_event_pair_order_and_sync` |
| C4 · guard 侧 fail-closed | `::test_append_failure_fail_closed` |
| C5 · 额外来源（APR-501 / GRD-403 / 绑定不符） | `::test_headless_apr501_no_execute` · `::test_granted_then_policy_tightened_grd403` · `::test_granted_binding_mismatch_denied` |
| **审批侧强同步（真实 Provider）** | **`test_approval.py::test_deny_flow` / `::test_timeout_ttl_expiry`**（G-3 更正后确认） |

**本轮补的恰好是 G-1/G-2**（编号化 + 单一锚点），并**把 C5 的 5 类来源收成单点矩阵**；**未修改任何既有测试**（`+244 / −0` 纯追加）。

---

## 5. Mutation 结果（**3 项已执行 → 已验证 RED → 已全部恢复**）

| # | 注入内容 | 目标测试 | 结果 | 恢复 |
|---|---|---|---|---|
| **M-C2** | `tools_guard._append_rejected` 的 `sync=True` → **`sync=False`** | **T-1[scope-hidden/guard/critical]** + **T-2** | ✅ **4 RED**；`assert False is True` ⇒ 断言直接命中；**T-1 的 2 个审批来源仍 GREEN**（其锚点是 `decision.issued`）；**T-3/T-4 不受影响** | ✅ |
| **M-C5** | executor 的 `d == "reject"` 分支**不再 `return`**（继续下落至关 3） | **T-1[scope-hidden/guard/critical]** + **T-2** | ✅ **4 RED**，断言显示 **`prov.calls == 1`** ⇒ 直接命中 **C1（Provider 零调用）**；审批来源不受影响（其路径不产生 `guard` 拒绝） | ✅ |
| **M-C6** | `approval._emit_verdict` 的 `sync=True` → **`sync=False`** | **T-4[denied] + T-4[timeout]** | ✅ **双 RED**（`assert False is True` ⇒ 审批拒绝事实未强同步） | ✅ |

**恢复与残留核验（独立复验）**：

| 检查 | 结果 |
|---|---|
| `grep -c MUTATION` 于 `tools_guard.py` / `tools_executor.py` / `approval.py` | **0 / 0 / 0** ✅ |
| `git status --porcelain pyharness` | **空** ✅ |
| 临时备份 `tmp/*05.bak` | **已删除**（`ls tmp/*.bak` → No such file）✅ |
| 语法 | `ast.parse` 通过 ✅ |
| 复跑 | `-k inv05` = **9 passed** · `tests/invariants` = **48 passed** ✅ |

**三项 mutation 各有明确的专属目标测试**，且**副作用面清晰**（M-C2 只影响 guard 侧 sync；M-C5 只影响 guard 拒绝的短路；M-C6 只影响审批裁决落盘）。

---

## 6. 全量回归与核验

| 项 | 结果 |
|---|---|
| `-k inv05` 定向 | ✅ **9 passed** |
| `tests/invariants/` | ✅ **48 passed** |
| **全量** | ✅ **1704 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1702 passed / 2 skipped / 0 failed**（50.6s，exit 0） |
| 对比基线 | 1695 / 1693 ⇒ **+9 = 新增用例**，**0 failed 不变** ⇒ **零既有回归** |
| **schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |
| **生产代码** | `git diff --name-only HEAD -- pyharness` = **0** ✅ |
| **Registry / Event Schema / Governance Core** | **0 改动** ✅ |
| 现有测试是否被改 | ❌ 未改（**`+244 / −0` 纯追加**）✅ |
| `git diff --check` | ✅ 通过 |

---

## 7. Coverage 状态变化

| # | 子性质 | Phase A | Phase F 后 | 判定依据 |
|---|---|---|---|---|
| **C1** | Provider 零调用 | covered | ✅ **covered** | T-1（5 类）+ **M-C5 实证** |
| **C2** | 无 `tool.result` | covered | ✅ **covered** | T-1 / T-3 / T-4 |
| **C3** | 拒绝事实强同步可证（**按来源分列**） | partial | ✅ **covered** | **guard 侧**：T-1/T-2 + **M-C2 实证**；**审批侧**：T-4 + **M-C6 实证** |
| **C4** | 落盘失败 fail-closed | covered | ✅ **covered** | T-3（**executor 端到端**） |
| **C5** | 覆盖全部拒绝来源 | covered | ✅ **covered** | T-1 单点矩阵（5 类）+ T-4（审批 2 类） |
| **G-1** | `tests/invariants/` 编号化专项 | gap | ✅ **closed** | 4 个编号化函数 / 9 条用例 |
| **G-2** | 单一编号锚点 | gap | ✅ **closed** | T-1/T-4 分别覆盖 guard 类与审批类 |
| **G-3** | 审批来源强同步可证 | **partial → 更正 covered** | ✅ **covered** | `test_approval.py::test_deny_flow` / `::test_timeout_ttl_expiry`（真实 Provider）+ **T-4 编号化锚点** |

**INV-05 最终状态**：

```
INV-05 = 拒绝后零副作用
  ├─ covered  : 8   C1 · C2 · C3(按来源分列) · C4 · C5 · G-1 · G-2 · G-3
  ├─ partial  : 0
  ├─ gap      : 0
  └─ deferred : 0
```

**⚠️ 与 Registry 的对应**：
- Registry 记载的 INV-05 `Coverage Gap` ①（无编号化专项）⇒ ✅ **本轮关闭**
- ②（无单一编号锚点，分散 guard/executor/tool_fs/cli 四处）⇒ ✅ **本轮关闭**
- **Registry 文件本轮未再改动**（F-2 调整已在 **Step 0 / `5cd66c9`** 完成）

---

## 8. 未处理的 Deferred Items

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| **D-1** | **O-1**：`tools_executor._reject` 死代码 | **登记 TD，不删除** | 见 §9 |
| **D-2** | INV-05 的治理层相邻面（`INV-G5` 治理层无执行 API） | **未处理** | 不在 INV-05 的 Registry 证据范围；`test_inv_governance.py` 已有对应用例 |
| **D-3** | **FC-8**（5 条误标 INV-03 未修） | **未处理**（依令） | `pending authorization` |

---

## 9. Technical Debt 记录（O-1 · 用户指定落点 = 本报告）

> 依裁定：**判定为结构债，暂不删除**。

| 项 | 内容 |
|---|---|
| **名称** | **Deprecated rejection path** |
| **位置** | `pyharness/core/tools_executor.py:633-649` 的 `ToolExecutor._reject()` |
| **现状** | **全库零调用点**（`grep "self._reject("` → 无命中）⇒ **死代码** |
| **它实现过什么** | "executor 侧终局拒"：`guard.evaluated`(deny) + `guard.rejected`(**sync=True**) + 登记 `_rejected`（GRD-401 单调语义） |
| **当前唯一拒绝入口** | ① **guard 链拒绝**：`GuardChain._evaluate_full` → `_append_rejected`（`tools_guard.py:830`，**唯一 `guard.rejected` 发射点**）；② **executor 侧终局拒**：`execute()` 的 `d == "reject"` 分支（`tools_executor.py:480-481`，**直接 `return`，不再走 `_reject`**） |
| **为何是债而非缺陷** | 无功能影响（无调用点 ⇒ 不参与任何运行路径）；但**保留双份"拒绝"语义**易在后续维护中被误用或误改，且 `_reject` 内的 `sync=True` 与 guard 链的 `_append_rejected` 构成**潜在第二真源** |
| **后续清理建议** | ① **确认无外部引用**（含插件/脚本）后**整体删除**该方法；② 若因 spec 契约需保留（`docs/specs/tools_executor.py.md` 若列有 `_reject`），则**先改 spec 或登记偏离**再删（沿用 `_invoke` 死亡代码的处置先例）；③ 清理时**补一条断言**：`guard.rejected` 的发射点唯一（静态：`"guard.rejected"` 字面量仅出现于 `tools_guard._append_rejected`） |
| **建议归属** | 独立 cleanup 小步（**不在 S6-2b-4 内实施**） |

---

## 10. 是否发现真实生产缺陷

| 项 | 结论 |
|---|---|
| **本轮测试是否揭示真实生产缺陷** | ❌ **否** —— M-C2/M-C5/M-C6 均为**人为注入**的变异（验证测试鉴别力），恢复后全部 GREEN |
| **F-2（范围不一致）** | ✅ 已由 **Step 0（`5cd66c9`）** 在 Registry 中按裁定落地（来源分列） |
| **O-1（死代码）** | ⚠️ **结构债**，非缺陷；已按裁定登记（§9） |
| **结论** | **无新增 KEY-FINDING**；**生产代码零改动** |

---

## 11. 状态与边界

| 项 | 结果 |
|---|---|
| 改动面 | **仅 `tests/invariants/test_inv_core.py`（+244 / −0 纯追加）** ✅ |
| 新增测试 | **4 个函数 / 9 条执行用例**，全部标注 **INV-05** |
| 状态标注 | `IMPLEMENTED` + `TESTED` + `VERIFIED`（定向 + 全量 + M-C2/M-C5/M-C6 均有实证） |
| 未做 | ❌ 改 `pyharness/` · ❌ 改 Registry（本轮）· ❌ 改 Event Schema / Governance Core · ❌ 处理 KF-B / FC-8 · ❌ 进入 INV-06 · ❌ commit · ❌ push |

**worktree**：`M tests/invariants/test_inv_core.py` + `?? S6-2b-4_COVERAGE_AUDIT.md`（+ 本报告）；**HEAD 仍 `5cd66c9`**。

---

**S6-2b-4 Phase F 结束。未进入 INV-06；等待人工授权。**
