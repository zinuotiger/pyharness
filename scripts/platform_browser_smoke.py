"""Bounded browser acceptance with synthetic data and a deterministic model.

All runtime data, browser profiles, screenshots and logs go to --output-dir.
No user settings, secrets or existing browser profiles are used.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def find_browser():
    # Prefer the existing cross-platform Chrome; never download a browser bundle.
    roots=[Path(os.environ[k]) for k in ('PROGRAMFILES','PROGRAMFILES(X86)') if os.environ.get(k)]
    candidates=[root/relative for relative in ('Google/Chrome/Application/chrome.exe','Microsoft/Edge/Application/msedge.exe') for root in roots]
    return next((str(p) for p in candidates if p.is_file()),None) or shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')


async def smoke(output, expect_docker=None):
    executable=find_browser()
    if executable is None:
        raise RuntimeError('An existing Chrome/Edge browser is required; no browser download is performed')
    from scripts.ci_verify import environment
    env=environment(output/'isolated')
    os.environ.clear();os.environ.update(env)
    from tests.support import isolated_settings
    from scripts.verification_local import DeterministicModel
    from pyharness import cli
    from pyharness.core import llm
    from pyharness.desktop.app import DesktopApp
    from playwright.async_api import async_playwright
    import uvicorn
    cfg=isolated_settings(output/'data')
    cfg.llm.model='verification-offline'
    llm.adapters[cfg.llm.model]=DeterministicModel()
    app=DesktopApp(cli.assemble_ctx(cfg))
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app.api,log_level='warning'))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    errors=[];console=[];shots=[]
    try:
        async with asyncio.timeout(120):
            for _ in range(100):
                if server.started:break
                await asyncio.sleep(.02)
            assert server.started
            async with async_playwright() as browser_api:
                browser=await browser_api.chromium.launch(executable_path=executable,headless=True,
                    args=['--disable-background-networking','--no-first-run'])
                context=await browser.new_context(viewport={'width':1440,'height':900},
                    extra_http_headers={'X-PyHarness-Token':app._api_token})
                page=await context.new_page()
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('console',lambda message:console.append({'type':message.type,'text':message.text}))
                await page.goto(f'http://127.0.0.1:{port}/')
                await page.get_by_text('还没有会话',exact=True).wait_for()
                assert await page.locator('.error').count()==0
                await page.route('**/api/v1/acceptance-no-content',lambda route:route.fulfill(status=204))
                assert await page.evaluate("api('/api/v1/acceptance-no-content')") is None
                await page.unroute('**/api/v1/acceptance-no-content')
                for status in (404,409,422):
                    await page.route('**/api/v1/usage',lambda route,request,s=status:route.fulfill(status=s,content_type='application/json',body=json.dumps({'code':str(s),'message':'controlled contract error'})))
                    await page.locator('#nav [data-nav="usage"]').click()
                    await page.get_by_text('加载失败',exact=True).wait_for()
                    assert str(status) in await page.locator('.error').inner_text()
                    await page.unroute('**/api/v1/usage')
                    await page.get_by_role('button',name='重试',exact=True).click()
                    await page.locator('.skeleton').first.wait_for(state='detached')
                    assert await page.locator('.error').count()==0
                health=await app.service.platform.sandboxes.backends['isolated'].health()
                if expect_docker:
                    assert health['status']==expect_docker,health
                dashboard=await (await context.request.get(f'http://127.0.0.1:{port}/api/v1/dashboard')).json()
                assert dashboard['health']['docker']['status']==health['status']
                for route in ['dashboard','chat','runs','approvals','sandboxes','agents','artifacts','knowledge','connections','schedules','usage','settings']:
                    await page.locator(f'#nav [data-nav="{route}"]').click()
                    await page.locator('.skeleton').first.wait_for(state='detached')
                    assert await page.locator('.error').count()==0,await page.locator('main').inner_text()
                    path=output/(route+'.png')
                    await page.screenshot(path=str(path),full_page=True)
                    shots.append(path.name)
                await page.locator('#nav [data-nav="agents"]').click()
                await page.get_by_role('button',name='通用助手',exact=True).click()
                await page.get_by_role('button',name='保存草稿',exact=True).click()
                await page.get_by_role('button',name='发布',exact=True).click()
                await page.get_by_text('已发布',exact=True).wait_for()
                agent_id=await page.get_by_role('button',name='测试运行',exact=True).get_attribute('data-id')
                await page.get_by_role('button',name='测试运行',exact=True).click()
                await page.locator('#messageText').wait_for()
                assert await page.locator('#agentSelect').input_value()==agent_id
                await page.locator('#messageText').fill('synthetic browser hello')
                await page.get_by_role('button',name='发送 ↑',exact=True).click()
                await page.locator('.message').filter(has_text='[DETERMINISTIC TEST MODEL] received: synthetic browser hello').wait_for(timeout=15000)
                assert 'Invalid Date' not in await page.locator('body').inner_text()
                await page.keyboard.press('Control+k')
                assert await page.locator('#globalSearch').evaluate('(e)=>e===document.activeElement')
                assert await page.evaluate("date(null)==='时间未知' && date('invalid')==='时间未知' && !date(1700000000).includes('1970')")
                # Hold the async refresh at a barrier while a user edits the draft.
                started=asyncio.Event();release=asyncio.Event()
                async def delay(route):
                    started.set();await asyncio.wait_for(release.wait(),5);await route.continue_()
                await page.route('**/api/v1/sessions',delay)
                refreshing=asyncio.create_task(page.evaluate('render(true)'))
                await asyncio.wait_for(started.wait(),5)
                await page.locator('#messageText').fill('draft must survive async refresh')
                release.set();await asyncio.wait_for(refreshing,10)
                await page.unroute('**/api/v1/sessions')
                assert await page.locator('#messageText').input_value()=='draft must survive async refresh'
                sid_a=await page.evaluate('state.sid')
                # Publishing v2 must not rebind this existing v1 conversation.
                await page.evaluate('(id)=>post("agents/"+id+"/publish")',agent_id)
                await page.locator('#messageText').fill('continue fixed version')
                await page.get_by_role('button',name='发送 ↑',exact=True).click()
                await page.locator('.message').filter(has_text='[DETERMINISTIC TEST MODEL] received: continue fixed version').wait_for(timeout=15000)
                fixed=await page.evaluate('async()=>{const d=await get("runs");return d.runs.filter(r=>r.session_id===state.sid).map(r=>r.agent_version)}')
                assert fixed and set(fixed)=={1}
                # Keep a POST response in flight while switching to another session.
                posted=asyncio.Event();post_release=asyncio.Event()
                async def delay_post(route):
                    if route.request.method=='POST':
                        response=await route.fetch();posted.set()
                        await asyncio.wait_for(post_release.wait(),8)
                        await route.fulfill(response=response)
                    else:await route.continue_()
                await page.route('**/api/v1/runs',delay_post)
                await page.locator('#messageText').fill('background session request')
                await page.get_by_role('button',name='发送 ↑',exact=True).click()
                await asyncio.wait_for(posted.wait(),5)
                await page.evaluate('newSession()')
                sid_b=await page.evaluate('state.sid')
                await page.locator('#messageText').fill('B draft must stay')
                post_release.set()
                await page.wait_for_function('document.querySelector("#toast").textContent.includes("原会话")')
                assert await page.evaluate('state.sid')==sid_b
                assert await page.locator('#messageText').input_value()=='B draft must stay'
                await page.unroute('**/api/v1/runs')
                # Removing attachments must preserve the current unsubmitted text.
                await page.get_by_role('button',name='清除附件',exact=True).click()
                assert await page.locator('#messageText').input_value()=='B draft must stay'
                await page.locator(f'[data-session="{sid_a}"]').first.click()
                await page.locator('#messageText').wait_for()
                assert await page.locator('#agentSelect').input_value()==agent_id
                await page.locator('#messageText').fill('draft must survive async refresh')
                upload_started=asyncio.Event();upload_release=asyncio.Event()
                async def delay_upload(route):
                    response=await route.fetch();upload_started.set()
                    await asyncio.wait_for(upload_release.wait(),8)
                    await route.fulfill(response=response)
                await page.route('**/api/v1/artifacts',delay_upload)
                async with page.expect_file_chooser() as choice:
                    await page.get_by_role('button',name='附件',exact=True).click()
                await (await choice.value).set_files({'name':'synthetic.txt','mimeType':'text/plain','buffer':b'input'})
                await asyncio.wait_for(upload_started.wait(),5)
                await page.evaluate('newSession()')
                upload_release.set()
                await page.wait_for_function('(sid)=>composer(sid).attachments.length===1',arg=sid_a)
                await page.unroute('**/api/v1/artifacts')
                assert await page.evaluate('composer().attachments.length')==0
                await page.locator(f'[data-session="{sid_a}"]').first.click()
                await page.locator('#messageText').wait_for()
                assert await page.evaluate('composer().attachments.length')==1
                assert await page.locator('#messageText').input_value()=='draft must survive async refresh'
                # Force a transport failure, verify an actionable error, then retry.
                await page.route('**/api/v1/usage',lambda route:route.fulfill(status=503,content_type='application/json',body='{"code":"synthetic_failure"}'))
                await page.locator('#nav [data-nav="usage"]').click()
                await page.get_by_text('加载失败',exact=True).wait_for()
                await page.unroute('**/api/v1/usage')
                await page.get_by_role('button',name='重试',exact=True).click()
                await page.locator('.skeleton').first.wait_for(state='detached')
                assert await page.locator('.error').count()==0
                await page.locator('#nav [data-nav="chat"]').click()
                await page.locator('#messageText').wait_for()
                # A real offline/online cycle forces EventSource reconnect/catch-up.
                await context.set_offline(True)
                await page.evaluate('disconnectStream();connectStream(state.sid)')
                await page.wait_for_function('state.stream && state.stream.readyState!==1')
                await context.set_offline(False)
                await page.wait_for_function('state.stream && state.stream.readyState===1',timeout=15000)
                assert '[DETERMINISTIC TEST MODEL]' in await page.locator('#messages').inner_text()
                await page.set_viewport_size({'width':1280,'height':900})
                await page.locator('#nav [data-nav="dashboard"]').click()
                await page.locator('.skeleton').first.wait_for(state='detached')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(output/'desktop-1280.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                await page.locator('#nav [data-nav="dashboard"]').click()
                await page.locator('.skeleton').first.wait_for(state='detached')
                await page.screenshot(path=str(output/'mobile.png'),full_page=True)
                await browser.close()
                assert not errors,errors
    finally:
        server.should_exit=True
        await asyncio.wait_for(task,10)
        await app.service.shutdown()
        (output/'browser-console.json').write_text(json.dumps({'errors':errors,'console':console},ensure_ascii=False,indent=2),encoding='utf-8')
    report={'status':'passed','screenshots':shots,'model':'deterministic, not a real model',
        'checks':['twelve rendered pages','empty states','create/publish/test correct Agent','real queue message roundtrip','SSE offline reconnect','API failure and retry','draft refresh barrier','session attachment ownership','fixed version after publication','in-flight request session fence','clear attachment preserves draft','safe dates','Ctrl+K','mobile viewport'],
        'uncaught_errors':len(errors),'browser':Path(executable).name,'http_contracts':[204,404,409,422,503],'sandbox_health':health,'viewport':'1440x900 and 1280x900',
        'commit':__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
    (output/'browser-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--expect-docker',choices=['available','unavailable'])
    args=parser.parse_args()
    output=args.output_dir.resolve()
    if output.is_relative_to(ROOT):
        parser.error('output must be outside checkout')
    output.mkdir(parents=True,exist_ok=True)
    asyncio.run(smoke(output,args.expect_docker))
