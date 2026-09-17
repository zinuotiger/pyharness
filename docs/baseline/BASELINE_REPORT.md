# BASELINE_REPORT.md — 当前版本基线报告（S0）

> ⏱ **时点快照（Historical Baseline）** —— 本文固化于 `0cba75d` @ 2026-09-14，描述**当时**的
> 代码形态与问题清单。**其中部分问题已在此后阶段修复**（例如本文所述「生产防线是否完整：❌ 否
> ——`engine.py:474` 绕 `GuardChain.from_config` → g1 `g-schema` 恒 allow（P0）」**已由 S1 修复**）。
> **本文不代表当前状态。** 现行能力与已知限制见 [LIMITATIONS.md](../../LIMITATIONS.md)。
>
> **本次仅追加本提示块**：原有结论、数字与行文**一字未改**（阶段记录不得事后修饰）。

> **基线标识**：`0cba75d` @ 2026-09-14 ｜ 分支 `main` ｜ 工作区无代码修改
> **阶段**：S0 Baseline（Governed Agent Runtime v1.0 开发顺序的第 0 步，见 [ARCHITECTURE_DECISION_RECORD.md](../../ARCHITECTURE_DECISION_RECORD.md) §5）
> **数据性质**：**所有数字均来自实际执行结果**（pytest 运行产物 + `import` 后运行时计数 + 文件统计），无文档抄录。
> **本报告是 `docs/baseline/` 的索引与交叉核对。**

---

## 1. 基线三件套

| 文档 | 内容 | 关键数字 |
|---|---|---|
| [TEST_BASELINE.md](TEST_BASELINE.md) | 测试执行基线（环境/命令/结果/覆盖率/分布） | **1442 passed / 2 skipped / 0 failed** |
| [CODE_METRICS.md](CODE_METRICS.md) | 代码与文档度量 | **78 文件 / 30,881 行**；测试 **24,533 行** |
| [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md) | 实测架构（分层/主链/四关/事件真源/偏差） | 事件 **73 型** / 强同步 **11** |

---

## 2. 权威基线数据

### 2.1 测试

| 指标 | 值 |
|---|---|
| 收集用例数 | **1444** |
| **通过** | **1442** |
| 跳过 | **2** |
| 失败 / 错误 | **0 / 0** |
| 未收集（环境缺口） | **5**（2 个文件） |
| 退出码 | **0** |
| 耗时 | **39.97 s** |
| 语句覆盖率 | **77%**（16,964 语句 / 3,857 未覆盖） |
| 静态 `def test_` | 1326 |

### 2.2 代码与文档

| 指标 | 值 |
|---|---|
| `pyharness/` 文件数 | **78** |
| `pyharness/` 行数 | **30,881** |
| 其中 `core/` | 45 文件 / **18,501 行**（59.9%） |
| 最大单文件 | `application/service.py` **1,043 行** |
| 测试文件 / 行数 | **64 / 24,533** |
| 代码:测试 比 | **1 : 0.79** |
| `docs/` | **65 篇 / 1.33 MB** |
| ADR | **12**（ADR-001~012） |
| specs | 32 篇（未覆盖 30+ 模块） |

### 2.3 运行时事实

| 指标 | 值 |
|---|---|
| 事件类型 | **73** |
| 强同步类型 | **11** |
| 瞬态类型 | **3** |
| Payload 模型 | **71** |
| 内置 Guard | **7** |
| CLI 子命令 | **16** |
| 治理相关事件 | **9** |
| 治理层概念命中（`governance`/`receipt`/`evidence`/`checkpoint`/`traceability`） | **0 / 0 / 0 / 0 / 0** |

### 2.4 环境

| 项 | 值 |
|---|---|
| Python | **3.13.13**（`requires-python >=3.11`） |
| pytest | **9.1.1**（`pytest>=8.0`） |
| **缺失依赖** | **`PySide6` / `qasync` / `pywebview`** |
| 缺失后果 | 2 个测试文件不可收集；`desktop_native/**` 937 条语句 0% 覆盖 |

---

## 3. 基线判定

| 判定项 | 结论 |
|---|---|
| 测试是否全绿 | ✅ **是**（exit 0，0 failed / 0 error） |
| 是否可复现 | ✅ **是**（命令见 TEST_BASELINE.md §2） |
| 是否存在不可执行面 | ⚠️ **是**：2 文件 / 5 用例 / 937 语句（缺 PySide6，**环境问题非代码缺陷**） |
| 覆盖率是否达文档声称 | ❌ **否**：实测 77% vs `CODE-MATRIX.md:11` 声称 88% |
| 生产防线是否完整 | ❌ **否**：`engine.py:474` 绕 `GuardChain.from_config` → **g1 g-schema 恒 allow**（P0，见 REFACTOR_PLAN W1） |
| 是否有已知红 | ✅ **无**（但存在上述**未覆盖的失效防线**——绿不等于对） |

> **基线结论**：v1.0 起点基线 = **1442 passed / 2 skipped / 0 failed @ `0cba75d`**。
> **重要提醒**：**全绿不代表防线完整**——g1 空转、`guards.disabled` 不生效、`F026` 连败在主链失效等 P0/P1 缺陷**不表现为测试红**（因缺对应断言）。这正是 S1（Phase 0 接线收口）必须先行的原因。

---

## 4. 测试数量交叉核对（任务要求项）

| 来源 | 声称值 | 实测值 | 一致？ | 差异分析 |
|---|---|---|---|---|
| **README.md:4** | `1,400 passed / 2 skipped` | **1442 passed / 2 skipped** | ⚠️ **近似但不符** | 数值滞后约 42；且当前环境**无法**跑到 1400 这个数（缺 PySide6，最多跑到 1442） |
| **CODE-MATRIX.md:10** | `1298 passed / 2 skipped` | **1442 passed / 2 skipped** | ❌ **严重不符** | 滞后 144；该文档标注"终版 2026-09-07"，此后代码增长约 9,000 行 |
| **pytest 实际（collected）** | — | **1444** | — | 基准值 |
| **pytest 实际（passed）** | — | **1442** | — | 基准值 |
| **静态 `def test_`** | 1326（未在文档中声称） | **1326** | ✅ | 与 collected 差 118 = `parametrize` 展开 |

**结论**：
1. **权威基线 = 1442 passed / 2 skipped**（collected 1444）。
2. 两个文档数字**均与实测不符**：README 近似（1400 ≈ 1442，但不可复现该精确值）；CODE-MATRIX 严重滞后（1298）。
3. 建议修正方向：
   - `README.md:4` → `1442 passed / 2 skipped`（并注明"不含 native 壳 5 用例，需 `native` extra"）；
   - `CODE-MATRIX.md:10-11` → 数字与覆盖率整体刷新，或标注"快照日期"避免被当作当前值。

**其他被一并核出的文档-实测差异**（详见 CODE_METRICS.md §8）：

| 文档声称 | 出处 | 实测 |
|---|---|---|
| 覆盖率 88% | `CODE-MATRIX.md:11` | **77%** |
| 40 个 .py / 21,528 行 | `CODE-MATRIX.md:9` | **78 个 / 30,881 行** |
| 事件词表 64 类型 | `CODE-MATRIX.md:37` | **73** |
| cli 14 子命令 | `CODE-MATRIX.md:30`、`cli.py:6` | **16** |
| invariants 12 项 | `CODE-MATRIX.md:34` | **7 项 / 3 文件** |
| 事件词表 73 型 | `README.md:4` | **73** ✅ |

---

## 5. 对 v1.0 开发的基线含义

| 含义 | 依据 |
|---|---|
| **S1 必须先修防线缺口，而非先建治理层** | 全绿但 g1 恒 allow——治理层若建在失效的强制点上，会把错误语义固化 |
| **S1 首步须补齐 native 依赖并重测** | `desktop_native/**` 937 语句 / 5 用例未验证；`test_shell_parity.py`（双壳契约）是 README 卖点的唯一验证 |
| **回归安全网可用但需补强** | 1442 用例是可靠基线；但 `tests/acceptance/`、`tests/e2e/`、`tests/security/`、`tests/fixtures/` 全空，`INV-02/03/06` 无专项测试 |
| **覆盖率目标需重设** | 实测 77%（非 88%）；治理层新增代码应设独立覆盖率门槛 |
| **文档数字需在 S1 一并修正** | 否则 S6/S7 的"文档一致性校验"（`TraceabilityMatrix.check_consistency()`）会持续报错 |

---

## 6. 未决项（需人工决策）

| # | 待决 | 影响 |
|---|---|---|
| **B-1** | **是否安装 `native` extra（PySide6/qasync/pywebview）以补齐测试面？** | PySide6 体积较大（数百 MB）。不装则 `desktop_native/**` 永久 0% 覆盖、双壳契约不可验证；装则基线可完整复现 |
| **B-2** | `tmp/baseline/` 的原始产物是**归档到 `.ai-coding/evidence/`**（长期留证）还是**纳入 `.gitignore`**（临时）？ | 关系到 S0 证据的可追溯性（Protocol §10 Evidence） |
| **B-3** | 是否在 S1 修正 `README.md:4` / `CODE-MATRIX.md` 的数字？ | 影响 S6 的文档一致性校验能否通过 |
| **B-4** | pytest 版本是否锁定？（`pyproject` 写 `>=8.0`，实装 9.1.1） | pytest 9 的终端输出行为与 8 不同（本环境已不输出 `N passed` 汇总行），影响未来留证方式 |

---

## 7. 留证物

| 文件 | 大小/内容 |
|---|---|
| `tmp/baseline/pytest_full.log` | 带覆盖率完整输出（134 行） |
| `tmp/baseline/pytest_counts.log` | `--no-cov` 运行输出 |
| `tmp/baseline/junit.xml` | 机器可读权威计数（1444 testcase / 169,942 字节） |
| `docs/baseline/{BASELINE_REPORT,TEST_BASELINE,CODE_METRICS,CURRENT_ARCHITECTURE}.md` | 本报告四件套 |

**复现命令**：

```bash
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --ignore=tests/unit/test_desktop_native.py --ignore=tests/unit/test_shell_parity.py -q --no-cov --junit-xml=tmp/baseline/junit.xml
```

> 未归档到 `.ai-coding/evidence/`（待决项 B-2）。当前 `tmp/` 为未跟踪目录。
