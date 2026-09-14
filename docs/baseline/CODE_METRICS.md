# CODE_METRICS.md — 代码度量基线（S0）

> **基线标识**：`0cba75d` @ 2026-09-14
> **数据性质**：全部数字来自**实际执行/统计**，非文档抄录。
> **关联**：[BASELINE_REPORT.md](BASELINE_REPORT.md) · [TEST_BASELINE.md](TEST_BASELINE.md) · [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md)

---

## 1. 代码规模

### 1.1 `pyharness/` 包（实测 78 个 `.py` / 30,881 行）

| 目录 | `.py` 文件 | 行数 | 占比 |
|---|---|---|---|
| `pyharness/core/` | 45 | **18,501** | 59.9% |
| `pyharness/`（顶层） | 8 | 5,814 | 18.8% |
| `pyharness/desktop/` | 8 | 2,103 | 6.8% |
| `pyharness/application/` | 4 | 1,265 | 4.1% |
| `pyharness/desktop_native/` | 4 | 1,211 | 3.9% |
| `pyharness/events/` | 4 | 1,078 | 3.5% |
| `pyharness/bus/` | 4 | 909 | 2.9% |
| `pyharness/ui/` | 1（`__init__.py`） | 0 | 0%（前端资源见 §1.4） |
| **合计** | **78** | **30,881** | 100% |

### 1.2 顶层文件（行数降序）

| 行数 | 文件 |
|---|---|
| 1,713 | `pyharness/cli.py` |
| 890 | `pyharness/engine.py` |
| 812 | `pyharness/repair.py` |
| 802 | `pyharness/config.py` |
| 712 | `pyharness/persistence.py` |
| 676 | `pyharness/acp.py` |
| 201 | `pyharness/errors.py` |
| 8 | `pyharness/__init__.py` |

### 1.3 最大单文件（`core/` 与外围，前 12）

| 行数 | 文件 | 说明 |
|---|---|---|
| 1,043 | `application/service.py` | 跨壳业务门面（God facade） |
| 1,032 | `core/llm.py` | 唯一 LLM 出口 |
| 1,031 | `desktop/app.py` | 54 条 FastAPI 路由 + SSE |
| 991 | `core/session_query.py` | SQLite FTS5 派生索引 |
| 958 | `core/tools_guard.py` | g1–g7 单调拒绝链 |
| 904 | `desktop_native/main_window.py` | 单文件 Qt 装配（9 个 tab） |
| 854 | `core/approval.py` | 人类裁决服务 |
| 812 | `repair.py` | 崩溃修复 |
| 802 | `core/tool_web.py` | web.search/fetch |
| 782 | `core/tools_executor.py` | 四关执行管道 |
| 761 | `core/schedule.py` | cron/interval 定时器 |
| 712 | `persistence.py` | JSONL 物理层 |

### 1.4 非 `.py` 资源

| 资源 | 规模 |
|---|---|
| `pyharness/ui/index.html` | **1,233 行 / 67,206 字节**（内嵌 Web 前端，非空目录） |
| `skills/` | 2 个 `SKILL.md`（`demo-script` / `interview-pitch`） |
| `examples/plugins/` | 1 个示例插件（`hello_time/plugin.py`） |

---

## 2. 测试规模（实测）

| 指标 | 值 |
|---|---|
| 测试文件数 | **64**（含 `__init__.py`） |
| 测试代码行数 | **24,533** |
| 静态 `def test_` 数 | **1326** |
| 可执行用例数（collected） | **1444** |
| 代码 : 测试 行数比 | 30,881 : 24,533 ≈ **1 : 0.79** |

---

## 3. 文档规模（实测）

| 指标 | 值 |
|---|---|
| `docs/` 下 `.md` 文件数 | **65** |
| `docs/` 总字节 | **1,333,861**（≈ **1.30 MB**） |
| `docs/specs/` 规格篇数 | 32（`docs/specs/README.md:11`，覆盖 313 函数——**未覆盖约 30+ 个模块**，见 §6） |
| ADR 条数 | **12**（`docs/ADD.md`，ADR-001~012） |
| `docs/CONSTRAINTS-*.md` | 8 篇 |

---

## 4. 运行时事实（`import` 后实测，非文档）

| 项 | 实测值 | 来源 |
|---|---|---|
| 事件类型总数 | **73** | `pyharness.events.vocab.EVENT_TYPES` |
| 强同步类型 `SYNC_TYPES` | **11** | 同上 |
| 瞬态类型（禁入日志） | **3**（`llm.chunk` / `config.updated` / `registry.updated`） | 同上 |
| Payload 模型数 | **71** | `pyharness.events.payload` 中 `*Payload` 类计数 |
| 内置 Guard 数 | **7**（`g-schema`/`g-fs-path`/`g-credential-read`/`g-exec`/`g-overwrite`/`g-net-outbound`/`g-danger`） | `pyharness.core.tools_guard._BUILTIN_IDS` |
| CLI 子命令数 | **16** | `pyharness.cli.parse_args` 叶子命令计数 |
| 治理相关事件类型 | **9**（`approval.{requested,granted,denied,timeout}`、`budget.paused`、`guard.evaluated`、`guard.rejected`、`scope.updated`、`syscheck.fail`） | 词表过滤 |

> **关键对照**：治理层所需概念在代码中的命中数——`governance` **0**、`receipt` **0**、`evidence` **0**、`checkpoint` **0**、`traceability` **0**。即：**治理机制存在，但治理层不存在**。

---

## 5. 覆盖率（实测，与 TEST_BASELINE 同源）

| 指标 | 值 |
|---|---|
| 语句总数 | **16,964** |
| 未覆盖 | **3,857** |
| **总覆盖率** | **77%** |
| 74 个模块有覆盖 / 4 个 0%（全为 `desktop_native/`） |

**按覆盖率分档：**

| 档位 | 模块 |
|---|---|
| 0%（环境导致，非测试薄） | `desktop_native/{main_window,controller,app,__init__}.py` |
| 30–49% | `core/pty.py`(34%) · `core/tool_skill.py`(46%) |
| 50–69% | `cli.py`(57%) · `application/service.py`(58%) · `core/tool_ask.py`(61%) · `core/proc.py`(68%) |
| 70–79% | `desktop/app.py`(70%) · `core/plugin_loader.py`/`core/tenant_settings.py`(74%) · `core/budget.py`/`core/skill_registry.py`(76%) · `core/auto_title.py`/`core/tool_exec.py`/`desktop/sessions.py`/`engine.py`(77%) · `bus/plugin.py`(78%) |
| ≥90% | `errors.py`(98%) · `events/envelope.py`(97%) · `events/payload.py`(99%) · `events/vocab.py`(98%) · `desktop/constants.py`(100%) · `tools_guard.py`(92%) · `core/tools_registry.py`(90%) |

---

## 6. 结构性度量（引自 2026-09-14 架构审计）

> 详细证据见仓库根 [REFACTOR_PLAN.md](../../REFACTOR_PLAN.md)。

| 类别 | 数量 | 样例 |
|---|---|---|
| **重复实现** | 14 类 | 路径几何（`tools_guard._resolve_geometry` ≡ `tool_fs.resolve_in_workspace`）；spill 封装 ×3；bus→store 落盘适配器 ×**5** |
| **死代码** | 3 处 | `tools_executor._invoke`（零调用点）；`session_query._MIN_CJK/_MIN_LATIN`（仅 `__all__`）；`llm_fallback.pick`（仅自用） |
| **死状态** | 3 个 | `AgentLoop` 的 `paused`/`stopping`/`terminated` 永不赋值 |
| **上帝对象** | 3 处 | `Ctx`（~33 属性）；`EngineSpine`；`application/service.py`(1043 行) |
| **上帝函数** | 1 处 | `engine.build_runner_components`（185 行，装配 18 类组件） |
| **无界增长容器** | 3 处 | `task_queue._done`；`approval._grant_slots`；`tools_executor._rejected` |
| **异常吞掉点** | 10+ 处 | `EngineSpine.close` 连续 8 处 `except Exception: log.warning` |
| **装配缺口** | 1 处（P0） | `engine.py:474` 绕 `tools_guard.from_config()` → g1 的 validator 恒 None |
| **两套并行实现** | 2 组 | 两套 repair；两套预算状态判据 |

**规格覆盖率缺口**（`docs/specs/` 32 篇 vs 78 个模块）：无 spec 覆盖的模块包括 `engine.py`、`application/*`、`desktop_native/*`、`session_query.py`、`mcp.py`、`proc.py`、`pty.py`、`kv.py`、`todo.py`、`workflow.py`、`telemetry.py`、`tenant_settings.py`、`skill*.py`、`user_ask.py`、`tool_ask.py`、`auto_title.py`、`message_edit.py`、`orchestration.py`、`plugin_loader.py`、`budget.py` 等。

---

## 7. 版本控制状态（实测）

| 项 | 值 |
|---|---|
| HEAD | `0cba75db6a8f75b354e86b15e5f415264f7c91ba` |
| HEAD 摘要 | `0cba75d 2026-09-14 sync: 审计修复(P0–P3) + Protocol v0.3 + 脱敏` |
| 分支 | `main` |
| 工作区 | 干净（代码/文档无修改） |
| 未跟踪文件 | 4 项：`REFACTOR_PLAN.md`、`GOVERNED_AGENT_RUNTIME_DESIGN.md`、`ARCHITECTURE_DECISION_RECORD.md`、`tmp/` |

---

## 8. 与既有文档声称的差异

| 文档声称 | 出处 | 实测 | 差异 |
|---|---|---|---|
| "40 个 .py 文件，21,528 行" | `CODE-MATRIX.md:9` | **78 个 / 30,881 行** | ⚠️ 严重滞后（文件数近一倍） |
| "32/32 模块实现" | `CODE-MATRIX.md:9` | 78 个模块文件 | ⚠️ 口径为"功能模块"，非文件 |
| "事件词表 64 类型" | `CODE-MATRIX.md:37` | **73** | ⚠️ 滞后 |
| "cli(14 子命令)" | `CODE-MATRIX.md:30` | **16** | ⚠️ 滞后（`cli.py:6` docstring 亦写 14） |
| "事件词表 73 型" | `README.md:4` | **73** | ✅ 一致 |
| "60 份文档 1.24MB" | `README.md:145` | **65 份 / 1.30 MB** | ⚠️ 轻微滞后 |
| "覆盖率 88%" | `CODE-MATRIX.md:11` | **77%** | ⚠️ 滞后 11pp |
| "specs 32 份/313 函数" | `docs/specs/README.md:11` | 32 篇（未覆盖 30+ 模块） | ⚠️ 覆盖面被高估 |

> **度量结论**：`CODE-MATRIX.md` 标注"终版 2026-09-07"，此后代码增长了约 **9,000 行 / 38 个文件**，文档数字整体未同步；`README.md` 的 73 型词表准确，测试数（1400）不精确。
