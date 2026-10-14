import asyncio
from types import SimpleNamespace

import pytest


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate(): await asyncio.sleep(0)


def test_child_ids_do_not_collide_across_manager_lifetimes():
    from pyharness.core.subagent import SubagentManager
    a, b = SubagentManager(), SubagentManager()
    assert a._default_sub_id() != b._default_sub_id()


async def test_F05_job_cancel_removes_real_queued_work(e2e_factory):
    s = await e2e_factory(sid='s-job-cancel-remediation')
    await s.boot()
    q = s.ctx.task_queue
    entered, release = asyncio.Event(), asyncio.Event()
    executed = []
    async def work(task):
        executed.append(task.intent)
        if task.intent == 'occupy':
            entered.set()
            await release.wait()
    q._runner = SimpleNamespace(run_for_task=work)
    await q.submit('occupy')
    await entered.wait()
    jobs = s.ctx.engine_spine.jobs
    jid = await jobs.start('must-not-execute', SimpleNamespace(owner='test-owner'))
    try:
        await until(lambda: len(q.status().waiting) == 1)
        queued = q.status().waiting[0]
        await jobs.cancel(jid, by='test-owner')
        await asyncio.gather(jobs._running[jid].task, return_exceptions=True)
        assert queued not in q.status().waiting
        release.set()
        await until(lambda: q.status().running is None)
        assert executed == ['occupy']
        assert not (await q.wait_for(queued)).ok
    finally:
        release.set()
        await q.shutdown()


async def test_F07_acp_switch_reuses_session_runtime(tmp_path, adapter_factory):
    from pyharness.cli import assemble_ctx, _flush_session
    from pyharness.desktop.sessions import DesktopSessionManager
    from pyharness.acp import _ensure_engine_queue
    from tests.support import isolated_settings
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    ctx = assemble_ctx(cfg)
    ctx.channel = 'acp:test'
    manager = DesktopSessionManager(dir=tmp_path/'sessions', bus=ctx.bus, config=cfg)
    ctx.session = manager
    a, b = await manager.create(), await manager.create()
    made = []
    try:
        qa = await _ensure_engine_queue(ctx, await manager.open_session(a), a)
        made.append(ctx.engine_spine)
        qb = await _ensure_engine_queue(ctx, await manager.open_session(b), b)
        made.append(ctx.engine_spine)
        again = await _ensure_engine_queue(ctx, await manager.open_session(a), a)
        made.append(ctx.engine_spine)
        assert again is qa
        assert qa is not qb
    finally:
        for spine in {id(x):x for x in made}.values(): await spine.close()
        await manager.shutdown_all()
        runtime = getattr(ctx, 'llm_runtime', None)
        if runtime: await runtime.aclose()


async def test_F04_service_stops_writers_before_store_close(tmp_path, adapter_factory, monkeypatch):
    from pyharness.application.service import ApplicationService
    from pyharness.cli import assemble_ctx
    from tests.support import isolated_settings
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    service = ApplicationService(assemble_ctx(cfg), channel='desktop')
    sid = await service.create_session()
    log = await service.require_session(sid)
    await service.queue_for(sid, log)
    spine = service._engines[sid]
    actual_close = spine.close
    state = []
    async def close():
        state.append(spine.persistence._fh.closed)
        await actual_close()
    monkeypatch.setattr(spine, 'close', close)
    await service.shutdown()
    assert state == [False]
    assert spine.persistence._fh.closed


async def test_F04_shutdown_waits_for_admitted_initialization(tmp_path, adapter_factory, monkeypatch):
    from pyharness.application.service import ApplicationService
    from pyharness.cli import assemble_ctx
    from pyharness import engine
    from tests.support import isolated_settings
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    service = ApplicationService(assemble_ctx(cfg), channel='desktop')
    sid = await service.create_session()
    entered, release = asyncio.Event(), asyncio.Event()
    async def preload(spine):
        entered.set()
        await release.wait()
    monkeypatch.setattr(engine, '_preload_plugins', preload)
    assembly = asyncio.create_task(service.queue_for(sid))
    await entered.wait()
    stopping = asyncio.create_task(service.shutdown())
    try:
        await asyncio.sleep(.02)
        release.set()
        await asyncio.gather(assembly, return_exceptions=True)
        await stopping
        assert all(q._closing for q in service._queues.values())
        assert all(s.persistence._fh.closed for s in service._engines.values())
    finally:
        release.set()
        await asyncio.gather(assembly, stopping, return_exceptions=True)


async def test_F04_two_shutdown_waiters_wait_for_writer_finally(e2e_factory):
    s = await e2e_factory(sid='s-shutdown-barrier')
    await s.boot()
    q = s.ctx.task_queue
    entered, finishing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def run(task):
        entered.set()
        try: await asyncio.Event().wait()
        finally:
            finishing.set()
            await release.wait()
    q._runner = SimpleNamespace(run_for_task=run)
    await q.submit('wait')
    await entered.wait()
    a = asyncio.create_task(q.shutdown())
    await finishing.wait()
    b = asyncio.create_task(q.shutdown())
    try:
        await asyncio.sleep(.02)
        assert not a.done() and not b.done()
    finally:
        release.set()
        await asyncio.gather(a, b)
    assert q._exec is None or q._exec.done()


async def test_R04_failed_transport_close_can_retry():
    from pyharness.core.llm import OpenAICompatAdapter
    calls = []
    class Transport:
        async def aclose(self):
            calls.append(True)
            if len(calls) == 1: raise OSError('synthetic cleanup failure')
    adapter = OpenAICompatAdapter('synthetic', transport=Transport())
    with pytest.raises(OSError): await adapter.aclose()
    await adapter.aclose()
    assert len(calls) == 2


async def test_subagent_bootstrap_failure_closes_owned_store(e2e_factory, monkeypatch):
    from pyharness.core.subagent import SubagentSpec
    from pyharness import persistence
    s = await e2e_factory(sid='s-child-bootstrap-fault')
    await s.boot()
    manager = s.ctx.engine_spine.subagent
    before = {k:v[1] for k,v in persistence._LOCKS.items()}
    async def fail(*args): raise OSError('child bootstrap fault')
    monkeypatch.setattr(manager, '_bootstrap_child', fail)
    with pytest.raises(OSError, match='child bootstrap fault'):
        await manager.spawn(SubagentSpec(task='synthetic', depth=1), ctx=s.ctx)
    assert manager._active == 0
    assert not manager._runner._stores
    assert {k:v[1] for k,v in persistence._LOCKS.items()} == before


async def test_acp_failed_attach_keeps_borrowed_store_usable(tmp_path, adapter_factory, monkeypatch):
    from pyharness import acp, engine
    from pyharness.cli import assemble_ctx
    from pyharness.desktop.sessions import DesktopSessionManager
    from tests.support import isolated_settings
    cfg = isolated_settings(tmp_path); adapter_factory(cfg)
    ctx = assemble_ctx(cfg); ctx.channel='acp:synthetic'
    manager = DesktopSessionManager(dir=tmp_path/'sessions',bus=ctx.bus,config=cfg)
    ctx.session = manager
    sid = await manager.create(); log = await manager.open_session(sid)
    original = engine.activate_orchestration
    async def fail(*args,**kw): raise OSError('activation fault')
    monkeypatch.setattr(engine,'activate_orchestration',fail)
    try:
        with pytest.raises(OSError,match='activation fault'):
            await acp._ensure_engine_queue(ctx,log,sid)
        assert not manager._stores[sid]._fh.closed
        monkeypatch.setattr(engine,'activate_orchestration',original)
        q = await acp._ensure_engine_queue(ctx,log,sid)
        assert q is not None
        await log.append('user.message',{'content':'retry works'},actor='user',sync=True)
    finally:
        await acp.close_owned_runtimes(ctx)
        await manager.shutdown_all()


async def test_job_cancel_waits_for_real_terminal_without_timeout(e2e_factory, monkeypatch):
    from pyharness.core import orchestration
    s = await e2e_factory(sid='s-job-cleanup-terminal'); await s.boot()
    q = s.ctx.task_queue; jobs = s.ctx.engine_spine.jobs
    entered, finishing, release = asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def run(task):
        entered.set()
        try: await asyncio.Event().wait()
        finally:
            finishing.set(); await release.wait()
    q._runner = SimpleNamespace(run_for_task=run)
    jid = await jobs.start('synthetic',SimpleNamespace(owner='test'))
    await entered.wait()
    original = asyncio.wait_for
    def reject_timeout(awaitable,timeout):
        if timeout == 10:
            awaitable.close()
            raise AssertionError('must retain work owner until actual terminal')
        return original(awaitable,timeout)
    monkeypatch.setattr(orchestration.asyncio,'wait_for',reject_timeout)
    task = asyncio.create_task(jobs.cancel(jid,by='test'))
    await finishing.wait()
    try:
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await task
    assert q._exec is None or q._exec.done()
    assert jobs._running[jid].reason != 'CYC-999'



async def test_acp_eof_reaps_two_running_sessions_and_stores(tmp_path,adapter_factory):
    import io
    from pyharness import acp,persistence
    from pyharness.cli import assemble_ctx
    from pyharness.desktop.sessions import DesktopSessionManager
    from tests.support import isolated_settings
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    ctx=assemble_ctx(cfg);ctx.channel='acp:synthetic'
    manager=DesktopSessionManager(dir=tmp_path/'sessions',bus=ctx.bus,config=cfg);ctx.session=manager
    queues=[];stores=[];entered=[];finished=[]
    try:
        for i in range(2):
            sid=await manager.create();log=await manager.open_session(sid)
            q=await acp._ensure_engine_queue(ctx,log,sid);queues.append(q);stores.append(manager._stores[sid])
            ready=asyncio.Event();entered.append(ready)
            async def run(task,ready=ready,i=i):
                ready.set()
                try:await asyncio.Event().wait()
                finally:finished.append(i)
            q._runner=SimpleNamespace(run_for_task=run)
            await q.submit('synthetic');await ready.wait()
        assert await acp.serve(ctx,stdin=io.StringIO(''),stdout=io.StringIO())==0
        assert sorted(finished)==[0,1]
        assert all(q._exec is None or q._exec.done() for q in queues)
        assert all(s._fh.closed and s._lock_path is None for s in stores)
    finally:await acp.close_owned_runtimes(ctx);await manager.shutdown_all()


@pytest.mark.parametrize('amounts',[(2,3),(7,11),(0,9)])
async def test_shared_adapter_usage_and_close_are_session_scoped(tmp_path,amounts):
    from pyharness.application.service import ApplicationService
    from pyharness.cli import assemble_ctx
    from tests.support import isolated_settings
    from tests.unit.test_llm import _ctx,_raw,_usage
    cfg=isolated_settings(tmp_path)
    service=ApplicationService(assemble_ctx(cfg),channel='desktop')
    ids=[await service.create_session(),await service.create_session()]
    for sid in ids:await service.queue_for(sid)
    a,b=(service._engines[sid] for sid in ids)
    adapter=a.llm.require();assert adapter is b.llm.require()
    entered=0;both=asyncio.Event();closed=[]
    class Transport:
        async def complete(self,request):
            nonlocal entered
            entered+=1
            if entered>=2:both.set()
            await both.wait()
            amount=int(request['messages'][0]['content'])
            return _raw('synthetic',usage=_usage(amount,1))
        async def aclose(self):closed.append(True)
    adapter._transport=Transport()
    try:
        ca,cb=_ctx(a.session,counters=a.counters),_ctx(b.session,counters=b.counters)
        async with asyncio.timeout(5):
            await asyncio.gather(adapter.chat([{'role':'user','content':str(amounts[0])}],ctx=ca),adapter.chat([{'role':'user','content':str(amounts[1])}],ctx=cb))
        assert a.counters.task_total().in_tokens==amounts[0]
        assert b.counters.task_total().in_tokens==amounts[1]
        await a.close();assert closed==[]
        await adapter.chat([{'role':'user','content':'1'}],ctx=cb)
        assert b.counters.task_total().in_tokens==amounts[1]+1
        assert a.counters.task_total().in_tokens==amounts[0]
    finally:await service.shutdown()
    assert closed==[True]


@pytest.mark.parametrize('queued',[False,True])
async def test_task_cancel_audit_failure_still_stops_work(e2e_factory,monkeypatch,queued):
    s=await e2e_factory(sid='s-cancel-audit-fault');await s.boot()
    q=s.ctx.task_queue;entered=asyncio.Event();finished=asyncio.Event();executed=[]
    async def run(task):
        executed.append(task.intent);entered.set()
        try:await asyncio.Event().wait()
        finally:finished.set()
    q._runner=SimpleNamespace(run_for_task=run)
    running=await q.submit('running');await entered.wait()
    target=await q.submit('must-not-execute') if queued else running
    original=s.ctx.session.append
    async def fault(type_,*args,**kw):
        if type_==('task.failed' if queued else 'system.cancelled'):raise OSError('audit fault')
        return await original(type_,*args,**kw)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(s.ctx.session,'append',fault)
            with pytest.raises(OSError,match='audit fault'):await q.cancel(target)
        if queued:
            result=await q.wait_for(target,timeout=.2)
            assert not result.ok and executed==['running']
        else:
            await asyncio.wait_for(finished.wait(),.2)
    finally:await q.shutdown()

async def test_job_cancel_audit_failure_waits_for_real_cleanup(e2e_factory,monkeypatch):
    from pyharness.errors import PyHError
    s=await e2e_factory(sid='s-job-audit-cleanup');await s.boot()
    q=s.ctx.task_queue;jobs=s.ctx.engine_spine.jobs
    entered,finishing,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def run(task):
        entered.set()
        try:await asyncio.Event().wait()
        finally:finishing.set();await release.wait()
    q._runner=SimpleNamespace(run_for_task=run)
    jid=await jobs.start('synthetic',SimpleNamespace(owner='test'))
    await asyncio.wait_for(entered.wait(),3)
    original=s.ctx.session.append
    async def fault(type_,*args,**kw):
        if type_=='system.cancelled':raise PyHError('PERS-202',ctx={'fault':'synthetic audit'})
        return await original(type_,*args,**kw)
    with monkeypatch.context() as patch:
        patch.setattr(s.ctx.session,'append',fault)
        cancelling=asyncio.create_task(jobs.cancel(jid,by='test'))
        try:
            await asyncio.wait_for(finishing.wait(),3)
            for _ in range(12):await asyncio.sleep(0)
            assert not cancelling.done()
            assert not jobs._running[jid].terminal
        finally:
            release.set()
            with pytest.raises(PyHError,match='PERS-202'):await asyncio.wait_for(cancelling,3)
    assert q._exec is None or q._exec.done()


async def test_independent_services_do_not_mutate_shared_model_configuration(tmp_path):
    from pyharness.application.service import ApplicationService
    from pyharness.cli import assemble_ctx
    from tests.support import isolated_settings
    cfg=isolated_settings(tmp_path)
    original=cfg.llm.model
    services=[ApplicationService(assemble_ctx(cfg),channel='desktop') for _ in range(2)]
    actual=[]
    try:
        for service in services:
            sid=await service.create_session();await service.queue_for(sid)
            actual.append(service._engines[sid].llm.require().model)
        assert actual==[original,original]
        assert cfg.llm.model==original
    finally:
        for service in services:await service.shutdown()

async def test_independent_standalone_engines_preserve_shared_configuration(tmp_path):
    from pyharness.engine import assemble_real_engine
    from tests.support import isolated_settings
    cfg=isolated_settings(tmp_path);original=cfg.llm.model;contexts=[]
    try:
        for sid in ('s-first','s-second'):
            contexts.append(await assemble_real_engine(cfg,sid=sid,sessions_dir=tmp_path/'sessions',channel='cli'))
        assert [ctx.llm.require().model for ctx in contexts]==[original,original]
        assert cfg.llm.model==original
    finally:
        for ctx in contexts:await ctx.engine_spine.close()

async def test_registry_shutdown_failure_still_closes_other_tenants():
    from pyharness.application.registry import ApplicationServiceRegistry
    calls=[]
    class Service:
        def __init__(self,name):self.name=name
        async def shutdown(self):
            calls.append(self.name)
            if self.name=='first':raise OSError('first tenant close fault')
    registry=ApplicationServiceRegistry(SimpleNamespace())
    registry._services={'first':Service('first'),'second':Service('second')}
    with pytest.raises(OSError,match='first tenant close fault'):await registry.close_all()
    assert calls==['first','second']

@pytest.mark.parametrize('cancel_first',[False,True])
async def test_job_completion_cancel_barrier_has_one_terminal(e2e_factory,cancel_first):
    s=await e2e_factory(sid='s-job-final-race');await s.boot()
    q=s.ctx.task_queue;jobs=s.ctx.engine_spine.jobs
    entered,release=asyncio.Event(),asyncio.Event()
    async def run(task):entered.set();await release.wait()
    q._runner=SimpleNamespace(run_for_task=run)
    jid=await jobs.start('synthetic',SimpleNamespace(owner='test'))
    async with asyncio.timeout(5):
        await entered.wait()
        if cancel_first:
            cancelling=asyncio.create_task(jobs.cancel(jid,by='test'))
            await asyncio.sleep(0);release.set()
        else:
            release.set();cancelling=asyncio.create_task(jobs.cancel(jid,by='test'))
        await cancelling
        await asyncio.gather(jobs._running[jid].task,return_exceptions=True)
    rows=[e for e in s.ctx.session.events_after(0) if e.type in ('job.completed','job.failed') and e.payload['job_id']==jid]
    assert len(rows)==1
    assert jobs._running[jid].terminal
    assert q._exec is None or q._exec.done()
    assert await jobs.cancel(jid,by='test') is False


async def test_web_shutdown_reaps_active_sse_and_task(tmp_path,adapter_factory,monkeypatch):
    from pyharness.desktop.app import DesktopApp
    from pyharness.cli import assemble_ctx
    from tests.support import isolated_settings
    from tests.unit.test_desktop import _FakeRequest
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    app=DesktopApp(assemble_ctx(cfg));service=app.service
    sid=await service.create_session();q=await service.queue_for(sid)
    entered,release=asyncio.Event(),asyncio.Event();finished=[]
    async def run(task):
        entered.set()
        try:await release.wait()
        finally:finished.append(True)
    q._runner=SimpleNamespace(run_for_task=run)
    response=await app.stream_sse(_FakeRequest(),sid=sid,after_seq=0)
    registered=asyncio.Event();register=app.hub.register
    def registration(*args,**kwargs):
        result=register(*args,**kwargs);registered.set();return result
    monkeypatch.setattr(app.hub,'register',registration)
    async def pump():
        async for _ in response.body_iterator:pass
    stream=asyncio.create_task(pump())
    try:
        async with asyncio.timeout(5):
            await registered.wait();await q.submit('synthetic');await entered.wait()
            closing=asyncio.create_task(app._shutdown_async());release.set()
            await closing;await stream
        assert finished==[True]
        assert not app.hub.clients
        assert q._exec is None or q._exec.done()
    finally:
        release.set();await app._shutdown_async();stream.cancel();await asyncio.gather(stream,return_exceptions=True)

@pytest.mark.parametrize('failure',[RuntimeError,asyncio.CancelledError])
async def test_optional_index_activation_failure_releases_connection(e2e_factory,monkeypatch,failure):
    from pyharness import engine
    from pyharness.core.session_query import SessionQueryIndex
    s=await e2e_factory(sid='s-index-init-fault');await s.boot()
    opened=[]
    async def fail(self,ctx):opened.append(self);raise failure('synthetic index announce fault')
    monkeypatch.setattr(SessionQueryIndex,'announce',fail)
    spine=s.ctx.engine_spine
    ag=SimpleNamespace(session_id=spine.session.sid,ctx=SimpleNamespace(session=spine.session,bus=spine.bus))
    try:
        if failure is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):await engine._activate_session_caps(ag,spine)
        else:await engine._activate_session_caps(ag,spine)
        assert opened and all(index._db is None for index in opened)
    finally:
        for index in opened:await index.detach(None)

async def test_optional_index_cleanup_preserves_new_cancellation(e2e_factory,monkeypatch):
    from pyharness import engine
    from pyharness.core.session_query import SessionQueryIndex
    s=await e2e_factory(sid='s-index-cleanup-cancel');await s.boot();spine=s.ctx.engine_spine
    entered,release=asyncio.Event(),asyncio.Event();closed=[]
    async def announce(self,ctx):raise RuntimeError('primary optional fault')
    original=SessionQueryIndex.detach
    async def detach(self,ctx):
        entered.set();await release.wait();await original(self,ctx);closed.append(True)
    monkeypatch.setattr(SessionQueryIndex,'announce',announce)
    monkeypatch.setattr(SessionQueryIndex,'detach',detach)
    ag=SimpleNamespace(session_id=spine.session.sid,ctx=SimpleNamespace(session=spine.session,bus=spine.bus))
    task=asyncio.create_task(engine._activate_session_caps(ag,spine))
    try:
        await asyncio.wait_for(entered.wait(),3)
        task.cancel();await asyncio.sleep(0);assert not task.done();release.set()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,3)
        assert closed==[True]
    finally:release.set();await asyncio.gather(task,return_exceptions=True)
