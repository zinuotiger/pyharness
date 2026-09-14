# S1_FINAL_EXIT_GATE.md — S1 最终出口闸（Final Exit Gate）

> **阶段**：S1 FINAL EXIT GATE（进入 S2 前的最后一次审查）
> **日期**：2026-09-14 ｜ 基线 `0cba75d`
> **依据**：[S1_CHANGE_REPORT.md](S1_CHANGE_REPORT.md) · [S1_EXIT_REVIEW.md](S1_EXIT_REVIEW.md) · [S1_SCOPE_ADJUDICATION.md](S1_SCOPE_ADJUDICATION.md) · [ADR-019-s1-scope-adjudication.md](ADR-019-s1-scope-adjudication.md) · [S1_DOCUMENTATION_CLOSURE.md](S1_DOCUMENTATION_CLOSURE.md) · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) · [REFACTOR_PLAN.md](REFACTOR_PLAN.md)
> **性质**：**只审查**——本轮未修改任何源码、未修改测试、未创建 `pyharness/governance/`、未实现 Policy/DecisionEngine/DecisionReceipt/Evidence、未改 EventBus、未增加事件实现、**未进入 S2**。

---

## 0. 判决

# **PASS**

# S1 COMPLETE

# S2 ENTRY CONDITION = MET

**依据摘要**：十项检查**全部通过**（§2）；P-1~P-6 六项前置**全部满足**（P-6 由人工裁定，见 §1）；最终全量回归 **1452 collected / 1450 passed / 2 skipped / 0 failed**（exit 0）。

---

## 1. P-6 裁定记录（人工确认）

**裁定（2026-09-14，人工）**：**以冻结架构契约中的事件名称为唯一权威名称**，最终确定为**四个**：

| # | 冻结事件名（权威） |
|---|---|
| 1 | `decision.issued` |
| 2 | `receipt.emitted` |
| 3 | `evidence.archived` |
| 4 | `policy.updated` |

**同时确认**：`decision.receipt` **不成立** · `evidence.recorded` **不成立** · `governance.audit` **不成立**；**不因任务书中的错误命名修改冻结架构**；**不新建 ADR 修改 ADR-018**；**不修改 `ARCHITECTURE_DECISION_RECORD.md`**。

→ **P-6 = SATISFIED**。本次裁定**未要求任何文档改动**（C-2.2 已按冻结名登记，正是右列四名），故无新增编辑。

> **记录说明**：**本文件是 P-6 裁定的正式记录**。[S1_DOCUMENTATION_CLOSURE.md](S1_DOCUMENTATION_CLOSURE.md) §6.2/§7 中"P-6 未满足 / 待人工确认"为**裁定前**状态（见 §4 建议动作 R-1）。

---

## 2. 十项检查（逐项证据）

| # | 检查项 | 结果 | 证据（可独立复跑） |
|---|---|---|---|
| **1** | S1-01~04 **是否完成** | ✅ **完成** | ① `engine.py` 经 `guard_from_config` 装配（`grep -c guard_from_config` = 2：import + 调用点）；② `agent_loop.py:68` = `LoopState = Literal["idle", "running"]`；③ `run_turn` 内 `_reset_turn_failures` / `_turn_failure_capped` 在位；④ `session.py` `append` 的 `_closed` 失败回滚（`absorbed_finished` 分支）。测试：10 个新增/改写用例**全部通过**（详见 §2.1） |
| **2** | S1 Scope Adjudication **是否 ACCEPT** | ✅ **ACCEPT** | `S1_SCOPE_ADJUDICATION.md` §0 = "S1 Scope Decision: **ACCEPT**"；`ADR-019` 裁定 S1 范围 = S1-01~04 四项 |
| **3** | **C-2 是否 PASS** | ✅ **PASS** | `S1_DOCUMENTATION_CLOSURE.md` §0 = C-2 PASS；核验见本表 #6 #7 |
| **4** | **C-3 是否 PASS** | ✅ **PASS** | 同上；`S1_CHANGE_REPORT.md` §1 逐文件数字与 `git diff --numstat` **逐项一致**（脚本比对 11 文件，不一致项 = 无） |
| **5** | **P-1~P-6 是否全部满足** | ✅ **全部满足** | P-1 S0 登记闭合（#6#7）· P-2 S1 判据闭合（#1）· P-3 为 S2 内动作非进入条件 · P-4 范围歧义有正式记录（ADR-019）· P-5 报告数字已修（#4）· **P-6 人工裁定 SATISFIED（§1）** |
| **6** | `ADD.md` **是否已登记 ADR-001~019** | ✅ **已登记且连续无跳号** | `grep -o "^\| ADR-[0-9]*" docs/ADD.md` → `ADR-001 … ADR-019` 顺序完整；`grep -c "^\| ADR-"` = **19**（另 2 处命中来自 §2.1 路径表的 `ADR-013 ~ ADR-018` 与 `ADR-019` 行）；`docs/ADD.md:5` = "**19 条 ADR** 全部「已接受」(ADR-001~012 定稿…;ADR-013~019 治理期追加…)" |
| **7** | `EVENT-SCHEMA.md` **是否按冻结名称登记**（四名） | ✅ **四名齐备** | §3.6 F 组（`docs/EVENT-SCHEMA.md:479` 起）：`decision.issued`(2 处) · `receipt.emitted`(2) · `evidence.archived`(1) · `policy.updated`(1)（多处 = 表格行 + 不变式行）。**别名核对**：`decision.receipt` / `evidence.recorded` / `governance.audit` **各仅 1 处命中，且均在 §3.6 的"别名不成立"声明行内**——**未作为事件登记** ✅ |
| **8** | **是否存在与 S1 无关的源码修改** | ✅ **不存在** | `git diff --numstat -- '*.py'` = **9 个文件**，与 [S1_CHANGE_REPORT.md](S1_CHANGE_REPORT.md) §1 的 S1 改动集**完全相同**：`tools_guard +12/−2` · `engine +14/−2` · `agent_loop +52/−11` · `agent +11/−10` · `session +58/−32` · `test_agent +32/−15` · `test_agent_loop +92/−11` · `test_engine +74/−0` · `test_session +85/−0`。其余修改仅 4 个**文档**（`docs/MAP.md`、`docs/DIS-CORE.md` = S1-02 同步；`docs/ADD.md`、`docs/EVENT-SCHEMA.md` = C-2 登记） |
| **9** | **是否存在未登记的 S1 交付物** | ✅ **无孤立交付物** | 交付物清单与登记归属见 §2.2；每个 S1 产物均可在报告/ADR 中溯源 |
| **10** | 当前工作区**是否可安全建立 Git 回退锚点** | ✅ **可以**（附条件） | 可安全 `commit`（本地、可回退）；**两点前置**：① `tmp/` **不得入库**（含本机账户名标识，720K 临时产物）；② **`push` 为独立决策**（公开仓库披露，见 §5）。详见 §5 |

### 2.1 检查 1 的测试证据（10 例，实测通过）

| 项 | 用例 | 断言 |
|---|---|---|
| S1-01 | `test_engine.py::test_guard_from_config_injects_schema_validator` | 畸形参数 → **reject**；合规 → **allow** |
| S1-01 | `test_engine.py::test_guard_from_config_applies_cfg_disabled` | 越界调用默认 **reject**；`disabled=["g-fs-path"]` 后 **allow** |
| S1-02 | `test_agent.py::test_agent_loop_has_no_resume_api` | `not hasattr(AgentLoop, "resume")` |
| S1-02 | `test_agent.py::test_on_bus_event_approval_granted_does_not_mutate` | 事件不改 loop 状态 |
| S1-02 | `test_agent_loop.py::test_loop_state_space_is_reachable_only` | `set(get_args(LoopState)) == {"idle","running"}` |
| S1-02 | `test_agent_loop.py::test_wake_accepts_from_any_reachable_state` | 可达态 wake 正常 |
| S1-02 | `test_agent_loop.py::test_must_stop_three_gates_readonly` | `warn` 不拦、`exhausted` 触发 budget |
| S1-03 | `test_agent_loop.py::test_turn_failure_streak_terminates_turn_on_single_step_path` | 3 调用同工具 guard 失败 → **只执行 2 次** |
| S1-03 | `test_agent_loop.py::test_turn_failure_streak_not_counted_for_other_failures` | 超时类 → 执行 3 次、不计数 |
| S1-04 | `test_session.py::test_finished_validation_failure_rolls_back_closed` | `EVT-100` **且 `_closed is False`** → 重试成功 |
| S1-04 | `test_session.py::test_finished_flush_failure_keeps_closed_but_log_empty` | `PERS-202` 后 `_closed is True`、真源为空 |

**最终全量回归（本闸实跑）**：`tests=1452 / failures=0 / errors=0 / skipped=2 / passed=1450`，exit 0，37.4 s
（留证：`tmp/baseline/junit_final_gate.xml` · `tmp/baseline/pytest_final_gate.log`）

### 2.2 检查 9 的交付物注册清单

| 交付物 | 登记归属 |
|---|---|
| 9 个 `.py`（5 源 + 4 测试）改动 | `S1_CHANGE_REPORT.md` §1（逐文件数字已与 numstat 一致） |
| `docs/MAP.md` · `docs/DIS-CORE.md` | 同上 §1（S1-02 文档同步，依 ADR-014 第 5 项） |
| `S1_CHANGE_REPORT.md` | S1 变更报告本体 |
| `S1_EXIT_REVIEW.md` | 引用自 `S1_SCOPE_ADJUDICATION.md` §0/§3 |
| `S1_SCOPE_ADJUDICATION.md` | 引用自 `ADR-019` 前言 |
| `ADR-019-s1-scope-adjudication.md` | **`docs/ADD.md` §2 索引 + §2.1 路径表**（正式登记） |
| `S1_DOCUMENTATION_CLOSURE.md` | C-2/C-3 收口报告本体 |
| `docs/ADD.md` · `docs/EVENT-SCHEMA.md` | 本体（登记动作产物） |
| `ARCHITECTURE_DECISION_RECORD.md` | 承载 ADR-013~018 正文；**经 `ADD.md` §2.1 登记** |
| `REFACTOR_PLAN.md` · `GOVERNED_AGENT_RUNTIME_DESIGN.md` | 审计/设计阶段产物，被 ADR/各报告引用（可溯源） |
| `docs/baseline/*`（4 篇） | S0 前置产物，`BASELINE_REPORT.md` 为索引 |

→ **无孤儿交付物**。

---

## 3. 边界确认（本轮自查）

| 禁止项 | 状态 |
|---|---|
| 不修改 `.py` | ✅ 未修改（检查 #8） |
| 不修改测试 | ✅ 未修改（同 #8） |
| 不创建 `pyharness/governance/` | ✅ 不存在 |
| 不实现 Policy / DecisionEngine / Decision Receipt / Evidence | ✅ 未实现 |
| 不修改 EventBus | ✅ 未修改 |
| 不增加事件实现 | ✅ 未增加（词表仍 73 型；仅文档预登记） |
| 不进入 S2 | ✅ 未进入 |

**本轮产物**：`S1_FINAL_EXIT_GATE.md`（唯一新增文件）

---

## 4. 建议的记录动作（**非阻塞**，不属本闸判据）

| # | 建议 | 理由 |
|---|---|---|
| **R-1** | 将 P-6 裁定回填 `S1_DOCUMENTATION_CLOSURE.md` §6.2/§7（现记为"未满足 / 待确认"） | 保持文档一致；**本文件已为裁定正式记录**，故不影响任何门禁 |
| R-2 | 修正 `REFACTOR_PLAN.md §2.3/§4.4` 与 `S1_EXIT_REVIEW.md F-8` 的两处过度表述（M-1 落盘适配器"5 处各自手写"实为 2 处；M-2 `_invoke` 非纯死代码） | 依 `S1_SCOPE_ADJUDICATION.md` §4 的修正 |
| R-3 | 将 `tmp/` 加入 `.gitignore` | 见 §5 第 3 项 |

---

## 5. Git Commit Preparation

> **未执行 `git commit`**（按指令）。以下为提交前的准备与待确认项。

### 5.1 当前 HEAD

```
0cba75db6a8f75b354e86b15e5f415264f7c91ba
0cba75d 2026-09-14 sync: 审计修复(P0–P3) + Protocol v0.3 + 脱敏
```
**HEAD 未前进**——S0/S1 全部产出仍在工作区（**无回退锚点**，即 Exit Review 的 F-4）。

### 5.2 本次 S0/S1 全部未提交文件

**已修改（tracked，13 项）**

| 类别 | 文件 |
|---|---|
| S1 源码（5） | `pyharness/core/tools_guard.py` · `pyharness/engine.py` · `pyharness/core/agent_loop.py` · `pyharness/core/agent.py` · `pyharness/core/session.py` |
| S1 测试（4） | `tests/unit/test_engine.py` · `tests/unit/test_agent_loop.py` · `tests/unit/test_agent.py` · `tests/unit/test_session.py` |
| S1 文档（2） | `docs/MAP.md` · `docs/DIS-CORE.md` |
| C-2 登记（2） | `docs/ADD.md` · `docs/EVENT-SCHEMA.md` |

**未跟踪（交付物）**

| 类别 | 文件 |
|---|---|
| 审计/设计/冻结（3） | `REFACTOR_PLAN.md` · `GOVERNED_AGENT_RUNTIME_DESIGN.md` · `ARCHITECTURE_DECISION_RECORD.md` |
| S1 系列报告（4） | `S1_CHANGE_REPORT.md` · `S1_EXIT_REVIEW.md` · `S1_SCOPE_ADJUDICATION.md` · `S1_DOCUMENTATION_CLOSURE.md` |
| ADR-019（1） | `ADR-019-s1-scope-adjudication.md` |
| S0 基线（4） | `docs/baseline/{BASELINE_REPORT,TEST_BASELINE,CODE_METRICS,CURRENT_ARCHITECTURE}.md` |
| 本闸报告（1） | `S1_FINAL_EXIT_GATE.md` |
| **应当排除** | `tmp/`（见下） |

### 5.3 是否存在明显与 S0/S1 无关的文件

| 文件 | 判定 |
|---|---|
| `tmp/`（720K，9 文件） | ⚠️ **与 S0/S1 无关，且含本机账户名标识**——对 tmp/ 做仓库账户名 token 扫描 → **9 文件全部命中**；`junit*.xml` **未被 `.gitignore` 忽略**（`*.log` 已被忽略）。**不得入库** |
| 其余未跟踪文件 | 均为 S0/S1（或其前置）产物，**相关** |
| 仓库根的其他历史遗留（`probe_*.log`、`pytest_*.log`、`简历*.docx`、`collect_only.txt` 等） | 已被 `.gitignore` 覆盖，不在待提交集内 ✅ |

### 5.4 建议的 commit message

> 遵循本仓库既有风格（`<前缀>: <中文描述>`）；**不署 AI 名**（公开仓库纪律）。

```
fix: S1 运行时完整性修复(四项) + 治理期架构文档入库 + ADR 登记至 019

- S1-01 engine 改用 GuardChain.from_config 并注入 validator
  (修复 g1 g-schema 生产恒 allow；cfg.security.guards.disabled 恢复生效)
- S1-02 按 ADR-014 清理 AgentLoop 死态(paused/stopping/terminated)，
  收敛为 idle/running，删除调用不存在 resume() 的死分支
- S1-03 F026 轮内连败计数接入主循环单步路径(此前仅批量入口生效)
- S1-04 session.append 的 _closed 置位失败回滚，使 close 可重入
- 文档：治理期冻结记录/设计/审计/基线入库；ADD.md 登记 ADR-013~019(12→19 条)；
  EVENT-SCHEMA 预登记治理层 4 事件(F 组，未实现)
- 测试：1450 passed / 2 skipped / 0 failed(可运行集；
  desktop_native 因缺 PySide6 未收集，属基线遗留)
```

### 5.5 commit 前需要人工确认的事项

| # | 事项 | 说明 |
|---|---|---|
| **C-A** | **`tmp/` 必须排除** | 含本机账户名标识；建议先加 `.gitignore` 一行 `tmp/`，或提交时逐文件 `git add`（**不要用 `git add -A`/`git add .`**） |
| **C-B** | **公开仓库的披露决策** | remote = `github.com/zinuotiger/pyharness`（**公开**）。`S1_CHANGE_REPORT.md`、`REFACTOR_PLAN.md`、`docs/baseline/BASELINE_REPORT.md`、`docs/baseline/CURRENT_ARCHITECTURE.md` **详细描述了尚未修复的安全弱点**（g1 恒 allow、租户隔离半真实、`s-fork` 正则缺陷）。**是否公开披露需你决定**；可选：先 `commit` 不 `push`，或把这些细节改为内部文档后再发布 |
| **C-C** | **`ARCHITECTURE_DECISION_RECORD.md` 与 `ADR-019-s1-scope-adjudication.md` 必须一并提交** | `docs/ADD.md` §2.1 以相对链接 `../ARCHITECTURE_DECISION_RECORD.md` / `../ADR-019-s1-scope-adjudication.md` 指向它们；**漏提交 → 已发布仓库中链接断裂** |
| **C-D** | 提交信息**不署 AI 名** | 公开仓库纪律（已在 §5.4 遵循） |
| **C-E** | 是否顺带回填 §4 的 R-1/R-2/R-3（P-6 记录、M-1/M-2 修正、`tmp/` 忽略） | 均为文档/配置，非阻塞 |
| **C-F** | 行尾（LF/CRLF） | 本次编辑写入 LF，仓库部分文件为 CRLF，`git diff` 有 warning；确认 `core.autocrlf` 行为不产生整文件重写（当前 `git diff --stat` 未见整文件重写） |

**建议提交粒度**（可选，便于回退定位）：
1. `fix:` S1 源码 + 测试（9 文件）→ 建立代码回退锚点；
2. `docs:` 治理期文档 + ADR 登记（13 文件）→ 文档与代码分离，便于单独回退。

---

## 6. 停止点

**最终 Gate = PASS；S1 COMPLETE；S2 ENTRY CONDITION = MET。**

按指令：**本轮不进入 S2**——未修改 `.py`、未创建 `pyharness/governance/`、未实现 Policy / DecisionEngine / Decision Receipt / Evidence、未修改 EventBus、未增加事件实现、**未执行 `git commit`**。

**等待人工确认**：§5.5 的 C-A~C-F（尤其 **C-A（排除 `tmp/`）** 与 **C-B（公开披露决策）**），以及是否授权启动 S2。
