# F2 Remediation Report — PyHarness Governance & Runtime Closure（完整修复轮）

> **报告时点**：2026-09-20 ｜ **模式**：M3 Implementation（执行中，未提交）
> **基线**：F2 Baseline（本文件 §2）｜ **变更**：38 tracked 文件 + 8 新增测试/CI 文件
> **未提交、未 push**：本轮所有改动均在工作区。任何"已修复"结论指**工作区已实现且测试通过**，
> 不等于已发布。

---

## 1. Executive Summary（执行摘要）

**结论先行：**

> **13 个 GAP 全部逐项处置完毕，其中 9 项完全闭合、3 项部分闭合、1 项环境受限。**
> 全量测试 **1944 collected / 0 failed / 0 errors / 4 skipped / 79% 覆盖**（基线 1839 / 0 / 2 / 79%），
> **默认 `pytest` 已无需 `--ignore`**。原始 5 条反向验证**逐字复现且全部 PASS**（其中 NT-5 由 FAIL 翻为 PASS）。
>
> **本轮最重要的产物不是代码，而是"治理读侧从不可达变为可达"**：修复前 `AuditSystem` 与
> `EvidenceCollector` 是**已构造、已注入、零调用**的装饰品（`pyharness/` 内零生产调用点），
> 全部治理数据在盘上而产品内无入口；现在决策因果还原、被拒清单（含"拦了且没执行"证据位）、
> 一致性对账、**凭证逐条重算核验 + `prev_hash` 链**、按任务段的证据归档，全部经
> `ApplicationService` → HTTP 路由 / 原生壳审计页可达，并有 E2E 与验收测试覆盖。

**GAP 状态一览：**

```
Fully Closed (9):      GAP-2 · GAP-4 · GAP-5 · GAP-7 · GAP-8 · GAP-10 · GAP-11 · GAP-12 · GAP-13
Partially Closed (3):  GAP-1 · GAP-3 · GAP-9
Environment Blocked (1): GAP-6 (CI 已建立但未在 GitHub Actions 实跑 —— 未 push)
```

**本轮发现并处置的新缺口：0 个需要修的**；另登记 3 项**有意保留的负空间**（LLM 不走出网授权、
`ping` 不受闸、若干标识符不引入），已写入 `LIMITATIONS.md` 与代码 docstring。

**必须诚实说明的两件事**：

1. **CI 未经验证。** `.github/workflows/ci.yml` 已写、YAML 合法、其中每条静态闸命令我都在本地
   实跑通过，但**从未在 GitHub Actions 上跑过**（未 push）。按技能口径这是 `IMPLEMENTED`，
   不是 `VERIFIED`。
2. **PySide6 原生壳本地仍不可执行。** 本机 `.venv` 无 PySide6，两测试文件现在是**显式 skip**
   （带理由），不再是 collection error —— 但"原生壳 UI 行为经自动化验证"**仍不成立**。CI 装了
   `.[dev]` 应会真跑，同样**未验证**。

---

## 2. Baseline（基线）

**保留于 `tmp/f2_junit/F2_baseline_junit.xml`（未被后续结果覆盖）。**

| 维度 | 基线实测 |
|---|---|
| Full Test Suite | **1839 collected / 1837 passed / 0 failed / 0 errors / 2 skipped / 62.9s** |
| 覆盖率 | **79%**（18063 stmts / 10537 miss —— 单跑子集时的口径；全量为 18211/3800） |
| 跑法 | `pytest -q --ignore=tests/unit/test_desktop_native.py --ignore=tests/unit/test_shell_parity.py`（**必须 ignore**） |
| Governance E2E（Allow/Deny/Approval/Re-Judgment） | 4/4 通过（真实引擎 + 脚本化 LLM，零网络） |
| Negative Verification | 4/5 PASS；**NT-5 context loss 为 FAIL**（缺失通道静默降级为 `system`） |
| 生产调用路径 | `authorize()` 2 处（`tools_executor.py:479/:625`） |
| 库级可达但生产未接入 | `AuditSystem`（4 方法）· `EvidenceCollector.archive/collect_for_task` · `verify_receipt` · `ToolExecutor._reject` · `flush_interval_s` · `tenant_id`→事件 |

---

## 3. GAP-1 ~ GAP-13 Remediation（逐项）

### GAP-11 — Principal Attribution（主体归属）**Fully Closed**

- **根因**：`create_agent`（`core/agent.py`）构建 `ag.ctx` 时**从不设置 `ctx.channel`**，而
  `authorize()` 收到的是 **agent ctx**（`agent_loop.py:280` → `ctx.tools.execute(call, ctx)`），
  不是外壳自己的 ctx。`principal_of` 的 `getattr(ctx,"channel",None)` 恒为 `None` →
  回落 `system`。实测证明两个 ctx 是**不同对象**。
- **修复**：新增三态哨兵 `CHANNEL_UNDECLARED`（`core/agent.py`）；`EngineSpine.channel`
  取代散落声明；`create_agent` / `_runtime_ctx` 从 spine 落到 ctx；**属性缺失 ⇒ `APR-503`
  fail-closed**（与"显式 `channel=None`"区分开）。
- **生产装配点全部声明**：`cli.py`（cli/None）· `acp.py`（`acp:<id>`）· `application/service.py`
  ×2（desktop）· `desktop/app.py` ×2（desktop）· 原生壳经 `ApplicationServiceRegistry` 继承。
- **验证**：E2E 实测 D2 `principal_kind="human"`, `principal_channel="desktop"`；凭证
  `r.principal.kind == "human"` 且 `verify_receipt(r) is True`。

### GAP-13 — Flush Timer **Fully Closed**

- **根因**：`flush_interval_s` 被存储（`persistence.py:321`）但**全库零读取者**；三处注释
  （`persistence.py:301/348/370`）与 `cli.py:664` 宣称的"0.5s 定时器"**不存在**。
- **修复**：实现 `EngineSpine.start_flush_ticker()`（惰性启动、幂等、单次失败不杀定时器、
  不吞取消）；`close()` 先停表（协作式 stop + 2s 超时兜底 cancel）；`run_for_task` 为启动点。
  四处文档改为指向真实归属。
- **验证**：E2E 直接读 JSONL 物理文件，断言"间隔前不在盘、之后在盘"；另有"首跳失败后定时器
  仍继续跳"的负向用例。事件顺序未变，**1926 条既有测试无一回归**。

### GAP-2 — Persistence Retry / Failure Semantics **Fully Closed**

- **根因**：阈值判定 `_fail_streak >= 3 || _retry_q > 192` **只存在于 `_flush_batch`**；
  强同步 `append` 与公开 `flush` 只累加计数不判阈值 ⇒ 磁盘持续故障时**不暂停、队列无界增长**。
- **修复**：抽出单一判据 `_failure_state_reached()` 与状态迁移 `_enter_failure_state()`
  （发 `system.error` 作为**可观察证据** + `_suspended=True`），三条写路径共用。
- **验证**（故障注入，真实 store + 注入 `OSError` 写句柄）：强同步达阈值 → 暂停 +
  证据事件 + 行不丢；失败状态后新写入**快速拒绝且不触碰写通道**（无重试循环）；公开 `flush`
  同样有界；成功后 `_fail_streak` 归零；`close()` 不挂死。

### GAP-7 — AuditSystem Production Connection **Fully Closed**

- **根因**：`AuditSystem` 已构造注入 `GovernanceContext`，但 `causal_chain` / `denied_report` /
  `reconcile` / `legacy_session_audit` 在 `pyharness/` 内**零调用点**；桌面"审计"页只用旧遥测
  （事件类型计数，无决策因果）。
- **修复**：`ApplicationService.governance_audit`（replay-only 读侧）→ 路由
  `/api/sessions/{sid}/governance-audit` → Web UI 审计面板 + 原生壳 `refresh_audit`。
  一并接上 **`verify_receipt` / `verify_chain`**（此前 README 自陈"未接 CLI/HTTP/外壳"），
  且改为 **`rebuild_from_log`**（INV-G2/R2：不依赖内存缓存，重启后同样成立）。
- **验证**：E2E + Acceptance 实测真实运行的 allow/deny 会话，返回决策、被拒清单
  （`executed: false`）、`findings == []`（真实链路一致）、因果链 `missing == []`、
  凭证 `count==2 / verified==2 / chain_valid==True` 且 `prev_hash` 链正确。

### GAP-8 — Evidence Production Producer **Fully Closed**

- **根因**：订阅已接真总线，但唯一写点 `archive()` **零调用** ⇒ `evidence.archived` 永不产生、
  索引恒空、`collect_for_task()` 恒返回 `()`。
- **修复（含设计裁定）**：`engine.archive_task_evidence`——**单位=任务段**（段正是
  `collect_for_task` 的聚合键）、**触发=段内至少一条 `decision.issued`**（无治理决策的段**不归档**）、
  **只存引用**（INV-G4/E1）、**幂等取自日志重放**（重启后仍成立）、**失败静默**（派生视图不阻断主链）。
- **验证**：E2E 实测一条工件、refs 指向段锚 + 2 条 `decision_id` + 2 条 `receipt_id` 且全部可解析；
  索引与日志重建一致；重复归档返回 `None` 且不新增事件；纯文本段**零归档**（负向）。
  查询面经 `governance_evidence` 服务方法与路由可达。

### GAP-4 — tests/security **Fully Closed**

新建 `tests/security/`，**49 条用例**，覆盖：治理绕过（缺 governance/guard/session/scope ⇒
`CYC-999` + Provider 零调用）、禁放行 API 面（10 个方法全 AttributeError）、拒绝后执行
（零副作用 + `GRD-402` 重放拦截）、参数篡改（`GRD-403` + 文件未变 + **对照组证明不是"永远拒绝"**）、
主体伪造（格式非法 ⇒ `APR-503`；合法非人类前缀 ⇒ 解析但**永不成为 HUMAN**）、上下文丢失
（缺属性 ⇒ fail-closed；显式 headless ⇒ 合法 SYSTEM）、LLM 出网治理三态。

**纪律**：凡"没有执行"一律给**外部副作用证据**（文件存在性/内容）或 **Provider 零调用计数**，
不用字段自述代替。**未删除任何既有测试。**

### GAP-5 — Acceptance / E2E **Fully Closed**

| 目录 | 用例 | 内容 |
|---|---|---|
| `tests/acceptance/` | 7 | 治理审计面、凭证核验、证据面、负向（未知 decision_id 不臆造 / 空段无证据） |
| `tests/e2e/` | 13 | Allow / Deny(策略) / Deny(critical) / 人工审批重入判定 / 主体归属 / 通道缺失 / flush 定时器 ×2 / 证据生产者 ×3 |
| `tests/integration/` | 6 | MCP stdio（既有）+ **外壳↔服务契约 AST 漂移检测**（4） |

共享夹具上提到 `tests/conftest.py`（`e2e_factory` / `adapter_factory`），acceptance 与 e2e
共用同一套**真实装配原语**（真 store / SessionLog / GuardChain / ToolExecutor / 持久化；
**仅** LLM 适配器为脚本替身，且它不在被测链路上）。

### GAP-6 — CI **Environment Blocked（已建立，未验证）**

`.github/workflows/ci.yml`：`test` job（全量无 `--ignore` + 覆盖率下限 `COV_FLOOR=75` + junit 上传）
与 `gates` job（security / acceptance / e2e / invariants / 结构 / 静态闸分层跑）。
单 OS = windows-latest（项目 Windows-first，避免未验证平台引入与代码无关的红灯）；
安装 `.[dev]` 让原生壳用例**真跑**而非 skip。
**每条静态闸命令我都在本地实跑通过**（含 AST 判定 `authorize()` 恰 1 处）。
**未 push ⇒ 从未在 Actions 上执行。** 标记 `Environment Blocked`。

### GAP-3 — PySide6 Native Shell Coverage **Partially Closed**

- **修复**：两测试文件加 `importorskip`（**显式带理由的 skip**，不再是 collection error）；
  **新增不依赖 GUI 的边界**：`tests/integration/test_shell_service_contract.py` 用 AST 校验
  「`self.service.X()` 的 X 都存在」「`main_window` 调用的 `controller.X` 都存在」
  —— 这是一类**真实且常见**的缺陷，且无 PySide6 也能检出。
- **未闭合**：本机仍无 PySide6 ⇒ `desktop_native/` **UI 行为**仍不可本地自动化验证。
- **边界划分已明确**：`ApplicationService` **不依赖 GUI 即可验证**（本轮全部验收测试证明了这点）；
  native UI 行为属**外壳职责**，只能在有 PySide6 的环境验证。

### GAP-1 — LLM Exit Governance **Partially Closed**

- **关键判断：不在 8 个出口复制 `authorize()`。** 已存在的唯一汇点 `LLMClient._chat_any`
  被五个公开出口（`chat`/`chat_stream`/`mini`/`summarize`/`json_chat`）共用 ⇒ **只需一处加闸**。
- **为什么边界不是 `governance.authorize()`**：g1–g7 按**工具名**前缀匹配、`authorize()` 收
  `ToolCall`、产出以 `call_id` 串联 `tool.call`/`tool.result`。LLM 请求无工具名、无参数集合、
  无 Provider 副作用；塞进去只能靠伪造 `ToolCall`，会污染工具治理链语义（违反 P1）。**故不这么做。**
- **LLM 出网实际适用的治理面 = 会话预算**，已下沉为 `_egress_guard`：修复前
  `scope.check_budget` **只在轮起点**调用，`mini`（自动标题）/`summarize`（压缩）/`json_chat`
  （计划）三条**轮内**系统出口可绕过。
- **Negative Space 显式登记**：适配器层 `chat`/`chat_stream` 经核验**全库无生产直调**（唯一路径经本类）；
  `ping` 不治理（不产生 tokens / 不落计量，受预算约束语义颠倒）。
- **未闭合**：LLM 出网**没有** `decision` / `receipt` —— 这是**有意设计**，故"LLM 调用受授权"
  不成立，且**不计划**改变（需独立架构裁定）。

### GAP-9 — Context Propagation Contract **Partially Closed**

- **修复**：把契约写成**可执行断言**（`tests/invariants/test_inv_context_and_paths.py`）：
  `session_id`（必填/每条/持久化/可回放）· `call_id`（含**位置契约**）· `decision_id`
  （生成→持久化→可查询）· `receipt_id` · `agent_id` · `tenant_id`（生成/传播/持久化/恢复/查询）。
- **发现并登记一处真实的契约不一致（L-17）**：`call_id` 在 `tool.*` 里位于 **payload**、
  在治理类事件里位于 **Envelope.trace**。这不是随机，而是"工具面当业务字段、治理面当信封级
  关联元信息"的分工 —— 已写成逐类断言钉死，**统一化未计划**（需改多个读侧）。
- **负空间显式断言**：`request_id` / `trace_id` / `conversation_id` / `subject_id` / `user_id`
  **不在契约内**；测试会在有人悄悄加入时变红，强制走一次有意识的契约变更。
- **未闭合**：跨请求/跨会话关联仍不可用（**有意保持**，产品当前无此需求）。

### GAP-10 — Tenant Attribution **Fully Closed**

- **关键判断：不机械地把 tenant 加进 77 个载荷模型。** 租户是**横切**归属，放**信封**一处
  胜过改 77 处 schema（H-3 级且必然漂移）。
- **修复**：`Envelope.tenant_id`（可选，默认 `None`，旧日志向后兼容）→ 由
  `SessionLog.tenant_id` **框架侧**盖到每条事件（不由各调用点逐个传）；
  `open_session(..., tenant_id=)` **日志优先**恢复；桌面管理器 / 服务层 / 子 Agent 会话全部贯通。
- **验证**：E2E 实测每条事件带租户；**重开时日志胜出**（换调用方无法改写历史归属，告警不回写）；
  无声明会话**保持 `None`**（不捏造）；查询面报的是**事件事实**的租户而非服务实例当前租户。
  这同时闭合 L-1 登记的"租户未随会话落盘"残余。

### GAP-12 — Dead Code **Fully Closed**

- **取证**：`ToolExecutor._reject()` **零调用点**（仅自身定义 + 3 处说明性引用）；语义已整体
  上移到 `GuardChain._evaluate_full`。**保留的风险 > 价值**：它让 `guard.rejected` 存在
  **第二个发射点**，一旦被"顺手接线"就造出绕过 `decision.issued` 的第二条拒绝路径。
- **处置**：**删除**；模块 docstring 偏离 5/11 同步更新；新增不变量测试
  （AST 判定 `guard.rejected` 发射点**恰 1 处**、`ToolExecutor` 不得再有 `_reject`）。

---

## 4. New GAPs（本轮新发现）

**需要修的：0 个。** 本轮新发现的问题全部是**有意保留的负空间或被登记的既有不一致**，
已在 `LIMITATIONS.md` 登记为 L-17 / L-18 / L-19，并在代码 docstring 与测试中显式表达：

| 编号 | 内容 | 为什么不算"该修" |
|---|---|---|
| L-17 | `call_id` 位置不统一（payload vs trace） | 有内在分工，已写成逐类契约断言；统一化需改多个读侧且无收益 |
| L-18 | `ping` 不受出网闸约束 | 健康探针不产生 tokens/计量；受预算约束会使"探测可用性"被预算拒，语义颠倒 |
| L-19 | `scope` 缺失时出网闸降级放行 | 预算闸是**配额面**而非**授权面**；缺配额 ≠ 越权。与 executor 的 fail-closed 是不同判断，两者并存属有意 |

**另有一处"测试自身缺陷"被修复**（非产品问题）：我最初用 `grep "governance.authorize("` 写守卫，
而我在 docstring 里写了该符号 ⇒ 假阳性。已改为 **AST 精确判定**，并同步修正 CI 静态闸。

---

## 5. Architecture Changes（架构变更）

| # | 变更 | 性质 | 向后兼容 |
|---|---|---|---|
| A1 | `Envelope` 新增可选字段 `tenant_id` | 信封 schema（H-3） | ✅ 旧日志缺字段反序列化为 `None`，replay 不变 |
| A2 | `SessionLog` 新增 `tenant_id`；`open_session(tenant_id=)` | 构造面扩展 | ✅ 默认 `None` |
| A3 | `EngineSpine` 新增 `channel` / `_flush_task` / `_flush_stop` | 装配面扩展 | ✅ 默认哨兵 |
| A4 | `create_agent` / `_runtime_ctx` 落到 ctx 的字段 +`channel` | ctx 契约扩展 | ⚠ **行为收紧**：未声明通道从"静默 system"变为 `APR-503` |
| A5 | `LLMClient` 新增 `_egress_guard`（`_chat_any` 前） | 出网面新增闸 | ⚠ **行为收紧**：预算超限的轮内系统出口从"放行"变为拒绝 |
| A6 | `ApplicationService` 新增 `governance_audit` / `governance_evidence` | 服务契约扩展 | ✅ 新增方法 |
| A7 | `DesktopApp` / `NativeController` 新增两个读面 | 外壳契约扩展 | ✅ 新增路由/方法 |
| A8 | `ToolExecutor._reject` **删除** | 删除私有成员 | ⚠ 无调用者，删除安全 |
| A9 | `persistence` 失败判据收敛为 `_failure_state_reached` | 内部重构 | ⚠ **行为收紧**：强同步/公开 flush 现在也会进入失败状态 |

**架构原则未被破坏**（逐条核对）：

- **P1 决策权与执行权分离**：`GuardChain` 仍只有拒绝/请示，无放行 API（`tests/security` 钉死 10 个禁用方法）；`authorize()` 仍**恰 1 处**调用（AST 判定）；执行权仍归 `tools_executor` 关 3。
- **P2 单一权威事实源**：所有新增读侧（审计/证据/凭证核验）**一律经日志重放**（`rebuild_from_log` / `from_log`），不读订阅态缓存 —— 实测"事后构造的 spine 其活索引为 0 而日志重建为 1"，正是这条纪律的价值。
- **P3 约束性语义**：deny 不可逆（单调性用例仍在）；allow 不泛化（`GRD-402` 重放拦截仍在）；uncertainty 默认 deny（缺装配 fail-closed 仍在）。
- **P4 结论不自证**：本轮全部结论由**外部副作用**（文件系统）、**持久化事件**、**重放**、**负向验证**支撑，未用治理自身输出证明治理正确。

---

## 6. Production Call Paths（生产调用路径）

```
[修复前不可达 → 修复后可达]

决策因果 / 被拒清单 / 一致性对账
  HTTP GET /api/sessions/{sid}/governance-audit?reconcile=true
    → DesktopApp.governance_audit            [desktop/app.py]
    → ApplicationService.governance_audit    [application/service.py]
    → AuditSystem.{causal_chain,denied_report,reconcile}  [governance/audit.py]
    → 重放 append-only 日志（零写、可复现）

凭证核验（新接）
  同上 → rebuild_from_log(log) → verify_receipt / verify_chain  [governance/receipt.py]

证据归档（新接）
  跑完一个任务段
    → EngineSpine.run_for_task
    → engine.archive_task_evidence           [engine.py]
    → EvidenceCollector.archive()            [governance/evidence.py]
    → append("evidence.archived")
  查询：HTTP GET /api/sessions/{sid}/governance-evidence?task_id=…
    → ApplicationService.governance_evidence → EvidenceCollector.from_log → collect_for_task

LLM 出网闸（新接）
  ctx.llm.{chat,chat_stream,mini,summarize,json_chat}
    → LLMClient._chat_any                    [core/llm.py]   ← 五出口唯一汇点
    → LLMClient._egress_guard → ctx.scope.check_budget()

主体归属（新接）
  外壳装配 → EngineSpine.channel → create_agent/._runtime_ctx → ag.ctx.channel
    → GovernanceContext.principal_of         [governance/context.py]
    → Decision.principal → decision.issued / DecisionReceipt.protected()

租户归属（新接）
  ApplicationService(tenant_id) → DesktopSessionManager(tenant_id) → open_session(tenant_id)
    → SessionLog.tenant_id → make_envelope(tenant_id=) → Envelope.tenant_id → JSONL
  恢复：open_session 重放 → logged_tenant 胜出

落盘定时器（新接）
  make_runner.run_for_task → EngineSpine.start_flush_ticker
    → 周期调 SessionStore.flush()（间隔读 store.flush_interval_s）
  停止：EngineSpine.close() 先停表再关 store
```

---

## 7. Static Verification（静态验证）

| 断言 | 命令 | 结果 |
|---|---|---|
| `authorize()` 调用点恰 1 处 | AST 扫描 `pyharness/`（`tests/conftest.py::authorize_call_sites`） | ✅ `['pyharness/core/tools_executor.py']` |
| `guard.rejected` 发射点恰 1 处 | AST 扫描 `_append`/`append` 首参字面量 | ✅ `pyharness/core/tools_guard.py:862` |
| `ToolExecutor._reject` 不存在 | `hasattr` | ✅ False |
| `AuditSystem` 读侧有生产调用 | `grep -rq "governance.audit"` | ✅ 命中 |
| 证据生产者有生产调用 | `grep -rq "archive_task_evidence"` | ✅ 命中 |
| 外壳↔服务契约无漂移 | AST：`self.service.X()` / `self.controller.X()` 全在 | ✅ 6/6 |
| `Envelope` 必填字段 frozen + `extra=forbid` | `model_config` | ✅ |
| 负空间：5 个标识符不在信封 | `model_fields` | ✅ |

规模变化：`pyharness/` 18063 → 18216 stmts（+153）；`tests/` 新增 **8 个文件**。

---

## 8. Runtime Verification（运行时验证）

| 场景 | 观测 | 结果 |
|---|---|---|
| 全量测试（无 `--ignore`） | 1944 / 0 failed / 0 errors / 4 skipped / 75.6s | ✅ |
| Governance E2E（真引擎，脚本化 LLM） | Allow / Deny(策略) / Deny(critical) / 人工审批重入 | ✅ 4/4 |
| flush 定时器 | 直接读 JSONL：间隔前不在盘、之后在盘 | ✅ |
| flush 定时器故障 | 首跳 `OSError` 后仍继续跳 | ✅ |
| 持久化失败注入 | 强同步/公开 flush/批量三路径均进入失败状态 | ✅ 5/5 |
| 证据生产者 | 1 工件 / refs 全可解析 / 幂等 / 空段不归档 | ✅ 3/3 |
| GAP-11 before/after 对照 | 缺失通道：`system` → **`APR-503`** | ✅ 翻转为 fail-closed |
| 审计读侧于真实运行日志 | `findings == []`（真实链路一致） | ✅ |

**一处重要的运行时发现**（写入测试注释）：`public_spine_for` 事后新建的 spine，其
`EvidenceCollector`/`ReceiptStore` **内存索引为空**（订阅成立于归档之后）。这**不是 bug**，
正是读侧必须 replay 而非读缓存的理由 —— 已写成对照组测试（`indexed <= rebuilt` 且
`rebuilt == 日志事实`）。

---

## 9. E2E Verification（端到端验证）

真实装配：`assemble_real_engine`（真 store / 真 SessionLog / 真 GuardChain / 真 ToolExecutor /
真 spill / 真 JSONL），**仅** LLM 适配器为脚本替身（无网络环境唯一可替换点，且不在被测链路上）。

| 链路 | 断言要点 |
|---|---|
| **Allow** | `tool.call → guard.evaluated(allow,7 guards) → decision.issued(allow) → receipt.emitted(decision) → tool.result(ok)`；JSONL 与内存**逐条一致** |
| **Deny(策略)** | `g-fs-path/POL-FS-2` 拒绝；`tool.result == 0`；**越界文件确实未创建** |
| **Deny(critical)** | `scope-hidden/GRD-401`；**victim.txt 内容原样** |
| **人工审批** | `guard.evaluated` **两次** + `decision.issued` **两条**（D1 `approval_ref=null` → D2 `approval_ref=13, supersedes=D1`）⇒ **重入判定而非绕过**；文件 `ORIGINAL → APPROVED-WRITE` |
| **主体归属** | D2 `principal_kind="human" / channel="desktop"`；凭证 `principal.kind=="human"` 且可核验 |
| **证据归档** | 1 工件；refs = 段锚 + 2 decision_id + 2 receipt_id；索引与重建一致 |
| **凭证核验** | 2 凭证全 verified；`chain_valid`；`prev_hash` 链正确 |

---

## 10. Negative Verification（负向验证）

**原始 5 条（修复后重跑，逐字复现）：**

| # | 测试 | 结果 |
|---|---|---|
| NT-1 | 治理绕过（缺 governance/guard/session ⇒ CYC-999；10 个禁放行 API 全 AttributeError） | ✅ PASS |
| NT-2 | Deny→Execute（拒绝零副作用 + 重放 `GRD-402`） | ✅ PASS |
| NT-3 | 事实缺失（`NO-GUARD-EVENT` / `NO-RECEIPT-FOR-GRANT` / `SEQ-GAP` / 因果链缺环 / `emit=True` 写 `syscheck.fail`） | ✅ PASS |
| NT-4 | 参数篡改（`GRD-403` + 文件未变 + **对照组证明绑定一致时会放行**） | ✅ PASS |
| NT-5 | **上下文丢失** | ✅ **由 FAIL 翻为 PASS** —— 实测：缺失通道 ⇒ `APR-503`；显式 `None` ⇒ `system`；`desktop` ⇒ `human`；伪造 `hacker` ⇒ `APR-503` |

**新增负向（本轮）：** 未声明通道 fail-closed · 非法通道 fail-closed · 空段不产证据 · 重复归档被幂等拦 · 越界写零副作用 · critical 删除零副作用 · 凭证链篡改即 False（`verify_receipt`）· 持久化失败注入 · 重试耗尽进入失败状态 · 预算超限五出口零出网。

---

## 11. Persistence Verification（持久化验证）

- **有界重试 → 失败状态 → 可观察证据**：三路径共用判据；达阈值发 `system.error`（payload 含
  `fail_streak` / `retry_q` 读数）+ `_suspended=True`。
- **无静默丢行**：失败时 `retry_q = 旧行 + 本次批`（完整回填、保序）；暂停后拒新**不丢旧**。
- **无无限重试**：暂停后新写入**不触碰写通道**（实测写计数不变）。
- **无队列无界增长**：`_failure_state_reached` 含 `_retry_q > 192`。
- **无关闭挂死**：故障后 `close()` 在 5s 超时内返回（实测）。
- **落盘窗口有上界**：定时器使非 SYNC 事件不再无限期滞留 `_pending`。
- **成功复位**：写成功后 `_fail_streak = 0` 且 `retry_q` 清空（不重复落盘）。

---

## 12. Audit Verification（审计验证）

- **读侧可达**：4 个 `AuditSystem` 公开方法中 3 个（`causal_chain`/`denied_report`/`reconcile`）
  经服务层与 HTTP 路由可达；`legacy_session_audit` 保留为旧面兼容（未接外壳，**登记**）。
- **读侧零写**：`reconcile()` 默认 `emit=False`；唯一写点 `emit=True` 仅在显式请求时触发（路由暴露 `reconcile` 查询参数，默认 false）。
- **可复现**：全部结论经重放得出；实测真实链路上 `findings == []`（无 SEQ-GAP / NO-GUARD-EVENT / NO-RECEIPT-FOR-GRANT）。
- **能检出破坏**：用只读过滤视图（真实日志去掉 `guard.evaluated`）⇒ 正确报 `NO-GUARD-EVENT`（负向）。
- **`Envelope` frozen** 使"就地篡改事件"不可行（INV-01 在模型层强制）—— 这本身是正向证据。

---

## 13. Evidence Verification（证据验证）

- **生产者存在且在生产路径**：`archive_task_evidence` 由 `run_for_task` 调用（引擎唯一驱动点）。
- **产生规则明确且被负向验证**：有治理决策才归档；空段零归档。
- **索引可完全重建**（INV-E3）：`from_log` 与活索引在同源 spine 上一致；跨 spine 时以日志为准。
- **引用可解析**（INV-E2）：`archive()` 发射前校验每条 ref；能落盘即证明可解析。
- **只存引用不复制内容**（INV-G4/E1）：payload 仅 `evidence_id/claim/refs/artifact_path`；
  `summary` 不进载荷（测试据此调整，未放宽断言）。

---

## 14. Full Test Results（全量测试结果）

```
BASELINE : 1839 collected / 1837 passed / 0 failed / 0 errors / 2 skipped / 62.9s
FINAL    : 1944 collected / 1940 passed / 0 failed / 0 errors / 4 skipped / 75.6s

命令：.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --junit-xml=…
（**无需 --ignore** —— 基线必须 --ignore 两个原生壳文件）

跳过的 4 条（全部显式带理由）：
  2 × 既有 e2e 标记跳过
  1 × test_desktop_native.py（importorskip PySide6）
  1 × test_shell_parity::test_native_controller_exposes_all_shell_operations（同因）

分层：
  tests/security      49 passed
  tests/acceptance     9 passed
  tests/e2e           40 passed
  tests/invariants    82 passed（含新增 17 条契约用例）
  tests/integration    6 passed
  tests/unit        其余
```

> **口径补正（2026-09-21,M5.5 D-1 + 持续优化轮 R1-R3）**：上面这段"分层"是 **F2 当轮**的分布快照
> （其 `invariants 82` 属 F2 时点值）。**当前**分布（2026-09-21 实测，总数 2,042）：
> `unit 1784 · security 49 · acceptance 9 · e2e 40 · invariants 154 · integration 6`。
> 以 `LIMITATIONS.md §1` 为准（本文其余数字为历史快照，不随发布更新）。

---

## 15. Coverage（覆盖率）

| | stmts | miss | cover |
|---|---|---|---|
| 基线 | 18063 | 10537* | **79%**（全量口径 18063→18211/3800） |
| 最终 | **18216** | **3801** | **79%** |

\* 基线 miss 数是"只跑部分文件"时的口径，不可与最终直接比较；**可比的是全量 79% → 79%**
（代码 +153 行、测试 +8 文件、覆盖率**持平**）。CI 覆盖率下限设为 **75**（低于实际值，留余量；
不得为凑绿而下调）。

---

## 16. Capability Matrix（能力矩阵）

> 六个概念**互不等价**，逐列区分。

| Capability | Before | After | Evidence Type | Production Connected | Independently Verified |
|---|---|---|---|---|---|
| 工具授权 `authorize()` | Implemented+Tested | Runtime Verified | E2E | ✅（恰 1 处，AST 钉死） | ✅ 负向 + 副作用 |
| Guard 单调拒绝 g1–g7 | Runtime Verified | Runtime Verified | E2E | ✅ | ✅ 禁放行 API 面 |
| `decision.issued` 留痕 | Runtime Verified | Runtime Verified | E2E | ✅ | ✅ |
| `receipt.emitted` 凭证 | Implemented+Tested | Runtime Verified | E2E | ✅ | ✅ |
| **主体归属（谁批准的）** | **Code only（恒 system）** | **Runtime Verified** | **E2E** | ✅ | ✅ before/after 对照 |
| **决策因果还原** | **Implemented（零调用）** | **E2E Verified** | **E2E + Acceptance** | ✅ | ✅ 负向（破坏事实可检出） |
| **一致性对账 reconcile** | **Implemented（零调用）** | **E2E Verified** | E2E | ✅ | ✅ 三类检查全中 |
| **凭证离线核验** | **Implemented（库级孤岛）** | **E2E Verified** | E2E | ✅ | ✅ 链校验 |
| **证据归档/聚合** | **Wired（无生产者）** | **E2E Verified** | E2E | ✅ | ✅ 幂等 + 空段负向 |
| **租户归属** | **Code only（止于服务层）** | **Runtime Verified** | E2E | ✅ | ✅ 重启恢复 + 冲突不回写 |
| **上下文契约** | **不存在** | **Tested + Runtime** | Static + E2E | ✅ | ✅ 负空间断言 |
| **LLM 出网治理** | **无（轮内出口绕过预算）** | **Tested + Runtime** | Static + Negative | ✅（单点） | ✅ 五出口零出网 |
| **持久化失败语义** | Partially（仅异步路径有界） | **Runtime Verified** | Fault injection | ✅ | ✅ 故障注入 |
| **落盘定时器** | **不存在（文档虚构）** | **Runtime Verified** | E2E（读物理文件） | ✅ | ✅ |
| **拒绝出口唯一性** | 有第二（死）发射点 | **Runtime Verified** | Static | ✅ | ✅ |
| **安全测试面** | 不存在 | **Tested** | Test | — | ✅ 70 条（含出网） |
| **CI 门禁** | 不存在 | **Implemented（未验证）** | — | ❌ **未 push ⇒ 未跑过** | ❌ |
| **原生壳 UI 行为** | Not Verified | **Not Verified（显式 skip）** | — | ❌ | ❌ |

---

## 17. Final GAP Matrix（最终 GAP 矩阵）

| GAP | Original Finding | Remediation | Verification | Final Status |
|---|---|---|---|---|
| 1 | LLM 出口无统一治理；轮内系统出口绕过会话预算 | `LLMClient._egress_guard` 单点闸；Negative Space 登记 | security 16 用例（allow/deny/异常 + 契约对照） | **Partially Closed**（预算面闭合；决策面**有意不计划**） |
| 2 | 强同步/公开 flush 无重试上限 | 单一判据 `_failure_state_reached` 覆盖三路径 | 故障注入 5 用例 | **Closed** |
| 3 | PySide6 0% 覆盖 + 收集债务 | `importorskip` 显式 skip；新增无 GUI 契约漂移检测 | 无 `--ignore` 全量跑通 | **Partially Closed**（收集闭合；UI 行为仍不可本地验证） |
| 4 | `tests/security` 不存在 | 新建 49 用例 | 全部通过 | **Closed** |
| 5 | acceptance/e2e 空壳 | acceptance 9 / e2e 40 / integration 6 | 全部通过 | **Closed** |
| 6 | 无 CI | `.github/workflows/ci.yml` + 静态闸 | **本地命令通过；Actions 未跑** | **Environment Blocked** |
| 7 | AuditSystem 生产调用 0 | 服务方法 + 路由 + 两壳 UI + 凭证核验 | E2E + Acceptance | **Closed** |
| 8 | EvidenceCollector 无生产者 | `archive_task_evidence`（冻结产生规则） | E2E 3 + Acceptance 2 + 负向 | **Closed** |
| 9 | 无 Context Propagation Contract | 可执行契约 + 位置契约 + 负空间断言 | 17 条不变量用例 | **Partially Closed**（契约建立；跨会话标识符**有意不引入**） |
| 10 | tenant 不进事件载荷 | `Envelope.tenant_id` + 日志优先恢复 | E2E 4 + Acceptance | **Closed** |
| 11 | 主体恒 `system` | 三态通道 + 全装配点声明 + fail-closed | E2E + before/after 对照 | **Closed** |
| 12 | `_reject` 死代码 | 删除 + 唯一发射点断言 | AST 不变量 | **Closed** |
| 13 | flush 定时器不存在 | 真实定时器 + 文档同源 | E2E 2（含故障负向） | **Closed** |

---

## 18. Remaining Limitations（残余限制）

**完全诚实，不掩饰：**

1. **未提交、未 push。** 全部改动在工作区。任何"已修复"都不等于已发布。
2. **CI 从未运行。** YAML 合法、命令本地通过，但 Actions 上零执行史。首次 push 后**必须**核对首次运行结果（尤其 PySide6 安装与 `COV_FLOOR`）。
3. **原生壳 UI 行为仍不可本地验证。** `desktop_native/` 的 731 行 `main_window.py` 无执行证据。
4. **LLM 出网无决策/凭证**（有意）。"LLM 调用受授权"不成立；改变需架构裁定。
5. **`call_id` 位置不统一**（L-17）—— 已登记为契约，统一化未做。
6. **L-1 头回落面未闭合**：未登记会话仍回落客户端租户头（服务仅绑 `127.0.0.1` 缓解）。租户**持久化**残余已闭合，**派生**残余未闭合。
7. **L-3 审批二级复合键未收敛**（`_pending` 仍以裸 `seq` 为键）。
8. **L-14 `ag.ctx.persistence` 死传播**未动。
9. **L-15 / L-16 定时任务语义**待产品决策，未动。
10. **未做的验证**：ACP/CLI 真实交互会话未驱动；Web 壳真跑未做（仅静态 + 既有单测）；MCP/网络链路未复验；mutation 测试未做；`docs/` 全量声明未逐条核对（仅核对了 README 与 LIMITATIONS 的治理段与计数）。

---

## 19. Documentation Changes（文档变更）

| 文件 | 变更 |
|---|---|
| `LIMITATIONS.md` | 新增 §0（本轮修复总表）；§1 计数更新（1944 / 79% / 无需 `--ignore` / 信封 10 字段）；L-1/L-4/L-5/L-6/L-7/L-8/L-10/L-11/L-12/L-13 状态更新；**新增 L-17/L-18/L-19**；**新增"负空间"专节**；§4 修复状态 |
| `README.md` | 计数 1751→1944；治理段重写：读侧可达 + 凭证核验 + 单点出网闸 + 负空间指引；删除已失效的"`verify_receipt` 未接外壳"自陈 |
| `pyharness/core/llm.py` | `_egress_guard` docstring（边界理由 + 三态 + Negative Space） |
| `pyharness/core/tools_executor.py` | 偏离 5/11 更新；`_reject` 删除处的取证记录 |
| `pyharness/governance/context.py` | `principal_of` 三态契约 docstring |
| `pyharness/events/envelope.py` | `tenant_id` 字段的"为何在信封而非载荷"说明 |
| `pyharness/persistence.py` | 定时器归属纠正（三处） |
| `pyharness/core/session.py` | `open_session` 的"日志优先"租户恢复契约 |
| `tests/invariants/test_inv_context_and_paths.py` | 契约即文档（含位置契约与负空间） |

**未删除任何 limitation**；旧条目**全部保留**并更新状态。

---

## 20. Final Conclusion（最终结论）

**这个 Agent Runtime 现在哪些能力真正闭环了？**

**闭环（有真实运行证据 + 生产可达 + 独立佐证）：**
工具授权的 deny/allow 判定、guard 单调拒绝、人工审批的**重入判定**（非绕过）、决策留痕、
可校验凭证及其离线核验、决策因果还原、被拒清单（含"拦了且没执行"证据位）、一致性对账、
按任务段的证据归档、主体归属、租户归属与恢复、上下文契约、持久化失败语义、落盘定时器、
拒绝出口唯一性、LLM 出网的预算闸。

**未闭环（诚实标注）：** LLM 出网的**授权面**（有意不做）、解释型跨会话关联（有意不做）、
原生壳 UI 行为（环境受限）、CI 门禁（未 push 未跑）、L-1 头回落面、L-3 二级复合键、L-14。

**本轮最重要的方法学结论**：**"已构造 / 已注入 / 已订阅" 与 "运行时可达" 之间隔着一整条
生产调用路径**。`AuditSystem` 与 `EvidenceCollector` 是**最典型的实例** —— 它们已被正确构造、
正确注入、正确订阅、被完整单测覆盖，**却在生产零调用**。静态阅读无法发现这一点；
单测覆盖率高也无法发现这一点（测试直接调它们，当然通过）。
**只有"搜调用点、排除定义与测试"这一条，以及"从外壳入口真的跑一次"这一条，才能发现。**

在修复这些的过程中，**日志重放**这一设计反复证明其价值：事后构造的 spine 内存索引为空、
但日志重建完整 —— 这正是 P2（单一权威事实源）在工程上的具体红利。

---

**报告状态**：`IMPLEMENTED` / `TESTED` / `VERIFIED`（全量 + E2E + 负向已实跑）；
**`DONE` 未达成** —— 尚未提交、CI 未跑、原生壳未验证。这三项不在本轮授权范围内。

---

_文档时间戳：2026-09-20T17:09:56+08:00_
