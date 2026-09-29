"""Actual browser/HTTP/queue acceptance; model is explicitly synthetic, Docker is not used."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import socket
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class ScenarioModel:
    """Deterministic tool protocol, never a provider connectivity result."""
    def __init__(self):
        self.rounds=defaultdict(int)
        self.cancel_entered=asyncio.Event()

    async def chat(self,messages,tools=None,*,ctx):
        from pyharness.core import llm
        from pyharness.errors import PyHError
        text=next((str(m['content']) for m in reversed(messages) if m.get('role')=='user'),'')
        key=(ctx.session.sid,ctx.task_id)
        self.rounds[key]+=1
        n=self.rounds[key]
        calls=[];content='[SYNTHETIC AUDIT] completed'
        if 'audit-cancel' in text:
            self.cancel_entered.set()
            await asyncio.Event().wait()
        if 'audit-failure' in text:
            raise PyHError('LLM-303')
        if 'audit-continuous' in text:
            if n==1:calls=[('user.ask',{'question':'合成验收：请确认继续','ttl_s':60})]
            elif n==2:calls=[('subagent.spawn',{'task':'audit-child summarize synthetic input','tools_subset':[]})]
        if 'audit-reject' in text and n==1:
            calls=[('fs.write_file',{'path':'must-not-exist.txt','content':'never written'})]
        usage=SimpleNamespace(prompt_tokens=10,completion_tokens=5,prompt_cache_hit_tokens=0,prompt_cache_miss_tokens=10)
        await llm.report_usage(usage,'audit-synthetic',ctx=ctx,counters=getattr(ctx,'counters',None))
        tool_calls=[llm.ToolCall(id=f'audit-{n}-{i}',index=i,name=name,raw_args=args,raw_json=json.dumps(args)) for i,(name,args) in enumerate(calls)]
        result=llm.LLMResponse(content='' if calls else content,tool_calls=tool_calls or None,usage=usage,model='audit-synthetic',finish_reason='tool_calls' if calls else 'stop')
        event=await ctx.session.append('llm.response',{'model':'audit-synthetic','finish_reason':result.finish_reason,'content':result.content,'tool_calls':[{'id':c.id,'name':c.name,'arguments':c.raw_json} for c in tool_calls]},actor='llm')
        result.seq=event.seq
        return result

    async def chat_stream(self,*args,**kwargs):return await self.chat(*args,**kwargs)
    async def ping(self):return 0.0


async def acceptance(output):
    import uvicorn
    from playwright.async_api import async_playwright
    from pyharness import cli,web
    from pyharness.core import llm
    from pyharness.desktop.app import DesktopApp
    from scripts.platform_browser_smoke import find_browser,navigate
    from tests.support import isolated_settings

    rows=[];errors=[];snapshots=[]
    async def start(cfg):
        app=DesktopApp(cli.assemble_ctx(cfg));sock=socket.socket();sock.bind(('127.0.0.1',0))
        base=f'http://127.0.0.1:{sock.getsockname()[1]}'
        server=uvicorn.Server(uvicorn.Config(app.api,log_level='critical',access_log=False))
        task=asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done():await task;raise AssertionError('server exited')
            await asyncio.sleep(.01)
        return app,base,server,task
    async def stop(app,server,task):
        # Close owned browser SSE connections even if an assertion failed.
        for context in list(browser.contexts):
            await context.close()
        server.should_exit=True
        try:await asyncio.wait_for(task,10)
        finally:await app.service.shutdown()
    async def shot(page,name):
        await page.screenshot(path=str(output/(name+'.png')),full_page=True);snapshots.append(name+'.png')
    async def close_dialog(page):
        if await page.locator('#dialog').is_visible():await page.locator('#dialogClose').click()
    async def until(predicate):
        async with asyncio.timeout(15):
            while not await predicate():await asyncio.sleep(.05)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=find_browser(),headless=True,args=['--disable-background-networking','--no-first-run'])
        app,base,server,task=await start(web.settings_for(output/'unconfigured'))
        try:
            anonymous=await browser.new_context();anon=await anonymous.new_page();await anon.goto(base+'/')
            await anon.locator('#login').wait_for();assert anon.url.endswith('/login');await anonymous.close()
            ctx=await browser.new_context(viewport={'width':1440,'height':900},extra_http_headers={'X-PyHarness-Token':app._api_token})
            page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(type(e).__name__))
            await page.goto(base+'/');await page.locator('#modelReadiness').get_by_role('button',name='打开模型配置').wait_for()
            await page.locator('#messageText').fill('没有配置模型的合成任务')
            async with page.expect_response(lambda r:r.url.endswith('/api/v1/runs') and r.request.method=='POST') as pending:
                await page.get_by_role('button',name='发送 ↑',exact=True).click()
            assert (await pending.value).status==422
            await page.locator('#sendFeedback').get_by_role('button',name='打开模型配置').wait_for()
            assert await page.locator('#messageText').input_value()=='没有配置模型的合成任务'
            assert (await app.service.platform.sessions())['sessions']==[]
            assert (await app.service.platform.runs())['runs']==[]
            await shot(page,'missing-model-persistent-feedback')
            await page.locator('#sendFeedback').get_by_role('button',name='打开模型配置').click()
            await page.get_by_role('button',name='添加模型',exact=True).wait_for()
            await navigate(page,'chat');assert await page.locator('#messageText').input_value()=='没有配置模型的合成任务'
            rows.append({'area':'first-use/auth/model','result':'passed','operations':'anonymous login redirect; real unconfigured POST 422; persistent guidance; preserved draft; no orphan session/run','provider_requests':0})
            await ctx.close()
        finally:await stop(app,server,task)

        cfg=isolated_settings(output/'scenario-data');cfg.llm.model='audit-synthetic';model=ScenarioModel();llm.adapters[cfg.llm.model]=model
        app,base,server,task=await start(cfg)
        try:
            ctx=await browser.new_context(viewport={'width':1440,'height':900},extra_http_headers={'X-PyHarness-Token':app._api_token})
            page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(type(e).__name__))
            await page.goto(base+'/');await page.locator('#compose').wait_for()
            await navigate(page,'agents');await page.get_by_role('button',name='创建 Agent',exact=True).click()
            definition={'name':'合成闭环 Agent','allowed_tools':['user.ask','subagent.spawn','fs.write_file'],'sandbox_profile':{'mode':'disabled'},'permission_policy':'standard','max_rounds':8}
            await page.locator('#agentJson').fill(json.dumps(definition,ensure_ascii=False));await page.get_by_role('button',name='保存草稿',exact=True).click()
            await page.get_by_role('button',name='发布',exact=True).click();await page.get_by_text('已发布',exact=True).wait_for()
            await page.get_by_role('button',name='测试运行',exact=True).click();await page.locator('#compose').wait_for()
            sid=await page.evaluate('state.sid')
            await page.locator('#messageText').fill('audit-continuous')
            await page.locator('#messageText').press('Enter')
            await page.locator('.run-notice').wait_for()
            async def has_question():return bool((await app.service.pending_asks())['pending'])
            await until(has_question)
            await page.locator('#inspectorToggle').click();await page.get_by_role('button',name='待答问题',exact=True).click()
            await page.get_by_role('button',name='回答',exact=True).click();await page.locator('#answerText').fill('继续合成验收');await shot(page,'continuous-question')
            await page.get_by_role('button',name='提交答复',exact=True).click()
            await page.locator('.run-notice .badge.green').wait_for(timeout=20000)
            run_id=await page.locator('.run-notice').get_attribute('data-run-id')
            await page.get_by_role('button',name='子任务',exact=True).click();await page.get_by_text('已完成',exact=True).last.wait_for()
            history=await app.service.list_subagents(sid);assert len(history['history'])==1 and history['history'][0]['status']=='completed'
            await shot(page,'continuous-child-summary');await close_dialog(page)
            run=await app.service.platform.run(run_id);assert run['status']=='completed' and run['steps'] and run['token_usage']>0
            # Public artifact recorder is a fixture boundary. This report is derived
            # from the real synthetic Run, not claimed to be Docker-generated output.
            log=await app.service.require_session(sid)
            # Run ownership is already recorded in Artifact metadata. Keep the
            # content free of long opaque IDs rejected by the existing redactor.
            raw=json.dumps({'evidence':'synthetic model / real runtime','status':run['status']}).encode()
            artifact=await app.service.platform.record_generated(SimpleNamespace(session=log,task_id=run['task_id']),'audit-report.json',raw)
            await navigate(page,'artifacts');await page.get_by_role('button',name='预览',exact=True).click()
            await page.locator('#dialog').get_by_text('synthetic model / real runtime',exact=False).wait_for();await shot(page,'continuous-artifact-preview');await close_dialog(page)
            await page.get_by_role('button',name='申请导出',exact=True).click();await navigate(page,'approvals')
            await page.get_by_role('button',name='批准一次',exact=True).wait_for();await shot(page,'continuous-export-approval')
            page.once('dialog',lambda dialog:dialog.accept())
            async with page.expect_response(lambda r:'/approvals/' in r.url and r.url.endswith('/approve')) as approved:
                await page.get_by_role('button',name='批准一次',exact=True).click()
            assert (await approved.value).status==200
            async def exported():return (await app.service.platform.artifact(artifact['artifact_id']))['download_allowed']
            await until(exported)
            await navigate(page,'artifacts')
            async with page.expect_download() as download_info:await page.get_by_role('link',name='下载',exact=True).click()
            download=await download_info.value;target=output/'downloaded-audit-report.json';await download.save_as(target)
            assert hashlib.sha256(target.read_bytes()).hexdigest()==artifact['sha256']
            rows.append({'area':'continuous path','result':'passed','path':['UI Agent create/publish','UI session/send','real Run','UI answer','model tool delegates child','child summary','public recorder synthetic report fixture','UI preview','real export approval','UI download/hash'],'artifact_fixture_explicit':True,'sandbox_mode':'disabled','run_id':run_id})
            # Rejection is observed before the disabled execution boundary; never
            # approve a filesystem tool or substitute host execution for Docker.
            await navigate(page,'chat');await page.locator('#messageText').fill('audit-reject');await page.locator('#messageText').press('Enter')
            await page.locator('.run-notice .badge.amber').filter(has_text='等待审批').wait_for(timeout=15000)
            await shot(page,'waiting-approval-visible');await navigate(page,'approvals')
            page.once('dialog',lambda dialog:dialog.accept())
            await page.get_by_role('button',name='拒绝',exact=True).click()
            await navigate(page,'chat');await page.locator('.run-notice .badge.green').wait_for(timeout=15000)
            assert not (Path(app.service._session_workspace(sid))/'must-not-exist.txt').exists()
            assert any(a['status']=='rejected' for a in (await app.service.platform.approvals())['approvals'])
            rows.append({'area':'approval rejection','result':'passed','waiting_approval_observed':True,'filesystem_tool_executed':False})
            await page.locator('#messageText').fill('audit-cancel');await page.locator('#messageText').press('Enter');await asyncio.wait_for(model.cancel_entered.wait(),10)
            await page.get_by_role('button',name='停止',exact=True).click();await page.locator('.run-notice .badge').filter(has_text='已取消').wait_for(timeout=15000)
            await shot(page,'cancel-visible')
            await page.locator('#messageText').fill('audit-failure');await page.locator('#messageText').press('Enter')
            await page.locator('.run-notice [data-run-error]').filter(has_text='LLM-303').wait_for(timeout=15000)
            await page.locator('#messageText').fill('失败后保留的新草稿');await page.evaluate('render(true)');assert await page.locator('#messageText').input_value()=='失败后保留的新草稿'
            await shot(page,'runtime-failure-visible');rows.append({'area':'failure/cancel/draft','result':'passed'})
            # Knowledge's downstream citation is exercised through visible forms.
            await navigate(page,'knowledge')
            async with page.expect_file_chooser() as chooser:await page.get_by_role('button',name='上传文档',exact=True).click()
            await (await chooser.value).set_files({'name':'audit-knowledge.txt','mimeType':'text/plain','buffer':b'auditkeyword citation evidence'})
            await page.get_by_text('audit-knowledge.txt',exact=True).wait_for();await page.locator('#queryText').fill('auditkeyword');await page.locator('#knowledgeQuery button').click()
            await page.locator('#dialog').get_by_text('auditkeyword citation evidence',exact=False).wait_for();await shot(page,'knowledge-citation');await close_dialog(page)
            await page.locator('#queryText').fill('definitelyabsentword');await page.locator('#knowledgeQuery button').click()
            await page.locator('#dialog').get_by_text('no_reliable_evidence',exact=False).wait_for();await close_dialog(page)
            rows.append({'area':'Knowledge','result':'passed','external_rag':'not_configured'})
            from scripts.platform_account_browser_contract import verify_accounts
            rows.append(await verify_accounts(browser,page,app,base,output))
            from scripts.platform_lifecycle_browser_contract import verify_lifecycle
            rows.append(await verify_lifecycle(page,app,output))
            from scripts.platform_connection_browser_contract import verify_connection
            rows.append(await verify_connection(page,output))
            await navigate(page,'usage');await page.locator('main h1').wait_for();await shot(page,'usage-after-chain')
            await ctx.close()
        finally:await stop(app,server,task)
        # Normal shutdown and re-open same synthetic data, no replay/model call.
        before=sum(model.rounds.values());app,base,server,task=await start(cfg)
        try:
            ctx=await browser.new_context(viewport={'width':1440,'height':900},extra_http_headers={'X-PyHarness-Token':app._api_token});page=await ctx.new_page()
            await page.goto(base+'/');await page.locator(f'[data-session="{sid}"]').click();await page.locator('.run-notice').wait_for()
            assert (await app.service.platform.run(run_id))['status']=='completed'
            restored=await app.service.platform.artifact(artifact['artifact_id']);assert restored['download_allowed'] and restored['sha256']==artifact['sha256']
            assert (await app.service.list_subagents(sid))['history'][0]['status']=='completed'
            assert len((await app.service.platform.approvals())['approvals'])>=2
            assert (await app.service.platform.run(run_id))['trace']
            await shot(page,'restart-history');assert sum(model.rounds.values())==before
            rows.append({'area':'restart','result':'passed','records':['Session','Run','RunStep','child history','Approval','Trace','Artifact','usage'],'automatic_replay':False})
            await ctx.close()
        finally:await stop(app,server,task)
        await browser.close()
    assert not errors,errors
    report={'status':'passed','rows':rows,'screenshots':sorted(p.name for p in output.glob('*.png')),'uncaught_errors':errors,'real_provider_requests':0,'model':'explicit synthetic ScenarioModel','docker':'deferred; never used','host_fallback':False}
    (output/'feature-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args()
    output=args.output_dir.resolve()
    if output.is_relative_to(ROOT):parser.error('Use a new directory outside checkout')
    output.mkdir(parents=True,exist_ok=True)
    asyncio.run(asyncio.wait_for(acceptance(output),180))
