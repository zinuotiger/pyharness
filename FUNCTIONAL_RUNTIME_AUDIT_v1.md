# Functional Runtime Audit v1

> **性质**:只读审查报告(未修改任何代码/测试/文档)。
> **基线**:`HEAD = d2ccd45` · `main...origin/main [ahead 46]` · 未 push。
> **方法**:全量测试实跑 + AST 死符号扫描 + 4 路只读深挖(CLI/ACP · Persistence/Session · 编排族 · 桌面壳) + 手工核对 Agent Loop / Governance / Approval。
> **证据标注**:`[实测]` = 本轮直接跑出;`[代码级]` = 读码结论;`[替身]` = 被测试替身遮蔽。
> **下游**:修复设计见 `RT-FIX-PLAN-v1.md`。

---

## 0. 基线

| 项 | 值 |
|---|---|
| 全量测试 | **1719 collected / 1717 passed / 0 failed / 2 skipped** |
| 覆盖率 | **79%**(17,907 语句);有覆盖模块 73 个 |
| `EVENT_TYPES / SYNC_TYPES / TRANSIENT` | **77 / 14 / 3** |
| 测试文件 | 65;其中**仅 8 个(12%)使用真实引擎装配** |
| 测试替身类 | **68 个** |
| 零引用私有符号 | **26**(其中约 6 个为框架回调误报) |
| 零引用公开符号 | **31** |
| 覆盖率最低的用户面 | `cli.py` **59%**(1127 语句) · `application/service.py` **58%**(759 语句) · `desktop_native/**` **0%**(934 语句) |

**总体判断:未发现"整体不可运行"的功能。** 主链、编排、工具执行、治理边界、持久化真源均为真实接线。问题集中在三类:① 部分能力接口与测试齐备但**生产从不调用**;② **壳层与编排层端到端覆盖极薄(12%)**;③ 少数**真实缺陷**(F-25~F-28)。

---

## 1. 功能运行矩阵

| # | 模块 | 入口 | 调用链 | 测试覆盖 | 状态 | 问题 |
|---|---|---|---|---|---|---|
| 1 | CLI commands | `cli.main()` | 16 子命令 → `_attach_engine` → 真引擎/轻门面 | **6/16 零调用**(plan·search·fork·session·schedule·job) · 4/16 完整 · 6/16 部分 | **PARTIAL** | `chat` 唯一 "E2E" 用例断言的是失败路径;`_cmd_workflow` sid 恒空;13 处 `EVT-100` 零覆盖 |
| 2 | ACP | `acp.serve` [acp.py:613](pyharness/acp.py) | `chat`→`_ensure_engine_queue`→`attach_engine_to_ctx` | 替身强制早返回 ⇒ **真装配分支从未执行** | **PARTIAL** | 3 条错误分支 + `wait_for` 缺失 CYC-999 无测试 |
| 3 | Agent Loop | `AgentLoop.run` [agent_loop.py:152](pyharness/core/agent_loop.py) | `wake`→`_run_engine`→`run_turn`→工具步 | 16 用例**全用 FakeLLM/FakeTools/FakeScope/FakeCtx**;6 终态 reason 均有覆盖 | **WORKING** | `_REASONS`(:71)死常量;`stall` 仅 1 个测试文件 |
| 4 | Tool execution | `ToolExecutor.execute` [tools_executor.py:430](pyharness/core/tools_executor.py) | 关1a→1b→关2 治理→关2.5 审批→关3 Provider→关4 finalize | 错误码全覆盖 | **WORKING** | `_reject`(:633)·`_invoke`(:737)死代码 |
| 5 | Governance | `GovernanceContext.authorize` [context.py:81](pyharness/governance/context.py) | →`GuardChain._evaluate_full`→`DecisionEngine` | 真装配有覆盖 | **PARTIAL** | 无独立裁决权;**`policy.updated` 生产永不发射**;`audit.py` 零消费;策略启停 API 零调用 |
| 6 | Approval | `ApprovalProvider.request` [approval.py:269](pyharness/core/approval.py) | 通道判定→信任/合并→`_pending[seq]`→裁决 | 真实 provider 有测试 | **WORKING** | APR-502 仅 1 文件;`_pending` 以 **seq** 为键、跨会话共享 |
| 7 | Persistence | `SessionStore.append` [persistence.py:343](pyharness/persistence.py) | 强同步 `_flush_pending_all` / 攒批 `_flush_batch` / `flush` | 面广;**无部分写失败用例** | **PARTIAL** | 仅 `_flush_batch` 有上限+暂停;部分写→重复行;`flush_interval_s` 无消费者;`SessionStore.repair` 仅 tests |
| 8 | Session | `SessionLog.append` [session.py:257](pyharness/core/session.py) | append-only → 总线 → 落盘 | INV-01 编号化用例 | **WORKING** | 未发现缺陷;**无第二份消息历史真源**(全量枚举写盘点确认) |
| 9 | SubAgent | `subagent.spawn` [subagent.py:721](pyharness/core/subagent.py) | →`_run_child`→`runner.run_child`→真 AgentLoop | 25+ 用例全用 FakeRunner;真链路覆盖**本轮才建立** | **WORKING** | 修复前每次子 Agent 工具调用皆 CYC-999(已修) |
| 10 | Workflow | `WorkflowRunner.run` [workflow.py:45](pyharness/core/workflow.py) | 顺序步骤→`queue.submit`→真 AgentLoop | **全部 `_FakeRunner`;零端到端** | **WORKING** | 顺序编排符合 ADR-017(未做 DAG,正确);生产链零覆盖 |
| 11 | Scheduler | `Scheduler._tick` [schedule.py:530](pyharness/core/schedule.py) | `_fire`→`task_queue.submit`→真 AgentLoop | FakeQueue+FakeSession | **WORKING** | **无测试置 `auto_ticker=True`** ⇒ 泵启动零断言 |
| 12 | MCP | `_preload_mcp` [engine.py:362](pyharness/engine.py) | `McpClient.connect`→**真 subprocess** | **有真实子进程测试** | **WORKING(默认关闭)** | 默认 `mcp_servers: []`;TLB-805 多数分支未覆盖 |
| 13 | Desktop(Web) | `DesktopApp.mount_api` [app.py:249](pyharness/desktop/app.py) | 54 路由 → `ApplicationService` | **约 26 条路由零测试** | **PARTIAL** | 真装配路径从未被测试执行;租户正则缺陷;17 个零引用私有方法 |
| 14 | Desktop(Native) | `desktop_native/app.py:main` | `MainWindow`→`NativeController` | **0 个测试可运行** | **UNVERIFIABLE** | 缺 PySide6 → collection error;无 conftest/`--ignore` ⇒ 默认 `pytest` 直接失败 |

---

## 2. 五类问题清单

### 2.1 代码存在但没有真实调用

| ID | 项 | 证据 |
|---|---|---|
| **F-01** | **`policy.updated` 生产永不发射** | 唯一生产调用者 `PolicyEngine.emit_assembled`([policy.py:392](pyharness/governance/policy.py)) **零引用**;装配 `_build_governance`([engine.py:463-470](pyharness/engine.py)) 从不调用它;`disable_rule`/`enable_rule` 同样零调用 |
| **F-02** | `governance/audit.py` 构造但零消费 | [engine.py:659](pyharness/engine.py) 构造并注入;全部调用点在 tests |
| **F-03** | `tools_executor._reject`(:633) · `_invoke`(:737) | AST 零引用;`_invoke` 已被 spec 记为契约 ⇒ 处置须先改 spec |
| **F-04** | `desktop/app.py` **17 个零引用私有方法** | `:528/:537/:541/:561/:582/:646/:674/:788/:793/:806/:815/:851/:889/:892/:910` 等 |
| **F-05** | `application/models.py` **14 个 `*View` 类零引用** | 全模块仅 `SHELL_CAPABILITIES` 被 import |
| **F-06** | `SessionStore.repair`([persistence.py:513](pyharness/persistence.py)) 仅 tests 调用 | 生产走 `repair.repair_session` |
| **F-07** | `flush_interval_s` **零消费者** | 仅 `persistence.py:54/312/321` + `config.py:75/698` 赋值 |
| **F-08** | `assemble_real_engine`([engine.py:914](pyharness/engine.py)) **无 `pyharness/` 内调用者** | 仅 scripts(9 处)+tests;docstring 称 "desktop create_message 用",而 desktop 走 `build_runner_components` |
| **F-09** | `ctx.agent` **全包从未赋值** | 仅 `cli.py:547`、`engine.py:935` 硬编码 `None` |
| **F-10** | `/new` 全壳静默 no-op | `cmd_new`([commands.py:203](pyharness/core/commands.py)) 依赖 `ctx.agent`;`ctx.render` 亦恒 None(唯一赋值 `cli.py:551` )⇒ 连提示都被丢弃。`/exit` 已修(本轮复核 `shell.exit_code` 确为 0),两者行为不同 |
| **F-11** | 零引用公开函数 13 个 | `service.runner_seam`·`approval.{deny_async,create_approval}`·`errors.to_event`·`llm.{rate_limit_add,mark_degraded_from}`·`policy.{disable_rule,enable_rule}`·`receipt.{verify,is_approval}`·`bridge.{replay_frame,on_close}`·`proc.wait_result`·`plugin_loader.manager_state`·`cli.ExitResult`·`registry.tenants` |

### 2.2 测试通过但关键路径没有执行

| ID | 项 | 证据 |
|---|---|---|
| **F-12** | **全仓库仅 12% 测试文件使用真实引擎** | 8/65 使用 `assemble_real_engine`/`build_runner_components`/`build_spine` |
| **F-13** | `chat` 的"真实装配冒烟"用例断言**失败路径** | `test_chat_pipe_full_path`([test_cli.py:1262](tests/unit/test_cli.py)) 不调 `_attach_engine`,断言 `[任务失败]`+`task.failed` |
| **F-14** | ACP `chat` 真装配分支从未执行 | `_ensure_engine_queue`([acp.py:357](pyharness/acp.py)) 因 `_session is log_` 早返回 |
| **F-15** | **Workflow 生产链零端到端** | `test_workflow.py` 全部 `_FakeRunner`;`_cmd_workflow` 引擎装配无测试 |
| **F-16** | **Scheduler 泵启动零断言** | 无任何测试构造 `auto_ticker=True` |
| **F-17** | 桌面真装配路径从未执行 | `_engine_runner_for`→`build_runner_components`([app.py:493-526](pyharness/desktop/app.py)) 零测试 |
| **F-18** | 子 Agent 真链路覆盖**本轮才建立** | 25+ 用例的 `SuccessRunner.run_child` 只 append `agent.message`,从不构造 AgentLoop |

### 2.3 替身掩盖真实问题

| 替身 | 位置 | 遮蔽了什么 |
|---|---|---|
| `FakeLoop`/`FakeStore`/`FakeTools` | `tests/unit/test_agent.py` | Agent 生命周期真实装配 |
| `FakeLLM`/`FakeTools`/`FakeScope`/`FakeCtx` | `tests/unit/test_agent_loop.py` | 主循环对真实 scope/tools/session 的行为 |
| `SuccessRunner`/`GatedRunner`/`FailRunner` | `test_subagent.py:62/83/107` | `EngineSubagentRunner.run_child`→真 AgentLoop→`tools.execute` |
| `_FakeRunner` | `test_workflow.py:19` | 整条 workflow→AgentLoop |
| `FakeQueue`/`FakeSession` | `test_schedule.py:53/70` | 真 TaskQueue 泵 + 真事件落盘 |
| `FakeQueue`/`FakeApproval`/`FakeAgent` | `test_cli.py:75/92/107` | 真队列/真审批/Agent 生命周期 |
| `_Runner`/`_FakeManager`/`_FakeApproval` | `test_acp.py:51/90/448` | ACP 引擎装配、会话管理、审批 |
| `_FakeStore`/`_FakeQueue`/假 spines | `test_desktop.py:84/312/640` | 桌面真引擎装配 |
| 故障注入恒首调抛 | `test_persistence.py` / `test_session.py:644` | **部分写失败**(永远在写第一个字节前抛) |

> `InlineTransport`(MCP)与 `GuardChain` 注入属**设计内接缝**,不计为掩盖。

### 2.4 文档描述与实际行为不一致

| ID | 位置 | 文档说法 | 实际 |
|---|---|---|---|
| **F-19** | [desktop/app.py:127-129](pyharness/desktop/app.py) | 注释称"阻断伪造 `X-PyHarness-Tenant` 访问他租户会话" | 正则 `[0-9a-zA-Z]+`([:35-36](pyharness/desktop/app.py)) **不含 `-`/`.`**:`s-fork-abc123` 只匹配到 `s-fork` → 查不到 → **回落信任客户端头**;而 `cli.py:1125` 正是这样构造 fork id。缓解:仅绑 localhost |
| **F-20** | `docs/specs/commands.py.md` | `cmd_exit` 以 `ctx.agent is None` 为闸门;写序"先 `user.command` 再 `_gated`";`cmd_status` 用 `s.meta`/`s.last_seq()`/`s.session_id`;依赖 `shlex` | 实现均为另一套(7 处);上述属性**在 `SessionLog` 上不存在**;`commands.py` 从不 import `shlex` |
| **F-21** | [commands.py:13](pyharness/core/commands.py) | "三壳共用内核 … chat/desktop/ACP" | `handle_slash` **唯一调用者是 `cli.py:821`**;桌面/ACP 从不分发斜杠命令 |
| **F-22** | [persistence.py:378](pyharness/persistence.py) | "不丢任何一条、**不重复任何一条**" | 部分写失败时整批回填 ⇒ **会重复** |
| **F-23** | MAP.md / PRD-Core.md / TECH-ANCHOR.md | "四层架构" | `governance` 命中 **0/0/0**;治理层未进架构总览 |
| **F-24** | 计数漂移 | `EVENT-SCHEMA.md:101` 写 74;`CODE-MATRIX.md:9` 写 40 文件/21,528 行/1298 passed/88% | 实测 **77**;85 文件/33,422 行/1717 passed/79% |

### 2.5 真实缺陷

| ID | 级别 | 缺陷 | 证据 |
|---|---|---|---|
| **F-25** | P1 | **部分写失败 → 重复落行** | [persistence.py:400-409](pyharness/persistence.py):逐行 write 后一次 flush;OSError 时整批回填(含已写成功行);`replay()`(:473)**不按 seq 去重** ⇒ 重复行进入 `derive_messages`。**无任何测试覆盖部分写** |
| **F-26** | P1 | **强同步路径无重试上限、无暂停** | `_flush_pending_all`(:373)自身不自增 `_fail_streak`、无上限、无暂停;`append`(:363)自增但同样无上限/暂停;`flush`(:442)自增但无上限/暂停。守卫**只在** `_flush_batch`(:463) |
| **F-27** | P1 | 跨会话审计分裂 → **子会话恒报 INV-04 违规** | `GuardChain` 绑定父会话 ⇒ 子 Agent 的 `guard.evaluated` 落**父**日志而 `tool.call`/`tool.result` 落**子**日志。`[实测]` `AuditSystem(session=child).reconcile()` → `['NO-GUARD-EVENT:call_id=c1']` |
| **F-28** | P1 | 审批 `_pending` 以 **seq** 为键且跨会话共享 | [approval.py:294](pyharness/core/approval.py);`_ensure_channel` 对子 ctx 回落 provider 缺省通道 ⇒ 交互外壳下子 Agent 审批与父会话**同键空间**。`[代码级]` |
| **F-29** | — | **= F-01(重复编号,以 F-01 为准)** | 见 §2.1 |
| **F-30** | P2 | `child_session._record` **缺瞬时事件守卫** | [orchestration.py:144](pyharness/core/orchestration.py) vs [engine.py:732](pyharness/engine.py) 的 `hasattr(payload,"model_dump_json")`。当前不可达(`streaming=False`) |
| **F-31** | P3 | **`_cmd_workflow` 会话 id 恒为空** | [cli.py:1406](pyharness/cli.py) 读 `getattr(log_,"session_id","")`,而 `SessionLog` 只有 `sid`([session.py:93](pyharness/core/session.py)) ⇒ 文本与 `--json` 的 `sid` 永远是 `""`;该 handler 无任何测试 |
| **F-32** | P3 | `_REASONS` 死常量且词表不符 | [agent_loop.py:71](pyharness/core/agent_loop.py) 声明含 `timeout`/`close`,循环从不发这两个 |
| **F-33** | P3 | 工程基建缺失 | **248 处 `noqa`(BLE001 ×228)+ 249 处 `except Exception`,而 pyproject 无 ruff/mypy** |

**未覆盖 error path 汇总**:13 处 `_cmd_*` 的 `EVT-100`/`EVT-106` 分支 · `acp._ensure_repaired` 分支 · `persistence` 的 `:584/725/734/745` · `tenant_settings` CFG-601 ×3 · `desktop._unexpected_handler` CYC-999 · `CRED-701`(webhook) · 部分写失败 · `APR-502`(仅 1 文件) · MCP TLB-805 多数分支 · Scheduler `_session_ctx` 缺失分支。

---

## 3. 结论

| 层面 | 结论 |
|---|---|
| **内核**(Session/Persistence/Tool/Guard/Approval/Loop) | **真实可运行**。主链端到端有真实证据;**无第二真源**、治理无旁路、`authorize` 单入口成立 |
| **编排**(SubAgent/Workflow/Scheduler/MCP) | **真实可运行**(RT-GOV-01 修复后 SubAgent 才真正可用);但**端到端证据薄**:Workflow 零 E2E、Scheduler 泵零断言、MCP 默认关闭 |
| **壳层**(CLI/ACP/Desktop×2) | **最弱**。CLI 6/16 子命令无测试、覆盖率 59%;Desktop 26/54 路由无测试;**Native 完全无法验证** |
| **治理层** | **边界真实、能力未接线**。授权面可信;但 `policy.updated` 永不发射、`audit` 零消费、策略启停 API 零调用 |

**本报告未做**:未改任何代码/测试/文档;未 commit。临时产物均在 `tmp/`(`.gitignore` 覆盖)。
