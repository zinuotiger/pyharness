# S6-2b-1_CHANGE_REPORT.md — INV-01 不变量测试

> **阶段**：S6-2b-1（INV-01 Invariant Tests）
> **日期**：2026-09-15 ｜ **基线 commit**：**`c5fa851`**（INV-02 remediation final review 冻结）
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical INV-01）—— **未重新解释定义**
> **改动面**：**仅 1 个测试文件**（`tests/invariants/test_inv_core.py`，**+180 / −0**）· **生产代码零改动**
> **定向**：`tests/invariants` = **26 passed** ｜ **全量**：**1682 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1680 passed / 2 skipped / 0 failed**
> **对比基线**：1678 collected / 1676 passed ⇒ **+4 = 新增用例，零既有回归**
> **schema**：`EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`（**不变**）

---

## 1. INV-01 Canonical Definition（**照录，未改动**）

> 来源：`docs/INVARIANT_REGISTRY.md` §1 / `docs/CONSTRAINTS-06-Testing.md:62`

**日志只追加 / 历史必由日志派生（合并式）**

| 分句 | 内容 | 本阶段处理 |
|---|---|---|
| **①「日志只追加」** | 唯一真源、只追加（无 update/delete/改写 API）；append 后文件 hash 不变 | ✅ **Gap-1（物理面）+ Gap-2（静态面）新增用例**；**方法面**已有覆盖 ⇒ 不重复 |
| **②「历史必由日志派生」** | 一切历史与派生形态由日志派生，无第二份权威状态，可整体重建 | ✅ **已有充分覆盖** ⇒ 本阶段**不重复建设**（见 §3） |

**INV-01 未被修改**：`docs/INVARIANT_REGISTRY.md` 本轮 **0 改动**。

---

## 2. 新增测试（4 条，全部落在 `tests/invariants/test_inv_core.py`）

| # | 用例 | 证明的子性质 | 类型 |
|---|---|---|---|
| **T1** | `test_inv01_append_is_byte_level_append_only` | 分句① **物理面**：真实日志文件 append 后**旧字节逐字不变** ⇒ 操作是 **append 而非 rewrite** | 集成（真实 `SessionStore` + `SessionLog` + `EventBus`，走生产同一条落盘路径；`tmp_path`） |
| **T2** | `test_inv01_append_only_predicate_detects_rewrite` | **T1 判据的自检**：该字节判据**真能**识别 rewrite（否则是空转） | 单元自检（只操作 `tmp_path` 文件） |
| **T3** | `test_inv01_no_second_message_history_write_path` | 分句① **唯一真源（静态面）**：包内**每个**写盘站点都必须显式登记"为何不是第二份消息历史" | 静态（AST，文件级白名单 + 双向校验） |
| **T4** | `test_inv01_write_scan_detects_rogue_writer` | **T3 判据的自检**：扫描器能捕获"新增的第二条持久化路径"，且**不误报只读打开** | 单元自检（tmp 合成树，**不触碰生产代码**） |

### 2.1 T1 断言详解（对应 Gap-1 的全部要求）

| Gap-1 要求 | T1 的实现 |
|---|---|
| 使用**真实日志文件** | `open_store(sid, dir=tmp_path)` → 真实 `{sid}.jsonl`；经真实 `EventBus` 订阅落盘（镜像 `engine._record_to`） |
| 先取得文件 hash | `h_before = sha256(before)` |
| append 一个合法事件 | `log.append("user.message", {...}, actor="user")`（强同步 ⇒ 即写即刷） |
| 验证**旧内容未被修改** | `after[:len(before)] == before` |
| 验证原有**事件序列仍完整** | `[e.type for e in store.replay()] == ["session.created","user.message"]` |
| 验证操作是 **append 而非 rewrite** | 长度只增 + 前缀逐字节相同 + **前缀 hash 不变** |

> **前置条件分离**：`await store.flush()` 后断言 `before` 非空（`session.created` 非强同步，落在 `_pending`）。若该前置失败，错误信息为"**前置条件:首事件应已落盘(否则本用例无鉴别力)**" ⇒ 与"不变量被违反"**可区分**。

### 2.2 T3 白名单（逐条理由，防误报）

| 文件 | 登记理由 |
|---|---|
| `persistence.py` | SessionStore 追加句柄（`open 'a'`）+ **repair 专用**原子截断重写（`_rewrite_without_tail`：临时文件 + fsync + rename，**逐字节保留全部完整行**） |
| `repair.py` | 隔离坏行（quarantine 副本追加/重写），**不改主日志的完好行** |
| `cli.py` | CLI 配置导出/初始化（写 YAML 配置文件，非会话日志） |
| `core/spill.py` | 工具大结果 spill 落盘（非会话消息历史） |
| `core/tool_fs.py` | 文件工具 Provider（受 guard；非会话日志） |
| `core/skill_registry.py` | 技能包缓存元数据（非会话日志） |
| `core/tenant_settings.py` | 租户设置原子写（非会话日志） |

**防简单 grep 误报的措施**：① 用 **AST** 而非文本匹配；② 只认**字面量写模式**（`"w"/"a"/"wb"/…`）与 `write_text`/`write_bytes`；③ `os.open(...)` 因 mode 为**整数 flag** 无法静态判定 ⇒ **保守计入**（宁可多报不漏报）；④ **白名单双向校验**（既查"有写站点未登记"、也查"已登记却已无写站点"，防白名单过期掩盖回归）；⑤ **文件级**而非行级 ⇒ 不因文件上方增删而误报（回应上一轮 R-1 的脆弱性教训）。

---

## 3. 不重复建设：既有 INV-01 覆盖的归属说明

**本阶段未重复建设任何已充分覆盖的性质**：

| 已覆盖的子性质 | 既有用例（`tests/unit/`） | 本轮动作 |
|---|---|---|
| 无 update/delete/改写 API（方法面） | `test_session.py::test_method_surface_append_only_inv01` | ❌ **未重复** |
| 无第二份历史存储（结构面） | `test_session.py::test_append_only_no_second_history_store_inv02` | ❌ **未重复** |
| 历史只随 append 变化（行为面） | `test_session.py::test_derive_only_changes_via_append_inv02` | ❌ **未重复** |
| 内存态 ≡ 日志派生态 | `test_goal.py::test_rebuild_from_events_matches_live_state`、`test_plan_mode.py::test_real_session_full_lifecycle_events_valid` | ❌ **未重复** |
| 终态拒写落点 | `test_session.py::test_finished_flush_failure_keeps_closed_but_log_empty` | ❌ **未重复** |

**⇒ 本轮新增的 4 条，恰好覆盖 Registry 记录的 INV-01 两个真实缺口（Gap-1 / Gap-2），无一条与既有用例重合。**

---

## 4. 失败可诊断性（区分"实现缺陷 / 测试假设错误 / 环境问题"）

| 失败类型 | 在本测试中的表现 | 如何区分 |
|---|---|---|
| **实现缺陷** | 断言失败，消息**点名被违反的性质**：如 `"旧字节被改动 ⇒ 非 append(INV-01 违约)"`、`"INV-01 违约:出现未登记的写盘路径(疑第二份消息历史持久化)-> <file>:<行>"` | 消息含 **INV-01 违约** 字样且指向具体站点 |
| **测试假设错误** | **前置条件**断言失败，消息以 `前置条件:` 开头（如"首事件应已落盘(否则本用例无鉴别力)"）；或 T2/T4 自检失败（`判据漏判…` / `扫描器漏检…`） | 消息为**前置/自检**措辞，**不含** "INV-01 违约" |
| **测试自身无法覆盖** | 文件语法不可解析 ⇒ `AssertionError: 扫描无法覆盖 <file>(语法不可解析)` | 明确声明是**扫描范围**问题，非不变量问题 |
| **环境问题** | `tmp_path` 写入失败 ⇒ `OSError` → 存储层 `PERS-202`（非 AssertionError） | 异常类型不同（`OSError`/`PyHError` vs `AssertionError`）；且**全部用 `tmp_path`**，绝不写真实 `~/.pyharness` |

**本轮据此改进了一处**：`_write_sites` 原先 `except SyntaxError: continue`（**静默跳过**无法解析的模块 ⇒ 扫描不完整且可能掩盖写站点），已改为 **响亮失败**并指出文件。该改进由 mutation A 的失败症状暴露（见 §5）。

---

## 5. Mutation Check（**已执行、已验证 RED、已全部恢复**）

> 依要求："故意制造最小违反行为 … 验证测试能够 RED，然后恢复。不得留下 mutation。"

### Mutation A — 制造"rewrite"（针对 Gap-1）

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 | 备份 `pyharness/persistence.py` → `tmp/persistence_fixed.bak`（`tmp/` 已 gitignore） | — |
| 2 | **首次尝试失败（无残留）**：注入 `self._fh.seek(0)` 于写循环前 | ⚠️ **测试仍 GREEN** —— 因句柄以 `open(..., "a")` 打开 = **O_APPEND**，写永远落文件尾，`seek(0)` 被操作系统语义中和。**这本身是 INV-01 的结构性保证**（见 §6） |
| 3 | **改用真正的 truncate+rewrite**：把写循环替换为 `open(self.path, "w", ...)` 后逐行写入（同批次截断重写） | — |
| 4 | 运行 `-k inv01` | ✅ **`test_inv01_append_is_byte_level_append_only` RED**，断言消息：`旧字节被改动 ⇒ 非 append(INV-01 违约)`；diff 显示 `At index 7 diff: b'2' != b'1'` |
| 5 | 从备份恢复 `persistence.py` | 已恢复 |
| 6 | 核验无残留 | `git status --porcelain pyharness` **为空**；`git diff --stat HEAD -- pyharness` **空** ✅ |

### Mutation B — 制造"第二条持久化路径"（针对 Gap-2）

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 | 新建临时文件 `pyharness/core/_mut_rogue_writer.py`，内含 `Path("history.jsonl").write_text(...)` | — |
| 2 | 运行 `-k no_second_message_history` | ✅ **RED**，精确报出：`INV-01 违约:出现未登记的写盘路径(疑第二份消息历史持久化)-> core/_mut_rogue_writer.py:[6]` |
| 3 | 删除该临时文件 | 已删除（`ls` 确认不存在） |
| 4 | 核验无残留 | `git status --porcelain pyharness` **为空** ✅ |

### 恢复后复验

✅ `tests/invariants/test_inv_core.py` → **9 passed**（5 INV-02 + 4 INV-01）；**生产代码相对 `c5fa851` 零改动**（`git diff --stat HEAD -- pyharness` 空）。

**另**：T2 / T4 是**永久可复现的判据自检**（在 `tmp` 合成输入上验证"判据能识别违约"），等价于把 mutation check 固化为回归资产 —— **不需要也不依赖**任何生产代码改动。

---

## 6. 本轮观察（**非缺陷，是既有实现的结构强度**）

| 观察 | 说明 |
|---|---|
| **O_APPEND 是结构性保证** | Mutation A 首次尝试（`seek(0)`）**未能造成 rewrite** —— 因为落盘句柄以 `open(path, "a")` 打开（`persistence.py:601/642/644/742`），即 O_APPEND；POSIX/Windows 语义下写永远落文件尾，`seek` 无法把写指针移回文件头。⇒ **"只追加"不只靠约定，还被操作系统 flag 兜住一层**。这是强化 INV-01 的正面事实，**不构成本轮改动** |
| **repair 的原子截断重写是已声明的例外** | `persistence.py:577 _rewrite_without_tail` 会**重写**日志文件，但其语义是**截去尾部坏行并逐字节保留全部完整行**（临时文件 + fsync + `os.replace` 原子替换）；`repair.py` 的 quarantine 亦然。二者已在 T3 白名单中**逐条声明理由**，且**不改动完好行** ⇒ **不违 INV-01**（"修正破损"≠"改写历史"） |
| **会话日志写入面收敛** | 全包仅 **7 个文件**存在内容写盘站点，其中**只有 `persistence.py` / `repair.py` 与会话日志相关**，其余 5 个均为无关产物（spill / 文件工具 / 技能缓存 / 租户设置 / CLI 配置）。⇒ **不存在第二条消息历史持久化路径**（当前事实） |

**未发现真实生产缺陷** ⇒ **本轮无新增 KEY-FINDING**。

---

## 7. 边界与核验

| 项 | 结果 |
|---|---|
| 改动面 | **仅 `tests/invariants/test_inv_core.py`（+180 / −0）** ✅ |
| 生产代码 | **零改动**（`git status --porcelain pyharness` 空；`git diff --stat HEAD -- pyharness` 空）✅ |
| `docs/INVARIANT_REGISTRY.md` | **未修改** ✅ |
| Event Schema | `77 / 14 / 3` **未变** ✅ |
| Governance Core / Audit / Evidence / Receipt | **未修改** ✅ |
| L-3 / KF-B / F042 其他偏离 | **均未处理** ✅ |
| acceptance / security tests | **未创建** ✅ |
| INV-03~09 / S6-3 / S7 | **未进入** ✅ |
| mutation 残留 | **无** ✅ |
| `git diff --check` | 通过（无空白错误） |

---

## 8. 结论

| 项 | 结论 |
|---|---|
| **INV-01 两个已知 Gap** | ✅ **均已落编号化用例**（Gap-1 物理字节面 T1 · Gap-2 静态写路径面 T3），且各带**判据自检**（T2 / T4） |
| **是否重复建设** | ❌ **否** —— 既有 5 类覆盖逐条列明未重复（§3） |
| **失败可诊断性** | ✅ 四类失败**可区分**（§4），并据 mutation 症状**改进了一处静默跳过** |
| **Mutation check** | ✅ **A / B 双项执行 → 均 RED → 均已恢复 → 无残留**（§5） |
| **回归** | ✅ **1680 passed / 2 skipped / 0 failed**（+4 新增，零既有回归） |
| **schema** | ✅ `77 / 14 / 3` 不变 |
| **状态** | `IMPLEMENTED` + `TESTED` + `VERIFIED`（定向 + 全量 + mutation 均有实证） |

**本轮产出**：本报告 + `tests/invariants/test_inv_core.py` 的 INV-01 段（+180 行）。
**未 commit、未 push**（依指令）。**未自动开始 S6-2b-2。**

---

**S6-2b-1 结束。等待人工 review。**
