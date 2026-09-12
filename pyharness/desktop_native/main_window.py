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


class MainWindow(QMainWindow):
    def __init__(self, controller: NativeController) -> None:
        super().__init__()
        self.controller = controller
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

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        return table

    def _build_timeline_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.timeline_refresh_btn = QPushButton("刷新")
        row.addWidget(self.timeline_refresh_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.timeline = QTreeWidget()
        self.timeline.setHeaderLabels(["#", "类型", "标题", "详情"])
        layout.addWidget(self.timeline)
        self.tabs.addTab(page, "轨迹")

    def _build_jobs_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.job_intent = QLineEdit()
        self.job_intent.setPlaceholderText("后台任务意图…")
        self.job_start_btn = QPushButton("启动")
        self.job_refresh_btn = QPushButton("刷新")
        row.addWidget(self.job_intent, 1)
        row.addWidget(self.job_start_btn)
        row.addWidget(self.job_refresh_btn)
        layout.addLayout(row)
        self.jobs_table = self._table(["Job", "状态", "进度", "耗时", "错误"])
        layout.addWidget(self.jobs_table)
        self.job_cancel_btn = QPushButton("取消选中 Job")
        layout.addWidget(self.job_cancel_btn)
        self.tabs.addTab(page, "Jobs")

    def _build_schedule_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QHBoxLayout()
        self.sch_name = QLineEdit(); self.sch_name.setPlaceholderText("name")
        self.sch_kind = QComboBox(); self.sch_kind.addItems(["cron", "interval", "at"])
        self.sch_expr = QLineEdit(); self.sch_expr.setPlaceholderText("0 9 * * *")
        self.sch_intent = QLineEdit(); self.sch_intent.setPlaceholderText("到点执行的意图")
        self.sch_add_btn = QPushButton("新增")
        form.addWidget(self.sch_name); form.addWidget(self.sch_kind)
        form.addWidget(self.sch_expr); form.addWidget(self.sch_intent, 1)
        form.addWidget(self.sch_add_btn)
        layout.addLayout(form)
        self.schedules_table = self._table(
            ["名称", "类型", "表达式", "状态", "下次触发", "触发/错过"])
        layout.addWidget(self.schedules_table)
        row = QHBoxLayout()
        self.sch_refresh_btn = QPushButton("刷新")
        self.sch_pause_btn = QPushButton("暂停/恢复")
        self.sch_remove_btn = QPushButton("删除")
        row.addWidget(self.sch_refresh_btn); row.addWidget(self.sch_pause_btn)
        row.addWidget(self.sch_remove_btn); row.addStretch(1)
        layout.addLayout(row)
        self.tabs.addTab(page, "定时")

    def _build_subagent_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QHBoxLayout()
        self.sub_task = QLineEdit(); self.sub_task.setPlaceholderText("子任务描述…")
        self.sub_tools = QLineEdit(); self.sub_tools.setPlaceholderText("工具子集，可空")
        self.sub_ratio = QLineEdit("0.25"); self.sub_ratio.setMaximumWidth(70)
        self.sub_spawn_btn = QPushButton("派发")
        form.addWidget(self.sub_task, 1); form.addWidget(self.sub_tools, 1)
        form.addWidget(self.sub_ratio); form.addWidget(self.sub_spawn_btn)
        layout.addLayout(form)
        self.subagents_table = self._table(["Sub ID", "状态", "运行秒数"])
        layout.addWidget(self.subagents_table)
        row = QHBoxLayout()
        self.sub_refresh_btn = QPushButton("刷新")
        self.sub_cancel_btn = QPushButton("取消选中")
        row.addWidget(self.sub_refresh_btn); row.addWidget(self.sub_cancel_btn)
        row.addStretch(1); layout.addLayout(row)
        self.tabs.addTab(page, "子 Agent")

    def _build_skill_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        row = QHBoxLayout()
        self.skill_refresh_btn = QPushButton("重扫本地")
        row.addWidget(self.skill_refresh_btn); row.addStretch(1)
        layout.addLayout(row)

        registry_row = QHBoxLayout()
        self.skill_registry_url = QLineEdit()
        self.skill_registry_url.setPlaceholderText("Skill Registry URL(json)")
        self.skill_query = QLineEdit()
        self.skill_query.setPlaceholderText("搜索名称/描述/标签…")
        self.skill_search_btn = QPushButton("搜索")
        registry_row.addWidget(self.skill_registry_url, 2)
        registry_row.addWidget(self.skill_query, 1)
        registry_row.addWidget(self.skill_search_btn)
        layout.addLayout(registry_row)

        self.skill_results = self._table(["名称", "最新版本", "可用版本", "描述"])
        layout.addWidget(self.skill_results)

        actions = QHBoxLayout()
        self.skill_install_btn = QPushButton("安装选中")
        self.skill_rollback_btn = QPushButton("回滚选中")
        self.skill_remove_btn = QPushButton("卸载选中")
        actions.addWidget(self.skill_install_btn)
        actions.addWidget(self.skill_rollback_btn)
        actions.addWidget(self.skill_remove_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        body = QSplitter(Qt.Horizontal)
        self.skill_list = QListWidget()
        self.skill_preview = QTextBrowser()
        body.addWidget(self.skill_list); body.addWidget(self.skill_preview)
        body.setStretchFactor(0, 0); body.setStretchFactor(1, 1)
        layout.addWidget(body)
        self.tabs.addTab(page, "技能")

    def _build_plugin_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.plugin_id = QLineEdit(); self.plugin_id.setPlaceholderText("插件 id")
        self.plugin_load_btn = QPushButton("装载")
        self.plugin_reload_btn = QPushButton("重载")
        self.plugin_unload_btn = QPushButton("卸载")
        self.plugin_refresh_btn = QPushButton("刷新")
        row.addWidget(self.plugin_id, 1); row.addWidget(self.plugin_load_btn)
        row.addWidget(self.plugin_reload_btn); row.addWidget(self.plugin_unload_btn)
        row.addWidget(self.plugin_refresh_btn)
        layout.addLayout(row)
        self.plugins_table = self._table(["ID", "状态", "版本", "工具", "会话"])
        layout.addWidget(self.plugins_table)
        self.tabs.addTab(page, "插件")

    def _build_workflow_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.workflow_name = QLineEdit("native")
        self.workflow_name.setPlaceholderText("workflow 名称")
        self.workflow_stop = QCheckBox("首步失败即停止")
        self.workflow_run_btn = QPushButton("运行 Workflow")
        row.addWidget(QLabel("名称"))
        row.addWidget(self.workflow_name, 1)
        row.addWidget(self.workflow_stop)
        row.addWidget(self.workflow_run_btn)
        layout.addLayout(row)
        self.workflow_steps = QPlainTextEdit()
        self.workflow_steps.setPlaceholderText("每行一个执行意图，例如：\n读取 README\n总结项目能力")
        layout.addWidget(self.workflow_steps, 1)
        self.workflow_results = QPlainTextEdit()
        self.workflow_results.setReadOnly(True)
        self.workflow_results.setPlaceholderText("运行结果")
        layout.addWidget(self.workflow_results, 1)
        self.tabs.addTab(page, "Workflow")

    def _build_settings_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        tenant_row = QHBoxLayout()
        self.tenant_id = QLineEdit(self.controller.tenant_id)
        self.tenant_id.setPlaceholderText("tenant id")
        self.tenant_switch_btn = QPushButton("切换/创建租户")
        self.settings_refresh_btn = QPushButton("刷新")
        tenant_row.addWidget(QLabel("租户"))
        tenant_row.addWidget(self.tenant_id, 1)
        tenant_row.addWidget(self.tenant_switch_btn)
        tenant_row.addWidget(self.settings_refresh_btn)
        layout.addLayout(tenant_row)
        self.settings_info = QLabel("API Key 只写不回显")
        layout.addWidget(self.settings_info)
        self.model_profiles = self._table(
            ["档案", "Provider", "模型", "Base URL", "API Key", "状态"])
        self.model_profiles.cellClicked.connect(self._profile_selected)
        layout.addWidget(self.model_profiles, 1)
        form = QFormLayout()
        self.mp_id = QLineEdit(); self.mp_id.setPlaceholderText("编辑时保留 ID")
        self.mp_label = QLineEdit(); self.mp_provider = QLineEdit("OpenAI Compatible")
        self.mp_model = QLineEdit(); self.mp_base_url = QLineEdit()
        self.mp_key = QLineEdit(); self.mp_key.setEchoMode(QLineEdit.Password)
        self.mp_key.setPlaceholderText("留空则保持原 Key")
        self.mp_temperature = QLineEdit("0.7")
        self.mp_max_tokens = QLineEdit("4096")
        self.mp_fallbacks = QLineEdit(); self.mp_fallbacks.setPlaceholderText("逗号分隔,可空")
        form.addRow("档案 ID", self.mp_id); form.addRow("显示名称", self.mp_label)
        form.addRow("Provider", self.mp_provider); form.addRow("模型", self.mp_model)
        form.addRow("Base URL", self.mp_base_url); form.addRow("API Key", self.mp_key)
        form.addRow("Temperature", self.mp_temperature); form.addRow("Max Tokens", self.mp_max_tokens)
        form.addRow("备用模型", self.mp_fallbacks)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.mp_save_btn = QPushButton("保存模型")
        self.mp_activate_btn = QPushButton("启用选中")
        self.mp_delete_btn = QPushButton("删除选中")
        actions.addWidget(self.mp_save_btn); actions.addWidget(self.mp_activate_btn)
        actions.addWidget(self.mp_delete_btn); actions.addStretch(1)
        layout.addLayout(actions)
        self.tabs.addTab(page, "设置")

    def _build_audit_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.audit_refresh_btn = QPushButton("生成审计报告")
        layout.addWidget(self.audit_refresh_btn)
        self.audit_view = QPlainTextEdit()
        self.audit_view.setReadOnly(True)
        layout.addWidget(self.audit_view)
        self.tabs.addTab(page, "审计")

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
        task = asyncio.create_task(coro)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception as exc:                       # noqa: BLE001
            self.statusBar().showMessage(f"错误:{type(exc).__name__}: {exc}", 8000)

    async def refresh_sessions(self) -> None:
        data = await self.controller.list_sessions()
        self.session_list.clear()
        for row in data.get("sessions", []):
            sid = str(row.get("sid") or "")
            title = row.get("title") or row.get("preview") or sid
            item = QListWidgetItem(f"{title}\n{sid}")
            item.setData(Qt.UserRole, sid)
            self.session_list.addItem(item)
            if sid == self.sid:
                self.session_list.setCurrentItem(item)
        if not self.sid and data.get("sessions"):
            await self.select_session(str(data["sessions"][0].get("sid")))

    async def new_session(self) -> None:
        sid = await self.controller.create_session()
        self.sid = sid
        self.controller.selected_sid = sid
        await self.refresh_sessions()
        await self.load_selected_session()

    def _session_clicked(self, item: QListWidgetItem) -> None:
        self._spawn(self.select_session(str(item.data(Qt.UserRole))))

    async def select_session(self, sid: str) -> None:
        self.sid = str(sid)
        self.controller.selected_sid = self.sid
        await self.load_selected_session()

    async def load_selected_session(self) -> None:
        if not self.sid:
            return
        await self.refresh_chat()
        await self.refresh_timeline()
        await self.refresh_budget()
        await self.refresh_jobs()
        await self.refresh_schedules()
        await self.refresh_subagents()
        await self.refresh_skills()
        await self.refresh_plugins()
        await self.refresh_model_settings()

    async def refresh_model_settings(self) -> None:
        data = self.controller.tenant_state()
        self.tenant_id.setText(str(data.get("tenant_id") or self.controller.tenant_id))
        self.settings_info.setText(
            f"租户 {data.get('tenant_id')} · 密钥存储 {data.get('secure_storage')} · API Key 不会回显")
        rows = data.get("profiles") or []
        active = str(data.get("active") or "")
        self.model_profiles.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [row.get("id"), row.get("provider"), row.get("model"),
                    row.get("base_url"), "已配置" if row.get("has_api_key") else "缺失",
                    "active" if row.get("id") == active else "inactive"]
            for j, value in enumerate(vals):
                item = QTableWidgetItem("" if value is None else str(value))
                if j == 0:
                    item.setData(Qt.UserRole, row)
                self.model_profiles.setItem(i, j, item)

    def _profile_selected(self, row: int, _col: int) -> None:
        item = self.model_profiles.item(row, 0)
        profile = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(profile, dict):
            return
        self.mp_id.setText(str(profile.get("id") or ""))
        self.mp_label.setText(str(profile.get("label") or ""))
        self.mp_provider.setText(str(profile.get("provider") or ""))
        self.mp_model.setText(str(profile.get("model") or ""))
        self.mp_base_url.setText(str(profile.get("base_url") or ""))
        self.mp_temperature.setText(str(profile.get("temperature", "0.7")))
        self.mp_max_tokens.setText(str(profile.get("max_tokens", "4096")))
        self.mp_fallbacks.setText(",".join(profile.get("fallback_models") or []))
        self.mp_key.clear()
        self.mp_key.setPlaceholderText(
            "已配置,留空保持" if profile.get("has_api_key") else "请输入 API Key")

    async def switch_tenant(self) -> None:
        result = self.controller.switch_tenant(self.tenant_id.text().strip() or "default")
        self.sid = ""
        self.statusBar().showMessage(f"已切换租户:{result['tenant_id']}", 4000)
        await self.refresh_sessions()
        await self.refresh_model_settings()

    async def save_model_profile(self) -> None:
        body = {
            "id": self.mp_id.text().strip(),
            "label": self.mp_label.text().strip(),
            "provider": self.mp_provider.text().strip(),
            "model": self.mp_model.text().strip(),
            "base_url": self.mp_base_url.text().strip(),
            "api_key": self.mp_key.text(),
            "temperature": float(self.mp_temperature.text() or 0.7),
            "max_tokens": int(self.mp_max_tokens.text() or 4096),
            "fallback_models": [x.strip() for x in self.mp_fallbacks.text().split(",")
                                if x.strip()],
        }
        result = self.controller.save_model_profile(body)
        self.mp_key.clear()
        self.statusBar().showMessage(
            "模型已保存；重启程序后对已有引擎生效" if result.get("restart_required")
            else "模型已保存", 5000)
        await self.refresh_model_settings()

    async def activate_model_profile(self) -> None:
        row = self.model_profiles.currentRow()
        if row < 0:
            return
        profile_id = self.model_profiles.item(row, 0).text()
        self.controller.activate_model_profile(profile_id)
        await self.refresh_model_settings()

    async def delete_model_profile(self) -> None:
        row = self.model_profiles.currentRow()
        if row < 0:
            return
        profile_id = self.model_profiles.item(row, 0).text()
        ans = QMessageBox.question(self, "删除模型", f"确认删除 {profile_id}?",
                                   QMessageBox.Yes | QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        self.controller.delete_model_profile(profile_id)
        await self.refresh_model_settings()

    async def refresh_chat(self) -> None:
        if not self.sid:
            return
        data = await self.controller.messages(self.sid)
        self._messages = data.get("messages") or []
        self._render_chat()

    def _render_chat(self) -> None:
        parts = ["<html><body style='font-family:Segoe UI;font-size:13px'>"]
        for msg in self._messages:
            role = msg.get("role", "system")
            content = html.escape(str(msg.get("content") or ""))
            if role == "user":
                parts.append(f"<p><b>你</b><br>{content}</p><hr>")
            elif role == "assistant":
                parts.append(f"<p><b>Agent</b><br>{content}</p><hr>")
            else:
                parts.append(f"<p style='color:#777'><b>{role}</b> {content}</p>")
        if self._stream_text:
            parts.append(f"<p><b>Agent(streaming)</b><br>{html.escape(self._stream_text)}</p>")
        parts.append("</body></html>")
        self.chat.setHtml("".join(parts))
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    async def send_message(self) -> None:
        if not self.sid:
            await self.new_session()
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._stream_text = ""
        await self.controller.send_message(
            self.sid, text, attachments=self._pending_attachments or None)
        self._pending_attachments.clear()
        await self.refresh_chat()

    async def edit_last_user(self) -> None:
        if not self.sid:
            return
        row = next((m for m in reversed(self._messages)
                    if m.get("role") == "user"), None)
        if row is None:
            QMessageBox.information(self, "编辑上条", "当前会话还没有用户消息")
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "编辑上条", "修改后立即重发：", str(row.get("content") or ""))
        if not ok or not text.strip():
            return
        await self.controller.edit_last_user(self.sid, text, resend=True)
        await self.refresh_chat()

    async def resend_last_user(self) -> None:
        if not self.sid:
            return
        await self.controller.resend_last_user(self.sid)
        await self.refresh_chat()

    async def feedback_last_agent(self, kind: str) -> None:
        if not self.sid:
            return
        await self.controller.feedback_last_agent(self.sid, kind)
        self.statusBar().showMessage(f"已记录反馈:{kind}", 3000)

    def choose_attachment(self) -> None:
        if not self.sid:
            QMessageBox.information(self, "附件", "请先新建或打开会话")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择附件", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif);;所有文件 (*)")
        if not path:
            return
        self._pending_attachments.append({"file_path": path})
        self.statusBar().showMessage(f"已附加:{path}（随下一条消息发送）", 5000)

    async def apply_preset(self, preset: str) -> None:
        result = await self.controller.set_preset(preset)
        if not result.get("ok"):
            self.statusBar().showMessage(
                f"切档失败:{result.get('error') or result.get('hint') or ''}", 5000)

    async def run_workflow(self) -> None:
        if not self.sid:
            return
        steps = [line.strip() for line in self.workflow_steps.toPlainText().splitlines()
                 if line.strip()]
        if not steps:
            return
        data = await self.controller.run_workflow(
            self.sid, steps, name=self.workflow_name.text().strip() or "native",
            stop_on_fail=self.workflow_stop.isChecked())
        self.workflow_results.setPlainText(
            json.dumps(data, ensure_ascii=False, indent=2, default=str))

    def _on_chunk(self, payload: Any) -> None:
        self._stream_text += str((payload or {}).get("delta") or "")
        self._render_chat()

    def _on_event(self, type_: str, payload: Any) -> None:
        if type_ == "llm.chunk":
            return
        if type_ in {"user.message", "agent.message", "llm.response", "task.completed",
                     "task.failed", "session.renamed"}:
            self._stream_text = ""
            QTimer.singleShot(80, lambda: self._spawn(self.refresh_chat()))
            QTimer.singleShot(80, lambda: self._spawn(self.refresh_budget()))
        if type_.startswith(("tool.", "guard.", "approval.", "segment.", "task.")):
            QTimer.singleShot(80, lambda: self._spawn(self.refresh_timeline()))

    async def refresh_timeline(self) -> None:
        if not self.sid:
            return
        data = await self.controller.timeline(self.sid)
        self.timeline.clear()
        for node in data.get("nodes", []):
            detail = node.get("detail")
            text = json.dumps(detail, ensure_ascii=False, default=str) if not isinstance(detail, str) else detail
            self.timeline.addTopLevelItem(QTreeWidgetItem([
                str(node.get("seq")), str(node.get("kind")), str(node.get("title")), text[:300]]))
        self.timeline.resizeColumnToContents(0)

    async def refresh_budget(self) -> None:
        if not self.sid:
            return
        data = await self.controller.budget(self.sid)
        if data.get("disabled"):
            self.budget_label.setText("预算 disabled")
        else:
            self.budget_label.setText(
                f"预算 in={data.get('used_in_tokens', 0)} out={data.get('used_out_tokens', 0)} "
                f"¥{data.get('used_cny', 0):.4f}")

    async def refresh_jobs(self) -> None:
        if not self.sid:
            return
        data = await self.controller.jobs(self.sid)
        rows = data.get("jobs", [])
        self.jobs_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            cells = [row.get("job_id"), row.get("state"), row.get("todo_summary"),
                     row.get("elapsed_ms"), row.get("error")]
            for j, value in enumerate(cells):
                self.jobs_table.setItem(i, j, QTableWidgetItem("" if value is None else str(value)))

    async def start_job(self) -> None:
        if not self.sid or not self.job_intent.text().strip():
            return
        await self.controller.start_job(self.sid, self.job_intent.text().strip())
        self.job_intent.clear()
        await self.refresh_jobs()

    async def cancel_job(self) -> None:
        row = self.jobs_table.currentRow()
        if row < 0 or not self.sid:
            return
        job_id = self.jobs_table.item(row, 0).text()
        await self.controller.cancel_job(self.sid, job_id)
        await self.refresh_jobs()

    async def refresh_schedules(self) -> None:
        if not self.sid:
            return
        data = await self.controller.schedules(self.sid)
        rows = data.get("schedules", [])
        self.schedules_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [row.get("name"), row.get("kind"), row.get("expr"),
                    "paused" if row.get("paused") else "active",
                    row.get("next_fire_at"),
                    f"{row.get('triggered', 0)}/{row.get('missed', 0)}"]
            for j, value in enumerate(vals):
                self.schedules_table.setItem(i, j, QTableWidgetItem("" if value is None else str(value)))

    async def add_schedule(self) -> None:
        if not self.sid or not self.sch_name.text().strip() or not self.sch_expr.text().strip() or not self.sch_intent.text().strip():
            return
        await self.controller.schedule_action(
            self.sid, action="add", name=self.sch_name.text().strip(),
            kind=self.sch_kind.currentText(), expr=self.sch_expr.text().strip(),
            intent=self.sch_intent.text().strip())
        self.sch_name.clear(); self.sch_expr.clear(); self.sch_intent.clear()
        await self.refresh_schedules()

    async def toggle_schedule(self) -> None:
        row = self.schedules_table.currentRow()
        if row < 0 or not self.sid:
            return
        name = self.schedules_table.item(row, 0).text()
        action = "resume" if self.schedules_table.item(row, 3).text() == "paused" else "pause"
        await self.controller.schedule_action(self.sid, action=action, name=name)
        await self.refresh_schedules()

    async def remove_schedule(self) -> None:
        row = self.schedules_table.currentRow()
        if row < 0 or not self.sid:
            return
        name = self.schedules_table.item(row, 0).text()
        await self.controller.schedule_action(self.sid, action="remove", name=name)
        await self.refresh_schedules()

    async def refresh_subagents(self) -> None:
        if not self.sid:
            return
        data = await self.controller.subagents(self.sid)
        rows = (data.get("status") or {}).get("active_children", [])
        self.subagents_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [row.get("sub_id"), row.get("state"), row.get("age_s")]
            for j, value in enumerate(vals):
                self.subagents_table.setItem(i, j, QTableWidgetItem("" if value is None else str(value)))

    async def spawn_subagent(self) -> None:
        task = self.sub_task.text().strip()
        if not self.sid or not task:
            return
        tools = [x.strip() for x in self.sub_tools.text().split(",") if x.strip()]
        try:
            ratio = float(self.sub_ratio.text() or "0.25")
        except ValueError:
            ratio = 0.25
        await self.controller.spawn_subagent(
            self.sid, task, tools_subset=tools or None, budget_ratio=ratio)
        self.sub_task.clear(); self.sub_tools.clear()
        await self.refresh_subagents()

    async def cancel_subagent(self) -> None:
        row = self.subagents_table.currentRow()
        if row < 0 or not self.sid:
            return
        sub_id = self.subagents_table.item(row, 0).text()
        await self.controller.cancel_subagent(self.sid, sub_id)
        await self.refresh_subagents()

    async def refresh_skills(self) -> None:
        data = await self.controller.skills()
        self.skill_list.clear()
        for row in data.get("skills", []):
            item = QListWidgetItem(f"{row.get('name')} — {row.get('description')}")
            item.setData(Qt.UserRole, row.get("name"))
            self.skill_list.addItem(item)

    def _skill_clicked(self, item: QListWidgetItem) -> None:
        self._spawn(self.show_skill(str(item.data(Qt.UserRole))))

    async def show_skill(self, name: str) -> None:
        data = await self.controller.skill_detail(name)
        self.skill_preview.setPlainText(
            f"{data.get('name')}\n{data.get('description')}\n\n{data.get('body')}")

    async def search_skills(self) -> None:
        data = await self.controller.search_skills(
            self.skill_query.text().strip(), self.skill_registry_url.text().strip())
        rows = data.get("skills", [])
        self.skill_results.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [row.get("name"), row.get("latest"),
                    ", ".join(row.get("versions") or []), row.get("description")]
            for j, value in enumerate(vals):
                self.skill_results.setItem(i, j, QTableWidgetItem("" if value is None else str(value)))

    async def install_skill(self) -> None:
        row = self.skill_results.currentRow()
        if row < 0 or not self.sid:
            return
        name = self.skill_results.item(row, 0).text()
        version = self.skill_results.item(row, 1).text()
        ans = QMessageBox.question(
            self, "安装 Skill",
            f"安装 {name} {version}?\n将下载到 quarantine，校验 SHA-256 后安装。",
            QMessageBox.Yes | QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        await self.controller.install_skill(
            self.sid, name, version=version,
            registry_url=self.skill_registry_url.text().strip(),
            approved_by="native-ui")
        await self.refresh_skills()

    async def rollback_skill(self) -> None:
        item = self.skill_list.currentItem()
        if item is None or not self.sid:
            return
        name = str(item.data(Qt.UserRole))
        versions = self.controller.skill_versions(name)
        if not versions:
            QMessageBox.information(self, "Skill 回滚", "没有可回滚的版本")
            return
        version, ok = QInputDialog.getItem(self, "Skill 回滚", "选择版本", versions, 0, False)
        if not ok:
            return
        await self.controller.rollback_skill(
            self.sid, name, version, approved_by="native-ui")
        await self.refresh_skills()

    async def remove_skill(self) -> None:
        item = self.skill_list.currentItem()
        if item is None or not self.sid:
            return
        name = str(item.data(Qt.UserRole))
        ans = QMessageBox.question(self, "卸载 Skill", f"确认卸载 {name}?",
                                   QMessageBox.Yes | QMessageBox.No)
        if ans == QMessageBox.Yes:
            await self.controller.remove_skill(
                self.sid, name, approved_by="native-ui")
            await self.refresh_skills()

    async def refresh_plugins(self) -> None:
        data = await self.controller.plugins()
        rows = data.get("plugins", [])
        self.plugins_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [row.get("id"), row.get("state"), row.get("version"),
                    ", ".join(row.get("tools") or []), row.get("session")]
            for j, value in enumerate(vals):
                self.plugins_table.setItem(i, j, QTableWidgetItem("" if value is None else str(value)))

    async def plugin_action(self, action: str) -> None:
        pid = self.plugin_id.text().strip()
        if not pid:
            return
        await self.controller.plugin_action(action, {"id": pid})
        await self.refresh_plugins()

    async def refresh_audit(self) -> None:
        if not self.sid:
            return
        data = await self.controller.telemetry(self.sid)
        self.audit_view.setPlainText(json.dumps(data, ensure_ascii=False, indent=2, default=str))

    def _poll_interactions(self) -> None:
        if self.sid:
            self._spawn(self.check_interactions())

    async def check_interactions(self) -> None:
        approvals = await self.controller.pending_approvals()
        for row in approvals.get("pending", []):
            key = (str(row.get("session_id") or ""), int(row["approval_id"]))
            if key in self._seen_approvals or str(row.get("session_id") or "") != self.sid:
                continue
            text = (f"工具:{row.get('tool')}\n风险:{row.get('risk')}\n参数:{row.get('args_summary')}")
            ans = QMessageBox.question(self, "工具审批请求", text, QMessageBox.Yes | QMessageBox.No)
            if ans == QMessageBox.Yes:
                await self.controller.decide_approval(int(row["approval_id"]), "approve", sid=self.sid)
                self._seen_approvals.add(key)
            elif ans == QMessageBox.No:
                await self.controller.decide_approval(int(row["approval_id"]), "deny", sid=self.sid)
                self._seen_approvals.add(key)

        asks = await self.controller.pending_asks()
        for row in asks.get("pending", []):
            key = (str(row.get("session_id") or ""), int(row["ask_id"]))
            if key in self._seen_asks or str(row.get("session_id") or "") != self.sid:
                continue
            question = str(row.get("question") or "Agent 有问题")
            options = list(row.get("options") or [])
            if options:
                value, ok = QInputDialog.getItem(self, "Agent 提问", question, options, 0, True)
                choice, text = (value, None) if ok else (None, None)
            else:
                value, ok = QInputDialog.getText(self, "Agent 提问", question)
                choice, text = (None, value) if ok else (None, None)
            if ok:
                await self.controller.answer_ask(int(row["ask_id"]), choice=choice, text=text, sid=self.sid)
                self._seen_asks.add(key)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._spawn(self.controller.close())
        super().closeEvent(event)
