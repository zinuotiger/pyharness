"""Local server startup and readiness helpers."""
from __future__ import annotations

import asyncio
import logging
import socket
import sys
import time

import uvicorn

from pyharness.errors import raise_code

from .constants import HOST, LISTEN_TIMEOUT_S

log = logging.getLogger("pyharness.desktop.net")

def pick_free_port() -> int:
    """127.0.0.1 随机空闲端口(bind 0 取系统分配 → 释放交 uvicorn;竞窗极小,F065 边界)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def run_uvicorn(app: "DesktopApp", port: int) -> None:
    """后台线程 serve:单 worker、禁 reload(seq 单调前提 ADR-005);阻塞至 should_exit。

    偏离 5:手工持 loop(server.serve 在自建 loop 上运行)并把 app.loop 暴露给
    DesktopBridge._run 跨线程投递;线程收尾复位 app.server/app.loop。
    PyInstaller --windowed 打包:进程无 console 句柄 → sys.stderr 为 None,
    uvicorn 默认 formatter 调 isatty() 崩(AttributeError)→ 无 stderr 时
    log_config=None 跳过 dictConfig(用标准 logging,日志进兜底 handler)。
    """
    kw: dict = dict(log_level="warning", workers=1)
    if sys.stderr is None:                    # windowed/frozen 无 stderr:禁用 uvicorn 自配日志
        kw["log_config"] = None
    config = uvicorn.Config(app.api, host=HOST, port=port, **kw)
    server = uvicorn.Server(config)
    app.server = server
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.loop = loop
    try:
        loop.run_until_complete(server.serve(sockets=None))
    except Exception:                       # noqa: BLE001 服务线程异常仅日志(域外 CYC-999)
        log.exception("uvicorn serve 异常退出 port=%s", port)
    finally:
        app.loop = None
        app.server = None
        asyncio.set_event_loop(None)
        loop.close()


def _probe_port(port: int) -> bool:
    """127.0.0.1:port 是否可连(就绪探测;失败静默返回 False)。"""
    try:
        with socket.create_connection((HOST, port), timeout=0.3):
            return True
    except OSError:
        return False


def wait_until_listening(port: int, timeout: float = LISTEN_TIMEOUT_S) -> bool:
    """同步就绪探测(webview 开窗前阻塞;失败 → 调用方退 1 不弹空窗)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _probe_port(port):
            return True
        time.sleep(0.05)
    return False


async def wait_listening_async(app: "DesktopApp", port: int,
                               timeout: float = LISTEN_TIMEOUT_S) -> None:
    """异步就绪探测(run_desktop 用;超时抛 CYC-999 由调用方收口)。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if _probe_port(port):
            return
        if getattr(app, "stopping", None) is not None and app.stopping.is_set():
            raise_code("CYC-999", op="listening", port=port,
                       hint="桌面已请求关闭,取消启动")
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.05)
    raise_code("CYC-999", op="listening", port=port,
               hint="uvicorn 就绪超时(端口/环境不可用),已取消启动")
