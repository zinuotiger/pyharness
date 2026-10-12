"""Keep browser identities separate for loopback servers on different ports."""
from __future__ import annotations

from fastapi import Request


AUTH_COOKIES = ('pyharness_token', 'pyharness_tenant_token', 'pyharness_account')


def cookie_name(request: Request, name: str) -> str:
    url = getattr(request, 'url', None)
    port = getattr(url, 'port', None)
    default_port = 443 if getattr(url, 'scheme', 'http') == 'https' else 80
    return f'{name}_{port}' if port and port != default_port else name


def read_cookie(request: Request, name: str) -> str:
    cookies = getattr(request, 'cookies', None) or {}
    # Once this port has an identity, never mix it with another instance's
    # legacy cookies (including a legacy team account overriding an operator).
    scoped = cookie_name(request, name)
    if scoped != name and any(cookie_name(request, key) in cookies for key in AUTH_COOKIES):
        return cookies.get(scoped, '') or ''
    # Existing clients can migrate by visiting their authenticated entry page.
    return cookies.get(name, '') or ''
