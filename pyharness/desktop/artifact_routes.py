"""Versioned HTTP artifact routes."""
from __future__ import annotations
from fastapi import Body
from fastapi.responses import Response
from pyharness.core.sandbox import fail

def mount_artifacts(router, desktop, service):
    @router.get('/artifacts')
    async def artifacts(cursor:str | None=None, limit:int | None=None):
        return await service().artifacts(cursor=cursor,limit=limit)

    @router.post('/artifacts')
    async def upload(body: dict = Body(...)):
        return await service().upload(body)

    @router.get('/artifacts/{ident}')
    async def artifact(ident: str):
        return await service().artifact(ident)

    @router.get('/artifacts/{ident}/download')
    async def download(ident: str):
        item, raw = await service().artifact_content(ident)
        from urllib.parse import quote
        return Response(raw, media_type='application/octet-stream', headers={
            'Content-Disposition':"attachment; filename*=UTF-8''" + quote(item['filename']),
            'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"default-src 'none'; sandbox"})

    @router.get('/artifacts/{ident}/preview')
    async def preview(ident: str):
        item, raw = await service().artifact_content(ident, preview=True)
        if item['preview_type']=='image':
            import base64
            return {'record':item,'image':'data:'+item['media_type']+';base64,'+base64.b64encode(raw).decode(), 'truncated':False}
        return {'record':item, 'text':raw[:65536].decode('utf-8', errors='replace'), 'truncated':len(raw)>65536}

    @router.get('/operations/{ident}')
    async def operation(ident:str):
        task=service().actions.get(ident)
        if task is None:
            fail('operation_not_found')
        if not task.done():
            return {'status':'waiting'}
        if task.cancelled():
            return {'status':'cancelled'}
        if task.exception():
            return {'status':'failed','code':'artifact_action_failed'}
        return {'status':'completed',**task.result()}

    @router.post('/artifacts/{ident}/{action}')
    async def artifact_action(ident: str, action: str):
        return await service().request_artifact_action(ident, action)

    @router.delete('/artifacts/{ident}')
    async def delete_artifact(ident: str):
        return await service().delete_artifact(ident)
