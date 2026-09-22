# M5 Controlled Remediation Report（受控修复报告）

> **模式**：M5 Controlled Remediation。**按 M4.5 的补丁范围执行，未扩大范围。**
> **未 commit、未 push。** 全部改动在工作区。
> **纪律**：每项修复按 Unit → Runtime → E2E → Negative 四层取证；未使用 fake green；
> 未删除失败测试；未放宽治理规则；未把静态证据写成运行时证据。

---

## 1. Patch Summary（补丁摘要）

| # | GAP | 改了什么 | 文件 |
|---|---|---|---|
| **M5-1** | **N5** 通道契约 | 新增**唯一解析器** `core/channel.py`（五态冻结 + 前缀归一）；`GovernanceContext.principal_of` 去掉 falsy 短路；`ApprovalProvider._ensure_channel` 改为消费契约并**删除 `self._channel` 回退**；新增 `set_default_channel`；`engine` 的 provider 缺省由**装配参数**决定（不再硬编码 `"desktop"`）；`ApplicationService` 构造期按白名单 fail-closed | `core/channel.py`(新) · `governance/context.py` · `core/approval.py` · `engine.py` · `application/service.py` |
| **M5-2** | **N2-a** 域可见性 | `SELF_DOMAINS` 补 `"subagent"`；新增 `RESTRICTED_DOMAINS`（把"隐式收紧"变**显式声明**） | `core/scope.py` |
| **M5-3** | **GAP-08R + N3** 证据生命周期 | 触发点从 `run_for_task` 函数体 → **`segment.end` 生命周期**（新增 `_evidence_producer` + 总线订阅）；工件 `claim` 标注**终态**；`run_for_task` 内的旧调用删除 | `engine.py` |
| **M5-4** | **N2-b** 子代理证据 | `_run_on_session` **开段**（`segment.start/end`）；`EngineSubagentRunner.child_session` 在 child bus 上订阅**同一生产者**（工厂经装配层注入，不反向 import）；**N11** 1 行修（`notify` 的 schema 默认 `null` 致 `joined` 永不发出） | `core/orchestration.py` · `engine.py` · `core/subagent.py` |
| **M5-5** | **N1** flush 配置 | `open_store` 接收**已解析标量**；新增鸭子取值助手 `flush_kwargs_of(cfg)`（**不 import Settings**）；**全部 12 个** `open_store` 调用点接线 | `persistence.py` · `engine.py` · `desktop/sessions.py` · `cli.py` · `core/orchestration.py` · `repair.py` |
| **M5-6** | **N9** 文档漂移 | LIMITATIONS / README / F2 报告的分层计数与总数修正；L-11 补上「F2 口径不准」的真实说明 | `LIMITATIONS.md` · `README.md` · `F2_REMEDIATION_REPORT.md` |
| **M5-7** | **N8** 死导出 | `require_adapter` **保留 + 加注**（经复核：文档零引用 ⇒ 无兼容承诺，但删除属 H-4） | `core/llm.py` |

**新增测试文件 6 个**：`tests/invariants/test_inv_channel_contract.py` · `test_inv_domain_classification.py` ·
`test_inv_config_reachability.py`；`tests/e2e/test_channel_contract_e2e.py` · `test_subagent_evidence_e2e.py` ·
`test_evidence_lifecycle_e2e.py` · `test_config_wiring_e2e.py`。

---

## 2. Root Cause → Fix（根因 → 修复）

| GAP | Root Cause（本轮实际改的那一条） | Fix |
|---|---|---|
| **N5** | **不存在唯一的通道解析函数**；6 层各自实现"状态→语义"映射（精确名匹配 / 前缀匹配 / falsy 合并 / 硬编码默认 / hasattr 哨兵） | 抽出 `normalize_channel`/`resolve_channel`/`require_channel`；核心层直接消费；治理层经其 `from_legacy_by` 消费并由**真值表**守卫一致 |
| **N2-a** | `"subagent"` 域不在任何白名单 ⇒ 落入**隐式**收紧面 ⇒ strict 档 `can_use=False` | 补域 + 新增 `RESTRICTED_DOMAINS` 使分类**完备且显式** |
| **GAP-08R / N3** | 证据生产的**触发点挂在 `run_for_task` 的函数体**，而段终态由 `task_queue._run_task` 的 `finally` 保证 —— 两者保证强度不同 | 触发点移到 **`segment.end`**（执行上下文不在被取消的子任务里，故 await 安全） |
| **N2-b** | `_run_on_session` 不开段 + child bus 无生产者订阅 | 开段 + child bus 订阅同一生产者 |
| **N11** | `bool(args.get("notify", True))`：schema 默认 `null` 被 pydantic 填成 `None`，`.get` 返回该 `None`（默认 `True` 永不生效） | `True if args.get("notify") is None else bool(...)` |
| **N1** | `open_store` **无 cfg 入参** ⇒ 配置链在唯一一跳断开 | 工厂收标量 + 装配层解析注入 |
| **N9** | 手工计数未随代码更新 | 按实测重写 |

---

## 3. Invariant Matrix（不变量矩阵）

| Invariant | Before | Patch | Unit | Runtime | E2E | Negative | Mutation | After |
|---|---|---|---|---|---|---|---|---|
| **Channel Contract Uniqueness**（N5） | 不存在；7 状态中 **3 个跨层分裂** | `core/channel.py` 五态；六层统一 | **38 passed** | 矩阵 **7/7 一致**；ACP → `acp` | **6 passed**（含两装配路径一致） | `test_provider_default_never_overrides_missing_channel` · `test_empty_string_is_not_silently_degraded` · `test_service_rejects_non_whitelisted_channel`(6 例) | **M5-1a 11 failed / M5-1b 3 failed** | ✅ 成立 |
| **Registered Tool Is Classified**（N2-a） | 不存在；`subagent` 域未分类 | 补域 + `RESTRICTED_DOMAINS` | **6 passed** | strict 档 `can_use=True`；1 子会话（原 False/0） | **4 passed** | 域分类完备断言 + `exec/proc` 仍收紧 | **M5-2 4 failed** | ✅ 成立 |
| **Segment Terminal Completeness**（GAP-08R/N3） | 不存在；5 终态中 **3 种证据为 0** | 触发点 → `segment.end` | 由 E2E 承载 | **5/5 终态 evidence=1** | **9 passed**（含真实 `task_queue.cancel`） | 空段零归档 · 幂等 · N3 顺序断言 | **M5-3b 8 failed** | ✅ 成立 |
| **Declared Config Is Reachable**（N1） | 不存在；配置 3.0/7 → store 0.5/64 | 工厂收标量 + 12 点接线 | **8 passed** | **WIRED=True**；默认仍 0.5/64 | **3 passed** | `test_default_config_still_uses_defaults` · 依赖方向断言 | **M5-5 1 failed** | ✅ 成立 |
| （附）子代理证据链（N2-b） | 子会话 `segment 0/0`、`evidence 0` | 开段 + child bus 订阅 | — | 子会话 `segment 1/1`、**`evidence 1`** | **4 passed**（含 `subagent.joined` 追溯） | — | **M5-4 1 failed** | ✅ 成立 |

**Mutation 合计 6 次 / 全部 RED**（无 GREEN 即"无鉴别力"的情况）。

---

## 4. Runtime Evidence（运行时证据）

### Probe 1 — N5 通道（七状态 × 六层）

| 状态 | Service | Approval | Governance | 一致? |
|---|---|---|---|---|
| MISSING | `desktop`(构造默认) | **APR-503** ← 曾 `desktop` | **APR-503** | ✅ |
| explicit None | APR-503(服务需交互通道) | `None`(headless) | `system` | ✅ |
| `""` | APR-503 | **APR-503** ← 曾 `desktop` | **APR-503** ← 曾 `system` | ✅ |
| `desktop` | `desktop` | `desktop` | `human:desktop` | ✅ |
| `cli` | `cli` | `cli` | `human:cli` | ✅ |
| **`acp:c1`** | `acp` | **`acp`** ← 曾 `desktop` | `human:c1:acp` | ✅ |
| INVALID `hacker` | APR-503 | **APR-503** ← 曾 `desktop` | APR-503 | ✅ |

`_ensure_channel("acp:client-7")` → **`"acp"`**(interactive)，与同文件 `_require_human` 的 `ACCEPTED` **不再矛盾**。

### Probe 2 — N2 子代理可达性

```
preset=strict   : can_use=True   tool.result=1  subagent.spawned=1  child=[s-sub0001.jsonl]   ← 修复前 False/0/0/[]
preset=standard : can_use=True   tool.result=1  subagent.spawned=1  child=[s-sub0001.jsonl]
```

### Probe 2b — 子会话证据

```
parent : decision=1 receipt=1 segment 1/1  EVIDENCE=1
CHILD  : decision=2 receipt=2 segment 1/1  EVIDENCE=1   ← 修复前 0/0/0
```

### Probe 3 — 五终态

```
success 1/1/1 · abnormal_reason 1/1/1 · exception 1/1/1 · budget 1/1/1 · cancellation 1/1/1
                              (decision / segment.end / evidence.archived)
修复前:exception / budget / cancellation 三项 evidence=0
```

### Probe 4 — N1 配置

```
config=(3.0, 7)  store=(3.0, 7)   WIRED=True     ← 修复前 store=(0.5, 64)
default(不传)    store=(0.5, 64)                 ← 默认行为未被破坏
```

---

## 5. Regression（回归）

```
before (M5 基线) : 1,944 collected / 1,937 passed / 0 failed / 0 errors / 4 skipped
after  (M5 完成) : 2,025 collected / 2,021 passed / 0 failed / 0 errors / 4 skipped
delta            : +81 tests

coverage         : 79% → 79%   (18216/3801 → 18322/3792 stmts/miss;+106 语句、-9 未覆盖)
failed           : 0 → 0
skipped          : 4 → 4       (2 既有 e2e 标记 + 2 原生壳 importorskip)
```

**分层（after）**：security 49 · acceptance 9 · e2e 40 · invariants 141 · unit 1779 · integration 6。

**静态结构扫描（§11-7）**：

```
authorize() 调用点          : ['pyharness/core/tools_executor.py']   OK（恰 1 处）
guard.rejected 发射点        : ['pyharness/core/tools_guard.py:862']  OK（恰 1 处）
normalize_channel 定义处     : ['pyharness/core/channel.py']          OK（唯一实现）
治理读侧符号有生产调用        : governance.audit / archive_task_evidence / governance_audit( / flush_kwargs_of / _evidence_producer / require_channel  全 OK
```

**一次真实回归的处置（未掩盖）**：接上 `**flush_kwargs_of` 后全量出现 **2 failed** ——
`test_tenant_settings` 用 `channel="test"`（**非白名单占位串**）。我的服务层收紧按 M5-1
要求拒绝它。**判定**：该占位串是潜伏缺陷（它会作为 `by=` 传给审批，在 `_require_human`
处才 APR-503），故**改测试用例的通道为合法值**（测试目的=租户隔离，不受影响），
并**新增负向用例**把"服务层拒绝非白名单通道"钉死。**未放宽任何断言。**

**另一次自查**：`test_service_rejects_non_whitelisted_channel[desktop:]` 变红 —— 暴露我的
解析器把 `"desktop:"`（前缀后空 id）判为合法，而治理层 `from_legacy_by` 会拒它。
**这正是 M5-1 要消除的那类分歧**，已修解析器（空 id ⇒ INVALID），测试保留。

---

## 6. Remaining Gaps（未修复 / 剩余缺口）

| 项 | 状态 | 为什么不修 |
|---|---|---|
| **GAP-3** 原生壳 UI | **Environment Blocked** | 本机无 PySide6；需装 + 补 `uv.lock` 的 native 包条目（D-1） |
| **GAP-6** CI | **Environment Blocked** | 未 push ⇒ 从未在 Actions 执行 |
| **GAP-1** LLM 决策面 | **有意不做** | LLM 请求无工具名/参数集合/Provider 副作用；纳入需伪造 `ToolCall`（负空间，已登记） |
| **GAP-9** 跨会话标识符 | **有意不做** | `request_id`/`trace_id`/`conversation_id`/`subject_id` 为负空间（测试会阻止悄悄加入） |
| **L-1** 头回落面 | 未闭合 | 租户**持久化**残余已闭合；**派生**残余（未登记会话回落客户端头）未闭合 |
| **L-3** 审批二级复合键 | 未做 | `_pending` 仍以裸 `seq` 为键（一级 fail-closed 已在） |
| **L-14** `ctx.persistence` 死传播 | 未做 | 删除属公共契约变更 |
| **L-15 / L-16** 定时任务语义 | 待产品决策 | — |
| **N8** `require_adapter` | 保留 | 删除属 H-4；已加注登记 |

---

## 7. New Findings（本轮新发现）

**M4.5 阶段发现、M5 已处置的**：N5 · N2-a · N2-b · N11 · N1（均列入 §1）。

**M5 执行过程中新发现、按规则「只记录不扩范围」的**：

| 编号 | 内容 | 证据 | 级别 | 为何不修 |
|---|---|---|---|---|
| **N10** | **子会话工作区根越出工作区树且从未创建**：`subagent.py:375` `pol.workspace_root = str(base.parent / sub_id)` —— 子 workspace 被设为 `workspaces/` 的**兄弟目录**（实测路径 `<tmp>/s-sub0001`），且无人 `mkdir`。⇒ 子会话的**文件类工具必然失败**（实测：子会话 `fs.list_dir` 治理判 `allow`，却在**关 3** 以 `TLB-802 目录不存在` 结束；`tool.result=0`） | 运行时（子会话 3 次 `tool.call` → 0 次 `tool.result`，错误 advice="目录不存在"）+ 代码 `subagent.py:373-375` | **P1**（子代理能力对文件类工作**功能性空心**） | **不阻塞 M5 验收**（子会话的**段**与**证据**均已成立）；且根因在 `subagent.py` 的工作区供给逻辑，与 M5-4 改动的证据/段路径**无因果**。→ 下一轮 |

**README/LIMITATIONS 未因本轮新发现被"修饰"**：N10 尚未写入 `LIMITATIONS.md`（按纪律：
文档不得代替修复；且本轮授权范围含"修 N9 的既有漂移"，不含"新增限制条目"）。
**建议下一轮把 N10 与 L-3/L-14 一并登记。**

---

## 8. Re-Verification（重新验证）

**M4.5 的四个核心探针全部复跑，全部通过：**

| 探针 | 要求 | 实测 | 结论 |
|---|---|---|---|
| **Probe 1** N5 ACP Channel | `acp:client-7` 不通向 desktop / None / system；两装配路径一致 | `_ensure_channel` → **`acp`**；七状态矩阵 **7/7 跨层一致**；E2E 断言 `assemble_real_engine` 与 `attach_engine_to_ctx` 同得 `acp` | ✅ **PASS** |
| **Probe 2** N2 Sub-Agent | strict 档 `subagent.spawn` 可达 + 子会话真实存在 | `can_use=True` · `subagent.spawned=1` · 子会话 JSONL 生成 | ✅ **PASS** |
| **Probe 2b** 子会话段+证据 | 子会话有自己的段与证据 | child `segment 1/1` · `evidence.archived=1` | ✅ **PASS** |
| **Probe 3** 五终态 | 全部 `decision=1 · segment.end=1 · evidence=1 · duplicate=0` | **5/5 全 1**；重复归档返回 `None` | ✅ **PASS** |
| **Probe 4** N1 Config→Store | 配置值 == 运行时值；默认不破坏 | `(3.0,7) → (3.0,7)`；默认 `(0.5,64)` | ✅ **PASS** |

**原始负向验证（5 条）**：NT-1~NT-4 **逐条 PASS**；**NT-5 = `APR-503`**（确定性 fail-closed，
自 M4 起稳定）。

---

## 9. 未声明为"完成"的部分（诚实标注）

- **未 commit、未 push** ⇒ 一切"已修复"指**工作区已实现且测试通过**。
- **CI 仍未跑过**（Environment Blocked）。
- **原生壳 UI 仍无运行时证据**（Environment Blocked）。
- **N10（子会话工作区）为 P1 未修** —— 子代理对文件类工作仍不可用。
- `tests/unit/test_tenant_settings.py` 的 `channel="test"→"desktop"` 是**本轮唯一改动测试的地方**，
  已在 §5 说明理由（占位串是非白名单值，测试目的不受影响，且新增负向用例覆盖）。

---

_文档时间戳：2026-09-20T23:26:30+08:00_
