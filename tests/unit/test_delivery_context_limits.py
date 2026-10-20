"""A role's quoted context ceiling applies to the complete selected context."""
import pytest
from tests.unit.test_delivery_handoffs import delivery_team,post,role
from tests.unit.test_delivery_consumers import reviewed,consumer_handoff
from tests.unit.test_delivery_packages import BASE
from tests.unit.test_product_tasks import ref
from tests.unit.test_run_attempts import submission,submit


@pytest.mark.parametrize('selection',['multiple-documents','citation'])
async def test_multiple_references_cannot_split_around_the_role_context_limit(delivery_team,selection):
    env=delivery_team;await reviewed(env);_,agent_ref=await consumer_handoff(env)
    response=await post(env.owner,'/role-contracts',dict(role(env),role_id='rc-'+'6'*16,expected_revision=1,
        recipient_username='delivery-owner',role='consumer',consumer_agent_ref=agent_ref,max_context_bytes=30),'limit-role')
    assert response.status_code==200,response.text
    response=await post(env.owner,'/role-handoffs',dict(schema_version=1,handoff_id='ho-'+'f'*16,
        role_ref=ref(response.json(),'role_id'),package_ref=ref(env.package,'package_id'),outputs=[env.output],minimum_context='C'*24),'limited-handoff')
    assert response.status_code==200,response.text
    grant=response.json()
    response=await post(env.owner,'/handoff-acks',dict(schema_version=1,ack_id='ha-'+'f'*16,handoff_ref=ref(grant,'handoff_id')),'limited-ack')
    assert response.status_code==200,response.text
    documents=[]
    for digit in ['9','a']:
        command=dict(schema_version=2,document_id='doc-'+digit*16,project_revision=1,title='28 byte controlled context '+digit,
            handoff_ref=ref(grant,'handoff_id'),output_ref=env.output,format='text')
        response=await post(env.owner,'/handoff-documents',command,'limited-document-'+digit)
        assert response.status_code==200,response.text
        documents.append(response.json())
    if selection=='multiple-documents':
        context=dict(document_refs=[ref(d,'document_id') for d in documents],citation_refs=[])
    else:
        doc=documents[0];block=doc['blocks'][0]
        response=await post(env.owner,'/citations',dict(schema_version=1,citation_id='cit-'+'b'*16,expected_revision=0,
            project_revision=1,locator=dict(document_ref=ref(doc,'document_id'),block_id=block['block_id'],start=block['start'],end=block['end'],quote_digest=block['quote_digest'])),'limited-citation')
        assert response.status_code==200,response.text
        context=dict(document_refs=[],citation_refs=[ref(response.json(),'citation_id')])
    response=await submit(env.owner,submission(env.task,attempt_id='ra-'+'c'*16,**context),'limited-attempt')
    assert response.status_code==200,response.text
    selected=dict(schema_version=1,attempt_ref=ref(response.json(),'attempt_id'),agent_ref=agent_ref)
    before=env.service.projects.store.log_path.read_bytes();calls=env.adapter.n
    response=await post(env.owner,'/execution-previews',selected,'limited-preview')
    assert response.status_code==403,response.text
    assert response.json()['reason']=='handoff_context_limit'
    # Graph admission uses the same combined-context gate, including documents
    # added implicitly by exact citation references.
    response=await post(env.owner,'/task-graphs',dict(schema_version=1,graph_id='tg-'+'d'*16,expected_revision=0,
        project_revision=1,title='No split context bypass',nodes=[dict(node_id='A',task_ref=ref(env.task,'product_task_id'),
            agent_ref=agent_ref,**context)]),'limited-graph')
    assert response.status_code==403 and response.json()['reason']=='handoff_context_limit',response.text
    assert env.service.projects.store.log_path.read_bytes()==before and env.adapter.n==calls
