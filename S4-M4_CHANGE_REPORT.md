# S4-M4_CHANGE_REPORT.md — 决策凭证（Decision Receipt）实施报告

> **阶段**：S4-M4（M4：DecisionReceipt emit / verify / 落盘）
> **日期**：2026-09-15 ｜ **基线 HEAD**：`34349b4`（S4-P1-3 checkpoint;worktree clean）
> **依据**：设计 §3.4（`DecisionReceipt` / `ReceiptStore`）· `docs/EVENT-SCHEMA.md:489,492`（`receipt.emitted` 冻结载荷 + INV-G1~G6）· ADR-018:318（`verify()` fail-closed）· M4 Entry Analysis 裁定 **R-1/R-2/R-3 = (a)**
> **性质**：**未 commit**。**不进入 M6/M7**（Evidence / Audit 未实现）。

---

## 0. 结论

# S4-M4 = PASS

**改动 11 文件 + 新增 3 文件**；全量回归 **1606 passed / 2 skipped / 0 failed**（较基线 1575 **+31**，零回归）。

---

## 1. Implementation Design（实施前已输出,此处复述要点）

| # | 项 | 落地 |
|---|---|---|
| 1 | 模块位置 | `pyharness/governance/receipt.py`（ADR-018 冻结目录） |
| 2 | domain object | `DecisionReceipt`（frozen;16 字段,含 `prev_hash`/`signature`） |
| 3 | `content_hash` 受保护集合 | `decision_id`/`kind`/`verdict`/`tool`/`inputs_digest`/`policy_fingerprint`/`principal_{kind,id,channel}`/`approval_ref`/`supersedes`/`prev_hash`;**排除**随机 `receipt_id` 与 append 时才产生的 `ts`/`event_seq` |
| 4 | payload 五字段 | `receipt_id`/`decision_id`/`kind`/`digest`/`prev_hash`（`digest` == `content_hash`,R-1(a)） |
| 5 | `prev_hash` 链 | 链头**由会话日志 replay 推出**（只读唯一真源）;内存仅为缓存,新 store 可续链 |
| 6 | 生成时机 | `approval_ref is not None` → `approval`;`verdict=APPROVAL 且 approval_ref is None`（D1）→ **不产生**;其余 → `decision` |
| 7 | `verify()` | 重算 `content_hash` + `kind`/`approval_ref` 语义自洽;**任一不符 → False（fail-closed）** |
| 8 | store 定位 | `get(receipt_id)` / `of_decision(decision_id) -> tuple`（**保复数语义**） |
| 9 | JSONL 唯一真源 | 持久化只有 `receipt.emitted`;索引与链头**均可由日志重建** |
| 10 | 修改文件 | 见 §3 |

**无新架构冲突。**

## 2. 数据流与 Receipt 生命周期

```
tool call (call.name + args)  →  _approval_binding  →  Decision.inputs_digest
        │
        ▼  authorize():  GuardChain → EvaluationResult → DecisionEngine.decide
decision.issued (sync;trace.call_id)
        │
        ▼  ReceiptStore.emit(ctx, decision, call_id=…)
receipt_kind_of(decision)
   ├ approval_ref != None      → kind="approval"   （D2）
   ├ verdict=APPROVAL & ref=None → **不产生**       （D1,审批请求决策）
   └ 其余                       → kind="decision"   （ALLOW / REJECT）
        │
        ▼  content_hash = sha256(canonical(受保护集合))   ← 发射前算定(确定性)
receipt.emitted (sync;5 字段;digest=content_hash;prev_hash=链头)
        │
        ▼
DecisionReceipt（运行时对象;可离线 verify();可由日志 rebuild_from_log 重建）
        ▼
后续 Evidence(S5)/Audit(S6) —— 本轮**未实现**
```

**生命周期**：`prev_hash` 由**日志中最后一条 `receipt.emitted.digest`** 决定（新 store 首次 emit 时 replay 推出）⇒ 链跨进程可续、可验、不可静默重排。

## 3. 修改文件

### 生产

| 文件 | 增/删 | 说明 |
|---|---|---|
| **`pyharness/governance/receipt.py`** | **新增 354 行** | `DecisionReceipt` / `ReceiptStore` / `verify_receipt` / `verify_chain` / `rebuild_from_log` / `content_hash_for` / `receipt_kind_of` |
| `pyharness/events/payload.py` | +23 / −1 | 新增 `ReceiptEmittedPayload`（5 字段）;头注 75→76 |
| `pyharness/events/vocab.py` | +9 / −4 | 注册 `receipt.emitted` + 入 `SYNC_TYPES` + 计数注释 75→76 |
| `pyharness/governance/context.py` | +8 / −1 | `authorize()` 在 decision.issued 后发射凭证;`receipts` 字段类型化 |
| `pyharness/governance/__init__.py` | +4 / −0 | 导出 `DecisionReceipt` / `ReceiptStore` / `verify_receipt` |
| `pyharness/engine.py` | +4 / −2 | 装配 `receipts=ReceiptStore()` |

### 纯注释（**AST-strip 逐字相同,零行为**）

| 文件 | 增/删 | 说明 |
|---|---|---|
| `pyharness/bus/event_bus.py` | +2 / −2 | 词表计数注释 74→**76**（M4 词表变更的直接维护后果） |
| `pyharness/core/approval.py` | +1 / −1 | 同上（`:78`） |
| `pyharness/core/tools_registry.py` | +1 / −1 | 同上（`:86`） |

> 这 3 处是**当前计数注释**,由词表 75→76 使其过期,**必须**同步（否则注释守卫 `test_s25_no_stale_vocab_count_in_source` 失败）。已用 AST 剥离 docstring 后比对确认**逐字相同**（纯注释）。

### 测试

| 文件 | 增/删 | 说明 |
|---|---|---|
| **`tests/unit/test_governance_receipt.py`** | **新增 221 行** | 20 例:产生规则 / 确定性哈希 / emit / 链 / verify / rebuild |
| **`tests/invariants/test_inv_governance.py`** | **新增 194 行** | INV-G4/G5/G6 + INV-R1/R3/R4/R5/R8 + 全生命周期 |
| `tests/unit/test_tools_executor.py` | +124 / −3 | `TestReceiptEmission`（A/B/C/D 四场景,含真实 JSONL replay）;`make_ctx(receipts=True)` |
| `tests/unit/test_events.py` | +11 / −8 | 计数 76 / SYNC 14;守卫 `_ALLOWED_COUNTS={"76"}` |
| `tests/unit/test_governance_decision.py` | +5 / −2 | 门面导出断言随阶段演进（凭证已导出;Evidence/Audit 仍未） |

## 4. hash 算法与 prev_hash

| 项 | 实现 |
|---|---|
| 规范化 | **复用 `policy.compute_fingerprint` 同款规则**：`json.dumps(sort_keys=True, ensure_ascii=False, separators=(",",":"), default=str)` ——**未新建第三套** |
| `content_hash` | `sha256(canonical(受保护集合))`——**确定性**（同事实 ⇒ 同哈希,INV-R3） |
| `inputs_digest` | **不变**（仍为 `_approval_binding` 语义;由 `decision_id` 派生,**不**参与 receipt 自身哈希） |
| `prev_hash` | 前一条 `content_hash`;创世 `None`;**链头由日志恢复**（不依赖内存真源） |

## 5. `verify()`

| 检查 | 行为 |
|---|---|
| ① `content_hash` 重算一致 | 任一受保护字段被改 ⇒ **False** |
| ② `kind` 与 `approval_ref` 自洽 | `approval` 必须带 ref;`decision` 必须不带 |
| ③ `kind` 合法 | 非法 kind ⇒ False |
| fail-closed | **返回 False ⇒ 调用方必须拒绝执行**（ADR-018:318 / INV-05 纪律） |
| `verify_chain(tuple)` | 逐条 verify + `prev_hash` 指向前一条（断链/重排 ⇒ False） |

## 6. `receipt.emitted`

- **已注册** · **∈ `SYNC_TYPES`** · **非 transient**（`EVENT_TYPES=76` · `SYNC_TYPES=14`）
- 载荷**恰 5 字段**（INV-G4）;`digest` == `content_hash`（R-1(a)）
- 强同步 ⇒ **继承 F-SYNC-1 修复后的"失败不静默成功 + 失败批不丢"契约**
- **无第二 persistence**：唯一写动作 = `session.append("receipt.emitted", …)`

## 7. Invariants 覆盖

| 不变量 | 覆盖位置 |
|---|---|
| **INV-G1**（执行前必有 decision.issued） | `test_inv_g1_g2_r2_r6_r7_full_approval_lifecycle`（生命周期事实面）+ 既有 executor 事件序用例 |
| **INV-G2**（每条 approval.granted 必对应一条 receipt） | 同上（计数 + 唯一可追踪） |
| **INV-G3**（重放后 verify 不变） | `rebuild_from_log` + `verify_chain`；`test_case_d_d2_emits_approval_receipt`（真实 JSONL 关闭重开） |
| **INV-G4**（只含引用与哈希） | `test_inv_g4_receipt_is_reference_and_hash_only` |
| **INV-G5**（治理层无执行 API） | `test_inv_g5_governance_has_no_execution_api` |
| **INV-G6**（强同步失败不 silent success） | `test_inv_g6_receipt_event_inherits_sync_contract` + `TestSyncDurability`（F-SYNC-1） |
| **INV-R1** 唯一绑定 decision_id | `test_inv_r1_…` |
| **INV-R2** 可 replay | `test_r2_rebuild_from_log_and_chain` · `test_inv_g1_g2_…` |
| **INV-R3** 确定性哈希 | `test_r3_same_facts_same_hash` / `changes_with_protected_facts` / `prev_hash_binds_chain` |
| **INV-R4** 篡改必败 | `test_r4_tamper_any_protected_field_fails` · `test_inv_r3_r4_r8_hash_tamper_chain` |
| **INV-R5** 非第二真源 | `test_inv_r5_no_second_persistence_source` · `test_chain_survives_store_recreation` |
| **INV-R6** granted→approval_ref→receipt 唯一 | `test_inv_g1_g2_…` |
| **INV-R7** D1 不产生凭证 | `test_r7_d1_produces_no_receipt` · `test_case_c_d1_emits_no_receipt` |
| **INV-R8** 链可验/不可断链 | `test_inv_r3_r4_r8_…` · `verify_chain` |

> **口径说明（不夸大）**：INV-G1 的**运行时强制点**仍是 `tools_executor._require_wiring` + `authorize()` 的不变发射；本文件的 G1 用例验证的是**事件事实面**（`decision.issued` 在授权路径上必然产生且携带 `call_id`），非逐工具一一枚举。

## 8. 测试结果

| 项 | 结果 |
|---|---|
| M4 专项（`test_governance_receipt.py`） | **20 passed** |
| Invariants（`tests/invariants/`） | **14 passed** |
| `test_tools_executor.py` | 全绿（含 `TestReceiptEmission` 4 例） |
| **全量回归** | **1606 passed / 2 skipped / 0 failed** |
| 相对基线 1575 | **+31** |

## 9. 只读核验

| 检查 | 结果 |
|---|---|
| `git diff --stat` 与报告一致 | ✅ 11 修改 + 3 新增 |
| 3 个注释文件确认为**纯注释** | ✅ AST 剥离 docstring 后 **逐字相同**（3/3 SAME） |
| `approval_ref` / `inputs_digest` / `Principal` 语义 | ✅ 未改（仅 `approval.py` 一行**注释**） |
| `Decision` 契约 | ✅ 未改（`decision.py` 零改动） |
| GuardChain | ✅ `tools_guard.py` 零改动 |
| F-SYNC-1 | ✅ `persistence.py` 零改动 |
| D1/D2 时序 | ✅ 未改（D2 仍为 APPROVAL） |
| Evidence / Audit | ✅ **未实现**（`governance/` 现 5 模块:`+receipt.py`） |
| 第二套 hash / 第二持久化 / DAG / IAM | ✅ 均无 |

## 10. 与冻结设计的符合性

| 冻结项 | 符合 |
|---|---|
| `receipt.emitted` 载荷 = 5 字段（EVENT-SCHEMA:489） | ✅ 逐字段一致 |
| `DecisionReceipt` 字段（设计 §3.4） | ✅ 全覆盖（`prev_hash`/`signature` 亦实现,`signature` v1.0 留 `None`） |
| `of_decision -> tuple`（保复数） | ✅ 未强行一对一 |
| `verify()` fail-closed（ADR-018:318） | ✅ |
| `digest_of` = `_approval_binding` | ⚠️ **未实现该 Protocol 方法**——见 §11 |
| 强同步 + JSONL 唯一真源（INV-G4/R5） | ✅ |

## 11. Deferred debt（新增）

| # | 项 | 说明 |
|---|---|---|
| **D-1** | **`ReceiptStore.digest_of` 未实现** | 设计 §3.4 列该 Protocol 方法（= `_approval_binding`）。实现它需 `import tools_executor`（违 ADR-018:308）或复制算法（违"不建第二套"）。**`inputs_digest` 已由 S4-P1-1 经装配层注入进入 `Decision`**,功能上已满足 ⇒ 本步**最小化取舍**,登记为待后续（若需对外暴露 digest 计算,应以**注入 callable** 方式提供,与 `chain_factory` 同型）。 |
| **D-2** | `tests/invariants/` 的 INV-G1 覆盖口径 | 见 §7 口径说明（事实面而非逐工具枚举）；如要求更强,可在 S6 补逐工具不变量测试。 |
| **D-3** | `content_hash` 不含 `ts`/`event_seq`/`call_id` | 因 `ts`/`event_seq` 于 append 时才产生（与"发射前算定 + 确定性"冲突）。**副作用**:篡改这三个**非受保护**字段不会被 `verify()` 检出——`call_id` 的完整性由 `decision.issued.trace` 与 replay 承担。已在 `receipt.py` 明文记录。 |

---

**本步产物**：`pyharness/governance/receipt.py` · `pyharness/events/payload.py` · `pyharness/events/vocab.py` · `pyharness/governance/context.py` · `pyharness/governance/__init__.py` · `pyharness/engine.py` · `pyharness/bus/event_bus.py`(注释) · `pyharness/core/approval.py`(注释) · `pyharness/core/tools_registry.py`(注释) · `tests/unit/test_governance_receipt.py` · `tests/invariants/test_inv_governance.py` · `tests/unit/test_tools_executor.py` · `tests/unit/test_events.py` · `tests/unit/test_governance_decision.py` · 本报告。

---

## 12. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **M6** | Evidence（`evidence.py` + `evidence.archived`;INV-G4 引用面） |
| **M7** | Audit（`audit.py`;`causal_chain`） |

**等待人工 checkpoint。**
