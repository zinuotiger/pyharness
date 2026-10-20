"""Private configuration history; identity and storage remain original owners."""
from __future__ import annotations
import inspect
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pyharness.application.configuration_history import ConfigurationHistory
from .accounts_api import COOKIE, principal, operator_principal, read_cookie, store


def configuration_authority(desktop, request, service):
    original = principal(request) or operator_principal(request)
    expected = (original['user_id'], original['tenant']) if original else None
    tenant = service.tenant_id
    def check():
        if expected is not None:
            current = store(desktop).authenticate(read_cookie(request, COOKIE))
            if (not current or (current['user_id'], current['tenant']) != expected
                    or current['tenant'] != tenant):
                raise HTTPException(403, 'configuration_manager_required')
            if current['role'] == 'operator' and tenant == 'default':
                mode = 'local_operator'
            elif current['role'] == 'owner':
                mode = 'tenant_account'
            else:
                raise HTTPException(403, 'configuration_manager_required')
            return {'mode': mode, 'role': current['role'], 'tenant': tenant, 'user_id': current['user_id']}
        if not desktop._operator_ok(request) or not desktop._tenant_authorized(request, tenant):
            raise HTTPException(403, 'configuration_manager_required')
        return {'mode': 'local_operator', 'tenant': tenant}
    check()
    return check


async def configuration_body(request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk) > 2048:
            raise HTTPException(422, 'configuration_request_limit')
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, 'configuration_invalid_request') from None
    if type(value) is not dict:
        raise HTTPException(422, 'configuration_invalid_request')
    return value


def mount_configuration_history(router, desktop, service):
    class PrivateConfigurationRoute(router.route_class):
        def get_route_handler(self):
            handler = super().get_route_handler()
            async def private(request):
                try:
                    response = await handler(request)
                except HTTPException as error:
                    response = JSONResponse({'code': error.detail if type(error.detail) is str
                        else 'configuration_operation_failed'}, status_code=error.status_code)
                except Exception as error:
                    convert = next((desktop.api.exception_handlers[cls] for cls in type(error).__mro__
                                    if cls in desktop.api.exception_handlers), None)
                    if convert is None:
                        raise
                    response = convert(request, error)
                    if inspect.isawaitable(response):
                        response = await response
                if response.status_code >= 400:
                    try:
                        value = json.loads(response.body)
                        code = value.get('code', 'configuration_operation_failed')
                    except (ValueError, TypeError, AttributeError):
                        code = 'configuration_operation_failed'
                    response = JSONResponse({'code': code}, status_code=response.status_code)
                response.headers['Cache-Control'] = 'no-store'
                return response
            return private
    private = APIRouter(route_class=PrivateConfigurationRoute)
    def owner(request):
        application = service().service
        authorize = configuration_authority(desktop, request, application)
        return ConfigurationHistory(application), authorize

    @private.get('/configuration-history/{kind}/{resource}/current')
    async def current(kind: str, resource: str, request: Request):
        history, authorize = owner(request)
        return history.current(kind, resource, authorize=authorize)

    @private.get('/configuration-history/{kind}/{resource}/revisions')
    async def revisions(kind: str, resource: str, request: Request):
        history, authorize = owner(request)
        return history.history(kind, resource, authorize=authorize)

    @private.post('/configuration-history/{kind}/{resource}/revisions')
    async def capture(kind: str, resource: str, request: Request):
        history, authorize = owner(request)
        body = await configuration_body(request)
        authorize()
        return history.capture(kind, resource, body, authorize=authorize)

    @private.get('/configuration-history/{kind}/{resource}/revisions/{ident}/preview')
    async def preview(kind: str, resource: str, ident: str, request: Request):
        history, authorize = owner(request)
        return history.preview(kind, resource, ident, authorize=authorize)

    @private.post('/configuration-history/{kind}/{resource}/revisions/{ident}/applications')
    async def apply(kind: str, resource: str, ident: str, request: Request):
        history, authorize = owner(request)
        body = await configuration_body(request)
        authorize()
        return history.apply(kind, resource, ident, body, authorize=authorize)

    @private.get('/configuration-history/{kind}/{resource}/commands/{command_key}')
    async def command(kind: str, resource: str, command_key: str, request: Request):
        history, authorize = owner(request)
        return history.command(kind, resource, command_key, authorize=authorize)

    router.include_router(private)
