"""Real PyHarness HTTP -> governed MCP -> pinned evleven R1 local trial.

The model is a deterministic command planner. It never supplies memory results.
Run with .work/verification/venv/Scripts/python.exe scripts/evleven_integration.py
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / '.work/integration-evleven'
R1_PYTHON = BASE / 'evleven-venv/Scripts/python.exe'
TOOLS = ['memory_create', 'memory_read', 'memory_search', 'memory_delete']

import httpx
import uvicorn
from pyharness import cli
from pyharness.config import McpServerCfg, Settings
from pyharness.core import llm
from pyharness.desktop import DesktopApp


def server_command(data):
    return [str(R1_PYTHON), '-I', '-B', str(ROOT / 'scripts/evleven_r1_server.py'),
            '--data-dir', str(Path(data).resolve())]


def settings(data, remote):
    cfg = Settings()
    for name, rel in {'root': '.', 'sessions_dir': 'sessions',
                      'workspaces_dir': 'workspaces', 'spill_dir': 'spill',
                      'db_path': 'index.db'}.items():
        setattr(cfg.storage, name, str(data / rel))
    cfg.plugins.dir = str(data / 'plugins')
    cfg.skills.dir = str(data / 'skills')
    cfg.security.credentials.file = str(data / 'credentials.yaml')
    cfg.security.policy.preset = 'standard'
    cfg.security.policy.deny_tools_extra = ['mcp.evleven.memory_delete']
    cfg.llm.model = 'integration-deterministic'
    cfg.llm.fallback_models = []
    cfg.plugins.mcp_servers = [McpServerCfg(name='evleven', command=server_command(remote),
        timeout_s=10, allowed_tools=TOOLS)]
    return cfg


class CommandModel:
    """Only converts an explicit synthetic command to a call; replies quote real tools."""
    def __init__(self):
        self.seen = set()

    async def chat(self, messages, tools=None, *, ctx):
        user = next((m.get('content', '') for m in reversed(messages)
                     if m.get('role') == 'user'), '')
        user_seq = max((e.seq for e in ctx.session.events_after(0)
                        if e.type == 'user.message'), default=0)
        key = (ctx.session.sid, user_seq, str(user))
        calls = []
        content = '[DETERMINISTIC PLANNER; REAL TOOL RESULT]\n'
        try:
            command = json.loads(user)
        except (ValueError, TypeError):
            command = {}
        if key not in self.seen and isinstance(command, dict) and 'tool' in command:
            self.seen.add(key)
            calls = [llm.ToolCall(id='r1-' + uuid.uuid4().hex, index=0,
                name=command['tool'], raw_args=command['args'],
                raw_json=json.dumps(command['args'], ensure_ascii=False))]
            content = ''
        else:
            content += str(next((m.get('content', '') for m in reversed(messages)
                                 if m.get('role') == 'tool'), 'No tool result.'))
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1)
        await llm.report_usage(usage, 'integration-deterministic', ctx=ctx,
                               counters=getattr(ctx, 'counters', None))
        response = llm.LLMResponse(content=content, tool_calls=calls or None, usage=usage,
            model='integration-deterministic', finish_reason='tool_calls' if calls else 'stop')
        event = await ctx.session.append('llm.response', {
            'model': response.model, 'finish_reason': response.finish_reason, 'content': content,
            'tool_calls': [{'id': c.id, 'name': c.name, 'arguments': c.raw_json} for c in calls]}, actor='llm')
        response.seq = event.seq
        return response

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self):
        return 0.0


async def until(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


@asynccontextmanager
async def harness(data, remote, *, tcp=False, ttl=10000):
    cfg = settings(Path(data), Path(remote))
    cfg.security.approval.ttl_ms = ttl
    Path(data).mkdir(parents=True, exist_ok=True)
    (Path(data) / 'integration-config.json').write_text(
        json.dumps(cfg.model_dump(mode='json'), ensure_ascii=False, indent=2), encoding='utf-8')
    previous = dict(llm.adapters)
    llm.adapters.clear()
    llm.adapters[cfg.llm.model] = CommandModel()
    app = DesktopApp(cli.assemble_ctx(cfg))
    task = server = sock = None
    processes = []
    try:
        if tcp:
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            server = uvicorn.Server(uvicorn.Config(app.api, log_level='warning'))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            await until(lambda: server.started or task.done())
            if not server.started:
                await task
                raise RuntimeError('HTTP startup failed')
            client_args = {'base_url': f'http://127.0.0.1:{sock.getsockname()[1]}'}
        else:
            client_args = {'base_url': 'http://127.0.0.1',
                           'transport': httpx.ASGITransport(app=app.api)}
        async with httpx.AsyncClient(**client_args, trust_env=False, timeout=20,
                headers={'X-PyHarness-Token': app._api_token}) as http:
            created = await http.post('/api/sessions')
            created.raise_for_status()
            sid = created.json()['sid']
            log, spine = await app.service.public_spine_for(sid)
            if not spine._mcp_clients:
                raise RuntimeError('evleven R1 MCP startup/registration failed; see process log')
            mcp = spine._mcp_clients[0]
            proc = mcp.transport._proc
            processes.append(proc)
            registered = sorted(d.name for d in spine.tool_registry.iter_definitions()
                                if d.name.startswith('mcp.evleven.'))
            assert registered == sorted('mcp.evleven.' + n for n in TOOLS), registered
            requests = []
            original = mcp.transport.request

            async def observed(method, params):
                record = {'method': method, 'params': params,
                          'request_id': mcp.transport._seq + 1, 'sid': sid}
                calls = [e for e in log.events_after(0) if e.type == 'tool.call'
                         and e.payload.get('name') == 'mcp.evleven.' + str(params.get('name'))]
                if calls:
                    record['call_id'] = calls[-1].payload['call_id']
                    running = spine.task_queue.status().running
                    record['task_id'] = getattr(running, 'id', running)
                requests.append(record)
                try:
                    result = await original(method, params)
                    record['result'] = result
                    return result
                except BaseException as exc:
                    record['error'] = type(exc).__name__
                    record['outcome'] = 'unknown; query before retrying writes'
                    raise

            mcp.transport.request = observed
            yield SimpleNamespace(app=app, http=http, sid=sid, log=log, spine=spine,
                mcp=mcp, requests=requests, proc=proc, registered=registered,
                url=client_args['base_url'])
    finally:
        if server:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
        await app._shutdown_async()
        if sock:
            sock.close()
        llm.adapters.clear()
        llm.adapters.update(previous)
        for proc in processes:
            assert proc.returncode is not None, 'Owned MCP process leaked'


async def submit(h, tool, args):
    before = max((e.seq for e in h.log.events_after(0)), default=0)
    response = await h.http.post(f'/api/sessions/{h.sid}/messages',
        json={'text': json.dumps({'tool': 'mcp.evleven.' + tool, 'args': args}, ensure_ascii=False)})
    response.raise_for_status()
    return response.json()['task_id'], before


async def complete(h, tid, before, verdict='approve'):
    queue = h.spine.task_queue
    await until(lambda: h.spine.approval.pending_count() or tid in queue._done)
    if h.spine.approval.pending_count():
        aid = next(iter(h.spine.approval._pending))
        if verdict in ('approve', 'deny'):
            response = await h.http.post(f'/api/approvals/{h.sid}/{aid}', json={'decision': verdict})
            response.raise_for_status()
        elif verdict == 'cancel':
            assert await queue.cancel(tid)
        elif verdict != 'timeout':
            raise ValueError(verdict)
    result = await queue.wait_for(tid, timeout=15)
    await until(lambda: queue.status().running is None and not queue.status().paused)
    await h.spine.persistence.flush()
    events = list(h.log.events_after(before))
    return result, events


async def operate(h, tool, args, verdict='approve'):
    tid, before = await submit(h, tool, args)
    result, events = await complete(h, tid, before, verdict)
    return events


def tool_value(events):
    values = [e.payload for e in events if e.type == 'tool.result' and e.payload.get('ok')]
    if len(values) != 1:
        raise RuntimeError('Expected one successful real MCP result: ' +
                           str([(e.type, e.payload) for e in events if e.type.startswith('tool.')]))
    return json.loads(values[0]['summary'])


async def phase(data, action):
    state_path = data / 'memory-reference.json'
    evidence = {'phase': action, 'pyharness_pid': os.getpid()}
    async with harness(data / ('pyharness-' + action), data / 'remote', tcp=True) as h:
        evidence.update(sid=h.sid, url=h.url, mcp_pid=h.proc.pid,
                        initialization=h.mcp.initialization, exposed=h.registered)
        if action == 'write':
            content = '合成跨会话记忆 R1-' + uuid.uuid4().hex + '；蓝色纸鹤在星期三测试。'
            created = tool_value(await operate(h, 'memory_create',
                {'content': content, 'entity_id': 'synthetic-integration'}))
            memory_id = created['memory']['id']
            read = tool_value(await operate(h, 'memory_read', {'memory_id': memory_id}))
            assert read['memory']['content'] == content
            sent = len(h.requests)
            denied = await operate(h, 'memory_delete', {'memory_id': memory_id})
            assert any(e.type == 'guard.rejected' for e in denied)
            assert len(h.requests) == sent, 'Denied delete reached MCP'
            print(json.dumps({'operation': 'memory_delete', 'session': h.sid,
                'result': 'DENIED by local policy before MCP send',
                'mcp_calls_sent': 0}, ensure_ascii=False), flush=True)
            read = tool_value(await operate(h, 'memory_read', {'memory_id': memory_id}))
            assert read['memory']['content'] == content
            reference = {'id': memory_id, 'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
                         'session_a': h.sid}
            state_path.write_text(json.dumps(reference), encoding='utf-8')
            evidence.update(reference=reference, actual_memory=read['memory'], denied_before_send=True)
        else:
            reference = json.loads(state_path.read_text())
            # Fresh local data and session. Only the remote ID is supplied to the model.
            assert not any(e.type == 'user.message' for e in h.log.events_after(0))
            assert h.sid != reference['session_a']
            read = tool_value(await operate(h, 'memory_read', {'memory_id': reference['id']}))
            assert hashlib.sha256(read['memory']['content'].encode()).hexdigest() == reference['content_sha256']
            evidence.update(actual_memory=read['memory'], new_session_no_inherited_history=True)
        audit = await h.app.service.governance_audit(h.sid, reconcile=True)
        assert not audit['findings'], audit
        messages = await h.http.get(f'/api/sessions/{h.sid}/messages')
        messages.raise_for_status()
        evidence.update(requests=h.requests, audit=audit, messages=messages.json())
        print(json.dumps({'phase': action, 'session': h.sid, 'memory': evidence['actual_memory'],
                          'model': 'DETERMINISTIC PLANNER; REAL MCP + STORAGE'}, ensure_ascii=False), flush=True)
    evidence['mcp_exit_code'] = h.proc.returncode
    (data / f'{action}-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    if h.proc.returncode != 0:
        raise RuntimeError(f'R1 did not shut down normally: {h.proc.returncode}')


async def bounded_phase(data, action):
    # Timeout unwinds the harness finally blocks before the parent subprocess limit.
    async with asyncio.timeout(90):
        await phase(data, action)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=BASE / 'trial')
    parser.add_argument('--phase', choices=['write', 'read'])
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError('Do not use -O')
    if Path(sys.prefix).resolve() != (ROOT / '.work/verification/venv').resolve():
        raise RuntimeError('Use .work/verification/venv/Scripts/python.exe')
    data = args.data_dir.resolve()
    if not data.is_relative_to(BASE.resolve()):
        raise RuntimeError('Trial data must stay in .work/integration-evleven')
    data.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        if key.startswith('PH_') or any(word in key.upper() for word in ('API_KEY', 'TOKEN', 'SECRET', 'PROXY')):
            os.environ.pop(key, None)
    for key, relative in {'HOME': 'home', 'USERPROFILE': 'home', 'APPDATA': 'home/appdata',
                          'LOCALAPPDATA': 'home/local', 'TEMP': 'tmp', 'TMP': 'tmp',
                          'XDG_CACHE_HOME': 'cache'}.items():
        folder = data / relative
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(folder)
    os.environ.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    if args.phase:
        logging.basicConfig(filename=data / f'{args.phase}-runtime.log', level=logging.DEBUG,
                            encoding='utf-8', format='%(asctime)s %(levelname)s %(name)s %(message)s')
        asyncio.run(bounded_phase(data, args.phase))
        return
    # New evidence per invocation, persistent remote database, never silently clear data.
    run = data / ('run-' + uuid.uuid4().hex)
    run.mkdir()
    print(f'Data/logs: {run}\nRemote persistence: {run / "remote"}', flush=True)
    subprocess.run(server_command(run/'remote') + ['--verify'], check=True, timeout=20)
    for action in ['write', 'read']:
        cmd = [sys.executable, str(Path(__file__).resolve()), '--phase', action, '--data-dir', str(run)]
        with (run / f'{action}-console.log').open('w', encoding='utf-8') as output:
            completed = subprocess.run(cmd, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, timeout=120)
        print((run / f'{action}-console.log').read_text(encoding='utf-8'), flush=True)
        if completed.returncode:
            raise RuntimeError(f'{action} failed ({completed.returncode}); see {run}')
    print(f'PASS: real MCP memory across fresh processes/sessions; evidence: {run}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('Integration failed')
        print(f'Integration failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
