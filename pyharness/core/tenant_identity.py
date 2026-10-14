"""Tenant identity: complete migrated implementation."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyharness.core.tenant_settings import TenantSettingsStore
import logging
import re
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
