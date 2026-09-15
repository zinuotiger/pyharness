# S5-2_CHANGE_REPORT.md — 证据派生与运行时装配（Evidence Index）

> **阶段**：S5-2（**S5-2a** 只读索引 + 段锚聚合 → **S5-2b** engine 装配；合并为单次 checkpoint）
> **日期**：2026-09-15 ｜ **基线 commit**：**`8454d46`**（S5-1 checkpoint;worktree clean）
> **依据**：冻结设计 §3.5（`EvidenceCollector.on_event` / `collect_for_task`）· S5 Pre-Implementation Review 裁定 **D-1(a)**（索引来源 = `evidence.archived` 事件）· **Δ-1**（不订阅 `tool.result`）· ADR-018:308
> **性质**：**本次 checkpoint**。**不进入 S5-3**；未创建 `audit.py`;未修改 events schema。

---

## 0. 结论

# S5-2 = PASS

**改动 5 文件（+378 / −19）**；全量回归 **1635 passed / 2 skipped / 0 failed**（较基线 1625 **+10**，零回归）。
**零 schema 变更**：`EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` **全部不变**。

---

## 1. S5-2 目标与范围

| 段 | 内容 |
|---|---|
| **S5-2a** | `on_event`（**只读**索引）· `from_log`（日志重建）· `collect_for_task`（段锚聚合） |
| **S5-2b** | `engine.py` 装配：**构造 → 订阅 → 注入**；`context.py` 字段类型标注 |

**不做**：`AuditSystem`（S5-3）· `reconcile` / `legacy_session_audit`（S5-4）· `TraceabilityMatrix`（**S7**）。

---

## 2. 修改文件列表（5 文件）

| 文件 | 增/删 | 段 | 内容 |
|---|---|---|---|
| `pyharness/governance/evidence.py` | **+134 / −4** | a | `__init__` 加 3 个派生缓存字段;新增 `on_event` / `from_log` / `_evidence_of` / `_task_at` / `_tasks_of` / `collect_for_task` / `evidence_count`;docstring 更新 |
| `tests/unit/test_governance_evidence.py` | **+168 / −5** | a + b | `TestEvidenceIndex`（7 例）+ `TestEvidenceWiring`（2 例）+ `asyncio` import + 头注 |
| `tests/invariants/test_inv_governance.py` | **+45 / −0** | a | **INV-E3 专项**（索引可再派生、只读） |
| `pyharness/engine.py` | **+23 / −3** | b | 治理 import 加 `EvidenceCollector`;新增 `_EVIDENCE_EVENT_TYPES`;构造 → 订阅 → 注入;`GovernanceContext(evidence=…)` |
| `pyharness/governance/context.py` | **+8 / −7** | b | import `EvidenceCollector`;`evidence` 字段类型化;docstring 形状清单更新 |

---

## 3. 派生索引设计（三个，皆为**只读缓存**）

| # | 索引 | 来源事件 | 用途 |
|---|---|---|---|
| ① | 证据索引 `evidence_id → (seq, Evidence)` | `evidence.archived` | `collect_for_task` 的**唯一**数据源（D-1(a)） |
| ② | 段索引 `task_id → [start_seq, end_seq]` | `segment.start` / `segment.end` | 把事件 seq 解析到 task |
| ③ | 锚 seq `decision_id` / `receipt_id → seq` | `decision.issued` / `receipt.emitted` | 把 `decision_id` / `receipt_id` 引用解析到段 |

**`collect_for_task(task_id)`**：四类 ref（`segment` 直配 / `seq` 区间 / `decision_id`、`receipt_id` 经锚 seq）→ 按事件 seq **升序**返回 `tuple[Evidence, ...]`;未知/空 task ⇒ `()`（**不抛**）。

**订阅面（5 类）**：`evidence.archived` · `segment.start` · `segment.end` · `decision.issued` · `receipt.emitted`。
**不订阅** `tool.result`（**Δ-1 裁定**：属工具执行因果关系,由 S5-3 `AuditSystem.causal_chain` 承担）。

---

## 4. 裁定落地

| 裁定 / 约束 | 落地 | 证据 |
|---|---|---|
| **D-1(a)**：索引来源 = `evidence.archived` 事件 | ✅ 证据索引**仅**由该事件建 | `collect_for_task` 只遍历 `_evidence` |
| **约束①** `on_event` 可订阅 `evidence.archived` | ✅ | 订阅面含它 |
| **约束②** 只消费不产生 | ✅ | **AST 实测 `on_event` 内 append = False**;T-3 断言喂完后**事件数不变** |
| **约束③** 索引只是派生缓存 | ✅ | `from_log()` 可完全重建 |
| **约束④** 重启后 replay 重建**相同** index | ✅ | **T-2**：新 collector 仅靠 replay ⇒ 与订阅态**逐条相等** |
| **约束⑤** `archive()` 不维护第二份事实存储 | ✅ | `archive()` **零改动**;专项断言「archive 后 `evidence_count()==0`」 |
| **Δ-1** 不订阅 `tool.result` | ✅ | 未订阅 |

---

## 5. 装配顺序（S5-2b，严格按要求）

```python
evidence = EvidenceCollector(session=log_)                    # ① 构造
if bus is not None:                                          # ② 订阅
    ev_owner = f"governance-evidence:{getattr(log_, 'session_id', '?')}"
    for t in _EVIDENCE_EVENT_TYPES:
        bus.subscribe(t, evidence.on_event, owner=ev_owner)
spine = EngineSpine(..., governance=GovernanceContext(       # ③ 注入
    policy=gov_policy, decisions=DecisionEngine(),
    receipts=ReceiptStore(), evidence=evidence), ...)
```

**端到端实测（真实 spine）**：

```
spine.governance.evidence = EvidenceCollector
ctx.governance is spine.governance = True         ← 单实例
段索引 = {'t-1': [2, None]}                       ← on_event 真收到 segment.start
evidence_count() = 1                              ← 真收到 evidence.archived
collect_for_task('t-1') = ['E1']                  ← 聚合在真实路径可用
```

---

## 6. 测试结果

| 项 | 结果 |
|---|---|
| S5-2 专项 `test_governance_evidence.py` | **28 passed**（S5-1 19 + S5-2a 7 + S5-2b 2） |
| Invariants（`tests/invariants/`） | **15 passed**（含 **INV-E3**） |
| **全量回归** | **1635 passed / 2 skipped / 0 failed** |
| 相对基线 1625 | **+10（零回归）** |

*2 条 skip = 既有平台相关（`test_spill`）。未执行面：`desktop_native/**` / `test_shell_parity.py`（缺 `PySide6`,基线遗留 R-7）。*

---

## 7. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 未修改 **S5-1 冻结契约**（`EvidenceRef` / `Evidence` / `archive()` 语义） | ✅ `archive()` 零改动 |
| 未修改 **events schema** | ✅ `payload.py` / `vocab.py` **零改动**;`EVENT_TYPES=77` / `SYNC_TYPES=14` / `TRANSIENT_TYPES=3` **全部不变** |
| 未新增 **persistence source** | ✅ 索引为可重建缓存;无文件/DB 操作 |
| 未创建 **`audit.py`** | ✅ `governance/` 仍 6 模块 |
| 未进入 **S5-3** | ✅ 无 `AuditSystem` / `reconcile` / `legacy_session_audit` |
| 未修改治理冻结项（`decision.py` / `policy.py` / `receipt.py`） | ✅ 零命中 |
| 未修改执行链 / 持久化 / 总线逻辑 | ✅ `core/*` · `persistence.py` · `bus/event_bus.py` 零命中 |
| 未修改 ADR / `REFACTOR_PLAN.md` / `ARCHITECTURE_DECISION_RECORD.md` / `docs/` | ✅ 零命中 |
| 治理层依赖边界 | ✅ `evidence.py` 仍无 `core` / `persistence` / `bus` / `engine` import |

**本步产物**：`pyharness/governance/evidence.py` · `pyharness/engine.py` · `pyharness/governance/context.py` · `tests/unit/test_governance_evidence.py` · `tests/invariants/test_inv_governance.py` · 本报告。

---

## 8. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S5-3** | `AuditSystem`（`causal_chain` / `denied_report`,**replay-only**,无写点） |
| **S5-4** | `reconcile()`（唯一写点:`syscheck.fail`）+ `legacy_session_audit`（**注入** `telemetry.session_audit`） |

**等待人工 checkpoint。**
