# S3-2-1_CHANGE_REPORT.md — 治理授权边界(Rich Evaluation + authorize)实施报告

> **阶段**：S3-2-1（S3 第二段第一子步:求值面 + 治理编排面 + 数据面;**不接 executor**）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`63406b4`（S3-1 checkpoint;worktree clean）
> **依据**：[S3-1_CHANGE_REPORT.md](S3-1_CHANGE_REPORT.md) · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) ADR-013/018 · S3-2-1 设计（B1~B5 裁定）
> **性质**：S3-2-1 实施。**未进入 S3-2-2**、未接 executor、未注册事件、**未修 F-SYNC-1**。

---

## 0. 结论

# S3-2-1 = COMPLETE / PASS

**改动 6 文件（+132 / −26）+ 新增 1 个测试文件（315 行）**；S3-2-1 专项 **19 passed**；全量回归 **1528 passed / 2 skipped / 0 failed**（较 S3-1 基线 1509 **恰 +19，零回归**）。

---

## 1. Objective / Scope

**目标**：打通"求值 → 结构化结果 → 治理装配 → Decision"的**本地链路**,并建立治理层的**唯一授权入口** `GovernanceContext.authorize()`。

**范围**：B1 rich evaluation（`_evaluate_full` / `evaluate` / `evaluate_detailed`）· B4 `authorize()` · `supersedes` 字段 · `DecisionEngine` 装配 · 并发隔离 · 等价性与结构守卫测试。

**明确不做（属 S3-2-2）**：executor 两出口切换 · `decision.issued` 注册与发射 · `DecisionIssuedPayload` · `ctx.guard.evaluate` 生产调用移除。

---

## 2. 基线

| 项 | 值 |
|---|---|
| S3-1 checkpoint | `63406b4 feat: establish governance decision model` |
| S3-1 baseline | **1509 passed / 2 skipped / 0 failed** |

---

## 3. 修改文件与增删（实测 `git diff --numstat`）

| 文件 | 增/删 | 内容 |
|---|---|---|
| `pyharness/core/tools_guard.py` | **+33 / −6** | `_evaluate_full()` 提取为唯一 waterfall；`evaluate()` 退化为兼容 wrapper；新增 `evaluate_detailed()` |
| `pyharness/governance/context.py` | **+59 / −11** | 实现 `authorize()`；`decisions` 字段类型化；R1 所有权文档 |
| `pyharness/governance/decision.py` | **+23 / −2** | 新增 `supersedes`；`decide()` 由 `prior` 派生；决策语义所有权表 |
| `pyharness/engine.py` | **+3 / −2** | 装配 `DecisionEngine()` 注入 `GovernanceContext` |
| `pyharness/governance/__init__.py` | **+4 / −3** | 导出 `DecisionEngine` |
| `tests/unit/test_governance_policy.py` | **+10 / −2** | T9 阶段断言更新（见 Δ-5） |
| `tests/unit/test_governance_authorize.py` | **新增 315 行** | 19 用例（T1~T17） |

**合计**：6 修改 + 1 新增；**+132 / −26**（不含新文件）。

---

## 4. B1 — Rich Evaluation

| 构件 | 说明 |
|---|---|
| `_evaluate_full(call, scope)` | **唯一实际 waterfall 实现**。返回 `(Decision, guard_ids, policy_refs)` |
| `evaluate(call, scope) -> Decision` | **兼容 wrapper**：`return (await self._evaluate_full(...))[0]`。签名/返回类型/求值序/短路/降级闸/事件发射次数**逐行不变** |
| `evaluate_detailed(call, scope) -> EvaluationResult` | rich API：同一次求值的结构化投影 |
| `EvaluationResult` | `verdict + guard_ids + policy_refs`,三者来自**同一调用现场** |

**结构性事实（实测）**：`_evaluate_full` 含 **1 个**循环；`evaluate` / `evaluate_detailed` 各含 **0 个**循环、**0 次** `_audit`/`_append_rejected`，且均调用 `_evaluate_full`。**无实例缓存**（无 `_last_eval*` 属性）。不读历史事件。

**并发隔离**：T15（5 轮并发 `evaluate_detailed`，critical×allow 交替）与 T16（并发 `authorize`）通过——无"A 的 verdict + B 的 refs"交叉污染。

---

## 5. B2 — 单一求值真源

- `GuardChain` 仍是 **g1–g7 waterfall / 求值序 / 短路 / 降级语义 / `guard_ids` / `policy_refs` / 规则级事件**的唯一所有者。
- `DecisionEngine` **不重实现 g1–g7**，不做 I/O（AST 实测 `awaits = 0`，无 `append`/`emit`/`write`/`session`/`bus`/`chain`/`request` 引用）。
- 运行时关系冻结为：`GuardChain → EvaluationResult → DecisionEngine → Decision`。

---

## 6. B3 — 一次求值一个 Decision

- **一次真实 `evaluate` 求值 ↔ 一个 `Decision`**；一次 `authorize()` 调用恰好一次 `evaluate_detailed` + 一次 `decide`。
- 同一 `call_id` **可**产生多个 `Decision`（审批重入 D001 → D002 合法）。
- `prior` / `supersedes`：`prior=None → supersedes=None`；`prior=D001 → D002.supersedes == D001.decision_id`。**不生成 attempt counter**。
- `DecisionEngine` **不用 `prior` 重新求值 g1–g7**（`decide()` 无链引用）。

---

## 7. B4 — `GovernanceContext.authorize()`

| 项 | 状态 |
|---|---|
| `authorize()` 已建立 | ✅ `governance/context.py` |
| 调用链 | `authorize()` → `GuardChain.evaluate_detailed()` → `EvaluationResult` → `DecisionEngine.decide()` → `Decision`（各一次） |
| **executor 是否已切换** | ❌ **未切换**（`tools_executor.py` **零改动**） |
| **当前生产路径** | 仍为 `Executor → ctx.guard.evaluate()`（`tools_executor.py:449` / `:563` 未变） |
| 本阶段是否发 `decision.issued` | ❌ 不发 |
| fail-closed | `decisions` / `chain` 为 None → `CYC-999`，绝不返回"看似授权"的决策 |

> **唯一生产治理入口属 S3-2-2**；本阶段**不声称**"生产已实现唯一入口"。

---

## 8. B5 — F-SYNC-1

- **未修**（`persistence.py` / `session.py` / `bus/event_bus.py` 零改动）。
- 所有"已落盘"类表述（含 `decision.issued` 的持久化）**限定正常持久化路径**；**不声明**磁盘故障下 fail-closed；未编写掩盖性测试。
- **S4 Entry Gate 前**必须关闭 F-SYNC-1 或取得明确架构豁免。

---

## 9. R1 — 决策语义所有权

生产 `authorize()` 调用 `decide()` 的关键字实参 = `approval_ref` · `call` · `inputs_digest` · `policy` · `principal` · `prior` ——**不含 `approval_available` / `approval_verdict`**（由 AST 测试 T11 钉死）。

| 原始条件 | 唯一所有者 |
|---|---|
| critical → reject | `GuardChain` |
| high + 无通道 → reject | `approval.py` APR-501 → executor |
| denied / timeout → 不执行 | executor |

二者参数保留在 `decide()` 签名中仅为**稳定 S3-1 Decision Contract 与兼容面**，在生产路径**不触发**。

---

## 10. Δ-1～Δ-7 最终状态

| Δ | 内容 | 状态 |
|---|---|---|
| **Δ-1** | `evaluate_detailed()` 用**函数内延迟 import** `EvaluationResult`（core 模块级 import 图不变） | ACCEPTED |
| **Δ-2** | `supersedes` 由 `decide()` 从 `prior` 派生 | ACCEPTED |
| **Δ-3** | `authorize()` 用 `PolicyEngine.current(scope)` 取 Policy 对象（`.fingerprint` 为 str），无需改 `decide()` 取值逻辑 | ACCEPTED |
| **Δ-4** | refs 映射：scope-hidden → `("scope-hidden",)/("GRD-401",)`；allow → `tuple(enabled)/()`；reject/approval → `(g.id,)/(policy,)` | ACCEPTED（逐值可追溯，见 §12） |
| **Δ-5** | 更新 S2-1 阶段断言 `test_t9_governance_context_m2_shape`：`not hasattr(authorize)` → `callable(authorize)` + 7 项禁止 API 断言 | ACCEPTED（**边界增强，非放宽**） |
| **Δ-6** | `governance/__init__.py` 追加导出 `DecisionEngine` | ACCEPTED |
| **Δ-7** | `engine.py` 每 spine 构造一个 `DecisionEngine()`（单实例、无共享状态） | ACCEPTED |

---

## 11. 测试覆盖（T1~T17）

| # | 用例 | 断言要点 |
|---|---|---|
| T1 | `evaluate` / `evaluate_detailed` 一致（参数化 ×3） | 逐例 verdict 相同；`evaluate()` 返回 `tools_guard.Decision` |
| T2 | refs 同源（scope-hidden） | `("scope-hidden",)/("GRD-401",)`；事件序与强同步位 |
| T3 | 真实规则命中 refs | `("g-danger",)/("POL-DGR-1",)` |
| T4 | allow 路径 | `guard_ids == tuple(enabled_guard_ids())`；`policy_refs == ()` |
| T5 | 自定义 chain 精确 refs | `("g-test",)/("POL-TEST-1",)` |
| T6 | **单份 waterfall 结构守卫** | wrapper 无循环、无审计、必调 `_evaluate_full` |
| T7 | 无实例缓存守卫 | `_last_eval*` 零命中 |
| T8 | `authorize()` 端到端 allow | 类型/指纹/supersedes/principal；**不发 `decision.issued`** |
| T9 | `authorize()` reject 语义 | critical→reject 由 GuardChain 拥有 |
| T10 | fail-closed | `decisions`/`chain` 未接线 → 抛错 |
| T11 | **R1 传参守卫** | `decide()` 无 `approval_available`/`approval_verdict` |
| T12 | `context.py` 依赖边界 | 无 core/engine/bus/persistence import |
| T13 | `supersedes` 关联 | `prior=D001 → D002.supersedes == D001.decision_id`；两者独立；无 attempt |
| T14 | `supersedes` 默认与 hash | 默认 None；eq/hash 契约成立 |
| T15 | **并发求值隔离**（5 轮） | 无交叉污染 |
| T16 | **并发 `authorize` 隔离** | 各得其所 |
| T17 | `EvaluationResult` 形状 | 契约稳定 |

---

## 12. Δ-4 refs 的代码依据（逐值追溯）

| 值 | 依据 |
|---|---|
| `("scope-hidden",)` / `("GRD-401",)` | `tools_guard.py:701`，与 `_audit(..., "scope-hidden", "GRD-401")` 同一字面量 |
| `(g.id,)` | `:723`，`g` 为当前循环 guard（同值传 `_audit` / `_append_rejected`） |
| `(policy,)` | `:723`，`g.check` 的返回值（降级时为 `POL-DGR-1`/`APR-501`），与审计同值 |
| `tuple(enabled)` | `:725`，同 `_audit(call, ALLOW, enabled, None)` |
| `()`（allow 的 refs） | `:724` 传 `None` 的忠实投影 |

**无占位值、无虚构 ref。** 附注（既有语义，非本阶段引入）：allow 路径的 `guard_ids` = 全链 enabled（"谁参与求值"），非"命中面"——与 `guard.evaluated` 载荷一致。

---

## 13. 测试结果

| 项 | 结果 |
|---|---|
| S3-2-1 专项 `test_governance_authorize.py` | **19 passed** |
| 相邻面（`test_tools_guard.py` / `test_engine.py` / `test_governance_decision.py`） | 全绿 |
| 全量回归 | **1528 passed / 2 skipped / 0 failed** |
| 相对 S3-1 基线 1509 | **+19**（零回归） |

*2 条 skip = 既有平台相关（`test_spill`）。未执行面:`desktop_native/**`、`test_shell_parity.py`（缺 `PySide6`，基线遗留 R-7）。*

---

## 14. 核验结果

| # | 检查 | 结果 |
|---|---|---|
| 1 | `_evaluate_full` 唯一 waterfall | ✅ loops=1；两 wrapper loops=0 且均调用它 |
| 2 | `authorize()` 无重复求值/decide | ✅ 生产调用点仅 `context.py:85`（evaluate_detailed）与 `:87`（decide）各一次 |
| 3 | R1 传参 | ✅ 无 `approval_available`/`approval_verdict` |
| 4 | AST 依赖边界 | ✅ `governance/{context,decision,policy,__init__}.py` 无 core/engine/bus/persistence |
| 5 | 并发 / 重入 | ✅ T15 · T16 |
| 6 | 白名单 | ✅ 7 文件，无额外；禁止面零命中 |
| 7 | `decision.issued` | ✅ **未注册**（`is_registered=False`） |
| 8 | receipt / evidence / audit | ✅ **未创建** |

---

## 15. S3-2-2 尚未实现（明确边界）

| 项 | 状态 |
|---|---|
| `tools_executor.py` 两出口切换 | **未做** |
| `decision.issued` 注册（vocab / SYNC_TYPES） | **未做** |
| `DecisionIssuedPayload` | **未创建** |
| `decision.issued` 事件发射 | **未做** |
| `ctx.guard.evaluate` 生产调用移除（单入口） | **未做**（唯一入口目标属 S3-2-2） |

## 16. 明确未修改

`pyharness/persistence.py` · `pyharness/core/session.py` · `pyharness/bus/event_bus.py` · `pyharness/core/approval.py` · `pyharness/core/scope.py` · `pyharness/core/tools_executor.py` · `pyharness/events/*` · 任意 `ADR-*` · `REFACTOR_PLAN.md` · `ARCHITECTURE_DECISION_RECORD.md`。**F-SYNC-1 未修。**

## 17. 明确未创建

`pyharness/governance/receipt.py` · `evidence.py` · `audit.py` —— 均**不存在**。

---

## 18. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 未修改 `tools_executor.py` | ✅ |
| 未注册 `decision.issued` / 未创建 payload | ✅ |
| 未修改 persistence / session / event_bus | ✅ |
| 未修 F-SYNC-1 | ✅ |
| 未创建 receipt / evidence / audit | ✅ |
| 未进入 S3-2-2 | ✅ |

**本步产物**：`pyharness/core/tools_guard.py` · `pyharness/governance/context.py` · `pyharness/governance/decision.py` · `pyharness/engine.py` · `pyharness/governance/__init__.py` · `tests/unit/test_governance_policy.py` · `tests/unit/test_governance_authorize.py` · 本报告。

---

## 19. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S3-2-2** | `decision.issued` 注册 + `DecisionIssuedPayload` + executor 两出口切换至 `authorize()` + 事件发射 + 单入口 AST 守卫 |
| （另立） | F-SYNC-1 durability 阶段（**S4 Entry Gate 前必须关闭或豁免**） |

**等待人工 checkpoint。**
