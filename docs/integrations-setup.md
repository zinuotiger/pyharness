# Codex、飞书与个人微信接入

本次功能位于现有工作台。模型配置里有“添加 Codex 执行器”，设置里有“飞书与个人微信”，任务检查器里的“查看结构化 Trace”打开事实轨迹页。Web 与桌面 WebView 使用同一页面和应用服务。

## 启动与 Codex 客户端

`start-integrations-web.cmd` 从当前项目启动，并使用独立的本地数据目录，不覆盖旧实例的数据。也可沿用现有启动方式。首次安装本项目所用客户端：

```powershell
powershell -File scripts/setup_codex.ps1
```

脚本把官方 `@openai/codex@0.162.1` 安装到 `tooling/codex/node_modules`，不更改全局安装、登录或用户配置；生成本机专用且不提交的 `tooling/codex/runtime.json`。Codex 的账号登录仍由官方客户端管理，PyHarness 不读取或复制登录凭据。若需登录，请通过所选官方客户端正常登录。

客户端选择顺序：已发布助手里的 `codex_executable` → 本进程 `PYHARNESS_CODEX_BIN` → 项目 `runtime.json` → 系统安装。支持官方 npm `bin/codex.js` 入口或原生可执行文件。界面显示实际路径和版本；执行器拒绝低于本次验证版本的客户端。当前机器已在本次任务独立工具目录安装 0.162.1，并由项目本机配置引用；不是仅在验收脚本临时改变环境变量。

在“添加 Codex 执行器”填写名称、可选模型和路径，确认本机执行权限后创建。创建的助手默认使用 `codex_tools=harness`：

* Codex 负责会话和模型执行；使用官方 app-server stdio 协议。
* 文件读取、写入、Python 运行使用官方实验性 `dynamicTools` / `item/tool/call` 客户端接口，实际转给既有 ToolExecutor、Scope、治理重入和人工审批。它没有另建工具执行系统。
* Codex 原生工具保持只读、网络关闭；原生命令/写入扩权请求拒绝。继承的 MCP 在进程启动前逐项禁用，并再次用官方 CLI 检查有效配置；桌面连接器不会自动转为平台权限。
* 任务在私有工作副本中执行，生成差异和测试报告；下载、应用变更仍走原有产物审批。`host_approved` 明确表示 PyHarness 的宿主工具没有额外 OS 隔离，仅向本地操作者开放；团队账户不能借用本机 Codex 登录。
* 支持新建/恢复会话、公开进度、失败、取消、断线、重启后查轨迹。断线不自动重跑在途任务；下一次用户明确提交才恢复已保存的 Codex thread。

`dynamicTools` 是官方文档标记的实验性接口，因此固定本次验证客户端，并保留协议测试。可选 `codex_tools=native` 使用 Codex 自身工作区沙箱和单次审批映射；本机该路径的命令执行受 Windows 沙箱 `helper_unknown_error: setup refresh had errors` 阻碍，不能称已通过原生命令验收。没有关闭 Windows 沙箱来掩盖错误。默认客户端工具路径已真实完成修改和测试，不依赖这个系统级修复。

默认路径的示例助手：

```json
{
  "name": "Codex 助手",
  "executor": "codex",
  "codex_tools": "harness",
  "codex_model": "gpt-6.1-sol",
  "permission_policy": "standard",
  "sandbox_profile": {"mode": "host_approved"},
  "allowed_tools": ["codex.run", "fs.read_file", "fs.list_dir", "fs.write_file", "exec.python_run"]
}
```

模型须为账号实际可用型号；模型目录可读取不等于任务可执行。Codex 金额计费数据不可得，界面报告未知，不承诺 `budget.cost` 金额硬上限。任务时限、工具调用、事件/输出上限与收到的累计 Token 用量均有执行限制；Token 限制在供应商报告用量后检查，不能撤销已经发生的消耗。`max_rounds` 是原生 PyHarness 循环参数，不声称能控制 Codex 内部模型轮次。

## 飞书应用

在“飞书与个人微信”配置自建应用：App ID、Tenant Key、允许的 Open ID 与 Chat ID、App Secret、Encrypt Key、Verification Token，以及可选的已发布助手版本。密钥通过既有 TenantSettingsStore 服务秘密接口保存，只写入不回显。未启用配置不接收任务。

在飞书控制台开启机器人能力、配置应用可用范围，订阅 `im.message.receive_v1`，申请发送机器人消息所需权限，例如 `im:message:send_as_bot`。接收私聊/群消息还需相应接收权限。配置事件回调为本机反向代理的 `/hooks/feishu/<连接名>`。只代理这个专用路径，并将上游 Host 改为 `127.0.0.1:<本机端口>`；管理页面与其他 API 保持本机认证，不公开部署。

回调实现了 URL challenge、加密事件解密、Verification Token、SHA256 签名、五分钟时间窗、应用与租户、用户/聊天允许列表验证。消息 ID 持久去重；渠道用户与聊天映射到独立会话。重复投递、重启、任务接收后的崩溃都不允许重复执行同一任务。

任务完成、失败、取消后，通过官方消息回复接口反馈。发送使用稳定 UUID，有限重试（最多六次，指数退避至五分钟），失败保留在界面，可人工重试反馈。配置成功不表示真实联通。当前没有真实飞书凭据，已验证协议与本地真实队列，未向真实联系人发测试消息。

## 个人微信：腾讯 ClawBot / iLink

这是个人微信通道，不是企业微信或公众号。依据腾讯官方 `Tencent/openclaw-weixin` 2.4.10 源码独立实现了 Python iLink 文本客户端，包括二维码、确认/配对码、区域重定向校验、Bot Token、长轮询游标、上下文令牌和回复。无需额外运行一套 OpenClaw 编排器。

在微信里确认账号具备 ClawBot 插件能力，在 PyHarness 点击“扫码绑定”，使用本人微信扫码并在微信确认。若插件要求配对码，填写插件显示的码；不输入微信密码，不复用其他客户端的登录文件。绑定只保存账号，不自动启用收发；选择已发布助手后，明确启用自动收发。

仅处理允许用户发给已绑定 Bot 的私聊文本；不读取历史，不处理群聊、媒体或任意联系人列表。反馈必须携带对应入站消息的上下文令牌。Bot Token 和上下文令牌存入项目服务秘密接口，消息数据库只保存秘密引用。收到的消息先持久化再推进游标，重复消息不会重复建任务。会话过期停止收信并显示需要重新绑定；关闭/暂停会取消长轮询并释放 HTTP 客户端。

当前未由本人扫码，也未验证账号插件可用性，因此不能声称真实微信已联通。真实收发的最小操作是本人完成这一步绑定，并指定一个允许的测试对话；本次没有代替用户发送消息。传统开放平台登录/分享文档不足以判断个人 Agent 接入，本次已纠正该早期判断，采用当前腾讯公开源码路径。

可选的手动转交接口仍作为补充保留，绝不计作自动微信接入完成。

## 渠道命令与隔离

普通文本提交任务。`/status [任务编号]` 查询，`/cancel [任务编号]` 取消，`/approvals` 查看本渠道用户会话的待审批。只有操作者显式启用渠道人工审批后，`/approve <编号>`、`/reject <编号>` 才可裁决；不会扩展助手工具权限。查询/取消不能操作其他用户或聊天的任务。

渠道配置和投递记录仅向本地操作者开放。HTTP 回调独立验证飞书签名，不信任调用方声明的 PyHarness 租户或身份。个人微信不开放公共收信 HTTP 入口。投递记录分页每页最多 100，入箱总量上限 10000；满时停止接收并保留游标，不静默丢消息。

## 官方依据

* Codex 协议、实验性客户端工具、取消与审批：https://learn.chatgpt.com/docs/app-server
* 客户端发布版本：https://learn.chatgpt.com/docs/changelog
* 模型与退役状态：https://learn.chatgpt.com/docs/models
* Windows 原生沙箱诊断：https://learn.chatgpt.com/docs/windows/windows-sandbox
* 飞书事件验签/解密：https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case
* 飞书接收消息：https://open.feishu.cn/document/server-docs/im-v1/message/events/receive
* 飞书回复消息：https://open.feishu.cn/document/server-docs/im-v1/message/reply
* 腾讯协议来源：https://github.com/Tencent/openclaw-weixin ，核验提交 `874637a0dbc24050f72d1385add4fc6170a2d726`，2026-10-13 当天仍有维护。
