# RT-FIX-STAGE1-REPORT

> **性质**:代码修复报告(Stage 1)。
> **上游**:`RT-FIX-PLAN-v1.md`(P-1~P-5 已裁定)· `FUNCTIONAL_RUNTIME_AUDIT_v1.md`。
> **范围**:仅 F-01 / F-19 / F-28(一级)。**未触碰** F-25 / F-26 / F-27。
> **基线**:`HEAD = d2ccd45` · 全量 1719 collected / 1717 passed / 2 skipped · 覆盖 79%。
> **变更后**:全量 **1732 collected / 1730 passed / 0 failed / 0 skipped(2 skipped)** · 覆盖 **79%**。

---

## 1. STATUS

`DONE`(通过 GATE-04)· `IMPLEMENTED + TESTED + VERIFIED`(含 4 项 mutation 反证)

| 项 | 结果 |
|---|---|
| 计划内 3 项 | **全部落地** |
| 定向测试 | **126 passed**(5 个受影响文件) |
| 全量回归 | **+13 零回归**(恰等于新增用例数) |
| Mutation 反证 | **4/4 全 RED**,且各由**其专属用例**捕获 |
| 越界 | 无(未动 `persistence.py` / `tools_guard.py` / `governance/**` / 协议 / INV registry) |

---

## 2. FILES CHANGED

### 2.1 本次 Stage 1 变更(6 文件,+308 / −8)

| 文件 | +/− | 归属 |
|---|---|---|
| `pyharness/engine.py` | **+14 / −0** | F-01 |
| `pyharness/desktop/app.py` | **+11 / −3** | F-19 |
| `pyharness/core/approval.py` | **+20 / −5** | F-28(两处守卫) |
| `tests/unit/test_engine_flow.py` | +73 / −0 | F-01 用例 ×1 |
| `tests/unit/test_desktop.py` | +82 / −0 | F-19 用例 ×5(含参数化 → 8 条执行) |
| `tests/unit/test_approval.py` | +108 / −0 | F-28 用例 ×4 |

### 2.2 ⚠️ 工作树中的**非本阶段**改动(为保 git diff 清晰,勿混读)

| 文件 | 归属 |
|---|---|
| `pyharness/cli.py` · `pyharness/core/commands.py` · `tests/unit/test_cli.py` | **F1 阶段既存未提交工作**(D1/D1b/D1d-A + RT-02) |
| `pyharness/core/orchestration.py` · `tests/unit/test_orchestration.py` | **RT-GOV-01**(上一轮,1 行 + 5 用例) |

**本阶段 commit 时应只显式列出 §2.1 的 6 个文件。**

---

## 3. 逐项修复与**与批准方案的偏离**

> 偏离均因**实测证据**产生,逐条列明;未擅自扩大范围。

### F-01 `policy.updated` 装配留痕 —— 挂点偏离(装配期 → 首次运行)

**Root Cause**:`PolicyEngine.emit_assembled`([policy.py:392](pyharness/governance/policy.py)) 是 `policy.updated` 的唯一生产发射器,却**零调用** ⇒ 词表已注册(77)、`SYNC_TYPES` 已含(14)、单测直调 `emit_updated` 通过,唯独装配层这一跳从未接上,**事件在生产永不出现**(ADR-020/Q3 承诺落空)。

**⚠️ 偏离 D-1(方案写"装配期 emit")**:LOAD 阶段实测证明装配期不可行——

```
seq after assembly = 0
attempt 1: emit BEFORE session.created → RAISED: EventError EVT-106 (须在 session.created 之后)
attempt 2: emit AFTER  session.created → result: True ; events = ['session.created','policy.updated']
```
`_build_governance` / `activate_orchestration` 运行时 `seq=0`(`session.created` 由调用方在装配**之后**落)⇒ 在装配期发射会让**整个引擎装配抛出 EVT-106**。

**实际落点**:`make_runner.run_for_task`([engine.py:776+](pyharness/engine.py)) —— chat/run 的**唯一生产驱动点**([cli.py:809](pyharness/cli.py) 明示"普通文本交 `task_queue.submit`"),且此处沿用既有 `_plugins_ready` 懒加载惯用法。幂等闸 = `EngineSpine._policy_announced`(带默认值的新字段)。
**失败策略**:发射失败记 `warning` 并继续(与相邻插件预载同款"不阻断"),幂等闸仍置位。

### F-19 租户校验 —— **范围收窄**(仅正则;回落策略保持)

**Root Cause**:`_SESSION_ID_IN_PATH` 的字符类 `[0-9a-zA-Z]+` **不含 `-`/`.`**,而 `validate_session_id` 允许 `s-[A-Za-z0-9._-]{6,64}`、`cli.py` 生成的 fork id 恰为 `s-fork-<hex>` ⇒ 捕获被截断成 `s-fork` → 查不到租户 → 中间件**静默回落信任客户端头**。

**已改**:字符类放宽为 `[0-9A-Za-z._-]+`;并把**过度声称**的注释([app.py:127-129](pyharness/desktop/app.py),原称"阻断伪造头访问他租户会话")订正为如实描述派生条件与回落面。

**⚠️ 偏离 D-2(方案第二步是"派生为空即拒绝")**:实测 `_SESSION_TENANTS` 是**纯进程内 dict、不落盘**([tenant_settings.py:314-321](pyharness/core/tenant_settings.py))。改为硬拒绝会**打断"重启后打开旧会话"**(中间件先于任何登记执行)⇒ 属功能回归。故**保持回落**,并把该残余**另立为新 finding R-1**(见 §6)。

### F-28 审批条目跨会话 —— **超出字面范围的同源补强**(需你确认)

**Root Cause(两处同源)**:provider 按 spine **共享**;
① `_pending` 以**事件 seq** 为键([approval.py:294](pyharness/core/approval.py)),子会话 seq 自 1 重数 ⇒ 与父会话同键空间 ⇒ 后到者**静默覆盖**先到者;
② `_merge` 只按**工具+参数指纹**合并([:272](pyharness/core/approval.py)) ⇒ 子会话同指纹请求**并入父批**、被父的裁决唤醒 ⇒ **跨会话授权污染**。

**关键事实**:`ApprovalRequest` **本就带 `session_id`**("请求所属会话")——信息已在,只是键忽略了它。

**已改**:
- **② `_merge` 键 → `(sid, fp)`**(2 行)—— 补强项。**⚠️ 偏离 D-3**:你的裁定字面是"pending 不允许覆盖",但**只守 `_pending` 是不完整的**——`_merge` 的跨会话并批更早发生、更易触发。且 `_trust` 本就按 `_trust_hit(fp, sid, ch)` 会话判,**合并漏了 sid 更像既有疏漏而非设计**。若你不同意,该处仅 2 行,可单独回退。
- **① `_pending` 跨会话覆盖 ⇒ fail-closed 拒绝**(`APR-503`),同会话重入按原语义放行。守卫位于 `approval.requested` 之后 ⇒ **已知副作用**:拒绝时该事件已落盘而无裁决留痕(工具侧会以 `APR-503` 记 `tool.error`,形成可追溯的失败记录)。已在代码注释与下文 R-2 中标注。

---

## 4. TEST RESULTS

| 口径 | 结果 |
|---|---|
| 定向(5 个受影响文件) | **126 passed** |
| **全量回归** | **1732 collected / 0 failed / 0 errors / 2 skipped = 1730 passed** |
| 基线 | 1719 / 1717 / 2 |
| 净变化 | **+13,恰等于新增用例**(F-01×1 + F-19×8 + F-28×4)⇒ **零回归** |
| 覆盖率 | 79%(17907 → 17919 语句;missed 3835) |
| `EVENT_TYPES / SYNC_TYPES / TRANSIENT` | **77 / 14 / 3 不变** |

### 4.1 Mutation 反证(项目既有纪律,4/4 全 RED)

| Mutation | 改动 | 被抓用例 |
|---|---|---|
| **M-F01** | 去掉装配期发射(`emit_assembled` → `pass`) | `test_policy_updated_announced_once_at_first_run` **RED** |
| **M-F19** | 正则还原为 `[0-9a-zA-Z]+` | `test_tenant_middleware_derives_registered_fork_session_tenant` + 参数化 2 例 **RED(3)** |
| **M-F28a** | `_merge` 键还原为纯指纹 | `test_cross_session_same_fingerprint_not_merged` **RED**;同会话合并守卫**仍 GREEN** |
| **M-F28b** | 去掉 `_pending` 跨会话守卫 | `test_cross_session_pending_seq_collision_refused` **RED** |

**M-F28a 的证据价值**:撤回会话限定后,**只有跨会话用例 RED、同会话用例仍 GREEN** ⇒ 证明"sid 限定"正是鉴别因素,且 R13 防轰炸语义未被破坏。
**还原核验**:`grep -rn "MUTATION" pyharness/` = **0**;`git diff` 逐行与意图一致。

---

## 5. IMPLEMENT 中发现的**新事实**(影响后续优先级)

### ⭐ N-1:`_pending` 跨会话碰撞的**可达性远高于 Plan v1 的估计**

Plan 中评"需父子同时 pending 且 seq 相等,窗口窄"。实测构建"两个全新会话"时,**两者的下一次 append 都是 `seq=2`** ⇒ **立即碰撞**(我的守卫当场触发)。推广:两个**新生的兄弟子 Agent** 同时请求审批,seq 极可能相同 ⇒ 碰撞并非罕见。

**后果**:① 守卫的价值比预期大(挡住的不是理论情形);② **复合键(第二级)的优先级应上调**——当前 fail-closed 会把第二个请求拒掉,虽安全但属功能损失。
**证据**:`test_cross_session_same_fingerprint_not_merged` 首次运行时即以 `pending=1` 超时暴露此点(已改写为让 B 的 seq 错开)。

---

## 6. KNOWN LIMITATIONS / 残余

| ID | 残余 | 级别 | 说明 |
|---|---|---|---|
| **R-1** | **未登记会话 / 无会话 id 路由 ⇒ 仍回落客户端租户头** | **P1(新登记)** | F-19 只修了"能匹配却匹配不全"。闭合需把**租户随会话落盘**(或注册前移到会话打开)。已用 `test_tenant_middleware_documents_unregistered_fallback` 钉住当前行为,使将来改 fail-closed 必须是一次**有意变更**而非静默漂移 |
| **R-2** | F-28 守卫的**孤儿 `approval.requested`** | P3 | 极罕见路径;有 `tool.error(APR-503)` 形成可追溯失败记录 |
| **R-3** | F-01 发射**失败即吞**(记 warning) | P3 | 与相邻插件预载同款;磁盘故障时任务本身亦会失败 |
| **R-4** | F-01 **只覆盖 task_queue 路径** | P3 | 纯 subagent-first / 无队列兜底路径不发射。生产 chat/run/desktop/ACP 均经 task_queue,故覆盖生产 |
| **R-5** | R-1 的**冲突策略未定** | P1 | 派生租户与客户端头**冲突**时现以派生为准(已测);但"派生为空"的语义仍需人工裁定(拒绝 vs 回落) |

**未处理的已知缺陷(按裁定暂缓)**:F-25 / F-26(需分两次交付)· F-27(待 ADR)· F-28 第二级复合键(合并 F1-X5/X6)。

---

## 7. PROTOCOL §11 其余字段

- **ARCHITECTURE IMPACT**:
  - F-01:**新增一条装配期强同步事件**(类型早已注册 ⇒ 无 schema 变更、不触发 EVT-102);不改 `authorize` 单入口、不新建治理模块。
  - F-19:纯字符类放宽 + 注释;无接口变化。
  - F-28:`_merge` 键类型 `str → tuple[str,str]`(**内部私有状态**,非公开 API);`_pending` 新增一条 fail-closed 分支。**未**改审批身份模型(复合键延后)。
- **CONSTRAINTS 触发**:CND-04(安全/外部输入:F-19 含 negative case)· CND-08(跨模块接线:F-01 端到端可达性已由行为用例证明)· CND-01(共享状态:F-28 的 provider 跨会话共享,已加隔离)。
- **BOUNDARIES(未做)**:未修 F-25/F-26/F-27;未改 `persistence.py` / `tools_guard.py` / `governance/**`;未动协议、INV registry、`docs/**`;未 commit、未 push。
- **FAILURES / CONVERGENCE**:首轮 3 个新用例 RED ⇒ 全部判定为**测试构造问题**(非产品缺陷),已修:① `ctx.task_runner` 是 `EngineRunner` 对象,须调 `.run_for_task(...)`;② 跨会话合并用例的两会话 seq 相同触发了新守卫,改为让 B 填充错开;③ 同会话槽位用例误用 `pending_count` 等待(预置已使计数为 1),改为以"任务未上抛"为准。**无已知 GAP**。
- **EVIDENCE**:定向 126 · 全量 1732/1730/0/2 · 4 项 mutation 全 RED 且无残留 · `git diff` 仅 6 文件属本阶段。

---

## 8. REMAINING WORK(待你确认后推进)

1. **R-1 / R-5 裁定**:是否推进"租户随会话落盘",以及"派生为空"改为拒绝还是保持回落。
2. **F-28 第二级**(复合键)是否与 F1-X5/X6 合并设计 —— **受 N-1 影响,建议提优先级**。
3. **F-27 的 Audit Session Ownership ADR 草案**(P-2 要求,本阶段未产出,因 Stage 1 明示"仅实施三项"且"不自动处理 F-27")—— 需要我现在单独产出吗?
4. Stage 2(F-26)· Stage 3(F-25)是否按 Plan §3 的顺序推进。
