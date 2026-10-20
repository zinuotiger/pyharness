"""Business verdicts and DAG admission consume the same current delivery facts."""
import asyncio
from tests.unit.test_delivery_handoffs import delivery_team,post
from tests.unit.test_delivery_consumers import reviewed,consumer_handoff,document
from tests.unit.test_delivery_packages import BASE
from tests.unit.test_product_tasks import ref


async def test_execution_business_view_tracks_independent_review_then_supersession(delivery_team):
    env=delivery_team;await reviewed(env)
    response=await env.owner.get(BASE+'/executions/'+env.result['execution_id'])
    assert response.status_code==200,response.text
    assert response.json()['business_acceptance']=='passed'
    command=dict(schema_version=1,package_id='dp-'+'e'*16,project_revision=1,execution_ref=ref(env.result,'execution_id'),
        delivery_ref=ref(env.delivery,'delivery_id'),bindings=[dict(item_id='report',outputs=[env.output])])
    response=await post(env.owner,'/delivery-packages',command,'new-frozen-package');assert response.status_code==200,response.text
    response=await env.owner.get(BASE+'/executions/'+env.result['execution_id'])
    assert response.status_code==200 and response.json()['business_acceptance']=='not_evaluated'
    response=await env.owner.get(BASE+'/delivery-acceptances/av-'+'4'*16)
    assert response.status_code==409,response.text


async def graph(env,agent_ref,doc,key='consumer-graph'):
    command=dict(schema_version=1,graph_id='tg-'+'f'*16,expected_revision=0,project_revision=1,title='Consume fixed handoff',
        concurrency=2,nodes=[dict(node_id='A',task_ref=ref(env.task,'product_task_id'),agent_ref=agent_ref,document_refs=[ref(doc,'document_id')]),
            dict(node_id='B',task_ref=ref(env.task,'product_task_id'),agent_ref=agent_ref,document_refs=[ref(doc,'document_id')],depends_on=['A'])])
    return await post(env.owner,'/task-graphs',command,key)


async def test_dag_definition_rejects_agent_outside_the_fixed_consumer_role(delivery_team):
    env=delivery_team;await reviewed(env);grant,_=await consumer_handoff(env)
    response=await document(env,grant);assert response.status_code==200,response.text
    before=env.service.projects.store.log_path.read_bytes()
    response=await graph(env,env.selection['agent_ref'],response.json())
    assert response.status_code==403,response.text
    assert response.json()['reason']=='handoff_fixed_consumer_required'
    assert env.service.projects.store.log_path.read_bytes()==before


async def test_existing_dag_scheduler_reserves_each_consuming_node_without_replaying_parent(delivery_team):
    env=delivery_team;await reviewed(env);grant,agent_ref=await consumer_handoff(env)
    response=await document(env,grant);assert response.status_code==200,response.text
    response=await graph(env,agent_ref,response.json());assert response.status_code==200,response.text
    selection=dict(schema_version=1,graph_ref=ref(response.json(),'graph_id'))
    response=await post(env.owner,'/task-graph-previews',selection,'unused-preview-key');assert response.status_code==200,response.text
    command=dict(selection,graph_run_id='gr-'+'d'*16,confirmation_digest=response.json()['confirmation_digest'],confirm_host_boundary=False)
    calls=env.adapter.n;response=await post(env.owner,'/task-graph-runs',command,'consumer-graph-start');assert response.status_code==200,response.text
    for _ in range(500):
        response=await env.owner.get(BASE+'/task-graph-runs/'+command['graph_run_id']);assert response.status_code==200,response.text
        if response.json()['status'] in ('completed','failed','cancelled','interrupted'):break
        await asyncio.sleep(.01)
    assert response.json()['status']=='completed',response.json()
    assert env.adapter.n==calls+2 and [n['status'] for n in response.json()['nodes']]==['completed','completed']
    resources,_,_=await env.service.projects.store._execution_read_state()
    assert len([r for r in resources.values() if 'handoff_budget' in r])==2
