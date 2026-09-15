# S5-3_CHANGE_REPORT.md — 治理审计视图（AuditSystem）实施报告

> **阶段**：S5-3（**S5-3a** 审计模块（replay-only）→ **S5-3b** engine 装配；**合并为单次 checkpoint**）
> **日期**：2026-09-15 ｜ **基线 commit**：**`18773cf`**（S5-2 技术归档 checkpoint;worktree clean）
> **依据**：冻结设计 §3.6（`AuditSystem.causal_chain` / `denied_report`）· `docs/EVENT-SCHEMA.md:492`（`INV-05`）· ADR-018:308 · S5-3 Final Implementation Plan（H-6 接受 / 两段式实施，合并提交）
> **性质**：**本次 checkpoint**。**不进入 S5-4**（`reconcile` / `legacy_session_audit` 未实现）。

---

## 0. 结论

# S5-3 = PASS

**改动 5 文件（+67 / −14）+ 新增 2 文件（550 行）**；全量回归 **1655 passed / 2 skipped / 0 failed**（较基线 1635 **+20**，零回归）。
**零 schema 变更**：`EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` **全部不变**。

---

## 1. S5-3 目标与范围

| 段 | 内容 |
|---|---|
| **S5-3a** | `AuditSystem` 纯模块：`causal_chain()`（因果链）· `denied_report()`（被拒清单）—— **replay-only** |
| **S5-3b** | `engine.py` 构造 + 注入；`context.py` 类型标注；`__init__.py` 导出 |

**不做**：`reconcile()` / `legacy_session_audit()`（**S5-4**）· `TraceabilityMatrix`（**S7**）。

---

## 2. 修改文件列表

### 新增

| 文件 | 行数 | 段 | 内容 |
|---|---|---|---|
| `pyharness/governance/audit.py` | **212** | a | `AuditSystem`（frozen dataclass）· `causal_chain()` · `denied_report()` · `_receipt_expected()` · `CHAIN_SEGMENTS` |
| `tests/unit/test_governance_audit.py` | **338** | a + b | 19 例（T-1~T-9 + 规则一致性 + 段名常量 + 装配 2 例） |

### 修改

| 文件 | 增/删 | 段 | 内容 |
|---|---|---|---|
| `tests/invariants/test_inv_governance.py` | **+41 / −0** | a | **INV-A1 / A2 / A3** 专项 |
| `pyharness/engine.py` | **+10 / −3** | b | import `AuditSystem`；构造（**无订阅**）+ 注入 `GovernanceContext(audit=…)` |
| `pyharness/governance/context.py` | **+8 / −7** | b | import；`audit` 字段类型化；docstring 形状清单更新 |
| `pyharness/governance/__init__.py` | **+3 / −0** | b | 导出 `AuditSystem` |
| `tests/unit/test_governance_decision.py` | **+5 / −4** | b | 门面断言更新（`AuditSystem` 已导出；`TraceabilityMatrix` 仍未） |

---

## 3. replay-only 设计约束

`AuditSystem` 是**纯派生视图（derived view）**，四条硬约束全部满足：

| # | 约束 | 落地 | 证据 |
|---|---|---|---|
| 1 | **不订阅**任何事件 | `audit.py` 内**零 `subscribe`**；`engine.py` **刻意不加** `bus.subscribe` | 单元 AST 断言（T-9）· 装配断言（订阅 owner 中无 audit） |
| 2 | **不缓存**跨调用状态 | 每次调用即时全量重放 `_read_events(session)` | 全新实例（无订阅历史）⇒ 结果与订阅态**相同**（T-5） |
| 3 | **不写**任何事件 | 模块内**无写点** | 调用前后**事件数不变** + AST 断言无会话对象 `append`（T-6） |
| 4 | **不依赖 `EvidenceCollector`** | `audit.py` **不 import evidence**；独立重放 | 依赖边界 AST（T-9） |

**为什么必须 replay-only**：
1. **重启可用性** —— 订阅态索引在重启后为空;审计必须**仅凭日志**可用;
2. **覆盖订阅面之外** —— 因果链需要 `tool.result` / `guard.*` / `approval.*`，**不在** `EvidenceCollector` 的订阅面内（`Δ-1` 有意为之）;
3. **零副作用** —— 不新增订阅者 ⇒ 无 EVT-103 噪声、无生命周期耦合;
4. **可复现** —— 同一日志 ⇒ 同一结果（不依时序、不依订阅先后）。

**已知代价（`H-6`，v1.0 接受）**：每次调用 **O(n) 全量重放**。裁定为 `deferred debt` —— **不引入任何持久化缓存或二级索引**（优先保证正确性、可复现性与单一真源）。

---

## 4. `AuditSystem` 与 `EvidenceCollector` 的边界

| 维度 | `EvidenceCollector`（S5-2） | `AuditSystem`（S5-3） |
|---|---|---|
| **组织轴** | **段轴（task / segment）** | **因果轴（`call_id` / `decision_id` / `approval_id` / `receipt_id`）** |
| **提问** | "**这个任务**有哪些证据？" | "**这条决策**为什么发生、经过了什么？" |
| **事件覆盖** | **5 类**（证据 / 段 / 锚） | **9 类**（+ `tool.call` / `tool.result` / `guard.*` / `approval.*` / `supersedes`） |
| **数据形态** | `Evidence`（工件 + 引用） | **原始事件事实**（verdict / principal / digest / by…） |
| **输出** | `tuple[Evidence, ...]`（**已归档**的证据） | **即时派生**的因果链字典 / 拒绝清单 |
| **覆盖范围** | 仅**被显式归档**的证据 | **全部**决策（含从未归档者） |
| **订阅** | ✅ 5 类 | ❌ **无** |
| **写点** | `archive()` → `evidence.archived` | **无**（`reconcile` 属 S5-4） |
| **状态** | 内存索引（缓存，可重建） | **无状态** |

**结论**：二者**职责正交**，互不依赖;审计**不得**建立在证据索引之上（否则该索引事实上升级为"审计的前提"，逼近第二真源，威胁 `INV-R5 / INV-E3`）。

---

## 5. 核心接口（冻结输出结构）

### 5.1 `causal_chain(decision_id) -> dict`（11 键）

`decision_id` · `call_id` · `request` · `policy` · `decision` · `approval` · `supersedes` · `receipt` · `execution` · `denied` · `missing`

**缺失语义（`INV-A1`）**：未命中的环节一律 `None`;**`decision.issued` 本身缺失** ⇒ 直接返回并只记该段（**不抛**）;应有而缺失的段（如非 D1 决策缺凭证）**显式登记**入 `missing`。**绝不臆造**。

### 5.2 `denied_report(*, session_id="", since_seq=0) -> list[dict]`（9 键）

`source` · `seq` · `tool` · `call_id` · `guard_id` · `policy_ref` · `decision_id` · `reason` · `executed`

**两类来源必须可区分（`INV-A3`）**：`source == "guard.rejected"`（规则级，`INV-05` 强同步留痕）或 `source == "decision.issued"`（治理级 `verdict=reject`）。
`executed` = 该 `call_id` 是否存在配对 `tool.result` ⇒ 审计可证**"拦了且没执行"**。

### 5.3 关联键总表

```
decision_id ──► decision.issued ──(trace)──► call_id
call_id ──► tool.call / tool.result / guard.* / approval.*        （payload 或 trace）
approval_ref ──► approval.requested.seq ──► approval.granted/denied/timeout.approval_id
decision_id ──► receipt.emitted.decision_id ──► receipt
supersedes ──► 前序 decision_id（D1 ← D2）
```

---

## 6. 测试结果

| 项 | 结果 |
|---|---|
| S5-3 专项 `test_governance_audit.py` | **19 passed**（S5-3a 17 + S5-3b 2） |
| Invariants（`tests/invariants/`） | **16 passed**（含 **INV-A1 / A2 / A3**） |
| **全量回归** | **1655 passed / 2 skipped / 0 failed** |
| 相对基线 1635 | **+20（零回归）** |

**关键断言**：
- **四场景因果链**：ALLOW（全段齐备）· REJECT（`denied=True`、`execution=None` **且不算缺失**）· D1（`receipt is None`、`approval is None`、`missing == []`）· D2（`approval == {approval_id:11, outcome:"granted", by:"cli:alice", seq:12}`、`supersedes == "D-C1"`、`receipt.kind == "approval"`）
- **拒绝报告**：`[{seq:6, source:"guard.rejected", guard_id:"g-danger"}, {seq:7, source:"decision.issued", decision_id:"D-B"}]`，**全部 `executed == False`**
- **零副作用**：调用前后事件数不变
- **replay 一致性**：全新实例（无订阅历史）结果与订阅态**相同**
- **规则一致性**：`_receipt_expected` 与 `receipt.receipt_kind_of` **逐例一致**（防漂移守卫）
- **装配**：`ctx.governance is spine.governance`;**订阅 owner 中无 audit 相关项**

*2 条 skip = 既有平台相关（`test_spill`）。未执行面：`desktop_native/**` / `test_shell_parity.py`（缺 `PySide6`，基线遗留 R-7）。*

---

## 7. 冻结项与边界检查

| 检查 | 结果 |
|---|---|
| **`EVENT_TYPES=77` / `SYNC_TYPES=14` / `TRANSIENT_TYPES=3`** | ✅ **全部不变**（本步**零 schema 变更**） |
| **未修改 `audit.py` 核心逻辑（S5-3b）** | ✅ S5-3a 的 212 行原样 |
| 未修改 `decision.py` / `receipt.py` / `policy.py` / `evidence.py` | ✅ 零命中 |
| 未修改 `core/**` / `persistence.py` / `events/**` / `bus/**` | ✅ 零命中 |
| 未修改 ADR / `REFACTOR_PLAN.md` / `ARCHITECTURE_DECISION_RECORD.md` / `docs/` | ✅ 零命中 |
| 未实现 S5-4 内容（`reconcile` / `legacy_session_audit`） | ✅ 无 |
| 未实现 `TraceabilityMatrix`（S7） | ✅ 无 |
| Governance Core v1.0 冻结项 | ✅ 未触（`Decision` / `inputs_digest` / `approval_ref` / `Principal` / GuardChain / F-SYNC-1 / D1-D2 时序） |

**本步产物**：`pyharness/governance/audit.py` · `tests/unit/test_governance_audit.py` · `pyharness/engine.py` · `pyharness/governance/context.py` · `pyharness/governance/__init__.py` · `tests/invariants/test_inv_governance.py` · `tests/unit/test_governance_decision.py` · 本报告。

---

## 8. deferred debt（本步新增）

| # | 项 | 说明 |
|---|---|---|
| **D-1** | **`causal_chain` / `denied_report` 为 O(n) 全量重放** | 裁定接受（H-6）。若未来需优化，可加**一次性**派生缓存 —— 但**必须**保持"可由日志完全重建"，且**不得**引入持久化。 |
| **D-2** | `denied_report` 的 `reason` 字段对治理级来源取 `policy_refs` 的**逗号拼接** | 属展示口径;若需结构化,应改为列表（本次按冻结的 9 键扁平结构实现）。 |

---

## 9. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S5-4** | `reconcile()`（**唯一写点**:既有 `syscheck.fail`）+ `legacy_session_audit()`（**注入** `telemetry.session_audit`） |
| **S6** | 不变量与测试面重写（M8/M9）· `tests/acceptance/` / `tests/security/` 激活 |
| **S7** | `TraceabilityMatrix`（矩阵）· 端到端 Demo（M10）· 文档刷新 |

**等待人工 checkpoint。**
