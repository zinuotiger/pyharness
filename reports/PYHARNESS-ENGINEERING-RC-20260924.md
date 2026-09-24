# PyHarness Engineering Release Candidate — 2026-09-24

这是本地工程候选版本，保留包版本 **0.1.0**，未推送、未发布。基础提交：`41d74db15f47bee5813976bd11435dff1be166ef`；候选分支：`codex/pyharness-engineering-rc-20260924`。

本报告证据阶段：**reviewed-precommit-source**。最终提交：`HEAD (resolve with git rev-parse HEAD after local commit)`。仓库内报告不能自引用其未来提交及包含自身的归档 SHA256；提交后的准确 SHA、重新执行结果、最终产物校验和在交付目录同名公开报告和 `SHA256SUMS.txt` 中补充，原提交不改写。

## 结果

| 检查 | 实际结果 |
|---|---|
| 完整 pytest | collected 2383；passed 2364；failed 0；skipped 19；warnings 5；138.88s；exit 0 |
| coverage | 83.42%，门禁 75% |
| 高风险回归 | 161/161，包含于完整测试 |
| 故障注入选择集 | 61/61，包含于完整测试 |
| Hypothesis 属性测试 | 6/6，不是生成样本数 |
| 并发/所有权选择集 | 19/19，包含于完整测试 |
| 固定 evleven R1 | 16/16，独立真实 MCP 测试；exit 0 |
| 代表性变异 | 10/10 检出：9 断言失败，1 硬超时；非全库分数 |
| 安装 | 全新非 editable base/native/desktop；24 步全部 exit 0 |
| Windows | 锁、进程树、Unicode/空格、回环地址及端口释放实际验证 |
| POSIX | R02 environment-blocked，未把 docker-desktop 作为验收环境 |

完整 pytest 的 19 个跳过为专用 R1 16 项、真实 POSIX 权限 2 项及需要 Windows 符号链接权限的 1 项。专用 R1 通过数未重复计入完整测试。选择集可重叠，不能相加得出测试总数。各选择集测试 ID 见同名 JSON。

## 变更与独立复核

F01–F15、R03–R04、G01–G02 共 19 项保留 confirmed-fixed；R01 safely-constrained；R02 environment-blocked。
候选只带入正式源代码、测试、配置、脚本及公开文档；历史本机报告和旧产物保存于私有外部证据。

两位独立只读 reviewer 分别复核运行时/安全和文件/交付。首轮发现两个 Medium 生命周期缺口：
重复取消 MCP close 会失去进程所有权；独立引擎装配失败会遗留自建 Store。修复范围限定为自建 Store/锁回滚及已完整装配 spine 的激活失败回收。
均先建立失败复现，再修复并补回归。另修正过期限制声明、机器 ACL 信息、失效导航和 sdist 文档排除。
没有未处理的有效 Critical/High/Medium 发现。新增修改仅在独立候选 worktree。

元数据补充 README/MIT；wheel 只显式包含三项公开示例资源。
R1 配置脚本要求显式产物目录；本地交付构建器要求显式依赖 pins 和仓库外输出。
正式依赖版本与锁文件未升级。独立安装从仓库外目录运行，核实实际 import 来自各环境 site-packages。
native 不带未声明 Web 依赖；base 不带 GUI/Web 依赖。

## 固定外部边界与产物

evleven R1 wheel SHA256：`40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e`。
使用独立进程、真实 MCP、真实持久化与治理；模型规划使用确定性替身。
R1 安装包来自已核实产物，未重新构建服务端。

产物范围：Precommit installation artifacts only; final rebuilt artifact hashes are in external postcommit attestation。

| 文件 | SHA256 |
|---|---|
| pyharness-0.1.0-py3-none-any.whl | `e296494e0d4aba276abb4b38d167a0f1d7906e29af816b3dbaef677d912941ba` |
| pyharness-0.1.0.tar.gz | `16357af0b90a97ca5a56cb7f66fb9514debaed700a5638c1d06cb1705d64e95c` |

源代码/测试清单 SHA256：`63ee6897655b2c1a4120c7667e043a11d958910f5d75db46ce0cd9558c5842e0`。
公开候选 payload 清单 SHA256：`dc1680b2ca310e9de505252a1d130a039a17c4bd1a1da19ec85f29754951ea26`（范围与算法见 JSON；不含自引用报告）。
完整本机分类清单、原始命令和日志留在私有外部证据。

## 可复现命令与边界

先在仓库外使用明确解释器创建环境、按 `uv.lock` 的 dev 依赖配置，并为 HOME、配置、缓存、临时目录和 coverage 指定独立位置。
模型和网络边界使用正式测试夹具；不继承真实用户配置。

```powershell
& $PYTHON -B -m pytest -p no:cacheprovider -o addopts= tests/unit/test_remediation_isolation.py
& $PYTHON -B -m pytest -p no:cacheprovider -o addopts= tests --cov=pyharness --cov-report=json --cov-fail-under=75
& $PYTHON -B scripts/check_authorization.py
& $PYTHON -B -m hatchling build -t wheel -t sdist -d $OUT
git diff --check
```

这些是逻辑命令模板；PowerShell 变量调用需使用 `& $PYTHON`，且工作目录应为隔离副本。完整实际参数与退出码在外部证据索引。
R1 专用测试遵循现有集成脚本的固定布局契约：先在**仓库外隔离源码副本**的 `.work/verification/venv` 创建全新 PyHarness 环境，按锁文件安装声明的 dev/test（含 desktop）依赖；将 `$PYTHON` 明确指向该环境的 `Scripts/python.exe`（Windows）。再以 `scripts/setup_evleven_r1.ps1 -ArtifactDirectory $R1 -PythonPath $PYTHON -UvPath $UV` 配置独立 R1 环境，最后必须使用**同一个 PyHarness 解释器**运行 `tests/integration/test_evleven_r1.py`。不能用任意外部解释器代替该固定布局；两套环境、数据和日志均留在外部副本中，不能复用原始工作树环境。
最终安装使用交付 wheel 的 base、`[native]`、`[desktop]`，从源码目录之外运行 `pyharness --help`；不会自动调用模型。

## 未验证和限制

- Engineering Release Candidate only; no push, remote tag, GitHub Release or remote CI run.
- Package version remains 0.1.0; branch and Git commit identify this engineering candidate.
- R01 unsafe PTY entry points remain disabled; no unsupported process-tree ownership guarantee.
- R02 environment-blocked: no independent Linux/WSL environment; docker-desktop is not used as a POSIX acceptance environment. Controlled syscall tests do not establish actual POSIX permissions.
- Windows local single-trust-domain acceptance only. No other-machine, Linux, WSL or Docker production guarantee.
- Deterministic model substitutes only; no real conversational model, paid API or model download.
- evleven R1 is an external real MCP process and persistent service; it is not an embedded model or PyHarness storage implementation. Deterministic keyword/ID recovery does not establish semantic retrieval.
- No complete manual GUI/WebView2 interaction validation; native Qt offscreen and desktop loopback HTTP were exercised.
- Synchronous-tool cancellation and remote MCP timeout do not guarantee stopping remote work or reversing side effects; unknown writes must not be blindly retried.
- Ruff is undeclared and unavailable, so it was not run; no formal type-check gate is configured.
- Dependency installation can need the configured package source or an existing cache; not an offline portable package.
- Review scope does not prove transactional rollback for every possible component constructor, nor preservation of every original MCP request exception if cleanup itself fails.
- Precommit report cannot embed its own future commit or the hash of an archive containing itself. Resolve HEAD in the committed checkout; exact postcommit evidence and rebuilt artifact checksums are provided alongside distribution artifacts.
