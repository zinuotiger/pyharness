"""pty 模块单测 — 契约:SECURITY §4 ④「超时杀进程树防孤儿」+ CFG F052「超时杀树」。

覆盖面:CND-05 —— PTY 超时/关闭须**树级**终止(委托 proc._kill_pid_tree),而非只杀
直接进程(否则 shell 的子孙进程遗留 = 载体未真正驱逐 = 孤儿)。需 pty extra(pywinpty);
平台不支持则跳过。
"""
import os
from pathlib import Path

import pytest

from pyharness.core import proc as proc_mod
from pyharness.core import pty as pty_mod


def _pty_command() -> list:
    return ["cmd", "/c", "echo hi"] if os.name == "nt" else ["sh", "-c", "echo hi"]


@pytest.mark.skipif(not pty_mod.pty_supported(),
                    reason="平台无 PTY 支持(需 pywinpty 或 posix pty)")
def test_pty_kill_delegates_to_tree_kill(monkeypatch):
    """CND-05:kill/close 须经**树级**终止(proc._kill_pid_tree)——
    修复前仅 self._p.terminate()/self._proc.kill()(直接单进程)→ 子孙孤儿留存。"""
    calls: list = []
    monkeypatch.setattr(proc_mod, "_kill_pid_tree", lambda pid: calls.append(pid))
    sess = pty_mod.open_pty(_pty_command(), cwd=Path.cwd())
    try:
        sess.kill()
    finally:
        sess.close()
    assert calls and calls[0] > 0        # 树级终止被调用(非直接 kill/terminate)
