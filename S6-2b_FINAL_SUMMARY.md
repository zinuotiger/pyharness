# S6-2b_FINAL_SUMMARY.md — S6-2b 阶段总结（只读整理）

> **阶段**：S6-2b（不变量测试面：INV-01 ~ INV-04）
> **日期**：2026-09-15 ｜ **HEAD**：**`b65d102`** ｜ `main...origin/main [ahead 38]` ｜ worktree **clean**
> **性质**：**只读整理**。本文件由既有 checkpoint 与 review 文档汇总而成，**未修改生产代码 / 测试 / Registry / Event Schema**；**未处理 KF-B / FC-8 / residual limitations**；**未 commit、未 push**。
> **语义来源**：`docs/INVARIANT_REGISTRY.md`（Canonical Invariant Registry，S6-1 冻结）
> **测试基线**（本次复跑）：**1695 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1693 passed / 2 skipped / 0 failed** ｜ `tests/invariants` = **39 passed** ｜ `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`

---

## 1. S6-2b 目标

**把 Canonical Invariant Registry 中的不变量逐个转化为真实、可执行、可重复验证的编号化不变量测试**，并在此过程中**如实暴露**实现与规格之间的差距（而不是把它们掩盖成"测试通过"）。

| 项 | 内容 |
|---|---|
| **冻结依据** | `ARCHITECTURE_DECISION_RECORD.md` §5.2 的 S6 行（M8「不变量测试面」）· `docs/INVARIANT_REGISTRY.md`（唯一编号与语义来源） |
| **本阶段范围** | **INV-01 / INV-02 / INV-03 / INV-04**（INV-05~09 未开始） |
| **落点** | **单一文件**：`tests/invariants/test_inv_core.py`（延续"同一文件逐条扩展，不另建第二套"的纪律） |
| **纪律** | ① 语义只取自 Registry，**不据旧测试注释重新解释编号**；② **零生产代码改动**为默认；③ 不为过测试而放宽断言、不用 `xfail`/`skip` 掩盖真违约 |

### 1.1 本阶段的两类产出

| 类别 | 说明 |
|---|---|
| **测试资产** | `tests/invariants/test_inv_core.py` —— **19 个测试函数**（INV-01×4 · INV-02×5 · INV-03×3 · INV-04×7），共 **29 条执行用例**（含参数化展开） |
| **发现与设计记录** | **1 条 P0 违约（KF-A，已修复并 CLOSED）** · 1 条 P1 控制面缺口（KF-B，OPEN）· 1 条范围裁定（F-1）· 4 份 COVERAGE_AUDIT/CHANGE_REPORT/FINAL_REVIEW/PLAN · 1 份 Midpoint Review |

---

## 2. INV-01 ~ INV-04 覆盖状态

> 判定口径（全程一致）：**"若该子性质被破坏，对应测试是否必然失败？"** —— **不因"测试存在"自动判 `covered`**。

| INV | Canonical 语义 | `covered` | `partial` | `gap` | `deferred` | 关键证据 |
|---|---|---:|---:|---:|---:|---|
| **INV-01** | 日志只追加 / 历史必由日志派生 | **5** | **2** | 0 | **2** | `S6-2b-1_FINAL_REVIEW.md` §7 |
| **INV-02** | 无绕过 agent-loop 直调 llm | **covered**（单语义面） | 0 | 0 | 0 | `S6-2a-P0-F_FINAL_REVIEW.md` §12 |
| **INV-03** | rebuild 与缓存一致 | **8** | **0** | 0 | **3** | `S6-2b-2_FINAL_REVIEW.md` §8 |
| **INV-04** | 无 guard 事件即非法执行（含单调拒绝 / 无 bypass） | **12** | **0** | 0 | **2** | `S6-2b-3_FINAL_REVIEW.md` §9 |

### 2.1 逐条明细

**INV-01**（5 / 2 / 0 / 2）
- ✅ covered：无改写 API（方法面）· **append 后文件 hash 不变**（物理面，本阶段关闭 Gap-1）· 无第二份历史存储 · 历史只随 append 变化 · 派生视图可整体重建
- ⚠️ partial：**⑥唯一写入口（静态面）**——T3/T4 只覆盖"直接内容写盘"形式，**漏报** `os.fdopen`（`core/kv.py:65` 实例，非会话日志域）· `sqlite3`（FTS 派生投影）· `shutil` · `os.replace`（**当前无违规**）· **⑦`derive` ≡ 逐事件重放**（仅间接覆盖）
- ⚪ deferred：跨进程并发 append-only 编号化 · 扫描器覆盖增强（**只分析不增强**）

**INV-02**（covered）
- 定义面（`llm.chat` 唯一合法调用方 = agent-loop）已由 **5 条用例**（3 静态 + 2 行为）覆盖，并经 mutation 反证。
- **三闸面**（系统工具出口是否受 budget/cancel 约束）**不在 INV-02 定义面内** —— 已明确划归 **KF-B**，**不混入 INV-02 计分**。

**INV-03**（8 / 0 / 0 / 3）
- ✅ covered：全量↔增量一致 · 二次 rebuild 无重复 · **编辑重放（双侧）** · cache invalidation · 重建 ≡ 重放结果 · **无副作用** · **确定性**（附 scope note）· 空/新/边界 session
- ⚪ deferred：D-1 `replay()` seq 去重（可达性未达成）· D-2 `session_query` rebuild O(n) · D-3 跨进程 rebuild

**INV-04**（12 / 0 / 0 / 2）
- ✅ covered：A1 `tool.result` 前必有同 `call_id` 的 `guard.evaluated` · A2 每次求值恰一条 · A3 经 `call_id` 关联 · A4 缺件 fail-closed · A5 凡执行必经 `tools.execute` · B1 恰三值无 bypass · B2 无 override/bypass/execute 类 API · B3 一次 reject 不可翻回 · B4 waterfall 短路 · B5 无移除/重排、挂载恒链尾 · B6 `disable` 须 `config_ref`、强制 guard 恒在 · **+ F-1 派生子性质（`tool.error` 两类区分）**
- ⚪ deferred：治理层相邻面（`INV-G1`/`INV-G5` 不在本 ID 证据范围）· **FC-8**（5 条误标 INV-03 的编号修正，`pending authorization`）

### 2.2 编号化用例分布（`tests/invariants/test_inv_core.py`）

| 不变量 | 函数数 | 备注 |
|---|---:|---|
| INV-01 | 4 | 物理字节级 append-only + 静态写路径白名单，各带判据自检 |
| INV-02 | 5 | 3 静态（调用面/派发面/类别边界）+ 2 行为（走 `mini` 非 `chat`、幂等） |
| INV-03 | 3 | 编辑后 rebuild · 真源零变化 · 确定性 |
| INV-04 | 7 | 缺件矩阵（参数化 4）· 双闸扫描（2）· `call_id` 配对（2）· 挂链尾 · `tool.error` 两类 |
| **合计** | **19**（29 条执行用例） | — |

---

## 3. KEY-FINDINGS 状态

> 载体：`docs/KEY-FINDINGS.md` **附录 A**（`4f05c95` 起 **+40/−0 纯追加**，既有 Finding 零改动）

| 条目 | 级别 | 状态 | 摘要 |
|---|---|---|---|
| **KF-A** | **P0** | ✅ **`CLOSED`**（`50c19ca`） | `pyharness/core/auto_title.py:36` 直调 `ctx.llm.chat(...)`（经 `engine.py:805-806` 生产接线）**违反 INV-02**。根因：F042 规格指定 `ctx.llm.mini` 而该出口**在实现中缺失** ⇒ 退化到对话出口。**违规点 = 出口选择**（+ 无闸，已划归 KF-B） |
| **KF-B** | **P1** | ⚠️ **`OPEN`** | 系统工具类 LLM 出口（`summarize`/`json_chat`/`mini`）**不经**轮数/取消/预算三闸；且 `summarize(prompt, budget=400)` 的 `budget` **从未被执行**。**独立于 INV-02**，未新建 INV 编号 |

**`KF-A = CLOSED` 的精确边界**：仅表示"错误出口选择"这一违约点已消除（INV-02 GREEN）；**不表示** F042 其余 6 项或 KF-B 已解决。

**本阶段未新增 KEY-FINDING** —— 三次 Final Review 均判定"**未发现真实生产缺陷**"；期间所有 RED 均来自**人为注入的 mutation**（用于验证测试鉴别力）。

---

## 4. Checkpoint 序列（`4f05c95` → HEAD，**ahead 38 / behind 0 / 未 push**）

```
142765e  docs: freeze S6-2 P0 remediation design
50c19ca  fix: restore F042 mini LLM endpoint              ← ★ 本阶段唯一生产代码改动
c5fa851  docs: freeze INV-02 remediation final review
9349596  test: establish INV-01 invariant coverage
c44c9ce  test: establish INV-03 invariant coverage
1a36eef  test: establish INV-04 invariant coverage
b65d102  docs: add S6-2b midpoint review record           ← HEAD
```

| commit | 主题 | 文件 | 变更 | 生产代码 |
|---|---|---:|---|---|
| `142765e` | docs: freeze S6-2 P0 remediation design | 3 | +1195 / −0 | ❌ |
| **`50c19ca`** | **fix: restore F042 mini LLM endpoint** | 6 | **+686 / −3** | ✅ `auto_title.py`(+2/−3) · `llm.py`(+13/−0) |
| `c5fa851` | docs: freeze INV-02 remediation final review | 2 | +248 / −2 | ❌ |
| `9349596` | test: establish INV-01 invariant coverage | 3 | +603 / −0 | ❌ |
| `c44c9ce` | test: establish INV-03 invariant coverage | 4 | +792 / −0 | ❌ |
| `1a36eef` | test: establish INV-04 invariant coverage | 4 | +1034 / −7 | ❌ |
| `b65d102` | docs: add S6-2b midpoint review record | 1 | +183 / −0 | ❌ |

**KF-A 修复的内容（`50c19ca`）**：新增 `LLMClient.mini(prompt, *, ctx) -> str`（`llm.py:974-985`，`_chat_any` 薄包装 ⇒ **超时/计量/事件/降级全复用既有链路、零新增真源**）+ `auto_title` 改走 `mini`（**改 3 行**）+ `docs/specs/llm.py.md` 补 17 行接口说明。**类别式边界**（非例外名单）：`chat`/`chat_stream` = Agent Loop 对话出口；`mini`/`summarize`/`json_chat` = System Tool LLM 出口。

---

## 5. 冻结面说明

**以下面在整个 S6-2b 期间（`4f05c95` → HEAD）逐项 0 改动**（独立核验）：

| 面 | 文件/路径 | 改动 |
|---|---|---:|
| **Canonical Registry** | `docs/INVARIANT_REGISTRY.md` | **0** |
| **Governance Core v1.0** | `pyharness/governance` | **0** |
| **Event Schema** | `docs/EVENT-SCHEMA.md` + `pyharness/events` | **0** |
| **L-3（trust path）** | `pyharness/core/approval.py` | **0**（**未处理**） |
| **生产代码** | `pyharness/**` | 仅 `50c19ca` 的 **2 文件**（KF-A 修复本体，**已授权**） |

**运行时冻结值**：`EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3`（全程不变）。

**纪律核验（逐阶段留证）**：
- 三次 mutation 轮次（INV-01 / INV-03 / INV-04）**全部执行 → 验证 RED → 立即还原**；`grep -c MUTATION` 逐文件 = **0**，临时文件与 `tmp/*.bak` **均已清理**；
- 测试文件**只增不改**：`9349596`/`c44c9ce`/`1a36eef` 三次改动的**删除行全部为模块 docstring**，`def`/`assert` 删除数 = **0**；
- **未处理**：KF-B · FC-8（`test_tools_guard.py` 5 条误标 INV-03）· 全部 residual limitations。

---

## 6. Residual Limitations

> 汇总自三份 Final Review 与 Midpoint Review；**均未被本阶段处理**。

### 🟠 P1（1 项）

| # | 限制 | 状态 |
|---|---|---|
| **P1-1** | **KF-B**：系统工具类 LLM 出口不经三闸；`summarize.budget` 未实现 | **`OPEN`** |

### 🟡 P2（3 项）

| # | 限制 | 状态 |
|---|---|---|
| **P2-1** | **F042 Behavior Compliance 6 项**（`first[:200]→[:500]` · `≤24→TITLE_MAX=64` · failure fallback · empty fallback · `test_f042_title.py` · `auto_title` 模块 spec） | **DEFERRED** |
| **P2-2** | **L-3 trust path**（`_trust_hit` 命中即 `granted`，不落事件、不产 Receipt；`_enabled` 默认 `False` ⇒ 默认不可达） | **未处理**（S6 只验证不修复） |
| **P2-3** | **INV-01 ⑥ 唯一写入口（静态面）= `partial`**：扫描器漏报 `os.fdopen`/`sqlite3`/`shutil`/`os.replace` | **登记**（**当前经独立枚举确认无违规**） |

### 🟢 P3（9 项）

| # | 限制 |
|---|---|
| **P3-1** | INV-01 ⑦ `derive` ≡ 逐事件重放无**编号化**直接断言 |
| **P3-2** | `test_inv02_loop_is_the_only_dynamic_entry_dispatcher` **硬编码 `agent_loop.py:241` 行号**（该行之前的增删会**误报 RED**） |
| **P3-3** | `mini` 为新增公开 API，全仓**无 LLM 门面 Protocol**（鸭子类型） |
| **P3-4** | INV-01 ⑧ 跨进程 append-only 无编号化断言 |
| **P3-5** | **D-1** `replay()` 无 seq 去重（与写路径 `_resolve_by_seq` 不对称；**可达性未达成**） |
| **P3-6** | **D-2** `session_query` rebuild 的 O(n) 性能面 |
| **P3-7** | **D-3** 跨进程 rebuild（= INV-03 确定性 scope note 的对应项） |
| **P3-8** | **FC-1~FC-14**（19 处 / 13 文件）编号迁移 `pending authorization`；其中 **FC-8/FC-13** 涉及 `test_tools_guard.py` 的误标 ⇒ 使**按编号读出的覆盖读数失真** |
| **P3-9** | `tests/invariants/test_inv_{bus,config,events}.py` 头部"**当前 RED：模块未实现**"**失实**（= FC-11） |

**⚠️ 本阶段新增登记的残余（S6-2b-3 Final Review R-1）**：Provider 双闸扫描的**私有索引漏报面** —— `registry._providers[name]` 直取**既非** `lookup_provider` **亦非** `.handle(` ⇒ 双闸不覆盖；**已独立 grep 确认当前无任何调用点**；建议闸③（扫描 `._providers` 外部访问）**本轮未实施**。

**⇒ 无 P0 残余** —— 唯一曾经的 P0（KF-A / INV-02 违约）已修复并 CLOSED。

---

## 7. 下一阶段入口

| 项 | 内容 |
|---|---|
| **下一步** | **S6-2b-4 / INV-05**（`guard 拒绝 = 零副作用`）—— **未开始，等人工授权** |
| **入口条件** | ✅ **已满足** —— worktree clean · 全量 **1693 / 2 / 0** · 冻结面逐项 0 · 无 P0 残余 |
| **建议沿用本阶段已验证的体例** | ① **真实装配优先**：`_wired_log`（真实 `SessionLog`+`EventBus`+真实 `SessionStore` 文件真源）· INV-04 的 `_inv04_ctx/_inv04_gov`（可逐件置 `None` 的 ctx + 真实 `GovernanceContext`）<br>② **静态扫描三件套**：AST（非 grep）+ **文件级白名单逐条写明理由** + **双向校验**（未登记 / 过期）+ `SyntaxError` **响亮失败**<br>③ **判据自检**：每个静态/字节级判据配一条"该判据真能识别违约"的自检用例<br>④ **mutation 纪律**：每条关键性质配一个 1 行级专属 mutation，**执行 → 验 RED → 立即还原 → 核验无残留** |
| **INV-05 的 Registry 已知缺口（待审计确认）** | 无 `tests/invariants/` 编号化用例；覆盖分散于 guard / executor / tool_fs / cli 四处、**无单一编号锚点** |
| **注意** | INV-05 的语义（`拒绝后零副作用`）与 **KF-B** 的控制面议题相邻但**不同**：前者是"拒绝后 Provider 零调用"，后者是"系统工具出口不受三闸" —— **不得混为一谈**，也**不得把 KF-B 的工作夹带入 INV-05** |

### 7.1 本阶段产出物清单（用于交接）

| 文件 | 性质 |
|---|---|
| `tests/invariants/test_inv_core.py` | **测试资产**（19 函数 / 29 用例，覆盖 INV-01~04） |
| `S6-2b-1_CHANGE_REPORT.md` · `S6-2b-1_FINAL_REVIEW.md` | INV-01 阶段报告与审查 |
| `S6-2b-2_COVERAGE_AUDIT.md` · `S6-2b-2_CHANGE_REPORT.md` · `S6-2b-2_FINAL_REVIEW.md` | INV-03 审计 / 实施 / 审查 |
| `S6-2b-3_COVERAGE_AUDIT.md` · `S6-2b-3_CHANGE_REPORT.md` · `S6-2b-3_FINAL_REVIEW.md` | INV-04 审计 / 实施 / 审查 |
| `S6-2b_MIDPOINT_REVIEW.md` | INV-04 前的阶段性冻结检查 |
| `S6-2_COVERAGE_MATRIX.md` · `S6-2a-P0_{CHANGE_REPORT}` · `S6-2a-P0-F_{PLAN,CHANGE_REPORT,FINAL_REVIEW}` | INV-02（KF-A）裁决 / 修复 / 审查 链 |
| `docs/KEY-FINDINGS.md`（附录 A） | KF-A / KF-B 正式登记 |
| `docs/specs/llm.py.md`（`mini` 小节） | 契约文档最小补充 |

---

## 8. 边界声明

**本文件性质**：**只读整理**。
**未做**：❌ 修改生产代码 · ❌ 修改测试 · ❌ 修改 Registry / Event Schema / Governance Core · ❌ 处理 KF-B / FC-8 / residual limitations · ❌ 进入 INV-05 · ❌ commit · ❌ push。

**worktree**：`?? S6-2b_FINAL_SUMMARY.md`（仅本文件）；**HEAD 仍 `b65d102`**，ahead 38。

---

**S6-2b Final Summary 结束。未自动进入 INV-05，等待授权。**
