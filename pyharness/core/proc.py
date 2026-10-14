"""pyharness/core/proc.py — 子进程会话管理(#58 exec 沙箱族后端)。

一句话职责:会话级子进程登记表——start 拉起(沙箱 cwd/裁剪 env/隐藏窗口)、
status 轮询读尾部、kill 结束;进程树按 session_id 隔离(会话关闭即清)。

应用层约束(诚实口径,见 tool_exec docstring):cwd/env/输出配额/超时 +
CREATE_NO_WINDOW。**这不是安全沙箱**:命令可绕过 cwd 并访问网络/绝对路径;
安全边界由 high 风险审批承担,恶意代码必须使用 OS 级隔离。
PTY 语义:Windows 无内建 pty,行式会话(stdin 写/stdout 读)为等价降级。

线程安全:每会话一个 asyncio.Lock;输出经后台线程读入 deque(reader daemon),
不阻塞事件循环;进程对象只在 _PROCS 内可达(会话结束 close_session 清理)。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.proc")

_PROC_KEY = "_pyharness_procs"
MAX_TAIL_LINES = 200
MAX_TAIL_CHARS = 50_000
READ_CHUNK = 8192

_PROCESS_CREATION_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 沙箱环境白名单:只保留子进程运行系统必需变量;API key/secret/proxy/项目路径
# 一律不继承(默认 env:NAME 由宿主启动期解析,子进程不需要原始环境)。
_ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_ARCHITEW6432", "OS", "PYTHONIOENCODING", "PYTHONUTF8",
    "LANG", "LC_ALL",
)


class ProcSession:
    """单子进程会话(登记在 _PROCS[sid][token])。"""

    def __init__(self, token: str, proc: subprocess.Popen, *, tail: deque,
                 reader: threading.Thread,
                 timer: Optional[threading.Timer] = None) -> None:
        self.token = token
        self.proc = proc
        self.tail = tail
        self.reader = reader
        self.timer = timer
        self.chars_total = 0

    @property
    def pid(self) -> int:
        return int(self.proc.pid or 0)

    def status(self) -> dict:
        owner = getattr(self, 'owner', None)
        rc = owner.poll() if owner is not None else self.proc.poll()
        return {
            "pid": self.pid,
            "token": self.token,
            "running": rc is None,
            "exit_code": rc,
            "chars_total": self.chars_total,
            "tail_lines": len(self.tail),
            "tail": list(self.tail)[-20:],
        }


_SESSIONS: dict[str, dict[str, "ProcSession"]] = {}
"""会话进程登记表:sid → {token: ProcSession}(会话关闭即清,见 close_session)。"""


def _table() -> dict[str, dict[str, "ProcSession"]]:
    return _SESSIONS


def sandbox_env(cwd: Path) -> dict:
    """沙箱环境:白名单最小继承;外部代理/密钥/PYTHONPATH 全部不进入子进程。"""
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    tmp = cwd / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(tmp)
    env["TEMP"] = str(tmp)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        env.pop(k, None)
    return env


def start_session(sid: str, sandbox: Path, command: str, *,
                  timeout_total_s: int) -> ProcSession:
    """沙箱内拉起子进程(shell 语义:cmd /c;POSIX sh -c)。"""
    sandbox.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        argv = ["cmd", "/d", "/s", "/c", command]
    else:
        argv = ["/bin/sh", "-c", command]
    from pyharness.core.process_owner import ProcessOwner
    owner = ProcessOwner(argv, cwd=sandbox, env=sandbox_env(sandbox),
                         text=True, merge_stderr=True, input_open=True)
    proc = owner.proc
    tail: deque = deque(maxlen=MAX_TAIL_LINES)
    sess = ProcSession(token=f"{sid}-{proc.pid}", proc=proc, tail=tail,
                       reader=None)
    reader = threading.Thread(target=_pump, args=(proc, tail, sess),
                              daemon=True, name=f"proc-{sid}-{proc.pid}")
    sess.owner = owner
    sess.reader = reader
    timer = threading.Timer(max(1, int(timeout_total_s)), _kill_tree, args=(sess,))
    timer.daemon = True
    sess.timer = timer
    def reap():
        try:
            owner.wait_exit()
            owner.terminate()
        except BaseException as exc:
            sess.cleanup_error = exc
    watcher = threading.Thread(target=reap, daemon=True, name=f"proc-owner-{sid}")
    sess.watcher = watcher
    started = []
    try:
        for worker in (reader, timer, watcher):
            worker.start()
            started.append(worker)
        _table().setdefault(sid, {})[sess.token] = sess
    except BaseException as primary:
        timer.cancel()
        try: owner.terminate()
        except BaseException as exc: primary.add_note(f'process cleanup: {type(exc).__name__}')
        for worker in started:
            try: worker.join(timeout=5)
            except BaseException as exc: primary.add_note(f'thread cleanup: {type(exc).__name__}')
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try: stream.close()
                except BaseException as exc: primary.add_note(f'pipe cleanup: {type(exc).__name__}')
        raise
    return sess


def _pump(proc: subprocess.Popen, tail: deque, sess: ProcSession) -> None:
    """后台读线程:行追加(配额内),进程退出后补读残余并保底清 stdin。"""
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line.rstrip("\n")[-4000:])
            sess.chars_total += len(line)
            if sess.chars_total > MAX_TAIL_CHARS:
                _kill_tree(sess)
                tail.append("[输出超配额,进程已终止]")
                break
    except Exception:                             # noqa: BLE001 读线程兜底
        tail.append("[输出读异常]")
    finally:
        if sess.timer is not None:
            sess.timer.cancel()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        try:
            proc.stdin.close()
        except OSError:
            pass


def find_session(sid: str, token_or_pid: Any) -> ProcSession:
    """定位会话(token 或裸 pid);缺失 → TLB-802(给 LLM 明确指引)。"""
    table = _table().get(sid) or {}
    if isinstance(token_or_pid, str) and token_or_pid in table:
        return table[token_or_pid]
    for s in table.values():
        if str(s.pid) == str(token_or_pid):
            return s
    raise_code("TLB-802", module="proc",
               hint=f"会话 {token_or_pid} 不存在或不属于本会话(sid={sid})")


def kill_session(sid: str, token_or_pid: Any) -> dict:
    """结束进程(树级:Windows taskkill /T 兜底;POSIX 组)。"""
    sess = find_session(sid, token_or_pid)
    _kill_tree(sess)
    return sess.status()


def _kill_tree(sess: ProcSession) -> None:
    """结束父进程及其子进程;失败再退化到父进程 kill。"""
    if sess.timer is not None:
        sess.timer.cancel()
    owner = getattr(sess, "owner", None)
    if owner is not None:
        owner.terminate()
    elif sess.proc.poll() is None:
        # Legacy injected process without a tree capability: only its live handle.
        sess.proc.kill()
        sess.proc.wait(timeout=5)


def _kill_pid_tree(pid: int) -> None:
    """Retained PID is not a capability. Call the ProcessOwner instead."""
    raise RuntimeError('PID-only tree termination is disabled; an owned process handle is required')


def close_session(sid: str) -> int:
    """会话收尾:结束全部子进程,清登记(agent close 时调用)。返回清理数。"""
    table = _table().get(sid, {})
    n = 0
    errors = []
    for token, sess in list(table.items()):
        n += 1
        item_errors = []
        if sess.timer is not None:
            sess.timer.cancel()
        try: _kill_tree(sess)
        except BaseException as exc: item_errors.append(exc)
        for worker in (sess.reader, sess.timer, getattr(sess, 'watcher', None)):
            if worker is not None and worker is not threading.current_thread():
                try:
                    worker.join(timeout=5)
                    if worker.is_alive(): raise RuntimeError('owned process worker did not stop')
                except BaseException as exc: item_errors.append(exc)
        if not item_errors: table.pop(token, None)
        errors.extend(item_errors)
    if not table: _table().pop(sid, None)
    if errors: raise BaseExceptionGroup('process session cleanup failed', errors)
    return n


def _argv(command: str, python: bool):
    if python:
        return [sys_executable(), "-I", "-B", "-c", command]
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


def wait_result(sandbox: Path, command: str, *, timeout_s: int, python: bool = False) -> dict:
    """Synchronous owned execution with bounded output and tree cleanup."""
    from pyharness.core.process_owner import ProcessOwner
    sandbox.mkdir(parents=True, exist_ok=True)
    owner = ProcessOwner(_argv(command, python), cwd=sandbox, env=sandbox_env(sandbox))
    return owner.collect(timeout_s)


async def wait_result_async(sandbox: Path, command: str, *, timeout_s: int, python: bool = False) -> dict:
    """Cancellation stops and reaps this owned tree before propagating cancellation."""
    from pyharness.core.process_owner import ProcessOwner
    sandbox.mkdir(parents=True, exist_ok=True)
    owner = ProcessOwner(_argv(command, python), cwd=sandbox, env=sandbox_env(sandbox))
    collector = asyncio.create_task(asyncio.to_thread(owner.collect, timeout_s))
    try:
        return await asyncio.shield(collector)
    except BaseException as primary:
        async def cleanup():
            try: await asyncio.to_thread(owner.terminate)
            except BaseException as exc: primary.add_note(f'process cleanup: {type(exc).__name__}')
            try: await collector
            except BaseException as exc: primary.add_note(f'collector cleanup: {type(exc).__name__}')
        cleanup_task = asyncio.create_task(cleanup())
        while not cleanup_task.done():
            try: await asyncio.shield(cleanup_task)
            except asyncio.CancelledError: continue
        raise


def sys_executable() -> str:
    """代码执行解释器:优先沙箱 venv,回落 sys.executable。"""
    import sys
    return sys.executable


__all__ = ["ProcSession", "start_session", "find_session", "kill_session",
           "close_session", "wait_result", "wait_result_async", "sandbox_env"]
