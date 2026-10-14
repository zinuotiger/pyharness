"""DesktopAuthority operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
import re
import secrets
import stat
from pathlib import Path
from typing import Any, Optional
from fastapi import Header, Request
from fastapi.responses import JSONResponse
from pyharness.core.tenant_settings import TenantSettingsStore, normalize_tenant_id, session_tenants, tenant_of_log, tenants_dir
from pyharness.errors import raise_code
from .auth_cookies import read_cookie
from .sessions import _SID_RE, _cfg_of, _resolve_secret_value, _sessions_dir_of
from pyharness.desktop.host_policy import _SESSION_ID_IN_PATH
if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    pass

class DesktopAuthority:
    """DesktopAuthority responsibility; state is owned by the composing facade."""

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
        from .accounts_api import principal
        account = principal(request)
        if account:
            return account['tenant']
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
            given = read_cookie(request, "pyharness_token")
        if not given:
            given = str((getattr(request, "query_params", None) or {}).get("token") or "")
        return str(given)

    @staticmethod
    def _presented_tenant_token(request: Request) -> str:
        """请求出示的**租户**令牌:头 / 同名 cookie / ``?token=``(壳加载 URL)。"""
        hdrs = getattr(request, "headers", None) or {}
        qp = getattr(request, "query_params", None) or {}
        return str(hdrs.get("x-pyharness-tenant-token")
                   or read_cookie(request, "pyharness_tenant_token")
                   or qp.get("token") or "")

    def _operator_ok(self, request: Request) -> bool:
        """是否持有全局(操作者)令牌;未配置令牌时视为放行(沿用既有语义)。"""
        from .accounts_api import principal
        if principal(request):
            return False
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
        from .accounts_api import principal
        account = principal(request)
        if account:
            return account['tenant'] == tenant
        if tenant == "default":
            return True
        return self._verify_tenant_token(
            tenant, self._presented_tenant_token(request))

    async def _tenant_middleware(self, request: Request, call_next: Any) -> Any:
        # The only public mutation is an independently signed Feishu callback.
        # It cannot select a tenant or reach any management/API endpoint.
        if getattr(request,'method','GET') == 'POST' and re.fullmatch(r'/hooks/feishu/[a-z][a-z0-9_]{1,30}', request.url.path):
            return await call_next(request)
        from .accounts_api import inspect_request, principal
        rejected = inspect_request(self, request)
        if rejected is not None:
            return rejected
        if request.url.path.startswith('/api/account/') or request.url.path == '/login':
            return await call_next(request)
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
        from pyharness.application.accounts import current_actor, public_actor
        actor_token = current_actor.set(public_actor(principal(request)))
        try:
            from .accounts_api import serve_with_quota
            return await serve_with_quota(self, request, call_next)
        finally:
            current_actor.reset(actor_token)
            self._tenant_var.reset(token)

    def _storage_root(self) -> Optional[Path]:
        """``storage.root``;鸭子类型 ctx / 无配置 ⇒ ``None``(调用方 fail-closed)。"""
        try:
            return Path(str(_cfg_of(self.ctx).storage.root)).expanduser()
        except (AttributeError, TypeError, ValueError):
            return None


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
        from .accounts_api import principal
        if principal(request):
            return
        expected = self._api_token
        if not expected:
            return
        given = x_pyharness_token or ""
        if not given and authorization and authorization.lower().startswith("bearer "):
            given = authorization[7:].strip()
        if not given:
            given = read_cookie(request, "pyharness_token")
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

