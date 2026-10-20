"""Stop the actual queue without restoring any withdrawn source-read capability."""
import asyncio
import copy
from types import SimpleNamespace
import pytest
from pyharness.errors import PyHError
from tests.unit.test_source_grants import PID,GID
from tests.unit.test_product_execution_start import running_product,wait_terminal


async def start(env,ident):
    projects=env.app.service.projects;preview=await projects.execution_preview(PID,env.selection)
    return await projects.start_execution(PID,dict(env.selection,execution_id=ident,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=preview['boundary']=='host_unrestricted'),'start-for-cancel')


def command(started):
    return dict(schema_version=1,cancellation_id='ec-'+'2'*16,
        execution_ref={k:started[k] for k in ('execution_id','revision','digest','source_seq')})


async def test_stop_waiting_model_and_repeat_original_key_without_extra_dispatch(running_product,monkeypatch):
    env=running_product;projects=env.app.service.projects;entered=asyncio.Event();resume=asyncio.Event()
    async def hold(ctx,response):entered.set();await resume.wait()
    env.controls.after_chat=hold
    started=await start(env,'ex-'+'d'*16);await asyncio.wait_for(entered.wait(),4)
    queue=env.app.service._queues[started['session_id']];cancel=queue.cancel;calls=[]
    async def counted(task_id,**kwargs):calls.append(task_id);return await cancel(task_id,**kwargs)
    monkeypatch.setattr(queue,'cancel',counted)
    try:
        requested=await projects.cancel_execution(PID,command(started),'cancel-model')
        assert requested['status']=='cancellation_requested' and requested['persisted'] is True
        result=await queue.wait_for(started['task_id'],timeout=4)
        assert not result.ok and result.code=='cancelled'
        terminal=await wait_terminal(projects,started['execution_id'])
        assert terminal['status']=='cancelled' and env.adapter.n==1
        before=projects.store.log_path.read_bytes()
        observed=await projects.cancel_execution(PID,command(started),'cancel-model')
        assert observed['execution_stopped'] is True and observed['terminal_observation']=='cancelled'
        assert calls==[started['task_id']] and projects.store.log_path.read_bytes()==before
    finally:resume.set()


@pytest.mark.parametrize('running_product',[dict(raw=b'captured\r\n',agent=dict(sandbox_profile=dict(mode='host_approved')))],indirect=True)
async def test_revoked_source_can_still_cancel_waiting_backend_without_source_body_reads(running_product,monkeypatch):
    env=running_product;projects=env.app.service.projects;manager=env.app.service.platform.sandboxes
    backend=manager.backends['host_approved'];create=backend.create;entered=asyncio.Event();resume=asyncio.Event()
    async def held(record,root,profile):await create(record,root,profile);entered.set();await resume.wait()
    backend.create=held
    started=await start(env,'ex-'+'e'*16);await asyncio.wait_for(entered.wait(),4)
    revoked=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
        headers={'Idempotency-Key':'withdraw-before-cancel'})
    assert revoked.status_code==200,revoked.text
    def forbidden(*args):raise AssertionError('stop tried to read unavailable source content')
    monkeypatch.setattr(projects.store,'_verify_snapshot_content',forbidden)
    monkeypatch.setattr(projects.store,'_verify_workspace_content',forbidden)
    try:
        await projects.cancel_execution(PID,command(started),'cancel-withdrawn-backend')
        queue=env.app.service._queues[started['session_id']]
        result=await queue.wait_for(started['task_id'],timeout=4)
        assert not result.ok and result.code=='cancelled' and env.adapter.n==0
        observed=await projects.cancellation_submission(PID,'cancel-withdrawn-backend')
        assert observed['status']=='committed' and observed['cancellation']['execution_stopped'] is True
        assert 'result_summary' not in str(observed) and str(env.source) not in str(observed)
        assert not backend.roots and not list(manager.root.iterdir())
        assert not any(e.type=='execution.effect' for e in env.app.service.surface_mgr()._logs[started['session_id']].events_after(0))
    finally:resume.set()


async def test_foreign_cached_runtime_receipt_cannot_receive_stop_signal(running_product,monkeypatch):
    env=running_product;projects=env.app.service.projects;entered=asyncio.Event();resume=asyncio.Event()
    async def hold(ctx,response):entered.set();await resume.wait()
    env.controls.after_chat=hold
    started=await start(env,'ex-'+'f'*16);await asyncio.wait_for(entered.wait(),4)
    sid=started['session_id'];spine=env.app.service._engines[sid]
    fake=copy.deepcopy(spine.platform_runtime.product_execution.receipt);fake['digest']='0'*64
    env.app.service._engines[sid]=SimpleNamespace(platform_runtime=SimpleNamespace(product_execution=SimpleNamespace(receipt=fake)))
    async def forbidden(*args,**kwargs):raise AssertionError('stop signalled mismatched cached runtime')
    monkeypatch.setattr(env.app.service._queues[sid],'cancel',forbidden)
    try:
        observed=await projects.cancel_execution(PID,command(started),'wrong-runtime-binding')
        assert observed['execution_stopped'] is None and observed['terminal_observation']=='unknown'
    finally:
        env.app.service._engines[sid]=spine;resume.set()
    result=await env.app.service._queues[sid].wait_for(started['task_id'],timeout=4)
    assert not result.ok and env.adapter.n==1


async def test_cancel_before_runner_starts_reports_real_queue_cancellation(running_product,monkeypatch):
    env=running_product;projects=env.app.service.projects;factory=env.app.service._queue_for_owned
    async def paused(sid,log):
        queue=await factory(sid,log);await queue.pause('test_before_start');return queue
    monkeypatch.setattr(env.app.service,'_queue_for_owned',paused)
    started=await start(env,'ex-'+'0'*16)
    assert env.adapter.n==0
    result=await projects.cancel_execution(PID,command(started),'cancel-queued')
    assert result['execution_stopped'] is True and result['terminal_observation']=='cancelled'
    observed=await projects.execution(PID,started['execution_id'])
    assert observed['status']=='cancelled' and observed['execution_started'] is False and not observed['effects']


async def test_lost_control_commit_receipt_still_signals_exact_owned_running_task(running_product,monkeypatch):
    env=running_product;projects=env.app.service.projects;entered=asyncio.Event();resume=asyncio.Event()
    async def hold(ctx,response):entered.set();await resume.wait()
    env.controls.after_chat=hold
    started=await start(env,'ex-'+'1'*16);await asyncio.wait_for(entered.wait(),4)
    verify=projects.store._verify_commit
    def lost(store,event):verify(store,event);raise OSError('synthetic cancel receipt lost')
    monkeypatch.setattr(projects.store,'_verify_commit',lost)
    try:
        with pytest.raises(PyHError):await projects.cancel_execution(PID,command(started),'lost-stop-receipt')
        queue=env.app.service._queues[started['session_id']]
        result=await queue.wait_for(started['task_id'],timeout=2)
        assert not result.ok and result.code=='cancelled' and projects.store._failed
        observed=await projects.cancellation_submission(PID,'lost-stop-receipt')
        assert observed['writer_requires_reopen'] is True and observed['cancellation']['execution_stopped'] is True
    finally:resume.set()
