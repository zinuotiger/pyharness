# S5-4_CHANGE_REPORT.md — 治理对账与旧审计面兼容适配（Reconciliation & Legacy Adapter）

> **阶段**：S5-4（**S5-4a** 对账与 legacy 适配实现 → 装配 → **单次 checkpoint**）
> **日期**：2026-09-15 ｜ **基线 commit**：**`dada674`**（S5-3 知识冻结 checkpoint;worktree clean）
> **依据**：冻结设计 §3.6（`AuditSystem.reconcile` / `legacy_session_audit`）· `ADR-018:308`（治理层依赖方向）· S5-4 Final Plan 的 4 项裁定（`emit` 默认 `False` / `CACHE-STALE` 暂缓 / legacy 注入 / 单 commit）
> **性质**：**本次 checkpoint**。**未进入 S6/S7**。

---

## 0. 结论

# S5-4 = PASS

**改动 4 文件（+373 / −15）**；全量回归 **1671 passed / 2 skipped / 0 failed**（较基线 1655 **+16**，零回归）。
**零 schema 变更**：`EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` **全部不变**。

---

## 1. S5-4 目标与范围

| 接口 | 定位 |
|---|---|
| **`reconcile()`** | **一致性自我检验** —— 判定三类"系统内部自相矛盾";**默认纯只读**,仅 `emit=True` 时落一条既有 `syscheck.fail` |
| **`legacy_session_audit()`** | **旧审计面兼容适配** —— 经**注入**转调 `telemetry.session_audit`,使既有调用点（`application/service.py:533`）可经治理层取得同一份报告 |

**不做**：`CACHE-STALE`（暂缓）· `TraceabilityMatrix`（S7）· S6 的不变量/测试面重写。

---

## 2. 修改文件列表（4 文件）

| 文件 | 增/删 | 内容 |
|---|---|---|
| `pyharness/governance/audit.py` | **+134 / −4** | ① `reconcile()`(**async**) · ② `legacy_session_audit()` · ③ dataclass 增 `legacy_audit` 字段 · ④ `_declared_holes()` 辅助 · ⑤ import `check_seq_gap` / `raise_code` · ⑥ 模块 docstring 更新（S5-4 边界 + `CACHE-STALE` 已延期） |
| `tests/unit/test_governance_audit.py` | **+193 / −8** | `TestReconcile`（14 例）+ `TestReconcileWiring`（1 例）;S5-3 的 `test_t6_no_side_effects` 断言**收窄**（见 §6 Δ-2） |
| `tests/invariants/test_inv_governance.py` | **+41 / −0** | **INV-A4 / A5 / A6** |
| `pyharness/engine.py` | **+5 / −3** | 注入 `legacy_audit=telemetry.session_audit`（一行 import + 一行构造） |

**`git diff --stat`**：`4 files changed, 373 insertions(+), 15 deletions(-)`

---

## 3. 三类检查的判据（实现即规格）

| ID | 判据 | 实现 |
|---|---|---|
| **`SEQ-GAP`** | 事件 seq 中**未被声明区间覆盖**的空洞 | **复用既有** `events.envelope.check_seq_gap`;声明面 = `context.compacted.ranges`（`[[lo,hi],…]`）+ `session.recovered.lost`（`[seq,…]` → 单点区间） |
| **`NO-GUARD-EVENT`** | 某 `call_id` 有 `tool.result` 但**无**同 `call_id`（经 `trace`）的 `guard.evaluated` | 新判定（`INV-04`）;`tool.result` 的 `call_id` 取自**载荷**,`guard.evaluated` 的取自 **`trace`** |
| **`NO-RECEIPT-FOR-GRANT`** | 某 `approval.granted(approval_id=N)`：① 找不到 `decision.issued(approval_ref=N)` ⇒ 报;② 找到但其 `decision_id` **无** `receipt.emitted` ⇒ 报 | 新判定（**`INV-G2` 的运行时哨兵**） |

**findings 格式**：`"SEQ-GAP:5,7"` · `"NO-GUARD-EVENT:call_id=c9"` · `"NO-RECEIPT-FOR-GRANT:approval_id=11"`;`findings.sort()` 保证**确定性顺序**。

---

## 4. `reconcile` 的实际契约

```python
async def reconcile(
    *,
    session_id: str = "",
    ctx: Any = None,
    emit: bool = False,
) -> list[str]
```

| 项 | 内容 |
|---|---|
| **默认语义** | **纯只读**（`emit=False`）—— `AuditSystem` 默认职责 = **observation / verification**,不主动改变 Event Log |
| **写点（显式）** | 仅当 `emit=True` **且** findings 非空 **且** 会话在位 ⇒ 追加**一条**既有 `syscheck.fail`（`{findings, trigger:"governance.reconcile"}`） |
| **不新增事件类型** | ✅ 仅用既有 `syscheck.fail`（已注册、非瞬态、**不在 `SYNC_TYPES`**） |
| **降级** | 无会话 ⇒ `[]` 不抛 |
| **幂等** | 写入的 `syscheck.fail` **不参与任何检查** ⇒ 二次 `reconcile` 的 findings **不变**（T-6） |

---

## 5. `legacy_session_audit` 的实际契约

```python
def legacy_session_audit(self, session: Any) -> dict
```

| 项 | 内容 |
|---|---|
| **机制** | 经**注入**的 `legacy_audit` callable 转调（装配层注入 `core.telemetry.session_audit`） |
| **等价性** | 输出与 `telemetry.session_audit(session)` **完全等价**（适配为**透传**,不改语义） |
| **注入纪律** | `audit.py` **不得** import `core.telemetry`（AST 守卫 T-11 断言无 `telemetry` / 无 `pyharness.core*`） |
| **fail-closed** | **未注入** ⇒ `CYC-999`（**不静默返回空 dict** —— 那会掩盖装配缺失） |

---

## 6. Δ 修订登记（两处,均已人工接受）

### Δ-1：`reconcile` 实现为 **async**

| 项 | 内容 |
|---|---|
| **事实** | `reconcile` 是 `async def`（非冻结伪签名中的同步形态） |
| **理由** | **runtime 一致性要求,不是临时实现细节** —— `reconcile(emit=True)` 需要调用 `SessionLog.append`,而后者是 `async def`;同步签名无法正确写入 |
| **先例** | PyHarness Governance Runtime 的核心接口均采用 async 模型：`GovernanceContext.authorize` · `EvidenceCollector.archive` · `ReceiptStore.emit` · `SessionLog.append` |
| **影响面** | **唯一形态差异**;语义、写点边界、检查判据**均无变化** |

### Δ-2：S5-3 的 AST 断言**收窄**

| 项 | 内容 |
|---|---|
| **原断言** | 「`audit.py` 全文不得对会话对象 `append`」 |
| **收窄后** | 「**`causal_chain` / `denied_report` 两个只读方法内**不得对会话对象 `append`」 |
| **理由** | S5-4 起 `reconcile` 是**唯一受控写点**;原全文件扫描必然失败。收窄后**仍保护原意图**（两只读方法零写） |
| **未放松** | 新写点的边界由 **`INV-A5` + T-4/T-5/T-6** 独立保证（默认零写 · 仅允许 `syscheck.fail` · 不新增事件类型） |

**新的禁止/允许边界（正式）**：

```
causal_chain()      → 必须零写
denied_report()     → 必须零写
reconcile()         → 默认(emit=False)零写;
                      emit=True 且 findings 非空 ⇒ 仅允许 syscheck.fail
```

---

## 7. 不变量（新增 INV-A4/A5/A6）

| # | 不变量 | 内容 |
|---|---|---|
| **INV-A4** | **确定性** | 同一日志 ⇒ 同一 findings（同内容、同顺序）;`reconcile` 无状态 |
| **INV-A5** | **默认只读 + 唯一受控写点** | `causal_chain` / `denied_report` **零写**;`reconcile` **默认零写**;唯一写动作是 `emit=True` 时的既有 `syscheck.fail`（**不得新增事件类型**） |
| **INV-A6** | **兼容等价 + 注入纪律** | `legacy_session_audit(s)` 与 `telemetry.session_audit(s)` **等价**;必须经注入（不得 import `core.telemetry`）;未注入 fail-closed |

**与既有不变量关系**：`INV-A4` ↔ `INV-A1`（确定性/不臆造）同族;`INV-A5` 是 `INV-A2`（纯只读）的**精确化**（默认仍纯只读,写点显式且唯一）,与 `INV-E4`（`archive` 唯一写点）同型;`INV-A6` 是 `ADR-018:308` 在兼容适配场景的具体化。

---

## 8. 测试结果

| 项 | 结果 |
|---|---|
| S5-4 专项（`test_governance_audit.py`） | **34 passed**（S5-3 19 + **S5-4 15**） |
| Invariants（`tests/invariants/`） | **17 passed**（含 **INV-A4/A5/A6**） |
| 专项合计 | **51 passed** |
| **全量回归** | **1671 passed / 2 skipped / 0 failed** |
| 相对基线 1655 | **+16（零回归）** |

**关键断言**：

- **T-1 `SEQ-GAP`**：挖洞 ⇒ 报 `SEQ-GAP:5`;`context.compacted.ranges` 声明覆盖 ⇒ **不报**
- **T-2 `NO-GUARD-EVENT`**：`cA`/`cC` 有 `tool.result` 无 `guard.evaluated` ⇒ 报;被拒的 `cB`（无 `tool.result`）**不报**;补 `guard.evaluated` ⇒ **不报**
- **T-3 `NO-RECEIPT-FOR-GRANT`**：① 有 D2 缺凭证 ⇒ 报;② **无 D2** ⇒ 报;③ 完整链路 ⇒ **不报**
- **T-4（★）默认零写**：findings **非空** + 默认 `emit=False` ⇒ **零写入**;`emit=True` ⇒ **恰一条** `syscheck.fail`（`findings` 一致、`trigger` 正确）
- **T-5**：clean log + `emit=True` ⇒ **零写入**
- **T-6 幂等**：连续两次 `emit=True` ⇒ findings **相同**
- **T-7 确定性**：不同实例 ⇒ findings 完全相等
- **T-9/T-10 legacy**：透传同一 session;未注入 ⇒ `CYC-999`
- **T-11**：AST 断言 `audit.py` **无** `telemetry` / `pyharness.core*` import
- **T-12**：`syscheck.fail` registered / 非瞬态 / **不在 SYNC**（**绝对计数**由 `test_events.py::test_vocab_full` 负责 —— 运行期注册表只增,全量跑时会被追加合成类型）
- **装配**：真实 spine 下 `legacy_session_audit == telemetry.session_audit`;默认 `reconcile` **零写**

*2 条 skip = 既有平台相关（`test_spill`）。未执行面：`desktop_native/**` / `test_shell_parity.py`（缺 `PySide6`,基线遗留 R-7）。*

---

## 9. 边界与冻结项检查

| 检查 | 结果 |
|---|---|
| **`EVENT_TYPES=77` / `SYNC_TYPES=14` / `TRANSIENT_TYPES=3`** | ✅ **全部不变**（**零 schema 变更**） |
| **`causal_chain` / `denied_report` 零修改** | ✅ 签名与行为均未变（既有 19 例全绿） |
| 未修改 `decision.py` / `receipt.py` / `policy.py` / `evidence.py` | ✅ 零命中 |
| 未修改 `core/**`（含 **`core/telemetry.py`**）/ `persistence.py` / `events/**` / `bus/**` | ✅ 零命中 |
| 未修改 ADR / `REFACTOR_PLAN.md` / `ARCHITECTURE_DECISION_RECORD.md` / `docs/` | ✅ 零命中 |
| 未新增事件类型 | ✅ 仅用既有 `syscheck.fail` |
| 未实现 `CACHE-STALE` | ✅ 已登记 deferred debt |
| 未实现 `TraceabilityMatrix`（S7）/ S6 内容 | ✅ 无 |
| Governance Core v1.0 冻结项 | ✅ 未触 |

---

## 10. deferred debt（本步新增 / 沿用）

| # | 项 | 说明 |
|---|---|---|
| **D-1（新）** | **`CACHE-STALE` 暂缓** | 设计已记录（比对派生视图 vs 日志重算）;延后原因：**避免 `AuditSystem` 第一阶段依赖 `EvidenceCollector` 内部结构**。前置条件：`EvidenceCollector` 提供稳定的只读快照面 |
| **D-2（新）** | `syscheck.fail` **非强同步** | findings 为**建议性**事实,可由重跑复得,不被任何不变量依赖 ⇒ 崩溃可丢,可接受 |
| D-3（沿用） | `reconcile` 为 **O(n) 全量重放** | 与 S5-3 同源;裁定接受（优先正确性/可复现性/单一真源） |

**本步产物**：`pyharness/governance/audit.py` · `pyharness/engine.py` · `tests/unit/test_governance_audit.py` · `tests/invariants/test_inv_governance.py` · 本报告。

---

## 11. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S6** | 不变量与测试面（M8/M9）：`tests/invariants/` 重写覆盖 INV-01~09 + INV-G1~G6;激活 `tests/acceptance/`、`tests/security/`（含"批准 A 执行 B"必拒） |
| **S7** | `TraceabilityMatrix` + 端到端 Demo（M10）+ 文档刷新（`MAP.md`/`README.md`） |

**等待人工 checkpoint。**
