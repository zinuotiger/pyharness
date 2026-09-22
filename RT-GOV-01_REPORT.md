# RT-GOV-01_REPORT

> **性质**：实施事实整理（**未修改生产代码 / 未修改测试 / 未新增功能**）。
> **基线**：`HEAD = ebbb444`（含 F-27 的 C1/C2）
> **状态**：已实施、已验证、**未提交**
> **变更面**：2 文件（1 行功能 + docstring / 5 个用例）

---

## 1. Background（背景）

### 1.1 发现的问题

子 Agent（`EngineSubagentRunner`）与**无队列** IntentRunner 兜底路径的**每一次**工具调用都以 `CYC-999` 失败——且**失败发生在 `tool.call` 发射之前**，事件日志中连 `tool.call` 都没有，只留一条：

```
system.error  {code: "CYC-999",
               hint: "ctx.governance 未接线:关2 唯一治理入口缺失,拒绝执行(S3-2-2)"}
```

**后果**：子 Agent 无法使用任何工具，只能产出纯文本；无队列兜底编排任务一律判失败。因日志无 `tool.call`，现象极易被误读为"模型没有调用工具"，而非装配缺陷。

### 1.2 触发原因

本问题在**架构现实校验（Architecture Reality Check）→ Functional Runtime Audit** 阶段被发现，触发路径是**判据倒推**：

- 审计关注"代码存在但**没有真实调用**"这一模式；
- 对 `ctx.governance` 做**全库 ctx 构造点扫描**（`SimpleNamespace(` / `wake(` 调用点）时发现：`wake()` 全库仅 **3 处**，其中 2 处的 ctx 含 `governance`，**唯 `orchestration._runtime_ctx` 不含**；
- 随后以真实引擎 + 真实子 Agent（调用 `util.now`）**实测复现**，确认可达且必现。

**关键前提**：该缺陷在修复前**未曾暴露**——因为下游 `_require_wiring` 把它拦成了 fail-closed。也就是说，**是"缺治理"导致"缺执行"，而"缺执行"又掩盖了"缺治理"**。

### 1.3 为什么属于 runtime governance wiring 问题

| 判据 | 说明 |
|---|---|
| **不是判定逻辑错误** | g1–g7 规则链、`Decision` 三值、单调拒绝面**全部完好**；不存在"判定错了"的情形 |
| **不是缺失能力** | `GovernanceContext` / `PolicyEngine` / `authorize()` / `DecisionEngine` 均已装配且在岗 |
| **而是运行期 ctx 未挂治理实例** | 治理层**存在但不可达**：链与治理对象都在 spine 上，唯独**子/兜底运行期 ctx 没把它们带过去** |
| **失效形态是 fail-closed** | `_require_wiring` 的硬要求把"漏接线"转成了明确拒绝（未静默降级、未带缺件执行）——因此属**接线缺陷**而非安全缺陷 |

一句话：**治理层已就位，但运行期上下文未把它接进来** —— 这是 wiring 问题，不是 governance 逻辑问题。

---

## 2. Root Cause（根因）

### 2.1 `_require_wiring` 对 `ctx.governance` 的要求

`ToolExecutor.execute()` 在**关 1 之前**执行装配前置检查（fail-closed）：

```
pyharness/core/tools_executor.py:441     self._require_wiring(ctx)
```

`_require_wiring` 逐件要求 `session` / `scope` / `guard` / **`governance`** 非 `None`，缺任一即上抛 `CYC-999`：

```python
# pyharness/core/tools_executor.py:564-567
if getattr(ctx, "governance", None) is None:
    raise_code("CYC-999", module="tools_executor",
               hint="ctx.governance 未接线:关2 唯一治理入口缺失,"
                    "拒绝执行(S3-2-2)")
```

该检查的**位置**是关键：它在 `tool.call` 发射（`tools_executor.py:462`）**之前**，故失败时事件链上没有任何调用痕迹。

### 2.2 `_runtime_ctx` 未注入 governance

`_runtime_ctx`（`pyharness/core/orchestration.py:25`）为**独立运行**构造 ctx。缺陷期（`HEAD` 版）其返回体（`orchestration.py:49`）挂了 `session` / `scope` / `guard` / `approval` / `llm` / `tools` / `bus` / `registry` / `storage` / `goals` … **唯独没有 `governance`**：

```python
# 缺陷期 orchestration.py:61-62
        guard=getattr(spine, "guard", None),
        approval=getattr(spine, "approval", None),      # ← governance 缺失
```

对比：治理层的**其余装配点均已挂载**（`core/agent.py:472` 的 `create_agent`、`engine.py:666/939/976`），**只有第三个 ctx 构造点被漏掉**。

### 2.3 影响路径

`_runtime_ctx` 是**两条**生产路径的运行期 ctx 来源：

| 路径 | 入口 | 说明 |
|---|---|---|
| **Child agent execution** | `EngineSubagentRunner.run_child` → `_run_on_session`（`orchestration.py:154`） | **始终**经 `_runtime_ctx` ⇒ 每次工具调用必失败 |
| **Orchestration fallback** | `EngineIntentRunner.run` 在 `task_queue is None` 时的 `_run_on_session`（`orchestration.py:114`） | 轻装配/无队列场景必经 ⇒ 同样必失败 |

**对照（未受影响）**：主链 `engine.make_runner` → `_agent_ctx_of` → `create_agent`，其 ctx 已含 `governance` ⇒ 主链正常。

---

## 3. Implementation Change（实施修改）

### 3.1 修改文件

| 文件 | 量级 | 性质 |
|---|---|---|
| `pyharness/core/orchestration.py` | **+9 / −3** | **1 行功能** + docstring |
| `tests/unit/test_orchestration.py` | **+280 / −0** | 新增 **5** 个用例（含 1 对照组 + 1 守卫） |

### 3.2 修改符号

**功能改动（唯一 1 行）** —— `_runtime_ctx` 的返回体新增一个键：

```python
# pyharness/core/orchestration.py:67
        governance=getattr(spine, "governance", None),
```

**docstring 改动** —— 两处：
1. 既有句 "Guard/approval remain the parent engine instances" → **"Guard/approval/governance remain…"**（把治理纳入"沿用父引擎实例"的既有语义）；
2. 新增 4 行说明：`governance` 与 `session`/`scope`/`guard`/`approval` **同为必接线**，并记录"漏挂 ⇒ 每次工具调用 fail-closed 且日志无 `tool.call`，易误判"这一失效特征。

**新增用例**（`tests/unit/test_orchestration.py`）

| 用例 | 行 | 考点 |
|---|---|---|
| `test_runtime_ctx_propagates_governance_singleton` | 201 | **结构面**：治理**单实例**身份同一性透传（有/无治理两种 spine） |
| `test_child_agent_tool_execution_reaches_governance` | 229 | **子 Agent**：真实 runner 下工具**真正执行** |
| `test_orchestration_fallback_tool_execution_reaches_governance` | 274 | **编排兜底**：`task_queue=None` 分支 |
| `test_main_agent_tool_execution_reaches_governance` | 314 | **对照**：主链路径（防止三条路径被改成同一副错形态） |
| `test_runtime_ctx_still_fails_closed_without_governance` | 349 | **守卫**：修复不得削弱 fail-closed（spine 无治理 ⇒ 仍 `CYC-999`、工具零执行） |

### 3.3 修改前后行为差异

| 维度 | 修改前 | 修改后 |
|---|---|---|
| `_runtime_ctx` 的 `governance` 键 | **不存在** | `getattr(spine, "governance", None)` |
| 子 Agent 工具调用 | 无一例外 `CYC-999` | **正常执行**（`tool.call → guard.evaluated → decision.issued → receipt.emitted → tool.result`） |
| 无队列 IntentRunner | 同上 | 正常执行 |
| 主链路径 | 正常 | **逐字不变**（同一 `spine.governance` 实例） |
| fail-closed 语义 | `governance` 缺失 ⇒ `CYC-999` | **不变**（spine 无治理时仍 `CYC-999`，工具零执行） |
| 治理层结构 | — | **未改**：未新建模块、未改 `authorize()` 单入口、未新建链 |

---

## 4. Runtime Flow（运行流变化）

### Before —— 断在 runtime ctx

```
tool execution
  → runtime ctx        (orchestration._runtime_ctx)
  → governance missing (❌ ctx 无 governance 键)
  → ToolExecutor.execute
      → _require_wiring(ctx)        [tools_executor.py:441]
          → getattr(ctx,"governance",None) is None
          → raise CYC-999           [tools_executor.py:564-567]
  ⇒ tool.call 未发射 · tool.result 无 · Provider 零调用
```

实际事件序列（实测，子会话）：
```
session.created → user.message → llm.usage → system.error(CYC-999)
```

### After —— 治理闸可达

```
tool execution
  → runtime ctx            (orchestration._runtime_ctx,governance 已注入)
  → governance injected    (✅ 与父引擎同一单实例)
  → ToolExecutor.execute
      → _require_wiring(ctx)        四件齐备,通过
      → ctx.governance.authorize(call, ctx, inputs_digest=…)
                                      [tools_executor.py:476]
          → GuardChain.evaluate_detailed → DecisionEngine.decide
      → 关 3 Provider 执行 → 关 4 finalize
  ⇒ tool.call → guard.evaluated → decision.issued → receipt.emitted → tool.result
```

实际事件序列（实测，子会话）：
```
session.created → user.message → llm.usage
  → tool.call → guard.evaluated → decision.issued → receipt.emitted → tool.result
  → llm.usage → agent.message
```

**形态收敛**：修复后子会话的事件顺序与**主链既有顺序**一致（`tests/unit/test_tools_executor.py:289` 已钉死 `["tool.call","guard.evaluated","decision.issued",…]`）。

---

## 5. Verification Evidence（验证证据）

### 5.1 定向测试

```
tests/unit/test_orchestration.py   →   7 passed
```
（2 个既有 + **5 个新增**）

### 5.2 独立验证（`HEAD + RT-GOV-01`，worktree 隔离）

| 项 | 值 |
|---|---|
| **collected** | **1718** |
| **passed** | **1716** |
| **failed** | **0** |
| **errors** | **0** |
| **skipped** | **2** |

**算术自洽**：`HEAD` 独有 **1713** + RT-GOV-01 的 **5** 例 = **1718**。

### 5.3 Mutation（鉴别力证据）

在隔离树内**撤销 governance 注入**（删掉 `orchestration.py:67` 那一行）后复跑：

```
FAILED  test_runtime_ctx_propagates_governance_singleton
FAILED  test_child_agent_tool_execution_reaches_governance
FAILED  test_orchestration_fallback_tool_execution_reaches_governance
```

→ **3 tests RED**。

**结论：测试具备鉴别能力** —— 撤销修复即被捕获，而非"恒绿"。同时：
- 对照组 `test_main_agent_tool_execution_reaches_governance` **未 RED**（主链不受影响）；
- 守卫 `test_runtime_ctx_still_fails_closed_without_governance` **未 RED**（fail-closed 语义未被破坏）。

> 变异已还原；以主仓库文件覆盖隔离树副本后 `diff -q` 确认**逐字节一致**。

---

## 6. Independence Verification（独立性验证）

### 6.1 worktree 隔离

| 步骤 | 结果 |
|---|---|
| `git worktree add --detach tmp/gov_verify HEAD` | 基线 `ebbb444` |
| 仅拷入 RT-GOV-01 两文件 | `git status --porcelain` = **恰 2 项** |
| 执行定向 + 全量 | **7 passed / 1718-1716-0-0-2** |
| 清理 | worktree 已移除；`git worktree list` 仅剩主仓库；**主工作树与 HEAD 未受影响** |

### 6.2 仅两个文件

`pyharness/core/orchestration.py` · `tests/unit/test_orchestration.py` —— 与 F1(3 文件) / Stage 1(6 文件) **无任何文件交集**。

### 6.3 无跨批依赖

**静态证据** —— `test_orchestration.py` 对其它批次构造的引用计数：

| 关键字 | 所属批次 | 命中 |
|---|---|---|
| `_policy_announced` | Stage 1（F-01） | **0** |
| `policy.updated` | Stage 1（F-01） | **0** |
| `_merge` | Stage 1（F-28） | **0** |
| `SESSION_ID_IN_PATH` | Stage 1（F-19） | **0** |
| `desktop` | Stage 1（F-19） | **0** |
| `session=` / `approval` | — | 各 1，均为**构造 bare spine 替身**的字段，**非** F-27/Stage 1 构造 |

`orchestration.py` 对 Stage 1 构造的引用 = **0**。依赖面仅 `config` · `core.llm` · `core.subagent` · `engine.assemble_real_engine`（均为 HEAD 既有符号）。

**实证证据** —— 隔离树内 `engine.py` 为 **HEAD 版**（无 F-01）、`approval.py` 为 **HEAD 版**（无 F-28），全量 **0 failures / 0 errors** ⇒ **不依赖 Stage 1**。

**反向提示（非本批问题）**：主树（含全部批次）实测 **1741 / 1739** 通过 ⇒ "先提交 RT-GOV-01、后提交 Stage 1"顺序安全。

### 6.4 结论

> **RT-GOV-01 = 独立可提交（INDEPENDENCE VERIFIED）**

---

## 7. Commit Plan

### 7.1 暂存（**显式列文件，禁 `git add -A`**）

```
git add pyharness/core/orchestration.py
git add tests/unit/test_orchestration.py
```

| 文件 | +/− |
|---|---|
| `pyharness/core/orchestration.py` | **+9 / −3** |
| `tests/unit/test_orchestration.py` | **+280 / −0** |

### 7.2 Commit message

```
fix: inject governance into orchestration runtime ctx
```

- **不署 AI 名**（公开仓库约定；不加 `Co-Authored-By` / `Generated with` 等任何署名行）
- 建议正文（可选，供参考）：

```
Orchestration built its runtime ctx without the governance key, so every tool
call made by a child agent or by the no-queue IntentRunner fallback failed
closed with CYC-999 before tool.call was emitted. The governance context is now
carried alongside guard/approval, which restores the authorize() entry point
for those paths. The main path is unchanged.
```

### 7.3 ⚠️ 待确认：本报告是否随同提交

上文 `git add` 清单**仅含 2 个文件（代码 + 测试）**，**未含 `RT-GOV-01_REPORT.md`**。

但项目惯例是**报告随同入库**（同期：Stage 1 → `RT-FIX-STAGE1-REPORT.md`；F-27 → 计划/变更/提交三份报告均已提交）。故此处存在一处**需你明确**的分歧：

| 选项 | 说明 |
|---|---|
| **(a)** 报告随代码同批提交 → `git add` 增列 `RT-GOV-01_REPORT.md`（共 3 文件） | 与项目惯例一致 |
| **(b)** 代码先行提交，报告随后单独提交 | 与本报告 §7.1 的清单一致 |

**未执行任何提交动作，等待确认。**

---

## 附：本阶段一致性声明

- **未修改生产代码**（`orchestration.py` 的改动发生于此前实施阶段，本阶段只读取）
- **未修改测试**
- **未新增功能**
- 本阶段**仅创建本报告文件**；未 commit、未 push
- 验证用临时 worktree 与 junit 已清理；主工作树与 `HEAD` 未受影响
