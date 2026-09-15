# S6-2b-3_FINAL_REVIEW.md — INV-04 覆盖最终审查

> **阶段**：S6-2b-3 Final Review（**只读独立核验**）
> **审查对象**：S6-2b-3 Phase F 产出（`tests/invariants/test_inv_core.py` 的 INV-04 段 +349/−7 · `S6-2b-3_COVERAGE_AUDIT.md` · `S6-2b-3_CHANGE_REPORT.md`）
> **日期**：2026-09-15 ｜ **基线 commit**：`c44c9ce` ｜ HEAD `c44c9ce` ｜ ahead 36
> **性质**：**只读审查**。**未新增测试 · 未修改生产代码 / Registry / Event Schema / Governance Core**；**未重跑生产代码 mutation**；**未 commit、未 push**（Freeze 提交在判定后执行）。
> **核验方式**：**独立复算** —— 重跑定向与全量、独立 AST/探针复验 F-1 两类行为、独立读 diff 与白名单、独立 enumerate Provider 访问面；**不引用**变更报告的自述。

---

## 1. T-1~T-5 是否真实覆盖 INV-04 子性质（**独立复核**）

> 独立清点：`tests/invariants/test_inv_core.py` 内 INV-04 段共 **7 个测试函数 / 10 条执行用例**（T-1 参数化 ×4）。

| 测试 | 行 | 映射子性质 | 关键断言（独立读源码确认） | 破坏时的证明 |
|---|---|---|---|---|
| `test_inv04_require_wiring_fail_closed_all_parts` | 600 | **A4** | 四件逐项置 `None` ⇒ `pytest.raises(PyHError)` 且 `code == "CYC-999"` + `prov.calls == 0` + `not sess.of("tool.result")` | ✅ **M-A4 实证**（删 `session` 分支 ⇒ RED） |
| `test_inv04_provider_acquisition_surface_is_executor_only` | 680 | **A5**（获取闸） | `lookup_provider` 调用点 ⊆ `{tools_executor.py}` **且归属 `_provider_handle`**（正向控制） | ✅ **M-A5 实证** |
| `test_inv04_provider_handle_call_surface_is_allowlisted` | 690 | **A5**（调用闸） | `.handle(` 调用点 ⊆ 白名单（`tool_skill.py` 附理由） | ✅ **M-A5 实证** |
| `test_inv04_guard_evaluated_and_tool_result_share_call_id` | 699 | **A1 · A3** | **逐字段** `ge[0]["trace"]["call_id"] == tr[0]["payload"]["call_id"] == "c-pair"` | ✅ **M-A3 实证** |
| `test_inv04_reject_path_call_id_pairing` | 725 | **A3**（拒绝路径） | `evaluated`/`rejected`/`tool.call` 三处 `call_id` 集合 == `{"c-rej"}` + 无 `tool.result` + `prov.calls == 0` | ✅ **M-A3 实证**（双双 RED） |
| `test_inv04_plugin_guard_appends_to_tail_only` | 757 | **B5** | `after[:len(before)] == before` + `after[-1] == "g-plugin-inv04"` + `chain_version() == v0+1` | ✅ **M-B3 实证**（且有别于既有测试：既有全绿） |
| `test_inv04_tool_error_two_classes` | 785 | **A1 · A4 + F-1 派生子性质** | ①两类执行前失败 ⇒ `sess.types() == ["tool.error"]`（**恰一条**）+ 无 `guard.evaluated` + `prov.calls == 0`；②执行后失败 ⇒ `ge` 恰 1 且 `types_.index("guard.evaluated") < types_.index("tool.error")` + `call_id` 一致 + `prov.calls == 1` | ✅ **M-A6 实证** |

**独立结论**：
- **断言非空转** —— 每条都有"M-* 实证 RED"支撑（§3）；
- **未越出子性质范围** —— 断言全部落在 A1/A3/A4/A5/B5 与 F-1 派生项；**未**重复 A2（恰一条 evaluated）与 B1~B4/B6 的既有断言；
- **可诊断性** —— 失败消息均点名子性质（如 `"INV-04 违约:guard.evaluated 与 tool.result 的 call_id 不一致(配对断裂)"`），且 T-1 的前置/失败措辞可与"环境问题"区分。

---

## 2. F-1 `tool.error` 两类口径是否与实现一致（**独立探针复验**）

用**只读探针**（复用既有测试夹具，未创建任何文件）在**当前实现**上复跑三类场景：

| 场景 | 事件序 | Provider 调用 | `evaluated` 先于 `error` | `call_id` 一致 |
|---|---|---|---|---|
| ①执行前失败 `TLB-802`（unknown tool） | `['tool.error']` | **0** | —（无 evaluated） | — |
| ①执行前失败 `TLB-803`（invalid args） | `['tool.error']` | **0** | —（无 evaluated） | — |
| ②执行后失败（Provider 抛错） | `['tool.call','guard.evaluated','decision.issued','tool.error']` | **1** | **True** | **True** |

**⇒ 与裁定的两类口径逐条吻合**，且与 T-5 的三组断言**逐字对应**：

| 裁定要求 | 实现实测 | T-5 断言 |
|---|---|---|
| `tool.result` 必须有同 `call_id` 的 `guard.evaluated` | T-3 场景实测成立 | 由 T-3a 断言 |
| 执行前失败：**不要求** evaluated + **必须证明 Provider 未调用** | ①两例均 `calls == 0` 且无 evaluated | `assert not sess.of("guard.evaluated")` + `assert prov.calls == 0` |
| 执行后失败：**必须**有 evaluated 且**先于** error + `call_id` 一致 | ②实测成立 | `assert types_.index(...) < types_.index(...)` + `call_id` 相等 |

**独立结论**：F-1 口径**与实现一致**，且已被**可执行断言钉死**。**未修改 Registry**（依裁定"只更新设计理解"）。

---

## 3. Mutation 结果是否可复核（**独立检查，本轮不重跑**）

| Mutation | 记录结果 | 独立复核 | 可复核性评估 |
|---|---|---|---|
| **M-A3** | T-3a/T-3b **双 RED** | 机制必然：抹掉 `trace.call_id` ⇒ `(ge[0]["trace"] or {}).get("call_id")` 得 `None` ⇒ 相等断言失败 | ✅ **可复核**（断言直接读该字段） |
| **M-A4** | T-1[session] **RED**，失败模式由 `CYC-999` 变 `AttributeError` | 机制必然：分支删除后 `_require_wiring` 不再抛，落到 `_append` 用 `None.append` | ✅ **可复核** |
| **M-A5** | **两道闸均 RED**，报 `core/_mut_bypass_inv04.py:[5]` | 机制必然：新增文件的 `lookup_provider` 与 `.handle(` 均不在白名单 | ✅ **可复核** |
| **M-B3** | T-4 **RED** 且**既有 `test_tools_guard.py` 全绿** | 机制必然：`insert(0,…)` 破坏前缀恒等；既有用例只断言"不可翻回"，不查位置 | ✅ **可复核**（该"既有全绿"正是 T-4 填补缺口的证据） |
| **M-A6** | T-5 **RED** | 机制必然：①b 路径多出一条 evaluated ⇒ `assert not sess.of("guard.evaluated")` 失败 | ✅ **可复核** |
| **M-A7** | **未执行**（人工裁定：跨函数生命周期移动，收益低于风险） | — | 记录理由 ✅ |

**残留核验（本轮实际执行）**：

| 检查 | 结果 |
|---|---|
| `grep -c "MUTATION" pyharness/core/tools_guard.py pyharness/core/tools_executor.py` | **0 / 0** ✅ |
| `git diff --stat HEAD -- pyharness` | **空** ✅ |
| 临时文件（`core/_mut_bypass_inv04.py` · `tmp/*.bak`） | **均已删除**（`ls` 确认不存在）✅ |
| 复跑 | `tests/invariants/test_inv_core.py` = **22 passed** · `tests/invariants` = **39 passed** ✅ |

**本轮未重跑 mutation**（重跑需临时改生产代码，违"不修改生产代码"边界）；上表以**机制必然性**替代重跑论证。

---

## 4. Provider 双闸扫描的漏报风险（**独立分析**）

### 4.1 双闸的覆盖面（已实现）

| 闸 | 断言 | 覆盖的绕过形态 |
|---|---|---|
| **闸①** `lookup_provider` | 调用点 ⊆ `{tools_executor.py}` 且归属 `_provider_handle` | 从 **公共 API** 取回 Provider 的任何旁路 |
| **闸②** `.handle(` | 调用点 ⊆ `{tool_skill.py}`（附理由） | 对 **Provider 对象**做属性式直调 |
| **辅助** | **双向校验**（未登记 + 过期）+ `SyntaxError` **响亮失败** | 漏登记、白名单过期、扫描不完整 |
| **正向控制** | 闸① 额外断言获取点仍存在且归属 `_provider_handle` | "删掉调用即变绿"的假绿 |

### 4.2 ⚠️ 漏报面（**已独立枚举，当前无实际暴露**）

| # | 漏报形态 | 是否被双闸覆盖 | 当前事实 |
|---|---|---|---|
| **N-1** | **私有索引直取**：`registry._providers[name]`（`ToolRegistry._providers` 是普通 dict，`tools_registry.py:371`） | ❌ **不覆盖** —— 既非 `lookup_provider` 亦非 `.handle(` | **已独立 grep 全包：无任何模块访问 `ToolRegistry._providers`**（命中项均为 `all_approval_providers` / `ask_providers` 等**审批通道**，与本注册表无关） |
| **N-2** | **裸可调用调用**（如 executor 的 `handle(args, ctx)`） | ⚠️ **不易由属性名捕获** | **但获取该可调用面必经闸①**（`_provider_handle` → `lookup_provider`）⇒ **组合覆盖成立**；**除非**经 N-1 路径取得 |
| **N-3** | 扫描范围仅 `pyharness/**` | ❌ 不覆盖 `scripts/**` | **已在文件 docstring 显式声明**：`scripts/` 为人工探针/e2e，不参与装配、不被 `pyharness` import ⇒ **不在运行时边界内** |
| **N-4** | 白名单**文件级**（不锁行号） | ✔️ 有意为之 | 避免 S6-2a 审查 R-1 那类"上方增删即误报"的脆弱性 |

**独立结论**：双闸对**公共 API 路径**是完备的；对**私有属性直取（N-1）**存在**未覆盖面** —— 属**静态扫描的固有边界**（要完全闭合需数据流分析或封装改造）。
**当前无实际暴露**（N-1 无任何调用点）。
**建议（本轮不实施）**：闸③ = 扫描 `._providers` 的外部访问；或将 `_providers` 改为不对外暴露的封装。**登记为 residual limitation**。

---

## 5. `test_inv_core.py` 是否只有新增 INV-04 内容（**独立核验**）

| 检查 | 结果 |
|---|---|
| **diff hunk 数** | **3 个**：`@@ -1,14 +1,18 @@`（模块 docstring）· `@@ -20,6 +24,7 @@`（`import Optional`）· `@@ -485,3 +490,340 @@`（**INV-04 段追加**） |
| **被删除行** | **7 行，全部为旧模块 docstring**（逐行列出确认） |
| **删除行中是否含 `def` / `assert` / `return`** | **0** ✅ |
| **INV-01 / INV-02 / INV-03 段是否被改** | ❌ **未改** —— 第 3 个 hunk 以 INV-03 段末 3 行为**上下文锚点**，仅在其**之后追加** 337 行 |
| **新增段位置** | 段标记 `# ==== INV-04 · guard 事件与单调拒绝` 位于 **:495**，晚于 INV-03（:361）⇒ 全部新增内容都在 INV-04 段内 |
| **既有测试语义** | ✅ **零改动**（`+349/−7`，删除全为 docstring） |

**独立结论**：✅ 仅新增 INV-04 内容 + 一次模块 docstring 更新（以反映文件现覆盖 INV-01/02/03/04）；**未触碰任何既有测试逻辑**。

---

## 6. 全量回归（**独立复跑**）

| 项 | 独立复算结果 |
|---|---|
| **全量** | ✅ **1695 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1693 passed / 2 skipped / 0 failed**（53.5s，exit 0） |
| **基线** | `c44c9ce`：1685 collected / 1683 passed |
| **差值** | **+10 passed = 新增的 10 条 INV-04 用例** ⇒ **零既有回归** ✅ |
| 定向 `-k inv04` / `tests/invariants` | ✅ 10 / **39 passed** |
| **Event Schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |

---

## 7. 冻结面（**独立核验**）

| 面 | 结果 |
|---|---|
| **`pyharness/**`（生产代码）** | `git diff --stat HEAD -- pyharness` = **空** ✅ |
| **`pyharness/governance`（Governance Core）** | **0 改动** ✅ |
| **Event Schema（`docs/EVENT-SCHEMA.md` + `pyharness/events`）** | **0 改动** ✅ |
| **`docs/INVARIANT_REGISTRY.md`（Registry）** | **0 改动** ✅ |
| **L-3（`pyharness/core/approval.py`）** | **0 改动**（未处理）✅ |
| **KF-B / FC-8** | **均未处理** ✅ |
| **是否进入 INV-05** | ❌ 否 ✅ |
| **worktree** | 仅 `M tests/invariants/test_inv_core.py` + 3 份未跟踪报告 ✅ |
| **branch** | `ahead 36 / behind 0`（**未 push**）✅ |

---

## 8. Residual Limitations

| # | 限制 | 等级 | 说明 |
|---|---|---|---|
| **R-1** | **Provider 双闸的私有索引漏报面（N-1）** | **低** | `registry._providers[name]` 直取不被双闸覆盖；**当前无任何调用点**（已独立 grep 确认）。属静态扫描固有边界 |
| **R-2** | 扫描范围限 `pyharness/**`（N-3） | **低** | 已声明边界；`scripts/` 为不在运行时的人工探针 |
| **R-3** | **M-A7 未执行** | **低** | 人工裁定：跨函数生命周期移动，收益低于风险；T-5② 的鉴别力由 **M-A6 + 实现实测**支撑 |
| **R-4** | **FC-8**（5 条误标 INV-03 未修正，P3-8） | **低** | `pending authorization`；仅影响"按编号读覆盖"，**不影响本轮新增用例** |
| **R-5** | `test_inv02_loop_is_the_only_dynamic_entry_dispatcher` **硬编码 `agent_loop.py:241` 行号** | **低** | **前序阶段遗留**（S6-2a-P0-F 审查 R-1），非本阶段引入；该行之前的增删会**误报 RED**（失败响亮） |
| **R-6** | INV-04 的治理层相邻面（`INV-G1`/`INV-G5`） | **低** | 不在 INV-04 的 Registry 证据范围；`test_inv_governance.py` 已有对应用例 |
| — | **无 P0 / P1 级残余** | — | 上述均为**低**级且已登记 |

---

## 9. Coverage 最终状态

```
INV-04 = 无 guard 事件即非法执行(含单调拒绝 / 无 bypass)
  ├─ covered  : 12   A1·A2·A3·A4·A5 · B1·B2·B3·B4·B5·B6 · + F-1 派生子性质(两类 tool.error)
  ├─ partial  : 0
  ├─ gap      : 0
  └─ deferred : 2    治理层相邻面(INV-G1/G5 不在本 ID 证据范围) · FC-8(pending authorization)
```

**独立确认**：`covered` 的 12 条**逐条**满足"若破坏则对应测试应失败"（§1 的"破坏时的证明"列 + §3 的 mutation 实证）；**未因"测试存在"自动判 covered**（A4/A5 由 M-A4/M-A5 实证；F-1 派生项由 M-A6 实证；A1/A3/B5 的加严由 M-A3/M-B3 实证）。

**与 Registry 的对应**：
- gap ①（`tests/invariants/` 内 0 条）⇒ ✅ **关闭**
- gap ③（无 A5 静态扫描）⇒ ✅ **关闭**（双闸）
- gap ②（5 条误标 INV-03）⇒ **仍开放**（FC-8，本轮明令不处理）
- **Registry 文件本身未修改**

---

## 10. 是否满足 Freeze

| Freeze 前置 | 结果 |
|---|---|
| T-1~T-5 真实覆盖且各有实证 | ✅ |
| F-1 两类口径与实现**独立探针**复验一致 | ✅ |
| mutation 可复核 · 无残留 · 无临时文件 | ✅ |
| 仅新增 INV-04 内容（既有测试零改动） | ✅ |
| 双闸漏报风险**已枚举并登记** | ✅（R-1，当前无暴露） |
| 全量 ≥ 基线且零回归 | ✅ 1693 / 2 / 0 |
| schema 77/14/3 · 生产代码 / Registry / Governance Core / Event Schema 零改动 | ✅ |
| 未处理 KF-B / FC-8 · 未进入 INV-05 | ✅ |

⇒ ✅ **满足 Freeze 条件**。

---

## 11. 最终判定

> # ✅ **S6-2b-3 = PASS**

| 标签 | 值 |
|---|---|
| **S6-2b-3 Phase F** | **PASS** |
| **INV-04** | **`covered` × 12 · `partial` 0 · `gap` 0 · `deferred` 2** |
| **A4 / A5** | **`partial` → `covered`** |
| **F-1** | 口径与实现一致，已钉死为可执行断言 |
| **生产代码 / Registry / Event Schema / Governance Core** | 均 **零改动** |
| **KF-B** | **`OPEN`**（未处理） |
| **INV-05** | **READY**（不自动开始） |

**判定依据**：① 7 函数 / 10 用例**逐条映射**子性质且**各有 M-* 实证**；② F-1 两类口径经**独立探针**复验与实现一致；③ mutation 结果**机制必然可复核**、残留与临时文件**均已清理**；④ 双闸漏报面 **N-1~N-4 已独立枚举**并登记（当前无暴露）；⑤ 测试文件**仅新增 INV-04**（既有逻辑零改动）；⑥ 全量 **1693/2/0**（**+10 零回归**）；⑦ 冻结面**逐项 0 改动**。

---

**S6-2b-3 Final Review 结束。判定 = PASS。等待 Freeze 提交；未 push。**
