"""Finding an owned stop reference never requires source bodies or an active project."""
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_run_boundaries import register_only
from tests.unit.test_experiment_runs import BASE,RID
from tests.unit.test_regression_dataset_api import reference


async def test_archived_owned_control_directory_and_stop_survive_unavailable_case_blob(running_product,monkeypatch):
    from tests.unit.test_source_grants import PID,GID
    env=running_product;_,run=await register_only(env,monkeypatch);store=env.app.service.projects.store
    r=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'control-index-withdraw-source'})
    assert r.status_code==200,r.text
    r=await env.client.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=1,name='Archived experiment controls',archived=True),headers={'Idempotency-Key':'archive-control-index-project'})
    assert r.status_code==200,r.text
    resources,_,_=await store._execution_read_state()
    row=next(v for v in resources.values() if 'regression_case' in v);path=store._blob(row['digest']);raw=path.read_bytes()
    before=store.log_path.read_bytes();calls=env.adapter.n;path.unlink()
    try:
        r=await env.client.get(BASE+'/experiment-runs')
        assert r.status_code==200 and r.json()['control_only'] and r.json()['total']==1,r.text
        item=r.json()['items'][0]
        assert item['experiment_run_id']==RID and 'selection' not in r.text and 'title' not in r.text
        assert store.log_path.read_bytes()==before and env.adapter.n==calls
        r=await env.client.post(BASE+'/experiment-cancellations',json=dict(schema_version=1,cancellation_id='ecx-'+'5'*16,
            experiment_run_ref=reference(run,'experiment_run_id')),headers={'Idempotency-Key':'archive-source-independent-stop'})
        assert r.status_code==200 and r.json()['cancel_requested'],r.text
        r=await env.client.get(BASE+'/experiment-runs/'+RID+'/control')
        assert r.status_code==200 and r.json()['cancel_requested'],r.text
        assert env.adapter.n==calls and not getattr(env.app.service,'_task_graph_owners',{})
    finally:path.write_bytes(raw)
