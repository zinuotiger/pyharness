"""Two real browser accounts/teams and immediate role enforcement; synthetic data only."""
import asyncio
from scripts.platform_browser_smoke import navigate


async def verify_accounts(browser,operator,app,base,output):
    password='synthetic-acceptance-password'
    for username,team in [('audit-owner','audit-a'),('audit-second','audit-b')]:
        await navigate(operator,'settings')
        await operator.get_by_role('button',name='账户与团队',exact=True).click()
        form=operator.locator('#accountCreate')
        await form.locator('[name=username]').fill(username)
        await form.locator('[name=password]').fill(password)
        await form.locator('[name=tenant]').fill(team)
        await form.get_by_role('button',name='创建账户',exact=True).click()
        await operator.get_by_text('账户已创建',exact=True).wait_for()
    contexts=[]
    async def login(username,team):
        context=await browser.new_context(viewport={'width':1440,'height':900});contexts.append(context)
        page=await context.new_page();await page.goto(base+'/login')
        await page.locator('[name=username]').fill(username);await page.locator('[name=password]').fill(password);await page.locator('[name=tenant]').fill(team)
        await page.get_by_role('button',name='登录',exact=True).click();await page.locator('#compose').wait_for()
        assert await page.locator('#accountName').inner_text()==username
        return context,page
    try:
        a,pa=await login('audit-owner','audit-a');b,pb=await login('audit-second','audit-b')
        # Account workspaces require their own profile, even when the explicitly
        # registered adapter is synthetic. Exercise the same UI as a real owner.
        for team_page in (pa,pb):
            await navigate(team_page,'connections')
            await team_page.get_by_role('button',name='添加模型',exact=True).click()
            for field,value in [('label','synthetic team model'),('model','audit-synthetic'),('base_url','http://127.0.0.1:1/v1'),('api_key','synthetic-team-only-credential')]:
                await team_page.locator('#model_'+field).fill(value)
            await team_page.get_by_role('button',name='保存',exact=True).click()
            await team_page.get_by_text('synthetic team model',exact=True).wait_for()
            await navigate(team_page,'chat')
        await pa.locator('.sidebar [data-action="new-chat"]').click()
        await pa.wait_for_function('state.sid!==null && !document.querySelector(".skeleton")')
        sid=await pa.evaluate('state.sid')
        await pa.locator('#messageText').fill('audit team a synthetic task');await pa.locator('#messageText').press('Enter')
        try:
            await pa.locator('.chat-top .badge').filter(has_text='已完成').wait_for(timeout=15000)
        except Exception:
            await pa.screenshot(path=str(output/'team-send-failure.png'),full_page=True)
            print('SYNTHETIC TEAM DIAGNOSTIC', await pa.locator('#sendFeedback').inner_text(),
                  await pa.locator('#modelReadiness').inner_text())
            raise
        async with pa.expect_file_chooser() as chooser:await pa.get_by_role('button',name='附件',exact=True).click()
        await (await chooser.value).set_files({'name':'tenant-a.txt','mimeType':'text/plain','buffer':b'synthetic private team A'})
        await pa.locator('#attachmentCount').filter(has_text='附件 1').wait_for()
        artifacts=(await (await a.request.get(base+'/api/v1/artifacts')).json())['artifacts'];assert len(artifacts)==1
        foreign=await b.request.get(base+'/api/v1/artifacts/'+artifacts[0]['artifact_id']+'/download')
        assert foreign.status in (403,404)
        assert (await (await b.request.get(base+'/api/v1/sessions')).json())['sessions']==[]
        await navigate(pa,'settings');await pa.get_by_role('button',name='团队记忆',exact=True).click()
        await pa.locator('#memoryForm [name=key]').fill('audit_note');await pa.locator('#memoryForm [name=op]').select_option('set');await pa.locator('#memoryForm [name=value]').fill('team A evidence')
        await pa.locator('#memoryForm button').click();await pa.locator('#memoryForm [role=status]').filter(has_text='已写入').wait_for()
        other=(await (await b.request.get(base+'/api/v1/memory?key=audit_note')).json())
        assert 'team A evidence' not in str(other)
        await pa.get_by_role('button',name='账户与团队',exact=True).click()
        await pa.locator('#memberForm [name=username]').fill('audit-second');await pa.locator('#memberForm [name=role]').select_option('viewer')
        await pa.locator('#memberForm button').click();await pa.locator('#memberForm [role=status]').filter(has_text='成员权限已更新').wait_for()
        viewer,pv=await login('audit-second','audit-a')
        await pv.locator('#messageText').fill('viewer must not submit');await pv.locator('#messageText').press('Enter')
        await pv.locator('#sendFeedback [role=alert]').filter(has_text='权限').wait_for()
        assert await pv.locator('#messageText').input_value()=='viewer must not submit'
        await pv.screenshot(path=str(output/'team-viewer-denied.png'),full_page=True)
        # Change the role on the real management form. The existing login must
        # acquire permission on its next request, with no stale cached role.
        await pa.locator('#memberForm [name=role]').select_option('member')
        async with pa.expect_response(lambda r:r.url.endswith('/api/account/members')) as membership:
            await pa.locator('#memberForm button').click()
        assert (await membership.value).status==200
        await pv.locator('#messageText').fill('member synthetic success');await pv.locator('#messageText').press('Enter')
        await pv.locator('.chat-top .badge').filter(has_text='已完成').wait_for(timeout=15000)
        await pv.screenshot(path=str(output/'team-member-allowed.png'),full_page=True)
        await navigate(pv,'settings');await pv.get_by_role('button',name='账户与团队',exact=True).click()
        await pv.get_by_role('button',name='退出登录',exact=True).click();await pv.locator('#login').wait_for()
        assert (await viewer.request.get(base+'/api/v1/sessions')).status==401
        await pb.screenshot(path=str(output/'second-team-isolated.png'),full_page=True)
        return {'area':'two-team UI isolation/roles','result':'passed','checks':['UI provision/login','real model-free synthetic queue per tenant','foreign artifact rejected','foreign sessions/memory hidden','viewer submit denied with draft','role update effective in existing login','logout revoked'],'real_provider_requests':0}
    finally:
        for context in contexts:await context.close()
