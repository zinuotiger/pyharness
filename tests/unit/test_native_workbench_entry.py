"""Native entry races and actual Qt geometry; not a physical desktop claim."""
import asyncio
import pytest

pytest.importorskip('PySide6',reason='native extra required')
from PySide6.QtWidgets import QApplication,QMainWindow
from shiboken6 import delete
from pyharness import cli
from pyharness.desktop_native.controller import NativeController
from pyharness.desktop_native.main_window import MainWindow
from pyharness.desktop_native.window_geometry import fit_available_screen
from pyharness.desktop_native import window_workbench
from pyharness.desktop_native.workbench import NativeWorkbenchBridge
from tests.support import isolated_settings


@pytest.fixture(scope='module')
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('change',['tenant','dispose'])
async def test_close_or_tenant_change_during_start_never_opens_old_identity(qt_app,tmp_path,monkeypatch,change):
    controller=NativeController(cli.assemble_ctx(isolated_settings(tmp_path)))
    window=MainWindow(controller);entry=window._workbench_entry
    entered=asyncio.Event();release=asyncio.Event();opened=[]
    original=NativeWorkbenchBridge.start
    async def held(bridge):
        result=await original(bridge);entered.set();await release.wait();return result
    monkeypatch.setattr(NativeWorkbenchBridge,'start',held)
    monkeypatch.setattr(window_workbench.QDesktopServices,'openUrl',lambda url:opened.append(url.toString()) or True)
    pending=asyncio.create_task(entry.open_details())
    try:
        await asyncio.wait_for(entered.wait(),5)
        if change=='tenant':controller.switch_tenant('team')
        else:entry.dispose()
        release.set();await pending
        assert opened==[] and window.tabs.count()==9
        controller.switch_tenant('default')
        # An already disposed UI cannot release a bootstrap URL either.
        entry.dispose();await entry.open_details();assert opened==[]
    finally:
        release.set();await pending
        window.dispose();await controller.close();delete(window)


def test_actual_qt_frame_fits_its_screen_and_deleted_window_is_safe(qt_app):
    window=QMainWindow();window.resize(1500,1200);window.show();qt_app.processEvents()
    try:
        fit_available_screen(window);qt_app.processEvents()
        assert window.screen().availableGeometry().contains(window.frameGeometry())
        width,height=window.width(),window.height()
        fit_available_screen(window)
        assert (window.width(),window.height())==(width,height)
    finally:
        window.hide();delete(window)
    fit_available_screen(window)


def test_late_native_callback_after_dispose_does_not_create_unawaited_coroutine(qt_app,tmp_path):
    import gc,warnings
    controller=NativeController(cli.assemble_ctx(isolated_settings(tmp_path)))
    window=MainWindow(controller)
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter('always')
            window.dispose()
            window._spawn(window.refresh_sessions())
            qt_app.processEvents();gc.collect()
        assert not [w for w in captured if issubclass(w.category,RuntimeWarning)],captured
    finally:
        delete(window)
        asyncio.run(controller.close())
