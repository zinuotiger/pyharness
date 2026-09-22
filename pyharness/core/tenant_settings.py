"""Tenant-scoped model profiles and encrypted API-key storage.

Metadata (provider/model/base_url/etc.) is separated from secrets.  On Windows
the secret map is protected with the current user's DPAPI key; otherwise the
file is stored with POSIX mode 0600.  Secret values are never returned by the
public/list API.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import secrets
import stat
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.tenant_settings")

_TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RESERVED = {".", ".."}
_STORES: dict[str, "TenantSettingsStore"] = {}
_SESSION_TENANTS: dict[str, set] = {}   # sid → 认领该 sid 的租户集合(多认领=归属未知)


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


def tenants_dir(storage_root: Any) -> Path:
    """**租户根目录的唯一派生点**:``<storage.root>/tenants``。

    R31-2:此前该表达式在 ``TenantSettingsStore`` 的构造处各写一遍;而 per-tenant
    鉴权也要定位同一目录下的 ``token`` 文件,再抄一遍就又是一个"同源规则多份实现"
    (R14-3 教训)。故收口到此,读写两侧共用。
    """
    return Path(str(storage_root)).expanduser() / "tenants"


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

    # ---------------------------------------------------------- per-tenant 令牌
    def token_path(self, tenant_id: str) -> Path:
        """本租户 API 令牌文件(**读写唯一落点**):``<tenants>/<t>/token``。"""
        return self.tenant_dir(tenant_id) / "token"

    def api_token(self, tenant_id: str) -> str:
        """读取本租户令牌;不存在则**生成**(32 字节 hex,原子写 + ``chmod 0600``,后者仅 POSIX 生效)。

        R31-2:per-tenant 鉴权的凭证。**创建路径 = 引导页持操作者令牌时铸/取**
        (``DesktopApp._index_page``);运维也可直接读取该文件交给外部客户端。
        **不落日志、不进 API 响应**。

        权限:POSIX 上为 0600;**Windows 上 ``chmod`` 不生效**,可读面由父目录 ACL
        继承决定(见 ``DesktopApp.publish_operator_token`` 的实测说明)。

        ``default`` 租户**不使用**本机制(沿用全局 ``shell.web.token``,保兼容),
        故调用方只对非 default 租户取用。
        """
        p = self.token_path(tenant_id)
        try:
            if p.is_file():
                tok = p.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
        except OSError:
            pass
        tok = secrets.token_hex(32)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)   # 生成=显式行为,可建目录
        except OSError:
            pass
        self._atomic_write(p, tok.encode("utf-8"))
        try:
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)     # POSIX 0600;Windows 无效(见 docstring)
        except OSError:
            pass
        return tok

    def verify_token(self, tenant_id: str, given: str) -> bool:
        """常数时间比对;未出示 / 无令牌文件 / 不匹配 → ``False``(**只读**)。

        刻意**不调用** :meth:`api_token`:校验发生在**未认证**的请求路径上,若在那里
        "读不到就生成",任意人报一个租户名即可凭空造出令牌与目录(且能读到自己的
        Set-Cookie),整条授权即为空转。故校验只读,生成只走壳/运维。
        """
        if not given:
            return False
        p = self.token_path(tenant_id)
        try:
            if not p.is_file():
                return False
            expected = p.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not expected:
            return False
        return secrets.compare_digest(str(given), expected)

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
    """登记「会话 → 租户」进程内记忆(sid → **租户集合**;不覆盖、可多认领)。

    2026-09-21 修(两轮):①此前**无条件覆写** ⇒ 跨租户 sid 撞名时后写者夺走归属;
    ②改成"先写者胜"后仍不对 —— 进程内会话被删/服务重启时段落不清,旧认领会**挡住**
    新租户的合法登记。会话目录本就**按租户分目录**(sid 只在租户内唯一),故正确模型是
    **集合**:同一 sid 允许被多个租户认领,查询时"多认领 ⇒ 归属未知" → 由
    ``tenant_for_session`` 返回空串,消费方按**未知**走 fail-closed(见
    ``event_tenant_allowed``),绝不给一个**错误的确定答案**。
    """
    sid = str(session_id or "")
    if not sid:
        return
    tid = normalize_tenant_id(tenant_id)
    holders = _SESSION_TENANTS.setdefault(sid, set())
    if holders and tid not in holders:
        log.warning("会话 sid 被多租户认领 sid=%s 已有=%s 新增=%s"
                    "(按租户分目录 ⇒ sid 可跨租户撞名;归属转未知,fail-closed)",
                    sid, sorted(holders), tid)
    holders.add(tid)


def tenant_for_session(session_id: str) -> str:
    """会话归属租户;**多认领(歧义)⇒ 返回 ""**(未知,由消费方 fail-closed)。"""
    holders = _SESSION_TENANTS.get(str(session_id or ""))
    if not holders or len(holders) != 1:
        return ""
    return next(iter(holders))


def session_tenants(session_id: str) -> set:
    """本进程已**认领**该 sid 的租户集合(只读快照)。

    与 ``tenant_for_session`` 的区别:后者把"无人认领"和"多租户抢认"都压成 ``""``,
    而"会话**不存在**"与"会话**归属歧义**"的处置**相反**(前者回落声明、后者必须拒),
    消费方需要区分这两者(desktop ``resolve_request_tenant`` 的 fail-closed 判据)。
    """
    return set(_SESSION_TENANTS.get(str(session_id or ""), ()))


def tenant_of_log(path: Any) -> str:
    """从**落盘日志**取会话租户(GAP-10 的已持久化事实);取不到 → ``""``(未知)。

    与 ``open_session`` 同口径:**最后一条有 ``tenant_id`` 的信封胜出**(日志即既成
    事实,重启后仍可恢复)。只读**尾部窗口**(租户逐条盖在信封上,末条即可代表),
    不解析 payload、**不抛**(不可读 → 未知,由调用方 fail-closed)。

    L-1 闭合用:自此"未登记的磁盘会话"也能拿到**服务端权威**租户,不再只能回落
    客户端自报头。
    """
    import json as _json
    from pathlib import Path as _Path

    p = _Path(str(path))
    try:
        size = p.stat().st_size
        if size <= 0:
            return ""
        window = min(size, 65536)
        with open(p, "rb") as fh:
            if size > window:
                fh.seek(size - window)
                fh.readline()                      # 丢弃半行
            raw = fh.read()
    except OSError:
        return ""
    found = ""
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            env = _json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        t = str(env.get("tenant_id") or "")
        if t:
            found = t                          # 最后一条有值者胜出
    if not found or found in _RESERVED:
        return ""
    try:
        return normalize_tenant_id(found)
    except Exception:                          # noqa: BLE001 非法租户名 → 视为未知
        return ""


def event_tenant_of(payload: Any, *, session_id: str = "") -> str:
    """事件租户归属**唯一派生点**:优先**落盘信封** ``tenant_id``,回落进程内映射。

    为什么优先信封:信封租户是 GAP-10 的**已持久化事实**(随 JSONL 落盘、可按日志
    恢复),对"进程重启后映射为空""跨租户 sid 撞名"都免疫;进程内映射只是记忆。
    """
    env_tenant = str(getattr(payload, "tenant_id", "") or "")
    if env_tenant:
        return normalize_tenant_id(env_tenant)
    sid = session_id or str(getattr(payload, "session_id", "") or "")
    return tenant_for_session(sid) if sid else ""


def event_tenant_allowed(client_tenant: str, event_tenant: str) -> bool:
    """租户事件可见性**唯一判据**(两个外壳共用,禁止各自实现)。

    规则(失败方向统一为**关闭**,与投影层既有语义一致):
      1. 事件租户**已知**:相同 → 允许;不同 → 拒绝;
      2. 事件租户**未知**(既无信封租户又无会话归属):仅 ``default`` 客户端可见。

    2026-09-21 修:此前投影层(``EventStreamHub``)与原生壳(``_event_allowed``)
    **各自实现**且失败方向**相反** —— 原生壳 ``not owner or owner == tenant`` 在
    归属未知时**放行**,跨租户事件可达 UI;投影层 fail-closed。同 N5(通道解析
    六层五种实现)的横切分裂,故收成单点判据。
    """
    client = normalize_tenant_id(client_tenant)
    event = str(event_tenant or "")
    if not event:
        return client == "default"
    return client == normalize_tenant_id(event)


def unregister_session_tenant(session_id: str,
                              tenant_id: Optional[str] = None) -> None:
    """撤销会话租户认领。

    ``tenant_id`` 给定 ⇒ **只撤该租户的认领**(会话删除只需撤自己那份,别把别人的
    认领一起抹掉 —— 多租户同名 sid 时那会让归属从"歧义"变成"错误确定");
    缺省 ⇒ 撤全部认领(测试/整表清理用)。
    """
    sid = str(session_id or "")
    if not sid:
        return
    if tenant_id is None:
        _SESSION_TENANTS.pop(sid, None)
        return
    holders = _SESSION_TENANTS.get(sid)
    if not holders:
        return
    holders.discard(normalize_tenant_id(tenant_id))
    if not holders:
        _SESSION_TENANTS.pop(sid, None)


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
    "tenants_dir",
    "unregister_session_tenant",
    # 租户事件可见性唯一判据与派生点(两壳共用,2026-09-21)
    "event_tenant_of",
    "event_tenant_allowed",
]
