"""Browser assertions for the chat-first shell; only synthetic failed submissions."""
import asyncio


async def verify_chat_shell(page, output):
    await page.locator('#compose').wait_for()
    assert await page.evaluate('state.page') == 'chat'
    assert not await page.locator('.inspector').is_visible()
    assert await page.locator('.chat-left').count() == 0
    assert await page.locator('#workbenchEntry').is_visible()
    assert await page.locator('#pinnedSessions').count() == 0
    assert await page.locator('#sessionSearch').count() == 0
    assert await page.locator('.sidebar .workspace').count() == 0
    await page.evaluate('document.fonts.ready')
    assert not await page.locator('#toast').is_visible()
    await page.screenshot(path=str(output/'chat-initial.png'), full_page=True)
    for width, height, name in [(1280,900,'chat-1280.png'),(390,844,'chat-mobile.png')]:
        await page.set_viewport_size({'width':width,'height':height})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert await page.locator('#messageText').is_visible()
        await page.screenshot(path=str(output/name), full_page=True)
    await page.locator('.mobile-sessions').click()
    assert await page.locator('#settingsEntry').is_visible()
    await page.locator('#sessionListHeading').click()
    await page.locator('#compose').wait_for()
    await page.set_viewport_size({'width':1440,'height':900})
    field = page.locator('#messageText')
    await field.fill('保留中文草稿')
    await page.locator('#inspectorToggle').click()
    assert await page.locator('.inspector').is_visible()
    assert await field.input_value() == '保留中文草稿'
    await page.screenshot(path=str(output/'chat-inspector-open.png'), full_page=True)
    await page.locator('#inspectorToggle').click()
    assert not await page.locator('.inspector').is_visible()
    await page.locator('#settingsEntry').click()
    await page.locator('.settings-nav').wait_for()
    assert await page.locator('#workbenchEntry').is_visible()
    for route in ['runs','approvals','artifacts','usage','connections','agents','knowledge','sandboxes','schedules']:
        assert await page.locator(f'.settings-nav [data-nav="{route}"]').is_visible()
    await page.get_by_role('button', name='模型配置', exact=True).click()
    await page.locator('[data-action="model-new"]').wait_for()
    await page.locator('[data-nav="chat"]').first.click()
    await field.wait_for()
    assert await field.input_value() == '保留中文草稿'
    await field.press('Shift+Enter')
    assert '\n' in await field.input_value()

    requests = []
    entered, release = asyncio.Event(), asyncio.Event()
    async def fail_submit(route):
        if route.request.method != 'POST':
            await route.continue_()
            return
        requests.append(route.request.post_data_json)
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        await route.fulfill(status=503, content_type='application/json',
                            body='{"code":"synthetic_submit_failed","message":"safe failure"}')
    await page.route('**/api/v1/runs', fail_submit)
    try:
        # IME confirmation and key repeat must not be interpreted as submission.
        await field.dispatch_event('compositionstart')
        await field.dispatch_event('keydown', {'key':'Enter','code':'Enter','isComposing':True})
        await field.dispatch_event('compositionend')
        await field.dispatch_event('keydown', {'key':'Enter','code':'Enter','keyCode':229})
        await field.dispatch_event('keydown', {'key':'Enter','code':'Enter','repeat':True})
        assert not requests
        await field.fill('preserve on failed submit')
        await field.press('Enter')
        await asyncio.wait_for(entered.wait(), 5)
        # Bypass the disabled button: a second form event must still be ignored.
        await page.locator('#compose').dispatch_event('submit')
        assert len(requests) == 1
        release.set()
        await page.locator('#sendFeedback [role=alert]').filter(has_text='synthetic_submit_failed').wait_for()
        # Feedback remains in the composer even after the short-lived toast closes.
        await page.locator('#toast').wait_for(state='hidden')
        assert await page.locator('#sendFeedback [role=alert]').is_visible()
        assert await field.input_value() == 'preserve on failed submit'
        assert await page.locator('#compose button[type=submit]').is_enabled()
        await page.screenshot(path=str(output/'chat-input-failure.png'), full_page=True)
    finally:
        release.set()
        await page.unroute('**/api/v1/runs', fail_submit)
    # Submission failures must remain actionable without opening the inspector.
    cases = [(422,'model_not_configured','模型配置'),
             (409,'tenant_model_profile_required','模型配置'),
             (401,'account_session_expired','重新登录'),
             (403,'account_read_only','权限'),
             (429,'run_quota','配额'),
             (503,'synthetic_service_failure','synthetic_service_failure')]
    for status, code, expected in cases:
        async def reject(route, request, status=status, code=code):
            await route.fulfill(status=status, content_type='application/json',
                                body='{"code":"'+code+'"}')
        await page.route('**/api/v1/runs', reject)
        await field.fill('preserve actionable failure draft')
        await field.press('Enter')
        await page.locator('#sendFeedback [role=alert]').filter(has_text=expected).wait_for()
        assert await field.input_value() == 'preserve actionable failure draft'
        assert await page.locator('#compose button[type=submit]').is_enabled()
        await page.unroute('**/api/v1/runs', reject)
    async def disconnected(route):
        await route.abort('connectionrefused')
    await page.route('**/api/v1/runs', disconnected)
    await field.press('Enter')
    await page.locator('#sendFeedback').get_by_text('查看任务中心', exact=True).wait_for()
    assert await field.input_value() == 'preserve actionable failure draft'
    await page.screenshot(path=str(output/'chat-network-feedback.png'), full_page=True)
    await page.unroute('**/api/v1/runs', disconnected)
    async def malformed(route):
        await route.fulfill(status=200,content_type='text/html',body='<html>unexpected proxy response</html>')
    await page.route('**/api/v1/runs',malformed)
    await field.press('Enter')
    await page.locator('#sendFeedback').get_by_text('查看任务中心',exact=True).wait_for()
    assert await field.input_value()=='preserve actionable failure draft'
    await page.unroute('**/api/v1/runs',malformed)
    # Exercise the actual bounded timer; no sleeps, fake clock or auto retry.
    timeout_entered,timeout_release=asyncio.Event(),asyncio.Event()
    timeout_calls=[]
    async def stalled(route):
        timeout_calls.append(True);timeout_entered.set();await timeout_release.wait()
        from playwright.async_api import Error
        try:await route.abort('timedout')
        except Error:pass  # Fetch was already aborted by its real deadline.
    await page.route('**/api/v1/runs',stalled)
    try:
        await field.press('Enter');await asyncio.wait_for(timeout_entered.wait(),5)
        await page.locator('#sendFeedback [role=alert]').filter(has_text='超时').wait_for(timeout=40000)
        assert len(timeout_calls)==1
        assert await field.input_value()=='preserve actionable failure draft'
        assert await page.locator('#compose button[type=submit]').is_enabled()
        await page.screenshot(path=str(output/'chat-timeout-feedback.png'),full_page=True)
    finally:
        timeout_release.set()
        await page.unroute('**/api/v1/runs',stalled)
    await field.fill('')
    await page.reload()
    await page.locator('#compose').wait_for()
    assert not await page.locator('#toast').is_visible()
    assert not await page.locator('.inspector').is_visible()
