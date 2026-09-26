"""Application service layer shared by desktop shells and future native UI."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from pyharness.application.models import SHELL_CAPABILITIES
from pyharness.core.approval import ApprovalProvider
from pyharness.core.channel import normalize_channel
from pyharness.core.skill import SkillManager
from pyharness.core.task_queue import TaskQueue, queue_kwargs_of
from pyharness.core.tenant_settings import (
    TenantSettingsStore,
    normalize_tenant_id,
    register_session_tenant,
    register_tenant_store,
    tenants_dir,
    unregister_session_tenant,
)
from pyharness.errors import raise_code
from pyharness.events import call_id_of
from pyharness.events.vocab import validate_payload

log = logging.getLogger("pyharness.application.service")


def _with_message_origin(log_: Any, msgs: list[dict]) -> list[dict]:
    """给派生消息附**来源标记**(只供 UI 判断消息来自谁,不参与 LLM 请求)。

    定时任务到点触发时,Scheduler 会以 ``actor="user"`` 写一条 ``user.message``,
    其 ``origin="schedule:<name>"``(schedule._fire)。``derive_messages`` 只投影
    role/content,且**同时喂给 agent_loop 的 LLM 请求与 compaction 的 token 估算**,
    故**不改其输出契约**;改在本层按 1:1 对应关系**复制**并附加键:

      - ``session._fold_history`` 对每条 ``user.message`` 恰产出一条 ``role=user``
        消息(``user.message_edited`` 是替换内容而非增删),故第 k 条 user 消息 ↔
        第 k 条 user.message 事件;
      - 复制字典,避免污染 ``derive_messages`` 的 history 缓存对象。

    普通用户输入同样写 ``origin=None``(键恒在,便于前端统一判断)。
    """
    origins = [getattr(e, "origin", None) for e in log_.events_after(0)
               if getattr(e, "type", "") == "user.message"]
    out: list[dict] = []
    k = 0
    for m in msgs:
        row = dict(m)
        if row.get("role") == "user":
            row["origin"] = origins[k] if k < len(origins) else None
            k += 1
        out.append(row)
    if k != len(origins):                  # 不变式破损:宁可少标记也不错标
        log.warning("user.message 与派生 user 消息数不一致(events=%d msgs=%d)",
                    len(origins), k)
        for row in out:
            row.pop("origin", None)
    return out



def _session_module():
    """Lazy import avoids desktop package <-> app shell import cycles."""
    from pyharness.desktop import sessions
    return sessions


def _as_dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(vars(value))


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


_METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal",
                             "100.100.100.200"})


def _validate_registry_url(url: str) -> str:
    """Skill Registry URL 校验(SSRF 收紧):仅 http(s);拒云元数据地址。

    客户端可传 registry_url,服务端据此发起 HTTP——无校验则可指向内网/元数据做
    SSRF,或指向 file:// 类本地读取面(P3:registry_url SSRF)。loopback/私网不强拒
    (本地自建 registry 是合法用法),仅挡非 http(s) scheme 与云元数据端点。
    """
    from urllib.parse import urlsplit
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in ("http", "https"):
        raise_code("CFG-601", field="skills.registry_url", value=str(url)[:200],
                   advice="registry_url 须为 http(s) 地址(拒 file:// 等本地读取面)")
    host = (parts.hostname or "").lower()
    if host in _METADATA_HOSTS:
        raise_code("CFG-601", field="skills.registry_url", value=host,
                   advice="registry_url 指向云元数据地址,拒绝(SSRF)")
    return str(url).strip()


from pyharness.core.lifecycle import admitted, stop_admissions


class ApplicationService:
    """Session-scoped business facade independent from HTTP and UI frameworks."""

    def __init__(self, ctx: Any, *, channel: str = "desktop",
                 tenant_id: str = "default") -> None:
        self.ctx = ctx
        # N5:通道经**同一契约**归一化并 fail-closed。修复前 ``str(channel or
        # "desktop")`` 会把显式 ``None``(headless 意图)与空串**静默提升为
        # desktop**(人类通道)—— falsy 回退改写声明语义。现:非法/未知 ⇒ APR-503;
        # ``"acp:<id>"`` ⇒ 归一为 ``"acp"``(保留 ACP 身份)。
        st = normalize_channel(channel)
        if not st.is_interactive:
            raise_code("APR-503", module="application.service", field="channel",
                       got=repr(channel),
                       why="ApplicationService 需要明确的交互通道名"
                           "(HEADLESS/非法/未知均不适用于桌面服务门面)")
        self.channel = str(st.name)
        self.tenant_id = normalize_tenant_id(tenant_id)
        self._surface: Any = None
        self._logs: dict[str, Any] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._queue_locks: dict[str, asyncio.Lock] = {}
        self._queues: dict[str, TaskQueue] = {}
        self._approvals: dict[str, ApprovalProvider] = {}
        self._engines: dict[str, Any] = {}
        self._settings_store = TenantSettingsStore(
            tenants_dir(self._storage_root()))     # R31-2:派生点收口到 tenants_dir
        register_tenant_store(self.tenant_id, self._settings_store)
        from pyharness.core.llm import LLMRuntime
        self.llm_runtime = LLMRuntime()

    def _storage_root(self) -> Path:
        cfg = _session_module()._cfg_of(self.ctx)
        root = getattr(getattr(cfg, "storage", None), "root", None)
        return Path(str(root or "~/.pyharness")).expanduser()

    @property
    def platform(self):
        if not hasattr(self, '_platform'):
            from pyharness.application.platform_service import PlatformService
            self._platform = PlatformService(self)
        return self._platform

    def tenant_root(self) -> Path:
        return self._storage_root() / "tenants" / self.tenant_id

    def _tenant_sessions_dir(self, ctx: Any = None) -> Path:
        """本租户的**会话目录**—— 唯一派生点(R24 收口)。

        `default` 租户沿用装配 ctx 的 `storage.sessions_dir`(L1 锚点);其余租户落在
        自己的租户根下,与 `effective_settings()` 里 `storage.db_path` 的租户分域
        **配套**(索引库与会话目录同域,见 R14-8)。此前该分支在**三处**各写一遍
        (三份相同且正确,但"同源规则多份实现"必然漂移 —— 见 R14-3 方法论)。
        """
        c = ctx if ctx is not None else self.ctx
        if self.tenant_id == "default":
            return _session_module()._sessions_dir_of(c)
        return self.tenant_root() / "sessions"

    def effective_settings(self) -> Any:
        """Settings overridden by this tenant's active model profile **and storage scope**.

        ``2026-09-21 R14-8``:非 ``default`` 租户的**会话目录**本就按租户分
        （调用点一律用 ``tenant_root()/sessions``），但其**派生索引库**
        （``storage.db_path``）仍是**全局**的 ⇒ 两个租户共用一个 FTS 库：
        ① ``query`` 没有任何租户维度 ⇒ A 的 agent 能检索到 B 的会话内容
           （实测：A 侧不带 ``session_id`` 查询直接命中 B 的条目，`session.fts_query`
           的 ``session_id`` 又取自 LLM 自报参数）；
        ② 同名 sid 跨租户在 ``fts_rows`` 主键 ``(session_id, seq)`` 上**碰撞**
           ⇒ 后写者被 ``INSERT OR IGNORE`` 静默丢弃、其内容根本不入索引（实测）。
        **派生视图必须与真源同域**：索引库随会话目录一起按租户分。
        """
        cfg = _session_module()._cfg_of(self.ctx)
        profile = self._settings_store.active_profile(self.tenant_id)
        scoped = self.tenant_id != "default"
        if (profile is None and not scoped) or not hasattr(cfg, "model_dump"):
            return copy.deepcopy(cfg)       # Runtime registration must not mutate caller settings.
        raw = cfg.model_dump()
        if scoped:                          # 索引库随会话目录同域（R14-8）
            storage = dict(raw.get("storage") or {})
            storage["db_path"] = str(self.tenant_root() / "index.db")
            raw["storage"] = storage
        if profile is not None:
            llm = dict(raw.get("llm") or {})
            llm.update({
                "model": profile["model"],
                "base_url": profile["base_url"],
                "api_key": f"tenant:{self.tenant_id}:{profile['id']}",
                "temperature": profile.get("temperature",
                                           llm.get("temperature", 0.7)),
                "max_tokens": profile.get("max_tokens",
                                          llm.get("max_tokens", 4096)),
                "fallback_models": list(profile.get("fallback_models") or []),
            })
            raw["llm"] = llm
        from pyharness.config import Settings
        return Settings.model_validate(raw)

    def tenant_state(self) -> dict:
        return self._settings_store.state(self.tenant_id)

    def save_model_profile(self, body: dict) -> dict:
        self._ensure_open()
        result = self._settings_store.upsert_profile(self.tenant_id, body)
        result["restart_required"] = bool(self._engines)
        return result

    def activate_model_profile(self, profile_id: str) -> dict:
        self._ensure_open()
        result = self._settings_store.activate_profile(self.tenant_id, profile_id)
        result["restart_required"] = bool(self._engines)
        return result

    def delete_model_profile(self, profile_id: str) -> dict:
        self._ensure_open()
        result = self._settings_store.delete_profile(self.tenant_id, profile_id)
        result["restart_required"] = bool(self._engines)
        return result

    def _surface_ctx(self) -> Any:
        if self.tenant_id == "default":
            return self.ctx
        try:
            ctx = copy.copy(self.ctx)
        except Exception:                              # noqa: BLE001
            ctx = SimpleNamespace(**dict(vars(self.ctx)))
        try:
            ctx.session = None
        except Exception:                              # noqa: BLE001
            pass
        return ctx

    @staticmethod
    def capabilities() -> dict:
        """Return the shared UI capability contract.

        Both shells expose this through their own adapter so feature drift is
        visible at runtime, not only in tests.
        """
        rows = list(SHELL_CAPABILITIES)
        return {"capabilities": rows, "count": len(rows)}

    def surface_mgr(self) -> Any:
        if self._surface is None:
            ctx = self._surface_ctx()
            sm = _session_module()
            sessions_dir = self._tenant_sessions_dir(ctx)
            self._surface = sm._surface_of(
                ctx, bus=getattr(ctx, "bus", None),
                config=self.effective_settings(),
                sessions_dir=sessions_dir,
                tenant_id=self.tenant_id)      # GAP-10:租户随会话落盘
            if isinstance(self._surface, sm.DesktopSessionManager):
                self._surface._ctx_repair = getattr(ctx, "repair", None)
        return self._surface

    @admitted
    async def require_session(self, sid: str) -> Any:
        sm = _session_module()
        sid = sm.validate_session_id(sid)
        lock = self._session_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            if sid in self._logs:
                register_session_tenant(sid, self.tenant_id)
                return self._logs[sid]
            log_ = await sm._await(self.surface_mgr().open_session(sid))
            self._logs[sid] = log_
            register_session_tenant(sid, self.tenant_id)
            return log_

    @admitted
    async def queue_for(self, sid: str, log_: Any = None) -> TaskQueue:
        self._ensure_open()
        if log_ is None:
            log_ = await self.require_session(sid)
        lock = self._queue_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            q = self._queues.get(sid)
            if q is None:
                runner = await self._engine_runner_for(sid, log_)
                q = TaskQueue(session=log_, runner=runner,
                              **queue_kwargs_of(getattr(self.ctx, "settings", None)))
                self._queues[sid] = q
                spine = self._engines.get(sid)
                if spine is not None:
                    from pyharness import engine as _eng
                    await _eng.activate_orchestration(spine, task_queue=q)
            return q

    async def _engine_runner_for(self, sid: str, log_: Any) -> Any:
        spine = self._engines.get(sid)
        from pyharness import engine as _eng
        from pyharness.engine import make_runner as _eng_make_runner
        cfg = self.effective_settings()
        from pyharness.application.platform_service import PlatformService
        platform_binding = PlatformService.binding(log_)
        platform_definition = None
        if platform_binding:
            from pyharness.application.platform_models import AgentDefinition
            platform_definition = AgentDefinition.model_validate(platform_binding['definition'])
            cfg.loop.max_turns = platform_definition.max_rounds
            cfg.budget.task.max_in_tokens = platform_definition.budget.input_tokens
            cfg.budget.task.max_out_tokens = platform_definition.budget.output_tokens
            cfg.budget.task.max_cost_yuan = platform_definition.budget.cost
            cfg.security.policy.preset = platform_definition.permission_policy
            cfg.plugins.enabled = []
            cfg.plugins.mcp_servers = []
            if platform_definition.mcp_connections:
                from pyharness.config import McpServerCfg
                configured=self.platform.connection_definitions()
                for name in platform_definition.mcp_connections:
                    connection=configured.get('mcp:'+name)
                    if connection is None or not connection['config']['enabled']:
                        raise_code('CFG-601',reason='agent_mcp_connection_disabled')
                    cfg.plugins.mcp_servers.append(McpServerCfg.model_validate(connection['config']))
            if platform_definition.model_connection:
                profiles = self.tenant_state()['profiles']
                profile = next((p for p in profiles if p['id'] == platform_definition.model_connection), None)
                if profile is None:
                    raise_code('CRED-701', reason='agent_model_connection_missing')
                cfg.llm.model, cfg.llm.base_url = profile['model'], profile['base_url']
                cfg.llm.api_key = f'tenant:{self.tenant_id}:{profile["id"]}'
                cfg.llm.fallback_models = []
        if self.tenant_id != 'default' and self._settings_store.active_profile(self.tenant_id) is None:
            # Explicit borrowed test/local adapters do not resolve host credentials.
            models = [cfg.llm.model, *(cfg.llm.fallback_models or [])]
            if not all(self.llm_runtime.has_borrowed_adapter(model) for model in models):
                raise_code('CRED-701', reason='tenant_model_profile_required')
        if spine is None:
            store = getattr(self.surface_mgr(), "_stores", {}).get(sid)
            sessions_dir = self._tenant_sessions_dir()
            spine = _eng.build_runner_components(
                cfg, log_=log_, bus=getattr(self.ctx, "bus", None),
                sessions_dir=sessions_dir, store=store,
                attach_persistence=False,
                # GAP-11:桌面服务层即 human 通道 "desktop"(构造期声明)——
                # 必须下沉到 spine,否则该会话的每次工具授权都 APR-503。
                channel=self.channel, llm_runtime=self.llm_runtime)
            try:
                if platform_definition is not None:
                    from pyharness.application.platform_runtime import PlatformRuntime
                    PlatformRuntime(self.platform, platform_definition, sid).configure_spine(spine)
            except BaseException:
                await spine.close()
                raise
            self._engines[sid] = spine
        if not getattr(spine, "_plugins_ready", False):
            await _eng._preload_plugins(spine)
            spine._plugins_ready = True
        if self.tenant_id == "default":
            if getattr(self.ctx, "approval", None) is None:
                self.ctx.approval = spine.approval
                self.ctx._approval_owner_sid = sid   # 归属记账:approval_for 判复用
            if getattr(self.ctx, "guard", None) is None:
                self.ctx.guard = spine.guard
                self.ctx._guard_owner_sid = sid
        return _eng_make_runner(spine)

    def runner_seam(self) -> Any:
        ctx = self.ctx
        make = getattr(ctx, "make_runner", None)
        if callable(make):
            return make()
        return getattr(ctx, "task_runner", None)

    def owner_for(self, sid: str) -> str:
        if self.tenant_id == "default":
            return f"{self.channel}:{sid}"
        return f"{self.channel}:{self.tenant_id}:{sid}"

    @admitted
    async def public_spine_for(self, sid: str) -> tuple[Any, Any]:
        self._ensure_open()
        log_ = await self.require_session(sid)
        spine = self._engines.get(sid)
        if spine is None:
            # One authorized construction path, before creating or caching any
            # model adapter. A rejected request must not leave a host-bound spine.
            await self.queue_for(sid, log_)
            spine = self._engines[sid]
        return log_, spine

    def approval_for(self, sid: str, log_: Any) -> ApprovalProvider:
        provider = self._approvals.get(sid)
        if provider is None:
            ctx_approval = (getattr(self.ctx, "approval", None)
                            if self.tenant_id == "default" else None)
            owner = getattr(self.ctx, "_approval_owner_sid", None)
            # 仅当共享 ctx.approval 确属本会话(owner==sid)或未记归属(外部单会话
            # 注入)才复用;否则建 per-session——防第二会话拿到第一会话的审批通道。
            if ctx_approval is not None and owner in (None, sid):
                return ctx_approval
            provider = ApprovalProvider(
                session=log_, bus=None,
                config=_session_module()._cfg_of(self.ctx),
                # R12-3:队列联动接线(惰性取值 ⇒ 与构造序无关)——挂起审批时真挂起
                # 本会话队列(队列是 queue.suspended/resumed 的唯一写者)。
                queue_getter=lambda: self._queues.get(sid))
            self._approvals[sid] = provider
        return provider

    def all_approval_providers(self) -> list:
        seen, out = set(), []
        ctx_approval = (getattr(self.ctx, "approval", None)
                        if self.tenant_id == "default" else None)
        engine_providers = [getattr(s, 'approval', None) for s in self._engines.values()]
        for p in list(self._approvals.values()) + engine_providers + ([ctx_approval] if ctx_approval else []):
            if p is not None and id(p) not in seen:
                seen.add(id(p))
                out.append(p)
        return out

    @admitted
    async def create_session(self) -> str:
        self._ensure_open()
        mgr = self.surface_mgr()
        fn = getattr(mgr, "create", None)
        if not callable(fn):
            raise_code("CYC-999", hint="会话门面未实现 create(缺 DesktopSessionManager)")
        sid = fn()
        if hasattr(sid, "__await__"):
            sid = await sid
        self._logs[sid] = await self.require_session(sid)
        register_session_tenant(sid, self.tenant_id)
        return str(sid)

    @admitted
    async def delete_session(self, sid: str) -> dict:
        """Delete an idle session and all of its tenant-scoped files."""
        sid = _session_module().validate_session_id(sid)
        queue = self._queues.get(sid)
        if queue is not None:
            status = queue.status()
            if status.running or status.waiting or status.paused:
                # BUSY 是 ERR.md 已登记的**命名状态字面量**、`errors.py` **故意不登记**
                # (同 QUE-001/ATT-001 先例:acp.py / core/agent.py / task_queue.py 三处
                # 均**直接构造**保字面码)。此处曾用 ``raise_code("BUSY")`` ⇒ 被静默改写为
                # CYC-999(R19):同一"会话忙"条件在 ACP 报 BUSY、在本路径报"未知内部错误",
                # 且 HTTP 落 500 而非 409。
                from pyharness.errors import PyHError
                raise PyHError("BUSY", ctx={
                    "session_id": sid,
                    "advice": "会话仍有运行/等待任务,完成或取消后再删除"})
        spine = self._engines.get(sid)
        if spine is not None:
            # FTS 派生索引级联清理(2026-09-21 R8):`SessionQueryIndex.delete_session`
            # 此前**无生产调用者** ⇒ 删除会话后其索引行仍在 ⇒ **已删内容仍可被搜索**
            # (且搜索结果指向不存在的会话)。必须在 `spine.close()`(会 detach 关库)
            # **之前**清;索引未激活(无 fts)则无索引行,跳过即可。
            fts = getattr(spine, "fts", None)
            if fts is not None and callable(getattr(fts, "delete_session", None)):
                try:
                    await fts.delete_session(sid)
                except Exception:                      # noqa: BLE001 清理失败不阻断删除
                    log.warning("fts delete_session 失败 sid=%s(索引残留,"
                                "可由 rebuild 自愈)", sid, exc_info=True)
            await spine.close()
            self._engines.pop(sid, None)
        self._approvals.pop(sid, None)
        self._queues.pop(sid, None)
        self._queue_locks.pop(sid, None)
        self._logs.pop(sid, None)
        result = await self.surface_mgr().delete_session(sid)
        self._session_locks.pop(sid, None)
        unregister_session_tenant(sid, self.tenant_id)   # 只撤本租户认领
        return {"ok": True, **result}

    def _attachment_limits(self) -> tuple[int, int, tuple]:
        """附件上限(F061)取自 ``security.attachment.*``;缺省回落模块 L1 锚点。

        2026-09-21:**这三个配置键此前零读取者**(死配置,见 LIMITATIONS L-20),
        接线后运维改它们即生效。
        """
        from pyharness.core import attachment as att_mod
        att = getattr(getattr(getattr(self.ctx, "settings", None),
                              "security", None), "attachment", None)
        n = getattr(att, "max_per_message", None)
        b = getattr(att, "max_bytes", None)
        mimes = getattr(att, "mime_whitelist", None)
        return (int(n) if n else att_mod.DEFAULT_MAX_PER_MESSAGE,
                int(b) if b else att_mod.DEFAULT_MAX_BYTES,
                tuple(mimes) if mimes else att_mod.DEFAULT_MIME_WHITELIST)

    def _session_workspace(self, sid: str) -> str:
        """会话工作区(F061 附件落点):引擎 scope 权威值,缺位则**同单点**派生。"""
        spine = self._engines.get(str(sid))
        ws = getattr(getattr(getattr(spine, "scope", None), "policy", None),
                     "workspace_root", "")
        if ws:
            return str(ws)
        from pyharness.core.scope import session_workspace
        cfg = getattr(self.ctx, "settings", None)
        root = getattr(getattr(cfg, "storage", None), "workspaces_dir",
                       "~/.pyharness/workspaces")
        return session_workspace(str(root), str(sid))

    def attachment_payloads(self, attachments: Any, *, sid: str = "") -> list[dict]:
        """附件入站**单点校验**(F061):数量 → 魔数/大小 → 内容寻址落盘。

        2026-09-21 修(L-22):此前只做 schema 形状组装(客户端自报 mime、缺省回落
        `image/png`;无魔数/大小/数量校验;`sha256` 可省略回落全零占位)⇒ **伪装图片的
        恶意文件可进事件流**,且同内容不去重。现按 PRD-Core F061 规格:魔数定类型
        (不信扩展名/自报 mime)→ 单张 ≤`max_bytes` → 单消息 ≤`max_per_message`
        → `sha256(raw)[:16]` 内容寻址去重落盘到**会话工作区内**;任一不满足 →
        `ATT-001` 零写盘。schema 校验保留为第二道(形状)。
        """
        from pyharness.core import attachment as att_mod
        refs = list(attachments or [])
        if not refs:
            return []
        n_max, b_max, mimes = self._attachment_limits()
        if len(refs) > n_max:
            att_mod.reject_attachment("too-many", count=len(refs), limit=n_max)
        workspace = self._session_workspace(sid)
        payloads: list[dict] = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise_code("EVT-100", field="attachments", advice="附件须 dict 引用")
            payload = att_mod.ingest_image_path(
                ref.get("file_path") or ref.get("ref") or "", workspace=workspace,
                mime_whitelist=mimes, max_bytes=b_max)
            payloads.append(validate_payload("user.attachment.image", payload))
        return payloads

    @admitted
    async def create_message(self, sid: str, text: str, *,
                             attachments: Any = None) -> dict:
        self._ensure_open()
        text = str(text or "").strip()
        if not text:
            raise_code("EVT-100", advice="消息不能为空")
        log_ = await self.require_session(sid)
        payloads = self.attachment_payloads(attachments, sid=sid)
        queue = await self.queue_for(sid, log_)
        env = await log_.append("user.message", {"content": text},
                                actor="user", origin=self.channel, sync=True)
        for payload in payloads:
            await log_.append("user.attachment.image", payload, actor="user")
        task_id = await queue.submit(text, meta={"channel": self.channel,
                                                 "session_id": sid})
        return {"task_id": task_id, "user_seq": env.seq}

    @admitted
    async def list_jobs(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        owner = self.owner_for(sid)
        rows = [self._job_dict(await spine.jobs.status(job_id, by=owner))
                for job_id in spine.jobs.list_owned(owner)]
        return {"jobs": rows, "count": len(rows)}

    @staticmethod
    def _job_dict(status: Any) -> dict:
        return _json_safe(_as_dict(status))

    @admitted
    async def start_job(self, sid: str, intent: str) -> str:
        self._ensure_open()
        intent = str(intent or "").strip()
        if not intent:
            raise_code("EVT-100", field="intent", advice="job intent 不能为空")
        log_ = await self.require_session(sid)
        await self.queue_for(sid, log_)
        spine = self._engines[sid]
        owner = self.owner_for(sid)
        ctx = SimpleNamespace(owner=owner, owner_channel=self.channel,
                              scope=getattr(spine, "scope", None),
                              tools=getattr(spine, "tools", None), budget=None)
        return await spine.jobs.start(intent, ctx)

    @admitted
    async def job_status(self, sid: str, job_id: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        return {"job": self._job_dict(
            await spine.jobs.status(job_id, by=self.owner_for(sid)))}

    @admitted
    async def cancel_job(self, sid: str, job_id: str) -> bool:
        _, spine = await self.public_spine_for(sid)
        return bool(await spine.jobs.cancel(job_id, by=self.owner_for(sid)))

    @admitted
    async def list_schedules(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        rows = [_json_safe(_as_dict(j)) for j in await spine.schedule.list_jobs()]
        return {"schedules": rows, "count": len(rows)}

    @admitted
    async def schedule_action(self, sid: str, action: str, *,
                              name: str = "", kind: str = "cron",
                              expr: str = "", intent: str = "",
                              is_risky: Optional[bool] = None) -> dict:
        self._ensure_open()
        action = str(action or "").strip().lower()
        name = str(name or "").strip()
        log_ = await self.require_session(sid)
        _, spine = await self.public_spine_for(sid)
        ctx = SimpleNamespace(session=getattr(spine, "session", None),
                              task_queue=getattr(spine, "task_queue", None))
        if action == "add":
            expr = str(expr or "").strip()
            intent = str(intent or "").strip()
            if not name or not expr or not intent:
                raise_code("EVT-100", advice="schedule add 需要 name/expr/intent")
            await self.queue_for(sid, log_)
            await spine.schedule.register(
                name, str(kind or "cron").strip().lower(), expr,
                {"intent": intent}, is_risky=is_risky, ctx=ctx)
        elif action == 'edit':
            await spine.schedule.edit(name,kind,expr,intent,ctx=ctx)
        elif action == 'run_now':
            await spine.schedule.run_now(name,ctx=ctx)
        elif action in {"pause", "resume", "remove"}:
            if not name:
                raise_code("EVT-100", field="name", advice=f"schedule {action} 需要 name")
            await getattr(spine.schedule, action)(name, ctx=ctx)
        else:
            raise_code("EVT-100", field="action",
                       advice="action 须为 add/pause/resume/remove")
        return {"ok": True, "action": action, "name": name}

    @admitted
    async def list_subagents(self, sid: str) -> dict:
        _, spine = await self.public_spine_for(sid)
        return {"status": _json_safe(_as_dict(spine.subagent.status()))}

    @admitted
    async def spawn_subagent(self, sid: str, task: str, *,
                             tools_subset: Any = None,
                             deny_extra: Any = None,
                             budget_ratio: float = 0.25,
                             notify: bool = True) -> str:
        self._ensure_open()
        from pyharness.core.subagent import SubagentSpec
        task = str(task or "").strip()
        if not task:
            raise_code("EVT-100", field="task", advice="subagent task 不能为空")
        log_ = await self.require_session(sid)
        await self.queue_for(sid, log_)
        spine = self._engines[sid]
        spec = SubagentSpec(task=task, tools_subset=tools_subset,
                            deny_extra=list(deny_extra or []),
                            budget_ratio=float(budget_ratio), depth=1,
                            notify=bool(notify))
        ctx = SimpleNamespace(round_seq=int(log_.stats().get("seq", 1)),
                              owner=self.owner_for(sid),
                              scope=getattr(spine, "scope", None),
                              tools=getattr(spine, "tools", None),
                              bus=getattr(spine, "bus", None))
        return await spine.subagent.spawn(spec, ctx)

    @admitted
    async def cancel_subagent(self, sid: str, sub_id: str) -> bool:
        _, spine = await self.public_spine_for(sid)
        return bool(await spine.subagent.cancel(sub_id, by=self.owner_for(sid)))

    def _ensure_open(self):
        if getattr(self, '_closing', False) and asyncio.current_task() not in getattr(self, '_admissions', {}):
            raise_code('EVT-104', reason='service_closing', advice='服务正在关闭;拒绝新工作')

    async def shutdown(self) -> None:
        task = getattr(self, '_shutdown_task', None)
        if task is None:
            self._closing = True
            task = self._shutdown_task = asyncio.create_task(self._shutdown_owned())
        await asyncio.shield(task)

    async def _shutdown_owned(self):
        await stop_admissions(self)
        errors = []
        async def attempt(fn, *args, **kw):
            try:
                await fn(*args, **kw)
            except BaseException as exc:
                errors.append(exc)
        if hasattr(self, '_platform'):
            await attempt(self._platform.stop_actions)
        spines = list(self._engines.values())
        if self.tenant_id == 'default':
            spines.append(getattr(self.ctx, 'engine_spine', None))
        seen = set()
        for spine in spines:
            if spine is not None and id(spine) not in seen:
                seen.add(id(spine))
                await attempt(spine.close)
        for queue in self._queues.values():
            if callable(getattr(queue, 'shutdown', None)):
                await attempt(queue.shutdown, reason='service-shutdown')
        if hasattr(self, '_platform'):
            await attempt(self._platform.close)
        if isinstance(self._surface, _session_module().DesktopSessionManager):
            await attempt(self._surface.shutdown_all)
        await attempt(self.llm_runtime.aclose)
        if errors:
            raise BaseExceptionGroup('application cleanup failed', errors)

    # ------------------------------------------------------------ read models
    @admitted
    async def list_sessions(self) -> dict:
        lst = self.surface_mgr().list()
        if callable(lst) or hasattr(lst, "__await__"):
            lst = await _session_module()._await(lst)

        def _title_of(sid: str) -> str:
            log_ = self._logs.get(str(sid))
            if log_ is None:
                return ""
            title = ""
            for ev in log_.events_after(0):
                if ev.type == "session.created":
                    title = ev.payload.get("title") or title
                elif ev.type == "session.renamed":
                    title = ev.payload.get("new_title") or title
            return str(title)

        sessions: list[dict] = []
        for item in (lst or []):
            row = item if isinstance(item, dict) else {"sid": str(item)}
            sid = str(row.get("sid") or item)
            register_session_tenant(sid, self.tenant_id)
            if not row.get("title"):
                row["title"] = _title_of(sid)
            sessions.append(row)
        return {"sessions": sessions, "count": len(sessions)}

    @admitted
    async def session_messages(self, sid: str, after_seq: int = 0) -> dict:
        log_ = await self.require_session(sid)
        msgs = log_.derive_messages()
        stats_fn = getattr(log_, "stats", None)
        to_seq = int((stats_fn().get("seq") or 0)) if callable(stats_fn) else 0
        return {"sid": sid, "after_seq": int(after_seq), "to_seq": to_seq,
                "messages": _with_message_origin(log_, msgs)}

    @admitted
    async def session_timeline(self, sid: str, after_seq: int = 0,
                               kinds: str = "") -> dict:
        log_ = await self.require_session(sid)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int):
            raise_code("EVT-100", field="after_seq", value=after_seq,
                       advice="after_seq 须 int(seq 游标)")
        from pyharness.desktop.projection import derive_timeline
        nodes = derive_timeline(
            log_.events_after(after_seq=max(0, int(after_seq))),
            kinds=str(kinds or ""))
        return {"sid": sid, "base_seq": max(0, int(after_seq)),
                "nodes": [asdict(n) for n in nodes]}

    @admitted
    async def event_detail(self, sid: str, seq: int) -> dict:
        log_ = await self.require_session(sid)
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise_code("EVT-100", field="seq", value=seq, advice="seq 须 int")
        env = log_.get(int(seq))
        if env is None:
            raise_code("EVT-100", sid=sid, seq=seq,
                       advice="seq 不存在(空洞/坏行隔离/越界)")
        from pyharness.desktop.projection import _env_dict
        return {"event": _env_dict(env)}

    @admitted
    async def telemetry_report(self, sid: str) -> dict:
        """旧遥测面:按事件类型计数聚合(与治理因果无关,保留兼容)。"""
        log_ = await self.require_session(sid)
        from pyharness.core import telemetry as _tel
        try:
            return {"sid": str(sid), "ok": True,
                    "audit": _tel.session_audit(log_)}
        except Exception as e:                         # noqa: BLE001
            return {"sid": str(sid), "ok": False,
                    "error": f"{type(e).__name__}:{e}"[:200]}

    # ------------------------------------------------------------ 治理审计(GAP-7)
    @admitted
    async def governance_audit(self, sid: str, *, decision_id: str = "",
                               since_seq: int = 0, reconcile: bool = False,
                               limit: int = 100) -> dict:
        """治理审计查询面:把 ``governance.audit.AuditSystem`` 接成**产品入口**。

        GAP-7 修复点:此前 ``AuditSystem`` 已构造并注入 ``GovernanceContext``,但
        ``causal_chain`` / ``denied_report`` / ``reconcile`` / ``legacy_session_audit``
        在 ``pyharness/`` 内**零调用点** ⇒ 治理数据在盘上、查询面不可达,桌面
        "审计"页只能用旧遥测(类型计数,无决策因果)顶上。

        读侧纪律(**replay-only**):每条结论都由**重放会话日志**现算得出,不读
        任何内存缓存、不订阅事件 ⇒ 重启后同一日志得同一结果(可复现)。

        返回:``decisions``(最近 N 条 decision.issued,倒序)·
        ``denied``(被拒清单,含 ``executed`` 证据位:该 call_id 是否有配对
        tool.result)· ``findings``(``reconcile=True`` 时的三类一致性对账)·
        ``chain``(给定 ``decision_id`` 的因果链还原)。
        """
        log_, spine = await self.public_spine_for(sid)
        audit = getattr(getattr(spine, "governance", None), "audit", None)
        if audit is None:
            raise_code("CYC-999", module="application.service",
                       field="governance.audit",
                       why="治理审计面未接线(AuditSystem 未注入 GovernanceContext)")
        from pyharness.governance import verify_receipt
        events = list(log_.events_after(0))
        decisions = []
        tenant = getattr(log_, "tenant_id", None)
        for e in events:
            if e.type != "decision.issued":
                continue
            p = getattr(e, "payload", None) or {}
            decisions.append({
                "decision_id": str(p.get("decision_id") or ""),
                "verdict": str(p.get("verdict") or ""),
                "tool": str(p.get("tool") or ""),
                "guard_ids": list(p.get("guard_ids") or []),
                "policy_refs": list(p.get("policy_refs") or []),
                "principal_kind": p.get("principal_kind"),
                "principal_id": p.get("principal_id"),
                "principal_channel": p.get("principal_channel"),
                "approval_ref": p.get("approval_ref"),
                "supersedes": p.get("supersedes"),
                "seq": getattr(e, "seq", None),
                "call_id": call_id_of(e),
                # 租户归属(GAP-10):取自**事件信封**(框架侧落盘的事实),
                # 不是从服务实例的 self.tenant_id 反推 —— 后者是"当前请求"的租户,
                # 前者是"该事件属于谁"的事实。
                "tenant_id": getattr(e, "tenant_id", None),
            })
        n = max(1, int(limit or 100))
        # 凭证核验面(离线可验):README 曾自陈 ``verify_receipt`` "未接 CLI/HTTP
        # /工具外壳"—— 库级可达而产品不可达。此处接上:**从事件日志重建**全部凭证
        # (``rebuild_from_log``,INV-G2/R2:不依赖任何内存缓存,重启后同样成立),
        # 逐条重算哈希(篡改任一受保护字段即 False),再校验 prev_hash 链单调。
        from pyharness.governance import verify_chain
        from pyharness.governance.receipt import rebuild_from_log
        all_rec = rebuild_from_log(log_)
        receipts: dict = {
            "count": len(all_rec),
            "verified": sum(1 for r in all_rec if verify_receipt(r)),
            "chain_valid": verify_chain(all_rec) if all_rec else True,
            "items": [{"receipt_id": r.receipt_id,
                       "decision_id": r.decision_id, "kind": r.kind,
                       "verified": verify_receipt(r),
                       "approval_ref": r.approval_ref,
                       "content_hash": r.content_hash,
                       "prev_hash": r.prev_hash} for r in all_rec],
        }
        return {
            "sid": str(sid), "ok": True,
            "tenant_id": tenant,
            "decisions": decisions[-n:][::-1],
            "denied": audit.denied_report(since_seq=int(since_seq or 0)),
            "findings": (await audit.reconcile()) if reconcile else [],
            "chain": audit.causal_chain(str(decision_id)) if decision_id else None,
            "receipts": receipts,
        }

    # ------------------------------------------------------------ 治理证据(GAP-8)
    @admitted
    async def governance_evidence(self, sid: str, *, task_id: str = "") -> dict:
        """证据查询面:按任务段聚合的治理证据工件。

        GAP-8 修复点:此前 ``EvidenceCollector`` 只有订阅者、没有生产者
        (``archive()`` 零调用) ⇒ ``evidence.archived`` 永不产生、索引恒空、
        ``collect_for_task()`` 恒返回 ``()``。现由 ``engine.archive_task_evidence``
        在任务段结束后按冻结规则产出。

        **INV-E3 可核验**:回答**不取自订阅态内存索引**,而是由 ``from_log`` 从
        append-only 日志**现场重建**——故重启后同一日志得同一答案。``indexed`` 与
        ``rebuilt`` 两数并列展示,二者不一致即说明派生缓存与真源脱节(可观测)。
        """
        log_, spine = await self.public_spine_for(sid)
        coll = getattr(getattr(spine, "governance", None), "evidence", None)
        if coll is None:
            raise_code("CYC-999", module="application.service",
                       field="governance.evidence",
                       why="证据面未接线(EvidenceCollector 未注入 GovernanceContext)")
        from pyharness.governance import EvidenceCollector
        rebuilt = await EvidenceCollector.from_log(log_)     # INV-E3:由日志重建
        out: dict = {
            "sid": str(sid), "ok": True, "task_id": str(task_id),
            "indexed": coll.evidence_count(),
            "rebuilt": rebuilt.evidence_count(),
            "artifacts": [
                {"evidence_id": str((getattr(e, "payload", None) or {})
                                    .get("evidence_id") or ""),
                 "claim": str((getattr(e, "payload", None) or {}).get("claim") or ""),
                 "refs": list((getattr(e, "payload", None) or {}).get("refs") or []),
                 "seq": getattr(e, "seq", None)}
                for e in log_.events_after(0)
                if getattr(e, "type", None) == "evidence.archived"],
        }
        if task_id:
            out["for_task"] = [
                {"evidence_id": ev.evidence_id, "claim": ev.claim,
                 "refs": [{"kind": r.kind, "locator": r.locator}
                          for r in ev.refs]}
                for ev in rebuilt.collect_for_task(str(task_id))]
        return out

    # ------------------------------------------------------------ edit / feedback
    @staticmethod
    def _last_env_of(log_: Any, type_: str):
        for ev in reversed(list(log_.events_after(0))):
            if ev.type == type_:
                return ev
        return None

    @admitted
    async def resend_text(self, log_: Any, sid: str, text: str) -> dict:
        self._ensure_open()
        text = str(text or "").strip()
        if not text:
            raise_code("EVT-100", advice="重发内容为空")
        env = await log_.append("user.message", {"content": text},
                                actor="user", origin=self.channel, sync=True)
        queue = await self.queue_for(sid, log_)
        task_id = await queue.submit(text, meta={"channel": self.channel,
                                                 "session_id": sid})
        return {"task_id": task_id, "user_seq": env.seq}

    @admitted
    async def edit_message(self, sid: str, seq: int, text: str,
                           *, resend: bool = False) -> dict:
        text = str(text or "").strip()
        log_ = await self.require_session(sid)
        from pyharness.core.message_edit import edit_user_message
        env = await edit_user_message(log_, int(seq), text)
        out: dict = {"ok": True, "edited_seq": env.seq, "target_seq": int(seq)}
        if resend:
            out["resend"] = await self.resend_text(log_, sid, text)
        return out

    @admitted
    async def resend_message(self, sid: str, seq: int) -> dict:
        log_ = await self.require_session(sid)
        env = log_.get(int(seq))
        if env is None or env.type != "user.message":
            raise_code("EVT-101", seq=int(seq), hint="目标不是 user.message,无法重发")
        return await self.resend_text(log_, sid, env.payload.get("content", ""))

    @admitted
    async def feedback(self, sid: str, seq: int, kind: str,
                       note: str = "") -> dict:
        kind = str(kind or "")
        if kind not in ("up", "down", "flag"):
            raise_code("EVT-100", field="kind", kind=kind,
                       advice="kind 取值 up/down/flag")
        log_ = await self.require_session(sid)
        env = log_.get(int(seq))
        if env is None or env.type != "agent.message":
            raise_code("EVT-101", seq=int(seq), hint="反馈目标须是 agent.message")
        await log_.append("user.feedback",
                          {"target_seq": int(seq), "kind": kind,
                           "note": str(note or "")[:500] or None},
                          actor="user", sync=True)
        return {"ok": True, "target_seq": int(seq), "kind": kind}

    @admitted
    async def edit_last_user(self, sid: str, text: str,
                             *, resend: bool = False) -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "user.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有可编辑的用户消息")
        return await self.edit_message(sid, env.seq, text, resend=resend)

    @admitted
    async def resend_last_user(self, sid: str) -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "user.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有可重发的用户消息")
        return await self.resend_message(sid, env.seq)

    @admitted
    async def feedback_last_agent(self, sid: str, kind: str,
                                  note: str = "") -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "agent.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有 agent 回复可反馈")
        return await self.feedback(sid, env.seq, kind, note)

    # ------------------------------------------------------------ approvals / asks
    @admitted
    async def pending_approvals(self) -> dict:
        out: list[dict] = []
        seen: set[tuple] = set()
        for p in self.all_approval_providers():
            pending = getattr(p, "_pending", None)
            if not isinstance(pending, dict):
                continue
            for aid, req in pending.items():
                identity = (getattr(req, 'session_id', ''), aid)
                if identity in seen:
                    continue
                seen.add(identity)
                out.append({"approval_id": aid,
                            "tool": getattr(req, "tool", ""),
                            "args_summary": getattr(req, "args_summary", ""),
                            "risk": getattr(req, "danger", "high"),
                            "ttl_ms": getattr(req, "ttl_ms", None),
                            "session_id": getattr(req, "session_id", "")})
        out.sort(key=lambda x: (str(x.get("session_id") or ""), x["approval_id"]))
        return {"pending": out, "count": len(out)}

    @staticmethod
    def _approval_sid_of(provider: Any, aid: int) -> str:
        pending = getattr(provider, "_pending", None)
        req = pending.get(int(aid)) if isinstance(pending, dict) else None
        return str(getattr(req, "session_id", "") or getattr(
            getattr(req, "log", None), "sid", "") or "")

    def provider_owning(self, aid: int, *, sid: Optional[str] = None) -> Any:
        matches = []
        for p in self.all_approval_providers():
            pending = getattr(p, "_pending", None)
            if isinstance(pending, dict) and int(aid) in pending:
                psid = self._approval_sid_of(p, aid)
                if sid is None or psid == sid:
                    matches.append(p)
            elif callable(getattr(p, "owns_initializing", None)):
                if p.owns_initializing(aid, sid=sid):
                    matches.append(p)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise_code("EVT-101", approval_id=aid,
                       hint="多个会话存在同 approval_id;请使用 /api/approvals/{sid}/{aid}")
        if sid is not None:
            raise_code("APR-503", approval_id=aid, session_id=sid,
                       hint="该会话下不存在此 approval_id")
        ctx_approval = getattr(self.ctx, "approval", None)
        if ctx_approval is not None:
            return ctx_approval
        raise_code("APR-503", approval_id=aid,
                   hint="未知/已裁决的 approval_id;同一审批至多一个结果(防重放)")

    @admitted
    async def decide_approval(self, aid: int, decision: str, *,
                              sid: Optional[str] = None) -> dict:
        if sid is not None:
            sid = _session_module().validate_session_id(sid)
        if isinstance(aid, bool) or not isinstance(aid, int):
            raise_code("EVT-100", field="aid", aid=aid, advice="aid 须 int")
        if decision not in {"approve", "deny"}:
            raise_code("EVT-100", field="decision", decision=decision,
                       advice="decision 取值 approve/deny")
        provider = self.provider_owning(aid, sid=sid)
        fn = (getattr(provider, f"{decision}_async", None)
              or getattr(provider, decision, None))
        if not callable(fn):
            raise_code("CYC-999", hint="审批裁决入口未装配")
        res = fn(int(aid), by=self.channel)
        if inspect.isawaitable(res):
            await res
        return {"ok": True, "approval_id": int(aid)}

    def ask_providers(self) -> list:
        seen, out = set(), []
        for spine in list(self._engines.values()):
            p = getattr(spine, "ask", None)
            if p is not None and id(p) not in seen:
                seen.add(id(p))
                out.append(p)
        spine = getattr(self.ctx, "engine_spine", None)
        p = getattr(spine, "ask", None)
        if p is not None and id(p) not in seen:
            out.append(p)
        return out

    def sid_of_ask(self, provider: Any) -> str:
        for sid, spine in list(self._engines.items()):
            if getattr(spine, "ask", None) is provider:
                return str(sid)
        return ""

    @admitted
    async def pending_asks(self) -> dict:
        out: list[dict] = []
        for p in self.ask_providers():
            for item in (p.pending_list() if hasattr(p, "pending_list") else []):
                item = dict(item)
                item["session_id"] = self.sid_of_ask(p) or getattr(
                    getattr(p, "_session", None), "sid", "")
                out.append(item)
        out.sort(key=lambda x: (str(x.get("session_id") or ""), x["ask_id"]))
        return {"pending": out, "count": len(out)}

    @admitted
    async def answer_ask(self, ask_id: int, *, choice: Any = None,
                         text: Any = None, sid: Optional[str] = None) -> dict:
        if sid is not None:
            sid = _session_module().validate_session_id(sid)
        matches = []
        for p in self.ask_providers():
            pending = getattr(p, "_pending", None)
            if isinstance(pending, dict) and int(ask_id) in pending:
                psid = self.sid_of_ask(p) or getattr(
                    getattr(p, "_session", None), "sid", "")
                if sid is None or str(psid) == sid:
                    matches.append(p)
        if len(matches) > 1:
            raise_code("EVT-101", ask_id=ask_id,
                       hint="多个会话存在同 ask_id;请指定会话")
        if len(matches) == 1:
            provider = matches[0]
            fn = getattr(provider, "answer_async", None) or provider.answer
            res = fn(int(ask_id), choice=choice, text=text, by=self.channel)
            ok = await res if inspect.isawaitable(res) else res
            if ok:
                return {"ok": True, "ask_id": int(ask_id), "session_id": sid}
        raise_code("APR-503", approval_id=int(ask_id),
                   hint="未知/已答复的 ask_id")

    # ------------------------------------------------------------ attachments / workflow / budget
    @admitted
    async def upload_attachment(self, sid: str, attachment: dict) -> dict:
        self._ensure_open()
        log_ = await self.require_session(sid)
        for payload in self.attachment_payloads([attachment], sid=sid):
            await log_.append("user.attachment.image", payload, actor="user")
        return {"ok": True, "sid": str(sid)}

    @admitted
    async def run_workflow(self, sid: str, steps: list, *,
                           name: str = "desktop",
                           stop_on_fail: bool = False) -> dict:
        self._ensure_open()
        if not isinstance(steps, list):
            raise_code("EVT-100", field="steps", advice="steps 须为非空字符串数组")
        log_ = await self.require_session(sid)
        queue = await self.queue_for(sid, log_)
        from pyharness.core.workflow import WorkflowRunner, queue_submit_adapter
        runner = WorkflowRunner(log_, queue_submit_adapter(log_, queue),
                                name=str(name or self.channel))
        results = await runner.run(steps, stop_on_fail=bool(stop_on_fail))
        return {"ok": all(r["ok"] for r in results), "sid": sid,
                "results": results}

    @admitted
    async def budget_dashboard(self, sid: str) -> dict:
        log_ = await self.require_session(sid)
        usages = [e for e in log_.events_after() if e.type == "llm.usage"]
        used_in = sum(int(u.payload.get("in_tokens", 0)) for u in usages)
        used_out = sum(int(u.payload.get("out_tokens", 0)) for u in usages)
        base = {"sid": sid, "used_in_tokens": used_in,
                "used_out_tokens": used_out,
                "recent": [dict(u.payload) for u in usages[-20:]]}
        budget = None
        try:                                       # 优先本会话自己装配的预算门面
            spine, _log2 = await self.public_spine_for(sid)
            budget = getattr(spine, "budget", None)
        except Exception:                          # noqa: BLE001 读面降级到 ctx
            budget = None
        if budget is None:
            # 共享 ctx.budget 作 fallback:owner 为空(外部单会话注入)或确属本会话
            # 才用;owner 指向别的会话(多会话最后装配者)→ 拒用,防串场。
            owner = getattr(self.ctx, "_budget_owner_sid", None)
            if owner is None or owner == sid:
                budget = getattr(self.ctx, "budget", None)
        snap = getattr(budget, "snapshot", None)
        if not callable(snap):
            return {**base, "disabled": True,
                    "reason": "budget-gate-unassembled", "limit_cny": None,
                    "used_cny": None, "warned": False, "ratio": 0.0}
        st = snap(sid)
        if inspect.isawaitable(st):
            st = await st
        limit = float(getattr(st, "limit_cny", 0) or 0)
        used = float(getattr(st, "used_cny", 0) or 0)
        ratio = (used / limit) if limit else 0.0
        cfg = _session_module()._cfg_of(self.ctx)
        warn = float(getattr(getattr(cfg, "budget", None), "warn_ratio", 0.8))
        return {**base, "disabled": False, "limit_cny": limit, "used_cny": used,
                "warned": ratio >= warn, "ratio": round(ratio, 4)}

    # ------------------------------------------------------------ plugins / preset
    def plugin_roots(self) -> list[Path]:
        roots = ([Path(__file__).resolve().parents[2] / "examples" / "plugins"]
                 if self.tenant_id == "default" else [self.tenant_root() / "plugins"])
        if self.tenant_id == "default":
            cfg = _session_module()._cfg_of(self.ctx)
            try:
                extra = getattr(getattr(cfg, "plugins", None), "dir", None)
                if extra:
                    roots.append(Path(str(extra)).expanduser())
            except Exception:                          # noqa: BLE001
                pass
        return roots

    @staticmethod
    def _plug_ctx_for(spine: Any) -> Any:
        return SimpleNamespace(session=getattr(spine, "session", None),
                               bus=getattr(spine, "bus", None))

    @admitted
    async def list_plugins(self) -> dict:
        out: list[dict] = []
        for sid, spine in self._engines.items():
            mgr = getattr(spine, "plugins", None)
            if mgr is None:
                continue
            for pid, rec in dict(getattr(spine, "plugin_state", {})).items():
                spec = rec.get("spec")
                out.append({
                    "id": pid,
                    "state": mgr.state(pid),
                    "version": getattr(spec, "manifest", {}).get("version", "?"),
                    "tools": list(rec.get("tool_names", ())),
                    "session": str(sid),
                })
        return {"plugins": out, "count": len(out)}

    @admitted
    async def plugin_action(self, kind: str, body: dict) -> dict:
        self._ensure_open()
        pid = str((body or {}).get("id") or "")
        if not pid:
            return {"ok": False, "error": "缺插件 id"}
        if not self._engines:
            return {"ok": False, "error": "无活动会话(先新建/打开会话)"}
        from pyharness.core import plugin_loader as _pl
        from pyharness.desktop.sessions import _plugin_within
        pdir = ((Path(__file__).resolve().parents[2] / "examples" / "plugins")
                if self.tenant_id == "default" else (self.tenant_root() / "plugins")) / pid
        results: list[dict] = []
        for sid, spine in list(self._engines.items()):
            mgr = spine.plugins
            try:
                if kind == "load":
                    d = Path(str(body.get("dir") or pdir)).expanduser().resolve()
                    if not d.exists():
                        return {"ok": False, "error": f"插件目录不存在:{d}"}
                    if not any(_plugin_within(d, r) for r in self.plugin_roots()):
                        return {"ok": False,
                                "error": "插件目录必须在配置的插件根目录内"}
                    spec = _pl.load_spec(d)
                    await _pl.load_plugin(mgr, spec, self._plug_ctx_for(spine),
                                          spine.tool_registry,
                                          state=spine.plugin_state)
                elif kind == "unload":
                    rec = spine.plugin_state.get(pid)
                    if rec is None:
                        results.append({"session": str(sid), "ok": False,
                                        "error": "未安装"})
                        continue
                    await _pl.unload_plugin(mgr, rec["spec"],
                                            self._plug_ctx_for(spine),
                                            spine.tool_registry,
                                            state=spine.plugin_state)
                elif kind == "reload":
                    rec = spine.plugin_state.get(pid)
                    if rec is None:
                        results.append({"session": str(sid), "ok": False,
                                        "error": "未安装"})
                        continue
                    await _pl.reload_plugin(
                        mgr, rec["spec"], self._plug_ctx_for(spine),
                        spine.tool_registry, state=spine.plugin_state,
                        pkg_dir=Path(rec.get("path", "")).parent or pdir)
                else:
                    return {"ok": False, "error": f"未知操作:{kind}"}
                results.append({"session": str(sid), "ok": True,
                                "state": mgr.state(pid)})
            except Exception as e:                     # noqa: BLE001
                results.append({"session": str(sid), "ok": False,
                                "error": f"{type(e).__name__}:{e}"[:200]})
        ok_all = all(r.get("ok") for r in results) and bool(results)
        return {"ok": ok_all, "results": results,
                "hint": "新会话(重启程序)自动预载 examples/plugins/ 全部插件"}

    @staticmethod
    def _reset_preset_state(spine: Any) -> None:
        from pyharness import engine as _eng
        cfg = spine.settings
        cfg.security.policy.preset = "strict"
        p = spine.scope.policy
        p.deny_tools = set()
        try:
            p.sandbox_level = "strict"
        except Exception:                              # noqa: BLE001
            pass
        try:
            cfg.security.sandbox.level = "strict"
        except Exception:                              # noqa: BLE001
            pass
        _eng._apply_preset(cfg, p, spine.tool_registry)

    @admitted
    async def set_preset(self, body: dict) -> dict:
        self._ensure_open()
        preset = str((body or {}).get("preset") or "")
        valid = ("strict", "standard", "readonly", "locked")
        if preset not in valid:
            return {"ok": False, "error": f"非法档位:{preset}", "valid": list(valid)}
        if not self._engines:
            return {"ok": False, "error": "无活动会话(先新建/打开会话)"}
        from pyharness import engine as _eng
        done = 0
        for spine in list(self._engines.values()):
            try:
                spine.settings.security.policy.preset = preset
                p = spine.scope.policy
                p.deny_tools = set()
                try:
                    p.sandbox_level = "strict"
                except Exception:                      # noqa: BLE001
                    pass
                try:
                    spine.settings.security.sandbox.level = "strict"
                except Exception:                      # noqa: BLE001
                    pass
                _eng._apply_preset(spine.settings, p, spine.tool_registry)
                done += 1
            except Exception as e:                     # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}:{e}"[:200]}
        return {"ok": True, "preset": preset, "sessions": done,
                "hint": "exec.* 等危险工具:standard 档可见,strict 档隐藏"}

    def skill_installer(self) -> Any:
        from pyharness.core.skill_registry import SkillInstaller
        cfg = _session_module()._cfg_of(self.ctx)
        sk = getattr(cfg, "skills", None)
        root = (Path(str(getattr(sk, "dir", "~/.pyharness/skills"))).expanduser()
                if self.tenant_id == "default"
                else self.tenant_root() / "skills")
        return SkillInstaller(
            root,
            max_package_bytes=int(getattr(sk, "max_package_bytes", 5 * 1024 * 1024)),
            max_files=int(getattr(sk, "max_files", 200)))

    @admitted
    async def search_skills(self, query: str,
                            registry_url: str = "") -> dict:
        cfg = _session_module()._cfg_of(self.ctx)
        url = str(registry_url or getattr(getattr(cfg, "skills", None),
                                          "registry_url", "") or "").strip()
        if not url:
            raise_code("CFG-601", field="skills.registry_url",
                       advice="未配置 Skill Registry URL")
        url = _validate_registry_url(url)
        rows = await self.skill_installer().search(query, url)
        return {"registry_url": url, "skills": rows, "count": len(rows)}

    @admitted
    async def install_skill(self, sid: str, name: str, *, version: str = "",
                            registry_url: str = "",
                            approved_by: str = "user") -> dict:
        self._ensure_open()
        cfg = _session_module()._cfg_of(self.ctx)
        url = str(registry_url or getattr(getattr(cfg, "skills", None),
                                          "registry_url", "") or "").strip()
        if not url:
            raise_code("CFG-601", field="skills.registry_url",
                       advice="未配置 Skill Registry URL")
        url = _validate_registry_url(url)
        result = await self.skill_installer().install(
            name, registry_url=url, version=version or None,
            approved_by=approved_by)
        log_ = await self.require_session(sid)
        await log_.append("skill.installed", {
            "name": result["name"], "version": result["version"],
            "sha256": result["sha256"], "source": result["source"],
            "approved_by": approved_by}, actor="user", sync=True)
        self.skills_mgr().scan()
        return result

    @admitted
    async def remove_skill(self, sid: str, name: str, *,
                           approved_by: str = "user") -> dict:
        self._ensure_open()
        result = self.skill_installer().remove(name, approved_by=approved_by)
        if result.get("removed"):
            log_ = await self.require_session(sid)
            await log_.append("skill.removed", {
                "name": name, "version": result.get("version", ""),
                "approved_by": approved_by}, actor="user", sync=True)
            self.skills_mgr().scan()
        return result

    @admitted
    async def rollback_skill(self, sid: str, name: str, version: str, *,
                             approved_by: str = "user") -> dict:
        self._ensure_open()
        result = self.skill_installer().rollback(
            name, version, approved_by=approved_by)
        log_ = await self.require_session(sid)
        await log_.append("skill.rollback", {
            "name": name, "from_version": result.get("from_version", ""),
            "to_version": version, "approved_by": approved_by},
            actor="user", sync=True)
        self.skills_mgr().scan()
        return result

    def skill_versions(self, name: str) -> list[str]:
        return self.skill_installer().versions(name)

    def skills_mgr(self) -> SkillManager:
        for spine in self._engines.values():
            mgr = getattr(spine, "skills", None)
            if mgr is not None:
                return mgr
        if self.tenant_id != "default":
            return SkillManager([self.tenant_root() / "skills"])
        roots = [Path(__file__).resolve().parents[2] / "skills"]
        cfg = _session_module()._cfg_of(self.ctx)
        try:
            extra = getattr(getattr(cfg, "skills", None), "dir", None)
            if extra:
                roots.append(Path(str(extra)).expanduser())
        except Exception:                              # noqa: BLE001
            pass
        return SkillManager(roots)

    @admitted
    async def list_skills(self) -> dict:
        mgr = self.skills_mgr()
        rows = mgr.list()
        return {"skills": rows, "count": len(rows), "catalog": mgr.render_catalog()}

    @admitted
    async def skill_detail(self, name: str) -> dict:
        data = self.skills_mgr().load(str(name))
        return {"ok": True, "name": data["name"],
                "description": data["description"], "dir": data["dir"],
                "body": data["body"]}

    @admitted
    async def reload_skills(self) -> dict:
        counts = []
        for spine in self._engines.values():
            mgr = getattr(spine, "skills", None)
            if mgr is not None:
                counts.append({"session": str(getattr(
                    getattr(spine, "session", None), "sid", "?")),
                    "count": mgr.scan()})
        if not counts:
            counts.append({"direct": True, "count": self.skills_mgr().scan()})
        return {"ok": True, "results": counts,
                "hint": "新技能立即可被 agent skill.load"}
