"""WindowManagement operations, separated from facade assembly."""
from __future__ import annotations
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QListWidgetItem, QMessageBox, QTableWidgetItem, QTreeWidgetItem

class WindowManagement:
    """WindowManagement responsibility; state is owned by the composing facade."""

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
        """审计页(GAP-7):治理因果审计为主,旧遥测计数并列展示。

        修复前本页只调 ``telemetry``(事件类型计数),``AuditSystem`` 虽已构造
        注入却零调用 ⇒ 治理数据在盘上而产品内无入口。
        """
        if not self.sid:
            return
        gov = await self.controller.governance_audit(self.sid, reconcile=True)
        tel = await self.controller.telemetry(self.sid)
        data = {"governance": gov, "telemetry": tel}
        self.audit_view.setPlainText(
            json.dumps(data, ensure_ascii=False, indent=2, default=str))

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

