# specs/commands.py.md — 编码规格

> **模块文件**:`pyharness/core/commands.py` | **功能编号**:F041(斜杠命令,核心)· 联动 F040(todo 状态,`/status` 数据源)/F032(预算,`/cost` 数据源)/F042(会话标题)/F064(CLI 外壳 headless R8)/F015(审批通道语义) | **权威口径**:PRD-Core §5.4 F041(路由伪代码)、EVENT-SCHEMA §3.2(user.command"命中即写、不产生 user.message、不消耗 LLM")/§6.2(EVT-105)、ERR.md §2.2(EVT-105)/§2.8(APR-501)、DEP.md §5.2/§5.3(命令表与生命周期)、CFG.md §3.8(R8:headless 由 stdin 是否 tty 判定,无配置开关);冲突以 PRD-Core 为准
> **一句话**:斜杠命令总闸——输入以 `/` 开头即走本模块:**先落 `user.command`(命中即写,EVT-104 安全序)再分发**,命中即"不给 LLM 的控制通道"(零 LLM 保证,INV-02);未知命令回显 `system.error(EVT-105)` 会话继续;headless(管道/后台 job,stdin 非 tty)下状态变更类命令无交互通道直接拒(APR-501 语义,R8)——危险动作不存在于斜杠面,一切执行走工具+guard(F041 边界条件)。
> **代码目录**:`pyharness/core/commands.py`(三壳共用内核:F064 chat / F065 desktop / F066 ACP 经 agent.submit 进同一分发;外壳只做 IO 回显,判断/事件/状态都在本模块)。

## 模块职责

1. **注册表与分发(F041)**:`COMMANDS` 有序注册表(name→handler+danger+help);`handle_slash(raw, ctx)` 解析 `/name args`,命中 → **先** `session.append("user.command", {name, args})` **再**调 handler(EVENT-SCHEMA"命中即写本事件"为权威——先落盘保证 `/new` 结束会话前命令已留痕,规避 F041 伪代码"先执行后落盘"在终态命令上的 EVT-104 拒写缺陷);未命中 → 不写 user.command,回显 `system.error(EVT-105)` 会话继续,零 LLM。
2. **零 LLM 硬保证**:命令处理路径不 import llm、不触达 agent-loop 的模型调用——`/foo` 后 `llm.request` 事件数不增是验收断言(DEP G5);命令数据一律从事件派生(原则 1:status/cost 读会话事件,不建第二份内存状态)。
3. **headless 安全默认(R8/F064/CFG §3.8)**:headless = 外壳装配时 `ctx.channel is None`(CLI 管道/后台 job,stdin 非 tty;无配置开关可改);只读命令(help/status/cost)恒允许;状态变更命令(new/exit)danger=high → headless 直接拒(APR-501,零副作用),与工具审批的 R8 同构——管道脚本不能静默结束/重置会话。
4. **内置命令面(F041 命令表,DEP §5.3 对齐)**:`/help` 命令帮助(可带名查单条);`/new` 结束当前会话开新(日志留档,交互必需);`/status` 会话/任务/todo 进度快照(F040,只读离线派生);`/cost` 预算成本报表(F032 数据,只读,离线可用);`/exit` 退出(别名 `/quit`,交互必需;F064 Ctrl-D 等价)。别名表:`quit→exit`、`budget→cost`(兼容 DEP §5.3 文档命令);`/undo /plan /schedule` 为阶段 4 扩展点——本阶段未注册,命中 → EVT-105(PRD F041"命令集随阶段扩展")。
5. **扩展注册点(阶段 4 挂载)**:`register_command` 供 plan_mode/undo 等模块运行时注册新命令(各自 import,不产生阶段 import 依赖,INV-08);重复注册 = 装配期 ValueError 早失败(编程错误不进事件系统,禁现场造码)。
6. **与审批域的关系**:斜杠命令本身不触发工具审批;headless 拒绝复用 APR-501 码(ERR"无人工审批通道,请求已拒绝"语义扩展)并以 `system.error` 留痕,命令不执行、不写 user.command(零执行零事件,与工具 denied 零 tool.result 同构,INV-05 精神)。

## 依赖

- **单向依赖**:本文件 → `session.append`(user.command/system.error 事件)、`errors`(PyHError)、`ctx.channel`(外壳装配注入:tty 交互="cli"/ACP="acp:<id>"/headless=None)、`ctx.session`(事件查询:标题/seq/todo.updated/llm.usage 派生)、`ctx.budget`(F032 状态,未装配时降级只输出用量段)、`ctx.agent`(new/exit 的会话生命周期委托,finished 唯一归属见 DIS-CORE §2.3)——**不 import llm/agent_loop**(零 LLM 保证)。
- **消费方**:外壳输入循环(F064 chat/桌面)检测首字符 `/` 后调用;agent.submit 内部对命令输入不进 agent-loop(命中即不产生 user.message)。
- **外部**:shlex(参数切分,保守模式)。

## 数据结构表

**命令注册表(`COMMANDS`,有序 dict,注册序即 /help 展示序)**:

| name | danger | 类别 | handler | help(作用一句话) |
|---|---|---|---|---|
| `help` | none | 只读 | cmd_help | 命令帮助;/help <name> 查单条 |
| `status` | none | 只读 | cmd_status | 会话/任务/todo 进度快照(F040/F042) |
| `cost` | none | 只读 | cmd_cost | 当前会话预算/成本报表(F032) |
| `new` | high | 状态变更(交互必需) | cmd_new | 结束当前会话并开新(日志留档) |
| `exit` | high | 状态变更(交互必需) | cmd_exit | 退出会话/外壳(别名 /quit) |

**别名表**:`quit→exit`、`budget→cost`(解析期先查别名再查主表;别名不占独立 help 行,标注于主命令)。

**其他结构**:

| 结构 | 字段 | 规则 |
|---|---|---|
| `CommandEntry` | name/handler/danger/help | 注册即冻结(装配后只读) |
| `CmdResult` | text(回显)/ok | 由外壳渲染;headless 拒绝时 text 走 stderr |
| 事件 | user.command/system.error | name/args;code=EVT-105 或 APR-501 + hint(不含参数原文,审计最小化) |

## 类与函数清单

### `async def handle_slash(raw: str, ctx) -> bool` — 斜杠命令分发总入口(F041)

**功能**:解析 `/name args` → 查别名/主表 → 命中先落 `user.command` 再执行 handler(返回其回显);未知 → `system.error(EVT-105)` 回显、会话继续;恒返回 True(本输入已被命令通道消费,不产生 user.message),全程零 LLM。

```python
async def handle_slash(raw, ctx):
    body = raw[1:].strip()                                         # 去掉首 "/"
    name, _, arg = body.partition(" ")                             # 首词=命令名
    name = name.lower().lstrip("/")
    entry = COMMANDS.get(ALIASES.get(name, name))                  # 别名→主名→查表
    if entry is None:                                              # 未知命令
        await ctx.session.append("system.error",
            {"code": "EVT-105", "hint": f"未知命令 /{name},可用 /help 查看"},
            actor="system")
        return True                                                # 已消费;零 LLM
    await ctx.session.append("user.command",                       # 命中即写(先落盘!)
        {"name": entry.name, "args": arg.strip()}, actor="user")
    if _gated(entry, ctx):                                         # headless × high
        await ctx.session.append("system.error",
            {"code": "APR-501", "hint": f"无交互通道,/ {entry.name} 已拒绝(headless)"},
            actor="system")
        return True                                                # 拒绝不执行(INV-05 精神)
    text = await entry.handler(arg.strip(), ctx)                   # 分发执行
    ctx.render(text)                                               # 外壳回显(注入,非日志)
    return True
```

**参数表**:`raw`=含首 `/` 的整行输入;`ctx`=会话门面(session/channel/render)。**返回**:bool 恒 True(命令输入永不落 user.message)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 未知命令(不在命令表) | EVT-105 | system.error 回显;会话继续;零 LLM(验收断言 llm 事件不增) |
| `PyHError` | headless 下调状态变更命令 | APR-501 | system.error 留痕;命令零执行;无 user.command |
| `PyHError` | 命令 handler 内部异常 | CYC-999 | 兜底:回显失败+本地堆栈,会话不崩 |

**关联测试**:test_f041_commands.py(路由/EVT-105/零 LLM)、DEP G5(两次 Ctrl-C 退出码 0;/foo 后 llm.request 数不增)、ERR GWT-ERR-02、EVENT-SCHEMA §6.2 EVT-105 处置。

### `def register_command(name: str, handler, *, danger: str = "none", help_text: str) -> None` — 扩展注册点(阶段 4 挂载)

**功能**:向 COMMANDS 注册新命令;重名/非法名(非 `[a-z][a-z0-9]*`)抛 ValueError(装配期编程错误,早失败,不进事件系统);danger ∈ {none, high}(命令面无 critical——危险动作不存在于斜杠面,F041)。

```python
def register_command(name, handler, *, danger="none", help_text=""):
    if name in COMMANDS or name in ALIASES:                        # 重名拒
        raise ValueError(f"command already registered: /{name}")
    if not re.fullmatch(r"[a-z][a-z0-9]*", name):                  # 非法名拒
        raise ValueError(f"illegal command name: /{name}")
    if danger not in ("none", "high"):                             # 命令面无 critical
        raise ValueError(f"command danger must be none|high: /{name}")
    COMMANDS[name] = CommandEntry(name=name, handler=handler,
                                  danger=danger, help=help_text)   # 注册即冻结
```

**参数表**:`name`=命令名(小写);`handler`=async (args, ctx) -> str;`danger`=none|high;`help_text`=帮助一行。**返回**:None。**异常表**:ValueError(重名/非法名/非法 danger)——编程错误,装配期失败,无错误码(不跨边界,禁现场造码)。

**关联测试**:test_f041_commands.py(注册/重名拒)、INV-08(阶段 4 命令模块运行时注册,无静态 import 依赖)。

### `async def cmd_help(args: str, ctx) -> str` — /help(F041 帮助)

**功能**:无参列出全部命令(名/作用,按注册序,≤40 行);带参查单条命令详情;只读,danger=none,headless 可用。

```python
async def cmd_help(args, ctx):
    if args:                                                       # /help <name>:单条详情
        entry = COMMANDS.get(ALIASES.get(args.lower(), args.lower()))
        if entry is None:
            return f"未知命令 /{args};可用 /help 查看全部"           # 纯文本,零 LLM
        return f"/{entry.name} — {entry.help}"
    rows = [f"/{e.name:<10} {e.help}" for e in COMMANDS.values()]
    rows.append("别名: /budget=/cost, /quit=/exit")
    rows.append("未知命令回显 EVT-105;斜杠命令不消耗 LLM")
    return "\n".join(rows)                                         # 注册序输出
```

**参数表**:`args`=可空(目标命令名);`ctx`=门面(本命令不使用)。**返回**:帮助文本。**异常表**:无。

**关联测试**:test_f041_commands.py(帮助列出全部注册命令;EVT-105 hint 引导 /help)、DEP §5.3。

### `async def cmd_status(args: str, ctx) -> str` — /status(会话快照;F040/F042 数据源,只读)

**功能**:打印会话状态快照——sid/标题(F042)/最新 seq/当前任务与 todo 进度(F040,未完成 ≤20 项)/运行状态;全部从会话事件派生(原则 1:聚合 todo.updated 最新态 + session 元数据),零 LLM、零第二份状态。

```python
async def cmd_status(args, ctx):
    s = ctx.session
    title = s.meta.get("title") or "(未命名)"                        # F042 自动标题
    last = s.last_seq()                                            # 事件最新 seq
    todos = _latest_todos(s)                                       # 回放 todo.updated 派生(F040)
    pending = [t for t in todos if not t["done"]][:20]             # 未完成 ≤20
    lines = [f"会话 {s.session_id} | {title} | seq 至 {last}",
             f"运行态: {ctx.budget.state if ctx.budget else 'n/a'}"]
    if pending:
        lines.append(f"任务进度(F040): {len(pending)} 项未完成")
        lines += [f"  [{t['id']}] {t['text']}" for t in pending]
    else:
        lines.append("任务进度: 当前无未完成 todo 项")
    return "\n".join(lines)
```

**参数表**:`args`=可空(未用);`ctx`=门面(session 事件查询/budget 状态)。**返回**:状态文本。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 事件回放异常(日志坏行) | PERS-201 | 隔离记跳,提示 repair;命令仍回显可用部分 |

**关联测试**:test_f041_commands.py(status 含 todo 派生)、test_f040_todo.py 联动(清单状态与 todo.updated 事件一致)、EVENT-SCHEMA todo.updated 消费例。

### `async def cmd_cost(args: str, ctx) -> str` — /cost(预算成本报表;F032,只读离线)

**功能**:打印当前会话成本——in/out tokens、估算费用(聚合 llm.usage 事件的 cost_est)、硬闸额度与用量百分比(warn 80% 提示);数据=事件聚合+预算状态,离线可用(headless 允许,脚本友好),零 LLM。

```python
async def cmd_cost(args, ctx):
    usage = _aggregate_usage(ctx.session)                          # llm.usage 事件聚合(F029)
    line = (f"当前会话 in={usage['in_tokens']/1000:.1f}k "
            f"out={usage['out_tokens']/1000:.1f}k "
            f"估算 {usage['cost_est']:.2f} 元")
    if ctx.budget:                                                 # 装配了预算闸(F032)
        lim, used = ctx.budget.limit_cny, ctx.budget.used_cny
        pct = used / lim * 100 if lim else 0.0
        warn = " ⚠warn 80%" if pct >= 80 else ""
        line += f" | 硬闸 {lim:.2f} 元({pct:.0f}%){warn}"
    return line                                                    # DEP §4.3.4 输出同形
```

**参数表**:`args`=可空(未用);`ctx`=门面。**返回**:成本一行/多行文本。**异常表**:无(数据缺省段降级输出,不抛错)。

**关联测试**:test_f041_commands.py(cost 与 llm.usage 聚合一致)、test_f032_budget.py 联动(warn 80% 标记)、DEP §4.3.4(离线数据实时打印)。

### `async def cmd_new(args: str, ctx) -> str` — /new(结束当前会话开新;DEP §5.2,交互必需)

**功能**:结束当前会话(session.finished,reason=user_command_new,日志留档)并开新会话(seq 从 1);生命周期归属 agent(DIS-CORE §2.3 session.finished 唯一归属),本函数只委托不自己落终态;danger=high——headless 无交互通道直接拒(APR-501,管道不能静默重置)。

```python
async def cmd_new(args, ctx):
    if ctx.agent is None:                                          # 无生命周期通道(理论不可达)
        return "当前外壳不支持 /new"
    old = ctx.session.session_id
    await ctx.agent.finish_session(reason="user_command_new")      # 终态委托(唯一归属)
    await ctx.agent.new_session()                                  # 新会话 seq 从 1
    return (f"已结束会话 {old} 并开启新会话 "
            f"{ctx.session.session_id}(原日志留档,可 session list 找回)")
```

**参数表**:`args`=可空(未用);`ctx`=门面(agent 生命周期)。**返回**:确认文本。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | headless 下调 /new | APR-501 | 前置 `_gated` 已拒;本函数 headless 不可达 |
| `PyHError` | finished 重复/终态后操作 | EVT-104 | 查绕过路径(INV-01);命令不产生 user.message 属预期 |

**关联测试**:test_f041_commands.py(/new 后旧会话 finished+新会话 seq=1)、DEP §5.2(生命周期操作)、EVENT-SCHEMA §6.2 EVT-104(先落 user.command 再终态的序断言)。

### `async def cmd_exit(args: str, ctx) -> str` — /exit(退出;F064,Ctrl-D 等价)

**功能**:请求外壳退出——置退出标志并结束当前会话(finished,reason=user_command_exit);别名 `/quit`;交互会话必需(交互性由 REPL 循环持有,退出前已完成留痕);headless 直接拒(管道喂 /exit 不能静默杀进程)。

```python
async def cmd_exit(args, ctx):
    if ctx.agent is None:                                          # 无生命周期通道
        return "当前外壳不支持 /exit"
    await ctx.agent.finish_session(reason="user_command_exit")     # 终态留痕
    ctx.shell.request_exit(0)                                      # 外壳退出码 0(F064)
    return "再见"                                                   # 回显后外壳即退
```

**参数表**:`args`=可空;`ctx`=门面。**返回**:告别文本。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | headless 下调 /exit | APR-501 | 前置 `_gated` 已拒(管道不静默退出) |
| `PyHError` | finished 重复 | EVT-104 | 查绕过路径;Ctrl-D 双次退出为 F064 正常路径 |

**关联测试**:DEP G5(/quit 退出码 0)、test_f041_commands.py(别名 quit→exit 等价)、F064 交互循环验收。

### `def _gated(entry: CommandEntry, ctx) -> bool` — headless × 状态变更闸(R8/F064)

**功能**:判定命令是否应在当前通道被拒——`danger=="high"` 且 `ctx.channel is None`(headless:管道/后台 job,stdin 非 tty,CFG §3.8 无配置开关)即 True;只读命令(none)任何通道放行。

```python
def _gated(entry, ctx):
    if entry.danger != "high":                                     # 只读命令恒放行
        return False
    if ctx.channel is not None:                                    # 有交互通道(CLI tty/ACP)
        return False
    return True                                                    # headless × high → 拒
```

**参数表**:`entry`=CommandEntry;`ctx`=门面(channel 由外壳装配:tty="cli"/ACP="acp:<id>"/管道=None)。**返回**:bool(True=应拒)。**异常表**:无。

**关联测试**:T-SEC-09 同构场景(headless 下 high 动作直接拒,不挂起不自动同意)、CFG §3.8(R8 无配置开关,由 tty 判定)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.4 F040/F041、§5.6 F064、§6.2 R8 | 命令集/零 LLM/headless 安全默认 |
| EVENT-SCHEMA.md | §3.2 user.command、§6.2 EVT-104/105 | 事件协议:命中即写、未知回显、终态写序 |
| ERR.md | §2.2 EVT-105、§2.8 APR-501、§6.3 排障 | 错误码与处置语义(含 headless 拒绝) |
| DEP.md | §5.2 会话生命周期、§5.3 斜杠命令表 | 命令表/别名/`/new` 语义权威 |
| CFG.md | §3.8 外壳域 R8 | headless 判定(tty)与无配置开关约束 |
| approval.py.md | 通道注入语义 | ctx.channel 装配(tty/ACP/None)同源 |
| scope.py.md | F032 budget_state | /cost 数据源(预算状态) |
