"""Service-owned liveness is observation only, never execution or replay authority."""
import asyncio,copy
import httpx,pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_product_execution_cancellation import start,command
from tests.unit.test_source_grants import PID


async def held(env):
    entered=asyncio.Event();release=asyncio.Event()
    async def hold(ctx,response):env.controls.ctx=ctx;entered.set();await release.wait()
    env.controls.after_chat=hold
    started=await start(env,'ex-'+'c'*16);await asyncio.wait_for(entered.wait(),5)
    return started,release


async def test_real_running_heartbeat_expires_without_replay_or_fake_completion(running_product):
    env=running_product;p=env.app.service.projects;started,release=await held(env)
    try:
        r=await p.execution(PID,started['execution_id']);control=r['control_observation']
        assert control['service_status']=='connected' and control['execution_owner']=='live'
        assert control['heartbeat']['state']=='fresh' and control['activity']=='running_unclassified'
        assert control['heartbeat']['scope']=='service_event_loop' and control['replay_authorized'] is False
        boundary=env.app.service._engines[started['session_id']].platform_runtime.product_execution
        await boundary.heartbeat.stop()
        stopped=(await p.execution(PID,started['execution_id']))['control_observation']
        assert stopped['heartbeat']['age_seconds']<10 and stopped['heartbeat']['state']=='inactive' and stopped['activity']=='heartbeat_unavailable'
        boundary.heartbeat.last_mono-=30
        r=await p.execution(PID,started['execution_id']);control=r['control_observation']
        assert r['status']=='running' and control['heartbeat']['state']=='expired' and control['activity']=='heartbeat_expired'
        assert env.adapter.n==1 and not r['unknown_effects']
        await p.cancel_execution(PID,command(started),'stop-expired-heartbeat')
        r=await wait_terminal(p,started['execution_id'])
        assert r['control_observation']['activity']=='terminal' and r['status']=='cancelled' and env.adapter.n==1
        assert boundary.heartbeat.task is None
    finally:release.set()


async def test_cold_reader_never_invents_heartbeat_or_opens_execution_writer(running_product,monkeypatch):
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    env=running_product;started,release=await held(env)
    cold=DesktopApp(cli.assemble_ctx(copy.deepcopy(env.app.service.ctx.settings)))
    def forbidden(*a,**kw):raise AssertionError('cold observation opened writer')
    monkeypatch.setattr(cold.service.projects.store,'_open',forbidden)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            r=await client.get(f'/api/v1/projects/{PID}/executions/'+started['execution_id'])
        assert r.status_code==200,r.text
        v=r.json();assert v['status']=='interrupted'
        c=v['control_observation'];assert c['execution_owner']=='absent' and c['heartbeat']['state']=='not_observed' and c['activity']=='interrupted'
        assert not cold.service._engines and not cold.service._queues and env.adapter.n==1
    finally:await cold.service.shutdown();release.set()


async def test_actual_monitor_advances_and_duplicate_runner_cannot_replace_its_owner(running_product,monkeypatch):
    from pyharness.errors import PyHError
    monkeypatch.setattr('pyharness.application.execution_liveness.HEARTBEAT_INTERVAL_SECONDS',.01)
    env=running_product;started,release=await held(env);p=env.app.service.projects
    boundary=env.app.service._engines[started['session_id']].platform_runtime.product_execution
    try:
        first=(await p.execution(PID,started['execution_id']))['control_observation']['heartbeat']
        await asyncio.sleep(.05)
        second=(await p.execution(PID,started['execution_id']))['control_observation']['heartbeat']
        assert first['observer_id']==second['observer_id'] and first['last_observed_at']!=second['last_observed_at']
        task=boundary.heartbeat.task;log=env.controls.ctx.session;before=list(log.events_after(0))
        async def forbidden():raise AssertionError('duplicate runner entered')
        with pytest.raises(PyHError):await boundary.run_task(None,env.controls.ctx,forbidden)
        assert boundary.heartbeat.task is task and not task.done() and list(log.events_after(0))==before and env.adapter.n==1
        await p.cancel_execution(PID,command(started),'stop-live-monitor')
        await wait_terminal(p,started['execution_id'])
        assert boundary.heartbeat.task is None and task.done()
    finally:release.set()


@pytest.mark.parametrize('running_product',[dict(script=[dict(id='wait-real-approval',name='fs.write_file',args=dict(path='unchanged.txt',content='not-yet-written'))],agent=dict(sandbox_profile=dict(mode='host_approved')))],indirect=True)
async def test_actual_approval_wait_is_separate_from_heartbeat_and_no_effect_started(running_product):
    env=running_product;p=env.app.service.projects;started=await start(env,'ex-'+'c'*16)
    async with asyncio.timeout(8):
        while True:
            pending=(await env.app.service.pending_approvals())['pending']
            if pending:break
            await asyncio.sleep(.01)
    r=await p.execution(PID,started['execution_id']);c=r['control_observation']
    assert c['heartbeat']['state']=='fresh' and c['activity']=='waiting_approval'
    assert c['approval_waits']==[dict(session_id=started['session_id'],approval_id=pending[0]['approval_id'],ttl_ms=120000)]
    assert not r['effects'] and env.adapter.n==1
    await p.cancel_execution(PID,command(started),'stop-actual-approval')
    r=await wait_terminal(p,started['execution_id'])
    assert r['status']=='cancelled' and r['control_observation']['approval_waits']==[] and env.adapter.n==1
