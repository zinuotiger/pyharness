"""Authenticated, independently deployable Docker worker. Never executes on host.

Run: python -m pyharness.remote_sandbox --help. Tokens come from an owner-only
JSON file, never command line arguments. A token identifies one account.
"""
from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pyharness.core.sandbox_models import SandboxProfile, SandboxRecord
from pyharness.core.sandbox import SandboxManager, safe_path, read_regular
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.errors import PyHError

from pyharness.core.workspace_codec import MAX_BYTES, pack_files, unpack_files






def create_app(root: Path, accounts: dict[str, str], *, manager=None, retention=900,
               max_active=4, images=('python:3.13-slim',)):
    """manager injection is for deterministic tests only; CLI always uses Docker."""
    if not accounts or len(set(accounts.values())) != len(accounts) or any(not isinstance(token,str) or not 32<=len(token)<=4096 or not token.isascii() or any(c.isspace() or ord(c)<33 for c in token) for token in accounts.values()):
        raise ValueError('Each account needs a random token of at least 32 characters')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    manager = manager or SandboxManager(root / 'workspaces')
    tasks, jobs = {}, {}
    lock = asyncio.Lock()

    def save():
        doc = {key: {k: v for k, v in row.items() if k != 'lock'} for key, row in tasks.items()}
        TenantSettingsStore._atomic_write(root / 'tasks.json', json.dumps(doc).encode())

    def audit(owner, task, action, **data):
        # No tokens, file contents or command text in service logs.
        line = json.dumps(dict(time=time.time(), owner=owner, task=task, action=action, **data))
        if (root/'audit.jsonl').exists() and (root/'audit.jsonl').stat().st_size>5*1048576:
            os.replace(root/'audit.jsonl',root/'audit.previous.jsonl')
        with (root / 'audit.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(line + '\n'); stream.flush(); os.fsync(stream.fileno())

    async def remove(ident):
        row = tasks[ident]
        job = jobs.get(ident)
        if job and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        if row['sandbox'] in manager.records:
            await manager.destroy(row['sandbox'])
        row['status'] = 'deleted'
        row['result'] = None
        save()
        audit(row['owner'], ident, 'deleted')

    async def janitor():
        while True:
            await asyncio.sleep(2)
            async with lock:
                for ident, row in list(tasks.items()):
                    if row['status'] == 'deleted':
                        if row['expires']+86400 < time.time(): del tasks[ident];save()
                        continue
                    over = False
                    try:
                        pack_files(manager.workspace(row['sandbox']))
                    except Exception:
                        over = True
                    if row['expires'] < time.time() or over:
                        try: await remove(ident)
                        except Exception: audit(row['owner'], ident, 'cleanup_failed')

    @asynccontextmanager
    async def lifespan(app):
        # Recover only containers whose owner label matches this worker root.
        if (root / 'tasks.json').exists():
            old = json.loads((root / 'tasks.json').read_text('utf-8'))
            for ident, row in old.items():
                if row['status'] != 'deleted':
                    record = SandboxRecord.model_validate(row['record'])
                    await manager.recover(record)
                    await manager.destroy(record.sandbox_id)
                    audit(row['owner'], ident, 'restart_cleanup')
            tasks.update({k: {**v, 'status': 'deleted', 'result': None} for k, v in old.items()})
            save()
        cleaner = asyncio.create_task(janitor())
        try: yield
        finally:
            cleaner.cancel()
            await asyncio.gather(cleaner, return_exceptions=True)
            async with lock:
                for ident, row in tasks.items():
                    if row['status'] != 'deleted': await remove(ident)
            await manager.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.manager, app.state.tasks = manager, tasks

    @app.middleware('http')
    async def authenticate(request, next_handler):
        given = request.headers.get('authorization', '').removeprefix('Bearer ')
        owner = next((name for name, token in accounts.items() if secrets.compare_digest(given, token)), None)
        if owner is None: return JSONResponse({'code': 'unauthorized'}, status_code=401)
        request.state.owner = owner
        # Bound decoded request bodies, including chunked requests.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 15 * 1048576:
                return JSONResponse({'code': 'request_too_large'}, status_code=413)
        request._body = bytes(raw)
        try: return await next_handler(request)
        except (PyHError, ValueError, KeyError):
            return JSONResponse({'code': 'remote_operation_failed'}, status_code=422)

    def owned(request, ident):
        row = tasks.get(ident)
        if not row or row['owner'] != request.state.owner or row['status'] == 'deleted':
            raise HTTPException(404, 'task_not_found')
        if row['expires'] < time.time(): raise HTTPException(410, 'task_expired')
        return row

    @app.get('/v1/health')
    async def health():
        engine = await manager.backends['isolated'].health()
        image_ready = {}
        for image in images:
            check = await manager.backends['isolated']._command(['image', 'inspect', image], timeout=5)
            image_ready[image] = check['exit_code'] == 0
        return {'protocol': 1, 'engine': engine, 'images': image_ready,
                'ready': engine['status'] == 'available' and all(image_ready.values())}

    @app.post('/v1/tasks/{ident}')
    async def create(request: Request, ident: str):
        if not __import__('re').fullmatch(r'[a-f0-9]{32}', ident): raise HTTPException(422)
        body = await request.json()
        profile = SandboxProfile.model_validate({**body['profile'], 'mode': 'isolated', 'remote_connection': None})
        if profile.image not in images: raise HTTPException(422, 'image_not_allowed')
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        async with lock:
            if ident in tasks:
                row = owned(request, ident)
                if row['fingerprint'] != fingerprint: raise HTTPException(409, 'idempotency_conflict')
                return {'task_id': ident, 'status': row['status']}
            if len(tasks)>=4096: raise HTTPException(429,'worker_metadata_quota')
            if sum(r['owner'] == request.state.owner and r['status'] != 'deleted' for r in tasks.values()) >= max_active:
                raise HTTPException(429, 'account_quota')
            record = await manager.create(ident, profile.model_copy(update={'workspace_mode': 'empty'}))
            try:
                async with manager.snapshot(record.sandbox_id) as workspace:
                    unpack_files(workspace, body.get('files', {}))
            except BaseException:
                await manager.destroy(record.sandbox_id)
                raise
            tasks[ident] = dict(owner=request.state.owner, sandbox=record.sandbox_id,
                record=record.model_dump(), fingerprint=fingerprint, status='ready', result=None,
                operation=None, expires=time.time() + min(max(retention, 30), 3600))
            save(); audit(request.state.owner, ident, 'created')
            return {'task_id': ident, 'status': 'ready'}

    @app.post('/v1/tasks/{ident}/execute')
    async def execute(request: Request, ident: str):
        body = await request.json()
        command, operation = body.get('command'), body.get('operation')
        if not isinstance(command, str) or len(command) > 32000 or not isinstance(operation, str) or len(operation) > 64:
            raise HTTPException(422)
        async with lock:
            row = owned(request, ident)
            digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            if row['operation'] == operation:
                if row.get('operation_digest') != digest: raise HTTPException(409, 'idempotency_conflict')
                return {'status': row['status']}
            if row['status'] not in ('ready', 'completed'): raise HTTPException(409, 'task_busy')
            row.update(status='running', operation=operation, operation_digest=digest, result=None)
            save(); audit(row['owner'], ident, 'executing', operation=operation, digest=digest)
            async def run():
                try:
                    result = await manager.execute(row['sandbox'], command, python=body.get('python') is True)
                    row.update(status='completed', result=result)
                except asyncio.CancelledError:
                    row.update(status='cancelled', result={'error_code': 'cancelled'})
                    raise
                except Exception as exc:
                    row.update(status='failed', result={'error_code': 'timeout' if isinstance(exc, asyncio.TimeoutError) else 'execution_failed'})
                finally:
                    save(); audit(row['owner'], ident, row['status'], operation=operation)
            jobs[ident] = asyncio.create_task(run())
            return {'status': 'running'}

    @app.get('/v1/tasks/{ident}')
    async def status(request: Request, ident: str):
        row = owned(request, ident)
        return {key: row[key] for key in ('status', 'result', 'operation', 'expires')}

    @app.post('/v1/tasks/{ident}/files')
    async def files(request: Request, ident: str):
        body = await request.json()
        async with lock:
            row = owned(request, ident)
            if row['status'] not in ('ready', 'completed'): raise HTTPException(409, 'task_busy')
            async with manager.snapshot(row['sandbox']) as workspace:
                if 'files' in body: unpack_files(workspace, body['files'])
                return {'files': pack_files(workspace)}

    @app.post('/v1/tasks/{ident}/file-operation')
    async def file_operation(request: Request, ident: str):
        body = await request.json()
        async with lock:
            row = owned(request, ident)
            if row['status'] not in ('ready', 'completed'): raise HTTPException(409, 'task_busy')
            if body.get('name') not in ('fs.read_file', 'fs.list_dir', 'fs.write_file', 'fs.patch'): raise HTTPException(422)
            value = await manager.file_operation(row['sandbox'], body['name'], body['args'])
            audit(row['owner'], ident, 'file_operation', tool=body['name'])
            return {'value': value}

    @app.delete('/v1/tasks/{ident}')
    async def cancel(request: Request, ident: str):
        async with lock:
            row = tasks.get(ident)
            if row and row['owner'] == request.state.owner and row['status'] == 'deleted': return {'ok': True}
            owned(request, ident)
            await remove(ident)
            return {'ok': True}
    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--accounts-file', type=Path, required=True, help='JSON account ID -> random token (32+ characters)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=60500)
    parser.add_argument('--ssl-certfile')
    parser.add_argument('--ssl-keyfile')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', '::1', 'localhost') and not (args.ssl_certfile and args.ssl_keyfile):
        parser.error('Non-loopback serving requires a TLS certificate and key')
    app = create_app(args.data_dir, json.loads(args.accounts_file.read_text('utf-8-sig')))
    uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile, access_log=False)


if __name__ == '__main__':
    main()
