# ARCHITECTURE_DECISION_RECORD.md

> **PyHarness Governed Agent Runtime v1.0 — Architecture Freeze 决策记录**
> 日期：2026-09-14 ｜ 基线：`main` @ `0cba75d`
> 状态：**FROZEN（架构冻结）**——本文档生效后，v1.0 的架构原则、ADR、范围边界与接口契约**不得再变更**；新增需求一律进入 §4 v1.1 候选列表。
> 依据：[REFACTOR_PLAN.md](REFACTOR_PLAN.md)（2026-09-14 审计）· [GOVERNED_AGENT_RUNTIME_DESIGN.md](GOVERNED_AGENT_RUNTIME_DESIGN.md)（2026-09-14 设计提案）
> 纪律：`.ai-coding/PROTOCOL.md` v0.3（FROZEN/OBSERVE）· `docs/ADD.md` §3（ADR 只增不改）· INV-01~09

---

## 0. 冻结声明

### 0.1 本次冻结锁定什么

| 锁定项 | 内容 | 变更路径 |
|---|---|---|
| **架构原则** | §1 的 G-1~G-7 七条 | 需新 ADR 取代（不可修改本条） |
| **ADR** | ADR-013 ~ ADR-018（§2） | ADR 只增不改，推翻则新建取代（`ADD.md:35`） |
| **v1.0 范围** | §3 的 Must / Must-Not | 移出范围需人工 Review |
| **接口契约** | 设计提案 §3 的 8 组接口（`Principal`/`PolicyEngine`/`DecisionEngine`/`DecisionReceipt`/`EvidenceCollector`/`AuditSystem`/`GovernanceContext`/`ApprovalChannel`） | 变更需记录在实现期的偏离说明 |
| **治理层目录** | `pyharness/governance/` 6 模块（§2 ADR-018） | 需新 ADR |
| **新增事件类型** | `policy.updated` / `decision.issued` / `receipt.emitted` / `evidence.archived` | 词表只增不改（`EVENT-SCHEMA.md:588`） |

### 0.2 本次冻结**不**锁定什么

- 实现细节（内部数据结构、缓存策略、线程模型）；
- 测试用例的具体断言（只锁"必须有不变量测试"这一要求）；
- 性能优化（如 `open_session` 全量重放的优化）；
- 文档措辞。

### 0.3 ADR 登记动作（**必须执行**，否则违反单一注册表纪律）

> ⚠️ **本文件是 Freeze 会话的决策记录；ADR 的权威注册表是 `docs/ADD.md`。**

`docs/ADD.md` 现状（已核实）：ADR-001 ~ **ADR-012**，索引表在 §2（12 行），正文在 §4，每条固定七节，`### ADR-NNN:标题` 格式，声明"编号不可变递增、只增不改"（`ADD.md:4,35`）。

**因此**：本文 §2 的 ADR-013 ~ ADR-018 必须回填进 `docs/ADD.md`：
1. §2 索引表追加 6 行；
2. §4 正文追加 6 条（沿用七节格式）；
3. 头部版本行 `12 条 ADR`→`18 条 ADR`（`ADD.md:5`）。

**禁止**：让本文档与 `docs/ADD.md` 形成两套并行 ADR 注册表。回填完成后，本文档降格为"Freeze 会话记录"，ADR 权威以 `ADD.md` 为准。

---

## 1. 最终 v1.0 架构原则

> 七条。前六条来自设计提案 §0，第七条为本次 Freeze 新增（范围纪律）。

### G-1 · 收编，不搬迁
已有机制（Guard Chain / Approval / Event Sourcing / Policy Ref / Binding Fingerprint / Audit Events）是**资产**，用适配与提级收编为显式层。**禁止**为了"架构对称"而搬运成熟代码。

**判定判据**：一个模块是否搬迁，只看"搬迁后是否降低了真实耦合"，不看"目录是否好看"。

### G-2 · 不新增第二真源
治理层的一切持久化都写成事件，落在既有 append-only JSONL 上。凭证、证据、审计视图全部是**派生或引用**。

**判定判据**：治理层产物中若出现"事件 payload 的全文副本"，即为违反（INV-G4 结构断言）。

### G-3 · 规则实现与规则治理分离
`g1–g7` 的**判定逻辑冻结不动**（规则库）；新增的是**规则之上**的策略对象、版本指纹、决策对象与凭证。

### G-4 · 强制点不搬家
唯一把"意图"变"动作"的关口仍是 `core/tools_executor.py` 四关管道。治理层**不持有执行权**。

### G-5 · 单调性不可逆
治理层继承 guard 的单调拒绝语义：只加严、无 relax、无 bypass。审批通过**不等于**放行（须重入求值）。

### G-6 · 文档即契约
接口一旦冻结进 specs，变更须走事件词表演进规则（`EVENT-SCHEMA.md:588`）。**文档与代码不一致 = 缺陷**，须有 `TraceabilityMatrix.check_consistency()` 可自动检出。

### G-7 · 范围即架构（**本次新增**）
v1.0 的架构边界由 §3 的 Must / Must-Not 显式定义。**"没做"必须是决定，不是遗漏**——任何 v1.0 未实现的能力，必须在 §4 有对应条目与目标版本。

---

## 2. ADR 列表（ADR-013 ~ ADR-018）

> 以下 6 条按 `docs/ADD.md` §3 固定格式撰写（元信息 / 背景 / 决策 / 后果 / 违反它的后果 / 备选方案对比 / 关联）。
> ADR-013 为总纲（对应 Freeze 决策的前置共识）；ADR-014~017 逐一对应你确认的 4 项技术决策；ADR-018 对应治理层目录确认。

---

### ADR-013：Governance Layer 作为独立权威层——授权与执行分离

**状态**：已接受 | **日期**：2026-09-14 | **落点**：v1.0（M2~M7），波及 `tools_executor` / `engine` / `agent` | **原则映射**：G-1 / G-2 / G-3 / G-4 / G-5

**背景**：PyHarness 已具备治理所需的**全部执行机制**——`core/tools_guard.py` 的 guard 单调拒绝链（g1–g7 + `_FORBIDDEN_API` 结构防线）、`core/approval.py` 的 HITL 裁决（TTL/合并/防重放/绑定指纹）、`core/session.py` 的 append-only 事件真源、`guard.rejected` 的 `policy_ref` 令牌、`tools_executor._approval_binding` 的授权↔执行绑定指纹、以及 9 种治理相关事件类型（实测：`approval.*`×4、`budget.paused`、`guard.evaluated`、`guard.rejected`、`scope.updated`、`syscheck.fail`）。

但这套机制是**散落的、未命名的、且部分未接线**的：`receipt`/`evidence`/`governance` 三个概念在代码中命中数为 **0**；`tools_guard.py:906` 的 `from_config()` 具备完整装配能力却在生产零调用点（`engine.py:474` 直构 `GuardChain`），导致 g1 g-schema 恒为 allow；「决策」退化为一个三值枚举 `Decision`（`:127`），无法回答"谁在什么策略下做的决定、依据哪一版、能否核验"。

若把这些机制继续散放在 `core/` 里逐点增强，将无法回答治理的核心问题，也无法向使用者证明"系统真的在治理"。需要的是**一个显式的权威层**。

**决策**：采纳**治理层独立成层，且只授权、不执行**。在 `pyharness/governance/` 下新建治理层，收编既有机制为五个显式构件（Policy / Decision / Receipt / Evidence / Audit，由 `GovernanceContext` 聚合）。

**治理层与执行层的关系被固定为**：

- `tools_executor` 关 2 向 `governance.authorize(call, ctx)` **请求授权**，取回 `Decision` 对象；
- 治理层**没有任何**执行、放行、翻案 API（延续 `tools_guard.py:629` 的 `_FORBIDDEN_API` 结构防线，并以 INV-G5 钉死）；
- 治理层与现有代码的**新增接缝只有 3 处**：① 关 2 出口形态（枚举 → 决策对象）② `ctx.governance` 单实例装配点 ③ 4 个新增事件类型。**除此之外执行路径一行不改。**

**明确不搬迁的 4 处（本 ADR 的一等决策）**：

| 模块 | 处置 | 理由 |
|---|---|---|
| `core/approval.py`（854 行） | **原位保留**，抽 `ApprovalChannel` 接口 | 其 TTL/合并/信任/防重放/跨会话隔离是**传输与时序语义**，非决策语义；且审批是决策的*输入通道*而非决策本身。搬迁会造成大面积回归而收益仅为"目录对称"（违 G-1） |
| `session` 事件流 | **原位保留**，`evidence.py` 只为其**派生索引** | 事件日志是 INV-01 唯一真源；搬迁=制造第二真源（违 G-2） |
| `tools_guard.py` 的 `g1–g7` 判定函数 | **原位保留**，被 `PolicyEngine` 引用 | 规则库，被 76 个单测钉死；物理搬迁留待单独一次 CND-07 |
| `core/tools_executor.py` 四关管道 | **原位保留** | 唯一强制点（G-4）；移入治理层即让治理层获得执行编排权 |

**后果**：收益——①决策可追（`decision_id` 贯穿全链）；②凭证可验（离线核验）；③越权必拦且可证（INV-05 保留）；④既有能力零丢失（收编而非重写）；⑤审计独立性成立（决策者 ≠ 执行者）。代价——①治理层与 `tools_executor` 之间新增一层调用（每次工具调用多一次 `authorize`）；②`Decision` 对象需保持 `__eq__`/`__str__` 兼容以免破坏既有 `d == "reject"` 断言（`tools_executor.py:449,563`）；③`events/payload.py` 的 `extra="forbid"` 会拒绝新字段入载荷，须遵守既有"传输元数据走 `trace`"先例或升词表版本。

**违反它的后果（具体场景）**：①若把 `approval.py` 搬进 `governance/decision.py`：854 行含 TTL 定时器与合并批的代码被拆散，76+ 交叉测试大面积变红，而架构收益为零——最终为了"恢复全绿"而放松断言，安全面反而下降。②若把 session events 搬进 `evidence.py`：证据层成为第二真源，"日志说 A、证据说 B"重现（正是 ADR-001 要消灭的场景），且 `session.replay`/FTS 索引/telemetry 全部要改道——一次重写。③若让治理层持有执行 API：治理层同时是决策者与执行者，审批独立性与审计客观性同时失效——此时"审计"只是自我声明。

**备选方案对比**：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 治理层独立成层，授权/执行分离，收编既有机制 | 决策可追/凭证可验/零丢失/审计独立 | 新增 3 处接缝 + `Decision` 兼容层 | ✅ 采纳 |
| 在 `core/` 内逐点增强（不建层） | 改动最小 | 无统一决策模型；无法回答治理核心问题；`receipt`/`evidence` 仍无处安放 | ❌ |
| 把 `approval`/`guard`/`executor` 全部搬进 `governance/` | 目录"纯粹" | 大规模回归 + 制造第二真源 + 治理层获得执行权 | ❌ |
| 引入外部治理框架/策略引擎 | 开箱能力 | 违 CONSTRAINTS-01 H 系列（禁现成 Agent 框架/重依赖）；黑盒无法逐行审查 | ❌ |

**关联**：ADR-003（guard 单调拒绝，本 ADR 继承其语义）、ADR-001（事件溯源真源，本 ADR 的 G-2 依据）、ADR-011（错误码契约）、ADR-014~018（本次同批决策）、`GOVERNED_AGENT_RUNTIME_DESIGN.md` §1/§2/§3、INV-G1~G6。

---

### ADR-014：v1.0 不实现运行时暂停——删除死态依赖，推迟至 v1.1 Runtime Recovery

**状态**：已接受 | **日期**：2026-09-14 | **落点**：v1.0 Phase 0（W2/死态清理），能力本身落 v1.1 | **原则映射**：G-7 / G-4

**背景**：`docs/DIS-CORE.md §1.4` 与 `MAP.md:226` 都承诺了 agent-loop 的"三态机"，`core/agent_loop.py:63` 据此声明 `LoopState = Literal["idle","running","paused","stopping","terminated"]`。

但实测（`grep` 确认）**只有 2 个状态被真正赋值**：`self.state = "running"`（`:160`）与 `= "idle"`（`:179`）。`paused`/`stopping`/`terminated` **全文无任何赋值点**。由此产生一串死代码与一个断链：

- `core/agent.py:412` 调用 `await self.ctx.loop.resume()`，而 `AgentLoop` **没有 `resume()` 方法**（方法全集已核实：无 resume）；
- 该调用的前置条件 `loop.state == "paused"` 永不成立，因此分支不可达；
- `agent.py` 自身的 docstring（`:40-43`）承认订阅"实际不命中任何会话事件"——即该分支**既死、若触发即 `AttributeError`**；
- 测试之所以全绿，是因为 `tests/unit/test_agent.py:507,516` 使用了自带 `resume()` 的 `FakeLoop` 替身（`test_agent.py:80`）——**替身掩盖了生产缺方法**；
- 连带死分支：`wake()` 的 `state in ("stopping","terminated")` 判断（`:354`）、`_must_stop` 闸 3 的 `"paused"` 比较（`:288`，而 `Scope.budget_state` 只返回 `ok/warn/exhausted`）、`agent.close` 的 `loop.state in ("running","paused")` 中的 `"paused"`（`agent.py:305`）。

需要澄清一个易混点：`Agent.state`（`init/ready/busy/stopping/closed`，`agent.py:228,268,274,303,333`）**五态全部真实在用**，`stopping` 由 `close()` 置位（`:303`）、由 `submit()` 检查（`:270`）——死态问题**只在 `AgentLoop`**。

**决策**：**v1.0 不实现真实暂停。**

1. 删除 `agent.py:412` 的 `loop.resume()` 调用及其不可达分支；
2. 清理 `AgentLoop` 中所有对 `paused`/`stopping`/`terminated` 的引用与判断；
3. 将 `LoopState` 收敛为**可达状态集**（`idle` / `running`），并在类型定义处标注 `paused` 为 v1.1 保留位；
4. **保留** `Agent.state` 的 `stopping`（真实在用）；
5. 同步修正 `MAP.md:39,226` 与 `DIS-CORE.md §1.4` 中"三态机"的措辞，使其与实现一致（G-6）；
6. 修正 `tests/unit/test_agent.py` 中依赖 `FakeLoop.resume` 的用例，改为断言"生产 `AgentLoop` 无 resume 方法"（`hasattr` 断言 AttributeError），**消除替身掩盖**。

**暂停能力（paused/resume）整体推迟至 v1.1，归入 Runtime Recovery**（见 §4 C-01）。

**后果**：收益——①消除"审批通过 → 恢复执行"的虚假承诺；②消除一处 `AttributeError` 埋雷；③消除替身掩盖的测试盲区；④state 字面量与实现一致，后续 Runtime Recovery 有干净起点。代价——①`agent.py:412` 的"审批 granted 后恢复 loop"路径确实不存在，须确认审批恢复由 `approval` 层与 `queue.suspended/resumed` 承担（现状如此：审批等待靠 `task_queue.pause/resume` 硬扛，不依赖 loop 暂停）；②若未来实现真暂停，需重新引入状态与 `resume()`。

**违反它的后果（具体场景）**：①若保留死分支不动：某次审批 `granted` 恰好命中该分支（例如后续给 `AgentLoop` 加了 `paused` 赋值），立即 `AttributeError` 打挂会话，且该错误会被归入 `CYC-999` 掩盖真实原因。②若"实现暂停"以图消除死代码：新增并发面（暂停点与工具执行、审批等待、取消传播的交错），触发 CND-01（并发/共享状态）全部排查项，而 v1.0 的验收目标（决策可追/凭证可验）与暂停能力**毫无关系**——用范围蔓延换风险。③若放任 `FakeLoop` 替身继续存在：测试持续给出"resume 已被覆盖"的**虚假信心**，未来真实现 resume 时会与替身契约漂移。

**备选方案对比**：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 删除死态依赖，暂停推迟 v1.1 | 零新增并发面；消除埋雷与假测试；state 与实现一致 | 需同步改文档与测试；确认无外部依赖者 | ✅ 采纳 |
| v1.0 实现真 paused/resume | 兑现 DIS §1.4 承诺 | 新增并发面（CND-01 全量）；与 v1.0 验收目标无关；风险远超收益 | ❌ |
| 保留死代码不动，仅补文档说明 | 改动最小 | 埋雷仍在；替身掩盖仍在；违反 G-6/G-7（"没做"未成为决定） | ❌ |

**关联**：ADR-013（G-4 授权/执行分离——暂停属运行时协调，不属治理）、ADR-016（Checkpoint 同归 Runtime Recovery）、§4 C-01、`agent_loop.py:63,160,179,288,354`、`agent.py:305,412`、`test_agent.py:80,507,516`。

---

### ADR-015：决策事件双轨并存——`guard.evaluated`（规则级）与 `decision.issued`（治理级）

**状态**：已接受 | **日期**：2026-09-14 | **落点**：v1.0（M3） | **原则映射**：G-2 / G-3 / G-6

**背景**：`tools_guard.py` 的 `GuardChain.evaluate()`（`:672`）当前对**每次**求值落一条 `guard.evaluated`（`:786` 的 `_audit`，payload = `{tool, decision, guard_ids, reasons}`），并在 reject 时额外落 `guard.rejected`（强同步）。这是 INV-04 的载体（"执行前必有 guard.evaluated"），语义为**规则级**：记录"哪些规则参与了对本次调用的求值、结论是什么"。

治理层需要的是**决策级**事件：回答"谁（Principal）在什么策略版本下（`policy_fingerprint`）作出了什么决定（verdict）、依据哪些策略引用（`policy_refs`）、针对哪一份输入（`inputs_digest`）"，并且有稳定主键 `decision_id` 供凭证与审计串联。

两者**不可互相替代**：

- 用 `decision.issued` 取代 `guard.evaluated`：会破坏 INV-04 的既有语义与测试面（`guard.evaluated` 被 76 个 guard 用例与 `tools_executor._require_wiring` 的 fail-closed 判据依赖），且丢失"逐规则参与情况"的细粒度审计能力；
- 用 `guard.evaluated` 代替 `decision.issued`：该 payload 只有 4 个字段（`extra="forbid"`），无法承载 Principal / 策略版本 / 输入摘要 / `decision_id`，且它缺失"未命中任何规则"与"全部放行"时的完整决策语义（现有实现只在命中规则时记名）。

**决策**：**双轨并存，职责分离。**

| 事件 | 层级 | 回答的问题 | 落点时机 | 强同步 |
|---|---|---|---|---|
| `guard.evaluated` | **规则级** | 哪些规则参与了求值、逐个结论如何 | 命中规则时（含 allow 汇总） | 否（既有行为） |
| `guard.rejected` | 规则级 | 哪条规则拒绝了、依据什么 policy_ref | 任一 reject（INV-05） | **是**（既有行为） |
| `decision.issued` | **治理级** | 谁在什么策略下作出什么决定、依据什么、针对哪份输入 | **每次**治理求值（含全部 allow） | **是** |
| `decision` 的凭证 | 凭证级 | 上述决策的可离线核验证明 | 产生凭证时（`receipt.emitted`） | **是** |

三条配套约束：

1. `decision.issued` **不是** `guard.evaluated` 的替代品，两者在同一调用上**同时存在**，通过 `call_id`（经 `Envelope.trace`）双向关联；
2. `guard.evaluated` **降级为细粒度审计**（保留全量，不做裁剪），以维持 INV-04 与既有测试不变；
3. 新增不变量 **INV-G1**：任何工具执行前必有同 `call_id` 的 `decision.issued`。

**后果**：收益——①治理语义显式（决策级）+ 审计细粒度（规则级）二者兼得；②INV-04 与 76 个 guard 用例**零改动**；③`decision_id` 为凭证/证据/审计提供稳定锚点；④治理层可在不改 `tools_guard` 判定逻辑（G-3）的前提下独立演进。代价——①每次工具调用的事件量增加（一条 `decision.issued`）；②两条事件的关联依赖 `trace.call_id`，须有配对测试；③`guard.evaluated` 的"规则级"定位需在 `EVENT-SCHEMA` 中写清，否则后续实践者会误以为它是治理决策事件。

**违反它的后果（具体场景）**：①若用 `decision.issued` 取代 `guard.evaluated`：INV-04 的"执行前必有 guard.evaluated"判据失效，`tools_executor._require_wiring`（`:520`）与 76 个 guard 用例需集体改写；一旦改写中放松了某条断言，就永久失去"逐规则参与情况"的审计能力——事后追查"到底是 g3 还是 g4 拦的"将无据可依。②若只用 `guard.evaluated` 兼做治理决策：因 payload `extra="forbid"` 无法写入 Principal 与策略版本，只能把治理要素塞进 `trace` 自由 dict——治理语义藏在信封里，可发现性与契约性同时下降，且"全部放行"路径无完整决策记录。③若两轨不建立 `call_id` 关联：凭证无法回溯到产生它的决策，审计链在第一批事件处即断。

**备选方案对比**：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 双轨并存（规则级 + 治理级） | 语义完整 + INV-04 零改动 + 细粒度审计保留 | 事件量增加；需配对测试 | ✅ 采纳 |
| `decision.issued` 取代 `guard.evaluated` | 事件精简 | 破坏 INV-04 与 76 用例；丢失规则级审计 | ❌ |
| 只用 `guard.evaluated` 兼做治理决策 | 零新增事件 | payload 装不下治理要素；全放行路径无完整记录；语义混淆 | ❌ |
| 把治理要素塞进 `guard.evaluated.trace` | 不改词表 | 治理语义不可发现、不可契约；违 G-6 | ❌ |

**关联**：ADR-013（G-3 规则实现/治理分离）、`tools_guard.py:672,786,803`、`tools_executor.py:449,520`、`events/payload.py`（`extra="forbid"`）、`EVENT-SCHEMA.md:298,313`（INV-04/05 引用点）、INV-G1。

---

### ADR-016：Checkpoint 不进入 v1.0——归 Runtime Recovery，非 Governance 核心

**状态**：已接受 | **日期**：2026-09-14 | **落点**：v1.0 明确排除；能力落 v1.1 | **原则映射**：G-7

**背景**：审计确认运行时**不存在**检查点机制（`checkpoint` 全库命中 0，仅文档中"落地检查点"一词指实现校验点，非机制）。现有最接近物有两个：`ScopeSnapshot`（`scope.py:184`，仅策略+预算的值拷贝）与 `SeqState`（seq 水位）。崩溃恢复走的是**全量重放**：`open_session()`（`session.py:417`）遍历 `persistence.replay()` 重建缓存，成本为 **O(全事件)**。

Governed Agent Runtime v1.0 的核心命题是"**决策可追、凭证可验、越权必拦**"——三者都在 Governance Layer，均**不依赖**检查点：

- 决策可追：靠 `decision.issued` 事件 + `trace` 关联；
- 凭证可验：靠 `DecisionReceipt.verify()` 对事件真源的重放核验；
- 越权必拦：靠四关管道 + guard 单调链（既有）。

而检查点解决的是另一个问题：**恢复成本与恢复点精度**（"回到哪个状态、还差什么"）。它属于 Runtime Recovery，会触及 `session`/`persistence`/`agent_loop`/`task_queue` 的状态模型，并引入"快照与事件真源的一致性"这一新的不变量面（快照本身可能成为第二真源，与 G-2 直接冲突，需专门设计）。

**决策**：**Checkpoint 不进入 v1.0。**

1. v1.0 的恢复一致性由**既有机制**承担：`repair` 全管线（F060）+ `open_session` 重放 + **新增的凭证重放核验**（`receipt.verify()` 在重放后结果不变，INV-G3）；
2. 检查点能力整体推迟至 **v1.1 Runtime Recovery**（见 §4 C-02），且届时必须与 `paused/resume`（C-01）**一并设计**——两者共享同一状态模型；
3. v1.0 不引入任何快照格式、不写任何快照文件、不新增相关事件类型。

**后果**：收益——①v1.0 范围聚焦治理核心，不摊薄；②避免"快照 vs 事件真源"的第二真源问题（G-2）；③恢复能力不退化（`repair` 已在，且凭证重放核验是**新增**的恢复期一致性保障）。代价——①大会话的崩溃恢复仍是 O(n) 全量重放（Phase 4 引入索引时应一并测量）；②"恢复到任意中间点"的能力在 v1.0 不可用。

**违反它的后果（具体场景）**：①若 v1.0 引入快照：必须同时定义"快照与事件冲突时谁对"——若答"快照优先"则快照成第二真源（违 ADR-001/G-2），若答"事件优先"则快照仅是缓存（那就不需要它，`rebuild_from_log` 已足够）；这个二难无法在 v1.0 范围内被充分论证。②若把检查点当作"治理能力"塞进 `governance/`：治理层将持有会话状态模型，与 ADR-013 的"治理层只授权不执行"红线冲突——治理层开始管运行时状态，审计独立性随之受损。③若为赶进度做"半个检查点"（只存不验）：恢复后无法判定是否与真源一致，比全量重放**更危险**——给出的是错误的一致性保证。

**备选方案对比**：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| v1.0 排除；用 repair + 重放 + 凭证核验承担恢复一致性 | 范围聚焦；无第二真源风险；恢复能力不退化 | 恢复仍 O(n)；无中间点恢复 | ✅ 采纳 |
| v1.0 实现会话级检查点 | 恢复快 | 触及核心状态模型；快照一致性二难；与治理范围无关；风险高 | ❌ |
| v1.0 实现"轻量检查点"（只存 seq 水位） | 改动小 | `SeqState` 已是 seq 水位，无增量收益；不解决恢复成本 | ❌ |

**关联**：ADR-014（paused/resume 同归 Runtime Recovery，须与检查点一并设计）、ADR-001（事件溯源真源，G-2 依据）、ADR-013、`scope.py:184`（`ScopeSnapshot`）、`session.py:369,417`（重建/开会话）、`persistence.py:430`（replay）、INV-G3、§4 C-02。

---

### ADR-017：Workflow 保持顺序步骤编排——不做 DAG，治理粒度下移到 segment

**状态**：已接受 | **日期**：2026-09-14 | **落点**：v1.0（确认现状，不改代码） | **原则映射**：G-1 / G-7

**背景**：审计核实全库**无 DAG、无依赖调度、无并行编排**：`core/workflow.py:45` 的 `WorkflowRunner.run` 就是一个 `for idx, intent in enumerate(steps)` 串行循环，仅 `stop_on_fail` 控制首败是否继续（`:64`）；`core/orchestration.py` 是 job/subagent → `AgentLoop` 的**适配层**，不是调度器；`core/plan_mode.py:478` 的 `plan_execute` 也是严格顺序。全库唯一并发是 `jobs.py` 的独立协程池（并发 ≤4）与 `subagent` 的递归深度上限，且 job 之间**相互独立**。

同时，治理所需的关键基础设施**已经存在**：`core/task_queue.py:404-429` 为每个任务写 `segment.start`（**强同步**）/`segment.end` 段锚，并以 `events_between(start_seq, end_seq)` 支持按段回放。**段锚天然就是治理的证据单元边界**。

若为"工作流治理"而引入 DAG 引擎：将引入节点依赖解析、并行调度、失败传播策略、部分失败回滚等一系列编排能力——这些是**编排能力**，不是**治理能力**。v1.0 的命题是"让每一步都可治理"，而不是"让编排更强"。

**决策**：**Workflow 保持顺序步骤编排器，不引入 DAG。治理粒度下移至 segment。**

1. `core/workflow.py` **不修改**（除既有的 `segment` 接入确认）；
2. 每个 workflow step 是一个 **governed task**：经由 `task_queue` 入队，拥有自己的 `segment.start/end`、自己的 `Decision`（`decision.issued`）与（如需）`DecisionReceipt`；
3. 因此"工作流被治理"**无需修改 workflow 模块**——治理在步骤粒度自动生效；
4. 若未来需要 DAG，作为**编排能力**单独提案（见 §4 C-03），不与治理层耦合。

**后果**：收益——①v1.0 零编排改动；②治理覆盖到每个步骤，粒度**比 DAG 级治理更细**；③段锚复用既有强同步机制，无新真源；④`workflow` 模块无需承担治理职责。代价——①不具备并行步骤执行能力（v1.0 接受）；②非 DAG 的工作流无法表达步骤间依赖（现状即如此，非本次退化）。

**违反它的后果（具体场景）**：①若 v1.0 引入 DAG：编排引擎的失败传播策略与 `task_queue` 的单飞泵（同一时刻仅一个 running）语义冲突，须先改造队列——而队列的串行保证正是审批挂起（`queue.suspended`）与段锚配对的前提，改造将连带冲击审批与审计两条已稳定的链路。②若把"工作流治理"实现为 workflow 模块内部的决策逻辑：治理逻辑会散落到编排层，与 ADR-013 的单一治理入口（`governance.authorize`）冲突，形成第二套决策路径——这是审计最应避免的形态。③若不做治理粒度下移（只做任务级治理）：工作流内的每个步骤将共享一个外层决策，无法回答"是哪一步被拒的"——审计粒度不足以支撑 v1.0 的 DoD。

**备选方案对比**：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 保持顺序编排 + 治理粒度下移到 segment | 零编排改动；粒度更细；复用强同步段锚 | 无并行步骤 | ✅ 采纳 |
| v1.0 引入 DAG 引擎 | 表达力强 | 与单飞泵语义冲突；冲击审批/段锚；属编排非治理 | ❌ |
| 只在 workflow 模块内做决策逻辑 | 实现直接 | 形成第二套决策路径；治理逻辑散落 | ❌ |

**关联**：ADR-013（单一治理入口）、`workflow.py:45,64`、`task_queue.py:404-429`（`open_segment`/`close_segment`）、`orchestration.py`（适配层定位）、§4 C-03。

---

### ADR-018：治理层目录与接口契约冻结

**状态**：已接受 | **日期**：2026-09-14 | **落点**：v1.0（M2~M7） | **原则映射**：G-1 / G-6

**背景**：设计提案 §3 定义了 8 组接口契约（`Principal` / `PolicyEngine`+`Policy`+`PolicyRule` / `DecisionEngine`+`Decision`+`ApprovalChannel` / `DecisionReceipt`+`ReceiptStore` / `Evidence`+`EvidenceCollector`+`TraceabilityMatrix` / `AuditSystem` / `GovernanceContext`）与 6 条治理不变量（INV-G1~G6）。接口需要**冻结**——否则实现期会各自演化，`decision_id` 的语义、`inputs_digest` 的算法、`verify()` 的 fail-closed 判据都会漂移，凭证互操作性丧失。

同时需固定一个**装配纪律**：治理上下文必须以**单实例**挂在 `ctx.governance` 上。`Ctx`（`agent.py:76`）当前已因 `create_agent` 追加约 25 个属性而退化为 Service Locator（`:467-488`）——若治理内容继续逐个挂散字段，将加剧该问题。

**决策**：**冻结治理层目录结构与接口契约。**

**目录**（新建，6 模块）：

```
pyharness/governance/
├── __init__.py     # 包门面：仅再导出 GovernanceContext 与公开类型
├── context.py      # GovernanceContext：单实例聚合 + authorize() 唯一入口 + principal_of()
├── policy.py       # PolicyEngine / PolicyRegistry / Policy / PolicyRule
├── decision.py     # Principal / Verdict / Decision / DecisionEngine / ApprovalChannel(Protocol)
├── receipt.py      # DecisionReceipt / ReceiptStore（emit / get / verify / digest_of）
├── evidence.py     # Evidence / EvidenceRef / EvidenceCollector / TraceabilityMatrix
└── audit.py        # AuditSystem（causal_chain / denied_report / reconcile / legacy_session_audit）
```

**依赖方向**（INV-08 扩展，强制）：`governance/` 只允许依赖 `pyharness.events`（读写）与 `pyharness.errors`；**禁止** import `tools_executor` / `llm` / `bus.plugin`。`tools_executor` 允许 import `governance`，反向禁止。

**契约冻结清单**：

| 冻结项 | 内容 |
|---|---|
| `ctx.governance` | 单实例挂载（**禁止**向 `Ctx` 追加治理散字段） |
| `authorize()` | `tool_executor` 关 2 的**唯一**调用入口 |
| `decision_id` | 凭证/证据/审计的稳定主键；uuid |
| `inputs_digest` | 算法 = 既有 `tools_executor._approval_binding`（复用而非重写），**不得**引入第二套参数规范化 |
| `verify()` | fail-closed：返回 False ⇒ 调用方必须拒绝执行（沿用 INV-05 纪律） |
| `Decision` 兼容面 | `__eq__`/`__str__` 必须与 `"allow"/"reject"/"approval"` 字面量可比较（保证 `tools_executor.py:449,563` 零改动） |
| `ApprovalChannel` | 由 `core/approval.py` **原位**实现（鸭子类型，不 import 治理层） |
| INV-G1~G6 | 必须有专项测试（`tests/invariants/`） |

**后果**：收益——①接口稳定，实现可并行；②凭证互操作（同一 `inputs_digest` 算法）；③`Ctx` 不再膨胀；④依赖方向可静态校验（INV-08 扩展）。代价——①接口冻结后新增能力需走 ADR；②`ApprovalChannel` 的鸭子类型实现需专门的一致性测试（接口与实现分离的固有代价）。

**违反它的后果（具体场景）**：①若 `inputs_digest` 各实现一套：审批时用 `tools_executor` 的 sha1(工具+规范化参数)、凭证核验时用另一套 canonical_json——"批准 A 执行 B"的防护在跨实现处失效，而这正是凭证存在的**唯一理由**。②若治理内容继续挂 `ctx.decision_engine` / `ctx.receipts` / `ctx.evidence` 等散字段：`Ctx` 从 ~33 属性涨到 ~40，任何能力缺失仍以 `getattr(..., None)` 静默跳过——治理层的缺失将不可发现（与"越权必拦"目标直接冲突）。③若 `verify()` 失败时调用方继续执行：凭证从"防篡改证明"退化为"装饰性日志"，比没有凭证更糟——给出的是错误的信任信号。④若 `governance/` 反向 import `tools_executor`：形成循环依赖，且治理层获得执行编排视角，为"治理层开始执行"打开缺口。

**备选方案对比**：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 冻结目录 + 接口契约 + 单实例装配 | 接口稳定/凭证互操作/依赖可校验 | 新增能力需走 ADR | ✅ 采纳 |
| 不冻结，实现期演化 | 灵活 | `decision_id`/`digest`/`verify` 语义漂移；凭证互操作丧失 | ❌ |
| 治理内容挂 ctx 散字段 | 少写一个类 | `Ctx` 持续膨胀；缺失静默不可见 | ❌ |
| `governance/` 与 `core/` 双向依赖 | 编码方便 | 循环依赖；治理层获得执行视角（违 G-4） | ❌ |

**关联**：ADR-013（授权/执行分离与 3 处接缝）、`GOVERNED_AGENT_RUNTIME_DESIGN.md` §3（8 组接口草案）、`agent.py:76,467-488`（Ctx 膨胀）、`tools_executor.py:218,449,563`、`approval.py:741`（`_require_human` → Principal 来源）、INV-08。

---

## 3. v1.0 范围冻结

### 3.1 Must（v1.0 的定义 —— 缺一不可）

| # | 交付项 | 冻结的验收判据 | 关联 ADR |
|---|---|---|---|
| **M1** | **接线收口**（W1–W5） | ① `PolicyEngine.from_config()` 成为 guard 链唯一装配入口且注入 `validator`（g1 不再是 no-op）；② 预算单信号（不再有两种终态）；③ F026 连败在主链生效；④ `session.append` 关闭可重入；⑤ `_closed` 时序修复 | ADR-013 |
| **M2** | **Policy 一等对象 + 内容哈希指纹** | `fingerprint(policy)` 随规则内容变化；`policy.updated` 落盘 | ADR-013/018 |
| **M3** | **Decision 对象 + 双轨事件** | 每次工具调用产生 `decision.issued`；`guard.evaluated` 保留全量；`Decision.__eq__("reject")` 为真 | ADR-015 |
| **M4** | **DecisionReceipt：emit + verify + 落盘** | 篡改任一字段 → `verify()` False；重启会话后 `verify()` 仍成立；fail-closed | ADR-018 |
| **M5** | **Principal 取代裸 `by` 字符串** | 身份白名单语义保留；`Principal ⇄ by` 双向往返无损 | ADR-013/018 |
| **M6** | **EvidenceCollector：段锚证据聚合** | 按 `task_id` 聚合完整证据链；无 payload 全文副本（INV-G4） | ADR-018 |
| **M7** | **AuditSystem.causal_chain** | 给定 `decision_id` 可还原"谁/何时/因何/依据哪版策略/结果" | ADR-018 |
| **M8** | **治理不变量测试 INV-G1~G6** | `tests/invariants/` 全绿且六条均有专项用例 | ADR-018 |
| **M9** | **激活空置测试面** | `tests/acceptance/` 与 `tests/security/` 各 ≥1 真实用例，含"批准 A 执行 B"必须被拒 | ADR-013 |
| **M10** | **端到端治理 Demo**（走真实外壳，无 mock 通道） | 四场景：策略拒绝 / 审批闭环 / 崩溃恢复后凭证仍可验 / 篡改凭证必拒 | 全部 |

### 3.2 Must-Not（v1.0 明确不做 —— † 为本次 Freeze 新确定）

| # | 不做 | 归属 | 关联 ADR |
|---|---|---|---|
| **N1** | 运行时暂停（paused/resume）† | v1.1 Runtime Recovery（C-01） | ADR-014 |
| **N2** | Checkpoint / 快照回滚 † | v1.1 Runtime Recovery（C-02） | ADR-016 |
| **N3** | Workflow DAG 引擎 † | v1.1+ 编排能力（C-03） | ADR-017 |
| **N4** | 策略外部化（YAML / 可插拔策略插件） | v1.1（C-04） | ADR-013 |
| **N5** | 数字签名 / 密钥治理 | v1.1（C-05） | ADR-018 |
| **N6** | 多租户治理策略 | v1.1（C-06） | ADR-013 |
| **N7** | `approval.py` / `g1–g7` / `tools_executor` 物理搬迁 | 不搬迁（G-1）；如需另走 CND-07 | ADR-013 |
| **N8** | 大会话恢复的性能优化（O(n) 重放优化） | Phase 4 引入索引时一并测量 | ADR-016 |

### 3.3 冻结后的变更规则

- 从 §3.1 移出某项：**需人工 Review**（DONE 降级为 PARTIAL）；
- 向 §3.1 加入新项：**需新 ADR**（ADR-019+），并说明为何不属 §4 候选；
- §3.2 任一项提前实现：**需新 ADR 取代** ADR-014/016/017 中对应条款（旧条目保留原貌）。

---

## 4. v1.1 候选列表

> 按"与 v1.0 的耦合度"排序；C-01/C-02 必须一并设计（共享运行时状态模型）。

| # | 候选 | 目标版本 | 为什么不在 v1.0 | 前置依赖 | 关联 ADR |
|---|---|---|---|---|---|
| **C-01** | **运行时暂停/恢复**（真实 `paused` 态 + `resume()`） | v1.1 Runtime Recovery | 属运行时协调，非治理核心；引入并发面（CND-01 全量） | 与 C-02 共享状态模型；须先定"恢复点"语义 | ADR-014 |
| **C-02** | **Checkpoint / 增量快照** | v1.1 Runtime Recovery | 触及 session/persistence/agent_loop 状态模型；须先解决"快照 vs 事件真源"的 G-2 冲突 | C-01；大会话重放测量数据 | ADR-016 |
| **C-03** | **Workflow DAG 引擎**（节点+依赖+并行+失败策略） | v1.1+ | 属编排能力；与 `task_queue` 单飞泵语义冲突，须先改造队列 | 队列语义改造（影响审批挂起与段锚配对） | ADR-017 |
| **C-04** | **策略外部化**（YAML 策略文件 / 可插拔策略插件） | v1.1 | v1.0 策略来源为 `config.security.*` + 内置规则，已够用 | M2 的 `Policy` 对象稳定后 | ADR-013 |
| **C-05** | **凭证数字签名 + 密钥治理** | v1.1 | v1.0 用内容哈希 + 前序哈希链（零外部依赖）已满足"防篡改" | M4 稳定；需密钥体系设计 | ADR-018 |
| **C-06** | **多租户治理策略** | v1.1 | 租户隔离本身尚需修（`desktop/app.py:35-36` 正则缺 `-`/`.` 使 `s-fork-*` 退化） | 租户隔离修复 | ADR-013 |
| **C-07** | **资源级权限**（并发上限 / 速率窗 / 时间窗） | v1.1 | v1.0 仅预算（token/成本）；`jobs` 并发上限硬编码 | Policy 资源域扩展 | ADR-013 |
| **C-08** | **系统级对账调度**（`syscheck` 定期触发） | v1.1 | v1.0 提供 `AuditSystem.reconcile()` 手动面；调度属运维 | M7 | ADR-018 |
| **C-09** | **治理策略模拟/预演**（"这条策略会不会拦到这个调用"） | v1.1+ | 非治理必需；`PolicyEngine` 只读面稳定后可加 | M2 | ADR-013 |
| **C-10** | **`g1–g7` 物理搬迁至 `governance/policy_rules.py`** | v1.1 | 纯搬迁零收益（违 G-1）；须单独走 CND-07 | M2 稳定 | ADR-013 |

---

## 5. 开发顺序

### 5.1 排序原则

1. **依赖先行**：M1 的 `from_config`/`validator` 注入是 M2 的入口，M2 的 `Policy` 是 M3 的输入，M3 的 `decision_id` 是 M4 的主键。
2. **风险隔离**：触及安全主干的两步（M3、M4）**必须分两次交付**，不得合并。
3. **每步可独立回退**：因治理层产物全是事件（只增不改），回退 = 停止消费，数据无损。
4. **每步出口全绿**：既有 1326+ 用例 + invariants；**禁止为适配新形态而放松安全断言**（CORE-03）。

### 5.2 开发顺序表

| 序 | 步骤 | 内容 | 交付物 | 触及安全主干 | 出口判据（Gate） |
|---|---|---|---|---|---|
| **S0** | **冻结登记**（文档） | ① ADR-013~018 回填 `docs/ADD.md`（索引 + 正文 + 头部条数）；② `MAP.md`/`DIS-CORE.md` 的"三态机"措辞修正（ADR-014）；③ `EVENT-SCHEMA.md` 预登记 4 个新事件类型的占位说明 | 文档改动 | 否 | `ADD.md` 18 条 ADR 自洽；无第二套 ADR 注册表 |
| **S1** | **Phase 0 接线收口** | M1：W1–W5 五项修复 + `engine.build_runner_components` 按职责拆分为 5 个子函数 + 5 处重复落盘适配器收敛 + 两套 repair 收敛 + 死代码清理（含 `_invoke`、死态依赖） | 代码 | **是**（engine/guard 装配） | ① g1 注入 validator 后**能**拒绝非法参数；② `cfg.security.guards.disabled` 生效；③ 预算单信号；④ 全量回归绿；⑤ `AgentLoop` 无 resume 且测试改为 `hasattr` 负断言 |
| **S2** | **治理骨架 + Policy** | M2：`governance/__init__.py` + `context.py` + `policy.py`；`PolicyEngine.from_config()` 接管 guard 链装配；`policy.updated` 事件 | 代码 | **是**（装配入口切换） | ① 策略指纹随内容变化；② **一致性测试**：治理层对同一 `(tool,args,scope)` 的判定与 `tools_guard` 逐例一致；③ 全量绿 |
| **S3** | **Decision 对象 + 双轨事件** | M3：`decision.py`（`Principal`/`Verdict`/`Decision`/`DecisionEngine`/`ApprovalChannel` Protocol）；关 2 出口升格；`decision.issued` 落盘；`ctx.governance` 挂载 | 代码 | **是**（guard/executor） | ① **等价性测试**：新对象 verdict 逐例等于旧枚举；② `d == "reject"` 断言零改动通过；③ INV-G1 专项测试；④ **全量回归**（CORE-02 高影响信号） |
| **S4** | **Decision Receipt + Principal** | M4/M5：`receipt.py`（`emit`/`verify`/`digest_of`）；`approval._grant_slots` → 落盘凭证；`Principal` 取代散字符串 | 代码 | **是**（审批校验） | ① 篡改必拒；② 重启后 `verify()` 成立（INV-G3）；③ "批准 A 执行 B"被拒（`tests/security/` 首例）；④ fail-closed 验证；⑤ 全量绿 |
| **S5** | **Evidence + Audit** | M6/M7：`evidence.py`（只存引用）+ `audit.py`（`causal_chain`）；`telemetry.session_audit` 转兼容适配器 | 代码 | 否（只读派生） | ① 按 `task_id` 聚合证据链；② INV-G4 结构断言（无全文副本）；③ 一条命令还原一次拒绝的完整因果链 |
| **S6** | **不变量 + 测试面** | M8/M9：`tests/invariants/` 重写覆盖 INV-01~09 + INV-G1~G6；激活 `tests/acceptance/`、`tests/security/` | 测试 | 否 | ① 六条 INV-G 均有专项用例；② 空置目录非空；③ 若暴露既有缺陷 → 记 `KEY-FINDINGS.md` 按 P 级分期，**不阻塞** |
| **S7** | **生产级 Demo** | M10：`scripts/probe_governance_e2e.py`（四场景）；刷新 `MAP.md`/`README.md`；`TraceabilityMatrix.check_consistency()` 接 CI 判据 | 代码+文档 | 否 | ① 四场景可重复执行且结果一致；② 文档与运行时真值一致（校验器返回空）；③ DoD 七条逐条打勾 |

### 5.3 并行与串行安排

- **可并行**：S5（Evidence/Audit，只读派生）与 S4 后半段（凭证核验）无写冲突，可在 S4 通过 Gate 后并行推进。
- **必须串行**：S0 → S1 → S2 → S3 → S4。原因：S1 的 `validator` 注入是 S2 `PolicyEngine` 接管装配的前提；S3 的 `decision_id` 是 S4 凭证的主键。
- **释放点**：S3 与 S4 各自**独立交付**（不得合并为一次提交）——两者都触及安全主干，合并会放大回归面且使回退粒度变粗。

### 5.4 v1.0 完成门（DoD）

> **任意一次工具调用，都能回答"谁在什么策略下做了什么决定、凭什么、有无凭证、能否独立核验"，并且这套回答本身是防篡改的。**

逐条：

1. **可追**：`decision_id` 贯穿 `tool.call → decision.issued → (approval.*) → receipt.emitted → tool.result`，无断链（INV-G1）。
2. **可验**：`receipt.verify()` 离线可跑；篡改任一字段 → False；事件重放后结果不变（INV-G3）。
3. **必拦**：critical 工具 reject 且审计可证"拦了且没执行"（INV-05）；"批准 A 执行 B"被拒。
4. **无第二真源**：`evidence`/`receipt` 只含引用与哈希（INV-G4）。
5. **无回归**：既有 1326+ 用例全绿；**没有任何测试为适配新形态而放松安全断言**。
6. **无死层**：治理层无执行/放行 API（INV-G5）；`AgentLoop` 无死态（ADR-014）。
7. **文档同步**：`ADD.md`(18 条 ADR) / `MAP.md` / `EVENT-SCHEMA.md`(77 型) 与代码一致；`TraceabilityMatrix.check_consistency()` 返回空列表。

---

## 附 A：Phase 0 待核实项（不猜测，列入 S1 核查）

| # | 待核实 | 已知证据 | 处理 |
|---|---|---|---|
| V-1 | `agent.submit()` 在**首次**唤醒时是否可行 | `agent.py:279` 调 `loop.wake(env)` **不传 ctx**；`agent_loop.py:357` 无 ctx 时回落 `self._ctx`，而 `_ctx` 仅在 `run()`（`:159`）内绑定 → 首次为 `None` → `:373` 抛 `CYC-999`。但生产路径 `engine.make_runner` 是 `loop.wake(env, ctx=ag_ctx)`（传 ctx）；`agent.submit` 未找到生产调用点 | S1 核实：若 `agent.submit` 无生产调用点 → 标记为遗留或删除；若被外壳调用 → 补 ctx 传递 |
| V-2 | 两套 repair 的实际调用面 | `persistence.SessionStore.repair`（`:470`）与 `repair.repair_session`（`:380`）语义分歧（前者隔离仅内存行号，坏行仍在主文件） | S1 收敛为单一 repair；确认无外部依赖 `SessionStore.repair` |
| V-3 | `test_agent.py` 中 `FakeLoop` 的契约与生产 `AgentLoop` 的差异面 | `FakeLoop`（`:62-81`）含 `resume()`，生产无 | S1 改为负断言（`hasattr(AgentLoop, "resume") is False`）并复查是否有其他替身掩盖 |
| V-4 | 全量测试的真实通过数 | `README.md:4`="1400 passed" vs `CODE-MATRIX.md:11`="1298"；日志无汇总行可复核 | S1 首步跑一次全量并**留证归档**（`.ai-coding/evidence/`） |

## 附 B：本冻结的效力与后续

- 本文件生效后，v1.0 的**架构原则（§1）、ADR（§2）、范围（§3）、接口契约（设计提案 §3 + 本文件 §2 ADR-018）**即为冻结基线。
- 任何偏离必须记录在实现期的**偏离说明**中（沿用各模块 docstring 既有惯例），并注明"为何偏离本冻结"。
- **本冻结不修改 `PROTOCOL.md`**：若 S0–S7 执行中发现协议缺口，按 v0.3 纪律记 **Protocol Improvement Candidate（PIC）**，经人工 Review 才可动协议——**禁止自升级**。
- ADR 一旦回填 `docs/ADD.md`，其权威性以 `ADD.md` 为准，本文件降格为 Freeze 会话记录。
