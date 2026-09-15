# S6-2b-4_COVERAGE_AUDIT.md — INV-05 覆盖审计（Phase A）

> **阶段**：S6-2b-4 Phase A（INV-05 Coverage Audit）｜ **日期**：2026-09-15
> **基线 commit**：`4a41068`（S6-2b Final Summary Freeze）｜ HEAD `4a41068` ｜ `main...origin/main [ahead 39]` ｜ worktree **clean**
> **性质**：**只读审计**。**未创建测试 · 未修改生产代码 / 测试 / Registry / Event Schema / Governance Core**；**未进入 Phase F**；**未 commit / 未 push**。
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-05）—— **未据旧注释重新解释**
> **基线实测**：全量 **1695 collected / 0 failures / 0 errors / 2 skipped = 1693 passed** · `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`

---

## Revision / Correction（2026-09-15 · 仅 `G-3`）

> **本节为事后更正**。**Phase A 的原始判断一律保留在上文**（**不删除、不重写**），仅在此声明其**哪一部分已被推翻**及**最终状态**。

| 项 | 内容 |
|---|---|
| **受影响条目** | **仅 `G-3`**（"审批来源拒绝的强同步留痕可证"） |
| **Phase A 原判（**保留**）** | `partial` · 假绿风险 **高** —— 依据："既有 approval 用例**全用 `FakeApproval` 替身**（**不发射** `approval.denied`/`approval.timeout`）⇒ 真实 `ApprovalProvider` 的 `sync=True` 落盘路径**无 INV-05 侧证据**" |
| **原判为何不成立** | 该推断**只核对了 executor 侧**，**漏查了 approval 模块自身的用例**。实测：**`tests/unit/test_approval.py::test_deny_flow`**（`:194`）与 **`tests/unit/test_approval.py::test_timeout_ttl_expiry`**（`:213`）**已用真实 `ApprovalProvider` + 真实 `SessionLog` + 真实 `EventBus`**（`_make_stack`）走 **deny / timeout** 两条路径，并各自断言 **`await _wait_flushed(store, seq)`**；而 `FakeStore.flush(seq)` **仅**由 `SessionLog._flush` 在 `sync or type in SYNC_TYPES` 时调用（`session.py:321-322`）⇒ **该断言即"该事件已强同步落盘"的证明** |
| **⇒ `G-3` 最终状态** | ✅ **`covered`**（在 **approval 模块层**已由**真实 Provider 路径**守护） |
| **推翻后的影响** | ① §4 汇总的 `partial 2` 应读作 **`partial 1`**（**仅 `C3`**）；② `G-3` **既非 gap 亦非 partial** ⇒ Phase F 的目标**收窄**为"为 INV-05 补**编号化锚点**"，而非重复建一套真实 Provider 强同步测试 |
| **Phase F 的落地** | `tests/invariants/test_inv_core.py::test_inv05_approval_reject_real_provider_strong_sync`（**参数化 `denied` / `timeout`**）—— **只补编号化锚点**，并在 docstring **显式引用**上述两条既有用例，声明"approval 模块层已覆盖、此处**不重复建设**" |
| **教训（供后续审计复用）** | 判定"某路径**无证据**"之前，**必须核实是否存在另一条真实路径的既有用例** —— 不能只据**替身**（`FakeApproval`）在某处的使用就断言缺失 |

**未受影响的条目**：`C1` / `C2` / `C4` / `C5` / `G-1` / `G-2` 的 Phase A 判断**保持不变**；`C3` 的 Phase A 判断为 `partial`，其后经 **F-2 裁定**（来源分列）+ **Phase F 实施**转为 `covered`（详见 `S6-2b-4_CHANGE_REPORT.md`）。

---

## 1. INV-05 Canonical Definition（照录）

> `docs/INVARIANT_REGISTRY.md` §1

**INV-05 = 拒绝后零副作用**

> 任意 reject（scope / guard / critical / 审批 `denied` / `timeout`）后，**Provider 调用计数 = 0** 且**无该 `call_id` 的 `tool.result`**；拒绝须**强同步**落 `guard.rejected`（`sync=True`），落盘失败即 fail-closed 上抛。

**Intent**：使"**拦了且没执行**"**可证** —— 防"`reject` 后仍执行 ⇒ 日志说拒、文件其实被删"这一**审计与真实世界分叉**（`SECURITY.md:214`）。

### 1.1 子性质拆解（5 条 + 3 条结构性）

| # | 子性质 |
|---|---|
| **C1** | 任意 reject 后 **Provider 调用计数 = 0** |
| **C2** | **无该 `call_id` 的 `tool.result`** |
| **C3** | 拒绝须**强同步**落 `guard.rejected`（`sync=True`） |
| **C4** | **落盘失败 ⇒ fail-closed 上抛**（绝不带未落盘的拒绝继续） |
| **C5** | 覆盖**全部** reject 来源：scope / guard / critical / 审批 `denied` / `timeout` |
| **G-1** | `tests/invariants/` 内有**编号化专项**（Registry `Coverage Gap` ①） |
| **G-2** | 有**单一编号锚点**（Registry `Coverage Gap` ②：现分散于 guard/executor/tool_fs/cli） |
| **G-3** | 审批来源拒绝的**强同步留痕**可证（见 §3 F-2）〔⚠️ 状态已更正为 `covered` —— 见文首 `Revision / Correction`〕 |

---

## 2. 真实实现审计（Phase B · 只读）

### 2.1 拒绝路径的数据流与**实测事件序**（只读探针，未创建文件）

| 拒绝来源 | 实测事件序 | `sync` 标记 | Provider 调用 | `tool.result` |
|---|---|---|---|---|
| **guard 拒**（如 `g-fs-path`） | `tool.call` → `guard.evaluated` → **`guard.rejected`** → `decision.issued` | rejected **`sync=True`** | **0** | **0** |
| **critical 拒**（`g-danger`，不可审批） | 同上（实测 `fs.delete_file` danger=critical） | rejected **`sync=True`** | **0** | **0** |
| **scope-hidden 拒** | `_evaluate_full` 的 scope 前置分支 ⇒ `evaluated` + `rejected(sync=True)` | rejected **`sync=True`** | **0** | **0** |
| **审批 `denied`**（替身） | `tool.call` → `guard.evaluated` → `decision.issued` — **无 `guard.rejected`** | `decision.issued` = sync | **0** | **0** |
| **审批 `timeout`** | 同 `denied` | 同上 | **0** | **0** |
| **`APR-501`**（headless 无通道） | 审批不可用 ⇒ 直接拒（`_rejected` 登记） | — | **0** | **0** |
| **`GRD-403`**（批准后策略收紧 / 绑定不符） | 直接拒（`_rejected` 登记） | — | **0** | **0** |

**关键实现事实**：
- `guard.rejected` 的**唯一发射点** = `GuardChain._append_rejected`（`tools_guard.py:830-844`），`sync=True` 硬编码于 `_append`（`:843`）；
- 强同步清单**唯一真源** = `events.vocab.SYNC_TYPES`（含 `guard.rejected` 与 `approval.denied`/`approval.timeout`）；
- 审批侧强同步落盘在 `approval.py:413`（`sess.append(type_, payload, actor=actor, sync=True, …)`），`approval.denied`/`approval.timeout` 均在 `SYNC_TYPES` 内；
- 关 3（Provider 执行）**只在** `d == "allow"` 且审批 `executed=True` 时到达；所有 reject/非放行分支**均在关 3 之前 return**。

### 2.2 审计问逐项回答

| 审计问 | 实测答案 |
|---|---|
| **第二写路径** | ❌ **无** —— `guard.rejected` 只经 `GuardChain._append`；`tool.result` 只经 executor `_finalize` |
| **绕过路径** | ❌ **未发现** —— 关 3 的唯一入口在 `execute()` 内、严格位于关 2/2.5 之后 |
| **隐式副作用** | ❌ **无关**前路径 —— 拒绝分支不触 Provider |

### 2.3 ⚠️ 审计发现 O-1：`tools_executor._reject` 为**死代码**

`tools_executor.py:633-649` 的 `_reject()`（"executor 侧终局拒 + `guard.evaluated` + `guard.rejected(sync=True)`"）**全库零调用点**（`grep "self._reject(" → 无命中`）。
其职责已被 `GuardChain._evaluate_full` 的 `_append_rejected` 完全覆盖（`d == "reject"` 分支在 `:480-481` 直接 return）。
⇒ **结构债（非缺陷、非 INV-05 违约）**，建议随 deferred cleanup 处理；**本轮只记录**。

### 2.4 ⚠️ 审计发现 F-2：C3 的**字面范围 > 实现域**（与 F-1 同型）

INV-05 的 C3 逐字为"拒绝须**强同步**落 **`guard.rejected`**（`sync=True`）"。但**审批来源**的拒绝（`denied`/`timeout`/`APR-501`/`GRD-403`）**不落 `guard.rejected`**，其强同步留痕是 **`approval.denied` / `approval.timeout`**（亦 `sync=True`，见 `approval.py:413` + `SYNC_TYPES`）。

| 口径 | 判定 |
|---|---|
| **字面**（"拒绝须落 `guard.rejected`"） | ⚠️ **审批来源不满足** —— 它们落的是 `approval.*` |
| **意图**（"拒绝事实必须强同步可证"） | ✅ **满足** —— `approval.denied`/`approval.timeout` 同为 `sync=True` 且进 `SYNC_TYPES` |
| **是否缺陷** | ❌ **否** —— 无非法执行、无副作用、留痕完整 |
| **为何必须登记** | **直接影响测试设计** —— 朴素的 `test_inv05_reject_always_emits_guard_rejected` **今天会对审批路径 RED**（且是**真** RED） |

**建议（待人工裁定，与 F-1 同型）**：认定 C3 的"落 `guard.rejected`"**仅适用于 guard 链来源**；**审批来源的强同步留痕由 `approval.*` 承载**（同为 `SYNC_TYPES`）⇒ 如需在 Registry 写清，须**另行授权**。

---

## 3. 既有测试证据盘点（Phase A）

### 3.1 直接承载 INV-05 的用例（Registry Evidence Source 范围）

| 子性质 | 用例 | 断言要点 |
|---|---|---|
| **C3（guard 来源）** | `test_tools_guard.py::test_reject_event_pair_order_and_sync` | 先 `evaluated(deny)` 后 `rejected`；**`rj[0]["sync"] is True`**；payload 含 `guard_id`/`policy_ref` 且**不含参数原文** |
| | `::test_rejected_sync_awaited_with_async_session` | 异步 session 下 `sync` 语义 |
| **C3（scope 来源）** | `::test_scope_hidden_reject_events_grd401` | scope-hidden ⇒ 两事件齐备 |
| **C4** | `::test_append_failure_fail_closed` | `guard.rejected` **与** `guard.evaluated` 落盘失败**均上抛**（fail-closed） |
| **C1 · C2 · C5** | `test_tools_executor.py::test_guard_reject_critical_zero_side_effects` | critical 拒 ⇒ `prov.calls == 0` + 无 `tool.result` |
| | `::test_scope_hidden_terminal_reject` | scope-hidden ⇒ 零副作用 |
| | `::test_verdict_not_granted_no_execute`（**参数化 denied/timeout**） | `prov.calls == 0` + **`tool.result == [] and tool.error == []`** |
| | `::test_headless_apr501_no_execute` | APR-501 ⇒ 不执行 |
| | `::test_granted_then_policy_tightened_grd403` · `::test_granted_binding_mismatch_denied` · `::test_binding_mismatch_still_denies` | 批准后收紧 / 绑定不符 ⇒ 不执行 |
| | `::test_approval_not_granted_single_decision` | denied ⇒ 恰一条 `decision.issued`、无 D2 |
| | `::test_t5_denied` · `::test_t6_timeout` | denied/timeout：D1 恰一条、Provider 零执行 |
| | `test_tool_fs.py::test_executor_critical_delete_denied_zero_side_effect` | 文件真实未被删（**副作用面实证**） |
| | `test_cli.py::test_run_headless_rejected_listing` | headless 拒 ⇒ 拒绝清单回显、工作区零副作用 |

### 3.2 编号化现状

| 检查 | 结果 |
|---|---|
| `tests/invariants/` 内 **INV-05 编号化用例** | ❌ **0 条**（Registry gap ① 成立） |
| 单一编号锚点 | ❌ **无** —— 证据分散于 `test_tools_guard.py` / `test_tools_executor.py` / `test_tool_fs.py` / `test_cli.py` **四文件**（Registry gap ② 成立） |
| 与 INV-04 的重叠 | `test_inv_core.py::test_inv04_reject_path_call_id_pairing` 已断言拒绝路径**无 `tool.result`** + `prov.calls == 0`（**INV-04 的 A3 面**）⇒ 与 INV-05 的 C2 **部分重叠**，但**未断言强同步留痕** |

---

## 4. Coverage Matrix（Phase C · 逐子性质判定）

> 判定口径：**"若该性质被破坏，现有测试是否必然失败？"** + **是否存在测试假绿风险？**

| # | 子性质 | 状态 | 决定性证据 | **破坏时必红？** | **假绿风险** |
|---|---|---|---|---|---|
| **C1** | 任意 reject 后 Provider 调用计数 = 0 | ✅ **covered** | 5 类来源**各有** `prov.calls == 0` 断言（guard/critical/scope/审批 denied/审批 timeout/APR-501/GRD-403） | ✅ **是** | **低** |
| **C2** | 无该 `call_id` 的 `tool.result` | ✅ **covered** | `sess.of("tool.result") == []`（多例）· `test_tool_fs.py` 断言**文件真实未变**（副作用面） | ✅ **是** | **低** |
| **C3** | 拒绝须**强同步**落 `guard.rejected` | ⚠️ **partial** | **guard/scope 来源 ✅**（`sync is True` 直接断言）· **审批来源 ❌ 不落 `guard.rejected`**（落 `approval.*`，见 §2.4 F-2） | ⚠️ **guard 侧是；审批侧否** | **中**（见下） |
| **C4** | 落盘失败 ⇒ fail-closed 上抛 | ✅ **covered** | `test_append_failure_fail_closed`（`rejected` 与 `evaluated` **两条**均验） | ✅ **是** | **低** |
| **C5** | 覆盖全部 5 类 reject 来源 | ✅ **covered** | 五类各有零副作用断言；另有 APR-501 / GRD-403 / 绑定不符三类**额外**路径 | ✅ **是** | **低** |
| **G-1** | `tests/invariants/` 编号化专项 | ❌ **gap** | 0 条 | — | — |
| **G-2** | 单一编号锚点 | ❌ **gap** | 分散 4 文件 | — | — |
| **G-3** | 审批来源的强同步留痕可证 | ⚠️ **partial** | 既有 approval 用例**全用 `FakeApproval` 替身**（**不发射** `approval.denied`/`approval.timeout`）⇒ 真实 `ApprovalProvider` 的 `sync=True` 落盘路径**无 INV-05 侧证据** | ❌ **不一定** | **高**（替身不发射事件 ⇒ 停发也绿）<br>⚠️ **本行已更正：最终状态 = `covered`（非 partial、非高假绿）—— 见文首 `Revision / Correction`** |

**汇总**：`covered` **3**（C1 · C2 · C4 · C5 —— 计 4 条）· `partial` **2**（C3 · G-3）· `gap` **2**（G-1 · G-2）· `deferred` **0**。
> ⚠️ **本行已更正**（`G-3` 转 `covered`）⇒ **Phase A 汇总应读作**：`covered` **4**（C1 · C2 · C4 · C5；另 `G-3` 亦为 `covered`）· `partial` **1**（**仅 C3**）· `gap` **2**（G-1 · G-2）· `deferred` **0**。（见文首 `Revision / Correction`；`C3` 其后亦经 F-2 裁定 + Phase F 转 `covered`）

> **精确计数**：C1 / C2 / C4 / C5 = **4 covered**；C3 / G-3 = **2 partial**；G-1 / G-2 = **2 gap**。
> ⚠️ **本行已更正**：`G-3` 最终为 **`covered`** ⇒ 应读作 **5 covered**（C1 · C2 · C4 · C5 · **G-3**）· **1 partial**（仅 C3）· **2 gap**。（见文首 `Revision / Correction`）

---

## 5. Partial / Gap 的具体原因

| # | 类别 | 为什么不算 covered |
|---|---|---|
| **C3** | partial | ① **F-2 范围不一致**：定义字面点名 `guard.rejected`，审批来源落的是 `approval.*`；② **审批侧的强同步无可执行断言** |
| **G-3** | partial | 既有 approval 流程用例**一律使用 `FakeApproval` 替身**，替身**不产生事件** ⇒ "拒绝强同步落盘"的真实路径（`approval.py:413`）**没有被 INV-05 的证据触及**；若真实 Provider 停发 `approval.denied`，现有 INV-05 用例**仍全绿**<br>⚠️ **本行已更正：`G-3` 最终为 `covered`** —— 真实路径证据在 `tests/unit/test_approval.py::test_deny_flow` / `::test_timeout_ttl_expiry`（**见文首 `Revision / Correction`**） |
| **G-1** | gap | `tests/invariants/` 内**零** INV-05 编号化用例 |
| **G-2** | gap | 无单一编号锚点 ⇒ **"是否所有拒绝路径都已覆盖"无法从编号读出**；新增一条拒绝来源时无机制提醒补测 |

---

## 6. 是否发现真实生产缺陷

| 发现 | 判定 |
|---|---|
| **F-2（C3 字面范围 > 实现域）** | ⚠️ **非缺陷** —— 意图（拒绝事实强同步可证）**已满足**（`approval.*` 同 `SYNC_TYPES` 且 `sync=True`）；属**定义-实现范围不一致**，需人工裁定口径 |
| **O-1（`tools_executor._reject` 死代码）** | ⚠️ **结构债**（零调用点），**非缺陷、非违约**；建议随 deferred cleanup 处理 |
| **C1 / C2 / C4 / C5 的实现面** | ✅ **无问题** —— 所有 reject 分支均在关 3 之前 return；`guard.rejected` 单一发射点；`_append` 的 `sync=True` 路由至 `SYNC_TYPES` |
| **结论** | **未发现真实生产缺陷** ⇒ **无新增 KEY-FINDING**（F-2 建议登记为 scope 说明项，与 F-1 同型） |

---

## 7. 最小测试设计（**只设计，不创建**）

> 原则：只补 C3 / G-1 / G-2 / G-3；**不重复** C1 / C2 / C4 / C5 的既有断言（只建映射）。
> 落点：`tests/invariants/test_inv_core.py`（续用 S6-2b 既有体例）。

| # | 目标 | 用例（建议名） | 设计要点 |
|---|---|---|---|
| **T-1** | **G-1 · G-2 · C1 · C2**（单一编号锚点） | `test_inv05_reject_paths_zero_side_effect_and_strong_sync` | **参数化 5 类 reject 来源**（scope-hidden / guard / critical / 审批 `denied` / 审批 `timeout`）；每例**三合一**断言：① `prov.calls == 0` ② `sess.of("tool.result") == []` ③ **该 `call_id` 有强同步拒绝留痕**（guard 来源 = `guard.rejected[0]["sync"] is True`；审批来源 = `approval.*`，取决于 F-2 裁定） |
| **T-2** | **C3（guard 侧）· G-2** | `test_inv05_guard_reject_is_strongly_synced` | 真实 `GuardChain(session=真实/替身)` 走 guard reject ⇒ 断言 `guard.rejected` **恰一条**且 `sync is True`，并断言 `guard.evaluated` 的 `sync` 为 `False`（**对照面**，防"全 sync"式假绿） |
| **T-3** | **C4 端到端** | `test_inv05_reject_flush_failure_does_not_reach_provider` | 令 `guard.rejected` 落盘失败 ⇒ 断言 `execute()` **上抛**（`CYC-999`/`PERS-202` 语义）且 **`prov.calls == 0`**（把 guard 侧的 C4 提升到 **executor 端到端**：确认 authorize 抛错时执行链**不继续**） |
| **T-4** | **G-3（审批来源强同步）** | `test_inv05_approval_reject_strong_sync_evidence` | 走**真实 `ApprovalProvider`**（非替身）⇒ 断言 `approval.denied` / `approval.timeout` 以 **`sync=True`** 落盘（若 F-2 裁定为"审批来源由 `approval.*` 承载"，本用例即为该口径的**编号化锚点**） |

**不新增（只建映射）**：C1/C2（`test_guard_reject_critical_zero_side_effects` · `test_scope_hidden_terminal_reject` · `test_verdict_not_granted_no_execute` · `test_executor_critical_delete_denied_zero_side_effect`）· C4 的 guard 侧（`test_append_failure_fail_closed`）· C5 的额外来源（APR-501 / GRD-403 / 绑定不符）。

---

## 8. Mutation 设计（Phase E · **只设计，不执行**）

| # | Mutation（最小） | 目标测试 | 预期 | 安全性/代价 |
|---|---|---|---|---|
| **M-C1** | `_evaluate_full` 的 reject 分支由"短路 return"改为 `continue`（继续求值） | **T-1** · 既有 `test_guard_reject_critical_zero_side_effects` | **RED**（后续 guard 全 allow ⇒ 可能落到关 3 ⇒ Provider 被调） | ✅ 1 行 |
| **M-C2** | `_append_rejected` 的 `sync=True` → `sync=False` | **T-2** · 既有 `test_reject_event_pair_order_and_sync` | **RED**（`sync is True` 断言失败） | ✅ 1 行；**这是 T-2 的专属 mutation** |
| **M-C3** | `_evaluate_full` 的 reject 分支**删去** `_append_rejected` 调用 | **T-1/T-2** · `test_reject_event_pair_order_and_sync` | **RED**（无 `guard.rejected`） | ✅ 1 行 |
| **M-C4** | `GuardChain._append` 吞掉落盘异常（`try/except: pass`） | **T-3** · 既有 `test_append_failure_fail_closed` | **RED**（不再上抛） | ✅ 1 行 |
| **M-C5** | executor 的 `d == "reject"` 分支**改为不 return**（继续下落至关 3） | **T-1** · `test_guard_reject_critical_zero_side_effects` | **RED**（Provider 被调 + 出现 `tool.result`） | ✅ 1 行；**这是 C1/C2 的核心 mutation** |
| **M-C6** | `approval.py` 的 `_settle` 落盘改 `sync=False` | **T-4** | **RED**（审批拒绝不再强同步） | ✅ 1 行；**仅在 T-4 采纳真实 Provider 路径时才有意义** |

**说明**：M-C2 / M-C5 分别为 **T-2 / T-1** 的专属 mutation；M-C6 仅对 **T-4** 有效（若 T-4 未创建或使用替身，则该 mutation 无可观测效果 —— 这本身即 G-3 的证据）。

---

## 9. 当前边界说明

**做了**：读取 Registry 的 INV-05 定义并拆解 5+3 条子性质 · 读实现（`GuardChain._evaluate_full`/`_append_rejected`/`_append` · executor 的四关与审批轮 · `approval.py` 的 `_settle`/`sync=True` · `SYNC_TYPES`）· **只读探针实测** guard/critical/审批拒绝三类的事件序与 sync 标记 · 盘点既有 INV-05 证据（4 文件 · 15+ 用例）· 覆盖分类（4 covered / 2 partial / 2 gap）· 最小测试设计（4 条）· mutation 设计（6 条）。

**没做**（本阶段明令禁止）：❌ 创建测试 · ❌ 修改生产代码 · ❌ 修改测试 · ❌ 修改 Registry / Event Schema / Governance Core · ❌ 进入 Phase F · ❌ commit · ❌ push。

**产出**：本文件（`S6-2b-4_COVERAGE_AUDIT.md`）唯一。
**worktree**：仅 `?? S6-2b-4_COVERAGE_AUDIT.md`；**HEAD 仍 `4a41068`**。

---

**S6-2b-4 Phase A 结束。等待人工裁定 F-2 口径与 Phase F 实施授权；未创建测试、未改代码、未 commit、未 push。**
