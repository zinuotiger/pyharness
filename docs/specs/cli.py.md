# specs/cli.py.md — 编码规格

> 目标代码:pyharness/cli.py(console_scripts 入口 `pyharness`),PRD F064(CLI 完整外壳)+ F065(desktop 子命令接管原 web 位)+ F060(repair 子命令桥)。DEP §5(子命令总表/旗标/退出码)为调用面权威。AI 编码 Agent 只读本文件即可写出 CLI 全量代码。权威源:PRD-Core.md F064/DEP.md §5/DEP.md §4.7A、ERR.md §1.4(错误只回 code+advice)。全中文,仅 Python,禁 TS。禁止:无码异常裸跨、现场造码、把引擎错误吞成 success、交互下静默执行危险审批(R8 headless 安全默认)。本模块=纯外壳:零业务逻辑,一切动作委托引擎(commands.handle_slash / task_queue / session / repair / config),事件由总线订阅渲染,不直写存储。

## 模块职责
一句话:PyHarness 第一壳——argparse 解析 14 个叶子子命令(chat/run/plan/schedule/job/search/session/fork/repair/desktop/acp/config/budget/stats)与全局旗标,按命令分发到交互循环/单发任务/离线命令/外壳启动,统一退出码(0 成功、1 引擎错误带 F019 码、2 用法错误),headless(stdin 非 tty)默认拒绝无审批通道的危险动作(R8),`--json` 输出机器可读事件流,Ctrl-C 一次取消当前轮、两次退出。

## 依赖
| import | 用途 |
|---|---|
| argparse | 子命令/旗标解析(禁第三方 click/typer——TECH-ANCHOR 不做项);用法错误退出 2 |
| asyncio、signal | 事件循环宿主、Ctrl-C 两段式信号处理 |
| sys | stdin.isatty() 判定 headless、stdout/stderr 分流、exit code |
| pyharness.bootstrap / ctx 装配 | §2.5 启动序:config→guard→budget→session(repair 前置)→外壳注入 channel |
| pyharness.commands(handle_slash) | 斜杠命令分发(F041,零 LLM;未知斜杠 EVT-105 回显会话继续) |
| pyharness.task_queue | run/chat 单发任务:submit→订阅渲染→wait_for 终态 |
| pyharness.session(open_session、events_after) | 历史回放渲染、--session 恢复 |
| pyharness.repair(repair_session、auto_scan) | repair 子命令与启动前自检桥(F060) |
| pyharness.approval(approve/deny/on_verdict) | 交互审批裁决(channel="cli") |
| pyharness.errors(PyHError、raise_code) | 唯一错误出口;跨边界一律带码 |
| pyharness.events(Envelope、register_type) | 渲染事件对象,瞬时事件 llm.chunk 流式打字机 |
| pyharness.bus(EventBus.subscribe) | 订阅 llm.chunk/agent.message/tool.call/guard.*/approval.* 实时渲染 |

## 数据结构表
| 结构 | 字段 | 说明 |
|---|---|---|
| `ParseResult` | cmd:str;positional:list[str];flags:dict(config/json/session/once/verbose) | argparse 产物;json=True 全程 stdout 只出结构化行 |
| `ShellCtx` | ctx:门面;loop:AbstractEventLoop;channel:str\|None | 装配后外壳上下文;channel="cli",headless 时 None |
| `ExitResult` | code:int;payload:dict\|None | 子命令返回;main 转进程退出码 |
| `StreamRenderer` | seen_seq:int;mode:"text"\|"json";carriage:bool | 事件→屏幕/JSONL 渲染器;json 模式不写人类文本到 stdout |
| `CmdTable` | name→(handler, offline:bool, need_session:bool) | 分发表;config/budget/stats 标 offline |

## 类与函数清单

### `def main(argv: list[str] | None = None) -> int` — 进程入口(console_scripts)
功能:包装 cli_main 的事件循环宿主;任何未捕获异常→CYC-999 结构化到 stderr、退出 1;返回进程退出码。
参数表:argv=None 取 sys.argv[1:]。返回:int 退出码。
伪代码:
```python
def main(argv=None):
    try:
        parsed = parse_args(argv if argv is not None else sys.argv[1:])
        return asyncio.run(cli_main(parsed))            # 事件循环只在这里创建
    except KeyboardInterrupt:
        print("\n[interrupted] 已退出", file=sys.stderr); return 130
    except PyHError as e:                                # 引擎错误:带码退出
        _emit_error(e, json=args_json()); return 1
    except Exception as e:                               # 未预期兜底,禁现场造码
        print(f"CYC-999 {e!r}", file=sys.stderr); return 1
```
异常表:KeyboardInterrupt|用户二次 Ctrl-C|130|直退;PyHError|引擎任何域失败|F019 码|打印 code+advice(CYC-999 兜底);Exception|未预期|CYC-999|stderr 堆栈,退出 1。
关联测试:test_f064_cli.py(退出码 0/非 0 断言)、DEP §4.7D 冒烟。

### `def parse_args(argv: list[str]) -> ParseResult` — 子命令分发解析
功能:全局旗标 + 14 子命令各带参数;未知子命令/缺参→用法错误退出 2(argparse 惯例,不造码);--help 列全命令(以本函数为 DEP §5.1"以 --help 为准"的落地)。
参数表:argv。返回:ParseResult。
伪代码:
```python
def parse_args(argv):
    p = argparse.ArgumentParser(prog="pyharness", description="PyHarness Agent 框架")
    g = p.add_argument_group("全局")
    g.add_argument("--config", "-c"); g.add_argument("--json", action="store_true")
    g.add_argument("--session"); g.add_argument("--verbose", "-v", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)   # 缺子命令→退出 2
    def leaf(name, **kw):
        s = sub.add_parser(name, **kw); s.add_argument("args", nargs="*"); return s
    leaf("chat"); leaf("run").add_argument("text", nargs="?")      # run 无 text=headless 读 stdin
    leaf("plan").add_argument("goal"); leaf("schedule"); leaf("job"); leaf("search").add_argument("q")
    leaf("session").add_argument("sid", nargs="?"); leaf("fork").add_argument("sid")
    leaf("repair"); leaf("desktop"); leaf("acp"); leaf("config"); leaf("budget"); leaf("stats")
    ns = p.parse_args(argv)
    return ParseResult(cmd=ns.cmd, positional=ns.args, flags=vars(ns))
```
异常表:SystemExit(2)|用法错误|无码(argparse 惯例)|--help 提示;args 过少同上。
关联测试:test_f064_cli.py(每子命令可解析)、DEP §5.1 总表逐行对齐。

### `async def cli_main(r: ParseResult) -> int` — 分发总控(F064 验收直译)
功能:offline 命令(config/budget/stats)不装配引擎直接跑;其余按子命令进 bootstrap 后分发;desktop/acp 为长驻外壳交出其控制权。
参数表:r=ParseResult。返回:int 退出码。
伪代码:
```python
async def cli_main(r):
    if r.cmd in {"config", "budget", "stats"}:          # 离线命令:零网络零 LLM(DEP §5.1)
        return offline_cmd(r.cmd, r.flags)
    sh = await bootstrap_shell(r)                        # §2.5 启动序 + repair 前置
    if r.cmd == "chat":  return await interactive_loop(sh, once=sh.flags.get("once"))
    if r.cmd == "run":   return await run_intent(sh, text_or_stdin(sh), headless=sh.headless)
    if r.cmd == "repair": return await repair_cmd(sh, r.flags.get("session"))   # F060 桥
    if r.cmd == "desktop":
        from pyharness.desktop import run_desktop; return await run_desktop(sh.ctx)  # F065 交权
    if r.cmd == "acp":
        from pyharness.acp import AcpBridge; return await AcpBridge(sh.ctx, channel=f"acp:cli").serve()
    # plan/schedule/job/search/session/fork:统一"装配+执行+渲染"单发路径
    return await run_once_sub(sh, r.cmd, r.positional, r.flags)
```
异常表:PyHError|bootstrap 失败(CFG-601/LLM-310)|原码透传|打印 code+advice 退 1;EVT-106|先于 created|EVT-106|提示先 chat/run 建会话。
关联测试:test_f064_cli.py(全子命令分发命中)、DEP G1(config 离线)。

### `async def bootstrap_shell(r: ParseResult) -> ShellCtx` — 启动序 + headless 判定
功能:装配门面(§2.5:config→guard→budget→storage→session);repair 前置:目标会话(或自动扫描命中者)先过 auto_scan,有损坏→交互/自动 repair(F060);channel 注入:stdin.isatty() 且 cmd∈{chat,plan} → "cli",否则 None(headless)。
参数表:r。返回:ShellCtx(headless=channel is None)。
伪代码:
```python
async def bootstrap_shell(r):
    cfg = load_config(r.flags.get("config"))             # CFG-601 失败即中止,不装配
    ctx = assemble_ctx(cfg)                              # 脊柱八模块 + 外围挂 ctx.*
    sid = r.flags.get("session")
    if not sid and r.cmd in {"chat", "run"}:
        health = await auto_scan(ctx.storage.sessions_dir)   # 启动自检(F060)
        sid = pick_first_unhealthy(health) or None       # 命中→repair 子流程处理
    if sid:
        await ensure_repair(ctx, sid)                    # 损坏则先 repair 再 open(幂等)
        ctx = await ctx.session.open_session(sid)        # 重放重建;EVT-106 守卫
    headless = not (sys.stdin.isatty() and r.cmd in {"chat", "plan", "interactive"})
    ctx.channel = None if headless else "cli"
    return ShellCtx(ctx=ctx, loop=asyncio.get_running_loop(), channel=ctx.channel,
                    headless=headless, flags=r.flags)
```
异常表:PyHError|配置非法/装配失败|CFG-601|列出字段,拒绝启动(HARDENED 无回退);PERS-201|坏行且 repair 拒绝修复|PERS-201|建议手工处理隔离区。
关联测试:test_f064_cli.py(headless 判定)、test_f060_repair.py(启动自检联动)。

### `async def interactive_loop(sh: ShellCtx, *, once: bool = False) -> int` — chat 交互主循环
功能:提示符循环:多行输入(`\` 续行)、斜杠命令交 handle_slash(零 LLM,false=退出)、普通文本交 task_queue.submit 并流式渲染、审批交互弹提示;Ctrl-C 一次取消当前轮、两次退出;once=True 单轮即退(--once 非交互注入,headless 用)。
参数表:sh;once=单轮模式。返回:int(0 正常 / 130 中断)。
伪代码:
```python
async def interactive_loop(sh, once=False):
    renderer = StreamRenderer(mode="json" if sh.flags.get("json") else "text")
    sig = install_two_stage_sigint(sh)                   # 第 1 次取消当前轮;第 2 次 SystemExit
    try:
        while True:
            raw = await read_user_input(sh, once=once)   # EOF(Ctrl-D)→退出;None→退出
            if raw is None: return 0
            if raw.startswith("/"):                      # 斜杠:不给 LLM 的控制通道(F041)
                cont = await commands.handle_slash(raw, sh.ctx)   # /quit→False;未知→EVT-105 回显
                if not cont: return 0
                continue
            task_id = await sh.ctx.task_queue.submit(raw, meta={"channel": sh.ctx.channel or "headless"})
            await sh.ctx.task_queue.wait_for(task_id)    # 渲染交给订阅回调(见 _render_events)
    except _CancelledRound:
        print("\n[已取消当前轮,再按 Ctrl-C 退出]", file=sys.stderr); return await interactive_loop(sh, once)
```
异常表:KeyboardInterrupt(1 次)|当前轮取消|无码|返回循环顶部;SystemExit(2 次)|退出|130|进程结束;PyHError|队列满等|QUE-001|回显码后回循环。
关联测试:test_f064_cli.py(交互冒烟/斜杠/两次退出)、test_f041_commands.py(handle_slash 联动)。

### `async def read_user_input(sh, *, once: bool) -> str | None` — 多行读取 + once 直通
功能:非 tty 时从 stdin 整读(管道喂词,headless run 语义);tty 时逐行,行尾 `\` 续行拼接,空输入重提示;EOF 返回 None;once 且带 --session 时返回 --once 文本。
参数表:sh;once。返回:str 或 None(EOF/退出)。
伪代码:
```python
async def read_user_input(sh, once=False):
    if once:
        txt = sh.flags.get("once") or sys.stdin.read()   # --once "继续" 或管道全文
        return txt.strip() or None
    if not sys.stdin.isatty(): return sys.stdin.read().strip() or None   # headless 一次性消费
    lines = []
    while True:
        line = await asyncio.to_thread(input, PROMPT)    # 阻塞读走线程,不卡事件循环
        if not line and not lines: return None if eof_marker() else ""
        if line.endswith("\\"): lines.append(line[:-1]); continue   # 续行
        lines.append(line); return "\n".join(lines).strip()
```
异常表:EOFError|Ctrl-D|无码|返回 None 退出;KeyboardInterrupt|Ctrl-C|130|向上冒泡两段式。
关联测试:test_f064_cli.py(管道输入/--once)。

### `async def run_intent(sh, text: str, *, headless: bool) -> int` — run 单发任务
功能:无人值守单轮:submit→阻塞至终态→报告结果/拒绝清单;headless 下 channel=None 使审批自动拒(APR-501)与 critical 直拒(GRD-401)天然生效(R8)。
参数表:sh;text;headless。返回:int 0/1。
伪代码:
```python
async def run_intent(sh, text, *, headless):
    if not text: raise_code("EVT-100", ctx={"advice": "run 需要任务文本(stdin 或参数)"})
    sh.ctx.channel = None if headless else "cli"         # 无审批通道即安全默认(R8/APR-501)
    task_id = await sh.ctx.task_queue.submit(text, meta={"channel": sh.ctx.channel})
    res = await sh.ctx.task_queue.wait_for(task_id)
    rejected = res.events_of("guard.rejected")           # 报告含"拒绝"明细(DEP G4 断言)
    if sh.flags.get("json"):
        print(json.dumps({"task_id": task_id, "result": res.summary(), "rejected": rejected}))
    else:
        print(res.summary()); [print(f"[拒绝] {r}") for r in rejected]
    return 0 if res.reason in {"complete", "cancelled_by_user"} else 1
```
异常表:LLM-310|降级链全败|LLM-310|终态 reason=error,退 1;GRD-401|critical 直拒|GRD-401|计入拒绝清单不退出(0 内);PERS-202|落盘故障|PERS-202|建议 repair。
关联测试:test_f064_cli.py(全子命令)、DEP G4(headless 危险默认拒绝→报告含"拒绝",工作区零副作用 INV-05)。

### `def offline_cmd(cmd: str, flags: dict) -> int` — 离线子命令(config/budget/stats)
功能:不装配引擎:config init/validate/show(DEP §5.1 离线列 ✅);budget 输出月度/任务预算报表;stats 输出会话统计;秘密只回显 env:/file: 引用(无 --show-secrets)。
参数表:cmd;flags。返回:int(config validate 失败退 1)。
伪代码:
```python
def offline_cmd(cmd, flags):
    if cmd == "config":
        sub = (flags.get("positional") or ["show"])[0]   # init/validate/show
        if sub == "validate":
            errs = config.validate()                     # 非法→列字段明细(TC-G2)
            if errs: print("\n".join(errs)); return 1
            print("ok"); return 0
        if sub == "show": print(config.show_json() if flags.get("json") else config.show_text()); return 0
        if sub == "init": config.init_default(); return 0
    if cmd == "budget": return print_budget_report(config.budget_state())   # 离线(F032 数据)
    if cmd == "stats":  return print_session_stats(scan_stats_dir())        # 只读派生
    return 2
```
异常表:PyHError|YAML 解析/越权|CFG-601|列字段退 1(TC-G2);FileNotFoundError|无配置且非 init|CFG-601|提示 config init。
关联测试:DEP G1(config init/validate/show 无 key 全成功)、TC-G2(config validate 退出非 0 同明细)。

### `async def repair_cmd(sh, sid: str | None) -> int` — repair 子命令桥(F060)
功能:对 sid(缺省=最近损坏会话)执行修复管线:备份 `.corrupt-{ts}`→截断→隔离→空洞→重建→recovered;交互确认删留;输出修复报告;不可修复→明确报错+备份留存。
参数表:sh;sid。返回:int 0 修复/无损坏,1 不可修复。
伪代码:
```python
async def repair_cmd(sh, sid=None):
    from pyharness.repair import repair_session, auto_scan
    if not sid:
        hits = await auto_scan(sh.ctx.storage.sessions_dir)     # 无 sid:扫全部
        sid = hits[0].sid if hits else None
    if not sid: print("[repair] 无损坏会话"); return 0
    interactive = sys.stdin.isatty()
    report = await repair_session(sh.ctx, sid, interactive=interactive)
    if sh.flags.get("json"): print(json.dumps(report.asdict()))
    else:
        print(f"[repair] {sid}: fixed={report.fixed} lost={report.lost} "
              f"backup={report.backup_path} quarantined={report.quarantined}")
    return 0
```
异常表:PyHError|不可修复损坏|PERS-201|报错+原文件已备 `.corrupt-{ts}`;PERS-202|备份/写失败|PERS-202|原文件未动,修复中止。
关联测试:test_f060_repair.py(截断/幂等/备份)、DEP G6(备份覆盖后二次 repair 无 lost)。

### `async def _render_events(sh, task_id: str) -> None` — 事件流渲染(流式打字机)
功能:订阅总线 llm.chunk(瞬时)/agent.message/tool.call/guard.rejected/approval.*;text 模式增量打印、工具调用与 guard 拦截成卡片,approval 触发交互提示;json 模式逐事件一行 JSONL 到 stdout。
参数表:sh;task_id。返回:None。
伪代码:
```python
async def _render_events(sh, task_id):
    q: asyncio.Queue = asyncio.Queue()
    sub = sh.ctx.bus.subscribe(f"task:{task_id}:#", lambda ev: q.put_nowait(ev), owner="cli")
    try:
        while True:
            ev = await q.get()
            if ev.type == "llm.chunk":           # 瞬时事件:打字机增量(不落日志,EVENT-SCHEMA §1.3)
                write_text(ev.payload["delta"], carriage=True)
            elif ev.type in {"agent.message", "tool.call", "tool.result", "guard.rejected"}:
                write_card(ev)                   # text: 卡片; json: json.dumps(ev.envelope)
            elif ev.type.startswith("approval."): await prompt_approval(sh, ev)
            elif ev.type == "session.finished": break
    finally:
        sh.ctx.bus.unsubscribe_all("cli")
```
异常表:EVT-103|订阅者异常|EVT-103|隔离该订阅不中断;无码异常|渲染器 bug|CYC-999|stderr 后继续(不吞引擎)。
关联测试:test_f064_cli.py(--json 输出形状)、DEP §4.3.1(流式打字机演示)。

### `async def prompt_approval(sh, ev) -> None` — 审批交互(F015 人类裁决)
功能:approval.requested 到达且 channel 非 None→打印工具/参数摘要与风险级,键盘 [y/N/t(超时)] 裁决→approval.approve/deny;headless(channel=None)不提示,裁决侧自动拒(APR-501,零等待)。
参数表:sh;ev=approval.requested。返回:None。
伪代码:
```python
async def prompt_approval(sh, ev):
    if sh.headless: return                                # 无通道即拒:裁决侧已 APR-501,勿悬挂
    aid = ev.payload["approval_id"]
    print(f"\n[审批] {ev.payload['tool']} {ev.payload['args_summary']} "
          f"(risk={ev.payload['risk']}, ttl={ev.payload['ttl_ms']}ms)")
    ans = (await asyncio.to_thread(input, "批准? [y/N] ")).strip().lower()
    if ans in {"y", "yes"}:
        await sh.ctx.approval.approve(aid, by="cli")      # 写 approval.granted(强同步)
    else:
        await sh.ctx.approval.deny(aid, by="cli")         # 拒绝终局,零副作用
```
异常表:APR-503|裁决重放/未知 id|APR-503|system.error 忽略;APR-502|等待被取消|APR-502|按拒绝处理;KeyboardInterrupt|用户中止|130|等价 deny 冒泡。
关联测试:test_f015_approval.py 联动(裁决入日志)、test_f064_cli.py(交互审批冒烟)。

### `def install_two_stage_sigint(sh) -> None` / `def exit_code_for(e: PyHError) -> int` — 信号与码映射
功能:首次 SIGINT 置 _CancelledRound 取消当前轮并恢复默认;二次直退 130(Windows 同语义,禁 signal.SIGKILL);exit_code_for 把引擎码映射为进程码(码域内一律 1,用法 2,中断 130)。
参数表:sh / e。返回:None / int。
伪代码:
```python
def install_two_stage_sigint(sh):
    state = {"armed": False}
    def handler(signum, frame):
        if not state["armed"]:
            state["armed"] = True; print("\n[Ctrl-C] 再按一次退出(取消当前轮)", file=sys.stderr)
            raise _CancelledRound()
        raise SystemExit(130)
    signal.signal(signal.SIGINT, handler); return state

def exit_code_for(e):
    return 130 if isinstance(e, KeyboardInterrupt) else 1   # 用法错误由 argparse 退 2,此处不覆盖
```
异常表:_CancelledRound|首次 Ctrl-C|无码|interactive_loop 捕获回顶部;SystemExit|二次 Ctrl-C|130|进程退出。
关联测试:test_f064_cli.py(Ctrl-C 语义)、DEP §5.2(Ctrl-C 一次取消两次退出)。

## 关联文档
1. PRD-Core.md §5.7 F064(全子命令/headless/--json/Ctrl-C)+ F060(repair 桥)+ F065(desktop 子命令为桌面壳入口)。
2. DEP.md §5(子命令总表/旗标/退出码契约)、§4.2-§4.7(演示台本)、DEP G1/G4/G6 验收场景。
3. ERR.md §1.4/§2(错误码跨边界契约:CLI 不持自有码域,外壳失败复用底层码;未知→CYC-999)。
4. EVENT-SCHEMA.md §1.3(瞬时事件 llm.chunk 渲染依据)、§3(approval.*/guard.* 渲染字段)。
5. specs/commands.py.md(F041 斜杠面,DEP §5.3 对齐)、specs/repair.py.md、specs/desktop.py.md、specs/acp.py.md(同批外壳)。
6. ADD.md ADR-005(单进程:CLI 只作装配者无第二状态)、ADR-011(/api 与 CLI 同码契约)。
