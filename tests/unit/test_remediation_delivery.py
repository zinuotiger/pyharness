from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.parametrize('files,expected', [({'a.py':'pass'},0),
    ({'a.py':'ctx.governance.authorize()'},1),
    ({'a.py':'ctx.governance.authorize(); ctx.governance.authorize()'},2),
    ({'a.py':'ctx.governance.authorize()', 'b.py':'ctx.governance.authorize()'},2),
    ({'a.py':'# ctx.governance.authorize()\ns = "ctx.governance.authorize()"'},0),
    ({'a.py':'gate = ctx.governance\ngate.authorize()'},1),
    ({'a.py':'check = ctx.governance.authorize\ncheck()'},1)])
def test_G02_counts_calls_and_simple_aliases(tmp_path, authorize_call_sites, files, expected):
    root = tmp_path/'pyharness'; root.mkdir()
    for name, content in files.items(): (root/name).write_text(content,encoding='utf-8')
    sites = authorize_call_sites(tmp_path)
    assert len(sites) == expected
    for site in sites:
        assert set(site) == {'file','line','column','expression'}
        assert site['line'] > 0 and site['column'] >= 0


@pytest.mark.controlled_process
def test_F11_native_import_does_not_require_web_stack(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    package_root = Path(__file__).resolve().parents[2]
    code = '''import sys, importlib.abc
class BlockWeb(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'uvicorn','fastapi','starlette','webview'}:
            raise ImportError('Web stack forbidden in native-only acceptance: '+fullname)
sys.meta_path.insert(0, BlockWeb())
sys.path.insert(0, sys.argv[1])
from pyharness.desktop_native import main
from pyharness.application.service import ApplicationService
from pyharness.desktop.sessions import DesktopSessionManager
assert callable(main)
print('native-only import OK')
'''
    result = subprocess.run([sys.executable,'-I','-B','-c',code,str(package_root)],
                            cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'native-only import OK'


def test_F12_ipv6_url():
    from pyharness.desktop.app import DesktopApp
    app = DesktopApp.__new__(DesktopApp)
    app._api_token = 'synthetic'
    assert app.bootstrap_url('::1', 12345) == 'http://[::1]:12345/?token=synthetic'


def test_F12_readiness_probes_configured_host(monkeypatch):
    from pyharness.desktop import net
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(net.socket, 'create_connection', lambda target,**kw: calls.append(target) or Connection())
    assert net._probe_port(12345, host='::1')
    assert calls == [('::1', 12345)]


@pytest.mark.parametrize('host,expected', [('127.0.0.1:12345',200), ('localhost:12345',200),
    ('[::1]:12345',200), ('evil.example',400), ('[::2]:12345',400),
    ('user@localhost',400), ('[::1]:bad',400), ('localhost/evil',400)])
async def test_F12_host_validation(tmp_path, host, expected):
    from pyharness.cli import assemble_ctx
    from pyharness.desktop.app import DesktopApp
    from tests.support import isolated_settings
    app = DesktopApp(assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as client:
            result = await client.get('/api/sessions',headers={'host':host,'X-PyHarness-Token':app._api_token})
            assert result.status_code == expected
    finally: await app._shutdown_async()


@pytest.mark.parametrize('host',['127.0.0.1','localhost','::1'])
def test_F12_real_windows_loopback_service(tmp_path,host):
    import threading
    from pyharness.desktop.app import DesktopApp
    from pyharness.desktop import net
    from pyharness.cli import assemble_ctx
    from tests.support import isolated_settings
    cfg=isolated_settings(tmp_path);cfg.shell.web.host=host;cfg.shell.web.port=0
    address,port=net.resolve_bind(cfg)
    app=DesktopApp(assemble_ctx(cfg))
    worker=threading.Thread(target=net.run_uvicorn,args=(app,port,address),name='test-owned-uvicorn')
    worker.start()
    try:
        assert net.wait_until_listening(port,timeout=5,host=address,app=app)
        url=f'http://[{address}]:{port}' if ':' in address else f'http://{address}:{port}'
        with httpx.Client(trust_env=False,timeout=5) as client:
            response=client.get(url+'/api/sessions',headers={'X-PyHarness-Token':app._api_token})
        assert response.status_code==200,response.text
        # A listening foreign endpoint alone cannot announce our app ready.
        foreign=SimpleNamespace(server=SimpleNamespace(started=False))
        assert not net.wait_until_listening(port,timeout=.02,host=address,app=foreign)
    finally:
        app.shutdown_gracefully();worker.join(timeout=10)
        assert not worker.is_alive()
    assert not net._probe_port(port,host=address)

@pytest.mark.parametrize('stage',['config','controller','window','show','interrupt','run'])
def test_native_main_cleans_partial_initialization_and_interrupt(monkeypatch,stage):
    import asyncio
    import qasync
    from pyharness.desktop_native import app as module
    calls=[]
    class QtApp:
        @staticmethod
        def instance():return QtApp()
        def setApplicationName(self,name):pass
    class Loop:
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def close(self):calls.append('loop-close')
        def run_forever(self):
            if stage=='interrupt':raise KeyboardInterrupt()
            if stage=='run':raise RuntimeError('run fault')
        def run_until_complete(self,coro):return asyncio.run(coro)
    class Controller:
        def __init__(self,ctx):
            if stage=='controller':raise RuntimeError('controller fault')
        async def close(self):calls.append('controller-close')
    class Window:
        def __init__(self,controller):
            if stage=='window':raise RuntimeError('window fault')
        def show(self):
            if stage=='show':raise RuntimeError('show fault')
    def context():
        if stage=='config':raise RuntimeError('config fault')
        return SimpleNamespace()
    monkeypatch.setattr(module,'QApplication',QtApp)
    monkeypatch.setattr(qasync,'QEventLoop',lambda app:Loop())
    monkeypatch.setattr(module.asyncio,'set_event_loop',lambda loop:None)
    monkeypatch.setattr(module,'assemble_desktop_ctx',context)
    monkeypatch.setattr(module,'NativeController',Controller)
    monkeypatch.setattr(module,'MainWindow',Window)
    assert module.main([])==(130 if stage=='interrupt' else 1)
    assert calls==(['loop-close'] if stage in ('config','controller') else ['controller-close','loop-close'])

async def test_native_controller_uses_real_application_channel(tmp_path,adapter_factory):
    from PySide6.QtWidgets import QApplication
    from pyharness.desktop_native.controller import NativeController
    from pyharness.cli import assemble_ctx
    from tests.support import isolated_settings
    app=QApplication.instance() or QApplication([])
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    controller=NativeController(assemble_ctx(cfg))
    try:
        sid=await controller.service.create_session()
        await controller.service.queue_for(sid)
        assert controller.service.channel=='desktop'
        assert controller.service._engines[sid].channel=='desktop'
    finally:await controller.close()
