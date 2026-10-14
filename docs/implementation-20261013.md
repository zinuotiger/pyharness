# PyHarness 五项实施与验收台账

本次隔离分支：`codex/pyharness-integrations-trajectory`。来源只读：`D:\zinuotiger-workspace\feature-worktrees\pyharness-platform-web-v1`。

## 可恢复基线

两个目录 HEAD 均为 `7b1bdeab76cff23a040c94002d2bfb33b0291a06`。588 个已跟踪及未忽略未跟踪文件逐个比较：内容无差异，136 个文件只有 CRLF/LF 差异。完整来源快照、Git bundle、未跟踪文件备份、SHA256 清单位于 `%USERPROFILE%\Documents\Codex\2026-10-13\pyharness-five-scope`。未修改来源、稳定 RC、portfolio 或旧聊天工作区。

## 契约与验收矩阵（持续更新）

|范围|契约|必须验收|当前|
|---|---|---|---|
|Codex|可替换 TaskRunner，官方 app-server stdio，保存 thread/turn 标识，逐任务事实事件|创建/恢复、流式进度、结果/命令/变更、审批、取消、断线、进程释放；真实与协议测试分开|默认受治理工具路径真实通过；可选原生命令模式受本机 Windows 沙箱初始化限制|
|飞书|外围 HTTP 事件入口，验证应用/签名/身份，持久去重与渠道会话映射，复用 PlatformService|重复/重启不重复任务，提交/查询/取消，结果回传重试，账号隔离，无凭据不能声称联通|实现/本地协议与队列通过；真实应用配置与目标对话待用户提供|
|个人微信|个人账号，腾讯 ClawBot / iLink 官方开源协议，自动收发|二维码、配对、游标、上下文令牌、身份/去重、过期/退出；不冒充企业微信，不绕过登录|实现/本地协议与队列通过；本人扫码和真实收发待验|
|轨迹|沿用 SessionLog 和现有运行详情，事实白名单，不展示隐藏推理|输入、执行器、时间线、工具/审批/变更/测试/产物、流式刷新、分页、重启、失败取消、权限隔离|已实现并通过测试及界面验收|
|解耦|C1 配置编译、C2 平台任务生命周期，复用已有队列/治理|兼容入口、职责与依赖前后比较、关键类型、资源关闭、全量回归|通过；立即导入循环为零，engine→application 反向依赖清零|

## 当前事实与实施顺序

现有边界包括 EngineRunner、TaskQueue、PlatformRuntime、SessionLog、PlatformService、分页 trace API。C1：ApplicationService._engine_runner_for 直接转换 AgentDefinition；C2：engine.make_runner 直接导入平台身份并管理证据/时限/释放。先改为配置编译器与生命周期协议，随后外部执行器与入口适配，再补 UI 和回归。

最初系统默认 CLI 是 0.140.0，官方当前版为 0.162.1；本次在独立工具目录安装当前版并以项目配置指定，没有修改全局安装/登录。两版 Schema 与真实协议证据均保存。只有 turn.status=completed 才表示该轮完成；实际修改/测试另有强断言，不把模型回复完毕等同任务目标通过。

## 最终检查点

最终全量：2811 passed、41 skipped、5 个既有弃用警告。真实 Codex 修改、测试、同会话恢复、取消与进程关闭通过；飞书/微信是协议模拟与真实本地队列验证，尚无真实账号联通。Wheel 构建、独立安装和入口/资源检查通过。完整验收见 `integration-acceptance-20261013.md`，配置见 `integrations-setup.md`，职责与依赖见 `architecture/integrations-boundaries.md`。

用户已再次确认：先交付本机可执行代码与真实证据，无需再询问是否先交付。外部条件保留待验：本人微信扫码、真实飞书应用配置及明确允许测试的对话。不得自动向真实联系人发测试消息。

不自动推送、合并或发布。接回时以本分支差异审查/提交移植为单位，先备份来源当前状态，禁止覆盖式复制或 reset/clean/stash。

本机 Git 缺少作者邮箱，本次不创建提交、不猜作者身份、不改全局配置。交付保留完整暂存差异，并导出可恢复补丁与完整源码快照；接回方式改为新分支 `git apply --check` 后审查应用。
