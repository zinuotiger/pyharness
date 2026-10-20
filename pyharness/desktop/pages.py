"""DesktopPages operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
import asyncio
from typing import Any, Optional
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pyharness.errors import PyHError
from .auth_cookies import cookie_name
from .constants import _STATUS_FOR_CODE
from .projection import redact
from .sessions import _await, validate_session_id
from pyharness.desktop.host_policy import log
if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    pass

class DesktopPages:
    """DesktopPages responsibility; state is owned by the composing facade."""

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
        if getattr(getattr(request, 'state', None), 'operator_bootstrap', False):
            from fastapi.responses import RedirectResponse
            from .accounts_api import COOKIE
            response = RedirectResponse('/', status_code=303, headers={
                'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
            response.set_cookie(cookie_name(request, 'pyharness_token'), self._api_token,
                                httponly=True, samesite='strict', path='/')
            response.delete_cookie(cookie_name(request, COOKIE), path='/')
            response.delete_cookie(cookie_name(request, 'pyharness_tenant_token'), path='/')
            return response
        declared = self._declared_tenant(request)
        tenant_tok = self._presented_tenant_token(request)
        operator = self._operator_ok(request)
        from .accounts_api import principal, operator_principal
        account = principal(request)
        tenant_ok = (declared != "default"
                     and self._verify_tenant_token(declared, tenant_tok))
        if operator and declared != "default":
            store = self._tenant_store()        # 操作者 ⇒ 可铸所声明租户的令牌
            if store is not None:
                tenant_tok = store.api_token(declared)
                tenant_ok = True
        if not (operator or tenant_ok or account):
            if 'text/html' in request.headers.get('accept', ''):
                from fastapi.responses import RedirectResponse
                return RedirectResponse('/login', status_code=303)
            return JSONResponse(status_code=401, content={
                "code": "CRED-701",
                "advice": "引导页需要凭证:全局 API token,或所声明租户的租户令牌"
                          "(?token=/头/cookie);见 docs/CFG.md 租户模型设置"})
        page = 'index.html' if getattr(getattr(request, 'url', None), 'path', '/') == '/classic' else 'platform.html'
        try:
            import importlib.resources as _ir
            html = _ir.files("pyharness.ui").joinpath(page).read_text(
                encoding="utf-8")
        except Exception:                       # noqa: BLE001 资源缺失兜底
            from pathlib import Path as _P
            p = _P(__file__).resolve().parent.parent / "ui" / page
            try:
                html = p.read_text(encoding="utf-8")
            except Exception:                   # noqa: BLE001
                html = ("<html><body style='background:#0f1420;color:#dbe4f5;"
                        "font-family:sans-serif;display:flex;align-items:center;"
                        "justify-content:center;height:100vh'>"
                        "<div><h2>PyHarness Desktop</h2>"
                        "<p>前端资源缺失(ui/index.html 未随包携带)</p></div></body></html>")
        resp = HTMLResponse(html, headers={'Cache-Control': 'no-store',
                                          'Referrer-Policy': 'no-referrer'})
        if self._api_token and operator and not operator_principal(request):
            resp.set_cookie(cookie_name(request, "pyharness_token"), self._api_token,
                            httponly=True, samesite="strict", path="/")
        if tenant_ok and not account:
            resp.set_cookie(cookie_name(request, "pyharness_tenant_token"), tenant_tok,
                            httponly=True, samesite="strict", path="/")
        return resp

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

    async def _appearance_asset(self, asset_path: str):
        """Only package-owned appearance resources, never application data."""
        from importlib.resources import files
        from fastapi.responses import Response
        parts = asset_path.split('/')
        if (not parts or parts[0] not in {'theme-kit','host-adapters'}
                or any(not p or p in {'.','..'} or '\\' in p or ':' in p for p in parts)):
            return Response(status_code=404)
        suffix=parts[-1].rsplit('.',1)[-1]
        mime={'js':'text/javascript','css':'text/css','json':'application/json','png':'image/png'}.get(suffix)
        if mime is None:return Response(status_code=404)
        try:
            raw=files('pyharness.ui').joinpath(*parts).read_bytes()
        except (OSError,ValueError):
            return Response(status_code=404)
        return Response(raw,media_type=mime,headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-cache'})

