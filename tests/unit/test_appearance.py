import json
import httpx
import pytest
from pyharness.ui.appearance import FilePreferences,normalize
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


def test_preference_whitelist_corruption_reset(tmp_path):
    store=FilePreferences(tmp_path/'appearance.json')
    assert store.save({'themeId':'dark','count':100000,'api_key':'synthetic-do-not-store','animationMode':'off'})
    assert store.load()['count']==80 and store.load()['animationMode']=='off'
    assert 'api_key' not in store.path.read_text()
    store.path.write_text('{broken',encoding='utf-8')
    assert store.load()==normalize({})
    assert store.reset() and not store.path.exists()


@pytest.mark.parametrize('scene', ['starfield', 'sakura', 'aurora', 'gold', 'ocean', 'tech-blue-particle'])
def test_web_scene_roundtrip_and_native_boundary(tmp_path, scene):
    store = FilePreferences(tmp_path / 'appearance.json')
    assert store.save({'themeId': scene, 'animationMode': 'low'})
    assert store.load()['themeId'] == scene
    assert store.load()['animationMode'] == 'low'
    assert normalize({'themeId': scene})['themeId'] == 'tech-blue-particle'


async def test_packaged_assets_mime_and_traversal(tmp_path):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as client:
            for path,mime in [('theme-kit/particles/engine.js','javascript'),('theme-kit/styles/theme.css','text/css'),('host-adapters/platform.js','javascript')]:
                response=await client.get('/assets/'+path)
                assert response.status_code==200 and mime in response.headers['content-type']
                assert response.headers['x-content-type-options']=='nosniff'
            for path in ['theme-kit/missing.js','host-adapters/%2e%2e/%2e%2e/config.py','appearance.py']:
                assert (await client.get('/assets/'+path)).status_code==404
            for scene in ['starfield', 'sakura', 'aurora', 'gold', 'ocean', 'technology']:
                response = await client.get('/assets/theme-kit/scenes/' + scene + '.png')
                assert response.status_code == 200
                assert response.headers['content-type'] == 'image/png'
                assert response.headers['x-content-type-options'] == 'nosniff'
                assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
    finally:await app.service.shutdown()


def test_native_theme_preferences_and_pause_lifecycle(tmp_path):
    pytest.importorskip('PySide6',reason='native extra required')
    from PySide6.QtWidgets import QApplication,QMainWindow,QLineEdit
    from pyharness.desktop_native.appearance import NativeAppearance
    app=QApplication.instance() or QApplication([])
    window=QMainWindow();editor=QLineEdit('preserve draft');window.setCentralWidget(editor)
    host=NativeAppearance(window,tmp_path/'native.ini')
    try:
        window.show();app.processEvents()
        assert host.banner.timer.isActive()
        window.hide();app.processEvents()
        assert not host.banner.timer.isActive()
        window.show();app.processEvents()
        assert host.banner.timer.isActive()
        host.update({'animationMode':'off','api_key':'synthetic-never-save'})
        window.hide();window.show();app.processEvents()
        assert not host.banner.timer.isActive() and editor.text()=='preserve draft'
        assert 'synthetic-never-save' not in (tmp_path/'native.ini').read_text()
        host.destroy();host.destroy();assert not host.banner.timer.isActive()
    finally:window.close();window.deleteLater();app.processEvents()
