# S3-1_CHANGE_REPORT.md — 治理决策模型(Decision Model)实施报告

> **阶段**：S3-1（M3 的第一段：纯决策抽象;**不接运行时**）
> **日期**：2026-09-14 ｜ **基线 HEAD**：`a7bd902`（S2 checkpoint;worktree clean）
> **依据**：[ARCHITECTURE_DECISION_RECORD.md](ARCHITECTURE_DECISION_RECORD.md) §5.2 S3 行 · ADR-013 · ADR-015 · ADR-018 · `GOVERNED_AGENT_RUNTIME_DESIGN.md` §3.1/§3.3
> **性质**：S3-1 实施。**未进入 S3-2**、未接 runtime、未注册事件、**未修 F-SYNC-1**。

---

## 0. 结论

# S3-1 = PASS

**改动 3 文件（新增 2 + 修改 1）**；S3-1 专项 **26 passed**；全量回归 **1509 collected / 1509 passed / 2 skipped / 0 failed**（较 S2 checkpoint 基线 1483 **恰 +26，零回归**）。

**本阶段目标**不是"把 Decision 接入系统"，而是建立**经过测试、稳定、可兼容旧系统**的 Governance Decision 数据契约。

---

## 1. 目标与范围

**目标（做什么）**：把"治理决策"从**值枚举**升格为**一等对象**——一次决策携带主键、主体、结果、依据与输入摘要，供 S3-2 接线与 S4 凭证/审计复用。

**范围（只做什么）**：Decision 数据模型 · `Principal` · `Verdict` · `Decision` · `DecisionEngine` 纯决策逻辑 · 与旧 verdict/allow/reject 语义的等价性测试 · 必要类型与接口定义。

**明确不做（本阶段边界）**：

| 不做 | 归属 |
|---|---|
| 运行时接线（`ctx.governance.decisions` 挂载、关 2 出口升格） | **S3-2** |
| `decision.issued` 事件词表注册 | **S3-2** |
| `authorize()` 实现 | **S3-2**（`tools_executor` 关 2 唯一入口） |
| `receipt.py` / `evidence.py` / `audit.py` | **S4~S5** |
| F-SYNC-1 修复 | durability / reliability 阶段 |

---

## 2. 修改文件清单（唯一 3 个）

| 文件 | 动作 | 行数 / 增删 |
|---|---|---|
| `pyharness/governance/decision.py` | **新增** | 325 行 |
| `tests/unit/test_governance_decision.py` | **新增** | 284 行 |
| `pyharness/governance/__init__.py` | **修改（最小追加）** | **+7 / −2** |

`__init__.py` 的追加**仅限**：导出 `Decision` / `Verdict` / `Principal` 三项 + 文档订正（说明 S3-1 追加 decision 契约、Receipt/Evidence/Audit 仍不导出）。**未**导出任何范围外符号。

**禁止面零改动（实测）**：`persistence.py` · `core/*`（含 `tools_guard.py` / `tools_executor.py` / `approval.py` / `scope.py` / `session.py`） · `bus/*` · `events/*` · `engine.py` · `core/agent.py` · `governance/policy.py` · `governance/context.py` · 任意 `ADR-*` · `REFACTOR_PLAN.md` · `ARCHITECTURE_DECISION_RECORD.md`。

---

## 3. 契约组成

### 3.1 `Principal` / `PrincipalKind`

`PrincipalKind`(StrEnum) = `HUMAN` / `AGENT` / `TOOL` / `PLUGIN` / `SYSTEM`。
`Principal`(frozen dataclass) = `kind` / `id` / `channel`,取代裸 `by: str`。

**构造期不变量**：`kind=HUMAN` ⇒ `channel ∈ HUMAN_CHANNELS` 且 `id` 不得以 `llm:` / `tool:` / `plugin:` 开头(延续 S-2 假冒审批防线);非 HUMAN 若给 `channel` 亦须在白名单内。

### 3.2 legacy `by` 双向映射（实际代码证据，非文档推测）

`from_legacy_by()` / `to_legacy_by()` 的解析面**严格对齐现网真实取值**：

| `by` 格式 | 真实来源（代码） | → Principal |
|---|---|---|
| `"system"` | `core/approval.py:305,401,541,558,567` | `SYSTEM` |
| `"cli"` / `"web"` / `"acp"` / `"desktop"` | `cli.py:1460`（`by="cli"`）· `desktop/constants.py:11` | `HUMAN`,channel=该通道 |
| `"<channel>:<id>"` | `approval.py:747`（合法形态）;实测 `cli:alice` / `web:bob` / `cli:test` / `cli:claire` | `HUMAN`,channel + id |
| `"llm:\|tool:\|plugin:<id>"` | `approval.py:743`（非人类前缀明列） | `AGENT` / `TOOL` / `PLUGIN` |
| 非法自报（`alice` / `hacker` / `mallory` / `llm:` …） | `approval.py:741-751` 的 `_require_human` | **拒（APR-503）** |

**往返无损**：`to_legacy_by(from_legacy_by(x)) == x` 对上述全部合法格式成立（T3 参数化断言）。

### 3.3 `Verdict`

`Verdict`(StrEnum) = `ALLOW="allow"` / `REJECT="reject"` / `APPROVAL="approval"`。
**值域与旧 `tools_guard.Decision` 逐一对应**（T1 断言集合相等）。

### 3.4 `Decision`（frozen, `eq=False`）

字段：`decision_id` / `verdict` / `tool` / `policy_refs` / `guard_ids` / `policy_fingerprint` / `inputs_digest` / `principal` / `ts` / `approval_ref` / `receipt_id`。

- `__eq__`：Decision↔Decision 按**完整字段**；Decision↔`str`/`Verdict` 按 verdict 字面量；其余 `NotImplemented`。
- `__str__` → verdict 字面量;`is_terminal_reject` 属性。
- **不持有任何 callable**（相等/指纹语义不依赖函数对象身份）。

### 3.5 `EvaluationResult`（只读投影，非求值器）

`verdict` / `guard_ids` / `policy_refs` —— **`GuardChain.evaluate` 产出面的结构化投影**。S3-1 **不产生它**，只**消费**。它**不是**规则/策略求值系统，不得发展为第二求值真源。

### 3.6 `ApprovalChannel`（Protocol，仅声明）

`request()` / `grant_binding()` / `cancel_all()` 三个方法声明。由 `core/approval.py` **原位**实现（鸭子类型）。**S3-1 不连接 `approval.py`**（该文件未改动）。

### 3.7 `DecisionEngine`（纯）

```python
decide(evaluation, *, principal, call=None, policy=None, tool=None,
       inputs_digest="", approval_available=True, approval_verdict=None,
       prior=None, approval_ref=None) -> Decision
```

施加的**治理决策语义**：verdict 归一（值域外 → `CYC-999`）· 单调性（前序终局 REJECT 不可被批准放宽）· 无审批通道 ⇒ APPROVAL 降为 REJECT · `approval_verdict ∈ {denied,timeout}` ⇒ APPROVAL 作废为 REJECT · 装配 `decision_id`/`ts`。

**无 I/O、无事件、无执行、无 `authorize()`**。

---

## 4. 单一求值链（不重实现 g1–g7）

```
GuardChain.evaluate / PolicyRule.check     ← 规则求值唯一真源（tools_guard，未改动）
            │  产出
            ▼
      EvaluationResult                      ← 结构化投影（S3-1 只消费）
            │  输入
            ▼
      DecisionEngine.decide                 ← 只施加治理决策语义（装配 + 单调性 + 通道门）
            │  产出
            ▼
        Decision
```

**实测证据**：`decision.py` 内 `.check` / `.match` / `def evaluate` **零命中**（仅 docstring 引用真源名）。即 **`GuardChain` 与 `DecisionEngine` 不各自求值**——不存在第二套 g1–g7 逻辑（守 G-2）。

---

## 5. 兼容性

| 兼容面 | 断言 | 状态 |
|---|---|---|
| `Verdict.REJECT == "reject"`（同理 allow/approval） | T2 | ✅ |
| `Decision == "reject"` / `d != "allow"` | T2 | ✅ |
| 旧 `tools_guard.Decision` 三值未变（`allow`/`reject`/`approval`，顺序不变） | T15 | ✅ |
| `tools_executor.py` 的 `d == "reject"` / `d != "allow"` 无需改动 | — | ✅（该文件**未改动**） |
| 与旧 `danger_default_policy` 逐组合等价 | T11 | ✅ |

**T11 覆盖**：`danger ∈ {none, low, high, critical, bogus} × has_channel ∈ {True, False}` 共 **10 组合**，新治理层裁决 == 真实 `tools_guard.danger_default_policy` 返回值。

---

## 6. 测试

### 6.1 T1–T15 覆盖

| # | 用例 | 关键断言 |
|---|---|---|
| T1 | Verdict 值域等价 | `{Verdict}` == `tools_guard._DECISIONS` 及旧 StrEnum 值集 |
| T2 | 字符串兼容 | `Verdict.REJECT == "reject"`；`Decision == "reject"`；`d != "allow"` |
| T3 | legacy 往返 | 9 个真实 `by` 参数化往返无损 + 四类前缀 kind |
| T4 | Principal 不变量 | HUMAN 缺 channel / 非人类前缀 / 非法通道 / 非法 `by` 均拒 |
| T5 | frozen | `Decision` / `Principal` 改字段 → `FrozenInstanceError` |
| T6 | eq/hash | 同字段相等、任一字段不同则不等、与 str/Verdict 比较、hash 一致 |
| T7 | str / `is_terminal_reject` | 字面量与终局语义 |
| T8 | 确定性 | 同输入两次 → 字段全等（注入固定 id/clock） |
| T9 | 不依赖 callable identity | 换 `check` 实现（同声明面）→ 指纹同 → Decision 相等 |
| T10 | 决策语义 | critical 不可升级；high 无通道→REJECT/有通道→APPROVAL；denied/timeout→REJECT；granted 保持；非法 verdict 拒 |
| T11 | 旧映射等价 | danger × has_channel 10 组合全等 |
| T12 | 无副作用 | 引擎仅持 `_id_factory`/`_clock`；无 `authorize`/`execute`/`emit`/`append`/`write` |
| T13 | **依赖边界（AST）** | import 白名单 + 无 core 符号 + 无 `def authorize` |
| T14 | 通道白名单一致 | `HUMAN_CHANNELS == approval._HUMAN_CHANNELS == approval.CHANNELS` |
| T15 | 旧枚举零回归 | 旧 `Decision` 三值/顺序不变 |

（另含门面导出守卫与 `ApprovalChannel` 协议性守卫。）

### 6.2 结果

| 项 | 基线（`a7bd902`） | **S3-1 后** | 变化 |
|---|---|---|---|
| S3-1 专项 `test_governance_decision.py` | — | **26 passed** | 新增 26 |
| 全量 collected | 1485 | **1511** | +26 |
| 全量 passed | 1483 | **1509** | **+26** |
| skipped | 2 | 2 | — |
| failed / error | 0 / 0 | **0 / 0** | — |

*2 条 skip = 既有平台相关（`test_spill`），与本次无关。*
*未执行面：`desktop_native/**`、`test_shell_parity.py`（缺 `PySide6`）——基线遗留 R-7，与本次无关。*

### 6.3 依赖边界检查（AST，独立复跑）

`decision.py` 的 import 集合 = `__future__ / dataclasses / datetime / enum / typing / uuid / pyharness.errors`。

- `pyharness.core.*` / `engine` / `bus` / `persistence` **命中 0**；
- 无 `def authorize`；无 `append` / `emit` / `write` 属性引用。

---

## 7. Δ 裁定（最终）

| Δ | 内容 | 最终裁定 |
|---|---|---|
| **Δ-1** | 不重实现 g1–g7 waterfall（`GuardChain` / `PolicyRule.check` 为唯一真源） | **APPROVED** |
| **Δ-2** | `decision_id` / `ts` 可注入（`id_factory` / `clock`），保证确定性可测 | **APPROVED** |
| **Δ-3** | `inputs_digest` 仅为输入字段；算法（`_approval_binding`）不复制、治理层不 import `tools_executor` | **APPROVED** |
| **Δ-4** | `decide()` 接**已解析 Policy**，不依赖 `PolicyEngine` 运行时（纯化子集） | **APPROVED** |
| **Δ-5** | `Decision` 与 `tools_guard.Decision` 并存（不删、不改名旧枚举） | **APPROVED** |
| **Δ-6** | 输入面由 `(call, scope, policy)` 调整为消费 `EvaluationResult` | **APPROVED**（单一求值链，见 §4） |
| **Δ-7** | 新增 `tool` / `inputs_digest` / `approval_available` / `prior` / `approval_ref` 治理上下文参数 | **APPROVED** |
| **Δ-8** | `approval_verdict ∈ {denied,timeout}` → APPROVAL 作废为 REJECT | **APPROVED**（有旧系统证据，见 §7.1） |
| **Δ-9** | `__hash__` 取 verdict | **APPROVED**，**登记为 M3 legacy compatibility debt**（见 §7.2） |
| **Δ-10** | `HUMAN_CHANNELS` 治理层本地声明 | **APPROVED v1.0**（白名单 + T14 守卫，见 §7.3） |

### 7.1 Δ-8 的旧系统证据

- **旧生产代码**：`tools_executor.py:562-563`
  ```python
  if verdict != "granted":          # denied/timeout = 不执行
      return f"审批{verdict},未执行"
  ```
  单一分支同时覆盖 `denied` 与 `timeout`；`approval.py:90` 的 `VERDICTS = ("granted","denied","timeout")` 确认值域。
- **旧测试**：`tests/unit/test_tools_executor.py:414` `@pytest.mark.parametrize("verdict", ["denied","timeout"])` → 断言 `not r.ok` 且 `prov.calls == 0`。

"不执行" 即治理语义的 **REJECT**（终局、零副作用）。

### 7.2 Δ-9：M3 legacy compatibility debt（登记）

`Decision.__hash__()` 以 **verdict** 为 hash basis，以满足：

```
Decision == "reject"  ⇒  hash(Decision) == hash("reject")
```

**正式确认**：

- Decision↔Decision 相等**仍按完整字段语义**判断；
- hash collision（同 verdict 的不同 Decision）是**允许的**；
- `Decision` **不应与裸 `str` 混用**于需要稳定身份语义的 set/dict（跨类型相等天然非传递）;
- **不得**为消除该债务修改 `tools_executor.py`;
- 本阶段**不重构** equality API。

### 7.3 Δ-10：`HUMAN_CHANNELS` 本地定义 + T14 守卫

治理层不得反向 import `core/approval.py`，故白名单在 `decision.py` 本地声明；**T14 一致性测试**断言其与 `approval._HUMAN_CHANNELS` / `approval.CHANNELS` 相等——把"复制"变为**受守卫的重复**，而非静默的第二真源。本阶段未移动该常量、未修改 `approval.py`。

---

## 8. F-SYNC-1 继承声明

**S3-1 不修 F-SYNC-1**（`persistence.py` / `session.py` / `event_bus.py` **零改动**，未编写掩盖性测试）。

已在 `decision.py` 模块 docstring 明确登记：**`decision.issued` 后续属强同步治理证据事件，其持久化可靠性继承 F-SYNC-1**的已知风险——强同步事件在**总线→store 适配器内** flush 失败时，该行被 `_flush_pending_all` 丢弃、异常被总线 `EVT-103` 隔离、`session.append` 面对空 pending **静默返回成功**。

**F-SYNC-1 必须在 S4 Entry Gate 前关闭，或取得明确的架构豁免**（当前判定：S3 = B 条件性；**S4 = C 阻塞**）。

---

## 9. 本阶段仍未实现（明确边界）

| 项 | 状态 |
|---|---|
| `decision.issued` 词表注册 | **未注册**（实测 `is_registered=False`；`EVENT_TYPES=74` / `SYNC_TYPES=12` 未变） |
| `receipt.py` / `evidence.py` / `audit.py` | **未创建**（三文件均不存在） |
| `ctx.governance.decisions` 挂载 | **未接线**（仍为 `None` 形状占位） |
| `authorize()` | **未实现** |
| 关 2 出口升格 | **未动**（`tools_executor.py` 未改） |
| S3-2 | **未进入** |

---

## 10. 边界确认（自查）

| 禁止项 | 状态 |
|---|---|
| 未修改 `tools_guard.py` / `tools_executor.py` / `approval.py` | ✅ |
| 未修改 `session.py` / `persistence.py` / `event_bus.py` | ✅ |
| 未修改 `events/vocab.py` / `events/payload.py` / `engine.py` / `core/agent.py` | ✅ |
| 未修改 `governance/policy.py` / `governance/context.py` | ✅ |
| 未修改任意 ADR / `REFACTOR_PLAN.md` / `ARCHITECTURE_DECISION_RECORD.md` | ✅ |
| 未修 F-SYNC-1 | ✅ |
| 未创建 receipt / evidence / audit | ✅ |
| 未注册 `decision.issued` | ✅ |
| 未进入 S3-2 | ✅ |

**本步产物**：`pyharness/governance/decision.py` · `tests/unit/test_governance_decision.py` · `pyharness/governance/__init__.py`（最小追加） · 本报告。

---

## 11. 下一步（待授权）

| 步 | 内容 |
|---|---|
| **S3-2** | 运行时接线：`decision.issued` 注册 + 关 2 出口升格 + `authorize()` + `ctx.governance.decisions` 挂载 |
| （另立） | F-SYNC-1 durability/reliability 阶段（含 T5 marker 红测试）——**S4 Entry Gate 前必须关闭或豁免** |

**等待人工 checkpoint。**
