# docs/INVARIANT_REGISTRY.md — Canonical Invariant Registry

> **性质**：**Canonical Invariant Registry（权威不变量注册表）** —— `INV-01`~`INV-09` 的**唯一权威定义来源**。
> **版本**：v1.0 ｜ **建立日期**：2026-09-15 ｜ **基线 commit**：`753b176`（S6 Entry Plan 冻结）
> **裁决依据**：`S6-1_INV_CLASSIFICATION.md` §12（2026-09-15 人工裁定）｜ **上位权威**：`docs/PRD-Core.md:838`（F018）
> **本文件状态**：**已建立（`established`）**；库中历史文本引用**尚未迁移**（19 处 `pending authorization`，见 §2 / §5）。

---

## 0. Canonical Source of Truth（权威性声明）

**本 Registry 是 `INV-01`~`INV-09` 的 Canonical Source of Truth。**

后续**一切**引用本组不变量编号的产物，**均以本 Registry 的 Canonical ID 为准**：

| 产物类别 | 落点 | 要求 |
|---|---|---|
| 不变量测试 | `tests/invariants/` | 用例的 INV 编号**必须**取自本 Registry；失败信息须能指出所违反的 Canonical ID |
| 验收测试 | `tests/acceptance/` | 同上；功能验收（`F0xx`）**不得**占用 INV 编号 |
| 安全测试 | `tests/security/` | 同上；安全断言须标注所守护的 Canonical ID |
| 报告 | `S*_CHANGE_REPORT.md` / `S*_FINAL_REVIEW.md` / 审计报告 | INV 引用以本 Registry 为唯一口径 |
| 未来 ADR | `ADR-0xx` / `docs/ADD.md` | 引用 INV 时以本 Registry 为准 |

### 0.1 正式原则（P-1 ~ P-5）

| # | 原则 |
|---|---|
| **P-1** | 一个 Canonical INV ID 只能对应**一个**正式语义。 |
| **P-2** | 现有代码 / 测试中的 INV 标记**不能自动视为权威**，只能作为**历史证据**。 |
| **P-3** | 已废弃编号**必须**通过 `Legacy Mapping` 保留历史关系（**不得删除历史语义**）。 |
| **P-4** | **不允许**为保留旧编号而重新解释已废弃的语义。 |
| **P-5** | 后续 tests / acceptance / security / reports（及未来 ADR 引用）全部以本 Registry 为**唯一编号来源**。 |

### 0.2 计数口径

本 Registry 与 `S6-1_INV_CLASSIFICATION.md` 使用**同一实测口径**：全库 `INV-0x` 出现次数（**occurrences**，非命中行数），扫描排除 `.git`/`.venv`/`build`/`dist`/`__pycache__`/`.pytest_cache`/`docs_html`，并排除本机残留产物 `tmp/_staged.diff`（84 次，`.gitignore` 已覆盖）。

**基线总数：812**（`docs+根 md` 517 · `pyharness/` 193 · `tests/` 95 · `scripts/` 7）。

### 0.3 `Status` 取值定义

| 值 | 含义 |
|---|---|
| `established` | 该 Canonical ID 的编号与语义在库中**已一致**，且**无**待迁移的历史错标引用。 |
| `migrated` | 该 Canonical ID 的**语义归属已裁定**，但库中**仍有**历史文本引用待迁移（见 §2 中 `Status = pending authorization` 的条目）。 |
| `retired` | 已废弃编号，**不作为 Canonical ID**；仅保留历史映射（见 §3）。 |

> **关于 `gap`**：覆盖缺口**不放入 `Status`**，而统一登记在每条 ID 的 **`Coverage Gap`** 字段 —— 因九条 ID **当前全部**存在覆盖缺口，若以 `gap` 作 Status 则失去区分度。`Status` 只表达**编号状态**。

---

## 1. Canonical INV-01 ~ INV-09

### INV-01

| 字段 | 内容 |
|---|---|
| **ID** | `INV-01` |
| **Canonical Name** | 日志只追加 / 历史必由日志派生（**合并式**，按 `CONSTRAINTS-06` §7） |
| **Canonical Definition** | 会话 JSONL 日志是**唯一真源**且**只追加**（无 `update` / `delete` / 改写 / 清空 API）；一切历史与派生形态（消息历史 / 统计 / UI 轨迹 / FTS 索引 / 预算 / todo）**均由日志派生**，不存在第二份权威状态；派生视图可**整体丢弃重建**。 |
| **Intent** | 事件溯源的立身之本：一次获得**回放 / 审计 / 恢复 / 一致性**四能力。消除"第二份状态"使**模型所见 ≡ 日志**、审计可信 —— 否则模块可自维护内存历史，注入经该路径混入而日志无痕（`SECURITY.md:206`）。 |
| **Evidence Source** | `docs/CONSTRAINTS-06-Testing.md:62`（权威表，合并式）· `docs/PRD-Core.md:838`（上位权威，F018）· `PRD-Core.md:308,357,360` · `docs/SECURITY.md:196,206` · `docs/EVENT-SCHEMA.md:13,503,629` · `docs/DIS-CORE.md:28,342,470` · `docs/SOP.md:51` |
| **Existing Test Evidence** | `tests/unit/test_session.py::test_method_surface_append_only_inv01`（分句①）· `::test_append_only_no_second_history_store_inv02`（分句②）· `::test_finished_flush_failure_keeps_closed_but_log_empty` · `tests/unit/test_goal.py::test_rebuild_from_events_matches_live_state`（分句②）· `tests/unit/test_plan_mode.py::test_real_session_full_lifecycle_events_valid` · `tests/unit/test_compaction.py::test_compact_appends_declaration_only_append_only`（分句①）· `tests/unit/test_agent_loop.py::test_single_turn_text_complete` |
| **Coverage Gap** | ① **`append` 后文件 hash 不变**（`CONSTRAINTS-06:62` 明文断言）—— 实测 `tests/` 内**无**针对会话 JSONL 的 hash 不变断言；② **"除 `session.py` 外无 append 到消息列表路径"**（`PRD-Core.md:360`）—— **无**全库静态扫描用例；③ `tests/invariants/` 内**零**编号化 INV-01 用例。 |
| **Legacy Mapping** | ← 历史 **`INV-02`（session-split，"历史必由日志派生"）并入本 ID**（§2 **L-1**）；历史 `INV-01`（"只追加"）为本 ID **第一分句**（兼容，无需迁移，§2 **L-2**）。 |
| **Status** | `migrated` |

### INV-02

| 字段 | 内容 |
|---|---|
| **ID** | `INV-02` |
| **Canonical Name** | 无绕过 agent-loop 直调 llm |
| **Canonical Definition** | 全库 `llm.chat` 的**唯一合法调用方 = agent-loop（`run_turn`）**；任何模块禁止直连模型端点、禁止绕过循环（= 禁止绕过轮数 / 预算 / 取消三闸）。 |
| **Intent** | 三闸（轮数 / 预算 / 护栏段组装）**只在循环内**强制；**绕过循环 = 绕过闸** —— 否则 workflow / 工具可"直调 llm 做小任务"，形成无闸模型调用（烧钱）且输出绕过护栏段（`SECURITY.md:208`）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"无绕过 agent-loop 直调 llm"）· `PRD-Core.md:634` · `docs/CONSTRAINTS-06-Testing.md:63` · `docs/SECURITY.md:197,208` · `docs/DIS-CORE.md:28,46,108,186` · `docs/specs/llm.py.md:4,8` · `docs/specs/agent_loop.py.md:4,12,262` · 实现 `pyharness/core/agent_loop.py:6,229` · `pyharness/core/llm.py:5,651` · `pyharness/core/subagent.py:50,799,805` · `pyharness/core/plan_mode.py:14` |
| **Existing Test Evidence** | `tests/unit/test_agent_loop.py::test_llm_chat_only_entry_point_inv02`（**全库唯一**；断言"日志派生的上下文"+"`run_turn` 之外无 `llm.chat` 路径"） |
| **Coverage Gap** | ① **仅 1 条用例**，且其中一半断言实为 `INV-01`；② 无"静态调用图 / 全库无第二 `llm.chat` 出口"的**扫描型**用例；③ `tests/invariants/` 内零编号化用例。 |
| **Legacy Mapping** | **无迁入**。历史 `INV-02 = 历史派生` **不并入本 ID**，迁出至 `INV-01`（§2 **L-1**）；其历史承载文档见 §2 **L-9**。 |
| **Status** | `migrated` |

### INV-03

| 字段 | 内容 |
|---|---|
| **ID** | `INV-03` |
| **Canonical Name** | rebuild 与缓存一致 |
| **Canonical Definition** | `rebuild_from_log` / `rebuild_from_events` 后，派生缓存与重建前**逐事件（逐行）一致**；含**增量读一致**（`events_after(last)` == 全量切片）、**编辑重放**（`edited` 覆盖目标行）、**二次 rebuild 无重复**；`history_cache` 仅当日志尾部未变时有效，任何 `append` 后整体失效。 |
| **Intent** | 派生视图**可整体丢弃重建** —— 防"审批被拒后缓存仍留'已批准'旧上下文 → 下轮基于过期事实行动"，以及"崩溃恢复后半新半旧缓存 → 模型基于错误历史决策而审计无法解释"（`SECURITY.md:210`）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"rebuild 与缓存一致"）· `PRD-Core.md:308,360` · `docs/CONSTRAINTS-06-Testing.md:64` · `docs/SECURITY.md:198,210` · `docs/EVENT-SCHEMA.md:503,526` · `docs/DIS-CORE.md:433,471,481` · 实现 `pyharness/core/session.py:70,143,338,396,473` · `pyharness/core/session_query.py:32,57,678` |
| **Existing Test Evidence** | `tests/unit/test_session.py::test_rebuild_from_log_invariant_inv03` · `::test_rebuild_derived_cache_invalidation_on_append` · `::test_events_after_incremental_gwt_s3_04` · `tests/unit/test_session_query.py::test_rebuild_full_matches_incremental` |
| **Coverage Gap** | ① 无编号化专项在 `tests/invariants/`；② **`tests/unit/test_tools_guard.py` 的 6 条误标 `INV-03` 会虚增本 ID 覆盖率**（编号修正前，"INV-03 覆盖"读数不可信）。 |
| **Legacy Mapping** | ← **无迁入**。历史 `INV-03 = guard 单调拒绝 / 无放行`（族 C）**不属本 ID**，已改判至 `INV-04`（§2 **L-3**）；`CODE-MATRIX.md:35` 的误标见 §2 **L-4**。 |
| **Status** | `migrated` |

### INV-04

| 字段 | 内容 |
|---|---|
| **ID** | `INV-04` |
| **Canonical Name** | 无 guard 事件即非法执行（**含 guard 单调拒绝 / 无 bypass**） |
| **Canonical Definition** | **(a) 执行前必有求值事实**：每个 `tool.result` / `tool.error` 之前必有**同 `call_id`** 的 `guard.evaluated`，缺失 = **非法执行**；**(b) 结构单调性**：guard 决策**恰三值** `{allow, reject, approval}`，**无 bypass 第四值**；`GuardChain` **无任何** `override` / `bypass` / `force_allow` / `set_allow` / `execute` / 移除 / 重排 / 翻回 API，且**一次 reject 不可被后续挂载翻回 allow**（只增拒绝面）。 |
| **Intent** | `guard` 是**唯一且不可绕过**的判定点。防两类失效：① 插件绕 `execute` 直调低层 API → **副作用已发生但无拦截记录**，抵赖成立（`SECURITY.md:212`）；② 存在任一放行/翻回点 → 攻击者只要找到它即可绕越其上全部检查，**安全强度 = 最松一环**（`docs/ADD.md:119`）。 |
| **Evidence Source** | **实现（7 处，同一模块）**：`pyharness/core/tools_guard.py:17`（"单调性三层结构防线(原则 3 / **INV-04**)"）· `:44` · `:622` · `:688` · `:804` · `:808` · `:815` ｜ **spec**：`docs/DIS-SEAM.md:582,640,814` ｜ **ADR**：`docs/ADD.md:21`（ADR-003）· `:33`（ADR-015）· `:117` · `ARCHITECTURE_DECISION_RECORD.md:175,181,196,199,201,207,208` ｜ **权威表**：`docs/PRD-Core.md:768,838` · `docs/CONSTRAINTS-06-Testing.md:65` · `docs/SECURITY.md:199,212` |
| **Existing Test Evidence** | `tests/unit/test_tools_guard.py::test_monotonic_api_surface_no_allow`（**已标 INV-04**）· `::test_evaluate_allow_single_evaluated_event` · 另有 6 条**同义但误标 `INV-03`**：`::test_decision_tri_value_exact_no_bypass` · `::test_guard_base_contract` · `::test_forbidden_bypass_api_attribute_error` · `::test_reject_deterministic_not_flippable` · `::test_register_after_reject_cannot_rescue` + 模块 docstring · `tests/unit/test_engine.py::test_guard_from_config_injects_schema_validator` · `::test_s23_tc_policy_rules_carry_injected_params` |
| **Coverage Gap** | ① 本 ID **名下无任何用例位于 `tests/invariants/`**；② 当前覆盖由 **6 条误标 `INV-03` 的用例"隐形"承载** ⇒ **编号修正前，本 ID 的真实覆盖无法从编号读出**；③ 无"全库凡执行必经 `tools.execute`"的静态扫描用例。 |
| **Legacy Mapping** | ← 历史 **`INV-03`（"guard 无放行 / 单调拒绝"，族 C）并入本 ID**（§2 **L-3**）；`CODE-MATRIX.md:35` 的 `INV-03 单调拒绝` 见 **L-4**。 |
| **Status** | `migrated` |
| **治理层对应** | `INV-G1`（执行前必有同 `call_id` 的 `decision.issued`；`GOVERNED_AGENT_RUNTIME_DESIGN.md:566` 明写"对齐 **INV-04**"）· `INV-G5`（治理层无执行/放行 API，对应 (b) 的治理层类比）。 |

### INV-05

| 字段 | 内容 |
|---|---|
| **ID** | `INV-05` |
| **Canonical Name** | 拒绝后零副作用 |
| **Canonical Definition** | 任意 reject（scope / guard / critical / 审批 `denied` / `timeout`）后，**Provider 调用计数 = 0** 且**无该 `call_id` 的 `tool.result`**；拒绝须**强同步**落 `guard.rejected`（`sync=True`），落盘失败即 fail-closed 上抛。 |
| **Intent** | 使"**拦了且没执行**"**可证**。防最严重失效模式：`reject` 后仍执行 → 日志说 `seq31` 拒、文件其实被删 —— **审计与真实世界分叉**，单调性的全部价值押于此（`SECURITY.md:214`）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"拒绝后零副作用"）· `PRD-Core.md:401,404,1771` · `docs/CONSTRAINTS-06-Testing.md:66` · `docs/SECURITY.md:200,214` · `docs/DIS-SEAM.md:582,650,814`（"G4=INV-05"）· 实现 `pyharness/core/tools_guard.py:13,689,721,832` · `pyharness/core/scope.py:278` · `pyharness/governance/receipt.py:188` |
| **Existing Test Evidence** | `tests/unit/test_tools_guard.py::test_reject_event_pair_order_and_sync` · `::test_scope_hidden_reject_events_grd401` · `::test_reject_deterministic_not_flippable`（亦涉 INV-04）· `tests/unit/test_tools_executor.py::test_scope_hidden_terminal_reject` · `tests/unit/test_tool_fs.py::test_executor_critical_delete_denied_zero_side_effect` · `tests/unit/test_cli.py::test_run_headless_rejected_listing` |
| **Coverage Gap** | ① 无编号化专项在 `tests/invariants/`；② "拒绝后零副作用"目前分散在 guard / executor / tool_fs / cli 四处，**无单一编号锚点**。 |
| **Legacy Mapping** | ← **部分迁入**：`CODE-MATRIX.md:35` 曾把"零副作用"标为历史 `INV-04`（§2 **L-4**）。 |
| **Status** | `migrated` |

### INV-06

| 字段 | 内容 |
|---|---|
| **ID** | `INV-06` |
| **Canonical Name** | 执行 args = 日志 args |
| **Canonical Definition** | `tool.call` **同时**存 `raw_args`（LLM 原始参数原文）与强类型 `args`（校验后实际执行）；**真实函数收到的参数 == 日志 `args` 逐字段一致**；畸形参数 → `TLB-803` 且 **Provider 零调用**、**不写 `tool.call`**。 |
| **Intent** | **LLM 零信任**：防"输出 `{"path":123}` 被实现层 `str()` 隐式转换后去删'名为 123 的文件'，而日志记 123 → **事后无法还原事故**"；并保证"模型说了什么"与"实际执行了什么"逐字段可比（`SECURITY.md:216`）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"执行 args=日志 args"）· `PRD-Core.md:414,421,972,1858` · `docs/CONSTRAINTS-06-Testing.md:67` · `docs/SECURITY.md:201,216` · `docs/ADD.md:258,264,274`（ADR-002/003 背景）· 实现 `pyharness/core/tools_executor.py:12,22,335,450,461` · `pyharness/core/tools_registry.py:313,592` · `pyharness/core/llm.py:110,129` · `pyharness/errors.py:90` |
| **Existing Test Evidence** | `tests/unit/test_tools_executor.py::test_param_wrong_type_zero_provider` · `::test_param_fail_extra_field_zero_provider` · `::test_happy_full_pipeline_event_order` · `tests/unit/test_tools_registry.py::test_malformed_args_tlb803_zero_exec` · `::test_non_identifier_property_name` · `tests/unit/test_llm.py::test_assistant_passthrough_raw_json` · `::test_multi_calls_parsed_in_order` · `tests/unit/test_spill.py::test_handle_executes_read` · `tests/unit/test_tool_fs.py::test_write_over_1mb_contract_tlb803` · `tests/unit/test_tool_web.py::test_validate_args_search_contract` |
| **Coverage Gap** | ① 无编号化专项在 `tests/invariants/`；② `PRD-Core.md:421` 要求的 **"30 例畸形参数"** 语料规模未在单一用例中体现（现分散于多个用例）。 |
| **Legacy Mapping** | ← 历史语义 **"参数非法"**（`docs/CONSTRAINTS-06-Testing.md:54` 废弃清单所列）**推定**归入本 ID —— **库中已无残留站点**，且该文档**未给出编号对位**，故为**推定而非文档明示**（§2 **L-7**）。 |
| **Status** | `established` |

### INV-07

| 字段 | 内容 |
|---|---|
| **ID** | `INV-07` |
| **Canonical Name** | 单进程 |
| **Canonical Definition** | 全库**进程数 = 1**，无 `multiprocessing` / 跨进程 RPC；唯一多进程出口 = **受 guard 的** `subprocess` / PTY；子 Agent / jobs / schedule 全为**协程级并发**，共享主会话日志与 seq 分配器；同一 `{sid}.jsonl` **单写者独占**。 |
| **Intent** | 保**单一事实源与 seq 单调**。防"子 agent 独立进程走 HTTP → ① 子进程事件无法共享主会话 seq → 两个事实源；② 主进程已收紧策略而子进程按旧策略执行危险操作 → **guard 策略不同步**"（`SECURITY.md:218`）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"单进程"）· `PRD-Core.md:435,438` · `docs/CONSTRAINTS-06-Testing.md:68` · `docs/SECURITY.md:202,218` · `docs/ADD.md:164`（ADR-012）· `docs/MAP.md:190,233` · `docs/EVENT-SCHEMA.md:630` · 实现 `pyharness/persistence.py:7,137,192,297,728` · `pyharness/core/schedule.py:8,169,717` |
| **Existing Test Evidence** | `tests/unit/test_persistence.py::test_cross_process_lock_blocks_second_writer` · `::test_replay_tolerates_crlf_leftover` |
| **Coverage Gap** | ① 无"全库无 `multiprocessing` import"的**静态扫描**用例；② 无编号化专项在 `tests/invariants/`。 |
| **Legacy Mapping** | ← 历史 **`INV-07 = 降级链`** **不并入本 ID**，**已 retired** 并移出 INV 族（§3 **R-1** / §2 **L-5**）。 |
| **Status** | `migrated` |

### INV-08

| 字段 | 内容 |
|---|---|
| **ID** | `INV-08` |
| **Canonical Name** | 阶段 import 方向 / 依赖方向 |
| **Canonical Definition** | **阶段 N 不得 import 阶段 N+1** 模块（核心脊柱严格无环）；**`governance/` 只允许依赖 `events` + `errors`**（ADR-018:308）；外围能力**只能经 `ctx` 注入**，禁止反向 import Consumer、禁止外围互引。 |
| **Intent** | 依赖单向 ⇒ 接口稳定、实现可并行、**外围可插拔**。防"阶段 3 文件工具先于阶段 1 guard 完成被**裸跑上线** → 危险能力先于防护存在"；防"外围绕过 `ctx` 直接 import 另一外围 → 卸载一个插件拖垮另一个"（`SECURITY.md:220` / `KEY-FINDINGS.md` PIT-11）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"阶段 import 方向"）· `PRD-Core.md:461,464,1920` · `docs/CONSTRAINTS-06-Testing.md:69` · `docs/SECURITY.md:203,220` · `docs/ADD.md:236,288`（ADR-010）· `ARCHITECTURE_DECISION_RECORD.md:308,323,336`（ADR-018 扩展）· `docs/MAP.md:190` · 实现 `pyharness/core/agent.py:32,81,345,350,357,459` · `pyharness/core/subagent.py:225,250,805` |
| **Existing Test Evidence** | `tests/unit/test_agent.py::test_attach_shadowing_spine_member_tlb802` · `tests/unit/test_cli.py::test_chat_pipe_full_path` · `tests/unit/test_commands.py::test_register_command_and_dispatch` · **S2-1 的 T3 AST 断言**（`governance/**` 零 `core.*` import）· **S2-3.2 的 T-A/T-B** |
| **Coverage Gap** | ① **无独立的"阶段 N 不 import N+1"静态扫描用例**（现仅治理层方向有 AST 断言）；② 无编号化专项在 `tests/invariants/`。 |
| **Legacy Mapping** | ← 历史 **`INV-08 = 循环必终止`** **不并入本 ID**，**已 retired** 并移出 INV 族（§3 **R-2** / §2 **L-6**）。 |
| **Status** | `migrated` |

### INV-09

| 字段 | 内容 |
|---|---|
| **ID** | `INV-09` |
| **Canonical Name** | 日志无凭据 / 全出口脱敏 |
| **Canonical Definition** | 事件 / 错误 / 日志 / `spill` / PTY / UI 投影**全出口**均无 **32+ 位疑似密钥原文**（`sk-` 等前缀同样打码）；secret **只存引用**（`env:NAME` / `file:PATH`），明文永不落日志。 |
| **Intent** | 凭据不落任何出口。防"**错误路径**把完整 key 打进 `tool.error` message → 日志泄露（会进版本库 / 被分享 / 被 FTS 索引）→ 账单失控"；泄露点常是**最难追查的错误分支**（`SECURITY.md:222`）。 |
| **Evidence Source** | `docs/PRD-Core.md:838`（"日志无凭据"）· `PRD-Core.md:810,1783,1848` · `docs/CONSTRAINTS-06-Testing.md:70` · `docs/SECURITY.md:204,222` · `docs/CFG.md:15,182,349` · `docs/ERR.md:53,338` · `docs/EVENT-SCHEMA.md:633` · 实现 `pyharness/config.py:149,395,707` · `pyharness/core/spill.py:34,179,296` · `pyharness/acp.py:75,148` · `pyharness/desktop/projection.py:182` |
| **Existing Test Evidence** | `tests/unit/test_config.py::test_redact_masks_secrets` · `::test_web_nonloopback_requires_token` · `tests/unit/test_acp.py::test_engine_error_detail_redacted_and_truncated` · `tests/unit/test_spill.py::test_redact_applied_before_disk` · `tests/unit/test_tool_fs.py::test_read_redact_applied` · `tests/unit/test_tool_web.py::test_fetch_redact_applied_direct_and_spill` · `tests/unit/test_desktop.py::test_render_tool_call_redacts_sensitive_args` · `tests/unit/test_schedule.py::test_register_writes_registered_event_and_job` |
| **Coverage Gap** | ① `PRD-Core.md:810` 要求的 **"INV-09 全库 grep"** 之**全库正则扫描**用例**不存在**（现仅为逐出口的定向断言）；② 无编号化专项在 `tests/invariants/`。 |
| **Legacy Mapping** | **无**（全库无冲突语义）。 |
| **Status** | `established` |

### 1.1 Canonical INV 总览

| ID | Canonical Name | Status | 覆盖缺口数 | 待迁移 |
|---|---|---|---:|---|
| INV-01 | 日志只追加 / 历史必由日志派生 | `migrated` | 3 | 8 处文本（L-1） |
| INV-02 | 无绕过 agent-loop 直调 llm | `migrated` | 3 | 同 L-1（文本仍写 INV-02） |
| INV-03 | rebuild 与缓存一致 | `migrated` | 2 | 8 处文本（L-3） |
| INV-04 | 无 guard 事件即非法执行（含单调拒绝 / 无 bypass） | `migrated` | 3 | 同 L-3（文本仍写 INV-03） |
| INV-05 | 拒绝后零副作用 | `migrated` | 2 | 1 处误标（L-4，仅加注） |
| INV-06 | 执行 args = 日志 args | `established` | 2 | — |
| INV-07 | 单进程 | `migrated` | 2 | 2 处 retired 引用（L-5） |
| INV-08 | 阶段 import 方向 / 依赖方向 | `migrated` | 2 | 2 处 retired 引用（L-6） |
| INV-09 | 日志无凭据 / 全出口脱敏 | `established` | 2 | — |

**合计**：9 条 Canonical ID，**定义数 = 9**（一 ID 一语义，P-1）；`established` 2 条 · `migrated` 7 条。

---

## 2. Legacy Mapping（历史映射，**保留历史语义，不删除**）

> 本表完整纳入 `S6-1_INV_CLASSIFICATION.md` §12.2 已裁决的 **L-1 ~ L-10**。
> `Status` 取值：`pending authorization`（待授权迁移）· `deferred`（历史载体，仅加注）· `record-only`（仅登记，不动）· `compatible`（兼容，无需迁移）

| # | Legacy Reference | Legacy Meaning | Current Location | Canonical Destination | Migration Action | Status |
|---|---|---|---|---|---|---|
| **L-1** | `INV-02`（session-split） | 历史必由日志派生 / 无第二份状态 | `pyharness/core/session.py:7,177` · `pyharness/acp.py:27` · `tests/unit/test_session.py:5,174,435` · `scripts/demo_phase1.py:5` | **`INV-01`**（第二分句） | 注释 / docstring / 文案：`INV-02` → `INV-01`（7 处） | `pending authorization` |
| **L-2** | `INV-01`（session-split） | 日志只追加 | `session.py:5,79,271,274` · `test_session.py:4,154,161,170` · `message_edit.py:3,5` · `test_compaction.py:11,451` 等 | **`INV-01`**（第一分句） | **无需迁移**（Canonical `INV-01` 为合并式，本义为其子集） | `compatible` |
| **L-3** | `INV-03`（旧 guard 编号） | guard 单调拒绝 / 无放行 / 无 bypass | `tests/unit/test_tools_guard.py:6,197,245,247,269,305` · `scripts/demo_phase1.py:61,71` | **`INV-04`**（结构单调面） | 注释 / 文案：`INV-03` → `INV-04`（8 处） | `pending authorization` |
| **L-4** | `INV-03` + `INV-04`（`CODE-MATRIX.md:35`） | "INV-03 单调拒绝 / INV-04 零副作用" | `CODE-MATRIX.md:35` | **`INV-04`** / **`INV-05`** | **加注不改数**（历史矩阵；沿用 S2-5.1 Tier C 先例） | `deferred` |
| **L-5** | `INV-07`（**retired**） | 降级链 / 降级生效（主模型 5xx/401 后走备用） | `tests/unit/test_llm_fallback.py:4,182` | **无（移出 INV 族）** → **`F013` / `F028`** 功能验收 | 去编号，改标 `F013/F028`（2 处） | `pending authorization` |
| **L-6** | `INV-08`（**retired**） | 循环必终止（`max_turns` 护栏） | `tests/unit/test_agent_loop.py:12,259`（+ 函数名 `test_max_turns_truncation_inv08`） | **无（移出 INV 族）** → **`F007`** 循环护栏 | 去编号，改标 `F007`；函数名去 `_inv08`（1 处改名，须单独授权） | `pending authorization` |
| **L-7** | 旧义 **"参数非法 / 强同步不丢 / 降级生效"** | 参数不合规拦下不执行 / 强同步事件不丢 / 降级真发生 | **库中无残留站点**（仅见于 `docs/CONSTRAINTS-06-Testing.md:54-55` 的废弃清单） | 推定：参数非法→**`INV-06`**；强同步不丢→**无对应 Canonical ID**（`F-SYNC-1` 修复 + `CONSTRAINTS-04` 承载）；降级生效→同 L-5 | **仅登记**（**推定归属，非文档明示**；该文档未给出编号对位，**不做猜测性迁移**） | `record-only` |
| **L-8** | 双写 `INV-01/02` | 同处引用两个含义 | `pyharness/core/session.py:347` · `REFACTOR_PLAN.md:146` | **`INV-01`** | 消歧：`(INV-01/02)` → `(INV-01)`；`REFACTOR_PLAN.md` 为历史产物 → **仅加注** | `session.py:347` `pending authorization` / `REFACTOR_PLAN` `deferred` |
| **L-9** | `INV-02`（session-split，**历史载体**） | 历史必由日志派生 | `docs/baseline/TEST_BASELINE.md:155` · `S6_ENTRY_PLAN.md:15` | **`INV-01`** | 前者**只加注不改数**（基线快照）；后者**不改**（冻结的冲突原始记录，**有意保留**） | `deferred` / `record-only` |
| **L-10** | 区间写法 `INV-01~09` | 全集引用（非单一语义） | 15 个文件，33 次 | `INV-01`~`INV-09`（整体） | **无需迁移** | `compatible` |

### 2.1 Legacy Mapping 统计

| 项 | 值 |
|---|---|
| Legacy 条目数 | **10**（L-1 ~ L-10） |
| Legacy **语义**类别数 | **4**（L-1 会话拆分 · L-3 旧 guard 编号 · L-5 降级链 · L-6 循环必终止） |
| 可迁移站点数 | **22 处 / 9 文件** |
| 其中 `pending authorization` | **19 处**（L-1 ×7 + L-3 ×8 + L-5 ×2 + L-6 ×2） |
| 其中 `deferred`（仅加注） | **3 处**（L-4 ×1 + L-9 ×2） |
| 其中 `compatible`（无需迁移） | L-2 · L-10（数量不计入站点数） |
| 其中 `record-only`（仅登记） | L-7 · L-9 后半部分 |
| **每条 Legacy 的 Canonical Destination** | **全部明确**：或指向唯一 Canonical ID（L-1→01 · L-3→04 · L-4→04/05 · L-7 推定→06 · L-8→01 · L-9→01）；或明确标注 **`retired` / 无对应 Canonical ID**（L-5 · L-6 · L-7 的"强同步不丢"） |

---

## 3. Retired References（已废弃，**不作为 Canonical ID**）

> 依 **P-4**：**不恢复** `INV-07` / `INV-08` 的旧语义为 Canonical ID，也**不允许**用这两个槽位承载它们。
> 但其承载的性质**确有价值**，故以**非 INV 编号**方式纳入未来测试体系。

### R-1 · `INV-07` = 降级链（**retired**）

| 项 | 内容 |
|---|---|
| **Retired Reference** | `tests/unit/test_llm_fallback.py:4`（"`INV-07` 载体"）· `:182`（"降级真的发生(`INV-07` 载体)"） |
| **历史语义** | 降级链：主模型 5xx/401 后请求走备用（`LLM-302` 直降备用；`LLM-303` exhausted） |
| **文档级自证** | `docs/CONSTRAINTS-06-Testing.md:72-74` **点名本文件**并逐字写明："它是**功能验收(F013/F028)**，**不是 INV 编号**——早期把它记作 **`INV-07`** 的做法**已废弃**。" |
| **性质是否仍有价值** | ✅ **有**。降级真发生可观测、可回归，已被 `test_llm_fallback.py::test_302_direct_degrade_happens` 断言（**用例保留，仅去编号归属**）。 |
| **非冲突纳入方式** | ① 编号用 **`F013` / `F028`**（功能验收），**不占 INV 编号**；② 事件留痕面（`degraded_from` 字段）已在 `docs/SOP.md:101` 作 F013 的硬判据；③ 若 S6-3 要纳入，作为 **F013 验收用例**进入 `tests/acceptance/`，**不新增 Canonical INV**。 |
| **禁止**（P-4） | ❌ 不得把"降级链"重新定义为某个 Canonical INV；❌ 不得把 `INV-07` 槽位改作他用（Canonical `INV-07` = 单进程）。 |

### R-2 · `INV-08` = 循环必终止（**retired**）

| 项 | 内容 |
|---|---|
| **Retired Reference** | `tests/unit/test_agent_loop.py:12`（"循环必终止(`INV-08`)"）· `:259`（"循环必终止铁律(`INV-08`)"）· 函数名 `test_max_turns_truncation_inv08` |
| **历史语义** | 任何输入在 `max_turns` 轮内结束，不存在无限工具轮 |
| **文档级自证** | `docs/CONSTRAINTS-06-Testing.md:56-58` **点名本文件**并逐字写明："'循环必终止'**不是不变量编号**，而是 **F007 循环护栏**（由 `max_turns` 用例覆盖，见 `tests/unit/test_agent_loop.py`）。" |
| **性质是否仍有价值** | ✅ **有**。循环有界是本框架"死循环烧钱"的结构性解药，已被该用例断言（**用例保留，仅去编号归属**）。 |
| **非冲突纳入方式** | ① 编号用 **`F007`**（循环护栏），**不占 INV 编号**；② 用例名去 `_inv08`（`test_max_turns_truncation`）；③ 若要提升为**不变量**，须走**新 ADR**（ADR 只增不改）并经人工裁定 —— **当前不建议、未执行**。 |
| **禁止**（P-4） | ❌ 不得为"循环必终止"新设 Canonical INV；❌ 不得用 `INV-08` 槽位承载它（Canonical `INV-08` = 阶段 import 方向）。 |

---

## 4. 边界：不属于本 Registry 的编号族

| 族 | 状态 | Canonical 来源 | 说明 |
|---|---|---|---|
| **`INV-G1`~`INV-G6`**（治理层不变量） | **本 Registry 范围外** | `docs/EVENT-SCHEMA.md:492` | 定义于冻结协议；测试面 `tests/invariants/test_inv_governance.py`。与 `INV-01~09` 的显式对应关系：`INV-G1` 对齐 `INV-04`（`GOVERNED_AGENT_RUNTIME_DESIGN.md:566`）· `INV-G5` 为 `INV-04`(b) 的治理层类比。**已知缺口：`INV-G3` 零专项用例**（属 S6-2）。 |
| **`INV-R1`~`R8` / `INV-E1`~`E4` / `INV-A1`~`A6`**（治理分族） | **本 Registry 范围外** | 同上 + `tests/invariants/test_inv_governance.py` | 现状由该文件的编号化用例承载。 |
| **`docs/架构设计.md:189-202`（§8）的 8 条无编号规矩** | **非编号族** | `docs/架构设计.md` §8 | **无 `INV-0x` token**；为 `INV-01` 的分句来源与 `INV-08` 废弃说明的历史来源，登记于本 Registry 的 L-7 / R-2 语境中，**不引入新编号**。 |
| **功能验收编号 `F0xx`** | **非不变量族** | `docs/PRD-Core.md` | `F007` / `F013` / `F028` 等承载"有价值但非 INV"的性质（见 §3）。 |

---

## 5. 待执行迁移（`pending authorization`，本 Registry **不执行**）

> 依 S6-1 裁决：**本轮不授权**修改 `pyharness/**`（含 docstring/comment）、现有测试、Event Schema、Governance Core。
> 以下清单与 `S6-1_INV_CLASSIFICATION.md` §12.4（FC-1~FC-14，涉 13 文件）**一一对应**，**待单独授权后执行**。

| # | 位置 | 内容 | 对应 Legacy |
|---|---|---|---|
| FC-1 | `pyharness/core/session.py:7` | `INV-02` → `INV-01`（模块 docstring） | L-1 |
| FC-2 | `pyharness/core/session.py:177` | `INV-02` → `INV-01`（方法 docstring） | L-1 |
| FC-3 | `pyharness/core/session.py:347` | `(INV-01/02)` → `(INV-01)`（消歧） | L-8 |
| FC-4 | `pyharness/acp.py:27` | `INV-02` → `INV-01`（模块 docstring） | L-1 |
| FC-5 | `pyharness/core/session.py:5,79,271,274` | **不改**（与合并式一致）—— 登记以免被误改 | L-2 |
| FC-6 | `tests/unit/test_session.py:5,174,435` | `INV-02` → `INV-01` | L-1 |
| FC-7 | `tests/unit/test_agent_loop.py:12,259` + 函数名 | 去 `INV-08`，改标 `F007` 护栏 | L-6 |
| FC-8 | `tests/unit/test_tools_guard.py:6,197,245,247,269,305` | `INV-03` → `INV-04` | L-3 |
| FC-9 | `tests/unit/test_llm_fallback.py:4,182` | 去 `INV-07`，改标 `F013/F028` | L-5 |
| FC-10 | `scripts/demo_phase1.py:5,61,71` | `INV-02`→`INV-01`；`INV-03`→`INV-04` | L-1 / L-3 |
| FC-11 | `tests/invariants/test_inv_{bus,config,events}.py` 头部 | 移除失实的"**当前 RED：模块未实现**" | — |
| FC-12 | `docs/specs/session.py.md:233` | 引用了不存在的 `tests/acceptance/test_f009_session_log.py` / `test_f018_invariants.py` | — |
| FC-13 | `CODE-MATRIX.md:34,35` | `:34` 计数失实；`:35` → `INV-04`/`INV-05` | L-4 |
| FC-14 | `docs/baseline/TEST_BASELINE.md:155` | `INV-02（无第二状态）` → 加注指向 `INV-01` | L-9 |

---

## 6. 变更规则

本 Registry 是 **Canonical Source of Truth**，其修改沿用仓库既有的**只增不改**纪律（参照 `docs/ADD.md` §3 对 ADR 的约束）：

1. **新增** Canonical ID 或**变更**现有定义 ⇒ **必须**经人工裁定，并在本文件追加**变更记录**（记录日期 / 变更内容 / 依据 / 裁定人），**不得**原地静默改写历史条目。
2. **Legacy Mapping 条目**（§2）与 **Retired References**（§3）**不得删除**（P-3）。
3. 任何迁移动作完成后，须回填对应条目的 `Status`（`pending authorization` → `done`）并留存验证证据（全量回归数字）。

### 6.1 变更记录

| 日期 | 变更 | 依据 | 状态 |
|---|---|---|---|
| 2026-09-15 | **建立本 Registry（v1.0）**：确立 `INV-01`~`INV-09` 的 Canonical 定义与 `Legacy Mapping`（L-1~L-10）、Retired References（R-1/R-2） | `S6-1_INV_CLASSIFICATION.md` §12（人工裁定，2026-09-15） | `established` |

---

## 7. 现状统计（与 `S6-1_INV_CLASSIFICATION.md` 一致）

| 项 | 值 |
|---|---|
| 基线 commit | `753b176` |
| 基线全量回归 | **1671 passed / 2 skipped / 0 failed**（collected 1673，exit 0） |
| 全库 `INV-0x` 出现次数（迁移前） | **812** |
| 冲突次数 | **22 → 0** |
| 语义归属闭合 | 808（`INV-01~09`）+ 4（移出 INV 族）= **812** ✅ |
| Canonical ID 数 | **9**（定义数 9，P-1 满足） |
| Legacy 条目数 | **10**（L-1 ~ L-10） |
| Retired 引用数 | **2**（R-1 `INV-07` 降级链 · R-2 `INV-08` 循环必终止） |
| 待授权迁移 | **19 处 / 9 文件** |
| 事件词表（不受本轮影响） | `EVENT_TYPES` 77 / `SYNC_TYPES` 14 / `TRANSIENT_TYPES` 3 |

---

**本文件为 S6-1 的权威编号来源。** 未修改任何 `pyharness/**` 生产代码 / 测试 / Event Schema / Governance Core；迁移动作全部 `pending authorization`。
