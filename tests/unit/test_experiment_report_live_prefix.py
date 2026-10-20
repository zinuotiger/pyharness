"""An unfinished observed prefix stays unfinished after later completion or cancellation."""
import asyncio
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_runs import BASE,RID,run_selection,start,finished
from tests.unit.test_experiment_reports import REPORT_ID,report_preview,save_command
from tests.unit.test_regression_dataset_api import reference


async def test_active_prefix_is_reported_as_nonterminal_and_recomputes_after_later_cancellation(running_product):
    env=running_product;body=await run_selection(env);entered=asyncio.Event();release=asyncio.Event()
    async def hold(ctx,result):entered.set();await release.wait()
    env.controls.after_chat=hold
    try:
        _,receipt=await start(env,body);await asyncio.wait_for(entered.wait(),15)
        preview=await report_preview(env,receipt);data=preview['report_data'];counts=data['execution_counts']
        assert counts['interrupted']==0 and counts.get('nonterminal',0)>=1,counts
        assert data['quality_counts']['passed']==0 and data['regressions']==[]
        saved=await env.client.post(BASE+'/experiment-reports',json=save_command(preview),headers={'Idempotency-Key':'save-still-active-prefix'})
        assert saved.status_code==200,saved.text
        stopped=await env.client.post(BASE+'/experiment-cancellations',json=dict(schema_version=1,cancellation_id='ecx-'+'4'*16,
            experiment_run_ref=reference(receipt,'experiment_run_id')),headers={'Idempotency-Key':'cancel-after-fixed-report'})
        assert stopped.status_code==200,stopped.text
        release.set();await finished(env)
        read=await env.client.get(BASE+'/experiment-reports/'+REPORT_ID)
        assert read.status_code==200 and read.json()['report_data']==data,read.text
        current=await env.client.post(BASE+'/experiment-report-previews',json=dict(schema_version=1,report_id='epr-'+'2'*16,
            experiment_run_ref=reference(receipt,'experiment_run_id')))
        assert current.status_code==200 and current.json()['report_data']['execution_counts']['cancelled']>=1,current.text
        assert current.json()['report_data']['quality_counts']['passed']==0
    finally:release.set()
