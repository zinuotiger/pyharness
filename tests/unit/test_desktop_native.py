from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# GAP-3:PySide6 是可选依赖(native extra)。**显式 skip 而非 collection error**
# —— 此前缺它时整个模块收集失败,默认 pytest 必须 --ignore 本文件才能跑全量;
# CI 上这会被当成"通过"从而掩盖未覆盖事实。skip 是**显式、可观察**的未覆盖声明
# (与 LIMITATIONS L-6 登记一致),不是把失败藏起来。
pytest.importorskip("PySide6", reason="原生壳需要 PySide6(native extra)")

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
