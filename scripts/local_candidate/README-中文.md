# PyHarness × evleven 本地候选版

这是 Windows 本机、单信任域的受控功能试用。真实 PyHarness 运行时、真实 HTTP API、治理审批、stdio MCP 和 evleven R1 持久化；**模型是确定性命令规划替身，没有真实模型自主推理**。终端命令将用户输入转成工具请求，工具结果始终来自真实服务。

## 安装和启动

前提：Windows、CPython **3.13**、已安装的 `uv`、可写的 D 盘目录。安装需要从包索引下载固定版本依赖（或命中本地缓存），**不是完全离线包，也不是零依赖便携程序**。不会下载 Python 或模型，不安装系统服务。不要将已有 runtime 连同虚拟环境搬到其他路径；移动交付包后重新安装。

将整个交付目录解包到 D 盘新目录。在 PowerShell 中用绝对路径执行（空格和中文可用）：

```powershell
$c = 'D:\你的目录\PyHarness 候选版'
& "$c\install.ps1" -PythonPath 'D:\你的Python\python.exe' -UvPath 'D:\你的工具\uv.exe'
& "$c\check.ps1"
& "$c\start.ps1"
```

也可给 `PythonPath` / `UvPath` 传入已经在 PATH 中的 `python` / `uv`。不更改执行策略或系统配置；脚本受客户端或本机策略阻止时应按现有审批流程处理。安装器先检查文件校验和，再建立独立的 `runtime/harness-venv` 和 `runtime/evleven-venv`，安装非 editable wheel。检查失败会返回非零退出码。

`check.ps1` 是自动化验收：使用随机合成记忆、自动批准测试请求、停止并重新启动双方、新会话按 ID 读回、验证策略拒绝以及审批拒绝/取消/超时。每次生成独立的 `runtime/checks/run-*` 与 `runtime/fault-checks/*`；**它不代表用户交互已经完成**。

`start.ps1` 是交互终端：只有 HTTP 就绪且真实 MCP 初始化、工具注册完成才打印 READY。监听 127.0.0.1 随机端口；stdio 子进程不开放网络端口。每次启动创建新会话，`new` 可在同次试用中创建下一会话。没有图形界面；所有操作通过现有 HTTP API 和 Agent 运行时。

## 两分钟试用

看到 `memory>` 后输入以下内容；每次出现审批提示，由你输入 `yes` 批准，其余输入拒绝。

```text
save 合成记忆：蓝色纸鹤周三测试 RC练习
yes
read last
yes
deny last
audit
quit
```

保存结果的 `memory.id` 是 evleven 来源标识。`read last` 仅从本地读取上次的 ID，再通过 MCP 从 evleven 读取正文，模型不保留固定答案。`deny last` 演示删除被策略拒绝，显示发送次数为 0；`audit` 显示实际会话的治理记录。可以再 `read last` 核对记忆仍然存在。

重新运行 `start.ps1`，看到不同的 session ID 后输入 `read last`、`yes`。也可输入 `read <memory.id>` 或 `search <确定性关键词>`。关键词/ID 读取通过不代表语义检索已经验收。`history` 查看当前会话公开消息，`new` 创建新会话，`quit` 正常停止拥有的服务进程。结束终端进程或断电的清理保证未验证，请优先使用 `quit`。

只输入合成内容，不输入个人记忆、密钥或业务配置。用户数据始终保留在 `runtime/user/`；evleven 数据库在 `runtime/user/remote/memory.db`，PyHarness 会话在 `runtime/user/pyharness/`，每次试用日志在 `runtime/user/logs/<随机ID>/`。自动验收和用户数据分离。重复安装/启动不会默认清空数据；同一用户数据只允许一个终端试用进程。

## 治理与限制

仅注册 memory_create、memory_read、memory_search、memory_delete 四项工具，均按本地高风险审批处理；删除被本地策略禁止。审批前不会发送原操作；拒绝、审批超时和取消不会继续发送。任务/会话/call ID、审批事件、MCP 请求以及返回结果可在 JSON 日志对应。

**请求超时、取消或断开不保证远端已停止，也不撤销已有副作用。** 发生写入结果不确定时，不盲目重试；先按已知 ID 或独特关键词查询核对。没有恰好一次或强制回滚保证。同步工具强制停止限制保持不变。

本版仅验证本机 Windows / CPython 3.13；其他机器、Python 版本、平台、网络服务、并发用户与生产部署未验证。R1 无向量模型下载，不把关键词检索称为真实语义模型效果。GUI、Qt、浏览器 WebView 不属于这个终端候选包，因而不安装这些可选依赖。

## 真实模型准备（不发出推理请求）

现有 Provider 为 `OpenAICompatAdapter`，使用 OpenAI 兼容 `chat/completions` 接口；复用 PyHarness 的 `llm.model/base_url/api_key`（env 引用）、fallback、retry、timeout 配置。三个 JSON 文件是**预检模板**，不是自动启用模型的开关：

- A `model-deterministic.json`：当前交互入口实际运行的替身模式。
- B `model-local.json`：填入已存在的本机模型来源和真实 model ID、回环接口；本轮不下载、不启动、不调用模型。必要的 key 环境变量即使是本地占位值也必须显式提供。
- C `model-external.json`：填入提供商、实际模型和端点；费用授权及预算必须另行确认，不可使用 Codex 客户端凭据。

```powershell
& "$c\runtime\harness-venv\Scripts\python.exe" -I -B -X utf8 "$c\model_preflight.py" "$c\model-local.json"
```

预检只检查显式文件字段及指定环境变量是否存在，不打印其值、不读凭据文件、不找电脑上的 token，不连接服务器；配置缺失退出 2。即使预检退出 0，也仅表示静态条件齐备，**真实推理仍未验证**。模板的 `llm` 对象可合并到原程序配置；不得把替身入口声称为真实模型入口。

未来最小真实模型验收：确认模型来源/许可证、OpenAI 兼容接口工具调用支持、model ID、显式凭据、本机路径和现有 MCP allowlist；建议单轮不超过 6 次模型请求、每次输出 512 tokens、30 秒总超时、关闭 fallback 和重试。这个请求数是建议而非本脚本执行的计费门禁。先授权预算，再验证模型能选择正确读写工具、等候人工审批、尊重拒绝/取消、不伪造返回结果以及新会话 ID 读取；外部 API 成本和自主规划能力本轮未验收。

## 校验和与重现

`SHA256SUMS.json` 列出包中全部静态文件（自身除外）；`BUILD.json` 记录 HEAD 和原集成脚本哈希，`SOURCE-STATE.json` 固定未提交代码文件。固定 R1 wheel 为 `40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e`。constraints 固定依赖，requirements 声明入口所需组件；安装后的实际版本、解释器与 import 路径见 `runtime/logs/installed.json`。不要把 runtime、环境或用户数据加入新分发包。
