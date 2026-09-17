# LIMITATIONS.md — 当前能力与已知限制（Current State）

> **本文是「当前状态」的单一权威入口。**
> 仓库根目录下的 `S1_*`~`S6_*` 是**治理期阶段报告**（历史记录）；`docs/baseline/*` 与
> `REFACTOR_PLAN.md` 是 **S0 时点快照**。它们**描述的是当时**的形态，**不代表当前状态**。
> **现行能力与限制以本文为准。**
>
> **快照**：`70b689d` @ 2026-09-17 ｜ 数据均来自实际执行结果（pytest 运行产物 + 运行时计数）

---

## 1. 当前已实现能力

| 面 | 内容 | 可核验锚点 |
|---|---|---|
| **主链** | 单进程事件溯源运行时：`engine` 装配 → `agent` → `agent_loop`（三态主循环）→ `tools_executor`（四关管道）→ `guard` → `approval` → `persistence` | `pyharness/engine.py` · `core/agent_loop.py` · `core/tools_executor.py` |
| **外壳** | CLI（**16 子命令**）· ACP（JSON-RPC over stdio）· Web 桌面（FastAPI）· PySide6 原生壳 | `cli.py` · `acp.py` · `desktop/` · `desktop_native/` |
| **治理层** | `authorize()` **唯一授权入口**（关 2 仅 2 处调用）；guard 单调拒绝链 **g1–g7**；`decision.issued` / `receipt.emitted` / `evidence.archived` 已接线 | `governance/context.py` · `core/tools_guard.py` |
| **事件真源** | append-only JSONL。`EVENT_TYPES` **77** · `SYNC_TYPES` **14** · `TRANSIENT` **3** · payload 模型 **75** | `events/vocab.py` · `core/session.py` |
| **不变量测试面** | **INV-01 ~ INV-05** 编号化用例 | `tests/invariants/test_inv_core.py`（**23 函数 / 31 用例**） |
| **编排** | jobs · schedule · subagent · workflow（**顺序步骤**，符合 ADR-017，不做 DAG）· MCP（配置门控） | `core/{jobs,schedule,subagent,workflow,mcp}.py` |
| **持久化与恢复** | JSONL 真源 + `repair`（F060 尾部截断/坏行隔离/视图重建）+ FTS 派生索引（可整体重建） | `persistence.py` · `repair.py` · `core/session_query.py` |
| **多租户（桌面）** | 进程内服务端派生 + 进程独立 store；服务仅绑 `127.0.0.1` | `desktop/app.py` · `core/tenant_settings.py` |
| **测试** | **1,723 collected / 1,721 passed / 0 failed / 2 skipped** · 覆盖率 **78%** | 全量实测快照 |

---

## 2. 当前未实现 / 已知限制

> 「影响范围」标注的是**可达面**；「计划」列区分**本地已修·未进入公开版本 / 未做 / 未计划 / 待定**。
> **注意**：「修复已在本地完成」**不等于已发布** —— 这些修复**尚未进入当前公开版本**，公开版本仍是缺陷形态。

| # | 限制 | 影响范围 | 计划 |
|---|---|---|---|
| **L-1** | 桌面租户派生正则漏 `-`/`.`（`s-fork-*` 会话匹配不全 ⇒ **回落信任客户端租户头**） | **同机跨租户**会话隔离弱化。缓解：服务仅绑 `127.0.0.1`，**非远程可达** | **修复已在本地完成，尚未进入当前公开版本** |
| **L-2** | `policy.updated` 事件在生产路径**永不发射**（词表已注册、`SYNC_TYPES` 已含，但装配层未调用发射器） | 治理策略生命周期**无审计留痕**（ADR-020 的强同步承诺未落地） | **修复已在本地完成，尚未进入当前公开版本** |
| **L-3** | 审批 `_pending` 以**裸 `seq`** 为键，provider 跨会话共享 ⇒ 跨会话可覆盖 | 罕见**跨会话审批错配** | **一级修复已在本地完成，尚未进入当前公开版本**；二级（复合键）**未做** |
| **L-4** | `LLMClient` 全部出口（`chat` / `chat_stream` / `summarize` / `json_chat` / `mini`）**无预算 / 取消 / guard / governance 闸**；三闸只绑在 agent-loop 上 | **非循环 LLM 消费路径**不受运行时三闸约束 | **未计划**（需独立设计） |
| **L-5** | 持久化**强同步路径**无重试上限与暂停守卫（守卫仅在异步批量路径） | 磁盘持续故障时 `_retry_q` **无界增长**、会话不暂停 | **未做**（须独立交付，与 L-1~L-3 分批） |
| **L-6** | `desktop_native/` **0% 覆盖**（901 语句） | PySide6 原生壳**不可自动化验证**（本机环境缺 PySide6，测试无法收集） | **未计划** |
| **L-7** | `tests/{acceptance,e2e,security}` 为**空壳 / 缺失** | 无**端到端**与**安全**自动回归；现有端到端验证靠人工脚本与真 tty 手驱动 | **未计划** |
| **L-8** | **无 CI 配置** | 无自动回归门禁 | 待定 |
| **L-9** | **无 LICENSE** | 公开使用授权不明确 | 待定 |

---

## 3. 与历史文档的关系

仓库中并存三类文档，**时间语义不同**：

| 类别 | 示例 | 时间语义 |
|---|---|---|
| **阶段报告**（根目录 69 个 `.md`） | `S1_CHANGE_REPORT.md` · `S2-1_EXIT_REVIEW.md` · … · `S6-2b_FINAL_SUMMARY.md` | **当时的执行与评审记录**；其中的"未修 / DEFERRED / OPEN"描述**属于当时状态** |
| **时点快照** | `docs/baseline/BASELINE_REPORT.md` · `docs/baseline/CURRENT_ARCHITECTURE.md` · `REFACTOR_PLAN.md` | **固化于 `0cba75d` @ 2026-09-14**；其中的问题清单**部分已在此后修复**（例如"g1 `g-schema` 恒 allow（P0）"已由 S1 修复） |
| **架构决策（现行）** | `docs/ADD.md`（22 条 ADR）· `docs/decisions/ADR-021/022` | **现行有效**，只增不改 |

> **读法建议**：先读本文件 → 再读 `docs/ADD.md`（决策）与 `docs/MAP.md`（架构视图）→ 需要过程细节时**再**按阶段读根目录报告。

---

## 4. 修复状态与发布节奏

- **L-1 / L-2 / L-3（一级）** 的修复**已在本地完成，尚未进入当前公开版本** —— 即**当前公开版本仍是限制形态**（不是已发布状态）；纳入后**本文件将同步更新**。
- **L-4 / L-5** 属需要独立设计与分次交付的改动，**当前无排期承诺**。
- 本文件**随发布更新**；若与任何阶段报告冲突，**以本文件为准**（阶段报告是历史记录）。
