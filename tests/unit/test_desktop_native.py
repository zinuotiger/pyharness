from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pyharness.bus import EventBus  # noqa: E402
from pyharness.desktop_native.controller import NativeController  # noqa: E402
from pyharness.desktop_native.main_window import MainWindow  # noqa: E402


class _Service:
    async def list_sessions(self):
        return {"sessions": [], "count": 0}

    async def shutdown(self):
        return None

    def tenant_state(self):
        return {"tenant_id": "default", "active": "", "profiles": [],
                "count": 0, "secure_storage": "test"}


def test_native_controller_signal_and_window_construct():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    controller = NativeController(SimpleNamespace(bus=EventBus()),
                                  service=_Service())
    got = []
    controller.chunk_received.connect(lambda payload: got.append(payload["delta"]))
    controller._on_bus_event("llm.chunk", {"delta": "x"})
    assert got == ["x"]

    window = MainWindow(controller)
    assert window.windowTitle() == "PyHarness Native"
    assert window.tabs.count() == 9
    assert window.session_list.count() == 0
