# S4-P1-3_CHANGE_REPORT.md — M5 运行时主体身份（Principal Identity）实施报告

> **阶段**：S4-P1-3（S4 Entry Review 的 P1-3：M5 正式 identity）
> **日期**：2026-09-15 ｜ **基线 HEAD**：`44979c4`（S4-P1-2 checkpoint;worktree clean）
> **依据**：S4 Entry Review 的 P1-3 · `ARCHITECTURE_DECISION_RECORD.md` §5.2（S4 = M4/M5）· `GOVERNED_AGENT_RUNTIME_DESIGN.md` §3.1（`Principal` / `from_legacy_by`）
> **性质**：**未 commit**。**不进入 Receipt**；未处理 receipt/evidence/audit、未改 `approval_ref` / `inputs_digest` / F-SYNC-1 / GuardChain / Decision 契约；未引入任何身份认证系统。

---

## 0. 结论

# S4-P1-3 = PASS

**改动 3 文件（+178 / −16）**；全量回归 **1575 passed / 2 skipped / 0 failed**（较基线 1566 **+9**，零回归）。

---

## 1. Entry Analysis

| 问题 | 实测结论 |
|---|---|
| `_TEMPORARY_PRINCIPAL` 定义位置 | `governance/context.py:41`（`Principal(SYSTEM, "pyharness-runtime", None)`） |
| `principal_of(ctx)` 现状 | **完全忽略 `ctx`**（`:60-66`），恒返回该常量 |
| production 为何只能得到 SYSTEM | 因为派生函数从未读取运行时的 caller/channel 信息 |
| 真实 caller identity 是否存在 | ✅ **已存在**：`ctx.channel`（由外壳**框架侧**写入） |
| 最小语义判定 | **A** —— identity 已存在，只需装配（经既有 `Principal.from_legacy_by`）；**无需新 seam（B）**，更非 C |

### 真实 caller identity 的代码证据

| 通道 | 赋值点 | 值 |
|---|---|---|
| CLI | `cli.py:764` | `ctx.channel = None if headless else "cli"`（R8：无通道即安全默认） |
| ACP | `acp.py:302` | `ctx.channel = f"acp:{st.client_id}"` —— 且 `acp.py:486` 明确**忽略客户端自报 `by`**（恒用通道身份） |
| Desktop | `application/service.py:78` · `registry.py:15` | `self.channel = str(channel or "desktop")` |

**关键验证（实测）**：`attach_engine_to_ctx(ctx, …)`（`cli.py:578` 传入的 ctx）在设置 `ctx.governance` / `ctx.tools` 的同时**保留 `ctx.channel`**：

```
attach 后 ctx.channel = 'cli' | ctx.governance set = True | ctx.tools set = True
```

⇒ 进入 `ToolExecutor.execute` 的 ctx **本已携带** `.channel` ⇒ **无需新增 seam**。

## 2. caller identity 的真实来源

**唯一来源 = `ctx.channel`**（框架侧按外壳装配写入，非客户端自报）。
**不从 tool 名 / verdict / approval 反推**；未新造身份。

## 3. 原 `_TEMPORARY_PRINCIPAL` 的问题

1. **恒等**：所有 Decision 的 `principal` 都是 `SYSTEM / pyharness-runtime`，无论真实 caller 是谁；
2. **无来源可述**：无法回答"这个 identity 从哪里来"；
3. **阻碍 M4/M6/M7**：Receipt 的定位是"谁在什么策略下做的决定"，恒等 SYSTEM 使该问题不可答。

## 4. 正式 Principal 数据流

```
ctx.channel（外壳框架侧写入：cli / desktop / acp:<client> / None）
        │
        ▼  GovernanceContext.principal_of(ctx)
Principal.from_legacy_by(channel or "system")      ← 既有解析面,统一路径
        │
        ▼  authorize(principal=None → principal_of(ctx))
DecisionEngine.decide(principal=…)                 ← 纯计算(既有参数)
        │
        ▼
Decision.principal  →  decision.issued.payload[principal_kind|principal_id|principal_channel]
        │
        ▼
后续 Receipt / Evidence（S4-M4/M6/M7）
```

## 5. 为什么采用该最小 seam

- **A 成立**：`ctx.channel` 已是真实生产事实，且经 `attach_engine_to_ctx` 保留到执行 ctx。
- **零新增 seam**：`authorize()` 的 `principal=None` 分支本已调用 `principal_of(ctx)`；本步只**填充该函数的派生逻辑**。
- **零新增 API**：复用既有 `Principal.from_legacy_by`（S3-1 已实现并有 T3 往返测试）。
- **零调用方改动**：executor 无需改动（`authorize(call, ctx)` 签名不变）。

## 6. 修改文件

| 文件 | 增/删 | 为什么 |
|---|---|---|
| `pyharness/governance/context.py` | **+20 / −10** | 删除 `_TEMPORARY_PRINCIPAL` 常量；`principal_of(ctx)` 改为自 `ctx.channel` 经 `from_legacy_by` 派生；无通道时用既有 `"system"` 字面量；`_emit_decision_issued` 的兜底 id 同步 |
| `tests/unit/test_tools_executor.py` | **+155 / −4** | 新增 `TestPrincipalIdentity`（T1–T8，9 例）；更新 1 处旧的临时身份断言 |
| `tests/unit/test_governance_authorize.py` | **+3 / −2** | T19 断言更新：无 channel（headless）⇒ `SYSTEM / "system"` |

**仅 1 个生产文件**（`governance/context.py`）。

## 7. 测试 T1–T8（真实运行路径）

| # | 用例 | 断言 |
|---|---|---|
| **T1** | `test_t1_real_caller_identity_reaches_decision` | `channel="cli"` → `Decision.principal = HUMAN/cli/cli`；**`principal_id != "pyharness-runtime"`** |
| **T2/T3** | `test_t2_t3_existing_channels_map_stably`（参数化 ×3） | 已存在的真实通道稳定映射：`cli→human/cli/cli` · `desktop→human/desktop/desktop` · `acp:alice→human/alice/acp` |
| **T4** | `test_t4_approval_lifecycle_same_principal` | `D1.principal == D2.principal`；`approval_ref`（None / =rid）、`inputs_digest`（同源非空）、`supersedes` **均不变** |
| **T5** | `test_t5_principal_recoverable_from_jsonl` | 真实 JSONL → 关闭 → 重开 replay，`principal` 三字段可恢复 |
| **T6** | `test_t6_missing_identity_is_explicit_system` | `channel=None` ⇒ **明确 SYSTEM fallback**（`system/system/None`），不伪装 human |
| **T7** | `test_t7_principal_legacy_roundtrip_no_regression` | `from_legacy_by` ↔ `to_legacy_by` 往返无回归（8 种真实/前缀格式） |
| **T8** | `test_t8_approval_ref_and_digest_unaffected` | 非 approval 路径 `approval_ref is None`、`supersedes is None`、`inputs_digest != ""` |

## 8. replay 验证

T5 以**真实 `SessionStore`（JSONL）**落盘 → `close()` → `open_store` 重开 → `replay()`：`decision.issued` 的 `principal_kind/id/channel` 完整恢复（`human/bob/acp`）。**不依赖任何内存状态**。

## 9. identity 缺失时的实际语义（T6）

**明确的 SYSTEM fallback**：`channel` 缺失或为空（headless / 无人类通道）⇒ `Principal(SYSTEM, "system", None)`。

- **不是新规则**：沿用 approval 侧既有的 `by="system"` 语义（框架自决：超时、取消、清理），并经**同一条** `from_legacy_by` 路径派生（无特殊分支）。
- **不静默猜**：**未知通道格式**（如 `"bogus"`）⇒ `from_legacy_by` 抛 **`APR-503` fail-closed**，不降级（实测）。
- **不伪装**：绝不把无通道场景标成 HUMAN/AGENT。

> 附带身份 id 由 `"pyharness-runtime"` 改为 `"system"`：使**全部** principal 都经 `from_legacy_by` 单一派生路径产生，与既有 legacy 词表一致；非"换一个硬编码字符串"——真正的变化是**有通道时现在能取到真实 caller**。

## 10. 架构边界核验

| 约束 | 状态 |
|---|---|
| GuardChain 是 g1–g7 唯一真源 | ✅ `tools_guard.py` 零改动 |
| GovernanceContext 是唯一 authorization orchestration entry | ✅ 未新增入口 |
| DecisionEngine 纯计算、无 I/O | ✅ `decision.py` 零改动；`principal` 仍是入参 |
| Governance 不暴露 execution API | ✅ |
| Executor 不直接调用 GuardChain | ✅ `tools_executor.py` 零改动 |
| JSONL 是唯一 persistence/source of truth | ✅ 未触及 persistence |
| Decision 契约未扩展 | ✅ 字段未变 |
| `decision_id` / `approval_ref` / `inputs_digest` 不变 | ✅ T4/T8 |
| F-SYNC-1 不变 | ✅ `persistence.py` 零改动 |
| D1/D2 approval lifecycle 与时序不变 | ✅ T4 |
| 不创建第二套 identity source of truth | ✅ 单一来源 `ctx.channel` |
| 未引入 IAM/RBAC/OAuth/JWT/账号系统 | ✅ |

**冻结 ADR 无需修改**：设计 §3.1 已定义 `Principal` 与 `from_legacy_by`，本实现按该定义装配；`ARCHITECTURE_DECISION_RECORD.md` / ADR-0xx 未定义 principal 的运行时来源 ⇒ **无冲突**。

## 11. 新增 deferred debt

**无。**

> 备注（非债务）：`channel` 为 `None` 的 headless 场景仍产生 SYSTEM 主体——这是**有意语义**（无人类 caller），非占位。

---

**本步产物**：`pyharness/governance/context.py` · `tests/unit/test_tools_executor.py` · `tests/unit/test_governance_authorize.py` · 本报告。

---

## 12. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S4-M4** | Decision Receipt（`receipt.py` + `receipt.emitted`；P1-1/P1-2/P1-3 前置均已就位） |

**等待人工 checkpoint。**
