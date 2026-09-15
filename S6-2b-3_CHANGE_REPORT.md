# S6-2b-3_CHANGE_REPORT.md — INV-04 缺失测试补充（Phase F）

> **阶段**：S6-2b-3 Phase F（INV-04 Coverage Implementation）
> **日期**：2026-09-15 ｜ **基线 commit**：`c44c9ce`（S6-2b-2 Freeze）｜ HEAD `c44c9ce` ｜ ahead 36
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-04）+ **F-1 裁定**（2026-09-15，见 §10）
> **改动面**：**仅 1 个测试文件**（`tests/invariants/test_inv_core.py`，**+349 / −7**；7 行删除**全为旧模块 docstring**，**零测试逻辑改动**）· **生产代码零改动**
> **定向**：`tests/invariants` = **39 passed** ｜ **全量**：**1695 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1693 passed / 2 skipped / 0 failed**
> **对比基线**：1685 collected / 1683 passed ⇒ **+10 = 新增用例，零既有回归**
> **schema**：`EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`（**不变**）

---

## 1. Phase A 审计结果摘要（回顾）

> 详见 `S6-2b-3_COVERAGE_AUDIT.md`（**未提交**）

- **INV-04 拆 11 条子性质** ⇒ `covered` **8** · `partial` **2**（A4 · A5）· `gap` **0** · `deferred` **1**
- **真实缺口**：**G-1**（`call_id` 配对一致性无逐字段断言）· **G-2**（"挂载恒链尾"顺序语义未断言）· **G-3**（`_require_wiring` 的 `session`/`scope` 两分支无用例）· **G-4**（A5 无静态扫描用例 = Registry gap ③）· **G-5**（`tests/invariants/` 内 0 条）
- **F-1（范围不一致）**：关1a/关1b 的 `tool.error` **无前置 `guard.evaluated`** ⇒ INV-04(a) 的**字面范围**宽于**实现域/哨兵域**（治理哨兵 `NO-GUARD-EVENT` 只查 `tool.result`）

---

## 2. T-1 ~ T-5 实施内容

全部落在 `tests/invariants/test_inv_core.py` 新增的 **INV-04 段**（含 6 个本地夹具 + 2 个扫描辅助）。

**本地夹具**（**只用生产公开 API**，不 import 其它测试模块）：

| 辅助 | 作用 |
|---|---|
| `_Sess` | 最小异步事件落点（`append` → 记录 `type/payload/actor/seq/sync/trace`） |
| `_Prov` | 记录型 Provider（计数 + 可选抛错 ⇒ 制造"执行后失败"） |
| `_inv04_registry` / `_inv04_scope` / `_inv04_gov` / `_inv04_ctx` / `_inv04_call` | 装配 `ToolRegistry` / scope 替身 / **真实 `GovernanceContext`**（`PolicyEngine`+`DecisionEngine`）/ 可逐件置 `None` 的 ctx / `ToolCall` |
| `_inv04_attr_calls(root, attr)` | 通用 AST 属性调用点扫描（**`SyntaxError` 响亮失败**，不静默跳过） |
| `_inv04_gate(attr, allow, what)` | 通用闸门断言：**双向校验**（未登记 + 过期） |

| # | 用例 | 实施要点 |
|---|---|---|
| **T-1** | `test_inv04_require_wiring_fail_closed_all_parts`（**4 param**） | **参数化四件**（`session`/`scope`/`guard`/`governance`）逐项置 `None` ⇒ 各自抛 **`CYC-999`** + **Provider 零调用** + **无 `tool.result`** |
| **T-2a** | `test_inv04_provider_acquisition_surface_is_executor_only` | 闸①：`lookup_provider()` 调用点 ⊆ `{tools_executor.py}`，且**归属 `_provider_handle`**（正向控制防"删调用即变绿"） |
| **T-2b** | `test_inv04_provider_handle_call_surface_is_allowlisted` | 闸②：`.handle(` 调用点 ⊆ 白名单（`tool_skill.py` = `_SkillHandle` **内层委派**，理由逐条写明） |
| **T-3a** | `test_inv04_guard_evaluated_and_tool_result_share_call_id` | **逐字段**：`guard.evaluated.trace["call_id"] == tool.result.payload["call_id"]` |
| **T-3b** | `test_inv04_reject_path_call_id_pairing` | 拒绝路径：`guard.evaluated` / `guard.rejected` / `tool.call` **三处 `call_id` 一致** + 无 `tool.result` + Provider 零调用 |
| **T-4** | `test_inv04_plugin_guard_appends_to_tail_only` | 挂载后 `chain[-1].id == new`；**既有 id 序列前缀恒等**；`chain_version()` **单调 +1** |
| **T-5** | `test_inv04_tool_error_two_classes` | **①执行前失败**（`TLB-802` / `TLB-803`）⇒ 事件序**恰** `['tool.error']`（无 `guard.evaluated`）+ **Provider 零调用**；**②执行后失败**（Provider 抛错）⇒ **有** `guard.evaluated` 且**先于** `tool.error` + 两者 `call_id` 一致 + Provider 恰 1 次 |

### 2.1 实施中发生的一处设计修正（如实记录）

初版 T-2 只扫描 `.handle(`，**首跑即 RED**（假阴性）：`tools_executor` 的 Provider 调用是**裸可调用**（`_provider_handle()` 取回可调用面后 `handle(args, ctx)`），**不是** `.handle(` 属性访问。
⇒ 改为**双闸**：**闸①`lookup_provider`（Provider 的获取入口，全库仅 `tools_executor._provider_handle` 一处）** + **闸② `.handle(`（对象直调面）**。
**该修正提升了测试强度**：既覆盖"从 registry 取 Provider"（唯一合法途径），也覆盖"直接调 Provider 对象"。

---

## 3. 每个测试对应的 Canonical INV-04 子性质

| 用例 | 对应子性质 | 原状态 |
|---|---|---|
| **T-1** | **A4** 缺件 ⇒ fail-closed（4 分支全覆盖） | partial → **covered** |
| **T-2a** | **A5** 凡执行必经 `tools.execute`（**获取闸**） | partial → **covered** |
| **T-2b** | **A5**（**调用闸**） | 同上 |
| **T-3a** | **A1 · A3**（`tool.result` 前有 evaluated + 经 `call_id` 关联 · **逐字段**） | covered（**加严**：原仅事件序） |
| **T-3b** | **A3**（拒绝路径配对） | covered（**加严**） |
| **T-4** | **B5**（无移除/重排；**挂载恒链尾**的顺序面） | covered（**加严**：原仅"不可翻回"） |
| **T-5** | **A1 · A4** + **F-1 裁定新子性质**（`tool.error` **两类**区分） | **新增**（原为范围不一致，无用例） |

---

## 4. 已有测试为何没有重复建设

本轮**只新增 7 个函数（10 条执行用例）**，与既有测试**无重叠**：

| 既有已充分覆盖 | 既有用例（**未重复**） |
|---|---|
| 决策恰三值、无 bypass 第四值（B1） | `test_tools_guard.py::test_decision_tri_value_exact_no_bypass`（**误标 INV-03**） |
| `_FORBIDDEN_API` 逐名假（B2） | `::test_forbidden_bypass_api_attribute_error`（**误标 INV-03**） |
| 一次 reject 不可翻回（B3） | `::test_register_after_reject_cannot_rescue` · `::test_reject_deterministic_not_flippable`（**误标 INV-03**） |
| waterfall 短路（B4） | `::test_forbidden_bypass_api_attribute_error` 内 `c.checked == 0` |
| allow 路径恰一条 evaluated（A2） | `::test_evaluate_allow_single_evaluated_event` · `::test_monotonic_api_surface_no_allow`（**已标 INV-04**） |
| `disable` 须 `config_ref`；强制 guard 恒在（B6） | `::test_disable_forced_guards_cfg601` · `::test_builtin_chain_fixed_order_and_forced_guards` |
| 事件序 `tool.call→guard.evaluated→decision.issued→tool.result`（A1） | `test_tools_executor.py:289`（成功路径）· `:950`（拒绝路径） |
| 缺 `guard` / 缺 `governance` ⇒ `CYC-999`（A4 的 2 分支） | `test_tools_executor.py:407-415` · `:960-969` |

**本轮补的 5 项恰好是审计确认的缺口**：A4 的另 2 分支 · A5 的两道闸 · `call_id` 逐字段配对 · 挂载顺序面 · `tool.error` 两类区分。**未修改任何既有测试的语义**（7 行删除全为旧模块 docstring）。

---

## 5. Mutation 结果（**5 项已执行 → 已验证 RED → 已全部恢复**）

> 备份 `tools_guard.py` / `tools_executor.py` → `tmp/*.bak`（`tmp/` 已 gitignore）

| # | 注入内容 | 目标测试 | 结果 | 恢复 |
|---|---|---|---|---|
| **M-A3** | `_audit` 的 `trace={"call_id":…}` → `trace=None`（抹掉配对键） | **T-3a + T-3b** | ✅ **双双 RED**（配对断裂 ⇒ `ids == {"c-rej"}` 失败 / trace 取不到 call_id） | ✅ |
| **M-A4** | 删除 `_require_wiring` 的 **`session` 分支** | **T-1[session]** | ✅ **RED**，且**失败模式可区分**：由 `CYC-999` 变为 `AttributeError: 'NoneType' has no attribute 'append'`（未 fail-closed） | ✅ |
| **M-A5** | 新建临时文件 `pyharness/core/_mut_bypass_inv04.py` 含 `registry.lookup_provider(name).handle(args, ctx)` | **T-2a + T-2b** | ✅ **两道闸均 RED**，精确报出 `core/_mut_bypass_inv04.py:[5]` | ✅（文件已删） |
| **M-B3** | `register_plugin_guard` 的 `chain.append(guard)` → `chain.insert(0, guard)`（插队） | **T-4** | ✅ **T-4 RED**，且 **`tests/unit/test_tools_guard.py` 全绿** ⇒ **证明 T-4 填补真实缺口**（插队对既有 guard 测试不可见） | ✅ |
| **M-A6** | 令**关1b**（参数校验失败）也先发一条 `guard.evaluated` | **T-5** | ✅ **RED**（①b 类断言"执行前失败**不要求** evaluated"被破） | ✅ |
| **M-A7** | 把 `guard.evaluated` 发射点从 `_evaluate_full` 后移到 `_finalize` | T-5② | ⛔ **未执行**（人工裁定：跨函数生命周期移动，**收益低于风险**） | — |

**恢复与残留核验（独立复验）**：

| 检查 | 结果 |
|---|---|
| `grep -c MUTATION pyharness/core/tools_guard.py pyharness/core/tools_executor.py` | **0 / 0** ✅ |
| `git status --porcelain pyharness` | **空** ✅ |
| 临时文件（`_mut_bypass_inv04.py` · `tmp/*.bak`） | **均已删除** ✅ |
| 语法 | `ast.parse` 通过 ✅ |
| 复跑 | `tests/invariants/test_inv_core.py` = **22 passed** · `tests/invariants` = **39 passed** ✅ |

---

## 6. 全量回归与核验

| 项 | 结果 |
|---|---|
| 定向 `tests/invariants` | ✅ **39 passed** |
| **全量** | ✅ **1695 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1693 passed / 2 skipped / 0 failed**（52.9s，exit 0） |
| 对比基线 | 1685 collected / 1683 passed ⇒ **+10 = 新增用例**，**0 failed 不变** ⇒ **零既有回归** |
| **schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |
| **生产代码** | `git diff --name-only HEAD -- pyharness` = **0** ✅ |
| **Registry / Event Schema / Governance Core** | **0 改动** ✅ |
| 现有测试是否被改 | ❌ 未改（`+349/−7`，**7 行删除全为旧模块 docstring**，`grep "^-[^-]" \| grep -c "def \|assert "` = **0**）✅ |
| `git diff --check` | ✅ 通过 |

---

## 7. Coverage 状态变化

| # | 子性质 | 变化 | 判定依据（**破坏时新测试是否必然失败**） |
|---|---|---|---|
| **A4** | 缺件 ⇒ fail-closed | **partial → ✅ covered** | **必然失败** —— **M-A4 实证**（删 `session` 分支 ⇒ T-1[session] RED） |
| **A5** | 凡执行必经 `tools.execute` | **partial → ✅ covered** | **必然失败** —— **M-A5 实证**（旁路文件 ⇒ 两道闸 RED）；且闸①的**正向控制**防"删调用即变绿" |
| **A1 · A3** | `call_id` 配对 | covered → **covered（加严）** | **必然失败** —— **M-A3 实证**（抹掉配对键 ⇒ T-3a/T-3b RED） |
| **B5** | 挂载恒链尾 | covered → **covered（加严）** | **必然失败** —— **M-B3 实证**（插队 ⇒ T-4 RED，**且既有测试全绿** ⇒ 补的是真实缺口） |
| **F-1 新子性质** | `tool.error` 两类区分 | **新增 → ✅ covered** | **必然失败** —— **M-A6 实证**（关1b 多发 evaluated ⇒ T-5 RED） |

**INV-04 最终状态**：

```
INV-04 = 无 guard 事件即非法执行(含单调拒绝 / 无 bypass)
  ├─ covered  : 12   A1·A2·A3·A4·A5 · B1·B2·B3·B4·B5·B6 · + F-1 派生子性质(两类 tool.error)
  ├─ partial  : 0
  ├─ gap      : 0
  └─ deferred : 2    治理层相邻面(INV-G1/G5 不在本 ID 证据范围) · FC-8(5 条误标 INV-03 的编号修正)
```

**⚠️ 与 Registry 的对应**：
- Registry 记载的 gap ①（`tests/invariants/` 内 0 条）⇒ ✅ **本阶段关闭**
- gap ③（无"凡执行必经 `tools.execute`"静态扫描）⇒ ✅ **本阶段关闭**（T-2 双闸）
- gap ②（5 条误标 INV-03 致覆盖读数失真）⇒ **仍开放**（**FC-8**，`pending authorization`，本轮**未处理**）
- **Registry 文件本身未修改**

---

## 8. 未处理的 Deferred Items

| # | 项 | 状态 | 理由 |
|---|---|---|---|
| **D-1** | F-1 的范围裁定 | ✅ **已裁定**（口径 i）⇒ 该项**消解**；**未改 Registry**（依裁定"只更新设计理解"） | — |
| **D-2** | INV-04 的**治理层相邻面**（`INV-G1` 执行前必有 `decision.issued`；`INV-G5` 治理层无执行 API） | **未处理** | 属**治理不变量面**，不在 INV-04 的 Registry 证据范围内（`test_inv_governance.py` 已有 G1/G5 用例） |
| **D-3** | **FC-8**（`test_tools_guard.py` 的 5 条误标 INV-03 → INV-04） | **未处理** | S6-1 迁移清单，`pending authorization`；**本轮明令不处理** |
| **M-A7** | `guard.evaluated` 发射点后移的 mutation | **未执行** | 人工裁定：跨函数生命周期移动，**收益低于风险**；保留为设计说明 |

---

## 9. 是否发现真实生产缺陷

| 项 | 结论 |
|---|---|
| **本轮测试是否揭示真实生产缺陷** | ❌ **否** —— M-A3/A4/A5/B3/A6 均为**人为注入**的变异（验证测试鉴别力），**非既有代码缺陷**；恢复后全部 GREEN |
| **F-1（`tool.error` 范围不一致）** | ✅ 已由**裁定**（口径 i）解决为**设计理解**问题；**非缺陷**，**未改 Registry、未改实现** |
| **结论** | **无新增 KEY-FINDING**；**生产代码零改动** |

---

## 10. F-1 设计理解更新（依裁定记录，**未修改 Registry**）

> 人工裁定（2026-09-15）：**采用两类 `tool.error` 分类**，作为**设计理解**更新。

**INV-04(a)「执行前必有 `guard.evaluated`」的适用范围 = 真实工具执行路径**：

| 事件 | 要求 |
|---|---|
| **`tool.result`** | **必须**有对应 `call_id` 的 `guard.evaluated` |
| **`tool.error` —— 执行前失败**（unknown tool / invalid args / wiring failure） | **不要求** `guard.evaluated`；**必须证明 Provider 未调用** |
| **`tool.error` —— 执行后失败**（Provider 已进入执行路径） | **必须**有 `guard.evaluated`，且**先于** `tool.error`；两者 `call_id` 一致 |

**落地方式**：写入本报告（设计记录）+ **以 T-5 用例把该口径钉死为可执行断言**（两类各 1 组断言）。
**未做**：未修改 `docs/INVARIANT_REGISTRY.md`（依裁定）；后续若需在 Registry 中写清该口径，须**另行授权**。

---

## 11. 状态与边界

| 项 | 结果 |
|---|---|
| 改动面 | **仅 `tests/invariants/test_inv_core.py`（+349 / −7，7 行删除为旧 docstring）** ✅ |
| 新增测试 | **7 个函数 / 10 条执行用例**，全部标注 **INV-04** |
| 状态标注 | `IMPLEMENTED` + `TESTED` + `VERIFIED`（定向 + 全量 + M-A3/A4/A5/B3/A6 均有实证；M-A7 依裁定说明理由不执行） |
| 未做 | ❌ 改 `pyharness/` · ❌ 改 Registry / Event Schema / Governance Core · ❌ 处理 KF-B / FC-8 · ❌ 进入 INV-05 · ❌ commit · ❌ push |

**worktree**：`M tests/invariants/test_inv_core.py` + 未跟踪报告（`S6-2b-3_COVERAGE_AUDIT.md` · `S6-2b_MIDPOINT_REVIEW.md` · 本文件）；**HEAD 仍 `c44c9ce`**。

---

**S6-2b-3 Phase F 结束。未自动进入 Final Review；等待人工授权。**
