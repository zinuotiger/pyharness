# S2-6_CONVERGENCE_REVIEW.md — S2 收敛最终审查与收口

> **阶段**：S2-6（**只审查、只出报告**——未修改任何代码、未修改 ADR、未修 `F-SYNC-1`、未 commit）
> **日期**：2026-09-14 ｜ **HEAD**：`576b038` ｜ 工作区 **clean** ｜ `main...origin/main [ahead 12]`
> **审查依据**：`ARCHITECTURE_DECISION_RECORD.md`（FROZEN，§3.1 M2 / §5.2 S2 行）· ADR-013~020 · `REFACTOR_PLAN.md` · `docs/ADD.md` · S2-1/S2-2.1/S2-3.2/S2-4.1/S2-5.1 五份 CHANGE_REPORT · S2-4/S2-5 两份 DESIGN
> **方法**：**不采信 CHANGE_REPORT 的自述**。M2 判据逐项回到**真实代码 + 运行时核验 + 实跑测试**复算；并做一次独立 **negative audit**。

---

## 0. 判定

# S2 = **COMPLETE**

**依据**：M2 的**全部退出判据已满足**（§2.4 / §3.4 / §4.4 逐项），且 **S2 全程对运行期执行面零改动**、**行为面零变化可证**。
**未关闭项 12 条**（§5）**全部为「已由 ADR 或人工裁定明确延期」或「S2 前既存」**，**无一构成 S2 blocker**（§6 分级 + §7 专项论证）。

---

## 1. 审查基线（实测）

| 项 | 值 |
|---|---|
| HEAD | `576b038` |
| 工作区 | **clean**（`git status --short` 空） |
| 未 push | `ahead 12` |
| **全量回归** | **1485 collected / 1483 passed / 2 skipped / 0 failed**（exit 0） |
| S2 相关测试文件用例 | **99** |
| S2 全程改动（`5de8b10..HEAD`） | **41 文件，+4396 / −53** |
| 异常产物 | 无（`exists.txt` 等均不存在） |

---

## 2. M2-1 核验：Governance Policy

| 判据 / 实际要求 | 当前实现 | 代码证据 | 测试证据 | 文档证据 | 满足 |
|---|---|---|---|---|---|
| `governance/policy.py` 存在 | 403 行 | `pyharness/governance/policy.py` | — | ADR-018 §目录冻结 | ✅ |
| `Policy` / `PolicyRule` / `PolicyRegistry` / `PolicyEngine` 齐备 | 4 类齐备 | `policy.py:145/208/271`（+`PolicyRule`） | `test_governance_policy.py` | ADR-018 | ✅ |
| `PolicyEngine.from_config()` 的 **injection boundary** | 含 `rules` / `params` / `chain_factory` / `validator` / `credential_paths` / `path_exists` / `link_resolver` / `approval_channel` / `policy_id` / `version` | 运行时 `inspect.signature` 实测 **13 参数** | T9（`chain_factory` 注入生效、未注入 → `chain is None`） | ADR-020 §决策 Δ-4（明记"增加 `chain_factory` 与 `rules` 两个注入位"） | ✅ |
| governance → `events`/`errors` **依赖方向** | 仅此二者 | **运行期传递闭包** = `{pyharness, errors, governance.*}`（触发惰性 `emit_updated` 后**只增 `events.*`**）；**AST**：`policy.py` → `errors` + `events.vocab`，`context.py`/`__init__.py` → 本包内 | T3（AST 断言） | ADR-018:308 | ✅ |
| **不得**依赖 `core`/`tools_guard`/`executor` | 零依赖 | 闭包 `VIOLATION(core/bus): NONE`；全库 `governance` 的消费者只有 `engine.py`（装配层，允许方向） | T3 | ADR-018 · ADR-020 Δ-4 | ✅ |
| `policy.updated` 已注册 | `is_registered` = **True** | `vocab.py` `_CORE_EVENT_TYPES` 含该行 | T-1/T-2 · `test_vocab_full` | ADR-020 | ✅ |
| `op ∈ {add, enable, disable}` | 实测 `POLICY_OPS = {'add','disable','enable'}` | `policy.py` `POLICY_OPS` | T6a（`tighten` → `CYC-999`）· T-3（值域冻结） | ADR-020 Δ-1 | ✅ |
| `config_ref` | **经 `Envelope.trace` 携带**，不入 payload | `policy.py:~265` `trace={"config_ref": …}`；`POLICY_UPDATED_FIELDS` 六字段不含它 | T6c（断言 `trace == {"config_ref": …}`）· T-3 | ADR-020 Δ-6 | ✅ |
| `scope.updated` **不被错误归入** governance policy lifecycle | 未归入 | governance 内 `scope.updated` 仅出现在 **2 处 docstring** + **1 处 `raise_code` 的提示文本**（`policy.py:~246` 的 `"(tighten 归 scope.updated)"`）——**无 import、无逻辑分支** | T-3（`scope.updated not in SYNC_TYPES`；字段面各守其域） | ADR-020 Δ-1/Δ-2 | ✅ |

**M2-1 = 满足。**

---

## 3. M2-2 核验：Runtime Assembly

| 判据 / 实际要求 | 当前实现 | 代码证据 | 测试证据 | 文档证据 | 满足 |
|---|---|---|---|---|---|
| `engine.py` 经治理层装配 | `_build_governance()` 子函数 | `engine.py:427`（定义）· `:533`（`gov_policy, guard = _build_governance(...)`）· `:647`（`governance=GovernanceContext(policy=gov_policy)`） | T-A/T-B（`build_spine` 真实装配） | ADR-013 接缝② | ✅ |
| `GovernanceContext` 生成 | `GovernanceContext(policy=…)` | `engine.py:647` | T9b（M2 形状：只挂 `policy`，4 字段 `None`） | ADR-018 | ✅ |
| `ctx.governance` 单实例挂载 | **两处挂载同一对象** | `core/agent.py:472`（`create_agent` 路径）· `engine.py:944`（`attach_engine_to_ctx` 路径） | T-B（`ctx.governance is spine.governance`）· T-C（attach 路径同一实例） | ADR-018（单实例挂载） | ✅ |
| 无重复实例 / 第二真源 | **只一条 GuardChain** | `spine.guard is spine.governance.policy.chain`（运行时核验 True）；`_build_governance` 以 `policy.chain` 作 guard，无第二次 `from_config` 直调 | **T-B**（该断言即为此设） | ADR-013 G-4 | ✅ |
| **未修改既定 runtime execution boundary** | ✅ | `git diff 5de8b10..HEAD -- pyharness/core/tools_executor.py` = **0**；`core/session.py` = **0**；`scope.py`/`approval.py` 有改动但**仅注释**（S2-5.1，AST 剥离 docstring 后逐字相同） | 全量回归绿 | ADR-013「唯一强制点不变」 | ✅ |
| guard execution chain **语义不变** | ✅ | `tools_executor.py:449` 的 `ctx.guard.evaluate(call, scope)` **调用点与签名未变**；`tools_guard.py` 的 `g1–g7` 判定逻辑**未动**（`+63/−0` 只加 `describe_rules` 只读投影 + S2-5.1 注释） | T-D（11 例行为 smoke 断言冻结期望）· T-C（参数同源）· 全量绿 | ADR-013 G-3/G-4 | ✅ |
| `authorize()` **未提前实现** | 无 `def authorize` | 全库仅 3 处**文档性提及**（`engine.py:441` · `governance/context.py:13` · `policy.py:23`，均写"不在本步声明"）；`ctx.governance` **零属性读取** | — | ADR-020 Δ-5（人工裁定） | ✅ |

**M2-2 = 满足。**

---

## 4. M2-3 核验：Event / Sync Consistency

| 判据 / 实际要求 | 当前实现 | 代码证据 | 测试证据 | 文档证据 | 满足 |
|---|---|---|---|---|---|
| **`SYNC_TYPES` 唯一真源** | **1 处定义**（`vocab.py:97`） | 全库 `_CORE_EVENT_TYPES/ SYNC_TYPES` 定义计数 = **1** | T-2 | ADR-019 P-3 · ADR-018 | ✅ |
| engine adapter 派生 | 函数内 import 真源 | `engine.py:696,702`（`sync=type_ in SYNC_TYPES`） | T1（**对全部 74 类型**断言 `sync == (type in SYNC_TYPES)`） | ADR-019 P-3 | ✅ |
| desktop adapter 派生 | 同上（`+1` 行 import） | `desktop/sessions.py:16,153` | T2（未绑定方法驱动真实 `_attach_persistence`；含 sid 过滤） | 同上 | ✅ |
| session / persistence / event_bus / cli / orchestration 消费点 | **全部派生** | `session.py:321` · `persistence.py:280` · `event_bus.py:209` · `cli.py:611-617` · `orchestration.py:138-145` | 全量绿 | ADR-019 P-3 | ✅ |
| `policy.updated` **已进强同步集合** | ✅ | 运行时 `'policy.updated' in SYNC_TYPES` = **True** | T-2 · T-3 | ADR-020 Q3 | ✅ |
| `scope.updated` **保持非强同步** | ✅ | 运行时 = **False** | T-3 | ADR-020 Q1 | ✅ |
| 是否仍存在**第二份硬编码 sync 集合** | **否**（见 §6-N5 的精度说明） | 4 个适配器全部派生 | **AST 守卫** `test_s24_no_hardcoded_sync_list_in_adapters`（扫 4 适配器） | — | ✅ |
| 有**测试防回归** | 有 2 道 | — | ① AST 守卫（S2-4.1）② **注释计数守卫 + 白名单**（S2-5.1 T-4） | — | ✅ |
| `SYNC_TYPES` 成员数 | **12** | 运行时 | T-2 | ADR-020 | ✅ |

**M2-3 = 满足。**

### 4.4 M2 退出判据（ADR §5.2 S2 行）总表

| 判据 | 状态 | 独立证据 |
|---|---|---|
| ① 策略指纹随规则内容变化 | ✅ | S2-1 T1/T2（8 项声明式字段各验一次 + 换实现指纹不变）；S2-3.2 T-A 复证装配态指纹可算 |
| ② 一致性测试（治理层判定与 `tools_guard` 逐例一致） | ✅ | **三层**：结构（T-A）+ **参数同源**（T-C：直接调**策略规则自己的** `check`，缺 `path` → `("reject","TLB-803")`）+ 行为（T-D 11 例冻结期望） |
| ③ 全量绿 | ✅ | 1483 passed / 0 failed |
| M2 必做项「`policy.updated` 落盘」（ADR §3.1 M2 行） | ✅ | S2-2.1 注册（74 型 + `SYNC_TYPES`）+ S2-4.1 真源收敛；T6c 真落盘断言 |

---

## 5. 偏离 / 延期项完整表

| ID | 问题 / 偏离 | 发现阶段 | 当前状态 | 是否影响 S2 完成 | 后续阶段 |
|---|---|---|---|---|---|
| **R-9** | 预算双信号（`scope.BudgetExhausted` → `reason=budget` vs `llm_fallback` 的 `PyHError("BUDGET-EXHAUSTED")` → `reason=error`） | S1 Exit Review | **未做**（ADR-019 裁定：非 S2 硬前置） | **否**（S2 不读 run 终态） | **S1.5** |
| **R-7** | `desktop_native/**` 因缺 `PySide6` 不可测（2 文件 / 5 用例 / 937 语句） | S0 基线（B-1） | 未变 | 否（基线遗留） | 基线/环境 |
| **R-8**（= 基线 V-1） | `agent.submit` 首次唤醒调 `loop.wake(env)` **不传 `ctx`**（`agent.py:279`），而 `_ctx` 仅 `run()` 内绑定 → 疑 `CYC-999`；且该路径**无生产调用点** | S0 基线 | **仍未核实**（S1 未处理，S2 未触及） | 否（无生产调用点；生产走 `engine.make_runner` 传 ctx） | 待核实（低优先） |
| **ADR-019 W3** | 预算单信号（同 R-9） | ADR-019 | 延期 | 否 | S1.5 |
| **ADR-019 ・落盘适配器收敛** | 5 处 bus→store 适配器 / 2 处硬编码 sync 清单 | ADR-019 判"**S2 内必修**" | ✅ **已于 S2-4.1 完成** | —（已关闭） | — |
| **ADR-019 ・`build_runner_components` 拆分** | 185 行 god function | ADR-019 判"**S2 内顺手做**" | ✅ **已于 S2-3.2 完成**（抽出 `_build_governance`） | —（已关闭） | — |
| **ADR-019 ・两套 repair 收敛** | `persistence.SessionStore.repair` vs `repair.repair_session` | ADR-019 | 延期 | 否 | S1.5 / S6 |
| **ADR-019 ・`_invoke` 处置** | `tools_executor._invoke` 零调用点，**但被 `docs/specs/tools_executor.py.md:75,180` 记载**（规格漂移，非纯死代码） | ADR-019 裁定 4 | 延期（**须先动 spec**） | 否 | S2 / S6（未做） |
| **S2-1 D-1** | `authorize()` 未在 M2 声明 | S2-1 | **保持延期**（属 S3） | 否（ADR-020 Δ-5 明确） | **S3** |
| **S2-1 D-2** | `policy.updated` 未注册 → `emit_updated` 降级 | S2-1 | ✅ **已由 S2-2.1 关闭** | — | — |
| **S2-1 D-3** | `ctx.governance` 未挂载（生产路径不经过治理层） | S2-1 | ✅ **已由 S2-3.2 关闭** | — | — |
| **S2-1 D-4** | `from_config` 签名与设计 §3.2 不同（新增两个注入位） | S2-1 | **实现已落地**；**设计文档 §3.2 该处仍为旧签名**（ADR-020 §决策 Δ-4 与备选方案 D 行已权威覆盖） | **否**（ADR-020 为权威，属"并读 ADR"） | **post-S2 文档加注** |
| **S2-1 D-5** | `op` 枚举与设计 §2.3 不同 | S2-1 | ✅ **已由 ADR-020 Δ-1 覆盖** | — | — |
| **S2-1 D-6** | `config_ref` 走 `trace` | S2-1 | ✅ **已由 ADR-020 Δ-6 覆盖** | — | — |
| **`F-SYNC-1`** | 强同步事件在适配器内 flush 失败 → 行被丢弃 + 总线 EVT-103 隔离 → **调用方静默未落盘** | S2-4（新发现） | **已登记（P1），未修**（人工裁定"不在 S2 修复"） | **否**（见 §7 专项论证） | **durability / reliability 阶段** |
| **S2-4 T5** | `test_F_SYNC_1_silent_loss_hole`（marker 红测试）未交付 | S2-4 | 未交付（实施步骤清单未含） | 否 | 随 `F-SYNC-1` 修复一并补 |
| **F-SYNC-1 归档** | 尚未进 `docs/KEY-FINDINGS.md` | S2-4 | 登记载体 = `S2-4.1_CHANGE_REPORT.md` §4（**该文件当时不在允许范围**） | 否 | 后续允许阶段转档 |
| **新：守卫误报精度** | S2-4.1 的 AST 守卫谓词在全库扫描下会**误报** `approval.py:222`（总线订阅清单）与 `compaction.py:441`（reducer 成员判定）——二者恰为 `SYNC_TYPES` 子集 | **S2-6（本轮）** | 守卫**当前只扫 4 个适配器文件**，故测试绿；但**不是全库证明** | **否** | post-S2（守卫精度） |
| **新：设计文档 §3.2 旧签名** | 同 S2-1 D-4，ADR-020 已覆盖但设计文档未加注 | S2-6（本轮复现） | 同上 | 否 | post-S2 |
| **新：`scope.py` 偏离 4 立论已失效** | 原"尚未入词表、会 EVT-102 拒写" | S2-5 | ✅ **已于 S2-5.1 勘误** | — | 建议 S2-6 登记（偏离登记，非 ADR） |

> **未把任何延期项算作 PASS**：上表"是否影响 S2 完成"列的**每一条"否"，都在 §6/§7 有独立理由**。

---

## 6. Negative Audit（10 项，逐项实测）

| # | 搜索项 | 结果 | 分级 |
|---|---|---|---|
| **N1** | `TODO` | `pyharness/` **0 命中** | — |
| **N2** | `FIXME` | **0 命中** | — |
| **N3** | `NotImplemented` | **1 处**：`core/mcp.py:46` —— `class Transport.request()` 的**抽象基类方法**（`StdioTransport`/`InlineTransport` 子类覆盖）→ 合法 ABC 模式，**与治理无关** | **intentional out-of-scope**（既有设计） |
| **N4** | dead / unreachable governance code | 运行时闭包仅 5 个模块；`governance/` 无未使用公开符号（4 类均被 `__init__` 再导出且被 engine/测试消费） | 无 |
| **N5** | **第二份 Policy / sync / event 真源** | `class Policy`/`PolicyRegistry`/`PolicyEngine` **各 1 处**；`_CORE_EVENT_TYPES` 定义 **1 处**；**4 适配器全部派生**。<br>⚠️ **但**：本轮的**全库** AST 扫描（谓词"≥3 个字符串常量且全体 ⊆ `SYNC_TYPES`"）**误报 2 处** —— `approval.py:222`（`for t in ("approval.granted", …)` = **总线订阅清单**）与 `compaction.py:441`（`elif t in ("approval.granted", …)` = **归一化 reducer 成员判定**）。二者**不是同步清单**，仅因 `approval.*` 恰属 `SYNC_TYPES` 而被误纳 | **post-S2 finding**（守卫精度，非真源问题） |
| **N6** | `authorize()` 是否提前出现 | **无 `def authorize`**；仅 3 处文档性提及（均声明"不在本步"）；`core/jobs._authorize` 与 `desktop/app._webhook_authorize` 是**无关的**作业归属/Webhook 鉴权 | 无 |
| **N7** | governance 是否**反向依赖**执行层 | 闭包 **NONE**；全库 `governance` 的消费者**只有 `engine.py`**（装配层 → 允许方向） | 无 |
| **N8** | 是否有**绕过 governance context** 的生产路径 | `ctx.governance.` **零属性读取**——治理层**尚未进入运行链**（S2-3 的裁定），故**不存在"绕过"**；运行期唯一强制点仍是 `tools_executor` 四关（`tools_executor.py` 全程 0 改动） | **by design**（S3 才接 `authorize()`） |
| **N9** | 是否有测试 mock **掩盖真实 runtime 行为** | 逐项评估见下 | 见下 |
| **N10** | S2 新增测试是否真正覆盖**生产路径** | ✅ **是**（关键项用真实装配） | 见下 |

### 6.1 N9/N10 细化：S2 测试是否覆盖真实路径

| 测试面 | 是否走真实代码 | 评估 |
|---|---|---|
| `test_engine.py`（T-A/T-B/T-C/T-D/F-1） | **走真实 `build_spine`**（`build_spine` 出现 **10 次**） | ✅ 覆盖真实装配：单链断言、两挂载路径、11 例 guard 行为、策略规则自带注入参数 |
| `test_governance_policy.py`（14→16 例） | **模块级单测**（`build_spine` 出现 **0 次**），用 `_rule()` 构造的 `PolicyRule` 替身 | ✅ **恰当**：它验的是 `Policy`/指纹/注册表/`emit_updated` 的**模块契约**；装配面由 `test_engine.py` 覆盖。**非掩盖** |
| `test_s24_engine_adapter_derives_sync_from_vocab` | 驱动**真实 `_record_to`** 闭包，仅以记录型 store 替身捕获 `sync=` | ✅ 被测对象（adapter 的派生逻辑）是**真的**；替身只替换边界（store） |
| `test_s24_desktop_adapter_derives_sync_from_vocab` | 用**未绑定方法**调用**真实** `_attach_persistence` | ✅ 覆盖真实 desktop 适配器代码路径 |
| `test_s25_scope_events_are_registered` 等 | 直接查运行时注册表 | ✅ 真实 |

**结论：S2 新增测试中，凡涉及"装配 / 适配器 / 事件边界"的关键断言均走真实生产代码；仅模块级契约（`Policy` 的指纹与治理动作）用替身，且装配面另有真实覆盖。未发现"替身掩盖真实 runtime"的情况。**

> **反例对照（历史教训）**：S1 曾发现 `FakeLoop.resume` 替身掩盖"生产类无该方法"（`test_agent.py:507/516`）——**S2 未出现同类问题**。

---

## 7. `F-SYNC-1` 专项审查（只审不修）

| 项 | 内容 |
|---|---|
| **复现条件** | 强同步事件（`SYNC_TYPES` 成员）在**总线→store 适配器内** flush 失败（磁盘 `OSError`）；即 `store.append(payload, sync=True)` → `_flush_pending_all()` 抛错 |
| **本轮独立复现** | ✅ **成功复现**（2026-09-14 重跑）：`append` **无异常** / `_pending` = **0** / `_retry_q` = **0** / 落盘文件 **0 字节** |
| **机理** | ① `_flush_pending_all`（`persistence.py:346-366`）的失败语义 = **丢弃本次 pending 行**、不入 `_retry_q`；② 适配器是**总线订阅者**，异常被 `event_bus.py:279` 的 `except Exception` **隔离为 EVT-103**（不外抛）；③ `session.append` 步骤 9 的 `_flush(seq)` 面对**空 pending** → 静默返回成功 |
| **影响范围** | **12 个**强同步事件 × **4 条**适配器路径（engine / desktop / cli / orchestration，均传 `sync=True`） |
| **留痕（非完全静默）** | 总线会记一条 **EVT-103 结构化错误日志**；但**无错误码上抛、无重试、无恢复路径** |
| **是否已进正式 findings** | ⚠️ **部分**：已登记于 `S2-4.1_CHANGE_REPORT.md` §4（**P1**）；但**尚未进 `docs/KEY-FINDINGS.md`**（当时不在允许范围） |
| **是否会阻塞 S2 COMPLETE** | **否** —— 四条理由见下 |
| **为什么可以延期到 durability 阶段** | 见下四条 |

### 7.1 不阻塞 S2 的四条理由（必须明确给出）

1. **S2 前既存，非 S2 引入**：该缺口作用于**全部 11 个既有强同步事件**，在 S2 开工前就已存在（cli/orchestration 适配器同样传 `sync=True`）。S2-4.1 的 Y-1(a) 只是把 `policy.updated` **纳入同一行为**（对齐，非新造）。
2. **当前零生产影响**：治理层**尚未进入运行链**（`ctx.governance` 零读取、`authorize()` 未实现），`policy.updated` **尚无生产发射路径**；既有的 11 个事件路径在 S2 期间**未被改动**（`tools_executor.py` / `session.py` 全程 0 改动）。
3. **人工已明确裁定出 S2 范围**：S2-4 裁定"**不要在 S2-4 修复**；登记为独立 P1；**后续单独进入 durability/reliability 评估**"，且**明令**不改 `persistence.py` / `session.py` / `event_bus.py`。
4. **属"持久化耐久性"而非"治理层"命题**：S2 的目标是"治理层骨架 + 事件/同步真源收敛"；`F-SYNC-1` 是 **store 写失败时的丢弃语义 + 总线异常隔离** 的组合，归 **durability / reliability**。且它**已有明确归属、级别（P1）、与测试计划（T5 marker 红测试）**，不是"无人认领的欠债"。

> **结论**：`F-SYNC-1` = **post-S2 finding（P1，已登记，已裁定延期）**，**不构成 S2 blocker**。

---

## 8. 测试与 Git 状态（准确数字）

| 项 | 实测 |
|---|---|
| 全量 pytest（可运行集） | **1485 collected / 1483 passed / 2 skipped / 0 failed**，exit 0 |
| skip 明细 | `test_spill.py::TestEnter::test_mode_600` · `TestRead::test_symlink_escape_zero_read`（**既有平台相关**，与 S2 无关） |
| S2 相关四个文件 | `test_governance_policy.py` · `test_engine.py` · `test_events.py` · `test_persistence.py` → **99 用例全绿** |
| 未执行面 | `desktop_native/**`（缺 `PySide6`，2 文件 / 5 用例）—— **基线遗留 R-7** |
| `git status` | **clean**（无 M / 无 ??) |
| `git diff` | 空 |
| HEAD | `576b038` |
| 最近 commit | `576b038`(S2-5.1 docs) · `d6b4179`(S2-5.1 fix) · `806a82e`(S2-4.1 docs) · `95aef3e`(S2-4.1 refactor) |
| 未 push | `ahead 12` |
| 异常产物 | 无 |

**未为了让测试通过而修改任何代码**（本轮为只读审查，工作区自 `576b038` 起零改动）。

### 8.1 S2 的 12 个 commit（6 组两段式）

| 组 | feat/refactor | docs |
|---|---|---|
| S2-1 | `e23484a` | `327a87b` |
| S2-2.1 | `c84f249` | `a6d097c` |
| S2-3.2 | `6825c6c` | `0cb841d` |
| S2-4.1 | `95aef3e` | `806a82e` |
| S2-5.1 | `d6b4179` | `576b038` |
| （ADR-020 并入 S2-1 docs） | — | `327a87b` |

---

## 9. S2 判定与理由

# S2 = **COMPLETE**

| 支撑 | 说明 |
|---|---|
| M2 退出判据 ①②③ | **全部满足**，且均由**回到真实代码 + 实跑测试**复算（§4.4） |
| M2 必做项（Policy 一等对象 + 内容哈希指纹 + `policy.updated` 落盘） | **全部满足**（ADR §3.1 M2 行） |
| ADR-013~020 符合性 | 逐条核验通过：授权/执行分离 ✅ · 事件双轨 ✅ · Checkpoint 未入 ✅ · Workflow 无 DAG ✅ · 目录与接口冻结 ✅ · S1 范围裁定 ✅ · 策略事件边界 ✅ |
| S2 对运行期执行面 | **零改动**（`tools_executor.py` / `session.py` 全程 0；`scope.py`/`approval.py` 仅注释） |
| 行为面 | **零变化**（AST docstring-strip 9/9 SAME，S2-5.1 实证） |
| 未关闭项 | 12 条，**全部为已裁定延期或既存**，**无一为 blocker**（§5 + §6 分级） |
| Negative audit | 10 项均无 blocker；仅 4 条 **post-S2 finding**（守卫精度 / 设计文档加注 / `F-SYNC-1` 归档 / `_invoke` 规格处置） |

> **S2 未被"为了收口而隐藏问题"**：本轮 negative audit 的 10 项搜索中，**唯一"新发现"是守卫谓词的 2 处误报**（§6-N5），且我**主动**把它写进报告而非略过；`F-SYNC-1` 也**未被算作 PASS**（§7）。

---

## 10. 建议的后续动作（不属 S2，供你裁定）

| # | 动作 | 优先级 | 说明 |
|---|---|---|---|
| **A-1** | **S2 checkpoint 提交**（S2-6 报告 + 若有 S2-6 设计文档） | — | 两段式惯例；本轮**未 commit**（遵指令） |
| **A-2** | 把 `F-SYNC-1` **转入 `docs/KEY-FINDINGS.md`** 长期归档 | 中 | 现载体为 `S2-4.1_CHANGE_REPORT.md` §4 |
| **A-3** | 为 `docs/KEY-FINDINGS.md` 之外的 3 条 post-S2 finding 建待办：① 守卫谓词精度（收窄为"仅扫适配器 + 显式白名单注释"或加"必须直接传给 `store.append` 的 `sync=` 形参"语义判定）；② `GOVERNED_AGENT_RUNTIME_DESIGN.md` §3.2 的 `from_config` 签名加注（ADR-020 已权威覆盖）；③ `_invoke` 的 spec 处置 | 低 | 均为文档/测试精度，非功能 |
| **A-4** | 启动 **durability / reliability 阶段**（含 `F-SYNC-1` 修复 + T5 marker 红测试） | 由你定 | 建议在 S3 之前或之后，取决于你对该缺口的风险偏好 |
| **A-5** | **S3**（Decision 对象 + 关 2 出口升格 + `authorize()` 落地） | 由你定 | S2 已完成，S3 前置（`policy.updated` 已注册、`ctx.governance` 已挂载）**已具备** |

**等待人工 checkpoint。**
