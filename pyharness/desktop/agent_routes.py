"""Versioned HTTP agent routes."""
from __future__ import annotations
from fastapi import Body
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pyharness.core.sandbox import fail

def mount_agents(router, desktop, service):
    @router.get('/agents')
    async def agents():
        return service().agents()

    @router.post('/agents')
    async def save_agent(body: dict = Body(...)):
        try:
            return service().save_agent(body)
        except ValidationError:
            return JSONResponse({'code':'invalid_agent','message':'Agent 配置字段或范围不合法'}, status_code=422)

    @router.put('/agents/{ident}')
    async def edit_agent(ident: str, body: dict = Body(...)):
        try:
            return service().save_agent(body, ident)
        except ValidationError:
            return JSONResponse({'code':'invalid_agent','message':'Agent 配置字段或范围不合法'}, status_code=422)

    @router.get('/agents/{ident}')
    async def agent(ident:str):
        item=next((a for a in service().agents()['agents'] if a['agent_id']==ident),None)
        if item is None: fail('agent_not_found')
        return item

    @router.get('/agents/{ident}/versions')
    async def versions(ident: str):
        return service().agent_action(ident, 'versions')

    @router.post('/agents/{ident}/{action}')
    async def agent_action(ident: str, action: str, body: dict = Body(default={})):
        return service().agent_action(ident, action, body)

    @router.delete('/agents/{ident}')
    async def delete_agent(ident: str):
        return service().agent_action(ident, 'delete')
