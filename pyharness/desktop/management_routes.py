"""Versioned HTTP management routes."""
from __future__ import annotations
from fastapi import Body, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pyharness.core.sandbox import fail

def mount_management(router, desktop, service):
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
        result = service().delete_knowledge(ident)
        await service().sync_knowledge()
        return result

    @router.post('/knowledge/{ident}/{action}')
    async def knowledge_action(ident:str,action:str,body:dict=Body(default={})):
        result = service().knowledge_action(ident,action,body)
        await service().sync_knowledge()
        return result

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

    @router.get('/workflows')
    async def workflows():
        return await service().workflows.list()

    @router.get('/memory')
    async def memory(key: str | None = None):
        return await desktop.service.memory_action({'op':'get','key':key} if key else {'op':'keys'})

    @router.post('/memory')
    async def edit_memory(body: dict=Body(...)):
        return await desktop.service.memory_action(body)

    @router.post('/workflows')
    async def start_workflow(body: dict=Body(...)):
        return await service().workflows.start(body)

    @router.post('/workflows/{ident}/cancel')
    async def cancel_workflow(ident: str):
        return await service().workflows.cancel(ident)

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
