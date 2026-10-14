"""Cli parser: complete migrated implementation."""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass
from typing import Any

from pyharness.cli_output import ParseResult, ShellCtx

def _add_global_flags(p: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    """全局旗标定义:主解析器带真默认(None/False);叶子用 default=SUPPRESS ——
    argparse 子解析器以全新 namespace 合并会用它自己的默认覆盖主解析器已解析值
    (parents 复用会让 `--config x config validate` 的前置旗标被冲成 None);
    SUPPRESS 缺省即不留键 → 前置值保留、后置旗标仍可覆盖(见偏离 1)。"""

    def d(value: Any) -> Any:
        return argparse.SUPPRESS if suppress else value

    p.add_argument("--config", "-c", default=d(None),
                   help="配置文件路径(显式指定后不再搜其余路径,L4 最高优先级)")
    p.add_argument("--json", action="store_true", default=d(False),
                   help="结构化输出(机器可读事件流)")
    p.add_argument("--session", default=d(None), metavar="SID",
                   help="恢复/指定会话 id")
    p.add_argument("--verbose", "-v", action="store_true", default=d(False),
                   help="引擎日志级别 debug")

def parse_args(argv: list[str]) -> ParseResult:
    """子命令分发解析:全局旗标 + 16 叶子子命令各带参数;未知子命令/缺参 → 用法
    错误退出 2(argparse 惯例,不造码);--help 列全命令(DEP §5.1 以 --help 为准)。"""
    p = argparse.ArgumentParser(prog="pyharness",
                                description="PyHarness Agent 框架(66 功能/6 阶段)")
    _add_global_flags(p)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<子命令>",
                           title="子命令(16)")

    def leaf(name: str, *pos: tuple, **kw: Any) -> argparse.ArgumentParser:
        """叶子解析器:全局旗标(SUPPRESS 缺省)+ 通用 *args 位置参(可关)+ 专用
        位置参;全局旗标放子命令前/后均合法(见偏离 1)。"""
        has_args = bool(kw.pop("bare_args", False))
        help_txt = kw.pop("help", "")
        s = sub.add_parser(name, help=help_txt, **kw)
        _add_global_flags(s, suppress=True)
        if has_args:
            s.add_argument("args", nargs="*", help="位置参数")
        for argname, opt in pos:
            s.add_argument(argname, **opt)
        return s

    # -- chat/run:交互/单发(会话产生者;run 无 text = headless 读 stdin)
    s = leaf("chat", help="交互对话(默认流式渲染;Ctrl-C 取消/二次退出)")
    s.add_argument("--once", default=None, metavar="TEXT",
                   help="非交互单轮注入文本(配合 --session;缺省读 stdin)")
    leaf("run", ("text", {"nargs": "?", "help": "单发任务文本(缺省读 stdin)"}),
         bare_args=False, help="单发任务,无人值守")
    # -- 引擎单发命令(位置参专名)
    leaf("plan", ("goal", {"help": "目标"}), bare_args=False,
         help="方案→批准→执行(须交互)")
    leaf("search", ("q", {"help": "FTS 检索词"}), bare_args=False,
         help="FTS 全文检索旧会话")
    leaf("fork", ("sid", {"help": "被分支会话 id"}), bare_args=False,
         help="分支会话(COW)")
    leaf("session", bare_args=True,
         help="会话列表/show 回放(DEP §5.1: session | session show <sid>)")
    # -- 其余叶子(通用 args)
    leaf("schedule", bare_args=True, help="定时任务查看/管理")
    leaf("job", bare_args=True, help="任务队列/记录查询")
    leaf("workflow", bare_args=True,
         help="顺序编排:workflow \"步骤1\" \"步骤2\" …(逐步进真 AgentLoop)")
    leaf("skill", bare_args=True,
         help="本地技能包:skill | skill <name>(离线,零装配)")
    leaf("repair", bare_args=True, help="崩溃/损坏修复(F060)")
    leaf("desktop", bare_args=True, help="桌面程序(pywebview 壳,F065)")
    leaf("acp", bare_args=True, help="ACP 桥(JSON-RPC over stdio,F066)")
    leaf("config", bare_args=True, help="config init/validate/show(离线)")
    leaf("budget", bare_args=True, help="预算报表(离线,F032)")
    leaf("stats", bare_args=True, help="会话统计(离线,只读派生)")

    ns = p.parse_args(argv)
    positional = _positional_of(ns)
    flags = vars(ns)
    flags["positional"] = positional            # offline_cmd 消费(见偏离 3)
    return ParseResult(cmd=ns.cmd, positional=positional, flags=flags)

def _positional_of(ns: argparse.Namespace) -> list[str]:
    """位置参聚合:通用 args 优先;专名位置参命令聚合命名位置参(见偏离 2)。"""
    args = getattr(ns, "args", None)
    if args:
        return list(args)
    out: list[str] = []
    for name in ("text", "goal", "q", "sid"):
        val = getattr(ns, name, None)
        if val is not None:
            out.append(val)
    return out

@dataclass(frozen=True)
class CmdEntry:
    """分发表条目:kind = offline|interactive|run|repair|shell|once;offline = 零装配;
    need_session = bootstrap 需要装配会话。"""

    kind: str
    offline: bool = False
    need_session: bool = False

CMD_TABLE: dict[str, CmdEntry] = {
    "chat":     CmdEntry("interactive", need_session=True),
    "run":      CmdEntry("run", need_session=True),
    "plan":     CmdEntry("once", need_session=True),     # 引擎单发(真装配见 _cmd_plan)
    "schedule": CmdEntry("once"),
    "job":      CmdEntry("once"),
    "workflow": CmdEntry("once"),
    "skill":    CmdEntry("offline", offline=True),
    "search":   CmdEntry("once"),
    "session":  CmdEntry("once"),
    "fork":     CmdEntry("once"),
    "repair":   CmdEntry("repair"),
    "desktop":  CmdEntry("shell"),
    "acp":      CmdEntry("shell"),
    "config":   CmdEntry("offline", offline=True),
    "budget":   CmdEntry("offline", offline=True),
    "stats":    CmdEntry("offline", offline=True),
}

def _args_json() -> bool:
    """main 兜底路径的 --json 探测(parse 成功后才有值;失败侧不适用)。"""
    return bool(sys.argv and "--json" in sys.argv)

async def _run_text(sh: ShellCtx, positional: list[str]) -> str:
    """run 文本解析(见偏离 4):位置参优先;缺省且 stdin 非 tty → 整读管道;
    tty 且无参 → 空串(由 run_intent 抛 EVT-100)。"""
    if positional:
        return " ".join(positional)
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""

# Preserve historical import and pickle identities through the compatibility facade.
CmdEntry.__module__ = 'pyharness.cli'
