"""Tenant-scoped model profiles and encrypted API-key storage.

Metadata (provider/model/base_url/etc.) is separated from secrets.  On Windows
the secret map is protected with the current user's DPAPI key; otherwise the
file is stored with POSIX mode 0600.  Secret values are never returned by the
public/list API.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import stat
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import raise_code

_TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RESERVED = {".", ".."}
_STORES: dict[str, "TenantSettingsStore"] = {}
_SESSION_TENANTS: dict[str, str] = {}


def normalize_tenant_id(value: Any) -> str:
    tenant = str(value or "default").strip()
    if tenant in _RESERVED or not _TENANT_RE.fullmatch(tenant):
        raise_code("EVT-100", field="tenant_id", tenant=tenant,
                   advice="tenant_id 仅允许 1-64 位字母/数字/._-")
    return tenant


def normalize_profile_id(value: Any) -> str:
    profile = str(value or "").strip()
    if not _PROFILE_RE.fullmatch(profile) or profile in _RESERVED:
        raise_code("EVT-100", field="profile_id", profile=profile,
                   advice="profile_id 仅允许 1-64 位字母/数字/._-")
    return profile


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buf = ctypes.create_string_buffer(data)
    return (_DataBlob(len(data), ctypes.cast(
        buf, ctypes.POINTER(ctypes.c_byte))), buf)


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI unavailable")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    in_blob, keepalive = _blob(data)
    out_blob = _DataBlob()
    description = ctypes.c_wchar_p("PyHarness tenant secret")
    desc_arg = ctypes.byref(description) if protect else None
    ok = fn(ctypes.byref(in_blob), desc_arg, None, None, None,
            0x01, ctypes.byref(out_blob))  # CRYPTPROTECT_UI_FORBIDDEN
    del keepalive
    if not ok:
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


class TenantSettingsStore:
    """Filesystem-backed tenant settings with write-only model secrets."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def tenant_dir(self, tenant_id: str) -> Path:
        return self.root / normalize_tenant_id(tenant_id)

    def _paths(self, tenant_id: str) -> tuple[Path, Path]:
        d = self.tenant_dir(tenant_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / "models.json", d / "model-secrets.bin"

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def _load_doc(self, tenant_id: str) -> dict:
        meta, _ = self._paths(tenant_id)
        if not meta.exists():
            return {"active": "", "profiles": []}
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:          # noqa: BLE001
            raise_code("CFG-601", reason="tenant_settings", file=str(meta),
                       detail=f"{type(exc).__name__}:{exc}")
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
            raise_code("CFG-601", reason="tenant_settings", file=str(meta),
                       detail="models.json 必须包含 profiles 数组")
        return data

    def _save_doc(self, tenant_id: str, doc: dict) -> None:
        meta, _ = self._paths(tenant_id)
        self._atomic_write(meta, json.dumps(
            doc, ensure_ascii=False, indent=2).encode("utf-8"))

    def _load_secrets(self, tenant_id: str) -> dict[str, str]:
        _, secret_path = self._paths(tenant_id)
        if not secret_path.exists():
            return {}
        raw = secret_path.read_bytes()
        try:
            plain = _dpapi(raw, protect=False) if os.name == "nt" else raw
            data = json.loads(plain.decode("utf-8"))
        except Exception as exc:                       # noqa: BLE001
            raise_code("CRED-703", reason="tenant_secret_decrypt",
                       detail=f"{type(exc).__name__}:{exc}",
                       advice="无法解密当前租户的模型密钥文件")
        if not isinstance(data, dict):
            raise_code("CRED-703", reason="tenant_secret_shape",
                       advice="租户密钥文件格式非法")
        return {str(k): str(v) for k, v in data.items()}

    def _save_secrets(self, tenant_id: str, secrets: dict[str, str]) -> None:
        _, secret_path = self._paths(tenant_id)
        plain = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
        raw = _dpapi(plain, protect=True) if os.name == "nt" else plain
        self._atomic_write(secret_path, raw)
        try:
            os.chmod(secret_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    @staticmethod
    def _mask_ref(profile: dict) -> str:
        if profile.get("has_api_key"):
            return "encrypted"
        ref = str(profile.get("api_key_ref") or "")
        if ref.startswith("env:"):
            return ref
        if ref.startswith("file:"):
            return "file:***"
        return ""

    def public_profile(self, profile: dict) -> dict:
        row = {k: v for k, v in profile.items() if k != "api_key_ref"}
        # Compatibility for older metadata written before has_api_key existed.
        if not row.get("has_api_key") and row.get("api_key_source") == "encrypted":
            row["has_api_key"] = True
        row["api_key_source"] = self._mask_ref(profile)
        return row

    def list_tenants(self) -> list[str]:
        names = {"default"}
        try:
            names.update(p.name for p in self.root.iterdir()
                         if p.is_dir() and _TENANT_RE.fullmatch(p.name))
        except OSError:
            pass
        return sorted(names)

    def state(self, tenant_id: str) -> dict:
        tenant = normalize_tenant_id(tenant_id)
        doc = self._load_doc(tenant)
        profiles = [self.public_profile(dict(p)) for p in doc["profiles"]
                    if isinstance(p, dict)]
        return {"tenant_id": tenant, "active": str(doc.get("active") or ""),
                "profiles": profiles, "count": len(profiles),
                "tenants": self.list_tenants(),
                "secure_storage": "windows-dpapi" if os.name == "nt" else "posix-0600"}

    def upsert_profile(self, tenant_id: str, body: dict) -> dict:
        tenant = normalize_tenant_id(tenant_id)
        doc = self._load_doc(tenant)
        profile_id = str((body or {}).get("id") or "").strip()
        if not profile_id:
            profile_id = f"p-{os.urandom(4).hex()}"
        profile_id = normalize_profile_id(profile_id)
        model = str((body or {}).get("model") or "").strip()
        base_url = str((body or {}).get("base_url") or "").strip().rstrip("/")
        if not model:
            raise_code("EVT-100", field="model", advice="模型名称不能为空")
        if not base_url.startswith(("http://", "https://")):
            raise_code("EVT-100", field="base_url",
                       advice="base_url 必须以 http:// 或 https:// 开头")
        api_key = str((body or {}).get("api_key") or "").strip()
        api_key_ref = str((body or {}).get("api_key_ref") or "").strip()
        if api_key.startswith(("env:", "file:")):
            api_key_ref, api_key = api_key, ""
        if api_key_ref and not api_key_ref.startswith(("env:", "file:")):
            raise_code("EVT-100", field="api_key_ref",
                       advice="api_key_ref 只允许 env:NAME 或 file:PATH")

        profiles = [dict(p) for p in doc.get("profiles", [])
                    if isinstance(p, dict)]
        current = next((p for p in profiles if str(p.get("id")) == profile_id), {})
        secrets = self._load_secrets(tenant)
        if api_key:
            secrets[profile_id] = api_key
            api_key_ref = ""
        elif profile_id in secrets:
            pass
        elif api_key_ref:
            secrets.pop(profile_id, None)
        elif current.get("api_key_ref"):
            api_key_ref = str(current.get("api_key_ref"))
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": profile_id,
            "label": str((body or {}).get("label") or
                         current.get("label") or model),
            "provider": str((body or {}).get("provider") or
                            current.get("provider") or "OpenAI Compatible"),
            "model": model,
            "base_url": base_url,
            "temperature": float((body or {}).get("temperature",
                                                  current.get("temperature", 0.7))),
            "max_tokens": int((body or {}).get("max_tokens",
                                               current.get("max_tokens", 4096))),
            "fallback_models": list((body or {}).get("fallback_models") or
                                    current.get("fallback_models") or []),
            "api_key_ref": api_key_ref,
            "has_api_key": bool(profile_id in secrets or api_key_ref),
            "created_at": current.get("created_at") or now,
            "updated_at": now,
        }
        if not 0 <= row["temperature"] <= 1.5:
            raise_code("EVT-100", field="temperature", advice="temperature 须在 0-1.5")
        if not 1 <= row["max_tokens"] <= 32768:
            raise_code("EVT-100", field="max_tokens", advice="max_tokens 须在 1-32768")
        profiles = [p for p in profiles if str(p.get("id")) != profile_id]
        profiles.append(row)
        profiles.sort(key=lambda p: str(p.get("label") or p.get("id")))
        doc["profiles"] = profiles
        if not doc.get("active"):
            doc["active"] = profile_id
        self._save_secrets(tenant, secrets)
        self._save_doc(tenant, doc)
        return {"ok": True, "tenant_id": tenant,
                "profile": self.public_profile(row),
                "active": str(doc.get("active") or "")}

    def activate_profile(self, tenant_id: str, profile_id: str) -> dict:
        tenant = normalize_tenant_id(tenant_id)
        profile_id = normalize_profile_id(profile_id)
        doc = self._load_doc(tenant)
        if not any(str(p.get("id")) == profile_id for p in doc.get("profiles", [])):
            raise_code("EVT-101", profile_id=profile_id,
                       advice="模型档案不存在")
        doc["active"] = profile_id
        self._save_doc(tenant, doc)
        return {"ok": True, "tenant_id": tenant, "active": profile_id}

    def delete_profile(self, tenant_id: str, profile_id: str) -> dict:
        tenant = normalize_tenant_id(tenant_id)
        profile_id = normalize_profile_id(profile_id)
        doc = self._load_doc(tenant)
        before = len(doc.get("profiles", []))
        doc["profiles"] = [p for p in doc.get("profiles", [])
                           if str(p.get("id")) != profile_id]
        if doc.get("active") == profile_id:
            doc["active"] = str(doc["profiles"][0].get("id")) if doc["profiles"] else ""
        secrets = self._load_secrets(tenant)
        removed_secret = secrets.pop(profile_id, None) is not None
        self._save_secrets(tenant, secrets)
        self._save_doc(tenant, doc)
        return {"ok": before != len(doc["profiles"]), "tenant_id": tenant,
                "profile_id": profile_id, "active": str(doc.get("active") or ""),
                "secret_removed": removed_secret}

    def active_profile(self, tenant_id: str) -> Optional[dict]:
        tenant = normalize_tenant_id(tenant_id)
        doc = self._load_doc(tenant)
        active = str(doc.get("active") or "")
        if not active:
            return None
        return next((dict(p) for p in doc.get("profiles", [])
                     if str(p.get("id")) == active), None)

    def resolve_ref(self, ref: str) -> str:
        parts = str(ref or "").split(":", 2)
        if len(parts) != 3 or parts[0] != "tenant":
            raise_code("CRED-701", ref=ref, reason="tenant_ref")
        tenant = normalize_tenant_id(parts[1])
        profile_id = normalize_profile_id(parts[2])
        secrets = self._load_secrets(tenant)
        value = str(secrets.get(profile_id) or "")
        if value:
            return value
        profile = next((p for p in self._load_doc(tenant).get("profiles", [])
                        if str(p.get("id")) == profile_id), None)
        fallback = str((profile or {}).get("api_key_ref") or "")
        if fallback.startswith(("env:", "file:")):
            from pyharness.core.llm import resolve_secret_ref
            return resolve_secret_ref(fallback)
        raise_code("CRED-701", ref=ref, reason="missing",
                   advice=f"租户 {tenant} 的模型档案 {profile_id} 未配置 API Key")


def register_tenant_store(tenant_id: str, store: TenantSettingsStore) -> None:
    _STORES[normalize_tenant_id(tenant_id)] = store


def register_session_tenant(session_id: str, tenant_id: str) -> None:
    sid = str(session_id or "")
    if sid:
        _SESSION_TENANTS[sid] = normalize_tenant_id(tenant_id)


def tenant_for_session(session_id: str) -> str:
    return _SESSION_TENANTS.get(str(session_id or ""), "")


def unregister_session_tenant(session_id: str) -> None:
    _SESSION_TENANTS.pop(str(session_id or ""), None)


def resolve_tenant_secret(ref: str) -> str:
    parts = str(ref or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "tenant":
        raise_code("CRED-701", ref=ref, reason="tenant_ref")
    tenant = normalize_tenant_id(parts[1])
    store = _STORES.get(tenant)
    if store is None:
        raise_code("CRED-701", ref=ref, reason="tenant_store_unavailable",
                   advice=f"租户 {tenant} 尚未装配")
    return store.resolve_ref(ref)


__all__ = [
    "TenantSettingsStore",
    "normalize_profile_id",
    "normalize_tenant_id",
    "register_session_tenant",
    "register_tenant_store",
    "resolve_tenant_secret",
    "tenant_for_session",
    "unregister_session_tenant",
]
