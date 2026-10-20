"""Local operator extension diagnostics, separate from governed tool execution."""
from __future__ import annotations
import json
from fastapi import HTTPException, Request
from pyharness.application.mcp_workbench import McpWorkbench
from pyharness.application.skill_workbench import SkillWorkbench
from pyharness.core.sandbox import fail
from .accounts_api import COOKIE, operator_principal, read_cookie, store


def operator_check(desktop, request):
    """Return a live authority check, including after long host-process awaits."""
    original = operator_principal(request)
    def check():
        if original:
            account = store(desktop).authenticate(read_cookie(request, COOKIE))
            if (not account or account['role'] != 'operator' or account['tenant'] != 'default'
                    or account['user_id'] != original['user_id']):
                raise HTTPException(403, 'operator_required')
        elif not desktop._operator_ok(request):
            raise HTTPException(403, 'operator_required')
    check()
    return check


async def bounded_body(request, limit):
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > limit:
            fail('diagnostic_request_limit')
        chunks.extend(chunk)
    value = json.loads(chunks)
    if type(value) is not dict:
        fail('invalid_diagnostic_request')
    return value


def mount_extension_workbench(router, desktop, service):
    @router.post('/extension-workbench/skill-snapshots')
    async def capture_skill(request: Request):
        authorize = operator_check(desktop, request)
        body = await bounded_body(request, 1024)
        if set(body) != {'name'}:
            fail('invalid_skill_snapshot')
        return SkillWorkbench(service()).capture(body['name'], authorize=authorize)

    @router.get('/extension-workbench/skill-snapshots/{ident}')
    async def skill_snapshot(ident: str, request: Request):
        authorize = operator_check(desktop, request)
        return SkillWorkbench(service()).snapshot(ident, authorize=authorize)

    @router.post('/extension-workbench/skill-snapshots/{ident}/rehearsals')
    async def rehearse_skill(ident: str, request: Request):
        authorize = operator_check(desktop, request)
        body = await bounded_body(request, 2048)
        return await SkillWorkbench(service()).rehearse(ident, body, authorize=authorize)

    @router.get('/extension-workbench/skill-rehearsals/{key}')
    async def skill_result(key: str, request: Request):
        authorize = operator_check(desktop, request)
        return SkillWorkbench(service()).result(key, authorize=authorize)

    @router.get('/extension-workbench/mcp/{source}/{name}')
    async def preview(source: str, name: str, request: Request):
        authorize = operator_check(desktop, request)
        return McpWorkbench(service()).preview(source, name, authorize=authorize)

    @router.post('/extension-workbench/mcp/{source}/{name}/discoveries')
    async def discover(source: str, name: str, request: Request):
        authorize = operator_check(desktop, request)
        body = await bounded_body(request, 2048)
        return await McpWorkbench(service()).discover(source, name, body, authorize=authorize)

    @router.get('/extension-workbench/discoveries/{key}')
    async def result(key: str, request: Request):
        authorize = operator_check(desktop, request)
        return McpWorkbench(service()).result(key, authorize=authorize)

    @router.post('/extension-workbench/discoveries/{key}/arguments')
    async def arguments(key: str, request: Request):
        authorize = operator_check(desktop, request)
        body = await bounded_body(request, 65536)
        if set(body) != {'tool', 'arguments'}:
            fail('invalid_diagnostic_request')
        return McpWorkbench(service()).arguments(key, body['tool'], body['arguments'], authorize=authorize)
