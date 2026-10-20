"""Restored original configuration reaches actual new queued model contexts."""
import asyncio
import copy
import pytest
from pyharness import cli
from pyharness.application.configuration_history import ConfigurationHistory
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


async def test_restore_changes_new_queued_runs_and_preserves_inflight_context_and_original_versions(tmp_path, adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter = adapter_factory(cfg)
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []
    original_chat = adapter.chat
    async def chat(messages, tools=None, *, ctx):
        observed.append((ctx.session.sid, ctx, copy.deepcopy(ctx.config.model_dump())))
        if len(observed) == 1:
            entered.set()
            await release.wait()
        return await original_chat(messages, tools, ctx=ctx)
    adapter.chat = chat
    app = DesktopApp(cli.assemble_ctx(cfg)); service = app.service
    allow = lambda: None
    history = ConfigurationHistory(service)
    try:
        profile = {'id': 'synthetic', 'model': cfg.llm.model, 'base_url': 'http://127.0.0.1:1',
                   'api_key': 'synthetic-runtime-history-key', 'temperature': 0.2, 'max_tokens': 1000}
        service.save_model_profile(profile)
        definition = service.platform.save_agent({'name': 'Original queue policy',
            'model_connection': 'synthetic', 'max_rounds': 12, 'allowed_tools': []})
        agent_id = definition['agent_id']
        v1 = service.platform.publish_agent(agent_id)
        revisions = {}
        for kind, resource in [('model', 'synthetic'), ('agent', agent_id)]:
            state = history.current(kind, resource, authorize=allow)
            revisions[kind] = history.capture(kind, resource, {'command_key': 'runtime-capture-'+kind,
                'source_sha256': state['source_sha256']}, authorize=allow)['revision']['revision_id']
        service.save_model_profile({k: v for k, v in {**profile, 'temperature': 0.8, 'max_tokens': 500}.items() if k != 'api_key'})
        service.platform.save_agent({**v1['snapshot'], 'name': 'Narrow current queue policy', 'max_rounds': 4}, agent_id)
        v2 = service.platform.publish_agent(agent_id)
        async def start(version):
            sid = await service.create_session()
            log = await service.require_session(sid)
            await log.append('session.renamed', {'new_title': 'Owned queued configuration proof', 'by': 'user'}, actor='user')
            run = await service.platform.start_run({'session_id': sid, 'agent_id': agent_id,
                'agent_version': version, 'text': 'Complete this synthetic queued task.'})
            return sid, log, run
        old_sid, old_log, old_run = await start(2)
        await asyncio.wait_for(entered.wait(), 10)
        old_ctx, fixed = observed[0][1:]
        assert fixed['llm']['temperature'] == 0.8 and fixed['llm']['max_tokens'] == 500
        assert fixed['loop']['max_turns'] == 4
        log_path = old_log._persistence.path
        before = log_path.read_bytes()
        for kind, resource in [('model', 'synthetic'), ('agent', agent_id)]:
            preview = history.preview(kind, resource, revisions[kind], authorize=allow)
            assert preview['compatible'], preview
            history.apply(kind, resource, revisions[kind], {'command_key': 'runtime-apply-'+kind,
                'preview_sha256': preview['preview_sha256'], 'confirm_apply': True,
                'confirm_permission_increase': True}, authorize=allow)
        assert old_ctx.config.model_dump() == fixed
        assert log_path.read_bytes() == before
        assert service.platform.version(agent_id, 1) == v1
        assert service.platform.version(agent_id, 2) == v2
        new_sid, new_log, new_run = await start(3)
        assert (await asyncio.wait_for(service._queues[new_sid].wait_for(new_run['task_id']), 10)).ok
        new = next(value for sid, _, value in observed if sid == new_sid)
        assert new['llm']['temperature'] == 0.2 and new['llm']['max_tokens'] == 1000
        assert new['loop']['max_turns'] == 12
        assert old_ctx.config.model_dump() == fixed
        release.set()
        assert (await asyncio.wait_for(service._queues[old_sid].wait_for(old_run['task_id']), 10)).ok
        assert log_path.read_bytes().startswith(before)
        assert service.platform.binding(old_log)['definition']['current_version'] == 2
        assert service.platform.binding(new_log)['definition']['current_version'] == 3
        for log in (old_log, new_log):
            assert len([e for e in log.events_after(0) if e.type == 'task.enqueued']) == 1
            assert len([e for e in log.events_after(0) if e.type == 'llm.response']) == 1
        assert adapter.n == 2
    finally:
        release.set()
        await service.shutdown()
