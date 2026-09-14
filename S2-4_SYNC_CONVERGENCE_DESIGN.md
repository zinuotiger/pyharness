# S2-4_SYNC_CONVERGENCE_DESIGN.md — sync 真源收敛设计（关闭 ADR-019 P-3）

> **阶段**：S2-4 设计（**只出设计，不落码**）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`0cb841d`（工作区 clean）
> **依据**：[ADR-019](ADR-019-s1-scope-adjudication.md) §2.3（P-3 漂移）· [ADR-020](ADR-020-policy-event-boundary.md) Q3（`policy.updated` 强同步）· [S2-2.1_CHANGE_REPORT.md](S2-2.1_CHANGE_REPORT.md) §5 R1
> **性质**：**未修改任何文件**。本文件按步骤 1 的事实核验结果撰写。

---

## 0. 结论摘要

漂移**已实测确认且只有 1 个名字**（`policy.updated`）；两处硬编码副本**彼此完全一致**、且是 `SYNC_TYPES` 的真子集。
**但收敛不是行为中性的**——本设计在核验中发现一个**比漂移更深的既存缺口**（§D-2）：把 `sync=` 从"11 名"机械扩到"12 名"，会把一个**静默丢失**路径扩大到 `policy.updated`。故 S2-4 存在一个必须裁定的选择 **Y-1**（§B.2 / §E.4）。

---

## A. 当前问题

### A.1 全库 sync 来源盘点（实测）

| # | 位置 | 形态 | 是否真源 |
|---|---|---|---|
| 1 | **`events/vocab.py:97`** | `SYNC_TYPES = frozenset({…})` —— **12 项**（含 `policy.updated`） | ✅ **唯一真源** |
| 2 | `core/session.py:321` | `if sync or env.type in SYNC_TYPES:` | ✅ 已派生 |
| 3 | `persistence.py:280` | `SYNC_TYPES: frozenset = SYNC_TYPES`（`SessionStore` 类属性） | ✅ 已派生 |
| 4 | `bus/event_bus.py:209` | `if type_ in SYNC_TYPES:`（强同步恒 sequential 分发） | ✅ 已派生 |
| 5 | `cli.py:611-617` | `from …vocab import SYNC_TYPES as _EVT_SYNC` → `sync=type_ in _EVT_SYNC` | ✅ 已派生 |
| 6 | `core/orchestration.py:138-145` | `from …vocab import EVENT_TYPES, SYNC_TYPES` → `sync=type_ in SYNC_TYPES` | ✅ 已派生 |
| 7 | **`engine.py:690`** | **`_SYNC = (…11 个名字…)`**（模块函数 `_record_to` 内） | ❌ **硬编码副本** |
| 8 | **`desktop/sessions.py:151-155`** | **内联 tuple（11 个名字）** | ❌ **硬编码副本** |
| 9 | `repair.py:710` | `_StoreSink` 恒 `sync=True` | ⚠️ **更保守**（恒强同步），非漂移 |
| 10 | 各模块 `append(..., sync=True)`（≈20 处） | **调用方显式声明**（`approval`/`tools_guard`/`task_queue`/`compaction`/`governance.policy` 等） | — 与本节无关（那是"本次调用要强同步"，非"类型级清单"） |

**⇒ 真源是 `vocab.SYNC_TYPES`；未派生的只有 2 处（#7 #8），即 ADR-019 所称的"P-3 漂移"。**

### A.2 `engine.py` 的 `_SYNC` 是什么

```python
# engine.py:690（位于 _record_to 内 —— 该函数产出 bus→store 的落盘订阅回调）
_SYNC = ("user.message", "guard.rejected", "approval.requested",
         "approval.granted", "approval.denied", "approval.timeout",
         "session.finished", "session.recovered", "segment.start",
         "fork.created", "context.compacted")            # ← 11 名，缺 policy.updated

async def _record(type_: str, payload: Any) -> None:
    if not hasattr(payload, "model_dump_json"):
        return                              # 瞬时事件(dict)不入库
    await store.append(payload, sync=type_ in _SYNC)     # engine.py:699 唯一使用点
```

**语义**：它是 **bus→store 落盘适配器**给 `SessionStore.append` 的 **`sync=` 提示位**——决定该事件在 store 层走"**即时 write+flush**"还是"**入 pending 攒批**"。

### A.3 `desktop/sessions.py` 的内联 tuple 是什么

```python
# desktop/sessions.py:148-155（DesktopSessionManager._attach_persistence 内）
async def _record(type_: str, payload: Any) -> None:
    if getattr(payload, "session_id", None) != sid:
        return                              # 跨会话事件:不落本文件(多会话隔离)
    await store.append(payload, sync=type_ in (
        "user.message", "guard.rejected", "approval.requested",
        "approval.granted", "approval.denied", "approval.timeout",
        "session.finished", "session.recovered", "segment.start",
        "fork.created", "context.compacted"))            # ← 11 名，缺 policy.updated
```

**语义**：与 #7 **同款**（桌面壳自己的 bus→store 适配器），额外多一层 **sid 过滤**（桌面共享全局总线，必须只落本会话）。

### A.4 判定

| 问题 | 结论 | 证据 |
|---|---|---|
| **内容是否一致（两副本之间）** | ✅ **完全一致** | 集合比对：`ENG == DESK` → `True`（各 11 名，逐一相同） |
| **语义是否一致（相对真源）** | ⚠️ **差 1 名** | `SYNC_TYPES - engine._SYNC = ['policy.updated']`；`engine._SYNC - SYNC_TYPES = []`（**无多余项**） |
| **是否存在未来漂移风险** | ❌ **高，且已实际发生 1 次** | 每次新增强同步事件都需人工改这 2 处；S2-2.1 加入 `policy.updated` 时**未同步这两处**（当时被本阶段边界刻意排除，登记为 R1） |

**漂移的实际后果（分两层）**：
- **耐久性**：**无缺口**。`session.append`（`session.py:321`）对 `SYNC_TYPES` 成员**独立 `_flush`**，`persistence.SessionStore.SYNC_TYPES`（`:280`）亦复用词表 → `policy.updated` 仍被强同步落盘。
- **实际差异**：那 2 处副本的 `sync=` 只影响它们**传给 `store.append` 的提示位**（即时写 vs 攒批）。**但**——见 §D-2，这个提示位**不是无害的**。

---

## B. 目标架构

### B.1 单一真源：`events/vocab.py`

```
                    events/vocab.py :: SYNC_TYPES  (唯一真源)
                                 |
        ┌────────────┬───────────┼───────────┬──────────────┐
        ▼            ▼           ▼           ▼              ▼
   engine.py   desktop/     core/        bus/         cli.py /
  (_record_to) sessions.py  session.py  event_bus.py  orchestration.py
        └────────────┴───────────┴───────────┴──────────────┘
                                 ▼
                        persistence.SessionStore
                     (SYNC_TYPES: frozenset = SYNC_TYPES)
```

**为什么由 `vocab.py` 作为唯一真源**：

| 理由 | 说明 |
|---|---|
| **语义内聚** | `SYNC_TYPES` 与词表注册表 `_CORE_EVENT_TYPES`（每项含 `transient` 标志）**同处一文件**——"某类型是否持久 / 是否强同步"是同一类元数据，拆开即产生第二真源 |
| **既成事实** | 6 个消费点中**已有 5 个**从该处派生（A.1 的 #2~#6）→ 收敛只是**补齐最后 2 处**，非新引入 |
| **既有测试已钉** | `test_persistence.py:497-503` 断言 `SessionStore.SYNC_TYPES == SYNC_TYPES`、成员资格 → 真源地位已被测试确认 |
| **INV-01 纪律** | ADR-018 的"`inputs_digest` 不得引入第二套"同款：类型级清单**不得有第二份** |
| **词表演进规则** | `EVENT-SCHEMA` §7 规则 1/5：新增类型须过词表登记 → 强同步标志应**随类型一并登记**，而非散落各壳 |

### B.2 ⚠️ 但"机械派生"不是行为中性的（**Y-1 待裁定**）

派生写法有两种，**语义不同**：

| 方案 | 适配器传的 `sync=` | 后果 |
|---|---|---|
| **Y-1(a) 机械派生**：`sync = type_ in SYNC_TYPES` | 12 类 → `True`（含 `policy.updated`） | 字面单一真源；但把 `policy.updated` 的写入**升级到 store 的即时写路径** → **把 §D-2 的静默丢失洞扩大到它** |
| **Y-1(b) 适配器恒不传（`sync=False`）**：强同步判定**完全交给 `session.append`**（`session.py:321` 本就读 `SYNC_TYPES`） | 全部 `False` | **消除**第二真源**且顺带关闭 §D-2 的洞**（见 D-2 分析）；耐久性不变；代价：11 个既有强同步事件的写路径由"适配器即时写"变为"session 层 flush 写"（同一 `append` 调用内，微秒级之后） |
| **Y-1(c) 机械派生 + 修 `persistence._flush_pending_all` 的丢弃语义** | 12 类 → `True` | 治本，但**超出本阶段范围**（`persistence.py` 不在允许清单） |

**建议 Y-1(b)**：它同时满足"单一真源"与"不制造新缺口"，且**落在允许文件范围内**（`engine.py` + `desktop/sessions.py`）。**但它是行为变更（11 类写路径）→ 需你裁定。**

> **⚖️ 裁定结果（2026-09-14，人工）**：**采纳 Y-1(a)** —— `event_type in SYNC_TYPES` 作为唯一同步判定来源。人工给出的理由：① 本阶段目标是关闭 `SYNC_TYPES` 多真源漂移；② 保持 S2 阶段零行为变化原则；③ 不改 adapter / session / persistence 的职责边界；④ 不在本阶段扩大到 `persistence.py`。
> **⇒ 上表"建议 Y-1(b)"未被采纳**（本块按"取代记录"体例**只追加**，不改原建议文字）。
> **⚠️ 对理由②的精确修正**："零行为变化"**仅对既有 11 类成立**；**`policy.updated` 的适配器 `sync` 由 `False` → `True`**（它不在旧副本里、但真源含它）——属**有意的行为对齐**，不是零变化。精确认定见 [S2-4.1_CHANGE_REPORT.md](S2-4.1_CHANGE_REPORT.md) §3。
> **`F-SYNC-1` 处理**：**仅登记（P1），不在 S2 范围内修复**；`persistence.py` / `session.py` / `event_bus.py` 均不改；另立 durability / reliability 评估阶段。
> **实施结果**：[S2-4.1_CHANGE_REPORT.md](S2-4.1_CHANGE_REPORT.md)（**PASS**；全量 1481 / 1479 passed / 2 skipped / 0 failed）。

---

## C. 修改范围设计

### C.1 允许修改

| 文件 | 改动（按 Y-1 分支） | 必需性 |
|---|---|---|
| **`pyharness/engine.py`** | 删除 `_SYNC` 定义（`:690-695`）；`_record` 内 `sync=type_ in _SYNC` → **Y-1(a)** `sync=type_ in SYNC_TYPES`（并在 `_record_to` 内 import）/ **Y-1(b)** 删去 `sync=` 参数 | **必需** |
| **`pyharness/desktop/sessions.py`** | 同款：删内联 tuple（`:151-155`） | **必需** |
| **`tests/unit/test_engine.py`** | 新增：engine 适配器的 `sync=` 派生断言（§E） | **必需** |
| **`tests/unit/test_desktop.py`** | 新增：桌面适配器的 `sync=` 派生断言（或落在 `test_persistence.py`） | **必需** |

### C.2 禁止修改

`pyharness/events/*`（词表已定：74 型 / `SYNC_TYPES` 12 项，S2-2.1 已验收）· `pyharness/governance/*` · 任何 ADR / `ARCHITECTURE_DECISION_RECORD.md` · `report.py` / `core/session.py` / `core/tools_executor.py`（与本次无关）。

**特别说明（超范围的唯一例外请求）**：若选 **Y-1(c)**，需改 `pyharness/persistence.py` 的 `_flush_pending_all`（`:364-366` 的丢弃语义）——**本设计不建议**（另立阶段）。

### C.3 不需要修改（已核验）

| 文件 | 理由 |
|---|---|
| `pyharness/cli.py` | 已 `from …vocab import SYNC_TYPES as _EVT_SYNC`（`:611`）→ 派生 |
| `pyharness/core/orchestration.py` | 已 import 词表（`:138`）→ 派生 |
| `pyharness/core/session.py` | 已用 `SYNC_TYPES`（`:321`） |
| `pyharness/persistence.py` | `SessionStore.SYNC_TYPES = SYNC_TYPES`（`:280`） |
| `pyharness/repair.py` | `_StoreSink` 恒 `sync=True`（更保守，非漂移） |

> **⇒ 收敛面恰好是 2 个源文件 + 2 个测试文件。**

---

## D. 风险分析

### D-1 `SYNC_TYPES` 是否包含所有需要强同步的事件

**是。** 三项交叉核对一致：

| 口径 | 内容 |
|---|---|
| `SYNC_TYPES`（实测 12） | `user.message` · `guard.rejected` · `approval.{requested,granted,denied,timeout}` · `session.{finished,recovered}` · `segment.start` · `fork.created` · `context.compacted` · **`policy.updated`** |
| `EVENT-SCHEMA` §1.2 强同步矩阵 | 与上列前 11 项一致 |
| ADR-020 Q3 | `policy.updated` **须**强同步 → 已入 |

**且无反向遗漏**：`engine._SYNC - SYNC_TYPES = []`（副本不含真源外的任何名字）。

### D-2 ⚠️ 删除内联 tuple **会改变行为** —— 且存在一个**既存静默丢失缺口**

**两条写路径的差异（实测 `persistence.SessionStore.append:316-344`）**：

| `sync=` | store 行为 | 失败语义 |
|---|---|---|
| **`True`** | `_pending.append` → **`_flush_pending_all()`**：立即 write+flush；`OSError` → `raise_code(PERS-202)` | **本次 pending 行丢弃**（`:346-366` 明示"强同步失败 = 不承诺"，不入 retry 队列） |
| **`False`** | `_pending.append`；≥64 条或 0.5s 定时器才 flush | 由 `flush()` 处理：`OSError` → **行回重试队列** + `PERS-202` 上抛（`:368-373`） |

**而适配器是总线订阅者 → 其异常被总线隔离**（实测 `event_bus.py:279`：`except Exception` → `EVT-103` 结构化日志 → **不外抛**）。

**⇒ 组合出的缺口（命名为 `F-SYNC-1`）**：

```
session.append(SYNC 类事件)
 ├─ 步骤8 _dispatch → bus.emit → _record → store.append(sync=True)
 │     └─ _flush_pending_all() 抛 OSError
 │           ├─ 该行已从 _pending 丢弃(persistence.py:357+365)
 │           └─ 异常被总线 EVT-103 隔离 → session.append 看不到
 └─ 步骤9 session.append 自己的 _flush(seq) → pending 已空 → 静默无错
 ⇒ append 返回"成功",但该事件未落盘
```

**实证（用真实代码路径复现，2026-09-14）**：把 `SessionStore` 的物理写句柄毒化为抛 `OSError`（模拟磁盘故障），并按 `engine._record_to` 的同款方式接总线，然后 `await log.append("user.message", …, sync=True)`：

| 观测项 | 实测 |
|---|---|
| `append` 是否抛错 | **否**（无异常） |
| 该行是否仍在 `_pending` | **否**（已被丢弃） |
| `_retry_q` 行数 | **0**（未回重试队列） |
| 落盘文件字节数 | **0** |

**⇒ 复现成立**：强同步事件在适配器内 flush 失败后**既未落盘、也未进重试队列、也不上抛调用方**。

> **精确措辞（避免夸大）**：这不是"全无留痕"——总线会记一条 **`EVT-103` 结构化错误日志**（`event_bus.py:279-280`）。但它**没有错误码上抛、没有重试、没有恢复路径**；对调用方与上层流程而言是静默的（`append` 返回成功、`session.finished` 等关键事件照常继续）。

**对照 `sync=False` 路径**：行留在 `_pending` → 步骤9 的 `flush(seq)` 写它 → 失败则**回重试队列 + PERS-202 上抛调用方** → 无丢失。

**关键判定**：
- 该缺口是**既存的、系统性的**——它影响**全部 11 个既有强同步事件**在 4 条适配器路径（engine / desktop / cli / orchestration，后两者同样传 `sync=True`）上的落盘。
- **S2-4 的收敛选择决定它是否扩散到 `policy.updated`**：
  - **Y-1(a)** → 扩散（新增第 12 个受影响事件）；
  - **Y-1(b)** → **不扩散，且顺带修复全部 12 个**（适配器不再干预，强同步判定与错误传播归 `session.append` 单点）。
- **⇒ 本设计建议 Y-1(b)。**

> **该缺口不在既往审计中**（`REFACTOR_PLAN` / `S1_EXIT_REVIEW` / `S2-1/2-2.1/2-3` 报告均未记录）→ 建议**登记为独立发现**（P1 级：数据完整性问题，触发条件为强同步事件落盘时磁盘故障）。

### D-3 desktop 路径与 engine 路径是否一致

**基本一致，两处差异均与本次无关**：

| 维度 | engine `_record_to` | desktop `_attach_persistence` |
|---|---|---|
| `sync=` 语义 | 同款（本次收敛目标） | 同款 |
| 瞬时过滤 | `hasattr(payload, "model_dump_json")` 跳过 dict | **无**（但瞬时事件本就不进 JSONL；desktop 靠 sid 过滤兜底） |
| 会话过滤 | 无（engine 单会话单 bus） | **有** `payload.session_id != sid` → skip（多会话共享全局 bus，必须） |

### D-4 测试覆盖现状

| 目标 | 现有覆盖 | 缺口 |
|---|---|---|
| `policy.updated` | `test_events.py`（注册/成员）· `test_governance_policy.py`（T6c 落盘 + `SYNC_TYPES` 成员断言） | ✅ 已覆盖；**未覆盖**"engine/desktop 适配器对该类型的 `sync=` 取值" |
| `scope.updated` | `test_events.py` · `test_scope.py` · `test_wiring_fixes.py` | ✅ 已覆盖（且它**不在** `SYNC_TYPES`，是非强同步对照） |
| session append / flush / 强同步 | `test_events.py` · `test_persistence.py`（`SessionStore.SYNC_TYPES == SYNC_TYPES`） | ⚠️ **无测试断言两个适配器的 `sync=` 派生**——正是本次要补的 |

---

## E. 测试计划

### E.1 新增测试

| # | 落点 | 用例 | 断言 |
|---|---|---|---|
| **T1** | `tests/unit/test_engine.py` | `test_sync_adapter_derives_from_vocab` | 用记录型 store 替身驱动 `engine._record_to(store)` 的回调，对**每类**事件断言 `传入 store.append 的 sync == (type in SYNC_TYPES)`（含 `policy.updated` 为 `True`、`scope.updated` 为 `False`） |
| **T2** | `tests/unit/test_desktop.py`（或 `test_persistence.py`） | `test_desktop_sync_adapter_derives_from_vocab` | 同款，覆盖 `DesktopSessionManager._attach_persistence`（含 sid 过滤：异会话事件不落） |
| **T3** | `tests/unit/test_persistence.py` | `test_sync_types_single_source_no_hardcoded_copies` | **防回归**：断言源码中**不再存在**硬编码 sync 清单——AST 扫描 `engine.py` / `desktop/sessions.py`，若出现"≥5 个字面量的 sync 名元组/集合"即失败（R-F/R-2 同款做法） |
| **T4**（Y-1(b) 专属） | `tests/unit/test_persistence.py` | `test_adapter_never_forces_sync` | 断言适配器调用 `store.append` 时 `sync` 恒 `False`；并断言强同步仍生效（`session.append` 对 `SYNC_TYPES` 成员调用 `flush`） |
| **T5**（若采纳 Y-1(a)） | `tests/unit/test_persistence.py` | `test_F_SYNC_1_silent_loss_hole`（**xfail/marker**） | 用会抛 OSError 的 store 替身复现 `F-SYNC-1`（强同步事件在适配器内 flush 失败 → 静默丢失），**作为已知缺口的红测试登记**（不因它阻塞 S2-4）。<br>⚠️ **本阶段未交付**（实施时未纳入步骤清单）——建议**随 durability / reliability 阶段**一并补上，作为修复的验收锚。见 [S2-4.1_CHANGE_REPORT.md](S2-4.1_CHANGE_REPORT.md) §4 待办 ② |

### E.2 修改测试

**无。** 既有测试（`test_events` / `test_persistence` / `test_scope` / `test_governance_policy`）**不应改动**——本次只改 2 处适配器的派生方式，类型级清单真源未变。

### E.3 验收标准

| # | 判据 |
|---|---|
| 1 | `set(engine 适配器用的 sync 清单) == SYNC_TYPES`（或 Y-1(b)：适配器恒 `False` 且 session 层 flush 覆盖全部 12 类） |
| 2 | 两处适配器对 `policy.updated` 与 11 个既有类型的 `sync=` 取值**一致**（与真源一致） |
| 3 | 全量回归绿（基线 `1476 / 1474 passed / 2 skipped / 0 failed`） |
| 4 | AST 守卫（T3）通过：源码中无第二份硬编码 sync 清单 |

### E.4 需你裁定（**1 项**）

| # | 事项 | 建议 |
|---|---|---|
| **Y-1** | (a) 机械派生 `sync=type_ in SYNC_TYPES` ／ (b) 适配器恒 `sync=False`（强同步判定与错误传播归 `session.append`，顺带关闭 `F-SYNC-1`）／ (c) (a)+修 `_flush_pending_all`（超范围） | **Y-1(b)** —— 单一真源 **且** 不扩散缺口；若你只接受"最小收敛"，则 (a) 并把 `F-SYNC-1` 登记为独立发现（建议 P1）待后续阶段修 |

> **⚖️ 已裁定（2026-09-14）**：采纳 **(a)**（**非**上表建议的 (b)）——详见 §B.2 的"裁定结果"块。本表**保留原建议作为决策前记录**，不修改其文字。

**另建议（非阻塞）**：把 `F-SYNC-1` 登记进 `KEY-FINDINGS.md`（本次核验新发现，未见于既往审计）。
> **实施差异说明**：该建议**未在本阶段执行**——`docs/KEY-FINDINGS.md` 不在 S2-4 允许修改范围内；`F-SYNC-1` 的登记载体为 [S2-4.1_CHANGE_REPORT.md](S2-4.1_CHANGE_REPORT.md) §4，待后续允许的阶段转入长期归档。

---

## F. 边界声明

| 项 | 状态 |
|---|---|
| **未修改任何文件** | ✅（本文件为唯一新增） |
| **未 commit** | ✅ |
| 未进入 S2-4.1 落码 | ✅ |
| 未修改 `events/*` / `governance/*` / ADR | ✅ |

**等待人工批准 S2-4.1（含 Y-1 裁定）。**
