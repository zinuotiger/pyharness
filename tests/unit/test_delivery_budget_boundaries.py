"""Reservations cannot be spent twice or promoted from a human review grant."""
import asyncio
import pytest
from tests.unit.test_delivery_handoffs import delivery_team,handoff,ack,post
from tests.unit.test_delivery_consumers import reviewed,consumer_handoff,document
from tests.unit.test_delivery_packages import BASE
from tests.unit.test_product_tasks import ref
from tests.unit.test_run_attempts import submission,submit


async def test_concurrent_previews_cannot_double_spend_parent_allowance(delivery_team):
    env=delivery_team;await reviewed(env)
    grant,agent_ref=await consumer_handoff(env,budget=dict(input_tokens=12000,output_tokens=1000,tool_calls=1,cost=.1))
    response=await document(env,grant);assert response.status_code==200,response.text
    doc=response.json();commands=[]
    for digit in ['d','e']:
        response=await submit(env.owner,submission(env.task,attempt_id='ra-'+digit*16,document_refs=[ref(doc,'document_id')]),'attempt-'+digit)
        assert response.status_code==200,response.text
        selection=dict(schema_version=1,attempt_ref=ref(response.json(),'attempt_id'),agent_ref=agent_ref)
        response=await post(env.owner,'/execution-previews',selection,'preview-'+digit)
        assert response.status_code==200,response.text
        commands.append(dict(selection,execution_id='ex-'+digit*16,confirmation_digest=response.json()['confirmation_digest']))
    calls=env.adapter.n
    responses=await asyncio.gather(*(post(env.owner,'/executions',command,'start-'+str(i)) for i,command in enumerate(commands)))
    assert sorted(r.status_code for r in responses)==[200,403],[r.text for r in responses]
    rejected=next(r for r in responses if r.status_code==403)
    assert rejected.json()['reason']=='handoff_parent_budget_exhausted'
    accepted=next(r.json() for r in responses if r.status_code==200)
    for _ in range(300):
        response=await env.owner.get(BASE+'/executions/'+accepted['execution_id'])
        if response.json()['status'] in ('completed','failed','cancelled'):break
        await asyncio.sleep(.01)
    assert response.json()['status']=='completed' and env.adapter.n==calls+1
    resources,_,_=await env.service.projects.store._execution_read_state()
    leases=[r['handoff_budget'] for r in resources.values() if 'handoff_budget' in r]
    assert len(leases)==1 and leases[0]['observed']['input_tokens']==100 and leases[0]['observed']['output_tokens']==20
    assert leases[0]['observed']['cost_microyuan']>0
    assert leases[0]['capacity']['input_tokens']==19900
    # Completion releases no reserved ceiling: another explicit new attempt
    # cannot reset the same parent's remaining allowance under a new key.
    other=next(c for c in commands if c['execution_id']!=accepted['execution_id'])
    response=await post(env.owner,'/executions',other,'different-key-no-refund')
    assert response.status_code==403 and env.adapter.n==calls+1


async def test_reviewer_document_does_not_authorize_any_agent_execution(delivery_team):
    env=delivery_team;grant=await handoff(env,'delivery-owner');await ack(env,env.owner)
    response=await document(env,grant);assert response.status_code==200,response.text
    response=await submit(env.owner,submission(env.task,attempt_id='ra-'+'d'*16,document_refs=[ref(response.json(),'document_id')]),'review-only-attempt')
    assert response.status_code==200,response.text
    before=env.service.projects.store.log_path.read_bytes();calls=env.adapter.n
    selection=dict(schema_version=1,attempt_ref=ref(response.json(),'attempt_id'),agent_ref=env.selection['agent_ref'])
    response=await post(env.owner,'/execution-previews',selection,'review-not-execution')
    assert response.status_code==403,response.text
    assert response.json()['reason']=='handoff_fixed_consumer_required'
    assert env.service.projects.store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_delivery_inventory_shows_review_to_producer_without_general_team_visibility(delivery_team):
    env=delivery_team;accepted=await reviewed(env)
    owner=await env.owner.get(BASE+'/delivery-center');reviewer=await env.reviewer.get(BASE+'/delivery-center');stranger=await env.stranger.get(BASE+'/delivery-center')
    assert owner.status_code==reviewer.status_code==stranger.status_code==200
    assert owner.json()['executions'][0]['execution_id']==env.result['execution_id']
    assert stranger.json()['executions']==[]
    assert owner.json()['items']['delivery_acceptance'][0]['resource_id']==accepted['acceptance_id']
    assert reviewer.json()['items']['role_handoff'][0]['resource_id']==env.handoff['handoff_id']
    assert all(not values for values in stranger.json()['items'].values())


async def test_unknown_package_commit_can_reconcile_without_reading_private_output(delivery_team,monkeypatch):
    from pyharness.errors import raise_code
    from tests.unit.test_delivery_packages import package
    env=delivery_team;store=env.service.projects.store;verify=store._verify_commit;calls=env.adapter.n
    def lost_receipt(owner,event):
        verify(owner,event)
        if event.type=='workspace.resource_committed' and event.payload['resource_kind']=='delivery_package':
            raise_code('PERS-202',reason='synthetic_package_receipt_lost')
    monkeypatch.setattr(store,'_verify_commit',lost_receipt)
    command=dict(package(env.result,env.delivery,env.output),package_id='dp-'+'f'*16)
    response=await post(env.owner,'/delivery-packages',command,'lost-package')
    assert response.status_code==503 and store._failed,response.text
    before=store.log_path.read_bytes();journal=env.service._tenant_sessions_dir()/(env.result['session_id']+'.jsonl')
    journal.write_bytes(journal.read_bytes().replace(b'"content":"done"',b'"content":"changed after receipt"'))
    response=await env.owner.get(BASE+'/delivery-submissions/by-key',params={'key':'lost-package'})
    assert response.status_code==200,response.text
    receipt=response.json();assert receipt['status']=='committed' and receipt['receipt']['resource_id']==command['package_id']
    assert receipt['writer_requires_reopen'] and 'changed after receipt' not in response.text
    assert (await env.stranger.get(BASE+'/delivery-submissions/by-key',params={'key':'lost-package'})).json()['status']=='not_found'
    assert store.log_path.read_bytes()==before and env.adapter.n==calls
