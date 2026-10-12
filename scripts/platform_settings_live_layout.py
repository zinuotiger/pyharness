"""Read-only layout checks against a running local PyHarness preview.

Uses its existing bootstrap credential without logging it. No model probes,
schedule creations or other writes are allowed in this browser context.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def verify(port, data, output):
    from playwright.async_api import async_playwright
    from scripts.platform_browser_smoke import find_browser

    token = (data / 'web.token').read_text(encoding='utf-8').strip()
    base = f'http://127.0.0.1:{port}'
    checks, screenshots, writes, errors = [], [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=find_browser(), headless=True)
        context = await browser.new_context(viewport={'width':1366,'height':768})

        async def readonly(route):
            if route.request.method not in ['GET','HEAD','OPTIONS']:
                writes.append(route.request.method)
                await route.abort()
            else:
                await route.continue_()
        await context.route('**/*', readonly)
        page = await context.new_page()
        page.on('pageerror', lambda error:errors.append(str(error)))
        await page.goto(base+'/?token='+token+'#connections')
        await page.get_by_role('button',name='添加模型',exact=True).wait_for()

        async def measure(name):
            result = await page.evaluate('''()=>{
              const elements=[document.scrollingElement,...document.querySelectorAll('.header,.sidebar,.main,.settings-layout,.settings-nav-panel,.settings-nav,.settings-content,form')];
              const targets=[...document.querySelectorAll('.settings-nav button,.model-editor-heading,.model-field,.schedule-editor .field,.model-editor-actions button')];
              return {viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,
                metrics:elements.map(e=>({element:e.id||e.className||e.tagName,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth})),
                outside:targets.filter(e=>{const r=e.getBoundingClientRect();return r.bottom>innerHeight+1||r.right>innerWidth+1||r.left<0||r.top<0}).map(e=>e.className),
                scrollbars:[...document.querySelectorAll('.settings-layout,.settings-layout *')].filter(e=>{const s=getComputedStyle(e);return (['auto','scroll'].includes(s.overflowY)&&e.scrollHeight>e.clientHeight+1)||(['auto','scroll'].includes(s.overflowX)&&e.scrollWidth>e.clientWidth+1)}).map(e=>e.className)};
            }''')
            assert await page.locator('.settings-nav button:visible').count()==14
            assert await page.locator('.settings-nav-footer').count()==0
            assert not result['outside'] and not result['scrollbars'], result
            assert all(m['scrollHeight']<=m['clientHeight']+1 and m['scrollWidth']<=m['clientWidth']+1 for m in result['metrics']), result
            checks.append({'name':name,**result})
            path=output/(name+'.png')
            await page.screenshot(path=str(path))
            screenshots.append(str(path))

        await page.locator('#workbenchEntry').click()
        await page.locator('.dashboard-page').wait_for()
        for width,height in [(1366,768),(1440,900),(1920,1080),(1280,720)]:
            await page.set_viewport_size({'width':width,'height':height})
            result=await page.evaluate('''()=>{
              const root=document.scrollingElement;
              const elements=[root,document.querySelector('.main'),document.querySelector('.dashboard-page')];
              return {viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,metrics:elements.map(e=>({element:e.id||e.className||e.tagName,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth})),
                outside:[...document.querySelectorAll('.dashboard-page .card,.dashboard-page .action,.dashboard-quickbar button')].filter(e=>{const r=e.getBoundingClientRect();return r.bottom>innerHeight+1||r.right>innerWidth+1||r.left<0||r.top<0}).map(e=>e.className)};
            }''')
            assert result['metrics'][0]['scrollHeight']<=height+1 and result['metrics'][0]['scrollWidth']<=width+1 and not result['outside'],result
            name=f'live-{port}-dashboard-{width}x{height}'
            checks.append({'name':name,**result})
            path=output/(name+'.png');await page.screenshot(path=str(path));screenshots.append(str(path))
        await page.set_viewport_size({'width':1366,'height':768})
        await page.locator('#settingsEntry').click()
        await page.locator('.settings-nav [data-nav=connections]').click()
        await page.get_by_role('button',name='添加模型',exact=True).wait_for()
        await page.get_by_role('button',name='添加模型',exact=True).click()
        for width,height in [(1366,768),(1440,900),(1920,1080)]:
            await page.set_viewport_size({'width':width,'height':height})
            await measure(f'live-{port}-add-{width}x{height}')
        await page.set_viewport_size({'width':1366,'height':768})
        await page.get_by_role('button',name='取消',exact=True).click()
        profiles=await page.evaluate('async()=>(await api("/api/settings/models")).profiles.map(p=>p.id)')
        if profiles:
            await page.evaluate('id=>actions["model-edit"](id)',profiles[0])
            assert await page.locator('#model_api_key').input_value()==''
            await measure(f'live-{port}-edit-1366x768')
        await page.locator('#workbenchEntry').click()
        await page.locator('.actions [data-action=schedule-new]').click()
        await page.locator('#scheduleForm').wait_for()
        assert not await page.locator('#dialog').is_visible()
        assert await page.locator('.settings-nav [data-nav=schedules]').get_attribute('aria-current')=='page'
        for width,height in [(1366,768),(1440,900),(1920,1080)]:
            await page.set_viewport_size({'width':width,'height':height})
            await measure(f'live-{port}-schedule-{width}x{height}')
        await page.reload()
        await page.locator('#scheduleForm').wait_for()
        assert not writes and not errors, {'writes':writes,'errors':errors}
        await browser.close()
    result={'status':'passed','port':port,'checks':checks,'screenshots':screenshots,
            'write_requests':len(writes),'page_errors':errors,'existing_profile_edit_checked':bool(profiles)}
    (output/f'live-{port}-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    output=args.output_dir.resolve()
    if output.is_relative_to(ROOT):parser.error('Use a runtime directory outside checkout')
    output.mkdir(parents=True,exist_ok=True)
    result=asyncio.run(verify(args.port,args.data_dir,output))
    print(json.dumps({'status':result['status'],'port':args.port,'layouts':len(result['checks']),'writes':result['write_requests']}))
