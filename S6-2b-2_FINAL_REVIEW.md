# S6-2b-2_FINAL_REVIEW.md — INV-03 覆盖最终审查

> **阶段**：S6-2b-2 Final Review（**只读独立核验**）
> **审查对象**：S6-2b-2 产出（`tests/invariants/test_inv_core.py` 的 INV-03 段 +134/−0 · `S6-2b-2_COVERAGE_AUDIT.md` · `S6-2b-2_CHANGE_REPORT.md`）
> **日期**：2026-09-15 ｜ **基线 commit**：`9349596` ｜ HEAD `9349596` ｜ ahead 35
> **本文件性质**：**只读审查**。**未新增测试 · 未修改生产代码 / Registry / Event Schema / Governance Core**；**未重跑生产代码 mutation**；**未 commit、未 push**（Freeze 提交在判定后执行）。
> **核验方式**：**独立复算** —— 重读测试源码、重跑定向与全量、独立清理与残留扫描、独立读 `stats()` 与冻结面；**不引用**变更报告的自述。

---

## 1. INV-03 Canonical Definition（**唯一权威来源，未被重新解释**）

> `docs/INVARIANT_REGISTRY.md`（**自 `9349596` 起 0 改动**，独立核验）

**INV-03 = `rebuild_from_log` / `rebuild_from_events` 后，派生缓存与重建前逐事件（逐行）一致；含增量读一致（`events_after(last)` == 全量切片）、编辑重放（`edited` 覆盖目标行）、二次 rebuild 无重复；`history_cache` 仅当日志尾部未变时有效，任何 `append` 后整体失效。**

| 核验 | 结论 |
|---|---|
| 本阶段是否重新解释 INV-03 | ❌ **否** —— 全部用例的语义标注均取自上述定义的分句；**未修改 Registry**（独立核验 `git diff --name-only 9349596 -- docs/INVARIANT_REGISTRY.md` = **0**） |
| 范围界定是否被扩大 | ❌ **否** —— 覆盖对象仍为 `session.py`（会话内存缓存）与 `session_query.py`（查询投影），与 Registry 的 Evidence Source 一致 |

---

## 2. 8 个 `covered` 子性质（**逐条独立判定**）

> 判定口径（依人工要求）：**"若该性质被破坏，对应测试是否具有足够鉴别力而应当失败？"** —— 不因"测试存在"自动判 `covered`。

| # | 子性质 | 状态 | 决定性证据 | 破坏时是否必红 |
|---|---|---|---|---|
| **1** | 全量 rebuild 与增量结果一致 | ✅ covered | `test_session.py::test_rebuild_from_log_invariant_inv03`（`after_evs == before_evs`）· `::test_events_after_incremental_gwt_s3_04`（逐一 k 验证增量 == 全量切片）· `test_session_query.py::test_rebuild_full_matches_incremental`（表 + 幂等闸**双比对**） | ✅ **是**（`==` 断言） |
| **2** | 二次 rebuild 不产生重复 | ✅ covered | `test_rebuild_from_log_invariant_inv03:500-503`（append → **再 rebuild** → 仍一致）· `test_session_query.py::test_rebuild_then_incremental_no_dup`（行键唯一 + 无重复行） | ✅ **是** |
| **3** | 编辑后的状态重新 rebuild 正确 | ✅ covered | **session 侧 = 本轮新增 T-1**（**M-2 实证**）· **query 侧 = 既有** `test_rebuild_full_matches_incremental`（语料含 `edited`，rebuild 后断言新旧词命中） | ✅ **是（两侧）** |
| **4** | cache invalidation 行为正确 | ✅ covered | `test_rebuild_derived_cache_invalidation_on_append`（append 后 `history_cache is None`）· `test_session.py:602` | ✅ **是** |
| **5** | rebuild 后状态与事件重放结果一致 | ✅ covered | `test_rebuild_from_log_invariant_inv03`（含 `stats()` **精确字典比对**）· `test_open_session_recovers_state`（**全新对象**从磁盘 replay ⇒ 事件/派生/get 全等） | ✅ **是** |
| **6** | rebuild 不产生错误副作用 | ✅ covered | **本轮新增 T-2**（rebuild 前后 **bytes + sha256 + 事件序列**三重恒等） | ✅ **是**（全量字节相等 ⇒ 完备守卫） |
| **7** | rebuild 的确定性 | ✅ covered（**附 scope note**） | **本轮新增 T-3**（连续三次快照 `a == b == c`） | ✅ **是（进程内）**；见 §7 scope note |
| **8** | 空 / 新 / 边界 session | ✅ covered | `test_open_session_empty_store_is_new_session` · `test_open_session_filters_other_sessions` · `test_session_query.py::test_rebuild_unknown_session_noop` · `::test_rebuild_not_entered_pers_202` · `::test_rebuild_single_session_scope` | ✅ **是** |

---

## 3. 3 个 `deferred` 子性质（**未被本阶段扩大**）

| # | 项 | 状态 | 为何保持 deferred（独立复核） |
|---|---|---|---|
| **D-1** | `replay()` 的 seq 去重（与写路径 `_resolve_by_seq` 对称化） | **deferred** | **可达性未达成** —— `_rotate()` 用 `os.replace`（整文件原子移动，**不复制**）· `_rewrite_without_tail` 截尾 · `quarantine_lines` 写副本（不改主日志完好行）⇒ 正常路径**不产生重复 seq**；且 `seq_holes()` 以"append 保证单调"为构造性假设。属 hardening，**非缺陷** |
| **D-2** | `session_query` rebuild 的 O(n) 性能面 | **deferred** | 与 INV-03 **正确性**语义无关（全表 DELETE + 重插的代价）；属 deferred debt |
| **D-3** | **跨进程** rebuild（一进程 rebuild + 另一进程并发 append） | **deferred** | 需多进程测试设施；`INV-07` 已覆盖单写者互斥；并发语义独立评估（**同时是 §7 P-3 scope note 的对应项**） |

**核验**：本阶段**未修复**上述任何一项（`git diff --name-only 9349596 -- pyharness/persistence.py pyharness/core/session_query.py` = **0**）。

---

## 4. T-1 / T-2 / T-3 证据（**独立复核**）

### 4.1 T-1 · `test_inv03_rebuild_preserves_edit_override`（`test_inv_core.py:402-431`）

| 人工核验点 | 独立结论 |
|---|---|
| **不是 query projection 测试** | ✅ 确认 —— 全程**无** `session_query` / SQLite / FTS 引用；断言对象为 `log.derive_messages()`（**会话内存缓存派生态**） |
| **不是重复现有编辑测试** | ✅ 确认 —— 既有 `test_derive_edited_overrides_gwt_s3_03` **不调用 `rebuild_from_log()`**（读源码确认）；且 M-2 下**既有 77 条 session/query 用例全绿**（§5） |
| **使用真实 `SessionLog` / `SessionStore` / replay 路径** | ✅ 确认 —— `_wired_log` 构造真实 `SessionLog` + `EventBus`（词表全量订阅）+ **真实 `SessionStore`（`open_store` 落 `tmp_path` 文件）**；`rebuild_from_log()` 走 `persistence.replay()` 真实文件重放 |
| **最终状态确实取 edited 内容** | ✅ 确认 —— `build` 前断言基线为 `[{"role":"user","content":"把 D:/work 归档"}]`；**rebuild 后**断言 `derive_messages() == before_msgs`（仍为新版）；并断言**日志原文两行都在**（`content` 为旧版、`new_content` 为新版 ⇒ 取新版留旧痕） |

**复核 M-2 的历史结果是否足以证明"T-1 填补了真实的原覆盖缺口"**：

| 环节 | 证据 | 是否充分 |
|---|---|---|
| T-1 必须 RED | ✅ M-2 下 T-1 RED，失败消息 `INV-03 违约:rebuild 后未取编辑后的新版(edited 覆盖丢失)`，diff 显示派生回退旧版 | ✅ |
| 既有 session/query 测试仍 GREEN | ✅ **`tests/unit/test_session.py` + `tests/unit/test_session_query.py` 全部 77 用例 GREEN** | ✅ |
| ⇒ 结论 | 存在一类真实缺陷（rebuild 丢失编辑覆盖）**能穿过既有全部用例**，而 T-1 **唯一捕获** ⇒ **T-1 填补了真实的原覆盖缺口** | ✅ **充分** |

### 4.2 T-2 · `test_inv03_rebuild_does_not_touch_truth_source`（`:435-460`）

| 人工核验点 | 独立结论 |
|---|---|
| rebuild 前后 **bytes 完全一致** | ✅ 断言 `store.path.read_bytes() == before_bytes` |
| **sha256 完全一致** | ✅ 断言 `sha256(read_bytes()) == before_sha` |
| **event sequence 完全一致** | ✅ 断言 `[(e.seq, e.type) for e in store.replay()] == before_seqs` |
| **没有把派生状态变化误认为事实源变化** | ✅ 确认 —— 比较对象是 **`store.path` 的原始字节**与 **`store.replay()`（真源读取）**，**不涉及** `derive_messages()` / `stats()` 等派生态 ⇒ 逻辑上不可能混淆两者 |
| **确实覆盖 Event Log 唯一事实源保护** | ✅ 确认为**唯一真源文件本身**的字节级守卫 |

**为什么 P-2 → `covered`**：
`rebuild_from_log` 若**回写真源**（追加/截断/重写），**必然**改变 `store.path` 的字节序列（除非写入与原文完全相同的内容，那在语义上不构成变化）⇒ **T-2 必然 RED**。⇒ 该断言是对"真源零变化"的**完备守卫**（而非仅仅"某个症状"），且覆盖的正是 INV-03 定义的"纯派生"前提。
**M-6 未执行的理由**（依授权）：`rebuild_from_log()` 为**同步**方法而存储写为**异步**（`store.append`/`flush` 皆 `async`），注入需伪造跨层异步桥接 —— 形态不自然、与真实缺陷距离远；**结论不依赖该 mutation**（上述"必然性"由断言的完备性直接给出）。

### 4.3 T-3 · `test_inv03_rebuild_is_deterministic`（`:462-487`）

| 人工核验点 | 独立结论 |
|---|---|
| **snapshot 只包含稳定量** | ✅ 确认 —— `snapshot() = ([(e.seq, e.type) for e in log.events_after()], log.derive_messages(), log.stats())`，共三类 |
| **不包含时间** | ✅ 确认 —— 独立读 `SessionLog.stats()`（`session.py`）返回 **恰好 6 个键**：`session_id` / `seq` / `event_count` / `closed` / `folded_ranges` / `holes_warned` —— **无任何时间字段** |
| **不包含随机 ID** | ✅ 确认 —— 上述键无 uuid/随机量 |
| **不包含调用次数** | ✅ 确认 —— `seq` 是**事件序号**（日志事实），**非** rebuild 调用次数；快照中无计数器 |
| **连续多次 rebuild 使用完全相同输入** | ✅ 确认 —— 三次 rebuild 之间**未做任何日志写入**；且 **T-2 已证 rebuild 自身不回写真源** ⇒ 输入恒定的前提成立 |
| **A == B == C 有实际意义** | ✅ 确认 —— 断言的是 `a == b` 与 `b == c`（**同输入下的结果可重复**），而非与某个外部期望值相等 ⇒ 恰恰对应"确定性"这一性质本身 |

**保留 scope note（不扩大结论）**：
> **当前仅证明"支持范围内的进程内确定性"。** T-3 为**单进程**用例 ⇒ 对**跨进程**方差（例如 `PYTHONHASHSEED` 影响集合/字典序遍历序）**不可见**。缓解事实：`rebuild_from_log` 的实现路径**只读文件**、顺序由文件次序固定、且 `_folded` 经 `_folded.sort()` **显式排序** ⇒ **当前无可达的跨进程可变输入**。
> **跨进程 rebuild 仍属 `deferred`（§3 D-3）** —— **不得**据此宣称"确定性已跨进程证明"。

---

## 5. Mutation Evidence（**独立检查已有结果；本轮不重跑生产代码 mutation**）

| Mutation | 注入内容 | 观察结果（历史留证 + 变更报告 §5） | 独立复核 |
|---|---|---|---|
| **M-2** | `rebuild_from_log` 重放循环内跳过 `user.message_edited` 吸收 | **T-1 RED**（消息 `INV-03 违约:rebuild 后未取编辑后的新版`）；**既有 session/query 77 用例 GREEN**；T-2/T-3 GREEN | ✅ 变更报告 §5 留证；机制上必然（T-1 断言派生取新版） |
| **M-3** | `replay()` 顺序反转 | **既有 `test_rebuild_from_log_invariant_inv03` RED** + **T-1 RED**；**T-2/T-3 GREEN** | ✅ 同上；T-2 不涉顺序、T-3 对"一致错误"不敏感 ⇒ 结果自洽 |
| **M-1** | 重建少消费一个事件（跳过 `session.created`） | **既有 `test_rebuild_from_log_invariant_inv03` RED**（`Right contains one more item`）+ **T-1 RED**；T-2/T-3 GREEN | ✅ 同上 |
| **M-6**（T-2 专属） | 令 rebuild 回写真源 | **未执行**（依授权：同步/异步跨层注入不自然） | ✅ 理由成立；T-2 的鉴别力由断言**完备性**给出（§4.2） |
| **T-3 专属** | — | **无自然最小 mutation**（M-1/M-3 令 rebuild **一致地**出错 ⇒ `a==b==c` 仍成立；破坏确定性须**人为引入非确定性**，依明令不执行） | ✅ 记录理由；间接鉴别力由 **M-3 → T-1 RED**（同款快照机制被证明敏感）+ 实现面（只读 `replay()`、顺序固定、`_folded.sort()`）佐证 |

**恢复与残留（独立复验，本轮实际执行）**：

| 检查 | 结果 |
|---|---|
| `grep -c "MUTATION" pyharness/core/session.py pyharness/persistence.py pyharness/core/auto_title.py` | **0 / 0 / 0** ✅ |
| `git status --porcelain pyharness` | **空** ✅ |
| **临时文件** | ✅ 本轮**已主动清理**三份 mutation 备份（`tmp/{session,persistence,auto_title}_fixed.bak`）⇒ `ls tmp/*.bak` = **No such file** |
| 语法 | ✅ `ast.parse` 通过（在前序步骤核验） |

---

## 6. Regressions（**独立复跑**）

| 项 | 独立复算结果 |
|---|---|
| **全量** | ✅ **1685 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1683 passed / 2 skipped / 0 failed**（43.3s，exit 0） |
| **基线** | `9349596`：1682 collected / 1680 passed |
| **差值** | **+3 passed = 新增的 3 条 INV-03 用例** ⇒ **零既有回归** ✅ |
| `-k inv03` 定向 | ✅ **3 passed** |
| `tests/invariants/` | ✅ **29 passed** |
| **Event Schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |
| **生产代码** | `git diff --name-only 9349596 -- pyharness` = **0** ✅ |
| **Governance Core** | `pyharness/governance` **0 改动** ✅ |
| **Registry** | `docs/INVARIANT_REGISTRY.md` **0 改动** ✅ |
| **Event Schema 文档** | `docs/EVENT-SCHEMA.md` **0 改动** ✅ |
| **KF-B** | **`OPEN`**（未处理）✅ |
| **L-3 / F042 / INV-01 scanner** | **均未处理** ✅ |
| **是否进入 INV-04** | ❌ 否 ✅ |

---

## 7. Residual Limitations

| # | 限制 | 等级 | 说明 |
|---|---|---|---|
| **R-1** | T-3 仅覆盖**进程内**确定性 | **低**（已声明 scope note） | 跨进程方差不可见；但 rebuild **无可达的跨进程可变输入**（只读文件 + 固定序 + 显式 `sort`）⇒ 当前无实际暴露面。对应 **D-3 deferred** |
| **R-2** | T-2 的专属 mutation（M-6）未执行 | **低** | 理由：跨层同步/异步注入不自然（依授权）。T-2 的鉴别力由**断言完备性**保证（全量字节相等）⇒ **不影响 `covered` 判定** |
| **R-3** | T-3 无专属 mutation | **低** | 破坏确定性须人为引入非确定性（依明令不执行）；以 M-3→T-1 RED 间接证明同款快照敏感 + 实现面佐证 |
| **R-4** | INV-03 的 `test_tools_guard.py` 6 条**误标**仍未修 | **低** | 属 S6-1 迁移清单 **FC-8**，`Status = pending authorization`；仅影响"覆盖率读数"，**不影响本轮新增用例的正确性** |
| **R-5** | `replay()` 无 seq 去重 | **低**（deferred） | 可达性未达成（D-1） |
| — | **无 P0 / P1 级残余** | — | 上述 R-1~R-5 均为**低**级、且**均已显式登记或划归 deferred** |

---

## 8. Coverage 最终状态

```
INV-03 = rebuild 与缓存一致
  ├─ covered  : 8   (全量↔增量 · 二次无重复 · 编辑重放(双侧) · cache 失效 ·
  │                  重建==重放 · 无副作用 · 确定性 · 空/新/边界)
  ├─ partial  : 0
  ├─ gap      : 0
  └─ deferred : 3   (replay seq 去重 · session_query rebuild O(n) · 跨进程 rebuild)
```

**独立确认**：`covered` 的 8 条**逐条满足**"若破坏则对应测试应失败"的鉴别力要求（§2 表的"破坏时是否必红"列）；**未因"测试存在"而自动判 covered**（P-2 与 P-3 的判定分别由**断言完备性**与 **M-2/M-3 实证 + 实现面**给出，而非"有测试即算"）。

**与 Registry 的对应**：
- Registry 记录的 INV-03 `Coverage Gap` 第 ① 项（"无编号化专项在 `tests/invariants/`"）⇒ ✅ **本阶段关闭**
- 第 ② 项（`test_tools_guard.py` 误标 INV-03 虚增覆盖）⇒ **仍开放**（FC-8，`pending authorization`）
- **Registry 文件本身未修改**

---

## 9. 是否满足 Freeze

| Freeze 前置 | 结果 |
|---|---|
| 3 条 partial 全部补齐且各有实证 | ✅ T-1（M-2）· T-2（断言完备性）· T-3（无自然 mutation + 间接证据） |
| 未重新解释 INV-03 | ✅ |
| 未重复建设既有覆盖 | ✅（6 类既有覆盖逐条只建映射） |
| mutation 恢复、无残留、无临时文件 | ✅（独立复验 + 主动清理 `.bak`） |
| 全量 ≥ 基线且零既有回归 | ✅ 1683 / 2 / 0 |
| schema 77/14/3 · 生产代码 / Registry / Governance Core / Event Schema 零改动 | ✅ |
| deferred 边界未扩大 · KF-B / L-3 / F042 / INV-01 scanner 均未处理 · 未进入 INV-04 | ✅ |

⇒ ✅ **满足 Freeze 条件**。

---

## 10. 是否允许进入 S6-2b-3 / INV-04

| 项 | 结论 |
|---|---|
| 阻塞项 | ❌ **无**（R-1~R-5 均为低级、已登记或已划归 deferred） |
| 结论 | ✅ **允许进入 S6-2b-3 / INV-04**（**不自动开始**，等待人工授权） |

---

## 11. 最终判定

> # ✅ **S6-2b-2 = PASS**

| 标签 | 值 |
|---|---|
| **S6-2b-2 Phase F** | **PASS** |
| **INV-03** | **`covered` × 8 · `partial` 0 · `gap` 0 · `deferred` 3** |
| **P-1 / P-2 / P-3** | 全部 **`partial` → `covered`** |
| **生产代码 / Registry / Event Schema / Governance Core** | 均 **零改动** |
| **KF-B** | **`OPEN`** |
| **S6-2b-3 / INV-04** | **READY**（不自动开始） |

**判定依据**：① 未重新解释 INV-03；② T-1/T-2/T-3 **逐项独立复核通过**（真实 `SessionLog`/`SessionStore`/replay 路径 · 真源字节级守卫 · 快照仅含稳定量）；③ M-2 的"**T-1 RED + 既有 77 用例 GREEN**"**充分证明** T-1 填补真实缺口；④ 全量 **1683/2/0**（**+3 零回归**）；⑤ 冻结面**逐项 0 改动**；⑥ 残余限制**逐条登记**且**未扩大结论**（T-3 的跨进程 scope note 明确不外推）。

---

**S6-2b-2 Final Review 结束。判定 = PASS。等待 Freeze 提交；未 push。**
