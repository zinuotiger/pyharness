# LIMITATIONS.md — 当前能力与已知限制（Current State）

> **Engineering RC（2026-09-24）说明：** 当前被测范围、安装结果与限制以 [STATUS.md](STATUS.md) 和 [候选报告](reports/PYHARNESS-ENGINEERING-RC-20260924.md) 为准。下文是 2026-09-21 的历史快照；其中“当前”“单一权威”、测试数量、native 安装状态和未提交状态均只指当时版本，不能代替本候选证据。


> **本文是「当前状态」的单一权威入口。**
> 仓库根目录下的 `S1_*`~`S6_*` 是**治理期阶段报告**（历史记录）；`docs/baseline/*` 与
> `REFACTOR_PLAN.md` 是 **S0 时点快照**。它们**描述的是当时**的形态，**不代表当前状态**。
> **现行能力与限制以本文为准。**
>
> **快照**：2026-09-21（F2 全量修复轮 + M4/M4.5 独立复核 + M5 受控修复 + M5.5 独立验收 + **持续优化轮 R1-R30**）｜ 数据均来自实际执行结果
> （pytest 运行产物 + 运行时探针）｜ 本轮改动**尚未提交**（工作区状态）。

---

## 0. M5 受控修复轮（2026-09-20，紧接 F2）

F2 之后又做了 **M4 独立验收**（含独立 agent 对抗性复核）与 **M4.5 修复前深度验证**，
发现 F2 的部分结论**范围过宽**，遂进入 M5 受控修复。**准确口径**：

| 项 | F2 结论 | M4/M4.5 复核 | M5 处置 | 证据等级 |
|---|---|---|---|---|
| **N5** 通道契约 | GAP-11 已修 | **证伪**：`_ensure_channel`（精确名）与 `_require_human`（前缀）**同文件内相反**；MISSING 在审批层=`desktop` 而治理层=reject；`""` 在治理层**静默降级**为 system；ACP 在审批层被误判 headless/被硬编码劫持为 desktop | **已修**：单一解析器 `core/channel.py`（五态冻结）；六层统一消费；服务层收紧为白名单 | E2E + 运行时矩阵 + 变异 |
| **N2-a** 域可见性 | 未发现 | **新发现 P1**：`subagent` 域不在 `SELF_DOMAINS` ⇒ 默认 strict 档 `subagent.spawn` **不可达**（同类缺陷第三次） | **已修**：补 `subagent` 域；新增 `RESTRICTED_DOMAINS` 显式声明 + "注册即须归类"不变量 | 运行时（strict vs standard） |
| **GAP-08R + N3** 证据生命周期 | GAP-8 "Fully Closed" | **证伪**：证据产出**当且仅当 `loop.wake` 正常返回**（实测 5 终态中 3 种为 0）；工件早于段关闭 | **已修**：触发点从 `run_for_task` 函数体移到 **`segment.end` 生命周期**（该事件由 `_run_task` 的 `finally` 保证、且不被取消）；工件 claim 标注终态 | 五终态 E2E + **真实 `task_queue.cancel`** + 变异 |
| **N2-b** 子代理证据 | N2 仅读码 | 运行时确证：子会话 `segment 0/0`、`evidence 0` | **已修**：`_run_on_session` 开段；child bus 订阅同一生产者 | 运行时 + E2E |
| **N11** `subagent.joined` | 未发现 | **新发现**：`bool(args.get("notify", True))` 因 schema 默认 `null` 恒得 False ⇒ 该事件在 LLM 工具路径**永不发出** | **已修**（1 行，属 M5-4 验收点） | E2E |
| **N1** flush 配置 | GAP-13 "Fully Closed" | **证伪**：`open_store` 从不读 config ⇒ `log.jsonl.flush_interval_s`/`flush_batch` 是**死配置**（实测 3.0/7 → 0.5/64） | **已修**：`open_store` 接收已解析标量；装配层经 `flush_kwargs_of(cfg)` 注入（**不 import Settings**，只鸭子取值） | 运行时（配置==实际）+ E2E + 变异 |
| **N9** 文档漂移 | — | 分层计数写错（acceptance 7→9、e2e 13→40） | **已修**（本文件 §1 与 F2 报告同步） | 实测 |
| **N8** 死导出 | — | `require_adapter` 零生产调用者、文档零命中 | **保留加注**（删除属 H-4 公共导出面变更） | Static |

**F2 结论的准确性修正（重要）**：F2 报告把 GAP-8 记为 `Fully Closed`、把 GAP-1/GAP-11 的
边界说得偏宽。M4 复核后 **GAP-8 重开**（现由 M5 闭合）；GAP-11 的正确表述是
"**治理层**已闭合，审批/服务层原先分歧（M5 已统一）"。

---

## 0.1 持续优化轮 R1-R30（2026-09-21）

承接 **M5.5 独立验收** 留下的"只记录未修"项，按"真跑一次 + 看外部产物"口径逐条复现后修复。
**所有结论均有运行时证据**（探针前后对照 / 全量回归）。

| 项 | M5.5 结论 | 本轮复现 | 处置 | 证据等级 |
|---|---|---|---|---|
| **N10** 子会话工作区 | P1 功能性空心：子根越出 `workspaces` 树且从未创建 ⇒ 子代理文件类工具必失败 | 探针复现：子根 = `…\workspaces\..\{sub_id}`、`exists=False`、`fs.list_dir` → `TLB-802` | **已修**（根因不止一处，见下行 + L-20） | 探针对照 + 全量回归 + 新不变量测试 |
| **N10 根因①** 会话根派生分裂 | （M5.5 判为"契约从未定义"，建议先定契约） | **更深一层**：`engine` 赋的是**裸** `workspaces_dir`（非每会话根），与 PRD-Core §5.6 / CFG §3.6 / OPS / DEP / 两份 spec **六处权威**冲突 ⇒ ①全部会话共用同一 fs 根（**跨会话隔离失效**）②子会话按规格形态推导必然越界 | **已修**：抽出唯一派生点 `scope.session_workspace`，engine/subagent 一律引用；子根**立即建目录**；`tool_exec._sandbox_dir` 去掉冗余 `/<sid>`（否则双嵌套） | 探针（两会话隔离 + 子根落点/可用）+ AST 单点断言 |
| **N10 根因②** `getattr` 取错属性名 | 未发现 | `getattr(log_, "session_id", "")` —— `SessionLog` 只有 `.sid` ⇒ `scope.session_id`、`BudgetGate.session_id`、CLI workflow 会话号**恒为空串**（静默 default 掩盖） | **已修**：5 处改回 `.sid`（含 2 处被短路掩盖的同类写法） | 探针对照（scope.session_id 由 `""` → 真实 sid）+ 不变量测试 |
| **O-2** 回喂文本误导 | P3：`tool.error` 的 message 写"工具未注册"，真实原因只在 stderr advice；对 LLM 误导 | 探针实测原文：`错误[TLB-802]:工具未注册。建议:tool.error 回喂 LLM 自查,不静默` —— 建议列是**运维话术**，调用点写好的现场指引被整条丢弃 | **已修**：`to_model_message` 建议取值改为 **调用点 advice > 调用点 hint > 码表处置列**；已作建议的文本不再重复进"明细" | 探针前后对照 + 单测三条（优先级/回落/去重） |
| **O-3** 预算终态码丢失 | P3：`BudgetExhausted` 落入 `except Exception` 兜底 ⇒ 持久化记 `error:CYC-999`，丢 budget 语义 | 探针复现：`task.failed{reason: error, error: CYC-999}` | **已修**：`task_queue._run_task` 增显式分支 → `reason=budget / error=LLM-305`（ERR.md 已登记该映射；常态下 agent-loop 自行收敛，本分支为防御路径） | 探针前后对照 + 单测 |
| **N1 泛化** 死配置 | M5 只修了 `flush_interval_s/flush_batch` 两个键 | 全量扫描 90 个叶子键：14 个在 `config.py` 之外零读取者，且全部在 CFG.md 里写成可调旋钮 | **已修 4 键**：`loop.max_arg_failures_per_round`（F026 阈值原**硬编码 2**）、`llm.probe.interval_s`、`plugins.backpressure_limit`、`loop.task_queue_max`；其余登记（L-20）+ **AST 级死配置防线** | 扫描 + 接线后运行时断言 + 防线自证 |

### R3（第三轮：资源生命周期 / 能力可达性）

本轮三个发现都属"**实现存在但生产不可达**"，均以运行时前后对照取证：

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R3-1** | **会话关闭不回收后台子进程**（F052/F053） | `proc.close_session`（docstring 与 `_SESSIONS` 注释都写"会话关闭即清"）**零生产调用者** ⇒ 起一个 `proc.start` 进程后 `spine.close()`：登记项**仍在**、`sess.status()["running"] is True`（进程树/泵线程/定时器一并泄漏，可能占工作区句柄） | **已修**：`EngineSpine.close()` 增进程树收尾（与 jobs/subagent 的 child-first 清理同处收口） | 探针前后对照 + 不变量测试 |
| **R3-2** | **模型降级后永不回切**（F033/F028） | `FallbackChain.probe_loop` **零生产调用者**（仅单测启动过），而健康状态机**只能**被探针改回 `healthy`、降级链 `idx` 也**只在探针里**归零 ⇒ 一次降级 = **进程生命周期内永久降级**，`llm.recovered` 永不发出 | **已修**：装配期启动探针任务（`fallback_models` 非空时），周期取自 `llm.probe.interval_s`；收尾取消任务；顺手修 `probe_loop` 遍历**注册表**导致"注册了链外模型 ⇒ `health[name]` KeyError 打死探针"的隐患（改为只探**链内**成员，偏离已在 docstring 说明） | 探针前后对照 + 不变量测试（含负空间：无链不起任务）+ 全量回归 |
| **R3-3** | **探针任务/`LLMClient.chain` 读取面** | `LLMClient` 的链是私有 `_chain`；引擎若按 `chain` 取名会静默取到 `None`(**同类属性名错**第二次出现) | **已修**：`LLMClient.chain` 只读别名 + 装配走别名 | 不变量测试断言 `spine.llm.chain is not None` |

**方法论（本轮第 2 次命中）**：**"实现存在 ≠ 调用链存在"** —— 死函数/死配置的检出不能只看"文件里有没有这段代码"，要问**谁调用它**（零调用者扫描 127 个候选，逐个核验真伪后落地 3 个真缺陷）。

### R4（第四轮：生命周期配对 / 共享总线 / 属性名同类扫描）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R4-1** | **会话关闭不处理未决审批**（F015/F025） | `ApprovalProvider.detach`（docstring 写"幂等，**随 ctx.close()**"）**零生产调用者** ⇒ 关会话后 `_pending` 仍挂着、`_detached=False`、等待裁决的任务**悬挂**；订阅未摘、"会话信任"未清 | **已修**：`EngineSpine.close()` 调 `approval.detach()`（放在 store 关闭**之前** —— detach 会落 `approval.denied`/`queue.resumed`） | 探针前后对照（`pending [13]→[]`、`detached False→True`）+ 不变量测试 |
| **R4-2a** | **共享总线上遗留整会话订阅**（F002/F004） | 桌面壳多会话**共用一条总线**（`bus=getattr(self.ctx,"bus")`）。实测关闭会话后共享总线遗留 **83 条订阅**（落盘记录器 + 证据收集器 + 证据生产者）⇒ 证据生产者继续对后续会话 `segment.end` 反应、记录器向已关闭 store 写入、内存随会话数线性增长 | **已修**：装配期登记本会话属主（`spine.bus_owners`），`close()` 按属主精确摘除 | 探针前后对照（86→0）+ **对抗用例**（关 A 不误伤 B，2 会话共享总线） |
| **R4-2b** | **订阅属主串取错属性名**（PIT-16 第二例） | 3 处 `getattr(log_, 'session_id', '?')` —— `SessionLog` 只有 `.sid` ⇒ 属主恒为 `engine:?` / `governance-evidence:?`：既标识错误，又**让"按属主摘除"会连坐其他仍在运行的会话** | **已修**：抽出 `_sid_of(log_)` 统一取值；属主串含真实 sid | 探针（属主由 `:?` → `:s-…`）+ 对抗用例 |
| **R4-3** | `getattr(x, "名", 默认)` 静默失败**系统扫描** | 982 处 getattr 字面量 → 56 处"全库未定义名"候选 → **逐个核验后无真缺陷**（均为 `__await__`/`is_alive` 等协议名、`schemas_for` 之类的鸭子类型回落、或已文档化的多形态兼容） | **不改**（避免为改而改）；该方法与结论记入 KEY-FINDINGS PIT-16 | 静态扫描 + 逐项核验（含运行时验证 `UsageCounters` 可 `setattr` 计数） |

### R5（第五轮：共享可变状态 / 注册表生命周期）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R5-1** | **插件 guard 钩子只注册不注销**（F004/F023） | `register_guard_hook` **无注销面** ⇒ ①卸载后陈旧 `Guard` 仍留在 `_PLUGIN_HOOKS`，新声明仍会解析到它（**陈旧守卫影响授权判定**）；②含钩子的插件**重装撞 TLB-801"名全库唯一"** —— 与 `PluginManager.uninstall` docstring 的"**支持重装(F004)**"对该类插件**矛盾** | **已修**：补 `unregister_guard_hook`（幂等）+ `PluginHost.detach` 在注销工具**之前**读 `guard_hooks` 并随之注销 | 单测（注册→激活→停用→钩子消失→可重注册） |
| **R5-2** | 共享可变状态盘点（模块级/类级容器） | 扫描出全部模块级注册表（`_scopes`/`_SESSIONS`/`adapters`/`_PENDING_EMITS`/`_STORES`/`_SESSION_TENANTS`/`_PLUGIN_HOOKS`/`ERRORS`）——除 R5-1 外，其余均有配对释放路径或属静态常量表 | **不改**；清单作为后续"跨会话/跨租户隔离"复盘的入口 | 静态扫描 + 逐项核验 |

**方法论（第三轮命中）**：**"成对契约必须查另一半"** —— 生命周期扫描不能只查"函数有没有被调用"，还要查**每一对**（`subscribe/unsubscribe`、`register/unregister`、`enter/detach`、`create/cancel`、`open/close`）的**另一半是否存在且被生产路径调用**。R4/R5 的 4 个缺陷全部由该判据命中。

### R6（第六轮：跨租户 / 状态机 / 错误传播 / 数据一致性 / F061）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R6-1** 跨租户事件可见性 | 两个外壳**各自实现**租户过滤且失败方向**相反**：Web 投影层 fail-closed，原生壳 `not owner or owner == tenant` 在归属**未知时放行**；归属取自**进程内映射**（重启即空、可被跨租户 sid 撞名污染） | 原生壳：未登记会话的事件**跨租户可达 UI**（fail-open）；映射被污染时投影层按错误租户投递 | **已修**：抽出**唯一判据** `tenant_settings.event_tenant_allowed` + **唯一派生点** `event_tenant_of`（**优先落盘信封** GAP-10，回落映射）；两壳统一消费 | 单测（判据真值表/信封优先）+ 端到端投影投递（本租户收、异租户拒、污染时信封胜出） |
| **R6-2** 会话收尾 × 在途任务 | `agent.close()` 只取消 **loop**，不排空**任务队列** ⇒ 会话终态后任务终态/段锚被 EVT-104 拒写 | 实测：`task.completed=0`、`segment.end=0`、`wait_for` **3s 未返回**（悬挂）、泵记 crash | **已修**：`TaskQueue.shutdown()`（摘排队 + 取消在途 + 等泵退场 + 兜底结算），`EngineSpine.close()`/`ApplicationService.shutdown()` 在 `session.finished` **之前**调用；并让 `_run_task` 的 started/终态/关段对 EVT-104 **不吞结算** | 双次序探针（排空优先：`failed(cancelled)`+`segment.end`；坏次序：不悬挂）+ 2 测试 |
| **R6-3** 错误传播到 API 层 | `_STATUS_FOR_CODE` 只映射 12 码，**客户端成因**的码（GRD-401/SKL-901/BUS-002/003/CFG-60x/TLB-802/803/JOB-001）全部回落 **500** | 与 ADR-011"4xx 客户端 / 5xx 引擎"相反：客户端把自身错误当服务端故障重试/告警 | **已修**：补齐 11 个客户端成因码（403/404/400/409/503）；引擎侧保持 500 | 端到端：`/api/skills/<不存在>` → **404**（修前 500）+ 映射真值表测试 |
| **R6-4** 数据一致性 CND-06 | 复核 `repair`/FTS：判据**已单源**（`repair._indexable` 委派 `session_query.is_indexable`；行切分同基座）、重写**原子且字节保真**（`_iter_bytes` 保留原行尾） | **但字节保真只有注释声明、无测试锁定** | **加固**：新增"逐字节往返（含 CRLF/末行无换行）"与"判坏集合 == 摘除集合"两条差分用例 | 2 测试（对同字节两路读取/重放比对） |
| **R6-5** L-22 / F061 | 附件入站只到 schema 层：mime 由客户端自报且缺省回落 `image/png`、**无魔数/大小/数量校验**、`sha256` 可省并回落**全零占位**（不去重、伪造哈希被接受） | 伪装图片的恶意文件可进事件流（PRD F061 明列要防） | **已按 PRD 实现**：新模块 `core/attachment.py`（魔数定类型/解析四格式尺寸/≤max_bytes/≤max_per_message/`sha256[:16]` 内容寻址去重落盘到**会话工作区**，`ATT-001` 零写盘）；服务层单点接入；**顺带接线 3 个死配置键**（`security.attachment.*`）；删除 2 处死代码副本 | 15 测试（四格式/伪装/超限/去重/工作区缺位 fail-closed/**真实 PNG 与独立 struct 读取交叉验证**） |

### R7（第七轮：二阶问题 · 重启/重装对抗）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R7-1** 租户归属的**二阶**缺陷（由 R6-1 引出） | R6-1 若采用"先写者胜"会把错误从"后写者夺走"变成"**陈旧者占位**"：`_SESSION_TENANTS` 从不清理，而 sid **只在租户内唯一**（会话目录按租户分目录）⇒ 服务重启后旧认领会挡住新租户的合法登记 | 归属查询会给出**错误的确定答案**（不是"未知"） | **已修**：模型改为 sid → **认领集合**；**单认领才给确定答案，多认领 ⇒ 未知(fail-closed)**；`unregister_session_tenant(sid, tenant)` 只撤本租户认领 | 单测（单认领/多认领→未知/撤其一恢复确定） |
| （复核）插件重装往返 | R5 补了 guard 钩子注销 | 既有 `test_pm_reinstall_after_uninstall` 覆盖 install→uninstall→install→activate | **无需改动**（R5 的钩子用例是增量） | 既有用例 + R5 新增用例 |

**方法论（第四轮命中）**：**"共享注册表用扁平键承载分层唯一标识 ⇒ 撞名时给错误确定答案"** —— 正确做法是**归属集合 + 歧义即未知**，并把失败方向统一为**关闭**；且修复后必须做**二阶分析**（R6-1 的第一版修法自身会引入 R7-1）。

### R8（第八轮：多会话并发对抗 / 重启恢复 / 派生状态生命周期 / 原生壳一致性）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R8-1** | **engine 落盘订阅缺 sid 过滤 ⇒ 跨会话 JSONL 串写**（P1） | 两 spine 共用一条 bus：A 的 JSONL 里出现 B 的 `session.created`（第 2 行）——回放错序、`repair` 会误判空洞/误隔离。桌面壳同款订阅**一直有**该过滤并注明"过滤责任在订阅者"，engine 侧长期缺失 | **已修**：抽**唯一实现** `persistence.session_recorder(store, sid)`；engine/desktop/CLI 三处一律委托（`_record_to` 保留为薄委托并对缺省 sid 告警） | 探针（污染 → 干净）+ 5 测试（落盘/并发/关闭隔离/租户/工作区 + 记录器单测） |
| **R8-2** | 重启恢复一致性（**未发现缺陷**） | — | 逐项实证"首开 == 恢复开"：工作区派生（纯函数）、作用域 sid、租户（日志胜出）、证据（`from_log` 重建计数一致）、审批（无残留 pending）、队列（空闲）、产物文件仍在 | 2 e2e 比较用例 |
| **R8-3** | **删除会话不级联清 FTS 索引行** | `SessionQueryIndex.delete_session` **零生产调用者** ⇒ **已删内容仍可被搜索**（结果还指向不存在的会话）；另 `_locks` 只增不减 | **已修**：`ApplicationService.delete_session` 在 `spine.close()` **之前**调 `fts.delete_session(sid)`；桌面管理器删除时释放每会话锁 | 2 e2e（含**调用顺序**断言：清索引早于关库） |
| **R8-4** | **原生壳订阅属主固定** ⇒ 跨租户互相摘除 | `DesktopNativeController` 每实例服务一个租户，却用固定 `"desktop-native"` 作属主；`close()` 按属主摘除 ⇒ 关闭 A 让 B 的原生窗口**收不到任何事件**（同类第三次） | **已修**：抽 `desktop.constants.native_event_owner(tenant_id)` 单点；订阅与摘除同源 | 2 测试（纯函数唯一性 + **静态**断言：无固定属主、走共享租户判据）——无 Qt 也可验证 |

### R9（第九轮：API 鉴权覆盖 / 外壳记录器收敛）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R9-1** | API 路由鉴权**覆盖核对**（**未发现缺陷**） | 56 条路由机械枚举：仅 2 条免鉴权 —— `/`（索引页，下发 HttpOnly cookie）与 `/api/webhook`（**自带 `_webhook_authorize` token 校验**）⇒ 与 docstring 口径一致 | 不改（口径已对齐）；方法留档 | 静态枚举（括号配平逐路由判定依赖） |
| **R9-2** | **CLI 侧第三份落盘订阅**（属主固定 + 无 sid 过滤） | `cli._attach_log_persistence` 自持一套实现，属主恒 `"persistence"`(多会话同总线互摘)且不过滤 sid(他会话事件写入本 store) | **已修**：委托 `session_recorder`，属主改 `persistence:{sid}`；S2-4 不变量测试的消费方清单扩到三处 | 单测（过滤 + 属主含 sid）+ 全量回归 |

**方法论（第五轮命中）**：**"同一横切关注点的第 N 份实现"** —— R8/R9 的 4 个缺陷有 3 个是"同一关注点的多处实现里漏了一处判据"（落盘 sid 过滤 ×2：engine/CLI；订阅属主 ×1：原生壳）。**收口方式固定为：抽唯一实现 + 静态不变量锁死消费方清单**（新增消费方必须登记）。

### R10（第十轮：安全边界 · INV-09 全出口脱敏）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R10-1** | **INV-09"全出口脱敏"在生产实际只覆盖 spill 一条路径**（P1 安全）：`ctx.redact`（F016 单口）被 tool_fs / tool_web / spill 三处读取，但**全库无任何装配点注入它** ⇒ 生产恒缺失，fs/web 出口**静默降级为原样返回** | 真实链路实测：`fs.read_file` 读到 `API_KEY=sk-AAAA…`（41 字符）时，`tool.result` summary 含**密钥原文**，且**整条事件流**（→ JSONL / FTS / LLM 上下文）均泄原文；SECURITY §6.4 明列"全出口覆盖=事件 payload/错误消息/tool.result summary/spill/PTY/日志" | **已修**：`config.redactor_of(cfg)` 作为**唯一注入源**（含 `log.redact_enabled` 开关语义），`create_agent` 装配期注入 `ctx.redact`；三个消费方立即生效（spill 原有回落保持不变） | **端到端**安全用例（真实四关管道 → 断言 summary 与整条事件流无原文、已打码 `sk-AAAAAA***`）+ 装配面断言 + 开关逃生口语义 |

**方法论（第六轮命中）**：**"约定被读 ≠ 被注入"** —— 一个"注入面"（facade）若只有读取方、没有装配方，则**所有读取方都会静默走 fallback**（此处 fallback 恰好是"原样返回"）。判据：**对每个 `getattr(ctx, "X", ...)` 的注入面，必须能在装配层找到一个赋值点**；否则它就是"永远为空的缝"。这一条与 R3/R4 的"零调用者"互补：**前者查函数有没有人调，后者查属性有没有人写**。

### R11（第十一轮：注入面全量扫描 / 配置面别名 / 任务关联）

**轴**：静态收集所有 `getattr(ctx,"X",…)` 注入缝 = **58 个**，与全库赋值点取差集 → **15 个无赋值点**，逐个 triage（读出/写入/初始化/恢复/repair/正常写/close-reopen 七问）：

| 缝 | 判定 | 结论 |
|---|---|---|
| `fts_last_seq` | **真断链 → 已修** | `repair._fts_last_seq_provider` 只认 `ctx.session_query/fts_last_seq`，**无任何生产调用方注入**，且 CLI `auto_scan` 连 ctx 都不收 ⇒ **索引落后对账从未启用**（`SessionQueryIndex.max_seq` 的旧 docstring 自认"装配后仍无法启用"）。修：新增只读 `session_query.read_max_seq`（与 `max_seq` **共用同一判据 SQL**）+ provider 第三兜底 + `auto_scan(db_path=…)` ctx-less 入口 + CLI 两处接线 |
| `task_id` | **真断链 → 已修** | `ctx.task_id` **无写入点** ⇒ 恒 None ⇒ `goal` 的"所关联任务失败→目标状态推导"（goal.py:357）与"关联任务"渲染**从未生效**；`todo` 亦恒落默认桶。修：`make_runner` 在任务边界绑定/还原（异常经 finally）；e2e 实证 `goal.created.task_id` 为真实任务 id，且任务结束即还原（R12 二阶复核） |
| `cfg` | **真断链 → 已修** | `tool_fs._cfg_get` **只认 `ctx.cfg`**，装配层设的是 `ctx.config` ⇒ 本模块**所有配置阈值恒回落默认**（`file_spill_bytes`、`max_per_file_bytes`、`credentials.file`、`read_extra_dirs`）。同族三处（session_query/spill/tool_web）一直兼容两名字 → **仅本处漏判据**（同类第五次）。修：兼容 `cfg/config/settings` 三名 + 静态不变量锁死 |
| `read_extra_dirs` | **结构性不可达 → BLOCKED_BY_PRODUCT_DECISION** | 两层都不认例外：`g3`(guard) 只按 workspace 几何判定；provider 对绝对路径先抛 POL-FS-1 ⇒ 运维配了例外目录仍被拒。**方向 fail-closed（不泄漏）** ⇒ 记 **L-24**：放开例外=扩大可读面，属安全姿态决策；已用"钉住当前行为"的用例锁死，未来放开必须先改该用例 |
| 其余 11 个（`get_secret`/`locator`/`round_seq`/`subagent_depth`/`agent`/`log`/`render`/`shell`/`owner_channel`/`search_calls`/`bump`/`repair`） | **安全的休眠缝或扫描假阳性** | 逐项核验：`get_secret`（fail-closed + 当前后端无需密钥）、`locator`/`repair`（文档化可选面 + 直挂属性回落）、`round_seq`（三层回落 + API 路径确有注入）、`subagent_depth`/`agent`/`log`/`render`/`shell`/`owner_channel`（**经构造 kwarg 赋值**，属扫描假阳性）、`search_calls`/`bump`（动态 setattr） |

### R12（第十二轮：并发 / 竞态）

| # | 发现 | 处置 | 证据等级 |
|---|---|---|---|
| **R12-1** | **R11-6 修复的二阶复核**：`ctx.task_id` 是任务边界的**共享可变状态** ⇒ 必须证明不串场 | 用"LLM 阻塞在途"固定窗口：在途绑定当前任务、任务结束**还原**、只落 agent ctx（不污染外壳 ctx） | e2e（确定性窗口） |
| **R12-2** | **同名 sid 跨租户时落盘/引擎/证据订阅属主碰撞** | `owner = f"persistence:{sid}"` 只含 sid，而会话目录按租户分目录 ⇒ 两租户同名 sid **同属主** ⇒ 关/删其一即**摘掉另一租户的落盘订阅**（其会话静默不落盘）。修：属主含**全部隔离维度**（tenant × sid），engine 三处 + 桌面 manager 一处 | 复现（两租户同名 sid ⇒ 只 1 个属主）+ 修后（2 个 + 关 A 不误伤 B） |
| **R12-3** | **审批的"队列联动"只发事件、从不驱动队列**（P3→状态不一致） | 实测（确定性探针，无随机 sleep）：未决期 `queue.suspended` 事件 **=1**，而 `queue.status().paused` **= False** —— 日志宣称"队列已挂起"而运行时从未挂起；受影响的消费方包括 `delete_session` 的 BUSY 守卫（等审批期间**不拦删除**）与 UI 徽章。根因：`TaskQueue.pause/resume` **全库无生产调用者** | **已修**：`ApprovalProvider(queue_getter=…)`（惰性取值、与构造序无关）在有队列时**真驱动** `pause/resume`（队列成为 `queue.suspended/resumed` 唯一写者，无双写）；服务/桌面/引擎三处接线 |
| **R12-3 二阶** | 我的修复让队列**真挂起** ⇒ 挂起态收尾撞上 5s 超时兜底 | 实测 `shutdown` 耗时 **5.01s**（泵阻塞在 `_resume_evt.wait()`）；且我最初的"唤醒泵"补丁**无效**（唤醒后仍因挂起再次等待） | **已修**：泵在 `_closing` 下**无视挂起直接退场**（**不清** `_pause_reasons`，让审批侧 close 后照常补 `queue.resumed` ⇒ 事件对不破）；测试断言收尾 **<1s** |
| **R12-3 三阶** | 泵"收尾即退场"使**收尾后 submit** 的等待者永不释放 | 收尾后仍接新任务 ⇒ 入队即悬挂 | **已修**：`submit` 增收尾闸 → `QUE-001`（**直接构造**：该功能码未登记，`raise_code` 会改写为 CYC-999，见模块偏离 5） | 单测（拒新 + 队深 0） |
| **R12-4~6** | **验证无缺陷**（R12 轴扫尾） | ①**cancel × 审批未决**：审批→denied(APR-502) · 队列→resumed · 等待者→释放 · `suspended/resumed` **严格配对** · `approval_id` == `approval.requested` 的 seq · 终态唯一 · 顺序正确；②**TTL 超时**：到点 denied + 恢复 + 释放（覆盖新增的**同步**恢复路径）；③**跨会话隔离**：A 的审批只挂起 A 的队列，B 的队列与日志**零影响** | —（三项均以 gate 固定窗口的确定性用例证明） | 3 单测 |
| **R13-1** | **验证无缺陷**（失败恢复：幽灵索引行 → 检出 → 重建 → 干净） | 真 JSONL + 真 `SessionQueryIndex`：注入"索引超前日志"行（崩溃现场）→ 经 **R11-2 的 ctx-less 读数口**检出 `index_stale` → `rebuild` 自愈 → 复扫健康。**该用例同时端到端验证 R11-2 的接线在生产形态下可用** | — | 1 单测（全真链路） |
| **R13-2** | **崩溃残片后首写静默丢事件**（数据丢失类） | 崩溃留下的**无换行尾部半行**在未跑 repair 时，下一次 append 会与之**拼在同一物理行** ⇒ 新事件**不可解析**（内存以为已落盘、磁盘读不回）。实测复现:`…"type":"agent.mess{"seq":3,…` 单行不可解析；且文档里的兜底"repair 前置"正是 **R11 发现的死钩子**（`ctx.repair.ensure_repaired` 无实现者）⇒ 桌面/ACP 路径不会自动修 | **已修**：`SessionStore` 在**首写前**给残片补一个 `\n`（`_heal_torn_tail`，三个 flush 入口经 `_write_batch` 单点）：残片自成一（坏）行、新事件完整可解析、**不删任何字节**（残片留待 repair 按"中部坏行"隔离），保持"open/回放不修"的既有口径；轮转后清标记 | 复现（拼接不可解析）→ 修后（残片独立 + 新事件可解析 + 字节保真）· 2 单测（含负空间：干净文件**不**多写字节） |
| **R13-3** | **R13-2 的二阶**：写**中途**失败（磁盘满）会新造残行，而残片标记已被本轮清除 ⇒ 下一轮写又与残行拼接 | 注入"半行后抛 OSError"实测：重试行与残行**拼接成不可解析的一行**（`…"actor"{"seq":1,…`） | **已修**：`_write_batch` 失败时按**可观测事实**重武装（`_tail_looks_torn`：先尽力 flush 把缓冲半行推上盘 → 再 `detect_truncation` 读事实；推不出去才保守认定"可能半行"）。**不猜**：注入"未写任何字节"的失败**不会**平白多出空行 | 新用例（半写→重试→可解析）+ **既有 7 条 durability 用例全绿**（防"修复引入空行"） |
| **R13-4** | **验证无缺陷**（中部坏行 × 运行期追加 × repair 隔离 × 重放一致 三面一体） | 真形态（管理器）：坏行插入中部 → 追加落在其后且行结构完好；repair 恰隔离该行（quarantine 留原文）+ 其余行**字节保真** + 追加 `session.recovered`；可解析事件集不丢、对账水位不受影响 | — | 1 单测（五面断言） |
| **R13-5** | **轮转段损坏**（真源的一部分）**零告警零修复** ⇒ **静默丢事件** | 实测：把 `.1.jsonl` 内**整行**（真实事件）替换为垃圾 → 重放 `[1,3]`（事件 2 丢）而 `repair`（`fixed=[]/quarantined=[]/lost=0`）与 `auto_scan`（`[]`）**都不报** —— 段被按"aux 跳过"（备份/隔离档才该跳过，轮转段是 replay 会读的**真源**） | **已修**：`repair_session` 覆盖本会话轮转段 —— 段级**备份**先做 → 尾部半行截断、中部坏行**隔离不删**（quarantine 留原文）→ 报告出现 `seg1-quarantine-N` 并随 `session.recovered` 落盘声明；其余段行字节保真；二次 repair 幂等 | 复现（静默丢）→ 修后（隔离 + 报告 + 备份 + 留证）· 2 单测 |
| **R13-6** | **空洞判定只看主文件** ⇒ 凡轮转过一次的会话**恒判"不健康"**，每次 repair 都报**假空洞**并追加一条 `session.recovered`（稳定态噪声 + 重复写入；CLI 启动自检也会误报） | 实测：干净轮转会话被 `auto_scan` 判为 unhealthy（`holes=[2]` 之类）；R13-5 用例的**幂等断言**直接撞出该噪声 | **已修**：空洞是**会话级**属性 ⇒ `scan_session` 的 seq 连续性**跨段**判定（新增 `_segment_scan`：读段取 seq/内容末位/声明区间；段不可读则跳过不放大）。**副作用更好**：段内**真实缺 seq** 现在能被启动自检**发现**（原"发现面缺口"随之闭合） | 2 单测（干净段不误报 + 真实缺 seq 被发现） |

### R14（第十四轮：repair × 派生视图重建 × replay/index/watermark 一致性）

**轴**：CND-06（持久化 / repair / 派生视图 / 恢复一致性）。口径 = "真跑一次 + 看外部产物"，
所有结论均有运行时证据（探针前后对照 / 全量回归）。

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R14-1** | **派生视图重建对 CLI/桌面形态结构性不可达**：`_index_handle` 只认 `ctx.session_query`，而 repair 的调用方（CLI 子命令 / 桌面）传的是**外壳 ctx**（无该属性）⇒ "⑤派生视图整体重建"从不执行 ⇒ repair 修完仍 `index_stale=True`（**不收敛**）。同 PIT-19"约定被读 ≠ 被注入" | 真 JSONL + 真 `SessionQueryIndex` + 外壳 ctx：重建**未发生**，复扫仍 stale | **已修**：`_index_handle` 三级解析（① 装配注入面 → ② 兄弟形态 `ctx.fts` / `ctx.engine_spine.fts` → ③ 由 `cfg.storage.db_path` **惰性构造**），自建句柄由调用方 `enter` / `detach` 收尾 | 复现（不可达）→ 修后（可达）· 单测（真句柄全链路） |
| **R14-1 二阶①** | **修复自身引出**：惰性构造的句柄**先 enter 后 detach**，而对账读数 `max_seq(sid)` 在 `finally`（detach 关库）**之后**取 ⇒ 关库后 `max_seq` 恒返 **0** ⇒ 与日志水位(>0)不等 ⇒ **假 PERS-201**（重建已成功却被判失败） | 实测 `log_last=3, view_last=0` → PERS-201 | **已修**：把对账**读数**移入句柄仍有效的 `try` 内；`finally` 的资源释放语义不变（`detach` 幂等 ⇒ 无 double-close、无泄漏） | 复现（修前失败）→ 修后通过 · 单测（真句柄 + Windows unlink 泄漏探针） |
| **R14-1 二阶②** | **水位判据多源**：重建源 `open_store(...).replay` **合并读轮转段**，而 `rebuild_derived_views` 的对账水位只算**主文件** ⇒ 主文件被**整文件半行**截空（或其尾部全为非内容事件）时，段内容使 `view_last>0` 而单文件 `log_last=0` ⇒ **假 PERS-201**。与 `scan_session` 的**会话级**水位口径分裂（CND-06/08 判据单源） | 实测经真 `repair_session` 管线：`log_last=0, view_last=3` → PERS-201 | **已修**：新增 `_session_content_last(path)` = `max(主文件, 轮转段)` 可索引内容末 seq（复用 `_segment_scan` 的同一 `_indexable` 判据），对账与 `scan_session` **单源** | 复现（修前失败）→ 修后通过 · 单测（真管线 Case B） |
| **R14-1 验收** | 按 R14-1 验收条件钉死 **Case A/B/C** + **二次幂等** | — | Case A 段级坏行隔离后**不进入 FTS**；Case B 截断尾部**无幽灵索引**且会话级 watermark 一致；Case C 幽灵行被清除、index 与 replay 一致。三者修后均 `healthy=True` / `stale=False`；**二次 repair/rebuild** 对（`session.recovered` 计数 / 隔离档数 / 索引行集 / 水位）**零变更** | 3 单测（真 JSONL + 真索引 + 真管线，逐项断言不变量快照） |
| **R14-2** | **启动自检把「活会话」当修复候选 ⇒ 第二个 CLI 启动失败**：索引是 200ms **攒批**落的派生视图，活会话在攒批窗口内必然 `view_last < last` ⇒ `scan_session` 判 `index_stale`；自检据此把该会话交给 `_ensure_repair` → `repair_session` 取会话锁撞 **PERS-202** → `_ensure_repair` 只对 PERS-201 特殊处理、其余**上抛** ⇒ `bootstrap_shell` 中止 | **两进程实测**：A 持活会话（store 开、索引未 flush）→ B 启动 `auto_scan` 报该会话 unhealthy → `repair_session` 抛 PERS-202 → 启动中止。属 **R11-2 的二阶**（R11-2 启用索引对账后才产生该暴露面） | **已修**：`persistence.session_lock_held`（**只探测**：同进程 `_LOCKS` 命中 / 跨进程 `try_acquire` 失败 ⇒ 占用；锁文件不存在 ⇒ 不新建、判未占用）+ `_scan_unhealthy` **跳过被其他进程持有的会话**（活会话既非损坏、本进程也修不动）。残余微秒级探测窗口已在 docstring 如实标注 | 两进程复现（修前启动中止）→ 修后跳过 · 3 单测（含跨进程子进程探针 + 负空间「释放后恢复为候选」） |
| **R14-3** | **会话辅助档被当成独立会话**（同一横切关注点的**第 N 份实现**，五处同源漏判据）：正确的辅助档判据只存在于 `repair._is_aux_file`，其余五处各自用 `stem.startswith("s-")` —— 而 `{sid}.1.jsonl` / `{sid}.corrupt-*` / `{sid}.quarantine-*` 的 stem **同样**以 `s-` 开头 ⇒ 守卫**从未生效**（`desktop/sessions.py` 的注释还写着"轮转段/备份非会话文件跳过"） | 实测 1 个真会话（含 1 轮转段 + 1 备份）：① `pyharness search <q>` **直接崩 PERS-202**（备份是主文件**字节副本**，同 `session_id` 同 `seq` ⇒ `rebuild` 撞 `fts_rows` 主键冲突）② 桌面会话列表列出 **3 条**（含"备份伪装成的会话"）③ `budget/stats` 报 **3 会话 / 3 事件**（真值 1 / 3，备份被重复计）④ `job` / `schedule list` 同源误判 | **已修（唯一来源）**：`persistence.is_aux_session_file`（判据）+ `session_log_paths`（**唯一会话枚举点**）+ `rotated_segment_paths` / `session_data_paths`（会话**数据面**＝段升序+主文件，与 `replay` 同序）；`repair` 两处改委托；`cli` 四处与 `desktop.list` 改用枚举点。**按会话聚合**而非逐文件：`_scan_usage`（段+主文件，每事件恰一次，终态取主文件尾）、`job`/`schedule`（新增 `_session_lines` 跨段读且归真实 sid） | 三组探针（修前复现 → 修后全绿）· 6 单测（search 不崩 / stats 不重复计 / job 跨段归真实 sid / 桌面列表排除辅助档 / 判据与数据面 / 锁探针） |
| **R14-4** | **`search` 会抹掉"本次打不开的"会话的索引行**（文档口径是"派生索引**只读**"，实际会重建）：`_cmd_search` 用**全量** `rebuild()`（DELETE 整表再重插），而源只含本次成功 `open_store` 的会话 —— 被活进程持锁的会话入不了源，其索引行被删且不再重建；**所有**会话都打不开时（目录不可读等）整个索引被清空 | 两进程实测：A 持活会话锁 → `search` 退出码 0（无任何报错）→ 被锁会话索引水位 **2 → 0**，静默不可搜（下游 `scan_session` 随后判 `index_stale` 触发 repair，属**自造噪声**） | **已修**：改为**逐会话** `rebuild(session_id=sid)`（只 DELETE 本会话行），打不开的会话索引原样保留。**刻意不**"按目录反推清理"陈旧行 —— 索引库是**全局**的而会话目录按租户分，反推会误删他租户的行（全量 rebuild 原本就有这个隐患，本修反而收缩了它） | 两进程探针（修前 2→0，修后 2→2）· 1 单测（本次会话集之外的索引行必须保留 + 负空间"本次会话确实被索引"） |
| **R14-5** | **只读命令 `search` 持有所有会话的 INV-07 写锁**：`_cmd_search` 借 `open_store` 建索引回放源，而 `open_store` 取会话独占锁并**持有到查询结束**（只在 `finally` 关）⇒ 搜索运行期间**其他进程开会话撞 PERS-202**（措辞是"另一进程正在写/修复该会话"，对只读命令完全误导）。R14-4 的"打不开"根因也在这里 | **两进程实测**（60k 事件、约 1.8s）：搜索运行中并发 `open_store` 探针交替得到 **OPEN_OK / BLOCKED PERS-202**（2/4 被拒） | **已修**：抽出**唯一回放实现** `persistence._iter_replay`（`SessionStore.replay` 与新增 `SessionReader` 共用），新增 `SessionReader` —— **只读**回放源：**不取锁**、不开写句柄、不建文件，只暴露 `replay()`/`quarantine_info()`；`_cmd_search` 改用它。不可读的单个源**告警跳过**（与 `_scan_usage`/`_segment_scan` 同款容错） | 两进程探针（修前 2/4 被拒 → 修后 0/4）· 1 单测（只读回放不得新增会话锁 + 跨段回放 + 不可读源跳过） |

| **R14-6** | **只读命令 `session show` 取会话写锁 ⇒ 看不了正在跑的会话**：`_cmd_session(show)` 借 `open_store` 读（取 INV-07 锁）⇒ 会话正被他进程使用时，**该命令自身**撞 PERS-202 | 两进程实测：无锁 `rc=0`；另进程持锁时 `rc≠0`（PERS-202） | **已修**：改用 `SessionReader`（R14-5 引入的只读回放源） | 两进程探针（修前有锁失败 → 修后 rc=0）· 1 单测（只读不得持锁 + 负空间"确实读出内容"） |
| **R14-7** | **`repair` 子命令对活会话动手 ⇒ 整条命令 PERS-202，且够不到真损坏**：`repair_cmd` 无 sid 时用 `auto_scan` + "取首个不健康"，未跳过活会话（同 R14-2 的攒批假 `index_stale`）⇒ ①唯一"待修"是活会话时**不报"无损坏会话"而直接抛 PERS-202** ②活会话排在前面时**真正损坏的会话永远够不到** | 两进程实测：仅活会话被标记时 `repair` 抛 **PERS-202** | **已修**：抽出**唯一候选筛选点** `_repairable_unhealthy(hits, sessions_dir)`（健康面过滤 + 跳过被他人持有的会话），启动自检（R14-2）与 `repair_cmd` 共用；删除被取代的 `_first_unhealthy` | 两进程探针（修前抛 PERS-202 → 修后 rc=0）· 1 单测（**按最坏顺序**钉死"跳过活会话并修到真损坏" + "仅活会话时报无损坏而不抛"） |

| **R14-8** | **跨租户经派生索引互相可见 / 同名 sid 静默丢索引**（安全相关）：非 `default` 租户的**会话目录**已按租户分（三处调用点一律 `tenant_root()/sessions`），但**派生索引库** `storage.db_path` 仍是**全局**的（`effective_settings` 只覆盖 `llm.*`）⇒ 两租户共用一个 FTS 库：① `query` 无任何租户维度，而 LLM 可见工具 `session.fts_query` 的 `session_id` **取自 LLM 自报参数** ⇒ A 的 agent 可检索到 B 的会话内容；② 同名 sid 跨租户在 `fts_rows` 主键 `(session_id, seq)` 上**碰撞** ⇒ 后写者被 `INSERT OR IGNORE` **静默丢弃**、内容根本不入索引 | 探针实测：共用库时 A 侧**不带 sid** 查询直接命中 B 的条目（`s-tenantb01` → `BBB [CONFIDENTIAL] PLAN`），显式传 B 的 sid 同样命中；同名 sid 场景下 B 的索引**为空**（`read_max_seq=0`） | **已修**：`effective_settings()` 对非 `default` 租户把 `storage.db_path` 一并按租户分（`tenant_root()/index.db`），**与会话目录同域**（"派生视图必须与真源同域"）；`default` 租户行为不变（`profile` 与存储域都无改动时**原样返回** `cfg`，保持既有对象语义）。**残余**：三处调用点仍各自内联 `sessions_dir` 的同一分支（三份**相同且正确**，无缺陷）—— 可后续与本次同法收口 | 探针（修前跨租户命中 / 修后各自独立）· 2 单测（租户索引库分域且 default 不变 + **同名 sid 跨租户不碰撞**且各库只含自身内容） |

| **R14-9** | **空洞合法化判据四份实现 + 判定时机错位**（假 F031 线索 / 假治理发现）：① `declared_ranges`（声明事件→合法空洞区间）在 `repair` / `persistence` / `session` / `governance.audit` **各写一份**，其中 `SessionLog._absorb` **只认 `context.compacted`**、`audit._declared_holes` **漏 `"seq-holes:[…]"`**；② `open_session` **边读边判**空洞，而 `session.recovered` 由 repair **追加在流尾** ⇒ 判定时声明尚未吸收；③ `warn_hole` 报的是**空洞之后那条事件的 seq**，不是缺失的 seq | 探针实测：一次合法修复（`fixed=['quarantine-3','seq-holes:[3]']`）后 `open_session` 仍报"回放遇未声明 seq 空洞: **4**"（缺的是 3），`open_store` + `open_session` 各报一次；治理侧同源 ⇒ 已声明空洞仍报 `SEQ-GAP` | **已修（唯一来源 + 时机后移）**：判据收口到 `events.declared_ranges`（+ `events.DECLARE_TYPES`），四处分发委托；`SessionLog` 新增 `_holes_declared`（与 compaction 的"已折叠"`_folded` **分开**，避免污染重折判据）；`open_session` 改为**收集全部空洞 → 声明吸收完毕后再判定**；告警改报**首个缺失 seq** | 探针（修前告警 2 次 → 修后 `holes_warned=[]` 且 `holes_declared=[[3,3]]`）· 2 单测（真 repair 管线后回放不再重报 + 治理对账认可 `seq-holes`；负空间"真未声明空洞仍报"）· 既有用例口径修正 1 处（告警号由"空洞后一条"改为缺失号本身，与其注释原意一致） |

| **R14-10** | **崩溃留下的未闭合任务段吞掉其后所有任务的证据**（恢复一致性）：`EvidenceCollector._task_at` 把未闭合段（进程被杀 → `_run_task` 的 `finally` 未执行 → 日志只有 `segment.start` 无 `segment.end`）的右界当作**无穷**，又按插入序返回首个命中 ⇒ 该段把其后所有 seq 都算进自己 | 探针实测：taskA 段未闭合 + taskB 段完整 ⇒ `collect_for_task("taskA")` 返回 `['evA','evB']`（B 的证据泄漏到 A），`collect_for_task("taskB")` 返回 **空** | **已修**：判据改为"**起点 ≤ seq 的最近一段**"（段在时间轴互不重叠 ⇒ 未闭合段的右界天然止于下一段起点之前），与 `compaction._segments_before` 对未闭合段的"丢弃"口径方向一致 | 探针（修前跨任务错算 / 修后各归其主）· 1 单测（未闭合段 + 后续完整段：两任务的证据都各归其主） |

| **R14-11** | **验证无缺陷**（桌面/投影层在 CND-06 各面的一致性复核） | — | ① 会话枚举**无重复实现**：web/原生/服务三处 listing 全部委托 `DesktopSessionManager.list()`（R14-3 已修）；② `delete_session` 级联完整（主文件 + `{sid}.*.jsonl` 段/备份/隔离 + `{sid}.jsonl.*` 锁文件，`relative_to` 防穿越，`_locks`/`_owners` 回收）；③ 遥测/治理审计/证据三端点的数据源都是**会话日志**（`require_session` / `from_log` replay-only），不扫目录、不受辅助档影响；④ `redact_args` 经 `render_timeline_node` 可达；⑤ 原生壳 `closeEvent → controller.close() → registry.close_all() → service.shutdown()`（队列排空 → 会话 flush/关 → spine 关），**配对完整**，无"关窗不落盘"；⑥ `require_session` 持有会话柄属**桌面宿主**的所有权模型（该进程本就是这些会话的写者），非 R14-5/6 那类"只读离线命令误持写锁" | 逐项静态核对 + 既有 e2e/单测覆盖 | 

| **R14-12** | **崩溃遗留的后台 job 恒显示 `running`**（claim-vs-reality drift + 恢复一致性）：`core/jobs.py` docstring 声称"崩溃…自动失败并告警"，但**事件级无写入者**——全库仅 `_run` 的 finally 与 `cancel` 写 `job.failed`；进程被杀 → `finally` 不执行 → **无终态**。而 `job list/show` 只看事件（`job.started` 无终态 ⇒ 恒 `running`） | 探针实测：只留 `job.started` 的会话，`job list` 输出 `job:j-1 … running`（一个并不存在的"运行中"任务，永久） | **已修（读侧如实补正）**：job 跑在**持有会话锁**的宿主进程里 ⇒ 锁已释放而仍无终态 = **不可能在跑** ⇒ `job list/show` 判为 `failed(reason=宿主进程已不在(崩溃遗留))`（复用 R14-2 的 `session_lock_held`，只探测不写事件，读命令保持只读）。同时把 docstring 由"崩溃自动失败"改为如实描述，**事件级**恢复写入登记 **L-26**（须先定补写者，避免造出第二事件写者） | 探针（修前 `running` → 修后 `failed` + 负空间"宿主持锁时仍 `running`"）· 1 单测（两态都钉死） |

| **R14-13** | **ACP 外壳退出时收尾被短路**（落盘丢失 + 生命周期不配对）：`cli._flush_session` 的三步（会话日志 flush / `spine.close()` / 会话门面 `shutdown_all()`）被写成**串行且相互依赖**——开头"`ctx.session` 没有 `_persistence` 就 **return**"把后两步一并跳过。而 ACP 外壳的 `ctx.session` 是 `DesktopSessionManager`（真源在各 store 里，**没有** `_persistence`）⇒ ACP 退出时**既不 flush 也不关 store**，与 chat/run 形态（`ctx.session` 是 SessionLog）行为不一致 | 探针实测：manager 形态下 `shutdown_all` 与 `spine.close` 调用次数**均为 0**（收尾被短路）。后果：61 个非强同步事件类型（`agent.message`/`tool.result`/`llm.response`/`task.completed` …）仍留在攒批缓冲，进程退出即丢；engine 的 FTS detach/flush 也被跳过 | **已修**：三步改为**彼此独立、各自判据**——会话日志 flush 仅在可 flush 时执行，`spine.close()` 与 `shutdown_all()` **无条件尝试**（各自 try/except 尽力，不掩盖退出码） | 探针（修前 0/0 → 修后 1/1）· 1 单测（manager 形态两步都执行 / 日志形态 flush 仍发生 / 空 ctx 不抛） |

| **R14-14** | **验证无缺陷**（持久化内部：收尾配对 / 残片×轮转 / fsync 口径 / 空洞判据重复） | — | ① 桌面外壳 `run_desktop` try/finally 的 `shutdown_gracefully → _shutdown_async` 完整（摘订阅 → hub close → **刷盘** → 各租户 `service.shutdown`）；② 轮转与残片自愈无冲突（`_maybe_rotate` 在写路径**之后**，写前必经 `_write_batch → _heal_torn_tail`；即便崩溃残片被旋进段，`_segment_damage` 按 `detect_truncation(seg)` 独立发现，R13-5 已修）；③ `fsync` 仅用于**重写路径**，append 走 `flush()`——与 `specs/persistence.py.md` §原子写"正常 append 路径仍是纯追加句柄"一致，非漂移；④ `seq_holes` 与 `check_seq_gap` 两处找洞口径等价（前缀 + 相邻差 vs `1..max − actual`），组合方式相同 | 逐项静态核对 + 既有单测覆盖 |
| **R14-15** | **写通道暂停后进程内永不恢复**（可用性 + 数据丢失风险）：`_suspended` 的**唯一清除点**是 `SessionStore.repair()` 的第 ⑦ 步，而该旧面**生产零调用者** ⇒ 磁盘故障达阈值（`_fail_streak ≥ N` 或重试队列越界）后，`append` 恒被"拒新不丢旧"挡回 PERS-202，而定时 `flush()` 只反复重写重试队列、**永不解除暂停**；`_enter_failure_state` 给的指引"跑 repair(F060)"在**本进程内不可执行**（外部 `repair` 还会被该会话的锁挡在 PERS-202），且进程一旦退出，重试队列（内存）里的行**静默丢失**且无 `session.recovered` 声明 | 探针实测：打满阈值后 `_suspended=True`、暂停期 append 被拒 PERS-202；**修前**恢复写通道后 `flush()` 成功仍 `_suspended=True`（死锁在暂停态） | **已修（复用既有路径，不引入新调用方）**：`flush()` 成功即视为"写通道确已恢复" ⇒ **就地解除暂停**（recovering→normal）；恢复由**既有** `EngineSpine.start_flush_ticker` 定时器自动完成（它本就在重试 `flush()`）。R13-2/R13-3 已把"残片拼接"风险在写时解决，故不再需要"必须跑 repair 才能恢复"的旧口径。同时修正 `system.error` 指引文案 + persistence 模块 docstring（不再自称 F060 修复入口、更正备份名口径） | 探针（修前恢复后仍 `_suspended=True` → 修后 `False` 且 append 成功；拒新语义保持）· 既有 GAP-2 用例增补"自动解除"断言 · 8 条新静态不变量 |

### R15（第十五轮：CONSTRAINTS-03 工具安全运行时核对）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R15** | **验证无缺陷**（T-01~T-10 十条约束逐条核对 + 邻近面实测） | — | ① **四关次序**：`execute()` = 契约 lookup → `validate_args`（先验后跑，失败**立即返回**）→ `tool.call` 存档 → `authorize`（关 2）→ 审批（关 2.5）→ provider；`workflow`/`orchestration`/`subagent` **无直连 provider 旁路**；② **g-overwrite**（覆写转审批，含 `mode=append` 读改写）**默认探针** `os.path.exists`，不依赖注入（不会因装配缺失而 fail-open）；③ 危险分级：`fs.write_file=low` + 覆写经 g7 转审批、`fs.delete_file=critical`（注册即拒，不可审批）；④ T-08 输出 schema 校验**已实现**且 `output_schema` 属**可选**声明（不在 T-05 五要素内）⇒ "无工具声明"不构成缺陷；⑤ **containment 实测**：`..` 越界（POL-FS-2）/ 绝对越界（POL-FS-1）/ **前缀撞名兄弟目录**（POL-FS-1）/ **junction 指向外部**（POL-FS-3，读+写）**全部拒绝**；`_under` 用 `rstrip("\\/") + os.sep` 带分隔符边界（非字符串前缀） | 逐项静态核对 + **越界探针**（Windows junction 实测）+ 既有 35 条安全用例 |

### R16（第十六轮：LLM 出网唯一汇点）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R16-1** | **出网"唯一汇点"的静态防线只写在 docstring 里，闸不存在**：`LLMClient._egress_guard` 的 negative space 声称"适配器层 `OpenAICompatAdapter.chat/chat_stream` 已核验全库无生产代码直接调用（**CI 静态闸扫此**）"，但全库**没有任何测试**扫描此事 ⇒ 该**安全性质**无自动防线：任何一处新写的 `adapter.chat(...)` 会**静默绕过会话级预算硬闸**，且不会让任何用例变红 | 全库检索：无 `静态闸`/adapter-scan 类测试；`OpenAICompatAdapter` 目前只出现在 `core/llm.py`（定义）与 `engine.py`（注册）——**现状正确，防线缺失** | **已补闸**：新增 `tests/invariants/test_inv_llm_egress_chokepoint.py`（3 条）——① 具体适配器类只许出现在定义处/注册处 ② `core/llm.py` 内对适配器实例的**动态派发唯一**（`getattr(inst, method)` 恰 1 处）③ 五个公开出口（`chat`/`chat_stream`/`mini`/`summarize`/`json_chat`）**都**经 `_chat_any` | 静态闸自证（现状绿）+ 既有 `tests/security/test_llm_egress.py` 行为面 |
| **R16-2** | **文档漂移（含本轮自身引入）**：§1 声明 `tests/invariants/` 为"157 用例 / 10 文件"（实测 **168 / 11**）；`authorize()` 写作"AST 判定**恰 1 处调用**"（实测**恰 1 处调用方**，同模块内 D1 + 审批重入 D2 共 2 次调用） | 计数实测：invariant 168 用例 / 11 文件；`authorize(` 调用点 2（同模块）+ 定义 1 | **已修**：§1 计数与措辞同步实测值 | 实测 + `tests/security/conftest.py::authorize_call_sites`（AST 判定调用方模块集合） |

### R17（第十七轮：负空间声明复核）/ R18（第十八轮：外壳装配入口）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R17** | **验证无缺陷**（把 R16-1 的模式推广：凡自称"已核验/全库无/静态闸/AST 判定/由测试锁死"处，逐条查防线是否存在） | — | 全库检索此类声明：①`llm.py` "CI 静态闸扫此" → **缺失**（R16-1 已补）②`desktop/constants.py` "由静态不变量锁死:属主串必须含租户" → `test_shell_parity.py:138` **存在** ③`session.py` "方法面由测试钉死" → `test_session.py` 禁令片段断言**存在** ④`tools_guard.__getattr__` 结构防线 → `test_governance_bypass.py:62` **存在** ⑤`decision.py` "值域由测试锁定为逐一对应" → `test_governance_decision.py:47-48` **存在** ⑥`llm.require_adapter` "零生产调用者" → LIMITATIONS **N8 已登记** ⑦`decision.CHANNELS` "受守卫的重复" → `test_inv_channel_contract.py` **存在** | 逐条静态核对 + 引用对应用例 |
| **R18-1** | **验证无缺陷**（零调用者扫描的高信号子集：docstring 声称有生产职责却零调用者） | — | 全量 635 个公开 `def` 扫描 → 高信号子集 3 项，逐项核验：①`cmd_exit` 经 `register_command` **注册后动态派发**（假阳性）②`run_uvicorn` 经 `threading.Thread(target=...)` **作值传递**（假阳性）③`assemble_real_engine` 真零调用者（见 R18-2）。**延伸验证**：`assemble_real_engine` 显式传 `counters=spine.counters` 而两条生产路径不传 ⇒ 疑似"预算计量不同源"，**实测排除**：适配器未注入 counters 时**调用期回落 `ctx.counters`**（`llm.py:735-740`），而 `create_agent` **无条件**把 `spine.counters` 播到 agent ctx（`core/agent.py:494`）⇒ 与 scope 预算闸同源，**无缺陷** | 静态扫描 + 计数器探针（`adapter.counters is spine.counters` 为 False 但回落路径成立） |
| **R18-2** | **`assemble_real_engine` 的 docstring 谎称用途**：称"desktop create_message 用"，而 desktop/服务层实际走 `build_runner_components`（F-08 早已登记"`pyharness/` 内零调用者"，但 docstring 一直未更正）⇒ 读者会误以为生产走该路径。实际使用方是 `scripts/`（9 处 demo/probe/e2e） | 全库引用检索：`pyharness/` 内 0 处、`scripts/` 9 处；F-08/F1-R1C 复核报告均记为 DEAD | **已修**：docstring 改为如实描述（演示/探针/e2e 入口 + 生产各壳走哪条 + 计数器同源的**实际机制**）；新增静态不变量 `test_inv_shell_assembly_entries.py`（3 条）钉死"谁走哪条装配路径"+"生产不得走 demo 入口"+"摘要行不得再声称生产用途" | 引用检索 + 静态闸自证 |

### R19（第十九轮：错误码纪律 —— `raise_code` 的码必须已登记）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R19-1** | **把「原因」当「错误码」用 ⇒ 拒绝静默降级为 CYC-999**：`skill_registry` 的路径穿越拒绝用 `raise_code("POL-FS-2", ...)`，而 `POL-FS-*` 是 **guard 拒绝原因**（ERR.md §2.11），不是错误码 ⇒ 未登记 ⇒ **改写为 CYC-999**：同一"路径逃逸"在 `tool_fs.resolve_in_workspace` 报 `GRD-401/POL-FS-2`、在技能安装路径报"未知内部错误"（**安全语义在审计/HTTP 上丢失**） | 静态扫描 503 个 `raise_code` 字面量码 → 3 处未登记；`errors.py::raise_code` 对未登记码的行为实测为 `code="CYC-999"` + `ctx.unknown_code=<原码>` | **已修**：`GRD-401` + `reason="POL-FS-2"`（与 `tool_fs` 同款同判） | 静态扫描 + 既有原因→码约定对照 |
| **R19-2** | **功能特性码 `ATT-001` 走了 `raise_code`**：`attachment` 落盘失败分支用 `raise_code("ATT-001", ...)`，而**同一模块**的统一拒绝出口 `_att_reject` 是**直接构造**（模块 docstring 明写"errors.py 未登记 → 直接构造，同 QUE-001 先例"）⇒ 只有**这一个分支**报 CYC-999 | 同上扫描；`ERR.md §2.11` 明确 `ATT-001` 是已登记**功能特性码**、`errors.py` 故意不收 | **已修**：改走本模块统一出口 `_att_reject`（顺带去掉该文件已无用的 `raise_code` 导入） | 静态扫描 + `test_inv_error_code_registry` 行为断言 |
| **R19-3** | **`BUSY` 在服务层走了 `raise_code`**：`application/service` 的"会话忙禁删"用 `raise_code("BUSY")`，而 `acp.py` / `core/agent.py` / `task_queue.py` **三处文档都写明**"BUSY 未登记 ⇒ 直接构造 PyHError" ⇒ 同一"会话忙"在 ACP 报 `BUSY`、在删除路径报 `CYC-999`（HTTP 落 500 而非 409） | 同上扫描；`grep '"BUSY"'` 显示三处直接构造 + 一处 `raise_code` | **已修**：直接构造 `PyHError("BUSY", ctx=…)`，与三处先例一致；并加注说明两类码的分工 | 静态扫描 + 现有 ACP 忙拒用例 |
| **R19 收口** | 加**静态闸**防复发 | — | 新增 `tests/invariants/test_inv_error_code_registry.py`（3 条）：① 所有 `raise_code(<字面量>)` 的码**必须在 `ERRORS` 中**（现状 0 例外）② 钉住"未登记码→CYC-999"的改写语义（闸存在的理由）③ 功能特性码必须**直接构造**且保住字面码（以 `ATT-001` 为样本） | 静态闸自证 + 行为断言 |

### R20（第二十轮：CONSTRAINTS-08 失败模式核对 + 文档锚点漂移）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R20-1** | **约束文档的"最大轮数默认值"写错**（硬约束写 10，代码是 30）：`CONSTRAINTS-02-LLM` / `CONSTRAINTS-07-Cost`（2 处）/ `CONSTRAINTS-08-Failure` 共 **4 处**称"默认 10 轮；失控在 10 轮内必然终止"，而代码/权威文档**四处一致为 30**（`CFG.md §3.1` L1 唯一权威 = 30、`PRD-Core.md §5.2`、`PARAMETER-ANCHOR.md`、`config.py` 的 `DEFAULTS`/`Field`） | 文档与代码逐字对照；`CONSTRAINTS-*.md` 是 CLAUDE.md §1 明列的**硬约束**读取源 ⇒ 照它实现/写测试会用错默认值，且"10 轮内终止"的结论不成立 | **已修**：4 处改为 30 并注明权威出处（`CFG §3.1 L1`） | 文档↔代码逐字对照 |
| **R20-2** | **验证无缺陷**（CONSTRAINTS-08 其余场景） | — | F-01 降级链→LLM-399 / F-02 429 不重试同模型（`_RETRYABLE_CODES` + 退避上限 4）/ F-03 损坏→repair（R13/R14 全面覆盖）/ F-04 磁盘满→PERS + 暂停→**R14-15 起可自动恢复** / F-05 崩溃→强同步已落盘 / F-06 轮数闸 `max_turns` ✓ / F-07 预算闸 `check_budget` ✓ / F-08 配置校验 `CFG-601` ✓ / F-09 工具超时 `TLB-805` ✓ / F-10 备份恢复（`.corrupt-` 备份 + 回放一致，R14-9 已修声明/回放口径）；**FR-03 `/status` 已注册**（`register_command("status")`，斜杠命令表 5 条：help/status/cost/new/exit） | 逐条静态核对 |
| **R20 收口** | 加**文档锚点漂移闸** | — | 新增 `tests/invariants/test_inv_doc_anchors.py`（7 条）：① `CFG.md` **76 行默认值锚点**逐行解析并比对 `config.DEFAULTS`（**当前 0 漂移**）② 约束文档的轮数上限声明必须等于真值 ③ 关键锚点必须在 `DEFAULTS` 里真实存在（防死声明）。**这是本仓反复出现的漂移类的通用防线**（N9/R16-2/R18-2/R20-1 同源） | 闸自证（76 锚点全绿）+ 解析行数下限断言（防表格改格式后闸静默失效） |

### R21（第二十一轮：CONSTRAINTS-07 成本约束核对）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R21-1** | **约束文档的配置键名批量失真**：`CONSTRAINTS-02-LLM.md:76` 与 `CONSTRAINTS-07-Cost.md:108`（**验收清单**）点名的键中，`llm.timeout.connect` / `llm.timeout.read` / `llm.retry.max_attempts` / `llm.budget.per_task` / `llm.budget.per_task_cny` / `llm.cost.model_prices` / `llm.max_turns` **共 10 处引用在 `config.DEFAULTS` 里都不存在**（真名：`llm.timeout.connect_s` / `llm.timeout.first_token_s`·`total_s` / `llm.retry.attempts` / `budget.task.max_cost_yuan` / `llm.usage.unit_price` / `loop.max_turns`） | 按配置段前缀（`DEFAULTS` 顶级段）扫描 4 份约束文档：10 处解析失败。`CONSTRAINTS-*.md` 是 CLAUDE.md §1 的**硬约束**读取源 ⇒ 照它写配置会撞 `CFG-601` 或静默不生效；§11 的"验收清单"因此**无法据以实现** | **已修**：10 处全部改为真名并注明"真名以 `CFG.md §3` 为准"；`budget.check()` 伪码改为真实 API `scope.check_budget()`（超限抛 `BudgetExhausted` → 终态 `reason=budget`） | 静态扫描 + 逐处对照 `DEFAULTS` |
| **R21-2** | **「预算硬闸只读 token 数」的说法与实现相反**（代码注释 + **规格** `specs/llm.py.md:12`）：实测**纯成本超限即拦**（`Scope.budget_state` 与 `BudgetGate._compute_state` 均为 `out/in/cost_est` **任一** ≥ 上限 → exhausted；`BudgetLimits.from_cfg` 把 `budget.task.max_cost_yuan=1.0` 接进 limits） | 探针：`max_cost_yuan=1.0`、token 远未达、`cost_est=1.5` ⇒ `budget_state()='exhausted'`；`from_cfg` 实测 `max_cost_yuan=1.0` | **已修**：代码注释与规格措辞改为"判据 = **token 与 cost 并列**（cost 由**软约束**单价表 `llm.usage.unit_price` 估算，上限 `budget.task.max_cost_yuan`）" | 运行时探针（纯成本超限即拦）+ `from_cfg` 读数 |
| **R21-3** | **验证无缺陷**（成本约束其余面） | — | C-01 单任务 ¥1 硬闸 ✓（探针实证）；C-03 月预算 `budget.monthly.limit_yuan`（默认 0=未启用）✓；C-08 每轮记账 `report_usage → llm.usage → UsageCounters` ✓（可整体重建）；C-09 `/cost` 已注册 ✓；C-06 输出有界 ✓（T-09 spill 2048）；C-04 轮数闸 ✓（R20-1 已修默认值） | 探针 + 静态核对 |
| **R21 收口** | 扩**文档锚点闸** | — | 在 `test_inv_doc_anchors.py` 增第 4 条：**约束文档引用的 `配置段前缀.xxx` 必须能在 `DEFAULTS` 里解析**（按后缀排除文件名类误匹配）。与 R20 的 CFG 76 锚点闸同属"文档漂移"通用防线 | 闸自证（现状 0 死引用） |

### R22（第二十二轮：文档锚点闸自身的解析面加固）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R22** | **R20 引入的闸自身有假阳面**：`_resolve` 只查 `config.DEFAULTS` 字面量，而**部分默认只定义在 `Settings` 模型字段上**（如 `security.policy.preset`：`DEFAULTS["security"]["policy"]` 仅 `deny_tools_extra`/`read_extra_dirs`，`preset` 在模型里）⇒ 文档引用这类键会被本闸误判为"死引用"。属 R20 的**二阶缺陷**（自造防线自身失真） | 探针：`_resolve("security.policy.preset")` → `None`（假阳），而该键确实存在（`config.py:649` `preset: Literal["locked","readonly","standard","strict"]`） | **已修**：`_resolve` 改为**两段解析**——先 `DEFAULTS` 字面量，再沿 `Settings` 的 pydantic 字段注解树递归。加固后 `preset` 解析为 `Literal['locked','readonly','standard','strict']`，而**真死键**（`llm.budget.per_task_cny`）仍返回 `None`（判别力保持） | 探针对照（加固前 None → 加固后 Literal）+ 闸 8 条全绿 |
| **R22 附带** | 复核被宽扫标红的疑似项 | — | 逐项核验：`storage.kv` 是**能力名**（`plugins.pre_activate` 默认值里的 id）+ `core/kv.py` 实际存在 ✓；`security.policy.preset` 存在 ✓；`llm.degrade.*` / `budget.*` / `config.*` 等 72 条标红中**绝大多数**是"事件名/API 名与配置段前缀撞名"的扫描假阳（`llm.response` ×46、`llm.chunk` ×32、`log.info` ×7、`loop.wake` …）⇒ **不建宽面闸**（会充满假阳，与 R4-3 的结论一致：先逐项核验再决定是否入闸） | 逐项核验 |

### R23（第二十三轮：SECURITY.md 守卫链声明核对）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R23** | **`SECURITY.md` 声明的 guard 链是「修复前」的求值序**（硬约束文档）：表述为 `[g-schema, **g-danger**, g-fs-path, g-credential-read, g-net-outbound]`+钩子(g-exec/g-overwrite)，把 `g-danger` 排第 2；而代码 `_BUILTIN_IDS` = `[g-schema, g-fs-path, g-credential-read, g-exec, g-overwrite, g-net-outbound, **g-danger**]` —— **danger 殿后**，且代码注释明写理由："分级只决定是否需要人类审批、**不短路形状 guard 求值**；修复前 `g-danger` 在 g2 短路，使 `g-exec`/`g-overwrite` 对 `danger=high` 工具**恒为死代码**" | 逐序对照文档 vs `_BUILTIN_IDS`（7 个 guard 的顺序与"钩子"归类均不同）。照旧文档实现会**复现该安全缺陷**（high 工具的 shell/cwd/strict 约束与覆写审批在人类批准前从不求值） | **已修**：SECURITY.md 改为真实求值序 + 说明"danger 必须最后"及其**后果**（提前短路 ⇒ g-exec/g-overwrite 死代码），并标注链序源 = `tools_guard._BUILTIN_IDS` | 逐序对照 + 下条静态闸 |
| **R23 收口** | 加闸：文档链序必须逐序等于代码 | — | 在 `test_inv_doc_anchors.py` 增第 5 条：解析 `SECURITY.md` 的方括号链并与 `_BUILTIN_IDS` **逐序**比较（顺序是安全语义的一部分，不是风格问题） | 闸自证（现状一致）+ 解析失败即 RED |

### R24（第二十四轮：A/B/C 三类待办全量处置——用户授权代行产品裁定）

| 类 | 项 | 处置 |
|---|---|---|
| **C1** | L-20 死配置 12 个逐个判「接线 or 如实标注」 | **接线 3 个**：`loop.message_edit_window_s`（编辑窗 `EDT-001` **此前根本不存在**，现接到唯一编辑入口）、`storage.spill.max_per_session_mb`（会话总量闸此前只有**单文件**闸 ⇒ 可反复 spill 撑爆磁盘；现按目录字节+本次判上限）、`storage.archive_days`（见 A3）。顺带把单文件闸由**字符数**改为**字节**（与键名 `max_per_file_bytes` 同口径、非 ASCII 更严）；其余 9 个保持登记并**修正理由**（如 `step_timeout_s` 实为「等价能力已由**工具级** `defn.timeout_s` 承载」的冗余声明） |
| **C2** | R14-8 残余：`sessions_dir` 分支三处内联 | 收成单点 `ApplicationService._tenant_sessions_dir()`（与 `storage.db_path` 的租户分域配套） |
| **C3** | L-14 `ag.ctx.persistence` 死传播 | 复核后判定为**可用门面**（spec `desktop.py.md:266` 曾规定 `flush_sync()` 兜底，已被 R14-13 的真实 flush 路径取代）⇒ docstring 如实标注，不再算「死传播」 |
| **C4** | L-17 `call_id` 存放位置不统一 | 抽 `events.call_id_of(env)`（payload 优先、回落 trace）为**唯一读取点**，全库 5 处读取点改走它（读侧本就各认对一侧，此举防**未来**新增读取方漏判） |
| **A1** | L-23 `shell.web.host/port` 零读取者 | 新增 `resolve_bind(cfg)`：**host 仅 loopback**（其他值 CFG-601 拒绝启动，保住安全姿态）、port 为 0/被占用则回落空闲口 |
| **A2** | L-24 `read_extra_dirs` 结构性不可达 | `ScopePolicy.read_extra_dirs` + **两层同判**（g3 与 provider）**只读**放行、写仍拒；钉住用例改写为正向 |
| **A3** | L-21 会话删除即永久删 | `archive_days` 归档（同域 `archive/sessions/{sid}-{ts}/`）+ 保留期清理；归档不可用则**拒绝删除** |
| **A4** | KF-C 到点语义 | 框架侧来源 `task.meta.source` → `ag_ctx.turn_source` → 受控系统提示段 `[本轮来源]`（不动事件/减少器/compaction）；O-1 **裁定=真的发出提醒** |
| **A5** | KF-D 关闭后是否执行 | O-2 **裁定=不执行**（维持进程内模型，与单写者锁同生命周期），L-16 记为范围裁定 |
| **B1** | L-26 崩溃 job 事件级恢复 | 新增 `JobManager.recover()`（补 `job.failed(reason=crash)`，幂等、唯一写口）+ `run_for_task` 幂等闸调用 |
| **B2** | L-27 两套修复策略并存 | `SessionStore.repair` 改**委托**权威 `repair_session`（含句柄安全序），~6 个早期面用例迁到生产语义 |
| **B3** | L-3 审批复合键 | **复核后无需改键**：provider 按会话唯一 ⇒ 裸 seq 按构造即正确；跨会话歧义由 `provider_owning` 报 `EVT-101` 并指路 `{sid}/{aid}`（已有对抗用例） |
| **B4** | L-15/L-16 调度语义与执行模型 | 同 A4/A5 |

**证据**：全量回归 **2156 passed / 0 failed / 4 skipped**；`test_persistence.py` 早期面用例已迁到生产语义（6 条）；新增/改写用例覆盖上表每项。

### R25（第二十五轮：CONSTRAINTS-04 SessionLog + EVENT-SCHEMA 锚点核对）

| # | 发现 | 修复前实测 | 处置 | 证据等级 |
|---|---|---|---|---|
| **R25-1** | **硬约束文档 S-04 的强同步规模失真**：`CONSTRAINTS-04-SessionLog` 写「强同步**三类**」，`EVENT-SCHEMA §1.2` 同；而 `events.vocab.SYNC_TYPES` 实为 **14 型**（治理/编排型事件后续追加：`decision.issued`/`receipt.emitted`/`policy.updated`/`session.finished`/`segment.start`/`fork.created`/`context.compacted`/`session.recovered` 等）。`§8.1 落盘矩阵` 只列 **8** 型 ⇒ **同一份规格里三个不同数字** | 逐处对照 `SYNC_TYPES`（14）。照旧规格实现会**漏强同步**——而崩溃时丢的正是"声明/裁决/终态"这类**语义崩坏**面 | **已修**：`§1.2` 保留"原始三类族"叙述并显式写明**现共 14 型、唯一真源＝`events.vocab.SYNC_TYPES`**；`§8.1` 强同步行改为**全 14 型清单**并补理由（治理证据不持久则重放/审计语义崩坏）；`CONSTRAINTS-04 S-04` 同步 | 逐处对照 + 新静态闸（下条） |
| **R25-2** | **S-05 引错错误码**：`CONSTRAINTS-04` 写「事件格式非法 → 拒绝写入(**EVT-105**)」，而 `EVT-105` 是「**未知斜杠命令**」；事件校验链用的是 **EVT-100**（信封/载荷非法） | `errors.py` 码表 + 全库用途核对（`EVT-105` 仅出现在斜杠分发与 HTTP 映射） | **已修**：改为 **EVT-100** 并注明 `EVT-105` 的真实语义（免再误引） | 码表对照 |
| **R25-3** | **`§7.3` 的 `since_version` 元数据未实现**（行为已实现） | `grep since_version` 全库 0 命中，而 §7.3 要求"每个事件模型登记 `since_version`" | **已澄清**：该条**行为**部分（未知类型回写 `EVT-102` 拒 / 读侧记跳不中断）**已实现**；逐模型 `since_version` 元数据**未实现**（无消费者，按 `KNOWN_UNIMPLEMENTED` 的「无外部读者属预期」类登记），文档改为如实分述 | 静态核对 |
| **R25 收口** | 加**强同步锚闸** | — | `test_inv_doc_anchors.py` 增第 6 条：两份文档都必须写明「`len(SYNC_TYPES)` 型」，且 `§8.1` 矩阵必须点名全部治理型强同步事件 | 闸自证 |

### R26 ~ R30（D 类五轴：硬约束 / 测试 / LLM / 规格 / 文档数字）

| 轮 | 轴 | 发现与处置 |
|---|---|---|
| **R26** | **L-1 闭合**（跨租户隔离残余） | 新增 `tenant_of_log(path)`（**只读**尾部窗口取落盘 `tenant_id`，与 `open_session` 同口径"末条胜出"）+ `DesktopApp.resolve_request_tenant()` 三段解析：①进程内登记 → ②**落盘事实** → ③两者皆不可得且**日志在盘** ⇒ **返回 None（403 拒绝，绝不回落客户端自报头）**；会话不存在时才回落头（该路由只会 404，无数据可泄）。**3 条对抗用例**（伪造头夺不走落盘归属 / 无租户拒绝 / 会话缺失才回落）  **⚠ 该三段解析已被 R31-1 取代**（"有会话必须有归属"保留，但归属不再是"解析序第①②步"，而是"**声明必须等于权威归属**"；且"单一命中即改判"被实测证伪为跨租户读取，见下文 R31 段） |
| **R26** | CONSTRAINTS-01-Hard 逐条 | ①4 条验收 grep 全过（无 TS 残留 / 无禁用依赖 / 无第二份历史 / guard 无放行语义）；②**H-02 与实现相反**（原文"仅允许 openai/pydantic/pytest"——列了**未用**的 `openai`、漏了在用的 `PyYAML`/`httpx`）→ 改为实际依赖集；③**H-03 锁工件名错**（写 `requirements.lock`，实际是 **`uv.lock`**，960 行/44 包）→ 更正。新增静态闸 `test_inv_hard_constraints.py`（5 条 + CI 一致性 1 条） |
| **R27** | CONSTRAINTS-06-Testing + L-8 | ①**TS-06 违规**：43 个已登记码中 **`CRED-702` 全库无测试** → 追查发现它**已登记但从不抛出**（文档承诺的「凭据文件权限过宽(>600) → 启动拒载」**不存在**）→ **接线**（POSIX 查 mode 的 group/other 位）+ 补用例 + **闸**（每个已登记码必须有测试引用）；②**TS-10 满足**：核心脊柱逐模块实测 engine 84 / persistence 85 / repair 87 / errors 98 / agent 89 / approval 81 / orchestration 94 / scope 95 / session 98 / session_query 84 / task_queue 90 / tools_executor 90 / tools_guard 92，**总覆盖 80%**；③**L-8 收窄**：CI 的每条命令（安装/自检/全量/**覆盖率门禁**/四层门禁/**authorize AST 闸**）**已在本地等价复跑通过**，`COV_FLOOR=75` 已接 `--cov-fail-under`，剩余未验证面**仅** GHA 运行时（需 push） |
| **R28** | CONSTRAINTS-02-LLM 余面 | ①**L-01 与实现相反**（写"openai SDK"，而实现**刻意不用 SDK**、自研 httpx 传输，见 `llm.py` 偏离 2）→ 更正；②**L-04 措辞**（"连接/读超时分开"→实为**三档** connect/first/total，含 `connect<first<total` 校验）→ 更正；③ L-02/L-03/L-05/L-06/L-09/L-10 逐条核对**与实现一致**（fallback=qwen-max、`env:DEEPSEEK_API_KEY`、retry 4/1.0s/±30%、degrade 连续 2 次限流） |
| **R29** | `docs/specs/*.md` 逐模块契约 | ①**机械核对 32 份规格的 221 个声明符号 → 代码中缺失 0**；②**"强同步三类"措辞全域过期**（8 份 spec + 10 份文档）→ 统一为「原始三类族」（列举三者时）/「强同步事件(`SYNC_TYPES`)」（未列举时），并加闸禁止该措辞再现 |
| **R30** | 约束文档数字/键名逐条 | ①配置键引用闸（R21）②`max_turns` 闸（R20）③强同步规模闸（R25）④guard 链逐序闸（R23）⑤**事件/载荷/信封规模闸**（新增：`EVENT_TYPES` 77 / payload 77 / `SYNC_TYPES` 14 / `TRANSIENT_TYPES` 3 / 信封字段 10，与 LIMITATIONS §1 声明比对） |
| **L-20** | 死配置 12 个的后续 | **又接线 2 个**（本轮累计 5）：`plugins.priority`（`_preload_plugins` 按小者先装载）、`plugins.deadletter_samples`（`EventBus` 背压丢弃时留有界样本）；并按防线规则**移出** `KNOWN_UNIMPLEMENTED`。**其余 7 个给出精确的不接线理由**（非"没做"）：`step_timeout_s` 冗余（工具级 timeout 已承载）、`tool_concurrency`/`thread_pool_size` **与 F026「连败 2 次即终止本轮后续调用」的顺序语义冲突**（接线＝重定义安全规则）、`proc_mem_limit_mb` POSIX-only（本机 Windows 不可验证）、`cache_ttl_s` 缺凭据缓存子系统、`ctx_lazy`/`pre_activate` 需 agent ctx 而装配期尚未创建（装配序问题）；CFG.md 已逐行标注**「未生效」** |
| **L-4 / L-12 / L-22** | 产品裁定项 | 均由用户授权**作出裁定并落档**：L-4 维持"预算闸即 LLM 出网治理面"（接入 `authorize()` 须伪造 ToolCall、污染工具链语义）；L-12 维持四个标识符不在契约内；L-22 残余（魔数定类型 / 不做引用计数）维持现状（工作区生命周期管理，无悬空引用） |

### R31（L-1 租户隔离对抗验证）

| 轮 | 轴 | 发现与处置 |
|---|---|---|
| **R31-1** | **L-1 归属预言机**（跨租户**读取**） | 实测**两版都漏**：①**HEAD（已提交）**把归属交给目录——信封 `tenant_id=beta` 的会话落在 default 分区（全局 sessions 目录）时，`header=default`（甚至**不带头**）即 **200 + 他租户正文**；②**R26~R30 段未提交的前版**更彻底——按权威目录**改判**归属，`header=alpha/default/无` 对 beta 会话一律 200（连"声明"都不需要）。根因：**"声明"与"归属"混为一谈**（头要么决定归属、要么完全不参与，从未校验 `声明 == 归属`），且进程内全局认领表被置于声明之前 ⇒ **跨请求污染**（任一请求登记过某 sid，后续任意声明都被改判到该租户）。**修复**：`resolve_request_tenant()` 重写为 fail-closed 不变式——*头只声明分区，不决定归属，服务端不跨租户改判*：①无 sid ⇒ 声明；②归属 = 认领集合 ∪ **全部权威目录**落盘事实；③**归属不可得**（文件在盘但未载租户）或**歧义**（多租户同名 sid）⇒ **拒**；④声明 ≠ 归属 ⇒ **拒**；⑤任何权威来源都查无此会话 ⇒ 回落声明（只会 404，无数据可泄）。配套：新增 `session_tenants()` 区分"无人认领"与"多租户抢认"（二者处置**相反**）；`_disk_owners()` 把"读不出归属"与"无命中"分开；**补上 SSE `?sid=` 的归属判定**（此前 sid 只在查询串，整条流式面绕过）。**证据**：对抗矩阵 **16/16** + 二阶攻击 **15/15** + 全量 **2179 用例 exit 0**；每格均有 200/403 + 正文零泄漏的端到端实测。**代价**：信封无 `tenant_id` 的历史会话经 HTTP 一律 403（fail-closed 的应有之义） |
| **R31-1-F** | **订阅属主碰撞**（跨租户**干扰**，非泄漏） | `SessionQueryIndex._owner` 是**模块常量** `cap:session_fts`，而索引是**每会话一个**、库按租户分域（R14-8）、总线在多租户桌面下**共享** ⇒ 任一实例 `detach()` 的 `unsubscribe_all(owner)` **连坐**其他租户/会话。走真实 `engine._activate_session_caps` 实测：2 租户共 **14** 条订阅被一次摘光 ⇒ 未摘净的一方检索**静默变陈旧**（无异常、无告警）。**已修**：属主 = 常量前缀 + 库路径（含租户）+ 会话集（14 → 7：甲 detach 后乙 7 条存活）。回归用例**先断言伤害面**（乙租户索引不再收事件）**再断言机制面**（属主不共用），已验 RED→GREEN |


**方法论（第十四轮命中）**：**"修复自身的二阶缺陷"** —— 修 A 时新引入的句柄生命周期与判据口径，
必须与原路径**同轴复核**（R14-1 两处：读数落在 `finally` 之后 / 水位判据段级 vs 会话级）。
判据：**对每处"修好后新出现的数据流"，问它读的句柄是否还活着、它的判据是否与既有扫描面同源**。
**另一条（R14-3）**：**"横切关注点的判据必须只有一份实现"** —— 一个谓词若在多处各自手写，
其中若干处会以"看起来对、实际恒真"的形式失效（此处 `stem.startswith("s-")` 配一行
声称它会跳过辅助档的注释），**注释越像真的越危险**。收口固定动作：把判据连同**枚举点**一起
抽成唯一函数，并让既有调用方全部改走它。
**第三条（R14-2/R14-4/R14-5/R14-6/R14-7 同源）**：**"活会话（锁被他人持有）与只读命令的边界"是
本轮最高产的一条轴** —— 同一根因（离线命令没想清楚"会话正被使用"这一状态）在**五个**位置
各自显形：启动自检把它当待修（→ 启动中止）、`repair` 把它当待修（→ 命令失败且够不到真损坏）、
`search` 把它当不存在（→ 抹掉索引）、`search` 自己锁住全部会话（→ 他进程开不了会话）、
`session show` 自身被写锁挡住（→ 看不了正在跑的会话）。判据：**只读的离线命令要么绕开锁
（只读回放源 `SessionReader`），要么跳过并保留派生状态**，绝不"当作不存在"或"当作待修"；
且**候选筛选必须只有一处**（`_repairable_unhealthy`）。
**第四条（R14-8）**：**"派生视图必须与真源同域"** —— 索引是会话的派生副本，其**存储域**必须
与真源（会话目录）完全一致；两者域不同即产生跨域可见与键空间碰撞（本轮：跨租户检索泄漏 +
同名 sid 静默丢索引）。判据：**凡为某真源建派生副本，先核对"副本的隔离维度是否与真源逐一对应"**。

---

## 1. 当前已实现能力

| 面 | 内容 | 可核验锚点 |
|---|---|---|
| **主链** | 单进程事件溯源运行时：`engine` 装配 → `agent` → `agent_loop`（三态主循环）→ `tools_executor`（四关管道）→ `guard` → `approval` → `persistence` | `pyharness/engine.py` · `core/agent_loop.py` · `core/tools_executor.py` |
| **外壳** | CLI（**16 子命令**）· ACP（JSON-RPC over stdio）· Web 桌面（FastAPI）· PySide6 原生壳 | `cli.py` · `acp.py` · `desktop/` · `desktop_native/` |
| **治理层（写侧）** | `authorize()` **唯一授权入口**（关 2，AST 判定**恰 1 处调用方**＝`tools_executor.py`；同模块 D1 + 审批重入 D2 两次调用同一入口）；guard 单调拒绝链 g1–g7；`decision.issued` / `receipt.emitted` 已接线 | `governance/context.py` · `core/tools_guard.py` |
| **治理层（读侧）** | 决策因果还原 · 被拒清单（含 `executed` 证据位）· 一致性对账 · **凭证逐条重算核验 + prev_hash 链**；全部经 `ApplicationService` → HTTP/原生两壳可达 | `governance/{audit,receipt}.py` · `application/service.py` |
| **证据面** | 任务段结束按冻结规则归档 `evidence.archived`（仅存引用）；按 `task_id` 聚合查询；索引可由日志完全重建 | `engine.archive_task_evidence` · `governance/evidence.py` |
| **LLM 出网闸** | 单一汇点 `_chat_any` → `_egress_guard`；会话预算对所有五类出口生效 | `core/llm.py` |
| **事件真源** | append-only JSONL。`EVENT_TYPES` **77** · `SYNC_TYPES` **14** · `TRANSIENT_TYPES` **3** · payload 模型 **77** · **信封字段 10**（本轮新增 `tenant_id`） | `events/vocab.py` · `events/envelope.py` |
| **不变量测试面** | INV-01 ~ INV-05 编号化用例（**运行时事实来源**）+ 上下文传播/租户/唯一拒绝出口契约 + **F055 会话根单点派生/隔离/子会话可达** + **声明配置可达防线（AST 级）** + **资源生命周期（后台进程树随会话终止）** | `tests/invariants/`（**193 用例 / 15 文件**） |
| **编排** | jobs · schedule · subagent · workflow（顺序步骤，符合 ADR-017）· MCP（配置门控） | `core/{jobs,schedule,subagent,workflow,mcp}.py` |
| **持久化与恢复** | JSONL 真源 + `repair`（F060）+ FTS 派生索引（可整体重建）；**间隔落盘定时器**（有上界崩溃窗口） | `persistence.py` · `repair.py` · `engine.py` |
| **多租户（桌面）** | 进程内服务端派生 + 进程独立 store + **租户随事件落盘、按日志恢复**；服务仅绑 `127.0.0.1` | `desktop/app.py` · `core/tenant_settings.py` · `events/envelope.py` |
| **测试** | **2,174 collected / 2,169 passed / 0 failed / 5 skipped** · 覆盖率 **80%**；**默认 pytest 无需 `--ignore`** | F2+M5+持续优化轮 R1-R30 实测 |

---

## 2. 当前未实现 / 已知限制

> 「影响范围」标注的是**可达面**；「计划」列区分**已发布 / 未做 / 未计划 / 待定**。
> **注意**：本轮改动**尚未提交**，故「计划」列标"已修复"者指**工作区已实现且测试通过**，
> **不等于**已发布。

| # | 限制 | 影响范围 | 计划 |
|---|---|---|---|
| **L-1** | 桌面租户隔离：**归属面与鉴权面均已闭合**。归属面（R31-1）：请求声明的租户必须**等于**会话的权威归属（认领集合 ∪ 全部权威目录的落盘 `tenant_id`），服务端**不回落客户端头、也不跨租户改判**；不可得 / 歧义 / 不符 ⇒ 403。鉴权面（**R31-2 / ADR-023**）：`default` 沿用全局 `shell.web.token`（兼容口），**其余租户必须同时出示该租户令牌**（`tenants/<t>/token`）；缺失/错误 ⇒ **401**，且鉴权**先于**归属判定（不泄露"该租户下是否有某会话"）；引导页 `/` 不再向无凭证调用方发放令牌 | **剩余边界（如实声明）**：服务仅绑 `127.0.0.1`，且**同一 Windows 用户能读文件**的进程本就可直接读 `~/.pyharness/tenants/<t>/`（含 `token`）⇒ 本机制**不防**此类进程，只挡"能连回环端口但读不到该用户文件"者。**令牌文件的"0600"在 Windows 上不成立**（`chmod` 对 NTFS 无效，实测 `S_IMODE=0o666`；实际可读面由父目录 ACL 继承决定，本机实测多一条 `本机额外 ACL 授权组（具体机器信息未分发）:(RX)` 授权）—— 已按实测更正文档，**未**做 ACL 收紧。另：信封未载 `tenant_id` 的历史会话经 HTTP 一律 403 | **已闭合**（`a51e148` 归属 + 本提交鉴权）。**契约升级已落档**：`CFG.md` §9 原"不是强安全边界"→"经凭证绑定的访问边界，边界止于文件系统"；`SECURITY.md` 原"单进程无网络层身份伪造"已更正；详见 `docs/decisions/ADR-023-per-tenant-authorization.md` |
| **L-2** | `policy.updated` 装配留痕**已修复**（F-01） | 治理策略生命周期留痕已落地 | **已修复**（已发布） |
| **L-3** | 审批 identity 的**二级复合键**：`_pending` 以裸 `seq` 为键（= `approval_id`） | 碰撞时该次审批**失败**（fail-closed）而非错配 | **R24 复核：已在查找层收敛，无需改键**。① 每个 `ApprovalProvider` **按会话唯一**（`self._approvals[sid]`）⇒ 会话内 `seq` 本就唯一，裸键**按构造即正确**；② 跨会话同 `approval_id` 的歧义由 `ApplicationService.provider_owning` 处理：>1 命中 → `EVT-101` 并**指路** `{sid}/{aid}` 路由（sid 版裁决入口存在且被桌面端点使用）；③ 已有对抗用例 `test_same_approval_id_can_be_disambiguated_by_session`。故「二级复合键」不是缺口——把它搬进字典键反而会让每个读取点都要带 sid |
| **L-4** | LLM 出口治理**已部分闭合**：本轮引入**单一出网闸**（`LLMClient._egress_guard`），会话预算对 `chat`/`chat_stream`/`mini`/`summarize`/`json_chat` 五出口生效。**仍存**：LLM 出口**不经** `governance.authorize()`——这是**有意设计**（见下方"负空间"），故"LLM 调用有治理决策/凭证"**不成立**  **已裁定（R26，用户授权）**：维持现状——LLM 出网的治理面＝**会话预算硬闸**（`_egress_guard`），**不**把它塞进 `governance.authorize()`。理由（`llm.py` 已明写）：`authorize()` 收 `ToolCall`、g1–g7 按**工具名**前缀匹配，LLM 请求无工具名/无参数集/无 Provider 副作用，只能靠**伪造 ToolCall** 接入 ⇒ 污染工具治理链语义。**范围裁定，非缺陷** |
| **L-5** | 持久化失败语义**已闭合**：三条写路径（强同步 `append` / 公开 `flush` / 异步 `_flush_batch`）共用单一判据（连续失败 ≥3 或重试队列 >192）⇒ 暂停会话 + `system.error` | 原先强同步/公开 flush 在磁盘持续故障时不暂停、`_retry_q` 无界增长 | **已修复**（未提交；故障注入测试覆盖） |
| **L-6** | `desktop_native/` **本地 0% 可执行覆盖**：PySide6 未安装于本机 `.venv`，两测试文件**显式 skip**（不再是 collection error）；CI 安装 `.[dev]` 后应真实执行**但尚未在 CI 实跑验证**  收集债务**已闭合**；本地覆盖**未闭合**（需装 PySide6）；CI 实跑**未验证**。**R27 收窄**：CI 的每一步命令（安装/自检/全量/覆盖率门禁/四层门禁/两个静态闸）已在本地**等价复跑通过**，且新增静态闸断言「CI 引用的 `tests/...` 路径与自检 `import` 的包都必须真实存在/已声明」⇒ 剩余未验证面**仅** GitHub Actions 运行时本身（需 push） |
| **L-7** | `tests/{security,acceptance,e2e}` **已实体化**：unit 1821 · security 55 · e2e 51 · invariants 157 · acceptance 9 · integration 6 | 已有安全/验收/端到端自动回归 | **已修复**（未提交） |
| **L-8** | **CI 已建立**（`.github/workflows/ci.yml`：分层门禁 + 覆盖率下限 `COV_FLOOR=75` + 静态闸）。**尚未 push，故未在 GitHub Actions 实跑**  **已建立（未验证）** —— 首次 push 后须核对首次运行结果。**R27 收窄**：① 覆盖率下限 `COV_FLOOR=75` 已接 `--cov-fail-under`（非摆设）且实测总覆盖 **80%** 高于下限；② CI 内联的 `authorize()` AST 闸与结构门禁文件**已在本地等价复跑通过**；③ 新增静态闸（CI 路径/依赖/下限一致性）⇒ 剩余未验证面**仅** GHA 运行时（需 push） |
| **L-9** | LICENSE（历史条目，已解决：`cbb2033` 引入 MIT） | — | **已解决** |
| **L-10** | `AuditSystem` **生产调用路径已建立**：`ApplicationService.governance_audit` + `/api/sessions/{sid}/governance-audit` + 两壳 UI；**凭证核验也一并接上**（`verify_receipt` 不再是库级孤岛） | 桌面"审计"页现同时展示治理因果与旧遥测 | **已修复**（未提交） |
| **L-11** | `EvidenceCollector` 生产生产者**已建立且已覆盖全部终态**。**F2 口径不准**：当时只在「正常完成」路径归档（实测异常/预算/取消三种终态**零证据**）；**M5 已修**：触发点移到 `segment.end` 生命周期，五终态均恰一条工件。段内无 `decision.issued` ⇒ 不归档；幂等取自日志重放 | 按 `task_id` 聚合证据链运行期可达（含子会话） | **已修复**（未提交） |
| **L-12** | Context Propagation Contract **已建立且可执行**：`session_id`（必填/每条/持久化）· `call_id`（含**位置契约**，见 L-17）· `decision_id` · `receipt_id` · `agent_id` · `tenant_id` 逐项钉死；**负空间显式断言**：`request_id` / `trace_id` / `conversation_id` / `subject_id` / `user_id` **不在契约内**（引入须走一次有意识的契约变更——测试会先变红）  **已裁定（R26，用户授权）**：维持「不在契约内」——四个标识符（`trace_id`/`conversation_id`/`subject_id`/`user_id`）**有意不加**（产品当前无跨请求/跨会话关联需求）；引入须走一次显式契约变更（测试会先变红）。**范围裁定，非缺陷** |
| **L-13** | **决策/事件载荷的租户归属已闭合**：租户落在 `Envelope`（可选字段），随会话落盘、按日志恢复、查询面可读；**未**改 77 个载荷模型（横切归属放信封是刻意选择） | 审计轨现可**按事件**归属租户 | **已修复**（未提交） |
| **L-14** | **`ag.ctx.persistence` 死传播**：`Ctx.__init__` 赋值该字段，但全库无 `ctx.persistence` 读取点 | 无运行时影响；扩大 ctx 契约面 | **已核实并如实标注（R24）**：`spine.persistence` **有值**、该字段是**可用门面**（spec `desktop.py.md:266` 曾规定 `ctx.persistence.flush_sync()` 兜底 flush，已由 R14-13 的真实 flush 路径取代）；docstring 已注明「当前无 in-repo 读取方、保留为可用门面」。**删除**仍属公共契约变更（不做） |
| **L-15** | 定时提醒到点语义未区分（`intent` 兼作任务语义与重放消息；到点轮无通道告知模型）  **已处置（R24）**：到点轮现经**受控系统提示段**获知来源（`ag_ctx.turn_source` ← `task.meta.source`，由 `[本轮来源]` 段渲染）⇒ 模型不再只复述任务状态；intent 双语义保留（用户原话仍是 `user.message.content`） |
| **L-16** | 调度器为**进程内执行模型**（定义持久、执行不持久；未打开过的会话不触发）  **已裁定（R24，用户授权）**：维持**进程内执行模型**——执行载体＝桌面/CLI 进程本身，与 INV-07 单写者锁同生命周期；不引入常驻守护进程（会新增第二个写者与一套锁/配额语义）。**范围裁定，非缺陷** |
| **L-17** | **`call_id` 的存放位置不统一**：`tool.*` 在 **payload**、治理类（`guard.*`/`decision.issued`/`receipt.emitted`/`approval.requested`）在 **Envelope.trace** | "读 call_id"没有单一函数（`AuditSystem._trace_call_id` 只读 trace，因治理面恰好在 trace 而可用） | **已登记为契约**（`tests/invariants/test_inv_context_and_paths.py` 逐类钉死）；**读取侧已单点化（R24）**：新增 `events.call_id_of(env)`（payload 优先、回落 trace），全库 5 处读取点改走它 —— 事件**存放形状**保持不动（改 77 载荷属破坏性变更），故「统一化」以**读侧单点**兑现 |
| **L-18** | **`ping` 健康探针不受出网闸约束**（负空间）：它不产生 tokens、不落 F029 计量、不进 `llm.request`。让它受会话预算约束会使"探测可用性"本身被预算拒，语义颠倒 | 探针路径在审计上不可见（`llm.py` 明确不落事件） | **有意不治理**；已在 `_egress_guard` docstring 显式登记 |
| **L-19** | **`scope.check_budget` 在 scope 缺失时降级放行**（出网闸三态之一）：预算闸是**配额面**而非授权面，缺配额不等于越权。与 executor `_require_wiring` 的 fail-closed 是**不同判断**，两者并存属有意 | 轻装配/单测替身下 LLM 出网不拦 | **有意设计**；已在 `_egress_guard` docstring 与 `tests/security/test_llm_egress.py` 显式断言 |
| **L-20** | **声明配置不可达（死配置）**。**R1-R6 已接线 7 个**（功能本就存在、仅缺配置读取）：`loop.max_arg_failures_per_round`（F026 阈值原**硬编码 2**）、`llm.probe.interval_s`（F033 探针周期原恒 60s）、`plugins.backpressure_limit`（F005 原恒 1000）、`loop.task_queue_max`（F043 原恒 32）、`security.attachment.max_per_message`／`max_bytes`／`mime_whitelist`（F061，随 R6-5 实现接线）。**仍不可达 12 个**：`loop.step_timeout_s`／`loop.tool_concurrency`／`loop.thread_pool_size`／`loop.message_edit_window_s`／`security.sandbox.proc_mem_limit_mb`／`security.credentials.cache_ttl_s`／`storage.archive_days`／`storage.spill.max_per_session_mb`／`plugins.ctx_lazy`／`plugins.pre_activate`／`plugins.priority`／`plugins.deadletter_samples` | **运维按 CFG.md 调参不生效**（YAML / env / CLI 均无反应）且无任何提示。对应 PRD-Core §7 **未勾选**功能项（F005/F017/F022/F054/F055/F063 与 DIS-SEAM §3.3/3.4） | **已登记 + 已设防线**：`tests/invariants/test_inv_config_reachability.py::KNOWN_UNIMPLEMENTED`（13 项，含 `schema_version` 内部键）+ **AST 级死配置防线**（按"属性访问/关键字实参/点分路径串"判定；新死配置立即变红，已接线者必须移出登记表——该防线在 R6 实际拦下 3 个键） |
| **L-21** | **会话删除即永久删**：`DesktopSessionManager.delete_session` 直接 `unlink` 会话 JSONL 与轮转/备份文件，**无归档步骤**；`storage.archive_days`（CFG 写"删除归档 30 天；0=立即清"）零读取者  **已修（R24，用户授权代行产品裁定）**：`delete_session` 按 `storage.archive_days`（默认 30；`0`＝不归档立即删）把会话**全部相关文件**移入同域归档区 `{会话目录父}/archive/sessions/{sid}-{ts}/`（与会话目录**同域**，不跨租户），并按保留期清理过期归档（`_prune_archives`）；归档目录不可建 → **拒绝删除**（不静默永久删） |
| **L-22** | ~~**F061 图片附件：校验面只到事件 schema 层**~~ → **R6-5 已闭合**：现按 PRD F061 规格实现（`core/attachment.py` 单点：魔数定类型 + 四格式尺寸解析 + ≤`max_bytes` + ≤`max_per_message` + `sha256[:16]` 内容寻址去重落盘到会话工作区 + `ATT-001` 零写盘），服务层接入，`security.attachment.*` 三键由死配置转为**可调生效**  **残余已裁定（R26，用户授权）**：① mime 以**魔数**为准而非全字节内容嗅探（可接受：魔数即格式事实）；② **不做引用计数**——落盘在**会话工作区**内，其生命周期由工作区管理，同内容多消息共享一份**不产生悬空引用**（只有磁盘占用）。**范围裁定，非缺陷** |
| **L-23** | **Web 壳的 `shell.web.host` / `shell.web.port` 零读取者**（CFG §3.8 声明为可调、含 `PH_WEB_HOST`/`PH_WEB_PORT` env 映射）。实测：`desktop/net.py` 绑**模块常量** `HOST=127.0.0.1`、端口由 `pick_free_port()` **动态取空闲口**；`launcher.py` 两处都用动态口  **已修（R24）**：新增 `desktop.net.resolve_bind(cfg)` 作为 `shell.web.host/port` 的**唯一读取点**——**host 仅允许 loopback**（127.0.0.1/localhost/::1；其他值 **CFG-601 拒绝启动**，保住「仅绑 127.0.0.1」的姿态），**port** 为 0 或被占用 → 回落空闲口（不因端口冲突起不来）；launcher 两处改走它 |
| **L-24** | **`security.policy.read_extra_dirs`（workspace 外只读例外）结构性不可达**：①`g3`(guard) 只按 workspace 几何判定、不看例外清单；②provider `resolve_in_workspace` 对**绝对路径**先抛 `POL-FS-1`，也不看例外。CFG §3.3 却把它写成"workspace 外显式授权**只读**例外（须 config+guard 留痕）"  **已修（R24）**：`ScopePolicy` 增 `read_extra_dirs`（装配期从 `security.policy.read_extra_dirs` 注入），**两层同判**——`g3`（`_resolve_geometry`）与 provider（`resolve_in_workspace`）均在**只读类工具**下放行例外目录内的路径，写类一律仍拒；未列入例外的界外路径仍拒。原「钉住 fail-closed」用例已改写为正向用例（读放行／写仍拒／未列仍拒） |

| **L-25** | **轮转段在「修复面」与「扫描面」中的地位不对称**（R13-5 留档；`repair._rotated_segments` docstring 引用本项） | R13-5 起轮转段被纳入 `repair_session` 的**修复面**（段级备份 → 尾部截断 / 中部坏行隔离 → 报告 `seg{n}-quarantine-*` 并随 `session.recovered` 声明），而**全会话健康扫描**（`auto_scan`）仍按 aux 跳过段：段本身不进 `SessionHealth`。**这是有意保持的不对称**——扫描面按"每会话一个主文件"枚举（R14-3 的 `session_log_paths`），段属会话**数据面**而非独立扫描项；段级损坏改由 `repair_session` 内的 `_segment_damage` 单点发现 | **保持现状**：段**内容**已同时反映在会话级水位（`_segment_scan` 并把 seq/内容末位并入会话判定，R13-6）与对账（`_session_content_last`，R14-1 二阶②）；仅"段文件的独立健康条目"不产出。改扫描面会与"每会话一条"的枚举口径冲突，收益不抵复杂度 |
| **L-26** | **崩溃遗留 job 的「事件级」恢复写入未实现**：`core/jobs.py` 原 docstring 声称"崩溃…自动失败并告警"，但全库只有 `_run` 的 finally 与 `cancel` 写 `job.failed` ⇒ 进程被杀即**无终态事件**  **已修（R24）**：新增 `JobManager.recover()` —— 重放本会话事件，对「有 `job.started` 无终态」的 job **补写 `job.failed(reason=crash)`**（幂等、仍走本类唯一写口、失败不阻断）；由 `engine.run_for_task` 的**幂等闸**（`_jobs_recovered`）在生产唯一驱动点调用（装配期 seq=0 写不了）。读侧如实判定保留 |

| **L-27** | **`SessionStore.repair()` 是「早期面」：第二份完整修复实现，生产零调用者**：`persistence.py` 自带一套 备份 → 截断 → 隔离 → 空洞定位 → `session.recovered` 流水线；`specs/repair.py.md` §0 明令"**本模块是 F060 修复策略与编排的唯一归属…core/persistence 早期草案中同名 repair 若已实现，应改为委托本模块，禁两套修复策略并存**"，且更正备份命名为 `.corrupt-{ts}.jsonl`（PRD 权威，覆盖 persistence 草案的 `.jsonl.bak-`）  **已修（R24）**：`SessionStore.repair()` 改为**薄适配器**，委托权威 `repair.repair_session`（`specs/repair.py.md` §0「禁两套修复策略并存」）。安全委托序：`flush` → **关本句柄** → 委托 → **重开** + 重建 `_torn_tail` + 解除暂停；返回本模块 `RepairReport`（由权威报告投影）。~6 个早期面用例已迁到生产语义（备份 `.corrupt-`／隔离 `quarantine-N`／声明落文件／健康即不备份） |

### 负空间（明确"不治理 / 不引入"的部分）

| 项 | 为什么不 |
|---|---|
| LLM 出网**不**走 `governance.authorize()` | g1–g7 按**工具名**前缀匹配、`authorize()` 收 `ToolCall`、产出以 `call_id` 串联 `tool.call`/`tool.result`。LLM 请求无工具名/无参数集合/无 Provider 副作用——塞进去只能靠伪造 `ToolCall`，会污染工具治理链语义（P1 决策权与执行权分离）。**LLM 出网适用面 = 会话预算**，已单点建立。 |
| `request_id` / `trace_id` / `conversation_id` / `subject_id` | 产品当前无跨请求/跨会话关联需求；引入会带来跨会话数据模型变更。**契约测试会阻止悄悄加入**。 |
| 事件载荷逐个加 `tenant_id` | 租户是**横切**归属，放信封一处（10 字段）胜过改 77 个载荷模型（必然漂移，且属 H-3 级 schema 变更）。 |
| `ping` 纳入治理 | 见 L-18。 |
| `EvidenceCollector` 为空段造证据 | "不发生"与"失败"是两件事；无治理决策的段不归档。 |

---

## 3. 与历史文档的关系

仓库中并存三类文档，**时间语义不同**：

| 类别 | 示例 | 时间语义 |
|---|---|---|
| **阶段报告**（根目录 `.md`） | `S1_CHANGE_REPORT.md` · … · `S6-2b_FINAL_SUMMARY.md` | **当时的执行与评审记录** |
| **时点快照** | `docs/baseline/BASELINE_REPORT.md` · `REFACTOR_PLAN.md` | **固化于 `0cba75d` @ 2026-09-14** |
| **架构决策（现行）** | `docs/ADD.md`（22 条 ADR）· `docs/decisions/ADR-021/022` | **现行有效**，只增不改 |

> **读法建议**：先读本文件 → 再读 `docs/ADD.md` 与 `docs/MAP.md` → 需要过程细节时**再**按阶段读根目录报告。

---

## 4. 修复状态与发布节奏

- 本轮（F2）改动**全部位于工作区，尚未提交、尚未 push**。
- **L-5 / L-7 / L-10 / L-11 / L-13 已闭合**（代码 + 测试）；**L-4 / L-6 / L-12 部分闭合**；
  **L-1 / L-8 建立了机制但存在未验证/未闭合残余**（见 §2 各行）。
- **L-14 ~ L-16** 未触碰。
- 本文件**随发布更新**；若与任何阶段报告冲突，**以本文件为准**。

---

_文档时间戳：2026-09-21T13:45:00+08:00_
