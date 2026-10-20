"""PySide6 Widgets main window for the native PyHarness shell."""
from __future__ import annotations

import asyncio
import html
import json
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .controller import NativeController


from pyharness.desktop_native.window_tabs import WindowTabs
from pyharness.desktop_native.window_sessions import WindowSessions
from pyharness.desktop_native.window_management import WindowManagement

class MainWindow(WindowTabs, WindowSessions, WindowManagement, QMainWindow):
    def __init__(self, controller: NativeController) -> None:
        super().__init__()
        self.controller = controller
        self._disposed = False
        self.sid = ""
        self._messages: list[dict] = []
        self._stream_text = ""
        self._seen_approvals: set[tuple[str, int]] = set()
        self._pending_attachments: list[dict] = []
        self._seen_asks: set[tuple[str, int]] = set()

        self.setWindowTitle("PyHarness Native")
        self.resize(1380, 860)
        self._build_ui()
        self.skill_registry_url.setText(controller.default_registry_url)
        self._wire_signals()
        from pathlib import Path
        from .appearance import NativeAppearance
        storage = getattr(controller.service, '_storage_root', None)
        appearance_path = Path(storage())/'appearance'/'native.ini' if callable(storage) else None
        self.appearance = NativeAppearance(self, appearance_path)
        from .window_workbench import NativeWorkbenchEntry
        self._workbench_entry = NativeWorkbenchEntry(self)

        self._interaction_timer = QTimer(self)
        self._interaction_timer.timeout.connect(self._poll_interactions)
        self._interaction_timer.start(2000)

        QTimer.singleShot(0, lambda: self._spawn(self.refresh_sessions()))

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        split = QSplitter(Qt.Horizontal, self)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("会话"))
        self.session_list = QListWidget()
        lv.addWidget(self.session_list, 1)
        self.new_session_btn = QPushButton("+ 新会话")
        lv.addWidget(self.new_session_btn)
        left.setMinimumWidth(210)
        left.setMaximumWidth(320)
        split.addWidget(left)

        center = QWidget()
        cv = QVBoxLayout(center)
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        cv.addWidget(self.chat, 1)

        ops = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["strict", "standard", "readonly", "locked"])
        self.preset_combo.setToolTip("权限档位:standard 可见 exec.*,strict 隐藏")
        self.edit_last_btn = QPushButton("编辑上条")
        self.resend_last_btn = QPushButton("重发上条")
        self.feedback_up_btn = QPushButton("👍")
        self.feedback_down_btn = QPushButton("👎")
        self.feedback_flag_btn = QPushButton("🚩")
        self.attach_btn = QPushButton("📎 附件")
        for widget in (self.preset_combo, self.edit_last_btn, self.resend_last_btn,
                       self.feedback_up_btn, self.feedback_down_btn,
                       self.feedback_flag_btn, self.attach_btn):
            ops.addWidget(widget)
        ops.addStretch(1)
        cv.addLayout(ops)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入问题，Enter 发送…")
        self.send_btn = QPushButton("发送")
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        cv.addLayout(row)
        center.setMinimumWidth(420)
        split.addWidget(center)

        self.tabs = QTabWidget()
        self._build_timeline_tab()
        self._build_jobs_tab()
        self._build_schedule_tab()
        self._build_subagent_tab()
        self._build_skill_tab()
        self._build_plugin_tab()
        self._build_workflow_tab()
        self._build_settings_tab()
        self._build_audit_tab()
        self.tabs.setMinimumWidth(460)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 1)
        self.setCentralWidget(split)

        self.budget_label = QLabel("预算 --")
        self.statusBar().addPermanentWidget(self.budget_label)


    def _wire_signals(self) -> None:
        self.new_session_btn.clicked.connect(lambda: self._spawn(self.new_session()))
        self.session_list.itemClicked.connect(self._session_clicked)
        self.send_btn.clicked.connect(lambda: self._spawn(self.send_message()))
        self.input.returnPressed.connect(lambda: self._spawn(self.send_message()))
        self.edit_last_btn.clicked.connect(lambda: self._spawn(self.edit_last_user()))
        self.resend_last_btn.clicked.connect(lambda: self._spawn(self.resend_last_user()))
        self.feedback_up_btn.clicked.connect(lambda: self._spawn(self.feedback_last_agent("up")))
        self.feedback_down_btn.clicked.connect(lambda: self._spawn(self.feedback_last_agent("down")))
        self.feedback_flag_btn.clicked.connect(lambda: self._spawn(self.feedback_last_agent("flag")))
        self.attach_btn.clicked.connect(self.choose_attachment)
        self.preset_combo.currentTextChanged.connect(
            lambda preset: self._spawn(self.apply_preset(preset)))
        self.controller.chunk_received.connect(self._on_chunk)
        self.controller.event_received.connect(self._on_event)
        self.controller.sessions_changed.connect(
            lambda: QTimer.singleShot(100, lambda: self._spawn(self.refresh_sessions())))
        self.controller.jobs_changed.connect(
            lambda: QTimer.singleShot(100, lambda: self._spawn(self.refresh_jobs())))
        self.controller.schedules_changed.connect(
            lambda: QTimer.singleShot(100, lambda: self._spawn(self.refresh_schedules())))
        self.controller.subagents_changed.connect(
            lambda: QTimer.singleShot(100, lambda: self._spawn(self.refresh_subagents())))
        self.controller.error.connect(lambda msg: self.statusBar().showMessage(msg, 5000))

        self.timeline_refresh_btn.clicked.connect(
            lambda: self._spawn(self.refresh_timeline()))
        self.job_start_btn.clicked.connect(lambda: self._spawn(self.start_job()))
        self.job_refresh_btn.clicked.connect(lambda: self._spawn(self.refresh_jobs()))
        self.job_cancel_btn.clicked.connect(lambda: self._spawn(self.cancel_job()))
        self.sch_add_btn.clicked.connect(lambda: self._spawn(self.add_schedule()))
        self.sch_refresh_btn.clicked.connect(lambda: self._spawn(self.refresh_schedules()))
        self.sch_pause_btn.clicked.connect(lambda: self._spawn(self.toggle_schedule()))
        self.sch_remove_btn.clicked.connect(lambda: self._spawn(self.remove_schedule()))
        self.sub_spawn_btn.clicked.connect(lambda: self._spawn(self.spawn_subagent()))
        self.sub_refresh_btn.clicked.connect(lambda: self._spawn(self.refresh_subagents()))
        self.sub_cancel_btn.clicked.connect(lambda: self._spawn(self.cancel_subagent()))
        self.skill_refresh_btn.clicked.connect(lambda: self._spawn(self.refresh_skills()))
        self.skill_list.itemClicked.connect(self._skill_clicked)
        self.skill_search_btn.clicked.connect(lambda: self._spawn(self.search_skills()))
        self.skill_install_btn.clicked.connect(lambda: self._spawn(self.install_skill()))
        self.skill_rollback_btn.clicked.connect(lambda: self._spawn(self.rollback_skill()))
        self.skill_remove_btn.clicked.connect(lambda: self._spawn(self.remove_skill()))
        self.plugin_refresh_btn.clicked.connect(lambda: self._spawn(self.refresh_plugins()))
        self.plugin_load_btn.clicked.connect(lambda: self._spawn(self.plugin_action("load")))
        self.plugin_reload_btn.clicked.connect(lambda: self._spawn(self.plugin_action("reload")))
        self.plugin_unload_btn.clicked.connect(lambda: self._spawn(self.plugin_action("unload")))
        self.workflow_run_btn.clicked.connect(lambda: self._spawn(self.run_workflow()))
        self.settings_refresh_btn.clicked.connect(lambda: self._spawn(self.refresh_model_settings()))
        self.tenant_switch_btn.clicked.connect(lambda: self._spawn(self.switch_tenant()))
        self.mp_save_btn.clicked.connect(lambda: self._spawn(self.save_model_profile()))
        self.mp_activate_btn.clicked.connect(lambda: self._spawn(self.activate_model_profile()))
        self.mp_delete_btn.clicked.connect(lambda: self._spawn(self.delete_model_profile()))
        self.audit_refresh_btn.clicked.connect(lambda: self._spawn(self.refresh_audit()))

    def _spawn(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._disposed or loop is None:
            close = getattr(coro, 'close', None)
            if callable(close):
                close()
            return
        task = loop.create_task(coro)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception as exc:                       # noqa: BLE001
            if not self._disposed:
                self.statusBar().showMessage(f"错误:{type(exc).__name__}: {exc}", 8000)


    def showEvent(self, event) -> None:
        super().showEvent(event)
        from .window_geometry import fit_available_screen
        QTimer.singleShot(0, lambda: fit_available_screen(self))
        handle = self.windowHandle()
        if handle is not None and not getattr(self, "_screen_fit_connected", False):
            self._screen_fit_connected = True
            handle.screenChanged.connect(
                lambda screen: QTimer.singleShot(0, lambda: fit_available_screen(self)))

    def dispose(self) -> None:
        """Detach GUI resources even when the event loop stops before close."""
        self._disposed = True
        self._workbench_entry.dispose()
        control = getattr(self, 'product_execution_control', None)
        if control is not None:
            control.dispose()
        self.appearance.destroy()
        self._interaction_timer.stop()
        self.hide()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.dispose()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass  # The native entry's finally block remains the service owner.
        else:
            loop.create_task(self.controller.close()).add_done_callback(self._task_done)
        super().closeEvent(event)
