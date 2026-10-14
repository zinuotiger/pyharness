"""Real paged UI and renderer failure acceptance; isolated synthetic data only."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def verify(output):
    from scripts.platform_browser_smoke import find_browser
    from scripts.platform_scene_smoke import frontend_snapshot
    from scripts.ci_verify import environment,install_network_boundary
    executable=find_browser();assert executable
    snapshot=frontend_snapshot()
    env=environment(output/'isolated')
    os.environ.clear();os.environ.update(env)
    install_network_boundary()
    from tests.support import isolated_settings
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from playwright.async_api import async_playwright
    import uvicorn
    app=DesktopApp(cli.assemble_ctx(isolated_settings(output/'data')))
    p=app.service.platform
    sid=await app.service.create_session();log=await app.service.require_session(sid)
    for i in range(105):
        await log.append('task.enqueued',dict(task_id=f'fixture-{i}',pos=1),actor='system')
        await log.append('task.completed',dict(task_id=f'fixture-{i}',reason='ok'),actor='system')
        await p.upload(dict(session_id=sid,filename=f'fixture-{i}.txt',data_base64='eA=='))
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    base=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(app.api,log_level='critical',timeout_graceful_shutdown=3))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    errors=[];pages=[];devices=[]
    try:
        while not server.started:
            if task.done():await task
            await asyncio.sleep(.02)
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path=executable,headless=True,args=['--disable-background-networking','--no-first-run'])
            context=await browser.new_context(viewport={'width':1440,'height':900},extra_http_headers={'X-PyHarness-Token':app._api_token})
            page=await context.new_page();page.set_default_timeout(10000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto(base);await page.locator('#compose').wait_for()
            for kind,action,label in [('runs','run-detail','任务'),('artifacts','artifact-preview','产物')]:
                await page.evaluate('kind=>go(kind)',kind)
                pager=page.get_by_role('navigation',name=label+'分页')
                seen=[]
                for number,count in [(1,50),(2,50),(3,5)]:
                    await pager.get_by_text(f'第 {number} 页 · 共 105 条',exact=True).wait_for()
                    rows=page.locator(f'main [data-action="{action}"]')
                    assert await rows.count()==count
                    seen.extend(await rows.evaluate_all('(rows)=>rows.map(r=>r.dataset.id)'))
                    pages.append(dict(kind=kind,page=number,rows=count))
                    await page.screenshot(path=str(output/f'{kind}-page-{number}.png'),full_page=True)
                    if number<3:await pager.get_by_role('button',name='下一页',exact=True).click()
                assert len(seen)==len(set(seen))==105
                assert await pager.get_by_role('button',name='下一页',exact=True).is_disabled()
                await pager.get_by_role('button',name='上一页',exact=True).click()
                await pager.get_by_text('第 2 页 · 共 105 条',exact=True).wait_for()
                assert await page.locator(f'main [data-action="{action}"]').count()==50
                # A failed real page load retains the position and offers retry.
                async def fail_once(route):
                    await route.fulfill(status=503,json={'code':'synthetic_load_failure','message':'synthetic load failure'})
                pattern=base+'/api/v1/'+kind+'?*'
                await page.route(pattern,fail_once)
                await pager.get_by_role('button',name='下一页',exact=True).click()
                await page.get_by_text('加载失败',exact=True).wait_for()
                await page.unroute(pattern,fail_once)
                await page.get_by_role('button',name='重试',exact=True).click()
                await pager.get_by_text('第 3 页 · 共 105 条',exact=True).wait_for()
                await pager.get_by_role('button',name='首页',exact=True).click()
                await pager.get_by_text('第 1 页 · 共 105 条',exact=True).wait_for()
                await getattr(p,kind)(limit=50)
                assert p.list_index.metrics['projection_count']==0
            await context.close()
            # Controlled navigator profiles are measurements, not physical-device claims.
            for profile,cores,memory in [('standard',8,8),('constrained',2,2)]:
                context=await browser.new_context(viewport={'width':1920,'height':1080},device_scale_factor=2,extra_http_headers={'X-PyHarness-Token':app._api_token})
                await context.add_init_script(f'Object.defineProperty(navigator,"hardwareConcurrency",{{get:()=>{cores}}});Object.defineProperty(navigator,"deviceMemory",{{get:()=>{memory}}});')
                page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base);await page.locator('#compose').wait_for();await page.wait_for_function('window.pyharnessAppearance')
                await page.evaluate('pyharnessAppearance.updateConfig({themeId:"starfield",count:80,pixelRatio:2})')
                cdp=await context.new_cdp_session(page);await cdp.send('Performance.enable')
                start={m['name']:m['value'] for m in (await cdp.send('Performance.getMetrics'))['metrics']}
                frames=await page.evaluate('pyharnessAppearance.stats().frames')
                await page.wait_for_timeout(2200)
                stats=await page.evaluate('pyharnessAppearance.stats()')
                end={m['name']:m['value'] for m in (await cdp.send('Performance.getMetrics'))['metrics']}
                assert stats['deviceProfile']==profile and stats['pixelCount']<=stats['pixelLimit']
                assert stats['particles']==(24 if profile=='constrained' else 80)
                devices.append(dict(profile=profile,stats=stats,observed_frames=stats['frames']-frames,task_seconds=end['TaskDuration']-start['TaskDuration'],sample_seconds=end['Timestamp']-start['Timestamp'],heap_bytes=end['JSHeapUsedSize']))
                # Exercise both context loss and actual drawing/resize/clear exceptions.
                for fault in ('contextlost','draw','resize','clear'):
                    result=await page.evaluate('''async fault=>{
                        const {createParticles}=await import('/assets/theme-kit/particles/engine.js');
                        const host=document.createElement('div');host.style.cssText='width:300px;height:200px';document.body.append(host);
                        const effect=createParticles(host,{themeId:'starfield'}),canvas=host.querySelector('canvas'),ctx=canvas.getContext('2d');
                        if(fault==='contextlost')canvas.dispatchEvent(new Event('contextlost',{cancelable:true}));
                        else if(fault==='resize'){ctx.setTransform=()=>{throw Error('synthetic graphics fault')};effect.resize();}
                        else {ctx.clearRect=()=>{throw Error('synthetic graphics fault')};if(fault==='clear')effect.updateConfig({themeId:'ocean'});else effect.resume('stopped');}
                        await new Promise(resolve=>setTimeout(resolve,100));const stats=effect.stats();effect.stop();effect.destroy();host.remove();return stats;
                    }''',fault)
                    assert not result['active'] and 'render-failure' in result['reasons'],(fault,result)
                    await page.locator('#messageText').fill('graphics failure keeps input usable')
                    assert await page.locator('.phtheme-backdrop').is_visible()
                await context.close()
            await browser.close()
        assert not errors,errors
        assert frontend_snapshot()==snapshot
        (output/'summary.json').write_text(json.dumps(dict(status='passed',pages=pages,devices=devices,renderer_failures=['contextlost','draw','resize','clear'],errors=errors,frontend_sha256=snapshot,real_model_requests=0),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        server.should_exit=True
        try:await asyncio.wait_for(task,15)
        finally:await app.service.shutdown();sock.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    output=parser.parse_args().output_dir.resolve()
    if output.is_relative_to(ROOT):parser.error('output must be outside checkout')
    output.mkdir(parents=True,exist_ok=True)
    asyncio.run(verify(output))
