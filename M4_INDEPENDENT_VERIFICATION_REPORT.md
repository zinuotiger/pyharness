# M4 Independent Acceptance Verification Report

> **模式**：M4 Verification（独立验收）。**未修改任何生产代码、未 commit、未 push、未自行修复。**
> **方法学立场**：我是上一轮修复的作者，故**自审不构成独立性**。本轮把 GAP-7/8 与 GAP-9/10/11 的深度
> 追踪交给**两个未参与修复的独立 agent**（只有代码、没有我的结论），我本人负责基线复跑、GAP-1、
> GAP-13、变异证伪与汇聚。凡 agent 结论均经我**独立复现或直接读码验证**后才采信。
> **本轮只写了三类文件**：临时探针（`%TEMP%\m4-probe\`）、变异副本（`%TEMP%\m4-mut\`）、本报告。

---

## Executive Summary（执行摘要）

> **上一轮的修复，大部分成立；但有 1 项被证伪、1 项部分成立，另有 8 项新缺口被独立发现。**
>
> - **基线逐字复现**：1944 / 0 failed / 0 errors / 4 skipped / 79% —— 无漂移。
> - **GAP-7 / GAP-10 独立确认为 CLOSED**；**GAP-1 / GAP-11 的"闭合"结论在收窄后成立**。
> - **GAP-8 被证伪（PARTIALLY → REOPENED）**：我上一轮写的**冻结规则 #2 在生产不成立** ——
>   run 抛错/被取消时，一个已含 `decision.issued` + `receipt.emitted` + `tool.result` 的任务段
>   **产出 0 条证据**。我独立复现：正常 run → 证据 1 条；`loop.wake` 抛错 → **0 条**。
> - **GAP-13 的定时器本体成立**（启动/幂等/关闭/异常存活/不挂死/并发无重复无丢失），
>   但其**配置面被证伪**：`log.jsonl.flush_interval_s` / `flush_batch` 是**完全未接线**的死配置
>   （实测：配置要 3.0/7，store 实际 0.5/64）。
> - **GAP-3 / GAP-6 维持 Environment Blocked**，不接受"YAML 正确 = CI 已验证"。
> - **文档漂移**：分层测试计数在我自己的报告与 LIMITATIONS 中**双双写错**（acceptance 7→**9**，e2e 13→**18**）。
> - **变异证伪 7/7 RED**：本轮新增的不变量测试**确有鉴别力**，不是假绿。
> - **负向验证 3× 稳定**：NT-1~NT-4 逐次一致；**NT-5 三次全部 `APR-503`**（确定性 fail-closed）。

**一句话**：上一轮的修复**在主干上站得住**，但**"闭合"的边界比上一轮报告宣称的更窄** ——
证据面与非主链失败路径、以及配置面，是这次被独立审查打穿的三个点。

---

## Independent Verification（独立验证）

| 手段 | 本轮实际做了什么 | 覆盖 |
|---|---|---|
| 基线复跑 | 全量 + 逐层，独立执行 | 全部 |
| 独立 agent（无我上下文） | 两路：GAP-7/8；GAP-9/10/11（FALSIFY 口径） | 见下 |
| 我本人读码 + 运行时探针 | GAP-1 绕过面穷举、GAP-13 生命周期、D1 复现、GAP-NEW-01 实测 | 全部 |
| 变异证伪 | 临时副本注入 7 处单行违约 | 核心不变量 |
| 负向验证 | 原 5 条 × 3 次重复 | 全部 |

**agent 结论的采信规则**：凡我未能**独立复现或直接读码确认**的，标为"仅 agent 报告，未独立确认"。

---

## 1. Independent Baseline Check（独立基线检查）

```
声明   : 1944 collected / 0 failed / 0 errors / 4 skipped / 79%
实测   : 1944 collected / 0 failed / 0 errors / 4 skipped / 79%   ← 逐字一致,无漂移
耗时   : 73.9s(声明 75.6s,同一量级)
```

分层实测（本轮真实计数，用于 §13 漂移判定）：

| 目录 | 实测 | 上一轮声明 |
|---|---|---|
| `tests/security` | **49** | 49 ✅ |
| `tests/acceptance` | **9** | **7** ❌ |
| `tests/e2e` | **18** | **13** ❌ |
| `tests/invariants` | **82** | 82 ✅ |
| `tests/integration` | **6** | 6 ✅ |

**结果无变化，故无需调查差异。** 但分层计数暴露了文档漂移（见 §13）。

---

## 2. GAP-1 Independent Review（LLM 出口治理）

### A — `_chat_any` 是否唯一生产调用路径？

**是。** `LLMClient` 的**全部五个**公开出口都经它：
`chat`(llm.py:1017) · `chat_stream`(:1022) · `mini`(:1033) · `summarize`(:1044) · `json_chat`(:1060)。
`_egress_guard` 在 `_chat_any` **入口无条件调用**，早于 chain/直连分支。

### B — 是否存在绕过？

逐类穷举结果：

| 候选绕过面 | 实测结论 |
|---|---|
| direct adapter call | **无**。全库 `.chat(`/`.chat_stream(` 的生产命中只有 `llm_fallback.py:264`（在 `chat_with_fallback` 内，而该函数**只**由 `llm.py:960` 从 `_chat_any` 进入） |
| alternate client | 无。只有 `LLMClient` 一个门面 |
| `require_adapter()` | **零生产调用者**（仅定义 + `__all__` + 测试）→ 死导出（**GAP-NEW-08**, P3） |
| `build_client()` | 仅由适配器惰性传输调用（`llm.py:629`），位于适配器之后 ⇒ 已被本闸覆盖 |
| streaming bypass | 无。`chat_stream` 经同一 `_chat_any` |
| **error / retry bypass** | **无**。`_retry_adapter` 每次尝试前调 `BudgetGuard.check`；`chat_with_fallback` 每个适配器前再调一次 |
| test-only / background path | 后台（jobs/schedule/subagent）全部经 `agent_loop → ctx.llm.*` ⇒ 同一条路 |
| `ping` 健康探针 | **唯一不受闸者**，且是**显式登记的 Negative Space**（不产生 tokens/计量） |

### C — `_egress_guard` 实际治理的是什么？

**只治理 Budget（预算配额），不治理 Authorization / Policy / Tool Governance。**

依据：`_egress_guard` 唯一动作 = `ctx.scope.check_budget()`。而 `scope.budget_state()` 读
`self.limits`（= `BudgetLimits.from_cfg(cfg)` = **`cfg.budget.task`**）与共享 `counters`
——与 `BudgetGuard` **同限值、同计数器**。故它是"同一预算权威的**第二个入口**"，不是第二套账。

### D — 为什么不进 `governance.authorize()`？是否为方法论缺口？

**判定：Intentional Boundary（有意边界），不是 Methodology Gap。**

理由（本轮重新论证，非复述上一轮）：

1. `authorize()` 的**输入契约**是 `ToolCall`（工具名 + 参数集合），g1–g7 按**工具名前缀**匹配，
   产出的 `decision.issued` / `receipt.emitted` 以 `call_id` 串联 `tool.call` / `tool.result`。
2. LLM 请求**没有工具名、没有参数集合、没有 Provider 副作用** —— 三项都缺失。
3. 强行纳入只能伪造 `ToolCall`，其结果会**污染工具治理链的语义**：审计读侧
   （`denied_report` / `causal_chain`）会把"模型出网"当作"工具被拒"来渲染，
   `verify_receipt` 会为一次文本生成签发"执行凭证"。
4. 与 P1（决策权与执行权分离）无关：该原则约束的是"谁能执行"，而 LLM 出网**不是执行**。

**Runtime 证据（本轮新增，上一轮缺失）**：用**真实 Settings + 真实 `FallbackChain`** 重测五出口：

```
within-budget  (chain=False) → 5/5 REACHED_ADAPTER
over-budget    (chain=False) → 5/5 BLOCKED(BudgetExhausted) adapter_calls=0
within-budget  (chain=True)  → 5/5 REACHED_ADAPTER
over-budget    (chain=True)  → 5/5 BLOCKED(BudgetExhausted) adapter_calls=0
```

> **自我揭露**：我上一轮的 `tests/security/test_llm_egress.py` **只覆盖了 `chain=None` 的配置**，
> 而生产装配（`fallback_models` 非空）会挂链。本轮补测链上配置后才敢确认。

### E — LLM Governance Boundary（正式定义）

> **LLM 出网的治理边界 = `LLMClient._chat_any` 入口处的单一会话预算闸。**
>
> - **纳入治理**：五类公开出口（chat / chat_stream / mini / summarize / json_chat），
>   以及其下游的降级链与重试。判定动作 = 预算配额；拒绝形态 = `BudgetExhausted`（零出网）。
> - **明确不纳入（Negative Space）**：`OpenAICompatAdapter.ping` 及其健康探针调用 ——
>   不产生 tokens、不落 F029 计量，受预算约束会使"探测可用性"语义颠倒。
> - **明确不纳入（架构裁定）**：**任何**"LLM 调用有治理决策 / 有凭证"的主张。
>   本项目的 `decision` / `receipt` 语汇**专指工具授权**；LLM 出网只有**观测留痕**
>   （`llm.request` / `llm.response` / `llm.usage`），**没有授权语义**。
> - **残余**：`_egress_guard` 在 `ctx.scope` 缺失时**降级放行**（配额面非授权面，见 L-19）。
>
> **最终状态：Partially Closed（维持上一轮结论，但理由被本轮收窄并明确化）。**

---

## 3. GAP-7 Independent Review（AuditSystem 生产链路）

**独立 agent 全链追踪 + 我读码复核：CONFIRMED CLOSED。**

| 跳 | caller | callee |
|---|---|---|
| Web UI | `ui/index.html:957` | `GET /api/sessions/{sid}/governance-audit?reconcile=true` |
| 原生 UI | `desktop_native/main_window.py:868 refresh_audit` | `controller.governance_audit` |
| 路由注册 | `desktop/app.py:283-286` | `DesktopApp.governance_audit` |
| HTTP 处理 | `desktop/app.py:769-780` | `ApplicationService.governance_audit` |
| 原生控制 | `desktop_native/controller.py:163-169` | 同上 |
| 服务 | `application/service.py:585` | `public_spine_for`(:282-305) → `engine.build_runner_components`(:557) |
| 注入 | `engine.py:752-753` | `AuditSystem(session=log_, legacy_audit=session_audit)` |
| 实调 | `service.py:658/659/660` | `denied_report` / `reconcile` / `causal_chain` |
| 数据源 | `governance/audit.py:70-75 _events` | `receipt._read_events` → `session.events_after(0)`（append-only 日志） |

**可达性**：两个独立外壳（FastAPI 路由、Qt 原生）。唯一守卫 `audit is None → CYC-999` 是
fail-closed 且**永不触发**（`build_runner_components` 无条件构造并注入）。**无死守卫、无未挂载路由。**

**旧路径是否绕过？** `telemetry.session_audit` **仍可达**（`/api/sessions/{sid}/telemetry`、
原生 `controller.telemetry`），但这是**并存展示**，不是绕过：`governance_audit` **从不调用**
`legacy_session_audit`。判定：**additive，非 bypass。**

**证据等级**：链路可达性 = 读码追踪；数据源 = 独立 agent 实跑（`denied_report`/`causal_chain` 返回真实数据）。

---

## 4. GAP-8 Independent Review（Evidence 生产者）— **证伪**

**独立 agent 发现 + 我独立复现：上一轮的"Fully Closed"不成立。**

### 4.1 生产调用者存在（这部分成立）

```
Emitter : governance/evidence.py:196  append("evidence.archived")
   ← governance/evidence.py:159  archive()
   ← engine.py:1010              archive_task_evidence()
   ← engine.py:927               make_runner.run_for_task
   ← TaskQueue._run_runner       task_queue.py:285-299
观测：evidence.archived=1，refs=[segment "t-1:seg", decision_id …, receipt_id …]
      live indexed=1 == rebuilt=1；重复调用返回 None（幂等）；无决策段 0 归档
```

### 4.2 **D1 — 失败/取消路径零证据（P2, Overstated）** ← 我独立复现

`engine.py:915` `res = await loop.wake(env, ctx=ag_ctx)` 与 `:927` `archive_task_evidence(...)`
之间**没有 `finally`**。`loop.wake` 抛错（`CancelledError` / `BudgetExhausted` / 任何 `PyHError`）
⇒ 生产者**永不执行**。

我的独立复现（探针，非 agent 结果）：

| 场景 | decision.issued | receipt.emitted | tool.result | **evidence.archived** |
|---|---|---|---|---|
| 正常 run | 1 | 1 | 1 | **1** |
| `loop.wake` 抛错 | 1 | 1 | 1 | **0** |

**这直接违反我上一轮写下的冻结规则 #2**（`engine.py` docstring）：
"段内至少一条 `decision.issued` ⇒ 归档"。实际规则是
**"段内有决策 **且** run 未抛错 ⇒ 归档"** —— 未加限定语。

影响：证据覆盖是**快乐路径专属**。任何取消/预算耗尽/异常的 run **静默丢失其证据工件**。
（数据未丢——工件可由日志重导出（INV-E3）——故非数据损失，是**闭环主张被夸大**。）

### 4.3 **D2 — 子 Agent 会话零证据（P2/P3）** ← 我读码确认

`core/orchestration.py` 内 `EvidenceCollector` / `archive_task_evidence` / `evidence`
**零命中**。`child_session`(:141-165) 建孤立 child bus 只有 store sink；`run_child`(:167-177)
走 `_run_on_session`(:88-98)，**不开任务段、不调生产者**。
⇒ 子 Agent 的工具决策**永不归档**。**仅读码，未实跑复现。**

### 4.4 D3 — 生产者先于段关闭（P3）

`evidence.archived`(seq 21) < `segment.end`(seq 23)。今日无害（`_segment` 引用经 `segment.start`
解析、`_task_at` 视未闭合段为右开），但工件引用了**发出时尚未闭合**的段 —— 若将来消费者要求闭区间即脆弱。

### 4.5 "活索引 0 vs 日志重建 1" 的性质裁定

**判定：测试构造产物，不是生产竞态。** 依据（独立 agent 读码 + 我复核）：
生产顺序为 ① 构造并订阅 collector（`engine.py:742-746`）② 才写 `session.created`；
且 `session._dispatch` **await** `bus.emit(..., mode="sequential")`（`session.py:166-169`），
故 `on_event` 在 `append` 返回**之前**同步跑完。两条 spine 路径上**均无法复现 live=0**。
live=0 只出现在"collector 在归档事件之后才构造"的场景（即 `public_spine_for` 事后新建 spine 的测试路径）。

### 4.6 其他（agent 报告，我未独立复现）

- **D4（INFO）**：JobManager 自开段（`jobs.py:35`）无工件，但决策经内层 `t-N` 段窗口被捕获 ⇒ 无证据损失。
- schedule / plan 路径经同一 `run_for_task` ⇒ 已覆盖。

---

## 5. GAP-9 Independent Review（上下文契约）

**独立 agent 追踪 + 我读码确认：PARTIALLY。**

`call_id` 位置契约**在运行时成立**（实测 8 类事件）：

```
tool.call        trace=None             payload_call_id=c1
guard.evaluated  trace={'call_id':'c1'} payload_call_id=None
decision.issued  trace={'call_id':'c1'} payload_call_id=None
receipt.emitted  trace={'call_id':'c1'} payload_call_id=None
tool.result      trace=None             payload_call_id=c1
```

**必填/可选/不可变**：
- Envelope **schema 必填** = `seq, ts, type, session_id, actor`（`envelope.py:41-45`）。
- `call_id` / `decision_id` / `receipt_id` / `agent_id` / `tenant_id` **均非信封必填**（可选/语境性）；
  `call_id` 仅在 `tool.*` 载荷内必填。
- **不可变**：`Envelope` 为 frozen pydantic（`extra=forbid, frozen=True`）⇒ 信封字段（含 `session_id`/`tenant_id`）不可改。
  但 `call_id`/`decision_id`/`receipt_id` 位于 **payload/trace 字典内部**，其**内容**不受冻结保护
  —— 不可变性是"信封记录"的属性，不是"标识符值"的属性。

**新发现（GAP-NEW-04, P3）**：`approval.granted` / `approval.denied` / `approval.timeout` 的 `call_id`
**既不在 payload 也不在 trace**（payload=`{approval_id,by,ttl_ms}`，trace=`{"kind":"approval.verdict"}`）。
我上一轮的 `CALL_ID_IN_TRACE` 列表**未收录这三类**，故契约测试**通过**，而"治理面 → trace"的
**概括性陈述为假**。归约器靠 `approval_id → approval.requested` 反查补偿（`session.py:256-265`），
故审计仍可重建 —— 但契约描述不完整。

**为何没有 `request_id`/`conversation_id`/`trace_id`/`subject_id`/`user_id`？**
**判定：架构上的明确选择，不是"未完成"。** 依据：契约测试把它们的**缺席**写成了**可执行断言**
（`test_inv_context_and_paths.py::test_transport_identifiers_are_absent_by_contract`），
即"引入须走一次有意识的契约变更，测试会先变红"。这是**登记的负空间**，非遗漏。
（代价：跨请求/跨会话关联不可用 —— 已在 LIMITATIONS L-12 登记。）

**`agent_id`**：创建后**不进入任何事件**（`agent.py:463` 创建，仅用于注册表键/总线 owner/日志）。
这是**文档化**的（契约测试注释明写"不进入事件"），非静默缺失。

---

## 6. GAP-10 Independent Review（租户归属）

**独立 agent + 我读码：CONFIRMED。**

- **单一权威来源**：`Envelope.tenant_id`。`SessionLog.append` **无** `tenant_id` 形参 ⇒ 调用方
  **无法逐事件设定**；payload 模型**无一含** `tenant_id` ⇒ 无竞争来源。
- **A. 重放后是否保持不变？** 是。实测：`tenant-alpha` 会话 → 每条事件 `tenant-alpha`；
  不带租户重开 → `tenant-alpha`；带 `tenant-beta` 重开 → **`tenant-alpha`** + 告警。
- **B. 调用方能否篡改历史 tenant？** **不能。** 判定点在 `session.py:566-572`：`if logged_tenant is not None: log_.tenant_id = logged_tenant`（**日志优先**）。实测反例已确认。
- **C. Envelope 是否唯一权威？** 是。
- **D. 是否有事件路径绕过 Envelope？** **无。** 全库 `Envelope(` 直接构造**零处**；
  仅 `make_envelope`/`validate_envelope` 内构造，其余为 `model_validate_json`
  （`persistence.py:539,600`、`repair.py:162`）——均为**已落盘事件的反序列化**，保留原值。
- **无租户会话保持 `None`**（不捏造）—— 负向已验证。

---

## 7. GAP-11 Independent Review（主体归属）

**独立 agent + 我读码确认三态：CONFIRMED（治理层）；分层存在分歧。**

运行时实测（agent）：

```
[C1] channel='desktop' → principal_kind=human  principal_channel=desktop
[C2] channel=None      → principal_kind=system principal_channel=None
[C3] ctx 无 channel 属性 → RAISED ApprovalError code=APR-503
[C4] principal_of(channel=None) → kind=system id=system
[C5] ApplicationService(channel=None) → svc.channel='desktop'    ← 见 N6
```

**"是否还有别的路径把 missing channel 变成 system？"** —— 治理层**没有**：
`context.py:92` 的 `ch or _FRAMEWORK_BY` 只在 `:86` 的 `hasattr` 守卫**之后**可达。

**但发现三处分层分歧（我读码逐条确认）**：

| 编号 | 位置 | 事实 | 级别 |
|---|---|---|---|
| **N5** | `approval.py:602-615 _ensure_channel` | 读 `getattr(ctx,"channel",None)`；**属性缺失**时**回落 `self._channel`**，**不抛**。治理层对同一输入抛 `APR-503` ⇒ **两层规则矛盾** | **P2**（当前被调用顺序**遮蔽**：关 2 governance 先于关 2.5 approval） |
| **N6** | `engine.py:628-629` | `ApprovalProvider(..., channel="desktop")` **硬编码**。结合 N5 ⇒ 未声明通道的引擎，审批层认为"有 desktop 人类通道" | P3（被 N5 的顺序遮蔽） |
| **N7** | `service.py:112` | `str(channel or "desktop")` —— **显式 `None`（headless 意图）被静默提升为 `desktop`** | P3（当前无外壳传 None，潜伏） |

**结论**：上一轮"缺失通道全部 fail-closed"的说法**只对治理层成立**；
审批层与桌面服务层是**降级**而非 fail-closed。**Claim 过宽。**

---

## 8. GAP-13 Independent Review（定时刷新）— 本轮重点

全部经我本人运行时探针实测（真实引擎 + 真实 store）。

### A 启动 / 幂等 / 正常关闭

```
A_start_first           = true
A_start_second_idempotent = false      ← 幂等(第二次不起新任务)
A_task_running          = true
A_on_disk_immediately   = false        ← 非 SYNC 事件确实攒批
A_flushed_by_timer      = true         ← 定时器把它落了盘
A_cleared_after_close   = true         ← close 后 _flush_task/_flush_stop 归 None
A_task_done_after_close = true         ← 任务已终结,无悬挂
```

### B 定时 flush × 强同步 flush 并发

六种间隔配置（0.5s / 0.05s / 0.02s / 0.01s / **0.005s**）× 40 事件：

```
每轮: total_lines = 41 (=1 session.created + 40) ✓
      duplicate_lines = 0                       ✓
      missing = []                              ✓
```

> **自我揭露（差点报出假阳性）**：首轮探针报 `duplicated_marks: [1,2]`。追查后确认是
> **我自己的测量 bug** —— substring 计数：`CONC-1` 是 `CONC-10…CONC-19` 的子串。
> 决定性判据（总行数 + 整行去重）显示**完全正确**。**未产生任何重复写。**

**并发安全性依据（读码）**：`SessionStore.flush()` 函数体**全程无 `await`**（取批→改队列→写→刷）
⇒ 在事件循环内**原子**，两个并发 flush 无法交错；且强同步路径 `_flush_pending_all` 同样无内部 await。

### C 刷新异常是否杀死后台任务

```
C_injected_boom_count      = 1
C_timer_calls_after_boom   = 56      ← 注入失败后仍继续跳 56 次
C_timer_survived_exception = true
```

### D 是否产生重复写 / 丢失 / 丢任务 / 关闭挂死

注入强同步 flush 失败（第 2/3/5 次调用）三组，每组 40 事件：

```
每轮: total_lines = 41 (=期望) ✓   duplicate_lines = 0 ✓   missing = [] ✓
      sync_failures = 1            ← 失败如实上抛给调用方(契约行为)
D_close_seconds = 0.001            ← 间隔设为 5s,关闭仍立即(未等满)
D_no_hang = true
```

### E `flush_interval_s` 是否真正是运行时配置？→ **否**

**GAP-NEW-01（P2, Overstated）**：

```
配置想要     : interval=3.0  batch=7
store 实际   : interval=0.5  batch=64
E_wired      = false
```

穷证：
- `open_store(session_id, *, dir=None)`（`persistence.py:761-795`）**从不读 config**，
  构造 `SessionStore(...)` 时**不传** `flush_interval_s` / `flush_batch`。
- 全库 `SessionStore(` 生产构造点**仅 1 处**（`persistence.py:794`）。
- 全库（含 tests/scripts）**零处**书写 `flush_interval_s=` / `flush_batch=`。

⇒ `log.jsonl.flush_interval_s` / `flush_batch`（`config.py:696-699`，带 `ge/le` 校验）
是**完全未接线的死配置**：运维改了配置**不会有任何效果**，store 永远用 DEFAULTS（0.5 / 64）。

> **自我揭露**：我上一轮的 GAP-13 测试**直接在 store 对象上赋值** `store.flush_interval_s = 0.05`，
> **绕过了 config→store 这一跳**，因此从未验证过配置面。这正是 M4 独立审查的价值所在。

**GAP-13 最终判定**：定时器**本体 Fully Closed**；**配置面 REOPENED（GAP-NEW-01）**。

---

## 9. GAP-3 Native Shell（原生外壳）

**明确区分：本机真正闭合的是 Test Collection Boundary（测试收集边界），不是 Native UI Runtime Verification（原生 UI 运行时验证）。**

| 项 | 状态 |
|---|---|
| 默认 pytest 无需 `--ignore` | ✅ 闭合（实测：无 `--ignore` 跑通 1944） |
| 依赖缺失时**显式带理由 skip** | ✅ 闭合（`test_desktop_native.py` 1 skip；`test_shell_parity` 1 skip） |
| 不依赖 GUI 的外壳↔服务契约漂移检测 | ✅ 存在（`tests/integration/test_shell_service_contract.py`，AST，6 用例） |
| **`desktop_native/` UI 行为运行时验证** | ❌ **Environment Blocked** |

`desktop_native/` 覆盖实测仍为 **0%**（`main_window.py` 731 语句、`controller.py` 170、`app.py` 33）。

**要获得真实证据还需要什么**（不伪造成功）：
1. `.venv` 安装 `PySide6>=6.7` + `qasync>=0.27`（`pip install -e ".[native]"`）；
2. 且 `uv.lock` 对 native extra 补包条目（当前 lock 对 PySide6/qasync/pywinpty **零包条目** ⇒
   干净检出上 `uv sync --extra native` 无锁可依，即 LIMITATIONS 已登记的依赖面独立问题 D-1）；
3. 才能解除 `importorskip`，让 2 个已存在的原生壳用例真正执行。

**维持 Environment Blocked。**

---

## 10. GAP-6 CI Independent Review

静态检查（我本人执行）：

| 检查 | 结果 |
|---|---|
| YAML 语法 | ✅ 合法（`yaml.safe_load` 通过） |
| job 结构 | ✅ 2 job：`test`(7 steps) · `gates`(9 steps) |
| 安装依赖 | ✅ `pip install -e ".[dev]"`（含 PySide6） |
| PySide6 处理 | ⚠ **未实测**。CI 上装 `.[dev]` 后应真跑；但 `uv.lock` 零 native 包（D-1）不影响 pip 直装 —— **未验证** |
| coverage threshold | ✅ `COV_FLOOR=75`，实测 79% ⇒ 有余量（不得为凑绿下调） |
| security 被执行 | ✅ `gates` job 显式跑 `tests/security` |
| acceptance 被执行 | ✅ 显式跑 `tests/acceptance` |
| e2e 被执行 | ✅ 显式跑 `tests/e2e` |
| static gates 被执行 | ✅ AST 判定 `authorize()` 恰 1 处 + 治理读侧符号存在性；**两条我均在本地实跑通过** |

**关键判断：本地通过 ≠ CI 已验证。** 该工作流**从未在 GitHub Actions 上执行过**（未 push）。
按技能口径当前状态为 `IMPLEMENTED`，**不是 `VERIFIED`**。

**维持 Environment Blocked。不接受"YAML 正确 ⇒ Fully Closed"。**

---

## 11. Negative Verification（负向验证）

原 5 条，**×3 次重复**（验证稳定性）：

| # | 测试 | Expected | Actual（3 次一致） | 判定 |
|---|---|---|---|---|
| NT-1 | governance bypass | 缺 governance/guard/session ⇒ 拒绝；10 个禁放行 API 全 AttributeError | `no_governance_blocked=True` `no_guard_blocked=True` `no_session_blocked=True` `forbidden_api_all_absent=True` | ✅ PASS ×3 |
| NT-2 | deny → execute | 拒绝零副作用 + 重放 `GRD-402` | `first_ok=False` `replay_blocked=True` `replay_code=GRD-402` `escaped_exists_after_replay=False` | ✅ PASS ×3 |
| NT-3 | missing fact | 三类对账全中 + 因果链缺环 + `emit=True` 写 `syscheck.fail` | `NO-GUARD-EVENT:call_id=x1` / `SEQ-GAP:3,4` / `emit_true_events=[…,"syscheck.fail"]` | ✅ PASS ×3 |
| NT-4 | parameter tampering | `GRD-403` + 文件未变 | `file_unchanged=True`（`ORIGINAL`） | ✅ PASS ×3 |
| **NT-5** | **context loss** | **fail-closed** | **`APR-503` ×3** | ✅ **PASS，稳定** |

**NT-5 稳定性结论**：由上一轮的 **FAIL**（静默降级 `system`）翻为**确定性 `APR-503`**，
三次重复**逐字一致**，非偶发。

**补充负向（本轮新增/复验）**：缺失通道 `APR-503` · 非法通道 `APR-503` · 无决策段零归档 ·
重复归档被幂等拦 · 越界写零副作用 · critical 零副作用 · 持久化失败注入（3 组）· 预算超限五出口零出网（挂链/不挂链）。

---

## 12. Mutation / Falsification（变异 / 证伪）

**工具现状**：**无 mutation framework**（`mutmut` / `cosmic_ray` / `mutpy` 均未安装；`pyproject.toml` 无配置）。
S6-2b 声称的"14 次 mutation"是**历史手工记录**，非常驻套件。
按指示**不新造框架**，只做**小规模定向证伪**：把仓库复制到 `%TEMP%\m4-mut\`，注入**单行**违约，
跑目标测试，**立即还原副本**。原仓库零写入（已核验无 `# MUTATION` 残留）。

| # | Mutation | 目标测试 | 结果 |
|---|---|---|---|
| **M1** | **INV-03** `if d == "reject":` → `if False:` | `tests/security/test_deny_then_execute.py` | **RED**（3 failed, 1 passed） |
| **M2** | 装配 fail-closed：`governance is None` 检查 → `if False:` | `tests/security/test_governance_bypass.py` | **RED**（1 failed, 5 passed） |
| **M3** | **GAP-11** `create_agent` 通道传播 → 哨兵 | `test_principal_identity.py` + `test_governance_e2e.py` | **RED**（8 failed, 24 passed） |
| **M4** | **GAP-8** 证据幂等闸 → `pass` | `tests/e2e/test_governance_e2e.py` | **RED**（1 failed, 12 passed） |
| **M5** | **GAP-10** 租户"日志优先" → `if False:` | `tests/invariants/test_inv_context_and_paths.py` | **RED**（1 failed, 16 passed） |
| **M6** | **GAP-13** 定时器异常存活 → `raise` | `tests/e2e/test_governance_e2e.py` | **RED**（1 failed, 12 passed） |
| **M7** | **GAP-7** 审计读面 → `audit = None` | `tests/acceptance/test_governance_audit_acceptance.py` | **RED**（5 failed, 4 passed） |

**7 / 7 RED ⇒ 本轮新增的不变量测试确有鉴别力，不是假绿。**

**但变异也暴露了覆盖边界**：**没有任何 mutation 针对 D1（`loop.wake` 抛错时零证据）** ——
因为没有测试断言它。**D1 是真实的覆盖空洞**，这正是它从上一轮溜过的原因。

---

## 13. Documentation Consistency（文档一致性）

| 声称处 | 声称 | 实测 | 判定 |
|---|---|---|---|
| `LIMITATIONS.md:68` | `security 49 · acceptance 7 · e2e 13 · integration 6` | **49 · 9 · 18 · 6** | ❌ **漂移** |
| `F2_REMEDIATION_REPORT.md:419-421` | `acceptance 7 passed` / `e2e 13 passed` | **9 / 18** | ❌ **漂移** |
| `F2_REMEDIATION_REPORT.md:476` | `acceptance 7 / e2e 13 / integration 6` | **9 / 18 / 6** | ❌ **漂移** |
| `README.md:4` | `1,944 collected / 1,940 passed / 0 failed / 4 skipped` | ✅ 一致 | ✅ |
| `LIMITATIONS.md:50` | 同上 | ✅ 一致 | ✅ |
| `LIMITATIONS.md:45` | `信封字段 10` | ✅ 10（含 `tenant_id`） | ✅ |
| `LIMITATIONS.md:45` | `EVENT_TYPES 77 · SYNC 14 · TRANSIENT 3 · payload 77` | ✅ 77 / 14 / 3 / 77（78 含 `_PayloadBase`） | ✅ |
| `README.md` 治理段 | "读侧可达 / 凭证核验 / 单点出网闸" | ✅ 均经本轮实测成立 | ✅ |
| `LIMITATIONS.md` L-10/L-11 | "生产调用路径已建立" | ⚠ **L-11 过宽**：GAP-8 仅快乐路径成立（见 §4.2） | ❌ **漂移** |

**Documentation Drift 汇总（3 处）**：
1. 分层测试计数（acceptance / e2e）在 2 个文件中写错；
2. `LIMITATIONS` L-11 的"已修复"未限定"仅正常完成路径"；
3. `LIMITATIONS` L-4 的"预算面已闭合"**成立**（本轮实测确认），但**未登记**"LLM 无授权语义"这一同行结论在 README 中的表述边界（README 已写明"单一闸约束会话预算"，措辞准确 —— **无漂移**）。

**按要求：只记录，不修改。**

---

## 14. Production Closure Audit（生产闭环审计）

逐段判定。**判定规则：存在生产调用路径 **且** 有对应证据才算 Closed。**

| # | 环节 | 生产调用路径 | 证据 | 判定 |
|---|---|---|---|---|
| 1 | **Intent**（意图） | 外壳 → `user.message`(sync) → TaskQueue → `run_for_task` | 落盘事件 | **Closed** |
| 2 | **Judgment**（判断） | `_evaluate_full` 七条 guard 单调求值 | `guard.evaluated` 每次求值恰一条 | **Closed** |
| 3 | **Authorization**（授权） | `authorize()` 唯一入口（AST 恰 1 处） | `decision.issued`(SYNC) + 主体归属 | **Closed** |
| 4 | **Execution**（执行） | 关 3 Provider（超时/驱逐/取消） | `tool.result` / `tool.error`；**外部副作用**（文件） | **Closed** |
| 5 | **Result**（结果） | 关 4 finalize（schema/spill/摘要） | `tool.result` 载荷 | **Closed** |
| 6 | **Receipt**（凭证） | `ReceiptStore.emit`（context.py:128） | `receipt.emitted` + `prev_hash` 链 + **服务层重算核验** | **Closed** |
| 7 | **Persistence**（持久化） | `SessionLog.append` → bus → store；间隔定时器 | JSONL 真源 + replay 一致 + 失败有界 | **Closed**（配置面见 N1） |
| 8 | **Audit**（审计） | 服务 → 路由/原生 → `AuditSystem.{causal_chain,denied_report,reconcile}` | 真实运行日志上 `findings==[]`；破坏事实可检出 | **Closed** |
| 9 | **Evidence**（证据） | `run_for_task` → `archive_task_evidence` → `archive()` | `evidence.archived` + refs 可解析 + 幂等 | **Partial** — **仅正常完成路径**（D1/D2） |
| 10 | **Replay**（重放） | `open_session` / `from_log` / `rebuild_from_log` | 重启后逐条一致；租户日志优先 | **Closed** |
| 11 | **Verification**（验证） | `verify_receipt` / `verify_chain` / `reconcile` 经服务可达 | 凭证 `verified==count`、`chain_valid` | **Closed** |

**唯一 Partial 在 Evidence 段** —— 且失败的是**非主链**（异常/取消/子 Agent），
主链（正常完成）成立。

---

## 15. Final Independent GAP Matrix（最终独立缺口矩阵）

| GAP | 上一轮结论 | 独立复核结果 | Evidence | Final Status |
|---|---|---|---|---|
| **1** LLM 出口治理 | Partially Closed | **维持 Partially**，但边界被**收窄并明确化**：只治理预算；无授权语义为**有意边界**；`ping` 为登记负空间 | 真实 Settings + 真实链：超预算 5/5 `BudgetExhausted` 零出网（挂链/不挂链）；`require_adapter` 零调用者 | **Partially Closed**（理由更严） |
| **2** 持久化有界重试 | Closed | **未在本轮独立重验**（不在用户点名清单内）；相关测试文件在册 | — | **Closed（carried forward，未独立重验）** |
| **3** 原生壳 | Partially Closed | **维持**：仅收集边界闭合；UI 运行时 **Environment Blocked** | 覆盖 0%；2 个显式 skip；需 PySide6 + lock 补条目 | **Environment Blocked** |
| **4** security 测试 | Closed | 复跑 49 全绿；未做对抗性复审 | 49 passed | **Closed** |
| **5** acceptance/e2e | Closed | 功能成立，但**计数写错** | 实测 9 / 18 | **Closed（+ Documentation Drift）** |
| **6** CI | Environment Blocked | **维持**：YAML 合法、静态闸本地通过，**从未在 Actions 执行** | 无 CI run 记录 | **Environment Blocked** |
| **7** AuditSystem | Closed | **CONFIRMED**（独立 agent 全链 + 我读码）；旧 telemetry 为 additive 非 bypass | 两外壳全链可达；数据源实跑 | **Closed** |
| **8** Evidence 生产者 | Closed | **证伪**：正常路径成立，**异常/取消路径零证据**（我独立复现）；**子 Agent 零证据** | normal=1 / wake-raises=**0**；`orchestration.py` 零 `evidence` 命中 | **REOPENED → Partially Closed** |
| **9** 上下文契约 | Partially Closed | **维持**；新发现 `approval.*` 三类事件 `call_id` 两处皆无 | 实测 8 类位置契约成立；3 类缺失 | **Partially Closed** |
| **10** 租户归属 | Closed | **CONFIRMED**（单一权威、无 Envelope 旁路、日志优先实测） | 重开换租户 → 日志值胜出 | **Closed** |
| **11** 主体归属 | Closed | **治理层 CONFIRMED**；**"全部 fail-closed"过宽** —— 审批层/服务层为降级 | 三态实测；`approval.py:611` / `engine.py:628` / `service.py:112` | **Partially Closed** |
| **12** 死代码 | Closed | 未见复活；不变量测试在册（本轮未额外变异验证） | `hasattr(ToolExecutor,"_reject")==False` | **Closed（未独立重验）** |
| **13** flush 定时器 | Closed | **本体成立**（A–D 全通过）；**配置面证伪** | 6 配置 + 3 注入点：41 行/0 重复/0 缺失；配置 3.0/7 → 实际 0.5/64 | **Partially Closed + GAP-NEW-01** |

---

## Reopened GAPs（重新打开的缺口）

### REOPENED-GAP-08 — Evidence 生产者在失败路径缺席【P2 · Overstated】

- **原结论**：Fully Closed。
- **复核发现**：`archive_task_evidence` 不在 `finally` 内；`loop.wake` 抛错即跳过 ⇒
  已含治理决策的段产出 **0** 条证据。**我独立复现**（normal=1，wake-raises=0）。
- **为何算"重开"而非"新缺口"**：它直接违反上一轮为 GAP-8 写下的**冻结规则 #2**，属于原结论的适用范围过宽。
- **不自行修复**（本轮纪律）。

---

## New GAPs（新增缺口）

| 编号 | 内容 | 位置 | 级别 | 方向 | 状态 |
|---|---|---|---|---|---|
| **N1** | `flush_interval_s` / `flush_batch` **完全未接线**（死配置） | `persistence.py:761-795`；`config.py:696-699` | **P2** | Overstated | 实测 |
| **N2** | 子 Agent 会话**零证据**（child bus 无 collector，`_run_on_session` 不调生产者） | `orchestration.py:141-177` | P2/P3 | Neutral | 仅读码 |
| **N3** | 证据工件**先于段关闭**发出 | `engine.py:927` vs `task_queue.close_segment` | P3 | Neutral | 实测（seq 21 < 23） |
| **N4** | `approval.granted/denied/timeout` 的 `call_id` **payload/trace 两处皆无** | `approval.py:410-411` / `:428` | P3 | Neutral | 实测 |
| **N5** | **审批层与治理层对"通道缺失"规则矛盾**（前者回落 `self._channel`，后者 fail-closed） | `approval.py:602-615` vs `context.py:86-93` | **P2** | Neutral | 读码（当前被调用顺序遮蔽） |
| **N6** | `ApprovalProvider` 的 channel **硬编码 `"desktop"`** | `engine.py:628-629` | P3 | Overstated | 读码（被 N5 遮蔽） |
| **N7** | `str(channel or "desktop")` 把**显式 None**提升为人类通道 | `service.py:112` | P3 | Neutral | 读码（潜伏） |
| **N8** | `require_adapter` 是**死公共导出**（零生产调用者） | `llm.py:788` + `__all__` | P3 | Neutral | 实测 |
| **N9** | **文档漂移**：分层测试计数写错（acceptance 7→9，e2e 13→18）；L-11 未限定"仅正常路径" | `LIMITATIONS.md:68`；`F2_REMEDIATION_REPORT.md:419-421,476` | P3 | Understated | 实测 |

---

## Persistence Verification（持久化验证）

- **有界重试 → 失败状态 → 可观察证据**：三路径共用 `_failure_state_reached`（连续失败 ≥3 或 `_retry_q` >192）。
- **无丢失 / 无重复**（本轮独立实测）：3 组注入点 × 40 事件 ⇒ 每组 `total_lines=41`、`duplicate_lines=0`、`missing=[]`。
- **无无限重试**：暂停后新写入不触碰写通道。
- **无关闭挂死**：间隔 5s 时 `close()` 实测 **0.001s** 返回。
- **落盘窗口有上界**：定时器使非 SYNC 事件不再无限期滞留（实测攒批→按间隔落盘）。
- **配置面**：**未接线**（N1）。

---

## Audit Verification（审计验证）

- 四个 `AuditSystem` 公开方法中 3 个经服务层与路由可达（`causal_chain` / `denied_report` / `reconcile`）；
  `legacy_session_audit` 保留为旧面兼容，**未接外壳**（登记）。
- 读侧**零写**（`reconcile` 默认 `emit=False`）；唯一写点 `emit=True` 需显式请求。
- **可复现**：结论由重放得出；真实链路上 `findings == []`。
- **能检出破坏**：只读过滤视图（去 `guard.evaluated`）⇒ 正确报 `NO-GUARD-EVENT`。
- `Envelope` frozen ⇒ 就地篡改事件不可行。

---

## Evidence Verification（证据验证）

- 生产者存在且**在正常任务完成路径**上（`run_for_task` → `archive_task_evidence` → `archive()`）。
- 产生规则**部分成立**：有治理决策**且 run 未抛错**才归档（D1）。
- 幂等取自日志重放（实测重复调用返回 `None`）。
- 引用可解析（INV-E2，实测 refs 全部落地成功）。
- 只存引用不复制内容（INV-G4/E1）。
- **覆盖缺口**：异常/取消路径（D1，实测）、子 Agent 会话（D2，读码）。

---

## CI Status（持续集成状态）

**Environment Blocked。** YAML 合法、job/step 齐全、静态闸命令本地实跑通过，
但**从未在 GitHub Actions 上执行**（未 push）。**不接受"本地通过 = CI 已验证"。**

---

## Native Shell Status（原生外壳状态）

**Environment Blocked。**
真正闭合的只有**测试收集边界**（默认 pytest 无需 `--ignore`；缺依赖显式带理由 skip）。
`desktop_native/` **UI 行为运行时验证仍为 0**（覆盖 0%）。
解除阻塞需要：安装 `PySide6`+`qasync` **且** 补齐 `uv.lock` 的 native 包条目（D-1）。

---

## Documentation Consistency（文档一致性）

**3 处漂移**（详见 §13），全部为**测量/限定语**层面，非能力误述：
1. 分层测试计数（acceptance 7→9、e2e 13→18）出现在 `LIMITATIONS.md:68` 与报告 `:419-421,476`；
2. `LIMITATIONS` L-11 的"已修复"缺少"仅正常完成路径"限定；
3. （无其他）README 治理段、计数类声称（1944、10 字段、77/14/3/77）**均与实测一致**。

**按要求只记录，未修改。**

---

## Final Capability Matrix（最终能力矩阵）

| Capability | 独立复核 | Evidence | Production Connected | Independently Verified |
|---|---|---|---|---|
| 工具授权 `authorize()` | CONFIRMED | AST 恰 1 处 + E2E | ✅ | ✅（M2 变异 RED） |
| Guard 单调拒绝 g1–g7 | CONFIRMED | E2E + 禁放行 API 面 | ✅ | ✅（M1 变异 RED） |
| deny ⇒ 零执行 | CONFIRMED | 外部副作用（文件未变）+ 3× 稳定 | ✅ | ✅ |
| 人工审批重入判定 | CONFIRMED | 2× guard.evaluated + D1→D2 supersedes | ✅ | ✅ |
| 主体归属（治理层） | CONFIRMED | 三态实测 | ✅ | ✅（M3 变异 RED，8 failed） |
| 主体归属（**审批层/服务层**） | **PARTIAL** | 规则矛盾（N5/N6/N7） | ✅ | ❌ |
| 决策因果还原 | CONFIRMED | 两外壳全链 + 实跑 | ✅ | — |
| 凭证 + 离线核验 | CONFIRMED | `verified==count`、`chain_valid` | ✅ | — |
| 一致性对账 | CONFIRMED | `findings==[]` + 破坏可检出 | ✅ | — |
| **证据归档** | **PARTIAL** | 正常路径 1 条；**异常路径 0 条** | ✅（仅快乐路径） | ✅（M4 变异 RED；D1 独立复现） |
| 租户归属 + 日志优先 | CONFIRMED | 换租户重开实测 | ✅ | ✅（M5 变异 RED） |
| 上下文契约 | PARTIAL | 8 类位置契约成立；`approval.*` 3 类缺失 | ✅ | ✅ |
| LLM 出网预算闸 | CONFIRMED | 挂链/不挂链各 5/5 零出网 | ✅ | — |
| **flush 定时器本体** | CONFIRMED | 6 配置 + 3 注入点，0 重复 0 丢失 | ✅ | ✅（M6 变异 RED） |
| **flush 配置面** | **REFUTED** | 配置 3.0/7 → 实际 0.5/64 | ❌ | — |
| 持久化失败语义 | CONFIRMED（本轮） | 3 组故障注入 | ✅ | — |
| 安全测试面 | 复跑绿 | 49 passed | — | — |
| **CI 门禁** | **无法验证** | 无 run 记录 | ❌ | ❌ |
| **原生壳 UI** | **无法验证** | 覆盖 0% | ❌ | ❌ |

---

## Final GAP Matrix（最终缺口矩阵）

```
Closed (7)            : GAP-2* · GAP-4 · GAP-5* · GAP-7 · GAP-10 · GAP-12*
Partially Closed (4)  : GAP-1 · GAP-8(REOPENED) · GAP-9 · GAP-11 · GAP-13
Environment Blocked(2): GAP-3 · GAP-6
* GAP-2 / GAP-12 本轮未独立重验;GAP-5 功能成立但计数写错

New Gaps (9)          : N1(P2) · N2(P2/3) · N3(P3) · N4(P3) · N5(P2)
                        N6(P3) · N7(P3) · N8(P3) · N9(P3, 文档漂移)
Reopened (1)          : REOPENED-GAP-08 (P2)
```

---

## Production Closure Assessment（生产闭环评估）

**闭合的段落（7/11）**：Intent · Judgment · Authorization · Execution · Result · Receipt ·
Persistence（配置面除外）· Replay · Verification —— 其中 **9 段判定 Closed**。

**唯一的 Partial 在 Evidence 段**：主链（正常完成的任务）成立；
**非主链（异常 / 取消 / 预算耗尽 / 子 Agent）不成立**。

**未闭合的两段**：CI 门禁与原生壳 UI 运行时 —— 均为**环境受阻**，非代码缺陷。

**本轮的核心结论**：

> 上一轮的修复**在主干与主链上真实成立**，且经**变异证伪（7/7 RED）**证明其测试具备鉴别力。
> 但"闭合"的**范围**比上一轮报告宣称的**窄**：证据面只覆盖快乐路径、配置面完全未接线、
> 主体归属只在治理层 fail-closed。**这三处都是"主张过宽"而非"实现错误"** ——
> 即上一轮把"主链成立"外推成了"全部成立"。
>
> 另外需要记录一次**我方测量错误被自己抓住**：并发探针初报"重复写"，追查后确认是
> **substring 计数 bug**（`CONC-1` ⊂ `CONC-10…19`），实际文件逐字节正确。
> 该结论已在本报告中更正，未作为缺陷上报。

---

_文档时间戳：2026-09-20T17:52:00+08:00_
