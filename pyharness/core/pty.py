"""pyharness/core/pty.py — 真实伪终端(PTY)执行后端(F053)。

一句话职责:把命令放进**真 PTY** 里跑,使子进程看到 isatty()=True 的终端语义
(颜色/TTY 判定/行编辑/交互式 CLI 可用)。这是 proc.py 行式管道(stdin 写/stdout
读)的**升级替代**,不是降级。

后端选型(实测决定,诚实记录):
- Windows:`pywinpty`(ConPTY 的成熟封装,可选依赖 extra `pty`)。
  ⚠️ 为什么不自己 ctypes 直连 CreatePseudoConsole:2026-09-12 本机实测,按
  MSDN 流程(CreatePipe → CreatePseudoConsole → STARTUPINFOEXW +
  PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE → CreateProcessW)自建实现能创建并启动
  子进程,但 **输出端 PeekNamedPipe/ReadFile 恒为 0 字节**(7 种句柄/线程/轮询
  变体全试过);同环境 pywinpty 一次跑通并返回带控制序列的输出。故采用成熟封装,
  不再重复造 ConPTY 轮子(依赖缺失 → TLB-807,提示 pip install pywinpty)。
- POSIX:标准库 `pty.openpty`(零依赖)。

与 proc.py 的分工:
- proc.py    : 后台会话式进程(行式,可 status/kill 管理),无 TTY 语义;
- pty.py(本): 一次性 PTY 执行(可喂 stdin/超时/收集输出),有 TTY 语义;
- 两者共同点:cwd 锁在会话沙箱目录、env 走 proc.sandbox_env 白名单(不继承
  API key/代理)、墙钟上限——**都是误操作约束,不是安全沙箱**;安全主闸仍是
  exec 域的 danger=high → 审批(F015)。
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional, Union

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.pty")

DEFAULT_COLS = 120
DEFAULT_ROWS = 30
READ_CHUNK = 4096
MAX_OUTPUT_CHARS = 200_000          # 收集上限(超出截断,防打爆上下文)
GRACE_KILL_S = 3.0                  # 墙钟超时后的收尾宽限
POLL_S = 0.02                       # 空读轮询间隔


# ------------------------------------------------------------------ Windows
class _WinPtySession:
    """pywinpty(ConPTY)会话:后台线程读、非阻塞轮询取缓冲。"""

    def __init__(self, command: Union[str, list], *, cwd: Path, env: dict,
                 cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.cols = cols
        self.rows = rows
        self._p: Any = None
        self._parts: list[str] = []
        self._lock = threading.Lock()
        self._eof = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._closed = False

    def start(self) -> None:
        try:
            from winpty import PtyProcess           # 可选依赖 extra `pty`
        except ImportError as e:
            raise_code("TLB-807", module="pty",
                       hint=f"pywinpty 未安装({type(e).__name__})",
                       advice="pip install pywinpty 后重试;或改用 exec.shell_run")
        self._p = PtyProcess.spawn(self.command, cwd=str(self.cwd),
                                   env=self.env or None,
                                   dimensions=(self.rows, self.cols))
        self._reader = threading.Thread(target=self._pump, name="pty-reader",
                                        daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        """后台读:pywinpty.read 阻塞到有数据/EOF;缓冲到内存(上限截断)。"""
        total = 0
        while True:
            try:
                data = self._p.read(READ_CHUNK)
            except (EOFError, OSError):              # EOF/管道关闭
                break
            except Exception as e:                   # noqa: BLE001 读面异常即收尾
                log.debug("pty read stopped: %s", type(e).__name__)
                break
            if not data:
                break
            with self._lock:
                if total < MAX_OUTPUT_CHARS:
                    self._parts.append(data)
                    total += len(data)
        self._eof.set()
        # EOF 后在同线程阻塞收尸:把 exitstatus 落定,后续 exit_code() 读面零等待
        # (否则 pywinpty 的 isalive/exitstatus 会让每次调用白等数秒,实测 ~4s)
        try:
            self._p.wait()
        except Exception:                            # noqa: BLE001 收尸失败不影响输出
            pass

    # -------------------------------------------------- 交互
    def write(self, text: str) -> None:
        if self._p is None or self._closed:
            return
        try:
            self._p.write(text)
        except Exception as e:                       # noqa: BLE001 管道已断
            log.debug("pty write failed: %s", type(e).__name__)

    def read(self, timeout: float) -> str:
        """等 timeout 秒;返回自上次调用起累计的新增输出(非阻塞取缓冲)。

        流已关闭(_eof)且缓冲为空 → 立即返回 ""(不再空等,收尾更快)。
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                if self._parts:
                    data = "".join(self._parts)
                    self._parts.clear()
                    return data
            if self._eof.is_set():
                break
            time.sleep(POLL_S)
        with self._lock:
            data = "".join(self._parts)
            self._parts.clear()
        return data

    def finished(self) -> bool:
        """端到端结束判定:PTY 流已关闭(EOF)或进程不可寻 — 收尾优先信号。

        pywinpty 的 isalive() 在子进程退出后可能有秒级滞后(实测 ~4s),故以
        reader 线程观察到的 EOF 为准,避免每次调用白等。
        """
        if self._eof.is_set():
            return True
        try:
            return not self._p.isalive() if self._p is not None else True
        except Exception:                            # noqa: BLE001 读态失败保守为未结束
            return False

    def exit_code(self) -> Optional[int]:
        if self._p is None:
            return None
        try:
            if self._p.isalive():
                return None
        except Exception:                            # noqa: BLE001 读态失败视为未知
            return None
        st = getattr(self._p, "exitstatus", None)
        if st is None:                               # EOF 收尸尚未落定:不阻塞调用方
            return 0
        return int(st)

    def wait(self, timeout: float) -> Optional[int]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            rc = self.exit_code()
            if rc is not None:
                return rc
            if time.monotonic() >= deadline:
                return None
            time.sleep(POLL_S)

    def kill(self) -> None:
        if self._p is None:
            return
        try:
            self._p.terminate(force=True)
        except Exception:                            # noqa: BLE001 收尾尽力
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._p is not None:
            try:
                self._p.terminate(force=True)
            except Exception:                        # noqa: BLE001
                pass
            try:
                self._p.close(force=True)
            except Exception:                        # noqa: BLE001
                pass
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=1.0)


# ------------------------------------------------------------------ POSIX
class _PosixPty:
    """POSIX 伪终端(pty.openpty;与 Windows 分支同接口,非阻塞轮询)。"""

    def __init__(self, command: Union[str, list], *, cwd: Path, env: dict,
                 cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.cols = cols
        self.rows = rows
        self._master: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._closed = False

    def start(self) -> None:
        import pty
        master, slave = pty.openpty()
        try:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", self.rows, self.cols, 0, 0))
            os.set_blocking(master, False)           # 非阻塞轮询(同 Windows 语义)
        except Exception:                            # noqa: BLE001 尺寸/非阻塞尽力
            pass
        self._master = master
        argv = (shlex.split(self.command, posix=True)
                if isinstance(self.command, str) else list(self.command))
        self._proc = subprocess.Popen(
            argv, cwd=str(self.cwd), env=self.env or None,
            stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)

    def write(self, text: str) -> None:
        if self._master is None or self._closed:
            return
        try:
            os.write(self._master, text.encode("utf-8", "replace"))
        except OSError as e:
            log.debug("pty write failed: %s", e)

    def _drain(self) -> str:
        if self._master is None:
            return ""
        out = bytearray()
        while True:
            try:
                chunk = os.read(self._master, READ_CHUNK)
            except (BlockingIOError, OSError):
                break
            if not chunk:
                break
            out.extend(chunk)
            if len(out) >= MAX_OUTPUT_CHARS:
                break
        return bytes(out).decode("utf-8", "replace")

    def read(self, timeout: float) -> str:
        deadline = time.monotonic() + max(0.0, timeout)
        parts: list[str] = []
        while time.monotonic() < deadline:
            chunk = self._drain()
            if chunk:
                parts.append(chunk)
                continue
            if self.exit_code() is not None:
                parts.append(self._drain())
                break
            time.sleep(POLL_S)
        return "".join(parts)

    def exit_code(self) -> Optional[int]:
        if self._proc is None:
            return None
        return self._proc.poll()

    def finished(self) -> bool:
        """端到端结束判定(POSIX:进程已退出)。"""
        return self.exit_code() is not None

    def wait(self, timeout: float) -> Optional[int]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            rc = self.exit_code()
            if rc is not None:
                return rc
            if time.monotonic() >= deadline:
                return None
            time.sleep(POLL_S)

    def kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = None


# ------------------------------------------------------------------ 门面
def pty_supported() -> bool:
    """当前平台是否具备真 PTY(Windows 需 pywinpty;POSIX 需 stdlib pty)。"""
    if os.name == "nt":
        try:
            import winpty                            # noqa: F401
            return True
        except Exception:                            # noqa: BLE001
            return False
    return hasattr(os, "openpty")


def open_pty(command: Union[str, list], *, cwd: Path, env: Optional[dict] = None,
             cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> Any:
    """开一个真 PTY 会话(两平台同接口);不支持 → TLB-807。"""
    if not pty_supported():
        raise_code("TLB-807", module="pty",
                   hint=("pywinpty 未安装(Windows 真 PTY 后端缺失)"
                         if os.name == "nt" else f"平台无 PTY 支持(os={os.name})"),
                   advice="pip install pywinpty(或 extras: pip install '.[pty]');"
                          "或改用 exec.shell_run 行式执行")
    from pyharness.core import proc as proc_mod
    cwd = Path(cwd)
    env = env if env is not None else proc_mod.sandbox_env(cwd)
    cls = _WinPtySession if os.name == "nt" else _PosixPty
    sess = cls(command, cwd=cwd, env=env, cols=cols, rows=rows)
    sess.start()
    return sess


def run_in_pty(command: Union[str, list], *, cwd: Path,
               env: Optional[dict] = None, input_text: str = "",
               timeout_s: int = 60, cols: int = DEFAULT_COLS,
               rows: int = DEFAULT_ROWS) -> dict:
    """一次性 PTY 执行(同步;Provider 经 asyncio.to_thread 调用)。

    语义:开 PTY → (可选)喂 stdin → 收集输出直到退出或墙钟超时 → 超时则 kill
    并标 timed_out=True。env 缺省走 proc.sandbox_env(白名单,cwd 锁沙箱目录)。

    返回 {output, exit_code, timed_out, tty, truncated, elapsed_ms}。
    `tty=True` 表示走的是真 PTY 后端(探针 scripts/probe_pty.py 用子进程
    isatty 探测做硬验证)。
    """
    started = time.monotonic()
    sess = open_pty(command, cwd=Path(cwd), env=env, cols=cols, rows=rows)
    parts: list[str] = []
    timed_out = False
    truncated = False
    total = 0
    # PTY 的 Enter 是 CR(\r);统一归一,避免调用方用 \n 时"输入没反应"
    feed = input_text.replace("\r\n", "\r").replace("\n", "\r") if input_text else ""
    try:
        if feed:
            time.sleep(0.15)                         # 等 shell 就绪再喂(避免丢输入)
            sess.write(feed)
        deadline = started + max(1, int(timeout_s))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            chunk = sess.read(min(0.3, remaining))
            if chunk:
                parts.append(chunk)
                total += len(chunk)
                if total >= MAX_OUTPUT_CHARS:
                    truncated = True
                    break
                continue
            if sess.finished():
                parts.append(sess.read(0.3))         # 取尽残留
                break
        if timed_out:
            sess.kill()
            sess.wait(GRACE_KILL_S)
    finally:
        exit_code = sess.exit_code()
        if exit_code is None:
            exit_code = sess.wait(0.5)
        sess.close()
    return {"output": "".join(parts), "exit_code": exit_code,
            "timed_out": timed_out, "tty": True, "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}


__all__ = ["open_pty", "pty_supported", "run_in_pty", "MAX_OUTPUT_CHARS",
           "DEFAULT_COLS", "DEFAULT_ROWS"]
