import json
import asyncio
import os
import threading
from pathlib import Path

import pytest

from pyharness.core import tenant_settings as ts
from pyharness.errors import PyHError


def body(**extra):
    return {'id':'test', 'model':'synthetic', 'base_url':'http://127.0.0.1:9', **extra}


async def test_F01_unconfigured_tenant_cannot_inherit_host_model(tmp_path, monkeypatch):
    from pyharness.application.service import ApplicationService
    from pyharness.cli import assemble_ctx
    from pyharness.core import llm
    from tests.support import isolated_settings
    cfg = isolated_settings(tmp_path)
    cfg.llm.api_key = 'env:SYNTHETIC_HOST'
    monkeypatch.setenv('SYNTHETIC_HOST', 'HOST-synthetic-secret')
    reads = []
    monkeypatch.setattr(llm, 'resolve_secret_ref', lambda *a,**kw: reads.append(a) or 'HOST-synthetic-secret')
    service = ApplicationService(assemble_ctx(cfg), channel='desktop', tenant_id='other')
    try:
        sid = await service.create_session()
        with pytest.raises(PyHError): await service.queue_for(sid)
        assert reads == []
        assert service.llm_runtime.registry == {}
    finally: await service.shutdown()


@pytest.mark.parametrize('field,ref', [('api_key_ref','env:SYNTHETIC_HOST'),
                                     ('api_key_ref','file:synthetic-host'),
                                     ('api_key','env:SYNTHETIC_HOST')])
def test_F01_tenant_cannot_select_host_secret(tmp_path, field, ref):
    store = ts.TenantSettingsStore(tmp_path)
    with pytest.raises(PyHError): store.upsert_profile('other', body(**{field:ref}))
    assert store.state('other')['profiles'] == []


def test_F01_old_unsafe_profile_denied_before_secret_read(tmp_path, monkeypatch):
    store = ts.TenantSettingsStore(tmp_path)
    store._save_doc('other', {'active':'test','profiles':[body(api_key_ref='env:SYNTHETIC_HOST')]})
    reads = []
    def forbidden(*args, **kwargs):
        reads.append(True)
        return 'synthetic-secret'
    from pyharness.core import llm
    monkeypatch.setattr(llm, 'resolve_secret_ref', forbidden)
    with pytest.raises(PyHError): store.resolve_ref('tenant:other:test')
    assert not reads


def test_F13_direct_to_reference_and_clear(tmp_path, monkeypatch):
    store = ts.TenantSettingsStore(tmp_path)
    store.upsert_profile('default', body(api_key='OLD-synthetic'))
    monkeypatch.setenv('SYNTHETIC_KEY', 'NEW-synthetic')
    result = store.upsert_profile('default', body(api_key_ref='env:SYNTHETIC_KEY'))
    assert store.resolve_ref('tenant:default:test') == 'NEW-synthetic'
    assert result['profile']['api_key_source'] == 'env'
    store.upsert_profile('default', body(clear_api_key=True))
    assert not store.state('default')['profiles'][0]['has_api_key']
    with pytest.raises(PyHError): store.resolve_ref('tenant:default:test')


def test_F13_metadata_failure_keeps_old_secret(tmp_path, monkeypatch):
    store = ts.TenantSettingsStore(tmp_path)
    store.upsert_profile('other', body(api_key='OLD-synthetic'))
    def fail(*args): raise OSError('synthetic metadata failure')
    monkeypatch.setattr(store, '_save_doc', fail)
    with pytest.raises(OSError): store.upsert_profile('other', body(api_key='NEW-synthetic'))
    assert ts.TenantSettingsStore(tmp_path).resolve_ref('tenant:other:test') == 'OLD-synthetic'


def test_F13_endpoint_change_cannot_reuse_secret(tmp_path):
    store = ts.TenantSettingsStore(tmp_path)
    store.upsert_profile('other', body(api_key='OLD-synthetic'))
    with pytest.raises(PyHError):
        store.upsert_profile('other', body(base_url='http://127.0.0.1:10'))
    assert store.active_profile('other')['base_url'] == 'http://127.0.0.1:9'


def test_R02_atomic_write_uses_exclusive_private_temp(tmp_path, monkeypatch):
    import tempfile
    calls = []
    original = tempfile.mkstemp
    def tracked(*args, **kwargs):
        fd, name = original(*args, **kwargs)
        calls.append(Path(name))
        return fd, name
    monkeypatch.setattr(ts, 'tempfile', tempfile, raising=False)
    monkeypatch.setattr(tempfile, 'mkstemp', tracked)
    ts.TenantSettingsStore._atomic_write(tmp_path/'target', b'synthetic')
    assert len(calls) == 1
    assert calls[0].name != 'target.tmp'
    assert not calls[0].exists()


def test_F13_stale_lazy_and_cached_adapter_do_not_read_new_secret(tmp_path, monkeypatch):
    from pyharness.core import llm
    from pyharness.engine import register_default_llm
    from tests.support import isolated_settings
    store = ts.TenantSettingsStore(tmp_path/'tenants')
    ts.register_tenant_store('other', store)
    store.upsert_profile('other', body(api_key='OLD-synthetic'))
    cfg = isolated_settings(tmp_path)
    cfg.llm.model = 'synthetic'
    cfg.llm.base_url = 'http://127.0.0.1:9'
    cfg.llm.api_key = 'tenant:other:test'
    cfg.llm.fallback_models = []
    saved = dict(llm.adapters)
    try:
        key = register_default_llm(cfg)
        adapter = llm.adapters[key]
        store.upsert_profile('other', body(api_key='NEW-synthetic'))
        reads = []
        monkeypatch.setattr(store, '_load_secrets', lambda *a: reads.append(1) or {'test':'NEW-synthetic'})
        with pytest.raises(PyHError): adapter._ensure_transport()
        assert not reads
    finally:
        llm.adapters.clear()
        llm.adapters.update(saved)


def test_missing_committed_generation_preserves_metadata(tmp_path):
    store = ts.TenantSettingsStore(tmp_path)
    store.upsert_profile('other', body(api_key='synthetic'))
    doc = store._load_doc('other')
    meta = store._paths('other')[0]
    before = meta.read_bytes()
    store._secret_path('other',doc).unlink()
    with pytest.raises(PyHError): store.upsert_profile('other', body(id='second', api_key='new-synthetic'))
    assert meta.read_bytes() == before


def test_retired_secret_cleanup_retries_all_generations(tmp_path, monkeypatch):
    store = ts.TenantSettingsStore(tmp_path)
    store.upsert_profile('other', body(api_key='OLD-synthetic'))
    old = store._secret_path('other',store._load_doc('other'))
    unlink = Path.unlink
    def fail(path, *args, **kw):
        if path == old: raise PermissionError('injected retirement')
        return unlink(path,*args,**kw)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'unlink',fail)
        assert store.upsert_profile('other',body(api_key='NEW-synthetic'))['cleanup_pending']
    result = store.upsert_profile('other',body(clear_api_key=True))
    assert not result['cleanup_pending']
    assert not old.exists()


@pytest.mark.parametrize('failure', [None,'mode','replace'])
def test_R02_mode_is_private_before_first_byte(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    calls = []
    real = ts.os
    def chmod(fd, mode):
        calls.append(('mode',mode))
        if failure == 'mode': raise PermissionError('injected mode')
    def write(fd,data):
        assert calls[0] == ('mode',0o600)
        calls.append(('write',len(data)))
        return real.write(fd,data)
    def replace(a,b):
        if failure == 'replace': raise OSError('injected replace')
        return real.replace(a,b)
    fake = SimpleNamespace(name='posix',fchmod=chmod,fstat=lambda fd:SimpleNamespace(st_mode=0o600),
        write=write,fsync=real.fsync,close=real.close,replace=replace)
    monkeypatch.setattr(ts,'os',fake)
    if failure:
        with pytest.raises(OSError): store_write = ts.TenantSettingsStore._atomic_write(tmp_path/'secret',b'SYNTHETIC')
        assert not list(tmp_path.iterdir())
        if failure == 'mode': assert calls == [('mode',0o600)]
    else:
        ts.TenantSettingsStore._atomic_write(tmp_path/'secret',b'SYNTHETIC')
        assert (tmp_path/'secret').read_bytes() == b'SYNTHETIC'


@pytest.mark.parametrize('initial', ['direct','reference'])
def test_F13_all_update_states(tmp_path,monkeypatch,initial):
    store = ts.TenantSettingsStore(tmp_path)
    monkeypatch.setenv('SYNTHETIC_ROTATION','REF-synthetic')
    first = {'api_key':'OLD-synthetic'} if initial == 'direct' else {'api_key_ref':'env:SYNTHETIC_ROTATION'}
    store.upsert_profile('default',body(**first))
    expected = 'OLD-synthetic' if initial == 'direct' else 'REF-synthetic'
    for extra in ({},{'api_key':''},{'api_key':'  '}):
        store.upsert_profile('default',body(**extra))
        assert store.resolve_ref('tenant:default:test') == expected
    store.upsert_profile('default',body(api_key='NEW-synthetic'))
    assert store.resolve_ref('tenant:default:test') == 'NEW-synthetic'
    store.upsert_profile('default',body(clear_api_key=True))
    with pytest.raises(PyHError):store.resolve_ref('tenant:default:test')


async def test_F01_HTTP_tenant_boundary_and_F13_cached_binding(tmp_path, monkeypatch, caplog):
    """Real loopback HTTP; synthetic model response and synthetic secrets only."""
    import httpx
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pyharness.cli import assemble_ctx
    from pyharness.desktop.app import DesktopApp
    from pyharness.core import llm
    from pyharness.engine import register_default_llm
    from tests.support import isolated_settings
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('content-length',0)))
            received.append(self.headers.get('Authorization'))
            data = json.dumps({'choices':[{'message':{'content':'synthetic model'},'finish_reason':'stop'}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(assemble_ctx(cfg))
    store = app._tenant_store()
    tenant_token = store.api_token('other')
    headers = {'X-PyHarness-Token':app._api_token,'X-PyHarness-Tenant':'other','X-PyHarness-Tenant-Token':tenant_token}
    monkeypatch.setenv("SYNTHETIC_HOST", "HOST-synthetic-SECRET")
    (tmp_path/"synthetic-host").write_text("HOST-file-synthetic-SECRET",encoding="utf-8")
    host_reads = []
    original = llm.resolve_secret_ref
    def spy(ref,*args,**kw):
        host_reads.append(ref)
        return original(ref,*args,**kw)
    monkeypatch.setattr(llm,'resolve_secret_ref',spy)
    endpoint = f'http://127.0.0.1:{server.server_port}'
    runtime = llm.LLMRuntime()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://localhost') as client:
            for ref in ('env:SYNTHETIC_HOST','file:'+str(tmp_path/'synthetic-host')):
                response = await client.post('/api/settings/models',headers=headers,json=body(base_url=endpoint,api_key_ref=ref))
                assert response.status_code >= 400
                assert not host_reads and not received
            response = await client.post('/api/settings/models',headers=headers,json=body(base_url=endpoint,api_key='TENANT-synthetic-SECRET'))
            assert response.status_code == 200
            assert 'TENANT-synthetic-SECRET' not in response.text
        cfg.llm.model='synthetic';cfg.llm.base_url=endpoint;cfg.llm.api_key='tenant:other:test'
        ts.register_tenant_store('other',store)
        key = register_default_llm(cfg,registry=runtime.registry)
        adapter = runtime.registry[key]
        transport = adapter._ensure_transport()
        await transport.complete({'model':'synthetic','messages':[]})
        assert received == ['Bearer TENANT-synthetic-SECRET']
        store.upsert_profile('other',body(base_url=endpoint,api_key='NEW-synthetic-SECRET'))
        for operation in ('complete','ping','stream'):
            with pytest.raises(PyHError):
                if operation == 'ping':await transport.ping()
                elif operation == 'stream':
                    async for _ in transport.stream({'messages':[]}):pass
                else:await transport.complete({'messages':[]})
        assert len(received) == 1
        assert 'TENANT-synthetic-SECRET' not in caplog.text
        assert 'NEW-synthetic-SECRET' not in caplog.text
    finally:
        await runtime.aclose()
        await app._shutdown_async()
        await asyncio.to_thread(server.shutdown)
        server.server_close();worker.join(timeout=3)
        assert not worker.is_alive()

async def test_rejected_public_spine_cannot_cache_host_credentials(tmp_path, monkeypatch):
    from pyharness.application.service import ApplicationService
    from pyharness.cli import assemble_ctx
    from pyharness.core import llm
    from tests.support import isolated_settings
    cfg = isolated_settings(tmp_path)
    cfg.llm.api_key = 'env:SYNTHETIC_HOST'
    monkeypatch.setenv('SYNTHETIC_HOST', 'HOST-synthetic-secret')
    service = ApplicationService(assemble_ctx(cfg), channel='desktop', tenant_id='other')
    received=[]
    class Transport:
        def __init__(self, base_url, api_key, timeout): received.append(api_key)
        async def complete(self, req): return {'choices':[{'message':{'content':'synthetic'},'finish_reason':'stop'}]}
        async def aclose(self): pass
    monkeypatch.setattr(llm,'_OpenAICompatHTTPTransport',Transport)
    try:
        sid=await service.create_session()
        with pytest.raises(PyHError): await service.list_jobs(sid)
        service.save_model_profile(body(api_key='TENANT-synthetic-secret'))
        await service.queue_for(sid)
        adapter=service._engines[sid].llm.require()
        await adapter._ensure_transport().complete({'messages':[]})
        assert received==['TENANT-synthetic-secret']
        assert adapter.model=='synthetic'
    finally: await service.shutdown()

async def test_borrowed_primary_does_not_authorize_host_fallback(tmp_path,adapter_factory):
    from pyharness.application.service import ApplicationService
    from pyharness.cli import assemble_ctx
    from tests.support import isolated_settings
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    cfg.llm.fallback_models=['host-fallback-synthetic']
    service=ApplicationService(assemble_ctx(cfg),channel='desktop',tenant_id='other')
    try:
        sid=await service.create_session()
        with pytest.raises(PyHError):await service.list_jobs(sid)
        assert not service._engines and not service._queues
    finally:await service.shutdown()
