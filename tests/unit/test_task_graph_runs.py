"""Graph preflight is read-only; explicit graph execution follows diamond edges."""
import asyncio
import copy
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_task_graph_definitions import command
from tests.unit.test_source_grants import PID

def graph_ref(v):return {k:v[k] for k in ('graph_id','revision','digest','source_seq')}


async def test_graph_preflight_binds_current_environment_and_allocated_limits_without_execution(running_product,monkeypatch):
    env=running_product;base=f'/api/v1/projects/{PID}'
    saved=await env.client.post(base+'/task-graphs',json=command(env),headers={'Idempotency-Key':'graph-for-preview'});assert saved.status_code==200
    async def forbidden(*args,**kwargs):raise AssertionError('graph preflight contacted provider')
    monkeypatch.setattr(env.adapter,'ping',forbidden);monkeypatch.setattr(env.adapter,'chat',forbidden)
    before=env.app.service.projects.store.log_path.read_bytes()
    preview=await env.client.post(base+'/task-graph-previews',json=dict(schema_version=1,graph_ref=graph_ref(saved.json())))
    assert preview.status_code==200,preview.text
    p=preview.json();assert p['status']=='preflight_checked' and p['graph_ref']==graph_ref(saved.json()) and not p['execution_authorized'] and not p['automatic_dispatch']
    assert p['allocated_budget']==dict(input_tokens=80000,output_tokens=16000,tool_calls=80,cost_microyuan=4000000)
    assert len(p['confirmation_digest'])==64 and len(p['nodes'])==4 and preview.headers['cache-control']=='no-store'
    assert before==env.app.service.projects.store.log_path.read_bytes() and not env.app.service._queues and not env.app.service._engines and env.adapter.n==0


@pytest.mark.parametrize('case',['allocated-over-budget','stale-graph'])
async def test_graph_preflight_refuses_unallocatable_or_changed_definition_without_dispatch(running_product,case):
    env=running_product;base=f'/api/v1/projects/{PID}';body=command(env)
    if case=='allocated-over-budget':body['limits']=dict(cost_microyuan=1000000)
    saved=await env.client.post(base+'/task-graphs',json=body,headers={'Idempotency-Key':'graph-budget-definition'});assert saved.status_code==200
    reference=graph_ref(saved.json())
    if case=='stale-graph':
        r=await env.client.post(base+'/task-graphs',json=dict(body,expected_revision=1,title='new graph'),headers={'Idempotency-Key':'new-graph-before-preview'});assert r.status_code==200
    before=env.app.service.projects.store.log_path.read_bytes()
    preview=await env.client.post(base+'/task-graph-previews',json=dict(schema_version=1,graph_ref=reference))
    assert preview.status_code==(422 if case=='allocated-over-budget' else 409),preview.text
    assert before==env.app.service.projects.store.log_path.read_bytes() and env.adapter.n==0 and not env.app.service._queues


async def test_explicit_graph_run_completes_each_node_once_and_reconciles_original_key(running_product):
    env=running_product;base=f'/api/v1/projects/{PID}';saved=await env.client.post(base+'/task-graphs',json=command(env),headers={'Idempotency-Key':'graph-for-run'});assert saved.status_code==200
    preview=await env.client.post(base+'/task-graph-previews',json=dict(schema_version=1,graph_ref=graph_ref(saved.json())));assert preview.status_code==200,preview.text
    body=dict(schema_version=1,graph_run_id='gr-'+'b'*16,graph_ref=graph_ref(saved.json()),confirmation_digest=preview.json()['confirmation_digest'],confirm_host_boundary=False)
    first,second=await asyncio.gather(*(env.client.post(base+'/task-graph-runs',json=body,headers={'Idempotency-Key':'graph-run-once'}) for _ in range(2)))
    assert first.status_code==second.status_code==200,first.text
    assert first.json()['graph_run_id']==second.json()['graph_run_id']==body['graph_run_id']
    async with asyncio.timeout(40):
        while True:
            r=await env.client.get(base+'/task-graph-runs/'+body['graph_run_id']);assert r.status_code==200,r.text
            result=r.json()
            if result['status'] in ('completed','failed','cancelled','interrupted','unknown'):break
            await asyncio.sleep(.02)
    assert result['status']=='completed' and {n['node_id'] for n in result['nodes']}=={'A','B','C','D'}
    assert all(n['status']=='completed' for n in result['nodes']) and env.adapter.n==4
    assert result['business_acceptance']=='not_evaluated'
    calls=env.adapter.n;before=env.app.service.projects.store.log_path.read_bytes()
    bykey=await env.client.get(base+'/task-graph-run-submissions/by-key',params=dict(key='graph-run-once'))
    repeat=await env.client.post(base+'/task-graph-runs',json=body,headers={'Idempotency-Key':'graph-run-once'})
    assert bykey.status_code==repeat.status_code==200 and bykey.json()['status']=='committed'
    assert bykey.json()['graph_run']['graph_run_id']==body['graph_run_id'] and env.adapter.n==calls
    assert before==env.app.service.projects.store.log_path.read_bytes()
