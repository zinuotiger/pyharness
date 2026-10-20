"""Real governed file outputs, physical PDF pages and no stale-byte acceptance."""
import base64
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_product_execution_file_effects import OPTIONS,execute_writes
from tests.unit.test_delivery_packages import BASE,PACKAGES,prepare_delivered,outputs,package
from tests.unit.test_delivery_handoffs import post
from tests.unit.test_product_tasks import ref
from tests.unit.test_document_pdf import pdf_bytes


async def frozen_output(env,filename):
    selection,delivery=await prepare_delivered(env);env.selection=selection
    result,_=await execute_writes(env)
    found=await outputs(env,result)
    output=next(o['reference'] for o in found['outputs'] if o['filename']==filename)
    command=package(result,delivery,output)
    response=await env.client.post(PACKAGES,json=command,headers={'Idempotency-Key':'file-package'})
    assert response.status_code==200,response.text
    return result,response.json(),output


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_generated_physical_file_is_verified_again_after_freezing(running_product):
    env=running_product;_,saved,output=await frozen_output(env,'unchanged.txt')
    path=env.app.service.platform.root/'artifacts'/output['artifact_id']
    assert path.read_bytes()==b'synthetic-output\n'
    before=env.app.service.projects.store.log_path.read_bytes();path.write_bytes(b'changed physical bytes')
    response=await env.client.get(PACKAGES+'/'+saved['package_id'])
    assert response.status_code==409 and response.json()['reason']=='delivery_output_bytes_changed'
    assert env.app.service.projects.store.log_path.read_bytes()==before


TWO_FILES=dict(OPTIONS,script=[*OPTIONS['script'],dict(id='second-output-not-selected',name='fs.write_file',
    args=dict(path='second.txt',content='not in the selected output grant\n'))])


@pytest.mark.parametrize('running_product',[TWO_FILES],indirect=True)
async def test_reading_a_fixed_package_opens_only_the_selected_output_bytes(running_product,monkeypatch):
    import pyharness.application.execution_artifact_receipts as receipts
    env=running_product;_,saved,selected=await frozen_output(env,'unchanged.txt')
    touched=[];original=receipts.read_regular
    def observed(path,limit):
        touched.append(path.name)
        return original(path,limit)
    monkeypatch.setattr(receipts,'read_regular',observed)
    response=await env.client.get(PACKAGES+'/'+saved['package_id'])
    assert response.status_code==200,response.text
    assert touched and set(touched)=={selected['artifact_id']},touched


PDF=pdf_bytes()
PDF_OPTIONS=dict(agent=dict(sandbox_profile=dict(mode='host_approved'),permission_policy='standard'),
    script=[dict(id='write-real-pdf-output',name='exec.python_run',args=dict(code=
        "from pathlib import Path\nimport base64\nPath('delivery.pdf').write_bytes(base64.b64decode('"+base64.b64encode(PDF).decode()+"'))"))])


@pytest.mark.controlled_process
@pytest.mark.parametrize('running_product',[PDF_OPTIONS],indirect=True)
async def test_actual_generated_pdf_requires_preview_then_imports_real_physical_pages(running_product):
    env=running_product;result,saved,output=await frozen_output(env,'delivery.pdf')
    client=env.client
    response=await post(client,'/role-contracts',dict(schema_version=1,role_id='rc-'+'1'*16,expected_revision=0,
        project_revision=1,title='Read selected PDF',parent_execution_ref=ref(result,'execution_id'),recipient_username='local-operator',role='reviewer'),'pdf-role')
    assert response.status_code==200,response.text
    response=await post(client,'/role-handoffs',dict(schema_version=1,handoff_id='ho-'+'2'*16,role_ref=ref(response.json(),'role_id'),
        package_ref=ref(saved,'package_id'),outputs=[output],minimum_context='Read only the exact selected PDF.'),'pdf-handoff')
    assert response.status_code==200,response.text
    grant=response.json()
    response=await post(client,'/handoff-acks',dict(schema_version=1,ack_id='ha-'+'3'*16,handoff_ref=ref(grant,'handoff_id')),'pdf-ack')
    assert response.status_code==200,response.text
    command=dict(schema_version=2,document_id='doc-'+'4'*16,project_revision=1,title='Generated physical-page PDF',
        handoff_ref=ref(grant,'handoff_id'),output_ref=output,format='pdf')
    before=env.app.service.projects.store.log_path.read_bytes()
    response=await post(client,'/handoff-documents',command,'pdf-unconfirmed')
    assert response.status_code==409,response.text
    assert env.app.service.projects.store.log_path.read_bytes()==before
    response=await post(client,'/handoff-document-previews',command,'unused-preview-key')
    assert response.status_code==200,response.text
    preview=response.json();assert preview['page_count']==3 and preview['unlocatable_pages']==[2]
    assert env.app.service.projects.store.log_path.read_bytes()==before
    response=await post(client,'/handoff-documents',dict(command,confirmation_digest=preview['confirmation_digest']),'pdf-confirmed')
    assert response.status_code==200,response.text
    value=response.json()
    assert value['schema_version']==2 and [b['page'] for b in value['blocks']]==[1,3]
    assert 'First page' in value['blocks'][0]['text'] and value['unlocatable_pages']==[2]
    assert value['content_digest']==output['sha256']
    response=await client.get(BASE+'/documents/'+value['document_id']+'/revisions/1')
    assert response.status_code==200,response.text
    assert response.json()['blocks']==value['blocks']
