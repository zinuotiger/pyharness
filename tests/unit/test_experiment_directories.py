"""Scoped directories and original-key recovery expose no new execution authority."""
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_run_boundaries import register_only
from tests.unit.test_experiment_reports import REPORT_ID,report_preview,save_command
from tests.unit.test_experiment_runs import BASE,RID
from tests.unit.test_experiment_preparation import EID
from tests.unit.test_regression_dataset_api import reference


async def test_directories_and_original_command_queries_are_scoped_read_only_and_never_dispatch(running_product,monkeypatch):
    env=running_product;_,run=await register_only(env,monkeypatch);preview=await report_preview(env,run)
    saved=await env.client.post(BASE+'/experiment-reports',json=save_command(preview),headers={'Idempotency-Key':'directory-report-key'})
    assert saved.status_code==200,saved.text
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    for route,key,ident in [('experiments','experiment_id',EID),('experiment-runs','experiment_run_id',RID),('experiment-reports','report_id',REPORT_ID)]:
        r=await env.client.get(BASE+'/'+route)
        assert r.status_code==200,r.text
        value=r.json();assert value['total']==1 and value['items'][0][key]==ident and value['items'][0]['status']=='available'
        assert not value['automatic_dispatch'] and not value['execution_authorized'] and 'input_text' not in r.text
    for key,kind,target in [('run-definition','experiment_preparation',EID),('fund-then-stop','experiment_run',RID),('directory-report-key','experiment_report',REPORT_ID)]:
        r=await env.client.get(BASE+'/experiment-command-submissions/'+key)
        assert r.status_code==200 and r.json()['status']=='committed' and r.json()['resource_kind']==kind,r.text
        assert not r.json()['automatic_dispatch'] and target in r.text
    r=await env.client.get(BASE+'/experiment-command-submissions/no-such-key')
    assert r.status_code==200 and r.json()['status']=='not_found',r.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_other_project_directory_and_original_key_recovery_never_reveal_existing_experiment(running_product,monkeypatch):
    env=running_product;_,run=await register_only(env,monkeypatch)
    other='p-'+'2'*16;base='/api/v1/projects/'+other
    r=await env.client.post('/api/v1/projects',json=dict(project_id=other,expected_revision=0,name='Other project'),headers={'Idempotency-Key':'other-experiment-project'})
    assert r.status_code==200,r.text
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    for route in ('experiments','experiment-runs','experiment-reports'):
        r=await env.client.get(base+'/'+route)
        assert r.status_code==200 and r.json()['items']==[] and r.json()['total']==0,r.text
    r=await env.client.get(base+'/experiment-command-submissions/fund-then-stop')
    assert r.status_code==403 and RID not in r.text and 'selection' not in r.text,r.text
    r=await env.client.get(base+'/experiments/'+EID)
    assert r.status_code==403,r.text
    assert before==store.log_path.read_bytes() and env.adapter.n==calls
