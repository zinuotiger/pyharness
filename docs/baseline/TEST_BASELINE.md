# TEST_BASELINE.md — 测试基线（S0）

> **基线标识**：`0cba75d` @ 2026-09-14
> **数据性质**：全部数字来自**实际执行结果**，非文档抄录。执行命令与原始日志见 §8。
> **关联**：[BASELINE_REPORT.md](BASELINE_REPORT.md) · [CODE_METRICS.md](CODE_METRICS.md) · [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md)

---

## 1. 执行环境（实测）

| 项 | 值 | 来源 |
|---|---|---|
| 解释器 | **Python 3.13.13** | `.venv/Scripts/python.exe -V` |
| `pyproject` 要求 | `requires-python = ">=3.11"` | `pyproject.toml:5` |
| pytest | **9.1.1** | `pytest --version` |
| `pyproject` 要求 | `pytest>=8.0` | `pyproject.toml:26` |
| 虚环境 | `.venv/`（项目内） | — |
| 平台 | Windows 11（win32） | — |

**⚠️ 关键环境缺口 —— 三个桌面壳依赖未安装：**

| 依赖 | 状态 | 后果 |
|---|---|---|
| `PySide6` | **MISS** | `pyharness/desktop_native/**` 全部无法 import |
| `qasync` | **MISS** | 原生壳事件循环不可用 |
| `pywebview` | **MISS** | `pyharness/desktop/launcher.py` 的 `run_desktop` 路径不可实测（其 import 为惰性，故其余 desktop 测试仍可收集） |

已安装的关键依赖（实测 `importlib.util.find_spec`）：`pytest`、`pytest_asyncio`、`coverage`、`pytest_cov`、`hypothesis`、`pydantic`、`httpx`、`yaml`、`fastapi`、`uvicorn`、`winpty`。

**环境缺口的直接后果**：**2 个测试文件无法收集，共 5 个用例不可执行**。

| 文件 | 静态 `def test_` 数 | 收集失败根因 |
|---|---|---|
| `tests/unit/test_desktop_native.py` | 1 | `ModuleNotFoundError: No module named 'PySide6'`（该文件 `:8` 直接 import `PySide6.QtWidgets`） |
| `tests/unit/test_shell_parity.py` | 4 | 同上——经 `pyharness.desktop_native.__init__:4` → `app.py:8` 触发 |

> **架构影响**：`test_shell_parity.py` 是"Web 壳 ↔ PySide6 壳能力对齐"的**唯一契约测试**（校验 `application/models.py:126` 的 `SHELL_CAPABILITIES` 与两端入口一致）。该文件不可收集，意味着 **README.md:4 宣称的"双壳能力对齐"在本环境不可验证**。

---

## 2. 执行命令（可复现）

```bash
.venv/Scripts/python.exe -m pytest -p no:cacheprovider \
  --ignore=tests/unit/test_desktop_native.py \
  --ignore=tests/unit/test_shell_parity.py \
  -q --no-header --no-cov
```

```bash
# 覆盖率单独取（pyproject addopts 已含 --cov）
.venv/Scripts/python.exe -m pytest -p no:cacheprovider \
  --ignore=tests/unit/test_desktop_native.py \
  --ignore=tests/unit/test_shell_parity.py \
  -q --no-header -rf
```

```bash
# 权威计数取机器可读产物的那次
.venv/Scripts/python.exe -m pytest -p no:cacheprovider \
  --ignore=tests/unit/test_desktop_native.py \
  --ignore=tests/unit/test_shell_parity.py \
  -q --no-cov --junit-xml=tmp/baseline/junit.xml
```

**说明**：pytest 9.1.1 在本项目配置下**不输出** `N passed` 终端汇总行（已核实：即使 7 个用例的小样本，输出仅 `....... [100%]` 后即结束，无汇总行）。故权威计数改由 `--junit-xml` 的机器可读产物解析，避免依赖终端渲染。

---

## 3. 权威结果

| 指标 | 值 |
|---|---|
| 收集用例数（collected） | **1444** |
| **通过（passed）** | **1442** |
| 跳过（skipped） | **2** |
| 失败（failures） | **0** |
| 错误（errors） | **0** |
| 未收集（uncollected） | **5**（2 个文件因缺 PySide6） |
| 退出码 | **0** |
| 耗时 | **39.97 s** |
| 执行时间戳 | 2026-09-14T15:09:23+08:00 |

**跳过明细（2 条，全在 `tests/unit/test_spill.py`）**：

| 用例 | 性质 |
|---|---|
| `test_spill.py::TestEnter::test_mode_600` | 平台相关（POSIX 文件权限 0600），Windows 下跳过 |
| `test_spill.py::TestRead::test_symlink_escape_zero_read` | 符号链接语义，Windows 下跳过 |

---

## 4. 静态定义数 vs 收集数（口径说明）

| 口径 | 值 | 方法 |
|---|---|---|
| 静态 `def test_` 函数数 | **1326** | `grep -rh "def test_" tests --include="*.py" \| wc -l` |
| pytest 收集用例数 | **1444** | `junit.xml` 的 `testcase` 节点数（已排除 2 个不可收集文件） |
| 差额 | **+118** | `@pytest.mark.parametrize` 展开 |
| 若含不可收集的 2 文件 | 1449 | 1444 + 5（静态定义，均无法收集） |

> **结论**：文档中"1326 个测试"的说法指的是**静态函数数**；实际可执行**用例数**为 1444。两者都对，但必须区分口径——否则会误判"测试数量对不上"。

---

## 5. 分文件用例分布（实测，前 20 与最少 10）

**最厚（回归安全网）：**

| 用例数 | 文件 |
|---|---|
| 84 | `tests/unit/test_cli.py` |
| 83 | `tests/unit/test_llm.py` |
| 83 | `tests/unit/test_tools_guard.py` |
| 81 | `tests/unit/test_tools_registry.py` |
| 72 | `tests/unit/test_desktop.py` |
| 67 | `tests/unit/test_tool_web.py` |
| 65 | `tests/unit/test_spill.py` |
| 49 | `tests/unit/test_bus.py` |
| 47 | `tests/unit/test_repair.py` |
| 46 | `tests/unit/test_acp.py` |

**最薄（关注点）：**

| 用例数 | 文件 | 备注 |
|---|---|---|
| 1 | `tests/unit/test_engine_flow.py` | 装配主链仅 1 个大用例 |
| 1 | `tests/unit/test_engine_plugins.py` | — |
| 1 | `tests/unit/test_pty.py` | 对应 `core/pty.py` 覆盖率 **34%** |
| 1 | `tests/integration/test_mcp_stdio.py` | 集成面仅此 1 例 |
| 2 | `tests/unit/test_telemetry.py` | 审计导出面薄 |
| 2 | `tests/unit/test_orchestration.py` | — |
| 3 | `tests/invariants/test_inv_bus.py` | — |
| 4 | `tests/unit/test_workflow.py` | — |
| 4 | `tests/unit/test_skill_registry.py` | 对应 `core/skill_registry.py` 76% |
| 5 | `tests/unit/test_application_service.py` | 对应 `desktop/app.py` 70%、`application/service.py` 58% |

**空壳目录（0 个测试文件）：**

| 目录 | 状态 |
|---|---|
| `tests/acceptance/` | 仅 `__init__.py`——**`SOP.md` 与多篇 specs 引用的 `test_f{nnn}_*.py` 全部不存在** |
| `tests/e2e/` | 仅 `__init__.py` |
| `tests/fixtures/` | 仅 `__init__.py` |
| `tests/security/` | **目录不存在**（`specs/approval.py.md:158` 引用的 `tests/security/test_sec_*.py` 不存在） |

**`invariants/` 实际覆盖（实测 7 用例 / 3 文件）：**

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_inv_bus.py` | 3 | F005 背压阈值、EVT-102 未注册类型拒发 |
| `test_inv_config.py` | 2 | `PH_` env 白名单、敏感项引用 |
| `test_inv_events.py` | 2 | 强同步族在词表、Envelope 必填字段 |

> **INV 覆盖缺口**：`INV-02`（无第二状态）、`INV-03`（rebuild 与缓存一致）、`INV-06`（执行 args = 日志 args）**无专项不变量测试**。三文件头部注释仍写"当前 RED：模块未实现"（早期骨架遗留）。

---

## 6. 覆盖率（实测）

| 指标 | 值 |
|---|---|
| 语句总数 | **16,964** |
| 未覆盖语句 | **3,857** |
| **总覆盖率** | **77%** |
| 有覆盖的 `pyharness/` 模块 | 74 |
| 0% 覆盖的模块 | **4**（全部为 `desktop_native/`，因缺 PySide6 无法 import） |

**0% 与最低覆盖模块（升序，前 20）：**

| 覆盖率 | 模块 | 语句 | 未覆盖 |
|---|---|---|---|
| 0% | `desktop_native/main_window.py` | 731 | 731 |
| 0% | `desktop_native/controller.py` | 170 | 170 |
| 0% | `desktop_native/app.py` | 33 | 33 |
| 0% | `desktop_native/__init__.py` | 3 | 3 |
| 34% | `core/pty.py` | 283 | 188 |
| 46% | `core/tool_skill.py` | 35 | 19 |
| 57% | `cli.py` | 1117 | 475 |
| 58% | `application/service.py` | 759 | 317 |
| 61% | `core/tool_ask.py` | 18 | 7 |
| 68% | `core/proc.py` | 202 | 65 |
| 70% | `desktop/app.py` | 592 | 178 |
| 74% | `core/plugin_loader.py` | 120 | 31 |
| 74% | `core/tenant_settings.py` | 238 | 63 |
| 76% | `core/budget.py` | 55 | 13 |
| 76% | `core/skill_registry.py` | 234 | 56 |
| 77% | `core/auto_title.py` | 30 | 7 |
| 77% | `core/tool_exec.py` | 90 | 21 |
| 77% | `desktop/sessions.py` | 252 | 58 |
| 77% | `engine.py` | 527 | 120 |
| 78% | `bus/plugin.py` | 214 | 47 |

> **注**：`desktop_native/` 的 0% **不是测试薄**，而是**环境缺依赖**——该模块在本环境连 import 都失败。补齐 `native` extra 后此 937 条语句的覆盖率需重新测量。

---

## 7. 基线判定

| 判定 | 结论 |
|---|---|
| 测试是否全绿 | ✅ **是**（1442 passed / 0 failed / 0 error / exit 0） |
| 是否有红 | ❌ 无 |
| 是否有环境导致的不可执行面 | ⚠️ **有**：2 文件 / 5 用例 / 937 条语句（`desktop_native/**`） |
| 测试数是否可复现 | ✅ 可复现（见 §2 命令） |
| 覆盖率是否达标 | ⚠️ **77%**，低于 `CODE-MATRIX.md:11` 声称的 88% |

**基线结论**：以 `0cba75d` 为 v1.0 起点的**可运行集基线 = 1442 passed / 2 skipped / 0 failed**；同时记录**不可执行面 5 用例**（环境缺口，非代码缺陷）。

---

## 8. 原始产物（已留证）

| 文件 | 内容 |
|---|---|
| `tmp/baseline/pytest_full.log` | 带覆盖率的完整运行输出（134 行，含 coverage 表） |
| `tmp/baseline/pytest_counts.log` | `--no-cov` 运行输出 |
| `tmp/baseline/junit.xml` | 机器可读权威计数（169,942 字节，1444 testcase） |

> `tmp/` 为临时目录（当前未被 `.gitignore` 跟踪，属未跟踪文件）。S1 应决定：归档到 `.ai-coding/evidence/`（长期留证）还是纳入 `.gitignore`（临时）。见 [BASELINE_REPORT.md](BASELINE_REPORT.md) §6 待决项。

---

## 9. 与既有文档声明的差异

| 文档声明 | 出处 | 实测 | 差异 |
|---|---|---|---|
| "1,400 passed / 2 skipped" | `README.md:4` | **1442 passed / 2 skipped**（可运行集） | ⚠️ 数值不符（README 滞后；且当前环境无法跑到 1400 这个数） |
| "**1298 passed** / 2 skipped" | `CODE-MATRIX.md:10` | 1442 passed | ⚠️ 严重滞后 |
| "覆盖率 88%" | `CODE-MATRIX.md:11` | **77%** | ⚠️ 滞后 11 个百分点 |
| "invariants/ 12 项" | `CODE-MATRIX.md:34` | **7 项 / 3 文件** | ⚠️ 不符 |
| "unit 33 文件 + invariants" | `CODE-MATRIX.md:10` | unit **53 文件** + invariants 3 | ⚠️ 文件数滞后 |
| "INV-01~09 不变量测试全绿" | `CODE-MATRIX.md:34` | 仅 INV-01 边缘 + F005/EVT-102；**INV-02/03/06 无专项** | ⚠️ 覆盖面被高估 |
| "事件词表 73 型" | `README.md:4` | **73** | ✅ 一致 |
| "事件词表 64 类型" | `CODE-MATRIX.md:37` | **73** | ⚠️ 滞后 |

> **规律**：`README.md` 的数字**较新但不精确**；`CODE-MATRIX.md`（标注"终版 2026-09-07"）**整体滞后**于代码演进（模块数 32→78 文件、测试 1298→1442、词表 64→73）。
