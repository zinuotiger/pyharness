# S2-1_GOVERNANCE_SKELETON_DESIGN.md — S2-1 治理层骨架设计

> **阶段**：S2-1（S2 第一步：建 `governance/` 骨架；**暂不接装配**）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`5de8b10`
> **依据**：[S2_ARCHITECTURE_READINESS_REVIEW.md](S2_ARCHITECTURE_READINESS_REVIEW.md) §6 S2-1 · [ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) ADR-013/015/018 · [GOVERNED_AGENT_RUNTIME_DESIGN.md](GOVERNED_AGENT_RUNTIME_DESIGN.md) §3.2
> **性质**：**只设计**——**未创建 `governance/`、未修改任何代码**。本文件是 S2-1 的落码前置。

---

## 0. 前置裁定记录（Q1~Q4，人工裁定 2026-09-14）

| # | 裁定 | 内容 |
|---|---|---|
| **Q1** | **方案 (b) 分工**（**接受**） | `policy.updated` = 治理层 **Policy 对象**变化事件（管理 `policy_id`/`version`/`fingerprint`，含 **enable/disable** 等治理动作）；`scope.updated` **保留现有语义**（仅表示运行时 scope 单调收紧或会话级范围变化）；**不替代** `policy.updated`；**禁止两个事件表达同一种策略变化** |
| **Q2** | **方案 (a) 并入**（**接受**） | **不新增 `guard.disabled`**；guard disable 统一经 `policy.updated` **`op="disable"`** 表达；关闭 guard **必须进入治理事件链** |
| **Q3** | **确认强同步** | `policy.updated` **进入 `SYNC_TYPES`**；须保证 **event emission + persistence sync 一致** |
| **Q4** | **确认编号** | **S2 阶段 = M2 交付物**；不使用 "S2-M1" 表述 |

> Q1~Q4 为 **S2 开工前置条件**，已满足。**裁定记录于本文件**；若需不可变归档，建议后续立 **ADR-020**（见 §6 R-1）。

---

## 1. 由裁定导出的 5 项契约增量（**落码前须确认**）

Q1 的 (b) 分工与 Q2 的 (a) 并入，**改动了冻结设计 §3.2/§2.3 的三处表述**，并暴露一处**依赖方向冲突**。以下 5 项必须在 S2-1 落码前定型——否则骨架会把错误契约固化。

### Δ-1｜`policy.updated.op` 取值集变更

| 项 | 内容 |
|---|---|
| 冻结设计 §2.3 / ADR-015 | `op` = **`add` \| `tighten` \| `disable`** |
| **Q1 裁定后** | `tighten` **归 `scope.updated`**（Q1 明确"scope.updated 仅表示运行时 scope 单调收紧"）；Q1 同时明确 `policy.updated` "包含 **enable**/disable 等治理动作" |
| **建议 M2 取值** | **`op` = `add` \| `enable` \| `disable`**（去 `tighten`、加 `enable`） |
| 依据 | Q1「禁止两个事件表达同一种策略变化」——若保留 `tighten`，`policy.updated` 与 `scope.updated` 将同时表达"收紧"，正是被禁止的重叠 |

### Δ-2｜`Policy.with_tightened()` 从 M2 契约移除

| 项 | 内容 |
|---|---|
| 冻结设计 §3.2 | `Policy` 含 **`with_tightened(added, reason) -> Policy`** |
| **冲突** | Q1 裁定"运行时 scope 单调收紧"归 `scope.updated` → **收紧不是 Policy 对象变化**，`with_tightened` 在 Q1(b) 下无对应语义 |
| **建议** | 从 M2 契约**移除** `with_tightened`；代之以 **治理动作** 方法：`with_rule_disabled(rule_id, *, config_ref)` / `with_rule_enabled(rule_id)` |
| 依据 | Δ-1 同理；且与既有 `GuardChain.disable(guard_id, *, config_ref)`（`tools_guard.py:748`，要求 config 显式声明）同构 |

### Δ-3｜INV-G6 表述需改述（"策略单调无 relax" → "禁用必须留痕"）

| 项 | 内容 |
|---|---|
| 现行表述 | `ADR-018` §契约冻结清单 / 我的 [S2 就绪评审](S2_ARCHITECTURE_READINESS_REVIEW.md) C5：**"策略单调（fingerprint 变化只体现新增/收紧），无 relax（INV-G5/G6）"** |
| **冲突** | Q1 明确允许 **enable/disable** → 存在受控的"放宽"动作，严格单调不成立 |
| **建议改述** | **INV-G6：策略放宽只能经显式治理动作（`op=disable`），且必须携带 `config_ref` 并落 `policy.updated`；不存在静默放宽的代码路径。** |
| 依据 | 该语义正是既有 `GuardChain.disable` 的设计（`FORCED_GUARDS` 恒在不可关 + 必须 config 声明），**不是新发明**，只是把它提为治理层不变量 |

### Δ-4｜`PolicyEngine.from_config()` 必须改为**注入式**（依赖方向冲突）

| 项 | 内容 |
|---|---|
| 冻结设计 §3.2 | `PolicyEngine.from_config(cfg, *, session, bus, validator, credential_paths, path_exists, link_resolver, approval_channel) -> PolicyEngine`——**隐含** `PolicyEngine` 自行调用 `tools_guard.from_config` 并自建 g1–g7 的 `PolicyRule` |
| **冲突（硬）** | `ARCHITECTURE_DECISION_RECORD.md:308`（ADR-018）：**"`governance/` 只允许依赖 `pyharness.events` 与 `pyharness.errors`"**。自行调用 `tools_guard` ⇒ 违反该冻结约束（也会把 g1–g7 函数对象带进治理包） |
| **建议解法** | **注入式装配**（与本仓库既有的 guard/executor/approval/scope 注入惯例一致）：<br>① **`chain_factory`**：由装配层（`engine.py`）传入 `tools_guard.from_config`，治理层只调用不可见其来源；<br>② **`rules`**：规则描述**数据**由装配层构造后传入（`PolicyRule` 只**持有** `match`/`check` 可调用对象，**不 import 其模块**）；<br>③ 治理层**零** `core.*` import（运行期与 import 期皆然） |
| 待定（D5） | g1–g7 → `PolicyRule` 的**描述符构造**落在哪里？候选：`tools_guard` 新增 `describe_rules()`（**只读描述**，不改 g1–g7 判定逻辑 → 不触 B1）；或 core 侧适配器。**建议前者**，因 `_BUILTIN_IDS` 求值序已在该模块内 |

> **Δ-4 是本轮最重要的增量**：它把 `from_config` 的**公开签名**（设计 §3.2）改为注入式。该签名不在 ADR-018 的冻结清单内（清单只冻 `ctx.governance`/`authorize()`/`decision_id`/`inputs_digest`/`verify()`/`Decision` 兼容面/`ApprovalChannel`/INV-G*），故**属设计精化，可由本阶段确认**。

### Δ-5｜`GovernanceContext.authorize()` 在 M2 是否声明

| 项 | 内容 |
|---|---|
| 冻结契约 | `ADR-018` §契约冻结清单明列 **`authorize()` = `tool_executor` 关 2 的唯一调用入口** |
| 冲突 | 但 `authorize()` 的返回类型是 **`Decision`**（S3 交付）——M2 声明它需引用尚不存在的类型；且 M2 无执行路径可接（关 2 出口升格是 S3） |
| 方案 | **(a) 声明为签名占位并 `raise NotImplementedError`**（fail-closed，误调即响亮失败）；**(b) M2 不声明，记录偏离，S3 补齐**（避免引用 S3 类型） |
| **建议** | **(b)** ——理由：`authorize()` 的契约（入参 `ToolCall`/`ctx`、出参 `Decision`、不变量 INV-G1）**整体属 S3**；在 M2 声明会是半成品，且`NotImplementedError` 的占位会在 S3 前成为"看起来可用"的诱因。**偏离需在 S2 报告登记** |

---

## 2. `governance/` 骨架契约（S2-1 落码目标）

> M2 只创建 **3 个模块**（ADR-018 冻结 6 模块中的 3 个）；`decision.py`/`receipt.py`/`evidence.py`/`audit.py` **属 S3~S5，M2 禁止创建**。

### 2.1 `governance/__init__.py`

- **职责**：包门面，仅再导出 `GovernanceContext` 与治理公开类型。
- **导出**：`GovernanceContext` · `Policy` · `PolicyEngine` · `PolicyRegistry` · `PolicyRule`
- **模块 docstring 必写**：① 治理层只授权不执行（G-4 / INV-G5）；② 依赖方向（Δ-4：零 `core.*` import）；③ M2 只含 policy/context（S3~S5 补齐其余）。

### 2.2 `governance/policy.py`

```python
# 契约（只写签名与语义,实现见 S2-1 落码）
@dataclass(frozen=True)
class PolicyRule:
    """单条策略规则。`match`/`check` 由装配层注入(治理层不 import 其来源)。"""
    rule_id: str                        # "g-schema" / "g-fs-path" / 插件 rule id
    policy_refs: tuple[str, ...]        # ("POL-FS-1","POL-FS-2","POL-FS-3")
    match: Callable[..., bool]          # 只持有;不参与指纹(Δ-4/R-A)
    check: Callable[..., Awaitable[tuple[str, str | None]]]
    forced: bool = False                # 恒在不可关(对应 FORCED_GUARDS)

@dataclass(frozen=True)
class Policy:
    """策略集一等对象。指纹 = 声明式内容哈希(不含函数对象)。"""
    policy_id: str                      # "builtin:v1"
    version: str                        # 语义版本
    rules: tuple[PolicyRule, ...]       # ★ 元组序 = 求值序(对齐 _BUILTIN_IDS)
    disabled: frozenset[str] = frozenset()   # 已禁用 rule_id(治理动作留痕面)
    params: Mapping[str, Any] = field(default_factory=dict)  # 仅声明式值(禁 callable)
    fingerprint: str = ""               # 由 compute_fingerprint 填(S2-1 构造即算)

    def enabled_rules(self) -> tuple[PolicyRule, ...]: ...
    def with_rule_disabled(self, rule_id: str, *, config_ref: str) -> "Policy": ...   # Δ-2
    def with_rule_enabled(self, rule_id: str) -> "Policy": ...                        # Δ-2

def compute_fingerprint(*, policy_id, version, rules, disabled, params) -> str:
    """policy.updated 的指纹:sha256(规范化 JSON)。
    ★ 只含声明式内容:policy_id/version/元组序 rule_id/policy_refs/forced/disabled/params。
    ★ 排除 match/check(函数对象)——否则指纹每次装配都变(违反 M2 判据 ①)。
    ★ params 含 callable → 构造期即拒(防指纹失稳,边界校验)。
    """

class PolicyRegistry:
    """规则注册表。插件规则只增链尾(延续 register_plugin_guard 单调性)。"""
    def register_rule(self, rule: PolicyRule) -> None: ...   # 重名 → TLB-801
    def rules(self) -> tuple[PolicyRule, ...]: ...
    async def emit_updated(self, policy: Policy, op: str, *, reason: str,
                           config_ref: str | None = None) -> None: ...
    # op ∈ {"add","enable","disable"}(Δ-1);未注册词表时按仓库惯例降级(见 §3.2)

class PolicyEngine:
    """策略的装配、解析、版本治理。只提供策略,不做决策。"""
    @classmethod
    def from_config(cls, cfg, *, session, bus,
                    rules: Iterable[PolicyRule] = (),
                    params: Mapping[str, Any] | None = None,
                    chain_factory: Callable | None = None,       # Δ-4:装配层注入
                    validator=None, credential_paths=None, path_exists=None,
                    link_resolver=None, approval_channel=None,
                    policy_id: str = "builtin:v1", version: str = "1.0.0") -> "PolicyEngine": ...

    def resolve(self, call, scope) -> Policy: ...      # M2: 返回 current(纯只读)
    def current(self, scope) -> Policy: ...            # 会话当前绑定策略
    def fingerprint(self, policy: Policy | None = None) -> str: ...
    def registry(self) -> PolicyRegistry: ...
    @property
    def chain(self): ...                               # 执行对象;chain_factory=None → None
```

### 2.3 `governance/context.py`

```python
@dataclass
class GovernanceContext:
    """治理层单实例(M2 形状;ADR-018 冻结目录)。"""
    policy: PolicyEngine
    # S3~S5 补齐(ADR-018 目录冻结;M2 不实现,避免半成品 —— Δ-5):
    decisions: Any = None      # S3: DecisionEngine
    receipts: Any = None       # S4: ReceiptStore
    evidence: Any = None       # S5: EvidenceCollector
    audit: Any = None          # S5: AuditSystem
    # authorize(): 按 Δ-5(b) M2 不声明;S3 补齐(tool_executor 关 2 唯一入口)
```

---

## 3. 两条实现纪律（S2-1 必须内建）

### 3.1 依赖方向（Δ-4）——零 `core.*` 运行期 import

| 规则 | 判据 |
|---|---|
| `governance/**` 的 import 只允许 `pyharness.events` / `pyharness.errors` / 标准库 / 本包内 | **须有自动断言**（见 §4 T3） |
| 类型标注若需引用 `ToolCall`/`Scope` | 用 `Any`（不 import）——**不得**用 `TYPE_CHECKING` 变通（保留 import 即保留方向） |
| 外来的 `match`/`check` 可调用对象 | **只持有**，不探测其模块/来源 |

### 3.2 事件发射的降级纪律（S2-1 尚未注册 `policy.updated`）

`policy.updated` 的词表注册属 **S2-2**。S2-1 的 `emit_updated` 必须**沿用仓库既有惯例**（`approval._emit_trust`、`tools_guard._record` 同款）：**未注册 → 记日志降级、不抛**，使 S2-1 可独立测试；S2-2 注册后同路径自动生效，**不需改调用方**。

> 这条同时兑现 Q3 的"**event emission + persistence sync 一致**"：注册进 `SYNC_TYPES`（S2-2）后，`session.append` 的强同步判定与 S2-4 的两处 sync 清单收敛共同保证一致。

---

## 4. 测试计划（S2-1 出口判据）

| # | 用例 | 断言 | 对应判据 |
|---|---|---|---|
| **T1** | 指纹稳定性 | 同一配置两次装配 → **指纹相同**；改 `rule_id`/求值序/`policy_refs`/`disabled`/`params` 任一 → **指纹变化** | M2 判据 ①（R-A 的直接防护） |
| **T2** | 指纹不含函数对象 | 传入 `match`/`check` 不同实现但声明式内容相同 → **指纹相同** | R-A |
| **T3** | **依赖方向自检** | 解析 `governance/**` 的 import 图 → **只出现 `events`/`errors`/标准库/本包**；断言无 `pyharness.core` | Δ-4 / ADR-018:308（INV-08 扩展） |
| **T4** | 治理动作留痕语义 | `with_rule_disabled` 必须给 `config_ref`（缺 → 拒）；`forced=True` 的规则**不可禁用** | Δ-2/Δ-3（对齐 `FORCED_GUARDS` + `CFG-601`） |
| **T5** | 注册表重名 | `register_rule` 同 `rule_id` → **TLB-801** | 设计 §3.2 |
| **T6** | `emit_updated` 未注册时降级 | 词表未注册 → 不抛、记日志；`op` 非法值 → 拒 | §3.2；Δ-1 |
| **T7** | `params` 含 callable → 拒 | 构造期即 `raise`（防指纹失稳） | R-A 边界 |

**S2-1 不测**（属后续步）：`policy.updated` 落盘（S2-2）· 装配切换一致性（S2-3）· sync 收敛（S2-4）。

---

## 5. 文件清单与不变量

### 5.1 S2-1 落码清单（4 个文件）

| 文件 | 动作 |
|---|---|
| `pyharness/governance/__init__.py` | **新建**（门面） |
| `pyharness/governance/policy.py` | **新建**（`PolicyRule`/`Policy`/`compute_fingerprint`/`PolicyRegistry`/`PolicyEngine`） |
| `pyharness/governance/context.py` | **新建**（`GovernanceContext`，M2 形状） |
| `tests/unit/test_governance_policy.py` | **新建**（T1~T7） |

**不改任何既有文件**（S2-1 明确定义为"暂不接装配"；`engine.py`/`events/*`/`tools_guard.py` 的改动属 S2-2~S2-4）。

### 5.2 S2-1 遵守的不变量

| 不变量 | 本步如何满足 |
|---|---|
| **INV-G5**（治理层无执行/放行 API） | 骨架不含任何 execute/allow/bypass 方法；T3 只校验方向，方法面由 S2-6 补专项 |
| **INV-G6**（改述后） | `with_rule_disabled` 强制 `config_ref`；`forced` 不可禁（T4） |
| **INV-08 扩展** | 零 `core.*` import（T3） |
| **B1**（g1–g7 判定逻辑冻结） | 治理层只**持有**规则可调用对象，**不改不重写**；D5 的 `describe_rules()` 若采纳亦为**只读描述** |
| **M2 边界** | 不创建 `decision.py`/`receipt.py`/`evidence.py`/`audit.py`；不实现 `DecisionEngine`/`DecisionReceipt`/`Evidence`/`AuditSystem` |

---

## 6. 待确认清单与下一步

| # | 待确认 | 建议值 | 若不同意的影响 |
|---|---|---|---|
| **Δ-1** | `policy.updated.op` 取值集 | **`{add, enable, disable}`**（去 `tighten`、加 `enable`） | 改枚举常量与 T6 断言 |
| **Δ-2** | `Policy.with_tightened` 是否移除 | **移除** ；改用 `with_rule_disabled`/`with_rule_enabled` | 保留则需明确其语义归属（会与 Q1 分工冲突） |
| **Δ-3** | INV-G6 改述 | **"放宽只经显式治理动作 + 必须 `config_ref` 留痕；无静默放宽"** | 不改述则与 Q1 的 enable/disable 自相矛盾 |
| **Δ-4** | `from_config` 改注入式（`chain_factory` + `rules`） | **采纳**（ADR-018:308 硬约束） | 不采纳则治理层必须 import `tools_guard` → 违反冻结方向 |
| **Δ-5** | `authorize()` 在 M2 是否声明 | **不声明**，偏离登记，S3 补齐 | 声明则为半成品或需引用 S3 类型 |
| **D5** | g1–g7 → `PolicyRule` 描述符构造落点 | **`tools_guard.describe_rules()`**（只读，不改判定逻辑） | 落别处需在 S2-3 说明 |

### 下一步

1. **你确认 Δ-1~Δ-5 与 D5**（一次性确认即可，无需代码改动）；
2. 我按 §2/§5 落地 **S2-1 代码 + T1~T7 测试**，并跑通、出 **S2-1 报告**（含偏离登记）；
3. 期间**不触碰** `engine.py`/`events/*`/`tools_guard.py`（S2-2 起才动），**不创建** S3~S5 的四个模块。

### 建议（非阻塞）

| # | 建议 | 理由 |
|---|---|---|
| **R-1** | 为 Q1~Q4 立 **ADR-020**（或并入 S2 报告并注明"待 ADR 化"） | Q1/Q2 永久约束事件模型边界（`policy.updated` vs `scope.updated`、guard disable 的归属），属不可变架构决策；ADR-019 已为此类裁定立下先例 |
| **R-2** | S2 报告登记 **Δ-5 的偏离**（`authorize()` 延至 S3） | 冻结清单含 `authorize()`，S2 不实现须留痕（同 S1 的偏离登记惯例） |

---

**本文件为 S2-1 的设计交付物。未创建 `governance/`、未修改代码。确认 Δ-1~Δ-5 与 D5 后即可落码。**
