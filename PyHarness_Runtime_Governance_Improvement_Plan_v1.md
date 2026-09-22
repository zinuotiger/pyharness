# PyHarness_Runtime_Governance_Improvement_Plan_v1

> **模式**：**M2 Planning**（根因分析 / 方案设计 / 范围界定）—— **不含 M3 Implementation**
> **输入**：`Functional_Runtime_Audit_PyHarness_v2.0_Report.md`（M1 审计，14 条 Finding，全部 `AUDITED`）
> **基线**：`HEAD = cbb2033` @ 2026-09-17
> **硬约束**：**不新增架构** · **优先最小闭环** · 每项修复均为**原位改动**
> **本轮未修改任何代码，未执行 git 操作**（本计划文件本身按用户具名指令写入，见 §10）
> **状态**：本计划**尚未被授权**。以下任何一项进入 M3 均需用户对**具体条目**的显式授权。

## 修订记录（本文件自身的防漂移）

| 修订 | 日期 | 触发 | 变更 |
|---|---|---|---|
| **v1.0** | 2026-09-17 | M1 审计交付（14 条 Finding） | 初版：逐条分析 + A/B/C 分类 + H/C/A 分档 + 批次划分 |
| **v1.1** | 2026-09-17 | **Context Propagation Contract 调查** + D-1~D-4 裁定 | ① F-03 根因升级为系统性（新增 §2.5/§2.6）；② **新增 Finding F-16~F-20**（14 → **19** 条）；③ A/B/C 重排；④ B1 由 11 条 → **16 条**，B3 扩为 **F-03 + F-19**，B4' 撤销；⑤ §9.1 决策台账全部落地 |

> **Finding 计数口径变更说明**：v1.0 为 14 条（F-01~F-14），v1.1 为 **19 条**（+ F-16~F-20）。
> F-15 自始为"**未检查清单**"，两版均**不计数**。引用本文件的历史结论时，**请注明版本**。

---

## 1. 结论先行

**M1 → M2 的根因分析推翻了一条 Finding 的范围判定，暴露了一条此前未知的硬约束，并进一步证明该判定不是单点遗漏而是系统性状态。**

| # | M2 关键结论 |
|---|---|
| **①** | **F-03 不是桌面壳缺陷，而是全壳缺陷。** M1 报告称"两个桌面壳不设 `ctx.channel`"，实测**所有外壳都拿不到**：`ToolExecutor` 真正收到的 ctx 是 `create_agent` 产出的 **`ag.ctx`**，而 `ag.ctx` **从不拷贝 `channel`**；CLI/ACP 写到的是**外壳门面 ctx**（另一个对象）。⇒ **生产链上每一次 `decision.issued` 的主体都是 `SYSTEM`**。**F-03 升为 P0，且 M1 报告 §3 F-03 的范围陈述被本计划 §2 取代。** |
| **②** | **F-03 不能按"1 行透传"修。** 原生壳的通道名 `"desktop-native"` **不在** `HUMAN_CHANNELS`（`cli/web/acp/desktop`）内，`Principal.from_legacy_by("desktop-native")` **实测抛 `APR-503`**（fail-closed）。天真透传会让原生壳的**每一次工具调用直接失败**（把静默错归属升级为硬故障）。 |
| **③** | **F-03 不是单点遗漏 —— 不存在 Context Propagation Contract。** 实测：**57 个被读取的 ctx 字段中 28 个不在 `ag.ctx` 上**；生产里有 **6 处手写 ctx 仿制品**（`service.py:143/370/397/437` · `orchestration.py:77` · `engine.py:344` · `cli.py:1023`）；同一类缺口有**四种互不一致的应对策略**。F-03 只是**唯一未被掩盖**的那个 —— 因为只有它的兜底值（`SYSTEM`）是"语义上有意义的合法值"。**登记为 F-17。** |
| **④** | **F-03 与 F-19 耦合，必须双通道同改。** 原生壳审批通道被**硬编码为 `"desktop"`**（`engine.py:538`），`ApplicationService.channel`（`"desktop-native"`）到不了 provider。若只接治理层，同一次调用会变成 `decision.issued=desktop-native` / `approval=desktop` 的**双源不一致**。⇒ **D-4 接受：F-19 纳入 B3。** |
| **⑤** | **"最小闭环"在本批里主要指向"下调声明"，而非"补建能力"。** F-01 / F-02 的真正最小动作是**把声明改到与实现一致**（A-1/A-4，改动不能变更运行时行为）；补建运行面会触发 **H-4（API 契约变更）** 且与"不新增架构"冲突。 |
| **⑥** | **F-10（死代码）与 F-02(B)（导出 `verify_chain`）看似"顺手"，实为 Hard Stop。** 删除代码 = **H-1**；变更公共导出 = **H-4**。二者**不得**以"低风险小改动"处理。 |

**分类结果**（Finding 级：**19 条** = F-01~F-14 + F-16~F-20；F-15 为"未检查清单"不计数）：

| 类别 | 条数 | 覆盖 |
|---|---|---|
| **A Must Fix** | **19**（Finding 级） | **F-03**（P0）· F-01 · F-02 · F-04~F-14 · F-16 · F-17 · F-18 · F-19 · F-20 |
| **B Accept As Limitation** | **9**（**能力**级，跨 7 条 Finding） | 见 §4.2 B-1~B-9 —— 记录**不予补建**的那部分能力 |
| **C Deferred** | **0**（Finding 级） | — （**F-13 已由 D-3 裁定**，转入 A 类声明措辞调整） |

> **A 与 B 不是同一维度，必须分开读**：A 是"这个 Finding 要动"，B 是"这个能力我们不补"。
> 例如 **F-04** 在 A（登记缺口）**同时**在 B（接受生产面不补建）—— 二者不冲突。
> 若把 B 也按 Finding 计，会与 A **重复计数**，故 **B 按能力项计**。
>
> **展开为变更项**：**19 项**（每个 Finding 一项，见 §4.1）—— 其中
> **16 项落在自动层**（A-1 / A-2 / A-4，零运行时行为变更）·
> **1 项（F-09）命中 C-1**（需逐项确认）·
> **2 项（F-03 · F-19）命中 H-5 + H-6**（永不自动）。
> 校验：16 + 1 + 2 = 19 ✓

**建议的 M3 首批（若获授权）**：**Batch-1 = 16 条纯声明同步与限制登记** —— F-11 / F-12 / F-05 / F-08 / F-06 / F-07 / F-04 / F-14 / F-10(a) / F-02(A) / F-01(A) / F-13 / F-16 / F-17 / F-18 / F-20，全部落在 **A-1 / A-2 / A-4** 自动层，零运行时行为变更，可一次交付、一次验证。
**其余各自独立成批**：**B2 = F-09**（C-1）· **B3 = F-03 + F-19**（H-5 + H-6）· **B4 = F-10(b)**（H-1）。
（原 B4' = F-02(B)/F-01(B) 补公共面 —— **已由 D-2 否决，撤销**。）

---

## 2. M1 报告的更正（Re-audit of F-03）

> 本节按 Closure State Machine 的"**reopening**"语义处理：新证据触及 F-03 同一能力面 ⇒ F-03 **回到 `OPEN` 并立即重判**，原 `AUDITED` 判定作废。**M1 报告的 F-03 范围陈述以本节为准**；M1 报告文件本身**本轮不修改**（M2 无写权限，见 §10），其更正需求登记为 **F-16**（见 §4.4）。

### 2.1 证据链（全部 L3）

```
1) build_spine(cfg, sid=...)            → spine 无 channel 属性
2) create_agent(sid, spine, cfg)        → ag.ctx 无 channel 属性
3) GovernanceContext.principal_of(ag.ctx)
   → Principal(kind=SYSTEM, id='system', channel=None)
```

**`ag.ctx` 就是 `ToolExecutor.execute()` 收到的那个对象** —— 路径为
`make_runner.run_for_task`（`engine.py:813`）→ `_agent_ctx_of`（`engine.py:832`）
→ `loop.wake(env, ctx=ag_ctx)` → `agent_loop.py:280` `ctx.tools.execute(call, ctx)`。

### 2.2 为什么 CLI / ACP 的赋值到不了

| 外壳 | 赋值点 | 赋给谁 | 是否到达 executor |
|---|---|---|---|
| CLI | `cli.py:776` / `cli.py:927` | **外壳门面 ctx** | ❌ 不是 `ag.ctx` |
| ACP | `acp.py:302` | **外壳门面 ctx** | ❌ 同上 |
| Desktop | —（无赋值） | — | ❌ |
| Desktop-Native | —（无赋值） | — | ❌ |

`create_agent`（`core/agent.py:461-481`）从 spine 拷贝约 **20 个属性**到 `ag.ctx`
（session/scope/tools/llm/loop/sys/storage/persistence/bus/registry/config/guard/
governance/approval/counters/sysprompt/compactor/goals/todos/ask/skills/plan），
**唯独没有 `channel`**。

**旁证（死数据）**：CLI 把通道写进任务元数据（`cli.py:850`、`cli.py:936`
`meta={"channel": ...}`），但**全库无任何消费者读取 `meta["channel"]`**
（`task_queue.py` 只存不读）。即：该值被写入两处、读取零处。

### 2.3 硬约束：`desktop-native` 不在人类通道白名单

```
HUMAN_CHANNELS        = ('cli', 'web', 'acp', 'desktop')     # governance/decision.py:57
approval.CHANNELS     = ('cli', 'web', 'acp', 'desktop')     # core/approval.py:87,103

Principal.from_legacy_by('cli')            -> Principal(HUMAN, 'cli', 'cli')
Principal.from_legacy_by('desktop')        -> Principal(HUMAN, 'desktop', 'desktop')
Principal.from_legacy_by('acp:c1')         -> Principal(HUMAN, 'c1', 'acp')
Principal.from_legacy_by('desktop-native') -> RAISES ApprovalError [APR-503]   ← 实测
```

而 `desktop_native/controller.py:36` 传给 `ApplicationService` 的通道名**正是**
`"desktop-native"`。**⇒ 任何"把 `self.channel` 透传到 ctx"的修复，若不先解决白名单，
都会让原生壳每次工具调用抛 `APR-503` 而失败。**

> 附带确认：`principal_of` 对 `None` 是安全的（`context.py:79` 用
> `ch or _FRAMEWORK_BY` 兜底为 `"system"`），故当前的静默降级**不会抛异常** ——
> 这正是它长期未被发现的原因。

### 2.4 严重性重判

| 维度 | 判定 |
|---|---|
| 是否授权绕过 | **否**。`DecisionEngine.decide()` 中 `principal` 仅出现于字段定义（`decision.py:199`）、签名（`:298`）与赋值（`:335`），**从不参与 verdict** ⇒ guard 链与审批判定不受影响。 |
| 是否为"错误结论" | **是**。审计轨上的 `principal_id` 对**每一次人类发起的调用**都写入 `"system"` —— 不是"缺失"，而是**写入了错误值**。消费者（人或下游工具）读该字段会得到**明确错误**的身份归属。 |
| 是否影响凭证 | **是**。`principal_*` 进入 `receipt.py:92-94` 的 `content_hash` 受保护集合 ⇒ 修好后**凭证哈希会变**（历史凭证与新生凭证不可直接比对）。 |
| **结论** | **P0**（skill 风险模型 P0(a)：声称提供但运行不可达，且使消费者得到错误结果）。**非** P0(c)（无权限旁路）。 |

### 2.5 根因升级：F-03 是系统性状态的一个实例（M2 补充调查）

按用户指示执行 Context Propagation Contract 调查（只读、运行时实测），结论是
**根因方向与"单点遗漏"假设相反**：

**方法**：从 `pyharness/` 全量源码提取所有从 ctx 读取的字段名，对真实 `ag.ctx` 逐个 `hasattr` 检验。

```
READ fields total: 57
PRESENT on ag.ctx: 29
ABSENT  on ag.ctx: 28
```

**28 个缺失字段的定性**：

| 类 | 字段 | 定性 |
|---|---|---|
| **A. 外壳门面字段**（按设计从另一 ctx 读） | `engine_spine` `make_runner` `task_queue` `task_runner` `shell` `repair` `render` `redact` `locator` `_session` `settings` `cfg` `log` `agent` `get_secret` | 非缺陷 |
| **B. 可选字段**（`getattr` 默认 `None` 是正确语义） | `task_id` `fts_last_seq` `_approval_owner_sid` `_budget_owner_sid` `_guard_owner_sid` | 非缺陷 |
| **C. 有可用回落链** | `headless`（→ `self._headless`）· `session_id`/`sid`（→ `sess.sid` 优先） | 已被接住 |
| **D. 调用点临时伪造 ctx 补齐** | `owner` `owner_channel` `round_seq` `subagent_depth` | 已被掩盖 |
| **E. 静默落到语义错误的默认值** | **`channel`** | **F-03 —— 唯一未被掩盖的一个** |

**证据一 —— 生产里有 6 处手写 ctx 仿制品**（无一处是统一传播层）：

```
service.py:143    copy.copy(self.ctx) + SimpleNamespace(**vars(ctx))   (session=None)
service.py:370    jobs:     owner, owner_channel, scope, tools, budget
service.py:397    FTS:      session, task_queue
service.py:437    subagent: round_seq, …
orchestration.py:77          subagent_depth
engine.py:344                plugin ctx
cli.py:1023                  FTS index: config
```

**证据二 —— 同类缺口有四种互不一致的应对策略**：

| # | 策略 | 实例 | 失败方向 |
|---|---|---|---|
| 1 | 局部伪造 ctx | `owner` / `owner_channel` / `round_seq` / `subagent_depth` | 掩盖 |
| 2 | 构造器回落 | approval `_channel`/`_headless`；spill 的 sid 链 | 接住 |
| 3 | fail-closed 抛错 | `jobs.start` → `EVT-100`（`jobs.py:266`） | 安全 |
| 4 | **防御式静默默认** | **`channel` → `SYSTEM`** | **错误值** |

> **结论**：**从来没有传播层；`channel` 只是唯一没人给它打补丁的字段。** F-03 之所以是唯一暴露的缺陷，
> 机制上因为它**同时**满足两点：① 消费侧用 `getattr(ctx,"channel",None)` 防御式读取；
> ② 兜底值 `_FRAMEWORK_BY = "system"` 是一个**语义上有意义的合法值**（不是 `None`、不抛错）。
> 这解释了它为何能长期不被发现。
>
> **登记为 F-17。** 按"不新增架构"约束，**本轮不引入传播契约** —— 只补 `channel` 这一条通路（B3），
> 其余同类缺口**保持现状并登记**。**这意味着 B3 之后，同类缺陷对该类新字段仍然潜在。**

### 2.6 附带发现：审批通道双源（F-19）

实测（`build_runner_components` 路径）：

```
approval._channel       = 'desktop'    # engine.py:538 硬编码
_ensure_channel(ag.ctx) = 'desktop'
ag.ctx.headless present? -> False      # 回落 self._headless=False
```

`ApplicationService.channel`（`"desktop-native"`）**到不了 provider**；且该路径**不走
`attach_engine_to_ctx`**（后者才覆盖 `_channel`）。

⇒ **D-1 选择保留 `desktop-native` 语义后，若只把 `channel` 接进治理层而不管审批层**，
同一次工具调用会在两处出现不同主体（`decision.issued=desktop-native` / 审批路径 `desktop`）——
**归属从"两边都错"变成"两边不一致"，不是改善**。故 **D-4 接受：F-19 纳入 B3，双通道同改**。

---

## 3. 逐条 Finding 分析

> 每条给出：Problem / Root Cause / Runtime Impact / Fix Strategy / Risk / Priority。
> `H/C/A` 列为**助手侧单向否决（veto）**判定 —— 只能收紧、不能放宽；最终是否执行由用户对条目标注 `[auto]` / `[confirm]` 决定。

---

### F-03 · 全壳主体归属失效

| 字段 | 内容 |
|---|---|
| **Priority** | **P0**（M2 重判，原 P1） |
| **Problem** | 生产链上每一次 `decision.issued` 的 `principal_kind` 恒为 `system`，`principal_channel` 恒为 `None`。治理留痕的"谁"在全壳失效。 |
| **Root Cause** | **不是"漏了一个字段"，而是"不存在传播层"（F-17）**。`create_agent` 把 spine 的组件逐个拷进 `ag.ctx` 时**漏了 `channel`**；而各外壳只把通道写在自己的门面 ctx 上。**没有任何一层负责把"外壳身份"下沉到 agent ctx** —— 同类字段靠 **6 处手写 ctx 仿制品**逐个打补丁，`channel` 是**唯一没人打补丁的**（详见 §2.5）。它之所以暴露，是因为消费侧用 `getattr(ctx,"channel",None)` **防御式读取**，且兜底值 `SYSTEM` 是**语义上有意义的合法值**（不抛错、不是 `None`）⇒ 静默。次级根因：`desktop-native` 不在人类通道白名单，使直接透传不可行（§2.3）；审批通道另有硬编码 `"desktop"`（F-19，§2.6）。 |
| **Runtime Impact** | ① 审计不可答"谁"（README 治理叙事的核心承诺之一）；② `receipt.content_hash` 以错误主体入哈希；③ **不阻断执行、不报错** ⇒ 静默；④ 与 F-19 叠加时形成**双源不一致**（治理层与审批层主体不同）。 |
| **Fix Strategy** | **原位补线，不新增对象，且必须双通道同改**（D-1 + D-4 已裁定）：<br>**(a) 白名单**：`governance/decision.py:57` `HUMAN_CHANNELS` 与 `core/approval.py:87,103` `CHANNELS`/`_HUMAN_CHANNELS` 同增 `"desktop-native"` —— **两处必须同改**（`decision.py:36` 有一致性测试断言同源）。爆炸半径见 §5.1。<br>**(b) 装配层**：`attach_engine_to_ctx`（`engine.py:959`）**已收到 `channel` 参数**（现仅用于 `spine.approval._channel`）⇒ 增补 `spine.channel = channel`；`ApplicationService` 两处 spine 装配点（`service.py:_engine_runner_for` / `public_spine_for`）增补 `spine.channel = self.channel`；**同时取消 `engine.py:538` 的 `channel="desktop"` 硬编码，改为由通道来源统一供给**（F-19）。<br>**(c) agent 层**：`create_agent` 增补 `ag.ctx.channel = getattr(spine, "channel", None)`（**与既有 22 个属性同款写法**）。<br>**(d) 决策**：D-1 已选"扩白名单保留 `desktop-native`"；D-4 已定 F-19 并入本批。 |
| **Risk** | **高**。① 触 **H-5**（改变既有事件 `decision.issued` 的**actor attribution**）；② 触 **H-6**（`CHANNELS`/`_HUMAN_CHANNELS` 是**审批路径与信任名单的准入白名单** —— 扩容会连带新开通信任名单资格与裁决人资格，见 §5.1）；③ 触 **C-4**（状态归属）；④ 凭证哈希变更 ⇒ 需评估历史凭证兼容性；⑤ 原生壳不可自动化验证（L-6 / 0% 覆盖）⇒ 该壳的修复**无法用自动化回归证明**。 |
| **H/C/A** | **H-5 + H-6 + C-4 → Hard Stop Tier，永不自动执行** |
| **Evidence Level** | **L3** |
| **关联** | **F-17**（系统性根因，本条的母项）· **F-19**（审批通道双源，必须同批） |

---

### F-02 · 凭证核验面为测试专用

| 字段 | 内容 |
|---|---|
| **Priority** | **P1** |
| **Problem** | README:11 声明 `receipt.emitted` 为「带 `prev_hash` 链、**可独立校验**」的凭证；实测链校验器 `verify_chain` **仅在测试内被调用**，且**未出现在包级 `__all__`**；四外壳对 `receipt` **零命中**。 |
| **Root Cause** | **交付层级错位**（非实现错误）。S4/M4 的验收判据（`REFACTOR_PLAN.md:460`）写的是「需 `verify(receipt) -> bool`」——**按库级函数交付即可满足**。实现严格照此交付并配了不变量测试；缺的是 README 把**库级 API** 表述成了**产品级能力**。次级原因：`verify_receipt` 进了 `__all__` 而 `verify_chain` / `rebuild_from_log` 没进，属导出面手滑。 |
| **Runtime Impact** | 无运行时故障。消费者**能**经 `pyharness.governance.verify_receipt` 校验单条凭证；链条校验需知道子模块路径 `pyharness.governance.receipt`。**没有** CLI / HTTP / 工具面。 |
| **Fix Strategy** | **两条互斥路线，建议取 (A)（见 §5.2 决策矩阵）**：<br>**(A) 下调声明**（推荐）：README:11 的「可独立校验」补足限定语，明确为**库级 API**、未接外壳 ⇒ 声明与实现一致，**零运行时风险**。<br>**(B) 补齐公共 API 面**：把 `verify_chain` / `rebuild_from_log` 补进 `governance/__init__.py` 的导入与 `__all__` ⇒ 触 **H-4**。 |
| **Risk** | 路线 (A)：**极低**（纯文本，不能变更运行时行为 ⇒ **A-1**）。<br>路线 (B)：**中**——`H-4` 公共导出契约变更，且需同步 `S4-M4_CHANGE_REPORT.md` 等历史文献的导出清单。 |
| **H/C/A** | (A) = **A-1** ✅ 可自动｜(B) = **H-4** ⛔ |
| **Evidence Level** | **L1** |

---

### F-01 · AuditSystem 零运行路径

| 字段 | 内容 |
|---|---|
| **Priority** | **P1** |
| **Problem** | `AuditSystem` 四方法（`causal_chain` / `denied_report` / `reconcile` / `legacy_session_audit`）生产调用点 **全为 0**；`engine.py:661` 构造即注入 `governance.audit` 后无任何使用。设计文档 M7 的验收判据「**一条命令**还原一次拒绝」**无对应命令**。 |
| **Root Cause** | **交付边界收在半路**。S5-3b 的交付范围自述为「**只构造、只注入**」（`engine.py:656` 原文），即作者**明知**未接运行链并有意停在此处；但该停在 `LIMITATIONS.md §1` 只是一句括注，**未登记进 §2 的限制表 L-1~L-9** ⇒ 读者按"限制表"检索会漏掉。**根因是登记位置，不是实现。** |
| **Runtime Impact** | 治理链产生的数据**在盘上**（`decision.issued` / `receipt.emitted` 已 L3 证实），但**无产品内途径**做因果还原。README:74「审计 ✅」由**旧遥测** `telemetry.session_audit`（类型计数聚合）满足 —— 该能力**真存在**，但**不含决策因果**。 |
| **Fix Strategy** | **(A) 登记为限制**（推荐，最小）：在 `LIMITATIONS.md §2` 增一条 L-10，写明 `AuditSystem` 已构造注入但无调用路径、M7 未交付。<br>**(B) 补最小只读面**：扩展既有 `stats` 子命令或既有 `/api/sessions/{sid}/telemetry` 响应 ⇒ 触 **H-4**（CLI / HTTP 契约变更），且**新增读取能力**触 **C-1**。 |
| **Risk** | (A)：**极低**（**A-4**：标注"已实现、未交付"）。<br>(B)：**中高**——`H-4` + `C-1`，且顺带引入"审计输出格式"这一未定义契约 ⇒ 与"不新增架构"张力较大。 |
| **H/C/A** | (A) = **A-4** ✅ 可自动｜(B) = **H-4 + C-1** ⛔ |
| **Evidence Level** | **L2**（装配点存在）→ 无 L3 |

---

### F-06 · L-9「无 LICENSE」已过期

| 字段 | 内容 |
|---|---|
| **Priority** | **P1** |
| **Problem** | `LIMITATIONS.md:43`（L-9）记「无 LICENSE ｜ 公开使用授权不明确 ｜ 待定」；实存 `LICENSE`（MIT），由 `cbb2033`（= HEAD = `origin/main`）引入。 |
| **Root Cause** | 该文件为**手工维护的发布态快照**，其维护动作发生在 `d3597a6`；`cbb2033` 落地 LICENSE 时**未回写**该表。属"**写下时为真、发布后转假**"的经典形态。 |
| **Runtime Impact** | `LIMITATIONS.md` 自述为"当前状态单一权威入口"，却对外给出**错误的授权状态**。方向为 **understated**（少报能力），不误导消费者，但恰好违背该文件的首要用途。 |
| **Fix Strategy** | 删除 L-9；或在 §2 表内标「**已解决**（`cbb2033` 引入 MIT）」。**不新增行、不改结构**。 |
| **Risk** | **极低**（纯声明校正 ⇒ **A-1**）。 |
| **H/C/A** | **A-1** ✅ 可自动 |
| **Evidence Level** | **L3** |

---

### F-07 · `origin/main = d3597a6` 过期（2 处副本）

| 字段 | 内容 |
|---|---|
| **Priority** | **P1** |
| **Problem** | `LIMITATIONS.md:31` 与 `:68` 两处声明修复「已随 `d3597a6` 发布（`origin/main` = `d3597a6`）」；实测 `origin/main = cbb2033`。 |
| **Root Cause** | 同 F-06 同源：手工发布态快照未随 `cbb2033` 回写。**两处副本**说明同一事实被硬编码两次，无单一来源。 |
| **Runtime Impact** | 读者对"已发布版本包含哪些修复"的判定错误。 |
| **Fix Strategy** | 两处副本同步为 `cbb2033`。**可选加固**（属独立小项，不在本轮必需）：把该 pin 改为指向仓库常量而非硬编码，从结构上消除该类副本缺陷 —— 但**该加固会引入新机制**，与"不新增架构"冲突，**本轮建议不做，仅登记**。 |
| **Risk** | **极低**（**A-1**）。 |
| **H/C/A** | **A-1** ✅ 可自动 |
| **Evidence Level** | **L3** |

---

### F-05 · `payload 模型 75` 过期（实测 77）

| 字段 | 内容 |
|---|---|
| **Priority** | **P2** |
| **Problem** | 同一事实的两处副本均为 75，实测 77。 |
| **Root Cause** | 事件词表从 75 增至 77 时，两处数字副本未随动。典型的"**一处改了、别处没改**"。 |
| **Runtime Impact** | 无（**understated**，不误导）。 |
| **Fix Strategy** | 两处同步为 77：`events/payload.py:4`（docstring ⇒ **A-2**）与 `LIMITATIONS.md:19`（表内数字 ⇒ **A-1**）。 |
| **Risk** | **极低**。 |
| **H/C/A** | **A-1 + A-2** ✅ 可自动 |
| **Evidence Level** | **L3** |

---

### F-08 · `CODE-MATRIX.md` 标称"当前 RC"落后 5 个 commit

| 字段 | 内容 |
|---|---|
| **Priority** | **P2** |
| **Problem** | 表头标「当前」并锚定 `e561712`；实测 HEAD `cbb2033`，距离 5 个 commit；表内"git 72 次提交"实测 77。 |
| **Root Cause** | 该文件**部分自认历史快照**（阶段 0~6 章节），但**总览表头引入了"当前"这一时态承诺** ⇒ 内部两套时间语义并存，表头必然随时过期。 |
| **Runtime Impact** | 无功能影响；影响对"当前 RC 修复状态"的信任。 |
| **Fix Strategy** | **最小**：表头"当前"改为**具体 commit + "数据截至该 commit"**，取消"始终最新"的隐含承诺。（**不建议**本轮重新测算刷新到 `cbb2033`——那会把一条声明校正扩张成一次数据重采。） |
| **Risk** | **极低**（**A-1/A-2**）。 |
| **H/C/A** | **A-2** ✅ 可自动 |
| **Evidence Level** | **L3** |

---

### F-09 · 4 个探针含字面占位路径

| 字段 | 内容 |
|---|---|
| **Priority** | **P2** |
| **Problem** | `probe_engine_tools.py:4` · `probe_multisession.py:4` · `probe_scene3_roundA.py:4` · `probe_tools_call.py:4` 把 `sys.path` 硬编码为 `C:/Users/<user>/Desktop/mini-harness`（实测该路径不存在）⇒ 在**任何**机器上 `import pyharness` 必然失败。 |
| **Root Cause** | **脱敏动作引入的回归**：公开仓库整理时用占位符替换真实用户名，同时破坏了可执行性。这 4 个脚本**没有**采用仓库其余探针（如 `probe_mcp_stdio.py`）的相对路径写法。 |
| **Runtime Impact** | 无（非产品代码）。但 README:48 以"补了**可重复**的真实探针"为该轮凭证 —— 其中 4 个**不可重复**。 |
| **Fix Strategy** | 改为 `Path(__file__).resolve().parents[1]` 式相对路径，与仓库既有探针一致。**4 个文件各 1 行**。 |
| **Risk** | **低**。① 触及 `scripts/`（非产品代码，不改运行时行为）；② 但净效果是**恢复脚本的运行能力** ⇒ 判 **C-1**（新增行为面），**需确认**。 |
| **H/C/A** | **C-1** → 需逐项确认（**不得**自动执行） |
| **Evidence Level** | **L3** |

---

### F-10 · `ToolExecutor._reject` 死代码

| 字段 | 内容 |
|---|---|
| **Priority** | **P2** |
| **Problem** | `tools_executor.py:640` 的 `_reject()` 调用点 **0 处**；模块 docstring（`:52`）仍把它列为四关之一的有效路径。 |
| **Root Cause** | S3-2-2 把 scope 前置的运行时所有权上提到 `GuardChain._evaluate_full`（`execute()` 注释与 `context.py:91-93` 均记载该次职责上提），executor 侧拒绝对应实现**失去调用者但未删除**。属迁移残留。 |
| **Runtime Impact** | 无行为影响（不可达）。危害是**读者据 docstring 误判存在第二条拒绝路径**，与实测的"单漏斗"结论冲突。 |
| **Fix Strategy** | **拆为两项，建议只做 (a)**：<br>**(a) 只改注释**：修正 `:52` 的路径列表 ⇒ **A-2**，可自动，立即消除误导。<br>**(b) 删除方法体**（约 20 行）⇒ 触 **H-1**，本轮**建议延后**（dead code 无运行时成本，删除的收益是整洁度而非正确性）。 |
| **Risk** | (a) **极低**；(b) **H-1 硬停**——若 `_reject` 将来被重新接线而**未同时补 `decision.issued`**，会破坏"`decision_id` 贯穿"不变量。 |
| **H/C/A** | (a) = **A-2** ✅ 可自动｜(b) = **H-1** ⛔ |
| **Evidence Level** | **L1** |

---

### F-11 · `governance/context.py` docstring 两处自相矛盾

| 字段 | 内容 |
|---|---|
| **Priority** | **P3** |
| **Problem** | `:22` 称「本阶段**不发射** `decision.issued`」（同文件 `:125` 即发射）；`:48` 称「receipts/evidence/audit **仍为形状占位**」（`:51-55` 与 `engine.py:669-671` 均标已接线）。 |
| **Root Cause** | 该 docstring 撰写于 S2-1 骨架期，后续阶段（S3-2-2 / S4 / S5）逐步接线时**未回写模块头**。 |
| **Runtime Impact** | 无。属"注释 ≠ 事实"，读者按 docstring 会**低估**已接线范围。 |
| **Fix Strategy** | 删除/改写两处过期表述（**A-2**）。 |
| **Risk** | **极低**。 |
| **H/C/A** | **A-2** ✅ 可自动 |
| **Evidence Level** | **L1** |

---

### F-12 · `engine.py:_build_governance` docstring 描述已被 S3 取代

| 字段 | 内容 |
|---|---|
| **Priority** | **P3** |
| **Problem** | `:440-442` 称「运行期仍是 `ctx.guard.evaluate`……运行期向治理层问询（authorize）**属 S3**」。实测运行期为 `ctx.governance.authorize`（`tools_executor.py:476`），`ctx.guard.evaluate` **无调用点**（仅注释命中）。 |
| **Root Cause** | 同 F-11：S2-3 期文档未随 S3-2-2 回写。 |
| **Runtime Impact** | 无。该句的**结论**（"治理层无执行权"）仍为真，仅路径描述过期。 |
| **Fix Strategy** | 改写为"运行期关 2 经 `ctx.governance.authorize`（S3-2-2 起）"（**A-2**）。 |
| **Risk** | **极低**。 |
| **H/C/A** | **A-2** ✅ 可自动 |
| **Evidence Level** | **L1** |

---

### F-04 · EvidenceCollector 有订阅者、无生产者

| 字段 | 内容 |
|---|---|
| **Priority** | **P2** |
| **Problem** | `engine.py:650-654` 的订阅**真接线**；但 `archive()`（唯一写点）与 `collect_for_task()` 生产调用点均 **0** ⇒ `evidence.archived` 永不产生，索引恒空。 |
| **Root Cause** | 交付刻意分阶段（模块 docstring 明列 S5-1 只做数据契约 + 写点，S5-2b 做装配订阅），**订阅面先落地、生产面未落地**。作者已在 `LIMITATIONS.md §1` 括注披露，**但未登记进 §2 限制表**。 |
| **Runtime Impact** | 无错误结论（空索引返回 `()`，不抛）。设计文档 M6「按 task_id 聚合完整证据链」运行期不可达。 |
| **Fix Strategy** | 在 §2 增一条限制（**A-4**）。 |
| **Risk** | **极低**。 |
| **H/C/A** | **A-4** ✅ 可自动 |
| **Evidence Level** | **L2** |

---

### F-14 · `tests/security` 不存在；`acceptance`/`e2e` 为空壳

| 字段 | 内容 |
|---|---|
| **Priority** | **P2** |
| **Problem** | `tests/security` 目录不存在；`tests/acceptance/`、`tests/e2e/` 各仅一个 0 字节 `__init__.py`。`INVARIANT_REGISTRY.md §0` 要求这两处承载带 Canonical ID 标注的用例；设计文档 M9 要求「至少各 1 条真实用例」。 |
| **Root Cause** | 目录骨架早建、用例未填。**已由作者如实披露**（`LIMITATIONS.md:41` L-7）。 |
| **Runtime Impact** | 无端到端 / 安全自动回归（L-7 自述，端到端靠人工脚本）。 |
| **Fix Strategy** | 保持现状并**接受为限制**；建议在 `INVARIANT_REGISTRY.md §0` 同步登记"要求 vs 实存"的当前状态，以消除注册表自身的内部不一致。 |
| **Risk** | **极低**（登记项）。 |
| **H/C/A** | **A-4** ✅ 可自动 |
| **Evidence Level** | **L3** |

---

### F-13 · INV 编号三处口径不一

| 字段 | 内容 |
|---|---|
| **Priority** | **P2** |
| **Problem** | `README.md:11` / `LIMITATIONS.md:20` 用 **INV-01~05**；`pyproject.toml` marker 与 `CODE-MATRIX.md:49` 用 **INV-01~09**；`docs/INVARIANT_REGISTRY.md` 自述为 **INV-01~09 的唯一权威来源**。 |
| **Root Cause** | **编号面处于"已裁定、未迁移"的中间态**：注册表自述 `Status = migrated` + **19 处 `pending authorization`** 待迁移引用 ⇒ 分歧**已被作者知道且有意暂存**，非疏漏。 |
| **Runtime Impact** | 无。 |
| **Fix Strategy** | **需要一个决定，不宜助手代为选择**：① 把 README/LIMITATIONS 改为注册表口径（并注明"架构不变量子集"的定义来源）；**或** ② 在注册表内显式定义"架构不变量子集 = INV-01~05"。 |
| **Risk** | **极低**（纯文本）。但**属未决决策**，故本轮**不纳入 Must Fix**。 |
| **H/C/A** | **A-1/A-2**（裁定后可自动）——**但前置为用户决策** |
| **Evidence Level** | **L1/L3** |

---

## 4. 分类

### 4.1 A — Must Fix（进入闭环）

| ID | 条目 | 路线 | H/C/A | 运行时行为变更 |
|---|---|---|---|---|
| **F-03** | 全壳主体归属（P0） | 四跳补线（含白名单）+ D-1/D-4 | **H-5 + H-6** | **是** |
| **F-19** | 原生壳审批通道双源（P1） | 取消 `channel="desktop"` 硬编码，与 F-03 **同批** | **H-5 + H-6** | **是** |
| **F-02(A)** | README「可独立校验」加限定语 | A-1 | A-1 | 否 |
| **F-01(A)** | 登记 AuditSystem 未交付（L-10） | A-4 | A-4 | 否 |
| **F-04** | 登记 evidence 生产面缺失 | A-4 | A-4 | 否 |
| **F-14** | 登记测试面缺失（含注册表同步） | A-4 | A-4 | 否 |
| **F-17** | 登记"无传播契约"系统状态（含 §2.5 证据） | A-4 | A-4 | 否 |
| **F-18** | 登记决策载荷无租户归属 | A-4 | A-4 | 否 |
| **F-20** | 登记 `ag.ctx.persistence` 死传播 | A-4 | A-4 | 否 |
| **F-05** | payload 模型数 75→77（2 处） | A-1+A-2 | A-1/A-2 | 否 |
| **F-06** | L-9 LICENSE 过期 | A-1 | A-1 | 否 |
| **F-07** | `origin/main` pin（2 处） | A-1 | A-1 | 否 |
| **F-08** | CODE-MATRIX 表头取消"当前"承诺 | A-2 | A-2 | 否 |
| **F-13** | INV 编号口径声明（**D-3 已裁定**：runtime inventory 为事实来源，旧编号仅历史记录，不重整理范围） | A-1 | A-1 | 否 |
| **F-16** | M1 报告 F-03 措辞回写更正 | A-2 | A-2 | 否 |
| **F-09** | 4 个探针路径改相对 | C-1 | **C-1** | **是**（脚本恢复可运行） |
| **F-10(a)** | 只改 `:52` 注释（**不含删除**） | A-2 | A-2 | 否 |
| **F-11** | context.py docstring 校正 | A-2 | A-2 | 否 |
| **F-12** | engine.py docstring 校正 | A-2 | A-2 | 否 |

> **19 条 Finding 全部落在本表（19 项变更）**；F-10 行只代表**注释子项**，其删除子项见 §4.2 B-3。
> 档位分布：**16 项 A 自动层**（F-02(A) · F-01(A) · F-04 · F-14 · F-17 · F-18 · F-20 · F-05 · F-06 · F-07 · F-08 · F-13 · F-16 · F-10(a) · F-11 · F-12 = A-1 / A-2 / A-4）
> · **1 项 C-1**（F-09）· **2 项 H-5 + H-6**（F-03 · F-19）。
> 校验：16 + 1 + 2 = 19 ✓ · **本表为权威口径**。

### 4.2 B — Accept As Limitation

> **本节是"能力层面的次级接受"，与 §4.1 的 Finding 主分类是两个维度。**
> 表中各项**所属 Finding 已在 §4.1 计为 A**（其 A 侧动作是登记或部分修复）；此处记录的是**该 Finding 中不予补建的那部分**。

| # | 接受项 | 所属 Finding | 理由 |
|---|---|---|---|
| **B-1** | 证据聚合（`EvidenceCollector` 生产面）运行期不可达 | **F-04** | 作者刻意分阶段交付；补建属独立立项 |
| **B-2** | 无端到端 / 安全自动回归（`tests/security` 缺失等） | **F-14** | 已由 L-7 如实披露 |
| **B-3** | `_reject` 方法体保留为死代码 | **F-10** | 无运行时成本；**H-1** 需单独授权 |
| **B-4** | `verify_chain` 未进包公共面 / 无外壳面 | **F-02** | **H-4**；且**不解决**运行时可达性 |
| **B-5** | `AuditSystem` 无读取入口 | **F-01** | **H-4 + C-1**；与"不新增架构"冲突 |
| **B-6** | 发布态 pin 仍为硬编码（`LIMITATIONS`） | **F-07** | 改为常量引用会引入新机制，与"不新增架构"冲突 |
| **B-7** | `CODE-MATRIX` 表内数据仍为 `e561712` 时点 | **F-08** | 本轮只取消"当前"承诺，不重采数据 |
| **B-8** | 决策载荷无租户归属；多租户治理策略不落地 | **F-18** | 设计文档 §4.2 已列 out of scope（v1.1）；本轮只登记 |
| **B-9** | **不引入 Context Propagation Contract** | **F-17** | 与"不新增架构"冲突；本轮只补 `channel` 一条通路并登记系统状态 |

> **B-4 / B-5 的触发条件**：仅当用户**否决** §5.2 / §5.3 的路线 (A)、选择补公共面时才转为待办；
> **D-2 已确认采用路线 (A)**，故 B-4 / B-5 即为本批的**最终处置**。
>
> **B-9 的残余（必须显式声明）**：B3 落地后，**同类缺口对该类新字段仍然潜在** ——
> 本批不解决"无传播契约"本身，只解决它的一个实例（`channel`）。
> 若将来新增需下沉到 `ag.ctx` 的字段，**同一缺陷会以同样方式复现**。

### 4.3 C — Deferred

**Finding 级 Deferred：0 条。** 原唯一候选项 **F-13 已由 D-3 裁定**（保持 runtime inventory 为事实来源，旧编号仅作历史记录，不重新整理范围）⇒ 转为 **A 类声明措辞调整**（见 §4.1）。

**非 Finding 级延期项**（源自 M1 报告 §F-15 未检查项，不属本轮 19 条 Finding）：

| 项 | 理由 |
|---|---|
| 真实 LLM / MCP / 搜索 / 流式探针复跑 | 需外部 key 与网络 |
| `desktop_native` 自动化验证（937 语句 / 0% 覆盖） | L-6 未计划 ⇒ **阻塞 F-03/F-19 的原生壳回归** |
| `architecture.html` 在线可达性核验 | M1 环境网络策略阻断 |
| `docs/architecture.svg` / `.png` 与代码一致性 | 未做逐项比对 |
| mutation 反证复跑（README:12 声称 14 次） | 属 S6-2b 阶段记录 |

### 4.4 新增登记项 F-16 ~ F-20（M2 补充调查）

| ID | 级别 | 类别 | Problem | Root Cause | Runtime Impact | Fix Strategy | Risk | H/C/A | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| **F-16** | P3 | Claim Drift（元层） | M1 报告 §3 F-03 的范围陈述（"两个桌面壳不设 `ctx.channel`"）不准确 | M1 探针把**外壳门面 ctx** 而非 `ag.ctx` 传给了 `execute()` | 报告自身成为一处失真的声明副本 | 回写更正为"全壳 `ag.ctx` 不含 `channel`；桌面壳另有审批通道硬编码 `desktop`" | 极低 | **A-2** | L3 |
| **F-17** | **P1** | Missing Runtime Path（系统性） | **不存在 Context Propagation Contract**：57 个被读 ctx 字段中 **28 个不在 `ag.ctx`**；**6 处手写 ctx 仿制品**；**4 种互不一致的缺口策略** | 从设计上就没有"外壳身份 → spine → agent ctx"的传播层；每个消费者各自就地打补丁 | F-03 是其唯一未被掩盖的实例；**同类缺陷对该类新字段持续潜在** | **本轮只登记**（A-4）+ 只补 `channel` 一条通路（B3）。**不引入传播契约**（与"不新增架构"冲突） | 极低（登记） | **A-4** | **L3** |
| **F-18** | P2 | Evidence Gap | **决策载荷无租户归属**：ctx 层无 tenant 读者；**75 个事件载荷模型无一含 `tenant` 字段**（实测） | 多租户是 `ApplicationService` 层概念，未下沉到事件层；设计文档已列 out of scope | 审计轨**不可按租户归属**；跨租户取证无依据 | 登记进 `LIMITATIONS §2`（A-4）；不补 | 极低（登记） | **A-4** | **L3** |
| **F-19** | **P1** | Governance Gap | **原生壳审批通道硬编码 `"desktop"`**（`engine.py:538`），`ApplicationService.channel`（`"desktop-native"`）到不了 provider | 该路径不走 `attach_engine_to_ctx`（后者才覆盖 `_channel`），而 `build_runner_components` 把通道写死 | 两个桌面壳在**审批路径上不可区分**；与 F-03 叠加时，若只修治理层会造成**双源不一致** | **与 F-03 同批（D-4 已接受）**：取消硬编码，通道由统一来源供给 | **高**（H-5 + H-6） | **H-5 + H-6** | **L3** |
| **F-20** | P3 | Missing Runtime Path（反向） | `ag.ctx.persistence` **已填充但从不被读取**（`Ctx.__init__` 赋值，全库无 `ctx.persistence` 读取点） | 死传播 —— 装配时按"20 个组件"整齐拷贝，未按消费面裁剪 | 无运行时影响；扩大 ctx 契约面，掩盖真实依赖关系 | 登记（A-4）；**不建议本轮删除**（删字段会触 H-4 契约变更） | 极低 | **A-4** | **L3** |

> **F-17 与 F-03 的关系**：F-17 是**母项（系统性状态）**，F-03 是其**最高严重度实例**。
> 二者**状态机独立推进**：F-17 走"登记即闭合"（A-4）；F-03 走"修复 + 重审"（H 档，需独立授权）。
> **F-17 闭合不代表 F-03 闭合**，反之亦然。
>
> **不新增 Finding 的两项**（记录为说明，非缺陷）：
> - `request_id` —— **该概念不存在**（全库 0 读 0 写），非缺口；
> - `trace_id` —— **不作为 ctx 字段存在**；关联由 `Envelope.trace`（`{call_id}` / `{parent_seq}`，`envelope.py:49`）承担，是**逐事件**机制。无传播缺口。

---

## 5. 特别分析

### 5.1 F-03 · Principal Attribution（P0）

**问题本质**：不是"少了一个赋值"，而是**跨层身份没有承载体**。

```
外壳身份(人类通道)  ──✗──▶  spine  ──✗──▶  ag.ctx  ──▶  authorize()  ──▶  decision.issued
     cli/acp 写在这里            无 channel      无 channel      读不到 ⇒ SYSTEM
```

三层各有一处缺口，缺一不可：
1. `spine` 无 `channel` 字段（`build_spine` 不接受该参数；`attach_engine_to_ctx` 收了 `channel` 但只喂给 `spine.approval._channel`）
2. `create_agent` 不拷贝 `channel`
3. 没有任何消费方读 `Task.meta["channel"]`（CLI 已写入，**零消费者**）

**并叠加第四个缺口（F-19）**：审批 provider 的通道是 `engine.py:538` 的**硬编码 `"desktop"`**，
`ApplicationService.channel` 到不了它 ⇒ **治理层与审批层是两个独立通道来源**。

**决策点 D-1 —— 已裁定（2026-09-17，用户）：采用选项 B。**

| | ~~选项 A：原生壳复用 `"desktop"`~~ | ✅ **选项 B：把 `"desktop-native"` 加入白名单** |
|---|---|---|
| 裁定 | **否决** | **采纳** |
| 理由 | — | **保持 channel 粒度**，避免未来 `desktop` / `desktop-plugin` / `desktop-automation` 等场景混淆 |
| 改动 | 仅 `controller.py:36` 传参 | ① `decision.py:57` `HUMAN_CHANNELS` 增项；② `approval.py:87,103` `CHANNELS` / `_HUMAN_CHANNELS` 增项；③ **两处必须同改**（`decision.py:36` 有一致性测试断言同源） |
| H/C/A | 仍触 **H-5** | **H-5 + H-6** |

**扩白名单的精确爆炸半径（4 个行为消费者 + 1 个守卫测试）** —— 这是 H-6 判定的依据：

| 位置 | 原作用 | 扩白名单后的**新增**影响 |
|---|---|---|
| `approval.py:612,615` `_ensure_channel` | 无合法通道 → `APR-501` **自动拒** | 新接受 `desktop-native` 作为审批通道 |
| `approval.py:726` `_trust_hit` | 信任名单命中要求 `ch in CHANNELS` | **新开通信任名单资格**（信任命中 = 跳过重复审批） |
| `approval.py:461` / `:701` | 审批请求处理 / on 路径 | 同步放宽 |
| `approval.py:784` | `_HUMAN_CHANNELS` 校验裁决 `by` 合法性 | 新承认该通道可作**裁决人** |
| `decision.py:57` ↔ `approval.py:87,103` | 一致性测试断言两处同源 | **必须同改**，否则测试失败 |

> **助手的单向否决判定**：F-03 命中 **H-5 + H-6 + C-4** ⇒ **永不自动执行**。
> F-19 并入后**不改变档位**（同属 H-5 + H-6），但**改变了批次边界**：二者**必须同批**，
> 否则会出现 `decision.issued=desktop-native` / `approval=desktop` 的双源不一致。

**验证判据（须可命令表达）**：
① 装配真实引擎 → 跑一次工具调用 → 读回落盘 JSONL，断言 `decision.issued.payload.principal_kind != "system"`；
② **新增断言**：同一装配下 `principal_channel` 与审批路径 `_ensure_channel(ctx)` 的取值**一致**（该断言属 **C-6** 面 —— 判据可命令表达，但需新写测试）。

**残余（必须如实声明）**：**原生壳无法自动化验证**（L-6 / `desktop_native` 0% 覆盖，本机缺 PySide6）
⇒ 第 (b)(c) 跳的原生壳侧**只能人工验收**（真 tty + 人工脚本）。此项**不得在验收时被静默略过**。

---

### 5.2 F-02 · verify_chain production reachability（P1）

**事实分层**（三层常被混为一谈，必须分开看）：

| 层 | 现状 | 是否满足 README「可独立校验」 |
|---|---|---|
| **单条凭证核验** `verify_receipt` | ✅ 实现 + 测试 + **已进 `__all__`** | ✅ 满足（库级 API） |
| **链核验** `verify_chain` | ✅ 实现 + 测试；❌ **未进 `__all__`** | ⚠️ 可经 `pyharness.governance.receipt` 子模块路径取用，**不在包公共面** |
| **运行/外壳面** | ❌ 零（CLI / HTTP / 工具全无） | ❌ 不满足 |

**决策矩阵：**

| | 路线 (A) 下调声明 —— **推荐** | 路线 (B) 补公共 API |
|---|---|---|
| 动作 | README:11 补限定语："**库级 API，未接外壳**" | `governance/__init__.py` 导入 + `__all__` 增 `verify_chain` / `rebuild_from_log` |
| 文件数 | 1（README） | 2（`__init__.py` + 历史报告同步） |
| H/C/A | **A-1** ✅ 可自动 | **H-4** ⛔ |
| 运行时风险 | **零**（纯文本） | 中（公共契约面变更） |
| 遗留 | 运行面缺口**显式化**，成为坦白的限制 | 仍需另议是否补外壳面 |
| 助手建议 | **取 (A)** —— 理由：本批的硬约束是"不新增架构、优先最小闭环"；把一条**库级能力**如实描述为库级能力，是声明校正，不是能力削减。 | 若用户认为必须补导出，则应**独立成批**，不与本批混跑。 |

> **注意一个容易搞错的点**：路线 (B) 解决的是"包公共面"，**并不解决**"运行时可达性"。
> 即便补了 `__all__`，仍无 CLI/HTTP 面。**不要把它当成"补上了运行面"。**

---

### 5.3 F-01 · AuditSystem integration（P1）

**事实**：`AuditSystem` 是 **replay-only**（`engine.py:656` 自述"只构造、只注入"，其数据源是既有事件日志）。因此它**不需要新的数据**，只需要一个**读取入口**。

**但"加一个读取入口"在治理上并不便宜：**

| 维度 | 评估 |
|---|---|
| 数据是否已存在 | ✅ 是（`decision.issued` / `receipt.emitted` 已 L3 证实落盘） |
| 是否需新的存储 | ❌ 否（replay-only） |
| 是否需改治理层 | ❌ 否（`AuditSystem` 已完整实现且有 96% 行覆盖） |
| **新增入口的成本** | 新 CLI 子命令或扩展既有路由 ⇒ **H-4**；新增可读能力 ⇒ **C-1** |
| 与"不新增架构"的关系 | 补入口**不是**新架构（复用既有 replay 组件），但**是**新增对外契约面 |

**两条路线对比：**

| | 路线 (A) 登记为限制 —— **推荐** | 路线 (B) 补最小只读面 |
|---|---|---|
| 动作 | `LIMITATIONS.md §2` 增 L-10 | 扩展既有 `stats` 子命令（`cli.py:375`）或 `/api/sessions/{sid}/telemetry` |
| H/C/A | **A-4** ✅ 可自动 | **H-4 + C-1** ⛔ |
| 收益 | 消除"零调用路径却无登记"的缺口 | 兑现设计文档 M7 |
| 助手建议 | **取 (A)** | 若用户要兑现 M7，建议**单独立项**，先定审计输出契约 —— 那已超出"最小闭环" |

**关键区分**：路线 (A) **不降低** `AuditSystem` 的地位 —— 它是**已实现、已注入、有测试、未交付运行面**的完整组件。**登记不等于否定**。

---

### 5.4 F-13 · INV 编号（**D-3 已裁定**）

三处口径并存，且**注册表自述该迁移未完成**（`Status = migrated`，19 处 `pending authorization`）。

**D-3 裁定（2026-09-17，用户）：保持当前 runtime inventory 作为事实来源；旧编号只作为历史记录，不重新整理范围。**

⇒ 转为 **B1 内的一条声明动作**，不是 Deferred：

| 动作 | 落点 | 判据 |
|---|---|---|
| 明示 **runtime inventory（INV-01~05 用例面）为事实来源** | `README.md` · `LIMITATIONS.md` | 文内明示该口径 |
| 明示 **注册表 INV-01~09 为历史/编号层，不在本轮重整理** | `docs/INVARIANT_REGISTRY.md`（或仅在其引用处） | 不重编号、不改语义、不迁移 19 处引用 |
| 同步 `pyproject.toml` 的 pytest **marker 文案** | `pyproject.toml` | 文案与上述口径一致（**不触依赖/extra**） |

> **明确不做**：不重编号、不逐条迁移 19 处 `pending authorization` 引用、不重新裁定任何 INV 语义归属。
> 本轮只**加一句口径声明**，让三处口径的**关系**变得可读，而非把三处**统一**成一个。

---

## 6. H/C/A 汇总表（供用户标注 `[auto]` / `[confirm]`）

> 规则：**助手侧的 H/C/A 是"单向否决"** —— 只能把 `[auto]` 项**拉下来**，不能把 `[confirm]` 项**推上去**。
> **自主执行只能在 M2 内进入**；本表未标注 = `[confirm]`。

| 批次 | 项 | 文件范围 | 验证判据（可命令表达） | H/C/A | 助手 Triage |
|---|---|---|---|---|---|
| **B1** | F-11 改 context.py docstring | `pyharness/governance/context.py` | `grep -c "本阶段\*\*不发射\*\*\|仍为形状占位" <file>` → `0` | **A-2** | 可自动 |
| **B1** | F-12 改 engine.py docstring | `pyharness/engine.py` | `grep -c "运行期仍是 \`ctx.guard.evaluate\`" <file>` → `0` | **A-2** | 可自动 |
| **B1** | F-05 payload 数 75→77 | `pyharness/events/payload.py` · `LIMITATIONS.md` | `grep -c "75 payload 模型\|模型 \*\*75\*\*"` → `0` | **A-1+A-2** | 可自动 |
| **B1** | F-08 CODE-MATRIX 表头 | `CODE-MATRIX.md` | 表头含具体 commit 且**不含**"当前"承诺 | **A-2** | 可自动 |
| **B1** | F-06 L-9 LICENSE | `LIMITATIONS.md` | `grep -c "无 LICENSE"` → `0` | **A-1** | 可自动 |
| **B1** | F-07 origin/main pin ×2 | `LIMITATIONS.md` | `grep -c "d3597a6"` → `0` | **A-1** | 可自动 |
| **B1** | F-04 登记 evidence 缺口 | `LIMITATIONS.md` | §2 含 evidence 生产面条目 | **A-4** | 可自动 |
| **B1** | F-14 登记测试面 + 注册表同步 | `LIMITATIONS.md` · `docs/INVARIANT_REGISTRY.md` | §2 含测试面条目；注册表 §0 含实存状态 | **A-4** | 可自动 |
| **B1** | F-10(a) 改 `:52` 注释 | `pyharness/core/tools_executor.py` | `:52` 不再列 `_reject` 为有效路径 | **A-2** | 可自动 |
| **B1** | F-02(A) README 限定语 | `README.md` | README 措辞含"库级 API / 未接外壳"限定 | **A-1** | 可自动 |
| **B1** | F-01(A) 登记 L-10 | `LIMITATIONS.md` | §2 含 AuditSystem 条目 | **A-4** | 可自动 |
| **B1** | **F-13** 编号口径声明（D-3） | `README.md` · `LIMITATIONS.md` · `pyproject.toml` | 明示 runtime inventory 为事实来源、INV-01~09 为历史记录；**不重整理范围** | **A-1** | 可自动 |
| **B1** | **F-16** M1 报告 F-03 措辞回写 | `Functional_Runtime_Audit_PyHarness_v2.0_Report.md` | 该报告 §3 F-03 范围陈述与本计划 §2 一致 | **A-2** | 可自动 |
| **B1** | **F-17** 登记"无传播契约"系统状态 | `LIMITATIONS.md` | §2 含传播缺口条目（含 28/57 与 6 处仿制品） | **A-4** | 可自动 |
| **B1** | **F-18** 登记租户归属缺口 | `LIMITATIONS.md` | §2 含"决策载荷无 tenant"条目 | **A-4** | 可自动 |
| **B1** | **F-20** 登记 `ctx.persistence` 死传播 | `LIMITATIONS.md` | §2 含该死传播条目（**不删字段**） | **A-4** | 可自动 |
| **B2** | **F-09** 探针路径 | 4 个 `scripts/probe_*.py` | 4 脚本 `python <script>` 不再在 import 处失败 | **C-1** | ⚠️ **需确认** |
| **B3** | **F-03 + F-19** 主体归属（**双通道同批**） | `governance/decision.py` · `core/approval.py` · `engine.py` · `core/agent.py` · `application/service.py` · `desktop_native/controller.py` | ① 装配→调用→读回日志断言 `decision.issued.payload.principal_kind != "system"`；② **新增断言**：`principal_channel` 与审批路径 `_ensure_channel(ctx)` **取值一致** | **H-5 + H-6** | ⛔ **Hard Stop** |
| **B4** | **F-10(b)** 删除 `_reject` | `pyharness/core/tools_executor.py` | 全库无 `_reject` 定义与引用 | **H-1** | ⛔ **Hard Stop** |
| ~~B4'~~ | ~~F-02(B)/F-01(B) 补公共面~~ | — | — | ~~H-4~~ | **撤销（D-2 否决）** |

### 6.1 批次依赖声明（**必须前置声明；未声明即视为耦合**）

| 批次 | 内容 | 依赖 |
|---|---|---|
| **B1** | **16 条**纯声明同步与限制登记 | **无依赖**，各项互相独立，可整体执行 |
| **B2** | F-09 探针路径 | **独立于 B1**（不同文件、不同面） |
| **B3** | **F-03 + F-19 主体归属（双通道）** | **D-1 / D-4 已裁定**（前置条件已满足）；B3 内部**四跳**（白名单 / 装配层 / agent 层 / 审批 provider）**互相耦合，不可拆分执行** —— 拆开会产生双源不一致 |
| **B4** | F-10(b) 删 `_reject` | 独立于 B1（B1 只改注释，不删代码） |
| ~~B4'~~ | ~~F-02(B)/F-01(B) 补公共面~~ | **已撤销**（D-2 否决路线 B） |

> **B1 与 B2 可并行**；**B3、B4 各自单跑**，不与其他批次混合。
> **B3 不可拆分** —— 这是本计划中最强的一条耦合声明：F-03 与 F-19 共享同一"通道来源"概念，
> 任一单独落地都会使归属从"两边都错"恶化为"两边不一致"。

---

## 7. 非目标（Explicit Non-Goals）

本计划**明确不做**以下事项 —— 即使它们在技术上"顺手"：

1. **不新增任何模块、事件类型、配置项、ADL/ADR。**
2. **不重构 `principal_of()` 的语义。** 其语义已被 R1 裁定 + 测试守卫钉死（`context.py:16-20`）；F-03 的缺陷在**装配层**，不在语义层。
3. **不补建审计子系统 / 凭证核验外壳 / 证据聚合运行面**（D-2 已确认取路线 A）。三者均取"登记为限制"路线；补建属独立立项，与"不新增架构"冲突。
4. **不扩充白名单中 `"desktop-native"` 以外的任何项。** D-1 授权的白名单扩展**仅限**加入 `"desktop-native"` 这一个值（`decision.py:57` + `approval.py:87,103`）；**不得**顺带加入 `desktop-plugin` / `desktop-automation` / `web:*` 等任何未来取值 —— 那些留待各自独立论证。
5. **【新增】不引入 Context Propagation Contract（F-17）。** 本轮**只补 `channel` 一条通路**（B3）；**不建**统一的"外壳身份 → spine → agent ctx"传播层、**不建** ctx 字段清单校验、**不收敛**那 6 处手写 ctx 仿制品。⇒ **接受残余：同类缺陷对该类新字段仍然潜在。**
6. **【新增】不做多租户治理策略（F-18）。** 不为事件载荷增加 tenant 字段、不把 tenant 下沉到 ctx。仅登记该缺口。
7. **【新增】不删除 `ag.ctx.persistence`**（F-20）。删字段触 **H-4**；本轮仅登记其为死传播。同理**不裁剪** ctx 的其余未读字段。
8. **不动"`paused`/`resume`"等既有设计文档已声明不做的事项。**
9. **不重采 `CODE-MATRIX` 数据。** F-08 只取消"当前"这一时态承诺，不重新测算。
10. **不改 `pyproject.toml` 的依赖 / extra / 锁定面。** D-1（`uv.lock` 与 `pty`/`native` extra 不一致，L-6）为独立已知问题，**不在本批范围**。（注：F-13 只改 `pyproject.toml` 的 **pytest marker 文案**，不触依赖。）
11. **不补测试**（B3 的第 ② 条断言除外 —— 它是 H 档项自带的验证判据）。F-14 接受为限制。
12. **F-16 对 M1 审计报告的修改，严格限定为 §3 F-03 的范围措辞回写** —— 不动该报告的任何 Finding 结论、级别或证据。

---

## 8. 风险总表（含错误方向与阻塞判定）

| ID | 级别 | 错误方向 | 阻塞发布 | 成本估计 | 残余风险（须显式声明） |
|---|---|---|---|---|---|
| **F-03** | **P0** | overstated | ✅ 是 | 中（6 文件 + 1 项一致性断言） | **原生壳无法自动化验证（L-6）** ⇒ 该壳只能人工验收；凭证 `content_hash` 会变；**白名单扩容连带开通信任名单与裁决人资格**（§5.1） |
| **F-19** | **P1** | overstated | ✅ 是 | 低（取消 1 处硬编码 + 通道来源统一） | 必须与 F-03 **同批**，否则双源不一致；原生壳同样无法自动化验证 |
| **F-17** | P1 | neutral | ❌ | 0（仅登记） | **系统性状态不解决** ⇒ 同类缺陷对新字段仍潜在（B-9） |
| **F-01** | P1 | overstated | ✅ 是 | 低（取路线 A：1 段登记） | 运行面缺口保留，仅由声明显式化 |
| **F-02** | P1 | overstated | ✅ 是 | 极低（取路线 A：README 1 处） | 运行面缺口**保留**，仅由声明显式化 |
| **F-06** | P1 | understated | ✅ 是 | 极低 | 无 |
| **F-07** | P1 | neutral | ✅ 是 | 极低 | pin 仍为硬编码 ⇒ 同类副本缺陷**将来会复发**（本轮接受） |
| **F-18** | P2 | neutral | ❌ | 0（仅登记） | 审计轨**不可按租户归属**（延续；设计文档已列 out of scope） |
| **F-05** | P2 | understated | ❌ | 极低 | 无 |
| **F-08** | P2 | understated | ❌ | 极低 | 表内数据仍为 `e561712` 时点（本轮接受，不再承诺"当前"） |
| **F-09** | P2 | neutral | ❌ | 低（4 行） | 其余探针未逐一复跑 |
| **F-10** | P2 | neutral | ❌ | 极低（仅注释） | **方法体保留为死代码**（本轮接受） |
| **F-13** | P2 | neutral | ❌ | 极低（D-3 已裁定） | 三处口径**仍并存**，本轮只加声明、不统一编号 |
| **F-14** | P2 | neutral | ❌ | 0（仅登记） | 无端到端/安全自动回归（L-7 延续） |
| **F-11** | P3 | understated | ❌ | 极低 | 无 |
| **F-12** | P3 | understated | ❌ | 极低 | 无 |
| **F-04** | P2 | neutral | ❌ | 0（仅登记） | 证据聚合运行期不可达（延续） |
| **F-16** | P3 | understated | ❌ | 极低 | 无（报告自身措辞；仅局部回写） |
| **F-20** | P3 | neutral | ❌ | 0（仅登记） | `ctx.persistence` 死传播保留（本轮接受） |

> **共 19 行 = 19 条 Finding 全覆盖**（F-15 为"未检查清单"，不计数）。

---

## 9. M3 入口条件检查

| # | Gate 条件（Phase 6 入口） | 状态 |
|---|---|---|
| 1 | 用户对**具体** Fix Plan 给出**显式**授权（对象 + 范围） | ❌ **未给出** |
| 2 | 范围以**显式文件清单**给出（不得"按需修改"） | ✅ 本计划 §6 已逐项列出文件范围 |
| 3 | 每项有**可验证的完成判据** | ✅ 本计划 §6 已给出可命令表达判据 |
| 4 | 存在**显式非目标清单** | ✅ 见 §7 |
| 5 | 模式已为 **M3**（由用户声明） | ❌ **当前为 M2** |

**⇒ 门未开。本计划止于 M2，等待 Review 与授权。**

### 9.1 决策点台账（**全部已裁定**）

| # | 决策 | 裁定（2026-09-17，用户） | 阻塞解除 |
|---|---|---|---|
| **D-1** | `desktop-native` 的通道名 | ✅ **采用选项 B** —— 不复用 `"desktop"`，**保留 `"desktop-native"` 语义**，**扩充白名单**使其可进 `Principal` 解析链。理由：**保持 channel 粒度**，避免未来 `desktop` / `desktop-plugin` / `desktop-automation` 场景混淆 | **B3 前置已满足** |
| **D-2** | F-02 / F-01 取路线 (A) 还是 (B) | ✅ **取路线 (A)** —— 不补公共运行入口，调整公开声明范围；保留已实现事实，降低 README/文档中可能造成误解的完整性描述，**不扩展 `AuditSystem` runtime surface** | **B4' 撤销** |
| **D-3** | F-13 的 Canonical 编号口径 | ✅ **保持当前 runtime inventory 作为事实来源**；**旧编号只作历史记录，不重新整理范围** | **F-13 转入 B1** |
| **D-4** | F-19 是否纳入 B3 | ✅ **接受纳入** —— F-03 与 F-19 同属**主体归属链路（principal attribution chain）**，必须保证 `decision.issued` 与 **approval path** 使用**同一 channel source**，避免 `decision: desktop-native` / `approval: desktop` 的双来源不一致 | **B3 范围锁定为 F-03 + F-19** |

**⇒ 决策层面已无未决项。M3 入口的唯一缺口是"用户对 B1/B2/B3/B4 的具体授权"（Gate #1）与"模式切到 M3"（Gate #5）。**

---

## 10. 模式与写入声明

本文件按用户**具名指令**写入。按 Functional Runtime Audit v2.0 的 Output contract，
**M2 的产物默认以消息内容交付，写盘属文件编辑、需 M3 授权** —— 此处按用户对具名工件的显式指令写入，
并在此记录该偏离，以免与技能纪律混同（与 M1 报告 §8 同款处置）。

**v1.1 更新授权**：用户在确认 D-4 后，**显式授权**更新本文件的**规划记录**（F-16~F-20 登记 ·
F-03 根因描述 · A/B/C 分类 · B1/B3 批次规划 · 保留 M2 状态），并明确**不允许修改代码**。
本次更新**严格限于规划记录**，未触任何源代码、测试或配置。

**本轮除此之外未修改任何文件；未执行任何 git 写操作；未运行任何修复命令；未进入 M3。**

**架构范围未扩大**：v1.1 新增的 5 条 Finding 中，4 条（F-16/F-17/F-18/F-20）的处置是**登记**（A-4），
1 条（F-19）并入既有 B3。**未新增模块、事件类型、配置项或 ADR。**

---

> **本计划为 M2 出口产物。发现即停，等待人工 Review 与逐条授权。**
