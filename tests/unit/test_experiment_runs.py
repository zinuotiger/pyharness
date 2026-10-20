"""Funded experimental batches delegate to original graph queues exactly once."""
import asyncio
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_preparation import BASE,LIMITS,selection,prepared
from tests.unit.test_regression_dataset_api import reference

RID='er-'+'1'*16


async def run_selection(env):
    _,_,body=await selection(env);_,definition=await prepared(env,body,'run-definition')
    env.controls.after_chat=None
    return dict(schema_version=1,experiment_run_id=RID,experiment_ref=reference(definition,'experiment_id'),
        budget_grant=LIMITS,confirm_host_boundary=False)


async def start(env,body,key='start-experiment-once'):
    r=await env.client.post(BASE+'/experiment-run-previews',json=body);assert r.status_code==200,r.text
    command=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=True)
    r=await env.client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':key})
    assert r.status_code==200,r.text
    return command,r.json()


async def finished(env):
    async with asyncio.timeout(40):
        while True:
            r=await env.client.get(BASE+'/experiment-runs/'+RID);assert r.status_code==200,r.text
            if r.json()['status'] in {'completed','failed','cancelled','unknown','interrupted'}:return r.json()
            await asyncio.sleep(.02)


async def test_missing_budget_and_unconfirmed_start_create_no_funded_run_or_model_calls(running_product):
    env=running_product;body=await run_selection(env);store=env.app.service.projects.store
    before=store.log_path.read_bytes();calls=env.adapter.n
    no_budget={k:v for k,v in body.items() if k!='budget_grant'}
    r=await env.client.post(BASE+'/experiment-run-previews',json=no_budget)
    assert r.status_code==422,r.text
    r=await env.client.post(BASE+'/experiment-run-previews',json=body);assert r.status_code==200,r.text
    r=await env.client.post(BASE+'/experiment-runs',json=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=False),headers={'Idempotency-Key':'unconfirmed-funded-run'})
    assert r.status_code==422 and r.headers['cache-control']=='no-store',r.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_funded_pair_runs_on_original_graph_and_repeated_key_never_dispatches_twice(running_product):
    env=running_product;body=await run_selection(env);calls=env.adapter.n
    command,receipt=await start(env,body);result=await finished(env)
    assert result['status']=='completed' and len(result['nodes'])==2 and env.adapter.n==calls+2
    assert result['graph_run_ref']['graph_run_id'].startswith('gr-')
    resources,_,_=await env.app.service.projects.store._execution_read_state()
    assert sum('experiment_run' in row for row in resources.values())==1
    assert sum('task_graph_run' in row for row in resources.values())==1
    assert sum('task_graph_dispatch' in row for row in resources.values())==2
    r=await env.client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':'start-experiment-once'})
    assert r.status_code==200 and r.json()['experiment_run_id']==RID and env.adapter.n==calls+2,r.text
    assert not r.json()['automatic_dispatch']


async def test_registered_but_not_dispatched_requires_explicit_original_key_resumption(running_product,monkeypatch):
    from pyharness.errors import raise_code
    env=running_product;body=await run_selection(env);projects=env.app.service.projects;calls=env.adapter.n
    r=await env.client.post(BASE+'/experiment-run-previews',json=body);assert r.status_code==200,r.text
    command=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=True)
    async def lost(*args,**kwargs):raise_code('PERS-202',reason='synthetic_before_original_graph_dispatch')
    with monkeypatch.context() as patch:
        patch.setattr(projects,'start_task_graph',lost)
        r=await env.client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':'register-before-crash'})
        assert r.status_code==503,r.text
    r=await env.client.get(BASE+'/experiment-runs/'+RID)
    assert r.status_code==200 and r.json()['status']=='registered_not_dispatched' and env.adapter.n==calls,r.text
    r=await env.client.post(BASE+'/experiment-runs',json=command,headers={'Idempotency-Key':'register-before-crash'})
    assert r.status_code==200 and r.json()['status']=='registered_not_dispatched' and env.adapter.n==calls,r.text
    run_ref=reference(r.json(),'experiment_run_id')
    r=await env.client.post(BASE+'/experiment-run-resume-previews',json=dict(schema_version=1,experiment_run_ref=run_ref))
    assert r.status_code==200 and r.json()['status']=='confirmation_required' and env.adapter.n==calls,r.text
    r=await env.client.post(BASE+'/experiment-run-resumptions',json=dict(schema_version=1,experiment_run_ref=run_ref,
        confirmation_digest=r.json()['confirmation_digest'],confirmed=True),headers={'Idempotency-Key':'explicit-resume'})
    assert r.status_code==200,r.text
    result=await finished(env);assert result['status']=='completed' and env.adapter.n==calls+2


async def test_cancellation_uses_only_control_metadata_after_source_is_withdrawn(running_product):
    from tests.unit.test_source_grants import GID
    env=running_product;body=await run_selection(env);entered=asyncio.Event();release=asyncio.Event()
    async def slow(ctx,result):entered.set();await release.wait()
    env.controls.after_chat=slow
    try:
        _,receipt=await start(env,body);await asyncio.wait_for(entered.wait(),15)
        r=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-running-experiment-source'})
        assert r.status_code==200,r.text
        r=await env.client.post(BASE+'/experiment-cancellations',json=dict(schema_version=1,cancellation_id='ecx-'+'1'*16,
            experiment_run_ref=reference(receipt,'experiment_run_id')),headers={'Idempotency-Key':'cancel-experiment'})
        assert r.status_code==200 and r.json()['cancel_requested'] and not r.json()['automatic_dispatch'],r.text
        r=await env.client.get(BASE+'/experiment-runs/'+RID+'/control')
        assert r.status_code==200 and r.json()['cancel_requested'] and r.json()['control_only'],r.text
        r=await env.client.get(BASE+'/experiment-runs/'+RID)
        assert r.status_code==403 and 'record_canonical' not in r.text,r.text
    finally:release.set()
