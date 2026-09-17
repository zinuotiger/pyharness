# LIMITATIONS.md — 当前能力与已知限制（Current State）

> **本文是「当前状态」的单一权威入口。**
> 仓库根目录下的 `S1_*`~`S6_*` 是**治理期阶段报告**（历史记录）；`docs/baseline/*` 与
> `REFACTOR_PLAN.md` 是 **S0 时点快照**。它们**描述的是当时**的形态，**不代表当前状态**。
> **现行能力与限制以本文为准。**
>
> **快照**：`3ef7000` @ 2026-09-17 ｜ 数据均来自实际执行结果（pytest 运行产物 + 运行时计数）

---

## 1. 当前已实现能力

| 面 | 内容 | 可核验锚点 |
|---|---|---|
| **主链** | 单进程事件溯源运行时：`engine` 装配 → `agent` → `agent_loop`（三态主循环）→ `tools_executor`（四关管道）→ `guard` → `approval` → `persistence` | `pyharness/engine.py` · `core/agent_loop.py` · `core/tools_executor.py` |
| **外壳** | CLI（**16 子命令**）· ACP（JSON-RPC over stdio）· Web 桌面（FastAPI）· PySide6 原生壳 | `cli.py` · `acp.py` · `desktop/` · `desktop_native/` |
| **治理层** | `authorize()` **唯一授权入口**（关 2 仅 2 处调用）；guard 单调拒绝链 **g1–g7**；`decision.issued` / `receipt.emitted` / `evidence.archived` 已接线 | `governance/context.py` · `core/tools_guard.py` |
| **事件真源** | append-only JSONL。`EVENT_TYPES` **77** · `SYNC_TYPES` **14** · `TRANSIENT` **3** · payload 模型 **75** | `events/vocab.py` · `core/session.py` |
| **不变量测试面** | **INV-01 ~ INV-05** 编号化用例 | `tests/invariants/test_inv_core.py`（**40 函数 / 48 用例**） |
| **编排** | jobs · schedule · subagent · workflow（**顺序步骤**，符合 ADR-017，不做 DAG）· MCP（配置门控） | `core/{jobs,schedule,subagent,workflow,mcp}.py` |
| **持久化与恢复** | JSONL 真源 + `repair`（F060 尾部截断/坏行隔离/视图重建）+ FTS 派生索引（可整体重建） | `persistence.py` · `repair.py` · `core/session_query.py` |
| **多租户（桌面）** | 进程内服务端派生 + 进程独立 store；服务仅绑 `127.0.0.1` | `desktop/app.py` · `core/tenant_settings.py` |
| **测试** | **1,751 collected / 1,749 passed / 0 failed / 2 skipped** · 覆盖率 **79%** | 全量实测快照 |

---

## 2. 当前未实现 / 已知限制

> 「影响范围」标注的是**可达面**；「计划」列区分**已进入本地 HEAD（未 push）/ 未做 / 未计划 / 待定**。
> **注意**：**「已进入本地 HEAD」不等于已发布** —— `origin/main` 仍停在 `0cba75d`，这些修复**尚未 push**，公开版本仍是缺陷形态。

| # | 限制 | 影响范围 | 计划 |
|---|---|---|---|
| **L-1** | 桌面租户派生**仍依赖进程内 session registry**：派生的两个必要前置为「路径匹配会话 id」+「该会话已在本进程登记」，任一不满足即**回落客户端租户头**（重启后的磁盘会话、不含会话 id 的路由均落在回落面）。（原**正则漏 `-`/`.`** 的缺陷**已修复**：`_SESSION_ID_IN_PATH` 字符集已对齐 `validate_session_id`） | **同机跨租户**会话隔离弱化。缓解：服务仅绑 `127.0.0.1`，**非远程可达** | 正则缺陷**已修复**（进入本地 HEAD，未 push）；**残余**：**session identity persistence 未完成**（租户未随会话落盘），闭合前**不得断言「已阻断伪造头」** |
| **L-2** | `policy.updated` 装配留痕**已修复**（F-01）：生产唯一 seam（chat/run 公共路径）发射 + 每 spine 幂等闸。（历史：词表早已注册、`SYNC_TYPES` 已含，但装配层未调用发射器 ⇒ 一度**永不发射**） | 治理策略生命周期**审计留痕已落地**（ADR-020 强同步承诺） | **已在本地 HEAD 修复**（`e561712` F-01），**未 push** |
| **L-3** | 审批 identity 的**二级复合键未收敛**：`_pending` 仍以**裸 `seq`** 为键（provider 跨会话共享，父子会话 seq 可同键）。**一级 session isolation 已完成**：跨会话同键碰撞由 **fail-closed guard** 拒绝（`APR-503`），不再覆盖 | 残余：碰撞时该次审批**失败**（而非错配解决）；拒绝点位于 `approval.requested` **之后**，该事件已落盘而无裁决留痕（工具侧记 `APR-503` 形成可追溯失败） | 一级**已进入本地 HEAD**（`e561712` F-28），**未 push**；二级（复合键 `(sid, approval_id)`，F1-X5/X6）**未做** |
| **L-4** | LLM 出口（`chat` / `chat_stream` / `summarize` / `json_chat` / `mini`）**缺 governance gate 统一**（`llm.py` 无 governance 引用）；**取消 / guard 闸同样未统一**。**预算闸已在岗**：`BudgetGuard` 经 `FallbackChain` 在**每请求前**与**每次重试前**生效（`fallback_models` 缺省非空 ⇒ 默认挂载） | 非循环 LLM 消费路径不受**治理授权与取消**约束；**链缺位（显式 `chain=None`）时预算闸一并失效** | **未计划**（需独立设计） |
| **L-5** | 持久化**强同步路径**无重试上限与暂停守卫（守卫仅在异步批量路径） | 磁盘持续故障时 `_retry_q` **无界增长**、会话不暂停 | **未做**（须独立交付） |
| **L-6** | `desktop_native/` **0% 覆盖**（937 语句） | PySide6 原生壳**不可自动化验证**（本机环境缺 PySide6，测试无法收集） | **未计划** |
| **L-7** | `tests/{acceptance,e2e,security}` 为**空壳 / 缺失** | 无**端到端**与**安全**自动回归；现有端到端验证靠人工脚本与真 tty 手驱动 | **未计划** |
| **L-8** | **无 CI 配置** | 无自动回归门禁 | 待定 |
| **L-9** | **无 LICENSE** | 公开使用授权不明确 | 待定 |

> **依赖面独立问题（D-1，未修复）**：`pyproject.toml` 声明 `pty` / `native` 两个 extra
> （`PySide6` / `qasync` / `pywinpty`），而**已提交的 `uv.lock` 对三者零包条目**
> ⇒ 干净检出上 `uv sync --extra native` 无锁可依。与 L-6「原生壳不可自动化验证」同源，
> **须独立交付**（不在本文件职责内修复）。

---

## 3. 与历史文档的关系

仓库中并存三类文档，**时间语义不同**：

| 类别 | 示例 | 时间语义 |
|---|---|---|
| **阶段报告**（根目录 83 个 `.md`） | `S1_CHANGE_REPORT.md` · `S2-1_EXIT_REVIEW.md` · … · `S6-2b_FINAL_SUMMARY.md` | **当时的执行与评审记录**；其中的"未修 / DEFERRED / OPEN"描述**属于当时状态** |
| **时点快照** | `docs/baseline/BASELINE_REPORT.md` · `docs/baseline/CURRENT_ARCHITECTURE.md` · `REFACTOR_PLAN.md` | **固化于 `0cba75d` @ 2026-09-14**；其中的问题清单**部分已在此后修复**（例如"g1 `g-schema` 恒 allow（P0）"已由 S1 修复） |
| **架构决策（现行）** | `docs/ADD.md`（22 条 ADR）· `docs/decisions/ADR-021/022` | **现行有效**，只增不改 |

> **读法建议**：先读本文件 → 再读 `docs/ADD.md`（决策）与 `docs/MAP.md`（架构视图）→ 需要过程细节时**再**按阶段读根目录报告。

---

## 4. 修复状态与发布节奏

- **L-2 已闭合**；**L-1 / L-3 仅一级闭合**（残余见 §2）。三者修复均**已进入本地 HEAD（`e561712`）**，**尚未 push** —— 即**当前公开版本（`origin/main` = `0cba75d`）仍是缺陷形态**；push 后本文件须再核对。
- **L-4 / L-5** 属需要独立设计与分次交付的改动，**当前无排期承诺**。
- 本文件**随发布更新**；若与任何阶段报告冲突，**以本文件为准**（阶段报告是历史记录）。
