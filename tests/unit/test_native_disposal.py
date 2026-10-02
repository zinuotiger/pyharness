"""Exercise Qt object destruction and entry shutdown in isolated processes."""
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_verify import environment


@pytest.mark.controlled_process
@pytest.mark.parametrize("mode", ["delete", "loop-stop"])
def test_native_disposes_before_cpp_teardown(tmp_path, mode):
    pytest.importorskip("PySide6", reason="native extra required")
    pytest.importorskip("qasync", reason="native event loop required")
    root = Path(__file__).resolve().parents[2]
    script = r'''
import sys, asyncio
sys.path.insert(0, sys.argv[1])
from scripts.ci_verify import install_network_boundary
install_network_boundary()
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QTimer
from shiboken6 import delete, isValid
from pyharness.desktop_native.appearance import NativeAppearance
errors = []
sys.excepthook = lambda *args: errors.append(args)
if sys.argv[2] == 'delete':
    app = QApplication([])
    window = QMainWindow()
    host = NativeAppearance(window)
    window.show()
    app.processEvents()
    assert host.banner.timer.isActive()
    delete(window)
    assert host.dead
    assert host.banner.visibility_changed is None
    assert not isValid(host.banner.timer)
    host.destroy()
    assert not errors, [str(e[1]) for e in errors]
else:
    from pyharness.desktop_native import app as entry
    windows = []
    class Window(entry.MainWindow):
        def show(self):
            windows.append(self)
            super().show()
            QTimer.singleShot(50, asyncio.get_event_loop().stop)
    entry.MainWindow = Window
    assert entry.main(['pyharness-native']) == 0
    window = windows[0]
    assert window.appearance.dead
    assert window.appearance.banner.visibility_changed is None
    assert not window.appearance.banner.timer.isActive()
    assert not window._interaction_timer.isActive()
    delete(window)
    assert not errors, [str(e[1]) for e in errors]
print('native disposal verified')
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(root), mode],
        cwd=tmp_path, env=environment(tmp_path / "env"),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native disposal verified" in result.stdout
    assert "RuntimeError" not in result.stderr
    assert "Traceback" not in result.stderr
