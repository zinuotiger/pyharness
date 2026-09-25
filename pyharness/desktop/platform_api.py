"""Versioned thin HTTP adapter; authentication remains owned by DesktopApp."""
from __future__ import annotations
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from pyharness.core.sandbox import fail
from fastapi.routing import APIRoute
from pyharness.errors import PyHError


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
            except (ValidationError, ValueError, TypeError):
                return JSONResponse({'code':'invalid_request','message':'请求字段或状态不合法'}, status_code=422)
        return checked


def mount_platform(desktop):
    class OwnedPlatformRoute(PlatformRoute):
        @property
        def service_owner(self):
            return desktop.service
    router = APIRouter(prefix='/api/v1', route_class=OwnedPlatformRoute, dependencies=[Depends(desktop._require_api_auth)])

    def service():
        desktop.service._ensure_open()
        return desktop.service.platform

    @router.get('/dashboard')
    async def dashboard():
        return await service().dashboard()

    @router.get('/sessions')
    async def sessions():
        return await service().sessions()

    @router.post('/sessions')
    async def create_session():
        return {'sid':await desktop.service.create_session()}

    @router.post('/sessions/{sid}/metadata')
    async def session_metadata(sid: str, body: dict=Body(...)):
        return await service().session_metadata(sid, body)

    @router.get('/runs')
    async def runs():
        return await service().runs()

    @router.post('/runs')
    async def start_run(body: dict = Body(...)):
        return await service().start_run(body)

    @router.get('/runs/{ident}')
    async def run(ident: str):
        return await service().run(ident)

    @router.get('/runs/{ident}/trace')
    async def trace(ident: str):
        item = await service().run(ident)
        return {'trace':item['trace']}

    @router.post('/runs/{ident}/cancel')
    async def cancel(ident: str):
        return await service().cancel(ident)

    @router.post('/runs/{ident}/retry')
    async def retry(ident: str):
        return await service().retry(ident)

    @router.get('/approvals')
    async def approvals():
        return await service().approvals()

    @router.post('/approvals/{ident}/{action}')
    async def decision(ident: str, action: str):
        if action not in {'approve','reject'}:
            fail('invalid_action')
        return await service().decide(ident, action)

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

    @router.get('/artifacts')
    async def artifacts():
        return await service().artifacts()

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

    @router.get('/knowledge')
    async def knowledge():
        return service().knowledge_list()

    @router.post('/knowledge')
    async def add_knowledge(body: dict = Body(...)):
        return await service().add_knowledge(body)

    @router.post('/knowledge/query')
    async def query(body: dict = Body(...)):
        return await service().query_knowledge(body.get('query',''), body.get('sources'))

    @router.delete('/knowledge/{ident}')
    async def delete_knowledge(ident: str):
        return service().delete_knowledge(ident)

    @router.post('/knowledge/{ident}/{action}')
    async def knowledge_action(ident:str,action:str,body:dict=Body(default={})):
        return service().knowledge_action(ident,action,body)

    @router.get('/sandboxes')
    async def sandboxes():
        platform = service()
        return {'sandboxes':await platform.sandbox_records(),
            'health':await platform.sandboxes.backends['isolated'].health(),
            'warning':'当前命令运行于宿主环境，不具备 OS 级隔离。'}

    @router.get('/sandboxes/{ident}')
    async def sandbox(ident:str):
        item=next((r for r in await service().sandbox_records() if r['sandbox_id']==ident),None)
        if item is None: fail('sandbox_not_found')
        return item

    @router.post('/sandboxes/{ident}/destroy')
    async def destroy(ident: str):
        return await service().destroy_sandbox(ident)

    @router.get('/usage')
    async def usage(request: Request):
        return await service().usage(filters=dict(request.query_params))

    @router.get('/system/health')
    async def health():
        return await service().health()

    @router.get('/connections')
    async def connections():
        models = desktop.service.tenant_state()
        skills = await desktop.service.list_skills()
        plugins = await desktop.service.list_plugins()
        # Configuration is not connectivity evidence. Never expose command/env.
        cfg = desktop.service.effective_settings()
        mcp = [{'name':m.name, 'enabled':m.enabled, 'status':'not_probed'} for m in cfg.plugins.mcp_servers]
        return {'models':models, 'skills':skills, 'plugins':plugins, 'mcp':mcp,
                'connections':service().connections()}

    @router.post('/connections')
    async def save_connection(body: dict=Body(...)):
        try:
            return service().save_connection(body)
        except ValidationError:
            return JSONResponse({'code':'invalid_connection'},status_code=422)

    @router.post('/connections/{ident}/{action}')
    async def connection_action(ident:str,action:str):
        return await service().connection_action(ident,action)

    @router.get('/schedules')
    async def schedules():
        from pyharness.core.schedule import Scheduler
        from dataclasses import asdict
        rows=[];schedules=[]
        for session in (await desktop.service.list_sessions())['sessions'][:200]:
            # Do not start runtimes/credentials while rendering an overview.
            log=await desktop.service.require_session(session['sid'])
            scheduler=Scheduler.rebuild_for_session(log)
            for job in await scheduler.list_jobs():
                record=asdict(job)
                record['session_id']=session['sid']
                record['intent']=scheduler._jobs[job.name].template['intent']
                for field in ('next_fire_at','last_fired_at'):
                    record[field]=record[field].astimezone().isoformat() if record[field] else None
                schedules.append(record)
            for event in log.events_after(0):
                if event.type.startswith('schedule.'):
                    rows.append({'session_id':session['sid'],'type':event.type,'timestamp':event.ts,'payload':event.payload})
        return {'events':rows[-200:],'schedules':schedules}

    @router.post('/schedules')
    async def schedule_action(body:dict=Body(...)):
        sid = str(body.get('session_id') or '')
        if body.get('action') == 'add':
            sid = await service().schedule_session(body)
        return await desktop.service.schedule_action(sid,str(body.get('action') or ''),
            name=str(body.get('name') or ''),kind=str(body.get('kind') or 'interval'),
            expr=str(body.get('expr') or ''),intent=str(body.get('intent') or ''))

    desktop.api.include_router(router)
