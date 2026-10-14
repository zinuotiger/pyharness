"""Settings right-pane browser acceptance; isolated data, no model calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def verify(output):
    from scripts.ci_verify import environment, install_network_boundary
    from scripts.platform_browser_smoke import find_browser
    executable = find_browser()
    if not executable:
        raise RuntimeError('Existing Chrome/Edge required')
    env = environment(output / 'isolated')
    os.environ.clear()
    os.environ.update(env)
    install_network_boundary()
    from tests.support import isolated_settings
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from playwright.async_api import async_playwright
    import uvicorn

    app = DesktopApp(cli.assemble_ctx(isolated_settings(output / 'data')))
    sid = await app.service.create_session()
    await app.service.platform.session_metadata(sid, {'title':'归档验收', 'archived':True})
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    base = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app.api, log_level='warning', timeout_graceful_shutdown=5))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    errors, dialogs = [], []
    try:
        while not server.started:
            if task.done():
                await task
            await asyncio.sleep(.02)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=executable, headless=True,
                args=['--disable-background-networking', '--no-first-run'])
            context = await browser.new_context(viewport={'width':1536, 'height':1024},
                extra_http_headers={'X-PyHarness-Token':app._api_token})
            page = await context.new_page()
            page.set_default_timeout(10000)
            page.on('pageerror', lambda e:errors.append(str(e)))
            async def reject_dialog(dialog):
                dialogs.append(dialog.type)
                await dialog.dismiss()
            page.on('dialog', reject_dialog)
            await page.goto(base + '/#account')
            await page.wait_for_function('window.pyharnessAppearance')
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'light'})")

            async def check_inline(selector):
                await page.locator('.settings-content ' + selector).wait_for()
                assert not await page.locator('#dialog').is_visible()
                assert len(context.pages) == 1
                assert await page.locator('.settings-nav').is_visible()

            async def nav(route):
                await page.locator(f'.settings-nav [data-nav="{route}"]').click()
                await page.wait_for_function('(route)=>state.page===route&&!document.querySelector(".skeleton")', arg=route)
                assert await page.locator(f'.settings-nav [data-nav="{route}"]').get_attribute('aria-current') == 'page'

            async def back():
                await page.locator('[data-action="panel-back"]').click()

            # Real account creation, refreshable routes and read/write/delete memory.
            await check_inline('#accountCreate')
            form = page.locator('#accountCreate')
            for name, value in [('username','settings-owner'),('password','synthetic-settings-password'),('tenant','settings-team')]:
                await form.locator(f'[name={name}]').fill(value)
            await form.locator('button').click()
            await form.locator('[role=status]').filter(has_text='账户已创建').wait_for()
            assert await form.locator('[name=password]').input_value() == ''
            await page.screenshot(path=str(output/'settings-account.png'), full_page=True)
            await nav('memory')
            await page.reload()
            await check_inline('#memoryForm')
            await page.locator('#memoryForm [name=key]').fill('settings_test')
            await page.locator('#memoryForm [name=op]').select_option('set')
            await page.locator('#memoryForm [name=value]').fill('页面内保存的团队记忆')
            await page.locator('#memoryForm button').click()
            await page.locator('#memoryForm [role=status]').filter(has_text='已写入').wait_for()
            await page.locator('#memoryForm [name=op]').select_option('get')
            await page.locator('#memoryForm button').click()
            await page.locator('#memoryForm [role=status]').filter(has_text='页面内保存').wait_for()
            await page.screenshot(path=str(output/'settings-memory.png'), full_page=True)
            await page.locator('#memoryForm [name=op]').select_option('delete')
            await page.locator('#memoryForm button').click()
            await page.locator('#memoryForm [role=status]').filter(has_text='已删除').wait_for()
            await nav('workflows')
            await page.get_by_role('button', name='创建工作流', exact=True).click()
            await check_inline('#workflowForm')
            await back()
            await page.get_by_role('button', name='创建工作流', exact=True).wait_for()

            # Model save/edit/vision/key replacement and confirmation cancellation.
            await nav('connections')
            await page.get_by_role('button', name='添加模型', exact=True).click()
            await check_inline('#modelForm')
            for name,value in [('label','Settings model'),('model','synthetic-model'),('base_url','http://127.0.0.1:1/v1'),('api_key','synthetic-settings-key')]:
                await page.locator('#model_'+name).fill(value)
            await page.get_by_role('button', name='保存', exact=True).click()
            await page.get_by_text('Settings model', exact=True).wait_for()
            profile = await page.evaluate("async()=> (await get('connections')).models.profiles.find(p=>p.label==='Settings model').id")
            await page.evaluate('id=>actions["model-edit"](id)', profile)
            await check_inline('#modelEditForm')
            await page.locator('#modelEditForm [name=label]').fill('Settings edited')
            await page.get_by_role('button', name='保存修改', exact=True).click()
            await page.get_by_text('Settings edited', exact=True).wait_for()
            await page.evaluate('id=>actions["model-vision"](id)', profile)
            await check_inline('#visionForm')
            await page.locator('#visionForm select').select_option('true')
            await page.locator('#visionForm button').click()
            await page.get_by_text('Settings edited', exact=True).wait_for()
            await page.evaluate('id=>actions["model-rotate"](id)', profile)
            await check_inline('#rotate')
            await back()
            # Switching categories cancels the pending destructive action.
            await page.evaluate('id=>{void actions["model-delete"](id)}', profile)
            await check_inline('#confirmProceed')
            await nav('memory')
            assert await page.evaluate('async id=>(await get("connections")).models.profiles.some(p=>p.id===id)', profile)

            # A late probe result cannot replace the user's newly selected page.
            await nav('connections')
            entered, release = asyncio.Event(), asyncio.Event()
            async def probe(route):
                entered.set()
                await release.wait()
                await route.fulfill(status=200, content_type='application/json', body='{"status":"available","probe":"synthetic result"}')
            await page.route('**/api/v1/connections/model:*/test', probe)
            await page.evaluate('id=>{void actions["model-test"](id)}', profile)
            await page.locator('#confirmProceed').click()
            await asyncio.wait_for(entered.wait(), 5)
            await check_inline('[role=status]')
            await nav('account')
            release.set()
            await page.wait_for_function('state.modelProbes.size===0')
            await check_inline('#accountCreate')
            assert not await page.get_by_text('synthetic result').is_visible()
            await page.unroute('**/api/v1/connections/model:*/test', probe)

            # Nested detail returns preserve the exact previous DOM and form state.
            await nav('agents')
            await page.get_by_role('button', name='创建 Agent', exact=True).click()
            await check_inline('#agentForm')
            await page.locator('#agentJson').fill('{"name":"Settings Agent"}')
            await page.evaluate("modal('额外详情','<p>嵌套详情</p>')")
            await back()
            assert await page.locator('#agentJson').input_value() == '{"name":"Settings Agent"}'
            await page.get_by_role('button', name='保存草稿', exact=True).click()
            await page.get_by_role('heading', name='Settings Agent', exact=True).wait_for()
            await nav('schedules')
            await page.get_by_role('button', name='创建计划任务', exact=True).click()
            await check_inline('#scheduleForm')
            await page.locator('#scheduleName').fill('settings_schedule')
            await page.locator('#scheduleExpr').fill('86400')
            await page.locator('#scheduleIntent').fill('synthetic pending schedule')
            await page.locator('#scheduleForm [type=submit]').click()
            row = page.locator('tr').filter(has_text='settings_schedule')
            await row.wait_for()
            await row.get_by_role('button', name='编辑', exact=True).click()
            await check_inline('#editSchedule')
            await back()
            await row.get_by_role('button', name='删除', exact=True).click()
            await check_inline('#confirmProceed')
            await page.locator('#confirmCancel').click()
            await row.wait_for()
            await row.get_by_role('button', name='删除', exact=True).click()
            await page.locator('#confirmProceed').click()
            await page.get_by_text('暂无计划任务', exact=True).wait_for()
            await nav('knowledge')
            await page.get_by_role('button', name='切换 Provider', exact=True).click()
            await check_inline('#providerForm')
            await page.locator('#providerForm button').click()
            await page.locator('#knowledgeQuery').wait_for()
            await nav('usage')
            await page.get_by_role('button', name='筛选统计', exact=True).click()
            await check_inline('#usageFilterForm')
            await page.locator('#usageFilterForm [name=model]').fill('synthetic-model')
            await page.get_by_role('button', name='应用筛选', exact=True).click()
            await page.get_by_role('button', name='筛选统计', exact=True).click()
            assert await page.locator('#usageFilterForm [name=model]').input_value() == 'synthetic-model'
            await page.locator('#clearUsageFilter').click()
            await nav('archives')
            await page.get_by_role('button', name='重命名', exact=True).click()
            await check_inline('#sessionRenameForm')
            await back()

            for width in (1536, 390):
                await page.set_viewport_size({'width':width, 'height':900})
                await nav('account')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                await page.screenshot(path=str(output/f'settings-account-{width}.png'), full_page=True)
                await nav('memory')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                await page.screenshot(path=str(output/f'settings-memory-{width}.png'), full_page=True)
            await page.set_viewport_size({'width':1536, 'height':1024})
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'dark'})")
            await page.screenshot(path=str(output/'settings-memory-dark.png'), full_page=True)
            assert not errors, errors
            assert not dialogs, dialogs
            await context.close()
            await browser.close()
        (output/'summary.json').write_text(json.dumps({'status':'passed','errors':errors,'native_dialogs':dialogs,
            'checks':['account create','memory CRUD','route reload','workflow page','model CRUD editors',
                'confirmation cancel/navigation','late probe fence','nested back preserves form','Agent draft',
                'schedule CRUD','provider/filter forms','archive rename','desktop/mobile/dark'],
            'external_model_requests':0}, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        await app.service.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.is_relative_to(ROOT):
        parser.error('Use an output directory outside checkout')
    output.mkdir(parents=True, exist_ok=True)
    asyncio.run(asyncio.wait_for(verify(output), 150))
    print('Settings right-pane browser acceptance passed')
