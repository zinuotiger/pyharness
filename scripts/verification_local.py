"""Local trial of the existing API; model is explicitly deterministic/offline.

Run: python scripts/verification_local.py --smoke
Or:  python scripts/verification_local.py --serve
All generated data stays in .work/verification/trial unless --data-dir is given.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from pyharness import cli
from pyharness.config import Settings
from pyharness.core import llm
from pyharness.desktop import DesktopApp


class DeterministicModel:
    """No network, no semantic model: a labelled response to the latest input."""

    async def chat(self, messages, tools=None, *, ctx):
        text = next((m['content'] for m in reversed(messages)
                     if m.get('role') == 'user'), '')
        content = '[DETERMINISTIC TEST MODEL] received: ' + str(text)
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1,
            prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1)
        await llm.report_usage(usage, 'verification-offline', ctx=ctx,
                               counters=getattr(ctx, 'counters', None))
        event = await ctx.session.append('llm.response', {
            'model': 'verification-offline', 'finish_reason': 'stop',
            'content': content, 'tool_calls': []}, actor='llm')
        result = llm.LLMResponse(content=content, tool_calls=None, usage=usage,
                                 model='verification-offline', finish_reason='stop')
        result.seq = event.seq
        return result

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self):
        return 0.0


async def run_api(data: Path, phase: str):
    data.mkdir(parents=True, exist_ok=True)
    cfg = Settings()
    for name, rel in {'root': '.', 'sessions_dir': 'sessions',
                      'workspaces_dir': 'workspaces', 'spill_dir': 'spill',
                      'db_path': 'index.db'}.items():
        setattr(cfg.storage, name, str(data / rel))
    cfg.llm.model = 'verification-offline'
    cfg.llm.fallback_models = []
    cfg.plugins.dir = str(data / 'plugins')
    cfg.skills.dir = str(data / 'skills')
    cfg.security.credentials.file = str(data / 'credentials.yaml')
    cfg.plugins.mcp_servers = []
    llm.adapters.clear()
    llm.adapters[cfg.llm.model] = DeterministicModel()
    app = DesktopApp(cli.assemble_ctx(cfg))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app.api, log_level='warning'))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
                raise RuntimeError('server stopped before startup')
            await asyncio.sleep(0.025)
        assert server.started, 'API startup timeout'
        url = f'http://127.0.0.1:{port}'
        print(json.dumps({'phase': phase, 'url': url,
                          'model': 'DETERMINISTIC TEST MODEL'}, ensure_ascii=False), flush=True)
        if phase == 'serve':
            print('API token (local test only): ' + app._api_token, flush=True)
            print('Ctrl+C stops the API and closes session resources.', flush=True)
            await task
            return
        async with httpx.AsyncClient(base_url=url, trust_env=False,
                headers={'X-PyHarness-Token': app._api_token}) as client:
            root = await client.get('/')
            assert root.status_code == 200 and 'PyHarness' in root.text
            state_file = data / 'smoke-state.json'
            if phase == 'write':
                created = await client.post('/api/sessions')
                created.raise_for_status()
                sid = created.json()['sid']
                submitted = await client.post(f'/api/sessions/{sid}/messages',
                    json={'text': '独立本地试用 synthetic hello'})
                submitted.raise_for_status()
                tid = submitted.json()['task_id']
                for _ in range(200):
                    timeline = await client.get(f'/api/sessions/{sid}/timeline')
                    timeline.raise_for_status()
                    log = await app.service.require_session(sid)
                    if any(e.type == 'task.completed' and e.payload['task_id'] == tid
                           for e in log.events_after(0)):
                        break
                    await asyncio.sleep(0.025)
                else:
                    raise AssertionError('task did not complete')
                messages = (await client.get(f'/api/sessions/{sid}/messages')).json()
                expected = '[DETERMINISTIC TEST MODEL] received: 独立本地试用 synthetic hello'
                assert any(m['content'] == expected for m in messages['messages'])
                state_file.write_text(json.dumps({'sid': sid, 'messages': messages},
                    ensure_ascii=False), encoding='utf-8')
                print(json.dumps({'created': sid, 'submitted': tid,
                    'result': expected, 'http': '200', 'phase': 'write'}, ensure_ascii=False))
            else:
                saved = json.loads(state_file.read_text(encoding='utf-8'))
                sid = saved['sid']
                response = await client.get(f'/api/sessions/{sid}/messages')
                response.raise_for_status()
                assert response.json()['messages'] == saved['messages']['messages']
                print(json.dumps({'recovered': sid, 'messages_equal': True,
                                   'phase': 'read', 'http': response.status_code}))
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        await app._shutdown_async()
        sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path,
                        default=ROOT / '.work/verification/trial')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--phase', choices=['write', 'read', 'serve'], default='serve')
    args = parser.parse_args()
    if args.smoke:
        for phase in ['write', 'read']:
            subprocess.run([sys.executable, str(Path(__file__).resolve()),
                '--phase', phase, '--data-dir', str(args.data_dir.resolve())],
                cwd=ROOT, check=True, timeout=45)
        print('PASS: existing HTTP API + normal shutdown + independent process recovery')
    else:
        asyncio.run(run_api(args.data_dir.resolve(), args.phase))


if __name__ == '__main__':
    main()
