"""pyharness/core/tool_exec.py — exec.* 受限进程工具族(#28/#29/#35/#58 Consumer 面)。

工具:
- exec.shell_run : 在会话工作目录运行 shell 命令(cmd /c;一次性,输出配额内取全文)
- exec.python_run: 在会话工作目录运行 Python 代码(sys.executable -c)
- exec.pty      : 在**真伪终端(ConPTY/pty)**里运行命令(可喂 stdin;TTY 语义)
- proc.start/status/kill: 会话式子进程(行式 stdin/stdout,PTY 降级见 proc.py)

安全模型(诚实口径):
- danger=high → guard g-danger 转审批(F015):LLM 每次调 exec 都要人批准,
  "LLM 能跑代码但永远过不了人这关"= 审批是本模块的安全主闸;
- strict 沙箱档下 exec.* 不可见(域不在 workspace/allowlist/self)→ 演示用
  standard 档(security.policy.preset=standard)并配审批;
- cwd/env/超时/输出配额只是误操作约束,**不是安全沙箱**:命令仍可访问
  绝对路径、网络和宿主进程。若需抵御恶意代码,必须使用 OS 级隔离。
"""
from __future__ import annotations

from typing import Any

from pyharness.core.tools_registry import ToolDefinition  # noqa: F401
from pyharness.errors import raise_code


def _sandbox_dir(ctx: Any):
    """会话工作目录:workspace_root/<sid>/sandbox(fail-closed:无根 → CYC-999)。"""
    from pathlib import Path
    scope = getattr(ctx, "scope", None)
    ws = None
    if scope is not None:
        ws = getattr(scope.policy, "workspace_root", None) if getattr(
            scope, "policy", None) else getattr(scope, "workspace_root", None)
    if not ws:
        raise_code("CYC-999", module="exec",
                   hint="workspace_root 未装配:沙箱目录无法定位(fail-closed)")
    sid = getattr(getattr(ctx, "session", None), "sid", "anon")
    return Path(str(ws)) / str(sid) / "sandbox"


def _wallclock(ctx: Any) -> int:
    """进程墙钟上限(security.sandbox.proc_wallclock_s 默认 60)。"""
    try:
        v = ctx.config.security.sandbox.proc_wallclock_s
        return int(v)
    except Exception:                                # noqa: BLE001
        return 60


def _cfg_sandbox_level(ctx: Any) -> str:
    try:
        return str(ctx.config.security.sandbox.level)
    except Exception:                                # noqa: BLE001
        return "strict"


def _require_exec_visible(ctx: Any) -> None:
    """strict 档直调守卫:exec 域在 strict 下不可见(scope 已拦,此处双保险)。"""
    if _cfg_sandbox_level(ctx) == "strict":
        raise_code("TLB-802", module="exec",
                   hint="strict 沙箱档下 exec.* 不可用(域隔离);"
                        "改 security.policy.preset=standard 并配审批后使用")


async def exec_shell(args: dict, ctx: Any) -> str:
    """exec.shell_run Provider:审批门控的一次性 shell。"""
    _require_exec_visible(ctx)
    command = str(args.get("command") or "").strip()
    if not command:
        raise_code("EVT-100", field="command", hint="缺 command")
    from pyharness.core import proc
    r = await proc.wait_result_async(_sandbox_dir(ctx), command,
                                     timeout_s=_wallclock(ctx))
    return r["summary"]


async def exec_python(args: dict, ctx: Any) -> str:
    """exec.python_run Provider:审批门控地运行 Python 代码。"""
    _require_exec_visible(ctx)
    code = str(args.get("code") or "").strip()
    if not code:
        raise_code("EVT-100", field="code", hint="缺 code")
    from pyharness.core import proc
    r = await proc.wait_result_async(_sandbox_dir(ctx), code,
                                     timeout_s=_wallclock(ctx), python=True)
    return r["summary"]


async def exec_pty(args: dict, ctx: Any) -> str:
    """exec.pty Provider:审批门控的**真 PTY**执行(ConPTY;可喂 stdin)。

    同步 PTY 循环经 asyncio.to_thread 跑线程池(不阻塞事件循环);超时/配额由
    pty.run_in_pty 负责,输出超长走 spill 由 executor 关4 处理。
    """
    _require_exec_visible(ctx)
    command = str(args.get("command") or "").strip()
    if not command:
        raise_code("EVT-100", field="command", hint="缺 command")
    import asyncio

    from pyharness.core import pty as pty_mod
    timeout_s = int(args.get("timeout_s") or _wallclock(ctx))
    timeout_s = max(1, min(timeout_s, 600))
    result = await asyncio.to_thread(
        pty_mod.run_in_pty, command, cwd=_sandbox_dir(ctx), env=None,
        input_text=str(args.get("input") or ""), timeout_s=timeout_s)
    head = (f"[PTY] exit={result['exit_code']} tty={result['tty']} "
            f"timed_out={result['timed_out']} {result['elapsed_ms']}ms")
    body = result["output"].strip()
    if result["truncated"]:
        body += "\n...(输出被截断)"
    return f"{head}\n{body}" if body else head


async def proc_start(args: dict, ctx: Any) -> str:
    """proc.start Provider:审批门控的会话式后台进程(行式)。"""
    _require_exec_visible(ctx)
    command = str(args.get("command") or "").strip()
    if not command:
        raise_code("EVT-100", field="command", hint="缺 command")
    from pyharness.core import proc
    sid = getattr(getattr(ctx, "session", None), "sid", "anon")
    sess = proc.start_session(sid, _sandbox_dir(ctx), command,
                              timeout_total_s=_wallclock(ctx))
    return f"已启动会话 {sess.token}(pid={sess.pid});用 proc.status 查输出"


async def proc_status(args: dict, ctx: Any) -> str:
    """proc.status Provider:读会话尾部输出/退出码。"""
    from pyharness.core import proc
    sid = getattr(getattr(ctx, "session", None), "sid", "anon")
    st = proc.find_session(sid, args.get("pid")).status()
    parts = [f"pid={st['pid']} token={st['token']} "
             f"running={st['running']} exit={st['exit_code']}"]
    if st["tail"]:
        parts.append("尾部输出:\n" + "\n".join(st["tail"]))
    return "\n".join(parts)


async def proc_kill(args: dict, ctx: Any) -> str:
    """proc.kill Provider:结束会话(树级)。"""
    _require_exec_visible(ctx)
    from pyharness.core import proc
    sid = getattr(getattr(ctx, "session", None), "sid", "anon")
    st = proc.kill_session(sid, args.get("pid"))
    return f"已结束 {st['token']}(pid={st['pid']},exit={st['exit_code']})"


_DEFS = (
    ("exec.shell_run", "在会话工作目录执行 shell 命令(cmd /c 语义);每次调用"
                       "需人工审批;非 OS 级安全沙箱", 60,
     {"type": "object",
      "properties": {"command": {"type": "string", "minLength": 1,
                                 "maxLength": 4000}},
      "required": ["command"], "additionalProperties": False}),
    ("exec.python_run", "在会话工作目录执行 Python 代码(sys.executable -c);"
                        "需人工审批;非 OS 级安全沙箱", 120,
     {"type": "object",
      "properties": {"code": {"type": "string", "minLength": 1,
                              "maxLength": 8000}},
      "required": ["code"], "additionalProperties": False}),
    ("exec.pty", "在真伪终端(ConPTY/pty)中运行命令并返回终端输出(含颜色/控制"
                 "序列;可经 input 喂 stdin);需人工审批;非 OS 级安全沙箱", 90,
     {"type": "object",
      "properties": {"command": {"type": "string", "minLength": 1,
                                 "maxLength": 4000},
                     "input": {"type": "string", "maxLength": 4000},
                     "timeout_s": {"type": "integer", "minimum": 1,
                                   "maximum": 600}},
      "required": ["command"], "additionalProperties": False}),
    ("proc.start", "启动沙箱内后台会话进程(行式 stdin/stdout);返回 token/pid,"
                   "配合 proc.status/kill 管理", 30,
     {"type": "object",
      "properties": {"command": {"type": "string", "minLength": 1,
                                 "maxLength": 4000}},
      "required": ["command"], "additionalProperties": False}),
    ("proc.status", "查询后台进程运行态与输出尾部", 10,
     {"type": "object",
      "properties": {"pid": {"type": "string", "minLength": 1}},
      "required": ["pid"], "additionalProperties": False}),
    ("proc.kill", "结束后台进程(进程树级);需人工审批", 15,
     {"type": "object",
      "properties": {"pid": {"type": "string", "minLength": 1}},
      "required": ["pid"], "additionalProperties": False}),
)

_PROVIDERS = {
    "exec.shell_run": exec_shell,
    "exec.python_run": exec_python,
    "exec.pty": exec_pty,
    "proc.start": proc_start,
    "proc.status": proc_status,
    "proc.kill": proc_kill,
}

# 一次性 exec 与启动/结束需审批;status 只读 none
_DANGER = {"exec.shell_run": "high", "exec.python_run": "high",
           "exec.pty": "high",
           "proc.start": "high", "proc.kill": "high", "proc.status": "low"}


def register(registry: Any) -> list[str]:
    """五要素登记 + Provider 绑定(engine 装配面调用)。"""
    for name, desc, timeout_s, schema in _DEFS:
        registry.register_tool(
            ToolDefinition(name=name, danger=_DANGER[name], description=desc,
                           schema=schema, timeout_s=timeout_s, owner="builtin",
                           ctx_path="exec", version="1.0.0"),
            provider=_PROVIDERS[name])
    return [d[0] for d in _DEFS]


__all__ = ["register", "exec_shell", "exec_python", "exec_pty", "proc_start",
           "proc_status", "proc_kill", "_sandbox_dir"]
