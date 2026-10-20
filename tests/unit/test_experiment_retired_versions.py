"""Retirement blocks new authority without erasing immutable, still-authorized evidence."""
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_reports import quality_batch,report_preview,save_command,REPORT_ID
from tests.unit.test_experiment_preparation import EID,LIMITS
from tests.unit.test_experiment_runs import BASE,RID


async def test_retired_agent_version_keeps_old_report_readable_but_refuses_new_funding(running_product,monkeypatch):
    env=running_product;run,_=await quality_batch(env,monkeypatch);preview=await report_preview(env,run)
    saved=await env.client.post(BASE+'/experiment-reports',json=save_command(preview),headers={'Idempotency-Key':'report-before-retirement'})
    assert saved.status_code==200,saved.text
    reference=env.selection['agent_ref'];env.app.service.platform.agent_action(reference['agent_id'],'deprecate-version',dict(version=reference['version']))
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    definition=await env.client.get(BASE+'/experiments/'+EID)
    assert definition.status_code==200,definition.text
    observed=await env.client.get(BASE+'/experiment-runs/'+RID)
    assert observed.status_code==200 and observed.json()['status']=='completed',observed.text
    report=await env.client.get(BASE+'/experiment-reports/'+REPORT_ID)
    assert report.status_code==200 and report.json()['report_data']==preview['report_data'],report.text
    denied=await env.client.post(BASE+'/experiment-run-previews',json=dict(schema_version=1,experiment_run_id='er-'+'2'*16,
        experiment_ref=run['selection']['experiment_ref'],budget_grant=LIMITS,confirm_host_boundary=False))
    assert denied.status_code in (403,409,422),denied.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls
