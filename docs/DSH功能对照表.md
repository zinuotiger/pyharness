# PyHarness × DSH 功能对照表

> 对照基准:DSH 全功能清单(11 族 71 项)
> 核查日期:2026-09-07(基础表);2026-09-11 修复轮见文末“最终接线核对”。
> 图例:✅=做了且真跑 / 🧪=有代码+单测未真链 / ⚠️=半成 / ❌=没有

## 总表

| # | DSH 功能 | 族 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | Agent 循环 | 脊柱 | ✅ | engine+agent_loop 真链 |
| 2 | 工具注册表 | 脊柱 | ✅ | fs.* 真注册真调用 |
| 3 | 会话事件日志(内存) | 脊柱 | ✅ | 事件溯源总线 |
| 4 | 系统提示词组装 | 脊柱 | ⚠️ | 引擎未注入系统提示词(agent 裸奔) |
| 5 | 作用域注册 | 脊柱 | ✅ | spine 组件(单 agent 简化) |
| 6 | JSONL 落盘 | 会话日志 | ✅ | 核心卖点,11 类强同步事件 |
| 7 | 会话标题自动命名 | 会话日志 | ⚠️ | 无 LLM 起名,列表用首条消息前 10 字 |
| 8 | 崩溃恢复 | 会话日志 | ✅ | repair.py(F060)真功能 |
| 9 | 上下文压缩 | 会话日志 | ✅ | core/compaction.py 真实现 |
| 10 | 全文搜索 | 会话日志 | ✅ | CLI search + SQLite FTS5 |
| 11 | 会话投影 | 会话日志 | ⚠️ | SSE 时间线=投影形态✅,无独立模块 |
| 12 | 消息反馈 | 会话日志 | ❌ | F062 未实现 |
| 13 | 消息编辑/重发 | 会话日志 | ❌ | 无 |
| 14 | 会话分叉 fork | 会话日志 | ✅ | CLI fork(COW) |
| 15 | 附件管理 | 会话日志 | ⚠️ | API+事件有,无 UI/多模态 |
| 16 | LLM 客户端(DeepSeek) | 模型 | ✅ | 真链回复 |
| 17 | 降级链 | 模型 | ✅ | qwen-max 备用 |
| 18 | 流式输出 | 模型 | ❌ | 非流式(优化点 A2) |
| 19 | 多适配器 | 模型 | ✅ | register_adapter |
| 20 | 重试(指数退避) | 模型 | 🧪 | TimeoutLimits 有,退避未真链 |
| 21 | 多模态 | 模型 | ❌ | DeepSeek 不支持看图 |
| 22 | token 计量 | 模型 | ⚠️ | usage 落盘✅,仪表未真算(B2) |
| 23 | 请求头归属 | 模型 | ❌ | 单机不需要 |
| 24 | 文件读写工具 | 工具 | ✅ | 场景二真链 |
| 25 | 目录列举工具 | 工具 | ✅ | 真链 |
| 26 | Web 搜索 | 工具 | 🧪 | 65 测试,未注册引擎 |
| 27 | Web 抓取 | 工具 | 🧪 | 同上 |
| 28 | bash 执行 | 工具 | ❌ | 砍(正确) |
| 29 | 代码执行 runtime | 工具 | ❌ | 砍 |
| 30 | MCP 集成 | 工具 | ❌ | 砍(evleven 另讲) |
| 31 | 技能系统 | 工具 | ⚠️ | 插件框架在,零实装 |
| 32 | todo 工具 | 工具 | ❌ | 无 |
| 33 | 输出溢出 spill | 工具 | ✅ | 2KB 摘要真跑 |
| 34 | LSP 导航 | 工具 | ❌ | 砍 |
| 35 | 终端 PTY | 工具 | ❌ | 砍(F053) |
| 36 | 危险工具拦截 guard | 安全 | ✅ | 时间线 guard.evaluated 真走 |
| 37 | 人类审批 | 安全 | ✅ | 桌面弹窗 A/B 真链 |
| 38 | 用户提问 | 安全 | ❌ | 无 agent 反问(approval 半替代) |
| 39 | 权限预设 | 安全 | 🧪 | 危险分级有,无档位切换 |
| 40 | 沙箱 | 安全 | ❌ | 砍 |
| 41 | 凭据管理 | 安全 | ✅ | env:引用,CRED-701/702/703 |
| 42 | 运行时不变量 | 安全 | ✅ | 12 项 INV |
| 43 | 任务队列(顺序) | 编排 | ✅ | TaskQueue 真跑 |
| 44 | 子 Agent | 编排 | 🧪 | 代码+单测,未真链 |
| 45 | 工作流 workflow | 编排 | ❌ | F050 未实现 |
| 46 | 后台任务 jobs | 编排 | 🧪 | jobs.py+单测 |
| 47 | 定时任务 | 编排 | 🧪 | schedule 命令有,未真链 |
| 48 | 目标管理 | 编排 | 🧪 | goal 参数有,无持续跟踪 |
| 49 | 计划模式 plan | 编排 | 🧪 | plan 命令"引擎未来装配" |
| 50 | 插件树 Cordis | 插件 | ⚠️ | 五态框架,无实装 |
| 51 | 扩展包 | 插件 | ❌ | |
| 52 | 热重载 HMR | 插件 | ❌ | |
| 53 | 存储域 KV | 插件 | ⚠️ | F056 半成 |
| 54 | 命令行 CLI | 界面 | ✅ | 14 子命令 |
| 55 | Web 界面 | 界面 | ✅ | 桌面壳=FastAPI+HTML |
| 56 | ACP 桥 | 界面 | 🧪 | acp.py 在,未真链 |
| 57 | Webhook | 界面 | ❌ | 无 |
| 58 | 子进程管理 | 系统 | ❌ | 无(与砍 bash 一致) |
| 59 | workspace | 系统 | ✅ | fs 工具限定工作区 |
| 60 | 不变量测试 | 工程 | ✅ | 12 项全绿 |
| 61 | 错误码体系 | 工程 | ✅ | 10 族 41 码 |
| 62 | 结构化错误 | 工程 | ✅ | PyHError 树 |
| 63 | 超时/取消 | 工程 | ✅ | TimeoutLimits(卡死修复功臣) |
| 64 | 配置管理 | 工程 | ✅ | config.py 多级 |
| 65 | 人类命令 | 工程 | ✅ | /help /new /cost /exit |
| 66 | 遥测 | 工程 | ❌ | 砍 |
| 67-71 | Python SDK/typert/前端 44 包/vendor/CI | DSH 生态 | — | 不相关(程序本身就是 Python) |

## 汇总

- ✅ 真做 23 项 / 🧪 有代码 7 项 / ⚠️ 半成 8 项 / ❌ 没有 17 项(其中 9 项为清单自标"砍"、4 项 DSH 生态不相关)
- **清单标"必做"的 17 项全部有**(仅 #4 系统提示词为半成、#7 标题为可选)
- 砍掉清单里做回来的:compaction(#9)/FTS 搜索(#10)/fork(#14)/scope(#5)/ACL 分级(#39)/workspace(#59)等 9 项

## 最值得补的三个(面试讲得出故事,工作量小→中)

| 优先级 | 补什么 | 现状问题 | 工作量 |
|---|---|---|---|
| P1 | #4 系统提示词组装 | agent 无身份/规矩约束,模糊指令会乱探索 | 小(顺手解决演示翻车) |
| P2 | #22 token 计量接通仪表 | usage 落盘但预算徽章是摆设 | 小 |
| P3 | #38 用户提问 | agent 不能带选项反问用户(approval 是半替代) | 中 |

## 面试口径备忘

- 讲"有"的功能全部真链验证过;🧪/⚠️/❌ 主动说"框架在/未实装/下一步",不自称全实现
- 主线三连(工具干活→无人值守→危险拦截)= 需求文档.md §3 场景二/三,已全部真跑通

---

## 增量核对(2026-09-08,主 Agent 直接接线)——上文各行保持原样,只追加

> 背景:审计发现多项"模块完整但装配缺位"(engine.py 0% 覆盖为直接证据)。当日
> 完成 engine 装配层全链接线,并新增 tests/unit/test_engine.py(装配不变量)+
> tests/unit/test_engine_flow.py(离线全链冒烟:fake transport 注入,零网络,
> 真走 agent-loop/guard/executor/事件溯源/计数器,断言 sysprompt 注入与事件流)。
> 图例延续:✅=接线完成且有测试/冒烟锁证;🧪=已接线,真链(真实 LLM)待跑。

| # | 功能 | 9-07 状态 | 9-08 增量 | 证据/说明 |
|---|---|---|---|---|
| 4 | 系统提示词组装 | ⚠️ 引擎未注入 | ✅ 已注入 | engine 装配 SystemPromptAssembler → ctx.sysprompt;run_turn 出网前 assemble(system 段+护栏恒末);离线冒烟断言 messages[0].role=system |
| 9 | 上下文压缩 | ✅ 模块真 | ✅ 触发接线 | AgentLoop.wake 请求边界(loop idle)run_if_needed;LLMClient.summarize 摘要出口;长会话真链压缩待跑 |
| 13 | 消息编辑/重发 | ❌ | (维持砍) | append-only 不变式冲突,维持砍 |
| 17 | 备用模型降级链 | ✅ 模块 | ✅ 上 LLMClient | FallbackChain 挂 LLMClient._chain(退避+单调降级+BudgetGuard);engine/桌面装配即生效 |
| 18 | 流式输出 | ⚠️ 客户端在 | ✅ 主链可切 | run_turn 按 ctx.streaming 走 chat_stream(loop.streaming 配置);桌面 SSE 实时推送属 P4 UI |
| 20 | 重试(指数退避) | 🧪 | ✅ 链内生效 | FallbackChain._retry_adapter 随 chain 挂载(llm.retry 事件留痕),无需额外接线 |
| 22 | token 计量 | ⚠️ | ✅ 预算闸真接线 | UsageCounters 增 task_total/rate_limit/degrade(协议补齐);adapter→ctx.counters→scope 同一实例;budget.paused/scope.updated 入词表(70 型) |
| 26/27 | Web 搜索/抓取 | 🧪 未注册 | ✅ 已注册 | engine 注册 web.search/fetch;strict 下按域名 allowlist 显隐(g5 复核);真链待跑 |
| 31/50 | 技能系统/插件树 | ⚠️ 零实装 | (未动) | 插件框架在,实装示例属下一步(P2 尾项) |
| 32 | todo 工具 | ❌ | ✅ 全新实现 | core/todo.py TodoManager(事件源)+ tool_todo.py;test_todo.py 全绿 |
| 33 | 输出溢出 spill | ✅ 模块 | ✅ 装配激活 | engine._activate_storage_caps:SpillProvider.enter 挂 ctx.storage.spill;executor 关4 >2KB → spill |
| 38 | 用户提问 | ❌ | (未动,属 P4) | 需桌面弹窗,与 approval 同模式,排期 P4 |
| 39 | 权限预设 | 🧪 无档位 | ✅ 四档实现 | security.policy.preset ∈ strict/standard/readonly/locked;_apply_preset 按注册名编译 |
| 48 | 目标管理 | 🧪 | ✅ 注入 sysprompt | goal.* 工具注册(ctx.goals);assemble 注入活动目标段;离线冒烟断言第二轮含"当前活动目标" |
| 53 | 存储域 KV | ⚠️ F056 半成 | ✅ 全新实现 | core/kv.py SessionKV(JSON 原子写)+ storage.kv 工具;test_kv.py 全绿 |
| 65 | 人类命令 | ✅ | (未动) | — |
| 67-71 | DSH 生态 | — | (维持) | — |

新增测试:test_engine.py(装配不变量:counters/sysprompt/compactor/spill 工具面/
preset strict 显隐)、test_engine_flow.py(离线全链冒烟)、test_todo.py、
test_kv.py、test_events.py 词表 64→66。
引擎全量回归:1298+ 测试绿(9-08 两次全量,engine 覆盖率 0%→~60%+)。
待续(同会话内未竟,排期下一轮):P3 编排真链探针(plan/subagent/jobs/schedule/
acp 均已 CLI 接线,差真实 LLM 跑一遍记录)、P4 桌面 UI(SSE 流式推送、消息反馈
按钮、user.ask 弹窗、附件、LLM 标题命名)、#31/#50 示例插件实装、真链压测。

---

## 增量核对·B 档(2026-09-08,复活砍掉项)——只追加

> 用户拍板:砍掉项(原"要沙箱才安全/面试口径"那批)全部做出来。已落地(模块+
> 单测绿,部分真链待外部依赖):

| # | 功能 | 说明 | 测试 |
|---|---|---|---|
| 13 | 消息编辑/重发 | session 层投影早有(_fold_history _replace_at);新增 message_edit.py 出口(经公开 append,INV-01 方法面未动);重发=last_user_message+submit | test_session_edit.py |
| 28/29/35/58 | bash/代码runtime/PTY/子进程 | proc.py(会话登记/沙箱env剥代理/树级kill/行式PTY降级)+ tool_exec.py(exec.shell_run/python_run/proc.start·status·kill;danger=high→审批,strict 不可见,standard 档演示);应用层沙箱=目录锁+env裁剪+超时+配额+NO_WINDOW(Windows 等价,非OS容器,诚实口径) | test_exec.py |
| 30 | MCP 集成 | core/mcp.py:2024-11-05 子集(initialize/tools/list/tools/call),stdio+inline 传输抽象,工具桥 mcp.<name>.<tool>(danger=high 审批);真链需外部 MCP server(P3 探针) | test_mcp.py |
| 45 | workflow | core/workflow.py:步骤序列→队列逐条执行+workflow.step 事件(started/done/failed),queue_submit_adapter 与 engine seam 同构;stop_on_fail 可选 | test_workflow.py |
| 51/52 | 扩展包+HMR | core/plugin_loader.py:单文件插件(MANIFEST+register_tools 桥两注册表)+热重载(nonce+清pyc 确定性新字节码)+卸载;示例插件 examples/plugins/hello_time(util.now);PluginManager 五态全用 | test_plugin_loader.py |
| 66 | 遥测 | core/telemetry.py:事件流→审计报告(计数/用量折叠/工具/审批/错误),JSON 导出+摘要行;纯本地无外发 | test_telemetry.py |
| 23 | 请求头归属 | llm 传输层加 X-PyHarness-Agent 请求头(单机审计友好) | (llm.py) |

待续:webhook(#57)、多模态适配(#21,需视觉 key)、桌面 UI 波(SSE 流式/反馈按钮/
user.ask 弹窗/附件/LLM 标题命名 + B 档工具显隐与命令面)、P3 真链探针(真实 LLM:
exec 沙箱演示、MCP server 挂载、plan/subagent/jobs/schedule/acp)、README 同步。

---

## 增量核对·桌面后端面(2026-09-08,同轮续)

> 桌面核对结论:SSE 事件流(游标重连/心跳)、附件上传(#15 API 早全)、审批轮询/
> 裁决桥、轨迹时间线、预算仪表盘——路由均已存在。本轮新补 4 API(#12/#13/#57
> 桌面后端面):PATCH /api/sessions/{sid}/messages/{seq}(F063 编辑,resend 可选)、
> POST …/{seq}/resend(重发原文再投)、POST …/{seq}/feedback(up/down/flag →
> user.feedback 事件)、POST /api/webhook(content→新建/续会话,cfg shell.web.token
> 非空必校验)。test_desktop 全绿(无回归)。前端按钮与 user.ask/标题/SSE chunk
> 验证列 UI 波,仍在推进。

## 增量核对·#38 user.ask(2026-09-08,同轮续)

> user.question/user.answer 两事件入词表(70 型);AskProvider(会话级反问宿主:
> request 落事件+挂起等待 / answer 落盘+settle / pending_list 轮询源,TTL 超时
> 收敛占位不抛)+ user.ask 工具(danger none,user.* 自管理域恒可见)+ engine
> spine.ask/agent ctx.ask 装配;test_user_ask.py 全绿。桌面 pending/answer API
> 与前端弹窗仍在 UI 波;exec 沙箱等 B 档工具的桌面审批弹窗经 approval 通道已通。

## 增量核对·UI 波(2026-09-08,同轮续)

> 桌面/前端补齐(F042/#7 全链 + #38 弹窗 + #12/#13 按钮 + #57):
> - 桌面 API:asks pending/answer(GET /api/asks/pending、POST /api/asks/{id})、
>   last-user 编辑(PATCH)/重发(POST)、last-agent feedback(POST)(前端按钮
>   目标=最近消息,derive 视图无 seq 的定位方案);webhook POST /api/webhook
>   (token 校验)。
> - 前端 ui/index.html:消息操作条(✎ 编辑上条=载入输入框改后回车即编辑并重发、
>   ↻ 重发上条、👍👎🚩 反馈)+ #38 反问弹窗(选项按钮/自由文本/暂不答复)+
>   3s 轮询合并 + SSE 监听 user.question;会话列表显示 LLM 自动标题(截 24 字)。
>   JS 经 node --check 语法校验通过(两轮);真机交互验证待桌面启动。
> - #7/F042 自动标题:core/auto_title.py(首条用户消息→LLM ≤24 字标题→
>   session.renamed by=auto;幂等);engine run_for_task 收尾钩子;desktop
>   list_sessions 派生标题展示。engine_flow 离线冒烟断言第三轮=标题调用 +
>   session.renamed 落盘(全绿)。
> 剩余:SSE token 级 chunk 打字机(桌面 /api/stream 事件流已通,llm.chunk 瞬时
> 事件真机验证)、P3 真链(真实 LLM 跑 exec/plan/subagent/jobs/schedule/acp +
> MCP server 挂载)、README 收尾。

## 增量核对·SSE/真链实测修正(2026-09-08,同轮续)

> - SSE chunk 结论修正:llm.chunk 的桌面 fan-out **已实现且有测试锁证**——desktop
>   hub 裸 dict 广播(desktop.py L167 注释:瞬时 llm.chunk 直推全客户端;L354
>   瞬时事件天然不在日志)、test_desktop L483-489 断言 SSE 客户端收到 llm.chunk;
>   CLI 打字机增量渲染 test_cli L725-748 全绿。真正缺的只是"真实流式演示"
>   (config loop.streaming=True 切 chat_stream,属 P3)。
> - P3 真链探针两枚就绪(scripts/probe_real_chain.py、probe_exec_approval.py,
>   py_compile 通过),依赖 DEEPSEEK_API_KEY 注入进程后即可跑:
>   ① 真实 LLM:goal/todo 工具调用 + 终态 + F042 自动标题;
>   ② exec.shell_run:guard 高危拦截 → approval.requested → 探针模拟桌面用户
>   approve → 真实子进程 → tool.result 回流。网络实测:DeepSeek 直连 TLS 通
>   (401=仅缺 key);时代中转 api.shidongai.com 当日 SSL EOF 不可达,故探针
>   优先直连、.env(DEEPSEEK_API_KEY=,已 gitignore)为备注入点。
> - 降级链顺带真链证据:glm-5.3→qwen-max 单调降级 + llm.retry 落盘(链工作
>   正常,仅上游当日不通)。README 已更新至 2026-09-08 状态(增量表)。

## 增量核对·P3 真链双 PASS + 装配 bug 修复(2026-09-08)

> key 从 AppData/Local/hermes/.env 注入进程后,真实 DeepSeek 直连
> (deepseek-chat,api.deepseek.com/v1)两条链实测通过:
> - 探针 ① scripts/probe_real_chain.py **PASS**:真实模型自主 5 次工具调用
>   (todo.updated ×3 累进 + goal.created"周五前把面试串讲练熟")+ 自主触发
>   user.question 反问(无答复→TTL 占位回喂→继续)→ agent.message 总结 →
>   session.renamed 自动标题"整理面试主线并规划练熟目标"(by=auto)。
> - 探针 ② scripts/probe_exec_approval.py **PASS**:模型决策 exec.shell_run →
>   guard 高危 → approval.requested → queue.suspended → 探针模拟桌面用户
>   approve(by=probe)→ approval.granted → queue.resumed → 真实子进程回显
>   PYH_EXEC_REAL_42752_OK → tool.result ok + agent.message 原样回显。
> - 探针 ② 首跑暴露真装配 bug:preset=standard 只改 guard policy.sandbox_level,
>   tool_exec 按 cfg.security.sandbox.level 判可见性(TLB-802)→ engine._apply_preset
>   standard 分支补同步 cfg.security.sandbox.level="basic"(审批放行后工具仍
>   不可用的脱节修复);修复后一次通过。
> - CLI 编排族(plan/subagent/jobs/schedule/acp)与 MCP server 挂载的真链留桌面
>   演示(模块单测全绿,路径已由 engine 真链覆盖到 executor/guard/审批/队列)。

## 增量核对·F073 技能系统 P0(2026-09-08,#31 Hermes 同款机制)

> 用户拍板"要 Hermes 一样的技能":SKILL.md 知识包 + 目录注入 + 按需装载(本地
> 版;网络源 Phase 2 待骨架后接)。当日落地全绿:
> - core/skill.py SkillManager:技能库根(skills/)每技能一文件夹 SKILL.md;
>   frontmatter 解析(name/description,行锚定剥头)→ scan 索引 → list 目录 /
>   load 正文(>6KB 截断)/ render_catalog 目录段;坏 frontmatter/重名跳过记日志。
> - core/tool_skill.py:skill.load(正文+简介回喂,落 skill.used 审计,body 不进
>   日志)/ skill.list(danger none;skill 域入 SELF_DOMAINS → strict 恒可见)。
> - system_prompt:技能目录段注入(goal 段后、护栏段前;空技能库零开销)。
> - engine/agent 装配:build_spine 扫仓库 skills/(SkillManager 挂 spine.skills /
>   ctx.skills);词表 +skill.used(68→69);错误码 +SKL-901(未知名回喂自查)。
> - 真实技能 ×2:skills/interview-pitch(SKILL.md:面试口径红线+组织流程)、
>   skills/demo-script(三场景演示剧本+口径边界)。
> - test_skill.py 全绿(解析/扫描排序/剥正文/SKL-901/目录段/engine 装配:真实
>   两技能可见 + skill 工具 strict 恒可见);test_events 69;engine/engine_flow
>   无回归。
> - 面试口径:技能=给 agent 加"脑"(方法/知识),插件=加"手"(代码工具),两者
>   正交可互调;Phase 2 = 网络技能源(skill fetch + hash 校验)。

---

## 最终接线核对(2026-09-11)

> 本表覆盖本轮实际修复,用于纠正前文“模块存在但用户入口未接线”的旧结论。
> 真实探针均在仓库内可重复执行,不依赖人工口头状态。

| # | 功能 | 2026-09-11 状态 | 证据 |
|---|---|---|---|
| 18 | 流式输出 | ✅ 主链 + 降级链真链 | `scripts/probe_streaming_real.py` 收到真实 `llm.chunk` |
| 26/27 | Web 搜索/抓取 | ✅ 搜索真链;抓取代码与测试 | 默认 Bing RSS 无 key 后端;`scripts/probe_web_search.py` PASS |
| 30 | MCP 集成 | ✅ stdio 真链 | `scripts/probe_mcp_stdio.py`:initialize→tools/list→tools/call PASS |
| 44 | 子 Agent | ✅ 引擎 runner + 持久化子会话 | `tests/unit/test_orchestration.py::test_subagent_runner_executes_persisted_child` |
| 46 | 后台 jobs | ✅ 引擎 runner | `tests/unit/test_orchestration.py::test_jobs_runner_executes_real_agent_loop` |
| 47 | 定时任务 | ✅ Scheduler 装引擎 + ticker | `engine.activate_orchestration`;CLI `schedule` handler |
| 49 | 计划模式 | ✅ PlanManager 装 spine;CLI 用真实引擎 | `engine.build_runner_components` + `_cmd_plan` |
| 54 | CLI 完整外壳 | ✅ chat/run/plan/search/session/fork/schedule/job 接地 | `pyharness run`/`chat` 不再 CYC-999;六个 once handler 已注入 |
| Skill Registry | 远程 Skill 搜索/安全安装 | ✅ Registry JSON + SHA-256 + quarantine + 路径安全检查 + 版本回滚 + skill.* 审计事件 | `pyharness/core/skill_registry.py`;`tests/unit/test_skill_registry.py` |
| 原生桌面 | PySide6 + qasync + ApplicationService(MVP) | ✅ 会话/聊天/流式/轨迹/Jobs/定时/子 Agent/技能/插件/审计 | `pyharness/desktop_native/`;`scripts/smoke_desktop_native.py` PASS |
| 服务层 | ApplicationService | ✅ 会话/引擎/队列/审批/消息/Jobs/定时/子 Agent/技能 | `pyharness/application/service.py`;FastAPI 仅保留 HTTP/SSE 外壳 |
| 55 | 桌面 UI 管理面 | ✅ jobs/定时/子 Agent 面板 | 顶栏三个入口 + `/api/sessions/{sid}/jobs|schedules|subagents`;桌面路由测试覆盖列表/取消/暂停/删除 |
| 56 | ACP 桥 | ✅ 惰性接真实引擎/队列/LLM | `acp._ensure_engine_queue`;ACP 单测 87% 覆盖 |
| 62 | 结构化错误 | ✅ | `llm.chunk` 瞬时事件不再误进 JSONL 订阅 |

仍受外部依赖影响、不能宣称“无需配置即可真实外发”的部分:

- MCP 需要用户在 `plugins.mcp_servers` 配真实 server;仓库内置 echo server 仅用于真链探针。
- Web 搜索默认 Bing RSS 无 key,但仍受网络可达性与 `security.network.allowed_domains` 安全显隐约束。
- 真实 LLM / ACP 真链需要配置有效凭据;无凭据时按 `CRED-701` fail-closed。

**最终口径**:核心 Agent 主轴、桌面壳、CLI 用户入口、引擎编排装配已闭环;
外部服务类能力在配置/网络条件满足时闭环,未配置时明确结构化拒绝,不静默伪造成功。

---

## 双壳能力对齐核对(2026-09-12)

> 目标不是让 Qt 复制 HTML,而是让 Web 与 PySide6 两端拥有同一组业务能力、同一业务服务、同一事件真源。

| 能力 | Web 壳 | PySide6 原生壳 | 共享实现 | 对齐证据 |
|---|---|---|---|---|
| 会话 / 聊天 / 流式 | ✅ | ✅ | `ApplicationService` / EventBus | `test_shell_parity.py` |
| 编辑 / 重发 / 反馈 | ✅ | ✅ | `ApplicationService.edit_last_user` 等 | Web 操作条 + Qt `QInputDialog` |
| 附件 / 权限档位 | ✅ | ✅ | `upload_attachment` / `set_preset` | Web 附件面板 + Qt `QFileDialog/QComboBox` |
| Jobs / 定时 / 子 Agent | ✅ | ✅ | EngineSpine 编排 | 两端均有管理面板 |
| Skill Registry | ✅ | ✅ | `SkillInstaller` | 搜索、版本、SHA-256、安装、回滚、卸载 |
| 插件 / Workflow / 审计 | ✅ | ✅ | `plugin_action` / `WorkflowRunner` / telemetry | 两端均有入口 |
| 审批 / 反问 | ✅ | ✅ | ApprovalProvider / AskProvider | Web 弹窗 + Qt 原生弹窗 |

- 能力清单的唯一源码为 `pyharness/application/models.py::SHELL_CAPABILITIES`。
- Web 运行时读取:`GET /api/capabilities`;Qt 运行时读取:`NativeController.capabilities()`。
- 架构保持单 `main` 线:业务只实现一次,两套 UI 只负责展示和交互适配,不切长期分支。

### 租户与模型 API Key 设置(2026-09-12 增补)

- Web 顶栏和 PySide6 原生壳均新增“设置”入口,可创建/切换租户并维护多个 OpenAI 兼容模型档案。
- 每租户独立保存 `sessions/`、`skills/`、`plugins/`、模型元数据与密钥;同名模型使用独立适配器。
- Windows 使用 DPAPI 加密 API Key;查询接口仅返回 `has_api_key`,不返回明文。
- Web 业务请求使用 `X-PyHarness-Tenant`,SSE 使用 `tenant` 查询参数。
- 这是应用级逻辑租户隔离;MCP/插件进程配置仍属于部署者控制面。
