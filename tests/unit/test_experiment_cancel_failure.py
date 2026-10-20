"""A rejected or unconfirmed stop receipt still reduces exact owned runtime authority."""
import asyncio
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_runs import BASE,RID,run_selection,start
from tests.unit.test_regression_dataset_api import reference


@pytest.mark.parametrize('fault',['missing-blob','lost-commit-ack'])
async def test_unconfirmed_experiment_stop_signals_exact_owner_without_claiming_commit(running_product,monkeypatch,fault):
    from pyharness.core.tenant_settings import TenantSettingsStore
    from pyharness.errors import raise_code
    env=running_product;body=await run_selection(env);entered=asyncio.Event();release=asyncio.Event();arrivals=[]
    async def hold(ctx,result):
        arrivals.append(ctx.session.sid)
        if len(arrivals)==2:entered.set()
        await release.wait()
    env.controls.after_chat=hold
    try:
        _,run=await start(env,body);await asyncio.wait_for(entered.wait(),15)
        p=env.app.service.projects;store=p.store;calls=env.adapter.n;before=store.log_path.read_bytes()
        write=TenantSettingsStore._atomic_write;verify=store._verify_commit
        def false_write(path,data,*args,**kwargs):
            if path.parent==store.root/'blobs':return
            return write(path,data,*args,**kwargs)
        def lose_ack(log,event):
            verify(log,event)
            if event.payload.get('resource_kind')=='experiment_cancellation':raise_code('PERS-202',reason='synthetic_experiment_cancel_ack_lost')
        with monkeypatch.context() as patch:
            if fault=='missing-blob':patch.setattr(TenantSettingsStore,'_atomic_write',staticmethod(false_write))
            else:patch.setattr(store,'_verify_commit',lose_ack)
            r=await env.client.post(BASE+'/experiment-cancellations',json=dict(schema_version=1,cancellation_id='ecx-'+'6'*16,
                experiment_run_ref=reference(run,'experiment_run_id')),headers={'Idempotency-Key':'unconfirmed-experiment-stop'})
            assert r.status_code==503,r.text
            assert p.service._task_graph_owners[run['graph_run_ref']['graph_run_id']].closed
            assert all(q.status().running is None and not q.status().waiting for q in p.service._queues.values())
        if fault=='missing-blob':assert store.log_path.read_bytes()==before
        control=await env.client.get(BASE+'/experiment-runs/'+RID+'/control')
        assert control.status_code==200 and control.json()['cancel_requested']==(fault=='lost-commit-ack'),control.text
        assert env.adapter.n==calls
    finally:release.set()
