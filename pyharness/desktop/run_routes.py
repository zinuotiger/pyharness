"""Versioned HTTP run routes."""
from __future__ import annotations
from fastapi import Body
from pyharness.core.sandbox import fail

def mount_runs(router, desktop, service):
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

    @router.get('/history/search')
    async def history_search(q:str, session_id:str | None=None):
        return await service().history_search(q,session_id)

    @router.post('/sessions/{sid}/messages/{seq}/edit')
    async def edit_message(sid:str,seq:int,body:dict=Body(...)):
        return await service().edit_message(sid,seq,body)

    @router.post('/sessions/{sid}/messages/{seq}/feedback')
    async def feedback(sid:str,seq:int,body:dict=Body(...)):
        return await service().feedback(sid,seq,body)

    @router.get('/sessions/{sid}/activity')
    async def activity(sid:str):
        return await service().activity(sid)

    @router.post('/sessions/{sid}/subagents/{ident}/cancel')
    async def cancel_subagent(sid:str,ident:str):
        return {'ok':await desktop.service.cancel_subagent(sid,ident)}

    @router.post('/sessions/{sid}/jobs/{ident}/cancel')
    async def cancel_job(sid:str,ident:str):
        return {'ok':await desktop.service.cancel_job(sid,ident)}

    @router.get('/runs')
    async def runs(cursor:str | None=None, limit:int | None=None):
        return await service().runs(cursor=cursor,limit=limit)

    @router.get('/chat/readiness')
    async def chat_readiness(session_id: str | None = None, agent_id: str | None = None, agent_version: int | None = None):
        return await service().chat_readiness({'session_id':session_id, 'agent_id':agent_id, 'agent_version':agent_version})

    @router.get('/chat/configuration')
    async def chat_configuration(session_id: str | None=None, agent_id: str | None=None, version: int | None=None):
        return await service().chat_setup.configuration(session_id, agent_id, version)

    @router.post('/chat/assistants')
    async def create_use(body: dict=Body(...)):
        return service().chat_setup.create_use(body)

    @router.get('/chat/assistants')
    async def chat_assistants():
        return service().chat_setup.assistants()

    @router.post('/chat/switch')
    async def switch_chat(body: dict=Body(...)):
        return await service().chat_setup.switch(body)

    @router.post('/chat/execution-readiness')
    async def execution_readiness(body: dict=Body(...)):
        return await service().chat_setup.readiness(body)

    @router.get('/chat/remote-connections')
    async def remote_connections():
        return service().chat_setup.remotes()

    @router.post('/chat/remote-connections')
    async def remote_connection(body: dict=Body(...)):
        return service().chat_setup.save_remote(body)

    @router.post('/runs')
    async def start_run(body: dict = Body(...)):
        return await service().start_run(body)

    @router.get('/runs/{ident}')
    async def run(ident: str):
        return await service().run(ident)

    @router.get('/runs/{ident}/trace')
    async def trace(ident: str, cursor: str | None=None, limit: int=500):
        item = await service().run(ident, cursor=cursor, limit=limit)
        return {'trace':item['trace'], 'total':item['trace_total'],
                'truncated':item['trace_truncated'], 'cursor':item['trace_cursor']}

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
    async def decision(ident: str, action: str, body: dict=Body(default={})):
        if action not in {'approve','reject'}:
            fail('invalid_action')
        return await service().decide(ident, action, body or None)
