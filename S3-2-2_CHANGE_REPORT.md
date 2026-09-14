# S3-2-2_CHANGE_REPORT.md — 治理决策接入 Executor 运行时实施报告

> **阶段**：S3-2-2（S3 第二段第二子步:Runtime 接线 + `decision.issued`）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`ccc58c3`（S3-2-1 checkpoint;worktree clean）
> **依据**：[S3-2-1_CHANGE_REPORT.md](S3-2-1_CHANGE_REPORT.md) · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) ADR-013/015/018 · S3-2-2 Entry Review 的 B1~B5 / W-1 / W-2 裁定
> **性质**：S3-2-2 实施。**未进入 S3-3**、未创建 receipt/evidence/audit、**未修 F-SYNC-1**。

---

## 0. 结论

# S3-2-2 = COMPLETE / PASS

**改动 8 文件（+422 / −61）**；全量回归 **1538 passed / 2 skipped / 0 failed**（较 S3-2-1 基线 1528 **恰 +10**，零回归）。

---

## 1. Objective

把已在 S3-1/S3-2-1 建立的 Decision 模型与 `GovernanceContext.authorize()` **接入真实 Executor 运行时**,并注册/发射治理证据事件 `decision.issued`——同时保持单一治理入口、单一 waterfall 真源、`DecisionEngine` 纯度,并明确继承 F-SYNC-1 风险。

## 2. 基线

| 项 | 值 |
|---|---|
| S3-2-1 checkpoint | `ccc58c3 feat: establish governance authorization boundary` |
| S3-2-1 baseline | **1528 passed / 2 skipped / 0 failed** |

## 3. Runtime wiring:目标 vs 实际

| 目标 | 实际结果 |
|---|---|
| `Executor → GovernanceContext.authorize()` 唯一入口 | ✅ 达成 |
| executor 内 `ctx.guard.evaluate` = 0 | ✅ 达成（生产调用点 0） |
| `decision.issued` 注册并发射 | ✅ 达成 |
| `DecisionEngine` 保持纯 | ✅ 未变（本阶段未改 `decision.py`） |
| GuardChain 唯一 waterfall | ✅ `_evaluate_full` 未改 |
| 一次求值 → 一个 Decision → 一条事件 | ✅ 达成 |

## 4. 出口统一:三个原出口 → 一个入口

改造前 executor 有**三个** verdict 出口:

| 原出口 | 位置 | 改造后 |
|---|---|---|
| ⓪ scope 前置（自算 `scope.can_use`） | `:446-447` → `_reject()` | **上提**至 `GuardChain._evaluate_full` 的 scope 分支 |
| A 关 2b guard 链 | `:449` `str(await ctx.guard.evaluate(…))` | 并入 `authorize()` |
| B 关 2.5 审批重入 | `:563` 同型 | 并入 `authorize(prior=D1)` |

现统一为:

```
关2:  d = await ctx.governance.authorize(call, ctx)            # 唯一治理入口
      ├ REJECT   → ExecResult(拒绝)
      └ APPROVAL → _approval_round(prior=D1)
                     └ granted → ctx.governance.authorize(call, ctx, prior=D1)
```

**scope ownership**:唯一运行时所有者 = `GuardChain._evaluate_full`;executor 不再自算 `can_use`（实测 `can_use` 恰 **1** 次）。

## 5. `GovernanceContext.authorize()` 的最终职责

```python
authorize(call, ctx, *, principal=None, inputs_digest="", prior=None,
          approval_ref=None) -> Decision
```

职责:**编排**——`GuardChain.evaluate_detailed()` → `EvaluationResult` → `DecisionEngine.decide()` → `Decision` → `session.append("decision.issued", sync=True)`。

- **不**自行 `scope.can_use`（避免与链重复计算）;
- **不**向 `decide()` 传 `approval_available` / `approval_verdict`（R1）;
- 缺 `decisions` / `chain` → `CYC-999` fail-closed;
- **不是**第二套 rule engine。

## 6. `DecisionEngine` 纯度保持

本阶段**未修改** `pyharness/governance/decision.py`。其纯度约束（无 I/O / session / event / persistence / provider / approval request）继续由 S3-2-1 的 AST 守卫钉死。

## 7. GuardChain 唯一 waterfall

本阶段**未修改** `pyharness/core/tools_guard.py`。`_evaluate_full()` 仍是唯一实际 g1–g7 求值实现;`evaluate()` / `evaluate_detailed()` 均为其视图。`test_tools_guard.py`（全 g1–g7 用例）与 `test_engine.py` 全绿。

## 8. `decision.issued` 注册与 payload

**注册**:`events/payload.py` 新增 `DecisionIssuedPayload`;`events/vocab.py` 加入 import 名单与 `_CORE_EVENT_TYPES`;并入 `SYNC_TYPES`。

**payload（扁平 13 字段,`extra="forbid"`）**:

`decision_id` · `verdict` · `tool` · `guard_ids[]` · `policy_refs[]` · `policy_fingerprint` · `inputs_digest` · `principal_kind` · `principal_id` · `principal_channel` · `ts` · `approval_ref` · `supersedes`

- `verdict` 为 `str`（值域 `allow|reject|approval` 由治理层约束;**不使用** `deny` / `need_approval`）;
- `call_id` **走 `Envelope.trace`**,不入 payload;
- principal 为**扁平三字段**（不引入嵌套模型）。

## 9. 事件词表

`EVENT_TYPES = 75`（74→75）· `SYNC_TYPES = 13`（12→13）· `TRANSIENT_TYPES = 3`（未变）。
`SYNC_TYPES` 仍为**单一真源**（`vocab.py` 单一定义）,各适配器派生消费,无手工 sync tuple。

## 10. Decision → event 一一对应

`one evaluation → one Decision → one decision.issued`。

**生产侧完整性证据**:`authorize()` 的生产调用点仅 2 处（`tools_executor.py:471` / `:601`）,均在 `execute()` 内且位于 `_require_wiring(ctx)`（强制 `ctx.session is not None`）之后 ⇒ **不存在**"生产 Decision 无事件"的路径。

实测:allow / reject / scope-hidden / approval D1 / granted D2 各 **1** 条;denied/timeout 仅 D1 **1** 条。

## 11. approval D1/D2 + supersedes

实测事件序:

```
tool.call → guard.evaluated → decision.issued(D1) → approval.requested
→ queue.suspended → approval.granted → queue.resumed
→ guard.evaluated → decision.issued(D2) → tool.result
```

`D2.supersedes == D1.decision_id`;`decision_id` 互异;两者独立 Decision。

## 12. denied / timeout

`D1 = APPROVAL` 已产生 `decision.issued`;审批未获批 ⇒ **无 D2**、**不再次 authorize**、**Provider 零执行**、`guard.evaluated` 仅 1 条。该语义由 **approval transport + executor** 所有（R1）。

## 13. Principal（临时身份）

`PrincipalKind.SYSTEM` / `id="pyharness-runtime"` / `channel=None`（`context.py::_TEMPORARY_PRINCIPAL`）——**temporary S3-2-2 identity**。**不伪装** HUMAN / AGENT;approval 通道身份 ≠ 治理 principal;**未声称** M5 正式身份模型完成。

## 14. `approval_ref = None`（阶段限制 / S4 follow-up）

`approval.py` 公开 seam 仅暴露 `grant_binding(call_id)`,**无法取得** approval request seq ⇒ 本阶段一律 `None`。**未修改 `approval.py`**、**未新增** identity API、**未伪造** id。**记录为 S3-2-2 阶段限制 / S4 follow-up**（Approval Credential / approval_ref / Decision binding）。**不声称已实现 `approval_ref`**。

## 15. W-1 / W-2

| 项 | 结果 |
|---|---|
| **W-1** | `tests/unit/test_tool_fs.py` 纳入白名单（仅测试替身装配） |
| **W-2** | `_exec_ctx()` 改用**真实 `GuardChain(session=session)`**;`AllowGuard` 回退原状,仅用于不经 executor 的直调用例;**未复制**任何生产逻辑 |

## 16. Δ-1～Δ-10

| Δ | 内容 | 状态 |
|---|---|---|
| Δ-1 | `ApprovalRoundResult(decision/approved/executed/message)` 取代混合字符串返回契约 | ACCEPTED |
| Δ-2 | scope 前置上提至 `GuardChain._evaluate_full`（职责上提,非重复计算） | ACCEPTED |
| Δ-3 | `_emit_decision_issued` 无 session 时**静默降级** | ACCEPTED（hardening candidate,见 §17） |
| Δ-4 | 事件序新增 `decision.issued`（INV-G1 要求） | ACCEPTED |
| Δ-5 | `_require_wiring` 增 governance fail-closed | ACCEPTED |
| Δ-6 | `AllowGuard` 回退原状;`_exec_ctx` 用真实链（W-2） | ACCEPTED |
| Δ-7 | `test_events.py` 计数 74→75 / 12→13（B5 批准） | ACCEPTED |
| Δ-8 | `approval_ref=None` 阶段限制 | ACCEPTED（见 §14） |
| Δ-9 | `_reject()` 无生产调用点（**dead-code candidate**,按裁定保留） | ACCEPTED |
| Δ-10 | `tools_executor.py` 模块 docstring 漂移（`:37` 的 `d != "allow"`、`:52-54` 的 `_reject` summary 约定） | ACCEPTED（documentation debt,见 §18） |

## 17. Δ-3:future hardening candidate

生产 `authorize()` 仅由 Executor 可达,而 Executor 已由 `_require_wiring()` 强制要求 session,故当前不变量成立。

**登记**:`future hardening candidate: authorize() may enforce session locally`（把"生产 Decision 必有事件"由上游间接保证改为 `authorize()` 本地 fail-closed）。本阶段**不修改**。

## 18. Δ-10:documentation debt

`tools_executor.py` 模块 docstring 中:

- `:37` 仍写 `if d != "allow" → GRD-403 字面直译`（该字面比较已随字符串契约移除）;
- `:52-54` 仍描述 `_reject()` 的 `"guard 拒绝:…"` summary 前缀约定（`_reject` 现为 dead code）。

**纯文档,零行为影响**;本阶段**不修改**。

## 19. F-SYNC-1 继承风险

- **未修**;`persistence.py` / `core/session.py` / `bus/event_bus.py` **零修改**;
- `decision.issued` 走既有 `session.append → _dispatch → 适配器 → store.append(sync=…)` 路径;
- **正常持久化路径**可工作（有测试）;
- persistence failure 下**不保证 fail-closed**;`decision.issued` **不是故障安全**;
- **治理证据链尚不具备故障安全**;
- **S4 Entry Gate 前**必须关闭 F-SYNC-1 或取得正式架构豁免。

## 20. 测试结果

| 项 | 结果 |
|---|---|
| `test_tool_fs.py` | 44 passed |
| `test_tools_executor.py` | 50 passed（含 `TestGovernanceWiring` 6 例） |
| governance tests（authorize / decision / policy） | 全绿 |
| `test_events.py` | 全绿 |
| **全量回归** | **1538 passed / 2 skipped / 0 failed** |
| 相对 S3-2-1 基线 1528 | **+10** |

新增覆盖:scope-hidden 治理入口 · reject/allow 事件序 · 缺 governance fail-closed · executor 无直呼 guard.evaluate（AST） · D1/D2 关联与 supersedes · denied/timeout 无 D2 · payload 契约 · authorize 发射（有/无 session）。

*2 条 skip = 既有平台相关（`test_spill`）。未执行面:`desktop_native/**`、`test_shell_parity.py`（缺 `PySide6`,基线遗留 R-7）。*

## 21. S3-3 / Receipt / Evidence / Audit 尚未实现

| 项 | 状态 |
|---|---|
| `pyharness/governance/receipt.py` | **未创建** |
| `pyharness/governance/evidence.py` | **未创建** |
| `pyharness/governance/audit.py` | **未创建** |
| `receipt.emitted` / `evidence.archived` 注册 | **未做** |
| S3-3 | **未进入** |

## 22. F-SYNC-1 尚未修复

**未修复**（见 §19）。

## 23. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 未修改 `persistence.py` / `core/session.py` / `bus/event_bus.py` | ✅ |
| 未修改 `core/approval.py` / `core/scope.py` | ✅ |
| 未修改 `core/tools_guard.py` / `governance/decision.py` / `engine.py` | ✅ |
| 未修改 ADR / `REFACTOR_PLAN.md` / `ARCHITECTURE_DECISION_RECORD.md` | ✅ |
| 未修 F-SYNC-1 | ✅ |
| 未创建 receipt / evidence / audit | ✅ |
| 未进入 S3-3 | ✅ |

**本步产物**：`pyharness/core/tools_executor.py` · `pyharness/events/payload.py` · `pyharness/events/vocab.py` · `pyharness/governance/context.py` · `tests/unit/test_events.py` · `tests/unit/test_tools_executor.py` · `tests/unit/test_tool_fs.py` · `tests/unit/test_governance_authorize.py` · 本报告。

---

## 24. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S3-3** | Decision Receipt / Evidence / Audit（M4~M7）——**前置**:F-SYNC-1 关闭或豁免 |
| （另立） | F-SYNC-1 durability 阶段（含 T5 marker 红测试） |

**等待人工 checkpoint。**
