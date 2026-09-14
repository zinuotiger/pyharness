# AI Coding Protocol v0.3

> 执行协议。约束 AI 拿到既有规格后，如何执行编码、验证、修复、收敛与汇报。
> 本文件不承载项目架构/需求/知识；那些归 `docs/`。

**Change Notes（v0.2 → v0.3）**：本版来自**前 8 次真实任务实验**产出的候选规则，经**一次人工审查**后**正式采纳**——即 §13 闭环（Candidate → Human Review → **Adopt**）**确实发生**。采纳三条：**C2 → CORE-02**、**C3 → CND-01**、**C6 → CND-04**。

**Decision Record（§13 闭环证据）**：

| Candidate | Evidence（实验轮次） | Human Decision | Adopted Rule（落点） |
|---|---|---|---|
| **C2** | 第 5/8 轮：影响面分档全凭 AI 主观判断，无信号清单 | **ADOPT** | `CORE-02` 增"高影响信号清单"+"命中即默认高影响"+"不确定取高档" |
| **C3** | 第 6 轮：为验证 CND-01 **即兴排查 10 处**并发点，无清单 | **ADOPT** | `CND-01` 增"共享状态排查清单"+"无样本结论"口径 |
| **C6** | 第 2/3/5/7 轮：**外部输入→身份/租户/路径**在多子系统复发 | **ADOPT** | `CND-04` 增"外部输入类型清单"+"外部输入→资源路径"重点模式+路径守则+测试分级 |
| C1 / C4 / C5 / C7 | 见 §15 | **DEFER** | —（**不写入**协议正文） |
| C6 项目侧补充：`SECURITY.md` 路径原则未覆盖非 `fs.*` 子系统 | 第 7 轮 | **PROJECT-KNOWLEDGE** | **不入通用 Protocol**；留项目知识（见 §12/§15） |

> **纪律**：AI **无自主采纳权**；本次为**人工批准后**的升级。C1/C4/C5/C7 仅记录 DEFER，**未升级**。

**Change Notes（v0.1 → v0.2，保留）**：来自三次真实任务验证（①死代码重构 ②Bug 修复 ③**真实触发 CORE-03**）——A：GATE-01 增"编码前契约核对"；B：CORE-02 改"分级回归"；C：新增 CND-08；DEFER：LITE 路径等。


---

## 1. Purpose

本 Protocol 约束 **AI 拿到既有规格后，如何执行编码、验证、修复、收敛与汇报**。

它**不是**：项目架构文档、需求规格文档、项目知识库、Agent Runtime。

它**是**：AI Coding Execution Protocol —— 一份关于"如何做、何时可停、拿什么证明"的执行纪律。它引用既有规格，但不复制内容。

## 2. Scope

**必须遵守**的任务：
- 新增/修改功能、修复缺陷、重构、架构调整；
- 涉及多文件、跨模块、并发/共享状态、外部输入、UI、跨进程、不可逆副作用的改动；
- 影响构建/测试/接口/数据形态的任何变更。

**可豁免**（仅需说明）：
- 纯只读探查、纯问答、格式化/注释级微小编辑、用户明确要求"仅分析不改动"。

判定：只要会**改变系统行为或写入持久状态**，即受本 Protocol 约束。

## 3. Lifecycle

固定顺序，每步的 AI 义务：

| 步 | AI 必须做 |
|---|---|
| **LOAD** | 加载相关代码、规格、约束、既有测试；确认工作区状态（未提交改动、分支）。 |
| **UNDERSTAND** | 复述目标与验收口径；识别输入/输出/边界；列出不确定点。 |
| **IMPACT** | 判定改动波及的模块、接口、状态、数据流、并发面；标注触发哪些 CONDITIONAL 规则。 |
| **PLAN** | 给出可执行的改动清单与验证清单；标出需人确认的决策点。 |
| **IMPLEMENT** | 按计划改动；保持最小改动面；同步维护测试。 |
| **VERIFY** | 执行验证路径（测试/驱动程序/复现）；判定"已验证 vs 仅尝试"。 |
| **REPORT** | 按 §11 格式汇报，附证据与已知限制。 |

不得跳过 UNDERSTAND/IMPACT/PLAN 直接 IMPLEMENT（除非豁免任务）。

## 4. State Machine

**状态**：`PLANNED → READY → ANALYZING → PLANNING → IMPLEMENTING → TESTING → VERIFYING → CONVERGING → VERIFIED → DONE`，旁路终态 `PARTIAL`、`BLOCKED`。

**合法转换**
- `PLANNED → READY`（上下文就绪）
- `READY → ANALYZING → PLANNING`（可按需合并回环，但都须发生）
- `PLANNING → IMPLEMENTING`（GATE-01 通过）
- `IMPLEMENTING → TESTING → VERIFYING`
- `VERIFYING → CONVERGING`（发现 GAP）
- `CONVERGING → IMPLEMENTING`（回到实现）
- `VERIFYING → VERIFIED`（无已知 GAP）
- `VERIFIED → DONE`（GATE-04 通过）
- 任意活动态 `→ BLOCKED`；`→ PARTIAL`（部分完成且限制已记录）

**不得跳过的转换**
- 不得 `PLANNING → VERIFIED`（跳过实现与验证）。
- 不得 `IMPLEMENTING → DONE`（跳过 GATE-02/03/04）。
- 不得 `VERIFYING → DONE`（跳过 GATE-04 的收敛判据）。

**进入 `BLOCKED` 的条件**（任一）
- 需求/验收口径不明确且无法从既有规格推定；
- 架构或约束冲突（规格与实现互斥）；
- 外部依赖不可用且无替代验证路径；
- 环境问题导致无法执行验证；
- 需要人确认的高风险/不可逆决策。

**只能是 `PARTIAL` 的条件**
- 部分子项完成，其余有明确的、已记录的限制或待办；
- 前置依赖未就绪导致无法完整验证。

**只有满足全部才可 `DONE`**
1. 计划内改动全部落地；
2. 必要测试通过（含触发的 CONDITIONAL Required Test）；
3. 证据存在（§10）；
4. 无已知未记录 GAP；
5. 已知限制已写明；
6. 通过 GATE-04。

## 5. CORE PROTOCOL RULES

> 仅跨项目通用；不含任何具体项目/文件/平台细节。

**CORE-01**
- **Rule**：无执行证据，不得声称"完成/已修复"。
- **Why**：把"尝试过"报告成"已验证"会让缺陷在交付后暴露。
- **AI Must**：在汇报前实际执行验证路径，并如实区分"已验证/仅尝试/未验证"。
- **AI Must Not**：以代码存在、逻辑推断或"看起来对"替代执行结果。
- **Verification**：存在可复现的执行记录（测试输出/程序行为）。
- **Failure if Violated**：交付假阳性，风险外溢。（Severity S1）

**CORE-02**
- **Rule**：行为类改动完成后，**按影响面分级**执行回归，禁止只跑改动文件：
  - **隔离改动**（单模块、无跨模块影响、无契约/数据形态变化）→ 受影响模块测试 + 相关集成冒烟；
  - **高影响改动** → **全量回归**。
- **High Impact Signals（命中任一 → 默认按 High Impact 处理）**：
  跨模块 · 共享状态 · 公共契约 / API / Schema · 数据形态变化 · 安全边界 · 并发控制 · 进程 / 资源生命周期 · 持久化 / 一致性 · 其它明显高风险运行时逻辑。
- **兜底**：影响面判断**存在不确定性时取高档**（按 High Impact 处理）。
- **Why**：改动会打破其他模块锁定的旧契约；分档若无信号清单，易系统性**低估**影响面 → 漏检。
- **AI Must**：先按信号清单判定档位（不确定取高档），按对应深度执行并报告通过/失败计数与**所判档位**。
- **AI Must Not**：以"**改动文件自己的测试通过**"作为完整回归判断——**硬底线，任何档位都不得违反**。
- **Verification**：回归深度与所判档位（含**信号命中记录**）一致；结果可见。
- **Failure if Violated**：跨模块回归漏检。（S1）

**CORE-03**
- **Rule**：改动使既有测试失败时，先判定该测试锁定的是"有意行为"还是"缺陷"，再决定改码或改测。
- **Why**：防止"为让测试变绿而改测试"，也防止把契约当 bug 改掉。
- **AI Must**：对每次改断言给出"为何是缺陷而非契约"的说明。
- **AI Must Not**：在不判定性质的情况下修改任何测试。
- **Verification**：改测处均有理由说明。
- **Failure if Violated**：契约被静默篡改或缺陷被固化。（S1）

**CORE-04**
- **Rule**：编辑某符号前，检测**同名重复/遮蔽定义**（后定义覆盖先定义）。
- **Why**：编辑到被遮蔽的死定义，改动不生效且误导。
- **AI Must**：编辑前对目标符号做一次定义计数，落到**生效的**定义上。
- **AI Must Not**：假定文件内符号唯一。
- **Verification**：改动作用于实际生效定义。
- **Failure if Violated**：无效改动、死代码蔓延。（S2）

## 6. CONDITIONAL PROTOCOL

> 按 Trigger 触发；每条含 Trigger / Rule / AI Action / Required Test / Required Evidence / Exit Condition。

### CND-01 Concurrency / Shared State
- **Trigger**：改动共享/单例/全局门面/缓存/连接/线程池状态。
- **Rule**：共享状态改动必须验证**多实例/多会话隔离**，并复核既有测试是否锁定旧共享语义。
- **AI Action**：为共享读写点引入显式归属或隔离；复核既有用例口径。**不得为触发本规则而人为制造竞态。**
- **排查清单（可执行 · 可复核，逐项核对）**：
  1. **全局可变对象**（模块级 dict/list/set 注册表）；
  2. **共享缓存**（懒建实例/句柄）；
  3. **check-then-await**（先判定、后 await，中间可被插入）；
  4. **check-and-set 原子性**（判定与写入之间是否无 await → 原子）；
  5. **资源上限计数原子性**（限流/并发闸的判定与自增）；
  6. **多实例状态污染**（互相覆盖 / 串场）；
  7. **跨 session / context / tenant 键隔离**；
  8. 既有同步机制（lock / snapshot / single-flight）是否**真正覆盖**该读写点。
- **"无样本"结论口径**：经上述清单**系统排查确认不存在真实并发缺陷**时，可形成 **"CND-01 无真实样本"** 的**有效验证结论**，但须附**排查证据**。**注意**：排查通过 **≠** 并发安全已被证明——仅表示**未发现**，不等于**已证安全**。
- **Required Test**：若存在共享状态改动 → ≥2 实例/会话的对抗用例。
- **Required Evidence**：隔离用例通过 + 旧用例判定结论；或（无样本时）清单排查结论 + **排查证据**。
- **Exit Condition**：多实例无串场。

### CND-02 Cross-Process
- **Trigger**：资源具"单写者/单实例"语义且可能被多进程访问。
- **Rule**：以 **OS 级锁**保护单写者语义，并提供**进程内可重入**。
- **AI Action**：加锁于打开/关闭生命周期；锁与数据文件分离且不随句柄关闭而失效。
- **Required Test**：独立进程的对抗用例（第二写者被拒/释放后可获取）。
- **Required Evidence**：跨进程用例输出。
- **Exit Condition**：并发写被拒且无数据损坏。

### CND-03 Frontend / UI
- **Trigger**：改动客户端脚本/模板/样式或影响交互。
- **Rule**：在**真实渲染器**中驱动并读取控制台；顶层脚本错误会静默终止整个应用。
- **AI Action**：启动界面→执行关键交互→捕获控制台与截图。
- **Required Test**：手动/自动化端到端交互（非仅单测）。
- **Required Evidence**：截图 + 无未捕获错误的控制台记录。
- **Exit Condition**：关键路径可交互且无致命脚本错误。

### CND-04 Security / External Input
- **Trigger**：消费来自客户端/外部的一切输入——尤其：**identity · tenant · actor · URL · path · resource name · query / parameter · session / ownership** 等。
- **Rule**：**不信任**外部自报；服务端**派生**或**严格校验**。其中 **"外部输入 → 资源路径"为重点安全模式**。
- **AI Action**：
  - 身份 / 租户 / actor / 会话归属：改为**服务端派生**或绑定**可信通道**；
  - URL：做 scheme / host 校验（拒 SSRF / 云元数据地址）；
  - **路径类（path / resource name）**：**必须**经**单一安全校验入口** → **归一化** → **root containment（包含校验）** → **拒绝路径逃逸**（含 `.` / `..` / 路径分隔符 / symlink·junction 越界）。
- **Required Test**：**正常路径**用例 + **"伪造 / 非法输入被拒"用例**（**必须包含 negative case**）。
- **Required Evidence**：按**验证层级**标注——
  - **Boundary Rejection Verified**（校验入口拒绝；代码 / 单测层）；
  - **End-to-End Unauthorized Access Verified**（真实端到端越权被拦）。
  - 若端到端环境不可用 → 允许**等价可达性验证**，但**必须明确标记验证层级**；**不得**把分析结果描述为"端到端已验证"。
- **Exit Condition**：伪造输入无法越权。

### CND-05 Timeout / Non-Cancellable Operation
- **Trigger**：存在"无法安全中止"的操作且有超时。
- **Rule**：超时后**驱逐其执行载体**，禁止复用同一载体执行后续操作。
- **AI Action**：隔离本次执行载体；超时/取消即弃用，不复用。
- **Required Test**：超时后"后续调用不经旧载体/不被其阻塞"用例。
- **Required Evidence**：驱逐用例输出。
- **Exit Condition**：无载体复用导致的副作用重叠。

### CND-06 Data Consistency / Derived State
- **Trigger**：存在"源 + 派生视图/索引/缓存"，或"读全文—改—写回"的重写路径。
- **Rule**：(a) 派生状态读写**共享同一判据**（水位/谓词单源）；(b) 重写时**未改动数据字节级保真**。
- **AI Action**：抽取单一判据供读写两侧引用；重写只改目标，不动其余字节（行尾/编码/顺序）。
- **Required Test**：对账一致性用例 + 往返字节不变用例。
- **Required Evidence**：对账与保真用例输出。
- **Exit Condition**：读写口径一致且未改动数据不变。

### CND-07 Refactoring / Architecture Change
- **Trigger**：抽取/移动/重命名/消除重复/调整模块边界。
- **Rule**：确认**生效的**定义（消除遮蔽/重复）并立即重跑受影响模块测试。
- **AI Action**：重构后先跑模块级测试再继续。
- **Required Test**：受影响模块测试。
- **Required Evidence**：模块测试输出。
- **Exit Condition**：模块测试通过、无残留遮蔽。

### CND-08 Cross-Module State / Wiring Consistency
- **Trigger**：一个模块**生产**某状态、另一个模块**消费**该状态（跨模块共享的计数 / 标志 / 缓存 / 水位等）。
- **Rule**：必须验证 **Producer → State → Consumer 实际可达**——即该读写链路**不是 dead path**。
- **AI Action**：逐项核对——
  ① Producer 是否真的存在；
  ② Producer 是否真的被调用（有无调用者）；
  ③ State 是否真的会发生变化；
  ④ Consumer 是否能读取到该变化；
  ⑤ 写入是否发生在读取之前。
- **Required Test**：**至少一个端到端（或等价可达性）测试**证明该链路可达，而非只测单侧（只测 Producer 或只测 Consumer）。
- **Required Evidence**：可达性用例输出（可观察到 State 由 Producer 驱动、并被 Consumer 读取）。
- **Exit Condition**：五步核对全部成立，且存在证明链路可达的测试。
- **Note（规则动机，非规则）**：真实缺陷样本——某计数由"生产方"递增、供"消费方"判阈值，但**递增方法全库无调用者** → 计数恒不变 → 消费方的阈值分支成为**不可达（dead）路径**，而单侧单测全绿、毫无察觉。此类"生产者未接线"只能由**端到端可达性**测试发现。（依据 §12，具体模块/函数名不入本协议，仅留存于项目知识库。）

## 7. QUALITY GATES

> **未通过 Gate，不得进入下一阶段。**

**GATE-01 READY**
- Input：目标与验收口径、相关代码/规格、工作区状态。
- Checks：目标可复述；触及的 CONDITIONAL 已标注；不确定点已列出；**契约核对**（见下）。
- Pass Criteria：无阻塞性不确定；触及规则清单完整；**契约核对通过**（或已识别冲突且具备变更依据）。
- Failure Action：回到 UNDERSTAND；不明确则置 `BLOCKED` 并提问。**无变更依据的契约冲突 → 不得进入 IMPLEMENT。**

> **契约核对（强制 · 实施前）**：行为变化**实施前**，必须核对既有**测试 / 规格 / ADR** 是否已锁定当前行为。
> - 未锁定 → 可实施。
> - 已锁定且与本次修改冲突 → **必须存在明确的规格 / ADR / 任务依据**支持该变化；**否则不得实施**（不开始，或调整任务范围）。
> - **与 CORE-03 的关系**：**GATE-01 = 实施前的预防**（冲突且无依据 → 不实施）；**CORE-03 = 失败后的判定**（已实施改动使测试变红时，判定"有意行为 vs 缺陷"）。二者互补——能预防的由 GATE-01 前置拦截；测试未覆盖但契约隐性存在的，由 CORE-03 事后兜底。

**GATE-02 IMPLEMENTATION**
- Input：改动清单、实现结果。
- Checks：改动最小面；未破坏无关行为；测试随改动同步。
- Pass Criteria：实现完整、无半成品、无 TODO 占位。
- Failure Action：回 `IMPLEMENTING` 补齐。

**GATE-03 TEST / VERIFY**
- Input：测试执行结果。
- Checks：CORE-02 回归按影响面档位执行（高影响=全量；隔离=模块+集成冒烟）；CND-* 的 Required Test 全过。
- Pass Criteria：所判档位的回归通过；触发的条件测试通过。
- Failure Action：进入 `CONVERGING`（§9）。

**GATE-04 DONE**
- Input：验证结论、证据、限制。
- Checks：无已知未记录 GAP；证据齐备；限制已记；汇报符合 §11。
- Pass Criteria：六条 DONE 条件全满足。
- Failure Action：降级为 `PARTIAL` 并记录待办。

## 8. FAILURE HANDLING

| 情况 | AI 处理 |
|---|---|
| 测试失败 | 判定"有意行为 vs 缺陷"（CORE-03）；修因不修测；记入收敛。 |
| 需求不明确 | 不猜测实现；列出歧义与候选，必要时置 `BLOCKED` 提问。 |
| 架构冲突 | 不绕过；记录冲突点，依既有规格/约束裁定，无法裁定即 `BLOCKED`。 |
| 约束冲突 | 以更严约束为准；记录取舍；触及安全/数据一致性时不得放宽。 |
| 外部依赖失败 | 标注为环境/外部失败，尝试替代验证路径；无替代则 `PARTIAL`/`BLOCKED`。 |
| 部分实现 | 明确划分已完成/未完成，未完成项入 TODO，状态置 `PARTIAL`。 |
| 环境问题 | 记录现象与影响面；不得以"环境问题"掩盖未验证代码。 |
| 无法验证 | 如实声明"未验证"，不得声称完成；说明缺哪条验证路径。 |

**特别禁止**：为了让测试通过而**盲目修改测试**。修改测试必须经 CORE-03 判定并留理由。

## 9. Convergence

循环：`Implementation → Test → Compare against requirements/spec/constraints → Find GAP → Create follow-up task → Implement → Verify → Repeat`。

收敛停止条件（全部满足才 `VERIFIED`）：
1. 无已知 GAP；
2. 必要测试通过；
3. 证据存在；
4. 已知限制已记录。

任一不满足 → 继续循环或依情形降级为 `PARTIAL`/`BLOCKED`。

## 10. Evidence

必须留证的情形：任何行为性改动、任何"完成"声明、任何 CONDITIONAL 触发。

链条：`Claim → Implementation → Test → Result → Evidence → Limitation`。

分级真实：
- **代码存在 ≠ 测试通过**
- **测试通过 ≠ 完整验证**
- **完整验证 ≠ 生产证明**

汇报须指明当前处于哪一级，不得跨级宣称。

## 11. Report Format

每次任务结束必须输出：

`STATUS` · `SUMMARY` · `FILES CHANGED` · `ARCHITECTURE IMPACT` · `BOUNDARIES` · `CONSTRAINTS` · `FAILURES` · `TESTS` · `TEST RESULTS` · `EVIDENCE` · `CONVERGENCE` · `KNOWN LIMITATIONS` · `REMAINING WORK`

## 12. Project Knowledge Boundary

**写入项目知识库（不写入 Protocol）**：
- 决策/ADR → 项目架构决策文档
- 经验/坑位 → 项目经验文档
- 硬约束 → 项目约束文档
- 规格 → 规格目录
- 变更历史 → 变更日志

**不得进入通用 Protocol**：具体函数名、变量名、文件路径、会话标识、日志文本、单次 bug、单项目架构实现细节、用户个人的交流偏好。

## 13. Protocol Evolution

`开发 → 事实 → Lesson → Candidate Rule → Evidence → Human Review → Protocol Update`。

- AI **不得**自动把一次事故升级为长期规则。
- 新规则必须经**人工确认**后写入。
- 单次发生不足以晋升；需可复现的证据与跨场景适用性。

## 14. Final Rule Table

| Rule ID | Category | Trigger | AI Action | Verification | Evidence | Severity |
|---|---|---|---|---|---|---|
| CORE-01 | Core | 任何"完成"声明 | 先执行验证，区分已验证/仅尝试 | 有可复现执行记录 | 测试/程序输出 | S1 |
| CORE-02 | Core | 行为类改动收尾 | 按影响面分级回归（命中高影响信号即全量；不确定取高档） | 回归深度与档位一致；信号命中记录 | 回归输出 | S1 |
| CORE-03 | Core | 测试变红 | 判定有意行为 vs 缺陷后再动测试 | 改测有理由说明 | 判定记录 | S1 |
| CORE-04 | Core | 编辑符号前 | 检测同名重复/遮蔽 | 改动落到生效定义 | 定义计数 | S2 |
| CND-01 | 并发/共享状态 | 改共享/单例状态 | 加隔离并复核旧用例；或按**排查清单**判"无样本" | ≥2 实例对抗用例 或 清单排查证据 | 用例输出 / 排查证据 | S1 |
| CND-02 | 跨进程 | 单写者/单实例资源 | OS 锁 + 进程内可重入 | 跨进程对抗用例 | 跨进程输出 | S1 |
| CND-03 | 前端/UI | 改客户端脚本/交互 | 真实渲染器驱动 + 读控制台 | 端到端交互 | 截图 + 无致命错误日志 | S1 |
| CND-04 | 安全/外部输入 | 消费外部身份/租户/URL/路径等 | 服务端派生或校验；**路径类经单点归一+containment** | 正常+**negative** 用例；标注**验证层级** | 拒绝/越权用例输出 | S1 |
| CND-05 | 超时/不可取消操作 | 不可中止操作有超时 | 超时驱逐载体不复用 | 驱逐用例 | 驱逐用例输出 | S2 |
| CND-06 | 数据一致性/派生状态 | 源+派生视图；读改写回 | 判据单源 + 未改动数据保真 | 对账 + 往返不变用例 | 对账/保真输出 | S1 |
| CND-07 | 重构/架构变更 | 抽取/移动/去重 | 确认生效定义 + 跑模块测试 | 模块测试 | 模块测试输出 | S2 |
| CND-08 | 跨模块状态/接线 | 一方生产、另一方消费状态 | 验证 Producer→State→Consumer 可达 | 端到端可达性用例 | 可达性用例输出 | S1 |

## 15. Deferred（明确不实施，继续观察）

**v0.3 人工裁定 DEFER（未采纳，不改协议正文）**：
- **C1**｜环境依赖与 Required Test 证据等级（环境受限时的等价验证）——继续观察。
- **C4**｜CND-06 / CND-08 边界澄清——继续观察。
- **C5**｜依赖缺失时的回退策略显式化——**样本仅 1**，继续观察。
- **C7**｜CND-03 "判无样本也须 renderer 证据"——继续观察。

**PROJECT-KNOWLEDGE（不入本协议）**：
- C6 项目侧补充——"项目安全文档的路径原则未覆盖非 `fs.*` 子系统"——属**项目知识**，**不**提升为通用 Protocol Rule，留待项目侧知识库处理（见 §12）。

**v0.2 起 DEFER（保留）**：
- **LITE 路径**（超小/零行为变化的轻量通道）：证据不足；且已观察到"看似微小的改动也可能触发 CORE-03"，暂不启用。
- **扩充 CND 分类**：除已有类目外不再新增。
- **自动规则晋升**：AI 仍**不得**自动升级规则（见 §13）。
- **其它未经真实任务证明的机制**：一律不引入。

---

**版本**：v0.3（**来自前 8 次真实任务实验 + 一次人工审查**；采纳 C2/C3/C6；DEFER C1/C4/C5/C7。CORE-01/02/03/04 与 CND-05/06/07/08 已获样本；CND-01 经清单排查仍无真实样本）。
**演进**：见 §13——AI **无自主采纳权**；新规则须经**人工确认**后方可写入。
