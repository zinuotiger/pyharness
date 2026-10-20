"""A downstream output cannot keep borrowing revoked upstream review authority."""
import asyncio
from tests.unit.test_delivery_handoffs import delivery_team,post
from tests.unit.test_delivery_consumers import reviewed,consumer_handoff,document
from tests.unit.test_delivery_packages import BASE,package
from tests.unit.test_product_tasks import ref
from tests.unit.test_run_attempts import submission,submit


async def test_downstream_frozen_output_rechecks_upstream_independent_reviewer_membership(delivery_team):
    env=delivery_team;await reviewed(env);grant,agent_ref=await consumer_handoff(env)
    response=await document(env,grant);assert response.status_code==200,response.text
    response=await submit(env.owner,submission(env.task,attempt_id='ra-'+'d'*16,document_refs=[ref(response.json(),'document_id')],
        delivery_ref=ref(env.delivery,'delivery_id')),'downstream-attempt')
    assert response.status_code==200,response.text
    selection=dict(schema_version=1,attempt_ref=ref(response.json(),'attempt_id'),agent_ref=agent_ref)
    response=await post(env.owner,'/execution-previews',selection,'downstream-preview');assert response.status_code==200,response.text
    command=dict(selection,execution_id='ex-'+'e'*16,confirmation_digest=response.json()['confirmation_digest'])
    response=await post(env.owner,'/executions',command,'downstream-start');assert response.status_code==200,response.text
    for _ in range(300):
        response=await env.owner.get(BASE+'/executions/'+command['execution_id']);assert response.status_code==200,response.text
        if response.json()['status']=='completed':break
        await asyncio.sleep(.01)
    assert response.json()['status']=='completed'
    result=response.json()
    from pyharness.application.execution_effects import flush_confirmed
    await flush_confirmed(env.service.surface_mgr()._logs[result['session_id']])
    response=await env.owner.get(BASE+'/executions/'+result['execution_id']+'/delivery-outputs')
    assert response.status_code==200,response.text
    body=dict(package(result,env.delivery,response.json()['outputs'][0]['reference']),package_id='dp-'+'f'*16)
    response=await post(env.owner,'/delivery-packages',body,'downstream-package');assert response.status_code==200,response.text
    env.accounts.membership('delivery-team','delivery-reviewer','remove')
    before=env.service.projects.store.log_path.read_bytes();calls=env.adapter.n
    response=await env.owner.get(BASE+'/delivery-packages/'+body['package_id'])
    assert response.status_code==403,response.text
    assert env.service.projects.store.log_path.read_bytes()==before and env.adapter.n==calls
