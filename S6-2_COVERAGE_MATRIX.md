# S6-2_COVERAGE_MATRIX.md — S6-2a 覆盖矩阵（只读勘察）

> **阶段**：S6-2a（覆盖矩阵，只读）｜ **日期**：2026-09-15 ｜ **HEAD**：`4f05c95`（S6-1 Freeze）
> **唯一编号来源**：`docs/INVARIANT_REGISTRY.md`（Canonical Invariant Registry）
> **性质**：**只读勘察产物**。本文件**未修改任何** `pyharness/**` 生产代码 / 现有测试 / Registry / Event Schema / ADR / Governance Core；**未新增测试**；**未 commit / 未 push**。
> **基线**：**1671 passed / 2 skipped / 0 failed** · `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`
> **本文件状态**：覆盖矩阵完成（缺口已实测核验）；**等待 review**。**未进入 S6-2b。**

---

## 0. 方法与口径

| 项 | 说明 |
|---|---|
| **编号** | 一律用 Registry 的 Canonical ID。旧编号引用**只在 §5.2 作为"错标"登记**，不作为归属依据（守 S6-2 纪律 1/2）。 |
| **`Test Type`** | `unit`（`tests/unit/*`，模块内）· `integration`（跨模块，`tests/integration/*`）· `invariant`（`tests/invariants/*`，带 `pytestmark = pytest.mark.invariant`） |
| **`Status`** | `covered` = 决定性性质均有可执行证据 · `partial` = 部分性质有证据、部分缺失 · `gap` = 决定性性质**无可执行证据** |
| **核验方式** | 全部缺口**逐项实测**（AST 扫描 / grep / 读取用例体），**不照抄 Registry 的表述**；下文凡实测与 Registry 表述有差异之处均显式标注。 |
| **重要范围事实** | **`tests/invariants/` 内 `INV-01`~`INV-09` 的编号化用例数 = 0**（4 个文件分别是 bus / config / events 骨架 + 治理层 `INV-G/R/E/A`）。⇒ **九条 ID 的"编号化专项"全部待 S6-2b 建立**。 |

**实测命令基线**（可复核）：`.venv/Scripts/python.exe -m pytest tests --ignore=tests/unit/test_desktop_native.py --ignore=tests/unit/test_shell_parity.py`（本机 `.venv` 缺 PySide6/qasync，两文件无法收集，为既有环境限制）。

---

## 1. 总览

| INV ID | Canonical Name | 现有用例数 | Test Type | Status | 真正缺口数 |
|---|---|---:|---|---|---:|
| INV-01 | 日志只追加 / 历史必由日志派生 | 7+ | unit | `partial` | **3** |
| INV-02 | 无绕过 agent-loop 直调 llm | **1** | unit | **`gap`** | **3** |
| INV-03 | rebuild 与缓存一致 | 4+ | unit | `partial`（接近 covered） | 2 |
| INV-04 | 无 guard 事件即非法执行（含单调拒绝 / 无 bypass） | 8 | unit | `partial` | 3 |
| INV-05 | 拒绝后零副作用 | 6+ | unit | `partial`（接近 covered） | 2 |
| INV-06 | 执行 args = 日志 args | 10+ | unit | `partial`（接近 covered） | 2 |
| INV-07 | 单进程 | 2 | unit | `partial` | 2 |
| INV-08 | 阶段 import 方向 / 依赖方向 | 3 + 1 AST | unit | `partial` | 2 |
| INV-09 | 日志无凭据 / 全出口脱敏 | 8 | unit | `partial`（接近 covered） | 2 |

**共性结论**：九条的**共性缺口**是"**无编号化专项用例**"（`tests/invariants/` 内 0 条）与"**无静态结构性扫描**"（除治理层 `INV-R5` 一个先例外）。**个性缺口**见 §3/§4。

---

## 2. 逐项矩阵（INV-01 ~ INV-09）

### INV-01

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-01` |
| **Definition** | 会话 JSONL 为**唯一真源**且**只追加**（无 update/delete/改写/清空 API）；一切历史与派生形态**均由日志派生**，不存在第二份权威状态；派生视图可整体丢弃重建。（详见 Registry §1） |
| **Existing Tests** | `test_session.py::test_method_surface_append_only_inv01` · `::test_append_only_no_second_history_store_inv02` · `::test_finished_flush_failure_keeps_closed_but_log_empty` · `::test_close_...` · `test_goal.py::test_rebuild_from_events_matches_live_state` · `test_plan_mode.py::test_real_session_full_lifecycle_events_valid` · `test_compaction.py::test_compact_appends_declaration_only_append_only` · `test_agent_loop.py::test_single_turn_text_complete` · `test_subagent.py::test_join_unknown_and_double_join_evt101` |
| **Test Type** | `unit` |
| **Current Evidence** | ① 分句①（只追加）由 `test_method_surface_append_only_inv01` 的**方法面白名单 + 禁用片段**断言钉死（读取类公开方法集，断言无 `update/delete/remove/pop/clear/rewrite/…`）；② 分句②（无第二份历史）由 `test_append_only_no_second_history_store_inv02` 的**实例属性集合断言**钉死（仅允许 `_cache/_seqs/_folded/_holes_warned`）；③ 派生一致性由 goal/plan_mode 的 rebuild 用例旁证。 |
| **Missing Property** | ① **`append` 后文件 hash 不变** —— **实测 `tests/` 内无任何针对会话 JSONL 的 hash 不变断言**（`hashlib` 仅出现于 `test_skill_registry.py`，与日志无关）；② **"除 `session.py` 外无 `append` 到消息列表路径"**（`PRD-Core.md:360`）—— **无全库静态扫描**；③ `tests/invariants/` 内**零**编号化用例。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv01_append_does_not_mutate_existing_bytes`（写前记录文件字节+hash，append 后断言**前缀字节逐字不变**、hash 变化仅因追加）· `::test_inv01_no_second_message_list_write_path`（**静态扫描** `pyharness/**`：除 `session.py` 外无对消息列表的 append/写口） |
| **Risk** | 低—中。分句①/②证据**充分**；缺的是"**物理不可变性**"（hash/前缀字节）与"**全库唯一写口**"两个**结构性**断言 —— 这两者恰是 `INV-01` 的"唯一真源"命题的**物理保证**，当前只由行为断言间接承载。 |
| **Status** | `partial` |

### INV-02

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-02` |
| **Definition** | 全库 `llm.chat` 的**唯一合法调用方 = agent-loop（`run_turn`）**；任何模块禁止直连模型端点、禁止绕过循环（= 绕过轮数 / 预算 / 取消三闸）。 |
| **Existing Tests** | `test_agent_loop.py::test_llm_chat_only_entry_point_inv02`（**全库唯一**） |
| **Test Type** | `unit` |
| **Current Evidence** | 该用例用 `FakeLLM` 断言：① 两轮 `llm.chat` 各携带**日志派生**的上下文；② 跨输入上下文连续；③ `run_turn` 之外无 `llm.chat` 调用路径（**在该测试场景内**）。**其中①②实为 `INV-01` 的性质**（见 §5.1）。 |
| **Missing Property** | ① **决定性性质（"全库唯一调用方"）无证据** —— 现有用例是**行为级 + 替身**，**不扫描真实源码**；② 无**静态调用图**用例；③ `tests/invariants/` 内零编号化用例。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv02_llm_chat_sole_caller`（**AST 静态扫描** `pyharness/**`：`.chat(` / `.chat_stream(` / `.json_chat(` / `.chat_with_fallback(` 的调用点必须全部落在**允许集**内；沿用 `test_inv_r5_no_second_persistence_source` 的 AST+字符串扫描先例）· 辅以 `::test_inv02_no_direct_endpoint_client`（除 `llm.py` 外无 LLM 端点客户端构造） |
| **Risk** | **高 —— 见 §7。实测发现 `auto_title.py:36` 直接调用 `ctx.llm.chat(...)` 且为生产接线**（`engine.py:805-806`）。⇒ 一个**朴素的静态断言在当前代码上即为 RED**。此缺口**不能按普通 gap 处理**，须先裁定（§7）。 |
| **Status** | **`gap`** |

### INV-03

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-03` |
| **Definition** | `rebuild_from_log` / `rebuild_from_events` 后派生缓存与重建前**逐事件（逐行）一致**；含**增量读一致**、**编辑重放**、**二次 rebuild 无重复**；`history_cache` 仅当日志尾部未变时有效，任何 `append` 后整体失效。 |
| **Existing Tests** | `test_session.py::test_rebuild_from_log_invariant_inv03` · `::test_rebuild_derived_cache_invalidation_on_append` · `::test_events_after_incremental_gwt_s3_04` · `test_session_query.py::test_rebuild_full_matches_incremental` · `::test_rebuild_then_incremental_no_dup` · `::test_rebuild_single_session_scope` · `::test_edited_updates_target_row` |
| **Test Type** | `unit` |
| **Current Evidence** | **四类性质均有直接证据**：① 全量 vs 增量逐行一致（`test_rebuild_full_matches_incremental`）；② **二次 rebuild 无重复**（`test_rebuild_then_incremental_no_dup` + 行键唯一断言）；③ 编辑重放（`test_edited_updates_target_row`）；④ 缓存失效（`test_rebuild_derived_cache_invalidation_on_append`、`test_session.py:602` 断言 `history_cache is None`）。⇒ **本 ID 是九条中证据最完整者之一**。 |
| **Missing Property** | ① 无编号化专项在 `tests/invariants/`；② **`test_tools_guard.py` 的 6 处旧 `INV-03` 误标**使"INV-03 覆盖"读数**虚高且不可信**（修正前无法从编号读出真实覆盖）。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv03_rebuild_is_idempotent_and_matches_incremental`（**重述**上述断言并**编号化**；不删除原用例） |
| **Risk** | 低。性质已充分验证；风险主要是**编号读数失真**（误标），修正后即消除。 |
| **Status** | `partial`（**接近 covered**） |

### INV-04

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-04` |
| **Definition** | **(a) 执行前必有求值事实**：每个 `tool.result`/`tool.error` 前必有**同 `call_id`** 的 `guard.evaluated`，缺失 = 非法执行；**(b) 结构单调性**：guard 决策恰三值、**无 bypass**，`GuardChain` 无 override/bypass/force_allow/set_allow/execute/移除/重排/翻回 API，且一次 reject 不可被后续挂载翻回 allow。 |
| **Existing Tests** | `test_tools_guard.py::test_monotonic_api_surface_no_allow`（**已标 INV-04**）· `::test_evaluate_allow_single_evaluated_event` · `::test_decision_tri_value_exact_no_bypass` · `::test_guard_base_contract` · `::test_forbidden_bypass_api_attribute_error` · `::test_reject_deterministic_not_flippable` · `::test_register_after_reject_cannot_rescue` · `test_engine.py::test_guard_from_config_injects_schema_validator` · `::test_s23_tc_policy_rules_carry_injected_params` |
| **Test Type** | `unit` |
| **Current Evidence** | 面 (a)：`test_evaluate_allow_single_evaluated_event`（全链 allow 恰一条 `guard.evaluated`）+ `test_engine.py` 的 g1 注入断言（`validator=None` ⇒ g-schema 恒 allow ⇒ INV-04 空转，反证有效）。面 (b)：`test_forbidden_bypass_api_attribute_error`（`_FORBIDDEN_API` 逐名 `hasattr` 假）+ `test_register_after_reject_cannot_rescue`（追加 allow 型 guard 仍 reject）+ `test_monotonic_api_surface_no_allow` + `test_decision_tri_value_exact_no_bypass`。⇒ **两个面都有直接证据**。 |
| **Missing Property** | ① 本 ID **名下无任何用例位于 `tests/invariants/`**；② 当前覆盖由 **6 条误标旧 `INV-03`** 的用例"隐形"承载（§5.2）⇒ **编号修正前，本 ID 的真实覆盖无法从编号读出**；③ **无"全库凡执行必经 `tools.execute`"的静态扫描**（面 (a) 的**全库版**：现仅有单点行为断言）。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv04_no_bypass_api_surface` + `::test_inv04_every_execution_has_guard_evaluated`（重述并编号化）· `::test_inv04_single_execution_entry_static`（**静态扫描**：`pyharness/**` 中 Provider 执行入口仅 `tools_executor`） |
| **Risk** | 中。性质本身证据强，风险在**编号可读性**（误标）与**全库执行入口**缺静态保证（现有 `tools_executor._require_wiring` 为运行期 fail-closed，非静态）。 |
| **Status** | `partial` |

### INV-05

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-05` |
| **Definition** | 任意 reject（scope / guard / critical / 审批 `denied` / `timeout`）后 **Provider 调用计数 = 0** 且**无该 `call_id` 的 `tool.result`**；拒绝须**强同步**落 `guard.rejected`，落盘失败即 fail-closed 上抛。 |
| **Existing Tests** | `test_tools_guard.py::test_reject_event_pair_order_and_sync` · `::test_scope_hidden_reject_events_grd401` · `::test_reject_deterministic_not_flippable` · `test_tools_executor.py::test_scope_hidden_terminal_reject` · `::test_...`（`@parametrize("verdict", ["denied","timeout"])` 两处：`:431` 断言"不执行"，`:1017` 断言"Provider 零执行"）· `test_tool_fs.py::test_executor_critical_delete_denied_zero_side_effect` · `test_cli.py::test_run_headless_rejected_listing` |
| **Test Type** | `unit` |
| **Current Evidence** | **各拒绝路径均有零副作用断言**：guard/scope（G RD-401）、critical（`test_executor_critical_delete_denied_zero_side_effect`）、审批 `denied`/`timeout`（两处 parametrize）。强同步留痕由 `test_reject_event_pair_order_and_sync` 断言（先 `evaluated` 后 `rejected`，`sync=True`）。⇒ **本 ID 覆盖面广**（4 个领域）。 |
| **Missing Property** | ① 无编号化专项在 `tests/invariants/`；② 覆盖**分散在 4 个文件**，**无单一编号锚点** ⇒ 无法据编号判断"是否所有拒绝路径都已覆盖"；③ 缺"**拒绝路径全覆盖清单**"断言（新增一条拒绝路径时无机制提醒补测）。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv05_all_reject_paths_zero_side_effect`（**参数化"拒绝路径 × 断言"矩阵**，把既有 4 处断言收敛为单点编号锚） |
| **Risk** | 低。性质已被验证；风险是**可发现性**（新增拒绝路径时易漏测）。 |
| **Status** | `partial`（**接近 covered**） |

### INV-06

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-06` |
| **Definition** | `tool.call` 同时存 `raw_args`（LLM 原文）与强类型 `args`（实际执行），**真实函数收到的参数 == 日志 `args` 逐字段一致**；畸形参数 → `TLB-803` 且 Provider 零调用、不写 `tool.call`。 |
| **Existing Tests** | `test_tools_registry.py::test_malformed_args_tlb803_zero_exec`（**@parametrize 8 例**：`path=123` / `maxLength` 超限 / enum 越值 / `extra` 多余字段 / 不可转换串 / array 元素类型错 / 嵌套类型错 / anyOf 非 null）· `::test_non_identifier_property_name` · `test_tools_executor.py::test_param_wrong_type_zero_provider` · `::test_param_fail_extra_field_zero_provider` · `::test_happy_full_pipeline_event_order` · `test_llm.py::test_assistant_passthrough_raw_json` · `::test_multi_calls_parsed_in_order` · `::test_tool_round_content_null_normalized` · `test_spill.py::test_handle_executes_read` · `test_tool_fs.py::test_write_over_1mb_contract_tlb803` · `test_tool_web.py::test_validate_args_search_contract` |
| **Test Type** | `unit` |
| **Current Evidence** | 双份存档（`raw_args`/`args`）由 `test_assistant_passthrough_raw_json`、`test_happy_full_pipeline_event_order` 断言；"执行 == 日志 args"由 `test_tools_executor.py::test_...`（`prov.last == {...}`，`:300`）与 `test_spill.py`（schema 默认值落到 args）断言；畸形参数零调用由 8 例 parametrize + 各工具契约层用例断言。⇒ **覆盖面广**。 |
| **Missing Property** | ① 无编号化专项在 `tests/invariants/`；② **`PRD-Core.md:421` 要求的"30 例畸形参数"语料未在单点体现** —— **实测最大单点语料 = 8 例**（`test_tools_registry` 的 parametrize），其余分散于 executor/fs/web 契约用例，**总规模未在任一处声明或断言**；③ 缺"**双份存档必须同时存在**"的结构断言（现有为间接）。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv06_args_mirror_raw_args`（**统一语料**：在单点汇集 ≥30 例畸形参数，断言 `TLB-803` + Provider 零调用 + `tool.call` 未写；并把既有分散用例标注为**子性质**引用） |
| **Risk** | 低—中。性质已充分验证；风险是**语料规模口径**：`PRD` 声明 30 例、实测单点 8 例，**若以"30 例"为验收判据则当前无可对照之物**（属口径缺口，非行为缺陷）。 |
| **Status** | `partial`（**接近 covered**） |

### INV-07

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-07` |
| **Definition** | 全库**进程数 = 1**，无 `multiprocessing`/跨进程 RPC；唯一多进程出口 = **受 guard 的** `subprocess`/PTY；子 Agent/jobs/schedule 为**协程级并发**，共享主会话日志与 seq 分配器；同一 `{sid}.jsonl` **单写者独占**。 |
| **Existing Tests** | `test_persistence.py::test_cross_process_lock_blocks_second_writer` · `::test_replay_tolerates_crlf_leftover` |
| **Test Type** | `unit` |
| **Current Evidence** | 单写者由 `test_cross_process_lock_blocks_second_writer` 断言（父进程持锁 → 子进程开同一会话 → `PERS-202`）。**实测 `pyharness/**` 无 `multiprocessing` / `ProcessPool` / `concurrent.futures.Process` 命中**（`grep` 结果为空）。 |
| **Missing Property** | ① **无"全库无 `multiprocessing` import"的静态扫描用例** —— 当前事实为**真空**，但**无测试守护**（未来新增即静默违约）；② 无编号化专项在 `tests/invariants/`。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv07_no_multiprocessing_import`（**静态扫描** `pyharness/**` 的 import 图：禁 `multiprocessing`/`ProcessPoolExecutor`；`subprocess`/PTY 允许但须受 guard —— 可加"subprocess 调用点须在受 guard 的模块内"断言） |
| **Risk** | 中。当前**事实合规但无守护**：单写者锁覆盖了"并发写"面，但"**引入多进程**"这一更根本的违约路径**无任何测试拦截**。 |
| **Status** | `partial` |

### INV-08

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-08` |
| **Definition** | **阶段 N 不得 import 阶段 N+1**（核心脊柱严格无环）；**`governance/` 只依赖 `events` + `errors`**（ADR-018:308）；外围能力**只能经 `ctx` 注入**，禁反向 import Consumer、禁外围互引。 |
| **Existing Tests** | `test_agent.py::test_attach_shadowing_spine_member_tlb802`（cap 不可覆盖脊柱）· `test_cli.py::test_chat_pipe_full_path` · `test_commands.py::test_register_command_and_dispatch` · **`test_governance_policy.py::test_t3_governance_import_direction`**（`ast.parse` + `ImportFrom`/`Import` 遍历，断言 `governance/**` 零 `pyharness.core` 等） |
| **Test Type** | `unit` |
| **Current Evidence** | 治理层方向有**AST 静态断言**（`test_t3`，`:107-117`）；脊柱遮蔽面有 TLB-802 行为断言。⇒ **治理层子命题证据强**。 |
| **Missing Property** | ① **无"阶段 N 不 import N+1"的全库/全脊柱静态扫描** —— 现有 AST 断言**仅作用于 `governance/**`**（S2-1 的 T3），`docs/MAP.md:190` 的拓扑序（bus→persistence→session→{scope,llm,tools,system-prompt}→agent→agent-loop→外壳）**无对应测试**；② 无编号化专项在 `tests/invariants/`。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv08_module_import_direction`（**静态扫描** `pyharness/**` 的 import 图，按 `MAP.md:190` 的拓扑层号断言**无回边** CAF；`*_check` 层号表可显式声明在测试内） |
| **Risk** | 中。**治理层已守护，脊柱主干未守护** —— 而 `INV-08` 的 canonical 表述以"阶段 N 不得 import N+1"为主句，当前**主句无证据**（只有子命题有）。 |
| **Status** | `partial` |

### INV-09

| 字段 | 内容 |
|---|---|
| **INV ID** | `INV-09` |
| **Definition** | 事件 / 错误 / 日志 / `spill` / PTY / UI 投影**全出口**均无 **32+ 位疑似密钥原文**；secret **只存引用**（`env:NAME` / `file:PATH`），明文永不落日志。 |
| **Existing Tests** | `test_config.py::test_redact_masks_secrets` · `::test_web_nonloopback_requires_token` · `test_acp.py::test_engine_error_detail_redacted_and_truncated` · `test_spill.py::test_redact_applied_before_disk` · `test_tool_fs.py::test_read_redact_applied` · `test_tool_web.py::test_fetch_redact_applied_direct_and_spill` · `test_desktop.py::test_render_tool_call_redacts_sensitive_args` · `test_schedule.py::test_register_writes_registered_event_and_job` |
| **Test Type** | `unit` |
| **Current Evidence** | **逐出口定向断言**：config（值脱敏）· acp（错误 detail 脱敏+截断）· spill（落盘前）· tool_fs（读取）· tool_web（直返+spill 双侧）· desktop（UI 投影）· schedule（meta 不入事件）。⇒ **覆盖面广，出口几乎齐全**。 |
| **Missing Property** | ① **`PRD-Core.md:810` 要求的"INV-09 全库 grep"之全库正则扫描用例不存在** —— 现仅为**逐出口**断言，"**未知出口**"无守护；② 无编号化专项在 `tests/invariants/`。 |
| **Proposed Test** | `tests/invariants/test_inv_core.py::test_inv09_no_secret_shape_in_repo_outputs`（与既有 `test_events.py` 的注释扫描守卫同款机制：对**产物面**——事件 payload / 错误消息 / spill 文件 / 桌面投影——做 **32+ 位疑似密钥正则全扫**） |
| **Risk** | 低—中。出口覆盖广；风险在**"未知出口"**（新增输出面时无机制拦截）与断言**逐点重复**（8 处各自为政，改规则需改 8 处）。 |
| **Status** | `partial`（**接近 covered**） |

---

## 3. INV-01 两个已记录 gap 的专项核验（**你的指定项**）

| # | Registry 记录 | 实测结论 | 证据 |
|---|---|---|---|
| **1** | `append` 后文件 hash 不变 | ✅ **缺口确认** | `grep -rn "hashlib\|sha256\|md5(" tests/` 的**唯一**命中是 `test_skill_registry.py`（技能包校验，与会话日志无关）。**`tests/` 内不存在针对会话 JSONL 的任何 hash / 字节不变断言。** `CONSTRAINTS-06-Testing.md:62` 的断言原文为"append 后文件 hash 不变"⇒ **有要求、无实现**。 |
| **2** | 除 `session.py` 外无 `append` 到消息列表路径 | ✅ **缺口确认** | **全库无该静态扫描用例。** 现存**唯一**同类先例是 `test_inv_governance.py::test_inv_r5_no_second_persistence_source`（`:118`），但它只扫 `receipt.py` 单文件、断言 `open(`/`Path(`/`sqlite`/`json.dump(`/`os.replace` 缺席 + import 白名单，**适用范围为治理层**。⇒ 机制已有先例，**对象未扩展到"全库写口"**。 |

**补充实测（Registry 未记录）**：`INV-01` 的分句①/②的现有断言均为**行为级/属性级**，而"**物理不可变性**"（字节/hash）与"**全库唯一写口**"是**结构性**命题 —— 二者缺口性质相同：**有行为证据，缺结构证据**。

---

## 4. INV-02 ~ INV-09 gap 逐项核验（**你的指定项**）

| INV | Registry 记录的 gap | 实测结论 | 差异/补充 |
|---|---|---|---|
| **INV-02** | ①仅 1 条用例；②无静态扫描；③无 invariants 用例 | ✅ 三项全部确认，且**严重于记录** | ⚠️ **新增实测事实**：全库**确实存在第二个 `llm.chat` 调用方**（`auto_title.py:36`）⇒ 静态扫描**当前会 RED**。详见 **§7**。 |
| **INV-03** | ①无 invariants 用例；②误标虚增 | ✅ 确认 | 补充：**四类性质（全量/增量一致、二次重建无重复、编辑重放、缓存失效）全部有直接证据** ⇒ 实测**优于** Registry 的 `partial` 印象，应记 **`partial`（接近 covered）**。 |
| **INV-04** | ①无 invariants 用例；②6 条误标承载；③无全库执行入口扫描 | ✅ 三项确认 | 补充：两个面（求值事实 / 结构单调）**各自都有直接证据**，无行为层缺口。 |
| **INV-05** | ①无 invariants 用例；②无单一锚点 | ✅ 确认 | 补充实测：**审批 `denied`/`timeout` 的零副作用已被覆盖**（`test_tools_executor.py:431` 与 `:1017` 两处 `@parametrize("verdict",["denied","timeout"])`）⇒ Registry 未列此项，实际**优于**预期。 |
| **INV-06** | ①无 invariants 用例；②30 例语料未单点体现 | ✅ 确认 | 补充实测：**最大单点语料 = 8 例**（`test_malformed_args_tlb803_zero_exec` 的 parametrize）；其余分散 ⇒ "30 例"**当前无对照物**。 |
| **INV-07** | ①无 multiprocessing 静态扫描；②无 invariants 用例 | ✅ 确认 | 补充实测：`grep multiprocessing\|ProcessPool pyharness/` **为空** ⇒ **当前合规但无守护**。 |
| **INV-08** | ①无独立的阶段方向静态扫描；②无 invariants 用例 | ✅ 确认 | 补充实测：治理层方向有 AST 断言（`test_t3_governance_import_direction`），**但脊柱主干（阶段拓扑）无守护** ⇒ 主句无证据、子命题有证据。 |
| **INV-09** | ①无全库正则扫描；②无 invariants 用例 | ✅ 确认 | 补充实测：**8 个出口均有点对点断言** ⇒ 出口覆盖广，"未知出口"是唯一空缺。 |

---

## 5. 三类特别清单（**你的指定项**）

### 5.1 现有测试中**只属于某个 INV 子性质**的（不得据以新设编号）

| 现有用例 | 实为 | 说明 |
|---|---|---|
| `test_session.py::test_method_surface_append_only_inv01` | `INV-01` **分句①**（只追加） | 方法面无改写 API |
| `test_session.py::test_append_only_no_second_history_store_inv02` | `INV-01` **分句②**（无第二份历史） | 实例属性集合断言 |
| `test_session.py::test_derive_only_changes_via_append_inv02` | `INV-01` **分句②** | 历史只随日志事件变化 |
| `test_session.py::test_finished_flush_failure_keeps_closed_but_log_empty` | `INV-01` **分句①**（终态拒写落点） | |
| `test_goal.py::test_rebuild_from_events_matches_live_state` | `INV-01` **分句②** | 内存态 ≡ 日志派生态 |
| `test_plan_mode.py::test_real_session_full_lifecycle_events_valid` | `INV-01` **分句②** | |
| `test_compaction.py::test_compact_appends_declaration_only_append_only` | `INV-01` **分句①** | 原文零改动、只尾追一条 |
| `test_agent_loop.py::test_single_turn_text_complete` / `::test_tool_round_then_text_counts` | `INV-01` **分句②** | 上下文由日志现派生 |
| `test_agent_loop.py::test_llm_chat_only_entry_point_inv02` 的①②断言 | `INV-01` **分句②** | **该用例名下的断言一半属 INV-01**（仅③属 INV-02） |
| `test_tools_guard.py::test_reject_deterministic_not_flippable` | `INV-04`(b) + `INV-05` | 同用例跨两条 ID（**合法**，非错标） |

### 5.2 被**错误使用旧 INV 编号**的测试（Registry §2 已登记，此处为测试面清单）

| 现有测试/位置 | 现标编号 | 应属 | Legacy |
|---|---|---|---|
| `test_tools_guard.py:6,197,245,247,269,305`（5 用例 + 模块 docstring + 小节标题） | `INV-03` | **`INV-04`** | L-3 |
| `test_llm_fallback.py:4,182`（+ `::test_302_direct_degrade_happens`） | `INV-07` | **非 INV** → `F013/F028` | L-5 |
| `test_agent_loop.py:12,259`（+ `::test_max_turns_truncation_inv08`） | `INV-08` | **非 INV** → `F007` | L-6 |
| `test_session.py:5,174,435`（+ 模块 docstring） | `INV-02` | **`INV-01`** | L-1 |
| `CODE-MATRIX.md:35`（非测试） | `INV-03` / `INV-04` | `INV-04` / `INV-05` | L-4 |
| `scripts/demo_phase1.py:5,61,71`（非测试） | `INV-02` / `INV-03` | `INV-01` / `INV-04` | L-1 / L-3 |

**合计 13 处测试面 + 4 处非测试面**（与 Registry §2.1 的"22 处 / 9 文件"一致）。**本阶段按纪律 1/2 一律以 Registry 归属，不据旧注释解释编号。**

### 5.3 已有**充分证据**的性质（S6-2b 只需"重述 + 编号化"，无需新增语义）

| INV | 已充分的性质 | 代表用例 |
|---|---|---|
| `INV-03` | 全量↔增量一致 · 二次 rebuild 无重复 · 编辑重放 · 缓存失效 | `test_rebuild_full_matches_incremental` · `test_rebuild_then_incremental_no_dup` · `test_edited_updates_target_row` · `test_rebuild_derived_cache_invalidation_on_append` |
| `INV-05` | 各拒绝路径零副作用（guard/scope/critical/审批 denied+timeout）· 拒绝强同步留痕 | `test_reject_event_pair_order_and_sync` · `test_executor_critical_delete_denied_zero_side_effect` · `test_tools_executor.py:431,1017` |
| `INV-06` | 双份存档 · 执行 args==日志 args · 畸形参数零调用（8 例） | `test_assistant_passthrough_raw_json` · `test_malformed_args_tlb803_zero_exec` · `test_happy_full_pipeline_event_order` |
| `INV-09` | 8 个出口的定向脱敏断言 | `test_redact_masks_secrets` · `test_engine_error_detail_redacted_and_truncated` · `test_fetch_redact_applied_direct_and_spill` 等 |
| `INV-01` | 分句①（方法面无改写）· 分句②（无第二份历史） | `test_method_surface_append_only_inv01` · `test_append_only_no_second_history_store_inv02` |
| `INV-04` | 面 (a)（恰一条 evaluated）· 面 (b)（无 bypass API + 不可翻回） | `test_evaluate_allow_single_evaluated_event` · `test_forbidden_bypass_api_attribute_error` · `test_register_after_reject_cannot_rescue` |

---

## 6. 真正 Coverage Gap 汇总（S6-2b 的对象）

| # | INV | 缺口 | 类型 | 优先级 |
|---|---|---|---|---|
| **1** | INV-02 | **全库 `llm.chat` 唯一调用方**——无静态证据，且**当前实测存在第二个调用方** | 静态结构 | **P0（须先裁定，见 §7）** |
| **2** | INV-08 | **阶段 N 不 import N+1**（脊柱拓扑）——无全库静态扫描（治理层子命题除外） | 静态结构 | P1 |
| **3** | INV-01 | **`append` 后文件 hash 不变**（物理不可变性） | 结构/物理 | P1 |
| **4** | INV-01 | **除 `session.py` 外无第二写口**（全库静态） | 静态结构 | P1 |
| **5** | INV-07 | **全库无 `multiprocessing`**（当前合规但无守护） | 静态结构 | P1 |
| **6** | INV-04 | **全库凡执行必经 `tools.execute`**（执行入口唯一性） | 静态结构 | P2 |
| **7** | INV-09 | **未知出口**的密钥形态全扫（现仅逐出口） | 正则全扫 | P2 |
| **8** | INV-06 | **"30 例畸形参数"语料口径**（单点最大 8 例） | 语料规模/口径 | P2 |
| **9** | INV-05 | 拒绝路径**全覆盖清单**（现分散 4 文件、无单一锚） | 覆盖可发现性 | P3 |
| **10** | INV-03 / INV-04 | **旧编号误标**致覆盖读数失真（INV-03 虚高、INV-04 隐形） | 编号可读性 | P3（属 FC-8/FC-13） |
| **11** | 全部 9 条 | **`tests/invariants/` 内零编号化用例** | 编号化 | P1（S6-2b 主交付） |

> **注**：缺口 #10 属 **S6-1 的 `pending authorization` 迁移清单（FC-8 / FC-13）**，与 S6-2b 有**重叠面**。建议在 S6-2b 前单独裁定其归属，避免把错误编号**复制进** `tests/invariants/`。

---

## 7. ⚠️ 新发现：`INV-02` 存在第二个 `llm.chat` 调用方（`auto_title`）—— **需裁定**

> 依 S6-2 纪律 14：**记录为 KEY-FINDINGS，不擅自修改生产代码**。本节给出事实、影响与选项，**由你裁定**。
>
> ⚠️ **本节已被 §10（S6-2a-P0 · INV-02 Scope Adjudication）取代并大幅扩展。** §10 补齐了本节缺失的关键事实（`LLMClient` 四出口结构 · PRD 的 F042 伪码指定 `ctx.llm.mini` · `summarize` 的 `budget` 参数未实现）并给出**推荐裁决 = Option A**。**以 §10 为准**；本节保留为首次发现的原始记载。

### 7.1 事实（实测，可复核）

| 项 | 内容 |
|---|---|
| **站点** | `pyharness/core/auto_title.py:36` —— `resp = await ctx.llm.chat([{"role":"user","content":prompt}], tools=None, ctx=ctx)` |
| **生产接线** | ✅ **已接线**：`pyharness/engine.py:805-806` 在 run 正常完成后调用 `auto_title(ag_ctx)`（`spine._auto_titled` 幂等标记于 `:803`） |
| **测试覆盖** | **零** —— `grep -rln "auto_title" tests/` **无命中** |
| **调用形态** | agent-loop 自身用 **`getattr(ctx.llm, chat_fn)`**（`agent_loop.py:240-241`，`chat_fn ∈ {chat, chat_stream}`）⇒ **AST 扫描看不到 agent-loop，却能看到 auto_title** |
| **对照**：`plan_mode` | 用 `ctx.llm.json_chat`（`plan_mode.py:341-352`，经 `getattr`），**spec 明示其合规**（`docs/specs/plan_mode.py.md:17`）⇒ **不是新问题** |
| **对照**：第二端点路径 | **无** —— `grep httpx pyharness/` 仅 `skill_registry.py`（技能包下载，与 LLM 无关）⇒ "无直连模型端点"这一**读法**成立 |

### 7.2 与 Registry `INV-02` 的关系

Registry 的 Canonical Definition 为：**"全库 `llm.chat` 的唯一合法调用方 = agent-loop（`run_turn`）"**。
⇒ `auto_title` 是 **`llm.chat` 的第二个调用方**，**结构上与该表述冲突**。（`ctx.llm.json_chat` 属另一方法，不在该句字面范围；`auto_title` 用的正是被点名的 `chat`。）

### 7.3 影响评估（精确化，避免夸大）

| 面 | 影响 |
|---|---|
| **审计可追溯** | ✅ **不受损** —— `llm.chat` 内部落 `llm.request`（`llm.py:661`）与 `llm.usage`（经 `llm.py:680` → `report_usage`，`:538`）⇒ 该次调用**在日志中留痕**，非审计盲区。 |
| **单一端点路径** | ✅ **不受损** —— 仍经 `LLMClient.chat`（`llm.py:647`「非流式唯一出口」）。 |
| **循环三闸** | ⚠️ **部分失效** —— 该调用发生在 **run 完成之后**（engine 钩子），**不在 `agent_loop.run_turn` 内** ⇒ **不经过轮数闸与取消闸**。 |
| **预算闸（F032）** | ⚠️ **调用前不咨询** —— 实测 `LLMClient.chat` **只计量、不拦截**（`llm.py:542`/`:561` 落 `llm.usage` + 计数器；`llm.py:191` 注明"`scope.budget_state`/`BudgetGuard` **每轮现读**本方法"）⇒ **预算硬闸由循环/scope 侧执行**，故 `auto_title` 的调用**不经过闸前检查**，超支只能在**下一轮**被发现。 |
| **违规模式吻合度** | ⚠️ 正是 `SECURITY.md:208` 描述的违约模式："**workflow/工具直调 llm 做"小任务"** → 无闸模型调用"。 |

### 7.4 对 S6-2b 的直接后果（**关键**）

拟议的 `INV-02` 静态用例（§2 / §6-#1）**在当前代码上会 RED**。⇒ **不能按普通 gap 直接落测试**。可选路径：

| 选项 | 内容 | 代价 |
|---|---|---|
| **(a) 先裁定 + 登记，再落测试**（推荐） | 本阶段先按纪律 14 登记 KEY-FINDINGS；由你裁定 `auto_title` 的定性（**违约** / **经批准的第二入口**）后，S6-2b 再写测试并对齐 | 需一次人工裁定；若判为"经批准"，须**新 ADR** 记录该例外（ADR 只增不改） |
| **(b) 测试采用"宽读法"** | 静态用例只断言"**除 `llm.py` 外无 LLM 端点客户端**"（当前 **GREEN**），另设**独立**用例登记 `auto_title` 为**已知例外**（`xfail`/标记） | 规避裁定，但 **`xfail` 可能被视为"以 skip 掩盖问题"**（S6-2 纪律 13 / S6-6 禁止 `xfail` 当作解决）⇒ **不推荐** |
| **(c) 判为"范围外"** | 认定 `INV-02` 的 "唯一调用方" 表述**仅指循环内的对话路径**，`auto_title` 属"会话维护"另一类 | 需**修订 Registry 的 Canonical Definition** —— 本轮（S6-2a）**明确禁止修改 Registry**；且触及 P-4（不得重解释语义）⇒ **须走正式裁定** |

**建议**：走 **(a)**。本阶段**只报告、不落测试、不修代码**。

### 7.5 拟提交的 KEY-FINDINGS 条目（**待授权后填写**）

> **`INV-02` 存在第二个 `llm.chat` 调用方（`auto_title`）**
> **现象**：`pyharness/core/auto_title.py:36` 直接调用 `ctx.llm.chat(...)`，经 `engine.py:805-806` 在生产路径接线；无测试覆盖。
> **影响**：绕过 `agent_loop.run_turn` 的**轮数/取消闸**；`LLMClient.chat` 只计量不拦截 ⇒ **调用前不咨询预算闸（F032）**。审计留痕与单端点路径**不受影响**。
> **定性**：与 `INV-02` Canonical Definition（"唯一调用方 = agent-loop"）**结构冲突**；定性待裁定。
> **处置**：**不修**（S6-2 纪律 14）；待裁定后决定"补测试 + 新 ADR 记录例外"或"改造 `auto_title` 走循环"。
> **状态**：`OPEN`（本条目**尚未写入** `docs/KEY-FINDINGS.md` —— 该动作不在 S6-2a 允许清单内，**等待授权**）

---

## 8. 最小测试设计（S6-2b 建议，**本阶段不含代码**）

> 全部设计**只新增测试**、**不改生产代码**、**不改现有测试**；静态扫描类用例沿用仓内既有先例 `test_inv_r5_no_second_persistence_source`（AST + 字符串扫描）。

| # | 目标文件（建议） | 用例（编号化） | 手段 | 预期 |
|---|---|---|---|---|
| 1 | `tests/invariants/test_inv_core.py` | `test_inv01_no_mutation_of_existing_bytes` | 字节前缀 + hash 比对 | GREEN |
| 2 | 同上 | `test_inv01_sole_log_write_path` | 全库静态扫描 | GREEN（**需先确认扫描谓词不误报**） |
| 3 | 同上 | `test_inv02_llm_chat_sole_caller` | AST 调用点 + 允许集 | **⚠️ 见 §7（当前 RED）** |
| 4 | 同上 | `test_inv03_rebuild_idempotent_matches_incremental` | 重述既有断言 | GREEN |
| 5 | 同上 | `test_inv04_no_bypass_surface_and_single_evaluation` | 重述既有断言 | GREEN |
| 6 | 同上 | `test_inv05_all_reject_paths_zero_side_effect` | 参数化矩阵 | GREEN |
| 7 | 同上 | `test_inv06_args_mirror_raw_args_corpus` | ≥30 例统一语料 | GREEN |
| 8 | 同上 | `test_inv07_no_multiprocessing` | 静态 import 扫描 | GREEN |
| 9 | 同上 | `test_inv08_import_direction_topological` | 全库 import 图 + 层号表 | **需先验证当前拓扑无回边**（若不成立即为新发现） |
| 10 | 同上 | `test_inv09_secret_shape_full_scan` | 产物面正则全扫 | GREEN |
| 11 | `tests/invariants/test_inv_governance.py` | `test_inv_g3_receipt_verify_stable_after_replay` | `receipt.verify()` 重放后不变 | GREEN（补 F-2 缺口） |

**设计原则**：① 每条用例的**失败信息须能指出所违反的 Canonical ID**；② **不删除** `tests/unit/*` 的原始用例（迁移=**重述+编号化**）；③ 静态扫描用例须**内置白名单并写明白名单理由**（沿用 S2-5.1 的 T-4 注释扫描守卫体例）。

---

## 9. 边界声明

**做了**：只读勘察；逐项实测 9 条 ID 的覆盖与缺口；核验 INV-01 两个已记录 gap；核验 INV-02~09 各自 gap；建立三类特别清单；产出最小测试设计；发现并记录 `INV-02` 的第二个 `llm.chat` 调用方。

**没做**（S6-2a 明令禁止）：❌ 修改任何 Python 生产代码 · ❌ 修改任何现有测试 · ❌ **新增测试** · ❌ 修改 Registry · ❌ 修改 Event Schema · ❌ 修复 L-3 · ❌ 写入 `docs/KEY-FINDINGS.md`（§7.5 条目**待授权**）· ❌ 进入 S6-2b · ❌ commit · ❌ push。

**产出**：本文件（`S6-2_COVERAGE_MATRIX.md`）唯一。中间实测数据落在 `tmp/`（`.gitignore` 已覆盖，不入库）。

**worktree**：仅 `?? S6-2_COVERAGE_MATRIX.md`（未跟踪）；**HEAD 仍 `4f05c95`**，其余 clean。

---

**本阶段结束。等待 review；未 commit、未 push。**

---
---

# §10 INV-02 P0 Scope Adjudication（S6-2a-P0）

> **问题**：`auto_title` 的 LLM 调用是否**真正违反** Canonical `INV-02`，还是一个**合法的系统级例外 / 不同语义的 LLM 调用路径**？
> **方法**：不看注释、不据编号猜测。全部结论给出 **文件:行号 + 实现证据 + 文档证据 + 行为证据**。
> **边界**：本节**只分析**。未改任何生产代码 / 测试 / Registry；**未创建 INV-02 测试**；**未写入 `KEY-FINDINGS.md`**；未修复 `auto_title`；未进入 S6-2b。
> **本节新增的关键事实（前序 §7 未涵盖）**：`LLMClient` 实为**四出口门面**（`chat`/`chat_stream`/`summarize`/`json_chat`），且 **PRD 的 F042 验收伪码指定的是 `ctx.llm.mini(...)`，而 `mini` 在实现中不存在**。

## 10.1 Step 1 · Call Chain（完整调用链）

```
[触发层] task_queue runner
  └─ engine.py:776  run_for_task(task)
       ├─ engine.py:787-796  定位/补写该任务的 user.message（严格归属窗口；窗口空则按 task.intent
       │                     补写一条 user.message(sync=True, actor="system")）— 会写事件日志
       ├─ engine.py:799  ag_ctx = await _agent_ctx_of(spine, env)   ← 复用 agent 的 ctx
       ├─ engine.py:800  res = await loop.wake(env, ctx=ag_ctx)     ← ★ Agent 推理循环在此执行完
       │                        └─ agent_loop.run() → 逐轮：_must_stop(三闸) → scope.check_budget(F032)
       │                           → run_turn → ctx.llm.chat(...)   ← 循环内的合法 LLM 调用
       └─ engine.py:802-809  ★ 循环返回之后：
            if res.reason == "complete" and not spine._auto_titled:
                from pyharness.core.auto_title import auto_title
                if await auto_title(ag_ctx): spine._auto_titled = True
            except Exception: log.debug(...)          ← 静默吞掉一切异常

[被调用层] core/auto_title.py:26  auto_title(ctx)
  ├─ :28  幂等闸：日志已有 session.renamed → return None（不再调用 LLM）
  ├─ :16-21 读取：遍历 ctx.session.events_after(0)，取**首条 user.message** 内容（只读）
  ├─ :36  ★★ resp = await ctx.llm.chat([{"role":"user","content":prompt}], tools=None, ctx=ctx)
  ├─ :38-40 LLM 失败(PyHError) → log.info + return None（不抛、不阻断对话）
  └─ :46  await ctx.session.append("session.renamed", {"new_title":…, "by":"auto"}, actor="system")

[出口层] LLMClient.chat（llm.py:964）→ _chat_any("chat", …)（llm.py:957）
  └─ 有降级链 → chat_with_fallback（llm.py:959）；否则直连适配器
       └─ LLMAdapter.chat（llm.py:647「非流式唯一出口」）
            ├─ :661  ctx.session.append("llm.request")     ← 写事件日志
            ├─ :663  asyncio.wait_for(transport.complete(req), timeout=self.timeout.total_s)  ← 仅超时闸(F017)
            ├─ :680  report_usage(...) → llm.py:558 append("llm.usage") + :561 cnt.task_add(...)  ← 仅计量
            └─ :682  ctx.session.append("llm.response")
```

**逐项回答（Step 1 清单）**

| 问题 | 答案 | 证据 |
|---|---|---|
| **触发条件** | `res.reason == "complete"` **且** 本 spine 尚未自动命名过（`_auto_titled` 幂等标记） | `engine.py:802-803` |
| **调用时机** | **在 `loop.wake()` 返回之后** —— 即 Agent 循环**已到达终态**（complete）之后 | `engine.py:800` → `:802-806` |
| **输入** | `ag_ctx`（循环用过的同一个 ctx）+ 派生自日志的首条 `user.message` 内容（截断 500 字） | `auto_title.py:16-21` |
| **输出** | 标题字符串；并 append `session.renamed` | `auto_title.py:46` |
| **是否属 Agent task execution（任务执行）** | ❌ **否** —— 它是任务完成后的一次**后处理工具** | `engine.py:802` 的 `reason=="complete"` 前置条件 |
| **是否属主 Agent reasoning loop（推理循环）** | ❌ **否** —— 循环已退出；它**不在** `agent_loop.run()` 的 `while True` 内 | `agent_loop.py:196-202` vs `engine.py:806` |
| **是否修改 session state** | ✅ **是**（仅追加，不改写）—— `session.renamed` | `auto_title.py:46` |
| **是否产生 side effect** | ✅ **是** —— 一次真实 LLM 请求 + 一条新事件 | `llm.py:663` / `auto_title.py:46` |
| **是否写入 Event Log** | ✅ **是**，共 **4 类**：`llm.request` / `llm.usage` / `llm.response` / `session.renamed` | `llm.py:661,558,682`；`auto_title.py:46` |
| **是否产生 `llm.request` / `llm.usage`** | ✅ **均产生** | `llm.py:661` / `llm.py:558` |
| **是否经过 scope** | ❌ **否** —— `llm.py` 全文无 `scope` **执行**代码（仅注释提及） | `grep -n "scope" pyharness/core/llm.py` → 命中全为注释/类型注 |
| **是否经过 cancellation** | ❌ **否** —— 仅有 `asyncio.wait_for(timeout)`（F017 超时），**无**循环的取消闸 | `llm.py:663`；`agent_loop.py:197,204`（取消闸在循环内） |
| **是否经过 budget** | ❌ **否** —— 预算硬闸 `ctx.scope.check_budget()` **只由循环调用** | `agent_loop.py:199`；`scope.py:352-360` |
| **是否经过 guard** | ❌ **否**（且**不需要**：无工具调用，`tools=None`） | `auto_title.py:36` |
| **是否经过 governance** | ❌ **否** —— 无 `decision.issued`；`llm.py`/`auto_title.py` 不 import `governance` | AST 扫描（§7）显示唯一 `llm.chat` 调用点；`governance/` 与 LLM 无调用边 |
| **能否被 replay / audit 还原** | ✅ **能** —— 请求/用量/响应/命名**四类事件全在 append-only 日志**；标题本身由 `session.renamed` 派生（合 `INV-01`） | `llm.py:661,558,682`；`auto_title.py:46`；`EVENT-SCHEMA.md:121`（`by=auto`） |

---

## 10.2 Step 2 · `LLMClient` 的实际保护边界（**看实现，不看注释**）

**唯一的物理出口链**：`chat`/`chat_stream`/`summarize`/`json_chat` → `_chat_any`（`llm.py:957`）→ `chat_with_fallback`（`:959`）或直连适配器 → **`LLMAdapter.chat`（`llm.py:647`）**。

| # | 问题 | 实测答案 | 证据 |
|---|---|---|---|
| **1** | `chat()` 是否执行 **budget enforcement** | ❌ **否** | `llm.py:647-684` 全文无预算判定；`report_usage`（`:538-562`）**只** append `llm.usage` + `cnt.task_add`（`：561`）。**预算硬闸在 `agent_loop.py:199` → `scope.py:352`** |
| **2** | 是否执行 **cancellation** | ❌ **否**（仅超时） | `llm.py:663` 为 `asyncio.wait_for(..., timeout=self.timeout.total_s)` = **F017 总时长闸**；循环的取消闸在 `agent_loop.py:197`(`_must_stop`) / `:204`(`run.cancelled`) |
| **3** | 是否执行 **guard** | ❌ **否**（且设计上无关：LLM 调用非工具调用，guard 是工具侧） | `tools_guard` 只在 `tools_executor` 关 2b；`llm.py` 无 import |
| **4** | 是否执行 **governance decision** | ❌ **否** | 无 `decision.issued`；`governance/` 按 ADR-018:308 只依赖 `events`+`errors`，与 `llm` 无调用边 |
| **5** | 是否**只有计量、没有阻断** | ✅ **确认** | 全链只有：`llm.request` 落盘（`:661`）→ 超时（`:663`）→ 归一（`:665`）→ `report_usage` 计量（`:680`）→ `llm.response`（`:682`）。**无任何"拒绝/中止"分支** |
| **6** | `auto_title` 是否**绕过 Agent Loop 的控制边界** | ✅ **确认绕过** | 三闸**全部**位于 `agent_loop.run()`：轮数闸 `:197`、取消闸 `:197/:204`、预算闸 `:199`。`auto_title` 在 `:806` 于循环**返回之后**被调用，**不经过上述任何一行** |
| **7** | 该绕过是否导致**实际安全/资源控制缺失** | ⚠️ **部分缺失**（详见 §10.5 / §10.7） | **缺失**：轮数闸、取消闸、预算前置闸。**未缺失**：审计留痕、单端点路径、超时闸、guard（无工具调用） |

**⭐ 决定性补充事实（Step 2 之外、但改变裁决形状）**：
`summarize(prompt, *, budget: int = 400, ctx)`（`llm.py:974-982`）—— **`budget` 参数只出现在签名，函数体内从未使用**（`sed -n '974,983p' | grep budget` 仅命中第 1 行）。⇒ **即便是被指定的"系统工具出口"，当前实现也不施加任何资源上界**。

---

## 10.3 Step 3 · 规格与架构证据（INV-02 的原始意图）

### 10.3.1 「唯一调用方」的规范性表述（4 处一致）

| 出处 | 原文 | 语义 |
|---|---|---|
| `docs/PRD-Core.md:838`（F018，**上位权威**） | "INV-02 **无绕过 agent-loop 直调 llm**" | 主语 = *绕过 agent-loop* |
| `docs/CONSTRAINTS-06-Testing.md:63`（S6 权威表） | 断言 = "**全库 `llm.chat` 唯一合法调用方 = agent-loop**" | **全库** + **具体方法 `llm.chat`** |
| `docs/DIS-CORE.md:186` | "INV-02：本模块是 **llm.chat 唯一合法调用方**；绕过即架构违规" | 具体方法 |
| `docs/specs/agent_loop.py.md:262` | "本模块是全框架 `llm.chat` **唯一合法调用方**；任何绕过路径 = 架构违规（**测试钉死**）" | 具体方法 + 要求测试 |

### 10.3.2 意图（为什么存在）

`docs/PRD-Core.md:634`：*"三态+三闸是'死循环烧钱'的结构性解药；上下文每次现派生…**本函数是全框架正确性枢纽，禁止任何模块绕过它直调 llm.chat(INV-02)**。"*
⇒ **意图 = 保证每一次模型调用都经过循环的三闸（轮数/预算/取消）与日志派生上下文。**

### 10.3.3 ⭐ 架构的"系统工具出口"设计（**INV-02 未覆盖的面**）

`LLMClient`（`llm.py`）暴露 **4 个出口**，**全部**汇聚到 `_chat_any` → 同一物理端点：

| 出口 | 行 | 定位 | 实际调用者 |
|---|---|---|---|
| `chat(messages, tools, ctx)` | `964` | **会话对话出口**（INV-02 点名者） | `agent_loop.run_turn`（`agent_loop.py:241`，经 `getattr`）+ **`auto_title.py:36`** |
| `chat_stream(...)` | `969` | 流式对话出口 | `agent_loop`（`streaming=True` 时） |
| `summarize(prompt, budget=400, ctx)` | `974` | **压缩摘要·系统工具出口** | `compaction._call_summarize`（`compaction.py:510-516`） |
| `json_chat(prompt, **kw)` | `984` | **JSON 强约束·系统工具出口** | `plan_mode._json_chat`（`plan_mode.py:341-352`） |

⇒ **架构本身就有"非循环的系统工具 LLM 出口"这一类**（`summarize` / `json_chat`），且 **spec 明文认可**（`docs/specs/plan_mode.py.md:17`："消费 `ctx.llm.json_chat`（**唯一 LLM 出口**，INV-02）"）。
⇒ 因它们用的是**别的方法名**，`INV-02` 的字面（"`llm.chat` 唯一调用方"）**未被它们触碰**。

### 10.3.4 ⭐⭐ F042 的规格：**PRD 明文授权直调，但指定 `mini`**

`docs/PRD-Core.md:1248-1252`（F042 验收伪码，逐字）：

```python
async def auto_title(ctx):
    first = ctx.session.first_user_message()
    if ctx.session.has_title: return
    title = (await ctx.llm.mini(f"用≤24字概括: {first[:200]}")).strip() or first[:20]
    await session.append("session.renamed", new_title=title[:24], by="auto")
```

**三条推论（这是本裁决的核心证据）：**

1. **功能层授权**：PRD **自己**把"一次直调 LLM"写进 F042 的验收伪码 ⇒ **`auto_title` 作为系统工具直调 LLM，是 PRD 授权的形态**（→ 支持结论 **B** 的**意图**侧）。
2. **方法层违规**：PRD 指定 **`ctx.llm.mini(...)`** —— 一个**小调用工具出口**；而实现用了 **`ctx.llm.chat(...)`** —— **正是 `INV-02` 点名保留给循环的方法**。
3. **`mini` 在实现中不存在**：`grep -rn "def mini" pyharness/` **零命中** ⇒ 实现者缺少 PRD 指定的出口，转而使用了 `chat`。

`docs/PRD-Core.md:1244`：*"失败**降级"前 20 字符"规则**，不阻塞"* —— 实现（`auto_title.py:38-40`）在 LLM 失败时返回 `None` 并**未落实该降级规则**（标题留空而非取前 20 字符）；另 `:1878` 要求 `test_f042_title.py`（**不存在**）、`docs/specs/auto_title.py.md`（**不存在**）。⇒ **F042 是一个规格-实现-测试三缺的口子**（登记，不属 INV-02 裁决本体）。

---

## 10.4 Step 4 · Evidence Chain

```text
Evidence
  │  ① llm.py 全文无 scope/budget/guard/governance **执行**代码（仅注释）
  │     证据：grep -n "scope|budget|guard|governance|cancelled" pyharness/core/llm.py
  │           → 命中行 144/191/736/974 全为 docstring 或签名默认值
  │  ② 三闸全在循环内：agent_loop.py:197(_must_stop) / :199(scope.check_budget) / :204(run.cancelled)
  │  ③ 预算闸实体：scope.py:352 check_budget → :360 raise BudgetExhausted
  │  ④ auto_title 在 engine.py:806 于 loop.wake(:800) 返回**之后**执行
  │  ⑤ PRD-Core.md:1248-1252 F042 伪码 = ctx.llm.mini(...)（直调，但用 mini）
  │  ⑥ grep "def mini" pyharness/ → 零命中（mini 不存在）
  │  ⑦ PRD-Core.md:838 / CONSTRAINTS-06:63 / DIS-CORE:186 / specs/agent_loop.py.md:262
  │     四处一致：llm.chat 唯一合法调用方 = agent-loop；绕过 = 架构违规
  │  ⑧ llm.py:974-982 summarize 的 budget 参数**从未被使用**
  ▼
Architecture Intent
  │  · 意图（PRD:634）：每一次模型调用都必须经过循环三闸（轮数/预算/取消）+ 日志派生上下文
  │  · 架构现状：LLMClient 有 **对话出口**（chat/chat_stream）与 **系统工具出口**
  │    （summarize/json_chat）两类；二者汇聚到同一物理端点，但**只有循环调用受三闸约束**
  │  · F042 的意图：auto_title 属**系统工具**，PRD 为其指定的出口是 **mini**
  ▼
Current Implementation
  │  · auto_title.py:36 调用的却是 **chat** —— 对话出口（INV-02 点名者）
  │  · mini 不存在 ⇒ 实现者无指定出口可用，落到 chat
  │  · 调用点位于循环之外（engine.py:806）⇒ 三闸全不过
  │  · 审计面完好：llm.request / llm.usage / llm.response / session.renamed 四类事件齐全
  ▼
Observed Behavior
  │  · 运行期：每次 run 正常完成后，若会话尚无标题，则发生一次**不受轮数/取消/预算前置闸约束**的模型调用
  │  · 资源：调用**被计量**（llm.usage + counters）但**不被拦截**；超支只能在**下一轮**由 scope.check_budget 发现
  │  · 取消：run 已 complete，取消语义本已结束；但该调用**不受**任何取消令牌约束
  │  · 审计：可从日志完整还原（含 llm.request/usage/response）
  ▼
Invariant Interpretation
  │  · INV-02 的**字面**（4 处规范一致）：**"全库 llm.chat 唯一合法调用方 = agent-loop"**
  │      ⇒ auto_title 调用 llm.chat 且不是 agent-loop ⇒ **字面被违反**
  │  · INV-02 的**意图**（PRD:634）：保证每次模型调用过三闸 ⇒ auto_title 不过三闸 ⇒ **意图被违反**
  │  · INV-02 的**未覆盖面**：架构存在合法的"系统工具 LLM 出口"（summarize/json_chat），
  │     INV-02 的字面**不适用于它们**（方法名不同）；F042 本应走 mini（同类系统工具出口）
  ▼
Conclusion
   · **违反成立，但违反点在"方法选择 + 无闸"，不在"功能存在"。**
   · auto_title 作为**系统工具直调 LLM** 是 PRD 授权形态（→ 不判 B）；
   · 但其**实现调用了被 INV-02 保留的 `chat`**，且该调用**不经过任何运行时闸**（→ 判 A）。
   · 使之合规的最小方向 = 走**系统工具出口**（PRD 指定的 `mini`，或既有 `summarize` 式包装），
     而非"扩大 INV-02 例外"。
```

---

## 10.5 Step 5 · A / B / C 三种结论

### 结论 A — **确认违反 INV-02**（**推荐**）

**成立理由**：
1. **字面**：`INV-02` 的四处规范表述（`PRD:838`/`CONSTRAINTS-06:63`/`DIS-CORE:186`/`specs/agent_loop.py.md:262`）**均以 `llm.chat` 为对象**，且**均表述为"全库/全框架唯一调用方 = agent-loop"**。`auto_title.py:36` 调用 `llm.chat`，且**不是** agent-loop。
2. **意图**：`PRD:634` 的立论是"三闸只在循环内"。实测 `auto_title` 的调用**不经过任何一闸**（`agent_loop.py:197-199` 三行全不经过）。
3. **合规路径本已存在且被 PRD 指定**：`ctx.llm.mini`（`PRD:1251`）。实现**未走**该路径。

**定性**：**genuine invariant violation（真实不变量违约）** —— 但**限于"调用 `chat` 这一方法选择"**；`auto_title` 作为**系统工具直调 LLM 的功能形态**本身**有 PRD 依据**。

**要求（按你的 Option A 条款）**：
- 标记为 **KEY-FINDING / P0**（条目草案见 §10.8；**本阶段不写入** `docs/KEY-FINDINGS.md`）。
- **不修代码**（S6-2 纪律 14）。
- **设计最小修复边界**（§10.9），**暂不进入实现**。

### 结论 B — 确认合法例外（**不成立，理由如下**）

要成立 B，必须同时满足：
1. **规范明文承认该例外** —— ⚠️ **部分满足**：`PRD:1248-1252` 记了直调，但**指定的是 `mini` 而非 `chat`**，且**没有**任何文档说"`chat` 可被 `auto_title` 调用"；`CONSTRAINTS-06:63` 的"全库唯一"**无但书**。
2. **实现走在被承认的出口上** —— ❌ **不满足**：实现用的是 `chat`，不是 `mini`。
3. **它能享受"被承认出口"的控制** —— ❌ **不满足**：实测**任何** LLM 出口（含 `summarize`）都**不受三闸约束**，且 `summarize` 的 `budget` 参数**未被实现**（`llm.py:974-982`）⇒ **不存在可援引的"该出口享有的控制"**。

⇒ 判 B 必须**或**修改实现（走 `mini`）、**或**修改 INV-02 表述。二者**都超出本阶段授权**，且后者**正是你明令禁止的"临时扩大 INV-02 例外范围"**。⇒ **不可判 B**。

### 结论 C — 当前规范不足以判断（**不成立，理由如下**）

判 C 需要"事实缺失"。实测**关键事实齐备**：调用链（§10.1）、保护边界（§10.2）、意图（§10.3）、行为（§10.1/§10.4）**均已由实现与文档证据确定**，无空缺。
⇒ **不存在"需要更多证据才能裁决"的情形**；分歧只在**裁决口径**（字面 vs 意图），而二者**在本例中指向同一结论**（都要求过闸）。
⇒ **不可判 C**。

---

## 10.6 Step 6 · 特别检查：是否存在「控制绕过」（**独立问题**）

> 本问题**独立于** INV-02：**"不违反 INV-02" ≠ "没有工程风险"**。

### 10.6.1 `auto_title` 的控制面（逐项）

| 控制 | 是否经过 | 证据 | 缺失后果 |
|---|---|---|---|
| **预算（F032）** | ❌ **否** | 闸在 `agent_loop.py:199`→`scope.py:352`；`auto_title` 在循环外（`engine.py:806`） | 调用**前不咨询**预算；超支仅在**下一轮**被发现 ⇒ 单次可**越过**预算上界 |
| **取消（F025）** | ❌ **否** | 取消闸 `agent_loop.py:197,204`；`llm.py:663` 仅 `wait_for(timeout)` | 调用**不受取消令牌约束**（仅受总超时） |
| **轮数闸** | ❌ **否** | `agent_loop.py:197` | 该调用**不计入轮数**（设计上它也不是一轮；但于"模型调用总量"口径下**不可见**） |
| **scope** | ❌ **否** | `llm.py` 无 scope 执行代码 | 不读 `window_tokens` 等（该调用**不组装会话上下文**，仅一条 prompt ⇒ 影响小） |
| **guard** | ➖ **不适用** | `tools=None`（`auto_title.py:36`） | 无工具调用，无副作用面 |
| **governance** | ❌ **否** | 无 `decision.issued` | 该调用**不出现在治理因果链**（但**出现在 `llm.*` 事件中**） |
| **审计留痕** | ✅ **是** | `llm.request`/`llm.usage`/`llm.response`/`session.renamed` | — |
| **超时** | ✅ **是** | `llm.py:663`（F017 总时长闸） | — |
| **降级链** | ✅ **是** | `_chat_any` → `chat_with_fallback`（`llm.py:957-961`） | — |

### 10.6.2 ⚠️ 绕过**不是 `auto_title` 独有**（关键校正）

实测：架构的**全部系统工具 LLM 出口**都在循环之外，因而**同样不受三闸约束**：

| 出口 | 调用者 | 位置 | 是否过三闸 |
|---|---|---|---|
| `chat` | `agent_loop.run_turn`（`agent_loop.py:241`） | **循环内** | ✅ 过 |
| `chat` | **`auto_title`（`auto_title.py:36`）** | 循环外（`engine.py:806`） | ❌ **不过** |
| `json_chat` | `plan_mode._json_chat`（`plan_mode.py:341-352`） | 编排层（非循环） | ❌ **不过** |
| `summarize` | `compaction._call_summarize`（`compaction.py:510-516`） | 压缩路径（非循环） | ❌ **不过** |

⇒ **"系统工具类 LLM 调用不受运行时三闸约束"是架构的既存属性**，`auto_title` 只是**其中唯一使用了被 `INV-02` 点名的 `chat` 方法**者。

### 10.6.3 ⭐ 额外发现：`summarize` 的 `budget` 参数**未实现**

`llm.py:974-982`：`budget: int = 400` **只出现在签名**，函数体仅 `_chat_any("chat", messages, None, ctx)` 后取 `content`。
`compaction.py:266,299` 的 spec 却声称"`summarize(prompt, budget=...)` 摘要出口（可缺 → LLM-399 降级）"、"`summarize_budget_tokens`" ⇒ **规格声明的资源上界在当前实现中不生效**。

⇒ **登记为独立 KEY-FINDING 候选（不归入 INV-02）**：见 §10.8 条目 **KF-B**。

---

## 10.7 Recommended Ruling（推荐裁决）

> **裁定 = Option A：确认 `auto_title` 违反 Canonical `INV-02`。**
> **但违反的**范围**必须精确表述**，不外溢：

| 维度 | 裁定 |
|---|---|
| **是否违反 `INV-02`** | ✅ **是** —— `auto_title.py:36` 调用 `llm.chat`（`INV-02` 点名保留给 agent-loop 的方法），且不经过循环三闸 |
| **违反的是"功能存在"吗** | ❌ **不是** —— `auto_title` 作为**系统工具直调 LLM** 的功能形态**有 PRD 依据**（`PRD:1248-1252`） |
| **违反点是** | **① 方法选择**（用了 `chat`，PRD 指定 `mini`）+ **② 无闸**（三闸在循环内） |
| **是否追加"合法例外"以放行** | ❌ **否** —— 依你的明令"不得为了让现有实现通过而临时扩大 INV-02 例外范围"。`mini` 的存在本身即证明**无需**扩大例外 |
| **是否保持 `INV-02` 定义不变** | ✅ **是** —— Registry 与 Canonical Definition **不改**（本阶段亦禁止改） |
| **定性等级** | **P0（不变量违约）** —— 但**非安全漏洞**：审计可还原、无工具副作用、有超时与降级 |

**最小修复边界（设计，不实施）** —— 三条任一即可消除违约：
1. **首选**：补齐 PRD 指定的 **`LLMClient.mini()`**（一次小调用出口，语义 = "系统工具级单次调用"），`auto_title` 改调 `mini`。**不改 `INV-02`、不改循环、不改 Registry**。
2. **次选**：`auto_title` 改走既有 `summarize()` 式包装（需先修 `summarize` 的 `budget` 失效，见 KF-B）。
3. **兜底**：`auto_title` 的功能改由**循环内**完成（如 `session.renamed` 由 run 终态钩子在**循环内**触发）—— 代价最大，且改变 F042 语义。

> **三方案的共同点**：都**不需要**修改 `INV-02` 的定义，也**不需要**创造例外 ⇒ 进一步印证"判 A"是唯一自洽解。

---

## 10.8 Whether KEY-FINDING is Required（**是**）

**需要，且建议登记两条相互独立的条目**（**本阶段均不写入 `docs/KEY-FINDINGS.md`** —— 该动作不在 S6-2a 允许清单内，**等待授权**）：

### KF-A · `auto_title` 绕过 Agent Loop 直接调用 `llm.chat`（违反 INV-02）

| 字段 | 内容 |
|---|---|
| **现象** | `pyharness/core/auto_title.py:36` 调 `ctx.llm.chat(...)`；生产接线于 `engine.py:805-806`（`res.reason=="complete"` 后）；**零测试**（`grep -rln auto_title tests/` 空）；**无 spec**（`docs/specs/auto_title.py.md` 缺失） |
| **违反** | `INV-02`（Canonical：`llm.chat` 唯一合法调用方 = agent-loop） |
| **影响** | 该调用**不经过**轮数闸 / 取消闸 / 预算前置闸（预算超支仅下一轮可见）；**审计面完好**（`llm.request`/`llm.usage`/`llm.response` 齐全）；无工具副作用 |
| **附加偏离** | PRD 指定 `ctx.llm.mini`（`PRD:1251`），实现用了 `chat`；`mini` 不存在；PRD 要求的失败降级"前 20 字符"（`:1244`）未落实；PRD 要求的 `test_f042_title.py`（`:1878`）不存在 |
| **等级** | **P0**（不变量违约）；**非安全漏洞** |
| **处置** | **不修**（S6-2 纪律 14）；修复方向见 §10.7；需**人工裁定**后另行阶段实施 |
| **状态** | `OPEN` |

### KF-B · 系统工具类 LLM 出口不受运行时三闸约束（**独立于 INV-02**）

| 字段 | 内容 |
|---|---|
| **现象** | ① `LLMClient` **全部**出口（`chat`/`chat_stream`/`summarize`/`json_chat`）**均不执行** budget/cancellation/guard/governance 判定（`llm.py` 无相关**执行**代码）；三闸**只**在 `agent_loop.py:197-199`；② 非循环消费者 `plan_mode→json_chat`、`compaction→summarize` **同样不过闸**；③ **`summarize(budget=400)` 的 `budget` 参数在实现中从未被使用**（`llm.py:974-982`），而 `compaction.py:266,299` 的 spec 声称该上界存在 |
| **为何独立** | `json_chat`/`summarize` **不使用 `llm.chat`** ⇒ **不违反 `INV-02`**；但**存在资源控制缺失**（"不违反 INV-02" ≠ "无工程风险"） |
| **等级** | **P1**（资源/控制面；无审计缺失、无安全绕过面） |
| **处置** | **不修**；**不归入 INV-02**；建议独立评估（可与 durability/reliability 阶段合并） |
| **状态** | `OPEN` |

---

## 10.9 Impact on S6-2b

| 项 | 影响 |
|---|---|
| **`INV-02` 静态用例能否现在落？** | ❌ **不能**。§7 拟议的 `test_inv02_llm_chat_sole_caller` **在当前代码上必然 RED**。且 §10.7 已判 A ⇒ 该 RED 是**真实违约**，**不是**测试写错 |
| **S6-2b 的处置** | **暂不**为 `INV-02` 落"唯一调用方"断言。先在 S6-2b **只落不依赖该断言的部分**：`test_inv02_no_direct_endpoint_client`（除 `llm.py` 外无 LLM 端点客户端构造 —— 实测当前 **GREEN**，`skill_registry` 的 httpx 与 LLM 无关） |
| **何时补 `INV-02` 主断言** | 待 KF-A 的**修复落地**（补齐 `mini` 或改造调用路径）后**再补**，届时断言应 GREEN 且**可回归** |
| **是否用 `xfail`/`skip` 规避** | ❌ **禁止** —— 违 S6-2 纪律 13 与 S6 通则（不得以 skip 掩盖问题） |
| **对 §6 缺口清单的修订** | 缺口 #1（INV-02 唯一调用方）**性质变更**：从"缺测试"→**"已知违约 + 缺测试 + 待修复"**，优先级维持 **P0**，但**执行顺序后移**至修复之后 |
| **对 §8 最小测试设计的修订** | #3 拆为两步：(i) `test_inv02_no_direct_endpoint_client`（**现在可落**）；(ii) `test_inv02_llm_chat_sole_caller`（**待 KF-A 修复后落**） |
| **INV-08 拓扑用例（§8 #9）** | **不受影响**，但同样建议**先只读验证**当前 import 图是否有回边，再落断言（若发现回边即**又一个独立发现**，按纪律 14 登记，非本 P0 范围） |

---

## 10.10 本节边界声明

**做了**：完整调用链追踪（`auto_title.py:36` ↔ `engine.py:805-806`）· `LLMClient` 四出口与保护边界的实现级核验 · 规格/架构意图取证（PRD/CONSTRAINTS-06/DIS-CORE/specs/DIS-SEAM）· 证据链 · A/B/C 判据 · 控制绕过独立分析 · 关键修复边界设计 · KEY-FINDINGS 条目草案。

**没做**（S6-2a-P0 明令禁止）：❌ 修改 Python 生产代码 · ❌ 修改现有测试 · ❌ **创建正式 INV-02 测试** · ❌ 修改 `INVARIANT_REGISTRY.md` · ❌ 修改 INV-02 定义 · ❌ 修改 Governance Core · ❌ 修复 `auto_title` · ❌ 修复 L-3 · ❌ 创建 acceptance/security tests · ❌ 进入 S6-2b · ❌ **写入正式 `docs/KEY-FINDINGS.md`** · ❌ commit · ❌ push。

**本阶段产物**：仅本文件（`S6-2_COVERAGE_MATRIX.md`）的 §10 新增段。中间实测数据在 `tmp/`（gitignore 覆盖）。

**worktree**：仅 `?? S6-2_COVERAGE_MATRIX.md`；**HEAD 仍 `4f05c95`**。

---

**S6-2a-P0 结束。推荐裁决 = Option A。等待你的人工裁决；未写测试、未改代码、未改 Registry、未 commit、未 push。**

---
---

# §11 INV-02 P0 Remediation Design（S6-2a-P0-R）

> **阶段**：S6-2a-P0-R（**只设计，不实现**）
> **裁定前提**（已由人工接受）：`INV-02` **Option A 违反成立**;违规点精确定义为 **① 出口选择（误用 Agent Loop 专用的 `ctx.llm.chat()`，而 PRD F042 指定 `ctx.llm.mini(...)`）+ ② 无闸（已划归 KF-B 独立处理）**。
> **边界**：本节**只做设计分析**。未改任何 `pyharness/**/*.py` / 现有测试 / Registry / Event Schema / Governance Core；**未创建 INV-02 测试**；**未新建 INV 编号**；未 commit / 未 push。
> **KF-A / KF-B 已登记**：`docs/KEY-FINDINGS.md` **附录 A**（**+40/−0 纯追加**，既有条目零改动）。

## 11.1 Finding

**KF-A（P0）**：`pyharness/core/auto_title.py:36` 调用 `ctx.llm.chat([...], tools=None, ctx=ctx)`，经 `pyharness/engine.py:805-806` 在生产路径接线，**违反 Canonical `INV-02`**（"全库 `llm.chat` 唯一合法调用方 = agent-loop"）。详见 `docs/KEY-FINDINGS.md` 附录 A.1。

## 11.2 Root Cause（根因）

**F042 被设计为"系统工具"类直调，PRD 为其指定了专用出口 `mini()`；但该出口在实现中不存在，实现者只能落到唯一的对话出口 `chat`。**

```
PRD:1251  title = await ctx.llm.mini(f"用≤24字概括: {first[:200]}")
             └─ 指定出口 = mini（系统工具级单次调用）
                    │
                    ▼
             grep "def mini" pyharness/ → 零命中（mini 不存在）
                    │
                    ▼
auto_title.py:36  ctx.llm.chat([...], tools=None, ctx=ctx)
             └─ 落到「对话出口」——INV-02 点名保留给 agent-loop 的那一个
                    │
                    ▼
             INV-02 字面被违反（第二个 chat 调用方）
```

**深层成因（架构层）**：`LLMClient` 当时**已有** `summarize`（F058）与 `json_chat`（F045）两个系统工具出口，**唯独缺少 F042 指定的 `mini`** ⇒ **F042 是唯一"规格指定了出口、实现却没有该出口"的功能**，因而成为唯一使用对话出口的系统工具。

## 11.3 Step 1 · `mini()` 设计分析

### 11.3.1 十四项设计裁定

| # | 问题 | 裁定 | 依据 |
|---|---|---|---|
| **1** | **职责** | **系统工具级单次文本调用**：单 prompt 进 → 纯文本出。不组装会话上下文、不带工具、不参与对话轮。 | PRD:1242"规则或**一次迷你 LLM** 生成";PRD:1251 用法 |
| **2** | **与 `chat()` 的区别** | 不只是名字，**契约面不同**：`chat(messages, tools, *, ctx) -> LLMResponse`（全量 messages + tools schema + 含 `tool_calls`/`usage`/`seq` 的响应 + 一轮对话语义）；`mini(prompt, *, ctx) -> str`（单串入、单串出、无 tools、无轮语义）。 | `llm.py:964` vs 设计稿 |
| **3** | **与 `summarize()` / `json_chat()` 的关系** | **同类**（System Tool LLM Endpoint）。三者**同构**：都是 `_chat_any("chat", …, None, ctx)` 的薄包装（`llm.py:981,997`），共享单次/无 tools/无轮语义/同一条降级链/同一套事件。**差异仅在输出形态**：`mini`=纯文本 · `summarize`=压缩语义 · `json_chat`=JSON 抽取。 | `llm.py:957,974-1003` |
| **4** | **是否属 System Tool LLM Endpoint** | ✅ **是**。有 PRD 明文依据（F042 指定），且架构已有两个同类成员 ⇒ **不是新概念，是补齐既有类别的一个缺失成员**。 | `PRD:1251`；`llm.py:974,984` |
| **5** | **输入输出契约** | `async def mini(self, prompt: str, *, ctx: Any) -> str`；返回 `(resp.content or "").strip()`；**空内容返回 `""`（不抛）**，由调用方决定降级 —— 与 `summarize` 同款。 | 对齐 `llm.py:974-982` |
| **6** | **timeout** | **复用** `LLMAdapter.chat` 的 F017 总时长闸（`llm.py:663`）。**不新增第二套超时真源**（新增=制造配置分裂）。 | `llm.py:663` |
| **7** | **usage accounting** | ✅ **自动获得，零新增代码** —— 仍走 `_chat_any` → adapter → `report_usage`（`llm.py:558`）→ `llm.usage` + `counters.task_add`（`:561`）。 | `llm.py:558,561` |
| **8** | **event logging** | ✅ **自动获得，事件类型零新增** —— `llm.request`（`:661`）+ `llm.usage`（`:558`）+ `llm.response`（`:682`）。**`EVENT_TYPES` 不变（仍 77）**。 | `llm.py:661,558,682` |
| **9** | **error handling** | 沿用 `_chat_any` 的错误归一（`normalize_exc`，`:665`）与 `LLM-3xx` 结构化上抛；**`mini` 自身不吞错**，由 `auto_title` 按 F042 降级。 | `llm.py:665` |
| **10** | **fallback** | ✅ **自动获得** —— `_chat_any` 在降级链在岗时走 `chat_with_fallback`（`:957-961`），与 `chat` **同一条链**。 | `llm.py:957-961` |
| **11** | **budget 是否应该存在** | ❌ **不设 `budget` 参数**。理由：`summarize(budget=400)` 的前车之鉴是**参数存在但从未实现**（`llm.py:974-982`）⇒ 制造"看起来有上界"的**错觉，比没有更糟**。是否引入**真正生效**的统一上界 = **KF-B 的设计题**，不预埋死参数。 | KF-B / §12 |
| **12** | **cancellation 是否应该存在** | ❌ **本轮不增设**。取消语义属循环（`run.cancelled`，`agent_loop.py:204`）；`auto_title` 在 run **已 complete 之后**执行，取消令牌已过期。为系统工具出口引入取消 = **KF-B 范围**。 | `engine.py:802`；KF-B |
| **13** | **是否需要 governance** | ❌ **不需要**。① 无工具调用 ⇒ 无执行授权面（`tools=None`）；② `ADR-018:308` 禁止 `governance/` 与 `llm` 互相依赖 ⇒ 引入治理决策须**新 ADR** ⇒ 属 KF-B 待决项，不属 INV-02 修复。 | `ADR-018:308` |
| **14** | **是否需要 audit** | ✅ **已有，无需新增**（同 #8）。**但不在治理因果链**（无 `decision.issued`）—— 该事实与 KF-B 一致，**不是本修复的缺口**。 | §10.2 |

### 11.3.2 ⭐ 为什么 `auto_title → mini` 能**正确**解决 INV-02，而不是"换一个函数名"

必须分三层回答（**第三层是诚实的限制声明**）：

**第 1 层 —— 它恢复的是 INV-02 的"契约对象"，不是"名字"**
INV-02 的对象是 **`llm.chat` 这个契约**：`(messages, tools) -> LLMResponse[含 tool_calls]`，这是**对话轮**的签名。而 `auto_title` 的调用形态（单 prompt、`tools=None`、只取 `.content`）**本就不是对话轮** —— 它是在**误用**对话出口。`mini(prompt) -> str` 与它的真实用法**契约对齐**。
⇒ 换的是**出口类别**（对话出口 → 系统工具出口），**不是函数名**。

**第 2 层 —— 它把"例外"变成"类别"（这是本质区别）**

| 方案 | 得到的结构 | 可机械校验？ | 新增系统工具时 |
|---|---|---|---|
| 修改 INV-02 加"`auto_title` 例外" | **枚举式例外** | ❌ 无法校验（"因为是 auto_title"不可判定） | 每加一个都要**再扩一次例外** |
| 新增 `mini`（本方案） | **类别式边界** | ✅ 可静态校验：**除 `agent_loop` 外无模块调用公开 `.chat(`/`.chat_stream(`** | **无需再改 INV-02** |

⇒ 这正是"补类别"与"开例外"的分野，也是**不构成"临时扩大 INV-02 例外范围"**的理由。

**第 3 层 —— ⚠️ 它【不】解决"三闸"问题（必须明确声明）**

裁定把违规点定义为 **① 出口选择 + ② 无闸**。**`mini` 只修复 ①**。修复后 `auto_title` **仍不经过**轮数/取消/预算闸（与 `summarize`/`json_chat` 同）。

- **这不是隐瞒性"部分修复"**，而是**与裁定范围一致**：② 已被裁定**明确划归 KF-B 独立处理**（`docs/KEY-FINDINGS.md` A.2）。
- **同时必须声明**：修复后 **`INV-02` GREEN**，但 **`KF-B` 仍 `OPEN`**。
- **若**目标被改为"每次模型调用都必须过预算闸"，那是 **KF-B 的设计题（更大、需新 ADR）**，**不在 INV-02 修复内**，也不应以 INV-02 之名夹带。

## 11.4 Step 2 · F042 完整要求追踪（PRD → Implementation → Current Gap）

> F042 规格：`docs/PRD-Core.md:1241-1254`（含验收伪码）；验收清单：`PRD:1878`。

| # | PRD 要求 | 出处 | 当前实现 | 状态 |
|---|---|---|---|---|
| **1** | 出口 = **`ctx.llm.mini(...)`** | `PRD:1251` | `ctx.llm.chat([...], tools=None, ctx=ctx)`（`auto_title.py:36`）；**`mini` 不存在** | ❌ **偏离（KF-A 本体）** |
| **2** | 输入截断 = `first[:200]` | `PRD:1251` | `first[:500]`（`auto_title.py:22`） | ❌ **偏离** |
| **3** | 标题 ≤ **24 字** | `PRD:1242,1252`；`auto_title.py:3` docstring 亦写 ≤24 | `TITLE_MAX = 64`（`:16`），`[:TITLE_MAX]`（`:42`） | ❌ **偏离**（模块 docstring 与常量自相矛盾） |
| **4** | 失败降级"**前 20 字符**" | `PRD:1244,1251`（`or first[:20]`） | 失败 → `return None`（`:38-40`） | ❌ **缺失**（无规则降级） |
| **5** | 空结果降级"前 20 字符" | `PRD:1251`（`or first[:20]`） | 空 → `return None`（`:43-44`） | ❌ **缺失** |
| **6** | 写 `session.renamed`（`by="auto"`） | `PRD:1243,1252` | ✅ `append("session.renamed", {"new_title":…, "by":"auto"}, actor="system")`（`:45-47`） | ✅ 符合 |
| **7** | 只做一次（再改需显式 `/rename`） | `PRD:1244` | ✅ 日志已有 `session.renamed` → `return None`（`:28`） | ✅ 符合（**日志派生，更合 INV-01**） |
| **8** | 必须异步 | `PRD:1248`（`async def`） | ✅ `async def auto_title`（`:26`） | ✅ 符合 |
| **9** | **不阻塞**主流程 | `PRD:1244` | ✅ `engine.py:808-809` `except Exception` 吞掉；`auto_title` 内 `PyHError` 亦吞 | ✅ 符合 |
| **10** | **标题不入 LLM 上下文** | `PRD:1244` | ✅ `session.renamed` 不在 reducer 的事件→消息映射内（仅标题派生用） | ✅ 符合 |
| **11** | 提供 `test_f042_title.py`（一次性/降级/≤24 字） | `PRD:1878` | ❌ **文件不存在**；`auto_title` **零测试** | ❌ **缺失** |
| **12** | 存在模块 spec | `docs/specs/` 体例（32 模块） | ❌ `docs/specs/auto_title.py.md` **不存在** | ❌ **缺失** |

**小结**：F042 共 **5 项实现偏离 + 2 项交付物缺失**（测试文件、spec）。其中 **#1 是 `INV-02` 违约本体**；**#2~#5、#11、#12 是 F042 的规格遵从性问题**，与 `INV-02` **无关**，属**独立于 KF-A/B 的第三类**（建议一并裁定归属，见 §11.9）。

## 11.5 Specification Evidence

| 证据 | 原文要点 |
|---|---|
| `docs/PRD-Core.md:838`（F018，上位权威） | "INV-02 **无绕过 agent-loop 直调 llm**" |
| `docs/CONSTRAINTS-06-Testing.md:63` | 断言 = "**全库 `llm.chat` 唯一合法调用方 = agent-loop**" |
| `docs/DIS-CORE.md:186` | "INV-02：本模块是 `llm.chat` 唯一合法调用方；绕过即架构违规" |
| `docs/specs/agent_loop.py.md:262` | "（**测试钉死**）" |
| `docs/PRD-Core.md:634` | 意图 = "三态+三闸"是"死循环烧钱"的结构性解药 ⇒ 每次调用须过闸 |
| `docs/PRD-Core.md:1251` | F042 指定出口 = **`ctx.llm.mini`**（**修复的规格依据**） |
| `docs/specs/plan_mode.py.md:17` | `ctx.llm.json_chat`（**唯一 LLM 出口**，INV-02）—— 佐证"系统工具出口"是被认可的**类别** |
| `ARCHITECTURE_DECISION_RECORD.md:308`（ADR-018） | `governance/` 只允许依赖 `events` + `errors`（约束 #13 的设计空间） |

## 11.6 Implementation Evidence

| 证据 | 内容 |
|---|---|
| `pyharness/core/auto_title.py:36` | 违约调用点（`ctx.llm.chat`） |
| `pyharness/engine.py:800,802-809` | 调用时机 = `loop.wake()` **返回之后**（循环外）；异常静默吞 |
| `pyharness/core/agent_loop.py:196-199,241` | 三闸全在循环内；循环内的合法 `chat` 调用走 `getattr(ctx.llm, chat_fn)` |
| `pyharness/core/llm.py:957-961` | `_chat_any` = 四出口唯一汇聚点（含降级链路由） |
| `pyharness/core/llm.py:647-684` | `LLMAdapter.chat` = 唯一物理出口（事件/超时/归一/计量/落盘；**无准入判定**） |
| `pyharness/core/llm.py:964,969,974,984` | `LLMClient` 四出口：`chat` / `chat_stream` / `summarize` / `json_chat`（**无 `mini`**） |
| `pyharness/core/llm.py:974-982` | `summarize.budget` **仅存在于签名**（判别 #11 的前车之鉴） |
| `tests/`（全库） | `grep -rln auto_title tests/` → **空**（零测试） |

## 11.7 Minimal Remediation（三方案比较）

| 维度 | **Option A**<br>新增 `LLMClient.mini()` | **Option B**<br>改用现有 `summarize()` | **Option C**<br>统一 System LLM Gateway |
|---|---|---|---|
| **做法** | `llm.py` 新增 `mini`；`auto_title` 改调 `mini` | `auto_title` 改调 `summarize`（1 行） | 新建网关模块，统管 `mini`/`summarize`/`json_chat` 的准入控制 |
| **修改文件数** | **2**（`llm.py` +10 行；`auto_title.py` 改 2-3 行） | **1**（`auto_title.py` 改 2 行） | **≥5 + 新模块**（网关 + `llm`/`plan_mode`/`compaction`/`auto_title` + 装配层） |
| **行为变化** | 仅**该次调用的出口方法**；事件/计量/降级**全不变**；**无闸状态不变**（KF-B） | 同 A 的出口变化；**额外**：`summarize(budget=400)` 会被传而**静默无效** ⇒ 误导 | **大**：三出口准入语义统一；可能改变事件与预算语义 |
| **风险** | **低**（纯新增方法 + 2 处调用替换；`auto_title` 零测试 ⇒ 无回归面） | **中**：语义错配（`summarize` 契约 = F058 压缩摘要，被 `compaction` 消费）+ **掩盖 KF-B** | **高**：架构级；需新 ADR；S6 为测试阶段（冻结计划 §5.2"M8/M9 均为测试，不触及安全主干"） |
| **对 Governance Core** | **零影响**（不 import `governance`；`ADR-018:308` 依赖方向不破） | 零影响 | **可能触及**（把治理决策引入 LLM 出口 ⇒ **须新 ADR**） |
| **对 Event Schema** | **零影响**（无新事件类型、无 payload 变更；`EVENT_TYPES` 仍 77） | 零影响 | **可能新增**事件 |
| **对现有测试** | **零影响** | 零影响 | **可能影响** `compaction` / `plan_mode` 相关用例 |
| **是否扩大 S6 范围** | **否**（属 `INV-02` 修复本体） | 否 | **是 —— 明确扩大** ⇒ **不予采纳** |
| **是否消除 INV-02 违约** | ✅ 消除 | ✅ 消除（但**继续偏离 PRD**，规格指定的是 `mini`） | ✅ 消除（代价过大） |
| **是否需要新 ADR** | ❌ **不需要** —— `PRD:1251` 已指定 `mini`，属**实现既有规格**；按 S2-1 的偏离登记先例（记入报告即可） | ❌ 不需要 | ✅ **需要** |

## 11.8 Recommended Option（最小可接受方案）

> ### ✅ **推荐 Option A**：新增 `LLMClient.mini()`，`auto_title` 改调 `mini`。

**理由**：

1. **规格已存在**：`PRD:1251` 明文指定 `mini` ⇒ 本方案是**实现既有规格**，不是新增架构决策 ⇒ **无需 ADR**（按 S2-1 先例记入修复报告）。
2. **改动最小且零回归面**：2 文件；`llm.py` 纯新增方法；`auto_title` **零测试** ⇒ 无既有断言可被破坏。
3. **零外溢**：**Event Schema 不变**（77/14/3）· **Governance Core 不变** · **既有测试 0 影响**。
4. **不扩大 S6 范围**：属 `INV-02` 修复本体，不是架构重构。
5. **消除违约且不扩大例外**：把"例外"变成"类别"（§11.3.2 第 2 层），守 `P-4`。

**最小代码边界（设计，不实施）**：

| 文件 | 变更 | 规模 |
|---|---|---|
| `pyharness/core/llm.py` | 在 `summarize` **之前**新增 `async def mini(self, prompt: str, *, ctx: Any) -> str` —— 体为用户 `_chat_any("chat", [{"role":"user","content":prompt}], None, ctx)` 后取 `content.strip()`；**不设 `budget` 参数**（判别 #11） | **+≈8 行** |
| `pyharness/core/auto_title.py` | `:36-37` 的 `resp = await ctx.llm.chat([...], tools=None, ctx=ctx)` 改为 `text = await ctx.llm.mini(prompt, ctx=ctx)`；`:41-42` 的解构相应简化为直接对 `text` 处理（去掉 `resp.content` / `.splitlines()` 的 `chat` 残留语义） | **改 ≈3 行** |

**⚠️ 随附项（需单独勾选，不默认包含）**：F042 的其余规格偏离 **#2（`first[:200]`）· #3（≤24 字）· #4/#5（失败/空降级"前 20 字符"）· #11（`test_f042_title.py`）· #12（spec）**。
- **含义**：修 #3 会**改变现有行为**（64 → 24 字上限），修 #4/#5 会**新增降级路径** ⇒ 属**行为变更**，**不属"最小修复"**。
- **建议**：与 `INV-02` 修复**分开裁定**（可同批授权，但须显式勾选），并在修复报告中按 S2-1 体例登记为**规格遵从性修复**。
- 无论是否随附，**F042 的 `mini` 出口修复（#1）都是必须项**。

## 11.9 Post-fix Test Plan（Step 5 · **只设计，不创建**）

### KF-A 修复后（`INV-02`）

| # | 验证目标 | 测试形态 | 断言要点 | 预期 |
|---|---|---|---|---|
| **1** | `auto_title` **不再调用 `chat`** | `tests/invariants/` 静态（AST） | `auto_title.py` 内无 `.chat(` 属性调用 | GREEN（修复后） |
| **2** | `auto_title` 调用 **F042 指定出口** | `unit`（注入记录型假 LLM） | 假 LLM 记录被调方法名 ⇒ 断言 `mini` 被调 **且** `chat` 未被调 | GREEN |
| **3** | `agent_loop` 仍是 `chat` 的**合法路径** | 静态（AST，全库） | 公开 `.chat(` / `.chat_stream(` 调用点 ⊆ `{pyharness/core/agent_loop.py}` | GREEN |
| **4** | **`INV-02` 唯一调用约束恢复** | `tests/invariants/test_inv_core.py::test_inv02_llm_chat_sole_caller` | 同 #3 + 允许集显式声明（白名单须写明理由，沿用 S2-5.1 T-4 体例） | GREEN |
| **5** | **F042 fallback 符合规格** | `unit`（新 `test_f042_title.py`，`PRD:1878` 要求） | 一次性（幂等）· 失败 → `first[:20]` · 空 → `first[:20]` · ≤24 字截断 · `session.renamed(by="auto")` · 标题不入 LLM 上下文 | GREEN（**若随附项被授权**） |

**前置依赖**：**#4 是 `INV-02` 主断言，必须待 KF-A 修复落地后方可落**（修复前必 RED，且 RED 为**真违约**，不得以 `xfail`/`skip` 规避 —— 违 S6-2 纪律 13）。
**可先行项**：`test_inv02_no_direct_endpoint_client`（除 `llm.py` 外无 LLM 端点客户端构造）—— 实测当前 **GREEN**，**现在即可落**。

### KF-B（**只设计，不实施**）

| # | 未来验证目标 | 前置条件 |
|---|---|---|
| **1** | `summarize` 的 `budget` **真正生效**，**或**该死参数被移除 | 需先裁定 KF-B 的控制设计 |
| **2** | 系统工具出口在调用**前**咨询预算（F032） | 需先有 KF-B 的准入设计（可能需新 ADR） |
| **3** | 系统工具出口受取消令牌约束（F025） | 同上 |

---

# §12 KF-B System LLM Endpoint Governance（**只登记现状与风险，不进入实现**）

> **定位**：**独立于 `INV-02`** 的控制面缺口（`docs/KEY-FINDINGS.md` A.2，P1）。**不新建 INV 编号、不修改 Registry。** 本节**只做问题建模**。

## 12.1 `json_chat` 现状

| 项 | 内容 |
|---|---|
| **定义** | `pyharness/core/llm.py:984-1003` —— "JSON 强约束出口（plan_mode 等编排消费）：单次 chat 后稳健抽取 JSON" |
| **谁调用** | `pyharness/core/plan_mode.py:341-352`（`PlanManager._json_chat`，经 `getattr(llm,"json_chat")` 注入式取用）；spec 认可：`docs/specs/plan_mode.py.md:17`"消费 `ctx.llm.json_chat`（**唯一 LLM 出口**，INV-02）" |
| **为什么允许不走 Agent Loop** | 它是**编排层的结构性决策请求**（生成 plan 提案），**不是对话轮**：输出是结构化 JSON（受 `PlanManager` 结构校验），输出**不进入模型上下文**，且**不产生工具执行**。⇒ 语义上不属于 `INV-02` 守护的"对话路径" |
| **有哪些控制** | ✅ 单端点路径（经 `_chat_any`→`chat_with_fallback`）· ✅ 超时（F017）· ✅ 降级链 · ✅ 计量（`llm.usage`）· ✅ 事件留痕（`llm.request`/`llm.response`）· ✅ JSON 解析失败 → `LLM-304`（**不静默伪装**）· ✅ 结构校验在消费方 |
| **缺什么控制** | ❌ **预算前置**（F032）· ❌ **取消**（F025）· ❌ **轮数归属** · ❌ 无"该出口是否被允许调用"的**准入判定**（依赖消费方自律）· ⚠️ 参数无资源上界（对比 `summarize` 至少**声明**了 `budget`） |

## 12.2 `summarize` 现状

| 项 | 内容 |
|---|---|
| **定义** | `pyharness/core/llm.py:974-982` —— `async def summarize(self, prompt: str, *, budget: int = 400, ctx: Any = None) -> str` |
| **谁调用** | `pyharness/core/compaction.py:510-516`（`_call_summarize`，经 `getattr(llm,"summarize")`）；未装配 → `LLM-399` 走**规则降级**（`compaction.py:515-516`） |
| **`budget` 参数** | 签名默认 `400`；spec 侧声称存在：`compaction.py:266`（"`summarize(prompt, budget=...)` 摘要出口"）· `compaction.py:299`（`"summarize_budget_tokens"` → `_summary_budget`） |
| **实际是否执行** | ❌ **未执行** —— `budget` **只出现在签名**（`sed -n '974,983p' llm.py \| grep budget` 仅命中第 1 行）；函数体为 `_chat_any("chat", messages, None, ctx)` 后取 `content`，**全程不读 `budget`** |
| **有哪些控制** | 同 12.1：单端点 · 超时 · 降级链 · 计量 · 事件留痕 · ✅ **未装配时 fail-soft 降级**（`LLM-399` → 规则摘要） |
| **缺什么控制** | ❌ **预算前置**（F032）· ❌ **取消**（F025）· ❌ 轮数归属 · ⚠️ **`budget` 参数存在但失效** —— 比"没有参数"更危险：调用方以为有上界 |

## 12.3 缺口汇总（`System LLM Endpoints Governance Gap`）

```text
KF-B  System LLM Endpoints Governance Gap
  │
  ├─ 成员（非 agent_loop 的 LLM 出口）
  │    ├─ json_chat   ← plan_mode（编排提案）
  │    ├─ summarize   ← compaction（压缩摘要）
  │    └─ mini        ← auto_title（KF-A 修复后纳入本类）
  │
  ├─ 控制绑定错位（根因）
  │    └─ 三闸实现于「循环」而非「LLM 出口」：
  │         agent_loop.py:197(_must_stop) · :199(scope.check_budget) · :204(run.cancelled)
  │         而 llm.py 全链无任何准入判定（只有 请求→超时→归一→计量→落盘）
  │
  ├─ 缺失的控制
  │    ├─ 预算前置（F032）—— 超支仅下一轮可见
  │    ├─ 取消（F025）—— 不受取消令牌约束
  │    └─ 轮数归属 —— 该类调用不出现在轮计数中
  │
  ├─ 不受损的面（须如实记录，避免夸大）
  │    ├─ 审计留痕（llm.request / llm.usage / llm.response 齐全）
  │    ├─ 单端点路径 + 超时 F017 + 降级链
  │    └─ guard —— 无工具调用，不适用
  │
  └─ 附带事实
       └─ summarize 的 budget 参数（默认 400）在实现中从未被使用
          （spec 声称存在：compaction.py:266,299）⇒ 误导性"有上界"印象
```

**性质声明**：这是 **P1 工程风险**，**不是**不变量违约（`json_chat`/`summarize` 不使用 `llm.chat` ⇒ 不触 `INV-02`）。
**不新建 INV 编号**；**不修改 Registry**；**不解决**（S6-2a-P0-R 边界）。

## 12.4 KF-B 未来测试（**只设计，不实施**）

| # | 目标 | 前置 |
|---|---|---|
| 1 | `summarize(budget=…)` 的 `budget` **生效**（或参数被移除、spec 同步订正） | 需先裁定 KF-B 设计 |
| 2 | 系统工具出口调用**前**咨询预算（F032） | 需先有准入设计（可能需新 ADR） |
| 3 | 系统工具出口受取消令牌约束（F025） | 同上 |
| 4 | 三出口的准入判定**单一真源**（不得各出口自行实现） | 同上 |

**建议归属**：与 durability/reliability 评估阶段合并（**不进 S6-2b**）。

---

# §13 S6-2b 可开始性评估

| 项 | 结论 |
|---|---|
| **S6-2b 能否开始？** | ✅ **可以开始，但须带一个明确豁免** |
| **可直接落的** | INV-01 · INV-03 · INV-04 · INV-05 · INV-06 · INV-07 · INV-08 · INV-09 的编号化用例 + `INV-G3` 专项 + `test_inv02_no_direct_endpoint_client` |
| **必须缓落的** | `INV-02` 主断言 `test_inv02_llm_chat_sole_caller` —— **待 KF-A 修复落地后**（现在落必 RED，且 RED 为真违约） |
| **前置动作（需另行授权）** | **KF-A 修复**（Option A）—— **属生产代码改动，不得在 S6-2（测试阶段）内实施**；须先经人工授权，作为独立步骤执行 |
| **测试阶段纪律** | 不得以 `xfail`/`skip` 规避真违约（S6-2 纪律 13） |
| **对 §6 缺口清单的修订** | 缺口 #1 性质变更为"**已知违约 + 缺测试 + 待修复**"，优先级 **P0**，执行顺序**后移**至修复之后 |

---

**S6-2a-P0-R 结束。推荐最小修复 = Option A。等待你的人工批准后再执行任何代码修改；未改代码、未改 Registry、未创建测试、未 commit、未 push。**
