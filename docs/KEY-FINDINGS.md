# KEY-FINDINGS — 关键发现与经验沉淀

> **项目**:PyHarness——用 Python 3.11 全功能复刻 DeepSeek Harness(DSH)架构思想的单进程 Agent 框架(目录沿用 mini-harness)
> **文档类型**:跨阶段经验沉淀(设计期收尾,供代码阶段开工前预读 + 面试前回顾)
> **版本**:v1.0 | **日期**:2026-09-06 | **状态**:定稿(设计期 60 份文档 + 33 份 specs 已齐,待代码实现)
> **读者**:①作者(开发者/面试者——把 ADR 与 6 条原则讲成"人话"的底稿)②AI 编码 Agent(实现前先读第 3 节坑位清单)
> **权威**:冲突以 PRD-Core.md 为准;错误码以 ERR.md 为准;决策理由以 ADD.md 为准;本文档只做提炼,不新增约束。

---

# 1 关键设计决策与理由(为什么 + 代价)

全部架构可压缩成一句话:**单进程插件架构 = 事件溯源真源(JSONL append-only)+ 能力 seam 三件套 + guard 单调拒绝 + 自研插件总线 + 八模块不可换脊柱,66 项功能沿 6 个"每阶段可运行"里程碑生长**。共同主线:**凡引黑盒换省事的方案全部被否**——压倒性约束是"从零可讲、可逐行审查、可演示",于是 Cordis 不抄全量、pluggy 不引、LangGraph 不碰、React 不做。

| ADR | 决策 | 为什么(关键理由) | 代价(付出什么) |
|---|---|---|---|
| 001 | **事件溯源**:JSONL append-only 为唯一真源,历史全部派生 | 一次获得回放/审计/恢复/一致性四能力,"内存说 A 日志说 B"结构上不可能——第一卖点 | 读侧 O(n) 靠 SQLite 投影补偿;文件增长靠 compaction;单一写入口靠 INV-01 拦捷径 |
| 002 | **能力 seam 三件套**:Definition/Provider/Consumer 分离 | 新能力=Definition+Provider 即自动获得校验/guard/审批/计量——它们在消费协议里,不在实现里 | 每能力三份工件;Provider 禁反向 import Consumer;Definition 不可随意改字段 |
| 003 | **guard 单调拒绝**,无 allow、无信任模式 | 安全只紧不松:配错方向也=拒绝;注入成功也被关笼子;面试"如何保证不误删"有确定答案 | 合法高频操作偶被打断(作用域预授权缓解);单调性由 INV-05 钉死 |
| 004 | **自研轻量插件总线**(数百行),不引 pluggy | 总线是面试主战场,每行可讲可追问;pluggy 的 hookspec/wrapper 死重功能一项都用不上 | 分发顺序/异常隔离(EVT-103)边界要自测;跨包发现留适配器位以后再加 |
| 005 | **单进程插件架构**,拒微服务/多进程/队列 | seq 全进程单调是事件溯源前提;guard/审批同信任域即时生效;`python -m pyharness` 即完整系统 | 单机扩展受限(个人场景够用);死循环以轮数 30/预算闸/超时/栈深 ≤16 兜底 |
| 006 | **JSONL 存事件、SQLite 只做查询投影** | 两域各取所长:真源可 cat/回放/备份=copy;FTS 查询库可删除重建,永不回写真源 | 双写路径+双份磁盘;纪律:查询库永远派生 |
| 007 | **降级链走 openai SDK 单客户端**,降级=换 (base_url, api_key, model) 三元组 | 一份客户端覆盖 DeepSeek/qwen-max/任意兼容模型——加第三家只加配置行,流式/计量/重试只写一次 | 多 1 个依赖(计入 ≤15);依赖厂商兼容承诺;特性走透传槽 |
| 008 | **六阶段每阶段交付可运行里程碑** | 复杂度集中在"循环 × 真实 LLM × 工具",一次写完无法定位故障层;每阶段有可录屏实物 | 阶段性重构不可避免,靠信封+错误码+契约吸收;未过门不写下一阶段 |
| 009 | **LLM 参数零信任:先验后跑**(pydantic 先于执行) | 非法参数物理上进不了执行,副作用永不先于校验;失败 TLB-803 回喂自纠(可演示) | Definition.schema 须权威,契约漂移=校验失真;怪异但合法的参数靠回喂迭代,不放宽校验 |
| 010 | **脊柱 8 模块不可换,外围可插拔**(三条判据) | 判据:①循环正确性依赖②跨能力共享契约③语义不可降级替换;防"外围升格"(膨胀)与"脊柱降格"(裸奔) | 边界靠纪律评审;脊柱改动过严回归;灰色带(compaction/fork)显式决策 |
| 011 | **错误码为对外契约**(域前缀-NNN+分类+处置) | 降级/回喂/展示/测试全按码决策不匹配文本;码=跨阶段契约,阶段 1 定的码阶段 6 直接复用 | 每类失败起码表(ERR.md 41 码);新失败不裸抛不绕码;长尾用 CYC-999 吸收 |
| 012 | **桌面程序**:pywebview 壳+FastAPI 同进程,不建 SPA | 双击 exe 演示冲击力 > 浏览器 URL;窗口=只读事件投影,轨迹时间线是事件溯源最直观证明;禁 React/44 包 | 交互上限低于 Qt(不在 66 项内);SSE 断线从最后 seq 续拉;依赖 WebView2(Win11 自带) |

**共性观察**:ADD.md 收录的 ADR 同一套论证模板——先写"违反它的后果(具体场景)"再决定;被否方案不是"不够好",而是"在某个可枚举场景下会炸"——这个叙事比任何单一决策更值钱。

# 2 与 DSH 原版差异对照(复刻思想,不翻译源码)

| 对比项 | DSH 原版 | PyHarness | 决策理由 |
|---|---|---|---|
| 语言/规模 | TS,~250 npm 包,核心 ~19,300 行 | Python 3.11,8,000-12,000 行,依赖 ≤15 | Python 是主栈;搬 TS 依赖无学习价值 |
| 插件系统 | Cordis 全量(fiber/Proxy/5 种分发) | 自研轻量总线+注册表(500-1,000 行) | 学思想不做魔法(ADR-004) |
| 会话内核 | 事件溯源+surface+compaction | 事件溯源 JSONL 唯一真源+简化 compaction | 事件溯源从零写透(ADR-001/006) |
| 并发 | Node 单线程 | asyncio 单进程单事件循环 | Python 自然等价,不跨进程(ADR-005) |
| 前端 | React(44 包) | **pywebview 桌面壳+FastAPI 同进程+原生 JS/Vue** | 44 包对 Python 无意义,明令不做(ADR-012) |
| 基建 | Docker/PG/Redis | SQLite+JSONL+文件 | 单进程不需要容器与重型存储 |
| 文档 | 英文 | 中文注释+中文文档 | 面试要能逐行讲清 |
| 定位 | 全栈 Agent 框架 | 技术引擎型:内核+CLI+桌面壳,开发者嵌入 | 竞品对标 LangGraph(编排)与 DSH(全栈) |

**自研意义**:LangGraph 解决"图编排",但不提供"事件日志唯一真源、guard 单调拒绝、可插拔插件总线"整套**可审计运行时**;DSH 有思想但绑死 TS/Docker/React。PyHarness = 最小技术栈完整复现 DSH 架构思想,使五条主线成为可逐行审查、可演示、可讲深的一等公民。**凡不属于五条主线之一的功能不进 v1.0**。

# 3 实现时最可能踩的坑(坑/症状/规避)

坑位来自 IMPACT-MATRIX 实测修复(已发生)+ CONSTRAINTS-01~08 推演 + PRD §9 风险表。**写代码前逐条对照;踩中任一条 = 该模块不合格**。

| # | 坑(根因) | 症状(现场什么样) | 规避(正确做法) |
|---|---|---|---|
| PIT-01 | **错误码手写错位**(errors.py 初稿 12 处与 ERR.md 冲突:LLM-301~304 错位、TLB-801/802/803 互换、缺 TLB-806 多 TLB-899;IMPACT P0-3 实测) | 降级/回喂按错码决策:把认证错当限流无限重试,或对参数错放弃降级 | **单向锚定**:代码从 ERR.md §2 程序化抽取,禁手写后"反查";修复后 41 码双向对齐零误差,后续每模块照此 |
| PIT-02 | **未登记码被引用**(EVENT-SCHEMA 示例用 TLB-404——ERR §9.2 裁定未登记且与已用码冲突;IMPACT P0-1 实测) | 编码 Agent 照示例实现 → 用未登记码 → raise_code 兜底 CYC-999 → 分类失效 | 新码五步(注册→登记→测试→同步 EVENT-SCHEMA→§9.2 核对);**表外码出现即错误**;示例区 grep 清零 |
| PIT-03 | **第二份历史**(内存维护 messages,JSONL 只做异步备份;违反原则 1/INV-01) | 崩溃丢最近窗口 → 重启后 AI 记忆与所见不一致,无对照物无人察觉;审计"谁触发了删除"无解 | 只有 `session.append` 一个写入口;历史只由 `derive_history()`/`rebuild_from_log()` 派生;history_cache 可丢,append 即失效 |
| PIT-04 | **校验晚于副作用/宽容解析**(执行后校验、try 兜底、静默补默认;违反原则 4/INV-06) | `{"path": 123}` 被 `str()` 隐式转换后去删"名为 123 的文件";审计答不出删了什么 | raw_args→执行前 pydantic 强校验→guard→才执行;失败=TLB-803 回喂零执行;INV-06 三十例畸形参数断言函数零调用 |
| PIT-05 | **guard 有 allow/审批即放行**("批准过就跳过 guard";违反原则 3/INV-05) | 注入诱导 delete_file→误点批准→直接执行,guard 无第二次机会;安全强度=最松一环 | 单调拒绝,reject=终局;granted 后**重入 guard 链起点**(策略可能已变);critical 不可审批;INV-05 断言拒绝后零副作用 |
| PIT-06 | **子 Agent/job 死循环拖垮全体**(单进程代价被低估) | 一个后台 job 无限调 LLM 占死事件循环,前台对话无响应;子 Agent 递归失控 | 轮数 30+预算硬闸(默认 ≤1 元)+每步超时+栈深 ≤16+子 Agent 深度 ≤3/并发 ≤8;job 超预算直接杀 |
| PIT-07 | **静默吞错**(裸 `except: pass`;违反 CONSTRAINTS-05 E-04) | 持久化失败被吞 → 以为存了,重启全丢;工具失败被吞 → AI 基于错误结果继续推理 | except 至少 log 一行(码+上下文);不确定就 re-raise 或转致命;能降级就显式降级留痕——吞错=把确定故障变随机幽灵 |
| PIT-08 | **按异常文本判断失败**(无错误码;违反 ADR-011) | 厂商文案一改判断就断;测试只能断言"抛了异常",无法断言"安全拒绝还是网络错" | 全失败以"域前缀-NNN"表达;消费方全按码决策;未知失败归 CYC-999,禁现场造码 |
| PIT-09 | **Windows 路径反斜杠被吞**(YAML/命令行写裸 `D:\x`;DEP §2.5-5/CFG-602 实测) | 启动报配置错;路径乱码;g-fs-path 判定失真 | YAML/命令行一律正斜杠 `D:/杂乱文件夹`;事件文本内路径 JSON 转义;代码统一 pathlib+resolve_in_workspace 单点(F055) |
| PIT-10 | **mock 太假/模拟数据混入产物**(测试只断言"没抛异常"或 mock 永远成功;PRD 禁模拟数据附录) | 测试全绿真实环境翻车;文档示例被 AI 当真实输出参考,污染验收 | mock 模拟真实失败(5xx/超时/坏 JSON);断言具体行为(guard 拒绝后调用次数=0、降级后走备用 base_url);合成数据仅测试用途并标注 |
| PIT-11 | **阶段 import 反向依赖+外围互引**(阶段 N import N+1 模块;外围绕过 ctx 直接 import 另一外围;违反 INV-08/ADR-010) | 卸载一插件拖垮另一个;脊柱被外围污染;返工从"改循环"扩大为"改所有依赖方" | INV-08 断言 import 方向;外围依赖只能经 ctx 注入;卸载测试(摘订阅+注册后事件停投)作热插拔验收 |
| PIT-12 | **大结果直接进上下文**(工具输出超限不截断;违反 N3/F039) | "整理 3MB 大文件夹"一次读入打爆上下文与预算;分不清工具没截断还是循环没预算闸 | 输出 >64KB 转 spill(原文落私有区,上下文只放 ≤2KB 摘要+引用);spill 读取计入已读量 |
| PIT-13 | **同步插件阻塞事件循环**(同步 handler 直接跑在循环里;风险 R9) | 一个同步工具卡 10s,对话/审批/心跳全卡 | 同步 handler 一律 `asyncio.to_thread` 线程池(默认 4);循环延迟 >1s 告警 |
| PIT-14 | **headless 下危险操作静默执行**(无审批通道仍放行;违反 R8/F015) | 脚本驱动任务,delete_file 无人审批直接执行——"自动化事故" | 无审批通道=直接拒绝(headless 安全默认);ACP 桥可下发 approve 但仍过 guard/预算,桥无特权 |
| PIT-15 | **同一个"横切值"被多层各自派生**(会话 workspace 根曾有 **4 处**各自实现:`engine` 赋裸 `workspaces_dir`、`tool_exec` 再拼 `/<sid>`、`subagent` 取 `.parent`、`build_scope` 走 helper;2026-09-21 实测) | 静默分裂:①所有会话共用同一 fs 根(**跨会话隔离失效**)②子代理根越出 `workspaces` 树且**无人创建** ⇒ 子代理文件类工具恒 `TLB-802`;而 6 份权威文档(PRD/CFG/OPS/DEP/2 份 spec)早已写明"每会话专属根" | 横切值(路径/域分类/通道/证据触发点)**只允许一个派生点**,装配层一律引用(`scope.session_workspace`);配 **AST/契约不变量**钉死唯一性;每次改动问三句:**"配置声明了≠生效""事件存在≠发出""已构造注入≠生产可达"** |
| PIT-16 | **`getattr(obj, "错名", 默认值)` 静默取默认**(`SessionLog` 只有 `.sid`,代码却取 `"session_id"`;**首轮 5 处、第四轮又扫出 3 处订阅属主串**;2026-09-21 实测) | 不抛错、不崩、不告警:`scope.session_id` / `BudgetGate.session_id` / CLI workflow 会话号**恒为空串**,只在外部产物里露馅;订阅属主恒为 `engine:?` ⇒ 多会话共享总线时"按属主摘除"会**连坐其他仍在运行的会话**;两处还被 `or` 短路掩盖成"永远走不到" | 关键标识符**直取属性**(缺了就该 AttributeError),或取后**断言非空**;横切标识符(会话/租户/请求)抽**单一取值函数**(如 `engine._sid_of`)+ 传播不变量;排查手法:枚举全库 `getattr(x,"字面量",…)`,与"全库已定义名"取差集(982 处 → 56 候选 → 逐项核验)|
| PIT-17 | **测试脚手架写死实现细节,把错误行为锁成"基绿"**(e2e 硬编码 `tmp/"workspaces"` 当 fs 根;subagent 单测只断言 `sub_id in workspace_root`;2026-09-21 实测) | 越界+未创建的真缺陷**长期全绿**:断言对"错误形态"同样为真(`sub_id in "…/workspaces/../sub_id"`);子会话只数 `decision/receipt/segment` 事件、不看 `tool.error` ⇒ "事件存在 ≠ 能力可用" | 测试从**实际装配值**取路径(`scope.policy.workspace_root`)而非猜目录;断言**落点/包含关系/终局事件类型**(`tool.result` 而非只有 `tool.error`)而非子串;新增能力时补**"消费者真跑一次"**的端到端断言 |
| PIT-18 | **共享注册表用"扁平键"承载"分层唯一"的标识**(`_SESSION_TENANTS` 以裸 sid 为键,而会话目录**按租户分目录** ⇒ sid 只在租户内唯一;2026-09-21 R6/R7 两轮才收敛) | 撞名时给出**错误的确定答案**:请求租户派生/SSE 过滤/原生壳事件落点全部按错租户判定 ⇒ **跨租户事件误投或漏投**;且修法若选"先写者胜",进程内残留认领会**挡住**新租户的合法登记(把"夺走"换成"占位") | 归属模型必须匹配真实唯一性:此处用 **sid → 认领集合**,**单认领才给确定答案、多认领 ⇒ 未知**;消费方对"未知"一律 **fail-closed**(`event_tenant_allowed`);并**优先取已落盘事实**(信封 `tenant_id`,GAP-10)而非易失映射。**通用判据:任何"按 X 查唯一 Y"的注册表,先问 X 在 Y 的哪一层唯一** |
| PIT-19 | **"约定被读 ≠ 被注入"——注入面只有读取方、没有装配方**(`ctx.redact` 被 tool_fs/tool_web/spill 三处读取,全库**无赋值点**;2026-09-21 R10 实测) | 所有读取方**静默走 fallback**;若 fallback 恰好是"原样返回",就是**安全洞**:实测 `fs.read_file` 读含 `API_KEY=sk-AAAA…` 的文件时,`tool.result` 与整条事件流(→JSONL/FTS/LLM 上下文)均含**密钥原文**,而代码注释写着"出口脱敏后再落盘(INV-09)"、SECURITY §6.4 宣称"全出口覆盖" | 对**每个** `getattr(ctx, "X", …)` 式注入面,必须在装配层找到**赋值点**(可用静态扫描:收集 `getattr(…,"字面量",…)` 与全库赋值名做差集);注入面与消费方**同一次改动内补齐**,并配**端到端**用例(真实链路 + 断言外部产物无原文)。与 PIT-16/零调用者互补:**前者查函数有没有人调,后者查属性有没有人写** |

**跨坑主线**:PIT-01/02 是"登记册 vs 代码"锚定;PIT-03/04/05 是六原则被捷径绕过;PIT-06/12/13 是单进程代价兜底;PIT-10 是测试与文档诚实性。

# 4 面试讲法建议(3-5 个亮点怎么讲)

## 4.1 支撑数据(先记住这一表)

| 指标 | 数值 | 一句话解读 |
|---|---|---|
| 功能规格 | 66 项(F001-F066,每项含验收伪代码) | 每项 = 一个可执行 pytest 验收模块 |
| 编码规格 | 32 份模块规格 / 313 个函数定义(specs/) | 代码阶段"照单写",函数级伪代码先行 |
| 文档体量 | 60 份中文文档,规格约 1.2MB | 需求→架构→协议→安全→约束→specs 全链条 |
| 开发阶段 | 6 阶段,每阶段可运行可演示 | 阶段门没过不进下一阶段(INV-08 机器检查) |
| 代码预算 | 引擎 8,000-12,000 行,依赖 ≤15,零 Node/Docker | 一人可维护、可逐行审查 |

## 4.2 总纲(30 秒,背熟)

> "我完整分析了 DeepSeek Harness 的架构,然后用 Python 全功能复刻了它——不是翻译 TS 源码,而是复刻架构决策:事件溯源会话日志、guard 单调拒绝的工具管道、能力 seam 三件套、自研插件总线。66 项功能分 6 阶段推进,每阶段可运行可录屏,32 份编码规格 313 个函数已写好,现在进入代码实现。"

## 4.3 五个亮点(每个 1-2 分钟,按面试方向选 3-5 个)

| 亮点 | 怎么讲(叙事线) | 演示/数据支撑 |
|---|---|---|
| ①事件溯源内核 | 从"状态"到"事实累积",一次获得回放/审计/恢复/一致性;**没有第二份状态**——上下文/UI/token 统计全是日志投影,任何 bug 拿日志重演即现场 | 桌面轨迹时间线把 JSONL 渲染成 Agent 调工具、被 guard 拦的逐帧回放;`cat 会话.jsonl` 每行都是事实 |
| ②安全:guard 单调拒绝 | "怎么保证 AI 不误删?"——默认拒绝,唯一放行=逐次审批留痕,批准后仍重入 guard 链;critical 不可审批;拒绝必有 guard.rejected 强同步事件 | 威胁演示:AI 连试三次(绝对路径删→move 到回收站→删工作区内副本)全被不同 guard 拦下,日志三行不可变,文件系统零副作用 |
| ③能力 seam+自研总线 | "一切皆插件"落地:三件套让新能力自动获得校验/guard/审批/计量——它们在消费协议里;总线数百行自研,emit→热卸载→异常隔离每行可讲,**不是"pluggy 干的"** | 现场热卸载:卸载后事件即刻停投,消费者零改动;脊柱 8 模块 uninstall 返回 BUS-002(结构性拒绝) |
| ④工程纪律:规格→验收闭环 | 66 项规格带验收伪代码+9 条 INV 把六原则机器化+错误码单向锚定;讲"抓出并修复错误码 12 处手写错位"这类真事故,证明体系在跑 | 1.2MB 规格/60 文档/313 函数/41 码双向对齐;`pytest tests/invariants/` 最先全绿才是开工信号 |
| ⑤取舍叙事(差异化) | 为什么不用 LangGraph/Cordis/pluggy/React——每条都有可枚举的"违反后果":引 LangGraph=学习价值归零;引 pluggy=黑盒+违反禁止表;上 React=双部署单元+演示依赖 node_modules | 每份 ADR 的被否方案表;讲清"pywebview 而非 PySide6/React"尤其加分(弃 Qt:2-4 周学习曲线换不来求职增量) |

## 4.4 高频追问预备

| 追问 | 一句话答案要点 |
|---|---|
| 单进程能撑住吗? | 个人场景 IO 密集,asyncio 协程足够;同步插件走线程池;真并行撞"seq 单调"约束,v2 先解再动 |
| 为什么不直接用 LangGraph? | 它解决图编排,不提供"唯一真源+guard 单调+可插拔总线"这套可审计运行时——我要复刻的是后者 |
| 测试怎么证明可靠? | 9 条 INV 断言架构不被破坏(比功能 bug 更早暴露)+66 验收模块+INV-06 三十例畸形参数断言零执行 |
| 文档 1.2MB 是否过度设计? | 每份文档对应可验证产物(specs→代码→pytest);目的是让 AI 编码 Agent 和我"照单实现不跑偏" |

# 5 后续路线

## 5.1 代码阶段(下一步:32 模块按 specs/ 顺序推进)

1. **入口**:specs/README.md 索引 → 每模块 specs/xxx.py.md(签名/伪代码/异常表/测试),32 模块 313 函数覆盖阶段 0-6。
2. **顺序**:严格按 6 阶段门垂直切片——阶段 0 总线 4 模块(bus/config/errors/events)→ 阶段 1 脊柱 10 模块 → ……每阶段尾跑通验收 + `scripts/demo_phase{n}.py` 里程碑;INV-08 查 import 方向,未过门不写下一阶段。
3. **纪律**:错误码从 ERR.md 程序化抽取(PIT-01 教训);每功能落 `tests/acceptance/test_f{nnn}_{slug}.py`;66 项+9 条 INV+NFR 全达标=v1.0(PRD §8 清单)。
4. **骨架顺序建议**(按依赖):events→errors→bus→config → session→persistence→tools_registry→tools_executor→tools_guard→agent_loop→llm→scope→system_prompt→approval → 外围能力按阶段 2-6 由脊柱向外长。

## 5.2 v2 方向(全部在 v1.0 范围外,先声明卡点再动手)

| 方向 | 卡点(为什么现在不做) | 启动条件 |
|---|---|---|
| 跨会话并行/多进程 worker | seq 须全进程单调(原则 1),worker 内存态进不了日志投影(INV-07) | 先设计共享 seq+日志汇聚协议,新增 ADR |
| 远程审批通道 | 审批=单调链外人类决策,需先定身份/防重放/加密 | 有真实多端需求时;三通道已留 by 字段 |
| 插件生态+entry-points 发现 | 单进程显式注册够用,动态发现引入隐式状态 | Registry 已预留适配器位,届时加不重构 |
| 容器级沙箱替代 | Win 用户态无强隔离,策略=不运行不可信编译产物(诚实声明局限) | Windows Sandbox/WSL2 集成在"零 Docker"内可行时 |
| 云端/多人部署 | 单进程+本机存储是 v1.0 定位,牵动真源与 guard 设计 | 产品化时整体走"服务化"新 ADR,不在单进程上打补丁 |

**路线总原则**:v1.0 把五条主线做透;v2 每个方向先回答"会不会动摇五条主线之一",会动摇的先写 ADR 再动手——与设计期同一纪律。

# 6 关联文档

| 文档 | 关系 |
|---|---|
| ADD.md | 第 1 节权威来源(ADR 索引与正文,含违反后果与备选对比) |
| PRD-Core.md | 66 项功能唯一权威规格(§1 定位/§2 架构/§4 原则/§5 功能/§8 验收) |
| IMPACT-MATRIX.md | 第 3 节 PIT-01/02 实测来源(矛盾扫描 4 例修复记录) |
| ERR.md | 错误码权威目录(41 码;§9.2 TLB-404 注记)——代码单向锚定对象 |
| CONSTRAINTS-01~08 | 硬约束全集(PIT-03~14 推演来源,违反=不合格) |
| specs/README.md | 代码阶段入口:32 模块 313 函数索引与实现顺序 |
| TECH-ANCHOR.md | 禁止技术表与六原则总纲(五条主线判据源头) |
| DEP.md / OPS.md | 阶段运行指南与排障(PIT-09 Windows 路径案例) |
| EVENT-SCHEMA.md | 事件信封/词表字段级权威(PIT-02 修复目标) |

---

*— KEY-FINDINGS v1.0 完 — 12 决策 × 14 坑位 × 5 亮点;开工前重读第 3 节,面试前重读第 4 节。*

---
---

# 附录 A — S6-2 阶段登记(2026-09-15)

> **本附录为新增记录**(S6-2a-P0 人工裁定后登记),**不修改 §1~§6 任何既有条目**。
> 编号用 `KF-A` / `KF-B`,与 §1 的 ADR 系列、§3 的 PIT 系列**不重叠**。
> 两条 Finding **相互独立**:KF-A 是**不变量违约**(编号问题),KF-B 是**控制面缺口**(工程风险)。**KF-B 不新建 INV 编号。**
> 裁定依据:`S6-2_COVERAGE_MATRIX.md` §10 / §11 / §12 · `docs/INVARIANT_REGISTRY.md`(INV-02)。

## A.1 KF-A(P0)— `auto_title` 越过 Agent Loop 直接调用 `llm.chat`,违反 INV-02

| 字段 | 内容 |
|---|---|
| **现象** | `pyharness/core/auto_title.py:36` 调用 `ctx.llm.chat([...], tools=None, ctx=ctx)`;经 `pyharness/engine.py:805-806` 在生产路径接线(`res.reason=="complete"` 且 `spine._auto_titled` 为假时,于 `loop.wake()` **返回之后**触发);**零测试**(`grep -rln auto_title tests/` 为空);**无 spec**(`docs/specs/auto_title.py.md` 不存在)。 |
| **根因** | **错误的 LLM 出口选择**。F042 属"系统工具"类直调,PRD 为其指定的出口是 **`ctx.llm.mini(...)`**(`docs/PRD-Core.md:1251`),但 **`mini()` 在实现中不存在**(`grep -rn "def mini" pyharness/` 零命中)⇒ 实现者落到唯一可用的对话出口 `chat`。 |
| **规格证据** | `PRD-Core.md:838`(F018)"INV-02 无绕过 agent-loop 直调 llm" · `CONSTRAINTS-06-Testing.md:63` 断言"**全库 `llm.chat` 唯一合法调用方 = agent-loop**" · `DIS-CORE.md:186`"本模块是 llm.chat 唯一合法调用方;绕过即架构违规" · `docs/specs/agent_loop.py.md:262`"(测试钉死)" · `PRD-Core.md:634`(意图=三闸只在循环内) · `PRD-Core.md:1241-1254`(F042 规格) |
| **实现证据** | `auto_title.py:36`(调用点) · `engine.py:800`→`:802-806`(调用时机在循环外) · `agent_loop.py:196-199`(三闸全在循环内:`_must_stop` / `scope.check_budget`) · `llm.py:647-684`(`LLMAdapter.chat` 只计量不阻断) · `llm.py:957`(`_chat_any` 为四出口唯一汇聚点) |
| **影响** | ⚠️ **不变量**:`INV-02` 字面与意图均被违反。⚠️ **控制**:该调用**不经过**轮数/取消/预算前置闸(预算超支仅**下一轮**可见)。✅ **未受损**:审计留痕(`llm.request`/`llm.usage`/`llm.response` 齐全)、单端点路径、超时闸(F017)、降级链、无工具副作用(`tools=None`)。**非安全漏洞**。 |
| **附带规格偏离(F042 共 5 项)** | ① 出口 `mini`→`chat`;② 输入截断 `first[:200]`→`first[:500]`(`auto_title.py:22`);③ 标题上限 `≤24`→`TITLE_MAX=64`(`:16,:42`,而模块 docstring `:3` 仍写 ≤24);④ 失败/空降级"**前 20 字符**"→`return None`(`:40,:44`);⑤ PRD 要求的 `test_f042_title.py`(`PRD:1878`)不存在。(另 `ctx.session.has_title` 不存在,实现改用日志派生判断 —— **等价且更合 INV-01**,不计偏离。) |
| **裁定** | **Option A:违反成立**;违规点 = **出口选择**(+ 无闸,已划归 KF-B)。**不修改 INV-02 定义、不扩大例外范围**。 |
| **处置** | **已修复**(commit **`50c19ca`** `fix: restore F042 mini LLM endpoint`):新增 `LLMClient.mini()`(`pyharness/core/llm.py:974-985`)· `auto_title` 改走 `mini`(`pyharness/core/auto_title.py:36`)· `docs/specs/llm.py.md` 补最小接口说明。**修复范围仅"出口选择"**。判定见 `S6-2a-P0-F_FINAL_REVIEW.md`。 |
| **状态** | `CLOSED`(2026-09-15, commit `50c19ca`) —— **仅限本条的"出口选择"违约**;F042 其余 6 项(见上行)与 **KF-B** 仍**各自独立登记**,不因本条关闭而消解 |
| **关联** | `docs/INVARIANT_REGISTRY.md` INV-02 · `S6-2_COVERAGE_MATRIX.md` §7 / §10 / §11 |

## A.2 KF-B(P1)— 系统工具类 LLM 出口不经运行时三闸(**独立于 INV-02**)

| 字段 | 内容 |
|---|---|
| **现象** | ① `LLMClient` 的**全部**出口(`chat` / `chat_stream` / `summarize` / `json_chat`)**均不执行** budget / cancellation / guard / governance 判定 —— `llm.py` 全文**无相关执行代码**(`grep -n "scope\|budget\|guard\|governance\|cancelled" pyharness/core/llm.py` 命中全为 docstring/签名);三闸**只**在 `agent_loop.py:197-199`。② 非循环消费者同样不过闸:`plan_mode` → `json_chat`(`plan_mode.py:341-352`)、`compaction` → `summarize`(`compaction.py:510-516`)。③ **`summarize(prompt, *, budget: int = 400, ctx)` 的 `budget` 参数只出现在签名,函数体内从未被使用**(`llm.py:974-982`),而 `compaction.py:266,299` 的 spec 声称该上界存在。 |
| **根因** | **三闸的实现位置绑定在"循环"而非"LLM 出口"**。`agent_loop.run()` 在每轮前置判定(轮数 `:197` / 预算 `:199` / 取消 `:197,204`),而 `LLMClient` 任一出口都只是"请求-归一-计量-落盘",**不含任何准入判定**。⇒ 任何**非循环**的 LLM 调用路径天然不受闸。 |
| **为何独立于 INV-02** | `json_chat` / `summarize` **不使用 `llm.chat`**(经 `_chat_any("chat", …)` 直接下发),故**不违反 `INV-02`**;反之 `auto_title` 修复后(KF-A)也**仍不过闸**。⇒ "**不违反 INV-02**" ≠ "**无工程风险**"。**不为其新建 INV 编号。** |
| **缺失的控制** | 预算前置(F032)· 取消(F025)· 轮数归属。**未缺失**:审计留痕 · 单端点路径 · 超时(F017) · 降级链 · guard(无工具调用,不适用)。 |
| **风险** | **P1**(资源/控制面)。系统工具类调用可**越过预算上界**(超支仅下一轮可见)、**不受取消令牌约束**;且 `summarize.budget` 的"有上界"印象与实现不符(**比没有参数更易误导调用方**)。无审计缺失、无安全绕过面。 |
| **处置** | **不修**;**不归入 INV-02**;建议独立评估(可与 durability/reliability 阶段合并)。未来测试**只设计不实施**(见 `S6-2_COVERAGE_MATRIX.md` §12)。 |
| **状态** | `OPEN` |
| **关联** | `S6-2_COVERAGE_MATRIX.md` §12 · `pyharness/core/llm.py:957,974` · `pyharness/core/agent_loop.py:197-199` |

**登记口径声明**:本附录仅登记**事实、证据、影响与裁定**,**不包含任何修复动作**;两条 Finding 的修复均**需另行人工授权**,且**不得在测试阶段(S6-2)内实施生产代码改动**。

---
---

# 附录 B — Scheduler 用户能力阶段沉淀(2026-09-19 · Phase 0–3 Closure)

> **本附录为新增记录**,**不修改 §1~§6 与附录 A 任何既有条目**。编号 `KF-C` / `KF-D`,与 §1 的 ADR 系列、§3 的 PIT 系列、附录 A 的 KF-A/KF-B **均不重叠**。
> 登记范围:Phase 0–3(commits `ff6df8a` / `e0c3eaa`)交付的 Scheduler 用户能力。
> **本附录只登记事实、证据、边界与决策,不含任何修复动作**;两条边界均**需另行人工授权**方可改动。
> 裁定依据:Phase 0–3 Closure Report(`Final Finding Count = 0 confirmed defects`)。

## B.1 能力与端到端链路(只读核对所得)

**现在能做什么**:会话内以**自然语言**创建定时任务(Agent 经 `schedule` 工具调 `ctx.schedule`);三种触发方式 `cron`(5 字段)/`interval`(秒,≥60)/`at`(ISO8601 一次性);管理面 `list`/`pause`/`resume`/`remove`;到点把 intent 作为新任务入队 → 由 Agent 真实执行 → 在**同一会话**产生 `agent.message`;会话窗口可区分「定时触发」与「用户自己说的」。

| 层 | 代码位置 | 职责 |
|---|---|---|
| Tool | `core/tool_schedule.py:91,114` | 参数完整性检查 + 委托;**零调度逻辑** |
| Registration | `core/schedule.py:430` `register` | 校验表达式/名字/重名 → 落 `schedule.registered`(含 `template.intent`/`is_risky`/`next_fire_at` 快照) |
| Event Persistence | `core/session.py` `append`(唯一写入口) | `schedule.*` 为**普通落盘**(非强同步;符合 spec 声明的 §8.1 口径) |
| Recovery | `core/schedule.py:643` `recover` | 回放重建 → 核算 `missed` → 重臂 → 启泵 |
| Ticker | `core/schedule.py:772,781` | 分钟边界对齐的单协程泵(INV-07 单写者) |
| Trigger | `core/schedule.py:540` `_fire` | 写 `schedule.trigger` → 写 `user.message`(`actor=user`,`origin=schedule:<name>`)→ `task_queue.submit` |
| Task Queue | `core/task_queue.py` | 单飞 FIFO;`task.enqueued{task_id,pos}` —— **不含 intent 文本** |
| Engine | `engine.py:778` `run_for_task`(+ `:741` `_owned_task_message`) | 按归属窗口取回那条 `user.message` → `loop.wake(env)` |
| Agent Loop | `core/agent_loop.py:232` | `derive_messages` → `sysprompt.assemble` → `llm.chat` |
| LLM → agent.message | `core/llm.py` / `core/agent_loop.py` | 真实调用;纯文本终态落 `agent.message` |

**治理边界(只写已核对的事实,不推测)**
- 执行器与治理层**无任何 `schedule` 专用分支**(`grep schedule` 于 `core/tools_executor.py` 与 `governance/*.py` 零命中)。
- 授权唯一入口 = `ctx.governance.authorize`(`core/tools_executor.py:479`;审批后重入 `:625`);guard **不感知**来源(`core/tools_guard.py` 无 `origin`/`meta` 读取)。
- ⇒ 定时任务到点后产生的 tool call 走**与前台完全相同**的四关管道:实测定时路径同时产出 `guard.evaluated` / `decision.issued` / `receipt.emitted`,危险工具被 `guard.rejected`。
- Scheduler 自身**不读 intent 内容**;唯一与内容相关的判断是 `is_risky`(深夜禁触窗),来自模板 `meta.tools` 推导或显式覆盖。
- 已知且**已公开声明**的治理缺口不因本能力而改变:`evidence.archived` 无生产者(§附录 A 之外,见 `LIMITATIONS.md` L-11)、`AuditSystem` 无调用路径(L-10)。

## B.2 决策登记

**Accepted(已接受)**
| # | 决策 | 依据 |
|---|---|---|
| D-1 | Phase 0–3 当前行为接受,无待修缺陷 | Closure:`Final Finding Count = 0 confirmed defects` |
| D-2 | Scheduler 现为**进程内执行模型**(定义持久 / 执行不持久) | ADR-005 单进程;`engine.py:615` 每会话一个实例 |
| D-3 | **不做** missed trigger 补偿(只记 `schedule.missed`,不补跑) | F048 规格明文边界 |
| D-4 | `is_risky` **单调**:工具只能升级为危险,不得下调 Scheduler 推导 | Phase 1 人工批准 |
| D-5 | 不改 `derive_messages` 输出契约;UI 来源标记在 service 层复制附加 | Phase 3 实现取舍(`service.py:31` `_with_message_origin`) |
| D-6 | 本阶段**不修改** KF-C / KF-D | Closure 裁定 |

**Open(待产品决策,本附录不代为决定)**
| # | 待决 | 裁定(**2026-09-21 R24 由用户授权代行为产品决策**) |
|---|---|---|
| O-1 | 到点提醒的**最终语义**:Agent 应"真的发出提醒",还是"确认任务状态"? | **已裁定:真的发出提醒**。实现取本文 §B.3 所列的**最小面**——`engine.run_for_task` 把**框架侧**来源(`task.meta.source="schedule:<name>"`,受控非模型文本)提到 `ag_ctx.turn_source`,由系统提示的**受控段** `[本轮来源] …(系统/定时触发的一轮。请直接产出该触发要求的内容本身…)` 渲染。不动事件、不动减少器、不动 compaction。 |
| O-2 | 应用关闭后**是否仍需执行**? | **已裁定:不执行(维持进程内模型)**。执行载体=桌面/CLI 进程本身,与 INV-07 单写者锁同一生命周期;引入常驻守护进程会新增第二个写者与一套锁/配额语义,不在本项目姿态内。故 KF-D **不改**;该边界在 LIMITATIONS **L-16** 如实登记(不是缺陷,是范围裁定)。 |

## B.3 KF-C(P2)— Scheduled Intent semantic/context gap

| 字段 | 内容 |
|---|---|
| **现象** | 到点触发后,真实 Agent 回复的是**任务状态复述**("你的定时提醒已就绪……每天早上 8:00 会提醒你……"),而非真的发出提醒内容(如"该开始工作了")。实测:`1分钟后提醒我站起来活动一下` → 到点回复 `That reminder is already set — you're all covered.` |
| **根因** | intent 为**单串双语义**(任务语义 + 被重放的用户话术);且到点那一轮**无任何通道**把"这是定时触发"告知模型 |
| **证据(数据流)** | 创建 `core/tool_schedule.py:114` → `core/schedule.py:430`;持久化 `schedule.registered.template.intent`;触发 `core/schedule.py:540` 内 `intent` **同一变量**既作 `user.message.content` 又作 `q.submit(intent, meta={"source": "schedule:<name>"})`;入队载荷仅 `{task_id,pos}`(`events/payload.py` `TaskEnqueuedPayload`);执行 `engine.py:778` 只读 `enqueued_seq`/`intent` |
| **证据(三条通道全断)** | ① `task.meta` **全库无消费者**(`grep '\.meta\['` / `meta.get("source")` 零命中);② `core/session.py:192` `_fold_history` 对 `user.message` 只取 `content`,**`origin` 不进历史**;③ `core/system_prompt.py:405` `_vars` 白名单仅 role/deny/domains/workspace/sandbox_level/window_tokens/title/capabilities,且明文"**禁止任何 LLM 输入/工具结果入表**(注入防御)" |
| **为何不是 Runtime Defect** | 链路按既有契约完整运作:无异常、无数据丢失、无治理绕过;F048"把模板作为新任务入队并复用 guard/预算/日志纪律"全部满足 |
| **为何不影响 Phase 0–3 验收** | 验收口径为"到点后产生当前会话的 `agent.message`"——**已满足**(实测 `schedule.trigger=1`、`task.enqueued`、同会话 `agent.message` +1)。本项属**回复语义质量**,不在任何既有验收项内 |
| **若未来要改,涉及层** | 最小面为**上下文构造侧**:`engine.run_for_task` 消费 `task.meta` + `system_prompt` 增受控段(**不动事件、不动减少器、不动 compaction**);若改为产品侧重定义 intent 语义,则须改 `_fire` 写入形状,波及 replay/compaction/UI |
| **状态** | `CLOSED`(2026-09-21 R24;O-1 已裁定=真的发出提醒,实现见上表 O-1 行) |
| **关联** | `core/schedule.py:540` · `engine.py:778` · `core/session.py:192` · `core/system_prompt.py:405` · `core/task_queue.py` · Phase 0–3 Closure Report |

## B.4 KF-D(P2)— In-process scheduler execution boundary

| 字段 | 内容 |
|---|---|
| **现象** | 应用运行时到点可触发;**应用退出后不再触发** |
| **Schedule Definition 是否持久化** | **是**。`schedule.registered` 存 name/kind/expr/`template.intent`/`is_risky`/`paused`/`next_fire_at` 快照;`last_fired_at` 由 `schedule.trigger` 派生(`core/schedule.py:131` `ScheduleJob`) |
| **Execution 是否持久化** | **否**。ticker 为进程内 asyncio 协程(`core/schedule.py:781`),随 spine 生灭;任务队列 `_q` 为纯内存 `deque`(`core/task_queue.py:123`),**无重启补投** |
| **应用重启如何恢复** | `recover()`(`core/schedule.py:643`)回放重建 → 核算 `missed` → 重臂 `next_fire_at` → `_ensure_ticker` 启泵。**未来触发照常**;宕机窗内错过的**只记不补**(D-3) |
| **应用关闭时发生什么** | `EngineSpine.close()`(`engine.py:108`)→ `self.schedule.stop()`(`:125`)← `ApplicationService.shutdown()` ← `DesktopApp._shutdown_async()`。**拆卸完整,无泄漏 ticker** |
| **未打开会话为何不运行** | spine 为**逐会话懒装配**:`_engines[sid]` 仅在 `application/service.py:222` `queue_for` / `:278` `public_spine_for` 写入,**无启动全量扫描**。即应用运行期间,用户未点开的会话其定时任务**同样不触发** |
| **单写者锁约束** | `persistence.py:179` `acquire_session_lock` 取 OS 级非阻塞独占锁,被占即 `PERS-202`(`:191`)。⇒ 应用持锁期间**外部进程无法打开同一会话**,构成"外部 tick"方案的前置约束 |
| **为何不是 Bug** | 恢复、持久化、防重复均已实现且有测试(含**真实落盘**重启用例 `tests/unit/test_schedule_nl_phase2.py`);进程内模型是 ADR-005 的直接推论,且 `docs/MAP.md:233` 已声明"子 Agent/jobs/schedule/workflow 全为协程级并发" |
| **准确名称** | **in-process, event-sourced scheduler**(定义持久 / 执行**不**持久)。称 "persistent scheduler" 会误导 |
| **若未来要支持关机后继续触发** | 最小变化 = 新增 CLI 子命令复用 `recover()+_tick()`,由系统计划任务周期调用;**前置**必须先解决与单写者锁的关系(否则 app 持锁时外部 tick 必失败) |
| **状态** | `OPEN`(待 O-2) |
| **关联** | `core/schedule.py:643,781` · `engine.py:108,615` · `core/task_queue.py:123` · `persistence.py:179` · `application/service.py:222,278` · `docs/MAP.md:233` · ADR-005 |

**登记口径声明**:本附录仅登记**事实、证据、边界与决策**,**不含任何修复动作**;KF-C / KF-D 的改动均**需另行人工授权**,且须**先有 O-1 / O-2 的产品答案**。

*— 附录 B 完 —*

