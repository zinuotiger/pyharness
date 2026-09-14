# S1_CHANGE_REPORT.md — Runtime Integrity Repair（S1）变更报告

> **阶段**：S1（Governed Agent Runtime v1.0 开发顺序第 1 步；见 [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) §5）
> **基线**：`0cba75d`（[docs/baseline/](docs/baseline/BASELINE_REPORT.md)）→ 本报告描述工作区改动
> **范围纪律**：不新增 governance 代码 · 不改变 v1.0 架构 · 不进入 Governance Layer
> **原则**：每项修改必须有测试证明；每项修改在本报告登记证据

---

## 0. 一句话结论

修复了治理层接入前必须清掉的 **4 类运行时基础问题**（防线空转、死态、计数失效、状态不一致）。
**1450 passed / 2 skipped / 0 failed**（基线 1442/2/0，新增 8 例），退出码 0，全量回归无红。

> **⚠️ Gate 状态（2026-09-14 追记；勿据此进入 S2）**
> - 本报告只描述 **S1-01~04 的实施**。S1 出口审查结论为 **CONDITIONAL PASS**（见 [S1_EXIT_REVIEW.md](S1_EXIT_REVIEW.md)），**非 PASS**。
> - S1 范围歧义已由 **S1 Scope Adjudication 裁定 ACCEPT**（见 [S1_SCOPE_ADJUDICATION.md](S1_SCOPE_ADJUDICATION.md) · [ADR-019-s1-scope-adjudication.md](ADR-019-s1-scope-adjudication.md)）：**S1 实施范围 = S1-01~04 四项**；§5.2 剩余 5 项按依赖分流（W3→S1.5；拆分/落盘收敛→S2 内；repair/`_invoke`→S1.5/S6）。
> - **C-2（S0/S1 文档登记）与 C-3（本报告数字修正）完成前，不得进入 S2。** 本报告中出现"并入 S2 前置"等表述的，一律以 ADR-019 的分流结论为准。

---

## 1. 总览

| 项 | 值 |
|---|---|
| 改动文件 | **11**（5 源 + 4 测试 + 2 文档） |
| 增删 | **+436 / −87** |
| 测试（可运行集） | **1452 collected / 1450 passed / 2 skipped / 0 failed** |
| 基线对比 | 1444 → **1452** collected（**+8** 例）；passed 1442 → **1450** |
| 耗时 | 34.5 s（最终确认跑） |
| 覆盖率 | 总 77%（语句 16,964→16,986）；`tools_guard.py` **92% → 94%** |
| 未执行面 | 不变：`desktop_native/**` 因缺 PySide6 不可收集（2 文件 / 5 用例） |

**改动文件清单**（增删列 = `git diff --numstat` 实测值，口径见 §1.1）

| 文件 | 增删 | 类别 |
|---|---|---|
| `pyharness/core/tools_guard.py` | +12 / −2 | S1-01 源 |
| `pyharness/engine.py` | +14 / −2 | S1-01 源 |
| `pyharness/core/agent_loop.py` | +52 / −11 | S1-02 + S1-03 源 |
| `pyharness/core/agent.py` | +11 / −10 | S1-02 源 |
| `pyharness/core/session.py` | +58 / −32 | S1-04 源 |
| `docs/DIS-CORE.md` | +3 / −1 | S1-02 文档同步 |
| `docs/MAP.md` | +3 / −3 | S1-02 文档同步 |
| `tests/unit/test_engine.py` | +74 / −0 | S1-01 测试 |
| `tests/unit/test_agent_loop.py` | +92 / −11 | S1-02 + S1-03 测试 |
| `tests/unit/test_agent.py` | +32 / −15 | S1-02 测试 |
| `tests/unit/test_session.py` | +85 / −0 | S1-04 测试 |
| **合计（11 文件）** | **+436 / −87** | — |

### 1.1 数字口径与勘误（2026-09-14 C-3 修正）

**口径**：增删列取自 **`git diff --numstat`**（纯新增 / 纯删除行数）。

```bash
git diff --numstat -- docs/DIS-CORE.md docs/MAP.md pyharness/core/agent.py pyharness/core/agent_loop.py pyharness/core/session.py pyharness/core/tools_guard.py pyharness/engine.py tests/unit/test_agent.py tests/unit/test_agent_loop.py tests/unit/test_engine.py tests/unit/test_session.py
```

**修正说明**：本表此前 8/11 行有误——误把 `git diff --stat` 的**"总变动行数"（additions+deletions）当成 additions**（例如 `agent.py` 的 `21` 实为 `11+10`）。合计 `+436 / −87` 当时即正确，故**总数不变，仅逐文件修正**。

**范围界定**：上表为 **S1 改动集（11 文件）**。此后 C-2 另加 `docs/ADD.md`、`docs/EVENT-SCHEMA.md` 两个**文档**文件（登记补齐，非 S1 代码改动），不在本表内。

---

## 2. S1-01 — 修复 engine 绕过 `GuardChain.from_config`

### 2.1 修改文件
`pyharness/core/tools_guard.py`（`from_config` 签名扩展）· `pyharness/engine.py`（装配改用工厂）· `tests/unit/test_engine.py`（+2 生产路径测试）

### 2.2 修改原因
`engine.py` 此前直构 `GuardChain(session=log_, bus=bus)`，**绕过了 `tools_guard.from_config()`**（该工厂在仓库中存在且已有单测，但**零生产调用点**）。后果三处：

| # | 缺口 | 机制 |
|---|---|---|
| ① | **g1 g-schema 生产恒 allow** | `from_config` 未透传 `validator` → `build_builtin_chain` 默认 `validator=None` → `g_schema_check` 直接 `return ("allow", None)`。即 INV-04 承诺的"内层复查防旁路"在生产链路上**永不生效** |
| ② | **`cfg.security.guards.disabled` 永不生效** | 只有 `from_config` 会读该配置并逐个 `disable()`；直构路径完全不读 |
| ③ | **凭据清单 / path_exists / link_resolver 与 config 声明脱节** | 直构时走模块默认值 |

**根因**：`from_config` 的签名（原）**不接受** `validator` / `path_exists` / `link_resolver`——即便装配层想用工厂也无从注入 g1 的校验面。故修复分两步：**先补工厂注入位，再切装配**。

### 2.3 实现要点
1. `from_config(...)` 新增三个关键字参数 `validator` / `path_exists` / `link_resolver`（**全部缺省 `None` = 既有行为**，API 向后兼容），并透传给 `GuardChain`。
2. `engine.py` 改为 `guard_from_config(cfg, session=log_, bus=bus, validator=tool_reg.validate_args, approval_channel=True)`。
3. `approval_channel=True` 为**行为保持**选择：`GuardChain.evaluate` 仅在 `_approval_channel is False` 时把 `approval` 降级为 `reject`，`None` 与 `True` 等价；故不改 headless 行为（无通道场景仍由 `approval` 层 APR-501 兜底拒绝）。

### 2.4 证据（生产路径测试）
`tests/unit/test_engine.py` 新增 2 例，均经 `build_spine()` 走**真实装配**：

| 用例 | 断言 | 证明 |
|---|---|---|
| `test_guard_from_config_injects_schema_validator` | 畸形参数（`fs.read_file` 缺必填 `path`）→ `evaluate` 返回 **reject**；合规参数 → **allow** | **g1 真的在拦**（修复前必为 allow） |
| `test_guard_from_config_applies_cfg_disabled` | 同一越界调用（`fs.list_dir` + `path=../../outside`）：默认链 **reject**（g-fs-path）；`disabled=["g-fs-path"]` 后 **allow** 且 `enabled_guard_ids()` 不含该项 | **`guards.disabled` 真的在生效** |

> 判别式选择说明：`fs.list_dir` 的 `danger=none` 且不被 g4/g6/g7/g5 管辖，故该调用**只**由 g3 g-fs-path 裁决——禁用后放行，是 `disabled` 生效的干净判别式。

### 2.5 风险与回退
**风险**：
- **中**：g1 由空转变为生效，任何"参数非法但此前能穿过 guard"的调用现在会被拒。**缓解**：正常路径的证据是 `tools_executor` 关 1b **已先做同一校验**（`registry.validate_args`），故 g1 只是复核，正常路径不受影响；全量回归 1450 例零红即为此提供证据。
- **低**：`validator` 绑定的是 `tool_reg.validate_args` 绑方法；插件/MCP 后续注册的工具在**调用时**解析，不受装配时点影响。

**回退方式**：
1. 单文件回退 `engine.py` → 恢复 `GuardChain(session=log_, bus=bus)`（`from_config` 的签名扩展是纯向后兼容，可保留）；
2. 或仅回退 `validator=` 实参 → 等价于修复前行为（g1 回到空转）。**不需改数据、不需改测试以外的东西**。

---

## 3. S1-02 — 隔离 `paused`/`resume` 死态（依 ADR-014）

### 3.1 修改文件
`pyharness/core/agent_loop.py` · `pyharness/core/agent.py` · `docs/DIS-CORE.md` · `docs/MAP.md` · 测试 2 文件

### 3.2 修改原因
ADR-014 决定 **v1.0 不实现运行时暂停/恢复**。审计确认为死态并伴随一个活体缺陷：

- `agent_loop.py` 声明 `LoopState = Literal["idle","running","paused","stopping","terminated"]`，但**只有 `"running"`(:160) 与 `"idle"`(:179) 被赋值**——其余三态全文无赋值点；
- 由此衍生死分支：`wake()` 的 `stopping/terminated` 拒入、`_must_stop` 闸3 的 `"paused"` 判据（`Scope.budget_state` 只返回 `ok/warn/exhausted`）、`agent.close` 对 `"paused"` 的判断；
- **活体缺陷**：`agent.py` 在 `approval.granted` + `loop.state == "paused"` 时调用 `await self.ctx.loop.resume()`——`AgentLoop` **没有该方法**。分支不可达，但**一旦可达即 `AttributeError`**；
- **替身掩盖**：`tests/unit/test_agent.py` 的 `FakeLoop` 自带 `resume()`，使该缺陷长期"假绿"。

### 3.3 实现要点
| 位置 | 改动 |
|---|---|
| `agent_loop.py` `LoopState` | 收敛为 `Literal["idle","running"]`，并注明 paused 归 v1.1 |
| `agent_loop.py` `wake()` | 删除 `state in ("stopping","terminated")` 拒入分支（会话关闭拒入归 `agent.submit`，其 `agent.state` 判据真实在用） |
| `agent_loop.py` `_must_stop` | 闸3 判据 `in ("paused","exhausted")` → `== "exhausted"` |
| `agent.py` `close()` | `loop.state in ("running","paused")` → `== "running"` |
| `agent.py` `_on_bus_event()` | **删除** `approval.granted → loop.resume()` 分支；handler 收敛为纯只读可见性钩子 |
| `agent.py` docstring(7) | 同步说明删除理由与替代路径 |
| 测试 `FakeLoop` | **移除** `resume()` / `resumes`——替身面须与生产契约一致（消除掩盖） |
| `docs/DIS-CORE.md` §1.2/§1.4 | 加 **v1.0 修订注**（不重写设计稿；ADR 只增不改） |
| `docs/MAP.md` :39/:166/:226 | "三态机" → "两态机(v1.0)" |

**不实现新的暂停系统**（严守 S1 边界）：审批等待仍由 `task_queue` 的 `queue.suspended/resumed` 承担，不经 loop 暂停。

### 3.4 证据
10 例新增/改写（明细见 §6）。关键两例：

| 用例 | 断言 |
|---|---|
| `test_agent.py::test_agent_loop_has_no_resume_api` | `not hasattr(AgentLoop, "resume")` —— **以"生产类无此 API"钉死契约，取代旧的 resume 断言** |
| `test_agent_loop.py::test_loop_state_space_is_reachable_only` | `set(get_args(LoopState)) == {"idle","running"}` —— 防止死态被重新引入 |

其余改写：`test_wake_stopping_terminated_busy`（断言死态拒入）→ 改为 `test_wake_accepts_from_any_reachable_state`；`test_must_stop_three_gates_readonly` 的 `budget="paused"` → `warn`/`exhausted` 两档；`test_on_bus_event_*` 两例改为"不改状态"断言。

### 3.5 风险与回退
**风险**：
- **低**：无外部依赖者（全库 grep 确认 `loop.resume` 仅 `agent.py` 一处调用；`loop.state` 的 paused 判据仅 3 处）。
- **低**：`_must_stop` 收窄后，"paused" 不再触发预算闸——该值本就不可能由 `scope` 产出，故无行为变化。
- **已知**：若 v1.1 实现真暂停，须按新 ADR 重新引入状态与转移（**不复用本次删除的形态**，ADR-014 已写明）。

**回退方式**：`git checkout` 上述 4 个源文件 + 测试文件即可（纯删除性改动，无数据影响）。文档加注可独立保留（其内容描述的是 v1.0 事实）。

---

## 4. S1-03 — 修复 F026 连败计数主链失效

### 4.1 修改文件
`pyharness/core/agent_loop.py` · `tests/unit/test_agent_loop.py`

### 4.2 修改原因
F026 规格：**轮内同工具连败 ≥2 → 终止本轮**。实现只在**批量入口** `ToolExecutor.execute_tool_calls()` 里做了计数与 `break`；而 agent-loop 的实际主循环走的是**逐个** `run_step → ctx.tools.execute`（`run_turn` 的 `for call in resp.tool_calls`）。

⇒ `reset_turn_failures()` / `mark_turn_failure()` 在**生产主链上一次都没被调用**，F026 在主链上**完全失效**（只在单测直接调批量入口时生效）。

### 4.3 实现要点
1. `run_step` 增加返回值：返回 `ExecResult`（此前返回 `None`），供调用方判定。
2. `run_turn` 工具循环：轮起点调 `_reset_turn_failures(ctx)`；每步后用 `_turn_failure_capped(ctx, call, res)` 判定，命中则 `break`。
3. 两个新私有辅助（`agent_loop` 内）用 `getattr(ctx.tools, ...)` **鸭子调用**，不 import `tools_executor`（守 INV-08）：
   - `_reset_turn_failures`：`ctx.tools.reset_turn_failures()` 存在则调用（替身/旧装配缺失 → 跳过）；
   - `_turn_failure_capped`：**计入口径与批量入口逐条一致**——仅 `summary` 以 `"参数校验"` 或 `"guard"` 开头且 `ok=False` 才计入；`mark_turn_failure` 累计 ≥2 即封顶。

### 4.4 证据
| 用例 | 断言 |
|---|---|
| `test_turn_failure_streak_terminates_turn_on_single_step_path` | 3 个同工具调用、前两个 guard 失败 → **只执行 2 次**（第 3 个不执行）、`fail_counts["mock_tool"] == 2`、`resets >= 1` |
| `test_turn_failure_streak_not_counted_for_other_failures` | 3 个同工具调用、全部"执行超时"（非 参数校验/guard）→ **执行 3 次**、`fail_counts == {}`（口径与批量入口一致） |

为支撑用例，`FakeTools` 扩展了 `reset_turn_failures` / `mark_turn_failure` / `results` 脚本；**缺省仍返回 `None`**，故既有用例行为不变（全量回归为证）。

### 4.5 风险与回退
**风险**：
- **中（行为变更）**：主链上"同工具连续 2 次 参数校验/guard 失败"现在会**提前结束本轮**，剩余调用不执行。**缓解**：这正是 F026 的规格语义（且批量入口一直如此）；计入面被严格限制在 2 类失败，超时/Provider 异常/审批 denied **不计入**（有专项用例锁定）。
- **低**：`run_step` 由返回 `None` 改为返回 `ExecResult`——已检查其返回值不被其它调用方消费（仅 `run_turn` 使用）。

**回退方式**：回退 `agent_loop.py` 的 `run_turn` 内 3 行 + 删除两个辅助 + `run_step` 恢复返回 `None`；测试随之回退。**无数据影响**。

---

## 5. S1-04 — 修复 Agent close 状态一致性

### 5.1 修改文件
`pyharness/core/session.py` · `tests/unit/test_session.py`

### 5.2 修改原因
`SessionLog.append` 对 `session.finished` 采取"**先置位、后校验/落盘**"：在第 3 步就设 `self._closed = True`，而能失败的动作（`make_envelope` 校验 EVT-100/102、`_flush` 落盘 PERS-202）都在其**之后**。

⇒ 任一步失败后 `_closed` **永久为真**；`agent.close` 虽然回滚了自身 `state` 并声称"幂等可重入"（docstring 6），但重试会被 `append` 第 1 步 `EVT-104` 直接短路——**契约不成立**。

### 5.3 实现要点
1. **把 `_closed` 置位与其后的全部可失败动作收进同一 `try`**，`except BaseException` 时按**内存事实**回滚：
   - 事件**未入缓存**（校验在 `make_envelope` 阶段失败）→ `self._closed = False`（**本次修复的主目标**）；
   - 事件**已入缓存**（`absorbed` 且缓存尾已是 `session.finished`）→ 保持 `_closed = True`（与内存事实一致，不给"二次 finished 写入"留口）。
2. 置位区间仍为**同步段**（第 1 步至此无 `await`），故并发双写的原有防护**未被削弱**。
3. 顺带把 `session.created` / 引用锚点预检（纯校验、不会因 `_closed` 变化）移入 `try` 之前，逻辑等价、读序更清晰。

### 5.4 证据
| 用例 | 断言 |
|---|---|
| `test_finished_validation_failure_rolls_back_closed` | finished 载荷非法（`reason` 必填）→ `EVT-100` **且 `log._closed is False`** → **同进程内重试成功** → 再次 append 仍 `EVT-104`（终态只写一次不被破坏） |
| `test_finished_flush_failure_keeps_closed_but_log_empty` | flush 失败（PERS-202）→ `_closed is True`（内存事实）+ `store._rows == []`（**真源为空**，物理上未发生）——**边界语义锁定** |

后一例的 fake 采用 **"缓冲未落盘"模型**（`record` 入待刷缓冲、`flush` 成功才落 replay 面），与真实 `SessionStore.append`（入 `_pending` → flush 才 write）语义对齐，故 `_rows == []` 是"落盘失败 ⇒ 未发生"的物理证据。

### 5.5 风险与回退
**风险**：
- **低**：回滚分支仅在失败路径执行；正常路径与修复前逐行等价（`try` 无非正常开销）。
- **已知残留（已登记，见 §7 R-2）**：`_closed=True` 而真源为空时，同进程内重试仍 `EVT-104`；跨进程由 repair/重启按日志事实重算。该边界**刻意保留**（回滚会制造"缓存有 finished 而 `_closed=False`"的脱钩），与 ADR/契约的"repair 后重试可重入"口径一致。

**回退方式**：回退 `session.py` 的 `append` 到"置位前置、无回滚"形态；测试随之回退。**无数据影响**（未改任何事件格式或落盘格式）。

---

## 6. 测试变化

### 6.1 总量

| 指标 | 基线 `0cba75d` | S1 后 | 变化 |
|---|---|---|---|
| collected | 1444 | **1452** | **+8** |
| passed | 1442 | **1450** | **+8** |
| skipped | 2 | 2 | — |
| failed / error | 0 / 0 | **0 / 0** | — |
| 耗时 | 39.97 s | 34.53 s | — |

### 6.2 逐项明细（12 处增删改）

**S1-01（`test_engine.py`，+2 新增）**
1. `test_guard_from_config_injects_schema_validator`
2. `test_guard_from_config_applies_cfg_disabled`

**S1-02（改写 3、新增 1、替身收口 1）**
3. `test_agent_loop.py`：`test_wake_stopping_terminated_busy`（删）→ `test_wake_accepts_from_any_reachable_state` + **新增** `test_loop_state_space_is_reachable_only`
4. `test_agent_loop.py`：`test_must_stop_three_gates_readonly`（预算档位改写，纳入 `warn`/`exhausted` 两档）
5. `test_agent.py`：`test_on_bus_event_ignores_foreign_session`（改写为"不改状态"）+ `test_on_bus_event_approval_granted_resumes_paused_loop`（删）→ **新增** `test_agent_loop_has_no_resume_api` + `test_on_bus_event_approval_granted_does_not_mutate`
6. `test_agent.py`：`FakeLoop` **移除** `resume()` / `resumes`（消除替身掩盖）

**S1-03（`test_agent_loop.py`，+2 新增 + 替身扩展）**
7. `test_turn_failure_streak_terminates_turn_on_single_step_path`
8. `test_turn_failure_streak_not_counted_for_other_failures`
9. `FakeTools` 增 `reset_turn_failures` / `mark_turn_failure` / `results`（缺省行为不变）

**S1-04（`test_session.py`，+2 新增 + 1 替身）**
10. `test_finished_validation_failure_rolls_back_closed`
11. `test_finished_flush_failure_keeps_closed_but_log_empty`
12. 新增 `_PendingBufferStore`（对齐真实 persistence 的"缓冲 → flush 才落盘"模型）

### 6.3 覆盖率

| 模块 | 基线 | S1 后 |
|---|---|---|
| `core/tools_guard.py` | 92% | **94%**（missed 31→21；S1-01 用例打到 `from_config` 透传与 g1 拒绝路径） |
| `core/agent_loop.py` | 95%（183 stmt） | 95%（198 stmt，新辅助已覆盖） |
| `core/session.py` | 97%（223 stmt） | 97%（232 stmt） |
| `core/agent.py` | 89%（221 stmt） | 89%（219 stmt，删除死分支） |
| **总计** | 77%（16,964 stmt） | 77%（16,986 stmt） |

### 6.4 契约变更（测试为"有意行为变更"，非缺陷，依 CORE-03 判定）
| 变更 | 依据 |
|---|---|
| `LoopState` 由 5 态声明收敛为 2 态 | **ADR-014**（v1.0 不支持运行时暂停） |
| `wake()` 不再对 `stopping/terminated` 拒 BUSY | 同上（死态无赋值点；会话关闭拒入归 `agent.submit`） |
| `_must_stop` 闸3 仅 `exhausted` 触发 | 同上（`paused` 不在 `scope` 返回面） |
| `_on_bus_event` 不再调 `loop.resume()` | 同上（方法从不存在） |
| 主链同工具连败 ≥2 终止本轮 | **F026 规格**（补齐主链与批量入口的口径差） |
| `_closed` 在 finished 校验失败时回滚 | **`agent.close` docstring 6**（"幂等可重入"契约） |

---

## 7. 风险汇总与残留登记

| # | 残留 / 风险 | 级别 | 处置 |
|---|---|---|---|
| **R-1** | `guard.disabled` 事件**不在事件词表**（73 型之外）→ `from_config` 的 `disable()` 审计留痕经 `_record` 降级为**本地日志**（不落 JSONL） | 低 | 词表扩展属治理层/事件模块范围，**不在 S1**；已在 `tools_guard.py:38` 既有偏离说明中登记 |
| **R-2** | S1-04 边界：finished 已入内存后 flush 失败 → 同进程内重试仍 `EVT-104`（跨进程 repair 后可重入） | 低 | **刻意保留**并有用例锁定；已在 `session.py` 注释与 §5.5 说明 |
| **R-3** | g1 由空转变为生效：理论上有"此前能穿过 guard 的非法参数"现被拒 | 中 | 关 1b 已做同一校验 → 正常路径不受影响；1450 例全量回归零红 |
| **R-4** | `approval_channel=True` 硬编码于 engine：guard 的通道位**不随** `approval` 层的实际通道漂移（headless 仍靠 APR-501 兜底） | 低 | 与修复前语义等价；更紧的同源推导留待后续（需改 `attach_engine_to_ctx` 的装配序） |
| **R-5** | `docs/SECURITY.md` 等文档仍按设计稿描述 loop 暂停语义 | 低 | 本次仅同步 `MAP.md` + `DIS-CORE.md`（加注）；其余文档随 S6 文档一致性校验统一处理 |
| **R-6** | 本次编辑写入 LF，仓库部分文件为 CRLF（`git diff` 有 warning） | 极低 | `git diff --stat` 未出现整文件重写；提交时由 `core.autocrlf` 归一 |
| **R-7** | `desktop_native/**` 仍不可测（缺 PySide6） | 中 | **基线遗留**（[BASELINE_REPORT.md](docs/baseline/BASELINE_REPORT.md) B-1），非 S1 范围 |
| **R-8** | `agent.submit` 首次唤醒疑传 `ctx` 缺失（基线 V-1） | 待定 | **S1 未处理**（不在四项内）；仍待核实后决定删除或补传 |

---

## 8. 证据留证

| 文件 | 内容 |
|---|---|
| `tmp/baseline/pytest_s1_full.log` | S1 全量（含覆盖率） |
| `tmp/baseline/pytest_s1_final.log` | S1 最终确认跑 |
| `tmp/baseline/junit_s1.xml` · `junit_s1_final.xml` | 机器可读权威计数（各 1452 testcase） |

**复现命令**

```bash
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --ignore=tests/unit/test_desktop_native.py --ignore=tests/unit/test_shell_parity.py -q --no-header --junit-xml=tmp/baseline/junit_s1_final.xml
```

---

## 9. 出口判据核对（S1 Gate）

> **范围口径已由 [ADR-019](ADR-019-s1-scope-adjudication.md) 裁定**（S1 Scope Adjudication = **ACCEPT**）：S1 实施范围 = S1-01~04 四项；§5.2 S1 行出口判据 **③（预算单信号）随 W3 移出 S1**，改由 S1.5 承担。下表按**裁定后**判据核对。

| ADR §5.2 S1 行出口判据 | 结果 |
|---|---|
| ① g1 注入 validator 后**能**拒绝非法参数 | ✅ `test_guard_from_config_injects_schema_validator` |
| ② `cfg.security.guards.disabled` 生效 | ✅ `test_guard_from_config_applies_cfg_disabled` |
| ③ 预算单信号 | ➡️ **已移出 S1**（ADR-019 裁定 3 → S1.5，见下方 R-9） |
| ④ 全量回归绿 | ✅ 1450 passed / 0 failed / 2 skipped |
| ⑤ `AgentLoop` 无 resume 且测试改为 `hasattr` 负断言 | ✅ `test_agent_loop_has_no_resume_api` + `test_loop_state_space_is_reachable_only` |

**裁定后 S1 判据 = ① ② ④ ⑤ 四条，全部满足**；③ 移出后不再阻塞 S1。

**审查结论（2026-09-14 追记）**：
- **S1 出口审查 = CONDITIONAL PASS**（[S1_EXIT_REVIEW.md](S1_EXIT_REVIEW.md)）——**非 PASS**。其阻塞项中，"范围歧义"已由 S1 Scope Adjudication（**ACCEPT**）解除；**F-3（S0 登记未闭合）/ F-5·F-6（本报告数字）** 分别由 **C-2 / C-3** 处理。
- **S2 前置条件 = 未满足（NOT MET）**：依 `ARCHITECTURE_DECISION_RECORD §5.3`「必须串行 S0 → S1 → S2」与 GATE-01（以**已登记**的 ADR 为契约核对依据），**在 C-2 完成前不得进入 S2**。本报告不含、也不支持"可直接进入 S2"的任何表述。

**S1 残留（未做，勿误以为已修）**：

| # | 残留 | 处置（依 ADR-019） |
|---|---|---|
| **R-9** | 预算两套信号未收敛：`scope.check_budget()` 抛 `BudgetExhausted`（→ `reason=budget`）vs `llm_fallback.BudgetGuard.check` 抛 `PyHError("BUDGET-EXHAUSTED")`（→ 落 `except PyHError` → `reason=error`） | ➡️ **移出 S1**，承接阶段 = **S1.5**（依 ADR-019：非 S2 硬前置；改终态语义与错误码契约，建议独立小 ADR） |

---

## 10. 未进入 Governance Layer 的确认

| 确认项 | 状态 |
|---|---|
| 未新增 `pyharness/governance/` 任何文件 | ✅ |
| 未新增 `PolicyEngine` / `DecisionEngine` / `DecisionReceipt` / `EvidenceCollector` / `AuditSystem` | ✅ |
| 未新增事件类型（词表仍 73 型） | ✅ |
| 未改变 v1.0 架构（3 处接缝未动） | ✅ |
| 改动仅为"接线收口 + 死态清理 + 计数补齐 + 状态回滚"四类 | ✅ |
