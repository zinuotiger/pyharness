"""pyharness/cli.py — PyHarness 第一壳:CLI 完整外壳 (specs/cli.py.md 契约;阶段 6)

功能编号:F064(CLI 完整外壳)· F065(desktop 子命令接管原 web 位)· F066(acp 子命令桥)·
F060(repair 子命令桥 + 启动自检)· F041(斜杠命令零 LLM 通道)· F015(审批人类裁决)。

一句话职责:argparse 解析 14 个叶子子命令(chat/run/plan/schedule/job/search/session/
fork/repair/desktop/acp/config/budget/stats)与全局旗标(--config/--json/--session/
--verbose),按命令分发到交互循环 / 单发任务 / 离线命令 / 长驻外壳,统一退出码
(0 成功、1 引擎错误带 F019 码、2 用法错误),headless(stdin 非 tty)默认拒绝无审批
通道的危险动作(R8:channel=None → APR-501/GRD-401 在裁决侧天然生效),--json 输出
机器可读事件流,Ctrl-C 一次取消当前轮、两次退出(130)。

本模块 = 纯外壳:零业务逻辑,一切动作委托引擎(commands.handle_slash / task_queue /
session / repair / config),事件由总线订阅渲染,不直写存储;desktop/acp 子命令只做
分发与参数透传,实现在 pyharness/desktop.py / pyharness/acp.py(同批任务)。

偏离说明(契约 = specs/cli.py.md;以下为与规格伪码冲突/未展开处的取舍,均列理由,
与 commands.py/approval.py 同款先例,已入 docstring 供审查):
1. parse_args 的全局旗标在每片叶子 subparser 上经 parents 重复声明——argparse 顶层
   旗标只认子命令前的位置,DEP §5.1 示例 `repair --session <sid>`/`fork <sid> --as`
   需要旗标可置于子命令后;parents 复用后两种写法均合法(伪码 leaf() 未覆盖)。
2. 伪码 leaf() 给所有子命令加通用 *args 后再给 run/plan/search/fork/session 加专用
   位置参(argparse 两可位置参冲突)→ 通用 args 只加在无专用位置参的叶子上,专用
   位置参命令的 ParseResult.positional 由命名位置参聚合(用户可见 CLI 不变)。
3. offline_cmd(cmd, flags) 读 flags["positional"] 与 ParseResult 结构表分离字段自相
   矛盾 → 签名改为 offline_cmd(cmd, positional, flags),位置参直传更清晰。
4. cli_main 伪码 run 分支的 text_or_stdin 未入函数清单 → 内联为 _run_text 助手
   (位置参优先;缺省且 stdin 非 tty 时整读管道;tty 且无参 = 空 → run_intent 抛
   EVT-100,与 spec 异常表一致)。
5. interactive_loop 伪码"取消后递归重入"(return await interactive_loop(...))会无限
   深递归 → 改为 while 循环 + 捕获 _CancelledRound 后重布防(armed=False),语义
   等价(一次取消当前轮)且无栈风险。
6. --once 单轮语义按 DEP §5.2 落地:成功处理完一轮即返 0;伪码 while 循环会在
   once 文本来自 --once 时把同一文本无限重复提交(每次循环都重读 flags["once"])
   → 判为伪码缺陷,单轮后返回(取消轮次例外:继续回到循环顶)。
7. bootstrap_shell 的 repair 前置(auto_scan/repair_session/ensure_repair)与
   repair_cmd 依赖 pyharness/repair.py(F060 同批任务,可能尚未落盘)→ 一律函数内
   惰性导入 + 未装配时降级跳过(记日志),不硬依赖同批未落地模块;装配后自动生效。
   同理 desktop/acp 惰性导入(见 cli_main),fakemodule/真实模块两形态兼容
   (acp: 模块级 serve(ctx, client_id=...) 权威面;AcpBridge 旧面兜底)。
8. 事件渲染订阅:伪码订阅字面类型 `task:{task_id}:#`,而真实总线(EventBus)只认
   已注册类型/`段.*` 通配 → 渲染器按真实词表订阅精确类型 + `agent.*/tool.*/
   guard.*/approval.*/session.*/task.*` 通配(段前缀匹配),JSONL 行以
   {"type":…,"payload":…} 直通总线事件(信封 Envelope 化属引擎装配面,后续补齐)。
9. run_intent 伪码 res.events_of("guard.rejected") 不存在于真实 TaskResult
   (dataclass: ok/code/summary/duration_ms,终态以事件为准 INV-01)→ 拒绝清单改为
   getattr 探测:结果对象带 events_of/rejected 面则用,否则空清单(引擎装配后由
   事件面补全);退出码按 ok/reason/code 多面兼容归一。
10. Ctrl-C 真实信号 → 协程上下文投递依赖 asyncio 信号处理内部细节(handler 在事件
    循环机制层触发时异常不经交互协程),本模块按 spec 语义落地 handler(一次置位
    raise _CancelledRound / 二次 SystemExit(130))并把 _CancelledRound 同时作为
    main() 的受控退出路径(130),真实按键语义留待 PTY 交互冒烟校准(DEP §4.7)。
11. 启动序(ctx 装配):spec 依赖表引 pyharness.bootstrap / assemble_ctx,该装配模块
    未落盘 → 装配在 cli 内联为轻量门面(settings/bus/session/task_queue(runner=None,
    未注入执行器时任务 CYC-999 快速失败,防 S-1 伪造执行)/approval/shell(退出旗标)/
    storage/handlers(单发处理器注入面,见 run_once_sub)),引擎脊柱注入点保留注释。
12. repair_cmd/run_once_sub 报告字段按鸭子类型读取(fixed/lost/backup_path/
    quarantined 可能为列表或计数,repair 模块报告面为准),避免对同批模块字段形状
    硬编码。
13. DEP §10 G5 验收文本"连续两次 Ctrl-C 退出码 0"与 specs/cli.py.md 异常表
    SystemExit(130)/KeyboardInterrupt→130 冲突 → 以本模块规格为准:中断退出 130,
    /exit 与 Ctrl-D(EOF) 才走 0。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from pyharness import config as config
from pyharness.core import commands
from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.cli")

# ---------------------------------------------------------------- 常量
PROMPT = "pyharness> "                     # chat 提示符(无换行)
EXIT_INTERRUPT = 130                       # Ctrl-C 退出码(KeyboardInterrupt)
OFFLINE_CMDS = frozenset({"config", "budget", "stats"})     # 零网络零 LLM(DEP §5.1 ✅)
SHELL_CMDS = frozenset({"desktop", "acp"})  # 长驻外壳:交出其控制权(F065/F066)
INTERACTIVE_CMDS = frozenset({"chat", "plan"})  # tty 判定:交互命令才注入 channel

# 文本模式渲染卡片的事件类型(其余事件 json 模式全量、text 模式静默)
_TEXT_CARD_TYPES = frozenset({"agent.message", "tool.call", "tool.result",
                              "tool.error", "guard.rejected", "user.message",
                              "session.finished", "task.completed", "task.failed"})
# 渲染终止事件(引擎终态或会话终态;渲染任务另由 wait_for 返回后强制取消兜底)
_END_EVENT_TYPES = frozenset({"session.finished", "task.completed", "task.failed"})
# 总线订阅面:精确 + 段通配(真实总线 EventBus 的 _wild_match 段前缀匹配)
_RENDER_PATTERNS = ("llm.chunk", "agent.*", "tool.*", "guard.*",
                    "approval.*", "session.*", "task.*")


# ---------------------------------------------------------------- 数据结构
class _CancelledRound(Exception):
    """首次 Ctrl-C 信号:取消当前轮次(interactive_loop 捕获后回循环顶)。"""


@dataclass
class ParseResult:
    """argparse 产物:cmd/positional/flags(flags = vars(ns),含 config/json/session/
    verbose/once 及各子命令专有旗标;positional 为分离字段,同时回填 flags 供
    offline_cmd 消费)。"""

    cmd: str
    positional: list[str]
    flags: dict


@dataclass
class ShellCtx:
    """外壳上下文:ctx = 装配门面;loop = 事件循环;channel = 审批/斜杠交互通道
    (headless 时 None);headless = stdin 非 tty;flags = 全局旗标镜像。"""

    ctx: Any
    loop: Any = None
    channel: Optional[str] = None
    headless: bool = False
    flags: dict = field(default_factory=dict)


@dataclass
class ExitResult:
    """子命令返回体:code = 退出码;payload = 结构化结果(可空;--json 出口)。"""

    code: int
    payload: Optional[dict] = None


@dataclass
class StreamRenderer:
    """事件 → 屏幕/JSONL 渲染器:text 模式增量打字机 + 卡片;json 模式 stdout 只出
    结构化行(人类提示一律 stderr,保机器流纯净)。carriage 标记打字机行未换行。"""

    mode: str = "text"                 # text | json
    seen_seq: int = 0                  # 已见事件 seq(信封面接入后使用)
    carriage: bool = False             # 打字机行进行中(未换行)

    # ---------------------------------------------------- 瞬时增量(llm.chunk)
    def delta(self, payload: dict) -> None:
        text = payload.get("delta", "")
        if self.mode == "json":
            self.event("llm.chunk", payload)          # 机器流:逐事件一行
            return
        if text:
            sys.stdout.write(text)
            sys.stdout.flush()
            self.carriage = True

    # ---------------------------------------------------- 卡片/结构化事件
    def event(self, type_: str, payload: Any) -> None:
        """单事件出口:text 模式按类型画卡片;json 模式逐事件一行 JSONL。"""
        payload = _normalize_payload(payload)
        if self.mode == "json":
            _json_line({"type": type_, "payload": payload})
            return
        if self.carriage:
            sys.stdout.write("\n")                     # 打字机行收尾再画卡片
            self.carriage = False
        card = _text_card(type_, payload)
        if card:
            print(card)

    def newline(self) -> None:
        if self.carriage:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.carriage = False


def _normalize_payload(payload: Any) -> dict:
    """信封/模型/裸 dict 归一化(引擎装配面可能直通 Envelope,鸭子类型兼容)。"""
    if isinstance(payload, dict):
        return payload
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:                              # 老版 pydantic 无 mode 参
            return dump()
    return {"raw": str(payload)}


def _trunc(s: str, n: int = 240) -> str:
    """卡片文本截断(防刷屏;超长加省略号)。"""
    return s if len(s) <= n else s[: n - 3] + "..."


def _text_card(type_: str, payload: dict) -> Optional[str]:
    """单事件文本卡片;未列类型返回 None(text 模式静默)。"""
    if type_ not in _TEXT_CARD_TYPES:
        return None
    if type_ == "agent.message":
        return payload.get("content", "") or ""
    if type_ == "user.message":
        return ""
    if type_ == "tool.call":
        name = payload.get("tool") or payload.get("name") or "?"
        args = payload.get("args_summary") or payload.get("args") or {}
        return f"[工具] {name} {_trunc(json.dumps(args, ensure_ascii=False))}"
    if type_ == "tool.result":
        data = payload.get("content") or payload.get("summary") or payload.get("result")
        return f"[结果] {_trunc(str(data))}"
    if type_ == "tool.error":
        return f"[工具错误] {_trunc(payload.get('error') or str(payload))}"
    if type_ == "guard.rejected":
        return f"[拒绝] {_trunc(json.dumps(payload, ensure_ascii=False))}"
    if type_ == "session.finished":
        return f"[会话结束] {payload.get('reason', '')}"
    if type_ == "task.completed":
        return f"[任务完成] {payload.get('task_id', '')}"
    if type_ == "task.failed":
        return (f"[任务失败] {payload.get('task_id', '')} "
                f"{payload.get('error', '')} {payload.get('reason', '')}".strip())
    return None


# ---------------------------------------------------------------- 输出助手
def _json_line(obj: dict) -> None:
    """stdout 结构化行(唯一机器出口;禁人类文本混入)。"""
    print(json.dumps(obj, ensure_ascii=False))


def _emit_plain(text: str, *, err: bool = False) -> None:
    """人类文本出口:text 模式 stdout / json 模式一律 stderr(保流纯净);
    err=True 恒走 stderr(错误/中断提示)。"""
    if err or True:                                    # 提示类文本统一 stderr 保守
        print(text, file=sys.stderr)
    else:
        print(text)


def _emit_error(e: PyHError, *, json_mode: bool) -> None:
    """引擎错误出口(ERR §1.4):只回 code+advice(hint 优先——raise_code 现场
    提示比登记册通用 advice 更可行动;ctx 敏感明细不外泄)。"""
    detail = (e.ctx.get("advice") or e.ctx.get("hint") or e.spec.advice)
    if json_mode:
        _json_line({"type": "system.error",
                    "payload": {"code": e.code, "hint": str(detail)}})
    else:
        print(f"{e.code}:{detail}", file=sys.stderr)


def _result_ok(res: Any) -> bool:
    """任务结果成功归一(真实 TaskResult / mock 多面兼容)。"""
    if res is None:
        return True
    ok = getattr(res, "ok", None)
    if ok is not None:
        return bool(ok)
    reason = getattr(res, "reason", None)
    return reason in (None, "complete", "cancelled_by_user")


def _result_code(res: Any) -> str:
    return str(getattr(res, "code", "") or "")


def _result_summary(res: Any) -> str:
    val = getattr(res, "summary", None)
    if callable(val):
        try:
            val = val()
        except Exception:                              # noqa: BLE001 摘要面缺失
            val = None
    if not val:
        val = getattr(res, "reason", "ok") or "ok"
    return str(val)


def _result_reason(res: Any) -> str:
    return str(getattr(res, "reason", "") or "")


def _rejected_list(res: Any) -> list:
    """拒绝清单读取(鸭子类型):events_of('guard.rejected') / .rejected 均可。"""
    fn = getattr(res, "events_of", None)
    if callable(fn):
        try:
            return list(fn("guard.rejected") or [])
        except Exception:                              # noqa: BLE001 无该面
            return []
    return list(getattr(res, "rejected", None) or [])


# ---------------------------------------------------------------- 解析 (F064)
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
    """子命令分发解析:全局旗标 + 14 叶子子命令各带参数;未知子命令/缺参 → 用法
    错误退出 2(argparse 惯例,不造码);--help 列全命令(DEP §5.1 以 --help 为准)。"""
    p = argparse.ArgumentParser(prog="pyharness",
                                description="PyHarness Agent 框架(66 功能/6 阶段)")
    _add_global_flags(p)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<子命令>",
                           title="子命令(14)")

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


# ============================================================ 分发表 (CmdTable)
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
    "plan":     CmdEntry("once", need_session=True),     # 引擎单发(plan 引擎未来装配)
    "schedule": CmdEntry("once"),
    "job":      CmdEntry("once"),
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


# ---------------------------------------------------------------- 主入口
def main(argv: Optional[list[str]] = None) -> int:
    """进程入口(console_scripts `pyharness`):事件循环只在这里创建;任何未捕获
    异常 → CYC-999 结构化到 stderr、退出 1。返回进程退出码。"""
    try:
        parsed = parse_args(argv if argv is not None else sys.argv[1:])
        return asyncio.run(cli_main(parsed))
    except KeyboardInterrupt:
        print("\n[interrupted] 已退出", file=sys.stderr)
        return EXIT_INTERRUPT
    except _CancelledRound:                    # 信号→循环机制层透传的受控路径(偏离 10)
        print("\n[已取消当前轮,再按 Ctrl-C 退出]", file=sys.stderr)
        return EXIT_INTERRUPT
    except PyHError as e:                      # 引擎错误:带码退出(1)
        _emit_error(e, json_mode=_args_json())
        return 1
    except SystemExit:                         # argparse 用法错误(2)/--help(0)透传
        raise
    except Exception as e:                     # 未预期兜底,禁现场造码(CYC-999)
        print(f"CYC-999 {e!r}", file=sys.stderr)
        log.debug("cli uncaught", exc_info=True)
        return 1


def _args_json() -> bool:
    """main 兜底路径的 --json 探测(parse 成功后才有值;失败侧不适用)。"""
    return bool(sys.argv and "--json" in sys.argv)


# ---------------------------------------------------------------- 分发总控
async def cli_main(r: ParseResult) -> int:
    """分发总控(F064 验收直译):offline 命令不装配引擎直接跑;其余按子命令进
    bootstrap 后分发;desktop/acp 为长驻外壳交出其控制权。"""
    if r.cmd not in CMD_TABLE:                 # 理论不可达(argparse 已拦)
        raise_code("CYC-999", cmd=r.cmd, hint="未知子命令未被子命令解析拦截")
    entry = CMD_TABLE[r.cmd]
    if entry.offline:                          # config/budget/stats:零装配
        return offline_cmd(r.cmd, r.positional, r.flags)
    sh = await bootstrap_shell(r)              # 启动序 + repair 前置 + headless 判定
    try:
        if r.cmd == "chat":
            return await interactive_loop(sh, once=r.flags.get("once"))
        if r.cmd == "run":
            return await run_intent(sh, await _run_text(sh, r.positional),
                                    headless=sh.headless)
        if r.cmd == "repair":
            return await repair_cmd(sh, r.flags.get("session"))    # F060 桥
        if r.cmd == "desktop":
            from pyharness.desktop import run_desktop     # F065 交权(惰性:同批任务)
            return await run_desktop(sh.ctx)
        if r.cmd == "acp":
            mod = __import__("pyharness.acp", fromlist=["serve", "AcpBridge"])
            serve = getattr(mod, "serve", None)
            if serve is None:                            # AcpBridge 旧面兜底(偏离 7)
                bridge = getattr(mod, "AcpBridge")(sh.ctx, channel="acp:cli")
                return await bridge.serve()
            return await serve(sh.ctx, client_id="acp:cli")
        # plan/schedule/job/search/session/fork:统一"装配+执行+渲染"单发路径
        return await run_once_sub(sh, r.cmd, r.positional, r.flags)
    finally:
        await _flush_session(sh)               # 收尾落盘:普通事件攒批全量刷盘(F011)


async def _run_text(sh: ShellCtx, positional: list[str]) -> str:
    """run 文本解析(见偏离 4):位置参优先;缺省且 stdin 非 tty → 整读管道;
    tty 且无参 → 空串(由 run_intent 抛 EVT-100)。"""
    if positional:
        return " ".join(positional)
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


# ---------------------------------------------------------------- 装配 (启动序)
def _load_settings(cfg_path: Optional[str]) -> Any:
    """配置四层加载(L1→L2 文件→L3 env→L4 CLI 路径),CFG-601 失败即中止不装配。

    config.load_settings 只收 CLI 覆盖元组、不收路径 → 显式 --config 路径经公开
    原语(deep_merge/load_file_layer/env_subset/Settings/validate_rules)重走同一
    管线,语义与 load_settings 一致(见偏离 11)。
    """
    if not cfg_path:
        return config.load_settings()
    raw = config.deep_merge(config.load_file_layer(cfg_path), config.env_subset())
    try:
        settings = config.Settings.model_validate(raw)
    except Exception as e:                     # noqa: BLE001 与 config 主管线同错面
        if isinstance(e, config.ValidationError):
            fields = [".".join(str(x) for x in err.get("loc") or ())
                      for err in e.errors()]
            raise_code("CFG-601", reason="type_range", fields=fields,
                       detail="类型/范围/枚举越界,修配置文件对应字段")
        raise
    config.validate_rules(settings)
    return settings


def assemble_ctx(cfg: Any, *, session: Any = None) -> SimpleNamespace:
    """轻量门面装配(见偏离 11):settings/bus/session/task_queue/approval/shell
    (退出旗标)/storage/handlers(单发处理器注入面)。引擎脊柱八模块注入点保留,
    同批后续任务在此接线。"""
    from pyharness.bus import EventBus
    bus = EventBus()
    ns = SimpleNamespace(
        settings=cfg,
        bus=bus,
        session=session,
        task_queue=None,
        approval=None,
        agent=None,
        budget=None,
        channel=None,
        shell=_ExitFlag(),
        render=None,
        handlers={},                            # 引擎单发处理器注入面(run_once_sub)
        storage=SimpleNamespace(
            sessions_dir=Path(cfg.storage.sessions_dir).expanduser(),
            db_path=Path(cfg.storage.db_path).expanduser()),
    )
    if session is not None:
        _wire_queue(ns)
    return ns


def _wire_queue(ns: SimpleNamespace) -> None:
    """任务队列接线(执行器注入面:runner=None 时任务按 CYC-999 快速失败,防
    S-1 伪造已执行;agent_loop.run_for_task 由引擎装配注入)。"""
    from pyharness.core.approval import ApprovalProvider
    from pyharness.core.task_queue import TaskQueue
    ns.task_queue = TaskQueue(session=ns.session, runner=None)
    try:
        ns.approval = ApprovalProvider(session=ns.session, bus=ns.bus)
    except Exception:                          # noqa: BLE001 审批服务装配失败不阻断
        log.warning("approval provider 装配失败,审批降级为无通道自动拒", exc_info=True)


class _ExitFlag:
    """外壳退出旗标(commands.cmd_exit 经 ctx.shell.request_exit(0) 副作用表达退出;
    interactive_loop 消费 .exit_code,见 commands.py.md 偏离 6)。"""

    def __init__(self) -> None:
        self.exit_code: Optional[int] = None

    def request_exit(self, code: int = 0) -> None:
        self.exit_code = int(code)


def _sessions_path(cfg: Any) -> Path:
    return Path(cfg.storage.sessions_dir).expanduser()


def _attach_log_persistence(bus: Any, log_: Any, store: Any) -> None:
    """总线 → 存储订阅(事件一入内存即入真源队列;tests/unit/test_agent.py
    _wired_log 同款 idiom,owner="persistence" 与外壳渲染订阅隔离)。

    落盘双速:强同步三类/显式 sync 经 store.append(sync=True) 即写即刷;普通事件
    入攒批,由后续强同步点 flush(seq) 或收尾全量 flush 落盘(F011 双速写)。
    SessionLog 构造时 bus=None(open_session 面),装配后回填 _bus 并先订后写。
    """
    from pyharness.events import EVENT_TYPES
    from pyharness.events.vocab import SYNC_TYPES as _EVT_SYNC

    async def _record(type_: str, payload: Any) -> None:
        env = payload                          # SessionLog._dispatch 以 Envelope 为载荷
        await store.append(env, sync=type_ in _EVT_SYNC)

    for t in EVENT_TYPES:                      # 词表 57 类型逐类型订阅(精确命中)
        bus.subscribe(t, _record, owner="persistence")
    log_._bus = bus                            # 回填总线:append 即分发即落盘


async def _open_log(cfg: Any, sid: str, bus: Any) -> Any:
    """重放重建会话(核心路径:store 追加句柄 + open_session 全量回放 + 总线落盘
    订阅;坏行由回放记跳 PERS-201 隔离,不中断)。"""
    from pyharness.core.session import open_session
    from pyharness.persistence import open_store
    store = open_store(sid, dir=_sessions_path(cfg))
    log_ = await open_session(sid, store)
    _attach_log_persistence(bus, log_, store)
    return log_


async def _new_session(cfg: Any, bus: Any) -> Any:
    """开新会话:随机 sid → store → session.created 首事件(seq=1,EVT-106 引导);
    先接总线落盘订阅再写首事件(created 亦入真源)。"""
    from pyharness.core.session import open_session
    from pyharness.persistence import open_store
    sid = f"s-{uuid.uuid4().hex[:12]}"          # Envelope session_id min_length=8
    store = open_store(sid, dir=_sessions_path(cfg))
    log_ = await open_session(sid, store)       # 空日志回放(文件刚建)
    _attach_log_persistence(bus, log_, store)
    await log_.append("session.created",
                      {"title": "", "model": cfg.llm.model},
                      actor="system", sync=True)
    return log_


async def _flush_session(sh: ShellCtx) -> None:
    """收尾落盘:会话存储攒批全量 flush(强同步锚点外的普通事件在退出前落盘;
    F011 双速写——真源不可丢)。引擎定时 flush 任务装配后此调用仍幂等安全。"""
    log_ = getattr(sh.ctx, "session", None)
    if log_ is None:
        return
    persist = getattr(log_, "_persistence", None)
    fl = getattr(persist, "flush", None)
    if not callable(fl):
        return
    try:
        res = fl()
        if hasattr(res, "__await__"):
            await res
    except Exception:                          # noqa: BLE001 收尾刷盘失败不掩盖退出码
        log.warning("会话收尾 flush 失败(事件已入攒批,疑磁盘问题)", exc_info=True)


async def _scan_unhealthy(sessions_dir: Path) -> Optional[str]:
    """启动自检(F060):pyharness.repair.auto_scan 装配后取首个不健康会话;模块未
    装配 → 降级跳过(记日志),不阻断启动(见偏离 7)。"""
    try:
        from pyharness.repair import auto_scan
    except Exception as exc:                   # noqa: BLE001 repair 模块未装配
        log.info("repair.auto_scan 未装配,跳过启动自检 (%s)", exc)
        return None
    try:
        hits = await auto_scan(sessions_dir)
    except PyHError:
        raise
    except Exception:                          # noqa: BLE001 自检失败不阻断启动
        log.warning("启动自检异常,跳过", exc_info=True)
        return None
    for h in hits:
        if not getattr(h, "healthy", False):
            return getattr(h, "sid", None)
    return None


def _first_unhealthy(hits: list) -> Optional[Any]:
    """从 auto_scan 结果取首个不健康记录(无 healthy 面视为不健康——auto_scan
    只返损坏清单时全数命中)。"""
    for h in hits:
        if not getattr(h, "healthy", True):
            return h
    return None


async def _ensure_repair(ctx: Any, sid: str) -> None:
    """repair 前置(幂等):损坏则先 repair 再 open;模块未装配 → 跳过,坏行由回放
    PERS-201 记跳(见偏离 7)。不可修复 → PERS-201 上抛(原文件已备份留存)。"""
    try:
        from pyharness.repair import repair_session
    except Exception as exc:                   # noqa: BLE001 未装配降级
        log.info("repair_session 未装配,跳过会话前置修复 (%s)", exc)
        return
    try:
        await repair_session(ctx, sid, interactive=False)
    except PyHError as e:
        if e.code.startswith("PERS-201"):
            raise                                  # 不可修复:明确报错+备份留存
        log.warning("repair_session sid=%s 异常:%s", sid, e.code)
        raise


async def bootstrap_shell(r: ParseResult) -> ShellCtx:
    """启动序 + headless 判定:config → ctx 装配 →(repair 前置:显式 --session 或
    自检命中)→ 会话开/建 → channel 注入(stdin.isatty() 且 cmd∈{chat,plan} →
    "cli",否则 None=headless,R8 安全默认)。"""
    cfg = _load_settings(r.flags.get("config"))     # CFG-601 失败即中止,不装配
    sessions_dir = _sessions_path(cfg)
    sid: Optional[str] = r.flags.get("session")
    cmd = r.cmd
    ctx: SimpleNamespace = assemble_ctx(cfg)

    # 会话解析:chat/run 无 --session → 启动自检(F060)命中损坏则修复续跑,否则新建;
    # chat/run/plan 显式 --session → 缺日志 EVT-106 守卫 + 损坏先修再 open(幂等);
    # 其余命令(repair/desktop/acp/引擎单发)不预开会话,由各自处理器按需装配。
    if sid is None and cmd in {"chat", "run"}:
        bad = await _scan_unhealthy(sessions_dir)   # repair 模块未装配时跳过
        if bad is not None:
            sid = bad                               # 命中损坏会话 → 修复并续跑
    if sid is not None and cmd in {"chat", "run", "plan"}:
        path = sessions_dir / f"{sid}.jsonl"
        if not path.exists():
            raise_code("EVT-106", sid=sid,
                       hint="会话不存在(日志缺失);先 `session list` 确认 sid,"
                            "或省略 --session 开启新会话")
        await _ensure_repair(ctx, sid)              # 损坏先修再 open(F060)
        ctx.session = await _open_log(cfg, sid, ctx.bus)
        _wire_queue(ctx)
    elif sid is None and cmd in {"chat", "run"}:
        # 新建会话:run/chat 是会话产生者(引擎后续只在已开会话上跑任务)
        ctx.session = await _new_session(cfg, ctx.bus)
        _wire_queue(ctx)

    interactive_cmd = cmd in INTERACTIVE_CMDS and not r.flags.get("once")
    headless = not (sys.stdin.isatty() and interactive_cmd)
    ctx.channel = None if headless else "cli"       # R8:无通道即安全默认
    return ShellCtx(ctx=ctx, loop=asyncio.get_running_loop(),
                    channel=ctx.channel, headless=headless, flags=r.flags)


# ---------------------------------------------------------------- 交互主循环
def install_two_stage_sigint(sh: ShellCtx) -> dict:
    """两段式 SIGINT:第 1 次置位并 raise _CancelledRound(取消当前轮);第 2 次
    SystemExit(130)。Windows 同语义(禁 SIGKILL);返回 state 供测试取 handler。"""
    state: dict[str, Any] = {"armed": False}

    def handler(signum: int, frame: Any) -> None:
        if not state["armed"]:
            state["armed"] = True
            print("\n[Ctrl-C] 再按一次退出(取消当前轮)", file=sys.stderr)
            raise _CancelledRound()
        raise SystemExit(EXIT_INTERRUPT)

    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):                  # 非主线程/平台限制:降级不装
        log.warning("SIGINT handler 安装失败(非主线程?),Ctrl-C 语义降级", exc_info=True)
    state["handler"] = handler
    return state


def _shell_exit_code(sh: ShellCtx) -> Optional[int]:
    """ctx.shell 退出旗标读取(commands.cmd_exit 置位后消费)。"""
    shell = getattr(sh.ctx, "shell", None)
    code = getattr(shell, "exit_code", None) if shell is not None else None
    return code if code is not None else None


async def interactive_loop(sh: ShellCtx, *, once: Any = False) -> int:
    """chat 交互主循环:多行输入(`\\` 续行)、斜杠命令交 handle_slash(零 LLM)、
    普通文本交 task_queue.submit 并流式渲染、审批交互弹提示;Ctrl-C 一次取消当前
    轮、两次退出(130);once 真值 = 单轮即返(--once 非交互注入,见偏离 6)。"""
    state = install_two_stage_sigint(sh)
    once_round = bool(once)
    while True:
        try:
            raw = await read_user_input(sh, once=once_round)
            if raw is None:                          # EOF(Ctrl-D)→ 退出 0
                return 0
            if raw.startswith("/"):                  # 斜杠:不给 LLM 的控制通道(F041)
                cont = await commands.handle_slash(raw, sh.ctx)
                code = _shell_exit_code(sh)
                if code is not None:                 # /exit 经 ctx.shell 置位
                    return code
                if not cont:                         # 兼容旧面 false=退出
                    return 0
                if once_round:
                    return 0
                continue
            await _submit_wait_report(sh, raw)       # 文本意图:入队+渲染+终态
            if once_round:
                return 0                             # 单轮模式:处理完一轮即返
        except _CancelledRound:
            state["armed"] = False                   # 重布防:下一轮再按 = 取消该轮
            print("\n[已取消当前轮,再按 Ctrl-C 退出]", file=sys.stderr)
            continue                                 # 回循环顶(等价伪码递归,见偏离 5)
        except PyHError as e:                        # 引擎错误回显后回循环(交互韧性)
            _emit_error(e, json_mode=bool(sh.flags.get("json")))
            continue
        except KeyboardInterrupt:                    # 兜底(信号未达 handler 的路径)
            return EXIT_INTERRUPT


async def _submit_wait_report(sh: ShellCtx, raw: str) -> None:
    """一轮文本意图:submit → 订阅渲染 → wait_for 终态;失败摘要回显(引擎装配后
    事件由渲染器呈现,此处只兜终态失败)。"""
    q = getattr(sh.ctx, "task_queue", None)
    if q is None:
        raise_code("CYC-999", hint="任务队列未装配(runner 注入点待引擎装配)")
    meta = {"channel": sh.ctx.channel or "headless"}
    task_id = await q.submit(raw, meta=meta)
    res = await _wait_and_render(sh, task_id)
    if res is not None and not _result_ok(res):
        _emit_plain(f"[任务失败] {_result_code(res) or 'CYC-999'}"
                    f" {_result_summary(res)}", err=True)


async def read_user_input(sh: ShellCtx, *, once: bool = False) -> Optional[str]:
    """多行读取 + once 直通:once → --once 文本或管道全文;stdin 非 tty → 整读
    一次性消费(headless run/chat 语义);tty → 逐行,行尾 `\\` 续行,空输入重提示;
    EOF(Ctrl-D) → None。"""
    if once:
        txt = sh.flags.get("once") or sys.stdin.read()
        return (txt or "").strip() or None
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        return data.strip() or None                  # headless 一次性消费
    lines: list[str] = []
    json_mode = bool(sh.flags.get("json"))
    while True:
        _write_prompt(sh, json_mode=json_mode)
        try:
            line = await asyncio.to_thread(input, "")
        except EOFError:
            return None                              # Ctrl-D:净退
        except KeyboardInterrupt:
            raise                                    # 两段式向上冒泡(130)
        if not line:
            if not lines:
                continue                             # 空输入重提示
            break                                    # 续行中空行 = 提交
        if line.endswith("\\"):
            lines.append(line[:-1])                  # 续行:去尾 \ 拼接
            continue
        lines.append(line)
        break
    return "\n".join(lines).strip() or None


def _write_prompt(sh: ShellCtx, *, json_mode: bool) -> None:
    """提示符出口:json 模式人类提示禁入 stdout,改走 stderr(机器流纯净)。"""
    print(PROMPT, end="", flush=True,
          file=sys.stderr if json_mode else sys.stdout)


# ---------------------------------------------------------------- 单发任务
async def _wait_and_render(sh: ShellCtx, task_id: str) -> Any:
    """wait_for 终态 + 并行总线渲染:渲染子任务随 wait 返回强制取消(终态事件或
    取消兜底双通道,杜绝悬挂订阅)。"""
    render = None
    bus = getattr(sh.ctx, "bus", None)
    if bus is not None:
        render = asyncio.create_task(_render_events(sh, task_id))
    try:
        return await sh.ctx.task_queue.wait_for(task_id)
    finally:
        if render is not None:
            render.cancel()
            try:
                await render
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 渲染清理
                pass


async def run_intent(sh: ShellCtx, text: str, *, headless: bool) -> int:
    """run 单发任务:无人值守单轮 submit → 阻塞至终态 → 报告结果/拒绝清单;
    headless 下 channel=None 使审批自动拒(APR-501)与 critical 直拒(GRD-401)
    天然生效(R8)。返回 0/1。"""
    if not text:
        raise_code("EVT-100", advice="run 需要任务文本(stdin 或参数)",
                   hint="用法:pyharness run \"任务文本\" 或管道喂入")
    sh.ctx.channel = None if headless else "cli"     # 无审批通道即安全默认(R8)
    q = getattr(sh.ctx, "task_queue", None)
    if q is None:
        raise_code("CYC-999", hint="任务队列未装配(runner 注入点待引擎装配)")
    task_id = await q.submit(text, meta={"channel": sh.ctx.channel})
    res = await _wait_and_render(sh, task_id)
    rejected = _rejected_list(res)                   # guard.rejected 拒绝明细(G4)
    if sh.flags.get("json"):
        _json_line({"task_id": task_id,
                    "result": {"summary": _result_summary(res),
                               "code": _result_code(res) or None,
                               "reason": _result_reason(res) or None},
                    "rejected": [str(r) for r in rejected]})
    else:
        print(_result_summary(res))
        for r in rejected:                           # 拒绝清单逐条(含 headless 拒)
            print(f"[拒绝] {r}")
    reason = _result_reason(res)
    if reason in {"complete", "cancelled_by_user"}:
        return 0
    if _result_ok(res):
        return 0
    return 1                                         # 引擎错误终态(reason=error)


async def run_once_sub(sh: ShellCtx, cmd: str, positional: list[str],
                       flags: dict) -> int:
    """plan/schedule/job/search/session/fork 统一单发路径:经 ctx.handlers 注入的
    引擎侧处理器(装配面;specs/cli.py.md run_once_sub 伪码未展开,外壳只做分发与
    参数透传——见模块 docstring 偏离 11/12)。未装配 → CYC-999 明确报错不静默。"""
    handlers = getattr(sh.ctx, "handlers", None) or {}
    fn = handlers.get(cmd)
    if fn is None:
        raise_code("CYC-999", cmd=cmd,
                   detail=f"引擎单发处理器未装配(handlers.{cmd} 注入点保留;"
                          f"依赖引擎装配层,同批后续落地)")
    res = fn(sh, positional, flags)
    if hasattr(res, "__await__"):
        res = await res
    return int(res) if res is not None else 0


# ---------------------------------------------------------------- 事件渲染
async def _render_events(sh: ShellCtx, task_id: str) -> None:
    """事件流渲染:订阅 llm.chunk(瞬时)/agent.message/tool.call/guard.rejected/
    approval.* 等;text 模式增量打字机 + 卡片,approval 触发交互提示;json 模式
    逐事件一行 JSONL 到 stdout(机器可读事件流)。终态事件或外部取消即收尾。"""
    renderer = StreamRenderer(mode="json" if sh.flags.get("json") else "text")
    q: asyncio.Queue = asyncio.Queue()
    bus = getattr(sh.ctx, "bus", None)
    if bus is None:
        return                                      # 无总线:渲染退化为摘要兜底
    subscriptions = []
    try:
        for pattern in _RENDER_PATTERNS:
            sub = bus.subscribe(pattern, lambda t, p: q.put_nowait((t, p)),
                                owner="cli")
            subscriptions.append(sub)
        while True:
            type_, payload = await q.get()
            if type_.startswith("approval."):
                await prompt_approval(sh, type_, payload)   # 审批交互(F015)
                continue
            if type_ == "llm.chunk":
                renderer.delta(_normalize_payload(payload))
                continue
            renderer.event(type_, payload)          # 卡片/json 行
            if type_ in _END_EVENT_TYPES:
                break
    finally:
        bus.unsubscribe_all("cli")
        renderer.newline()


async def prompt_approval(sh: ShellCtx, type_: str, payload: Any) -> None:
    """审批交互(F015 人类裁决):approval.requested 到达且通道非 None → 打印工具/
    参数摘要与风险级,键盘 [y/N] 裁决 → approve/deny;headless(channel=None)不提示
    ——裁决侧 APR-501 自动拒(零等待,勿悬挂)。"""
    if sh.headless or sh.ctx.channel is None:
        return                                      # 无通道即拒:裁决侧已处理
    payload = _normalize_payload(payload)
    approval = getattr(sh.ctx, "approval", None)
    aid = payload.get("approval_id") or payload.get("id")
    if aid is None:
        return
    ttl = payload.get("ttl_ms", "")
    risk = payload.get("risk", "?")
    tool = payload.get("tool", type_)
    print(f"\n[审批] {tool} {_trunc(str(payload.get('args_summary', '')), 160)}"
          f" (risk={risk}, ttl={ttl}ms)", file=sys.stderr)
    try:
        ans = (await asyncio.to_thread(input, "")).strip().lower()
    except (EOFError, KeyboardInterrupt):           # 用户中止 = 拒绝(等价 deny)
        ans = "n"
    verdict_fn = (approval.approve if ans in {"y", "yes"} else approval.deny)
    if approval is not None and callable(verdict_fn):
        out = verdict_fn(aid, by="cli")
        if hasattr(out, "__await__"):
            await out                              # 裁决入日志(approval.granted/denied)


# ---------------------------------------------------------------- 离线子命令
def offline_cmd(cmd: str, positional: list[str], flags: dict) -> int:
    """离线子命令(config/budget/stats):不装配引擎零网络零 LLM(DEP §5.1 ✅)。
    config validate 失败退 1;未知子动作退 2。"""
    if cmd == "config":
        return _config_cmd(positional, flags)
    if cmd == "budget":
        return _budget_cmd(flags)
    if cmd == "stats":
        return _stats_cmd(flags)
    return 2


def _target_cfg_path(flags: dict) -> Optional[str]:
    """config 子命令目标路径:--config 显式优先,否则 ~/.pyharness/config.yaml。"""
    p = flags.get("config")
    if p:
        return str(p)
    return str(Path("~/.pyharness/config.yaml").expanduser())


def _config_cmd(positional: list[str], flags: dict) -> int:
    """config init/validate/show(秘密只回显 env:/file: 引用,无 --show-secrets)。"""
    sub = (positional or ["show"])[0]
    json_mode = bool(flags.get("json"))
    if sub == "validate":
        errs = config.validate_only(flags.get("config"))   # 非法 → 字段明细(TC-G2)
        if errs:
            for line in errs:
                print(line)
            return 1
        print("ok")
        return 0
    if sub == "show":
        settings = _load_settings(flags.get("config"))
        data = config.render_show(settings)
        if json_mode:
            _json_line(data)
        else:
            for k in sorted(data):
                print(f"{k} = {data[k]}")
        return 0
    if sub == "init":
        path = Path(_target_cfg_path(flags)).expanduser()
        if path.exists():
            print(f"config 已存在:{path}(不覆盖;validate 查看健康度)")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        path.write_text(yaml.safe_dump(config.DEFAULTS, allow_unicode=True,
                                       sort_keys=False),
                        encoding="utf-8")
        print(f"已写入默认配置:{path}(密钥请改用 env:/file: 引用)")
        return 0
    print(f"未知 config 子命令:{sub}(可用:init/validate/show)", file=sys.stderr)
    return 2


def _scan_usage(sessions_dir: Path) -> dict:
    """会话日志只读扫描(离线派生):逐文件数事件、尾部终态与 llm.usage 成本聚合。"""
    files = sorted(sessions_dir.glob("*.jsonl")) if sessions_dir.is_dir() else []
    total_events = 0
    finished = 0
    cost = 0.0
    for f in files:
        tail_type = ""
        n = 0
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue                          # 坏行不计(repair 域)
                tail_type = ev.get("type", "")
                if tail_type == "llm.usage":
                    cost += float((ev.get("payload") or {}).get("cost_est") or 0.0)
        total_events += n
        if tail_type == "session.finished":
            finished += 1
    return {"sessions": len(files), "events": total_events, "finished": finished,
            "cost_est": round(cost, 4)}


def _budget_cmd(flags: dict) -> int:
    """budget 报表(离线,F032 数据):任务硬闸/月度上限(配置锁死)+ 实测成本聚合。"""
    settings = _load_settings(flags.get("config"))
    b = settings.budget
    usage = _scan_usage(_sessions_path(settings))
    data = {
        "task": {"max_cost_yuan": b.task.max_cost_yuan,
                 "max_in_tokens": b.task.max_in_tokens,
                 "max_out_tokens": b.task.max_out_tokens,
                 "warn_ratio": b.warn_ratio},
        "monthly": {"limit_yuan": b.monthly.limit_yuan,
                    "alert_ratio": b.monthly.alert_ratio},
        "usage": {"cost_est": usage["cost_est"],
                  "sessions_scanned": usage["sessions"]},
    }
    if flags.get("json"):
        _json_line(data)
        return 0
    lim = b.monthly.limit_yuan
    limit_txt = f"¥{lim:.2f}(未启用)" if lim <= 0 else f"¥{lim:.2f}"
    print(f"任务预算硬闸: ¥{b.task.max_cost_yuan:.2f}/任务 "
          f"(in≤{b.task.max_in_tokens:,} out≤{b.task.max_out_tokens:,} tok, "
          f"告警 {b.warn_ratio:.0%})")
    print(f"月度上限: {limit_txt}(告警 {b.monthly.alert_ratio:.0%})")
    print(f"实测估算: ¥{usage['cost_est']:.4f}({usage['sessions']} 个会话)")
    return 0


def _stats_cmd(flags: dict) -> int:
    """stats 会话统计(只读派生):会话数/事件数/终态数。"""
    settings = _load_settings(flags.get("config"))
    data = _scan_usage(_sessions_path(settings))
    if flags.get("json"):
        _json_line({"sessions": data["sessions"], "events": data["events"],
                    "finished": data["finished"],
                    "sessions_dir": str(_sessions_path(settings))})
        return 0
    print(f"会话数: {data['sessions']}")
    print(f"事件总数: {data['events']}")
    print(f"已终态会话: {data['finished']}")
    print(f"日志目录: {_sessions_path(settings)}")
    return 0


# ---------------------------------------------------------------- repair 桥
async def repair_cmd(sh: ShellCtx, sid: Optional[str] = None) -> int:
    """repair 子命令桥(F060):对 sid(缺省 = 自动扫描首损)执行修复管线;报告输出;
    不可修复 → 明确报错 + 备份留存(PERS-201 上抛)。"""
    from pyharness.repair import auto_scan, repair_session     # 惰性(偏离 7)
    if not sid:
        hits = await auto_scan(sh.ctx.storage.sessions_dir)
        hit = _first_unhealthy(hits)
        sid = getattr(hit, "sid", None) if hit is not None else None
    if not sid:
        print("[repair] 无损坏会话")
        return 0
    interactive = sys.stdin.isatty()
    report = await repair_session(sh.ctx, sid, interactive=interactive)
    if sh.flags.get("json"):
        _json_line(_report_dict(report, sid))
    else:
        print(f"[repair] {sid}: fixed={_report_field(report, 'fixed')} "
              f"lost={_report_field(report, 'lost', 0)} "
              f"backup={_report_field(report, 'backup_path', '')} "
              f"quarantined={_report_field(report, 'quarantined', 0)}")
    return 0


def _report_field(report: Any, name: str, default: Any = "") -> Any:
    """报告字段鸭子读取:list → len(fixed/quarantined 为动作清单或计数);缺失回落
    默认(见偏离 12)。"""
    val = getattr(report, name, None)
    if val is None:
        val = default
    if isinstance(val, (list, tuple, set)):
        return len(val) if name in {"fixed", "lost", "quarantined"} else val
    return val


def _report_dict(report: Any, sid: str) -> dict:
    """报告 → dict(json 出口):优先 asdict()/dataclasses;否则显式字段拼装
    (json 保留原始形状:fixed/quarantined 动作清单或行号,与 repair 模块报告面
    一致;文本出口才用计数,见 _report_field)。"""

    def _raw(name, default):
        val = getattr(report, name, None)
        return default if val is None else val

    fn = getattr(report, "asdict", None)
    if callable(fn):
        try:
            out = dict(fn())
            out["sid"] = sid
            return out
        except Exception:                            # noqa: BLE001
            pass
    import dataclasses as _dc
    if _dc.is_dataclass(report):
        out = _dc.asdict(report)
        out["sid"] = sid
        return out
    return {"sid": sid,
            "fixed": _raw("fixed", []),
            "lost": _raw("lost", 0),
            "backup_path": _raw("backup_path", None),
            "quarantined": _raw("quarantined", [])}


# ---------------------------------------------------------------- 码映射
def exit_code_for(e: PyHError) -> int:
    """引擎码 → 进程码(码域内一律 1;用法错误由 argparse 退 2,此处不覆盖;
    中断 130 由 KeyboardInterrupt 路径负责)。"""
    return 130 if isinstance(e, KeyboardInterrupt) else 1


__all__ = [
    # 数据结构
    "ParseResult", "ShellCtx", "ExitResult", "StreamRenderer", "CmdEntry",
    "CMD_TABLE", "_CancelledRound",
    # 函数清单(contract 面)
    "main", "parse_args", "cli_main", "bootstrap_shell", "interactive_loop",
    "read_user_input", "run_intent", "offline_cmd", "repair_cmd",
    "_render_events", "prompt_approval", "install_two_stage_sigint",
    "exit_code_for", "assemble_ctx", "run_once_sub", "_run_text",
]
