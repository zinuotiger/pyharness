# S2-1_EXIT_REVIEW.md — S2-1 出口审查

> **阶段**：S2-1 Exit Review（S2-1 骨架实施的独立核验）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`5de8b10`（工作区含 S2-1 改动，**未 commit**）
> **依据**：[S2-1_CHANGE_REPORT.md](S2-1_CHANGE_REPORT.md) · [S2-1_GOVERNANCE_SKELETON_DESIGN.md](S2-1_GOVERNANCE_SKELETON_DESIGN.md) · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md)（ADR-013/015/018）
> **性质**：**只审查**——未修改任何代码、未进入 S2-2、未 commit、**未创建 ADR-020**。
> **方法**：**不采信报告自述**；结论全部由运行期检查（新解释器导入闭包 / 直接 `import` 读值）、AST 静态提取、`git diff` 与 pytest 实跑独立复算。

---

## 0. 判决

# **PASS**

# **S2-2 READY**

**12 项检查全部通过**；3 项**非阻断发现**（§3）。其中三项关键结论**强于** S2-1 报告的自述：
- **⑧ 依赖方向**：运行期**传递闭包**（非仅语法）验证——import `governance` 后 `sys.modules` 只含 `{pyharness, errors, governance.*}`；触发惰性 `emit_updated` 后**只增加 `events.*`**；**全程无 `core`/`bus`**。
- **⑦ `_RULE_POLICY_REFS` 漂移**：AST 提取 7 条 `g_*_check` 的返回字面量 → 与声明表**逐条完全一致（零漂移）**。风险是**潜在**的（仅由未来改动触发），报告 §5.1 的措辞偏保守但不错。
- **⑥ `describe_rules()`**：`git diff` 为 **+63 / −0**（唯一 `-` 行是 diff 头）→ 纯追加，`g_*_check`/`_BUILTIN_IDS`/`GuardChain`/`from_config` **零改动**。

**测试事实（独立复跑）**：`1466 collected / 1464 passed / 2 skipped / 0 failed`，exit 0；其中 `test_governance_policy.py` = **14 例**；2 条 skip 为既有平台相关（`test_spill` 的 chmod 0600 与 symlink）。

---

## 1. 十二项检查（逐项证据）

| # | 检查项 | 结果 | 独立证据 |
|---|---|---|---|
| **1** | **Δ-1 ~ Δ-6 与代码一致** | ✅ **一致** | 见 §1.1 逐条 |
| **2** | **D-4 注入式签名满足 ADR-018 依赖方向** | ✅ **满足（运行期传递闭包验证）** | 新解释器 `import pyharness.governance` → `sys.modules` 的 `pyharness.*` = `{pyharness, errors, governance, governance.context, governance.policy}`；**VIOLATION: NONE**。触发 `emit_updated()` 的惰性 `from pyharness.events.vocab import is_registered` 后 → 仅增 `events.*`（`events, events.envelope, events.payload, events.vocab`）；**仍无 `core`/`bus`** |
| **3** | **D-5 `op={add,enable,disable}` 与 `scope.updated` 语义不重叠** | ✅ **不重叠** | `POLICY_OPS = frozenset({'add','disable','enable'})`（实测）；`scope.updated` **语义未变**——发射点仍为 `op:"tighten"`（`scope.py:304` `tighten()` / `:318` `note_tighten()`）。**`tighten` 只出现在 `scope.updated`，从未进入 `policy.updated`**（`emit_updated` 的白名单会以 `CYC-999` 拒之，T6 覆盖） |
| **4** | **`Policy.with_tightened()` 不存在且无遗留引用** | ✅ **代码层无残留**（文档层有 1 处，见 F-2） | `Policy` 方法集实测 = `{_derive, disabled, enabled_rules, fingerprint, rule_of, rules, with_rule_disabled, with_rule_enabled}`——**无 `with_tightened`**；全库 grep 仅命中 `policy.py:17`（Δt-2 的说明注释）、`S2-1_CHANGE_REPORT.md`、`S2-1_GOVERNANCE_SKELETON_DESIGN.md`，以及 **`GOVERNED_AGENT_RUNTIME_DESIGN.md:318`**（旧契约文本，见 F-2） |
| **5** | **`config_ref → Envelope.trace` 与既有机制一致** | ✅ **一致** | 既有先例两处同款：`tools_guard.py:801/817` 把 payload 模型不容的 `call_id` 经 `trace={"call_id": …}` 携带；`approval.py:280` 把 `channel` 经 `trace` 携带。本次 `policy.py:265` `trace = {"config_ref": …}` **同款**，且未扩 payload——`POLICY_UPDATED_FIELDS` 保持设计 §2.3 的六字段 |
| **6** | **`describe_rules()` 属只读投影** | ✅ **是** | `git diff --numstat` = **`63 / 0`**（纯追加）；AST 读出 `describe_rules` 唯一动作是 `build_builtin_chain(...)` + `tuple(RuleDescriptor(...))` 投影，**不构造 `GuardChain`、不写状态**；追加段不含对 `g_*_match/check`、`_resolve_geometry`、`_BUILTIN_IDS`、`GuardChain`、`from_config` 的任何修改 |
| **7** | **`_RULE_POLICY_REFS` 与各 `g*_check` 实际返回的漂移** | ✅ **零漂移**（AST 实证） | 对 `tools_guard.py` 做 AST 提取每个 `g_*_check` 的 `Return` 字符串字面量（含 g3 经 `_resolve_geometry`），与 `_RULE_POLICY_REFS` 比对：**7/7 完全一致，MISMATCH = NONE**。链级终局 refs（`GRD-401` @ :695-696/:814-815、`APR-501` @ :714）**不在规则声明面**，属 `GuardChain.evaluate` 的降级兜底，**符合预期** |
| **8** | **`governance/` 实际 import graph（含 TYPE_CHECKING / 间接）** | ✅ **无越界** | ① **运行期传递闭包**见 #2；② **AST 扫描** `governance/*.py` 的 import 节点（含函数内惰性 import）——项目内 import 只有 `pyharness.errors`、`pyharness.events.vocab`、`pyharness.governance.*`；③ **无 `TYPE_CHECKING` 变通**（全库 grep `TYPE_CHECKING` in `governance/` = 无命中）；④ import `governance` **甚至不加载 `events`**（惰性到调用点） |
| **9** | **M2 是否提前实现 Decision / Receipt / Evidence / Audit** | ✅ **未实现** | `grep -niE "class (Decision\|DecisionReceipt\|EvidenceCollector\|AuditSystem\|DecisionEngine)\|def authorize" pyharness/governance/` → **无命中**；`decision.py`/`receipt.py`/`evidence.py`/`audit.py` **均不存在**；`GovernanceContext` 只有 `None` 占位字段（形状冻结，无实现） |
| **10** | **`ctx.governance` 是否仍未接入生产路径** | ✅ **未接入** | `grep -n governance pyharness/engine.py` → **零命中**；`pyharness/governance` 不被 `engine.py`/`agent.py`/`core/*` 的任何模块 import（闭包证据）。**生产路径当前不经过治理层** |
| **11** | **是否有超出批准范围的修改** | ✅ **无** | `git status --short` = 1 处修改（`pyharness/core/tools_guard.py`）+ 4 处新增（`pyharness/governance/`、`tests/unit/test_governance_policy.py`、2 份 S2-1 文档）。**无** `engine.py`/`events/*`/`scope.py`/`desktop/*`/测试既有文件的改动。批准范围 = "governance 3 模块 + 1 测试 + `tools_guard.describe_rules()` 只读接口" → **完全吻合，且未越界** |
| **12** | **测试事实 1464 passed / 2 skipped** | ✅ **复现一致** | 独立复跑：`tests=1466 failures=0 errors=0 skipped=2 passed=1464`，exit 0；`governance_policy` 用例 = **14**；skip 明细 = `test_spill.py::TestEnter::test_mode_600`、`test_spill.py::TestRead::test_symlink_escape_zero_read`（**均为既有 Windows 平台相关跳过，与 S2-1 无关**）。相对 S1 基线 1452/1450 → **+14，零回归** |

### 1.1 Δ-1~Δ-6 与代码逐条比对

| Δ | 裁定内容 | 代码实测 | 一致 |
|---|---|---|---|
| **Δ-1** | `op` = `{add, enable, disable}`；`tighten` 归 `scope.updated` | `POLICY_OPS = {'add','disable','enable'}`；`emit_updated` 非白名单 → `CYC-999`（T6）；`scope.updated` 仍 `op:"tighten"` | ✅ |
| **Δ-2** | 移除 `with_tightened`；改 `with_rule_enabled` / `with_rule_disabled`，禁用必带 `config_ref` | `Policy` 方法集无 `with_tightened`；`with_rule_disabled(rule_id, *, config_ref)` 强制非空（T4a）；`with_rule_enabled` 对称（T4c） | ✅ |
| **Δ-3** | INV-G6：放宽/禁用须有 `policy.updated` + `config_ref` + 可审计，禁静默 | `with_rule_disabled` 检 `config_ref`；`forced` 不可禁 → `CFG-601`（T4b）；`emit_updated` 对 `op=disable` **二次校验** `config_ref`（T6a） | ✅ |
| **Δ-4** | `from_config` 注入式（`chain_factory` + `rules`）；零 `core.*` import | 签名实测 = `(cfg, session, bus, rules, params, chain_factory, validator, credential_paths, path_exists, link_resolver, approval_channel, policy_id, version)`；运行期闭包无 `core`（#2） | ✅ |
| **Δ-5** | M2 不声明 `authorize()` | `hasattr(GovernanceContext,'authorize') = False`；字段 = `['policy','decisions','receipts','evidence','audit']` | ✅ |
| **Δ-6** | `config_ref` 经 `Envelope.trace`，不入 payload | `policy.py:265` 构造 `trace={"config_ref": …}`；`POLICY_UPDATED_FIELDS` 仍六字段 | ✅ |

---

## 2. 重点问题（A~D）

### A. Δ-1~Δ-5 是否已从"临时实现选择"变为**长期架构决策**？

**分类回答——不是全部：**

| Δ | 性质 | 理由 |
|---|---|---|
| **Δ-1**（`op` 集 + 事件边界） | **长期架构决策** | 它编码的是 **Q1 裁定**（`policy.updated` 与 `scope.updated` 的分工）。任何后续改动都等于**重开 Q1 裁定**，且直接决定 S5 审计因果链是否会被两条重叠事件污染 |
| **Δ-2**（治理动作 + `config_ref`） | **长期架构决策** | 它是 **INV-G6 的机制面**；`config_ref` 强制是"放宽必须留痕"的结构保证，不是实现口味 |
| **Δ-3**（INV-G6 表述） | **长期架构决策** | 它就是**不变量本身**，会被 `tests/invariants/` 依赖（S6 的 M8） |
| **Δ-4**（注入式） | **派生约束，非新决策** | 它**不是**选择——ADR-018:308 已冻结"`governance/` 只允许依赖 `events`/`errors`"，注入是**该冻结规则的唯一可行实现**。值得记录（因为它改了设计 §3.2 的**签名文本**），但**不构成独立架构决策** |
| **Δ-5**（`authorize()` 延后） | **临时偏离，不是决策** | 它是**阶段边界**的产物（S3 会补齐并接 `tools_executor.py:449`）。**不应 ADR 化**——否则会把一个临时缺口固化为"决策" |

**结论**：**Δ-1 / Δ-2 / Δ-3 是长期架构决策；Δ-4 是 ADR-018 的派生实现（记录即可）；Δ-5 是临时偏离（登记即可）。**

### B. 是否应创建 **ADR-020**？

**应创建，但范围应限于 Δ-1 / Δ-2 / Δ-3（事件模型边界），并附带记录 Δ-4。**

理由：
1. **Δ-1/Δ-2/Δ-3 与冻结设计的文本直接冲突**：`GOVERNED_AGENT_RUNTIME_DESIGN.md` §2.3 写 `op(add|tighten|disable)`、§3.2 写 `Policy.with_tightened()`——**这两处现在都是错的**（见 F-2）。长期约束必须有不可变记录，否则后续读者会照旧文本实现。
2. **它们已被人工确认**（Q1/Q2 裁定 + Δ 逐条接受）**且已被 S2-1 验证**（14 例全绿）——正好满足你设定的"验证后再定"条件。
3. **不应包含 Δ-5**：临时偏离 ADR 化会制造伪决策。
4. **不应包含 Δ-4 作为独立决策**：它是 ADR-018 的推论；可作为 ADR-020 的**"关联/实现说明"**一段，记明"由 ADR-018:308 推导，改的是设计 §3.2 的签名文本"。

**时机建议**：**宜在 S2-2 落地前**——S2-2 会把 `policy.updated` 写进词表与 `SYNC_TYPES`，一旦落地，"事件边界"就已在代码中生效；先有不可变记录更稳。

### C. 若创建 ADR-020，它应记什么（边界纪律）

**应记**：① `policy.updated` 的语义边界 + `op ∈ {add, enable, disable}`（去 `tighten`）；② `scope.updated` **保留**运行时 scope 收紧语义；③ **不新增 `guard.disabled`**，guard 关闭统一经 `policy.updated op="disable"` 且必带 `config_ref`；④ **INV-G6** 表述；⑤ 声明**取代** `GOVERNED_AGENT_RUNTIME_DESIGN.md` §2.3 的 `op` 枚举与 §3.2 的 `with_tightened` **两处文本**（该文件非 FROZEN，可声明取代）；⑥ 关联段记 Δ-4 的来源（ADR-018:308）与 Δ-6（`config_ref`→`trace`）。

**不得做**：❌ 修改 ADR-013~019 任何文本 ❌ 修改 `ARCHITECTURE_DECISION_RECORD.md`（FROZEN）❌ 重述或"改进"ADR-018 ❌ 把 Δ-5 写入为决策 ❌ 引入未经人工确认的新范围。

> 本次**未创建 ADR-020**（遵你的指示）——上述为建议内容，供你裁量。

### D. 当前是否满足进入 S2-2 的条件？

**满足（S2-2 READY）。**

| S2-2 前置 | 状态 | 证据 |
|---|---|---|
| S2-1 完成且验证 | ✅ | 本审查 PASS |
| `policy.updated` 的**字段契约**已声明 | ✅ | `POLICY_UPDATED_FIELDS` 六字段（S2-2 的 payload 模型须与之一致） |
| **`op` 集**已定 | ✅ | `POLICY_OPS`（Δ-1；Q1 裁定） |
| **强同步**要求已定 | ✅ | Q3：进 `SYNC_TYPES` |
| 词表注册面已定位 | ✅ | `vocab.py:18` 具名 import 名单 + `_CORE_EVENT_TYPES` + `SYNC_TYPES` |
| sync 收敛的两处硬编码已定位 | ✅ | `engine.py:641` `_SYNC` · `desktop/sessions.py:151`（ADR-019 P-3 实测修正：**仅 2 处**） |
| 无阻塞性未决裁定 | ✅ | Q1~Q4 + Δ-1~Δ-5/D5 均已裁定 |

**唯一建议**（非阻塞）：**先立 ADR-020 再落 S2-2**（见 B 的时机）。

---

## 3. 非阻断发现

| # | 发现 | 级别 | 影响 | 建议 |
|---|---|---|---|---|
| **F-1** | `describe_rules()` 用 `_RULE_POLICY_REFS.get(g.id, ())`——**静默默认空元组**。当前 7 条**全部命中**（零漂移，已实证），但若未来向 `_BUILTIN_IDS` 新增内置 guard 而忘记补表，描述符会**静默给出空 `policy_refs`** | 中（潜在） | 仅在**未来**改动时触发；当前无影响 | S2-3 加断言：`_BUILTIN_IDS ⊆ _RULE_POLICY_REFS`（或用 `[]` 索引替代 `.get`，让缺失**响亮失败**） |
| **F-2** | `GOVERNED_AGENT_RUNTIME_DESIGN.md:318` **仍声明 `with_tightened()`**（Δ-2 已移除）；同文件 §2.3 的 `op(add\|tighten\|disable)` 亦已过时 | 低（文档） | 后续读者会照旧文本实现；与代码/裁定不一致 | ① 若立 ADR-020 → 由其在"取代关系"中声明；② 否则在 `with_tightened` 与 `op` 枚举处加**修订注**（同 S1-02 对 `MAP.md`/`DIS-CORE.md` 的做法） |
| **F-3** | **S2-1 报告 §5.1 把 `_RULE_POLICY_REFS` 漂移列为"可能失同步"风险**——本审查实证其**当前零漂移** | 极低（表述） | 无影响；报告措辞**偏保守**（安全方向） | 无需改动报告；本审查 §1 已补上实证结论 |

**另记（非发现，属设计使然）**：`D-2`（事件未注册 → `emit_updated` 降级返回 `False`）与 `D-3`（`ctx.governance` 未挂载）意味着 **S2-1 对生产行为零影响**——这既是"零回归"的原因，也提醒：**当前"治理层"尚未生效**，不可误读为已上线。

---

## 4. 边界确认（本次审查自身）

| 禁止项 | 状态 |
|---|---|
| 不修改任何代码 | ✅ 未修改（本次仅新增 `S2-1_EXIT_REVIEW.md`） |
| 不进入 S2-2 | ✅ 未进入 |
| 不 commit | ✅ 未提交 |
| **不创建 ADR-020** | ✅ 未创建（仅在 §2.B/C 给出建议内容） |

---

## 5. 最终判定

# **PASS**

**理由**：12 项检查全部通过——Δ-1~Δ-6 与代码**逐条一致**；**依赖方向经运行期传递闭包验证**（零 `core`/`bus`，无 `TYPE_CHECKING` 变通）；`op` 集与 `scope.updated` 语义**完全不重叠**；`with_tightened` 代码层**无残留**；`config_ref→trace` 与**既有两处先例同款**；`describe_rules()` 为 **+63/−0 纯追加只读投影**；**`_RULE_POLICY_REFS` 零漂移（AST 实证）**；M2 边界**未越界**；`ctx.governance` **确未接入生产路径**；无超范围改动；**1464 passed / 2 skipped 独立复现一致**。

3 项非阻断发现（F-1 潜在风险 / F-2 文档过时 / F-3 措辞保守）**均不构成门禁条件**，其中 F-1 已给出 S2-3 的具体断言建议。

# **S2-2 READY**

前置 7 项全满足（§2.D）。**唯一建议：先立 ADR-020（限 Δ-1/Δ-2/Δ-3 + 关联记 Δ-4）再落 S2-2**——但这是建议，不是进入条件。

---

**等待人工确认后提交 S2-1，或授权进入 S2-2（及是否创建 ADR-020）。**
