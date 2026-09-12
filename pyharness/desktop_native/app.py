"""Native PySide6 desktop entry point."""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

from PySide6.QtWidgets import QApplication

from pyharness.desktop.launcher import assemble_desktop_ctx

from .controller import NativeController
from .main_window import MainWindow


def main(argv: Optional[list[str]] = None) -> int:
    """Start the native Qt desktop on a qasync-integrated asyncio loop."""
    try:
        import qasync
    except ImportError:
        print("缺少 qasync；请安装: pip install PySide6 qasync", file=sys.stderr)
        return 1

    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("PyHarness Native")
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    try:
        ctx = assemble_desktop_ctx()
    except Exception as exc:                         # noqa: BLE001
        print(f"启动配置装配失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    controller = NativeController(ctx)
    window = MainWindow(controller)
    window.show()
    try:
        with loop:
            loop.run_forever()
            loop.run_until_complete(controller.close())
    except KeyboardInterrupt:
        return 130
    return 0
