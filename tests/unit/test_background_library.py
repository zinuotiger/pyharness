"""Local resource and compatibility checks; no external service or model."""
import io
from importlib.resources import files

import httpx
import pytest
from PIL import Image

from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.ui.appearance import FilePreferences, normalize
from tests.support import isolated_settings


@pytest.mark.parametrize('theme', ['light','dark','tech-blue-particle','starfield','sakura','aurora','gold','ocean'])
def test_old_preferences_survive_background_extension(tmp_path, theme):
    store = FilePreferences(tmp_path / 'appearance.json')
    store.path.write_text('{"themeId":"'+theme+'","animationMode":"off","count":17}', encoding='utf-8')
    value = store.load()
    assert value['themeId'] == theme
    assert value['animationMode'] == 'off' and value['count'] == 17
    assert value['backgroundId'] is None
    assert store.save(value) and store.load() == value


def test_unknown_background_not_persisted_as_usable_background(tmp_path):
    store = FilePreferences(tmp_path / 'appearance.json')
    assert store.save({'themeId':'dark','backgroundId':'unknown-background','count':17})
    assert store.load()['themeId'] == 'dark'
    assert store.load()['backgroundId'] is None
    assert store.load()['count'] == 17


@pytest.mark.parametrize('background', ['particle-nebula-light','particle-ice-aurora-light',
    'particle-ocean-blue-light','particle-starry-sky-light','particle-aurora-light','particle-ocean-light'])
def test_static_background_roundtrip_keeps_theme_and_native_boundary(tmp_path, background):
    store = FilePreferences(tmp_path / 'appearance.json')
    for theme in ('light','dark','tech-blue-particle'):
        assert store.save({'themeId':theme,'backgroundId':background,'animationMode':'off','count':17})
        value = store.load()
        assert value['backgroundId'] == background and value['themeId'] == theme
        assert value['count'] == 17 and value['animationMode'] == 'off'
        assert normalize(value)['backgroundId'] is None


async def test_background_modules_and_real_small_thumbnails_served(tmp_path):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),
                base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as client:
            for module in ['appearance/background-library.js','tokens/backgrounds.js']:
                response = await client.get('/assets/theme-kit/'+module)
                assert response.status_code == 200 and 'javascript' in response.headers['content-type']
            names = ['light','dark','technology','starfield','sakura','aurora','gold','ocean',
                'particle-nebula-light','particle-ice-aurora-light','particle-ocean-blue-light',
                'particle-starry-sky-light','particle-aurora-light','particle-ocean-light']
            for name in names:
                response = await client.get('/assets/theme-kit/scenes/thumbnails/'+name+'.png')
                assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
                with Image.open(io.BytesIO(response.content)) as image:
                    assert max(image.size) <= 320
                assert len(response.content) < 125_000
                if name not in ['light','dark']:
                    original = files('pyharness.ui').joinpath('theme-kit','scenes',name+'.png').read_bytes()
                    assert len(response.content) < len(original) / 10
                    full = await client.get('/assets/theme-kit/scenes/'+name+'.png')
                    assert full.status_code == 200 and full.content == original
    finally:
        await app.service.shutdown()
