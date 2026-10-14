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
from typing import Any, Optional, TYPE_CHECKING
from pyharness.application.message_projection import (log, _with_message_origin, _as_dict, _json_safe)
from pyharness.application.session_access import (_session_module, _METADATA_HOSTS)

if TYPE_CHECKING:
    from pyharness.engine import EngineSpine
    from pyharness.core.session import SessionLog

from pyharness.application.models import SHELL_CAPABILITIES
from pyharness.core.approval import ApprovalProvider
from pyharness.core.channel import normalize_channel
from pyharness.core.skill import SkillManager
from pyharness.core.task_queue import TaskQueue, queue_kwargs_of
from pyharness.core.task_runner import TaskRunner
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


from pyharness.core.url_policy import validate_service_url as _validate_registry_url


from pyharness.core.lifecycle import admitted, stop_admissions


from pyharness.application.tenant_service import TenantService
from pyharness.application.orchestration_service import OrchestrationService
from pyharness.application.history_service import HistoryService
from pyharness.application.interaction_service import InteractionService
from pyharness.application.extension_service import ExtensionService

class ApplicationService(TenantService, OrchestrationService, HistoryService, InteractionService, ExtensionService):
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
        self._logs: dict[str, SessionLog] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._queue_locks: dict[str, asyncio.Lock] = {}
        self._queues: dict[str, TaskQueue] = {}
        self._approvals: dict[str, ApprovalProvider] = {}
        self._engines: dict[str, EngineSpine] = {}
        self._settings_store = TenantSettingsStore(
            tenants_dir(self._storage_root()))     # R31-2:派生点收口到 tenants_dir
        register_tenant_store(self.tenant_id, self._settings_store)
        from pyharness.core.llm import LLMRuntime
        self.llm_runtime = LLMRuntime()


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

    async def _engine_runner_for(self, sid: str, log_: SessionLog) -> TaskRunner:
        spine = self._engines.get(sid)
        from pyharness import engine as _eng
        from pyharness.engine import make_runner as _eng_make_runner
        from pyharness.application.execution_config import compile_execution
        cfg, platform_definition = compile_execution(self, log_)
        if self.tenant_id != 'default' and self._settings_store.active_profile(self.tenant_id) is None and not (platform_definition and platform_definition.executor == 'codex'):
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
                channel=self.channel, llm_runtime=self.llm_runtime,
                register_model=not (platform_definition and platform_definition.executor == 'codex'))
            try:
                if platform_definition is not None:
                    from pyharness.application.platform_runtime import PlatformRuntime
                    PlatformRuntime(self.platform, platform_definition, sid).configure_spine(spine)
            except BaseException:
                await spine.close()
                raise
            self._engines[sid] = spine
        await _eng.prepare_plugins(spine)
        if self.tenant_id == "default":
            if getattr(self.ctx, "approval", None) is None:
                self.ctx.approval = spine.approval
                self.ctx._approval_owner_sid = sid   # 归属记账:approval_for 判复用
            if getattr(self.ctx, "guard", None) is None:
                self.ctx.guard = spine.guard
                self.ctx._guard_owner_sid = sid
        if platform_definition is not None and platform_definition.executor == 'codex':
            if spine.external_runner is None:
                from pyharness.application.codex_runner import CodexRunner
                spine.external_runner = CodexRunner(spine, spine.platform_runtime)
            return spine.external_runner
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
        for spine in self._engines.values():
            runner = getattr(getattr(spine, 'subagent', None), '_runner', None)
            engine_providers.extend(getattr(runner, 'approval_providers', {}).values())
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
        if hasattr(self, '_channels'):
            await attempt(self._channels.close)
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


    # ------------------------------------------------------------ 治理审计(GAP-7)

    # ------------------------------------------------------------ 治理证据(GAP-8)

    # ------------------------------------------------------------ edit / feedback


    # ------------------------------------------------------------ approvals / asks


    # ------------------------------------------------------------ attachments / workflow / budget


    # ------------------------------------------------------------ plugins / preset


