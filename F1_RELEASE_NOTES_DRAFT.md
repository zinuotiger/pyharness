# F1 Release Notes — Draft

> 状态：**草稿（DRAFT）**，用于提交前评审。未 commit、未 push。
> 范围：F1 阶段（Core Runtime Path Audit）累计变更。

---

## 1. F1 目标

对 PyHarness 的**核心运行时主链**做一次功能性运行性审计，回答一个具体问题：

> 从**当前产品真实入口**出发、使用**当前产品实际采用的活装配路径**，能否完成一次真实 Agent Runtime 执行，并在出现断链时定位到确切位置？

审计基线（F1 建立）：

| 项 | 值 |
|---|---|
| 主链判定 | **CLOSED_LOOP_CONFIRMED**（Entry → Session → Agent Loop → LLM → Tool Call → Governance → Executor → Real Effect → Result → Event → Replay） |
| 活装配路径 | CLI → `attach_engine_to_ctx`；Desktop → `build_runner_components`（**不使用**已判定的死装配 `assemble_real_engine`） |
| 真实证据 | 真实 LLM 端到端运行、真 tty 交互会话、JSONL 真源逐条核对 |

审计过程中定位并修复 **3 个真实运行时缺陷**（D1 / D1b / D1d-A），并记录一批**未修复**的未决项。

---

## 2. 已关闭 Finding

| ID | 名称 | 严重度 | 影响 |
|---|---|---|---|
| **D1** | CLI 实时事件渲染层级错误 | 中 | 活路径上所有人类可读输出退化为占位符（`[工具] ? {}` / `[结果] None`），助手回复不可见；`--json` 机器出口契约被破坏 |
| **D1b** | CLI 审批提示 runtime contract 不符 | 高 | 高风险工具触发审批时**从不弹出提示**；且结果事件反向触发提问，用户作答必抛 `APR-503` |
| **D1d-A** | `/exit` 生命周期闸门 | 高 | 交互式 `chat` 中 `/exit` **无法退出** → 会话收尾 flush 不执行 → 尾部事件**不入 JSONL 真源** |

三个缺陷**均已修复、验证并冻结**。

---

## 3. 核心修复

### 3.1 D1 — 渲染面取错事件层级

- **根因**：总线投递的 canonical 形状是 `Envelope`（`SessionLog._dispatch` → `bus.emit(type, env)`），而渲染归一化只展平信封、未取内层 `payload`，导致卡片按错误层级取值全部落空。
- **修复**：`_normalize_payload` 识别信封形状（`payload` + `session_id` + `seq` 三键）并解包内层 `payload`；瞬时类型（`llm.chunk`）仍走裸 dict。
- **附带**：`task.completed` 卡片增补真实完成理由。

### 3.2 D1b — 审批身份与事件门

- **根因 1**：审批身份取自载荷（`approval_id` / `id`），而 canonical 契约是**该 `approval.requested` 事件的 `Envelope.seq`**（载荷 schema 明确不含 `approval_id`）。
- **根因 2**：渲染分发用 `startswith("approval.")` 通配，使 `granted` / `denied` / `timeout` 等**结果事件也进入人工提问分支**，用户作答后裁决必然失败。
- **修复**：`prompt_approval` 增加事件类型门（仅 `approval.requested` 提问）；审批身份改为从信封 `seq` 读取；收窄后不再为结果事件提问。
- **不变量**：`approval_id` == 请求事件 `seq`，request 与 response 使用同一 identity。

### 3.3 D1d-A — `/exit` 与 `ctx.agent` 解耦

- **根因**：`cmd_exit` 以 `ctx.agent is None` 充当**外壳退出闸门**，而生产 CLI 路径从不装配 `ctx.agent`（真实 `Agent` 类亦不含 `finish_session` / `new_session`，该接口仅存在于测试替身）→ 退出旗标永不置位 → 主循环永不返回 → 会话收尾 flush 不执行。
- **修复**：会话终态留痕改为**尽力而为**（`ctx.agent` 存在则调用，缺失即跳过）；外壳退出归 `ctx.shell`，二者不再互为前置。功能性改动**1 行**。
- **不变量**：`ctx.agent` 是否存在，不得决定 `/exit` 能否终止外壳。

---

## 4. 验证证据

### 4.1 测试

| 口径 | 结果 |
|---|---|
| 目标测试文件 | **92 passed** |
| 全量回归 | **1712 collected / 1712 passed / 0 failed / 0 errors / 2 skipped** |
| 基线对照 | F1-R1A 前 1704 → F1 结束 1712（净增 8 条回归用例） |
| F1-R1B 反向验证 | approval 相关 9 条在 D1d-A 修复后原样通过 |

### 4.2 Mutation Proof（测试鉴别力）

以运行期变异注入"修复前的错误实现"，验证新测试会失败：

| 缺陷面 | 变异 | 结果 |
|---|---|---|
| D1 | 恢复旧归一化 / 去掉事件类型门 / 只读载荷 | 均**被检出** |
| D1b | 恢复旧身份取值 / 去掉类型门 / 只读载荷 | 均**被检出** |
| D1d-A | 恢复旧退出闸门 | **2 failed**（失败信息即原始症状） |

### 4.3 真实产品证据

| 场景 | 结果 |
|---|---|
| D1 | 真实 `run`：改前 `[工具] ? {}` / `[结果] None` → 改后真实工具名与结果；`--json` 恢复 `{"type":…,"payload":<内层载荷>}` |
| D1b | 真 tty APPROVE / DENY / TTL 三场景，JSONL 判据全部满足（含 `approval.granted` 的 `approval_id` 与请求 `seq` 一致、D2 的 `approval_ref` 指向同一 identity） |
| D1d-A | 真 tty `chat`：`/exit` 后**进程退出**（改前永久存活）；`_flush_session` marker 直接观测；**JSONL 17/17 零缺失**（改前 6/17，缺 11 条）；replay 恢复完整历史（改前助手回复整段消失） |

---

## 5. Deferred 列表

**DEFERRED ≠ 已修复。** 以下均**未处理**，仅完成记录与隔离：

| ID | 名称 | 类别 |
|---|---|---|
| D1d-B | `ctx.render is None` 使失败信息静默丢弃 | 可观测性 |
| D1d-C | `flush_interval_s` 无消费者；"定时 flush 任务"不存在 | 持久化可靠性 |
| D1c | 重复 `ApprovalProvider` 订阅 → 合法裁决被记 `system.error(APR-503)` | 审计事实污染 |
| NEW-CMD-001 | 斜杠 `/new` 在全部壳中静默 no-op | 命令可用性 |
| SPEC-DRIFT-001 | commands 规格与实现不一致（规格编码了缺陷闸门） | 契约/文档漂移 |
| X2–X7 | 分发设计 / CLI 身份模型 / seq 碰撞 / push-pull identity 分歧 / 文档同步面 | 架构风险 |
| D2–D5 | 控制流被记 ERROR / `budget`·`stats` BROKEN / 注释漂移 / 审批通道硬编码 | 可诊断性与可用性 |

另：F0 阶段的独立发现面（dead feature 群、桌面层死簇、租户隔离正则、默认 `pytest` collection error、全域 E2E=0 等）保持在其原阶段报告中。

---

## 6. 已知限制

1. **未提交**：本阶段变更仍在工作树中，尚未 commit。
2. **无 E2E 测试目录**：`tests/e2e/`、`tests/acceptance/` 为空；F1 的真实验证走**独立脚本与真 tty 手驱动**，未沉淀为可重复的 CI 用例。
3. **`ctx.agent` 长期口径未定**：当前 CLI 路径下恒为 `None`，`/exit` 已与之解耦，但 `/new` 仍受影响（见 NEW-CMD-001），且规格尚未同步（见 SPEC-DRIFT-001）。
4. **审批场景需放开沙箱档位**：默认 `strict` 档下 `exec.*` 对 LLM 不可见，故"高风险工具触发审批"的真实场景验证需临时配置。
5. **持久化丢失窗口仍在**：即使 `/exit` 已修好，非强同步事件仍依赖会话收尾 flush；若进程被强制终止，最后一次强同步锚点后的事件仍可能丢失（见 D1d-C）。
6. **默认 `pytest` 口径不可直接运行**：需补 `--ignore` 两个文件，否则 collection 中断（环境缺 PySide6）。

---

*本文档为草稿，供提交前评审使用。*
