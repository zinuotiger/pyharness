# F27_CHANGE_REPORT

> **性质**：F-27 实施变更报告（C1 + C2）。
> **上游**：`docs/decisions/ADR-021-audit-session-ownership.md`（**Accepted**）· `F27_IMPLEMENT_PLAN.md`
> **基线**：`HEAD = d2ccd45` · 全量 **1732 collected / 1730 passed / 0 failed / 2 skipped** · 覆盖 79%
> **终值**：全量 **1741 collected / 1739 passed / 0 failed / 0 errors / 2 skipped**（**+9**）· 覆盖 **79%**
> **状态**：`DONE`（GATE-04 通过）· **未 commit、未 push**

---

## 1. 修改文件

### C1 —— 文档（2 文件）

| 文件 | +/− | 内容 |
|---|---|---|
| `docs/decisions/ADR-021-audit-session-ownership.md` | 新文件（本批前已落盘） | `## Status`：**Proposed → Accepted**；新增**裁定记录表（A-1~A-6）**与**实施约束 6 条**；A-6 结果块（登记 + 暂不迁移 019/020 + "ADR storage convention 需未来单独 ADR"） |
| `docs/ADD.md` | **+5 / −3** | 顶部条数 **20→21**；§2 索引追加 ADR-021 行；§2.1 标题 `013~019`→`013~021` + 新增路径行（链接 `decisions/ADR-021-…`，并注明"首个落在 `docs/decisions/` 的 ADR，迁移待另立 ADR"） |

### C2 —— 生产代码（2 文件）

| 文件 | +/− | 内容 |
|---|---|---|
| `pyharness/core/tools_guard.py` | **+50 / −20** | 5 个方法签名加 **keyword-only** `session=None`（`_evaluate_full` / `evaluate` / `evaluate_detailed` / `_audit` / `_append_rejected` / `_append` —— 共 6 处）；**2 处统一解析**（`_evaluate_full` 与 `_append`）；**5 处内部调用**传 `session=sess`；方法级 docstring 补归属说明 |
| `pyharness/governance/context.py` | **+9 / −1** | `authorize()` 内 `evaluate_detailed(...)` 传 **`session=getattr(ctx, "session", None)`** + 6 行说明注释 |

### C2 —— 测试（2 文件）

| 文件 | +/− | 新增用例 |
|---|---|---|
| `tests/invariants/test_inv_core.py` | **+184 / −0** | **T-1 / T-2 / T-3 / T-4 / T-5 / T-6 / T-8**（7 例）+ 2 个本组私有助手 |
| `tests/unit/test_tools_guard.py` | **+52 / −0** | **T-7**（2 例：`session=` kwarg 语义；拒绝路径跟随） |

**合计：6 文件，+300 / −24。**

---

## 2. Diff 摘要（关键片段）

### 2.1 生产：唯一的解析点（`_append`）与装配层一跳

```python
# tools_guard.py::GuardChain._append —— **唯一解析点**
sess = session if session is not None else self._session   # ← 新增语义

# governance/context.py::authorize —— 装配层一跳
evaluation = await chain.evaluate_detailed(
    call, scope, session=getattr(ctx, "session", None))    # ← ADR-021
```

### 2.2 事件汇点：`guard.evaluated` / `guard.rejected` 同源

```python
await self._audit(call, d, g.id, policy, session=sess)
if d is Decision.REJECT:
    await self._append_rejected(call, g.id, policy, session=sess)   # 同 session
```

### 2.3 **明确未改**（约束遵守）

`GuardChain._session`（装配期默认来源，`:665`）· **`_record`（`guard.disabled`）** —— policy 级事件仍走装配期会话（由 T-6 钉住）· 模块级 docstring —— **刻意不改**，以免把 INV registry 的 `:17/:44/:622` 三处锚也拖入漂移（见 §6 R-a）。

### 2.4 与计划的**两处实现偏离**（均如实登记）

| # | 计划 | 实际 | 原因 |
|---|---|---|---|
| **I-1** | `session=ctx.session` | **`session=getattr(ctx, "session", None)`** | 首轮冒烟时 `test_governance_authorize.py` **4 例 RED**（`AttributeError: 'SimpleNamespace' object has no attribute 'session'`）。判定为 **CORE-03 的"实现不符模块自身约定"**：`authorize` 对本方法其余 ctx 属性一律 `getattr(ctx, "scope", None)`，`_emit_decision_issued` 亦用 `getattr(ctx, "session", None)`。**修实现，未改测试**；且对"未装配 `ctx.session` 的调用方"保持既有语义（回落装配期会话） |
| **I-2** | 新增 **1** 个单元用例（T-7） | 新增 **2** 个（kwarg 语义 + 拒绝路径） | 拒绝路径在单元层需要一个**不与 T-4 重复**的入口；两例合计 52 行，仍属最小 |
| **I-3** | 计划 T-1~T-7（7 例） | 实交付 **9 例**（+T-8） | 实现中发现**真实覆盖缺口**：T-1~T-7 **全部直接驱动链**，而 `authorize()` 那一跳（`session=ctx.session`）**无任何用例覆盖** ⇒ M-B 变异不会被抓到。补 **T-8** 封该跳（M-B 现由其专属捕获，见 §4） |

---

## 3. 测试结果

| 口径 | 结果 |
|---|---|
| **定向**（invariants + tools_guard + tools_executor + governance_authorize + engine + engine_flow + orchestration + subagent） | **295 passed** |
| **全量回归** | **1741 collected / 1739 passed / 0 failed / 0 errors / 2 skipped** |
| 基线 | 1732 / 1730 / 2 |
| **净变化** | **+9**（= 新增用例数：T-1~T-6,T-8 = 7 + T-7 = 2）⇒ **零回归** |
| 覆盖率 | **79%**（17,920 语句 / 3,833 missed） |
| `EVENT_TYPES / SYNC_TYPES / TRANSIENT / payload` | **77 / 14 / 3 / 75 —— 全不变**（验收#4） |

### 3.1 首轮失败与判定（CORE-03 留痕）

| 失败 | 判定 | 处置 |
|---|---|---|
| `test_governance_authorize.py` **4 例** `AttributeError: … no attribute 'session'` | **实现缺陷**（违反本模块 `getattr` 防御式约定），**非**测试锁定有意行为 | **改实现**（见 §2.4 I-1）→ 4 例复绿 |
| `test_adr021_policy_level_event_is_not_call_scoped`（T-6） | **测试缺陷**：`_record` 为 fire-and-forget（`create_task`），未等调度 | 加 `_wait(...)`；另注 `guard.disabled` 不在词表（真实日志 EVT-102），故用记录型替身考察"该路径指向哪个会话" |
| `test_inv04_default_session_stays_on_assembly_session`（T-2） | **测试缺陷**：断言子串写错（实际格式为 `NO-GUARD-EVENT:call_id=<id>`） | 修正断言串 |

---

## 4. Mutation Test 结果

驱动器：`tmp/f27_mutate.py`（逐项：施加 → 跑用例 → **无条件还原**）。还原目标 = `tmp/f27_post/`（**改动后**快照）。

| # | 变异 | RED | 主路径 `engine_flow` | 结论 |
|---|---|---|---|---|
| **M-A** | 撤掉 `_audit`/`_append_rejected` 的 `session` 透传 | **7 例** | **GREEN** | 唯一鉴别点 = 会话解析；主路径不受影响 |
| **M-B** | `context.py` 删掉 `session=…`（装配层一跳缺失） | **仅 T-8** | **GREEN** | **T-8 的必要性被证明**（其余 8 例均抓不到） |
| **M-D** | `_append` 忽略入参 `session`（汇点层不可绕过） | **7 例** | **GREEN** | 解析不可被绕过 |
| **M-E** ⭐ | **只迁 `guard.evaluated`,`guard.rejected` 留父（半修）** | **仅 2 例拒绝路径**（T-4 / T-7b） | **GREEN** | **A-2 的必要性被测试强制** |
| **M-F** ⭐ | **双写**父子两会话（看似稳妥的错误修法） | **5 例（含 T-3 父日志去噪）** | **GREEN** | "父日志去噪"有**独立判据**，不被双写蒙混 |
| **M-G** | `_record`（`guard.disabled`）丢落点 | **仅 T-6** | **GREEN** | 封"过度修改"（策略级事件不随调用迁移） |

**共同结论**：**6/6 变异全部 RED，且主路径在每一个变异下均 GREEN** —— 既证明用例有鉴别力，又证明零回归性质成立于机制层。

**还原核验**：还原后复跑 **无 RED**；`grep -rn "MUTATION" pyharness/` = **0**；两文件与 `tmp/f27_post/` **逐字节相同**。

### 4.1 ⚠️ M-C（另建一条链）**未作为 diff 变异执行** —— 如实登记

计划中的 **M-C**（在子路径另建 `GuardChain`）**无法在本 diff 内机械注入**（它要改的是 `orchestration.py`，不在本阶段改动面）。该方向的封锁由**既有断言**承担：

```
tests/unit/test_engine.py::test_s23_tb_single_chain_and_single_governance
    assert spine.guard is spine.governance.policy.chain      # 只一条链
```

该用例全程 **GREEN**（定向与全量均通过）。**故 M-C 报为"不适用（非本 diff 可注入）"，而非"已通过"。**

---

## 5. 回归结果

| 项 | 值 |
|---|---|
| 全量 | **1741 collected / 1739 passed / 0 failed / 0 errors / 2 skipped** |
| 覆盖率 | 79%（未降） |
| schema | `77 / 14 / 3 / 75` 不变 |
| 单链不变式 | `test_s23_tb_single_chain_and_single_governance` **PASSED** |
| 白名单文件 | `governance/audit.py` · `docs/INVARIANT_REGISTRY.md` · `tools_executor.py` —— **均未改动** |
| 协议 / 冻结记录 | 未改动 |

> 说明：`git status` 中 `pyharness/core/orchestration.py` 与 `pyharness/engine.py` 的改动来自**前几轮**（RT-GOV-01 与 Stage 1 F-01），**本阶段未触碰**（已 grep 确认二者无 `ADR-021`/`F-27` 标记）。

---

## 6. 已知残留

| ID | 残留 | 级别 | 说明 |
|---|---|---|---|
| **R-a** | **INV registry 行号证据锚漂移** | P3 | `docs/INVARIANT_REGISTRY.md` 的 INV-04 `Evidence Source` 以**行号**引用 `tools_guard.py :17/:44/:622/:688/:804/:808/:815`。本阶段插入 ~30 行 ⇒ **`:688 / :804 / :808 / :815` 漂移**（约 **+2 ~ +9 行**）；`:17/:44/:622` **不漂移**（未插入其之前 —— 模块级 docstring 被**刻意跳过**，见 §2.3）。**按 B-2 裁定：本阶段不改 registry，仅记录**；**符号锚迁移另立任务**。先例：`test_inv02_loop_…` 硬编码 `agent_loop.py:241` 曾被评审判为"该行之前的增删会误报 RED"（同类缺陷） |
| **R-b** | `evaluate()` 的 docstring 已由"签名逐行不变"改为"**位置参数/返回类型/求值语义不变；仅新增可选 kwarg**" | — | 已随 F1 处理（§2.4 相关的表述修正） |
| **R-c** | `_record` 的 docstring 仍写"词表 57 锁定"（**实为 77**） | P3 | **既存**陈旧注释（属 F-24 计数漂移族），**非本阶段引入、未修** |
| **R-d** | `guard.disabled` **不在词表** ⇒ 真实 `SessionLog` 写 EVT-102 拒写，仅本地日志留痕（T-6 用记录型替身考察代码路径。） | P3 | 既存；ADR-020 已裁定"`guard.disabled` 不新增、并入 `policy.updated op=disable`" |
| **R-e** | F-28 第二级（`validation` 复合键） | P1 | 与 ADR-021 同属"跨会话归属"议题，建议合并一次设计评审（`RT-FIX-PLAN-v1.md` §3） |
| **R-f** | `docs/decisions/` 为**新建约定**，ADR 现散落两处（仓库根 + `docs/decisions/`） | — | **A-6 已裁定**：暂不迁移、待另立"ADR storage convention" ADR |

---

## 7. 回滚说明

### 快照

| 快照 | 路径 | 用途 |
|---|---|---|
| **R0**（**改动前**） | `tmp/f27_bak/`（6 文件：2 源 + 2 测试 + ADR-021 + ADD.md） | 整体中止 |
| **R1**（**改动后**） | `tmp/f27_post/`（2 源文件） | 变异测试还原目标 · 局部回退基准 |

> `tmp/` 已被 `.gitignore` 覆盖 ⇒ 不入库。

### 三级回退

| 级 | 场景 | 手段 |
|---|---|---|
| **R-1**（最常用） | 未提交 | 从 `tmp/f27_bak/` **逐文件**复制回原位（2 源 + 2 测试 + 2 文档） |
| **R-2** | 已提交 | **逆序** `git revert <C2> <C1>`（保留历史） |
| **R-3** | ⚠️ 需丢弃全部 F-27 | `git reset --hard <C1 之前>` —— **本仓库工作树另有未提交改动**（F1 三件 / RT-GOV-01 两件 / Stage 1 六件），`--hard` 会**一并丢弃** ⇒ **仅确认无其他未提交价值时使用**，否则一律走 R-1 |

### 提交粒度（计划既定）

| Commit | 内容 | 文件 |
|---|---|---|
| **C1** | `docs:` ADR-021 Status→Accepted + `docs/ADD.md` 登记 | 2 |
| **C2** | `fix:` guard 事件会话归属 + 测试 | 4 |

**提交纪律**：**显式列出文件**（**禁止 `git add -A`** —— 工作树含 F1 阶段既存改动）；提交信息**不署 AI 名**。

---

## 8. 实施过程中的一次操作事故（如实登记）

**事故**：首版变异驱动器 `tmp/f27_mutate.py` 把 `_restore()` 指向了 **R0（改动前）快照**，而变异测试的还原目标应为**改动后**状态。第一项变异（M-A）跑完后 `_restore()` **把 Step 1 的代码改动覆盖回退**，随后 M-B 因锚点消失而断言失败。

**影响**：`pyharness/core/tools_guard.py` 与 `pyharness/governance/context.py` **一度回到改动前状态**（未提交，无外部影响；**未发生行尾污染** —— 两文件本就 CRLF，`git diff` 已核验）。

**恢复**：以可复现脚本 `tmp/f27_apply.py` **重放 9 处编辑**（幂等、带锚点断言），AST 校验 + 冒烟 **239 passed**，再建立 `tmp/f27_post/` 快照并把驱动器还原目标改为之后重跑。

**教训（值得写入项目经验）**：**变异测试的"还原目标"必须是"改动后"状态，而非"改动前"快照** —— 二者在语义上相反；用一个 `tmp/*_post/` 快照做还原、把 `tmp/*_bak/` 仅留给"整体中止"，可避免此类事故。

---

## 9. 验收判据对照

| # | 判据 | 结果 |
|---|---|---|
| 1 | 计划内改动全部落地 | **是**（C1 2 文件 + C2 4 文件） |
| 2 | 定向 + 全量 GREEN | **是**（295 / 1739 passed） |
| 3 | Mutation 全 RED 且专属捕获 | **是**（M-A/M-B/M-D/M-E/M-F/M-G = 6/6；M-C 不适用，已登记） |
| 4 | schema 不变 | **是**（77/14/3/75） |
| 5 | `git diff` 仅含计划文件 | **是**（6 文件；白名单全 0） |
| 6 | 单链不变式成立 | **是**（`test_s23_tb_single_chain_and_single_governance` PASSED） |
| 7 | 用户 6 条约束 | **全部遵守**（未改 INV-04 定义 · 未改 INVARIANT_REGISTRY · 无 cross-session reconcile · 无 lineage resolver · 无新 audit session · 单链保持） |

---

## 10. 剩余工作

1. **提交 C1 / C2**（等你指令；提交时显式列文件、不署 AI 名）。
2. **R-a 符号锚迁移**（另立任务）。
3. **F-28 第二级**（复合键）—— 建议与 ADR-021 的"跨会话归属"合并设计。
4. **ADR storage convention ADR**（A-6 已记录待办）。
