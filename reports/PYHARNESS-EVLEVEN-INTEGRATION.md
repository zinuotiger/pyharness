# PyHarness × evleven R1 真实 MCP 与跨会话记忆交付

日期：2026-09-23，Windows 本地单信任域。**限定范围通过**：真实 MCP 集成、治理和跨会话持久化交付完成；最终完整测试 **2,218 passed / 3 原有 skip / 0 failed**，覆盖率 **82.93%**；构建、静态检查及主试用入口通过。真实模型未验证。

## 范围、版本与隔离

唯一写入仓库 `D:\zinuotiger-workspace\repositories\pyharness`；origin `https://github.com/zinuotiger/pyharness.git`，分支 `main`，起始及最终 HEAD 均为 `41d74db15f47bee5813976bd11435dff1be166ef`（工作树补丁未提交）。未 reset、clean、clone、push、发布或修改 Git 全局配置。原验收及缺口补齐的全部修改、测试和报告保留。

开始时重算 `.work/verification/gaps-tested-files-sha256.json` 的 417 个文件，差异为空。上轮 **94 项仅为相关回归**，本次没有用它替代完整测试。旧报告作为历史记录保留，新状态入口为根目录 `STATUS.md`。

只读 evleven 的 R1 验收报告、`docs/R1-RUNBOOK.md`、`reports/r1-evidence/{README.md,delivery-manifest.json,SHA256SUMS.txt}` 以及报告指定 wheel。未读取其他仓库；没有操作 evleven Git、源码、环境或数据。包内容检查和运行均来自复制到 PyHarness 的 wheel。

| 固定对象 | 版本 / 身份 |
|---|---|
| evleven R1 基础 HEAD（来自验收报告） | `d2a6251d9eb5a0470f7e38f79e7b95eef9bfbcf5` + 报告记录的未提交 R1 成果；不单靠 HEAD 识别包 |
| R1 原始交付位置 | `D:/zinuotiger-workspace/repositories/evleven/dist/r1/20260923T134303329608/evleven-1.1.0-py3-none-any.whl` |
| R1 wheel SHA-256 | `40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e` |
| delivery-manifest SHA-256 | `73b0614848d54546d4d1cf89e6f37649a9126e516eec0d2dc9b67ca1a07bdc80`，与交付校验和文件匹配 |
| R1 报告冻结指纹 | `b9460f51631a118574e774b7965489ad519cfce7c3b4c77bef249addde0ff2be`；引用标识，没有重做 R1 180 分钟实验 |
| PyHarness 解释器 | `.work/verification/venv/Scripts/python.exe`，Python 3.13.13 |
| evleven 解释器 | `.work/integration-evleven/evleven-venv/Scripts/python.exe`，Python 3.13.13 |
| R1 实际 import | 上述独立环境 `Lib/site-packages/evleven/__init__.py`，17 个 Python 模块与 wheel 逐字节核对 |
| R1 依赖 | 按交付清单固定的 38 个运行时发行包；包括 mcp 1.30.0、anyio 4.15.1、pydantic 2.13.5、numpy 2.5.3；详见 `r1-install-check.log` / `setup-replay.log` |
| 本轮专用目录 | `.work/integration-evleven/`（Git 忽略），环境、交付包副本、缓存、日志、配置、合成数据库和证据均在其中 |

最终被测文件清单为 `.work/integration-evleven/evidence/final-tested-files.json`，规范化清单 SHA-256：**`f317944f16503b52808d65dd6a353535e460c0d929529b7b8ece112ea0524c7a`**。涵盖 tracked 文件及未提交的新代码/测试/脚本，报告和 STATUS 排除自引用；HEAD 加此清单才是本次被测身份。与上轮清单相比，旧文件仅新增修改 `.gitignore`、`pyharness/config.py`、`pyharness/core/mcp.py`、`pyharness/engine.py`、`tests/unit/test_mcp.py`；原先其他补丁哈希保持一致。新增三个接入脚本、本轮集成测试、报告和 STATUS，未提交到 Git。

`scripts/setup_evleven_r1.ps1` 核对清单和 wheel，使用固定 constraints 非 editable 安装，执行 `uv pip check` 和真实 import 自检，已实跑退出 0。没有安装 embedding/image/audio extras，没有模型下载。PyHarness 原环境未修改依赖。服务启动使用 `-I -B`，不继承源码 PYTHONPATH；启动器校验解释器、包文件和实际依赖版本，然后才调用原 `python -m evleven` 模块入口。

evleven 子进程清除业务环境变量、密钥和代理，cwd、数据库绝对路径、HOME/USERPROFILE、APPDATA、TEMP/TMP、缓存均指向本轮运行目录。MCP 模式不开 Web 服务；PyHarness HTTP 只绑定动态 `127.0.0.1` 端口。日志包括每次实际服务 PID、cwd、import 位置和数据库位置。没有服务端写回 evleven 仓库的配置路径。

## 实际协议与配置

- 传输为真实子进程 stdio、UTF-8 换行 JSON-RPC 2.0；初始化顺序 `initialize → notifications/initialized → tools/list`。
- PyHarness 提议且 R1 实际接受 **2024-11-05**。R1 独立验收曾与其官方客户端协商 2025-11-25，本次不能沿用那个数字。`serverInfo.name=evleven`；服务报告的 `version=1.30.0` 为 SDK 版本，不是 wheel 的 1.1.0。
- 当次真实发现 19 项工具，完整 schema 留在 `handshake.json`。没有按数量断言，也未给模型开放全部发现能力。
- 配置新增 `plugins.mcp_servers[].allowed_tools`：缺省 None 保持原注册行为，空列表不暴露工具，明确列表只注册已发现的指定工具，缺失名称失败。不会用服务端 description/annotations 放宽权限。
- 本次最小集合为下表四项；每项 `danger=high`，使用原审批服务。`security.policy.preset=standard` 只允许 MCP 域可达，未关闭 guard；`deny_tools_extra=[mcp.evleven.memory_delete]` 明确禁止删除。

| 远端工具 / PyHarness 前缀 `mcp.evleven.` | 本次实际参数 | 实际输出 / 治理 |
|---|---|---|
| `memory_create` | `content`, `entity_id`；其余由真实 schema 默认值补齐 | `success:true, memory:{id,content,…}`；必须审批 |
| `memory_read` | `memory_id` | `success:true, memory:{…}`；必须审批 |
| `memory_search` | `query` | `success:true, results:[…], total:N`；必须审批，合法空集合成功 |
| `memory_delete` | `memory_id` | 在本地 scope/guard 拒绝，MCP 未发送；用于验证拒绝链 |

安装及试用不直接 import evleven 业务函数。服务 launcher 仅核实分发包并运行其正式模块入口；所有业务读写都经过真实 MCP。SQLite 直接连接只用于隔离数据库的锁故障注入，不替代正常业务路径。

实际配置保存为每个 PyHarness 数据目录中的 `integration-config.json`。模型为 `integration-deterministic`，只把用户明确的合成命令变成 tool_call；第二步输出来自运行时真实工具消息，不提供记忆固定答案。测试主持程序通过已有 HTTP 审批端点提交明确批准/拒绝，**不是模型自批**；默认高风险治理没有降低。

## 成功链与证据来源

主试用复用 `DesktopApp` HTTP API：POST sessions → POST messages → 真 AgentLoop/ToolExecutor/Scope/Guard/Approval → `McpClient`/stdio → 安装后的 R1 服务 → SQLite/FTS5 → 工具结果 → timeline/messages/JSONL。

会话 A 用随机 UUID 生成本轮合成中文记忆，经实际审批写入并用返回 ID 读取；随后请求删除，被策略在 MCP 发送前拒绝，再次读取确认数据原样。终端显示拒绝，证据同时包含发送调用记录和真实服务端数据。

正常关闭 A 所在的 PyHarness 和 evleven 测试进程后，另一个 PyHarness 进程启动新的 R1 进程，连接同一远端数据库。创建全新会话 B，使用另一个空的 PyHarness 本地数据目录；只向模型提供 `memory_id`。共享引用文件只包含 ID、预期内容 SHA-256 和 A 的会话 ID，不包含内容原文。B 的第一个用户请求前断言无 user.message；读回内容哈希与 A 预先保存的哈希一致。

`write-evidence.json` / `read-evidence.json` 留存两个 PyHarness PID、MCP PID、不同 session ID、真实请求/响应、原 messages 输出、审计结果及退出码。新会话未载入 A 的历史；实际内容来自 MCP `tools/call` 响应。两个 R1 正常退出码均为 0。该证据证明 **ID 读取和词法查询、跨进程持久化**，不证明语义理解。

最终用户主入口实跑目录：`.work/integration-evleven/trial/run-4b66bf3f00f54963a9203058ccc76e72/`。A=`s-fc8b1fc5a538`、B=`s-5c25870063c3`；记忆 ID=`01a0ceed-dec0-7e14-b4ab-bbe5a840453a`。PyHarness PID 为 40248/21584，stdio 父进程 PID 为 42320/29048，HTTP 端口 60294/49970。Windows venv 可能另有实际 Python 子 PID，已由 R1 launcher 记录在 `remote/logs/process-*.json`。事后检查全部 60 个已记录自建 R1 实际 PID 均不在运行，见 `evidence/process-check.json`；没有终止其他应用进程。

MCP 调用记录含 `sid/task_id/call_id/request_id`；JSONL 中 `segment.start/end` 对应 task_id，tool.call/result/error 的 call_id 与 decision/receipt trace 对应，approval_ref 指向 approval.requested 的 seq。传输 DEBUG 日志另记实际 pid/id/method/tool。新增关联仅供本地证据，不改变协议参数或让远端控制授权。

## 治理与错误路径

全部测试位于 `tests/integration/test_evleven_r1.py`，正常路径始终使用固定 R1 wheel。故障注入与正常链分开运行、分开存证。

| 场景 | 实际验证 |
|---|---|
| 允许读写 | 本地高风险审批批准后真实创建/读取，decision→approval→重判→receipt→result 可追踪，审计 findings 为空 |
| 策略拒绝 | 删除前后客户端发送数不增加、无成功工具结果、存在 guard.rejected；MCP 读取证明记忆仍在 |
| 批准前不执行 | pending 时发送列表为空；审批拒绝、真实 1 秒 TTL、取消三条路径均无原写请求；新 MCP 查询不存在该内容 |
| 非法参数 | 空 content 在注册表校验为 TLB-803、未发送；另以真实 MCP 发送错误类型，服务返回 isError=true |
| 未知工具 | PyHarness 未注册名称 TLB-802、未发送；直接协议边界探测未知远端工具返回 isError=true |
| 服务端业务错误 | 读取不存在的合成 ID，R1 返回 isError=true；桥转 tool.error/TLB-805，无成功 tool.result |
| 协议错误 / 空结果 | R1 SDK 对未知 method 返回 -32602（实际观察，不改写为 -32601）；合法无匹配返回 success=true/results=[]/total=0 |
| 启动失败 | 缺失解释器、服务无效启动参数明确失败；主入口等真实初始化/发现/注册，失败非零，未切换假内存 |
| 运行中断开 | 对自有 R1 stdin 发送 EOF 并等进程退出；旧连接后续调用失败，不自动启动未经握手的新服务 |
| 请求取消 | 在真实 SQLite 锁等待中取消 MCP 请求；旧连接退役、原进程收尾，新连接初始化后查询核对 |
| 超时/取消后的不确定写入 | SQLite 锁延迟提交；客户端停止等待后再释放锁，R1 真实提交一条记忆。超时反馈含“不确定/先查询/不要盲目重试”，取消为 partial 并说明可能仍运行、未撤销副作用。新治理会话查询恰有一条原内容；没有重发写入 |

最后两项是故障注入证据：只在本轮临时数据库加锁，释放由测试负责，正常链没有延迟或假服务。接口并不承诺 exactly-once、强制停止或事务回滚。当前 stdio 会话报错后必须用新客户端/新会话重新初始化，先查证再决定是否人工重试。

## 已证实问题与最小修复

1. **通知错误带 id**：`mcp-probe-before.log` 记录 R1 将 initialized 当请求并返回校验错误，随后 response id 错位。修正 JSON-RPC 通知不携带 id，`mcp-probe-after.log` 真实连接和空查询通过。
2. **显式 deny 未进入引擎策略**：初次完整业务试用中合成记忆实际被删除，日志可追踪到远端 memory_delete。真实引擎用空 ScopePolicy 装配，未读取 deny_tools_extra；预设重建也遗漏。只在原 `_apply_preset` 加入配置显式禁止项，初装和预设切换统一生效。回归同时检查策略、零发送及服务端数据。
3. **正常关闭直接 terminate**：R1 实际退出码 1。改为先关闭 stdin 等待 EOF 正常收尾，超出边界才终止自己创建的进程；回归正常退出码 0。
4. **断开后静默重启、取消后遗留流**：RED 测试分别观察到新 PID（未经握手）和取消后仍存活的旧流。传输实例关闭后不可复用；取消/超时清理连接并保留不确定语义，不自动重试写入。

新增本地工具选择配置、协商结果记录和不支持版本拒绝。仍复用原客户端、队列、审批和工具桥；没有引入新的 Agent 平台、审批系统、数据迁移或服务端补丁。PyHarness schema 编译器保持原严格限制，没有为了连接而接受任意参数。

首批集成 RED：14 项中 9 pass / 5 fail（四类运行时问题及其端到端后果）；修复后同 14 项通过。之后补充 2 项“不确定写入后对账”测试。试用脚本开发中的两处 API 属性名错误已修正，日志保留为脚本调试记录，不归因于 R1。

## 运行命令和最终检查

`P=.work/verification/venv/Scripts/python.exe`。验证命令通过 `P .work/integration-evleven/run.py <标签> <命令…>` 记录精确 argv、cwd、开始时间、退出码和耗时；只对当前进程设置隔离环境及缓存，附加原本地网络限制。各次日志、JUnit、临时目录独立，没有覆盖上轮验收证据。

| 标签 / 检查 | 结果与证据 |
|---|---|
| r1-install-check / setup-replay | 0；真实导入、文件与依赖校验，pip check 通过 |
| mcp-probe-before / after | 1 → 0；真实协议失败与修复证据 |
| integration-red / green | 5 fail + 9 pass → 14 pass |
| uncertain-writes | 0；2 pass |
| related-regression | 0；257 pass；授权、审批、执行器、scope、持久化、引擎、应用服务及全部相关集成 |
| full-final | 0；2,221 项收集，2,218 pass / 3 skip / 0 fail；82.92% 覆盖率，75% 门禁通过 |
| release-final | **0；2,218 pass / 3 skip / 0 fail / 0 error，82.9273%**；仅补充试用终端的明确拒绝展示后，最终版本再次全量运行；包装器 131.105 秒 |
| build-final | 0；`uv build --python .work/verification/venv/Scripts/python.exe --out-dir .work/integration-evleven/dist`；wheel + sdist |
| compile-final | 0；`P -X pycache_prefix=.work/integration-evleven/bytecode -m compileall -q pyharness scripts/evleven_integration.py scripts/evleven_r1_server.py tests/integration/test_evleven_r1.py` |
| ui-extract / ui-check | 各 0；提取原 UI JavaScript 后 `node --check`，无 UI 功能改造 |
| main-trial | 0；`P scripts/evleven_integration.py`，8.618 秒，完整主链及两次正常停止 |
| final-evidence-review | 0；JUnit、coverage、所有 Python 包文件逐字节匹配、wheel/sdist 均未包含 `.work/` |

最终分层：unit 1,876 pass / 3 skip；e2e 51；integration 34（含本次全部 16 项 R1 实测）；security 55；acceptance 9；invariants 193。汇总、逐测试结果与证据路径见 `.work/integration-evleven/evidence/summary.json` 和 `release-final.xml/.log/.json`。

初次构建 wheel SHA-256 为 `eed50e7e020b8cb1b85524d0d6f6ddaacbc9c1dcf995c7c60e1c4b7d80b4fd26`；其全部 pyharness Python 文件与最终被测源码相同。构建器提示缓存位于源码内，已实际检查两种包内容，没有包含缓存、环境、数据库或 `.work/`。最终文档收尾后的交付包另存 `release-dist` 并以 `evidence/delivery-artifacts.json` 记录校验和，避免覆盖构建证据。未发布。

最终全量命令：

```powershell
.work/verification/venv/Scripts/python.exe .work/integration-evleven/run.py release-final .work/verification/venv/Scripts/python.exe -m pytest -q -ra -s -p no:cacheprovider --basetemp=.work/integration-evleven/release-final-tmp --junitxml=.work/integration-evleven/release-final.xml --cov-fail-under=75 --cov-report=json:.work/integration-evleven/release-final-coverage.json
```

三个 skip 沿用既有 Windows 条件（两个 POSIX mode 检查、一个无符号链接权限）；本机 R1 集成全部实际执行。新增真实依赖测试在未配置 R1 环境的其他机器会明确 skip，而不会换 Mock；验收前应先执行 setup 并核对该文件的全部用例运行。

本次命中跨模块、治理和连接生命周期高影响信号，因此执行完整回归，未用 94 或 257 项替代。未删测试、放宽有效断言或降低覆盖率门禁。未重跑无关 R1 180 分钟实验。

## 一套本地试用命令

在 PowerShell 中，从 PyHarness 仓库执行：

```powershell
Set-Location D:\zinuotiger-workspace\repositories\pyharness
# 可重复执行；校验固定交付包和独立环境，不修改 evleven。
pwsh -NoProfile -File scripts/setup_evleven_r1.ps1
# 主入口：启动、保存、读取、展示拒绝、正常停止、重启、新会话读取、再次停止。
.work/verification/venv/Scripts/python.exe scripts/evleven_integration.py
```

自动化测试使用 `.work/integration-evleven/tests/<随机ID>`；用户入口使用 `.work/integration-evleven/trial/run-<随机ID>`，每次新建合成数据目录，不删除历史试用库。程序打印数据与日志路径；成功必须等真实业务验证完毕才打印 PASS，任何断言、连接或环境失败返回非零。主流程自动停止自己创建的双方进程，无需猜两套启动参数。

再次读取某次保留的远端数据库（把路径换成该次主入口输出，**不用重新写入**）：

```powershell
.work/verification/venv/Scripts/python.exe scripts/evleven_integration.py --phase read --data-dir .work/integration-evleven/trial/run-<实际ID>
```

该命令仍创建新会话，正常完成后自动停止。运行中可 Ctrl+C；它是终止试用请求，不是远端写入撤销承诺，需查看原数据库/证据并用读取核对。应用自身有 90 秒异步总限时，让 finally 收尾先于父进程兜底超时。未承诺 OS 强杀、断电时清理一定完成。

只运行本轮集成测试（使用新标签和目录以保留本轮记录）：

```powershell
.work/verification/venv/Scripts/python.exe .work/integration-evleven/run.py user-integration .work/verification/venv/Scripts/python.exe -m pytest tests/integration/test_evleven_r1.py -q -s -o addopts= -p no:cacheprovider --basetemp=.work/integration-evleven/user-integration-tmp --junitxml=.work/integration-evleven/user-integration.xml
```

## 分项结论与边界

| 验收面 | 结论 |
|---|---|
| 协议互通 | 真实 stdio、2024-11-05 初始化、发现、调用及正常收尾通过 |
| 业务集成 | 固定 R1 wheel 的记忆创建、ID 读取、词法查询及合法空结果通过 |
| 治理路径 | 真实策略拒绝、批准前零执行、审批拒绝/TTL/取消、失败传播和审计一致性通过 |
| 跨会话持久化 | 双方进程重启、全新 B 会话、无 A 历史、真实 R1 持久化内容恢复通过 |
| 真实模型验证 | **未验证**；规划与回复组织为确定性替身，不代表自主智能或语义理解 |

剩余限制：仅 Windows x64/Python3.13 本地单信任域；没有远程 CI、Linux/macOS、HTTP MCP、生产认证或多租户安全验收。MCP 实现仍为协议子集，不宣称支持全部 server-initiated 请求、分页或所有 JSON Schema 组合。仅接入上述四项；导入、永久删除、媒体、备份等工具未开放。R1 当前文本后端是 FTS5/字面匹配；无 embedding 模型，无语义质量结论。严格 schema 子集可能拒绝 R1 可选自由 metadata 字段，不绕过校验扩展此次最小参数范围。

同步工具无法安全强停的原限制保留；传输退出、任务取消、tool.error/partial 都不等于副作用撤销。无自动写重试、通用幂等键或恰好一次保证。正常关闭及隔离锁注入通过，不扩大为任意磁盘损坏、断电恢复或长时间生产 SLA。
