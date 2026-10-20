"""Versioned graph definitions stay on the sole control owner with zero dispatch."""
import asyncio
import copy
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_product_tasks import ref
from tests.unit.test_source_grants import PID,GID


def command(env):
    return dict(schema_version=1,graph_id='tg-'+'a'*16,expected_revision=0,project_revision=1,title='Actual diamond definition',
        nodes=[dict(node_id=ident,task_ref=ref(env.task,'product_task_id'),agent_ref=copy.deepcopy(env.selection['agent_ref']),depends_on=parents)
            for ident,parents in [('A',[]),('B',['A']),('C',['A']),('D',['B','C'])]])


async def test_graph_definition_versions_and_original_keys_never_start_work(running_product):
    env=running_product;url=f'/api/v1/projects/{PID}/task-graphs';body=command(env)
    first,second=await asyncio.gather(*(env.client.post(url,json=body,headers={'Idempotency-Key':'graph-definition-once'}) for _ in range(2)))
    assert first.status_code==second.status_code==200,first.text
    original=first.json();assert original==second.json() and original['revision']==1 and original['persisted'] and not original['executable']
    assert first.headers['cache-control']=='no-store'
    before=env.app.service.projects.store.log_path.read_bytes()
    read=await env.client.get(url+'/'+body['graph_id']+'/revisions/1')
    bykey=await env.client.get(f'/api/v1/projects/{PID}/task-graph-submissions/by-key',params=dict(key='graph-definition-once'))
    rows=await env.client.get(url)
    assert read.status_code==rows.status_code==bykey.status_code==200
    assert read.json()['digest']==original['digest'] and bykey.json()['graph']['digest']==original['digest'] and bykey.json()['status']=='committed'
    assert rows.json()['graphs'][0]['digest']==original['digest'] and env.app.service.projects.store.log_path.read_bytes()==before
    changed=dict(body,expected_revision=1,title='Second fixed graph version')
    saved=await env.client.post(url,json=changed,headers={'Idempotency-Key':'graph-definition-r2'})
    assert saved.status_code==200 and saved.json()['revision']==2
    old=await env.client.get(url+'/'+body['graph_id']+'/revisions/1')
    assert old.json()['title']==body['title'] and old.json()['digest']==original['digest']
    assert env.adapter.n==0 and not env.app.service._queues and not env.app.service._engines


@pytest.mark.parametrize('case',['foreign-project','wrong-task-version','wrong-agent-version','withdrawn-source','cycle'])
async def test_graph_definition_rejects_unbound_inputs_without_control_append_or_dispatch(running_product,case):
    env=running_product;body=command(env);project=PID
    if case=='foreign-project':
        project='p-'+'e'*16
        r=await env.client.post('/api/v1/projects',json=dict(project_id=project,expected_revision=0,name='Other graph project'),headers={'Idempotency-Key':'graph-other-project'});assert r.status_code==200
    if case=='wrong-task-version':body['nodes'][0]['task_ref']['source_seq']+=1
    if case=='wrong-agent-version':body['nodes'][0]['agent_ref']['sha256']='f'*64
    if case=='withdrawn-source':
        r=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'graph-withdraw'});assert r.status_code==200
    if case=='cycle':body['nodes'][0]['depends_on']=['D']
    before=env.app.service.projects.store.log_path.read_bytes()
    response=await env.client.post(f'/api/v1/projects/{project}/task-graphs',json=body,headers={'Idempotency-Key':'graph-rejected'})
    assert response.status_code==(422 if case=='cycle' else 403 if case in ('foreign-project','withdrawn-source') else 409),response.text
    assert response.headers['cache-control']=='no-store'
    assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==0 and not env.app.service._queues and not env.app.service._engines


async def test_cold_graph_queries_open_no_control_writer_engine_or_queue(running_product,monkeypatch):
    import httpx
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    env=running_product;body=command(env);url=f'/api/v1/projects/{PID}/task-graphs'
    saved=await env.client.post(url,json=body,headers={'Idempotency-Key':'cold-graph-definition'});assert saved.status_code==200
    cfg=copy.deepcopy(env.app.service.ctx.settings);path=env.app.service.projects.store.log_path
    await env.app.service.shutdown();before=path.read_bytes();cold=DesktopApp(cli.assemble_ctx(cfg))
    def forbidden(*args,**kwargs):raise AssertionError('cold graph query opened a control writer')
    monkeypatch.setattr(cold.service.projects.store,'_open',forbidden)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            replies=[await client.get(url+'/'+body['graph_id']+'/revisions/1'),await client.get(url),
                await client.get(f'/api/v1/projects/{PID}/task-graph-submissions/by-key',params=dict(key='cold-graph-definition'))]
        assert all(r.status_code==200 for r in replies)
        assert not cold.service._queues and not cold.service._engines and cold.service.projects.store._lease is None and env.adapter.n==0
        assert path.read_bytes()==before
    finally:await cold.service.shutdown()
