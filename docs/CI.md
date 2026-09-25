# Windows 与 Linux 候选版 CI

`.github/workflows/ci.yml` 检查 PR 的准确 head SHA（不以临时 merge commit 替代），以及 main / 工程候选分支的 push。仅有 `contents: read` 权限；不使用 `pull_request_target`，不调用真实模型或用户 MCP。

- Windows Python 3.13：完整 pytest、75% coverage、隔离哨兵、高风险回归、原有 security / acceptance / e2e / invariants / structure 分层门禁、授权 AST 与治理读侧可达检查。
- Ubuntu 24.04 Python 3.11 和 3.13：完整适用测试、同样的覆盖率门禁与隔离边界、独立 R02 真实 POSIX 权限用例。专用 R02 阶段要求 Linux、非 root、至少一个用例且零 skip；每个用例恢复 umask。只有实际通过的 run 才能关闭 R02，不能用 Windows skip 代替。
- 三种 OS/Python 组合均构建 wheel + sdist，从源码目录外的全新环境安装 base wheel 并检查 import / CLI / 包元数据。Windows 另在独立 native / desktop 环境执行 Qt 离屏启动关闭和回环 HTTP 就绪、停止与端口释放预检。完整人工 GUI / WebView2 和真实模型不在此 CI 的验收范围。

`uv.lock` 经 `uv export --frozen --extra dev --no-emit-project` 导出后以 `--require-hashes` 安装到新虚拟环境。不依赖缓存；构建工具固定为 hatchling 1.32.4。安装阶段可访问依赖下载站点；pytest 导入前安装非回环 socket 拒绝边界，现有逐用例 fixture 再限制未声明的子进程。网络边界是测试进程防误用措施，并非针对恶意代码的系统沙箱。

`scripts/ci_verify.py` 必须指定仓库外 `--output-dir`。HOME、USERPROFILE、PH_CFG_PATH、临时数据、下载缓存、coverage、构建输出和安装环境都进入该目录；不会读取已有的 PH 配置或传递 API 凭据。测试数据均为合成数据。脚本拒绝把输出放进源码 checkout。

可在已有 uv / Python 的环境中重复执行（`<outside-output>` 必须为独立的仓库外目录）：

```text
python -B scripts/ci_verify.py prepare --output-dir <outside-output>
python -B scripts/ci_verify.py static --output-dir <outside-output>
python -B scripts/ci_verify.py test --suite isolation --output-dir <outside-output>
python -B scripts/ci_verify.py test --suite full --output-dir <outside-output>
python -B scripts/ci_verify.py test --suite high-risk --output-dir <outside-output>
python -B scripts/ci_verify.py test --suite posix --output-dir <outside-output>
python -B scripts/ci_verify.py package --output-dir <outside-output>
```

`posix` 阶段在 Windows 上明确返回非零，正式测试文件自身的 Windows skip 保留平台原因。其余 suite 还有 security、acceptance、e2e、invariants、structural。

上传内容仅包括状态化 JUnit、统计 JSON、wheel、sdist、SHA256SUMS，保留 7 天。JUnit 删除 failure/error 详情、captured stdout/stderr 与 properties，并隐藏参数值；不上传整个 HOME、原始 coverage 数据、秘密文件、私有检查点或原始测试失败日志。完整失败诊断保留在对应 Actions step 中。

R01 继续安全禁用危险 PTY 路径。取消或超时不等于远端操作停止或副作用撤销。Linux CI 权限成功不能扩展成所有 POSIX 平台或多用户生产环境的保证。
