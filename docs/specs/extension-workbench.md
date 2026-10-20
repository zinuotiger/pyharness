# 扩展观察与技能演练

> 当前状态更新（2026-10-21）：本规格对应能力已在S08–S18相应阶段及S18统一版本中验收。下文旧阶段状态为实现历史，当前边界与交付核验见[统一版本说明](../user-guide/unified-delivery.md)。S19最终交付须以新源码冻结和新CI为准。


状态：S15 实现及开发切片验证中，尚未完成冻结源码总验收。决策见 [ADR-034](../decisions/ADR-034-extension-diagnostics.md)。

连接测试、启动、停止和经典技能管理保留。新增入口不连接到模型的工具表，不执行 MCP 工具，不授予宿主执行或付费模型权限。

| 方法 | 路径（前缀 `/api/v1/extension-workbench`） | 结果 |
|---|---|---|
| GET | `/mcp/{source}/{name}` | 当前配置摘要；零进程、零工具调用 |
| POST | `/mcp/{source}/{name}/discoveries` | 明确确认当前摘要后启动原 stdio 客户端；仅握手和发现，关闭拥有的进程 |
| GET | `/discoveries/{key}` | 原命令历史观测；配置变化标记 stale，缺少完成回执为 unconfirmed，不重放 |
| POST | `/discoveries/{key}/arguments` | 原 ToolRegistry 严格验证参数；不保留或返回参数值，不调用 Provider |
| POST | `/skill-snapshots` | 从当前技能库捕获固定加载视图；不创建正式引擎或模型调用 |
| GET | `/skill-snapshots/{id}` | 原固定视图、源文件 SHA 与实际加载 SHA；不重新读取原路径 |
| POST | `/skill-snapshots/{id}/rehearsals` | 明确确认后运行独立目录中的原 SessionLog/TaskQueue/EngineRunner/GuardChain/ToolExecutor |
| GET | `/skill-rehearsals/{key}` | 原演练状态；只观察，冷启动或未知结果不自动补跑 |

所有入口仅供当前本地操作者使用。操作者账户在长操作前后重新核对原会话数据库；团队 owner、member、viewer 均不能通过此入口启动宿主进程或读取操作者快照。请求流分别限 1–64 KiB，确认必须为实际布尔 true，配置和快照摘要必须匹配。来源 `managed` 与 `runtime` 分别指原 PlatformStore 连接和原运行时配置，同名连接不混用身份。

MCP 发现使用独立原客户端，不替换正在服务的连接。仅显式 2024-11-05 标记为协议 verified；原兼容的缺失版本仍可发现，明确标记 legacy_unverified。显式错版本、分页工具表、不可编译 Schema 和超时不标为完成。工具 Schema 使用原严格编译器；高危险级和原实际执行链不变。配置/参数预览有效不等于 scope、guard、审批或预算准入。

发现元数据和同键不重放标记保存在原 PlatformStore.preferences，最多128条；固定技能视图与演练分别最多64条。上限到达明确拒绝，不静默清理旧键。Schema 上限64 KiB、64个工具、2048个遍历节点，命令键1–80个安全字符。出口私密内容拒绝是有限模式检查，不声称能够发现所有秘密；配置 argv/env 不对外展示，参数值不进入诊断持久化。

SkillManager 在读文件前复用原根目录 containment，目录链接或文件链接逃逸被拒绝，坏 UTF-8 跳过且不影响其他技能。正式平台引擎保存自己的固定索引；系统正文注入和后续 skill.load 使用同一索引。经典显式刷新保持可用，新正式任务装配读取新快照；刷新不改写已装配任务。非默认租户的用户技能目录跟随租户隔离，随包发布的公共技能仍可用。

技能快照准确保留原 load 的6000字符加载上限及截断说明，源文件 SHA 与加载视图 SHA 分开。受控演练只用固定脚本规划器驱动原执行链，不请求外部或付费模型，不评价模型质量。实际 skill.used 与下一轮输入取自原日志和真实模型接口；超过原2 KiB工具结果阈值仍走原溢出摘要/引用，不谎称全文已进入下一轮。演练另有一次真实内置禁止调用，保留原 guard 拒绝与未授权结论；产品不获取或包装 Provider，也不宣称测量了 Provider 调用次数。独立测试通过原执行器调用边界的仅观察转发验证实际零次禁止 Provider。tool.call 是审批/guard 之前的尝试事件，不能单独作为 Provider 已执行证据。

演练目录由服务端在自己平台根下派生，不接受客户端路径；每次演练另生成唯一会话SID，避免原进程登记表按SID清理时影响其他会话或租户。不更改正式文件、策略或会话。取消（含重复取消）先等原队列、写者和运行时清理完成；缺少结果时保留未确认状态。独立演练日志是原 SessionLog，不是另一份生产执行权威。历史元数据不授权重跑或回滚任何已有外部效果。
