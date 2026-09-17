# ADR-022：拒绝反馈契约（Rejection Feedback Contract）——拒绝类事件在 LLM 历史中的投影形态

> **编号核验（2026-09-17 实测）**：`docs/ADD.md` 索引实际登记 **21 条**（ADR-001~021）；全库扫描 `ADR-022` **无定义占用**。故取号 **022**。
>
> **Status**：**Accepted（已接受）** —— 2026-09-17 人工裁定「采纳方案 A+ / D-7 选 (a) / 同意补齐 DIS-CORE §4.2 的 `tool.error` 漂移」。
> **日期**：2026-09-17 ｜ **落点**：F-34 ｜ **来源**：`Functional Verification Report v1`（FV-004 guard 拒绝 · FV-006 审批拒绝）
> **登记**：`docs/ADD.md` **待回填**（本阶段未改该文件——不在本次授权清单内）。

---

## Status

**Accepted**。触发来源为 FV-004 / FV-006 **实测**（真实引擎 + 真实会话）。

| 项 | 值 |
|---|---|
| 前置 | 无（缺陷自始存在；F-27/RT-GOV-01 使子 Agent 与兜底路径**首次可达**，扩大了暴露面） |
| 实施 | `pyharness/core/session.py`（派生层）+ `pyharness/core/tools_executor.py`（1 处分支） |
| 不改动 | INV-04 / INV-05 的 canonical 文本；`docs/INVARIANT_REGISTRY.md`；事件词表；`guard.rejected` / `approval.denied` 的产生逻辑 |

---

## Context

### 现象（实测）

被拒绝的 tool call 在**派生给 LLM 的历史**中失去配对：

| 日志层 | 内容 |
|---|---|
| **事件层（正确）** | `tool.call` → `guard.evaluated(deny)` → `guard.rejected(sync)` → `decision.issued(reject)`<br>或 `tool.call` → `guard.evaluated(need_approval)` → `decision.issued(approval)` → `approval.requested` → `queue.suspended` → `approval.denied` → `queue.resumed` |
| **投影层（缺陷）** | `assistant.tool_calls = [{id}]` 存在，**配对 `tool` 消息不存在** ⇒ **悬空** |

实测（`AuditSystem` 之外的直接观察）：
```
assistant tool_calls ids = {'fv1'}
配对 tool 消息 ids       = 无
**悬空 tool_call**       = {'fv1'}
```

### 机制

reducer 只把 `tool.result`（`session.py:215-220`）与 `tool.error`（`:221-229`）映射为 tool 消息；**`guard.*` / `approval.*` 被显式排除**（`:230-231`）。而执行器在关 2 reject（`tools_executor.py:477-479`）与关 2.5 审批非放行（`:481-485`）时**直接返回 `ExecResult(ok=False)` 且不写任何 tool 事件**。

### 成文规则（⚠️ 权威层级）

该"排除"是**成文规则**，且见于**两份**文档：

| 文档 | 位置 | 原文 |
|---|---|---|
| **`docs/PRD-Core.md`**（**唯一权威规格**） | **`:304`** | `# guard.rejected 只留审计流,不进 LLM 上下文(§6);compacted 事件 → 插入摘要文本` |
| `docs/DIS-CORE.md`（细化层，从属于 PRD） | `:400` | `# guard.rejected 只留审计流不进 LLM 上下文;llm.chunk 不入日志(F027)` |

### 代码自述的后果

同一文件两处注释指出该形态会**端点 400**：
- `session.py:199-200`："tool 消息前须有含同 id 的 assistant，否则端点 400——**真链实测**"
- `session.py:222-223`："失败也须配对 tool 消息（assistant.tool_calls 后悬空 → 端点 400，**实测 LLM-304 刷屏**）"

⇒ 代码**已经**为 `tool.error` 解决了同一问题，**唯独拒绝路径未被覆盖**。

### 旁证：§4.2 伪码本身已落后于实现

`DIS-CORE.md` §4.2 的 reducer 伪码**没有 `tool.error` 分支**（实现后来补的）。即该映射面**在实现期已被扩展过一次**，拒绝路径是漏网的那一次。

---

## Problem

| # | 问题 |
|---|---|
| **P-1** | **协议非法**：悬空 `assistant.tool_calls` ⇒ OpenAI/DeepSeek 端点 400（会话中断，非静默降级） |
| **P-2** | **反馈缺失**：模型收不到"该调用被拒"的信息，无法改口重试 |
| **P-3** | 若采纳"新增事件类型"路线，会**制造第二真源**（违反 INV-01） |

---

## Constraints

| # | 约束 | 来源 |
|---|---|---|
| **C-1** | INV-01 单一真源（每会话一份日志；禁止同事实两条权威记录） | `docs/INVARIANT_REGISTRY.md` |
| **C-2** | INV-04 / INV-05 证据面**逐字不变**（`guard.evaluated`/`guard.rejected` 的发射与强同步不动） | 同上 |
| **C-3** | ADR-018 依赖方向（`governance/` 只依赖 `events`/`errors`） | `ARCHITECTURE_DECISION_RECORD.md:308` |
| **C-4** | ADR-021 归属模型（拒绝事件落**发起调用**的会话） | `docs/decisions/ADR-021-*.md` |
| **C-5** | 事件词表 = append-only 唯一真源；不得轻率新增类型 | ADR-020 先例（`guard.disabled` **不新增**、并入 `op=disable`） |
| **C-6** | 脱敏纪律：拒绝反馈不得含参数原文/凭据 | `docs/SECURITY.md §6.4` |
| **C-7** | `authorize()` 仍是关 2 唯一授权入口 | S3-2-2 |

---

## Options

| | 方案 | 载体 |
|---|---|---|
| **A** | **复用既有事件 → 派生配对 tool 消息** | `guard.rejected` / `approval.denied` / `approval.timeout` |
| **B** | 新增拒绝事件（如 `tool.rejected`）→ 派生 tool 消息 | 新事件类型（词表 77→78） |
| **C** | 仅剥离悬空 `assistant.tool_calls`（不回喂） | 不改映射 |

**共同硬约束**：要修复协议，投影**只能**二选一 —— ① 出现与 `tool_call_id` 配对的 **`tool` 角色消息**；或 ② 该轮 `assistant.tool_calls` **整体不出现**。用 `system`/`user` 角色插说明**不能**消除悬空。

**B 被否**：拒绝事实已有 `guard.rejected`（INV-05 要求强同步）与 `approval.denied`；再加一条 ⇒ **同一次拒绝两条并列记录 = 第二真源**（违反 C-1）。且触发词表冻结面（`EVENT_TYPES` 77→78、`tests/unit/test_events.py` 三处硬断言 + 文本扫描守卫 `_ALLOWED_COUNTS={"77"}`）。**与 ADR-020 拒绝 `guard.disabled` 完全同型。**

**C 仅作兜底**：模型会反复重试同一禁用调用（靠 F026 连败 / `stall` 封顶兜底 = 预算空转）。

---

## Decision

**采纳方案 A+**（复用既有事件、在**派生层**合成配对 tool 消息；不新增事件类型）。

| # | 决策 |
|---|---|
| **D-1** | 拒绝类事实**进入 LLM 上下文**，但**不以其原形**：仅经派生层合成配对 `tool` 消息。进入面 = `{guard.rejected, approval.denied, approval.timeout}` |
| **D-2** | 载体形态 = **`role="tool"`**（唯一能消除悬空的角色） |
| **D-3** | **不新增事件类型** —— `EVENT_TYPES / SYNC_TYPES / TRANSIENT` 恒 **77 / 14 / 3** |
| **D-4** | **修订/取代**：本 ADR 声明取代 `PRD-Core.md:304` 与 `DIS-CORE.md:400` 的**字面表述**（"不进 LLM 上下文" → "**不以原形**进入，但派生为配对 tool 消息"）。`DIS-CORE.md §4.2` 的伪码**已同步更新**（含既有 `tool.error` 漂移的对齐） |
| **D-5** | **相容性**：INV-01 保持（reducer 是**投影**，非第二真源）；INV-04/05 不变（事件层零改动）；ADR-021 归属不变（拒绝事件已落该会话 ⇒ 投影随之） |
| **D-6** | **content 口径（脱敏）**：`<tool> 调用被拒绝:<guard_id\|policy_ref>`；审批来源为固定文本（"人工审批未通过" / "超时"）。**不含参数原文与凭据**（C-6） |
| **D-7** | **APR-501（headless 无通道）例外 → 选 (a)**：该路径**不发 `approval.requested`**（零事件零等待）⇒ **无可关联事件**。故由**执行器**把该结局经**既有** `_on_error` 通道发射 `tool.error`（天然配对）。**未新增事件类型、未改变 `guard.rejected`/`approval.denied` 的产生逻辑** |
| **D-8** | **反查兜底**：`approval.denied/timeout` 的 `approval_id` 找不到对应 `approval.requested` ⇒ **不抛错**，降级为剥离（fail-safe） |
| **D-9** | **终局兜底**：投影末尾统一 `drop_unpaired_tool_calls` —— 仍无配对的 `assistant.tool_calls` 予以剔除（旧日志 / 反查失败的最后防线）。**只作用于投影，不动日志**（INV-01） |

---

## Consequences

### 正面

1. **E-1 协议合法**：投影恒为 provider 合法形态（无悬空）。
2. **E-2 反馈可达**：模型获得脱敏的拒绝原因，可改口重试。
3. **E-3 审计不变**：事件层零改动（发射点、强同步、载荷、落点全部不动）。
4. **E-4 单一真源**：零新事件；拒绝事实仍只在既有事件里。
5. **E-5 归属不变**：投影随事件落该会话（ADR-021）。
6. 与既有 `tool.error` 分支**同构** —— 是"把同款处理补到拒绝路径"，非新机制。

### 负面 / 代价

1. **必须修订权威规格**：`PRD-Core.md:304` 是**唯一权威**文档的成文规则（本 ADR 声明取代；**该文件本身尚未编辑**，见 §Migration Impact）。
2. **审批路径需反查**：`approval.denied` 的 `trace` 只有 `{"kind":"approval.verdict"}`（`approval.py::_emit_verdict`），**不带 `call_id`** ⇒ 必须经 `approval_id == approval.requested.seq` 反查其 `trace.call_id`。
3. **行为变更**：拒绝事实对模型**由不可见变为可见** —— 任何依赖旧行为的断言须按 CORE-03 重判（**实测：现有用例中无此类断言**）。
4. `tools_executor.py` 的 APR-501 分支新增一次事件发射（**仅换出口**，判定语义与返回类型不变）。

### 违反它的后果

| 情形 | 后果 |
|---|---|
| **不修（现状）** | 每个被拒调用使下一轮请求 **400** ⇒ 会话中断；模型无从学习 |
| **采纳 B** | 第二真源（违 INV-01）+ 词表 77→78 + 三处硬断言 + 文本守卫连锁 |
| **采纳 C** | 模型反复重试同一禁用调用（预算空转）；反馈仍缺失（不达 E-2） |
| **只做 D-1 不做 D-7** | **APR-501 仍悬空**（headless 是最常见的无人值守形态）⇒ 修复只覆盖一半 |

---

## Migration Impact

### 已改（本 ADR 的 IMPLEMENT）

| 文件 | 改动 | 量级 |
|---|---|---|
| `pyharness/core/session.py` | reducer：新增 `guard.rejected` 配对分支、`approval.denied/timeout` 反查配对分支、`approval.requested` 索引、`paired` 跟踪、`_drop_unpaired_tool_calls` 终局兜底、`_APPROVAL_REJECT_NOTES` + `_guard_reject_note`（脱敏文本） | **+82 / −5** |
| `pyharness/core/tools_executor.py` | APR-501 分支增发 `tool.error`（**复用既有 `_on_error` 通道**） | **+7 / −0** |
| `docs/DIS-CORE.md` | §4.2 reducer 伪码：补齐既有 `tool.error` 漂移 + 新增拒绝配对分支 + 终局兜底 | 文档 |
| `tests/invariants/test_inv_core.py` | **T-1 ~ T-9 + T-4b（10 用例）** | **+253 / −0** |

### 明确不改

`approval.py` · `tools_guard.py` · `governance/*` · `events/*`（词表/payload）· `persistence.py` · `.ai-coding/PROTOCOL.md` · `ARCHITECTURE_DECISION_RECORD.md` · `docs/INVARIANT_REGISTRY.md`

### ⚠️ 未完成项（须人工裁定）

| # | 项 | 说明 |
|---|---|---|
| **U-1** | **`PRD-Core.md:304` 本体未编辑** | 本次授权清单未含该文件。当前状态：**由本 ADR 声明取代其字面表述**（与 ADR-020 "声明取代设计文档 §2.3" 的先例同型）。若要求 PRD 自洽，需另行授权编辑 |
| **U-2** | **`docs/ADD.md` 未登记 ADR-022** | 同上（不在授权清单）。按 S1 的 P-1/C-2 教训，**不应长期停留于"ADR 已存在却未登记"**，建议尽快回填 |

### 验证

| 项 | 值 |
|---|---|
| 新增用例 | **10**（T-1~T-9 + T-4b）全绿 |
| 全量回归 | **1751 collected / 1749 passed / 0 failed / 0 errors / 2 skipped**（基线 1741/1739 ⇒ **+10，零回归**） |
| Mutation | **M-A / M-B / M-C / M-D 全 RED**，各由其专属用例捕获（**M-C 仅 T-4b 捕获**） |
| 事件 schema | **77 / 14 / 3 不变** |
| 功能复验 | guard 拒绝后 `配对 tool 消息 ids = {'fv4'}`、**悬空 = 无**，content = `fs.read_file 调用被拒绝:g-fs-path`（脱敏） |
