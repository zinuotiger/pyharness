# F27_IMPLEMENT_PLAN

> **性质**：实施计划（**只读阶段产物**）。本文件不产生任何代码/测试改动。
> **上游**：`docs/decisions/ADR-021-audit-session-ownership.md`（A-1~A-6 **已裁定**）· F-27 IMPLEMENT DESIGN（B-1~B-4 **已裁定**）
> **基线**：`HEAD = d2ccd45` · 全量 **1732 collected / 1730 passed / 0 failed / 2 skipped** · 覆盖 79% · `EVT/SYNC/TRANSIENT = 77/14/3`
> **状态**：**待二次确认后进入 IMPLEMENT**

## 0. 用户明令的约束（本计划的落地映射）

| # | 约束 | 本计划如何守住 | 验证判据 |
|---|---|---|---|
| **C-α** | 不修改 **INV-04 canonical definition** | §1.4 不改 `docs/INVARIANT_REGISTRY.md`；§8 明列 | 改动文件清单不含该文件 |
| **C-β** | 不引入 **cross-session reconcile** | `AuditSystem` 一字不改；`reconcile` 仍只读 `ctx.session` | §1.4；T-5 |
| **C-γ** | 不修改 **AuditSystem 架构** | `governance/audit.py` **不在改动清单** | §1.4；`git diff` 无该文件 |
| **C-δ** | 不新增 **Audit lineage resolver** | 2-C 被 A-3 显式排除；不新增任何 resolver API | §8 |
| **C-ε** | 保持 **Session 单链不变量** | 不新建链；`spine.guard is governance.policy.chain` 不变 | **T-5** + **M-C** |

---

## 1. 修改文件

### 1.1 生产代码（2 个文件）

| # | 文件 | 改动摘要 | 预估 | 提交归属 |
|---|---|---|---|---|
| **F1** | `pyharness/core/tools_guard.py`（1057 行） | 5 个方法签名加 **keyword-only** `session=None`；2 处统一解析；5 处内部调用传 `session=sess`；docstring 补"事件汇点按调用解析" | **+20 / −6** | C2 |
| **F2** | `pyharness/governance/context.py`（159 行） | `authorize()` 内 `evaluate_detailed(...)` 增 `session=ctx.session` | **+1 / −1** | C2 |

### 1.2 测试（1 必改 + 1 可选）

| # | 文件 | 内容 | 预估 | 提交归属 |
|---|---|---|---|---|
| **F3** | `tests/invariants/test_inv_core.py` | 新增 **T-1 ~ T-6**（INV-04 段） | +90 | C2 |
| **F4** | `tests/unit/test_tools_guard.py` | **+1 单元用例**（`session=` kwarg 语义；用现成 `_gc()` + `FakeSession`）—— **B-1 已接受** | +20 | C2 |

### 1.3 文档（**与 IMPLEMENT 同批**，B-3）

| # | 文件 | 内容 | 时机 |
|---|---|---|---|
| **D1** | `docs/decisions/ADR-021-audit-session-ownership.md` | `## Status`：`Proposed` → **`Accepted`** + 记录 A-1~A-6 裁定 | **Step 0（IMPLEMENT 开始前）**，B-4 |
| **D2** | `docs/ADD.md` | ADR-021 登记：顶部条数 **20→21** + §2 索引追加 1 行 + §2.1 路径表（链接 `decisions/ADR-021-audit-session-ownership.md`） | C1 |

### 1.4 **明确不改（白名单）**

`pyharness/core/orchestration.py` · `pyharness/engine.py` · `pyharness/governance/audit.py` · `pyharness/core/tools_executor.py` · `pyharness/core/session.py` · `pyharness/events/*`（词表/payload）· `pyharness/persistence.py` · `.ai-coding/PROTOCOL.md` · `ARCHITECTURE_DECISION_RECORD.md` · **`docs/INVARIANT_REGISTRY.md`**（B-2 裁定：本阶段不改，仅记录漂移风险）

---

## 2. 修改符号位置（**确切行号，基线 `d2ccd45` 工作树**）

### 2.1 `pyharness/core/tools_guard.py`

| 符号 | 当前行 | 改动 |
|---|---|---|
| `GuardChain._session` 赋值 | **:665** | **不改**（仍为默认值来源） |
| `GuardChain._evaluate_full` | **:672** | 签名加 `*, session=None`；函数体首部插 `sess = session if session is not None else self._session` |
| └ 调用点 `self._audit(...)` scope-hidden | **:699** | 传 `session=sess` |
| └ 调用点 `self._append_rejected(...)` scope-hidden | **:700** | 传 `session=sess` |
| └ 调用点 `self._audit(...)` 非 allow | **:720** | 传 `session=sess` |
| └ 调用点 `self._append_rejected(...)` reject | **:722** | 传 `session=sess` |
| └ 调用点 `self._audit(...)` ALLOW | **:724** | 传 `session=sess` |
| `GuardChain.evaluate` | **:727** | **B-1：加 `*, session=None`** 并透传（**保持**"兼容 wrapper / 逐行不变"的语义承诺，仅新增可选 kwarg） |
| `GuardChain.evaluate_detailed` | **:735** | 签名加 `*, session=None`；`_evaluate_full(call, scope, session=session)` |
| `GuardChain._audit` | **:813** | 签名加 `*, session=None`；`_append(..., session=session)`；docstring 补归属说明 |
| `GuardChain._append_rejected` | **:830** | 签名加 `*, session=None`；`_append(..., session=session)`；docstring 补归属说明 |
| `GuardChain._append` | **:846** | 签名加 `session=None`；`:850` 行改为 `sess = session if session is not None else self._session` |
| `GuardChain._record`（`guard.disabled`） | **:858** | ❗**明确不改** —— policy 级事件属**装配归属**（见 §3.4），由 **T-6** 钉住 |
| 模块 docstring（单调性三层防线段） | **:17 / :44 / :622** 附近 | 补一句"事件汇点**按调用**解析（ADR-021）"；**不动既有 INV-04 断言语义** |

### 2.2 `pyharness/governance/context.py`

| 符号 | 当前行 | 改动 |
|---|---|---|
| `GovernanceContext.authorize` 内的链调用 | **:110** | `await chain.evaluate_detailed(call, scope)` → `await chain.evaluate_detailed(call, scope, session=ctx.session)` |

> `ctx` 在 `authorize` 签名中**已存在**（`authorize(self, call, ctx, *, ...)`）⇒ 无需新增参数、无需新增 import（守 C-5 依赖方向）。

### 2.3 设计要点（防回归）

1. **全部 keyword-only**（`*, session=None`）：既有位置参数调用点**不可能**被破坏。
2. **`None` 语义 = "沿用装配期默认"**：与既有 `from_config(..., path_exists=None, approval_channel=None)` 惯例一致。
3. **解析点仅 2 处**（`_evaluate_full` 与 `_append`）：避免散落解析导致语义分歧。
4. **`_record` 不在解析面**（§2.1）。

---

## 3. 数据流变化

### 3.1 子 Agent · 成功路径

| | 事件序列 |
|---|---|
| **改前 · 子** | `session.created` → `user.message` → `llm.usage` → **`tool.call`** → `decision.issued` → `receipt.emitted` → **`tool.result`** → `llm.usage` → `agent.message` |
| **改前 · 父** | `session.created` → `plugin.installed` → `subagent.spawned` → **`guard.evaluated`** ← ❗ 分裂 |
| **改后 · 子** | `session.created` → `user.message` → `llm.usage` → **`tool.call` → `guard.evaluated` → `decision.issued` → `receipt.emitted` → `tool.result`** → `llm.usage` → `agent.message` |
| **改后 · 父** | `session.created` → `plugin.installed` → `subagent.spawned` → `subagent.joined` |

**形态收敛**：改后子会话顺序与**主路径既有顺序一致**（[`test_tools_executor.py:289`](tests/unit/test_tools_executor.py) 已钉死 `["tool.call","guard.evaluated","decision.issued",…]`）。

### 3.2 子 Agent · 拒绝路径（A-2：`guard.rejected` 同步迁移）

| | 事件序列 |
|---|---|
| **改前 · 子** | `tool.call` → `decision.issued` → `tool.error` |
| **改前 · 父** | `guard.evaluated` **+** `guard.rejected`（**两者都在父**） |
| **改后 · 子** | `tool.call` → **`guard.evaluated` → `guard.rejected`(sync=True)** → `decision.issued` → `tool.error` |
| **改后 · 父** | *（无守卫事件）* |

### 3.3 主路径（chat / run / desktop / ACP 的普通调用）—— **逐项不变**

`ctx.session is chain._session` ⇒ `sess` 解析结果**恒等于原值** ⇒ 事件类型、顺序、`sync` 标志**一字不改**。

### 3.4 `guard.disabled`（policy 级）—— **不变**

仍写 `chain._session`（装配归属）。**理由**：它描述"某 guard 被关闭"这一**策略/配置动作**，不是"某一次调用被求值" ⇒ 与 D-2 的边界原则一致。**由 T-6 钉住**，防"一刀切"式过度修改。

### 3.5 跨会话事件**总量不变**，仅**落点**由父迁子

---

## 4. 测试计划

### 4.1 落点

**主**：`tests/invariants/test_inv_core.py`（INV-04 段；已有 `_wired_log` 真实 `SessionLog`+`EventBus`+真实 `SessionStore`，与 `_inv04_*` 系列）
**辅**：`tests/unit/test_tools_guard.py`（B-1 接受：`session=` kwarg 参数语义）

### 4.2 用例

| # | 名称 | 断言（判据） | 文件 |
|---|---|---|---|
| **T-1** | 子会话审计自足 | ① 子会话含 `guard.evaluated` 且其 `trace.call_id` == 该 `tool.call` 的 `call_id`；② **`AuditSystem(session=child_log).reconcile()` 不含 `NO-GUARD-EVENT`**；③ 子会话存在对应 `tool.result` | invariants |
| **T-2** | 主路径零回归 | ① 单元级：`ctx.session is chain._session` ⇒ `_audit` 落点与 `chain._session` **同一对象**；② 既有 `test_engine_flow.py::test_engine_full_chain_offline` 保持 GREEN（它已断言主路径全序） | invariants（+ 既有） |
| **T-3** | 父日志去噪 | 子 Agent 运行后：父会话**不含** `guard.evaluated`；父事件集合 == 改前父集合 **减去** 该守卫事件 | invariants |
| **T-4** | 拒绝路径同归属 | 子 Agent 触发 guard 拒绝：① 子会话含 `guard.rejected` 且 **`sync=True`**；② 父会话**不含** `guard.rejected`；③ 与 `tool.error` 同会话 | invariants |
| **T-5** | 单链不变式回归（封 C-ε） | 真实装配下 `spine.guard is spine.governance.policy.chain` 仍成立；`enabled_guard_ids()` 与 `describe_rules()` 面未变 | invariants |
| **T-6** | `_record`（`guard.disabled`）**不**随之迁移 | 触发 guard 关闭 ⇒ `guard.disabled` 仍落 **chain 装配会话**，**不落**调用会话 | invariants |
| **T-7**（B-1） | `session=` kwarg 语义 | ① 传 `session=X` ⇒ 事件落 X；② 不传 ⇒ 落 `chain._session`（默认值不变）；③ `evaluate()` 亦支持该 kwarg | test_tools_guard.py |

**判据自检（项目既有体例）**：**T-3 与 T-6 互为对照** —— T-3 断言"父**不含**"，T-6 断言"装配会话**含**"；二者合起来同时排除"把一切都迁走"与"什么都没迁"两种错误。

### 4.3 明确**不**新增

不写"跨会话 reconcile""父子 lineage 对账"用例 —— 那是 2-C，**已被 A-3 / C-β / C-δ 排除**；若写会制造"能力已存在"的误导。

### 4.4 既有用例影响（**已核验**）

| 面 | 结论 |
|---|---|
| `test_subagent.py` / `test_orchestration.py` / `test_engine_flow.py` | **无**任何 `guard.evaluated` 断言 ⇒ 不撞行为变更 |
| `test_tools_executor.py`（`:289/:370/:399/:446/:486/:927/:950/:1037`） | 主路径（`ctx.session == chain._session`）⇒ **不受影响** |
| `test_tools_guard.py`（~76 用例） | 直接用 `GuardChain(session=…)` + 单会话 ⇒ 默认值路径 ⇒ **不受影响** |
| `test_governance_authorize.py:109` | 单会话断言 ⇒ 不受影响 |

---

## 5. Mutation 计划

| # | 变异 | 期望 | **证明了什么** |
|---|---|---|---|
| **M-A** | `_audit` / `_append_rejected` 的会话解析改回 `self._session`（撤掉 `session=sess` 透传） | **T-1 RED**、T-3 RED、**T-2 GREEN** | 唯一鉴别点 = 会话解析；主路径不受影响 |
| **M-B** | `context.py` 删掉 `session=ctx.session`（回落默认） | **T-1 RED** | 装配层这一跳是**必要条件**（与 F-01 同型：能力齐备但装配未接） |
| **M-C** | 在子路径**另建一条 `GuardChain`** | **T-5 RED** | 封 3-C；**单链不变式有测试强制**（对应 C-ε） |
| **M-D** | `_append` 忽略入参 `session`（恒用 `self._session`） | **T-1 + T-4 RED** | 汇点层解析不可被绕过 |
| **M-E** ⭐ | **只迁 `guard.evaluated`，`guard.rejected` 留父** | **T-4 RED，T-1 GREEN** | **A-2 的必要性被测试强制** —— 半修当场被抓住 |
| **M-F** ⭐ | 改为**同时写入父子两个会话**（"双写"这一看似稳妥的错误修法） | **T-3 RED**（T-1 可能假绿） | "父日志去噪"有**独立判据**，不被双写蒙混 |
| **M-G** | 让 `_record`（`guard.disabled`）也跟随调用会话 | **T-6 RED** | 封过度修改（§3.4 的守点） |

**纪律**：逐个执行 → 验 RED → **立即还原** → `grep -rn "MUTATION" pyharness/` = **0** → 复跑全量确认基线回到 `1732/1730/2`。

---

## 6. 回滚点

### 6.1 快照（**R0 — 变更前**）

IMPLEMENT 第一步（在任何编辑之前）建立文件级快照：

```
tmp/f27_bak/            （tmp/ 已被 .gitignore 覆盖）
  ├── tools_guard.py.bak      ← pyharness/core/tools_guard.py
  ├── context.py.bak          ← pyharness/governance/context.py
  ├── test_inv_core.py.bak    ← tests/invariants/test_inv_core.py
  └── test_tools_guard.py.bak ← tests/unit/test_tools_guard.py
```

### 6.2 三级回退

| 级 | 场景 | 手段 |
|---|---|---|
| **R1** | 未提交（最可能） | 从 `tmp/f27_bak/` **逐文件复制回原位** |
| **R2** | 已提交 | **逆序** `git revert <C2> <C1>`（保留历史） |
| **R3** | 需丢弃全部 F-27 | `git reset --hard <C1 之前>` —— ⚠️ **本仓库工作树另有未提交改动**（F1 三件 / RT-GOV-01 / Stage 1 六件），**`reset --hard` 会一并丢弃** ⇒ **仅在确认无其他未提交价值时使用**；否则一律用 **R1** |

### 6.3 提交粒度（便于二分与回退）

| Commit | 内容 | 预估 |
|---|---|---|
| **C1** | `docs:` ADR-021 Status→Accepted + `docs/ADD.md` 登记（D1+D2） | 2 文件 |
| **C2** | `fix:` guard 事件会话归属（F1+F2）+ 测试（F3+F4） | 4 文件 |

> **提交纪律**：**显式列出文件**，**禁止 `git add -A`**（工作树含 F1 阶段既存未提交改动，`-A` 会混入）；提交信息**不署 AI 名**（公开仓库约定）。

### 6.4 中止判据

**若 T-2（主路径零回归预检）在实施中途 RED ⇒ 立即中止并回退到 R0**，不得"改测试让它变绿"（CORE-03）。

---

## 7. 验收判据（GATE-03 / GATE-04）

| # | 判据 |
|---|---|
| 1 | **T-1 ~ T-7 全 GREEN** |
| 2 | **全量回归 GREEN**，预期 **+7**（T-1~T-6 = 6 + T-7 = 1）⇒ `1739 collected / 1737 passed / 2 skipped`（基线 1732/1730） |
| 3 | **M-A ~ M-G 全 RED 且各由专属用例捕获**；还原后 `grep MUTATION` = 0 |
| 4 | `EVENT_TYPES / SYNC_TYPES / TRANSIENT` = **77/14/3 不变** |
| 5 | `git diff --name-only` **仅含 F1~F4 (+D1/D2)**，无 §1.4 白名单文件 |
| 6 | `spine.guard is spine.governance.policy.chain` 仍成立（T-5） |

---

## 8. 明确不做（约束落地）

| 约束 | 落地 |
|---|---|
| **C-α** 不改 INV-04 canonical definition | `docs/INVARIANT_REGISTRY.md` 不在改动清单；T-1 是**实现回归定义**，不是改定义 |
| **C-β** 不引入 cross-session reconcile | `reconcile` 签名与语义不动；T-5 不含跨会话用例 |
| **C-γ** 不改 AuditSystem 架构 | `governance/audit.py` 不在改动清单 |
| **C-δ** 不新增 Audit lineage resolver | 不新增任何 resolver API；2-C 留待另立 ADR |
| **C-ε** 保持单链不变量 | 不新建链；M-C 封死该方向 |

---

## 9. 已知残留（登记，不在本阶段修）

| # | 残留 | 处置 |
|---|---|---|
| **R-a** | **INV registry 行号证据锚漂移**：`docs/INVARIANT_REGISTRY.md` 的 INV-04 `Evidence Source` 以行号引用 `tools_guard.py :17/:44/:622/:688/:804/:808/:815`；其中 **`:688 / :804 / :808 / :815` 位于插入点之后**，改动后漂移约 **+2 ~ +6 行**（`:17/:44/:622` 在插入点之前，**不漂移**）。另注：`:804` = "结构防线:禁 bypass/放行/执行类方法面(INV-04 测试钉死)" 的 docstring，`:808` = 同段 `f"放行/执行路径(单调拒绝,INV-04);执行权归 executor 关3")` | **B-2 裁定：本阶段不改，仅在此记录**；**符号锚迁移（行号→符号）另立任务**。先例支撑：本项目已因同类硬编码行号吃过一次亏（`test_inv02_loop_is_the_only_dynamic_entry_dispatcher` 硬编码 `agent_loop.py:241` ⇒ "该行之前的增删会误报 RED"） |
| **R-b** | `evaluate()` 兼容 wrapper 在 B-1 后新增 kwarg ⇒ 其 docstring 的"签名…逐行不变"表述需微调为"**位置参数与返回语义不变**，仅新增可选 kwarg" | 随 F1 一并处理 |
| **R-c** | F-28 第二级（复合键）与本议题同属"跨会话归属" | 见 `RT-FIX-PLAN-v1.md` §3；建议合并一次设计评审 |

---

## 10. 待你确认

1. **提交粒度**采纳 §6.3 的 **C1（docs）+ C2（fix+tests）两段式**？
2. **Step 0**（ADR-021 `Status → Accepted`）由我在 IMPLEMENT 首步执行，还是你先自行确认 ADR 文本无误后再动？
3. §1.2 的 **F4（`test_tools_guard.py` 单元用例）** 是否与本批同提交（B-1 已接受其必要性，此处仅确认**归属批次**）。

确认后进入 **IMPLEMENT**（将严格按本计划执行，并在收尾输出 `F27_CHANGE_REPORT.md`）。
