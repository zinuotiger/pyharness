"""Explicit bounded export/import, original provenance and no execution or case cloning."""
import copy
import hashlib
import json
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_source_grants import GID
from tests.unit.test_regression_dataset_api import BASE,CID,DID,saved,save_case,reference


async def dataset(env):
    body,case=await saved(env)
    selected=dict(schema_version=1,dataset_id=DID,expected_revision=0,project_revision=1,
        title='可验证导出集合',case_refs=[reference(case,'case_id')],confirmed=True)
    response=await env.client.post(BASE+'/regression-datasets',json=selected,headers={'Idempotency-Key':'transfer-dataset'})
    assert response.status_code==200,response.text
    return body,case,response.json()


async def export(env):
    response=await env.client.post(BASE+'/regression-datasets/'+DID+'/export-previews',json=dict(schema_version=1,revision=1))
    assert response.status_code==200,response.text
    preview=response.json()
    response=await env.client.post(BASE+'/regression-datasets/'+DID+'/exports',json=dict(schema_version=1,revision=1,
        confirmation_digest=preview['confirmation_digest'],confirmed=True))
    assert response.status_code==200,response.text
    return response.json()


def import_body(artifact):
    return dict(schema_version=1,dataset_id='ds-'+'2'*16,expected_revision=0,project_revision=1,
        title='明确另存集合',bundle_canonical=artifact['bundle_canonical'])


async def test_export_import_preserves_exact_old_case_version_and_commits_one_dataset_only(running_product):
    env=running_product;body,case,original=await dataset(env);calls=env.adapter.n
    response=await save_case(env,dict(body,expected_revision=1,title='新用例修订'),'newer-case-before-export')
    assert response.status_code==200,response.text
    store=env.app.service.projects.store;before=store.log_path.read_bytes()
    artifact=await export(env)
    assert hashlib.sha256(artifact['bundle_canonical'].encode()).hexdigest()==artifact['bundle_digest']
    bundle=json.loads(artifact['bundle_canonical'])
    assert bundle['dataset_ref']==reference(original,'dataset_id')
    assert bundle['cases'][0]['reference']==reference(case,'case_id') and bundle['cases'][0]['record']['revision']==1
    body=import_body(artifact)
    response=await env.client.post(BASE+'/regression-dataset-import-previews',json=body)
    assert response.status_code==200,response.text
    preview=response.json();assert preview['status']=='confirmation_required' and not preview['persisted']
    assert store.log_path.read_bytes()==before
    confirmed=dict(body,confirmed=True,confirmation_digest=preview['confirmation_digest'])
    one=await env.client.post(BASE+'/regression-dataset-imports',json=confirmed,headers={'Idempotency-Key':'import-fixed-dataset'})
    assert one.status_code==200,one.text
    two=await env.client.post(BASE+'/regression-dataset-imports',json=confirmed,headers={'Idempotency-Key':'import-fixed-dataset'})
    assert two.status_code==200 and two.json()==one.json(),two.text
    assert one.json()['selection']['case_refs']==[reference(case,'case_id')]
    assert one.json()['selection']['import_source']['dataset_ref']==reference(original,'dataset_id')
    resources,_,events=await store._execution_read_state()
    assert len([r for r in resources.values() if 'regression_case' in r])==1
    appended=[e for e in events if e.type=='workspace.resource_committed' and e.payload['command_id']=='import-fixed-dataset']
    assert len(appended)==1 and appended[0].payload['resource_kind']=='regression_dataset'
    assert env.adapter.n==calls and not one.json()['execution_authorized']


async def test_unconfirmed_tampered_foreign_and_oversize_bundles_never_write_control(running_product):
    env=running_product;await dataset(env);artifact=await export(env);body=import_body(artifact)
    response=await env.client.post(BASE+'/regression-dataset-import-previews',json=body)
    assert response.status_code==200,response.text
    digest=response.json()['confirmation_digest'];store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    for confirmed in (False,1):
        response=await env.client.post(BASE+'/regression-dataset-imports',json=dict(body,confirmed=confirmed,confirmation_digest=digest),headers={'Idempotency-Key':'not-confirmed'})
        assert response.status_code==422,response.text
    for field in ('tenant_id','subject_ref'):
        bundle=json.loads(artifact['bundle_canonical']);bundle[field]='another-private-scope'
        response=await env.client.post(BASE+'/regression-dataset-import-previews',json=dict(body,bundle_canonical=json.dumps(bundle)))
        assert response.status_code==403,response.text
    bundle=json.loads(artifact['bundle_canonical']);bundle['cases'][0]['record']['selection']['input_text']='silently changed input'
    response=await env.client.post(BASE+'/regression-dataset-import-previews',json=dict(body,bundle_canonical=json.dumps(bundle)))
    assert response.status_code in (409,422),response.text
    response=await env.client.post(BASE+'/regression-dataset-import-previews',json=dict(body,bundle_canonical='x'*65537))
    assert response.status_code==422,response.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_withdrawal_after_previews_blocks_export_and_import_without_a_dataset_commit(running_product):
    env=running_product;await dataset(env);artifact=await export(env);body=import_body(artifact)
    imported=await env.client.post(BASE+'/regression-dataset-import-previews',json=body)
    assert imported.status_code==200,imported.text
    exported=await env.client.post(BASE+'/regression-datasets/'+DID+'/export-previews',json=dict(schema_version=1,revision=1))
    assert exported.status_code==200,exported.text
    response=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-before-transfer'})
    assert response.status_code==200,response.text
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    response=await env.client.post(BASE+'/regression-datasets/'+DID+'/exports',json=dict(schema_version=1,revision=1,confirmed=True,confirmation_digest=exported.json()['confirmation_digest']))
    assert response.status_code==403 and response.headers['cache-control']=='no-store',response.text
    response=await env.client.post(BASE+'/regression-dataset-imports',json=dict(body,confirmed=True,confirmation_digest=imported.json()['confirmation_digest']),headers={'Idempotency-Key':'withdrawn-import'})
    assert response.status_code==403 and response.headers['cache-control']=='no-store',response.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls
