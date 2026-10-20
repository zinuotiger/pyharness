"""Native details entry; browser details retain the original HTTP identity checks."""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from pyharness.application.accounts import current_actor


class NativeWorkbenchEntry:
    def __init__(self, window):
        self.window = window
        self.dead = False
        self.busy = False
        self.menu = window.menuBar().addMenu('工作台')
        self.action = QAction('完整工作台（沿用原账户授权）', window)
        self.menu.addAction(self.action)
        self.action.triggered.connect(lambda: window._spawn(self.open_details()))

    def identity(self):
        controller = self.window.controller
        actor = current_actor.get()
        return (controller.service, controller.tenant_id,
                tuple(sorted(actor.items())) if actor else None)

    async def open_details(self):
        if self.dead or self.busy:
            return
        self.busy = True
        self.action.setEnabled(False)
        identity = self.identity()
        try:
            # Keep the native-only extra usable without optional Web packages.
            from .workbench import NativeWorkbenchBridge
            controller = self.window.controller
            bridge = getattr(controller, '_native_workbench', None)
            if bridge is None:
                bridge = NativeWorkbenchBridge(controller)
                controller._native_workbench = bridge
            self.window.statusBar().showMessage('正在启用本机完整工作台…')
            await bridge.start()
            if self.dead or identity != self.identity():
                return
            url = bridge.url_for_current_identity()
            if not QDesktopServices.openUrl(QUrl(url)):
                raise RuntimeError('native_workbench_browser_not_opened')
            self.window.statusBar().showMessage('完整工作台已打开；团队账户仍需原登录授权。', 8000)
        except ImportError:
            if not self.dead:
                self.window.statusBar().showMessage('完整工作台需要安装 web 扩展依赖。', 8000)
        except Exception:
            if not self.dead:
                self.window.statusBar().showMessage('完整工作台未确认打开；没有提交或重发任务。', 8000)
        finally:
            self.busy = False
            if not self.dead:
                self.action.setEnabled(True)

    def dispose(self):
        self.dead = True
        self.action.setEnabled(False)
