# S4-P1-1_CHANGE_REPORT.md — `Decision.inputs_digest` 接入实施报告

> **阶段**：S4-P1-1（S4 Entry Review 的 P1-1：补齐授权↔执行绑定摘要）
> **日期**：2026-09-15 ｜ **基线 HEAD**：`66946e1`（F-SYNC-1 checkpoint;worktree clean）
> **依据**：S4 Entry Review（`S4 ENTRY = CONDITIONAL PASS`，P1-1）· ADR-018「`inputs_digest` 算法 = 既有 `tools_executor._approval_binding`，复用而非重写」· 设计 §3.4
> **性质**：**未 commit**。**不进入 P1-2**、未处理 `approval_ref` / M5 / receipt·evidence·audit、**未修改 F-SYNC-1**。

---

## 0. 结论

# S4-P1-1 = PASS

**改动 2 文件（+111 / −5）**：`pyharness/core/tools_executor.py`（+15/−5）· `tests/unit/test_tools_executor.py`（+96/−0）。
全量回归 **1558 passed / 2 skipped / 0 failed**（较基线 1553 **+5**，零回归）。

---

## 1. 修改文件与职责

| 文件 | 增/删 | 职责 |
|---|---|---|
| `pyharness/core/tools_executor.py` | **+15 / −5** | 在关 2 计算一次绑定摘要并注入 `authorize(inputs_digest=…)`；`_approval_round` 改为接收该值（消除重复计算） |
| `tests/unit/test_tools_executor.py` | **+96 / −0** | `TestInputsDigestBinding` 5 例（真实运行路径） |

**仅改执行侧**：`governance/*` · `approval.py` · `persistence.py` · `events/*` · `session.py` · `event_bus.py` **零改动**。

## 2. 为什么这样修改（Entry Analysis 结论）

| 分析项 | 实测结论 |
|---|---|
| `_approval_binding` 定义位置 | `tools_executor.py:240`（唯一实现，无第二套） |
| 输入 | `(call, args)` —— 仅用 `call.name` + 已校验 `args` |
| 输出 | `sha1(f"{call.name}\n{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}")` 十六进制串 |
| 当前使用点 | **仅 1 处**：`_approval_round` 内（`binding = _approval_binding(call, args)`），用于与 `ap.grant_binding(call_id)` 比对 |
| 如何在不 import executor 的前提下复用 | **由执行侧计算并经既有 `authorize(inputs_digest=…)` 参数注入**——与 `chain_factory` 注入先例同型；治理层只**持有**该值 |
| 是否需提取共享模块 | **不需要**——两个使用点（关 2 与 `_approval_round`）**同在 `tools_executor.py` 内**，提取反而制造无谓依赖 |
| 重复计算风险 | **有**（关 2 与 `_approval_round` 各算一次）→ **已消除**：`execute()` 算一次并下传 |
| ALLOW / REJECT / APPROVAL 语义差异 | **无**——摘要仅依赖 `(tool, args)`，在 verdict 产生**之前**即可算；三种 verdict（含 scope-hidden REJECT）同源同值 |
| 是否需修改架构边界 | **否** —— 未新增模块、未改 `authorize` 契约、未改 `Decision` 字段、未引入第二真源 |

## 3. digest 的实际来源

**唯一来源 = 既有 `tools_executor._approval_binding`**（复用，未重写）。
未新增任何参数规范化 / 哈希逻辑；**不存在第二套 inputs digest 算法**。

## 4. `inputs_digest` 数据流

```
tool call inputs (call.name + 已校验 args)
        │
        ▼  execute() 关2 之前 —— 算一次
_approval_binding(call, args)          ← 既有算法(授权↔执行绑定)
        │  digest
        ├──────────────────────────────► ctx.governance.authorize(call, ctx, inputs_digest=digest)
        │                                        │
        │                                        ▼
        │                                 DecisionEngine.decide(..., inputs_digest=digest)   ← 纯计算
        │                                        │
        │                                        ▼
        │                                 Decision.inputs_digest
        │                                        │
        │                                        ▼
        │                                 decision.issued.payload["inputs_digest"]
        │
        └──────────────────────────────► _approval_round(..., binding=digest)
                                                 │
                                                 ▼
                                          ap.request(..., binding=digest)
                                                 │  与 ap.grant_binding(call_id) 比对
                                                 ▼
                                          granted 后重入 authorize(prior=D1, inputs_digest=digest)
```

**关键性质**：`decision.issued` 里的 `inputs_digest` **就是**审批侧记录的绑定值 ⇒ 参数被替换时二者不一致，**"批准 A 执行 B"可被识别**。

## 5. 新增测试（真实运行路径）

| # | 用例 | 断言 |
|---|---|---|
| 1 | `test_allow_path_digest_not_empty` | ALLOW 路径 `inputs_digest != ""`（**不再恒为空**） |
| 2 | `test_reject_path_digest_not_empty` | REJECT（critical→g-danger）路径同样非空 |
| 3 | `test_digest_deterministic_and_input_sensitive` | 同 inputs → 同 digest；不同 inputs → 不同 digest |
| 4 | `test_digest_equals_approval_recorded_binding` | **批准 A 执行 B 防线**：D1/D2 的 `inputs_digest` **== `ap.grant_binding(call_id)`**（与审批记录同源）；`D2.supersedes == D1.decision_id` 未被破坏 |
| 5 | `test_binding_mismatch_still_denies` | 既有绑定校验行为不变（不一致 → `GRD-403`，Provider 零执行） |

测试全部走**真实** `GuardChain` / 真实 `ApprovalProvider` / 真实事件面，**未 mock 内部计算**。

## 6. 测试结果

| 项 | 结果 |
|---|---|
| P1-1 专项 `TestInputsDigestBinding` | **5 passed** |
| `test_tools_executor.py` + `test_tool_fs.py` | 全绿 |
| **全量回归** | **1558 passed / 2 skipped / 0 failed** |
| 相对基线 1553 | **+5** |

## 7. 架构边界影响

| 边界 | 受影响？ |
|---|---|
| `DecisionEngine` 纯计算、无 I/O | ❌ 未受影响（`decision.py` 零改动；digest 仍为**入参**） |
| `GuardChain` 是 g1–g7 唯一真源 | ❌ 未受影响（`tools_guard.py` 零改动） |
| `GovernanceContext` 是唯一 authorization orchestration entry | ❌ 未受影响（`authorize` 契约未变，`inputs_digest` 本就是既有参数） |
| Executor 不重新实现治理逻辑 | ❌ 未受影响（executor 只**提供**已校验输入摘要，不做决策） |
| 不产生第二套 inputs digest 算法 | ✅ 复用同一函数 |
| 不引入第二套 persistence/source of truth | ✅ 未触及 persistence |
| 不修改 `Decision` 字段契约 | ✅ `decision.py` 零改动 |
| 不引入 DAG / checkpoint / pause | ✅ |

**结论：零架构边界变更。**

## 8. Deferred debt

**本轮未新增 deferred debt。**
`_approval_round` 的 `binding` 由可选改为**必填关键字参数**（消除重复计算），其唯一调用点在 `execute()` 内、已同步更新；无外部/测试直接调用（实测 grep 确认）。

---

## 9. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 未处理 `approval_ref` | ✅ |
| 未处理 M5 identity | ✅ |
| 未实现 receipt / evidence / audit | ✅ |
| 未修改 F-SYNC-1（`persistence.py` 零改动） | ✅ |
| 未修改 `governance/*` / `approval.py` / `events/*` / `session.py` / `event_bus.py` | ✅ |
| 未进入 P1-2 | ✅ |

**本步产物**：`pyharness/core/tools_executor.py` · `tests/unit/test_tools_executor.py` · 本报告。

---

## 10. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **P1-2** | `approval_ref` 只读 seam（触 `approval.py`，需单独授权） |
| **P1-3** | M5 正式 identity（与 M4 同批） |

**等待人工 checkpoint。**
