# S6-2a-P0_CHANGE_REPORT.md — S6-2a-P0 / P0-R 阶段变更报告与冻结

> **阶段**：S6-2a-P0（INV-02 Scope Adjudication）+ S6-2a-P0-R（Remediation Design）
> **日期**：2026-09-15 ｜ **基线 commit**：`4f05c95`（S6-1 Freeze）｜ **HEAD** `4f05c95`
> **性质**：**文档 + 只读勘察**。本阶段**未修改任何 Python 生产代码**、**未修改任何现有测试**、**未修改 Registry / Event Schema / Governance Core**。
> **产出**：`docs/KEY-FINDINGS.md`（附录 A，**+40/−0 纯追加**）· `S6-2_COVERAGE_MATRIX.md`（**969 行**，含 §1~§13）· 本报告。
> **baseline**：**1671 passed / 2 skipped / 0 failed**（collected 1673，exit 0，47.0s）· `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`

---

## 0. 本阶段做了什么 / 没做什么

| 项 | 状态 |
|---|---|
| 建立 INV-02 调用链（`auto_title.py:36` ↔ `engine.py:805-806`） | ✅ 完成（Matrix §10.1） |
| 核验 `LLMClient` 实际保护边界（看实现） | ✅ 完成（§10.2） |
| 规格与架构意图取证（PRD / CONSTRAINTS-06 / DIS-CORE / specs / DIS-SEAM / ADR） | ✅ 完成（§10.3） |
| 证据链（Evidence → … → Conclusion） | ✅ 完成（§10.4） |
| A / B / C 判据与推荐裁决 | ✅ 完成（§10.5 / §10.7） |
| 控制绕过独立分析（KF-B） | ✅ 完成（§10.6 / §12） |
| `mini()` 修复设计（14 项 + "为何非改名"） | ✅ 完成（§11.3） |
| F042 完整要求追踪（12 项） | ✅ 完成（§11.4） |
| 三方案比较 + 最小修复推荐 | ✅ 完成（§11.7 / §11.8） |
| 修复后测试 DoD | ✅ 完成（§11.9） |
| KF-A / KF-B 正式登记 | ✅ 完成（`KEY-FINDINGS.md` 附录 A） |
| **修改生产代码** | ❌ **未做（本阶段禁止）** |
| **创建 INV-02 测试 / acceptance / security 测试** | ❌ **未做** |
| **修复 KF-B / 实现三闸** | ❌ **未做** |
| **backlog：F042 其他行为偏离** | ❌ **未做（已登记，见 §5）** |

---

## 1. KF-A 发现（P0 · INV-02 真实违约）

**事实**：`pyharness/core/auto_title.py:36` 调用 `ctx.llm.chat([...], tools=None, ctx=ctx)`，经 `pyharness/engine.py:805-806` 在**生产路径**接线（`res.reason=="complete"` 且 `spine._auto_titled` 为假时，于 `loop.wake()` **返回之后**触发）。

**根因链（人工裁定接受）**：

```
F042 规格指定 ctx.llm.mini()            （docs/PRD-Core.md:1251）
        ↓
LLMClient.mini() 在实现中缺失           （grep -rn "def mini" pyharness/ → 零命中）
        ↓
实现退化到已有出口 ctx.llm.chat()
        ↓
auto_title 使用了 Agent Loop 专用 LLM 出口
        ↓
违反 INV-02（"全库 llm.chat 唯一合法调用方 = agent-loop"）
```

**证据索引**

| 类型 | 位置 |
|---|---|
| 规格（4 处一致） | `PRD-Core.md:838,634` · `CONSTRAINTS-06-Testing.md:63` · `DIS-CORE.md:186` · `specs/agent_loop.py.md:262` |
| 规格（出口指定） | `PRD-Core.md:1251`（`ctx.llm.mini`） |
| 实现（违约点） | `auto_title.py:36` |
| 实现（时机） | `engine.py:800`（`loop.wake`）→ `:802-806`（循环**之后**） |
| 实现（三闸位置） | `agent_loop.py:196-199`（`_must_stop` / `scope.check_budget`）· `:204`（`run.cancelled`） |
| 实现（保护边界） | `llm.py:647-684`（只计量不阻断）· `:957`（四出口汇聚点） |
| 覆盖缺口 | `grep -rln auto_title tests/` → **空**；`docs/specs/auto_title.py.md` **不存在** |

**影响**（如实分级，不夸大）：
- ⚠️ **不变量**：`INV-02` 字面与意图均被违反。
- ⚠️ **控制**：该调用**不经过**轮数 / 取消 / 预算前置闸（预算超支仅**下一轮**可见）。
- ✅ **未受损**：审计留痕齐全（`llm.request` / `llm.usage` / `llm.response` / `session.renamed`）· 单端点路径 · 超时闸（F017）· 降级链 · 无工具副作用（`tools=None`）。
- **定性**：**P0 不变量违约**，**非安全漏洞**。

---

## 2. KF-B 发现（P1 · 独立于 INV-02 的控制面缺口）

**事实**：
1. `LLMClient` 的**全部**出口（`chat` / `chat_stream` / `summarize` / `json_chat`）**均不执行** budget / cancellation / guard / governance 判定 —— `llm.py` 全链只有"请求 → 超时 → 归一 → 计量 → 落盘"，**无任何准入判定**。
2. 三闸**只**位于 `agent_loop.py:197-199`。⇒ 任何**非循环** LLM 调用路径天然不受闸：
   - `plan_mode` → `json_chat`（`plan_mode.py:341-352`）
   - `compaction` → `summarize`（`compaction.py:510-516`）
3. **`summarize(prompt, *, budget: int = 400, ctx)` 的 `budget` 参数只出现在签名，函数体内从未被使用**（`llm.py:974-982`），而 spec 侧声称其存在（`compaction.py:266,299`）。

**为何独立于 INV-02**：`json_chat` / `summarize` **不使用 `llm.chat`**（经 `_chat_any("chat", …)` 直接下发）⇒ **不违反 INV-02**；且 KF-A 修复后 `auto_title` **仍不过闸**。⇒ "**不违反 INV-02**" ≠ "**无工程风险**"。**不为其新建 INV 编号。**

**定性**：**P1 资源/控制面风险**。无审计缺失、无安全绕过面。

---

## 3. INV-02 裁决（人工已接受）

| 项 | 裁定 |
|---|---|
| **是否违反** | ✅ **是 —— Option A：违反成立** |
| **违规点（精确定义）** | **① 出口选择**（误用 Agent Loop 专用的 `ctx.llm.chat()`，而 PRD F042 指定 `ctx.llm.mini(...)`）**+ ② 无闸**（**已划归 KF-B 独立处理**） |
| **违规点（明确不是）** | ❌ **不是**"`auto_title` 作为系统工具直调 LLM"这一功能形态 —— 该形态**有 PRD 明文依据**（`PRD:1248-1252`） |
| **B / C 为何不成立** | **B**：要判 B 须或改实现、或扩 `INV-02` 表述；后者正是明令禁止的"临时扩大例外范围"，且实测**不存在可援引的"该出口享有的控制"**（所有出口都无闸）。**C**：关键事实齐备（调用链/保护边界/意图/行为均有实现与文档证据），**不存在需要更多证据的情形** |
| **是否修改 INV-02 定义** | ❌ **否** —— `INVARIANT_REGISTRY.md` **未修改**（`git diff` 0 行） |
| **是否扩大 INV-02 例外范围** | ❌ **否** —— 见 §4.3 |

---

## 4. Remediation Design（摘要；详见 `S6-2_COVERAGE_MATRIX.md` §11）

### 4.1 `mini()` 设计要点（14 项裁定）

| 项 | 裁定 |
|---|---|
| 职责 | **系统工具级单次文本调用**：单 prompt 进 → 纯文本出；不组装会话上下文、不带工具、不参与对话轮 |
| 契约 | `async def mini(self, prompt: str, *, ctx: Any) -> str`；空内容返回 `""`（不抛），由调用方决定降级 |
| 类别 | ✅ 属 **System Tool LLM Endpoint**（PRD 指定；架构已有 `summarize`/`json_chat` 两个同类成员） |
| timeout | **复用** `LLMAdapter.chat` 的 F017 总时长闸（`llm.py:663`）——**不新增第二套超时真源** |
| usage accounting | ✅ **自动获得，零新增代码**（`_chat_any` → adapter → `report_usage`，`llm.py:558,561`） |
| event logging | ✅ **自动获得，事件类型零新增**（`llm.request`/`llm.usage`/`llm.response`）⇒ **`EVENT_TYPES` 仍 77** |
| error handling | 沿用 `_chat_any` 的错误归一（`llm.py:665`）；`mini` 自身**不吞错**，由 `auto_title` 按 F042 降级 |
| fallback | ✅ **自动获得**（`llm.py:957-961` 的降级链） |
| **budget** | ❌ **不设参数** —— `summarize.budget` 的前车之鉴（有参数无实现 ⇒ 制造"有上界"错觉，比没有更糟） |
| **cancellation** | ❌ **本轮不增设**（取消语义属循环；`auto_title` 在 run 已 complete 后执行）⇒ KF-B 范围 |
| **governance** | ❌ **不需要**（无工具调用 ⇒ 无执行授权面；且 `ADR-018:308` 禁止 `governance/` 与 `llm` 互相依赖） |
| **audit** | ✅ **已有，无需新增**（同 event logging） |

### 4.2 F042 完整要求追踪

✅ **符合 5 项**：`session.renamed(by="auto")` · 只做一次（日志派生，更合 INV-01）· 异步 · 不阻塞 · **标题不入 LLM 上下文**（已实测 reducer 为 **if/elif 白名单**，`session.renamed` 不在映射内，`session.py:184-215`）。
❌ **偏离 5 项 + 缺失 2 项**：见 §5。

### 4.3 ⭐ 为什么 `mini` 不是"换个函数名"

1. **契约面不同**：`chat(messages, tools, *, ctx) -> LLMResponse[含 tool_calls]` = **对话轮**签名；`mini(prompt, *, ctx) -> str` = 单串入出、无 tools、无轮语义。`auto_title` 的真实用法本就不是对话轮，是在**误用**对话出口 ⇒ 换的是**出口类别**。
2. **"例外"→"类别"**：改 `INV-02` 表述得**枚举式例外**（不可机械校验；每加一个系统工具就要再扩一次）；新增 `mini` 得**类别式边界**（可静态校验"除 `agent_loop` 外无模块调用公开 `.chat(`/`.chat_stream(`"；**新增系统工具无需改 INV-02**）⇒ 这正是**不构成"临时扩大例外范围"**的理由。
3. **⚠️ 诚实限制**：`mini` **只修 ① 出口选择**，**不修 ② 无闸** —— ② 已被裁定**明确划归 KF-B**，故与本修复范围一致。修复后 **`INV-02` GREEN，但 KF-B 仍 `OPEN`**。**不以 INV-02 之名夹带 KF-B。**

---

## 5. ⚠️ 本次**未处理**的 F042 其他偏离（**已登记，暂不实施**）

> 人工裁定：以下内容归入 **F042 Behavior Compliance（F042 行为符合性）后续问题**，**不并入本次 KF-A 修复**。**不得因为新增 `mini()` 顺便修改这些行为。**

| # | 项目 | PRD | 当前实现 | 状态 |
|---|---|---|---|---|
| 1 | 输入截断 | `first[:200]`（`PRD:1251`） | `first[:500]`（`auto_title.py:22`） | **未处理** |
| 2 | 标题长度上限 | ≤ **24 字**（`PRD:1242,1252`；模块 docstring `:3` 亦写 ≤24） | `TITLE_MAX = 64`（`:16`）⇒ `[:64]`（`:42`） | **未处理** |
| 3 | 失败降级 | "前 20 字符"规则（`PRD:1244,1251`） | `return None`（`:38-40`） | **未处理** |
| 4 | 空结果降级 | "前 20 字符"规则（`PRD:1251`） | `return None`（`:43-44`） | **未处理** |
| 5 | 验收测试文件 | `test_f042_title.py`（`PRD:1878`） | **不存在** | **未处理** |
| 6 | 模块 spec | `docs/specs/auto_title.py.md` | **不存在** | **未处理** |

**注**：入口截断（#1）与标题长度（#2）的修改会**改变现有行为**，失败/空降级（#3/#4）会**新增降级路径** ⇒ 均**不属"最小修复"**，须**单独裁定**。

---

## 6. 当前 baseline（冻结前实测）

| 项 | 值 |
|---|---|
| 全量回归 | **1673 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1671 passed / 2 skipped / 0 failed**（exit 0，47.0s） |
| 与基线对比 | **相同**（未低于） |
| `EVENT_TYPES` / `SYNC_TYPES` / `TRANSIENT_TYPES` | **77 / 14 / 3**（不变） |
| Registry | `docs/INVARIANT_REGISTRY.md` **未修改**（`git diff` 0 行） |
| Governance Core / Event Schema | **未修改**（`git diff` 0 行） |
| Python 生产代码 | **未修改**（`git diff --name-only HEAD -- pyharness` = 0 行） |
| 现有测试 | **未修改**（`git diff --name-only HEAD -- tests` = 0 行） |
| `git diff --check` | ✅ 通过（无空白错误） |
| Registry 唯一性 / Legacy 完整性 | ✅ 仍有效（S6-1 冻结态，本阶段未触碰） |

---

## 7. 本阶段 checkpoint 文件清单

| 文件 | 变更 | 说明 |
|---|---|---|
| `docs/KEY-FINDINGS.md` | **M · +40 / −0** | 附录 A：KF-A / KF-B 正式登记（**纯追加，既有 Finding 零改动**，已核验删除行数 = 0） |
| `S6-2_COVERAGE_MATRIX.md` | **A · 新增 · 969 行** | §1~§9 覆盖矩阵 · §10 P0 裁决 · §11 修复设计 · §12 KF-B 建模 · §13 S6-2b 可开始性 |
| `S6-2a-P0_CHANGE_REPORT.md` | **A · 新增 · 本文件** | 本阶段变更报告与冻结 |

**不含任何 Python 生产代码**（`git diff --cached --name-only` 将逐项核验）。

---

## 8. 下一步（**需另行授权，本阶段不启动**）

1. **Phase 1**：`S6-2a-P0-F_PLAN.md` —— 修复实施计划（本阶段已完成，见同名文件）。
2. **Phase 2**：P0-F 实施（**等人工授权**）—— 范围严格限于：**新增 `LLMClient.mini()`** · **`auto_title` 改用 `mini()`** · **为证明修复而新增的必要测试** · 必要的最小文档说明 · **验证 INV-02 恢复**。
3. 修复落地后：S6-2b 可落 `test_inv02_llm_chat_sole_caller`（届时应 GREEN）。

**未授权前**：**不得**修改 `pyharness/core/llm.py` / `pyharness/core/auto_title.py`。

---

**本报告为 S6-2a-P0 / P0-R 的冻结记录。未修改生产代码 / 现有测试 / Registry / Event Schema / Governance Core；未 push。**
