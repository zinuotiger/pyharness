"""pyharness/core/commands.py — 斜杠命令总闸 F041(specs/commands.py.md 契约;阶段 3)

功能编号:F041(斜杠命令,核心)· 联动 F040(todo 状态,`/status` 数据源)/F032(预算,
`/cost` 数据源)/F042(会话标题)/F064(CLI 外壳 headless R8)/F015(审批通道语义)。

职责一句话:输入以 `/` 开头即走本模块——**先落 `user.command`(命中即写,EVT-104 安全序)
再分发**;命中即\"不给 LLM 的控制通道\"(零 LLM 硬保证:本模块不 import llm/agent_loop,
`/foo` 后 llm.request 事件数不增为验收断言);未知命令回显 `system.error(EVT-105)` 会话
继续;headless(外壳装配时 ctx.channel is None,即 stdin 非 tty,无配置开关可改)下
状态变更命令(danger=high)无交互通道直接拒(APR-501,R8)。危险动作不存在于斜杠面——
一切执行走工具 + guard(F041 边界条件)。

本模块 = 三壳共用内核(F064 chat / F065 desktop / F066 ACP 经 agent.submit 进同一分发):
判断/事件/状态都在本模块,外壳只做 IO 回显(经注入的 ctx.render)。

数据纪律:命令数据一律从会话事件派生(原则 1)——status/cost 读 session 事件
(created/renamed/todo.updated/llm.usage),不建第二份内存状态;headless 判定同
approval.py 通道注入语义(ctx.channel:tty 交互=\"cli\" / ACP=\"acp:<id>\" / 管道=None)。

错误面:EVT-105/APR-501 是**事件侧**错误(system.error 落盘留痕,不抛异常不传播,
handle_slash 恒返回 True);handler 内部异常兜底回显失败文本 + 本地堆栈,会话不崩;
跨边界的结构化异常一律来自 errors.raise_code(本模块禁裸 raise str)。

偏离说明(相对 specs/commands.py.md 伪码;契约=spec,以下为与既有实现冲突处的取舍,
均列理由,与 approval.py/agent.py 同款先例):
1. headless × high 的写序:handle_slash 伪码\"先写 user.command 再 _gated\"与模块职责 6 /
   异常表\"命令不执行、不写 user.command(零执行零事件)\"直接冲突——按异常表为规范面
   (approval.py 偏离 2 同口径:headless 拒 = APR-501 零事件,拒绝留痕只经 system.error),
   本实现把 _gated 前置:拒绝路径不写 user.command,只落 system.error(APR-501)。
2. 会话只读适配:伪码 ctx.session.meta/last_seq()/session_id 不存在于真实 SessionLog
   (权威面为 sid/stats()/events_after,见 specs/session.py.md)→ 标题由 session.created/
   session.renamed 事件派生(F042),最新 seq 取 stats()[\"seq\"],会话 id 经 _sid() 兼容
   sid/session_id 双名。
3. ctx.render 经 getattr 注入判定:伪码无条件 ctx.render(text);本实现未注入
   (ACP 无屏/测试嵌入)时静默跳过——装配面(注入 render 的外壳)行为与伪码一致。
4. handler 内部异常兜底:异常表 CYC-999 行(回显失败 + 本地堆栈,会话不崩)→
   PyHError 保留原码回显,未预期异常按 CYC-999 回显,均不传播、日志含本地堆栈。
5. cmd_exit 的 ctx.shell 装配缺失(外壳未注入 request_exit)→ 降级文本不静默退出
   (与 ctx.agent 为 None 的降级同构,防\"以为退出了还在跑\")。
6. specs/cli.py.md 外壳循环注释\"handle_slash 返回 false=退出\"与本文档\"恒返回 True +
   ctx.shell.request_exit(0)\"冲突——以本文档(commands.py.md,本模块契约)为准,退出
   经 request_exit 副作用表达,F064 消费方按外壳退出标志收尾。
"""
from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from pyharness.errors import PyHError, wrap_unexpected

log = logging.getLogger("pyharness.commands")

# ------------------------------------------------------------------ 注册表
@dataclass(frozen=True)
class CommandEntry:
    """命令注册表条目(注册即冻结,装配后只读)。

    name = 命令名(小写);handler = async (args, ctx) -> str 回显文本;
    danger = none(只读,恒放行)| high(状态变更,headless 拒)——命令面无 critical,
    危险动作不存在于斜杠面(F041);help = /help 展示的一句话。
    """

    name: str
    handler: Callable[..., Any]
    danger: str = "none"
    help: str = ""


# 有序注册表:注册序即 /help 展示序(Python dict 保插入序)
COMMANDS: dict[str, CommandEntry] = {}

# 别名表:解析期先查别名再查主表;别名不占独立 help 行,标注于主命令(DEP §5.3)
ALIASES: dict[str, str] = {
    "quit": "exit",      # /quit ≡ /exit(F064 Ctrl-D 等价)
    "budget": "cost",    # /budget ≡ /cost(兼容 DEP §5.3 文档命令名)
}


# ------------------------------------------------------------ 事件派生辅助
def _iter_events(s: Any) -> Any:
    """会话事件迭代(SessionLog.events_after(0) 全量;门面无该面时为空)。"""
    ea = getattr(s, "events_after", None)
    if callable(ea):
        yield from ea(0)
    return


def _sid(s: Any) -> str:
    """会话 id 读取:SessionLog.sid 权威,兼容门面别名 session_id。"""
    sid = getattr(s, "sid", None) or getattr(s, "session_id", None)
    return sid if isinstance(sid, str) else str(sid or "")


def _last_seq(s: Any) -> int:
    """事件最新 seq(SessionLog.stats()[\"seq\"];stats 缺面时回放尾事件)。"""
    st = getattr(s, "stats", None)
    if callable(st):
        try:
            return int(st().get("seq", 0) or 0)
        except Exception:  # noqa: BLE001 门面异常 → 回放兜底
            pass
    last = 0
    for env in _iter_events(s):
        last = env.seq
    return last


def _session_title(s: Any) -> str:
    """会话标题派生(F042):session.created 初值 + 最新 session.renamed 覆盖。"""
    title = ""
    for env in _iter_events(s):
        p = env.payload
        if env.type == "session.created":
            title = p.get("title") or ""
        elif env.type == "session.renamed":
            title = p.get("new_title") or title
    return title or "(未命名)"


def _aggregate_usage(s: Any) -> dict:
    """llm.usage 事件聚合(F029/F032):in/out tokens 与 cost_est 逐条累加。"""
    in_tokens = out_tokens = 0
    cost_est = 0.0
    for env in _iter_events(s):
        if env.type != "llm.usage":
            continue
        p = env.payload
        in_tokens += int(p.get("in_tokens") or 0)
        out_tokens += int(p.get("out_tokens") or 0)
        cost_est += float(p.get("cost_est") or 0.0)
    return {"in_tokens": in_tokens, "out_tokens": out_tokens, "cost_est": cost_est}


def _latest_todos(s: Any) -> list[dict]:
    """todo 派生(F040):todo.updated 为全量快照语义 → 每 task_id 取最新事件,扁平化。"""
    latest: dict[str, list[dict]] = {}
    for env in _iter_events(s):
        if env.type != "todo.updated":
            continue
        latest[env.payload.get("task_id", "")] = [
            {"id": it.get("id"), "text": it.get("text"),
             "done": bool(it.get("done"))}
            for it in (env.payload.get("todos") or [])]
    out: list[dict] = []
    for task_id in sorted(latest):  # 多任务按 task_id 稳定排序(确定性输出)
        for it in latest[task_id]:
            out.append({**it, "task_id": task_id})
    return out


# ------------------------------------------------------------------ 内置命令
async def cmd_help(args: str, ctx: Any) -> str:
    """/help:无参列全部命令(注册序,≤40 行);带参查单条详情(别名先归一)。只读。"""
    if args:  # /help <name>:单条详情(未知则纯文本提示,零 LLM)
        name = ALIASES.get(args.lower(), args.lower())
        entry = COMMANDS.get(name)
        if entry is None:
            return f"未知命令 /{args};可用 /help 查看全部"
        return f"/{entry.name} — {entry.help}"
    rows = [f"/{e.name:<10} {e.help}" for e in COMMANDS.values()]
    rows.append("别名: /budget=/cost, /quit=/exit")
    rows.append("未知命令回显 EVT-105;斜杠命令不消耗 LLM")
    return "\n".join(rows)


async def cmd_status(args: str, ctx: Any) -> str:
    """/status:会话/任务/todo 进度快照(F040/F042)——事件派生,零 LLM 零第二份状态。"""
    s = ctx.session
    budget = getattr(ctx, "budget", None)
    state = getattr(budget, "state", None) if budget is not None else None
    lines = [
        f"会话 {_sid(s)} | {_session_title(s)} | seq 至 {_last_seq(s)}",
        f"运行态: {state if state else 'n/a'}",
    ]
    pending = [t for t in _latest_todos(s) if not t["done"]][:20]  # 未完成 ≤20
    if pending:
        lines.append(f"任务进度(F040): {len(pending)} 项未完成")
        lines += [f"  [{t['id']}] {t['text']}" for t in pending]
    else:
        lines.append("任务进度: 当前无未完成 todo 项")
    return "\n".join(lines)


async def cmd_cost(args: str, ctx: Any) -> str:
    """/cost:当前会话预算/成本报表(F032)——只读离线,预算闸未装配降级只输出用量段。"""
    usage = _aggregate_usage(ctx.session)
    line = (f"当前会话 in={usage['in_tokens'] / 1000:.1f}k "
            f"out={usage['out_tokens'] / 1000:.1f}k "
            f"估算 {usage['cost_est']:.2f} 元")
    budget = getattr(ctx, "budget", None)
    lim = getattr(budget, "limit_cny", None) if budget is not None else None
    if isinstance(lim, (int, float)) and lim > 0:  # 装配了预算闸:硬闸 + warn 80%
        used = float(getattr(budget, "used_cny", 0.0) or 0.0)
        pct = used / float(lim) * 100.0
        warn = " ⚠warn 80%" if pct >= 80 else ""
        line += f" | 硬闸 {float(lim):.2f} 元({pct:.0f}%){warn}"
    return line


async def cmd_new(args: str, ctx: Any) -> str:
    """/new:结束当前会话并开新(DEP §5.2;交互必需)。

    终态委托 ctx.agent(DIS-CORE §2.3:session.finished 唯一归属 agent.close/agent
    生命周期面)——本函数只委托不自己落终态;danger=high,headless 由前置 _gated 已拒。
    """
    if ctx.agent is None:  # 无生命周期通道(理论不可达,防御降级)
        return "当前外壳不支持 /new"
    old = _sid(ctx.session)
    await ctx.agent.finish_session(reason="user_command_new")   # 终态委托(唯一归属)
    await ctx.agent.new_session()                               # 新会话 seq 从 1
    return (f"已结束会话 {old} 并开启新会话 "
            f"{_sid(ctx.session)}(原日志留档,可 session list 找回)")


async def cmd_exit(args: str, ctx: Any) -> str:
    """/exit:请求外壳退出(F064,Ctrl-D 等价;别名 /quit)——置退出标志 + 结束会话。

    会话结束委托 ctx.agent(finished,reason=user_command_exit);退出码经 ctx.shell.
    request_exit(0) 表达(回显后外壳即退);danger=high,headless 由前置 _gated 已拒。
    """
    if ctx.agent is None:  # 无生命周期通道
        return "当前外壳不支持 /exit"
    await ctx.agent.finish_session(reason="user_command_exit")  # 终态留痕
    shell = getattr(ctx, "shell", None)
    if shell is None or not callable(getattr(shell, "request_exit", None)):
        return "当前外壳不支持 /exit"  # 装配缺失:无退出通道,不静默(见偏离 5)
    shell.request_exit(0)              # 外壳退出码 0(F064)
    return "再见"


# ------------------------------------------------------------------ 注册点
def register_command(name: str, handler: Callable[..., Any], *,
                     danger: str = "none", help_text: str = "") -> None:
    """扩展注册点(阶段 4 plan_mode/undo 等运行时挂载,INV-08:各自 import)。

    重名/非法名(非 [a-z][a-z0-9]*)/非法 danger(∉{none, high},命令面无 critical)
    → ValueError(装配期编程错误,早失败,不进事件系统,禁现场造码);注册即冻结。
    """
    if name in COMMANDS or name in ALIASES:  # 重名/占用别名 → 拒
        raise ValueError(f"command already registered: /{name}")
    if not re.fullmatch(r"[a-z][a-z0-9]*", name):  # 非法名 → 拒
        raise ValueError(f"illegal command name: /{name}")
    if danger not in ("none", "high"):             # 命令面无 critical → 拒
        raise ValueError(f"command danger must be none|high: /{name}")
    COMMANDS[name] = CommandEntry(name=name, handler=handler,
                                  danger=danger, help=help_text)


def _install_builtins() -> None:
    """内置命令注册(注册序 = /help 展示序;别名见 ALIASES 表,不占独立 help 行)。"""
    register_command("help", cmd_help, danger="none",
                     help_text="命令帮助;/help <name> 查单条")
    register_command("status", cmd_status, danger="none",
                     help_text="会话/任务/todo 进度快照(F040/F042)")
    register_command("cost", cmd_cost, danger="none",
                     help_text="当前会话预算/成本报表(F032)")
    register_command("new", cmd_new, danger="high",
                     help_text="结束当前会话并开新(日志留档)")
    register_command("exit", cmd_exit, danger="high",
                     help_text="退出会话/外壳(别名 /quit)")


_install_builtins()


# ------------------------------------------------------------------ 安全闸
def _gated(entry: CommandEntry, ctx: Any) -> bool:
    """headless × 状态变更闸(R8/F064/CFG §3.8):danger=high 且无交互通道 → 拒。

    headless 判定唯一口径 = ctx.channel is None(外壳装配:stdin 非 tty/管道/后台
    job 无通道;无配置开关可改);只读命令(none)任何通道放行。
    """
    if entry.danger != "high":        # 只读命令恒放行
        return False
    if getattr(ctx, "channel", None):  # 有交互通道(CLI tty/ACP)
        return False
    return True                       # headless × high → 拒


# ------------------------------------------------------------------ 分发总入口
async def _record_error(ctx: Any, code: str, hint: str) -> None:
    """system.error 留痕(EVT-105 未知 / APR-501 headless 拒):错误面 = 事件,不传播。

    hint 不含参数原文(审计最小化,EVENT-SCHEMA §6.2);code 均为 ERR.md 已登记码。
    """
    await ctx.session.append("system.error",
                             {"code": code, "hint": hint}, actor="system")


async def _render(ctx: Any, text: str) -> None:
    """外壳回显(注入,非日志):ctx.render 由三壳装配注入;未注入静默跳过(偏离 3)。"""
    render = getattr(ctx, "render", None)
    if not callable(render):
        return
    out = render(text)
    if inspect.isawaitable(out):     # 容忍异步回显(桌面/ACP 壳)
        await out


async def handle_slash(raw: str, ctx: Any) -> bool:
    """斜杠命令分发总入口(F041):解析 → 查别名/主表 → 分发执行。恒返回 True。

    流程(调用方契约:仅 '/'-开头输入进入本函数):
        1. body = raw[1:],首词 = 命令名(小写归一);
        2. 未知 → system.error(EVT-105) 回显,会话继续,零 LLM(不写 user.command);
        3. headless × danger=high → system.error(APR-501) 留痕,零执行零副作用,
           不写 user.command(见偏离 1);命令面无审批请求;
        4. 命中 → **先** user.command 落盘(EVT-104 安全序:保证 /new /exit 终态前
           命令已留痕)再调 handler;handler 异常兜底回显,会话不崩;
        5. ctx.render 回显 handler 文本;恒返回 True(命令输入永不落 user.message)。

    零 LLM 硬保证:本路径不 import llm、不触达 agent-loop 的模型调用——命令后
    llm.request 事件数不增为验收断言(DEP G5)。
    """
    body = raw[1:].strip()                       # 去掉首 "/"
    name, _, arg = body.partition(" ")           # 首词 = 命令名
    name = name.lower().lstrip("/")
    entry = COMMANDS.get(ALIASES.get(name, name))  # 别名 → 主名 → 查表
    if entry is None:                            # 未知命令:EVT-105,会话继续
        await _record_error(ctx, "EVT-105",
                            f"未知命令 /{name},可用 /help 查看")
        return True                              # 已消费;零 LLM
    if _gated(entry, ctx):                       # headless × high:直接拒(APR-501)
        await _record_error(ctx, "APR-501",
                            f"无交互通道,/{entry.name} 已拒绝(headless)")
        return True                              # 拒绝不执行(INV-05 精神)
    # 命中即写(先落盘!):/new /exit 终态命令执行前命令已留痕(EVT-104 安全序)
    await ctx.session.append("user.command",
                             {"name": entry.name, "args": arg.strip()},
                             actor="user")
    try:
        text = await entry.handler(arg.strip(), ctx)   # 分发执行
    except PyHError as e:
        # 结构化失败:保留原码回显 + 本地堆栈,会话不崩(异常表 CYC-999 兜底精神)
        log.error("slash handler failed command=/%s code=%s", entry.name,
                  e.code, exc_info=True)
        text = f"命令 /{entry.name} 执行失败[{e.code}];详见本地日志,会话继续"
    except Exception as e:  # noqa: BLE001 未预期异常兜底:归 CYC-999,本地堆栈
        wrapped = wrap_unexpected(e, f"commands.cmd_{entry.name}")
        log.error("slash handler unexpected command=/%s code=%s", entry.name,
                  wrapped.code, exc_info=True)
        text = f"命令 /{entry.name} 执行失败[CYC-999];详见本地日志,会话继续"
    await _render(ctx, text)                     # 外壳回显(注入,非日志)
    return True                                  # 恒 True:命令输入已消费


__all__ = [
    "COMMANDS", "ALIASES", "CommandEntry",
    "register_command", "handle_slash", "_gated",
    "cmd_help", "cmd_status", "cmd_cost", "cmd_new", "cmd_exit",
]
