"""Browser acceptance for sidebar pins and workbench layout, without model calls.

Uses isolated synthetic sessions and an installed browser. Runtime data and
screenshots must be written outside the checkout.
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
from scripts.platform_browser_smoke import find_browser
from scripts.ci_verify import environment, install_network_boundary


async def verify(output):
    executable = find_browser()
    if not executable:
        raise RuntimeError('Existing Chrome/Edge required')
    env = environment(output / 'isolated')
    os.environ.clear()
    os.environ.update(env)
    install_network_boundary()
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from tests.support import isolated_settings
    from playwright.async_api import async_playwright
    import uvicorn

    app = DesktopApp(cli.assemble_ctx(isolated_settings(output / 'data')))
    service = app.service.platform
    first = await app.service.create_session()
    second = await app.service.create_session()
    third = await app.service.create_session()
    for sid, title in [(first, '布局验收：项目分析'), (second, '布局验收：资料整理'), (third, '布局验收：计划讨论')]:
        await service.session_metadata(sid, {'title': title})
    await service.session_metadata(first, {'pinned': True})
    long_title = '长会话标题' + '无空格标题' * 20
    await service.session_metadata(third, {'title': long_title})
    log = await app.service.require_session(first)
    long_task = '长任务名称' + 'longunbrokentaskname' * 6
    task_id = 'layout-long-task'
    await log.append('user.message', {'content':long_task, 'meta':{'platform':{'task_id':task_id,'name':long_task,'definition':service.agents()['templates'][0]}}}, actor='user')
    await log.append('task.enqueued', {'task_id':task_id,'pos':1}, actor='system')
    await log.append('task.started', {'task_id':task_id}, actor='system')
    await log.append('task.completed', {'task_id':task_id,'reason':'synthetic layout fixture'}, actor='system')
    content_log = await app.service.require_session(second)
    await content_log.append('user.message', {'content':'cranberrysearchneedle ' + 'LongBodyWithoutSpaces' * 60}, actor='user')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app.api, log_level='warning', timeout_graceful_shutdown=5))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    errors, external = [], []
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(.02)
        assert server.started
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=executable, headless=True,
                args=['--disable-background-networking', '--no-first-run'])
            context = await browser.new_context(viewport={'width':1536, 'height':1024},
                extra_http_headers={'X-PyHarness-Token':app._api_token})
            async def boundary(route):
                if route.request.url.startswith(url + '/'):
                    await route.continue_()
                else:
                    external.append(route.request.url.split('?')[0])
                    await route.abort()
            await context.route('**/*', boundary)
            page = await context.new_page()
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.set_default_timeout(15000)
            await page.goto(url)
            await page.locator('#compose').wait_for()
            await page.wait_for_function('window.pyharnessAppearance')
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'light'})")
            assert await page.locator('.sidebar .workspace').count() == 0
            assert await page.locator('#sessionList [data-session]').count() == 3
            assert await page.locator(f'#pinnedSessions [data-session="{first}"]').count() == 1

            async def menu(sid, name):
                row = page.locator(f'#sessionList .session-list-item:has([data-session="{sid}"])')
                await row.hover()
                await row.locator('[data-action="session-menu"]').click()
                await page.get_by_role('menuitem', name=name, exact=True).click()

            assert await page.locator('#sessionSearch').count() == 0
            await menu(first, '取消置顶')
            await page.locator('#pinnedSessions').wait_for(state='detached')
            await page.reload()
            await page.locator('#compose').wait_for()
            assert await page.locator('#pinnedSessions').count() == 0
            await menu(first, '置顶')
            await page.locator(f'#pinnedSessions [data-session="{first}"]').wait_for()

            # Pin/unpin through the actual menu and metadata API, with no duplicates.
            await menu(second, '置顶')
            await page.locator(f'#pinnedSessions [data-session="{second}"]').wait_for()
            assert await page.locator(f'#sessionList [data-session="{second}"]').count() == 1
            await page.reload()
            await page.locator(f'#pinnedSessions [data-session="{second}"]').wait_for()
            await menu(second, '取消置顶')
            await page.wait_for_function('(sid)=>!document.querySelector(`#pinnedSessions [data-session="${sid}"]`)', arg=second)
            assert await page.locator(f'#sessionList [data-session="{second}"]').count() == 1

            # Collapsing survives navigation. Top search finds titles and content.
            await page.locator('#pinnedSessions summary').click()
            assert not await page.locator(f'#pinnedSessions [data-session="{first}"]').is_visible()
            await page.locator('#workbenchEntry').click()
            await page.locator('.dashboard-page').wait_for()
            assert await page.locator('#pinnedSessions').get_attribute('open') is None
            await page.keyboard.press('Control+k')
            assert await page.locator('#globalSearch').evaluate('(e)=>e===document.activeElement')
            await page.locator('#globalSearch').fill('项目分析')
            await page.locator(f'#globalSearchResults [data-session="{first}"]').wait_for()
            await page.screenshot(path=str(output/'search-pinned-title.png'), full_page=True)
            await page.locator(f'#globalSearchResults [data-session="{first}"]').click()
            await page.locator('#compose').wait_for()
            assert await page.evaluate('state.sid') == first
            assert not await page.locator('#globalSearchResults').is_visible()
            assert await page.locator('#pinnedSessions').get_attribute('open') is None
            await page.locator('#globalSearch').fill('cranberrysearchneedle')
            await page.locator('#globalSearch').press('Enter')
            await page.locator(f'#globalSearchResults [data-session="{second}"]').wait_for()
            await page.locator('#globalSearch').press('ArrowDown')
            await page.keyboard.press('Enter')
            await page.wait_for_function('(sid)=>state.sid===sid&&!document.querySelector(".skeleton")', arg=second)
            assert await page.locator('.messages').get_by_text('cranberrysearchneedle', exact=False).is_visible()
            await page.locator('#globalSearch').fill('不会存在的会话')
            await page.locator('#globalSearchResults').get_by_text('没有匹配的会话', exact=True).wait_for()
            await page.locator('#globalSearch').press('Escape')
            assert not await page.locator('#globalSearchResults').is_visible()
            await page.locator('#pinnedSessions summary').click()

            # Archive removes the pin from the active sidebar; restoring retains it.
            await menu(first, '归档')
            await page.locator(f'#sessionList [data-session="{first}"]').wait_for(state='detached')
            assert await page.locator('#pinnedSessions').count() == 0
            await page.locator('#globalSearch').fill('项目分析')
            archived_result = page.locator(f'#globalSearchResults [data-session="{first}"]')
            await archived_result.wait_for()
            assert '已归档' in await archived_result.inner_text()
            await page.locator('#globalSearch').press('Escape')
            await page.locator('#sessionArchiveToggle').click()
            await page.locator(f'#sessionList [data-session="{first}"]').wait_for()
            assert await page.locator('#pinnedSessions').count() == 0
            await menu(first, '取消归档')
            await page.locator(f'#sessionList [data-session="{first}"]').wait_for(state='detached')
            await page.locator('#sessionArchiveToggle').click()
            await page.locator(f'#pinnedSessions [data-session="{first}"]').wait_for()

            # Rename is reflected in the sidebar and real dashboard projection.
            await menu(first, '重命名')
            await page.locator('#sessionRenameForm [name="title"]').fill('布局验收：已重命名')
            await page.locator('#sessionRenameForm button[type="submit"]').click()
            await page.locator('#pinnedSessions .session-title').filter(has_text='已重命名').wait_for()
            await page.locator('#workbenchEntry').click()
            await page.locator('.dashboard-page .recent').get_by_text('布局验收：已重命名', exact=True).wait_for()
            assert await page.locator('.dashboard-page .greeting .stat').count() == 4
            assert await page.locator('.dashboard-page .actions>.action').count() == 4
            assert await page.locator('.dashboard-page .bottom-grid>.card').count() == 4
            assert await page.locator('#workbenchEntry').get_attribute('aria-current') == 'page'
            await page.evaluate('document.fonts.ready')
            await page.locator('#toast').wait_for(state='hidden')
            await page.screenshot(path=str(output / 'workbench-desktop.png'), full_page=True)
            layouts=[]
            for width,height in [(1366,768),(1440,900),(1920,1080),(1280,720)]:
                await page.set_viewport_size({'width':width,'height':height})
                result=await page.evaluate('''()=>{
                  const root=document.scrollingElement;
                  const elements=[root,document.querySelector('.main'),document.querySelector('.dashboard-page')];
                  return {viewport:[innerWidth,innerHeight],metrics:elements.map(e=>({element:e.id||e.className||e.tagName,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth})),
                    outside:[...document.querySelectorAll('.dashboard-page .card,.dashboard-page .action,.dashboard-quickbar button')].filter(e=>{const r=e.getBoundingClientRect();return r.bottom>innerHeight+1||r.right>innerWidth+1||r.left<0||r.top<0}).map(e=>e.className)};
                }''')
                assert not result['outside'],result
                assert all(m['scrollWidth']<=m['clientWidth']+1 for m in result['metrics']),result
                assert result['metrics'][0]['scrollHeight']<=height+1,result
                assert await page.locator('.recent>.row').count()<=2
                assert await page.locator('.task-card>.row').count()<=2
                layouts.append(result)
                await page.screenshot(path=str(output/f'dashboard-fit-{width}x{height}.png'))
            (output/'layout-metrics.json').write_text(json.dumps(layouts,ensure_ascii=False,indent=2),encoding='utf-8')
            await page.set_viewport_size({'width':1536,'height':1024})
            task_title = page.locator('.task-state strong').first
            assert await task_title.get_attribute('title')
            assert await task_title.evaluate('(e)=>e.scrollWidth>e.clientWidth')
            for width, height in [(1280,900), (390,844)]:
                await page.set_viewport_size({'width':width, 'height':height})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                await page.screenshot(path=str(output / f'workbench-{width}.png'), full_page=True)
                if width == 390:
                    await page.locator('.mobile-sessions').click()
                    assert await page.locator('#pinnedSessions summary').is_visible()
                    assert await page.locator('#settingsEntry').is_visible()
                    await page.screenshot(path=str(output / 'sidebar-mobile.png'))
                    await page.locator('#settingsEntry').click()
                    await page.locator('.settings-nav').wait_for()
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            await page.set_viewport_size({'width':1536, 'height':1024})
            await page.locator('#workbenchEntry').click()
            await page.locator('.dashboard-page').wait_for()
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'dark'})")
            await page.screenshot(path=str(output / 'workbench-dark.png'), full_page=True)
            await page.locator('#sessionListHeading').click()
            await page.locator('#compose').wait_for()
            await page.locator('#messageText').fill('布局切换保留草稿')
            await page.locator('#workbenchEntry').click()
            await page.locator('.dashboard-page').wait_for()
            await page.locator('#sessionListHeading').click()
            await page.locator('#compose').wait_for()
            assert await page.locator('#messageText').input_value() == '布局切换保留草稿'

            # Zoom changes the CSS viewport; do not hide document overflow.
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'light'})")
            for width, height, name in [(1536,1024,'desktop'),(1280,900,'laptop'),(1024,768,'zoom-125'),(768,640,'zoom-200'),(390,844,'mobile'),(320,640,'small-mobile')]:
                await page.set_viewport_size({'width':width,'height':height})
                sidebar_style = None
                for route in ('dashboard','chat','settings','runs','connections','knowledge','schedules','usage'):
                    await page.evaluate('(route)=>go(route)', route)
                    await page.wait_for_function('(route)=>state.page===route&&!document.querySelector(".skeleton")', arg=route)
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), (width,route)
                    if width > 750:
                        style = await page.locator('.sidebar').evaluate('(e)=>{const s=getComputedStyle(e);return [s.left,s.top,s.bottom,s.width,s.borderRadius,s.backgroundColor]}')
                        assert style[0]=='0px' and style[2]=='0px' and style[4]=='0px', style
                        if sidebar_style is None: sidebar_style=style
                        else: assert style==sidebar_style, (route,style,sidebar_style)
                    if route in ('dashboard','chat','settings'):
                        await page.screenshot(path=str(output/f'{route}-{name}.png'),full_page=True)
                # The relocated search works with a tiny header input as well.
                await page.locator('#globalSearch').fill('资料整理')
                await page.locator(f'#globalSearchResults [data-session="{second}"]').wait_for()
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                await page.locator(f'#globalSearchResults [data-session="{second}"]').click()
                await page.wait_for_function('(sid)=>state.sid===sid&&!document.querySelector(".skeleton")', arg=second)
            # A delayed result for an old query must not reappear after clearing.
            entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
            async def delayed_history(route):
                entered.set()
                await release.wait()
                await route.fulfill(status=200,content_type='application/json',body=json.dumps({'hits':[{'session_id':first,'snippet':'obsolete result'}]}))
                finished.set()
            await page.route('**/api/v1/history/search?*',delayed_history)
            await page.locator('#globalSearch').fill('obsolete')
            await page.locator('#globalSearch').press('Enter')
            await asyncio.wait_for(entered.wait(),5)
            await page.locator('#globalSearch').fill('')
            release.set()
            await asyncio.wait_for(finished.wait(),5)
            await page.evaluate('new Promise(requestAnimationFrame)')
            await page.unroute('**/api/v1/history/search?*',delayed_history)
            assert not await page.locator('#globalSearchResults').is_visible()
            # Escape from a keyboard-focused result closes without reopening.
            await page.locator('#globalSearch').fill('资料整理')
            await page.locator(f'#globalSearchResults [data-session="{second}"]').wait_for()
            await page.locator('#globalSearch').press('ArrowDown')
            await page.keyboard.press('Escape')
            assert await page.locator('#globalSearch').evaluate('(e)=>e===document.activeElement')
            assert not await page.locator('#globalSearchResults').is_visible()
            assert not errors, errors
            assert not external, external
            assert not app.service._engines and not app.service._queues
            await context.close()
            await browser.close()
        (output / 'summary.json').write_text(json.dumps({'status':'passed',
            'checks':['pin/unpin menu','reload persistence','no duplicates','collapse','search',
                'empty pins hidden','archive/restore','rename projection','dashboard cards','long task ellipsis',
                'title/content/archived search','keyboard search','stale search fence','consistent sidebar',
                'desktop/mobile/zoom CSS viewports','dark theme','draft preservation'],
            'synthetic_sessions':True,'model_calls':0,'errors':errors}, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        await app.service.shutdown()
        server.should_exit = True
        await asyncio.wait_for(task, 15)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.is_relative_to(ROOT):
        raise SystemExit('Use an output directory outside the checkout')
    output.mkdir(parents=True, exist_ok=True)
    asyncio.run(verify(output))
    print('Workbench browser acceptance passed')
