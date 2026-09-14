# S2_ARCHITECTURE_READINESS_REVIEW.md — S2 开工就绪评审

> **阶段**：S2 Architecture Readiness Review（S2 开工前的架构就绪评审）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`5de8b10`（工作区 clean，未 push）
> **依据**：[GOVERNED_AGENT_RUNTIME_DESIGN.md](GOVERNED_AGENT_RUNTIME_DESIGN.md) · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) · [REFACTOR_PLAN.md](REFACTOR_PLAN.md) · `docs/baseline/*` · [ADR-019-s1-scope-adjudication.md](ADR-019-s1-scope-adjudication.md)
> **性质**：**只评审**——未创建 `pyharness/governance/`、未修改任何代码、未进入 S2。

---

## 0. 结论

### 0.1 编号更正（先纠一处范围歧义）

任务书写"**S2-M1**"。**冻结记录中不存在该编号**（`grep "S2-M1"` 全库无命中）。正确映射为：

| 冻结记录口径 | 内容 |
|---|---|
| **S2**（阶段，`ARCHITECTURE_DECISION_RECORD.md:412`） | 治理骨架 + Policy |
| **M2**（交付物，`:347`） | Policy 一等对象 + 内容哈希指纹 |

即 **S2 阶段 = M2 交付物**（"S2" 是开发顺序，`M2` 是 v1.0 必做项；两者一一对应，**没有 S2-M1**）。

> 本评审按 **S2（= M2）** 执行，并以 M1 已被 S1 交付完毕（`3d450cf`）为前提。**若你本意是别的范围，请在开工前指出**——这正是 S1 阶段 F-2 那类范围歧义的同类风险。

### 0.2 就绪判定

# READY = **NO（待 2 项事件模型裁定）**

**M2 的交付物本身就是"`policy.updated` 事件"，而该事件的模型边界有两处未决**（§1.1 Q1 / Q2）。二者均为**人工裁定型**问题（与 P-6 同类，**无需改代码即可解除**）。

**除该 2 项外**：架构风险已识别且可缓解（§1.2），M2 最小范围明确（§2），文件清单与边界清楚（§3/§4）。**裁定完成后即可开工。**

---

## 1. 遗漏的架构风险检查

### 1.1 阻断项（必须先裁定）

#### **Q1｜`policy.updated` 与 `scope.updated` 的关系未定**（设计 §附 决策点 5；ADR 未裁定）

**实测事实**：

| 事件 | 已注册 | 强同步 | 载荷 |
|---|---|---|---|
| `scope.updated` | **✅ True**（`vocab.py:255`） | False | `{op, added, reason}`（`payload.py:561`） |
| `budget.paused` | ✅ True | False | — |
| `policy.updated` | **❌ False** | （冻结设计定：**强同步**） | `{policy_id, version, fingerprint, op(add\|tighten\|disable), added, reason}` |

**冲突**：`policy.updated` 的载荷是 `scope.updated` 的**超集**（多出 `policy_id`/`version`/`fingerprint`，且 `op` 多两种取值），且 **`scope.updated` 已是注册且正在发射的事件**——发射点 `scope.py:304`（`tighten`）与 `:318`（`note_tighten`，engine 装配期预设档位用，`engine.py:279` 有既有修复注释）。

**为什么阻断 M2**：M2 的验收判据之一是"`policy.updated` 落盘"。若不定二者关系，S2 落地后会**同时存在两条策略审计事件**（策略收紧时 `scope.updated` 与 `policy.updated` 各发一次）→ 违反 ADR-018「`inputs_digest` 不得引入第二套」同款的**单一真源精神**，并直接污染 S5 的审计因果链。

**需裁定（三选一）**：
- **(a) 替代**：`policy.updated` 取代 `scope.updated` 的策略语义面；`scope.updated` 保留为兼容别名（发射点改为发 `policy.updated`，或双发一段时间）——注意 `scope.updated` **有既有测试**，改动面需评估；
- **(b) 分工**：`policy.updated` = 治理层策略集变更（装配/版本/指纹）；`scope.updated` = 会话级单调收紧（**保留原样**）。两者语义边界清晰、互不重叠；
- **(c) 合并**：把 `policy_id`/`version`/`fingerprint` **扩到 `scope.updated`**（payload 只增可选字段，`EVENT-SCHEMA` §7 规则 2 允许），**不新增 `policy.updated`**——但这与 ADR-018 冻结的「新增事件类型：`policy.updated`」**冲突**，须改 ADR 才能走。

#### **Q2｜`guard.disabled` 审计缺口（S1 的 R-1）在 S2 的归属未定**

**实测事实**：`guard.disabled` **未注册**（`is_registered` = False）→ `tools_guard._record` 尽力而为降级为**本地日志**，不落 JSONL。

**为什么与 M2 相关**：`PolicyEngine.from_config()` 会调用 `chain.disable()`（读 `cfg.security.guards.disabled`）；且冻结设计给 `policy.updated` 定义了 **`op=disable`**。因此 S2 天然是关闭该审计缺口的时机。

**需裁定（二选一）**：
- **(a) 并入**：`op=disable` 的 `policy.updated` 即"谁放松了安全"的审计事实（**不再单独注册 `guard.disabled`**，`tools_guard._record` 的降级保留）；
- **(b) 单独登记**：注册 `guard.disabled` 为独立事件，`policy.updated` 不承载 disable。

> 若采 (b)，则词表新增数为 2（`policy.updated` + `guard.disabled`），影响 §3 的文件清单与"新增事件类型"边界。

### 1.2 非阻断风险（S2 内处理，已识别）

| # | 风险 | 等级 | 依据 | 缓解 |
|---|---|---|---|---|
| **R-A** | **`Policy.fingerprint` 内容哈希不稳定** | **高** | 设计 §3.2 定义 `fingerprint` = 内容哈希；但 `PolicyRule.check` 是**函数对象**，若把 `repr`/`id` 纳入哈希，则每次装配指纹不同 → **直接违反 M2 验收判据 ①**（"fingerprint 随规则内容变化"会退化为"每次都变"） | **只哈希声明式内容**：`rule_id` + 求值序 + `policy_refs` + 构造期参数（凭据清单/域名表/沙箱档等**值**），**排除函数对象**。须有"同一配置两次装配 → 指纹相同"的测试 |
| **R-B** | 装配入口切换触及安全主干 | 中高 | ADR §5.2 S2 行标注"**是**（engine/guard 装配）" | 按 CORE-02 高影响信号 → **全量回归**；先建装配验收断言（设计 §5.1「装配拆分」行亦指出 `test_engine_flow.py` 仅 1 大用例） |
| **R-C** | **`policy.updated` 若为强同步，2 处硬编码 sync 清单必须同步**（否则治理事件在 engine/desktop 两条主路径退化为批量落盘 → 崩溃丢决策事件） | 中高 | [ADR-019 §2.3](ADR-019-s1-scope-adjudication.md)：实测**仅 2 处**硬编码（`engine.py:641` 的 `_SYNC`、`desktop/sessions.py:151` 的内联 tuple），另 3 处已从 `SYNC_TYPES` 派生 | S2 内**必做收纳**（≈4 行：两处改为 `SYNC_TYPES`），ADR-019 P-3 已列明"不得静默略过" |
| **R-D** | `ctx.governance` 单实例挂载点落进 ~185 行 god function `build_runner_components` | 中 | ADR-013 接缝 ② ；ADR-019 判"拆分 = S2 内顺手做" | S2 内按职责拆 1~2 个子函数（纯结构重构，行为不变），避免加剧 P-2 |
| **R-E** | 一致性测试（M2 判据 ②）需要同时驱动 guard 与 governance 的 harness | 中 | ADR §5.2 S2 行判据 ②："治理层对同一 `(tool,args,scope)` 的判定与 `tools_guard` 逐例一致" | 复用 `tests/unit/test_engine.py` 的 `build_spine` 真实装配模式（S1 已建）；注意 `GuardChain.evaluate` 是 **async** 且需真实 `Scope`（含 `workspace_root`） |
| **R-F** | 词表注释与实际不符（`vocab.py:176` 写"70 事件类型"/`EVENT-SCHEMA` 写 57；**实测 73**） | 低 | S0 基线实测 | S2 注册新事件时**顺带校正该注释**（不改判定逻辑） |
| **R-G** | `scope.py:41` 注释称"`budget.paused`/`scope.updated` 尚未入词表"——**实测两者均已注册**，属过期注释 | 低 | 运行时核验（`is_registered` 均 True） | 随 S2 一并对齐注释（**不是行为变更**） |

---

## 2. S2（= M2）的最小实现范围

### 2.1 范围内（Must）

| # | 内容 | 判据来源 |
|---|---|---|
| 1 | 新建 `pyharness/governance/` 的 **3 个模块**：`__init__.py` · `context.py` · `policy.py` | ADR §5.2 S2 行；ADR-018 §目录 |
| 2 | `Policy` / `PolicyRule` / `PolicyRegistry` / `PolicyEngine` 一等对象（内容哈希指纹） | 设计 §3.2；M2 |
| 3 | **`PolicyEngine.from_config()`** 接管 guard 链装配（engine 的 `guard_from_config(...)` 改由此入口） | ADR §5.2；设计 §3.2 |
| 4 | `policy.updated` 事件 **落盘**（含 `op` 三值） | M2 验收："`policy.updated` 落盘" |
| 5 | **一致性测试**：治理层判定与 `tools_guard` 逐例一致 | ADR §5.2 S2 判据 ② |
| 6 | `ctx.governance` **单实例**挂载 | ADR-013 接缝 ②；ADR-018 |
| 7 | 若 `policy.updated` 为强同步 → **2 处 sync 清单收敛** | ADR-019 P-3（R-C） |

### 2.2 范围外（Must-Not，属后续阶段）

| 不做 | 归属 |
|---|---|
| `decision.py`（`Decision` 对象 / `DecisionEngine` / `ApprovalChannel` Protocol） | **S3** |
| 关 2 出口形态升格（枚举 → 决策对象） | **S3** |
| `receipt.py` / `decision_id` 凭证 | **S4** |
| `evidence.py` / `audit.py` | **S5** |
| INV-G1~G6 专项不变量测试全套 | **S6**（M8）；但 S2 需至少覆盖 **INV-G5**（治理层无执行 API）与 **INV-G6**（策略单调）的轻量断言 |
| 端到端治理 Demo | **S7** |

### 2.3 M2 的"最小"边界线（重要）

> **M2 不改变任何工具调用的判定结果。**

`PolicyEngine` 在 M2 只是**策略的持有者与装配者**：它内部**复用** `tools_guard.from_config()` 构造的 `GuardChain` 作为**执行对象**（治理层不持有执行权，G-4），并把"规则集 + 求值序 + policy_refs + 构造参数"提为 `Policy` 对象、算出 `fingerprint`、在装配/收紧/禁用时发 `policy.updated`。

**判据 ③"全量绿"即为此边界提供证据**：若 M2 改变了判定结果，一致性测试（判据 ②）与全量回归会同时失败。这也是 M2 与 S3 的分界——**S3 才把关 2 的出口从枚举换成决策对象**。

---

## 3. S2 开始前必须确认的接口契约

### 3.1 需人工裁定（2 项，见 §1.1）

| # | 契约 | 选项 |
|---|---|---|
| **Q1** | `policy.updated` vs `scope.updated` | (a) 替代 / (b) 分工 / (c) 合并（须改 ADR） |
| **Q2** | `guard.disabled` 审计缺口归属 | (a) 并入 `policy.updated op=disable` / (b) 单独登记 |

### 3.2 已在冻结记录中确定、S2 须严格遵守的契约

| # | 契约 | 出处 |
|---|---|---|
| C1 | **治理层目录与 6 模块冻结**；S2 只创建其中 3 个 | ADR-018 |
| C2 | **`ctx.governance` 单实例**（禁止向 `Ctx` 追加治理散字段） | ADR-018；ADR-013 接缝 ② |
| C3 | **依赖方向**：`governance/` **只允许** import `pyharness.events` 与 `pyharness.errors`；**禁止** import `tools_executor` / `llm` / `bus.plugin` | ADR-018（INV-08 扩展） |
| C4 | 治理层**无执行/放行 API**（INV-G5） | ADR-013 红线 |
| C5 | 策略**单调**：`with_tightened` 只增不改，无 relax（INV-G6 / G-5） | 设计 §3.2；ADR-013 |
| C6 | **规则实现冻结**：g1–g7 判定逻辑**不得重写**，只被引用 | G-3；ADR-013 |
| C7 | **唯一强制点不变**：四关管道结构/顺序不动 | G-4；ADR-013 |
| C8 | `PolicyEngine.from_config()` 签名须接受 `validator` / `credential_paths` / `path_exists` / `link_resolver` / `approval_channel`（S1 已为 `tools_guard.from_config` 补齐同款注入位） | 设计 §3.2；S1 交付 |
| C9 | 事件词表**只增不改**；payload 只加**可选**字段 | `EVENT-SCHEMA` §7 规则 1/2 |
| C10 | `decision_id` / `inputs_digest` / `verify()` 等**S3+ 契约**在 M2 **不得提前实现**（避免半成品） | ADR-018 + S2 范围 |

### 3.3 需 S2 内明确的实现细节（不需人工裁定，但须写进 S2 报告）

| # | 细节 | 建议 |
|---|---|---|
| D1 | `GovernanceContext` 在 M2 的形状 | **只挂 `policy`**；`decisions`/`receipts`/`evidence`/`audit` 字段以 `None` + 注释"S3~S5 补齐"。**不预造空类** |
| D2 | `GuardChain` 在 M2 的定位 | 保持现状（执行对象）；**S3** 才把 `evaluate` 编排搬入 `DecisionEngine`，`GuardChain` 转薄门面（设计 §2.1） |
| D3 | fingerprint 的哈希输入 | **只含声明式内容**（见 R-A） |
| D4 | `policy.updated` 的发射时机 | 装配期（`op=add`）/ 会话收紧（`op=tighten`）/ 禁用（`op=disable`）——须与 Q1/Q2 的裁定一致 |

---

## 4. 第一批修改文件列表

> 口径：**M2 的全部改动**（"第一批"= M2 内一次性交付；S3 才展开第二片）。共 **5 改 + 3 新 + 3 测试**（若 Q2 选 (b)，`events/*` 多一个事件类型，但不增文件）。

### 4.1 新建（3）

| 文件 | 内容 |
|---|---|
| `pyharness/governance/__init__.py` | 包门面：仅再导出 `GovernanceContext` 与公开类型（ADR-018） |
| `pyharness/governance/policy.py` | `PolicyRule` / `Policy` / `PolicyRegistry` / `PolicyEngine`（含 `from_config` / `resolve` / `current` / `fingerprint` / `registry`） |
| `pyharness/governance/context.py` | `GovernanceContext`（M2 只挂 `policy`；`authorize()` 入口**在 S3 才接**，M2 可仅留占位或暂不定义——见 D1） |

### 4.2 修改（5）

| 文件 | 改动 | 依据 |
|---|---|---|
| `pyharness/engine.py` | ① `guard = guard_from_config(...)` → `policy_engine = PolicyEngine.from_config(...)`（内部仍经 `tools_guard.from_config` 构造执行对象）；② 挂 `ctx.governance`；③ 按职责拆 1~2 个装配子函数（R-D）；④ 若 `policy.updated` 强同步 → **`_SYNC` 改从 `SYNC_TYPES` 派生**（R-C） | ADR §5.2；ADR-013 接缝②；ADR-019 P-3 |
| `pyharness/events/payload.py` | 新增 `PolicyUpdatedPayload`（`__all__` 自动收录，无需改） | C9 |
| `pyharness/events/vocab.py` | ① `payload` 具名 import 增补（`vocab.py:18` 为显式名单）；② `_CORE_EVENT_TYPES` 增注册行；③ 若强同步 → 加 `SYNC_TYPES`；④ 校正 `:176` 的规模注释（R-F） | C9；R-C；R-F |
| `pyharness/core/scope.py` | 按 **Q1 裁定**处理 `scope.updated` 与 `policy.updated` 的关系（改发射点 / 保留分工 / 扩载荷）；对齐 `:41` 过期注释（R-G） | Q1；R-G |
| `pyharness/desktop/sessions.py` | 若 `policy.updated` 强同步 → `:151` 内联 tuple 改从 `SYNC_TYPES` 派生（R-C） | ADR-019 P-3 |

> **`pyharness/core/tools_guard.py` 是否改？** 视 `PolicyEngine.from_config` 的实现选择：若它**直接调用** `tools_guard.from_config` 则**无需修改**（推荐，最省回归面）；若要让 `from_config` 转为薄委托则需改。**默认：不改**，以最大化"零行为变更"。

### 4.3 测试（3）

| 文件 | 内容 | 判据 |
|---|---|---|
| `tests/unit/test_governance_policy.py`（新） | ① 指纹稳定性（同配置两次装配 → 同指纹；规则内容变 → 指纹变）② 指纹不含函数对象（R-A）③ `with_tightened` 单调无 relax ④ `PolicyRegistry` 重名 → TLB-801 | M2 判据 ①；INV-G6 |
| `tests/unit/test_engine.py`（改） | 一致性测试：同一 `(tool, args, scope)` 下，**治理层判定 ≡ `tools_guard` 判定**（逐例） | M2 判据 ② |
| `tests/unit/test_events.py`（改） | `policy.updated` 注册 + 载荷校验 + （若强同步）落盘强度 | M2 判据；C9 |

---

## 5. 不允许修改的边界

### 5.1 硬边界（改即违 ADR，须先改 ADR）

| # | 边界 | 依据 |
|---|---|---|
| **B1** | **`core/tools_guard.py` 的 g1–g7 判定逻辑**（`g_*_match/check`、`_resolve_geometry`、`_BUILTIN_IDS` 求值序） | G-3；ADR-013 |
| **B2** | **`core/tools_executor.py` 四关结构与顺序**（关1a/1b/2a/2b/2.5/3/4）；关 2 出口形态**不得**在 M2 升格 | G-4；S2 范围（关2 升格 = S3） |
| **B3** | **`core/approval.py`**（原位保留，S4 才动 `_grant_slots`） | ADR-013 §不搬迁 |
| **B4** | **`core/session.py` 的 `append` 契约与 `events/*` 五步校验链** | INV-01；ADR-018 |
| **B5** | **`pyharness/events/*` 的既有事件类型语义**（只增不改；payload 只加可选字段） | `EVENT-SCHEMA` §7 |
| **B6** | **`ARCHITECTURE_DECISION_RECORD.md`**（FROZEN，不可编辑） | 该文件 §0.1；ADR-019 裁定 5 |
| **B7** | **ADR-013~019 的历史内容**（只增不改，推翻须新建取代） | `docs/ADD.md:35` |
| **B8** | **治理层依赖方向**（只许 `events`/`errors`） | C3；ADR-018 |
| **B9** | **`Ctx` 不得追加治理散字段**（只 `ctx.governance` 单实例） | C2；ADR-018 |
| **B10** | **治理层不得持有执行/放行 API** | C4；INV-G5 |

### 5.2 范围边界（M2 不得越界实现）

| # | 不得实现 |
|---|---|
| **B11** | `decision.py` / `DecisionEngine` / `Verdict` / `Principal` / `ApprovalChannel` Protocol（→ S3） |
| **B12** | `receipt.py` / `DecisionReceipt` / `verify()` / `decision_id` 生成（→ S4） |
| **B13** | `evidence.py` / `audit.py` / `TraceabilityMatrix`（→ S5） |
| **B14** | 除 `policy.updated`（及 Q2 裁定后可能的 `guard.disabled`）之外的**任何新事件类型** |
| **B15** | EventBus 改动、`guard.evaluated` 语义改动、`INV-04` 路径改动 |
| **B16** | 策略外部化（YAML/插件）、DAG、Checkpoint、数字签名（ADR-016/017 + 设计 §4.2 已明确排除） |

---

## 6. S2（= M2）实施计划

> 前置：**Q1 + Q2 裁定完成**（§1.1）。以下为裁定后的执行顺序。

| 步 | 内容 | 出口判据 | 触及安全主干 |
|---|---|---|---|
| **S2-1** | **建 `governance/` 骨架**：`__init__.py` + `policy.py`（`Policy`/`PolicyRule`/`PolicyRegistry`/`PolicyEngine`）+ `context.py`（D1 形状）。**暂不接装配** | 单测 `test_governance_policy.py` 通过（指纹稳定性 + 单调 + 重名 TLB-801）；`governance/` import 方向自检通过（C3/B8） | 否（旁挂） |
| **S2-2** | **注册 `policy.updated`**：`payload.py` 新模型 + `vocab.py` 注册（+ `SYNC_TYPES` 视裁定） | `test_events.py`：注册可用、载荷校验、`extra="forbid"` 生效 | 否 |
| **S2-3** | **装配入口切换**：`engine.py` 改由 `PolicyEngine.from_config(...)` 装配；挂 `ctx.governance`；按职责拆子函数 | 一致性测试（判据 ②）逐例一致；**全量回归**（CORE-02） | **是** |
| **S2-4** | **sync 收敛**（若 `policy.updated` 强同步）：`engine.py:641` + `desktop/sessions.py:151` 改派生自 `SYNC_TYPES` | 新增断言：`policy.updated` 在两路径均 `sync=True` | 中 |
| **S2-5** | **对齐过期注释**（R-F/R-G）+ 若 Q1 裁定要求则改 `scope.py` 发射点 | 相关单测通过；`scope.updated` 兼容面（既有测试）零回归 | 中（视 Q1） |
| **S2-6** | **收敛与报告**：全量回归 + 一致性测试 + 指纹测试齐备；按 `PROTOCOL` §11 出 S2 报告 | **判据 ① ② ③ 全绿** | — |

**关键提示**：
- **S2-3 是本阶段唯一的"高风险步"**，与 S1-01 同类（装配入口切换）——按 CORE-02 走**全量回归**，且 S1 已建的 `test_engine.py` 真实装配断言可直接扩展复用。
- **不得把 S2-3 与 S3 的关 2 升格合并**（ADR §5.3 的"释放点"纪律虽点名 S3/S4，同理适用：**一次只动一类语义**）。
- **M2 完成后 `policy.updated` 应已落盘**，但**不产生任何 `decision.issued`**（那是 S3）——若 S2 结束时出现了 `decision.*` 事件，即越界。

---

## 7. 需你裁定的清单（开工前）

| # | 待裁定 | 选项 | 影响 |
|---|---|---|---|
| **Q1** | `policy.updated` vs `scope.updated` | (a) 替代 / (b) 分工（**建议**）/ (c) 合并（须改 ADR） | §3.1 D4、§4.2 `scope.py` 行 |
| **Q2** | `guard.disabled` 归属 | (a) 并入 `op=disable`（**建议**）/ (b) 单独登记 | 词表新增数 1 或 2 |
| **Q3** | `policy.updated` 是否强同步 | 依冻结设计 = **是** → 则 §6 S2-4 必做 | 是否触发 sync 收敛 |
| **Q4** | "S2-M1" 的实际所指 | 确认 = **S2（阶段）的 M2（交付物）** | 范围基线 |

**Q1 的建议理由**：(b) 分工使两者语义边界清晰（治理层**策略集/版本** vs 会话**单调收紧**）、`scope.updated` 的既有测试零回归、且不与 ADR-018 冻结的"新增 `policy.updated`"冲突——**代价最小且无 ADR 变更**。
**Q2 的建议理由**：(a) 并入可让 `policy.updated` 完整承载"谁放松了安全"，**顺带关闭 S1 的 R-1 缺口**，且不新增事件类型。

**裁定完成后即可按 §6 开工。在此之前不创建 `governance/`、不修改代码。**
