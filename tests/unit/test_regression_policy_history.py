"""A current confidentiality restriction denies a selected copy, not historical decoding."""
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_regression_dataset_api import BASE,CID,DID,reference,case_command,save_case


async def test_new_confidentiality_rule_does_not_turn_old_case_or_entire_control_log_into_corruption(running_product,monkeypatch):
    from pyharness import config
    env=running_product;body=await case_command(env);marker='manual-only-marker-123'
    body['input_text']=marker;response=await save_case(env,body,'before-secret-policy-update')
    assert response.status_code==200,response.text
    original=response.json()
    dataset=await env.client.post(BASE+'/regression-datasets',json=dict(schema_version=1,dataset_id=DID,
        expected_revision=0,project_revision=1,title=marker,case_refs=[reference(original,'case_id')],confirmed=True),
        headers={'Idempotency-Key':'dataset-before-secret-policy-update'})
    assert dataset.status_code==200,dataset.text
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    redact=config.redact
    with monkeypatch.context() as patch:
        patch.setattr(config,'redact',lambda text:redact(text).replace(marker,'[redacted]'))
        projects=await env.client.get('/api/v1/projects')
        assert projects.status_code==200,projects.text
        unavailable=await env.client.get(BASE+'/regression-cases/'+CID)
        assert unavailable.status_code==403 and unavailable.headers['cache-control']=='no-store',unavailable.text
        unavailable=await env.client.get(BASE+'/regression-datasets/'+DID)
        assert unavailable.status_code==403 and unavailable.headers['cache-control']=='no-store',unavailable.text
        rejected=await save_case(env,dict(body,expected_revision=1),'after-secret-policy-update')
        assert rejected.status_code==422,rejected.text
        directory=await env.client.get(BASE+'/regression-cases')
        assert directory.status_code==200,directory.text
        row=directory.json()['items'][0]
        assert row['status']=='source_unavailable' and 'title' not in row
        assert not store._failed and store.log_path.read_bytes()==before
    restored=await env.client.get(BASE+'/regression-cases/'+CID)
    assert restored.status_code==200 and restored.json()==original,restored.text
    assert env.adapter.n==calls and store.log_path.read_bytes()==before
