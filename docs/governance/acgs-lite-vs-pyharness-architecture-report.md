# ACGS-Lite vs PyHarness Architecture Difference Report

架构差异报告 · 分析日期：2026年10月14日（Asia/Shanghai）

## 0. 结论与取证边界

**PyHarness 已属于“内嵌运行时治理的 Agent Runtime”，具备智能体运行时治理系统的核心特征。** 它不仅运行 Agent，还在真实工具调用前执行权限、守卫和审批判断，并记录决策、凭证、证据与审计关系。它尚不能据此被认定为完整的企业级、跨框架治理控制平面。[P01][P04][P05][P07]

**最大差异是治理与执行的组织方式，以及凭证在执行中的地位。** PyHarness 围绕自身任务队列、AgentLoop、ToolExecutor 和事件日志实施治理；ACGS-Lite 的核心执行包装器围绕外部 callable 建立授权边界。PyHarness 的凭证目前主要是随授权生成的可核验审计证据，执行器读取 Decision 放行；ACGS-Lite 当前源码的 production profile 会验证并消费与实际调用绑定的 ExecutionGrant，再进入 callable。[P04][P05][P06][A01][A02]

### 0.1 样本版本

| 项目 | 本次分析基线 |
|---|---|
| PyHarness | 当前工作区 `D:\zinuotiger-workspace\feature-worktrees\pyharness-platform-web-v1`；分支 `feat/pyharness-platform-web-v1`；HEAD `7b1bdeab76cff23a040c94002d2bfb33b0291a06` |
| PyHarness 工作区状态 | **存在既有未提交修改及未跟踪文件**，按磁盘源码分析，不能将结论全部归属于上述 HEAD。特别纳入未跟踪的 `pyharness/application/trajectory.py`，以及已修改的 service、platform_service、workflow、platform_api、event payload 等实现 |
| ACGS-Lite | GitHub `acgs-ai/acgs-lite`，固定提交 [`da5732dc8944067ceacc16a9b27d220334f1c3f3`](https://github.com/acgs-ai/acgs-lite/commit/da5732dc8944067ceacc16a9b27d220334f1c3f3)，提交时间 `2026-10-12T19:30:44Z` |
| ACGS-Lite 获取方式 | 按 agent-reach 的 GitHub CLI 路由，使用 `gh api` 获取固定 SHA 的源码归档，在系统临时目录解包；没有把参考项目加入 PyHarness 仓库 |

ACGS-Lite 的 `pyproject.toml` 标记版本为 3.0.1，但该提交的 README 明确区分已发布 wheel 和尚未发布的源码强化。本报告比较的是**固定提交源码**，尤其 production grant、trusted context、durable audit 的分析不可直接外推到已发布 wheel。[A00][A01][A02]

### 0.2 分析方法与证据等级

- **已接线**：检查实现及生产装配/调用方，而非仅凭模块名、README 或设计文档。
- **条件能力**：需要选择特定 profile、配置后端或安装可选依赖；不会写成默认启用。
- **本次未证实**：在列出的源码范围未找到完整实现或端到端接线，不等于证明项目所有分支、外部部署都没有该能力。
- **建议**：基于已确认差异提出的未来设计，不描述为当前已有行为。

本次为静态源码研究：未启动 Agent、模型、MCP、Docker 或远程执行服务；未运行测试或基准。测试文件仅作为已有验证意图的辅助证据，**不构成本次测试通过声明**。没有修改源码、重构或添加功能，产出仅为本报告。

交付核对：对分析期间记录的 604 个既有 Git 跟踪/非忽略未跟踪文件进行 SHA-256 比对，未发现内容改变；新增文件仅本报告。报告中的本地源码链接均检查可解析。该检查证明本次文件操作边界，不代表运行时安全测试。

## 1. 第一阶段：PyHarness Architecture

### 1.1 Runtime Architecture：装配与运行边界

Web/Desktop 的公共服务路径在 `ApplicationService.queue_for()` 中按 session 懒建 TaskQueue；`_engine_runner_for()` 调用 `engine.build_runner_components()`，生成会话级 EngineSpine，再用 `make_runner()` 适配任务执行。平台会话还会根据绑定的 AgentDefinition 设置模型连接、轮数、预算、权限档位及 PlatformRuntime。[P01][P02]

EngineSpine 汇集 SessionLog、EventBus、Scope、LLMClient、ToolExecutor、GuardChain、ApprovalProvider、AgentLoop、治理上下文及编排组件。`_build_governance()` 注入同一条 GuardChain；真实装配创建 DecisionEngine、ReceiptStore、EvidenceCollector、AuditSystem，避免把治理仅作为旁观日志模块。[P01][P05]

CLI/ACP 使用 `attach_engine_to_ctx()` 复用这些装配原语。`assemble_real_engine()` 的存在不能代替生产调用链证据：本文件将其定位为 demo/probe/e2e 入口，公共服务的实际入口是 `build_runner_components()`。[P01]

```text
Web / Desktop / CLI / ACP
           │
     ApplicationService / shell adapter
           │
     SessionLog ───── EventBus ───── JSONL SessionStore
           │                  └──── 查询、投影、UI 流
       TaskQueue
           │
     EngineRunner → AgentLoop → LLMClient
                         │
                    ToolExecutor
                         │
        Scope + GuardChain + GovernanceContext
                         │
              ApprovalProvider（按需）
                         │
        Provider / PlatformRuntime / sandbox backend
```

图中的 EventBus 是进程内分发机制；JSONL 是会话事件持久化来源，二者不是分布式消息队列。[P08][P09]

### 1.2 Task Lifecycle：任务生命周期

公共消息入口先以 `sync=True` 写 `user.message`，再调用 `queue.submit()`。平台入口预分配 `p-<uuid>`，把 task、Agent 版本、definition 和当前 actor 信息写入消息 metadata，再用相同 task_id 入队；一般公共入口由 TaskQueue 分配 `t-N`。[P02][P03][P10]

TaskQueue 的实际事件与状态关系如下，不存在应当凭空补出的 `task.created` 阶段：[P03]

```text
user.message
    ↓
task.enqueued（waiting）
    ↓  FIFO 泵取队首
task.started
    ↓
segment.start（任务日志段开始）
    ↓
runner.run_for_task → AgentLoop
    ↓
task.completed 或 task.failed
    ↓  finally
segment.end
    ↓
按条件归档 evidence.archived
```

- 同一队列同时只有一个 running，后续任务 waiting；默认等待队深为 32，满时拒新。
- `_run_task()` 仅把明确的 `reason='complete'` 或成功 TaskResult 记为完成；错误、预算、取消、轮数封顶、停滞等落失败终态。
- `pause/resume` 以原因计数控制队列推进；审批等待会挂起推进，当前工具协程等待审批结果。
- 取消同时尝试通知 runner 并取消执行子协程；队列泵可以继续处理后续任务。
- 日志段关闭后，`archive_task_evidence()` 在段内存在治理决策时归档决策/凭证/segment 引用。归档是派生能力，异常会记录日志并跳过，不能认定每个任务都一定有 Evidence 工件。[P03][P01]

### 1.3 Execution Flow：执行流程

`make_runner()` 定位属于当前任务的 user.message，创建或复用 Agent context，设置 task_id，再调用 `loop.wake()`。平台路径增加 evidence 准备、任务总超时、产物收尾和资源释放。[P01][P11]

`AgentLoop.run_turn()` 每轮从 SessionLog 派生消息，装配系统提示词；按平台需要物化输入，再向 LLM 下发当前 Scope 可见工具 schemas。模型返回纯文本时写 `agent.message` 并自然结束；返回 tool_calls 时逐条执行 `run_step()`，把工具事实反馈到后续轮次。轮数、取消、预算和连续停滞均有停止条件。[P12]

工具强制路径在 `ToolExecutor.execute()`：[P04]

1. 查找工具契约，校验路径与参数；参数不合法时不调用 Provider。
2. 写 `tool.call`，同时保留 raw_args 和经过校验的 args。
3. 计算调用绑定摘要，进入 `ctx.governance.authorize()`，取得 Decision。
4. reject 直接终止；approval 进入审批等待，批准后重新授权，产生 D2，并检查需要的绑定条件。
5. 放行后执行 Provider，应用超时/取消控制。
6. 校验与整理输出，长输出可转 spill，写 `tool.result` 或 `tool.error`。

平台会话在 Provider 调用处进入 `PlatformRuntime.execute()`，再次检查 allowed_tools 与工具调用预算，并把 fs/exec/proc 操作路由到所选沙箱。该平台执行边界确实位于治理判断之后。[P04][P11]

### 1.4 Event System：事件系统

`Envelope` 包含 seq、UTC ts、type、session_id、actor、origin、task_id、payload、trace 和 tenant_id；Pydantic 禁止额外顶层字段，seq 由框架按会话递增分配，payload 按已注册词表校验。[P08]

SessionLog 是会话事件追加入口：构造 Envelope、推进缓存、分发至总线，强同步事件再等待持久化 flush。`derive_messages()`、`events_after()`、`rebuild_from_log()` 提供派生与恢复。EventBus 支持 sequential/waterfall/parallel，强同步事件强制顺序分发，并隔离订阅者异常；ordered emit 还有背压拒新行为。[P09]

`policy.updated`、`decision.issued`、`receipt.emitted` 属于强同步事件；`evidence.archived` 为派生索引事件；`llm.chunk` 等瞬时事件不进入持久事件词表。SessionStore 使用 JSONL、单写者锁、批量 flush、fsync 写入路径及故障重试/拒新；不能把“总线处理完毕”单独等同于数据已经持久化。[P08][P09][P13]

**边界：** 会话事件可追加、校验与重建，不意味着完整 JSONL 本身已经拥有签名或整条事件哈希链。当前显式哈希链主要存在于治理 Receipt；普通 tool/message 事件的 seq 连续性也不等价于抗恶意改写证明。[P06][P08][P13]

### 1.5 Scheduler：调度系统

`core/schedule.py` 的 Scheduler 实现 cron、interval、at，具备注册、编辑、暂停、恢复、删除和 run_now。ticker 触发 `_tick()`，`_fire()` 写 `schedule.trigger` 与来源明确的 user.message，再提交同一 TaskQueue；没有绕过 AgentLoop/ToolExecutor 的独立模型执行路径。[P14]

风险夜间窗口会写 `schedule.blocked`。重启恢复从 schedule 事件重建注册表，错过的触发记录为 `schedule.missed`，**不补跑**。这是进程内调度和事件恢复机制，本次未证实分布式 leader election、租约或跨节点调度去重。[P14]

### 1.6 Worker Model：工作模型

| 执行单元 | 实际模型与边界 |
|---|---|
| 会话任务 | 每会话 TaskQueue + asyncio 泵，串行消费；会话之间可有不同 EngineSpine |
| 单轮工具 | AgentLoop 顺序遍历 tool_calls；每步建立 asyncio 子任务便于取消 |
| 异步 Provider | 直接 await handler，外层 wait_for 控制超时 |
| 同步 Provider | 每次调用独立 `ThreadPoolExecutor(max_workers=1)`；超时后不复用该池，但 Python 线程可能仍运行，不能声称超时自动撤销副作用 |
| Background Job | JobManager 维护后台 asyncio 句柄、owner、终态和通知；默认活动上限 4。生产 EngineJobRunner 仍把主 Agent 工作提交至会话 TaskQueue，所以“4 个 job 句柄”不等于“同会话 4 个 AgentLoop 并行运行” |
| Subagent | SubagentManager 建独立子 session/scope/workspace，缩减工具集合和预算，用 asyncio 并发执行子 loop；并发上界 8、深度上界 3；子会话事件单独落盘 |

上述结论来自任务、工具、Job、Subagent 实现与真实 runner 适配；本次未发现这些主要路径使用独立的远程 worker 集群。[P03][P04][P12][P15][P16]

## 2. 第一阶段：Agent Execution Pipeline 与已有治理

### 2.1 从 User Request 到 Trajectory / Audit

| 阶段 | 代码路径 | 已确认行为 |
|---|---|---|
| User Request | `application/service.py::create_message`；`application/platform_service.py::start_run` | 校验请求/资源，记录 user.message；平台消息携带 definition、版本与 actor |
| Task Creation | `core/task_queue.py::submit` | 分配或接受 task_id，拒绝重复 ID，写 task.enqueued，启动 FIFO 泵 |
| Agent Processing | `engine.py::make_runner`；`core/agent_loop.py::run_turn` | 定位消息、组装 context/history/prompt、调用模型、逐轮迭代 |
| Tool Execution | `core/tools_executor.py::execute` | 参数先验、Scope/Guard 授权、审批重入、Provider 调用、超时与输出处理 |
| Result | `core/agent_loop.py`；`core/task_queue.py`；`application/platform_runtime.py` | agent.message、tool.result/error、任务终态、segment.end；平台另有产物收尾 |
| Trajectory / Audit | `application/trajectory.py`；`governance/audit.py`；`governance/evidence.py` | 事件投影成运行轨迹；按决策关联请求/策略/审批/凭证/结果；证据归档保存引用 |

证据：[P01][P02][P03][P04][P07][P10][P11][P12][P17]。

### 2.2 Approval Mechanism 与 Human-in-the-loop

`ApprovalProvider.request()` 创建请求、绑定调用参数、等待 granted/denied/timeout；无合法交互通道时拒绝，超时不执行，关闭时取消未决请求。审批结果按请求所属会话写入；服务层提供查询、裁决、归属检查，队列暂停与恢复由审批生命周期联动。[P18][P02]

批准并非直接执行：`ToolExecutor._approval_round()` 再次进入授权，D2 通过 supersedes 关联 D1，通过 approval_ref 关联真实审批请求。新拒绝或所需参数绑定不一致时不调用 Provider。`_rejected` 记住已拒绝 call_id，阻止当前执行器内的再次执行尝试。[P04][P05]

此外已有 `user.ask`/AskProvider 的人工回答能力；平台还能装配 `AutomaticReviewer`。后者是有界的规则/AST 审核器，仅在无网络 isolated/remote 环境和有限工具/明确任务条件下自动批准；不匹配、异常或超时转 manual，不应描述为通用 AI 审批 Agent。[P18][P19][P11]

### 2.3 Permission Control 与 Policy System

权限至少分为三个实际层次：

1. **账号/租户入口**：账号会话、tenant 校验，以及 owner/member/viewer 的 API 写权限限制，已存在于 Accounts 与 HTTP 层。[P20]
2. **运行时 Scope/Guard**：allowed_tools、deny_tools、strict/basic、危险分级、路径几何、凭据读取、网络出站、exec 与覆盖写入等守卫；Scope 决定工具可见性，GuardChain 在执行前再次检查。链在第一个非 allow 结果处返回，不应写成总会收集所有规则结论。[P21][P04]
3. **平台执行定义**：AgentDefinition 的工具白名单、approval_rules、预算、沙箱 profile；`configure_spine()` 和 `PlatformRuntime.execute()` 将其落实至执行路径。[P11][P19]

治理 `PolicyEngine` 已有 Policy/PolicyRule/PolicyRegistry、版本、SHA-256 指纹、规则启停和 `policy.updated`。不过 `current(scope)` 当前直接返回同一 `_policy`，指纹的受保护输入主要是规则描述、disabled 集和 params；它没有自动把完整 Scope、平台所有权限变化和工具实现身份纳入不可变执行策略快照。[P22]

**因此不能写“PyHarness 没有 Policy Engine”，也不能写“现有 fingerprint 已覆盖所有有效权限”。** 下一步应统一运行时有效策略的表示与快照，而不是仅增加一个同名引擎。[P22][P11]

### 2.4 Decision Receipt：已经有，但不是独立执行令牌

当前 Receipt 包含 decision、verdict、tool、inputs_digest、policy_fingerprint、principal、approval_ref、supersedes 与 prev_hash 等信息；落盘 payload 保存 receipt_id、decision_id、kind、digest、prev_hash，再从决策事件重建。ALLOW/REJECT 产生 decision receipt，审批前中间态 D1 不产生；审批后 D2 产生 approval receipt。[P06]

已有 `verify_receipt()`、`verify_chain()` 及 `rebuild_from_log()`；公共 `governance_audit()` 也会核验凭证和链，故不是“只有离线工具、没有查询接线”。[P06][P02]

**执行限制有直接源码依据：**

- `GovernanceContext.authorize()` 在 `receipts is not None` 时调用 emit，但返回的是 Decision；receipts 为空时可跳过发射。
- `ToolExecutor.execute()` 根据 Decision/审批结果进入 Provider，没有在关 3 前取 Receipt、执行 verify、验证有效期或消费一次性授权。
- Receipt 的 signature 当前为 `None` 占位；content hash 明确排除 receipt_id、时间、event_seq，受保护集合也不包含 call_id、tenant_id。
- 当前调用绑定为工具名和规范化参数的 SHA-1 摘要，主要服务审批同参校验；它不是来自受信发行者的签名或 MAC。

真实默认装配已经包含 ReceiptStore，发射失败会沿 await 路径阻断该次授权返回；上述限制指的是**缺少独立的凭证验证与消费契约**，不是说正常路径在副作用后才生成凭证。[P01][P04][P05][P06]

### 2.5 Audit System 与 Trajectory Tracking

AuditSystem 从事件重放构造 request → policy → decision → approval → receipt → execution 关系，可输出 denied_report，reconcile 检查 seq 空洞、tool.result 缺 guard 事件、审批 grant 缺 D2 Receipt 等。EvidenceCollector 记录 claim 与事件引用，而非复制完整执行事实。[P07]

审计能力的边界也要保留：`reconcile()` 的执行集合基于 `tool.result`，不等于侦测宿主机所有外部副作用；核验/重建不能代替事前授权和外部不可变存证。[P07]

当前工作区新增的 `project_trajectory()` 从事件投影出队列、模型请求、工具、审批等待、子会话 lane、workflow、人工问答、产物与终态，支持增量分页、按 run/workflow 过滤；`PlatformService.trajectory()` 及 `/sessions/{sid}/trajectory` 已接入。它会把缺少可靠句柄/终态的历史记录标成 unknown，避免把不完整记录当成仍在运行。[P17]

这是**执行可观测轨迹**。该投影函数没有调用授权或拒绝接口，本次未证实它提供跨动作频率、累积金额、敏感工具组合等实时轨迹策略执行。轨迹页存在，不等于已存在轨迹风险策略引擎。[P17]

### 2.6 Sandbox Capability

必须区分两类执行边界：传统 `tool_exec.py` 使用会话 sandbox 工作目录和宿主子进程；平台 `PlatformRuntime` 才根据 profile 路由至 SandboxManager。仅有工作目录/strict 档位不能称为 OS 沙箱。[P23][P11]

平台已实现 disabled、host_approved、isolated（Docker）以及远程后端接线。Docker 创建参数包含 network=none、非 root 用户、cap-drop=ALL、no-new-privileges、只读根文件系统、CPU/内存/PID 限制、受控 workspace 挂载；无 Docker/创建失败会报错。[P23]

`host_approved` 的记录明确标记 host_unrestricted；remote 的实际隔离依赖远程服务实现和部署。可确认“存在真实 Docker/remote 执行实现”，但本次未运行 Docker 或远程验收，不能声明本机环境已满足这些隔离属性。[P23]

## 3. 第二阶段：ACGS-Lite Architecture

### 3.1 Governance Layer：治理层的三条相关路径

ACGS-Lite 应按路径分析，不能把所有模块能力合并成一个默认强制体系：

| 路径 | 主要职责 | 重要边界 |
|---|---|---|
| `GovernanceEngine.validate()` 与集成适配器 | 根据 Constitution/Rule 校验动作文本与指定 context，输出 ValidationResult/拒绝 | 校验函数本身不是工具执行器；调用方必须实际使用其结果 |
| `GovernedCallable` | 包装同步/异步 callable；先授权 gate，后调用目标函数 | production profile 与默认 compatibility 的保证不同；只保护经过包装的调用 |
| OpenShell governance HTTP integration | evaluate、提交审批、validator 投票、执行结果记录 | 有单独 GovernanceDecision/ActionEnvelope/状态存储契约，不能直接等同于 Callable 的 DecisionReceipt/ExecutionGrant |

证据：[A01][A03][A09]。`GovernedAgent` 另有角色检查与输入/输出校验，但包装外部 Agent 的 run 并不自动证明其内部所有工具副作用都经过 receipt gate。[A01][A08]

### 3.2 Policy Engine：Constitution 与规则执行

核心 `GovernanceEngine` 装配 active rules、关键词与正则匹配、可选匹配后端、custom validators，按 condition/有效期/deprecated 等信息选择运行期规则。ValidationResult 可包含阻断、警告、review_requests、escalations、incident_alerts；strict 模式会为阻断结果抛出异常。[A03][A04]

规则支持 severity 与 workflow_action；`resolve_enforcement()` 把匹配结果映射为 warn、block、block-and-notify、human review、escalation 或 halt。决策状态契约还定义 ALLOW、ALLOW_WITH_CONTROLS、TRANSFORM_REQUIRED、REPLAN_REQUIRED、STRUCTURED_REVIEW_REQUIRED、DENY_OPERATION_WITH_ALTERNATIVE、DENY_GOAL、HARD_DENY。[A04][A05]

**状态枚举不等于自动执行器。** 例如出现 TRANSFORM_REQUIRED 或 REPLAN_REQUIRED，不代表每个集成会自动转换输入、重规划并重新授权。LangGraph 适配器有自己的结果到状态映射；实现保证应按具体适配器核对。[A05][A13]

源码还有 `PolicyLifecycleOrchestrator`，提供 draft/review/staged/active/deprecated/archived 与审批数、lint、测试、影响范围、attestation gate；其直接实现使用内存 policy/trail 容器。可以借鉴策略治理模型，不能据模块存在断言所有运行期入口都已使用持久化的企业策略发布系统。[A14]

### 3.3 Decision Receipt：决策凭证与授权真伪

`legitimacy/receipt.py::DecisionReceipt` 包含 request_id、goal、proposed_method、decision_type、authority_basis、matched_constraints、policy_version、required_controls、transformation/rationale、execution_boundary、issued_at、receipt_hash。ExecutionBoundary 定义 method、scope、subjects、expires_at、single_use。[A06]

`validate_receipt_for_execution()` 拒绝缺失凭证、未知状态、缺策略/授权依据、无约束证明、哈希异常、不可执行状态与边界不匹配；仅 ALLOW/ALLOW_WITH_CONTROLS 允许进入执行。结构化人类审批检查包含 approved_by、approved_at、approval_id。[A07]

但普通 receipt 的 SHA-256 只能检查内容一致性。源码另有 Ed25519 SignedReceipt 与 pinned public key 验证；production Callable 仍不能仅凭一个调用者提供的普通或 signed receipt 执行。其 `_gate()` 当前要求本 wrapper authority 签发的 ExecutionGrant，signed-only 分支明确拒绝执行。[A01][A10]

### 3.4 Authorization Flow：授权到副作用

当前 production Callable 的可证实流程：[A01][A02][A07][A11]

```text
宿主认证 actor / tenant，构造 TrustedExecutionContext（可要求必须存在）
    ↓
issue_grant(target, exact args)
    ├─ 动作载荷校验
    ├─ bind_invocation：实际 callable / args / scope / subjects
    ├─ bind_policy：当前策略绑定
    └─ authority 签发带 binding MAC 的 ExecutionGrant
    ↓
调用被包装函数并传入 grant
    ↓
_gate：issuer / MAC / receipt hash / method / args / policy / context 校验
    ↓
validate_receipt_for_execution：状态与执行边界校验
    ↓
InProcessGrantLedger.consume：原子登记 grant 与 attempt
    ↓
若 require_durable_audit：确认 execution_authorized 持久化成功
    ↓
实际 callable 执行 → 输出校验 → attempt 终态与结果摘要
```

ExecutionAuthority 使用随机进程内密钥和 HMAC-SHA256 绑定 grant；还检查实际 callable 对象身份、参数摘要、policy digest、可信 context。TrustedExecutionContext 的 actor_id/scope/allowed_subjects 由宿主提供，不是该库替宿主完成身份认证。[A02]

**profile 限制：** 默认是 compatibility，production 需要显式选择；trusted context 与 durable audit 也有各自开关。`require_durable_execution=True`、`require_restart_recovery=True` 当前会直接抛错，不能声称 grant 在跨重启/跨进程范围可恢复或全局仅执行一次。[A01][A02]

**重复调用限制：** 同 wrapper 实例内，同一 grant 被不同 attempt 再用会拒绝；同 attempt 已完成时可恢复已缓存且摘要相符的结果，而不重新执行；在途或不确定结果会拒绝。ledger 与结果缓存都在内存，不能替代外部 API 幂等键、持久 ledger 或分布式排他。[A11][A01]

### 3.5 Approval Flow 与 MACI

MACIEnforcer 有 role/action 权限检查、未分配角色的 observer 默认行为，以及 proposer_id 不得等于 validator_id 的检查。GovernedAgent 使用 MACI；这些检查并不表示任意同进程代码或 HTTP 自报身份天然可信。[A08][A01]

OpenShell integration 实际提供 evaluate-action、submit-for-approval、review-approval、record-outcome、audit-log 路由，使用 QuorumManager 管理 approval gate；review 路径校验 validator 动作权限、禁止提交者自审，再投票更新决定。Quorum 支持唯一 voter、人数门槛、veto、deadline。可通过状态 backend 保存并加载 decisions/quorum，包含 JSON、SQLite、Redis 实现，默认仍是内存。[A09][A15]

此处有两个关键边界：路由从请求 payload 的 actor/reviewer 信息建立角色，`create_openshell_governance_app()` 本身没有配置认证依赖；宿主需要提供可信身份边界。`record-outcome` 是结果上报接口，不是 OS 沙箱执行器，也不是自动消费 Callable ExecutionGrant 的接口。**审批状态持久化不等于副作用执行账本持久化。**[A09][A15][A01]

### 3.6 Audit Mechanism

`AuditLog` 以 entry hash 与 previous hash 形成 SHA-256 链，提供 verify、查询、导出与 backend 恢复。无 backend 时为进程内日志。普通 `record()` 的 backend 写失败会告警，不自动转为执行拒绝；需要与 `record_durable()` 区分。[A12]

production Callable 配置 `require_durable_audit` 时，会要求合适后端并在 callable 启动前写 execution_authorized；JSONLAuditBackend 的持久确认包含文件 fsync、身份检查和目录 fsync；`record_durable()` 在失败时回滚，回滚不确定会 poison strict 状态。该能力有平台/文件系统部署条件，本次未运行其持久化验收。[A01][A12]

哈希链属于可检测不一致的证据机制，若攻击者能替换完整日志和信任锚，不能仅靠本地链声称不可篡改。与 PyHarness 一样，外部锚定、密钥托管和日志保留制度需要独立设计。[A12][P06]

### 3.7 Replay、Trajectory 与 Sandbox

- **Replay**：`replay_and_verify()` 对 SignedReceipt 验证哈希与指定公钥签名，向调用者提供的 evaluator 传入 ReplayInputs，再比较重算决策。它没有替调用者自动取得历史策略或完整原始环境，也不是重执行 Agent 或副作用。[A16]
- **Trajectory**：TrajectoryMonitor 提供频率阈值、累积值、敏感工具序列检查。Anthropic 和 MCP 集成调用 checkpoint，把结果加入治理上下文/可观测信息；Anthropic 的监视异常有捕获告警路径，因此不能把监视器概括为所有工具调用的统一 fail-closed 总闸。[A17]
- **Sandbox**：`constitution/sandbox.py::PolicySandbox` 比较候选策略与生产策略对样本动作的决定，属于策略模拟。已审阅的 Callable/OpenShell governance 路径没有自建 Docker/OS 隔离执行能力；OS 沙箱由外部运行时承担。[A18][A01][A09]

## 4. 第三阶段：Capability Matrix

矩阵的“已有”仅表示所引源码范围的实现；条件与限制均保留。

| Capability（能力） | ACGS-Lite | PyHarness | Difference（差异） | Recommendation（建议） |
|---|---|---|---|---|
| Agent Control Point（智能体控制点） | Callable gate、GovernedAgent、框架适配器与治理 API；覆盖取决于接入点 [A01][A09] | 自身 TaskQueue/AgentLoop/ToolExecutor，以及平台执行边界 [P01][P04][P11] | 前者便于作为外部组件接入；后者深入控制自身运行时 | 保留单一 ToolExecutor 强制点，列出 LLM/工具/插件/管理动作的完整副作用边界 |
| Action Authorization（动作授权） | production grant 绑定 callable、args、policy、context，并单次消费；默认 compatibility 较弱 [A01][A02][A11] | Scope/Guard → Decision → 审批重入 → Provider；存在调用摘要与拒绝 ID 记忆 [P04][P05][P21] | PyHarness 未在执行前验证并消费授权凭证；ACGS 的消费仅限实例内 | 设计受信授权 grant 与执行消费契约，单独解决持久幂等与不确定结果 |
| Policy Engine（策略引擎） | Constitution、文本/上下文规则、自定义 validator、review/escalate/halt；另有生命周期模块 [A03][A04][A14] | 已有 PolicyEngine、版本/指纹、守卫链、Scope 和平台审批规则 [P22][P21][P19] | ACGS 的业务策略表达更丰富；PyHarness 的执行权限落地更贴近自身工具 | 先统一有效策略快照，再扩展业务/资源属性规则与审批发布流程 |
| Approval Flow（审批流程） | 结构化 review 状态；OpenShell quorum、veto、自审禁止、可选状态持久化 [A07][A09][A15] | 真实交互审批、TTL、暂停/恢复、参数绑定、批准后重入、有限自动审核 [P18][P04][P19] | PyHarness 交互运行闭环强；本次未证实其多人 quorum/职责分离工作流 | 在现有机制上扩展实名审批、职责分离、多人门槛及恢复语义 |
| Decision Receipt（决策凭证） | 丰富 receipt/边界；可选 Ed25519；production 使用 authority grant，signed-only 不可直接执行 [A06][A10][A01] | 已有 receipt、SHA-256 链、离线/服务核验；signature 占位 [P06][P02] | 两者都有凭证，执行契约不同；哈希本身均不证明发行者可信 | 扩展保护字段与发行者认证；把 verify/consume 接到副作用前 |
| Audit System（审计系统） | 审计 SHA-256 链、backend、可选 durable authorization confirmation；普通 record 写错只告警 [A12][A01] | 会话 JSONL、决策关系追踪、reconcile、Evidence 引用、审计 API [P07][P13][P02] | 前者强调授权/执行证据确认；后者把审计与任务事件统一 | 保留事件真源，增加外部锚定/保留/导出及关键授权持久确认契约 |
| Replay Capability（重放能力） | 签名 receipt + 注入 evaluator 重算判决；同实例 grant 同 attempt 结果恢复 [A16][A11] | 日志重建消息/状态/Receipt/审计/轨迹；job crash 标失败、schedule missed 不补跑 [P09][P06][P14][P15][P17] | 历史视图重建、判决复算、执行恢复是三种能力；均未证明跨节点 exactly-once | 增加历史策略快照与纯决策重算；严禁默认重放真实副作用 |
| Trajectory System（轨迹系统） | 频率、累积值、工具序列风险监视，部分集成 checkpoint [A17] | 丰富执行树/等待/子会话/workflow/产物/增量 UI 投影，当前工作区已有接线 [P17] | ACGS 偏轨迹风险规则；PyHarness 偏运行过程可观测 | 从事件投影提取只读特征，将需要阻断的规则接回统一授权入口 |
| Sandbox Capability（沙箱能力） | PolicySandbox 为策略模拟；所审阅 gate 未提供自建 OS 执行隔离 [A18][A01] | 平台有 Docker/remote；host_approved 为宿主执行；传统 exec 工作目录不等于隔离 [P23][P11] | PyHarness 已有执行后端能力；ACGS 治理可与外部沙箱组合 | 以 isolated/remote 为适用场景的明确部署档位，维护真实隔离验收证据 |
| Identity / Separation of Duties（身份/职责分离） | TrustedExecutionContext/MACI/禁止自审；认证由宿主保证 [A02][A08][A09] | tenant 与账号角色已存在；治理 Principal 主要由 channel 派生 [P20][P05] | API 权限和决策归属不是同一层；PyHarness 尚未形成完整实名主体绑定 | 将可信账号、tenant、代理、审批者绑定到 Decision/Receipt，增加 proposer/validator/executor 区分 |
| Runtime Orchestration（运行时编排） | 已审阅核心把 Agent/工具执行交给宿主框架 [A01][A03] | 模型轮次、FIFO、调度、job、subagent、预算、取消、产物统一实现 [P01][P03][P12][P14][P15][P16] | PyHarness 的完整运行链是现有优势 | 优先增强现有治理闭环，避免为引入治理替换运行时地基 |

## 5. 第四阶段：Conclusion

### 5.1 PyHarness 是否属于 Agent Runtime Governance System？

**是，按“运行时对 Agent 工具动作进行事前授权、人工介入和可追溯记录”的定义。** 直接证据是生产装配的 GovernanceContext，以及 ToolExecutor 强制调用 authorize、approval re-entry 和 Provider 之前的拒绝路径。更准确的定位为 **Governed Agent Runtime / 内嵌治理的智能体运行时**。[P01][P04][P05]

若定义额外要求跨框架统一控制、可携带受信授权、强身份/职责分离、跨重启消费账本与外部不可变审计，目前证据不足以称其已完整具备。该判断是本报告给出的能力分类，并非依据某项认证标准。[P06][P20][P22]

### 5.2 ACGS-Lite 和 PyHarness 最大区别

**PyHarness 以执行运行时为中心内嵌治理；ACGS-Lite 以可接入执行边界的治理组件为中心。** 二者并不是“无治理框架”和“完整治理系统”的二选一。进一步看，PyHarness 的当前执行放行依赖 Decision/审批，ACGS production Callable 更强调受信 grant 的验证和消费，但仍受实例内 ledger 限制。[P04][P05][A01][A11]

### 5.3 PyHarness 当前优势

1. **运行链完整且治理已接线**：任务、模型、工具、审批、预算、取消和事件有共同入口，无需从零增加治理层。[P01][P03][P04][P12]
2. **事件真源便于解释与恢复视图**：消息、任务段、审批、决策、Receipt、Evidence、轨迹可从会话事实建立关联。[P06][P07][P09][P17]
3. **人工操作体验与执行机制结合**：审批能暂停/恢复队列、批准后重入，子会话也有独立审批/日志接线。[P18][P16]
4. **已有平台安全执行能力**：账号/租户角色、AgentDefinition 白名单、有限自动审核、私有工作区与真实 Docker/remote 后端。[P20][P11][P19][P23]
5. **已有轨迹和产物闭环**：模型、工具、等待、子任务、workflow 与产物能投影到统一运行视图。[P17][P11]

### 5.4 缺少或尚未完整证实的企业级治理能力

| 能力缺口 | 具体源码依据 | 应补齐的契约 |
|---|---|---|
| 可验证授权的强制执行消费 | executor 读 Decision；ReceiptStore 可为空；verify 主要用于审计；无 grant consume [P04][P05][P06] | 缺凭证/无效签名/参数或策略不符/重复消费时，Provider 必须零调用 |
| 完整的可信主体归属与职责分离 | Principal 从 channel 派生；平台 actor 与 tenant 另有记录；审批机制未出现通用 quorum 流程 [P05][P10][P18][P20] | 登录用户、tenant、代理、审批者和执行者明确绑定，禁止自审、支持多人门槛 |
| 有效策略快照与发布治理 | fingerprint 覆盖声明式规则；current(scope) 未解析 scope；平台规则另行配置 [P22][P11][P19] | 完整权限/资源/工具实现版本的不可变快照；审批、验证、发布与撤回可追踪 |
| 跨重启副作用幂等/不确定结果管理 | `_rejected` 是内存集合；job 恢复补 crash 终态；schedule 不补跑 [P04][P15][P14] | 持久 attempt ledger、外部幂等键、in-flight/unknown/partial 对账与人工处置 |
| 全事件外部信任锚与保留制度 | JSONL 追加与 seq 校验；Receipt 本地哈希链且 signature 占位 [P06][P08][P13] | 外部锚定/签名与密钥轮换、保留/法律冻结、可验证导出与访问审计 |
| 历史策略的纯决策复算 | 目前 replay 重建已发生事实；缺少完整有效策略快照自动重算链 [P07][P09][P22] | 固定历史输入与策略、确定性 evaluator、差异输出；默认不触发任何副作用 |
| 轨迹风险的统一事前执行 | trajectory 是事件只读投影 [P17] | 跨动作风险规则、可信特征与统一 authorize 拒绝路径；明确监视失败行为 |
| 跨节点治理与运行控制 | 主要 queue/scheduler/job/subagent 为进程内对象 [P03][P14][P15][P16] | 若目标是集群：租约、原子消费、调度去重、一致策略版本与全局审计关联 |

这些缺口是本次范围内的设计成熟度判断，不能反向推断 ACGS-Lite 全部已经解决；其 production Callable 也明确没有 durable execution/restart recovery，OpenShell 还需宿主认证。[A01][A09]

### 5.5 下一步演进方向

**优先建议：先写“授权到执行契约”的 ADR 和验收设计，不直接引入或替换框架。** 本次不实施以下事项。

| 顺序 | 研究/设计交付物 | 应回答的问题/验收标准 |
|---|---|---|
| P0 | 副作用边界清单与威胁模型 | 哪些工具、MCP/plugin、模型出站、产物操作受控；哪些宿主管理行为属于可信边界；恶意同进程代码是否在模型内 |
| P0 | 统一授权对象与执行消费 ADR | 使用现有 Decision/Receipt 还是增加 ExecutionGrant；精确绑定 caller/tenant/task/call、参数、有效策略、执行目标、有效期；明确缺失/无效/过期/重复授权拒绝 |
| P0 | 有效策略快照与主体绑定规范 | 让同一授权事实可重算；把账号权限与 channel Principal 统一关联，区分人工与自动 reviewer |
| P1 | 持久 attempt/幂等与恢复设计 | 明确 started/completed/failed/partial/unknown；崩溃后先对账，不能自动重跑外部副作用；结合外部系统幂等键 |
| P1 | 审批与审计增强方案 | 需要多人时接 quorum/职责分离；对关键授权建立持久确认、签名与外部锚定；保留现有事件真源 |
| P2 | 轨迹风险规则与纯决策 replay 设计 | 风险规则走同一授权点；固定历史策略复算结果，区分历史视图、决策重算与执行重放 |
| 条件分支 | ACGS-Lite 适配可行性研究 | 如选择接入，应把其判决作为现有治理链的输入，唯一执行权继续归 ToolExecutor；不能让两个执行器各自放行或重复审批 |

授权契约建议覆盖的验收场景：无凭证、伪造发行者、参数篡改、租户/主体错配、策略变更、审批中撤权、重复消费、进程崩溃、审计落盘失败、工具超时但副作用未停止，以及敏感工具组合。现有 security/receipt/trajectory/Docker 测试可作为未来验收入口，但本次没有执行或改动这些测试。[P24]

## 6. 源码证据索引

下列 PyHarness 链接相对本报告指向当前工作区；行号以本次磁盘文件为准。ACGS-Lite 全部指向固定提交，不使用会漂移的 main 链接。符号名用于行号后续变化时定位。

### 6.1 PyHarness

| 编号 | 实际源码路径、定位与作用 |
|---|---|
| P01 | [pyharness/engine.py][P01]：`_build_governance:577`、`build_runner_components:621`、`GovernanceContext` 装配 `862`、`make_runner:1002`、`archive_task_evidence:1137`、`attach_engine_to_ctx:1440` |
| P02 | [pyharness/application/service.py][P02]：`queue_for:283`、`_engine_runner_for:301`、`create_message:539`、`governance_audit:819`、`decide_approval:1081` |
| P03 | [pyharness/core/task_queue.py][P03]：`submit:216`、`_pump:270`、`_run_task:310`、`pause:452`、`shutdown:498`、`cancel:551`、段开闭 `617/631` |
| P04 | [pyharness/core/tools_executor.py][P04]：`_approval_binding:234`、`execute:438`、`_require_wiring:567`、`_approval_round:588`、Provider 路由/线程 `712/731` |
| P05 | [pyharness/governance/context.py][P05]：`principal_of:60`、`authorize:105`、`_emit_decision_issued:159`；另见 `governance/decision.py::DecisionEngine.decide:297` |
| P06 | [pyharness/governance/receipt.py][P06]：`receipt_kind_of:61`、`DecisionReceipt:108`、`verify_receipt:184`、`ReceiptStore.emit:238`、`rebuild_from_log:292`、`verify_chain:336` |
| P07 | [pyharness/governance/audit.py][P07]：`causal_chain:82`、`denied_report:176`、`reconcile:241`；[pyharness/governance/evidence.py](../../pyharness/governance/evidence.py)：归档与引用收集 |
| P08 | [pyharness/events/envelope.py][P08]：`Envelope:33`、`validate_envelope:72`；[pyharness/events/vocab.py](../../pyharness/events/vocab.py)：`SYNC_TYPES:102`、瞬时事件与治理事件注册 |
| P09 | [pyharness/core/session.py][P09]：`append`、`derive_messages:443`、`rebuild_from_log:494`；[pyharness/bus/event_bus.py](../../pyharness/bus/event_bus.py)：分发 `212`、背压 `320` |
| P10 | [pyharness/application/platform_service.py][P10]：`start_run:355`，平台 task/definition/actor metadata 与 queue 提交 `398–447` |
| P11 | [pyharness/application/platform_runtime.py][P11]：`validate_answer:56`、`sandbox:126`、`finish:183`、`release:247`、`execute:262`、`configure_spine:360` |
| P12 | [pyharness/core/agent_loop.py][P12]：`run:156`、`_run_engine:203`、`run_turn:247`、`run_step:304`、`cancel:398`、`wake:423` |
| P13 | [pyharness/persistence.py][P13]：SessionStore、`append:555`、`flush:628`、重试/去重及 fsync 写路径 `725–754`、`open_store:1100` |
| P14 | [pyharness/core/schedule.py][P14]：`_fire:556`、`_tick:583`、`recover:659`、`_ticker:797` |
| P15 | [pyharness/core/jobs.py][P15]：`recover:188`、`start:299`；[pyharness/core/orchestration.py](../../pyharness/core/orchestration.py)：`EngineJobRunner.run:152` |
| P16 | [pyharness/core/subagent.py][P16]：子 Scope `372`、slot `742`、`spawn:756`；[pyharness/core/orchestration.py](../../pyharness/core/orchestration.py)：子日志/审批/执行 `212/288` |
| P17 | [pyharness/application/trajectory.py][P17]：`project_trajectory:11`、`trajectory_page:212`；[platform_service.py](../../pyharness/application/platform_service.py)：`trajectory:200`；[platform_api.py](../../pyharness/desktop/platform_api.py)：轨迹路由 `89` |
| P18 | [pyharness/core/approval.py][P18]：`request:259`、裁决/自动审核 `472/497`、队列联动 `704`、`_ensure_channel:730`；[core/user_ask.py](../../pyharness/core/user_ask.py) |
| P19 | [pyharness/application/automatic_review.py][P19]：`AutomaticReviewer:13`；[approval_policy.py](../../pyharness/application/approval_policy.py)：`validate_rules:11`、`apply_rules:23` |
| P20 | [pyharness/desktop/accounts_api.py][P20]：账号、tenant 与 API 角色闸 `99–132`；[application/accounts.py](../../pyharness/application/accounts.py)：Accounts 与 membership `159` |
| P21 | [pyharness/core/tools_guard.py][P21]：内置守卫 `373–641`、`_evaluate_full:716`；[core/scope.py](../../pyharness/core/scope.py)：`can_use:304`、`tighten:325` |
| P22 | [pyharness/governance/policy.py][P22]：`compute_fingerprint:74`、`PolicyEngine:271`、`from_config:291`、`current:338`、规则启停 `364/381` |
| P23 | [pyharness/core/sandbox.py][P23]：host backend `175`、Docker `198/251`、manager `373`；[core/remote_sandbox.py](../../pyharness/core/remote_sandbox.py)、[core/tool_exec.py](../../pyharness/core/tool_exec.py) |
| P24 | [tests/security/test_governance_bypass.py][P24]；[test_param_tampering.py](../../tests/security/test_param_tampering.py)；[test_principal_identity.py](../../tests/security/test_principal_identity.py)；[test_governance_receipt.py](../../tests/unit/test_governance_receipt.py)；[test_trajectory.py](../../tests/unit/test_trajectory.py)；[test_real_sandbox.py](../../tests/docker_acceptance/test_real_sandbox.py) |

### 6.2 ACGS-Lite

| 编号 | 固定提交源码路径、定位与作用 |
|---|---|
| A00 | [README.md][A00]：发布 wheel/source candidate 的范围声明；[pyproject.toml](https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/pyproject.toml)：版本/依赖 |
| A01 | [src/acgs_lite/governed.py][A01]：GovernedAgent；`GovernedCallable:723`、profile 限制 `764–799`、`issue_grant:802`、`_gate:903`、同步/异步 wrapper `1224–1324` |
| A02 | [src/acgs_lite/legitimacy/authorization.py][A02]：TrustedExecutionContext `33`、ExecutionGrant `84`、ExecutionAuthority `121`、`verify:172`、`resolve_profile:309` |
| A03 | [src/acgs_lite/engine/core.py][A03]：GovernanceEngine、audit mode `71–98`、规则过滤、`validate:920` |
| A04 | [src/acgs_lite/engine/enforcement.py][A04]：`resolve_enforcement:233`；[constitution/rule.py](https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/constitution/rule.py)：Severity、ViolationAction、Rule |
| A05 | [src/acgs_lite/legitimacy/decide.py][A05]：DecisionState `8`、`canonicalize_decision_state:50` |
| A06 | [src/acgs_lite/legitimacy/receipt.py][A06]：ExecutionBoundary `22`、DecisionReceipt `33`、create/hash 校验 |
| A07 | [src/acgs_lite/legitimacy/invariants.py][A07]：`call_matches:105`、`validate_receipt_for_execution:129`、结构化审批 `183` |
| A08 | [src/acgs_lite/maci/enforcer.py][A08]：`check:55`、`check_no_self_validation:101` |
| A09 | [src/acgs_lite/integrations/openshell_governance.py][A09]：ActionEnvelope `166`、GovernanceDecision `192`、router `649`、approval 路由 `700/768`、outcome `919`、app `1014` |
| A10 | [src/acgs_lite/legitimacy/signing.py][A10]：Ed25519ReceiptSigner `90`、SignedReceipt `170`、pinned-key `verify:182` |
| A11 | [src/acgs_lite/legitimacy/ledger.py][A11]：InProcessGrantLedger `61`、consume `74`、finalize `137`、mark_unknown `163` |
| A12 | [src/acgs_lite/audit.py][A12]：JSONLAuditBackend `160`、AuditLog `315`、record `383`、validate_durable_state `463`、record_durable `481`、链验证/恢复 |
| A13 | [src/acgs_lite/adapters/langgraph.py][A13]：`_decision_from_result:326` 等适配器状态映射 |
| A14 | [src/acgs_lite/constitution/policy_lifecycle.py][A14]：PolicyState/GateType `21/41`、orchestrator `204`、内存状态 `242–246` |
| A15 | [src/acgs_lite/openshell_state.py][A15]：JSON/SQLite/Redis backend、PersistentGovernanceState `272`；[constitution/quorum.py](https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/constitution/quorum.py)：cast `146`、QuorumManager `191` |
| A16 | [src/acgs_lite/legitimacy/replay_verify.py][A16]：ReplayInputs `29`、`replay_and_verify:66` |
| A17 | [src/acgs_lite/trajectory.py][A17]：三类规则、TrajectoryMonitor `197`；[integrations/anthropic.py](https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/integrations/anthropic.py)：checkpoint `358`、默认 monitor `522`；[integrations/mcp_server.py](https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/integrations/mcp_server.py)：checkpoint `398` |
| A18 | [src/acgs_lite/constitution/sandbox.py][A18]：PolicySandbox `76`、test_constitution `95` |

[P01]: ../../pyharness/engine.py
[P02]: ../../pyharness/application/service.py
[P03]: ../../pyharness/core/task_queue.py
[P04]: ../../pyharness/core/tools_executor.py
[P05]: ../../pyharness/governance/context.py
[P06]: ../../pyharness/governance/receipt.py
[P07]: ../../pyharness/governance/audit.py
[P08]: ../../pyharness/events/envelope.py
[P09]: ../../pyharness/core/session.py
[P10]: ../../pyharness/application/platform_service.py
[P11]: ../../pyharness/application/platform_runtime.py
[P12]: ../../pyharness/core/agent_loop.py
[P13]: ../../pyharness/persistence.py
[P14]: ../../pyharness/core/schedule.py
[P15]: ../../pyharness/core/jobs.py
[P16]: ../../pyharness/core/subagent.py
[P17]: ../../pyharness/application/trajectory.py
[P18]: ../../pyharness/core/approval.py
[P19]: ../../pyharness/application/automatic_review.py
[P20]: ../../pyharness/desktop/accounts_api.py
[P21]: ../../pyharness/core/tools_guard.py
[P22]: ../../pyharness/governance/policy.py
[P23]: ../../pyharness/core/sandbox.py
[P24]: ../../tests/security/test_governance_bypass.py
[A00]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/README.md
[A01]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/governed.py
[A02]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/legitimacy/authorization.py
[A03]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/engine/core.py
[A04]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/engine/enforcement.py
[A05]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/legitimacy/decide.py
[A06]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/legitimacy/receipt.py
[A07]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/legitimacy/invariants.py
[A08]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/maci/enforcer.py
[A09]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/integrations/openshell_governance.py
[A10]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/legitimacy/signing.py
[A11]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/legitimacy/ledger.py
[A12]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/audit.py
[A13]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/adapters/langgraph.py
[A14]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/constitution/policy_lifecycle.py
[A15]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/openshell_state.py
[A16]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/legitimacy/replay_verify.py
[A17]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/trajectory.py
[A18]: https://github.com/acgs-ai/acgs-lite/blob/da5732dc8944067ceacc16a9b27d220334f1c3f3/src/acgs_lite/constitution/sandbox.py
