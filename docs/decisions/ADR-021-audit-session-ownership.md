# ADR-021：审计会话归属（Audit Session Ownership）—— `guard.evaluated` 的落点、父子审计边界与 INV-04 校验范围

> **编号核验（2026-09-16 实测）**：`docs/ADD.md` 索引实际登记 **20 条**（ADR-001~020）；全库扫描 `ADR-021` **无定义占用** —— 仅 `S2-2.1_REGISTRATION_DESIGN.md:150,176,196` 与 `S2-5_SCOPE_UPDATED_AND_COMMENT_CONSISTENCY_DESIGN.md:221` 作为"**若采纳 Option B 才需要**"的**假设**提及，而 Option B 当时未被采纳。故取号 **021**，不占用任何既有编号。
>
> **落点位置说明**：本文件置于 `docs/decisions/`，为**新目录**。既有 ADR 正文位于**仓库根**（`ADR-019-*.md` / `ADR-020-*.md`），且 `docs/ADD.md` 以相对路径 `../ADR-0xx-*.md` 登记。**本目录属新建约定**；若采纳，须同时：
> ① 回填 `docs/ADD.md`（索引 + §2.1 路径表，链接由 `../ADR-0xx` 改为 `decisions/ADR-0xx`）；
> ② 决定既有 ADR-019/020 是否一并迁移（否则 ADR 将散落**三处**：ADD.md 索引 / 仓库根 / `docs/decisions/`）。
> **禁止**出现"ADR-021 存在于 `docs/decisions/` 却未登记"的中间态（本项目 S1 的 P-1/C-2 教训：ADR 已存在却不回填 ⇒ 形成两套并行注册表）。
>
> **A-6 裁定结果（2026-09-16）**：① 执行登记（见 A-5）；② **暂不迁移** ADR-019/020，**不立即改变 ADR 存储结构**。**已记录待办**：`ADR storage convention` 需**未来单独建立 ADR**（当前 ADR 实际散落两处：仓库根与 `docs/decisions/`）；本 ADR 不影响既有 ADR-019/020。
>
> **Status**：**Accepted（已接受）** —— 2026-09-16 人工裁定 A-1~A-6 全部接受（见 `## Status`）。

---

## Status

**Accepted（已接受）** —— 2026-09-16 人工裁定 **A-1 ~ A-6 全部接受**。

| 项 | 值 |
|---|---|
| 触发来源 | `FUNCTIONAL_RUNTIME_AUDIT_v1.md` **F-27**（P1）· `RT-FIX-PLAN-v1.md` **P-2** 裁定 |
| 前置 | RT-GOV-01（`_runtime_ctx` 缺 `governance`）已修 —— 该修复使子 Agent 工具调用**首次可达**，F-27 随之暴露 |
| 实施计划 | `F27_IMPLEMENT_PLAN.md`（C1 docs + C2 fix+tests 两段式） |
| 不改动 | INV-04 / INV-05 的 canonical 文本；`docs/INVARIANT_REGISTRY.md`；`AuditSystem` 架构；任何 cross-session reconcile / lineage resolver |

### 裁定记录（2026-09-16）

| # | 裁定项 | 结果 | 理由（用户原述要点） |
|---|---|---|---|
| **A-1** | `guard.evaluated` ownership = **1-A（child session）** | **接受** | 治理事件描述的是**具体 tool execution 的治理结果**，应归属**产生该 action 的 session** |
| **A-2** | `guard.rejected` **同步迁移**到 child session | **接受** | 成功路径与拒绝路径**必须保持同一 audit ownership 模型** |
| **A-3** | `AuditSystem` 保持 **single-session reconciliation**；不引入隐式 parent-child cross-session reconcile | **接受** | — |
| **A-4** | parent **不再**拥有 child execution governance event；parent 仅保留 `subagent.spawned` / `subagent.joined` / `subagent.failed`；child 保留自身完整 execution chain | **接受** | — |
| **A-5** | ADR-021 登记 `docs/ADD.md` | **接受** | 与 IMPLEMENT 同批处理（C1） |
| **A-6** | **暂不迁移** ADR-019/020；**不要立即改变 ADR 存储结构** | **接受** | **记录**：ADR storage convention 需**未来单独建立 ADR**；当前保持 ADR-021 不影响既有 ADR-019/020 |

### 实施约束（裁定附带，落码时必须遵守）

1. 不修改 **INV-04 canonical definition**
2. 不修改 **`docs/INVARIANT_REGISTRY.md`**
3. 不引入 **cross-session reconcile**
4. 不引入 **audit lineage resolver**
5. 不创建新的 **audit session**
6. 保持 **Session single-chain invariant**（`spine.guard is spine.governance.policy.chain`）

---

## Context

### 现象（实测，2026-09-16）

真实引擎 + 真实子 Agent（`EngineSubagentRunner`），子会话调用 `util.now` 工具：

| 日志 | 事件序列 |
|---|---|
| **子会话** `s-sub0001.jsonl` | `session.created` → `user.message` → `llm.usage` → **`tool.call`** → `decision.issued` → `receipt.emitted` → **`tool.result`** → `llm.usage` → `agent.message` |
| **父会话** | `session.created` → `plugin.installed` → `subagent.spawned` → **`guard.evaluated`** → `subagent.joined` |

```
AuditSystem(session=<子会话>).reconcile()   →   ['NO-GUARD-EVENT:call_id=c1']
```

### 机制

`GuardChain` 在**装配期**绑定固定 session（[tools_guard.py:665](../../pyharness/core/tools_guard.py) `self._session`，由 [engine.py:457-461](../../pyharness/engine.py) 的 `chain_factory` 注入）；其两个事件出口都写 `self._session`：

- `_audit()` → `guard.evaluated`（[tools_guard.py:812-828](../../pyharness/core/tools_guard.py)）
- `_append_rejected()` → `guard.rejected`（[tools_guard.py:830+](../../pyharness/core/tools_guard.py)）

而子 Agent 运行期 ctx 的 `session` 是**子会话**（[orchestration.py:49-73](../../pyharness/core/orchestration.py)），`guard` 却是父绑定实例 ⇒ **规则级事件落父日志，执行链落子日志**。

### 关键旁证：这是**遗留不一致**，不是有意设计

治理层的其他事件**已经是会话正确的**：

| 事件 | 出处 | 用的会话 |
|---|---|---|
| `decision.issued` | `GovernanceContext._emit_decision_issued`（[context.py:134](../../pyharness/governance/context.py)） | **`ctx.session`** ✅ |
| `receipt.emitted` | `receipts.emit(ctx, ...)`（[context.py:121-123](../../pyharness/governance/context.py)） | **`ctx.session`** ✅ |
| `approval.*` | `ApprovalProvider`（`_log_of(ctx)`） | **`ctx.session`** ✅ |
| `subagent.*` | `SubagentManager`（父管理器） | 父会话（**正确**：委派事实属父） |
| **`guard.evaluated` / `guard.rejected`** | **`GuardChain._session`** | **装配期固定** ❌ |

⇒ **同一治理层内，只有 `tools_guard` 一层用装配期固定会话。**

---

## Problem

**P-1** `guard.evaluated` 的会话归属未定义 ⇒ 实现取"装配归属"（父），语义应取"调用归属"（子）。

**P-2** 父子审计边界无成文规定 ⇒ 实现呈"父日志含子调用守卫事件"的**混合态**。

**P-3** INV-04 的校验范围被 `AuditSystem` 按 `session_id` 过滤**隐含收窄**：`docs/INVARIANT_REGISTRY.md` 的 INV-04 canonical 文本为"**(a) 执行前必有求值事实**：每个 `tool.result` / `tool.error` 之前必有**同 `call_id`** 的 `guard.evaluated`" —— **它是以 `call_id` 定义的因果链约束，从未要求"同一 session 分区"**。⇒ **不是 INV-04 违约，而是实现让 INV-04 在子会话内不可满足**。

**P-4** `AuditSystem` 的会话边界未定义（能否跨会话、由谁提供父指针）。

**P-5** "审计"与"Session 单链不变式"的关系未定义。

---

## Constraints

| # | 约束 | 来源 |
|---|---|---|
| **C-1** | **单链不变式**：`spine.guard is spine.governance.policy.chain`；`describe_rules` 与 `chain_factory` 注入参数同源 | S2-3.1 R-A/R-B；[test_engine.py:314/336](../../tests/unit/test_engine.py) |
| **C-2** | `authorize()` 是关 2 **唯一**授权入口，不得新增旁路 | S3-2-2；[tools_executor.py:476/615](../../pyharness/core/tools_executor.py) |
| **C-3** | **INV-01**：会话日志是唯一真源，**每会话一份**；子事件**不得**写进父 JSONL | [orchestration.py:131-140](../../pyharness/core/orchestration.py)（"child must not share the parent bus … otherwise duplicated into the parent JSONL"） |
| **C-4** | **INV-04 / INV-05 的 canonical 文本不得改写**；结论必须是"**消除实现偏差**"而非"改定义适配实现" | `docs/INVARIANT_REGISTRY.md`（`Status = migrated`） |
| **C-5** | 治理层只依赖 `events` + `errors` | ADR-018:308 |
| **C-6** | **不新增事件类型 / 不改载荷 schema**（本议题应可在既有词表内解决） | ADR-020 先例 |
| **C-7** | `tools_guard.py` 被 ~76 用例与 `tests/invariants` 的 INV-04 段密集覆盖 ⇒ 改动必须**加性**、默认路径**逐字不变** | `test_tools_guard.py` |
| **C-8** | 不得为过测试而改测试 | `PROTOCOL.md` CORE-03 |
| **C-9** | 审计必须能对**单个导出的 `.jsonl` 文件**离线自证（replay = recovery = audit 同一路径） | [persistence.py](../../pyharness/persistence.py) `replay` docstring；`SECURITY.md` |

---

## Options

### 决策 1 —— `guard.evaluated` 的 ownership

| | 归属 | 含义 |
|---|---|---|
| **1-A** | **发起 tool call 的 child session** | 事件跟随**调用** |
| **1-B** | **parent session**（现状） | 事件跟随**装配** |
| **1-C** | **独立 audit session** | 事件进入第三个日志 |

### 决策 2 —— `AuditSystem` 的会话边界

| | 方案 | 含义 |
|---|---|---|
| **2-A** | **永远只验证单 session** | 现状语义；审计产物可离线自证 |
| **2-B** | **支持 parent-child session graph 查询** | 审计层可遍历会话图 |
| **2-C** | **保持单 session reconcile，另提供显式 lineage resolver** | 主路径不变；跨会话能力**仅由调用方显式触发** |

### 决策 3 —— 事件发射的修法

| | 方案 | 手段 |
|---|---|---|
| **3-A** | **调用归属优先**：`GuardChain` 增加可选 `session=None` 形参，由 `authorize()` 传 `ctx.session` | 加性、不新建链 |
| **3-B** | 审计侧跨会话扩展 | 见决策 2-B |
| **3-C** | 每子会话各建一条链 | ❌ **构造性排除**：违反 C-1；且"注入参数同源"与 INV-04(b) 的"无翻回 API"单点失效 |

---

## Decision

> **以下为建议值，待人工裁定（A-1 ~ A-5）。**

### D-1 · `guard.evaluated` 的 ownership = **1-A：发起 tool call 的 child session**

**选择依据（五条，按强度排序）**：

1. **语义**：`guard.evaluated` 描述的是"**这一次调用**在执行前被求值过"——它是**调用**的属性，不是**引擎**的属性。同理 `guard.rejected` 描述"这一次调用被拒"。
2. **INV-04 可满足性**：INV-04(a) 要求"`tool.result`/`tool.error` 之前有**同 `call_id`** 的 `guard.evaluated`"。只有 1-A 使该要求在**单一日志内成立**（`call_id` 经 `trace` 携带，[tools_guard.py:826](../../pyharness/core/tools_guard.py)），从而 `reconcile` 自包含。
3. **一致性**：治理层的 `decision.issued` / `receipt.emitted` / `approval.*` **已全部**按 `ctx.session` 落点。1-A 使该层**收敛为一致口径**；1-B 会保留一处孤例。
4. **INV-01 / C-3**：1-B 把"子调用的守卫事件"写进**父**会话 ⇒ 父日志含 `guard.evaluated` 却**没有**对应的 `tool.call`/`tool.result`，父会话出现**断链的孤儿事件**，并使其因果链被不属于它的调用污染。
5. **离线自证（C-9）**：审计须能对**单个导出文件**自证。1-A 下子会话文件自足；1-B 下必须同时持有父文件才可解释。

**1-C（独立 audit session）不予采纳**：它引入**第三个日志**承载审计事实，构成**第二真源**（直接违反 INV-01 与 C-3）；且新会话需要自己的 `session.created`（EVT-106 约束：`session.created` 必须为首事件且 `seq=1`）、独立生命周期与独立落盘路径 ⇒ **载荷/词表/lifecycle 三重 schema 变更**（违反 C-6），而其与 `tool.call` 的一致性**仍无保障**（只是换了个地方分裂）。

### D-2 · Parent / Child 审计边界

**① parent session 是否拥有 child agent 的全部审计事件？→ 否。** 父只拥有**委派事实**。

**② child session 是否必须拥有自己的完整审计链？→ 是。** 子会话日志必须**自包含**其自身的完整执行因果链。

**③ 逐事件落点（建议的规范表）**

| 事件 | 落点 | 现状 | 理由 |
|---|---|---|---|
| `tool.call` | **child** | ✅ 已如此 | 调用的输入事实 |
| `tool.result` / `tool.error` | **child** | ✅ 已如此 | 调用的输出事实 |
| **`guard.evaluated`** | **child** | ❌ **父** | 与 `tool.call` 同 `call_id`，须同日志（D-1） |
| **`guard.rejected`** | **child** | ❌ **父** | 拒绝事实须与被拒调用同会话（INV-05 证据面） |
| `decision.issued` | **child** | ✅ 已如此 | 治理层用 `ctx.session` |
| `receipt.emitted` | **child** | ✅ 已如此 | 同上 |
| `approval.requested/granted/denied/timeout` | **child** | ✅ 已如此 | 审批用 `ctx.session` |
| `subagent.spawned/joined/failed` | **parent** | ✅ 已如此 | 委派是**父**的事实 |
| `segment.start/end` | 各自会话 | ✅ | — |
| `session.created/finished` | 各自会话 | ✅ | 生命周期自属 |

**边界原则**：**一个会话的日志自包含其自身因果链**。跨会话的**唯一**引用是**委派锚**——父的 `subagent.spawned{sub_id}` ↔ 子的 `{sub_id}.jsonl`（会话 id 即文件名）。**该锚无需新增任何载荷字段**（满足 C-6）。

### D-3 · INV-04 的校验范围 = **A：单 session 内严格校验**

**理由**：

1. **INV-04 的文本本就是 `call_id` 级因果约束**，不含 session 语义。选 B 等于把校验口径**扩大到 session 图**——那是**改写验证口径以适配缺陷**，而非修正缺陷。C-4 明令：结论必须是"消除实现偏差"。
2. **审计可离线自证（C-9）**：单文件必须自足。B 使"导出一个子会话文件"无法独立审计。
3. **先例一致**：本项目已有"不为保留旧编号重新解释语义"的裁定（`S6-1` 的 **P-4**）；同理不得为迁就实现而重释 INV-04。
4. **实施 D-2 + D-4 后，A 自然成立** ⇒ B 无需求。

### D-4 · `AuditSystem` → **2-A（保持单 session）**，lineage 能力（2-C）**延后且须显式**

**2-C 的价值与代价（逐项分析）**

| 轴 | 2-A 单 session | 2-B session graph | 2-C 显式 lineage resolver |
|---|---|---|---|
| **安全影响** | 边界最清晰；不跨真源；**无外部输入进入查询面** | ⚠️ 审计层成为**跨会话真源读取方**；需父指针；多级委派（depth ≤3）下反向查询（父→子）需索引；若 sid 可由外部（tenant/客户端）影响，则有**越权读取面** | reconcile 语义不变 ⇒ **无隐式跨会话**；resolver 仅在被显式调用时工作 ⇒ 无自动越权面。但 resolver 若接受外部 sid，仍须**自行做归属校验** |
| **实现复杂度** | **0**（现状） | **高**：需 `parent_sid` 落盘 —— `subagent.spawned` 载荷现为 `{sub_id, parent_seq, task}`（[subagent.py:770-773](../../pyharness/core/subagent.py)）**无 parent_sid** ⇒ 载荷 schema + payload 模型 + 词表三处改动（破 C-6）；或维护**外部索引**（新持久化面 = 新真源风险） | **中**：resolver 本身极小；**但数据来源与 2-B 同难**。运行期内存里有 `ChildHandle.parent_sid` / `ChildContext.parent_sid`（[subagent.py:774-782](../../pyharness/core/subagent.py)），**重启后不可得** ⇒ 仍需落盘方案 |
| **与 Session 单链不变式关系** | **无** | 无直接关系（属 audit 侧），但引入"跨会话事件流"概念，与 D-5 冲突 | **无**（纯读侧、纯显式） |

**建议**：**现在采纳 2-A**；**2-C 延后**，且其数据来源（父指针如何落盘）**须另立 ADR**（属 schema 变更，不在本 ADR 范围）。**2-B 不推荐**。

### D-5 · Session 单链不变式的影响 —— 三个"是否"的明确回答

| 问题 | 回答 | 依据 |
|---|---|---|
| **一个 audit chain 是否允许横跨多个 session？** | **不允许** | 每个 session 的 audit chain **自包含**（D-2）。跨会话的只有**委派锚**，它是"事实引用"而非"链的延伸" |
| **一个 session 是否允许查询其它 session 的 event？** | **不允许隐式查询** | `reconcile` 只读 `ctx.session`（[audit.py](../../pyharness/governance/audit.py) `_events`）。**显式**跨会话对账（2-C）不改变此语义：它由调用方在 `reconcile` **之外**构造 |
| **parent-child 是否共享 event stream？** | **不允许**，且**已有既成事实反对** | 子会话使用**独立 `EventBus`**（[orchestration.py:135-150](../../pyharness/core/orchestration.py)），其注释明确："*The child must not share the parent bus: the parent engine persists all events seen on its bus, so child events would otherwise be duplicated into the parent JSONL.*" ⇒ 共享 event stream 会**直接制造第二真源**（父 JSONL 复制子事件） |

**对"单链不变式"（C-1）的影响：无。** `spine.guard is spine.governance.policy.chain` **继续成立**——3-A 不新建链，链对象仍唯一；改变的只是**事件汇点按调用解析**而非装配期固化。**只有 3-C（每子会话一条链）会违反 C-1，已被构造性排除。**

### D-6 · 事件发射修法 = **3-A**

`GuardChain.evaluate_detailed()` / `_evaluate_full()` 增加**可选** `session=None` 形参（`None` ⇒ 沿用 `self._session`），透传至 `_audit()` / `_append_rejected()`；由 `GovernanceContext.authorize()` 传入 `ctx.session`。

- **加性**：主路径（`ctx.session is chain._session`）**逐字不变** ⇒ 零回归（守 C-7）
- **不新建链**（守 C-1）、**不新增入口**（守 C-2）、**不新增 import**（守 C-5）
- **零 schema 变更**（守 C-6）

---

## Consequences

### 正面

1. **F-27 消除**：`AuditSystem(child).reconcile()` 不再报 `NO-GUARD-EVENT`；INV-04 的证据面在子会话内**可满足**。
2. **子会话因果链首次完整**：`tool.call → guard.evaluated → decision.issued → receipt.emitted → tool.result` **同日志**（S5 审计矩阵的前置）。
3. **父日志去噪**：不再含不属于它的守卫事件。
4. **离线自证成立**（C-9）：单个导出会话文件可独立审计。
5. **治理层口径收敛**：`tools_guard` 与 `decision.issued`/`receipt.emitted`/`approval.*` 统一按 `ctx.session`。
6. **零 schema 变更**：`EVENT_TYPES/SYNC_TYPES/TRANSIENT = 77/14/3` 不变。

### 负面 / 代价

1. **触 `tools_guard.py`**（C-7 的密集覆盖区）⇒ 必须严格加性。
2. **`GuardChain` 语义微变**：从"事件汇点 = 装配期固定"变为"**每次调用解析**"。须在模块 docstring 与 ADR 同时写明，防后人误以为链是"会话级"对象。
3. **行为变更**：父会话**不再**收到子调用的守卫事件。这是**有意的**（D-2），但任何断言"父日志含某 `guard.evaluated`"的用例须重判（CORE-03）。**已核验**：`test_subagent.py` / `test_orchestration.py` / `test_engine_flow.py` **无**此类断言；其余 `guard.evaluated` 断言均在主路径（`ctx.session == chain._session`）⇒ 不受影响。
4. **`guard.rejected` 一并改变**（若采纳 A-2）⇒ INV-05 的证据位置随之移至子会话。**建议一并改**，否则同一链的两个出口分属两会话，语义自相矛盾。
5. **跨会话委派一致性仍不可查**（2-C 延后）⇒ 登记 deferred。

### 违反它的后果

| 情形 | 后果 |
|---|---|
| **不修（现状）** | 每个子 Agent 工具调用恒报 INV-04 违规 ⇒ 审计输出**失去信号价值**；INV-04 在子会话内**恒不可满足** |
| **采纳 1-B** | 父日志出现无对应 `tool.call` 的**孤儿守卫事件**；子会话因果链断裂；C-9 离线自证失效 |
| **采纳 1-C** | **第二真源**（违反 INV-01/C-3）；会话生命周期与 `seq=1` 引导约束需另行设计（破 C-6） |
| **采纳 2-B** | 需 `parent_sid` 落盘（破 C-6）；审计层跨越 INV-01 的每会话边界；多级委派下反向查询需索引；**审计可信度依赖两文件同时在位** |
| **采纳 3-C** | **直接违反 C-1**：`spine.guard is policy.chain` RED；"注入参数同源"与 INV-04(b) 的单点论证基础被削弱 |
| **裁定后未回填 ADD.md** | ADR 存在却未登记 ⇒ **两套并行注册表**（S1 的 P-1/C-2 教训重演） |

---

## Migration Impact

### 修改面（**预估，尚未实施**）

| 文件 | 改动 | 量级 |
|---|---|---|
| `pyharness/core/tools_guard.py` | `evaluate_detailed()` / `_evaluate_full()` 增可选 `session=None`；`_audit()` / `_append_rejected()` 增可选 `session=None`（`None` → `self._session`）；两处 `_append` 调用改用解析后的会话；模块 docstring 补"事件汇点按调用解析" | **≈15~30 行，全加性** |
| `pyharness/governance/context.py` | `authorize()` 内 `chain.evaluate_detailed(call, scope, session=ctx.session)` | **1 行** |
| `tests/unit/test_tools_guard.py` 或 `tests/invariants/test_inv_core.py` | 新增 T-1 ~ T-5 | ≈60~90 行 |
| `docs/INVARIANT_REGISTRY.md` | **不改定义**；仅 INV-04 的 `Existing Test Evidence` 补登新用例（**属独立授权项**，本 ADR 不自动触发） | 0~3 行 |
| `docs/ADD.md` | ADR-021 登记（索引 + §2.1 路径表；若采纳 `docs/decisions/` 则链接为 `decisions/ADR-021-*`） | ≈3~5 行 |

### **明确不改**

`pyharness/core/orchestration.py`（`_runtime_ctx` 无需变）· `pyharness/engine.py` · `pyharness/governance/audit.py` · `pyharness/core/tools_executor.py` · 事件词表 / payload 模型 · `.ai-coding/PROTOCOL.md` · `ARCHITECTURE_DECISION_RECORD.md` · `docs/INVARIANT_REGISTRY.md` 的定义面

### 兼容性

- **主路径逐字不变**：`ctx.session is chain._session` ⇒ 解析结果恒等于原值 ⇒ 既有 ~76 用例的行为断言不变。
- **子路径行为变更**：子会话**新增** `guard.evaluated`（此前落父）；父会话**不再**收子调用的守卫事件。
- **事件计数**：跨会话**总量不变**，只是**落点**由父迁子。

### 验证方案（实施时执行）

| # | 用例 | 判据 |
|---|---|---|
| **T-1** | 子 Agent 工具调用后，**子会话**含 `guard.evaluated`（同 `call_id`） | `AuditSystem(child).reconcile()` **无** `NO-GUARD-EVENT` |
| **T-2** | 主 Agent 路径**对照** | 主会话事件序列与修复前**逐项一致** |
| **T-3** | 父会话**不再**含子调用的 `guard.evaluated` | D-2 边界成立 |
| **T-4** | 子 Agent 的 guard **拒绝**路径 | `guard.rejected` 落**子**会话且 `sync=True` |
| **T-5** | 单链不变式回归 | `spine.guard is spine.governance.policy.chain` 仍成立 |

**Mutation 反证**：
- **M-A**：把 `_audit`/`_append_rejected` 的会话解析改回 `self._session` ⇒ **T-1 RED**、T-2 仍 GREEN
- **M-B**：让 `authorize` 传 `self._session`（而非 `ctx.session`）⇒ **T-1 RED**
- **M-C**：（封 C-1）在子路径另建一条链 ⇒ **T-5 RED**

### 回归风险与顺序

**中高**。缓解：① 严格加性（默认 `None`）；② **先跑 T-2**（主路径逐项对照）作为"零回归"的**先行判据**，再做子路径；③ 全量回归 + `tests/invariants` 定向。

**前置**：无（RT-GOV-01 已使子路径可达）。
**后继**：F-28 第二级（复合键）与本 ADR 同属"跨会话归属"议题，**建议合并一次设计评审**（见 `RT-FIX-PLAN-v1.md` §3）。

---

## Recommended Decision

**采纳组合：`1-A`（ownership = child session）+ `2-A`（AuditSystem 单 session）+ `3-A`（调用归属优先的加性修法）。**

一句话：**`guard.evaluated` / `guard.rejected` 跟随调用（`ctx.session`）；父会话只拥有委派事实；INV-04 按单会话严格校验；`AuditSystem` 保持单会话；跨会话 lineage（2-C）延后且须显式、另立 ADR。**

### 若进入 IMPLEMENT，需要修改的文件

| # | 文件 | 改动 | 性质 |
|---|---|---|---|
| **1** | `pyharness/core/tools_guard.py` | 4 处加可选 `session=None`（`evaluate_detailed` / `_evaluate_full` / `_audit` / `_append_rejected`）+ 2 处 `_append` 改用解析会话 + docstring | **加性** |
| **2** | `pyharness/governance/context.py` | `authorize()` 传 `session=ctx.session`（**1 行**） | 加性 |
| **3** | `tests/unit/test_tools_guard.py` **或** `tests/invariants/test_inv_core.py` | 新增 **T-1 ~ T-5** | 新增 |
| **4** | `docs/ADD.md` | ADR-021 登记（索引 + §2.1 路径表） | **裁定通过后**执行 |
| **5** | `docs/INVARIANT_REGISTRY.md` | INV-04 的 `Existing Test Evidence` 补登（**仅证据，不改定义**） | **独立授权** |

**明确不在 IMPLEMENT 范围**：`orchestration.py` · `engine.py` · `audit.py` · `tools_executor.py` · 事件词表/payload · 协议 · 冻结记录 · 2-C 的 lineage 能力。

### 待人工裁定

| # | 裁定项 | 建议 |
|---|---|---|
| **A-1** | 是否采纳 **1-A**（ownership = child session） | **采纳** |
| **A-2** | 是否**同时**改变 `guard.rejected` 的落点 | **同时改**（否则同链两出口分属两会话） |
| **A-3** | `AuditSystem` 是否**永久**保持单 session（**2-A**）；**2-C** 是否延后 | **2-A 现在；2-C 延后并另立 ADR** |
| **A-4** | 是否接受"父日志不再含子调用守卫事件"为**有意行为变更** | **接受** |
| **A-5** | 裁定通过后是否**立即**回填 `docs/ADD.md` | **立即回填** |
| **A-6** | （本文件引入的）`docs/decisions/` 目录约定是否成立；既有 ADR-019/020 是否一并迁移 | **需你决定** —— 否则 ADR 将散落三处 |
