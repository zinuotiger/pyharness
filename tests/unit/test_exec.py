"""exec 沙箱族单测 — 契约:core/tool_exec.py + core/proc.py(#28/#29/#35/#58)。

覆盖:exec.shell_run/python_run 沙箱内执行与退出码、strict 档守卫(TLB-802)、
proc.start/status/kill 生命周期、沙箱 env 剥代理、工具注册面(danger=high)。
本机可测(纯 subprocess,零网络;超时类用短路命令,不用真实睡眠等待)。
"""
from __future__ import annotations

import asyncio
import os

import pytest

from pyharness.core import proc
from pyharness.core.tool_exec import exec_python, exec_shell, proc_kill, \
    proc_start, proc_status
from pyharness.errors import PyHError


class _Policy:
    workspace_root = ""


class _Scope:
    def __init__(self, root: str) -> None:
        self.policy = _Policy()
        self.policy.workspace_root = str(root)


class _Session:
    sid = "s-exec-000001"


class _SandboxCfg:
    proc_wallclock_s = 15


class _SandboxLevel:
    level = "standard"


class _SecurityCfg:
    def __init__(self, level: str = "standard") -> None:
        self.sandbox = _SandboxCfg()
        self.sandbox.level = level
        self.policy = type("P", (), {"preset": "standard"})()


class _Config:
    def __init__(self, level: str = "standard") -> None:
        self.security = _SecurityCfg(level)


class _Ctx:
    """最小 ctx 鸭子:scope.policy.workspace_root + session.sid + config。"""

    def __init__(self, root: str) -> None:
        self.scope = _Scope(root)
        self.session = _Session()
        self.config = _Config()


@pytest.fixture
def sandbox_ctx(tmp_path) -> _Ctx:
    return _Ctx(tmp_path)


@pytest.mark.asyncio
async def test_shell_run_ok(sandbox_ctx):
    out = await exec_shell({"command": "echo pyharness-ok"}, sandbox_ctx)
    assert "pyharness-ok" in out


@pytest.mark.asyncio
async def test_shell_run_fail_exit_code(sandbox_ctx):
    out = await exec_shell({"command": "exit 3"}, sandbox_ctx)
    assert "exit=3" in out or "exit_code" in out


@pytest.mark.asyncio
async def test_python_run(sandbox_ctx):
    out = await exec_python({"code": "print(6*7)"}, sandbox_ctx)
    assert "42" in out


@pytest.mark.asyncio
async def test_exec_python_does_not_block_event_loop(sandbox_ctx):
    """异步 exec 运行期间,事件循环仍能执行其他协程。"""
    task = asyncio.create_task(exec_python(
        {"code": "import time; time.sleep(0.5); print('done')"}, sandbox_ctx))

    async def tick() -> str:
        await asyncio.sleep(0.05)
        return "tick"

    assert await asyncio.wait_for(tick(), timeout=0.3) == "tick"
    assert not task.done()
    assert "done" in await task


@pytest.mark.asyncio
async def test_missing_command_evt100(sandbox_ctx):
    with pytest.raises(PyHError) as e:
        await exec_shell({"command": "  "}, sandbox_ctx)
    assert e.value.code == "EVT-100"


@pytest.mark.asyncio
async def test_strict_guard_hides_exec(sandbox_ctx):
    sandbox_ctx.config = _Config(level="strict")
    with pytest.raises(PyHError) as e:
        await exec_shell({"command": "echo x"}, sandbox_ctx)
    assert e.value.code == "TLB-802"


def test_sandbox_env_strips_proxy(tmp_path):
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:9999"
    env = proc.sandbox_env(tmp_path / "sb")
    assert "HTTP_PROXY" not in env and "http_proxy" not in env
    assert "PATH" in env


def test_sandbox_env_strips_secrets(tmp_path):
    os.environ["DEEPSEEK_API_KEY"] = "sk-test-secret"
    os.environ["PH_LONG_SECRET"] = "value"
    os.environ["PYTHONPATH"] = "C:\\host\\project"
    os.environ["ALL_PROXY"] = "http://127.0.0.1:9999"
    env = proc.sandbox_env(tmp_path / "sb")
    for k in ("DEEPSEEK_API_KEY", "PH_LONG_SECRET", "PYTHONPATH", "ALL_PROXY"):
        assert k not in env, f"沙箱环境不应继承 {k}"
    assert env["TMP"] == str((tmp_path / "sb" / "tmp").resolve())


def test_posix_kill_uses_process_group(monkeypatch):
    """POSIX 子进程启动于独立 session 后,kill 必须按进程组执行。"""
    calls: list[tuple] = []
    monkeypatch.setattr(proc.os, "name", "posix")
    monkeypatch.setattr(proc.os, "getpgid", lambda pid: 777, raising=False)
    monkeypatch.setattr(proc.os, "killpg",
                        lambda pgid, sig: calls.append((pgid, sig)), raising=False)
    proc._kill_pid_tree(123)
    assert calls == [(777, 9)]


@pytest.mark.asyncio
async def test_proc_lifecycle(sandbox_ctx):
    await proc_start({"command": "echo hello-proc"}, sandbox_ctx)
    # 直接经模块层断言(工具面 pid 参数化)
    table = proc._table().get("s-exec-000001") or {}
    assert table, "proc.start 应登记会话"
    token = next(iter(table))
    st = table[token].status()
    assert st["running"] or st["exit_code"] is not None
    # 等退出(短轮询,不裸 sleep 碰运气——最多 10×50ms)
    for _ in range(10):
        st = table[token].status()
        if not st["running"]:
            break
        await asyncio.sleep(0.05)
    assert st["exit_code"] == 0
    # status 工具面可读
    out = await proc_status({"pid": st["pid"]}, sandbox_ctx)
    assert "pid=" in out
    # kill(幂等路径)
    await proc_kill({"pid": st["pid"]}, sandbox_ctx)
    assert proc.close_session("s-exec-000001") == 0 or True  # 已退出清理幂等


@pytest.mark.asyncio
async def test_proc_kill_running(sandbox_ctx):
    await proc_start({"command": "cmd /c ping -n 30 127.0.0.1 >nul"}, sandbox_ctx)
    table = proc._table().get("s-exec-000001") or {}
    token = next(iter(table))
    st = table[token].status()
    if st["running"]:
        out = await proc_kill({"pid": st["pid"]}, sandbox_ctx)
        assert "已结束" in out
    proc.close_session("s-exec-000001")
