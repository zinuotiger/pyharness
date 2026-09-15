# S6-2b-1_FINAL_REVIEW.md — INV-01 不变量测试最终审查

> **阶段**：S6-2b-1 Final Review（**只读独立核验**）
> **审查对象**：S6-2b-1 产出（`tests/invariants/test_inv_core.py` 的 INV-01 段 +180/−0 · `S6-2b-1_CHANGE_REPORT.md`）
> **日期**：2026-09-15 ｜ **基线 commit**：`c5fa851` ｜ **HEAD** `c5fa851` ｜ ahead 34
> **本文件性质**：**只读审查**。**未新增测试 · 未修改生产代码 · 未修改 Registry / Event Schema / Governance Core**；**未 commit、未 push**（Freeze 提交在判定后执行）。
> **核验方式**：**独立复算** —— 重新加载扫描器模块并复跑、独立枚举全包写面 API、重跑全量回归；不引用变更报告的自述。

---

## 1. INV-01 定义（**Canonical Source of Truth**）

> 来源：`docs/INVARIANT_REGISTRY.md` §1（**本轮 0 改动**，`git diff` 逐项 = 0）

**INV-01 =「日志只追加 / 历史必由日志派生」（合并式）**

| 分句 | Canonical Definition 要点 |
|---|---|
| **①** | 会话 JSONL 日志是**唯一真源**且**只追加**（无 `update`/`delete`/改写/清空 API） |
| **②** | 一切历史与派生形态（消息历史 / 统计 / UI / FTS / 预算 / todo）**均由日志派生**；**不存在第二份权威状态**；派生视图可**整体丢弃重建** |

⇒ 本审查**逐子性质**判断，**不因用例增加而整体标为 covered**（依人工要求）。

---

## 2. 新增测试与子性质映射（**独立复核**）

| # | 用例 | 映射子性质 | 独立复核结论 |
|---|---|---|---|
| **T1** | `test_inv01_append_is_byte_level_append_only` | **物理 append-only（只追加）** + **历史前缀不可变** | ✅ 覆盖。断言：旧前缀逐字节相同 · 前缀 `sha256` 不变 · 长度只增 · `replay()` 事件序列完整。走**生产同一条落盘路径**（真实 `SessionStore` + `SessionLog` + `EventBus`；镜像 `engine._record_to`），非 mock |
| **T2** | `test_inv01_append_only_predicate_detects_rewrite` | **T1 判据自身可靠性** | ✅ 覆盖。tmp 内构造"合法 append"与"原地改写"两情形，断言判据分别在两侧给出 True / False |
| **T3** | `test_inv01_no_second_message_history_write_path` | **唯一历史写入口（静态面）** | ⚠️ **覆盖但范围受限**（见 §4） |
| **T4** | `test_inv01_write_scan_detects_rogue_writer` | **静态扫描器自身可靠性** | ✅ 覆盖。tmp 合成树验证：rogue writer **必报出**；只读 `open` **必不误报** |

**四问逐条回答（人工指定）**：
- **物理 append-only** → ✅ T1（+ T2 自检）
- **历史前缀不可变** → ✅ T1（前缀逐字节 + 前缀 hash 双断言）
- **唯一历史写入口** → ⚠️ T3，**部分覆盖**（扫描器只认直接内容写盘形式）
- **静态扫描器自身可靠性** → ✅ T4（自检）+ T2（判据自检）

### 2.1 `repair rewrite` 是否被误判为违规（**人工指定核实**）

| 项 | 结论 |
|---|---|
| `persistence.py:577 _rewrite_without_tail` | **未被误判** —— `persistence.py` 在白名单内，且条目理由**逐字写明**："repair 专用原子截断重写（临时文件 + fsync + rename，**逐字节保留全部完整行**）" |
| 语义判定 | 该重写的语义是**截去尾部坏行并保留全部完整行**（`src.read(cut_offset)`，字节级切点）⇒ **"修正破损" ≠ "改写历史"**，**不违 INV-01** |
| `repair.py` 的 quarantine（`:499` 追加 / `:533` wb 副本） | **未被误判** —— 同样在白名单内，理由："隔离坏行，**不改主日志的完好行**" |
| 复核手段 | 独立复跑扫描器：`repair.py` 与 `persistence.py` **均在 flagged 集合内**，且二者**均已在白名单** ⇒ 测试 GREEN 是"已登记"而非"漏检" |

---

## 3. 白名单核验（人工指定：允许路径 / 原因 / 风险 / 当前证据）

**独立复跑扫描器的实际输出**（直接加载测试模块调用 `_write_sites`）：

```
cli.py: [1551]
core/skill_registry.py: [236]
core/spill.py: [300]
core/tenant_settings.py: [95]
core/tool_fs.py: [374]
persistence.py: [152, 587, 601, 642, 644, 742]
repair.py: [281, 483, 499, 533]
未登记: 无      过期: 无
```

| # | 允许路径 | 真实用途（**独立读源码确认**） | 风险 | 当前证据 |
|---|---|---|---|---|
| 1 | `persistence.py` | 会话日志**追加句柄**（`open 'a'`，:601/642/644/742）+ **repair 专用**原子截断重写（:587 `"wb"`）+ 跨进程锁（:152 `os.open(O_RDWR\|O_CREAT)`） | **中**：本文件**确实**拥有会话日志的物理写权；白名单是"知情的"而非"放宽的" | 唯一持有会话日志物理写权的模块（`store.path` / `_fh` 均在此） |
| 2 | `repair.py` | 坏行隔离（`:499` 追加 quarantine / `:533` 写副本 / `:281` `os.open(O_RDONLY)` / `:483`） | **低**：只动 quarantine 副本，不改主日志完好行 | 语义：修破损，保完好行 |
| 3 | `cli.py:1551` | `path.write_text(yaml.safe_dump(config.DEFAULTS, …))` —— **CLI 配置导出/初始化** | **低**：写 YAML 配置文件，与会话日志无关 | 独立读源码确认目标为配置路径 |
| 4 | `core/spill.py:300` | 工具大结果 spill 落盘（`open(tmp,"w")`） | **低**：spill 私有区，非消息历史 | 模块职责为 spill（F039） |
| 5 | `core/tool_fs.py:374` | 文件工具 Provider 原子写（tmp + `os.replace`） | **低**：受 guard 的工具能力，非会话日志 | 模块职责 = 文件工具（F034） |
| 6 | `core/skill_registry.py:236` | 技能包缓存元数据 `.pyharness-version` | **低**：技能包目录，非会话日志 | 模块职责 = 技能注册 |
| 7 | `core/tenant_settings.py:95` | 租户设置原子写（`write_bytes` + `os.replace`） | **低**：设置文件，非会话日志 | 模块职责 = 租户设置 |

### 3.1 「是否为了让测试通过而放宽白名单」

**结论：否。** 证据链：

1. **白名单先于测试运行**：7 条由**独立的 AST 勘察**（上一阶段）先行得出，随后测试首跑即 GREEN；**不是"先失败再逐条加白名单"**。
2. **集合完全吻合**：flag 集合（7 文件）与白名单（7 文件）**逐一对应**，且 **双向校验为空**（无未登记、无过期）⇒ 白名单**未刻意避开**任何 flagged 文件。
3. **每条都有"为何不是第二份消息历史"的实质理由**（上表），且经**独立读源码**逐条确认，非模板化措辞。
4. **反向检查**：被我独立发现的**未被扫描器捕获**的写路径（`core/kv.py`，见 §4）**并未因此被塞进白名单** ⇒ 白名单**没有**被当作"掩盖漏检"的工具。

### 3.2 「是否存在未登记的第二写路径」

| 层面 | 结论 |
|---|---|
| **扫描器可检出范围内** | ✅ **无** —— 未登记 = 空 |
| **扫描器覆盖范围外** | ⚠️ **存在未被检出的写路径**（`os.fdopen` / sqlite / shutil / `os.replace`）；经**独立枚举**逐一定性，**均非第二份消息历史**（详见 §4.3、§7） |

### 3.3 白名单过期风险

| 项 | 结论 |
|---|---|
| 已有防护 | ✅ **双向校验**：既断言"有写站点但未登记"（`unlisted`），也断言"已登记但已无写站点"（`stale`）⇒ 覆盖"新增漏登记"与"删除后白名单过期掩盖回归"两个方向 |
| 残余风险 | **低—中**：若未来新增写站点时，理由写成"不是会话日志"这类**空洞措辞**，白名单会退化为橡皮图章。**缓解**：条目理由须具体到"该文件写什么、为何不是消息历史"（现 7 条皆满足） |

---

## 4. Scanner Integrity（人工指定：只分析，不增强）

### 4.1 `SyntaxError` 处理 —— ✅ **已修正且符合要求**

```python
        except SyntaxError as e:                     # 扫描不完整 ⇒ 响亮失败,不静默
            raise AssertionError(
                f"扫描无法覆盖 {p.relative_to(root).as_posix()}(语法不可解析): {e}") from e
```

| 要求 | 结论 |
|---|---|
| **不再静默跳过** | ✅ 已由 `continue` 改为 `raise AssertionError`（独立读当前源码确认） |
| **无法解析模块必须显式失败** | ✅ 失败消息**指名文件**并说明"扫描无法覆盖"，与"INV-01 违约"措辞**可区分** |
| 该修正的来源 | 由 mutation A 的失败症状暴露（当时症状为"白名单过期: persistence.py"，实为文件被变异破坏语法后被静默跳过）⇒ **本轮已修复** |

### 4.2 AST 扫描边界（当前实现）

| 检出 | 不检出 |
|---|---|
| `open(path, "<字面量写模式>")`（含 `mode=` 关键字形式） | 非字面量 mode **且** 非 `os.open`（实测：仅 `persistence.py:152`、`repair.py:281`，前者为 `os.open` 被保守计入，后者为 `O_RDONLY` 也被计入） |
| `os.open(...)` —— **保守全收**（mode 为整数 flag） | `os.fdopen(fd, ...)` · `os.write` · 已持有句柄上的 `fh.write` |
| `write_text` / `write_bytes` | `sqlite3` 写（`db.execute(INSERT/CREATE/DELETE)`） |
| 写模式集合：`w/a/wb/ab/w+/a+/x/xb/r+/r+b/w+b` | `shutil.copy*` / `shutil.move` · `os.replace` / `os.rename` · `json.dump(obj, fh)`（其 `open` 侧可被检出） |
| **只扫 `pyharness/**`**（运行时包） | `scripts/**`（人工探针/e2e，不在运行时边界，已在文件 docstring 声明） |

### 4.3 误报（false positive）风险

| # | 情形 | 判定 |
|---|---|---|
| 1 | `repair.py:281` `os.open(path, O_RDONLY)` —— **只读**却被保守计入 | **已知误报**，被白名单吸收（`repair.py` 已登记）⇒ 不产生测试失败，但需如实声明 |
| 2 | 只读 `open(p, "r")` | ✅ **不误报** —— 由 T4 的 `reader.py` 合成用例**显式断言** |
| 3 | `open(p)` 单参（默认只读） | ✅ 不计入（无 mode 实参、无 `mode=` 关键字） |

### 4.4 漏报（false negative）风险 —— ⚠️ **实测存在，且本轮发现一处具体案例**

| # | 未覆盖的动态/间接写模式 | 实测命中 | 是否 INV-01 违规 |
|---|---|---|---|
| **1** | **`os.fdopen(fd, "w")`** | **`core/kv.py:65`** —— 会话级 KV 存储 `<sid>.kv.json`（插件存储域） | ❌ **不是** —— 该模块 docstring 明写"KV 是插件存储域（**非会话日志**），不进事件词表"；且非消息历史 |
| 2 | `sqlite3` 写 | `core/session_query.py`（FTS 投影索引；`rebuild()` 做 `DELETE FROM` + 重插） | ❌ **不是** —— **派生视图**，可整体丢弃重建（`Registry` INV-01 ② 明确允许"派生视图可整体丢弃重建"） |
| 3 | `shutil.copy*/move` | `core/plugin_loader.py:50,51` · `core/skill_registry.py:229,234,235` | ❌ **不是** —— 插件/技能包文件搬运 |
| 4 | `os.replace` / `os.rename` | `core/kv.py:61,67` · `spill.py:302` · `tenant_settings.py:96` · `tool_fs.py:350,375` · `persistence.py:592` | ❌ **不是** —— 原子替换（其 `open`/`fdopen` 侧多已被检出；`kv.py` 除外） |

**⇒ 关键结论（必须如实声明）**：
- T3 的 GREEN 含义**限于**「**不存在未登记的、以直接内容写盘形式出现的**第二条持久化路径」；
- 它**不能**被读作「不存在任何形式的第二份持久化」；
- **但**经**独立全量枚举**（上表 + §3.2），当前所有写面**要么非会话日志域、要么为可重建的派生投影** ⇒ **INV-01 的"无第二份权威状态"在当前代码上成立**；
- ⇒ 该漏报面是**扫描器的覆盖范围限制**，**不是**当前存在违规。**本轮只分析，不增强扫描器**（依边界）。

---

## 5. Mutation Evidence（**独立复核历史结果，本轮不重跑**）

| 项 | 结果 | 独立确认 |
|---|---|---|
| **Mutation A**（`persistence._flush_pending_all` → `open(path,"w")` 截断重写） | ✅ **T1 RED** —— 断言消息 `旧字节被改动 ⇒ 非 append(INV-01 违约)`，diff `At index 7 diff: b'2' != b'1'` | 变更报告 §5 留证；**A 的首次尝试（`seek(0)`）未能制造违约** —— 因句柄为 `O_APPEND`（**INV-01 的结构性保证**） |
| **Mutation B**（新建 `core/_mut_rogue_writer.py` 含 `write_text`） | ✅ **T3 RED** —— 精确报 `INV-01 违约:出现未登记的写盘路径(疑第二份消息历史持久化)-> core/_mut_rogue_writer.py:[6]` | 变更报告 §5 留证 |
| **当前正确实现 → GREEN** | ✅ 独立复跑 `tests/invariants/test_inv_core.py` = **9 passed** | **本轮已复跑确认** |
| **mutation 文件无残留** | ✅ `ls pyharness/core/_mut_rogue_writer.py` → **No such file**；`git status --porcelain pyharness` → **空** | **本轮已复验** |
| 本轮是否重跑 mutation | ❌ **未重跑** —— 重跑需临时改生产代码，违本轮"只读 / 不修改生产代码"边界；且 **T2 / T4 是永久可复现的判据自检**，等价于把 mutation 固化为回归资产 | — |

---

## 6. 全量回归（**独立复跑**）

| 项 | 独立复算结果 |
|---|---|
| **全量** | ✅ **1682 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1680 passed / 2 skipped / 0 failed**（42.8s，exit 0） |
| **基线** | `c5fa851`：1678 collected / 1676 passed |
| **差值** | **+4 passed = 新增的 4 条 INV-01 用例** ⇒ **零既有回归** ✅ |
| **Event Schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |
| **Governance Core** | `pyharness/governance` 自 `c5fa851` **0 改动** ✅ |
| **Event Schema 文档** | `docs/EVENT-SCHEMA.md` **0 改动** ✅ |
| **生产代码** | `git diff --stat HEAD -- pyharness` **空** ✅ |
| **S6-1 Registry** | `docs/INVARIANT_REGISTRY.md` **0 改动** ✅ |

---

## 7. INV-01 Residual Gaps（**逐子性质判断，不整体标 covered**）

> 依人工要求：按 Registry 定义**逐项**判定，四种状态 = `covered` / `partial` / `gap` / `deferred`

| # | 子性质（按 Registry 定义拆解） | 状态 | 依据 / 缺口 |
|---|---|---|---|
| 1 | 无 `update`/`delete`/改写/清空 **API**（方法面） | ✅ **covered** | `test_session.py::test_method_surface_append_only_inv01`（公开方法白名单 + 禁用片段） |
| 2 | **`append` 后文件 hash 不变**（物理面） | ✅ **covered**（本轮新增） | T1（前缀逐字节 + 前缀 sha256 + 只增长 + replay 完整）+ T2 判据自检 |
| 3 | **无第二份历史存储**（内存结构面） | ✅ **covered** | `test_session.py::test_append_only_no_second_history_store_inv02`（实例属性集合） |
| 4 | 历史**只随 `append` 变化**（行为面） | ✅ **covered** | `test_session.py::test_derive_only_changes_via_append_inv02` |
| 5 | 派生视图**可整体丢弃重建**（②） | ✅ **covered** | INV-03 面：`test_session.py::test_rebuild_from_log_invariant_inv03` · `::test_rebuild_derived_cache_invalidation_on_append` · `test_session_query.py::test_rebuild_full_matches_incremental` |
| 6 | **唯一写入口**（静态面，无第二份持久化） | ⚠️ **partial**（本轮新增） | T3/T4 覆盖"**直接内容写盘**"形式；**漏报面**（§4.4）：`os.fdopen`（`kv.py:65` 实例）· `sqlite3`（FTS 投影）· `shutil` · `os.replace`。当前**无违规**（皆为非会话日志域或可重建投影），但**静态保证不完整** |
| 7 | **`derive` 结果 == 逐事件重放**（`CONSTRAINTS-06:62` 列明） | ⚠️ **partial** | 由 `test_goal.py::test_rebuild_from_events_matches_live_state`、`test_plan_mode.py::test_real_session_full_lifecycle_events_valid` **间接**覆盖；**缺** `SessionLog.derive_messages()` 自身的"≡ 逐事件重放"编号化断言 |
| 8 | **跨进程**单写者下的 append-only | ⚪ **deferred** | `INV-07` 面（`test_persistence.py::test_cross_process_lock_blocks_second_writer`）已覆盖"互斥"，但"并发追加后前缀不变"未作编号化断言；**非本轮范围** |
| 9 | 扫描器覆盖增强（fd/sqlite/shutil/rename） | ⚪ **deferred** | **本轮只分析不增强**（依边界）；建议随 S6-2b 后续或独立小步补齐 |

**汇总**：`covered` **5** 项 · `partial` **2** 项 · `gap` **0** 项 · `deferred` **2** 项。

> **Registry 记录的两个缺口的关闭情况**：
> - **Gap-1（append 后文件 hash 不变）** ⇒ ✅ **已关闭**（子性质 #2 covered）
> - **Gap-2（除允许入口外无第二写路径）** ⇒ ⚠️ **部分关闭**（子性质 #6 partial：直接写盘形已覆盖，间接/动态写面未覆盖）

**⚠️ 明确声明**：**INV-01 不因本阶段而标为"完全 covered"** —— 仍为 `partial`（子性质 #6 / #7）。这与 Registry 中 INV-01 的 `Coverage Gap` 字段现有表述一致（该字段未在本轮修改）。

---

## 8. 是否满足 Freeze

| Freeze 前置 | 结果 |
|---|---|
| 4 条新用例正确映射子性质 | ✅ |
| 判据自检齐备（T2 / T4） | ✅ |
| mutation A / B 均 RED 且已恢复、无残留 | ✅（独立复验无残留） |
| 白名单未经"为过测试而放宽" | ✅（3.1 证据链） |
| `repair rewrite` 未被误判为违规 | ✅（2.1） |
| `SyntaxError` 不再静默跳过 | ✅（4.1） |
| 全量回归 ≥ 基线且零既有回归 | ✅ 1680 / 2 / 0 |
| schema 77/14/3 · Registry / Governance Core / 生产代码零改动 | ✅ |
| 未进入 INV-03 / 未建 acceptance·security tests / 未改 KF-B·L-3·F042 | ✅ |

⇒ ✅ **满足 Freeze 条件**。

---

## 9. 是否允许进入 S6-2b-2

| 项 | 结论 |
|---|---|
| 阻塞项 | ❌ **无** —— 残余项（#6 partial / #7 partial / #8·#9 deferred）**均非阻塞**：前者为**扫描范围限制**（当前无违规）+ **一处间接覆盖**；后者为**明确延期** |
| 结论 | ✅ **允许进入 S6-2b-2**（**不自动开始**，等待人工授权） |

---

## 10. 最终判定

> # ✅ **S6-2b-1 = PASS**

| 标签 | 值 |
|---|---|
| **S6-2b-1** | **PASS** |
| **INV-01 Gap-1** | ✅ **CLOSED** |
| **INV-01 Gap-2** | ⚠️ **PARTIAL**（直接写盘面已覆盖；间接/动态写面未覆盖，当前无违规） |
| **INV-01 整体** | **`partial`**（**非 fully covered**） |
| **生产代码 / Registry / Event Schema / Governance Core** | 均 **零改动** |
| **S6-2b-2** | **READY**（不自动开始） |

**判定依据**：① 4 条用例**逐条映射** Registry 的子性质，且各带判据自检；② 白名单经**独立复算**与**逐条读源码**核验，**未为过测试而放宽**，`repair rewrite` **未被误判**；③ `SyntaxError` 已改为**响亮失败**；④ mutation A/B **双 RED 且无残留**（独立复验）；⑤ 全量 **1680/2/0**，**+4 零回归**，schema 与冻结面**逐项 0 改动**；⑥ 残余缺口**逐项列明**并**未掩盖**（含本轮新发现的 `kv.py` 扫描漏报实例）。

---

**S6-2b-1 Final Review 结束。判定 = PASS。等待 Freeze 提交；未 push。**
