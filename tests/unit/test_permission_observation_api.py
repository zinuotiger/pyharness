"""Exact input/Agent selection; read-only predictions must not start anything."""
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_source_grants import PID,GID

URL='/api/v1/projects/'+PID+'/permission-previews'


@pytest.mark.parametrize('running_product',[dict(script=[dict(id='read',name='fs.read_file',args=dict(path='unchanged.txt'))],agent=dict(sandbox_profile=dict(mode='isolated')))],indirect=True)
async def test_exact_selection_predicts_without_model_sandbox_queue_or_journal_write(running_product,monkeypatch):
    env=running_product;service=env.app.service;before=service.projects.store.log_path.read_bytes()
    source_before=(env.source/'unchanged.txt').read_bytes()
    def forbidden(*a,**kw):raise AssertionError('preview must not create an engine or sandbox')
    monkeypatch.setattr(service,'_engine_runner_for',forbidden)
    monkeypatch.setattr(service.platform.sandboxes,'create',forbidden)
    response=await env.client.post(URL,json=dict(env.selection,tool_name='fs.read_file',arguments=dict(path='unchanged.txt')))
    assert response.status_code==200,response.text
    value=response.json();assert value['prediction']=='allow' and value['execution_authorized'] is False
    assert value['attempt_ref']==env.selection['attempt_ref'] and value['agent_ref']==env.selection['agent_ref']
    assert response.headers['cache-control']=='no-store'
    assert env.adapter.n==0 and not service._engines and not service._queues and service.projects.store.log_path.read_bytes()==before
    assert (env.source/'unchanged.txt').read_bytes()==source_before


async def test_stale_version_and_withdrawn_source_fail_closed(running_product):
    env=running_product;body=dict(env.selection,tool_name='fs.read_file',arguments=dict(path='unchanged.txt'))
    stale=dict(body,agent_ref=dict(env.selection['agent_ref'],sha256='0'*64))
    response=await env.client.post(URL,json=stale);assert response.status_code in (403,409,422),response.text
    assert (await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'preview-revoke'})).status_code==200
    response=await env.client.post(URL,json=body)
    assert response.status_code==403 and env.adapter.n==0 and response.headers['cache-control']=='no-store',response.text


async def test_preview_rechecks_real_source_after_guard_await(running_product,monkeypatch):
    import pyharness.application.permission_observation as observation
    env=running_product;original=observation.predict_permission
    async def revoke(*args,**kwargs):
        result=await original(*args,**kwargs)
        # The real grant is withdrawn, rather than a substituted guard verdict.
        response=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'mid-preview-revoke'})
        assert response.status_code==200,response.text
        return result
    monkeypatch.setattr(observation,'predict_permission',revoke)
    response=await env.client.post(URL,json=dict(env.selection,tool_name='fs.read_file',arguments=dict(path='unchanged.txt')))
    assert response.status_code==403 and env.adapter.n==0 and 'prediction' not in response.text,response.text


@pytest.mark.parametrize('extra',[dict(actor='forged-owner'),dict(authorization_token='bypass'),dict(arguments={'path':'x','nested':{'x':[[[[[[[[[[0]]]]]]]]]]}})])
async def test_prediction_body_rejects_authority_and_unbounded_structure(running_product,extra):
    env=running_product
    response=await env.client.post(URL,json=dict(dict(env.selection,tool_name='fs.read_file',arguments=dict(path='unchanged.txt')),**extra))
    assert response.status_code==422 and response.headers['cache-control']=='no-store',response.text
    assert env.adapter.n==0
