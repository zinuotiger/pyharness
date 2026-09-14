# S2-1_CHANGE_REPORT.md — S2-1 治理层骨架实施报告

> **阶段**：S2-1（S2 第一步：建 `governance/` 骨架；**暂不接装配**）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`5de8b10`（工作区 clean）
> **依据**：[S2-1_GOVERNANCE_SKELETON_DESIGN.md](S2-1_GOVERNANCE_SKELETON_DESIGN.md)（Δ-1~Δ-5/D5 已人工确认）· [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) ADR-013/015/018 · Q1~Q4 裁定
> **性质**：S2-1 实施。**未 commit**、未 push、**未接装配**、**未创建** S3~S5 模块。

---

## 0. 结论

# S2-1 = PASS

**新建 4 文件 + 追加 1 文件（纯追加 +63/−0）**；**14 个新用例全绿**；全量回归 **1466 collected / 1464 passed / 2 skipped / 0 failed**（较 S1 基线 1452/1450 **恰 +14，零回归**）。
**M2 判据 ①（指纹随内容变化）已由 T1/T2 直接证明。**

---

## 1. 文件变更

### 1.1 新建（4）

| 文件 | 行数（`wc -l` 实测） | 内容 |
|---|---|---|
| `pyharness/governance/__init__.py` | 28 | 包门面：再导出 `GovernanceContext` / `Policy` / `PolicyEngine` / `PolicyRegistry` / `PolicyRule` / `compute_fingerprint` / `POLICY_OPS` / `EVENT_POLICY_UPDATED` |
| `pyharness/governance/policy.py` | 403 | `POLICY_OPS` · `EVENT_POLICY_UPDATED` · `POLICY_UPDATED_FIELDS` · `compute_fingerprint` · `PolicyRule`(+`from_descriptor`) · `Policy` · `PolicyRegistry` · `PolicyEngine` |
| `pyharness/governance/context.py` | 45 | `GovernanceContext`（M2 形状：只挂 `policy`；4 个 `None` 占位**冻结形状**） |
| `tests/unit/test_governance_policy.py` | 286 | T1~T9（14 用例） |

### 1.2 修改（1）——**纯追加，零删除**

| 文件 | 变更 | 性质 |
|---|---|---|
| `pyharness/core/tools_guard.py` | **+63 / −0** | **仅追加**：`_RULE_POLICY_REFS`（rule_id→policy_refs **声明表**）· `RuleDescriptor`（frozen dataclass）· `describe_rules()`（调用既有 `build_builtin_chain` 做**只读投影**）· 3 个 `__all__` 条目 |

**零判定逻辑改动（已核验）**：`git diff` 新增/删除统计 = `+63 / −0`（唯一 `-` 行是 diff 头 `--- a/...`）。`g1–g7` 的 `g_*_match/check`、`_resolve_geometry`、`_BUILTIN_IDS`、`build_builtin_chain`、`GuardChain`、`from_config` **一行未动**（守 **B1** / G-3）。

### 1.3 明确未改动（边界核验）

```
git diff --name-only | grep -E "engine.py|events/|scope.py|desktop/"  →  空
```

| 未触碰 | 归属 |
|---|---|
| `pyharness/engine.py`（装配切换 + `ctx.governance` 挂载） | **S2-3** |
| `pyharness/events/{vocab,payload}.py`（`policy.updated` 注册） | **S2-2** |
| `pyharness/core/scope.py`（Q1 分工下的 `scope.updated` 面） | **S2-5** |
| `pyharness/desktop/sessions.py`（sync 清单收敛） | **S2-4** |
| `decision.py` / `receipt.py` / `evidence.py` / `audit.py` | **S3~S5**（已核验不存在） |

---

## 2. 契约增量落地对照

| # | 裁定内容 | 落地位置 | 证据 |
|---|---|---|---|
| **Δ-1** | `op` = `{add, enable, disable}`（去 `tighten`、加 `enable`） | `policy.py` `POLICY_OPS`；`emit_updated` op 白名单校验 | **T6**：`op="tighten"` → `CYC-999` 拒；`POLICY_OPS` 断言 |
| **Δ-2** | 移除 `with_tightened`；改 `with_rule_disabled/with_rule_enabled`，禁用必带 `config_ref` | `Policy.with_rule_disabled(rule_id, *, config_ref)` / `with_rule_enabled` | **T4**：空 `config_ref` → `CFG-601`；未知规则 → `CFG-601`；幂等 |
| **Δ-3** | INV-G6：放宽/禁用必须有 `policy.updated` + `config_ref` + 可审计；禁止静默 | `with_rule_disabled` 强制 `config_ref`；`emit_updated` 对 `op=disable` 二次校验；`forced` 不可禁 | **T4**（forced → `CFG-601`）；**T6**（disable 缺 config_ref → `CFG-601`） |
| **Δ-4** | `from_config` 注入式（`chain_factory` + `rules`）；治理层零 `core.*` import | `PolicyEngine.from_config(...)` 签名；`PolicyRule.from_descriptor` 鸭子读取 | **T3**（AST 扫描 import 图）；**T9**（`chain_factory` 注入生效、未注入 → `chain is None`） |
| **Δ-5** | M2 不声明 `authorize()` | `GovernanceContext` 无该方法 | **T9**（`not hasattr(g, "authorize")`） |
| **D5** | g1–g7 描述符由 `tools_guard.describe_rules()` 提供 | `tools_guard.describe_rules()` → `PolicyRule.from_descriptor` | **T8**（7 条、序 = `_BUILTIN_IDS`、`policy_refs`/`forced` 正确、check 行为不变）；**T9**（端到端接入） |
| **Q3** | `policy.updated` 强同步 | `emit_updated` 以 `sync=True` append | 见 §4 偏离 D-2（词表未注册，落盘路径待 S2-2 激活） |

### 2.1 本步新增的实现选择（已在设计 §计划中预告，此处登记为 Δ-6）

| # | 选择 | 理由 |
|---|---|---|
| **Δ-6** | **`config_ref` 经 `Envelope.trace` 携带**，不入 payload | 沿用既有「传输元数据走 `trace`」先例（`tools_guard.py:36-39` 的 `call_id`、`approval.py:36-40` 的 `channel`）；**不扩 payload 模型**。`POLICY_UPDATED_FIELDS` 因此保持设计 §2.3 的六字段不变 |

---

## 3. 测试变化

### 3.1 新增 14 用例（`tests/unit/test_governance_policy.py`）

| # | 用例 | 断言要点 | 对应 |
|---|---|---|---|
| **T1a** | `test_t1_fingerprint_stable_for_same_declarative_content` | 同声明式内容两次构造 → 指纹相同 | M2 判据 ① |
| **T1b** | `test_t1_fingerprint_changes_with_content` | `policy_id`/`version`/**求值序**/`rule_id`/`policy_refs`/`forced`/`disabled`/`params` 任一变 → 指纹变（8 项各验一次） | M2 判据 ① |
| **T2** | `test_t2_fingerprint_excludes_callables` | `match`/`check` **换实现**但声明式相同 → 指纹相同 | **R-A 直接防护** |
| **T3** | `test_t3_governance_import_direction` | AST 扫描 `governance/*.py` → 项目内 import 只在 `events`/`errors`/本包 | ADR-018:308 |
| **T4a** | `test_t4_disable_requires_config_ref` | 空/空白 `config_ref` → `CFG-601` | Δ-3 |
| **T4b** | `test_t4_forced_rule_cannot_be_disabled` | `forced` 规则 → `CFG-601` | Δ-3 / `FORCED_GUARDS` |
| **T4c** | `test_t4_unknown_rule_and_idempotence` | 未知规则 → `CFG-601`；重复禁用幂等；`enable` 对称回退 | Δ-2 |
| **T5** | `test_t5_registry_duplicate_and_order` | 重名 → `TLB-801`；登记序 = 求值序；**无** `unregister`/`remove`/`reorder`/`clear` | 设计 §3.2 单调性 |
| **T6a** | `test_t6_emit_updated_validates_op_and_config_ref` | `op="tighten"` → `CYC-999`；`op=disable` 缺 `config_ref` → `CFG-601` | Δ-1 / Δ-3 |
| **T6b** | `test_t6_emit_updated_degrades_when_unregistered` | 词表未注册 → 返回 `False` 不抛；`POLICY_OPS` 断言 | S2-2 前置姿态 |
| **T7** | `test_t7_callable_param_rejected` | params 含 callable → `CYC-999`；集合类参数**归一为有序**（防指纹失稳） | R-A 边界 |
| **T8** | `test_t8_describe_rules_projection` | 7 条、序 = `_BUILTIN_IDS`、`policy_refs` 正确、`forced` 对；`await check(...)` 行为不变（`web.fetch` 空 allowlist → `reject/POL-NET-1`） | D5 / B1 |
| **T9a** | `test_t9_engine_from_descriptors_and_injection` | 描述符装配可用；未注入 → `chain is None`；**同配置指纹相同**；cfg 禁用面进初始 `disabled`；`chain_factory` 收到 7 个注入参数、注入面变 → 指纹变 | Δ-4 / Q3 |
| **T9b** | `test_t9_governance_context_m2_shape` | `policy` 在岗；4 字段 `None`；**无 `authorize`** | Δ-5 / M2 形状 |

### 3.2 全量回归

| 指标 | S1 基线（`5de8b10`） | **S2-1 后** | 变化 |
|---|---|---|---|
| collected | 1452 | **1466** | **+14** |
| passed | 1450 | **1464** | **+14** |
| skipped | 2 | 2 | — |
| failed / error | 0 / 0 | **0 / 0** | — |
| 耗时 | 34.5 s | **31.9 s** | — |

**零回归**：`tools_guard.py` 虽被改动（安全主干），但纯追加 → 既有 1450 例全部保持通过；新增 14 例全绿。

**留证**：`tmp/baseline/junit_s2_1.xml` · `tmp/baseline/pytest_s2_1.log`（`tmp/` 已由 `.gitignore` 忽略）

---

## 4. 偏离登记（S2 报告须留痕项）

| # | 偏离 | 内容 | 依据 |
|---|---|---|---|
| **D-1** | **`authorize()` 未在 M2 声明** | `ADR-018` §契约冻结清单**明列** `authorize()`，但 M2 未声明——其返回类型 `Decision` 属 S3，M2 声明即半成品或引用不存在类型 | Δ-5（人工确认）。*S3 补齐时须同步接 `tools_executor.py:449`* |
| **D-2** | **`policy.updated` 尚未注册，落盘路径未激活** | `emit_updated` 现按仓库惯例**降级**（`False` + 日志）；Q3 的"强同步 + emission/persistence 一致"在 **S2-2 注册** + **S2-4 sync 收敛**后才真正成立 | S2-1 边界（不碰 `events/*`） |
| **D-3** | **`ctx.governance` 尚未挂载** | `GovernanceContext` 已定义但**未接到 `ctx`**——装配切换属 S2-3；故生产路径当前**不经过治理层**（零行为变更，也是零回归的原因） | S2-1 边界 |
| **D-4** | **`from_config` 签名与冻结设计 §3.2 不同** | 新增 `rules` / `chain_factory` 两个注入位（Δ-4）；该签名**不在 ADR-018 冻结清单内**，属设计精化 | Δ-4（人工确认） |
| **D-5** | **`op` 枚举与冻结设计不同** | 设计 §2.3 = `add\|tighten\|disable`；实现 = `add\|enable\|disable`（Δ-1） | Δ-1（人工确认） |
| **D-6** | **`config_ref` 走 `trace`** | 见 §2.1 Δ-6 | 既有先例 |

> **D-4/D-5 是"实现 ≠ 冻结设计文本"的实质差异**——若 Q1/Q2 的边界被确认为长期架构约束，应立 **ADR-020** 记录（当前按你的指示**暂缓**，S2-1 验证后再定）。

---

## 5. 风险与回退

### 5.1 风险

| 风险 | 等级 | 说明 |
|---|---|---|
| 治理层与 `tools_guard` 契约漂移 | 低 | `_RULE_POLICY_REFS` 是**声明表**，与 `g_*_check` 的返回字面量可能失同步。**T8 断言了 7 条映射的内容**，但不校验"check 实际返回什么"——彻底防漂移需在 S2-3 加"描述符 refs ⊇ 实际返回 refs"的断言（**建议列入 S2-3**） |
| `describe_rules()` 与 `from_config` 的注入面不一致 | 低 | 二者都接受同 5 个注入参数，但 `describe_rules` 只用于**描述**；若 S2-3 传入不同参数集，指纹会变（**T9 已覆盖"注入面变 → 指纹变"**） |
| 指纹稳定性被后续误改 | 低 | T2 已把"函数对象不入指纹"钉死；若有人往 params 塞 callable，T7 会拒 |
| 无行为变更（本步） | — | S2-1 **未接装配**，生产路径不经过治理层；全量回归零回归即为证据 |

### 5.2 回退

**本步改动完全可回退且不影响既有行为**：

1. **无需回退生产行为**——`engine.py` 等未改，生产路径未接治理层；
2. 回退动作 = 删除 `pyharness/governance/` 与 `tests/unit/test_governance_policy.py`（4 文件），并回退 `tools_guard.py` 的追加段（**+63/−0，纯删除即可**）；
3. `tools_guard.py` 的追加段**无既有代码引用**（`describe_rules`/`RuleDescriptor` 仅被新测试与未来 S2-3 使用）→ 删除零影响；
4. **无数据影响**：未动事件格式、未动落盘、未动 `session.append`。

---

## 6. 下一步（S2-2 起）

| 步 | 内容 | 出口判据 |
|---|---|---|
| **S2-2** | 注册 `policy.updated`：`events/payload.py` 新增 `PolicyUpdatedPayload`（**须与 `POLICY_UPDATED_FIELDS` 六字段一致**）+ `events/vocab.py` 具名 import + `_CORE_EVENT_TYPES` 行 + **进 `SYNC_TYPES`**（Q3）；校正 `vocab.py:176`/`EVENT-SCHEMA` 的规模注释（S0 的 R-F） | `policy.updated` `is_registered` = True 且 ∈ `SYNC_TYPES`；载荷校验（`extra="forbid"`）通过；词表 73→74 |
| **S2-3** | 装配切换：`engine.py` 的 `guard_from_config(...)` → `PolicyEngine.from_config(rules=tools_guard.describe_rules(...), chain_factory=tools_guard.from_config, ...)`；挂 `ctx.governance`；按职责拆 1~2 个子函数 | **一致性测试**（M2 判据 ②）：治理层判定与 `tools_guard` **逐例一致**；全量回归 |
| **S2-4** | sync 收敛：`engine.py` `_SYNC` + `desktop/sessions.py` 内联 tuple → 派生自 `SYNC_TYPES` | `policy.updated` 在两路径均 `sync=True` |
| **S2-5** | `scope.updated` 面（Q1 分工）：确认无重叠表达；对齐过期注释 | 相关单测零回归 |
| **S2-6** | 收敛报告 | M2 判据 ①②③ 全绿 |

**建议同时列入 S2-3**（承 §5.1 第一条）：加"描述符 `policy_refs` ⊇ 各 `g_*_check` 实际可能返回的 refs"的断言，把 D-4 的漂移风险封死。

---

## 7. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 未创建 `DecisionEngine` / `Decision` / `DecisionReceipt` / `Evidence` / `AuditSystem` | ✅ |
| 未创建 `decision.py` / `receipt.py` / `evidence.py` / `audit.py` | ✅（已核验四个路径均不存在） |
| 未修改 `tools_guard.py` 除 `describe_rules()` 只读接口外的任何内容 | ✅（`+63/−0`，且未触 `g_*_check`/`_BUILTIN_IDS`/`GuardChain`/`from_config`） |
| 未接装配（`ctx.governance` 未挂载） | ✅ |
| **未 commit、未 push** | ✅（工作区改动待人工确认后提交） |

**本步产物**：`pyharness/governance/{__init__,policy,context}.py` · `tests/unit/test_governance_policy.py` · `pyharness/core/tools_guard.py`（追加段）· 本报告 · [S2-1_GOVERNANCE_SKELETON_DESIGN.md](S2-1_GOVERNANCE_SKELETON_DESIGN.md)

**等待人工确认后提交（或先进入 S2-2）。**
