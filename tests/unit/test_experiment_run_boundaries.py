"""Budget, frozen configuration, stop-before-dispatch and cold observation boundaries."""
import copy
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_runs import BASE,RID,run_selection,start,finished
from tests.unit.test_regression_dataset_api import reference


@pytest.mark.parametrize('field',['input_tokens','output_tokens','tool_calls','cost_microyuan'])
async def test_each_budget_dimension_must_cover_original_allocation(running_product,field):
    env=running_product;body=await run_selection(env);store=env.app.service.projects.store
    before=store.log_path.read_bytes();calls=env.adapter.n
    body=copy.deepcopy(body);body['budget_grant'][field]=0
    r=await env.client.post(BASE+'/experiment-run-previews',json=body)
    assert r.status_code==422,r.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_changed_configuration_and_wrong_funding_confirmation_never_register(running_product):
    env=running_product;body=await run_selection(env);store=env.app.service.projects.store
    r=await env.client.post(BASE+'/experiment-run-previews',json=body);assert r.status_code==200,r.text
    command=dict(body,confirmed=True,confirmation_digest=r.json()['confirmation_digest'])
    before=store.log_path.read_bytes();calls=env.adapter.n
    r=await env.client.post(BASE+'/experiment-runs',json=dict(command,confirmation_digest='0'*64),headers={'Idempotency-Key':'wrong-funding-digest'})
    assert r.status_code==409,r.text
    env.app.service.ctx.settings.llm.temperature=.123
    r=await env.client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':'changed-funded-configuration'})
    assert r.status_code==409,r.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def register_only(env,monkeypatch):
    from pyharness.errors import raise_code
    body=await run_selection(env);projects=env.app.service.projects
    r=await env.client.post(BASE+'/experiment-run-previews',json=body);assert r.status_code==200,r.text
    command=dict(body,confirmed=True,confirmation_digest=r.json()['confirmation_digest'])
    async def hold(*a,**k):raise_code('PERS-202',reason='synthetic_registered_only')
    with monkeypatch.context() as patch:
        patch.setattr(projects,'start_task_graph',hold)
        r=await env.client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':'fund-then-stop'})
        assert r.status_code==503,r.text
    r=await env.client.get(BASE+'/experiment-runs/'+RID);assert r.status_code==200,r.text
    return command,r.json()


@pytest.mark.parametrize('archived',[False,True])
async def test_cancel_before_dispatch_blocks_resumption_and_generic_graph_bypass(running_product,monkeypatch,archived):
    from tests.unit.test_source_grants import PID
    env=running_product;command,run=await register_only(env,monkeypatch);calls=env.adapter.n
    if archived:
        r=await env.client.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=1,name='Archived experimental project',archived=True),headers={'Idempotency-Key':'archive-funded-project'})
        assert r.status_code==200,r.text
    cancel=dict(schema_version=1,cancellation_id='ecx-'+'2'*16,experiment_run_ref=reference(run,'experiment_run_id'))
    r=await env.client.post(BASE+'/experiment-cancellations',json=cancel,headers={'Idempotency-Key':'cancel-before-dispatch'})
    assert r.status_code==200 and r.json()['cancel_requested'],r.text
    before=env.app.service.projects.store.log_path.read_bytes()
    r=await env.client.post(BASE+'/experiment-cancellations',json=cancel,headers={'Idempotency-Key':'cancel-before-dispatch'})
    assert r.status_code==200,r.text
    r=await env.client.post(BASE+'/experiment-run-resume-previews',json=dict(schema_version=1,experiment_run_ref=cancel['experiment_run_ref']))
    assert r.status_code in (403,409),r.text
    r=await env.client.post(BASE+'/task-graph-runs',json=run['graph_command'],headers={'Idempotency-Key':'generic-funding-bypass'})
    assert r.status_code==403,r.text
    assert env.adapter.n==calls and env.app.service.projects.store.log_path.read_bytes()==before
    r=await env.client.get(BASE+'/experiment-runs/'+RID+'/control')
    assert r.status_code==200 and r.json()['graph_run_ref'] is None and r.json()['cancel_requested'],r.text


async def test_completed_cold_read_and_duplicate_funding_do_not_recreate_owner_or_write(running_product):
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    import httpx
    env=running_product;body=await run_selection(env);command,receipt=await start(env,body);await finished(env)
    cfg=env.app.service.ctx.settings;calls=env.adapter.n;root=env.app.service.storage_root
    await env.app.service.shutdown();journals={p:p.read_bytes() for p in root.rglob('*.jsonl')}
    cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            r=await client.get(BASE+'/experiment-runs/'+RID)
            assert r.status_code==200 and r.json()['status']=='completed' and not r.json()['scheduler_active'],r.text
            assert cold.service.projects.store._store is None
            r=await client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':'start-experiment-once'})
            assert r.status_code==200 and r.json()['status']=='completed',r.text
            assert cold.service.projects.store._store is None and cold.service.projects.store._lease is None
            assert not getattr(cold.service,'_task_graph_owners',{}) and not cold.service._engines and not cold.service._queues
            assert all(p.read_bytes()==raw for p,raw in journals.items()) and env.adapter.n==calls
    finally:await cold.service.shutdown()


async def test_lost_funding_commit_ack_never_dispatches_and_reopen_can_observe(running_product,monkeypatch):
    from pyharness.errors import raise_code
    env=running_product;body=await run_selection(env);store=env.app.service.projects.store;verify=store._verify_commit
    r=await env.client.post(BASE+'/experiment-run-previews',json=body);assert r.status_code==200,r.text
    command=dict(body,confirmed=True,confirmation_digest=r.json()['confirmation_digest']);calls=env.adapter.n
    def lose(log,event):
        verify(log,event)
        if event.payload.get('resource_kind')=='experiment_run':raise_code('PERS-202',reason='synthetic_funding_ack_lost')
    with monkeypatch.context() as patch:
        patch.setattr(store,'_verify_commit',lose)
        r=await env.client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':'unknown-funded-ack'})
        assert r.status_code==503,r.text
    assert store._failed and env.adapter.n==calls and not getattr(env.app.service,'_task_graph_owners',{})
    r=await env.client.get(BASE+'/experiment-runs/'+RID)
    assert r.status_code==200 and r.json()['status']=='registered_not_dispatched',r.text
    r=await env.client.post(BASE+'/experiment-run-resume-previews',json=dict(schema_version=1,experiment_run_ref=reference(r.json(),'experiment_run_id')))
    assert r.status_code==503,r.text
