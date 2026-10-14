"""Only gaps from REPO-VERIFICATION: approval endings and continuing effects.

Real service/engine/queue/governance/JSONL. The model is scripted. The explicit
test provider uses a real thread and files so stopping a wait cannot fake rollback.
"""
import asyncio
import json
from pathlib import Path
import threading

import pytest

from pyharness import cli
from pyharness.application.service import ApplicationService
from pyharness.core.tools_registry import ToolDefinition
from tests.integration.test_verification_runtime import local_settings


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize('ending', ['deny', 'timeout', 'cancel'])
async def test_real_approval_endings_leave_file_unchanged(tmp_path, adapter_factory, ending):
    cfg = local_settings(tmp_path)
    cfg.security.approval.ttl_ms = 1000 if ending == 'timeout' else 10000
    adapter_factory(cfg, [{'id': 'gap-approval', 'name': 'fs.write_file',
                          'args': {'path': 'protected.txt', 'content': 'FORBIDDEN'}}])
    service = ApplicationService(cli.assemble_ctx(cfg))
    try:
        sid = await service.create_session()
        log, spine = await service.public_spine_for(sid)
        target = Path(spine.scope.policy.workspace_root) / 'protected.txt'
        target.write_text('ORIGINAL', encoding='utf-8')
        submitted = await service.create_message(sid, 'controlled overwrite request')
        queue = spine.task_queue
        await until(lambda: spine.approval.pending_count() == 1)
        aid, request = next(iter(spine.approval._pending.items()))
        assert target.read_text(encoding='utf-8') == 'ORIGINAL'
        assert queue.status().paused
        assert not any(e.type == 'tool.result' for e in log.events_after(0))
        if ending == 'deny':
            await service.decide_approval(aid, 'deny', sid=sid)
        elif ending == 'cancel':
            assert await queue.cancel(submitted['task_id'])
        result = await queue.wait_for(submitted['task_id'], timeout=5)
        outcome = {'timeout': 'approval.timeout', 'deny': 'approval.denied',
                   'cancel': 'system.cancelled'}[ending]
        await until(lambda: any(e.type == outcome for e in log.events_after(0))
                    and not queue.status().paused and queue.status().running is None)
        assert target.read_text(encoding='utf-8') == 'ORIGINAL'
        assert spine.approval.pending_count() == 0
        assert request.timer is None or request.timer.cancelled()
        if ending == 'cancel':
            assert not result.ok and result.code == 'cancelled'
            assert request.state == 'denied' and request.waiter.done()
        else:
            # A denied tool is fed back to the model; the conversation can finish.
            assert result.ok
        await spine.persistence.flush()
        disk = list(spine.persistence.replay())
        verdicts = [e for e in disk if e.type == outcome]
        assert len(verdicts) == 1
        if ending == 'cancel':
            # APR-502 cancels the batch without inventing a human verdict.
            # Existing contract records the cancellation at task level instead.
            assert verdicts[0].payload['what'] == f"task:{submitted['task_id']}"
            assert not any(e.type in {'approval.denied', 'approval.timeout'} for e in disk)
            failed = [e for e in disk if e.type == 'task.failed']
            assert len(failed) == 1
            assert failed[0].payload['task_id'] == submitted['task_id']
            assert failed[0].payload['reason'] == 'cancelled'
        else:
            assert verdicts[0].payload['approval_id'] == aid
        assert len([e for e in disk if e.type == 'queue.suspended']) == 1
        assert len([e for e in disk if e.type == 'queue.resumed']) == 1
        assert not any(e.type in {'approval.granted', 'tool.result'} for e in disk)
        assert len([e for e in disk if e.type == 'decision.issued']) == 1
        assert not any(e.type == 'receipt.emitted' for e in disk)
        assert len([e for e in disk if e.type == 'segment.end']) == 1
        audit = await service.governance_audit(sid, reconcile=True)
        assert audit['findings'] == []
        evidence = {'case': ending, 'sid': sid, 'approval_id': aid,
                    'task_ok': result.ok, 'task_code': result.code,
                    'file': target.read_text(encoding='utf-8'),
                    'pending': spine.approval.pending_count(),
                    'outcome': outcome, 'audit_findings': audit['findings']}
        (tmp_path / 'gap-evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
        print(json.dumps(evidence))
    finally:
        await service.shutdown()


@pytest.mark.parametrize('ending', ['timeout', 'cancel'])
async def test_wait_end_records_continuing_sync_effects(tmp_path, adapter_factory, ending):
    cfg = local_settings(tmp_path)
    name = 'fs.verification_effect'
    adapter_factory(cfg, [{'id': 'gap-effect', 'name': name,
                          'args': {'path': 'effect.txt'}}])
    service = ApplicationService(cli.assemble_ctx(cfg))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    workers = []
    try:
        sid = await service.create_session()
        log, spine = await service.public_spine_for(sid)
        target = Path(spine.scope.policy.workspace_root) / 'effect.txt'

        def controlled_provider(args, ctx):
            workers.append(threading.current_thread())
            target.write_text('BEFORE', encoding='utf-8')
            entered.set()
            try:
                if not release.wait(10):
                    raise TimeoutError('test cleanup gate was not released')
                target.write_text('BEFORE+AFTER', encoding='utf-8')
                return {'written': True}
            finally:
                finished.set()

        spine.tool_registry.register_tool(ToolDefinition(
            name=name, description='Controlled verification provider with real file effects',
            schema={'type': 'object', 'properties': {'path': {'type': 'string'}},
                    'required': ['path']}, danger='low', owner='verification',
            timeout_s=1 if ending == 'timeout' else 10), provider=controlled_provider)
        submitted = await service.create_message(sid, 'controlled continuing effect')
        await until(entered.is_set)
        assert target.read_text(encoding='utf-8') == 'BEFORE'
        if ending == 'cancel':
            assert await spine.task_queue.cancel(submitted['task_id'])
        result = await spine.task_queue.wait_for(submitted['task_id'], timeout=5)
        assert not finished.is_set() and workers[0].is_alive()
        assert spine.tools._zombie_pools, 'still-running worker must remain tracked'
        await spine.persistence.flush()
        disk = list(spine.persistence.replay())
        kind = 'tool.error' if ending == 'timeout' else 'tool.result'
        events = [e for e in disk if e.type == kind and e.payload.get('call_id') == 'gap-effect']
        assert len(events) == 1
        payload = events[0].payload
        message = payload.get('message') or payload.get('summary')
        if ending == 'timeout':
            assert payload['code'] == 'TLB-805'
        else:
            assert not result.ok and result.code == 'cancelled'
            assert payload['ok'] is False and payload['truncated'] is True
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        await asyncio.to_thread(workers[0].join, 3)
        assert not workers[0].is_alive()
        assert target.read_text(encoding='utf-8') == 'BEFORE+AFTER'
        evidence = {'case': ending, 'sid': sid, 'task_ok': result.ok,
                    'task_code': result.code, 'event': kind, 'feedback': message,
                    'worker_alive_after_wait': True, 'worker_joined': True,
                    'effects_after_wait': target.read_text(encoding='utf-8')}
        (tmp_path / 'gap-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(evidence, ensure_ascii=False))
        # Feedback must not imply that cancelling the wait stopped the provider.
        assert '仍' in message and '运行' in message
        assert '副作用' in message and ('未撤销' in message or '不' in message)
    finally:
        release.set()
        for worker in workers:
            await asyncio.to_thread(worker.join, 3)
            assert not worker.is_alive(), 'test-owned thread leaked'
        await service.shutdown()
