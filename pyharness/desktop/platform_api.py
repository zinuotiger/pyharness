"""Versioned thin HTTP adapter; authentication remains owned by DesktopApp."""
from __future__ import annotations
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from pyharness.core.sandbox import fail
from fastapi.routing import APIRoute
from pyharness.errors import PyHError
from pyharness.integrations.wechat_ilink import ILinkError


class PlatformRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def checked(request):
            try:
                from pyharness.core.lifecycle import admitted
                @admitted
                async def owned(owner):
                    return await handler(request)
                return await owned(self.service_owner)
            except PyHError as exc:
                if exc.code.isupper():
                    raise
                code = exc.code
                status = (404 if code.endswith('_not_found') else
                          503 if code in {'sandbox_unavailable','knowledge_unavailable'} else
                          409 if any(s in code for s in ('busy','pending','fixed','in_use','integrity','expired','not_active','not_pending','not_allowed','cannot_delete','baseline','deprecated')) else 422)
                return JSONResponse({'code':code,'message':code}, status_code=status)
            except ILinkError as exc:
                return JSONResponse({'code':exc.code,'message':'微信连接操作未完成，请检查连接状态'},status_code=503)
            except (ValidationError, ValueError, TypeError):
                return JSONResponse({'code':'invalid_request','message':'请求字段或状态不合法'}, status_code=422)
        return checked


from .channel_routes import mount_channels
from .run_routes import mount_runs
from .agent_routes import mount_agents
from .artifact_routes import mount_artifacts
from .management_routes import mount_management

def mount_platform(desktop):
    class OwnedPlatformRoute(PlatformRoute):
        @property
        def service_owner(self):
            return desktop.service
    router = APIRouter(prefix='/api/v1', route_class=OwnedPlatformRoute, dependencies=[Depends(desktop._require_api_auth)])

    def service():
        desktop.service._ensure_open()
        return desktop.service.platform

    mount_channels(router, desktop, service)

    mount_runs(router, desktop, service)

    mount_agents(router, desktop, service)

    mount_artifacts(router, desktop, service)

    mount_management(router, desktop, service)

    desktop.api.include_router(router)
