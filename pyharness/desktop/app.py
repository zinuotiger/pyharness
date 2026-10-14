"""Desktop FastAPI application and API handlers."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import secrets
import stat
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from fastapi import Body, Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.responses import PlainTextResponse

from pyharness.application import ApplicationService, ApplicationServiceRegistry
from pyharness.core.approval import ApprovalProvider
from pyharness.core.tenant_settings import (TenantSettingsStore,
                                            normalize_tenant_id,
                                            session_tenants,
                                            tenant_of_log,
                                            tenants_dir)
from pyharness.core.task_queue import TaskQueue, queue_kwargs_of
from pyharness.errors import PyHError, raise_code
from pyharness.events import EVENT_TYPES
from .auth_cookies import cookie_name, read_cookie

# 路径中的会话 id(供租户服务端派生:以会话注册租户为准,不信客户端自报头)
# 字符集必须与 SessionLog 的 sid 合法面一致(sessions.validate_session_id =
# `s-[A-Za-z0-9._-]{6,64}`)。**原为 `[0-9a-zA-Z]+`,不含 `-`/`.`** ⇒ fork 会话
# (`s-fork-<hex>`,cli.py 生成)只匹配到 `s-fork` 前缀 → 查不到租户 → 静默回落
# 客户端头。故此处放宽为 `[0-9A-Za-z._-]+`(`-` 置末为字面量)。

from .constants import (
    SSE_HEARTBEAT_S,
    _STATUS_FOR_CODE,
)
from .projection import (
    EventStreamHub,
    StreamClient,
    redact,
)
from .sessions import (
    DesktopSessionManager,
    _SID_RE,
    _await,
    _cfg_of,
    _resolve_secret_value,
    _sessions_dir_of,
    validate_session_id,
)

from pyharness.desktop.host_policy import (_SESSION_ID_IN_PATH, log, LoopbackHostMiddleware)

if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    from .bridge import DesktopBridge


from pyharness.desktop.authority import DesktopAuthority
from pyharness.desktop.routes import DesktopRoutes
from pyharness.desktop.pages import DesktopPages
from pyharness.desktop.handlers import DesktopHandlers
from pyharness.desktop.lifecycle import DesktopLifecycle

class DesktopApp(DesktopAuthority, DesktopRoutes, DesktopPages, DesktopHandlers, DesktopLifecycle):
    """桌面程序总装(F065 内核入口):FastAPI 装配 + 总线 → SSE fan-out + 会话面。

    字段(spec 数据结构表):api / url / bridge / hub / ctx / stopping / server / loop;
    实现附加:_surface(会话解析面)/ _logs(会话同柄缓存)/ _queues(per-session TaskQueue)/
    _approvals(per-session ApprovalProvider)/ _sub(总线订阅登记)。
    """

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.hub = EventStreamHub()
        self.bridge: Optional[DesktopBridge] = None
        self.server: Any = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stopping = threading.Event()
        self.url: str = ""
        self._sub: Any = None
        self._graceful_done: bool = False        # 优雅停服幂等标记(双击关闭只走一次)
        self._tenant_var: contextvars.ContextVar[str] = contextvars.ContextVar(
            "pyharness_desktop_tenant", default="default")
        self._service_registry = ApplicationServiceRegistry(
            ctx, channel="desktop")
        self.service = self._service_registry.get("default")
        self.api = FastAPI(title="PyHarness Desktop", docs_url=None, redoc_url=None)
        self._api_token = self._resolve_api_token()
        self._tstore: Optional[TenantSettingsStore] = None   # 租户令牌库(惰性)
        self.api.middleware("http")(self._tenant_middleware)
        # Starlette's last-added middleware is outermost: reject malformed
        # Host before account/cookie code constructs a URL (also under ASGI).
        self.api.add_middleware(LoopbackHostMiddleware)
        self.mount_api()
        from .platform_api import mount_platform
        mount_platform(self)
        from .accounts_api import mount_accounts
        mount_accounts(self)
        bus = getattr(ctx, "bus", None)
        if bus is not None:
            self._sub = self._subscribe_all(bus)

    # ------------------------------------------------ tenant/service registry
    @property
    def service(self) -> ApplicationService:
        return self._service_registry.get(self._tenant_var.get())

    @service.setter
    def service(self, value: ApplicationService) -> None:
        tenant = normalize_tenant_id(getattr(value, "tenant_id", "default"))
        self._service_registry._services[tenant] = value

    def current_tenant(self) -> str:
        return self._tenant_var.get()

    def _sse_heartbeat_seconds(self) -> float:
        """Keep the public app configuration seam at the composition boundary."""
        return SSE_HEARTBEAT_S


    # ------------------------------------------------ 租户鉴权(R31-2)


    # ------------------------------------------------ service compatibility
    @property
    def _logs(self) -> dict[str, Any]:
        return self.service._logs

    @_logs.setter
    def _logs(self, value: dict[str, Any]) -> None:
        self.service._logs = value

    @property
    def _session_locks(self) -> dict[str, asyncio.Lock]:
        return self.service._session_locks

    @_session_locks.setter
    def _session_locks(self, value: dict[str, asyncio.Lock]) -> None:
        self.service._session_locks = value

    @property
    def _queue_locks(self) -> dict[str, asyncio.Lock]:
        return self.service._queue_locks

    @_queue_locks.setter
    def _queue_locks(self, value: dict[str, asyncio.Lock]) -> None:
        self.service._queue_locks = value

    @property
    def _queues(self) -> dict[str, TaskQueue]:
        return self.service._queues

    @_queues.setter
    def _queues(self, value: dict[str, TaskQueue]) -> None:
        self.service._queues = value

    @property
    def _approvals(self) -> dict[str, ApprovalProvider]:
        return self.service._approvals

    @_approvals.setter
    def _approvals(self, value: dict[str, ApprovalProvider]) -> None:
        self.service._approvals = value

    @property
    def _engines(self) -> dict[str, Any]:
        return self.service._engines

    @_engines.setter
    def _engines(self, value: dict[str, Any]) -> None:
        self.service._engines = value

    @property
    def _surface(self) -> Any:
        return self.service._surface

    @_surface.setter
    def _surface(self, value: Any) -> None:
        self.service._surface = value

    # ------------------------------------------------ API 鉴权


    # ------------------------------------------------ 总线订阅(偏离 4)
    def _subscribe_all(self, bus: Any) -> list:
        """全事件 fan-out:词表 70 精确类型 + plugin.* 段通配(owner=desktop)。"""
        subs = []
        for t in EVENT_TYPES:
            subs.append(bus.subscribe(t, self.hub.forward, owner="desktop"))
        subs.append(bus.subscribe("plugin.*", self.hub.forward, owner="desktop"))
        return subs

    def _surface_mgr(self) -> Any:
        return self.service.surface_mgr()

    # ------------------------------------------------ API 装配

    # ------------------------------------------------ 统一错误体


    # ------------------------------------------------ 页面(前端入口)

    # ------------------------------------------------ 会话解析(端点共用)


    async def _queue_for(self, sid: str, log_: Any) -> TaskQueue:
        """per-session TaskQueue(runner = per-session 真实引擎;真 LLM 已注册)。"""
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
        """per-session 引擎 runner(懒装配):复用 manager 已 open 的 log_/bus,
        补 loop/scope/llm/空工具表 + 总线落盘订阅 → engine.make_runner。

        无可用 LLM 凭据时在落 user.message 前失败,避免留下“有消息无任务”的半提交。
        """
        spine = self._engines.get(sid)
        from pyharness import engine as _eng
        from pyharness.engine import make_runner as _eng_make_runner
        cfg = self.ctx.settings
        _eng.register_default_llm(cfg)              # 幂等;无 key → CRED-701
        if spine is None:
            store = getattr(self._surface_mgr(), "_stores", {}).get(sid)
            spine = _eng.build_runner_components(
                cfg, log_=log_, bus=getattr(self.ctx, "bus", None),
                sessions_dir=_sessions_dir_of(self.ctx), store=store,
                attach_persistence=False,   # manager 已订阅落盘;重复订=双写卡死
                # GAP-11:Web 桌面壳 = human 通道 "desktop"(框架侧声明,不信任
                # 前端自报);不下沉到 spine 则该会话每次工具授权 APR-503。
                channel="desktop")
            self._engines[sid] = spine
        if not getattr(spine, "_plugins_ready", False):
            await _eng._preload_plugins(spine)   # 示例插件预载(util.now 会话可用)
            spine._plugins_ready = True
        # 审批接线:engine ApprovalProvider → ctx.approval(桌面裁决端点
        # _provider_owning 经 ctx.approval 兜底定位;否则弹窗 A/B 打来 APR-503)
        if getattr(self.ctx, "approval", None) is None:
            self.ctx.approval = spine.approval
            self.ctx._approval_owner_sid = sid   # 归属记账:_approval_for 据此判能否复用
        if getattr(self.ctx, "guard", None) is None:
            self.ctx.guard = spine.guard
            self.ctx._guard_owner_sid = sid
        # 预算门面:按会话记账归属;/api/budget/{sid} 主路径读本会话 spine.budget,
        # ctx.budget 仅作 fallback 且需 owner==sid(防多会话共享 ctx 串场)
        self.ctx.budget = spine.budget
        self.ctx._budget_owner_sid = sid
        return _eng_make_runner(spine)

    def _runner_seam(self) -> Any:
        """执行器注入点(引擎装配层):ctx.make_runner()/ctx.task_runner 优先,None 缺省。"""
        ctx = self.ctx
        make = getattr(ctx, "make_runner", None)
        if callable(make):
            return make()
        return getattr(ctx, "task_runner", None)

    @staticmethod
    def _owner_for(sid: str) -> str:
        """桌面会话的编排 owner(channel 级身份,不信任前端自报)。"""
        return f"desktop:{sid}"

    async def _public_spine_for(self, sid: str) -> tuple[Any, Any]:
        """Build/return a session spine without requiring an LLM credential.

        Management read/pause/remove actions should remain available even if the
        provider key is absent; start/spawn paths call ``_queue_for`` first, which
        performs the credential/LLM registration gate.
        """
        log_ = await self._require_session(sid)
        spine = self._engines.get(sid)
        if spine is None:
            from pyharness import engine as _eng
            store = getattr(self._surface_mgr(), "_stores", {}).get(sid)
            spine = _eng.build_runner_components(
                _cfg_of(self.ctx), log_=log_, bus=getattr(self.ctx, "bus", None),
                sessions_dir=_sessions_dir_of(self.ctx), store=store,
                attach_persistence=False, channel="desktop")   # GAP-11
            self._engines[sid] = spine
            await _eng.activate_orchestration(spine)
        return log_, spine

    def _approval_for(self, sid: str, log_: Any) -> ApprovalProvider:
        """per-session ApprovalProvider;ctx.approval(装配注入,单会话形态)优先。

        多会话各 provider 以 bus=None 构造(偏离 1 注):裁决结果事件仍由 sess.append
        经会话总线分发到 hub(SSE 可见),provider 侧按 approval.py 偏离 3 直接消费
        on_verdict——避免共享总线把 A 会话裁决扇到 B provider 触发跨会话 APR-503
        system.error 噪音(approval.on_verdict 未知 id 兜底路径)。
        """
        provider = self._approvals.get(sid)
        if provider is None:
            ctx_approval = getattr(self.ctx, "approval", None)
            owner = getattr(self.ctx, "_approval_owner_sid", None)
            # 仅当共享 ctx.approval 确属本会话(owner==sid)或未记归属才复用;否则
            # 按本会话 log_ 建 per-session provider——防第二会话拿到第一会话的审批通道。
            if ctx_approval is not None and owner in (None, sid):
                return ctx_approval          # 装配注入的全会话 provider(单会话形态)
            provider = ApprovalProvider(
                session=log_, bus=None, config=_cfg_of(self.ctx),
                # R12-3:队列联动(惰性取值 ⇒ 与构造序无关)
                queue_getter=lambda: self._queues.get(sid))
            self._approvals[sid] = provider
        return provider

    def _all_approval_providers(self) -> list:
        """pending 聚合:per-session providers + ctx.approval(去重)。"""
        seen, out = set(), []
        ctx_approval = getattr(self.ctx, "approval", None)
        for p in list(self._approvals.values()) + ([ctx_approval] if ctx_approval else []):
            if p is not None and id(p) not in seen:
                seen.add(id(p))
                out.append(p)
        return out

    def request_shutdown(self) -> None:
        """关窗事件入口:置 stopping 旗标(幂等);真正停服在 webview.start() 返回后。"""
        self.stopping.set()

    def publish_operator_token(self) -> Optional[Path]:
        """把**本进程生成**的操作者令牌落盘,供浏览器/外部客户端取用。

        ``R31-2`` 的配套。引导页按 ADR-023 不再向无凭证调用方发放令牌,而
        ``shell.web.token`` 未配置时令牌是**进程随机**且从不打印 ⇒ 浏览器用户无从
        取得,该入口等于关闭(实测:改动后浏览器访问 401 且无处可查)。故生成态令牌
        落盘到 ``<storage.root>/web.token``(与租户令牌 ``tenants/<t>/token`` 同口径)。
        **不写日志、不进 API 响应**;调用方只应记录**路径**。

        **权限(2026-09-21 UI 验收实测更正)**:代码调用 ``chmod(0o600)``,但

        - **POSIX**:生效,文件为 ``0600``。
        - **Windows**:``chmod`` **不生效** —— 实测 ``stat.S_IMODE`` 仍为 ``0o666``
          (NTFS 不存 POSIX 权限位)。实际可读面由**父目录 ACL 继承**决定:
          ``%USERPROFILE%`` 下的 ``.pyharness`` 默认只含当前用户 + SYSTEM +
          Administrators(等效"仅属主");若该目录被外部工具**额外授权**给别的组(本机实测存在
          一条 ``CodexSandboxUsers:(OI)(CI)(RX)``),本文件会**继承**该授权。
          故 Windows 上**不得**声称"0600";要收紧须显式改 ACL(未做,见 ADR-023
          的已知边界)。

        配置了 ``shell.web.token`` 时**不落盘** —— 用户已知该值,没必要多一份副本。
        返回落盘路径;非生成态 / 取不到 ``storage.root`` ⇒ ``None``。
        """
        if not (self._api_token and self._api_token_generated):
            return None
        root = self._storage_root()
        if root is None:
            return None
        p = root / "web.token"
        try:
            root.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(self._api_token, encoding="utf-8")
            tmp.replace(p)                                # pathlib 原子替换
            p.chmod(stat.S_IRUSR | stat.S_IWUSR)          # POSIX 0600;Windows 无效(见 docstring)
        except OSError:
            return None
        return p

    async def governance_audit(self, sid: str, decision_id: str = "",
                               since_seq: int = 0,
                               reconcile: bool = False) -> dict:
        """治理审计面(GAP-7):决策因果还原 / 被拒清单 / 一致性对账。

        与 ``/telemetry``(旧遥测:事件类型计数)**语义不同**:本端点回答"这条
        决策为什么发生、经过了什么、拦了没有真执行",数据来源是 append-only
        事件日志的重放(可复现),不是计数聚合。
        """
        return await self.service.governance_audit(
            sid, decision_id=decision_id, since_seq=since_seq,
            reconcile=bool(reconcile))

    async def governance_evidence(self, sid: str, task_id: str = "") -> dict:
        """证据归档面(GAP-8):按任务段聚合的治理证据工件(由日志重建)。"""
        return await self.service.governance_evidence(sid, task_id=task_id)




    # ============================================================ 端点实现

    # ------------------------------------------------ jobs / schedule / subagent


    # -------------------------------------------------- 插件管理(#50/#51)


    # ---------------------------------------------- 权限档位(#39 动态切换)


    # ---------------------------------------------- 技能管理(F073 桌面面)


    # ------------------------------------------------ 编辑/重发/反馈/Webhook


    # ============================================================ 优雅停服


