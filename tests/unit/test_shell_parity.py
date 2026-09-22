"""Capability-parity contract for the Web and PySide6 shells."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastapi import FastAPI  # noqa: E402

from pyharness.application import ApplicationService, SHELL_CAPABILITIES  # noqa: E402
from pyharness.desktop.app import DesktopApp  # noqa: E402

# GAP-3:PySide6 是**可选**依赖(``native`` extra)。缺它时不得让整个模块
# collection error —— 此前默认 pytest 必须 ``--ignore`` 本文件才能跑全量,
# 是隐性债务(CI 上会被当成"测试通过"而掩盖)。
try:                                             # pragma: no cover - 环境相关
    from pyharness.desktop_native.controller import NativeController
except Exception:                                # noqa: BLE001 缺 PySide6/qasync
    NativeController = None

_needs_native = pytest.mark.skipif(
    NativeController is None,
    reason="原生壳需要 PySide6(可选依赖 native extra);其余能力契约仍被验证")


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
    # GAP-7 / GAP-8:治理审计与证据读面必须在两壳同时可达
    "governance_audit", "governance_evidence",
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
    # GAP-7 / GAP-8:治理审计与证据读面(读侧 replay,零写)
    ("/api/sessions/{sid}/governance-audit", "GET"),
    ("/api/sessions/{sid}/governance-evidence", "GET"),
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


@_needs_native
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


# ============ R8-4:原生壳在无 PySide6 时也能验证的部分(纯逻辑 + 静态)============
def test_native_event_owner_is_tenant_scoped():
    """订阅属主必须**按租户唯一**(否则关一个租户的控制器会摘掉另一个的订阅)。

    修复前实测:控制器用固定 ``"desktop-native"`` 作属主,而 ``close()`` 按属主摘除
    ⇒ 多租户同总线时,关闭 A 会让 B 的原生窗口**收不到任何事件**。同类缺陷第三次
    出现(engine 订阅属主 / 插件 guard 钩子 / 此处)。
    """
    from pyharness.desktop.constants import native_event_owner

    a, b = native_event_owner("alpha"), native_event_owner("beta")
    assert a != b, "属主串必须随租户变化"
    assert "alpha" in a and "beta" in b
    assert a.startswith("desktop-native")
    assert native_event_owner("") == "desktop-native:default"   # 缺省租户有确定形态


def test_native_controller_uses_tenant_owner_and_shared_tenant_predicate():
    """**静态**(不依赖 Qt):控制器必须用按租户属主 + 共用租户可见性判据。

    控制器模块顶层 import PySide6(可选依赖),无 GUI 环境无法实例化 ⇒ 用源码断言
    锁死这两条契约(它们是纯逻辑,与 Qt 无关)。
    """
    src = (Path(__file__).resolve().parents[2]
           / "pyharness/desktop_native/controller.py").read_text(encoding="utf-8")
    assert "native_event_owner(" in src, "订阅属主必须走按租户单点"
    assert 'owner="desktop-native"' not in src, "仍存在固定属主(多租户会互相摘除)"
    assert "event_tenant_allowed(" in src, "租户可见性必须走共享判据(勿各自实现)"
    assert "tenant_for_session(" not in src, \
        "不得直接读易失归属映射(应经 event_tenant_of:信封优先)"
