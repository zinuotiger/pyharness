# S6-2a-P0-F_PLAN.md — KF-A 最小修复实施计划（Phase 1 · **只计划，不实施**）

> **阶段**：S6-2a-P0-F（INV-02 最小修复实施计划）
> **日期**：2026-09-15 ｜ **基线 commit**：**`142765e`**（S6-2 P0 remediation design 冻结）｜ **HEAD** `142765e` ｜ worktree clean ｜ ahead 32
> **授权范围**（人工裁定）：**仅** ① 新增 `LLMClient.mini()` · ② `auto_title` 改用 `mini()` · ③ 为证明修复而新增必要测试 · ④ 必要的最小文档说明 · ⑤ 验证 INV-02 恢复。
> **本文件性质**：**计划**。**未修改任何代码**；`pyharness/core/llm.py` 与 `pyharness/core/auto_title.py` **均未触碰**（等 Phase 2 授权）。
> **当前 baseline**：**1671 passed / 2 skipped / 0 failed** · `EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3`

---

## 0. 特别确认：`mini()` **不是** `chat()` 的换名包装

> 人工裁定的必答项。**结论：不是换名，是"出口类别"与"契约面"的双重区分。**

| 维度 | `chat()`（对话出口） | `mini()`（系统工具出口） |
|---|---|---|
| **契约签名** | `chat(messages: list[dict], tools: list \| None, *, ctx) -> LLMResponse` | `mini(prompt: str, *, ctx) -> str` |
| **入参语义** | **全量消息列表** —— 承载会话上下文（含历史、system 提示、tool 配对） | **单个 prompt** —— 无上下文组装、**无 tools 面** |
| **出参语义** | `LLMResponse`（`content` + `tool_calls` + `usage` + `finish_reason` + `seq`） | **纯文本**（`content` strip 后） |
| **调用语义** | **一轮对话** —— 调用方预期处于"轮"的生命周期内（工具步、轮计数、收敛指纹） | **一次系统工具调用** —— 无轮语义、无工具步、不参与收敛判定 |
| **在 INV-02 中的地位** | **被点名保护**："全库 `llm.chat` 唯一合法调用方 = agent-loop" | **不被 INV-02 覆盖**（是另一类出口，与 `summarize`/`json_chat` 同类） |
| **若误用会怎样** | 非循环调用方 = **违约**（KF-A 现状） | 无违约（类别正确） |

**为什么这构成"类别"而非"例外"**（守 `P-4`，不扩大例外范围）：

- 改 `INV-02` 表述 = **枚举式例外**（"`auto_title` 也可调 `chat`"）⇒ **不可机械校验**，且每新增一个系统工具都要**再扩一次**。
- 新增 `mini` = **类别式边界**：`INV-02` 表述**一字不改**，系统工具出口作为**另一类**由其成员枚举；新增系统工具**无需触碰 `INV-02`**。
- ⇒ 修复后可写出**可判定的静态断言**（§11）。

**⚠️ 诚实限制（必须随本计划声明）**：`mini` **只修"出口选择"（①）**，**不修"无闸"（②）**。② 已被裁定**明确划归 KF-B**。修复后 **`INV-02` GREEN，但 `KF-B` 仍 `OPEN`**。**不得以 INV-02 之名夹带三闸实现。**

---

## 1. `LLMClient.mini()` 的最终接口

```python
# pyharness/core/llm.py — LLMClient 类内，插入位置：chat_stream(:969-972) 之后、summarize(:974) 之前
    async def mini(self, prompt: str, *, ctx: Any) -> str:
        """系统工具级单次文本出口(F042 auto_title 等消费):单 prompt 进 → 纯文本出。

        与 chat 的类别区别:无 messages 列表、无 tools 面、无对话轮语义,
        不返回 LLMResponse;与 summarize/json_chat 同类(同走 _chat_any),
        差别仅在输出形态(纯文本 vs 压缩语义 vs JSON 抽取)。
        超时(F017)/计费(F029)/事件/降级链全部复用既有链,不新增真源。
        """
        resp = await self._chat_any("chat", [{"role": "user", "content": prompt}],
                                    None, ctx)
        return (resp.content or "").strip()
```

| 项 | 决定 |
|---|---|
| 方法名 | `mini` —— **取自 PRD F042 的指定名**（`PRD-Core.md:1251`），非自创 |
| 签名 | `async def mini(self, prompt: str, *, ctx: Any) -> str` |
| `ctx` | **必填 keyword-only（无默认值）** —— 与 `summarize(ctx=None)` **有意不同**：`_chat_any` 全链依赖 `ctx`（`ctx.session.append` / `_llm_cfg(ctx)`），留默认可选只会把错误推迟到调用中途 ⇒ **在 API 边界 fail-fast** |
| 返回 | `str`；**空内容返回 `""`（不抛）** —— 由调用方决定降级（与 `summarize` 同款） |
| `budget` 参数 | ❌ **不设**（`summarize.budget` 的前车之鉴：有参数无实现 = 误导性"有上界"印象；见 KF-B） |
| `__all__` | **无需修改** —— `llm.py:1017` 的 `__all__` 只列模块级对象（数据结构/异常/函数/适配器），**不含类方法** |
| Protocol 同步 | **无需** —— 全仓无 LLM 门面 Protocol；消费方一律 `getattr(llm, "<name>", None)` 鸭子取用（`compaction.py:513` / `plan_mode.py:347`）；已核验 |
| 预估规模 | **+9 行（纯新增）** |

**被否决的备选签名**：
- `mini(prompt, *, ctx=None)` —— 可选 ctx 会把配置/落盘错误推迟到中途，且与"系统工具出口必有 ctx"的事实不符。**否决**。
- `mini(prompt, *, budget=...)` —— 见上，**否决**（实为 KF-B 的题）。
- 让 `auto_title` 走 `getattr(ctx.llm, "mini", None)` + 缺件 `CYC-999`（`plan_mode._json_chat` 体例）—— 更保守但多 4 行；因 `mini` 将成为 `LLMClient` 的**必备门面面**，缺件只可能是测试替身问题 ⇒ **本计划取直接调用**（最小修复），并在 §11-3 的测试中用带 `mini` 的替身覆盖。

---

## 2. `mini()` 的内部复用路径（**零新增真源**）

```
mini(prompt, *, ctx)                                    ← 新增（9 行）
   └─ _chat_any("chat", [{"role":"user","content":prompt}], None, ctx)   llm.py:957 （既有）
        ├─ 降级链在岗 → chat_with_fallback(..., method="chat")            llm.py:959-961（既有）
        └─ 否则       → self.require().chat(..., ctx=ctx)                 llm.py:962（既有）
             └─ LLMAdapter.chat                                            llm.py:647（既有）
                  ├─ :661  append("llm.request")             ← 事件（既有）
                  ├─ :663  asyncio.wait_for(timeout=total_s) ← 超时 F017（既有）
                  ├─ :665  normalize_exc                      ← 错误归一（既有）
                  ├─ :680  report_usage → :558 append("llm.usage") + :561 counters.task_add  ← 计量（既有）
                  └─ :682  append("llm.response")             ← 事件（既有）
```

⇒ **与 `summarize`(:974-982) / `json_chat`(:984-1003) 完全同构**：三者都是 `_chat_any` 的薄包装，**唯输出形态不同**。**本次不新增任何链路、不新增任何真源。**

---

## 3. `auto_title` 的修改点

**文件**：`pyharness/core/auto_title.py` · **仅改 `:35-42` 区段**（**2 行**）

| 行 | 现状 | 改为 |
|---|---|---|
| `:36-37` | `resp = await ctx.llm.chat([{"role": "user", "content": prompt}],`<br>`                          tools=None, ctx=ctx)` | `text = await ctx.llm.mini(prompt, ctx=ctx)` |
| `:41` | `title = (resp.content or "").strip().strip('"“”\' ').splitlines()[0:1]` | `title = text.strip().strip('"“”\' ').splitlines()[0:1]` |
| `:38-40` | `except PyHError as e:` → `log.info` + `return None` | **不变**（错误处理保持现状） |
| `:42` | `title = (title[0] if title else "").strip()[:TITLE_MAX]` | **不变**（`TITLE_MAX=64` **不动** —— 属 F042 行为符合性，本次不处理） |
| `:16` | `TITLE_MAX = 64` | **不变** |
| `:22` | `[:500]` | **不变** |
| `:28` | 幂等检查 | **不变** |
| `:45-47` | `append("session.renamed", ..., actor="system")` | **不变** |

**明确不改**：`engine.py`（触发点不变）· `agent_loop.py`（循环不变）· `TITLE_MAX` · `[:500]` · 失败/空降级语义 · `session.renamed` payload。⇒ **行为变化仅限"该次 LLM 调用走哪个出口"**。

---

## 4. 是否新增 / 修改事件

| 项 | 结论 |
|---|---|
| 新增事件类型 | ❌ **否** —— `mini` 复用 `_chat_any("chat")`，落的是既有的 `llm.request` / `llm.usage` / `llm.response` |
| 修改 payload 字段 | ❌ **否** |
| 修改词表 / `SYNC_TYPES` / `TRANSIENT_TYPES` | ❌ **否** —— 冻结为 **77 / 14 / 3** |
| `session.renamed` | ❌ **不变**（`auto_title.py:45-47` 原样） |
| **结论** | **Event Schema 零变更**（`docs/EVENT-SCHEMA.md` 不动） |

---

## 5. timeout 行为

| 项 | 结论 |
|---|---|
| 机制 | **复用** `LLMAdapter.chat` 的 F017 总时长闸（`llm.py:663` `asyncio.wait_for(..., timeout=self.timeout.total_s)`） |
| 是否新增超时真源 | ❌ **否** —— 新增第二套超时会制造配置分裂（违"单一真源"纪律） |
| 行为变化 | **无** —— 修复前 `chat` 与修复后 `mini` 走**同一个** `LLMAdapter.chat`，超时行为**逐字相同** |
| 是否引入取消（F025） | ❌ **否** —— 属 KF-B |

---

## 6. usage accounting

| 项 | 结论 |
|---|---|
| 机制 | **自动获得** —— `report_usage`（`llm.py:538-562`）→ `append("llm.usage")`（`:558`）+ `cnt.task_add(ev.payload)`（`:561`，F032 预算硬闸读 token） |
| 新增代码 | **0 行** |
| 行为变化 | **无** —— 计量口径与 `chat` 完全一致（同一条 `LLMAdapter.chat`） |
| 是否顺带修 `summarize.budget` | ❌ **否** —— 属 KF-B，本次禁改 |

---

## 7. error handling

| 层 | 行为 |
|---|---|
| `mini()` 自身 | **不吞错** —— 沿用 `_chat_any` → `normalize_exc`（`llm.py:665`）的归一与 `LLM-3xx` 结构化上抛 |
| `auto_title` | **保持现状** —— `except PyHError` → `log.info("auto_title skipped code=%s")` → `return None`（`:38-40`） |
| `engine.py` | **保持现状** —— `except Exception` → `log.debug("auto_title failed: %s")`，**不阻断对话**（`:808-809`） |
| 行为变化 | **无** |

---

## 8. fallback 在本次是否保持现状

| 层 | 本次处理 |
|---|---|
| **LLM 层降级链**（F013/F028，`chat_with_fallback`） | ✅ **自动继承** —— `_chat_any` 同一条链（`llm.py:957-961`），**无需改代码** |
| **`auto_title` 的功能降级**（PRD 的"前 20 字符"规则） | ⛔ **保持现状（`return None`）** —— F042 行为符合性，**本次不处理**（见 CHANGE_REPORT §5） |
| **空结果降级** | ⛔ **保持现状（`return None`）** —— 同上 |
| **结论** | **fallback 行为本次完全保持现状**；仅 LLM 出口方法变化 |

---

## 9. 与 `chat()` 的职责边界（落地后）

| | `chat()` / `chat_stream()` | `mini()` |
|---|---|---|
| **定位** | 会话对话出口 | 系统工具出口（文本） |
| **合法调用方** | **仅 `agent_loop`**（`agent_loop.py:241`，经 `getattr(ctx.llm, chat_fn)`） | 非循环的**系统工具消费方**（`auto_title`；未来同类） |
| **INV-02 覆盖** | ✅ 被点名保护 | ❌ 不在覆盖面（与 `summarize`/`json_chat` 同类） |
| **可静态校验** | ✅ "除 `agent_loop.py` 外无模块调用公开 `.chat(`/`.chat_stream(`" | ✅ "系统工具出口成员 ∈ 显式允许集" |
| **禁止** | 非循环调用方使用 | 用于**对话轮**（会丢失轮语义/工具面） |

---

## 10. 与 `summarize()` / `json_chat()` 的关系

| 出口 | 行 | 输出形态 | 现有消费方 | 本次是否改动 |
|---|---|---|---|---|
| `summarize` | `llm.py:974` | 纯文本（压缩语义；`budget` 参数**未实现**） | `compaction._call_summarize`（`compaction.py:510`） | ❌ **不改**（其 `budget` 失效属 KF-B） |
| `json_chat` | `llm.py:984` | JSON 抽取（dict/list） | `plan_mode._json_chat`（`plan_mode.py:341`） | ❌ **不改** |
| **`mini`（新增）** | 新 | **纯文本（无附加语义）** | **`auto_title`** | ✅ **新增** |

**三者同属 System Tool LLM Endpoint**：同走 `_chat_any("chat", …, None, ctx)`、同为单次无 tools、同享超时/计量/事件/降级。**差别仅输出形态**。⇒ **本次只补齐类别的缺失成员，不重构既有成员**（KF-B 保持 OPEN）。

---

## 11. 需要新增哪些测试（Phase 2 交付）

> 授权范围③"为证明该修复而新增必要测试"。**只增不改**；**不触碰 F042 行为符合性的断言**（≤24 / `[:200]` / 前 20 字符降级 —— 均已登记为后续问题）。

| # | 文件 | 用例 | 类型 | 断言要点 |
|---|---|---|---|---|
| **T1** | `tests/unit/test_llm.py`（**追加**） | `test_mini_returns_text_and_shares_event_path` | unit | ① `mini` 返回 `str`（strip 后）；② 落 **`llm.request` + `llm.usage` + `llm.response`** 三类事件（与 `chat` 同）；③ 请求**不带 tools** |
| **T2** | 同上 | `test_mini_requires_ctx_keyword` | unit | 缺 `ctx` → `TypeError`（**API 边界 fail-fast**，§1 的契约） |
| **T3** | `tests/unit/test_auto_title.py`（**新建**） | `test_auto_title_uses_mini_not_chat` | unit | 注入**记录型假 LLM** ⇒ 断言 `mini` 被调用**恰 1 次**、`chat` 被调用 **0 次** |
| **T4** | 同上 | `test_auto_title_writes_renamed_once` | unit | 落 `session.renamed`（`by="auto"`）；二次调用**幂等**返回 `None` 且**不再调 LLM** |
| **T5** | `tests/invariants/test_inv_core.py`（**新建**） | `test_inv02_llm_chat_sole_caller` | **invariant**（静态 AST） | 全库 `pyharness/**` 的公开 `.chat(` / `.chat_stream(` 调用点 **⊆ 允许集 `{agent_loop.py}`**（允许集**显式声明并写明理由**，沿用 S2-5.1 的 T-4 注释扫描体例） |
| **T6** | 同上 | `test_inv02_no_direct_endpoint_client` | invariant（静态） | 除 `llm.py` 外无 LLM 端点客户端构造（实测当前 **GREEN**，可先落） |

**注**：T5/T6 落在 `tests/invariants/test_inv_core.py`（S6-2b 的主交付文件）。**本修复只需其中的 INV-02 两条**；S6-2b 再扩展同一文件补齐 INV-01/03~09。⇒ **避免重复建设**（决策点，见 §14）。

---

## 12. 如何证明 INV-02 恢复 GREEN

**四重证据**（缺一不可）：

| # | 证据 | 手段 | 预期 |
|---|---|---|---|
| **1** | **静态**：`llm.chat` 再无第二个调用方 | T5 的 AST 扫描（允许集 = `{agent_loop.py}`） | GREEN |
| **2** | **行为**：`auto_title` 走 `mini` | T3 的记录型假 LLM（`mini`=1 / `chat`=0） | GREEN |
| **3** | **反证自检**（证明测试**有效**，非空转） | **临时**把 `auto_title` 改回 `chat` → T5 **必须变红** → 恢复 | 先 RED 后 GREEN |
| **4** | **全量回归 + 冻结面** | `1671 / 2 / 0` 不变；`EVENT_TYPES=77 / SYNC_TYPES=14 / TRANSIENT_TYPES=3` 不变 | 不变 |

**验证命令（Phase 2 执行）**：

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_llm.py tests/unit/test_auto_title.py tests/invariants/test_inv_core.py -q
```

```bash
.venv/Scripts/python.exe -m pytest tests --ignore=tests/unit/test_desktop_native.py --ignore=tests/unit/test_shell_parity.py --junit-xml=tmp/junit_p0f.xml -p no:cacheprovider
```

```bash
git diff --name-only HEAD -- pyharness
```

**预期 diff 面（仅 2 个生产文件）**：`pyharness/core/llm.py`（+9）· `pyharness/core/auto_title.py`（改 2）。**其余 `pyharness/**` 零变更。**

---

## 13. 修复边界（人工裁定）

### ✅ 允许
1. 新增 `LLMClient.mini()`（`pyharness/core/llm.py`）
2. `auto_title` 改用 `mini()`（`pyharness/core/auto_title.py`）
3. 为证明修复而新增必要测试（T1~T6）
4. 必要的最小文档说明（修复报告；**是否回填 `docs/specs/llm.py.md` 需另行确认** —— 该文件是契约文档）
5. 验证 INV-02 恢复

### ⛔ 禁止
修改 `INV-02` 定义 · 修改 `docs/INVARIANT_REGISTRY.md` · 修复 KF-B · 实现 budget/cancellation/governance 三闸 · 修改 Agent Loop · 修改 Event Schema · 修改 Governance Core · 修复 L-3 · 顺便修复 F042 其他行为偏离（`200→500` / `24→64` / failure fallback / empty fallback）· 创建 acceptance tests · 创建 security tests · 进入 S6-3 · 进入 S7 · push

---

## 14. 待确认决策点（Phase 2 前）

| # | 事项 | 建议 |
|---|---|---|
| **D-1** | T5/T6 落 `tests/invariants/test_inv_core.py`（该文件是 S6-2b 的主交付）还是落 `tests/unit/`？ | **落 `tests/invariants/`** —— INV-02 是架构不变量，且 S6-2b 将扩展同一文件，**避免建两处** |
| **D-2** | 新增 `mini()` 是否需 ADR？ | **不需要** —— `PRD-Core.md:1251` 已指定 `mini`，属**实现既有规格**；按 S2-1 偏离登记先例记入修复报告即可 |
| **D-3** | 是否同步回填 `docs/specs/llm.py.md`（契约文档）新增 `mini` 接口？ | **建议回填**（该文件是模块契约，缺 `mini` 会形成新的文档偏离）；但**须计入"最小文档说明"范围**由你确认 |
| **D-4** | `auto_title` 缺 spec（`docs/specs/auto_title.py.md` 不存在）是否本次补？ | **不补** —— 已登记为 F042 后续问题（CHANGE_REPORT §5 #6） |

---

## 15. 回退方案

| 级别 | 操作 | 影响 |
|---|---|---|
| **提交级** | `git revert <P0-F commit>` | 保历史；回到 `142765e` |
| **文件级** | 删 `llm.py` 的 `mini` 方法（自包含，无既有引用）· `auto_title.py` 恢复 `resp = await ctx.llm.chat([...], tools=None, ctx=ctx)` 与 `resp.content` | 2 文件，**无数据影响** |
| **测试级** | 删 `tests/unit/test_auto_title.py`、`tests/invariants/test_inv_core.py`，撤 `test_llm.py` 的追加 | 无既有断言被改 |

**回退后必须复现**：`1671 / 2 / 0` · `77 / 14 / 3` · 且 `auto_title.py` 回到 `ctx.llm.chat`（即 KF-A 重新 OPEN）。

---

## 16. 实施顺序（Phase 2，待授权）

```
① llm.py 新增 mini()                      (+9 行，纯新增)
② auto_title.py 改 2 行                    (chat → mini；resp.content → text)
③ 新增 T3/T4 (tests/unit/test_auto_title.py)   ← 证明"走 mini 而非 chat"
④ 新增 T1/T2 (tests/unit/test_llm.py 追加)     ← 证明 mini 的契约与事件路径
⑤ 新增 T5/T6 (tests/invariants/test_inv_core.py) ← 证明 INV-02 恢复（含反证自检）
⑥ 跑定向 + 全量回归；核验 diff 面仅 2 个生产文件
⑦ 出修复报告（含 D-2 的偏离登记；D-3 若授权则含 specs 回填）
```

---

**本文件为计划产物。未修改任何代码；`llm.py` / `auto_title.py` 未被触碰。等待 Phase 2 的人工授权；未 commit、未 push。**
