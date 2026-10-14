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
import signal
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from pyharness import config as config
from pyharness.core import commands
from pyharness.errors import PyHError, raise_code



# 文本模式渲染卡片的事件类型(其余事件 json 模式全量、text 模式静默)
# 渲染终止事件(引擎终态或会话终态;渲染任务另由 wait_for 返回后强制取消兜底)
# 总线订阅面:精确 + 段通配(真实总线 EventBus 的 _wild_match 段前缀匹配)










from pyharness.cli_output import (log, PROMPT, EXIT_INTERRUPT, OFFLINE_CMDS, SHELL_CMDS, INTERACTIVE_CMDS, _TEXT_CARD_TYPES, _END_EVENT_TYPES, _RENDER_PATTERNS, _CancelledRound, ParseResult, ShellCtx, ExitResult, StreamRenderer, _normalize_payload, _trunc, _text_card, _json_line, _emit_plain, _emit_error, _result_ok, _result_code, _result_summary, _result_reason, _rejected_list, _wait_and_render, _render_events, prompt_approval, _report_field, _report_dict, exit_code_for)
from pyharness.cli_parser import (_add_global_flags, parse_args, _positional_of, CmdEntry, CMD_TABLE, _args_json, _run_text)
from pyharness.cli_settings import (_load_settings)
from pyharness.cli_sessions import (_ExitFlag, _sessions_path, _flush_session, _scan_unhealthy, _repairable_unhealthy, _ensure_repair)
from pyharness.cli_history import (_sessions_dir_ctx, _session_lines, _cmd_search, _cmd_session, _cmd_fork)
from pyharness.cli_orchestration import (_cmd_job, _cmd_schedule, _cmd_plan as _plan_command, _cmd_workflow as _workflow_command)
from pyharness.cli_maintenance import (_skill_cmd, _target_cfg_path, _scan_usage, _budget_cmd, _stats_cmd, repair_cmd)

from pyharness.cli_runtime import (_wire_queue)

async def _attach_engine(sh: ShellCtx) -> None:
    """CLI 真引擎装配:把轻量门面换成 spine + runner + 真实 TaskQueue。

    bootstrap_shell 只做会话/权限门面;chat/run/plan 真正干活前必须把
    engine 装配进来(原设计留了 ctx.handlers/runner 注入点但从未接线)。
    """
    ctx = getattr(sh, "ctx", None)
    if ctx is None:
        raise_code("CYC-999", hint="ShellCtx 缺 ctx,无法装配引擎")
    if getattr(ctx, "engine_spine", None) is not None:
        return
    from pyharness import engine as _eng
    log_ = getattr(ctx, "session", None)
    if log_ is None:
        raise_code("CYC-999", hint="引擎装配前无会话(chat/run/plan 应先开/建会话)")
    channel = "cli" if not getattr(sh, "headless", True) else None
    # The bootstrap provider owns verdict subscriptions on this same bus.
    # Close it before replacing it so only the runtime receives its verdicts.
    bootstrap_approval = getattr(ctx, "approval", None)
    if bootstrap_approval is not None:
        await bootstrap_approval.aclose()
    await _eng.attach_engine_to_ctx(
        ctx, ctx.settings, log_=log_,
        sessions_dir=Path(getattr(getattr(ctx, "storage", None),
                                  "sessions_dir", ".")).expanduser(),
        bus=ctx.bus,
        store=getattr(log_, "_persistence", None),
        channel=channel)


def _attach_log_persistence(bus: Any, log_: Any, store: Any) -> None:
    """总线 → 存储订阅(**委托唯一实现** ``persistence.session_recorder``)。

    2026-09-21 R9:此前这里是**第三份**各自实现 —— 属主固定 ``"persistence"``(多会话
    同总线时"按属主摘除"会互相误伤)且**无 sid 过滤**(总线上他会话事件会被写进本会话
    store,同 R8-1 在 engine 侧修掉的缺陷)。现统一:属主 ``persistence:{sid}`` +
    过滤在 ``session_recorder`` 单点。

    SessionLog 构造时 bus=None(open_session 面),装配后回填 _bus 并先订后写。
    """
    from pyharness.events import EVENT_TYPES
    from pyharness.persistence import session_recorder

    sid = str(getattr(log_, "sid", "") or "")
    record = session_recorder(store, sid)
    for t in EVENT_TYPES:                      # 词表逐类型订阅(精确命中)
        bus.subscribe(t, record, owner=f"persistence:{sid}")
    log_._bus = bus                            # 回填总线:append 即分发即落盘


async def _open_log(cfg: Any, sid: str, bus: Any) -> Any:
    """重放重建会话(核心路径:store 追加句柄 + open_session 全量回放 + 总线落盘
    订阅;坏行由回放记跳 PERS-201 隔离,不中断)。"""
    from pyharness.core.session import open_session
    from pyharness.persistence import flush_kwargs_of, open_store
    store = open_store(sid, dir=_sessions_path(cfg), **flush_kwargs_of(cfg))
    log_ = await open_session(sid, store)
    _attach_log_persistence(bus, log_, store)
    return log_


async def _new_session(cfg: Any, bus: Any) -> Any:
    """开新会话:随机 sid → store → session.created 首事件(seq=1,EVT-106 引导);
    先接总线落盘订阅再写首事件(created 亦入真源)。"""
    from pyharness.core.session import open_session
    from pyharness.persistence import flush_kwargs_of, open_store
    sid = f"s-{uuid.uuid4().hex[:12]}"          # Envelope session_id min_length=8
    store = open_store(sid, dir=_sessions_path(cfg), **flush_kwargs_of(cfg))
    log_ = await open_session(sid, store)       # 空日志回放(文件刚建)
    _attach_log_persistence(bus, log_, store)
    await log_.append("session.created",
                      {"title": "", "model": cfg.llm.model},
                      actor="system", sync=True)
    return log_


def offline_cmd(cmd: str, positional: list[str], flags: dict) -> int:
    """离线子命令(config/budget/stats/skill):不装配引擎零网络零 LLM(DEP §5.1 ✅)。
    config validate 失败退 1;未知子动作退 2。"""
    if cmd == "config":
        return _config_cmd(positional, flags)
    if cmd == "budget":
        return _budget_cmd(flags)
    if cmd == "stats":
        return _stats_cmd(flags)
    if cmd == "skill":
        return _skill_cmd(positional, flags)
    return 2


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
            await _attach_engine(sh)           # 真实引擎:浅门面 → 可跑执行路径
            return await interactive_loop(sh, once=r.flags.get("once"))
        if r.cmd == "run":
            text = await _run_text(sh, r.positional)
            if not text:
                raise_code("EVT-100", advice="run 需要任务文本(stdin 或参数)",
                           hint="用法:pyharness run \"任务文本\" 或管道喂入")
            await _attach_engine(sh)
            return await run_intent(sh, text, headless=sh.headless)
        if r.cmd == "repair":
            return await repair_cmd(sh, r.flags.get("session"))    # F060 桥
        if r.cmd == "desktop":
            from pyharness.desktop import run_desktop     # F065 交权(惰性:同批任务)
            return await run_desktop(sh.ctx)
        if r.cmd == "acp":
            from pyharness.desktop.sessions import DesktopSessionManager
            sh.ctx.session = DesktopSessionManager(
                dir=_sessions_path(sh.ctx.settings),
                bus=sh.ctx.bus, config=sh.ctx.settings)   # ACP 真会话门面
            mod = __import__("pyharness.acp", fromlist=["serve", "AcpBridge"])
            serve = getattr(mod, "serve", None)
            if serve is None:                            # AcpBridge 旧面兜底(偏离 7)
                bridge = getattr(mod, "AcpBridge")(sh.ctx, channel="acp:cli")
                return await bridge.serve()
            return await serve(sh.ctx, client_id="acp:cli")
        # plan/schedule/job/search/session/fork:统一"装配+执行+渲染"单发路径
        sh.ctx.handlers = _build_once_handlers(sh)
        return await run_once_sub(sh, r.cmd, r.positional, r.flags)
    finally:
        await _flush_session(sh)               # 收尾落盘:普通事件攒批全量刷盘(F011)




def assemble_ctx(cfg: Any, *, session: Any = None) -> SimpleNamespace:
    """轻量门面装配(见偏离 11):settings/bus/session/task_queue/approval/shell
    (退出旗标)/storage/handlers(单发处理器注入面)。引擎脊柱八模块注入点保留,
    同批后续任务在此接线。"""
    from pyharness.bus import EventBus, bus_kwargs_of
    bus = EventBus(**bus_kwargs_of(cfg))     # F005 背压阈值由装配层注入(N1)
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
        bad = await _scan_unhealthy(sessions_dir, cfg=cfg)  # repair 模块未装配时跳过
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
    session = getattr(sh.ctx, "session", None)
    if session is not None:
        await session.append(
            "user.message", {"content": raw}, actor="user", origin="cli",
            sync=True)
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
    session = getattr(sh.ctx, "session", None)
    if session is not None:
        await session.append(
            "user.message", {"content": text}, actor="user", origin="cli",
            sync=True)
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


def _build_once_handlers(sh: ShellCtx) -> dict:
    """真实单发处理器表(plan/schedule/job/search/session/fork)。

    此前 handlers 留空导致 5 个 once 子命令一律 CYC-999;这里把已有模块
    (session_query/plan_mode/schedule/jobs)以只读/管理命令面接进 CLI。
    """
    return {
        "search": _cmd_search,
        "session": _cmd_session,
        "fork": _cmd_fork,
        "job": _cmd_job,
        "schedule": _cmd_schedule,
        "plan": _cmd_plan,
        "workflow": _cmd_workflow,
    }










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


async def _cmd_plan(sh: ShellCtx, positional: list[str], flags: dict) -> int:
    return await _plan_command(sh, positional, flags, new_session=_new_session, attach_engine=_attach_engine)


async def _cmd_workflow(sh: ShellCtx, positional: list[str], flags: dict) -> int:
    return await _workflow_command(sh, positional, flags, new_session=_new_session, attach_engine=_attach_engine)
