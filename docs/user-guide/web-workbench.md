# Web workbench

受控浏览器的本地接入、原任务审批和恢复步骤见[受控浏览器工作台](controlled-browser.md)。配置生成不启动浏览器，动作仍经过原任务工具链。

本机管理员也可以使用固定账号密码登录原有 `default` 工作区。首次配置时，在项目环境运行
`python -m pyharness.web --data-dir <现有数据目录> --create-admin <用户名>`，按提示输入并确认至少 12 个字符的密码。
配置命令仅创建账号，不启动服务；重名会拒绝，不能覆盖或提升已有团队账号。密码以 scrypt 加盐哈希保存。
更新后重启 Web 服务，打开 `/login` 输入账号密码，团队 ID 留空，即可访问原有数据及本地管理员功能。
设置 → 账户与团队显示管理员用户名并提供退出登录。退出会撤销该次会话；会话有效期为 12 小时。
团队账号仍限于所属工作区。网页不包含默认密码，创建本机管理员的操作仅通过本机初始化入口提供。

Start the existing `pyharness-desktop` entry and use its authenticated local browser/WebView. `/` opens the light Chinese workbench; `/classic` retains the original runtime manager. Empty installations display zero counts and explicit missing model/sandbox states.

The sidebar keeps 工作台 at the top, followed by 新建会话 and the conversation list. A collapsible 置顶 section appears only when non-archived pinned conversations exist. The sidebar stays straight and flush with the edge across pages. The settings gear and current account stay together at the bottom; the workspace selector is hidden. Task, approval, Agent, knowledge, connection, sandbox, schedule, artifact and usage management remain inside 设置. The workbench shows recent conversations, task status, system health and shortcuts backed by real data. Use a conversation's “⋯” menu to pin/unpin it. Pins persist in tenant-scoped session metadata; archived conversations stay out of both active lists until restored. Existing metadata permissions still apply (local operators and team administrators can change pins). Ctrl+K focuses the top global search. Typing finds session titles, IDs and tags, including archived sessions; Enter also searches historical content. Click a result or use ArrowDown and Enter to open its conversation. Escape closes results. The sidebar has no separate search field. Long list titles use ellipses and expose their full text on hover; tables scroll within their own containers rather than widening the page. Dates accept ISO or epoch seconds and show 时间未知 for invalid input. API errors surface with retry and toast; HTTP 204 is handled without parsing JSON. SSE reconnect reloads persisted state; no second realtime transport is introduced.

Publish an Agent, create a new session, select the version, attach optional inputs and send a task. A session fixes its first version; use a new session to change execution policy. Inputs are staged under inputs/ in the session-owned workspace, copied into the private sandbox and remain separate from an arbitrary host repository. The inspector links steps, approvals, public trace and files. Archive/restore and tags are reversible session metadata.

Settings categories keep their navigation on the left and their content on the right. Account/team, team memory and workflows have refreshable routes. Creation forms, editors, previews, version differences, query evidence and connection-test results open in the same right pane. Use the return button to restore the previous view, including its form values. Confirmation choices, the knowledge provider selector and usage filters also stay in the pane. Switching categories cancels an unanswered confirmation; a late model probe cannot replace the new page. Chat continues to use its existing dialogs.

添加与修改模型使用同一紧凑表单：名称 / 模型名、接口地址、温度 / 最大输出 Token、密钥依次排列。常用桌面尺寸下完整显示；全部字段始终展开，校验错误聚焦对应输入；较小窗口采用自然文档流，不隐藏导航或字段。单独保存返回列表且不调用模型；“保存并测试连接”先保存，再由用户确认一次最小生成测试。测试结果持续显示，失败或取消不撤销配置，重试使用已有连接 ID。修改接口地址需要更换或明确清除密钥；保存成功后输入框清空，后续测试使用服务端凭据。应用登录失效与模型认证失败分别提示。

Schedules create a dedicated version-bound session; interval values are seconds. Enable/disable, edit, run now and delete use the existing Schedule engine. They run only while the application is open; event history persists. Usage filters accept from/to timezone-aware ISO, session_id, agent_id, run_id, model and tool. Unknown costs/latencies remain unknown.

For the code-change exercise, upload the two files in examples/platform-code-change to a new session; choose the published code template. Ask it to fix inputs/add.py and run `python inputs/check.py`. Review file/command approvals, inspect the generated patch and test report, then request apply/export and approve separately. Real execution requires a safely configured model and available Docker image. Automated acceptance uses deterministic model transport and a fake backend; neither is a real model/container claim.


聊天窗口采用紧凑布局：顶部显示会话标题和单一运行状态，运行详情在右侧按需展开；目标、后台任务、审批中心和产物入口位于顶部“•••”菜单。正文居中显示，助手回复支持安全的标题、列表、粗体、行内代码和代码块排版。用户消息的编辑、重发和反馈操作在悬浮或键盘聚焦时显示，触屏设备直接显示。

输入框随文字自动增高，常用操作在底部：＋附件、助手、权限、当前模型、•••输入选项和发送。点击模型可查看模型配置及环境检查详情；视觉图片开关和清除待发送附件位于输入选项菜单。窄窗口将模型和发送操作换到下一行。工具执行记录、历史审批与产物默认折叠，待审批卡片和任务失败原因继续直接显示。返回聊天、刷新页面和切换会话时沿用原有草稿及附件归属；实时更新时，正在向上阅读的用户不会被强制跳回底部。

Settings now use a flat shell with a thin navigation divider. All 14 categories remain expanded; the settings-local workbench and classic-manager footer links are removed (their other entry points remain). Model and schedule creation forms use compact fields without enclosing cards. The workbench 创建计划任务 shortcut and the settings list share `#schedule-new`, which restores the creation form on refresh. Schedule failures retain the draft with persistent feedback, and pending creation blocks duplicate submission. Desktop layout acceptance measures the complete shell at 1366×768, 1440×900 and 1920×1080, including validation and connection-test feedback; smaller windows keep all navigation and fields accessible in natural document flow.

工作台采用紧凑概览：最近会话和任务状态各展示最近 2 条，操作日志展示最近 1 条，完整记录通过原有“查看全部”入口访问。快捷操作收成一行，系统状态、知识库、模型配置及日志在桌面底部并排显示。短高度桌面进一步减少卡片留白；不隐藏滚动条、不裁切卡片，较窄窗口仍采用自然文档流。

## 交付中心

在项目空间打开“交付中心”，选一次提交时已附交付要求的生产执行，读取实际产物，并为每个必交项目明确选择输出后冻结交付包。不能事后换一份较低标准的清单为旧执行补验收。填写承接账户用户名与职责，承接执行还需选择固定发布的Agent版本；明确最小说明和输出子集后交接。接收者在自己的账户中签收，按原标准逐项选择通过、不通过或尚无法确认；生产者不能自批。

文件审阅需要生产者另行授予审阅导出；验收通过不自动生成下载权限。交付导出还需单独选择有效独立验收记录。原执行详情同步显示当前业务验收结论；后续包、标准、字节或授权变化可能使原结论失效。输入资料/公开文本均按引用数据处理，不赋予额外工具或发布权限。

如需继续加工，接收者将已授予的输出固定为承接资料；PDF先预览再导入，按真实物理页引用。再到原任务收件箱明确选择资料、固定承接Agent并预览/启动。保存交接或资料不自动执行；子任务完整额度先从父剩余额度预留，改键或完成不会自动退还该上限。未知回执用原提交键核对，页面不会自动重发。当前承接执行的通用续做入口明确阻断，需独立预算准入；普通旧S08续做保留。


## 合并与安全写回（S11，当前阶段验收中）

在项目空间打开“合并与安全写回”。owner先明确目标绝对目录、接收账号、相对文件路径、允许操作和到期时间；这一步不写目标文件。选择自己的固定交付包及已独立通过的验收，读取具体来源，逐个选中目标并映射输入基线与产物，再只读预览。冲突或验证失败会阻塞保存和应用。保存固定方案后核对差异、范围和完整摘要，再明确申请应用。结果显示完成、部分、失败或未知；未知先按原键核对，不重复申请。下载合并结果另行授权，不自动写回。

当前应用支持Windows独占handle；其他系统仅预览/冻结/明确导出。目标父目录须已存在，保留UTF8/BOM/CRLF；不运行交付代码。团队实际工具验证走隔离Docker，不放开宿主执行。范围和限制见[契约](../specs/three-way-writeback.md)。

## 版本实验与证据（S14 已验收）

在项目空间打开“版本实验与证据”。读取已保存数据集，明确选择本批1–10例和两个不同的发布版本，预览并确认保存固定任务；此时还没有执行。定义限额与本次预算分别填写，再预览实际分配和边界，确认本次预算后开始运行。状态刷新只观察；提交结果未知时，窗口会展开原键查询，不自动重发。已登记但尚未派发的运行需要另行核对并确认恢复。

报告分别显示实际执行、有限断言和独立业务验收，尚未记录终态、缺少证据、取消和未知费用均如实标注。先预览并明确保存固定报告，下载再单独确认；测试通过不自动发布。资料不可读或项目归档后，仍可从最小运行目录选择确切目标并请求停止。停止回执未确认不等于停止失败，也不等于记录已提交，须核对原键或最小控制状态。实现和限制见[契约](../specs/experiment-comparisons.md)。

## MCP 与技能调试（S15，验收中）

在“工具与连接”中，受管 MCP 和运行时 MCP 分别有“调试发现”。先查看零调用配置预览，再勾选启动当前宿主进程的确认，点击“明确发现”。结果显示协商版本、Schema 和进程清理情况；发现结果是历史观测，不能代表当前仍连通。参数 JSON 预览只验证结构，不执行工具；实际工具仍须经原任务、审批和执行收据。结果未知时查询原命令键，页面不会自动重发。

Skill 中点击“固定快照与演练”，先保存当前库的实际加载视图。源文件之后变化不会替换这个快照。明确确认后，独立临时会话以固定脚本模型验证实际技能使用、下一轮上下文和禁止调用拒绝；不调用付费模型，也不证明模型质量。长正文仍可能只把原摘要和溢出引用带入上下文，页面会明确说明。正式平台任务保留装配时技能；经典显式刷新供经典会话和新装配使用。所有新调试入口仅向本地操作者开放。范围和边界见[契约](../specs/extension-workbench.md)。
## 配置版本入口

模型配置和 Agent 管理中新增“配置历史”，支持固定版本、差异预览、兼容恢复及原命令查询。使用步骤与限制见[配置历史与兼容恢复](configuration-history.md)。原模型编辑、密钥轮换、Agent 草稿和版本管理入口继续可用。
