"""Tenant isolation and write-only model API-key settings tests."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pyharness.application import ApplicationService
from pyharness.bus import EventBus
from pyharness.config import DEFAULTS, Settings
from pyharness.desktop.app import DesktopApp
from pyharness.core import llm as llm_mod
from pyharness.core.tenant_settings import (
    TenantSettingsStore,
    register_tenant_store,
    resolve_tenant_secret,
)
from pyharness.engine import register_default_llm


def _profile(profile_id: str = "p-openai", model: str = "gpt-test") -> dict:
    return {
        "id": profile_id,
        "label": "OpenAI Test",
        "provider": "OpenAI",
        "model": model,
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-direct-secret-value",
        "temperature": 0.4,
        "max_tokens": 2048,
        "fallback_models": [],
    }


def test_tenant_secret_is_encrypted_and_never_returned(tmp_path):
    store = TenantSettingsStore(tmp_path)
    register_tenant_store("alpha", store)
    saved = store.upsert_profile("alpha", _profile())
    state = store.state("alpha")
    raw_meta = (tmp_path / "alpha" / "models.json").read_text(encoding="utf-8")
    secret_raw = (tmp_path / "alpha" / "model-secrets.bin").read_bytes()

    assert saved["profile"]["has_api_key"] is True
    assert "sk-direct-secret-value" not in raw_meta
    assert b"sk-direct-secret-value" not in secret_raw
    assert "sk-direct-secret-value" not in json.dumps(state, ensure_ascii=False)
    assert resolve_tenant_secret("tenant:alpha:p-openai") == "sk-direct-secret-value"


def test_same_model_in_two_tenants_uses_isolated_adapters(tmp_path):
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    ctx = SimpleNamespace(bus=EventBus(), settings=Settings.model_validate(raw))
    alpha = ApplicationService(ctx, channel="test", tenant_id="alpha")
    beta = ApplicationService(ctx, channel="test", tenant_id="beta")
    alpha.save_model_profile(_profile("same", "same-model"))
    beta_body = _profile("same", "same-model")
    beta_body["api_key"] = "sk-second-secret-value"
    beta.save_model_profile(beta_body)

    cfg_a = alpha.effective_settings()
    cfg_b = beta.effective_settings()
    keys = []
    try:
        keys.append(register_default_llm(cfg_a, force=True))
        keys.append(register_default_llm(cfg_b, force=True))
        assert keys[0] != keys[1]
        a_adapter = llm_mod.adapters[keys[0]]
        b_adapter = llm_mod.adapters[keys[1]]
        assert a_adapter._triple.api_key_ref != b_adapter._triple.api_key_ref
        assert a_adapter._triple.base_url == b_adapter._triple.base_url
    finally:
        for key in keys:
            llm_mod.adapters.pop(key, None)


@pytest.mark.asyncio
async def test_session_storage_is_isolated_by_tenant(tmp_path):
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    raw["storage"]["sessions_dir"] = str(tmp_path / "sessions")
    cfg = Settings.model_validate(raw)
    ctx = SimpleNamespace(bus=EventBus(), settings=cfg)
    alpha = ApplicationService(ctx, channel="test", tenant_id="alpha")
    beta = ApplicationService(ctx, channel="test", tenant_id="beta")

    sid_a = await alpha.create_session()
    sid_b = await beta.create_session()
    assert [row["sid"] for row in (await alpha.list_sessions())["sessions"]] == [sid_a]
    assert [row["sid"] for row in (await beta.list_sessions())["sessions"]] == [sid_b]
    assert (tmp_path / "tenants" / "alpha" / "sessions").is_dir()
    assert (tmp_path / "tenants" / "beta" / "sessions").is_dir()
    assert not (tmp_path / "tenants" / "alpha" / "sessions" / f"{sid_b}.jsonl").exists()
    deleted = await alpha.delete_session(sid_a)
    assert deleted["ok"] is True and deleted["count"] >= 1
    assert not (tmp_path / "tenants" / "alpha" / "sessions" / f"{sid_a}.jsonl").exists()
    assert (await alpha.list_sessions())["count"] == 0
    await alpha.shutdown()
    await beta.shutdown()


@pytest.mark.asyncio
async def test_desktop_service_registry_switches_tenant(tmp_path):
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    app = DesktopApp(SimpleNamespace(
        bus=EventBus(), settings=Settings.model_validate(raw)))

    token = app._tenant_var.set("alpha")
    try:
        saved = await app.save_model_profile(_profile("alpha-profile"))
    finally:
        app._tenant_var.reset(token)
    assert saved["tenant_id"] == "alpha"

    token = app._tenant_var.set("beta")
    try:
        state = await app.list_model_profiles()
    finally:
        app._tenant_var.reset(token)
    assert state["tenant_id"] == "beta"
    assert state["count"] == 0
    assert app._service_registry.get("alpha").tenant_state()["count"] == 1
    assert app._service_registry.get("beta").tenant_state()["count"] == 0
    await app._service_registry.close_all()


def test_settings_http_routes_are_tenant_scoped(tmp_path):
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    app = DesktopApp(SimpleNamespace(
        bus=EventBus(), settings=Settings.model_validate(raw)))
    client = TestClient(app.api, base_url="http://localhost")
    headers = {
        "X-PyHarness-Token": app._api_token,
        "X-PyHarness-Tenant": "alpha",
    }
    created = client.post("/api/settings/models", headers=headers,
                          json=_profile("http-profile"))
    assert created.status_code == 200
    assert created.json()["tenant_id"] == "alpha"

    alpha = client.get("/api/settings/models", headers=headers).json()
    assert alpha["count"] == 1
    beta = client.get("/api/settings/models",
                      headers={**headers, "X-PyHarness-Tenant": "beta"}).json()
    assert beta["tenant_id"] == "beta" and beta["count"] == 0
