"""Shared memory reuses SessionKV with tenant ownership; never global model memory."""
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


async def test_memory_cross_session_restart_delete_and_tenant_isolation(tmp_path):
    cfg=isolated_settings(tmp_path)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        a=app._service_registry.get('team-a')
        b=app._service_registry.get('team-b')
        await a.create_session()
        await a.memory_action({'op':'set','key':'style','value':'synthetic concise'})
        await a.create_session()
        assert (await a.memory_action({'op':'get','key':'style'}))['result']=='synthetic concise'
        assert 'synthetic concise' not in str(await b.memory_action({'op':'get','key':'style'}))
    finally:
        for service in app._service_registry.all(): await service.shutdown()
    restored=DesktopApp(cli.assemble_ctx(cfg))
    try:
        a=restored._service_registry.get('team-a')
        assert (await a.memory_action({'op':'get','key':'style'}))['result']=='synthetic concise'
        await a.memory_action({'op':'delete','key':'style'})
        assert 'synthetic concise' not in str(await a.memory_action({'op':'get','key':'style'}))
    finally:
        for service in restored._service_registry.all(): await service.shutdown()


async def test_published_agent_explicitly_shares_existing_kv(tmp_path,adapter_factory):
    import asyncio
    cfg=isolated_settings(tmp_path)
    adapter=adapter_factory(cfg)
    instances={}
    async def response(*args,**kwargs):
        sid=kwargs['ctx'].session.sid
        if sid not in instances:
            operation={'op':'set','key':'preference','value':'synthetic shared preference'} if not instances else {'op':'get','key':'preference'}
            instances[sid]=type(adapter)(adapter.model,[{'id':'memory','name':'storage.kv','args':operation}])
        return await instances[sid].chat(*args,**kwargs)
    adapter.chat=adapter.chat_stream=response
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        p=app.service.platform
        agent=p.save_agent({'name':'team memory','allowed_tools':['storage.kv'],'memory_scope':'team'})
        p.publish_agent(agent['agent_id'])
        for _ in range(2):
            run=await p.start_run({'text':'use permitted memory','agent_id':agent['agent_id'],'agent_version':1})
            assert (await asyncio.wait_for(app.service._queues[run['session_id']].wait_for(run['task_id']),10)).ok
        log=await app.service.require_session(run['session_id'])
        assert any(e.type=='tool.result' and 'synthetic shared preference' in str(e.payload) for e in log.events_after(0))
    finally:
        await app.service.shutdown()
