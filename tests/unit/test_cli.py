"""pyharness/cli.py 单测 — 契约:specs/cli.py.md(F064/F065/F066/F060 权威)+ DEP §5
(子命令总表/旗标/退出码/Ctrl-C 语义)+ ERR.md(错误只回 code+advice,禁现场造码)+
CFG.md §3.8(R8 headless 由通道判定)+ commands.py.md(F041 斜杠面,退出经
ctx.shell.request_exit 副作用)。

覆盖面(任务要求 + 规格模块级测试):
    14 叶子子命令分发解析(chat/run/plan/schedule/job/search/session/fork/
        repair/desktop/acp/config/budget/stats);全局旗标前/后置均合法
    未知子命令/缺参 → SystemExit(2)(argparse 惯例,不造码);--help → 0
    cli_main 分发命中:chat/run/repair/desktop/acp/offline/引擎单发(run_once_sub)
    headless 判定(R8):stdin 非 tty → channel=None;run_intent headless 覆盖
        ctx.channel=None(submit meta 带 None),交互通道 → "cli"
    退出码:0 成功 / 1 引擎错误(带 F019 码文本)/ 2 用法 / 130 中断(SystemExit)
    --json:run 终态摘要 JSONL / chat 事件流 JSONL(stdout 只出结构化行)/
        config show / budget / stats / repair 报告
    run 空文本 → EVT-100 退 1;任务失败终态 → 退 1;拒绝清单打印(R8 明细)
    交互循环:斜杠命令零 LLM 分发(/help 不 submit;/exit 置退出旗标退 0;
        未知斜杠 EVT-105 落 system.error 会话继续)、文本 submit+meta、
        管道整读(headless 一次性消费)、--once 单轮、Ctrl-C 一次取消二次
        SystemExit(130)(handler 直测)、EOF → 0
    启动序:chat 新建会话(真实 store)、--session 恢复/缺失 EVT-106、自检
        降级跳过(repair 模块未装配不阻断)
    渲染:llm.chunk 打字机/json 逐行、卡片、终态事件收尾、订阅清理
    审批:headless 不提示(y 批准 / 默认拒绝 → approve/deny 入日志)
    repair 桥:F060 auto_scan 无损坏/命中修复报告(text+json)
    desktop/acp:惰性分发到 run_desktop/serve(参数透传)

装配风格与 test_commands 同:斜杠/渲染用真实 SessionLog(纯内存)+ EventBus;
run/单发路径用 FakeQueue(注入 runner seam 面 submit/wait_for);headless 判定由
sys.stdin 假体(isatty)驱动;CLI 不直写引擎内部(INV-08)。
"""
import asyncio
import json
import signal
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import pyharness.cli as cli
from pyharness.bus import EventBus
from pyharness.core import commands
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError
from pyharness.events.vocab import TRANSIENT_TYPES

SID = "s-cli-0001"          # 会话 id(Envelope session_id min_length=8)
SID2 = "s-cli-0002"
MODEL = "deepseek-chat"
ALL_CMDS = ["chat", "run", "plan", "schedule", "job", "search", "session",
            "fork", "repair", "desktop", "acp", "config", "budget", "stats"]


# ===================================================================== 替身
class FakeResult:
    """任务终态替身(真实 TaskResult: ok/code/summary/duration_ms;附加面:
    reason/events_of/rejected 供 run_intent 拒绝清单与退出码归一)。"""

    def __init__(self, ok=True, reason="complete", summary="已完成", code=None,
                 rejected=None):
        self.ok = ok
        self.reason = reason
        self.summary = summary
        self.code = code
        self.rejected = list(rejected or [])

    def events_of(self, type_):                     # guard.rejected 事件面
        if type_ == "guard.rejected":
            return self.rejected
        return []


class FakeQueue:
    """task_queue seam 替身:submit/wait_for 记录 + 可编程终态结果。"""

    def __init__(self, result=None):
        self.submitted = []                         # (intent, meta)
        self.result = result if result is not None else FakeResult()
        self._n = 0

    async def submit(self, intent, *, meta=None, task_id=None):
        self.submitted.append((intent, dict(meta or {})))
        self._n += 1
        return task_id or f"t-{self._n}"

    async def wait_for(self, task_id):
        return self.result


class FakeApproval:
    """ctx.approval 替身:approve/deny 裁决留痕(强同步写 approval.granted/denied
    的真实职责在引擎,外壳只转发人类裁决)。"""

    def __init__(self):
        self.granted = []
        self.denied = []

    def approve(self, approval_id, *, by):
        self.granted.append((approval_id, by))

    def deny(self, approval_id, *, by):
        self.denied.append((approval_id, by))


class FakeAgent:
    """cmd_new/cmd_exit 生命周期替身(finish_session 落真 session.finished)。"""

    def __init__(self, log):
        self.log = log
        self.finished = []

    async def finish_session(self, reason):
        self.finished.append(reason)
        await self.log.append("session.finished", {"reason": reason},
                              actor="system", sync=True)


class _ScriptedReader:
    """cli.read_user_input 打点替身:按序吐值;None=EOF;异常实例=原样抛出。"""

    def __init__(self, *values):
        self._values = list(values)
        self.calls = 0

    async def __call__(self, sh, *, once=False):
        if not self._values:
            return None
        self.calls += 1
        v = self._values.pop(0)
        if isinstance(v, BaseException):
            raise v
        return v


class _FakeStdin:
    """stdin 假体:isatty 可控 + read 按次弹块(模拟管道逐次消费)。"""

    def __init__(self, *texts, tty=False):
        self._chunks = list(texts)
        self._tty = tty

    def isatty(self):
        return self._tty

    def read(self):
        return self._chunks.pop(0) if self._chunks else ""


@pytest.fixture
def reset_sigint():
    """interactive 测试后还原 SIGINT(防 handler 泄漏影响 pytest 自身 Ctrl-C)。"""
    yield
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    except (ValueError, OSError):
        pass


@pytest.fixture
def tmp_env(monkeypatch, tmp_path):
    """存储三件套指向 tmp(启动序/单测不触碰真实 ~/.pyharness)。"""
    root = tmp_path / "ph"
    monkeypatch.setenv("PH_STORAGE_ROOT", str(root))
    monkeypatch.setenv("PH_STORAGE_SESSIONS_DIR", str(root / "sessions"))
    monkeypatch.setenv("PH_STORAGE_DB_PATH", str(root / "pyharness.db"))
    return root


def _ctx(**kw) -> SimpleNamespace:
    """门面替身(装配面=session/channel/task_queue/approval/agent/shell/bus/
    handlers/storage,与 specs/cli.py.md ShellCtx 注入面同构)。"""
    base = dict(
        settings=SimpleNamespace(),
        bus=EventBus(),
        session=None,
        task_queue=FakeQueue(),
        approval=FakeApproval(),
        agent=None,
        channel="cli",
        shell=cli._ExitFlag(),
        render=None,
        handlers={},
        storage=SimpleNamespace(sessions_dir=Path("/nonexistent")),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _sh(ctx, *, headless=True, flags=None, channel=None) -> cli.ShellCtx:
    if channel is None:
        channel = None if headless else "cli"
    return cli.ShellCtx(ctx=ctx, channel=channel, headless=headless,
                        flags=dict(flags or {}))


async def _new_inmem_session(sid: str = SID, bus=None) -> SessionLog:
    """真实 SessionLog 纯内存(session.created 首事件引导,EVT-106 序)。

    bus 非空时 append → _dispatch → ``bus.emit(type, Envelope)``,与生产装配
    (cli._attach_log_persistence/engine)同款:总线上的对象是信封,不是裸载荷。
    """
    log_ = SessionLog(sid=sid, bus=bus)
    await log_.append("session.created", {"title": "", "model": MODEL},
                      actor="system")
    return log_


# ============================================================== 子命令解析
class TestParseArgs:
    """F064 分发解析:16 叶子全可解析;未知/缺参退 2(--help 0);旗标前后置合法。"""

    @pytest.mark.parametrize("cmd,args", [
        (c, []) for c in ("chat", "run", "schedule", "job", "session", "repair",
                          "desktop", "acp", "config", "budget", "stats",
                          "workflow", "skill")
    ] + [("plan", ["目标"]), ("search", ["备份"]), ("fork", [SID])])
    def test_all_leaf_parsable(self, cmd, args):
        r = cli.parse_args([cmd] + args)
        assert r.cmd == cmd
        assert r.flags["cmd"] == cmd
        assert r.flags["session"] is None and r.flags["json"] is False

    def test_run_text_positional(self):
        r = cli.parse_args(["run", "整理 D:/文件夹"])
        assert r.positional == ["整理 D:/文件夹"]
        assert r.flags["text"] == "整理 D:/文件夹"

    def test_chat_once_flag(self):
        r = cli.parse_args(["chat", "--once", "继续", "--json"])
        assert r.flags["once"] == "继续"
        assert r.flags["json"] is True

    def test_global_flag_after_subcommand(self):
        """DEP §5.1 `repair --session <sid>`:全局旗标可放子命令后(parents 复用)。"""
        r = cli.parse_args(["repair", "--session", SID])
        assert r.flags["session"] == SID
        r2 = cli.parse_args(["--config", "x.yaml", "config", "validate"])
        assert r2.flags["config"] == "x.yaml"

    def test_plan_missing_goal_exit2(self):
        with pytest.raises(SystemExit) as e:
            cli.parse_args(["plan"])
        assert e.value.code == 2

    def test_unknown_command_exit2(self):
        """未知子命令 → 用法错误退出 2(argparse 惯例,不造码)。"""
        with pytest.raises(SystemExit) as e:
            cli.parse_args(["bogus"])
        assert e.value.code == 2

    def test_help_exit0(self):
        with pytest.raises(SystemExit) as e:
            cli.parse_args(["--help"])
        assert e.value.code == 0

    def test_no_command_exit2(self):
        with pytest.raises(SystemExit) as e:
            cli.parse_args([])
        assert e.value.code == 2

    def test_positional_aggregation(self):
        r = cli.parse_args(["session", "show", SID])
        assert r.positional == ["show", SID]
        assert r.flags["positional"] == ["show", SID]     # offline 消费面
        assert cli.parse_args(["fork", SID]).positional == [SID]


# ============================================================== 主入口/退出码
class TestMain:
    """进程入口:用法错误 2 / 引擎错误 1(带 F019 码)/ 成功 0 / --json 错误行。"""

    def test_main_unknown_cmd_exit2(self):
        with pytest.raises(SystemExit) as e:
            cli.main(["bogus"])
        assert e.value.code == 2

    def test_main_run_empty_text_evt100(self, monkeypatch, tmp_env, capsys):
        """run 无文本(stdin 为 tty 且无参)→ EVT-100 退 1(带码,禁裸异常)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        cfg = str(tmp_env / "no.yaml")
        code = cli.main(["run", "--config", cfg])
        assert code == 1
        assert "EVT-100" in capsys.readouterr().err

    def test_main_config_validate_ok(self, tmp_path, capsys):
        cfg = str(tmp_path / "missing.yaml")          # 缺文件 = 回落 L1 默认(合法)
        assert cli.main(["config", "--config", cfg, "validate"]) == 0
        assert capsys.readouterr().out.strip() == "ok"

    def test_exit_code_for(self):
        assert cli.exit_code_for(PyHError("CFG-601")) == 1
        assert cli.exit_code_for(KeyboardInterrupt()) == 130


# ============================================================== cli_main 分发
class TestCliMain:
    """分发总控:F064 各子命令命中对应路径;offline 零装配;desktop/acp 交权。"""

    async def test_offline_dispatch_no_engine(self, tmp_path, capsys):
        """config validate 走离线分支:不装配引擎(无会话/无存储副作用)。"""
        cfg = str(tmp_path / "missing.yaml")
        code = await cli.cli_main(cli.parse_args(["config", "--config", cfg,
                                                  "validate"]))
        assert code == 0
        assert capsys.readouterr().out.strip() == "ok"

    async def test_desktop_dispatch(self, monkeypatch):
        """F065:desktop 惰性交权 run_desktop(ctx)(参数透传,实现在 desktop.py)。"""
        calls = []

        async def run_desktop(ctx):
            calls.append(ctx)
            return 7

        mod = types.ModuleType("pyharness.desktop")
        mod.run_desktop = run_desktop
        monkeypatch.setitem(sys.modules, "pyharness.desktop", mod)
        code = await cli.cli_main(cli.parse_args(["desktop"]))
        assert code == 7
        assert calls and calls[0].settings is not None

    async def test_acp_dispatch(self, monkeypatch):
        """F066:acp 惰性分发 serve(ctx, client_id="acp:cli")(acp.py.md 权威面)。"""
        calls = []

        async def serve(ctx, *, client_id=None):
            calls.append((ctx, client_id))
            return 3

        mod = types.ModuleType("pyharness.acp")
        mod.serve = serve
        monkeypatch.setitem(sys.modules, "pyharness.acp", mod)
        code = await cli.cli_main(cli.parse_args(["acp"]))
        assert code == 3
        assert calls[0][1] == "acp:cli"

    async def test_acp_dispatch_acpbridge_fallback(self, monkeypatch):
        """acp 模块只提供 AcpBridge 类(specs/cli.py.md 旧面)时兜底可用。"""
        class AcpBridge:
            def __init__(self, ctx, *, channel=None):
                self.ctx = ctx
                self.channel = channel

            async def serve(self):
                return 5

        mod = types.ModuleType("pyharness.acp")
        mod.AcpBridge = AcpBridge
        monkeypatch.setitem(sys.modules, "pyharness.acp", mod)
        code = await cli.cli_main(cli.parse_args(["acp"]))
        assert code == 5

    async def test_run_once_sub_handler(self):
        """plan/schedule/job/search/session/fork:统一单发路径走 handlers 注入面。"""
        calls = []

        async def search_handler(sh, positional, flags):
            calls.append((positional, flags["json"]))
            return 0

        ctx = _ctx(task_queue=FakeQueue())
        ctx.handlers = {"search": search_handler}
        sh = _sh(ctx, headless=True, flags={"json": True})
        assert await cli.run_once_sub(sh, "search", ["备份"], {"json": True}) == 0
        assert calls == [(['备份'], True)]

    async def test_run_once_sub_unmounted_engine_error(self):
        """单发处理器未装配 → 引擎错误 CYC-999(明确报错不静默,不吞 success)。"""
        ctx = _ctx()
        sh = _sh(ctx, headless=True)
        with pytest.raises(PyHError) as e:
            await cli.run_once_sub(sh, "job", ["list"], {})
        assert e.value.code == "CYC-999"


# ============================================================== 启动序/headless
class TestBootstrap:
    """启动序:config → 装配 → 会话开/建 → channel 注入(R8 headless 判定)。"""

    def _parsed(self, *args):
        return cli.parse_args(list(args))

    async def test_chat_headless_new_session(self, monkeypatch, tmp_env):
        """stdin 非 tty(headless):channel=None;chat 无 --session 自动开新会话。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
        cfg = str(tmp_env / "no.yaml")
        sh = await cli.bootstrap_shell(self._parsed("chat", "--config", cfg))
        try:
            assert sh.headless is True
            assert sh.channel is None
            assert sh.ctx.channel is None            # R8:无审批通道即安全默认
            assert sh.ctx.session is not None and sh.ctx.session.sid.startswith("s-")
            assert sh.ctx.task_queue is not None     # 队列已接线(session 事件源)
            evs = list(sh.ctx.session.events_after(0))
            assert evs[0].type == "session.created"  # seq=1 首事件引导
        finally:
            if sh.ctx.session is not None:
                sh.ctx.session._persistence.close()

    async def test_chat_tty_channel_cli(self, monkeypatch, tmp_env):
        """stdin tty 且 cmd∈{chat} → channel="cli"(审批/斜杠交互可用)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        cfg = str(tmp_env / "no.yaml")
        sh = await cli.bootstrap_shell(self._parsed("chat", "--config", cfg))
        try:
            assert sh.headless is False
            assert sh.channel == "cli"
        finally:
            sh.ctx.session._persistence.close()

    async def test_run_headless_always(self, monkeypatch, tmp_env):
        """run 单发非交互命令:即使 tty 也 headless(DEP G4 无人值守语义)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        cfg = str(tmp_env / "no.yaml")
        sh = await cli.bootstrap_shell(self._parsed("run", "--config", cfg,
                                                    "任务"))
        try:
            assert sh.headless is True and sh.channel is None
        finally:
            sh.ctx.session._persistence.close()

    async def test_session_resume_opens_existing(self, monkeypatch, tmp_env):
        """--session 恢复:重放既有会话(会话文件先建)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
        cfg = str(tmp_env / "no.yaml")
        # 先经 chat 开一个真实会话拿 sid
        sh0 = await cli.bootstrap_shell(self._parsed("chat", "--config", cfg))
        sid = sh0.ctx.session.sid
        sh0.ctx.session._persistence.close()
        sh1 = await cli.bootstrap_shell(self._parsed("chat", "--session", sid,
                                                     "--config", cfg))
        try:
            assert sh1.ctx.session.sid == sid
            evs = list(sh1.ctx.session.events_after(0))
            assert any(e.type == "session.created" for e in evs)
        finally:
            sh1.ctx.session._persistence.close()

    async def test_session_resume_missing_evt106(self, monkeypatch, tmp_env):
        """--session 指向不存在会话 → EVT-106 守卫(先建后恢复语义,带码不裸抛)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
        cfg = str(tmp_env / "no.yaml")
        with pytest.raises(PyHError) as e:
            await cli.bootstrap_shell(self._parsed("chat", "--session",
                                                   "s-missing-99", "--config", cfg))
        assert e.value.code == "EVT-106"

    async def test_scan_unhealthy_module_missing_skips(self, monkeypatch, tmp_env):
        """repair 模块未装配(同批任务未落地):启动自检降级跳过不阻断(cfg 干净)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
        cfg = str(tmp_env / "no.yaml")
        out = await cli._scan_unhealthy(Path(tmp_env))
        assert out is None

    async def test_scan_unhealthy_hit_then_repair_open(self, monkeypatch, tmp_env):
        """自检命中损坏会话 → 先 repair 再 open 续跑(F060 启动自检联动)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
        cfg = str(tmp_env / "no.yaml")
        # 先建一个真实会话文件(作为"损坏命中"目标)
        sh0 = await cli.bootstrap_shell(self._parsed("chat", "--config", cfg))
        sid = sh0.ctx.session.sid
        sh0.ctx.session._persistence.close()

        repaired = []
        from pyharness.core.session import open_session
        from pyharness.persistence import open_store

        async def fake_repair_session(ctx, sid2, *, interactive=False, policy=None):
            repaired.append(sid2)
            store = open_store(sid2, dir=Path(tmp_env) / "ph" / "sessions")
            log_ = await open_session(sid2, store)
            ctx.session = log_                        # 修复后装回会话(幂等)
            ctx.task_queue = cli._wire_queue(ctx) and ctx.task_queue

        async def fake_auto_scan(d):
            h = SimpleNamespace(sid=sid, healthy=False)
            return [h]

        mod = types.ModuleType("pyharness.repair")
        mod.repair_session = fake_repair_session
        mod.auto_scan = fake_auto_scan
        monkeypatch.setitem(sys.modules, "pyharness.repair", mod)
        sh = await cli.bootstrap_shell(self._parsed("chat", "--config", cfg))
        assert repaired == [sid]
        assert sh.ctx.session.sid == sid
        try:
            sh.ctx.session._persistence.close()
        except Exception:                             # noqa: BLE001 清理不阻断
            pass


# ============================================================== run 单发/R8
class TestRunIntent:
    """run 单发:headless 下 channel=None 使审批自动拒(APR-501)/critical 直拒
    (GRD-401)天然生效(R8);退出码 0/1;--json 终态摘要;拒绝清单明细(G4)。"""

    async def test_run_success_text(self, capsys):
        q = FakeQueue(FakeResult(reason="complete", summary="整理完成"))
        ctx = _ctx(task_queue=q)
        sh = _sh(ctx, headless=True)
        code = await cli.run_intent(sh, "整理文件夹", headless=True)
        assert code == 0
        assert q.submitted[0][0] == "整理文件夹"
        assert q.submitted[0][1]["channel"] is None    # R8 核心:headless 无审批通道
        assert capsys.readouterr().out.strip() == "整理完成"

    async def test_run_interactive_channel_cli(self, capsys):
        q = FakeQueue(FakeResult(reason="complete", summary="完成"))
        ctx = _ctx(task_queue=q)
        sh = _sh(ctx, headless=False)
        code = await cli.run_intent(sh, "任务", headless=False)
        assert code == 0
        assert q.submitted[0][1]["channel"] == "cli"   # 有通道:审批可交互

    async def test_run_engine_error_exit1(self, capsys):
        """引擎错误终态(reason=error)→ 退 1(带 F019 码文本,不吞 success)。"""
        q = FakeQueue(FakeResult(ok=False, reason="error", code="LLM-310",
                                 summary="链尾全败"))
        sh = _sh(_ctx(task_queue=q), headless=True)
        code = await cli.run_intent(sh, "任务", headless=True)
        assert code == 1
        out = capsys.readouterr().out
        assert "链尾全败" in out

    async def test_run_cancelled_by_user_exit0(self):
        q = FakeQueue(FakeResult(ok=False, reason="cancelled_by_user",
                                 code="cancelled", summary="用户取消"))
        sh = _sh(_ctx(task_queue=q), headless=True)
        assert await cli.run_intent(sh, "任务", headless=True) == 0

    async def test_run_empty_text_evt100(self):
        sh = _sh(_ctx(), headless=True)
        with pytest.raises(PyHError) as e:
            await cli.run_intent(sh, "", headless=True)
        assert e.value.code == "EVT-100"

    async def test_run_headless_rejected_listing(self, capsys):
        """R8:headless 危险动作被拒 → 拒绝清单逐条回显,工作区零副作用(INV-05)。"""
        q = FakeQueue(FakeResult(
            reason="complete", summary="其余步骤完成",
            rejected=[{"tool": "fs.delete_file", "risk": "critical",
                       "why": "GRD-401 critical 无审批通道直拒"}]))
        sh = _sh(_ctx(task_queue=q), headless=True)
        code = await cli.run_intent(sh, "删除文件", headless=True)
        assert code == 0                                # 拒绝计入报告,不退出
        out = capsys.readouterr().out
        assert "[拒绝]" in out and "critical" in out

    async def test_run_json_summary(self, capsys):
        """--json:stdout 单行机器可读终态摘要(含 task_id/result/rejected)。"""
        q = FakeQueue(FakeResult(reason="complete", summary="完成",
                                 rejected=[{"tool": "x", "risk": "high"}]))
        sh = _sh(_ctx(task_queue=q), headless=True, flags={"json": True})
        code = await cli.run_intent(sh, "任务", headless=True)
        assert code == 0
        line = json.loads(capsys.readouterr().out.strip())
        assert line["task_id"].startswith("t-")
        assert line["result"]["summary"] == "完成"
        assert line["result"]["reason"] == "complete"
        assert len(line["rejected"]) == 1


# ============================================================== 交互主循环
class TestInteractiveLoop:
    """chat 主循环:斜杠零 LLM 分发、文本 submit+渲染、/exit 退出旗标、
    Ctrl-C 一次取消、EOF→0、--once 单轮。"""

    async def _mk_sh(self, monkeypatch, *, reader, result=None, agent=None):
        log_ = await _new_inmem_session()
        if agent is None:
            agent = FakeAgent(log_)
        ctx = _ctx(session=log_, task_queue=FakeQueue(result or FakeResult()),
                   agent=agent, channel="cli")
        sh = _sh(ctx, headless=False, flags={})
        monkeypatch.setattr(cli, "read_user_input", reader)
        return sh

    async def test_slash_help_no_submit(self, monkeypatch, reset_sigint):
        """斜杠命令零 LLM:/help 不产生 task submit,EOF 净退 0。"""
        reader = _ScriptedReader("/help", None)
        sh = await self._mk_sh(monkeypatch, reader=reader)
        assert await cli.interactive_loop(sh) == 0
        assert sh.ctx.task_queue.submitted == []

    async def test_slash_exit_via_shell_flag(self, monkeypatch, reset_sigint):
        """F041 联动:/exit 经 ctx.shell.request_exit(0) 副作用退出(commands.py.md
        偏离 6 权威面),循环消费退出旗标返回 0;会话落 session.finished。"""
        reader = _ScriptedReader("/exit")
        sh = await self._mk_sh(monkeypatch, reader=reader)
        code = await cli.interactive_loop(sh)
        assert code == 0
        assert sh.ctx.shell.exit_code == 0
        types_ = [e.type for e in sh.ctx.session.events_after(0)]
        assert "user.command" in types_ and "session.finished" in types_

    async def test_unknown_slash_evt105_continue(self, monkeypatch, reset_sigint):
        """未知斜杠 → EVT-105 system.error 留痕,会话继续(零 LLM 零 submit)。"""
        reader = _ScriptedReader("/bogus", "你好", None)
        sh = await self._mk_sh(monkeypatch, reader=reader)
        assert await cli.interactive_loop(sh) == 0
        types_ = [e.type for e in sh.ctx.session.events_after(0)]
        assert "system.error" in types_
        assert "user.command" not in types_            # 未知命令不写 user.command
        assert len(sh.ctx.task_queue.submitted) == 1   # 仅文本"你好"入队

    async def test_text_submit_rendered(self, monkeypatch, reset_sigint):
        """普通文本 → task_queue.submit(meta 带 channel)→ wait_for 终态。"""
        reader = _ScriptedReader("你好世界", None)
        sh = await self._mk_sh(monkeypatch, reader=reader)
        assert await cli.interactive_loop(sh) == 0
        (intent, meta), = sh.ctx.task_queue.submitted
        assert intent == "你好世界"
        assert meta["channel"] == "cli"

    async def test_ctrl_c_cancels_round_then_eof(self, monkeypatch, reset_sigint,
                                                 capsys):
        """Ctrl-C 一次取消当前轮:捕获 _CancelledRound 回循环顶,EOF 净退 0。"""
        reader = _ScriptedReader(cli._CancelledRound(), None)
        sh = await self._mk_sh(monkeypatch, reader=reader)
        assert await cli.interactive_loop(sh) == 0
        assert "[已取消当前轮" in capsys.readouterr().err

    async def test_once_single_round(self, reset_sigint):
        """--once 单轮注入:处理完一轮即返(不重复提交同一文本,偏离 6)。"""
        log_ = await _new_inmem_session()
        ctx = _ctx(session=log_, task_queue=FakeQueue(), agent=FakeAgent(log_),
                   channel="cli")
        sh = _sh(ctx, headless=False, flags={"once": "继续上一轮"})
        code = await cli.interactive_loop(sh, once=sh.flags.get("once"))
        assert code == 0
        assert [i for i, _ in ctx.task_queue.submitted] == ["继续上一轮"]

    async def test_eof_returns_0(self, monkeypatch, reset_sigint):
        reader = _ScriptedReader(None)
        sh = await self._mk_sh(monkeypatch, reader=reader)
        assert await cli.interactive_loop(sh) == 0

    async def test_pipe_stdin_headless_chat(self, monkeypatch, reset_sigint):
        """headless 管道喂词:stdin 整读一次性消费,submit 带 channel=headless。"""
        log_ = await _new_inmem_session()
        ctx = _ctx(session=log_, task_queue=FakeQueue(), agent=FakeAgent(log_),
                   channel=None)
        sh = _sh(ctx, headless=True, flags={})
        monkeypatch.setattr(sys, "stdin",
                            _FakeStdin("夜里喂词 一次消费\n第二行"))
        code = await cli.interactive_loop(sh)
        assert code == 0
        assert len(ctx.task_queue.submitted) == 1
        assert ctx.task_queue.submitted[0][0] == "夜里喂词 一次消费\n第二行"
        assert ctx.task_queue.submitted[0][1]["channel"] == "headless"


class TestSigint:
    """两段式 SIGINT:一次置位 raise _CancelledRound;二次 SystemExit(130)。"""

    def test_first_cancels_second_exits(self, reset_sigint):
        sh = _sh(_ctx(), headless=True)
        state = cli.install_two_stage_sigint(sh)
        handler = state["handler"]
        with pytest.raises(cli._CancelledRound):
            handler(2, None)
        assert state["armed"] is True
        with pytest.raises(SystemExit) as e:
            handler(2, None)
        assert e.value.code == 130


# ============================================================== 输入读取
class TestReadUserInput:
    """多行/续行/EOF/headless 整读/--once 直通。"""

    async def test_once_text(self, tmp_path):
        sh = _sh(_ctx(), headless=True, flags={"once": "继续"})
        assert await cli.read_user_input(sh, once=True) == "继续"

    async def test_once_reads_stdin(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeStdin("管道任务文本"))
        sh = _sh(_ctx(), headless=True, flags={"once": None})
        assert await cli.read_user_input(sh, once=True) == "管道任务文本"

    async def test_headless_full_stdin(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin",
                            _FakeStdin("  第一行\n第二行  "))
        sh = _sh(_ctx(), headless=True)
        assert await cli.read_user_input(sh) == "第一行\n第二行"

    async def test_headless_empty_stdin_none(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeStdin(""))
        sh = _sh(_ctx(), headless=True)
        assert await cli.read_user_input(sh) is None

    async def test_tty_multiline_continuation(self, monkeypatch):
        """tty 逐行:行尾 `\\` 续行拼接;EOF(Ctrl-D) → None。"""
        answers = iter(["第一行\\", "第二行"])

        def fake_input(_prompt=""):
            return next(answers)

        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        monkeypatch.setattr("builtins.input", fake_input)
        sh = _sh(_ctx(), headless=False)
        assert await cli.read_user_input(sh) == "第一行\n第二行"

    async def test_tty_eof_none(self, monkeypatch):
        def fake_input(_prompt=""):
            raise EOFError

        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        monkeypatch.setattr("builtins.input", fake_input)
        sh = _sh(_ctx(), headless=False)
        assert await cli.read_user_input(sh) is None


# ============================================================== 事件渲染
class TestRenderEvents:
    """事件流渲染:text 打字机+卡片;json 逐事件一行 JSONL(stdout 纯净);终态收尾。

    事件经**真实 SessionLog** 投递 → 总线上是 Envelope(与生产 _dispatch 同形)。
    此前替身用裸 dict 直喂总线,形状与生产不一致,掩盖了渲染面按信封字段取值的
    缺陷(工具名/结果/终态理由退化为 ?/{} /None)。瞬时类型(llm.chunk)不经信封,
    按生产(core/llm.py:364)仍走裸 dict。
    """

    _ACTOR = {"llm.chunk": "llm", "agent.message": "agent", "tool.call": "tool",
              "tool.result": "tool", "guard.rejected": "tool",
              "session.finished": "system"}

    async def _feed(self, sh, events):
        task = asyncio.create_task(cli._render_events(sh, "t-1"))
        await asyncio.sleep(0)                          # 订阅就位
        log_ = await _new_inmem_session(bus=sh.ctx.bus)
        for type_, payload in events:
            if type_ in TRANSIENT_TYPES:                # 瞬时:仅总线、裸 dict
                await asyncio.wait_for(asyncio.create_task(
                    sh.ctx.bus.emit(type_, payload)), timeout=5)
            else:                                       # 落盘类:信封上总线
                await asyncio.wait_for(asyncio.create_task(
                    log_.append(type_, payload,
                                actor=self._ACTOR.get(type_, "system"))),
                    timeout=5)
        await asyncio.wait_for(task, timeout=5)         # 终态事件收尾

    async def test_json_event_stream(self, capsys):
        """--json:stdout 每事件一行 JSONL;payload = **内层事件载荷**(非信封)。"""
        sh = _sh(_ctx(), headless=True, flags={"json": True})
        await self._feed(sh, [
            ("llm.chunk", {"delta": "你好"}),
            ("agent.message", {"content": "你好,我是助手"}),
            ("tool.call", {"name": "fs.write_file", "args": {"path": "/x"},
                           "raw_args": {"path": "/x"}, "call_id": "c-1"}),
            ("session.finished", {"reason": "user_command_exit"}),
        ])
        lines = [json.loads(s) for s in
                 capsys.readouterr().out.strip().splitlines()]
        assert [ln["type"] for ln in lines] == ["session.created", "llm.chunk",
                                                "agent.message", "tool.call",
                                                "session.finished"]
        assert lines[1]["payload"]["delta"] == "你好"
        # canonical 形状:工具名在 payload 顶层,信封字段(seq/ts/actor)不混入
        assert lines[3]["payload"]["name"] == "fs.write_file"
        assert "seq" not in lines[3]["payload"]
        assert "actor" not in lines[3]["payload"]

    async def test_text_mode_chunk_cards(self, capsys):
        """text 模式:llm.chunk 增量打字机(无换行)+ 卡片换行;guard 拒绝成卡片。"""
        sh = _sh(_ctx(), headless=False, flags={"json": False})
        await self._feed(sh, [
            ("llm.chunk", {"delta": "流式"}),
            ("llm.chunk", {"delta": "增量"}),
            ("guard.rejected", {"tool": "fs.delete_file", "guard_id": "g-danger",
                                "reason": "GRD-401 critical 直拒"}),
            ("session.finished", {"reason": "done"}),
        ])
        out = capsys.readouterr().out
        assert "流式增量" in out.replace("\n", "")      # chunk 拼接为打字机行
        assert "[拒绝]" in out and "critical" in out

    async def test_text_mode_tool_card_and_agent(self, capsys):
        """text 模式卡片读真实载荷:工具名/参数、结果摘要、终态理由均不退化。"""
        sh = _sh(_ctx(), headless=False, flags={"json": False})
        await self._feed(sh, [
            ("tool.call", {"name": "fs.write_file", "args": {"path": "a.txt"},
                           "raw_args": {"path": "a.txt"}, "call_id": "c-1"}),
            ("tool.result", {"name": "fs.write_file", "call_id": "c-1",
                             "ok": True, "summary": '{"bytes":13}',
                             "truncated": False}),
            ("agent.message", {"content": "已写入 a.txt"}),
            ("session.finished", {"reason": "done"}),
        ])
        out = capsys.readouterr().out
        assert "fs.write_file" in out and "a.txt" in out
        assert '{"bytes":13}' in out                    # 结果摘要非 None
        assert "已写入 a.txt" in out
        assert "[工具] ? {}" not in out and "[结果] None" not in out

    async def test_text_mode_task_completed_reason(self, capsys):
        """task.completed 卡片显示真实 task_id 与完成理由(strict D1 验收面)。"""
        sh = _sh(_ctx(), headless=False, flags={"json": False})
        await self._feed(sh, [
            ("task.completed", {"task_id": "t-1", "reason": "ok"}),
        ])
        out = capsys.readouterr().out
        assert "[任务完成] t-1 ok" in out
        assert "[任务完成] None" not in out


async def _approval_envelope(*, type_: str = "approval.requested", payload=None,
                             bus=None, sid: str = SID):
    """真实 SessionLog 产生的审批事件信封(总线形状 = 生产 _dispatch 同款)。

    approval.requested 按 ApprovalRequestedPayload 四字段构造——**不含**
    approval_id:canonical identity 只由 Envelope.seq 承载(D1b)。
    """
    log_ = await _new_inmem_session(sid=sid, bus=bus)
    if payload is None:
        payload = ({"tool": "exec.shell_run", "args_summary": "command=echo hi",
                    "ttl_ms": 120000, "risk": "high"}
                   if type_ == "approval.requested"
                   else {"approval_id": 2, "by": "system", "ttl_ms": 120000})
    return await log_.append(type_, payload, actor="tool", sync=True)


class TestPromptApproval:
    """审批交互(F015):headless 不提示(裁决侧 APR-501 自动拒,勿悬挂);
    tty 下 y 批准 / 其余默认拒绝 → approve/deny 入日志。

    D1b:事件一律用**真实 SessionLog 产生的 Envelope**(不再手写伪造 approval_id);
    身份断言绑定 env.seq,即生产真实分配的请求事件 seq。
    """

    async def test_headless_no_prompt(self, capsys):
        env = await _approval_envelope()
        appr = FakeApproval()
        sh = _sh(_ctx(approval=appr), headless=True, channel=None)
        await cli.prompt_approval(sh, "approval.requested", env)
        assert appr.granted == [] and appr.denied == []
        assert "[审批]" not in capsys.readouterr().err

    async def test_approve_y(self, monkeypatch, capsys):
        """Scenario 1(approve):identity 取自 Envelope.seq,非载荷字段。"""
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        env = await _approval_envelope()
        appr = FakeApproval()
        sh = _sh(_ctx(approval=appr), headless=False, channel="cli")
        await cli.prompt_approval(sh, "approval.requested", env)
        assert appr.granted == [(env.seq, "cli")]        # = 真实请求事件 seq
        assert appr.denied == []
        assert "risk=high" in capsys.readouterr().err    # 摘要/风险级提示

    async def test_default_deny(self, monkeypatch):
        """Scenario 1(deny):空答复 → deny(env.seq)。"""
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        env = await _approval_envelope()
        appr = FakeApproval()
        sh = _sh(_ctx(approval=appr), headless=False, channel="cli")
        await cli.prompt_approval(sh, "approval.requested", env)
        assert appr.denied == [(env.seq, "cli")]
        assert appr.granted == []

    async def test_input_abort_denies(self, monkeypatch):
        def abort(_prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", abort)
        env = await _approval_envelope()
        appr = FakeApproval()
        sh = _sh(_ctx(approval=appr), headless=False, channel="cli")
        await cli.prompt_approval(sh, "approval.requested", env)
        assert appr.denied == [(env.seq, "cli")]         # 用户中止 = 拒绝(等价 deny)

    @pytest.mark.parametrize("type_", ["approval.granted", "approval.denied",
                                       "approval.timeout"])
    async def test_outcome_event_never_prompts(self, monkeypatch, capsys, type_):
        """Scenario 2/3/4:结果事件不得触发 input/approve/deny,不得抛 APR-503。

        结果载荷**含** approval_id,但那是审批结果、不是人类动作请求;旧实现按
        ``startswith("approval.")`` 通配分发,会对结果事件再次提问,且用户作答后
        裁决必抛 APR-503(未知/已消费 id)。
        """
        prompted = []

        def fake_input(prompt=""):
            prompted.append(prompt)
            return "y"                                   # 即使作答也不得被采纳

        monkeypatch.setattr("builtins.input", fake_input)
        env = await _approval_envelope(type_=type_)
        appr = FakeApproval()
        sh = _sh(_ctx(approval=appr), headless=False, channel="cli")
        await cli.prompt_approval(sh, type_, env)        # 静默返回,不抛
        assert prompted == []
        assert appr.granted == [] and appr.denied == []
        assert "[审批]" not in capsys.readouterr().err


class TestApprovalLiveChain:
    """组件级活链(D1b):SessionLog → Envelope → EventBus → _render_events →
    prompt_approval → **真实 ApprovalProvider**.approve/deny。

    证明三者一致:总线投递形状(Envelope)、canonical identity(Envelope.seq)、
    provider pending state(_pending 以同一 seq 为键)。执行侧入口用真实的
    ``ApprovalProvider.request()``,不用替身。
    """

    async def _run(self, monkeypatch, answer):
        from pyharness.core.approval import ApprovalProvider

        bus = EventBus()
        log_ = await _new_inmem_session(bus=bus)
        prov = ApprovalProvider(session=log_, bus=bus, channel="cli")
        ctx = _ctx(bus=bus, session=log_, approval=prov, channel="cli")
        sh = _sh(ctx, headless=False, channel="cli")
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)

        call = SimpleNamespace(name="exec.shell_run", args={"command": "echo hi"},
                               raw_args={"command": "echo hi"}, call_id="c-1",
                               parent_seq=None)
        # 渲染器必须先订阅(否则 approval.requested 在订阅前上总线即丢失)
        render = asyncio.create_task(cli._render_events(sh, "t-1"))
        await asyncio.sleep(0)                           # 订阅就位
        # 执行侧真实入口:request() 落 approval.requested 后阻塞等裁决
        req = asyncio.create_task(
            prov.request(call, "command=echo hi", ctx, binding="b-1"))
        verdict = await asyncio.wait_for(req, timeout=5)  # 经总线提示后被裁决
        await log_.append("session.finished", {"reason": "done"}, actor="system")
        await asyncio.wait_for(render, timeout=5)
        return verdict, prov, list(log_.events_after())

    async def test_approve_through_bus(self, monkeypatch, capsys):
        verdict, prov, events = await self._run(monkeypatch, "y")
        assert verdict == "granted"
        req_seq = [e.seq for e in events if e.type == "approval.requested"][0]
        granted = [e for e in events if e.type == "approval.granted"][0]
        assert granted.payload["approval_id"] == req_seq   # request/response 同 identity
        assert req_seq not in prov._pending                # 结算后 pending 已清理
        assert "[审批]" in capsys.readouterr().err

    async def test_deny_through_bus(self, monkeypatch):
        verdict, prov, events = await self._run(monkeypatch, "n")
        assert verdict == "denied"
        req_seq = [e.seq for e in events if e.type == "approval.requested"][0]
        denied = [e for e in events if e.type == "approval.denied"][0]
        assert denied.payload["approval_id"] == req_seq
        assert req_seq not in prov._pending


# ================================================== D1d-A 退出生命周期(生产装配)
def _tmp_cfg(tmp_path) -> Path:
    """存储域全部指向 tmp_path,避免测试污染 ~/.pyharness。"""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "storage:\n"
        f"  sessions_dir: {tmp_path / 'sessions'}\n"
        f"  workspaces_dir: {tmp_path / 'ws'}\n"
        f"  spill_dir: {tmp_path / 'spill'}\n",
        encoding="utf-8")
    return cfg


class TestExitLifecycleRealWiring:
    """D1d-A 回归:**不注入 FakeAgent** 的真实装配路径。

    现有 _mk_sh 默认注入 FakeAgent,正是原始 bug 未被发现的原因——CLI 活路径
    从不装配 ctx.agent,而 /exit 曾以它充当退出闸门。
    """

    async def test_exit_sets_shell_flag_without_ctx_agent(self, tmp_path, monkeypatch):
        """INV-D1d-01:ctx.agent 缺失不得阻止 shell.request_exit(0)。"""
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))   # tty → channel="cli"
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-unused")  # 仅过装配闸,不出网
        r = cli.parse_args(["chat", "--config", str(_tmp_cfg(tmp_path))])
        sh = await cli.bootstrap_shell(r)
        await cli._attach_engine(sh)

        assert sh.ctx.agent is None            # 生产事实:CLI 从不装配 ctx.agent
        assert sh.ctx.channel == "cli"
        assert sh.ctx.shell.exit_code is None

        cont = await commands.handle_slash("/exit", sh.ctx)

        assert cont is True
        assert sh.ctx.shell.exit_code == 0     # ← 修复前恒为 None(退出从不发生)
        assert cli._shell_exit_code(sh) == 0   # interactive_loop 的退出判据
        assert "user.command" in [e.type for e in sh.ctx.session.events_after(0)]


class TestExitFinalFlushPersistence:
    """INV-01 回归(D1d-A):/exit → loop 返回 → cli_main finally → final flush →
    已 append 的事件全部进入 JSONL。"""

    async def test_exit_persists_all_emitted_events(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-unused")
        r = cli.parse_args(["chat", "--config", str(_tmp_cfg(tmp_path))])

        holder: dict = {}
        orig_loop = cli.interactive_loop

        async def loop_with_round(sh, **kw):
            """先造一轮(含非 SYNC 尾部事件),再走真实 /exit 退出。"""
            log_ = sh.ctx.session
            holder["log"] = log_
            await log_.append("user.message", {"content": "hi"}, actor="user",
                              sync=True)
            await log_.append("agent.message", {"content": "ok"}, actor="agent")
            seg = await log_.append("segment.start", {"task_id": "t-1"},
                                    actor="system")
            await log_.append("task.completed",
                              {"task_id": "t-1", "reason": "ok"}, actor="system")
            await log_.append("segment.end",
                              {"task_id": "t-1", "start_seq": seg.seq},
                              actor="system")
            rc = await orig_loop(sh, **kw)
            holder["flag"] = getattr(sh.ctx.shell, "exit_code", None)
            return rc

        class _OnceReader:
            """只允许读一次:第二次读 = loop 未被 /exit 旗标终止(EOF 兜底)。"""

            def __init__(self, value):
                self._v, self.n = value, 0

            async def __call__(self, sh, *, once=False):
                self.n += 1
                assert self.n == 1, "loop 未由 /exit 退出旗标终止(读了第二次)"
                return self._v

        monkeypatch.setattr(cli, "interactive_loop", loop_with_round)
        monkeypatch.setattr(cli, "read_user_input", _OnceReader("/exit"))

        assert await cli.cli_main(r) == 0      # loop 返回 → finally → _flush_session
        assert holder["flag"] == 0             # 由退出旗标终止(非 EOF 兜底)

        log_ = holder["log"]
        emitted = [e.seq for e in log_.events_after(0)]
        files = list((tmp_path / "sessions").glob("*.jsonl"))
        assert len(files) == 1
        rows = [json.loads(x) for x in
                files[0].read_text(encoding="utf-8").splitlines() if x.strip()]
        persisted = [x["seq"] for x in rows]
        types_ = [x["type"] for x in rows]

        for t in ("user.message", "agent.message", "task.completed",
                  "segment.end", "user.command"):
            assert t in types_
        assert max(persisted) == max(emitted)                # last persisted == emitted
        assert sorted(set(emitted) - set(persisted)) == []   # missing == []


# ============================================================== 离线子命令
class TestOffline:
    """离线(config/budget/stats):零装配零网络零 LLM;config validate 失败退 1。"""

    async def test_config_validate_errors_exit1(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("llm: [未闭合", encoding="utf-8")
        code = await cli.cli_main(cli.parse_args(
            ["config", "--config", str(bad), "validate"]))
        assert code == 1
        out = capsys.readouterr().out
        assert "CFG-601" in out                          # TC-G2:字段明细同退非 0

    async def test_config_show_text(self, tmp_path, capsys):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("llm:\n  model: deepseek-chat\n", encoding="utf-8")
        assert await cli.cli_main(cli.parse_args(
            ["config", "--config", str(cfg), "show"])) == 0
        assert "llm.model = deepseek-chat" in capsys.readouterr().out

    async def test_config_show_json(self, tmp_path, capsys):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("llm:\n  model: deepseek-chat\n", encoding="utf-8")
        assert await cli.cli_main(cli.parse_args(
            ["config", "--config", str(cfg), "--json", "show"])) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["llm.model"] == "deepseek-chat"
        assert data["llm.api_key"] == "env:DEEPSEEK_API_KEY"   # 秘密只回显引用

    async def test_config_init_creates_defaults(self, tmp_path, capsys):
        target = tmp_path / "new" / "config.yaml"
        assert await cli.cli_main(cli.parse_args(
            ["config", "--config", str(target), "init"])) == 0
        assert target.exists()
        import yaml
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert data["storage"]["root"] == "~/.pyharness"
        out = capsys.readouterr().out
        assert "已写入默认配置" in out

    async def test_config_init_no_overwrite(self, tmp_path, capsys):
        target = tmp_path / "cfg.yaml"
        target.write_text("llm:\n  model: x\n", encoding="utf-8")
        assert await cli.cli_main(cli.parse_args(
            ["config", "--config", str(target), "init"])) == 0
        assert "已存在" in capsys.readouterr().out

    async def test_budget_offline(self, tmp_env, capsys):
        cfg = str(tmp_env / "no.yaml")
        assert await cli.cli_main(cli.parse_args(
            ["budget", "--config", cfg])) == 0
        out = capsys.readouterr().out
        assert "任务预算硬闸" in out and "实测估算" in out

    async def test_budget_json(self, tmp_env, capsys):
        cfg = str(tmp_env / "no.yaml")
        assert await cli.cli_main(cli.parse_args(
            ["budget", "--config", cfg, "--json"])) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["task"]["max_cost_yuan"] == 1.0
        assert data["monthly"]["limit_yuan"] == 0.0

    async def test_stats_offline_empty(self, tmp_env, capsys):
        cfg = str(tmp_env / "no.yaml")
        assert await cli.cli_main(cli.parse_args(
            ["stats", "--config", cfg])) == 0
        out = capsys.readouterr().out
        assert "会话数: 0" in out

    async def test_stats_counts_jsonl(self, tmp_env, capsys):
        """stats 只读派生:真实会话 JSONL 事件计数(不建第二份状态)。"""
        sessions = tmp_env / "sessions"
        sessions.mkdir(parents=True)
        (sessions / f"{SID}.jsonl").write_text(
            '{"session_id": "%s", "seq": 1, "ts": "t", "type": "session.created",'
            ' "actor": "system", "payload": {"title": "", "model": "m"}}\n'
            '{"session_id": "%s", "seq": 2, "ts": "t", "type": "session.finished",'
            ' "actor": "system", "payload": {"reason": "done"}}\n' % (SID, SID),
            encoding="utf-8")
        cfg = str(tmp_env / "no.yaml")
        assert await cli.cli_main(cli.parse_args(
            ["stats", "--config", cfg])) == 0
        out = capsys.readouterr().out
        assert "会话数: 1" in out and "事件总数: 2" in out and "已终态会话: 1" in out

    # ------------------------------------------------------ RT-02 扫描容错
    def _two_sessions(self, root):
        """建 1 个正常会话 + 1 个不可读会话,返回 (sessions_dir, bad_path)。"""
        sessions = root / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "s-ok0000000a.jsonl").write_text(
            '{"session_id": "s-ok0000000a", "seq": 1, "ts": "t",'
            ' "type": "session.created", "actor": "system",'
            ' "payload": {"title": "", "model": "m"}}\n'
            '{"session_id": "s-ok0000000a", "seq": 2, "ts": "t",'
            ' "type": "llm.usage", "actor": "llm",'
            ' "payload": {"cost_est": 1.5}}\n',
            encoding="utf-8")
        bad = sessions / "s-bad000000b.jsonl"
        bad.write_text("x\n", encoding="utf-8")
        return sessions, bad

    @staticmethod
    def _deny_open(monkeypatch, bad):
        """让指定路径的 open 抛 PermissionError(模拟被占用/权限不足)。"""
        real_open = open

        def _deny(path, *a, **kw):
            if Path(path) == bad:
                raise PermissionError(13, "Permission denied", str(path))
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", _deny)

    def test_scan_usage_skips_unreadable_file(self, tmp_path, monkeypatch):
        """RT-02:单文件不可读 → 跳过并告警;可读者仍被聚合,不抛出。"""
        sessions, bad = self._two_sessions(tmp_path)
        self._deny_open(monkeypatch, bad)

        out = cli._scan_usage(sessions)

        assert out["sessions"] == 2      # 扫描面含被跳过者(语义保持)
        assert out["events"] == 2        # 只聚合可读者
        assert out["cost_est"] == 1.5

    async def test_budget_and_stats_survive_unreadable_session(
            self, tmp_env, monkeypatch, capsys):
        """RT-02:命令级——bad file 存在时 budget/stats 仍 rc=0 并给出报表。"""
        _sessions, bad = self._two_sessions(tmp_env)
        self._deny_open(monkeypatch, bad)
        cfg = str(tmp_env / "no.yaml")

        assert await cli.cli_main(cli.parse_args(["budget", "--config", cfg])) == 0
        assert "实测估算" in capsys.readouterr().out

        assert await cli.cli_main(cli.parse_args(["stats", "--config", cfg])) == 0
        assert "会话数: 2" in capsys.readouterr().out


# ============================================================== repair 桥
class TestRepairCmd:
    """repair 子命令桥(F060):无 sid 自动扫描;命中 → repair_session → 报告。"""

    def _install_repair(self, monkeypatch, *, hits=(), report=None, calls=None):
        mod = types.ModuleType("pyharness.repair")

        async def fake_auto_scan(sessions_dir):
            return list(hits)

        async def fake_repair_session(ctx, sid, *, interactive=False, policy=None):
            if calls is not None:
                calls.append((sid, interactive))
            return report

        mod.auto_scan = fake_auto_scan
        mod.repair_session = fake_repair_session
        monkeypatch.setitem(sys.modules, "pyharness.repair", mod)

    async def test_no_damage_ok(self, monkeypatch, capsys):
        self._install_repair(monkeypatch, hits=[])
        sh = _sh(_ctx(), headless=True)
        assert await cli.repair_cmd(sh) == 0
        assert capsys.readouterr().out.strip() == "[repair] 无损坏会话"

    async def test_repair_report_text(self, monkeypatch, capsys):
        report = SimpleNamespace(fixed=2, lost=0, backup_path="b.jsonl",
                                 quarantined=1)
        self._install_repair(monkeypatch,
                             hits=[SimpleNamespace(sid=SID, healthy=False)],
                             report=report)
        sh = _sh(_ctx(storage=SimpleNamespace(
            sessions_dir=Path("/x"))), headless=True)
        assert await cli.repair_cmd(sh) == 0
        out = capsys.readouterr().out
        assert f"[repair] {SID}" in out and "fixed=2" in out and "backup=b.jsonl" in out

    async def test_repair_report_json(self, monkeypatch, capsys):
        report = SimpleNamespace(fixed=["tail-truncated"], lost=0,
                                 backup_path="b.jsonl", quarantined=[2, 3])
        self._install_repair(monkeypatch,
                             hits=[SimpleNamespace(sid=SID, healthy=False)],
                             report=report)
        sh = _sh(_ctx(storage=SimpleNamespace(sessions_dir=Path("/x"))),
                 headless=True, flags={"json": True})
        assert await cli.repair_cmd(sh) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["sid"] == SID
        assert data["fixed"] == ["tail-truncated"]
        assert data["quarantined"] == [2, 3]

    async def test_repair_interactive_flag_passthrough(self, monkeypatch):
        """tty 下 interactive=True 透传(修复管线交人类删留决策)。"""
        calls = []
        report = SimpleNamespace(fixed=[], lost=0, backup_path=None,
                                 quarantined=[])
        self._install_repair(monkeypatch,
                             hits=[SimpleNamespace(sid=SID, healthy=False)],
                             report=report, calls=calls)
        sh = _sh(_ctx(storage=SimpleNamespace(sessions_dir=Path("/x"))),
                 headless=False)
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        assert await cli.repair_cmd(sh) == 0
        assert calls[0][0] == SID and calls[0][1] is True


# ============================================================== 端到端冒烟
class TestEndToEndSmoke:
    """真实装配冒烟:chat headless 管道喂词走真实 SessionLog+TaskQueue 全链路
    (引擎 runner 未注入 → 任务 CYC-999 快速失败,防 S-1 伪造执行),EOF 净退 0。"""

    async def test_chat_pipe_full_path(self, monkeypatch, tmp_env, capsys):
        monkeypatch.setattr(sys, "stdin",
                            _FakeStdin("你好,引擎还没装", ""))
        cfg = str(tmp_env / "no.yaml")
        parsed = cli.parse_args(["chat", "--config", cfg])
        sh = await cli.bootstrap_shell(parsed)
        sid = sh.ctx.session.sid
        try:
            code = await cli.interactive_loop(sh)
            assert code == 0                              # EOF 净退
            err = capsys.readouterr().err
            assert "[任务失败]" in err                    # runner 未装配 → CYC-999
            await sh.ctx.session._persistence.flush()     # 收尾落盘(普通事件攒批)
            # 真源落盘:created/enqueued/started/failed/segment 配对(INV-01)
            lines = (tmp_env / "sessions" / f"{sid}.jsonl").read_text(
                encoding="utf-8").strip().splitlines()
            types_ = [json.loads(l)["type"] for l in lines]
            assert types_[0] == "session.created"
            assert "task.enqueued" in types_ and "task.failed" in types_
        finally:
            if sh.ctx.session is not None:
                sh.ctx.session._persistence.close()
