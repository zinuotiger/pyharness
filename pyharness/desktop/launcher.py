"""Desktop window and process launcher."""
from __future__ import annotations

import logging
import sys
import threading
from types import SimpleNamespace
from typing import Optional

from pyharness.errors import PyHError

from .app import DesktopApp
from .bridge import DesktopBridge
from .constants import HOST, WINDOW_HEIGHT, WINDOW_TITLE, WINDOW_WIDTH
from .net import pick_free_port, run_uvicorn, wait_listening_async, wait_until_listening

log = logging.getLogger("pyharness.desktop.launcher")

def _load_webview():
    """惰性导入 pywebview(无显示环境模块导入零副作用;启动路径才需 GUI 运行时)。"""
    try:
        import webview
        return webview
    except Exception as exc:                            # noqa: BLE001
        log.error("pywebview 导入失败(需 WebView2 运行时): %s", exc)
        return None


def _create_window(wv: Any, app: DesktopApp, bridge: DesktopBridge):
    """建窗 + 挂关窗事件(events.closing 置 stopping;webview.start 返回后优雅停服)。"""
    window = wv.create_window(WINDOW_TITLE, app.url, js_api=bridge,
                              width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
    try:
        window.events.closing += lambda: app.request_shutdown()
    except Exception:                                   # noqa: BLE001 假窗/旧版无事件面
        log.debug("window.events.closing 不可用,关窗停服由 bridge.on_close 承担")
    return window


def _bootstrap_desktop(cfg_path: Optional[str]) -> Any:
    """装配桌面 ctx(配置 → 总线 → 门面;CFG-601 失败即中止,不弹窗)。"""
    from pyharness import cli as _cli                   # 复读 cli 装配管线(偏离 3)
    cfg = _cli._load_settings(cfg_path)
    return _cli.assemble_ctx(cfg)


def assemble_desktop_ctx(cfg_path: Optional[str] = None) -> SimpleNamespace:
    """桌面 ctx 装配面(与 cli.assemble_ctx 同形状;session=None 由 DesktopApp 惰性补)。"""
    return _bootstrap_desktop(cfg_path)


def main(argv: Optional[list[str]] = None) -> int:
    """进程入口(pyharness-desktop):装配 ctx → uvicorn 后台线程 → webview 开窗 → 关窗优雅停服。

    返回 int 退出码(0 正常 / 1 启动失败:CFG-601 配置错、端口/WebView2 不可用、就绪超时)。
    argv 未用(桌面无 CLI 参数,保留签名对称)。非主线程调 webview 会 RuntimeError(文档注明)。
    """
    try:
        ctx = _bootstrap_desktop(None)
    except PyHError as e:                               # CFG-601 等装配错
        advice = e.ctx.get("advice") or e.ctx.get("hint") or e.spec.advice
        print(f"{e.code}:{advice}", file=__import__("sys").stderr)
        return 1
    app = DesktopApp(ctx=ctx)
    try:
        port = pick_free_port()
    except OSError:
        log.exception("pick_free_port 失败")
        return 1
    app.url = f"http://{HOST}:{port}"
    threading.Thread(target=run_uvicorn, args=(app, port),
                     name="desktop-uvicorn", daemon=True).start()
    if not wait_until_listening(port):
        log.error("uvicorn 就绪超时 port=%s(不弹空窗,退出 1)", port)
        app.shutdown_gracefully()
        return 1
    wv = _load_webview()
    if wv is None:                                      # WebView2/环境缺失:退 1
        print("pywebview 不可用:请安装 Microsoft Edge WebView2 Runtime 后重试",
              file=__import__("sys").stderr)
        app.shutdown_gracefully()
        return 1
    bridge = DesktopBridge(app)
    app.bridge = bridge
    _create_window(wv, app, bridge)
    try:
        wv.start(debug=False)                           # 阻塞至全部窗口关闭
    except Exception:                                   # noqa: BLE001 窗口生命周期未预期
        log.exception("webview.start 未预期退出")
        app.shutdown_gracefully()
        return 1
    app.shutdown_gracefully()                           # 关窗 = 优雅停服
    return 0


async def run_desktop(ctx: Any) -> int:
    """CLI desktop 子命令桥(cli.py 调用):复用已装配 ctx 走同一 DesktopApp 生命周期。

    等价入口:`uv run pyharness desktop`;桌面必须主线程启动(RuntimeError 域外)。
    """
    app = DesktopApp(ctx=ctx)
    port = pick_free_port()
    app.url = f"http://{HOST}:{port}"
    threading.Thread(target=run_uvicorn, args=(app, port),
                     name="desktop-uvicorn", daemon=True).start()
    await wait_listening_async(app, port)
    wv = _load_webview()
    if wv is None:
        app.shutdown_gracefully()
        raise_code("CYC-999", hint="pywebview 不可用(需 WebView2 Runtime)")
    bridge = DesktopBridge(app)
    app.bridge = bridge
    _create_window(wv, app, bridge)
    try:
        wv.start(debug=False)                           # 阻塞至全部窗口关闭(主线程)
    finally:
        app.shutdown_gracefully()
    return 0


__all__ = [
    # 常量
    "WINDOW_TITLE", "WINDOW_WIDTH", "WINDOW_HEIGHT", "HOST",
    "TIMELINE_KINDS", "CHANNEL", "WARN_RATIO",
    # 数据结构
    "TimelineNode", "StreamClient", "EventStreamHub", "DesktopBridge",
    "DesktopApp", "DesktopSessionManager",
    # 纯函数/核心
    "render_timeline_node", "derive_timeline", "approval_node", "redact_args",
    "redact", "pick_free_port", "run_uvicorn", "wait_until_listening",
    "wait_listening_async",
    # 入口
    "main", "run_desktop", "assemble_desktop_ctx",
]
