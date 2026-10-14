# 五项增量实施验收记录

交付状态：本机可执行的实现与验收完成后先交付；真实飞书应用、微信本人扫码和明确测试对话保留为外部待验条件。没有真实账号证据时不写“已接通”。

## 基线与恢复

来源与隔离目录最初 HEAD 都是 `7b1bdeab76cff23a040c94002d2bfb33b0291a06`。588 个已跟踪/未忽略未跟踪文件逐个核对，无实质差异；136 个仅换行符不同。保存了完整源码快照、Git bundle、原未跟踪文件和 SHA256 清单。

证据根目录：`%USERPROFILE%\Documents\Codex\2026-10-13\pyharness-five-scope`。源码只修改本次隔离目录，未覆盖来源、主仓库、稳定 RC、旧聊天成果或 portfolio；未推送、发布或合并。

## 逐项验收矩阵

|编号|要求|实现与证据|状态|
|---|---|---|---|
|C01|可替换执行器|`TaskRunner`、`RunnerResult`、`TaskLifecycle`；沿用 TaskQueue|通过|
|C02|真实 Codex 接入|官方 0.162.1 app-server，真实队列/人工审批/流式回复；`codex-real-text.log`|真实通过|
|C03|实际修改与测试|`verify_codex_change.py` 强断言实际函数加法 AST、差异、测试输出标记和退出码；`changes.patch` 与 `test-report.json` 均 passed|真实通过|
|C04|会话恢复|真实 `thread/resume`，断言同一个 thread ID；`codex-real-change.log` resume 段|真实通过|
|C05|取消与资源|真实取消为 cancelled、队列失败结算；活动连接/沙盒为零、子进程已关闭|真实通过|
|C06|失败/中断/断线/重复|真实旧客户端供应商拒绝与原生沙箱失败均保留非成功；协议测试覆盖 failed/interrupted、EOF、重复 item、一次性权限裁决|通过|
|C07|审批与外部能力边界|默认动态客户端工具复用 ToolExecutor 和治理重入；继承 MCP 启动前禁用并检查；拒绝权限扩展/未知方法/原生扩权；真实日志记录 disabled_mcp_count=4|通过|
|C08|正常入口选择新版|项目 `runtime.json`，界面显示 0.162.1 和实际路径；`ui/codex-client.jpg`；没有配置 PyHarness 模型的正常 Web 设置也通过协议队列测试|通过|
|C09|可选 Codex 原生命令模式|本机 Windows elevated 沙箱 refresh 初始化失败；未关闭或绕过沙箱|环境受限；非默认模式|
|C10|预算口径|工具、时间、事件/输出和供应商已报告累计 Token 限制；金额未知、内部轮次不可控明确展示|通过，限制已说明|
|F01|官方飞书协议|加密事件、签名、Verification Token、URL challenge、应用/租户/用户/聊天验证；本地 HTTP 协议测试|通过；非真实账号|
|F02|持久去重与映射|SQLite 入箱唯一消息 ID；独立用户/聊天会话；RunRequest.request_id 在 SessionLog 中去重|通过|
|F03|提交/查询/取消/审批|普通文本与 /status、/cancel、/approvals、可选 /approve /reject；复用原平台服务|本地验证通过|
|F04|结果与重试|完成/失败/取消反馈；官方 reply API；稳定 UUID、有限指数退避、分页记录与人工反馈重试|协议/本地队列通过|
|F05|真实收发|缺少实际应用配置与指定目标对话；未向真实联系人发送消息|待用户配置后验收|
|W01|个人微信路径核验|腾讯官方维护仓库与 2.4.10 协议，非企业微信/公众号|通过|
|W02|自动接收与反馈|二维码绑定、确认/配对码、长轮询、游标、上下文令牌、私聊文本；真实平台队列配协议模拟|通过；非真实账号|
|W03|身份与去重|绑定 Bot 与允许用户校验；群聊忽略；重复消息不重复任务；令牌仅存秘密接口|通过|
|W04|断线/过期/暂停/关闭|HTTP 重连、-14 停止并要求重绑；先入箱再推进游标；背压保留游标；取消长轮询和 HTTP 资源释放|通过|
|W05|真实账号|需本人具备 ClawBot 能力并扫码；未登录、未导出聊天、未代发消息|待本人操作后验收|
|T01|事实轨迹|任务输入、执行器、状态、任务/工具/审批/模型公开事件、结果、产物与验证状态|通过|
|T02|流式与终态|沿用认证 SSE；断线重连重新读取事实；终态保留；不展示 reasoning items|通过|
|T03|任务隔离与恢复|单会话查询，显式 task ID / segment 围栏；旧任务不被新任务事件污染；重启可查|通过|
|T04|权限/分页/边界|原租户权限链；每页最多 500，界面每次 100；游标验证与前后页无丢失|通过|
|T05|界面流程|实际浏览器检查任务检查器→轨迹、设置→渠道、模型配置→Codex 客户端；保留皮肤；截图和无控制台错误检查|通过|
|M01|C1 配置职责|AgentDefinition → Settings 编译移到 `execution_config.py`|通过|
|M02|C2 生命周期|身份、证据、时限、完成和释放由 PlatformRuntime 的 TaskLifecycle 实现；engine 不再导入 application|通过|
|M03|类型与资源|关键 Spine 字段具体类型；执行器协议；服务秘密公共入口；ToolExecutor 拥有并取消 Provider Task，修复取消启动窗口的未等待协程|通过|
|M04|全项目依赖核对|`analyze_dependencies.py` 比较基线与当前；无立即导入循环；既有延迟依赖保留并解释|通过|

## 测试与可重复命令

所有真实 Codex 验收只使用临时样例和私有工作副本，不修改来源代码。验收脚本在缺少实际差异、实际测试或资源关闭证据时非零退出；模型说“完成”不等于修改测试通过。

```powershell
.venv\Scripts\python scripts/probe_codex_protocol.py
.venv\Scripts\python scripts/verify_codex_change.py
.venv\Scripts\python -m pytest -o addopts= --no-cov -q
.venv\Scripts\python scripts/analyze_dependencies.py
uv build --wheel
```

最终完整回归：**2811 passed、41 skipped、5 个既有弃用警告**，耗时 447.84 秒。证据为 `regression-complete.log` / `regression-complete.xml`。跳过项是环境/真实供应商等既有可选测试，不作为成功证据。真实 Codex 的模型消耗来自本人现有账号，费用值不可得，未伪造成本。Wheel 构建并在独立环境安装，新增协议模块与 UI 资源齐备，安装后 Web 入口和模块导入通过。

核心新增测试：`test_codex_integration.py`、`test_channels_integration.py`、`test_wechat_ilink.py`。已有任务轨迹分页/恢复测试增加三个任务生命周期事实的预期数量；事件词表同步扩展到 82 个，并更新文档锚点，没有删除既有功能检查。

## 接回方式与最小用户操作

本次没有创建提交：本机 Git 作者邮箱未配置，用户也未要求提交，因此没有猜测身份或修改全局配置。完整新增文件与修改保留在暂存区，并导出 `five-scope.patch`、完整源码快照和恢复清单。

接回时先审查本次分支与基线差异，备份来源此刻的提交及未提交文件，再在来源的新分支对补丁运行 `git apply --check`；确认后应用并审查冲突。不要整目录覆盖，不做 reset/clean/stash。补丁已针对可恢复基线独立校验；本次没有自动应用到来源。运行时路径文件不提交，接回后执行项目客户端安装脚本或在界面选择已安装的受支持客户端。

用户仅需完成两项外部条件：在界面填写真实飞书应用配置并指定允许测试的对话；在个人微信界面由本人扫码绑定并启用收发、指定允许测试对话。本次交付不会自动执行这些联系人消息发送。对应五项范围与恢复目标仍记录在本文件及实施台账中。
