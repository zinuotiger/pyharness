"""Host policy: complete migrated implementation."""
from __future__ import annotations
from typing import TYPE_CHECKING
import logging
import re
from starlette.responses import PlainTextResponse
if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    pass


_SESSION_ID_IN_PATH = re.compile(
    r"/(?:sessions|budget|approvals|asks)/(s-[0-9A-Za-z._-]+)")

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

# Preserve historical import and pickle identities through the compatibility facade.
LoopbackHostMiddleware.__module__ = 'pyharness.desktop.app'
