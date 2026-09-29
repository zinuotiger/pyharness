> 历史实施报告。用户实测后的最新修复与验证以 [聊天修复与十九项复审](PYHARNESS-CHAT-REPAIR.md) 为准；本文旧入口和统计不代表当前候选。

# PyHarness Web 十九项本地开发候选交付

日期：2026-09-28。基线：`42c0307866696aa2ea7915b9defa051d45ac64d6`，分支 `feat/pyharness-platform-web-v1`。本报告对应基线之后的当前本地实现；没有把旧提交远程 CI 当成本轮验证。本轮未提交或上传修改，精确文件 SHA256 清单保存在仓库外交付证据中。

## 范围与结果

完整逐项状态、文件、证据和限制见根目录 [实施矩阵](../PYHARNESS-IMPLEMENTATION-MATRIX.md)。十九项保持用户批准的原编号，SaaS 应用属于本轮实施，Docker 明确延期。

最新状态为 14 completed、4 partial、1 deferred，无 blocked。恢复入口为根目录 `CURRENT-IMPLEMENTATION-STATUS.md`。partial 分别保留真实模型人工门禁、外部语义 RAG、第三方 MCP 环境和远程交付决定；本地 SaaS 的账户、团队、角色、凭据与资源隔离没有延期。PTY 的 completed 仅表示评估完成。

主要完成：子任务错误传播和父子历史；并发子任务审批身份隔离；聊天默认入口与左下设置；Enter/Shift+Enter/中文输入法与失败草稿恢复；有预算且显式确认的模型生成测试；反问答复；账户、团队、角色、归属、模型凭据和配额边界；显式团队记忆；后台幂等工作流；独立常驻 Web 入口和停服恢复。

复用原 ApplicationService、SessionLog、TaskQueue、ApprovalProvider、WorkflowRunner、SessionKV、LLMClient/Adapter/Transport。未建立第二套模型、审批、任务或记忆系统。经典界面、CLI、native、Skill/插件管理入口保留，原能力对应表见 [兼容核对](PYHARNESS-WEB-COMPATIBILITY.md)。

## 验证

| 验证 | 结果 | 证据边界 |
|---|---|---|
| 完整 pytest | 2590 passed，41 skipped，5 warnings，0 failed | Windows Python 3.13；真实 Docker 集成未启用，跳过不算通过 |
| 覆盖率 | 83.68%，超过 75% 门禁 | 最终 Python 源码回归；不等于安全形式证明 |
| 浏览器 | 12 页，25 张截图，未捕获 JavaScript 错误 0 | 已有 Chrome；1440×900、1280×900、390×844；不下载浏览器 |
| 浏览器工作流 | Queue/SSE/失败刷新、输入法和草稿、账户登录退出、团队记忆、工作流失败、失败子任务、反问答复 | 真 API/运行时；模型为确定性合成适配器；连接测试 UI 的合成响应明确标注 |
| 模型测试链 | 一次请求、64 输出 Token、60 秒上限、无工具/重试/fallback；成功、认证、协议、非预期工具响应、期限取消和服务端重复请求回归 | 真实 LLMClient/Adapter，合成 Transport；付费模型请求 0 |
| MCP | 受控本地 stdio echo 真实进程发现/调用/关闭 | 不能替代外部 evleven 或第三方 MCP 部署验收 |
| 安装 | base/native/desktop 初次独立环境安装通过，最终 wheel 重装复验；另建独立 web 环境 | 从仓库外导入 installed package，CLI、native offscreen、HTTP、隐藏 WebView 检查通过 |
| Web 生命周期 | 安装入口两次进程启动、两次正常关闭，Session/Artifact SHA256/团队记忆/账户恢复 | Run/Approval/Trace 的恢复另由确定性服务集成覆盖；此进程检查不调用模型 |
| 备份恢复 | 合成数据停服备份并恢复到同一路径，Run/Artifact/账户可读取 | 未验证跨账户 Windows DPAPI、热备、断电原子性或跨机迁移 |
| 静态门禁 | 授权 AST、治理读侧可达、Python AST、路径/私钥模式/固定演示数据及 Git diff 检查通过 | 静态模式检查，不声称完成渗透测试或镜像漏洞扫描 |

保留了失败证据：子任务把模型错误当成功、并发审批序号碰撞、禁用通知丢终态、工作流立即取消、空答复消费问题、损坏 KV 被当空数据，以及独立 Web 关闭信号绕过服务清理，均有相应修复和回归。最终浏览器账户姓名出现于侧栏和弹窗后，旧全页定位器发生严格模式冲突；改为验证具体弹窗，未删除姓名断言。

续验补充：连接超时曾被归为普通失败，现明确返回 `model_connection_timeout`；Event 控制到期测试验证取消传输、关闭客户端和拒绝第二个请求。双账户 HTTP 测试覆盖 Session、Run、Artifact、Memory、Connection 的跨租户拒绝；Knowledge 引用和删除跨重启一致；成功和失败工作流在服务重启后同键不重放。真实浏览器上下文在工作流等待中关闭，服务仍完成一次主任务。任务审计加强 Session/Run/Agent/工具/审批/用量/Artifact 关联及重启 Trace 一致性。

工作流测试曾把辅助标题生成也计为重复主任务；最终分别核对 `main_agent` 次数、唯一 Run 和重启后零新增调用，保留原失败日志，没有放宽“不能重复执行主任务”的要求。

## 验证命令与逐项证据

命令使用既有隔离验证 Python；所有缓存、日志和构建目录均在仓库外。`continued-final-report/verification-ledger.json` 逐项对应完整命令、退出码、耗时、日志和限制；原始执行记录保存在 `commands.jsonl`。

| 证据标签 | 实际验证入口 | 结果 |
|---|---|---|
| continued-full | `python -B run_checks.py continued-full -ra --cov-report=term --cov-report=json:… --cov-fail-under=75` | 2590 passed，41 skipped；83.68% |
| continued-combinations-green | `python -B run_checks.py continued-combinations-green tests/unit/test_platform_model_probe.py tests/unit/test_platform_workflows.py tests/unit/test_platform_service.py tests/unit/test_platform_accounts.py --no-cov` | 40 passed |
| continued-audit | `python -B run_checks.py continued-audit tests/unit/test_platform_service.py::test_governed_code_change_approval_patch_export_and_restart --no-cov` | 1 passed |
| continued-real-protocol | `python -B run_checks.py continued-real-protocol tests/integration/test_mcp_stdio.py tests/unit/test_platform_memory.py tests/unit/test_kv.py --no-cov` | 10 passed |
| continued-browser | `python -B scripts/platform_browser_smoke.py --output-dir <外部证据目录>` | 12 页、25 张截图、真实浏览器关闭后任务完成 |
| continued-package | 仓库外 `continued_package.py` 调用现有构建、安装与静态门禁 | base/native/desktop/web、HTTP、隐藏 WebView 通过 |
| continued-web-lifecycle | 仓库外 `continued_web_lifecycle.py` 运行已安装 Web 入口 | 两次实际启动、正常退出和持久数据恢复通过 |

## 交付和复现

启动及账户/团队/后台/备份操作见 [运行说明](../docs/platform-web-operations.md)。最终 wheel、sdist、SHA256SUMS、包安装摘要、源文件清单、测试摘要、截图索引及可分享报告放在本轮仓库外检查点。最终包逐字节核对 Python/HTML 资源与源码一致；包含登录页面与 `pyharness-web` 入口。包版本号仍为 0.1.0，仅为本地开发候选，不能与旧同名包混用；以 SHA256 为准。

远程只读核对时，功能分支仍指向基线 `42c0307`，PR #2 的 base 为 Engineering RC；PR #1、#2 均 OPEN/Draft，未合并。本轮没有 push、更新 PR、触发远程修复、tag、Release 或公开部署。原 main 工作区既有修改保留，RC 工作区保持只读。

## 集中人工验收

1. 在新隔离数据目录打开程序，确认默认聊天、会话列表、设置往返及中文输入行为。
2. 从本地操作者创建两个人工测试账户和团队，分别登录；查看团队身份、角色变更、只读限制和资源隔离。
3. 查看子任务、任务失败、审批与反问、工作流和产物的合成自动验收截图及测试记录；真实模型工具质量尚需独立有预算的人工运行。
4. 需要真实模型时，由用户自行配置安全输入框，并主动确认一次最小连接测试；本轮没有读取原 shidongai 数据或调用真实模型。
5. 检查后台入口、正常停服和重新启动。停止服务后才能备份；不要直接上传含会话/凭据/令牌的运行数据目录。

## 明确保留的限制

- Docker 延期，未验证本轮真实容器；isolated 不可用时继续 fail-closed，不回退宿主。
- PTY 仍禁用。评估和恢复条件已记录，普通 shell/exec 不是交互终端验收。
- 外部语义 RAG、第三方 MCP/插件、真实模型和操作系统输入法/真机触摸需独立人工环境验收。
- 本轮 SaaS 为真实账户与团队的本地应用边界；未公开上线，未完成 TLS 代理、外部身份提供方、强对抗多租户或多进程共享存储认证。
- 无支付、订阅、短信邮件服务；不包含跨机器 Secret 迁移或热备。
- 代码及自动检查完成不等于十九项人工验收全通过；环境延期和待验項按矩阵保留。

## P03/P04 布局复验

旧首页仍有多列导航，本次已按批准要求修正为会话侧栏与聊天区域；管理入口集中设置，运行检查器默认折叠。12 页真实 Chrome 浏览器检查通过，29 截图，未捕获错误 0。输入、附件、草稿、SSE、会话归属和 Agent 版本断言保留。正常首页与故障证据分开保存。

完整 pytest 和覆盖率数字属于此前后端回归基线，本次仅重跑受影响浏览器范围和重建安装包。最终包见独立 layout-package，原实例不覆盖。矩阵仍为 14 completed、4 partial、1 deferred，未提交或发布。
