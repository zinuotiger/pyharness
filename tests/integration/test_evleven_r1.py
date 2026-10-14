"""Pinned real R1 wheel tests. Normal paths never replace the MCP server.

Only synthetic data under .work/integration-evleven; no paid model. See the
integration report/setup script for provisioning the separately installed wheel.
"""
import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from scripts.evleven_integration import (BASE, ROOT, R1_PYTHON, harness, operate,
    submit, complete, tool_value, until, server_command)
from pyharness.core.mcp import McpClient, StdioTransport
from pyharness.errors import PyHError

pytestmark = [pytest.mark.controlled_process, pytest.mark.skipif(not R1_PYTHON.exists(), reason='Provision pinned local evleven R1 wheel first')]


@pytest.fixture
def data():
    path = BASE / 'tests' / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def evidence(data, name, value):
    (data / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'R1 evidence: {data / (name + ".json")}')


def client_for(data, timeout=10):
    return McpClient('evleven', StdioTransport(server_command(data), timeout_s=timeout))


async def test_real_handshake_allowlist_and_graceful_shutdown(data):
    async with harness(data/'ph', data/'remote') as h:
        assert h.mcp.initialization['protocolVersion'] == '2024-11-05'
        assert h.mcp.initialization['serverInfo']['name'] == 'evleven'
        assert all(h.spine.tool_registry.lookup(n).danger == 'high' for n in h.registered)
        assert not h.spine.tool_registry.has('mcp.evleven.memory_import')
        empty = tool_value(await operate(h, 'memory_search', {'query': 'nevermatchesxyz'}))
        assert empty == {'success': True, 'results': [], 'total': 0}
        evidence(data, 'handshake', {'initialization': h.mcp.initialization,
            'discovered': h.mcp._tools, 'exposed': h.registered, 'requests': h.requests})
    assert h.proc.returncode == 0


async def test_configured_denial_survives_assembly_and_preset(data):
    async with harness(data/'ph', data/'remote') as h:
        # Config deny must remain effective even though MCP tools arrive later.
        assert not h.spine.scope.can_use('mcp.evleven.memory_delete')
        outcome = await h.app.service.set_preset({'preset': 'standard'})
        assert outcome['ok']
        assert not h.spine.scope.can_use('mcp.evleven.memory_delete')
        item = tool_value(await operate(h, 'memory_create',
            {'content': 'protected synthetic ' + uuid.uuid4().hex, 'entity_id': 'test'}))['memory']
        sent = len(h.requests)
        events = await operate(h, 'memory_delete', {'memory_id': item['id']})
        assert len(h.requests) == sent
        assert any(e.type == 'guard.rejected' for e in events)
        assert not any(e.type in ('tool.result', 'approval.requested') for e in events)
        actual = tool_value(await operate(h, 'memory_read', {'memory_id': item['id']}))['memory']
        assert actual['content'] == item['content']
        evidence(data, 'deny', {'requests': h.requests, 'events': [e.model_dump() for e in events],
                               'actual_memory': actual})


@pytest.mark.parametrize('ending', ['deny', 'timeout', 'cancel'])
async def test_pending_approval_never_sends_rejected_write(data, ending):
    async with harness(data/'ph', data/'remote', ttl=1000 if ending == 'timeout' else 10000) as h:
        content = 'forbidden-' + uuid.uuid4().hex
        tid, start = await submit(h, 'memory_create', {'content': content, 'entity_id': 'test'})
        await until(lambda: h.spine.approval.pending_count() == 1)
        assert h.requests == []
        result, events = await complete(h, tid, start, ending)
        assert h.requests == []
        assert not any(e.type in ('tool.result', 'receipt.emitted') for e in events)
        assert h.spine.approval.pending_count() == 0
        if ending == 'cancel':
            assert not result.ok and result.code == 'cancelled'
        actual = tool_value(await operate(h, 'memory_search', {'query': content}))
        assert actual['results'] == []
        assert not (await h.app.service.governance_audit(h.sid, reconcile=True))['findings']
        evidence(data, ending, {'events': [e.model_dump() for e in events],
                                'actual_query': actual, 'requests': h.requests})


@pytest.mark.parametrize('tool,args,expected,sent', [
    ('memory_create', {'content': '', 'entity_id': 'test'}, 'TLB-803', False),
    ('not_a_tool', {}, 'TLB-802', False),
    ('memory_read', {'memory_id': 'nonexistent-synthetic-id'}, 'TLB-805', True),
])
async def test_local_and_remote_errors_are_not_success(data, tool, args, expected, sent):
    async with harness(data/'ph', data/'remote') as h:
        events = await operate(h, tool, args)
        errors = [e.payload for e in events if e.type == 'tool.error']
        assert len(errors) == 1 and errors[0]['code'] == expected
        assert bool(h.requests) == sent
        assert not any(e.type == 'tool.result' and e.payload['ok'] for e in events)
        if sent:
            assert h.requests[-1]['result']['isError'] is True
        evidence(data, expected, {'events': [e.model_dump() for e in events], 'requests': h.requests})


async def test_protocol_errors_and_server_validation_remain_distinct(data):
    client = client_for(data/'remote')
    try:
        await client.connect()
        for tool, args in [('not_a_tool', {}), ('memory_create', {'content': 7, 'entity_id': 'test'})]:
            value = await client.call_tool(tool, args)
            assert value['isError'] is True
        with pytest.raises(PyHError) as exc:
            await client.transport.request('not/a/method', {})
        assert '-32602' in str(exc.value.ctx)
        empty = await client.call_tool('memory_search', {'query': 'nevermatchesxyz'})
        assert not empty.get('isError')
        evidence(data, 'errors', {'protocol_error': exc.value.ctx, 'empty': empty})
    finally:
        await client.close()


@pytest.mark.parametrize('failure', ['missing', 'bad_start', 'disconnect'])
async def test_service_failures_do_not_silently_restart(data, failure):
    if failure == 'missing':
        command = [str(data/'missing-python.exe')]
    elif failure == 'bad_start':
        command = [str(R1_PYTHON), '-I', '-m', 'evleven', '--invalid-option']
    else:
        command = server_command(data/'remote')
    client = McpClient('evleven', StdioTransport(command, timeout_s=5))
    try:
        if failure != 'disconnect':
            with pytest.raises((PyHError, OSError)):
                await client.connect()
        else:
            await client.connect()
            proc = client.transport._proc
            # EOF only to the child owned by this test, never arbitrary process killing.
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), 5)
            with pytest.raises(PyHError):
                await client.call_tool('memory_search', {'query': 'unavailable'})
            assert client.transport._proc is None or client.transport._proc is proc
        evidence(data, failure, {'failed_as_expected': True})
    finally:
        await client.close()


async def test_cancelled_inflight_request_retires_connection(data):
    client = client_for(data/'remote')
    lock = None
    try:
        await client.connect()
        proc = client.transport._proc
        lock = sqlite3.connect(data/'remote/memory.db')
        lock.execute('BEGIN IMMEDIATE')
        seq = client.transport._seq
        work = asyncio.create_task(client.call_tool('memory_create',
            {'content': 'uncertain-' + data.name, 'entity_id': 'test'}))
        await until(lambda: client.transport._seq > seq)
        work.cancel()
        lock.rollback()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert proc.returncode is not None, 'Cancelled transport left a live stream with a late response'
        with pytest.raises(PyHError):
            await client.call_tool('memory_search', {'query': data.name})
    finally:
        if lock:
            lock.close()
        await client.close()
    # Fresh initialization and query; do not automatically repeat the write.
    check = client_for(data/'remote')
    try:
        await check.connect()
        actual = await check.call_tool('memory_search', {'query': data.name})
        assert not actual.get('isError')
        evidence(data, 'cancel-unknown', {'query_after_new_connection': actual, 'write_retried': False})
    finally:
        await check.close()


def test_real_http_fresh_process_cross_session_memory(data):
    result = subprocess.run([sys.executable, str(ROOT/'scripts/evleven_integration.py'),
        '--data-dir', str(data/'trial')], cwd=ROOT, capture_output=True, text=True,
        encoding='utf-8', timeout=120)
    (data/'trial-console.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    run = next((data/'trial').glob('run-*'))
    a = json.loads((run/'write-evidence.json').read_text(encoding='utf-8'))
    b = json.loads((run/'read-evidence.json').read_text(encoding='utf-8'))
    assert a['sid'] != b['sid'] and a['pyharness_pid'] != b['pyharness_pid']
    assert a['actual_memory']['content'] == b['actual_memory']['content']
    assert a['mcp_exit_code'] == b['mcp_exit_code'] == 0
    assert b['new_session_no_inherited_history'] and a['denied_before_send']
    evidence(data, 'restart', {'write': a, 'read': b})


@pytest.mark.parametrize('ending', ['timeout', 'cancel'])
async def test_lost_write_ack_is_unknown_and_reconciled_without_retry(data, ending):
    content = 'committed-after-wait-' + data.name
    lock = timer = None
    async with harness(data/'ph', data/'remote') as h:
        try:
            lock = sqlite3.connect(data/'remote/memory.db')
            lock.execute('BEGIN IMMEDIATE')
            tid, start = await submit(h, 'memory_create', {'content': content, 'entity_id': 'test'})
            await until(lambda: h.spine.approval.pending_count() == 1)
            aid = next(iter(h.spine.approval._pending))
            before = h.mcp.transport._seq
            h.mcp.transport._timeout = .2 if ending == 'timeout' else 10
            response = await h.http.post(f'/api/approvals/{h.sid}/{aid}', json={'decision': 'approve'})
            response.raise_for_status()
            await until(lambda: h.mcp.transport._seq > before)
            # Real SQLite lock delays commit. Release after the client stops waiting;
            # graceful EOF does not pretend to roll back an already accepted call.
            timer = asyncio.get_running_loop().call_later(.4, lock.rollback)
            if ending == 'cancel':
                assert await h.spine.task_queue.cancel(tid)
            result = await h.spine.task_queue.wait_for(tid, timeout=10)
            await until(lambda: h.spine.task_queue.status().running is None)
            await h.spine.persistence.flush()
            events = list(h.log.events_after(start))
            if ending == 'timeout':
                terminal = next(e for e in events if e.type == 'tool.error')
                assert terminal.payload['code'] == 'TLB-805'
                assert '不确定' in terminal.payload['message']
                assert '不要盲目重试' in terminal.payload['message']
            else:
                assert not result.ok and result.code == 'cancelled'
                terminal = next(e for e in events if e.type == 'tool.result')
                assert not terminal.payload['ok'] and terminal.payload['truncated']
                assert '可能仍在运行' in terminal.payload['summary']
            assert h.proc.returncode == 0
            assert len(h.requests) == 1 and h.requests[0]['params']['name'] == 'memory_create'
            evidence(data, 'lost-ack-' + ending, {'requests': h.requests,
                'terminal': terminal.model_dump(), 'write_retried': False})
        finally:
            if timer:
                timer.cancel()
            if lock:
                lock.rollback()
                lock.close()
    # New governed session/connection only queries. Expect exactly the one real write.
    async with harness(data/'check', data/'remote') as check:
        value = tool_value(await operate(check, 'memory_search', {'query': data.name}))
        assert value['total'] == 1 and value['results'][0]['content'] == content
        evidence(data, 'reconciled-' + ending, value)
