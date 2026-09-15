# S6-2b-3_COVERAGE_AUDIT.md — INV-04 覆盖审计（Phase A）

> **阶段**：S6-2b-3 Phase A（INV-04 Coverage Audit）｜ **日期**：2026-09-15
> **基线 commit**：`c44c9ce`（S6-2b-2 Freeze）｜ HEAD `c44c9ce` ｜ `main...origin/main [ahead 36]` ｜ worktree **clean**
> **性质**：**只读审计**。**未创建测试 · 未修改生产代码 / Registry / Event Schema / Governance Core**；**未 commit / 未 push**。
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-04）—— **未据旧注释重新解释**
> **基线实测**：全量 **1685 collected / 0 failures / 0 errors / 2 skipped = 1683 passed** · `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`

---

## 1. INV-04 Canonical Definition（照录）

> `docs/INVARIANT_REGISTRY.md` §1

**INV-04 = 无 guard 事件即非法执行（含 guard 单调拒绝 / 无 bypass）**

- **(a) 执行前必有求值事实**：每个 `tool.result` / `tool.error` 之前必有**同 `call_id`** 的 `guard.evaluated`，缺失 = **非法执行**
- **(b) 结构单调性**：guard 决策**恰三值** `{allow, reject, approval}`，**无 bypass 第四值**；`GuardChain` **无任何** `override` / `bypass` / `force_allow` / `set_allow` / `execute` / 移除 / 重排 / 翻回 API，且**一次 reject 不可被后续挂载翻回 allow**（只增拒绝面）

### 1.1 子性质拆解（11 条）

| 面 | # | 子性质 |
|---|---|---|
| **(a)** | **A1** | 每个 `tool.result` 之前必有同 `call_id` 的 `guard.evaluated` |
| | **A2** | 每次求值**恰好一条** `guard.evaluated`（不多不少） |
| | **A3** | `guard.evaluated` 与 `tool.result` 经 **`call_id`** 关联（`trace` 携带） |
| | **A4** | 缺任一件 ⇒ **fail-closed 拒绝执行**（结构上不可带缺件执行） |
| | **A5** | **凡执行必经 `tools.execute`**（无绕过路径） |
| **(b)** | **B1** | 决策**恰三值**，无 bypass 第四值 |
| | **B2** | `GuardChain` **无** override/bypass/force_allow/set_allow/execute 类 API |
| | **B3** | **一次 reject 不可被后续挂载翻回 allow**（只增拒绝面） |
| | **B4** | **waterfall 短路**：首个非 allow 即停，后续 guard **不再求值** |
| | **B5** | **无移除/重排 API**；挂载**恒在链尾** |
| | **B6** | `disable` **须 config 显式声明**（`config_ref` 留痕）；`g-schema`/`g-danger` **强制恒在** |

---

## 2. 实现审计（Phase B · 真实数据流 / 状态来源 / 绕过面）

### 2.1 Face (a) 的真实数据流

```
ToolExecutor.execute(ctx, call)
  ├─ :521  _require_wiring(ctx)        ← ★ fail-closed:session / scope / guard / governance 四件必接线,
  │                                        缺任一 → CYC-999 上抛(绝不带缺件执行)
  ├─ 关1a  lookup(call.name)           ← 失败:TLB-802 ⇒ _on_error ⇒ tool.error
  ├─ 关1b  validate_args(...)          ← 失败:TLB-803 ⇒ _on_error ⇒ tool.error
  ├─ :549  append("tool.call", {args, raw_args, call_id})   ← 双份存档(INV-06)
  ├─ 关2   d = await ctx.governance.authorize(call, ctx, inputs_digest=…)
  │            └─ GovernanceContext.authorize(governance/context.py:81)
  │                   └─ GuardChain.evaluate_detailed()            (tools_guard.py:735)
  │                          └─ _evaluate_full()                   (tools_guard.py:672)
  │                                 ├─ _audit("guard.evaluated")   恰一条 (:699 scope-hidden / :720 短路 / :724 全 allow)
  │                                 └─ reject ⇒ _append_rejected("guard.rejected", sync=True) (:722 → :838)
  │                   └─ DecisionEngine.decide() ⇒ Decision ⇒ append("decision.issued")
  ├─ 关2b  审批轮(必要时重入 authorize(prior=D1) → D2)
  └─ 关3   _run_provider(…) → _invoke(defn, args, ctx) → provider.handle(args, ctx)
              └─ _finalize ⇒ append("tool.result")
```

### 2.2 审计问逐项回答

| 审计问 | 实测答案 | 证据 |
|---|---|---|
| **真实数据流** | 见 §2.1；`guard.evaluated` 的**唯一发射点** = `GuardChain._audit`，由 `_evaluate_full` 在**三条出口各调一次** | `tools_guard.py:699,720,724` |
| **状态来源** | `GuardChain` 的唯一状态 = **链装配**（`chain` / `disabled` / `_snapshot_seq`）；**求值本身无状态残留** | `:623-624`（docstring 声明）· `:657,658,668` |
| **是否存在第二写路径** | ❌ **无** —— `guard.evaluated`/`guard.rejected` 只经 `GuardChain._append` 单一出口；`tool.result`/`tool.error` 只经 executor 的 `_append` | `tools_guard.py:846`（`_append` 统一封装） |
| **是否存在绕过路径** | ❌ **未发现** —— **AST 全库扫描 Provider 调用面**：`.handle(` 调用点**仅** `tools_executor._invoke`（`tool_skill.py:53` 是 Provider 委派给其**内层 handler**，非绕过 executor 执行工具） | §2.3 |
| **是否存在隐式副作用** | ⚠️ **有一处需登记（F-1）**：`关1a/关1b` 失败路径发 `tool.error` 而**无** `guard.evaluated` | §3 F-1 |

### 2.3 Provider 执行面（AST 扫描，绕过排查）

```
pyharness/core/tool_skill.py:53   handle.handle({**args, "_op": op}, ctx)   ← Provider 内层委派(非工具执行绕行)
（其余 .handle( 命中均为 HTMLParser.handle_starttag/… ，与执行面无关）
```

`tools_executor` 内部：`_run_provider`（`:691`）→ `_invoke`（`:737`）→ `provider.handle`，**唯一执行入口** ✓

---

## 3. ⚠️ 实现审计发现 F-1：`tool.error` 的字面范围 > 实现域（**非缺陷，但需裁定**）

### 3.1 事实（**实证，非推断**）

用既有测试夹具**实际运行**两条 pre-guard 失败路径（只读探针，未创建测试文件）：

| 路径 | Provider 调用 | 发出的事件序列 | 含 `guard.evaluated`？ |
|---|---|---|---|
| **关1a** 未知工具（`TLB-802`） | **0** | `['tool.error']` | ❌ **否** |
| **关1b** 参数非法（`TLB-803`） | **0** | `['tool.error']` | ❌ **否** |

### 3.2 三方口径对照

| 口径 | 判定 |
|---|---|
| **INV-04(a) 的**字面**（"每个 `tool.result` **/ `tool.error`** 之前必有同 `call_id` 的 `guard.evaluated`"） | ⚠️ **字面上不满足** —— 上述两条 `tool.error` 无前置 `guard.evaluated` |
| **INV-04(a) 的**意图**（"缺失 = **非法执行**"） | ✅ **满足** —— Provider **零调用**，**不是执行**（关 1a/1b 在关 2 之前返回） |
| **治理哨兵的**实现**（`governance/audit.py` 的 `NO-GUARD-EVENT`） | ✅ **与意图一致** —— 其 `executed` 集合**只由 `tool.result` 构建**（`audit.py` 中 `if type == "tool.result"`），**不检查 `tool.error`** |

### 3.3 判定（**本轮只记录，不修复**）

| 项 | 结论 |
|---|---|
| **是否真实生产缺陷** | ❌ **否** —— 无非法执行、无安全绕过；Provider 零调用；审计留痕正常（`tool.error` 本身即留痕） |
| **性质** | **定义-实现范围不一致（scope discrepancy）** —— INV-04(a) 的**字面**（含 `tool.error`）**宽于**其**实现域与哨兵域**（仅"执行"= `tool.result`） |
| **为何必须登记** | ① **直接影响测试设计** —— 朴素的 `test_inv04_every_tool_error_has_guard_evaluated` **今天必然 RED**（且是**真** RED，非测试写错）；② 若不裁定，后续审计会把它报成"违约" |
| **建议** | 由人工裁定：**(i)** 认定 INV-04(a) 的"执行前"**仅指执行路径**（`tool.result`），并**在 Registry 的 `Canonical Definition` 中把 `tool.error` 的适用范围写清**（须走正式授权，本轮禁改 Registry）；**或 (ii)** 要求 关1a/1b 也发 `guard.evaluated`（**改变现有行为**，代价大且语义可疑：未过 guard 的调用谈不上"guard 求值"）。**建议 (i)** |
| **是否建 KEY-FINDING** | **建议登记为 scope 说明项**（非缺陷）；**本轮不写 KEY-FINDINGS**（依边界） |

---

## 4. 既有测试证据盘点（Phase A）

| 文件 | 用例 | 覆盖子性质 | **编号标注** |
|---|---|---|---|
| `tests/unit/test_tools_guard.py` | `test_monotonic_api_surface_no_allow` | B2 · B5 | ✅ 已标 **INV-04** |
| | `test_evaluate_allow_single_evaluated_event` | A2（allow 路径恰一条） | ✅ 已标 **INV-04** |
| | `test_decision_tri_value_exact_no_bypass` | **B1** | ⚠️ **误标 INV-03** |
| | `test_guard_base_contract` | B2（`allows_nothing_extra`） | ⚠️ **误标 INV-03** |
| | `test_forbidden_bypass_api_attribute_error` | **B2 · B4**（`_FORBIDDEN_API` 逐名 + `c.checked == 0`） | ⚠️ **误标 INV-03** |
| | `test_reject_deterministic_not_flippable` | **B3** | ⚠️ **误标 INV-03**（并涉 INV-05） |
| | `test_register_after_reject_cannot_rescue` | **B3 · B5** | ⚠️ **误标 INV-03** |
| | `test_builtin_chain_fixed_order_and_forced_guards` | **B6**（`FORCED_GUARDS == {g-schema, g-danger}`） | 未标编号 |
| | `test_disable_forced_guards_cfg601` · `:1045` | **B6**（config_ref 必需） | 未标编号 |
| | `test_scope_hidden_reject_events_grd401` | A1（scope-hidden 路径留痕）· 涉 INV-05 | 未标编号 |
| `tests/unit/test_tools_executor.py` | `:289` 断言 `["tool.call","guard.evaluated","decision.issued","tool.result"]` | **A1 · A3**（经 `call_id`/事件序） | 未标编号 |
| | `:950` 断言 `["tool.call","guard.evaluated","guard.rejected","decision.issued"]` | **A1 · A2**（reject 路径） | 未标编号 |
| | `:407-415`（缺 guard → `CYC-999`）· `:960-969`（缺 governance → `CYC-999`） | **A4**（4 分支中 **2** 个） | 未标编号 |
| `tests/unit/test_engine.py` | `test_guard_from_config_injects_schema_validator` · `test_s23_tc_policy_rules_carry_injected_params` | 装配面（g1 注入 ⇒ INV-04 的内层复查生效） | 未标编号 |
| `tests/invariants/` | **无任何 INV-04 用例** | — | — |

**关键观察**：**覆盖几乎全部由 `test_tools_guard.py` 承载，其中 5 条误标 INV-03** ⇒ Registry 记载的 gap ② 依然成立；`tests/invariants/` 内 **0 条**（gap ①）。

---

## 5. Coverage Matrix（Phase C · 逐子性质判定）

> 判定口径：**"若该性质被破坏，现有测试是否必然失败？"** + **是否存在测试假绿风险？**

| # | 子性质 | 状态 | 决定性证据 | 破坏时必红？ | **假绿风险** |
|---|---|---|---|---|---|
| **A1** | `tool.result` 前必有同 `call_id` 的 `guard.evaluated` | ✅ **covered** | `test_tools_executor.py:289` **完整事件序**断言（含 `decision.issued`）· governance `audit.py::NO-GUARD-EVENT` 运行时哨兵 | ✅ 是 | **低**（序断言精确到列表相等） |
| **A2** | 每次求值**恰一条** `guard.evaluated` | ✅ **covered** | `test_evaluate_allow_single_evaluated_event`（allow 恰 1）· `:927`/`:1037` 计数 · `:950` 序（reject 恰 1）· `_evaluate_full` 三条出口各一次 | ✅ 是 | **低**（既查"恰一条"又查序） |
| **A3** | 经 `call_id` 关联 | ✅ **covered** | `:289` 序 + `_audit` 写 `trace={"call_id":…}`（`tools_guard.py:828`）+ 治理哨兵按 `trace.call_id` 配对 | ✅ 是 | **中**：若 `call_id` 被写成**同一常量**，序断言仍绿 ⇒ 关联**语义**未直接断言（见 §7 G-1） |
| **A4** | 缺件 ⇒ fail-closed | ⚠️ **partial** | `_require_wiring` **4 分支**；既有用例覆盖 **2 个**（缺 `guard` :407 · 缺 `governance` :960）；**缺 `session` / 缺 `scope` 分支无独立用例** | ❌ **不一定**（未覆盖的 2 分支被破坏时无测试失败） | **中** |
| **A5** | 凡执行必经 `tools.execute`（无绕过） | ⚠️ **partial** | **结构上成立**（AST 扫描：Provider 调用面仅 executor）· 但 **无静态扫描用例**（Registry gap ③） | ❌ **否**（新增绕过路径无人拦截） | **高**（无守卫 = 静默退化） |
| **B1** | 决策恰三值，无 bypass 第四值 | ✅ **covered** | `test_decision_tri_value_exact_no_bypass` · `_evaluate_full:709` 对未知值抛 `CYC-999` | ✅ 是 | **低** |
| **B2** | 无 override/bypass/execute 类 API | ✅ **covered** | `test_forbidden_bypass_api_attribute_error`（**逐名** `hasattr` 假）· `test_monotonic_api_surface_no_allow` | ✅ 是 | **低**（逐名遍历 `_FORBIDDEN_API`） |
| **B3** | 一次 reject 不可被翻回 | ✅ **covered** | `test_register_after_reject_cannot_rescue`（追加 allow 型 guard 后仍 reject）· `test_reject_deterministic_not_flippable` | ✅ 是 | **低** |
| **B4** | waterfall 短路（后续 guard 不求值） | ✅ **covered** | `test_forbidden_bypass_api_attribute_error` 内 `c.checked == 0`（`c` 在 `a`/`b` 之后） | ✅ 是 | **低** |
| **B5** | 无移除/重排 API；挂载恒链尾 | ✅ **covered** | 同 B2（`_FORBIDDEN_API` 含 `clear_reject`/`release_decision`）· `register_plugin_guard:769` 链尾追加 + 重名/已禁用拒绝 | ✅ 是 | **中**：**"仅追加链尾"的顺序语义**未直接断言（见 §7 G-2） |
| **B6** | `disable` 须 `config_ref`；强制 guard 恒在 | ✅ **covered** | `test_disable_forced_guards_cfg601`（对 g-schema/g-danger 抛 CFG-601）· `test_builtin_chain_fixed_order_and_forced_guards`（`FORCED_GUARDS == {g-schema,g-danger}`）· `:1045`（带 `config_ref` 可关） | ✅ 是 | **低** |

**汇总**：`covered` **8** · `partial` **2**（A4 · A5）· `gap` **0** · `deferred` **1**（见 §8）+ **1 条范围不一致（F-1）**。

---

## 6. True Gaps（真实缺口）

| # | 缺口 | 类型 |
|---|---|---|
| **G-1** | **`guard.evaluated` 与 `tool.result` 的 `call_id` **配对一致性**未直接断言** —— 现有断言分别是"事件序"与"哨兵配对"，但**无**"同一次调用的 `guard.evaluated.trace.call_id` **等于** `tool.result.payload.call_id`"的**逐字段**断言。⇒ 若实现把两者的 `call_id` 写成**不同的值**（或恒为常量），现有测试**可能仍绿** | **假绿风险（中）** |
| **G-2** | **`register_plugin_guard` 的"**恒在链尾**"顺序语义**未直接断言** —— 现有仅断言"追加后不可翻回"，未断言**新 guard 的索引 == len(chain)-1** | 假绿风险（中） |
| **G-3** | **`_require_wiring` 的 `session` / `scope` 两分支无独立用例** | 覆盖缺口 |
| **G-4** | **A5（凡执行必经 `tools.execute`）无静态扫描用例**（Registry gap ③ 原文） | 覆盖缺口（**全部缺失**） |
| **G-5** | `tests/invariants/` 内 **0 条** INV-04 编号化用例（Registry gap ①） | 编号化缺口 |

---

## 7. Partial Properties（为什么不算 covered）

| # | 子性质 | 为什么不是 covered | 破坏时的实际后果 |
|---|---|---|---|
| **P-1** | **A4** 缺件 ⇒ fail-closed | `_require_wiring` 有 **4** 个缺件分支，既有用例只覆盖 **2** 个（`guard` / `governance`）；`session` / `scope` 分支被破坏时**无测试失败** | 缺 `ctx.session` 或 `ctx.scope` 时若不再 fail-closed ⇒ **静默执行**（无事件落点/无 scope 前置），现有测试**全绿** |
| **P-2** | **A5** 无绕过路径 | **结构上成立但无守卫** —— 无任何静态扫描断言"Provider 执行面仅 `tools_executor`"；新增绕过路径（如某模块直接 `provider.handle(...)`）**不会被任何测试拦截** | 出现旁路执行 ⇒ 该执行**无 `guard.evaluated`**（INV-04 的核心失效形态），而测试面**无感** |

---

## 8. Deferred Items

| # | 项 | 理由 |
|---|---|---|
| **D-1** | **F-1 的范围裁定与（若采纳口径 ii）实现变更** | 属**定义澄清 / 行为变更**，须人工裁定；**本轮不改 Registry、不改实现** |
| **D-2** | INV-04 的**治理层对应面**（`INV-G1`：`decision.issued` 必先于执行；`INV-G5`：治理层无执行 API） | 属**治理不变量面**（`tests/invariants/test_inv_governance.py` 已有 G1/G5 用例）⇒ 不在 INV-04 的 Registry 证据范围内，登记为**相邻面** |
| **D-3** | **FC-8**（`test_tools_guard.py` 的 5 条**误标 INV-03** 修正） | 属 S6-1 迁移清单，`pending authorization`；**未修正前，INV-04 的真实覆盖无法从编号读出** |

---

## 9. 最小测试设计（**只设计，不创建**）

> 原则：只补 §6/§7 的真实缺口；**不重复**已 covered 的 8 条（A1/A2/A3/B1~B6 的既有断言只建映射）。
> 建议落点：`tests/invariants/test_inv_core.py`（续用 S6-2b-1/2 的体例与 `_wired_log`-类接线辅助）。

| # | 目标缺口 | 用例（建议名） | 设计要点 |
|---|---|---|---|
| **T-1** | **A4**（缺件 fail-closed · 补 2 分支） | `test_inv04_require_wiring_fail_closed_all_parts` | **参数化**四件（`session`/`scope`/`guard`/`governance`）逐项置 `None`；断言各自抛 `CYC-999` 且 **Provider 零调用**、**无 `tool.result`**（复用 `tests/unit/test_tools_executor.py` 的夹具体例） |
| **T-2** | **A5**（无绕过路径 · 静态） | `test_inv04_provider_invocation_surface_is_executor_only` | **AST 全库扫描** `pyharness/**`：`.handle(` 调用点 ⊆ `{core/tools_executor.py}`（**白名单显式声明理由**，并允许 `tool_skill.py` 的**内层委派**为登记例外——须逐条写清）；沿用 S6-2b-1 的"白名单 + 双向校验 + `SyntaxError` 响亮失败"体例 |
| **T-3** | **G-1**（`call_id` 配对一致性） | `test_inv04_guard_evaluated_and_tool_result_share_call_id` | 真实 executor + 真实/替身 session；成功路径断言 **`guard.evaluated.trace["call_id"] == tool.result.payload["call_id"]`**（**逐字段**，而非仅事件序）；**并**在 reject 路径断言 `guard.rejected.trace["call_id"]` 同值 |
| **T-4** | **G-2**（挂载恒链尾） | `test_inv04_plugin_guard_appends_to_tail_only` | `register_plugin_guard` 后断言 `chain[-1].id == new_guard.id` 且**既有 id 序列**逐项不变（前缀恒等），并断言 `chain_version()` 单调 +1 |

**不新增（只建映射）**：A1/A2/A3（既有事件序与计数断言已充分）· B1（`test_decision_tri_value_exact_no_bypass`）· B2/B4/B5 的 API 面（`test_forbidden_bypass_api_attribute_error`）· B3（`test_register_after_reject_cannot_rescue`）· B6（`test_disable_forced_guards_cfg601`）—— **5 条误标 INV-03 者仅作映射，待 FC-8 修正编号**。

---

## 10. Mutation 设计（Phase E · **只设计，不执行**）

| # | Mutation（最小） | 目标测试 | 预期 | 安全性/代价 |
|---|---|---|---|---|
| **M-A1** | `_evaluate_full` 在 **allow 出口漏发** `_audit("guard.evaluated")`（删 `:724` 的调用） | 既有 `:289` 序断言 · **T-3** | **RED**（序缺一条） | ✅ 安全，1 行 |
| **M-A2** | `_evaluate_full` 在**短路出口多发一条** `_audit`（`_audit` 调两次） | 既有 `test_evaluate_allow_single_evaluated_event` · `:927`/`:1037` · **T-3** | **RED**（"恰一条"被破） | ✅ 安全，1 行 |
| **M-A3** | `_audit` 把 `trace={"call_id": ""}`（**抹掉配对键**） | **T-3**（专为目标） | **RED**（配对不一致） | ✅ 安全，1 行；**这是 T-3 的专属 mutation** |
| **M-A4** | `_require_wiring` 删去 **`ctx.session` 分支** | **T-1**（专为目标） | **RED**（缺 session 不再 fail-closed） | ✅ 安全，1 行 |
| **M-A5** | 在某模块新增 `ctx.tools.lookup_provider(name).handle(args, ctx)` 直接调用（**模拟绕过**） | **T-2**（专为目标） | **RED**（扫描器报出未登记的 `.handle(`） | ✅ 安全（临时文件，**用后即删**）；**须确认不留残留** |
| **M-B1** | `_DECISIONS` 加入第四值（如 `"bypass"`） | `test_decision_tri_value_exact_no_bypass` | **RED** | ✅ 安全，1 行 |
| **M-B2** | `_FORBIDDEN_API` **移除** `"bypass"` 一项 | `test_forbidden_bypass_api_attribute_error` | **RED**（`hasattr` 由假变真） | ✅ 安全，1 行 |
| **M-B3** | `register_plugin_guard` 改为 `self.chain.insert(0, guard)`（**插队**而非链尾） | **T-4** | **RED**（链尾断言失败；**既有用例可能仍绿** ⇒ 正是 T-4 的鉴别力） | ✅ 安全，1 行 |

**说明**：
- **M-A3 是 T-3 的专属 mutation**（唯一能专门触发"`call_id` 配对错误"分支）；
- **M-A4 / M-A5 分别是 T-1 / T-2 的专属 mutation**；
- **M-B3 是 T-4 的专属 mutation**，且预期**既有测试仍绿** —— 这将直接证明 T-4 填补了真实缺口（与 S6-2b-2 的 M-2 同款论证方式）；
- 全部为 **1 行 / 临时文件**级，**安全性高、易还原**；**本轮不执行**，留待实施阶段。

---

## 11. 是否发现真实生产问题

| 项 | 判定 |
|---|---|
| **F-1（`tool.error` 的范围不一致）** | ⚠️ **非缺陷** —— 无非法执行、Provider 零调用、审计留痕正常；属**定义-实现范围不一致**，需人工裁定口径（**建议**：明确 INV-04(a) 的"执行前"仅指执行路径，并**经授权**在 Registry 中写清 `tool.error` 的适用范围） |
| **A5 无静态守卫** | ⚠️ **非缺陷** —— 当前**结构上无绕过路径**（AST 实证），但**无守卫**；属**覆盖缺口**（G-4） |
| **其余实现审计项** | ✅ **无问题** —— `guard.evaluated` 单一发射点（三条出口各一次）· `GuardChain` 无第二写路径 · `__getattr__` 防线完整 · waterfall 短路 · 挂载恒链尾 · Provider 执行面唯一 |
| **结论** | **未发现真实生产缺陷** ⇒ **无新增 KEY-FINDING**（F-1 建议登记为 scope 说明项） |

---

## 12. 对后续 S6-2b-3 实施的建议

| # | 建议 |
|---|---|
| **1** | 实施阶段**只新增 §9 的 T-1~T-4**，落 `tests/invariants/test_inv_core.py`；**不重复**已 covered 的 8 条子性质 |
| **2** | **先裁定 F-1 的口径** —— 否则 `INV-04(a)` 的用例"断言范围"无法确定（**这是本阶段唯一的实质前置**；建议采纳口径 (i)，即"执行前"仅指 `tool.result` 路径） |
| **3** | mutation 阶段执行 **M-A3（T-3）· M-A4（T-1）· M-A5（T-2）· M-B3（T-4）** 四条专属 mutation，并对 **M-B3 额外核验"既有测试仍绿"**（证明 T-4 填补真实缺口） |
| **4** | **FC-8**（5 条误标 INV-03 → INV-04）建议**随本阶段一并授权修正**，否则 INV-04 的覆盖读数持续失真 |
| **5** | 复用本阶段已验证的体例：**AST 白名单 + 双向校验 + `SyntaxError` 响亮失败**（T-2）· **参数化缺件矩阵**（T-1） |

---

## 13. 边界声明

**做了**：只读读取 Registry 的 INV-04 定义并拆解 11 条子性质 · 读实现（`tools_guard` 的 `_evaluate_full`/`_audit`/`_append_rejected`/`register_plugin_guard`/`disable`/`__getattr__` · `tools_executor` 的四关与 `_require_wiring` · `governance.context.authorize` · `governance.audit` 哨兵）· **AST 全库扫描 Provider 调用面** · **只读探针实证** 关1a/关1b 的事件序列 · 盘点既有测试（含误标）· 覆盖分类（8 covered / 2 partial / 0 gap / 1 deferred + F-1）· 最小测试设计（4 条）· mutation 设计（8 条）。

**没做**（本阶段明令禁止）：❌ 创建测试 · ❌ 修改生产代码 · ❌ 修改 Registry / Event Schema / Governance Core · ❌ 修复 F-1 / KF-B / L-3 / F042 · ❌ 进入 INV-05 / S6-3 / S7 · ❌ 执行 mutation · ❌ commit · ❌ push。

**产出**：本文件（`S6-2b-3_COVERAGE_AUDIT.md`）唯一。

**worktree**：仅 `?? S6-2b-3_COVERAGE_AUDIT.md`；**HEAD 仍 `c44c9ce`**。

---

**S6-2b-3 Phase A 结束。等待人工裁定 F-1 口径与实施授权；未创建测试、未改代码、未 commit、未 push。**
