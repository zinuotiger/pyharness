"""The bootstrap approval service must not observe the runtime's verdicts."""
import asyncio
from types import SimpleNamespace

import pytest

from pyharness import cli
from pyharness.core.approval import ApprovalProvider
from tests.support import isolated_settings


@pytest.mark.parametrize('verdict', ['granted', 'denied'])
async def test_cli_replaces_bootstrap_approval_without_stale_callback(
        tmp_path, monkeypatch, verdict):
    cfg = isolated_settings(tmp_path)
    config = tmp_path / 'config.json'
    config.write_text(cfg.model_dump_json(), encoding='utf-8')
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    callbacks = []
    original = ApprovalProvider.on_verdict

    async def observed(self, type_, envelope):
        callbacks.append((self, type_, envelope.payload['approval_id']))
        await original(self, type_, envelope)

    monkeypatch.setattr(ApprovalProvider, 'on_verdict', observed)
    sh = await cli.bootstrap_shell(cli.parse_args(['chat', '--config', str(config)]))
    bootstrap = sh.ctx.approval
    request_task = None
    try:
        await cli._attach_engine(sh)
        runtime = sh.ctx.approval
        requested = asyncio.Queue()
        sh.ctx.bus.subscribe('approval.requested',
                             lambda _, event: requested.put_nowait(event.seq),
                             owner='approval-ownership-test')
        call = SimpleNamespace(name='fs.write_file', args={'path': 'sample.txt'},
                               raw_args={'path': 'sample.txt'}, call_id='bounded',
                               parent_seq=None)
        request_task = asyncio.create_task(runtime.request(
            call, 'synthetic approval only; no provider execution', sh.ctx,
            binding='synthetic-binding'))
        aid = await asyncio.wait_for(requested.get(), 5)
        decide = runtime.approve_async if verdict == 'granted' else runtime.deny_async
        await decide(aid, by='cli')
        assert await asyncio.wait_for(request_task, 5) == verdict
        events = list(sh.ctx.session.events_after())
        assert sum(e.type == 'approval.requested' for e in events) == 1
        assert sum(e.type == 'approval.' + verdict for e in events) == 1
        assert callbacks == [(runtime, 'approval.' + verdict, aid)]
        assert bootstrap._detached
        assert runtime.pending_count() == 0
        assert not any(e.type == 'system.error' and e.payload.get('code') == 'APR-503'
                       for e in events)
    finally:
        if request_task is not None and not request_task.done():
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
        await bootstrap.aclose()
        await cli._flush_session(sh)
