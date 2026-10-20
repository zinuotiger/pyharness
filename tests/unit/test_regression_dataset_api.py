"""Confirmed control commits, fixed dataset references and historical private reads."""
import copy
import hashlib
import json
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_run_observation_comparison_api import second
from tests.unit.test_source_grants import PID,GID
from tests.unit.test_failure_candidate_api import preview

BASE='/api/v1/projects/'+PID
CID='case-'+'1'*16
DID='ds-'+'1'*16


def reference(value,key):
    return {key:value[key],'revision':value['revision'],'digest':value['digest'],'source_seq':value['source_seq']}


async def case_command(env):
    failure=await second(env,failure=True)
    response=await preview(env,failure['execution_id']);assert response.status_code==200,response.text
    candidate=response.json()
    return confirmed_case(candidate)


def confirmed_case(candidate):
    return dict(schema_version=1,case_id=CID,expected_revision=0,project_revision=1,
        title='人工确认的回归用例',origin=candidate['origin'],candidate_digest=candidate['candidate_digest'],
        input_text='Review the exact synthetic input.',
        assertions=[dict(kind='output_contains',expected='reviewed')],confirmed=True)


async def save_case(env,body,key='save-confirmed-case'):
    return await env.client.post(BASE+'/regression-cases',json=body,headers={'Idempotency-Key':key})


async def saved(env):
    body=await case_command(env);response=await save_case(env,body)
    assert response.status_code==200,response.text
    return body,response.json()


async def test_unconfirmed_and_changed_candidate_commit_nothing_then_original_key_is_idempotent(running_product):
    env=running_product;body=await case_command(env);store=env.app.service.projects.store
    before=store.log_path.read_bytes();calls=env.adapter.n
    response=await save_case(env,dict(body,confirmed=False),'unconfirmed')
    assert response.status_code==422,response.text
    response=await save_case(env,dict(body,candidate_digest='0'*64),'changed-candidate')
    assert response.status_code==409,response.text
    assert store.log_path.read_bytes()==before
    one=await save_case(env,body);assert one.status_code==200,one.text
    committed=store.log_path.read_bytes();two=await save_case(env,body)
    assert two.status_code==200 and one.json()==two.json(),two.text
    assert store.log_path.read_bytes()==committed and env.adapter.n==calls
    assert not one.json()['execution_authorized'] and one.json()['revision']==1


async def test_case_edit_does_not_rewrite_old_dataset_or_old_case_bytes(running_product):
    env=running_product;body,case=await saved(env);calls=env.adapter.n
    dataset=dict(schema_version=1,dataset_id=DID,expected_revision=0,project_revision=1,
        title='固定版本集合',case_refs=[reference(case,'case_id')],confirmed=True)
    response=await env.client.post(BASE+'/regression-datasets',json=dataset,headers={'Idempotency-Key':'dataset-v1'})
    assert response.status_code==200,response.text
    first=response.json();changed=copy.deepcopy(body);changed.update(expected_revision=1,title='第二修订')
    changed['assertions']=[dict(kind='output_contains',expected='new expectation')]
    revised=await save_case(env,changed,'case-v2');assert revised.status_code==200,revised.text
    assert revised.json()['revision']==2 and revised.json()['digest']!=case['digest']
    old=await env.client.get(BASE+'/regression-cases/'+CID,params={'revision':1})
    assert old.status_code==200 and old.json()==case,old.text
    fixed=await env.client.get(BASE+'/regression-datasets/'+DID,params={'revision':1})
    assert fixed.status_code==200 and fixed.json()==first,fixed.text
    assert fixed.json()['selection']['case_refs']==[reference(case,'case_id')]
    assert env.adapter.n==calls


async def test_private_content_and_unknown_assertions_are_rejected_before_control_append(running_product):
    env=running_product;body=await case_command(env);store=env.app.service.projects.store;before=store.log_path.read_bytes()
    for changes in [dict(input_text='password=do-not-save-this'),
            dict(assertions=[dict(kind='python',expected='__import__("os")')]),
            dict(confirmed=1),dict(origin=dict(body['origin'],tenant_id='another-tenant'))]:
        response=await save_case(env,dict(body,**changes),'rejected-'+str(len(str(changes))))
        assert response.status_code in (403,422),response.text
        assert response.headers['cache-control']=='no-store'
        assert store.log_path.read_bytes()==before


async def test_cold_case_and_dataset_reads_are_private_and_source_withdrawal_blocks_the_copy(running_product):
    env=running_product;_,case=await saved(env);calls=env.adapter.n
    dataset=dict(schema_version=1,dataset_id=DID,expected_revision=0,project_revision=1,title='冷读集合',
        case_refs=[reference(case,'case_id')],confirmed=True)
    response=await env.client.post(BASE+'/regression-datasets',json=dataset,headers={'Idempotency-Key':'cold-dataset'})
    assert response.status_code==200,response.text
    cfg=env.app.service.ctx.settings;roots=(env.app.service.projects.store.log_path.parent,env.app.service._tenant_sessions_dir())
    await env.app.service.shutdown();journals={p:hashlib.sha256(p.read_bytes()).hexdigest() for root in roots for p in root.rglob('*.jsonl')}
    cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',
                headers={'X-PyHarness-Token':cold._api_token}) as client:
            for path in ['/regression-cases/'+CID,'/regression-datasets/'+DID]:
                response=await client.get(BASE+path)
                assert response.status_code==200 and response.headers['cache-control']=='no-store',response.text
            assert cold.service.projects.store._store is None and not cold.service._engines and not cold.service._queues
            assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in journals.items())
            response=await client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),
                headers={'Idempotency-Key':'withdraw-dataset-source'})
            assert response.status_code==200,response.text
            for path in ['/regression-cases/'+CID,'/regression-datasets/'+DID]:
                response=await client.get(BASE+path)
                assert response.status_code==403 and response.headers['cache-control']=='no-store',response.text
        assert env.adapter.n==calls
    finally:await cold.service.shutdown()


async def test_canonical_receipt_is_the_actual_blob_and_preserves_non_javascript_safe_numbers(running_product):
    env=running_product;body=await case_command(env)
    body['assertions']=[dict(kind='json_pointer_equals',pointer='/value',expected=9007199254740993)]
    response=await save_case(env,body,'exact-large-number-case')
    assert response.status_code==200,response.text
    value=response.json();raw=value['record_canonical'].encode('utf-8')
    assert hashlib.sha256(raw).hexdigest()==value['digest']
    assert env.app.service.projects.store._blob(value['digest']).read_bytes()==raw
    assert b'9007199254740993' in raw
    assert json.loads(raw)['selection']['assertions'][0]['expected']==9007199254740993
