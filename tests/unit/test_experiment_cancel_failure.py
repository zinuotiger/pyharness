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


async def test_unconfirmed_stop_waits_for_exact_owned_queue_unwind(running_product,monkeypatch):
    from pyharness.errors import raise_code
    env=running_product;body=await run_selection(env)
    entered=asyncio.Event();release=asyncio.Event();arrivals=[]
    async def hold(ctx,result):
        arrivals.append(ctx.session.sid)
        if len(arrivals)==2:entered.set()
        await release.wait()
    env.controls.after_chat=hold
    unwind=asyncio.Event();signals=asyncio.Event();finished=asyncio.Event();request=None
    try:
        _,run=await start(env,body);await asyncio.wait_for(entered.wait(),15)
        p=env.app.service.projects;store=p.store
        owner=p.service._task_graph_owners[run['graph_run_ref']['graph_run_id']]
        queues=[p.service._queues[target['session_id']] for target in owner.stop_targets.values()]
        assert len(queues)==2
        other_queues=[queue for queue in p.service._queues.values() if queue not in queues]
        other_states=[queue.status() for queue in other_queues]
        for queue in queues:
            close=queue._close_segment_guarded
            async def held_close(*args,original=close,**kwargs):
                await unwind.wait()
                return await original(*args,**kwargs)
            monkeypatch.setattr(queue,'_close_segment_guarded',held_close)
        stop=owner.stop_children
        async def observed_stop():
            await stop();finished.set()
        monkeypatch.setattr(owner,'stop_children',observed_stop)
        signal=p._signal_execution_stop;signalled=set()
        async def observed_signal(target,queue,by):
            await signal(target,queue,by);signalled.add(id(queue))
            if len(signalled)==2:signals.set()
        monkeypatch.setattr(p,'_signal_execution_stop',observed_signal)
        verify=store._verify_commit
        def lose_ack(log,event):
            verify(log,event)
            if event.payload.get('resource_kind')=='experiment_cancellation':
                raise_code('PERS-202',reason='synthetic_experiment_cancel_ack_lost')
        monkeypatch.setattr(store,'_verify_commit',lose_ack)
        request=asyncio.create_task(env.client.post(BASE+'/experiment-cancellations',
            json=dict(schema_version=1,cancellation_id='ecx-'+'7'*16,
                      experiment_run_ref=reference(run,'experiment_run_id')),
            headers={'Idempotency-Key':'stop-waits-for-owned-unwind'}))
        await asyncio.wait_for(signals.wait(),15)
        assert not finished.is_set(),'Stop returned before the exact owned queues finished cleanup'
        assert owner.closed
        from pyharness.errors import PyHError
        with pytest.raises(PyHError,match='GRD-403'):
            owner.ready(owner.authority['nodes'][0])
        assert any(queue.status().running is not None for queue in queues)
        unwind.set()
        response=await asyncio.wait_for(request,15)
        assert response.status_code==503,response.text
        assert owner.closed and all(queue.status().running is None and not queue.status().waiting for queue in queues)
        assert [queue.status() for queue in other_queues]==other_states
    finally:
        unwind.set();release.set()
        if request is not None:await asyncio.gather(request,return_exceptions=True)
