# 当前仓库独立验收、缺陷修复与本地试用交付

验收日期：2026-09-23（Asia/Shanghai）。仅依据本仓库文件及本轮实际运行证据。

## STATUS / SUMMARY

**VERIFIED：受控离线边界内的本地验收与缺口补齐完成。** 2026-09-23 后续缺口核对新增 5 项真实集成验证，修正两处超时/取消反馈文案，相关回归 **94 passed / 0 failed / 0 skipped**。详见文末“验收缺口核对与补齐”；未重新进行全面审计。

上一阶段全量收集 **2,198** 项：**2,195 passed / 0 failed / 0 errors / 3 skipped**，覆盖率 **82.75%**，现有 CI 75% 门禁通过。该统计对应补齐前的文件清单，不冒充修改后的新全量运行；未变化部分沿用其有效证据。三个跳过沿用既有 Windows 条件，未扩大 skip、未删除测试、未降低有效断言或门禁。

已验证原程序 HTTP API 的会话创建、消息提交、任务执行、结果读取，以及正常退出后第二个独立进程读回。模型为明确标识的确定性替身，**不是实际模型效果验收**。MCP 使用本轮启动的仓库 echo 服务，经过真实配置装配、审批、工具执行及审计。

## 起始版本、最终版本与成果保护

| 项目 | 实际值 |
|---|---|
| 工作目录 / Git 根目录 | `D:\zinuotiger-workspace\repositories\pyharness` |
| origin fetch / push | `https://github.com/zinuotiger/pyharness.git` |
| 分支 | `main` |
| 起始 HEAD | `41d74db15f47bee5813976bd11435dff1be166ef` |
| 起始工作树 | `git status --porcelain=v1` 为空 |
| 最终 HEAD | 与起始一致；未 commit、未 push、未发布 |
| 上一阶段全量被测版本 | 上述 HEAD 加原验收工作树补丁及两个新增 Python 文件 |
| 当前最终被测版本 | 上一阶段版本，加 tools_executor 两处反馈文案修正及 test_verification_gaps.py；最终文件清单、94 项适用回归见文末 |

上一阶段全量被测文件清单保存在 `.work/verification/tested-files-sha256.json`，规范化清单 SHA-256 为 `fc7647fad2a5e65c3d7b6932bf92148e028dc04191f133f9a0518171582d0119`。清单涵盖原有 tracked 文件和两个新增 Python 文件；本报告在验证后生成，不参与自引用哈希。该清单及原始证据均保留。

当前最终工作树：修改 `.github/workflows/ci.yml`、`.gitignore`、`pyharness/core/session.py`、`pyharness/core/task_queue.py`、`pyharness/core/tools_executor.py`、`pyharness/persistence.py`、`tests/unit/test_events.py`；新增 `scripts/verification_local.py`、`tests/integration/test_verification_runtime.py`、`tests/integration/test_verification_gaps.py`、本报告。环境、日志、缓存、测试数据库均在被忽略的 `.work/verification/` 中。

未 reset、clean、覆盖历史报告、修改远程或 Git 全局配置；未访问其他仓库；未扫描 C 盘旧版本。读取了已解析的现有 Python 可执行程序以创建环境，没有安装系统服务。

适用规则检查：工作目录及祖先未发现 AGENTS.md，仓库隐藏文件搜索也未发现嵌套 AGENTS.md；读取 CLAUDE.md 和 `.ai-coding/PROTOCOL.md`，参考 README、LIMITATIONS、M5.5 验收记录、S6 汇总、约束说明、相关 session/persistence/task_queue 规格、CI 和依赖配置。历史报告仅用于识别契约与限制，未充当本轮通过证据。没有现成 STATUS 文件或旧版 `reports/REPO-VERIFICATION.md` 需要覆盖。

## 环境与执行隔离

依赖安装采用锁文件：`uv sync --frozen --all-extras`，退出码 **0**。安装时进程级 `UV_PROJECT_ENVIRONMENT`、`UV_CACHE_DIR`、`TEMP`、`TMP` 分别指向本仓库 `.work/verification/venv`、`uv-cache`、`temp`；无全局环境修改。

| 工具 / 依赖 | 实际版本 |
|---|---|
| OS | Windows 11，10.0.26200 |
| Python | 3.13.13，64 bit |
| uv / Node | 0.12.12 / v24.14.0 |
| pytest / pytest-asyncio / pytest-cov | 9.1.1 / 1.4.0 / 7.1.0 |
| pydantic / PyYAML | 2.13.5 / 6.0.3 |
| httpx / FastAPI / uvicorn | 0.28.1 / 0.141.1 / 0.52.4 |
| PySide6 / qasync / pywinpty | 6.11.2 / 0.28.0 / 3.0.5 |

完整依赖列表见 `.work/verification/dependencies.log`，版本自检见 `versions.log`。Qt 使用 `QT_QPA_PLATFORM=offscreen`，可选依赖已安装，未因缺少 native/PTY 依赖跳过整层测试。

执行器 `.work/verification/run.py` 为每次运行记录实际参数、cwd、开始时间、退出码、耗时（同名 `.json`），完整输出保存在同名 `.log`。它只在子进程中重定向 home、应用数据、TEMP、缓存、Hypothesis 和 coverage 路径，清除继承的 PH 配置、API 密钥及代理变量。测试 Python 进程额外使用 `sitecustomize` 拒绝非回环 socket 连接；这不是 OS 沙箱，不声称拦截任意原生程序出网。

测试前检查了 conftest、外部调用、子进程和启动脚本。原 `scripts/e2e_real_llm.py`、真实 DeepSeek/搜索探针未执行。未调用付费 API，未下载模型，未使用个人数据，未安装 Docker/WSL。HTTP 仅绑定 `127.0.0.1` 动态端口，MCP 为 stdio；只关闭本轮创建的服务和子进程。

## 实际命令与结果

以下命令都从上述仓库目录运行。`P` 表示 `.work/verification/venv/Scripts/python.exe`。测试等命令实际由 `P .work/verification/run.py <标签> <命令…>` 包装；精确展开的 argv 见相应 JSON，便于复跑并保持隔离。

| 标签 | 实际命令（P 简写） | 退出码 / 结果 |
|---|---|---|
| install | `uv sync --frozen --all-extras` | 0 |
| baseline | `P -m pytest tests/unit tests/e2e tests/security tests/acceptance tests/invariants tests/integration/test_shell_service_contract.py tests/integration/test_mcp_stdio.py -q -p no:cacheprovider --basetemp=.work/verification/baseline-tmp --junitxml=.work/verification/baseline.xml --cov-fail-under=75 --cov-report=json:.work/verification/baseline-coverage.json` | 1；2,187 pass / 1 fail / 3 skip，82.63% |
| independent-red | `P -m pytest tests/integration/test_verification_runtime.py -q -p no:cacheprovider -o addopts= --basetemp=.work/verification/independent-red-tmp --junitxml=.work/verification/independent-red.xml` | 1；当时 4 个新增用例中 3 pass / 1 fail |
| order-red | `P -m pytest tests/unit/test_events.py::test_register_plugin_type_and_validate tests/invariants/test_inv_doc_anchors.py::test_event_and_envelope_counts_in_docs_match_code -q -o addopts= -p no:cacheprovider --basetemp=.work/verification/order-red-tmp` | 1；1 pass / 1 fail |
| admission-red | 新集成文件的 `test_cancelled_admission_cannot_execute_behind_running_task` | 1；未完成入队的任务提前执行 |
| storage-recovery-red | 新集成文件的 `test_suspended_store_recovery_never_acknowledges_a_lost_message` | 1；成功提交的消息不在磁盘日志 |
| admission-waiter-red | 新集成文件的取消入队用例追加等待者检查 | 1；入队取消后仍 BUSY |
| terminal-storage-red | 新集成文件的 `test_storage_failure_during_task_settles_waiter` | 1；加强拒写传播后，等待者超时 BUSY |
| targeted-complete | `P -m pytest tests/integration/test_verification_runtime.py tests/unit/test_task_queue.py tests/unit/test_events.py tests/invariants/test_inv_doc_anchors.py tests/unit/test_session.py tests/unit/test_persistence.py tests/e2e/test_persistence_failure_e2e.py -q -o addopts= -p no:cacheprovider --basetemp=.work/verification/targeted-complete-tmp --junitxml=.work/verification/targeted-complete.xml` | 0；170 pass |
| final | `P -m pytest -q -ra -p no:cacheprovider --basetemp=.work/verification/final-tmp --junitxml=.work/verification/final.xml --cov-fail-under=75 --cov-report=json:.work/verification/final-coverage.json` | **0；2,195 pass / 0 fail / 3 skip，82.75%** |
| order-final | 与 order-red 相同节点，独立临时目录 | 0；2 pass，确认不依赖默认收集顺序 |
| final-build | `uv build --python .work/verification/venv/Scripts/python.exe --out-dir .work/verification/final-dist` | 0；sdist + wheel |
| final-compile | `P -m compileall -q pyharness scripts/verification_local.py tests/integration/test_verification_runtime.py` | 0 |
| ui-extract / ui-check | `P scripts/extract_ui_js.py .work/verification/ui.js`；`node --check .work/verification/ui.js` | 各 0；提取 48,837 字符 |
| cli-help | `.work/verification/venv/Scripts/pyharness.exe --help` | 0；现有 16 个子命令可加载 |
| api-smoke-final | `P scripts/verification_local.py --smoke --data-dir .work/verification/api-smoke-final-data` | 0；真实本地 HTTP + 两次独立进程 |
| ci-import-red / ci-import-correct | `P -c "import pywinpty"` / `P -c "import winpty; print(winpty.__file__)"` | 1 / 0 |
| artifact-review-final | `P .work/verification/summarize.py` | 0；JUnit 汇总、包内容和源码一致性 |
| diff review | `git diff --check` | 0；只有 Git 原有 CRLF 转换提示 |

基线命令覆盖修改前已有的全部 2,191 个测试，按层指定次序；没有忽略原有文件。该顺序暴露了既有全局注册污染。新增测试当时单独运行以保留 RED 证据。最终使用默认全量发现，包含全部 7 个新增集成测试；又用原失败顺序的两个节点确认污染已修复。

最终分层统计（从 `final.xml` 读取）：

| 层 | 通过 | 失败 | 跳过 |
|---|---:|---:|---:|
| unit | 1,874 | 0 | 3 |
| e2e | 51 | 0 | 0 |
| integration | 13 | 0 | 0 |
| security | 55 | 0 | 0 |
| acceptance | 9 | 0 | 0 |
| invariants | 193 | 0 | 0 |

全量 pytest 实际测试耗时 96.447 秒；包装器总耗时 102.197 秒。核心脊柱相关文件 coverage 均超过文档要求的 80%；例如 persistence 85.66%，其余具体值见 `summary.json` 的 `final.core_coverage`。未另设或放宽门禁。

## FAILURES / 修复与回归证据

### 1. 入队拒绝、取消与执行之间存在窗口

真实 `SessionLog` 已终态时，`TaskQueue.submit()` 抛 EVT-104，但 `_q` 残留 `t-1`（depth=1）。另一个真实总线探针阻塞第二任务的 `task.enqueued` 处理；第一任务完成后，原队列泵仍执行第二任务，而第二个 submit 尚未返回。

修复：继续以队列条目预留容量，但泵只消费 `enqueued_seq` 已确认的条目；拒写/取消删除预留，结算失败、尝试记录失败终态并唤醒泵；shutdown 同样唤醒入队等待。追加等待者检查发现初版修复遗漏 Future 清理（`admission-waiter-red`），已在最终补丁收口。回归验证零提前执行、无残留等待条目、取消结果可读取且 Future 移除。

### 2. 存储暂停恢复时，新消息出现虚假成功

真实 store 写通道注入 OSError，重复写失败进入 suspended。恢复底层写句柄后立即提交新消息：原逻辑的存储订阅拒写被总线隔离，随后 flush 只恢复旧行，调用方却收到成功，新消息在 replay 中不存在。`storage-recovery-red.log` 留有失败断言。

修复：`SessionStore.ensure_writable()` 集中判定暂停/已关闭；`SessionLog.append()` 在分配 seq、写入内存和分发前调用该检查，store 自身写入也复用。暂停期返回 PERS-202；正常 flush 恢复后再重试。新增回归检查恢复后的消息真实落盘且恰好一次。没有修改日志格式或执行迁移。

### 3. 运行中拒写传播后的任务结算缺口

在任务等待模型期间使真实存储进入故障状态；加强写入拒绝后，终态写失败会跳过 `_settle`，导致 `wait_for` 一直 BUSY。这是在本轮初步修复后由追加用例暴露的未收敛路径，不把它算作原始全量基线的失败。

最终修复：成功终态遇 PERS-202 仍传播并转失败；失败终态自身无法写盘时告警，但必须向等待者结算失败。关段失败释放内存段标记并告警，不伪造磁盘证据。回归返回 `ok=False/code=PERS-202`，Future 和执行子任务引用清理。故障磁盘上缺少终态/段结束事件仍明确列为风险。

### 4. 原有测试全局注册污染

原测试注册 `plugin:echo.ping` 后不清理，导致后续文档锚点测试读到 EVENT_TYPES=78。独立解释器实测生产注册数确为 77，所以没有修改文档或放宽计数断言。只对该测试新增 finalizer 清理其合成类型；全部原有断言保留。`order-red` → `order-final` 为 1 fail → 2 pass。

### 5. CI 的 pywinpty 自检导入名错误

锁文件安装的 `pywinpty==3.0.5` 已存在，但 `import pywinpty` 实际抛 ModuleNotFoundError；`import winpty` 成功。只把 CI 自检改为正确模块名。远程 GitHub Actions 未执行，本项结论限定为本机重放该自检命令。

过程问题另外保留：一条 PowerShell 内联统计命令因引号转义失败，改为文件脚本；首次文件哈希汇总因 Git 对中文路径进行引用而失败，改用 `git ls-files -z` 正确读取。两者均为验收辅助脚本问题，最终 `artifact-review-final` 通过。新增取消测试曾错误地把模型调用次数固定为 1，实际正常任务还会调用 mini 自动标题；修正为完成第一任务后的实际计数不再增加，并增加 `t-2` 无 `task.started` 断言，没有删除或弱化原有测试断言。

## 已验证主流程与组件边界

| 验收面 | 本轮证据 | 结论边界 |
|---|---|---|
| 会话、消息、任务、结果 | 新集成 API roundtrip；`api-smoke-final` 的 HTTP 200、task.completed、结果文本 | 原 DesktopApp → ApplicationService → 引擎/队列/JSONL；只替换模型 |
| 正常重启 | 试用 write 子进程优雅关闭，read 子进程重开同一目录，逐消息相等；原 restart E2E | 正常退出恢复，未模拟断电 |
| 存储失败 / 队列拒绝 | API 写失败 500/PERS-202；队满 503/QUE-001，模型无执行；暂停恢复用例 | 无成功响应冒充拒绝；失败请求不承诺事务回滚 |
| 工具 allow / deny / approval | `tests/e2e/test_governance_e2e.py`、55 security、9 acceptance | 真 guard/governance/executor/store；拒绝后文件不变，审批重判及 receipt 可核验 |
| 审计一致性 | acceptance causal_chain、denied、reconcile、receipt；新增 MCP 审计 findings 为空 | 测试范围内一致；不是所有插件的形式化证明 |
| 取消 | 真实 TaskQueue.cancel E2E、入队取消新增用例、执行器取消测试 | 等待者终结、执行引用释放、段/证据路径有测试 |
| 超时 | 原 executor `test_timeout_tlb805`、`test_timeout_evicts_pool_not_reused`；queue BUSY | 超时后不复用旧线程池；不等于能杀死同步线程 |
| 重试 | 原 LLM fallback 429×3→成功、预算复查；存储恢复后重试新增用例 | 模型重试边界为替身，存储重试为真实 JSONL；不承诺请求 exactly-once |
| MCP | 既有 stdio 集成 + 新 `test_mcp_configuration_uses_real_process_and_closes_it` | 真子进程 initialize/list/call，审批前无结果，批准后回显中文，审计无 findings，shutdown 后进程退出 |
| 资源生命周期 | 原 proc lifecycle/kill、引擎关闭、共享总线和多会话隔离测试；本轮 MCP/队列故障用例 | 仅本轮自建进程与临时资源；不终止其他会话 |

MCP 服务为 `scripts/mcp_echo_server.py`，不是外部生产服务器。单元测试中存在 fake store/runner/guard，不能把这些单测单独视为真实系统验收；上述新增集成及现有 E2E/acceptance 使用真实关键运行时实现。

## 实际验证过的本地试用入口

在 PowerShell 中执行：

```powershell
Set-Location D:\zinuotiger-workspace\repositories\pyharness
.\.work\verification\venv\Scripts\python.exe scripts\verification_local.py --smoke --data-dir .work\verification\trial
```

脚本复用原程序 API，不创建另一套业务服务：绑定空闲回环端口，创建会话，POST messages，读取 timeline/messages，检查带 `[DETERMINISTIC TEST MODEL]` 标识的预期回复，然后正常关闭。第二个独立 Python 进程读取同一会话并断言消息一致，结束时打印 PASS；失败断言会使命令非零退出。

最终实跑目录为 `.work/verification/api-smoke-final-data`，write/read 端口分别为 **52413 / 61246**，会话 **s-9c89dace04ad**，任务 **t-1**。两个端口均在试用结束后释放；再次运行自动选择新端口、新会话。

如需保留 API 供本地手动试用：

```powershell
.\.work\verification\venv\Scripts\python.exe scripts\verification_local.py --serve --data-dir .work\verification\trial-manual
```

它输出本机 URL 和本轮生成的测试 API token。访问 POST `/api/sessions`、POST `/api/sessions/{sid}/messages`（JSON `{"text":"你好"}`）及 GET messages/timeline 时带 `X-PyHarness-Token`。Ctrl+C 退出。受控自动流程已验证；未把浏览器交互或完整人工 UI 操作包装成已验收。

上一阶段构建产物：`.work/verification/final-dist/pyharness-0.1.0-py3-none-any.whl` 和对应 tar.gz。wheel SHA-256：`7b4d00f287c2e3845cca5ae520db3d5581df73c443e88c77339ff9df6d72901f`。当时包检查确认 UI 资源存在、三个修改生产文件与当时源码逐字节一致、wheel 的 97 项与 sdist 的 416 项均不包含 `.work/`。未发布这些产物。后续补齐未重建安装包，旧 wheel 不含本次反馈文案修正；当前本地试用从仓库源码运行。

## ARCHITECTURE IMPACT / CONSTRAINTS / CONVERGENCE

影响面按 Protocol CORE-02 判为高影响：涉及事件写入、共享队列状态、并发、持久化和资源生命周期，触发 CND-01/04/06/08。因此在相关回归之外执行全量验证，含多会话隔离、参数拒绝、派生重建、重启和真实调用链。未引入 Agent OS、新平台、事件字段、破坏性迁移或不兼容接口改造。EventBus 的订阅异常隔离契约保持原样。

编辑前核对了相关定义和规格。改动属于已有“拒绝不得执行”“落盘成功才确认”“失败不得悬挂”契约的修复；测试计数污染只修 fixture 生命周期。`.gitignore` 仅新增验收目录忽略项。历史报告、README、LIMITATIONS 均未覆盖。

上一阶段收敛顺序：失败复现 → 最小修复 → 追加边界检查 → 170 项相关回归 → 2,198 项全量 → HTTP 双进程冒烟、构建、静态检查、包检查及 diff 复核。通过结论对应各自已记录哈希的代码；后续修改及其回归另记于文末。

## KNOWN LIMITATIONS / 未验证项与剩余风险

- **真实 LLM、语义能力、外部搜索、生产 MCP、付费 API 未验证**；按用户边界不调用。确定性模型不能作为真实模型可用性或质量证明。
- 原生 Qt 测试在 offscreen 下实际运行；浏览器/WebView2/Qt 人工交互、打包 exe、长时间稳定性没有验收。UI 仅做原有测试、根页面 HTTP 与 JavaScript 语法检查。
- 三个原有 skip：POSIX 凭据 mode 检查、spill 0600 检查不适用于 Windows；符号链接逃逸用例受当前用户权限阻塞。未提权或修改系统设置来刷绿。
- 普通异步事件仍采用原有攒批策略；本轮验证正常重启，不承诺断电、磁盘硬故障下所有事件耐久。磁盘故障时无法保证写出任务终态、segment.end 或治理工件，日志会告警，调用方获得失败。
- 写失败前已进入缓存/重试队列的旧事件可能随后恢复落盘；接口没有客户端幂等请求键，重试不承诺跨请求 exactly-once。队满返回 503 前，用户消息可能已追加，这属于本轮观察到的非事务边界；它不会伪造 task_id 或触发被拒任务。
- 同步 Provider 超时只驱逐执行线程池，Python 无法安全强杀旧线程；旧线程可能继续其已有副作用。测试证明后续调用不复用旧载体，不证明所有副作用均已回滚或停止。
- 本地启动有一条现有 `TLB-802 tool=session.fts_query` 日志噪声，随后任务实际完成；未据此宣称 FTS 功能通过，也未扩大范围重构该注册流程。
- 远程 CI 未运行；本机验证了相同依赖、测试、覆盖率及修正后的导入命令。验证脚本的出网保护不替代 OS 沙箱。

上述限制均已记录；本轮已证实且纳入范围的失败路径已修复并回归。保留全部本地证据供复核，不存在已知未记录的验收阻塞。

## 验收缺口核对与补齐（2026-09-23，延续原验收）

### 版本核对和初始分类

继续任务时先读取本报告、`summary.json`、`final.xml/.json`、`api-smoke-final.log` 及相关代码/测试。未发现独立 STATUS 文件，本报告 STATUS 为当前状态入口。工作目录、origin、main 分支及 HEAD 均未改变；开始补测前按原 `tested-files-sha256.json` 逐文件重算，差异为 **空列表**。因此没有重新建立环境、全面审计或重跑保存/重启入口。

| 用户验收项目 | 补齐前分类 | 有效证据或缺口 | 补齐后结论 |
|---|---|---|---|
| 保存失败不虚假成功 | 已有有效证据 | `test_api_storage_failure_and_queue_rejection_are_not_success`、`test_suspended_store_recovery_never_acknowledges_a_lost_message`；`final.xml` / `terminal-storage-red.log` / `targeted-complete.xml` | 沿用；相关实现未变化 |
| 队列拒绝、任务失败与用户反馈一致 | 已有有效证据 | `test_rejected_enqueue_leaves_no_waiting_task`、`test_storage_failure_during_task_settles_waiter`；`final.xml`；拒绝返回非成功、失败等待者释放 | 沿用；没有把任务会话完成当作每次工具都成功 |
| 保存后正常重启读取 | 已有有效证据 | `test_api_roundtrip_and_normal_restart`；`api-smoke-final.log/.json` 两个独立进程 | 沿用；入口及持久化未变 |
| 拒绝不执行、批准前不执行、授权审计一致 | 已有有效证据 | `test_e2e_deny_policy_geometry_zero_side_effect`、`test_e2e_deny_critical_still_has_file`、`test_e2e_human_approval_rejudgment`；`final.xml` | 沿用，并作为文案修复相关集成回归通过 |
| 审批拒绝、TTL 超时、取消后的真实落盘与清理 | 证据不足 | 原审批单测多使用内存 store；缺少完整服务中磁盘记录与实际文件副作用的联合验证 | 新增 3 个参数化集成用例通过 |
| 请求超时/取消后仍在运行及已发生的副作用 | 证据不足 | 原执行器验证驱逐、不复用、partial；尚未联合验证真实落盘反馈与延后文件写入 | 新增 2 个集成用例复现反馈缺陷，最小修复后通过 |
| 本轮任务、连接和子进程清理 | 已有有效证据，新增线程需补证 | 原 MCP 实进程关闭、HTTP 双进程退出、队列收尾有效 | 新测试各自 shutdown；继续运行的线程显式 release/join 并断言退出 |
| 启动→创建会话→消息→结果→关闭→重启历史 | 已有有效证据 | `scripts/verification_local.py --smoke`；`api-smoke-final.log`，会话 `s-9c89dace04ad` | 不重复执行；复用上文源码试用入口 |

四类关键项目中没有因外部服务而阻塞的补测；真实商业模型和外部生产 MCP 属既定范围外，不把它们标成通过。既有 Windows 平台不适用/权限跳过保持原记录。

### 补测与缺陷判定

新增文件：`tests/integration/test_verification_gaps.py`。使用现有 `ApplicationService`、装配器、引擎、队列、治理/审批、执行器、真实 JSONL 与隔离工作区。模型沿用脚本化 adapter，无付费调用；测试日志中的 `deepseek-chat` 名称及 `llm.usage.cost_est` 是配置标签/合成 usage，**不是实际模型或实际费用**。

1. **审批结束**：真实 `fs.write_file` 请求覆写预置 `ORIGINAL` 文件。审批等待时文件不变、队列暂停；分别执行人工 deny、真实 1 秒 TTL、取消任务。最终文件仍为 `ORIGINAL`，pending=0、定时器已清除、队列恢复、无成功工具结果及执行 receipt；磁盘事件与任务结果配对，reconcile findings 为空。
2. **持续副作用**：显式注册测试 Provider `fs.verification_effect`（不是生产工具效果验收），在真实线程先写 `BEFORE`，受 gate 控制继续等待。真实执行器超时或真实队列取消后，测试直接检查工作线程仍存活、未结束、被执行器弃用池引用跟踪；读取磁盘 `tool.error`/partial `tool.result`。释放 gate 后确实写为 `BEFORE+AFTER`，join 并断言物理线程退出。每个用例都在 finally 释放 gate、join 自建线程并 shutdown 服务。

最初 `gaps-red` 为 **2 pass / 3 fail**。其中审批取消失败是**新增用例预期不符既有契约**：`docs/specs/approval.py.md` APR-502 规定取消后置 denied 并释放等待，现有 `test_cancel_all_aprid_502_no_dangling` / `test_request_task_cancelled_no_dangling` 明确取消不生成审批裁决事件。实际磁盘已有 `system.cancelled`、`task.failed(reason=cancelled)`、`queue.resumed` 和 `segment.end`。因此仅纠正新增用例，强化检查这些真实终态、请求 state=denied、waiter.done、暂停/恢复严格配对；没有修改审批实现或任何既有测试。

纠正预期后的 `gaps-confirmed-red` 为 **3 pass / 2 fail**，两个失败均有真实文件副作用和线程证据，确认以下反馈缺陷：

- 超时只记录 `执行超时(1s)`，未向调用方说明同步工具可能仍运行。
- 取消记录 `已取消:执行被中断...`，与仍在运行、随后继续写文件的事实不一致。

最小修复仅改 `pyharness/core/tools_executor.py` 两处反馈文本：超时明确“已停止等待；工具可能仍在运行，已发生副作用未撤销”；取消明确“已取消等待：工具可能仍在运行，已发生副作用未撤销(partial)”。保留 TLB-805、超时前缀、ok=False/truncated=True、partial、取消重抛以及现有事件结构。未改变授权、并发控制、存储或资源生命周期。

### 实际命令与回归

继续使用原独立 venv、进程级缓存/数据配置、离线 socket 限制；未安装依赖。Python 3.13.13 / pytest 9.1.1，完整版本沿用上文。

下列 `P` 同上，均通过 `P .work/verification/run.py <标签> <命令>` 执行；精确命令、时间和退出码保存在每个标签的 `.json`，输出 `.log`，测试 `.xml`。各次 basetemp 和日志使用新标签，不覆盖旧证据。

| 标签 | 实际测试选择和参数 | 退出码 / 统计 |
|---|---|---|
| gaps-red | `P -m pytest tests/integration/test_verification_gaps.py -q -s -o addopts= -p no:cacheprovider --basetemp=.work/verification/gaps-red-tmp --junitxml=.work/verification/gaps-red.xml` | 1；2 pass / 3 fail；含上述新增用例契约误判 |
| gaps-confirmed-red | 同测试选择，独立 `gaps-confirmed-red-tmp` / `gaps-confirmed-red.xml` | 1；3 pass / 2 fail；生产代码修复前有效失败复现 |
| gaps-green | 同测试选择，独立 `gaps-green-tmp` / `gaps-green.xml` | 0；5 pass；3.65 秒 |
| gaps-regression | `P -m pytest tests/unit/test_tools_executor.py tests/e2e/test_governance_e2e.py tests/integration/test_verification_gaps.py -q -s -o addopts= -p no:cacheprovider --basetemp=.work/verification/gaps-regression-tmp --junitxml=.work/verification/gaps-regression.xml` | **0；94 pass / 0 fail / 0 skip**；8.91 秒（包装器 10.53 秒） |
| gaps-evidence-review | `P .work/verification/summarize-gaps.py` | 0；汇总测试、五份结构化证据、文件哈希并断言修改范围 |
| diff review | `git diff --check`；逐项阅读最终 diff 和新增测试 | 0；仅原有 CRLF 转换提示 |

按 Protocol CORE-02 判为**隔离改动**：单模块两处说明文本，没有修改公共字段、错误码、返回结构、授权、安全边界、并发控制、持久化策略或资源生命周期；执行器 76 项 + 治理 E2E 13 项 + 新集成 5 项回归。既有超时驱逐/不复用和取消 partial 测试保留并通过。不重跑无关联的保存恢复、全量覆盖率、构建和双进程入口，不把上一阶段覆盖率说成本次重测值。

最终五份 `gap-evidence.json`、合成文件和会话 JSONL 都位于 `.work/verification/gaps-regression-tmp/test_*/`；精确相对路径及结果收录于 `.work/verification/gaps-summary.json`。取消审批会话 `s-765d06824ca0`，持续副作用的 timeout/cancel 会话分别为 `s-008ab87a33b5` / `s-b41233f2fe15`。本次未另开监听服务或 MCP 子进程；测试进程正常退出，新增受控工作线程均已 join。

### 最终版本与剩余边界

HEAD 仍为 `41d74db15f47bee5813976bd11435dff1be166ef`，main、origin 未变，未 commit/push。与原验收文件清单比较，仅 `pyharness/core/tools_executor.py` 内容变化，另增本次集成测试；原全部改动保留。

当前文件清单：`.work/verification/gaps-tested-files-sha256.json`，规范化 SHA-256 **`aec6233acbe35961c495e8d5378af2c4206b95e1f7b45f54b4f12cf8e00e9ef2`**。清单覆盖原清单及新增测试，不含报告自引用。原报告完整快照另存 `.work/verification/report-before-gaps.md`，旧日志/统计/包未覆盖。

四类验收证据在既有契约和本轮受控范围内已补齐。仍须准确理解：线程池跟踪和明确反馈不是强制停止；产品没有对任意同步 Provider 的晚到副作用生成完整明细或回滚保证。本次真实文件内容、线程存活/退出事实保留在验证证据中，不能把 generic partial/error 当成所有副作用已停止的证明。正常会话 task_ok=true 也不代表其中每次工具成功：审批拒绝、工具超时可作为失败工具反馈交给模拟模型后结束会话；对应工具事件仍明确失败。实际模型、真实个人数据、生产外部服务以及原有未验证项均未扩展验收结论。
