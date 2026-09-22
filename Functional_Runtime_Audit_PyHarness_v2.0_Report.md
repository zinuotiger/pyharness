# Functional_Runtime_Audit_PyHarness_v2.0_Report

> **方法论**：Functional Runtime Audit v2.0（Frozen）
> **模式**：**M1 Audit**（只读）｜ M2 规划 / M4 状态验证 / M5 闭环**评估** —— 本次**不含 M3 Implementation**
> **审计对象**：PyHarness（`<repo>` = `Desktop/mini-harness`）
> **审计基线**：`HEAD = cbb2033`（2026-09-17）· `main == origin/main`（ahead/behind = 0/0）· worktree 有 11 个未跟踪 `.md`
> **审计日期**：2026-09-17
> **本轮未修改任何文件**（报告本身除外，见 §8）。未执行任何 git 写操作。

---

## 1. Audit Overview

### 1.1 审计范围（Audit Scope）

| 类别 | 纳入 | 不纳入 |
|---|---|---|
| 代码 | `pyharness/` 全部 85 个 `.py` | `build/` `dist/` `__pycache__/` `.venv/` |
| 声明载体 | `README.md` · `LIMITATIONS.md` · `CODE-MATRIX.md` · `GOVERNED_AGENT_RUNTIME_DESIGN.md` · `docs/INVARIANT_REGISTRY.md` · 源码注释/docstring · `pyproject.toml` | 根目录 `S1_*`~`S6_*` 阶段报告（自述为**历史记录**，不承担当前状态声明） |
| 测试 | `tests/` 全量收集与执行（1,751 collected） | `desktop_native/`（本机缺 PySide6，无法收集） |
| 运行时 | 真实装配引擎 + 真实工具管道 + 落盘 JSONL | 真实 LLM 调用（无 key）、MCP/网络依赖面 |

**本轮重点**：Capability Reachability · False Closure · Claim Drift · Governance Boundary · Evidence Level。

### 1.2 结论（先行）

**PyHarness 声明中「工具调用治理链」这一层，经运行时证实为真。**
`authorize()` 唯一入口（生产恰 2 处调用）、7 条 guard 单调链、`decision.issued` 与 `authorize()` 1:1、`receipt.emitted` 在放行与拒绝两侧各落一条 —— **全部取得 L3 运行执行证据**，非仅代码存在。README 的核心数字（1,751/1,749/2 skipped/79%/77 型事件/16 子命令）**逐项复测通过**。

**但「治理闭环」的外围三层，全部停在 L1/L2 —— 建造完整、已注入上下文对象、有单测与不变量测试，却没有任何生产调用路径。**

| 层 | 状态 | 说明 |
|---|---|---|
| **审计查询面**（`AuditSystem.causal_chain` / `denied_report` / `reconcile`） | **L2 → 无 L3** | 在 `engine.py:661` 构造并注入 `governance.audit`，**全库 0 处调用** |
| **证据归档面**（`EvidenceCollector.archive` / `collect_for_task`） | **L2 → 无 L3** | 订阅者已接线（索引可跑），但**无任何生产者** ⇒ 索引恒空 |
| **凭证核验面**（`verify_chain` / `ReceiptStore.verify`） | **L1 → 无 L3** | 仅测试调用；`verify_chain` **未进入包级 `__all__`** |

这三层恰好是 README「Governed Agent Runtime」叙事中"**凭什么 / 有无凭证 / 能否独立核验**"所依赖的机制。**链是通的，闭环是断的。**

**次生发现**：两个桌面壳（README 主推的产品形态）**从不设置 `ctx.channel`**，导致 `decision.issued` 中的人类主体被静默记为 `SYSTEM` —— 治理留痕的"**谁**"在两个桌面壳上失真。此点取得 L3 证据。

**风险分布**：P0 ×0（无隐匿性越权/无可触发的错误结论）· **P1 ×6** · P2 ×7 · P3 ×2。**无 P0** 的判断依据见 §4.1。

---

## 2. Architecture Understanding

### 2.1 实测架构（非文档复述）

单进程、事件溯源、四层。**装配路径**（`engine.build_spine` → `_build_governance`）实测产出：

```
EngineSpine
├── session   (JSONL append-only 真源)
├── bus       (自研插件总线,订阅者按 owner 隔离)
├── guard     GuardChain  —— 实测 7 条(见下)
├── approval  ApprovalProvider
├── governance GovernanceContext(单实例,ADR-018)
│     ├── policy    PolicyEngine        ✅ 运行链
│     ├── decisions DecisionEngine      ✅ 运行链
│     ├── receipts  ReceiptStore        ✅ 运行链(写) / ❌ 核验面无调用
│     ├── evidence  EvidenceCollector   ⚠️ 订阅已接 / ❌ 无生产者
│     └── audit     AuditSystem         ❌ 构造即止,零调用
└── tools     ToolExecutor / ToolRegistry
```

**工具执行四关**（`core/tools_executor.py:430 execute()`）：

| 关 | 位置 | 实测行为 |
|---|---|---|
| 关 1a/1b 契约 | :443-461 | 未注册 → `tool.error`；参数非法 → 立即返回，零调用（INV-06） |
| **关 2 治理授权** | **:476 `await ctx.governance.authorize(...)`** | **唯一治理入口** |
| 关 2.5 审批 | :481-486 | `d == "approval"` → `_approval_round`（:622 为第 2 处 `authorize`，D2 重入） |
| 关 3 Provider | :488-521 | 线程池 + 超时 `TLB-805`；取消时如实记 partial |
| 关 4 结果检查 | :523 | `_finalize` → `tool.result` |

**单调性实测**：`evaluate_detailed` 全库**仅 1 处生产调用**（`governance/context.py:117`）。不存在第二条求值路径 ⇒ 「guard 可拒绝、不可放行」在**结构上**成立，非仅靠测试断言。

**实测 guard 链（7 条，运行期枚举）**：

```
g-schema · g-fs-path · g-credential-read · g-exec · g-overwrite · g-net-outbound · g-danger
```

`FORCED_GUARDS = {'g-danger', 'g-schema'}`（不可关闭）。

### 2.2 运行时证据（本轮实际执行，非推导）

用 `engine.assemble_real_engine()` 装配真实脊柱，经真实 `ToolExecutor` 驱动两次工具调用（一次放行 / 一次拒绝），读回**磁盘 JSONL**：

```
session.created  1
tool.call        2      ← 两次调用均有存档
guard.evaluated  2      ← 与 authorize 1:1
decision.issued  2      ← 与 authorize 1:1
receipt.emitted  2      ← 放行 1 条 + 拒绝 1 条
tool.result      1      ← 仅放行那次
guard.rejected   1      ← 拒绝那次
```

**关键**：被拒调用有 `tool.call` 但**无 `tool.result`** ⇒ `docs/MAP.md:150`「拦了且没执行」取得运行证据。
**未出现**：`evidence.archived`（0）· `policy.updated`（0，见下）· `approval.*`（本场景无 danger≥high）。

**`policy.updated` 单独复测**（F-01 声称的修复）：经 `TaskQueue.submit` 驱动 `make_runner.run_for_task`（`engine.py:785-798` 为发射点）：

```
session.created 1 · task.enqueued 1 · task.started 1 · segment.start 1
plugin.installed 1 · policy.updated 1 · user.message 1
```

**`policy.updated` 恰 1 条** ⇒ L-2 自述的 F-01 修复**取得运行证据**。

---

## 3. Findings

> **Evidence Level 口径**：L1 = 代码存在证据｜L2 = 调用链/装配证据｜L3 = 运行执行证据。
> 每条均附 `detect:` 命令 —— 该命令在问题存在时失败/非零输出。

---

### F-01 · AuditSystem 全层零生产调用路径

| 字段 | 内容 |
|---|---|
| **ID** | F-01 |
| **Severity** | **P1** |
| **Category** | V-2 Missing Runtime Path · V-3 Governance Gap |
| **Description** | `AuditSystem` 的四个公开方法 `causal_chain` / `denied_report` / `reconcile` / `legacy_session_audit` 在 `engine.py:661` 被构造并注入 `GovernanceContext.audit`，但**全库无任何调用点**。`GOVERNED_AGENT_RUNTIME_DESIGN.md:589`（M7 必做交付项）声明验收判据为「**一条命令**还原一次拒绝」；库中**不存在该命令**（CLI 无子命令、无 API 路由、无工具）。README:74 的「审计 ✅」由**旧遥测** `telemetry.session_audit` 满足（`application/service.py:533` → `/api/sessions/{sid}/telemetry`），**非** `AuditSystem`。 |
| **Evidence** | `engine.py:661` `audit = AuditSystem(session=log_, legacy_audit=_legacy_audit)`（唯一构造点）<br>`governance/context.py:55` `audit: Optional[AuditSystem] = None`（仅注入）<br>四方法调用点计数：`causal_chain` **0** · `denied_report` **0** · `reconcile` **0** · `legacy_session_audit` **0**<br>README:74 与 `application/service.py:533` 的审计数据源对照 |
| **Detection Method** | 方法名全库调用点计数（排除 docstring 与定义行）+ 审计数据源回溯 |
| **detect:** | `grep -rn "\.causal_chain(\|\.denied_report(\|\.reconcile(" --include=*.py pyharness/ \| wc -l` → 期望 `0`（当前 `0`） |
| **Runtime Impact** | 治理链产生的 `decision.issued` / `receipt.emitted` **数据在盘上**，但**没有产品内途径**把它还原成「谁/何时/因何/依据哪版策略/结果」。审计能力实际由**旧遥测的类型计数聚合**提供，不含决策因果。用户按 README/设计文档预期使用会得到**能力缺口而非错误结论**。 |
| **Evidence Level** | **L2**（装配点存在）**→ 无 L3**（零运行调用） |
| **Recommendation** | 二选一，**不得留悬空声明**：① 补最小运行面（一条 CLI 子命令或一条只读 API 路由，走 `causal_chain`）；② 在 `LIMITATIONS.md §2` 显式登记为未实现项（当前**仅在 §1 能力表内隐性提及**，§2 的 L-1~L-9 **无对应条目**）。 |

---

### F-02 · 凭证核验面为「测试专用」——`verify_chain` 未进入包级公共 API

| 字段 | 内容 |
|---|---|
| **ID** | F-02 |
| **Severity** | **P1** |
| **Category** | V-1 False Completion · V-4 Evidence Gap |
| **Description** | README:11 声明 `receipt.emitted` 为「**可校验凭证**（带 `prev_hash` 链，**可独立校验**）」。实测：`verify_chain` 的**全部调用点仅在测试内**（`tests/invariants/test_inv_governance.py:144,151`）；`verify_receipt` 的生产调用点只有 `receipt.py:290`（`ReceiptStore.verify` 内部），而 `ReceiptStore.verify` 自身**零调用**；`verify_chain` 与 `rebuild_from_log` **未出现在 `pyharness.governance.__all__`**（仅 `verify_receipt` 被导出）；四个外壳 `cli.py` / `desktop/app.py` / `desktop_native/controller.py` / `acp.py` 中 **`receipt` 零命中**。即：**链被生成、被测试，但从不被校验**，且链校验器未出现在包的公共 API 面上。 |
| **Evidence** | `pyharness/governance/__init__.py:29,40`（仅导入/导出 `verify_receipt`）<br>运行时断言：`hasattr(pyharness.governance,'verify_chain')` → **False**<br>`verify_chain` 调用点：测试 2 处，生产 **0** 处<br>四外壳 `receipt` 命中数 **0** |
| **Detection Method** | 公共 API 面运行时探查 + 调用点分类计数（生产 / 测试） |
| **detect:** | `python -c "import pyharness.governance as g; assert hasattr(g,'verify_chain')"` → 当前 **AssertionError**（问题存在时失败） |
| **Runtime Impact** | 「防篡改」的可证伪性停留在测试层。消费者**无法**经产品接口核验凭证；且因 `verify_chain` 未导出，按 `import pyharness.governance` 的直觉写法会 `AttributeError`，须知道子模块路径 `pyharness.governance.receipt` 才行。 |
| **Evidence Level** | **L1**（实现完整、有测试）**→ 无 L3** |
| **Recommendation** | 与 F-01 同源处置。最小动作是**把 `verify_chain` / `rebuild_from_log` 补进 `__all__`**（成本 1 行、不改语义），或在 README 措辞上把「可独立校验」降级为「**提供离线校验函数（库级 API，未接外壳）**」。 |

---

### F-03 · 【L3 实证】桌面壳从不设置 `ctx.channel` ⇒ 治理留痕的人类主体静默降级为 SYSTEM

| 字段 | 内容 |
|---|---|
| **ID** | F-03 |
| **Severity** | **P1**（**M2 重判为 P0**，见下方更正段；原 P1 保留不改） |
| **Category** | V-3 Governance Gap · V-5 Claim Drift（overstated） |
| **Description** | `governance/context.py:62-79` `principal_of()` 的设计意图是「主体取自运行时**真实 caller/channel identity**」，并逐壳列举：CLI `"cli"`、ACP `"acp:<client_id>"`、**Desktop `"desktop"`（`application/service.py`）**。实测：`ApplicationService.__init__` 把 channel 存进 `self.channel`（`service.py:78`），**从不赋值 `ctx.channel`**；`desktop_native/controller.py:36` 同理。CLI（`cli.py:776,927`）与 ACP（`acp.py:302`）**都设置**。结果：**两个桌面壳**（README 主推形态）的每次工具调用，`decision.issued` 都把人类发起的动作记为 `SYSTEM`。 |
| **Evidence** | 运行输出（`assemble_real_engine` = 桌面装配路径）：<br>`engine ctx has channel attr? False None`<br>`decision.issued -> verdict=allow principal_kind=system principal_id=system principal_channel=None`<br>对照：`cli.py:776` `ctx.channel = None if headless else "cli"`；`acp.py:302` `ctx.channel = f"acp:{st.client_id}"`；`service.py:78` 仅 `self.channel = ...` |
| **Detection Method** | 真实装配 + 真实工具调用 + 读回落盘 `decision.issued` 载荷 |
| **detect:** | 装配后断言 `getattr(ctx,'channel',None) is not None`（桌面路径）→ 当前 **False**；或读回日志断言 `decision.issued.payload.principal_kind != 'system'` → 当前为 `'system'` |
| **Runtime Impact** | 治理可追溯性的「**谁**」在两个桌面壳上失效。这不是 fail-closed（不拒绝、不报错），而是**静默归属错误** —— 一条由人类在桌面点击触发的工具调用，审计上无法与框架自身发起的调用区分。反向影响：因 `principal` 参与 `content_hash`（`receipt.py:92-94`），凭证内容本身正确，但**内容里记的主体是错的**。 |
| **Evidence Level** | **L3**（运行执行证据） |
| **Recommendation** | 装配层补齐一跳：`ApplicationService` / `NativeController` 初始化时同步 `ctx.channel = self.channel`。**注意**：`context.py` 文档注释明确「无通道 ⇒ 沿用 `by="system"` 既有语义，**绝不伪装** HUMAN/AGENT」，故当前行为不构成"伪装"，但**降级触发在本非 headless 的路径上**，与设计枚举不符 ⇒ 属**实现与设计枚举的字面矛盾**，应同时修正文档枚举或补齐赋值。 |

> #### M2 Root Cause Correction（根因更正）
>
> **追加于 `PyHarness_Runtime_Governance_Improvement_Plan_v1.md` v1.1 阶段（2026-09-17）**。
> 本节**不修改上方任何原文** —— 原描述与证据**按审计轨迹保留**。上方 `Description` / `Runtime Impact`
> 中"**两个桌面壳**"的范围陈述**已被本更正段取代**。
>
> **更正一 —— 范围：不是桌面壳缺陷，而是全壳缺陷。**
> M1 探针把**外壳门面 ctx** 传给了 `execute()`，而非 executor 真正收到的对象。真实调用链为：
> `make_runner.run_for_task`（`engine.py:813`）→ `_agent_ctx_of` → **`create_agent`（`core/agent.py:461-481`）产出的 `ag.ctx`**
> → `loop.wake` → `agent_loop.py:280` `ctx.tools.execute(call, ctx)`。
> 实测：`build_spine` 无 `channel`；`ag.ctx` **无 `channel`**；`GovernanceContext.principal_of(ag.ctx)`
> → `Principal(kind=SYSTEM, id='system', channel=None)`。
> **⇒ 生产链上每一次 `decision.issued` 的主体都是 `SYSTEM` —— CLI / ACP / Web 桌面 / 原生壳，无一例外。**
> CLI（`cli.py:776,927`）与 ACP（`acp.py:302`）的赋值写在**外壳门面 ctx**（另一个对象）上，**到不了 executor**。
>
> **更正二 —— 严重性：P1 → P0。**
> 依据 skill 风险模型 P0(a)：声称提供但运行不可达，**且使消费者得到错误结果**
> （审计轨上写入的是错误的主体值，而非缺失值）。**非** P0(c) —— `principal` 在
> `DecisionEngine.decide()` 中仅出现于字段定义 / 签名 / 赋值，**从不参与 verdict**，故不构成权限旁路。
>
> **更正三 —— 根因：不是单点遗漏。**
> 实测 **57 个被读取的 `ctx` 字段中 28 个不在 `ag.ctx` 上**；生产中有 **6 处手写 `ctx` 仿制品**
> （`service.py:143/370/397/437` · `orchestration.py:77` · `engine.py:344` · `cli.py:1023`）；
> 同类缺口有**四种互不一致的应对策略**。**不存在 Context Propagation Contract** ——
> F-03 只是**唯一未被掩盖**的那个（因为其兜底值 `SYSTEM` 是"语义上有意义的合法值"，不抛错）。
> 该系统性状态**已另立为 F-17**（P1）。
>
> **更正四 —— 关联缺陷：F-19（新增）。**
> 原生壳**审批通道被硬编码为 `"desktop"`**（`engine.py:538`），`ApplicationService.channel`
> （`"desktop-native"`）到不了 provider（实测 `_ensure_channel(ag.ctx) = 'desktop'`）。
> 若只修治理层，同一次调用会出现 `decision.issued=desktop-native` / `approval=desktop` 的**双源不一致**。
> ⇒ **F-03 与 F-19 必须同批修复**（计划文件 B3）。
>
> **更正五 —— 修复前置约束。**
> `"desktop-native"` **不在** `HUMAN_CHANNELS`（`cli/web/acp/desktop`）内，
> `Principal.from_legacy_by("desktop-native")` **实测抛 `APR-503`**（fail-closed）。
> 天真透传会让原生壳**每次工具调用直接失败**。已裁定采用**扩充白名单**方案
> （`decision.py:57` + `approval.py:87,103` 同改；爆炸半径见计划文件 §5.1）。
>
> **状态机**：F-03 因本更正**回到 `OPEN` 并重判**，随后由 M2 规划进入 `PLANNED` 的候选（B3）。
> 本报告 F-03 条目的终态以 `PyHarness_Runtime_Governance_Improvement_Plan_v1.md` 为准。

---

### F-04 · EvidenceCollector 索引有订阅者、无生产者（已部分披露）

| 字段 | 内容 |
|---|---|
| **ID** | F-04 |
| **Severity** | **P2** |
| **Category** | V-2 Missing Runtime Path · V-4 Evidence Gap |
| **Description** | `engine.py:650-654` 确实把 `evidence.on_event` 订阅到 4 类事件（订阅面真接线）；但 `archive()`（**唯一写点**）与 `collect_for_task()`（聚合入口）在 `pyharness/` 中**零调用**，`evidence.archived` 在生产日志中永不出现 ⇒ **索引恒空**。`LIMITATIONS.md §1` 已如实披露为「运行链接入为后续扩展」，**但该限制未登记进 §2 的 L-1~L-9 限制表**。 |
| **Evidence** | `archive(` 生产调用点 **0**（全部命中在 `tests/unit/test_governance_evidence.py`）<br>`collect_for_task` 生产调用点 **0**<br>`engine.py:654` `bus.subscribe(t, evidence.on_event, owner=ev_owner)`<br>运行日志中 `evidence.archived` 计数 **0** |
| **Detection Method** | 写点调用点分类计数 + 运行日志事件类型普查 |
| **detect:** | `grep -rn "\.archive(\|collect_for_task(" --include=*.py pyharness/ \| grep -v "def " \| wc -l` → 期望 `>0`（当前 `0`） |
| **Runtime Impact** | 无错误结论（`collect_for_task` 对空索引返回 `()`，不抛）。但「按 task 聚合完整证据链」（设计文档 M6）在运行期**不可达**。 |
| **Evidence Level** | **L2**（订阅已接）**→ 无 L3**（无生产者） |
| **Recommendation** | 登记进 `LIMITATIONS.md §2`（当前只在 §1 表内以括注形式出现，读者按"限制表"检索会漏）。 |

---

### F-05 · `payload 模型 75` 为过期副本（实测 77）

| 字段 | 内容 |
|---|---|
| **ID** | F-05 |
| **Severity** | **P2** |
| **Category** | V-5 Claim Drift（stale）· 错误方向：**understated** |
| **Description** | 同一事实的两个副本都停留在 75，实测 77（与 `EVENT_TYPES` 同步增长）。典型「**一处改了、另一处没改**」。 |
| **Evidence** | `pyharness/events/payload.py:4` → `77 事件词表(75 payload 模型)`<br>`LIMITATIONS.md:19` → `payload 模型 **75**`<br>实测：`EVENT_TYPES = 77` · 顶层 pydantic 事件负载类 **77**（扣除 `BaseModel` / `_PayloadBase` 两个基类） |
| **Detection Method** | 反射枚举 `pyharness.events.payload` 顶层类 + 与两处副本比对 |
| **detect:** | `python -c "from pyharness.events import payload as p; from pydantic import BaseModel; import inspect; n=len([x for x in vars(p).values() if inspect.isclass(x) and issubclass(x,BaseModel) and x.__name__ not in ('BaseModel','_PayloadBase')]); print(n)"` → 当前 `77`（声明 75） |
| **Runtime Impact** | 无功能影响（understated，不误导消费者）。 |
| **Evidence Level** | **L3**（运行测量） |
| **Recommendation** | 两处副本同步为 77（属 A-1 类：下调/校正声明以匹配实现）。 |

---

### F-06 · `LIMITATIONS.md` L-9「无 LICENSE」已过期（LICENSE 已存在于 HEAD）

| 字段 | 内容 |
|---|---|
| **ID** | F-06 |
| **Severity** | **P1** |
| **Category** | V-5 Claim Drift（publish-state / stale） |
| **Description** | `LIMITATIONS.md` 自述为「**当前状态的单一权威入口**」，其 §2 限制表 L-9 记「**无 LICENSE** ｜ 公开使用授权不明确 ｜ 待定」。实测：仓库根存在 `LICENSE`（**MIT**，`Copyright (c) 2026 zinuotiger`），且由 `cbb2033` 引入 —— 该 commit **正是 HEAD 且等于 `origin/main`**。同时该文件自身**内部不一致**：文首快照标 `3ef7000`，正文（:31, :68）却标 `origin/main = d3597a6`。 |
| **Evidence** | `HEAD ... LICENSE` 文件存在，首行 `MIT License`<br>`git log --oneline --diff-filter=A -- LICENSE` → `cbb2033 docs: ... add MIT license`<br>`LIMITATIONS.md:43`（L-9 行）<br>`LIMITATIONS.md:8`（快照 `3ef7000`）vs `:31,:68`（`d3597a6`） |
| **Detection Method** | 声明副本 vs 文件系统实存 + `git log --diff-filter=A` 定位引入 commit |
| **detect:** | `test -f LICENSE && grep -q "无 LICENSE" LIMITATIONS.md` → 当前**同时成立**（问题存在时返回 0） |
| **Runtime Impact** | 权威"当前状态"文件对公开发布面给出错误信息。方向为 **understated**（少报能力），不误导消费者 —— 但**恰好违背该文件的核心用途**（对外声明真实授权状态），且是**发布后自动失效**的一类（`cbb2033` 一落地 L-9 即假）。 |
| **Evidence Level** | **L3**（文件系统 + git 证据） |
| **Recommendation** | 删除 L-9，或在 §2 表内将其标为「**已解决**（`cbb2033` 引入 MIT）」；并同步修正文内 `origin/main` 与文首快照 commit 的三处不一致。 |

---

### F-07 · `origin/main = d3597a6` 为过期发布态声明（实际 `cbb2033`）

| 字段 | 内容 |
|---|---|
| **ID** | F-07 |
| **Severity** | **P1** |
| **Category** | V-5 Claim Drift（publish-state）· 同一数字 2 处副本 |
| **Description** | `LIMITATIONS.md` 两处声明修复「**已随 `d3597a6` 发布**（`origin/main` = `d3597a6`）」。实测 `origin/main = cbb2033`（`d3597a6` 之后又有 1 个 commit `cbb2033`）。属"**写下时为真、发布后转假**"的一类，是 Phase 8 发布闸的前置项 4（publish-state claim sync）未覆盖到的残留。 |
| **Evidence** | `git rev-parse --short origin/main` → `cbb2033`<br>`git rev-list --left-right --count origin/main...HEAD` → `0  0`<br>`LIMITATIONS.md:31` 与 `:68`（两处副本） |
| **Detection Method** | 逐处提取 pin 值 + 与实际 remote 比对 |
| **detect:** | `git rev-parse --short origin/main \| grep -q d3597a6` → 当前**不匹配**（声明与实测分歧） |
| **Runtime Impact** | 读者对「已发布版本包含哪些修复」的判定错误。 |
| **Evidence Level** | **L3** |
| **Recommendation** | 两处副本同步为 `cbb2033`；建议把该 pin 改为**指向常量而非硬编码**（或在发布流程内自动刷新），以消除该副本类缺陷。 |

---

### F-08 · `CODE-MATRIX.md` 标称「当前 RC」已落后 HEAD 5 个 commit

| 字段 | 内容 |
|---|---|
| **ID** | F-08 |
| **Severity** | **P2** |
| **Category** | V-5 Claim Drift（stale） |
| **Description** | `CODE-MATRIX.md:4` 声明「总览**已按当前 RC 更新**：`e561712`」，并在表头标注「**当前(RC e561712 @ 2026-09-17)**」。实测 HEAD = `cbb2033`，`e561712..HEAD` = **5 个 commit**；表内「git 72 次提交」实测 **77**。文档**部分**自认历史快照（阶段 0~6 章节），但**总览表头明确标注为"当前"**，故构成对该表有效性的过期陈述。 |
| **Evidence** | `git rev-list --count e561712..HEAD` → `5`<br>`git rev-list --count HEAD` → `77`（表内 72）<br>`CODE-MATRIX.md:4,13` |
| **Detection Method** | commit 距离测量 + 计数复核 |
| **detect:** | `git rev-list --count e561712..HEAD` → 期望 `0`（当前 `5`） |
| **Runtime Impact** | 无功能影响；影响对"当前 RC 修复状态"表的信任度。 |
| **Evidence Level** | **L3** |
| **Recommendation** | 表头"当前"二字改为具体 commit 并注明"表内数据截至该 commit"，或整体刷新到 `cbb2033`。 |

---

### F-09 · 4 个探针脚本含字面占位路径，任何检出上都无法运行

| 字段 | 内容 |
|---|---|
| **ID** | F-09 |
| **Severity** | **P2** |
| **Category** | V-4 Evidence Gap（证据工件完整性） |
| **Description** | 4 个 `scripts/` 探针把 `sys.path` 硬编码为**字面占位串** `C:/Users/<user>/Desktop/mini-harness`。该路径**在任何机器上都不存在**（含本机），脚本在 `import pyharness` 处必然失败 ⇒ **无法作为可重复证据**。形态上属脱敏动作引入的**回归**：占位符替换掉了真实用户名，同时破坏了可执行性。 |
| **Evidence** | `scripts/probe_engine_tools.py:4`<br>`scripts/probe_multisession.py:4`<br>`scripts/probe_scene3_roundA.py:4`<br>`scripts/probe_tools_call.py:4`<br>（另 `scripts/demo_phase1.py:74` 有 `<user>` 出现在**示例参数**中，非 import 路径） |
| **Detection Method** | 全 `scripts/` 扫描绝对路径 + 识别 `<user>` 占位符 |
| **detect:** | `grep -rln "<user>" scripts/*.py \| wc -l` → 期望 `0`（当前 `5`，其中 4 处为 import 路径） |
| **Runtime Impact** | 无运行时影响（探针非产品代码）。但 README:48 以「补了**可重复**的真实探针」为该轮修复的凭证 —— 其中 4 个**不可重复**。 |
| **Evidence Level** | **L3**（文件内容 + 路径不存在性） |
| **Recommendation** | 改为相对路径（`Path(__file__).resolve().parents[1]`），与仓库其余探针（如 `probe_mcp_stdio.py`）保持一致。**该修复与 README:56-58 所列、确实可跑的探针无冲突**，本轮未发现 README 显式点名的探针损坏。 |

---

### F-10 · `ToolExecutor._reject` 为死代码（S3-2-2 迁移残留）

| 字段 | 内容 |
|---|---|
| **ID** | F-10 |
| **Severity** | **P2** |
| **Category** | V-2 Missing Runtime Path（残留路径） |
| **Description** | `_reject()`（`tools_executor.py:640`）自述为「终局拒（executor 侧，如 scope-hidden）」，模块 docstring（`:52`）仍把它列为四关之一的有效路径。实测 `self._reject(` 调用点 **0**。根因：S3-2-2 把 scope 前置的运行时所有权上提到 `GuardChain._evaluate_full`（`execute()` 内注释与 `context.py:91-93` 均记载此次职责上提），executor 侧拒绝对应实现**已失去调用者但未删除**。`GOVERNED_AGENT_RUNTIME_DESIGN.md §4.2` 对同类残留（`paused/resume`）明文要求「**不得留死代码**（实现或删除皆可）」。 |
| **Evidence** | `grep -rn "self\._reject(" --include=*.py pyharness/ tests/ \| wc -l` → **0**<br>`tools_executor.py:52`（docstring 仍列为有效路径）<br>对照：同类名 `plan_mode.plan_reject` / `Decision.is_terminal_reject` 均无关 |
| **Detection Method** | 私有方法全库调用点计数 |
| **detect:** | `grep -rn "self\._reject(" --include=*.py pyharness/ \| wc -l` → 期望 `>0`（当前 `0`） |
| **Runtime Impact** | 无行为影响（不可达）。危害是**读者据 docstring 误判存在第二条拒绝路径**，与 §2.1 实测的"单漏斗"结论冲突。 |
| **Evidence Level** | **L1**（代码存在）**→ 无 L2/L3**（不可达） |
| **Recommendation** | 删除该 20 行并同步修正 `:52` docstring；或若确需保留 executor 侧终局拒语义，接线并补 `decision.issued`（否则会破坏 F-01 所依赖的"`decision_id` 贯穿"不变量）。 |

---

### F-11 · `governance/context.py` 模块 docstring 两处自相矛盾

| 字段 | 内容 |
|---|---|
| **ID** | F-11 |
| **Severity** | **P3** |
| **Category** | V-5 Claim Drift（in-source, stale） |
| **Description** | 该文件 docstring 有两处与**同文件代码**直接矛盾。 |
| **Evidence** | ① `context.py:22`：`本阶段**不发射** decision.issued(事件注册/载荷/executor 两出口接线属 S3-2-2)` —— 同文件 `:125` 即 `await self._emit_decision_issued(...)`，`:134-164` 为其完整实现。<br>② `context.py:48`：`"""治理层上下文(policy + decisions 已接线;receipts/evidence/audit 仍为形状占位)。"""` —— 同文件 `:51-55` 的五字段注释均标"已接线"，`:129` 分支仅在 `receipts is None` 时降级，`engine.py:669-671` 三件全部注入。 |
| **Detection Method** | docstring 断言 vs 同文件/装配层事实比对 |
| **detect:** | `grep -n "本阶段\*\*不发射\*\*\|仍为形状占位" pyharness/governance/context.py` → 当前 2 命中（问题存在） |
| **Runtime Impact** | 无运行时影响。属"**注释 ≠ 事实**"的引导性风险：读者按 docstring 会**低估**已接线范围，与 F-01/F-02 的"高估"方向相反，二者叠加使该模块的可信边界难以从注释判断。 |
| **Evidence Level** | **L1** |
| **Recommendation** | 属 A-2 类（修正注释事实错误）：删除两处过期表述。 |

---

### F-12 · `engine.py` `_build_governance` docstring 描述的运行链已被 S3 取代

| 字段 | 内容 |
|---|---|
| **ID** | F-12 |
| **Severity** | **P3** |
| **Category** | V-5 Claim Drift（in-source, stale） |
| **Description** | `engine.py:440-442` 称：「治理层只进**装配链**、不进运行链：**运行期仍是 `ctx.guard.evaluate`**（tools_executor 关 2b 调用），治理层无执行权……**运行期向治理层问询（authorize）属 S3**」。实测：运行期关 2 为 `ctx.governance.authorize`（`tools_executor.py:476`），其内部调用 `chain.evaluate_detailed`；`ctx.guard.evaluate` 在 `pyharness/` 中**仅出现在注释/docstring**，无调用点。「属 S3」的将来时表述在 S3 已交付后失去意义。 |
| **Evidence** | `tools_executor.py:476` `d = await ctx.governance.authorize(call, ctx, inputs_digest=digest)`<br>`governance/context.py:117`（`evaluate_detailed` 唯一生产调用点）<br>`engine.py:440-442` |
| **Detection Method** | docstring 断言 vs executor 实际调用 + 调用点计数 |
| **detect:** | `grep -rn "ctx\.guard\.evaluate(" --include=*.py pyharness/ \| wc -l` → 期望 `>0`（当前 `0`，仅注释命中） |
| **Runtime Impact** | 无。该句陈述的**结论**（"治理层无执行权"）仍为真，仅路径描述过期。 |
| **Evidence Level** | **L1** |
| **Recommendation** | 属 A-2 类：把该段改为"运行期关 2 经 `ctx.governance.authorize`（S3-2-2 起）"。 |

---

### F-13 · 「5 条架构不变量（INV-01~05）」与权威注册表 INV-01~09 的编号面不一致

| 字段 | 内容 |
|---|---|
| **ID** | F-13 |
| **Severity** | **P2** |
| **Category** | V-5 Claim Drift（scope overreach） |
| **Description** | `docs/INVARIANT_REGISTRY.md` 自述为「`INV-01`~`INV-09` 的**唯一权威定义来源**」，且规定"后续一切引用本组编号的产物均以本 Registry 为准"。README:11 与 `LIMITATIONS.md:20` 均把断言面表述为 **INV-01~05**；而 `pyproject.toml` 的 pytest marker 写「全库不变量测试(**INV-01~09**)」；`CODE-MATRIX.md:49` 写「**INV-01~09** 不变量测试全绿」。同一编号面在库中**三处口径**，且**均在当前代码/文档内**（非历史快照）。<br>注册表自身另声明：**九条 ID 当前全部存在覆盖缺口**，且**19 处历史引用待迁移**（`Status = pending authorization`）。 |
| **Evidence** | `docs/INVARIANT_REGISTRY.md:3,13,48`（INV-01~09 = 权威；九条全有覆盖缺口）<br>`README.md:11` · `LIMITATIONS.md:20`（INV-01~05）<br>`pyproject.toml`（`markers` → `INV-01~09`）· `CODE-MATRIX.md:49`<br>实测 `tests/invariants/test_inv_core.py` 内含 INV-01..INV-05（40 函数 / 48 用例） |
| **Detection Method** | 编号面三处口径比对 + 注册表权威声明核对 |
| **detect:** | 比对 README/LIMITATIONS 的 `INV-01~05` 与 `pyproject.toml` 的 `INV-01~09` → 当前**分歧** |
| **Runtime Impact** | 无。但**注册表是自述权威**，其迁移状态为 `migrated` + `pending authorization` ⇒ 该分歧**已被作者知道且未闭合**，属未完成迁移而非疏漏。README 的 5 条口径**可能是有意的子集表述**（"架构不变量"），但库中无该子集的显式定义，读者无法判定。 |
| **Evidence Level** | **L1/L3 混合**（文件比对 + 测试收集实测） |
| **Recommendation** | 二选一：① README/LIMITATIONS 改用注册表的 Canonical ID 口径并注明子集来源；② 在注册表内显式定义"架构不变量子集 = INV-01~05"。**不宜**让三处口径继续并存。 |

---

### F-14 · `tests/security` 不存在、`tests/acceptance`/`tests/e2e` 为空壳（已披露）

| 字段 | 内容 |
|---|---|
| **ID** | F-14 |
| **Severity** | **P2** |
| **Category** | V-2 Missing Runtime Path（验证面缺口） |
| **Description** | `INVARIANT_REGISTRY.md §0` 要求 `tests/acceptance/`、`tests/security/` 承载**标注 Canonical ID** 的用例。实测：`tests/security` **目录不存在**；`tests/acceptance/`、`tests/e2e/` 各仅含一个**空 `__init__.py`**。`GOVERNED_AGENT_RUNTIME_DESIGN.md:600`（M9 必做项）要求「至少各 1 条真实用例（含"批准 A 执行 B"必须被拒）」。`LIMITATIONS.md:41`（L-7）已如实披露为「空壳 / 缺失」。 |
| **Evidence** | `ls tests/security` → `No such file or directory`<br>`tests/acceptance/` → 仅 `__init__.py`（0 字节）<br>`tests/e2e/` → 仅 `__init__.py`（0 字节）<br>`INVARIANT_REGISTRY.md §0`（要求表）· `LIMITATIONS.md:41` |
| **Detection Method** | 目录存在性 + 内容清单 |
| **detect:** | `test -d tests/security && ls tests/security/*.py` → 当前**目录不存在** |
| **Runtime Impact** | 无端到端/安全自动回归（L-7 自述）。属**已披露**缺口，风险在"注册表要求 vs 实际目录"的**内部状态不一致**（C8 面）。 |
| **Evidence Level** | **L3** |
| **Recommendation** | 已披露，优先级低。但注册表 §0 的要求与实存目录矛盾，建议在注册表内同步登记该缺口的当前状态。 |

---

### F-15 · 未检查项（No Silence）

| 项 | 状态 |
|---|---|
| `https://zinuotiger.github.io/pyharness/architecture.html` 在线可达性（README:38-40 的发布态声明） | **未检查** —— 环境网络策略阻断 WebFetch，无法验证 404 / 内容一致性 |
| `docs/architecture.svg` / `.png` 与代码现状的一致性 | **未检查** —— 本轮未做图像与架构的逐项比对 |
| 真实 LLM / MCP stdio / Bing RSS / 流式 chunk 探针（README:56-58 声明 PASS） | **未检查** —— 需外部 key 与网络；本轮未复跑 |
| `desktop_native/`（937 语句 / 0% 覆盖） | **未检查** —— 本机缺 PySide6，无法收集（与 L-6 一致） |
| 覆盖率 79% 的分母口径合理性 | **未检查** —— 仅复测了汇总值与 `desktop_native` 的 0% |
| mutation 反证（README:12「14 次 mutation 反证测试鉴别力」） | **未检查** —— 属 S6-2b 阶段记录，未复跑 |

---

## 4. Findings Registry（汇总）

### 4.1 数量与分级

| 级别 | 数量 | 说明 |
|---|---|---|
| **P0** | **0** | **判定依据**：未发现（a）被声明提供且运行不可达**且会让消费者得到错误结论**的能力；（b）公开发布面上**超越实现**的定量声明（核验过的数字全部准确）；（c）治理/权限边界存在**可触发**的旁路（关 2 为单漏斗，实测 `evaluate_detailed` 仅 1 个生产调用者）；（d）公共工件含敏感或不可逆错误。F-02/F-03 接近 P0，因**方向为"少报能力"或"归属降级而非放行"**，判定为 P1。 |
| **P1** | 5 | F-01 · F-02 · F-03 · F-06 · F-07 |
| **P2** | 7 | F-04 · F-05 · F-08 · F-09 · F-10 · F-13 · F-14 |
| **P3** | 2 | F-11 · F-12 |

> 合计 **14 条已登记 Finding**（0 + 5 + 7 + 2）+ 1 条未检查清单（F-15）。

### 4.2 登记表

| ID | 级别 | 错误方向 | 类别 | 状态机状态 | Evidence Level | 是否已披露 |
|---|---|---|---|---|---|---|
| F-01 | P1 | overstated | Missing Runtime Path / Governance Gap | `AUDITED` | L2 | **否**（§1 隐性，§2 无条目） |
| F-02 | P1 | overstated | False Completion / Evidence Gap | `AUDITED` | L1 | **否** |
| F-03 | P1 | overstated | Governance Gap | `AUDITED` | **L3** | **否** |
| F-04 | P2 | neutral | Missing Runtime Path | `AUDITED` | L2 | **部分**（§1 括注） |
| F-05 | P2 | understated | Claim Drift（stale） | `AUDITED` | **L3** | 否 |
| F-06 | P1 | understated | Claim Drift（publish-state） | `AUDITED` | **L3** | 否 |
| F-07 | P1 | neutral | Claim Drift（publish-state） | `AUDITED` | **L3** | 否 |
| F-08 | P2 | understated | Claim Drift（stale） | `AUDITED` | **L3** | 否 |
| F-09 | P2 | neutral | Evidence Gap | `AUDITED` | **L3** | 否 |
| F-10 | P2 | neutral | Missing Runtime Path（残留） | `AUDITED` | L1 | 否 |
| F-11 | P3 | understated | Claim Drift（in-source） | `AUDITED` | L1 | 否 |
| F-12 | P3 | understated | Claim Drift（in-source） | `AUDITED` | L1 | 否 |
| F-13 | P2 | neutral | Claim Drift（scope） | `AUDITED` | L1/L3 | **是**（注册表自述待迁移） |
| F-14 | P2 | neutral | Missing Runtime Path | `AUDITED` | **L3** | **是**（L-7） |

**全部 14 条均已达 `AUDITED`**（具备可重复 `detect:` 命令 + 分级 + 边界陈述）。**无一条进入 `PLANNED` 或之后** —— 本轮为纯 M1，未授权任何修复，状态机按设计停在 `AUDITED`。

---

## 5. 未闭环能力列表（Unclosed Capabilities）

按「**C1 存在 / C2 可达 / C3 已验证 / C4 有证据 / C5 边界受控 / C6 回归通过 / C7 声明准确 / C8 状态一致**」逐条评估治理层五构件：

| 构件 | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | 判定 |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| `PolicyEngine` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Closed**（`policy.updated` L3 实测 1 条） |
| `DecisionEngine` + `authorize()` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **Open(C8)** —— F-03 主体归属失真 |
| `ReceiptStore.emit`（写面） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | **Open(C7)** —— F-02；F-03 经 principal 进入 `content_hash` |
| `ReceiptStore.verify` / `verify_chain`（核验面） | ✅ | ❌ | ❌ | ❌ | — | — | ❌ | — | **Open(C2)** —— F-02 |
| `EvidenceCollector`（订阅面） | ✅ | ✅ | ⚠️ | ❌ | — | — | ⚠️ | — | **Open(C4)** —— F-04（索引恒空） |
| `EvidenceCollector.archive`（写面） | ✅ | ❌ | ❌ | ❌ | — | — | ⚠️ | — | **Open(C2)** —— F-04 |
| `AuditSystem`（全部） | ✅ | ❌ | ❌ | ❌ | — | — | ❌ | — | **Open(C2)** —— F-01 |
| `ToolExecutor._reject` | ✅ | ❌ | ❌ | ❌ | — | — | ❌ | — | **Open(C2)** —— F-10（死代码） |

> **C2 是断层线**：五构件全部通过 C1（存在），四个在 C2（可达）断开。这正是本方法论 §Core Check 1 所针对的形态 —— `Exists ≠ Reachable`。

---

## 6. Runtime Evidence Gap 列表

按"**声称具备运行证据 / 实际只有 L1 或 L2**"列出：

| # | 声明 | 声明载体 | 声称的证据形式 | 实测证据等级 | 缺口 |
|---|---|---|---|---|---|
| **E-1** | 审计可"还原一次拒绝" | `GOVERNED_AGENT_RUNTIME_DESIGN.md:589`（M7）、README:74「审计 ✅」 | 隐含 L3 | **L2** | 无调用路径；README 的 ✅ 由**另一机制**（旧遥测）满足 |
| **E-2** | 凭证"**可独立校验**" | README:11 | 隐含 L3 | **L1** | 校验器存在且有测试，**无运行面**；`verify_chain` 未导出 |
| **E-3** | 治理留痕可答"**谁**" | README:11、`context.py:62-79` | 隐含 L3 | **L1→L3（但结论为 SYSTEM）** | 桌面壳不设 `ctx.channel` ⇒ 归属降级（**这一条有 L3，L3 证明的是缺陷本身**） |
| **E-4** | 按 task 聚合"完整证据链" | `GOVERNED_AGENT_RUNTIME_DESIGN.md:588`（M6） | 隐含 L3 | **L2** | 订阅已接、无生产者 ⇒ 索引恒空 |
| **E-5** | 4 个探针"可重复" | README:48「补了**可重复**的真实探针」 | L3 | **不可运行** | 字面占位路径（F-09） |
| **E-6** | `origin/main` 发布态 | `LIMITATIONS.md:31,68` | — | **与实际不符** | F-07 |
| **E-7** | L-9「无 LICENSE」 | `LIMITATIONS.md:43` | — | **与实际不符** | F-06 |

> **对照组（取得 L3、声明成立）**：`authorize()` 唯一入口（2 处）· guard 链 7 条 · `decision.issued` 1:1 · `receipt.emitted` 放行/拒绝各 1 · 拒绝调用无 `tool.result` · `policy.updated` 恰 1 条 · 事件词表 77/14/3 · CLI 16 子命令 · 1,749 passed / 2 skipped / 79%。

---

## 7. 下一阶段建议（M2 起点，**非本轮执行**）

> 本轮为纯 M1。以下**仅为建议**，不构成授权。任何修复须经用户对具体 Fix Plan 的显式授权方可进入 M3。

### 7.1 建议的处理顺序

| 序 | 范围 | 依据 | 备注 |
|---|---|---|---|
| **1** | **F-03**（桌面 `ctx.channel` 归属） | 唯一具备 L3 的 P1；一次装配层赋值即可；**触及主体归属 ⇒ 同时影响 `content_hash`** | 按 M3 策略应落入 **C-4/C-6 面**（并发/状态归属；验证判据需可命令化），不应作为 auto 项 |
| **2** | **F-02 的最小闭环**（`verify_chain` 补进 `__all__`） | 成本最低、方向明确；或改为**下调 README 措辞** | 补导出属 **C-1**（新增行为面）；降措辞属 **A-1** |
| **3** | **F-06 / F-07**（发布态同步） | `LIMITATIONS.md` 是自述权威入口，当前给出错误发布信息 | 均属 **A-1/A-2** 面 |
| **4** | **F-01**（审计面决策） | 需**先做二选一**：补运行面 or 登记为限制 | 补运行面属 **C-1**；**不得**在未决策状态下继续悬空 |
| **5** | **F-04**（`LIMITATIONS §2` 登记） | 补一行登记即可 | **A-4** 面 |
| **6** | F-05 · F-08 · F-09 · F-10 · F-11 · F-12 · F-13 | 数值副本同步 / 死代码清除 / 注释校正 | 多为 **A-1/A-2/A-4** |

### 7.2 本轮**明确不建议**的做法

- **不建议**把 F-01 / F-02 合并为"补一整套审计/核验外壳"的大改动。这两条的最小处置是**二选一**（补最小面 **或** 下调声明），不是新建子系统 —— 后者会把 M2 范围推到 M10（治理 Demo）的量级。
- **不建议**在修 F-03 时顺带重构 `principal_of()`。该函数的语义已被 R1 裁定 + 测试守卫钉死（`context.py:16-20`），当前缺陷**只在装配层**（谁赋值 `ctx.channel`），不在语义层。

### 7.3 对"是否可进入 M2"的判定

**可以进入 M2**（M1 → M2 的全部前置条件已满足）：

- ✅ 审计报告与 Gap Register 已交付（本文件 §3 / §4）
- ✅ **每条 finding 均带可重复 `detect:` 命令**（§3 逐条）
- ✅ 用户已可查看（本文件）
- ⚠️ **未满足**：用户尚未就任何具体 Fix Plan 给出授权 ⇒ **M2 → M3 不可进入**

---

## 8. 关于本报告的写入（模式声明）

本次任务指令要求生成 `Functional_Runtime_Audit_PyHarness_v2.0_Report.md`。按 Functional Runtime Audit v2.0 的 Output contract，**M1 的产物默认以消息内容交付，写盘属文件编辑、需 M3 授权**。此处按用户对**具名工件**的显式指令写入该文件，并**同时**在本节记录该偏离，以免与技能纪律混同。

**本轮除此之外未修改任何文件**，未执行任何 git 写操作。所有运行时探针均在系统临时目录（`tempfile.mkdtemp`）内建立会话，未污染仓库会话目录。

---

## 9. 审计完整性声明

| 维度 | 覆盖 |
|---|---|
| 静态读取 | ✅ `pyharness/` 85 文件结构 · 治理层 6 文件全文 · README/LIMITATIONS/CODE-MATRIX/注册表/设计文档关键节 |
| 运行时执行 | ✅ 真实装配引擎 ×2 场景 · 真实工具管道（放行 + 拒绝） · 真实任务队列驱动 · 全量 pytest |
| 定量复测 | ✅ 7 项（模块/测试/事件词表/子命令/覆盖率/commit/guard 链） |
| 未覆盖 | ❌ 见 F-15（6 项） |

> **结论先行、事实与推断分离、未检查项不静默** —— 已按 Output contract 的报告级硬要求逐条执行。
> 本轮**发现即停**，等待人工 Review。
