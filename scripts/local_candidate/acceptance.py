"""Installed-artifact fault checks; all normal services use the fixed R1 wheel."""
import asyncio
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parent


async def checks():
    runpy.run_path(str(ROOT/'integrity.py'))['verify']()
    data = ROOT/'runtime/fault-checks'/uuid.uuid4().hex
    data.mkdir(parents=True)
    runpy.run_path(str(ROOT/'trial.py'))['isolate'](data)
    api = SimpleNamespace(**runpy.run_path(str(ROOT/'evleven_integration.py')))
    results = []
    for ending in ('deny', 'cancel', 'timeout'):
        async with api.harness(data/ending/'ph', data/ending/'remote', tcp=True,
                               ttl=500 if ending == 'timeout' else 10000) as h:
            phrase = 'rejected-candidate-' + uuid.uuid4().hex
            tid, before = await api.submit(h, 'memory_create', {'content':phrase, 'entity_id':'test'})
            await api.until(lambda:h.spine.approval.pending_count() == 1)
            assert h.requests == [], 'MCP sent before approval'
            _, events = await api.complete(h, tid, before, ending)
            assert h.requests == [], 'Rejected operation reached MCP'
            empty = api.tool_value(await api.operate(h, 'memory_search', {'query':phrase}))
            assert empty['results'] == [], empty
            results.append({'case':ending, 'sid':h.sid, 'pid':h.proc.pid,
                            'events':[e.model_dump(mode='json') for e in events], 'server_query':empty})
        assert h.proc.returncode == 0
        results[-1]['exit_code'] = h.proc.returncode
    original = api.server_command.__globals__['R1_PYTHON']
    api.server_command.__globals__['R1_PYTHON'] = data/'missing-python.exe'
    try:
        try:
            async with api.harness(data/'startup/ph', data/'startup/remote', tcp=True):
                raise AssertionError('Missing service was accepted')
        except RuntimeError as exc:
            assert 'startup/registration failed' in str(exc), str(exc)
            results.append({'case':'missing-service', 'error':str(exc)})
    finally:
        api.server_command.__globals__['R1_PYTHON'] = original
    (data/'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS: 4 installed fault cases; {data}')


if __name__ == '__main__':
    asyncio.run(checks())
