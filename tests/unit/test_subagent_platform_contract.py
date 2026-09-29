"""Public subtask lifecycle: deterministic model, real engine and persistence."""
import asyncio
import httpx
from types import SimpleNamespace

import pytest

from pyharness.core.subagent import SubagentSpec
from pyharness.engine import assemble_real_engine
from pyharness.errors import PyHError
from tests.support import isolated_settings
from pyharness import cli
from pyharness.desktop.app import DesktopApp


async def test_child_model_failure_is_not_successful_join(tmp_path, adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter = adapter_factory(cfg)

    async def fail(*args, **kwargs):
        raise PyHError('LLM-303')

    adapter.chat = adapter.chat_stream = fail
    ctx = await assemble_real_engine(cfg, sid='s-child-failure',
                                     sessions_dir=tmp_path/'sessions', channel='desktop')
    try:
        await ctx.session.append('session.created', {'title':'synthetic','model':cfg.llm.model},
                                 actor='system', sync=True)
        manager = ctx.engine_spine.subagent
        child = await manager.spawn(SubagentSpec(task='synthetic child', depth=1),
                                    SimpleNamespace(round_seq=1))
        with pytest.raises(PyHError) as caught:
            await asyncio.wait_for(manager.join(child), 5)
        assert caught.value.code == 'LLM-303'
        events = list(ctx.session.events_after(0))
        assert any(e.type == 'subagent.failed' and e.payload['sub_id'] == child for e in events)
        assert not any(e.type == 'subagent.joined' for e in events)
    finally:
        await ctx.engine_spine.close()


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
@pytest.mark.parametrize('notify', [True,False])
async def test_public_subtask_history_survives_restart(tmp_path, adapter_factory, outcome, notify):
    cfg = isolated_settings(tmp_path)
    adapter = adapter_factory(cfg)
    entered = asyncio.Event()

    if outcome != 'completed':
        async def response(*args, **kwargs):
            entered.set()
            if outcome == 'failed':
                raise PyHError('LLM-303')
            await asyncio.Event().wait()
        adapter.chat = adapter.chat_stream = response

    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        sid = await app.service.create_session()
        child = await app.service.spawn_subagent(sid, 'synthetic task', notify=notify)
        manager = app.service._engines[sid].subagent
        if outcome == 'completed':
            assert await asyncio.wait_for(manager.join(child), 5) == 'done'
        elif outcome == 'failed':
            with pytest.raises(PyHError):
                await asyncio.wait_for(manager.join(child), 5)
        else:
            await asyncio.wait_for(entered.wait(), 5)
            assert await app.service.cancel_subagent(sid, child)
        result = await app.service.list_subagents(sid)
        row = next(r for r in result['history'] if r['session_id'] == child)
        assert row['parent_session_id'] == sid
        assert row['status'] == outcome
        assert row['finished_at']
    finally:
        await app.service.shutdown()
    reopened = DesktopApp(cli.assemble_ctx(cfg))
    try:
        result = await reopened.service.list_subagents(sid)
        assert result['history'][0]['status'] == outcome
        assert not reopened.service._engines, 'Reading history must not restart work'
    finally:
        await reopened.service.shutdown()


def test_subtask_projection_preserves_parent_and_interruption():
    from pyharness.application.platform_projection import subtask_history
    def event(seq, kind, **payload):
        return SimpleNamespace(seq=seq, type=kind, payload=payload,
                               ts='2026-09-28T00:00:00Z', task_id=None)
    events = [event(1, 'segment.start', task_id='parent-task'),
              event(2, 'subagent.spawned', sub_id='child', parent_seq=1,
                    task='synthetic api_key=synthetic-secret')]
    running = subtask_history('parent', events, [{'sub_id':'child'}])[0]
    assert running['parent_run_id'] == 'parent~parent-task'
    assert running['status'] == 'running'
    assert 'synthetic-secret' not in running['task']
    interrupted = subtask_history('parent', events)[0]
    assert interrupted['status'] == 'failed'
    assert interrupted['error_code'] == 'runtime_interrupted'


async def test_subtask_history_api_is_authenticated_and_read_only(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        sid = await app.service.create_session()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                                     base_url='http://127.0.0.1') as client:
            url = f'/api/sessions/{sid}/subagents'
            assert (await client.get(url)).status_code == 401
            result = await client.get(url, headers={'X-PyHarness-Token':app._api_token})
            assert result.status_code == 200
            assert result.json()['history'] == []
            assert not app.service._engines
    finally:
        await app.service.shutdown()


@pytest.mark.parametrize('decision', ['approve','cancel'])
async def test_child_write_waits_for_parent_approval(tmp_path, adapter_factory, decision):
    cfg = isolated_settings(tmp_path)
    adapter = adapter_factory(cfg, [{'id':'child-write', 'name':'fs.write_file',
                                    'args':{'path':'synthetic.txt','content':'approved child output'}}])
    original_chat = adapter.chat
    async def with_existing_file(*args, **kwargs):
        from pathlib import Path
        if adapter.n == 0:
            root = Path(kwargs['ctx'].scope.policy.workspace_root)
            (root/'synthetic.txt').write_text('original synthetic input', encoding='utf-8')
        return await original_chat(*args, **kwargs)
    adapter.chat = adapter.chat_stream = with_existing_file
    app = DesktopApp(cli.assemble_ctx(cfg))
    pending = asyncio.Queue()
    async def observe(kind, event):
        pending.put_nowait((event.session_id, event.seq))
    try:
        sid = await app.service.create_session()
        child = await app.service.spawn_subagent(sid, 'write synthetic.txt after approval')
        manager = app.service._engines[sid].subagent
        # Children own their event bus/log. Observe that real bus, then use the
        # public service decision API with the child's full approval identity.
        manager._children[child].session._bus.subscribe(
            'approval.requested', observe, owner='child-acceptance')
        approval_sid, ident = await asyncio.wait_for(pending.get(), 5)
        assert approval_sid == child
        root = tmp_path/'workspaces'/child
        assert (root/'synthetic.txt').read_text('utf-8') == 'original synthetic input'
        rows = (await app.service.platform.approvals())['approvals']
        assert any(r['status'] == 'pending' for r in rows)
        if decision == 'approve':
            await app.service.decide_approval(ident, 'approve', sid=child)
            assert await asyncio.wait_for(manager.join(child), 5) == 'done'
            assert (root/'synthetic.txt').read_text('utf-8') == 'approved child output'
            assert (await app.service.list_subagents(sid))['history'][0]['status'] == 'completed'
        else:
            assert await app.service.cancel_subagent(sid, child)
            assert not (await app.service.pending_approvals())['pending']
            assert (root/'synthetic.txt').read_text('utf-8') == 'original synthetic input'
            assert (await app.service.list_subagents(sid))['history'][0]['status'] == 'cancelled'
            assert not manager._runner.approval_providers
            assert not manager._runner._stores
            with pytest.raises(PyHError):
                await app.service.decide_approval(ident,'approve',sid=child)
    finally:
        await app.service.shutdown()


async def test_parallel_children_have_independent_approval_identity(tmp_path, adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter = adapter_factory(cfg)
    per_child = {}
    async def response(*args, **kwargs):
        from pathlib import Path
        ctx = kwargs['ctx']
        if ctx.session.sid not in per_child:
            root = Path(ctx.scope.policy.workspace_root)
            (root/'synthetic.txt').write_text('before', encoding='utf-8')
            per_child[ctx.session.sid] = type(adapter)(adapter.model,[{'id':'write',
                'name':'fs.write_file','args':{'path':'synthetic.txt','content':'after'}}])
        return await per_child[ctx.session.sid].chat(*args, **kwargs)
    adapter.chat = adapter.chat_stream = response
    app = DesktopApp(cli.assemble_ctx(cfg))
    pending = asyncio.Queue()
    async def observe(kind, event):
        pending.put_nowait((event.session_id, event.seq))
    joins=[]
    try:
        sid = await app.service.create_session()
        children=[]
        for _ in range(2):
            child=await app.service.spawn_subagent(sid,'overwrite synthetic.txt after approval')
            manager=app.service._engines[sid].subagent
            manager._children[child].session._bus.subscribe('approval.requested',observe,owner='acceptance')
            children.append(child)
        joins=[asyncio.create_task(manager.join(child)) for child in children]
        first=await asyncio.wait_for(pending.get(),5)
        second=await asyncio.wait_for(pending.get(),5)
        assert first[1] == second[1], 'The regression requires colliding local sequence numbers'
        assert {row['status'] for row in (await app.service.list_subagents(sid))['history']} == {'waiting_approval'}
        await app.service.decide_approval(first[1],'approve',sid=first[0])
        assert (tmp_path/'workspaces'/second[0]/'synthetic.txt').read_text('utf-8')=='before'
        await app.service.decide_approval(second[1],'approve',sid=second[0])
        assert await asyncio.wait_for(asyncio.gather(*joins),5)==['done','done']
        assert not (await app.service.pending_approvals())['pending']
        assert all(row['status']=='completed' for row in (await app.service.list_subagents(sid))['history'])
    finally:
        await app.service.shutdown()
        await asyncio.gather(*joins, return_exceptions=True)


@pytest.mark.parametrize('child_fails',[False,True])
async def test_platform_parent_uses_governed_child_and_cannot_hide_failure(tmp_path,adapter_factory,child_fails):
    cfg=isolated_settings(tmp_path)
    adapter=adapter_factory(cfg,[{'id':'delegate','name':'subagent.spawn','args':{'task':'synthetic child'}}])
    parent_response=adapter.chat
    child_adapter=type(adapter)(adapter.model)
    async def response(*args,**kwargs):
        if kwargs['ctx'].session.sid.startswith('s-sub'):
            if child_fails: raise PyHError('LLM-303')
            return await child_adapter.chat(*args,**kwargs)
        return await parent_response(*args,**kwargs)
    adapter.chat=adapter.chat_stream=response
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        platform=app.service.platform
        agent=platform.save_agent({'name':'delegate','allowed_tools':['subagent.spawn']})
        platform.publish_agent(agent['agent_id'])
        run=await platform.start_run({'text':'delegate synthetic','agent_id':agent['agent_id'],'agent_version':1})
        result=await asyncio.wait_for(app.service._queues[run['session_id']].wait_for(run['task_id']),10)
        history=(await app.service.list_subagents(run['session_id']))['history']
        assert len(history)==1
        assert history[0]['status']==('failed' if child_fails else 'completed')
        assert result.ok is not child_fails
        assert (await platform.run(run['run_id']))['status']==('failed' if child_fails else 'completed')
    finally:
        await app.service.shutdown()
