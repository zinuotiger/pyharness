# S6-2a-P0-F_CHANGE_REPORT.md — INV-02 P0 修复实施报告（KF-A）

> **阶段**：S6-2a-P0-F（INV-02 P0 Remediation Implementation）
> **日期**：2026-09-15 ｜ **计划基线 commit**：`142765e`（S6-2 P0 remediation design 冻结）
> **性质**：**最小修复**。范围严格限 ① 新增 `LLMClient.mini()` · ② `auto_title` 改用 `mini()` · ③ 为证明修复的必要测试 · ④ 最小文档补充 · ⑤ 验证 INV-02 恢复。
> **baseline（修复前）**：**1671 passed / 2 skipped / 0 failed**（collected 1673）
> **修复后**：**1676 passed / 2 skipped / 0 failed**（collected **1678**，**+5 = 新增用例**，零回归）
> **schema**：`EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`（**不变**）

---

## 1. Root Cause（根因）

**F042 被设计为"系统工具"类直调，PRD 为其指定了专用出口 `mini()`；但该出口在实现中不存在，实现者只能落到唯一的对话出口 `chat` ⇒ 形成 INV-02 违约。**

```
PRD-Core.md:1251   title = await ctx.llm.mini(f"用≤24字概括: {first[:200]}")
                        │
                        ▼
                   grep "def mini" pyharness/ → 零命中（mini 不存在）
                        │
                        ▼
auto_title.py:36   ctx.llm.chat([...], tools=None, ctx=ctx)   ← 对话出口（INV-02 点名）
                        │
                        ▼
                   INV-02 违约：llm.chat 出现第二个调用方，且该调用在循环之外、不过三闸
```

**深层成因**：`LLMClient` 当时**已有** `summarize`（F058）与 `json_chat`（F045）两个系统工具出口，**唯独缺少 F042 指定的 `mini`** ⇒ F042 成为唯一"规格指定了出口、实现却没有该出口"的功能。

---

## 2. 修改文件与修改行数（精确 numstat）

| # | 文件 | 变更 | numstat |
|---|---|---|---|
| 1 | `pyharness/core/llm.py` | **新增** `LLMClient.mini()`（插入于 `chat_stream`(:969) 与 `summarize`(:987) 之间，落在 **:974-985**） | **+13 / −0**（纯新增） |
| 2 | `pyharness/core/auto_title.py` | **只改 LLM 出口**：`:36-37` 的 `ctx.llm.chat([...], tools=None, ctx=ctx)` → `ctx.llm.mini(prompt, ctx=ctx)`；`:41` 的 `resp.content` → `text` | **+2 / −3** |
| 3 | `tests/invariants/test_inv_core.py` | **新增**（5 用例：3 静态 + 2 行为） | **新增文件** |
| 4 | `docs/specs/llm.py.md` | **新增** `### async def mini(...)` 小节（D-3 授权的最小补充） | **+17 / −0** |
| 5 | `S6-2a-P0-F_CHANGE_REPORT.md` | 本报告 | 新增文件 |
| 6 | `S6-2a-P0-F_PLAN.md` | Phase 1 实施计划（本修复的设计记录） | 新增文件 |

**生产代码合计：`pyharness/**` 2 文件 · +15 / −3。**其余 `pyharness/**` **零变更**（逐文件核验见 §9）。

---

## 3. 为什么这是最小修复

| 判据 | 说明 |
|---|---|
| **规格已有** | `PRD-Core.md:1251` 明文指定 `ctx.llm.mini` ⇒ 本次是**实现既有规格**，非新增架构决策 ⇒ **无需 ADR**（D-2 已裁定） |
| **零新增真源** | `mini` 是 `_chat_any("chat", …, None, ctx)` 的薄包装，与 `summarize`/`json_chat` **同构**；不新建链路、不新建事件、不新建计数 |
| **零外溢** | Event Schema **不变**（77/14/3）· Governance Core **不变** · Agent Loop **不变** · 既有测试**零修改** |
| **改动面** | 2 个生产文件、**+15/−3**；`auto_title` 的行为变化**仅限"该次 LLM 调用走哪个出口"** |
| **不夹带** | 未修 KF-B、未实现三闸、未碰 F042 其他行为偏离（§7） |
| **可回退** | 文件级即可回退（`mini` 自包含无既有引用；`auto_title` 改回 1 处调用） |

---

## 4. `mini()` 与 `chat()` 的边界（**类别式，非例外名单**）

| 维度 | `chat()` / `chat_stream()` | `mini()` |
|---|---|---|
| **出口类别** | **Agent Loop LLM 出口** | **System Tool LLM 出口**（与 `summarize`/`json_chat` 同类） |
| **签名** | `chat(messages, tools=None, *, ctx) -> LLMResponse` | `mini(prompt, *, ctx) -> str` |
| **入参** | 全量 messages 列表（会话上下文）+ tools schema | **单 prompt**，**无 tools 面** |
| **出参** | `LLMResponse`（`content`/`tool_calls`/`usage`/`finish_reason`/`seq`） | **纯文本**（strip 后） |
| **语义** | **一轮对话**（工具步、轮计数、收敛指纹） | **一次系统工具调用**（无轮语义） |
| **INV-02** | **被点名保护**：唯一合法调用方 = `agent_loop` | **不在覆盖面** |
| **合法调用方** | `agent_loop.py:241`（经 `getattr(ctx.llm, chat_fn)` 动态派发） | `auto_title`（F042）；未来同类系统工具 |
| **实现** | `_chat_any("chat"/"chat_stream", …)` | `_chat_any("chat", …, None, ctx)` |

**⚠️ 关键**：`mini` **不是** `chat` 的换名包装 —— 二者**契约面不同**（入参/出参/语义三层皆异），且修复后形成**可静态判定的类别边界**（§10-1/§10-2），而非需要逐一枚举的例外名单。**未把 `mini` 加入 INV-02 的例外名单。**

---

## 5. INV-02 修复前 / 后状态

| 项 | 修复前 | 修复后 |
|---|---|---|
| `llm.chat` 的调用方 | `agent_loop`（合法）+ **`auto_title`（违约）** | **仅 `agent_loop`** |
| `auto_title` 的 LLM 出口 | `ctx.llm.chat([...], tools=None, ctx=ctx)`（对话出口） | `ctx.llm.mini(prompt, ctx=ctx)`（系统工具出口） |
| `getattr(ctx.llm, <method>)` 动态派发点 | `agent_loop.py:241`（唯一） | `agent_loop.py:241`（唯一，**未变**） |
| 包内直接 `.chat(`/`.chat_stream(` 属性调用 | **1**（`auto_title.py:36`） | **0** |
| KF-A 状态 | `OPEN`（违约） | **已在实现层消除**（`docs/KEY-FINDINGS.md` 附录 A.1 保持登记，`处置` 待你裁定后回填） |
| **INV-02** | **RED** | ✅ **GREEN** |

**⚠️ 未随之修复的（明确声明）**：`auto_title` 仍**不经过**轮数 / 取消 / 预算前置闸 —— 该面属 **KF-B**，本次**未处理**（§8）。⇒ **INV-02 GREEN，但 KF-B 仍 OPEN。**

---

## 6. 静态证据

| # | 断言 | 手段 | 结果 |
|---|---|---|---|
| 1 | **`auto_title.py` 不再直接 `.chat(`** | `grep -n "chat" pyharness/core/auto_title.py` | **零命中** ✅ |
| 2 | 包内**无任何**直接 `.chat(`/`.chat_stream(` 属性调用 | `test_inv02_no_direct_conversation_entry_call`（AST） | **PASS**（修复前此断言 **RED**，见 §8 反证） |
| 3 | `agent_loop.py:241` 仍是 `chat` 的**唯一动态派发点** | `test_inv02_loop_is_the_only_dynamic_entry_dispatcher`（AST，断言 `sites == ["pyharness/core/agent_loop.py:241"]`） | **PASS** |
| 4 | `mini` **不是** `chat` 的换名包装 | `test_inv02_mini_is_system_tool_entry_not_chat_wrapper`（AST：`mini` 体必须含 `_chat_any`，且**不得**含 `chat`/`chat_stream`） | **PASS** |
| 5 | 系统工具出口与对话出口**不混淆** | 同 4（类别断言）；`mini`/`summarize`/`json_chat` 走 `_chat_any`，`chat`/`chat_stream` 由 `agent_loop` 经 getattr 派发 | **PASS** |
| 6 | 生产代码 diff 面 | `git diff --name-only HEAD -- pyharness` | 仅 `core/llm.py` · `core/auto_title.py` |

---

## 7. 行为证据（记录型 fake LLM）

`tests/invariants/test_inv_core.py::test_inv02_auto_title_uses_mini_and_never_chat`

| 断言 | 期望 | 实测 |
|---|---|---|
| `mini_calls` | **1** | ✅ 1 |
| `chat_calls` | **0** | ✅ 0 |
| `chat_stream_calls` | **0** | ✅ 0 |
| 入参形态 | **单 `str`（非 messages 列表）** | ✅ `isinstance(prompts[0], str)` |
| 落盘事件 | `session.renamed`（`by="auto"`, `actor="system"`） | ✅ 逐字段一致 |
| 幂等（已有 `session.renamed`） | 返回 `None` 且**不再调 LLM** | ✅ `mini=0, chat=0`，无新增事件 |

**替身设计**：`_RecordingLLM.chat` / `.chat_stream` **被调用即 `raise AssertionError`** ⇒ 反向锁死"不得调用对话出口"。

---

## 8. 反证证据（mutation check，**已执行并已恢复**）

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 | 备份修复版（`tmp/auto_title_fixed.bak`，`tmp/` 已 gitignore） | — |
| 2 | **注入变异**：把 `text = await ctx.llm.mini(prompt, ctx=ctx)` 临时替换为 `_r = await ctx.llm.chat([...], tools=None, ctx=ctx); text = _r.content` | — |
| 3 | 运行 `tests/invariants/test_inv_core.py` | ✅ **2 failed / 3 passed** —— `test_inv02_no_direct_conversation_entry_call`（静态）与 `test_inv02_auto_title_uses_mini_and_never_chat`（行为）**均变红** |
| 4 | 从备份**恢复**正确实现 | 已恢复 |
| 5 | 核验无残留 | `grep -c "ctx.llm.chat\|_r.content" pyharness/core/auto_title.py` → **0** ✅ |
| 6 | 复跑 | ✅ **5 passed**（GREEN） |

**结论**：测试**不是空转** —— 它能真实捕捉 KF-A 的违约形态（静态 + 行为双路捕获）。

---

## 9. 全量测试与核验

| 项 | 结果 |
|---|---|
| **定向**：`pytest tests/invariants` | ✅ **22 passed**（含新增 5） |
| **全量回归** | ✅ **1678 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1676 passed / 2 skipped / 0 failed**（exit 0，44.5s） |
| **与 baseline 对比** | **1671 → 1676**（**+5 = 新增用例**）；**0 failed 不变** ⇒ **不低于 baseline** ✅ |
| **schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3`（**不变**）✅ |
| **生产代码 diff 面** | `core/llm.py`（+13/−0）· `core/auto_title.py`（+2/−3）—— **仅此 2 个文件** ✅ |
| 既有测试是否被改 | **否**（`git diff --name-only HEAD -- tests` 仅新增文件）✅ |
| Registry / Event Schema / Governance Core / Agent Loop | **未修改** ✅ |
| `LLMClient.__all__` | **无需改**（`__all__` 只列模块级对象，不含类方法）✅ |
| Protocol 同步 | **无需**（全仓无 LLM 门面 Protocol，消费方一律 `getattr` 鸭子取用）✅ |

---

## 10. F042 其他未处理项（**保持 OPEN / DEFERRED**）

> 依人工裁定：归入 **F042 Behavior Compliance 后续问题**，**本次未修**，也**未因新增 `mini()` 而顺带修改**。

| # | 项目 | PRD | 当前实现 | 状态 |
|---|---|---|---|---|
| 1 | 输入截断 | `first[:200]` | `first[:500]`（`auto_title.py:22`） | **DEFERRED** |
| 2 | 标题长度上限 | ≤ **24 字** | `TITLE_MAX = 64`（`:16`） | **DEFERRED** |
| 3 | 失败降级 | "前 20 字符"规则 | `return None`（`:38-40`） | **DEFERRED** |
| 4 | 空结果降级 | "前 20 字符"规则 | `return None`（`:43-44`） | **DEFERRED** |
| 5 | 验收测试 `test_f042_title.py` | 要求存在（`PRD:1878`） | 不存在（**本次未建**） | **DEFERRED** |
| 6 | `auto_title` 模块 spec | 体例要求 | 不存在（**本次未建**，D-4 已裁定） | **DEFERRED** |

**本次仅解决**：`chat` → `mini`。**未扩大范围。**

---

## 11. KF-B 未处理项（**保持 OPEN**）

| # | 项 | 状态 |
|---|---|---|
| 1 | 系统工具类 LLM 出口不经过轮数 / 取消 / 预算三闸 | **OPEN**（未实现） |
| 2 | `summarize` 的 `budget` 参数未实际执行 | **OPEN**（未修） |
| 3 | 是否建立统一 System LLM Gateway | **未实施** |

**未新建 INV 编号** · **未修改 Registry** · **未实现任何三闸逻辑**。

---

## 12. 结论

| 项 | 结论 |
|---|---|
| **KF-A 违约是否在实现层消除** | ✅ **是** —— 包内 `llm.chat` 仅剩 `agent_loop` 一个调用方（动态派发），`auto_title` 改走 `mini` |
| **INV-02 是否恢复** | ✅ **GREEN**（静态 + 行为 + 反证 + 全量回归 四重证据） |
| **是否修改 INV-02 定义 / 扩大例外** | ❌ **否** —— 采用**类别式边界** |
| **是否夹带 KF-B / F042 其他偏离** | ❌ **否** |
| **可进入 S6-2b？** | 由你裁定（本阶段按指令**不自动进入**） |

**`docs/KEY-FINDINGS.md` 附录 A.1 的 `处置` 字段未回填**（该动作需你裁定后授权）。**未 push。**

---

**本报告为 S6-2a-P0-F 的实施记录。生产代码改动仅 `core/llm.py`(+13/−0) 与 `core/auto_title.py`(+2/−3)；新增 1 测试文件 + 1 spec 小节。**
