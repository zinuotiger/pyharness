# EVENT-SCHEMA.md — 会话事件协议规范(字段级权威展开)

> **文档类型**:协议规范(字段级)。权威上游:PRD-Core.md §3(冲突以 PRD §3 为准)与 §5 对应 F;实现伪代码见 DIS-CORE.md §3/§8([DIS-§x.x] 引用),不重复代码。**术语**:事件=会话事实最小持久单元;信封=事件外框;payload=类型化负载;强同步=落盘成功才返回;派生视图=日志折叠产物(消息历史/UI/FTS/预算),可整体重建。

**章节**:§1 设计总则 · §2 信封 Envelope · §3 词汇总表(A 会话/B 用户/C 模型/D 工具/E 编排)· §4 reducer · §5 JSONL 物理格式 · §6 校验错误码 · §7 版本演进 · §8 持久化边界。

---

# 1 设计总则

## 1.1 事件 = 唯一真源,只追加

1. **事件 = 唯一真源**:会话一切事实都是事件;日志 = 完整事实集(INV-01)。不存在"日志之外的状态":队列位置、budget 用量、todo、标题、feedback 统计全部可由事件重算。
2. **无 update/delete**:任何"修正"= 追加修正事件(`user.message_edited` 修输入、`user.feedback` 修评价、`context.compacted` 压缩、`session.recovered` 声明修复)。原文永留日志(reducer 取新版留旧痕,§4.2)。
3. **先校验后写入**:坏事件在 `session.append` 入口被拒(§2.2),拒绝 = 不进内存/总线/日志(PRD §3.1-3)。
4. **seq/ts 由框架打**:只由 `session.append` 生成分配(`_seq+1`,[DIS-§3.3.1]);LLM/工具/插件无权自报——防伪造乱序(PRD §3.1-5)。

## 1.2 强同步三类(崩溃一致性锚点)

以下三类事件**立即写盘 + flush,成功才返回**(`sync=True` / `SYNC_TYPES`,[DIS-§3.3.1][DIS-§8.2]):

| 事件族 | 语义 | 为何强同步 |
|---|---|---|
| `user.message` | 用户输入事实原点 | 已提交输入若丢=悄悄吞话;一切轮次由它触发 |
| `guard.rejected` | 安全决策事实 | "此调用被拒过"是单调审计链关键点(INV-04/05) |
| `approval.*` | 人类授权事实 | 授权/拒绝是执行判据;丢失则重放得错误裁决 |

崩溃语义:**至多丢强同步点后 ≤0.5s 攒批窗事件**,由 `session.recovered` 声明(PRD §3.6、§8.3)。

## 1.3 两类事件通道

| 通道 | 去向 | 代表 |
|---|---|---|
| 持久事件(本词汇表主体) | 内存 → 总线 → JSONL 日志 | 全部 §3 事件 |
| 瞬时事件(仅总线) | 内存 → 总线,入日志被禁止 | `llm.chunk`(流式碎片,PRD §3.3-C 唯一明示)[§3.3.6];`registry.updated`(F003 注册表广播) |

**判定准则**:能由其他事件完整重算出、频率极高且单条无审计价值的事实 = 瞬时事件;其余 = 持久事件。新增类型必须二选一登记,瞬时事件不得混入持久词表。

---

# 2 事件信封 Envelope 完整 Schema

## 2.1 信封字段总表

| 字段 | 类型 | 必填 | 规则与约束 |
|---|---|---|---|
| `seq` | int | ✓ | 会话内从 1 起单调 +1(`ge=1`);由框架分配,见 §2.3 |
| `ts` | str | ✓ | ISO8601 UTC,微秒精度,末尾 `Z`(正则 `^\d{4}-\d{2}-\d{2}T.*Z$`);框架统一打 |
| `type` | str | ✓ | §3 词表枚举;未注册 → `EVT-102` 拒写 |
| `session_id` | str | ✓ | 会话 UUID(`min_length=8`,形如 `s-abc12345`);fork 产生新 id(F059) |
| `actor` | enum | ✓ | 六值:`user / agent / llm / tool / system / plugin`(Literal 校验) |
| `origin` | str? | — | 来源插件/能力 id(如 `cap:tool_fs`、`plugin:echo`);脊柱模块事件可省略 |
| `task_id` | str? | — | 所属任务段 id(F043/F044);无任务上下文(闲聊/系统级)为空 |
| `payload` | dict | ✓ | 按 `type` 的 pydantic 模型强校验(§3 每事件字段表);校验失败 → `EVT-100` |
| `trace` | dict? | — | 语义父关联,见 §2.4;仅为审计与配对服务,不参与 reducer |

校验骨架:`Envelope.model_validate(raw)` 逐字段强类型;`payload` 在类型注册表取出对应模型后二次强校验(PRD §3.2 骨架;[DIS-§3.3.1])。**物理行完整样例见 PRD §3.6(信封+payload 拍平单行);下文 §3 各事件"示例"省略框架自动填充字段(`seq/ts/session_id/origin/task_id/trace`),仅以 `type/actor/payload` 示 payload 形态。**

## 2.2 校验链(总线入口,顺序不可交换)

1. **信封字段合法**(类型/必填/枚举/正则)→ `EVT-100` 拒;
2. **类型已注册**(词表或插件注册 schema)→ `EVT-102` 拒;
3. **payload 模型校验**(按 type 强类型)→ `EVT-100` 拒;
4. **seq 连续性**(= `_next_seq+1`)→ `EVT-101` 拒,提示 repair;
5. **会话状态**(未 created → `EVT-106`;已 finished/closed → `EVT-104`)拒。

任何一步失败:**事件不进内存、不上总线、不落盘**,返回结构化错误(含码 + 违规字段明细)。实现见 [DIS-§3.3.1 / DIS-§3.5]。

## 2.3 seq 生成规则与空洞处理

| 情形 | seq 规则 | 空洞? |
|---|---|---|
| 正常追加 | 框架 `_seq+1` 分配,连续 | 无 |
| `session.created` | 恒为 1(首事件) | — |
| compaction(F058) | 折叠区间以 `context.compacted` 一条事件代替;seq **不回填**,后续从 `max` 继续 | 合法空洞:已被摘要代表(PRD §3.4) |
| fork(F059) | 新会话从 1 起;主会话在分叉点后照常 +1 | 无(COW 共享区只读) |
| 崩溃丢尾(repair,F060) | 截断尾部半行,从最后完整点续写 | 截断区间由 `session.recovered` 声明 |
| 中部坏行 | 隔离 quarantine,seq 不回填 | 声明于 `session.recovered` |

**空洞合法性判定**:回放遇无声明空洞 → `warn_hole` 告警 + F031 深查([DIS-§3.3.4]);append 永不产生空洞——"跳号"必由声明事件(`context.compacted.ranges` / `session.recovered.fixed` / `fork.created.base_seq`)覆盖。seq 只前进,空洞只解释不回填。

## 2.4 trace 语义父关联

`trace` 建议形态:`{"parent_seq": int}`(可扩展 `kind` 注明父类型),记录"本事件由哪个事件触发/回应"。协议要求的关键配对:

| 子事件 | 语义父 | payload 内互补锚点 |
|---|---|---|
| `tool.call` | 发出该调用的 `llm.response`(空 content 轮) | — |
| `guard.evaluated/rejected` | 被审的 `tool.call` | `tool` 同名 |
| `approval.*` | 被审的 `tool.call` | `approval_id` = requested 的 seq |
| `tool.result/tool.error` | 对应 `tool.call` | `call_id` 完全一致 |
| `llm.response` | 其 `llm.request` | `model` 一致 |
| `user.feedback/message_edited` | 目标消息 | `target_seq` |
| `subagent.joined/failed` | `subagent.spawned` 所在轮 | `sub_id` |

校验强度:信封层仅验类型;`trace.parent_seq` 指向不存在 seq 记告警不拒写(崩溃截断场景父可能已丢)。审计链查询(guard 覆盖 F031、拒绝追溯)以 payload 锚点 + trace 双通道。
---

# 3 事件词汇总表

> **阅读约定**:每事件小节固定四要素——**字段表**(`✓`=必填,`—`=可选)/ **必填校验** / **消费方** / **示例**(payload 形态,信封省略规则见 §2.1);**落盘时机**分强同步(§1.2)/ 普通(攒批 ≤0.5s 或 ≥64 条)/ 仅总线(瞬时)。E 组(阶段 4-6)事件字段按 PRD §3.3 要点 + 对应 F 展开,未实现前可先冻结(§7)。词表 57 个事件名按 A-E 分组,另含 F028 明示落盘的 `llm.retry`。

## 3.1 A — 会话生命周期

### 3.1.1 `session.created`(首事件,seq=1)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 初始标题(可为空串,随后由 F042 自动命名) |
| `model` | str | ✓ | 主模型名(降级链首位) |

- 校验:必须 seq=1 且为会话首条(否则 EVT-106/101);同会话仅一条。触发者:`system`(agent.create)。
- 消费:session 状态机(pending→active,[DIS-§3.4])、UI 会话列表。落盘:普通(紧随创建的强同步窗口)。
- 示例:`{"type":"session.created","actor":"system","payload":{"title":"","model":"deepseek-chat"}}`

### 3.1.2 `session.renamed`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `new_title` | str | ✓ | 新标题,≤64 字,非空 |
| `by` | str | — | `auto`(F042 自动标题)/`user`(显式 /rename) |

- 校验:标题空 → EVT-100;超长截断 64。触发者:`user` 或 `agent`(自动命名)。
- 消费:FTS 索引、UI/CLI 列表;标题不入 LLM 上下文。落盘:普通。
- 示例:`{"type":"session.renamed","actor":"agent","payload":{"new_title":"文件归类实验","by":"auto"}}`

### 3.1.3 `session.finished`(终态,只一次)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | str | ✓ | 枚举 `idle / timeout / budget / error / cancelled`(F007/F025/F032) |

- 校验:**只允许一次**;重复/closed 后写 → EVT-104 拒(置位防并发双写,终态不可逆)。触发者:`system`(agent.close 唯一归属,[DIS-§2.3.3])。
- 消费:session 状态机(active→finished)、persistence 收尾 flush、UI。落盘:强同步(终态必须可见)。
- 示例:`{"type":"session.finished","actor":"system","payload":{"reason":"idle"}}`

### 3.1.4 `session.recovered`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `fixed` | list[str] | ✓ | 修复动作清单(`tail-truncated`/`quarantine-N`/`seq-holes:[…]`,F060) |
| `backup` | str | — | 修复前备份路径 `.jsonl.bak-{ts}` |
| `lost` | list[int] | — | 被截断丢弃的 seq 区间(若有) |

- 校验:`fixed` 非空列表,空不得写本事件。触发者:`system`(repair 后追加,F060/[DIS-§8.3.3])。
- 消费:repair 报告、审计(崩溃声明可追溯)、状态机(pending→recovering→active,[DIS-§3.4])。落盘:强同步(修复结论必须落定)。
- 示例:`{"type":"session.recovered","actor":"system","payload":{"fixed":["tail-truncated"],"backup":"s-abc12345.jsonl.bak-20260906T070000Z","lost":[41]}}`

## 3.2 B — 用户输入侧

### 3.2.1 `user.message`(强同步)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | str | ✓ | 用户原始输入,**原样保存**,非空 |
| `meta` | dict | — | 来源通道等附加信息(cli/web/acp),不参与派生 |

- 校验:content 非空(空 → EVT-100);斜杠命令命中时不产生本事件而写 user.command(F041)。触发者:`user`(agent.submit)。
- 消费:reducer → `role=user`(§4.2);agent-loop 启动一轮;FTS;auto_title(F042);预算/任务段归属。落盘:**强同步**(§1.2)。
- 示例:`{"type":"user.message","actor":"user","payload":{"content":"把 D:\\杂乱文件夹 按主题归类"}}`

### 3.2.2 `user.message_edited`(只追加修正)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `target_seq` | int | ✓ | 被修正 `user.message` 的 seq |
| `new_content` | str | ✓ | 新版全文,非空 |

- 校验:target 必须是 user.message 且存在,否则 EVT-100(BadTarget);30 分钟编辑窗,超窗 EDT-001 拒(防长会话篡改);agent 消息不可编辑。触发者:`user`(F063)。
- 消费:reducer(该位置替换新版,原文留审计,§4.2)、FTS 级联更新;其触发的回复不自动作废(提示 /new)。落盘:普通。
- 示例:`{"type":"user.message_edited","actor":"user","payload":{"target_seq":2,"new_content":"把 D:\\work 按类型归档并生成报告"}}`

### 3.2.3 `user.feedback`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `target_seq` | int | ✓ | 被评价 `agent.message` 的 seq |
| `kind` | str | ✓ | 枚举 `up / down / flag` |
| `note` | str | — | 备注 ≤500 字 |

- 校验:target 必须存在且类型为 agent.message,否则 EVT-100(BadTarget);kind 非法 → EVT-100;可多次反馈,后写不覆盖(reducer/统计取最新,原文留痕)。触发者:`user`(F062)。
- 消费:质量统计(按最新聚合,可重算)、UI 反馈展示、down 触发"可编辑重问"提示;不进 LLM 上下文。落盘:普通。
- 示例:`{"type":"user.feedback","actor":"user","payload":{"target_seq":9,"kind":"down","note":"第三段路径写错了"}}`

### 3.2.4 `user.attachment.image`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | str | ✓ | 落盘于 workspace `attachments/` 的路径(内容寻址:sha256 前 16 位命名) |
| `mime` | str | ✓ | 白名单:`image/jpeg|png|webp|gif`(魔数校验,不信扩展名) |
| `sha256` | str | ✓ | 内容哈希(64 hex);同哈希自动去重 |
| `w` / `h` | int | ✓ | 像素尺寸 |
| `size_bytes` | int | — | 文件大小(≤10MB,超限 `ATT-001`) |

- 校验:mime 不在白名单/大小超限 → ATT-001 拒;单消息 ≤5 张。触发者:`user`(F061,附属其后 user.message 轮)。
- 消费:模型侧视觉附加(受限时降级"图片已存档,可读元数据")、UI 缩略图。落盘:普通。
- 示例:`{"type":"user.attachment.image","actor":"user","payload":{"file_path":"attachments/9f2a1c…8b.txt.png","mime":"image/png","sha256":"9f2a…","w":800,"h":600}}`

### 3.2.5 `user.command`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | ✓ | 斜杠命令名(`new/undo/plan/budget/help/quit/…`) |
| `args` | str | — | 命令参数原文(可空串) |

- 校验:命中即写本事件并**不产生** user.message,不消耗 LLM;未知命令 → 不写本事件,回显 `system.error(code=EVT-105)` 会话继续。触发者:`user`(输入以 `/` 开头,F041)。
- 消费:命令处理器、CLI/Web 回显;危险操作不存在于此通道(一切执行走工具 + guard)。落盘:普通。
- 示例:`{"type":"user.command","actor":"user","payload":{"name":"plan","args":"归档 D:\\work"}}`
## 3.3 C — 模型侧

### 3.3.1 `llm.request`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | str | ✓ | 实际请求模型(降级链命中者) |
| `degraded_from` | str | — | 原主模型;非空=降级事实(F013) |
| `prompt_tokens` | int | — | 请求 token(未返回前可空) |
| `n_tools` | int | — | 本请求暴露工具数 |

- 校验:model 非空。触发者:`llm` 模块(chat/chat_stream 发起前,F012/F027)。
- 消费:预算计量(F029/F032)、降级审计;计数器可从事件重建。落盘:普通。
- 示例:`{"type":"llm.request","actor":"llm","payload":{"model":"qwen-max","degraded_from":"deepseek-chat","n_tools":12}}`

### 3.3.2 `llm.response`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | str | ✓ | 实际响应模型 |
| `finish_reason` | str | ✓ | `stop/tool_calls/length/content_filter/…` |
| `content` | str | — | 聚合后完整文本;**空串=工具调用轮** |
| `tool_calls` | list | — | 原生 tool_calls(id/name/arguments 原文),供 F022 回填 |

- 校验:content 与 tool_calls 至少其一;对应 llm.request 可查。触发者:`llm` 模块(完成时,F027 流式只聚合落一条)。
- 消费:reducer(有 content→`role=assistant`;空→置 pending 等 tool 配对,§4.2)、F022、UI。落盘:普通。
- 示例:`{"type":"llm.response","actor":"llm","payload":{"model":"deepseek-chat","finish_reason":"tool_calls","content":"","tool_calls":[{"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{\"path\":\"D:/work\"}"}}]}}`

### 3.3.3 `llm.usage`(计量,F029)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | str | ✓ | 计费模型 |
| `in_tokens` / `out_tokens` | int | ✓ | 输入/输出 token |
| `cache_hit` | int | — | 缓存命中 token |
| `cost_est` | float | — | 估算成本(单价表来自 config;仅报表,预算硬闸用 token) |

- 校验:in/out ≥0。触发者:`llm` 模块(每请求 report_usage);计数器只由本事件累加。
- 消费:预算控制器(F032)、会话/任务/模型三级聚合。落盘:普通。
- 示例:`{"type":"llm.usage","actor":"llm","payload":{"model":"deepseek-chat","in_tokens":812,"out_tokens":143,"cache_hit":0,"cost_est":0.0012}}`

### 3.3.4 `llm.error`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | str | ✓ | LLM-3xx 注册码或 `timeout`(F017) |
| `message` | str | ✓ | 人读错误(脱敏) |
| `retryable` | bool | ✓ | F028 依据(429/5xx/断网/超时=True,4xx 业务错=False) |
| `attempt` | int | — | 已重试次数 |

- 校验:code 必须注册(禁裸字符串,F019)。触发者:`llm` 模块(失败/超时)。
- 消费:重试/降级链(F013/F028)、审计。落盘:普通。
- 示例:`{"type":"llm.error","actor":"llm","payload":{"code":"LLM-302","message":"upstream 429","retryable":true,"attempt":2}}`

### 3.3.5 `agent.message`(最终展示文本)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | str | ✓ | 面向用户的最终文本,非空 |
| `model` | str | — | 来源模型(透传 llm.response) |

- 校验:content 非空;空响应不写。触发者:`agent`(agent-loop 在 llm.response 聚合后派生,F007)。
- 消费:UI/CLI 渲染、`user.feedback` 的 target 锚点(§3.2.3 校验依赖)、FTS。落盘:普通。
- 示例:`{"type":"agent.message","actor":"agent","payload":{"content":"已按主题归类,42 个文件移入 6 个文件夹。","model":"deepseek-chat"}}`

### 3.3.6 `llm.chunk`(瞬时,仅总线)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `delta` | str | ✓ | 流式增量文本碎片 |

- 校验:**禁止 append 入日志**——只经总线发 UI(PRD §3.3-C 明示;F027 聚合一致性由 llm.response 保证)。
- 触发者:`llm` 模块(流式迭代,每增量一次)。消费:UI 打字机渲染。落盘:**不入日志**。
- 示例:`{"type":"llm.chunk","actor":"llm","payload":{"delta":"已按主"}}`

### 3.3.7 `llm.retry`(F028 留痕;词表外扩展,§7 规则登记)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `attempt` | int | ✓ | 本次重试序号(0 起) |
| `delay_ms` | int | ✓ | 本次等待毫秒(±30% 抖动后) |
| `model` | str | — | 重试目标模型 |

- 校验:attempt < 上限(默认 4);耗尽转 `llm.error(LLM-303)` 交降级链。触发者:`llm` 模块(F028 每次等待前)。
- 消费:重试审计。落盘:普通(F028 代码明示 append)。
- 示例:`{"type":"llm.retry","actor":"llm","payload":{"attempt":1,"delay_ms":1400}}`

## 3.4 D — 工具侧(guard 与审批链)

> 工具轮完整事实序列:`tool.call` → `guard.evaluated`(必经)→ 拒绝:`guard.rejected` 停 / 危险:`approval.requested`→裁决 → `tool.result` 或 `tool.error`。INV-04/05:无 guard.evaluated 的 tool.result 即非法执行(F031 自检)。

### 3.4.1 `tool.call`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | ✓ | 注册表存在的工具名(否则 TLB-8xx) |
| `args` | dict | ✓ | **强类型校验后**参数(F026:多余字段拒绝) |
| `raw_args` | dict | ✓ | LLM 原始参数原文(INV-06 一致性审计) |
| `call_id` | str | ✓ | 与 llm.response.tool_calls[].id 一致 |

- 校验:args 强校验失败 → `TLB-803`,不执行不写本事件;同工具连续 2 次失败终止该轮。触发者:`agent`(F022 回填后、guard 前)。
- 消费:guard 链(F014)、执行管道、审计。落盘:普通。
- 示例:`{"type":"tool.call","actor":"agent","payload":{"name":"read_file","args":{"path":"D:/work","max_bytes":4096},"raw_args":{"path":"D:/work"},"call_id":"call_1"}}`

### 3.4.2 `guard.evaluated`(每调用必经,单调审计点)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tool` | str | ✓ | 被审工具名 |
| `decision` | str | ✓ | `allow / deny / need_approval` |
| `guard_ids` | list[str] | ✓ | 命中 guard 集(空列表=无命中) |
| `reasons` | list[str] | — | 逐条人读理由 |

- 校验:每个 tool.call 必有且仅有紧随其一条;decision 三值之一。触发者:`tool`(guard 链执行完,F014)。
- 消费:F031 覆盖自检、单调拒绝审计(原则 3)、need_approval → F015 审批。落盘:普通(配套 deny 由 guard.rejected 强同步)。
- 示例:`{"type":"guard.evaluated","actor":"tool","payload":{"tool":"shell_exec","decision":"deny","guard_ids":["g-danger-cmd"],"reasons":["命中危险命令表"]}}`

### 3.4.3 `guard.rejected`(强同步)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tool` | str | ✓ | 被拒工具名 |
| `guard_id` | str | ✓ | 命中 guard 标识 |
| `reason` | str | ✓ | 拒绝理由 |
| `policy_ref` | str | — | 策略出处 |

- 校验:前面必有 guard.evaluated(decision=deny);拒绝后零副作用(INV-05)。触发者:`tool`(F014 裁决 deny)。
- 消费:审计流(不进 LLM 上下文,§4.2)、UI 安全提示。落盘:**强同步**(§1.2)。
- 示例:`{"type":"guard.rejected","actor":"tool","payload":{"tool":"shell_exec","guard_id":"g-danger-cmd","reason":"rm -rf 高危操作","policy_ref":"builtin:danger-cmd.v1"}}`

### 3.4.4 `approval.requested`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tool` | str | ✓ | 待批工具名 |
| `args_summary` | str | ✓ | 参数摘要(人读,敏感脱敏) |
| `ttl_ms` | int | ✓ | 裁决有效期,默认 120_000 |
| `risk` | str | — | `high/critical` |

- 校验:本事件 seq 即 `approval_id`;60s 内同工具同参合并;headless 无通道→直接 denied(不写 requested)。触发者:`tool`(guard=need_approval → F015);审批期队列暂停(queue.suspended)。
- 消费:人类裁决通道(CLI/Web/ACP)、等待器按 approval_id 唤醒。落盘:**强同步**。
- 示例:`{"type":"approval.requested","actor":"tool","payload":{"tool":"shell_exec","args_summary":"del D:/old_cache/**","ttl_ms":120000,"risk":"high"}}`

### 3.4.5 `approval.granted / denied / timeout`(三结果同构)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `approval_id` | int | ✓ | = 对应 approval.requested 的 seq |
| `by` | str | ✓ | granted/denied:裁决人;timeout:恒为 `system` |
| `ttl_ms` | int | — | 透传请求 TTL |

- 校验:approval_id 必须指向已存在的 requested,否则 `EVT-101`;同一 requested 至多一个结果。触发者:`user`(granted/denied)/`system`(timeout=denied 安全默认,F015)。
- 消费:等待器唤醒;granted 后由 F014 **重入 guard 链**(非直接执行);denied/timeout 不执行。落盘:**强同步**(先落盘后执行/不执行)。
- 示例:`{"type":"approval.granted","actor":"user","payload":{"approval_id":15,"by":"alice"}}` · `{"type":"approval.timeout","actor":"system","payload":{"approval_id":15,"by":"system"}}`

### 3.4.6 `tool.result`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` / `call_id` | str | ✓ | 与 tool.call 一致(call_id=配对键) |
| `ok` | bool | ✓ | 执行是否成功 |
| `summary` | str | ✓ | 回喂 LLM 摘要,**≤2KB**,大输出只放引用 |
| `truncated` | bool | ✓ | 被截断?取消时已发生副作用如实写 `ok=True,truncated=True`(F025) |
| `spill_ref` | dict | — | F039 溢出引用 `{ref,chars,lines,preview(头500字符)}` |

- 校验:必有配对 tool.call;无 guard.evaluated → F031 告警(INV-04)。触发者:`tool`(执行管道)。
- 消费:reducer(配对 `role=tool`,§4.2)、F022、UI。落盘:普通。
- 示例:`{"type":"tool.result","actor":"tool","payload":{"name":"list_dir","call_id":"call_1","ok":true,"summary":"42 项:3 文件夹,39 文件…","truncated":false}}`

### 3.4.7 `tool.error`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` / `call_id` | str | ✓ | 配对键(同上) |
| `code` | str | ✓ | 结构化码或 `timeout`(工具默认 60s,F017) |
| `message` | str | ✓ | 错误文本(脱敏),回喂 LLM |

- 校验:必有配对 tool.call。触发者:`tool`(失败/超时)。
- 消费:**不进历史派生**(§4.2 注:错误回喂由 F022 在下一轮 llm.request 构造时注入,保 reducer 纯映射稳定);审计、UI。落盘:普通。
- 示例:`{"type":"tool.error","actor":"tool","payload":{"name":"read_file","call_id":"call_2","code":"TLB-802","message":"参数校验失败:路径不存在"}}`
## 3.5 E — 编排与系统侧(阶段 4-6 扩展)

> 家族字段表约定:列 `适用` 标注字段属于哪些事件(未列即不携带);每事件单独给校验差异与示例。触发者一律 `system`(队列/调度/框架)或 `agent`(编排产物),已注明除外。

### 3.5.1 任务与段:`task.enqueued/started/completed/failed` + `segment.start/end`(F043/F044)

| 字段 | 类型 | 必填 | 适用 | 说明 |
|---|---|---|---|---|
| `task_id` | str | ✓ | 六事件 | 任务 id(一次用户意图=一个任务);无任务上下文的闲聊不携带 |
| `pos` | int | ✓ | enqueued | 入队位置(1 起) |
| `reason` | str | ✓ | completed/failed | 完成/失败原因(失败可含错误码) |
| `error` | str | — | failed | 结构化错误文本(脱敏) |
| `start_seq` | int | ✓ | segment.end | = 对应 segment.start 的 seq(查询闭区间 `[start_seq,end_seq]`) |

- 必填校验:enqueued 队深 <32(满 → `QUE-001`);started 前必有 enqueued;终态事件每任务至多一个;segment.start/end 一一配对、不嵌套(子 Agent 独立 session)。
- 消费方:队列泵(F043,同一时刻仅一个 running)、UI 队列、段切片查询与回放(`events_between`)、compaction 最小折叠单位(F058)。
- 落盘:segment.start **强同步**(段锚先落,崩溃后段不悬空);其余普通。
- 示例:`{"type":"task.enqueued","actor":"system","payload":{"task_id":"t-7","pos":1}}` · `{"type":"task.started","actor":"system","payload":{"task_id":"t-7"}}` · `{"type":"task.completed","actor":"system","payload":{"task_id":"t-7","reason":"ok"}}` · `{"type":"task.failed","actor":"system","payload":{"task_id":"t-7","reason":"budget"}}` · `{"type":"segment.start","actor":"system","payload":{"task_id":"t-7"}}` · `{"type":"segment.end","actor":"system","payload":{"task_id":"t-7","start_seq":14}}`

### 3.5.2 方案与目标:`plan.proposed/approved/rejected/exec.step` + `goal.created/updated/completed`

| 字段 | 类型 | 必填 | 适用 | 说明 |
|---|---|---|---|---|
| `plan_id` | int | ✓ | approved/rejected/exec.step | = plan.proposed 的 seq |
| `goal` | str | ✓ | proposed | 用户目标 |
| `steps` | list | ✓ | proposed | ≤8 步 `{action,tool,expected,risk}`;危险步标"审批点" |
| `selfcheck_ok` | bool | — | proposed | 可执行性/依赖自检 |
| `step_idx` / `action` | int/str | ✓ | exec.step | 已执行步下标(0 起)/动作摘要 |
| `who` | str | — | approved/rejected | 裁决人;方案 24h 未确认自动 rejected(who=system) |
| `goal_id` | str | ✓ | goal 三事件 | 目标 id |
| `status` | str | ✓ | goal.updated/completed | `active/paused/done/abandoned` |
| `desc`/`note` | str | — | created/updated | 目标描述/变更说明 |

- 必填校验:approved/rejected/exec.step 的 plan_id 必须指向存在的 plan.proposed,否则 `EVT-101`;拒绝后不再推进;goal.updated/completed 前必有 goal.created,completed 后不可再 updated。
- 消费方:plan 执行器、UI 方案弹窗/审批(F045)、目标看板;compaction 保留未完成 goal/plan(F058 不可折叠集)。
- 落盘:普通(proposed 展示前先落)。触发者:proposed/exec.step=`agent`;approved/rejected=`user`;goal.completed=`system`。
- 示例:`{"type":"plan.proposed","actor":"agent","payload":{"goal":"归档 D:\\work","steps":[{"action":"扫描","tool":"list_dir","expected":"清单","risk":"low"}],"selfcheck_ok":true}}` · `{"type":"plan.approved","actor":"user","payload":{"plan_id":20}}` · `{"type":"plan.exec.step","actor":"agent","payload":{"plan_id":20,"step_idx":0}}` · `{"type":"goal.created","actor":"agent","payload":{"goal_id":"g-1","desc":"归档 work"}}` · `{"type":"goal.completed","actor":"system","payload":{"goal_id":"g-1","status":"done"}}`

### 3.5.3 子 Agent 与调度:`subagent.spawned/joined/failed` + `schedule.trigger` + `job.started/completed/failed` + `workflow.step` + `queue.suspended/resumed`

| 事件 | 必填字段表(字段:类型=说明) | 校验与消费方 | 落盘 |
|---|---|---|---|
| `subagent.spawned` | `sub_id`str=子 session_id;`parent_seq`int=主会话发起轮 seq;`task`str—=委派描述 | 子 Agent **独立 session**(F044 段不嵌套),主会话只记委派/回收事实;joined/failed 前必有 spawned。消费:编排者回收结果入下一轮请求。触发者:`agent` | 普通 |
| `subagent.joined` | `sub_id`str;`summary`str=结果摘要 | 同上;失败走 failed | 普通 |
| `subagent.failed` | `sub_id`str;`summary`str=失败原因 | 同上;失败原因可含错误码 | 普通 |
| `schedule.trigger` | `job`str=cron 任务名;`cron`str;`fired_at`str=ISO8601 | cron 非法 → CFG-6xx 拒;触发即入队。消费:调度器 | 普通 |
| `job.started` | `job_id`str | 后台 job;completed/failed 前必有 started | 普通 |
| `job.completed` | `job_id`str;`elapsed_ms`int— | 消费:job 管理器/UI;取消归一化为 failed(cancelled)(F025) | 普通 |
| `job.failed` | `job_id`str;`elapsed_ms`int—;`reason`str— | 超预算直接 kill → reason=budget(F032);超时只标该 job(F017) | 普通 |
| `workflow.step` | `wf_name`str;`step_idx`int;`action`str;`state`str— | wf_name 已注册;消费:workflow 断点续跑/UI 进度。触发者:`agent` | 普通 |
| `queue.suspended` | `reason`str=审批等待/预算暂停;`by`str— | 先 suspended 后 resumed,重复挂起合并;消费:队列泵(F043)、预算(F032) | 普通 |
| `queue.resumed` | `reason`str=裁决返回/预算恢复 | 同上 | 普通 |

- 示例:`{"type":"subagent.spawned","actor":"agent","payload":{"sub_id":"s-sub9f2a","parent_seq":31,"task":"列目录"}}` · `{"type":"subagent.joined","actor":"agent","payload":{"sub_id":"s-sub9f2a","summary":"3 层 47 项"}}` · `{"type":"schedule.trigger","actor":"system","payload":{"job":"nightly-report","cron":"0 2 * * *","fired_at":"2026-09-06T18:00:00Z"}}` · `{"type":"job.failed","actor":"system","payload":{"job_id":"j-3","reason":"budget"}}` · `{"type":"workflow.step","actor":"agent","payload":{"wf_name":"report-pipeline","step_idx":2,"action":"汇总"}}` · `{"type":"queue.suspended","actor":"system","payload":{"reason":"approval-pending"}}`

### 3.5.4 `fork.created`(会话分叉,F059;强同步)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `new_session_id` | str | ✓ | 新会话 id(其 seq 从 1 起) |
| `base_seq` | int | ✓ | 分叉点:主会话**已落盘** seq(COW 共享区 `[1..base_seq]` 只读) |
| `reason` | str | — | 分叉说明 |

- 必填校验:base_seq ≤ 主会话 last_persisted_seq(未落盘不可 fork);凭据/策略继承主会话快照。触发者:`system`(F059)。
- 消费方:COW 链接表(session_links 父子关系)、会话列表。落盘:**强同步**(分叉锚必须持久)。
- 示例:`{"type":"fork.created","actor":"system","payload":{"new_session_id":"s-fork7b1e","base_seq":55,"reason":"实验另一归档策略"}}`

### 3.5.5 `context.compacted`(压缩声明,F058;强同步)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ranges` | list[[lo,hi]] | ✓ | 折叠段闭区间列表(即 PRD §3.3 要点 `dropped_seq_range` 的段级形态,统一写 ranges) |
| `summary` | str | ✓ | 折叠区摘要(代区间进入派生历史) |
| `tokens_before`/`tokens_after` | int | — | 压缩前后派生 token(留痕) |

- 必填校验:ranges 各区间存在且不重叠;触发=派生历史 ≥75% 窗口且新增 ≥10 轮;不可折叠:最近 12 轮、未完成 plan/goal/todo、待审批、guard 拒绝记录;折叠单位=整段不切半。触发者:`system`。
- 消费方:reducer(以摘要代区间 → `[已压缩…]` system 消息,§4.2)、窗口预算、审计展开(原文仍在日志,"压缩但不撒谎")。落盘:**强同步**(空洞合法化声明必须持久,否则回放误报空洞)。
- 示例:`{"type":"context.compacted","actor":"system","payload":{"ranges":[[1,13]],"summary":"用户要求整理文件,已确认按类型归档。","tokens_before":52100,"tokens_after":2300}}`

### 3.5.6 系统杂项速查(plugin/bus/cancel/error/todo/syscheck)

| 事件 | 必填字段表(字段:类型=说明) | 校验与消费方 | 示例 |
|---|---|---|---|
| `plugin.installed`(F004/F006) | `plugin_id`str;`version`str;`api_version`str(不匹配拒装载) | 校验:id 非脊柱保留名(BUS-002)、依赖拓扑满足;消费:热插拔审计。触发者:`plugin` | `{"type":"plugin.installed","actor":"plugin","payload":{"plugin_id":"echo","version":"0.1.0","api_version":"1"}}` |
| `plugin.uninstalled`(F004) | `plugin_id`str | 校验:非 running 态(BusyUninstall);消费:卸载审计 | `{"type":"plugin.uninstalled","actor":"system","payload":{"plugin_id":"echo"}}` |
| `bus.backpressure`(F005) | `sender`str;`dropped`int=累计丢弃;`sample`list—(≤100 死信样本) | 校验:在途>1000 才发(可配);消费:背压观测,禁静默丢事件 | `{"type":"bus.backpressure","actor":"system","payload":{"sender":"plugin:feeder","dropped":3}}` |
| `system.cancelled`(F025) | `what`str=被取消对象;`reason`str— | 校验:取消 re-raise 不吞;消费:取消审计(防"以为停了还在跑") | `{"type":"system.cancelled","actor":"system","payload":{"what":"run#5","reason":"ctrl-c"}}` |
| `system.error`(F019/F041) | `code`str(必注册);`hint`str;`ctx`dict— | 消费:错误 UI/诊断;未知命令 EVT-105、落盘故障 PERS-202 都经此 | `{"type":"system.error","actor":"system","payload":{"code":"EVT-105","hint":"未知命令 /xx"}}` |
| `todo.updated`(F040) | `task_id`str;`todos`list(≤20 项 id/text/done) | 校验:done 重复幂等;消费:任务清单、崩溃续跑、compaction 清 done 项 | `{"type":"todo.updated","actor":"agent","payload":{"task_id":"t-7","todos":[{"id":1,"text":"扫描","done":true}]}}` |
| `syscheck.fail`(F031) | `findings`list(SEQ-GAP/CACHE-STALE/NO-GUARD-EVENT+seq);`trigger`str— | 校验:每 100 事件或 1min 自检;失败不杀进程,提示 repair;消费:不变量线上告警 | `{"type":"syscheck.fail","actor":"system","payload":{"findings":[{"kind":"NO-GUARD-EVENT","seq":44}]}}` |
---

# 4 派生历史 reducer 规范

> 实现见 DIS-CORE §3.3.2(`derive_history`,唯一权威);本节做协议定义与边界裁定,不重复代码。

## 4.1 定位与纯函数约束

- **输入**:有序事件序列(seq 升序,全量或自 base_seq 的尾部切片)+ `max_tokens` 窗口余量(scope.window_tokens 联动)。**输出**:LLM 消息列表(OpenAI 形态;工具轮 = assistant 空文本 + tool 配对)。
- **纯函数约束**:无副作用、不落盘、不读外部状态;同输入必得同输出;可整体重建并与增量缓存逐事件比对(INV-03)。**禁止第二份历史存储**(INV-01)——"内存说 A 日志说 B"结构上不可能。
- reducer 是"日志事实→模型可见文本"的唯一翻译闸门;UI/FTS/预算等各有独立投影,不共享中间态。

## 4.2 事件 → 消息映射表

| 事件 | 派生动作 |
|---|---|
| `user.message` | append `{role:user, content}` |
| `user.message_edited` | 以 `new_content` 覆盖 `target_seq` 位置(原文留痕,取新版) |
| `context.compacted` | append `{role:system, content:"[已压缩 {ranges}] {summary}"}`(空洞合法化锚点) |
| `llm.response`(content 非空) | append `{role:assistant, content}` |
| `llm.response`(content 空) | 不 append;置 `pending=该 seq`(工具调用轮) |
| `tool.result`(pending 非空) | append `{role:tool, content:summary, name}`;清 pending |
| `tool.error` | **不进派生历史**:回喂由 F022 在下一轮 `llm.request` 构造时注入(请求层职责),保映射稳定 |
| `guard.rejected`/`guard.evaluated` | 不进 LLM 上下文,仅留审计流(§6 可查、UI 可展示) |
| `user.feedback`/`agent.message`/其余 `llm.*`/E 组 | 不直接进历史;各自投影消费(reducer 不读) |
| `llm.chunk` | 日志中不存在,天然无映射 |

配对规则:工具轮 = 空 content 的 llm.response 之后、由 `pending` 承接的首个 tool.result(其 `call_id` 应与该轮 tool_calls 内一致);多调用轮的完整配对由 F022 在请求构造层展开(reducer 只保证单配对投影,与 DIS-§3.3.2 一致)。

## 4.3 增量折叠与缓存纪律

- 折叠代数:`state_0=[]; state_n = fold(state_{n-1}, ev_n)`——每条事件只作用于当前尾部,天然支持增量追尾。
- `history_cache` 仅在日志尾部未变时有效;任何 append 后整体失效;`rebuild_from_log()` 可随时整体重建(INV-03 比对)。
- 窗口超限:头部截断(`truncate_head`);压缩职责归 F058(§3.5.5),reducer 不承担摘要。
- 空洞语义:遇 compacted 声明区间 → 摘要代区间;无声明空洞 → 告警(warn_hole,F031),reducer 照常线性折叠不中断。

---

# 5 JSONL 物理格式与落盘

> 实现见 DIS-CORE §8(persistence 模块);本节为协议级物理约束。

## 5.1 文件与行结构

- 路径:`~/.pyharness/sessions/{session_id}.jsonl`(可配);**一行一事件**,信封 + payload 拍平单行,UTF-8 无 BOM。
- 行 = `Envelope.model_dump_json()` 输出(字段序稳定,便于 diff/审计)+ `\n`。**事件内禁止字面换行**:文本含 `\n`/`\r` 必须经 JSON 转义;Windows 路径反斜杠转义为 `\\`(PRD §3.6 行样例)。
- 空行容忍:读侧跳过(轮转残留),不视为坏行([DIS-§8.3.2]);单文件 >50MB 轮转 `{sid}.{n}.jsonl`,重放按序号合并([DIS-§8.3.4])。

## 5.2 写策略(双速 flush)

| 档位 | 触发 | 语义 |
|---|---|---|
| 强同步 | `sync=True` 或事件 ∈ SYNC_TYPES(user.message / guard.rejected / approval.*) | 立即 `write + flush`,成功才返回;失败抛 PERS-202([DIS-§8.3.1]) |
| 攒批 | 其余事件 | 入 `_pending`;≥64 条或 ≤0.5s 定时器批量 flush |

- 崩溃一致性:**至多丢强同步点之后 ≤0.5s 攒批窗事件**;丢的由 repair 截断 + `session.recovered` 声明,绝不静默。
- 写失败:强同步路径直接抛 PERS-202;攒批路径入 `_retry_q` 重试 ≤3 次,仍败 → `system.error(PERS-202)` + 会话暂停(**拒新不丢旧**)。

## 5.3 读与损坏检测

| 损坏形态 | 检测时机 | 处置 |
|---|---|---|
| 中部坏行(JSON 解析失败) | 回放/repair 逐行 `Envelope.model_validate_json` | 记 `PERS-201` 跳过,进 quarantine 隔离,**绝不中断回放**;删否由用户定(F060) |
| 尾部半行(写中断) | 打开时/repair 扫尾 | repair 截断 + recovered 声明(未完成事实不假装发生) |
| seq 空洞 | 回放 open_session | 有 compacted/recovered 声明 → 合法;否则告警 + F031 深查 |
| 派生视图落后 | repair 对账 | FTS/storage 整体重建(原则 1:派生可弃) |

repair 幂等:同日志重复修复结果一致;修复前强制备份 `.jsonl.bak-{ts}`([DIS-§8.3.3])。

---

# 6 校验规则 → 错误码 → 处置映射表

## 6.1 处置三分法

| 处置 | 语义 | 适用 |
|---|---|---|
| **拒写** | 事件不进内存/总线/日志,调用方得结构化错误 | 信封非法/类型未注册/seq 乱序/终态违规/先于 created |
| **告警(继续)** | 事件已写,系统记录告警提示跟进 | 订阅者异常、未知类型回放、空洞无声明 |
| **修复(repair)** | 走 F060:截断/隔离/重建后以 recovered 声明 | 文件损坏、可疑丢事件、视图落后 |

## 6.2 EVT 码总表(检测时机=写入路径:session.append 入口)

| 码 | 违规 | 检测时机 | 处置 | 动作 |
|---|---|---|---|---|
| `EVT-100` | 信封字段非法 / payload 模型失败 / 空消息 / feedback、edited 的 target 非法(BadTarget) | 校验链 §2.2 第 1/3 步 | 拒写 | 返回结构化错误(含字段明细),修正后重试 |
| `EVT-101` | seq 不连续/重复;approval_id、plan_id、target_seq 指向不存在事件 | 第 4 步 + §2.4 | 拒写 | 疑丢事件 → 提示跑 repair(F060) |
| `EVT-102` | 未知事件类型(未注册) | 第 2 步 | 拒写 | 插件先注册类型 schema 才能 emit(§7) |
| `EVT-103` | 订阅者/谓词 handler 抛异常 | 总线分发(F002) | 告警 | 捕获跳过该订阅,不中断其他订阅者 |
| `EVT-104` | finished 后任何 append / finished 重复 / closed 写 | 第 5 步 | 拒写 | 查绕过路径(INV-01);终态不可逆 |
| `EVT-105` | 未知斜杠命令 | user.command 处理(F041) | 告警 | `system.error(EVT-105)` 回显,会话继续 |
| `EVT-106` | 事件先于 session.created | 第 5 步 | 拒写 | 首事件必须是 session.created(seq=1) |

## 6.3 关联域码(命中时按域引用;语义详见 ERR.md)

| 码域 | 代表 | 处置 |
|---|---|---|
| PERS-2xx | `PERS-201` 坏行(记跳交 repair);`PERS-202` 落盘失败(强同步点抛错 → 会话暂停,repair 后恢复) | 修复/暂停 |
| TLB-8xx | `TLB-801` 重名、`TLB-803` 参数强校验失败 | 拒执行,不写 tool.call |
| ATT-001 | 附件超限/类型非法(F061) | 拒写附件事件 |
| EDT-001 | 编辑超 30 分钟窗(F063) | 拒写 edited |
| QUE-001 | 任务队列满(F043) | 拒入队 |
| CFG-6xx / CRED-7xx / LLM-3xx | 配置/凭据/模型错误(经 llm.error / system.error 落事件) | 按各自流程 |

新增校验必须登记:码 + 检测时机 + 处置档位 + 测试,禁裸 raise str(F019)。

---

# 7 版本演进规则

1. **事件类型只增不改**:既有 type 语义与必填字段**冻结**;破坏性变更 = 新增类型(如 `user.message_v2`),旧类型永不改写(旧会话回放不中断)。
2. **payload 演进**:只许加**可选**字段(带默认值);新必填字段 = 新类型或随大版本协商;字段类型/枚举收缩一律视为破坏性。
3. **schema 版本**:每个事件模型登记 `since_version`;未知类型回放 → 跳过 + 警告,绝不中断(§5.3 同纪律)。
4. **错误码**:一经发布不改含义;可新增码(码 = 语义契约,F019)。
5. **词表登记制**:框架内新增类型须过 §3 词表与本文件登记(含 §1.3 通道二选一、字段表、消费方);插件事件经注册表动态登记(命名空间 `plugin.<id>.<name>` 防冲突),登记即受 §2.2 校验链约束。
6. **迁移实例**:E 组事件在阶段实现前属"冻结待激活"——schema 已定义可先冻结;激活时只许加可选字段,不许改 §3 已列语义。

---

# 8 事件与持久化边界

## 8.1 落盘矩阵

| 类别 | 事件 | 落盘 | 理由 |
|---|---|---|---|
| 强同步(立即 write+flush) | `user.message`、`guard.rejected`、`approval.*`、`session.finished`、`session.recovered`、`segment.start`、`fork.created`、`context.compacted` | ✓ 持久 | §1.2 三类核心 + 终态/锚点/空洞声明(声明不持久则重放语义崩坏) |
| 普通(攒批 ≤0.5s/64 条) | §3 其余持久事件(created/renamed/llm.*/tool.*/task.*/plan.*/goal.*/job.*/subagent.*/queue.*/plugin.*/system.*/todo.*/bus.backpressure 等) | ✓ 持久 | 事实留痕;崩溃丢 ≤0.5s 窗由 recovered 声明 |
| 仅内存(仅总线) | `llm.chunk`(明示)、`registry.updated`(F003 广播) | ✗ 不落 | 高频可重算/碎片无审计价值(§1.3 准则) |

## 8.2 强同步点清单(崩溃一致性边界锚)

按序落定即"用户可感知事实已持久":① user.message 提交;② guard 拒绝;③ 审批裁决;④ 会话终态;⑤ 分叉/压缩/修复声明;⑥ 任务段开始。崩溃后重放 = 从最后完整行继续,丢失窗口由 `session.recovered.fixed/lost` 显式列出。

## 8.3 边界裁定

- **只读边界**:session 模块外无 append 路径(INV-01);订阅者可读不可写回日志;派生视图(UI/FTS/预算)只读日志投影。
- **写者边界**:单进程单写者(INV-07);禁止跨进程打开同一 `{sid}.jsonl` 写(实现见 DIS-§8)。
- **内存态**:`_cache/_folded/_seq/history_cache` 均可由日志重建;`_folded`(折叠区间索引)与 compacted 事件保持推导一致,崩溃后重放重建。
- **跨会话**:fork 的 COW 共享区只读(§3.5.4);子 Agent 独立 session(§3.5.3);父会话事件不落子会话文件。
- **凭据边界**:事件内容全出口脱敏(INV-09):payload/错误消息含疑似 key(sk- 等 32+ 位)→ 写前打码(F016)。

---

**关联文档**:PRD-Core.md §3(权威,冲突以 PRD 为准)/§4.1/§5 · DIS-CORE.md §3(session)§8(persistence)· MAP.md · ADD.md(ADR-001/006)· 架构设计.md · ERR.md(EVT-1xx/PERS-2xx 详细表)。
