# 原有能力兼容核对

基线保留 30 项 SHELL_CAPABILITIES，未删除经典界面、CLI 或 native 外壳。Platform 默认转为聊天，管理入口进入设置；未在新首页逐项重做的管理能力通过经典界面继续提供。团队账户与可信本地操作者权限不同，不能把限制团队账户解释为删除原操作者能力。

| 原能力 | 现有正式实现 | 本轮差异 / 验证入口 |
|---|---|---|
| `session.list` | `list_sessions` | 保留；ApplicationService / 壳层契约回归 |
| `session.create` | `create_session` | 保留；ApplicationService / 壳层契约回归 |
| `chat.send` | `create_message` | 保留；ApplicationService / 壳层契约回归 |
| `chat.stream` | `session_messages / EventStreamHub` | 保留；ApplicationService / 壳层契约回归 |
| `chat.edit_last_user` | `edit_last_user` | 保留；ApplicationService / 壳层契约回归 |
| `chat.resend_last_user` | `resend_last_user` | 保留；ApplicationService / 壳层契约回归 |
| `chat.feedback_last_agent` | `feedback_last_agent` | 保留；ApplicationService / 壳层契约回归 |
| `chat.attachment` | `upload_attachment` | 保留；ApplicationService / 壳层契约回归 |
| `approval.decide` | `decide_approval` | 保留；ApplicationService / 壳层契约回归 |
| `ask.answer` | `answer_ask` | 空答复不消费问题；新增 Platform 答复入口；test_user_ask.py |
| `jobs.list` | `list_jobs` | 保留；ApplicationService / 壳层契约回归 |
| `jobs.start` | `start_job` | 保留；ApplicationService / 壳层契约回归 |
| `jobs.cancel` | `cancel_job` | 保留；ApplicationService / 壳层契约回归 |
| `schedule.list` | `list_schedules` | 保留；ApplicationService / 壳层契约回归 |
| `schedule.action` | `schedule_action` | 保留；ApplicationService / 壳层契约回归 |
| `subagent.list` | `list_subagents` | 补失败传播、并发审批、持久化历史和 Platform 委派；test_subagent_platform_contract.py |
| `subagent.spawn` | `spawn_subagent` | 补失败传播、并发审批、持久化历史和 Platform 委派；test_subagent_platform_contract.py |
| `subagent.cancel` | `cancel_subagent` | 补失败传播、并发审批、持久化历史和 Platform 委派；test_subagent_platform_contract.py |
| `skill.list` | `list_skills` | 保留；ApplicationService / 壳层契约回归 |
| `skill.registry_search` | `search_skills` | 保留；ApplicationService / 壳层契约回归 |
| `skill.install` | `install_skill` | 保留；ApplicationService / 壳层契约回归 |
| `skill.rollback` | `rollback_skill` | 保留；ApplicationService / 壳层契约回归 |
| `skill.remove` | `remove_skill` | 保留；ApplicationService / 壳层契约回归 |
| `plugin.list` | `list_plugins` | 保留；ApplicationService / 壳层契约回归 |
| `plugin.action` | `plugin_action` | 保留；ApplicationService / 壳层契约回归 |
| `preset.set` | `set_preset` | 保留；ApplicationService / 壳层契约回归 |
| `workflow.run` | `run_workflow` | 原同步入口保留，后台工作流复用相同 Runner/Queue；test_platform_workflows.py |
| `telemetry.read` | `telemetry_report` | 保留；ApplicationService / 壳层契约回归 |
| `tenant.switch` | `tenant-scoped service registry` | 保留；ApplicationService / 壳层契约回归 |
| `settings.model_profiles` | `save_model_profile / activate_model_profile / delete_model_profile` | 保留 profile/Secret 生命周期；连接测试改最小生成；test_platform_model_probe.py |

## 证据边界

- `tests/integration/test_shell_service_contract.py` 检查 Web/native 服务调用的真实方法；不等同于逐项真实设备 GUI 人工验收。
- Skill、Registry、Plugin、KV、Schedule、真实 stdio MCP、壳层契约合计本轮 64 passed。MCP 使用受控本地 echo 进程，未连接第三方 MCP。
- 原知识库与 Artifact/版本/Trace 边界由 Platform 服务、安全和完整回归覆盖；本地关键词检索不能描述为已通过外部语义 RAG。
- 真实模型、真实 Docker 和 PTY 交互不在本轮通过声明中。
