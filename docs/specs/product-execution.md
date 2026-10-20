# 产品任务受治理执行与效果收据（S07）

运行尝试由 S06 保存。S07 将其绑定到已发布 Agent 的精确版本，经明确预览与启动进入原 Queue、AgentLoop、ToolExecutor、SandboxManager；不建第二任务数据库。保存定义、登记尝试、打开页面、刷新和查询均不启动执行。阶段验收状态见 `.ai-coding/evidence/PyHarness-continuous-progress.json`，功能规格不代替验收报告。

## HTTP 契约

前缀 `/api/v1/projects/{project_id}`，所有响应及可处理错误 `Cache-Control: no-store`。每次由原 WorkspaceRoute/Accounts 检查当前租户、当前成员角色和创建主体。owner/member 只能控制本人执行，viewer 不可通过控制查询绕权限。主体和租户不接受客户端提供。

| 方法与路径 | 命令或返回 | 边界 |
|---|---|---|
| POST /execution-previews | schema1、attempt_ref、agent_ref，返回 confirmation_digest 和实际边界 | 零持久化、零启动；使用已发布版本的服务端 sha256 |
| POST /executions | 预览选择、ex-ID、确认摘要、confirm_host_boundary、Idempotency-Key | 精确绑定、重新检查权限及模型配置；同尝试至多一个授权 |
| GET /execution-submissions/by-key?key= | committed/not_found 与原授权摘要 | 完整来源检查、严格只读；不补投 |
| GET /run-attempts/{id}/execution | 当前尝试的既有授权 | 完整来源检查；无本地线索也能发现 |
| GET /run-attempts/{id}/execution-control | control_only、精确 attempt_ref/execution_ref、原停止回执 | 最小控制投影；不读来源正文或打开 writer；项目归档/正文缺失/撤权仍可降低权限停止 |
| GET /executions/{id} | 状态、效果、结果摘要、子任务和原产物引用 | 完整来源检查、严格日志图和物理产物校验；不隐式下载或修复 |
| POST /execution-cancellations | schema1、ec-ID、精确 execution_ref、独立 Idempotency-Key | 保存停止意图，再向当前精确运行发信号；不以意图冒充已停 |
| GET /cancellation-submissions/by-key?key= | 原停止事实与 execution_stopped/terminal_observation | 当前角色及本人绑定；实际状态可为未知，不要求来源正文 |

引用锁定 ID、revision、digest、source_seq；Agent 锁定 agent_id、version、sha256。完整预览与执行观察不使用最小控制投影授权。最小查询严格核对历史控制元数据图、唯一尝试授权及当前主体；返回中没有题目、来源路径、模型配置、结果正文或凭据。原停止意图存在时返回同一回执，不另建停止命令。

## 实际执行与治理

不可变授权记录在唯一控制 SessionLog；私有 s-pa 会话的开头强事件绑定授权摘要、序号、主体和任务。普通会话写入口不能修改私有会话；旧公开会话契约保持。当前权限、原来源授权、发布定义、模型指纹、预算与截止时间在准备、模型、工具、收尾和资源释放后重核。

捕获的输入及引用资料在独立工作区物化。文本 inline 有界，较大资料按受控文件布局读取；精确原字节、BOM/CRLF 和来源版本保持。派生资料目录拒绝产品文件写工具，但 host_unrestricted 不提供操作系统、网络或内存隔离。Docker 与远程协议边界分别记录，不把协议替身称为真实远程运行。

子任务沿原 SubagentManager/Runner 进入独立私有会话与工作区，复用 Root 授权、当前主体、绝对截止时间和预算上限；跨日志 admission 绑定真实 invoker、tool call、decision、effect-start、输入锚点与子定义。父停止同步回收孩子，原顺序工具语义保留。子产物保留各自 session/task 身份，不归冒 Root。结果最多展示最近 400 个孩子并明确截断。

`execution.effect` 的 started 是真实治理通过并确认落盘后的调用边界；returned 是原 Provider 返回及结果摘要，不等于业务成功；取消、异常或不完整确认保留 unknown。未知效果不能盲目重放。文件观察在真实 Provider 前后经只读 SandboxManager.observe 取得原字节哈希；远程观察仅下载、不上传。单文件写/patch 与真实路径绑定；命令/进程观察受 8192 遍历项、250 文件、20 MiB 边界限制。观察失败、权限变化、后台仍运行均明确 unavailable，不能用空变化冒充核实。文件观察不证明事务、独占因果、全部外部效果或宿主写回。

产物引用来自原 generated 事件，核对执行/会话/任务、事件序号、原 record 摘要和物理原字节/manifest。integrity 为 verified/mismatch/unavailable，和下载许可、validation_status 分开。执行 completed 时 business_acceptance 仍 not_evaluated；独立业务验收属于 S10，合并写回属于 S11。

## 页面与恢复

项目空间 → 任务收件箱 → 执行 / 查看收据。项目级“核对执行 / 停止”接受尝试 ID，撤权后普通列表隐藏也可定位当前主体的精确控制目标。先核对，再选发布版本、预览、明确开始。宿主执行必须另行勾选边界确认。人工审批继续使用现有审批中心，无自动授权替代。

浏览器按当前主体、项目、尝试及命令类型保存原键、原命令引用和指纹，不存正文、凭据或授权判断。输入严格字段、格式和长度检查；回读须匹配完整选择与 request_digest。POST 失回执、404 未找到、4xx 或网络失败均保留未知线索，不自动换键、重发或恢复引擎。只读核对和原键原选择重试分开按钮；停止线索目标还须匹配当前服务端最小控制发现。

每次网络前后检查主体和窗口版本。关闭、换项目、换尝试或主体变化使迟到响应失效，旧操作不会解锁新窗口。读取失败清除私有正文、答案与产物显示；同主体仍可沿服务端最小控制停止。结果、文件名和 Provider 文本均只作文本展示。定时更新仅 GET；实际停止未知或仍运行均如实展示。
