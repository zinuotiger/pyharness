"""Model editor browser acceptance with synthetic credentials and loopback HTTP only."""
from __future__ import annotations
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
    os.environ.clear(); os.environ.update(env)
    install_network_boundary()
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from playwright.async_api import async_playwright
    import uvicorn
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from tests.support import isolated_settings

    app = DesktopApp(cli.assemble_ctx(isolated_settings(output / 'data')))
    provider = FastAPI()
    provider_mode = {'value': 'ok'}
    provider_requests = []

    @provider.post('/v1/chat/completions')
    async def completion(body: dict):
        assert body['model'] == 'editor-synthetic'
        assert body.get('max_tokens', body.get('max_completion_tokens')) == 64
        assert body['temperature'] == 0 and not body.get('tools')
        provider_requests.append({'model': body['model'], 'mode': provider_mode['value']})
        if provider_mode['value'] == 'auth':
            return JSONResponse({'error': {'message': 'synthetic rejected', 'type': 'authentication_error'}}, status_code=401)
        return {'id':'editor-probe','object':'chat.completion','created':0,'model':body['model'],
                'choices':[{'index':0,'message':{'role':'assistant','content':'PYHARNESS_MODEL_OK'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}

    sockets, servers, tasks = [], [], []
    for api in (app.api, provider):
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sockets.append(sock)
        server = uvicorn.Server(uvicorn.Config(api, log_level='critical', access_log=False, timeout_graceful_shutdown=3))
        servers.append(server); tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
    base = f'http://127.0.0.1:{sockets[0].getsockname()[1]}'
    endpoint = f'http://127.0.0.1:{sockets[1].getsockname()[1]}/v1'
    errors, checks, screenshots, save_requests, layouts = [], [], [], [], []
    try:
        while not all(server.started for server in servers):
            for task in tasks:
                if task.done(): await task
            await asyncio.sleep(.02)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=executable, headless=True,
                args=['--disable-background-networking','--no-first-run'])
            context = await browser.new_context(viewport={'width':1366,'height':768})
            page = await context.new_page(); page.set_default_timeout(10000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            def record(request):
                if request.method == 'POST' and request.url == base + '/api/settings/models':
                    payload = request.post_data_json
                    save_requests.append({'id':payload.get('id'), 'has_key':bool(payload.get('api_key'))})
            page.on('request', record)
            await page.goto(app.bootstrap_url('127.0.0.1', sockets[0].getsockname()[1])+'#connections')
            await page.get_by_role('button',name='添加模型',exact=True).wait_for()
            await page.wait_for_function('window.pyharnessAppearance')
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'light'})")

            async def profiles():
                return await page.evaluate('async()=> (await api("/api/settings/models"))')

            async def new(label='模型连接设计验收'):
                await page.get_by_role('button',name='添加模型',exact=True).click()
                for field,value in [('label',label),('model','editor-synthetic'),('base_url',endpoint),('api_key','synthetic-editor-key')]:
                    await page.locator('#model_'+field).fill(value)

            async def fits(name, shot=True):
                result = await page.evaluate('''()=>{
                    const root=document.scrollingElement;
                    const fields=[...document.querySelectorAll('.header, .sidebar, .settings-nav-heading, .settings-nav button, .model-editor-form .model-field, .schedule-editor .field, .schedule-editor .warning, .schedule-editor-feedback, .model-editor-actions button, .model-editor-feedback, .model-editor-heading')].filter(e=>e.getClientRects().length);
                    const metrics=[root,...document.querySelectorAll('.header,.sidebar,.main,.settings-layout,.settings-nav-panel,.settings-nav,.settings-content,.model-editor-form,.schedule-editor,form')].map(e=>({element:e.id||e.className||e.tagName,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth,overflowY:getComputedStyle(e).overflowY,overflowX:getComputedStyle(e).overflowX}));
                    return {metrics,dpr:devicePixelRatio,height:root.scrollHeight,viewport:innerHeight,width:root.scrollWidth,viewportWidth:innerWidth,
                      outside:fields.filter(e=>{const r=e.getBoundingClientRect();return r.top<0||r.bottom>innerHeight+1||r.right>innerWidth+1||r.left<0}).map(e=>e.className),
                      scrollbars:[...document.querySelectorAll('.settings-layout, .settings-layout *')].filter(e=>{const s=getComputedStyle(e);return (['auto','scroll'].includes(s.overflowY)&&e.scrollHeight>e.clientHeight+1)||(['auto','scroll'].includes(s.overflowX)&&e.scrollWidth>e.clientWidth+1)}).map(e=>e.className)};
                }''')
                layouts.append({'name':name,**result})
                assert await page.locator('.settings-nav button:visible').count()==14
                assert await page.locator('.settings-nav-footer').count()==0
                assert await page.locator('.settings-nav [data-nav=sandboxes]').is_visible()
                assert result['height'] <= result['viewport']+1, (name,result)
                assert result['width'] <= result['viewportWidth']+1, (name,result)
                assert not result['outside'] and not result['scrollbars'], (name,result)
                assert all(m['overflowY'] not in ['hidden','clip'] and m['overflowX'] not in ['hidden','clip'] for m in result['metrics'] if m['element'] in ['settings-layout','settings-nav-panel','settings-nav','settings-content','model-editor-form','schedule-editor']), result
                if shot:
                    path = output / (name+'.png'); await page.screenshot(path=str(path)); screenshots.append(str(path))

            await new()
            for width,height in [(1366,768),(1440,900),(1920,1080)]:
                await page.set_viewport_size({'width':width,'height':height})
                await fits(f'add-model-{width}x{height}')
            await page.set_viewport_size({'width':1366,'height':768})
            assert await page.locator('#model_temperature').input_value() == '0.7'
            assert await page.locator('#model_max_tokens').input_value() == '4096'
            assert not await page.locator('#modelEndpointNotice').is_visible()

            # Saving fails before any test; draft remains and the error is persistent.
            async def denied(route):
                await route.fulfill(status=401, content_type='application/json', body=json.dumps({'code':'CRED-701','detail':{'reason':'api-token'}}))
            await page.route('**/api/settings/models', denied)
            await page.get_by_role('button',name='保存并测试连接',exact=True).click()
            await page.locator('.model-editor-error').filter(has_text='保存失败').wait_for()
            assert await page.locator('#model_api_key').input_value() == 'synthetic-editor-key'
            assert not provider_requests and not await page.locator('#confirmProceed').is_visible()
            await fits('add-model-save-auth-error')
            await page.unroute('**/api/settings/models', denied)
            # Plain save neither tests nor prompts, and first-profile activation is unchanged.
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.get_by_role('heading',name='模型连接设计验收',exact=True).wait_for()
            first = (await profiles())['profiles'][0]
            assert not provider_requests and (await profiles())['active'] == first['id']
            checks += ['save failure preserves input and never probes', 'plain save sends zero model requests', 'activation rules unchanged']

            await page.evaluate('id=>actions["model-edit"](id)', first['id'])
            assert await page.locator('#model_api_key').input_value() == ''
            for width,height in [(1366,768),(1440,900),(1920,1080)]:
                await page.set_viewport_size({'width':width,'height':height})
                await fits(f'edit-model-{width}x{height}')
            await page.set_viewport_size({'width':1366,'height':768})
            await page.locator('#model_base_url').fill(endpoint+'/changed')
            await page.get_by_role('button',name='保存修改',exact=True).click()
            await page.locator('.model-editor-error').filter(has_text='填写新 API Key').wait_for()
            await fits('edit-model-endpoint-validation')
            assert (await profiles())['profiles'][0]['base_url'] == endpoint
            await page.locator('#model_api_key').fill('synthetic-replacement-key')
            await page.locator('[name=clear_api_key]').check()
            await page.get_by_role('button',name='保存修改',exact=True).click()
            await page.locator('.model-editor-error').filter(has_text='只能选择一项').wait_for()
            await page.locator('[name=clear_api_key]').uncheck()
            await page.locator('#model_base_url').fill(endpoint)
            await page.locator('#model_api_key').fill('')
            await page.locator('#model_label').fill('编辑后模型')
            await page.get_by_role('button',name='保存修改',exact=True).click()
            await page.get_by_role('heading',name='编辑后模型',exact=True).wait_for()
            assert (await profiles())['profiles'][0]['has_api_key'] and not save_requests[-1]['has_key']
            checks += ['endpoint credential replacement/clear validation', 'blank edit key preserves server credential']

            # Slow save plus forced duplicate submit: exactly one creation request.
            await new('新增后测试模型')
            entered,release=asyncio.Event(),asyncio.Event()
            async def hold_save(route):
                entered.set();await release.wait();await route.continue_()
            await page.route('**/api/settings/models', hold_save)
            before = len(save_requests)
            await page.get_by_role('button',name='保存并测试连接',exact=True).click()
            await asyncio.wait_for(entered.wait(),5)
            await fits('add-model-saving')
            await page.locator('form.model-editor-form').evaluate('(f)=>f.dispatchEvent(new SubmitEvent("submit",{bubbles:true,cancelable:true,submitter:f.querySelector("[data-save-test]")}))')
            assert len(save_requests)==before+1
            release.set()
            await page.get_by_role('button',name='开始测试',exact=True).wait_for()
            await page.unroute('**/api/settings/models',hold_save)
            assert await page.locator('#model_api_key').input_value() == ''
            assert '密钥已保存' in await page.locator('[data-key-hint]').inner_text()
            assert len((await profiles())['profiles'])==2 and not provider_requests
            second = next(p for p in (await profiles())['profiles'] if p['id']!=first['id'])
            assert (await profiles())['active']==first['id']
            await fits('model-test-confirmation')
            await page.get_by_role('button',name='暂不测试',exact=True).click()
            assert not provider_requests and len((await profiles())['profiles'])==2
            await page.get_by_role('button',name='再次测试',exact=True).click()
            before=len(save_requests)
            assert await page.get_by_role('button',name='开始测试',exact=True).is_visible()
            assert len(save_requests)==before
            await page.get_by_role('button',name='开始测试',exact=True).click()
            await page.locator('[role=status]').filter(has_text='连接测试通过').wait_for()
            assert len(provider_requests)==1 and len((await profiles())['profiles'])==2
            await fits('model-test-passed')
            checks += ['duplicate submit creates only one profile', 'test cancellation retains saved configuration', 'retest uses saved ID without resaving', 'real bounded loopback HTTP probe']

            # Changed fields are saved to the same ID; provider auth failure is distinct.
            provider_mode['value']='auth'
            await page.locator('#model_temperature').fill('0.4')
            before=len(save_requests)
            await page.get_by_role('button',name='保存并测试连接',exact=True).click()
            await page.get_by_role('button',name='开始测试',exact=True).wait_for()
            assert len(save_requests)==before+1 and save_requests[-1]['id']==second['id']
            await page.get_by_role('button',name='开始测试',exact=True).click()
            await page.locator('[role=status]').filter(has_text='模型服务认证失败').wait_for()
            assert len(provider_requests)==2 and len((await profiles())['profiles'])==2
            await fits('model-test-auth-failed')

            # Controlled pending probe and duplicate click do not issue another request.
            entered,release=asyncio.Event(),asyncio.Event()
            async def delayed_probe(route):
                entered.set();await release.wait()
                await route.fulfill(status=200,content_type='application/json',body='{"status":"unavailable","code":"model_connection_timeout"}')
            await page.route('**/api/v1/connections/model:*/test',delayed_probe)
            await page.get_by_role('button',name='再次测试',exact=True).click()
            await page.get_by_role('button',name='开始测试',exact=True).click()
            await asyncio.wait_for(entered.wait(),5)
            await fits('model-testing')
            assert await page.locator('[data-save-test]').is_disabled()
            await page.locator('#confirmProceed').evaluate('(b)=>b.click()')
            release.set()
            await page.locator('[role=status]').filter(has_text='测试结果未确认').wait_for()
            await fits('model-test-timeout')
            await page.unroute('**/api/v1/connections/model:*/test',delayed_probe)
            checks += ['changed configuration updates same ID', 'provider auth failure retains profile', 'timeout stays uncertain with no auto retry', 'pending test blocks duplicate clicks']

            # All fields and all navigation remain visible without adaptive hiding.
            await page.locator('#model_temperature').fill('2')
            await page.get_by_role('button',name='保存修改',exact=True).click()
            await page.locator('.model-editor-error:visible').wait_for()
            await fits('edit-model-parameter-validation')
            assert await page.locator('#model_temperature').evaluate('(e)=>e===document.activeElement')
            await page.locator('#model_temperature').fill('0.4')
            checks.append('all fields and 14 navigation entries remain expanded during validation')
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'dark'})")
            await fits('edit-model-dark')
            await page.get_by_role('button',name='取消',exact=True).click()
            await page.get_by_role('button',name='添加模型',exact=True).wait_for()
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'light'})")
            # Persist the explicit clear operation, then rotate and verify the new key.
            await page.evaluate('id=>actions["model-edit"](id)',first['id'])
            await page.locator('#model_base_url').fill(endpoint+'/cleared')
            await page.locator('[name=clear_api_key]').check()
            await fits('edit-model-clear-credential')
            await page.get_by_role('button',name='保存修改',exact=True).click()
            await page.get_by_role('heading',name='编辑后模型',exact=True).wait_for()
            assert not next(p for p in (await profiles())['profiles'] if p['id']==first['id'])['has_api_key']
            await page.evaluate('id=>actions["model-edit"](id)',first['id'])
            await page.locator('#model_base_url').fill(endpoint)
            await page.locator('#model_api_key').fill('synthetic-rotated-key')
            provider_mode['value']='ok'
            await page.get_by_role('button',name='保存并测试连接',exact=True).click()
            await page.get_by_role('button',name='开始测试',exact=True).click()
            await page.locator('[role=status]').filter(has_text='连接测试通过').wait_for()
            assert len(provider_requests)==3 and len((await profiles())['profiles'])==2
            assert next(p for p in (await profiles())['profiles'] if p['id']==first['id'])['has_api_key']
            await fits('edit-model-rotated-key-tested')
            checks.append('explicit clear and key replacement persist correctly')
            # Expired application auth during testing is not reported as provider auth.
            async def expired_test(route):
                await route.fulfill(status=401,content_type='application/json',body='{"code":"CRED-701","detail":{"reason":"api-token"}}')
            await page.route('**/api/v1/connections/model:*/test',expired_test)
            await page.get_by_role('button',name='再次测试',exact=True).click()
            await page.get_by_role('button',name='开始测试',exact=True).click()
            await page.locator('[role=status]').filter(has_text='应用登录校验失败').wait_for()
            await page.unroute('**/api/v1/connections/model:*/test',expired_test)
            checks.append('application auth and provider credential failures use distinct messages')
            # Late responses must not replace a page after the user leaves the form.
            entered,release=asyncio.Event(),asyncio.Event()
            await page.route('**/api/v1/connections/model:*/test',delayed_probe)
            await page.get_by_role('button',name='再次测试',exact=True).click()
            await page.get_by_role('button',name='开始测试',exact=True).click()
            await asyncio.wait_for(entered.wait(),5)
            await page.locator('.settings-nav [data-nav=account]').click()
            await page.locator('#accountCreate').wait_for()
            release.set()
            await page.wait_for_function('!document.querySelector(".model-editor-form")')
            await page.locator('#accountCreate').wait_for()
            checks.append('late test response cannot replace a newly selected page')
            # Both entry points use one real refreshable schedule creation route.
            schedule_requests=[]
            def record_schedule(request):
                if request.method=='POST' and request.url==base+'/api/v1/schedules':
                    schedule_requests.append(request.post_data_json['action'])
            page.on('request',record_schedule)
            await page.locator('#workbenchEntry').click()
            await page.locator('.actions [data-action=schedule-new]').click()
            await page.locator('#scheduleForm').wait_for()
            assert page.url.endswith('#schedule-new')
            assert not await page.locator('#dialog').is_visible()
            assert await page.locator('.settings-nav [data-nav=schedules]').get_attribute('aria-current')=='page'
            assert not schedule_requests
            for width,height in [(1366,768),(1440,900),(1920,1080)]:
                await page.set_viewport_size({'width':width,'height':height})
                await fits(f'create-schedule-{width}x{height}')
            await page.set_viewport_size({'width':1366,'height':768})
            await page.reload()
            await page.locator('#scheduleForm').wait_for()
            assert await page.locator('#scheduleExpr').input_value()=='3600'
            await page.get_by_role('button',name='返回定时任务',exact=True).click()
            await page.get_by_role('button',name='创建计划任务',exact=True).click()
            await page.locator('#scheduleForm').wait_for()
            assert page.url.endswith('#schedule-new') and not schedule_requests
            await page.locator('#scheduleForm [type=submit]').click()
            await page.locator('#scheduleForm [role=alert]:visible').wait_for()
            await fits('create-schedule-validation')
            await page.locator('#scheduleName').fill('UPPERCASE')
            await page.locator('#scheduleIntent').fill('synthetic deferred schedule')
            await page.locator('#scheduleForm [type=submit]').click()
            assert not schedule_requests
            await page.locator('#scheduleName').fill('layout_schedule')
            await page.locator('#scheduleExpr').fill('59')
            await page.locator('#scheduleForm [type=submit]').click()
            assert not schedule_requests
            await page.locator('#scheduleExpr').fill('86400')
            entered,release=asyncio.Event(),asyncio.Event()
            async def hold_schedule(route):
                entered.set();await release.wait()
                await route.fulfill(status=503,content_type='application/json',body='{"code":"synthetic_save_failed","message":"本机受控保存失败"}')
            await page.route('**/api/v1/schedules',hold_schedule)
            await page.locator('#scheduleForm [type=submit]').click()
            await asyncio.wait_for(entered.wait(),5)
            await fits('create-schedule-saving')
            await page.locator('#scheduleForm').evaluate('(f)=>f.dispatchEvent(new SubmitEvent("submit",{bubbles:true,cancelable:true,submitter:f.querySelector("[type=submit]")}))')
            assert len(schedule_requests)==1
            release.set()
            await page.locator('#scheduleForm [role=alert]').filter(has_text='创建失败').wait_for()
            assert await page.locator('#scheduleName').input_value()=='layout_schedule'
            assert await page.locator('#scheduleIntent').input_value()=='synthetic deferred schedule'
            await fits('create-schedule-save-failed')
            await page.unroute('**/api/v1/schedules',hold_schedule)
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'dark'})")
            await fits('create-schedule-dark')
            await page.evaluate("pyharnessAppearance.updateConfig({themeId:'light'})")
            await page.locator('#scheduleForm [type=submit]').click()
            await page.locator('tr').filter(has_text='layout_schedule').wait_for()
            schedules=await page.evaluate('async()=>await get("schedules")')
            assert len(schedules['schedules'])==1 and schedules['schedules'][0]['expr']=='86400'
            assert schedules['schedules'][0]['triggered']==0
            assert len(schedule_requests)==2
            await page.get_by_role('button',name='创建计划任务',exact=True).click()
            await page.locator('#scheduleForm').wait_for()
            await page.get_by_role('button',name='取消',exact=True).click()
            await page.locator('tr').filter(has_text='layout_schedule').wait_for()
            assert len(schedule_requests)==2
            checks += ['workbench and settings share refreshable schedule creation route',
                       'schedule validation preserves constraints without requests',
                       'pending schedule prevents duplicate creation',
                       'failed schedule retains draft and succeeds on explicit retry',
                       'opening and cancelling schedule never creates or executes jobs']
            assert not errors,errors
            assert 'synthetic-editor-key' not in await page.locator('body').inner_text()
            assert 'synthetic-replacement-key' not in await page.locator('body').inner_text()
            await context.close();await browser.close()
        summary={'status':'passed','checks':checks,'screenshots':screenshots,'page_errors':errors,
                 'controlled_provider_requests':len(provider_requests),'external_provider_requests':0,
                 'profiles_created':2,'save_requests':len(save_requests),'layouts':layouts,'schedule_requests':len(schedule_requests)}
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        return summary
    finally:
        for server in servers:server.should_exit=True
        await asyncio.gather(*(asyncio.wait_for(task,10) for task in tasks))
        for sock in sockets:sock.close()
        for service in app._service_registry.all():await service.shutdown()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,required=True)
    output=parser.parse_args().output_dir.resolve()
    if output.is_relative_to(ROOT):parser.error('Use a runtime directory outside checkout')
    output.mkdir(parents=True,exist_ok=True)
    result=asyncio.run(asyncio.wait_for(verify(output),240))
    print(json.dumps({'status':result['status'],'checks':len(result['checks']),'screenshots':len(result['screenshots']),'external_provider_requests':0}))
