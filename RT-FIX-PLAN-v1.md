# RT-FIX-PLAN-v1

> **性质**:只读设计阶段产物(**未修改任何代码**)。
> **上游**:`FUNCTIONAL_RUNTIME_AUDIT_v1.md`(基线 `d2ccd45`,全量 1719/1717/2,覆盖 79%)。
> **状态**:待人工确认后进入 IMPLEMENT。**本文件不构成实施授权。**

## 0. 分级口径(先定义,再分类)

| 级别 | 判据 |
|---|---|
| **P0** | 可达 **且** 导致数据损坏 / 安全失效 / 主链不可用 —— 须立即修 |
| **P1** | 可达 **且** 造成正确性 / 审计 / 可靠性错误,**或对外承诺与实现不符** |
| **P2** | 本身不产生错误结果,但构成结构性风险,或使某项保障机制失效 |
| **P3** | 维护性 / 一致性 / 文档 |

> **F-29 与 F-01 是同一缺陷的重复编号**(审计报告 §2.5 已标注)。本方案以 **F-01** 为准,F-29 不再单列。

---

## 1. 分级总表

### P0 —— **无**

本轮全量核查**未发现 P0**。主链、真源、治理边界均无"必炸"级问题。这是本阶段的**重要正面结论**,不应被后续讨论淹没。

### P1 —— 6 项(建议按此顺序)

| ID | 缺陷 | 一句话 | 修复面 | 回归风险 |
|---|---|---|---|---|
| **F-25** | 部分写失败 → 重复落行 | 真源可被污染,且**无测试可发现** | 小(1 函数) | **高**(在读路径上) |
| **F-26** | 强同步无重试上限/暂停 | 磁盘持续故障 → 队列无界增长 | 小(1 辅助函数+2 处) | **中高**(与 F-SYNC-1 交互) |
| **F-01**(≡F-29) | `policy.updated` 生产永不发射 | 治理对外承诺与实际不符 | 小(1 处 await) | **中**(装配处新增事件) |
| **F-19** | 租户正则漏 `-`/`.` | 跨租户读取(仅本机可达) | 极小(1 行) | **低** |
| **F-27** | 子 Agent 审计分裂 | 子会话恒报 INV-04 违规 | 中 | **中高**(触 `tools_guard`) |
| **F-28** | 审批 `_pending` 跨会话碰撞 | 可能跨会话错配裁决 | 小~中 | **中**(触审批身份面) |

### P2 —— 12 项

| ID | 缺陷 | 为何 P2 |
|---|---|---|
| F-02 | `governance/audit.py` 零消费 | 能力未接线,不产生错误结果 |
| F-07 | `flush_interval_s` 零消费者 | 可靠性"承诺"失真,但实际丢失量有界(≤`flush_batch`) |
| F-09 | `ctx.agent` 从未赋值 | 是 F-10 的根因,本身无独立影响 |
| F-10 | `/new` 静默 no-op | 功能缺陷,但无数据损坏 |
| **F-12** | **仅 12% 测试用真实装配** | **元级**:它不是缺陷,而是"缺陷为何漏网"的机制 |
| F-13 | `chat` 的"E2E"用例断言失败路径 | 测试名实不符,误导风险 |
| F-14 | ACP 真装配分支从未执行 | 同上 |
| F-15 | Workflow 生产链零端到端 | 覆盖缺口 |
| F-16 | Scheduler 泵启动零断言 | 覆盖缺口 |
| F-17 | 桌面真装配从未执行 | 覆盖缺口 |
| F-18 | 子 Agent 真链路本轮才建立 | 覆盖缺口(已部分闭合) |
| F-30 | `child_session._record` 缺瞬时守卫 | 当前不可达(`streaming=False`),且被总线 EVT-103 隔离 |

### P3 —— 14 项

F-03(`_reject`/`_invoke` 死代码) · F-04(桌面 17 个零引用私有方法) · F-05(`models.py` 14 个 `*View` 零引用) · F-06(`SessionStore.repair` 仅 tests) · F-08(`assemble_real_engine` 无生产调用者) · F-11(13 个零引用公开函数) · F-20(`docs/specs/commands.py.md` 7 处漂移) · F-21(commands "三壳共用" 不成立) · F-22(persistence docstring 过度声称) · F-23(架构总览缺治理层) · F-24(计数漂移) · F-31(`_cmd_workflow` sid 恒空) · F-32(`_REASONS` 死常量) · F-33(248 `noqa` / 零 linter)

---

## 2. P1 逐项详析

### F-25 —— 部分写失败 → 重复落行

| 字段 | 内容 |
|---|---|
| **Root Cause** | 三条写路径均为"**逐行 write → 一次 flush**",失败时把 `to_write` **整批**回填重试队列,不记录已成功写入的行数:`_flush_pending_all`([persistence.py:400-409](pyharness/persistence.py))、`flush`(:433-444)、`_flush_batch`(:453-461)。缓冲流在缓冲区满时会在 `write()` 内部触发 flush,故 `write()` 自身可中途抛出 ⇒ 前 k 行已进入流而整批被回填 ⇒ 下次重试**重写前 k 行**。读侧无幂等:`replay()`(:473-504)不过滤重复 seq,`SessionLog._absorb`/`_fold_history` 亦不去重。 |
| **Impact** | **真源污染**:① `derive_messages` 出现重复 user/agent 消息 → **送给 LLM 的上下文错**;② `llm.usage` 重复 → 预算/成本**双计**(F032 硬闸误触发);③ FTS 派生索引重复行;④ `stats()` 事件计数失真。属本项目最严重的一类错误(INV-01 的"历史必由日志派生"被静默破坏)。 |
| **可达性** | 需**部分写失败**(ENOSPC/EIO/写满)。概率低,**后果重**。当前**无任何测试覆盖**(全部故障注入恒在写第一个字节前抛)。 |
| **Minimal Fix Strategy** | **⭐ 不做"只回填尾部"的写侧修复**——那会把"重复"换成"丢行"(flush 失败时缓冲区内容不可知),而"不丢"是 `_flush_pending_all` 明确的优先语义。**正确的最小修法在读侧:让 `replay()` 对 seq 幂等** —— 同一 seq 且内容相同 → 跳过(先到者胜);同一 seq 但内容不同 → `PERS-202` fail-closed(与写侧 `_resolve_by_seq` 同判据)。同时**订正过度声称的 docstring**([:378](pyharness/persistence.py) "不重复任何一条" → 明确"写侧不保证不重复;读侧按 seq 幂等")。 |
| **修改文件** | `pyharness/persistence.py`(主) · 可选 `pyharness/core/session.py`(若去重须落在 `_absorb`) |
| **修改范围** | ≈15~30 行(一个去重判据 + 调用点) + docstring。**无 schema 变更、无新事件、无新错误码**(复用 PERS-202) |
| **是否影响架构边界** | **否**,但**触及"唯一真源"的读路径** —— 属高爆炸半径区。且与 **INV-01(只追加)** 与 **INV-03(rebuild 与缓存一致)** 相邻:改 replay 会影响 rebuild 语义 ⇒ 必须与 `docs/INVARIANT_REGISTRY.md` 的 INV-01/INV-03 逐条对照,不得使已 frozen 的编号化用例失效 |
| **是否需要新增测试** | **是(必需)** ① 部分写故障注入(前 k 行成功、第 k+1 行抛)⇒ 断言 `replay()`/`derive_messages()` **无重复 seq**;② 同 seq 不同内容 ⇒ PERS-202;③ 既有"不丢"语义回归(全批失败 ⇒ 行不丢) |
| **回归风险** | **高**。`replay()` 是所有读路径的咽喉(replay/derive/rebuild/FTS/repair/quarantine)。必须跑全量 + `tests/invariants`。**渐进法建议**:先把幂等做成可断言的小函数并单独测,再接入 `replay()` |

---

### F-26 —— 强同步路径无重试上限、无暂停

| 字段 | 内容 |
|---|---|
| **Root Cause** | 上限+暂停判据 `if self._fail_streak >= 3 or len(self._retry_q) > _RETRY_Q_LIMIT:` 只存在于 `_flush_batch`([persistence.py:463-468](pyharness/persistence.py))。① `_flush_pending_all`(:373-409) **自身不自增 `_fail_streak`**(仅在成功时 `= 0`)、无上限、无暂停;② `append` 的 `except`(:362-365) 自增 `_fail_streak` 但**不检查上限、不暂停**;③ `flush`(:439-444) 自增但同样无上限/暂停。`_suspended` 只在 `append`(:352) 被读。**⇒ 强同步事件(唯一有崩溃一致性承诺的一类)走的是无保护路径。** |
| **Impact** | 磁盘持续故障下:① `_retry_q` **无界增长**(内存);② 每次后续 flush 都要重写整个队列 ⇒ **O(n²) 写放大**;③ 会话**永不暂停** ⇒ 上层的"拒新不丢旧"降级语义不生效(只有异步攒批路径会暂停)。注意:强同步调用方**每次仍会收到 PERS-202**(异常可感知),故不是静默失败。 |
| **可达性** | 持续磁盘故障 + 继续 append。**可构造**(磁盘写满)。 |
| **Minimal Fix Strategy** | 抽出**单一失败记账辅助函数**(如 `_note_flush_failure()`:自增 `_fail_streak` → 判上限 → 发 `system.error(PERS-202)` → 置 `_suspended`)并在 3 条路径的 `except OSError` 中共用。**保持异常类型不变**(继续抛 `OSError`,由 `append`/调用方包装成 PERS-202)——**这点是硬约束**,否则会打断 F-SYNC-1 建立的"session.append 步骤 9 = 第二次真实尝试"机制。 |
| **修改文件** | `pyharness/persistence.py`(唯一) |
| **修改范围** | ≈20~35 行(1 个辅助函数 + 3 个 `except` 块各 1 行调用) + docstring |
| **是否影响架构边界** | **否**(无 schema / 无事件 / 无新码)。但**改变 `_suspended` 的置位条件**,属运行时降级语义 → 需在 `docs/KEY-FINDINGS.md` 或报告留痕 |
| **是否需要新增测试** | **是** ① 强同步路径持续失败 N 次后 `_retry_q` 有界且 `_suspended is True`;② 暂停后新 append 立即 PERS-202(**拒新不丢旧**);③ **回归锁**:`flush(seq)` 失败仍抛 `OSError`(不得变成 PyHError) |
| **回归风险** | **中高**。既有断言该语义的用例 4 处:`test_persistence.py:415-441`(`_flush_batch` 暂停)、`:439-441`(暂停期 append)、`:396-406`(sync append OSError)、`test_session.py:648-657`(sync 落盘失败)。**新增暂停会使"失败一次之后"的后续 append 行为改变** ⇒ 必须先逐条读这 4 个用例,判定是"契约"还是"缺陷"(CORE-03),**不得为过测试而改测试** |

---

### F-01(≡F-29) —— `policy.updated` 生产永不发射

| 字段 | 内容 |
|---|---|
| **Root Cause** | 发射器 `PolicyRegistry.emit_updated`([policy.py:231](pyharness/governance/policy.py)) 的唯一生产调用者有 3 个,全部零引用:`PolicyEngine.emit_assembled`(:392,**docstring 自称"S2-3 装配后调用"**)、`disable_rule`(:364)、`enable_rule`(:381)。而装配函数 `_build_governance`([engine.py:463-470](pyharness/engine.py)) 只 `return policy, policy.chain`,**从不调用 `emit_assembled`**。⇒ 词表已注册(77 型)、`SYNC_TYPES` 已含(14)、单测直接调用 `emit_updated` 亦通过 —— **唯独装配层这一跳从未接上**。 |
| **Impact** | ① **ADR-020/Q3 承诺落空**:策略集变化本应"强同步落 `policy.updated`",实际**零发射**;② 治理层"策略生命周期可审计"不成立(策略指纹/版本无落盘痕迹);③ `SYNC_TYPES` 中有一个**永不出现的成员**,使"强同步清单"这一审计口径失真;④ 与 `decision.issued`/`receipt.emitted` 并列的第三个治理事件**只有它从不产生**。 |
| **Minimal Fix Strategy** | 在**异步的 `activate_orchestration`**([engine.py:404](pyharness/engine.py),每个 spine 必经、已是 async)中补一次 `await spine.governance.policy.emit_assembled()` + **幂等闸**(如 `EngineSpine._policy_announced: bool = False`)。**不改 `_build_governance` 的同步签名**,不动 `tools_guard`,不动 `authorize` 单入口设计。 |
| **修改文件** | `pyharness/engine.py`(+ 可能 `EngineSpine` 加 1 个带默认值的字段) |
| **修改范围** | ≈6~12 行 |
| **是否影响架构边界** | **是(轻度)**。激活了一个从未发射过的治理事件。事件本身已注册、已强同步,**无 schema 变更**;但装配期**新增一条强同步事件** ⇒ 影响"装配后事件序列"的所有断言 |
| **是否需要新增测试** | **是** ① 每 spine 装配恰一条 `policy.updated`(且 `op="add"`);② **幂等**(重复调用不重复发射);③ 强同步已落盘(参照 `test_governance_policy.py::test_t6_emit_updated_persists_after_registration` 的体例) |
| **回归风险** | **中**。装配期多一条事件 ⇒ 既有"事件序列/计数"断言可能变化。需先 diff 出受影响用例,逐条按 CORE-03 判定(契约 vs 缺陷)。**风险可控**因为该事件类型早已在册,不触发 EVT-102 |

---

### F-19 —— 租户正则漏 `-`/`.`

| 字段 | 内容 |
|---|---|
| **Root Cause** | `_SESSION_ID_IN_PATH = re.compile(r"/(?:sessions\|budget\|approvals\|asks)/(s-[0-9a-zA-Z]+)")`([desktop/app.py:35-36](pyharness/desktop/app.py)) 的字符类**不含 `-`/`.`/`_`**,而 `validate_session_id`([sessions.py:20](pyharness/desktop/sessions.py)) 允许 `[A-Za-z0-9._-]`;`cli.py:1125` 生成的 fork id 正是 `s-fork-<8hex>`。⇒ `s-fork-abc123` 只捕获 `s-fork` → `tenant_for_session("s-fork")` 返回空 → 中间件**回落到信任客户端 `x-pyharness-tenant` 头**([app.py:130-135](pyharness/desktop/app.py))。另:不带会话 id 的路由(skills/plugins/settings/tenant/attachments/preset)**永远**信任该头。 |
| **Impact** | 本机进程可伪造租户头读取**其它租户**的会话数据;而 tenant 绑定模型 profile 与 API key ⇒ **跨租户凭据面暴露**。**缓解**:服务仅绑 `127.0.0.1`([constants.py:7](pyharness/desktop/constants.py)、[net.py:37](pyharness/desktop/net.py)、`TrustedHostMiddleware` [app.py:99-102](pyharness/desktop/app.py)) ⇒ 非远程可达。 |
| **Minimal Fix Strategy** | ① 字符类放宽为 `[0-9A-Za-z._-]+`(**`-` 置末或转义**);② **明确冲突策略**:派生出的租户与客户端头**冲突时以派生为准**(已有),但**派生为空时不得回落信任头**——改为拒绝(`{sid}.jsonl` 未注册 ⇒ 404/403),这是把"静默降级"改为 fail-closed 的关键一步。③ 无会话 id 的路由另案(需身份源,不在最小面内) |
| **修改文件** | `pyharness/desktop/app.py`(1 行正则 + 中间件回落分支) · `tests/unit/test_desktop.py` |
| **修改范围** | ≈3~10 行 |
| **是否影响架构边界** | **否** |
| **是否需要新增测试** | **是** ① fork 形状 id ⇒ 派生出正确租户;② 已注册会话 + **冲突头** ⇒ 以派生为准;③ **未注册会话 + 伪造头 ⇒ 拒绝**(negative case,CND-04 要求) |
| **回归风险** | **低**。但②③会把当前"回落到头"的行为改为"拒绝" ⇒ 既有依赖该回落的用例(若有)会 RED,须按 CORE-03 判定。另:`s-fork` 截断目前**恰好**因查不到而回落,放宽正则后行为反转,需确认无测试依赖旧行为 |

---

### F-27 —— 子 Agent 审计分裂(设计型,建议先裁定)

| 字段 | 内容 |
|---|---|
| **Root Cause** | `GuardChain` 在装配期绑定**固定** `session=`(spine 的会话,[engine.py:457-461](pyharness/engine.py) → `tools_guard.from_config`)。子 Agent 运行时 `_runtime_ctx` 传 `session=子会话` 但 `guard=spine.guard`(父绑定)⇒ 链的 `guard.evaluated`/`guard.rejected` 落**父**日志,而 `tool.call`/`decision.issued`/`tool.result` 落**子**日志([orchestration.py:49-73](pyharness/core/orchestration.py))。 |
| **Impact** | ① **`AuditSystem.reconcile` 对子会话报 `NO-GUARD-EVENT`**,即**每一次子 Agent 工具调用都被判为 INV-04 违规**(已实测)——审计可信度是本项目核心卖点,误报直接损害叙事;② 父日志混入不属于它的 guard 事件,因果链污染;③ INV-04 的证据要求("执行前必有 `guard.evaluated`",按 `call_id`)在**子会话范围内不可满足**。 |
| **Minimal Fix Strategy** | **三个候选,代价各不相同 —— 建议先裁定再实施**:<br>**(a) 推荐**:给 `GuardChain.evaluate_detailed`/`_append_rejected` 增加**可选 `session=None` 形参**(默认 None = 用 `self._session`,**既有调用点行为逐字不变**),由 `GovernanceContext.authorize` 传入 `ctx.session`。**加性改动、默认路径零回归**;代价 = 触及 `tools_guard.py`(被 76 个测试钉死的模块)且改变"guard 事件归属"语义 ⇒ 需 ADR 留痕。<br>**(b) 零代码**:认定 guard 事件**按引擎(父)日志归属**,并把 `AuditSystem` 的扫描范围在子会话场景扩到父日志。代价 = 改审计语义 + 需要父子关联信息。<br>**(c) 明确不采纳**:给子会话各建一条链 —— **违反 S2-3 的"单链"不变式**(`spine.guard is spine.governance.policy.chain` 有测试钉死)。 |
| **修改文件** | (a):`pyharness/core/tools_guard.py`(2 处签名+发射目标) · `pyharness/governance/context.py`(传入 `ctx.session`)。(b):`pyharness/governance/audit.py` |
| **修改范围** | (a) ≈15~30 行,全为**加性**;(b) ≈20~40 行 |
| **是否影响架构边界** | **是**。(a) 触 `tools_guard` 且改变 guard 事件归属;(b) 改审计语义。二者都**建议附一次 ADR 记录**(项目已有 ADR-015 处理过"双轨事件"先例) |
| **是否需要新增测试** | **是** ① 子 Agent 工具调用后,**子会话**含 `guard.evaluated` 且 `AuditSystem(child).reconcile()` **无** `NO-GUARD-EVENT`;② **主会话行为逐字不变**(对照);③ 父日志**不再**混入子调用的 guard 事件 |
| **回归风险** | **中高**。(a) 虽加性,但 `tools_guard.py` 被 `test_tools_guard.py`(~76 用例)与 `tests/invariants` 的 INV-04 段密集覆盖 ⇒ 必须全量回归 + INV-04 定向。**且需先判定**:in (a) 之下,`GuardChain._rejected` 等**规则级**状态是否需按会话隔离(当前为链级共享,与链单实例一致) |

---

### F-28 —— 审批 `_pending` 跨会话碰撞

| 字段 | 内容 |
|---|---|
| **Root Cause** | `self._pending: dict[int, ApprovalRequest]`([approval.py:208](pyharness/core/approval.py)),写入键为 `env.seq`(:294),而 `env` 是 `ctx.session` 上的 `approval.requested`;**provider 实例按 spine 共享**,子会话 seq 从 1 重新计数 ⇒ 父子**同键空间**。且 `_ensure_channel`([:157-168](pyharness/core/approval.py)) 对子 ctx(不设 `channel`)**回落 provider 缺省通道** ⇒ 交互外壳下子 Agent 审批不会被 headless 短路,确实会写进 `_pending`。 |
| **⭐ 关键事实(缩小了修复面)** | **`ApprovalRequest` 已带 `session_id` 字段**("请求所属会话(事件落点路由)")——**信息已存在,只有 `_pending` 的键忽略了它**。 |
| **Impact** | ① 两会话同 seq ⇒ 后到者**静默覆盖**先到者;② `_pending.get(aid)` 可能取回**别的会话**的请求 ⇒ **跨会话错配裁决**(批准 A 实际放行了 B 的调用);③ `approval_id`(裸 int)在 UI/ACP/桌面三壳中**跨会话歧义**。与 F1 已登记的 **X5 同源**;其**可达性因 RT-GOV-01 修复而上升**(此前子路径根本到不了审批)。 |
| **可达性** | 需父子**同时**有 pending 且 seq 相等。父 seq 通常远大于子 seq ⇒ 窗口窄但非不可能(父会话很短 or 子会话很长)。`[代码级]` 结论,**未做端到端演示**。 |
| **Minimal Fix Strategy** | **两级,建议先做第一级**:<br>**第一级(极小、立即消除"静默错配")**:写入前检查 `_pending` 是否已有同键且**属于不同 `session_id`** ⇒ 有则**拒绝覆盖**并 fail-closed(`APR-503`,先例:`system.error(APR-503)` 已存在)。约 3~6 行,**API 形状不变**。把"静默覆盖"变成"响亮失败"。<br>**第二级(结构性)**:键改为 `(session_id, approval_id)` 复合键,并在 `pending_approvals`/`decide` 的公开面上补 sid 维度。代价 = **改变公开身份类型**(`int` → 复合),触 CLI/ACP/桌面三壳的裁决调用点 ⇒ 需与 X5/X6 一并设计。 |
| **修改文件** | 第一级:`pyharness/core/approval.py` + `tests/unit/test_approval.py`。第二级:另加 `cli.py`/`acp.py`/`desktop/app.py`/`desktop_native/controller.py` |
| **修改范围** | 第一级 ≈3~6 行;第二级 ≈40~80 行(跨 4 个壳) |
| **是否影响架构边界** | 第一级:**否**。第二级:**是**(审批身份模型,触及三壳公开契约) |
| **是否需要新增测试** | **是** ① 两会话同 seq ⇒ 第二次请求**不得静默覆盖**(fail-closed 且原请求仍可裁决);② 正常单会话路径逐字不变;③ (第二级)按 sid 裁决互不干扰 |
| **回归风险** | 第一级:**低**(纯加守卫,正常路径不触发);但**新增的 APR-503 路径**可能与既有 APR-503 用例(`test_approval.py` 4 文件)语义混淆 ⇒ 需明确二者区别并各留一例。第二级:**高**(触三壳) |

---

## 3. 建议实施顺序与依赖

```
阶段 1(互相独立,可并行): F-19(1 行) · F-01(装配补 1 跳) · F-28 第一级(3~6 行)
阶段 2(独立但需先设计):   F-26(失败记账收敛) → 先逐条判 4 个既有用例
阶段 3(高爆炸半径):       F-25(replay 幂等) → 先做判据小函数 + 单独测,再接入
阶段 4(需先裁定):         F-27(建议 ADR 后择 (a)/(b)) · F-28 第二级
```

**依赖关系**:
- F-26 与 F-25 都动 `persistence.py` 的失败路径 ⇒ **必须分两次交付**(S3/S4 分次交付的先例)。
- F-27 与 F-28 都源于"**跨会话职责切分**" ⇒ 建议**合并为一次"跨会话归属"设计评审**(连同 F1 的 X5/X6),而不是分头改。
- F-25 的读侧幂等与 **INV-03(rebuild 一致性)** 相邻 ⇒ 改前须读 `docs/INVARIANT_REGISTRY.md` 对应条目。

## 4. 需人工裁定的事项(P-1 ~ P-5)

| # | 裁定内容 | 选项 | 我的建议 |
|---|---|---|---|
| **P-1** | F-25 修"写侧只回填尾部"还是"读侧按 seq 幂等" | (a) 写侧 / (b) 读侧 | **(b)**:写侧会把"重复"换成"丢行",违背 `_flush_pending_all` 明确的"不丢"优先语义 |
| **P-2** | F-27 走 (a) 加性 session 形参 / (b) 改审计范围 / (c) 不修仅记录 | — | **(a)** + 一次 ADR;但**先裁定"guard 事件归属哪个会话"**这一语义 |
| **P-3** | F-28 只做第一级(禁止静默覆盖)还是连第二级(复合键) | 一级 / 两级 | **先一级**;第二级与 X5/X6 合并设计 |
| **P-4** | F-01 触发点:装配期(`activate_orchestration`)是否合适 | 装配期 / 其他 | **装配期 + 幂等闸**;但需确认"每 spine 恰一条"不对既有事件序列断言造成大面积破坏 |
| **P-5** | P2/P3 中哪些纳入本轮 | 见 §1 | 建议本轮**只做 P1**;P2 的 F-12/F-13~F-18(测试面)另立"覆盖补齐"阶段,P3 随 lint 基线一次性处理 |

## 5. 本轮方案的自我约束声明

- **未修改任何代码 / 测试 / 文档**(除本文件与 `FUNCTIONAL_RUNTIME_AUDIT_v1.md` 两份新报告)。
- 未 commit、未 push。
- 所有方案均以 **file:line** 为据;**未做端到端演示**的结论已显式标注(`[代码级]`)。
- 方案设计遵循既有约束:**不改 `authorize` 单入口**、**不新建治理模块**、**不破坏单链不变式**、**不为过测试而改测试**(CORE-03)。
