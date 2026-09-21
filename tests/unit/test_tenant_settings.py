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
    alpha = ApplicationService(ctx, channel="desktop", tenant_id="alpha")
    beta = ApplicationService(ctx, channel="desktop", tenant_id="beta")
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
    alpha = ApplicationService(ctx, channel="desktop", tenant_id="alpha")
    beta = ApplicationService(ctx, channel="desktop", tenant_id="beta")

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


# ================== 租户事件可见性:单点判据 + 归属不被跨租户 sid 撞名夺走
def test_tenant_event_visibility_is_single_fail_closed_predicate():
    """**唯一判据**:已知同租户放行 / 已知异租户拒 / **未知 fail-closed**（仅 default）。

    修复前:原生壳 ``not owner or owner == tenant`` 在归属**未知时放行**
    （fail-open ⇒ 跨租户事件可达 UI）,而 Web 壳投影层对同一问题 fail-closed
    —— 两壳各自实现且失败方向相反（同 N5 的横切分裂）。
    """
    from pyharness.core.tenant_settings import event_tenant_allowed

    assert event_tenant_allowed("alpha", "alpha") is True
    assert event_tenant_allowed("alpha", "beta") is False
    assert event_tenant_allowed("alpha", "") is False      # 未知 → 非 default 拒
    assert event_tenant_allowed("default", "") is True     # 未知 → default 可见
    assert event_tenant_allowed("default", "alpha") is False


def test_event_tenant_prefers_persisted_envelope_over_registry():
    """归属派生优先**落盘信封**(GAP-10),对"重启后映射为空/撞名"免疫。"""
    from pyharness.core.tenant_settings import (event_tenant_of,
                                                register_session_tenant,
                                                unregister_session_tenant)

    sid = "s-inv-tenant-pref-1"
    unregister_session_tenant(sid)
    try:
        env = SimpleNamespace(session_id=sid, tenant_id="alpha")
        # 映射为空(模拟重启后未登记):仍能由信封得到正确归属
        assert event_tenant_of(env, session_id=sid) == "alpha"
        # 映射被登记为另一租户(模拟撞名污染):信封仍胜出
        register_session_tenant(sid, "beta")
        assert event_tenant_of(env, session_id=sid) == "alpha"
        # 无信封租户 → 回落映射
        bare = SimpleNamespace(session_id=sid, tenant_id=None)
        assert event_tenant_of(bare, session_id=sid) == "beta"
    finally:
        unregister_session_tenant(sid)


def test_session_tenant_binding_is_not_stolen_by_cross_tenant_sid():
    """跨租户同名 sid:**不得给出错误确定答案** —— 多认领 ⇒ 归属未知(fail-closed)。

    两轮修复:①原实现无条件覆写(后写者夺走归属);②"先写者胜"仍不对 —— 进程内
    会话被删/服务重启时段落不清,旧认领会**挡住**新租户的合法登记。现模型 =
    sid → **认领集合**:单认领才给确定答案;多认领 ⇒ ``""``(未知),消费方按未知
    fail-closed(``event_tenant_allowed`` 对非 default 客户端拒收)。
    """
    from pyharness.core.tenant_settings import (register_session_tenant,
                                                tenant_for_session,
                                                unregister_session_tenant)

    sid = "s-inv-tenant-clash-1"
    unregister_session_tenant(sid)
    try:
        register_session_tenant(sid, "alpha")
        assert tenant_for_session(sid) == "alpha"          # 单认领:确定
        register_session_tenant(sid, "beta")
        assert tenant_for_session(sid) == "", "多认领必须转未知,不得任选其一"
        # 撤销其一 → 恢复确定(证明认领集合语义,而非"先写者永久占位")
        unregister_session_tenant(sid, "alpha")
        assert tenant_for_session(sid) == "beta"
        unregister_session_tenant(sid, "beta")
        assert tenant_for_session(sid) == ""
    finally:
        unregister_session_tenant(sid)


@pytest.mark.asyncio
async def test_projection_does_not_leak_events_across_tenants():
    """**端到端投递面**:信封租户决定投递;映射被撞名污染时信封仍胜出。

    修复前:归属取自**进程内映射**(重启后为空 / 可被跨租户撞名污染)→ 投影层会
    按错误租户投递或漏投;原生壳同问题更严重(fail-open)。现两壳共用同一判据
    且以**落盘信封**为准。
    """
    from pyharness.core.tenant_settings import (register_session_tenant,
                                                unregister_session_tenant)
    from pyharness.desktop.projection import EventStreamHub, StreamClient

    sid = "s-inv-tenant-sse-1"
    unregister_session_tenant(sid)
    hub = EventStreamHub()
    alpha = StreamClient(sid=sid, tenant="alpha")
    beta = StreamClient(sid=sid, tenant="beta")
    default = StreamClient(sid=sid, tenant="default")

    def _drain(c) -> int:
        n = 0
        while not c.queue.empty():
            c.queue.get_nowait()
            n += 1
        return n

    def _env(seq: int, tenant: str):
        from pyharness.events.envelope import Envelope
        return Envelope(seq=seq, ts="2026-09-21T00:00:00.000000Z",
                        type="agent.message", session_id=sid,
                        actor="agent", payload={"text": "hi"},
                        tenant_id=tenant)

    try:
        async with hub.register(alpha), hub.register(beta), hub.register(default):
            await hub.forward("agent.message", _env(3, "alpha"))
            assert _drain(alpha) == 1, "本租户客户端应收"
            assert _drain(beta) == 0, "异租户客户端不得收"
            assert _drain(default) == 0, "default 客户端不得收异租户事件"

        # 映射被撞名污染 → 信封租户必须胜出(不得按错误映射投递)
        register_session_tenant(sid, "beta")
        async with hub.register(alpha), hub.register(beta):
            await hub.forward("agent.message", _env(4, "alpha"))
            assert _drain(alpha) == 1 and _drain(beta) == 0, \
                "映射被污染时信封租户必须胜出"

        # 未知归属(无信封租户且无映射)→ 非 default 客户端 fail-closed
        from pyharness.events.envelope import Envelope
        hub2 = EventStreamHub()
        ghost_sid = "s-inv-tenant-ghost-1"
        ghost = StreamClient(sid=ghost_sid, tenant="beta")
        async with hub2.register(ghost):
            await hub2.forward("agent.message", Envelope(
                seq=1, ts="2026-09-21T00:00:00.000000Z",
                type="agent.message", session_id=ghost_sid, actor="agent",
                payload={"text": "x"}, tenant_id=None))
            assert _drain(ghost) == 0, "归属未知时非 default 客户端必须拒收"
    finally:
        unregister_session_tenant(sid)


def _tenant_svc(tmp_path, tenant_id):
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    ctx = SimpleNamespace(bus=EventBus(), settings=Settings.model_validate(raw))
    return ApplicationService(ctx, channel="desktop", tenant_id=tenant_id)


def test_index_db_is_tenant_scoped(tmp_path):
    """**R14-8 回归**:派生**索引库**必须与**会话目录**同域(按租户分)。

    修复前 ``effective_settings`` 只覆盖 ``llm.*``,``storage.db_path`` 全租户共用
    全局库 ⇒ ①``query`` 没有任何租户维度,且 ``session.fts_query`` 的 ``session_id``
    取自 LLM 自报参数 ⇒ A 的 agent 能检索到 B 的会话内容(实测命中)②同名 sid 跨租户
    在 ``fts_rows`` 主键 ``(session_id, seq)`` 上碰撞 ⇒ 后写者被静默丢弃、内容不入索引。
    """
    from pathlib import Path

    alpha, beta = _tenant_svc(tmp_path, "alpha"), _tenant_svc(tmp_path, "beta")
    default = _tenant_svc(tmp_path, "default")
    pa = Path(alpha.effective_settings().storage.db_path)
    pb = Path(beta.effective_settings().storage.db_path)
    pd = Path(default.effective_settings().storage.db_path)

    assert pa != pb, "两租户的索引库必须分开"
    assert pa == alpha.tenant_root() / "index.db"
    assert pb == beta.tenant_root() / "index.db"
    # 与会话目录同域:同一个租户根下(sessions 与 index.db 并列)
    root_a = alpha.tenant_root()
    assert pa.parent == root_a and (root_a / "sessions").parent == root_a
    assert pd == Path(DEFAULTS["storage"]["db_path"]), \
        "default 租户保持全局库(行为不变,且原样返回 cfg)"
    assert "tenants" not in pd.parts, "default 不落到租户根下"


async def test_same_sid_in_two_tenants_does_not_collide_in_index(tmp_path):
    """**R14-8 ②**:同名 sid 跨租户不得在索引里碰撞(修复前后写者静默不入索引)。"""
    from pyharness.core.session import SessionLog
    from pyharness.core.session_query import SessionQueryIndex, read_max_seq

    sid = "s-samesid01"                    # 两租户同名(R12-2 已认定为真实场景)
    seen = {}
    for tenant, text in (("alpha", "ALPHA-ONLY-MARKER"), ("beta", "BETA-ONLY-MARKER")):
        db = _tenant_svc(tmp_path, tenant).effective_settings().storage.db_path
        log_ = SessionLog(sid=sid)
        await log_.append("session.created", {"title": "", "model": "m"},
                          actor="system")
        await log_.append("user.message", {"content": text}, actor="user")
        ix = SessionQueryIndex(db_path=db, sources={sid: log_}, batch_ms=60_000)
        await ix.enter(SimpleNamespace(bus=EventBus()))
        for e in log_.events_after(0):
            await ix.on_event(e.type, e)
        await ix.flush()
        hits = (await ix.query("MARKER")).hits
        seen[tenant] = ([h.snippet for h in hits], read_max_seq(db, sid))
        await ix.detach(None)

    a_hits, a_wm = seen["alpha"]
    b_hits, b_wm = seen["beta"]
    assert a_wm == 2 and b_wm == 2, "同名 sid 在两个库里都必须完整入索引(不再被丢弃)"
    assert all("ALPHA" in s for s in a_hits) and a_hits, \
        f"alpha 库只能看到自己的内容:{a_hits}"
    assert all("BETA" in s for s in b_hits) and b_hits, \
        f"beta 库只能看到自己的内容:{b_hits}"


# ================ L-1 租户隔离:会话归属是**服务端权威**,客户端头只是声明
@pytest.fixture(autouse=True)
def _clean_session_tenant_registry():
    """进程内认领表是**模块级全局**;跨用例残留会让归属判定带上"上一用例的租户"。

    这是被测实现的真实性质(同一进程内多次请求共享该表),用例之间必须洗净,否则
    失败会是**顺序相关**的假象。本文件所有用例统一前置/后置清空。
    """
    from pyharness.core.tenant_settings import _SESSION_TENANTS
    _SESSION_TENANTS.clear()
    yield
    _SESSION_TENANTS.clear()


def _tenant_app(tmp_path):
    """装配 root+sessions_dir 都指向 tmp 的桌面 app(避免触碰真实 ~/.pyharness)。"""
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    raw["storage"]["sessions_dir"] = str(tmp_path / "sessions")
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    app = DesktopApp(SimpleNamespace(bus=EventBus(),
                                     settings=Settings.model_validate(raw)))
    return app


def _write_session(tmp_path, sid, tenant=None):
    """默认(全局)sessions 目录下的会话;``tenant=None`` ⇒ 归属**不可得**。"""
    _write_log(tmp_path / "sessions", sid, tenant)


def _write_log(directory, sid, tenant, text="payload"):
    from pyharness.events import Envelope
    directory.mkdir(parents=True, exist_ok=True)
    e1 = Envelope(seq=1, ts="2026-09-07T06:00:00.000000Z", type="session.created",
                  session_id=sid, actor="system",
                  payload={"title": "", "model": "m"}, tenant_id=tenant)
    e2 = Envelope(seq=2, ts="2026-09-07T06:00:01.000000Z", type="user.message",
                  session_id=sid, actor="user", payload={"content": text},
                  tenant_id=tenant)
    (directory / f"{sid}.jsonl").write_text(
        e1.model_dump_json() + "\n" + e2.model_dump_json() + "\n", encoding="utf-8")


def _req(path, tenant_header, query=b""):
    from starlette.requests import Request
    hdrs = [] if tenant_header is None else [
        (b"x-pyharness-tenant", tenant_header.encode())]
    return Request({"type": "http", "method": "GET", "path": path,
                    "query_string": query, "headers": hdrs})


def _resolve(app, sid, header, query=b"", path=None):
    return app.resolve_request_tenant(
        _req(path or f"/api/sessions/{sid}/messages", header, query))


def test_request_tenant_fail_closed_when_ownership_unavailable(tmp_path):
    """**归属不可得**(日志在盘但未载租户)⇒ ``None``(拒绝),**不回落**客户端头。

    与"会话不存在"**处置相反**:前者必须拒(有数据可泄),后者才允许回落(只会 404)。
    二者在磁盘上的差别只是"文件在不在",故 ``_disk_owners`` 必须把"读不出归属"
    单独标出,不能与"无命中"混为一谈。
    """
    sid = "s-l1nolog001"
    _write_session(tmp_path, sid, tenant=None)          # 日志存在但无 tenant_id
    app = _tenant_app(tmp_path)
    assert _resolve(app, sid, "beta") is None, "归属不可得必须拒绝,不得回落伪造头"
    assert _resolve(app, sid, "default") is None, "声明与自身相同的租户也不放行"


def test_request_tenant_rejects_header_conflicting_with_persisted_owner(tmp_path):
    """**声明 ≠ 权威归属 ⇒ 拒绝**(不再"改判为真实归属")。

    本轮前版把该情形解析为"真实归属 alpha" ⇒ 服务端把**声明了 beta 的请求交给
    alpha 的租户服务作答**,连**不带头**的请求都能拿到 alpha 的会话正文(实测
    200 + 正文)。头只声明分区,不是归属真相来源,更不是改判依据。
    """
    sid = "s-l1logalpha1"
    _write_session(tmp_path, sid, tenant="alpha")
    app = _tenant_app(tmp_path)
    assert _resolve(app, sid, "alpha") == "alpha"       # 声明与归属相符 ⇒ 放行
    assert _resolve(app, sid, "beta") is None, "伪造头夺不走归属,也不得改判"
    assert _resolve(app, sid, None) is None, "不带头 == 声明 default,与归属不符 ⇒ 拒"


def test_request_tenant_owner_is_server_derived_for_tenant_scoped_storage(tmp_path):
    """权威面覆盖**租户隔离目录** ``tenants/<t>/sessions``(不止默认 sessions 目录)。"""
    sid = "s-l1tenantb01"
    _write_log(tmp_path / "tenants" / "beta" / "sessions", sid, "beta", "BETA-ONLY")
    app = _tenant_app(tmp_path)
    assert _resolve(app, sid, "beta") == "beta"
    assert _resolve(app, sid, "alpha") is None, "跨租户声明必须拒"
    assert _resolve(app, sid, "default") is None


def test_global_dir_session_is_not_owned_by_directory(tmp_path):
    """**HEAD 版真实漏洞的回归**:全局 sessions 目录里的会话按**信封租户**归属。

    修复前归属取自"文件落在哪个分区":租户 beta 的信封落在 default 分区(全局
    sessions 目录)时,声明 ``default``(甚至不带头)即可 200 读到 beta 正文 ——
    信封租户被目录整个覆盖。现按落盘事实判定,声明不符即拒。
    """
    sid = "s-l1globb01"
    _write_session(tmp_path, sid, tenant="beta")
    app = _tenant_app(tmp_path)
    assert _resolve(app, sid, "default") is None, "目录不是归属依据"
    assert _resolve(app, sid, None) is None, "不带头 == default ⇒ 拒"
    assert _resolve(app, sid, "beta") == "beta"


def test_request_tenant_denies_when_same_sid_spans_two_tenants(tmp_path):
    """同名 sid 跨租户 ⇒ 归属**歧义** ⇒ 任何声明都拒(不得任选其一)。"""
    sid = "s-l1shared001"
    _write_log(tmp_path / "tenants" / "alpha" / "sessions", sid, "alpha")
    _write_log(tmp_path / "tenants" / "beta" / "sessions", sid, "beta")
    app = _tenant_app(tmp_path)
    for header in ("alpha", "beta", "default", None):
        assert _resolve(app, sid, header) is None, \
            f"歧义归属必须拒(header={header!r})"


def test_request_tenant_denies_on_registry_contradiction(tmp_path):
    """进程内认领与请求声明**矛盾**时拒绝,而不是静默改判到认领租户。

    这是"跨请求污染"的攻击面:租户 beta 的服务一旦 ``open_session`` 过该 sid,
    全局认领表就记下 beta;此后**任何**声明(含 alpha)若按认领表改判,都会把
    beta 的正文交给 alpha 的客户端。
    """
    from pyharness.core.tenant_settings import register_session_tenant
    sid = "s-l1registr01"
    _write_log(tmp_path / "tenants" / "beta" / "sessions", sid, "beta", "BETA-ONLY")
    app = _tenant_app(tmp_path)
    register_session_tenant(sid, "beta")
    assert _resolve(app, sid, "beta") == "beta"
    assert _resolve(app, sid, "alpha") is None, "认领表不得把 alpha 的请求改判给 beta"


def test_request_tenant_denies_when_sid_is_only_in_query_string(tmp_path):
    """SSE 把 sid 放在**查询串** ⇒ 归属判定必须覆盖它,否则整条流式面绕过隔离。

    ``/api/stream?sid=…`` 路径里没有 sid,只认路径的实现会直接按 ``tenant`` 参数
    放行 ⇒ "声明即授权"(声明任意租户即可订阅该租户会话的事件流)。
    """
    sid = "s-l1query0001"
    _write_log(tmp_path / "tenants" / "beta" / "sessions", sid, "beta")
    app = _tenant_app(tmp_path)
    assert _resolve(app, sid, "alpha", query=f"sid={sid}&tenant=alpha".encode(),
                    path="/api/stream") is None
    assert _resolve(app, sid, "beta", query=f"sid={sid}&tenant=beta".encode(),
                    path="/api/stream") == "beta"


def test_request_tenant_falls_back_only_when_session_absent(tmp_path):
    """负空间:会话**不存在**时仍可回落客户端头(该路由只会 404,无数据可泄)。"""
    app = _tenant_app(tmp_path)
    assert _resolve(app, "s-nosuch0001", "beta") == "beta"
    # 不含会话 id 的路由:头即声明(既有语义不变)
    assert app.resolve_request_tenant(_req("/api/settings/models", "beta")) == "beta"


def test_forged_header_cannot_read_another_tenants_session_over_http(tmp_path):
    """**端到端**:伪造头读他租户会话 ⇒ 403 且正文零泄漏(**不再 200 改判放行**)。

    单元级 ``resolve_request_tenant`` 只能证明解析结果;此处走真实 ASGI 栈(中间件
    → 租户服务 → 会话读取),证明**没有任何一层**再按客户端头把请求交给别的租户。
    """
    from fastapi.testclient import TestClient

    sid = "s-l1e2ebeta01"
    _write_log(tmp_path / "tenants" / "beta" / "sessions", sid, "beta", "BETA-ONLY")
    app = _tenant_app(tmp_path)
    client = TestClient(app.api, base_url="http://localhost")

    def get(header):
        h = {"X-PyHarness-Token": app._api_token}
        if header is not None:
            h["X-PyHarness-Tenant"] = header
        return client.get(f"/api/sessions/{sid}/messages", headers=h)

    for header in ("alpha", "default", None):
        r = get(header)
        assert r.status_code == 403, f"header={header!r} 必须 403,实测 {r.status_code}"
        assert "BETA-ONLY" not in r.text, f"header={header!r} 泄漏了正文"
    ok = get("beta")
    assert ok.status_code == 200 and "BETA-ONLY" in ok.text, "归属租户自身仍可读"

