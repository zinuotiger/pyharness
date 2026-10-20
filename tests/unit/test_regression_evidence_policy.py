"""Current confidentiality applies to source evidence, without rewriting history."""
from types import SimpleNamespace
import pytest
from pyharness import config
from pyharness.application.regression_candidates import public_candidate
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_regression_dataset_api import BASE,CID,DID,reference,saved


@pytest.mark.parametrize('role,value',[
    ('user','sk-abcdefghi'),('assistant','sk-abcdefghi'),
    ('user','Bearer synthetic-local-value'),('assistant','Bearer synthetic-local-value'),
],ids=['short-key-input','short-key-output','bearer-input','bearer-output'])
def test_candidate_rechecks_existing_source_text_policy_for_each_visible_value(role,value):
    events=[SimpleNamespace(type='execution.context',payload=dict(role='main_agent',
        public_view=dict(messages=[dict(role='user',content=value if role=='user' else 'public input')],truncated=False))),
        SimpleNamespace(type='agent.message',payload=dict(content=value if role=='assistant' else 'public output'))]
    candidate=public_candidate('p-'+'1'*16,{},events)
    key='observed_input' if role=='user' else 'observed_output'
    assert candidate[key+'_state']=='withheld'
    assert value not in (candidate[key+'_text'] or '')


async def test_new_rule_hides_original_evidence_without_invalidating_case_history(running_product,monkeypatch):
    env=running_product;_,case=await saved(env)
    marker=case['evidence']['observed_input_text']
    assert marker and marker!=case['selection']['input_text']
    dataset=await env.client.post(BASE+'/regression-datasets',json=dict(schema_version=1,dataset_id=DID,
        expected_revision=0,project_revision=1,title='Public fixed dataset',case_refs=[reference(case,'case_id')],confirmed=True),
        headers={'Idempotency-Key':'evidence-policy-dataset'})
    assert dataset.status_code==200,dataset.text
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    redact=config.redact
    with monkeypatch.context() as patch:
        patch.setattr(config,'redact',lambda value:'[redacted]' if value==marker else redact(value))
        projects=await env.client.get('/api/v1/projects');assert projects.status_code==200,projects.text
        for path in ['/regression-cases/'+CID,'/regression-datasets/'+DID]:
            r=await env.client.get(BASE+path)
            assert r.status_code==403 and r.headers['cache-control']=='no-store',r.text
            assert 'record_canonical' not in r.text
        r=await env.client.get(BASE+'/regression-cases')
        assert r.status_code==200 and r.json()['items'][0]['status']=='source_unavailable' and 'title' not in r.json()['items'][0],r.text
        r=await env.client.post(BASE+'/regression-datasets/'+DID+'/export-previews',json=dict(schema_version=1,revision=1))
        assert r.status_code==403 and 'bundle_canonical' not in r.text,r.text
        r=await env.client.post(BASE+'/failure-candidates',json=dict(schema_version=1,execution_id=case['selection']['origin']['execution_ref']['execution_id']))
        assert r.status_code==200 and r.json()['observed_input_state']=='withheld' and r.json()['observed_input_text'] is None,r.text
        assert store.log_path.read_bytes()==before and not store._failed
    r=await env.client.get(BASE+'/regression-cases/'+CID)
    assert r.status_code==200 and r.json()==case,r.text
    assert env.adapter.n==calls and store.log_path.read_bytes()==before
