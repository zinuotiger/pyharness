"""Explicit new attempts inherit real state without replaying old actual writes."""
import asyncio,copy,json
import httpx,pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.core import llm
from tests.conftest import ScriptedAdapter
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_product_execution_cancellation import command as stop
from tests.unit.test_product_execution_file_effects import OPTIONS,RAW,AFTER
from tests.unit.test_source_grants import PID

CONTINUABLE=dict(OPTIONS,script=[*OPTIONS['script'],dict(id='read-produced',name='fs.read_file',args=dict(path='unchanged.txt'))],
    agent=dict(sandbox_profile=dict(mode='host_approved'),budget=dict(input_tokens=100000,output_tokens=20000,tool_calls=20,cost=2)))


@pytest.fixture
async def interrupted_product(running_product):
    env=running_product;original=env.adapter.chat;entered=asyncio.Event();old_sid=None
    async def stop_before_second_request(messages,tools=None,*,ctx):
        nonlocal old_sid
        old_sid=old_sid or ctx.session.sid
        if ctx.session.sid==old_sid and env.adapter.n==1:
            entered.set();await asyncio.Event().wait()
        return await original(messages,tools,ctx=ctx)
    env.adapter.chat=stop_before_second_request
    p=env.app.service.projects;preview=await p.execution_preview(PID,env.selection)
    started=await p.start_execution(PID,dict(env.selection,execution_id='ex-'+'d'*16,confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'continuation-origin')
    async with asyncio.timeout(15):
        while not entered.is_set():
            for request in (await env.app.service.pending_approvals())['pending']:
                if request['session_id']==started['session_id']:
                    await env.app.service.decide_approval(request['approval_id'],'approve',sid=request['session_id'])
            await asyncio.sleep(.01)
    await p.cancel_execution(PID,stop(started),'stop-before-new-model');old=await wait_terminal(p,started['execution_id'])
    assert old['status']=='cancelled' and env.adapter.n==1 and not old['unknown_effects']
    cfg=copy.deepcopy(env.app.service.ctx.settings);log=env.app.service._engines[old_sid].session;path=log._persistence.path
    await env.app.service.shutdown();old_bytes=path.read_bytes()
    adapter=ScriptedAdapter(cfg.llm.model,[dict(id='new-id-old-write',name='fs.write_file',args=dict(path='unchanged.txt',content=AFTER)),
        dict(id='new-read-restored',name='fs.read_file',args=dict(path='unchanged.txt'))])
    llm.adapters[cfg.llm.model]=adapter;contexts=[];capture=adapter.chat
    async def next_context(messages,tools=None,*,ctx):
        contexts.append(copy.deepcopy(messages));return await capture(messages,tools,ctx=ctx)
    adapter.chat=next_context
    cold=DesktopApp(cli.assemble_ctx(cfg))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
        yield dict(env=env,app=cold,client=client,origin=old,old_path=path,old_bytes=old_bytes,adapter=adapter,contexts=contexts)
    await cold.service.shutdown()


@pytest.mark.parametrize('running_product',[CONTINUABLE],indirect=True)
async def test_explicit_continuation_creates_new_owned_attempt_and_keeps_old_write_once(interrupted_product,monkeypatch):
    from pathlib import Path
    from pyharness.core.tenant_settings import TenantSettingsStore
    atomic=TenantSettingsStore._atomic_write;new_writes=[]
    def counted(path,*args,**kwargs):
        if Path(path).name=='unchanged.txt':new_writes.append(str(path))
        return atomic(path,*args,**kwargs)
    monkeypatch.setattr(TenantSettingsStore,'_atomic_write',staticmethod(counted))
    item=interrupted_product;client=item['client'];app=item['app'];old=item['origin'];base=f'/api/v1/projects/{PID}/executions/'+old['execution_id']
    plan=await client.get(base+'/continuation-plan');assert plan.status_code==200,plan.text
    assert plan.json()['state_available'] and not app.service._engines and not app.service._queues and item['adapter'].n==0
    command=dict(schema_version=1,execution_ref={k:old[k] for k in ('execution_id','revision','digest','source_seq')},
        plan_digest=plan.json()['plan_digest'],attempt_id='ra-'+'b'*16,execution_id='ex-'+'b'*16,confirm_host_boundary=True)
    r=await client.post(base+'/continuations',json=command,headers={'Idempotency-Key':'continue-original-once'})
    assert r.status_code==200,r.text
    started=r.json();assert started['session_id']!=old['session_id'] and started['attempt_ref']['attempt_id']==command['attempt_id']
    async with asyncio.timeout(15):
        while True:
            for request in (await app.service.pending_approvals())['pending']:
                if request['session_id']==started['session_id']:
                    await app.service.decide_approval(request['approval_id'],'approve',sid=request['session_id'])
            queue=app.service._queues[started['session_id']];status=queue.status()
            if status.running!=started['task_id'] and started['task_id'] not in status.waiting:break
            await asyncio.sleep(.01)
    done=await wait_terminal(app.service.projects,command['execution_id'])
    assert done['status']=='completed' and done['business_acceptance']=='not_evaluated'
    assert not any(e['tool']=='fs.write_file' for e in done['effects'])
    assert any(e['tool']=='fs.read_file' and e['phase']=='returned' for e in done['effects'])
    assert AFTER.strip() in json.dumps(item['contexts'][0]) and not new_writes
    assert (item['env'].source/'unchanged.txt').read_bytes()==RAW and item['old_path'].read_bytes()==item['old_bytes']
    artifact=next(a for a in done['artifacts'] if a['filename']=='unchanged.txt')
    assert (app.service.platform.root/'artifacts'/artifact['artifact_id']).read_bytes()==AFTER.encode()
    calls=item['adapter'].n
    repeated=await client.post(base+'/continuations',json=command,headers={'Idempotency-Key':'continue-original-once'})
    assert repeated.status_code==200 and repeated.json()['session_id']==started['session_id'] and item['adapter'].n==calls
    observed=await client.get(f'/api/v1/projects/{PID}/continuation-submissions/by-key',params=dict(key='continue-original-once'))
    assert observed.status_code==200 and observed.json()['status']=='committed' and observed.json()['execution']['session_id']==started['session_id']
    assert observed.headers['cache-control']=='no-store'
    before=app.service.projects.store.log_path.read_bytes()
    different=dict(command,attempt_id='ra-'+'c'*16,execution_id='ex-'+'c'*16)
    denied=await client.post(base+'/continuations',json=different,headers={'Idempotency-Key':'same-checkpoint-new-key'})
    assert denied.status_code==409 and before==app.service.projects.store.log_path.read_bytes() and item['adapter'].n==calls


@pytest.mark.parametrize('running_product',[CONTINUABLE],indirect=True)
@pytest.mark.parametrize('case',['stale-plan','host-unconfirmed','source-withdrawn'])
async def test_rejected_continuation_creates_no_new_attempt_queue_or_model(interrupted_product,case):
    from tests.unit.test_source_grants import GID
    item=interrupted_product;client=item['client'];app=item['app'];old=item['origin'];base=f'/api/v1/projects/{PID}/executions/'+old['execution_id']
    p=(await client.get(base+'/continuation-plan')).json()
    command=dict(schema_version=1,execution_ref={k:old[k] for k in ('execution_id','revision','digest','source_seq')},
        plan_digest=p['plan_digest'],attempt_id='ra-'+'b'*16,execution_id='ex-'+'b'*16,confirm_host_boundary=True)
    if case=='stale-plan':command['plan_digest']='f'*64
    if case=='host-unconfirmed':command['confirm_host_boundary']=False
    if case=='source-withdrawn':
        revoked=await client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'continue-withdraw-source'})
        assert revoked.status_code==200,revoked.text
    control=app.service.projects.store.log_path.read_bytes()
    r=await client.post(base+'/continuations',json=command,headers={'Idempotency-Key':'rejected-continuation'})
    assert r.status_code==(409 if case=='stale-plan' else 403),r.text
    assert control==app.service.projects.store.log_path.read_bytes() and not app.service._engines and not app.service._queues and item['adapter'].n==0
    assert (item['env'].source/'unchanged.txt').read_bytes()==RAW and item['old_path'].read_bytes()==item['old_bytes']


@pytest.mark.parametrize('running_product',[CONTINUABLE],indirect=True)
async def test_second_continuation_keeps_ancestor_write_blocked_across_new_sessions(interrupted_product,monkeypatch):
    from pathlib import Path
    from pyharness.core.tenant_settings import TenantSettingsStore
    item=interrupted_product;client=item['client'];app=item['app'];old=item['origin'];base=f'/api/v1/projects/{PID}/executions/'+old['execution_id']
    adapter=item['adapter'];adapter.calls=[dict(id='chain-read-only',name='fs.read_file',args=dict(path='unchanged.txt'))]
    entered=asyncio.Event();first_chat=adapter.chat
    async def hold_before_second(messages,tools=None,*,ctx):
        if adapter.n==1:entered.set();await asyncio.Event().wait()
        return await first_chat(messages,tools,ctx=ctx)
    adapter.chat=hold_before_second
    plan=(await client.get(base+'/continuation-plan')).json()
    command=dict(schema_version=1,execution_ref={k:old[k] for k in ('execution_id','revision','digest','source_seq')},
        plan_digest=plan['plan_digest'],attempt_id='ra-'+'b'*16,execution_id='ex-'+'b'*16,confirm_host_boundary=True)
    r=await client.post(base+'/continuations',json=command,headers={'Idempotency-Key':'continue-first-chain'});assert r.status_code==200,r.text
    middle=r.json();await asyncio.wait_for(entered.wait(),10)
    await app.service.projects.cancel_execution(PID,dict(stop(middle),cancellation_id='ec-'+'b'*16),'stop-continued-before-second-model')
    middle=await wait_terminal(app.service.projects,middle['execution_id']);assert middle['status']=='cancelled' and adapter.n==1
    middle_path=app.service._engines[middle['session_id']].session._persistence.path;cfg=copy.deepcopy(app.service.ctx.settings)
    await app.service.shutdown();middle_bytes=middle_path.read_bytes()
    next_adapter=ScriptedAdapter(cfg.llm.model,[dict(id='third-session-ancestor-write',name='fs.write_file',args=dict(path='unchanged.txt',content=AFTER)),
        dict(id='third-read-current',name='fs.read_file',args=dict(path='unchanged.txt'))]);llm.adapters[cfg.llm.model]=next_adapter
    third=DesktopApp(cli.assemble_ctx(cfg));writes=[];atomic=TenantSettingsStore._atomic_write
    def count(path,*args,**kwargs):
        if Path(path).name=='unchanged.txt':writes.append(str(path))
        return atomic(path,*args,**kwargs)
    monkeypatch.setattr(TenantSettingsStore,'_atomic_write',staticmethod(count))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=third.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':third._api_token}) as c:
            base=f'/api/v1/projects/{PID}/executions/'+middle['execution_id'];r=await c.get(base+'/continuation-plan');assert r.status_code==200,r.text
            current=r.json();assert current['can_continue'],current['blockers']
            command=dict(schema_version=1,execution_ref={k:middle[k] for k in ('execution_id','revision','digest','source_seq')},
                plan_digest=current['plan_digest'],attempt_id='ra-'+'c'*16,execution_id='ex-'+'c'*16,confirm_host_boundary=True)
            r=await c.post(base+'/continuations',json=command,headers={'Idempotency-Key':'continue-second-chain'});assert r.status_code==200,r.text
            started=r.json()
            async with asyncio.timeout(15):
                while True:
                    for request in (await third.service.pending_approvals())['pending']:
                        if request['session_id']==started['session_id']:
                            await third.service.decide_approval(request['approval_id'],'approve',sid=request['session_id'])
                    state=third.service._queues[started['session_id']].status()
                    if state.running!=started['task_id'] and started['task_id'] not in state.waiting:break
                    await asyncio.sleep(.01)
            final=await wait_terminal(third.service.projects,started['execution_id'])
            assert final['status']=='completed' and not writes and next_adapter.n==2
            assert not any(e['tool']=='fs.write_file' for e in final['effects'])
            assert item['old_path'].read_bytes()==item['old_bytes'] and middle_path.read_bytes()==middle_bytes
            assert (item['env'].source/'unchanged.txt').read_bytes()==RAW
    finally:await third.service.shutdown()
