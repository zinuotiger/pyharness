"""Real browser account boundaries and explicitly synthetic model-probe UI responses."""
import asyncio


async def verify_management(browser, page, app, base, output):
    await page.set_viewport_size({'width':1440,'height':900})
    await page.evaluate('go("settings")')
    await page.get_by_role('button',name='账户与团队',exact=True).click()
    await page.locator('#accountCreate').wait_for()
    form=page.locator('#accountCreate')
    await form.locator('[name=username]').fill('browser-user')
    await form.locator('[name=password]').fill('synthetic-browser-password')
    await form.locator('[name=tenant]').fill('browser-team')
    await form.locator('button').click()
    await page.get_by_text('账户已创建',exact=True).wait_for()
    await page.locator('#dialog').evaluate('(d)=>d.close()')

    account_context=await browser.new_context(viewport={'width':1440,'height':900})
    account_page=await account_context.new_page()
    try:
        await account_page.goto(base+'/login')
        await account_page.locator('[name=username]').fill('browser-user')
        await account_page.locator('[name=password]').fill('synthetic-browser-password')
        await account_page.get_by_role('button',name='登录',exact=True).click()
        await account_page.locator('#compose').wait_for()
        await account_page.locator('#accountName').filter(has_text='browser-user').wait_for()
        assert await account_page.locator('#workspaceName').inner_text()=='browser-team'
        assert await account_page.locator('#accountRole').inner_text()=='团队管理员'
        assert not any(c['name']=='pyharness_token' for c in await account_context.cookies())
        await account_page.locator('#settingsEntry').click()
        await account_page.get_by_role('button',name='账户与团队',exact=True).click()
        await account_page.locator('#dialog').get_by_text('browser-user',exact=True).wait_for()
        await account_page.screenshot(path=str(output/'account-team.png'),full_page=True)
        await account_page.locator('#dialog').evaluate('(d)=>d.close()')
        await account_page.get_by_role('button',name='团队记忆',exact=True).click()
        await account_page.locator('#memoryForm [name=key]').fill('synthetic')
        await account_page.locator('#memoryForm [name=op]').select_option('set')
        await account_page.locator('#memoryForm [name=value]').fill('synthetic team memory')
        await account_page.locator('#memoryForm button').click()
        await account_page.locator('#memoryForm [role=status]').filter(has_text='已写入').wait_for()
        await account_page.locator('#memoryForm [name=op]').select_option('get')
        await account_page.locator('#memoryForm button').click()
        await account_page.get_by_text('synthetic team memory',exact=True).wait_for()
        await account_page.screenshot(path=str(output/'team-memory.png'),full_page=True)
        await account_page.locator('#dialog').evaluate('(d)=>d.close()')
        await account_page.get_by_role('button',name='账户与团队',exact=True).click()
        await account_page.get_by_role('button',name='退出登录',exact=True).click()
        await account_page.locator('form#login').wait_for()
        assert (await account_context.request.get(base+'/api/v1/sessions')).status==401
    finally:
        await account_context.close()

    entered,release=asyncio.Event(),asyncio.Event()
    calls=[]
    async def fake_probe(route):
        calls.append(route.request.url)
        entered.set()
        await release.wait()
        await route.fulfill(status=200,content_type='application/json',
            body='{"status":"available","probe":"synthetic UI response","retried":false}')
    await page.route('**/api/v1/connections/model:synthetic/test',fake_probe)
    saved=[]
    async def fake_save(route):
        if route.request.method != 'POST':
            await route.continue_()
            return
        saved.append(True)
        await route.fulfill(status=200,content_type='application/json',body='{"profile":{"id":"synthetic"}}')
    await page.route('**/api/settings/models',fake_save)
    await page.evaluate('actions["model-new"]()')
    for name,value in {'label':'Synthetic','model':'synthetic-model','base_url':'https://synthetic.invalid/v1','api_key':'synthetic-browser-key'}.items():
        await page.locator('#model_'+name).fill(value)
    page.once('dialog',lambda dialog:dialog.accept())
    try:
        first=asyncio.create_task(page.get_by_role('button',name='保存并测试连接',exact=True).click())
        await asyncio.wait_for(entered.wait(),5)
        assert len(saved)==1
        await page.get_by_text('测试中，请稍候…',exact=True).wait_for()
        await page.evaluate('actions["model-test"]("synthetic")')
        assert len(calls)==1
        release.set()
        await first
        await page.get_by_text('synthetic UI response',exact=False).wait_for()
        await page.screenshot(path=str(output/'model-probe-ui-synthetic.png'),full_page=True)
    finally:
        release.set()
        await page.unroute('**/api/v1/connections/model:synthetic/test',fake_probe)
        await page.unroute('**/api/settings/models',fake_save)
        await page.locator('#dialog').evaluate('(d)=>d.close()')

    await page.evaluate('go("schedules")')
    await page.get_by_role('button',name='工作流',exact=True).click()
    await page.get_by_role('button',name='创建工作流',exact=True).click()
    await page.locator('#workflowForm [name=name]').fill('synthetic failed workflow')
    await page.locator('#workflowForm [name=steps]').fill('first\nnever after failure')
    await page.locator('#workflowForm button').click()
    await page.get_by_text('synthetic failed workflow',exact=True).wait_for()
    rows=(await app.service.platform.workflows.list())['workflows']
    await asyncio.wait_for(app.service.platform.workflows.wait(rows[0]['workflow_id']),10)
    await page.evaluate('actions.workflows()')
    assert (await app.service.platform.workflows.list())['workflows'][0]['status']=='failed'
    await page.screenshot(path=str(output/'workflow-failed.png'),full_page=True)

    # Render history from an actual failed child, not a seeded browser fixture.
    from pyharness.errors import PyHError
    sid=await app.service.create_session()
    child=await app.service.spawn_subagent(sid,'synthetic child failure')
    manager=app.service._engines[sid].subagent
    try:
        # Join must capture the live handle before a fast child is reaped;
        # an outer wait_for schedules another task on Python 3.11.
        await manager.join(child,timeout=10)
        raise AssertionError('The configured failing adapter must fail the child')
    except PyHError as error:
        assert error.code=='LLM-303'
    await page.locator('#dialog').evaluate('(d)=>d.close()')
    await page.evaluate('async sid=>{state.sid=sid;await go("chat")}',sid)
    if await page.locator('#inspectorToggle').get_attribute('aria-expanded')=='false':
        await page.locator('#inspectorToggle').click()
    await page.get_by_role('button',name='子任务',exact=True).click()
    await page.get_by_text(child,exact=True).wait_for()
    assert '失败' in await page.locator('#dialog').inner_text()
    await page.screenshot(path=str(output/'subtask-failed.png'),full_page=True)
    await page.locator('#dialog').evaluate('(d)=>d.close()')

    # Exercise the real pending-question owner and public answer endpoint.
    answer=asyncio.create_task(app.service._engines[sid].ask.request('synthetic question'))
    try:
        await page.wait_for_function('async()=>{const d=await api("/api/asks/pending");return d.pending.some(q=>q.question==="synthetic question")}')
        await page.get_by_role('button',name='待答问题',exact=True).click()
        await page.get_by_role('button',name='回答',exact=True).click()
        await page.locator('#answerText').fill('synthetic answer')
        await page.screenshot(path=str(output/'question-answer.png'),full_page=True)
        await page.get_by_role('button',name='提交答复',exact=True).click()
        assert await asyncio.wait_for(answer,5)=='用户答复: synthetic answer'
        assert not (await app.service.pending_asks())['pending']
    finally:
        if not answer.done(): answer.cancel()
        await asyncio.gather(answer,return_exceptions=True)

    # Close a real authenticated browser context while its workflow is running.
    # Only the model is synthetic; HTTP, background owner and queue are real.
    from pyharness.core import llm
    from pyharness.core.llm_diagnostics import call_role
    from scripts.verification_local import DeterministicModel
    import json
    model=llm.adapters[app.service.effective_settings().llm.model]
    previous_chat,previous_stream=model.chat,model.chat_stream
    synthetic=DeterministicModel()
    entered,release=asyncio.Event(),asyncio.Event()
    main_calls=[]
    async def controlled(*args,**kwargs):
        if call_role.get()=='main_agent':
            main_calls.append(True);entered.set();await release.wait()
        return await synthetic.chat(*args,**kwargs)
    model.chat=model.chat_stream=controlled
    background_context=await browser.new_context(extra_http_headers={'X-PyHarness-Token':app._api_token})
    try:
        background_page=await background_context.new_page()
        await background_page.goto(base+'/')
        await background_page.locator('#compose').wait_for()
        result=await background_page.evaluate('async()=>{const r=await fetch("/api/v1/workflows",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({steps:["synthetic background completion"],idempotency_key:"browser-closed",name:"关闭浏览器后的合成工作流"})});return {status:r.status,body:await r.json()}}')
        assert result['status']==200
        ident=result['body']['workflow_id']
        await asyncio.wait_for(entered.wait(),5)
        await background_context.close()
        assert not app.service.platform.workflows.tasks[ident].done()
        release.set()
        await asyncio.wait_for(app.service.platform.workflows.wait(ident),10)
        row=next(r for r in (await app.service.platform.workflows.list())['workflows'] if r['workflow_id']==ident)
        assert row['status']=='completed' and len(main_calls)==1
        await page.evaluate('actions.workflows()')
        await page.get_by_text('关闭浏览器后的合成工作流',exact=True).wait_for()
        await page.screenshot(path=str(output/'workflow-browser-closed.png'),full_page=True)
        (output/'browser-background.json').write_text(json.dumps({'real_browser_context_closed':True,'server_workflow_completed':True,'main_executions':1,'real_model_requests':0}),encoding='utf-8')
    finally:
        release.set()
        await background_context.close()
        model.chat,model.chat_stream=previous_chat,previous_stream
