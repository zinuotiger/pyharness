"""Incomplete or withdrawn evidence cannot become a passing or exportable report."""
import copy
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_run_boundaries import register_only
from tests.unit.test_experiment_reports import REPORT_ID,report_preview,save_command,quality_batch
from tests.unit.test_experiment_runs import BASE,RID
from tests.unit.test_regression_dataset_api import reference


@pytest.mark.parametrize('cancelled',[False,True])
async def test_unstarted_and_cancelled_batches_have_zero_passed_quality_and_no_hard_regressions(running_product,monkeypatch,cancelled):
    env=running_product;_,receipt=await register_only(env,monkeypatch);calls=env.adapter.n
    if cancelled:
        r=await env.client.post(BASE+'/experiment-cancellations',json=dict(schema_version=1,cancellation_id='ecx-'+'3'*16,
            experiment_run_ref=reference(receipt,'experiment_run_id')),headers={'Idempotency-Key':'cancel-unstarted-for-report'})
        assert r.status_code==200,r.text
    preview=await report_preview(env,receipt);data=preview['report_data']
    assert data['execution_counts']['cancelled' if cancelled else 'not_dispatched']==2
    assert data['quality_counts']==dict(total=2,evaluable=0,passed=0,failed=0,unknown=2)
    assert data['regressions']==[] and data['business_counts']['not_evaluated']==2
    assert env.adapter.n==calls and all(v is None for v in preview['basis']['nodes'].values())
    r=await env.client.post(BASE+'/experiment-reports',json=save_command(preview),headers={'Idempotency-Key':'save-incomplete-report'})
    assert r.status_code==200,r.text
    r=await env.client.get(BASE+'/experiment-reports/'+REPORT_ID)
    assert r.status_code==200 and r.json()['report_data']==data,r.text


async def test_false_confirmation_or_invented_prefix_never_appends_report(running_product,monkeypatch):
    env=running_product;_,receipt=await register_only(env,monkeypatch);preview=await report_preview(env,receipt)
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    invalid=[]
    for change in (dict(confirmed=False),dict(report_digest='0'*64),dict(confirmation_digest='0'*64)):
        invalid.append(dict(save_command(preview),**change))
    forged=copy.deepcopy(save_command(preview));forged['basis']['control']['prefix_digest']='0'*64;invalid.append(forged)
    extra=copy.deepcopy(save_command(preview));extra['basis']['nodes']['invented_node']=None;invalid.append(extra)
    for body in invalid:
        r=await env.client.post(BASE+'/experiment-reports',json=body,headers={'Idempotency-Key':'invalid-fixed-report'})
        assert r.status_code in (409,422),r.text
        assert r.headers['cache-control']=='no-store' and store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_withdrawal_denies_saved_report_and_export_but_keeps_minimal_control(running_product,monkeypatch):
    from tests.unit.test_source_grants import GID
    env=running_product;receipt,_=await quality_batch(env,monkeypatch);preview=await report_preview(env,receipt)
    r=await env.client.post(BASE+'/experiment-reports',json=save_command(preview),headers={'Idempotency-Key':'withdrawn-fixed-report'})
    assert r.status_code==200,r.text
    r=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-report-original-source'})
    assert r.status_code==200,r.text
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    r=await env.client.get(BASE+'/experiment-reports/'+REPORT_ID)
    assert r.status_code==403 and 'report_data' not in r.text,r.text
    r=await env.client.post(BASE+'/experiment-reports/'+REPORT_ID+'/export-previews',json=dict(schema_version=1))
    assert r.status_code==403 and 'report_canonical' not in r.text,r.text
    r=await env.client.get(BASE+'/experiment-runs/'+RID+'/control')
    assert r.status_code==200 and r.json()['control_only'],r.text
    assert before==store.log_path.read_bytes() and env.adapter.n==calls


async def test_cold_report_recompute_is_read_only_and_common_audit_accepts_all_experiment_kinds(running_product,monkeypatch):
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    import httpx
    env=running_product;_,receipt=await register_only(env,monkeypatch);preview=await report_preview(env,receipt)
    r=await env.client.post(BASE+'/experiment-reports',json=save_command(preview),headers={'Idempotency-Key':'cold-incomplete-report'})
    assert r.status_code==200,r.text
    cfg=env.app.service.ctx.settings;root=env.app.service.storage_root;calls=env.adapter.n
    await env.app.service.shutdown();journals={p:p.read_bytes() for p in root.rglob('*.jsonl')};cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            r=await client.get(BASE+'/experiment-reports/'+REPORT_ID)
            assert r.status_code==200 and r.json()['report_data']==preview['report_data'],r.text
            assert cold.service.projects.store._store is None and cold.service.projects.store._lease is None
            r=await client.get('/api/v1/workspace/audit',params=dict(pagination_version=1,limit=200))
            assert r.status_code==200 and {'experiment_run','experiment_report'}<={e['resource_kind'] for e in r.json()['events']},r.text
            # The old audit API intentionally opens its existing writer. Keep its
            # established contract separate from the new report's zero-writer read.
            assert not cold.service._engines and not cold.service._queues and env.adapter.n==calls
            assert all(p.read_bytes()==raw for p,raw in journals.items())
    finally:await cold.service.shutdown()
