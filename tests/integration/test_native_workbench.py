"""Real loopback HTTP over the exact native registry, queues and account policy."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest

pytest.importorskip('PySide6', reason='native extra required')
from pyharness import cli
from pyharness.application.accounts import Accounts, current_actor
from pyharness.desktop_native.controller import NativeController
from pyharness.desktop_native.workbench import NativeWorkbenchBridge
from tests.support import isolated_settings

PID='p-'+'a'*16


@pytest.fixture
async def native(tmp_path):
    controller=NativeController(cli.assemble_ctx(isolated_settings(tmp_path)))
    bridge=NativeWorkbenchBridge(controller);controller._native_workbench=bridge
    yield controller,bridge
    await controller.close()


async def test_native_projection_stays_on_owner_event_loop(native,monkeypatch):
    import threading
    controller,_=native;seen=[];owner=threading.get_ident()
    monkeypatch.setattr(controller,'_on_bus_event',lambda kind,payload:seen.append((kind,threading.get_ident())))
    await controller.ctx.bus.emit('llm.chunk',{'delta':'synthetic'})
    assert seen==[('llm.chunk',owner)]


async def test_close_during_real_asgi_startup_never_reopens_or_leaves_channels(native,monkeypatch):
    from contextlib import asynccontextmanager
    from pyharness.desktop_native import workbench
    controller,bridge=native;entered=asyncio.Event();release=asyncio.Event()
    original=workbench._SharedDesktopApp
    class Delayed(original):
        def __init__(self,owner):
            super().__init__(owner)
            previous=self.api.router.lifespan_context
            @asynccontextmanager
            async def held(app):
                async with previous(app):
                    entered.set();await release.wait();yield
            self.api.router.lifespan_context=held
    monkeypatch.setattr(workbench,'_SharedDesktopApp',Delayed)
    pending=asyncio.create_task(bridge.start())
    await asyncio.wait_for(entered.wait(),5)
    closing=asyncio.create_task(bridge.close())
    try:
        await asyncio.sleep(.03);release.set()
        with pytest.raises(RuntimeError,match='start_unconfirmed'):await pending
        await closing
        assert bridge._task.done() and bridge._socket.fileno()==-1
        assert controller.service.channels.closed
        assert (await controller.service.projects.list())['total']==0
        with pytest.raises(RuntimeError,match='closed'):await bridge.start()
    finally:
        release.set();await asyncio.gather(pending,closing,return_exceptions=True)


async def test_real_http_and_native_use_one_registry_and_bridge_close_keeps_service(native):
    controller,bridge=native;registry=controller._registry;service=controller.service
    assert bridge.desktop is None and bridge._task is None and registry.all()==[service]
    seen=[];controller.ctx.bus.subscribe('session.created',lambda p:seen.append(p),owner='desktop')
    first,second=await asyncio.gather(bridge.start(),bridge.start())
    assert first==second and bridge.desktop._service_registry is registry
    assert bridge.desktop.service is service and bridge._loop is asyncio.get_running_loop()
    await service.projects.save(dict(project_id=PID,expected_revision=0,name='Native initial'),'native-create')
    async with httpx.AsyncClient(base_url=first,trust_env=False) as client:
        assert (await client.get('/api/v1/projects')).status_code==401
        client.headers['X-PyHarness-Token']=bridge.desktop._api_token
        rows=(await client.get('/api/v1/projects')).json()
        assert rows['projects'][0]['project']['name']=='Native initial'
        result=await client.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=1,name='HTTP changed'),
                                 headers={'Idempotency-Key':'http-edit'})
        assert result.status_code==200,result.text
        assert (await service.projects.list())['projects'][0]['project']['name']=='HTTP changed'
        assert (await client.get('/api/v1/projects',headers={'Host':'foreign.invalid'})).status_code==400
        assert (await client.post('/api/account/login',json={},headers={'Origin':'https://foreign.invalid'})).status_code==403
    assert not service._queues and not service._engines
    await asyncio.gather(bridge.close(),bridge.close())
    assert bridge._task.done() and bridge._socket.fileno()==-1
    assert service in registry.all()
    assert (await service.projects.list())['total']==1
    assert any(sub.owner=='desktop' for sub in controller.ctx.bus._by_type['session.created'])
    assert all(sub.owner!=bridge.desktop._native_bus_owner for rows in controller.ctx.bus._by_type.values() for sub in rows)
    with pytest.raises(RuntimeError,match='closed'):await bridge.start()


async def test_two_native_instances_and_ports_remain_isolated(tmp_path):
    controllers=[NativeController(cli.assemble_ctx(isolated_settings(tmp_path/str(i)))) for i in range(2)]
    bridges=[NativeWorkbenchBridge(c) for c in controllers]
    for controller,bridge in zip(controllers,bridges):controller._native_workbench=bridge
    try:
        urls=await asyncio.gather(*(bridge.start() for bridge in bridges))
        assert urls[0]!=urls[1]
        assert controllers[0].service is not controllers[1].service
        await controllers[0].service.projects.save(dict(project_id=PID,expected_revision=0,name='only first'),'first')
        async with httpx.AsyncClient(base_url=urls[1],trust_env=False,
                headers={'X-PyHarness-Token':bridges[1].desktop._api_token}) as client:
            assert (await client.get('/api/v1/projects')).json()['total']==0
            assert (await client.get('/api/v1/projects',headers={'X-PyHarness-Token':bridges[0].desktop._api_token})).status_code==401
            await controllers[0].close()
            assert (await client.get('/api/v1/projects')).status_code==200
    finally:
        for controller in controllers:await controller.close()


async def test_tenant_details_require_original_login_and_cannot_borrow_host(native,tmp_path):
    controller,bridge=native
    accounts=Accounts(tmp_path);accounts.provision('native-team-owner','synthetic-team-password','team')
    await bridge.start()
    operator_url=bridge.url_for_current_identity();assert '?token=' in operator_url
    controller.switch_tenant('team')
    assert bridge.url_for_current_identity()==bridge.base_url+'/login'
    async with httpx.AsyncClient(base_url=bridge.base_url,trust_env=False) as client:
        assert (await client.get('/api/v1/projects',headers={'X-PyHarness-Tenant':'team'})).status_code==401
        response=await client.post('/api/account/login',json={'username':'native-team-owner','password':'synthetic-team-password'})
        assert response.status_code==200,response.text
        response=await client.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=0,name='Team original account'),
                                   headers={'Idempotency-Key':'team-native-create'})
        assert response.status_code==200,response.text
        assert bridge.desktop._service_registry.get('team') is controller.service
        # Read the same subject-bound state natively; tenant names confer no account authority.
        token=current_actor.set({'tenant':'team','user_id':accounts.authenticate(client.cookies.get('pyharness_account_'+str(bridge._socket.getsockname()[1])))['user_id']})
        try:assert (await controller.service.projects.list())['total']==1
        finally:current_actor.reset(token)
        assert (await client.post('/api/v1/browser-workbench/configuration',json={})).status_code==403
        assert (await client.post('/api/v1/projects',json={},headers={'Origin':'https://foreign.invalid'})).status_code==403
        response=await client.get('/api/v1/projects',headers={'X-PyHarness-Tenant':'default'})
        assert response.status_code==403
        assert (await client.post('/api/account/logout')).status_code==200
        assert (await client.get('/api/v1/projects')).status_code==401
    controller.switch_tenant('default')
    assert (await controller.service.projects.list())['total']==0
    token=current_actor.set({'tenant':'team','user_id':'synthetic-account'})
    try:assert bridge.url_for_current_identity().endswith('/login')
    finally:current_actor.reset(token)


async def test_provided_service_has_no_implicit_new_authority():
    controller=NativeController(SimpleNamespace(),service=SimpleNamespace())
    bridge=NativeWorkbenchBridge(controller)
    with pytest.raises(ValueError,match='owned_registry'):await bridge.start()
    assert bridge.desktop is None and bridge._task is None and bridge._dead
    await bridge.close()


async def test_start_failure_retires_server_and_detaches_only_own_subscriptions(native,monkeypatch):
    from pyharness.desktop_native import workbench
    controller,bridge=native
    controller.ctx.bus.subscribe('session.created',lambda p:None,owner='desktop')
    async def broken(server,sockets=None):raise RuntimeError('synthetic-start-failure')
    monkeypatch.setattr(workbench._EmbeddedServer,'serve',broken)
    with pytest.raises(RuntimeError,match='synthetic-start-failure'):await bridge.start()
    assert bridge._dead and bridge._task.done() and bridge._socket.fileno()==-1
    assert any(sub.owner=='desktop' for sub in controller.ctx.bus._by_type['session.created'])
    assert all(sub.owner!=bridge.desktop._native_bus_owner for rows in controller.ctx.bus._by_type.values() for sub in rows)
    assert (await controller.service.projects.list())['total']==0
    with pytest.raises(RuntimeError,match='closed'):await bridge.start()
    # The original startup error remains visible; cleanup does not invent success.
    with pytest.raises(RuntimeError,match='synthetic-start-failure'):await bridge.close()
    controller._native_workbench=None  # Expected injected server failure already asserted above.


async def test_native_started_actual_queue_is_visible_and_stopped_over_real_http(tmp_path,adapter_factory):
    from tests.unit.test_run_attempts import ready,submission,submit
    from tests.unit.test_product_tasks import ref
    from tests.unit.test_product_execution_start import wait_terminal
    from tests.unit.test_source_grants import PID as task_project
    cfg=isolated_settings(tmp_path/'app');adapter=adapter_factory(cfg,[])
    entered=asyncio.Event();release=asyncio.Event();original=adapter.chat
    async def hold(messages,tools=None,*,ctx):
        result=await original(messages,tools,ctx=ctx)
        entered.set();await release.wait();return result
    adapter.chat=hold
    controller=NativeController(cli.assemble_ctx(cfg))
    bridge=NativeWorkbenchBridge(controller);controller._native_workbench=bridge
    try:
        await bridge.start()
        async with httpx.AsyncClient(base_url=bridge.base_url,trust_env=False,
                headers={'X-PyHarness-Token':bridge.desktop._api_token}) as client:
            response=await client.post('/api/v1/projects',json=dict(project_id=task_project,expected_revision=0,name='Native queue'),
                                        headers={'Idempotency-Key':'native-project'})
            assert response.status_code==200,response.text
            source=tmp_path/'captured';source.mkdir();(source/'unchanged.txt').write_bytes(b'unchanged\r\n')
            task=await ready(client,source);attempt=(await submit(client,submission(task))).json()
            platform=controller.service.platform
            agent=platform.save_agent(dict(name='Native published',allowed_tools=[]))
            version=platform.publish_agent(agent['agent_id'])
            selection=dict(schema_version=1,attempt_ref=ref(attempt,'attempt_id'),agent_ref=dict(
                agent_id=version['agent_id'],version=version['version'],sha256=version['sha256']))
            projects=controller.service.projects;preview=await projects.execution_preview(task_project,selection)
            command=dict(selection,execution_id='ex-'+'c'*16,confirmation_digest=preview['confirmation_digest'])
            started=await projects.start_execution(task_project,command,'native-start')
            await asyncio.wait_for(entered.wait(),5)
            base=f'/api/v1/projects/{task_project}'
            observed=await client.get(base+'/executions/'+command['execution_id'])
            assert observed.status_code==200 and observed.json()['status']=='running'
            assert bridge.desktop.service._queues is controller.service._queues
            assert bridge.desktop.service._queues[started['session_id']] is controller.service._queues[started['session_id']]
            stop=dict(schema_version=1,cancellation_id='ec-'+'d'*16,
                      execution_ref={k:started[k] for k in ('execution_id','revision','digest','source_seq')})
            response=await client.post(base+'/execution-cancellations',json=stop,headers={'Idempotency-Key':'native-http-stop'})
            assert response.status_code==200,response.text
            terminal=await wait_terminal(projects,command['execution_id'])
            assert terminal['status']=='cancelled' and adapter.n==1
            before=projects.store.log_path.read_bytes()
            query=await client.get(base+'/cancellation-submissions/by-key',params={'key':'native-http-stop'})
            assert query.status_code==200 and query.json()['cancellation']['execution_stopped'] is True
            assert projects.store.log_path.read_bytes()==before and adapter.n==1
    finally:
        release.set();await controller.close()
