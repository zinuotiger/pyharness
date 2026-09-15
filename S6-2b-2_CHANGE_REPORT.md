# S6-2b-2_CHANGE_REPORT.md — INV-03 部分覆盖测试实施（Phase F）

> **阶段**：S6-2b-2 Phase F（INV-03 Partial Coverage Implementation）
> **日期**：2026-09-15 ｜ **基线 commit**：**`9349596`** ｜ HEAD `9349596` ｜ ahead 35
> **改动面**：**仅 1 个测试文件**（`tests/invariants/test_inv_core.py`，**+134 / −0**）· **生产代码零改动**
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-03）—— **未据旧注释重新解释**
> **定向**：`tests/invariants` = **29 passed** ｜ `-k inv03` = **3 passed**
> **全量**：**1685 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1683 passed / 2 skipped / 0 failed**
> **对比基线**：1682 collected / 1680 passed ⇒ **+3 = 新增用例，零既有回归**
> **schema**：`EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`（**不变**）

---

## 1. Phase A~E 审计结果摘要（回顾）

> 详见 `S6-2b-2_COVERAGE_AUDIT.md`（**未提交**）

- **INV-03 拆 9 条子性质** ⇒ `covered` **5** · `partial` **3** · `gap` **0** · `deferred` **3**
- **三条 partial（本轮实施对象）**：
  - **P-1** 编辑后 **session 侧** rebuild 正确（`test_derive_edited_overrides_gwt_s3_03` **不调用 rebuild**）
  - **P-2** rebuild **无副作用**（仅隐含断言，无"真源字节恒等"显式防线）
  - **P-3** rebuild **确定性**（无"连续两次 rebuild 输出恒等"断言）
- **实现审计**：`rebuild_from_log` **纯派生**（只读 `persistence.replay()`、无写回）· 无第二状态来源 · 两条 cache 失效点齐备 · 顺序由 `replay()` 保证
- **健壮性观察（非缺陷）**：`replay()` 不做 seq 去重（与写路径 `_resolve_by_seq` 不对称），**可达性未达成** ⇒ deferred，本轮**不处理**

---

## 2. T-1 / T-2 / T-3 实施内容

全部落在 `tests/invariants/test_inv_core.py` 新增的 **INV-03 段**，并复用两个自带辅助：

| 辅助 | 作用 |
|---|---|
| `_wired_log(sid, tmp_path)` | **真实装配**：`SessionLog` + `EventBus` → **真实 `SessionStore`（文件真源）**，与生产同一条落盘路径（复用 INV-01 段的 `_record_to_store`） |
| `_seed_rebuild_fixture(log)` | 固定语料（created → user.message → **edited** → llm.response → tool.result → guard.rejected），**不含时间/随机量** |

| # | 用例 | 实施要点 |
|---|---|---|
| **T-1** | `test_inv03_rebuild_preserves_edit_override` | 真实 store + 真实 replay；构造 created → `user.message` → `user.message_edited{target_seq=2,new_content}`；`store.flush()` 后取 `derive_messages()` 基线 → **`log.rebuild_from_log()`** → 断言 ① 派生**逐条恒等**（仍取新版）② 事件类型序列完整 ③ **日志原文两行都在**（`content` 为旧版、`new_content` 为新版，取新版留旧痕） |
| **T-2** | `test_inv03_rebuild_does_not_touch_truth_source` | 真实日志文件；rebuild 前记录 **bytes / sha256 / 事件序列**；`rebuild_from_log()` 后逐项重读并断言**完全一致** |
| **T-3** | `test_inv03_rebuild_is_deterministic` | 固定日志；`snapshot() = (事件 seq/type 序列, derive_messages(), stats())`，**只取稳定量**；连续 rebuild **三次** ⇒ 断言 `a == b` 且 `b == c` |

**前置条件与可诊断性**（沿用 S6-2b-1 体例）：
- T-1 显式断言 `store.flush()` 后基线非空且已取新版（失败消息以"**前置条件:**"开头 ⇒ 与"INV-03 违约"**可区分**）
- T-2 显式断言真源已落盘且非空
- 三条断言的失败消息**均点名被违反的子性质**（如"rebuild 后未取编辑后的新版(edited 覆盖丢失)"）

---

## 3. 每个测试对应的 Canonical INV-03 子性质

| 用例 | 对应子性质（按 §1 拆解编号） | 对应 partial |
|---|---|---|
| **T-1** | **#3 编辑后的状态重新 rebuild 正确**（**session 侧**） | **P-1** |
| **T-2** | **#6 rebuild 不产生错误副作用** | **P-2** |
| **T-3** | **#7 rebuild 的确定性** | **P-3** |

> Registry 的 INV-03 三段要求（增量读一致 / 编辑重放 / 二次 rebuild 无重复）中：**增量读一致** 与 **二次 rebuild 无重复** 已由既有用例覆盖；本轮补的是 **编辑重放（session 侧）**、以及 Registry 未明列但由定义推出的 **无副作用** 与 **确定性**。

---

## 4. 已有测试为何没有重复建设

本轮**只新增 3 条**，与既有测试**无重叠**：

| 既有已充分覆盖的性质 | 既有用例（**未重复**） |
|---|---|
| 二次 rebuild 不产生重复 | `test_session.py::test_rebuild_from_log_invariant_inv03` · `test_session_query.py::test_rebuild_then_incremental_no_dup` |
| 全量 ↔ 增量一致 | `test_session.py::test_events_after_incremental_gwt_s3_04` · `test_session_query.py::test_rebuild_full_matches_incremental` |
| 编辑重放（**query 投影侧**） | `test_session_query.py::test_rebuild_full_matches_incremental` · `::test_edited_updates_target_row` |
| cache invalidation | `test_session.py::test_rebuild_derived_cache_invalidation_on_append` |
| 重启恢复 ≡ 崩溃前 | `test_session.py::test_open_session_recovers_state` |
| 空 / 新 / 边界 session | `test_session.py::test_open_session_empty_store_is_new_session` · `test_session_query.py::test_rebuild_unknown_session_noop` 等 |

**T-1 与既有"编辑重放"用例的区别（关键，非重复）**：既有用例覆盖的是 **query 投影**（SQLite FTS 索引），T-1 覆盖的是 **会话内存缓存**（`SessionLog._cache` → `derive_messages()`）。二者是**两个不同的派生视图**，由**不同的实现路径**维护 —— M-2 的实测结果（§5）**经验性证明**了这一点：既有全部用例在 M-2 下仍绿。

**未修改任何既有测试**（`git diff` 仅 `+134/−0` 纯追加）。

---

## 5. Mutation 结果（**全部已执行 → 已恢复 → 无残留**）

> 备份 `pyharness/core/session.py` → `tmp/session_fixed.bak`（`tmp/` 已 gitignore）

### M-2（T-1 专属）— rebuild **跳过 `user.message_edited` 吸收**

| 步骤 | 结果 |
|---|---|
| 注入：重放循环内 `if env.type == "user.message_edited": continue` | — |
| `-k inv03` | ✅ **T-1 RED**：`INV-03 违约:rebuild 后未取编辑后的新版(edited 覆盖丢失)`，diff 显示派生回退到旧版 `把 D:/杂乱 按主题归类`；**T-2 / T-3 仍绿**（不受影响） |
| **⭐ 既有测试是否漏检**（关键证据） | ✅ **`tests/unit/test_session.py` + `tests/unit/test_session_query.py` 全部 GREEN（77 用例）** ⇒ **经验性证明 P-1 曾是真实缺口**，且 **T-1 具唯一鉴别力** |

### M-3 — replay **顺序反转**

| 步骤 | 结果 |
|---|---|
| 注入：`for env in reversed(list(self._persistence.replay()))` | — |
| 运行 invariants + `test_session.py` | ✅ **既有 `test_rebuild_from_log_invariant_inv03` RED**（`rebuild 与缓存逐事件一致` 失败，首元素即是 seq=6）+ **T-1 RED**；**T-2 / T-3 仍绿** |

### M-1 — rebuild **少消费一个事件**（跳过 `session.created`）

| 步骤 | 结果 |
|---|---|
| 注入：重放循环内 `if env.type == "session.created": continue` | — |
| 运行 invariants + `test_session.py` | ✅ **既有 `test_rebuild_from_log_invariant_inv03` RED**（`Right contains one more item`）+ **T-1 RED**；**T-2 / T-3 仍绿** |

### M-6（T-2 专属）— **未执行**（依人工授权："不自然则不执行危险 mutation"）

| 项 | 说明 |
|---|---|
| 拟注入 | 令 `rebuild_from_log` 在重放后**写回真源**（追加/截断文件） |
| **为何不执行** | `rebuild_from_log()` 为**同步**方法，而存储写为**异步**（`store.append` / `flush` 均 `async`）⇒ 注入需伪造跨层异步桥接（如 `asyncio.get_event_loop().run_until_complete` 或在同步路径直接 `open(path,"a")`），**形态不自然、与真实缺陷距离远**，且会给生产代码引入临时同步 I/O |
| **T-2 的鉴别力如何保证** | T-2 断言的是**全量字节相等**（`read_bytes() == before_bytes` + `sha256` 相等 + 事件序列相等）—— 这是对"真源零变化"的**完备守卫**：**任何**对真源文件的写操作（除非写入完全相同的内容，那不构成变化）都必然改变 bytes ⇒ T-2 必然 RED。⇒ 依人工口径"只说明原因"，**不执行** |

### T-3 的 mutation — **无自然最小 mutation**（依人工授权记录理由）

| 项 | 说明 |
|---|---|
| 实测结论 | **M-1 / M-3 均不使 T-3 RED**（已实测）：它们令 rebuild **一致地**出错 ⇒ `a == b == c` 仍成立（T-3 断言的是**三次结果彼此相等**，而非与期望值相等） |
| 为何无自然 mutation | 破坏"确定性"必须引入**进程内可变输入**（调用计数器 / 时间 / 真随机）。此类改动是**人为制造非确定性**，不贴近任何真实缺陷形态 ⇒ 依人工明确指示"**不要为了凑 mutation 人为引入非确定性**"，**不执行** |
| 间接鉴别力证据 | ① **M-3 → T-1 RED** 用的正是 T-3 同款快照机制（`derive_messages()` 比对）⇒ 证明该**快照对真实差异敏感**（非空转）；② **实现面**：`rebuild_from_log` 只读 `persistence.replay()`，**不依赖时间 / 随机 / 调用次数 / 外部状态**，顺序由文件次序固定 ⇒ 确定性由构造保证 |
| 记录 | **"无自然最小 mutation"** + 上述理由（依人工要求留档） |

### 恢复与残留核验

| 项 | 结果 |
|---|---|
| 生产代码残留 | ✅ `git status --porcelain pyharness` **空**；`grep -c "MUTATION" pyharness/core/session.py` = **0** |
| 语法 | ✅ `ast.parse` 通过 |
| 复跑 | ✅ `tests/invariants/test_inv_core.py` = **12 passed** |

---

## 6. 全量回归与核验

| 项 | 结果 |
|---|---|
| `-k inv03` 定向 | ✅ **3 passed** |
| `tests/invariants/` 全目录 | ✅ **29 passed**（26 + 3） |
| **全量** | ✅ **1685 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1683 passed / 2 skipped / 0 failed**（43.9s，exit 0） |
| 对比基线 | 1682 collected / 1680 passed ⇒ **+3 = 新增用例**，**0 failed 不变** ⇒ **零既有回归** |
| **schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |
| **生产代码** | `git diff --name-only HEAD -- pyharness` = **0** ✅ |
| **Registry / Event Schema / Governance Core** | **0 改动** ✅ |
| `git diff --check` | ✅ 通过（无空白错误） |
| 已有测试是否被改 | ❌ 未改（`git diff` 仅 `+134/−0` 纯追加） |

---

## 7. Coverage 状态变化（**逐条回答"性质被破坏时新测试是否必然失败"**）

| # | 性质 | 变化 | 判定依据 |
|---|---|---|---|
| **P-1** | 编辑后 session 侧 rebuild 正确 | **`partial` → ✅ `covered`** | **必然失败** —— 由 **M-2 实证**：跳过 `user.message_edited` 吸收 ⇒ T-1 **RED**，且**既有 77 条 session/query 用例全绿** ⇒ T-1 是该性质**唯一且必要**的守卫 |
| **P-2** | rebuild 无错误副作用（真源零变化） | **`partial` → ✅ `covered`** | **必然失败** —— T-2 断言**全量字节相等 + sha256 相等 + 事件序列相等**，是对"真源零变化"的**完备守卫**；任何真源写操作都改变 bytes ⇒ 必然 RED（M-6 因不自然未执行，依授权只说明理由） |
| **P-3** | rebuild 确定性 | **`partial` → ✅ `covered`**（**附 scope note**） | **必然失败（进程内）** —— 若 rebuild 引入进程内非确定性（计数/时间/真随机），T-3 的 `a == b` 必然 RED。**scope note**：T-3 为**单进程**用例 ⇒ 对**跨进程**方差（如 `PYTHONHASHSEED` 影响集合/字典序）**不可见**；但 `rebuild_from_log` 的实现路径**只读文件、顺序固定、无哈希序遍历**（`_folded.sort()` 显式排序）⇒ 该盲区当前**无可达输入**。跨进程 rebuild 已登记 **D-3 deferred** |

**INV-03 汇总**：`covered` **8** · `partial` **0** · `gap` **0** · `deferred` **3**（D-1 replay 去重 / D-2 查询投影性能 / D-3 跨进程）。

**⚠️ 与 Registry 的对应关系**：
- Registry 记载的 INV-03 `Coverage Gap` 第 ① 项（"**无编号化专项在 `tests/invariants/`**"）⇒ ✅ **本轮已关闭**（新增 3 条编号化用例）
- 第 ② 项（`test_tools_guard.py` 的 6 条**误标 INV-03** 会虚增覆盖率）⇒ **仍开放** —— 属 S6-1 的迁移清单 **FC-8**，`Status = pending authorization`，**本轮未触碰**
- **Registry 文件本身未修改**（依边界）

---

## 8. 未处理的 Deferred Items

| # | 项 | 状态 | 理由 |
|---|---|---|---|
| **D-1** | `replay()` 的 seq 去重（与写路径 `_resolve_by_seq` 对称化） | **未处理** | 可达性未达成（rotation 用 `os.replace` 不复制 · repair 只截尾/隔离）⇒ 属 hardening；触及 `persistence.py` 生产代码 ⇒ 独立评估 |
| **D-2** | `session_query` rebuild 的 O(n) 性能面 | **未处理** | 与 INV-03 **正确性**语义无关；属 deferred debt |
| **D-3** | **跨进程** rebuild（一进程 rebuild + 另一进程并发 append） | **未处理** | 需多进程测试设施；`INV-07` 已覆盖单写者互斥；并发语义独立评估（亦是 P-3 scope note 的对应项） |
| **FC-8** | `test_tools_guard.py` 的 6 条误标 INV-03 | **未处理** | 属 S6-1 迁移清单，`pending authorization` |

---

## 9. 是否发现真实生产缺陷

| 项 | 结论 |
|---|---|
| **本轮测试是否揭示真实生产缺陷** | ❌ **否** —— M-1 / M-2 / M-3 均为**人为注入**的变异（用于验证测试鉴别力），**非既有代码中的缺陷**；移除变异后全部 GREEN |
| **audit 阶段的健壮性观察**（replay 无 seq 去重） | 维持 **deferred**（可达性未达成）⇒ **不建 KEY-FINDING** |
| **结论** | **无新增 KEY-FINDING**；**生产代码零改动** |

---

## 10. 状态与边界

| 项 | 结果 |
|---|---|
| 改动面 | **仅 `tests/invariants/test_inv_core.py`（+134 / −0）** ✅ |
| 新增测试 | **3 条**（T-1 / T-2 / T-3），全部标注 **INV-03** |
| 状态标注 | `IMPLEMENTED` + `TESTED` + `VERIFIED`（定向 + 全量 + mutation A/M-1/M-2/M-3 均有实证；M-6 与 T-3 的 mutation 依授权记录理由后不执行） |
| 未做 | ❌ 改 `pyharness/` · ❌ 改 Registry / Event Schema / Governance Core · ❌ 修 seq dedup / KF-B / L-3 / F042 · ❌ 增强 INV-01 scanner · ❌ 进入 INV-04 / S6-3 / S7 · ❌ commit · ❌ push |

**worktree**：`M tests/invariants/test_inv_core.py` + `?? S6-2b-2_COVERAGE_AUDIT.md`；**HEAD 仍 `9349596`**。

---

**S6-2b-2 Phase F 结束。未自动进入 Final Review；等待人工授权。**
