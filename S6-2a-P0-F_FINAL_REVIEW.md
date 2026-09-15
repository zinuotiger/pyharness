# S6-2a-P0-F_FINAL_REVIEW.md — INV-02 P0 修复最终审查

> **阶段**：S6-2a-P0-F Final Review（**只读独立核验**）
> **审查对象**：commit **`50c19ca`**（`fix: restore F042 mini LLM endpoint`）
> **日期**：2026-09-15 ｜ **基线**：`142765e` → 修复后 `50c19ca`｜ **HEAD** `50c19ca` ｜ ahead 33
> **本文件性质**：**只读审查**。未修改生产代码 / 测试 / Registry / Event Schema；**唯一的写动作** = 依授权对 `docs/KEY-FINDINGS.md` **KF-A 条目做 2 行最小状态更新**（KF-B 未动）；**未 commit、未 push**。
> **核验方式**：所有结论**独立复算**（重新读实现、重新跑 AST 扫描、重新跑测试），**不引用**修复报告的自述。

---

## 1. 修复前问题

| 项 | 内容 |
|---|---|
| **现象** | `pyharness/core/auto_title.py:36` 直接调用 `ctx.llm.chat([...], tools=None, ctx=ctx)` |
| **生产接线** | `pyharness/engine.py:805-806` —— `run_for_task` 在 `loop.wake()` **返回之后**、`res.reason=="complete"` 时触发 |
| **后果** | `llm.chat` 出现**第二个调用方**（除 agent-loop 外），且该调用在循环之外、**不经过**轮数/取消/预算前置闸 |
| **违反** | **Canonical `INV-02`**（`docs/INVARIANT_REGISTRY.md`）："全库 `llm.chat` 唯一合法调用方 = agent-loop" |
| **等级** | P0（不变量违约）；**非安全漏洞**（审计留痕完好、无工具副作用） |

---

## 2. 根因

**F042 的规格指定了一个不存在的出口。**

```
PRD-Core.md:1251   await ctx.llm.mini(...)         ← 规格指定
       ↓
grep "def mini" pyharness/ → 零命中               ← 实现缺失
       ↓
auto_title.py:36   ctx.llm.chat(...)               ← 退化到对话出口（INV-02 点名者）
```

**架构层成因**：`LLMClient` 当时已有 `summarize`(F058) 与 `json_chat`(F045) 两个系统工具出口，**唯独缺 F042 指定的 `mini`**。

---

## 3. 修复内容（独立核验）

| 文件 | 变更 | 独立确认 |
|---|---|---|
| `pyharness/core/llm.py` | 新增 `mini()`，落在 **:974-985**（`chat_stream` 收于 :972、`summarize` 起于 :987） | 已读源码确认；`+13/−0` |
| `pyharness/core/auto_title.py` | `:36-37` → `text = await ctx.llm.mini(prompt, ctx=ctx)`；`:41` `resp.content` → `text` | `git diff 142765e HEAD` = **恰 3 行**（见下） |
| `tests/invariants/test_inv_core.py` | 新增 5 用例（3 静态 + 2 行为） | 已读，5 passed |
| `docs/specs/llm.py.md` | 新增 `mini` 小节（+17） | 已确认 |

**`auto_title.py` 相对修复前的完整 diff（逐行核验）**：

```diff
     try:
-        resp = await ctx.llm.chat([{"role": "user", "content": prompt}],
-                                  tools=None, ctx=ctx)
+        text = await ctx.llm.mini(prompt, ctx=ctx)
     except PyHError as e:
         log.info("auto_title skipped code=%s", e.code)
         return None
-    title = (resp.content or "").strip().strip('"“”\' ').splitlines()[0:1]
+    title = text.strip().strip('"“”\' ').splitlines()[0:1]
     title = (title[0] if title else "").strip()[:TITLE_MAX]
```

⇒ **`TITLE_MAX`、`first[:500]`、失败/空降级、`session.renamed` payload 全部未动**（§9 逐项复核）。

---

## 4. `mini()` / `chat()` 边界（**基于实现，非注释**）

**实测签名（`llm.py:974`）**：`async def mini(self, prompt: str, *, ctx: Any) -> str`
**实测函数体（`:982-985`）**：
```python
        resp = await self._chat_any("chat", [{"role": "user", "content": prompt}],
                                    None, ctx)
        return (resp.content or "").strip()
```

| 核验项 | 实测结论 | 证据 |
|---|---|---|
| **signature** | `(prompt: str, *, ctx: Any) -> str` —— 单 prompt、`ctx` **必填关键字**、返回 `str` | `llm.py:974` |
| **input/output contract** | 入 = **单串 prompt**（无 messages 列表、**无 tools**）；出 = **纯文本**（`content` strip） | `:983-985` |
| **与 `chat()` 的职责区别** | `chat(messages, tools=None, *, ctx) -> LLMResponse`（全量 messages + tools + 含 `tool_calls`/`usage`/`seq`）vs `mini` 的单串入出 ⇒ **契约面三层皆异**（入参/出参/语义） | `:964-967` vs `:974-985` |
| **是否复用统一底层真源** | ✅ 唯一汇聚点 `_chat_any`（`:957`）→ 降级链或直连适配器 → `LLMAdapter.chat`（`:647`）。**未新建链路** | AST：`mini` 体内调用的属性方法 = **`['_chat_any', 'strip']`** |
| **usage accounting** | ✅ 复用 `report_usage`（`:558` 落 `llm.usage` + `:561` `task_add`）—— **零新增代码** | 同链路 |
| **event logging** | ✅ 复用 `llm.request`(`:661`)/`llm.usage`(`:558`)/`llm.response`(`:682`) —— **事件类型零新增**（实测 `EVENT_TYPES` 仍 77） | §5-2 |
| **timeout** | ✅ 复用 `asyncio.wait_for(..., timeout=self.timeout.total_s)`（`:663`，F017）—— **未新增第二套超时真源** | 同链路 |
| **error behavior** | ✅ 复用 `normalize_exc`（`:665`）与 `LLM-3xx` 结构化上抛；`mini` **自身不吞错** | `:983-985` 无 try/except |
| **是否意外改变 `chat()` 语义** | ❌ **无改变** —— `chat`(`:964-967`)/`chat_stream`(`:969-972`) 源码**逐字未动**；`+13/−0` 为纯追加 | `git diff` |
| **是否只是 rename wrapper** | ❌ **不是** —— `mini` 体内**不含** `chat`/`chat_stream` 调用（AST 实测）；它经 `_chat_any` 复用链路，与 `summarize`/`json_chat` 同构 | AST + `test_inv02_mini_is_system_tool_entry_not_chat_wrapper` |
| **出口类别** | ✅ **System Tool LLM Endpoint**（与 `summarize`:987 / `json_chat` 同类）；`chat`/`chat_stream` = **Agent Loop LLM 出口** | `_chat_any` 汇聚 + 无 tools + 返回纯文本 |

---

## 5. 四重验证证据（**独立复算**）

### 5.1 静态

| # | 断言 | 独立复算结果 |
|---|---|---|
| 1 | `auto_title.py` 的 `.chat()` / `.chat_stream()` 直接调用 = **0** | `src.count("chat")` = **0**；`src.count("mini")` = **1** ✅ |
| 2 | `getattr(<x>.llm, …)` 合法动态调用**仅**存在于 agent_loop | 全包 AST 扫描 → `['pyharness/core/agent_loop.py:241']`（**唯一**）✅ |
| 3 | 包内**零**直接 `.chat(`/`.chat_stream(` 属性调用 | 全包 AST 扫描 → 命中 **0** ✅ |
| 4 | `mini` 调用边界符合**类别式**设计（非 chat 包装） | `mini` 体内属性调用 = `['_chat_any','strip']`；`{"chat","chat_stream"} & called` = **False** ✅ |

### 5.2 行为

独立复跑 `pytest tests/invariants/test_inv_core.py -v` → **5 passed**。
`_RecordingLLM` 实测：`mini_calls == 1` · `chat_calls == 0` · `chat_stream_calls == 0` · 入参为 `str` · 落 `session.renamed(by="auto", actor="system")` · 幂等路径不再调 LLM。✅

### 5.3 反证（mutation check）

| 项 | 结论 |
|---|---|
| 此前是否执行 | ✅ 已执行（`S6-2a-P0-F_CHANGE_REPORT.md` §8 留证）：注入 `mini → chat` 变异后 **2 failed / 3 passed** —— **静态**测试（扫 `.chat(`）与**行为**测试（替身 `chat` 即 raise）**双路捕获** |
| **本轮是否重跑** | ❌ **未重跑** —— 重跑需临时改生产代码，违本轮"只读 / 不改生产代码"边界 |
| **无残留 mutation** | ✅ **已独立确认**：`git diff HEAD --stat` **为空**（工作区 clean） |
| **当前是否恢复正确实现** | ✅ `auto_title.py:36` = `text = await ctx.llm.mini(prompt, ctx=ctx)`（已读源码） |
| **机制层面是否必然捕获** | ✅ 可静态推定：`_RecordingLLM.chat`/`.chat_stream` 被调即 `raise AssertionError`；`test_inv02_no_direct_conversation_entry_call` 扫描 `.chat(` 属性调用 ⇒ 任何回退都被双路拦截 |

### 5.4 回归

| 项 | 独立复算结果 |
|---|---|
| **全量** | ✅ **1678 collected / 0 failures / 0 errors / 2 skipped** ⇒ **1676 passed / 2 skipped / 0 failed**（49.9s，exit 0） |
| **baseline** | 1671 passed / 2 skipped / 0 failed |
| **差值** | **+5 passed**（= 新增的 5 条 INV-02 用例）；**既有 1671 全部保持通过** ⇒ **零既有回归** ✅ |
| **schema** | `EVENT_TYPES=77` · `SYNC_TYPES=14` · `TRANSIENT_TYPES=3` ✅ 不变 |

---

## 6. INV-02 修复结论

| 项 | 结论 |
|---|---|
| **当前 Canonical INV-02** | "禁止绕过 Agent Loop 直接使用其专用 `chat()` LLM 出口"（`llm.chat` 唯一合法调用方 = agent-loop）—— **未被修改** |
| **`agent_loop` 是否仍为 `chat()` 合法核心调用方** | ✅ 是 —— `agent_loop.py:241` `getattr(ctx.llm, chat_fn)`，**全包唯一动态派发点** |
| **`auto_title` 是否仍直接调用 `chat()` / `chat_stream()`** | ✅ **否**（源码 `"chat"` 命中 = 0） |
| **`auto_title` 是否改用 `mini()`** | ✅ 是（`:36`） |
| **是否需要修改 INV-02** | ❌ **不需要** |
| **是否需要向 INV-02 增加例外名单** | ❌ **不需要** —— 采用**类别式边界**（System Tool LLM 出口由 `LLMClient` 公开面枚举） |
| **INV-02 状态** | ✅ **GREEN** |

---

## 7. KF-A Closure Decision

| 字段 | 内容 |
|---|---|
| **Finding** | `auto_title` 越过 Agent Loop 直接调用 `llm.chat`，违反 INV-02（`docs/KEY-FINDINGS.md` A.1） |
| **Root Cause** | F042 指定的 `mini()` 出口在实现中缺失 ⇒ 退化到 `chat` |
| **Remediation** | commit `50c19ca`：新增 `LLMClient.mini()` + `auto_title` 改走 `mini` + spec 最小补充（生产代码 2 文件 `+15/−3`） |
| **Verification** | 四重证据**独立复算通过**（静态 4 项 · 行为 · 反证留证 + 无残留 · 全量回归 +5 无回归） |
| **Residual Risk** | 见 §10（**均不属"出口选择"违约**） |
| **Closure Decision** | ✅ **`KF-A = CLOSED`** |

### ⚠️ Closure 的**精确含义**（防混淆，依人工强调）

| 判断 | 结论 |
|---|---|
| **`KF-A = CLOSED` 是否表示 INV-02 已恢复？** | ✅ **是**（Inv-02 GREEN，出口选择违约已在实现层消除） |
| **是否表示 F042 的其他 6 项也已解决？** | ❌ **否** —— 6 项**全部仍 `DEFERRED`**（§9） |
| **是否表示 KF-B 已解决？** | ❌ **否** —— KF-B **仍 `OPEN`**（§8） |
| **是否表示 `auto_title` 已受三闸约束？** | ❌ **否** —— 修复仅改变**出口类别**，**未引入** budget/cancellation/governance 三闸（KF-B 范围） |

⇒ **`KF-A CLOSED` 的边界 = "错误出口选择"这一违约点**；**不得**解读为 F042 完备或 KF-B 消解。

**已执行的写动作**：`docs/KEY-FINDINGS.md` 中 **KF-A 条目的 `处置` 与 `状态` 两行**已最小更新（`OPEN` → `CLOSED`，附 commit 与边界声明）。**KF-B 条目零改动**（`状态` 仍 `OPEN`，已核验）。

---

## 8. KF-B 当前状态

| 项 | 状态 |
|---|---|
| 系统工具类 LLM 出口不经运行时三闸（轮数 / 取消 / 预算） | **`OPEN`** —— 本轮**未处理** |
| `summarize(prompt, budget=400)` 的 `budget` 未实际执行 | **`OPEN`** —— 本轮**未处理** |
| 统一 System LLM Gateway | **未实施** |
| 是否新建 INV 编号 | ❌ **否** |
| 本轮是否修改 KF-B 条目 | ❌ **否**（`docs/KEY-FINDINGS.md` 中 KF-B 零改动） |

⇒ **KF-B 独立登记、独立评估**（建议并入 durability/reliability 阶段）。

---

## 9. F042 剩余问题（**全部 `DEFERRED`，本轮未处理**）

| # | 项目 | PRD | 当前实现（本次修复后**未变**） | 状态 |
|---|---|---|---|---|
| 1 | 输入截断 | `first[:200]` | `first[:500]`（`auto_title.py:22`） | **DEFERRED** |
| 2 | 标题长度上限 | ≤ 24 字 | `TITLE_MAX = 64`（`:16`） | **DEFERRED** |
| 3 | 失败降级 | "前 20 字符" | `return None`（`:38-40`） | **DEFERRED** |
| 4 | 空结果降级 | "前 20 字符" | `return None`（`:43-44`） | **DEFERRED** |
| 5 | `test_f042_title.py` | 要求存在（`PRD:1878`） | 不存在 | **DEFERRED** |
| 6 | `auto_title` 模块 spec | 体例要求 | 不存在（D-4 已裁定） | **DEFERRED** |

**独立核验**：`auto_title.py` 相对修复前 diff **仅 3 行**（§3）⇒ 上述 6 项**确未被误计入本次修复**，也**未被顺带修改**。

---

## 10. Residual Risks（残余风险）

| # | 风险 | 等级 | 说明与建议（**本轮不实施**） |
|---|---|---|---|
| **R-1** | **`test_inv02_loop_is_the_only_dynamic_entry_dispatcher` 硬编码行号** | **P3（测试质量）** | 断言为 `sites == ["pyharness/core/agent_loop.py:241"]` ⇒ `agent_loop.py` 在 :241 **之前**的任何增删都会让该用例**误报 RED**（不变量仍成立）。**失败是响亮的、非静默**，不影响本次修复正确性。**建议**改为断言"恰 1 个派发点 **且** 其文件 == `agent_loop.py`"（不锁行号）。**本轮未改**（边界：不改测试） |
| **R-2** | **静态不变量范围 = `pyharness/`** | 已声明边界 | `scripts/{e2e_real_llm,probe_proxy_env,probe_tools_call}.py` 存在 3 处直调 `.chat(` —— 它们是**人工探针 / e2e 脚本**，不参与装配、不被 `pyharness` import ⇒ **不在 INV-02 运行时边界内**。已在测试文件 docstring 显式声明范围。**非缺口**，但属**范围假设**，若将来脚本被纳入运行时即需重估 |
| **R-3** | **`mini` 为新增公开 API，无 Protocol 约束** | **P3** | 全仓无 LLM 门面 Protocol（消费方一律 `getattr` 鸭子取用）。未来消费方**理论上**可误用 `mini` 承载对话轮（丢失 tools/轮语义）。当前无此用法；类别由 `docs/specs/llm.py.md` 与三条静态用例声明 |
| **R-4** | `auto_title` 仍无独立 spec 与 `test_f042_title.py` | **P3** | 已登记为 F042 DEFERRED 项（§9#5/#6） |
| **R-5** | `auto_title` 仍不受三闸约束 | **P1（已分离）** | **属 KF-B**，非本次修复缺口；不因 KF-A 关闭而消解 |

**R-5 是唯一 P1 级残余**，且**已被裁定明确划归 KF-B 独立处理** ⇒ **不构成本次修复的阻塞项**。

---

## 11. 是否允许进入 S6-2b

| 项 | 结论 |
|---|---|
| **前置条件** | INV-02 主断言 `test_inv02_llm_chat_sole_caller` 已于本次修复落地并 **GREEN**（实际落在 `tests/invariants/test_inv_core.py`，即 D-1 裁定位置，S6-2b 可直接复用） |
| **S6-2b 的剩余范围** | `tests/invariants/test_inv_core.py` **扩展** INV-01/03~09 编号化用例 + `test_inv_governance.py` 补 **INV-G3** 专项 |
| **是否有阻塞项** | ❌ **无**（R-1/R-3/R-4 为 P3 质量项；R-5 属 KF-B） |
| **结论** | ✅ **`S6-2b = READY`**（**不自动开始**，等待人工授权） |

---

## 12. 最终判定

> # ✅ **S6-2a-P0-F = PASS**

| 标签 | 值 |
|---|---|
| **S6-2a-P0-F** | **PASS** |
| **INV-02** | **GREEN** |
| **KF-A** | **CLOSED**（仅限"出口选择"违约；F042 其余 6 项仍 DEFERRED） |
| **KF-B** | **OPEN** |
| **S6-2b** | **READY** |

**判定依据**：① 修复语义正确且**未修改 INV-02 / 未加例外名单**；② `mini` 经实现核验属 **System Tool LLM 出口**，**非 rename wrapper**，且**未改变 `chat` 语义**；③ 四重证据**独立复算通过**（静态 4 项 · 行为 · 反证留证 + 工作区 clean 无残留 · 全量 **1676/2/0** 仅 +5 新增无回归）；④ 冻结面（Registry / Governance Core / Event Schema `77/14/3` / L-3 / deferred debt）**零改动**（`git diff 4f05c95 HEAD` 逐项 = 0）。

**本轮唯一写动作**：`docs/KEY-FINDINGS.md` 的 **KF-A 条目 2 行**状态更新（已核验 diff 仅 2 行，KF-B 零改动）。

**未执行**：未改生产代码 · 未新增测试 · 未改 Registry / Event Schema · 未修 KF-B · 未修 F042 其他偏离 · 未修 L-3 · **未进入 S6-2b** · **未 commit** · **未 push**。

**`git status`**：`M docs/KEY-FINDINGS.md` + `?? S6-2a-P0-F_FINAL_REVIEW.md`；**HEAD 仍 `50c19ca`**。

---

**S6-2a-P0-F Final Review 结束。判定 = PASS。等待人工授权；未 commit、未 push。**
