# PyHarness × evleven 本地候选版与独立安装验收

2026-09-24。STATUS / SUMMARY：**本机功能交付验收通过；真实模型及跨机器能力未验证**。新增安装包装、终端交互和安装验收，没有修改核心业务实现。缓存边界偏差如实列于末节，不能宣称全轮所有临时写入均留在 D 盘。

## 1. 固定版本、检查点与产物

实际仓库 `D:\zinuotiger-workspace\repositories\pyharness`；remote `https://github.com/zinuotiger/pyharness.git`；main。起始、最终 HEAD 均为 `41d74db15f47bee5813976bd11435dff1be166ef`，工作树有原有未提交补丁及新增交付文件；没有提交、push 或发布。

起始核对上轮 `final-tested-files.json`：**421 文件全部哈希一致**。将当前全部 424 个非忽略文件（含报告/STATUS）保存为可恢复的 `checkpoint.zip`，逐文件重新读取校验。以下 E 指 `.work/release-candidate/20260924T053750/`：

- E/checkpoint.json：完整起始文件哈希、HEAD、全部未提交状态。
- E/checkpoint.zip：SHA-256 `854fbb72f21a13c970861bfddac2d3077b06352710a5294d93c527127d3af2e1`。未收录忽略的环境、缓存、数据库或个人材料。恢复时解压到新检查目录比对，不覆盖现有成果。
- 分发包 SOURCE-STATE.json：构建时完整非忽略文件哈希和状态；SHA-256 `39a1091b62c6e2dbbec29f72739cd449b1db7a8da505b759e47bb46cc22b15ff`。
- E/final-state.json：报告/STATUS 收尾后的完整文件状态；避免文档自身哈希递归，不改动已封装产物。

原有修改全部保留：CI、配置、MCP、会话、队列、工具执行、engine、持久化、两个单测，以及上轮新增报告、脚本和集成测试。相对上轮 421 个被测文件，仅 `.gitignore` 增加本轮工作目录忽略项；所有旧核心实现、测试和集成脚本未变，历史报告未覆盖。

**主要交付目录：`dist/local-candidate/20260924T053750-final/`**。同级 ZIP：`20260924T053750-final.zip`。中间失败候选保留，不作为交付版本。

| 文件 | SHA-256 |
|---|---|
| PyHarness 0.1.0 wheel，677120 bytes | `eed50e7e020b8cb1b85524d0d6f6ddaacbc9c1dcf995c7c60e1c4b7d80b4fd26` |
| 固定 evleven R1 1.1.0 wheel，64041 bytes | `40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e` |
| R1 原 delivery-manifest.json | `73b0614848d54546d4d1cf89e6f37649a9126e516eec0d2dc9b67ca1a07bdc80` |
| 分发 SHA256SUMS.json | `e7a86645898f944af3d216235340cf2cbe0a930e022a006a7d5503ddaca519cd` |
| 最终 ZIP | `92d160e87556c49d4712bc94fbd31ac7c7f211bd3750d358df77bd2f3abd5675` |

PyHarness 从当前代码重新构建，其 wheel 与上轮哈希相同；逐个比较包内 `pyharness/` 文件与当前源码一致。R1 使用已确认并复制到本仓库的原 wheel，未重新构建或换版本。分发包仅含静态文件、wheel、依赖清单、模板、中文说明和校验和，不含 runtime/环境/验收数据。包内没有 `.work` 缓存。见 E/artifact-check.json。

## 2. 改动和架构影响

原 `scripts/evleven_integration.py` 是**自动化验收程序**：预设场景、自动测试审批、固定阶段，不是用户交互入口，且绑定旧虚拟环境及源码路径。本轮保留它，构建时生成便携路径副本；只替换 bundle 路径、隔离解释器和安装校验，替换点不唯一即构建失败。

FILES CHANGED：新增 `scripts/build_local_candidate.py`、`scripts/verify_local_candidate.py`、`scripts/local_candidate/`；更新 `.gitignore`、STATUS 和本报告。没有新增业务核心补丁。

- install.ps1：检查清单，创建两套 venv，非 editable 安装，约束依赖，核对 import 和安装字节。
- start.ps1 / trial.py：终端交互包装，通过原公开 HTTP API→Agent→审批→MCP 执行。用户输入 save/read/search/deny，输入 yes 批准；history/audit 查看记录，new 新会话，quit 正常停止。
- check.ps1：复用真实 R1 两阶段跨进程恢复，另跑 acceptance.py 的审批拒绝/取消/超时及启动失败。
- integrity.py：拒绝源码/旧环境导入，比较安装文件与 wheel，核对全部安装依赖版本。
- model_preflight.py：只验证显式模型模板和指定环境变量存在性，不连接模型、不加载凭据文件、不打印值。

ARCHITECTURE IMPACT：无 Agent 平台、客户端或 Provider 重写。入口涉及进程生命周期，按 CORE-02 高影响档跑全量回归，另补独立安装集成。CND-01/02 验证不同数据目录隔离、同一试用数据 OS 锁第二进程被拒且原进程正常退出；CND-04 包含路径包含约束、模型非回环/秘密字面量负例；CND-05 沿用关闭超时载体契约；CND-08 通过真实请求链路留证。没有新增 GUI/浏览器脚本，因此没有新的浏览器渲染结论。

## 3. 独立环境与导入来源

最终 ZIP 解包到 **E/最终 解包 验收/**，中文和空格路径已实际执行。两套新 venv 均由安装器创建，没有复制旧环境；安装/验收 cwd 在解包目录，交互子进程 cwd 在 E/interactive-final，均不在源码根目录。

Python 使用 `-I -B -X utf8`，PYTHONPATH 为空且被隔离模式忽略。实际 sys.path 只含已有 CPython 标准库、新 venv/site-packages，无源码根、旧 venv 或用户 site-packages。

| 项目 | 实际结果 |
|---|---|
| 解释器 | CPython 3.13.13 / Windows x64；使用已有 Python，没有下载解释器 |
| 安装器 | uv 0.12.12；实际从 Windows PowerShell powershell.exe -NoProfile -File 调用 |
| PyHarness 来源 | `E/最终 解包 验收/runtime/harness-venv/Lib/site-packages/pyharness/__init__.py` |
| evleven 来源 | `E/最终 解包 验收/runtime/evleven-venv/Lib/site-packages/evleven/__init__.py` |
| PyHarness 依赖 | 18 个发行包；fastapi 0.141.1、uvicorn 0.52.4、pydantic 2.13.5、httpx 0.28.1 |
| evleven 依赖 | 38 个发行包；mcp 1.30.0、uvicorn 0.53.0、pydantic 2.13.5、numpy 2.5.3 |
| 完整版本和来源证据 | E/install-final.log、解包 runtime/logs/installed.json；分发内两个 constraints 文件 |

不从 evleven 源码目录导入业务函数，使用独立 Python 模块入口和 stdio MCP。实际协商版本 **2024-11-05**；仅暴露 memory_create/read/search/delete，均为本地高风险审批，delete 策略禁止。真实运行时、授权、协议和持久化；模型仅是确定性命令规划替身。

## 4. TESTS / TEST RESULTS / EVIDENCE

| 检查 | 最终结果 | E 下原始证据 |
|---|---|---|
| 重新构建、产物与源码核对 | exit 0，wheel 字节一致 | build-final.json/.log，artifact-check.json |
| 中文/空格路径独立安装 | exit 0，52.066 秒；两套 uv pip check 通过 | install-final.json/.log |
| 真实 MCP 写/读/拒绝/双方正常停止重启/新 B 会话读回 | 两阶段通过，正文独立哈希相等；exit 0 | check-final.json/.log；解包 runtime/checks/run-6f5ff1543f42433e94f5641c2ecc6951/ |
| 审批拒绝、取消、审批超时、服务缺失 | 4 场景通过；拒绝前后原操作发送 0，真实 MCP 查询确认未写入；缺服务明确失败 | 解包 runtime/fault-checks/e7c288645ffa496498dce3f8aedf40ef/results.json |
| 实际交互输入 A→关闭→B | 中文内容保存/批准/读取/删除拒绝/audit/审批拒绝/新会话读取/search/history 通过 | interactive-final/interactive-A.log、interactive-B.log、cross-session.json |
| 交互/预检负例/安装缺失/跨进程锁 | 10 项驱动检查通过，含预期非零负例 | interactive-final/commands.json、各子进程日志、lock-owner.log |
| 源码完整回归 | **2221 总数：2218 pass + 3 原 skip，0 failure/error**；覆盖率 82.93%，门禁 75%；exit 0，146.161 秒 | source-full.json/.log、full.xml、full-coverage.json |
| 静态编译和 diff 空白检查 | 均 exit 0 | syntax-final.json/.log、diff-check.json/.log |
| 独立空白用户试用安装和就绪→退出 | 安装 exit 0，52.666 秒；READY→quit exit 0，4.669 秒；没有创建记忆 | install-user.*、user-ready.*、user-ready-command.json |

全量回归包含授权、持久化、错误处理和既有 16 项真实 R1 测试。3 项 skip 仍为原有 Windows POSIX 权限位/ACL/符号链接限制；没有删除测试、降低门禁或扩大跳过。**94 项是历史相关回归，未当作完整测试。** 没有配置或声称运行 Ruff/mypy；静态检查结论限定为 Python 编译与 diff 检查。

交互 A 为 `s-6c714491f5e1`，B/记忆/请求/call/task ID 详见 cross-session.json。B 首项 MCP 请求为 memory_read，只传 memory_id；所有 B 请求参数均不含 A 的保存正文。返回内容与独立生成的合成输入精确相等，模型无固定答案。A 请求中无 memory_delete、无审批拒绝后的第二次 memory_create。正常退出码 0，已验证 HTTP 端口释放及所记录 MCP PID 不再运行。

最终自动检查与交互检查并行运行在不同数据目录；没有串数据。交互试验属于程序化驱动真实终端输入，不声称完成了人工 GUI 使用或真实模型规划。

## 5. 实际安装与用户操作

主交付是上面的 dist 目录及 ZIP。已另解包并安装一套**专供用户**的目录，与自动验收目录分开；只执行就绪后 quit，不灌入验收记忆。以下安装参数已实际使用：

```powershell
$c = 'D:\zinuotiger-workspace\repositories\pyharness\.work\release-candidate\local-trial-20260924T053750'
& "$c\install.ps1" -PythonPath '%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe' -UvPath '%USERPROFILE%\AppData\Local\hermes\bin\uv.exe'
& "$c\start.ps1"
```

当前已安装且服务已停止，直接执行启动行即可。新机器须已有 Python 3.13、uv、可写 D 盘路径及依赖下载能力，**不是完全离线/零依赖包**。移动到其他路径后重新安装，不复制/搬动 venv。不改变执行策略或全局环境变量。

看到 memory> 后逐行输入，每次审批由本人选择：

```text
save 合成记忆：蓝色纸鹤周三测试
yes
read last
yes
deny last
audit
quit
```

再运行 start.ps1，在新会话输入 `read last`、`yes`，检查正文与来源 memory.id。`last` 只记录 ID，不缓存答案。还可 `search 关键词`、`history`、`new`；正常关闭用 quit。用户数据在 `$c/runtime/user`，R1 数据库为其中 `remote/memory.db`；记录在 `logs/<run>/`，不默认清空。

快速检查入口为 `& "$c\check.ps1"`，其数据在 runtime/checks、fault-checks，与用户目录分离。它已在相同字节的“最终 解包 验收”运行通过，但属于自动验收，不等同于用户交互。安装包内 README-中文.md 有完整步骤。

重现安装版终端验收：运行 `scripts/verify_local_candidate.py --candidate <新的验收解包目录> --evidence <新的.work/release-candidate证据目录>`。具体 argv/cwd/input/退出码见 E/interactive-final.json 与 commands.json；不要用用户试用目录运行自动驱动。

## 6. 证据复用与失败收敛

沿用：上轮存储失败/队列拒绝/isError/传输断开/写入结果未知/超时取消不代表撤销的证据，相关代码和依赖一致，本轮源码全量回归再次覆盖自动用例。不重复 180 分钟实验。

重新验证：构建、独立安装、真实 import、中文/空格路径、安装版 MCP 主链路、审批各结局、异常启动、资源清理、交互输入、跨会话恢复、模型静态预检和双进程试用锁。

FAILURES：开发环境没有 build 模块的首次构建失败，改用现有 uv build。早期 PowerShell 5 包装遇到 Get-FileHash 不可用、中文清单 UTF-8 误解码、uv 绝对含空格参数路径被截断，分别采用 .NET SHA256、显式 UTF-8、包装器切到自身目录后传相对 uv 参数修正；因此仍不依赖调用者 cwd。安装日志编码改为 Python -X utf8。中间失败记录保留在 build.log、install-rc1/rc2/rc3*.log，最终重新构建、解包、安装和验证，不将修补中的目录冒充最终 ZIP。没有为刷绿修改业务测试断言。

## 7. 模型边界和剩余条件

| 类别 | 结论 |
|---|---|
| 协议互通 | 安装版真实 stdio MCP / 2024-11-05，通过 |
| 业务集成 | 合成记忆写入、ID 读取、确定性关键词查询通过；不是语义理解证明 |
| 治理路径 | 真实审批与策略拒绝通过，原操作未在拒绝后发送 |
| 跨会话持久化 | 双方正常停止重启后，新会话从 R1 数据读取，通过 |
| 真实模型 | **未验证**；仅确定性命令规划替身及静态预检 |

模板分别为 A deterministic、B local、C external，均不含密钥。既有 Provider 为 OpenAICompatAdapter / chat/completions，使用 llm.model/base_url/api_key env 引用等现有配置；没有模型网关。预检只检查指定字段与指定环境变量是否存在，不联网、不读凭据文件、不找 Codex token、不打印值，退出 0 也不代表推理已通过。模板不是自动启用模型的开关。

真实验收最低条件：模型来源/真实 model ID、兼容工具调用接口、显式凭据、现有 MCP allowlist、用户审批、外部费用单独授权。建议上限 6 次请求、512 输出 tokens/次、30 秒、关闭 fallback 和重试；请求数是建议而非计费门禁。需验证工具选择、审批等待、拒绝/取消、忠实结果和新会话恢复。本轮不下载聊天/向量模型、不发真实推理、不调用付费 API；不将替身称为自主智能。

## 8. BOUNDARIES / CONSTRAINTS / KNOWN LIMITATIONS

- 仅本机 Windows、Python 3.13.13、固定依赖、单信任域。其他机器/平台、GUI/Qt/WebView、生产或并发用户未验证；入口是交互终端。
- 超时、断开、任务取消不保证远端已停止或副作用撤销，不保证恰好一次。写入结果未知时先查再决定，不盲目重试。正常 quit 清理通过，强杀终端/断电未验收；同步工具强制停止限制保持不变。
- 故障检查出现可选本地 session.fts_query 未注册提示；四项指定 MCP 工具正常，未把该可选本地能力算作本轮通过功能。
- **缓存边界偏差**：定位 uv 路径时，一次直接 uv pip install 诊断没有设置 UV_CACHE_DIR，输出 Prepared 1 package；默认位置经只读 uv cache dir 确认为 `%USERPROFILE%\AppData\Local\uv\cache`。可能使用/新增默认缓存，不能声称所有临时写入均在 D 盘。没有扫描或删除外部共享缓存，以免影响其他任务。正式安装器和最终安装均显式指向 D 盘各自 runtime/cache、tmp，安装环境和证据均在本仓库。
- 除上述缓存偏差，没有修改其他仓库、工作台根目录、系统设置、全局 Git 配置；本轮直接复用本仓库已核实的 R1 包，没有再读取 evleven 源码；没有加入 rag-engine。

CONVERGENCE：交付/独立安装/交互缺口已补齐，最终包对应实际被测版本。REMAINING WORK：真实模型、其他机器和生产级保证未验证；本轮停止新增功能，不自动进入下一轮加固。
