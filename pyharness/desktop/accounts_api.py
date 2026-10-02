"""Account HTTP boundary layered onto the existing tenant service registry."""
from __future__ import annotations

import asyncio
import json
import re
from fastapi import Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pyharness.application.accounts import Accounts, AccountError

COOKIE = 'pyharness_account'
SESSION_QUOTA = 100
ACTIVE_RUN_QUOTA = 20
ARTIFACT_BYTES_QUOTA = 100 * 1024 * 1024


def store(app):
    root = app._storage_root()
    if root is None: raise AccountError('accounts_unavailable',503)
    if not hasattr(app, '_accounts'):
        app._accounts = Accounts(root)
    return app._accounts


def principal(request):
    return getattr(getattr(request, 'state', None), 'account', None)


def denied(code='account_forbidden', status=403):
    return JSONResponse({'code':code},status_code=status)


async def serve_with_quota(app, request, call_next):
    account = principal(request)
    if not account or request.method not in ('POST','PUT'):
        return await call_next(request)
    locks = getattr(app, '_account_quota_locks', None)
    if locks is None: locks = app._account_quota_locks = {}
    async with locks.setdefault(account['tenant'], asyncio.Lock()):
        path = request.url.path
        platform = app.service.platform
        if path.startswith(('/api/v1/runs','/api/v1/workflows')) and not path.endswith('/cancel'):
            if not app.service.tenant_state()['active']:
                return denied('tenant_model_profile_required',409)
        if path.startswith('/api/v1/agents') and request.method in ('POST','PUT'):
            try:
                raw = await request.body()
                body = json.loads(raw) if raw else {}
            except (ValueError, UnicodeDecodeError):
                return denied('invalid_request',422)
            if not isinstance(body,dict):
                return denied('invalid_request',422)
            profile = body.get('sandbox_profile') or {}
            if isinstance(profile,dict) and profile.get('mode') == 'host_approved':
                return denied('account_host_execution_forbidden')
            if body.get('mcp_connections') or body.get('plugins'):
                return denied('account_host_execution_forbidden')
        if path in ('/api/v1/sessions','/api/v1/runs','/api/v1/schedules','/api/v1/workflows'):
            sessions = (await app.service.list_sessions())['sessions']
            if len(sessions) >= SESSION_QUOTA:
                if path == '/api/v1/sessions': return denied('session_quota',429)
                body = await request.json()
                if not body.get('session_id'): return denied('session_quota',429)
        if path == '/api/v1/runs' or (path.startswith('/api/v1/runs/') and path.endswith('/retry')):
            runs = (await platform.runs())['runs']
            if sum(r['status'] in ('running','queued','waiting_approval','paused') for r in runs) >= ACTIVE_RUN_QUOTA:
                return denied('active_run_quota',429)
        if path == '/api/v1/artifacts':
            artifacts = (await platform.artifacts())['artifacts']
            body = await request.json()
            encoded = body.get('data_base64','')
            if not isinstance(encoded,str): return denied('invalid_artifact',422)
            estimate = len(encoded) * 3 // 4
            if sum(a['size'] for a in artifacts) + estimate > ARTIFACT_BYTES_QUOTA:
                return denied('artifact_quota',429)
        return await call_next(request)


def inspect_request(app, request):
    """Resolve the server-owned identity before legacy tenant routing."""
    token = (getattr(request, 'cookies', None) or {}).get(COOKIE)
    if token:
        account = store(app).authenticate(token)
        if account is None:
            if request.url.path == '/' and 'text/html' in request.headers.get('accept', ''):
                from fastapi.responses import RedirectResponse
                return RedirectResponse('/login', status_code=303)
            if request.url.path not in ('/login','/api/account/login','/api/account/logout'):
                return denied('account_session_expired',401)
        else:
            request.state.account = account
            declared = request.headers.get('x-pyharness-tenant') or request.query_params.get('tenant')
            if declared and declared != account['tenant']:
                return denied('account_tenant_mismatch')
            path, method = request.url.path, request.method
            if path == '/classic': return denied('account_use_platform')
            if method not in ('GET','HEAD','OPTIONS'):
                if account['role'] == 'viewer' and path != '/api/account/logout':
                    return denied('account_read_only')
                if account['role'] == 'member' and not (
                    path in ('/api/account/logout','/api/v1/runs','/api/v1/sessions','/api/v1/artifacts')
                    or path.startswith('/api/asks/')
                    or re.fullmatch(r'/api/v1/sessions/[^/]+/(messages/[0-9]+/(edit|feedback)|(subagents|jobs)/[^/]+/cancel)',path)
                    or (path.startswith('/api/v1/runs/') and path.endswith(('/cancel','/retry')))):
                    return denied('account_role_required')
            # Legacy global-management APIs were designed for the local operator.
            # Account users only get tenant-safe Platform and conversation routes.
            if path.startswith('/api/') and not path.startswith((
                '/api/v1/','/api/account/','/api/sessions/','/api/asks/','/api/stream','/api/settings/models')):
                return denied('account_operator_api')
            if method not in ('GET','HEAD','OPTIONS'):
                if path.startswith('/api/sessions/'):
                    return denied('account_use_platform')
                if path.startswith('/api/v1/connections') and not path.startswith('/api/v1/connections/model:'):
                    return denied('account_operator_connection')
                if path.endswith('/provider') and path.startswith('/api/v1/knowledge'):
                    return denied('account_operator_connection')
    if getattr(request,'method','GET') not in ('GET','HEAD','OPTIONS') and (token or request.url.path.startswith('/api/account/')):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return denied('cross_origin_request')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return denied('cross_origin_request')
    return None


def mount_accounts(app):
    async def account_error(request, exc):
        return denied(exc.code,exc.status)
    app.api.add_exception_handler(AccountError, account_error)

    @app.api.post('/api/account/users',status_code=201)
    async def provision(request: Request, body: dict=Body(...)):
        if principal(request) or not app._operator_ok(request):
            return denied()
        result = await asyncio.to_thread(store(app).provision, body.get('username'), body.get('password'), body.get('tenant'))
        return JSONResponse(result,status_code=201)

    @app.api.post('/api/account/login')
    async def login(request: Request, body: dict=Body(...)):
        if not hasattr(app, '_account_login_slots'): app._account_login_slots=asyncio.Semaphore(4)
        if app._account_login_slots.locked(): return denied('login_rate_limited',429)
        async with app._account_login_slots:
            token = await asyncio.to_thread(store(app).login, body.get('username'), body.get('password'), body.get('tenant'))
        response = JSONResponse({'account':store(app).authenticate(token)})
        response.set_cookie(COOKIE,token,httponly=True,samesite='strict',secure=request.url.scheme=='https',
                            max_age=Accounts.session_seconds,path='/')
        return response

    @app.api.post('/api/account/logout')
    async def logout(request: Request):
        token = request.cookies.get(COOKIE)
        if token: store(app).logout(token)
        response = JSONResponse({'ok':True})
        response.delete_cookie(COOKIE,path='/')
        return response

    @app.api.get('/api/account/me')
    async def me(request: Request):
        account = principal(request)
        if account:
            return {'account':account,'teams':store(app).teams(account['user_id'])}
        if app._operator_ok(request): return {'mode':'local_operator'}
        return denied('account_login_required',401)

    @app.api.post('/api/account/members')
    async def members(request: Request, body: dict=Body(...)):
        account=principal(request)
        if not account or account['role']!='owner': return denied()
        return store(app).membership(account['tenant'],body.get('username'),body.get('role'))

    @app.api.get('/login')
    async def page():
        from importlib.resources import files
        return HTMLResponse(files('pyharness.ui').joinpath('login.html').read_text(encoding='utf-8'),headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
