"""Native PySide6 desktop entry point."""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

from PySide6.QtWidgets import QApplication

from pyharness.application.bootstrap import assemble_desktop_ctx

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
    controller = None
    failure = None
    exit_code = 0
    try:
        ctx = assemble_desktop_ctx()
        controller = NativeController(ctx)
        window = MainWindow(controller)
        window.show()
        loop.run_forever()
    except KeyboardInterrupt as exc:
        failure, exit_code = exc, 130
    except Exception as exc:                         # noqa: BLE001 entry point
        failure, exit_code = exc, 1
    finally:
        # Keep the loop alive until all service owners have completed cleanup,
        # including partially initialized windows and interrupted GUI execution.
        try:
            if controller is not None:
                loop.run_until_complete(controller.close())
        except BaseException as exc:
            if failure is None:
                failure, exit_code = exc, 1
            else:
                failure.add_note(f"controller cleanup failed: {type(exc).__name__}: {exc}")
        try:
            loop.close()
        except BaseException as exc:
            if failure is None:
                failure, exit_code = exc, 1
            else:
                failure.add_note(f"event loop cleanup failed: {type(exc).__name__}: {exc}")
        finally:
            asyncio.set_event_loop(None)
    if failure is not None:
        print(f"原生入口退出: {type(failure).__name__}: {failure}", file=sys.stderr)
        for note in getattr(failure, '__notes__', ()):
            print(note, file=sys.stderr)
    return exit_code
