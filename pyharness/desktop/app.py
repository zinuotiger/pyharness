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

# 路径中的会话 id(供租户服务端派生:以会话注册租户为准,不信客户端自报头)
# 字符集必须与 SessionLog 的 sid 合法面一致(sessions.validate_session_id =
# `s-[A-Za-z0-9._-]{6,64}`)。**原为 `[0-9a-zA-Z]+`,不含 `-`/`.`** ⇒ fork 会话
# (`s-fork-<hex>`,cli.py 生成)只匹配到 `s-fork` 前缀 → 查不到租户 → 静默回落
# 客户端头。故此处放宽为 `[0-9A-Za-z._-]+`(`-` 置末为字面量)。
_SESSION_ID_IN_PATH = re.compile(
    r"/(?:sessions|budget|approvals|asks)/(s-[0-9A-Za-z._-]+)")

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

if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    from .bridge import DesktopBridge

log = logging.getLogger("pyharness.desktop.app")


class LoopbackHostMiddleware:
    """Strict HTTP authority check including bracketed IPv6; no DNS rebinding."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            hosts = [value.decode('latin-1') for key,value in scope.get('headers', []) if key.lower() == b'host']
            match = re.fullmatch(r'(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?', hosts[0], re.I) if len(hosts) == 1 else None
            if match is None or (match.group(1) and not 0 < int(match.group(1)) <= 65535):
                if scope['type'] == 'websocket':
                    await send({'type':'websocket.close', 'code':1008})
                else:
                    await PlainTextResponse('Invalid host header', status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)

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
        self.api.add_middleware(LoopbackHostMiddleware)
        self._api_token = self._resolve_api_token()
        self._tstore: Optional[TenantSettingsStore] = None   # 租户令牌库(惰性)
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

    def _candidate_session_dirs(self) -> list[Path]:
        """会话可能落盘的**全部权威目录** = 默认 sessions 目录 + `<root>/tenants/*/sessions`。

        与 ``ApplicationService._tenant_sessions_dir`` 的派生一致(default 用装配的
        ``storage.sessions_dir``,其余租户用 ``<storage.root>/tenants/<t>/sessions``)。
        这里只做**枚举**,不做归属判定 —— "同源规则多份实现必然漂移",故派生点唯一。
        """
        dirs: list[Path] = []
        try:
            base = _sessions_dir_of(self.ctx)
            if base:
                dirs.append(Path(str(base)))
        except (TypeError, ValueError):
            pass
        try:
            root = Path(str(_cfg_of(self.ctx).storage.root)).expanduser()
            tdir = root / "tenants"
            if tdir.is_dir():
                dirs.extend(sorted(d / "sessions" for d in tdir.iterdir()
                                   if d.is_dir()))
        except (AttributeError, TypeError, ValueError, OSError):
            pass
        return dirs

    def _disk_owners(self, sid: str) -> tuple[set, bool]:
        """磁盘上的**权威归属** → ``(租户集合, 是否存在"归属不可得"的会话文件)``。

        第二项是 fail-closed 的枢轴:文件**在盘**但读不出租户(GAP-10 缺失/坏行)
        ⇒ 归属**未知**,调用方必须拒绝;绝不能把"读不出归属"混同于"会话不存在"
        (后者才允许回落客户端自报头)。
        """
        owners: set = set()
        unknown = False
        for d in self._candidate_session_dirs():
            f = d / f"{sid}.jsonl"
            try:
                if not f.is_file():
                    continue
            except OSError:
                unknown = True               # 目录不可读 ⇒ 无法断言归属
                continue
            t = tenant_of_log(f)
            if t:
                owners.add(t)
            else:
                unknown = True                # 会话在盘但未载租户 ⇒ 归属未知
        return owners, unknown

    @staticmethod
    def _request_sid(request: Request) -> str:
        """本请求指向的**会话 id**:路径段优先,其次 ``?sid=``(SSE 流式面)。

        SSE 把 sid 放在查询串(`/api/stream?sid=…`)⇒ 只认路径会**整条绕过**归属
        判定,流式面就成了"声明即授权"(实测:声明任意租户即可订阅该租户会话事件)。
        查询参数按 ``validate_session_id`` 同款正则校验,避免把普通查询参数误判为 sid。
        """
        m = _SESSION_ID_IN_PATH.search(request.url.path)
        if m:
            return m.group(1)
        q = str(request.query_params.get("sid") or "")
        return q if _SID_RE.fullmatch(q) else ""

    def resolve_request_tenant(self, request: Request) -> Optional[str]:
        """解析本请求的**服务端权威**租户;不可得 → ``None``(调用方须 fail-closed)。

        **不变式(2026-09-21 R31-1)**:客户端自报头只声明"我要访问哪个租户的**分区**",
        它**绝不决定一个已存在会话的归属**,服务端也**绝不据此把请求改判/重定向到
        另一个租户**。解析序:

          ① 无会话语境(路径与查询都没有 sid) ⇒ 头即声明(既有语义);
          ② 会话的权威归属 = 进程内认领集合 ∪ 全部权威目录的落盘事实;
          ③ **归属不可得**(文件在盘但未载租户)或**归属歧义**(多租户同名 sid)
             ⇒ ``None``(**拒绝**);
          ④ 归属确定:声明与归属**相同** ⇒ 放行;**不同** ⇒ ``None``(拒绝);
          ⑤ 任何权威来源都查无此会话 ⇒ 回落声明(该路由只会 404/EVT-106,无数据可泄)。

        为什么"不一致即拒"而不是"改判为真实归属":改判等于把**声明了 A 的请求交给
        B 的租户服务作答** ⇒ 未声明 B 的客户端读到 B 的会话正文。两版实现均可复现:
        HEAD 版在 default 分区(即全局 sessions 目录)按目录归属放行,不认信封租户;
        本轮前版(按权威目录改判)更彻底 —— **任意头甚至不带头**都返回真实归属的数据
        (实测 `header=alpha/default/无` 于 beta 会话一律 200 + `BETA-ONLY`)。
        **鉴权不由本函数负责**:R31-2 在中间件里先行把关(``_tenant_authorized``),
        本函数只回答"这个会话的**权威归属**是谁",不回答"该主体**能否**用这个租户"。
        """
        declared = self._declared_tenant(request)
        sid = self._request_sid(request)
        if not sid:
            return declared                      # ① 无会话语境:头即声明
        owners, unknown = self._disk_owners(sid)
        owners |= session_tenants(sid)           # ② 进程内认领(仅作**归属来源**)
        if unknown or len(owners) > 1:
            return None                          # ③ 归属不可得 / 歧义 ⇒ 拒
        if not owners:
            return declared                      # ⑤ 查无此会话(路由只会 404)
        return declared if declared in owners else None   # ④ 声明≠权威归属 ⇒ 拒

    # ------------------------------------------------ 租户鉴权(R31-2)
    def bootstrap_url(self, host: str, port: int) -> str:
        """壳加载 UI 的 URL(**带操作者令牌**,R31-2)。

        ``/`` 不再无条件发放全局令牌,故壳必须自证持有它。令牌走 URL 查询串而非页面
        meta:它随即被换成 HttpOnly cookie(JS 不可读、不进 DOM/日志),与既有 P1-2
        口径一致。URL 构造收在此处,供两处启动路径共用(免第二份实现漂移)。
        """
        authority = f'[{host}]' if ':' in host else host
        base = f"http://{authority}:{port}/"
        return f"{base}?token={self._api_token}" if self._api_token else base

    @staticmethod
    def _declared_tenant(request: Request) -> str:
        """客户端**声明**的租户(头优先,其次 ``?tenant=``,缺省 ``default``)。"""
        return normalize_tenant_id(
            request.headers.get("x-pyharness-tenant")
            or request.query_params.get("tenant") or "default")

    def _tenant_store(self) -> Optional[TenantSettingsStore]:
        """租户令牌库;派生点与 ``ApplicationService`` 同为 ``tenants_dir(root)``。

        拿不到 ``storage.root``(鸭子类型 ctx / 无配置)⇒ ``None`` ⇒ 调用方按
        **无法校验** 处理(fail-closed),而不是回落放行。
        """
        if self._tstore is None:
            root = self._storage_root()
            if root is None:
                return None                 # fail-closed:无 root 即无法校验
            self._tstore = TenantSettingsStore(tenants_dir(root))
        return self._tstore

    def _verify_tenant_token(self, tenant: str, given: str) -> bool:
        """校验租户令牌;无令牌库 ⇒ ``False``(**fail-closed**)。"""
        store = self._tenant_store()
        return bool(store) and store.verify_token(tenant, given)

    @staticmethod
    def _presented_token(request: Request) -> str:
        """请求出示的**全局**令牌(头 / Bearer / cookie / ``?token=``)。"""
        hdrs = getattr(request, "headers", None) or {}
        given = hdrs.get("x-pyharness-token") or ""
        auth = hdrs.get("authorization") or ""
        if not given and auth.lower().startswith("bearer "):
            given = auth[7:].strip()
        if not given:
            given = (getattr(request, "cookies", None) or {}).get(
                "pyharness_token", "") or ""
        if not given:
            given = str((getattr(request, "query_params", None) or {}).get("token") or "")
        return str(given)

    @staticmethod
    def _presented_tenant_token(request: Request) -> str:
        """请求出示的**租户**令牌:头 / 同名 cookie / ``?token=``(壳加载 URL)。"""
        hdrs = getattr(request, "headers", None) or {}
        cookies = getattr(request, "cookies", None) or {}
        qp = getattr(request, "query_params", None) or {}
        return str(hdrs.get("x-pyharness-tenant-token")
                   or cookies.get("pyharness_tenant_token")
                   or qp.get("token") or "")

    def _operator_ok(self, request: Request) -> bool:
        """是否持有全局(操作者)令牌;未配置令牌时视为放行(沿用既有语义)。"""
        expected = self._api_token
        if not expected:
            return True
        given = self._presented_token(request)
        return bool(given) and secrets.compare_digest(given, expected)

    def _tenant_authorized(self, request: Request, tenant: str) -> bool:
        """**鉴权(R31-2)**:声明 ``default`` 沿用全局令牌(路由依赖再强制一次);
        声明非 default ⇒ 必须出示**该租户**的令牌。

        为什么 default 放行:它是**兼容口** —— 桌面壳与既有客户端只持全局令牌,
        若 default 也要租户令牌,引导页与全部既有流程即刻断。收益全在非 default:
        此前任何人只要**报对租户名**即可进入(只有归属校验 R31-1,没有资格校验)。
        """
        if tenant == "default":
            return True
        return self._verify_tenant_token(
            tenant, self._presented_tenant_token(request))

    async def _tenant_middleware(self, request: Request, call_next: Any) -> Any:
        # ① 鉴权先行:未出示 / 出示错误的本租户令牌 ⇒ 401。**先于**归属判定,故不会向
        #    未授权方泄露"该租户下是否存在某会话"(403 与 404 之差即是信息)。
        declared = self._declared_tenant(request)
        # 引导页 ``/`` **自带**更细的凭证规则(持操作者令牌者可铸所声明租户的令牌),
        # 故此处放行 —— 否则中间件先 401,铸令牌那条路径根本到不了(实测)。
        if request.url.path != "/" and not self._tenant_authorized(request, declared):
            return JSONResponse(status_code=401, content={
                "code": "CRED-701",
                "advice": f"租户 {declared} 需要该租户的 API 令牌"
                          "(X-PyHarness-Tenant-Token,或 /?tenant= 下发的 cookie);"
                          "default 租户沿用全局令牌"})
        # ② 归属判定(R31-1):不可得 / 歧义 / 与声明不符 ⇒ 403
        tenant = self.resolve_request_tenant(request)
        if tenant is None:
            # 归属不可得 / 歧义 / 声明与权威归属不符 ⇒ **拒绝**;一律不按客户端自报头
            # 放行,也不改判到别的租户(防伪造头跨租户读取,L-1/R31-1)
            return JSONResponse(status_code=403, content={
                "code": "GRD-401",
                "advice": "会话租户归属不可得、存在歧义,或与请求声明的租户不符;"
                          "拒绝服务(既不按客户端自报头判定,也不跨租户改判)"})
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
    def _storage_root(self) -> Optional[Path]:
        """``storage.root``;鸭子类型 ctx / 无配置 ⇒ ``None``(调用方 fail-closed)。"""
        try:
            return Path(str(_cfg_of(self.ctx).storage.root)).expanduser()
        except (AttributeError, TypeError, ValueError):
            return None

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

    def _resolve_api_token(self) -> str:
        """API token:配置 shell.web.token(env:/file: 引用)优先,缺省进程级随机令牌。

        同时记录 ``_api_token_generated``:随机态下该令牌**无处可查**,须由
        :meth:`publish_operator_token` 落盘,否则浏览器/外部客户端无从取得。
        """
        raw = ""
        cfg = _cfg_of(self.ctx)
        try:
            web = getattr(getattr(cfg, "shell", None), "web", None)
            raw = str(getattr(web, "token", None) or "")
        except Exception:                                # noqa: BLE001 非 Settings 形状
            raw = ""
        val = _resolve_secret_value(raw)
        self._api_token_generated = not bool(val)
        return val if val else secrets.token_hex(16)

    def _require_api_auth(self, request: Request,
                          x_pyharness_token: Optional[str] = Header(default=None),
                          authorization: Optional[str] = Header(default=None)) -> None:
        """读/写端点统一鉴权依赖(P1-2 收紧):令牌缺失/不匹配 → CRED-701(401)。

        令牌来源:① X-PyHarness-Token / Authorization: Bearer 头(pywebview 桥/
        测试/外部客户端);② pyharness_token HttpOnly cookie(_index_page 下发,
        JS 不可读、同源请求自动携带)。仅索引页 / 与 webhook 入站端点免鉴权
        (引导/外联);全部数据端点(含读端与 SSE)强制鉴权——此前 39 个读端点
        匿名、SSE 无鉴权,任何本地进程可枚举全部会话。
        """
        expected = self._api_token
        if not expected:
            return
        given = x_pyharness_token or ""
        if not given and authorization and authorization.lower().startswith("bearer "):
            given = authorization[7:].strip()
        if not given:
            given = (request.cookies or {}).get("pyharness_token", "") or ""
        ok = bool(given) and secrets.compare_digest(given, expected)
        if not ok:
            # ``R31-2``:非 default 租户可用**该租户令牌**通过本依赖。租户**授权**已在
            # 中间件强制(它先跑);此处只是第二层,不代替它 —— 只有租户令牌、没有全局
            # 令牌的浏览器因此可用,而它拿不到全局令牌,故换不到别的租户的 cookie。
            tenant = self.current_tenant()
            ok = (tenant != "default"
                  and self._verify_tenant_token(
                      tenant, self._presented_tenant_token(request)))
        if not ok:
            raise_code("CRED-701", reason="api-token",
                       hint="缺少或非法 API token"
                            "(X-PyHarness-Token / Bearer / pyharness_token cookie;"
                            "非 default 租户亦可出示该租户令牌)")

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
    def mount_api(self) -> None:
        """注册全部端点(只读投影 + 引擎门面写 + SSE)+ PyHError 统一错误体(ADR-011)。"""
        a = self.api
        a.add_api_route("/", self._index_page, methods=["GET"])
        a.add_api_route("/api/sessions", self.create_session, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions", self.list_sessions, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}", self.delete_session,
                        methods=["DELETE"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages", self.session_messages,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages", self.create_message,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/timeline", self.session_timeline,
                        methods=["GET"],                            # 轨迹(核心)
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/event/{seq}", self.event_detail,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/telemetry", self.telemetry_report,
                        methods=["GET"],            # F066 审计导出(桌面面)
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/governance-audit",
                        self.governance_audit,
                        methods=["GET"],            # GAP-7 治理因果审计面
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/governance-evidence",
                        self.governance_evidence,
                        methods=["GET"],            # GAP-8 证据归档面
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/stream", self.stream_sse, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])  # SSE
        a.add_api_route("/api/approvals/pending", self.pending_approvals,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/approvals/{sid}/{aid}", self.decide_approval_for,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/approvals/{aid}", self.decide_approval,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 审批弹窗裁决
        a.add_api_route("/api/asks/pending", self.pending_asks,
                        methods=["GET"],                              # #38 反问轮询
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/asks/{sid}/{ask_id}", self.answer_ask_for,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/asks/{ask_id}", self.answer_ask,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # #38 反问答复
        a.add_api_route("/api/plugins", self.list_plugins,
                        methods=["GET"],                              # 插件列表
                        dependencies=[Depends(self._require_api_auth)])
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
                        methods=["GET"],                              # 双壳能力契约
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/tenant", self.tenant_state, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/settings/models", self.list_model_profiles,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
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
                        methods=["GET"],                              # 技能目录
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills/registry/search", self.search_skills,
                        methods=["GET"],                              # Registry 搜索
                        dependencies=[Depends(self._require_api_auth)])
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
                        methods=["GET"],                              # 已装版本
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills/{name}", self.skill_detail,
                        methods=["GET"],                              # 技能正文
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/budget/{sid}", self.budget_dashboard, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/attachments", self.upload_attachment, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/workflow", self.run_workflow,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        # ------------------------------------------------------------ 编排管理面
        a.add_api_route("/api/sessions/{sid}/jobs", self.list_jobs,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/jobs", self.start_job,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/jobs/{job_id}", self.job_status,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/jobs/{job_id}/cancel",
                        self.cancel_job, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/schedules", self.list_schedules,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/schedules/action",
                        self.schedule_action, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/subagents", self.list_subagents,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
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
    def _index_page(self, request: Request) -> HTMLResponse:
        """根路由:加载内嵌前端页(会话列表/对话/轨迹时间线/审批/预算)。

        前端文件打包为 data 资源:源码运行读 pyharness/ui/index.html;PyInstaller
        打包时 --add-data 携带同相对路径(_MEIPASS 下亦命中);缺失 → 兜底提示页。
        API token 不再明文注入页面 meta(P1-2 收紧,防 DOM/日志泄漏):改 HttpOnly
        cookie 下发,JS 不可读、同源 fetch/SSE 自动携带。

        ``R31-2``:本页**不再无条件发放**全局令牌 —— 它此前是"任何本机进程 GET / 即
        得令牌"的免费发放点,而有了租户令牌后,拿到全局令牌即可换取**任意租户**的
        cookie,授权面当场作废。现在必须出示凭证之一:全局(操作者)令牌,或**所声明
        租户**的租户令牌。声明了非 default 租户且校验通过时,一并下发该租户 cookie,
        前端无需改动即可继续用 ``X-PyHarness-Tenant`` 走查(租户令牌随 cookie 自动
        携带)。凭证既可走头/cookie,也可走 ``?token=``(壳加载 URL 用)。

        ``R31-2 创建路径``:**持操作者令牌 + 声明某租户 ⇒ 铸/取该租户令牌**。
        没有这一条,非 default 租户既无令牌可出示、又无任何生产路径可生成
        (``api_token()`` 在别处零调用)⇒ 该租户**永远 401**(UI 验证实测:租户
        边界用例只能靠手工创建令牌文件才能跑起来)。
        这**不放宽**边界:操作者令牌本就等同"同用户文件访问"这一既成信任级
        (能读 ``~/.pyharness/web.token`` 者本就能直读全部会话与租户令牌);
        租户令牌的价值仍在**可外发的最小凭证** —— 可单独交给外部客户端,
        而它**换不到**别的租户(见 :meth:`_tenant_authorized`)。
        """
        declared = self._declared_tenant(request)
        tenant_tok = self._presented_tenant_token(request)
        operator = self._operator_ok(request)
        tenant_ok = (declared != "default"
                     and self._verify_tenant_token(declared, tenant_tok))
        if operator and declared != "default":
            store = self._tenant_store()        # 操作者 ⇒ 可铸所声明租户的令牌
            if store is not None:
                tenant_tok = store.api_token(declared)
                tenant_ok = True
        if not (operator or tenant_ok):
            return JSONResponse(status_code=401, content={
                "code": "CRED-701",
                "advice": "引导页需要凭证:全局 API token,或所声明租户的租户令牌"
                          "(?token=/头/cookie);见 docs/CFG.md 租户模型设置"})
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
        resp = HTMLResponse(html)
        if self._api_token and operator:
            resp.set_cookie("pyharness_token", self._api_token,
                            httponly=True, samesite="strict", path="/")
        if tenant_ok:
            resp.set_cookie("pyharness_tenant_token", tenant_tok,
                            httponly=True, samesite="strict", path="/")
        return resp

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
        errors = []
        try: await self._flush_persistence_async()
        except BaseException as exc: errors.append(exc)
        for service in self._service_registry.all():
            try:
                await service.shutdown()
            except BaseException as exc:
                errors.append(exc)
        if self.server is not None:
            try:
                self.server.should_exit = True      # uvicorn 线程自然退出
            except Exception:                       # noqa: BLE001
                log.warning("desktop server stop signal failed", exc_info=True)
        if errors: raise BaseExceptionGroup('desktop cleanup failed', errors)

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
        # This helper flushes only. ApplicationService owns writer shutdown.
        surface = getattr(self, '_surface', None)
        for store in list(getattr(surface, '_stores', {}).values()):
            await _await(store.flush())

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


