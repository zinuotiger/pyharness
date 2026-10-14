# PyHarness 当前本地交付状态

## 当前阶段：全仓修复与最终验收（2026-09-24）

基线为 main / `41d74db15f47bee5813976bd11435dff1be166ef`，包含任务前已有修改；完整状态由仓库外快照和逐次文件哈希固定，HEAD 不单独代表被测版本。

本轮修复与验收已完成。F01–F15、R03–R04、G01–G02 为 confirmed-fixed；R01 为 safely-constrained（PTY 禁用、进程所有权受控）；R02 为 environment-blocked（代码与fake POSIX测试已完成，缺独立POSIX环境）。

最终完整离线测试：2352 passed / 0 failed / 19 skipped，覆盖率 83.41%（门禁75%）；固定 evleven R1 真实MCP补充测试 16 项通过。三种全新非editable安装、原生Qt离屏入口及回环HTTP预检通过。真实模型、人工完整GUI、WSL/Linux及远程CI未验证。ruff未安装且非声明依赖，未冒充静态工具通过。

完整报告：[PYHARNESS-FULL-REMEDIATION-20260924.md](reports/PYHARNESS-FULL-REMEDIATION-20260924.md)。机器可读矩阵同名JSON。最终wheel/sdist与安装日志：`D:\zinuotiger-workspace\checkpoints\pyharness-full-remediation-pre-20260924\sandbox\release-closure`。所有临时输出位于仓库外检查点；旧候选包与用户试用数据保留。代码未提交、未推送。

以下模型预检、2,218 项测试、435 文件哈希及本地候选包均属历史证据。旧候选包不会自动包含本轮修复。


## 历史阶段：真实模型接入预检（2026-09-24）

状态：**离线预检已完成；等待模型来源、请求上限和费用预算决定，未启动真实模型验收。** 下一阶段目标为自然语言驱动 PyHarness × 固定 evleven R1 的项目记忆保存、读取、审批拒绝与跨会话恢复。不是重新全面审计或打包，不引入 rag-engine。

本轮核对 main / HEAD `41d74db15f47bee5813976bd11435dff1be166ef`；与上轮 `.work/release-candidate/20260924T053750/final-state.json` 的 **435 个文件哈希全部一致**。已读 LOCAL-CANDIDATE-REPORT、集成报告和本 STATUS。仅更新此下一阶段事项，未改源码、安装包、配置、用户数据或历史证据；未启动服务、模型探针或真实推理，模型网络请求 **0**，未产生本轮 API 支出。

### 已有接入能力及模式区分

| 位置 | 当前真实能力 / 模式 |
|---|---|
| `pyharness/core/llm.py:591` OpenAICompatAdapter | 已实现真实 HTTP Provider，不是整个项目缺少真实模型能力。支持 OpenAI 兼容 `POST <base>/chat/completions`，非流式及 SSE 流式、tools/tool_calls、Bearer 凭据引用、usage 记录；没有据此宣称原生 Responses/Anthropic/Gemini 协议支持。兼容端点仍需实测。 |
| `pyharness/engine.py:333` / `:1409` | 正式装配经 register_default_llm 注册真实适配器；CLI chat 接入原 AgentLoop、工具执行器、治理审批、持久化和 MCP preload。 |
| `pyharness/core/llm.py:918`、`llm_fallback.py:325` | 有真实 `GET <base>/models` 探针。它只证明连通性，不证明推理/工具能力，也不计入 llm.request/usage；有 fallback 链时引擎启动后台探针。**本轮未运行探针**；后续须单独记录探针次数。 |
| `scripts/evleven_integration.py` | 自动集成程序向 llm.adapters 注入 CommandModel，规划为确定性替身；实际 MCP/存储是真实的。 |
| 已交付 `start.ps1 → trial.py → evleven_integration.py` | 交互命令入口仍使用上述替身；save/read 命令不等于自然语言模型规划。它会清理外部凭据环境并自行装配配置，不能靠修改 model-local/external.json 自动切换真实模式。 |

真实模式优先复用已安装的 **`runtime/harness-venv/Scripts/pyharness.exe --config <本轮专用配置.yaml> chat`**，在交互终端中处理自然语言及原 CLI 审批。此命令是后续入口说明，本轮没有执行 chat。CLI help 已通过安装后的解释器调用 `pyharness.cli.main(['--help'])`，exit 0，确认 --config/chat/run 等入口存在；不能写成不存在的 `python -m pyharness` 入口。无人值守 run/管道 stdin 默认没有人工审批通道，高风险操作会拒绝，不能以此替代批准场景。原 DesktopApp HTTP/审批端点也可复用，候选安装未包含 GUI 可选依赖，不要求为本阶段安装 GUI。

### 配置和缺失条件（只检查明确位置）

正式配置顺序为 `--config` → `PH_CFG_PATH` → `~/.pyharness/config.yaml`/`config.d`，另有 PH_ 白名单覆盖。因此后续必须显式指定 D 盘独立配置并清理无关 PH_ 覆盖，不能默认继承其他项目配置。

- 代码默认值为 `deepseek-chat` / `https://api.deepseek.com` / `env:DEEPSEEK_API_KEY`，默认 fallback 为 qwen-max。**默认值不是用户为下一阶段选定的实际模型。** 本轮没有运行配置加载器来自动采用它们。
- 检查当前进程：`PH_CFG_PATH`、`PH_LLM_MODEL`、`PH_LLM_BASE_URL` 均无；`DEEPSEEK_API_KEY` **有**，只检查有无，未打印、使用或验证有效性/归属；不视为 API 支出授权。模板指定的 `PYHARNESS_TRIAL_MODEL_KEY` **无**。
- 对准确的默认应用位置只做存在性检查：`~/.pyharness/config.yaml`、`~/.pyharness/config.d`、`~/.pyharness/credentials.yaml` 均不存在；仓库根 config.yaml 和交付试用目录指定 credentials.yaml 也不存在。没有读取凭据文件内容、遍历电脑或查找 Codex 登录令牌。
- 读取已交付 `local-trial-20260924T053750/runtime/user/pyharness/integration-config.json` 的必要字段：model=integration-deterministic，base_url 保留代码默认值；固定 evleven MCP 配置和四项 allowlist 已有。这不能算真实模型已配置。
- 交付的 model-local.json 仍是 `REPLACE_WITH_SERVED_MODEL_ID` / `http://127.0.0.1:8000/v1`，external 模板仍是占位模型 / `https://provider.example/v1`。两者的模型来源均未填写。实际运行现有 `model_preflight.py` 检查这两份文件，均 **exit 2**：缺来源、真实 model ID、模板指定凭据；输出 network_requests=0。没有发起端口探测或推理。
- 本地模型服务**未确认存在或已部署**；8000 只是模板值。若选择本地模式，用户需提供已有兼容服务/模型信息，或另行安排部署；不能声称本机一定没有模型。本轮不下载或部署模型。

后续尚待用户决定：**① 本地或外部提供商，以及准确模型来源/ID/端点；② 是否明确授权使用已有 DEEPSEEK_API_KEY 或另行配置专用凭据（只通过环境/凭据引用，不在消息中贴密钥）；③ 单任务及整轮请求上限；④ 外部费用预算与单位，或仅本地、外部费用为零。** 默认配置中的预算和价格表不是本次授权，也不能作为当前提供商账单事实。

### 后续五个场景（全部尚未做真实模型验收）

复用正式 CLI/原 HTTP API、固定 R1 launcher/MCP 配置以及现有集成测试的事件、零发送与读回断言；不新建演示程序。下一阶段需在 D 盘新目录形成真实模式专用配置，沿用原 allowlist、删除拒绝和审批策略及数据隔离，将模型接线改为正式 Provider；不改动已经封装的替身交付包，不借用用户试用数据。

| 场景 | 自然语言输入与独立通过依据 |
|---|---|
| 保存 | 用户提供本轮随机合成项目事实，要求记住；真实模型选择 memory_create，人工批准前 0 原操作发送；真实 R1 读回正文与预先生成的事实相等，保留来源 ID。 |
| 跨会话读取 | 正常关闭双方后重新启动，B 不加载 A 历史；只给合成项目名称/检索关键词而不提供事实正文，模型通过真实 MCP 找回并展示来源；若仅按 ID/词法匹配成功，只报告该方式。 |
| 未保存信息 | 查询预先确定没有写入的另一个随机项目事实；核对真实空结果，模型说明未知，不补造答案或偷偷保存。 |
| 审批拒绝 | 自然语言请求保存另一条合成事实，用户拒绝审批；原调用在批准前及拒绝后均不发送，任务/审计一致，再通过真实查询确认无副作用。沿用删除策略拒绝作为补充，不把二者混同。 |
| 服务异常或超时 | 只针对本轮服务/数据，复用既有错误注入；明确报告失败/不确定，不切换假存储或重试未知写入。可安全查询时对账；取消/超时不代表远端副作用撤销。 |

边界建议（**待用户确认，不已授权**）：每任务最多 6 次推理请求、整轮最多 30 次；每次输出 512 tokens、总超时 30 秒；fallback_models=[]、retry.attempts=0、degrade.enabled=false，避免自动切换/重试及 fallback 后台探针。已有 loop.max_turns 可设 6，但它是主循环轮数上限，不能冒充包含摘要/子任务的全局网络请求硬上限；后续验收驱动需分别计数全部真实请求并到限停止，限制无关子任务/压缩请求。费用金额本轮不代选，未确定前不启动 Agent 循环。

记录要求：实际模型及端点、PyHarness/R1 被测身份、每次推理/重试/辅助请求及独立探针次数、session/task/tool call/approval ID、MCP 发送和实际副作用、成功失败/停止原因、provider usage 中 input/output/cache tokens。沿用 llm.request/response/usage、tool/approval 事件和现有审计记录；成本按明确价格配置估算，并与实际账单区分。usage 缺失/估计或价格未知时明确标记，不能写成实际费用为零。确定性替身只可验证驱动，不计入五场景真实模型通过数。

交接：预检到此停止。真实适配器已存在；尚缺**被用户选定和授权的连接配置、请求上限及预算**，而非需要重新开发模型平台。本轮没有新文档体系、重打包、全量加固、其他仓库修改或发布。

---

2026-09-24：**本地候选版与独立安装功能验收通过（限定本机）**。

- 交付目录：`dist/local-candidate/20260924T053750-final/`，含 wheel、固定 R1、安装/检查/交互入口、模板、中文说明与校验和；未 push/发布。
- 新建独立非 editable 环境，在中文/空格解包目录验证真实 MCP、人工输入式审批、拒绝、双方重启和新会话恢复。完整回归 2,218 pass / 3 原有 skip，覆盖率 82.93%。
- 交互主入口：`.work/release-candidate/local-trial-20260924T053750/start.ps1`，已独立安装并验证 READY→quit，未灌入验收记忆。旧 evleven_integration.py 明确属于自动验收入口。
- 模型为显著标记的确定性规划替身，真实模型只有配置模板和无网络静态预检，仍未验证。
- 一次临时 uv 诊断遗漏缓存重定向，可能使用 C 盘默认缓存；正式安装器已固定 D 盘路径。完整证据、边界偏差和版本范围见 [本地候选版报告](reports/LOCAL-CANDIDATE-REPORT.md)。

以下保留上一轮历史状态：

2026-09-23：**PyHarness × evleven R1 集成在 Windows 本地单信任域限定范围通过**。

- 真实公开 HTTP 入口、Agent 运行时、授权/审批、MCP stdio 与固定 R1 wheel 的 SQLite 持久化链路已验证。
- 已验证保存、读取、策略拒绝、正常关闭后双方重启，以及不继承 A 历史的新会话 B 读取同一记忆。
- 最终完整测试：**2,218 passed / 3 原有 Windows skipped / 0 failed**；覆盖率 **82.93%**（门禁 75%）。本次 16 项真实 R1 集成测试全部执行。
- 构建、Python 编译、JavaScript 语法检查、依赖核对和主试用均通过；尚未 push、发布或运行远程 CI。
- 规划模型为明确标识的确定性替身；没有真实模型、语义能力、多租户或跨平台生产验证。
- 超时/取消/断开不保证远端副作用撤销；结果不确定时查询核对，不自动重试写入。

主试用：

```powershell
.work/verification/venv/Scripts/python.exe scripts/evleven_integration.py
```

依赖校验/安装及完整使用说明见 [集成交付报告](reports/PYHARNESS-EVLEVEN-INTEGRATION.md)。双方环境、合成数据和日志均在当前仓库 D 盘 `.work/` 内；evleven 原仓库仅按授权读取了 R1 交付资料和包。

HEAD 仍为 `41d74db15f47bee5813976bd11435dff1be166ef`，main 分支。工作树包含原验收补丁和本次集成补丁，**不能仅以 HEAD 代替被测版本**；最终哈希清单见 `.work/integration-evleven/evidence/final-tested-files.json`。

历史记录完整保留：[原独立验收与缺口补齐](reports/REPO-VERIFICATION.md)。其中 94 项为上轮相关回归，不是本轮完整测试统计。
