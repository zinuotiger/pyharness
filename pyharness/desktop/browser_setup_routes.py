"""Metadata only; no route can directly invoke a browser action."""
import inspect
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pyharness.application.browser_setup import browser_configuration
from .extension_workbench_routes import operator_check, bounded_body


def mount_browser_setup(router, desktop):
    class PrivateBrowserRoute(router.route_class):
        def get_route_handler(self):
            handler = super().get_route_handler()
            async def private(request):
                try:
                    response = await handler(request)
                except HTTPException as error:
                    response = JSONResponse({'code': 'browser_operator_required' if error.status_code in (401,403)
                        else 'invalid_browser_configuration'}, status_code=error.status_code)
                except RecursionError:
                    response = JSONResponse({'code':'invalid_browser_configuration'}, status_code=422)
                except OSError:
                    response = JSONResponse({'code':'browser_setup_unavailable'}, status_code=503)
                except Exception as error:
                    convert = next((desktop.api.exception_handlers[cls] for cls in type(error).__mro__
                                    if cls in desktop.api.exception_handlers), None)
                    if convert is None:
                        raise
                    response = convert(request, error)
                    if inspect.isawaitable(response):
                        response = await response
                if response.status_code >= 400:
                    response = JSONResponse({'code':'browser_configuration_refused'}, status_code=response.status_code)
                response.headers['Cache-Control'] = 'no-store'
                return response
            return private
    private = APIRouter(route_class=PrivateBrowserRoute)

    @private.post('/browser-workbench/configuration')
    async def configuration(request: Request):
        authorize = operator_check(desktop, request)
        body = await bounded_body(request, 8192)
        authorize()
        value = browser_configuration(body)
        authorize()
        return value
    router.include_router(private)
