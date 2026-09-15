# S5-1_CHANGE_REPORT.md — 证据契约与归档入口（Evidence Contract）

> **阶段**：S5-1（M6 第一步：证据数据契约 + `evidence.archived` 注册;**不接线**）
> **日期**：2026-09-15 ｜ **基线 commit**：**`f191c85`**（S4-M4 checkpoint;worktree clean）
> **依据**：冻结设计 §3.5（`Evidence` / `EvidenceRef` / `EvidenceCollector`）· `docs/EVENT-SCHEMA.md:490`（`evidence.archived` 冻结载荷 + 通道）· ADR-018:308（治理层依赖边界）
> **性质**：**未 commit**。**不进入 S5-2/S5-3/S5-4**；未创建 `audit.py`；未接入 `engine.py`。

---

## 0. 结论

# S5-1 = PASS

**改动 8 文件 + 新增 2 文件**；全量回归 **1625 passed / 2 skipped / 0 failed**（较基线 1606 **+19**，零回归）。
**生产行为逐字不变**——`evidence.archived` 已注册但**无任何调用方**。

---

## 1. S5-1 目标与范围

**目标**：建立**证据工件的数据契约**与**唯一归档入口**，使「支持某 claim 的引用集合 + 脱敏摘要」成为一等对象，并以事件留痕。

**范围（本步只做）**：
`EvidenceRef` · `Evidence` · `EvidenceCollector.archive()` · `evidence.archived` 事件注册 · `EvidenceArchivedPayload`（含 `refs` validator）· 相关测试。

**明确不做（属后续步）**：见 §8。

---

## 2. 修改文件列表（8 改 + 2 新增）

### 新增

| 文件 | 行数 | 内容 |
|---|---|---|
| `pyharness/governance/evidence.py` | **192** | `REF_KINDS` · `EVENT_EVIDENCE_ARCHIVED` · `EvidenceRef` · `Evidence` · `_coerce_refs()` · `EvidenceCollector`（`__init__` / `_resolve()` / **`archive()`**） |
| `tests/unit/test_governance_evidence.py` | **203** | 19 例（契约一致性 / payload / INV-E1 / INV-E2 / INV-E4 / 降级 / 字典 refs） |

### 修改

| 文件 | 增/删 | 内容 |
|---|---|---|
| `pyharness/events/payload.py` | +45 / −2 | 新增 `_EVIDENCE_REF_KINDS`（本地声明）+ `EvidenceArchivedPayload`（4 字段 + `field_validator("refs")`）+ 头注 76→77（74→75 payload 模型） |
| `pyharness/events/vocab.py` | +7 / −3 | import `EvidenceArchivedPayload` · `_CORE_EVENT_TYPES` 追加一行 · 头注 76→77（19→20 扩展项） |
| `pyharness/governance/__init__.py` | +4 / −0 | 导出 `Evidence` / `EvidenceRef` / `EvidenceCollector` |
| `tests/unit/test_events.py` | +20 / −? | `EXPECTED_ALL` +`evidence.archived`（→**77**）· `test_vocab_full` 77 · `test_s25_channel_counts`（**SYNC 仍 14** + 显式断言 `evidence.archived **not in** SYNC_TYPES`）· `_ALLOWED_COUNTS={"77"}` · 守卫文案 · 头注 |
| `tests/unit/test_governance_decision.py` | +4 / −2 | 门面断言：Evidence 三项**已导出**;`AuditSystem` / `TraceabilityMatrix` **仍未导出** |
| `pyharness/bus/event_bus.py` | +2 / −2 | **仅注释**（`:106` `:114`，76→77） |
| `pyharness/core/approval.py` | +1 / −1 | **仅注释**（`:78`，76→77） |
| `pyharness/core/tools_registry.py` | +1 / −1 | **仅注释**（`:86`，76→77） |

**`git diff --stat`（tracked）**：`8 files changed, 75 insertions(+), 18 deletions(-)`

> 后 3 处是**当前计数注释**，由词表 76→77 使其过期，必须同步（否则注释守卫 `test_s25_no_stale_vocab_count_in_source` 失败）。已用 **AST 剥离 docstring 后比对**确认 **3/3 逐字相同**（纯注释，零行为）。

---

## 3. 新增事件 `evidence.archived`

| 项 | 值 |
|---|---|
| **payload（冻结 4 字段）** | `evidence_id` str(`min_length=1`) · `claim` str(`min_length=1`) · `refs` list · `artifact_path` str? |
| **payload validator** | `field_validator("refs")`：每项必须是 dict 且**键集恰为 `{kind, locator}`** · `kind ∈ _EVIDENCE_REF_KINDS` · `locator` 非空字符串 |
| **通道** | **普通（攒批）** —— **不入 `SYNC_TYPES`**（EVENT-SCHEMA:490 冻结） |
| **写入者** | **仅** `EvidenceCollector.archive()`（本模块**唯一写点**） |

**为什么非强同步**（INV-E3，非缺陷）：证据是**索引**而非事实 —— 其可信度全部来自被引用的真源事件；丢失可由既有事件**再派生**。故**不**继承 F-SYNC-1 的强同步契约。

---

## 4. 词表变更

| 项 | 变化 |
|---|---|
| `EVENT_TYPES` | **76 → 77** |
| `SYNC_TYPES` | **14 → 14（保持）** —— `evidence.archived` **未**入 SYNC |
| `TRANSIENT_TYPES` | **3（不变）** |

**实测**：`is_registered('evidence.archived')=True` · `'evidence.archived' in SYNC_TYPES=**False**` · `is_transient=False`。

---

## 5. `archive()` 语义

```
archive(claim, refs, *, ctx=None, summary="", artifact_path=None) -> Evidence | None
```

| 情形 | 行为 |
|---|---|
| 正常 | 校验 `claim` / `refs` → **INV-E2 存在性校验** → `append("evidence.archived")` → 返回 `Evidence` |
| `ctx` 省略 | 用构造期注入的 `session` |
| **无会话**（纯内存/单测） | **降级返回 `None`**（不发射、不抛） |
| `claim` 空 / `refs` 形态非法 / `kind` 越界 / `locator` 空 | **`CYC-999` fail-closed** |
| **locator 不可解析**（`seq` / `decision_id` / `receipt_id` / `segment` 未指向真实存在的真源锚） | **`CYC-999` fail-closed**（INV-E2） |
| `kind="test"` | **外部引用**（测试路径）,不做运行时存在性校验 |

**INV-E2 的校验方式**：只读读日志（复用 `receipt._read_events` 的鸭子类型读法：`events_after(0)` 优先、回落 `replay()`），按 kind 逐类比对（seq 值 / `decision.issued.decision_id` / `receipt.emitted.receipt_id` / `segment.start.task_id`）——**不写任何事件**。

---

## 6. 不变量验证结果

| 不变量 | 结果 | 证据 |
|---|---|---|
| **INV-E1**（只存引用,不复制事件内容） | ✅ | `test_inv_e1_archive_stores_reference_not_fact`：payload 恰 4 字段;含被引用事件的 `refs`;断言不含 `verdict` / `tool` 等内容副本 |
| **INV-E2**（locator 必须可解析） | ✅ | `test_inv_e2_locator_must_resolve`（**参数化 8 例**：4 类 × 命中/未命中）;不可解析 → `CYC-999` |
| **INV-E4**（`archive` 是唯一写点） | ✅ | `test_inv_e4_archive_is_only_writer`：3 次调用恰增 3 条事件;模块内无其它 `append` 调用点 |
| **INV-E3**（非强同步 ⇒ 可再派生） | ✅ | `test_event_registered_but_not_sync`：`evidence.archived ∉ SYNC_TYPES` |
| 依赖边界（ADR-018:308） | ✅ | `test_dependency_boundary`（AST）：`evidence.py` 无 `core/persistence/bus/engine` import |
| 契约一致性 | ✅ | `test_ref_kinds_match_evidence_module`：payload 本地 `_EVIDENCE_REF_KINDS` == `governance.evidence.REF_KINDS` |

> **INV-E1/E2/E4 三项均实现并通过专项用例**；本步**未**引入其他 INV-E*。

---

## 7. 测试结果

| 项 | 结果 |
|---|---|
| S5-1 专项 `test_governance_evidence.py` | **19 passed** |
| S5-1 关联面（evidence + events + invariants + decision 门面） | **91 passed** |
| **全量回归** | **1625 passed / 2 skipped / 0 failed** |
| 相对基线 1606 | **+19（零回归）** |

*2 条 skip = 既有平台相关（`test_spill`）。未执行面：`desktop_native/**` / `test_shell_parity.py`（缺 `PySide6`,基线遗留 R-7）。*

---

## 8. 未实现范围（明确边界）

| 项 | 归属 | 状态 |
|---|---|---|
| `EvidenceCollector.on_event`（只读订阅,建内存索引） | **S5-2** | **未实现**（全库零命中） |
| `EvidenceCollector.collect_for_task`（段锚聚合） | **S5-2** | **未实现**（全库零命中） |
| `engine.py` 装配 / `GovernanceContext.evidence` 接线 | **S5-2** | **未接线**（`engine.py` 零改动） |
| `AuditSystem`（`causal_chain` / `denied_report`） | **S5-3** | **未实现**；`audit.py` **未创建** |
| `reconcile()` / `legacy_session_audit()` | **S5-4** | **未实现**；`GovernanceContext.audit` 仍为 `None` 占位 |
| `TraceabilityMatrix` | **S7**（冻结计划 §5.2） | **未实现**（已从 S5 移出） |

---

## 9. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 未修改 `persistence.py` / `core/session.py` / `core/tools_guard.py` / `core/tools_executor.py` / `core/scope.py` / `core/agent.py` / `engine.py` | ✅ 零命中 |
| 未修改治理冻结契约（`decision.py` / `policy.py` / `context.py` / `receipt.py`） | ✅ 零命中 |
| 未修改任意 `ADR-*` / `REFACTOR_PLAN.md` / `ARCHITECTURE_DECISION_RECORD.md` / `docs/` | ✅ 零命中 |
| 未创建 `audit.py` | ✅ |
| 未实现 `on_event` / `collect_for_task` / `TraceabilityMatrix` | ✅ 零命中 |
| 3 处注释文件为**纯注释** | ✅ AST-strip **SAME (3/3)** |
| 未 commit / 未 push | ✅ |

**本步产物**：`pyharness/governance/evidence.py` · `tests/unit/test_governance_evidence.py` · `pyharness/events/payload.py` · `pyharness/events/vocab.py` · `pyharness/governance/__init__.py` · `tests/unit/test_events.py` · `tests/unit/test_governance_decision.py` · `pyharness/bus/event_bus.py`(注释) · `pyharness/core/approval.py`(注释) · `pyharness/core/tools_registry.py`(注释) · 本报告。

---

## 10. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S5-2** | `on_event`（**只读**订阅）+ `collect_for_task`（段锚聚合）+ `engine.py` 装配 |
| **S5-3** | `AuditSystem`（`causal_chain` / `denied_report`,**replay-only**） |
| **S5-4** | `reconcile()`（唯一写点:`syscheck.fail`）+ `legacy_session_audit`（**注入** `telemetry.session_audit`） |

**等待人工 checkpoint。**
