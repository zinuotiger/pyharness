"""Six scene visual/lifecycle acceptance on an isolated loopback application.

Uses the existing Chrome and synthetic files. No model requests or user profiles.
All screenshots and runtime files must be outside the checkout.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.platform_browser_smoke import find_browser, navigate
from scripts.ci_verify import environment, install_network_boundary

SCENES = ['starfield', 'sakura', 'aurora', 'gold', 'ocean', 'tech-blue-particle']


def frontend_snapshot():
    files = [ROOT / 'pyharness/ui/platform.html']
    files += list((ROOT / 'pyharness/ui/theme-kit').rglob('*'))
    files += list((ROOT / 'pyharness/ui/host-adapters').glob('*.js'))
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.is_file()}


async def main(output, representative=False):
    source_snapshot = frontend_snapshot()
    executable = find_browser()
    if not executable:
        raise RuntimeError('Existing Chrome/Edge required; no download')
    env=environment(output / 'isolated')
    os.environ.clear(); os.environ.update(env)
    install_network_boundary()
    from pyharness import cli
    from pyharness.web import settings_for
    from pyharness.desktop.app import DesktopApp
    from playwright.async_api import async_playwright
    import uvicorn
    app = DesktopApp(cli.assemble_ctx(settings_for(output / 'data')))
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app.api, log_level='warning', timeout_graceful_shutdown=10))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    errors = []; records = []; external = []
    try:
        for _ in range(200):
            if server.started: break
            await asyncio.sleep(.02)
        assert server.started
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=executable, headless=True,
                args=['--disable-background-networking','--no-first-run'])
            context = await browser.new_context(viewport={'width':1440,'height':900},
                extra_http_headers={'X-PyHarness-Token':app._api_token,'Connection':'close'})
            async def boundary(route):
                if route.request.url.startswith(url + '/'):
                    await route.continue_()
                else:
                    external.append(route.request.url.split('?')[0]); await route.abort()
            await context.route('**/*', boundary)
            page = await context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            async def reload_page():
                await page.evaluate('window.disconnectStream?.()')
                await page.wait_for_timeout(100)
                await page.reload()
            await page.goto(url); await page.locator('#compose').wait_for()
            await page.wait_for_function('window.pyharnessAppearance')
            await page.locator('#messageText').fill('这是隔离预览草稿，不会发送给模型。')
            if not representative:
                from scripts.platform_theme_contract import synthetic_png
                async with page.expect_file_chooser() as chooser:
                    await page.locator('[data-action="artifact-upload"]').click()
                await (await chooser.value).set_files({'name':'scene-sample.png','mimeType':'image/png','buffer':synthetic_png()})
                await page.wait_for_function('composer().attachments.length===1')
                # Persist only clearly labelled synthetic conversation records.
                log = await app.service.require_session(await page.evaluate('state.sid'))
                await log.append('user.message', {'content':'合成外观验收：请检查文字、换行、按钮和滚动。\n这是测试正文，不包含用户数据。'}, actor='user', sync=True)
                await log.append('llm.response', {'model':'synthetic-visual-fixture','finish_reason':'stop','content':'合成回复（没有调用模型）：场景在正文背后，输入框和审批信息保持独立、清晰。'}, actor='agent', sync=True)
                async def approval_fixture(route):
                    await route.fulfill(json={'approvals':[{'id':'scene-fixture','tool':'合成审批 · 不执行工具','status':'pending','args_summary':'仅用于外观可读性检查，不会批准或执行任何操作。','risk':'high','ttl_ms':60000,'created_at':'2026-10-03T00:00:00Z','session_id':'synthetic-scene'}]})
                await page.route('**/api/v1/approvals', approval_fixture)
            cdp=await context.new_cdp_session(page)
            await cdp.send('Performance.enable')
            for scene in (SCENES[:1] if representative else SCENES):
                await navigate(page,'settings')
                await page.locator(f'[data-scene-choice="{scene}"]').click()
                assert await page.locator(f'[data-scene-choice="{scene}"]').get_attribute('aria-pressed')=='true'
                assert await page.get_by_role('combobox',name='界面皮肤',exact=True).input_value()==scene
                await page.locator('[data-appearance-field="animationMode"]').select_option('normal')
                await page.wait_for_function('document.querySelector(".phtheme-backdrop").complete && document.querySelector(".phtheme-backdrop").naturalWidth>0')
                await page.screenshot(path=str(output / f'{scene}-settings.png'),full_page=True)
                await navigate(page,'chat');await page.locator('#compose').wait_for()
                if not representative:
                    await page.locator('.message').filter(has_text='合成回复').wait_for()
                    await page.locator('#messages').evaluate('(e)=>e.scrollTop=e.scrollHeight')
                await page.wait_for_function('pyharnessAppearance.stats().active')
                await page.screenshot(path=str(output / f'{scene}-chat.png'))
                metrics0={m['name']:m['value'] for m in (await cdp.send('Performance.getMetrics'))['metrics']}
                frames=[];pixel_hashes=[]
                for number in range(3):
                    start=await page.evaluate('pyharnessAppearance.stats().frames')
                    await page.wait_for_function('(n)=>pyharnessAppearance.stats().frames>=n+16',arg=start)
                    frame=output / f'{scene}-motion-{number}.png'
                    await page.screenshot(path=str(frame))
                    canvas=await page.evaluate('document.querySelector("canvas.phtheme-canvas").toDataURL()')
                    pixel_hashes.append(hashlib.sha256(canvas.encode()).hexdigest())
                    frames.append({'path':frame.name,'frame':await page.evaluate('pyharnessAppearance.stats().frames')})
                assert len(set(pixel_hashes))==3,'Scene canvas did not animate'
                stats=await page.evaluate('pyharnessAppearance.stats()')
                assert stats['pixelCount']<=2400000 and stats['particles']<=80
                metrics1={m['name']:m['value'] for m in (await cdp.send('Performance.getMetrics'))['metrics']}
                for width in (1280,390):
                    await page.set_viewport_size({'width':width,'height':900})
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    assert await page.locator('#messageText').is_visible()
                    await page.locator('#messageText').scroll_into_view_if_needed()
                    top=page.get_by_role('combobox',name='界面皮肤',exact=True)
                    await top.select_option('dark')
                    await top.select_option(scene)
                    assert await page.evaluate('pyharnessAppearance.config.themeId')==scene
                    assert await page.locator('#messageText').input_value()=='这是隔离预览草稿，不会发送给模型。'
                    await page.screenshot(path=str(output / f'{scene}-{width}.png'))
                await page.set_viewport_size({'width':1440,'height':900})
                await reload_page();await page.locator('#compose').wait_for()
                await page.wait_for_function('(s)=>window.pyharnessAppearance?.config.themeId===s',arg=scene)
                assert await page.locator('#messageText').input_value()=='这是隔离预览草稿，不会发送给模型。'
                if not representative:assert await page.evaluate('composer().attachments.length')==1
                for mode in ('low','off'):
                    await navigate(page,'settings')
                    await page.locator('[data-appearance-field="animationMode"]').select_option(mode)
                    snapshot=await page.evaluate('pyharnessAppearance.stats().frames')
                    # A finite observation window verifies that RAF has actually stopped.
                    await page.wait_for_timeout(180)
                    assert await page.evaluate('pyharnessAppearance.stats().frames')==snapshot
                    assert not await page.evaluate('pyharnessAppearance.stats().active')
                    await reload_page();await page.wait_for_function('(m)=>window.pyharnessAppearance?.config.animationMode===m',arg=mode)
                await page.locator('[data-appearance-field="animationMode"]').select_option('normal')
                await page.emulate_media(reduced_motion='reduce')
                await page.wait_for_function('window.pyharnessAppearance?.stats().reasons.includes("reduced")')
                assert not await page.evaluate('pyharnessAppearance.stats().active')
                await page.emulate_media(reduced_motion='no-preference')
                await page.evaluate('pyharnessAppearance.setHostHidden(true)')
                assert not await page.evaluate('pyharnessAppearance.stats().active')
                await page.evaluate('pyharnessAppearance.setHostHidden(false)')
                await page.wait_for_function('pyharnessAppearance.stats().active')
                # Exercise the document visibility handler with controlled browser
                # state. This is not a physical minimize/real-device acceptance.
                await page.evaluate('Object.defineProperty(document,"hidden",{configurable:true,get:()=>true});document.dispatchEvent(new Event("visibilitychange"))')
                assert 'hidden' in await page.evaluate('pyharnessAppearance.stats().reasons')
                snapshot=await page.evaluate('pyharnessAppearance.stats().frames')
                await page.wait_for_timeout(100)
                assert await page.evaluate('pyharnessAppearance.stats().frames')==snapshot
                await page.evaluate('delete document.hidden;document.dispatchEvent(new Event("visibilitychange"))')
                if not representative:
                    await navigate(page,'approvals')
                    await page.get_by_text('合成审批 · 不执行工具',exact=True).wait_for()
                    await page.screenshot(path=str(output / f'{scene}-approval-fixture.png'))
                await navigate(page,'chat');await page.locator('#compose').wait_for()
                records.append({'scene':scene,'stats':stats,'frames':frames,'canvas_sha256':pixel_hashes,
                    'renderer_task_seconds':metrics1['TaskDuration']-metrics0['TaskDuration'],
                    'sample_seconds':metrics1['Timestamp']-metrics0['Timestamp'],
                    'js_heap_bytes':metrics1['JSHeapUsedSize'],'widths':[1440,1280,390],
                    'draft_attachment_preserved':not representative,'modes_restart_reduced_host_pause':True})
                print('Scene passed: '+scene, flush=True)
            if not representative:
                # Repeated replacements must not leave old observers or RAF owners.
                heap=[]
                dom=[]
                for cycle in range(4):
                    for scene in SCENES:
                        await page.evaluate('(themeId)=>pyharnessAppearance.updateConfig({themeId})',scene)
                        assert await page.locator('.phtheme-layer').count()==1
                        assert await page.locator('canvas.phtheme-canvas').count()==1
                    await cdp.send('HeapProfiler.collectGarbage')
                    heap.append((await cdp.send('Runtime.getHeapUsage'))['usedSize'])
                    dom.append(await cdp.send('Memory.getDOMCounters'))
                assert heap[-1]-heap[0]<4_000_000,heap
                assert dom[-1]['jsEventListeners']<=dom[0]['jsEventListeners']+2,dom
                lifecycle=await page.evaluate('''async()=>{
                    const {createParticles}=await import('/assets/theme-kit/particles/engine.js');
                    const host=document.createElement('div');host.style.cssText='width:400px;height:200px';document.body.append(host);
                    const a=createParticles(host,{}),b=createParticles(host,{});a.destroy();
                    const c=createParticles(host,{});if(host.querySelectorAll('canvas').length!==1)throw Error('lost owner');
                    c.resume('stopped');c.destroy();b.destroy();host.remove();
                    return !c.stats().active&&c.stats().canvasCount===0;
                }''')
                assert lifecycle
                await page.route('**/scenes/starfield.png',lambda r:r.abort())
                await page.evaluate('pyharnessAppearance.updateConfig({themeId:"starfield"})')
                await reload_page();await page.locator('#compose').wait_for()
                await page.wait_for_function('document.querySelector(".phtheme-backdrop").hidden')
                assert await page.locator('#messageText').is_visible()
                await page.unroute('**/scenes/starfield.png')
                await page.route('**/particles/engine.js',lambda r:r.abort())
                await reload_page();await page.locator('#compose').wait_for()
                await page.wait_for_function('window.pyharnessAppearance?.stats().reasons.includes("static-fallback")')
                await page.unroute('**/particles/engine.js')
                await page.evaluate('window.disconnectStream?.()')
                await page.wait_for_timeout(100)
                await page.goto('about:blank')
                await context.close()
                # Each existing light/dark preference must survive the new scene catalogue.
                for old in ('light','dark','tech-blue-particle'):
                    context=await browser.new_context(extra_http_headers={'X-PyHarness-Token':app._api_token,'Connection':'close'})
                    await context.add_init_script('localStorage.setItem("pyharness.appearance.v1",JSON.stringify({themeId:'+json.dumps(old)+'}))')
                    page=await context.new_page();await page.goto(url);await page.wait_for_function('window.pyharnessAppearance')
                    assert await page.evaluate('pyharnessAppearance.config.themeId')==old
                    await page.goto('about:blank')
                    await context.close()
                records.append({'heap_after_gc':heap,'dom_counters':dom,'replacements':24,'disposed_owner_check':True,
                                'asset_engine_fallback':True,'legacy_preferences_preserved':True})
            assert not errors, errors
            assert not external,external
            await browser.close()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 30)
        finally:
            await app.service.shutdown()
            sock.close()
    result={'status':'passed','real_model_requests':0,'external_requests':external,
            'errors':errors,'records':records,'representative_only':representative,
            'frontend_sha256':source_snapshot}
    assert frontend_snapshot()==source_snapshot, 'Concurrent frontend edit; rerun against a stable snapshot'
    (output / 'scene-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--representative',action='store_true')
    args=parser.parse_args(); output=args.output_dir.resolve()
    if output==ROOT or output.is_relative_to(ROOT):parser.error('Output must be outside checkout')
    output.mkdir(parents=True,exist_ok=True)
    asyncio.run(main(output,args.representative))
