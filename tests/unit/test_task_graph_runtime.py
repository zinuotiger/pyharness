"""Real queue admission, concurrency, durable cancellation and cold read checks."""
import asyncio,copy
import pytest,httpx
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_task_graph_definitions import command
from tests.unit.test_task_graph_runs import graph_ref
from tests.unit.test_source_grants import PID,GID

BASE=f'/api/v1/projects/{PID}'


async def selection(env,suffix='b'):
    r=await env.client.post(BASE+'/task-graphs',json=command(env),headers={'Idempotency-Key':'runtime-graph'});assert r.status_code==200,r.text
    preview=await env.client.post(BASE+'/task-graph-previews',json=dict(schema_version=1,graph_ref=graph_ref(r.json())));assert preview.status_code==200,preview.text
    return dict(schema_version=1,graph_run_id='gr-'+suffix*16,graph_ref=graph_ref(r.json()),confirmation_digest=preview.json()['confirmation_digest'])


async def terminal(env,ident):
    async with asyncio.timeout(40):
        while True:
            r=await env.client.get(BASE+'/task-graph-runs/'+ident);assert r.status_code==200,r.text
            if r.json()['status'] in ('completed','failed','cancelled','interrupted','unknown'):return r.json()
            await asyncio.sleep(.02)


async def test_diamond_holds_two_siblings_and_never_runs_join_before_both_confirm(running_product):
    env=running_product;body=await selection(env);entered={key:asyncio.Event() for key in ('B','C')};release={key:asyncio.Event() for key in entered}
    async def hold(ctx,response):
        receipt=env.app.service._engines[ctx.session.sid].platform_runtime.product_execution.receipt
        owner=env.app.service._task_graph_owners[body['graph_run_id']]
        node=next(n['node_id'] for n in owner.authority['nodes'] if n['execution_id']==receipt['execution_id'])
        if node in entered:entered[node].set();await release[node].wait()
    env.controls.after_chat=hold
    try:
        r=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'diamond-held'});assert r.status_code==200,r.text
        async with asyncio.timeout(30):await asyncio.gather(*(v.wait() for v in entered.values()))
        owner=env.app.service._task_graph_owners[body['graph_run_id']];assert owner.active=={'B','C'} and owner.completed=={'A'} and env.adapter.n==3
        release['B'].set()
        async with asyncio.timeout(20):
            while 'B' not in owner.completed:await asyncio.sleep(.02)
        assert 'D' not in owner.active and env.adapter.n==3
        release['C'].set();result=await terminal(env,body['graph_run_id'])
        assert result['status']=='completed' and env.adapter.n==4
    finally:
        for event in release.values():event.set()


@pytest.mark.parametrize('withdraw',[False,True])
async def test_graph_cancel_freezes_admission_and_stops_actual_child_even_after_source_withdrawal(running_product,withdraw):
    env=running_product;body=await selection(env);entered=asyncio.Event();release=asyncio.Event()
    async def hold(ctx,response):entered.set();await release.wait()
    env.controls.after_chat=hold
    try:
        r=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'cancel-held-graph'});assert r.status_code==200,r.text
        async with asyncio.timeout(20):await entered.wait()
        reference={k:r.json()[k] for k in ('graph_run_id','revision','digest','source_seq')}
        if withdraw:
            revoked=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'graph-source-withdrawn'});assert revoked.status_code==200
        cancel=dict(schema_version=1,cancellation_id='gc-'+'c'*16,graph_run_ref=reference)
        first=await env.client.post(BASE+'/task-graph-cancellations',json=cancel,headers={'Idempotency-Key':'cancel-graph-original'});assert first.status_code==200,first.text
        second=await env.client.post(BASE+'/task-graph-cancellations',json=cancel,headers={'Idempotency-Key':'cancel-graph-original'});assert second.status_code==200 and first.json()==second.json()
        release.set();owner=env.app.service._task_graph_owners[body['graph_run_id']]
        assert owner.closed and env.adapter.n==1
        resources,_=env.app.service.projects.store._cancellation_state()
        assert sum('execution_authorization' in row for row in resources.values())==1
        for queue in env.app.service._queues.values():assert queue.status().running is None and not queue.status().waiting
        if not withdraw:assert (await terminal(env,body['graph_run_id']))['status']=='cancelled'
        else:assert (await env.client.get(BASE+'/task-graph-runs/'+body['graph_run_id'])).status_code==403
    finally:release.set()


async def test_unconfirmed_graph_authorization_never_launches_and_original_key_does_not_replay(running_product,monkeypatch):
    env=running_product;body=await selection(env);store=env.app.service.projects.store;verify=store._verify_commit
    def lost_ack(log,event):
        verify(log,event)
        if event.payload.get('resource_kind')=='task_graph_run':raise PyHError('PERS-202')
    monkeypatch.setattr(store,'_verify_commit',lost_ack)
    first=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'graph-lost-ack'});assert first.status_code==503,first.text
    monkeypatch.setattr(store,'_verify_commit',verify)
    again=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'graph-lost-ack'});assert again.status_code==200,again.text
    assert again.json()['status']=='interrupted' and env.adapter.n==0 and not env.app.service._queues


async def test_cold_graph_observation_reads_original_nodes_with_zero_writer_or_dispatch(running_product,monkeypatch):
    env=running_product;body=await selection(env)
    r=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'graph-cold-original'});assert r.status_code==200,r.text
    assert (await terminal(env,body['graph_run_id']))['status']=='completed'
    cfg=copy.deepcopy(env.app.service.ctx.settings);path=env.app.service.projects.store.log_path
    await env.app.service.shutdown();before=path.read_bytes();cold=DesktopApp(cli.assemble_ctx(cfg))
    def forbidden(*args,**kwargs):raise AssertionError('cold graph acquired a writer')
    monkeypatch.setattr(cold.service.projects.store,'_open',forbidden)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            observed=await client.get(BASE+'/task-graph-runs/'+body['graph_run_id']);assert observed.status_code==200,observed.text
            repeat=await client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'graph-cold-original'});assert repeat.status_code==200,repeat.text
            assert observed.json()['status']==repeat.json()['status']=='completed' and not repeat.json()['scheduler_active']
        assert before==path.read_bytes() and not cold.service._queues and not cold.service._engines and cold.service.projects.store._lease is None and env.adapter.n==4
    finally:await cold.service.shutdown()


async def test_reserved_graph_node_ids_cannot_be_consumed_by_ordinary_attempt_or_execution(running_product):
    from pyharness.application.task_graph_run_contracts import StartTaskGraph
    from tests.unit.test_run_attempts import submission
    env=running_product;body=await selection(env);projects=env.app.service.projects
    authority,first=await projects.store.authorize_task_graph_run(PID,StartTaskGraph.model_validate(body),command_id='reserve-only-graph',authorize=lambda:projects._authorize(write=True),describe=projects._describe_graph_execution)
    assert first;node=authority['nodes'][0];before=projects.store.log_path.read_bytes()
    ordinary=submission(env.task);ordinary['attempt_id']=node['attempt_id']
    r=await env.client.post(BASE+'/run-attempts',json=ordinary,headers={'Idempotency-Key':'steal-graph-attempt'});assert r.status_code==403,r.text
    preview=await projects.execution_preview(PID,env.selection)
    with pytest.raises(PyHError,match='GRD-403'):
        await projects.start_execution(PID,dict(env.selection,execution_id=node['execution_id'],confirmation_digest=preview['confirmation_digest']),'steal-graph-execution')
    assert before==projects.store.log_path.read_bytes() and env.adapter.n==0 and not env.app.service._queues


async def test_unconfirmed_node_dispatch_never_creates_attempt_queue_or_model(running_product,monkeypatch):
    env=running_product;body=await selection(env);store=env.app.service.projects.store;verify=store._verify_commit
    def lost_ack(log,event):
        verify(log,event)
        if event.payload.get('resource_kind')=='task_graph_dispatch':raise PyHError('PERS-202')
    monkeypatch.setattr(store,'_verify_commit',lost_ack)
    first=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'dispatch-lost-ack'});assert first.status_code in (200,503)
    owner=env.app.service._task_graph_owners[body['graph_run_id']]
    async with asyncio.timeout(20):await owner.task
    assert owner.closed and env.adapter.n==0 and not env.app.service._queues and not env.app.service._engines
    rows,_,_=await store._execution_read_state()
    assert sum('task_graph_dispatch' in row for row in rows.values())==1 and sum('run_attempt' in row for row in rows.values())==1
    again=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'dispatch-lost-ack'});assert again.status_code==200 and again.json()['status']=='interrupted'


@pytest.mark.parametrize('running_product',[dict(raw=b'large captured context\r\n'*3000)],indirect=True)
async def test_graph_node_cannot_bypass_original_large_context_reader_requirement(running_product):
    env=running_product;body=await selection(env)
    r=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'graph-no-context-reader'});assert r.status_code==200,r.text
    owner=env.app.service._task_graph_owners[body['graph_run_id']]
    async with asyncio.timeout(30):await owner.task
    assert owner.error=='GRD-403' and env.adapter.n==0 and not env.app.service._queues


@pytest.mark.parametrize('archived',[False,True])
async def test_graph_minimal_control_and_cancel_original_key_remain_available_without_source_bodies(running_product,archived):
    from pyharness.application.task_graph_run_contracts import StartTaskGraph
    env=running_product;body=await selection(env);p=env.app.service.projects
    run,first=await p.store.authorize_task_graph_run(PID,StartTaskGraph.model_validate(body),command_id='control-only-graph',authorize=lambda:p._authorize(write=True),describe=p._describe_graph_execution)
    revoked=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-control-graph'});assert revoked.status_code==200
    if archived:
        updated=await env.client.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=1,name='Archived graph',archived=True),headers={'Idempotency-Key':'archive-graph-project'});assert updated.status_code==200,updated.text
    path=BASE+'/task-graph-runs/'+run['graph_run_id'];control=await env.client.get(path+'/control');assert control.status_code==200,control.text
    assert control.json()['control_only'] and 'nodes' not in control.json() and 'environment' not in control.text and env.adapter.n==0
    before=p.store.log_path.read_bytes();denied=await env.client.get(path);assert denied.status_code==403 and p.store.log_path.read_bytes()==before
    cancel=dict(schema_version=1,cancellation_id='gc-'+'d'*16,graph_run_ref=control.json()['graph_run_ref'])
    saved=await env.client.post(BASE+'/task-graph-cancellations',json=cancel,headers={'Idempotency-Key':'cancel-source-withdrawn-graph'});assert saved.status_code==200,saved.text
    before=p.store.log_path.read_bytes();bykey=await env.client.get(BASE+'/task-graph-cancellation-submissions/by-key',params={'key':'cancel-source-withdrawn-graph'})
    assert bykey.status_code==200 and bykey.json()['status']=='committed' and bykey.json()['cancellation']['graph_run_ref']==control.json()['graph_run_ref']
    assert before==p.store.log_path.read_bytes() and env.adapter.n==0 and not env.app.service._queues


async def test_graph_cancel_blob_false_ack_never_appends_a_dangling_control_fact(running_product,monkeypatch):
    from pyharness.application.task_graph_run_contracts import StartTaskGraph
    from pyharness.core.tenant_settings import TenantSettingsStore
    env=running_product;body=await selection(env);p=env.app.service.projects
    run,first=await p.store.authorize_task_graph_run(PID,StartTaskGraph.model_validate(body),command_id='cancel-false-ack-root',authorize=lambda:p._authorize(write=True),describe=p._describe_graph_execution)
    before=p.store.log_path.read_bytes();write=TenantSettingsStore._atomic_write
    def false_ack(path,data,*args,**kwargs):
        if path.parent==p.store.root/'blobs':return
        return write(path,data,*args,**kwargs)
    monkeypatch.setattr(TenantSettingsStore,'_atomic_write',staticmethod(false_ack))
    response=await env.client.post(BASE+'/task-graph-cancellations',json=dict(schema_version=1,cancellation_id='gc-'+'e'*16,graph_run_ref={k:run[k] for k in ('graph_run_id','revision','digest','source_seq')}),headers={'Idempotency-Key':'cancel-missing-blob'})
    assert response.status_code==503,response.text
    assert p.store.log_path.read_bytes()==before and env.adapter.n==0 and not env.app.service._queues


async def test_unconfirmed_graph_stop_still_signals_exact_owned_queue_without_claiming_commit(running_product,monkeypatch):
    from pyharness.core.tenant_settings import TenantSettingsStore
    env=running_product;body=await selection(env);entered=asyncio.Event();release=asyncio.Event()
    async def hold(ctx,response):entered.set();await release.wait()
    env.controls.after_chat=hold
    try:
        started=await env.client.post(BASE+'/task-graph-runs',json=body,headers={'Idempotency-Key':'unconfirmed-stop-root'});assert started.status_code==200,started.text
        async with asyncio.timeout(20):await entered.wait()
        p=env.app.service.projects;before=p.store.log_path.read_bytes();write=TenantSettingsStore._atomic_write
        def false_ack(path,data,*args,**kwargs):
            if path.parent==p.store.root/'blobs':return
            return write(path,data,*args,**kwargs)
        monkeypatch.setattr(TenantSettingsStore,'_atomic_write',staticmethod(false_ack))
        command=dict(schema_version=1,cancellation_id='gc-'+'f'*16,graph_run_ref={k:started.json()[k] for k in ('graph_run_id','revision','digest','source_seq')})
        stopped=await env.client.post(BASE+'/task-graph-cancellations',json=command,headers={'Idempotency-Key':'lost-stop-no-false-success'})
        assert stopped.status_code==503 and p.store.log_path.read_bytes()==before and env.adapter.n==1
        assert env.app.service._task_graph_owners[body['graph_run_id']].closed
        for queue in env.app.service._queues.values():assert queue.status().running is None and not queue.status().waiting
    finally:release.set()
