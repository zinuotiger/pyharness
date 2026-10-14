"""BUG-03/04/05 regressions converted from the preserved audit counterexamples."""
import asyncio
from types import SimpleNamespace

import pytest

from pyharness import cli
from pyharness.application.platform_runtime import PlatformRuntime
from pyharness.core.session import SessionLog
from pyharness.core.subagent import SubagentManager, SubagentSpec
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings


@pytest.mark.parametrize('ending', ['success', 'failure', 'denial', 'cancellation'])
async def test_queue_twenty_five_rounds_clear_buffers_and_keep_artifacts(tmp_path, adapter_factory, ending):
    """100 actual queued runs total; isolation/model are deterministic substitutes."""
    from tests.unit.test_platform_service import FakeBackend
    from pyharness.core.sandbox import SandboxManager
    from pyharness.application.platform_service import TEMPLATES
    cfg = isolated_settings(tmp_path)
    calls = [{'id':'write', 'name':'fs.write_file', 'args':{'path':'sample.txt','content':'changed synthetic data'}},
             {'id':'exec', 'name':'exec.python_run', 'args':{'code':'print(1)'}}]
    adapter = adapter_factory(cfg, calls)
    normal = adapter.chat
    async def response(*args, **kwargs):
        if ending == 'failure' and adapter.n >= 1:
            raise PyHError('LLM-303')
        return await normal(*args, **kwargs)
    adapter.chat = adapter.chat_stream = response
    app = DesktopApp(cli.assemble_ctx(cfg))
    pending = asyncio.Queue()
    async def observe(kind, event): pending.put_nowait((event.session_id,event.seq))
    app.ctx.bus.subscribe('approval.requested', observe, owner='audit-terminal-cleanup')
    p = app.service.platform
    backend = FakeBackend()
    p.sandboxes = SandboxManager(tmp_path/'synthetic-sandboxes', docker=backend)
    draft = p.save_agent(TEMPLATES[1]); p.publish_agent(draft['agent_id'])
    sid = await app.service.create_session()
    root = __import__('pathlib').Path(app.service._session_workspace(sid))
    root.mkdir(parents=True, exist_ok=True); (root/'sample.txt').write_text('original synthetic data')
    names = ('sandboxes','calls','locks','baselines','baseline_hashes','command_results',
             'process_tokens','failed_children','path_error_rounds','evidence')
    retained = None
    try:
        for i in range(25):
            adapter.n = 0
            submitted = await p.start_run(dict(session_id=sid,agent_id=draft['agent_id'],agent_version=1,text=f'synthetic {ending} {i}'))
            _, aid = await asyncio.wait_for(pending.get(), 5)
            await app.service.decide_approval(aid, 'approve', sid=sid)
            _, aid = await asyncio.wait_for(pending.get(), 5)
            if ending == 'cancellation':
                assert await app.service._queues[sid].cancel(submitted['task_id'], by='user')
            else:
                await app.service.decide_approval(aid, 'deny' if ending == 'denial' else 'approve', sid=sid)
            outcome = await asyncio.wait_for(app.service._queues[sid].wait_for(submitted['task_id']), 10)
            # A model may finish by explaining a denied command; denial does not
            # force unrelated generic dialogue to fail. The command never runs.
            assert outcome.ok == (ending in ('success', 'denial'))
            if ending == 'denial':
                log = await app.service.require_session(sid)
                assert any(e.type=='approval.denied' for e in log.events_after(0))
                assert backend.executions == 0
            runtime = app.service._engines[sid].platform_runtime
            assert all(not getattr(runtime,name) for name in names)
            assert all(record.status == 'destroyed' for record in p.sandboxes.records.values())
            if ending == 'success' and retained is None:
                artifacts = (await p.artifacts())['artifacts']
                patch = next(a for a in artifacts if a['filename']=='changes.patch')
                retained = (patch['artifact_id'], (await p.artifact_content(patch['artifact_id'],preview=True))[1])
    finally:
        await app.service.shutdown()
    if retained:
        app = DesktopApp(cli.assemble_ctx(cfg))
        try:
            assert (await app.service.platform.artifact_content(retained[0],preview=True))[1] == retained[1]
        finally:
            await app.service.shutdown()


@pytest.mark.parametrize('ending',['done','failed','cancelled'])
async def test_real_disk_restart_join_without_parent_notification(tmp_path, adapter_factory, ending):
    cfg=isolated_settings(tmp_path)
    adapter=adapter_factory(cfg)
    entered=asyncio.Event()
    original=adapter.chat
    async def answer(*args,**kwargs):
        entered.set()
        if ending=='failed': raise PyHError('LLM-303')
        if ending=='cancelled': await asyncio.Event().wait()
        return await original(*args,**kwargs)
    adapter.chat=adapter.chat_stream=answer
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        sid=await app.service.create_session()
        log,spine=await app.service.public_spine_for(sid)
        manager=spine.subagent
        child=await manager.spawn(SubagentSpec(task='synthetic child',depth=1,notify=False),
            SimpleNamespace(scope=spine.scope,round_seq=1))
        task=manager._children[child].task
        await asyncio.wait_for(entered.wait(),5)
        if ending=='cancelled': await manager.cancel(child)
        await asyncio.wait_for(asyncio.gather(task,return_exceptions=True),5)
        assert not any(e.type in ('subagent.joined','subagent.failed') for e in log.events_after(0))
        await app.service.shutdown()
        app=DesktopApp(cli.assemble_ctx(cfg))
        _,reopened=await app.service.public_spine_for(sid)
        assert not reopened.subagent._completed
        for _ in range(2):
            if ending=='done': assert await reopened.subagent.join(child)
            elif ending=='failed':
                with pytest.raises(PyHError,match='LLM-303'): await reopened.subagent.join(child)
            else:
                with pytest.raises(asyncio.CancelledError): await reopened.subagent.join(child)
        with pytest.raises(PyHError,match='EVT-101'):
            await reopened.subagent.join('unowned-child')
    finally:
        await app.service.shutdown()


async def test_late_first_join_repeat_and_evicted_history():
    log = SessionLog('audit-parent')
    await log.append('session.created', {'title': 'synthetic', 'model': 'synthetic'}, actor='system')
    async def child(env):
        return 'child-result'
    manager = SubagentManager(session=log, runner=child)
    manager._completed_limit = 2
    first = None
    for _ in range(5):
        ident = await manager.spawn(SubagentSpec(task='synthetic', depth=1))
        first = first or ident
        task = manager._children[ident].task
        await task
        assert await manager.join(ident) == 'child-result'
        assert await manager.join(ident) == 'child-result'
    assert len(manager._completed) == 2
    assert not manager._children
    assert await manager.join(first) == 'child-result'
    reopened = SubagentManager(session=log)
    assert await reopened.join(first) == 'child-result'
    with pytest.raises(PyHError):
        await reopened.join('unowned-child')
    await manager.detach()


@pytest.mark.parametrize('failure', [False, True])
async def test_hundred_releases_drop_task_buffers_even_if_destroy_fails(failure):
    class Sandboxes:
        async def destroy(self, ident):
            if failure:
                raise RuntimeError('synthetic cleanup failure')
            return SimpleNamespace(model_dump=lambda: {'sandbox_id': ident})
    class Session:
        async def append(self, *args, **kwargs):
            pass
    runtime = PlatformRuntime(SimpleNamespace(sandboxes=Sandboxes()), None, 'synthetic')
    names = ('sandboxes', 'calls', 'locks', 'baselines', 'baseline_hashes',
             'command_results', 'process_tokens', 'failed_children', 'path_error_rounds', 'evidence')
    for index in range(100):
        ident = str(index)
        for name in names:
            getattr(runtime, name)[ident] = {'synthetic': 'x' * 8192}
        runtime.sandboxes[ident] = 'synthetic-box'
        ctx = SimpleNamespace(task_id=ident, session=Session())
        if failure:
            with pytest.raises(RuntimeError):
                await runtime.release(ctx)
        else:
            await runtime.release(ctx)
        assert all(not getattr(runtime, name) for name in names)


async def test_old_run_trace_survives_new_activity_and_pages_without_loss(tmp_path):
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(cli.assemble_ctx(cfg))
    sid = None
    try:
        platform = app.service.platform
        sid = await app.service.create_session()
        log = await app.service.require_session(sid)
        for task, count in [('old', 3), ('later', 300)]:
            await log.append('task.enqueued', {'task_id': task, 'pos': 1}, actor='system')
            await log.append('task.started', {'task_id': task}, actor='system')
            for i in range(count):
                cid = f'{task}-{i}'
                await log.append('tool.call', {'name': 'fs.list_dir', 'args': {'path': '.'},
                    'raw_args': {'path': '.'}, 'call_id': cid}, actor='tool', task_id=task)
                await log.append('tool.result', {'name': 'fs.list_dir', 'call_id': cid,
                    'ok': True, 'summary': 'synthetic', 'truncated': False}, actor='tool', task_id=task)
            await log.append('agent.message', {'content': 'synthetic completion'}, actor='agent', task_id=task)
            await log.append('task.completed', {'task_id': task, 'reason': 'ok'}, actor='system')
        old = await platform.run(sid+'~old', limit=2)
        assert old['status'] == 'completed' and len(old['steps']) == 3
        assert len(old['trace']) == 2 and old['trace_truncated']
        found = list(old['trace'])
        cursor = old['trace_cursor']
        while cursor:
            page = await platform.run(sid+'~old', limit=2, cursor=cursor)
            found.extend(page['trace'])
            cursor = page['trace_cursor']
        assert len(found) == old['trace_total'] == 10  # Include three task lifecycle facts.
        assert len({s['span_id'] for s in found}) == 10
        assert all(s['run_id'] == sid+'~old' for s in found)
        with pytest.raises(PyHError):
            await platform.run(sid+'~old', cursor=sid+'~99999')
    finally:
        await app.service.shutdown()
    reopened = DesktopApp(cli.assemble_ctx(cfg))
    try:
        restored = await reopened.service.platform.run(sid+'~old')
        assert restored['trace_total'] == 10
        assert restored['status'] == 'completed'
    finally:
        await reopened.service.shutdown()
