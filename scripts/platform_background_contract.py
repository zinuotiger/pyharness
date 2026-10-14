"""Background library acceptance against the real app, isolated data, no models.

Uses the installed Chrome/Edge. Screenshots/runtime go outside the checkout.
--package-root can point at an extracted wheel to verify its actual assets.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.platform_browser_smoke import find_browser, navigate
from scripts.ci_verify import environment, install_network_boundary

ORIGINAL = ['light', 'dark', 'tech-blue-particle', 'starfield', 'sakura', 'aurora', 'gold', 'ocean']
PARTICLES = ['particle-nebula-light','particle-ice-aurora-light','particle-ocean-blue-light',
    'particle-starry-sky-light','particle-aurora-light','particle-ocean-light']


async def verify(output, package_root=None):
    browser_path = find_browser()
    assert browser_path, 'Existing Chrome/Edge required; never downloads a browser'
    env = environment(output / 'isolated')
    os.environ.clear()
    os.environ.update(env)
    install_network_boundary()
    if package_root:
        sys.path.insert(0, str(package_root))
    import pyharness
    from pyharness import cli
    from pyharness.web import settings_for
    from pyharness.desktop.app import DesktopApp
    from playwright.async_api import async_playwright
    import uvicorn

    app = DesktopApp(cli.assemble_ctx(settings_for(output / 'data')))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app.api, log_level='warning', timeout_graceful_shutdown=5))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    errors, missing, external, full_requests, checks = [], [], [], [], []
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(.02)
        assert server.started
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=browser_path, headless=True,
                args=['--disable-background-networking', '--no-first-run'])
            context = await browser.new_context(viewport={'width':1440, 'height':1000},
                extra_http_headers={'X-PyHarness-Token':app._api_token, 'Connection':'close'})

            async def boundary(route):
                if route.request.url.startswith(url + '/'):
                    await route.continue_()
                else:
                    external.append(route.request.url.split('?')[0])
                    await route.abort()
            await context.route('**/*', boundary)
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('response', lambda response: missing.append(response.url) if response.status == 404 else None)
            page.on('request', lambda request: full_requests.append(request.url) if '/scenes/' in request.url and '/thumbnails/' not in request.url else None)
            await page.goto(url)
            await page.locator('#compose').wait_for()
            await page.wait_for_function('window.pyharnessAppearance')
            await page.locator('#messageText').fill('背景验收草稿，不发送给模型。')

            async def config():
                return await page.evaluate('pyharnessAppearance.config')

            async def stored():
                return await page.evaluate('localStorage.getItem("pyharness.appearance.v1")')

            async def reload():
                await page.evaluate('window.disconnectStream?.()')
                await page.reload()
                await page.wait_for_function('window.pyharnessAppearance')

            async def open_library():
                await navigate(page, 'settings')
                await page.locator('[data-more-backgrounds]').click()
                await page.locator('.phtheme-library[open]').wait_for()

            dialog = page.locator('.phtheme-library')
            async def preview(identity):
                await dialog.locator(f'button[data-background-id="{identity}"]').click()
                await page.wait_for_function('!document.querySelector("[data-background-apply]").disabled')

            for identity in ORIGINAL:
                assert await page.evaluate('(themeId)=>pyharnessAppearance.updateConfig({themeId,animationMode:"off"})', identity)
                assert (await config())['themeId'] == identity
                if identity not in ['light', 'dark']:
                    await page.wait_for_function('document.querySelector(".phtheme-backdrop").naturalWidth>0')
            checks.append('all 8 original IDs and 6 original scene images remain usable')
            await page.evaluate('pyharnessAppearance.updateConfig({themeId:"dark",count:17,speed:.2,opacity:.4,animationMode:"off"})')
            before = await stored()
            await open_library()
            assert await dialog.locator('button[data-background-id]').count() == 14
            assert await dialog.locator('[aria-disabled=true]').count() == 0
            assert await dialog.locator('[data-applied]').get_attribute('data-background-id') == 'dark'
            before_requests = len(full_requests)
            await dialog.locator('[data-background-filter="particle"]').click()
            assert await dialog.locator('.phtheme-library-choice:visible').count() == 6
            assert await dialog.locator('.phtheme-library-choice:visible img').count() == 6
            await page.screenshot(path=str(output / 'particle-backgrounds-library.png'))
            await dialog.locator('[data-background-filter="original"]').click()
            assert await dialog.locator('.phtheme-library-choice:visible').count() == 8
            assert len(full_requests) == before_requests, 'Opening/filtering loads thumbnails only'
            await preview('aurora')
            assert (await config())['themeId'] == 'aurora' and await stored() == before
            await page.screenshot(path=str(output / 'more-backgrounds-preview.png'))
            await page.keyboard.press('Escape')
            assert (await config())['themeId'] == 'dark' and await stored() == before
            assert await page.locator('[data-more-backgrounds]').evaluate('(el)=>el===document.activeElement')
            for label in ['取消', '关闭背景库']:
                await open_library()
                await preview('gold')
                await dialog.get_by_role('button', name=label, exact=True).click()
                assert (await config())['themeId'] == 'dark' and await stored() == before
            checks.append('filters, real thumbnails/current badge, preview, cancel, close, Esc, focus restoration; thumbnails only')
            await open_library()
            await preview('ocean')
            await dialog.locator('[data-background-apply]').click()
            await page.wait_for_function('!document.querySelector(".phtheme-library").open')
            assert json.loads(await stored())['themeId'] == 'ocean'
            await reload()
            assert (await config())['themeId'] == 'ocean'
            await navigate(page, 'chat')
            assert await page.locator('#messageText').input_value() == '背景验收草稿，不发送给模型。'
            await page.screenshot(path=str(output / 'original-ocean-chat.png'))
            await open_library()
            await dialog.locator('[data-background-default]').click()
            await page.wait_for_function('!document.querySelector("[data-background-apply]").disabled')
            assert await dialog.locator('[aria-pressed=true][data-background-id]').get_attribute('data-background-id') == 'tech-blue-particle'
            await dialog.locator('[data-background-apply]').click()
            current = await config()
            assert current['themeId'] == 'tech-blue-particle'
            assert (current['count'],current['speed'],current['opacity'],current['animationMode']) == (17,.2,.4,'off')
            await page.locator('[data-appearance-reset]').click()
            await page.wait_for_function('pyharnessAppearance.config.themeId==="light"')
            assert (await config())['count'] == 17
            await page.locator('[data-scene-choice="sakura"]').click()
            await page.wait_for_function('pyharnessAppearance.savedConfig.themeId==="sakura"')
            checks.append('apply, reload, page navigation, draft retention, background-only default/reset, original immediate shortcuts')

            # Each new image is applied, displayed, refreshed and navigated in
            # the real app. No concept renders substitute for these screenshots.
            await page.evaluate('pyharnessAppearance.updateConfig({themeId:"light",backgroundId:null})')
            for identity in PARTICLES:
                before = await stored()
                await open_library()
                await dialog.locator('[data-background-filter="particle"]').click()
                await preview(identity)
                assert (await config())['themeId'] == 'light'
                assert (await config())['backgroundId'] == identity and await stored() == before
                assert not await page.evaluate('pyharnessAppearance.stats().active')
                await dialog.locator('[data-background-apply]').click()
                await page.wait_for_function('!document.querySelector(".phtheme-library").open')
                await reload()
                assert (await config())['backgroundId'] == identity and (await config())['themeId'] == 'light'
                await navigate(page, 'chat')
                await page.wait_for_function('(id)=>document.querySelector(".phtheme-backdrop").src.endsWith(id+".png")&&document.querySelector(".phtheme-backdrop").naturalWidth===1672',arg=identity)
                assert await page.locator('#messageText').input_value() == '背景验收草稿，不发送给模型。'
                assert await page.locator('[data-pyh-static-background]').count() == 1
                await page.screenshot(path=str(output / (identity+'-chat.png')))
            await page.evaluate('pyharnessAppearance.updateConfig({themeId:"dark",backgroundId:null})')
            await open_library()
            await preview(PARTICLES[0])
            assert (await config())['themeId'] == 'dark'
            assert await page.locator('body').get_attribute('data-pyh-theme') == 'dark'
            assert await page.locator('body').get_attribute('data-pyh-scene') is None
            await page.screenshot(path=str(output / 'particle-dark-mode-preview.png'))
            await dialog.locator('[data-background-apply]').click()
            await page.wait_for_function('!document.querySelector(".phtheme-library").open')
            await reload()
            assert (await config())['themeId'] == 'dark' and (await config())['backgroundId'] == PARTICLES[0]
            assert await page.locator('body').get_attribute('data-pyh-theme') == 'dark'
            checks.append('all six independent static backgrounds apply/reload/navigate; light/dark mode unchanged; no animation')

            # Reproduce switching from the legacy ocean skin (the reported
            # screenshot) rather than only testing an already-light theme.
            for identity in PARTICLES:
                await page.evaluate('pyharnessAppearance.updateConfig({themeId:"ocean",backgroundId:null})')
                before = await stored()
                await open_library()
                await dialog.locator('[data-background-filter="particle"]').click()
                await preview(identity)
                if identity == PARTICLES[-1]:
                    await page.screenshot(path=str(output / 'ocean-to-light-preview.png'))
                assert await page.locator('body').get_attribute('data-pyh-scene') is None, 'Legacy ocean overlay leaks into light background'
                assert await page.locator('body').get_attribute('data-pyh-theme') == 'light'
                assert (await config())['themeId'] == 'ocean' and await stored() == before
                for select in await page.locator('[data-appearance-field="themeId"]').all():
                    assert await select.input_value() == identity, 'Skin selector displays the old background'
                assert not await page.evaluate('pyharnessAppearance.stats().active')
                await page.keyboard.press('Escape')
                assert await page.locator('body').get_attribute('data-pyh-scene') == 'ocean'
                assert (await config())['backgroundId'] is None and await stored() == before
                await open_library()
                await preview(identity)
                await dialog.locator('[data-background-apply]').click()
                await page.wait_for_function('!document.querySelector(".phtheme-library").open')
                await reload()
                assert (await config())['themeId'] == 'ocean' and (await config())['backgroundId'] == identity
                assert await page.locator('body').get_attribute('data-pyh-scene') is None
                await navigate(page, 'settings')
                for selector in ['.settings-layout', '.settings-content']:
                    assert await page.locator(selector).evaluate('(el)=>getComputedStyle(el).backgroundColor') == 'rgba(0, 0, 0, 0)'
                if identity == PARTICLES[-1]:
                    await page.screenshot(path=str(output / 'ocean-to-light-settings.png'))
                    await open_library()
                    await dialog.locator('[data-background-filter="particle"]').click()
                    await page.screenshot(path=str(output / 'ocean-to-light-applied-library.png'))
                    await page.keyboard.press('Escape')
            for theme in ORIGINAL[2:]:
                await page.evaluate('(themeId)=>pyharnessAppearance.updateConfig({themeId,backgroundId:null})',theme)
                before = await stored()
                await open_library()
                await preview(PARTICLES[0])
                assert await page.locator('body').get_attribute('data-pyh-theme') == 'light'
                assert await page.locator('body').get_attribute('data-pyh-scene') is None
                await page.keyboard.press('Escape')
                assert (await config())['themeId'] == theme and await stored() == before, {'expected_theme':theme,'actual':await config(),'before':before,'after':await stored()}
            await page.evaluate('pyharnessAppearance.updateConfig({themeId:"ocean",backgroundId:null})')
            # Both existing selectors also select the actual static background.
            for select in await page.locator('[data-appearance-field="themeId"]').all():
                await select.select_option('particle-nebula-light')
                await page.wait_for_function('pyharnessAppearance.savedConfig.backgroundId==="particle-nebula-light"')
                assert (await config())['themeId'] == 'ocean'
                await select.select_option('ocean')
                await page.wait_for_function('pyharnessAppearance.savedConfig.backgroundId===null')
                assert await page.locator('body').get_attribute('data-pyh-scene') == 'ocean'
            checks.append('legacy ocean to all six light backgrounds: no old overlay; accurate selectors; cancel/reload; transparent settings; direct dropdown selection')

            # A native dialog's close event is queued. It must not cancel an
            # independent selection made immediately after the cancel button.
            await page.evaluate('pyharnessAppearance.updateConfig({themeId:"light",backgroundId:null})')
            await reload()
            quick_started, quick_release = asyncio.Event(), asyncio.Event()
            async def quick_delayed(route):
                quick_started.set()
                await quick_release.wait()
                await route.continue_()
            await page.route('**/scenes/sakura.png', quick_delayed)
            await open_library()
            await preview(PARTICLES[0])
            await page.evaluate('''()=>{
                document.querySelector('[data-library-close]').click();
                window.quickChange=undefined;
                pyharnessAppearance.updateConfig({themeId:'sakura',backgroundId:null}).then(ok=>window.quickChange=ok);
            }''')
            await asyncio.wait_for(quick_started.wait(),5)
            await page.wait_for_timeout(100)
            quick_release.set()
            await page.wait_for_function('typeof window.quickChange==="boolean"')
            await page.unroute('**/scenes/sakura.png',quick_delayed)
            assert await page.evaluate('window.quickChange'), 'Queued dialog close cancels subsequent background selection'
            assert (await config())['themeId'] == 'sakura'
            assert json.loads(await stored())['themeId'] == 'sakura'
            await open_library()
            await preview(PARTICLES[1])
            await page.evaluate('''()=>new Promise(resolve=>{
                const dialog=document.querySelector('.phtheme-library');
                dialog.addEventListener('close',()=>resolve(true),{once:true});dialog.close();
            })''')
            assert (await config())['themeId'] == 'sakura' and (await config())['backgroundId'] is None
            checks.append('cancel followed immediately by a new selection cannot be undone by a queued native close event')

            # Reload clears the controller image cache to exercise actual races.
            await page.evaluate('pyharnessAppearance.updateConfig({themeId:"light",backgroundId:null})')
            await reload()
            started, release = asyncio.Event(), asyncio.Event()
            async def delayed(route):
                started.set()
                await release.wait()
                await route.continue_()
            await page.route('**/scenes/particle-ice-aurora-light.png', delayed)
            await open_library()
            await dialog.locator('[data-background-id="particle-ice-aurora-light"]').click()
            await asyncio.wait_for(started.wait(), 5)
            await preview('particle-nebula-light')
            release.set()
            await page.wait_for_timeout(200)
            assert (await config())['backgroundId'] == 'particle-nebula-light'
            await page.keyboard.press('Escape')
            assert (await config())['themeId'] == 'light'
            await page.unroute('**/scenes/particle-ice-aurora-light.png', delayed)
            started.clear()
            release.clear()
            await page.route('**/scenes/particle-ocean-light.png', delayed)
            before = await stored()
            await open_library()
            await dialog.locator('[data-background-id="particle-ocean-light"]').click()
            await asyncio.wait_for(started.wait(), 5)
            await page.keyboard.press('Escape')
            release.set()
            await page.wait_for_timeout(200)
            assert (await config())['themeId'] == 'light' and await stored() == before
            await page.unroute('**/scenes/particle-ocean-light.png', delayed)
            checks.append('last selection wins; late image after close cannot reapply or persist')

            await page.route('**/scenes/particle-starry-sky-light.png', lambda route: route.abort())
            await open_library()
            await dialog.locator('[data-background-id="particle-starry-sky-light"]').click()
            await dialog.get_by_text('背景图片加载失败，已保留可用背景。',exact=True).wait_for()
            assert (await config())['backgroundId'] is None and await stored() == before
            assert await dialog.locator('[data-background-apply]').is_disabled()
            await page.unroute('**/scenes/particle-starry-sky-light.png')
            await preview('particle-starry-sky-light')
            await page.keyboard.press('Escape')

            await page.route('**/scenes/starfield.png', lambda route: route.abort())
            await open_library()
            await dialog.locator('[data-background-id="starfield"]').click()
            await dialog.get_by_text('背景图片加载失败，已保留可用背景。',exact=True).wait_for()
            assert (await config())['themeId'] == 'light' and await stored() == before
            assert await dialog.locator('[data-background-apply]').is_disabled()
            await page.unroute('**/scenes/starfield.png')
            await preview('starfield')
            await page.keyboard.press('Escape')
            await open_library()
            await preview('dark')
            await page.evaluate('()=>{window.originalSetItem=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new Error("synthetic storage denial")};}')
            await dialog.locator('[data-background-apply]').click()
            await dialog.get_by_text('保存失败，已恢复原背景。请检查存储权限后重试。',exact=True).wait_for()
            assert (await config())['themeId'] == 'light' and await stored() == before
            await page.evaluate('()=>{Storage.prototype.setItem=window.originalSetItem;}')
            await page.keyboard.press('Escape')
            checks.append('image failure preserves usable background, retry works, storage failure rolls back with clear feedback')

            await page.evaluate('localStorage.setItem("pyharness.appearance.v1",JSON.stringify({themeId:"unknown",backgroundId:"unknown-background"}))')
            await reload()
            assert (await config())['themeId'] == 'tech-blue-particle' and (await config())['backgroundId'] is None
            await page.evaluate('localStorage.setItem("pyharness.appearance.v1","{broken")')
            await reload()
            assert (await config())['themeId'] == 'tech-blue-particle'
            checks.append('unknown IDs and corrupt preferences fall back safely')
            await page.set_viewport_size({'width':390, 'height':844})
            await open_library()
            await dialog.locator('[data-background-filter="particle"]').click()
            await preview('particle-aurora-light')
            assert await dialog.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert await dialog.locator('[data-background-apply]').evaluate('(el)=>el.getBoundingClientRect().bottom<=innerHeight')
            await page.screenshot(path=str(output / 'more-backgrounds-narrow.png'))
            await page.keyboard.press('Tab')
            assert await dialog.evaluate('(el)=>el.contains(document.activeElement)')
            await page.keyboard.press('Escape')
            await page.set_viewport_size({'width':1440, 'height':1000})
            await open_library()
            await page.screenshot(path=str(output / 'more-backgrounds-dialog.png'))
            await page.keyboard.press('Escape')
            checks.append('390px and desktop layout, native dialog keyboard focus containment')
            ordered = await page.evaluate('''async()=>{
                const {createAppearance}=await import('/assets/theme-kit/appearance/controller.js');
                const root=document.createElement('section');document.body.append(root);
                const writes=[];
                const controller=await createAppearance(root,{store:{load:async()=>({themeId:'light'}),
                    save:async value=>{await new Promise(resolve=>setTimeout(resolve,value.themeId==='dark'?80:0));writes.push(value.themeId);return true;}}});
                const first=controller.updateConfig({themeId:'dark'});
                await new Promise(resolve=>setTimeout(resolve,0));
                const second=controller.updateConfig({themeId:'light'});
                await Promise.all([first,second]);
                const result={writes,config:controller.savedConfig.themeId};controller.destroy();root.remove();return result;
            }''')
            assert ordered == {'writes':['dark','light'],'config':'light'}, ordered
            checks.append('asynchronous WebView preference saves stay ordered; last selection persists')
            assert not errors, errors
            assert not missing, missing
            assert not external, external
            await context.close()
            await browser.close()
        report = {'status':'passed', 'package':str(Path(pyharness.__file__).parent), 'checks':checks,
            'page_errors':errors,'image_404':missing,'external_requests':external,
            'original_options':8,'original_scene_images':6,'new_materials_ready':6,
            'new_materials_pending':0,'real_model_requests':False}
        (output / 'background-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        await app.service.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--package-root',type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Use an output directory outside the checkout')
    output.mkdir(parents=True,exist_ok=True)
    asyncio.run(verify(output,args.package_root))
