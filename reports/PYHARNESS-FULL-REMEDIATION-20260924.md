# PyHarness 全仓修复、验证与本地交付报告（2026-09-24）

仓库：`D:\zinuotiger-workspace\repositories\pyharness`。分支 `main`，起始与最终 HEAD 均 `41d74db15f47bee5813976bd11435dff1be166ef`。HEAD未改变，包含已有未提交修改；完整被测代码由 `225842d909580b13e2f5a88972c1fda0affcdd536772aa3aee78c985affc4ad8` 及逐文件清单固定，不能仅以HEAD代表本轮版本。

## 1. 总体结论与边界

完整离线测试 **2352 passed、0 failed、19 skipped**，收集 2371，5 warnings，JUnit总耗时 136.225s，退出码0。覆盖率 **83.41% ≥ 75%**。固定R1真实MCP专测另行 **16 passed**，退出码0。不能把两组执行次数相加称为不重复测试数。

真实组件：Agent运行时、授权/审批、队列、JSONL、SQLite、Windows进程、HTTP、MCP及evleven R1。模型规划和响应使用明确的确定性替身/回环合成响应。没有真实模型能力或外部API费用验收；没有访问其他项目或正式用户数据。

## 2. 成果保护与初始状态

安全快照：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924`。修改前435文件（414 tracked、21 untracked）逐一SHA256核验，snapshot-verification.json为ok=true；binary diff、remote、最近10次提交、协议和用户请求原文保留。排除.git、环境、缓存、真实数据与大型历史产物。原有报告字节未改变；旧候选包不会自动包含本轮修复。

```text
M .github/workflows/ci.yml
 M .gitignore
 M pyharness/config.py
 M pyharness/core/mcp.py
 M pyharness/core/session.py
 M pyharness/core/task_queue.py
 M pyharness/core/tools_executor.py
 M pyharness/engine.py
 M pyharness/persistence.py
 M tests/unit/test_events.py
 M tests/unit/test_mcp.py
?? STATUS.md
?? reports/
?? scripts/build_local_candidate.py
?? scripts/evleven_integration.py
?? scripts/evleven_r1_server.py
?? scripts/local_candidate/
?? scripts/setup_evleven_r1.ps1
?? scripts/verification_local.py
?? scripts/verify_local_candidate.py
?? tests/integration/test_evleven_r1.py
?? tests/integration/test_verification_gaps.py
?? tests/integration/test_verification_runtime.py
```

初始diff统计：
```text
.github/workflows/ci.yml         |   2 +-
 .gitignore                       |   3 ++
 pyharness/config.py              |   2 +
 pyharness/core/mcp.py            | 103 ++++++++++++++++++++++++---------------
 pyharness/core/session.py        |   5 ++
 pyharness/core/task_queue.py     |  44 ++++++++++++++---
 pyharness/core/tools_executor.py |   7 +--
 pyharness/engine.py              |   5 +-
 pyharness/persistence.py         |   8 ++-
 tests/unit/test_events.py        |   5 +-
 tests/unit/test_mcp.py           |  22 +++++++++
 11 files changed, 152 insertions(+), 54 deletions(-)
```

## 3. 子代理与独立审查

设计阶段实际启动3个并行只读子代理：audit_security_delivery（F01/F13/R02）、audit_correctness（存储/锁/初始化）、audit_architecture（运行时/取消/进程）。第4槽位不可用，主代理承担安装/网络/测试/文档角色，未虚构4个同时运行。设计完成后由主代理实施。最终另外启动2个独立只读reviewer；代码reviewer再委派1个凭据/LLM交叉复核。主代理直接启动总计5个，含子reviewer总计6个实际子代理实例。均已完成；没有子代理写入源码。

最终独立审查发现及处理详见 `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\review-results\independent-review-final.json`。初轮1 High+3 Medium全部动态复现（6失败参数），随后原生生命周期/跨租户清理2项、真实native通道1项、索引初始化及清理期间取消2项也均RED→GREEN；每次有效更正后相关回归及最终全量再执行。最后只读差异复核无新增有效中高问题；末次5个测试文件的清理/Unicode驱动更正也经独立只读复核，未改变断言契约或隐藏失败。早期“重复start_job”假说因真实入口先queue_for被撤回，采用实际list_jobs缓存污染完整复现。

## 4. 问题闭环矩阵

|编号|问题|最终状态|
|---|---|---|
|F01|租户宿主秘密边界|confirmed-fixed|
|F02|JSONL部分提交唯一性|confirmed-fixed|
|F03|锁租约一次性释放|confirmed-fixed|
|F04|关闭顺序与写入者终态|confirmed-fixed|
|F05|Job取消传播真实队列|confirmed-fixed|
|F06|一次性exec进程树回收|confirmed-fixed|
|F07|ACP多会话所有权|confirmed-fixed|
|F08|会话计量和连接隔离|confirmed-fixed|
|F09|非零退出不报告工具成功|confirmed-fixed|
|F10|初始化失败资源回滚|confirmed-fixed|
|F11|native最小安装和实际入口|confirmed-fixed|
|F12|IPv6回环接线|confirmed-fixed|
|F13|凭据更新四态|confirmed-fixed|
|F14|E2E replay和测试资源所有权|confirmed-fixed|
|F15|工具取消句柄与同步池生命周期|confirmed-fixed|
|R01|PID复用和PTY危险路径|safely-constrained|
|R02|POSIX秘密文件创建时权限|environment-blocked|
|R03|默认测试隔离|confirmed-fixed|
|R04|HTTP transport应用所有权|confirmed-fixed|
|G01|文档与交付事实一致|confirmed-fixed|
|G02|授权调用点门禁|confirmed-fixed|

### F01 — 租户宿主秘密边界

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\tenant_settings.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\llm.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\application\service.py`
- 根因：普通租户可自选宿主引用与端点；拒绝前缓存宿主spine亦可旁路。
- 修复：普通租户拒env/file；自有secret绑定租户/端点/版本；初始化先授权；所有fallback一并检查。
- 文件：`pyharness/core/tenant_settings.py`、`pyharness/core/llm.py`、`pyharness/application/service.py`、`tests/unit/test_remediation_credentials.py`。

- 修复前证据：`tests.unit.test_remediation_credentials::test_F01_tenant_cannot_select_host_secret[api_key_ref-env:SYNTHETIC_HOST]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`；`tests.unit.test_remediation_credentials::test_F01_tenant_cannot_select_host_secret[api_key_ref-file:synthetic-host]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`；`tests.unit.test_remediation_credentials::test_F01_tenant_cannot_select_host_secret[api_key-env:SYNTHETIC_HOST]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`；`tests.unit.test_remediation_credentials::test_F01_old_unsafe_profile_denied_before_secret_read` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`；`tests.unit.test_remediation_credentials::test_F01_unconfigured_tenant_cannot_inherit_host_model` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-164714-482685-adjacent-boundaries-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_credentials::test_F01_unconfigured_tenant_cannot_inherit_host_model`、`tests.unit.test_remediation_credentials::test_F01_tenant_cannot_select_host_secret[api_key_ref-env:SYNTHETIC_HOST]`、`tests.unit.test_remediation_credentials::test_F01_tenant_cannot_select_host_secret[api_key_ref-file:synthetic-host]`、`tests.unit.test_remediation_credentials::test_F01_tenant_cannot_select_host_secret[api_key-env:SYNTHETIC_HOST]`、`tests.unit.test_remediation_credentials::test_F01_old_unsafe_profile_denied_before_secret_read`、`tests.unit.test_remediation_credentials::test_F01_HTTP_tenant_boundary_and_F13_cached_binding`、`tests.unit.test_remediation_credentials::test_rejected_public_spine_cannot_cache_host_credentials`、`tests.unit.test_remediation_credentials::test_borrowed_primary_does_not_authorize_host_fallback`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_credentials.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F02 — JSONL部分提交唯一性

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\persistence.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\repair.py`
- 根因：已提交前缀被整批重试；缺换行尾与扫描缓存判断不一致。
- 修复：物理完整行对账，仅补未提交后缀；同seq异内容拒绝；补换行后重扫描；保留损坏字节。
- 文件：`pyharness/persistence.py`、`pyharness/repair.py`、`tests/unit/test_remediation_storage.py`。

- 修复前证据：`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[write-1]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`；`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[write-2]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`；`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[partial-1]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`；`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[partial-2]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`；`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[flush-before-0]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_properties::test_property_partial_retry_exact_suffix`、`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[write-0]`、`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[write-1]`、`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[write-2]`、`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[partial-0]`、`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[partial-1]`、`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[partial-2]`、`tests.unit.test_remediation_storage::test_F02_retry_preserves_exact_physical_events[newline-0]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_storage.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F03 — 锁租约一次性释放

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\persistence.py`
- 根因：重复close会重复扣同路径共享引用。
- 修复：锁路径租约置None只消费一次；关闭互斥；故障后独立关闭句柄与释放租约。
- 文件：`pyharness/persistence.py`、`tests/unit/test_remediation_storage.py`。

- 修复前证据：`tests.unit.test_remediation_storage::test_F03_close_consumes_only_own_lease` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_properties::test_property_close_owner_once`、`tests.unit.test_remediation_properties::test_windows_real_lock_survives_duplicate_close`、`tests.unit.test_remediation_storage::test_F03_close_consumes_only_own_lease`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_storage.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F04 — 关闭顺序与写入者终态

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\application\service.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\application\registry.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\engine.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop_native\app.py`
- 根因：先关持久化再停写入者；半初始化/中断跳过外壳清理；首租户错误阻断余项。
- 修复：停止准入，等任务/Job/子agent、ticker，再关MCP/索引/存储/模型；幂等shield owner；逐项聚合错误。
- 文件：`pyharness/application/service.py`、`pyharness/application/registry.py`、`pyharness/engine.py`、`pyharness/desktop_native/app.py`、`tests/unit/test_remediation_lifecycle.py`。

- 修复前证据：`tests.unit.test_remediation_lifecycle::test_F04_service_stops_writers_before_store_close` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-161003-426294-lifecycle-red`；`tests.unit.test_remediation_lifecycle::test_F04_shutdown_waits_for_admitted_initialization` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-162353-855955-lifecycle-review-red`；`tests.unit.test_remediation_lifecycle::test_F04_two_shutdown_waiters_wait_for_writer_finally` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-162353-855955-lifecycle-review-red`；`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[config]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-171356-682783-native-lifecycle-review-red`；`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[controller]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-171356-682783-native-lifecycle-review-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.integration.test_shell_service_contract::test_native_main_window_only_calls_existing_controller_methods`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[config]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[controller]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[window]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[show]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[interrupt]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[run]`、`tests.unit.test_remediation_lifecycle::test_F04_service_stops_writers_before_store_close`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_lifecycle.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F05 — Job取消传播真实队列

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\jobs.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\orchestration.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\task_queue.py`
- 根因：只取消等待包装任务；取消审计失败可跳过执行任务清理等待。
- 修复：记录真实queue task id；取消队列并等待实际终态；审计失败保留但不跳过清理；取消/成功竞态单终态。
- 文件：`pyharness/core/jobs.py`、`pyharness/core/orchestration.py`、`pyharness/core/task_queue.py`、`tests/unit/test_remediation_lifecycle.py`。

- 修复前证据：`tests.unit.test_remediation_lifecycle::test_F05_job_cancel_removes_real_queued_work` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-161003-426294-lifecycle-red`；`tests.unit.test_remediation_lifecycle::test_F05_job_cancel_removes_real_queued_work` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-161353-990136-lifecycle-green`；`tests.unit.test_remediation_lifecycle::test_job_cancel_waits_for_real_terminal_without_timeout` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-164355-613644-lifecycle-adjacent-red`；`tests.unit.test_remediation_lifecycle::test_task_cancel_audit_failure_still_stops_work[False]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-170349-417896-task-cancel-audit-red`；`tests.unit.test_remediation_lifecycle::test_task_cancel_audit_failure_still_stops_work[True]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-170349-417896-task-cancel-audit-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_lifecycle::test_F05_job_cancel_removes_real_queued_work`、`tests.unit.test_remediation_lifecycle::test_job_cancel_waits_for_real_terminal_without_timeout`、`tests.unit.test_remediation_lifecycle::test_task_cancel_audit_failure_still_stops_work[False]`、`tests.unit.test_remediation_lifecycle::test_task_cancel_audit_failure_still_stops_work[True]`、`tests.unit.test_remediation_lifecycle::test_job_cancel_audit_failure_waits_for_real_cleanup`、`tests.unit.test_remediation_lifecycle::test_job_completion_cancel_barrier_has_one_terminal[False]`、`tests.unit.test_remediation_lifecycle::test_job_completion_cancel_barrier_has_one_terminal[True]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_lifecycle.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F06 — 一次性exec进程树回收

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\proc.py`
- 根因：线程等待被取消并不停止真实子进程。
- 修复：Windows Job加启动门；POSIX受控session/group；等待器取消仍收尾实际process owner。
- 文件：`pyharness/core/proc.py`、`pyharness/core/process_owner.py`、`tests/unit/test_remediation_process.py`。

- 修复前证据：`tests.unit.test_remediation_process::test_F06_cancel_stops_test_owned_parent_and_child` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-160552-673847-process-red`；`tests.unit.test_remediation_process::test_F06_timeout_has_no_surviving_owned_process` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-165637-542273-review-baseline-full`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_process::test_F06_cancel_stops_test_owned_parent_and_child`、`tests.unit.test_remediation_process::test_F06_timeout_has_no_surviving_owned_process`、`tests.unit.test_remediation_process::test_process_partial_initialization_cleans_every_owned_resource[assign]`、`tests.unit.test_remediation_process::test_process_partial_initialization_cleans_every_owned_resource[second-reader]`、`tests.unit.test_remediation_process::test_exec_natural_exit_cancel_race_reaps_owned_process[False]`、`tests.unit.test_remediation_process::test_exec_natural_exit_cancel_race_reaps_owned_process[True]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_process.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F07 — ACP多会话所有权

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\acp.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\engine.py`
- 根因：切换覆盖单个ctx持有的runtime；EOF遗漏manager存储。
- 修复：按会话ctx缓存队列/spine；共享应用LLM owner；切换失败回滚；EOF关闭全部runtime及store。
- 文件：`pyharness/acp.py`、`pyharness/engine.py`、`tests/unit/test_remediation_lifecycle.py`。

- 修复前证据：`tests.unit.test_remediation_lifecycle::test_F07_acp_switch_reuses_session_runtime` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-161003-426294-lifecycle-red`；`tests.unit.test_remediation_lifecycle::test_acp_failed_attach_keeps_borrowed_store_usable` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-164355-613644-lifecycle-adjacent-red`；`tests.unit.test_remediation_lifecycle::test_acp_eof_reaps_two_running_sessions_and_stores` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-165748-864954-acp-shared-owner-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_lifecycle::test_F07_acp_switch_reuses_session_runtime`、`tests.unit.test_remediation_lifecycle::test_acp_failed_attach_keeps_borrowed_store_usable`、`tests.unit.test_remediation_lifecycle::test_acp_eof_reaps_two_running_sessions_and_stores`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_lifecycle.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F08 — 会话计量和连接隔离

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\llm.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\engine.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\application\service.py`
- 根因：共享adapter保存构造期计量；模型名不足以区别连接；Settings被内部键污染。
- 修复：按调用ctx记账；完整连接身份key；运行时自己的registry及Settings副本。
- 文件：`pyharness/core/llm.py`、`pyharness/engine.py`、`pyharness/application/service.py`、`tests/unit/test_remediation_runtime.py`。

- 修复前证据：`tests.unit.test_remediation_runtime::test_F08_counter_belongs_to_current_context` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-160047-765673-F08-R04-binding-red`；`tests.unit.test_remediation_runtime::test_F08_same_model_different_endpoint_has_distinct_adapter` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-160047-765673-F08-R04-binding-red`；`tests.unit.test_remediation_lifecycle::test_independent_services_do_not_mutate_shared_model_configuration` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-170954-454030-independent-review-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_lifecycle::test_shared_adapter_usage_and_close_are_session_scoped[amounts0]`、`tests.unit.test_remediation_lifecycle::test_shared_adapter_usage_and_close_are_session_scoped[amounts1]`、`tests.unit.test_remediation_lifecycle::test_shared_adapter_usage_and_close_are_session_scoped[amounts2]`、`tests.unit.test_remediation_lifecycle::test_independent_services_do_not_mutate_shared_model_configuration`、`tests.unit.test_remediation_lifecycle::test_independent_standalone_engines_preserve_shared_configuration`、`tests.unit.test_remediation_runtime::test_F08_counter_belongs_to_current_context`、`tests.unit.test_remediation_runtime::test_F08_same_model_different_endpoint_has_distinct_adapter`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_runtime.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F09 — 非零退出不报告工具成功

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\proc.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\tool_exec.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\tools_executor.py`
- 根因：provider返回包含失败的字典仍被执行器包装ok=True。
- 修复：显式ProviderOutcome契约；非零状态贯通tool.result，同时保留stdout/stderr和退出码。
- 文件：`pyharness/core/provider_result.py`、`pyharness/core/proc.py`、`pyharness/core/tool_exec.py`、`pyharness/core/tools_executor.py`、`tests/unit/test_remediation_process.py`。

- 修复前证据：`tests.unit.test_remediation_process::test_F09_nonzero_exec_is_not_tool_success` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-160552-673847-process-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_process::test_F09_nonzero_exec_is_not_tool_success`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_process.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F10 — 初始化失败资源回滚

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\persistence.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop\sessions.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\subagent.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\engine.py`
- 根因：锁、store、订阅、子agent或索引创建后失败，没有统一owner收尾。
- 修复：先登记owner再await；事务式回滚；保留主错误；FTS降级前关库，清理期间取消仍传播。
- 文件：`pyharness/persistence.py`、`pyharness/desktop/sessions.py`、`pyharness/core/subagent.py`、`pyharness/engine.py`、`tests/unit/test_remediation_properties.py`。

- 修复前证据：`tests.unit.test_remediation_storage::test_F10_factory_failure_releases_lease[1]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`；`tests.unit.test_remediation_storage::test_F10_factory_failure_releases_lease[2]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155131-286933-storage-red`；`tests.unit.test_remediation_lifecycle::test_subagent_bootstrap_failure_closes_owned_store` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-164355-613644-lifecycle-adjacent-red`；`tests.unit.test_remediation_lifecycle::test_subagent_bootstrap_failure_closes_owned_store` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-164412-126204-subagent-bootstrap-red`；`tests.unit.test_remediation_lifecycle::test_optional_index_activation_failure_releases_connection[RuntimeError]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-172329-469504-optional-index-cleanup-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_lifecycle::test_subagent_bootstrap_failure_closes_owned_store`、`tests.unit.test_remediation_lifecycle::test_optional_index_activation_failure_releases_connection[RuntimeError]`、`tests.unit.test_remediation_lifecycle::test_optional_index_activation_failure_releases_connection[CancelledError]`、`tests.unit.test_remediation_lifecycle::test_optional_index_cleanup_preserves_new_cancellation`、`tests.unit.test_remediation_properties::test_half_initialization_preserves_other_owner[False-created-before]`、`tests.unit.test_remediation_properties::test_half_initialization_preserves_other_owner[False-created-after]`、`tests.unit.test_remediation_properties::test_half_initialization_preserves_other_owner[False-replay]`、`tests.unit.test_remediation_properties::test_half_initialization_preserves_other_owner[False-subscribe]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_properties.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F11 — native最小安装和实际入口

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop\__init__.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop_native\app.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop_native\controller.py`
- 根因：native导入链耦合Web；控制器通道不属于既有白名单。
- 修复：延迟Web导入，公共bootstrap；native使用既有desktop人类通道，不扩大权限。
- 文件：`pyharness/desktop/__init__.py`、`pyharness/application/bootstrap.py`、`pyharness/desktop_native/app.py`、`pyharness/desktop_native/controller.py`、`tests/unit/test_remediation_delivery.py`。

- 修复前证据：`tests.unit.test_remediation_delivery::test_F11_native_import_does_not_require_web_stack` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-162907-370174-F11-import-red`；`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[config]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-171356-682783-native-lifecycle-review-red`；`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[controller]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-171356-682783-native-lifecycle-review-red`；`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[window]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-171356-682783-native-lifecycle-review-red`；`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[show]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-171356-682783-native-lifecycle-review-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.integration.test_shell_service_contract::test_native_main_window_only_calls_existing_controller_methods`、`tests.unit.test_remediation_delivery::test_F11_native_import_does_not_require_web_stack`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[config]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[controller]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[window]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[show]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[interrupt]`、`tests.unit.test_remediation_delivery::test_native_main_cleans_partial_initialization_and_interrupt[run]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_delivery.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F12 — IPv6回环接线

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop\net.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop\app.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\desktop\launcher.py`
- 根因：IPv4端口探测/裸IPv6 URL/Host校验与实际监听不一致。
- 修复：统一地址族和host；IPv6方括号；就绪要求本次server.started且同地址可连。
- 文件：`pyharness/desktop/net.py`、`pyharness/desktop/app.py`、`pyharness/desktop/launcher.py`、`tests/unit/test_remediation_delivery.py`。

- 修复前证据：`tests.unit.test_remediation_delivery::test_F12_ipv6_url` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-162832-890739-F12-red`；`tests.unit.test_remediation_delivery::test_F12_readiness_probes_configured_host` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-162832-890739-F12-red`；`tests.unit.test_remediation_delivery::test_F12_host_validation[[::1]:12345-200]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-162832-890739-F12-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_delivery::test_F12_ipv6_url`、`tests.unit.test_remediation_delivery::test_F12_readiness_probes_configured_host`、`tests.unit.test_remediation_delivery::test_F12_host_validation[127.0.0.1:12345-200]`、`tests.unit.test_remediation_delivery::test_F12_host_validation[localhost:12345-200]`、`tests.unit.test_remediation_delivery::test_F12_host_validation[[::1]:12345-200]`、`tests.unit.test_remediation_delivery::test_F12_host_validation[evil.example-400]`、`tests.unit.test_remediation_delivery::test_F12_host_validation[[::2]:12345-400]`、`tests.unit.test_remediation_delivery::test_F12_host_validation[user@localhost-400]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_delivery.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F13 — 凭据更新四态

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\tenant_settings.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\llm.py`
- 根因：direct/ref切换遗留旧secret；metadata与秘密写入不同步；旧transport复用。
- 修复：新秘密代文件+metadata提交点；keep/direct/ref/clear明确；端点变更要求选择凭据；旧绑定拒绝。
- 文件：`pyharness/core/tenant_settings.py`、`pyharness/core/llm.py`、`tests/unit/test_remediation_credentials.py`。

- 修复前证据：`tests.unit.test_remediation_credentials::test_F13_direct_to_reference_and_clear` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`；`tests.unit.test_remediation_credentials::test_F13_metadata_failure_keeps_old_secret` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`；`tests.unit.test_remediation_credentials::test_F13_endpoint_change_cannot_reuse_secret` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`；`tests.unit.test_remediation_credentials::test_F13_stale_lazy_and_cached_adapter_do_not_read_new_secret` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-160047-765673-F08-R04-binding-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_credentials::test_F13_direct_to_reference_and_clear`、`tests.unit.test_remediation_credentials::test_F13_metadata_failure_keeps_old_secret`、`tests.unit.test_remediation_credentials::test_F13_endpoint_change_cannot_reuse_secret`、`tests.unit.test_remediation_credentials::test_F13_stale_lazy_and_cached_adapter_do_not_read_new_secret`、`tests.unit.test_remediation_credentials::test_missing_committed_generation_preserves_metadata`、`tests.unit.test_remediation_credentials::test_retired_secret_cleanup_retries_all_generations`、`tests.unit.test_remediation_credentials::test_F13_all_update_states[direct]`、`tests.unit.test_remediation_credentials::test_F13_all_update_states[reference]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_credentials.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F14 — E2E replay和测试资源所有权

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\conftest.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\e2e\test_derived_state_lifecycle_e2e.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\e2e\test_persistence_failure_e2e.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\unit\test_engine.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\unit\test_wiring_fixes.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\unit\test_application_service.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\unit\test_engine_flow.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\unit\test_schedule_nl_phase2.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\unit\test_tool_schedule.py`
- 根因：replay借写入store漏close；测试清理吞错误/只释放scope；使用非生产queue掩盖泄漏。
- 修复：只读Reader回放；复用真实queue；await完整close；清理失败可见；物理记录精确断言。
- 文件：`tests/conftest.py`、`tests/e2e/test_derived_state_lifecycle_e2e.py`、`tests/e2e/test_persistence_failure_e2e.py`、`tests/unit/test_engine.py`、`tests/unit/test_wiring_fixes.py`、`tests/unit/test_application_service.py`、`tests/unit/test_engine_flow.py`、`tests/unit/test_schedule_nl_phase2.py`、`tests/unit/test_tool_schedule.py`、`tests/unit/test_remediation_runtime.py`。

- 修复前证据：`tests.unit.test_remediation_runtime::test_F14_replay_does_not_acquire_writer` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155541-275527-F14-F15-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_persistence.TestSyncDurability::test_same_seq_replay_does_not_duplicate_line`、`tests.unit.test_remediation_runtime::test_F14_replay_does_not_acquire_writer`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_runtime.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### F15 — 工具取消句柄与同步池生命周期

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\tools_executor.py`
- 根因：保存原始协程而非Task导致.cancel失效；成功同步工具线程池未关。
- 修复：持有实际Task；重复取消幂等；同步成功/失败清理pool。
- 文件：`pyharness/core/tools_executor.py`、`tests/unit/test_remediation_runtime.py`。

- 修复前证据：`tests.unit.test_remediation_runtime::test_F15_cancel_current_owns_task` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155541-275527-F14-F15-red`；`tests.unit.test_remediation_runtime::test_F15_sync_success_releases_pool` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155541-275527-F14-F15-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_runtime::test_F15_cancel_current_owns_task`、`tests.unit.test_remediation_runtime::test_F15_sync_success_releases_pool`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_runtime.py`；下节矩阵。
- 边界：同步运行中的任意工具不可保证强制停止；停止等待不等于副作用撤销。

### R01 — PID复用和PTY危险路径

- 状态：`safely-constrained`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\proc.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\pty.py`
- 根因：裸PID历史值不构成所有权；PTY缺可靠进程树owner。
- 修复：Windows Job句柄；POSIX组长在killpg前保留未回收锚；没有安全树所有权的PTY入口TLB-807拒绝。
- 文件：`pyharness/core/process_owner.py`、`pyharness/core/proc.py`、`pyharness/core/pty.py`、`tests/unit/test_remediation_process.py`。

- 修复前证据：`tests.unit.test_remediation_process::test_R01_closed_process_does_not_kill_recycled_pid` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-160552-673847-process-red`；`tests.unit.test_remediation_process::test_process_cleanup_failure_remains_retryable` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-161904-516665-process-cleanup-red`；`tests.unit.test_remediation_process::test_R01_pty_without_tree_ownership_fails_before_spawn` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-161904-516665-process-cleanup-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_process::test_process_cleanup_failure_remains_retryable`、`tests.unit.test_remediation_process::test_R01_pty_without_tree_ownership_fails_before_spawn`、`tests.unit.test_remediation_process::test_R01_closed_process_does_not_kill_recycled_pid`、`tests.unit.test_remediation_process::test_R01_posix_group_is_killed_before_leader_reaped`、`tests.unit.test_remediation_process::test_process_wait_failure_keeps_reap_retryable`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_process.py`；下节矩阵。
- 边界：PTY明确禁用；POSIX实际进程组未在Linux/WSL执行，已用受控系统调用替身校验顺序。

### R02 — POSIX秘密文件创建时权限

- 状态：`environment-blocked`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\tenant_settings.py`
- 根因：先写秘密后chmod暴露窗口；固定临时名存在竞争。
- 修复：mkstemp独占随机0600；写前fchmod/fstat验证；replace/权限故障明确失败；Windows DPAPI单列。
- 文件：`pyharness/core/tenant_settings.py`、`tests/unit/test_remediation_credentials.py`。

- 修复前证据：`tests.unit.test_remediation_credentials::test_R02_atomic_write_uses_exclusive_private_temp` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-155714-353247-credentials-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_credentials::test_R02_atomic_write_uses_exclusive_private_temp`、`tests.unit.test_remediation_credentials::test_R02_mode_is_private_before_first_byte[None]`、`tests.unit.test_remediation_credentials::test_R02_mode_is_private_before_first_byte[mode]`、`tests.unit.test_remediation_credentials::test_R02_mode_is_private_before_first_byte[replace]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_credentials.py`；下节矩阵。
- 边界：Windows已运行fake POSIX调用顺序/权限/replace故障；wsl --list --quiet只有docker-desktop，不具独立POSIX运行环境。未宣称真实umask022已通过。

### R03 — 默认测试隔离

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\conftest.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\security\conftest.py`
- 根因：测试加载默认用户配置/真实路径和隐式外部能力。
- 修复：显式最小Settings；进程白名单env+临时HOME；预收集及fixture网络闸；子进程显式marker。
- 文件：`tests/conftest.py`、`tests/support.py`、`tests/security/conftest.py`、`tests/unit/test_remediation_isolation.py`。

- 修复前证据：`tests.unit.test_remediation_isolation::test_shared_fixture_ignores_host_configuration` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-154857-925557-R03-red`；`tests.unit.test_remediation_isolation::test_default_process_boundary` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-154857-925557-R03-red`；`tests.unit.test_remediation_isolation::test_shared_fixture_ignores_host_configuration` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-154955-523257-R03-green`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_isolation::test_shared_fixture_ignores_host_configuration`、`tests.unit.test_remediation_isolation::test_offline_boundary_blocks_network_without_dns`、`tests.unit.test_remediation_isolation::test_default_process_boundary`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_isolation.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### R04 — HTTP transport应用所有权

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\llm.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\application\service.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\engine.py`
- 根因：全局registry跨app/loop持有transport；关闭会话误伤共享客户端。
- 修复：每应用LLMRuntime；借用adapter不夺所有权；spine和应用区别；close失败可重试；跨loop发送拒绝。
- 文件：`pyharness/core/llm.py`、`pyharness/application/service.py`、`pyharness/engine.py`、`tests/unit/test_remediation_lifecycle.py`。

- 修复前证据：`tests.unit.test_remediation_runtime::test_R04_standalone_spine_closes_owned_transport` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-160047-765673-F08-R04-binding-red`；`tests.unit.test_remediation_lifecycle::test_R04_failed_transport_close_can_retry` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-162353-855955-lifecycle-review-red`；`tests.unit.test_remediation_lifecycle::test_independent_services_do_not_mutate_shared_model_configuration` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-170954-454030-independent-review-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_attachment::test_png_parser_matches_independent_struct_read_on_real_asset`、`tests.unit.test_remediation_lifecycle::test_R04_failed_transport_close_can_retry`、`tests.unit.test_remediation_lifecycle::test_shared_adapter_usage_and_close_are_session_scoped[amounts0]`、`tests.unit.test_remediation_lifecycle::test_shared_adapter_usage_and_close_are_session_scoped[amounts1]`、`tests.unit.test_remediation_lifecycle::test_shared_adapter_usage_and_close_are_session_scoped[amounts2]`、`tests.unit.test_remediation_lifecycle::test_independent_services_do_not_mutate_shared_model_configuration`、`tests.unit.test_remediation_lifecycle::test_independent_standalone_engines_preserve_shared_configuration`、`tests.unit.test_remediation_properties::test_property_restart_preserves_independent_messages`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_lifecycle.py`；下节矩阵。
- 边界：仅限本报告环境、合成数据和指定边界；不代表穷尽所有输入或生产平台。

### G01 — 文档与交付事实一致

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\README.md`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\docs\DEP.md`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\docs\CFG.md`
- 根因：混入其他项目路径、旧入口/计数、自动Python固定、生产与历史边界错误。
- 修复：纠正入口/路径/默认数据/CI平台/模型替身/凭据/PTY/IPv6/退出码；独立文档复核。
- 文件：`README.md`、`docs/DEP.md`、`docs/CFG.md`、`STATUS.md`。

- 修复前证据：见任务前快照对应源码、phase-command-index与专项安装/文档对比；不虚构未运行失败测试。。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：当前文档对比/独立复核及最终安装、静态扫描证据；完整同范围回归见最终JUnit。
- 故障/竞态证据：``；下节矩阵。
- 边界：文档通过不等于真实模型、完整人工GUI或其他机器验收。

### G02 — 授权调用点门禁

- 状态：`confirmed-fixed`；最终不存在本轮已复现错误路径，或按状态明确约束/外部阻塞。
- 原始位置：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\.github\workflows\ci.yml`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\pyharness\core\tools_executor.py`；`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\tracked-files-before\tests\conftest.py`
- 根因：按文件数而非实际调用点计数，漏同文件重复。
- 修复：AST输出文件/行/列/表达式；统一授权helper保留审批后重新授权；0/1/2与简单别名测试。
- 文件：`scripts/check_authorization.py`、`.github/workflows/ci.yml`、`pyharness/core/tools_executor.py`、`tests/conftest.py`、`tests/unit/test_remediation_delivery.py`。

- 修复前证据：`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files1-1]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-163106-065069-G02-red`；`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files2-2]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-163106-065069-G02-red`；`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files3-2]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-163106-065069-G02-red`；`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files5-1]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-163106-065069-G02-red`；`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files6-1]` @ `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-163106-065069-G02-red`。更多逐次失败见JSON与phase-command-index（含命令、退出码、统计、耗时）。

- 修复后：`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files0-0]`、`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files1-1]`、`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files2-2]`、`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files3-2]`、`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files4-0]`、`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files5-1]`、`tests.unit.test_remediation_delivery::test_G02_counts_calls_and_simple_aliases[files6-1]`；完整同范围回归见最终JUnit。
- 故障/竞态证据：`tests/unit/test_remediation_delivery.py`；下节矩阵。
- 边界：静态扫描仅承诺明确的直接调用和简单本地别名，不声称解析反射或所有跨模块别名。

## 5. 故障注入、属性与并发

故障注入均在外部副本/合成数据完成。具体测试节点可从最终JUnit与 test_remediation_*.py 追溯，不将测试替身当真实平台证明。

|编号|注入点|证据|
|---|---|---|
|1–3|部分写入前缀0/1/N-1、半行/只缺换行、flush/fsync前后失败|test_remediation_storage：test_F02_retry_preserves_exact_physical_events / fsync_after_visible_batch|
|4–7|取锁后初始化、session.created前后、replay、订阅后失败|test_remediation_storage::test_F10_factory_failure_releases_lease；test_remediation_properties::test_half_initialization_preserves_other_owner|
|8|主失败与close/unlink清理同时失败|test_remediation_storage修复关闭/重写测试；properties cleanup_error参数|
|9|Job取消和完成竞态、审计失败|test_remediation_lifecycle::test_job_completion_cancel_barrier_has_one_terminal / test_job_cancel_audit_failure_waits_for_real_cleanup|
|10–13|exec取消/超时/自然退出；terminate及wait失败|test_remediation_process全模块，真实Windows进程树＋fake故障边界|
|14|HTTP transport close失败重试|test_remediation_lifecycle::test_R04_failed_transport_close_can_retry|
|15–17|秘密保存/metadata/replace/权限设置失败|test_remediation_credentials F13/R02测试；POSIX部分为fake系统调用|
|18|IPv6地址族/就绪探测失败与非本服务端口|test_remediation_delivery F12参数及真实IPv6服务|
|19|native缺Web可选依赖|sandbox/release-red/native-import.log（uvicorn缺失RED）；三种fresh wheel GREEN|
|20|ACP切换装配失败|test_remediation_lifecycle::test_acp_failed_attach_keeps_borrowed_store_usable|
|补充|FTS announce失败/取消/清理中新取消；原生配置/控制器/窗口/运行中断；跨租户close失败|test_remediation_lifecycle optional_index系列；test_remediation_delivery native_main参数|

属性测试6组，Hypothesis每组最多15例、database=None、无下载：Unicode消息保存重启顺序/seq；部分提交精确后缀；重复关闭租约；secret四态序列；ID路径约束；Unicode Settings YAML/CRLF语义往返。取消幂等、会话间隔离/计量、非零退出、副作用边界和owned资源终态以确定性事件/barrier回归补充，不冒充无限压力证明。

并发矩阵12项：同路径A/B Store＋第三真实进程争锁；A重复close；Job排队取消；Job完成/取消两种顺序；Web任务终态与关闭；ACP A→B→A/EOF；共享adapter双会话；A关闭后B继续请求；exec释放文件屏障与取消；SSE活动流/服务关闭；多个shutdown等待者。测试使用Event/受控文件就绪和有界超时，过程输出归外部目录；无无限压力循环。

SQLite资源诊断先观察到11个未主动关闭连接，包含旧test_engine不完整清理、旧索引测试未detach及运行时装配调用。由此补充真实故障复现，确认可选索引注册失败路径没有owner；不将所有警告都误归于这一个原因。已修复生产owner和测试清理，相关106项/63项诊断均0残留。后续全量诊断还定位8个测试owner遗漏，已改用完整close/shutdown、真实owner队列及重启装配，46项复验且sqlite-diagnostic.json=[]。诊断插件仅在外部，最终完整测试不使用该Connection子类。

Unicode属性驱动先遇到PyYAML裸NEL折叠，后遇到JSON surrogate-pair由YAML reader解析为两个码元的问题；这两次测试驱动失败均保留日志。最终用safe_dump(allow_unicode=False)输出规范YAML转义，保留全部字符生成范围、NEL/U+10000固定样例和严格往返相等断言；独立reviewer确认未弱化契约。

## 6. Mutation testing

采用代表性人工定向变异，未安装非声明mutation工具；每个变异在外部独立副本，单次35秒上限。M1结果/路径，M2前缀/锁，M3取消/子agent清理，M4拒绝/计量/秘密，M5Windows进程owner/ACP/MCP isError。最初11个变异10个检出（其中取消变异超时退出124），ID语法变异幸存；补充独立非法ID断言后杀死。另1个MCP isError变异被杀死。最终12个不同定向变异均检出，其中11个断言失败、1个受控超时；这是定向证据，不是全库mutation score。

证据目录：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\phase-results\mutation-20260924`、`mutation-followup-20260924`、`mutation-mcp-20260924`。命令、源码替换、退出码及JUnit保留。无宣称等价变异；最初幸存属于真实测试缺口。

## 7. 最终测试、静态检查与命令

解释器：Python 3.13.13，Windows 11 10.0.26200，Git 2.53.0.windows.1；完整依赖版本在 baseline/environment.json 和安装freeze日志。测试沿用已存在解释器，只读包环境；运行从外部source副本，PATH/PH/HOME/缓存经过子进程级隔离，未改全局环境。

完整实际pytest argv：
```text
D:\zinuotiger-workspace\repositories\pyharness\.work\verification\venv\Scripts\python.exe -B -X utf8 -m pytest -p no:cacheprovider -p audit_safety -o addopts= --basetemp D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-174349-416539-release-closure-final\pytest --junitxml D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-174349-416539-release-closure-final\junit.xml tests -q --cov=pyharness --cov-report=term --cov-report=json --cov-fail-under=75
```
工作目录：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\test-logs\20260924-174349-416539-release-closure-final\source`。外部启动器 `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\run_checks.py` 保存源码清单、命令、环境、输出、JUnit和coverage；安全插件禁止非回环网络。

可重跑的PowerShell入口（每次自动创建新证据目录）：
```powershell
& "D:\zinuotiger-workspace\repositories\pyharness\.work\verification\venv\Scripts\python.exe" -I -B -X utf8 "D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\run_checks.py" repeat-verification tests -q --cov=pyharness --cov-report=term --cov-report=json --cov-fail-under=75
```

静态结果：内存语法编译263个Python文件通过；授权AST门禁恰1调用；依赖方向/治理/不变量纳入完整pytest；git diff --check退出0（Git CRLF提示不是错误）。`python -I -B -m ruff check pyharness tests`实际退出1：No module named ruff。ruff不在声明依赖中，按用户约束未安装，故ruff检查为未执行成功；未将语法/AST替代伪称ruff通过。项目无mypy配置/声明门禁，类型检查不适用。完整日志 `D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\final-results\static-closure`。

跳过项逐项如下（没有把真实失败改成skip）：

|测试|原因|
|---|---|
|tests.integration.test_evleven_r1::test_real_handshake_allowlist_and_graceful_shutdown|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_configured_denial_survives_assembly_and_preset|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_pending_approval_never_sends_rejected_write[deny]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_pending_approval_never_sends_rejected_write[timeout]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_pending_approval_never_sends_rejected_write[cancel]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_local_and_remote_errors_are_not_success[memory_create-args0-TLB-803-False]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_local_and_remote_errors_are_not_success[not_a_tool-args1-TLB-802-False]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_local_and_remote_errors_are_not_success[memory_read-args2-TLB-805-True]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_protocol_errors_and_server_validation_remain_distinct|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_service_failures_do_not_silently_restart[missing]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_service_failures_do_not_silently_restart[bad_start]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_service_failures_do_not_silently_restart[disconnect]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_cancelled_inflight_request_retires_connection|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_real_http_fresh_process_cross_session_memory|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_lost_write_ack_is_unknown_and_reconciled_without_retry[timeout]|Provision pinned local evleven R1 wheel first|
|tests.integration.test_evleven_r1::test_lost_write_ack_is_unknown_and_reconciled_without_retry[cancel]|Provision pinned local evleven R1 wheel first|
|tests.unit.test_llm::test_cred702_loose_credential_permission_is_rejected|Windows mode 位无安全含义（ACL 尽力而为），本检查仅 POSIX 生效|
|tests.unit.test_spill.TestEnter::test_mode_600|Windows ACL 尽力而为(spec)|
|tests.unit.test_spill.TestRead::test_symlink_escape_zero_read|无符号链接权限(Windows 需管理员/开发者模式)|

其中16个R1测试因普通外部副本没有既有.work/R1环境而按原条件skip；已在专用固定产物外部布局独立运行，16 passed，37.952s，退出0。证据：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\sandbox\r1-final\delivery-final`。其余平台/显式可选skip不计通过。

## 8. 构建和独立安装

Hatchling在外部源码副本构建wheel+sdist，三种环境分别uv venv后uv pip install <wheel> / <wheel>[native] / <wheel>[desktop]，没有editable和源码PYTHONPATH。所有import均位于各自Lib/site-packages；执行cwd为 `outside source 中文`。声明依赖可能需要下载，不是完全离线/零依赖/通用便携包。版本均0.1.0，因此必须同时核对以下产物哈希。

|产物|SHA256|字节|
|---|---|---|
|`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\sandbox\release-closure\source\dist\pyharness-0.1.0-py3-none-any.whl`|`db2ef1ee025ebf0739ba6a2312595c7cf13ed900a92c80df9fb351fa5d3fcd76`|688289|
|`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\sandbox\release-closure\source\dist\pyharness-0.1.0.tar.gz`|`ae7020702c18654c61af45d41532786080d7da07d2260cc9feb319eef7c2ae74`|3477723|

运行源码/完整测试/最终构建副本的代码和测试输入清单已逐项对比；R1专测生产文件也相同。wheel内所有pyharness文件及sdist代码/测试均与最终工作树逐字节一致。最终README/STATUS及新报告是验证后的说明，不构成运行代码差异。

每种安装实际通过：import、CLI help、真实ApplicationService/Agent/工具授权、策略拒绝、审批拒绝/批准、无害文件副作用核对、两次shutdown、独立进程重启/replay、新空会话；模型是有请求上限的确定性替身。native额外实际Qt/qasync离屏窗口启动/定时停止；desktop额外真实uvicorn回环HTTP就绪/请求/停止和端口释放。未声称完整人工GUI/WebView2交互已通过。

安装命令及每步退出码：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\sandbox\release-closure\steps.json`；base/native/desktop的*-dependencies.log记录完整freeze。主产物目录：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\sandbox\release-closure\source\dist`。最小用户复核：
```powershell
& "D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\sandbox\release-closure\base-env\Scripts\pyharness.exe" --help
```
此命令已实际执行，不需要真实模型。应用真实chat仍需用户另行明确模型配置/预算，不会自动运行。

## 9. 平台、真实组件与限制

Windows原生实际验证：共享文件锁/跨进程争用、关闭重复、Job Object进程树、PID存活检查、启动部分失败、超时、中文/空格路径、UTF-8、Unicode YAML CRLF、临时文件、PowerShell命令、三种wheel安装、127.0.0.1/localhost/::1监听与释放。

WSL列表只有docker-desktop；未使用Docker内部发行版当独立POSIX环境，未安装系统服务/WSL/Docker。POSIX权限/组长保留/killpg顺序使用fake系统调用运行测试，不代表实际Linux权限或进程行为已验收。R02保留environment-blocked状态。项目没有Docker/Compose交付，Docker不适用。远程CI配置仍为Windows，未触发远程CI。

边界：同步工具不能保证强制停止；客户端取消/超时不代表远端MCP副作用已撤销，未知写入通过受控查询核对而不盲重试。PTY缺安全树owner路径明确TLB-807禁用。Windows秘密使用DPAPI，不把POSIX mode宣传成Windows ACL；同一Windows用户单信任域不扩展为多主机生产隔离。

可选FTS注册失败仍按既有契约降级并留日志；本轮修复该失败的连接泄漏，不把本地消息/记忆流程成功扩大成全部可选检索能力可用。完整测试的剩余warnings见日志，不作为忽略错误的手段。

## 10. 文档核对与未执行项

独立文档角色核对README/DEP/CFG/STATUS和当前接口。纠正其他项目路径、不存在python -m pyharness/包目录、自动Python版本选择、默认数据绝对安全承诺、IPv6回环/令牌描述、凭据来源字段、未生效秘密TTL、Ctrl-C退出130、Windows CI和历史数字。替身模型/外部evleven R1/旧候选版均明确边界。7处具体矛盾修正后再次独立复核通过。

- R02真实POSIX/umask022缺独立环境
- ruff未安装且未在项目依赖声明；未安装
- 无项目配置的mypy门禁
- 无真实模型/付费API调用
- 无人工完整Qt/WebView2交互验收
- 无Docker交付配置，未强加Docker
- 未运行GitHub远程CI或其他机器
- 同步工具取消/远端MCP超时不保证副作用撤销
- PTY主动禁用
- 最小安装需可访问依赖源或已有缓存，非完全离线通用便携包

## 11. 最终工作树与变更清单

任务前已有11个tracked修改、21个实际untracked文件均有快照，未删除/还原；本轮修改区分依据快照哈希而非HEAD差异。旧报告保持字节不变。下表仅本轮相对起始新增/改变，不把全部git diff归为本轮：

改变的已有文件：
- `.github/workflows/ci.yml`
- `README.md`
- `STATUS.md`
- `docs/CFG.md`
- `docs/DEP.md`
- `pyharness/acp.py`
- `pyharness/application/registry.py`
- `pyharness/application/service.py`
- `pyharness/cli.py`
- `pyharness/core/approval.py`
- `pyharness/core/jobs.py`
- `pyharness/core/llm.py`
- `pyharness/core/orchestration.py`
- `pyharness/core/proc.py`
- `pyharness/core/pty.py`
- `pyharness/core/schedule.py`
- `pyharness/core/subagent.py`
- `pyharness/core/task_queue.py`
- `pyharness/core/tenant_settings.py`
- `pyharness/core/tool_exec.py`
- `pyharness/core/tools_executor.py`
- `pyharness/desktop/__init__.py`
- `pyharness/desktop/app.py`
- `pyharness/desktop/launcher.py`
- `pyharness/desktop/net.py`
- `pyharness/desktop/sessions.py`
- `pyharness/desktop_native/app.py`
- `pyharness/desktop_native/controller.py`
- `pyharness/engine.py`
- `pyharness/persistence.py`
- `pyharness/repair.py`
- `tests/conftest.py`
- `tests/e2e/test_derived_state_lifecycle_e2e.py`
- `tests/e2e/test_evidence_lifecycle_e2e.py`
- `tests/e2e/test_multisession_isolation_e2e.py`
- `tests/e2e/test_persistence_failure_e2e.py`
- `tests/e2e/test_subagent_evidence_e2e.py`
- `tests/integration/test_evleven_r1.py`
- `tests/integration/test_mcp_stdio.py`
- `tests/integration/test_verification_runtime.py`
- `tests/invariants/test_inv_core.py`
- `tests/security/conftest.py`
- `tests/security/test_llm_egress.py`
- `tests/unit/test_application_service.py`
- `tests/unit/test_audit_run.py`
- `tests/unit/test_desktop.py`
- `tests/unit/test_engine.py`
- `tests/unit/test_engine_flow.py`
- `tests/unit/test_exec.py`
- `tests/unit/test_mcp.py`
- `tests/unit/test_persistence.py`
- `tests/unit/test_pty.py`
- `tests/unit/test_schedule_nl_phase2.py`
- `tests/unit/test_tenant_settings.py`
- `tests/unit/test_tool_fs.py`
- `tests/unit/test_tool_schedule.py`
- `tests/unit/test_tool_web.py`
- `tests/unit/test_tools_guard.py`
- `tests/unit/test_wiring_fixes.py`

本轮新文件：
- `pyharness/application/bootstrap.py`
- `pyharness/core/lifecycle.py`
- `pyharness/core/process_owner.py`
- `pyharness/core/provider_result.py`
- `reports/PYHARNESS-FULL-REMEDIATION-20260924.json`
- `reports/PYHARNESS-FULL-REMEDIATION-20260924.md`
- `scripts/check_authorization.py`
- `tests/__init__.py`
- `tests/support.py`
- `tests/unit/test_remediation_credentials.py`
- `tests/unit/test_remediation_delivery.py`
- `tests/unit/test_remediation_isolation.py`
- `tests/unit/test_remediation_lifecycle.py`
- `tests/unit/test_remediation_process.py`
- `tests/unit/test_remediation_properties.py`
- `tests/unit/test_remediation_runtime.py`
- `tests/unit/test_remediation_storage.py`

最终git status --short：
```text
M .github/workflows/ci.yml
 M .gitignore
 M README.md
 M docs/CFG.md
 M docs/DEP.md
 M pyharness/acp.py
 M pyharness/application/registry.py
 M pyharness/application/service.py
 M pyharness/cli.py
 M pyharness/config.py
 M pyharness/core/approval.py
 M pyharness/core/jobs.py
 M pyharness/core/llm.py
 M pyharness/core/mcp.py
 M pyharness/core/orchestration.py
 M pyharness/core/proc.py
 M pyharness/core/pty.py
 M pyharness/core/schedule.py
 M pyharness/core/session.py
 M pyharness/core/subagent.py
 M pyharness/core/task_queue.py
 M pyharness/core/tenant_settings.py
 M pyharness/core/tool_exec.py
 M pyharness/core/tools_executor.py
 M pyharness/desktop/__init__.py
 M pyharness/desktop/app.py
 M pyharness/desktop/launcher.py
 M pyharness/desktop/net.py
 M pyharness/desktop/sessions.py
 M pyharness/desktop_native/app.py
 M pyharness/desktop_native/controller.py
 M pyharness/engine.py
 M pyharness/persistence.py
 M pyharness/repair.py
 M tests/conftest.py
 M tests/e2e/test_derived_state_lifecycle_e2e.py
 M tests/e2e/test_evidence_lifecycle_e2e.py
 M tests/e2e/test_multisession_isolation_e2e.py
 M tests/e2e/test_persistence_failure_e2e.py
 M tests/e2e/test_subagent_evidence_e2e.py
 M tests/integration/test_mcp_stdio.py
 M tests/invariants/test_inv_core.py
 M tests/security/conftest.py
 M tests/security/test_llm_egress.py
 M tests/unit/test_application_service.py
 M tests/unit/test_audit_run.py
 M tests/unit/test_desktop.py
 M tests/unit/test_engine.py
 M tests/unit/test_engine_flow.py
 M tests/unit/test_events.py
 M tests/unit/test_exec.py
 M tests/unit/test_mcp.py
 M tests/unit/test_persistence.py
 M tests/unit/test_pty.py
 M tests/unit/test_schedule_nl_phase2.py
 M tests/unit/test_tenant_settings.py
 M tests/unit/test_tool_fs.py
 M tests/unit/test_tool_schedule.py
 M tests/unit/test_tool_web.py
 M tests/unit/test_tools_guard.py
 M tests/unit/test_wiring_fixes.py
?? STATUS.md
?? pyharness/application/bootstrap.py
?? pyharness/core/lifecycle.py
?? pyharness/core/process_owner.py
?? pyharness/core/provider_result.py
?? reports/
?? scripts/build_local_candidate.py
?? scripts/check_authorization.py
?? scripts/evleven_integration.py
?? scripts/evleven_r1_server.py
?? scripts/local_candidate/
?? scripts/setup_evleven_r1.ps1
?? scripts/verification_local.py
?? scripts/verify_local_candidate.py
?? tests/__init__.py
?? tests/integration/test_evleven_r1.py
?? tests/integration/test_verification_gaps.py
?? tests/integration/test_verification_runtime.py
?? tests/support.py
?? tests/unit/test_remediation_credentials.py
?? tests/unit/test_remediation_delivery.py
?? tests/unit/test_remediation_isolation.py
?? tests/unit/test_remediation_lifecycle.py
?? tests/unit/test_remediation_process.py
?? tests/unit/test_remediation_properties.py
?? tests/unit/test_remediation_runtime.py
?? tests/unit/test_remediation_storage.py
```
最终git diff --stat：
```text
.github/workflows/ci.yml                      |  23 +-
 .gitignore                                    |   3 +
 README.md                                     |  20 +-
 docs/CFG.md                                   |  39 ++--
 docs/DEP.md                                   |  84 ++++----
 pyharness/acp.py                              |  87 ++++++--
 pyharness/application/registry.py             |  15 +-
 pyharness/application/service.py              | 154 +++++++++----
 pyharness/cli.py                              |  43 ++--
 pyharness/config.py                           |   2 +
 pyharness/core/approval.py                    |  10 +-
 pyharness/core/jobs.py                        |  44 ++--
 pyharness/core/llm.py                         |  86 +++++++-
 pyharness/core/mcp.py                         | 103 +++++----
 pyharness/core/orchestration.py               | 102 ++++++---
 pyharness/core/proc.py                        | 215 ++++++++----------
 pyharness/core/pty.py                         |  21 +-
 pyharness/core/schedule.py                    |   6 +
 pyharness/core/session.py                     |   5 +
 pyharness/core/subagent.py                    |  76 ++++---
 pyharness/core/task_queue.py                  |  89 +++++---
 pyharness/core/tenant_settings.py             | 238 ++++++++++++++++----
 pyharness/core/tool_exec.py                   |   6 +-
 pyharness/core/tools_executor.py              |  34 +--
 pyharness/desktop/__init__.py                 |  22 +-
 pyharness/desktop/app.py                      |  57 ++---
 pyharness/desktop/launcher.py                 |  26 ++-
 pyharness/desktop/net.py                      |  26 +--
 pyharness/desktop/sessions.py                 |  71 +++---
 pyharness/desktop_native/app.py               |  53 +++--
 pyharness/desktop_native/controller.py        |   2 +-
 pyharness/engine.py                           | 300 ++++++++++++--------------
 pyharness/persistence.py                      | 288 +++++++++++++++++--------
 pyharness/repair.py                           |  15 +-
 tests/conftest.py                             | 104 ++++++---
 tests/e2e/test_derived_state_lifecycle_e2e.py |   3 +
 tests/e2e/test_evidence_lifecycle_e2e.py      |   7 +-
 tests/e2e/test_multisession_isolation_e2e.py  |   6 +-
 tests/e2e/test_persistence_failure_e2e.py     |  25 ++-
 tests/e2e/test_subagent_evidence_e2e.py       |  12 +-
 tests/integration/test_mcp_stdio.py           |   1 +
 tests/invariants/test_inv_core.py             |   7 +
 tests/security/conftest.py                    |   4 +-
 tests/security/test_llm_egress.py             |   2 +-
 tests/unit/test_application_service.py        |   9 +-
 tests/unit/test_audit_run.py                  |   1 +
 tests/unit/test_desktop.py                    |  10 +-
 tests/unit/test_engine.py                     |  37 ++--
 tests/unit/test_engine_flow.py                |  41 ++--
 tests/unit/test_events.py                     |   5 +-
 tests/unit/test_exec.py                       |  33 +--
 tests/unit/test_mcp.py                        |  23 ++
 tests/unit/test_persistence.py                |   7 +-
 tests/unit/test_pty.py                        |  45 ++--
 tests/unit/test_schedule_nl_phase2.py         |  84 ++++----
 tests/unit/test_tenant_settings.py            |   9 +-
 tests/unit/test_tool_fs.py                    |   4 +-
 tests/unit/test_tool_schedule.py              |  14 +-
 tests/unit/test_tool_web.py                   |   1 +
 tests/unit/test_tools_guard.py                |   3 +-
 tests/unit/test_wiring_fixes.py               |  14 +-
 61 files changed, 1813 insertions(+), 1063 deletions(-)
```

所有新增环境、测试输出、缓存、构建文件、故障注入数据均在仓库外检查点；未向Git加入这些内容。完整文件哈希及最终状态在final-results；没有执行reset/clean/restore/checkout/stash/commit/push等操作，没有改变分支或remote，没有修改其他仓库或系统配置。最终代码保持未提交、未推送。

最终独立验收：代码reviewer复核最后5个测试文件，未发现弱化断言或掩盖清理失败；文档reviewer逐项核对MD/JSON/STATUS、60阶段证据、24个安装步骤、59个改变/17个新增文件及452文件哈希，无新增事实矛盾。最终完整测试仍为2352 passed/19 skipped/5 warnings，代码未再变化。`final-results/final-integrity-check.json`确认旧报告未变、无保护文件丢失、无测试后源码漂移、无仓库内新增缓存/构建输出；`final-results/processes-after.json`为[]，没有命令行属于本轮检查点的存活Python测试进程。该进程检查不扫描或终止其他应用。
