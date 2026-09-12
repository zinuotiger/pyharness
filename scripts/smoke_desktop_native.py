"""Offscreen smoke test for the PySide6 native desktop shell."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import qasync  # noqa: E402

from pyharness.desktop.launcher import assemble_desktop_ctx  # noqa: E402
from pyharness.desktop_native.controller import NativeController  # noqa: E402
from pyharness.desktop_native.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication([])
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    ctx = assemble_desktop_ctx()
    controller = NativeController(ctx)
    window = MainWindow(controller)
    window.show()
    QTimer.singleShot(2500, app.quit)
    with loop:
        loop.run_forever()
        loop.run_until_complete(controller.close())
    print("NATIVE_DESKTOP_SMOKE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
