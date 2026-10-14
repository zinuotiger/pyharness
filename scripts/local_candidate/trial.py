"""Interactive terminal wrapper over the existing public API and governed runtime."""
import asyncio
import json
import logging
import msvcrt
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parent


def isolate(data):
    for key in list(os.environ):
        if key.startswith('PH_') or any(w in key.upper() for w in ('API_KEY', 'TOKEN', 'SECRET', 'PROXY')):
            os.environ.pop(key, None)
    for key, rel in {'HOME':'home', 'USERPROFILE':'home', 'APPDATA':'home/appdata',
                     'LOCALAPPDATA':'home/local', 'TEMP':'tmp', 'TMP':'tmp',
                     'XDG_CACHE_HOME':'cache'}.items():
        path = data / rel
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    os.chdir(data)


async def prompt(text):
    try:
        return await asyncio.to_thread(input, text)
    except EOFError:
        return 'quit'


async def session(api, data, evidence):
    async with api.harness(data/'pyharness', data/'remote', tcp=True, ttl=120000) as h:
        print(f'READY session={h.sid} HTTP={h.url} MCP=evleven R1 / stdio', flush=True)
        print('确定性命令规划替身；真实运行时、审批、MCP、存储。没有调用真实模型。\n'
              '命令：save 内容 | read ID/last | search 关键词 | deny ID/last | audit | history | new | quit\n'
              '仅输入合成记忆。结果来源为 evleven memory.id；超时/取消不代表远端副作用撤销。', flush=True)
        reference = data/'last-memory-id.txt'
        while True:
            line = (await prompt('memory> ')).strip()
            cmd, _, value = line.partition(' ')
            if cmd in ('quit', 'new'):
                break
            if cmd == 'audit':
                print(json.dumps(await h.app.service.governance_audit(h.sid, reconcile=True), ensure_ascii=False), flush=True)
                continue
            if cmd == 'history':
                response = await h.http.get(f'/api/sessions/{h.sid}/messages')
                response.raise_for_status()
                print(json.dumps(response.json(), ensure_ascii=False), flush=True)
                continue
            names = {'save':'memory_create', 'read':'memory_read', 'search':'memory_search', 'deny':'memory_delete'}
            if cmd not in names or not value.strip():
                print('请输入有效命令和内容。', flush=True)
                continue
            if cmd in ('read', 'deny') and value == 'last':
                if not reference.exists():
                    print('尚无 last ID，请提供 evleven memory.id。', flush=True)
                    continue
                value = reference.read_text().strip()
            args = ({'content':value, 'entity_id':'synthetic-local-trial'} if cmd == 'save' else
                    {'query':value} if cmd == 'search' else {'memory_id':value})
            sent = len(h.requests)
            tid, before = await api.submit(h, names[cmd], args)
            await api.until(lambda: h.spine.approval.pending_count() or tid in h.spine.task_queue._done)
            verdict = 'deny'
            if h.spine.approval.pending_count():
                print(f'APPROVAL session={h.sid} task={tid} tool={names[cmd]} MCP sent before approval={len(h.requests)-sent}', flush=True)
                answer = await prompt('批准执行? 输入 yes 批准，其余拒绝 > ')
                verdict = 'approve' if answer.strip().lower() == 'yes' else 'deny'
            result, events = await api.complete(h, tid, before, verdict)
            records = [e.model_dump(mode='json') for e in events]
            audit = await h.app.service.governance_audit(h.sid, reconcile=True)
            record = {'sid':h.sid, 'task_id':tid, 'command':cmd, 'events':records,
                      'requests':h.requests[sent:], 'audit':audit}
            (evidence/f'{tid}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            if any(e.type == 'guard.rejected' for e in events):
                print(f'DENIED before MCP send: {len(h.requests)-sent} calls; task={tid}', flush=True)
            else:
                successes = [e for e in events if e.type == 'tool.result' and e.payload.get('ok')]
                if successes:
                    actual = api.tool_value(events)
                    print('SOURCE evleven R1 MCP: ' + json.dumps(actual, ensure_ascii=False), flush=True)
                    if cmd == 'save' and actual.get('success') and actual.get('memory', {}).get('id'):
                        reference.write_text(actual['memory']['id'], encoding='utf-8')
                else:
                    print('NOT EXECUTED / ERROR: ' + json.dumps(records, ensure_ascii=False), flush=True)
            print(f'记录: {evidence / (str(tid)+".json")}', flush=True)
        snapshot = {'sid':h.sid, 'url':h.url, 'mcp_pid':h.proc.pid, 'requests':h.requests}
    snapshot['mcp_exit_code'] = h.proc.returncode
    (evidence/f'session-{h.sid}.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'STOPPED session={h.sid} owned MCP exit={h.proc.returncode}', flush=True)
    return cmd == 'new'


def main():
    runpy.run_path(str(ROOT/'integrity.py'))['verify']()
    data = ROOT/'runtime/user'
    data.mkdir(parents=True, exist_ok=True)
    isolate(data)
    with (data/'trial.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('This user data is already open by another trial process') from exc
        evidence = data/'logs'/uuid.uuid4().hex
        evidence.mkdir(parents=True)
        logging.basicConfig(filename=evidence/'runtime.log', level=logging.DEBUG, encoding='utf-8')
        api = SimpleNamespace(**runpy.run_path(str(ROOT/'evleven_integration.py')))
        print(f'Data: {data}\nLogs: {evidence}', flush=True)
        async def run():
            while await session(api, data, evidence):
                pass
        asyncio.run(run())


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'Trial failed: {type(exc).__name__}: {exc}; 远端结果可能不确定，写入前请先查询核对。', file=sys.stderr)
        sys.exit(1)
