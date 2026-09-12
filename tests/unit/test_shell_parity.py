"""Capability-parity contract for the Web and PySide6 shells."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastapi import FastAPI  # noqa: E402

from pyharness.application import ApplicationService, SHELL_CAPABILITIES  # noqa: E402
from pyharness.desktop.app import DesktopApp  # noqa: E402
from pyharness.desktop_native.controller import NativeController  # noqa: E402


EXPECTED_CAPABILITIES = {
    "session.list", "session.create", "chat.send", "chat.stream",
    "chat.edit_last_user", "chat.resend_last_user", "chat.feedback_last_agent",
    "chat.attachment", "approval.decide", "ask.answer", "jobs.list",
    "jobs.start", "jobs.cancel", "schedule.list", "schedule.action",
    "subagent.list", "subagent.spawn", "subagent.cancel", "skill.list",
    "skill.registry_search", "skill.install", "skill.rollback", "skill.remove",
    "plugin.list", "plugin.action", "preset.set", "workflow.run",
    "telemetry.read", "tenant.switch", "settings.model_profiles",
}

NATIVE_METHODS = {
    "list_sessions", "create_session", "send_message", "edit_last_user",
    "resend_last_user", "feedback_last_agent", "upload_attachment",
    "jobs", "start_job", "cancel_job", "schedules", "schedule_action",
    "subagents", "spawn_subagent", "cancel_subagent", "skills",
    "search_skills", "install_skill", "rollback_skill", "remove_skill",
    "plugins", "plugin_action", "set_preset", "run_workflow", "telemetry",
    "switch_tenant", "tenant_state", "save_model_profile",
    "activate_model_profile", "delete_model_profile",
}

WEB_ROUTES = {
    ("/api/sessions", "GET"),
    ("/api/sessions/{sid}", "DELETE"),
    ("/api/sessions/{sid}/messages", "POST"),
    ("/api/sessions/{sid}/messages/last-user", "PATCH"),
    ("/api/sessions/{sid}/messages/last-user/resend", "POST"),
    ("/api/sessions/{sid}/messages/last-agent/feedback", "POST"),
    ("/api/attachments", "POST"),
    ("/api/preset", "POST"),
    ("/api/skills/registry/search", "GET"),
    ("/api/sessions/{sid}/skills/install", "POST"),
    ("/api/sessions/{sid}/skills/{name}/rollback", "POST"),
    ("/api/sessions/{sid}/skills/{name}/remove", "POST"),
    ("/api/plugins", "GET"),
    ("/api/sessions/{sid}/jobs", "GET"),
    ("/api/sessions/{sid}/schedules", "GET"),
    ("/api/sessions/{sid}/subagents", "GET"),
    ("/api/sessions/{sid}/workflow", "POST"),
    ("/api/tenant", "GET"),
    ("/api/settings/models", "GET"),
    ("/api/settings/models", "POST"),
    ("/api/settings/models/{profile_id}/activate", "POST"),
    ("/api/settings/models/{profile_id}", "DELETE"),
}


def _mounted_api() -> FastAPI:
    app = object.__new__(DesktopApp)
    app.api = FastAPI()
    app.mount_api()
    return app.api


def test_shared_capability_contract_is_complete():
    assert set(SHELL_CAPABILITIES) == EXPECTED_CAPABILITIES
    contract = ApplicationService.capabilities()
    assert contract["capabilities"] == list(SHELL_CAPABILITIES)
    assert contract["count"] == len(EXPECTED_CAPABILITIES)


def test_web_exposes_all_shell_routes():
    routes = {(r.path, method) for r in _mounted_api().routes
              for method in getattr(r, "methods", set())}
    missing = WEB_ROUTES - routes
    assert not missing, f"Web shell routes missing: {sorted(missing)}"


def test_native_controller_exposes_all_shell_operations():
    missing = {name for name in NATIVE_METHODS
               if not callable(getattr(NativeController, name, None))}
    assert not missing, f"Native controller missing: {sorted(missing)}"
    controller = NativeController.__new__(NativeController)
    controller.service = SimpleNamespace(
        capabilities=ApplicationService.capabilities)
    assert controller.capabilities()["count"] == len(EXPECTED_CAPABILITIES)


def test_both_ui_surfaces_have_parity_entrypoints():
    root = Path(__file__).resolve().parents[2]
    html = (root / "pyharness/ui/index.html").read_text(encoding="utf-8")
    native = (root / "pyharness/desktop_native/main_window.py").read_text(
        encoding="utf-8")
    for token in (
        "btn-workflow", "skill-registry-url", "skill-registry-results",
        "editLastUser", "resendLastUser", "feedbackAgent", "attachOpen",
        "applyPreset", "loadWorkflowPanel", "btn-settings",
        "loadSettingsPanel", "settings/models", "deleteSession",
        "oncontextmenu", "session-menu-delete",
    ):
        assert token in html, f"Web UI missing {token}"
    for token in (
        "_build_workflow_tab", "edit_last_user", "resend_last_user",
        "feedback_last_agent", "choose_attachment", "apply_preset",
        "run_workflow", "preset_combo", "_build_settings_tab",
        "save_model_profile", "switch_tenant",
    ):
        assert token in native, f"Native UI missing {token}"
