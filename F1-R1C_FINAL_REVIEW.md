# F1-R1C Final Review — D1d-A `/exit` Lifecycle Fix

> 阶段：F1-R1C（D1d 根因取证 → 最小修复 → 最终审查）
> 性质：只读审查与收口记录。本阶段无生产/测试代码改动、无 commit、无 push。
> 上游：F1-R1A（D1，已冻结）· F1-R1B（D1b，已冻结）· F1-R1C-0（D1d 定性）· F1-R1C-1（D1d-A 修复）

---

## 1. Scope

### 1.1 修复范围（冻结）

| 项 | 值 |
|---|---|
| 唯一生产修改文件 | `pyharness/core/commands.py`（`cmd_exit`，功能性改动 **1 行**） |
| 唯一测试修改文件 | `tests/unit/test_cli.py`（新增 2 用例） |
| 本阶段新增测试 | `TestExitLifecycleRealWiring::test_exit_sets_shell_flag_without_ctx_agent`<br>`TestExitFinalFlushPersistence::test_exit_persists_all_emitted_events` |

### 1.2 明确未触碰（mtime 与 git 双重佐证）

| 文件 | mtime | 判定 |
|---|---|---|
| `pyharness/cli.py` | 14:22:47（F1-R1B 交付态） | 本阶段**未动** |
| `pyharness/engine.py` | 09-15 11:15:55 | 未动 |
| `pyharness/core/approval.py` | 09-15 21:04:57 | 未动 |
| `pyharness/persistence.py` | 09-15 13:38:32 | 未动 |
| `pyharness/core/agent.py` | 09-14 19:11:40 | 未动 |
| `pyharness/events/`、INV registry、Methodology、README、Protocol v0.3、`assemble_real_engine` | — | 未动 |

工作树状态（`git status --short`）：

```
 M pyharness/cli.py            ← F1-R1A/F1-R1B 结转（未提交）
 M pyharness/core/commands.py  ← F1-R1C-1
 M tests/unit/test_cli.py      ← F1-R1A/R1B/R1C-1 累计
?? docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md   ← 本阶段之前既存
?? docs/governance.html                          ← 本阶段之前既存
```

`git log -1` = `d2ccd45`（**未 commit、未 push**）。

---

## 2. Root Cause

`cmd_exit` 曾以 `ctx.agent is None` 充当**外壳退出闸门**：

```
生产 ctx.agent = None
  → commands.cmd_exit()
  → if ctx.agent is None: return          ← 提前返回
  → shell.request_exit(0) 从未执行
  → interactive_loop 永不返回
  → cli_main finally 永不到达
  → _flush_session 不执行
  → /exit 后尾部非 SYNC 事件无法持久化
```

根因链的四条静态事实：

| # | 事实 | 位置 |
|---|---|---|
| 1 | `ctx.agent` 在 CLI 装配中显式置 `None` | `cli.py:547`（`assemble_ctx`） |
| 2 | `attach_engine_to_ctx` 装配 spine/llm/scope/tools/guard/governance/budget/approval/task_runner/make_runner/task_queue——**独缺 agent** | `engine.py:971-987` |
| 3 | 全 `pyharness/` 搜 `.agent\s*=` → **0 命中**；`ctx.agent` 的赋值仅两处，且均为 `None`（`cli.py:547`、`engine.py:935`） | — |
| 4 | `create_agent` 唯一调用点在 `engine.py:831`（惰性、按会话），结果只进 `spine.active_agents`，**从不绑到 `sh.ctx.agent`** | — |

**静默性**：`commands._render`（`commands.py:293-297`）在 `ctx.render is None` 时直接返回，而 `assemble_ctx` 设 `render=None`（`cli.py:551`）→ 用户键入 `/exit` 后**看不到任何提示**，只见新提示符。

**为何测试未发现**：`tests/unit/test_cli.py:569-574` 的 `_mk_sh` **默认注入 `agent=FakeAgent(log_)`**——测试替身提供了生产从不提供的依赖（与 D1、D1b 同款失真模式）。

---

## 3. Fix Decision: A vs B

### 3.1 方案 B（在 CLI attach 阶段补接 `ctx.agent`）——**否决**

| 判据 | 证据 |
|---|---|
| 是否存在 canonical 生产对象 | **不存在**。真实 `Agent`（`agent.py:168`）方法面为 `enter / submit / close / attach_capability / announce / detach_capability / snapshot` |
| 所需接口是否存在 | **不存在**。`finish_session` / `new_session` 在全仓库**只定义于测试替身**（`tests/unit/test_cli.py:113`、`tests/unit/test_commands.py:48`） |
| 强行接线的后果 | `cmd_exit` 由「静默失效」变为 **`AttributeError`**（更坏） |

→ 无"明确、唯一、已有职责对应的生产对象"；为其伪造 Agent 或引入新抽象层，均属超出最小职责范围。

### 3.2 方案 A（职责解耦）——**采用**

`finish_session()` 是**尽力而为的会话终态留痕**，外壳退出归 `ctx.shell`，二者不得互为前置。判据：

- 规格对 `cmd_new` 同一守卫自述为「**理论不可达**，防御降级」（`specs/commands.py.md:181`）——作者本意是降级，不是闸门；
- `cmd_exit` 的第二个守卫（`shell is None or not callable(request_exit)`）才是真正的"无退出通道"判据，**保留不动**。

```diff
-    if ctx.agent is None:  # 无生命周期通道
-        return "当前外壳不支持 /exit"
-    await ctx.agent.finish_session(reason="user_command_exit")  # 终态留痕
+    if ctx.agent is not None:              # 尽力而为:会话终态留痕(缺失即跳过)
+        await ctx.agent.finish_session(reason="user_command_exit")
```

> 说明：`engine.py:971-987` 位于已判定的 `attach_engine_to_ctx` 活路径；`engine.py:935` 属 `assemble_real_engine`（F0 已判 DEAD），同样未接线 agent。故**任何生产路径下 `ctx.agent` 恒为 `None`**。

---

## 4. Production Diff Summary

- 文件数：**1**
- 功能性改动：**1 行**（布尔判据的后果：由"阻断退出"改为"跳过一步收尾"）
- 其余：`cmd_exit` docstring 补充职责边界说明
- 未新增抽象、未新增依赖、未改动调用序、未触碰 shell 退出通道判据、未改其他命令（`cmd_new` 原样保留）
- 退出链其余环节（`_shell_exit_code` → `interactive_loop` return → `cli_main` finally → `_flush_session`）**一行未动**

---

## 5. Regression Evidence

| 口径 | 命令 | 结果 |
|---|---|---|
| 目标文件单测 | `pytest tests/unit/test_cli.py` | **92 passed**（90 → 92） |
| 全量回归 | `pytest --ignore=tests/unit/test_desktop_native.py --ignore=tests/unit/test_shell_parity.py` | **collected 1712 / passed 1712 / failed 0 / errors 0 / skipped 2** |
| 基线对照 | F1-R1B 基线 1710 | +2（新增用例），零回归 |
| F1-R1B 未被破坏 | `-k "PromptApproval or ApprovalLiveChain"` | **9 passed**（requested 正常提示 / granted·denied·timeout 不再提示 / `Envelope.seq` canonical identity 保持） |

---

## 6. Mutation Proof

手法：运行期取生产源码 → 精确替换目标片段 → 在 `commands` 命名空间重定义，并**替换 `COMMANDS["exit"]` 注册表条目的 handler**（`cmd_exit` 经 `COMMANDS.get()` 分发，仅替换模块属性无效）。**不修改仓库文件**；锚点未命中即抛错。

| 变异 | 内容 | 结果 |
|---|---|---|
| `none`（修复态） | — | **92 passed** |
| **M1** | 恢复旧闸门 `if ctx.agent is None: return "当前外壳不支持 /exit"` | **2 failed**（两个 D1d 新测试），失败信息即原始症状：<br>`AssertionError: loop 未由 /exit 退出旗标终止(读了第二次)` |

→ 新测试对原始生产 bug 具备**直接鉴别力**（非补绿灯）。

---

## 7. Real Product Evidence

**入口**：真实 `pyharness.exe chat`（真 tty 控制台，非 winpty 专属路径）。

```
t+28.1  输入最小无工具任务 → [任务完成] t-1 ok
t+70.0  输入 /exit
t+75.2  进程退出（pty reader EOF）
t+95.0  alive = False          ← ✅
```

**对照**（F1-R1C-0，同场景、修复前）：`/exit` 后进程**永久存活**（30s 观测窗后仍 `alive=True`，主线程空转于事件循环选择器）。

### 7.1 生命周期 marker 直接对照（同一 wrapper 观测手段）

| 阶段 | 修复前 | 修复后 |
|---|---|---|
| `handle_slash ENTER` | `raw='/exit' ctx.agent=None ctx.render=None` | 同 |
| `commands._render` | `text='当前外壳不支持 /exit'` | `text='再见'`（handler 走到末尾） |
| `handle_slash EXIT` | `shell.exit_code=`**`None`** | `shell.exit_code=`**`0`** |
| `_flush_session` | **无 marker** | **`ENTER` → `flush(up_to_seq=None)` → `EXIT ok`** |
| `cli_main` | 永不返回（`WATCHDOG fired: 进程仍存活`） | **`EXIT rc=0`** |
| 线程收尾 | `MainThread + asyncio_0`（执行器滞留） | **`['MainThread']`**（干净退出） |

> 该次实测同时补上了此前仅由落盘结果间接推断的一环：**`/exit` 路径下 `cli_main` 的 finally 确实执行了 `_flush_session`**（INV-D1d-02 直接观测）。

---

## 8. Persistence Evidence

`/exit` 后重新读取 JSONL 真源（同一最小无工具场景）：

| | 修复前 | 修复后 |
|---|---|---|
| PERSISTED | 6（seq 1–6） | **17（seq 1–17）** |
| `last persisted seq` | 6 | **17** |
| `missing seq` | **7–17（11 条）** | **无** |
| `agent.message` | ✗ | ✓ seq 10 |
| `session.renamed` | ✗ | ✓ seq 14 |
| `task.completed` | ✗ | ✓ seq 15 |
| `segment.end` | ✗ | ✓ seq 16 |
| `user.command`（`/exit` 自身留痕） | ✗ | ✓ seq 17 |

**INV-D1d-03 达成**：`last emitted seq == last persisted seq == 17`；`missing == []`。

补充口径（不改变结论）：修复前的落盘边界恰为**最后一个强同步锚点**（seq 6 = `segment.start`），其后非 SYNC 事件留在攒批；而 `flush_interval_s` 无消费者（见 §12 D1d-C），故积压未达 `flush_batch=64` 时永不落盘。

---

## 9. Replay Evidence

`pyharness session show <sid>`（真实入口，走 `persistence.replay`）：

```
修复前 (6 条):
  [user] Reply with exactly PYH-R1C-D1D-9F2B
  ← 助手回复整段消失:replay 出的历史 ≠ 实际发生的历史

修复后 (17 条):
  [user] Reply with exactly PYH-R1C-D1D-FIX
  [assistant] PYH-R1C-D1D-FIX
  [assistant] PYH-R1C-D1D-FIX 固定回复
```

这是 INV-01「日志为唯一真源 / 历史必由日志派生」被破坏、并被修复的**最直观证据**。
`task.completed` / `segment.end` / `user.command` 已确认存在于修复后 JSONL（§8）；`session show` 的卡片渲染面本就不为这些类型出卡片，其存在性以 JSONL 为准。

---

## 10. `ctx.agent` / `cmd_new` 独立 Finding

### 10.1 静态审查结论

| 检查项 | 结论 |
|---|---|
| `ctx.agent` 是否在 production path 中**始终**为 None | **是**。赋值仅两处且均为 `None`（`cli.py:547`、`engine.py:935`）；无任何壳装配 Agent |
| `cmd_new` 是否仍依赖 `ctx.agent` | **是**。`commands.py:209-214` 保持 `if ctx.agent is None: return "当前外壳不支持 /new"` |
| 是否存在与 D1d-A 类似的 silent-degradation 风险 | **存在**。`/new` 走 `handle_slash` → 提前返回 → `_render(None)` 静默丢弃 → 用户无任何反馈，会话也未切换 |
| 是否属于 D1d-A 的一部分 | **否**。D1d-A 收敛于「`/exit` → 生命周期终止 → 最终 flush」；`/new` 不涉及外壳退出，无持久化后果，是**独立缺陷面** |

### 10.2 影响面

- 受影响面：**斜杠命令面 `/new`**（`commands.handle_slash` 分发路径）
- 不受影响：桌面/原生壳的"新建会话"走各自入口（`application/service.py:287` `create_session`、`desktop_native/controller.py:124`），不经 `cmd_new`
- 后果等级：静默 no-op（无数据损坏、无生命周期异常），但**用户以为已开新会话**

### 10.3 记录

```
NEW-CMD-001 (deferred): 斜杠 /new 在全部壳中静默失效
  位置: pyharness/core/commands.py:209-214
  根因: 与 D1d-A 同源(ctx.agent 恒 None),但后果不同(无退出/无 flush 影响)
  建议: 独立立项;修复方式需与 D1d-A 的决定保持一致口径
  本阶段: 不修复,不引入 Agent abstraction,不改 engine wiring
```

---

## 11. SPEC-DRIFT-001

```
SPEC-DRIFT-001: commands specification does not match current production implementation
```

| 项 | 内容 |
|---|---|
| **drift 具体位置** | `docs/specs/commands.py.md:203-209`（`cmd_exit` 伪码块）；关键行为行在 **:205-207** |
| **spec 原描述** | `if ctx.agent is None: return "当前外壳不支持 /exit"` → `await ctx.agent.finish_session(...)` → `ctx.shell.request_exit(0)` |
| **当前代码行为** | `if ctx.agent is not None: await ctx.agent.finish_session(...)`（best-effort）→ 随后无条件走 `request_exit(0)`；另有独立的 `shell` 通道守卫 |
| **差异性质** | 行为性差异（非注释/格式）：spec 把 `ctx.agent` 缺失编码为**退出闸门**，而该编码正是 D1d 缺陷的源头 |
| **本阶段不自动同步的理由** | ① `docs/specs/*` 不在本阶段授权修改范围（仅 `commands.py` + `test_cli.py`）；② spec 是**契约面**，其修订应与 INV/canonical 面一并评审，不宜在修复阶段顺带改写；③ 同步需同时决定 `cmd_new` 段（:181 附近）的口径（见 NEW-CMD-001） |
| **是否建议进入下一次人工 review** | **建议**。宜与 NEW-CMD-001 合并为一次「commands 契约面修订」，一并决定 `ctx.agent` 在 CLI 的长期命运（保持 None 并正式降级，或引入规范的会话生命周期对象） |

---

## 12. Deferred Issues

以下项**全部保持 DEFERRED**，本阶段未因其中任何一项重新打开 D1d-A：

| ID | 内容 | 位置 / 证据 | 状态 |
|---|---|---|---|
| **D1d-B** | Failure Visibility：`ctx.render is None` → `commands._render` 静默跳过 → `/exit` 一类失败对用户不可见 | `cli.py:551`；`commands.py:293-297` | DEFERRED |
| **D1d-C** | Dead Configuration：`log.jsonl.flush_interval_s=0.5` 无任何消费者；`_flush_session` docstring 所称"引擎定时 flush 任务"不存在；丢失窗口达 ≤63 条（`flush_batch=64`） | `config.py:698`、`persistence.py:321`、`persistence.py:368-369` | DEFERRED |
| **D1c** | 重复 `ApprovalProvider` 订阅 → 合法裁决被记 `system.error(APR-503)` 幽灵事件（审计污染） | `cli.py:569` + `engine.py:537` | DEFERRED |
| **NEW-CMD-001** | 斜杠 `/new` 静默失效（本次审查新增记录） | `commands.py:209-214` | DEFERRED（新） |
| **SPEC-DRIFT-001** | commands 规格与实现不一致（本次审查新增记录） | `docs/specs/commands.py.md:205-207` | DEFERRED（新） |
| X1–X7 | 死分支残迹 / 分发重排 / CLI 用户身份模型 / cross-session seq collision / push-pull identity 分歧 / `__all__` 文档同步 等 | F1-R1B §13 | DEFERRED |
| D2 / D3 / D4 / D5 | TLB-802 控制流被记 ERROR / `budget`·`stats` BROKEN / 工具数注释漂移 / 审批通道硬编码 | F0/F1 | DEFERRED |
| 其他 F1 未关闭项 | 全域 E2E=0、`assemble_real_engine` DEAD、其余 dead feature | F0/F1 | DEFERRED |

> **边界声明**：D1d-A 解决的是 `/exit → lifecycle termination → final flush`，**不是**整个 persistence architecture。D1d-C 的存在不构成重开 D1d-A 的理由。

---

## 13. Final Closure Statement

### 13.1 证据充分性核对（逐条）

| # | 待证事项 | 结论 | 证据来源 |
|---|---|---|---|
| 1 | `ctx.agent` 在真实 CLI production wiring 中为 `None` | ✅ | §2 表 1/3；R1C-1 测试断言；R1C-0 Exp A' 进程内实测 |
| 2 | 真实 `Agent` 不存在 `finish_session()` / `new_session()` | ✅ | `agent.py:168` 方法面；全库唯二定义在测试替身 |
| 3 | `create_agent()` 不负责向 `ctx.agent` 注入 canonical 对象 | ✅ | 唯一调用点 `engine.py:831` |
| 4 | 因此方案 B 不成立 | ✅ | §3.1 |
| 5 | 方案 A：`ctx.agent` 存在则 best-effort finish，否则继续 shell exit | ✅ | §3.2 diff |
| 6 | `/exit` 在真实 `pyharness.exe chat` 中可使进程退出 | ✅ | §7 |
| 7 | `cli_main` finally 能执行 `_flush_session` | ✅ | §7.1 marker 直接观测 |
| 8 | emitted seq == persisted seq | ✅ | §8（17 == 17） |
| 9 | 修复前后 JSONL 形成明确对照 | ✅ | §8（6/17 缺失 11 条 → 17/17 零缺失） |
| 10 | replay 能恢复修复后的 assistant / task.completed / segment.end / user.command 等历史 | ✅ | §9（replay 差异）+ §8（类型逐条核对） |
| 11 | Mutation proof 在恢复旧 guard 后稳定失败 | ✅ | §6（M1 → 2 failed） |
| 12 | full regression = 1712 passed / 0 failed / 0 errors / 2 skipped | ✅ | §5 |

### 13.2 正式标记

```
F1-R1C D1d-A = CLOSED
D1d-B = DEFERRED
D1d-C = DEFERRED
D1c   = DEFERRED
```

**新增未决项**（本次审查只记录，未修复、未扩大 F1-R1C）：

```
NEW-CMD-001    = DEFERRED
SPEC-DRIFT-001 = DEFERRED
```

**阶段约束遵守**：本 Final Review 未修改任何生产代码、测试代码、规格、README、Methodology、INV registry、Protocol v0.3；未 commit、未 push；未继续寻找新问题。
