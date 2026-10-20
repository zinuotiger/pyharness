"""Selected public output reaches an actual governed consumer, within parent limits."""
import asyncio
import pytest
from tests.unit.test_delivery_handoffs import delivery_team,handoff,ack,acceptance,post,role
from tests.unit.test_delivery_packages import BASE
from tests.unit.test_product_tasks import ref
from tests.unit.test_run_attempts import submission,submit


async def reviewed(env):
    await handoff(env);await ack(env)
    response=await post(env.reviewer,'/delivery-acceptances',acceptance(env),'independent-review')
    assert response.status_code==200,response.text
    return response.json()


async def consumer_handoff(env,**changes):
    settings=dict(name='Consumer',budget=dict(input_tokens=4000,output_tokens=1000,tool_calls=1,cost=.1),max_rounds=2,timeout=30)
    settings.update(changes)
    agent=env.service.platform.save_agent(settings)
    version=env.service.platform.publish_agent(agent['agent_id'])
    agent_ref=dict(agent_id=version['agent_id'],version=version['version'],sha256=version['sha256'])
    command=dict(role(env),role_id='rc-'+'6'*16,recipient_username='delivery-owner',role='consumer',consumer_agent_ref=agent_ref)
    response=await post(env.owner,'/role-contracts',command,'consumer-role');assert response.status_code==200,response.text
    command=dict(schema_version=1,handoff_id='ho-'+'7'*16,role_ref=ref(response.json(),'role_id'),
        package_ref=ref(env.package,'package_id'),outputs=[env.output],minimum_context='Use only the selected public final output.')
    response=await post(env.owner,'/role-handoffs',command,'consumer-handoff');assert response.status_code==200,response.text
    grant=response.json()
    response=await post(env.owner,'/handoff-acks',dict(schema_version=1,ack_id='ha-'+'8'*16,handoff_ref=ref(grant,'handoff_id')),'consumer-ack')
    assert response.status_code==200,response.text
    return grant,agent_ref


async def document(env,grant):
    command=dict(schema_version=2,document_id='doc-'+'9'*16,project_revision=1,title='Selected producer output',
        handoff_ref=ref(grant,'handoff_id'),output_ref=env.output,format='text')
    return await post(env.owner,'/handoff-documents',command,'consumer-document')


async def test_actual_consumer_reads_only_frozen_public_output_and_reserves_parent_budget(delivery_team):
    env=delivery_team;await reviewed(env);grant,agent_ref=await consumer_handoff(env)
    response=await document(env,grant);assert response.status_code==200,response.text
    doc=response.json();assert doc['schema_version']==2 and doc['blocks'][0]['text']=='done' and doc['persisted'] is True
    assert doc['source']['kind']=='handoff_output' and not doc['export_authorized']
    before=env.adapter.n;messages=[];original=env.adapter.chat
    async def capture(values,tools=None,*,ctx):
        messages.append(values)
        return await original(values,tools,ctx=ctx)
    env.adapter.chat=capture
    attempt=await submit(env.owner,submission(env.task,attempt_id='ra-'+'d'*16,document_refs=[ref(doc,'document_id')]),'consumer-attempt')
    assert attempt.status_code==200,attempt.text
    selection=dict(schema_version=1,attempt_ref=ref(attempt.json(),'attempt_id'),agent_ref=agent_ref)
    response=await post(env.owner,'/execution-previews',selection,'consumer-preview');assert response.status_code==200,response.text
    command=dict(selection,execution_id='ex-'+'e'*16,confirmation_digest=response.json()['confirmation_digest'])
    response=await post(env.owner,'/executions',command,'consumer-start');assert response.status_code==200,response.text
    for _ in range(300):
        response=await env.owner.get(BASE+'/executions/'+command['execution_id']);assert response.status_code==200,response.text
        if response.json()['status'] in ('completed','failed','cancelled'):break
        await asyncio.sleep(.01)
    assert response.json()['status']=='completed' and env.adapter.n==before+1
    prompt=str(messages)
    assert 'Selected producer output' in prompt and 'done' in prompt and grant['minimum_context'] in prompt
    assert 'Inspected exact selected text' not in prompt
    resources,_,_=await env.service.projects.store._execution_read_state()
    leases=[r['handoff_budget'] for r in resources.values() if 'handoff_budget' in r]
    assert len(leases)==1 and leases[0]['execution_id']==command['execution_id']
    assert leases[0]['reserved']['input_tokens']==4000
    assert (await post(env.owner,'/executions',command,'consumer-start')).status_code==200
    assert env.adapter.n==before+1


async def test_consumer_document_cannot_replace_independent_acceptance(delivery_team):
    env=delivery_team;grant,_=await consumer_handoff(env)
    before=env.service.projects.store.log_path.read_bytes();calls=env.adapter.n
    response=await document(env,grant)
    assert response.status_code==403,response.text
    assert env.service.projects.store.log_path.read_bytes()==before and env.adapter.n==calls


@pytest.mark.parametrize('capability',['tool','memory','policy','executor'])
async def test_role_cannot_widen_parent_capability(delivery_team,capability):
    env=delivery_team
    changes={'tool':dict(allowed_tools=['fs.write_file']),'memory':dict(memory_scope='team'),
        'policy':dict(permission_policy='standard'),'executor':dict(executor='codex')}[capability]
    agent=env.service.platform.save_agent(dict(name='Invalid widened role',**changes))
    version=env.service.platform.publish_agent(agent['agent_id'])
    command=dict(role(env),role='consumer',consumer_agent_ref=dict(agent_id=version['agent_id'],version=version['version'],sha256=version['sha256']))
    before=env.service.projects.store.log_path.read_bytes()
    response=await post(env.owner,'/role-contracts',command,'widened-role')
    assert response.status_code in (403,422),response.text
    assert env.service.projects.store.log_path.read_bytes()==before
