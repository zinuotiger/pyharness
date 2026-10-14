"""WindowSessions operations, separated from facade assembly."""
from __future__ import annotations
import html
import json
from typing import Any
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QInputDialog, QListWidgetItem, QMessageBox, QTableWidgetItem

class WindowSessions:
    """WindowSessions responsibility; state is owned by the composing facade."""

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

