"""pyharness/core/commands.py 单测 — 契约:specs/commands.py.md(F041 权威)+ EVENT-SCHEMA
§3.2.5(user.command 命中即写/不产生 user.message)+ DEP §5.3(命令表/别名)+ ERR.md
(EVT-105/APR-501)+ CFG.md §3.8(R8 headless 由通道判定)。

覆盖面(任务要求 + 规格模块级测试):
    命中落 user.command(先落盘再分发,EVT-104 安全序;不产生 user.message)
    未知命令 → system.error(EVT-105),会话继续,不写 user.command,零 LLM
    headless(channel=None)× danger=high → system.error(APR-501) 直接拒:
        零执行(agent 未触碰)、零副作用(会话未终态)、不写 user.command;只读恒放行
    命令分发:参数透传、别名(quit→exit / budget→cost)、大小写归一、注册序 help
    零 LLM:任意斜杠命令后会话 llm.* 事件数不增(DEP G5)
    注册表扩展:register_command 成功/重名拒/非法名拒/非法 danger 拒
    /help(全量列表 + 单条 + 别名归一)、/status(todo.updated 派生 + 标题)、
    /cost(llm.usage 聚合 + 硬闸 warn 80% + 未装配降级)
    /new(旧会话 finished + 新会话 seq 从 1,user.command 先于 finished)
    /exit(别名 /quit;finish_session reason + request_exit(0);再见)

装配与 test_approval 同风格:真实 SessionLog(纯内存,无总线)= append 五步校验链全真;
ctx 为 SimpleNamespace 门面替身(外壳装配注入面 = session/channel/render/budget/
agent/shell,与 specs/cli.py.md ShellCtx 同构);asyncio_mode=auto。
"""
import types

import pytest

from pyharness.core import commands
from pyharness.core.session import SessionLog

SID = "s-cmd-0001"        # 会话 id(Envelope session_id min_length=8)
SID2 = "s-cmd-0002"
MODEL = "deepseek-chat"


# ===================================================================== 替身
class _FakeAgent:
    """cmd_new/cmd_exit 的生命周期替身:finish 落真 session.finished;new 换 ctx.session。

    模拟外壳装配语义:finish_session 委托会话终态(user_command_* 落真日志);
    new_session 开新 SessionLog(纯内存)并重装 ctx.session(外壳 reassemble)。
    """

    def __init__(self, log: SessionLog, ns: types.SimpleNamespace) -> None:
        self.log = log
        self.ns = ns
        self.finished: list[str] = []
        self.started: list[str] = []

    async def finish_session(self, reason: str) -> None:
        self.finished.append(reason)
        await self.log.append("session.finished", {"reason": reason},
                              actor="system", sync=True)

    async def new_session(self) -> None:
        log2 = SessionLog(sid=SID2)
        await log2.append("session.created", {"title": "", "model": MODEL},
                          actor="system")
        self.ns.session = log2           # 外壳重装:ctx.session 指向新会话
        self.started.append(SID2)


async def boot(log: SessionLog, *, title: str = "") -> None:
    """会话引导:session.created(seq=1;此后校验链 EVT-106 开闸)。"""
    await log.append("session.created", {"title": title, "model": MODEL},
                     actor="system")


def _ctx(log: SessionLog, *, channel: str = "cli", budget=None, agent=None,
         shell=None, renderer=None) -> types.SimpleNamespace:
    """handle_slash 的 ctx 门面替身:装配注入面 = session/channel/render/budget/agent/shell。"""
    return types.SimpleNamespace(session=log, channel=channel, budget=budget,
                                 agent=agent, shell=shell, render=renderer)


def _by_type(log: SessionLog, type_: str) -> list:
    return [e for e in log.events_after(0) if e.type == type_]


def _types(log: SessionLog) -> list:
    return [e.type for e in log.events_after(0)]


def _payloads(log: SessionLog, type_: str) -> list:
    return [e.payload for e in log.events_after(0) if e.type == type_]


# ================================================================= 命中落事件
async def test_hit_records_user_command_before_dispatch_and_renders():
    """命中 → 先落 user.command(actor=user,含 args)再调 handler;无 user.message。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    ctx = _ctx(log, renderer=rendered.append)

    ok = await commands.handle_slash("/status", ctx)

    assert ok is True                       # 命令输入永不落 user.message
    assert _types(log) == ["session.created", "user.command"]
    (cmd,) = _payloads(log, "user.command")
    assert cmd["name"] == "status" and cmd["args"] == ""
    assert _by_type(log, "user.message") == []
    assert len(rendered) == 1               # handler 回显经 ctx.render
    assert "会话" in rendered[0] and "seq 至" in rendered[0]


async def test_hit_records_args_verbatim():
    """user.command.args = 参数原文(strip 后),原样透传 handler。"""
    log = SessionLog(sid=SID)
    await boot(log)
    seen: dict = {}
    rendered: list[str] = []

    async def _fake(args, ctx):             # 测试替身命令:回显参数
        seen["args"] = args
        return f"arg=<{args}>"

    commands.register_command("tprobe", _fake, danger="none",
                              help_text="测试探针")
    try:
        ctx = _ctx(log, renderer=rendered.append)
        ok = await commands.handle_slash("/tprobe   归档 D:\\work ", ctx)
    finally:
        commands.COMMANDS.pop("tprobe", None)

    assert ok is True
    assert seen["args"] == "归档 D:\\work"
    (cmd,) = _payloads(log, "user.command")
    assert cmd["name"] == "tprobe" and cmd["args"] == "归档 D:\\work"
    assert rendered == ["arg=<归档 D:\\work>"]


# ================================================================= 未知命令
async def test_unknown_command_evt105_session_continues():
    """未知 → system.error(EVT-105) 留痕,会话继续;不写 user.command、零渲染。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    ctx = _ctx(log, renderer=rendered.append)

    ok = await commands.handle_slash("/foobar --x", ctx)

    assert ok is True
    assert _by_type(log, "user.command") == []          # 未命中不写
    (err,) = _payloads(log, "system.error")
    assert err["code"] == "EVT-105"
    assert "foobar" in err["hint"] and "/help" in err["hint"]   # hint 引导 /help
    assert rendered == []                               # 回显走事件面
    assert log.stats()["closed"] is False               # 会话继续


async def test_unknown_command_zero_llm_after_any_slash():
    """零 LLM 硬保证:任意斜杠命令后会话 llm.* 事件数不增(DEP G5)。"""
    log = SessionLog(sid=SID)
    await boot(log)
    ctx = _ctx(log)
    for raw in ("/status", "/help", "/cost", "/wobble", "/",
                "/QUIT"):                                # 含未知/别名/裸斜杠
        ok = await commands.handle_slash(raw, ctx)
        assert ok is True
    llm_types = [t for t in _types(log) if t.startswith("llm.")]
    assert llm_types == []                              # 零 llm.request(零 LLM)
    # 仅 created + 命中命令的 user.command + 未知的 system.error(EVT-105)
    assert _by_type(log, "user.message") == []


# ============================================================= headless 拒
async def test_headless_gates_state_change_commands_apr501():
    """/new 在 headless(channel=None)→ system.error(APR-501):零执行零事件。"""
    log = SessionLog(sid=SID)
    await boot(log)
    agent = _FakeAgent(log, types.SimpleNamespace())    # 未触碰即零执行证据
    ctx = _ctx(log, channel=None, agent=agent)

    ok = await commands.handle_slash("/new", ctx)

    assert ok is True
    (err,) = _payloads(log, "system.error")
    assert err["code"] == "APR-501"
    assert "headless" in err["hint"] and "/new" in err["hint"]
    assert _by_type(log, "user.command") == []          # 拒绝零执行性事件
    assert _by_type(log, "session.finished") == []      # 零副作用:未终态
    assert agent.finished == [] and agent.started == []  # agent 未触碰


async def test_headless_gates_exit_too():
    """/quit 在 headless → APR-501 拒:不退出、不终态、不落 user.command。"""
    log = SessionLog(sid=SID)
    await boot(log)
    agent = _FakeAgent(log, types.SimpleNamespace())
    shell = types.SimpleNamespace(exits=[])

    def _exit(code): shell.exits.append(code)
    shell.request_exit = _exit
    ctx = _ctx(log, channel=None, agent=agent, shell=shell)

    ok = await commands.handle_slash("/quit", ctx)

    assert ok is True
    (err,) = _payloads(log, "system.error")
    assert err["code"] == "APR-501"
    assert shell.exits == []                            # 管道不能静默杀进程
    assert agent.finished == []
    assert _by_type(log, "user.command") == []


async def test_headless_readonly_commands_allowed():
    """headless 下只读命令(help/status/cost)恒放行(R8 语义:脚本可查不可改)。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    ctx = _ctx(log, channel=None, renderer=rendered.append)

    for raw in ("/help", "/status", "/cost"):
        ok = await commands.handle_slash(raw, ctx)
        assert ok is True
    assert _by_type(log, "system.error") == []
    assert len(rendered) == 3


# ============================================================= 分发与别名
async def test_aliases_dispatch_to_canonical():
    """别名 quit→exit / budget→cost:user.command 记录规范主名,行为等价。"""
    log = SessionLog(sid=SID)
    await boot(log)
    agent = _FakeAgent(log, types.SimpleNamespace())
    shell = types.SimpleNamespace(exits=[])

    def _exit(code): shell.exits.append(code)
    shell.request_exit = _exit
    ctx = _ctx(log, agent=agent, shell=shell)

    ok = await commands.handle_slash("/quit", ctx)
    assert ok is True
    (cmd,) = _payloads(log, "user.command")
    assert cmd["name"] == "exit"                        # 规范主名落盘
    assert agent.finished == ["user_command_exit"]
    assert shell.exits == [0]                           # 退出码 0(F064)

    # budget → cost 输出与 /cost 逐字一致
    log2 = SessionLog(sid=SID)
    await boot(log2)
    a_rendered: list[str] = []
    b_rendered: list[str] = []
    await commands.handle_slash("/budget", _ctx(log2, renderer=a_rendered.append))
    await commands.handle_slash("/cost", _ctx(log2, renderer=b_rendered.append))
    assert a_rendered == b_rendered
    assert _payloads(log2, "user.command")[0]["name"] == "cost"


async def test_case_insensitive_command_names():
    """/HELP 与 /help 等价(名称小写归一;别名同样不区分大小写)。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    ctx = _ctx(log, renderer=rendered.append)
    ok = await commands.handle_slash("/HELP", ctx)
    assert ok is True
    (cmd,) = _payloads(log, "user.command")
    assert cmd["name"] == "help"
    assert len(rendered) == 1 and "/status" in rendered[0]


# ============================================================= 注册表扩展
async def test_register_command_and_dispatch():
    """register_command 成功注册后可分发(阶段 4 运行时挂载,INV-08)。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    seen: list[tuple] = []

    async def _handler(args, ctx):
        seen.append((args, ctx.session.sid))
        return f"plan:{args}"

    commands.register_command("tphase4", _handler, danger="none",
                              help_text="阶段4扩展探针")
    try:
        ok = await commands.handle_slash("/tphase4 step1", _ctx(log, renderer=rendered.append))
    finally:
        commands.COMMANDS.pop("tphase4", None)

    assert ok is True
    assert seen == [("step1", SID)]
    assert rendered == ["plan:step1"]
    assert _payloads(log, "user.command")[0]["name"] == "tphase4"


def test_register_duplicate_rejected():
    """重名(含占用别名)注册 → ValueError,注册表不被污染(装配期早失败)。"""
    before = list(commands.COMMANDS)
    with pytest.raises(ValueError, match="already registered"):
        commands.register_command("help", lambda *a: "", help_text="x")
    with pytest.raises(ValueError, match="already registered"):
        commands.register_command("quit", lambda *a: "", help_text="x")
    assert list(commands.COMMANDS) == before


def test_register_illegal_name_rejected():
    """非法命令名(非 [a-z][a-z0-9]*)→ ValueError,零插入。"""
    before = list(commands.COMMANDS)
    for bad in ("9bad", "Bad", "with_underscore", "with-dash", "has space", ""):
        with pytest.raises(ValueError, match="illegal command name"):
            commands.register_command(bad, lambda *a: "", help_text="x")
    assert list(commands.COMMANDS) == before


def test_register_illegal_danger_rejected():
    """danger ∉ {none, high}(命令面无 critical)→ ValueError,零插入。"""
    before = list(commands.COMMANDS)

    async def _h(args, ctx):
        return ""
    with pytest.raises(ValueError, match="danger must be none|high"):
        commands.register_command("tcritical", _h, danger="critical",
                                  help_text="x")
    assert "tcritical" not in commands.COMMANDS
    assert list(commands.COMMANDS) == before


# ================================================================= /help
async def test_help_lists_all_builtin_commands():
    """/help 全量:注册序输出全部内置命令(名 + 作用)≤40 行,含别名注记。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    await commands.handle_slash("/help", _ctx(log, renderer=rendered.append))
    text = rendered[0]
    lines = text.splitlines()
    assert 1 <= len(lines) <= 40
    names = [e.name for e in commands.COMMANDS.values()]
    for name in names:
        assert any(line.startswith(f"/{name} ") for line in lines)
    assert any("别名: /budget=/cost, /quit=/exit" in line for line in lines)
    assert any("EVT-105" in line for line in lines)     # 零 LLM/未知提示
    # 注册序 = 表序:help/status/cost/new/exit
    order = [line.split()[0].lstrip("/") for line in lines
             if line and not line.startswith(("别名", "未知"))]
    assert order == ["help", "status", "cost", "new", "exit"]


async def test_help_single_command_and_alias_normalized():
    """/help <name> 单条详情;别名预算归一(budget→cost);未知名纯文本提示。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []

    async def _ask(raw):
        rendered.clear()
        await commands.handle_slash(raw, _ctx(log, renderer=rendered.append))
        return rendered[0]

    single = await _ask("/help new")
    assert single == "/new — 结束当前会话并开新(日志留档)"
    alias = await _ask("/help budget")
    assert alias.startswith("/cost — ")                # 别名归一查主命令
    missing = await _ask("/help nope")
    assert "未知命令 /nope" in missing and "/help" in missing


# =============================================================== /status
async def test_status_derives_from_events():
    """/status 全事件派生:标题(created/renamed)+ seq + todo.updated 最新态。"""
    log = SessionLog(sid=SID)
    await boot(log, title="归档整理")
    await log.append("session.renamed", {"new_title": "桌面归档", "by": "auto"},
                     actor="system")
    await log.append("todo.updated",
                     {"task_id": "t-1", "todos": [
                         {"id": 1, "text": "扫描", "done": True},
                         {"id": 2, "text": "归类", "done": False},
                         {"id": 3, "text": "报告", "done": False},
                     ]}, actor="agent")
    budget = types.SimpleNamespace(state="ok")
    rendered: list[str] = []
    await commands.handle_slash("/status", _ctx(log, budget=budget,
                                                renderer=rendered.append))
    text = rendered[0]
    assert f"会话 {SID}" in text
    assert "桌面归档" in text                           # renamed 覆盖 created
    assert f"seq 至 {log.stats()['seq']}" in text
    assert "运行态: ok" in text
    assert "任务进度(F040): 2 项未完成" in text           # done 项排除
    assert "  [2] 归类" in text and "  [3] 报告" in text
    assert "  [1] 扫描" not in text


async def test_status_latest_todo_snapshot_and_no_budget():
    """todo.updated 取每任务最新快照;未装配 budget → 运行态 n/a;无 todo 提示。"""
    log = SessionLog(sid=SID)
    await boot(log)
    await log.append("todo.updated", {"task_id": "t-1", "todos": [
        {"id": 1, "text": "旧", "done": False},
    ]}, actor="agent")
    await log.append("todo.updated", {"task_id": "t-1", "todos": [
        {"id": 1, "text": "新", "done": False},
        {"id": 2, "text": "收尾", "done": True},
    ]}, actor="agent")
    rendered: list[str] = []
    await commands.handle_slash("/status", _ctx(log, renderer=rendered.append))
    text = rendered[0]
    assert "运行态: n/a" in text
    assert "1 项未完成" in text
    assert "  [1] 新" in text and "  [2] 收尾" not in text
    assert "旧" not in text                             # 旧快照被最新覆盖

    log2 = SessionLog(sid=SID)
    await boot(log2)
    rendered.clear()
    await commands.handle_slash("/status", _ctx(log2, renderer=rendered.append))
    assert "任务进度: 当前无未完成 todo 项" in rendered[0]


# =================================================================== /cost
async def test_cost_aggregates_usage_and_budget_gate():
    """/cost 聚合 llm.usage(cost_est 累加);硬闸百分比 + warn 80% 标记。"""
    log = SessionLog(sid=SID)
    await boot(log)
    for ev in ({"model": MODEL, "in_tokens": 600, "out_tokens": 300,
                "cost_est": 0.12},
               {"model": MODEL, "in_tokens": 400, "out_tokens": 200,
                "cost_est": 0.18}):
        await log.append("llm.usage", ev, actor="llm")
    budget = types.SimpleNamespace(state="warn", limit_cny=1.0, used_cny=0.9)
    rendered: list[str] = []
    await commands.handle_slash("/cost", _ctx(log, budget=budget,
                                              renderer=rendered.append))
    text = rendered[0]
    assert "in=1.0k" in text and "out=0.5k" in text
    assert "估算 0.30 元" in text
    assert "硬闸 1.00 元(90%)" in text
    assert "⚠warn 80%" in text                          # pct≥80 提示


async def test_cost_without_budget_degrades_and_no_warn_below_80():
    """预算闸未装配 → 只输出用量段;pct<80 → 无 warn 标记。"""
    log = SessionLog(sid=SID)
    await boot(log)
    await log.append("llm.usage", {"model": MODEL, "in_tokens": 1000,
                                   "out_tokens": 1000, "cost_est": 0.4},
                     actor="llm")
    rendered: list[str] = []
    await commands.handle_slash("/cost", _ctx(log, renderer=rendered.append))
    assert "硬闸" not in rendered[0]                     # 降级:无预算段

    budget = types.SimpleNamespace(state="ok", limit_cny=10.0, used_cny=1.0)
    rendered.clear()
    await commands.handle_slash("/cost", _ctx(log, budget=budget,
                                              renderer=rendered.append))
    text = rendered[0]
    assert "硬闸 10.00 元(10%)" in text
    assert "warn" not in text                            # pct<80 无告警


# ==================================================================== /new
async def test_new_finishes_old_and_starts_fresh_seq1():
    """/new:user.command 先于 session.finished(EVT-104 安全序);新会话 seq 从 1。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    ctx = _ctx(log, channel="cli", renderer=rendered.append)
    agent = _FakeAgent(log, ctx)          # new_session 重装的就是 handle_slash 的 ctx.session
    ctx.agent = agent

    ok = await commands.handle_slash("/new", ctx)

    assert ok is True
    # EVT-104 安全序:user.command(seq2)落盘后才可能落 finished(seq3)
    events = list(log.events_after(0))
    seqs = {e.type: e.seq for e in events}
    assert seqs["user.command"] < seqs["session.finished"]
    (cmd,) = _payloads(log, "user.command")
    assert cmd["name"] == "new"
    (fin,) = _payloads(log, "session.finished")
    assert fin["reason"] == "user_command_new"
    assert agent.finished == ["user_command_new"]
    assert agent.started == [SID2]
    assert rendered[0].startswith(f"已结束会话 {SID} 并开启新会话 {SID2}")
    # 新会话 ctx.session 已重装且 seq 从 1(created 为唯一事件)
    assert ctx.session.sid == SID2
    assert [e.type for e in ctx.session.events_after(0)] == ["session.created"]


async def test_new_without_agent_degrades():
    """/new 无生命周期通道(ctx.agent=None,理论不可达)→ 降级文本,零副作用。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    ok = await commands.handle_slash("/new", _ctx(log, renderer=rendered.append))
    assert ok is True
    assert rendered[0] == "当前外壳不支持 /new"
    assert _by_type(log, "session.finished") == []


# ==================================================================== /exit
async def test_exit_finishes_session_and_requests_shell_exit():
    """/exit:finish_session(reason=user_command_exit)+ request_exit(0)+ 再见。"""
    log = SessionLog(sid=SID)
    await boot(log)
    ns = types.SimpleNamespace(session=log, channel="cli")
    agent = _FakeAgent(log, ns)
    shell = types.SimpleNamespace(exits=[])

    def _exit(code): shell.exits.append(code)
    shell.request_exit = _exit
    rendered: list[str] = []
    ok = await commands.handle_slash("/exit", _ctx(log, agent=agent, shell=shell,
                                                   renderer=rendered.append))

    assert ok is True
    events = list(log.events_after(0))
    seqs = {e.type: e.seq for e in events}
    assert seqs["user.command"] < seqs["session.finished"]   # 留痕先于终态
    (cmd,) = _payloads(log, "user.command")
    assert cmd["name"] == "exit"
    assert _payloads(log, "session.finished")[0]["reason"] == "user_command_exit"
    assert agent.finished == ["user_command_exit"]
    assert shell.exits == [0]                       # 外壳退出码 0(F064)
    assert rendered == ["再见"]
    assert _by_type(log, "user.message") == []      # 命令不产生 user.message


# ============================================================ handler 兜底
async def test_handler_failure_echoed_session_survives():
    """handler 内部 PyHError → 兜底回显(带码),会话继续、不传播。"""
    log = SessionLog(sid=SID)
    await boot(log)

    async def _boom(args, ctx):
        from pyharness.errors import raise_code
        raise_code("PERS-202", hint="模拟落盘失败")

    commands.register_command("tboom", _boom, danger="none", help_text="炸弹")
    try:
        rendered: list[str] = []
        ok = await commands.handle_slash("/tboom", _ctx(log, renderer=rendered.append))
    finally:
        commands.COMMANDS.pop("tboom", None)

    assert ok is True
    assert "PERS-202" in rendered[0]
    assert "执行失败" in rendered[0]
    assert log.stats()["closed"] is False           # 会话不崩


async def test_handler_unexpected_exception_wrapped_cyc999():
    """handler 非 PyHError 未预期异常 → CYC-999 兜底回显,会话继续。"""
    log = SessionLog(sid=SID)
    await boot(log)

    async def _boom(args, ctx):
        raise RuntimeError("意外")

    commands.register_command("tboom2", _boom, danger="none", help_text="炸弹2")
    try:
        rendered: list[str] = []
        ok = await commands.handle_slash("/tboom2", _ctx(log, renderer=rendered.append))
    finally:
        commands.COMMANDS.pop("tboom2", None)

    assert ok is True
    assert "CYC-999" in rendered[0]
    assert log.stats()["closed"] is False


# ============================================================ 防御分支覆盖
async def test_exit_with_agent_but_no_shell_degrades():
    """/exit 有 agent 无 shell(装配缺失)→ 降级文本不静默退出;会话仍正常终态留痕。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []
    ctx = _ctx(log, channel="cli", renderer=rendered.append)
    agent = _FakeAgent(log, ctx)
    ctx.agent = agent

    ok = await commands.handle_slash("/exit", ctx)

    assert ok is True
    assert rendered[0] == "当前外壳不支持 /exit"          # 不静默退出(偏离 5)
    assert agent.finished == ["user_command_exit"]      # 终态留痕先行


async def test_async_render_supported():
    """ctx.render 为协程(桌面/ACP 壳)→ 等待其完成后再返回。"""
    log = SessionLog(sid=SID)
    await boot(log)
    rendered: list[str] = []

    async def _render(text):
        rendered.append(text)

    ctx = _ctx(log, renderer=_render)
    ok = await commands.handle_slash("/status", ctx)
    assert ok is True
    assert len(rendered) == 1 and "会话" in rendered[0]


async def test_status_fallback_when_stats_unavailable():
    """ctx.session 无 stats 面(stats 抛错)→ 回放兜底取 seq,命令不崩。"""
    rows = [types.SimpleNamespace(seq=5, type="session.created",
                                  payload={"title": "兜底", "model": MODEL})]

    async def _noop_append(*a, **k):
        return None

    duck = types.SimpleNamespace(
        sid=SID,
        stats=lambda: (_ for _ in ()).throw(RuntimeError("no stats")),
        events_after=lambda after=0: iter(rows),
        append=_noop_append)                           # 占位:本路径不写
    rendered: list[str] = []
    ok = await commands.handle_slash("/status",
                                     _ctx(duck, renderer=rendered.append))
    assert ok is True
    text = rendered[0]
    assert f"会话 {SID}" in text
    assert "兜底" in text                                # 标题来自事件
    assert "seq 至 5" in text                            # 兜底 seq 回放
