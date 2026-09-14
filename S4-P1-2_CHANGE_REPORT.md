# S4-P1-2_CHANGE_REPORT.md — `Decision.approval_ref` 接入实施报告

> **阶段**：S4-P1-2（S4 Entry Review 的 P1-2：把决策关联到真实 approval identity）
> **日期**：2026-09-15 ｜ **基线 HEAD**：`7daaab2`（S4-P1-1 checkpoint;worktree clean）
> **依据**：S4 Entry Review 的裁定 **(1) 降级 INV-APPROVAL-REF** · 冻结设计 `GOVERNED_AGENT_RUNTIME_DESIGN.md:375`（`approval_ref: int | None = None  # approval_id（= approval.requested 的 seq）`）
> **性质**：**未 commit**。**不进入 P1-3**、未处理 M5 / receipt·evidence·audit、**未修改 F-SYNC-1 / inputs_digest / Decision 契约 / GuardChain**。

---

## 0. 结论

# S4-P1-2 = PASS

**改动 3 文件（+216 / −2）**；全量回归 **1566 passed / 2 skipped / 0 failed**（较基线 1558 **+8**，零回归）。

---

## 1. Entry Analysis 结论

| 问题 | 实测结论 |
|---|---|
| `approval.requested` 生成处 | `approval.py:285-286`（`sess.append(..., sync=True)`） |
| 其 `seq` 产生处 | `session.append` 分配并返回 `env.seq` |
| `approval_id` 设置处 | `approval.py:289` —— `req.approval_id = env.seq` ⇒ **`approval_id` ≡ `approval.requested` 的 seq** |
| `ApprovalOutcomePayload.approval_id` | 由 `_prepare_outcome` 写入，值 = 同一 seq |
| 是否已持久化 / replay 可恢复 | ✅ 事件在 JSONL；outcome 载荷携带 `approval_id` |
| `request()` 返回 | **仅 verdict 字符串**，不带 approval_id |
| 内部是否保存 request id | ✅ `req.approval_id`（**仅 lead**）；merged waiter 的为 `None`（共享 lead 的批） |
| `grant_binding()` 保存什么 | **绑定指纹**（不是 approval_id） |
| 结算后是否可查 | ❌ `_settle` 会 `_pending.pop(approval_id)`（`:491-492`） |
| 最小 seam 判定 | **B**（需最小内部 state + 只读访问器）；**A 不成立**（无现成 `call_id → approval_id` 路径） |

### 决定性时序（实测，真 `ApprovalProvider`）

```
seq 1  tool.call
seq 2  guard.evaluated
seq 3  decision.issued      ← D1  verdict=approval  approval_ref=None
seq 4  approval.requested   ← approval_id = 4   ★ 此刻才产生
seq 5  queue.suspended
seq 6  approval.granted     ← approval_id = 4
seq 7  queue.resumed
seq 8  guard.evaluated
seq 9  decision.issued      ← D2  approval_ref=4  supersedes=D1.decision_id
seq 10 tool.result
```

⇒ **D1 产生于 approval identity 存在之前** ⇒ `approval_ref=None` 为**正确语义**（裁定 (1)）。

## 2. approval identity 的真实来源

**唯一来源 = `approval.requested` 事件的 seq**（`req.approval_id = env.seq`）。
**未新造 identity**：未引入 uuid、未用 `decision_id`/`call_id`/verdict 字符串代替。

## 3. `approval_ref` 数据流

```
approval.requested (seq=N)
        │  req.approval_id = N
        ▼
_settle(): self._ref_slots[call_id] = N        ← S4-P1-2 新增(结算时记录)
        │
        ▼
ApprovalProvider.approval_ref_of(call_id) -> N  ← 只读访问器
        │  ap.request() 返回后由 executor 读取
        ▼
ToolExecutor._approval_round: authorization(prior=D1, approval_ref=N)
        │
        ▼
DecisionEngine.decide(..., approval_ref=N)      ← 纯计算(既有参数)
        │
        ▼
Decision.approval_ref = N   →   decision.issued.payload["approval_ref"] = N
```

D1 路径不经过该链（`approval_ref=None`）。

## 4. 修改文件与理由

| 文件 | 增/删 | 为什么 |
|---|---|---|
| `pyharness/core/approval.py` | **+22 / −0** | ① `_ref_slots: dict[str, int]` 记录 `call_id → approval_id`；② `_settle` 在结算时写入（含 merged waiter → 共享 lead identity）；③ 新增**只读**访问器 `approval_ref_of(call_id)` |
| `pyharness/core/tools_executor.py` | **+8 / −2** | `_approval_round` 在 `ap.request()` 返回后读取 ref，并在**重入** `authorize(prior=D1, approval_ref=…)` 时传入（→ D2 带 ref） |
| `tests/unit/test_tools_executor.py` | **+186 / −0** | `TestApprovalRef` T1–T8（真实运行路径 + 真实 JSONL replay） |

## 5. 新增 / 修改 API

| API | 类型 | 说明 |
|---|---|---|
| `ApprovalProvider.approval_ref_of(call_id) -> int \| None` | **新增（只读）** | 返回该 call_id 关联的 approval identity（= `approval.requested` 的 seq）；结算后仍可查；`None` = 从未产生 approval 请求 |
| `ApprovalProvider._ref_slots` | **新增内部 state** | `call_id → approval_id`；与既有 `_grant_slots` 同型生命周期（call_id 每次调用唯一） |
| `_approval_round(..., binding=)` | 修改（P1-1 已引入） | 本步追加读取 ref 并在重入时传 `approval_ref` |

**无第二套 approval identity**；未修改 `request()` 的返回契约（仍返回 verdict 字符串）。

## 6. 为什么这是最小 seam

- **A 不成立**：结算后 lead 被 `_pending.pop` 摘除，且不存在 `call_id → approval_id` 映射 ⇒ 无现成只读路径。
- **B（本实现）**：仅在 `_settle`（**唯一终态迁移单点**）追加一行记录 + 一个只读 getter；不新增事件、不改审批流程、不改 `request()` 契约。
- **C 不适用**：D1 的 `approval_ref=None` 经裁定为**正确语义**，无需改时序。

## 7. D1 / D2 / granted / denied / timeout 关系（真实 runtime）

| 对象 | verdict | approval_ref | 说明 |
|---|---|---|---|
| **D1** | `APPROVAL` | **`None`** | 审批**请求**决策，产生于 `approval.requested` 之前 |
| `approval.requested` | — | — | identity = 其 seq |
| `approval.granted/denied/timeout` | — | — | 载荷 `approval_id` = 同一 seq |
| **D2**（granted 后重入） | **`APPROVAL`**（真实 runtime，非 ALLOW） | **= approval_id** | `supersedes == D1.decision_id`；绑定校验通过 → 执行 |
| denied / timeout | — | 无 D2 | 零 provider 执行 |

**关于 D2 = APPROVAL**（按裁定第五项核实）：重入对**同参**重新求值，`g-danger` 对 `danger=high` 恒判 `approval`；随后由**绑定指纹校验**（本调用指纹 == 审批记录的绑定）放行执行。这是既有 **"偏离 3"** 语义（见 `_approval_round` docstring），**属当前设计预期**，未做改动。

## 8. Replay 关系

从 JSONL 重放后可完整恢复：

```
D1 (approval_ref=None)
        ↓ approval.requested (seq=N)
        ↓ approval.granted (approval_id=N)
        ↓ D2 (approval_ref=N, supersedes=D1.decision_id)
```

经 `env.seq` 与载荷 `approval_id` / `approval_ref` / `supersedes` 关联，**不依赖任何内存状态**。T7 以**真实 `SessionStore` JSONL** 关闭重开验证。

## 9. 测试覆盖

| # | 用例 | 断言 |
|---|---|---|
| **T1** | `test_t1_d1_is_approval_with_none_ref` | D1 `verdict=approval` 且 `approval_ref is None`（**正确语义**） |
| **T2** | `test_t2_approval_id_is_requested_seq` | `approval_ref_of(call_id) == approval.requested.seq` |
| **T3** | `test_t3_granted_carries_same_approval_id` | `approval.granted.approval_id == requested.seq` |
| **T4** | `test_t4_d2_binds_ref_and_supersedes` | `D2.approval_ref == granted.approval_id == N`；`D2.supersedes == D1.decision_id`；`D2.verdict=approval`（真实）；`inputs_digest` 仍非空且与 D1 同源；provider 执行 1 次 |
| **T5** | `test_t5_denied` | D1 `approval_ref=None`；`approval.denied.approval_id == requested.seq`；零执行 |
| **T6** | `test_t6_timeout` | D1 `approval_ref=None`；`approval.timeout.approval_id == requested.seq`；零执行 |
| **T7** | `test_t7_chain_recoverable_from_jsonl` | 真实 JSONL 落盘 → 关闭 → 重开 replay：四条记录关联可恢复 |
| **T8** | `test_t8_non_approval_paths_have_none_ref` | 普通 ALLOW / REJECT：`approval_ref is None` 且**无** `approval.requested` 事件 |

## 10. 测试结果

| 项 | 结果 |
|---|---|
| P1-2 专项 `TestApprovalRef` | **8 passed** |
| **全量回归** | **1566 passed / 2 skipped / 0 failed** |
| 相对基线 1558 | **+8** |

## 11. 架构边界检查

| 约束 | 状态 |
|---|---|
| 不修改 `Decision` 契约 | ✅ `decision.py` 零改动（`approval_ref` 为既有字段） |
| `DecisionEngine` 仍纯计算、无 I/O | ✅ 零改动；`approval_ref` 仍是**入参** |
| governance 不 import executor | ✅ `governance/*` 零改动 |
| executor 不重新实现 approval 逻辑 | ✅ 只经只读 getter 取值 |
| 不创建第二套 approval identity | ✅ identity 恒来自 `approval.requested` 的 seq |
| 不创建第二套 persistence source of truth | ✅ 未触及 persistence |
| `approval.requested.seq` 既有语义 | ✅ 未变 |
| replay 后 identity 可解释 | ✅ T7 |
| `D1 → D2 supersedes` 未破坏 | ✅ T4 |
| F-SYNC-1 未修改 | ✅ `persistence.py` 零改动 |
| `inputs_digest` 未修改 | ✅ 算法与 P1-1 一致（T4 断言同源非空） |
| M5 / receipt / evidence / audit | ✅ 均未处理 |
| D1 时序未改 / 未补发第二条 `decision.issued` | ✅ 一求值一决策一事件保持 |

**冻结 ADR 无需修改**：冻结设计 `GOVERNED_AGENT_RUNTIME_DESIGN.md:375` **已定义** `approval_ref = approval_id(= approval.requested 的 seq)`，本实现与该定义**完全一致**；`ARCHITECTURE_DECISION_RECORD.md` / ADR-0xx **未定义** `approval_ref` 语义 ⇒ **无冲突、无需追加**。

## 12. 新增 deferred debt

**无。**

---

**本步产物**：`pyharness/core/approval.py` · `pyharness/core/tools_executor.py` · `tests/unit/test_tools_executor.py` · 本报告。

---

## 13. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **P1-3** | M5 正式 identity（取代临时 SYSTEM principal） |
| **S4-M4** | Decision Receipt（依赖 P1-1/P1-2/M5） |

**等待人工 checkpoint。**
