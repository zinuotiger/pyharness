# S2-5_SCOPE_UPDATED_AND_COMMENT_CONSISTENCY_DESIGN.md — `scope.updated` 面确认 + 注释一致性修复设计

> **阶段**：S2-5 设计（**只出设计，不落码**）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`806a82e`（工作区 clean）
> **依据**：[ADR-020](ADR-020-policy-event-boundary.md)（Q1 分工裁定）· [S2-4.1_CHANGE_REPORT.md](S2-4.1_CHANGE_REPORT.md) · [docs/baseline/](docs/baseline/BASELINE_REPORT.md)
> **性质**：**未修改代码、未修改文档、未 commit**。

---

## 1. 当前事实（实测）

### 1.1 词表真实计数（运行时核验）

| 项 | 实测 | 各处文档/注释的声称 |
|---|---|---|
| `EVENT_TYPES` | **74** | 57 / 64 / 73 / 74 —— **四种口径并存** |
| `SYNC_TYPES` | **12** | 文档多写 8 或 11 |
| `TRANSIENT_TYPES` | **3** | — |
| `*Payload` 模型类 | **72** | 文档写"57 事件负载" |
| 被 `EVENT_TYPES` 引用的独立模型 | **72** | — |

### 1.2 `scope.updated` 的实际形态

| 项 | 事实 |
|---|---|
| 注册 | ✅ **已注册**（`vocab.py` `_CORE_EVENT_TYPES` 有该行；`is_registered("scope.updated")` = **True**） |
| 载荷 | `ScopeUpdatedPayload`：`{op: str(min_length=1), added: list[str], reason: str(min_length=1)}`（`payload.py:561-566`） |
| 通道 | `transient=False` → **普通持久事件**；**不在** `SYNC_TYPES`（非强同步） |
| 发射点 | **2 处，`op` 恒 `"tighten"`**：`scope.py:304`（`Scope.tighten`）、`:318`（`Scope.note_tighten`，engine 装配期预设档位用） |
| 语义 | 会话级 deny 集合的**单调收紧留痕**（`added` = 新增的**工具名**） |
| 现存的过期注释 | `scope.py:41` 称"budget.paused/scope.updated **尚未入 events 词表**（57 锁定类型之外）" → **与实测相反** |

### 1.3 `policy.updated`（对照）

注册 ✅ · `SYNC_TYPES` ✅（强同步）· `op ∈ {add, enable, disable}` · 载荷 `{policy_id, version, fingerprint, op, added, reason}` · 发射点 `governance/policy.py:emit_updated`（装配/启用/禁用）。

---

## A. `scope.updated` 的语义归类

| 备选归类 | 判定 | 理由 |
|---|---|---|
| 普通状态事件？ | **✅ 是** | `transient=False`；非强同步；`op="tighten"` 的会话级状态留痕 |
| scope 生命周期事件？ | ⚠️ **部分**（属"scope 状态变更"而非"scope 创建/销毁"） | 它记录策略面的一次**变更**，不是生命周期边界；scope 的边界事件是 `session.created`/`session.finished` |
| governance policy lifecycle？ | **❌ 否** | ADR-020 Q1 明确：策略集/版本/开关归 `policy.updated`；`scope.updated` 只承载**运行时收紧** |
| 是否应进入治理事件边界？ | **❌ 不应** | ADR-020 Q1 已裁定为**保留原语义**、**禁止两事件表达同一变化**；若纳入治理边界，就与 `policy.updated` 争夺职责——正是 Q1 要消除的重叠 |

**⇒ 归类结论**：`scope.updated` = **会话运行时状态事件（会话级策略收紧留痕）**，**不属治理层事件**。**ADR-020 的裁定与当前代码一致，无需改变。**

---

## B. `scope.updated` 与 `policy.updated` 的关系

| 维度 | `scope.updated` | `policy.updated` | 是否重叠 |
|---|---|---|---|
| **触发** | 会话内 deny 单调收紧（运行时） | 治理层策略集变化（装配 / enable / disable） | **不重叠** ✅ |
| **`op` 值域** | `{"tighten"}`（唯一） | `{"add", "enable", "disable"}` | **不重叠**（值域互斥）✅ |
| **`added` 语义** | 新增的 **工具名**（如 `fs.delete_file`） | 被禁用的 **rule_id**（如 `g-fs-path`） | ⚠️ **同名不同域** |
| **`reason` 语义** | 收紧原因（如 `preset:readonly`） | 治理动作原因（`assembly` / `guard-disabled`） | ⚠️ **同名不同域** |
| **生命周期** | 随会话 | 随策略集（会话可跨 run） | **边界清晰** ✅ |
| **通道** | 普通落盘 | **强同步** | 不冲突 |

**结论**：
1. **事件职责不重叠、`op` 值域互斥** —— Q1 的分工在**代码层已满足**。
2. **唯一可改进点**：`added` / `reason` 在**两个事件里同名但语义域不同**（工具名 vs rule_id；收紧原因 vs 治理动作原因）。这是**阅读混淆风险**，不是功能性冲突。**建议**（非必需）：在 `EVENT-SCHEMA.md` 的两张字段表下各加一句"本字段域"说明；**不要求改代码**（改字段名会破坏向后兼容，违反 `EVENT-SCHEMA` §7 规则 1"只增不改"）。
3. **无需新增/合并/拆分任何事件**。

---

## C. 过期注释与计数声明盘点（全库）

> 判定口径区分两类：**「当前计数声明」**（说"词表有 N 个" → 应更新）vs **「基线引用」**（说"§3 的 57 A-E 分组" → 可能是故意的基线名，可保留）。

### C-1 Tier A：**事实错误**（陈述与代码相反，非仅数字）

| # | 位置 | 现文 | 实测 | 严重度 |
|---|---|---|---|---|
| **A-1** | `pyharness/core/scope.py:41` | "注意：budget.paused/scope.updated **尚未入 events 词表**（57 锁定类型之外…）" | **两者均已注册**（`is_registered` 均为 True）→ 该注释是本模块"**偏离 4**"的**立论依据**，而**立论已失效**（现在它们会正常落盘，不再 EVT-102 降级） | **高**（会误导读者以为这两个事件只落本地日志） |
| **A-2** | `docs/EVENT-SCHEMA.md:481` | "本组 **4 型尚未入词表**（实测词表 73 型）→ 实现前经 `session.append` **一律 EVT-102 拒写**；升词表版本（**73→77**）" | **`policy.updated` 已于 S2-2.1 注册**（74 型）→ 4 型中 **1 型已入词表**；其余 3 型仍预登记；版本目标数 73→77 已被 73→74 部分兑现 | **高**（把"已注册可落盘"说成"会被拒写"） |

### C-2 Tier B：**计数漂移**（活文档 / 源码注释；建议统一到 **74 型 / 72 payload 模型 / 12 强同步**）

| # | 位置 | 现文 | 归类 |
|---|---|---|---|
| B-1 | `pyharness/events/vocab.py:178-180` | "74 事件类型全量入册（… **57** A-E 分组权威 + 词表外扩展 **14 项** …基线 57）" | 头部 74 已对；**算术不闭合**（57+14=71≠74） |
| B-2 | `pyharness/events/payload.py:4` | "**57 事件词表** + llm.retry" | 当前计数声明 → 74 型 / 72 模型 |
| B-3 | `pyharness/events/__init__.py:3` | "**57 事件**负载词表注册" | 当前计数声明 → 74 |
| B-4 | `pyharness/events/__init__.py:10` | "payload.py — **57 事件**各自 payload 负载模型" | 当前计数声明 → 74 / 72 |
| B-5 | `pyharness/bus/event_bus.py:106` | "不进 **57** 词表 EVENT_TYPES" | 当前计数声明 → 74 |
| B-6 | `pyharness/bus/event_bus.py:114` | "其余 **57** 词表类型" | 当前计数声明 → 74 |
| B-7 | `pyharness/bus/event_bus.py:143` | "词表 **57 核心类型**" | 当前计数声明 → 74 |
| B-8 | `pyharness/cli.py:619` | "词表 **57 类型**逐类型订阅" | 当前计数声明 → 74 |
| B-9 | `pyharness/core/approval.py:38` | "（**57 类型已锁定**，实测超字段 append → EVT-100）" | 当前计数声明 → 74 |
| B-10 | `pyharness/core/approval.py:78` | "trust_* 未入 **57** 词表" | **陈述正确**（`approval.trust_*` 确未注册）；仅数字 → 74 |
| B-11 | `pyharness/core/tools_guard.py:30` | "**57 类型已锁定**；实测：超字段 append → EVT-100" | 当前计数声明 → 74 |
| B-12 | `pyharness/core/tools_guard.py:38` | "guard.disabled 未入 **57** 词表" | **陈述正确**（`guard.disabled` 确未注册）；仅数字 → 74 |
| B-13 | `README.md:4` | "事件词表 **73 型**" | **S2-2.1 后已过期** → 74 |
| B-14 | `docs/EVENT-SCHEMA.md:101` | "词表 **57 个事件名**按 A-F 分组（F 组为治理层预登记…）" | 当前计数声明 → 74（且 F 组已部分激活：`policy.updated` 已注册） |
| B-15 | `CODE-MATRIX.md:37` | "事件词表 **64 类型**（57 核心 + plan/schedule 扩展）" | 当前计数声明 → 74（**但见 C-3 的"历史快照"考虑**） |

### C-3 Tier C：**建议不改**（历史快照）或**需裁定**（基线引用）

| # | 位置 | 现文 | 判定 |
|---|---|---|---|
| C-1 | `docs/baseline/{BASELINE_REPORT,TEST_BASELINE,CODE_METRICS,CURRENT_ARCHITECTURE}.md` | 多处"73 型 / 强同步 11" | **历史时点基线**（2026-09-14 S0 产物，明确标注"基线标识 `0cba75d`"）→ **改数等于伪造历史**。**建议不改**；如需，加一行"后续 S2-2.1 增至 74 / 12" |
| C-2 | `CODE-MATRIX.md`（整体，标"终版 2026-09-07"） | `:37` 的 64 类型等 | 同属**历史快照**（已被 S0 基线报告记载为"整体滞后"）→ 建议**不改数**（或加注"当前值见 docs/baseline/"） |
| **C-3** | `docs/specs/events.py.md:6,81,286` · `docs/specs/README.md:24` | "**57 个事件负载**词表注册" | ⚠️ **需你裁定**：specs 是**编码契约/基线定义**。若"57"指"**§3 的 A-E 基线**"，则它是**基线引用、可以保留**；若它被读作"当前词表大小"，则**应更新**。**我倾向"基线引用"**（依据：`vocab.py:178` 亦以"57 A-E 分组权威"作基线名）→ 但**建议加注一句**"基线 57 + 扩展登记见 EVENT-SCHEMA §3"以消除歧义 |

---

## 2. 问题列表（汇总）

| # | 问题 | 类型 | 影响 |
|---|---|---|---|
| **P-1** | `scope.py:41` 的"尚未入词表"是**事实错误**，且是该模块"偏离 4"的立论依据 | 事实错误 | 高：误导读者以为 `scope.updated`/`budget.paused` 不落盘 |
| **P-2** | `EVENT-SCHEMA.md:481` 说 4 型"尚未入词表"，实际 `policy.updated` 已注册 | 事实错误 | 高：与 S2-2.1 的交付直接矛盾 |
| **P-3** | 词表规模存在 **4 种口径**（57/64/73/74）散落 15 处 | 计数漂移 | 中：读者无法确定真值 |
| **P-4** | `vocab.py:178` 的记账算术不闭合（57+14=71≠74） | 计数漂移 | 中：注释自相矛盾 |
| **P-5** | `added`/`reason` 在 `scope.updated` 与 `policy.updated` 中**同名不同域** | 文档标注缺失 | 低：阅读混淆（非功能冲突） |
| **P-6** | `docs/baseline/*` 与 `CODE-MATRIX.md` 的数字已过期，但它们是**历史快照** | 历史记录 | 低：**不建议改** |

---

## 3. 是否需要代码修改

**结论：不需要任何「逻辑」修改；只需「注释 / docstring 订正」（零行为变更）。**

| 项 | 判定 |
|---|---|
| 是否需要新增/删除/合并事件 | **否**（ADR-020 的分工与代码一致） |
| 是否需要改 `scope.updated` 语义 | **否**（Q1 已裁定"保留现有语义"，且代码已满足） |
| 是否需要改 `SYNC_TYPES` / 落盘矩阵 | **否**（S2-4.1 已收敛真源） |
| 是否需要改 payload 模型 | **否**（改字段名违反 `EVENT-SCHEMA` §7 规则 1"只增不改"） |
| **需要做的** | **仅订正注释与文档计数**：Tier A（2 处事实错误）+ Tier B（15 处计数漂移） |

---

## 4. 修改范围（建议）

### 4.1 必改（Tier A — 事实错误，2 文件 2 处）

| 文件 | 位置 | 改动 |
|---|---|---|
| `pyharness/core/scope.py` | `:41`（模块"偏离 4"） | 改为事实描述：`budget.paused`/`scope.updated` **已入词表**（现 74 型），经 `session.append` 正常落盘；**删除已失效的"EVT-102 降级"立论**；保留 `_record` 的"事件出口注入式"说明（与"是否注册"无关） |
| `docs/EVENT-SCHEMA.md` | `§3.6` 的状态行（`:481`） | 改为：本组 4 型中 **`policy.updated` 已于 S2-2.1 注册**（词表 73→**74**，并入 `SYNC_TYPES`，见 ADR-020）；其余 **3 型仍为预登记**（`decision.issued` / `receipt.emitted` / `evidence.archived`），实现前 `append` 仍 `EVT-102` 拒写；版本目标改为"74→77（余 3 型实现时）" |

### 4.2 建议改（Tier B — 计数漂移；统一到 **74 型 / 72 payload 模型**）

**源码注释（8 文件 12 处）**：`events/vocab.py:178`（顺带修算术）、`events/payload.py:4`、`events/__init__.py:3,10`、`bus/event_bus.py:106,114,143`、`cli.py:619`、`core/approval.py:38,78`、`core/tools_guard.py:30,38`

**活文档（3 文件 3 处）**：`README.md:4`（73→74）、`docs/EVENT-SCHEMA.md:101`（57→74）、`CODE-MATRIX.md:37`（见 §4.3 的替代处置）

> **改法纪律**：`docs/*.md` 与源码注释中的 `57` 若为**当前计数声明** → 改为实测值；若为**基线引用**（如"§3 的 57 A-E 分组权威"）→ **保留 57 但补一句"基线 + 扩展见 §3"**。**逐处判定，不做全局替换**（避免把基线误改成当前值）。

### 4.3 建议不改（Tier C）

| 位置 | 处置 |
|---|---|
| `docs/baseline/*`（4 篇） | **不改**（历史时点基线，标注了 `0cba75d`）；可选**加一行**"后续 S2-2.1 起为 74 型 / 12 强同步" |
| `CODE-MATRIX.md` | **不改数**；建议**加注**"当前值见 `docs/baseline/BASELINE_REPORT.md`"（它已是公认的历史快照） |
| `docs/specs/events.py.md:6,81,286` · `docs/specs/README.md:24` | **需你裁定**（C-3）：保留"基线 57" + 加注，或更新为 74 |

---

## 5. 禁止修改范围

| 项 | 理由 |
|---|---|
| **任何事件的行为语义 / `op` 值域** | ADR-020 Q1 已冻结分工；本步只订正注释 |
| **`events/vocab.py` 的 `_CORE_EVENT_TYPES` / `SYNC_TYPES` 内容** | 词表 74 型、`SYNC_TYPES` 12 项已在 S2-2.1/S2-4.1 定型 |
| **`events/payload.py` 的任何模型字段** | 改字段名违反 `EVENT-SCHEMA` §7 规则 1（只增不改）；本步只改 docstring |
| **`persistence.py` / `session.py` / `event_bus.py` 的逻辑** | F-SYNC-1 已裁定不在 S2 内修 |
| **任何 ADR / `ARCHITECTURE_DECISION_RECORD.md`** | 冻结 |
| **`governance/*` 的代码** | S2-3/S2-4 已定型 |

> **整体约束**：本步是**零行为变更**的"注释/文档订正"——**不得**顺手调整任何常量、注册表内容或事件语义。

---

## 6. 测试计划

本步**不改变行为** → **不新增行为测试**。需要的是一道 **"文档/计数一致性"守卫**：

| # | 落点 | 用例 | 断言 |
|---|---|---|---|
| **T-1** | `tests/unit/test_events.py` | `test_vocab_counts_documented_consistently` | ① `scope.updated` 与 `budget.paused` **均 `is_registered`**（**封 P-1**：防注释与事实再次背离）；② `len(EVENT_TYPES) == 74`、`len(SYNC_TYPES) == 12`、`len(TRANSIENT_TYPES) == 3`（既有 `test_vocab_full` 已覆盖 74，此处补 12/3） |
| **T-2** | `tests/unit/test_events.py` | `test_no_stale_vocab_count_in_source` | **AST/文本守卫**：扫描 `pyharness/**/*.py` 的注释与 docstring，若出现形如"**词表 N 类型**/**N 事件词表**/**N 个事件**"且 N ∉ {74}（或 ≤57 的历史数字）→ 失败，并列出位置（封 P-3/P-4 复发） |
| **T-3** | `tests/unit/test_events.py` | `test_scope_updated_semantics_frozen` | ① `scope.updated` **不在** `SYNC_TYPES`；② `policy.updated` **在** `SYNC_TYPES`；③ 两者 `op` 值域互斥（封 Q1 分工不被后续误改） |

**验收标准**：
1. T-1~T-3 全绿；
2. 全量回归绿（基线 `1481 / 1479 passed / 2 skipped / 0 failed`）；
3. `git diff` 中**无任何非注释/非 docstring/非 .md 的行改动**（可用"逐文件 diff 只含注释行"人工核验）。

> **T-2 的谓词风险**：文本守卫易误报（例如"57 A-E 分组"是有意的基线引用）。**建议 T-2 采用白名单**：允许 `57` 出现在"基线 / A-E 分组 / 权威"语境，只拦截"**词表 N**/**N 事件词表**"这类**当前计数**句式。若误报率不可接受，可降级为**只对 Tier A 两处做存在性断言**（即断言那两句已改），放弃全局扫描。

---

## 7. 风险分析

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R-1 | **全局替换把"基线引用"误改成当前值** | **中高** | §4.2 明令**逐处判定、不做全局替换**；`57 A-E 分组`类表述保留并补注 |
| R-2 | 修改源码注释时误触代码行（缩进/上下文） | 中 | 只改注释/docstring 文本；**不改任何语句**；靠"全量回归绿 + diff 只含注释行"双重核验 |
| R-3 | `docs/baseline/*` 若被改，历史基线失真 | 中 | Tier C 明确**不改**（仅可选加注） |
| R-4 | T-2 文本守卫误报 → 逼出为"过测试"而扭曲注释 | 中 | 白名单 + 允许降级为"仅断言 Tier A 两处已改" |
| R-5 | `EVENT-SCHEMA.md` 是"协议规范"，改动可能被视为契约变更 | 中 | 本次改动仅**订正与代码不符的事实**（P-2），**不新增/修改任何协议语义**；如需可标注"勘误" |
| R-6 | `scope.py` 的"偏离 4"被删除后，失去"为何注入式事件出口"的记录 | 低 | **只删"尚未入词表"的错误部分**，保留"事件出口注入式、未接线降级"的正确说明 |

---

## 8. 是否影响 ADR

**结论：不影响任何 ADR；本步无需新建 ADR。**

| 项 | 判定 |
|---|---|
| ADR-020（Q1 事件边界） | **不受影响、无需修改**——本步只**订正与它不符的注释**，而代码早已符合 Q1；`scope.updated`/`policy.updated` 的分工**不变** |
| ADR-013/015/018（治理层） | 不受影响 |
| `ARCHITECTURE_DECISION_RECORD.md` | **不改**（FROZEN） |
| 是否需要 ADR-021 | **不需要**——本步是**勘误/一致性**，不是新决策 |
| 建议（非阻塞） | 在 S2-5 报告中**登记**"`scope.py` 偏离 4 的立论已失效"（属**偏离登记**范畴，非 ADR 范畴） |

> **注**：若你认定"删掉 `scope.py` 偏离 4 的立论"改变了模块的**偏离记录**（而偏离记录属契约面），则应在 S2-5 报告中完整登记"原立论 → 现事实"，而不是修改 ADR——与 ADR-019 的"取代记录"体例一致。

---

## 附：需你裁定

| # | 事项 | 建议 |
|---|---|---|
| **Y-2** | `docs/specs/*.md` 的 "57 个事件负载"：**基线引用**（保留 + 加注）还是**当前计数**（改为 74）？ | **基线引用**（保留 + 加注一句） |
| **Y-3** | `CODE-MATRIX.md:37`：改数 还是 加注？ | **加注**（历史快照，改数等于伪造） |
| **Y-4** | `docs/baseline/*`：完全不动 还是 加一行"后续增至 74"？ | **完全不动**（它们是带基线标识的时点记录） |
| **Y-5** | T-2 文本守卫采用哪种强度：全局扫描（+白名单）／ 仅断言 Tier A 两处已改？ | 先做**全局扫描 + 白名单**，误报不可接受则降级 |
| **Y-6** | 本步是否包含 Tier B 的**活文档 3 处**（README / EVENT-SCHEMA §3 intro / CODE-MATRIX） | README 与 EVENT-SCHEMA **改**；CODE-MATRIX **加注**（Y-3） |

**等待你裁定 Y-2~Y-6 后再落码（S2-5.1）。**
