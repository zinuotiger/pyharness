# F-SYNC-1_CHANGE_REPORT.md — 强同步落盘失败语义修复实施报告

> **阶段**：F-SYNC-1 REMEDIATION（S4 Entry Gate 的 P0 前置）
> **日期**：2026-09-15 ｜ **基线 HEAD**：`7998638`（S3-2-2 checkpoint;worktree clean）
> **依据**：F-SYNC-1 REMEDIATION DESIGN（方案 A 批准）· A-1 裁定（(c) fail-closed）
> **性质**：**未 commit**。**不进入 S4**、未创建 receipt/evidence/audit。

---

## 0. 结论

# F-SYNC-1 = FIXED / PASS

**改动 1 个生产文件（`pyharness/persistence.py`，+48 / −5）+ 2 个测试文件**；全量回归 **1553 passed / 2 skipped / 0 failed**（较 S3-2-2 基线 1538 **+15**，零回归）。

---

## 1. 根因

`SessionStore._flush_pending_all()`（**仅**强同步路径调用，唯一调用点 = `persistence.append(sync=True)`）在写失败时的**丢弃语义**：

```python
        except OSError:
            self._retry_q = deque(retry_old)      # 仅恢复旧重试行
            raise                                 # pending_now 永久丢失
```

同文件中另两条写入路径（`flush()`、`_flush_batch()`）本就是**回填 `_retry_q`**（拒新不丢旧）。**只有强同步路径会丢行**——这是单点缺陷。

## 2. 修复前行为（实证复现）

| 环节 | 层 | 行为 |
|---|---|---|
| 1 | `session.append` 步骤 8 | `_dispatch` → 总线 → 适配器 `store.append(payload, sync=True)` |
| 2 | `persistence.append` | `_pending.append` → `_flush_pending_all` → OSError |
| 3 | **`_flush_pending_all`** | **丢弃 `pending_now`**，只保 `retry_old`，`raise` |
| 4 | **EventBus** | `except Exception → EVT-103`，**订阅者异常不外抛** |
| 5 | `session.append` 步骤 9 | `_flush(env.seq)` → `persistence.flush`：两队列皆空 → **静默返回成功** |
| 6 | 调用方 | **收到成功**，但事件未落盘 |

⇒ `Decision 在内存` + `append 看似成功` + `restart 后事件不存在` —— **可能**。

## 3. 修复后行为

`session.append` 步骤 9 的 `flush(seq)` 不再是空操作，而成为对同一批的**第二次真实尝试**：

- **成功** ⇒ 事件真正持久，`append` 成功返回，`replay` 可读出；
- **仍失败** ⇒ `PERS-202` **上抛至调用方**（不再 silent success），行仍留在 `_retry_q` 待 repair 后恢复。

## 4. `_retry_q` 回填

```python
        except OSError:
            self._retry_q = deque(retry_old)
            self._retry_q.extend(pending_now)     # 本批完整回填(保序)
            raise
```

`retry_old + pending_now` 完整保留；不丢任何一条。**顺序**：重试行恒先写（旧行 seq 更小）。

## 5. Caller-visible `PERS-202`

端到端实证（真 `SessionLog` + 真 `EventBus` + 真 `SessionStore` + 真适配器）：

```
adapter append  → OSError → PERS-202 → 总线 EVT-103 隔离
step9 flush     → 重试仍失败 → PERS-202 上抛
调用方          → PyHError(code="PERS-202")   ← 不再 silent success
_retry_q        → [2]（行未丢）
```

## 6. Failed batch 不丢

多事件批 `[10, 11, 12]` 首次失败 → `_retry_q = [10,11,12]`；连续两轮失败后仍为 `[10,11,12]`；恢复后写出 `10→11→12`，`replay` 序亦 `10→11→12`。**不丢 10、不重复 11、不乱序 12**。

## 7. Seq ordering

| 场景 | 时序 |
|---|---|
| 正常 | 10 → 11 → 12 |
| 失败后 retry | 10 → 11 → 12 |
| 多轮失败后 retry | 10 → 11 → 12 |
| 恢复后 replay | 10 → 11 → 12 |

三条写入路径均满足"**旧 retry 先于新 batch** + **物理写序 = seq 升序**"。

## 8. A-1：同 seq 冲突 fail-closed（裁定 (c)）

```python
def _resolve_by_seq(rows) -> list[tuple[int, str]]:
    seen: dict[int, str] = {}
    for seq, line in rows:
        prev = seen.get(seq)
        if prev is None:
            seen[seq] = line
        elif prev != line:
            raise_code("PERS-202", op="seq_conflict", seq=seq, ...)   # fail-closed
    return [(s, seen[s]) for s in sorted(seen)]
```

| 情形 | 行为（实证） |
|---|---|
| 同 seq + **相同行** | `[(7,'A'),(7,'A')] → [(7,'A')]` —— 去重（同 seq 重放同一事件，幂等） |
| 同 seq + **不同行** | `[(7,'A'),(7,'B')]` → **`PERS-202`（`op=seq_conflict`）** |

**不 first-wins、不 last-wins、不静默覆盖**——冲突时**两份数据都保留**（校验在**任何队列状态变更之前**执行，无副作用）。

**未引入新错误码**：复用既有 `PERS-202`（域语义 = 落盘失败，此处为"落盘前的数据一致性冲突"）。

**契约依据**：`seq` 由框架经 `SeqState.next_seq/commit` 单调分配，`SessionLog` 另有 EVT-101 失步闸 ⇒ 同一 seq 只可能代表同一事件。正常写入路径上冲突**不可达**；本校验针对低层 `SessionStore` API 拒绝静默数据替换。

## 9. SYNC_TYPES 通用性

- **实现层**：写路径**零 event type 特判**，统一适用于全部 **13 个** `SYNC_TYPES`。
- **测试层**：仅覆盖 **3 个代表性类型**（`user.message` / `guard.rejected` / `decision.issued`）。**不声称"13 个逐一实测"**。

## 10. 非 sync 行为保持

`sync=False` 仍走 `_flush_batch()`（拒新不丢旧、**不抛**）；EVT-103 隔离不变；`_resolve_by_seq` 不改其语义。相关用例全绿。

## 11. `_fail_streak` 的 known behavioral change（conservative hardening）

**已记录（不重新设计）**：

修复后步骤 9 的 `flush` 不再空转，因此**磁盘故障下**一次 sync append 会经两处各计一次 `_fail_streak`：
- `persistence.append` 的 `except`（`:350`）
- `flush` 的 `except`（`:426`）

**影响**：`_fail_streak` 增长更快。因 `_suspended = True` **仅**在 `_flush_batch`（`:452`）置位，**同步路径本身不会触发暂停**；但会让后续异步批**更早**达到阈值——方向为**更保守**（fail-closed 一致）。

**分类**：`known behavioral change / conservative hardening`。
**未修改** `_fail_streak` / `_suspended` / `_RETRY_Q_LIMIT` 的任何逻辑。

## 12. Durability boundary

**= `write + fh.flush()`（进程级持久）**。

**不包含**：`os.fsync` · power-loss safety。

## 13. 未实现（明确边界）

**未实现**：`os.fsync` · WAL · power-loss safety · process-crash atomicity。

本修复**不是**"完整 durability solution"；**不声称**断电安全；**不声称** process-crash atomicity 已解决。process crash / 半行 / shutdown 中途仍由既有 `repair`(F060) 与 replay 的 PERS-201 坏行隔离承担。

## 14. 测试结果

| 项 | 结果 |
|---|---|
| F-SYNC-1 专项（`TestSyncDurability` 12 例 + 端到端 3 例） | 全绿 |
| `test_persistence.py` + `test_session.py` + `test_bus.py` | **131 passed** |
| **全量回归** | **1553 passed / 2 skipped / 0 failed** |
| 相对 S3-2-2 基线 1538 | **+15** |

**新增覆盖**：T1 首成功 · T2 首失败+二次成功 · T3 首失败+二次失败 · T4 多事件 batch · T5 retry order · T6 same-seq retry · T7 多 sync 类型 · T8 sync=False 回归 · **T10 同 seq 同内容去重** · **T11 同 seq 异内容 fail-closed** · **T12 retry 后 seq 唯一** · 端到端真链（Case A/B + async 不变）。

全部用例走**真实** `SessionStore`（仅对 `_fh.write` 注入故障），**未** mock 内部函数制造假成功。

## 15. 实际文件清单

| 文件 | 增/删 | 性质 |
|---|---|---|
| `pyharness/persistence.py` | **+48 / −5** | **唯一生产修改点** |
| `tests/unit/test_persistence.py` | **+179 / −0** | 测试 |
| `tests/unit/test_session.py` | **+73 / −1** | 测试（端到端真链） |

**禁止面零修改**：`session.py` · `event_bus.py` · `governance/*` · `tools_executor.py` · `approval.py` · `scope.py` · `events/*` · `ADR` · `REFACTOR_PLAN` · `RECORD` · receipt/evidence/audit。

## 16. 状态

# F-SYNC-1 = FIXED / PASS

**Fixed**：`pending_now` 不再丢失 · sync failure 不再 silent success · caller-visible `PERS-202` · retry 保序 · 全部 `SYNC_TYPES` 共用同一 contract · 同 seq 冲突 fail-closed。

**Not fixed**：`os.fsync` / power-loss safety · WAL · process-crash atomicity · 其它未验证的 crash consistency。

---

## Commit 白名单（已裁定 (a)）

本阶段 checkpoint 正式定义为 **4 个文件**：

| # | 文件 | 性质 |
|---|---|---|
| 1 | `pyharness/persistence.py` | 唯一生产修改点 |
| 2 | `tests/unit/test_persistence.py` | store 层测试 |
| 3 | `tests/unit/test_session.py` | **端到端验证文件（本阶段要求）** |
| 4 | `F-SYNC-1_CHANGE_REPORT.md` | 本报告 |

**`tests/unit/test_session.py` 属于正式 checkpoint**：它不是无关测试，而是本阶段明确要求的**真实端到端证据载体**，用于证明——

- sync failure → **caller-visible `PERS-202`**；
- retry recovery → **event 真正可读**；
- async / event isolation 回归（INV-F5）。

它构成 F-SYNC-1 修复的**验证闭环**（裁定：不采用纯 mock 断言取代真实 `SessionStore → adapter → retry → flush` 链）。

**未混入**：governance · executor · approval · scope · event_bus · session 之外的其它生产代码 · receipt/evidence/audit · ADR / REFACTOR_PLAN。
