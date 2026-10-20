"""Real execution output freezes on the original control log, without a rerun."""
import hashlib
import pytest
from tests.unit.test_product_execution_start import running_product, wait_terminal
from tests.unit.test_product_tasks import ref
from tests.unit.test_run_attempts import submission, submit
from tests.unit.test_source_grants import PID

BASE=f'/api/v1/projects/{PID}'
PACKAGES=BASE+'/delivery-packages'
DEL='del-'+'a'*16


async def prepare_delivered(env):
    response=await env.client.post(BASE+'/delivery-definitions',json=dict(schema_version=1,delivery_id=DEL,
        expected_revision=0,project_revision=1,task_ref=ref(env.task,'product_task_id'),
        items=[dict(item_id='report',title='Final report',required=True,acceptance_criteria=['Explain the captured input'],citation_refs=[])]),
        headers={'Idempotency-Key':'delivery-definition'})
    assert response.status_code==200,response.text
    delivery=response.json()
    attempt=await submit(env.client,submission(env.task,attempt_id='ra-'+'a'*16,delivery_ref=ref(delivery,'delivery_id')),'delivered-attempt')
    assert attempt.status_code==200,attempt.text
    selection=dict(env.selection,attempt_ref=ref(attempt.json(),'attempt_id'))
    return selection,delivery


async def delivered(env):
    selection,delivery=await prepare_delivered(env)
    projects=env.app.service.projects
    preview=await projects.execution_preview(PID,selection)
    command=dict(selection,execution_id='ex-'+'b'*16,confirmation_digest=preview['confirmation_digest'])
    await projects.start_execution(PID,command,'delivered-execution')
    result=await wait_terminal(projects,command['execution_id'])
    assert result['status']=='completed'
    return result,delivery


async def outputs(env,result):
    response=await env.client.get(BASE+'/executions/'+result['execution_id']+'/delivery-outputs')
    assert response.status_code==200,response.text
    return response.json()


def package(result,delivery,output):
    return dict(schema_version=1,package_id='dp-'+'c'*16,project_revision=1,
        execution_ref=ref(result,'execution_id'),delivery_ref=ref(delivery,'delivery_id'),
        bindings=[dict(item_id='report',outputs=[output])])


async def test_freeze_public_output_once_and_cold_read_without_writers(running_product):
    from pyharness.application.workspace_store import WorkspaceStore
    env=running_product;result,delivery=await delivered(env)
    found=await outputs(env,result)
    assert len(found['outputs'])==1 and found['outputs'][0]['reference']['kind']=='message'
    output=found['outputs'][0]['reference']
    assert hashlib.sha256(found['outputs'][0]['text_preview'].encode()).hexdigest()==output['sha256']
    command=package(result,delivery,output)
    response=await env.client.post(PACKAGES,json=command,headers={'Idempotency-Key':'package-one'})
    assert response.status_code==200,response.text
    saved=response.json();assert saved['persisted'] and saved['business_acceptance']=='not_evaluated'
    assert not saved['execution_authorized'] and not saved['export_authorized']
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    assert (await env.client.post(PACKAGES,json=command,headers={'Idempotency-Key':'package-one'})).json()==saved
    await store.close();fresh=WorkspaceStore(store.root,tenant_id='default');env.app.service.projects.store=fresh
    response=await env.client.get(PACKAGES+'/'+command['package_id'])
    assert response.status_code==200,response.text
    assert response.json()==saved and fresh._store is None and fresh._lease is None
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


@pytest.mark.parametrize('change',['fake-event','fake-hash','wrong-delivery','missing-required','unknown-item','foreign-session'])
async def test_fake_or_missing_output_never_commits(running_product,change):
    env=running_product;result,delivery=await delivered(env);found=await outputs(env,result)
    command=package(result,delivery,found['outputs'][0]['reference'])
    selected=command['bindings'][0]['outputs'][0]
    if change=='fake-event':selected['source_seq']+=100
    if change=='fake-hash':selected['sha256']='0'*64
    if change=='wrong-delivery':command['delivery_ref']['digest']='0'*64
    if change=='missing-required':command['bindings']=[]
    if change=='unknown-item':command['bindings'][0]['item_id']='other'
    if change=='foreign-session':selected['session_id']='s-pa-'+'0'*32
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    response=await env.client.post(PACKAGES,json=command,headers={'Idempotency-Key':'bad-package'})
    assert response.status_code in (403,409,422),response.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls
