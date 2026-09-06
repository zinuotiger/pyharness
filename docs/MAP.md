# MAP.md — PyHarness 架构总览图(分层全景 · 数据流 · 依赖 · 词汇)

> **定位**:PyHarness(Python 复刻 DSH 架构思想)的**图形化架构索引**——把 TECH-ANCHOR 六条原则与 PRD-Core 的四层架构、66 项功能浓缩为 5 张 ASCII 图 + 映射表 + 词汇表。**先看图,再按需读字。**
> **权威声明**:本文是**视图**,不引入新设计。凡功能编号(F001-F066)、事件、模块职责、依赖方向与 PRD-Core.md 冲突,**一律以 PRD-Core.md 为准**(其 §2.6 已声明);ADD.md 的 ADR 若冲突亦同。
> **关联文档**:TECH-ANCHOR.md(仓库根:原则/禁止表)· PRD-Core.md(本 docs/ 目录,权威)· ADD.md(ADR,同批次产出)· 架构设计.md(v0.1 原型,已被 PRD §2 吸收)
> **读者**:新人先读本文;面试用第 1/3/4 章复述"一次对话怎么穿层";动手前用第 2/5 章定位模块。

**7 章**:1 分层全景图 · 2 六阶段映射表 · 3 一次对话数据流 · 4 工具调用四关 · 5 进程内依赖图 · 6 使用指南 · 7 词汇表

---

## 1 分层架构全景图

单进程四层架构:一切能力同处一个进程、一个 asyncio 事件循环(原则 5);层间只有函数调用与事件投递。自下而上:①插件总线地基 → ②核心脊柱 8 模块 → ③外围能力层 → ④外壳层。

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         用户 / 外部程序                                │
│          文本 · 图片 · 斜杠命令 · JSON-RPC · HTTP/SSE                  │
└───────────────────────────────────┬──────────────────────────────────┘
                                    │ 只做协议适配,零业务逻辑
┌───────────────────────────────────▼──────────────────────────────────┐
│ ④ 外壳层 shell(阶段6 · 可插拔入口 · 无自己的主循环)                     │
│   CLI(pyharness chat/run/plan/...)   IO/流式渲染/审批交互              │
│   Desktop(Windows 桌面程序:pywebview 壳 + FastAPI 同进程 · SSE)       │
│           会话列表/对话/Agent轨迹时间线回放/审批弹窗/预算仪表盘           │
│   ACP 桥(JSON-RPC over stdio)        把引擎变成可编程组件             │
│   —— 三壳共用同一内核入口 = 同一事件流的不同投影                        │
├──────────────────────────────────────────────────────────────────────┤
│ ③ 外围能力层(40+ 能力 · 全可插拔 · 挂 ctx.* · Definition+Provider)     │
│   ctx.tools   文件读写/列目录/Web搜索/抓取/subprocess/PTY(F034-038,52-53)│
│   ctx.agent   队列/plan/goal/schedule/subagent/workflow/jobs(F043-051)│
│   ctx.session fork/compaction/FTS查询/repair(F057-060)                │
│   ctx.storage 会话级域 KV(F056)      ctx.sys sandbox/workspace(54-55) │
│   ctx.ui      附件/反馈/编辑(F061-063)                                 │
│   —— 无主循环:只在脊柱驱动的会话里被调用,动作以事件留痕                 │
├──────────────────────────────────────────────────────────────────────┤
│ ② 核心脊柱 8 模块(每次对话必经 · 启动硬接线 · 不可换不可热插拔)          │
│   agent-loop    循环驱动器:三态机 idle/running/terminated             │
│   agent         会话实体 + ctx 门面(能力 seam 消费端总闸)              │
│   session       事件日志门面:append/回放/派生历史/订阅                 │
│   llm           DeepSeek 客户端 + 降级链 + 流式/重试/用量              │
│   system-prompt 系统提示词组装 + 护栏段注入(F024)                      │
│   scope         作用域:权限边界/预算/上下文窗口/危险标记集             │
│   tools         工具注册表总闸:name→Definition→Provider               │
│   persistence   JSONL 落盘/轮转/损坏检测/崩溃恢复入口                  │
│   —— 注册表保留这 8 个名字(BUS-002),插件不得同名覆盖                   │
├──────────────────────────────────────────────────────────────────────┤
│ ① 插件总线地基(阶段0 · 自研 Cordis 等价物 · 单进程内唯一通信通道)        │
│   事件总线 emit/subscribe(只中转不落盘)                                 │
│   分发器 精确/通配(tool.*)/谓词过滤   背压 同 sender FIFO,拒新不丢旧   │
│   注册表 插件/工具/能力三类索引(脊柱名保留 BUS-002)                     │
│   热插拔 install/uninstall/activate/deactivate + 五态生命周期          │
└──────────────────────────────────────────────────────────────────────┘
        │ 所有会话事件同时被日志订阅者捕获
        ▼
   ┌────────────────────────────────────────────────────┐
   │ 会话事件日志 = JSONL append-only = 唯一真源(原则1)     │
   │ 消息历史/UI 渲染/FTS 索引全是它的派生视图,不存第二份    │
   └────────────────────────────────────────────────────┘
```

**读图要点**:① **单进程**——唯一进程出口是显式工具 subprocess/PTY,且必须过 guard;② **脊柱不可换是结构性的**——注册表 RESERVED 写死 8 名,卸载脊柱模块直接 BUS-002;③ **事件双写**——事件先经总线分发,由日志订阅者追加 JSONL,**追加成功才算生效**;④ **外壳无特权**——写操作全走 session.append,与用户输入同级过校验与 guard。脊柱职责/ctx 命名空间/代码目录对照 PRD §2.2/2.3/2.6。

---

## 2 六阶段功能 ↔ 模块 ↔ 文档映射表

66 项 F001-F066 按 6 阶段推进(分布 6/20/7/9/9/8/7);**每阶段有可运行演示,阶段门没过不进下一阶段**(原则 6)。文档描述最终态,代码按阶段生长。

| 阶段 | 功能编号(项数) | 主要模块(脊柱∩能力) | 对应文档 | 阶段门(演示命令) |
|---|------|------|------|------|
| 0 插件总线 | F001-F006(6) | bus:总线内核/分发器/注册表/热插拔/背压/生命周期 | PRD §5.1;DIS-SEAM.md(总线展开);specs/plugin_bus | 双插件互发事件、热卸载后事件停(`python -m pyharness.demo_bus`) |
| 1 核心脊柱 | F007-F026(20) | 脊柱 8 模块全量 + guards/审批/凭据/错误码/配置/护栏/取消/强校验 | PRD §5.2;DIS-CORE.md;EVENT-SCHEMA.md;SECURITY.md | CLI 单轮对话→JSONL→重启回放;危险工具被 guard 拒(`pyharness chat --once "你好"`) |
| 2 模型加厚 | F027-F033(7) | llm(流式/重试/用量/多适配器/探针)+ scope(预算联动)+ 自检 | PRD §5.3;ADI.md;CFG.md | 流式;断网自动重试;401 降级 qwen-max(`pyharness chat --stream`) |
| 3 工具能力 | F034-F042(9) | ctx.tools(fs/Web搜索抓取/spill/todo)+ 斜杠命令 + 标题派生 | PRD §5.4;DIS-SEAM.md;specs/tool_fs | "整理文件夹按主题归类"全自动(`pyharness run "整理 D:\杂乱文件夹"`) |
| 4 任务编排 | F043-F051(9) | ctx.agent:队列/独立日志段/plan/goal/schedule/subagent/workflow/jobs | PRD §5.5;DIS-CORE.md(编排部分);specs/jobs | plan:方案→批准→执行;定时触发(`pyharness plan "每周备份笔记"`) |
| 5 系统能力 | F052-F059(8) | ctx.tools(subprocess/PTY)+ ctx.sys(sandbox/workspace)+ ctx.storage(KV)+ ctx.session(FTS/compaction/fork) | PRD §5.6;SECURITY.md;EVENT-SCHEMA.md(compacted 声明) | workspace 内 subprocess/PTY;FTS 命中;compaction 续跑;fork(`pyharness search "备份"`) |
| 6 周边+外壳 | F060-F066(7) | ctx.session(repair)+ ctx.ui(附件/反馈/编辑)+ 外壳 CLI/**Desktop(pywebview)**/ACP | PRD §5.7;DEP.md;specs/cli、desktop | kill -9 后 repair 恢复;双击 exe 桌面对话(轨迹回放+审批弹窗);ACP 驱动(`pyharness-desktop` / `pyharness acp`) |

**文档口径**:PRD-Core 是**唯一权威功能规格**(已产出);DIS/EVENT/SECURITY/ADI/CFG/DEP 与 specs/ 系列按 TECH-ANCHOR 分派计划在后续批次产出,是 PRD 的伪代码级/字段级展开——**落地前一律以 PRD-Core §5 对应章节为准**;ADD.md 记录"为什么这样设计"(ADR),与本文同批次并行。

---

## 3 数据流:一次对话从输入到完成

事件侧原则:**每步动作先成为事件 → 总线分发 → 日志订阅者落盘(append-only) → 再继续下一步**;落盘即事实,后续一切视图从日志派生。

```text
步  动作者                   动作                                           落盘事件(seq 递增)
─  ──────                   ─────                                           ──────────────
1  用户                     输入文本/图片/斜杠命令                              —
2  外壳(CLI/Desktop/ACP)    协议适配:变成一次 submit 请求,零业务逻辑            —
3  session(F009)            session.append(user.message)                      user.message
                            —— 强同步点①:日志订阅者落盘成功后才 ACK 外壳
4  agent-loop(F007)         唤醒: idle → running(同会话仅一个 running,          —
                            新输入在 running 时入队)
5  system-prompt(F010)      组装上下文 = 模板 + session.derive_history()        —
                            (从日志现算,截窗)+ 护栏段(F024);无第二份历史
6  scope(F017/F032)         前置:预算够?轮数未超?窗口未超?(超限先 compaction)   —
7  llm(F012/F013)           llm.chat(messages, tools=当前 schema)               llm.request/response
                            用量记账(F029);失败走降级链+指数退避(F028)
                            │
                            ├─ 返回纯文本 → 8a
                            └─ 返回 tool_calls → 8b
8a agent                    agent.message 事件 → 外壳渲染 → 回 idle              agent.message
8b tools+guards             tool_calls 逐个过四关(§4):契约校验→guard→审批→      tool.call / guard.rejected /
                            Provider 执行→结果检查→tool.result → 回到步骤 5       tool.result / tool.error
9  任意时刻                 轮数>30 / 预算>1元 / 超时(10s/60s/180s)/ 取消(F025)   session.finished
                            → 强制终态,无"再给一次机会"通道                      (reason=idle/timeout/budget/error)
```

主循环形状:`(3)user.message → (4)idle→running → (5)组装上下文 → (6)scope 前置 → (7)llm.chat → 纯文本则自然终态回 idle / tool_calls 则 (8b)过四关 → tool.result 回上下文 → 回 (5)`,直到纯文本或三闸强制终态(步骤 9)。每轮工具结果以 tool.result 事件供下一轮 LLM 消费;崩溃落在任意两步间都不丢已落盘事实(F060 repair 收尾)。

---

## 4 工具调用链路:四关(guard → 校验 → 执行 → 检查)

LLM 是系统里最不可信组件(原则 4):其 tool_calls 只是"意图",要变成系统动作必须穿过四关。**落地顺序以 PRD §2.4/§4.4 为准:契约校验(先验后跑)先于 guard 链;guard 链内 g1 schema 对内部调用再做复查。** "guard → 校验 → 执行 → 检查"是安全视角简记,其中"校验"指 guard 链内 schema 复查;下图按执行落地顺序画全。

```text
LLM 返回 tool_calls(raw JSON —— 不可信:畸形/编造/被注入)
   │ F022 解析出 (name, raw_args)
   ▼
关1 · 契约校验(F026/F008,参数先验后跑)
   tools 注册表查 Definition → raw_args 过 pydantic 强类型转换
   查不到 → TLB-802;类型失败 → TLB-803;均回喂 LLM 修正
   —— 不允许:部分参数通过 / 原样透传 / 隐式类型修正后执行
   ▼
关2 · guard 单调拒绝(F014/F023/F015,原则3)
   scope 前置:工具在当前 scope 可见? 不可见 → REJECT(终局)
   guard 链按注册序:g1 schema → g2 danger → g3 fs_workspace →
     g4 credential_read → g5 net_outbound(插件 guard 只加严)
   任一 reject → REJECT 终局:guard.rejected 强同步落盘②
     (含 guard_id+policy_ref),其后零副作用
   danger≥high → 人类审批(F015);critical → 直接拒绝不可审批
   审批通过 ≠ 放行:请求重入 guard 链起点(策略可能已变)
   ▼
关3 · 执行(Provider 运行)
   全链 allow → Provider 执行(换实现不碰消费方,能力 seam)
   全程在笼子里:scope 权限 + 预算(F032)+ 超时(F017)+ 取消传播(F025)
   同步 handler 走线程池不阻塞事件循环;失败 → tool.error(错误码)
   ▼
关4 · 结果检查(落地前最后一道)
   输出超长 → spill 截断(F039)留痕,防打爆上下文与预算
   日志 args 与执行 args 逐字段一致(INV-06:说了什么 ↔ 做了什么)
   写 tool.result 事件(trace 关联父 llm.response)→ 回 agent-loop
```

**单调语义(原则 3)**:任一 guard 的 reject 都是**终局**——无翻回 allow、无旁路、无"审批即免检";被拒调用没有 tool.result,审计可证"拦了且没执行"。对应 PRD §6.1 信任边界:不可信内容进引擎必须穿过"校验 → guard → scope/预算 → 执行 → 落日志 → 人类审批",**管道内无跳过开关**——即使 LLM 被完全攻破,破坏也被关在 guard/审批/预算的笼子里。

---

## 5 进程内模块依赖图(核心脊柱无环)

规则一句话:**地基最下,脊柱单向收敛到 agent-loop,能力与外壳只经总线/ctx 消费,禁止任何反向依赖。** 顶点:agent-loop 与外壳/能力层(消费端);汇点:bus 地基(被依赖,不依赖任何人)。

```text
┌─ 外壳层(消费端,顶层)────────────────────────────────────────────────┐
│ CLI ─┐ Desktop(pywebview) ─┐ ACP ─┐                                  │
│      └────── bootstrap(启动6步:配置→注册表→总线+日志订阅者→脊柱      │
│               硬接线→内置能力注册→外壳接入)                          │
└──────────────┬──────────────────────────────────────────────────────┘
               │ submit / 订阅事件流(渲染)
┌──────────────▼──────────────────────────────────────────────────────┐
│ agent-loop ◄── 循环驱动:一次对话的总指挥(三态机)                       │
└──┬────┬────┬────┬───────────────────────────────────────────────────┘
   ▼    ▼    ▼    ▼       依赖箭头指向被依赖方(下层)
┌─ agent ───────────────┐  ┌─ llm ───────────────┐  ┌─ system-prompt ─┐
│ 会话实体+ctx 门面       │  │ chat/流式/重试/用量  │  │ 组装提示词(只读) │
│ 聚合 scope/session/    │  │ 降级链/探针(F033)    │  │ ──►session(派生)│
│ tools/llm 的总闸       │  └────────┬────────────┘  └───────┬────────┘
└─┬────┬────┬────┬───────┘           │                        │
  ▼    ▼    ▼    ▼(四条依赖边见下)     ▼(F030)   ▼(只读派生)
┌────────────────────────────────────────────────────────────────────┐
│ session ──► persistence(JSONL 落盘/轮转/损坏检测)                   │
│   └ 日志订阅者挂地基总线,收全部会话事件追加落盘                       │
├────────────────────────────────────────────────────────────────────┤
│ 外围能力层 cap(40+):Provider 注册进 tools 注册表,Definition 自描述;  │
│   经 ctx.* 被消费——cap 不 import 核心模块,核心也不 import cap,       │
│   唯一耦合点 = 地基总线 + ctx 契约                                   │
├────────────────────────────────────────────────────────────────────┤
│ ① 插件总线地基:事件总线/分发器/注册表/热插拔/背压                    │
│   被所有上层依赖,自身不依赖任何上层(无环的汇点)                      │
└────────────────────────────────────────────────────────────────────┘
```

**依赖明细(与 PRD §2.2 一致)**:`agent-loop → {agent, llm, session, scope}`;`agent → {scope, session, tools, llm}`(agent 是消费端总闸);`system-prompt → session`(**只读**派生);`session → persistence`;`tools → bus 注册表 + guards`;`llm → scope`(预算/用量联动);cap → bus + tools 注册表 + ctx;外壳 → bootstrap → agent-loop。

**无环论证**:按被依赖深度排拓扑序 `bus(0) → persistence(1) → session(2) → {scope,llm,tools,system-prompt}(3) → agent(4) → agent-loop(5) → 外壳/能力层(6)`,所有依赖边从大号指向小号,**无回边 → 核心脊柱严格无环**,启动按此序硬接线。INV-08 测试 import 方向(阶段 N 不得 import N+1 模块);INV-07 钉死单进程。

**禁止的方向(违反即架构错误)**:① 脊柱依赖外围能力(外围一律经 ctx 注入);② 能力层 import 核心模块(循环 import + 绕过总线耦合点);③ 外壳直呼模块内部而非经 agent-loop/任务队列(绕过 guard/预算/事件的"特权路径")。唯一向下的进程出口是显式工具 subprocess/PTY(F052/F053),受 guard 管辖。

---

## 6 为什么需要这张图 · 使用指南

**为什么需要**:66 功能 × 6 阶段 × 四层架构散在多份文档里,新人读 PRD-Core(125KB)只见树木、读代码只见叶子、面试要 3 分钟讲全貌——纯文字做不到。MAP 提供:① **空间定位**(第 1/5 章:组件在哪层、依赖谁、写什么事件);② **时间定位**(第 2 章:功能属哪个阶段、看哪份文档);③ **两条主因果链**(第 3/4 章:一次对话怎么穿层、一条 tool_calls 怎么落地——全系统其余都是它们的变体);④ **词汇共识**(第 7 章)。MAP 是**索引视图**,不新增设计,只把 TECH-ANCHOR/PRD 已定的事实画出来。

| 你想做什么 | 看什么 |
|------|------|
| 快速了解系统结构/组件关系/数据流 | **MAP.md(本文)**——入口,3 分钟建全局心智 |
| 查某功能的确切输入输出/边界/验收伪代码 | PRD-Core.md §5(**唯一权威**,冲突以它为准) |
| 查"为什么这样设计"(事件溯源/单调拒绝/自研总线) | ADD.md(ADR)+ TECH-ANCHOR.md(六原则) |
| 实现/修改某模块的伪代码与状态机 | DIS-CORE.md / DIS-SEAM.md(PRD 伪代码级展开) |
| 查事件类型/字段/seq、错误码、配置、LLM 协议、部署 | EVENT-SCHEMA.md / ERR.md / CFG.md / ADI.md / DEP.md |
| 写 Python 代码、逐函数签名 | specs/ 系列(按 6 阶段模块分 20+ 份) |
| 查约束/测试/成本红线 | CONSTRAINTS + FLC 系列(后处理批次) |

**新人阅读顺序**:需求文档 → TECH-ANCHOR → PRD §2 → **本文全图** → PRD §5 按需 → ADD(为什么)/DIS(怎么做)→ EVENT/ERR/CFG/ADI/DEP → specs → 代码。**先图后字,先整体后局部。**

**维护纪律**:① MAP 是派生视图——新增功能/模块/事件先改 PRD 再刷新本文,反向会产生不一致;② 冲突仲裁链:`PRD-Core §4/§5 > TECH-ANCHOR 六原则 > ADD(ADR)> MAP 与其他展开文档`,图与文字冲突以文字规格为准;③ 第 1/5 章图随 PRD §2.1/2.2 同步、第 2 章随 PRD §5 分布同步、第 3/4 章随 PRD §2.4/4.3-4.4 同步——改 PRD 后对本图 grep 校对,防"图过期"。

---

## 7 关键设计词汇表

| 术语 | 一句话定义 |
|------|------|
| **核心脊柱** | 8 个每次对话必经、启动硬接线、不可热插拔的模块(agent-loop/agent/session/llm/system-prompt/scope/tools/persistence);能力再常用也可换,脊柱不可换(RESERVED+BUS-002) |
| **能力 seam** | 三件套:Definition(契约:name/schema/danger/guard 钩子,不可变)+ Provider(实现,可整体替换)+ Consumer(消费点:校验→guard→执行);换 Provider 不碰 Consumer,新能力自动获得校验/guard/审批/计量 |
| **插件总线** | 阶段0 自研 Cordis 等价物:事件总线 + 分发器(精确/通配/谓词)+ 注册表 + 热插拔;单进程内唯一通信通道,只中转不落盘,不做 fiber/Proxy 魔法 |
| **事件溯源** | 会话事件日志(JSONL append-only)= 唯一真源;一切视图(历史/UI/FTS)从它派生;无 update/delete,"修正"=追加修正事件;缓存可弃重建,不存第二份 |
| **guard 单调拒绝** | 调用流经 guard 链,任一 guard 可拒绝;拒绝=终局,无机制翻回 allow、无旁路;审批是决策权移交人类,批准后**重入 guard 链起点** |
| **ctx.\*** | 脊柱向能力层开放的统一挂载门面(ctx.tools/agent/session/storage/sys/ui);cap 无特权路径,内置与第三方插件平权 |
| **三态机** | agent-loop 状态机:idle→running→terminated;同会话仅一个 running,新输入入队;终态由纯文本(自然)或三闸(轮数/预算/超时/取消)触发 |
| **降级链** | 主模型 DeepSeek 失败(401/超时/断网)→ 备用 qwen-max,配指数退避与健康探针择优(F013/F033);降级不降安全,备用模型同过校验/guard/预算 |
| **LLM 零信任** | 对模型输出默认不信任:参数先验后跑(pydantic)、输出按契约校验、每次请求强制三档超时(10s/60s/180s);raw_args 与强类型 args 并列存档供审计(INV-06) |
| **强同步点** | 三类必须"落盘成功才继续"的事件:user.message(落盘才 ACK)、guard.rejected、approval.\*;其余事件异步批量 flush(≤0.5s 或 64 条) |
| **派生历史** | session.derive_history() 每次请求从日志现算上下文(reducer 唯一权威),截窗后交 system-prompt;编辑=追加 user.message_edited,reducer 取新版留旧痕 |
| **spill** | 工具输出超长时的截断留痕(F039),防打爆上下文窗口与预算;截断不吞事实,溢出部分可查 |
| **compaction** | 上下文压缩(F058):旧段摘要化 + compacted 声明,seq 空洞因有声明而合法;只动派生视图,不删真源事件 |
| **单进程并发** | 子 Agent/jobs/schedule/workflow 全为协程级并发,共享同一事件日志与 seq 分配器;唯一进程出口是受 guard 的 subprocess/PTY(INV-07) |

**补两条常被问到的事实**:**危险分级**——Definition 声明 danger,≥high 转人类审批(F015),critical 直接拒绝不可审批,新增 guard 只增加拒绝面;**外壳三件**——CLI/Desktop(pywebview)/ACP 是同一事件流的三种投影,均无特权路径,一切写操作过 session.append。

---

*本文档由 PyHarness 文档流水线产出(批次 2b-B2,与 ADD.md 同波);口径以 PRD-Core.md 为唯一权威,随 PRD 变更同步维护(§6.3)。*
