"""Desktop FastAPI application and API handlers."""
from __future__ import annotations

import asyncio
import contextvars
import html as html_mod
import inspect
import json
import logging
import os
import pathlib
import secrets
import threading
import time
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from fastapi import Body, Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pyharness.application import ApplicationService, ApplicationServiceRegistry
from pyharness.core.approval import ApprovalProvider
from pyharness.core.tenant_settings import normalize_tenant_id
from pyharness.core.task_queue import TaskQueue
from pyharness.errors import PyHError, raise_code
from pyharness.events import EVENT_TYPES, Envelope
from pyharness.events.vocab import TRANSIENT_TYPES, validate_payload

from .constants import (
    BRIDGE_TIMEOUT_S,
    CHANNEL,
    HOST,
    LISTEN_TIMEOUT_S,
    SSE_HEARTBEAT_S,
    WARN_RATIO,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
    _STATUS_FOR_CODE,
)
from .net import pick_free_port, run_uvicorn, wait_listening_async, wait_until_listening
from .projection import (
    EventStreamHub,
    StreamClient,
    TimelineNode,
    _env_dict,
    approval_node,
    derive_timeline,
    redact,
    redact_args,
    render_timeline_node,
)
from .sessions import (
    DesktopSessionManager,
    _await,
    _cfg_of,
    _plugin_within,
    _resolve_secret_value,
    _sessions_dir_of,
    _surface_of,
    validate_session_id,
)

log = logging.getLogger("pyharness.desktop.app")

class DesktopApp:
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
        self.api.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=["127.0.0.1", "localhost"],
            www_redirect=False)
        self._api_token = self._resolve_api_token()
        self.api.middleware("http")(self._tenant_middleware)
        self.mount_api()
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

    async def _tenant_middleware(self, request: Request, call_next: Any) -> Any:
        tenant = normalize_tenant_id(
            request.headers.get("x-pyharness-tenant")
            or request.query_params.get("tenant") or "default")
        token = self._tenant_var.set(tenant)
        try:
            return await call_next(request)
        finally:
            self._tenant_var.reset(token)

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
    def _resolve_api_token(self) -> str:
        """API token:配置 shell.web.token(env:/file: 引用)优先,缺省进程级随机令牌。"""
        raw = ""
        cfg = _cfg_of(self.ctx)
        try:
            web = getattr(getattr(cfg, "shell", None), "web", None)
            raw = str(getattr(web, "token", None) or "")
        except Exception:                                # noqa: BLE001 非 Settings 形状
            raw = ""
        val = _resolve_secret_value(raw)
        return val if val else secrets.token_hex(16)

    def _require_api_auth(self, request: Request,
                          x_pyharness_token: Optional[str] = Header(default=None),
                          authorization: Optional[str] = Header(default=None)) -> None:
        """写端点依赖:令牌缺失/不匹配 → CRED-701(401);前端从 index meta 注入。"""
        expected = self._api_token
        if not expected:
            return
        given = x_pyharness_token or ""
        if not given and authorization and authorization.lower().startswith("bearer "):
            given = authorization[7:].strip()
        if not given or not secrets.compare_digest(given, expected):
            raise_code("CRED-701", reason="api-token",
                       hint="缺少或非法 API token(X-PyHarness-Token / Bearer)")

    def _plugin_roots(self) -> list[Path]:
        """插件装载白名单根:仓库示例目录 + 配置 plugins.dir,HTTP 只能装载其下目录。"""
        roots = [Path(__file__).resolve().parents[1] / "examples" / "plugins"]
        cfg = _cfg_of(self.ctx)
        try:
            extra = getattr(getattr(cfg, "plugins", None), "dir", None)
            if extra:
                roots.append(Path(str(extra)).expanduser())
        except Exception:                                # noqa: BLE001 鸭子配置
            log.debug("plugin root config unavailable", exc_info=True)
        return [p.resolve() for p in roots]

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

    async def _require_session(self, sid: str) -> Any:
        return await self.service.require_session(sid)

    async def _queue_for(self, sid: str, log_: Any) -> TaskQueue:
        return await self.service.queue_for(sid, log_)

    async def _engine_runner_for(self, sid: str, log_: Any) -> Any:
        return await self.service._engine_runner_for(sid, log_)

    def _runner_seam(self) -> Any:
        return self.service.runner_seam()

    @staticmethod
    def _owner_for(sid: str) -> str:
        return ApplicationService.owner_for(sid)

    async def _public_spine_for(self, sid: str) -> tuple[Any, Any]:
        return await self.service.public_spine_for(sid)

    def _approval_for(self, sid: str, log_: Any) -> ApprovalProvider:
        return self.service.approval_for(sid, log_)

    def _all_approval_providers(self) -> list:
        return self.service.all_approval_providers()

    # ------------------------------------------------ API 装配
    def mount_api(self) -> None:
        """注册全部端点(只读投影 + 引擎门面写 + SSE)+ PyHError 统一错误体(ADR-011)。"""
        a = self.api
        a.add_api_route("/", self._index_page, methods=["GET"])
        a.add_api_route("/api/sessions", self.create_session, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions", self.list_sessions, methods=["GET"])
        a.add_api_route("/api/sessions/{sid}", self.delete_session,
                        methods=["DELETE"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages", self.session_messages,
                        methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/messages", self.create_message,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/timeline", self.session_timeline,
                        methods=["GET"])                            # 轨迹(核心)
        a.add_api_route("/api/sessions/{sid}/event/{seq}", self.event_detail,
                        methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/telemetry", self.telemetry_report,
                        methods=["GET"])            # F066 审计导出(桌面面)
        a.add_api_route("/api/stream", self.stream_sse, methods=["GET"])  # SSE
        a.add_api_route("/api/approvals/pending", self.pending_approvals,
                        methods=["GET"])
        a.add_api_route("/api/approvals/{sid}/{aid}", self.decide_approval_for,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/approvals/{aid}", self.decide_approval,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 审批弹窗裁决
        a.add_api_route("/api/asks/pending", self.pending_asks,
                        methods=["GET"])                              # #38 反问轮询
        a.add_api_route("/api/asks/{sid}/{ask_id}", self.answer_ask_for,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/asks/{ask_id}", self.answer_ask,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # #38 反问答复
        a.add_api_route("/api/plugins", self.list_plugins,
                        methods=["GET"])                              # 插件列表
        a.add_api_route("/api/plugins/load", self.plugin_load,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 插件装载
        a.add_api_route("/api/plugins/unload", self.plugin_unload,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 插件卸载
        a.add_api_route("/api/plugins/reload", self.plugin_reload,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 插件热重载
        a.add_api_route("/api/preset", self.set_preset,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 权限档位切换
        a.add_api_route("/api/capabilities", self.capabilities,
                        methods=["GET"])                              # 双壳能力契约
        a.add_api_route("/api/tenant", self.tenant_state, methods=["GET"])
        a.add_api_route("/api/settings/models", self.list_model_profiles,
                        methods=["GET"])
        a.add_api_route("/api/settings/models", self.save_model_profile,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/settings/models/{profile_id}/activate",
                        self.activate_model_profile, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/settings/models/{profile_id}",
                        self.delete_model_profile, methods=["DELETE"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills", self.list_skills,
                        methods=["GET"])                              # 技能目录
        a.add_api_route("/api/skills/registry/search", self.search_skills,
                        methods=["GET"])                              # Registry 搜索
        a.add_api_route("/api/skills/reload", self.reload_skills,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 技能库重扫
        a.add_api_route("/api/sessions/{sid}/skills/install",
                        self.install_skill, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/skills/{name}/rollback",
                        self.rollback_skill, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/skills/{name}/remove",
                        self.remove_skill, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills/{name}/versions", self.skill_versions,
                        methods=["GET"])                              # 已装版本
        a.add_api_route("/api/skills/{name}", self.skill_detail,
                        methods=["GET"])                              # 技能正文
        a.add_api_route("/api/budget/{sid}", self.budget_dashboard, methods=["GET"])
        a.add_api_route("/api/attachments", self.upload_attachment, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/workflow", self.run_workflow,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        # ------------------------------------------------------------ 编排管理面
        a.add_api_route("/api/sessions/{sid}/jobs", self.list_jobs,
                        methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/jobs", self.start_job,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/jobs/{job_id}", self.job_status,
                        methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/jobs/{job_id}/cancel",
                        self.cancel_job, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/schedules", self.list_schedules,
                        methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/schedules/action",
                        self.schedule_action, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/subagents", self.list_subagents,
                        methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/subagents", self.spawn_subagent,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/subagents/{sub_id}/cancel",
                        self.cancel_subagent, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        # F063 编辑/重发 + #12 反馈 + #57 webhook(桌面后端面;前端按钮在 ui/)
        # 固定后缀必须先于 {seq} 注册,否则 "last-user" 会被当成 int seq。
        a.add_api_route("/api/sessions/{sid}/messages/last-user",
                        self.edit_last_user, methods=["PATCH"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/last-user/resend",
                        self.resend_last_user, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/last-agent/feedback",
                        self.feedback_last_agent, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/{seq}",
                        self.edit_message, methods=["PATCH"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/{seq}/resend",
                        self.resend_message, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/{seq}/feedback",
                        self.feedback, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/webhook", self.webhook_ingest, methods=["POST"])
        a.add_exception_handler(PyHError, self._pyhe_handler)
        a.add_exception_handler(Exception, self._unexpected_handler)

    # ------------------------------------------------ 统一错误体
    @staticmethod
    def _detail_of(e: PyHError) -> Optional[dict]:
        """错误 detail:ctx 脱敏后透出(ERR §1.4:禁 ctx 敏感值原文)。"""
        if not e.ctx:
            return None
        return redact(e.ctx)

    async def _pyhe_handler(self, request: Request, exc: PyHError) -> JSONResponse:
        """PyHError → ApiErrorBody{code,advice,detail} + HTTP 状态映射(ADR-011)。"""
        status = _STATUS_FOR_CODE.get(exc.code, 500)
        advice = (exc.ctx.get("advice") or exc.ctx.get("hint")
                  or exc.spec.advice or exc.message)
        body: dict[str, Any] = {"code": exc.code, "advice": str(advice),
                                "detail": self._detail_of(exc)}
        return JSONResponse(status_code=status, content=body)

    async def _unexpected_handler(self, request: Request, exc: Exception) -> JSONResponse:
        """未预期兜底 → CYC-999(堆栈仅本地 debug;远端只回码+建议)。"""
        log.error("desktop api 未预期异常 path=%s", request.url.path, exc_info=True)
        return JSONResponse(status_code=500,
                            content={"code": "CYC-999",
                                     "advice": "引擎内部错误(见本地日志)",
                                     "detail": {"type": type(exc).__name__}})

    # ------------------------------------------------ 页面(前端入口)
    def _index_page(self) -> HTMLResponse:
        """根路由:加载内嵌前端页(会话列表/对话/轨迹时间线/审批/预算)。

        前端文件打包为 data 资源:源码运行读 pyharness/ui/index.html;PyInstaller
        打包时 --add-data 携带同相对路径(_MEIPASS 下亦命中);缺失 → 兜底提示页。
        API token 以 meta 注入前端(写端点依赖校验,读端点不强制)。
        """
        try:
            import importlib.resources as _ir
            html = _ir.files("pyharness.ui").joinpath("index.html").read_text(
                encoding="utf-8")
        except Exception:                       # noqa: BLE001 资源缺失兜底
            from pathlib import Path as _P
            p = _P(__file__).resolve().parent / "ui" / "index.html"
            try:
                html = p.read_text(encoding="utf-8")
            except Exception:                   # noqa: BLE001
                html = ("<html><body style='background:#0f1420;color:#dbe4f5;"
                        "font-family:sans-serif;display:flex;align-items:center;"
                        "justify-content:center;height:100vh'>"
                        "<div><h2>PyHarness Desktop</h2>"
                        "<p>前端资源缺失(ui/index.html 未随包携带)</p></div></body></html>")
        token = self._api_token or ""
        if token:
            safe_token = html_mod.escape(token, quote=True)
            meta = (f'<meta name="pyharness-token" content="{safe_token}">')
            html = html.replace("</head>", meta + "</head>", 1)
        return HTMLResponse(html)

    # ------------------------------------------------ 会话解析(端点共用)
    async def _require_session(self, sid: str) -> Any:
        """解析会话日志(同柄缓存):未 open → EVT-106 守卫(伪码 ensure_open_session)。"""
        sid = validate_session_id(sid)
        lock = self._session_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            if sid in self._logs:           # 同柄复用(INV-03)
                return self._logs[sid]
            log_ = await _await(self._surface_mgr().open_session(sid))
            self._logs[sid] = log_
            return log_

    async def _queue_for(self, sid: str, log_: Any) -> TaskQueue:
        """per-session TaskQueue(runner = per-session 真实引擎;真 LLM 已注册)。"""
        lock = self._queue_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            q = self._queues.get(sid)
            if q is None:
                runner = await self._engine_runner_for(sid, log_)
                q = TaskQueue(session=log_, runner=runner)
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
                attach_persistence=False)   # manager 已订阅落盘;重复订=双写卡死
            self._engines[sid] = spine
        if not getattr(spine, "_plugins_ready", False):
            await _eng._preload_plugins(spine)   # 示例插件预载(util.now 会话可用)
            spine._plugins_ready = True
        # 审批接线:engine ApprovalProvider → ctx.approval(桌面裁决端点
        # _provider_owning 经 ctx.approval 兜底定位;否则弹窗 A/B 打来 APR-503)
        if getattr(self.ctx, "approval", None) is None:
            self.ctx.approval = spine.approval
        if getattr(self.ctx, "guard", None) is None:
            self.ctx.guard = spine.guard
        # 预算门面随会话对齐(多会话共享 ctx:每次装配本会话时覆盖,保证
        # /api/budget/{sid} 读的是该会话自己的 counters)
        self.ctx.budget = spine.budget
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
                attach_persistence=False)
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
            if ctx_approval is not None and not self._approvals:
                return ctx_approval          # 装配注入的全会话 provider(单会话形态)
            provider = ApprovalProvider(session=log_, bus=None,
                                        config=_cfg_of(self.ctx))
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

    # ============================================================ 端点实现
    async def create_session(self) -> dict:
        sid = await self.service.create_session()
        return {"sid": sid}

    # ------------------------------------------------ jobs / schedule / subagent
    async def list_jobs(self, sid: str) -> dict:
        return await self.service.list_jobs(sid)

    async def start_job(self, sid: str, body: dict = Body(default={})) -> dict:
        job_id = await self.service.start_job(sid, (body or {}).get("intent"))
        return {"ok": True, "job_id": job_id}

    async def job_status(self, sid: str, job_id: str) -> dict:
        return await self.service.job_status(sid, job_id)

    async def cancel_job(self, sid: str, job_id: str) -> dict:
        cancelled = await self.service.cancel_job(sid, job_id)
        return {"ok": True, "cancelled": cancelled}

    async def list_schedules(self, sid: str) -> dict:
        return await self.service.list_schedules(sid)

    async def schedule_action(self, sid: str,
                              body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.schedule_action(
            sid, body.get("action"), name=body.get("name"),
            kind=body.get("kind") or "cron", expr=body.get("expr"),
            intent=body.get("intent"), is_risky=body.get("is_risky"))

    async def list_subagents(self, sid: str) -> dict:
        return await self.service.list_subagents(sid)

    async def spawn_subagent(self, sid: str,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        ratio = body.get("budget_ratio")
        sub_id = await self.service.spawn_subagent(
            sid, body.get("task"), tools_subset=body.get("tools_subset"),
            deny_extra=body.get("deny_extra"),
            budget_ratio=float(ratio) if ratio is not None else 0.25,
            notify=bool(body.get("notify", True)))
        return {"ok": True, "sub_id": sub_id}

    async def cancel_subagent(self, sid: str, sub_id: str) -> dict:
        cancelled = await self.service.cancel_subagent(sid, sub_id)
        return {"ok": True, "cancelled": cancelled}

    # -------------------------------------------------- 插件管理(#50/#51)
    def _plugin_roots(self) -> list[Path]:
        return self.service.plugin_roots()

    @staticmethod
    def _plug_ctx_for(spine: Any) -> Any:
        return ApplicationService._plug_ctx_for(spine)

    async def list_plugins(self) -> dict:
        return await self.service.list_plugins()

    async def plugin_load(self, body: dict) -> dict:
        return await self.service.plugin_action("load", body)

    async def plugin_unload(self, body: dict) -> dict:
        return await self.service.plugin_action("unload", body)

    async def plugin_reload(self, body: dict) -> dict:
        return await self.service.plugin_action("reload", body)

    # ---------------------------------------------- 权限档位(#39 动态切换)
    @staticmethod
    def _reset_preset_state(spine: Any) -> None:
        return ApplicationService._reset_preset_state(spine)

    async def set_preset(self, body: dict) -> dict:
        return await self.service.set_preset(body)

    # ---------------------------------------------- 技能管理(F073 桌面面)
    def _skills_mgr(self) -> Any:
        return self.service.skills_mgr()

    async def capabilities(self) -> dict:
        return self.service.capabilities()

    async def tenant_state(self) -> dict:
        return self.service.tenant_state()

    async def list_model_profiles(self) -> dict:
        return self.service.tenant_state()

    async def save_model_profile(self,
                                 body: dict = Body(default={})) -> dict:
        return self.service.save_model_profile(body or {})

    async def activate_model_profile(self, profile_id: str) -> dict:
        return self.service.activate_model_profile(profile_id)

    async def delete_model_profile(self, profile_id: str) -> dict:
        return self.service.delete_model_profile(profile_id)

    async def list_skills(self) -> dict:
        return await self.service.list_skills()

    async def skill_detail(self, name: str) -> dict:
        return await self.service.skill_detail(name)

    async def reload_skills(self, body: dict = None) -> dict:
        return await self.service.reload_skills()

    async def search_skills(self, q: str = "", registry_url: str = "") -> dict:
        return await self.service.search_skills(q, registry_url)

    async def install_skill(self, sid: str,
                            body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.install_skill(
            sid, str(body.get("name") or ""), version=str(body.get("version") or ""),
            registry_url=str(body.get("registry_url") or ""),
            approved_by="web-ui")

    async def rollback_skill(self, sid: str, name: str,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.rollback_skill(
            sid, name, str(body.get("version") or ""), approved_by="web-ui")

    async def remove_skill(self, sid: str, name: str,
                           body: dict = Body(default={})) -> dict:
        return await self.service.remove_skill(
            sid, name, approved_by="web-ui")

    async def skill_versions(self, name: str) -> dict:
        versions = self.service.skill_versions(name)
        return {"name": name, "versions": versions, "count": len(versions)}

    async def list_sessions(self) -> dict:
        return await self.service.list_sessions()

    async def delete_session(self, sid: str) -> dict:
        return await self.service.delete_session(sid)

    async def session_messages(self, sid: str, after_seq: int = 0) -> dict:
        return await self.service.session_messages(sid, after_seq)

    async def session_timeline(self, sid: str, after_seq: int = 0,
                               kinds: str = "") -> dict:
        return await self.service.session_timeline(sid, after_seq, kinds)

    async def event_detail(self, sid: str, seq: int) -> dict:
        return await self.service.event_detail(sid, seq)

    async def telemetry_report(self, sid: str) -> dict:
        return await self.service.telemetry_report(sid)

    async def stream_sse(self, request: Request, sid: str = "",
                         after_seq: int = 0) -> StreamingResponse:
        """SSE 事件流:注册 StreamClient(游标 after_seq)→ 总线事件按 seq 序 fan-out;
        断开 → 客户端带 last_seq 重连续拉(F065 边界);15s 心跳注释行保活。"""
        me = StreamClient(sid=str(sid or ""), tenant=self.current_tenant(),
                          last_seq=max(0, int(after_seq)))

        async def gen():
            # 注册生命周期必须覆盖响应体实际迭代期:ASGI/uvicorn 在视图函数返回后才
            # 流式读取 body,若像伪码那样在视图内 `async with register` 再 return,
            # __aexit__ 会在流开始前就把游标注销 → SSE 只见心跳收不到事件。故注册
            # 移入生成器内部:首个 anext 进入注册,生成器净退/断连/异常时 __aexit__
            # 自动注销(行为契约修正,偏离 6 同条)。
            async with self.hub.register(me):
                while not self.stopping.is_set():
                    if await request.is_disconnected():
                        break                       # 前端断开 → 回收游标
                    try:
                        item = await asyncio.wait_for(me.queue.get(),
                                                      timeout=SSE_HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"          # 心跳注释行(SSE 保活)
                        continue
                    if item is None:                # hub.close_all 哨兵 → 净退
                        break
                    type_, text = item
                    yield f"event: {type_}\ndata: {text}\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"})

    async def create_message(self, sid: str, body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.create_message(
            sid, body.get("text"), attachments=body.get("attachments"))

    async def _append_attachments(self, log_: Any, attachments: Any) -> None:
        for payload in self.service.attachment_payloads(attachments):
            await log_.append("user.attachment.image", payload, actor="user")

    @staticmethod
    def _attachment_payloads(attachments: Any) -> list[dict]:
        return ApplicationService.attachment_payloads(attachments)

    async def decide_approval_for(self, sid: str, aid: int,
                                  body: dict = Body(default={})) -> dict:
        return await self.service.decide_approval(
            int(aid), (body or {}).get("decision"), sid=sid)

    async def decide_approval(self, aid: int,
                              body: dict = Body(default={})) -> dict:
        return await self.service.decide_approval(
            int(aid), (body or {}).get("decision"))

    async def _decide_approval(self, aid: int, body: dict, *,
                               sid: Optional[str] = None) -> dict:
        return await self.service.decide_approval(
            int(aid), (body or {}).get("decision"), sid=sid)

    @staticmethod
    def _approval_sid_of(provider: Any, aid: int) -> str:
        return ApplicationService._approval_sid_of(provider, aid)

    def _provider_owning(self, aid: int, *, sid: Optional[str] = None) -> Any:
        return self.service.provider_owning(aid, sid=sid)

    # ------------------------------------------------ 编辑/重发/反馈/Webhook
    async def edit_message(self, sid: str, seq: int,
                           body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.edit_message(
            sid, int(seq), body.get("text"), resend=bool(body.get("resend")))

    async def resend_message(self, sid: str, seq: int) -> dict:
        return await self.service.resend_message(sid, int(seq))

    async def feedback(self, sid: str, seq: int,
                       body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.feedback(
            sid, int(seq), body.get("kind"), body.get("note") or "")

    async def webhook_ingest(self, body: dict = Body(default={}),
                             x_webhook_token: Optional[str] = Header(default=None),
                             authorization: Optional[str] = Header(default=None)) -> dict:
        body = body or {}
        self._webhook_authorize(body, self._api_token,
                                x_webhook_token=x_webhook_token,
                                authorization=authorization)
        text = str(body.get("content") or body.get("text") or "").strip()
        if not text:
            raise_code("EVT-100", advice="webhook content 不能为空")
        sid = str(body.get("session_id") or "")
        if not sid or sid not in self._logs:
            sid = await self.service.create_session()
        log_ = await self.service.require_session(sid)
        resend = await self.service.resend_text(log_, sid, text)
        return {"sid": sid, **resend}

    def _webhook_token(self) -> str:
        return self._api_token

    @staticmethod
    def _webhook_authorize(body: dict, expected: str, *,
                           x_webhook_token: Optional[str] = None,
                           authorization: Optional[str] = None) -> None:
        if not expected:
            return
        given = str(body.get("token") or x_webhook_token or "")
        if not given and authorization and authorization.lower().startswith("bearer "):
            given = authorization[7:].strip()
        if not given or not secrets.compare_digest(given, expected):
            raise_code("CRED-701", reason="webhook-token",
                       hint="webhook token 缺失或不匹配(拒绝注入)")

    @staticmethod
    def _last_env_of(log_: Any, type_: str):
        return ApplicationService._last_env_of(log_, type_)

    async def edit_last_user(self, sid: str,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.edit_last_user(
            sid, body.get("text"), resend=bool(body.get("resend")))

    async def resend_last_user(self, sid: str) -> dict:
        return await self.service.resend_last_user(sid)

    async def feedback_last_agent(self, sid: str,
                                  body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.feedback_last_agent(
            sid, body.get("kind"), body.get("note") or "")

    async def pending_approvals(self) -> dict:
        return await self.service.pending_approvals()

    def _ask_providers(self) -> list:
        return self.service.ask_providers()

    def _sid_of_ask(self, provider: Any) -> str:
        return self.service.sid_of_ask(provider)

    async def pending_asks(self) -> dict:
        return await self.service.pending_asks()

    async def answer_ask_for(self, sid: str, ask_id: int,
                             body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.answer_ask(
            int(ask_id), choice=body.get("choice"), text=body.get("text"), sid=sid)

    async def answer_ask(self, ask_id: int,
                         body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.answer_ask(
            int(ask_id), choice=body.get("choice"), text=body.get("text"))

    async def _answer_ask(self, ask_id: int, body: dict, *,
                          sid: Optional[str] = None) -> dict:
        return await self.service.answer_ask(
            int(ask_id), choice=body.get("choice"), text=body.get("text"), sid=sid)

    async def budget_dashboard(self, sid: str) -> dict:
        return await self.service.budget_dashboard(sid)

    async def upload_attachment(self, body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.upload_attachment(
            body.get("sid"), body.get("attachment", {}))

    async def run_workflow(self, sid: str,
                           body: dict = Body(default={})) -> dict:
        body = body or {}
        return await self.service.run_workflow(
            sid, body.get("steps"), name=body.get("name") or "desktop",
            stop_on_fail=bool(body.get("stop_on_fail")))

    # ============================================================ 优雅停服
    def shutdown_gracefully(self) -> None:
        """窗口关闭 = 优雅停服(F065 边界;幂等:双击关闭只走一次)。

        序:stopping 置位 → 停事件 fan-out → hub 关闭(SSE 客户端收 close)→ 兜底 flush
        (强同步事件已随 append 落盘)→ server.should_exit=True 停 uvicorn → webview.destroy。
        幂等判据用 _graceful_done(伪码 `if stopping.is_set(): return` 会在 closing 事件
        先置位时让首次 graceful 空转——伪码缺陷,以行为契约修正,见模块 docstring)。
        """
        if self._graceful_done:
            return
        self._graceful_done = True
        self.stopping.set()
        loop = self.loop
        if loop is not None and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
                fut.result(timeout=15)
            except Exception:                       # noqa: BLE001
                log.warning("desktop async shutdown failed", exc_info=True)
        else:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self._shutdown_async())
            else:
                # 已处于另一个事件循环(通常仅启动失败路径):用短线程隔离执行。
                errors: list[BaseException] = []

                def _worker() -> None:
                    try:
                        asyncio.run(self._shutdown_async())
                    except BaseException as exc:    # noqa: BLE001
                        errors.append(exc)

                t = threading.Thread(target=_worker, name="desktop-shutdown", daemon=True)
                t.start()
                t.join(timeout=15)
                if errors:
                    log.warning("desktop shutdown worker failed", exc_info=errors[0])
        self._destroy_webview()

    async def _shutdown_async(self) -> None:
        """在 uvicorn 事件循环内完成 fan-out 摘除、刷盘和服务器退出信号。"""
        bus = getattr(self.ctx, "bus", None)
        if bus is not None and self._sub:
            try:
                bus.unsubscribe_all("desktop")      # 停事件 fan-out
            except Exception:                       # noqa: BLE001
                log.warning("desktop shutdown unsubscribe failed", exc_info=True)
        self._sub = None
        self.hub.close_all()                        # SSE 客户端收 close,前端可重开
        await self._flush_persistence_async()       # 必须等待 pending 攒批落盘
        for service in self._service_registry.all():
            try:
                await service.shutdown()
            except Exception:                       # noqa: BLE001
                log.warning("desktop tenant service shutdown failed", exc_info=True)
        if self.server is not None:
            try:
                self.server.should_exit = True      # uvicorn 线程自然退出
            except Exception:                       # noqa: BLE001
                log.warning("desktop server stop signal failed", exc_info=True)
    async def _flush_persistence_async(self) -> None:
        """异步收尾:等待 flush 完成后才允许调用方关闭 store。"""
        persist = getattr(self.ctx, "persistence", None)
        if persist is not None:
            flush_sync = getattr(persist, "flush_sync", None)
            fn = flush_sync if callable(flush_sync) else getattr(persist, "flush", None)
            if fn is not None:
                try:
                    await _await(fn())
                except Exception:                       # noqa: BLE001 兜底失败不阻断退出
                    log.warning("desktop 收尾 flush 失败", exc_info=True)
        surface = getattr(self, "_surface", None)
        if isinstance(surface, DesktopSessionManager):
            try:
                await surface.shutdown_all()
            except Exception:                           # noqa: BLE001
                log.warning("desktop session shutdown failed", exc_info=True)
        spines = list(getattr(self, "_engines", {}).values())
        ctx_spine = getattr(self.ctx, "engine_spine", None)
        if ctx_spine is not None and all(s is not ctx_spine for s in spines):
            spines.append(ctx_spine)
        for spine in spines:
            try:
                await spine.close()
            except Exception:                           # noqa: BLE001
                log.warning("desktop engine close failed", exc_info=True)

    @staticmethod
    def _destroy_webview() -> None:
        """webview.destroy 兜底(已销毁则忽略,本地异常不跨边界阻止进程退出)。"""
        try:
            from .launcher import _load_webview
            wv = _load_webview()
            if wv is not None:
                wv.destroy()
        except Exception:                           # noqa: BLE001
            log.debug("webview destroy skipped", exc_info=True)


