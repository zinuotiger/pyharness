# S6-2b-2_COVERAGE_AUDIT.md — INV-03 覆盖审计（Phase A~E）

> **阶段**：S6-2b-2（INV-03 Coverage Audit）｜ **日期**：2026-09-15 ｜ **基线 commit**：**`9349596`** ｜ HEAD `9349596` ｜ ahead 35
> **性质**：**只读审计**。**未新增/修改任何测试**；**未修改任何生产代码 / Registry / Event Schema / Governance Core**；**未 commit、未 push**。
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-03）—— **未据旧测试注释重新解释**。
> **基线实测**：全量 **1682 collected / 0 failures / 0 errors / 2 skipped = 1680 passed** · `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`

---

## 1. INV-03 Canonical Definition（照录）

> `docs/INVARIANT_REGISTRY.md` §1 · Evidence Source: `PRD-Core.md:838,308,360` · `CONSTRAINTS-06:64` · `SECURITY.md:198,210` · `EVENT-SCHEMA.md:503,526` · `DIS-CORE.md:433,471,481` · 实现 `session.py:70,143,338,396,473` · `session_query.py:32,57,678`

**INV-03 = `rebuild_from_log` / `rebuild_from_events` 后，派生缓存与重建前逐事件（逐行）一致；含增量读一致（`events_after(last)` == 全量切片）、编辑重放（`edited` 覆盖目标行）、二次 rebuild 无重复；`history_cache` 仅当日志尾部未变时有效，任何 `append` 后整体失效。**

**拆解为 9 条可判定子性质**（下文逐条判定）：

| # | 子性质 |
|---|---|
| 1 | 全量 rebuild 与增量结果一致 |
| 2 | 二次 rebuild 不产生重复 |
| 3 | 编辑后的状态重新 rebuild 正确 |
| 4 | cache invalidation（缓存失效）行为正确 |
| 5 | rebuild 后状态与事件重放结果一致 |
| 6 | rebuild 不产生错误副作用 |
| 7 | rebuild 的确定性 |
| 8 | 空数据 / 新 session / 边界 session 行为 |
| 9 | Registry 明确要求但当前无直接测试的性质 |

**范围界定**：Registry 的 Evidence Source 只列 `session.py` 与 `session_query.py` ⇒ **INV-03 的覆盖对象 = 会话日志内存缓存 + 查询投影索引**。`goal.py` / `plan_mode.py` / `schedule.py` / `todo.py` 各自的 `rebuild_from_events` 属**模块级事件溯源**，**不在 INV-03 的 Registry 证据范围内**（各有独立测试，见 §3.3 备注）。

---

## 2. 现有实现证据（Phase B · 读实现，非读注释）

### 2.1 `SessionLog.rebuild_from_log()`（`session.py:395-417`）

| 审计问 | 实测答案 | 行号 |
|---|---|---|
| **输入** | 无入参（不接受外部数据） | `:395` |
| **数据源** | **唯一** = `self._persistence.replay()`（磁盘 JSONL） | `:408` |
| **缓存的事实来源** | 仅日志；重建时**先整体清空**再重放 | `:401-407` |
| **是否纯派生** | ✅ **是** —— 只读 `replay()`，**无任何写回**（全程无 `append`/`flush`/文件写） | `:408-417` |
| **清除的派生状态** | `_cache` · `_seqs` · `_folded` · `_holes_warned` · `_seq` · `history_cache` · `_closed` · `_seq_state` | `:401-407` |
| **重放后收尾** | `_seq_state.rebuild(sid, _seq)`（续写点 max+1，空洞不回填）+ `_recompute_terminal()`（终态按日志事实重算） | `:415-417` |
| **多会话隔离** | 过滤 `env.session_id != self.sid`（轮转合并兜底） | `:410-411` |
| **坏行** | 由 `persistence.replay` 记 PERS-201 隔离，**不中断** | `:405-406` |

### 2.2 `SessionLog` 增量读与缓存（`session.py:344-371`）

| 审计问 | 实测答案 | 行号 |
|---|---|---|
| **incremental 与 rebuild 的关系** | **同源**：`events_after` 走 `_cache`（增量内存态），`rebuild_from_log` 从磁盘重放**重建同一 `_cache`** ⇒ 二者是"同一事实的两种读取路径" | `:362-371` / `:395-417` |
| **是否存在第二状态来源** | ✅ **否** —— `history_cache` 是**可弃派生**（append/rebuild 均置 `None`）；`_cache` 由日志重建；`_seq_state` 由 `_seq` 重建 | `:339,352-356,406` |
| **stale cache 是否可能** | **两条失效点**：① `append` 步骤 10 置 `history_cache = None`（`:339`）；② `rebuild_from_log` 置 `None`（`:406`）。**且** `_ensure_rebuilt()` 保证"有持久化但未回放"时先重建（`:125-126`） | `:125-126,339,406` |
| **重复应用事件** | ⚠️ 见 §2.4 | — |
| **顺序依赖** | `replay()` 按 `_rotated_paths()`（序号升序）+ 主文件 ⇒ **seq 升序**；`_absorb` 顺序追加 ⇒ `_seqs` 单调 ⇒ `bisect` 前提成立 | `persistence.py:481` |
| **`derive_messages` 缓存纪律** | 仅当 `max_tokens is None` 时用/填 `history_cache`；返回**浅拷贝** `[dict(m) for m in ...]` 防外部改写缓存 | `:352-356` |

### 2.3 `session_query.rebuild()`（查询投影，`session_query.py:890-935`）

| 审计问 | 实测答案 |
|---|---|
| **是否纯派生** | ✅ 只重建**派生索引**；`DELETE` 后从真源重放重插（"派生可弃，JSONL 一行不改"） |
| **原子性** | ✅ `DELETE` + 重插 + **幂等闸重填**在**同一事务**（`with self._db`）内完成 |
| **幂等闸** | 显式重填 `fts_rows(session_id, seq)` —— 注释记明"偏离 6 修复：rebuild 只清不填会让后续增量 flush 绕过闸产生重复行" |
| **前置 flush** | ✅ `await self.flush()` 先清缓冲，防新旧混写 |

### 2.4 ⚠️ 审计发现：`replay()` **不做 seq 去重**（**非确认缺陷**，见 §10）

| 事实 | 证据 |
|---|---|
| **读路径无去重** | `SessionStore.replay()`（`persistence.py:473-504`）**逐行 yield**，不按 `seq` 去重 |
| **写路径有去重** | `_resolve_by_seq()`（`persistence.py:64-88`）按 seq 去重且**同 seq 异内容 → PERS-202 fail-closed**；仅用于写路径（`:398`） |
| **⇒ 不对称** | 若日志出现**同一 seq 两行**，`rebuild_from_log` 会**静默重复吸收**（`_absorb` 无条件 append）⇒ `_seqs` 非严格单调 |
| **可达性评估** | **未能从任何正常路径达成**：`_rotate()` 用 `os.replace`（整文件原子移动，**不复制**，`persistence.py:640`）· `_rewrite_without_tail` 截尾（不复制）· `quarantine_lines` 写副本（不改主日志完好行）⇒ 正常 rotation/repair **不产生重复 seq** |
| **代码库的既有假设** | `seq_holes()` docstring 明写"重复/倒退…**append 保证单调**"（`persistence.py:652-653`）⇒ 单调性是**构造性假设**，由写路径的去重与 seq 分配器保证 |
| **判定** | **健壮性观察（hardening opportunity）**，**未确认为真实生产问题**（不可达）⇒ **本轮只记录，不修复、不建 KEY-FINDING** |

---

## 3. 现有测试证据（Phase A · 只读盘点）

### 3.1 INV-03 直接相关用例（Registry Evidence Source 范围内）

| 文件 | 用例 | 覆盖的子性质 |
|---|---|---|
| `tests/unit/test_session.py` | `test_rebuild_from_log_invariant_inv03` | 1 · 2 · 5（+ 隐式 6/7） |
| | `test_rebuild_derived_cache_invalidation_on_append` | 4 |
| | `test_events_after_incremental_gwt_s3_04` | 1（增量侧） |
| | `test_open_session_recovers_state` | 5（重启恢复 ≡ 崩溃前；续写 seq 不重复不空洞） |
| | `test_open_session_empty_store_is_new_session` | 8（空存储） |
| | `test_open_session_filters_other_sessions` | 8（多会话过滤） |
| | `test_derive_edited_overrides_gwt_s3_03` | 3（**仅派生侧，未经 rebuild**） |
| | `test_append_seq_monotonic_no_holes` · `test_get_single_and_hole_none` | 5（相邻：seq 单调/空洞语义） |
| `tests/unit/test_session_query.py` | `test_rebuild_full_matches_incremental` | **1 · 2 · 3 · 5**（含 `edited` 事件 + rebuild 后新旧词命中断言） |
| | `test_rebuild_then_incremental_no_dup` | **2**（二次 rebuild 无重复行） |
| | `test_rebuild_single_session_scope` | 8（单会话作用域） |
| | `test_rebuild_unknown_session_noop` | 8（未知会话 noop） |
| | `test_rebuild_not_entered_pers_202` | 8（未装载边界） |
| | `test_edited_updates_target_row` · `test_edited_malformed_skipped` | 3（编辑→索引行更新） |
| | `test_replay_source_invalid_pers_201` | 6（坏源不崩，隔离） |
| `tests/invariants/test_inv_core.py` | `test_inv01_*`（4 条） | **INV-01**，非 INV-03（INV-01 的"派生视图可整体重建"与之相邻，但不构成本审计证据） |

### 3.2 **无** INV-03 编号化用例

`tests/invariants/` 内**不存在** `INV-03` 标注用例（与 §1 的 Registry `Coverage Gap` 记载一致）。

### 3.3 范围外（**不计入 INV-03 证据**）

`test_goal.py`（6）· `test_plan_mode.py`（5）· `test_schedule.py`（3）· `test_todo.py`（2）· `test_governance_evidence.py::test_t2_rebuild_from_log_matches_subscription_state` · `test_governance_receipt.py::test_r2_rebuild_from_log_and_chain` · `test_events.py::test_seqstate_rebuild_and_next` · `test_repair.py`（7）—— 均为**模块级事件溯源 / 治理层派生**，各有独立语义，**不属于 INV-03 的 Registry 证据范围**（不据此提高 INV-03 覆盖判定）。

---

## 4. 子性质 Coverage Matrix（Phase C）

> 判定标准（依人工要求）：**"若该性质被破坏，现有测试是否一定会失败？"** —— 回答"否/不一定"者**不得**标 `covered`。

| # | 子性质 | 状态 | 直接证据 | **破坏时现有测试是否必红？** |
|---|---|---|---|---|
| **1** | 全量 rebuild 与增量结果一致 | ✅ **covered** | `test_rebuild_from_log_invariant_inv03`（`after_evs == before_evs`）· `test_events_after_incremental_gwt_s3_04`（增量 == 全量切片，逐一 k 验证）· `test_session_query.py::test_rebuild_full_matches_incremental`（rebuild 表 == 增量表，**行 + 幂等闸双比对**） | ✅ **是**（破坏 rebuild 一致性 ⇒ 断言 `==` 直接失败） |
| **2** | 二次 rebuild 不产生重复 | ✅ **covered** | `test_rebuild_from_log_invariant_inv03:500-503`（append → **再 rebuild** → 仍一致）· `test_rebuild_then_incremental_no_dup`（行键唯一 + 二次 rebuild 后增量不重复） | ✅ **是**（重复 ⇒ 长度/逐事件比对失败） |
| **3** | 编辑后的状态重新 rebuild 正确 | ⚠️ **partial** | **query 侧 ✅**：`test_rebuild_full_matches_incremental`（语料含 `edited(SID,6,1,…)`，rebuild 后断言 `query("照片")=={1}`、`query("桌面")=={}`）· **session 侧 ❌**：`test_derive_edited_overrides_gwt_s3_03` **不调用 `rebuild_from_log()`** | ⚠️ **query 侧是；session 侧否** —— 若 `rebuild_from_log` 对 `user.message_edited` 处理错（如漏吸收/顺序错），**无任何会话侧测试会失败** |
| **4** | cache invalidation 行为正确 | ✅ **covered** | `test_rebuild_derived_cache_invalidation_on_append`（append 后 `history_cache is None` + 派生反映新事件）· `test_session.py:602` 同款断言 | ✅ **是**（不置 `None` ⇒ `is None` 断言直接失败） |
| **5** | rebuild 后状态与事件重放结果一致 | ✅ **covered** | `test_rebuild_from_log_invariant_inv03`（重建自磁盘重放后与缓存逐事件一致 + `stats()` **精确字典比对**）· `test_open_session_recovers_state`（**全新对象**从磁盘 replay ⇒ `events_after()`/`derive_messages()`/`get(2)` 全等） | ✅ **是** |
| **6** | rebuild 不产生错误副作用 | ⚠️ **partial** | **隐含**：`:495` `after_evs == before_evs`（若 rebuild 往日志写事件，重放结果会多出事件而失败）· `test_open_session_recovers_state` 的"续写 seq = len+1"（无幽灵事件）· `test_replay_source_invalid_pers_201`（坏源隔离不崩） | ⚠️ **大概率会红但非直接断言** —— **无**"rebuild 后真源文件字节/行数不变"的**显式**断言 |
| **7** | rebuild 的确定性 | ⚠️ **partial** | **隐含**：query 侧 `test_rebuild_full_matches_incremental`（rebuild == 增量 ⇒ 确定性的一种形式）· session 侧 `:500-503` 为"append → rebuild"非"rebuild → rebuild" | ⚠️ **否** —— **无**"连续两次 rebuild 输出恒等"的会话侧断言 |
| **8** | 空 / 新 / 边界 session | ✅ **covered** | `test_open_session_empty_store_is_new_session`（空存储 ⇒ seq=0、event_count=0）· `test_open_session_filters_other_sessions` · `test_session_query.py::test_rebuild_unknown_session_noop`（无源 ⇒ 0 行且不动既有）· `::test_rebuild_not_entered_pers_202`（未装载 ⇒ 结构化错误）· `::test_rebuild_single_session_scope`（单会话不清他会话） | ✅ **是** |
| **9** | Registry 明确要求但无直接测试 | 见 #3(session 侧) · #6 · #7 | Registry 三段要求中：**增量读一致 ✅**（`test_events_after_incremental_gwt_s3_04`）· **编辑重放 ⚠️**（session 侧缺）· **二次 rebuild 无重复 ✅** | — |

**汇总**：`covered` **5** · `partial` **3**（#3 · #6 · #7）· `gap` **0** · `deferred` **0**（+ 1 条健壮性观察，见 §2.4/§10）。

---

## 5. True Gaps（真实缺口）

**无 `gap` 级缺口。** 三条 `partial` 中，**#7（确定性）最接近 gap**（破坏它必然有**任何**现有测试失败吗？—— 不一定，见下），故与 #3(session 侧) / #6 一并列入 §6。

---

## 6. Partial Properties（部分覆盖，逐条说明"为什么不算 covered"）

| # | 子性质 | 为什么不是 covered | 破坏时的实际后果 |
|---|---|---|---|
| **P-1** | 编辑后 **session 侧** rebuild 正确 | `test_derive_edited_overrides_gwt_s3_03` 只验"derive 取新版"，**未经过 `rebuild_from_log`**；会话侧无"含 edited 事件的日志 → rebuild → 派生仍取新版"的断言（**query 侧有**，但那是另一个派生视图） | `rebuild_from_log` 若对 `user.message_edited` 漏吸收/错序，**全部现有测试仍绿** —— 静默错误 |
| **P-2** | rebuild **无错误副作用** | 现有为**隐含**断言（结果比对 + 续写 seq），**无**"rebuild 前/后真源文件字节或行数恒等"的显式断言 | rebuild 若意外向日志写入（多写/重写），`after_evs == before_evs` **很可能**捕获，但**非设计上的直接防线** |
| **P-3** | rebuild **确定性** | 会话侧无"**连续两次 rebuild**（输入相同）⇒ 输出恒等"的断言；现有 `:500-503` 是"append → rebuild"（输入**已变**） | 若 rebuild 依赖外部易变状态（如字典序、时间、遍历序），现有测试**不会**失败 |

---

## 7. Deferred Items（延期项）

| # | 项 | 延期理由 |
|---|---|---|
| **D-1** | `replay()` 的 **seq 去重**（与写路径 `_resolve_by_seq` 对称化） | **不可达**（正常 rotation/repair 不产生重复 seq）⇒ 属 hardening，非缺陷；且会触及 `persistence.py` 生产代码 ⇒ **本轮不改、后续独立评估** |
| **D-2** | `session_query` 的 **性能/规模**面（rebuild 全表 DELETE + 重插的 O(n) 代价） | 属性能议题（`reconcile` O(n) 已登记在 deferred debt），与 INV-03 的**正确性**语义无关 |
| **D-3** | `INV-03` 的**跨进程** rebuild（一进程 rebuild、另一进程并发 append） | 需多进程测试设施；`INV-07` 已覆盖单写者互斥；并发语义属独立评估 |

---

## 8. 最小测试设计（Phase D · **只设计，不创建**）

> 原则：只补 §6 的 3 条 `partial`，**每条不超过 1 个用例**；**不重复** `test_rebuild_then_incremental_no_dup` / 全量↔增量一致 / 编辑重放（query 侧）/ cache invalidation（均已被 S6-1/S6-2a 核验过，只建映射）。
> 建议落点：`tests/invariants/test_inv_core.py`（与 INV-01/02 同文件，S6-2b-2 实施阶段执行）。

| # | 目标 | 用例（建议名） | 设计要点 | 对应 partial |
|---|---|---|---|---|
| **T-1** | 编辑后 **session 侧** rebuild 正确 | `test_inv03_rebuild_preserves_edit_override` | 真实 `SessionLog` + `_store_wired`（复用 `test_session.py` 的接线方式）；append `user.message` → append `user.message_edited{target_seq, new_content}` → 记录 `derive_messages()`；`rebuild_from_log()` → 断言 ① 派生**逐条恒等**（仍取新版）② 日志**两行原文都在**（`events_after()` 类型序列 + 原文字段未被改写） | **P-1** |
| **T-2** | rebuild **无副作用**（真源不变） | `test_inv03_rebuild_does_not_touch_truth_source` | 真实 store + `tmp_path`；`_conversation(log)` → 记录 `store.path.read_bytes()` 与 `sha256` → `rebuild_from_log()` → 断言 ① **文件字节逐字恒等** ② `len(list(store.replay()))` 不变 | **P-2** |
| **T-3** | rebuild **确定性** | `test_inv03_rebuild_is_deterministic` | 同一 `SessionLog` 连续 `rebuild_from_log()` **两次**；断言两次快照恒等 = `(list(events_after()), derive_messages(), stats())` | **P-3** |

**不新增的部分（只建映射）**：

| 已充分覆盖的性质 | 既有用例（**不重复建设**） |
|---|---|
| 二次 rebuild 不产生重复 | `test_session.py::test_rebuild_from_log_invariant_inv03` · `test_session_query.py::test_rebuild_then_incremental_no_dup` |
| 全量 ↔ 增量一致 | `test_events_after_incremental_gwt_s3_04` · `test_session_query.py::test_rebuild_full_matches_incremental` |
| 编辑重放（**query 侧**） | `test_session_query.py::test_rebuild_full_matches_incremental` · `::test_edited_updates_target_row` |
| cache invalidation | `test_session.py::test_rebuild_derived_cache_invalidation_on_append` |
| 重启恢复 ≡ 崩溃前 | `test_session.py::test_open_session_recovers_state` |
| 空/新/边界 session | `test_session.py::test_open_session_empty_store_is_new_session` · `test_session_query.py::test_rebuild_unknown_session_noop` 等 |

---

## 9. Mutation Design（Phase E · **只设计，不执行生产代码修改**）

| # | Mutation（最小） | 目标测试 | 预期 | 安全性/代价评估 |
|---|---|---|---|---|
| **M-1** | `rebuild_from_log` 的重放循环**少消费一个事件**（如首条 `continue`，或循环末尾 `break`） | T-1 · T-3 · 既有 `test_rebuild_from_log_invariant_inv03` | **RED**（`after_evs` 短 1 条） | **安全**：1 行、仅影响 rebuild 路径、易还原 |
| **M-2** | `rebuild_from_log` 对 `user.message_edited` **跳过吸收**（`if env.type == "user.message_edited": continue`） | **T-1**（专为目标） | **RED**（派生回退旧版） | **安全**：1 行；**这是唯一能专门证明 T-1 区分力**的 mutation ⇒ **首选** |
| **M-3** | 重放**顺序反转**（`for env in reversed(list(self._persistence.replay()))`） | 既有 `test_rebuild_from_log_invariant_inv03` · T-3 | **RED**（逐事件比对的**顺序**不符） | **安全**：1 行；同时证明"顺序依赖"面被守护 |
| **M-4** | `_absorb` 在一次重放中被**调用两次**（重复应用） | 既有 `test_rebuild_from_log_invariant_inv03` · T-3 | **RED**（事件数翻倍） | **安全**：1 行；**注意**若改在 `_absorb` 内部会同时影响 append 路径 ⇒ 应改在**重放循环内**调用两次，把影响面限制在 rebuild |
| **M-5** | `append` 步骤 10 **移除** `self.history_cache = None` | `test_rebuild_derived_cache_invalidation_on_append` · `test_session.py:602` | **RED**（stale cache 被复用） | **安全**：1 行；证明 cache invalidation 面被守护 |
| **M-6** | `rebuild_from_log` 末尾**改写真源**（追加/截断文件） | **T-2** | **RED**（字节/行数变化） | ⚠️ **代价偏高**：需注入跨层写调用（`rebuild_from_log` 为同步、存储写为异步）⇒ mutation 形态不自然、与真实缺陷距离远。**建议**：T-2 的区分力改由 **M-1/M-3 的结果面**间接佐证 + T-2 保留为"**显式防线**"（不必专属 mutation） |

**结论**：
- **M-2 是 T-1 的最佳最小 mutation**（唯一能专门触发"编辑处理错"这一分支）。
- **M-1 / M-3 / M-4 / M-5** 均为 1 行、安全、易还原，可分别证明 4 条目标测试/既有测试的区分力。
- **M-6（T-2 专属）代价过高且不自然** ⇒ 依人工要求"如果 mutation 不安全或代价太高，只说明原因"，**说明并建议以相邻 mutation 间接佐证**。
- **确定性（#7）无自然的最小 mutation** —— 破坏确定性需引入非确定性（如依赖遍历序/时间），与真实缺陷形态距离远；建议以 **M-1/M-3**（漏消费/顺序错）间接证明 T-3 的鉴别力。

---

## 10. 是否发现真实生产问题

| 项 | 判定 |
|---|---|
| **#2.4 `replay()` 无 seq 去重** | ⚠️ **健壮性观察，非确认缺陷** —— 与写路径 `_resolve_by_seq` **不对称**；但经**可达性评估**（rotation 用 `os.replace` 不复制 · repair 只截尾/隔离不复制）**未能从任何正常路径产生重复 seq**；代码库亦以"append 保证单调"为**构造性假设**。⇒ **本轮只记录，不改、不建 KEY-FINDING** |
| **其余实现审计项** | ✅ **无问题** —— rebuild **纯派生**（无写回）· 无第二状态来源 · 两条 cache 失效点齐备 · `_ensure_rebuilt` 兜底 · 顺序由 `replay()` 的轮转+主文件次序保证 · query 侧 rebuild 原子 + 幂等闸重填 |
| **结论** | **未发现真实生产缺陷** ⇒ **无新增 KEY-FINDING**（与人工已接受的 INV-01 处理口径一致） |

---

## 11. 对后续 S6-2b-2 实施的建议

| # | 建议 |
|---|---|
| **1** | 实施阶段**只新增 §8 的 T-1 / T-2 / T-3 三条**，落 `tests/invariants/test_inv_core.py`；**不重复建设** §8 末表中已充分覆盖的 6 类性质 |
| **2** | 每条新用例**附判据自检**（沿用 S6-2b-1 的 T2/T4 体例），并把 §9 的 **M-2（T-1）· M-1/M-3（T-3）· M-5（既有 cache 用例）** 作为实施阶段的 mutation check 执行一次并留证；**M-6 不执行**（说明理由） |
| **3** | 实施后 `S6-1` 的 Registry `INV-03` 行**不必修改**（其 `Coverage Gap` 表述与此审计一致）；如需回填，须走人工授权 |
| **4** | **D-1（replay 去重）** 建议另立独立小项评估（可与 durability/reliability 阶段合并），**不在 S6-2b-2 内实施** |
| **5** | 本审计的 `status` 为 **`partial`**（3 条 partial、0 条 gap）⇒ **INV-03 不得标 `covered`**，与 INV-01 的处理口径一致 |

---

## 12. 边界声明

**做了**：只读盘点 INV-03 的全部实现与测试证据 · 拆解 9 条子性质并逐条判定 · 读实现核验（输入/数据源/纯派生/第二状态/陈旧缓存/重复应用/顺序依赖）· 覆盖分类（5 covered / 3 partial / 0 gap）· 最小测试设计（3 条）· mutation 设计（6 条，含 1 条"代价过高"的说明）· 1 条健壮性观察（附可达性评估）。

**没做**（本阶段明令禁止）：❌ 修改 Python 生产代码 · ❌ 修改现有测试 · ❌ **创建新测试** · ❌ 修改 Registry / Event Schema / Governance Core · ❌ 修复 KF-B / L-3 / F042 · ❌ 增强 INV-01 scanner · ❌ 进入 INV-04 / S6-3 · ❌ commit · ❌ push。

**产出**：本文件（`S6-2b-2_COVERAGE_AUDIT.md`）唯一。

**worktree**：仅 `?? S6-2b-2_COVERAGE_AUDIT.md`；**HEAD 仍 `9349596`**。

---

**S6-2b-2 Phase A~E 结束。等待人工 review；未新增测试、未改代码、未 commit、未 push。**
